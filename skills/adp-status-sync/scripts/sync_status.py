#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Apply lightweight ADP status updates to Workstream Delivery Records."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


VOLATILE_FIELDS = {
    "status": ("Identity", "Current ADP status"),
    "phase": ("Identity", "Current BMM phase"),
    "progress": ("Project Status", "Progress"),
    "blockers": ("Project Status", "Blockers"),
    "risks": ("Project Status", "Risks"),
    "dependencies": ("Project Status", "Dependencies"),
    "change_notes": ("Project Status", "Scope or change notes"),
    "next_actions": ("Project Status", "Next actions"),
    "last_status_sync": ("Project Status", "Last status sync"),
}

ACTION_LEDGER_REL = Path("actions") / "action-ledger.md"
BASELINE_REL = Path("plans") / "program-baseline.md"
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
ACTION_STATUSES = {"open", "in-progress", "blocked", "done", "cancelled"}
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
PROJECT_ACTION_IDS = {"program", "project", "adp-program"}
MILESTONE_STATUSES = {"planned", "in-progress", "at-risk", "done", "blocked"}
RECEIPT_SCHEMA_VERSION = 1
STATUS_SYNC_RECEIPT_REL = Path("receipts") / "status-sync"
ATTESTATION_WRAPPER_FIELDS = {
    "attestation",
    "attested_at",
    "attested_by",
    "execution_report",
    "original_report",
    "wrapper_attestation",
}
DEFAULT_CONFIG_SCRIPT = Path(__file__).resolve().parents[2] / "adp-plan-baseline/scripts/adp_effective_config.py"
ROADMAP_FIELDS = [
    "Milestone ID",
    "Milestone",
    "Type",
    "Status",
    "Planned",
    "Forecast",
    "Actual",
    "Owner",
    "Confidence",
    "Depends On",
    "Source",
    "Baseline Revision",
]
ACTION_FIELDS = [
    "Action ID",
    "Status",
    "Owner",
    "Workstream",
    "Affected Workstreams",
    "Action",
    "Source",
    "Reason",
    "Due / Trigger",
    "Closure Criteria",
    "Created At",
    "Started At",
    "Done At",
    "Cancelled At",
    "Baseline Revision",
    "Related Plan Items",
    "Related Flow Edges",
    "Last Updated",
    "Owning Workflow",
]


@dataclass
class ActionUpdate:
    action_id: str | None = None
    status: str = "open"
    owner: str = "TBD"
    workstream: str = "TBD"
    affected_workstreams: list[str] = field(default_factory=list)
    action: str = ""
    source: str = ""
    reason: str = ""
    due_or_trigger: str = "TBD"
    closure_criteria: str = "TBD"
    owning_workflow: str = "adp-status-sync"
    created_at: str | None = None
    started_at: str | None = None
    done_at: str | None = None
    cancelled_at: str | None = None
    baseline_revision: int | None = None
    related_plan_item_ids: list[str] | None = None
    related_flow_edge_ids: list[str] | None = None


@dataclass
class MilestoneUpdate:
    milestone_id: str
    status: str
    forecast: str | None = None
    actual: str | None = None
    evidence: list[str] = field(default_factory=list)
    expected_baseline_revision: int | None = None


@dataclass
class StatusUpdate:
    workstream_id: str
    status: str | None = None
    phase: str | None = None
    progress: str | None = None
    blockers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    change_notes: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    actions: list[ActionUpdate] = field(default_factory=list)
    milestones: list[MilestoneUpdate] = field(default_factory=list)
    reported_gaps: list[str] = field(default_factory=list)
    source: str = "status sync"

    def has_reliable_delta(self) -> bool:
        return any(
            [
                self.status,
                self.phase,
                self.progress,
                self.blockers,
                self.risks,
                self.dependencies,
                self.change_notes,
                self.next_actions,
                self.actions,
                self.milestones,
            ]
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="Resolve language, config source, and ADP memory state.")
    context.add_argument("project_root", help="Target project root containing BMad configuration.")
    context.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    context.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
    context.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    context.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    update = subparsers.add_parser("update", help="Apply one or more lightweight status updates.")
    update.add_argument("project_root", help="Project root containing ADP memory.")
    update.add_argument("--id", help="Workstream id for a single update.")
    update.add_argument("--status", help="Current ADP status.")
    update.add_argument("--phase", help="Current BMM phase.")
    update.add_argument("--progress", help="Short progress summary.")
    update.add_argument("--blocker", action="append", default=[], help="Blocker to set; repeat as needed.")
    update.add_argument("--risk", action="append", default=[], help="Risk to set; repeat as needed.")
    update.add_argument("--dependency", action="append", default=[], help="Dependency change to set; repeat as needed.")
    update.add_argument("--change-note", action="append", default=[], help="Scope or change note; repeat as needed.")
    update.add_argument("--next-action", action="append", default=[], help="Next action; repeat as needed.")
    update.add_argument("--milestone-id", help="Baseline milestone ID for a single structured milestone update.")
    update.add_argument("--milestone-status", choices=sorted(MILESTONE_STATUSES), help="Canonical milestone status.")
    update.add_argument("--milestone-forecast", help="Forecast date in ISO YYYY-MM-DD format.")
    update.add_argument("--milestone-actual", help="Actual date in ISO YYYY-MM-DD format.")
    update.add_argument("--milestone-evidence", action="append", default=[], help="Traceable milestone evidence; repeat as needed.")
    update.add_argument("--baseline-revision", type=int, help="Expected current program baseline revision.")
    update.add_argument("--source", default="status sync", help="Source label for daily log entry.")
    update.add_argument("--updates-file", help="JSON file containing a list or {'updates': [...]} batch payload.")
    update.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    update.add_argument("--dry-run", action="store_true", help="Report planned writes without changing files.")
    update.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    update.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    migrate = subparsers.add_parser(
        "migrate-receipt",
        help="Create one explicit compatibility receipt from historical successful execution evidence.",
    )
    migrate.add_argument("project_root", help="Project root containing ADP memory.")
    migrate.add_argument("--updates-file", required=True, help="Exact historical updates file to attest.")
    migrate.add_argument(
        "--evidence-file",
        required=True,
        help="Original historical non-dry-run status-sync result JSON with direct input_path/input_hash fields.",
    )
    migrate.add_argument("--applied-at", required=True, help="Attested application time as timezone-aware ISO-8601.")
    migrate.add_argument(
        "--attested-by",
        required=True,
        help="Attribution signature stored on the migration receipt; not proof of execution or authorization.",
    )
    migrate.add_argument("--dry-run", action="store_true", help="Verify the exact evidence binding without writing a receipt.")
    migrate.add_argument(
        "--verified-plan-token",
        help="Token returned by the verified dry-run; required before a durable migration receipt is written.",
    )
    migrate.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    migrate.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    migrate.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    stale = subparsers.add_parser("stale", help="List workstream records whose status sync is stale or missing.")
    stale.add_argument("project_root", help="Project root containing ADP memory.")
    stale.add_argument("--max-age-days", type=int, default=7, help="Age threshold in days. Default: 7.")
    stale.add_argument("--as-of", help="Date for age calculation, YYYY-MM-DD. Default: today.")
    stale.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    stale.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    stale.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    return parser.parse_args()


def normalize_id(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value:
        raise ValueError("workstream id must contain at least one letter or digit")
    return value


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def require_project_root(raw: str) -> Path:
    project_root = Path(raw).resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise ValueError("project_root is not an existing directory")
    return project_root


def require_file(raw: str, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} is not an existing file: {path}")
    return path


def load_python_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load required script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def normalize_required_timestamp(raw: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def updates_from_args(args: argparse.Namespace) -> list[StatusUpdate]:
    updates: list[StatusUpdate] = []
    if args.updates_file:
        payload = json.loads(Path(args.updates_file).read_text(encoding="utf-8"))
        items = payload.get("updates", payload) if isinstance(payload, dict) else payload
        payload_revision = parse_optional_revision(payload.get("baseline_revision"), "baseline_revision") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("updates-file must contain a list or an object with an 'updates' list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each batch update must be a JSON object")
            updates.append(update_from_mapping(item, default_source=args.source, default_revision=payload_revision))

    single_has_fields = any(
        [
            args.status,
            args.phase,
            args.progress,
            args.blocker,
            args.risk,
            args.dependency,
            args.change_note,
            args.next_action,
            args.milestone_id,
            args.milestone_status,
            args.milestone_forecast,
            args.milestone_actual,
            args.milestone_evidence,
        ]
    )
    if args.id or single_has_fields:
        if not args.id:
            raise ValueError("--id is required for single-update fields")
        updates.append(
            StatusUpdate(
                workstream_id=normalize_id(args.id),
                status=clean_optional(args.status),
                phase=clean_optional(args.phase),
                progress=clean_optional(args.progress),
                blockers=clean_list(args.blocker),
                risks=clean_list(args.risk),
                dependencies=clean_list(args.dependency),
                change_notes=clean_list(args.change_note),
                next_actions=clean_list(args.next_action),
                actions=[],
                milestones=milestones_from_cli(args),
                source=args.source,
            )
        )

    if not updates:
        raise ValueError("provide --id with status fields or --updates-file")
    return updates


def update_from_mapping(item: dict[str, Any], default_source: str, default_revision: int | None = None) -> StatusUpdate:
    raw_id = item.get("id") or item.get("workstream_id")
    if not raw_id:
        raise ValueError("batch update is missing id/workstream_id")
    return StatusUpdate(
        workstream_id=normalize_id(str(raw_id)),
        status=clean_optional(item.get("status")),
        phase=clean_optional(item.get("phase")),
        progress=clean_optional(item.get("progress")),
        blockers=clean_list(item.get("blockers", [])),
        risks=clean_list(item.get("risks", [])),
        dependencies=clean_list(item.get("dependencies", [])),
        change_notes=clean_list(item.get("change_notes", item.get("changeNotes", []))),
        next_actions=clean_list(item.get("next_actions", item.get("nextActions", []))),
        actions=actions_from_mapping(
            item,
            default_workstream=normalize_id(str(raw_id)),
            default_source=default_source,
            default_revision=parse_optional_revision(item.get("baseline_revision"), "baseline_revision") or default_revision,
        ),
        milestones=milestones_from_mapping(
            item,
            default_revision=parse_optional_revision(item.get("baseline_revision"), "baseline_revision") or default_revision,
        ),
        reported_gaps=clean_list(item.get("unresolved_gaps", item.get("gaps", []))),
        source=str(item.get("source") or default_source),
    )


def milestones_from_cli(args: argparse.Namespace) -> list[MilestoneUpdate]:
    supplied = any(
        [
            args.milestone_id,
            args.milestone_status,
            args.milestone_forecast,
            args.milestone_actual,
            args.milestone_evidence,
        ]
    )
    if not supplied:
        return []
    if not args.milestone_id:
        raise ValueError("--milestone-id is required when milestone fields are supplied")
    if not args.milestone_status:
        raise ValueError("--milestone-status is required for a milestone update")
    evidence = clean_list(args.milestone_evidence)
    if not evidence:
        raise ValueError("--milestone-evidence is required for a milestone update")
    return [
        MilestoneUpdate(
            milestone_id=args.milestone_id.strip(),
            status=args.milestone_status,
            forecast=clean_iso_date(args.milestone_forecast, "milestone forecast"),
            actual=clean_iso_date(args.milestone_actual, "milestone actual"),
            evidence=evidence,
            expected_baseline_revision=parse_optional_revision(args.baseline_revision, "baseline_revision"),
        )
    ]


def milestones_from_mapping(item: dict[str, Any], default_revision: int | None) -> list[MilestoneUpdate]:
    raw_milestones = item.get("milestones", item.get("milestone_updates", []))
    if raw_milestones is None:
        return []
    if not isinstance(raw_milestones, list):
        raise ValueError("batch update milestones must be a list")
    milestones: list[MilestoneUpdate] = []
    for raw in raw_milestones:
        if not isinstance(raw, dict):
            raise ValueError("each milestone update must be a JSON object")
        milestone_id = clean_optional(raw.get("milestone_id") or raw.get("id"))
        if not milestone_id:
            raise ValueError("milestone update is missing milestone_id")
        status = (clean_optional(raw.get("status")) or "").lower()
        if status not in MILESTONE_STATUSES:
            raise ValueError(f"milestone {milestone_id} status must be one of: {', '.join(sorted(MILESTONE_STATUSES))}")
        evidence = clean_list(raw.get("evidence", raw.get("sources", raw.get("source", []))))
        if not evidence:
            raise ValueError(f"milestone {milestone_id} requires traceable evidence")
        revision = parse_optional_revision(raw.get("baseline_revision"), f"milestone {milestone_id} baseline_revision")
        milestones.append(
            MilestoneUpdate(
                milestone_id=milestone_id,
                status=status,
                forecast=clean_iso_date(raw.get("forecast"), f"milestone {milestone_id} forecast"),
                actual=clean_iso_date(raw.get("actual"), f"milestone {milestone_id} actual"),
                evidence=evidence,
                expected_baseline_revision=revision or default_revision,
            )
        )
    return milestones


def clean_iso_date(value: Any, label: str) -> str | None:
    text = clean_optional(value)
    if not text or text.upper() == "TBD":
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a real ISO YYYY-MM-DD date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{label} must use canonical ISO YYYY-MM-DD format")
    return text


def parse_optional_revision(value: Any, label: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if revision < 1:
        raise ValueError(f"{label} must be a positive integer")
    return revision


def actions_from_mapping(
    item: dict[str, Any],
    default_workstream: str,
    default_source: str,
    default_revision: int | None = None,
) -> list[ActionUpdate]:
    raw_actions = item.get("actions", [])
    if raw_actions is None:
        return []
    if not isinstance(raw_actions, list):
        raise ValueError("batch update actions must be a list")
    actions: list[ActionUpdate] = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            raise ValueError("each action update must be a JSON object")
        action_id = clean_optional(raw_action.get("action_id") or raw_action.get("id"))
        status = normalize_action_status(raw_action.get("status"))
        if status in {"done", "cancelled"} and not action_id:
            raise ValueError(f"{status} action update requires action_id")
        action_text = clean_optional(raw_action.get("action") or raw_action.get("text") or raw_action.get("next_action")) or ""
        if not action_text and not action_id:
            raise ValueError("action update is missing action/text or action_id")
        affected_workstreams = normalize_workstream_list(
            raw_action.get("affected_workstreams")
            or raw_action.get("affectedWorkstreams")
            or raw_action.get("impacts")
        )
        raw_workstream = clean_optional(raw_action.get("workstream") or raw_action.get("workstream_id"))
        if raw_workstream and raw_workstream.upper() != "TBD":
            workstream = normalize_id(raw_workstream)
        elif len(affected_workstreams) > 1:
            workstream = "program"
        elif affected_workstreams:
            workstream = affected_workstreams[0]
        else:
            workstream = default_workstream
        actions.append(
            ActionUpdate(
                action_id=action_id,
                status=status,
                owner=clean_optional(raw_action.get("owner")) or "TBD",
                workstream=workstream or "TBD",
                affected_workstreams=affected_workstreams,
                action=action_text,
                source=clean_optional(raw_action.get("source")) or clean_optional(item.get("source")) or default_source,
                reason=clean_optional(raw_action.get("reason")) or "TBD",
                due_or_trigger=(
                    clean_optional(
                        raw_action.get("due_or_trigger")
                        or raw_action.get("due")
                        or raw_action.get("trigger")
                    )
                    or "TBD"
                ),
                closure_criteria=clean_optional(raw_action.get("closure_criteria")) or "TBD",
                owning_workflow=clean_optional(raw_action.get("owning_workflow")) or "adp-status-sync",
                created_at=clean_iso_timestamp(raw_action.get("created_at"), "action created_at"),
                started_at=clean_iso_timestamp(raw_action.get("started_at"), "action started_at"),
                done_at=clean_iso_timestamp(raw_action.get("done_at"), "action done_at"),
                cancelled_at=clean_iso_timestamp(raw_action.get("cancelled_at"), "action cancelled_at"),
                baseline_revision=parse_optional_revision(raw_action.get("baseline_revision"), "action baseline_revision") or default_revision,
                related_plan_item_ids=(
                    normalize_stable_id_list(raw_action.get("related_plan_item_ids"), "related_plan_item_ids")
                    if "related_plan_item_ids" in raw_action
                    else None
                ),
                related_flow_edge_ids=(
                    normalize_stable_id_list(raw_action.get("related_flow_edge_ids"), "related_flow_edge_ids")
                    if "related_flow_edge_ids" in raw_action
                    else None
                ),
            )
        )
    return actions


def clean_iso_timestamp(value: Any, label: str) -> str | None:
    text = clean_optional(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_stable_id_list(value: Any, label: str) -> list[str]:
    values = clean_list(value)
    result: list[str] = []
    for item in values:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item):
            raise ValueError(f"{label} contains invalid stable ID {item!r}")
        if item not in result:
            result.append(item)
    return sorted(result)


def normalize_action_status(raw: Any) -> str:
    status = clean_optional(raw)
    if not status:
        return "open"
    normalized = status.lower().strip().replace("_", "-")
    if normalized in {"in progress", "inprogress"}:
        normalized = "in-progress"
    if normalized not in ACTION_STATUSES:
        raise ValueError(f"action status must be one of: {', '.join(sorted(ACTION_STATUSES))}")
    return normalized


def clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]
    return [str(item).strip() for item in raw_items if str(item).strip()]


def normalize_workstream_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"\s*[,;]\s*", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item).strip()
        if not text or text.upper() == "TBD":
            continue
        normalized_id = normalize_id(text)
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        normalized.append(normalized_id)
    return normalized


def ensure_action_ledger(memory_root: Path, dry_run: bool) -> Path:
    path = memory_root / ACTION_LEDGER_REL
    if not path.exists() and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_action_ledger(), encoding="utf-8", newline="\n")
    return path


def default_action_ledger() -> str:
    return "\n".join(
        [
            "# Action Ledger",
            "",
            "This is the ADP action source of truth. Do not use `views/fde-actions.md` as a source file.",
            "",
            "| " + " | ".join(ACTION_FIELDS) + " |",
            "| " + " | ".join("---" for _ in ACTION_FIELDS) + " |",
            "",
        ]
    )


def parse_action_ledger(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    table_lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return rows
    headers = [normalize_header(cell) for cell in split_markdown_row(table_lines[0])]
    for line in table_lines[1:]:
        cells = split_markdown_row(line)
        if all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        normalized = {field: row.get(normalize_header(field), "") for field in ACTION_FIELDS}
        if any(value.strip() for value in normalized.values()):
            rows.append(normalized)
    return rows


def write_action_ledger(path: Path, rows: list[dict[str, str]], dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Action Ledger",
        "",
        "This is the ADP action source of truth. Do not use `views/fde-actions.md` as a source file.",
        "",
        "| " + " | ".join(ACTION_FIELDS) + " |",
        "| " + " | ".join("---" for _ in ACTION_FIELDS) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(table_cell(row.get(field, "")) for field in ACTION_FIELDS) + " |")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


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


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def table_cell(value: Any) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def upsert_actions(
    ledger_path: Path,
    action_updates: list[ActionUpdate],
    timestamp: str,
    dry_run: bool,
) -> dict[str, Any]:
    rows = parse_action_ledger(ledger_path)
    registered: list[str] = []
    updated: list[str] = []
    closed: list[str] = []
    gaps: list[str] = []

    for action_update in action_updates:
        if not action_update.action_id and find_registered_action(rows, action_update) is not None:
            continue
        match = find_action_row(rows, action_update)
        if match is None:
            if action_update.status in {"done", "cancelled"}:
                ref = action_update.action_id or action_update.action or "(missing action)"
                gaps.append(f"{ref}: close/update action was not found in ledger")
                continue
            new_row = new_action_row(rows, action_update, timestamp)
            rows.append(new_row)
            registered.append(new_row["Action ID"])
            gaps.extend(action_gaps(new_row))
            continue

        before_status = match.get("Status", "")
        validate_action_transition(before_status, action_update.status, action_update.action_id or match.get("Action ID", ""))
        merge_action_row(match, action_update, timestamp)
        action_id = match.get("Action ID", "")
        if match["Status"] in {"done", "cancelled"} and before_status != match["Status"]:
            closed.append(action_id)
        else:
            updated.append(action_id)
        gaps.extend(action_gaps(match))

    if action_updates:
        write_action_ledger(ledger_path, rows, dry_run)

    return {
        "rows": rows,
        "actions_registered": registered,
        "actions_updated": updated,
        "actions_closed": closed,
        "unresolved_gaps": sorted(set(gaps)),
    }


def find_action_row(rows: list[dict[str, str]], action_update: ActionUpdate) -> dict[str, str] | None:
    if not action_update.action_id:
        return None
    for row in rows:
        if row.get("Action ID") == action_update.action_id:
            return row
    return None


def find_registered_action(rows: list[dict[str, str]], action_update: ActionUpdate) -> dict[str, str] | None:
    if not action_update.action or not action_update.source:
        return None
    registration_key = action_key(action_update.owner, action_update.workstream, action_update.action, action_update.source)
    for row in rows:
        row_key = action_key(
            row.get("Owner", ""), row.get("Workstream", ""), row.get("Action", ""), row.get("Source", "")
        )
        if row_key == registration_key:
            return row
    return None


def action_key(owner: str, workstream: str, action: str, source: str) -> str:
    return "|".join(normalize_text_key(value) for value in [owner, workstream, action, source])


def normalize_text_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def new_action_row(rows: list[dict[str, str]], action_update: ActionUpdate, timestamp: str) -> dict[str, str]:
    action_id = action_update.action_id or next_action_id(rows, timestamp)
    started_at = action_update.started_at or (timestamp if action_update.status == "in-progress" else "")
    done_at = action_update.done_at or (timestamp if action_update.status == "done" else "")
    cancelled_at = action_update.cancelled_at or (timestamp if action_update.status == "cancelled" else "")
    return {
        "Action ID": action_id,
        "Status": action_update.status,
        "Owner": action_update.owner or "TBD",
        "Workstream": action_update.workstream or "TBD",
        "Affected Workstreams": action_workstreams_cell(action_update),
        "Action": action_update.action,
        "Source": action_update.source or "TBD",
        "Reason": action_update.reason or "TBD",
        "Due / Trigger": action_update.due_or_trigger or "TBD",
        "Closure Criteria": action_update.closure_criteria or "TBD",
        "Created At": action_update.created_at or timestamp,
        "Started At": started_at,
        "Done At": done_at,
        "Cancelled At": cancelled_at,
        "Baseline Revision": str(action_update.baseline_revision or ""),
        "Related Plan Items": "; ".join(action_update.related_plan_item_ids or []),
        "Related Flow Edges": "; ".join(action_update.related_flow_edge_ids or []),
        "Last Updated": timestamp,
        "Owning Workflow": action_update.owning_workflow or "adp-status-sync",
    }


def merge_action_row(row: dict[str, str], action_update: ActionUpdate, timestamp: str) -> None:
    row["Status"] = action_update.status
    assign_if_meaningful(row, "Owner", action_update.owner)
    assign_if_meaningful(row, "Workstream", action_update.workstream)
    row["Affected Workstreams"] = merge_action_workstreams(
        row.get("Affected Workstreams", ""),
        action_update.affected_workstreams,
        action_update.workstream,
    )
    assign_if_present(row, "Action", action_update.action)
    assign_if_meaningful(row, "Source", action_update.source)
    assign_if_meaningful(row, "Reason", action_update.reason)
    assign_if_meaningful(row, "Due / Trigger", action_update.due_or_trigger)
    assign_if_meaningful(row, "Closure Criteria", action_update.closure_criteria)
    if not row.get("Created At"):
        row["Created At"] = action_update.created_at or row.get("Last Updated") or timestamp
    if action_update.created_at:
        row["Created At"] = action_update.created_at
    if action_update.status == "in-progress" and not row.get("Started At"):
        row["Started At"] = action_update.started_at or timestamp
    elif action_update.started_at:
        row["Started At"] = action_update.started_at
    if action_update.status == "done":
        row["Done At"] = action_update.done_at or timestamp
    elif action_update.done_at:
        row["Done At"] = action_update.done_at
    if action_update.status == "cancelled":
        row["Cancelled At"] = action_update.cancelled_at or timestamp
    elif action_update.cancelled_at:
        row["Cancelled At"] = action_update.cancelled_at
    if action_update.baseline_revision is not None:
        row["Baseline Revision"] = str(action_update.baseline_revision)
    if action_update.related_plan_item_ids is not None:
        row["Related Plan Items"] = "; ".join(action_update.related_plan_item_ids)
    if action_update.related_flow_edge_ids is not None:
        row["Related Flow Edges"] = "; ".join(action_update.related_flow_edge_ids)
    row["Last Updated"] = timestamp
    assign_if_meaningful(row, "Owning Workflow", action_update.owning_workflow)


def validate_action_transition(before: str, after: str, action_id: str) -> None:
    if before.lower() in {"done", "cancelled"} and after != before.lower():
        raise ValueError(f"terminal action {action_id} cannot transition from {before} to {after}")


def build_action_flow_contract(rows: list[dict[str, str]], ledger_path: Path) -> dict[str, Any]:
    fingerprint = "sha256:" + hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    actions: list[dict[str, Any]] = []
    for row in rows:
        action_id = row.get("Action ID", "").strip()
        status = row.get("Status", "").strip().lower()
        revision = row.get("Baseline Revision", "").strip()
        created_at = row.get("Created At", "").strip()
        updated_at = row.get("Last Updated", "").strip()
        if not action_id or status not in ACTION_STATUSES or not revision.isdigit() or int(revision) < 1 or not created_at or not updated_at:
            continue
        actions.append(
            {
                "action_id": action_id,
                "status": status,
                "created_at": clean_iso_timestamp(created_at, f"action {action_id} created_at"),
                "updated_at": clean_iso_timestamp(updated_at, f"action {action_id} updated_at"),
                "started_at": clean_iso_timestamp(row.get("Started At"), f"action {action_id} started_at"),
                "done_at": clean_iso_timestamp(row.get("Done At"), f"action {action_id} done_at"),
                "cancelled_at": clean_iso_timestamp(row.get("Cancelled At"), f"action {action_id} cancelled_at"),
                "baseline_revision": int(revision),
                "related_plan_item_ids": normalize_stable_id_list(re.split(r"\s*[;,]\s*", row.get("Related Plan Items", "")), "related_plan_item_ids"),
                "related_flow_edge_ids": normalize_stable_id_list(re.split(r"\s*[;,]\s*", row.get("Related Flow Edges", "")), "related_flow_edge_ids"),
                "source": {
                    "artifact_id": "ACTION-LEDGER",
                    "artifact_path": "actions/action-ledger.md",
                    "source_fingerprint": fingerprint,
                },
            }
        )
    return {
        "action_flow_schema_version": "1.0.0",
        "actions": sorted(actions, key=lambda item: item["action_id"]),
        "compatibility": {"strategy": "preserve-unmapped", "migration_error_code": "ADP-ACTION-FLOW-MIGRATION-REQUIRED"},
    }


def assign_if_meaningful(row: dict[str, str], field_name: str, value: str) -> None:
    text = str(value or "").strip()
    if text and text.upper() != "TBD":
        row[field_name] = text


def assign_if_present(row: dict[str, str], field_name: str, value: str) -> None:
    text = str(value or "").strip()
    if text:
        row[field_name] = text


def action_workstreams_cell(action_update: ActionUpdate) -> str:
    workstreams = action_update.affected_workstreams
    if not workstreams and action_update.workstream.upper() not in {"", "TBD"}:
        workstreams = [action_update.workstream]
    return "; ".join(workstreams) if workstreams else "TBD"


def parse_workstream_cell(value: str) -> list[str]:
    return normalize_workstream_list(value)


def merge_action_workstreams(existing: str, new: list[str], fallback: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for candidate in [*parse_workstream_cell(existing), *new]:
        if candidate in seen:
            continue
        seen.add(candidate)
        merged.append(candidate)
    if not merged and fallback.upper() not in {"", "TBD"} and fallback not in PROJECT_ACTION_IDS:
        merged.append(fallback)
    return "; ".join(merged) if merged else "TBD"


def next_action_id(rows: list[dict[str, str]], timestamp: str) -> str:
    try:
        prefix_date = datetime.fromisoformat(timestamp).strftime("%Y%m%d")
    except ValueError:
        prefix_date = date.today().strftime("%Y%m%d")
    prefix = f"ACT-{prefix_date}-"
    max_seen = 0
    for row in rows:
        action_id = row.get("Action ID", "")
        if not action_id.startswith(prefix):
            continue
        suffix = action_id.removeprefix(prefix)
        if suffix.isdigit():
            max_seen = max(max_seen, int(suffix))
    return f"{prefix}{max_seen + 1:03d}"


def action_gaps(row: dict[str, str]) -> list[str]:
    if row.get("Status", "").lower() not in ACTIVE_ACTION_STATUSES:
        return []
    action_id = row.get("Action ID", "(missing id)")
    gaps: list[str] = []
    if is_missing_action_value(row.get("Owner", "")):
        gaps.append(f"{action_id}: Owner is missing")
    if is_missing_workstream(row.get("Workstream", "")) and not parse_workstream_cell(row.get("Affected Workstreams", "")):
        gaps.append(f"{action_id}: Workstream is missing")
    if is_missing_action_value(row.get("Due / Trigger", "")):
        gaps.append(f"{action_id}: Due / Trigger is missing")
    if is_missing_action_value(row.get("Closure Criteria", "")):
        gaps.append(f"{action_id}: Closure Criteria is missing")
    return gaps


def is_missing_action_value(value: str) -> bool:
    text = str(value or "").strip()
    return not text or text.upper() == "TBD"


def is_missing_workstream(value: str) -> bool:
    text = str(value or "").strip()
    return not text or text.upper() == "TBD"


def split_next_actions(value: str) -> list[str]:
    placeholders = {"", "tbd", "todo", "none", "n/a", "na", "fill missing state"}
    items = [item.strip() for item in re.split(r"\s*;\s*", value or "")]
    return [item for item in items if item and item.lower() not in placeholders]


def merge_unique(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for item in group:
            key = normalize_text_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def active_action_summaries(rows: list[dict[str, str]], workstream_id: str) -> list[str]:
    summaries: list[str] = []
    normalized_workstream = normalize_id(workstream_id)
    for row in rows:
        if row.get("Status", "").lower() not in ACTIVE_ACTION_STATUSES:
            continue
        row_workstream = safe_normalize_id(row.get("Workstream", ""))
        affected_workstreams = parse_workstream_cell(row.get("Affected Workstreams", ""))
        if row_workstream != normalized_workstream and normalized_workstream not in affected_workstreams:
            continue
        action = row.get("Action", "").strip()
        if not action:
            continue
        owner = row.get("Owner", "").strip() or "TBD"
        due = row.get("Due / Trigger", "").strip() or "TBD"
        action_id = row.get("Action ID", "").strip() or "TBD"
        summaries.append(f"[action_id:{action_id}] {owner}: {action} (due: {due})")
    return summaries


def safe_normalize_id(value: str) -> str:
    try:
        return normalize_id(value)
    except ValueError:
        return ""


def remove_closed_action_summaries(
    existing_actions: list[str], action_updates: list[ActionUpdate]
) -> tuple[list[str], list[str]]:
    closed_ids = {
        action.action_id
        for action in action_updates
        if action.status in {"done", "cancelled"} and action.action_id
    }
    if not closed_ids:
        return existing_actions, []
    kept: list[str] = []
    preserved_legacy = False
    for summary in existing_actions:
        summary_id = action_summary_id(summary)
        if summary_id in closed_ids:
            continue
        if summary_id is None:
            preserved_legacy = True
        kept.append(summary)
    gaps = []
    if preserved_legacy:
        gaps.append(
            "legacy Next actions entries without action_id were preserved while closing "
            + ", ".join(sorted(closed_ids))
        )
    return kept, gaps


def action_summary_id(summary: str) -> str | None:
    match = re.match(r"^\[action_id:([^\]]+)\]\s+", summary.strip())
    return match.group(1).strip() if match else None


def existing_field_value(markdown: str, section_title: str, label: str) -> str:
    lines = markdown.splitlines()
    start, end = find_section(lines, section_title)
    if start is None:
        return ""
    pattern = re.compile(rf"^\s*-\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE)
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match:
            return match.group(1).strip()
    return ""


def parse_program_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("program baseline is missing; run adp-plan-baseline before syncing milestones")
    text = path.read_text(encoding="utf-8-sig")
    marker_index = text.find(BASELINE_MARKER)
    if marker_index < 0:
        raise ValueError(f"program baseline marker is missing: {BASELINE_MARKER}")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker_index:], re.DOTALL)
    if not match:
        raise ValueError("canonical program baseline JSON is missing")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError("canonical program baseline must be an object")
    return value


def validate_milestone_updates(memory_root: Path, updates: list[StatusUpdate]) -> dict[str, Any] | None:
    requested = [(update, milestone) for update in updates for milestone in update.milestones]
    if not requested:
        return None
    baseline_path = memory_root / BASELINE_REL
    baseline = parse_program_baseline(baseline_path)
    revision = parse_optional_revision(baseline.get("revision"), "program baseline revision")
    if revision is None:
        raise ValueError("program baseline revision is missing")
    milestones = baseline.get("milestones")
    if not isinstance(milestones, list):
        raise ValueError("program baseline milestones must be a list")
    index: dict[str, dict[str, Any]] = {}
    for item in milestones:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        if item["id"] in index:
            raise ValueError(f"program baseline contains duplicate milestone ID {item['id']}")
        index[item["id"]] = item
    for update, milestone in requested:
        baseline_item = index.get(milestone.milestone_id)
        if baseline_item is None:
            raise ValueError(
                f"unknown baseline milestone {milestone.milestone_id}; status sync never creates baseline milestones implicitly"
            )
        baseline_workstream = normalize_id(str(baseline_item.get("workstream_id", "")))
        if baseline_workstream != update.workstream_id:
            raise ValueError(
                f"milestone {milestone.milestone_id} belongs to workstream {baseline_workstream}, not {update.workstream_id}"
            )
        if milestone.expected_baseline_revision is not None and milestone.expected_baseline_revision != revision:
            raise ValueError(
                f"milestone {milestone.milestone_id} expected baseline revision "
                f"{milestone.expected_baseline_revision}, found {revision}"
            )
        record_path = memory_root / "workstreams" / update.workstream_id / "delivery-record.md"
        if not record_path.exists():
            raise ValueError(f"delivery-record.md not found for milestone workstream {update.workstream_id}")
    return {"path": baseline_path, "revision": revision, "milestones": index}


def apply_milestone_updates(
    markdown: str,
    milestone_updates: list[MilestoneUpdate],
    baseline_context: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    if not milestone_updates:
        return markdown, []
    headers, rows, section_start, table_start, table_end = parse_roadmap_table(markdown)
    extra_headers = [header for header in headers if header not in ROADMAP_FIELDS]
    output_headers = [*ROADMAP_FIELDS, *extra_headers]
    changes: list[dict[str, str]] = []
    baseline_revision = str(baseline_context["revision"])

    for update in milestone_updates:
        baseline = baseline_context["milestones"][update.milestone_id]
        name = str(baseline.get("name") or update.milestone_id)
        row = next(
            (
                candidate
                for candidate in rows
                if candidate.get("Milestone ID") == update.milestone_id
                or (not candidate.get("Milestone ID") and candidate.get("Milestone") == name)
            ),
            None,
        )
        if row is None:
            row = {header: "" for header in output_headers}
            rows.append(row)
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        row["Milestone ID"] = update.milestone_id
        row["Milestone"] = name
        row["Type"] = str(baseline.get("type") or row.get("Type") or "checkpoint")
        row["Status"] = update.status
        row["Planned"] = str(baseline.get("planned_date") or "TBD")
        if update.forecast:
            row["Forecast"] = update.forecast
        elif not row.get("Forecast"):
            row["Forecast"] = "TBD"
        if update.actual:
            row["Actual"] = update.actual
        elif not row.get("Actual"):
            row["Actual"] = "TBD"
        row["Owner"] = str(baseline.get("owner") or row.get("Owner") or "TBD")
        row["Confidence"] = row.get("Confidence") or "low"
        dependencies = baseline.get("dependencies", [])
        row["Depends On"] = "; ".join(str(value) for value in dependencies) if dependencies else "TBD"
        row["Source"] = "; ".join(update.evidence)
        row["Baseline Revision"] = baseline_revision
        after = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if before != after:
            changes.append(
                {
                    "field": f"Milestone {update.milestone_id}",
                    "before": before if before != "{}" else "",
                    "after": after,
                }
            )

    table_lines = [
        "| " + " | ".join(output_headers) + " |",
        "| " + " | ".join("---" for _ in output_headers) + " |",
    ]
    table_lines.extend(
        "| " + " | ".join(table_cell(row.get(header, "")) for header in output_headers) + " |"
        for row in rows
    )
    lines = markdown.splitlines()
    if table_start is not None and table_end is not None:
        lines[table_start:table_end] = table_lines
    elif section_start is not None:
        _, section_end = find_section(lines, "Roadmap")
        insert_at = section_end
        while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = ["", *table_lines]
    else:
        insert_at = next((index for index, line in enumerate(lines) if re.match(r"^##\s+Record Rule\s*$", line, re.IGNORECASE)), len(lines))
        block = ["## Roadmap", "", *table_lines, ""]
        if insert_at and lines[insert_at - 1].strip():
            block.insert(0, "")
        lines[insert_at:insert_at] = block
    return "\n".join(lines).rstrip() + "\n", changes


def parse_roadmap_table(markdown: str) -> tuple[list[str], list[dict[str, str]], int | None, int | None, int | None]:
    lines = markdown.splitlines()
    section_start, section_end = find_section(lines, "Roadmap")
    if section_start is None:
        return [], [], None, None, None
    table_start = next(
        (index for index in range(section_start + 1, section_end) if lines[index].strip().startswith("|")),
        None,
    )
    if table_start is None or table_start + 1 >= section_end:
        return [], [], section_start, None, None
    headers = split_markdown_row(lines[table_start])
    divider = split_markdown_row(lines[table_start + 1])
    if len(headers) != len(divider) or not all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in divider):
        raise ValueError("existing Roadmap table has an invalid header divider")
    rows: list[dict[str, str]] = []
    table_end = table_start + 2
    while table_end < section_end and lines[table_end].strip().startswith("|"):
        cells = split_markdown_row(lines[table_end])
        if len(cells) != len(headers):
            raise ValueError("existing Roadmap table has a malformed row")
        row = dict(zip(headers, cells, strict=True))
        placeholder_id = row.get("Milestone ID", "").strip().upper()
        if not (row.get("Milestone", "").strip().upper() == "TBD" and placeholder_id in {"", "TBD"}):
            rows.append(row)
        table_end += 1
    return headers, rows, section_start, table_start, table_end


def apply_update(
    memory_root: Path,
    update: StatusUpdate,
    ledger_actions: list[dict[str, str]],
    baseline_context: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    record_path = memory_root / "workstreams" / update.workstream_id / "delivery-record.md"
    if not record_path.exists():
        project_action_scope = (
            update.workstream_id in PROJECT_ACTION_IDS
            and update.actions
            and not any(
                [
                    update.status,
                    update.phase,
                    update.progress,
                    update.blockers,
                    update.risks,
                    update.dependencies,
                    update.change_notes,
                ]
            )
        )
        if update.actions and (project_action_scope or not has_wdr_delta(update)):
            timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            daily_log = append_daily_log(memory_root, update, [], timestamp, dry_run)
            gaps = unresolved_gaps(update)
            if not project_action_scope:
                gaps.append(f"delivery-record.md not found for workstream {update.workstream_id}")
            return {
                "ok": True,
                "workstream_id": update.workstream_id,
                "record": str(record_path),
                "daily_log": str(daily_log),
                "changed_fields": [],
                "action_candidates": active_action_summaries(ledger_actions, update.workstream_id),
                "actions_registered": [],
                "actions_updated": [],
                "actions_closed": [],
                "unresolved_gaps": sorted(set(gaps)),
                "no_op": False,
                "wdr_missing": not project_action_scope,
                "project_action_scope": project_action_scope,
            }
        return {
            "ok": False,
            "workstream_id": update.workstream_id,
            "record": str(record_path),
            "error": "delivery-record.md not found",
        }

    original = record_path.read_text(encoding="utf-8")
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if not update.has_reliable_delta():
        daily_log = append_daily_log(memory_root, update, [], timestamp, dry_run)
        return {
            "ok": True,
            "workstream_id": update.workstream_id,
            "record": str(record_path),
            "daily_log": str(daily_log),
            "changed_fields": [],
            "action_candidates": active_action_summaries(ledger_actions, update.workstream_id),
            "actions_registered": [],
            "actions_updated": [],
            "actions_closed": [],
            "unresolved_gaps": unresolved_gaps(update),
            "no_op": True,
        }

    values = update_values(update, timestamp)
    active_summaries = active_action_summaries(ledger_actions, update.workstream_id)
    summary_gaps: list[str] = []
    if update.next_actions or active_summaries or update.actions:
        existing_actions = split_next_actions(existing_field_value(original, "Project Status", "Next actions"))
        existing_actions, summary_gaps = remove_closed_action_summaries(existing_actions, update.actions)
        merged_actions = merge_unique(existing_actions, [*update.next_actions, *active_summaries])
        values["next_actions"] = "; ".join(merged_actions) if merged_actions else "fill missing state"
    if values and "last_status_sync" not in values:
        values["last_status_sync"] = timestamp
    changed_fields: list[dict[str, str]] = []
    updated = original
    for field_name, value in values.items():
        section, label = VOLATILE_FIELDS[field_name]
        updated, old_value, did_change = set_section_bullet(updated, section, label, value)
        if did_change:
            changed_fields.append({"field": label, "before": old_value, "after": value})

    milestone_changes: list[dict[str, str]] = []
    if update.milestones:
        if baseline_context is None:
            raise ValueError("internal error: milestone update has no baseline context")
        updated, milestone_changes = apply_milestone_updates(updated, update.milestones, baseline_context)
        changed_fields.extend(milestone_changes)

    daily_log = append_daily_log(memory_root, update, changed_fields, timestamp, dry_run)
    if changed_fields and not dry_run:
        record_path.write_text(updated, encoding="utf-8", newline="\n")

    return {
        "ok": True,
        "workstream_id": update.workstream_id,
        "record": str(record_path),
        "daily_log": str(daily_log),
        "changed_fields": changed_fields,
        "milestones_updated": [
            item.milestone_id
            for item in update.milestones
            if any(change["field"] == f"Milestone {item.milestone_id}" for change in milestone_changes)
        ],
        "action_candidates": active_summaries or update.next_actions,
        "actions_registered": [],
        "actions_updated": [],
        "actions_closed": [],
        "unresolved_gaps": sorted(set([*unresolved_gaps(update), *summary_gaps])),
    }


def has_wdr_delta(update: StatusUpdate) -> bool:
    return any(
        [
            update.status,
            update.phase,
            update.progress,
            update.blockers,
            update.risks,
            update.dependencies,
            update.change_notes,
            update.next_actions,
            update.milestones,
        ]
    )


def update_values(update: StatusUpdate, timestamp: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if update.status:
        values["status"] = update.status
    if update.phase:
        values["phase"] = update.phase
    if update.progress:
        values["progress"] = update.progress
    if update.blockers:
        values["blockers"] = "; ".join(update.blockers)
    if update.risks:
        values["risks"] = "; ".join(update.risks)
    if update.dependencies:
        values["dependencies"] = "; ".join(update.dependencies)
    if update.change_notes:
        values["change_notes"] = "; ".join(update.change_notes)
    if update.next_actions:
        values["next_actions"] = "; ".join(update.next_actions)
    if update.milestones:
        values["last_status_sync"] = timestamp
    if values:
        values["last_status_sync"] = timestamp
    return values


def set_section_bullet(markdown: str, section_title: str, label: str, value: str) -> tuple[str, str, bool]:
    lines = markdown.splitlines()
    start, end = find_section(lines, section_title)
    bullet = f"- {label}: {value}"
    if start is None:
        insert_at = len(lines)
        if lines and lines[-1].strip():
            lines.append("")
            insert_at += 1
        lines.extend([f"## {section_title}", "", bullet])
        return "\n".join(lines) + "\n", "", True

    pattern = re.compile(rf"^\s*-\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE)
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match:
            old_value = match.group(1).strip()
            if old_value == value:
                return markdown, old_value, False
            lines[index] = bullet
            return "\n".join(lines) + "\n", old_value, True

    insert_at = end
    while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, bullet)
    return "\n".join(lines) + "\n", "", True


def find_section(lines: list[str], title: str) -> tuple[int | None, int]:
    heading = re.compile(rf"^##\s+{re.escape(title)}\s*$", re.IGNORECASE)
    next_heading = re.compile(r"^##\s+")
    for index, line in enumerate(lines):
        if heading.match(line):
            end = len(lines)
            for next_index in range(index + 1, len(lines)):
                if next_heading.match(lines[next_index]):
                    end = next_index
                    break
            return index, end
    return None, len(lines)


def append_daily_log(
    memory_root: Path,
    update: StatusUpdate,
    changed_fields: list[dict[str, str]],
    timestamp: str,
    dry_run: bool,
) -> Path:
    log_date = date.today().isoformat()
    daily_path = memory_root / "daily" / f"{log_date}.md"
    if dry_run:
        return daily_path

    daily_path.parent.mkdir(parents=True, exist_ok=True)
    if daily_path.exists():
        content = daily_path.read_text(encoding="utf-8").rstrip() + "\n\n"
    else:
        content = f"# Daily Log - {log_date}\n\n"

    lines = [
        f"## {timestamp} Status sync - {update.workstream_id}",
        "",
        f"- Source: {update.source}",
        f"- Changed fields: {', '.join(item['field'] for item in changed_fields) if changed_fields else 'no reliable field change'}",
    ]
    if update.progress:
        lines.append(f"- Progress: {update.progress}")
    extend_items(lines, "Blockers", update.blockers)
    extend_items(lines, "Risks", update.risks)
    extend_items(lines, "Dependencies", update.dependencies)
    extend_items(lines, "Change notes", update.change_notes)
    extend_items(lines, "Next actions", update.next_actions)
    if update.actions:
        lines.append("- Actions:")
        for action in update.actions:
            action_ref = action.action_id or action.action
            lines.append(f"  - {action.status}: {action_ref}")
    if update.milestones:
        lines.append("- Milestones:")
        for milestone in update.milestones:
            date_parts = compact_values(
                [
                    f"forecast {milestone.forecast}" if milestone.forecast else "",
                    f"actual {milestone.actual}" if milestone.actual else "",
                ]
            )
            suffix = f" ({', '.join(date_parts)})" if date_parts else ""
            lines.append(f"  - {milestone.milestone_id}: {milestone.status}{suffix}")
            lines.extend(f"    - Evidence: {evidence}" for evidence in milestone.evidence)
    daily_path.write_text(content + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return daily_path


def extend_items(lines: list[str], label: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"- {label}:")
    lines.extend(f"  - {item}" for item in items)


def compact_values(values: list[str]) -> list[str]:
    return [value for value in values if value]


def unresolved_gaps(update: StatusUpdate) -> list[str]:
    gaps = list(update.reported_gaps)
    if not any([update.status, update.progress, update.blockers, update.risks, update.dependencies, update.next_actions, update.actions, update.milestones]):
        gaps.append("status note contained no reliable volatile field update")
    if update.blockers and not update.next_actions:
        gaps.append("blockers were recorded without next actions")
    if update.risks and not update.next_actions:
        gaps.append("risks were recorded without next actions or owner follow-up")
    return gaps


def find_stale(memory_root: Path, max_age_days: int, as_of: date) -> list[dict[str, Any]]:
    workstreams_root = memory_root / "workstreams"
    if not workstreams_root.exists():
        return []
    stale: list[dict[str, Any]] = []
    for record_path in sorted(workstreams_root.glob("*/delivery-record.md")):
        text = record_path.read_text(encoding="utf-8")
        last_sync = extract_last_sync(text)
        item = {
            "workstream_id": record_path.parent.name,
            "record": str(record_path),
            "last_status_sync": last_sync,
        }
        if not last_sync:
            item["reason"] = "missing Last status sync"
            stale.append(item)
            continue
        parsed = parse_date(last_sync)
        if not parsed:
            item["reason"] = "unparseable Last status sync"
            stale.append(item)
            continue
        age_days = (as_of - parsed).days
        item["age_days"] = age_days
        if age_days > max_age_days:
            item["reason"] = f"older than {max_age_days} days"
            stale.append(item)
    return stale


def extract_last_sync(text: str) -> str | None:
    match = re.search(r"^\s*-\s*Last status sync\s*:\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def parse_date(value: str) -> date | None:
    for candidate in [value, value.replace("Z", "+00:00")]:
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def validate_update_targets(memory_root: Path, updates: list[StatusUpdate]) -> None:
    for update in updates:
        record_path = memory_root / "workstreams" / update.workstream_id / "delivery-record.md"
        if record_path.is_file():
            record_path.read_text(encoding="utf-8")
            continue
        project_action_scope = (
            update.workstream_id in PROJECT_ACTION_IDS
            and bool(update.actions)
            and not any(
                [
                    update.status,
                    update.phase,
                    update.progress,
                    update.blockers,
                    update.risks,
                    update.dependencies,
                    update.change_notes,
                ]
            )
        )
        if update.actions and (project_action_scope or not has_wdr_delta(update)):
            continue
        raise ValueError(f"delivery-record.md not found for workstream {update.workstream_id}")


def changed_staged_files(memory_root: Path, staged_root: Path) -> list[Path]:
    changed: list[Path] = []
    for staged_path in sorted(path for path in staged_root.rglob("*") if path.is_file()):
        relative = staged_path.relative_to(staged_root)
        canonical_path = memory_root / relative
        if not canonical_path.is_file() or canonical_path.read_bytes() != staged_path.read_bytes():
            changed.append(relative)
    return changed


def write_temp_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def publish_staged_files(memory_root: Path, staged_root: Path, relatives: list[Path]) -> None:
    originals = {
        relative: (memory_root / relative).read_bytes() if (memory_root / relative).is_file() else None
        for relative in relatives
    }
    prepared: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for relative in relatives:
            prepared[relative] = write_temp_bytes(memory_root / relative, (staged_root / relative).read_bytes())
        for relative in relatives:
            os.replace(prepared[relative], memory_root / relative)
            committed.append(relative)
    except BaseException:
        for relative in reversed(committed):
            canonical_path = memory_root / relative
            original = originals[relative]
            if original is None:
                canonical_path.unlink(missing_ok=True)
            else:
                restore_temp = write_temp_bytes(canonical_path, original)
                os.replace(restore_temp, canonical_path)
        raise
    finally:
        for temp_path in prepared.values():
            temp_path.unlink(missing_ok=True)


def remap_staged_paths(value: Any, staged_root: Path, memory_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: remap_staged_paths(item, staged_root, memory_root) for key, item in value.items()}
    if isinstance(value, list):
        return [remap_staged_paths(item, staged_root, memory_root) for item in value]
    if isinstance(value, str):
        try:
            return str(memory_root / Path(value).resolve().relative_to(staged_root.resolve()))
        except ValueError:
            return value
    return value


def status_sync_receipt(
    *,
    receipt_type: str,
    input_path: Path,
    input_hash: str,
    update_count: int,
    applied_at: str | None,
    dry_run: bool,
    migration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_type": receipt_type,
        "execution_id": f"ssr-{uuid.uuid4().hex}",
        "ok": True,
        "status": "preview" if dry_run else "applied",
        "durable": not dry_run,
        "dry_run": dry_run,
        "input_path": str(input_path),
        "input_hash": input_hash,
        "applied_at": applied_at,
        "mode": "update",
        "update_count": update_count,
    }
    if migration is not None:
        receipt["migration"] = migration
    return receipt


def receipt_relative_path(receipt: dict[str, Any]) -> Path:
    return STATUS_SYNC_RECEIPT_REL / f"{receipt['execution_id']}.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp_path = write_temp_bytes(path, content)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def historical_evidence(
    payload: Any,
    evidence_path: Path,
    input_path: Path,
    input_hash: str,
    update_count: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("evidence-file root must be a JSON object")
    wrapper_fields = sorted(ATTESTATION_WRAPPER_FIELDS.intersection(payload))
    if wrapper_fields:
        raise ValueError(
            "evidence-file is an attestation wrapper, not the original execution report: "
            + ", ".join(wrapper_fields)
        )
    if payload.get("dry_run") is not False:
        raise ValueError("dry-run evidence cannot create a durable migration receipt")
    status = str(payload.get("status") or payload.get("lifecycle_status") or "").strip().lower()
    if payload.get("ok") is not True or (status and status != "applied"):
        raise ValueError("evidence-file does not record a successful status-sync execution")
    if str(payload.get("mode") or "").strip().lower() != "update":
        raise ValueError("evidence-file mode must be update")
    updates = payload.get("updates")
    if not isinstance(updates, list) or not updates:
        raise ValueError("evidence-file must contain non-empty execution updates")
    if len(updates) != update_count:
        raise ValueError("evidence-file execution update count does not match updates-file")
    evidence_input_hash = str(payload.get("input_hash") or "").strip().lower()
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", evidence_input_hash):
        raise ValueError("original evidence report must directly declare the exact updates-file input_hash")
    if evidence_input_hash.removeprefix("sha256:") != input_hash.removeprefix("sha256:"):
        raise ValueError("evidence-file input_hash does not match updates-file raw bytes")
    evidence_input_path = payload.get("input_path")
    if not isinstance(evidence_input_path, str) or not evidence_input_path.strip():
        raise ValueError("original evidence report must directly declare the exact updates-file input_path")
    if Path(evidence_input_path).expanduser().resolve() != input_path:
        raise ValueError("evidence-file input_path does not match the exact updates-file path")
    if evidence_path == input_path:
        raise ValueError("evidence-file must be distinct from updates-file")
    return {
        "evidence_path": str(evidence_path),
        "evidence_hash": sha256_bytes(evidence_path.read_bytes()),
        "evidence_mode": "update",
        "evidence_input_path": str(input_path),
        "evidence_input_hash": input_hash,
        "verification_status": "verified",
    }


def migration_plan_token(
    input_path: Path,
    input_hash: str,
    evidence_path: Path,
    evidence_hash: str,
    applied_at: str,
    attested_by: str,
) -> str:
    identity = {
        "input_path": str(input_path),
        "input_hash": input_hash,
        "evidence_path": str(evidence_path),
        "evidence_hash": evidence_hash,
        "applied_at": applied_at,
        "attested_by": attested_by,
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"status-sync-migration-{digest}"


def run_context(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    config_script = require_file(args.config_script, "config-script")
    config_module = load_python_module(config_script, "adp_status_sync_effective_config")
    code, config = config_module.resolve_effective_config(project_root)
    if code != 0 or not config.get("ok"):
        raise ValueError(str(config.get("error") or "ADP effective config could not be resolved"))

    sources = config.get("sources_checked", [])
    config_path = next((str(item.get("path")) for item in sources if item.get("exists")), None)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    communication_locale = str(config.get("communication_locale") or "en")
    document_locale = str(config.get("document_locale") or "en")
    diagnostics = [str(item) for item in config.get("warnings", [])]
    payload = {
        "ok": True,
        "mode": "context",
        "project_root": str(project_root),
        "config_path": config_path,
        "communication_language": "Chinese" if communication_locale == "zh" else "English",
        "document_output_language": "Chinese" if document_locale == "zh" else "English",
        "language_sources": {
            "communication_language": config.get("value_sources", {}).get("communication_language"),
            "document_output_language": config.get("value_sources", {}).get("document_output_language"),
        },
        "memory_root": str(memory_root),
        "memory_root_exists": memory_root.is_dir(),
        "diagnostics": diagnostics,
    }
    if args.verbose:
        for diagnostic in diagnostics:
            print(diagnostic, file=sys.stderr)
    emit(payload, args.output)
    return 0


def run_update(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    input_path = require_file(args.updates_file, "updates-file") if args.updates_file else None
    input_hash = sha256_bytes(input_path.read_bytes()) if input_path else None
    if input_path:
        args.updates_file = str(input_path)
    updates = updates_from_args(args)
    baseline_context = validate_milestone_updates(memory_root, updates)
    validate_update_targets(memory_root, updates)
    memory_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".status-sync-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        if memory_root.is_dir():
            shutil.copytree(memory_root, staged_root)
        else:
            staged_root.mkdir(parents=True)
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        action_updates = [action for update in updates for action in update.actions]
        staged_ledger_path = staged_root / ACTION_LEDGER_REL
        if action_updates:
            staged_ledger_path = ensure_action_ledger(staged_root, False)
            ledger_result = upsert_actions(staged_ledger_path, action_updates, timestamp, False)
        else:
            ledger_result = {
                "rows": parse_action_ledger(staged_ledger_path),
                "actions_registered": [],
                "actions_updated": [],
                "actions_closed": [],
                "unresolved_gaps": [],
            }
        staged_action_flow_path = staged_root / "views/action-flow.json"
        if staged_ledger_path.is_file():
            staged_action_flow_path.parent.mkdir(parents=True, exist_ok=True)
            staged_action_flow_path.write_text(
                json.dumps(build_action_flow_contract(ledger_result["rows"], staged_ledger_path), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        ledger_rows = ledger_result["rows"]
        hydrate_action_updates_from_ledger(updates, ledger_rows)
        results = [apply_update(staged_root, update, ledger_rows, baseline_context, False) for update in updates]
        for result, update in zip(results, updates, strict=True):
            update_action_ids = {action.action_id for action in update.actions if action.action_id}
            update_keys = {
                action_key(action.owner, action.workstream, action.action, action.source)
                for action in update.actions
            }
            result["actions_registered"] = related_action_ids(
                ledger_rows, ledger_result["actions_registered"], update_action_ids, update_keys
            )
            result["actions_updated"] = related_action_ids(
                ledger_rows, ledger_result["actions_updated"], update_action_ids, update_keys
            )
            result["actions_closed"] = related_action_ids(
                ledger_rows, ledger_result["actions_closed"], update_action_ids, update_keys
            )
            result["unresolved_gaps"] = sorted(
                set([*result.get("unresolved_gaps", []), *ledger_result["unresolved_gaps"]])
            )
        errors = [item for item in results if not item.get("ok")]
        if errors:
            raise ValueError("; ".join(str(item.get("error") or "status update failed") for item in errors))
        receipt = None
        receipt_rel = None
        if input_path and input_hash:
            receipt = status_sync_receipt(
                receipt_type="execution",
                input_path=input_path,
                input_hash=input_hash,
                update_count=len(updates),
                applied_at=None if args.dry_run else timestamp,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                receipt_rel = receipt_relative_path(receipt)
                receipt_path = staged_root / receipt_rel
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                receipt_path.write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
        changed = changed_staged_files(memory_root, staged_root)
        if not args.dry_run:
            publish_staged_files(memory_root, staged_root, changed)
        results = remap_staged_paths(results, staged_root, memory_root)
    ledger_path = memory_root / ACTION_LEDGER_REL
    action_flow_path = memory_root / "views/action-flow.json"
    payload = {
        "ok": True,
        "mode": "update",
        "dry_run": args.dry_run,
        "input_path": str(input_path) if input_path else None,
        "input_hash": input_hash,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "baseline_path": str(baseline_context["path"]) if baseline_context else None,
        "baseline_revision": baseline_context["revision"] if baseline_context else None,
        "action_ledger": str(ledger_path),
        "action_flow": str(action_flow_path) if action_flow_path.is_file() or args.dry_run else None,
        "actions_registered": ledger_result["actions_registered"],
        "actions_updated": ledger_result["actions_updated"],
        "actions_closed": ledger_result["actions_closed"],
        "unresolved_gaps": ledger_result["unresolved_gaps"],
        "updates": results,
        "receipt": receipt,
        "receipt_path": str(memory_root / receipt_rel) if receipt_rel else None,
    }
    emit(payload, args.output)
    return 0


def run_migrate_receipt(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    input_path = require_file(args.updates_file, "updates-file")
    evidence_path = require_file(args.evidence_file, "evidence-file")
    input_bytes = input_path.read_bytes()
    input_hash = sha256_bytes(input_bytes)
    try:
        input_payload = json.loads(input_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"updates-file must contain valid JSON: {exc}") from exc
    if not isinstance(input_payload, dict) or not isinstance(input_payload.get("updates"), list) or not input_payload["updates"]:
        raise ValueError("updates-file must contain a non-empty 'updates' list")
    try:
        evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"evidence-file must contain valid JSON: {exc}") from exc
    attested_by = " ".join(args.attested_by.split())
    if not attested_by:
        raise ValueError("attested-by must not be empty")
    applied_at = normalize_required_timestamp(args.applied_at, "applied-at")
    try:
        migration = historical_evidence(
            evidence_payload,
            evidence_path,
            input_path,
            input_hash,
            len(input_payload["updates"]),
        )
    except ValueError as exc:
        if not args.dry_run:
            raise
        emit(
            {
                "ok": True,
                "mode": "migrate-receipt",
                "dry_run": True,
                "verification_status": "unverified",
                "reason": str(exc),
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "input_path": str(input_path),
                "input_hash": input_hash,
                "evidence_path": str(evidence_path),
                "receipt": None,
                "receipt_path": None,
            },
            args.output,
        )
        return 0
    migration["attested_by"] = attested_by
    plan_token = migration_plan_token(
        input_path,
        input_hash,
        evidence_path,
        str(migration["evidence_hash"]),
        applied_at,
        attested_by,
    )
    if not args.dry_run and args.verified_plan_token != plan_token:
        raise ValueError("durable migration requires the verified-plan-token from an unchanged dry-run")
    receipt = status_sync_receipt(
        receipt_type="migration",
        input_path=input_path,
        input_hash=input_hash,
        update_count=len(input_payload["updates"]),
        applied_at=None if args.dry_run else applied_at,
        dry_run=args.dry_run,
        migration=migration,
    )
    receipt_path = None if args.dry_run else memory_root / receipt_relative_path(receipt)
    if receipt_path is not None:
        write_json_atomic(receipt_path, receipt)
    if args.verbose and receipt_path is not None:
        print(f"Wrote migration receipt: {receipt_path}", file=sys.stderr)
    emit(
        {
            "ok": True,
            "mode": "migrate-receipt",
            "dry_run": args.dry_run,
            "verification_status": "verified",
            "verified_plan_token": plan_token,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "receipt": receipt,
            "receipt_path": str(receipt_path) if receipt_path is not None else None,
        },
        args.output,
    )
    return 0


def hydrate_action_updates_from_ledger(updates: list[StatusUpdate], rows: list[dict[str, str]]) -> None:
    by_id = {row.get("Action ID", ""): row for row in rows if row.get("Action ID")}
    for update in updates:
        for action in update.actions:
            if not action.action_id:
                continue
            row = by_id.get(action.action_id)
            if not row:
                continue
            if not action.action:
                action.action = row.get("Action", "")
            if action.owner == "TBD":
                action.owner = row.get("Owner", "TBD")
            if action.workstream == update.workstream_id:
                action.workstream = row.get("Workstream", update.workstream_id)


def related_action_ids(
    rows: list[dict[str, str]],
    ids: list[str],
    requested_ids: set[str | None],
    requested_keys: set[str],
) -> list[str]:
    id_set = set(ids)
    related: list[str] = []
    for row in rows:
        action_id = row.get("Action ID", "")
        if action_id not in id_set:
            continue
        row_key = action_key(row.get("Owner", ""), row.get("Workstream", ""), row.get("Action", ""), row.get("Source", ""))
        if action_id in requested_ids or row_key in requested_keys:
            related.append(action_id)
    return related


def run_stale(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    stale = find_stale(memory_root, args.max_age_days, as_of)
    payload = {
        "ok": True,
        "mode": "stale",
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "max_age_days": args.max_age_days,
        "as_of": as_of.isoformat(),
        "stale_workstreams": stale,
    }
    emit(payload, args.output)
    return 0


def emit(payload: dict[str, Any], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "context":
            return run_context(args)
        if args.command == "update":
            return run_update(args)
        if args.command == "stale":
            return run_stale(args)
        if args.command == "migrate-receipt":
            return run_migrate_receipt(args)
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        emit(payload, getattr(args, "output", None))
        return 2


if __name__ == "__main__":
    sys.exit(main())
