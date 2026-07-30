#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Apply lightweight ADP status updates to Workstream Delivery Records."""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

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
ACTION_LEDGER_STATE_REL = Path("actions") / "action-ledger.state.json"
ACTION_PROJECTION_REL = "action-projection.json"
BASELINE_REL = Path("plans") / "program-baseline.md"
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
ACTION_STATUSES = {"open", "in-progress", "blocked", "done", "cancelled"}
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
STATUS_INTENT_FIELDS = {
    "status",
    "phase",
    "progress",
    "blockers",
    "risks",
    "dependencies",
    "change_notes",
    "refresh_actions",
}
MILESTONE_STATUSES = {"planned", "in-progress", "at-risk", "done", "blocked"}
RECEIPT_SCHEMA_VERSION = 1
STATUS_SYNC_RECEIPT_REL = Path("receipts") / "status-sync"
REPAIR_TOKEN_REL = Path("state") / "repair-tokens"
REPAIR_RECEIPT_REL = Path("receipts") / "repair"
REPAIR_ATTEMPT_LEDGER_REL = Path("state") / "repair-attempt-ledger.json"
REPAIR_RECEIPT_INDEX_REL = Path("state") / "repair-receipt-index.json"
TRANSACTION_REL = Path("state") / "transactions"
FACT_LOCK_REL = Path("state") / "fact-write.lock"
WINDOWS_LOCK_RETRY_SECONDS = 0.05
WINDOWS_LOCK_CONTENTION_ERRORS = {
    error
    for error in (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", None))
    if error is not None
}
WINDOWS_LOCK_CONTENTION_WINERRORS = {33, 36}
CONTRACT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "assets/panel-sync-contracts.schema.json"
CONTRACT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "assets/CONTRACT-REGISTRY.json"
ATTESTATION_WRAPPER_FIELDS = {
    "attestation",
    "attested_at",
    "attested_by",
    "execution_report",
    "original_report",
    "wrapper_attestation",
}
DEFAULT_CONFIG_SCRIPT = Path(__file__).resolve().parents[2] / "adp-plan-baseline/scripts/adp_effective_config.py"
DEFAULT_SCOPE_CONTRACT_SCRIPT = Path(__file__).resolve().parents[2] / "adp-plan-baseline/scripts/scope_contract.py"
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
    "Closure Criteria Verifiable",
    "Created At",
    "Started At",
    "Done At",
    "Cancelled At",
    "Baseline Revision",
    "Related Plan Items",
    "Related Flow Edges",
    "Last Updated",
    "Owning Workflow",
    "Action Revision",
]

ACTION_PATCH_FIELDS = {
    "status",
    "owner",
    "workstream",
    "affected_workstreams",
    "action",
    "source",
    "reason",
    "due_or_trigger",
    "closure_criteria",
    "closure_criteria_verifiable",
    "created_at",
    "started_at",
    "done_at",
    "cancelled_at",
    "baseline_revision",
    "related_plan_item_ids",
    "related_flow_edge_ids",
    "owning_workflow",
}


class StatusSyncContractError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


@contextmanager
def fact_write_lock(memory_root: Path):
    lock_path = memory_root / FACT_LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        acquire_file_lock(handle)
        try:
            yield
        finally:
            release_file_lock(handle)


def acquire_file_lock(handle: BinaryIO) -> None:
    if sys.platform != "win32":
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if (
                exc.errno not in WINDOWS_LOCK_CONTENTION_ERRORS
                and getattr(exc, "winerror", None) not in WINDOWS_LOCK_CONTENTION_WINERRORS
            ):
                raise
            time.sleep(WINDOWS_LOCK_RETRY_SECONDS)


def release_file_lock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_SCOPE_CONTRACT_MODULE: Any | None = None


@dataclass
class ActionUpdate:
    operation: str = "create"
    command_id: str | None = None
    action_id: str | None = None
    resolved_action_id: str | None = None
    expected_revision: int | None = None
    status: str | None = "open"
    owner: str | None = "TBD"
    workstream: str | None = "TBD"
    affected_workstreams: list[str] | None = field(default_factory=list)
    action: str | None = ""
    source: str | None = ""
    reason: str | None = ""
    due_or_trigger: str | None = "TBD"
    closure_criteria: str | None = "TBD"
    closure_criteria_verifiable: bool | None = None
    owning_workflow: str | None = "adp-status-sync"
    created_at: str | None = None
    started_at: str | None = None
    done_at: str | None = None
    cancelled_at: str | None = None
    baseline_revision: int | None = None
    related_plan_item_ids: list[str] | None = None
    related_flow_edge_ids: list[str] | None = None
    present_fields: set[str] = field(default_factory=set)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_observed_at: list[str] = field(default_factory=list)


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
    next_actions_provided: bool = False
    refresh_actions: bool = False
    actions: list[ActionUpdate] = field(default_factory=list)
    milestones: list[MilestoneUpdate] = field(default_factory=list)
    reported_gaps: list[str] = field(default_factory=list)
    source: str = "status sync"
    consumed_intent_ids: list[str] = field(default_factory=list)
    consumed_intents: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_fields_present: set[str] = field(default_factory=set)

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
                self.next_actions_provided,
                self.refresh_actions,
                self.actions,
                self.milestones,
                self.consumed_intent_ids,
                self.consumed_intents,
                self.current_fields_present,
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
    update.add_argument(
        "--refresh-actions",
        action="store_true",
        help="Explicitly replace ledger-projected action summaries for the target physical workstream.",
    )
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

    repair = subparsers.add_parser(
        "repair",
        help="Dry-run or apply one exact state-audit action-projection repair batch.",
    )
    repair.add_argument("project_root", help="Project root containing ADP memory.")
    repair.add_argument("--audit-json", required=True, help="State-audit JSON containing repair_contract.")
    repair.add_argument("--batch-id", required=True, help="Exact repair_batch_id from the audit.")
    repair.add_argument("--dry-run", action="store_true", help="Validate current facts and issue a single-use apply token.")
    repair.add_argument("--token", help="Single-use token returned by a successful repair dry-run.")
    repair.add_argument("--principal", default="adp-status-sync", help="Stable operator or automation principal.")
    repair.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    repair.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    repair.add_argument("--fail-after-stage", action="store_true", help=argparse.SUPPRESS)
    repair.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

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


def scope_contract_module() -> Any:
    global _SCOPE_CONTRACT_MODULE
    if _SCOPE_CONTRACT_MODULE is None:
        _SCOPE_CONTRACT_MODULE = load_python_module(
            DEFAULT_SCOPE_CONTRACT_SCRIPT,
            "adp_status_sync_scope_contract",
        )
    return _SCOPE_CONTRACT_MODULE


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
        if isinstance(payload, dict) and not isinstance(items, list):
            items = status_batch_updates(payload)
        payload_revision = parse_optional_revision(payload.get("baseline_revision"), "baseline_revision") if isinstance(payload, dict) else None
        payload_refresh_actions = boolean_value(payload.get("refresh_actions", False), "refresh_actions") if isinstance(payload, dict) else False
        if not isinstance(items, list):
            raise ValueError("updates-file must contain a list or an object with an 'updates' list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each batch update must be a JSON object")
            updates.append(
                update_from_mapping(
                    item,
                    default_source=args.source,
                    default_revision=payload_revision,
                    default_refresh_actions=payload_refresh_actions,
                )
            )
        if isinstance(payload, dict):
            bind_status_intents(updates, payload.get("status_intents", []))

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
            args.refresh_actions,
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
                next_actions_provided=bool(args.next_action),
                refresh_actions=bool(args.refresh_actions),
                actions=[],
                milestones=milestones_from_cli(args),
                source=args.source,
                current_fields_present={
                    field_name
                    for field_name, supplied in {
                        "status": args.status is not None,
                        "phase": args.phase is not None,
                        "progress": args.progress is not None,
                        "blockers": bool(args.blocker),
                        "risks": bool(args.risk),
                        "dependencies": bool(args.dependency),
                        "change_notes": bool(args.change_note),
                    }.items()
                    if supplied
                },
            )
        )

    if not updates:
        raise ValueError("provide --id with status fields or --updates-file")
    return updates


def status_batch_updates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    raw_intents = payload.get("status_intents", [])
    if raw_intents is None:
        raw_intents = []
    if not isinstance(raw_intents, list):
        raise TypeError("status_intents must be a list")
    for intent in raw_intents:
        if not isinstance(intent, dict):
            raise TypeError("status_intents entries must be objects")
        workstream_id = normalize_id(str(intent.get("workstream_id") or ""))
        patch = intent.get("set")
        if not isinstance(patch, dict) or not patch:
            raise ValueError(f"status intent {intent.get('intent_id')} set must be a non-empty object")
        update = grouped.setdefault(workstream_id, {"id": workstream_id, "source": "status intent", "actions": []})
        for field_name, value in patch.items():
            if field_name in update and update[field_name] != value:
                raise ValueError(f"conflicting status intents for {workstream_id}.{field_name}")
            update[field_name] = value

    raw_commands = payload.get("action_commands", [])
    if raw_commands is None:
        raw_commands = []
    if not isinstance(raw_commands, list):
        raise TypeError("action_commands must be a list")
    for command in raw_commands:
        if not isinstance(command, dict):
            raise TypeError("action_commands entries must be objects")
        operation = str(command.get("operation") or "").lower()
        action_set = command.get("create") if operation == "create" else command.get("set")
        if not isinstance(action_set, dict):
            action_set = command
        routing_scope = str(
            action_set.get("routing_scope_id")
            or action_set.get("workstream")
            or command.get("workstream")
            or "program"
        )
        workstream_id = normalize_id(routing_scope)
        action = {
            **action_set,
            "operation": operation,
            "command_id": command.get("command_id"),
            "action_id": command.get("action_id"),
            "expected_action_revision": command.get("expected_revision", command.get("expected_action_revision")),
            "evidence": command.get("evidence", []),
        }
        if "due_trigger" in action and "due_or_trigger" not in action:
            action["due_or_trigger"] = action.pop("due_trigger")
        if "routing_scope_id" in action and "workstream" not in action:
            action["workstream"] = action.pop("routing_scope_id")
        grouped.setdefault(workstream_id, {"id": workstream_id, "source": "action command", "actions": []})[
            "actions"
        ].append(action)
    if not grouped:
        raise ValueError("status-sync batch contains no updates, status intents, or action commands")
    return [grouped[key] for key in sorted(grouped)]


def bind_status_intents(updates: list[StatusUpdate], raw_intents: Any) -> None:
    if raw_intents in (None, []):
        return
    if not isinstance(raw_intents, list):
        raise TypeError("status_intents must be a list")
    by_workstream = {update.workstream_id: update for update in updates}
    seen: set[str] = set()
    for intent in raw_intents:
        if not isinstance(intent, dict):
            raise TypeError("status_intents entries must be objects")
        intent_id = clean_optional(intent.get("intent_id"))
        workstream_id = normalize_id(str(intent.get("workstream_id") or ""))
        if not intent_id or intent_id in seen:
            raise ValueError("status_intents require unique non-empty intent_id values")
        seen.add(intent_id)
        if workstream_id not in by_workstream:
            raise ValueError(f"status intent {intent_id} has no matching workstream update")
        intent_set = intent.get("set")
        if not isinstance(intent_set, dict) or not intent_set:
            raise ValueError(f"status intent {intent_id} set must be a non-empty object")
        unknown = sorted(set(intent_set) - STATUS_INTENT_FIELDS)
        if unknown:
            raise ValueError(
                f"status intent {intent_id} contains unsupported fields: {', '.join(unknown)}"
            )
        update = by_workstream[workstream_id]
        for field_name, expected in intent_set.items():
            if field_name == "refresh_actions":
                supplied = update.refresh_actions
            else:
                if field_name not in update.current_fields_present:
                    raise ValueError(
                        f"status intent {intent_id} field {field_name} is absent from its StatusUpdate"
                    )
                supplied = getattr(update, field_name)
            if supplied != expected:
                raise ValueError(
                    f"status intent {intent_id} field {field_name} does not match its StatusUpdate"
                )
        update.consumed_intent_ids.append(intent_id)
        update.consumed_intents[intent_id] = dict(intent)
    for update in updates:
        update.consumed_intent_ids.sort()


def update_from_mapping(
    item: dict[str, Any],
    default_source: str,
    default_revision: int | None = None,
    default_refresh_actions: bool = False,
) -> StatusUpdate:
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
        next_actions_provided="next_actions" in item or "nextActions" in item,
        refresh_actions=boolean_value(
            item.get("refresh_actions", item.get("refreshActions", default_refresh_actions)),
            "refresh_actions",
        ),
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
        current_fields_present={
            field_name
            for field_name, aliases in {
                "status": ("status",),
                "phase": ("phase",),
                "progress": ("progress",),
                "blockers": ("blockers",),
                "risks": ("risks",),
                "dependencies": ("dependencies",),
                "change_notes": ("change_notes", "changeNotes"),
            }.items()
            if has_any_key(item, *aliases)
        },
    )


def boolean_value(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false")
    return value


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
        raw_operation = clean_optional(raw_action.get("operation"))
        operation = (raw_operation or ("patch" if action_id else "create")).lower()
        if operation not in {"create", "patch"}:
            raise ValueError("action operation must be create or patch")
        if operation == "patch" and not action_id:
            raise ValueError("action patch requires exact action_id")
        typed_v2 = raw_operation is not None or str(raw_action.get("schema_version") or "").strip() == "2.0.0"
        expected_revision = parse_optional_revision(
            raw_action.get("expected_action_revision", raw_action.get("expected_revision")),
            f"action {action_id or '(create)'} expected_action_revision",
        )
        if typed_v2 and operation == "patch" and expected_revision is None:
            raise ValueError("typed action patch requires expected_action_revision")

        status = normalize_action_status(raw_action.get("status")) if "status" in raw_action else None
        if operation == "create" and status is None:
            status = "open"
        if status in {"done", "cancelled"} and not action_id:
            raise ValueError(f"{status} action update requires action_id")
        action_text = clean_optional(first_present(raw_action, "action", "text", "next_action"))
        if operation == "create" and not action_text:
            raise ValueError("action update is missing action/text or action_id")
        affected_present = has_any_key(raw_action, "affected_workstreams", "affectedWorkstreams", "impacts")
        affected_workstreams = (
            normalize_workstream_list(first_present(raw_action, "affected_workstreams", "affectedWorkstreams", "impacts"))
            if affected_present
            else None
        )
        raw_workstream = clean_optional(first_present(raw_action, "workstream", "workstream_id"))
        if raw_workstream and raw_workstream.upper() != "TBD":
            workstream = normalize_id(raw_workstream)
        elif affected_workstreams and len(affected_workstreams) > 1:
            workstream = "program"
        elif affected_workstreams:
            workstream = affected_workstreams[0]
        elif operation == "patch":
            workstream = None
        else:
            workstream = default_workstream

        present_fields: set[str] = set()
        aliases = {
            "status": ("status",),
            "owner": ("owner",),
            "workstream": ("workstream", "workstream_id"),
            "affected_workstreams": ("affected_workstreams", "affectedWorkstreams", "impacts"),
            "action": ("action", "text", "next_action"),
            "source": ("source",),
            "reason": ("reason",),
            "due_or_trigger": ("due_or_trigger", "due", "trigger"),
            "closure_criteria": ("closure_criteria",),
            "closure_criteria_verifiable": ("closure_criteria_verifiable",),
            "created_at": ("created_at",),
            "started_at": ("started_at",),
            "done_at": ("done_at",),
            "cancelled_at": ("cancelled_at",),
            "baseline_revision": ("baseline_revision",),
            "related_plan_item_ids": ("related_plan_item_ids",),
            "related_flow_edge_ids": ("related_flow_edge_ids",),
            "owning_workflow": ("owning_workflow",),
        }
        for field_name, field_aliases in aliases.items():
            if has_any_key(raw_action, *field_aliases):
                present_fields.add(field_name)
        if operation == "create":
            present_fields = set(ACTION_PATCH_FIELDS)

        evidence = raw_action.get("evidence", [])
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            raise TypeError("action evidence must be a list")
        normalized_evidence: list[dict[str, Any]] = []
        evidence_observed_at: list[str] = []
        for evidence_item in evidence:
            if not isinstance(evidence_item, dict):
                raise TypeError("action evidence entries must be objects")
            if not evidence_item:
                raise ValueError("action evidence entries must be non-empty objects")
            normalized_item = dict(evidence_item)
            if normalized_item.get("observed_at"):
                observed_at = clean_iso_timestamp(normalized_item["observed_at"], "action evidence observed_at")
                if observed_at:
                    normalized_item["observed_at"] = observed_at
                    evidence_observed_at.append(observed_at)
            normalized_evidence.append(normalized_item)

        command_id = clean_optional(raw_action.get("command_id"))
        if command_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", command_id):
            raise ValueError("action command_id must be a stable path-safe identifier")
        if typed_v2 and not command_id:
            raise ValueError("typed action command requires stable command_id")
        if typed_v2 and not action_id:
            raise ValueError("typed action command requires stable action_id")
        if typed_v2 and not normalized_evidence:
            raise ValueError("typed action command requires non-empty evidence")
        actions.append(
            ActionUpdate(
                operation=operation,
                command_id=command_id,
                action_id=action_id,
                expected_revision=expected_revision,
                status=status,
                owner=(clean_optional(raw_action.get("owner")) if operation == "patch" else clean_optional(raw_action.get("owner")) or "TBD"),
                workstream=workstream if operation == "patch" else workstream or "TBD",
                affected_workstreams=affected_workstreams,
                action=action_text,
                source=(
                    clean_optional(raw_action.get("source"))
                    if operation == "patch"
                    else clean_optional(raw_action.get("source")) or clean_optional(item.get("source")) or default_source
                ),
                reason=(clean_optional(raw_action.get("reason")) if operation == "patch" else clean_optional(raw_action.get("reason")) or "TBD"),
                due_or_trigger=(
                    clean_optional(first_present(raw_action, "due_or_trigger", "due", "trigger"))
                    if operation == "patch"
                    else clean_optional(first_present(raw_action, "due_or_trigger", "due", "trigger")) or "TBD"
                ),
                closure_criteria=(
                    clean_optional(raw_action.get("closure_criteria"))
                    if operation == "patch"
                    else clean_optional(raw_action.get("closure_criteria")) or "TBD"
                ),
                closure_criteria_verifiable=parse_optional_boolean(
                    raw_action.get("closure_criteria_verifiable"),
                    "action closure_criteria_verifiable",
                ),
                owning_workflow=(
                    clean_optional(raw_action.get("owning_workflow"))
                    if operation == "patch"
                    else clean_optional(raw_action.get("owning_workflow")) or "adp-status-sync"
                ),
                created_at=clean_iso_timestamp(raw_action.get("created_at"), "action created_at"),
                started_at=clean_iso_timestamp(raw_action.get("started_at"), "action started_at"),
                done_at=clean_iso_timestamp(raw_action.get("done_at"), "action done_at"),
                cancelled_at=clean_iso_timestamp(raw_action.get("cancelled_at"), "action cancelled_at"),
                baseline_revision=(
                    parse_optional_revision(raw_action.get("baseline_revision"), "action baseline_revision")
                    if operation == "patch"
                    else parse_optional_revision(raw_action.get("baseline_revision"), "action baseline_revision") or default_revision
                ),
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
                present_fields=present_fields,
                evidence=normalized_evidence,
                evidence_observed_at=sorted(evidence_observed_at),
            )
        )
    return actions


def has_any_key(mapping: dict[str, Any], *keys: str) -> bool:
    return any(key in mapping for key in keys)


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


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


def parse_optional_boolean(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean when supplied")
    return value


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
        if normalized.get("Action ID") and not normalized.get("Action Revision"):
            normalized["Action Revision"] = "1"
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
    no_op: list[str] = []
    gaps: list[str] = []

    for action_update in action_updates:
        validate_action_update(action_update)
        match = find_action_row(rows, action_update)
        if match is None:
            if action_update.operation == "patch":
                ref = action_update.action_id or action_update.action or "(missing action)"
                if action_update.expected_revision is not None:
                    raise StatusSyncContractError("ACTION_NOT_FOUND", f"{ref}: action patch target was not found")
                gaps.append(f"{ref}: close/update action was not found in ledger")
                continue
            new_row = new_action_row(rows, action_update, timestamp)
            validate_action_row_chronology(new_row)
            rows.append(new_row)
            action_update.resolved_action_id = new_row["Action ID"]
            registered.append(new_row["Action ID"])
            gaps.extend(action_gaps(new_row))
            continue

        if action_update.operation == "create":
            raise StatusSyncContractError(
                "ACTION_CREATE_CONFLICT",
                f"action create target {action_update.action_id} already exists",
            )
        before_status = match.get("Status", "")
        action_update.resolved_action_id = match.get("Action ID")
        before_revision = action_revision(match)
        if action_update.expected_revision is not None and action_update.expected_revision != before_revision:
            raise StatusSyncContractError(
                "ACTION_REVISION_CONFLICT",
                f"action {action_update.action_id} expected revision {action_update.expected_revision}, found {before_revision}",
            )
        validate_action_evidence_freshness(match, action_update)
        after_status = action_update.status if "status" in action_update.present_fields else before_status.lower()
        validate_action_transition(before_status, after_status or before_status.lower(), action_update.action_id or match.get("Action ID", ""))
        changed = merge_action_row(match, action_update, timestamp)
        action_id = match.get("Action ID", "")
        if not changed:
            no_op.append(action_id)
        elif match["Status"] in {"done", "cancelled"} and before_status != match["Status"]:
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
        "actions_no_op": no_op,
        "unresolved_gaps": sorted(set(gaps)),
    }


def validate_action_update(action_update: ActionUpdate) -> None:
    if action_update.operation == "patch":
        if not action_update.present_fields:
            raise ValueError(f"action {action_update.action_id} patch set must not be empty")
        nullable_fields = {
            "closure_criteria_verifiable",
            "created_at",
            "started_at",
            "done_at",
            "cancelled_at",
            "baseline_revision",
            "related_plan_item_ids",
            "related_flow_edge_ids",
            "affected_workstreams",
        }
        for field_name in action_update.present_fields - nullable_fields:
            if clean_optional(getattr(action_update, field_name)) is None:
                raise ValueError(f"action {action_update.action_id} patch field {field_name} must be non-empty")
    if action_update.operation == "create" and not clean_optional(action_update.action):
        raise ValueError("action create requires action text")


def action_revision(row: dict[str, str]) -> int:
    raw = row.get("Action Revision", "").strip()
    if not raw:
        return 1
    if not raw.isdigit() or int(raw) < 1:
        raise StatusSyncContractError("ACTION_REVISION_INVALID", f"action {row.get('Action ID', '')} has invalid revision")
    return int(raw)


def validate_action_evidence_freshness(row: dict[str, str], action_update: ActionUpdate) -> None:
    if not action_update.evidence_observed_at:
        return
    last_updated = clean_iso_timestamp(row.get("Last Updated"), f"action {row.get('Action ID', '')} last_updated")
    if last_updated and max(action_update.evidence_observed_at) < last_updated:
        raise StatusSyncContractError(
            "ACTION_EVIDENCE_STALE",
            f"action {row.get('Action ID', '')} evidence predates Last Updated",
        )


def find_action_row(rows: list[dict[str, str]], action_update: ActionUpdate) -> dict[str, str] | None:
    if not action_update.action_id:
        return None
    for row in rows:
        if row.get("Action ID") == action_update.action_id:
            return row
    return None


def normalize_text_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def new_action_row(rows: list[dict[str, str]], action_update: ActionUpdate, timestamp: str) -> dict[str, str]:
    action_id = action_update.action_id or next_action_id(rows, timestamp)
    status = action_update.status or "open"
    started_at = action_update.started_at or (
        timestamp if status in {"in-progress", "blocked", "done"} else ""
    )
    done_at = action_update.done_at or (timestamp if status == "done" else "")
    cancelled_at = action_update.cancelled_at or (timestamp if status == "cancelled" else "")
    return {
        "Action ID": action_id,
        "Status": status,
        "Owner": action_update.owner or "TBD",
        "Workstream": action_update.workstream or "TBD",
        "Affected Workstreams": action_workstreams_cell(action_update),
        "Action": action_update.action,
        "Source": action_update.source or "TBD",
        "Reason": action_update.reason or "TBD",
        "Due / Trigger": action_update.due_or_trigger or "TBD",
        "Closure Criteria": action_update.closure_criteria or "TBD",
        "Closure Criteria Verifiable": (
            str(action_update.closure_criteria_verifiable).lower()
            if action_update.closure_criteria_verifiable is not None
            else ""
        ),
        "Created At": action_update.created_at or timestamp,
        "Started At": started_at,
        "Done At": done_at,
        "Cancelled At": cancelled_at,
        "Baseline Revision": str(action_update.baseline_revision or ""),
        "Related Plan Items": "; ".join(action_update.related_plan_item_ids or []),
        "Related Flow Edges": "; ".join(action_update.related_flow_edge_ids or []),
        "Last Updated": timestamp,
        "Owning Workflow": action_update.owning_workflow or "adp-status-sync",
        "Action Revision": "1",
    }


def merge_action_row(row: dict[str, str], action_update: ActionUpdate, timestamp: str) -> bool:
    before = dict(row)
    fields = action_update.present_fields
    if "status" in fields and action_update.status:
        row["Status"] = action_update.status
    if "owner" in fields:
        assign_if_meaningful(row, "Owner", action_update.owner or "")
    if "workstream" in fields:
        assign_if_meaningful(row, "Workstream", action_update.workstream or "")
    if "affected_workstreams" in fields:
        values = action_update.affected_workstreams or []
        row["Affected Workstreams"] = "; ".join(values) if values else "TBD"
    if "action" in fields:
        assign_if_present(row, "Action", action_update.action or "")
    if "source" in fields:
        assign_if_meaningful(row, "Source", action_update.source or "")
    if "reason" in fields:
        assign_if_meaningful(row, "Reason", action_update.reason or "")
    if "due_or_trigger" in fields:
        assign_if_meaningful(row, "Due / Trigger", action_update.due_or_trigger or "")
    if "closure_criteria" in fields:
        assign_if_meaningful(row, "Closure Criteria", action_update.closure_criteria or "")
    if "closure_criteria_verifiable" in fields and action_update.closure_criteria_verifiable is not None:
        row["Closure Criteria Verifiable"] = str(action_update.closure_criteria_verifiable).lower()
    if not row.get("Created At"):
        row["Created At"] = action_update.created_at or row.get("Last Updated") or timestamp
    if "created_at" in fields and action_update.created_at:
        row["Created At"] = action_update.created_at
    if "status" in fields and action_update.status in {"in-progress", "blocked", "done"} and not row.get("Started At"):
        row["Started At"] = action_update.started_at or timestamp
    elif "started_at" in fields and action_update.started_at:
        row["Started At"] = action_update.started_at
    if "status" in fields and action_update.status == "done":
        row["Done At"] = action_update.done_at or timestamp
    elif "done_at" in fields and action_update.done_at:
        row["Done At"] = action_update.done_at
    if "status" in fields and action_update.status == "cancelled":
        row["Cancelled At"] = action_update.cancelled_at or timestamp
    elif "cancelled_at" in fields and action_update.cancelled_at:
        row["Cancelled At"] = action_update.cancelled_at
    if "baseline_revision" in fields and action_update.baseline_revision is not None:
        row["Baseline Revision"] = str(action_update.baseline_revision)
    if "related_plan_item_ids" in fields and action_update.related_plan_item_ids is not None:
        row["Related Plan Items"] = "; ".join(action_update.related_plan_item_ids)
    if "related_flow_edge_ids" in fields and action_update.related_flow_edge_ids is not None:
        row["Related Flow Edges"] = "; ".join(action_update.related_flow_edge_ids)
    if "owning_workflow" in fields:
        assign_if_meaningful(row, "Owning Workflow", action_update.owning_workflow or "")
    business_fields = [field for field in ACTION_FIELDS if field not in {"Last Updated", "Action Revision"}]
    changed = any(before.get(field, "") != row.get(field, "") for field in business_fields)
    if not changed:
        row.clear()
        row.update(before)
        return False
    row["Last Updated"] = timestamp
    row["Action Revision"] = str(action_revision(before) + 1)
    validate_action_row_chronology(row)
    return True


def validate_action_row_chronology(row: dict[str, str]) -> None:
    action_id = row.get("Action ID", "")
    created_at = clean_iso_timestamp(row.get("Created At"), f"action {action_id} created_at")
    updated_at = clean_iso_timestamp(row.get("Last Updated"), f"action {action_id} updated_at")
    started_at = clean_iso_timestamp(row.get("Started At"), f"action {action_id} started_at")
    done_at = clean_iso_timestamp(row.get("Done At"), f"action {action_id} done_at")
    cancelled_at = clean_iso_timestamp(row.get("Cancelled At"), f"action {action_id} cancelled_at")
    if not created_at or not updated_at or not valid_action_flow_timestamps(
        row.get("Status", "").lower(), created_at, updated_at, started_at, done_at, cancelled_at
    ):
        raise StatusSyncContractError("ACTION_CHRONOLOGY_INVALID", f"action {action_id} lifecycle timestamps are invalid")


def validate_action_transition(before: str, after: str, action_id: str) -> None:
    if before.lower() in {"done", "cancelled"} and after != before.lower():
        raise ValueError(f"terminal action {action_id} cannot transition from {before} to {after}")


def valid_action_flow_timestamps(
    status: str,
    created_at: str,
    updated_at: str,
    started_at: str | None,
    done_at: str | None,
    cancelled_at: str | None,
) -> bool:
    if status in {"in-progress", "blocked", "done"} and not started_at:
        return False
    if status == "done":
        if not done_at or cancelled_at:
            return False
    elif done_at:
        return False
    if status == "cancelled":
        if not cancelled_at or done_at:
            return False
    elif cancelled_at:
        return False
    if status == "open" and started_at:
        return False

    def parsed(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    created = parsed(created_at)
    updated = parsed(updated_at)
    if created > updated:
        return False
    started = parsed(started_at) if started_at else None
    terminal_value = done_at or cancelled_at
    terminal = parsed(terminal_value) if terminal_value else None
    if started is not None and not (created <= started <= updated):
        return False
    if terminal is not None and not (created <= terminal <= updated):
        return False
    if started is not None and terminal is not None and started > terminal:
        return False
    return True


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
        try:
            normalized_created_at = clean_iso_timestamp(created_at, f"action {action_id} created_at")
            normalized_updated_at = clean_iso_timestamp(updated_at, f"action {action_id} updated_at")
            normalized_started_at = clean_iso_timestamp(row.get("Started At"), f"action {action_id} started_at")
            normalized_done_at = clean_iso_timestamp(row.get("Done At"), f"action {action_id} done_at")
            normalized_cancelled_at = clean_iso_timestamp(row.get("Cancelled At"), f"action {action_id} cancelled_at")
        except ValueError:
            continue
        if normalized_created_at is None or normalized_updated_at is None:
            continue
        if not valid_action_flow_timestamps(
            status,
            normalized_created_at,
            normalized_updated_at,
            normalized_started_at,
            normalized_done_at,
            normalized_cancelled_at,
        ):
            continue
        actions.append(
            {
                "action_id": action_id,
                "status": status,
                "created_at": normalized_created_at,
                "updated_at": normalized_updated_at,
                "started_at": normalized_started_at,
                "done_at": normalized_done_at,
                "cancelled_at": normalized_cancelled_at,
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


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_id(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def contract_ref(schema_id: str) -> dict[str, str]:
    missing = [path for path in (CONTRACT_SCHEMA_PATH, CONTRACT_REGISTRY_PATH) if not path.is_file()]
    if missing:
        raise StatusSyncContractError(
            "CONTRACT_ASSET_MISSING",
            "installed panel-sync contract assets are missing: " + ", ".join(str(path) for path in missing),
        )
    return {
        "schema_id": schema_id,
        "schema_sha256": sha256_bytes(CONTRACT_SCHEMA_PATH.read_bytes()),
        "registry_sha256": sha256_bytes(CONTRACT_REGISTRY_PATH.read_bytes()),
    }


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_existing_json_object(path: Path, error_code: str, label: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StatusSyncContractError(error_code, f"{label} is malformed: {path}") from exc
    if not isinstance(value, dict):
        raise StatusSyncContractError(error_code, f"{label} must be a JSON object: {path}")
    return value


def build_action_ledger_state(
    ledger_path: Path,
    rows: list[dict[str, str]],
    previous_state: dict[str, Any],
    action_updates: list[ActionUpdate],
) -> dict[str, Any]:
    ledger_fingerprint = "sha256:" + hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    previous_fingerprint = str(previous_state.get("ledger_fingerprint") or "")
    previous_revision = previous_state.get("ledger_revision")
    if not isinstance(previous_revision, int) or isinstance(previous_revision, bool) or previous_revision < 1:
        previous_revision = 0
    ledger_revision = previous_revision if previous_fingerprint == ledger_fingerprint and previous_revision else previous_revision + 1
    ledger_revision = max(ledger_revision, 1)
    applied_commands = [
        item
        for item in previous_state.get("applied_commands", [])
        if isinstance(item, dict)
    ]
    by_command = {str(item.get("command_id")): item for item in applied_commands if item.get("command_id")}
    for update in action_updates:
        resolved_action_id = update.resolved_action_id or update.action_id
        if not update.command_id or not resolved_action_id:
            continue
        command_fingerprint = action_command_fingerprint(update)
        existing = by_command.get(update.command_id)
        if existing and existing.get("command_fingerprint") != command_fingerprint:
            raise StatusSyncContractError(
                "ACTION_COMMAND_REPLAY_CONFLICT",
                f"action command {update.command_id} was already applied with different bytes",
            )
        by_command[update.command_id] = {
            "command_id": update.command_id,
            "command_fingerprint": command_fingerprint,
            "action_id": resolved_action_id,
        }
    state = {
        "contract": contract_ref("urn:adp:panel-sync-contracts:2026-07-24#action-ledger-state-v1"),
        "schema_version": "1.0.0",
        "ledger_path": "actions/action-ledger.md",
        "ledger_fingerprint": ledger_fingerprint,
        "ledger_revision": ledger_revision,
        "actions": sorted(
            [
                {
                    "action_id": row.get("Action ID", ""),
                    "action_revision": action_revision(row),
                    "row_fingerprint": content_id({field: row.get(field, "") for field in ACTION_FIELDS}),
                }
                for row in rows
                if row.get("Action ID")
            ],
            key=lambda item: item["action_id"],
        ),
        "applied_commands": [by_command[key] for key in sorted(by_command)],
    }
    state["state_id"] = content_id(state)
    return state


def action_command_fingerprint(update: ActionUpdate) -> str:
    return content_id(
        {
            "operation": update.operation,
            "action_id": update.action_id,
            "expected_revision": update.expected_revision,
            "set": {
                field_name: getattr(update, field_name)
                for field_name in sorted(update.present_fields)
            },
            "evidence": update.evidence,
        }
    )


def filter_replayed_action_updates(
    state: dict[str, Any],
    updates: list[ActionUpdate],
) -> tuple[list[ActionUpdate], list[str]]:
    applied = {
        str(item.get("command_id")): item
        for item in state.get("applied_commands", [])
        if isinstance(item, dict) and item.get("command_id")
    }
    pending: list[ActionUpdate] = []
    replayed: list[str] = []
    for update in updates:
        if not update.command_id or update.command_id not in applied:
            pending.append(update)
            continue
        existing = applied[update.command_id]
        if existing.get("command_fingerprint") != action_command_fingerprint(update):
            raise StatusSyncContractError(
                "ACTION_COMMAND_REPLAY_CONFLICT",
                f"action command {update.command_id} was already applied with different bytes",
            )
        update.resolved_action_id = str(existing.get("action_id") or update.action_id or "") or None
        replayed.append(update.resolved_action_id or "")
    return pending, sorted(item for item in replayed if item)


def validate_action_ledger_state(ledger_path: Path, state: dict[str, Any]) -> None:
    if not state:
        return
    if not ledger_path.is_file():
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger state exists without its ledger")
    claimed_state_id = state.get("state_id")
    state_body = dict(state)
    state_body.pop("state_id", None)
    if claimed_state_id != content_id(state_body):
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger state identity is invalid")
    if state.get("ledger_fingerprint") != sha256_bytes(ledger_path.read_bytes()):
        raise StatusSyncContractError(
            "ACTION_LEDGER_STATE_MISMATCH",
            "action ledger bytes changed without a matching ledger-state commit",
        )
    rows = parse_action_ledger(ledger_path)
    expected_actions = {
        row.get("Action ID", ""): {
            "action_id": row.get("Action ID", ""),
            "action_revision": action_revision(row),
            "row_fingerprint": content_id({field: row.get(field, "") for field in ACTION_FIELDS}),
        }
        for row in rows
        if row.get("Action ID")
    }
    state_actions = state.get("actions")
    if not isinstance(state_actions, list):
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger state action index is invalid")
    indexed_ids = [str(item.get("action_id") or "") for item in state_actions if isinstance(item, dict)]
    if len(indexed_ids) != len(state_actions) or len(indexed_ids) != len(set(indexed_ids)):
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger state contains duplicate action IDs")
    if {str(item["action_id"]): item for item in state_actions} != expected_actions:
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger state action index does not match ledger rows")
    applied = state.get("applied_commands")
    if not isinstance(applied, list):
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger applied-command index is invalid")
    command_ids = [str(item.get("command_id") or "") for item in applied if isinstance(item, dict)]
    if len(command_ids) != len(applied) or len(command_ids) != len(set(command_ids)):
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "action ledger state contains duplicate command IDs")
    if any(str(item.get("action_id") or "") not in expected_actions for item in applied):
        raise StatusSyncContractError("ACTION_LEDGER_STATE_MISMATCH", "applied command references an unknown action")


def write_action_ledger_state(
    memory_root: Path,
    ledger_path: Path,
    rows: list[dict[str, str]],
    action_updates: list[ActionUpdate],
) -> tuple[Path, dict[str, Any]]:
    path = memory_root / ACTION_LEDGER_STATE_REL
    state = build_action_ledger_state(ledger_path, rows, load_json_object(path), action_updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path, state


def wdr_state_path(record_path: Path) -> Path:
    return record_path.with_name("delivery-record.state.json")


def update_wdr_state(record_path: Path, before_bytes: bytes, after_bytes: bytes) -> dict[str, Any]:
    path = wdr_state_path(record_path)
    previous = load_json_object(path)
    previous_revision = previous.get("wdr_revision") if isinstance(previous.get("wdr_revision"), int) else 0
    previous_generation = previous.get("file_generation") if isinstance(previous.get("file_generation"), int) else 0
    changed = before_bytes != after_bytes
    state = {
        "schema_version": "1.0.0",
        "workstream_id": record_path.parent.name,
        "wdr_path": f"workstreams/{record_path.parent.name}/delivery-record.md",
        "wdr_fingerprint": "sha256:" + hashlib.sha256(after_bytes).hexdigest(),
        "wdr_revision": max(1, previous_revision + (1 if changed else 0)),
        "file_generation": max(1, previous_generation + (1 if changed else 0)),
    }
    state["state_id"] = content_id(state)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return state


def action_projection_record(row: dict[str, str]) -> dict[str, Any]:
    action_id = row.get("Action ID", "").strip()
    owner = row.get("Owner", "").strip() or "TBD"
    action = row.get("Action", "").strip() or "TBD"
    due = row.get("Due / Trigger", "").strip() or "TBD"
    affected = sorted(parse_workstream_cell(row.get("Affected Workstreams", "")))
    routing_scope = safe_normalize_id(row.get("Workstream", "")) or "program"
    return {
        "action_id": action_id,
        "owner": owner,
        "action": action,
        "due_trigger": due,
        "status": row.get("Status", "").strip().lower(),
        "action_revision": action_revision(row),
        "routing_scope_id": routing_scope,
        "affected_workstreams": affected,
        "rendered_summary": f"[action_id:{action_id}] {owner}: {action} (due: {due})",
    }


def write_action_projection_sidecar(
    memory_root: Path,
    workstream_id: str,
    rows: list[dict[str, str]],
    ledger_state: dict[str, Any],
) -> Path:
    record_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
    state = load_json_object(wdr_state_path(record_path))
    if not state:
        state = update_wdr_state(record_path, record_path.read_bytes(), record_path.read_bytes())
    records = [
        action_projection_record(row)
        for row in rows
        if row.get("Status", "").lower() in ACTIVE_ACTION_STATUSES
        and (
            safe_normalize_id(row.get("Workstream", "")) == workstream_id
            or workstream_id in parse_workstream_cell(row.get("Affected Workstreams", ""))
        )
    ]
    payload = {
        "contract": contract_ref("urn:adp:panel-sync-contracts:2026-07-24#wdr-action-projection-v1"),
        "schema_version": "1.0.0",
        "workstream_id": workstream_id,
        "ledger_fingerprint": ledger_state["ledger_fingerprint"],
        "ledger_revision": ledger_state["ledger_revision"],
        "wdr_revision": state["wdr_revision"],
        "file_generation": state["file_generation"],
        "renderer_id": "urn:adp:wdr-action-renderer:1.0.0",
        "renderer_sha256": "sha256:" + hashlib.sha256(b"adp-wdr-action-renderer:1.0.0").hexdigest(),
        "actions": sorted(records, key=lambda item: item["action_id"]),
    }
    path = record_path.with_name(ACTION_PROJECTION_REL)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def consume_status_intents(
    memory_root: Path,
    updates: list[StatusUpdate],
    consumer_id: str,
    consumed_at: str,
) -> dict[str, Any]:
    requested = {
        intent_id
        for update in updates
        for intent_id in update.consumed_intent_ids
    }
    path = memory_root / "state/status-intent-outbox.json"
    if not requested:
        current = load_json_object(path)
        pending = current.get("pending", []) if isinstance(current.get("pending"), list) else []
        return {
            "status": "converged" if not pending else "pending",
            "pending_intent_ids": sorted(str(item) for item in pending),
            "consumed_intent_ids": [],
            "outbox": str(path) if path.is_file() else None,
        }
    outbox = load_json_object(path)
    if not outbox:
        raise StatusSyncContractError("STATUS_INTENT_OUTBOX_MISSING", "typed status intents require durable producer outbox state")
    rows = outbox.get("intents")
    if not isinstance(rows, list):
        raise StatusSyncContractError("STATUS_INTENT_OUTBOX_INVALID", "status intent outbox rows are invalid")
    validate_status_intent_outbox(outbox, rows)
    durable_ids = [
        str(row.get("intent_id"))
        for row in rows
        if isinstance(row, dict) and row.get("intent_id")
    ]
    if len(durable_ids) != len(set(durable_ids)):
        raise StatusSyncContractError(
            "STATUS_INTENT_OUTBOX_INVALID",
            "status intent outbox contains duplicate intent_id values",
        )
    by_id = {
        str(row.get("intent_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("intent_id")
    }
    if requested - set(by_id):
        raise StatusSyncContractError(
            "STATUS_INTENT_BINDING_MISMATCH",
            "status-sync input references intents absent from the durable outbox: " + ", ".join(sorted(requested - set(by_id))),
        )
    for update in updates:
        for intent_id in update.consumed_intent_ids:
            durable_row = by_id[intent_id]
            durable_intent = durable_row.get("intent")
            caller_intent = update.consumed_intents.get(intent_id)
            if (
                not isinstance(durable_intent, dict)
                or caller_intent != durable_intent
                or durable_row.get("payload_hash") != content_id(durable_intent)
                or durable_intent.get("intent_id") != intent_id
                or safe_normalize_id(str(durable_intent.get("workstream_id") or "")) != update.workstream_id
            ):
                raise StatusSyncContractError(
                    "STATUS_INTENT_BINDING_MISMATCH",
                    f"status intent {intent_id} does not match its durable outbox payload",
                )
    requested_by_workstream = {
        update.workstream_id: set(update.consumed_intent_ids)
        for update in updates
        if update.consumed_intent_ids
    }
    pending_by_workstream: dict[str, set[str]] = {}
    for intent_id, row in by_id.items():
        if row.get("state") != "pending":
            continue
        intent = row.get("intent") if isinstance(row.get("intent"), dict) else {}
        workstream_id = str(intent.get("workstream_id") or "")
        pending_by_workstream.setdefault(workstream_id, set()).add(intent_id)
    for workstream_id, intent_ids in requested_by_workstream.items():
        expected = pending_by_workstream.get(workstream_id, set())
        already_consumed = {
            intent_id
            for intent_id in intent_ids
            if by_id[intent_id].get("state") == "consumed" and by_id[intent_id].get("consumed_by") == consumer_id
        }
        if intent_ids != expected and intent_ids != already_consumed:
            raise StatusSyncContractError(
                "STATUS_INTENT_PARTIAL_CONSUMPTION",
                f"workstream {workstream_id} must consume the complete pending intent set",
            )
    consumed: list[str] = []
    for intent_id in sorted(requested):
        row = by_id[intent_id]
        if row.get("state") == "consumed":
            if row.get("consumed_by") != consumer_id:
                raise StatusSyncContractError(
                    "STATUS_INTENT_ALREADY_CONSUMED",
                    f"status intent {intent_id} was consumed by another batch",
                )
            continue
        if row.get("state") != "pending":
            raise StatusSyncContractError("STATUS_INTENT_STATE_INVALID", f"status intent {intent_id} has invalid state")
        row["state"] = "consumed"
        row["consumed_by"] = consumer_id
        row["consumed_at"] = consumed_at
        consumed.append(intent_id)
    outbox["pending"] = sorted(intent_id for intent_id, row in by_id.items() if row.get("state") == "pending")
    outbox["consumed"] = sorted(intent_id for intent_id, row in by_id.items() if row.get("state") == "consumed")
    outbox["failed"] = []
    outbox["waived"] = []
    outbox["intents"] = [by_id[key] for key in sorted(by_id)]
    outbox.pop("state_id", None)
    outbox["state_id"] = content_id(outbox)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(outbox, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {
        "status": "converged" if not outbox["pending"] else "pending",
        "pending_intent_ids": outbox["pending"],
        "consumed_intent_ids": consumed,
        "outbox": str(path),
    }


def validate_status_intent_outbox(outbox: dict[str, Any], rows: list[Any]) -> None:
    claimed_state_id = outbox.get("state_id")
    body = dict(outbox)
    body.pop("state_id", None)
    if claimed_state_id != content_id(body):
        raise StatusSyncContractError("STATUS_INTENT_OUTBOX_INVALID", "status intent outbox identity is invalid")
    if outbox.get("failed") != [] or outbox.get("waived") != []:
        raise StatusSyncContractError(
            "STATUS_INTENT_OUTBOX_INVALID",
            "status intent outbox failed and waived sets must remain empty",
        )
    pending: list[str] = []
    consumed: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("intent_id") or row.get("state") not in {"pending", "consumed"}:
            raise StatusSyncContractError("STATUS_INTENT_OUTBOX_INVALID", "status intent outbox contains an invalid row")
        intent = row.get("intent")
        intent_id = str(row["intent_id"])
        if (
            not isinstance(intent, dict)
            or intent.get("intent_id") != intent_id
            or row.get("payload_hash") != content_id(intent)
        ):
            raise StatusSyncContractError(
                "STATUS_INTENT_OUTBOX_INVALID",
                f"status intent outbox row {intent_id} has invalid payload binding",
            )
        (pending if row["state"] == "pending" else consumed).append(intent_id)
    if outbox.get("pending") != sorted(pending) or outbox.get("consumed") != sorted(consumed):
        raise StatusSyncContractError(
            "STATUS_INTENT_OUTBOX_INVALID",
            "status intent outbox indexes do not match row states",
        )


def assign_if_meaningful(row: dict[str, str], field_name: str, value: str) -> None:
    text = str(value or "").strip()
    if text and text.upper() != "TBD":
        row[field_name] = text


def assign_if_present(row: dict[str, str], field_name: str, value: str) -> None:
    text = str(value or "").strip()
    if text:
        row[field_name] = text


def action_workstreams_cell(action_update: ActionUpdate) -> str:
    workstreams = list(action_update.affected_workstreams or [])
    workstream = action_update.workstream or ""
    if not workstreams and workstream.upper() not in {"", "TBD"}:
        workstreams = [workstream]
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
    if not merged and fallback.upper() not in {"", "TBD"} and not scope_contract_module().is_action_routing_id(fallback):
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


def refreshed_action_projection(existing: list[str], active_summaries: list[str]) -> list[str]:
    manual = [item for item in existing if action_summary_id(item) is None]
    return [*manual, *active_summaries]


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
            scope_contract_module().is_action_routing_id(update.workstream_id)
            and bool(update.actions)
            and not has_wdr_delta(update)
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
    active_summaries = active_action_summaries(ledger_actions, update.workstream_id) if update.refresh_actions else []
    if update.next_actions_provided:
        values["next_actions"] = "; ".join(update.next_actions)
    elif update.refresh_actions:
        existing_actions = split_next_actions(existing_field_value(original, "Project Status", "Next actions"))
        projected_actions = refreshed_action_projection(existing_actions, active_summaries)
        values["next_actions"] = "; ".join(projected_actions)
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
    wdr_state = None
    if changed_fields or update.refresh_actions:
        after_bytes = updated.encode("utf-8") if changed_fields else original.encode("utf-8")
        wdr_state = update_wdr_state(record_path, original.encode("utf-8"), after_bytes)

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
        "action_candidates": active_summaries if update.refresh_actions else update.next_actions,
        "actions_registered": [],
        "actions_updated": [],
        "actions_closed": [],
        "unresolved_gaps": sorted(set(unresolved_gaps(update))),
        "wdr_state": str(wdr_state_path(record_path)) if wdr_state else None,
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
            update.refresh_actions,
            update.milestones,
            update.current_fields_present,
        ]
    )


def update_values(update: StatusUpdate, timestamp: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if "status" in update.current_fields_present and update.status:
        values["status"] = update.status
    if "phase" in update.current_fields_present and update.phase:
        values["phase"] = update.phase
    if "progress" in update.current_fields_present and update.progress:
        values["progress"] = update.progress
    if "blockers" in update.current_fields_present:
        values["blockers"] = "; ".join(update.blockers)
    if "risks" in update.current_fields_present:
        values["risks"] = "; ".join(update.risks)
    if "dependencies" in update.current_fields_present:
        values["dependencies"] = "; ".join(update.dependencies)
    if "change_notes" in update.current_fields_present:
        values["change_notes"] = "; ".join(update.change_notes)
    if update.next_actions_provided:
        values["next_actions"] = "; ".join(update.next_actions) if update.next_actions else "fill missing state"
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
    if not has_wdr_delta(update) and not update.actions:
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
        if scope_contract_module().is_virtual_cli_scope_id(update.workstream_id):
            if has_wdr_delta(update):
                raise StatusSyncContractError(
                    "ADP-VIRTUAL-SCOPE-NOT-WDR-TARGET",
                    "program is a virtual scope and cannot receive WDR fields, milestone updates, or action refresh",
                )
            if update.actions:
                continue
        record_path = memory_root / "workstreams" / update.workstream_id / "delivery-record.md"
        if record_path.is_file():
            record_path.read_text(encoding="utf-8")
            continue
        project_action_scope = (
            scope_contract_module().is_action_routing_id(update.workstream_id)
            and bool(update.actions)
            and not has_wdr_delta(update)
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


def publish_staged_files(
    memory_root: Path,
    staged_root: Path,
    relatives: list[Path],
    *,
    transaction_kind: str = "status-mutation",
    transaction_id: str | None = None,
) -> dict[str, Any]:
    targets = []
    for relative in relatives:
        canonical_path = memory_root / relative
        staged_path = staged_root / relative
        before = canonical_path.read_bytes() if canonical_path.is_file() else None
        after = staged_path.read_bytes() if staged_path.is_file() else None
        targets.append(
            {
                "path": relative.as_posix(),
                "before_sha256": sha256_bytes(before) if before is not None else None,
                "after_sha256": sha256_bytes(after) if after is not None else None,
            }
        )
    if transaction_id is None:
        identity = content_id({"kind": transaction_kind, "targets": targets}).removeprefix("sha256:")
        transaction_id = next_status_transaction_id(memory_root, f"{transaction_kind}-{identity[:24]}")
    journal = memory_root / TRANSACTION_REL / transaction_id
    if journal.exists():
        raise StatusSyncContractError("STATUS_TRANSACTION_CONFLICT", f"status transaction already exists: {transaction_id}")
    before_root = journal / "before"
    after_root = journal / "after"
    manifest = {
        "schema_version": "1.0.0",
        "kind": transaction_kind,
        "transaction_id": transaction_id,
        "status": "prepared",
        "applied_count": 0,
        "targets": targets,
    }
    for relative, entry in zip(relatives, targets, strict=True):
        canonical_path = memory_root / relative
        staged_path = staged_root / relative
        if entry["before_sha256"] is not None:
            write_bytes_atomic(before_root / relative, canonical_path.read_bytes())
        if entry["after_sha256"] is not None:
            write_bytes_atomic(after_root / relative, staged_path.read_bytes())
    write_json_atomic(journal / "manifest.json", manifest)
    prepared: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for relative in relatives:
            after_path = after_root / relative
            if after_path.is_file():
                prepared[relative] = write_temp_bytes(memory_root / relative, after_path.read_bytes())
        for relative in relatives:
            if relative in prepared:
                os.replace(prepared[relative], memory_root / relative)
            else:
                (memory_root / relative).unlink(missing_ok=True)
            committed.append(relative)
            manifest["applied_count"] = len(committed)
            write_json_atomic(journal / "manifest.json", manifest)
        manifest["status"] = "committed"
        write_json_atomic(journal / "manifest.json", manifest)
    except BaseException:
        for relative in reversed(committed):
            canonical_path = memory_root / relative
            before_path = before_root / relative
            if not before_path.is_file():
                canonical_path.unlink(missing_ok=True)
            else:
                restore_temp = write_temp_bytes(canonical_path, before_path.read_bytes())
                os.replace(restore_temp, canonical_path)
        manifest["status"] = "rolled-back"
        write_json_atomic(journal / "manifest.json", manifest)
        raise
    finally:
        for temp_path in prepared.values():
            temp_path.unlink(missing_ok=True)
    return manifest


def write_bytes_atomic(path: Path, content: bytes) -> None:
    temporary = write_temp_bytes(path, content)
    os.replace(temporary, path)


def next_status_transaction_id(memory_root: Path, base: str) -> str:
    root = memory_root / TRANSACTION_REL
    candidate = base
    attempt = 0
    while (root / candidate).exists():
        attempt += 1
        candidate = f"{base}-r{attempt}"
    return candidate


def validated_recovery_target(root: Path, raw_path: Any) -> tuple[Path, Path]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise StatusSyncContractError("STATUS_TRANSACTION_CORRUPT", "transaction target path is invalid")
    relative = Path(raw_path)
    if relative.is_absolute() or relative == Path(".") or ".." in relative.parts:
        raise StatusSyncContractError(
            "STATUS_TRANSACTION_CORRUPT",
            f"transaction target must be a safe relative path: {raw_path}",
        )
    root_resolved = root.resolve()
    target = root / relative
    try:
        target.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise StatusSyncContractError(
            "STATUS_TRANSACTION_CORRUPT",
            f"transaction target escapes its recovery root: {raw_path}",
        ) from exc
    return relative, target


def find_committed_transaction(memory_root: Path, transaction_prefix: str) -> dict[str, Any] | None:
    root = memory_root / TRANSACTION_REL
    if not root.is_dir():
        return None
    matches: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob(f"{transaction_prefix}*/manifest.json")):
        manifest = load_json_object(manifest_path)
        if manifest.get("status") == "committed" and transaction_targets_match(memory_root, manifest, "after"):
            matches.append(manifest)
    return matches[-1] if matches else None


def transaction_targets_match(memory_root: Path, manifest: dict[str, Any], image: str) -> bool:
    hash_field = f"{image}_sha256"
    targets = manifest.get("targets")
    if not isinstance(targets, list):
        return False
    for entry in targets:
        if not isinstance(entry, dict) or not entry.get("path"):
            return False
        _, target = validated_recovery_target(memory_root, entry["path"])
        actual = sha256_bytes(target.read_bytes()) if target.is_file() else None
        if actual != entry.get(hash_field):
            return False
    return True


def recover_status_transactions(memory_root: Path) -> list[str]:
    root = memory_root / TRANSACTION_REL
    recovered: list[str] = []
    if not root.is_dir():
        return recovered
    supported = {"status-mutation", "repair-business", "repair-attempt"}
    for manifest_path in sorted(root.glob("*/manifest.json")):
        manifest = load_json_object(manifest_path)
        if manifest.get("kind") not in supported or manifest.get("status") != "prepared":
            continue
        targets = manifest.get("targets")
        if not isinstance(targets, list):
            raise StatusSyncContractError("STATUS_TRANSACTION_CORRUPT", f"transaction targets are invalid: {manifest_path}")
        for entry in targets:
            if not isinstance(entry, dict):
                raise StatusSyncContractError(
                    "STATUS_TRANSACTION_CORRUPT",
                    f"transaction target entry is invalid: {manifest_path}",
                )
            _, target = validated_recovery_target(memory_root, entry.get("path"))
            actual = sha256_bytes(target.read_bytes()) if target.is_file() else None
            if actual not in {entry.get("before_sha256"), entry.get("after_sha256")}:
                raise StatusSyncContractError(
                    "STATUS_TRANSACTION_CORRUPT",
                    f"transaction target contains unknown bytes: {entry.get('path')}",
                )
        journal = manifest_path.parent
        for entry in reversed(targets):
            relative, target = validated_recovery_target(memory_root, entry["path"])
            _, before = validated_recovery_target(journal / "before", relative.as_posix())
            if entry.get("before_sha256") is None:
                target.unlink(missing_ok=True)
            elif before.is_file() and sha256_bytes(before.read_bytes()) == entry.get("before_sha256"):
                write_bytes_atomic(target, before.read_bytes())
            else:
                raise StatusSyncContractError(
                    "STATUS_TRANSACTION_CORRUPT",
                    f"transaction before image is missing or invalid: {entry.get('path')}",
                )
        manifest["status"] = "rolled-back"
        manifest["recovered"] = True
        write_json_atomic(manifest_path, manifest)
        recovered.append(str(manifest.get("transaction_id")))
    return recovered


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


def completed_status_receipt(
    memory_root: Path,
    input_hash: str,
    update_count: int,
) -> tuple[Path, dict[str, Any]] | None:
    root = memory_root / STATUS_SYNC_RECEIPT_REL
    if not root.is_dir():
        return None
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("ssr-*.json")):
        receipt = load_json_object(path)
        if receipt.get("input_hash") != input_hash:
            continue
        execution_id = receipt.get("execution_id")
        valid = (
            receipt.get("receipt_schema_version") == RECEIPT_SCHEMA_VERSION
            and receipt.get("receipt_type") == "execution"
            and isinstance(execution_id, str)
            and re.fullmatch(r"ssr-[0-9a-f]{32}", execution_id) is not None
            and path.name == f"{execution_id}.json"
            and receipt.get("ok") is True
            and receipt.get("status") == "applied"
            and receipt.get("durable") is True
            and receipt.get("dry_run") is False
            and receipt.get("mode") == "update"
            and receipt.get("update_count") == update_count
            and isinstance(receipt.get("input_path"), str)
            and bool(receipt.get("input_path"))
            and isinstance(receipt.get("applied_at"), str)
        )
        if not valid:
            raise StatusSyncContractError(
                "STATUS_RECEIPT_INVALID",
                f"durable status-sync receipt is invalid: {path}",
            )
        normalize_required_timestamp(receipt["applied_at"], "status receipt applied_at")
        matches.append((path, receipt))
    return matches[-1] if matches else None


def validate_completed_input_replay(
    memory_root: Path,
    updates: list[StatusUpdate],
    input_hash: str,
) -> tuple[list[str], dict[str, Any]]:
    ledger_path = memory_root / ACTION_LEDGER_REL
    ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
    ledger_state = load_existing_json_object(
        ledger_state_path,
        "ACTION_LEDGER_STATE_MISMATCH",
        "action ledger state",
    )
    if ledger_path.is_file() or ledger_state_path.is_file():
        if not ledger_state:
            raise StatusSyncContractError(
                "ACTION_LEDGER_STATE_MISMATCH",
                "completed status-sync replay requires durable action ledger state",
            )
        validate_action_ledger_state(ledger_path, ledger_state)
    action_updates = [action for update in updates for action in update.actions]
    pending, replayed_action_ids = filter_replayed_action_updates(ledger_state, action_updates)
    if any(update.command_id for update in pending):
        raise StatusSyncContractError(
            "STATUS_RECEIPT_STATE_MISMATCH",
            "status receipt is not backed by all action command ledger entries",
        )

    requested = sorted(
        intent_id
        for update in updates
        for intent_id in update.consumed_intent_ids
    )
    outbox_path = memory_root / "state/status-intent-outbox.json"
    pending_intents: list[str] = []
    if requested:
        outbox = load_existing_json_object(
            outbox_path,
            "STATUS_INTENT_OUTBOX_INVALID",
            "status intent outbox",
        )
        rows = outbox.get("intents")
        if not isinstance(rows, list):
            raise StatusSyncContractError(
                "STATUS_INTENT_OUTBOX_INVALID",
                "status intent outbox rows are invalid",
            )
        validate_status_intent_outbox(outbox, rows)
        by_id = {
            str(row.get("intent_id")): row
            for row in rows
            if isinstance(row, dict) and row.get("intent_id")
        }
        for update in updates:
            for intent_id in update.consumed_intent_ids:
                row = by_id.get(intent_id)
                if (
                    not row
                    or row.get("intent") != update.consumed_intents.get(intent_id)
                    or row.get("state") != "consumed"
                    or row.get("consumed_by") != input_hash
                ):
                    raise StatusSyncContractError(
                        "STATUS_RECEIPT_STATE_MISMATCH",
                        f"status receipt is not backed by consumed intent {intent_id}",
                    )
        pending_intents = sorted(str(item) for item in outbox.get("pending", []))
    return replayed_action_ids, {
        "status": "converged" if not pending_intents else "pending",
        "pending_intent_ids": pending_intents,
        "consumed_intent_ids": [],
        "outbox": str(outbox_path) if outbox_path.is_file() else None,
    }


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
    validate_update_targets(memory_root, updates)
    baseline_context = validate_milestone_updates(memory_root, updates)
    completed_receipt = (
        completed_status_receipt(memory_root, input_hash, len(updates))
        if input_hash and not args.dry_run
        else None
    )
    if completed_receipt:
        receipt_path, receipt = completed_receipt
        replayed_action_ids, intent_convergence = validate_completed_input_replay(
            memory_root,
            updates,
            input_hash,
        )
        ledger_path = memory_root / ACTION_LEDGER_REL
        action_flow_path = memory_root / "views/action-flow.json"
        ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
        emit(
            {
                "ok": True,
                "mode": "update",
                "status": "already-applied",
                "reused": True,
                "dry_run": False,
                "input_path": str(input_path),
                "input_hash": input_hash,
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "baseline_path": str(baseline_context["path"]) if baseline_context else None,
                "baseline_revision": baseline_context["revision"] if baseline_context else None,
                "action_ledger": str(ledger_path),
                "action_flow": str(action_flow_path) if action_flow_path.is_file() else None,
                "action_ledger_state": str(ledger_state_path) if ledger_state_path.is_file() else None,
                "actions_registered": [],
                "actions_updated": [],
                "actions_closed": [],
                "actions_no_op": replayed_action_ids,
                "unresolved_gaps": [],
                "updates": [
                    {
                        "ok": True,
                        "workstream_id": update.workstream_id,
                        "no_op": True,
                        "changed_fields": [],
                        "milestones_updated": [],
                        "actions_registered": [],
                        "actions_updated": [],
                        "actions_closed": [],
                        "unresolved_gaps": [],
                    }
                    for update in updates
                ],
                "receipt": receipt,
                "receipt_path": str(receipt_path),
                "intent_convergence": intent_convergence,
                "refresh_required": False,
                "dirty_hints": [],
                "next_command": None,
                "next_command_args": [],
            },
            args.output,
        )
        return 0
    memory_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".status-sync-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        if memory_root.is_dir():
            shutil.copytree(memory_root, staged_root)
        else:
            staged_root.mkdir(parents=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        action_updates = [action for update in updates for action in update.actions]
        staged_ledger_path = staged_root / ACTION_LEDGER_REL
        previous_ledger_state = load_existing_json_object(
            staged_root / ACTION_LEDGER_STATE_REL,
            "ACTION_LEDGER_STATE_MISMATCH",
            "action ledger state",
        )
        validate_action_ledger_state(staged_ledger_path, previous_ledger_state)
        pending_action_updates, replayed_action_ids = filter_replayed_action_updates(
            previous_ledger_state,
            action_updates,
        )
        if action_updates:
            staged_ledger_path = ensure_action_ledger(staged_root, False)
            ledger_result = upsert_actions(staged_ledger_path, pending_action_updates, timestamp, False)
            ledger_result["actions_no_op"] = sorted({*ledger_result["actions_no_op"], *replayed_action_ids})
        else:
            ledger_result = {
                "rows": parse_action_ledger(staged_ledger_path),
                "actions_registered": [],
                "actions_updated": [],
                "actions_closed": [],
                "actions_no_op": [],
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
        ledger_state = None
        if staged_ledger_path.is_file():
            _, ledger_state = write_action_ledger_state(
                staged_root,
                staged_ledger_path,
                ledger_result["rows"],
                pending_action_updates,
            )
        ledger_rows = ledger_result["rows"]
        hydrate_action_updates_from_ledger(updates, ledger_rows)
        results = [apply_update(staged_root, update, ledger_rows, baseline_context, False) for update in updates]
        if ledger_state:
            for result, update in zip(results, updates, strict=True):
                if not update.refresh_actions or update.workstream_id in {"program", "project", "adp-program"}:
                    continue
                sidecar_path = write_action_projection_sidecar(
                    staged_root,
                    update.workstream_id,
                    ledger_rows,
                    ledger_state,
                )
                result["action_projection"] = str(sidecar_path)
        for result, update in zip(results, updates, strict=True):
            update_action_ids = {
                action.resolved_action_id or action.action_id
                for action in update.actions
                if action.resolved_action_id or action.action_id
            }
            result["actions_registered"] = related_action_ids(
                ledger_rows, ledger_result["actions_registered"], update_action_ids
            )
            result["actions_updated"] = related_action_ids(
                ledger_rows, ledger_result["actions_updated"], update_action_ids
            )
            result["actions_closed"] = related_action_ids(
                ledger_rows, ledger_result["actions_closed"], update_action_ids
            )
            result["unresolved_gaps"] = sorted(
                set([*result.get("unresolved_gaps", []), *ledger_result["unresolved_gaps"]])
            )
        errors = [item for item in results if not item.get("ok")]
        if errors:
            raise ValueError("; ".join(str(item.get("error") or "status update failed") for item in errors))
        intent_convergence = consume_status_intents(
            staged_root,
            updates,
            input_hash or content_id({"updates": [update.workstream_id for update in updates]}),
            timestamp,
        )
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
    ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
    next_command_args = (
        [
            "adp-panel-refresh",
            "plan",
            str(project_root),
            "--memory-root",
            str(memory_root),
        ]
        if changed
        else []
    )
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
        "action_ledger_state": str(ledger_state_path) if ledger_state_path.is_file() or args.dry_run else None,
        "actions_registered": ledger_result["actions_registered"],
        "actions_updated": ledger_result["actions_updated"],
        "actions_closed": ledger_result["actions_closed"],
        "actions_no_op": ledger_result["actions_no_op"],
        "unresolved_gaps": ledger_result["unresolved_gaps"],
        "updates": results,
        "receipt": receipt,
        "receipt_path": str(memory_root / receipt_rel) if receipt_rel else None,
        "intent_convergence": remap_staged_paths(intent_convergence, staged_root, memory_root),
        "refresh_required": bool(changed),
        "dirty_hints": [str(path.as_posix()) for path in changed],
        "next_command": shlex.join(next_command_args) if next_command_args else None,
        "next_command_args": next_command_args,
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


def repair_contract_from_audit(audit: dict[str, Any]) -> dict[str, Any]:
    contract = audit.get("repair_contract", audit)
    if not isinstance(contract, dict) or not isinstance(contract.get("repair_batches"), list):
        raise StatusSyncContractError("REPAIR_AUDIT_INVALID", "audit JSON does not contain a repair_contract")
    if contract.get("audit_id") != audit.get("input_audit_id", audit.get("audit_id")):
        raise StatusSyncContractError("REPAIR_AUDIT_INVALID", "repair contract audit_id does not match the audit")
    return contract


def validated_repair_batch(contract: dict[str, Any], batch_id: str) -> dict[str, Any]:
    matches = [row for row in contract["repair_batches"] if isinstance(row, dict) and row.get("batch_id") == batch_id]
    if len(matches) != 1:
        raise StatusSyncContractError("REPAIR_BATCH_NOT_FOUND", f"repair batch {batch_id} was not found exactly once")
    batch = matches[0]
    required = {"batch_id", "based_on_audit_id", "finding_ids", "command", "read_set", "batch_digest"}
    if not required.issubset(batch):
        raise StatusSyncContractError("REPAIR_BATCH_INVALID", "repair batch is missing required fields")
    body = {key: batch[key] for key in ("based_on_audit_id", "finding_ids", "command", "read_set")}
    if content_id(body) != batch["batch_id"] or content_id({"batch_id": batch["batch_id"], **body}) != batch["batch_digest"]:
        raise StatusSyncContractError("REPAIR_BATCH_INVALID", "repair batch identity or digest is invalid")
    command = batch["command"]
    if not isinstance(command, dict) or command.get("workflow") != "adp-status-sync" or command.get("operation") != "refresh_actions":
        raise StatusSyncContractError("REPAIR_BATCH_INVALID", "repair batch command is not an adp-status-sync refresh_actions command")
    action_ids = command.get("action_ids")
    if not isinstance(action_ids, list) or not action_ids or len(action_ids) != len(set(action_ids)):
        raise StatusSyncContractError("REPAIR_BATCH_INVALID", "repair batch action_ids must be a non-empty unique list")
    finding_ids = set(batch["finding_ids"])
    findings = [row for row in contract.get("findings", []) if isinstance(row, dict) and row.get("repair_batch_id") == batch_id]
    if {row.get("finding_id") for row in findings} != finding_ids:
        raise StatusSyncContractError("REPAIR_BATCH_INVALID", "repair batch finding membership is inconsistent")
    finding_action_ids = sorted({str(action_id) for row in findings for action_id in row.get("action_ids", [])})
    if finding_action_ids != sorted(str(item) for item in action_ids):
        raise StatusSyncContractError("REPAIR_BATCH_INVALID", "repair batch action IDs do not match its findings")
    return batch


def repair_live_snapshot(memory_root: Path, batch: dict[str, Any]) -> dict[str, Any]:
    command = batch["command"]
    workstream_id = normalize_id(str(command.get("workstream_id") or ""))
    ledger_path = memory_root / ACTION_LEDGER_REL
    ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
    record_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
    state_path = wdr_state_path(record_path)
    sidecar_path = record_path.with_name(ACTION_PROJECTION_REL)
    required_paths = [ledger_path, ledger_state_path, record_path, state_path]
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair source is missing: " + ", ".join(missing))
    rows = parse_action_ledger(ledger_path)
    ledger_fingerprint = sha256_bytes(ledger_path.read_bytes())
    ledger_state = load_existing_json_object(
        ledger_state_path,
        "REPAIR_READ_SET_STALE",
        "action ledger state",
    )
    if ledger_state.get("ledger_fingerprint") != ledger_fingerprint:
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "action ledger state does not match the ledger")
    wdr_state = load_existing_json_object(
        state_path,
        "REPAIR_READ_SET_STALE",
        "WDR state",
    )
    if wdr_state.get("wdr_fingerprint") != sha256_bytes(record_path.read_bytes()):
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "WDR state does not match delivery-record.md")
    revisions = {row.get("Action ID", ""): action_revision(row) for row in rows if row.get("Action ID")}
    read_set = batch.get("read_set") if isinstance(batch.get("read_set"), dict) else {}
    if read_set.get("ledger_fingerprint") != ledger_fingerprint:
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "ledger fingerprint changed after audit")
    if "ledger_revision" in read_set and read_set.get("ledger_revision") != ledger_state.get("ledger_revision"):
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "ledger revision changed after audit")
    if command.get("expected_wdr_revision") != wdr_state.get("wdr_revision"):
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "WDR revision changed after audit")
    if command.get("expected_file_generation") != wdr_state.get("file_generation"):
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "WDR file generation changed after audit")
    expected_revisions = read_set.get("action_revisions")
    if isinstance(expected_revisions, list):
        for expected in expected_revisions:
            action_id = str(expected.get("action_id") or "")
            actual = revisions.get(action_id)
            if bool(expected.get("expected_present")) != (actual is not None) or expected.get("revision") != actual:
                raise StatusSyncContractError("REPAIR_READ_SET_STALE", f"action {action_id} revision changed after audit")
    active_rows = [
        row
        for row in rows
        if row.get("Status", "").lower() in ACTIVE_ACTION_STATUSES
        and (
            safe_normalize_id(row.get("Workstream", "")) == workstream_id
            or workstream_id in parse_workstream_cell(row.get("Affected Workstreams", ""))
        )
    ]
    validate_repair_source_records(memory_root, read_set)
    return {
        "workstream_id": workstream_id,
        "ledger_path": ledger_path,
        "ledger_state_path": ledger_state_path,
        "record_path": record_path,
        "state_path": state_path,
        "sidecar_path": sidecar_path,
        "rows": rows,
        "active_rows": active_rows,
        "ledger_state": ledger_state,
        "wdr_state": wdr_state,
        "fingerprints": {
            ACTION_LEDGER_REL.as_posix(): ledger_fingerprint,
            ACTION_LEDGER_STATE_REL.as_posix(): sha256_bytes(ledger_state_path.read_bytes()),
            record_path.relative_to(memory_root).as_posix(): sha256_bytes(record_path.read_bytes()),
            state_path.relative_to(memory_root).as_posix(): sha256_bytes(state_path.read_bytes()),
            sidecar_path.relative_to(memory_root).as_posix(): (
                sha256_bytes(sidecar_path.read_bytes()) if sidecar_path.is_file() else None
            ),
        },
    }


def validate_repair_source_records(memory_root: Path, read_set: dict[str, Any]) -> None:
    source_records = read_set.get("source_records")
    required_paths = {
        ACTION_LEDGER_REL.as_posix(),
        ACTION_LEDGER_STATE_REL.as_posix(),
        f"workstreams/{normalize_id(str(read_set_source_workstream(read_set)))}/delivery-record.md",
        f"workstreams/{normalize_id(str(read_set_source_workstream(read_set)))}/delivery-record.state.json",
        f"workstreams/{normalize_id(str(read_set_source_workstream(read_set)))}/{ACTION_PROJECTION_REL}",
    }
    if not isinstance(source_records, list) or len(source_records) != len(required_paths):
        raise StatusSyncContractError(
            "REPAIR_READ_SET_STALE",
            "repair source_records must bind the complete ledger and WDR read set",
        )
    expected_root_id = "ri_" + hashlib.sha256(str(memory_root.resolve()).encode("utf-8")).hexdigest()
    seen: set[str] = set()
    for record in source_records:
        if not isinstance(record, dict) or record.get("root_instance_id") != expected_root_id:
            raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair source record root identity changed")
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute() or ".." in Path(raw_path).parts:
            raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair source record path is invalid")
        path = memory_root / raw_path
        try:
            path.resolve(strict=False).relative_to(memory_root.resolve())
        except ValueError as exc:
            raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair source record escapes memory root") from exc
        if raw_path in seen:
            raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair source records contain duplicate paths")
        seen.add(raw_path)
        actual = sha256_bytes(path.read_bytes()) if path.is_file() else "sha256:" + "0" * 64
        if record.get("fingerprint") != actual:
            raise StatusSyncContractError(
                "REPAIR_READ_SET_STALE",
                f"repair source record changed after audit: {raw_path}",
            )
    if seen != required_paths:
        raise StatusSyncContractError(
            "REPAIR_READ_SET_STALE",
            "repair source_records do not match the complete repair read set",
        )
    fact_state_path = memory_root / "state/fact-generation.json"
    fact_state = load_existing_json_object(
        fact_state_path,
        "REPAIR_READ_SET_STALE",
        "fact generation state",
    )
    actual_generation = fact_state.get("generation", 1)
    if (
        not isinstance(actual_generation, int)
        or isinstance(actual_generation, bool)
        or actual_generation < 1
        or read_set.get("fact_generation") != actual_generation
    ):
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "fact generation changed after audit")


def read_set_source_workstream(read_set: dict[str, Any]) -> str:
    revisions = read_set.get("wdr_revisions")
    if not isinstance(revisions, list) or len(revisions) != 1 or not isinstance(revisions[0], dict):
        raise StatusSyncContractError(
            "REPAIR_READ_SET_STALE",
            "repair read set must bind exactly one WDR revision",
        )
    workstream_id = str(revisions[0].get("workstream_id") or "")
    if not workstream_id:
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair WDR read set is missing workstream identity")
    return workstream_id


def repair_binding(audit_id: str, batch: dict[str, Any], principal: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "batch_id": batch["batch_id"],
        "batch_digest": batch["batch_digest"],
        "principal": principal,
        "read_fingerprints": snapshot["fingerprints"],
    }


def repair_token_path(memory_root: Path, token: str) -> Path:
    return memory_root / REPAIR_TOKEN_REL / f"{hashlib.sha256(token.encode('utf-8')).hexdigest()}.json"


def append_repair_attempt(memory_root: Path, receipt: dict[str, Any]) -> None:
    ledger_path = memory_root / REPAIR_ATTEMPT_LEDGER_REL
    index_path = memory_root / REPAIR_RECEIPT_INDEX_REL
    ledger = load_json_object(ledger_path) or {"schema_version": "1.0.0", "attempts": []}
    attempts = ledger.get("attempts") if isinstance(ledger.get("attempts"), list) else []
    if not any(row.get("receipt_id") == receipt["receipt_id"] for row in attempts if isinstance(row, dict)):
        attempts.append({
            "sequence": len(attempts) + 1,
            "receipt_id": receipt["receipt_id"],
            "batch_id": receipt["batch_id"],
            "outcome": receipt["outcome"],
            "recorded_at": receipt["recorded_at"],
        })
    ledger["attempts"] = attempts
    ledger["next_sequence"] = len(attempts) + 1
    ledger.pop("ledger_id", None)
    ledger["ledger_id"] = content_id(ledger)
    index = load_json_object(index_path) or {"schema_version": "1.0.0", "entries": []}
    entries = index.get("entries") if isinstance(index.get("entries"), list) else []
    relative_receipt = (REPAIR_RECEIPT_REL / f"{receipt['receipt_id'].removeprefix('sha256:')}.json").as_posix()
    if not any(row.get("receipt_id") == receipt["receipt_id"] for row in entries if isinstance(row, dict)):
        entries.append({"receipt_id": receipt["receipt_id"], "batch_id": receipt["batch_id"], "path": relative_receipt})
    index["entries"] = sorted(entries, key=lambda row: (row["batch_id"], row["receipt_id"]))
    index.pop("index_id", None)
    index["index_id"] = content_id(index)
    transaction_base = "repair-attempt-" + receipt["receipt_id"].removeprefix("sha256:")[:24]
    committed = find_committed_transaction(memory_root, transaction_base)
    if committed:
        return
    transaction_id = next_status_transaction_id(memory_root, transaction_base)
    with tempfile.TemporaryDirectory(prefix=".repair-attempt-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        write_json_atomic(staged_root / REPAIR_ATTEMPT_LEDGER_REL, ledger)
        write_json_atomic(staged_root / REPAIR_RECEIPT_INDEX_REL, index)
        write_json_atomic(staged_root / relative_receipt, receipt)
        publish_staged_files(
            memory_root,
            staged_root,
            [REPAIR_ATTEMPT_LEDGER_REL, REPAIR_RECEIPT_INDEX_REL, Path(relative_receipt)],
            transaction_kind="repair-attempt",
            transaction_id=transaction_id,
        )


def repair_receipt(batch: dict[str, Any], token_state: dict[str, Any], outcome: str, error_code: str | None) -> dict[str, Any]:
    body = {
        "schema_version": "1.0.0",
        "batch_id": batch["batch_id"],
        "batch_digest": batch["batch_digest"],
        "outcome": outcome,
        "nonce_status": token_state["status"],
        "nonce_state_id": token_state["state_id"],
        "retry_required": outcome != "committed",
        "error_code": error_code,
        "business_transaction_id": token_state.get("business_transaction_id"),
        "recorded_at": token_state.get("updated_at") or token_state.get("issued_at"),
    }
    return {"receipt_id": content_id(body), **body}


def issue_repair_token(memory_root: Path, audit_id: str, batch: dict[str, Any], principal: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    issued = datetime.now(timezone.utc)
    token = f"repair_{secrets.token_urlsafe(32)}"
    binding = repair_binding(audit_id, batch, principal, snapshot)
    state = {
        "schema_version": "1.0.0",
        "token_hash": sha256_bytes(token.encode("utf-8")),
        "audit_id": audit_id,
        "batch_id": batch["batch_id"],
        "batch_digest": batch["batch_digest"],
        "principal": principal,
        "binding_digest": content_id(binding),
        "binding": binding,
        "status": "unused",
        "issued_at": issued.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "previous_state_id": None,
    }
    state["state_id"] = content_id(state)
    write_json_atomic(repair_token_path(memory_root, token), state)
    return {"token": token, "token_state": state}


def update_token_state(
    path: Path,
    state: dict[str, Any],
    status: str,
    **changes: Any,
) -> dict[str, Any]:
    updated = dict(state)
    updated["previous_state_id"] = state["state_id"]
    updated["status"] = status
    updated["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated.update(changes)
    updated.pop("state_id", None)
    updated["state_id"] = content_id(updated)
    write_json_atomic(path, updated)
    return updated


def apply_repair_snapshot(
    memory_root: Path,
    batch: dict[str, Any],
    snapshot: dict[str, Any],
    fail_after_stage: bool,
    transaction_id: str,
) -> tuple[list[str], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix=".status-repair-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        shutil.copytree(memory_root, staged_root)
        workstream_id = snapshot["workstream_id"]
        record_path = staged_root / "workstreams" / workstream_id / "delivery-record.md"
        original = record_path.read_text(encoding="utf-8")
        summaries = [action_projection_record(row)["rendered_summary"] for row in snapshot["active_rows"]]
        existing = split_next_actions(existing_field_value(original, "Project Status", "Next actions"))
        next_actions = "; ".join(refreshed_action_projection(existing, summaries))
        updated, _, _ = set_section_bullet(original, "Project Status", "Next actions", next_actions)
        record_path.write_text(updated, encoding="utf-8", newline="\n")
        update_wdr_state(record_path, original.encode("utf-8"), updated.encode("utf-8"))
        write_action_projection_sidecar(
            staged_root,
            workstream_id,
            snapshot["rows"],
            snapshot["ledger_state"],
        )
        changed = changed_staged_files(memory_root, staged_root)
        allowed = {
            Path("workstreams") / workstream_id / "delivery-record.md",
            Path("workstreams") / workstream_id / "delivery-record.state.json",
            Path("workstreams") / workstream_id / ACTION_PROJECTION_REL,
        }
        unexpected = [path.as_posix() for path in changed if path not in allowed]
        if unexpected:
            raise StatusSyncContractError("REPAIR_TARGET_INVALID", "repair staged unexpected targets: " + ", ".join(unexpected))
        if fail_after_stage:
            raise StatusSyncContractError("REPAIR_INJECTED_FAILURE", "injected failure after repair staging")
        publication = publish_staged_files(
            memory_root,
            staged_root,
            changed,
            transaction_kind="repair-business",
            transaction_id=transaction_id,
        )
        return [path.as_posix() for path in changed], publication


def run_repair(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    audit_path = require_file(args.audit_json, "audit-json")
    audit = load_json_object(audit_path)
    contract = repair_contract_from_audit(audit)
    batch = validated_repair_batch(contract, args.batch_id)
    audit_id = str(contract["audit_id"])
    if args.dry_run:
        if args.token:
            raise StatusSyncContractError("REPAIR_TOKEN_INVALID", "--token is not accepted with --dry-run")
        snapshot = repair_live_snapshot(memory_root, batch)
        issued = issue_repair_token(memory_root, audit_id, batch, args.principal, snapshot)
        emit({
            "ok": True,
            "mode": "repair",
            "dry_run": True,
            "outcome": "applicable",
            "audit_id": audit_id,
            "batch_id": batch["batch_id"],
            "action_ids": batch["command"]["action_ids"],
            "token": issued["token"],
            "issued_at": issued["token_state"]["issued_at"],
            "expires_at": issued["token_state"]["expires_at"],
            "writes_performed": [str(repair_token_path(memory_root, issued["token"]))],
        }, args.output)
        return 0
    if not args.token:
        raise StatusSyncContractError("REPAIR_TOKEN_REQUIRED", "repair apply requires --token from a successful dry-run")
    token_path = repair_token_path(memory_root, args.token)
    token_state = load_existing_json_object(
        token_path,
        "REPAIR_TOKEN_INVALID",
        "repair token state",
    )
    if not token_state or token_state.get("token_hash") != sha256_bytes(args.token.encode("utf-8")):
        raise StatusSyncContractError("REPAIR_TOKEN_INVALID", "repair token is unknown")
    token_state_body = dict(token_state)
    claimed_state_id = token_state_body.pop("state_id", None)
    if claimed_state_id != content_id(token_state_body):
        raise StatusSyncContractError("REPAIR_TOKEN_INVALID", "repair token state identity is invalid")
    if (
        token_state.get("audit_id") != audit_id
        or token_state.get("batch_id") != batch["batch_id"]
        or token_state.get("batch_digest") != batch["batch_digest"]
        or token_state.get("principal") != args.principal
    ):
        raise StatusSyncContractError("REPAIR_TOKEN_INVALID", "repair token is bound to another audit, batch, or principal")
    if token_state.get("status") == "consumed":
        append_repair_attempt(memory_root, repair_receipt(batch, token_state, "committed", None))
        raise StatusSyncContractError("REPAIR_TOKEN_USED", "repair token is not unused")
    if token_state.get("status") == "invalidated":
        append_repair_attempt(
            memory_root,
            repair_receipt(
                batch,
                token_state,
                "rolled-back",
                str(token_state.get("terminal_error_code") or "REPAIR_APPLY_FAILED"),
            ),
        )
        raise StatusSyncContractError("REPAIR_TOKEN_USED", "repair token is not unused")
    if token_state.get("status") == "reserved":
        transaction_id = str(token_state.get("business_transaction_id") or "")
        manifest = load_json_object(memory_root / TRANSACTION_REL / transaction_id / "manifest.json")
        if manifest.get("status") == "committed" and transaction_targets_match(memory_root, manifest, "after"):
            consumed = update_token_state(token_path, token_state, "consumed")
            receipt = repair_receipt(batch, consumed, "committed", None)
            append_repair_attempt(memory_root, receipt)
            emit(committed_repair_result(project_root, memory_root, audit_id, batch, manifest, receipt, reused=True), args.output)
            return 0
        invalidated = update_token_state(
            token_path,
            token_state,
            "invalidated",
            terminal_error_code="REPAIR_INTERRUPTED",
        )
        append_repair_attempt(
            memory_root,
            repair_receipt(batch, invalidated, "rolled-back", "REPAIR_INTERRUPTED"),
        )
        raise StatusSyncContractError(
            "REPAIR_INTERRUPTED",
            "reserved repair did not reach a committed business transaction; run a new dry-run",
        )
    if token_state.get("status") != "unused":
        raise StatusSyncContractError("REPAIR_TOKEN_INVALID", "repair token has an invalid state")
    expires_at = datetime.fromisoformat(str(token_state.get("expires_at", "")).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="REPAIR_TOKEN_EXPIRED")
        raise StatusSyncContractError("REPAIR_TOKEN_EXPIRED", "repair token expired")
    try:
        snapshot = repair_live_snapshot(memory_root, batch)
    except Exception as exc:
        code = exc.error_code if isinstance(exc, StatusSyncContractError) else "REPAIR_READ_SET_STALE"
        invalidated = update_token_state(token_path, token_state, "invalidated", terminal_error_code=code)
        receipt = repair_receipt(batch, invalidated, "rolled-back", code)
        append_repair_attempt(memory_root, receipt)
        raise
    binding = repair_binding(audit_id, batch, args.principal, snapshot)
    if token_state.get("binding_digest") != content_id(binding) or token_state.get("binding") != binding:
        invalidated = update_token_state(
            token_path,
            token_state,
            "invalidated",
            terminal_error_code="REPAIR_READ_SET_STALE",
        )
        receipt = repair_receipt(batch, invalidated, "rolled-back", "REPAIR_READ_SET_STALE")
        append_repair_attempt(memory_root, receipt)
        raise StatusSyncContractError("REPAIR_READ_SET_STALE", "repair facts or authority changed after dry-run")
    transaction_base = "repair-business-" + batch["batch_id"].removeprefix("sha256:")[:24]
    transaction_id = next_status_transaction_id(memory_root, transaction_base)
    reserved = update_token_state(
        token_path,
        token_state,
        "reserved",
        business_transaction_id=transaction_id,
    )
    try:
        changed, publication = apply_repair_snapshot(
            memory_root,
            batch,
            snapshot,
            args.fail_after_stage,
            transaction_id,
        )
    except Exception as exc:
        code = exc.error_code if isinstance(exc, StatusSyncContractError) else "REPAIR_APPLY_FAILED"
        invalidated = update_token_state(token_path, reserved, "invalidated", terminal_error_code=code)
        receipt = repair_receipt(batch, invalidated, "rolled-back", code)
        append_repair_attempt(memory_root, receipt)
        raise
    consumed = update_token_state(token_path, reserved, "consumed")
    receipt = repair_receipt(batch, consumed, "committed", None)
    append_repair_attempt(memory_root, receipt)
    emit(
        committed_repair_result(
            project_root,
            memory_root,
            audit_id,
            batch,
            publication,
            receipt,
            changed_paths=changed,
        ),
        args.output,
    )
    return 0


def committed_repair_result(
    project_root: Path,
    memory_root: Path,
    audit_id: str,
    batch: dict[str, Any],
    publication: dict[str, Any],
    receipt: dict[str, Any],
    *,
    changed_paths: list[str] | None = None,
    reused: bool = False,
) -> dict[str, Any]:
    next_command_args = [
        "adp-panel-refresh",
        "detect",
        str(project_root),
        "--memory-root",
        str(memory_root),
    ]
    return {
        "ok": True,
        "mode": "repair",
        "dry_run": False,
        "outcome": "committed",
        "reused": reused,
        "audit_id": audit_id,
        "batch_id": batch["batch_id"],
        "action_ids": batch["command"]["action_ids"],
        "changed_paths": changed_paths or [str(row["path"]) for row in publication.get("targets", [])],
        "business_transaction": publication,
        "receipt": receipt,
        "refresh_required": True,
        "next_command": shlex.join(next_command_args),
        "next_command_args": next_command_args,
    }


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
            if action.owner in {None, "TBD"}:
                action.owner = row.get("Owner", "TBD")
            if action.workstream in {None, update.workstream_id}:
                action.workstream = row.get("Workstream", update.workstream_id)


def related_action_ids(
    rows: list[dict[str, str]],
    ids: list[str],
    requested_ids: set[str | None],
) -> list[str]:
    id_set = set(ids)
    related: list[str] = []
    for row in rows:
        action_id = row.get("Action ID", "")
        if action_id not in id_set:
            continue
        if action_id in requested_ids:
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
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_update(args)
        if args.command == "stale":
            return run_stale(args)
        if args.command == "migrate-receipt":
            return run_migrate_receipt(args)
        if args.command == "repair":
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_repair(args)
        raise ValueError(f"unknown command: {args.command}")
    except StatusSyncContractError as exc:
        payload = {"ok": False, "error_code": exc.error_code, "error": str(exc)}
        emit(payload, getattr(args, "output", None))
        return 2
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        emit(payload, getattr(args, "output", None))
        return 2


if __name__ == "__main__":
    sys.exit(main())
