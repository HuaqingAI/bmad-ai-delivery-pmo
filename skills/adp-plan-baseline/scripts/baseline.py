#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Plan, validate, version, archive, and inspect the ADP program baseline."""

from __future__ import annotations

import argparse
import copy
import ctypes
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from adp_effective_config import display_label, format_date, message, resolve_effective_config
from scope_contract import discover_wdr_registry, resolve_scope_contract


MARKER = "<!-- adp:program-baseline:v1 -->"
APPROVED_STATES = {"confirmed", "approved"}
ALL_STATES = {"candidate", *APPROVED_STATES}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
WDR_WORKSTREAM_ID_PATTERN = re.compile(r"^-\s*Workstream ID:\s*(\S.*?)\s*$", re.MULTILINE)
HARD_DEPENDENCY_TYPES = {"dependency", "aggregation"}
ARCHIVE_NAME_PATTERN = re.compile(r"^program-baseline-r([1-9][0-9]*)\.md$")
MEMORY_RELATIVE = Path("_bmad-output/adp/memory")
BASELINE_RELATIVE = MEMORY_RELATIVE / "plans/program-baseline.md"
HISTORY_RELATIVE = MEMORY_RELATIVE / "plans/baseline-history"
LOCK_RELATIVE = MEMORY_RELATIVE / "plans/.program-baseline.lock"
LOCK_RECOVERY_RELATIVE = MEMORY_RELATIVE / "plans/lock-recovery"
LOCK_RECOVERY_GUARD_RELATIVE = MEMORY_RELATIVE / "plans/.program-baseline.lock-recovering"
LOCK_SCHEMA_VERSION = "1.0"
WINDOWS_ERROR_ACCESS_DENIED = 5
WINDOWS_ERROR_INVALID_PARAMETER = 87
WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINDOWS_STILL_ACTIVE = 259


class WriteLockUnavailable(RuntimeError):
    """Raised when another baseline writer owns the project lock."""


def now_iso(value: str | None = None) -> str:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def finding(code: str, severity: str, path: str, message_text: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "path": path, "message": message_text}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def parse_baseline(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    marker_index = text.find(MARKER)
    if marker_index < 0:
        raise ValueError(f"baseline marker missing: {MARKER}")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker_index:], re.DOTALL)
    if not match:
        raise ValueError("canonical JSON block missing after baseline marker")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError("canonical baseline JSON must be an object")
    return value


def current_wdr_registry(
    project_root: Path,
    baseline_path: Path | None = None,
    *,
    include_physical: bool = True,
) -> set[str]:
    memory_root = project_root / MEMORY_RELATIVE
    if baseline_path is not None:
        resolved = baseline_path.expanduser().resolve()
        if resolved.parent.name == "plans":
            memory_root = resolved.parent.parent
        elif resolved.parent.name == "baseline-history" and resolved.parent.parent.name == "plans":
            memory_root = resolved.parent.parent.parent
    return {
        item["scope_id"]
        for item in discover_wdr_registry(memory_root, include_physical=include_physical)
    }


def source_ref(source: Any) -> str:
    if not isinstance(source, dict):
        return ""
    return str(source.get("reference", ""))


def _date_error(value: Any) -> str | None:
    if not isinstance(value, str):
        return "must be an ISO YYYY-MM-DD string"
    try:
        if date.fromisoformat(value).isoformat() != value:
            return "must use canonical ISO YYYY-MM-DD format"
    except ValueError:
        return "must be a real ISO YYYY-MM-DD date"
    return None


def _timestamp_error(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty ISO timestamp"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "must be a valid ISO timestamp"
    if parsed.tzinfo is None:
        return "must include a timezone"
    return None


def _validate_source(source: Any, path: str, execute: bool, findings: list[dict[str, str]]) -> None:
    if not isinstance(source, dict):
        findings.append(finding("source.invalid", "blocked", path, "source must be an object"))
        return
    for key in ("type", "reference"):
        if not isinstance(source.get(key), str) or not source[key].strip():
            findings.append(finding("source.missing", "blocked", f"{path}.{key}", f"source {key} is required"))
    if execute and (not isinstance(source.get("confirmed_by"), str) or not source["confirmed_by"].strip()):
        findings.append(finding("source.unconfirmed", "blocked", f"{path}.confirmed_by", "approved facts require confirmed_by"))


def _validate_item(
    item: Any,
    path: str,
    kind: str,
    execute: bool,
    stored: bool,
    virtual_scope_ids: set[str],
    findings: list[dict[str, str]],
) -> str | None:
    if not isinstance(item, dict):
        findings.append(finding("item.invalid", "blocked", path, f"{kind} must be an object"))
        return None
    if "dependencies" not in item:
        findings.append(finding("field.missing", "blocked", f"{path}.dependencies", "required field dependencies is missing"))
    if stored and "baseline_revision" not in item:
        findings.append(finding("field.missing", "blocked", f"{path}.baseline_revision", "required field baseline_revision is missing"))
    item_id = item.get("id")
    if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
        findings.append(finding("id.invalid", "blocked", f"{path}.id", "stable ID is missing or invalid"))
        item_id = None
    for key in ("name", "owner"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            findings.append(finding(f"{key}.missing", "blocked", f"{path}.{key}", f"{key} is required"))
    if kind == "milestone" and (not isinstance(item.get("workstream_id"), str) or not item["workstream_id"].strip()):
        findings.append(finding("workstream.missing", "blocked", f"{path}.workstream_id", "milestone workstream_id is required"))
    error = _date_error(item.get("planned_date"))
    if error:
        findings.append(finding("date.invalid", "blocked", f"{path}.planned_date", error))
    state = item.get("confirmation_status")
    if state not in ALL_STATES:
        findings.append(finding("confirmation.invalid", "blocked", f"{path}.confirmation_status", "confirmation_status must be candidate, confirmed, or approved"))
    elif execute and state not in APPROVED_STATES:
        findings.append(finding("confirmation.required", "blocked", f"{path}.confirmation_status", "executed baseline items must be confirmed or approved"))
    _validate_source(item.get("source"), f"{path}.source", execute, findings)
    dependencies = item.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(value, (str, dict)) for value in dependencies):
        findings.append(finding("dependencies.invalid", "blocked", f"{path}.dependencies", "dependencies must be stable IDs or vNext dependency objects"))
    if item.get("node_type") is not None and item.get("node_type") != kind:
        findings.append(finding("flow.node_type.invalid", "blocked", f"{path}.node_type", f"node_type must be {kind}"))
    lane = item.get("lane")
    if lane is not None:
        if not isinstance(lane, dict) or lane.get("lane_type") not in {"program", "virtual", "workstream"} or not isinstance(lane.get("lane_id"), str) or not ID_PATTERN.fullmatch(lane["lane_id"]):
            findings.append(finding("flow.lane.invalid", "blocked", f"{path}.lane", "lane must identify a stable program, virtual, or workstream lane"))
        elif kind == "milestone":
            is_virtual = item.get("workstream_id") in virtual_scope_ids
            allowed_lane_types = {"program", "virtual"} if is_virtual else {"workstream"}
            if lane.get("lane_type") not in allowed_lane_types:
                findings.append(finding("flow.lane.invalid", "blocked", f"{path}.lane", "milestone lane kind must match the shared scope contract"))
    tolerance = item.get("tolerance_days")
    if tolerance is not None and (not isinstance(tolerance, int) or isinstance(tolerance, bool) or not 0 <= tolerance <= 90):
        findings.append(finding("tolerance.invalid", "blocked", f"{path}.tolerance_days", "tolerance_days must be an integer from 0 through 90"))
    return item_id


def _find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            start = visiting.index(node)
            cycle = visiting[start:] + [node]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if node in visited:
            return
        visiting.append(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency)
        visiting.pop()
        visited.add(node)

    for node in graph:
        visit(node)
    return cycles


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


def normalized_dependencies(model: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_id = str(model.get("baseline_id") or "")
    revision = model.get("revision") if isinstance(model.get("revision"), int) else 1
    result: list[dict[str, Any]] = []
    for collection in ("gates", "milestones"):
        for item in model.get(collection, []) if isinstance(model.get(collection), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            for supplied in item.get("dependencies", []) if isinstance(item.get("dependencies"), list) else []:
                if isinstance(supplied, str):
                    dep = normalize_legacy_dependency(baseline_id, revision, supplied, item["id"], item.get("source") or {})
                elif isinstance(supplied, dict):
                    dep = copy.deepcopy(supplied)
                else:
                    continue
                dep["target"] = item["id"]
                result.append(dep)
    return result


def _illegal_flow_cycle(nodes: set[str], edges: list[dict[str, Any]]) -> bool:
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {node: [] for node in nodes}
    for edge in edges:
        predecessor = str(edge.get("predecessor") or "")
        target = str(edge.get("target") or "")
        if predecessor in nodes and target in nodes:
            adjacency[predecessor].append((target, edge))

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
        suffix = path(str(edge.get("target") or ""), str(edge.get("predecessor") or ""), set())
        if suffix is not None and any(item.get("relationship_type") != "rework" for item in [edge, *suffix]):
            return True
    return False


def validate_model(
    model: Any,
    execute: bool = False,
    stored: bool = True,
    registered_workstreams: set[str] | None = None,
    skip_wdr_registry: bool = False,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not isinstance(model, dict):
        return {"valid": False, "findings": [finding("model.invalid", "blocked", "$", "baseline must be an object")]}

    scope_contract = resolve_scope_contract(model, registered_workstreams or set())
    virtual_scope_ids = {str(item["scope_id"]) for item in scope_contract["virtual_scopes"]}
    for warning in scope_contract["migration_warnings"]:
        findings.append(
            finding(
                str(warning["code"]),
                "warning",
                str(warning["directory"]),
                str(warning["risk"]),
            )
        )

    required = ["schema_version", "baseline_id", "confirmation_status", "project", "default_tolerance_days", "gates", "milestones", "critical_path", "weighting"]
    if stored:
        required.extend(["revision", "created_at", "updated_at"])
    for key in required:
        if key not in model:
            findings.append(finding("field.missing", "blocked", key, f"required field {key} is missing"))
    if model.get("schema_version") != "1.0":
        findings.append(finding("schema.unsupported", "blocked", "schema_version", "schema_version must be 1.0"))
    if model.get("flow_contract_version") not in {None, "1.0.0"}:
        findings.append(finding("flow.schema.unsupported", "blocked", "flow_contract_version", "flow_contract_version must be 1.0.0"))
    baseline_id = model.get("baseline_id")
    if not isinstance(baseline_id, str) or not ID_PATTERN.fullmatch(baseline_id):
        findings.append(finding("baseline_id.invalid", "blocked", "baseline_id", "baseline_id must be a stable ID"))
    state = model.get("confirmation_status")
    if state not in ALL_STATES:
        findings.append(finding("confirmation.invalid", "blocked", "confirmation_status", "confirmation_status must be candidate, confirmed, or approved"))
    elif execute and state not in APPROVED_STATES:
        findings.append(finding("confirmation.required", "blocked", "confirmation_status", "executed baseline must be confirmed or approved"))

    revision = model.get("revision")
    if stored:
        if "revision" in model and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 1):
            findings.append(finding("revision.invalid", "blocked", "revision", "revision must be a positive integer"))
        parsed_timestamps: dict[str, datetime] = {}
        for key in ("created_at", "updated_at"):
            if key in model:
                error = _timestamp_error(model[key])
                if error:
                    findings.append(finding("timestamp.invalid", "blocked", key, error))
                else:
                    parsed_timestamps[key] = datetime.fromisoformat(model[key].replace("Z", "+00:00"))
        if set(parsed_timestamps) == {"created_at", "updated_at"}:
            if parsed_timestamps["updated_at"] < parsed_timestamps["created_at"]:
                findings.append(finding("timestamp.order", "blocked", "updated_at", "updated_at cannot precede created_at"))

    project = model.get("project")
    if not isinstance(project, dict):
        findings.append(finding("project.invalid", "blocked", "project", "project must be an object"))
    else:
        for key in ("name", "owner"):
            if not isinstance(project.get(key), str) or not project[key].strip():
                findings.append(finding(f"project.{key}.missing", "blocked", f"project.{key}", f"project {key} is required"))
        error = _date_error(project.get("target_date"))
        if error:
            findings.append(finding("date.invalid", "blocked", "project.target_date", error))
        _validate_source(project.get("source"), "project.source", execute, findings)

    tolerance = model.get("default_tolerance_days", 0)
    if not isinstance(tolerance, int) or isinstance(tolerance, bool) or not 0 <= tolerance <= 90:
        findings.append(finding("tolerance.invalid", "blocked", "default_tolerance_days", "default_tolerance_days must be an integer from 0 through 90"))

    ids: dict[str, str] = {}
    all_items: list[tuple[str, dict[str, Any]]] = []
    for collection, kind in (("gates", "gate"), ("milestones", "milestone")):
        rows = model.get(collection)
        if not isinstance(rows, list):
            findings.append(finding("collection.invalid", "blocked", collection, f"{collection} must be an array"))
            continue
        for index, row in enumerate(rows):
            path = f"{collection}[{index}]"
            item_id = _validate_item(row, path, kind, execute, stored, virtual_scope_ids, findings)
            if isinstance(row, dict):
                all_items.append((path, row))
                workstream_id = row.get("workstream_id")
                if (
                    kind == "milestone"
                    and registered_workstreams is not None
                    and not skip_wdr_registry
                    and isinstance(workstream_id, str)
                    and workstream_id.strip()
                    and workstream_id not in virtual_scope_ids
                    and workstream_id not in registered_workstreams
                ):
                    findings.append(
                        finding(
                            "workstream.unknown",
                            "blocked",
                            f"{path}.workstream_id",
                            f"milestone workstream_id {workstream_id!r} is not present in the current WDR registry",
                        )
                    )
            if item_id:
                folded = item_id.casefold()
                if folded in ids:
                    findings.append(finding("id.duplicate", "blocked", f"{path}.id", f"ID duplicates {ids[folded]} ignoring case"))
                else:
                    ids[folded] = item_id

    canonical_ids = set(ids.values())
    graph: dict[str, list[str]] = {}
    for path, row in all_items:
        item_id = row.get("id")
        if not isinstance(item_id, str):
            continue
        dependencies = row.get("dependencies", [])
        if not isinstance(dependencies, list):
            continue
        graph[item_id] = []
        for supplied in dependencies:
            dependency = supplied if isinstance(supplied, str) else supplied.get("predecessor")
            if dependency not in canonical_ids:
                findings.append(finding("dependency.unknown", "blocked", f"{path}.dependencies", f"unknown dependency {dependency!r}"))
            else:
                graph[item_id].append(dependency)
    flow_edges = normalized_dependencies(model)
    node_by_id = {str(row.get("id")): row for _, row in all_items if isinstance(row.get("id"), str)}
    hard_edge_pairs: set[tuple[str, str]] = set()
    edge_ids: dict[str, str] = {}
    for index, edge in enumerate(flow_edges):
        edge_path = f"flow_edges[{index}]"
        edge_id = edge.get("edge_id")
        if not isinstance(edge_id, str) or not ID_PATTERN.fullmatch(edge_id):
            findings.append(finding("flow.edge.invalid", "blocked", f"{edge_path}.edge_id", "edge_id must be a stable ID"))
        elif edge_id in edge_ids:
            findings.append(finding("flow.edge.duplicate", "blocked", f"{edge_path}.edge_id", f"edge_id duplicates {edge_ids[edge_id]}"))
        else:
            edge_ids[edge_id] = edge_path
        relationship_type = edge.get("relationship_type")
        if relationship_type not in {"dependency", "aggregation", "conditional", "rework", "informational"}:
            findings.append(finding("flow.relationship.invalid", "blocked", f"{edge_path}.relationship_type", "relationship_type is not supported"))
        if relationship_type in HARD_DEPENDENCY_TYPES:
            predecessor = str(edge.get("predecessor") or "")
            target = str(edge.get("target") or "")
            hard_edge_pairs.add((predecessor, target))
            predecessor_node = node_by_id.get(predecessor)
            target_node = node_by_id.get(target)
            if predecessor_node is not None and target_node is not None:
                predecessor_date = predecessor_node.get("planned_date")
                target_date = target_node.get("planned_date")
                if _date_error(predecessor_date) is None and _date_error(target_date) is None:
                    if date.fromisoformat(predecessor_date) > date.fromisoformat(target_date):
                        findings.append(
                            finding(
                                "dependency.date_order",
                                "blocked",
                                edge_path,
                                f"hard dependency {predecessor!r} is planned after target {target!r}",
                            )
                        )
        if relationship_type == "conditional":
            condition = edge.get("condition")
            if not isinstance(condition, dict):
                findings.append(finding("flow.condition.missing", "blocked", f"{edge_path}.condition", "conditional dependencies require a canonical condition fact"))
            else:
                for key in ("fact_id", "operator", "expected_value", "source"):
                    if key not in condition:
                        findings.append(finding("flow.condition.missing", "blocked", f"{edge_path}.condition.{key}", f"conditional fact requires {key}"))
                if condition.get("operator") not in {"equals", "not-equals", "in", "not-in"}:
                    findings.append(finding("flow.condition.invalid", "blocked", f"{edge_path}.condition.operator", "condition operator is invalid"))
        elif "condition" in edge:
            findings.append(finding("flow.condition.invalid", "blocked", f"{edge_path}.condition", "only conditional dependencies may carry condition"))
        _validate_source(edge.get("source"), f"{edge_path}.source", execute, findings)
        if stored and edge.get("baseline_revision") != revision:
            findings.append(finding("flow.reference.cross_revision", "blocked", f"{edge_path}.baseline_revision", f"edge revision does not match baseline revision {revision}"))
    for target in sorted({str(edge.get("target")) for edge in flow_edges if edge.get("relationship_type") == "aggregation"}):
        incoming = [edge for edge in flow_edges if edge.get("target") == target and edge.get("relationship_type") == "aggregation"]
        if len(incoming) < 2 or node_by_id.get(target, {}).get("predecessor_rule") != "all":
            findings.append(finding("flow.aggregation.rule", "blocked", f"nodes.{target}.predecessor_rule", "aggregation targets require at least two inputs and predecessor_rule all"))
    if all(
        isinstance(value, str)
        for _, row in all_items
        for value in (row.get("dependencies", []) if isinstance(row.get("dependencies"), list) else [])
    ):
        for cycle in _find_cycles(graph):
            findings.append(finding("dependency.cycle", "blocked", "dependencies", "dependency cycle: " + " -> ".join(cycle)))
    if _illegal_flow_cycle(canonical_ids, flow_edges):
        findings.append(finding("flow.cycle.illegal", "blocked", "dependencies", "dependency cycles are allowed only when every loop edge is explicit rework"))
    if stored and isinstance(revision, int) and not isinstance(revision, bool) and revision > 0:
        for path, row in all_items:
            if "baseline_revision" in row and row["baseline_revision"] != revision:
                findings.append(finding("revision.item_mismatch", "blocked", f"{path}.baseline_revision", f"item revision does not match baseline revision {revision}"))

    critical_path = model.get("critical_path")
    if not isinstance(critical_path, list) or any(not isinstance(value, str) for value in critical_path):
        findings.append(finding("critical_path.invalid", "blocked", "critical_path", "critical_path must be an array of stable IDs"))
    else:
        if len(critical_path) != len(set(critical_path)):
            findings.append(finding("critical_path.duplicate", "blocked", "critical_path", "critical_path contains duplicate IDs"))
        for index, item_id in enumerate(critical_path):
            if item_id not in canonical_ids:
                findings.append(finding("critical_path.unknown", "blocked", f"critical_path[{index}]", f"unknown critical-path ID {item_id!r}"))
        for index, (predecessor, target) in enumerate(zip(critical_path, critical_path[1:]), start=1):
            if predecessor in canonical_ids and target in canonical_ids and (predecessor, target) not in hard_edge_pairs:
                findings.append(
                    finding(
                        "critical_path.disconnected",
                        "blocked",
                        f"critical_path[{index}]",
                        f"critical_path is an ordered hard-dependency chain, but {predecessor!r} -> {target!r} is not a hard dependency edge",
                    )
                )

    weighting = model.get("weighting")
    milestones = model.get("milestones") if isinstance(model.get("milestones"), list) else []
    if not isinstance(weighting, dict) or not isinstance(weighting.get("enabled"), bool):
        findings.append(finding("weighting.invalid", "blocked", "weighting", "weighting requires a boolean enabled field"))
    elif weighting["enabled"]:
        if not isinstance(weighting.get("completion_measure"), str) or not weighting["completion_measure"].strip():
            findings.append(finding("weighting.measure_missing", "blocked", "weighting.completion_measure", "enabled weighting requires completion_measure"))
        _validate_source(weighting.get("source"), "weighting.source", execute, findings)
        total = 0.0
        for index, milestone in enumerate(milestones):
            if not isinstance(milestone, dict):
                continue
            weight = milestone.get("weight")
            if isinstance(weight, float) and not math.isfinite(weight):
                findings.append(finding("weight.non_finite", "blocked", f"milestones[{index}].weight", "milestone weight must be finite"))
            elif not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                findings.append(finding("weight.missing", "blocked", f"milestones[{index}].weight", "every milestone needs a positive weight when weighting is enabled"))
            else:
                total += float(weight)
            if not isinstance(milestone.get("completion_criteria"), str) or not milestone["completion_criteria"].strip():
                findings.append(finding("completion_criteria.missing", "blocked", f"milestones[{index}].completion_criteria", "weighted milestones require completion_criteria"))
        if abs(total - 100.0) > 1e-9:
            findings.append(finding("weight.total", "blocked", "milestones", f"milestone weights total {total:g}; expected 100"))
    else:
        for index, milestone in enumerate(milestones):
            if isinstance(milestone, dict) and milestone.get("weight") is not None:
                findings.append(finding("weight.ignored", "warning", f"milestones[{index}].weight", "weight is present but weighting is disabled"))

    blocked = any(item["severity"] == "blocked" for item in findings)
    return {"valid": not blocked, "findings": findings, "scope_contract": scope_contract}


def stamp_model(model: dict[str, Any], revision: int, timestamp: str, created_at: str | None = None) -> dict[str, Any]:
    stamped = copy.deepcopy(model)
    stamped["revision"] = revision
    stamped["created_at"] = created_at or timestamp
    stamped["updated_at"] = timestamp
    for collection in ("gates", "milestones"):
        for item in stamped.get(collection, []):
            if isinstance(item, dict):
                item["baseline_revision"] = revision
                for dependency in item.get("dependencies", []) if isinstance(item.get("dependencies"), list) else []:
                    if isinstance(dependency, dict):
                        dependency["baseline_revision"] = revision
    return stamped


def canonical_json(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True)


def fingerprint(model: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        values = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def render_markdown(model: dict[str, Any], locale: str, config: dict[str, Any]) -> str:
    project = model["project"]
    critical_ids = set(model["critical_path"])
    lines = [f"# {message('baseline.title', locale)}", "", f"## {message('baseline.metadata', locale)}", ""]
    lines.extend(_markdown_table(
        [message("field.baseline_id", locale), message("field.revision", locale), message("field.status", locale), message("field.project", locale), message("field.owner", locale), message("field.target_date", locale)],
        [[model["baseline_id"], model["revision"], display_label("confirmation", model["confirmation_status"], locale), project["name"], project["owner"], format_date(project["target_date"], locale)]],
    ))
    lines.extend(["", f"## {message('baseline.gates', locale)}", ""])
    gate_rows = []
    for gate in model.get("gates", []):
        gate_rows.append([gate["id"], gate["name"], format_date(gate["planned_date"], locale), gate["owner"], ", ".join(dependency_display(value) for value in gate.get("dependencies", [])) or "-", message("value.yes" if gate["id"] in critical_ids else "value.no", locale), source_ref(gate.get("source"))])
    lines.extend(_markdown_table(
        [message("field.id", locale), message("field.name", locale), message("field.planned_date", locale), message("field.owner", locale), message("field.dependencies", locale), message("field.critical", locale), message("field.source", locale)],
        gate_rows or [[message("baseline.no_items", locale), "-", "-", "-", "-", "-", "-"]],
    ))
    lines.extend(["", f"## {message('baseline.milestones', locale)}", ""])
    milestone_rows = []
    for milestone in model.get("milestones", []):
        milestone_rows.append([milestone["id"], milestone["name"], milestone["workstream_id"], format_date(milestone["planned_date"], locale), milestone["owner"], ", ".join(dependency_display(value) for value in milestone.get("dependencies", [])) or "-", source_ref(milestone.get("source"))])
    lines.extend(_markdown_table(
        [message("field.id", locale), message("field.name", locale), message("field.workstream", locale), message("field.planned_date", locale), message("field.owner", locale), message("field.dependencies", locale), message("field.source", locale)],
        milestone_rows or [[message("baseline.no_items", locale), "-", "-", "-", "-", "-", "-"]],
    ))
    lines.extend(["", f"## {message('baseline.critical_path', locale)}", "", " -> ".join(model.get("critical_path", [])) or message("baseline.no_items", locale), ""])
    fallbacks = config.get("fallbacks", [])
    if "document_output_language" in fallbacks:
        lines.extend([f"> {message('warning.fallback', locale)}", ""])
    elif fallbacks:
        lines.extend([f"> {message('warning.config_fallback', locale, keys=', '.join(fallbacks))}", ""])
    lines.extend([MARKER, "", "```json", canonical_json(model), "```", ""])
    return "\n".join(lines)


def dependency_display(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return f"{value.get('edge_id', '?')}:{value.get('predecessor', '?')}:{value.get('relationship_type', '?')}"
    return str(value)


def atomic_write(path: Path, text: str, *, replace: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temp_name, path)
        else:
            os.link(temp_name, path)
            os.unlink(temp_name)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def process_start_identity(pid: int) -> str | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        fields = proc_stat.read_text(encoding="utf-8").split()
        if len(fields) > 21:
            return f"linux-start-ticks:{fields[21]}"
    except (OSError, UnicodeError):
        pass
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None
    value = completed.stdout.strip()
    return f"ps-lstart:{value}" if completed.returncode == 0 and value else None


def windows_process_is_live(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == WINDOWS_ERROR_INVALID_PARAMETER:
            return False
        if error == WINDOWS_ERROR_ACCESS_DENIED:
            return True
        raise ctypes.WinError(error)
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return exit_code.value == WINDOWS_STILL_ACTIVE
    finally:
        close_handle(handle)


def process_is_live(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return windows_process_is_live(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if getattr(exc, "winerror", None) == WINDOWS_ERROR_INVALID_PARAMETER:
            return False
        raise
    return True


def lock_owner_metadata(timestamp: str | None = None) -> dict[str, Any]:
    pid = os.getpid()
    acquired_at = (
        now_iso(timestamp)
        if timestamp
        else datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "pid": pid,
        "hostname": socket.gethostname(),
        "process_start": process_start_identity(pid),
        "owner_token": uuid.uuid4().hex,
        "acquired_at": acquired_at,
    }


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def create_exclusive_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(handle.fileno(), 0o600)
            else:
                os.chmod(temp_path, 0o600)
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def create_immutable_json(path: Path, value: dict[str, Any]) -> None:
    try:
        create_exclusive_json(path, value)
    except FileExistsError:
        existing = read_json(path)
        if existing.get("lock_fingerprint") != value.get("lock_fingerprint") or existing.get("event") != value.get("event"):
            raise ValueError(f"lock recovery audit collision: {path}")


def inspect_lock_path(lock_path: Path) -> dict[str, Any]:
    if not lock_path.is_file():
        return {
            "present": False,
            "path": str(lock_path),
            "owner_state": "absent",
            "recoverable": False,
            "reason": "lock-not-present",
            "owner": None,
            "lock_fingerprint": None,
        }
    raw = lock_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        owner = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        owner = None
    if not isinstance(owner, dict):
        return {
            "present": True,
            "path": str(lock_path),
            "owner_state": "orphan",
            "recoverable": True,
            "reason": "invalid-owner-metadata",
            "owner": None,
            "lock_fingerprint": digest,
        }
    pid = owner.get("pid")
    owner_host = owner.get("hostname")
    local_host = socket.gethostname()
    if owner_host not in {None, "", local_host}:
        state, recoverable, reason = "remote-owner", False, "owner-host-not-local"
    elif not process_is_live(pid):
        state, recoverable, reason = "orphan", True, "owner-process-missing"
    else:
        supplied_start = owner.get("process_start")
        current_start = process_start_identity(pid)
        if supplied_start and current_start and supplied_start != current_start:
            state, recoverable, reason = "orphan", True, "owner-process-identity-mismatch"
        else:
            state, recoverable, reason = "live-owner", False, "owner-process-live"
    return {
        "present": True,
        "path": str(lock_path),
        "owner_state": state,
        "recoverable": recoverable,
        "reason": reason,
        "owner": owner,
        "lock_fingerprint": digest,
    }


def inspect_baseline_lock(project_root: Path) -> dict[str, Any]:
    return inspect_lock_path(project_root / LOCK_RELATIVE)


@contextmanager
def recovery_guard(project_root: Path):
    guard_path = project_root / LOCK_RECOVERY_GUARD_RELATIVE
    owner = lock_owner_metadata()
    for _ in range(200):
        try:
            create_exclusive_json(guard_path, owner)
            break
        except FileExistsError:
            state = inspect_lock_path(guard_path)
            if state["owner_state"] == "orphan":
                guard_path.unlink(missing_ok=True)
                continue
            time.sleep(0.005)
    else:
        raise WriteLockUnavailable(str(guard_path))
    try:
        yield guard_path
    finally:
        try:
            current = read_json(guard_path)
        except (OSError, ValueError, json.JSONDecodeError):
            current = {}
        if current.get("owner_token") == owner["owner_token"]:
            guard_path.unlink(missing_ok=True)


@contextmanager
def baseline_write_lock(project_root: Path):
    lock_path = project_root / LOCK_RELATIVE
    owner = lock_owner_metadata()
    try:
        create_exclusive_json(lock_path, owner)
    except FileExistsError as exc:
        raise WriteLockUnavailable(str(lock_path)) from exc
    try:
        yield lock_path
    finally:
        try:
            current = read_json(lock_path)
        except (OSError, ValueError, json.JSONDecodeError):
            current = {}
        if current.get("owner_token") == owner["owner_token"]:
            lock_path.unlink(missing_ok=True)


def merge_patch(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def structural_diff(old: Any, new: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(old) is not type(new):
        return [{"path": path, "before": old, "after": new}]
    if isinstance(old, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}"
            if key not in old:
                changes.append({"path": child, "before": None, "after": new[key]})
            elif key not in new:
                changes.append({"path": child, "before": old[key], "after": None})
            else:
                changes.extend(structural_diff(old[key], new[key], child))
        return changes
    if old != new:
        return [{"path": path, "before": old, "after": new}]
    return []


def fact_model(model: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(model)
    for key in ("revision", "created_at", "updated_at", "change_control"):
        result.pop(key, None)
    for collection in ("gates", "milestones"):
        for item in result.get(collection, []):
            if isinstance(item, dict):
                item.pop("baseline_revision", None)
                for dependency in item.get("dependencies", []) if isinstance(item.get("dependencies"), list) else []:
                    if isinstance(dependency, dict):
                        dependency.pop("baseline_revision", None)
    return result


def flow_structural_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    def node_map(model: dict[str, Any]) -> dict[str, Any]:
        return {
            str(item["id"]): {key: value for key, value in item.items() if key not in {"baseline_revision", "dependencies"}}
            for collection in ("gates", "milestones")
            for item in model.get(collection, []) if isinstance(item, dict) and item.get("id")
        }

    def edge_map(model: dict[str, Any]) -> dict[str, Any]:
        return {
            str(edge.get("edge_id")): {key: value for key, value in edge.items() if key != "baseline_revision"}
            for edge in normalized_dependencies(model)
            if edge.get("edge_id")
        }

    old_nodes, new_nodes = node_map(old), node_map(new)
    old_edges, new_edges = edge_map(old), edge_map(new)
    return {
        "nodes_added": sorted(set(new_nodes) - set(old_nodes)),
        "nodes_removed": sorted(set(old_nodes) - set(new_nodes)),
        "nodes_changed": sorted(key for key in set(old_nodes) & set(new_nodes) if old_nodes[key] != new_nodes[key]),
        "edges_added": sorted(set(new_edges) - set(old_edges)),
        "edges_removed": sorted(set(old_edges) - set(new_edges)),
        "edges_changed": sorted(key for key in set(old_edges) & set(new_edges) if old_edges[key] != new_edges[key]),
    }


def paths(project_root: Path) -> tuple[Path, Path]:
    return project_root / BASELINE_RELATIVE, project_root / HISTORY_RELATIVE


def validate_lineage(
    project_root: Path,
    current_model: dict[str, Any] | None = None,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    if baseline_path is None:
        baseline_path, history_path = paths(project_root)
    else:
        baseline_path = baseline_path.resolve()
        history_path = baseline_path.parent / "baseline-history"
    findings: list[dict[str, str]] = []
    archives: list[dict[str, Any]] = []

    if current_model is None:
        if not baseline_path.is_file():
            findings.append(finding("lineage.current_missing", "blocked", str(baseline_path), "current baseline is required to validate revision lineage"))
        else:
            try:
                current_model = parse_baseline(baseline_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                findings.append(finding("lineage.current_parse_error", "blocked", str(baseline_path), str(exc)))

    current_revision = current_model.get("revision") if isinstance(current_model, dict) else None
    if current_model is not None:
        if not isinstance(current_revision, int) or isinstance(current_revision, bool) or current_revision < 1:
            findings.append(finding("lineage.current_revision_invalid", "blocked", str(baseline_path), "current baseline revision must be a positive integer"))
        else:
            findings.extend(_lineage_item_findings(current_model, current_revision, baseline_path))

    archive_revisions: set[int] = set()
    if history_path.is_dir():
        for archive_path in sorted(history_path.glob("program-baseline-r*.md"), key=lambda path: path.name):
            match = ARCHIVE_NAME_PATTERN.fullmatch(archive_path.name)
            if not match:
                findings.append(finding("lineage.archive_name_invalid", "blocked", str(archive_path), "archive filename must be program-baseline-r<positive revision>.md"))
                archives.append({"path": str(archive_path), "filename_revision": None, "canonical_revision": None})
                continue
            filename_revision = int(match.group(1))
            archive_revisions.add(filename_revision)
            try:
                archive_model = parse_baseline(archive_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                findings.append(finding("lineage.archive_parse_error", "blocked", str(archive_path), str(exc)))
                archives.append({"path": str(archive_path), "filename_revision": filename_revision, "canonical_revision": None})
                continue
            canonical_revision = archive_model.get("revision")
            archives.append({"path": str(archive_path), "filename_revision": filename_revision, "canonical_revision": canonical_revision})
            if canonical_revision != filename_revision:
                findings.append(finding("lineage.filename_revision_mismatch", "blocked", str(archive_path), f"filename revision {filename_revision} does not match canonical revision {canonical_revision!r}"))
            findings.extend(_lineage_item_findings(archive_model, filename_revision, archive_path))

    if isinstance(current_revision, int) and not isinstance(current_revision, bool) and current_revision > 0:
        for revision in range(1, current_revision):
            if revision not in archive_revisions:
                missing_path = history_path / f"program-baseline-r{revision}.md"
                findings.append(finding("lineage.revision_missing", "blocked", str(missing_path), f"archive revision {revision} is required before current revision {current_revision}"))
        for revision in sorted(archive_revisions):
            if revision >= current_revision:
                archive_path = history_path / f"program-baseline-r{revision}.md"
                findings.append(finding("lineage.revision_unexpected", "blocked", str(archive_path), f"archive revision {revision} must precede current revision {current_revision}"))

    blocked = any(item["severity"] == "blocked" for item in findings)
    return {
        "valid": not blocked,
        "current_path": str(baseline_path),
        "current_revision": current_revision,
        "archives": archives,
        "findings": findings,
    }


def _lineage_item_findings(model: dict[str, Any], expected_revision: int, baseline_path: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for collection in ("gates", "milestones"):
        rows = model.get(collection)
        if not isinstance(rows, list):
            continue
        for index, item in enumerate(rows):
            if isinstance(item, dict) and item.get("baseline_revision") != expected_revision:
                path = f"{baseline_path}#{collection}[{index}].baseline_revision"
                findings.append(finding("lineage.item_revision_mismatch", "blocked", path, f"item revision must match file revision {expected_revision}"))
    return findings


def config_for(project_root: Path, language: str | None) -> tuple[int, dict[str, Any]]:
    overrides = {"document_output_language": language} if language else None
    return resolve_effective_config(project_root, overrides)


def base_result(intent: str, project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "intent": intent,
        "project_root": str(project_root),
        "document_locale": config.get("document_locale", "en"),
        "effective_config": config.get("values", {}),
        "value_sources": config.get("value_sources", {}),
        "fallbacks": config.get("fallbacks", []),
        "warnings": config.get("warnings", []),
    }


def command_lock_inspect(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    lock = inspect_baseline_lock(project_root)
    result = base_result("lock-inspect", project_root, config)
    result.update(
        {
            "status": "complete",
            "lock": lock,
            "recovery_command": (
                f'python3 "{Path(__file__).resolve()}" lock-recover "{project_root}"'
                if lock["recoverable"]
                else None
            ),
            "recommended_next_step": (
                "Run lock-recover; it will re-inspect ownership and preserve an immutable audit receipt before removing the orphan lock."
                if lock["recoverable"]
                else "Wait for the live owner to finish." if lock["present"] else "No baseline lock recovery is required."
            ),
        }
    )
    return 0, result


def command_lock_recover(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    result = base_result("lock-recover", project_root, config)
    try:
        with recovery_guard(project_root):
            lock = inspect_baseline_lock(project_root)
            if not lock["present"]:
                result.update(
                    {
                        "status": "complete",
                        "lock": lock,
                        "recovery_performed": False,
                        "audit_receipt": None,
                        "recommended_next_step": "No baseline lock recovery is required.",
                    }
                )
                return 0, result
            if not lock["recoverable"]:
                code = "write.lock_live_owner" if lock["owner_state"] == "live-owner" else "write.lock_owner_unverifiable"
                result.update(
                    {
                        "status": "blocked",
                        "lock": lock,
                        "recovery_performed": False,
                        "audit_receipt": None,
                        "findings": [finding(code, "blocked", lock["path"], "baseline lock owner is not an orphan and cannot be recovered")],
                        "recommended_next_step": "Wait for the owner to finish, then rerun lock-inspect. Do not delete the lock manually.",
                    }
                )
                return 1, result

            recovered_at = now_iso(getattr(args, "as_of", None))
            fingerprint_value = str(lock["lock_fingerprint"])
            receipt_path = project_root / LOCK_RECOVERY_RELATIVE / f"baseline-lock-{fingerprint_value}.json"
            receipt = {
                "schema_version": "1.0",
                "event": "baseline-lock-orphan-recovered",
                "lock_path": lock["path"],
                "lock_fingerprint": fingerprint_value,
                "orphan_reason": lock["reason"],
                "orphan_owner": lock["owner"],
                "recovered_at": recovered_at,
                "recovered_by": lock_owner_metadata(recovered_at),
            }
            try:
                create_immutable_json(receipt_path, receipt)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                result.update(
                    {
                        "ok": False,
                        "status": "error",
                        "error_code": "ADP-BASELINE-LOCK-RECOVERY-AUDIT-FAILED",
                        "reason": str(exc),
                        "lock": lock,
                        "recovery_performed": False,
                        "audit_receipt": None,
                        "recommended_next_step": "Restore writable audit storage and rerun lock-recover; the orphan lock was retained.",
                    }
                )
                return 2, result

            current = inspect_baseline_lock(project_root)
            if not current["present"]:
                result.update(
                    {
                        "status": "complete",
                        "lock": current,
                        "recovery_performed": False,
                        "audit_receipt": str(receipt_path),
                        "recommended_next_step": "Another recovery removed the same orphan lock; inspect the baseline before retrying the write.",
                    }
                )
                return 0, result
            if current["lock_fingerprint"] != fingerprint_value:
                result.update(
                    {
                        "status": "blocked",
                        "lock": current,
                        "recovery_performed": False,
                        "audit_receipt": str(receipt_path),
                        "findings": [finding("write.lock_changed", "blocked", current["path"], "baseline lock changed during recovery and was retained")],
                        "recommended_next_step": "Rerun lock-inspect and do not delete the changed lock manually.",
                    }
                )
                return 1, result
            try:
                Path(current["path"]).unlink()
            except OSError as exc:
                result.update(
                    {
                        "ok": False,
                        "status": "error",
                        "error_code": "ADP-BASELINE-LOCK-RECOVERY-REMOVE-FAILED",
                        "reason": str(exc),
                        "lock": current,
                        "recovery_performed": False,
                        "audit_receipt": str(receipt_path),
                        "recommended_next_step": "Restore lock-file permissions and rerun lock-recover; the audited orphan lock was retained.",
                    }
                )
                return 2, result
            result.update(
                {
                    "status": "complete",
                    "lock": {**current, "present": False, "owner_state": "recovered", "recoverable": False},
                    "recovery_performed": True,
                    "audit_receipt": str(receipt_path),
                    "recommended_next_step": "Inspect the current baseline revision, then retry the reviewed baseline write.",
                }
            )
            return 0, result
    except WriteLockUnavailable as exc:
        result.update(
            {
                "status": "blocked",
                "recovery_performed": False,
                "audit_receipt": None,
                "findings": [finding("write.lock_recovery_busy", "blocked", str(exc), "another baseline lock recovery is active")],
                "recommended_next_step": "Wait for the active recovery to finish, then rerun lock-inspect.",
            }
        )
        return 1, result


def command_propose(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    model = read_json(Path(args.input))
    validation = validate_model(
        model,
        execute=False,
        stored=False,
        registered_workstreams=current_wdr_registry(project_root),
    )
    baseline_path, _ = paths(project_root)
    result = base_result("propose", project_root, config)
    result.update({
        "status": "needs_confirmation" if validation["valid"] else "blocked",
        "dry_run": True,
        "can_apply": validation["valid"],
        "baseline_exists": baseline_path.is_file(),
        "candidate": model,
        "findings": validation["findings"],
        "planned_files": [],
        "recommended_next_step": "Confirm candidate authority and facts, then use create or update without --execute for an exact write preview." if validation["valid"] else "Resolve blocked findings and rerun propose.",
    })
    return (0 if validation["valid"] else 1), result


def _lock_conflict(intent: str, project_root: Path, config: dict[str, Any], lock_path: str) -> tuple[int, dict[str, Any]]:
    lock = inspect_baseline_lock(project_root)
    result = base_result(intent, project_root, config)
    result.update({
        "status": "blocked",
        "dry_run": False,
        "can_apply": False,
        "lock": lock,
        "findings": [finding("write.locked", "blocked", lock_path, f"baseline write lock state is {lock['owner_state']}")],
        "recommended_next_step": (
            "Run lock-inspect and then explicit lock-recover; never delete an orphan lock manually."
            if lock["recoverable"]
            else "Wait for the active writer to finish, inspect the current revision, and retry."
        ),
    })
    return 1, result


def command_create(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not args.execute:
        return _command_create(args, project_root, config)
    try:
        with baseline_write_lock(project_root):
            return _command_create(args, project_root, config)
    except WriteLockUnavailable as exc:
        return _lock_conflict("create", project_root, config, str(exc))


def _command_create(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    baseline_path, _ = paths(project_root)
    result = base_result("create", project_root, config)
    if baseline_path.exists():
        result.update({"status": "blocked", "dry_run": not args.execute, "can_apply": False, "findings": [finding("baseline.exists", "blocked", str(baseline_path), "baseline already exists; use update with expected revision")], "recommended_next_step": "Run inspect, then update with the current expected revision."})
        return 1, result
    timestamp = now_iso(args.as_of)
    model = stamp_model(read_json(Path(args.input)), 1, timestamp)
    registry = None if getattr(args, "skip_wdr_registry", False) else current_wdr_registry(project_root, baseline_path)
    validation = validate_model(
        model,
        execute=True,
        registered_workstreams=registry,
    )
    preview_token = fingerprint({"intent": "create", "baseline": fact_model(model)})
    result.update({"status": "ready" if validation["valid"] else "blocked", "dry_run": not args.execute, "can_apply": validation["valid"], "baseline_revision": 1, "baseline_fingerprint": fingerprint(model), "preview_token": preview_token, "findings": validation["findings"], "planned_files": [str(baseline_path)] if validation["valid"] else [], "recommended_next_step": "Review the plan, then repeat with --execute and --preview-token." if validation["valid"] and not args.execute else "Resolve blocked findings and rerun create."})
    if not validation["valid"]:
        return 1, result
    if args.execute:
        supplied_token = getattr(args, "preview_token", None)
        if supplied_token != preview_token:
            code = "preview.token_required" if supplied_token is None else "preview.token_mismatch"
            message_text = "--execute requires the preview_token from the reviewed dry-run" if supplied_token is None else "input no longer matches the reviewed dry-run"
            result.update({"status": "blocked", "can_apply": False, "findings": [finding(code, "blocked", "preview_token", message_text)], "recommended_next_step": "Rerun create without --execute and review the returned preview_token."})
            return 1, result
        try:
            atomic_write(baseline_path, render_markdown(model, config["document_locale"], config), replace=False)
        except FileExistsError:
            result.update({"status": "blocked", "can_apply": False, "findings": [finding("baseline.exists", "blocked", str(baseline_path), "baseline was created by another writer; use update with expected revision")], "recommended_next_step": "Run inspect, then update with the current expected revision."})
            return 1, result
        result.update({"status": "complete", "dry_run": False, "written_files": [str(baseline_path)], "recommended_next_step": "Run adp-state-audit, then adp-program-status when available."})
    return 0, result


def _validate_change_authority(change: dict[str, Any], findings: list[dict[str, str]]) -> None:
    reason = change.get("change_reason")
    if not isinstance(reason, str) or not reason.strip():
        findings.append(finding("change.reason_missing", "blocked", "change_reason", "approved baseline updates require a change_reason"))
    _validate_source(change.get("decision_source"), "decision_source", True, findings)


def command_update(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not args.execute:
        return _command_update(args, project_root, config)
    try:
        with baseline_write_lock(project_root):
            return _command_update(args, project_root, config)
    except WriteLockUnavailable as exc:
        return _lock_conflict("update", project_root, config, str(exc))


def _command_update(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    baseline_path, history_path = paths(project_root)
    result = base_result("update", project_root, config)
    if not baseline_path.is_file():
        result.update({"status": "blocked", "dry_run": not args.execute, "can_apply": False, "findings": [finding("baseline.missing", "blocked", str(baseline_path), "baseline does not exist; use create")], "recommended_next_step": "Use propose or create."})
        return 1, result
    current = parse_baseline(baseline_path)
    current_revision = current.get("revision")
    if not isinstance(current_revision, int) or isinstance(current_revision, bool) or current_revision != args.expected_revision:
        result.update({"status": "blocked", "dry_run": not args.execute, "can_apply": False, "baseline_revision": current_revision, "findings": [finding("revision.conflict", "blocked", "revision", f"expected revision {args.expected_revision}, found {current_revision}")], "recommended_next_step": "Inspect the current revision and rebuild the change against it."})
        return 1, result
    change = read_json(Path(args.input))
    findings: list[dict[str, str]] = []
    _validate_change_authority(change, findings)
    if not isinstance(change.get("changes"), dict):
        findings.append(finding("change.invalid", "blocked", "changes", "update input requires an object named changes"))
        patched = current
    else:
        patched = merge_patch(current, change["changes"])
    timestamp = now_iso(args.as_of)
    changes = structural_diff(fact_model(current), fact_model(patched))
    patched["change_control"] = {"reason": change.get("change_reason"), "decision_source": change.get("decision_source")}
    updated = stamp_model(patched, int(current_revision) + 1, timestamp, str(current.get("created_at") or timestamp))
    current_fingerprint = fingerprint(current)
    preview_token = fingerprint({
        "intent": "update",
        "expected_revision": args.expected_revision,
        "current_baseline_fingerprint": current_fingerprint,
        "baseline": fact_model(updated),
        "change_control": updated["change_control"],
    })
    validation = validate_model(
        updated,
        execute=True,
        registered_workstreams=current_wdr_registry(project_root),
    )
    findings.extend(validation["findings"])
    if not changes:
        findings.append(finding("change.empty", "blocked", "changes", "update does not change baseline facts"))
    blocked = any(item["severity"] == "blocked" for item in findings)
    archive_path = history_path / f"program-baseline-r{current_revision}.md"
    result.update({"status": "blocked" if blocked else "ready", "dry_run": not args.execute, "can_apply": not blocked, "baseline_revision": current_revision, "next_revision": int(current_revision) + 1, "current_baseline_fingerprint": current_fingerprint, "baseline_fingerprint": fingerprint(updated), "preview_token": preview_token, "findings": findings, "diff": changes, "flow_diff": flow_structural_diff(current, updated), "planned_files": [str(archive_path), str(baseline_path)] if not blocked else [], "recommended_next_step": "Review the diff, then repeat with --execute and --preview-token." if not blocked and not args.execute else "Resolve blocked findings and rerun update."})
    if blocked:
        return 1, result
    if args.execute:
        supplied_token = getattr(args, "preview_token", None)
        if supplied_token != preview_token:
            code = "preview.token_required" if supplied_token is None else "preview.token_mismatch"
            message_text = "--execute requires the preview_token from the reviewed dry-run" if supplied_token is None else "input or current baseline no longer matches the reviewed dry-run"
            result.update({"status": "blocked", "can_apply": False, "findings": [finding(code, "blocked", "preview_token", message_text)], "recommended_next_step": "Rerun update without --execute and review the returned diff and preview_token."})
            return 1, result
        history_path.mkdir(parents=True, exist_ok=True)
        existing_text = baseline_path.read_text(encoding="utf-8-sig")
        if archive_path.exists() and archive_path.read_text(encoding="utf-8-sig") != existing_text:
            result.update({"status": "blocked", "can_apply": False, "findings": findings + [finding("archive.conflict", "blocked", str(archive_path), "archive path exists with different content")], "recommended_next_step": "Resolve the archive conflict without overwriting history."})
            return 1, result
        if not archive_path.exists():
            try:
                atomic_write(archive_path, existing_text, replace=False)
            except FileExistsError:
                result.update({"status": "blocked", "can_apply": False, "findings": findings + [finding("archive.conflict", "blocked", str(archive_path), "archive path was created by another writer")], "recommended_next_step": "Inspect the current revision and archive history before retrying."})
                return 1, result
        atomic_write(baseline_path, render_markdown(updated, config["document_locale"], config))
        result.update({"status": "complete", "dry_run": False, "archive_path": str(archive_path), "written_files": [str(archive_path), str(baseline_path)], "recommended_next_step": "Run adp-state-audit, then regenerate program status and dependent views."})
    return 0, result


def command_validate(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    baseline_path = Path(args.baseline).resolve() if args.baseline else paths(project_root)[0]
    skip_wdr_registry = bool(getattr(args, "skip_wdr_registry", False))
    result = base_result("validate", project_root, config)
    if not baseline_path.is_file():
        result.update({"status": "blocked", "valid": False, "baseline_path": str(baseline_path), "findings": [finding("baseline.missing", "blocked", str(baseline_path), "baseline file does not exist")], "recommended_next_step": "Run adp-plan-baseline propose or create."})
        return 1, result
    try:
        model = parse_baseline(baseline_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"status": "blocked", "valid": False, "baseline_path": str(baseline_path), "findings": [finding("baseline.parse_error", "blocked", str(baseline_path), str(exc))], "recovery_command": f'uv run scripts/baseline.py validate "{project_root}" --baseline "{baseline_path}"', "recommended_next_step": "Restore a valid archived revision or repair through an approved update, then rerun the recovery command."})
        return 1, result
    registry = current_wdr_registry(
        project_root,
        baseline_path,
        include_physical=not skip_wdr_registry,
    )
    validation = validate_model(
        model,
        execute=True,
        registered_workstreams=registry,
        skip_wdr_registry=skip_wdr_registry,
    )
    lineage = validate_lineage(project_root, model, baseline_path)
    findings = validation["findings"] + lineage["findings"]
    valid = not any(item["severity"] == "blocked" for item in findings)
    revision = model.get("revision")
    result.update({"status": "complete" if valid else "blocked", "valid": valid, "baseline_path": str(baseline_path), "baseline_revision": revision, "baseline_fingerprint": fingerprint(model), "scope_contract": validation["scope_contract"], "lineage": {key: value for key, value in lineage.items() if key != "findings"}, "findings": findings, "recommended_next_step": "Baseline is valid; continue to adp-state-audit." if valid else "Resolve blocked findings through adp-plan-baseline update, then rerun validation."})
    if not valid:
        result["recovery_command"] = f'uv run scripts/baseline.py validate "{project_root}" --baseline "{baseline_path}"'
    return (0 if valid else 1), result


def command_inspect(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    baseline_path, history_path = paths(project_root)
    selected = baseline_path if args.revision is None else history_path / f"program-baseline-r{args.revision}.md"
    result = base_result("inspect", project_root, config)
    if not selected.is_file():
        result.update({"status": "blocked", "baseline_path": str(selected), "findings": [finding("baseline.missing", "blocked", str(selected), "requested baseline revision does not exist")], "recommended_next_step": "Run create for a missing current baseline, or inspect an available revision."})
        return 1, result
    try:
        model = parse_baseline(selected)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"status": "blocked", "valid": False, "baseline_path": str(selected), "findings": [finding("baseline.parse_error", "blocked", str(selected), str(exc))], "recovery_command": f'uv run scripts/baseline.py validate "{project_root}" --baseline "{selected}"', "recommended_next_step": "Restore or repair the selected baseline, then rerun the recovery command."})
        return 1, result
    validation = validate_model(
        model,
        execute=True,
        registered_workstreams=current_wdr_registry(project_root, selected),
    )
    lineage = validate_lineage(project_root, model if selected == baseline_path else None)
    findings = validation["findings"] + lineage["findings"]
    valid = not any(item["severity"] == "blocked" for item in findings)
    if not valid:
        revision = model.get("revision")
        result.update({"status": "blocked", "valid": False, "baseline_path": str(selected), "baseline_revision": revision, "baseline_fingerprint": fingerprint(model), "scope_contract": validation["scope_contract"], "lineage": {key: value for key, value in lineage.items() if key != "findings"}, "findings": findings, "recovery_command": f'uv run scripts/baseline.py validate "{project_root}" --baseline "{selected}"', "recommended_next_step": "Resolve blocked findings, then rerun the recovery command."})
        return 1, result
    archives = [item["path"] for item in lineage["archives"]]
    result.update({"status": "complete", "valid": True, "baseline_path": str(selected), "baseline_revision": model.get("revision"), "baseline_fingerprint": fingerprint(model), "scope_contract": validation["scope_contract"], "project": model.get("project"), "gate_count": len(model.get("gates", [])), "milestone_count": len(model.get("milestones", [])), "critical_path": model.get("critical_path", []), "history": archives, "lineage": {key: value for key, value in lineage.items() if key != "findings"}, "summary_markdown": render_markdown(model, config["document_locale"], config).split(MARKER, 1)[0].rstrip(), "findings": findings, "recommended_next_step": "Use update for approved plan changes; otherwise continue to baseline consumers."})
    return 0, result


def add_common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("project_root", help="Target project root where ADP memory lives.")
    subparser.add_argument("--language", help="Runtime document language override (Chinese/English or locale alias).")
    subparser.add_argument("-o", "--output", help="Write command JSON to this path instead of stdout.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    propose = subparsers.add_parser("propose", help="Validate a review-only baseline candidate.")
    add_common(propose)
    propose.add_argument("--input", required=True, help="Candidate baseline JSON path.")

    create = subparsers.add_parser("create", help="Preview or create revision 1.")
    add_common(create)
    create.add_argument("--input", required=True, help="Confirmed baseline JSON path.")
    create.add_argument("--execute", action="store_true", help="Atomically write the baseline; omitted means dry-run.")
    create.add_argument("--preview-token", help="preview_token from the reviewed dry-run; required with --execute.")
    create.add_argument("--as-of", help="Deterministic ISO timestamp for tests or controlled execution.")

    update = subparsers.add_parser("update", help="Preview or apply an approved merge-patch update.")
    add_common(update)
    update.add_argument("--input", required=True, help="Change JSON with changes, change_reason, and decision_source.")
    update.add_argument("--expected-revision", required=True, type=int, help="Revision the change was reviewed against.")
    update.add_argument("--execute", action="store_true", help="Archive and atomically write; omitted means dry-run.")
    update.add_argument("--preview-token", help="preview_token from the reviewed dry-run; required with --execute.")
    update.add_argument("--as-of", help="Deterministic ISO timestamp for tests or controlled execution.")

    validate = subparsers.add_parser("validate", help="Validate an existing baseline without writing.")
    add_common(validate)
    validate.add_argument("--baseline", help="Optional baseline path override.")
    validate.add_argument(
        "--skip-wdr-registry",
        action="store_true",
        help="Skip physical WDR identity checks for an explicitly virtual-only downstream scope.",
    )

    inspect = subparsers.add_parser("inspect", help="Inspect current or archived baseline without writing.")
    add_common(inspect)
    inspect.add_argument("--revision", type=int, help="Archived revision to inspect; default is current.")

    lock_inspect = subparsers.add_parser("lock-inspect", help="Inspect baseline write-lock ownership without changing it.")
    add_common(lock_inspect)

    lock_recover = subparsers.add_parser("lock-recover", help="Recover only a verified orphan lock and preserve an immutable audit receipt.")
    add_common(lock_recover)
    lock_recover.add_argument("--as-of", help="Recovery timestamp override for controlled execution and tests.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config_code, config = config_for(project_root, args.language)
    if config_code:
        result = {"ok": False, "status": "error", "intent": args.command, **config}
        code = 2
    else:
        handlers = {
            "propose": command_propose,
            "create": command_create,
            "update": command_update,
            "validate": command_validate,
            "inspect": command_inspect,
            "lock-inspect": command_lock_inspect,
            "lock-recover": command_lock_recover,
        }
        try:
            code, result = handlers[args.command](args, project_root, config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            code, result = 2, {"ok": False, "status": "error", "intent": args.command, "project_root": str(project_root), "error": str(exc)}
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(payload)
    return code


if __name__ == "__main__":
    sys.exit(main())
