#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Plan, validate, version, archive, and inspect the ADP program baseline."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from adp_effective_config import display_label, format_date, message, resolve_effective_config


MARKER = "<!-- adp:program-baseline:v1 -->"
APPROVED_STATES = {"confirmed", "approved"}
ALL_STATES = {"candidate", *APPROVED_STATES}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ARCHIVE_NAME_PATTERN = re.compile(r"^program-baseline-r([1-9][0-9]*)\.md$")
MEMORY_RELATIVE = Path("_bmad-output/adp/memory")
BASELINE_RELATIVE = MEMORY_RELATIVE / "plans/program-baseline.md"
HISTORY_RELATIVE = MEMORY_RELATIVE / "plans/baseline-history"
LOCK_RELATIVE = MEMORY_RELATIVE / "plans/.program-baseline.lock"


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
    if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
        findings.append(finding("dependencies.invalid", "blocked", f"{path}.dependencies", "dependencies must be an array of stable IDs"))
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


def validate_model(model: Any, execute: bool = False, stored: bool = True) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not isinstance(model, dict):
        return {"valid": False, "findings": [finding("model.invalid", "blocked", "$", "baseline must be an object")]}

    required = ["schema_version", "baseline_id", "confirmation_status", "project", "default_tolerance_days", "gates", "milestones", "critical_path", "weighting"]
    if stored:
        required.extend(["revision", "created_at", "updated_at"])
    for key in required:
        if key not in model:
            findings.append(finding("field.missing", "blocked", key, f"required field {key} is missing"))
    if model.get("schema_version") != "1.0":
        findings.append(finding("schema.unsupported", "blocked", "schema_version", "schema_version must be 1.0"))
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
            item_id = _validate_item(row, path, kind, execute, stored, findings)
            if isinstance(row, dict):
                all_items.append((path, row))
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
        for dependency in dependencies:
            if dependency not in canonical_ids:
                findings.append(finding("dependency.unknown", "blocked", f"{path}.dependencies", f"unknown dependency {dependency!r}"))
            else:
                graph[item_id].append(dependency)
    for cycle in _find_cycles(graph):
        findings.append(finding("dependency.cycle", "blocked", "dependencies", "dependency cycle: " + " -> ".join(cycle)))
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
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
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
    return {"valid": not blocked, "findings": findings}


def stamp_model(model: dict[str, Any], revision: int, timestamp: str, created_at: str | None = None) -> dict[str, Any]:
    stamped = copy.deepcopy(model)
    stamped["revision"] = revision
    stamped["created_at"] = created_at or timestamp
    stamped["updated_at"] = timestamp
    for collection in ("gates", "milestones"):
        for item in stamped.get(collection, []):
            if isinstance(item, dict):
                item["baseline_revision"] = revision
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
        gate_rows.append([gate["id"], gate["name"], format_date(gate["planned_date"], locale), gate["owner"], ", ".join(gate.get("dependencies", [])) or "-", message("value.yes" if gate["id"] in critical_ids else "value.no", locale), source_ref(gate.get("source"))])
    lines.extend(_markdown_table(
        [message("field.id", locale), message("field.name", locale), message("field.planned_date", locale), message("field.owner", locale), message("field.dependencies", locale), message("field.critical", locale), message("field.source", locale)],
        gate_rows or [[message("baseline.no_items", locale), "-", "-", "-", "-", "-", "-"]],
    ))
    lines.extend(["", f"## {message('baseline.milestones', locale)}", ""])
    milestone_rows = []
    for milestone in model.get("milestones", []):
        milestone_rows.append([milestone["id"], milestone["name"], milestone["workstream_id"], format_date(milestone["planned_date"], locale), milestone["owner"], ", ".join(milestone.get("dependencies", [])) or "-", source_ref(milestone.get("source"))])
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


@contextmanager
def baseline_write_lock(project_root: Path):
    lock_path = project_root / LOCK_RELATIVE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise WriteLockUnavailable(str(lock_path)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"pid": os.getpid()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


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
    return result


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


def command_propose(args: argparse.Namespace, project_root: Path, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    model = read_json(Path(args.input))
    validation = validate_model(model, execute=False, stored=False)
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
    result = base_result(intent, project_root, config)
    result.update({
        "status": "blocked",
        "dry_run": False,
        "can_apply": False,
        "findings": [finding("write.locked", "blocked", lock_path, "another baseline writer holds the project lock")],
        "recommended_next_step": "Wait for the active writer to finish, inspect the current revision, and retry.",
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
    validation = validate_model(model, execute=True)
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
    validation = validate_model(updated, execute=True)
    findings.extend(validation["findings"])
    if not changes:
        findings.append(finding("change.empty", "blocked", "changes", "update does not change baseline facts"))
    blocked = any(item["severity"] == "blocked" for item in findings)
    archive_path = history_path / f"program-baseline-r{current_revision}.md"
    result.update({"status": "blocked" if blocked else "ready", "dry_run": not args.execute, "can_apply": not blocked, "baseline_revision": current_revision, "next_revision": int(current_revision) + 1, "current_baseline_fingerprint": current_fingerprint, "baseline_fingerprint": fingerprint(updated), "preview_token": preview_token, "findings": findings, "diff": changes, "planned_files": [str(archive_path), str(baseline_path)] if not blocked else [], "recommended_next_step": "Review the diff, then repeat with --execute and --preview-token." if not blocked and not args.execute else "Resolve blocked findings and rerun update."})
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
    current_path = paths(project_root)[0]
    result = base_result("validate", project_root, config)
    if not baseline_path.is_file():
        result.update({"status": "blocked", "valid": False, "baseline_path": str(baseline_path), "findings": [finding("baseline.missing", "blocked", str(baseline_path), "baseline file does not exist")], "recommended_next_step": "Run adp-plan-baseline propose or create."})
        return 1, result
    try:
        model = parse_baseline(baseline_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"status": "blocked", "valid": False, "baseline_path": str(baseline_path), "findings": [finding("baseline.parse_error", "blocked", str(baseline_path), str(exc))], "recovery_command": f'uv run scripts/baseline.py validate "{project_root}" --baseline "{baseline_path}"', "recommended_next_step": "Restore a valid archived revision or repair through an approved update, then rerun the recovery command."})
        return 1, result
    validation = validate_model(model, execute=True)
    lineage = validate_lineage(project_root, model, baseline_path)
    findings = validation["findings"] + lineage["findings"]
    valid = not any(item["severity"] == "blocked" for item in findings)
    revision = model.get("revision")
    result.update({"status": "complete" if valid else "blocked", "valid": valid, "baseline_path": str(baseline_path), "baseline_revision": revision, "baseline_fingerprint": fingerprint(model), "lineage": {key: value for key, value in lineage.items() if key != "findings"}, "findings": findings, "recommended_next_step": "Baseline is valid; continue to adp-state-audit." if valid else "Resolve blocked findings through adp-plan-baseline update, then rerun validation."})
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
    validation = validate_model(model, execute=True)
    lineage = validate_lineage(project_root, model if selected == baseline_path else None)
    findings = validation["findings"] + lineage["findings"]
    valid = not any(item["severity"] == "blocked" for item in findings)
    if not valid:
        revision = model.get("revision")
        result.update({"status": "blocked", "valid": False, "baseline_path": str(selected), "baseline_revision": revision, "baseline_fingerprint": fingerprint(model), "lineage": {key: value for key, value in lineage.items() if key != "findings"}, "findings": findings, "recovery_command": f'uv run scripts/baseline.py validate "{project_root}" --baseline "{selected}"', "recommended_next_step": "Resolve blocked findings, then rerun the recovery command."})
        return 1, result
    archives = [item["path"] for item in lineage["archives"]]
    result.update({"status": "complete", "valid": True, "baseline_path": str(selected), "baseline_revision": model.get("revision"), "baseline_fingerprint": fingerprint(model), "project": model.get("project"), "gate_count": len(model.get("gates", [])), "milestone_count": len(model.get("milestones", [])), "critical_path": model.get("critical_path", []), "history": archives, "lineage": {key: value for key, value in lineage.items() if key != "findings"}, "summary_markdown": render_markdown(model, config["document_locale"], config).split(MARKER, 1)[0].rstrip(), "findings": [], "recommended_next_step": "Use update for approved plan changes; otherwise continue to baseline consumers."})
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

    inspect = subparsers.add_parser("inspect", help="Inspect current or archived baseline without writing.")
    add_common(inspect)
    inspect.add_argument("--revision", type=int, help="Archived revision to inspect; default is current.")
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
