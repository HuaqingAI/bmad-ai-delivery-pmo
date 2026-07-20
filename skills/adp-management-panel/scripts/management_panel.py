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
from contextlib import contextmanager
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
MEETING_LIFECYCLES = {"current-derived", "pre-meeting-snapshot", "post-sync-official", "sync-failed"}
SELECTION_POLICY_VERSION = "1.0.0"
GENERIC_RECOVERY_WORKFLOWS = ("adp-state-audit", "adp-management-panel")
PROGRAM_STATUS_RECOVERY = ("adp-state-audit", "adp-program-status")
ROADMAP_RECOVERY = ("adp-roadmap-sync",)
AUDITED_ROADMAP_RECOVERY = ("adp-state-audit", "adp-roadmap-sync")
FLOW_GRAPH_RECOVERY = ("adp-flow-graph",)
MEETING_PACK_RECOVERY = ("adp-meeting-pack",)
AUDITED_MEETING_PACK_RECOVERY = ("adp-state-audit", "adp-meeting-pack")
HISTORY_RECOVERY = ("adp-program-status",)
SELECTION_POLICY_RECOVERY = ("adp-management-panel",)
SHAREABLE_REMOVED_FIELDS = (
    "allocations",
    "counts",
    "id",
    "lineage",
    "milestone_ids",
    "next_milestone_ids",
    "owner",
    "paths",
    "selected_edge_ids",
    "selected_node_ids",
    "source",
    "source_fingerprints",
    "source_refs",
    "sources",
    "value_lineage",
)


class PanelError(RuntimeError):
    """A deterministic panel contract or publication failure."""

    def __init__(
        self,
        message: str,
        recommended_workflows: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.recommended_workflows = tuple(recommended_workflows) if recommended_workflows else None


@contextmanager
def recovery_route(*recommended_workflows: str):
    """Attach recovery ownership without replacing a more specific route."""

    try:
        yield
    except (PanelError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        if isinstance(exc, PanelError) and exc.recommended_workflows:
            raise
        raise PanelError(str(exc), recommended_workflows) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("operation", nargs="?", choices=("refresh", "inspect", "archive"), default="refresh")
    parser.add_argument("--memory-root", default=DEFAULT_MEMORY_ROOT)
    parser.add_argument("--fixture", action="store_true", help="Use the frozen panel-contract source fixture (tests only).")
    parser.add_argument("--input-bundle", help="Fully composed canonical input bundle override.")
    parser.add_argument(
        "--selection-policy",
        help="Explicit history, project-lead scope, and shareable allowlist JSON for canonical-memory compose.",
    )
    parser.add_argument("--locale", default="zh-CN")
    parser.add_argument("--default-view", choices=VIEW_IDS, default="project-lead")
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
    if resource.get("engine_license") != "EPL-2.0":
        raise PanelError("fixed ELK resource engine_license must be EPL-2.0")
    if resource.get("license_sha256_mode") != "utf8-lf":
        raise PanelError("fixed ELK license uses an unsupported checksum mode")
    canonical_license = canonical_utf8_lf_bytes(license_path.read_bytes(), license_path)
    actual_license = sha256_bytes(canonical_license)
    if actual_license != resource.get("license_sha256"):
        raise PanelError(
            f"fixed ELK license checksum mismatch: expected {resource.get('license_sha256')}, got {actual_license}"
        )
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


def structured_datetime(value: Any, source: Path) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PanelError(f"meeting pack generated_at is missing: {source}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PanelError(f"meeting pack generated_at is invalid: {source}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_current_meeting_pack(root: Path, scenario: str) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in sorted((root / scenario).rglob("*.json")):
        pack = load_json(path)
        if not isinstance(pack, dict):
            continue
        metadata = pack.get("panel_metadata") if isinstance(pack.get("panel_metadata"), dict) else {}
        declared_scenario = pack.get("scenario") or metadata.get("scenario")
        if declared_scenario != scenario:
            continue
        if pack.get("scenario") and metadata.get("scenario") and pack["scenario"] != metadata["scenario"]:
            raise PanelError(f"meeting pack scenario metadata conflicts: {path}")
        pack_id = pack.get("meeting_pack_id") or metadata.get("meeting_pack_id")
        if not isinstance(pack_id, str) or not pack_id:
            raise PanelError(f"meeting pack identity is missing: {path}")
        if pack.get("meeting_pack_id") and metadata.get("meeting_pack_id") and pack["meeting_pack_id"] != metadata["meeting_pack_id"]:
            raise PanelError(f"meeting pack identity metadata conflicts: {path}")
        lifecycle = pack.get("lifecycle") or metadata.get("lifecycle")
        if lifecycle not in MEETING_LIFECYCLES:
            raise PanelError(f"meeting pack lifecycle metadata is invalid: {path}")
        if pack.get("lifecycle") and metadata.get("lifecycle") and pack["lifecycle"] != metadata["lifecycle"]:
            raise PanelError(f"meeting pack lifecycle metadata conflicts: {path}")
        generated_at = pack.get("generated_at") or metadata.get("generated_at")
        candidates.append((structured_datetime(generated_at, path), path, pack))
    if not candidates:
        raise PanelError(f"canonical {scenario} meeting pack is missing under {root / scenario}")
    latest_generated_at = max(item[0] for item in candidates)
    current = [item for item in candidates if item[0] == latest_generated_at]
    if len(current) != 1:
        paths = ", ".join(str(item[1]) for item in current)
        raise PanelError(f"canonical {scenario} meeting pack identity is ambiguous at {latest_generated_at.isoformat()}: {paths}")
    _, path, pack = current[0]
    return path, pack


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
    if result["lifecycle"] not in MEETING_LIFECYCLES:
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


def history_snapshot_index(
    memory_root: Path, current_snapshot_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    snapshots: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for path in (memory_root / "snapshots/program-status").glob("*.json"):
        item = load_json(path)
        snapshot_id = item.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise PanelError(f"program-status history lacks snapshot_id: {path}")
        if snapshot_id == current_snapshot_id:
            continue
        if snapshot_id in snapshots:
            raise PanelError(f"program-status history snapshot_id is ambiguous: {snapshot_id}")
        snapshots[snapshot_id] = history_projection(item, path)
        paths[snapshot_id] = path
    return snapshots, paths


def load_memory_inputs(
    memory_root: Path, policy: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    status_path = memory_root / "views/program-status.json"
    roadmap_path = memory_root / "views/roadmap.json"
    graph_path = memory_root / "views/flow-graph.json"
    packs_root = memory_root / "views/meeting-packs"
    with recovery_route(*PROGRAM_STATUS_RECOVERY):
        status = load_json(status_path)
        status, status_audit_path = attach_artifact_audit(
            memory_root, status_path, status, "program-status"
        )
    with recovery_route(*ROADMAP_RECOVERY):
        roadmap = load_json(roadmap_path)
    with recovery_route(*AUDITED_ROADMAP_RECOVERY):
        roadmap, roadmap_audit_path = attach_artifact_audit(
            memory_root, roadmap_path, roadmap, "roadmap"
        )
    with recovery_route(*FLOW_GRAPH_RECOVERY):
        graph = load_json(graph_path)
    with recovery_route(*MEETING_PACK_RECOVERY):
        resolved_packs = {
            scenario: resolve_current_meeting_pack(packs_root, scenario)
            for scenario in ("fde-morning", "business-biweekly")
        }
    pack_paths = {scenario: resolved[0] for scenario, resolved in resolved_packs.items()}
    pack_audits: dict[str, Path] = {}
    packs: dict[str, dict[str, Any]] = {}
    for scenario, path in pack_paths.items():
        with recovery_route(*AUDITED_MEETING_PACK_RECOVERY):
            attached, pack_audits[scenario] = attach_artifact_audit(
                memory_root, path, resolved_packs[scenario][1], f"{scenario}-meeting-pack"
            )
        with recovery_route(*MEETING_PACK_RECOVERY):
            packs[scenario] = enrich_pack(attached, memory_root)
    with recovery_route(*HISTORY_RECOVERY):
        history_by_id, history_paths_by_id = history_snapshot_index(
            memory_root, status["snapshot_id"]
        )
    with recovery_route(*SELECTION_POLICY_RECOVERY):
        selection = validate_selection_policy(graph, policy, set(history_by_id))
    history_ids = selection["history_snapshot_ids"]
    inputs = {
        "program_status": status,
        "roadmap": roadmap,
        "flow_graph": graph,
        "meeting_packs": packs,
        "history": [history_by_id[snapshot_id] for snapshot_id in history_ids],
        "_panel_source_paths": {
            "program-status": str(status_path),
            "program-status-artifact-audit": str(status_audit_path),
            "roadmap": str(roadmap_path),
            "roadmap-artifact-audit": str(roadmap_audit_path),
            "flow-graph": str(graph_path),
            **{f"{scenario}-meeting-pack": str(path) for scenario, path in pack_paths.items()},
            **{f"{scenario}-meeting-pack-artifact-audit": str(path) for scenario, path in pack_audits.items()},
            **{
                f"history-{index:03d}": str(history_paths_by_id[snapshot_id])
                for index, snapshot_id in enumerate(history_ids, start=1)
            },
        },
    }
    return inputs, selection


def validated_ids(
    value: Any, label: str, available: set[str], *, preserve_order: bool = False
) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PanelError(f"selection policy {label} must be an array of non-empty canonical IDs")
    if len(value) != len(set(value)):
        raise PanelError(f"selection policy {label} contains duplicate IDs")
    unknown = sorted(set(value) - available)
    if unknown:
        raise PanelError(f"selection policy {label} contains unknown IDs: {', '.join(unknown)}")
    return list(value) if preserve_order else sorted(value)


def validate_selection_policy(
    graph: dict[str, Any], policy: Any, available_history_ids: set[str]
) -> dict[str, Any]:
    if not isinstance(policy, dict) or policy.get("policy_version") != SELECTION_POLICY_VERSION:
        raise PanelError(f"selection policy must use policy_version {SELECTION_POLICY_VERSION}")
    if policy.get("flow_graph_id") != graph.get("flow_graph_id"):
        raise PanelError("selection policy flow_graph_id does not match the canonical flow graph")
    project = policy.get("project_lead") if isinstance(policy.get("project_lead"), dict) else {}
    shareable = policy.get("shareable") if isinstance(policy.get("shareable"), dict) else {}
    graph_nodes = {
        item["node_id"] for item in graph.get("topology", {}).get("nodes", []) if item.get("node_id")
    }
    graph_edges = {
        item["edge_id"]: item for item in graph.get("topology", {}).get("edges", []) if item.get("edge_id")
    }
    graph_scope_values = [
        item["scope_id"]
        for item in graph.get("overlays", {}).get("scopes", [])
        if isinstance(item, dict) and isinstance(item.get("scope_id"), str) and item["scope_id"]
    ]
    graph_scope_ids = set(graph_scope_values)
    if len(graph_scope_values) != len(graph_scope_ids):
        raise PanelError("canonical flow graph contains duplicate overlay scope IDs")
    history_snapshot_ids = validated_ids(
        policy.get("history_snapshot_ids"),
        "history_snapshot_ids",
        available_history_ids,
        preserve_order=True,
    )
    project_scope_id = project.get("scope_id")
    if not isinstance(project_scope_id, str) or not project_scope_id:
        raise PanelError("selection policy project_lead.scope_id must be a non-empty canonical ID")
    if project_scope_id not in graph_scope_ids:
        raise PanelError(f"selection policy project_lead.scope_id is unknown: {project_scope_id}")
    project_nodes = validated_ids(project.get("node_ids"), "project_lead.node_ids", graph_nodes)
    project_edges = validated_ids(project.get("edge_ids"), "project_lead.edge_ids", set(graph_edges))
    visible_nodes = validated_ids(shareable.get("visible_node_ids"), "shareable.visible_node_ids", graph_nodes)
    visible_edges = validated_ids(shareable.get("visible_edge_ids"), "shareable.visible_edge_ids", set(graph_edges))
    for label, node_ids, edge_ids in (
        ("project_lead", set(project_nodes), project_edges),
        ("shareable", set(visible_nodes), visible_edges),
    ):
        open_edges = [
            edge_id
            for edge_id in edge_ids
            if graph_edges[edge_id].get("predecessor") not in node_ids
            or graph_edges[edge_id].get("target") not in node_ids
        ]
        if open_edges:
            raise PanelError(f"selection policy {label} edges are not closed over selected nodes: {', '.join(open_edges)}")
    return {
        "history_snapshot_ids": history_snapshot_ids,
        "project_lead_scope_id": project_scope_id,
        "project_lead_node_ids": project_nodes,
        "project_lead_edge_ids": project_edges,
        "shareable_policy": {
            "policy_version": policy["policy_version"],
            "visible_node_ids": visible_nodes,
            "visible_edge_ids": visible_edges,
            "removed_fields": list(SHAREABLE_REMOVED_FIELDS),
        },
    }


def embedded_selection_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request") if isinstance(inputs.get("request"), dict) else {}
    shareable = inputs.get("shareable_policy") if isinstance(inputs.get("shareable_policy"), dict) else {}
    history_ids = {
        item["snapshot_id"]
        for item in inputs.get("history", [])
        if isinstance(item, dict) and isinstance(item.get("snapshot_id"), str) and item["snapshot_id"]
    }
    return validate_selection_policy(
        inputs["flow_graph"],
        {
            "policy_version": shareable.get("policy_version"),
            "flow_graph_id": inputs["flow_graph"].get("flow_graph_id"),
            "history_snapshot_ids": request.get("history_snapshot_ids"),
            "project_lead": {
                "scope_id": request.get("project_lead_scope_id"),
                "node_ids": request.get("project_lead_node_ids"),
                "edge_ids": request.get("project_lead_edge_ids"),
            },
            "shareable": {
                "visible_node_ids": shareable.get("visible_node_ids"),
                "visible_edge_ids": shareable.get("visible_edge_ids"),
            },
        },
        history_ids,
    )


def build_request(
    inputs: dict[str, Any],
    resource: dict[str, Any],
    args: argparse.Namespace,
    profile: str,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = inputs["program_status"]
    forecast = status.get("progress", {}).get("overall", {}).get("series", {}).get("forecast_points", [])
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    selection = selection or embedded_selection_policy(inputs)
    config_hash = panel_model.canonical_hash(resource["options"])
    return {
        "generated_at": generated_at,
        "locale": args.locale,
        "distribution_profile": profile,
        "history_snapshot_ids": list(selection["history_snapshot_ids"]),
        "future_horizon_dates": [item["horizon_date"] for item in forecast if item.get("horizon_date")],
        "project_lead_scope_id": selection["project_lead_scope_id"],
        "project_lead_node_ids": selection["project_lead_node_ids"],
        "project_lead_edge_ids": selection["project_lead_edge_ids"],
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
        selection = embedded_selection_policy(inputs)
    elif args.input_bundle:
        input_path = Path(args.input_bundle).expanduser().resolve()
        with recovery_route(*SELECTION_POLICY_RECOVERY):
            inputs = load_json(input_path)
            inputs["_panel_source_paths"] = {"input-bundle": str(input_path)}
            selection = embedded_selection_policy(inputs)
    else:
        if not args.selection_policy:
            raise PanelError(
                "canonical-memory compose requires --selection-policy",
                SELECTION_POLICY_RECOVERY,
            )
        policy_path = Path(args.selection_policy).expanduser()
        if not policy_path.is_absolute():
            policy_path = Path(args.project_root).expanduser().resolve() / policy_path
        policy_path = policy_path.resolve()
        with recovery_route(*SELECTION_POLICY_RECOVERY):
            policy = load_json(policy_path)
        inputs, selection = load_memory_inputs(memory_root, policy)
        inputs["_panel_source_paths"]["panel-selection-policy"] = str(policy_path)
    inputs = copy.deepcopy(inputs)
    inputs["shareable_policy"] = selection["shareable_policy"]
    inputs["request"] = build_request(inputs, resource, args, profile, selection)
    return inputs


def run_input_audit_gate(
    inputs: dict[str, Any], args: argparse.Namespace, memory_root: Path
) -> tuple[dict[str, Any], Path]:
    module = load_panel_audit_module()
    status_as_of = inputs.get("program_status", {}).get("as_of")
    try:
        audit_date = datetime.fromisoformat(str(status_as_of)).date()
    except ValueError as exc:
        raise PanelError(
            "program-status as_of is invalid before panel audit",
            PROGRAM_STATUS_RECOVERY,
        ) from exc
    audit = module.audit_panel_inputs(
        inputs,
        as_of=audit_date,
        max_age_days=getattr(args, "max_age_days", 7),
    )
    audit_path = module.write_audit_record(audit, memory_root / "audits/management-panel")
    if audit["execution_disposition"] == "blocked":
        codes = [item["code"] for item in audit["blocking_gaps"]]
        raise PanelError(
            "panel pre-render audit blocked: " + ", ".join(codes),
            audit["recommended_workflows"],
        )
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


def panel_logical_identity(model: dict[str, Any]) -> tuple[Any, ...]:
    manifest = model.get("manifest") if isinstance(model.get("manifest"), dict) else {}
    return (
        model.get("panel_id"),
        model.get("panel_model_id"),
        manifest.get("panel_id"),
        manifest.get("panel_model_id"),
        manifest.get("layout_id"),
    )


def resolve_existing_panel_bundle(
    artifact_root: Path, expected_model: dict[str, Any]
) -> tuple[Path, Path | None, dict[str, Any] | None, bytes | None]:
    panel_id = expected_model["panel_id"]
    safe_path, legacy_path = panel_model.panel_bundle_paths(artifact_root, panel_id)
    existing_paths = [path for path in (safe_path, legacy_path) if path is not None and path.exists()]
    if not existing_paths:
        return safe_path, None, None, None

    payloads = {path: path.read_bytes() for path in existing_paths}
    if len(set(payloads.values())) != 1:
        raise PanelError(f"immutable panel bundle collision between safe and legacy basenames: {panel_id}")

    expected_identity = panel_logical_identity(expected_model)
    models: dict[Path, dict[str, Any]] = {}
    for path, payload in payloads.items():
        existing = load_json(path)
        if payload != canonical_json_bytes(existing):
            raise PanelError(f"immutable panel bundle bytes are not canonical: {path}")
        if panel_logical_identity(existing) != expected_identity:
            raise PanelError(f"immutable panel bundle identity collision: {path}")
        models[path] = existing

    selected_path = safe_path if safe_path in models else existing_paths[0]
    return safe_path, selected_path, models[selected_path], payloads[selected_path]


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
        raise PanelError("composed panel violates the panel schema: " + "; ".join([*model_errors, *manifest_errors]))
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
    artifact_root = memory_root / "snapshots/management-panel"
    _, bundle_path, existing_model, bundle = resolve_existing_panel_bundle(artifact_root, model)
    if bundle_path is None or existing_model != model or bundle is None:
        raise PanelError("current HTML does not match its immutable panel bundle")
    if manifest.get("layout", {}).get("engine_sha256") != resource["engine_sha256"]:
        raise PanelError("embedded ELK metadata differs from fixed resource")
    audit_module = load_panel_audit_module()
    artifact_audit = audit_module.audit_panel_artifacts(
        model,
        bundle,
        current.read_bytes(),
        publication_targets={"bundle": bundle_path},
        allow_legacy_bundle=True,
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
    artifact_root = memory_root / "snapshots/management-panel"
    bundle_path, _, existing, existing_bundle = resolve_existing_panel_bundle(artifact_root, model)
    if existing is not None and existing_bundle is not None:
        model = existing
        bundle = existing_bundle
        inputs["request"]["generated_at"] = existing["manifest"]["generated_at"]
        _, elk_js = verify_layout_resource()
        rendered = render_html(existing, elk_js, args.default_view)
    publication_targets = {"bundle": bundle_path}
    if args.operation == "archive":
        publication_targets["html"] = panel_model.panel_artifact_path(artifact_root, panel_id, ".html")
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
        archive = panel_model.panel_artifact_path(artifact_root, panel_id, ".html")
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
        recommended_workflows = (
            getattr(exc, "recommended_workflows", None) or GENERIC_RECOVERY_WORKFLOWS
        )
        result = {
            "ok": False,
            "status": "blocked",
            "operation": args.operation,
            "reason": str(exc),
            "recommended_workflows": list(recommended_workflows),
        }
    emit(result, args.output)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
