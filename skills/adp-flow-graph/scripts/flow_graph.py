#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Build and atomically publish the canonical ADP flow graph projection."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


FLOW_GRAPH_SCHEMA_VERSION = "1.0.0"
TOPOLOGY_SCHEMA_VERSION = "1.0.0"
STATE_SCHEMA_VERSION = "1.0.0"
OVERLAY_SCHEMA_VERSION = "1.0.0"
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
ACTIVE_RISK_LIFECYCLES = {"open", "monitoring", "mitigating", "accepted"}
COUNT_CATEGORIES = ("pending", "processed", "risk", "blocked")
STABLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*$")


class ContractError(ValueError):
    """Raised when canonical graph inputs cannot be projected safely."""


class TopologyBlocked(ContractError):
    def __init__(self, findings: list[dict[str, str]]) -> None:
        self.findings = findings
        super().__init__("baseline topology is blocked")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def day_start(value: str) -> str:
    parsed = date.fromisoformat(value)
    return iso_timestamp(datetime.combine(parsed, time.min, tzinfo=timezone.utc))


def day_after(value: str) -> str:
    parsed = date.fromisoformat(value) + timedelta(days=1)
    return iso_timestamp(datetime.combine(parsed, time.min, tzinfo=timezone.utc))


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
    else:
        marker = text.find(BASELINE_MARKER)
        if marker < 0:
            raise ContractError(f"baseline marker is missing: {path}")
        match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker:], re.DOTALL)
        if not match:
            raise ContractError(f"baseline machine JSON is missing: {path}")
        value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def finding(code: str, disposition: str, path: str, message: str, recovery: str) -> dict[str, str]:
    return {
        "code": code,
        "disposition": disposition,
        "path": path,
        "message": message,
        "recovery": recovery,
    }


def source_object(
    raw: Any,
    *,
    artifact_id: str,
    artifact_path: str,
    field: str,
    fingerprint: str,
) -> dict[str, str]:
    if isinstance(raw, dict) and {"artifact_id", "artifact_path", "field", "source_fingerprint"} <= set(raw):
        return {
            "artifact_id": str(raw["artifact_id"]),
            "artifact_path": str(raw["artifact_path"]),
            "field": str(raw["field"]),
            "source_fingerprint": str(raw["source_fingerprint"]),
        }
    reference = str(raw.get("reference") or artifact_path) if isinstance(raw, dict) else artifact_path
    return {
        "artifact_id": artifact_id,
        "artifact_path": reference.split("#", 1)[0] or artifact_path,
        "field": field,
        "source_fingerprint": fingerprint,
    }


def normalize_legacy_dependency(
    baseline_id: str,
    revision: int,
    predecessor: str,
    target: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    seed = f"{baseline_id}\n{revision}\n{predecessor}\n{target}".encode("utf-8")
    return {
        "edge_id": "legacy-" + hashlib.sha256(seed).hexdigest()[:20],
        "predecessor": predecessor,
        "relationship_type": "dependency",
        "source": copy.deepcopy(source),
        "baseline_revision": revision,
    }


def topology_projection(baseline: dict[str, Any], baseline_path: str) -> dict[str, Any]:
    baseline_id = str(baseline.get("baseline_id") or "")
    revision = baseline.get("revision")
    if not STABLE_ID.fullmatch(baseline_id) or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ContractError("baseline_id and positive revision are required")
    baseline_fingerprint = canonical_hash(baseline)
    artifact_id = f"{baseline_id}-R{revision}"
    nodes: list[dict[str, Any]] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, str]] = []

    for collection, node_type in (("gates", "gate"), ("milestones", "milestone")):
        rows = baseline.get(collection, [])
        if not isinstance(rows, list):
            raise ContractError(f"baseline {collection} must be an array")
        for index, raw in enumerate(rows):
            path = f"{collection}[{index}]"
            if not isinstance(raw, dict):
                findings.append(finding("flow.reference.unknown", "blocked", path, "Flow node must be an object.", "Correct the baseline node."))
                continue
            node_id = str(raw.get("id") or "")
            if not STABLE_ID.fullmatch(node_id) or node_id in raw_by_id:
                findings.append(finding("flow.node.duplicate", "blocked", f"{path}.id", f"Duplicate or invalid node ID {node_id!r}.", "Correct duplicate baseline node IDs."))
                continue
            node_revision = raw.get("baseline_revision", revision)
            if node_revision != revision:
                findings.append(finding("flow.reference.cross-revision", "blocked", f"{path}.baseline_revision", f"Node {node_id} is not revision {revision}.", "Regenerate against one approved revision."))
            if node_type == "milestone":
                lane = raw.get("lane") or {"lane_type": "workstream", "lane_id": raw.get("workstream_id")}
            else:
                lane = raw.get("lane") or {"lane_type": "program", "lane_id": "PROGRAM"}
            if not isinstance(lane, dict) or lane.get("lane_type") not in {"program", "workstream"} or not STABLE_ID.fullmatch(str(lane.get("lane_id") or "")):
                findings.append(finding("flow.reference.unknown", "blocked", f"{path}.lane", f"Node {node_id} has an invalid lane.", "Assign an explicit program or workstream lane."))
                continue
            node: dict[str, Any] = {
                "node_id": node_id,
                "name": str(raw.get("name") or node_id),
                "node_type": node_type,
                "lane": {"lane_type": str(lane["lane_type"]), "lane_id": str(lane["lane_id"])},
                "baseline_revision": revision,
                "source": source_object(
                    raw.get("source"),
                    artifact_id=artifact_id,
                    artifact_path=baseline_path,
                    field=f"{collection}.{node_id}",
                    fingerprint=baseline_fingerprint,
                ),
            }
            if raw.get("predecessor_rule") is not None:
                node["predecessor_rule"] = raw["predecessor_rule"]
            nodes.append(node)
            raw_by_id[node_id] = raw

    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    known_nodes = set(raw_by_id)
    for node in nodes:
        raw = raw_by_id[node["node_id"]]
        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list):
            findings.append(finding("flow.reference.unknown", "blocked", f"{node['node_id']}.dependencies", "Dependencies must be an array.", "Correct the baseline dependency list."))
            continue
        for index, supplied in enumerate(dependencies):
            if isinstance(supplied, str):
                dep = normalize_legacy_dependency(baseline_id, revision, supplied, node["node_id"], raw.get("source") or {})
            elif isinstance(supplied, dict):
                dep = copy.deepcopy(supplied)
            else:
                findings.append(finding("flow.reference.unknown", "blocked", f"{node['node_id']}.dependencies[{index}]", "Dependency must be a stable ID or object.", "Correct the baseline dependency."))
                continue
            edge_id = str(dep.get("edge_id") or "")
            predecessor = str(dep.get("predecessor") or "")
            relationship_type = str(dep.get("relationship_type") or "")
            edge_revision = dep.get("baseline_revision", revision)
            if not STABLE_ID.fullmatch(edge_id) or edge_id in edge_ids:
                findings.append(finding("flow.edge.duplicate", "blocked", f"{node['node_id']}.dependencies[{index}].edge_id", f"Duplicate or invalid edge ID {edge_id!r}.", "Assign unique stable edge IDs."))
                continue
            edge_ids.add(edge_id)
            if predecessor not in known_nodes:
                findings.append(finding("flow.reference.unknown", "blocked", f"edges.{edge_id}.predecessor", f"Edge {edge_id} references unknown node {predecessor!r}.", "Correct the baseline reference."))
            if edge_revision != revision:
                findings.append(finding("flow.reference.cross-revision", "blocked", f"edges.{edge_id}.baseline_revision", f"Edge {edge_id} is not revision {revision}.", "Regenerate against one approved revision."))
            if relationship_type not in {"dependency", "aggregation", "conditional", "rework", "informational"}:
                findings.append(finding("flow.reference.unknown", "blocked", f"edges.{edge_id}.relationship_type", f"Edge {edge_id} has an invalid relationship type.", "Use a frozen relationship type."))
            if relationship_type == "conditional" and not isinstance(dep.get("condition"), dict):
                findings.append(finding("flow.condition.missing", "blocked", f"edges.{edge_id}.condition", f"Conditional edge {edge_id} has no canonical condition fact.", "Attach the canonical condition fact."))
            edge: dict[str, Any] = {
                "edge_id": edge_id,
                "predecessor": predecessor,
                "target": node["node_id"],
                "relationship_type": relationship_type,
                "baseline_revision": revision,
                "source": source_object(
                    dep.get("source") or raw.get("source"),
                    artifact_id=artifact_id,
                    artifact_path=baseline_path,
                    field=f"{('gates' if node['node_type'] == 'gate' else 'milestones')}.{node['node_id']}.dependencies.{edge_id}",
                    fingerprint=baseline_fingerprint,
                ),
            }
            if isinstance(dep.get("condition"), dict):
                condition = dep["condition"]
                edge["condition"] = {
                    "fact_id": str(condition.get("fact_id") or ""),
                    "operator": str(condition.get("operator") or ""),
                    "expected_value": condition.get("expected_value"),
                    "source": source_object(
                        condition.get("source"),
                        artifact_id=str(condition.get("fact_id") or "CONDITION"),
                        artifact_path=str((condition.get("source") or {}).get("reference") or baseline_path).split("#", 1)[0],
                        field=f"{condition.get('fact_id')}.value",
                        fingerprint=canonical_hash(condition),
                    ),
                }
            if dep.get("label"):
                edge["label"] = str(dep["label"])
            edges.append(edge)

    node_by_id = {item["node_id"]: item for item in nodes}
    for target in sorted({item["target"] for item in edges if item["relationship_type"] == "aggregation"}):
        incoming = [item for item in edges if item["target"] == target and item["relationship_type"] == "aggregation"]
        if len(incoming) < 2 or node_by_id.get(target, {}).get("predecessor_rule") != "all":
            findings.append(finding("flow.aggregation.rule", "blocked", f"nodes.{target}.predecessor_rule", f"Aggregation target {target} must have at least two aggregation predecessors and predecessor_rule all.", "Put predecessor_rule: all on the aggregation target."))
    if illegal_cycle(known_nodes, edges):
        findings.append(finding("flow.cycle.illegal", "blocked", "topology.edges", "A dependency cycle contains a non-rework edge.", "Remove the cycle or model every loop edge as explicit rework."))
    if findings:
        raise TopologyBlocked(sorted(findings, key=lambda item: (item["code"], item["path"])))

    topology: dict[str, Any] = {
        "topology_schema_version": TOPOLOGY_SCHEMA_VERSION,
        "topology_id": "",
        "baseline_id": baseline_id,
        "baseline_revision": revision,
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "edges": sorted(edges, key=lambda item: item["edge_id"]),
        "lineage": [
            {
                "artifact_id": artifact_id,
                "artifact_path": baseline_path,
                "field": f"revision.{revision}",
                "source_fingerprint": baseline_fingerprint,
            }
        ],
    }
    topology["topology_id"] = canonical_hash(
        {"flow_graph_schema_version": FLOW_GRAPH_SCHEMA_VERSION, "topology": identity_topology(topology)}
    )
    return topology


def illegal_cycle(nodes: set[str], edges: list[dict[str, Any]]) -> bool:
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {node: [] for node in nodes}
    for edge in edges:
        if edge["predecessor"] in nodes and edge["target"] in nodes:
            adjacency[edge["predecessor"]].append((edge["target"], edge))

    def path(current: str, goal: str, visited: set[str]) -> list[dict[str, Any]] | None:
        if current == goal:
            return []
        visited.add(current)
        for target, edge in adjacency.get(current, []):
            if target in visited:
                continue
            suffix = path(target, goal, visited)
            if suffix is not None:
                return [edge, *suffix]
        return None

    for edge in edges:
        suffix = path(edge["target"], edge["predecessor"], set())
        if suffix is not None and any(item["relationship_type"] != "rework" for item in [edge, *suffix]):
            return True
    return False


def identity_topology(topology: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(topology)
    value.pop("topology_id", None)
    value["nodes"] = sorted(value["nodes"], key=lambda item: item["node_id"])
    value["edges"] = sorted(value["edges"], key=lambda item: item["edge_id"])
    value["lineage"] = sort_lineage(value["lineage"])
    return value


def sort_lineage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {canonical_bytes(item): item for item in items}
    return sorted(unique.values(), key=lambda item: (item["artifact_id"], item["artifact_path"], item["field"], item["source_fingerprint"]))


def state_projection(topology: dict[str, Any], program_status: dict[str, Any]) -> dict[str, Any]:
    flow_state = program_status.get("flow_state")
    if not isinstance(flow_state, dict) or flow_state.get("flow_state_schema_version") != STATE_SCHEMA_VERSION:
        raise ContractError("ADP-FLOW-STATE-MIGRATION-REQUIRED")
    if flow_state.get("baseline_id") != topology["baseline_id"] or flow_state.get("baseline_revision") != topology["baseline_revision"]:
        raise ContractError("program-status flow state does not match baseline identity")
    states = flow_state.get("node_states")
    if not isinstance(states, list):
        raise ContractError("program-status node_states must be an array")
    by_id = {str(item.get("node_id")): item for item in states if isinstance(item, dict)}
    expected = {item["node_id"] for item in topology["nodes"]}
    if set(by_id) != expected:
        raise ContractError("program-status flow state must cover every topology node exactly once")
    nodes = [
        {"node_id": node_id, "execution": copy.deepcopy(by_id[node_id]["execution"]), "health": copy.deepcopy(by_id[node_id]["health"])}
        for node_id in sorted(expected)
    ]
    relationships = [relationship_state(edge, by_id) for edge in topology["edges"]]
    lineage = sort_lineage(
        [source for item in nodes for axis in ("execution", "health") for source in item[axis].get("sources", [])]
    )
    state: dict[str, Any] = {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "state_snapshot_id": "",
        "topology_id": topology["topology_id"],
        "as_of": str(flow_state["as_of"]),
        "nodes": nodes,
        "relationships": sorted(relationships, key=lambda item: item["edge_id"]),
        "lineage": lineage,
    }
    state["state_snapshot_id"] = canonical_hash(
        {"topology_id": topology["topology_id"], "state": identity_state(state)}
    )
    return state


def relationship_state(edge: dict[str, Any], node_states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    predecessor = node_states[edge["predecessor"]]
    target = node_states[edge["target"]]
    edge_type = edge["relationship_type"]
    if edge_type == "conditional":
        state_value, state_rule = "pending-confirmation", "REL-CONDITION-PENDING"
        state_sources = [edge["condition"]["source"]]
    elif edge_type == "rework":
        active = target["execution"]["value"] == "in-progress" and predecessor["execution"]["value"] == "complete"
        state_value, state_rule = ("active", "REL-REWORK-ACTIVE") if active else ("inactive", "REL-REWORK-INACTIVE")
        state_sources = [*predecessor["execution"]["sources"], *target["execution"]["sources"]]
    elif predecessor["execution"]["value"] == "complete":
        state_value, state_rule = "satisfied", "REL-PREDECESSOR-COMPLETE"
        state_sources = predecessor["execution"]["sources"]
    elif predecessor["execution"]["value"] == "in-progress" or target["execution"]["value"] == "in-progress":
        state_value, state_rule = "active", "REL-ACTIVE-WORK"
        state_sources = [*predecessor["execution"]["sources"], *target["execution"]["sources"]]
    else:
        state_value, state_rule = "pending", "REL-PENDING"
        state_sources = predecessor["execution"]["sources"]
    health_values = {predecessor["health"]["value"], target["health"]["value"]}
    if "blocked" in health_values:
        health_value, health_rule = "blocked", "REL-HEALTH-BLOCKED"
    elif "at-risk" in health_values:
        health_value, health_rule = "at-risk", "REL-HEALTH-RISK"
    elif "indeterminate" in health_values or state_value == "pending-confirmation":
        health_value, health_rule = "indeterminate", "REL-HEALTH-INDETERMINATE"
    else:
        health_value, health_rule = "on-plan", "REL-HEALTH-PLAN"
    return {
        "edge_id": edge["edge_id"],
        "state": {"value": state_value, "rule_id": state_rule, "sources": sort_lineage(state_sources)},
        "health": {
            "value": health_value,
            "rule_id": health_rule,
            "sources": sort_lineage([*predecessor["health"]["sources"], *target["health"]["sources"]]),
        },
    }


def identity_state(state: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(state)
    value.pop("state_snapshot_id", None)
    value.pop("topology_id", None)
    value["nodes"] = sorted(value["nodes"], key=lambda item: item["node_id"])
    value["relationships"] = sorted(value["relationships"], key=lambda item: item["edge_id"])
    value["lineage"] = sort_lineage(value["lineage"])
    return value


def default_scopes(program_status: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    period = program_status.get("reporting_period") or {}
    start = str(period.get("start") or str(as_of)[:10])
    end = str(period.get("end") or str(as_of)[:10])
    window = {"start_inclusive": day_start(start), "end_exclusive": day_after(end)}
    return [
        {
            "scope_id": f"ACTIVE-{str(as_of)[:10]}",
            "scope_kind": "active-as-of",
            "as_of": as_of,
            "processed_window": window,
            "selection_window": None,
        },
        {
            "scope_id": f"REPORT-{start}-{end}",
            "scope_kind": "reporting-period",
            "as_of": as_of,
            "processed_window": window,
            "selection_window": copy.deepcopy(window),
        },
    ]


def overlay_projection(
    topology: dict[str, Any],
    program_status: dict[str, Any],
    actions_contract: dict[str, Any] | None,
    risks_contract: dict[str, Any] | None,
    scopes: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    actions: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    if isinstance(actions_contract, dict) and actions_contract.get("action_flow_schema_version") == "1.0.0":
        actions = [item for item in actions_contract.get("actions", []) if isinstance(item, dict)]
        lineage.extend(
            {
                "artifact_id": str(item["source"]["artifact_id"]),
                "artifact_path": str(item["source"]["artifact_path"]),
                "field": "actions",
                "source_fingerprint": str(item["source"]["source_fingerprint"]),
            }
            for item in actions
            if isinstance(item.get("source"), dict)
        )
    else:
        findings.append(finding("flow.source.migration-required", "degraded", "actions", "Canonical action-flow source is unavailable.", "Run adp-status-sync to migrate action flow fields."))
    if isinstance(risks_contract, dict) and risks_contract.get("risk_flow_schema_version") == "1.0.0":
        risks = [item for item in risks_contract.get("risks", []) if isinstance(item, dict)]
        lineage.extend(source for item in risks for source in item.get("sources", []) if isinstance(source, dict))
    else:
        findings.append(finding("flow.source.migration-required", "degraded", "risks", "Canonical risk-flow source is unavailable.", "Run adp-risk-dependency-change-review to migrate risk flow fields."))
    if not lineage:
        lineage = [
            {
                "artifact_id": "FLOW-SOURCE-MIGRATION",
                "artifact_path": "views",
                "field": "actions-and-risks",
                "source_fingerprint": canonical_hash({"actions": bool(actions_contract), "risks": bool(risks_contract)}),
            }
        ]

    effective_scopes = scopes or default_scopes(program_status, str(program_status["flow_state"]["as_of"]))
    known_nodes = {item["node_id"] for item in topology["nodes"]}
    known_edges = {item["edge_id"] for item in topology["edges"]}
    unmapped_by_source: dict[tuple[str, str], dict[str, Any]] = {}
    allocations_by_scope: list[dict[str, Any]] = []
    for raw_scope in effective_scopes:
        scope = normalize_scope(raw_scope)
        buckets: dict[tuple[str, str], dict[str, dict[tuple[str, str], dict[str, str]]]] = {}
        as_of_dt = parse_timestamp(scope["as_of"], "scope as_of")
        processed_start = parse_timestamp(scope["processed_window"]["start_inclusive"], "processed window start")
        processed_end = parse_timestamp(scope["processed_window"]["end_exclusive"], "processed window end")
        if processed_end <= processed_start:
            raise ContractError("processed window end must follow start")
        for action in actions:
            mapped = overlay_targets(action, "action", topology, known_nodes, known_edges, unmapped_by_source)
            if not mapped:
                continue
            created = parse_timestamp(str(action["created_at"]), f"action {action['action_id']} created_at")
            pending = action.get("status") in ACTIVE_ACTION_STATUSES and created <= as_of_dt
            done_at = parse_timestamp(str(action["done_at"]), f"action {action['action_id']} done_at") if action.get("done_at") else None
            processed = action.get("status") == "done" and done_at is not None and processed_start <= done_at < processed_end
            ref = {
                "source_kind": "action",
                "source_id": str(action["action_id"]),
                "source_fingerprint": str(action["source"]["source_fingerprint"]),
            }
            for target in mapped:
                bucket = buckets.setdefault(target, empty_bucket())
                if pending:
                    bucket["pending"][("action", str(action["action_id"]))] = ref
                if processed:
                    bucket["processed"][("action", str(action["action_id"]))] = ref
                if pending and action.get("status") == "blocked":
                    bucket["blocked"][("action", str(action["action_id"]))] = ref
        for risk in risks:
            mapped = overlay_targets(risk, "risk", topology, known_nodes, known_edges, unmapped_by_source)
            if not mapped:
                continue
            observed = parse_timestamp(str(risk["observed_at"]), f"risk {risk['risk_id']} observed_at")
            terminal = parse_timestamp(str(risk["terminal_at"]), f"risk {risk['risk_id']} terminal_at") if risk.get("terminal_at") else None
            active = risk.get("lifecycle") in ACTIVE_RISK_LIFECYCLES and observed <= as_of_dt and (terminal is None or terminal > as_of_dt)
            ref = {
                "source_kind": "risk",
                "source_id": str(risk["risk_id"]),
                "source_fingerprint": canonical_hash(risk.get("sources", [])),
            }
            for target in mapped:
                bucket = buckets.setdefault(target, empty_bucket())
                if active:
                    bucket["risk"][("risk", str(risk["risk_id"]))] = ref
                if active and risk.get("relation_state") == "blocked":
                    bucket["blocked"][("risk", str(risk["risk_id"]))] = ref
        allocations = []
        for (target_type, target_id), bucket in sorted(buckets.items()):
            counts: dict[str, Any] = {}
            for category in COUNT_CATEGORIES:
                refs = sorted(bucket[category].values(), key=lambda item: (item["source_kind"], item["source_id"], item["source_fingerprint"]))
                counts[category] = {"count": len(refs), "source_refs": refs}
            allocations.append({"target_type": target_type, "target_id": target_id, "counts": counts})
        allocations_by_scope.append({**scope, "allocations": allocations})

    unmapped = sorted(unmapped_by_source.values(), key=lambda item: (item["source_kind"], item["source_id"]))
    for item in unmapped:
        findings.append(
            finding(
                item["finding_code"],
                "degraded",
                f"overlays.unmapped[{item['source_id']}]",
                f"{item['source_kind'].title()} {item['source_id']} cannot be mapped: {item['reason']}.",
                item["recovery"],
            )
        )
    overlays: dict[str, Any] = {
        "overlay_schema_version": OVERLAY_SCHEMA_VERSION,
        "overlay_snapshot_id": "",
        "topology_id": topology["topology_id"],
        "scopes": sorted(allocations_by_scope, key=lambda item: item["scope_id"]),
        "unmapped": unmapped,
        "lineage": sort_lineage(lineage),
    }
    overlays["overlay_snapshot_id"] = canonical_hash(
        {"topology_id": topology["topology_id"], "overlays": identity_overlays(overlays)}
    )
    return overlays, sorted(findings, key=lambda item: (item["code"], item["path"]))


def normalize_scope(raw: dict[str, Any]) -> dict[str, Any]:
    required = {"scope_id", "scope_kind", "as_of", "processed_window", "selection_window"}
    if not isinstance(raw, dict) or not required <= set(raw):
        raise ContractError("scope is missing required fields")
    if raw["scope_kind"] not in {"active-as-of", "reporting-period", "meeting-window"}:
        raise ContractError("scope_kind is invalid")
    parse_timestamp(str(raw["as_of"]), "scope as_of")
    for name in ("processed_window", "selection_window"):
        window = raw[name]
        if window is None and name == "selection_window":
            continue
        if not isinstance(window, dict) or set(window) != {"start_inclusive", "end_exclusive"}:
            raise ContractError(f"{name} must have start_inclusive and end_exclusive")
        parse_timestamp(str(window["start_inclusive"]), f"{name} start")
        parse_timestamp(str(window["end_exclusive"]), f"{name} end")
    return {
        "scope_id": str(raw["scope_id"]),
        "scope_kind": str(raw["scope_kind"]),
        "as_of": str(raw["as_of"]),
        "processed_window": copy.deepcopy(raw["processed_window"]),
        "selection_window": copy.deepcopy(raw["selection_window"]),
    }


def empty_bucket() -> dict[str, dict[tuple[str, str], dict[str, str]]]:
    return {category: {} for category in COUNT_CATEGORIES}


def overlay_targets(
    source: dict[str, Any],
    source_kind: str,
    topology: dict[str, Any],
    known_nodes: set[str],
    known_edges: set[str],
    unmapped: dict[tuple[str, str], dict[str, Any]],
) -> list[tuple[str, str]]:
    source_id = str(source.get(f"{source_kind}_id") or "")
    node_ids = sorted(set(str(value) for value in source.get("related_plan_item_ids", [])))
    edge_ids = sorted(set(str(value) for value in source.get("related_flow_edge_ids", [])))
    reason: str | None = None
    code = "flow.overlay.unmapped"
    recovery = "Add explicit related plan-item or flow-edge IDs."
    if source.get("baseline_revision") != topology["baseline_revision"]:
        reason, code, recovery = "cross-revision", "flow.reference.cross-revision", "Regenerate the relation against the approved baseline revision."
    elif not node_ids and not edge_ids:
        reason = "missing-related-ids"
    elif any(value not in known_nodes for value in node_ids):
        reason, code, recovery = "unknown-node", "flow.reference.unknown", "Correct the related plan-item IDs."
    elif any(value not in known_edges for value in edge_ids):
        reason, code, recovery = "unknown-edge", "flow.reference.unknown", "Correct the related flow-edge IDs."
    if reason:
        unmapped[(source_kind, source_id)] = {
            "source_kind": source_kind,
            "source_id": source_id,
            "baseline_revision": source.get("baseline_revision") if isinstance(source.get("baseline_revision"), int) else None,
            "related_plan_item_ids": node_ids,
            "related_flow_edge_ids": edge_ids,
            "reason": reason,
            "finding_code": code,
            "recovery": recovery,
        }
        return []
    return [("node", value) for value in node_ids] + [("edge", value) for value in edge_ids]


def identity_overlays(overlays: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(overlays)
    value.pop("overlay_snapshot_id", None)
    value.pop("topology_id", None)
    value["scopes"] = sorted(value["scopes"], key=lambda item: item["scope_id"])
    for scope in value["scopes"]:
        scope["allocations"] = sorted(scope["allocations"], key=lambda item: (item["target_type"], item["target_id"]))
        for allocation in scope["allocations"]:
            for category in COUNT_CATEGORIES:
                allocation["counts"][category]["source_refs"] = sorted(
                    allocation["counts"][category]["source_refs"],
                    key=lambda item: (item["source_kind"], item["source_id"], item["source_fingerprint"]),
                )
    value["unmapped"] = sorted(value["unmapped"], key=lambda item: (item["source_kind"], item["source_id"]))
    value["lineage"] = sort_lineage(value["lineage"])
    return value


def build_flow_graph(
    baseline: dict[str, Any],
    program_status: dict[str, Any],
    actions_contract: dict[str, Any] | None,
    risks_contract: dict[str, Any] | None,
    *,
    baseline_path: str = "plans/program-baseline.md",
    scopes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    topology = topology_projection(baseline, baseline_path)
    state = state_projection(topology, program_status)
    overlays, findings = overlay_projection(topology, program_status, actions_contract, risks_contract, scopes)
    workflows: list[str] = []
    for item in findings:
        if item["path"].startswith("actions") or item["path"].startswith("overlays.unmapped[A"):
            workflows.append("adp-status-sync")
        elif item["path"].startswith("risks") or item["path"].startswith("overlays.unmapped[R"):
            workflows.append("adp-risk-dependency-change-review")
        else:
            workflows.append("adp-flow-graph")
    graph: dict[str, Any] = {
        "flow_graph_schema_version": FLOW_GRAPH_SCHEMA_VERSION,
        "flow_graph_id": "",
        "topology": topology,
        "state": state,
        "overlays": overlays,
        "findings": findings,
        "recovery": {"status": "required" if findings else "none", "workflows": sorted(set(workflows))},
        "compatibility": {
            "baseline": "legacy-string-normalize",
            "program_status": "ADP-FLOW-STATE-MIGRATION-REQUIRED",
            "actions": "ADP-ACTION-FLOW-MIGRATION-REQUIRED",
            "risks": "ADP-RISK-FLOW-MIGRATION-REQUIRED",
        },
        "layout_identity_owner": "adp-management-panel",
    }
    graph["flow_graph_id"] = canonical_hash(
        {
            "flow_graph_schema_version": FLOW_GRAPH_SCHEMA_VERSION,
            "topology_id": topology["topology_id"],
            "state_snapshot_id": state["state_snapshot_id"],
            "overlay_snapshot_id": overlays["overlay_snapshot_id"],
        }
    )
    return graph


def graph_semantic_errors(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if graph.get("layout_id") is not None:
        errors.append("layout identity is forbidden")
    topology = graph.get("topology", {})
    node_ids = {item.get("node_id") for item in topology.get("nodes", [])}
    edge_ids = {item.get("edge_id") for item in topology.get("edges", [])}
    if {item.get("node_id") for item in graph.get("state", {}).get("nodes", [])} != node_ids:
        errors.append("state node coverage mismatch")
    if {item.get("edge_id") for item in graph.get("state", {}).get("relationships", [])} != edge_ids:
        errors.append("relationship state coverage mismatch")
    if graph.get("state", {}).get("topology_id") != topology.get("topology_id") or graph.get("overlays", {}).get("topology_id") != topology.get("topology_id"):
        errors.append("topology identity reference mismatch")
    expected = copy.deepcopy(graph)
    topology_id = canonical_hash({"flow_graph_schema_version": FLOW_GRAPH_SCHEMA_VERSION, "topology": identity_topology(expected["topology"])})
    state_id = canonical_hash({"topology_id": topology_id, "state": identity_state(expected["state"])})
    overlay_id = canonical_hash({"topology_id": topology_id, "overlays": identity_overlays(expected["overlays"])})
    flow_id = canonical_hash({"flow_graph_schema_version": FLOW_GRAPH_SCHEMA_VERSION, "topology_id": topology_id, "state_snapshot_id": state_id, "overlay_snapshot_id": overlay_id})
    actual = (topology.get("topology_id"), graph.get("state", {}).get("state_snapshot_id"), graph.get("overlays", {}).get("overlay_snapshot_id"), graph.get("flow_graph_id"))
    if actual != (topology_id, state_id, overlay_id, flow_id):
        errors.append("graph identity mismatch")
    for scope in graph.get("overlays", {}).get("scopes", []):
        for allocation in scope.get("allocations", []):
            if allocation.get("target_type") == "node" and allocation.get("target_id") not in node_ids:
                errors.append("unknown allocation node")
            if allocation.get("target_type") == "edge" and allocation.get("target_id") not in edge_ids:
                errors.append("unknown allocation edge")
            for category in COUNT_CATEGORIES:
                count = allocation.get("counts", {}).get(category, {})
                refs = {(item.get("source_kind"), item.get("source_id")) for item in count.get("source_refs", [])}
                if count.get("count") != len(refs) or len(refs) != len(count.get("source_refs", [])):
                    errors.append(f"{allocation.get('target_id')} {category} count mismatch")
    return sorted(set(errors))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def publish_graph(memory_root: Path, graph: dict[str, Any]) -> dict[str, str]:
    errors = graph_semantic_errors(graph)
    if errors:
        raise ContractError("cannot publish invalid graph: " + "; ".join(errors))
    digest = graph["flow_graph_id"].split(":", 1)[1]
    immutable = memory_root / "snapshots/flow-graph" / f"fg-{digest}.json"
    current = memory_root / "views/flow-graph.json"
    latest = memory_root / "snapshots/flow-graph/latest.json"
    graph_text = canonical_text(graph)
    if immutable.exists():
        stored = load_document(immutable)
        if canonical_bytes(stored) != canonical_bytes(graph):
            raise ContractError("immutable flow graph identity collision")
    else:
        immutable.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(prefix=f".{immutable.name}.", suffix=".tmp", dir=immutable.parent)
        temp_path = Path(raw_temp)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(graph_text)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_path, immutable)
            except FileExistsError:
                stored = load_document(immutable)
                if canonical_bytes(stored) != canonical_bytes(graph):
                    raise ContractError("immutable flow graph identity collision")
        finally:
            temp_path.unlink(missing_ok=True)
    latest_payload = {
        "flow_graph_schema_version": FLOW_GRAPH_SCHEMA_VERSION,
        "flow_graph_id": graph["flow_graph_id"],
        "snapshot_path": f"snapshots/flow-graph/{immutable.name}",
        "topology_id": graph["topology"]["topology_id"],
        "state_snapshot_id": graph["state"]["state_snapshot_id"],
        "overlay_snapshot_id": graph["overlays"]["overlay_snapshot_id"],
    }
    originals = {path: path.read_bytes() if path.exists() else None for path in (current, latest)}
    try:
        atomic_write(current, graph_text)
        atomic_write(latest, canonical_text(latest_payload))
    except BaseException:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".rollback", dir=path.parent)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(raw_temp, path)
        raise
    return {"current": str(current), "immutable": str(immutable), "latest": str(latest)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--memory-root", default=DEFAULT_MEMORY_ROOT)
    parser.add_argument("--baseline")
    parser.add_argument("--program-status")
    parser.add_argument("--actions")
    parser.add_argument("--risks")
    parser.add_argument("--scopes", help="Optional JSON object with a scopes array.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-o", "--output")
    return parser


def resolve_path(project_root: Path, memory_root: Path, raw: str | None, default: str) -> Path:
    if not raw:
        return memory_root / default
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise ContractError("project_root is not an existing directory")
    memory_root = Path(args.memory_root).expanduser()
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    memory_root = memory_root.resolve()
    baseline_path = resolve_path(project_root, memory_root, args.baseline, "plans/program-baseline.md")
    status_path = resolve_path(project_root, memory_root, args.program_status, "views/program-status.json")
    action_path = resolve_path(project_root, memory_root, args.actions, "views/action-flow.json")
    risk_path = resolve_path(project_root, memory_root, args.risks, "views/risk-flow.json")
    if not baseline_path.is_file() or not status_path.is_file():
        raise ContractError("baseline and program-status inputs are required")
    scopes = None
    if args.scopes:
        scope_payload = load_document(resolve_path(project_root, memory_root, args.scopes, args.scopes))
        scopes = scope_payload.get("scopes")
        if not isinstance(scopes, list):
            raise ContractError("scopes JSON must contain an array named scopes")
    try:
        graph = build_flow_graph(
            load_document(baseline_path),
            load_document(status_path),
            load_document(action_path) if action_path.is_file() else None,
            load_document(risk_path) if risk_path.is_file() else None,
            baseline_path=baseline_path.relative_to(memory_root).as_posix() if baseline_path.is_relative_to(memory_root) else baseline_path.as_posix(),
            scopes=scopes,
        )
    except TopologyBlocked as exc:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "baseline topology is blocked",
            "findings": exc.findings,
            "outputs": {},
            "recommended_workflows": ["adp-plan-baseline"],
        }
    outputs = {} if args.dry_run else publish_graph(memory_root, graph)
    return {
        "ok": True,
        "status": "degraded" if graph["findings"] else "generated",
        "dry_run": bool(args.dry_run),
        "flow_graph_id": graph["flow_graph_id"],
        "topology_id": graph["topology"]["topology_id"],
        "state_snapshot_id": graph["state"]["state_snapshot_id"],
        "overlay_snapshot_id": graph["overlays"]["overlay_snapshot_id"],
        "findings": graph["findings"],
        "unmapped_count": len(graph["overlays"]["unmapped"]),
        "outputs": outputs,
        "graph": graph if args.dry_run else None,
        "recommended_workflows": graph["recovery"]["workflows"],
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        result = {"ok": False, "status": "error", "reason": str(exc), "outputs": {}, "recommended_workflows": ["adp-flow-graph", "adp-state-audit"]}
    text = canonical_text(result)
    if args.output:
        atomic_write(Path(args.output).expanduser().resolve(), text)
    else:
        sys.stdout.write(text)
    return 0 if result.get("ok") else (1 if result.get("status") == "blocked" else 2)


if __name__ == "__main__":
    sys.exit(main())
