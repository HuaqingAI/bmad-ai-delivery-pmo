#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Read-only audit gates for ADP management-panel inputs and artifacts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_ROOT.parents[1]
PANEL_ROOT = SKILLS_ROOT / "adp-management-panel"
PANEL_MODEL_SCRIPT = PANEL_ROOT / "scripts/panel_model.py"
FLOW_GRAPH_SCRIPT = SKILLS_ROOT / "adp-flow-graph/scripts/flow_graph.py"
RESOURCE_PATH = PANEL_ROOT / "assets/elk-resource-v1.json"
PANEL_APP_PATH = PANEL_ROOT / "assets/panel.js"
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SUSPICIOUS_TEXT_RE = re.compile(
    r"</?script\b|<foreignobject\b|\bon[a-z]+\s*=|javascript\s*:|<iframe\b|<object\b",
    re.IGNORECASE,
)
ALLOWED_READINESS = {"ready", "degraded", "blocked"}
ALLOWED_LIFECYCLE = {
    "current-derived",
    "pre-meeting-snapshot",
    "post-sync-official",
    "sync-failed",
}
RECOVERY_ORDER = [
    "adp-state-audit",
    "adp-program-status",
    "adp-roadmap-sync",
    "adp-flow-graph",
    "adp-meeting-pack",
    "adp-meeting-sync",
    "adp-management-panel",
    "adp-setup",
]


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def bytes_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_utf8_lf_bytes(value: bytes) -> bytes:
    value.decode("utf-8")
    return value.replace(b"\r\n", b"\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load contract module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finding(
    code: str,
    severity: str,
    disposition: str,
    summary: str,
    source: str,
    workflow: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "execution_disposition": disposition,
        "summary": summary,
        "source": source,
        "recommended_workflow": workflow,
        "category": "panel_audit",
        "gap_type": code,
    }


def _public_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(inputs)
    result.pop("_panel_source_paths", None)
    request = result.get("request")
    if isinstance(request, dict):
        for key in (
            "generated_at",
            "panel_input_audit_id",
            "panel_input_audit_disposition",
            "panel_input_audit_findings",
            "panel_input_audit_workflows",
        ):
            request.pop(key, None)
    return result


def _sealed_inputs(inputs: dict[str, Any]) -> dict[str, str]:
    public = _public_inputs(inputs)
    values = {
        "program-status": public.get("program_status"),
        "roadmap": public.get("roadmap"),
        "flow-graph": public.get("flow_graph"),
        "fde-meeting-pack": public.get("meeting_packs", {}).get("fde-morning")
        if isinstance(public.get("meeting_packs"), dict)
        else None,
        "business-meeting-pack": public.get("meeting_packs", {}).get("business-biweekly")
        if isinstance(public.get("meeting_packs"), dict)
        else None,
        "history": public.get("history", []),
        "request": public.get("request", {}),
        "shareable-policy": public.get("shareable_policy", {}),
    }
    return {key: canonical_hash(value) for key, value in values.items()}


def _sealed_source_files(inputs: dict[str, Any]) -> tuple[dict[str, str], list[tuple[str, Path]]]:
    hashes: dict[str, str] = {}
    missing: list[tuple[str, Path]] = []
    raw_source_paths = inputs.get("_panel_source_paths")
    if not isinstance(raw_source_paths, dict):
        return hashes, missing
    for name, raw_path in sorted(raw_source_paths.items()):
        path = Path(str(raw_path)).expanduser().resolve()
        if path.is_file():
            hashes[name] = bytes_hash(path.read_bytes())
        else:
            missing.append((name, path))
    return hashes, missing


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _iter_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "_panel_source_paths":
                continue
            found.extend(_iter_strings(item, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_iter_strings(item, f"{path}/{index}"))
    elif isinstance(value, str):
        found.append((path, value))
    return found


def _lineage_hash_errors(name: str, value: Any) -> list[str]:
    if not isinstance(value, dict) or not value:
        return [f"{name} source_fingerprints must be a non-empty object"]
    return [
        f"{name} source fingerprint {key!r} is not sha256"
        for key, fingerprint in value.items()
        if not isinstance(key, str) or not key or not isinstance(fingerprint, str) or not HASH_RE.fullmatch(fingerprint)
    ]


def _resource_validation(
    request: dict[str, Any], resource_path: Path = RESOURCE_PATH, panel_root: Path = PANEL_ROOT
) -> tuple[dict[str, Any] | None, list[str], dict[str, str]]:
    errors: list[str] = []
    evidence: dict[str, str] = {}
    try:
        resource = load_json(resource_path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"ELK resource metadata is unreadable: {exc}"], evidence
    bundle = panel_root / str(resource.get("bundle") or "")
    license_path = panel_root / str(resource.get("license") or "")
    if not bundle.is_file():
        errors.append("ELK bundle is missing")
    else:
        if resource.get("engine_sha256_mode") != "utf8-lf":
            errors.append("ELK bundle checksum mode is unsupported")
        try:
            actual = bytes_hash(canonical_utf8_lf_bytes(bundle.read_bytes()))
        except UnicodeDecodeError:
            errors.append("ELK bundle is not valid UTF-8")
        else:
            evidence["elk_bundle_sha256"] = actual
            if actual != resource.get("engine_sha256"):
                errors.append("ELK bundle checksum does not match resource metadata")
    if not license_path.is_file():
        errors.append("ELK license is missing")
    else:
        license_text = license_path.read_text(encoding="utf-8")
        evidence["elk_license_sha256"] = bytes_hash(license_text.encode("utf-8"))
        if "Eclipse Public License" not in license_text or "2.0" not in license_text:
            errors.append("ELK license does not identify EPL-2.0")
    layout = request.get("layout") if isinstance(request, dict) else None
    expected_layout = {
        "layout_contract_version": resource.get("layout_contract_version"),
        "engine": resource.get("engine"),
        "engine_version": resource.get("engine_version"),
        "engine_license": resource.get("engine_license"),
        "engine_sha256": resource.get("engine_sha256"),
        "config_sha256": canonical_hash(resource.get("options")),
        "node_dimensions_version": resource.get("node_dimensions_version"),
    }
    if layout != expected_layout:
        errors.append("panel request ELK version/license/hash/config metadata does not match the fixed resource")
    return resource, errors, evidence


def _finalize(
    audit_type: str,
    stable_id_field: str,
    stable_id_prefix: str,
    findings: list[dict[str, Any]],
    identity: dict[str, Any],
    generated_at: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    ordered_findings = sorted(findings, key=lambda item: (item["code"], item["source"], item["summary"]))
    blocking = [item for item in ordered_findings if item["execution_disposition"] == "blocked"]
    warnings = [item for item in ordered_findings if item["execution_disposition"] != "blocked"]
    disposition = "blocked" if blocking else "degraded" if warnings else "ready"
    workflows_requested = {item["recommended_workflow"] for item in ordered_findings if item.get("recommended_workflow")}
    workflows = [item for item in RECOVERY_ORDER if item in workflows_requested]
    stable_id = stable_id_prefix + canonical_hash(identity).split(":", 1)[1][:20]
    result = {
        "ok": True,
        "audit_type": audit_type,
        "audit_schema_version": "1.0.0",
        stable_id_field: stable_id,
        "generated_at": generated_at,
        "audit_status": "blocked" if blocking else "warning" if warnings else "pass",
        "execution_disposition": disposition,
        "blocking_gaps": blocking,
        "warnings": warnings,
        "recommended_workflows": workflows,
        "counts": {"blocking_findings": len(blocking), "warning_findings": len(warnings)},
        **details,
    }
    result["audit_content_hash"] = canonical_hash(result)
    return result


def validate_audit_integrity(value: Any, id_field: str) -> list[str]:
    if not isinstance(value, dict):
        return ["audit must be an object"]
    errors: list[str] = []
    content_hash = value.get("audit_content_hash")
    candidate = copy.deepcopy(value)
    candidate.pop("audit_content_hash", None)
    if content_hash != canonical_hash(candidate):
        errors.append("audit_content_hash does not match the audit record")
    if not isinstance(value.get(id_field), str) or not value[id_field]:
        errors.append(f"{id_field} is missing")
    if value.get("execution_disposition") not in {"ready", "degraded", "blocked"}:
        errors.append("execution_disposition is invalid")
    return errors


def audit_panel_inputs(
    inputs: dict[str, Any],
    *,
    as_of: date | None = None,
    max_age_days: int = 7,
    resource_path: Path = RESOURCE_PATH,
    panel_root: Path = PANEL_ROOT,
) -> dict[str, Any]:
    """Seal and validate canonical panel inputs without changing them."""
    before = canonical_hash(inputs)
    findings: list[dict[str, Any]] = []
    status = inputs.get("program_status")
    roadmap = inputs.get("roadmap")
    graph = inputs.get("flow_graph")
    packs = inputs.get("meeting_packs")
    request = inputs.get("request") if isinstance(inputs.get("request"), dict) else {}
    audit_date = as_of or _parse_date(status.get("as_of") if isinstance(status, dict) else None) or date.today()

    for name, value, workflow in (
        ("program-status", status, "adp-program-status"),
        ("roadmap", roadmap, "adp-roadmap-sync"),
        ("flow-graph", graph, "adp-flow-graph"),
    ):
        if not isinstance(value, dict):
            findings.append(_finding(f"panel.input.{name}.missing", "blocking", "blocked", f"{name} is missing", name, workflow))
    if not isinstance(packs, dict):
        findings.append(_finding("panel.input.meeting-pack.missing", "blocking", "blocked", "meeting packs are missing", "meeting-packs", "adp-meeting-pack"))

    if isinstance(status, dict):
        progress = status.get("progress")
        if not isinstance(progress, dict) or progress.get("progress_schema_version") != "2.0.0":
            findings.append(_finding("panel.input.progress.schema-mismatch", "blocking", "blocked", "program-status progress_schema_version must be 2.0.0", "program-status", "adp-program-status"))
        for error in _lineage_hash_errors("program-status", status.get("source_fingerprints")):
            findings.append(_finding("panel.input.source-lineage.invalid", "blocking", "blocked", error, "program-status", "adp-program-status"))
        for field in ("snapshot_id", "input_audit_id", "artifact_audit_id", "baseline_revision", "as_of"):
            if status.get(field) in (None, ""):
                findings.append(_finding("panel.input.source-lineage.missing", "blocking", "blocked", f"program-status is missing {field}", "program-status", "adp-program-status"))

    if isinstance(status, dict) and isinstance(roadmap, dict):
        if (
            roadmap.get("program_status_snapshot_id") != status.get("snapshot_id")
            or roadmap.get("baseline_revision") != status.get("baseline_revision")
            or roadmap.get("progress") != status.get("progress")
        ):
            findings.append(_finding("panel.input.progress.identity-mismatch", "blocking", "blocked", "roadmap does not copy the selected canonical progress/status identity", "roadmap", "adp-roadmap-sync"))
        for error in _lineage_hash_errors("roadmap", roadmap.get("source_fingerprints")):
            findings.append(_finding("panel.input.source-lineage.invalid", "blocking", "blocked", error, "roadmap", "adp-roadmap-sync"))
        for field in ("input_audit_id", "artifact_audit_id"):
            if not roadmap.get(field):
                findings.append(_finding("panel.input.source-lineage.missing", "blocking", "blocked", f"roadmap is missing {field}", "roadmap", "adp-roadmap-sync"))

    if isinstance(graph, dict):
        graph_errors: list[str] = []
        if graph.get("flow_graph_schema_version") != "1.0.0":
            graph_errors.append("flow_graph_schema_version must be 1.0.0")
        try:
            flow_module = _load_module(FLOW_GRAPH_SCRIPT, "adp_panel_audit_flow_graph")
            graph_errors.extend(flow_module.graph_semantic_errors(graph))
        except (ImportError, KeyError, TypeError, ValueError) as exc:
            graph_errors.append(f"flow graph semantic validation failed: {exc}")
        if isinstance(status, dict) and graph.get("topology", {}).get("baseline_revision") != status.get("baseline_revision"):
            graph_errors.append("flow graph baseline revision does not match program status")
        for error in sorted(set(graph_errors)):
            findings.append(_finding("panel.input.flow-graph.schema-mismatch", "blocking", "blocked", error, "flow-graph", "adp-flow-graph"))

    node_ids = {
        item.get("node_id") for item in graph.get("topology", {}).get("nodes", []) if isinstance(item, dict)
    } if isinstance(graph, dict) else set()
    edge_by_id = {
        item.get("edge_id"): item for item in graph.get("topology", {}).get("edges", []) if isinstance(item, dict)
    } if isinstance(graph, dict) else {}
    meeting_trace: dict[str, Any] = {}
    for scenario in ("fde-morning", "business-biweekly"):
        pack = packs.get(scenario) if isinstance(packs, dict) else None
        if not isinstance(pack, dict):
            findings.append(_finding("panel.input.meeting-pack.missing", "blocking", "blocked", f"{scenario} meeting pack is missing", scenario, "adp-meeting-pack"))
            continue
        for error in _lineage_hash_errors(scenario, pack.get("source_fingerprints")):
            findings.append(_finding("panel.input.source-lineage.invalid", "blocking", "blocked", error, scenario, "adp-meeting-pack"))
        for field in ("meeting_pack_id", "input_audit_id", "artifact_audit_id", "flow_selection_id"):
            if not pack.get(field):
                findings.append(_finding("panel.input.source-lineage.missing", "blocking", "blocked", f"{scenario} is missing {field}", scenario, "adp-meeting-pack"))
        if isinstance(status, dict) and (
            pack.get("scenario") != scenario
            or pack.get("program_status_snapshot_id") != status.get("snapshot_id")
            or pack.get("baseline_revision") != status.get("baseline_revision")
        ):
            findings.append(_finding("panel.input.meeting-pack.identity-mismatch", "blocking", "blocked", f"{scenario} status identity does not match program-status", scenario, "adp-meeting-pack"))
        subgraph = pack.get("flow_subgraph") if isinstance(pack.get("flow_subgraph"), dict) else {}
        selected_nodes = list(pack.get("selected_node_ids") or [])
        selected_edges = list(pack.get("selected_edge_ids") or [])
        subgraph_nodes = [item.get("node_id") for item in subgraph.get("nodes", []) if isinstance(item, dict)]
        subgraph_edges = [item.get("edge_id") for item in subgraph.get("edges", []) if isinstance(item, dict)]
        scope_invalid = (
            not isinstance(graph, dict)
            or subgraph.get("flow_graph_id") != graph.get("flow_graph_id")
            or subgraph.get("selection_id") != pack.get("flow_selection_id")
            or sorted(selected_nodes) != sorted(subgraph_nodes)
            or sorted(selected_edges) != sorted(subgraph_edges)
            or any(item not in node_ids for item in selected_nodes)
            or any(item not in edge_by_id for item in selected_edges)
            or any(
                edge_by_id[item].get("predecessor") not in selected_nodes or edge_by_id[item].get("target") not in selected_nodes
                for item in selected_edges
                if item in edge_by_id
            )
        )
        if scope_invalid:
            findings.append(_finding("panel.input.meeting-pack.flow-scope-mismatch", "blocking", "blocked", f"{scenario} flow scope is not traceable to the canonical graph", scenario, "adp-meeting-pack"))
        readiness = pack.get("readiness")
        lifecycle = pack.get("lifecycle")
        if readiness not in ALLOWED_READINESS:
            findings.append(_finding("panel.input.meeting-readiness.invalid", "blocking", "blocked", f"{scenario} meeting readiness is invalid", scenario, "adp-meeting-pack"))
        elif readiness != "ready":
            findings.append(_finding("panel.input.meeting-readiness.degraded", "warning", "degraded", f"{scenario} meeting readiness is {readiness}", scenario, "adp-meeting-pack"))
        if lifecycle not in ALLOWED_LIFECYCLE:
            findings.append(_finding("panel.input.meeting-lifecycle.invalid", "blocking", "blocked", f"{scenario} lifecycle is invalid", scenario, "adp-meeting-pack"))
        elif lifecycle == "sync-failed":
            findings.append(_finding("panel.input.meeting-lifecycle.sync-failed", "warning", "degraded", f"{scenario} lifecycle records a failed sync", scenario, "adp-meeting-sync"))
        official = pack.get("official_panel_archive")
        official_valid = (
            isinstance(official, dict)
            and official.get("receipt_status") == "applied"
            and isinstance(official.get("panel_id"), str)
            and HASH_RE.fullmatch(official["panel_id"])
        )
        if lifecycle == "post-sync-official" and not official_valid:
            findings.append(_finding("panel.input.meeting-lifecycle.official-association-invalid", "blocking", "blocked", f"{scenario} official lifecycle lacks an applied receipt panel association", scenario, "adp-meeting-sync"))
        elif lifecycle != "post-sync-official" and official is not None:
            findings.append(_finding("panel.input.meeting-lifecycle.false-official-association", "blocking", "blocked", f"{scenario} exposes an official panel association before post-sync-official lifecycle", scenario, "adp-meeting-sync"))
        if pack.get("meeting_window", {}).get("status") != "confirmed":
            findings.append(_finding("panel.input.meeting-window.unconfirmed", "warning", "degraded", f"{scenario} meeting window is not confirmed", scenario, "adp-meeting-pack"))
        meeting_trace[scenario] = {
            "meeting_pack_id": pack.get("meeting_pack_id"),
            "readiness": readiness,
            "lifecycle": lifecycle,
            "flow_selection_id": pack.get("flow_selection_id"),
            "flow_scope_id": pack.get("flow_scope_id"),
            "official_panel_id": official.get("panel_id") if official_valid else None,
            "selected_node_ids": sorted(selected_nodes),
            "selected_edge_ids": sorted(selected_edges),
        }

    generated_sources = [("program-status", status), ("roadmap", roadmap)]
    if isinstance(packs, dict):
        generated_sources.extend((scenario, packs.get(scenario)) for scenario in ("fde-morning", "business-biweekly"))
    for name, value in generated_sources:
        if not isinstance(value, dict):
            continue
        generated = _parse_datetime(value.get("generated_at"))
        if generated is None:
            findings.append(_finding("panel.input.freshness.invalid", "blocking", "blocked", f"{name} generated_at is invalid", name, "adp-management-panel"))
        elif generated.date() > audit_date:
            findings.append(_finding("panel.input.freshness.future", "blocking", "blocked", f"{name} was generated after panel as-of", name, "adp-management-panel"))
        elif (audit_date - generated.date()).days > max_age_days:
            findings.append(_finding("panel.input.freshness.stale", "blocking", "blocked", f"{name} is older than {max_age_days} days", name, "adp-management-panel"))
    if isinstance(status, dict):
        status_as_of = _parse_date(status.get("as_of"))
        if status_as_of is None or status_as_of > audit_date:
            findings.append(_finding("panel.input.as-of.invalid", "blocking", "blocked", "program-status as_of is invalid or in the future", "program-status", "adp-program-status"))
        if isinstance(roadmap, dict) and roadmap.get("as_of") != status.get("as_of"):
            findings.append(_finding("panel.input.as-of.mismatch", "blocking", "blocked", "roadmap as_of differs from program-status", "roadmap", "adp-roadmap-sync"))

    resource, resource_errors, resource_evidence = _resource_validation(request, resource_path, panel_root)
    for error in resource_errors:
        findings.append(_finding("panel.input.elk-asset.mismatch", "blocking", "blocked", error, str(resource_path), "adp-setup"))

    catalog_path = panel_root / "assets/panel-locale-catalog-v1.json"
    try:
        catalog = load_json(catalog_path)
        requested_locale = request.get("locale")
        if requested_locale not in catalog.get("supported_locales", []):
            findings.append(_finding("panel.locale.fallback", "warning", "degraded", f"unsupported locale {requested_locale!r} falls back to {catalog.get('default_locale')}", str(catalog_path), ""))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(_finding("panel.input.locale-catalog.invalid", "blocking", "blocked", f"panel locale catalog is unavailable: {exc}", str(catalog_path), "adp-setup"))

    suspicious = [(path, text) for path, text in _iter_strings(_public_inputs(inputs)) if SUSPICIOUS_TEXT_RE.search(text)]
    if suspicious:
        findings.append(_finding("panel.input.unsafe-source", "warning", "degraded", f"{len(suspicious)} source text value(s) require safe inert embedding", suspicious[0][0], "adp-management-panel"))

    source_files, missing_source_files = _sealed_source_files(inputs)
    for name, path in missing_source_files:
        findings.append(_finding("panel.input.source.missing", "blocking", "blocked", f"sealed source file is missing: {path}", name, "adp-management-panel"))

    if canonical_hash(inputs) != before:
        raise RuntimeError("panel input audit modified its inputs")
    sealed = _sealed_inputs(inputs)
    identity = {
        "audit_type": "panel-input",
        "as_of": audit_date.isoformat(),
        "max_age_days": max_age_days,
        "sealed_inputs": sealed,
        "sealed_source_files": source_files,
        "findings": findings,
        "resource": resource,
    }
    generated_at = str(status.get("generated_at")) if isinstance(status, dict) and status.get("generated_at") else audit_date.isoformat() + "T00:00:00Z"
    return _finalize(
        "panel-input",
        "panel_input_audit_id",
        "panel-input-audit-",
        findings,
        identity,
        generated_at,
        {
            "as_of": audit_date.isoformat(),
            "max_age_days": max_age_days,
            "safe_to_render": not any(item["execution_disposition"] == "blocked" for item in findings),
            "sealed_inputs": sealed,
            "sealed_source_files": source_files,
            "resource_evidence": resource_evidence,
            "meeting_trace": meeting_trace,
        },
    )


class _PanelHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.script_id: str | None = None
        self.scripts: dict[str, list[str]] = {}
        self.script_counts: dict[str, int] = {}
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.event_attributes: list[str] = []
        self.external_references: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag.lower())
        attr_map = {key.lower(): value for key, value in attrs}
        element_id = attr_map.get("id")
        if element_id:
            self.ids.add(element_id)
        for item in str(attr_map.get("class") or "").split():
            self.classes.add(item)
        for key, value in attrs:
            key_lower = key.lower()
            if key_lower.startswith("on"):
                self.event_attributes.append(key_lower)
            if key_lower in {"src", "href", "xlink:href"} and value and not value.startswith("#"):
                self.external_references.append(value)
        if tag.lower() == "script" and element_id:
            self.script_id = element_id
            self.script_counts[element_id] = self.script_counts.get(element_id, 0) + 1
            self.scripts.setdefault(element_id, [])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self.script_id = None

    def handle_data(self, data: str) -> None:
        if self.script_id:
            self.scripts[self.script_id].append(data)

    def script(self, element_id: str) -> str | None:
        values = self.scripts.get(element_id)
        return "".join(values) if values is not None else None


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def _walk_scalars(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _walk_scalars(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _walk_scalars(nested)]
    return [value]


def audit_panel_artifacts(
    model: dict[str, Any],
    bundle_bytes: bytes,
    html_bytes: bytes,
    *,
    input_audit: dict[str, Any] | None = None,
    source_inputs: dict[str, Any] | None = None,
    publication_targets: dict[str, str | Path] | None = None,
    resource_path: Path = RESOURCE_PATH,
    panel_root: Path = PANEL_ROOT,
) -> dict[str, Any]:
    """Validate a rendered panel candidate and immutable targets without writing them."""
    before_targets = {
        name: Path(path).read_bytes()
        for name, path in (publication_targets or {}).items()
        if Path(path).is_file()
    }
    findings: list[dict[str, Any]] = []
    manifest = model.get("manifest") if isinstance(model, dict) else None
    if not isinstance(manifest, dict):
        findings.append(_finding("panel.artifact.manifest.missing", "blocking", "blocked", "panel manifest is missing", "panel-model", "adp-management-panel"))
        manifest = {}

    panel_model_module = None
    try:
        panel_model_module = _load_module(PANEL_MODEL_SCRIPT, "adp_panel_audit_model")
        model_errors = panel_model_module.validate_schema(model, load_json(panel_model_module.PANEL_SCHEMA_PATH))
        manifest_errors = panel_model_module.validate_schema(manifest, load_json(panel_model_module.MANIFEST_SCHEMA_PATH))
        for error in [*model_errors, *manifest_errors]:
            findings.append(_finding("panel.artifact.schema-mismatch", "blocking", "blocked", error, "panel-model", "adp-management-panel"))
    except (ImportError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        findings.append(_finding("panel.artifact.schema-mismatch", "blocking", "blocked", f"panel schema validation failed: {exc}", "panel-model", "adp-management-panel"))

    if manifest.get("panel_id") != model.get("panel_id") or manifest.get("panel_model_id") != model.get("panel_model_id"):
        findings.append(_finding("panel.artifact.manifest.identity-mismatch", "blocking", "blocked", "manifest and panel model identities differ", "panel-model", "adp-management-panel"))
    try:
        decoded_bundle = json.loads(bundle_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        decoded_bundle = None
        findings.append(_finding("panel.artifact.bundle.invalid", "blocking", "blocked", f"immutable bundle is invalid JSON: {exc}", "panel-bundle", "adp-management-panel"))
    if decoded_bundle != model or bundle_bytes != canonical_json_bytes(model):
        findings.append(_finding("panel.artifact.bundle.mismatch", "blocking", "blocked", "immutable bundle bytes do not equal the canonical panel model", "panel-bundle", "adp-management-panel"))

    if input_audit is not None:
        for error in validate_audit_integrity(input_audit, "panel_input_audit_id"):
            findings.append(_finding("panel.artifact.input-audit.tampered", "blocking", "blocked", error, "panel-input-audit", "adp-state-audit"))
        if input_audit.get("execution_disposition") == "blocked":
            findings.append(_finding("panel.artifact.input-audit.blocked", "blocking", "blocked", "panel was rendered from a blocking input audit", "panel-input-audit", "adp-state-audit"))
        if input_audit.get("panel_input_audit_id") not in manifest.get("input_audit_ids", []):
            findings.append(_finding("panel.artifact.input-audit.identity-mismatch", "blocking", "blocked", "manifest does not carry the panel pre-render audit ID", "panel-manifest", "adp-management-panel"))
        if source_inputs is not None and input_audit.get("sealed_inputs") != _sealed_inputs(source_inputs):
            findings.append(_finding("panel.artifact.input-hash.mismatch", "blocking", "blocked", "canonical panel inputs changed after the pre-render audit", "panel-inputs", "adp-state-audit"))
        if source_inputs is not None:
            current_source_files, missing_source_files = _sealed_source_files(source_inputs)
            if missing_source_files or input_audit.get("sealed_source_files") != current_source_files:
                findings.append(_finding("panel.artifact.input-source-hash.mismatch", "blocking", "blocked", "one or more canonical input files changed or disappeared after the pre-render audit", "panel-inputs", "adp-state-audit"))

    if source_inputs is not None and panel_model_module is not None:
        try:
            expected_model = panel_model_module.compose_panel(copy.deepcopy(source_inputs))
            if expected_model != model:
                findings.append(_finding("panel.artifact.model.tampered", "blocking", "blocked", "panel model is not the deterministic projection of sealed canonical inputs", "panel-model", "adp-management-panel"))
        except (KeyError, TypeError, ValueError) as exc:
            findings.append(_finding("panel.artifact.model.unverifiable", "blocking", "blocked", f"cannot recompute expected panel model: {exc}", "panel-model", "adp-management-panel"))

    parser = _PanelHTMLParser()
    try:
        html_text = html_bytes.decode("utf-8")
        parser.feed(html_text)
    except (UnicodeDecodeError, ValueError) as exc:
        html_text = ""
        findings.append(_finding("panel.artifact.html.invalid", "blocking", "blocked", f"panel HTML is invalid UTF-8/HTML: {exc}", "panel-html", "adp-management-panel"))
    for element_id, expected in (("adp-panel-model", model), ("adp-panel-manifest", manifest)):
        raw = parser.script(element_id)
        if raw is None or parser.script_counts.get(element_id) != 1:
            findings.append(_finding("panel.artifact.embedded-model.missing", "blocking", "blocked", f"HTML must contain exactly one {element_id}", "panel-html", "adp-management-panel"))
            continue
        if any(character in raw for character in ("<", ">", "&", "\u2028", "\u2029")):
            findings.append(_finding("panel.artifact.safe-embedding.invalid", "blocking", "blocked", f"{element_id} contains an unescaped script-breaking codepoint", "panel-html", "adp-management-panel"))
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            findings.append(_finding("panel.artifact.embedded-model.invalid", "blocking", "blocked", f"{element_id} is invalid JSON: {exc}", "panel-html", "adp-management-panel"))
        else:
            if decoded != expected:
                findings.append(_finding("panel.artifact.embedded-model.mismatch", "blocking", "blocked", f"{element_id} differs from the immutable bundle", "panel-html", "adp-management-panel"))

    resource, resource_errors, resource_evidence = _resource_validation({"layout": manifest.get("layout")}, resource_path, panel_root)
    for error in resource_errors:
        findings.append(_finding("panel.artifact.elk-asset.mismatch", "blocking", "blocked", error, str(resource_path), "adp-setup"))
    if resource is not None:
        elk_path = panel_root / resource["bundle"]
        embedded_elk = parser.script("adp-elk-runtime")
        try:
            expected_elk = canonical_utf8_lf_bytes(elk_path.read_bytes()).decode("utf-8") if elk_path.is_file() else ""
        except UnicodeDecodeError:
            expected_elk = ""
        if parser.script_counts.get("adp-elk-runtime") != 1 or embedded_elk != expected_elk:
            findings.append(_finding("panel.artifact.elk-embedded.mismatch", "blocking", "blocked", "embedded ELK runtime does not match the pinned bundle", "panel-html", "adp-management-panel"))

    embedded_app = parser.script("adp-panel-runtime")
    expected_app = (panel_root / "assets/panel.js").read_text(encoding="utf-8")
    if parser.script_counts.get("adp-panel-runtime") != 1 or embedded_app != expected_app:
        findings.append(_finding("panel.artifact.runtime.mismatch", "blocking", "blocked", "embedded panel runtime differs from the shipped runtime", "panel-html", "adp-management-panel"))
    if embedded_app is not None:
        forbidden_runtime = [token for token in ("innerHTML", "outerHTML", "insertAdjacentHTML", "foreignObject", "eval(", "new Function") if token in embedded_app]
        used_svg_tags = set(re.findall(r'createSvg\("([A-Za-z0-9:-]+)"', embedded_app))
        declared_allowlist = set(manifest.get("safe_embedding", {}).get("svg_allowlist", []))
        if forbidden_runtime or not used_svg_tags.issubset(declared_allowlist):
            findings.append(_finding("panel.artifact.svg-allowlist.invalid", "blocking", "blocked", "panel runtime violates the SVG/DOM allowlist", "panel-runtime", "adp-management-panel"))
    required_fallback = {"no-js-content", "project-lead", "fde-morning", "business-biweekly"}
    if not required_fallback.issubset(parser.ids) or "stage-list" not in parser.classes:
        findings.append(_finding("panel.artifact.fallback.missing", "blocking", "blocked", "semantic no-JS/ELK fallback is incomplete", "panel-html", "adp-management-panel"))
    if parser.event_attributes or parser.external_references or "foreignobject" in parser.tags:
        findings.append(_finding("panel.artifact.html-allowlist.invalid", "blocking", "blocked", "HTML contains event attributes, external references, or foreignObject", "panel-html", "adp-management-panel"))

    locale = manifest.get("locale") if isinstance(manifest.get("locale"), dict) else {}
    try:
        catalog = load_json(panel_root / "assets/panel-locale-catalog-v1.json")
        requested = locale.get("requested")
        resolved = requested if requested in catalog.get("supported_locales", []) else catalog.get("default_locale")
        if locale.get("resolved") != resolved or locale.get("fallback") != (requested != resolved):
            findings.append(_finding("panel.artifact.locale.mismatch", "blocking", "blocked", "manifest locale/fallback does not match the fixed catalog", "panel-manifest", "adp-management-panel"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(_finding("panel.artifact.locale-catalog.invalid", "blocking", "blocked", f"locale catalog is unavailable: {exc}", "panel-locale", "adp-setup"))

    profile = manifest.get("distribution_profile")
    redaction = manifest.get("redaction") if isinstance(manifest.get("redaction"), dict) else {}
    if redaction.get("profile") != profile or redaction.get("topology_reconnected") is not False:
        findings.append(_finding("panel.artifact.redaction-manifest.invalid", "blocking", "blocked", "distribution profile and redaction manifest disagree", "panel-manifest", "adp-management-panel"))
    if profile == "internal-full" and any(redaction.get(key) for key in ("removed_fields", "hidden_nodes", "hidden_edges", "hidden_sources", "hidden_counts")):
        findings.append(_finding("panel.artifact.redaction-manifest.invalid", "blocking", "blocked", "internal-full must not claim redaction", "panel-manifest", "adp-management-panel"))
    if profile == "shareable-summary":
        removed = set(redaction.get("removed_fields") or [])
        leaked_keys = sorted(_walk_keys(model.get("data", {})) & removed)
        if leaked_keys:
            findings.append(_finding("panel.artifact.redaction.leak", "blocking", "blocked", "shareable data retains redacted fields: " + ", ".join(leaked_keys), "panel-model", "adp-management-panel"))
        if source_inputs is not None:
            internal_ids = {
                item.get(key)
                for item in source_inputs.get("flow_graph", {}).get("topology", {}).get("nodes", [])
                for key in ("node_id",)
                if isinstance(item, dict)
            } | {
                item.get(key)
                for item in source_inputs.get("flow_graph", {}).get("topology", {}).get("edges", [])
                for key in ("edge_id",)
                if isinstance(item, dict)
            }
            exposed_values = set(item for item in _walk_scalars(model.get("data", {})) if isinstance(item, str))
            if any(item in exposed_values for item in internal_ids if item):
                findings.append(_finding("panel.artifact.redaction.identity-leak", "blocking", "blocked", "shareable data exposes internal graph identity", "panel-model", "adp-management-panel"))

    expected_payloads = {"bundle": bundle_bytes, "html": html_bytes}
    for name, raw_path in (publication_targets or {}).items():
        path = Path(raw_path)
        expected = expected_payloads.get(name)
        if expected is None:
            continue
        if path.exists() and path.read_bytes() != expected:
            findings.append(_finding("panel.artifact.immutable-collision", "blocking", "blocked", f"publication target already exists with different bytes: {path}", str(path), "adp-management-panel"))
        if name == "bundle" and path.name != f"{manifest.get('panel_id')}.json":
            findings.append(_finding("panel.artifact.archive.identity-mismatch", "blocking", "blocked", "immutable bundle filename does not match panel_id", str(path), "adp-management-panel"))
        if name == "html" and "snapshots/management-panel" in path.as_posix() and path.name != f"{manifest.get('panel_id')}.html":
            findings.append(_finding("panel.artifact.archive.identity-mismatch", "blocking", "blocked", "immutable HTML filename does not match panel_id", str(path), "adp-management-panel"))

    for name, payload in before_targets.items():
        path = Path((publication_targets or {})[name])
        if path.read_bytes() != payload:
            raise RuntimeError(f"panel artifact audit modified immutable target: {path}")

    artifact_hashes = {"bundle_sha256": bytes_hash(bundle_bytes), "html_sha256": bytes_hash(html_bytes)}
    identity = {
        "audit_type": "panel-artifact",
        "panel_id": manifest.get("panel_id"),
        "input_audit_id": input_audit.get("panel_input_audit_id") if input_audit else None,
        "artifact_hashes": artifact_hashes,
        "findings": findings,
    }
    generated_at = str(manifest.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return _finalize(
        "panel-artifact",
        "panel_artifact_audit_id",
        "panel-artifact-audit-",
        findings,
        identity,
        generated_at,
        {
            "panel_id": manifest.get("panel_id"),
            "panel_input_audit_id": input_audit.get("panel_input_audit_id") if input_audit else None,
            "safe_to_publish": not any(item["execution_disposition"] == "blocked" for item in findings),
            "artifact_hashes": artifact_hashes,
            "resource_evidence": resource_evidence,
            "meeting_trace": {
                scenario: {
                    "meeting_pack_id": value.get("meeting_pack_id"),
                    "readiness": value.get("readiness"),
                    "lifecycle": value.get("lifecycle"),
                    "flow_selection_id": value.get("flow_selection_id"),
                    "flow_scope": copy.deepcopy(model.get("selection", {}).get("flow_scopes", {}).get(scenario)),
                }
                for scenario, value in model.get("data", {}).get("meetings", {}).items()
                if isinstance(value, dict)
            },
            "distribution": {"profile": profile, "redaction": copy.deepcopy(redaction)},
        },
    )


def write_audit_record(audit: dict[str, Any], output_dir: Path) -> Path:
    """Atomically create an immutable audit record; never touch panel artifacts."""
    id_field = "panel_input_audit_id" if audit.get("audit_type") == "panel-input" else "panel_artifact_audit_id"
    audit_id = str(audit[id_field])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{audit_id}.json"
    payload = canonical_json_bytes(audit)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable panel audit collision: {path}")
        return path
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=output_dir, delete=False) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        temp_path = Path(stream.name)
    try:
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError(f"immutable panel audit collision: {path}")
    finally:
        temp_path.unlink(missing_ok=True)
    return path
