#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Render a source-backed ADP roadmap view."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
LOCALE_CATALOG_PATH = Path(__file__).resolve().parents[2] / "adp-plan-baseline/assets/locale-catalog.json"
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
ROADMAP_SCHEMA_VERSION = 2
GENERATOR_VERSION = "2.0.0"
PLACEHOLDERS = {"", "-", "tbd", "todo", "none", "n/a", "na", "unknown"}
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
VALID_TYPES = {
    "checkpoint",
    "business-decision",
    "readiness-gate",
    "cutover-gate",
    "dependency-release",
    "delivery-window",
}
VALID_STATUSES = {"planned", "at-risk", "done", "blocked"}
PROGRAM_STATUSES = {"on-plan", "at-risk", "off-plan", "indeterminate"}
PROGRAM_CONFIDENCE = {"high", "medium", "low", "unknown"}
PROGRESS_SCHEMA_VERSION = "2.0.0"
PROGRESS_MIGRATION_ERROR = "ADP-PROGRESS-MIGRATION-REQUIRED"
VALID_CONFIDENCE = {"high", "medium", "low"}
DECISION_COMPLETED_STATUSES = {"accepted", "closed", "done"}
DECISION_OPEN_STATUSES = {"open"}
DECISION_TERMINAL_STATUSES = DECISION_COMPLETED_STATUSES | {
    "cancelled",
    "rejected",
    "superseded",
}
AUDIT_STATUSES = {"pass", "warning", "blocked"}
AUDIT_MAX_AGE = timedelta(hours=24)
AUDIT_FUTURE_TOLERANCE = timedelta(minutes=5)
PREPASS_SCHEMA_VERSION = 2
ROADMAP_CAPABILITY = "global-project-readout"
RENDER_SOURCE_PATTERNS = (
    "workstreams/*/delivery-record.md",
    "intake/bmm-checkpoints/candidates/CHK-*.json",
    "decisions/decision-log.md",
    "decisions/business-decision-packets/*.md",
    "views/acceptance-readiness.md",
    "views/cutover-readiness.md",
    "l0/extracted-gates.md",
    "l0/extracted-decision-gates.md",
    "actions/action-ledger.md",
)
FIXED_RENDER_SOURCES = (
    "decisions/decision-log.md",
    "views/acceptance-readiness.md",
    "views/cutover-readiness.md",
    "l0/extracted-gates.md",
    "l0/extracted-decision-gates.md",
    "actions/action-ledger.md",
)
PLACEHOLDER_IDS = {"", "tbd", "todo", "none", "n-a", "na", "unknown"}
L0_STATUS_MAP = {
    "open": "planned",
    "planned": "planned",
    "at-risk": "at-risk",
    "blocked": "blocked",
    "closed": "done",
    "done": "done",
}


@dataclass
class RoadmapItem:
    id: str
    milestone: str
    type: str
    status: str
    planned: str
    forecast: str
    actual: str
    owner: str
    confidence: str
    depends_on: str
    source: str
    source_type: str
    workstreams: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    variance_days: int | None = None
    baseline_revision: int | None = None
    planned_source: str = "TBD"
    forecast_source: str = "TBD"
    actual_source: str = "TBD"
    status_source: str = "TBD"
    status_rule_id: str = "TBD"
    source_references: list[str] = field(default_factory=list)


@dataclass
class ExcludedItem:
    source: str
    source_type: str
    item: str
    reason: str
    code: str
    risk: bool
    workstreams: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate views/roadmap.json and views/roadmap.md from ADP durable state. "
            "The script never invents milestone dates and does not promote action due dates to milestones."
        )
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument(
        "--memory-root",
        default=DEFAULT_MEMORY_ROOT,
        help=f"ADP memory root, relative to project root unless absolute. Default: {DEFAULT_MEMORY_ROOT}.",
    )
    parser.add_argument("--workstream", action="append", default=[], help="Workstream id to include. Repeatable.")
    parser.add_argument("--audit", help="Existing roadmap-scenario adp-state-audit JSON to validate and consume.")
    parser.add_argument("--prepass-json", help="Optional adp-state-prepass JSON forwarded when generating the audit.")
    parser.add_argument("--date", dest="as_of", help="Roadmap date, YYYY-MM-DD. Alias for --as-of.")
    parser.add_argument("--as-of", dest="as_of_alt", help="Roadmap date, YYYY-MM-DD.")
    parser.add_argument(
        "--output-dir",
        help="Directory for roadmap.md/json. Default: <memory-root>/views. Relative paths resolve from memory root.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build the roadmap payload without writing files.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write run result JSON to this file instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        result = {"ok": False, "status": "error", "error": str(exc)}
        emit(result, args.output)
        return 2
    emit(result, args.output)
    if not result.get("ok"):
        return 1 if result.get("status") == "blocked" else 2
    return 0


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return {"ok": False, "status": "error", "error": "project_root is not an existing directory", "project_root": str(project_root)}

    memory_root = resolve_memory_root(project_root, args.memory_root)
    if not memory_root.exists() or not memory_root.is_dir():
        return {
            "ok": False,
            "status": "blocked",
            "error": "ADP memory root is missing; run adp-project-kickoff or pass --memory-root",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflows": ["adp-project-kickoff"],
        }

    as_of = date.fromisoformat(args.as_of_alt or args.as_of) if (args.as_of_alt or args.as_of) else date.today()
    timeline_gate = load_canonical_timeline_inputs(project_root, memory_root, as_of)
    if not timeline_gate.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "error": timeline_gate.get("error", "canonical roadmap inputs are unavailable"),
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflows": timeline_gate.get(
                "recommended_workflows", ["adp-plan-baseline", "adp-program-status"]
            ),
        }
    selected = {normalize_id(item) for item in args.workstream if normalize_id(item)}
    available_workstreams = discover_workstream_ids(memory_root) | {
        normalize_id(str(item.get("workstream_id", "")))
        for item in timeline_gate["baseline"].get("milestones", [])
        if normalize_id(str(item.get("workstream_id", "")))
    }
    unknown_workstreams = sorted(selected - available_workstreams)
    if unknown_workstreams:
        return {
            "ok": False,
            "status": "blocked",
            "error": (
                f"unknown workstream(s): {', '.join(unknown_workstreams)}; "
                f"available: {', '.join(sorted(available_workstreams)) or '<none>'}"
            ),
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflows": ["adp-workstream-register"],
        }
    effective_workstreams = selected or available_workstreams
    output_dir = resolve_output_dir(args.output_dir, memory_root, selected)
    audit_gate = load_or_run_audit_gate(
        args,
        project_root,
        memory_root,
        selected,
        effective_workstreams,
        as_of,
    )
    if not audit_gate.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "error": audit_gate.get("error", "roadmap audit gate failed"),
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "audit_path": audit_gate.get("audit_path", ""),
            "recommended_workflows": audit_gate.get("recommended_workflows", ["adp-state-audit"]),
        }
    previous, previous_warning = load_previous_roadmap(output_dir / "roadmap.json")

    build = build_roadmap(
        project_root,
        memory_root,
        selected,
        as_of,
        args,
        audit_gate,
        timeline_gate,
    )
    refreshed_inventory = canonical_render_source_inventory(
        memory_root,
        selected,
        set(audit_gate["render_source_paths"]),
    )
    inventory_mismatch = compare_source_inventories(
        audit_gate["render_source_inventory"],
        refreshed_inventory,
    )
    if inventory_mismatch:
        return {
            "ok": False,
            "status": "blocked",
            "error": f"roadmap sources changed during rendering: {inventory_mismatch}",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "audit_path": audit_gate["audit_path"],
            "recommended_workflows": ["adp-state-audit"],
        }
    timeline_mismatch = verify_canonical_timeline_sources(timeline_gate)
    if timeline_mismatch:
        return {
            "ok": False,
            "status": "blocked",
            "error": f"canonical roadmap sources changed during rendering: {timeline_mismatch}",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "audit_path": audit_gate["audit_path"],
            "recommended_workflows": ["adp-state-audit", "adp-program-status"],
        }
    roadmap = build["roadmap"]
    changes, diff_warning = diff_previous(previous, roadmap)
    roadmap["changed_since_last_roadmap"] = changes
    markdown = render_markdown(roadmap)

    outputs = {
        "json": str(output_dir / "roadmap.json"),
        "markdown": str(output_dir / "roadmap.md"),
    }
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "roadmap.json").write_text(json.dumps(roadmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        (output_dir / "roadmap.md").write_text(markdown, encoding="utf-8", newline="\n")

    result = {
        "ok": True,
        "status": (
            "blocked"
            if audit_gate["gate_status"] == "blocked"
            else ("warning" if roadmap["risk_bearing"] else "complete")
        ),
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "as_of": as_of.isoformat(),
        "audit_path": audit_gate["audit_path"],
        "audit_status": audit_gate["audit_status"],
        "report_confidence": audit_gate["report_confidence"],
        "risk_bearing": roadmap["risk_bearing"],
        "counts": roadmap["counts"],
        "recommended_workflows": merge_recommendations(
            recommended_workflows(roadmap), audit_gate.get("recommended_workflows", [])
        ),
        "warnings": compact(
            audit_gate.get("warnings", [])
            + build["warnings"]
            + [previous_warning or "", diff_warning or ""]
        ),
    }
    if args.dry_run:
        result["would_write"] = outputs
        result["preview"] = {"roadmap": roadmap, "markdown": markdown}
    else:
        result["outputs"] = outputs
    return result


def load_or_run_audit_gate(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    selected_workstreams: set[str],
    effective_workstreams: set[str],
    as_of: date,
) -> dict[str, Any]:
    prepass_path = resolve_input_path(project_root, args.prepass_json) if args.prepass_json else None
    if args.audit:
        audit_path = resolve_input_path(project_root, args.audit)
    else:
        audit_run = run_roadmap_audit(args, project_root, memory_root, as_of)
        if not audit_run.get("ok"):
            return {
                "ok": False,
                "error": audit_run.get("error", "adp-state-audit failed"),
                "recommended_workflows": audit_run.get("recommended_workflows", ["adp-state-audit"]),
            }
        audit_path = Path(audit_run.get("outputs", {}).get("json", ""))

    if not audit_path.is_absolute():
        audit_path = (project_root / audit_path).resolve()
    else:
        audit_path = audit_path.resolve()
    if not audit_path.exists() or not audit_path.is_file():
        return {
            "ok": False,
            "error": f"roadmap audit JSON does not exist: {audit_path}",
            "audit_path": str(audit_path),
            "recommended_workflows": ["adp-state-audit"],
        }
    try:
        payload = json.loads(read_text(audit_path))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"roadmap audit JSON is invalid: {exc}",
            "audit_path": str(audit_path),
            "recommended_workflows": ["adp-state-audit"],
        }
    return validate_audit_gate(
        payload,
        audit_path,
        memory_root,
        selected_workstreams,
        effective_workstreams,
        as_of,
        prepass_path,
    )


def run_roadmap_audit(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    as_of: date,
) -> dict[str, Any]:
    audit_script = Path(__file__).resolve().parents[2] / "adp-state-audit" / "scripts" / "audit_state.py"
    if not audit_script.exists():
        return {
            "ok": False,
            "error": f"adp-state-audit script not found: {audit_script}",
            "recommended_workflows": ["adp-state-audit"],
        }
    command = [
        sys.executable,
        str(audit_script),
        str(project_root),
        "--scenario",
        "roadmap",
        "--memory-root",
        str(memory_root),
        "--as-of",
        as_of.isoformat(),
    ]
    if args.prepass_json:
        command.extend(["--prepass-json", str(resolve_input_path(project_root, args.prepass_json))])
    for workstream in args.workstream:
        command.extend(["--workstream", workstream])
    completed = subprocess.run(command, capture_output=True)
    stdout = decode_process_output(completed.stdout)
    stderr = decode_process_output(completed.stderr)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": (stderr or stdout or "adp-state-audit emitted invalid JSON").strip(),
            "recommended_workflows": ["adp-state-audit"],
        }
    if completed.returncode != 0 or not payload.get("ok"):
        payload.setdefault("ok", False)
        payload.setdefault("error", stderr.strip() or "adp-state-audit failed")
    return payload


def validate_audit_gate(
    payload: Any,
    audit_path: Path,
    memory_root: Path,
    selected_workstreams: set[str],
    effective_workstreams: set[str],
    as_of: date,
    prepass_path: Path | None = None,
) -> dict[str, Any]:
    def invalid(reason: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"roadmap audit is incompatible: {reason}",
            "audit_path": str(audit_path),
            "recommended_workflows": ["adp-state-audit"],
        }

    if not isinstance(payload, dict):
        return invalid("root must be a JSON object")
    if payload.get("ok") is not True:
        return invalid("ok must be the boolean true")
    if payload.get("audit_schema_version") != 1 or payload.get("schema_version") != 1:
        return invalid("audit schema version fields audit_schema_version and schema_version must both be 1")
    if payload.get("scenario") != "roadmap":
        return invalid("scenario must be roadmap")
    raw_memory_root = payload.get("memory_root")
    if not isinstance(raw_memory_root, str) or not raw_memory_root.strip():
        return invalid("memory_root is missing")
    if Path(raw_memory_root).resolve() != memory_root.resolve():
        return invalid("memory_root does not match the roadmap memory root")
    prepass = payload.get("prepass")
    if not isinstance(prepass, dict):
        return invalid("prepass must be an object")
    if payload.get("prepass_schema_version") != PREPASS_SCHEMA_VERSION:
        return invalid(f"prepass_schema_version must be {PREPASS_SCHEMA_VERSION}")
    if prepass.get("schema_version") != PREPASS_SCHEMA_VERSION:
        return invalid(f"prepass.schema_version must be {PREPASS_SCHEMA_VERSION}")
    if normalize_capability(prepass.get("capability")) != ROADMAP_CAPABILITY:
        return invalid(f"prepass.capability must be {ROADMAP_CAPABILITY!r}")
    scope = prepass.get("scope")
    if not isinstance(scope, dict):
        return invalid("prepass.scope must be an object")
    if not isinstance(scope.get("groups_scanned"), list) or any(
        not isinstance(group, str) or not clean(group) for group in scope.get("groups_scanned", [])
    ):
        return invalid("prepass.scope.groups_scanned must be an array of names")
    if not isinstance(scope.get("max_age_days"), int) or scope["max_age_days"] < 0:
        return invalid("prepass.scope.max_age_days must be a non-negative integer")
    if not isinstance(prepass.get("counts"), dict):
        return invalid("prepass.counts must be an object")
    requested_scope = normalized_id_array(scope.get("workstreams_requested"))
    if requested_scope is None:
        return invalid("prepass.scope.workstreams_requested must be an array of workstream ids")
    if requested_scope != selected_workstreams:
        return invalid(
            "prepass.scope.workstreams_requested does not match the render scope "
            f"(audit={sorted(requested_scope)}, render={sorted(selected_workstreams)})"
        )
    if scope.get("as_of") != as_of.isoformat():
        return invalid(
            "prepass.scope.as_of does not match the render as_of "
            f"(audit={scope.get('as_of')!r}, render={as_of.isoformat()!r})"
        )
    source_inventory = payload.get("source_inventory")
    if not isinstance(source_inventory, dict):
        return invalid("source_inventory must be an object")
    audited_workstreams = normalized_id_array(source_inventory.get("workstreams"))
    if audited_workstreams is None:
        return invalid("source_inventory.workstreams must be an array of workstream ids")
    if audited_workstreams != effective_workstreams:
        return invalid(
            "source_inventory.workstreams does not match the effective render scope "
            f"(audit={sorted(audited_workstreams)}, render={sorted(effective_workstreams)})"
        )
    audited_inventory, inventory_error = canonical_audit_inventory(source_inventory)
    if inventory_error:
        return invalid(inventory_error)
    inventory_items_error = validate_source_inventory_items(
        payload.get("source_inventory_items"),
        audited_inventory,
    )
    if inventory_items_error:
        return invalid(inventory_items_error)
    audited_render_paths = {path for path in audited_inventory if is_render_source_path(path)}
    current_inventory = canonical_render_source_inventory(
        memory_root,
        selected_workstreams,
        audited_render_paths,
    )
    audited_render_inventory = {
        path: fingerprint
        for path, fingerprint in audited_inventory.items()
        if is_render_source_path(path)
    }
    mismatch = compare_source_inventories(audited_render_inventory, current_inventory)
    if mismatch:
        return invalid(f"render source inventory does not match current sources: {mismatch}")
    if prepass_path is not None:
        prepass_error, prepass_identity = validate_prepass_identity(
            prepass_path,
            payload,
            memory_root,
        )
        if prepass_error:
            return invalid(prepass_error)
    else:
        prepass_identity = audit_prepass_identity(payload)
    generated_at = parse_audit_datetime(payload.get("generated_at"))
    if generated_at is None:
        return invalid("generated_at must be a timezone-aware ISO-8601 timestamp")
    age = datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
    if age > AUDIT_MAX_AGE:
        return invalid("audit is stale (older than 24 hours)")
    if age < -AUDIT_FUTURE_TOLERANCE:
        return invalid("generated_at is more than 5 minutes in the future")

    audit_status = clean(payload.get("audit_status")).lower()
    if audit_status not in AUDIT_STATUSES:
        return invalid("audit_status must be pass, warning, or blocked")
    for field_name in ["safe_to_generate", "safe_to_generate_green_report"]:
        if not isinstance(payload.get(field_name), bool):
            return invalid(f"{field_name} must be a boolean")
    if payload.get("safe_to_generate") is False:
        return invalid("safe_to_generate is false")
    report_confidence = clean(payload.get("report_confidence")).lower()
    if report_confidence not in VALID_CONFIDENCE:
        return invalid("report_confidence must explicitly be high, medium, or low")
    input_audit_id = clean(payload.get("input_audit_id"))
    if not input_audit_id:
        return invalid("input_audit_id is missing")
    baseline_revision = payload.get("baseline_revision")
    if not isinstance(baseline_revision, int) or baseline_revision < 1:
        return invalid("baseline_revision must be a positive integer")
    locale = clean(payload.get("locale"))
    if not locale:
        return invalid("locale is missing")
    if not isinstance(payload.get("locale_fallback"), bool):
        return invalid("locale_fallback must be a boolean")
    if not isinstance(payload.get("source_fingerprints"), dict) or not payload["source_fingerprints"]:
        return invalid("source_fingerprints must be a non-empty object")
    recommendations = payload.get("recommended_workflows", [])
    if not isinstance(recommendations, list):
        return invalid("recommended_workflows must be an array")
    green_report = payload["safe_to_generate_green_report"]
    risk_bearing = audit_status != "pass" or not green_report
    gate_status = "blocked" if audit_status == "blocked" else ("warning" if risk_bearing else "pass")
    warnings = []
    if risk_bearing:
        warnings.append(f"roadmap is risk-bearing because audit status is {audit_status}")
    return {
        "ok": True,
        "audit_path": str(audit_path),
        "audit_status": audit_status,
        "gate_status": gate_status,
        "report_confidence": report_confidence,
        "input_audit_id": input_audit_id,
        "baseline_revision": baseline_revision,
        "scenario": "roadmap",
        "locale": locale,
        "locale_fallback": payload["locale_fallback"],
        "source_fingerprints": dict(payload["source_fingerprints"]),
        "risk_bearing": risk_bearing,
        "warnings": warnings,
        "recommended_workflows": recommendations,
        "render_source_inventory": current_inventory,
        "render_source_paths": sorted(current_inventory),
        "prepass_identity": prepass_identity,
    }


def normalize_capability(value: Any) -> str:
    return " ".join(clean(value).lower().split())


def normalize_source_path(value: Any) -> str:
    return clean(value).replace("\\", "/").removeprefix("./")


def is_render_source_path(path: str) -> bool:
    normalized = normalize_source_path(path)
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in RENDER_SOURCE_PATTERNS)


def canonical_audit_inventory(
    source_inventory: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    sources_read = source_inventory.get("sources_read")
    missing_sources = source_inventory.get("missing_sources")
    if not isinstance(sources_read, list):
        return {}, "source_inventory.sources_read must be an array"
    if not isinstance(missing_sources, list) or any(not isinstance(path, str) for path in missing_sources):
        return {}, "source_inventory.missing_sources must be an array of paths"
    inventory: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources_read):
        if not isinstance(source, dict):
            return {}, f"source_inventory.sources_read[{index}] must be an object"
        path = normalize_source_path(source.get("path"))
        byte_count = source.get("bytes")
        modified = source.get("modified")
        modified_ns = source.get("modified_ns")
        if (
            not path
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not isinstance(modified, str)
            or not modified
            or not isinstance(modified_ns, int)
            or modified_ns < 0
        ):
            return {}, (
                f"source_inventory.sources_read[{index}] requires path, non-negative bytes, modified, and modified_ns"
            )
        if path in inventory:
            return {}, f"source_inventory contains duplicate path {path!r}"
        inventory[path] = {
            "path": path,
            "bytes": byte_count,
            "modified": modified,
            "modified_ns": modified_ns,
            "status": "read",
        }
    for raw_path in missing_sources:
        path = normalize_source_path(raw_path)
        if not path:
            return {}, "source_inventory.missing_sources contains an empty path"
        if path in inventory:
            return {}, f"source_inventory marks {path!r} as both read and missing"
        inventory[path] = {
            "path": path,
            "bytes": None,
            "modified": "",
            "modified_ns": None,
            "status": "missing",
        }
    return inventory, None


def validate_source_inventory_items(
    raw_items: Any,
    inventory: dict[str, dict[str, Any]],
) -> str | None:
    if not isinstance(raw_items, list):
        return "source_inventory_items must be an array"
    actual: dict[str, dict[str, str]] = {}
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            return f"source_inventory_items[{index}] must be an object"
        path = normalize_source_path(item.get("path"))
        status = item.get("status")
        modified = item.get("modified")
        kind = item.get("kind")
        if (
            not path
            or status not in {"read", "missing"}
            or not isinstance(modified, str)
            or not isinstance(kind, str)
            or not clean(kind)
        ):
            return f"source_inventory_items[{index}] requires path, kind, status, and modified"
        if path in actual:
            return f"source_inventory_items contains duplicate path {path!r}"
        actual[path] = {"status": status, "modified": modified}
    expected = {
        path: {"status": item["status"], "modified": item["modified"]}
        for path, item in inventory.items()
    }
    if actual != expected:
        return "source_inventory_items does not match source_inventory sources_read/missing_sources"
    return None


def canonical_render_source_inventory(
    memory_root: Path,
    selected_workstreams: set[str],
    audited_paths: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    paths = {rel for rel in FIXED_RENDER_SOURCES}
    paths.update(rel_to_memory(memory_root, path) for path in discover_wdrs(memory_root, selected_workstreams))
    paths.update(
        rel_to_memory(memory_root, path)
        for path in (memory_root / "intake" / "bmm-checkpoints" / "candidates").glob("CHK-*.json")
    )
    paths.update(
        rel_to_memory(memory_root, path)
        for path in (memory_root / "decisions" / "business-decision-packets").glob("*.md")
    )
    paths.update(
        path
        for path in (audited_paths or set())
        if is_render_source_path(path)
        and (
            not selected_workstreams
            or not fnmatch.fnmatchcase(path, "workstreams/*/delivery-record.md")
            or normalize_id(Path(path).parent.name) in selected_workstreams
        )
    )
    inventory: dict[str, dict[str, Any]] = {}
    for rel in sorted(paths):
        path = memory_root / rel
        if path.exists() and path.is_file():
            item = file_item(path, memory_root)
            inventory[rel] = {**item, "status": "read"}
        else:
            inventory[rel] = {
                "path": rel,
                "bytes": None,
                "modified": "",
                "modified_ns": None,
                "status": "missing",
            }
    return inventory


def compare_source_inventories(
    audited: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> str | None:
    if audited == current:
        return None
    audited_paths = set(audited)
    current_paths = set(current)
    parts = []
    if current_paths - audited_paths:
        parts.append(f"not audited={sorted(current_paths - audited_paths)}")
    if audited_paths - current_paths:
        parts.append(f"no longer required={sorted(audited_paths - current_paths)}")
    changed = sorted(path for path in audited_paths & current_paths if audited[path] != current[path])
    if changed:
        parts.append(f"changed={changed}")
    return "; ".join(parts) or "inventory mismatch"


def audit_prepass_identity(payload: dict[str, Any]) -> str:
    prepass = payload["prepass"]
    identity = {
        "schema_version": payload["prepass_schema_version"],
        "capability": normalize_capability(prepass.get("capability")),
        "memory_root": str(Path(payload["memory_root"]).resolve()),
        "scope": prepass.get("scope"),
        "counts": prepass.get("counts"),
        "sources_read": payload["source_inventory"]["sources_read"],
        "missing_sources": payload["source_inventory"]["missing_sources"],
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_prepass_identity(
    prepass_path: Path,
    audit_payload: dict[str, Any],
    memory_root: Path,
) -> tuple[str | None, str]:
    if not prepass_path.exists() or not prepass_path.is_file():
        return f"supplied prepass JSON does not exist: {prepass_path}", ""
    try:
        prepass = json.loads(read_text(prepass_path))
    except (OSError, json.JSONDecodeError) as exc:
        return f"supplied prepass JSON is invalid: {exc}", ""
    if not isinstance(prepass, dict) or prepass.get("ok") is not True:
        return "supplied prepass must be an object with ok=true", ""
    if prepass.get("schema_version") != PREPASS_SCHEMA_VERSION:
        return f"supplied prepass schema_version must be {PREPASS_SCHEMA_VERSION}", ""
    if normalize_capability(prepass.get("capability")) != ROADMAP_CAPABILITY:
        return f"supplied prepass capability must be {ROADMAP_CAPABILITY!r}", ""
    raw_root = prepass.get("memory_root")
    if not isinstance(raw_root, str) or Path(raw_root).resolve() != memory_root.resolve():
        return "supplied prepass memory_root does not match the roadmap memory root", ""
    supplied_inventory, error = canonical_audit_inventory(
        {
            "sources_read": prepass.get("sources_read"),
            "missing_sources": prepass.get("missing_sources"),
        }
    )
    if error:
        return f"supplied prepass {error}", ""
    audited_inventory, _ = canonical_audit_inventory(audit_payload["source_inventory"])
    audit_prepass = audit_payload["prepass"]
    if prepass.get("scope") != audit_prepass.get("scope"):
        return "supplied prepass scope does not match the audit prepass", ""
    if prepass.get("counts") != audit_prepass.get("counts"):
        return "supplied prepass counts do not match the audit prepass", ""
    if supplied_inventory != audited_inventory:
        return "supplied prepass source inventory does not match the audit inventory", ""
    identity = audit_prepass_identity(audit_payload)
    supplied_identity_payload = {
        "schema_version": prepass["schema_version"],
        "capability": normalize_capability(prepass.get("capability")),
        "memory_root": str(Path(prepass["memory_root"]).resolve()),
        "scope": prepass.get("scope"),
        "counts": prepass.get("counts"),
        "sources_read": prepass.get("sources_read"),
        "missing_sources": prepass.get("missing_sources"),
    }
    encoded = json.dumps(supplied_identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    supplied_identity = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if supplied_identity != identity:
        return "supplied prepass identity does not match the audit prepass identity", ""
    return None, identity


def parse_audit_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(clean(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def load_canonical_timeline_inputs(project_root: Path, memory_root: Path, as_of: date) -> dict[str, Any]:
    baseline_path = memory_root / "plans" / "program-baseline.md"
    if not baseline_path.is_file():
        return {"ok": False, "error": "approved program baseline is missing", "recommended_workflows": ["adp-plan-baseline"]}
    try:
        baseline = parse_baseline_document(baseline_path)
        validate_baseline_for_roadmap(baseline)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"program baseline is invalid: {exc}", "recommended_workflows": ["adp-plan-baseline"]}

    status_path = memory_root / "views" / "program-status.json"
    if not status_path.is_file():
        return {"ok": False, "error": "canonical program status is missing", "recommended_workflows": ["adp-program-status"]}
    try:
        program_status = json.loads(read_text(status_path))
        validate_program_status_for_roadmap(program_status, baseline, as_of, project_root, memory_root, baseline_path)
        snapshot_path = memory_root / "snapshots" / "program-status" / f"{program_status['snapshot_id']}.json"
        if not snapshot_path.is_file():
            raise ValueError(f"immutable program-status snapshot is missing: {snapshot_path}")
        if json.loads(read_text(snapshot_path)) != program_status:
            raise ValueError("program-status view does not match its immutable snapshot")
        baseline_changes = build_baseline_revision_diff(memory_root, baseline_path, baseline)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        return {
            "ok": False,
            "error": f"canonical program status is incompatible: {exc}",
            "recommended_workflows": ["adp-program-status", "adp-state-audit"],
        }

    source_paths = [baseline_path, status_path, snapshot_path]
    if baseline_changes.get("from_path"):
        source_paths.append(memory_root / baseline_changes["from_path"])
    return {
        "ok": True,
        "baseline": baseline,
        "program_status": program_status,
        "baseline_changes": baseline_changes,
        "baseline_path": baseline_path,
        "program_status_path": status_path,
        "snapshot_path": snapshot_path,
        "source_fingerprints": {str(path.resolve()): file_sha256(path) for path in source_paths},
    }


def parse_baseline_document(path: Path) -> dict[str, Any]:
    text = read_text(path)
    marker_index = text.find(BASELINE_MARKER)
    if marker_index < 0:
        raise ValueError(f"missing marker {BASELINE_MARKER}")
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text[marker_index:], flags=re.DOTALL)
    if not fenced:
        raise ValueError("missing canonical JSON block after baseline marker")
    payload = json.loads(fenced.group(1))
    if not isinstance(payload, dict):
        raise ValueError("canonical baseline JSON must be an object")
    return payload


def validate_baseline_for_roadmap(baseline: dict[str, Any]) -> None:
    revision = baseline.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("baseline revision must be a positive integer")
    if baseline.get("confirmation_status") not in {"confirmed", "approved"}:
        raise ValueError("baseline confirmation_status must be confirmed or approved")
    if not clean(baseline.get("baseline_id")):
        raise ValueError("baseline_id is required")
    project = baseline.get("project")
    if not isinstance(project, dict):
        raise ValueError("baseline project must be an object")
    validate_baseline_item(project, "project", revision, require_revision=False)
    seen: set[str] = set()
    for collection, require_workstream in [("gates", False), ("milestones", True)]:
        rows = baseline.get(collection)
        if not isinstance(rows, list):
            raise ValueError(f"baseline {collection} must be an array")
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                raise ValueError(f"baseline {collection}[{index}] must be an object")
            validate_baseline_item(item, f"{collection}[{index}]", revision, require_revision=True)
            item_id = clean(item.get("id"))
            if item_id in seen:
                raise ValueError(f"duplicate baseline constraint id {item_id!r}")
            seen.add(item_id)
            if require_workstream and not clean(item.get("workstream_id")):
                raise ValueError(f"baseline {collection}[{index}].workstream_id is required")


def validate_baseline_item(item: dict[str, Any], label: str, revision: int, *, require_revision: bool) -> None:
    planned = item.get("target_date") if label == "project" else item.get("planned_date")
    if not isinstance(planned, str) or parse_date(planned) is None:
        raise ValueError(f"baseline {label} planned date must be YYYY-MM-DD")
    if label != "project" and (not clean(item.get("id")) or not clean(item.get("name"))):
        raise ValueError(f"baseline {label} requires id and name")
    if not clean(item.get("owner")):
        raise ValueError(f"baseline {label}.owner is required")
    if require_revision and item.get("baseline_revision") != revision:
        raise ValueError(f"baseline {label}.baseline_revision does not match revision {revision}")
    source = item.get("source")
    if not isinstance(source, dict) or not clean(source.get("type")) or not clean(source.get("reference")):
        raise ValueError(f"baseline {label}.source requires type and reference")


def validate_program_status_for_roadmap(
    status: Any,
    baseline: dict[str, Any],
    as_of: date,
    project_root: Path,
    memory_root: Path,
    baseline_path: Path,
) -> None:
    if not isinstance(status, dict) or status.get("schema_version") != "1.0":
        raise ValueError("program status schema_version must be '1.0'")
    if status.get("baseline_id") != baseline.get("baseline_id"):
        raise ValueError("program status baseline id does not match the approved baseline")
    if status.get("baseline_revision") != baseline.get("revision"):
        raise ValueError("program status baseline revision does not match the approved baseline")
    if status.get("as_of") != as_of.isoformat():
        raise ValueError(
            f"program status as_of {status.get('as_of')!r} does not match roadmap as_of {as_of.isoformat()!r}"
        )
    if status.get("overall_status") not in PROGRAM_STATUSES:
        raise ValueError("program status overall_status is invalid")
    if status.get("report_confidence") not in PROGRAM_CONFIDENCE:
        raise ValueError("program status report_confidence is invalid")
    for key in ["snapshot_id", "input_audit_id", "generator_version", "locale", "overall_rule_id"]:
        if not clean(status.get(key)):
            raise ValueError(f"program status {key} is required")
    generated_at = parse_audit_datetime(status.get("generated_at"))
    if generated_at is None:
        raise ValueError("program status generated_at must be a timezone-aware ISO-8601 timestamp")
    period = status.get("reporting_period")
    if not isinstance(period, dict) or parse_date(str(period.get("start", ""))) is None or parse_date(str(period.get("end", ""))) is None:
        raise ValueError("program status reporting_period requires ISO start and end dates")
    for key in ["source_inventory", "rule_ids", "critical_path", "variances"]:
        if not isinstance(status.get(key), list):
            raise ValueError(f"program status {key} must be an array")
    if not isinstance(status.get("period_delta"), dict):
        raise ValueError("program status period_delta must be an object")
    validate_canonical_progress(status.get("progress"), status, baseline)
    fingerprints = status.get("source_fingerprints")
    if not isinstance(fingerprints, dict):
        raise ValueError("program status source_fingerprints must be an object")
    baseline_hashes = [
        clean(value).removeprefix("sha256:")
        for path, value in fingerprints.items()
        if normalize_source_path(path).endswith("plans/program-baseline.md")
    ]
    if baseline_hashes != [file_sha256(baseline_path)]:
        raise ValueError("program status baseline fingerprint does not match the approved baseline")
    for raw_path, raw_fingerprint in fingerprints.items():
        source_path = Path(str(raw_path))
        if not source_path.is_absolute():
            source_path = project_root / source_path
            if not source_path.is_file():
                memory_source = memory_root / str(raw_path)
                if memory_source.is_file():
                    source_path = memory_source
        if not source_path.is_file():
            raise ValueError(f"program status source is missing: {raw_path}")
        expected = clean(raw_fingerprint).removeprefix("sha256:")
        if file_sha256(source_path) != expected:
            raise ValueError(f"program status source fingerprint is stale: {raw_path}")

    expected = {
        "milestones": {str(item["id"]): item for item in baseline.get("milestones", [])},
        "gates": {str(item["id"]): item for item in baseline.get("gates", [])},
    }
    for collection in ["milestones", "gates"]:
        rows = status.get(collection)
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise ValueError(f"program status {collection} must be an array of objects")
        indexed = {str(item.get("id")): item for item in rows}
        if len(indexed) != len(rows) or set(indexed) != set(expected[collection]):
            raise ValueError(f"program status {collection} do not match baseline constraint ids")
        for item_id, item in indexed.items():
            validate_status_constraint(item, expected[collection][item_id], collection)
    project = status.get("project")
    target = project.get("target_assessment") if isinstance(project, dict) else None
    if not isinstance(target, dict) or target.get("id") != "PROJECT-TARGET":
        raise ValueError("program status project.target_assessment is required")
    if target.get("planned_date") != baseline["project"]["target_date"]:
        raise ValueError("program status project target planned date differs from baseline")
    validate_status_fields(target, "project target")


def validate_canonical_progress(progress: Any, status: dict[str, Any], baseline: dict[str, Any]) -> None:
    if not isinstance(progress, dict) or progress.get("progress_schema_version") != PROGRESS_SCHEMA_VERSION:
        raise ValueError(f"{PROGRESS_MIGRATION_ERROR}: canonical progress schema {PROGRESS_SCHEMA_VERSION} is required")
    required = {"basis", "as_of", "reporting_period", "scope_identity", "measurement_status", "overall", "by_workstream", "eligibility", "compatibility", "recovery"}
    missing = sorted(required - set(progress))
    if missing:
        raise ValueError("canonical progress is missing: " + ", ".join(missing))
    if progress.get("basis") != "weighted-milestone":
        raise ValueError("canonical progress basis must be weighted-milestone")
    if progress.get("as_of") != status.get("as_of"):
        raise ValueError("canonical progress as_of does not match program status")
    identity = progress.get("scope_identity")
    if not isinstance(identity, dict) or identity.get("baseline_revision") != baseline.get("revision"):
        raise ValueError("canonical progress baseline revision does not match the approved baseline")
    overall = progress.get("overall")
    if not isinstance(overall, dict) or not isinstance(overall.get("current"), dict):
        raise ValueError("canonical progress overall.current is required")
    if not isinstance(progress.get("by_workstream"), list):
        raise ValueError("canonical progress by_workstream must be an array")
    current = overall["current"]
    if progress.get("measurement_status") == "measurable" and progress.get("weighted_completion_percent") != current.get("actual_completion_percent"):
        raise ValueError("canonical progress legacy alias does not match overall actual completion")


def validate_status_constraint(item: dict[str, Any], baseline_item: dict[str, Any], collection: str) -> None:
    if item.get("planned_date") != baseline_item.get("planned_date"):
        raise ValueError(f"program status {collection} {item.get('id')} planned date differs from baseline")
    if collection == "milestones" and item.get("workstream_id") != baseline_item.get("workstream_id"):
        raise ValueError(f"program status milestone {item.get('id')} workstream differs from baseline")
    validate_status_fields(item, f"{collection} {item.get('id')}")


def validate_status_fields(item: dict[str, Any], label: str) -> None:
    if item.get("status") not in PROGRAM_STATUSES:
        raise ValueError(f"program status {label} status is invalid")
    if not clean(item.get("rule_id")):
        raise ValueError(f"program status {label} rule_id is required")
    for key in ["forecast_date", "actual_date"]:
        value = item.get(key)
        if value is not None and (not isinstance(value, str) or parse_date(value) is None):
            raise ValueError(f"program status {label} {key} must be null or YYYY-MM-DD")
    variance = item.get("variance_days")
    if variance is not None and (not isinstance(variance, int) or isinstance(variance, bool)):
        raise ValueError(f"program status {label} variance_days must be an integer or null")
    refs = item.get("source_references")
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not clean(ref) for ref in refs):
        raise ValueError(f"program status {label} source_references must be an array of references")


def build_baseline_revision_diff(memory_root: Path, baseline_path: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    revision = int(baseline["revision"])
    current_rel = rel_to_memory(memory_root, baseline_path)
    current_fingerprint = "sha256:" + file_sha256(baseline_path)
    if revision == 1:
        return {
            "status": "initial-baseline", "from_revision": None, "to_revision": 1,
            "from_path": None, "to_path": current_rel, "from_fingerprint": None,
            "to_fingerprint": current_fingerprint, "changes": [],
        }
    previous_path = memory_root / "plans" / "baseline-history" / f"program-baseline-r{revision - 1}.md"
    if not previous_path.is_file():
        raise ValueError(f"archived baseline revision {revision - 1} is missing")
    previous = parse_baseline_document(previous_path)
    validate_baseline_for_roadmap(previous)
    if previous.get("revision") != revision - 1:
        raise ValueError(f"archived baseline does not contain revision {revision - 1}")
    if previous.get("baseline_id") != baseline.get("baseline_id"):
        raise ValueError("archived baseline baseline_id does not match the current baseline")
    return {
        "status": "compared", "from_revision": revision - 1, "to_revision": revision,
        "from_path": rel_to_memory(memory_root, previous_path), "to_path": current_rel,
        "from_fingerprint": "sha256:" + file_sha256(previous_path),
        "to_fingerprint": current_fingerprint,
        "changes": diff_baseline_constraints(previous, baseline),
    }


def diff_baseline_constraints(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    def indexed(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result = {
            "baseline:ROOT": {
                "id": "BASELINE",
                "kind": "baseline",
                "default_tolerance_days": model.get("default_tolerance_days"),
                "critical_path": model.get("critical_path"),
                "weighting": model.get("weighting"),
            },
            "project:PROJECT-TARGET": {"id": "PROJECT-TARGET", "kind": "project", **model["project"]},
        }
        for collection, kind in [("gates", "gate"), ("milestones", "milestone")]:
            for item in model.get(collection, []):
                result[f"{kind}:{item['id']}"] = {"kind": kind, **item}
        return result

    before, after = indexed(previous), indexed(current)
    changes: list[dict[str, Any]] = []
    for key in sorted(set(before) | set(after)):
        if key not in before:
            changes.append({"change": "added", "id": after[key]["id"], "kind": after[key]["kind"], "fields": []})
        elif key not in after:
            changes.append({"change": "removed", "id": before[key]["id"], "kind": before[key]["kind"], "fields": []})
        else:
            fields = sorted(
                name for name in set(before[key]) | set(after[key])
                if name != "baseline_revision" and before[key].get(name) != after[key].get(name)
            )
            if fields:
                changes.append({"change": "updated", "id": after[key]["id"], "kind": after[key]["kind"], "fields": fields})
    return changes


def canonical_timeline_items(baseline: dict[str, Any], program_status: dict[str, Any], selected: set[str]) -> list[RoadmapItem]:
    constraints: list[tuple[str, dict[str, Any], dict[str, Any], list[str]]] = [
        ("project-target", baseline["project"], program_status["project"]["target_assessment"], [])
    ]
    status_gates = {str(item["id"]): item for item in program_status["gates"]}
    constraints.extend(("gate", item, status_gates[str(item["id"])], []) for item in baseline["gates"])
    status_milestones = {str(item["id"]): item for item in program_status["milestones"]}
    constraints.extend(
        ("milestone", item, status_milestones[str(item["id"])], [normalize_id(item["workstream_id"])])
        for item in baseline["milestones"]
        if not selected or normalize_id(item["workstream_id"]) in selected
    )
    snapshot_id = program_status["snapshot_id"]
    result: list[RoadmapItem] = []
    for kind, plan, status, workstreams in constraints:
        item_id = "PROJECT-TARGET" if kind == "project-target" else str(plan["id"])
        planned = plan.get("target_date") if kind == "project-target" else plan["planned_date"]
        source = plan["source"]
        status_ref = f"snapshots/program-status/{snapshot_id}.json#{item_id}"
        forecast, actual = status.get("forecast_date") or "TBD", status.get("actual_date") or "TBD"
        result.append(RoadmapItem(
            id=item_id,
            milestone=str(plan.get("name") or baseline["project"]["name"]),
            type=kind,
            status=status["status"],
            planned=str(planned),
            forecast=str(forecast),
            actual=str(actual),
            owner=str(plan["owner"]),
            confidence=program_status["report_confidence"],
            depends_on=", ".join(str(value) for value in plan.get("dependencies", [])) or "TBD",
            source=str(source["reference"]),
            source_type="program-baseline",
            workstreams=workstreams,
            variance_days=status.get("variance_days"),
            baseline_revision=int(baseline["revision"]),
            planned_source=str(source["reference"]),
            forecast_source=status_ref if forecast != "TBD" else "TBD",
            actual_source=status_ref if actual != "TBD" else "TBD",
            status_source=status_ref,
            status_rule_id=str(status["rule_id"]),
            source_references=list(status.get("source_references", [])),
        ))
    return result


def verify_canonical_timeline_sources(timeline_gate: dict[str, Any]) -> str | None:
    for raw_path, expected in timeline_gate["source_fingerprints"].items():
        path = Path(raw_path)
        if not path.is_file():
            return f"source disappeared: {path}"
        if file_sha256(path) != expected:
            return f"source changed: {path}"
    return None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_roadmap(
    project_root: Path,
    memory_root: Path,
    selected_workstreams: set[str],
    as_of: date,
    args: argparse.Namespace,
    audit_gate: dict[str, Any],
    timeline_gate: dict[str, Any],
) -> dict[str, Any]:
    items: list[RoadmapItem] = []
    excluded: list[ExcludedItem] = []
    warnings: list[str] = []
    sources: list[dict[str, Any]] = []

    baseline = timeline_gate["baseline"]
    program_status = timeline_gate["program_status"]
    canonical_items = canonical_timeline_items(baseline, program_status, selected_workstreams)
    baseline_ids = {item.id for item in canonical_items}
    for path in [
        timeline_gate["baseline_path"],
        timeline_gate["program_status_path"],
        timeline_gate["snapshot_path"],
    ]:
        sources.append(file_item(path, memory_root))
    if timeline_gate["baseline_changes"].get("from_path"):
        sources.append(file_item(memory_root / timeline_gate["baseline_changes"]["from_path"], memory_root))

    for record in discover_wdrs(memory_root, selected_workstreams):
        sources.append(file_item(record, memory_root))
        parsed_items, parsed_excluded = roadmap_items_from_wdr(memory_root, record)
        items.extend(parsed_items)
        excluded.extend(parsed_excluded)

    candidates, candidate_excluded, candidate_sources = roadmap_items_from_checkpoint_candidates(
        memory_root,
        selected_workstreams,
    )
    items.extend(candidates)
    excluded.extend(candidate_excluded)
    sources.extend(candidate_sources)

    decisions, decision_blocks, decision_excluded, decision_sources = roadmap_items_from_decisions(
        memory_root,
        selected_workstreams,
    )
    items.extend(decisions)
    excluded.extend(decision_excluded)
    sources.extend(decision_sources)

    readiness_items, readiness_excluded, readiness_sources = roadmap_items_from_readiness_views(
        memory_root,
        selected_workstreams,
    )
    items.extend(readiness_items)
    excluded.extend(readiness_excluded)
    sources.extend(readiness_sources)

    l0_items, l0_excluded, l0_sources = roadmap_items_from_l0(memory_root, selected_workstreams)
    items.extend(l0_items)
    excluded.extend(l0_excluded)
    sources.extend(l0_sources)

    dependency_excluded, dependency_sources = excluded_unstructured_dependencies(
        memory_root,
        selected_workstreams,
    )
    excluded.extend(dependency_excluded)
    sources.extend(dependency_sources)

    action_exclusions, action_sources = excluded_action_due_dates(memory_root, selected_workstreams)
    excluded.extend(action_exclusions)
    sources.extend(action_sources)

    sources.append(file_item(Path(audit_gate["audit_path"]), memory_root))
    if args.prepass_json:
        prepass_path = resolve_input_path(project_root, args.prepass_json)
        if prepass_path.exists():
            sources.append(file_item(prepass_path, memory_root))
        else:
            warnings.append(f"prepass source does not exist: {prepass_path}")

    unique_items, duplicate_excluded = dedupe_items(items)
    excluded.extend(duplicate_excluded)
    excluded = filter_excluded(excluded, selected_workstreams)
    warnings.extend(
        f"excluded {item.source_type} item {item.item!r}: {item.reason}"
        for item in excluded
        if item.source_type != "action-ledger" or item.risk
    )
    source_failures = [item for item in excluded if item.risk]
    supplemental = [item for item in unique_items if item.id not in baseline_ids]
    unscheduled = [item for item in supplemental if not has_any_date(item)]
    unmapped = [item for item in supplemental if has_any_date(item)]
    dated = canonical_items
    at_risk = [item for item in dated if item.status in {"at-risk", "off-plan"}]

    risk_bearing = audit_gate["risk_bearing"] or bool(source_failures)
    if source_failures:
        warnings.append(
            f"roadmap is risk-bearing because {len(source_failures)} source artifact(s) could not be trusted"
        )
    persisted_sources = fingerprint_sources(dedupe_sources(sources), memory_root)
    render_contract = build_render_contract(audit_gate["locale"])
    source_fingerprints = {
        str(path): normalize_sha256(fingerprint)
        for path, fingerprint in audit_gate["source_fingerprints"].items()
    }
    source_fingerprints.update(
        {item["path"]: item["fingerprint"] for item in persisted_sources if item.get("fingerprint")}
    )
    roadmap = {
        "ok": True,
        "schema_version": ROADMAP_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "reporting_period": program_status["reporting_period"],
        "scenario": audit_gate["scenario"],
        "input_audit_id": audit_gate["input_audit_id"],
        "locale": audit_gate["locale"],
        "locale_fallback": audit_gate["locale_fallback"],
        "render_contract": render_contract,
        "canonical_status_title": render_contract["localized_system_text"][0],
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "audit_path": audit_gate["audit_path"],
        "audit_status": audit_gate["audit_status"],
        "report_confidence": audit_gate["report_confidence"],
        "risk_bearing": risk_bearing,
        "report_status": "risk-bearing" if risk_bearing else "normal",
        "baseline_revision": baseline["revision"],
        "baseline_id": baseline["baseline_id"],
        "program_status_snapshot_id": program_status["snapshot_id"],
        "baseline_changes": timeline_gate["baseline_changes"],
        "program_status": {
            "snapshot_id": program_status["snapshot_id"],
            "as_of": program_status["as_of"],
            "overall_status": program_status["overall_status"],
            "report_confidence": program_status["report_confidence"],
            "input_audit_id": program_status["input_audit_id"],
            "generator_version": program_status["generator_version"],
            "progress_schema_version": program_status["progress"]["progress_schema_version"],
            "source": rel_to_memory(memory_root, timeline_gate["program_status_path"]),
        },
        "progress": program_status["progress"],
        "scope": {
            "kind": "workstreams" if selected_workstreams else "global",
            "selected_workstreams": sorted(selected_workstreams),
        },
        "source_inventory": {
            "sources_read": persisted_sources,
            "missing_sources": sorted(
                path
                for path, fingerprint in audit_gate["render_source_inventory"].items()
                if fingerprint["status"] == "missing"
            ),
            "selected_workstreams": sorted(selected_workstreams),
            "notes": [
                "Action due dates are follow-up context only and are not promoted to milestones.",
                "TBD dates mean the source did not provide a parseable date.",
            ],
        },
        "source_fingerprints": dict(sorted(source_fingerprints.items())),
        "milestone_timeline": [asdict(item) for item in sorted(dated, key=timeline_sort_key)],
        "unscheduled_milestones": [asdict(item) for item in sorted(unscheduled, key=item_sort_key)],
        "unmapped_items": [asdict(item) for item in sorted(unmapped, key=timeline_sort_key)],
        "at_risk_dates": [asdict(item) for item in sorted(at_risk, key=timeline_sort_key)],
        "blocked_by_decisions": decision_blocks,
        "changed_since_last_roadmap": [],
        "excluded_items": [asdict(item) for item in excluded],
        "counts": {
            "sources_read": len(persisted_sources),
            "milestone_timeline": len(dated),
            "unscheduled_milestones": len(unscheduled),
            "unmapped_items": len(unmapped),
            "at_risk_dates": len(at_risk),
            "blocked_by_decisions": len(decision_blocks),
            "excluded_items": len(excluded),
        },
    }
    return {"roadmap": roadmap, "warnings": warnings}


def build_render_contract(locale: str) -> dict[str, Any]:
    catalog = json.loads(LOCALE_CATALOG_PATH.read_text(encoding="utf-8"))
    selected = catalog.get(locale)
    if not isinstance(selected, dict):
        raise ValueError(f"shared locale catalog does not support roadmap audit locale {locale!r}")
    key = "status.title"
    localized = selected.get(key)
    if not isinstance(localized, str) or not localized:
        raise ValueError(f"shared locale catalog is missing {key!r} for {locale!r}")
    return {
        "catalog_locale": locale,
        "catalog_fingerprint": file_sha256(LOCALE_CATALOG_PATH),
        "message_keys": [key],
        "unresolved_message_keys": [],
        "source_fact_translation_persisted": False,
        "localized_system_text": [localized],
    }


def normalize_sha256(value: Any) -> str:
    raw = str(value or "").strip().lower()
    digest = raw.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("source fingerprint must be a SHA-256 digest")
    return f"sha256:{digest}"


def discover_wdrs(memory_root: Path, selected: set[str]) -> list[Path]:
    root = memory_root / "workstreams"
    if not root.exists():
        return []
    records = sorted(root.glob("*/delivery-record.md"))
    if not selected:
        return records
    return [path for path in records if discover_record_workstream_id(path) in selected]


def discover_workstream_ids(memory_root: Path) -> set[str]:
    root = memory_root / "workstreams"
    if not root.exists():
        return set()
    workstream_ids = {discover_record_workstream_id(path) for path in root.glob("*/delivery-record.md")}
    return {workstream_id for workstream_id in workstream_ids if workstream_id}


def discover_record_workstream_id(record: Path) -> str:
    try:
        return workstream_id_from_record(record, read_text(record))
    except OSError:
        return normalize_id(record.parent.name)


def roadmap_items_from_wdr(memory_root: Path, record: Path) -> tuple[list[RoadmapItem], list[ExcludedItem]]:
    source_path = rel_to_memory(memory_root, record)
    try:
        text = read_text(record)
    except OSError as exc:
        return [], [
            ExcludedItem(
                source=source_path,
                source_type="wdr-roadmap",
                item=record.name,
                reason=f"cannot read source: {exc}",
                code="source_read_error",
                risk=True,
                workstreams=[normalize_id(record.parent.name)],
            )
        ]
    workstream = workstream_id_from_record(record, text)
    roadmap_lines = section_lines(text, "Roadmap")
    table_diagnostics: list[str] = []
    rows = parse_first_table(
        roadmap_lines,
        table_diagnostics,
        required_headers=(("milestone", "event", "name"), ("type",), ("status",), ("source", "source link", "source anchor")),
    )
    items: list[RoadmapItem] = []
    excluded: list[ExcludedItem] = [
        ExcludedItem(
            source=source_path,
            source_type="wdr-roadmap",
            item="malformed roadmap table row",
            reason=f"malformed markdown table: {diagnostic}",
            code="table_schema_error",
            risk=True,
            workstreams=[workstream],
        )
        for diagnostic in table_diagnostics
    ]
    for row in rows:
        milestone_id = first_value(row, "milestone id", "milestone_id", "id")
        milestone = first_value(row, "milestone", "event", "name")
        source = first_value(row, "source", "source link", "source anchor")
        if not is_meaningful(milestone):
            excluded.append(
                ExcludedItem(
                    source=source_path,
                    source_type="wdr-roadmap",
                    item=json.dumps(row, ensure_ascii=False),
                    reason="roadmap row missing milestone",
                    code="missing_milestone",
                    risk=True,
                    workstreams=[workstream],
                )
            )
            continue
        if not is_meaningful(source):
            excluded.append(
                ExcludedItem(
                    source=source_path,
                    source_type="wdr-roadmap",
                    item=milestone,
                    reason="roadmap row missing source; milestones require traceable source",
                    code="missing_source",
                    risk=True,
                    workstreams=[workstream],
                )
            )
            continue
        raw_type = first_value(row, "type")
        raw_status = first_value(row, "status")
        raw_confidence = first_value(row, "confidence")
        item_type = normalize_type(raw_type)
        status = normalize_roadmap_status(raw_status)
        confidence = normalize_confidence(raw_confidence)
        enum_errors = [
            enum_error(field_name, raw_value, allowed)
            for field_name, raw_value, normalized, allowed in [
                ("Type", raw_type, item_type, VALID_TYPES),
                ("Status", raw_status, status, VALID_STATUSES),
                ("Confidence", raw_confidence, confidence, VALID_CONFIDENCE),
            ]
            if normalized is None
        ]
        if enum_errors:
            excluded.append(
                ExcludedItem(
                    source=clean(source),
                    source_type="wdr-roadmap",
                    item=clean(milestone),
                    reason="; ".join(enum_errors),
                    code="invalid_enum",
                    risk=True,
                    workstreams=[workstream],
                )
            )
            continue
        planned, planned_note = normalize_date_field(first_value(row, "planned", "plan"))
        forecast, forecast_note = normalize_date_field(first_value(row, "forecast", "forecast date"))
        actual, actual_note = normalize_date_field(first_value(row, "actual", "actual date", "completed"))
        notes = compact([planned_note, forecast_note, actual_note])
        item = RoadmapItem(
            id=clean(milestone_id) if is_meaningful(milestone_id) else stable_id("wdr-roadmap", workstream, milestone, source),
            milestone=clean(milestone),
            type=item_type,
            status=status,
            planned=planned,
            forecast=forecast,
            actual=actual,
            owner=clean(first_value(row, "owner")) or "TBD",
            confidence=confidence,
            depends_on=clean(first_value(row, "depends on", "depends_on", "dependencies")) or "TBD",
            source=clean(source),
            source_type="wdr-roadmap",
            workstreams=[workstream],
            notes=notes,
        )
        items.append(item)
    return items, excluded


def roadmap_items_from_checkpoint_candidates(
    memory_root: Path,
    selected_workstreams: set[str],
) -> tuple[list[RoadmapItem], list[ExcludedItem], list[dict[str, Any]]]:
    candidates_root = memory_root / "intake" / "bmm-checkpoints" / "candidates"
    items: list[RoadmapItem] = []
    excluded: list[ExcludedItem] = []
    sources: list[dict[str, Any]] = []
    registered_workstreams = discover_workstream_ids(memory_root)
    if not candidates_root.exists():
        return items, excluded, sources
    for path in sorted(candidates_root.glob("CHK-*.json")):
        source = rel_to_memory(memory_root, path)
        sources.append(file_item(path, memory_root))
        try:
            candidate = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError) as exc:
            excluded.append(
                ExcludedItem(
                    source=source,
                    source_type="checkpoint-candidate",
                    item=path.name,
                    reason=f"malformed candidate JSON: {exc}",
                    code="candidate_json_error",
                    risk=True,
                )
            )
            continue
        if not isinstance(candidate, dict):
            excluded.append(
                ExcludedItem(
                    source=source,
                    source_type="checkpoint-candidate",
                    item=path.name,
                    reason="malformed candidate JSON: root must be an object",
                    code="candidate_schema_error",
                    risk=True,
                )
            )
            continue
        raw_workstream = candidate.get("workstream_id")
        workstream = normalize_id(raw_workstream) if isinstance(raw_workstream, str) else ""
        if (
            not isinstance(raw_workstream, str)
            or not is_meaningful(raw_workstream)
            or workstream in PLACEHOLDER_IDS
            or workstream not in registered_workstreams
        ):
            excluded.append(
                ExcludedItem(
                    source=source,
                    source_type="checkpoint-candidate",
                    item=clean(candidate.get("candidate_id")) or path.name,
                    reason="candidate workstream_id must name a registered non-placeholder workstream",
                    code="invalid_workstream",
                    risk=True,
                )
            )
            continue
        if selected_workstreams and workstream not in selected_workstreams:
            continue
        status = clean(candidate.get("status")).lower()
        if status not in {"confirmed", "applied"}:
            excluded.append(
                ExcludedItem(
                    source=source,
                    source_type="checkpoint-candidate",
                    item=clean(candidate.get("candidate_id")) or path.name,
                    reason="candidate status must explicitly be confirmed or applied",
                    code="candidate_not_confirmed",
                    risk=False,
                    workstreams=[workstream] if workstream else [],
                )
            )
            continue
        raw_checkpoint = candidate.get("checkpoint")
        claims = candidate.get("claims")
        raw_summary = claims.get("summary") if isinstance(claims, dict) else None
        if (
            not isinstance(raw_checkpoint, str)
            or not is_meaningful(raw_checkpoint)
            or not isinstance(claims, dict)
            or not isinstance(raw_summary, str)
            or not is_meaningful(raw_summary)
        ):
            excluded.append(
                ExcludedItem(
                    source=source,
                    source_type="checkpoint-candidate",
                    item=clean(candidate.get("candidate_id")) or path.name,
                    reason="candidate requires non-placeholder checkpoint and claims.summary strings",
                    code="candidate_schema_error",
                    risk=True,
                    workstreams=[workstream],
                )
            )
            continue
        checkpoint = clean(raw_checkpoint)
        summary = clean(raw_summary)
        actual = normalize_date_field(candidate.get("applied_at"))[0] if status == "applied" else "TBD"
        item_status = "done" if status == "applied" else "planned"
        raw_confidence = candidate.get("confidence")
        confidence = source_confidence(raw_confidence)
        if confidence is None:
            excluded.append(
                ExcludedItem(
                    source=source,
                    source_type="checkpoint-candidate",
                    item=clean(candidate.get("candidate_id")) or path.name,
                    reason=enum_error("Confidence", raw_confidence, VALID_CONFIDENCE),
                    code="invalid_enum",
                    risk=True,
                    workstreams=[workstream] if workstream else [],
                )
            )
            continue
        items.append(
            RoadmapItem(
                id=stable_id("checkpoint-candidate", candidate.get("candidate_id", ""), checkpoint, summary),
                milestone=f"{checkpoint_label(checkpoint)} checkpoint: {summary}",
                type="checkpoint",
                status=item_status,
                planned="TBD",
                forecast="TBD",
                actual=actual,
                owner=checkpoint_owner(candidate),
                confidence=confidence,
                depends_on="TBD",
                source=source,
                source_type="checkpoint-candidate",
                workstreams=[workstream] if workstream else [],
                notes=[f"candidate status: {status}"],
            )
        )
    return items, excluded, sources


def roadmap_items_from_decisions(
    memory_root: Path,
    selected_workstreams: set[str],
) -> tuple[list[RoadmapItem], list[dict[str, Any]], list[ExcludedItem], list[dict[str, Any]]]:
    items: list[RoadmapItem] = []
    blocks: list[dict[str, Any]] = []
    excluded: list[ExcludedItem] = []
    sources: list[dict[str, Any]] = []
    decision_log = memory_root / "decisions" / "decision-log.md"
    if decision_log.exists():
        source_path = rel_to_memory(memory_root, decision_log)
        sources.append(file_item(decision_log, memory_root))
        try:
            text = read_text(decision_log)
        except OSError as exc:
            excluded.append(
                ExcludedItem(
                    source=source_path,
                    source_type="decision-log",
                    item=decision_log.name,
                    reason=f"cannot read source: {exc}",
                    code="source_read_error",
                    risk=True,
                )
            )
            text = ""
        table_diagnostics: list[str] = []
        rows = parse_first_table(
            text.splitlines(),
            table_diagnostics,
            required_headers=(("decision / question", "decision", "question"), ("status",), ("affected workstreams",)),
        )
        excluded.extend(
            ExcludedItem(
                source=source_path,
                source_type="decision-log",
                item="malformed decision table row",
                reason=f"malformed markdown table: {diagnostic}",
                code="table_schema_error",
                risk=True,
            )
            for diagnostic in table_diagnostics
        )
        for row in rows:
            question = first_value(row, "decision / question", "decision", "question")
            status = normalize_decision_status(first_value(row, "status"))
            source = first_value(row, "link", "source") or source_path
            workstreams = split_workstreams(first_value(row, "affected workstreams"))
            if selected_workstreams and not (selected_workstreams & set(workstreams)):
                continue
            raw_confidence = first_value(row, "confidence")
            confidence = source_confidence(raw_confidence)
            if confidence is None:
                excluded.append(
                    ExcludedItem(
                        source=clean(source),
                        source_type="decision-log",
                        item=clean(question) or "decision row",
                        reason=enum_error("Confidence", raw_confidence, VALID_CONFIDENCE),
                        code="invalid_enum",
                        risk=True,
                        workstreams=workstreams,
                    )
                )
                continue
            if status in DECISION_COMPLETED_STATUSES and is_meaningful(question):
                actual, note = normalize_date_field(first_value(row, "date"))
                notes = [note] if note else []
                items.append(
                    RoadmapItem(
                        id=stable_id("decision-log", question, source),
                        milestone=clean(question),
                        type="business-decision",
                        status="done",
                        planned="TBD",
                        forecast="TBD",
                        actual=actual,
                        owner=clean(first_value(row, "confirmer")) or "TBD",
                        confidence=confidence,
                        depends_on="TBD",
                        source=clean(source),
                        source_type="decision-log",
                        workstreams=workstreams,
                        notes=notes,
                    )
                )
            elif status in DECISION_OPEN_STATUSES and is_meaningful(question):
                blocks.append(
                    {
                        "source": clean(source),
                        "decision": clean(question),
                        "owner": clean(first_value(row, "confirmer")) or "TBD",
                        "status": status,
                        "workstreams": workstreams,
                    }
                )
            elif status not in DECISION_TERMINAL_STATUSES:
                excluded.append(
                    ExcludedItem(
                        source=clean(source),
                        source_type="decision-log",
                        item=clean(question) or "decision row",
                        reason="decision status must explicitly be open or a declared terminal status",
                        code="invalid_decision_status",
                        risk=True,
                        workstreams=workstreams,
                    )
                )
    packet_root = memory_root / "decisions" / "business-decision-packets"
    if packet_root.exists():
        for path in sorted(packet_root.glob("*.md")):
            sources.append(file_item(path, memory_root))
            try:
                packet = parse_business_packet(memory_root, path)
            except OSError as exc:
                excluded.append(
                    ExcludedItem(
                        source=rel_to_memory(memory_root, path),
                        source_type="business-decision-packet",
                        item=path.name,
                        reason=f"cannot read source: {exc}",
                        code="source_read_error",
                        risk=True,
                    )
                )
                continue
            if selected_workstreams and not (selected_workstreams & set(packet["workstreams"])):
                continue
            status = normalize_decision_status(packet["status"])
            if status in DECISION_OPEN_STATUSES:
                blocks.append(packet)
            elif status not in DECISION_TERMINAL_STATUSES:
                excluded.append(
                    ExcludedItem(
                        source=packet["source"],
                        source_type="business-decision-packet",
                        item=packet["decision"],
                        reason="decision status must explicitly be open or a declared terminal status",
                        code="invalid_decision_status",
                        risk=True,
                        workstreams=packet["workstreams"],
                    )
                )
    return items, blocks, excluded, sources


def roadmap_items_from_readiness_views(
    memory_root: Path,
    selected_workstreams: set[str],
) -> tuple[list[RoadmapItem], list[ExcludedItem], list[dict[str, Any]]]:
    items: list[RoadmapItem] = []
    excluded: list[ExcludedItem] = []
    sources: list[dict[str, Any]] = []
    registered_workstreams = discover_workstream_ids(memory_root)
    for rel, label, item_type in [
        ("views/acceptance-readiness.md", "Acceptance readiness gate", "readiness-gate"),
        ("views/cutover-readiness.md", "Cutover readiness gate", "cutover-gate"),
    ]:
        path = memory_root / rel
        if not path.exists():
            continue
        sources.append(file_item(path, memory_root))
        try:
            text = read_text(path)
        except OSError as exc:
            excluded.append(
                ExcludedItem(
                    source=rel,
                    source_type="readiness-gate",
                    item=path.name,
                    reason=f"cannot read source: {exc}",
                    code="source_read_error",
                    risk=True,
                )
            )
            continue
        table_diagnostics: list[str] = []
        rows = parse_first_table(
            text.splitlines(),
            table_diagnostics,
            required_headers=(("workstream",), ("roadmap status",)),
        )
        excluded.extend(
            ExcludedItem(
                source=rel,
                source_type="readiness-gate",
                item="malformed readiness table row",
                reason=f"malformed markdown table: {diagnostic}",
                code="table_schema_error",
                risk=True,
            )
            for diagnostic in table_diagnostics
        )
        for index, row in enumerate(rows, start=1):
            raw_workstreams = first_value(row, "workstream")
            workstreams = split_workstreams(raw_workstreams)
            unknown_workstreams = sorted(set(workstreams) - registered_workstreams)
            if not workstreams or unknown_workstreams:
                detail = (
                    f"unknown workstream ids: {', '.join(unknown_workstreams)}"
                    if unknown_workstreams
                    else "readiness row missing a valid non-placeholder Workstream id"
                )
                excluded.append(
                    ExcludedItem(
                        source=rel,
                        source_type="readiness-gate",
                        item=f"{label} row {index}",
                        reason=detail,
                        code="invalid_workstream",
                        risk=True,
                    )
                )
                continue
            if selected_workstreams and not (selected_workstreams & set(workstreams)):
                continue
            raw_status = first_value(row, "roadmap status")
            status = normalize_roadmap_status(raw_status)
            if status is None:
                detail = enum_error("Roadmap Status", raw_status, VALID_STATUSES)
                readiness_status = clean(first_value(row, "readiness status", "status"))
                if readiness_status:
                    detail += f"; readiness status is {readiness_status!r}"
                excluded.append(
                    ExcludedItem(
                        source=rel,
                        source_type="readiness-gate",
                        item=f"{label}: {', '.join(workstreams)}",
                        reason=detail,
                        code="invalid_enum",
                        risk=True,
                        workstreams=workstreams,
                    )
                )
                continue
            raw_confidence = first_value(row, "confidence")
            confidence = source_confidence(raw_confidence)
            if confidence is None:
                excluded.append(
                    ExcludedItem(
                        source=rel,
                        source_type="readiness-gate",
                        item=f"{label}: {', '.join(workstreams)}",
                        reason=enum_error("Confidence", raw_confidence, VALID_CONFIDENCE),
                        code="invalid_enum",
                        risk=True,
                        workstreams=workstreams,
                    )
                )
                continue
            items.append(
                RoadmapItem(
                    id=stable_id("readiness-gate", rel, workstreams),
                    milestone=f"{label}: {', '.join(workstreams)}",
                    type=item_type,
                    status=status,
                    planned="TBD",
                    forecast="TBD",
                    actual="TBD",
                    owner=clean(first_value(row, "owner")) or "TBD",
                    confidence=confidence,
                    depends_on="TBD",
                    source=rel,
                    source_type="readiness-gate",
                    workstreams=workstreams,
                    notes=["gate source; no date promoted without an explicit roadmap source"],
                )
            )
    return items, excluded, sources


def roadmap_items_from_l0(
    memory_root: Path,
    selected_workstreams: set[str],
) -> tuple[list[RoadmapItem], list[ExcludedItem], list[dict[str, Any]]]:
    items: list[RoadmapItem] = []
    excluded: list[ExcludedItem] = []
    sources: list[dict[str, Any]] = []
    registered_workstreams = discover_workstream_ids(memory_root)
    for rel, identity_headers in [
        ("l0/extracted-gates.md", ("gate",)),
        ("l0/extracted-decision-gates.md", ("decision gate",)),
    ]:
        path = memory_root / rel
        if not path.exists():
            continue
        sources.append(file_item(path, memory_root))
        try:
            text = read_text(path)
        except OSError as exc:
            excluded.append(
                ExcludedItem(
                    source=rel,
                    source_type="l0-gate",
                    item=path.name,
                    reason=f"cannot read source: {exc}",
                    code="source_read_error",
                    risk=True,
                )
            )
            continue
        table_diagnostics: list[str] = []
        rows = parse_first_table(
            text.splitlines(),
            table_diagnostics,
            required_headers=(identity_headers, ("affected workstreams", "workstream"), ("status",)),
        )
        excluded.extend(
            ExcludedItem(
                source=rel,
                source_type="l0-gate",
                item="malformed L0 table row",
                reason=f"malformed markdown table: {diagnostic}",
                code="table_schema_error",
                risk=True,
            )
            for diagnostic in table_diagnostics
        )
        for index, row in enumerate(rows, start=1):
            milestone = first_value(row, *identity_headers)
            if not is_meaningful(milestone):
                continue
            raw_workstreams = first_value(row, "affected workstreams", "workstream")
            workstreams = split_workstreams(raw_workstreams)
            unknown_workstreams = sorted(set(workstreams) - registered_workstreams)
            if not workstreams or unknown_workstreams:
                detail = (
                    f"unknown workstream ids: {', '.join(unknown_workstreams)}"
                    if unknown_workstreams
                    else "L0 gate row missing valid non-placeholder Affected Workstreams"
                )
                excluded.append(
                    ExcludedItem(
                        source=rel,
                        source_type="l0-gate",
                        item=clean(milestone) or f"L0 row {index}",
                        reason=detail,
                        code="invalid_workstream",
                        risk=True,
                    )
                )
                continue
            if selected_workstreams and not (selected_workstreams & set(workstreams)):
                continue
            raw_status = normalize_decision_status(first_value(row, "status"))
            status = L0_STATUS_MAP.get(raw_status)
            if status is None:
                excluded.append(
                    ExcludedItem(
                        source=rel,
                        source_type="l0-gate",
                        item=clean(milestone),
                        reason=(
                            f"invalid L0 Status enum {raw_status or '<missing>'!r}; "
                            f"allowed: {', '.join(sorted(L0_STATUS_MAP))}"
                        ),
                        code="invalid_enum",
                        risk=True,
                        workstreams=workstreams,
                    )
                )
                continue
            raw_confidence = first_value(row, "confidence")
            confidence = source_confidence(raw_confidence)
            if confidence is None:
                excluded.append(
                    ExcludedItem(
                        source=rel,
                        source_type="l0-gate",
                        item=clean(milestone),
                        reason=enum_error("Confidence", raw_confidence, VALID_CONFIDENCE),
                        code="invalid_enum",
                        risk=True,
                        workstreams=workstreams,
                    )
                )
                continue
            items.append(
                RoadmapItem(
                    id=stable_id("l0-gate", rel, milestone, workstreams),
                    milestone=clean(milestone),
                    type="readiness-gate",
                    status=status,
                    planned="TBD",
                    forecast="TBD",
                    actual="TBD",
                    owner=clean(first_value(row, "owner")) or "TBD",
                    confidence=confidence,
                    depends_on="TBD",
                    source=rel,
                    source_type="l0-gate",
                    workstreams=workstreams,
                    notes=["L0 gate state uses the declared exact status mapping; schedule remains TBD"],
                )
            )
    return items, excluded, sources


def excluded_unstructured_dependencies(
    memory_root: Path,
    selected_workstreams: set[str],
) -> tuple[list[ExcludedItem], list[dict[str, Any]]]:
    excluded: list[ExcludedItem] = []
    sources: list[dict[str, Any]] = []
    for record in discover_wdrs(memory_root, selected_workstreams):
        try:
            text = read_text(record)
        except OSError:
            continue
        workstream = workstream_id_from_record(record, text)
        status = parse_key_bullets(section_lines(text, "Project Status"))
        for label in ["blockers", "dependencies"]:
            value = status.get(label, "")
            if is_meaningful(value):
                excluded.append(
                    ExcludedItem(
                        source=rel_to_memory(memory_root, record),
                        source_type="wdr-project-status",
                        item=f"{label}: {clean(value)}",
                        reason=(
                            "free-prose blocker/dependency context is not promoted to an active block; "
                            "persist explicit structured state upstream"
                        ),
                        code="unstructured_dependency_context",
                        risk=False,
                        workstreams=[workstream],
                    )
                )
        if any(item.source == rel_to_memory(memory_root, record) for item in excluded):
            sources.append(file_item(record, memory_root))
    return excluded, sources


def excluded_action_due_dates(memory_root: Path, selected_workstreams: set[str]) -> tuple[list[ExcludedItem], list[dict[str, Any]]]:
    path = memory_root / "actions" / "action-ledger.md"
    if not path.exists():
        return [], []
    try:
        text = read_text(path)
    except OSError as exc:
        return [
            ExcludedItem(
                source="actions/action-ledger.md",
                source_type="action-ledger",
                item=path.name,
                reason=f"cannot read source: {exc}",
                code="source_read_error",
                risk=True,
            )
        ], [file_item(path, memory_root)]
    table_diagnostics: list[str] = []
    rows = parse_first_table(
        text.splitlines(),
        table_diagnostics,
        required_headers=(("status",), ("action",)),
    )
    excluded: list[ExcludedItem] = []
    excluded.extend(
        ExcludedItem(
            source="actions/action-ledger.md",
            source_type="action-ledger",
            item="malformed action table row",
            reason=f"malformed markdown table: {diagnostic}",
            code="table_schema_error",
            risk=True,
        )
        for diagnostic in table_diagnostics
    )
    for row in rows:
        status = clean(first_value(row, "status")).lower()
        if status not in ACTIVE_ACTION_STATUSES:
            continue
        targets = split_workstreams(first_value(row, "affected workstreams")) or split_workstreams(first_value(row, "workstream"))
        if selected_workstreams and not (selected_workstreams & set(targets)):
            continue
        due = first_value(row, "due / trigger", "due", "trigger")
        action = first_value(row, "action")
        if not is_meaningful(due) and not is_meaningful(action):
            continue
        excluded.append(
            ExcludedItem(
                source=first_value(row, "source") or "actions/action-ledger.md",
                source_type="action-ledger",
                item=clean(action) or clean(due),
                reason="action due/trigger is follow-up context and is not a roadmap milestone by default",
                code="action_not_milestone",
                risk=False,
                workstreams=targets,
            )
        )
    return excluded, [file_item(path, memory_root)]


def filter_excluded(items: list[ExcludedItem], selected: set[str]) -> list[ExcludedItem]:
    if not selected:
        return items
    return [
        item
        for item in items
        if selected & set(item.workstreams)
        or (not item.workstreams and item.risk)
    ]


def dedupe_items(items: list[RoadmapItem]) -> tuple[list[RoadmapItem], list[ExcludedItem]]:
    by_id: dict[str, RoadmapItem] = {}
    conflicts: dict[str, set[str]] = {}
    for item in items:
        if item.id not in by_id:
            by_id[item.id] = item
            continue
        existing = by_id[item.id]
        changed_fields = {
            field_name
            for field_name in RoadmapItem.__dataclass_fields__
            if field_name not in {"workstreams", "notes"}
            and getattr(existing, field_name) != getattr(item, field_name)
        }
        if changed_fields:
            conflicts.setdefault(item.id, set()).update(changed_fields)
            existing.workstreams = sorted(set(existing.workstreams) | set(item.workstreams))
            continue
        existing.workstreams = sorted(set(existing.workstreams) | set(item.workstreams))
        existing.notes = compact([*existing.notes, *item.notes])
    excluded = [
        ExcludedItem(
            source=by_id[item_id].source,
            source_type=by_id[item_id].source_type,
            item=by_id[item_id].milestone,
            reason=(
                f"conflicting duplicate facts for id {item_id}: "
                f"{', '.join(sorted(fields))}"
            ),
            code="duplicate_conflict",
            risk=True,
            workstreams=by_id[item_id].workstreams,
        )
        for item_id, fields in sorted(conflicts.items())
    ]
    return [item for item_id, item in by_id.items() if item_id not in conflicts], excluded


def dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for source in sources:
        path = str(source.get("path", ""))
        if path:
            by_path[path] = source
    return [by_path[key] for key in sorted(by_path)]


def fingerprint_sources(sources: list[dict[str, Any]], memory_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in sources:
        raw_path = str(source.get("path", ""))
        path = Path(raw_path)
        if not path.is_absolute():
            path = memory_root / path
        enriched = dict(source)
        enriched["fingerprint"] = "sha256:" + file_sha256(path) if path.is_file() else ""
        result.append(enriched)
    return result


def load_previous_roadmap(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"previous roadmap is unreadable; change history omitted: {path}: {exc}"
    if not isinstance(payload, dict):
        return None, f"previous roadmap root is not an object; change history omitted: {path}"
    return payload, None


def diff_previous(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    if not previous:
        return [], None
    if previous.get("schema_version") != current.get("schema_version"):
        return [], "previous roadmap schema version does not match the current renderer; change history omitted"
    for field_name in ["project_root", "memory_root"]:
        previous_value = previous.get(field_name)
        current_value = current.get(field_name)
        if (
            not isinstance(previous_value, str)
            or not isinstance(current_value, str)
            or Path(previous_value).resolve() != Path(current_value).resolve()
        ):
            return [], f"previous roadmap {field_name} provenance does not match; change history omitted"
    previous_scope = roadmap_scope(previous)
    current_scope = roadmap_scope(current)
    if previous_scope is None or current_scope is None:
        return [], "previous or current roadmap lacks valid scope metadata; change history omitted"
    if previous_scope != current_scope:
        return [], (
            "previous roadmap scope does not match the current render; change history omitted "
            f"(previous={sorted(previous_scope)}, current={sorted(current_scope)})"
        )
    previous_items, previous_item_error = item_map(previous)
    if previous_item_error:
        return [], f"previous roadmap {previous_item_error}; change history omitted"
    current_items, current_item_error = item_map(current)
    if current_item_error:
        return [], f"current roadmap {current_item_error}; change history omitted"
    changes: list[dict[str, Any]] = []
    for item_id, item in current_items.items():
        if item_id not in previous_items:
            changes.append({"change": "added", "id": item_id, "milestone": item.get("milestone", "")})
            continue
        before = previous_items[item_id]
        changed_fields = [
            field
            for field in [
                "status",
                "planned",
                "forecast",
                "actual",
                "variance_days",
                "confidence",
                "planned_source",
                "forecast_source",
                "actual_source",
                "status_source",
                "baseline_revision",
            ]
            if before.get(field) != item.get(field)
        ]
        if changed_fields:
            changes.append(
                {
                    "change": "updated",
                    "id": item_id,
                    "milestone": item.get("milestone", ""),
                    "fields": changed_fields,
                }
            )
    for item_id, item in previous_items.items():
        if item_id not in current_items:
            changes.append({"change": "removed", "id": item_id, "milestone": item.get("milestone", "")})
    return changes, None


def roadmap_scope(payload: dict[str, Any]) -> set[str] | None:
    scope = payload.get("scope")
    if isinstance(scope, dict):
        kind = scope.get("kind")
        selected = normalized_id_array(scope.get("selected_workstreams"))
        if selected is not None and (
            (kind == "global" and not selected)
            or (kind == "workstreams" and bool(selected))
        ):
            return selected
    return None


def item_map(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
    result: dict[str, dict[str, Any]] = {}
    required_strings = {
        "id",
        "milestone",
        "type",
        "status",
        "planned",
        "forecast",
        "actual",
        "owner",
        "confidence",
        "source",
        "source_type",
    }
    for section in ["milestone_timeline", "unscheduled_milestones", "unmapped_items"]:
        items = payload.get(section)
        if not isinstance(items, list):
            return {}, f"field {section!r} is not an array"
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return {}, f"{section}[{index}] is not an object"
            invalid_fields = sorted(
                field_name
                for field_name in required_strings
                if not isinstance(item.get(field_name), str) or not clean(item.get(field_name))
            )
            if invalid_fields:
                return {}, f"{section}[{index}] has invalid fields: {', '.join(invalid_fields)}"
            item_id = item["id"]
            if item_id in result:
                return {}, f"contains duplicate item id {item_id!r}"
            result[item_id] = item
    return result, None


def render_markdown(roadmap: dict[str, Any]) -> str:
    lines = [
        "# ADP Roadmap",
        "",
        f"Generated: {roadmap['generated_at']}",
        f"As of: {roadmap['as_of']}",
        f"Memory root: `{roadmap['memory_root']}`",
        f"Audit JSON: `{roadmap['audit_path']}`",
        f"Audit status: `{roadmap['audit_status']}`",
        f"Report confidence: `{roadmap['report_confidence']}`",
        f"Report status: `{roadmap['report_status']}`",
        f"Baseline revision: `{roadmap['baseline_revision']}`",
        f"Program status snapshot: `{roadmap['program_status']['snapshot_id']}`",
        f"Canonical overall status: `{roadmap['program_status']['overall_status']}`",
        f"Canonical status model: {roadmap['canonical_status_title']}",
        "",
    ]
    if roadmap["risk_bearing"]:
        lines.extend([
            "> RISK-BEARING ROADMAP: the audit or source parsing did not fully pass. Timeline facts are shown for triage and must not be read as a green delivery status.",
            "",
        ])
    lines.extend([
        "> Derived view. Planned dates come from the approved baseline; forecast, actual, variance, and status come from the canonical program-status snapshot.",
        "",
        "## Canonical Progress",
        "",
        *render_progress(roadmap["progress"]),
        "",
        "## Source Inventory",
        "",
        f"- Sources read: {roadmap['counts']['sources_read']}",
        f"- Missing sources: {len(roadmap['source_inventory']['missing_sources'])}",
        "- Action due dates are excluded from milestone generation unless tied to an explicit baseline milestone.",
        "",
    ])
    add_source_table(lines, roadmap["source_inventory"]["sources_read"])
    if roadmap["source_inventory"]["missing_sources"]:
        lines.extend(["Missing sources:", ""])
        lines.extend(f"- `{path}`" for path in roadmap["source_inventory"]["missing_sources"])
        lines.append("")
    add_item_table(lines, "Milestone Timeline", roadmap["milestone_timeline"])
    add_item_table(lines, "Unscheduled Milestones", roadmap["unscheduled_milestones"])
    add_item_table(lines, "Unmapped Items", roadmap["unmapped_items"])
    add_item_table(lines, "At-Risk Dates", roadmap["at_risk_dates"])
    changes = roadmap["baseline_changes"]
    lines.extend([
        "## Baseline Changes",
        "",
        f"- Status: `{changes['status']}`",
        f"- Revisions: `{changes['from_revision']}` -> `{changes['to_revision']}`",
        f"- Sources: `{changes['from_path'] or 'N/A'}` -> `{changes['to_path']}`",
        f"- Fingerprints: `{changes['from_fingerprint'] or 'N/A'}` -> `{changes['to_fingerprint']}`",
        "",
    ])
    add_dict_table(lines, "Baseline Revision Diff", changes["changes"], ["change", "kind", "id", "fields"])
    add_dict_table(lines, "Blocked By Decisions", roadmap["blocked_by_decisions"], ["source", "decision", "owner", "status", "workstreams"])
    add_dict_table(lines, "Changed Since Last Roadmap", roadmap["changed_since_last_roadmap"], ["change", "id", "milestone", "fields"])
    add_dict_table(lines, "Excluded Items", roadmap["excluded_items"], ["source", "source_type", "item", "code", "risk", "reason", "workstreams"])
    metadata = {
        key: roadmap[key]
        for key in (
            "generated_at",
            "as_of",
            "reporting_period",
            "report_confidence",
            "scenario",
            "input_audit_id",
            "baseline_revision",
            "program_status_snapshot_id",
            "source_fingerprints",
            "locale",
            "locale_fallback",
            "render_contract",
            "generator_version",
        )
    }
    lines.extend(
        [
            "<!-- adp:artifact-metadata:v1 -->",
            "",
            "```json",
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_progress(progress: dict[str, Any]) -> list[str]:
    current = progress["overall"]["current"]
    forecast = progress["overall"]["forecast_summary"]
    lines = [
        f"- Progress schema: `{progress['progress_schema_version']}`",
        f"- Measurement status: `{progress['measurement_status']}`",
        f"- Actual completion: {progress_value(current['actual_completion_percent'], '%')}",
        f"- Planned completion: {progress_value(current['planned_completion_percent'], '%')}",
        f"- Completion gap: {progress_value(current['completion_gap_pp'], ' pp')}",
        f"- Forecast completion: {progress_value(forecast['forecast_completion_percent'], '%')}",
        f"- Forecast coverage: {progress_value(forecast['forecast_coverage_percent'], '%')} (`{forecast['forecast_coverage_status']}`)",
        f"- Comparability: `{progress['overall']['comparability']['disposition']}`",
        "",
        "| Workstream | Kind | Measurement | Actual | Planned | Gap | Project Weight | Contribution |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in progress["by_workstream"]:
        values = item["current"]
        lines.append(
            "| "
            + " | ".join(
                cell(value)
                for value in [
                    item["workstream_id"],
                    item["progress_kind"],
                    item["measurement_status"],
                    progress_value(values["actual_completion_percent"], "%"),
                    progress_value(values["planned_completion_percent"], "%"),
                    progress_value(values["completion_gap_pp"], " pp"),
                    progress_value(values["project_weight_percent"], "%"),
                    progress_value(values["completed_contribution_pp"], " pp"),
                ]
            )
            + " |"
        )
    return lines


def progress_value(value: Any, suffix: str) -> str:
    return "-" if value is None else f"{float(value):.2f}{suffix}"


def add_source_table(lines: list[str], sources: list[dict[str, Any]]) -> None:
    if not sources:
        lines.extend(["No sources read.", ""])
        return
    lines.extend(["| Source | SHA-256 | Bytes | Modified |", "| --- | --- | ---: | --- |"])
    for source in sources:
        lines.append(
            f"| {cell(source.get('path', ''))} | {cell(source.get('fingerprint', ''))} | "
            f"{cell(source.get('bytes', ''))} | {cell(source.get('modified', ''))} |"
        )
    lines.append("")


def add_item_table(lines: list[str], title: str, items: list[dict[str, Any]]) -> None:
    headers = ["Milestone", "Type", "Status", "Planned", "Forecast", "Actual", "Variance Days", "Owner", "Confidence", "Planned Source", "Forecast Source", "Actual Source", "Status Source", "Source", "Source Type"]
    lines.extend([f"## {title}", ""])
    if not items:
        lines.extend(["No items.", ""])
        return
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for item in items:
        values = [
            item.get("milestone", ""),
            item.get("type", ""),
            item.get("status", ""),
            item.get("planned", ""),
            item.get("forecast", ""),
            item.get("actual", ""),
            item.get("variance_days", ""),
            item.get("owner", ""),
            item.get("confidence", ""),
            item.get("planned_source", item.get("source", "")),
            item.get("forecast_source", ""),
            item.get("actual_source", ""),
            item.get("status_source", item.get("source", "")),
            item.get("source", ""),
            item.get("source_type", ""),
        ]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines.append("")


def add_dict_table(lines: list[str], title: str, items: list[dict[str, Any]], headers: list[str]) -> None:
    lines.extend([f"## {title}", ""])
    if not items:
        lines.extend(["No items.", ""])
        return
    display = [title_case(header) for header in headers]
    lines.append("| " + " | ".join(display) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for item in items:
        lines.append("| " + " | ".join(cell(format_value(item.get(header, ""))) for header in headers) + " |")
    lines.append("")


def parse_business_packet(memory_root: Path, path: Path) -> dict[str, Any]:
    text = read_text(path)
    return {
        "source": rel_to_memory(memory_root, path),
        "decision": heading_title(text) or path.stem,
        "owner": extract_colon_field(text, "Confirming owner") or extract_colon_field(text, "Confirmer") or "TBD",
        "deadline": extract_colon_field(text, "Deadline / trigger") or "TBD",
        "status": extract_colon_field(text, "Status"),
        "workstreams": split_workstreams(extract_colon_field(text, "Affected workstreams")),
    }


def section_lines(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    marker = re.compile(rf"^#+\s+{re.escape(heading)}\s*$", re.IGNORECASE)
    start = None
    start_level = 0
    for index, line in enumerate(lines):
        match = marker.match(line.strip())
        if match:
            start = index + 1
            start_level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= start_level:
            end = index
            break
    return lines[start:end]


def parse_first_table(
    lines: list[str],
    diagnostics: list[str] | None = None,
    required_headers: tuple[tuple[str, ...], ...] = (),
) -> list[dict[str, str]]:
    table: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append((line_number, stripped))
        elif table:
            break
    if not table:
        return []
    if len(table) < 2:
        if diagnostics is not None:
            diagnostics.append(f"line {table[0][0]} table has no separator row")
        return []
    headers = [normalize_header(cell) for cell in split_markdown_row(table[0][1])]
    if any(not header for header in headers):
        if diagnostics is not None:
            diagnostics.append(f"line {table[0][0]} contains an empty header")
        return []
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        if diagnostics is not None:
            diagnostics.append(f"line {table[0][0]} contains duplicate headers: {', '.join(duplicates)}")
        return []
    separator_cells = split_markdown_row(table[1][1])
    valid_separator = (
        len(separator_cells) == len(headers)
        and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator_cells)
    )
    if not valid_separator:
        if diagnostics is not None:
            diagnostics.append(f"line {table[1][0]} is not a valid separator for {len(headers)} columns")
        return []
    for aliases in required_headers:
        normalized_aliases = {normalize_header(alias) for alias in aliases}
        present = [header for header in headers if header in normalized_aliases]
        if len(present) > 1:
            if diagnostics is not None:
                diagnostics.append(
                    f"line {table[0][0]} has alias-colliding headers for {aliases[0]!r}: {', '.join(present)}"
                )
            return []
        if not present:
            if diagnostics is not None:
                diagnostics.append(f"line {table[0][0]} is missing required header {aliases[0]!r}")
            return []
    rows: list[dict[str, str]] = []
    for line_number, line in table[2:]:
        cells = split_markdown_row(line)
        if len(cells) != len(headers):
            if diagnostics is not None:
                diagnostics.append(
                    f"line {line_number} has {len(cells)} cells; expected {len(headers)}"
                )
            continue
        row = dict(zip(headers, cells, strict=True))
        if any(is_meaningful(value) for value in row.values()):
            rows.append(row)
    return rows


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def parse_key_bullets(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, value = stripped[2:].split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def workstream_id_from_record(record: Path, text: str) -> str:
    identity = parse_key_bullets(section_lines(text, "Identity"))
    return normalize_id(identity.get("workstream id", "")) or normalize_id(record.parent.name) or record.parent.name


def first_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(normalize_header(name), "")
        if value:
            return value.strip()
    return ""


def normalize_header(value: str) -> str:
    value = value.lower().replace("_", " ")
    value = re.sub(r"[^a-z0-9/ ]+", " ", value)
    return " ".join(value.split())


def normalize_type(value: str) -> str | None:
    normalized = clean(value)
    return normalized if normalized in VALID_TYPES else None


def normalize_roadmap_status(value: str) -> str | None:
    normalized = clean(value)
    return normalized if normalized in VALID_STATUSES else None


def normalize_decision_status(value: Any) -> str:
    normalized = normalize_id(value)
    return "cancelled" if normalized == "canceled" else normalized


def normalize_confidence(value: str) -> str | None:
    normalized = clean(value)
    return normalized if normalized in VALID_CONFIDENCE else None


def source_confidence(value: Any) -> str | None:
    if not is_meaningful(value):
        return "TBD"
    return normalize_confidence(clean(value))


def enum_error(field_name: str, value: Any, allowed: set[str]) -> str:
    display = clean(value) or "<missing>"
    return f"invalid {field_name} enum {display!r}; allowed: {', '.join(sorted(allowed))}"


def normalize_date_field(value: Any) -> tuple[str, str]:
    text = clean(value)
    if not is_meaningful(text):
        return "TBD", ""
    parsed = parse_date(text)
    if parsed:
        return parsed.isoformat(), ""
    return "TBD", f"unparseable date left as TBD: {text}"


def parse_date(value: str) -> date | None:
    text = clean(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        text,
    ):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def is_meaningful(value: Any) -> bool:
    text = clean(value).strip("`").lower()
    return text not in PLACEHOLDERS


def normalized_id_array(value: Any) -> set[str] | None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not normalize_id(item)
        for item in value
    ):
        return None
    return {normalize_id(item) for item in value}


def has_any_date(item: RoadmapItem) -> bool:
    return any(value != "TBD" for value in [item.planned, item.forecast, item.actual])


def timeline_sort_key(item: RoadmapItem) -> tuple[str, str]:
    for value in [item.actual, item.forecast, item.planned]:
        if value != "TBD":
            return value, item.milestone.lower()
    return "9999-12-31", item.milestone.lower()


def item_sort_key(item: RoadmapItem) -> tuple[str, str]:
    return item.source_type, item.milestone.lower()


def checkpoint_label(value: str) -> str:
    labels = {
        "prd": "PRD",
        "architecture": "Architecture",
        "epic-story": "Epic/story",
        "implementation": "Implementation",
        "validation": "Validation",
        "baseline": "Baseline",
    }
    return labels.get(value, value.title())


def checkpoint_owner(candidate: dict[str, Any]) -> str:
    authority = candidate.get("authority", {}) if isinstance(candidate.get("authority"), dict) else {}
    return clean(authority.get("asserted_by")) or "TBD"


def extract_colon_field(text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def heading_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def split_workstreams(value: str) -> list[str]:
    result: list[str] = []
    for raw in re.split(r"\s*[,;]\s*", value or ""):
        raw_placeholder = clean(raw).strip("`").lower()
        normalized = normalize_id(raw)
        if raw_placeholder not in PLACEHOLDERS and normalized not in PLACEHOLDER_IDS:
            result.append(normalized)
    return sorted(set(result))


def normalize_id(raw: str) -> str:
    value = str(raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def compact(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = clean(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def stable_id(*parts: Any) -> str:
    payload = json.dumps([clean(part).lower() for part in parts], ensure_ascii=False, sort_keys=True)
    return "RM-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12].upper()


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    path = Path(raw_memory_root)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_output_dir(
    raw_output_dir: str | None,
    memory_root: Path,
    selected_workstreams: set[str],
) -> Path:
    if not raw_output_dir:
        if not selected_workstreams:
            return memory_root / "views"
        scope_name = "--".join(sorted(selected_workstreams))
        return memory_root / "views" / "roadmaps" / scope_name
    path = Path(raw_output_dir)
    if not path.is_absolute():
        path = memory_root / path
    return path.resolve()


def resolve_input_path(project_root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def file_item(path: Path, memory_root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": rel_to_memory(memory_root, path),
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "modified_ns": stat.st_mtime_ns,
    }


def rel_to_memory(memory_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(memory_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def recommended_workflows(roadmap: dict[str, Any]) -> list[str]:
    workflows: list[str] = []
    if roadmap["blocked_by_decisions"]:
        workflows.append("adp-risk-dependency-change-review")
    if roadmap["at_risk_dates"]:
        workflows.append("adp-risk-dependency-change-review")
    if roadmap["excluded_items"]:
        workflows.append("adp-status-sync")
    if any(item.get("source_type") == "readiness-gate" for item in roadmap["unscheduled_milestones"]):
        workflows.append("adp-acceptance-readiness-review")
    return sorted(set(workflows))


def merge_recommendations(*groups: list[Any]) -> list[str]:
    workflows: list[str] = []
    for group in groups:
        for item in group:
            workflow = item.get("workflow", "") if isinstance(item, dict) else item
            if clean(workflow):
                workflows.append(clean(workflow))
    return sorted(set(workflows))


def title_case(value: str) -> str:
    return value.replace("_", " ").title()


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "TBD"
    return str(value)


def cell(value: Any) -> str:
    return str(value or "TBD").replace("\n", " ").replace("|", "\\|")


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


def decode_process_output(raw: bytes) -> str:
    for encoding in ["utf-8-sig", sys.getdefaultencoding(), "mbcs"]:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


if __name__ == "__main__":
    sys.exit(main())
