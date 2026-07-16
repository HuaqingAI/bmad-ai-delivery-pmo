#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Production panel-model composer for canonical ADP projections.

It selects, orders, redacts, and binds canonical values; it never derives
business status, progress, topology, overlay counts, or branch state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PANEL_SCHEMA_VERSION = "1.0.0"
PANEL_GENERATOR_VERSION = "adp-management-panel/1.0.3"
PANEL_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SKILLS = SKILL_ROOT.parent
FIXTURE_ROOT = SKILL_ROOT / "assets/fixtures/panel-contract-v1"
SOURCE_FIXTURE_PATH = FIXTURE_ROOT / "panel-source-fixture.json"
RECOVERY_FIXTURE_PATH = FIXTURE_ROOT / "recovery-cases.json"
MALICIOUS_FIXTURE_PATH = FIXTURE_ROOT / "malicious-safe-embedding.json"
IDENTITY_GOLDEN_PATH = FIXTURE_ROOT / "identity-golden.json"
PANEL_SCHEMA_PATH = SKILL_ROOT / "assets/adp-management-panel-v1.schema.json"
MANIFEST_SCHEMA_PATH = SKILL_ROOT / "assets/adp-management-panel-manifest-v1.schema.json"
CATALOG_PATH = SKILL_ROOT / "assets/panel-locale-catalog-v1.json"
PROGRESS_GOLDEN_PATH = PROJECT_SKILLS / "adp-program-status/assets/fixtures/progress-v3/golden-measurable-boundary.json"
FLOW_GOLDEN_PATH = PROJECT_SKILLS / "adp-flow-graph/assets/fixtures/flow-contract-v1/golden-parallel-aggregation-conditional-rework.json"

VIEW_SECTIONS = {
    "project-lead": [
        "pl-status-strip",
        "pl-progress-summary",
        "pl-progress-trend",
        "pl-workstream-comparison",
        "pl-flow",
        "pl-roadmap-variance",
        "pl-source-lineage",
    ],
    "fde-morning": [
        "fde-meeting-readiness",
        "fde-window-delta",
        "fde-blockers-commitments",
        "fde-flow-window",
        "fde-source-lineage",
    ],
    "business-biweekly": [
        "biz-meeting-readiness",
        "biz-status-drivers",
        "biz-next-period-progress",
        "biz-decisions",
        "biz-flow-spine",
        "biz-roadmap-readiness",
        "biz-source-lineage",
    ],
}
VISUALIZATION_MODES = ["quantitative-progress", "flow-progress"]
RECOVERY_ORDER = [
    "adp-state-audit",
    "adp-program-status",
    "adp-roadmap-sync",
    "adp-flow-graph",
    "adp-meeting-pack",
    "adp-management-panel",
]
SAFE_EMBEDDING = {
    "strategy": "json-script-text",
    "escaped_codepoints": ["U+003C", "U+003E", "U+0026", "U+2028", "U+2029"],
    "dom_text_only": True,
    "svg_allowlist": ["svg", "g", "path", "line", "rect", "circle", "polygon", "text", "tspan", "title", "desc", "defs", "marker"],
    "forbidden": ["innerHTML", "foreignObject", "event-attributes", "external-href", "source-css"],
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def panel_artifact_basename(panel_id: str) -> str:
    if not isinstance(panel_id, str) or not PANEL_ID_RE.fullmatch(panel_id):
        raise ValueError("panel_id must match sha256:<64 lowercase hex>")
    return "sha256-" + panel_id.removeprefix("sha256:")


def panel_artifact_path(root: Path, panel_id: str, suffix: str) -> Path:
    if suffix not in {".json", ".html"}:
        raise ValueError("panel artifact suffix must be .json or .html")
    return root / f"{panel_artifact_basename(panel_id)}{suffix}"


def panel_bundle_paths(root: Path, panel_id: str) -> tuple[Path, Path | None]:
    safe_path = panel_artifact_path(root, panel_id, ".json")
    legacy_path = None if os.name == "nt" else root / f"{panel_id}.json"
    return safe_path, legacy_path


def existing_panel_bundle_path(root: Path, panel_id: str) -> Path:
    safe_path, legacy_path = panel_bundle_paths(root, panel_id)
    if safe_path.exists() or legacy_path is None or not legacy_path.exists():
        return safe_path
    return legacy_path


def safe_json_for_script(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _decode_pointer_part(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current = document
    for raw in pointer[1:].split("/"):
        part = _decode_pointer_part(raw)
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def set_pointer(document: Any, pointer: str, value: Any, *, delete: bool = False) -> None:
    parts = [_decode_pointer_part(item) for item in pointer[1:].split("/")]
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    final = parts[-1]
    if delete:
        if isinstance(current, list):
            del current[int(final)]
        else:
            del current[final]
    elif isinstance(current, list):
        current[int(final)] = value
    else:
        current[final] = value


def apply_mutation(document: dict[str, Any], mutation: dict[str, Any]) -> None:
    set_pointer(
        document,
        mutation["path"],
        copy.deepcopy(mutation.get("value")),
        delete=mutation["operation"] == "delete",
    )


def _flow_selection(
    graph: dict[str, Any],
    selection_id: str,
    node_ids: list[str],
    edge_ids: list[str],
    scope_id: str | None,
    scenario: str,
) -> dict[str, Any]:
    node_set = set(node_ids)
    edge_set = set(edge_ids)
    nodes = [copy.deepcopy(item) for item in graph["topology"]["nodes"] if item["node_id"] in node_set]
    edges = [
        copy.deepcopy(item)
        for item in graph["topology"]["edges"]
        if item["edge_id"] in edge_set and item["predecessor"] in node_set and item["target"] in node_set
    ]
    kept_edges = {item["edge_id"] for item in edges}
    states = [copy.deepcopy(item) for item in graph["state"]["nodes"] if item["node_id"] in node_set]
    relationships = [copy.deepcopy(item) for item in graph["state"]["relationships"] if item["edge_id"] in kept_edges]
    scope = next((item for item in graph["overlays"]["scopes"] if item["scope_id"] == scope_id), None)
    allocations = [
        copy.deepcopy(item)
        for item in (scope or {}).get("allocations", [])
        if (item["target_type"] == "node" and item["target_id"] in node_set)
        or (item["target_type"] == "edge" and item["target_id"] in kept_edges)
    ]
    return {
        "available": True,
        "selection_status": "selected",
        "selection_id": selection_id,
        "flow_graph_id": graph["flow_graph_id"],
        "topology_id": graph["topology"]["topology_id"],
        "state_snapshot_id": graph["state"]["state_snapshot_id"],
        "overlay_snapshot_id": graph["overlays"]["overlay_snapshot_id"],
        "scenario": scenario,
        "scope_id": scope_id,
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "edges": sorted(edges, key=lambda item: item["edge_id"]),
        "node_states": sorted(states, key=lambda item: item["node_id"]),
        "relationship_states": sorted(relationships, key=lambda item: item["edge_id"]),
        "allocations": sorted(allocations, key=lambda item: (item["target_type"], item["target_id"])),
        "unmapped": copy.deepcopy(graph["overlays"].get("unmapped", [])),
    }


def _recompute_flow_identities(graph: dict[str, Any]) -> None:
    topology = copy.deepcopy(graph["topology"])
    topology.pop("topology_id", None)
    topology["nodes"] = sorted(topology["nodes"], key=lambda item: item["node_id"])
    topology["edges"] = sorted(topology["edges"], key=lambda item: item["edge_id"])
    topology["lineage"] = sorted(
        topology["lineage"],
        key=lambda item: (item["artifact_id"], item["artifact_path"], item["field"], item["source_fingerprint"]),
    )
    topology_id = canonical_hash({"flow_graph_schema_version": graph["flow_graph_schema_version"], "topology": topology})

    state = copy.deepcopy(graph["state"])
    state.pop("state_snapshot_id", None)
    state.pop("topology_id", None)
    state["nodes"] = sorted(state["nodes"], key=lambda item: item["node_id"])
    state["relationships"] = sorted(state["relationships"], key=lambda item: item["edge_id"])
    state["lineage"] = sorted(
        state["lineage"],
        key=lambda item: (item["artifact_id"], item["artifact_path"], item["field"], item["source_fingerprint"]),
    )
    state_id = canonical_hash({"topology_id": topology_id, "state": state})

    overlays = copy.deepcopy(graph["overlays"])
    overlays.pop("overlay_snapshot_id", None)
    overlays.pop("topology_id", None)
    overlays["scopes"] = sorted(overlays["scopes"], key=lambda item: item["scope_id"])
    for scope in overlays["scopes"]:
        scope["allocations"] = sorted(scope["allocations"], key=lambda item: (item["target_type"], item["target_id"]))
        for allocation in scope["allocations"]:
            for category in ("pending", "processed", "risk", "blocked"):
                allocation["counts"][category]["source_refs"] = sorted(
                    allocation["counts"][category]["source_refs"],
                    key=lambda item: (item["source_kind"], item["source_id"], item["source_fingerprint"]),
                )
    overlays["unmapped"] = sorted(overlays["unmapped"], key=lambda item: (item["source_kind"], item["source_id"]))
    overlays["lineage"] = sorted(
        overlays["lineage"],
        key=lambda item: (item["artifact_id"], item["artifact_path"], item["field"], item["source_fingerprint"]),
    )
    overlay_id = canonical_hash({"topology_id": topology_id, "overlays": overlays})

    graph["topology"]["topology_id"] = topology_id
    graph["state"]["topology_id"] = topology_id
    graph["state"]["state_snapshot_id"] = state_id
    graph["overlays"]["topology_id"] = topology_id
    graph["overlays"]["overlay_snapshot_id"] = overlay_id
    graph["flow_graph_id"] = canonical_hash(
        {
            "flow_graph_schema_version": graph["flow_graph_schema_version"],
            "topology_id": topology_id,
            "state_snapshot_id": state_id,
            "overlay_snapshot_id": overlay_id,
        }
    )


def load_source_fixture() -> dict[str, Any]:
    inputs = load_json(SOURCE_FIXTURE_PATH)
    progress = load_json(PROGRESS_GOLDEN_PATH)
    graph = load_json(FLOW_GOLDEN_PATH)
    graph["topology"]["baseline_revision"] = inputs["program_status"]["baseline_revision"]
    for node in graph["topology"]["nodes"]:
        node["baseline_revision"] = inputs["program_status"]["baseline_revision"]
    for edge in graph["topology"]["edges"]:
        edge["baseline_revision"] = inputs["program_status"]["baseline_revision"]
    _recompute_flow_identities(graph)
    inputs["program_status"]["progress"] = progress
    inputs["roadmap"]["progress"] = copy.deepcopy(progress)
    inputs["flow_graph"] = graph
    for scenario, pack in inputs["meeting_packs"].items():
        pack["program_status"] = {
            "snapshot_id": inputs["program_status"]["snapshot_id"],
            "overall_status": inputs["program_status"]["overall_status"],
            "report_confidence": inputs["program_status"]["report_confidence"],
            "reporting_period": copy.deepcopy(inputs["program_status"]["reporting_period"]),
            "baseline_revision": inputs["program_status"]["baseline_revision"],
            "progress": copy.deepcopy(progress),
        }
        pack["flow_subgraph"] = _flow_selection(
            graph,
            pack["flow_selection_id"],
            pack["selected_node_ids"],
            pack["selected_edge_ids"],
            pack["flow_scope_id"],
            scenario,
        )
    return inputs


def _finding(code: str, disposition: str, source: str, message: str, workflows: list[str]) -> dict[str, Any]:
    return {
        "code": code,
        "disposition": disposition,
        "source_artifact": source,
        "message": message,
        "recovery_workflows": workflows,
    }


def evaluate_recovery(inputs: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    status = inputs.get("program_status")
    status_progress_valid = False
    if not isinstance(status, dict):
        findings.append(_finding("panel.input.program-status.missing", "blocked", "program-status", "Canonical program status is missing.", ["adp-state-audit", "adp-program-status"]))
    else:
        progress = status.get("progress")
        if not isinstance(progress, dict) or progress.get("progress_schema_version") != "3.0.0":
            findings.append(_finding("panel.input.progress.migration-required", "blocked", "program-status", "Canonical progress schema 3.0.0 is required.", ["adp-state-audit", "adp-program-status"]))
        elif not isinstance(progress.get("by_scope"), list):
            findings.append(_finding("panel.input.progress.migration-required", "blocked", "program-status", "Canonical progress by_scope projection is required.", ["adp-state-audit", "adp-program-status"]))
        else:
            status_progress_valid = True

    roadmap = inputs.get("roadmap")
    if isinstance(status, dict) and isinstance(roadmap, dict) and status_progress_valid:
        if (
            roadmap.get("program_status_snapshot_id") != status.get("snapshot_id")
            or roadmap.get("baseline_revision") != status.get("baseline_revision")
            or roadmap.get("progress") != status.get("progress")
        ):
            findings.append(_finding("panel.input.roadmap.identity-mismatch", "blocked", "roadmap", "Roadmap does not copy the selected program-status snapshot and progress.", ["adp-roadmap-sync"]))

    graph = inputs.get("flow_graph")
    if not isinstance(graph, dict) or graph.get("flow_graph_schema_version") != "1.0.0":
        findings.append(_finding("panel.input.flow-graph.missing", "blocked", "flow-graph", "Canonical flow graph v1 is required.", ["adp-flow-graph"]))
    elif isinstance(status, dict) and graph.get("topology", {}).get("baseline_revision") != status.get("baseline_revision"):
        findings.append(_finding("panel.input.flow-graph.identity-mismatch", "blocked", "flow-graph", "Flow graph baseline revision does not match program status.", ["adp-flow-graph"]))

    packs = inputs.get("meeting_packs")
    for scenario in ("fde-morning", "business-biweekly"):
        pack = packs.get(scenario) if isinstance(packs, dict) else None
        if not isinstance(pack, dict):
            findings.append(_finding("panel.input.meeting-pack.missing", "degraded", scenario, f"{scenario} meeting pack is missing.", ["adp-meeting-pack"]))
        elif isinstance(status, dict) and (
            pack.get("scenario") != scenario
            or pack.get("program_status_snapshot_id") != status.get("snapshot_id")
            or pack.get("baseline_revision") != status.get("baseline_revision")
        ):
            findings.append(_finding("panel.input.meeting-pack.identity-mismatch", "blocked", scenario, f"{scenario} meeting pack identity does not match canonical status.", ["adp-meeting-pack"]))
        elif isinstance(graph, dict) and (
            pack.get("flow_subgraph", {}).get("flow_graph_id") != graph.get("flow_graph_id")
            or pack.get("flow_subgraph", {}).get("selection_id") != pack.get("flow_selection_id")
        ):
            findings.append(_finding("panel.input.meeting-pack.flow-mismatch", "blocked", scenario, f"{scenario} flow selection does not match the canonical graph.", ["adp-meeting-pack"]))

    history_ids = {item.get("snapshot_id") for item in inputs.get("history", []) if isinstance(item, dict)}
    requested_history = inputs.get("request", {}).get("history_snapshot_ids", [])
    if any(item not in history_ids for item in requested_history):
        findings.append(_finding("panel.selection.history.missing", "degraded", "program-status-history", "A requested immutable history snapshot is absent.", ["adp-program-status"]))

    forecast_dates: set[str] = set()
    if isinstance(status, dict) and isinstance(status.get("progress"), dict):
        forecast_dates.update(
            item.get("horizon_date")
            for item in status["progress"].get("overall", {}).get("series", {}).get("forecast_points", [])
            if item.get("horizon_date")
        )
    if any(item not in forecast_dates for item in inputs.get("request", {}).get("future_horizon_dates", [])):
        findings.append(_finding("panel.selection.future.missing", "degraded", "program-status", "A requested canonical forecast horizon is absent.", ["adp-program-status"]))

    catalog = load_json(CATALOG_PATH)
    if inputs.get("request", {}).get("locale") not in catalog["supported_locales"]:
        findings.append(_finding("panel.locale.fallback", "degraded", "locale-catalog", "Requested locale is unsupported; use the catalog default.", []))

    request = inputs.get("request", {})
    audit_disposition = request.get("panel_input_audit_disposition")
    if audit_disposition in {"degraded", "blocked"}:
        for code in request.get("panel_input_audit_findings", []):
            findings.append(
                _finding(
                    str(code),
                    audit_disposition,
                    "panel-input-audit",
                    "The panel pre-render audit requires this disposition.",
                    list(request.get("panel_input_audit_workflows", [])),
                )
            )

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in findings:
        identity = (
            item["disposition"],
            item["source_artifact"],
            item["message"],
            tuple(item["recovery_workflows"]),
        )
        current = deduped.get(identity)
        if current is None or item["code"] < current["code"]:
            deduped[identity] = item
    findings = sorted(deduped.values(), key=lambda item: (item["code"], item["source_artifact"]))
    disposition = "blocked" if any(item["disposition"] == "blocked" for item in findings) else "degraded" if findings else "ready"
    requested_workflows = {workflow for item in findings for workflow in item["recovery_workflows"]}
    workflows = [workflow for workflow in RECOVERY_ORDER if workflow in requested_workflows]
    return {"status": disposition, "findings": findings, "workflows": workflows, "lower_level_fallback_used": False}


def flow_empty_state(flow: dict[str, Any]) -> dict[str, Any] | None:
    if flow.get("nodes") or flow.get("edges"):
        return None
    window = flow.get("meeting_window") if isinstance(flow.get("meeting_window"), dict) else {}
    unmapped = flow.get("unmapped") if isinstance(flow.get("unmapped"), list) else []
    recoveries = sorted(
        {
            str(item.get("recovery"))
            for item in unmapped
            if isinstance(item, dict) and item.get("recovery")
        }
        | {str(item) for item in flow.get("recovery", []) if item}
    )
    return {
        "status": "scoped-empty",
        "confirmed": window.get("status") == "confirmed",
        "scope_id": flow.get("scope_id"),
        "selection_id": flow.get("selection_id"),
        "window": {
            key: window.get(key)
            for key in ("start", "end", "status", "confirmation_mode")
            if window.get(key) is not None
        },
        "node_count": 0,
        "edge_count": 0,
        "unmapped_count": len(unmapped),
        "recovery": recoveries,
        "source_details": [
            {
                key: item.get(key)
                for key in ("source_kind", "source_id", "reason", "finding_code")
                if item.get(key) is not None
            }
            for item in unmapped
            if isinstance(item, dict)
        ],
    }


def _scope_contract(
    graph: dict[str, Any],
    selection_id: str,
    owner: str,
    scope_id: str | None,
    node_ids: list[str],
    edge_ids: list[str],
    source_pack_id: str | None = None,
) -> dict[str, Any]:
    result = {
        "selection_id": selection_id,
        "layout_scope_id": canonical_hash(
            {
                "topology_id": graph["topology"]["topology_id"],
                "node_ids": sorted(node_ids),
                "edge_ids": sorted(edge_ids),
            }
        ),
        "parent_flow_graph_id": graph["flow_graph_id"],
        "source_owner": owner,
        "scope_id": scope_id,
        "node_ids": sorted(node_ids),
        "edge_ids": sorted(edge_ids),
    }
    if source_pack_id:
        result["source_pack_id"] = source_pack_id
    return result


def _binding(target: str, source_artifact: str, source: str, operation: str = "copy", stable_sort_key: str | None = None) -> dict[str, Any]:
    result = {
        "target_pointer": target,
        "source_artifact": source_artifact,
        "source_pointer": source,
        "operation": operation,
    }
    if stable_sort_key:
        result["stable_sort_key"] = stable_sort_key
    return result


def _section(section_id: str, binding: dict[str, Any], *, meeting: bool = False, flow: bool = False) -> dict[str, Any]:
    hints: dict[str, Any] = {"density": "meeting" if meeting else "workbench", "default_expanded": True}
    if flow:
        hints.update({"visible_node_budget": 40, "fallback": "stage-list"})
    return {"section_id": section_id, "bindings": [binding], "presentation_hints": hints}


def build_views() -> list[dict[str, Any]]:
    specs = {
        "project-lead": [
            _section("pl-status-strip", _binding("/data/status", "program-status", "", "allowlist")),
            _section("pl-progress-summary", _binding("/data/status/progress/overall", "program-status", "/progress/overall")),
            _section("pl-progress-trend", _binding("/data/status/progress/overall/series", "program-status", "/progress/overall/series")),
            _section("pl-workstream-comparison", _binding("/data/status/progress/by_scope", "program-status", "/progress/by_scope", "stable-sort", "scope_id")),
            _section("pl-flow", _binding("/data/flows/project-lead", "flow-graph", "", "select"), flow=True),
            _section("pl-roadmap-variance", _binding("/data/roadmap", "roadmap", "", "allowlist")),
            _section("pl-source-lineage", _binding("/data/status/source_fingerprints", "program-status", "/source_fingerprints")),
        ],
        "fde-morning": [
            _section("fde-meeting-readiness", _binding("/data/meetings/fde-morning", "fde-meeting-pack", "", "allowlist"), meeting=True),
            _section("fde-window-delta", _binding("/data/meetings/fde-morning/boards/fde_period_delta", "fde-meeting-pack", "/boards/fde_period_delta"), meeting=True),
            _section("fde-blockers-commitments", _binding("/data/meetings/fde-morning/boards", "fde-meeting-pack", "/boards", "allowlist"), meeting=True),
            _section("fde-flow-window", _binding("/data/flows/fde-morning", "fde-meeting-pack", "/flow_subgraph", "select"), meeting=True, flow=True),
            _section("fde-source-lineage", _binding("/data/meetings/fde-morning/source_fingerprints", "fde-meeting-pack", "/source_fingerprints"), meeting=True),
        ],
        "business-biweekly": [
            _section("biz-meeting-readiness", _binding("/data/meetings/business-biweekly", "business-meeting-pack", "", "allowlist"), meeting=True),
            _section("biz-status-drivers", _binding("/data/status", "program-status", "", "allowlist"), meeting=True),
            _section("biz-next-period-progress", _binding("/data/status/progress/overall/forecast_summary", "program-status", "/progress/overall/forecast_summary"), meeting=True),
            _section("biz-decisions", _binding("/data/meetings/business-biweekly/boards/business_decisions", "business-meeting-pack", "/boards/business_decisions"), meeting=True),
            _section("biz-flow-spine", _binding("/data/flows/business-biweekly", "business-meeting-pack", "/flow_subgraph", "select"), meeting=True, flow=True),
            _section("biz-roadmap-readiness", _binding("/data/roadmap", "roadmap", "", "allowlist"), meeting=True),
            _section("biz-source-lineage", _binding("/data/meetings/business-biweekly/source_fingerprints", "business-meeting-pack", "/source_fingerprints"), meeting=True),
        ],
    }
    return [
        {
            "view_id": view_id,
            "default_visualization_mode": "quantitative-progress",
            "visualization_modes": list(VISUALIZATION_MODES),
            "sections": specs[view_id],
        }
        for view_id in VIEW_SECTIONS
    ]


def _selected_status(status: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "snapshot_id",
        "generated_at",
        "as_of",
        "reporting_period",
        "baseline_revision",
        "overall_status",
        "report_confidence",
        "input_audit_id",
        "artifact_audit_id",
        "generator_version",
        "critical_path",
        "source_fingerprints",
        "progress",
    ]
    return {key: copy.deepcopy(status[key]) for key in keys}


def _selected_roadmap(roadmap: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "schema_version",
        "generator_version",
        "generated_at",
        "as_of",
        "baseline_revision",
        "program_status_snapshot_id",
        "input_audit_id",
        "artifact_audit_id",
        "source_fingerprints",
        "progress",
        "milestone_timeline",
        "at_risk_dates",
        "blocked_by_decisions",
        "unmapped_items",
        "excluded_items",
    ]
    return {key: copy.deepcopy(roadmap[key]) for key in keys}


def _selected_meeting(pack: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "schema_version",
        "meeting_pack_id",
        "scenario",
        "date",
        "generated_at",
        "program_status_snapshot_id",
        "baseline_revision",
        "input_audit_id",
        "artifact_audit_id",
        "meeting_window",
        "readiness",
        "lifecycle",
        "flow_selection_id",
        "flow_scope_id",
        "selected_node_ids",
        "selected_edge_ids",
        "official_panel_archive",
        "boards",
        "source_fingerprints",
    ]
    return {key: copy.deepcopy(pack[key]) for key in keys if key in pack}


def _manifest_reporting_period(status: dict[str, Any]) -> dict[str, str]:
    period = status.get("reporting_period")
    if not isinstance(period, dict) or not period.get("start") or not period.get("end"):
        raise ValueError("program-status reporting_period requires start and end")
    return {"start": str(period["start"]), "end": str(period["end"])}


def _remove_sensitive(value: Any, removed: set[str]) -> tuple[Any, int]:
    if isinstance(value, list):
        items = [_remove_sensitive(item, removed) for item in value]
        return [item[0] for item in items], sum(item[1] for item in items)
    if not isinstance(value, dict):
        return value, 0
    output: dict[str, Any] = {}
    count = 0
    for key, item in value.items():
        if key in removed:
            count += 1
            continue
        clean, nested = _remove_sensitive(item, removed)
        output[key] = clean
        count += nested
    return output, count


def _remove_internal_identity_values(value: Any, internal_ids: set[str]) -> tuple[Any, int]:
    if isinstance(value, list):
        output = []
        removed = 0
        for item in value:
            if isinstance(item, str) and item in internal_ids:
                removed += 1
                continue
            clean, nested = _remove_internal_identity_values(item, internal_ids)
            output.append(clean)
            removed += nested
        return output, removed
    if not isinstance(value, dict):
        return value, 0
    output: dict[str, Any] = {}
    removed = 0
    for key, item in value.items():
        if isinstance(item, str) and item in internal_ids:
            removed += 1
            continue
        clean, nested = _remove_internal_identity_values(item, internal_ids)
        output[key] = clean
        removed += nested
    return output, removed


def _redact_shareable(
    data: dict[str, Any], selection: dict[str, Any], inputs: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy = inputs["shareable_policy"]
    visible_nodes = set(policy["visible_node_ids"])
    visible_edges = set(policy["visible_edge_ids"])
    source_nodes = {item["node_id"] for item in inputs["flow_graph"]["topology"]["nodes"]}
    source_edges = {item["edge_id"]: item for item in inputs["flow_graph"]["topology"]["edges"]}
    public_nodes = {node_id: f"node-{index:03d}" for index, node_id in enumerate(sorted(visible_nodes), start=1)}
    kept_edge_ids = {
        edge_id
        for edge_id, edge in source_edges.items()
        if edge_id in visible_edges and edge["predecessor"] in visible_nodes and edge["target"] in visible_nodes
    }
    public_edges = {edge_id: f"edge-{index:03d}" for index, edge_id in enumerate(sorted(kept_edge_ids), start=1)}

    redacted = copy.deepcopy(data)
    redacted_selection = copy.deepcopy(selection)
    for view_id, flow in redacted["flows"].items():
        flow.pop("empty_state", None)
        flow["nodes"] = [item for item in flow.get("nodes", []) if item.get("node_id") in visible_nodes]
        flow["edges"] = [
            item
            for item in flow.get("edges", [])
            if item.get("edge_id") in kept_edge_ids
            and item.get("predecessor") in visible_nodes
            and item.get("target") in visible_nodes
        ]
        flow["node_states"] = [item for item in flow.get("node_states", []) if item.get("node_id") in visible_nodes]
        flow["relationship_states"] = [item for item in flow.get("relationship_states", []) if item.get("edge_id") in kept_edge_ids]
        flow["allocations"] = []
        flow["unmapped"] = []
        for node in flow["nodes"]:
            node["node_id"] = public_nodes[node["node_id"]]
        for edge in flow["edges"]:
            edge["edge_id"] = public_edges[edge["edge_id"]]
            edge["predecessor"] = public_nodes[edge["predecessor"]]
            edge["target"] = public_nodes[edge["target"]]
        for state in flow["node_states"]:
            state["node_id"] = public_nodes[state["node_id"]]
        for state in flow["relationship_states"]:
            state["edge_id"] = public_edges[state["edge_id"]]

        scope = redacted_selection["flow_scopes"][view_id]
        scope["node_ids"] = sorted(public_nodes[item] for item in scope["node_ids"] if item in public_nodes)
        scope["edge_ids"] = sorted(public_edges[item] for item in scope["edge_ids"] if item in public_edges)

    removed = set(policy["removed_fields"])
    redacted, removed_occurrences = _remove_sensitive(redacted, removed)
    redacted, removed_identities = _remove_internal_identity_values(
        redacted, source_nodes | set(source_edges)
    )
    manifest = {
        "policy_version": policy["policy_version"],
        "profile": "shareable-summary",
        "removed_fields": sorted(removed),
        "hidden_nodes": len(source_nodes - visible_nodes),
        "hidden_edges": len(set(source_edges) - kept_edge_ids),
        "hidden_sources": removed_occurrences + removed_identities,
        "hidden_counts": sum(len(flow.get("allocations", [])) for flow in data["flows"].values()),
        "topology_reconnected": False,
    }
    return redacted, redacted_selection, manifest


def compose_panel(inputs: dict[str, Any]) -> dict[str, Any]:
    recovery = evaluate_recovery(inputs)
    if recovery["status"] == "blocked":
        raise ValueError("panel inputs are blocked: " + ", ".join(item["code"] for item in recovery["findings"]))

    status = inputs["program_status"]
    roadmap = inputs["roadmap"]
    graph = inputs["flow_graph"]
    packs = inputs["meeting_packs"]
    request = inputs["request"]
    project_scope_id = request["project_lead_scope_id"]
    scope_matches = [
        item for item in graph["overlays"]["scopes"] if item["scope_id"] == project_scope_id
    ]
    if len(scope_matches) != 1:
        raise ValueError(
            f"project-lead scope must match exactly one canonical flow scope: {project_scope_id}"
        )

    project_selection_id = canonical_hash(
        {
            "flow_graph_id": graph["flow_graph_id"],
            "scenario": "project-lead",
            "scope_id": project_scope_id,
            "node_ids": sorted(request["project_lead_node_ids"]),
            "edge_ids": sorted(request["project_lead_edge_ids"]),
        }
    )
    flows = {
        "project-lead": _flow_selection(
            graph,
            project_selection_id,
            request["project_lead_node_ids"],
            request["project_lead_edge_ids"],
            project_scope_id,
            "project-lead",
        ),
        "fde-morning": copy.deepcopy(packs["fde-morning"]["flow_subgraph"]),
        "business-biweekly": copy.deepcopy(packs["business-biweekly"]["flow_subgraph"]),
    }
    for flow in flows.values():
        empty_state = flow_empty_state(flow)
        if empty_state is not None:
            flow["empty_state"] = empty_state
    selection = {
        "history_snapshot_ids": list(request["history_snapshot_ids"]),
        "future_horizon_dates": list(request["future_horizon_dates"]),
        "flow_scopes": {
            "project-lead": _scope_contract(
                graph,
                project_selection_id,
                "adp-flow-graph",
                flows["project-lead"]["scope_id"],
                request["project_lead_node_ids"],
                request["project_lead_edge_ids"],
            ),
            "fde-morning": _scope_contract(
                graph,
                packs["fde-morning"]["flow_selection_id"],
                "adp-meeting-pack",
                packs["fde-morning"]["flow_scope_id"],
                packs["fde-morning"]["selected_node_ids"],
                packs["fde-morning"]["selected_edge_ids"],
                packs["fde-morning"]["meeting_pack_id"],
            ),
            "business-biweekly": _scope_contract(
                graph,
                packs["business-biweekly"]["flow_selection_id"],
                "adp-meeting-pack",
                packs["business-biweekly"]["flow_scope_id"],
                packs["business-biweekly"]["selected_node_ids"],
                packs["business-biweekly"]["selected_edge_ids"],
                packs["business-biweekly"]["meeting_pack_id"],
            ),
        },
    }
    selected_history = [
        copy.deepcopy(item)
        for snapshot_id in request["history_snapshot_ids"]
        for item in inputs["history"]
        if item["snapshot_id"] == snapshot_id
    ]
    data = {
        "status": _selected_status(status),
        "roadmap": _selected_roadmap(roadmap),
        "flows": flows,
        "meetings": {
            "fde-morning": _selected_meeting(packs["fde-morning"]),
            "business-biweekly": _selected_meeting(packs["business-biweekly"]),
        },
        "history": selected_history,
    }

    profile = request["distribution_profile"]
    if profile == "shareable-summary":
        data, selection, redaction = _redact_shareable(data, selection, inputs)
    else:
        redaction = {
            "policy_version": "1.0.0",
            "profile": "internal-full",
            "removed_fields": [],
            "hidden_nodes": 0,
            "hidden_edges": 0,
            "hidden_sources": 0,
            "hidden_counts": 0,
            "topology_reconnected": False,
        }

    catalog_source = load_json(CATALOG_PATH)
    requested_locale = request["locale"]
    resolved_locale = requested_locale if requested_locale in catalog_source["supported_locales"] else catalog_source["default_locale"]
    catalog = {
        "catalog_schema_version": catalog_source["catalog_schema_version"],
        "locale": resolved_locale,
        "messages": copy.deepcopy(catalog_source["messages"][resolved_locale]),
    }
    views = build_views()
    layout_input = {
        "layout_contract_version": request["layout"]["layout_contract_version"],
        "topology_id": graph["topology"]["topology_id"],
        "layout_scope_ids": {view: scope["layout_scope_id"] for view, scope in selection["flow_scopes"].items()},
        "locale": resolved_locale,
        "distribution_profile": profile,
        "layout": request["layout"],
    }
    layout_id = canonical_hash(layout_input)
    model_identity_input = {
        "panel_schema_version": PANEL_SCHEMA_VERSION,
        "source_identities": {
            "program_status_snapshot_id": status["snapshot_id"],
            "roadmap_fingerprint": canonical_hash(roadmap),
            "flow_graph_id": graph["flow_graph_id"],
            "topology_id": graph["topology"]["topology_id"],
            "state_snapshot_id": graph["state"]["state_snapshot_id"],
            "overlay_snapshot_id": graph["overlays"]["overlay_snapshot_id"],
            "meeting_pack_ids": {scenario: pack["meeting_pack_id"] for scenario, pack in packs.items()},
        },
        "selection": selection,
        "catalog": catalog,
        "data": data,
        "views": views,
        "redaction": redaction,
        "recovery": recovery,
    }
    panel_model_id = canonical_hash(model_identity_input)

    all_fingerprints: dict[str, str] = {}
    for source in (status, roadmap, packs["fde-morning"], packs["business-biweekly"]):
        all_fingerprints.update(source.get("source_fingerprints", {}))
    for item in selected_history:
        all_fingerprints[f"history/{item['snapshot_id']}"] = item["source_fingerprint"]
    if profile == "shareable-summary":
        all_fingerprints = {f"source-{index:03d}": value for index, value in enumerate(sorted(all_fingerprints.values()), start=1)}
    input_audit_ids = {
        status["input_audit_id"],
        packs["fde-morning"]["input_audit_id"],
        packs["business-biweekly"]["input_audit_id"],
    }
    if request.get("panel_input_audit_id"):
        input_audit_ids.add(request["panel_input_audit_id"])
    input_audit_ids = sorted(input_audit_ids)
    artifact_audit_ids = sorted({status["artifact_audit_id"], roadmap["artifact_audit_id"], packs["fde-morning"]["artifact_audit_id"], packs["business-biweekly"]["artifact_audit_id"]})
    panel_id = canonical_hash(
        {
            "panel_schema_version": PANEL_SCHEMA_VERSION,
            "panel_model_id": panel_model_id,
            "layout_id": layout_id,
            "generator_version": PANEL_GENERATOR_VERSION,
            "source_fingerprints": all_fingerprints,
            "input_audit_ids": input_audit_ids,
            "artifact_audit_ids": artifact_audit_ids,
        }
    )
    manifest = {
        "panel_schema_version": PANEL_SCHEMA_VERSION,
        "panel_model_id": panel_model_id,
        "panel_id": panel_id,
        "generated_at": request["generated_at"],
        "as_of": status["as_of"],
        "reporting_period": _manifest_reporting_period(status),
        "baseline_revision": status["baseline_revision"],
        "program_status_snapshot_id": status["snapshot_id"],
        "roadmap_fingerprint": canonical_hash(roadmap),
        "topology_id": graph["topology"]["topology_id"],
        "state_snapshot_id": graph["state"]["state_snapshot_id"],
        "overlay_snapshot_id": graph["overlays"]["overlay_snapshot_id"],
        "flow_graph_id": graph["flow_graph_id"],
        "meeting_pack_ids": {scenario: pack["meeting_pack_id"] for scenario, pack in packs.items()},
        "history_snapshot_ids": list(selection["history_snapshot_ids"]),
        "future_horizon_dates": list(selection["future_horizon_dates"]),
        "flow_selection_ids": {view: scope["selection_id"] for view, scope in selection["flow_scopes"].items()},
        "source_fingerprints": all_fingerprints,
        "input_audit_ids": input_audit_ids,
        "artifact_audit_ids": artifact_audit_ids,
        "locale": {
            "requested": requested_locale,
            "resolved": resolved_locale,
            "fallback": requested_locale != resolved_locale,
            "catalog_schema_version": catalog_source["catalog_schema_version"],
        },
        "generator_version": PANEL_GENERATOR_VERSION,
        "layout_id": layout_id,
        "layout": copy.deepcopy(request["layout"]),
        "distribution_profile": profile,
        "redaction": redaction,
        "safe_embedding": copy.deepcopy(SAFE_EMBEDDING),
        "recovery_status": recovery["status"],
    }
    return {
        "panel_schema_version": PANEL_SCHEMA_VERSION,
        "panel_model_id": panel_model_id,
        "panel_id": panel_id,
        "manifest": manifest,
        "selection": selection,
        "catalog": catalog,
        "data": data,
        "views": views,
        "recovery": recovery,
    }


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }[expected]


def _resolve_schema_ref(root: dict[str, Any], reference: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if reference == "adp-management-panel-manifest-v1.schema.json":
        external = load_json(MANIFEST_SCHEMA_PATH)
        return external, external
    if not reference.startswith("#/"):
        raise AssertionError(f"unsupported schema reference: {reference}")
    value: Any = root
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value, root


def validate_schema(instance: Any, schema: dict[str, Any], root: dict[str, Any] | None = None, path: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        resolved, resolved_root = _resolve_schema_ref(root, schema["$ref"])
        return validate_schema(instance, resolved, resolved_root, path)
    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: invalid enum {instance!r}")
    expected = schema.get("type")
    if expected is not None:
        candidates = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(instance, item) for item in candidates):
            return [f"{path}: expected {candidates!r}, got {type(instance).__name__}"]
        if instance is None:
            return errors
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_schema(value, properties[key], root, f"{path}.{key}"))
            elif additional is False:
                errors.append(f"{path}: unexpected {key!r}")
            elif isinstance(additional, dict):
                errors.extend(validate_schema(value, additional, root, f"{path}.{key}"))
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0) or len(instance) > schema.get("maxItems", len(instance)):
            errors.append(f"{path}: item count outside bounds")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: duplicate items")
        if schema.get("items"):
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, schema["items"], root, f"{path}[{index}]"))
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength")
        if schema.get("pattern") and re.fullmatch(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match {schema['pattern']!r}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    return errors


def binding_sources(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "program-status": inputs["program_status"],
        "roadmap": inputs["roadmap"],
        "flow-graph": inputs["flow_graph"],
        "fde-meeting-pack": inputs["meeting_packs"]["fde-morning"],
        "business-meeting-pack": inputs["meeting_packs"]["business-biweekly"],
        "program-status-history": inputs["history"],
    }


def binding_errors(model: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    sources = binding_sources(inputs)
    errors: list[str] = []
    for view in model["views"]:
        for section in view["sections"]:
            for binding in section["bindings"]:
                try:
                    target = resolve_pointer(model, binding["target_pointer"])
                    source = resolve_pointer(sources[binding["source_artifact"]], binding["source_pointer"])
                except (KeyError, IndexError, ValueError) as exc:
                    errors.append(f"{section['section_id']}: unresolved binding: {exc}")
                    continue
                if binding["operation"] == "copy" and target != source:
                    errors.append(f"{section['section_id']}: copy binding changed canonical value")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose and schema-validate an ADP panel-model input bundle.")
    parser.add_argument("input_bundle", help="Canonical panel input bundle including the resolved request.")
    parser.add_argument("--output", help="Write the composed model JSON instead of stdout.")
    args = parser.parse_args(argv)
    try:
        model = compose_panel(load_json(Path(args.input_bundle).expanduser().resolve()))
        errors = validate_schema(model, load_json(PANEL_SCHEMA_PATH))
        if errors:
            raise ValueError("; ".join(errors))
        payload = json.dumps(model, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            Path(args.output).expanduser().resolve().write_text(payload, encoding="utf-8")
        else:
            sys.stdout.write(payload)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
