from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SKILLS = SKILL_ROOT.parent
FIXTURE_ROOT = SKILL_ROOT / "assets/fixtures/flow-contract-v1"
GOLDEN_PATH = FIXTURE_ROOT / "golden-parallel-aggregation-conditional-rework.json"
FLOW_SCHEMA_PATH = SKILL_ROOT / "assets/adp-flow-graph-v1.schema.json"
SOURCE_FIXTURE_PATH = FIXTURE_ROOT / "source-contract-golden.json"
SOURCE_SCHEMAS = {
    "baseline": PROJECT_SKILLS / "adp-plan-baseline/assets/program-baseline-flow-vnext.schema.json",
    "program_status": PROJECT_SKILLS / "adp-program-status/assets/program-status-flow-state-v1.schema.json",
    "actions": PROJECT_SKILLS / "adp-status-sync/assets/action-flow-relation-v1.schema.json",
    "risks": PROJECT_SKILLS / "adp-risk-dependency-change-review/assets/risk-flow-relation-v1.schema.json",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise AssertionError(f"only local schema refs are supported: {reference}")
    value: Any = root
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


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


def validate_schema(instance: Any, schema: dict[str, Any], root: dict[str, Any] | None = None, path: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        return validate_schema(instance, _resolve_ref(root, schema["$ref"]), root, path)

    errors: list[str] = []
    for branch in schema.get("allOf", []):
        errors.extend(validate_schema(instance, branch, root, path))
    if "oneOf" in schema:
        matches = [not validate_schema(instance, branch, root, path) for branch in schema["oneOf"]]
        if sum(matches) != 1:
            errors.append(f"{path}: expected exactly one oneOf branch")
    if "not" in schema and not validate_schema(instance, schema["not"], root, path):
        errors.append(f"{path}: matched forbidden schema")
    if "if" in schema:
        condition_matches = not validate_schema(instance, schema["if"], root, path)
        selected = schema.get("then") if condition_matches else schema.get("else")
        if selected:
            errors.extend(validate_schema(instance, selected, root, path))
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        candidates = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(instance, item) for item in candidates):
            errors.append(f"{path}: expected type {candidates!r}, got {type(instance).__name__}")
            return errors
        if instance is None:
            return errors

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_schema(value, properties[key], root, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")

    if isinstance(instance, list):
        if len(instance) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: too few items")
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, item_schema, root, f"{path}[{index}]"))
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")

    if isinstance(instance, str):
        if len(instance) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: shorter than minLength")
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    return errors


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sort_lineage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item["artifact_id"], item["artifact_path"], item["field"], item["source_fingerprint"]))


def identity_inputs(graph: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    topology = copy.deepcopy(graph["topology"])
    topology.pop("topology_id", None)
    topology["nodes"] = sorted(topology["nodes"], key=lambda item: item["node_id"])
    topology["edges"] = sorted(topology["edges"], key=lambda item: item["edge_id"])
    topology["lineage"] = _sort_lineage(topology["lineage"])

    state = copy.deepcopy(graph["state"])
    state.pop("state_snapshot_id", None)
    state.pop("topology_id", None)
    state["nodes"] = sorted(state["nodes"], key=lambda item: item["node_id"])
    state["relationships"] = sorted(state["relationships"], key=lambda item: item["edge_id"])
    state["lineage"] = _sort_lineage(state["lineage"])

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
    overlays["lineage"] = _sort_lineage(overlays["lineage"])
    return topology, state, overlays


def compute_identities(graph: dict[str, Any]) -> dict[str, str]:
    topology_input, state_input, overlay_input = identity_inputs(graph)
    topology_id = canonical_hash({"flow_graph_schema_version": graph["flow_graph_schema_version"], "topology": topology_input})
    state_id = canonical_hash({"topology_id": topology_id, "state": state_input})
    overlay_id = canonical_hash({"topology_id": topology_id, "overlays": overlay_input})
    flow_id = canonical_hash(
        {
            "flow_graph_schema_version": graph["flow_graph_schema_version"],
            "topology_id": topology_id,
            "state_snapshot_id": state_id,
            "overlay_snapshot_id": overlay_id,
        }
    )
    return {"topology_id": topology_id, "state_snapshot_id": state_id, "overlay_snapshot_id": overlay_id, "flow_graph_id": flow_id}


def normalize_legacy_dependency(baseline_id: str, revision: int, predecessor: str, target: str, source: dict[str, Any]) -> dict[str, Any]:
    seed = f"{baseline_id}\n{revision}\n{predecessor}\n{target}".encode("utf-8")
    return {
        "edge_id": "legacy-" + hashlib.sha256(seed).hexdigest()[:20],
        "predecessor": predecessor,
        "relationship_type": "dependency",
        "source": copy.deepcopy(source),
        "baseline_revision": revision,
    }


def _node_id(node: Any) -> str:
    return node if isinstance(node, str) else node["node_id"]


def _illegal_cycle(nodes: set[str], edges: list[dict[str, Any]]) -> bool:
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


def finding_for_case(case: dict[str, Any], revision: int = 2) -> str | None:
    node_ids = [_node_id(node) for node in case["nodes"]]
    if len(node_ids) != len(set(node_ids)):
        return "flow.node.duplicate"
    edge_ids = [edge["edge_id"] for edge in case["edges"]]
    if len(edge_ids) != len(set(edge_ids)):
        return "flow.edge.duplicate"
    known = set(node_ids)
    if any(edge["predecessor"] not in known or edge["target"] not in known for edge in case["edges"]):
        return "flow.reference.unknown"
    if any(edge["baseline_revision"] != revision for edge in case["edges"]):
        return "flow.reference.cross-revision"
    if any(edge["relationship_type"] == "conditional" and not edge.get("condition") for edge in case["edges"]):
        return "flow.condition.missing"
    aggregation_targets = {edge["target"] for edge in case["edges"] if edge["relationship_type"] == "aggregation"}
    node_by_id = {(_node_id(node)): node for node in case["nodes"]}
    for target in aggregation_targets:
        incoming = [edge for edge in case["edges"] if edge["target"] == target and edge["relationship_type"] == "aggregation"]
        node = node_by_id[target]
        if len(incoming) < 2 or not isinstance(node, dict) or node.get("predecessor_rule") != "all":
            return "flow.aggregation.rule"
    if _illegal_cycle(known, case["edges"]):
        return "flow.cycle.illegal"
    return None


def graph_semantic_errors(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    topology = graph["topology"]
    nodes = topology["nodes"]
    edges = topology["edges"]
    case = {"nodes": nodes, "edges": edges}
    finding = finding_for_case(case, topology["baseline_revision"])
    if finding:
        errors.append(finding)
    node_ids = {node["node_id"] for node in nodes}
    edge_ids = {edge["edge_id"] for edge in edges}
    if any(node["baseline_revision"] != topology["baseline_revision"] for node in nodes):
        errors.append("node revision mismatch")
    if {item["node_id"] for item in graph["state"]["nodes"]} != node_ids:
        errors.append("state node coverage mismatch")
    if {item["edge_id"] for item in graph["state"]["relationships"]} != edge_ids:
        errors.append("relationship state coverage mismatch")
    if graph["state"]["topology_id"] != topology["topology_id"] or graph["overlays"]["topology_id"] != topology["topology_id"]:
        errors.append("topology identity reference mismatch")
    for scope in graph["overlays"]["scopes"]:
        for allocation in scope["allocations"]:
            if allocation["target_type"] == "node" and allocation["target_id"] not in node_ids:
                errors.append("unknown allocation node")
            if allocation["target_type"] == "edge" and allocation["target_id"] not in edge_ids:
                errors.append("unknown allocation edge")
            for category in ("pending", "processed", "risk", "blocked"):
                count = allocation["counts"][category]
                refs = [(item["source_kind"], item["source_id"]) for item in count["source_refs"]]
                if count["count"] != len(refs) or len(refs) != len(set(refs)):
                    errors.append(f"{allocation['target_id']} {category} count mismatch")
    return errors


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def evaluate_scoped_counts(fixture: dict[str, Any]) -> dict[str, Any]:
    scope = fixture["scope"]
    as_of = _parse_time(scope["as_of"])
    start = _parse_time(scope["processed_window"]["start_inclusive"])
    end = _parse_time(scope["processed_window"]["end_exclusive"])
    known_nodes = set(fixture["known_node_ids"])
    known_edges = set(fixture["known_edge_ids"])
    result = {
        target: {category: [] for category in ("pending", "processed", "risk", "blocked")}
        for target in sorted(known_nodes | known_edges)
    }
    unmapped: list[str] = []

    for action in fixture["actions"]:
        supplied = [(value, "node") for value in action["related_plan_item_ids"]] + [(value, "edge") for value in action["related_flow_edge_ids"]]
        targets = [value for value, kind in supplied if (kind == "node" and value in known_nodes) or (kind == "edge" and value in known_edges)]
        if not supplied or len(targets) != len(supplied) or action["baseline_revision"] != 2:
            unmapped.append(action["action_id"])
            continue
        created = _parse_time(action["created_at"])
        pending = action["status"] in {"open", "in-progress", "blocked"} and created <= as_of
        done_at = _parse_time(action["done_at"]) if action.get("done_at") else None
        processed = action["status"] == "done" and done_at is not None and start <= done_at < end
        for target in targets:
            if pending:
                result[target]["pending"].append(action["action_id"])
            if processed:
                result[target]["processed"].append(action["action_id"])
            if action["status"] == "blocked" and pending:
                result[target]["blocked"].append(action["action_id"])

    for risk in fixture["risks"]:
        supplied = [(value, "node") for value in risk["related_plan_item_ids"]] + [(value, "edge") for value in risk["related_flow_edge_ids"]]
        targets = [value for value, kind in supplied if (kind == "node" and value in known_nodes) or (kind == "edge" and value in known_edges)]
        if not supplied or len(targets) != len(supplied) or risk["baseline_revision"] != 2:
            unmapped.append(risk["risk_id"])
            continue
        observed = _parse_time(risk["observed_at"])
        terminal = _parse_time(risk["terminal_at"]) if risk.get("terminal_at") else None
        active = risk["lifecycle"] in {"open", "monitoring", "mitigating", "accepted"} and observed <= as_of and (terminal is None or terminal > as_of)
        for target in targets:
            if active:
                result[target]["risk"].append(risk["risk_id"])
            if active and risk["relation_state"] == "blocked":
                result[target]["blocked"].append(risk["risk_id"])
    for counts in result.values():
        for values in counts.values():
            values.sort()
    return {**result, "unmapped": sorted(unmapped)}
