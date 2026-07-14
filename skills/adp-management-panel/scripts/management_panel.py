#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Compose, publish, inspect, and archive the ADP static management panel."""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import panel_model


SCRIPT_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_ROOT.parent
ASSET_ROOT = SKILL_ROOT / "assets"
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
RESOURCE_PATH = ASSET_ROOT / "elk-resource-v1.json"
TEMPLATE_PATH = ASSET_ROOT / "panel-template.html"
STYLE_PATH = ASSET_ROOT / "panel.css"
APP_PATH = ASSET_ROOT / "panel.js"
PANEL_AUDIT_PATH = SKILL_ROOT.parent / "adp-state-audit/scripts/panel_audit.py"
ARTIFACT_AUDIT_PATH = SKILL_ROOT.parent / "adp-state-audit/scripts/audit_state.py"
GENERATOR_VERSION = panel_model.PANEL_GENERATOR_VERSION
VIEW_IDS = ("project-lead", "fde-morning", "business-biweekly")
PROFILE_IDS = ("internal-full", "shareable-summary")


class PanelError(RuntimeError):
    """A deterministic panel contract or publication failure."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("operation", nargs="?", choices=("refresh", "inspect", "archive"), default="refresh")
    parser.add_argument("--memory-root", default=DEFAULT_MEMORY_ROOT)
    parser.add_argument("--fixture", action="store_true", help="Use the frozen phase 6 source fixture (tests only).")
    parser.add_argument("--input-bundle", help="Fully composed canonical input bundle override.")
    parser.add_argument("--locale", default="zh-CN")
    parser.add_argument("--default-view", choices=VIEW_IDS, default="project-lead")
    parser.add_argument("--history-limit", type=int, default=12)
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--distribution-profile", choices=PROFILE_IDS)
    parser.add_argument("--expected-panel-id")
    parser.add_argument("--generated-at", help="Explicit RFC3339 timestamp for deterministic generation.")
    parser.add_argument("--output", help="Write the headless JSON result to this path.")
    return parser.parse_args(argv)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PanelError(f"required canonical input is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PanelError(f"invalid JSON at {path}: {exc}") from exc


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_utf8_lf_bytes(value: bytes, source: Path | str) -> bytes:
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PanelError(f"fixed text resource is not valid UTF-8: {source}") from exc
    return value.replace(b"\r\n", b"\n")


def resolve_memory_root(project_root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()


def verify_layout_resource(
    resource_path: Path = RESOURCE_PATH, skill_root: Path = SKILL_ROOT
) -> tuple[dict[str, Any], str]:
    resource = load_json(resource_path)
    bundle = skill_root / resource["bundle"]
    license_path = skill_root / resource["license"]
    if not bundle.is_file() or not license_path.is_file():
        raise PanelError("fixed ELK bundle or license is missing")
    if resource.get("engine_sha256_mode") != "utf8-lf":
        raise PanelError("fixed ELK bundle uses an unsupported checksum mode")
    canonical_bundle = canonical_utf8_lf_bytes(bundle.read_bytes(), bundle)
    actual = sha256_bytes(canonical_bundle)
    if actual != resource["engine_sha256"]:
        raise PanelError(f"fixed ELK bundle checksum mismatch: expected {resource['engine_sha256']}, got {actual}")
    license_text = license_path.read_text(encoding="utf-8")
    if "Eclipse Public License" not in license_text or "2.0" not in license_text:
        raise PanelError("fixed ELK license does not identify EPL-2.0")
    return resource, canonical_bundle.decode("utf-8")


def load_panel_audit_module() -> Any:
    spec = importlib.util.spec_from_file_location("adp_management_panel_audit", PANEL_AUDIT_PATH)
    if spec is None or spec.loader is None:
        raise PanelError(f"panel audit gate is unavailable: {PANEL_AUDIT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_artifact_audit_module() -> Any:
    spec = importlib.util.spec_from_file_location("adp_management_panel_artifact_audit", ARTIFACT_AUDIT_PATH)
    if spec is None or spec.loader is None:
        raise PanelError(f"artifact audit contract is unavailable: {ARTIFACT_AUDIT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_source_fingerprints(value: Any, source_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise PanelError(f"{source_name} source_fingerprints must be a non-empty object")
    normalized: dict[str, str] = {}
    for raw_path, raw_fingerprint in value.items():
        path = str(raw_path or "").strip()
        fingerprint = str(raw_fingerprint or "").strip().lower()
        digest = fingerprint.removeprefix("sha256:")
        if not path or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise PanelError(f"{source_name} contains an invalid source fingerprint for {path or '<empty>'}")
        normalized[path] = "sha256:" + digest
    return dict(sorted(normalized.items()))


def resolve_artifact_audit(
    memory_root: Path,
    artifact_path: Path,
    payload: dict[str, Any],
    source_name: str,
) -> tuple[str, Path]:
    audit_module = load_artifact_audit_module()
    fingerprint = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    candidates: list[tuple[int, str, str, Path]] = []
    for audit_path in sorted((memory_root / "audits").glob("*artifact-validation-*.json")):
        try:
            audit = load_json(audit_path)
            audit_id = str(audit.get("artifact_validation_id") or "")
            if (
                audit.get("audit_type") != "artifact"
                or not audit_id
                or audit.get("safe_to_publish") is not True
                or audit.get("execution_disposition") == "blocked"
                or audit_module.stable_artifact_validation_id(audit) != audit_id
                or audit_module.audit_content_hash(audit) != audit.get("audit_content_hash")
                or audit.get("input_audit_id") != payload.get("input_audit_id")
                or audit.get("baseline_revision") != payload.get("baseline_revision")
            ):
                continue
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        for artifact in audit.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            recorded = str(artifact.get("fingerprint") or "").lower().removeprefix("sha256:")
            metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
            if recorded != fingerprint:
                continue
            if source_name == "program-status" and metadata.get("snapshot_id") != payload.get("snapshot_id"):
                continue
            if source_name == "roadmap" and (
                metadata.get("program_status_snapshot_id") != payload.get("program_status_snapshot_id")
            ):
                continue
            if source_name.endswith("-meeting-pack") and metadata.get("meeting_pack_id") != payload.get("meeting_pack_id"):
                continue
            recorded_path = Path(str(artifact.get("path") or "")).expanduser()
            try:
                exactness = 2 if recorded_path.resolve() == artifact_path.resolve() else 1 if recorded_path.name == artifact_path.name else 0
            except OSError:
                exactness = 1 if recorded_path.name == artifact_path.name else 0
            candidates.append((exactness, str(audit.get("generated_at") or ""), audit_id, audit_path))
    if not candidates:
        raise PanelError(
            f"{source_name} has no publishable immutable artifact audit matching its current fingerprint and identity"
        )
    _, _, audit_id, audit_path = max(candidates)
    return audit_id, audit_path


def attach_artifact_audit(
    memory_root: Path,
    artifact_path: Path,
    payload: dict[str, Any],
    source_name: str,
) -> tuple[dict[str, Any], Path]:
    result = copy.deepcopy(payload)
    audit_id, audit_path = resolve_artifact_audit(memory_root, artifact_path, result, source_name)
    existing = result.get("artifact_audit_id")
    if existing not in (None, "", audit_id):
        raise PanelError(f"{source_name} embedded artifact_audit_id conflicts with its immutable audit report")
    result["artifact_audit_id"] = audit_id
    result["source_fingerprints"] = normalize_source_fingerprints(result.get("source_fingerprints"), source_name)
    return result, audit_path


def latest_json(root: Path, scenario: str) -> Path:
    candidates = [path for path in root.rglob("*.json") if scenario in str(path).lower()]
    if not candidates:
        raise PanelError(f"canonical {scenario} meeting pack is missing under {root}")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def history_projection(snapshot: dict[str, Any], path: Path) -> dict[str, Any]:
    progress = snapshot.get("progress", {})
    overall = progress.get("overall", {}) if isinstance(progress, dict) else {}
    current = overall.get("current", {}) if isinstance(overall, dict) else {}
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "as_of": snapshot.get("as_of"),
        "baseline_revision": snapshot.get("baseline_revision"),
        "overall_status": snapshot.get("overall_status"),
        "report_confidence": snapshot.get("report_confidence"),
        "progress_current": copy.deepcopy(current),
        "source_fingerprint": sha256_bytes(path.read_bytes()),
    }


def enrich_pack(pack: dict[str, Any], memory_root: Path | None = None) -> dict[str, Any]:
    result = copy.deepcopy(pack)
    metadata = result.get("panel_metadata") if isinstance(result.get("panel_metadata"), dict) else {}
    subgraph = result.get("flow_subgraph", {})
    for key in (
        "meeting_pack_id",
        "scenario",
        "meeting_window",
        "program_status_snapshot_id",
        "baseline_revision",
        "input_audit_id",
        "artifact_audit_id",
        "readiness",
        "lifecycle",
        "flow_selection_id",
        "flow_scope_id",
        "selected_node_ids",
        "selected_edge_ids",
        "official_panel_archive",
    ):
        if result.get(key) in (None, "", []):
            if metadata.get(key) not in (None, "", []):
                result[key] = copy.deepcopy(metadata[key])
    result.setdefault("scenario", subgraph.get("scenario"))
    result.setdefault("program_status_snapshot_id", result.get("program_status", {}).get("snapshot_id"))
    result.setdefault("baseline_revision", result.get("program_status", {}).get("baseline_revision"))
    result.setdefault("flow_selection_id", subgraph.get("selection_id"))
    result.setdefault("flow_scope_id", subgraph.get("scope_id"))
    result.setdefault("selected_node_ids", [item.get("node_id") for item in subgraph.get("nodes", []) if item.get("node_id")])
    result.setdefault("selected_edge_ids", [item.get("edge_id") for item in subgraph.get("edges", []) if item.get("edge_id")])
    result.setdefault("source_fingerprints", result.get("lineage", {}).get("source_fingerprints", {}))
    result["source_fingerprints"] = normalize_source_fingerprints(
        result.get("source_fingerprints"), str(result.get("scenario") or "meeting-pack")
    )
    if memory_root is not None and result.get("meeting_pack_id"):
        lifecycle = meeting_receipt_lifecycle(memory_root, str(result["meeting_pack_id"]))
        result["lifecycle"] = lifecycle["lifecycle"]
        result.pop("official_panel_archive", None)
        if lifecycle.get("official_panel_archive"):
            result["official_panel_archive"] = lifecycle["official_panel_archive"]
    required = (
        "meeting_pack_id",
        "scenario",
        "meeting_window",
        "program_status_snapshot_id",
        "baseline_revision",
        "input_audit_id",
        "artifact_audit_id",
        "readiness",
        "lifecycle",
        "flow_selection_id",
    )
    missing = [key for key in required if not result.get(key)]
    if missing:
        raise PanelError("meeting pack lacks panel contract fields: " + ", ".join(missing))
    if result["readiness"] not in {"ready", "degraded", "blocked"}:
        raise PanelError("meeting pack readiness is invalid")
    if result["lifecycle"] not in {"current-derived", "pre-meeting-snapshot", "post-sync-official", "sync-failed"}:
        raise PanelError("meeting pack lifecycle is invalid")
    if result["lifecycle"] == "post-sync-official":
        official = result.get("official_panel_archive")
        if not isinstance(official, dict) or official.get("receipt_status") != "applied" or not official.get("panel_id"):
            raise PanelError("post-sync-official meeting pack lacks an applied receipt panel association")
    return result


def meeting_receipt_lifecycle(memory_root: Path, meeting_pack_id: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for path in sorted((memory_root / "meetings/receipts").glob("*.json")):
        try:
            receipt = load_json(path)
        except (OSError, json.JSONDecodeError, PanelError):
            continue
        if receipt.get("lineage", {}).get("meeting_pack_id") == meeting_pack_id:
            matches.append(receipt)
    if not matches:
        return {"lifecycle": "pre-meeting-snapshot"}
    receipt = max(matches, key=lambda item: str(item.get("applied_at") or item.get("started_at") or ""))
    if receipt.get("status") != "applied" or receipt.get("sync_status") == "failed":
        return {"lifecycle": "sync-failed"}
    official = receipt.get("official_panel_archive")
    if isinstance(official, dict) and official.get("panel_id"):
        evidence = copy.deepcopy(official)
        evidence["receipt_status"] = "applied"
        return {"lifecycle": "post-sync-official", "official_panel_archive": evidence}
    return {"lifecycle": "current-derived"}


def load_memory_inputs(memory_root: Path, history_limit: int) -> dict[str, Any]:
    status_path = memory_root / "views/program-status.json"
    roadmap_path = memory_root / "views/roadmap.json"
    graph_path = memory_root / "views/flow-graph.json"
    packs_root = memory_root / "views/meeting-packs"
    status, status_audit_path = attach_artifact_audit(
        memory_root, status_path, load_json(status_path), "program-status"
    )
    roadmap, roadmap_audit_path = attach_artifact_audit(
        memory_root, roadmap_path, load_json(roadmap_path), "roadmap"
    )
    graph = load_json(graph_path)
    pack_paths = {scenario: latest_json(packs_root, scenario) for scenario in ("fde-morning", "business-biweekly")}
    pack_audits: dict[str, Path] = {}
    packs: dict[str, dict[str, Any]] = {}
    for scenario, path in pack_paths.items():
        attached, pack_audits[scenario] = attach_artifact_audit(
            memory_root, path, load_json(path), f"{scenario}-meeting-pack"
        )
        packs[scenario] = enrich_pack(attached, memory_root)
    history: list[dict[str, Any]] = []
    history_paths: list[Path] = []
    for path in sorted((memory_root / "snapshots/program-status").glob("*.json"), reverse=True):
        item = load_json(path)
        if item.get("snapshot_id") == status.get("snapshot_id"):
            continue
        history.append(history_projection(item, path))
        history_paths.append(path)
        if len(history) >= history_limit:
            break
    return {
        "program_status": status,
        "roadmap": roadmap,
        "flow_graph": graph,
        "meeting_packs": packs,
        "history": history,
        "shareable_policy": default_shareable_policy(graph),
        "_panel_source_paths": {
            "program-status": str(status_path),
            "program-status-artifact-audit": str(status_audit_path),
            "roadmap": str(roadmap_path),
            "roadmap-artifact-audit": str(roadmap_audit_path),
            "flow-graph": str(graph_path),
            **{f"{scenario}-meeting-pack": str(path) for scenario, path in pack_paths.items()},
            **{f"{scenario}-meeting-pack-artifact-audit": str(path) for scenario, path in pack_audits.items()},
            **{f"history-{index:03d}": str(path) for index, path in enumerate(history_paths, start=1)},
        },
    }


def default_shareable_policy(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("topology", {}).get("nodes", [])
    visible_nodes = [item["node_id"] for item in nodes if item.get("lane", {}).get("lane_type") == "program"]
    if not visible_nodes:
        visible_nodes = [item["node_id"] for item in nodes[:40] if item.get("node_id")]
    node_set = set(visible_nodes)
    visible_edges = [
        item["edge_id"]
        for item in graph.get("topology", {}).get("edges", [])
        if item.get("predecessor") in node_set and item.get("target") in node_set
    ]
    return {
        "policy_version": "1.0.0",
        "visible_node_ids": visible_nodes,
        "visible_edge_ids": visible_edges,
        "removed_fields": [
            "id", "owner", "counts", "allocations", "source", "sources", "source_fingerprints",
            "source_refs", "paths", "lineage", "value_lineage", "milestone_ids", "next_milestone_ids",
            "selected_node_ids", "selected_edge_ids",
        ],
    }


def build_request(
    inputs: dict[str, Any], resource: dict[str, Any], args: argparse.Namespace, profile: str
) -> dict[str, Any]:
    status = inputs["program_status"]
    graph = inputs["flow_graph"]
    forecast = status.get("progress", {}).get("overall", {}).get("series", {}).get("forecast_points", [])
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    node_ids = [item["node_id"] for item in graph.get("topology", {}).get("nodes", []) if item.get("node_id")][:40]
    node_set = set(node_ids)
    edge_ids = [
        item["edge_id"]
        for item in graph.get("topology", {}).get("edges", [])
        if item.get("predecessor") in node_set and item.get("target") in node_set
    ]
    config_hash = panel_model.canonical_hash(resource["options"])
    return {
        "generated_at": generated_at,
        "locale": args.locale,
        "distribution_profile": profile,
        "history_snapshot_ids": [item["snapshot_id"] for item in inputs.get("history", [])[: args.history_limit]],
        "future_horizon_dates": [item["horizon_date"] for item in forecast if item.get("horizon_date")],
        "project_lead_node_ids": node_ids,
        "project_lead_edge_ids": edge_ids,
        "layout": {
            "layout_contract_version": resource["layout_contract_version"],
            "engine": resource["engine"],
            "engine_version": resource["engine_version"],
            "engine_license": resource["engine_license"],
            "engine_sha256": resource["engine_sha256"],
            "config_sha256": config_hash,
            "node_dimensions_version": resource["node_dimensions_version"],
        },
    }


def load_inputs(args: argparse.Namespace, resource: dict[str, Any], profile: str, memory_root: Path) -> dict[str, Any]:
    if args.fixture:
        inputs = panel_model.load_source_fixture()
    elif args.input_bundle:
        input_path = Path(args.input_bundle).expanduser().resolve()
        inputs = load_json(input_path)
        inputs["_panel_source_paths"] = {"input-bundle": str(input_path)}
    else:
        inputs = load_memory_inputs(memory_root, args.history_limit)
    inputs = copy.deepcopy(inputs)
    inputs["request"] = build_request(inputs, resource, args, profile)
    return inputs


def run_input_audit_gate(
    inputs: dict[str, Any], args: argparse.Namespace, memory_root: Path
) -> tuple[dict[str, Any], Path]:
    module = load_panel_audit_module()
    status_as_of = inputs.get("program_status", {}).get("as_of")
    try:
        audit_date = datetime.fromisoformat(str(status_as_of)).date()
    except ValueError as exc:
        raise PanelError("program-status as_of is invalid before panel audit") from exc
    audit = module.audit_panel_inputs(inputs, as_of=audit_date, max_age_days=getattr(args, "max_age_days", 7))
    audit_path = module.write_audit_record(audit, memory_root / "audits/management-panel")
    if audit["execution_disposition"] == "blocked":
        codes = [item["code"] for item in audit["blocking_gaps"]]
        raise PanelError("panel pre-render audit blocked: " + ", ".join(codes))
    request = inputs["request"]
    request["panel_input_audit_id"] = audit["panel_input_audit_id"]
    request["panel_input_audit_disposition"] = audit["execution_disposition"]
    request["panel_input_audit_findings"] = [item["code"] for item in [*audit["blocking_gaps"], *audit["warnings"]]]
    request["panel_input_audit_workflows"] = list(audit["recommended_workflows"])
    return audit, audit_path


def run_artifact_audit_gate(
    model: dict[str, Any],
    bundle: bytes,
    rendered: bytes,
    inputs: dict[str, Any],
    input_audit: dict[str, Any],
    memory_root: Path,
    publication_targets: dict[str, Path],
) -> tuple[dict[str, Any], Path]:
    module = load_panel_audit_module()
    audit = module.audit_panel_artifacts(
        model,
        bundle,
        rendered,
        input_audit=input_audit,
        source_inputs=inputs,
        publication_targets=publication_targets,
    )
    audit_path = module.write_audit_record(audit, memory_root / "audits/management-panel")
    if audit["execution_disposition"] == "blocked":
        codes = [item["code"] for item in audit["blocking_gaps"]]
        raise PanelError("panel post-render audit blocked: " + ", ".join(codes))
    return audit, audit_path


def static_fallback(model: dict[str, Any]) -> str:
    status = model["data"]["status"]
    overall = status.get("progress", {}).get("overall", {})
    current = overall.get("current", {})
    forecast = overall.get("forecast_summary", {})
    lines = [
        '<main id="no-js-content" class="no-js-content">',
        '<section aria-labelledby="fallback-heading"><h1 id="fallback-heading">ADP Management Panel</h1>',
        f'<p><strong>As of:</strong> {html.escape(str(status.get("as_of", "-")))}</p>',
        f'<p><strong>Status:</strong> {html.escape(str(status.get("overall_status", "-")))}; '
        f'<strong>Confidence:</strong> {html.escape(str(status.get("report_confidence", "-")))}</p>',
        '<dl class="metric-fallback">',
    ]
    for label, value in (
        ("Actual", current.get("actual_completion_percent")),
        ("Planned", current.get("planned_completion_percent")),
        ("Gap (pp)", current.get("completion_gap_pp")),
        ("Next forecast", forecast.get("forecast_completion_percent")),
        ("Forecast coverage", forecast.get("forecast_coverage_percent")),
    ):
        lines.append(f"<div><dt>{html.escape(label)}</dt><dd>{html.escape(str(value if value is not None else 'not measurable'))}</dd></div>")
    lines.extend(["</dl></section>", '<nav aria-label="Views"><a href="#project-lead">Project lead</a> <a href="#fde-morning">FDE morning</a> <a href="#business-biweekly">Business biweekly</a></nav>'])
    for view_id in VIEW_IDS:
        flow = model["data"]["flows"].get(view_id, {})
        states = {item.get("node_id"): item for item in flow.get("node_states", [])}
        meeting = {}
        lines.append(f'<section id="{view_id}" class="fallback-view"><h2>{html.escape(view_id)}</h2>')
        if view_id != "project-lead":
            meeting = model["data"]["meetings"].get(view_id, {})
            window = meeting.get("meeting_window", {})
            lines.append(
                f'<p>Pack ID: {html.escape(str(meeting.get("meeting_pack_id", "unavailable")))}; '
                f'Meeting window: {html.escape(str(window.get("start", "unavailable")))} to {html.escape(str(window.get("end", "unavailable")))} '
                f'({html.escape(str(window.get("status", "unavailable")))}); '
                f'readiness: {html.escape(str(meeting.get("readiness", "unavailable")))}; '
                f'lifecycle: {html.escape(str(meeting.get("lifecycle", "unavailable")))}</p>'
            )
        lines.append('<h3>Dependency order and canonical state</h3>')
        empty_state = flow.get("empty_state") if isinstance(flow.get("empty_state"), dict) else None
        if empty_state is not None:
            window = empty_state.get("meeting_window") if isinstance(empty_state.get("meeting_window"), dict) else {}
            if not window.get("start") or not window.get("end"):
                window = meeting.get("meeting_window") if isinstance(meeting.get("meeting_window"), dict) else window
            confirmed = empty_state.get("confirmed") or window.get("status") == "confirmed"
            heading = (
                "No explicitly related plan items in this confirmed scope"
                if confirmed
                else "No explicitly related plan items in this scope"
            )
            node_count = empty_state.get("node_count", 0)
            edge_count = empty_state.get("edge_count", 0)
            unmapped_count = empty_state.get("unmapped_count", 0)
            recovery = " ".join(str(item) for item in empty_state.get("recovery", []) if item)
            if not recovery:
                recovery = "Confirm explicit owner relations in the owning action/risk workflow, then refresh the graph and meeting pack."
            lines.extend(
                [
                    '<div class="flow-empty-state">',
                    f"<h4>{html.escape(heading)}</h4>",
                    f'<p>Window {html.escape(str(window.get("start", "TBD")))} to {html.escape(str(window.get("end", "TBD")))} '
                    f"selected {html.escape(str(node_count))} canonical nodes and {html.escape(str(edge_count))} canonical edges.</p>",
                    '<p class="warning">This empty scope is not proof of no delivery risk. Owner outputs contain no explicit '
                    "related_plan_item_ids or related_flow_edge_ids for these overlays.</p>",
                    '<dl class="flow-empty-metrics">',
                    f"<div><dt>Selected nodes</dt><dd>{html.escape(str(node_count))}</dd></div>",
                    f"<div><dt>Selected edges</dt><dd>{html.escape(str(edge_count))}</dd></div>",
                    f"<div><dt>Unmapped overlays</dt><dd>{html.escape(str(unmapped_count))}</dd></div>",
                    "</dl>",
                    f'<p class="flow-empty-recovery">Recovery: {html.escape(recovery)}</p>',
                    f'<details class="flow-empty-sources"><summary>Canonical unmapped source details ({html.escape(str(unmapped_count))})</summary><ul>',
                ]
            )
            for item in empty_state.get("source_details", []):
                if not isinstance(item, dict):
                    continue
                identity = " / ".join(str(item.get(key)) for key in ("source_kind", "source_id") if item.get(key)) or "unmapped source"
                detail = item.get("reason") or item.get("finding_code") or "No explicit canonical relation."
                lines.append(f"<li><strong>{html.escape(identity)}</strong> - {html.escape(str(detail))}</li>")
            lines.append("</ul></details></div>")
        else:
            lines.append('<ol class="stage-list">')
            for node in flow.get("nodes", []):
                state = states.get(node.get("node_id"), {})
                execution = state.get("execution", {}).get("value", "indeterminate")
                health = state.get("health", {}).get("value", "indeterminate")
                label = node.get("name") or node.get("node_id") or "unnamed"
                lines.append(f'<li><strong>{html.escape(str(label))}</strong> - {html.escape(str(execution))} / {html.escape(str(health))}</li>')
            lines.append("</ol>")
        lines.append("<details><summary>Sources</summary><ul>")
        for name, value in sorted(model["manifest"].get("source_fingerprints", {}).items()):
            lines.append(f"<li>{html.escape(str(name))}: <code>{html.escape(str(value))}</code></li>")
        lines.append("</ul></details></section>")
    redaction = model["manifest"]["redaction"]
    if redaction["hidden_nodes"] or redaction["hidden_edges"]:
        lines.append(f'<p class="warning">Part of the topology is hidden: {redaction["hidden_nodes"]} nodes, {redaction["hidden_edges"]} edges. No path was reconnected.</p>')
    lines.append("</main>")
    return "".join(lines)


def render_html(model: dict[str, Any], elk_js: str, default_view: str) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "{{PANEL_CSS}}": STYLE_PATH.read_text(encoding="utf-8"),
        "{{PANEL_JS}}": APP_PATH.read_text(encoding="utf-8"),
        "{{ELK_JS}}": elk_js,
        "{{MODEL_JSON}}": panel_model.safe_json_for_script(model),
        "{{MANIFEST_JSON}}": panel_model.safe_json_for_script(model["manifest"]),
        "{{DEFAULT_VIEW}}": default_view,
        "{{STATIC_FALLBACK}}": static_fallback(model),
    }
    for marker, value in replacements.items():
        if marker not in template:
            raise PanelError(f"panel template marker is missing: {marker}")
        template = template.replace(marker, value)
    return template.encode("utf-8")


def atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def immutable_write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise PanelError(f"immutable artifact collision: {path}")
        return "reused"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_name, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise PanelError(f"immutable artifact collision: {path}")
        return "created"
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def compose(
    args: argparse.Namespace, memory_root: Path, profile: str
) -> tuple[dict[str, Any], bytes, bytes, dict[str, Any], dict[str, Any], Path]:
    resource, elk_js = verify_layout_resource()
    inputs = load_inputs(args, resource, profile, memory_root)
    input_audit, input_audit_path = run_input_audit_gate(inputs, args, memory_root)
    model = panel_model.compose_panel(inputs)
    model_errors = panel_model.validate_schema(model, load_json(panel_model.PANEL_SCHEMA_PATH))
    manifest_errors = panel_model.validate_schema(model["manifest"], load_json(panel_model.MANIFEST_SCHEMA_PATH))
    if model_errors or manifest_errors:
        raise PanelError("composed panel violates phase 6 schema: " + "; ".join([*model_errors, *manifest_errors]))
    bundle = canonical_json_bytes(model)
    rendered = render_html(model, elk_js, args.default_view)
    return model, bundle, rendered, inputs, input_audit, input_audit_path


def extract_script(html_text: str, element_id: str) -> dict[str, Any]:
    opener = f'<script type="application/json" id="{element_id}">'
    start = html_text.find(opener)
    if start < 0:
        raise PanelError(f"embedded {element_id} is missing")
    start += len(opener)
    end = html_text.find("</script>", start)
    if end < 0:
        raise PanelError(f"embedded {element_id} is unterminated")
    return json.loads(html_text[start:end])


def inspect_current(memory_root: Path, expected_panel_id: str | None = None) -> dict[str, Any]:
    resource, _ = verify_layout_resource()
    current = memory_root / "views/management-panel/index.html"
    if not current.exists():
        raise PanelError(f"current panel is missing: {current}")
    text = current.read_text(encoding="utf-8")
    manifest = extract_script(text, "adp-panel-manifest")
    model = extract_script(text, "adp-panel-model")
    if manifest != model.get("manifest"):
        raise PanelError("embedded manifest differs from embedded panel bundle")
    if expected_panel_id and manifest.get("panel_id") != expected_panel_id:
        raise PanelError(f"panel id mismatch: expected {expected_panel_id}, got {manifest.get('panel_id')}")
    bundle_path = memory_root / "snapshots/management-panel" / f"{manifest['panel_id']}.json"
    if not bundle_path.exists() or load_json(bundle_path) != model:
        raise PanelError("current HTML does not match its immutable panel bundle")
    if manifest.get("layout", {}).get("engine_sha256") != resource["engine_sha256"]:
        raise PanelError("embedded ELK metadata differs from fixed resource")
    audit_module = load_panel_audit_module()
    artifact_audit = audit_module.audit_panel_artifacts(
        model,
        bundle_path.read_bytes(),
        current.read_bytes(),
        publication_targets={"bundle": bundle_path},
    )
    artifact_audit_path = audit_module.write_audit_record(
        artifact_audit, memory_root / "audits/management-panel"
    )
    if artifact_audit["execution_disposition"] == "blocked":
        codes = [item["code"] for item in artifact_audit["blocking_gaps"]]
        raise PanelError("panel artifact inspection blocked: " + ", ".join(codes))
    return {
        "ok": True,
        "status": "complete",
        "operation": "inspect",
        "panel_id": manifest["panel_id"],
        "panel_model_id": manifest["panel_model_id"],
        "layout_id": manifest["layout_id"],
        "recovery_status": manifest["recovery_status"],
        "distribution_profile": manifest["distribution_profile"],
        "current_html": str(current),
        "immutable_bundle": str(bundle_path),
        "html_sha256": sha256_bytes(current.read_bytes()),
        "elk_sha256": resource["engine_sha256"],
        "panel_artifact_audit_id": artifact_audit["panel_artifact_audit_id"],
        "panel_artifact_audit": str(artifact_audit_path),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise PanelError(f"project_root is not a directory: {project_root}")
    memory_root = resolve_memory_root(project_root, args.memory_root)
    if args.operation == "inspect":
        return inspect_current(memory_root, args.expected_panel_id)
    if args.operation == "archive" and not args.distribution_profile:
        raise PanelError("archive requires --distribution-profile internal-full|shareable-summary")
    profile = args.distribution_profile or "internal-full"
    model, bundle, rendered, inputs, input_audit, input_audit_path = compose(args, memory_root, profile)
    panel_id = model["panel_id"]
    bundle_path = memory_root / "snapshots/management-panel" / f"{panel_id}.json"
    if bundle_path.exists():
        existing = load_json(bundle_path)
        identities = ("panel_id", "panel_model_id")
        if all(existing.get(key) == model.get(key) for key in identities) and existing.get("manifest", {}).get("layout_id") == model["manifest"]["layout_id"]:
            model = existing
            bundle = canonical_json_bytes(existing)
            inputs["request"]["generated_at"] = existing["manifest"]["generated_at"]
            _, elk_js = verify_layout_resource()
            rendered = render_html(existing, elk_js, args.default_view)
    publication_targets = {"bundle": bundle_path}
    if args.operation == "archive":
        publication_targets["html"] = memory_root / "snapshots/management-panel" / f"{panel_id}.html"
    artifact_audit, artifact_audit_path = run_artifact_audit_gate(
        model,
        bundle,
        rendered,
        inputs,
        input_audit,
        memory_root,
        publication_targets,
    )
    bundle_state = immutable_write(bundle_path, bundle)
    result = {
        "ok": True,
        "status": "complete",
        "operation": args.operation,
        "panel_id": panel_id,
        "panel_model_id": model["panel_model_id"],
        "layout_id": model["manifest"]["layout_id"],
        "recovery_status": model["recovery"]["status"],
        "distribution_profile": profile,
        "immutable_bundle": str(bundle_path),
        "bundle_state": bundle_state,
        "recommended_workflows": model["recovery"]["workflows"],
        "panel_input_audit_id": input_audit["panel_input_audit_id"],
        "panel_input_audit": str(input_audit_path),
        "panel_artifact_audit_id": artifact_audit["panel_artifact_audit_id"],
        "panel_artifact_audit": str(artifact_audit_path),
    }
    if args.operation == "refresh":
        current = memory_root / "views/management-panel/index.html"
        atomic_replace(current, rendered)
        result["current_html"] = str(current)
        result["current_html_sha256"] = sha256_bytes(rendered)
    else:
        archive = memory_root / "snapshots/management-panel" / f"{panel_id}.html"
        result["archive_state"] = immutable_write(archive, rendered)
        result["archive_html"] = str(archive)
        result["archive_html_sha256"] = sha256_bytes(rendered)
    return result


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
    if output:
        atomic_replace(Path(output).expanduser().resolve(), payload.encode("utf-8"))
    else:
        sys.stdout.write(payload)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (PanelError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        result = {
            "ok": False,
            "status": "blocked",
            "operation": args.operation,
            "reason": str(exc),
            "recommended_workflows": ["adp-state-audit", "adp-management-panel"],
        }
    emit(result, args.output)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
