#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Apply lightweight ADP status updates to Workstream Delivery Records."""

from __future__ import annotations

import argparse
import ast
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
AUTHORITY_MIGRATION_TOKEN_REL = Path("state") / "authority-migration-tokens"
AUTHORITY_MIGRATION_RECEIPT_REL = Path("receipts") / "authority-state-migration"
INTAKE_RECONCILIATION_TOKEN_REL = Path("state") / "intake-reconciliation-tokens"
INTAKE_RETIREMENT_TOKEN_REL = Path("state") / "intake-retirement-tokens"
INTAKE_RETIREMENT_RECEIPT_REL = Path("receipts") / "status-sync-retirement"
INTAKE_RETIREMENT_REASONS = {"never-applied", "superseded-by", "invalid-proposal"}
MEETING_SYNC_RECEIPT_REL = Path("meetings") / "receipts"
HISTORICAL_INPUT_MIGRATION_EVIDENCE_REL = Path("receipts") / "status-sync-input-migration" / "originals"
WDR_FIELD_REPAIR_TOKEN_REL = Path("state") / "wdr-field-repair-tokens"
WDR_FIELD_REPAIR_RECEIPT_REL = Path("receipts") / "wdr-field-repair"
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
    def __init__(self, error_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.error_code = error_code
        self.details = details or {}
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
    intake_source: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    done_at: str | None = None
    cancelled_at: str | None = None
    baseline_revision: int | None = None
    related_plan_item_ids: list[str] | None = None
    related_flow_edge_ids: list[str] | None = None
    present_fields: set[str] = field(default_factory=set)
    declared_fields: set[str] = field(default_factory=set)
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

    authority_migration = subparsers.add_parser(
        "migrate-authority-state",
        help="Bootstrap ledger/WDR authority sidecars from current legacy project facts.",
    )
    authority_migration.add_argument("project_root", help="Project root containing ADP memory.")
    authority_migration.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect differences and issue a single-use apply token without changing authority artifacts.",
    )
    authority_migration.add_argument("--token", help="Single-use token returned by a successful dry-run.")
    authority_migration.add_argument(
        "--principal",
        default="adp-status-sync",
        help="Stable operator or automation principal bound to the migration token.",
    )
    authority_migration.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    authority_migration.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    authority_migration.add_argument("--fail-after-stage", action="store_true", help=argparse.SUPPRESS)
    authority_migration.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    field_repair = subparsers.add_parser(
        "repair-wdr-field",
        help="Deduplicate one reviewed canonical WDR field without guessing conflicting values.",
    )
    field_repair.add_argument("project_root", help="Project root containing ADP memory.")
    field_repair.add_argument("--id", required=True, help="Physical workstream ID.")
    field_repair.add_argument("--section", required=True, help="Canonical WDR section name.")
    field_repair.add_argument("--field", required=True, help="Canonical WDR bullet field name.")
    field_repair.add_argument(
        "--canonical-value-file",
        help="Reviewed UTF-8 single-line canonical value; required when duplicate values conflict.",
    )
    field_repair.add_argument("--dry-run", action="store_true", help="Validate and issue a single-use token.")
    field_repair.add_argument("--token", help="Single-use token returned by a successful dry-run.")
    field_repair.add_argument("--principal", default="adp-status-sync", help="Stable operator principal.")
    field_repair.add_argument(
        "--memory-root", default="_bmad-output/adp/memory", help="ADP memory root."
    )
    field_repair.add_argument("--fail-after-stage", action="store_true", help=argparse.SUPPRESS)
    field_repair.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    reconcile = subparsers.add_parser(
        "reconcile-intake",
        help="Reconcile one historical receipt-less intake against current canonical facts.",
    )
    reconcile.add_argument("project_root", help="Project root containing ADP memory.")
    reconcile.add_argument("--updates-file", required=True, help="Exact historical intake JSON to reconcile.")
    reconcile.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare every command to canonical facts and issue a single-use token only when all are satisfied.",
    )
    reconcile.add_argument("--token", help="Single-use token returned by a fully satisfied dry-run.")
    reconcile.add_argument(
        "--principal",
        default="adp-status-sync",
        help="Stable operator or automation principal bound to the token and receipt.",
    )
    reconcile.add_argument("--source", default="status sync", help="Default source used by legacy intake parsing.")
    reconcile.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    reconcile.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    reconcile.add_argument("--fail-after-stage", action="store_true", help=argparse.SUPPRESS)
    reconcile.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    retire = subparsers.add_parser(
        "retire-intake",
        help="Create a governed content-bound retirement receipt for one historical intake.",
    )
    retire.add_argument("project_root", help="Project root containing ADP memory.")
    retire.add_argument("--updates-file", required=True, help="Exact historical intake JSON to retire without modifying it.")
    retire.add_argument("--reason", required=True, choices=sorted(INTAKE_RETIREMENT_REASONS))
    retire.add_argument(
        "--superseded-by",
        help="Existing successor intake or durable status-sync receipt; required only for reason superseded-by.",
    )
    retire.add_argument(
        "--justification",
        help="Explicit governance rationale; required for never-applied and invalid-proposal.",
    )
    retire.add_argument("--principal", required=True, help="Governance authority principal bound to the token and receipt.")
    retire.add_argument("--dry-run", action="store_true", help="Validate retirement governance and issue a single-use token.")
    retire.add_argument("--token", help="Single-use token returned by a successful dry-run.")
    retire.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    retire.add_argument("--fail-after-stage", action="store_true", help=argparse.SUPPRESS)
    retire.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")

    migrate = subparsers.add_parser(
        "migrate-receipt",
        help="Create one explicit compatibility receipt from historical successful execution evidence.",
    )
    migrate.add_argument("project_root", help="Project root containing ADP memory.")
    migrate.add_argument("--updates-file", required=True, help="Exact historical updates file to attest.")
    migrate.add_argument(
        "--original-updates-file",
        help=(
            "Restored original updates bytes for a governed historical-input-change migration. "
            "The original and current canonical executable payloads must be identical."
        ),
    )
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


def updates_from_payload(
    payload: Any,
    default_source: str,
    *,
    allow_legacy_terminal_without_id: bool = False,
) -> list[StatusUpdate]:
    items = payload.get("updates", payload) if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and not isinstance(items, list):
        items = status_batch_updates(payload)
    payload_revision = (
        parse_optional_revision(payload.get("baseline_revision"), "baseline_revision")
        if isinstance(payload, dict)
        else None
    )
    payload_refresh_actions = (
        boolean_value(payload.get("refresh_actions", False), "refresh_actions")
        if isinstance(payload, dict)
        else False
    )
    if not isinstance(items, list):
        raise ValueError("updates-file must contain a list or an object with an 'updates' list")
    updates: list[StatusUpdate] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each batch update must be a JSON object")
        updates.append(
            update_from_mapping(
                item,
                default_source=default_source,
                default_revision=payload_revision,
                default_refresh_actions=payload_refresh_actions,
                allow_legacy_terminal_without_id=allow_legacy_terminal_without_id,
            )
        )
    if isinstance(payload, dict):
        bind_status_intents(updates, payload.get("status_intents", []))
    if not updates:
        raise ValueError("updates-file must contain at least one executable update")
    return updates


def load_updates_payload(
    path: Path,
    default_source: str,
    *,
    allow_legacy_terminal_without_id: bool = False,
) -> tuple[Any, list[StatusUpdate]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"updates-file must contain valid JSON: {exc}") from exc
    return payload, updates_from_payload(
        payload,
        default_source,
        allow_legacy_terminal_without_id=allow_legacy_terminal_without_id,
    )


def updates_from_args(args: argparse.Namespace) -> list[StatusUpdate]:
    updates: list[StatusUpdate] = []
    if args.updates_file:
        _, updates = load_updates_payload(Path(args.updates_file), args.source)

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
    allow_legacy_terminal_without_id: bool = False,
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
            allow_legacy_terminal_without_id=allow_legacy_terminal_without_id,
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
    allow_legacy_terminal_without_id: bool = False,
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
        if status in {"done", "cancelled"} and not action_id and not allow_legacy_terminal_without_id:
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
        if operation == "create" and "source" in item:
            present_fields.add("source")
        declared_fields = set(present_fields)
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
                intake_source=str(item.get("source") or default_source),
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
                declared_fields=declared_fields,
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


def build_action_projection_payload(
    workstream_id: str,
    rows: list[dict[str, str]],
    ledger_state: dict[str, Any],
    wdr_state: dict[str, Any],
) -> dict[str, Any]:
    records = [
        action_projection_record(row)
        for row in rows
        if row.get("Status", "").lower() in ACTIVE_ACTION_STATUSES
        and (
            safe_normalize_id(row.get("Workstream", "")) == workstream_id
            or workstream_id in parse_workstream_cell(row.get("Affected Workstreams", ""))
        )
    ]
    return {
        "contract": contract_ref("urn:adp:panel-sync-contracts:2026-07-24#wdr-action-projection-v1"),
        "schema_version": "1.0.0",
        "workstream_id": workstream_id,
        "ledger_fingerprint": ledger_state["ledger_fingerprint"],
        "ledger_revision": ledger_state["ledger_revision"],
        "wdr_revision": wdr_state["wdr_revision"],
        "file_generation": wdr_state["file_generation"],
        "renderer_id": "urn:adp:wdr-action-renderer:1.0.0",
        "renderer_sha256": "sha256:" + hashlib.sha256(b"adp-wdr-action-renderer:1.0.0").hexdigest(),
        "actions": sorted(records, key=lambda item: item["action_id"]),
    }


def write_action_projection_sidecar(
    memory_root: Path,
    workstream_id: str,
    rows: list[dict[str, str]],
    ledger_state: dict[str, Any],
    *,
    wdr_state: dict[str, Any] | None = None,
) -> Path:
    record_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
    state = wdr_state or load_json_object(wdr_state_path(record_path))
    if not state:
        state = update_wdr_state(record_path, record_path.read_bytes(), record_path.read_bytes())
    payload = build_action_projection_payload(workstream_id, rows, ledger_state, state)
    path = record_path.with_name(ACTION_PROJECTION_REL)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def optional_sha256_file(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def load_authority_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, ["missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"malformed JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["JSON root is not an object"]
    return payload, []


def validate_wdr_state(record_path: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    claimed_state_id = state.get("state_id")
    body = dict(state)
    body.pop("state_id", None)
    if claimed_state_id != content_id(body):
        errors.append("state identity is invalid")
    expected_path = f"workstreams/{record_path.parent.name}/delivery-record.md"
    if state.get("schema_version") != "1.0.0":
        errors.append("schema_version is not 1.0.0")
    if state.get("workstream_id") != record_path.parent.name:
        errors.append("workstream identity does not match the WDR path")
    if state.get("wdr_path") != expected_path:
        errors.append("wdr_path does not match the WDR path")
    if state.get("wdr_fingerprint") != sha256_bytes(record_path.read_bytes()):
        errors.append("WDR fingerprint does not match delivery-record.md")
    for field_name in ("wdr_revision", "file_generation"):
        value = state.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{field_name} must be a positive integer")
    return errors


def bootstrap_wdr_state(record_path: Path) -> dict[str, Any]:
    state = {
        "schema_version": "1.0.0",
        "workstream_id": record_path.parent.name,
        "wdr_path": f"workstreams/{record_path.parent.name}/delivery-record.md",
        "wdr_fingerprint": sha256_bytes(record_path.read_bytes()),
        "wdr_revision": 1,
        "file_generation": 1,
    }
    state["state_id"] = content_id(state)
    return state


def authority_artifact_report(
    relative: Path,
    kind: str,
    existing_path: Path,
    existing_payload: dict[str, Any] | None,
    desired_payload: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    desired_bytes = pretty_json_bytes(desired_payload)
    existing_bytes = existing_path.read_bytes() if existing_path.is_file() else None
    mismatched_fields = sorted(
        key
        for key in set(existing_payload or {}) | set(desired_payload)
        if (existing_payload or {}).get(key) != desired_payload.get(key)
    )
    if issues:
        status = "missing" if issues == ["missing"] else "stale"
    elif mismatched_fields:
        status = "stale"
    elif existing_bytes != desired_bytes:
        status = "format-drift"
    else:
        status = "current"
    report = {
        "path": relative.as_posix(),
        "kind": kind,
        "status": status,
        "changed": existing_bytes != desired_bytes,
        "existing_fingerprint": sha256_bytes(existing_bytes) if existing_bytes is not None else None,
        "desired_fingerprint": sha256_bytes(desired_bytes),
        "issues": issues,
        "mismatched_fields": mismatched_fields,
    }
    if kind == "action-ledger-state" and existing_payload is not None and issues:
        report["discarded_applied_command_count"] = len(
            [item for item in existing_payload.get("applied_commands", []) if isinstance(item, dict)]
        )
    return report


def authority_migration_snapshot(memory_root: Path) -> dict[str, Any]:
    ledger_path = memory_root / ACTION_LEDGER_REL
    if not ledger_path.is_file():
        raise StatusSyncContractError(
            "AUTHORITY_MIGRATION_SOURCE_MISSING",
            f"authority migration requires the current action ledger: {ledger_path}",
        )
    rows = parse_action_ledger(ledger_path)
    ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
    existing_ledger_state, ledger_issues = load_authority_json(ledger_state_path)
    if existing_ledger_state is not None:
        if not existing_ledger_state:
            ledger_issues.append("state object is empty")
        else:
            try:
                validate_action_ledger_state(ledger_path, existing_ledger_state)
            except StatusSyncContractError as exc:
                ledger_issues.append(str(exc))
    desired_ledger_state = (
        existing_ledger_state
        if existing_ledger_state is not None and not ledger_issues
        else build_action_ledger_state(ledger_path, rows, {}, [])
    )

    desired_outputs: dict[str, dict[str, Any]] = {
        ACTION_LEDGER_STATE_REL.as_posix(): desired_ledger_state,
    }
    source_fingerprints: dict[str, str | None] = {
        ACTION_LEDGER_REL.as_posix(): optional_sha256_file(ledger_path),
        ACTION_LEDGER_STATE_REL.as_posix(): optional_sha256_file(ledger_state_path),
    }
    authority_sources: dict[str, str] = {
        ACTION_LEDGER_REL.as_posix(): sha256_bytes(ledger_path.read_bytes()),
    }
    artifacts = [
        authority_artifact_report(
            ACTION_LEDGER_STATE_REL,
            "action-ledger-state",
            ledger_state_path,
            existing_ledger_state,
            desired_ledger_state,
            ledger_issues,
        )
    ]
    workstream_ids: list[str] = []
    workstreams_root = memory_root / "workstreams"
    for record_path in sorted(workstreams_root.glob("*/delivery-record.md")) if workstreams_root.is_dir() else []:
        if record_path.is_symlink() or record_path.parent.is_symlink():
            raise StatusSyncContractError(
                "AUTHORITY_MIGRATION_SOURCE_INVALID",
                f"authority migration refuses symlinked WDR sources: {record_path}",
            )
        try:
            record_path.resolve().relative_to(memory_root.resolve())
        except ValueError as exc:
            raise StatusSyncContractError(
                "AUTHORITY_MIGRATION_SOURCE_INVALID",
                f"authority migration WDR escapes the memory root: {record_path}",
            ) from exc
        workstream_id = record_path.parent.name
        workstream_ids.append(workstream_id)
        record_relative = record_path.relative_to(memory_root)
        state_path = wdr_state_path(record_path)
        state_relative = state_path.relative_to(memory_root)
        projection_path = record_path.with_name(ACTION_PROJECTION_REL)
        projection_relative = projection_path.relative_to(memory_root)
        authority_sources[record_relative.as_posix()] = sha256_bytes(record_path.read_bytes())
        source_fingerprints[record_relative.as_posix()] = authority_sources[record_relative.as_posix()]
        source_fingerprints[state_relative.as_posix()] = optional_sha256_file(state_path)
        source_fingerprints[projection_relative.as_posix()] = optional_sha256_file(projection_path)

        existing_wdr_state, wdr_issues = load_authority_json(state_path)
        if existing_wdr_state is not None:
            wdr_issues.extend(validate_wdr_state(record_path, existing_wdr_state))
        desired_wdr_state = (
            existing_wdr_state
            if existing_wdr_state is not None and not wdr_issues
            else bootstrap_wdr_state(record_path)
        )
        desired_outputs[state_relative.as_posix()] = desired_wdr_state
        artifacts.append(
            authority_artifact_report(
                state_relative,
                "delivery-record-state",
                state_path,
                existing_wdr_state,
                desired_wdr_state,
                wdr_issues,
            )
        )

        existing_projection, projection_issues = load_authority_json(projection_path)
        desired_projection = build_action_projection_payload(
            workstream_id,
            rows,
            desired_ledger_state,
            desired_wdr_state,
        )
        desired_outputs[projection_relative.as_posix()] = desired_projection
        artifacts.append(
            authority_artifact_report(
                projection_relative,
                "action-projection",
                projection_path,
                existing_projection,
                desired_projection,
                projection_issues,
            )
        )

    root_instance_id = "ri_" + hashlib.sha256(str(memory_root.resolve()).encode("utf-8")).hexdigest()
    desired_fingerprints = {
        path: sha256_bytes(pretty_json_bytes(payload))
        for path, payload in sorted(desired_outputs.items())
    }
    authority_fact_id = content_id(
        {
            "root_instance_id": root_instance_id,
            "authority_sources": authority_sources,
        }
    )
    migration_id = content_id(
        {
            "root_instance_id": root_instance_id,
            "source_fingerprints": source_fingerprints,
            "desired_fingerprints": desired_fingerprints,
        }
    )
    return {
        "root_instance_id": root_instance_id,
        "authority_fact_id": authority_fact_id,
        "migration_id": migration_id,
        "workstream_ids": workstream_ids,
        "authority_sources": authority_sources,
        "source_fingerprints": source_fingerprints,
        "desired_outputs": desired_outputs,
        "desired_fingerprints": desired_fingerprints,
        "artifacts": artifacts,
        "changed_paths": sorted(item["path"] for item in artifacts if item["changed"]),
        "differences": [item for item in artifacts if item["status"] != "current"],
    }


def authority_migration_binding(snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    return {
        "root_instance_id": snapshot["root_instance_id"],
        "authority_fact_id": snapshot["authority_fact_id"],
        "migration_id": snapshot["migration_id"],
        "principal": principal,
        "source_fingerprints": snapshot["source_fingerprints"],
        "desired_fingerprints": snapshot["desired_fingerprints"],
    }


def authority_migration_token_path(memory_root: Path, token: str) -> Path:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return memory_root / AUTHORITY_MIGRATION_TOKEN_REL / f"{digest}.json"


def issue_authority_migration_token(
    memory_root: Path,
    principal: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    issued = datetime.now(timezone.utc)
    token = f"authority_{secrets.token_urlsafe(32)}"
    binding = authority_migration_binding(snapshot, principal)
    state = {
        "schema_version": "1.0.0",
        "token_hash": sha256_bytes(token.encode("utf-8")),
        "principal": principal,
        "authority_fact_id": snapshot["authority_fact_id"],
        "migration_id": snapshot["migration_id"],
        "binding": binding,
        "binding_digest": content_id(binding),
        "status": "unused",
        "issued_at": issued.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "previous_state_id": None,
    }
    state["state_id"] = content_id(state)
    write_json_atomic(authority_migration_token_path(memory_root, token), state)
    return {"token": token, "token_state": state}


def authority_migration_receipt_path(memory_root: Path, migration_id: str) -> Path:
    return memory_root / AUTHORITY_MIGRATION_RECEIPT_REL / f"{migration_id.removeprefix('sha256:')}.json"


def authority_receipt_is_valid(receipt: dict[str, Any]) -> bool:
    claimed = receipt.get("receipt_id")
    body = dict(receipt)
    body.pop("receipt_id", None)
    return claimed == content_id(body)


def current_authority_migration_receipt(
    memory_root: Path,
    snapshot: dict[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    root = memory_root / AUTHORITY_MIGRATION_RECEIPT_REL
    if not root.is_dir():
        return None
    matches: list[tuple[str, Path, dict[str, Any]]] = []
    for path in root.glob("*.json"):
        receipt = load_json_object(path)
        if (
            not receipt
            or not authority_receipt_is_valid(receipt)
            or receipt.get("outcome") != "committed"
            or receipt.get("authority_fact_id") != snapshot["authority_fact_id"]
            or receipt.get("output_fingerprints") != snapshot["desired_fingerprints"]
        ):
            continue
        if any(
            optional_sha256_file(memory_root / relative) != fingerprint
            for relative, fingerprint in snapshot["desired_fingerprints"].items()
        ):
            continue
        matches.append((str(receipt.get("recorded_at") or ""), path, receipt))
    if not matches:
        return None
    _, path, receipt = max(matches, key=lambda item: item[0])
    return path, receipt


def authority_migration_receipt(
    snapshot: dict[str, Any],
    principal: str,
    transaction_id: str,
) -> dict[str, Any]:
    body = {
        "schema_version": "1.0.0",
        "migration_id": snapshot["migration_id"],
        "authority_fact_id": snapshot["authority_fact_id"],
        "root_instance_id": snapshot["root_instance_id"],
        "principal": principal,
        "outcome": "committed",
        "source_fingerprints": snapshot["source_fingerprints"],
        "authority_sources": snapshot["authority_sources"],
        "output_fingerprints": snapshot["desired_fingerprints"],
        "changed_paths": snapshot["changed_paths"],
        "differences": snapshot["differences"],
        "business_transaction_id": transaction_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    return {"receipt_id": content_id(body), **body}


def apply_authority_migration_snapshot(
    memory_root: Path,
    snapshot: dict[str, Any],
    principal: str,
    transaction_id: str,
    fail_after_stage: bool,
) -> tuple[list[str], dict[str, Any], Path, dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix=".authority-migration-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        copy_memory_tree(memory_root, staged_root)
        for relative, payload in snapshot["desired_outputs"].items():
            write_json_atomic(staged_root / relative, payload)
        receipt = authority_migration_receipt(snapshot, principal, transaction_id)
        receipt_path = authority_migration_receipt_path(staged_root, snapshot["migration_id"])
        write_json_atomic(receipt_path, receipt)
        changed = changed_staged_files(memory_root, staged_root)
        receipt_relative = receipt_path.relative_to(staged_root)
        allowed = {Path(path) for path in snapshot["desired_outputs"]} | {receipt_relative}
        unexpected = [path.as_posix() for path in changed if path not in allowed]
        if unexpected:
            raise StatusSyncContractError(
                "AUTHORITY_MIGRATION_TARGET_INVALID",
                "authority migration staged unexpected targets: " + ", ".join(unexpected),
            )
        if fail_after_stage:
            raise StatusSyncContractError(
                "AUTHORITY_MIGRATION_INJECTED_FAILURE",
                "injected failure after authority migration staging",
            )
        publication = publish_staged_files(
            memory_root,
            staged_root,
            changed,
            transaction_kind="authority-state-migration",
            transaction_id=transaction_id,
        )
        return [path.as_posix() for path in changed], publication, memory_root / receipt_relative, receipt


def authority_migration_result(
    project_root: Path,
    memory_root: Path,
    snapshot: dict[str, Any],
    *,
    status: str,
    dry_run: bool,
    token: str | None = None,
    receipt_path: Path | None = None,
    receipt: dict[str, Any] | None = None,
    publication: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
    reused: bool = False,
) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "migrate-authority-state",
        "status": status,
        "dry_run": dry_run,
        "reused": reused,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "root_instance_id": snapshot["root_instance_id"],
        "authority_fact_id": snapshot["authority_fact_id"],
        "migration_id": receipt.get("migration_id") if receipt else snapshot["migration_id"],
        "current_snapshot_id": snapshot["migration_id"],
        "workstream_ids": snapshot["workstream_ids"],
        "source_fingerprints": snapshot["source_fingerprints"],
        "output_fingerprints": snapshot["desired_fingerprints"],
        "authority_artifacts": snapshot["artifacts"],
        "differences": snapshot["differences"],
        "planned_changed_paths": snapshot["changed_paths"],
        "changed_paths": changed_paths or [],
        "token": token,
        "receipt_path": str(receipt_path) if receipt_path else None,
        "receipt": receipt,
        "business_transaction": publication,
    }


def run_authority_migration(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    principal = " ".join(str(args.principal or "").split())
    if not principal:
        raise StatusSyncContractError("AUTHORITY_MIGRATION_PRINCIPAL_INVALID", "principal must not be empty")
    if args.dry_run:
        if args.token:
            raise StatusSyncContractError(
                "AUTHORITY_MIGRATION_TOKEN_INVALID",
                "--token is not accepted with --dry-run",
            )
        snapshot = authority_migration_snapshot(memory_root)
        existing = current_authority_migration_receipt(memory_root, snapshot)
        if not snapshot["changed_paths"] and existing:
            receipt_path, receipt = existing
            emit(
                authority_migration_result(
                    project_root,
                    memory_root,
                    snapshot,
                    status="already-migrated",
                    dry_run=True,
                    receipt_path=receipt_path,
                    receipt=receipt,
                    reused=True,
                ),
                args.output,
            )
            return 0
        issued = issue_authority_migration_token(memory_root, principal, snapshot)
        emit(
            authority_migration_result(
                project_root,
                memory_root,
                snapshot,
                status="ready-to-apply",
                dry_run=True,
                token=issued["token"],
            ),
            args.output,
        )
        return 0

    if not args.token:
        raise StatusSyncContractError(
            "AUTHORITY_MIGRATION_TOKEN_REQUIRED",
            "authority migration apply requires --token from a successful dry-run",
        )
    token_path = authority_migration_token_path(memory_root, args.token)
    token_state = load_existing_json_object(
        token_path,
        "AUTHORITY_MIGRATION_TOKEN_INVALID",
        "authority migration token",
    )
    if not token_state:
        raise StatusSyncContractError("AUTHORITY_MIGRATION_TOKEN_INVALID", "authority migration token is unknown")
    claimed_state_id = token_state.get("state_id")
    state_body = dict(token_state)
    state_body.pop("state_id", None)
    if claimed_state_id != content_id(state_body) or token_state.get("token_hash") != sha256_bytes(args.token.encode("utf-8")):
        raise StatusSyncContractError("AUTHORITY_MIGRATION_TOKEN_INVALID", "authority migration token identity is invalid")
    if token_state.get("principal") != principal:
        raise StatusSyncContractError("AUTHORITY_MIGRATION_TOKEN_INVALID", "authority migration token belongs to another principal")
    if token_state.get("status") == "consumed":
        raise StatusSyncContractError("AUTHORITY_MIGRATION_TOKEN_USED", "authority migration token was already consumed")
    if token_state.get("status") == "reserved":
        transaction_id = str(token_state.get("business_transaction_id") or "")
        manifest = load_json_object(memory_root / TRANSACTION_REL / transaction_id / "manifest.json")
        receipt_path = Path(str(token_state.get("receipt_path") or ""))
        if manifest.get("status") == "committed" and transaction_targets_match(memory_root, manifest, "after") and receipt_path.is_file():
            snapshot = authority_migration_snapshot(memory_root)
            receipt = load_json_object(receipt_path)
            update_token_state(token_path, token_state, "consumed")
            emit(
                authority_migration_result(
                    project_root,
                    memory_root,
                    snapshot,
                    status="committed",
                    dry_run=False,
                    receipt_path=receipt_path,
                    receipt=receipt,
                    publication=manifest,
                    changed_paths=[str(item.get("path")) for item in manifest.get("targets", [])],
                    reused=True,
                ),
                args.output,
            )
            return 0
        update_token_state(
            token_path,
            token_state,
            "invalidated",
            terminal_error_code="AUTHORITY_MIGRATION_INTERRUPTED",
        )
        raise StatusSyncContractError(
            "AUTHORITY_MIGRATION_INTERRUPTED",
            "reserved authority migration did not reach a committed transaction; run a new dry-run",
        )
    if token_state.get("status") != "unused":
        raise StatusSyncContractError("AUTHORITY_MIGRATION_TOKEN_INVALID", "authority migration token has an invalid state")
    expires_at = datetime.fromisoformat(str(token_state.get("expires_at", "")).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        update_token_state(
            token_path,
            token_state,
            "invalidated",
            terminal_error_code="AUTHORITY_MIGRATION_TOKEN_EXPIRED",
        )
        raise StatusSyncContractError("AUTHORITY_MIGRATION_TOKEN_EXPIRED", "authority migration token expired")
    snapshot = authority_migration_snapshot(memory_root)
    binding = authority_migration_binding(snapshot, principal)
    if token_state.get("binding") != binding or token_state.get("binding_digest") != content_id(binding):
        update_token_state(
            token_path,
            token_state,
            "invalidated",
            terminal_error_code="AUTHORITY_MIGRATION_READ_SET_STALE",
        )
        raise StatusSyncContractError(
            "AUTHORITY_MIGRATION_READ_SET_STALE",
            "authority facts or sidecars changed after the migration dry-run",
        )
    transaction_base = "authority-state-migration-" + snapshot["migration_id"].removeprefix("sha256:")[:24]
    transaction_id = next_status_transaction_id(memory_root, transaction_base)
    receipt_path = authority_migration_receipt_path(memory_root, snapshot["migration_id"])
    reserved = update_token_state(
        token_path,
        token_state,
        "reserved",
        business_transaction_id=transaction_id,
        receipt_path=str(receipt_path),
    )
    try:
        changed, publication, receipt_path, receipt = apply_authority_migration_snapshot(
            memory_root,
            snapshot,
            principal,
            transaction_id,
            args.fail_after_stage,
        )
    except Exception as exc:
        code = exc.error_code if isinstance(exc, StatusSyncContractError) else "AUTHORITY_MIGRATION_APPLY_FAILED"
        update_token_state(token_path, reserved, "invalidated", terminal_error_code=code)
        raise
    update_token_state(token_path, reserved, "consumed")
    emit(
        authority_migration_result(
            project_root,
            memory_root,
            snapshot,
            status="committed",
            dry_run=False,
            receipt_path=receipt_path,
            receipt=receipt,
            publication=publication,
            changed_paths=changed,
        ),
        args.output,
    )
    return 0


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

    normalized_original, duplicate_repairs = normalize_duplicate_canonical_fields(original)
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
    changed_fields: list[dict[str, Any]] = list(duplicate_repairs)
    updated = normalized_original
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


def duplicate_canonical_field_error(
    section_title: str,
    label: str,
    occurrences: list[dict[str, Any]],
) -> StatusSyncContractError:
    return StatusSyncContractError(
        "WDR_DUPLICATE_CANONICAL_FIELD",
        f"{section_title}.{label} has conflicting duplicate canonical fields",
        {
            "section": section_title,
            "field": label,
            "occurrences": occurrences,
            "repair_plan": {
                "operation": "deduplicate-canonical-field",
                "strategy": "choose one authoritative value, remove every other occurrence, then retry",
                "automatic_merge_allowed": False,
            },
        },
    )


def normalize_duplicate_canonical_fields(markdown: str) -> tuple[str, list[dict[str, Any]]]:
    lines = markdown.splitlines()
    repairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for section_title, label in VOLATILE_FIELDS.values():
        identity = (section_title.lower(), label.lower())
        if identity in seen:
            continue
        seen.add(identity)
        start, end = find_section(lines, section_title)
        if start is None:
            continue
        pattern = re.compile(rf"^\s*-\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE)
        matches = [
            {"index": index, "line": index + 1, "value": pattern.match(lines[index]).group(1).strip()}
            for index in range(start + 1, end)
            if pattern.match(lines[index])
        ]
        if len(matches) < 2:
            continue
        values = {item["value"] for item in matches}
        public_occurrences = [{"line": item["line"], "value": item["value"]} for item in matches]
        if len(values) != 1:
            raise duplicate_canonical_field_error(section_title, label, public_occurrences)
        for item in reversed(matches[1:]):
            del lines[item["index"]]
        repairs.append(
            {
                "field": label,
                "section": section_title,
                "before": f"{len(matches)} identical occurrences",
                "after": matches[0]["value"],
                "repair": "deduplicated-canonical-field",
            }
        )
    normalized = "\n".join(lines)
    if markdown.endswith("\n"):
        normalized += "\n"
    return normalized, repairs


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
    matches = [
        {"index": index, "line": index + 1, "value": pattern.match(lines[index]).group(1).strip()}
        for index in range(start + 1, end)
        if pattern.match(lines[index])
    ]
    if len(matches) > 1:
        values = {item["value"] for item in matches}
        public_occurrences = [{"line": item["line"], "value": item["value"]} for item in matches]
        if len(values) != 1:
            raise duplicate_canonical_field_error(section_title, label, public_occurrences)
        for item in reversed(matches[1:]):
            del lines[item["index"]]
    if matches:
        first = matches[0]
        old_value = first["value"]
        if old_value == value and len(matches) == 1:
            return markdown, old_value, False
        lines[first["index"]] = bullet
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


def ignore_runtime_lock_files(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name.lower().endswith(".lock")}


def copy_memory_tree(memory_root: Path, staged_root: Path) -> None:
    shutil.copytree(memory_root, staged_root, ignore=ignore_runtime_lock_files)


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
    supported = {
        "status-mutation", "repair-business", "repair-attempt",
        "intake-reconciliation", "intake-retirement", "wdr-field-repair", "workstream-alias-retirement", "l0-reference-repair",
    }
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
    input_path: Path | None = None,
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
        common_valid = (
            receipt.get("receipt_schema_version") == RECEIPT_SCHEMA_VERSION
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
        if receipt.get("receipt_type") == "reconciliation":
            valid = bool(input_path and common_valid and reconciliation_receipt_valid(receipt, input_path, input_hash, update_count))
        else:
            valid = common_valid and receipt.get("receipt_type") == "execution"
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


def historical_report_claims(
    payload: Any,
    evidence_path: Path,
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
    evidence_input_path = payload.get("input_path")
    if not isinstance(evidence_input_path, str) or not evidence_input_path.strip():
        raise ValueError("original evidence report must directly declare the exact updates-file input_path")
    return {
        "evidence_path": str(evidence_path),
        "evidence_hash": sha256_bytes(evidence_path.read_bytes()),
        "evidence_mode": "update",
        "declared_input_path": evidence_input_path,
        "declared_input_hash": "sha256:" + evidence_input_hash.removeprefix("sha256:"),
    }


def historical_path_matches(memory_root: Path | None, raw_path: str, expected: Path) -> bool:
    if Path(raw_path).expanduser().resolve() == expected.resolve():
        return True
    return bool(memory_root and resolve_receipt_input_path(memory_root, raw_path) == expected.resolve())


def historical_evidence(
    payload: Any,
    evidence_path: Path,
    input_path: Path,
    input_hash: str,
    update_count: int,
    memory_root: Path | None = None,
) -> dict[str, Any]:
    claims = historical_report_claims(payload, evidence_path, update_count)
    evidence_input_hash = str(claims["declared_input_hash"])
    if evidence_input_hash.removeprefix("sha256:") != input_hash.removeprefix("sha256:"):
        raise ValueError("evidence-file input_hash does not match updates-file raw bytes")
    evidence_input_path = str(claims["declared_input_path"])
    if not historical_path_matches(memory_root, evidence_input_path, input_path):
        raise ValueError("evidence-file input_path does not match the exact updates-file path")
    if evidence_path == input_path:
        raise ValueError("evidence-file must be distinct from updates-file")
    return {
        **{key: claims[key] for key in ("evidence_path", "evidence_hash", "evidence_mode")},
        "evidence_input_path": str(input_path),
        "evidence_input_hash": input_hash,
        "verification_status": "verified",
    }


def canonical_executable_payload_from_raw(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("status-sync payload root must be a JSON object")
    executable_keys = (
        "baseline_revision",
        "refresh_actions",
        "status_intents",
        "action_commands",
        "updates",
    )
    projection = {key: payload[key] for key in executable_keys if key in payload}
    if not any(key in projection for key in ("updates", "status_intents", "action_commands")):
        raise ValueError("status-sync payload contains no executable command envelope")
    return projection


def json_pointer_segment(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def json_diff_paths(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "/"]
    if isinstance(left, dict):
        differences: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            child = f"{path}/{json_pointer_segment(key)}"
            if key not in left or key not in right:
                differences.append(child)
            else:
                differences.extend(json_diff_paths(left[key], right[key], child))
        return differences
    if isinstance(left, list):
        differences = []
        for index in range(max(len(left), len(right))):
            child = f"{path}/{index}"
            if index >= len(left) or index >= len(right):
                differences.append(child)
            else:
                differences.extend(json_diff_paths(left[index], right[index], child))
        return differences
    return [] if left == right else [path or "/"]


def historical_input_change_evidence(
    payload: Any,
    evidence_path: Path,
    logical_input_path: Path,
    original_input_hash: str,
    update_count: int,
    memory_root: Path,
) -> dict[str, Any]:
    claims = historical_report_claims(payload, evidence_path, update_count)
    if str(claims["declared_input_hash"]) != original_input_hash:
        raise ValueError("evidence-file input_hash does not match restored original updates bytes")
    if not historical_path_matches(memory_root, str(claims["declared_input_path"]), logical_input_path):
        raise ValueError("evidence-file input_path does not match the governed logical updates-file path")
    if evidence_path.resolve() == logical_input_path.resolve():
        raise ValueError("evidence-file must be distinct from updates-file")
    return {
        **{key: claims[key] for key in ("evidence_path", "evidence_hash", "evidence_mode")},
        "evidence_input_path": str(logical_input_path),
        "evidence_input_hash": original_input_hash,
        "verification_status": "verified",
    }


def migration_plan_token(
    input_path: Path,
    input_hash: str,
    evidence_path: Path,
    evidence_hash: str,
    applied_at: str,
    attested_by: str,
    historical_input_change: dict[str, Any] | None = None,
) -> str:
    identity = {
        "input_path": str(input_path),
        "input_hash": input_hash,
        "evidence_path": str(evidence_path),
        "evidence_hash": evidence_hash,
        "applied_at": applied_at,
        "attested_by": attested_by,
        "historical_input_change": historical_input_change,
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"status-sync-migration-{digest}"


ACTION_RECONCILIATION_ROW_FIELDS = {
    "status": "Status",
    "owner": "Owner",
    "workstream": "Workstream",
    "affected_workstreams": "Affected Workstreams",
    "action": "Action",
    "source": "Source",
    "reason": "Reason",
    "due_or_trigger": "Due / Trigger",
    "closure_criteria": "Closure Criteria",
    "closure_criteria_verifiable": "Closure Criteria Verifiable",
    "created_at": "Created At",
    "started_at": "Started At",
    "done_at": "Done At",
    "cancelled_at": "Cancelled At",
    "baseline_revision": "Baseline Revision",
    "related_plan_item_ids": "Related Plan Items",
    "related_flow_edge_ids": "Related Flow Edges",
    "owning_workflow": "Owning Workflow",
}


def normalized_reconciliation_value(field_name: str, value: Any) -> Any:
    if field_name in {"affected_workstreams", "related_plan_item_ids", "related_flow_edge_ids"}:
        if isinstance(value, list):
            values = value
        else:
            values = [item.strip() for item in re.split(r"\s*[;,]\s*", str(value or "")) if item.strip()]
        return sorted(normalize_text_key(str(item)) for item in values)
    if field_name == "closure_criteria_verifiable":
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return True if text == "true" else False if text == "false" else None
    if field_name == "baseline_revision":
        text = str(value or "").strip()
        return int(text) if text.isdigit() else None
    return normalize_text_key(str(value or ""))


def requested_action_value(action: ActionUpdate, field_name: str) -> Any:
    return getattr(action, field_name)


def action_status_is_satisfied(requested: str | None, row: dict[str, str]) -> bool:
    if not requested:
        return True
    current = row.get("Status", "").strip().lower()
    if requested == current:
        return True
    if requested == "open":
        return current in ACTION_STATUSES
    if requested == "in-progress":
        return current in {"in-progress", "blocked", "done"} and bool(row.get("Started At", "").strip())
    return False


def action_composite_key_from_update(action: ActionUpdate) -> tuple[str, ...] | None:
    values = (
        action.action,
        action.owner,
        action.source,
        action.due_or_trigger,
        action.closure_criteria,
    )
    if any(is_missing_action_value(str(value or "")) for value in values):
        return None
    return tuple(normalize_text_key(str(value)) for value in values)


def action_composite_key_from_row(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        normalize_text_key(row.get(field, ""))
        for field in ("Action", "Owner", "Source", "Due / Trigger", "Closure Criteria")
    )


def action_composite_without_source_from_update(action: ActionUpdate) -> tuple[str, ...] | None:
    values = (action.action, action.owner, action.due_or_trigger, action.closure_criteria)
    if any(is_missing_action_value(str(value or "")) for value in values):
        return None
    return tuple(normalize_text_key(str(value)) for value in values)


def action_composite_without_source_from_row(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        normalize_text_key(row.get(field, ""))
        for field in ("Action", "Owner", "Due / Trigger", "Closure Criteria")
    )


def parse_structured_mapping(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        try:
            payload = ast.literal_eval(raw)
        except (ValueError, SyntaxError, TypeError):
            return None
    return payload if isinstance(payload, dict) else None


def source_provenance_bindings(raw_source: str) -> set[str]:
    bindings = {normalize_text_key(raw_source)} if raw_source.strip() else set()
    payload = parse_structured_mapping(raw_source)
    if payload is None:
        return bindings
    recognized = {
        "source", "original_source", "legacy_source", "meeting_source", "source_path",
        "path", "reference", "uri", "source_bindings", "provenance",
        "migration_provenance", "original", "binding",
    }

    def collect(value: Any, trusted: bool = False) -> None:
        if isinstance(value, str):
            if trusted and value.strip():
                bindings.add(normalize_text_key(value))
            return
        if isinstance(value, list):
            for item in value:
                collect(item, trusted)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            collect(item, trusted or normalized_key in recognized)

    collect(payload)
    return bindings


def action_ledger_artifact_source(raw_source: str) -> dict[str, str] | None:
    payload = parse_structured_mapping(raw_source)
    if payload is None:
        return None
    artifact_id = str(payload.get("artifact_id") or "").strip()
    artifact_path = str(payload.get("artifact_path") or "").strip().replace("\\", "/")
    fingerprint = str(payload.get("source_fingerprint") or "").strip().lower()
    if (
        artifact_id.casefold() != "action-ledger"
        or artifact_path != ACTION_LEDGER_REL.as_posix()
        or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None
    ):
        return None
    return {
        "artifact_id": artifact_id,
        "artifact_path": artifact_path,
        "source_fingerprint": fingerprint,
    }


def action_source_lineage(action: ActionUpdate, row: dict[str, str]) -> dict[str, Any] | None:
    requested = normalize_text_key(str(action.source or ""))
    if not requested or requested == normalize_text_key(row.get("Source", "")):
        return None
    bindings = source_provenance_bindings(row.get("Source", ""))
    if requested not in bindings:
        return None
    return {
        "type": "action-source-provenance",
        "action_id": row.get("Action ID", ""),
        "requested_source": action.source,
        "current_source": row.get("Source", ""),
        "binding": requested,
        "action_revision": action_revision(row),
        "row_fingerprint": content_id({field: row.get(field, "") for field in ACTION_FIELDS}),
    }


def action_daily_log_events(memory_root: Path, action_id: str) -> list[dict[str, Any]]:
    if not action_id:
        return []
    heading = re.compile(r"^##\s+(\S+)\s+Status sync - (.+?)\s*$")
    action_pattern = re.compile(
        rf"^\s+-\s+(open|in-progress|blocked|done|cancelled):\s+{re.escape(action_id)}\s*$",
        re.I,
    )
    events: list[dict[str, Any]] = []
    for path in sorted((memory_root / "daily").glob("*.md")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        for index, line in enumerate(lines):
            match = heading.match(line)
            if not match:
                continue
            end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            block_lines = lines[index + 1:end]
            action_match = next((action_pattern.match(item) for item in block_lines if action_pattern.match(item)), None)
            if action_match is None:
                continue
            source_line = next((item for item in block_lines if item.startswith("- Source:")), "")
            source = source_line.partition(":")[2].strip()
            try:
                observed_at = normalize_required_timestamp(match.group(1), "daily action timestamp")
            except ValueError:
                continue
            events.append({
                "type": "ordered-daily-log-action-lineage",
                "action_id": action_id,
                "status": action_match.group(1).lower(),
                "source": source,
                "workstream_id": safe_normalize_id(match.group(2)),
                "observed_at": observed_at,
                "paths": [path.relative_to(memory_root).as_posix()],
                "fingerprint": sha256_bytes(path.read_bytes()),
                "sequence": index,
            })
    return sorted(events, key=lineage_event_sort_key)


def lineage_event_sort_key(event: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(event.get("observed_at") or ""),
        str(event.get("daily_log") or (event.get("paths") or [""])[0]),
        int(event.get("sequence", -1)),
    )


def lineage_event_is_later(old: dict[str, Any], newer: dict[str, Any]) -> bool:
    old_at = str(old.get("observed_at") or "")
    newer_at = str(newer.get("observed_at") or "")
    if newer_at > old_at:
        return True
    if newer_at != old_at:
        return False
    old_log = str(old.get("daily_log") or "")
    newer_log = str(newer.get("daily_log") or "")
    return bool(
        old_log
        and old_log == newer_log
        and int(newer.get("sequence", -1)) > int(old.get("sequence", -1))
    )


def action_daily_log_lineage(
    memory_root: Path,
    action: ActionUpdate,
    action_id: str | None = None,
) -> dict[str, Any] | None:
    resolved_action_id = action_id or action.action_id
    if not resolved_action_id or not action.status:
        return None
    expected_sources = {
        normalize_text_key(value)
        for value in (action.source, action.intake_source)
        if value and not is_missing_action_value(str(value))
    }
    expected_workstream = safe_normalize_id(str(action.workstream or ""))
    matches = [
        event
        for event in action_daily_log_events(memory_root, resolved_action_id)
        if event["status"] == action.status
        and (not expected_sources or normalize_text_key(event["source"]) in expected_sources)
        and (
            not expected_workstream
            or expected_workstream == "program"
            or event["workstream_id"] in {expected_workstream, "program"}
        )
    ]
    return matches[-1] if matches else None


def legacy_action_artifact_lineage(
    memory_root: Path,
    action: ActionUpdate,
    row: dict[str, str],
) -> list[dict[str, Any]] | None:
    artifact = action_ledger_artifact_source(row.get("Source", ""))
    action_id = row.get("Action ID", "")
    if artifact is None or not action_id or not action.status:
        return None
    requested_targets = sorted(action.affected_workstreams or [])
    if (
        "affected_workstreams" not in action.declared_fields
        or not requested_targets
        or sorted(parse_workstream_cell(row.get("Affected Workstreams", ""))) != requested_targets
    ):
        return None
    allowed_scopes = {"program", *requested_targets}
    daily = [
        event
        for event in action_daily_log_events(memory_root, action_id)
        if event["status"] == action.status
        and event["source"].strip()
        and event["workstream_id"] in allowed_scopes
    ]
    if not daily:
        return None
    observation = daily[0]
    return [
        {
            "type": "legacy-action-artifact-source-normalization",
            "action_id": action_id,
            "requested_source": action.source,
            "current_source": row.get("Source", ""),
            "artifact": artifact,
            "exact_affected_workstreams": requested_targets,
            "action_revision": action_revision(row),
            "row_fingerprint": content_id({field: row.get(field, "") for field in ACTION_FIELDS}),
        },
        observation,
    ]


def current_action_daily_event(
    memory_root: Path,
    row: dict[str, str],
    old_event: dict[str, Any],
) -> dict[str, Any] | None:
    action_id = row.get("Action ID", "")
    current_source = normalize_text_key(row.get("Source", ""))
    current_status = row.get("Status", "").strip().lower()
    current_workstream = safe_normalize_id(row.get("Workstream", ""))
    matches = [
        event
        for event in action_daily_log_events(memory_root, action_id)
        if event["status"] == current_status
        and normalize_text_key(event["source"]) == current_source
        and (not current_workstream or event["workstream_id"] == current_workstream)
        and lineage_event_is_later(old_event, event)
    ]
    return matches[-1] if matches else None


def action_revision_lineage(
    memory_root: Path,
    action: ActionUpdate,
    row: dict[str, str],
    discrepancies: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    revised_fields = sorted(str(item.get("field") or "") for item in discrepancies)
    if not revised_fields or any(field in {"action", "owner", "due_or_trigger", "closure_criteria"} for field in revised_fields):
        return None
    old = action_daily_log_lineage(memory_root, action, row.get("Action ID"))
    if old is None:
        return None
    newer = current_action_daily_event(memory_root, row, old)
    if newer is None:
        return None
    return [
        old,
        {
            **newer,
            "type": "ordered-daily-log-action-revision",
            "revised_fields": revised_fields,
            "current_action_revision": action_revision(row),
            "current_row_fingerprint": content_id({field: row.get(field, "") for field in ACTION_FIELDS}),
        },
    ]


def program_action_route_lineage(
    action: ActionUpdate,
    row: dict[str, str],
    daily_lineage: dict[str, Any] | None,
    resolved_action_id: str | None = None,
) -> dict[str, Any] | None:
    requested_workstream = safe_normalize_id(str(action.workstream or ""))
    current_workstream = safe_normalize_id(str(row.get("Workstream", "") or ""))
    affected_workstreams = parse_workstream_cell(row.get("Affected Workstreams", ""))
    daily_workstream = safe_normalize_id(str((daily_lineage or {}).get("workstream_id", "") or ""))
    action_id = resolved_action_id or action.action_id
    if (
        not action_id
        or row.get("Action ID") != action_id
        or current_workstream != "program"
        or not requested_workstream
        or requested_workstream == "program"
        or requested_workstream not in affected_workstreams
        or not daily_lineage
        or daily_lineage.get("type") != "ordered-daily-log-action-lineage"
        or daily_workstream not in {requested_workstream, "program"}
    ):
        return None
    return {
        "type": "program-action-route-normalization",
        "action_id": action_id,
        "historical_workstream": requested_workstream,
        "current_workstream": current_workstream,
        "affected_workstreams": affected_workstreams,
        "daily_log_workstream": daily_workstream,
        "status": daily_lineage.get("status"),
        "source": daily_lineage.get("source"),
        "observed_at": daily_lineage.get("observed_at"),
        "paths": list(daily_lineage.get("paths", [])),
        "fingerprint": daily_lineage.get("fingerprint"),
    }


def reconcile_action_command(
    memory_root: Path,
    action: ActionUpdate,
    rows: list[dict[str, str]],
    applied_commands: dict[str, dict[str, Any]],
    ordinal: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "command_type": "action",
        "command_index": ordinal,
        "command_id": action.command_id,
        "operation": action.operation,
        "requested_action_id": action.action_id,
        "satisfied": False,
    }
    if action.workstream == "program" and not action.affected_workstreams:
        result["reason"] = "program action reconciliation requires explicit affected_workstreams provenance"
        return result
    if action.command_id and action.command_id in applied_commands:
        applied = applied_commands[action.command_id]
        if applied.get("command_fingerprint") != action_command_fingerprint(action):
            result["reason"] = "canonical command ledger contains the command_id with different command facts"
            return result
        matched_id = str(applied.get("action_id") or "")
        if not any(row.get("Action ID") == matched_id for row in rows):
            result["reason"] = "canonical command ledger points to a missing action"
            return result
        result.update({
            "satisfied": True,
            "satisfied_by": "superseded-lineage",
            "match_method": "command-ledger",
            "matched_action_id": matched_id,
            "lineage_evidence": [{"type": "action-command-history", "command_id": action.command_id}],
        })
        return result

    matched: dict[str, str] | None = None
    source_lineage: dict[str, Any] | None = None
    route_lineage: dict[str, Any] | None = None
    legacy_lineage: list[dict[str, Any]] = []
    if action.action_id:
        matched = next((row for row in rows if row.get("Action ID") == action.action_id), None)
        result["match_method"] = "stable-action-id"
        if matched is not None:
            daily_lineage = action_daily_log_lineage(memory_root, action)
            source_lineage = action_source_lineage(action, matched) or daily_lineage
            route_lineage = program_action_route_lineage(action, matched, daily_lineage)
    else:
        composite = action_composite_key_from_update(action)
        if composite is None:
            result["reason"] = "action lacks a stable action_id and the full action+owner+source+due+closure composite"
            return result
        candidates = [row for row in rows if action_composite_key_from_row(row) == composite]
        if len(candidates) == 1:
            matched = candidates[0]
            result["match_method"] = "action-owner-source-due-closure"
        else:
            base = action_composite_without_source_from_update(action)
            base_candidates = [] if base is None else [
                row for row in rows if action_composite_without_source_from_row(row) == base
            ]
            provenance_candidates = [
                (row, action_source_lineage(action, row)) for row in base_candidates
            ]
            provenance_candidates = [(row, lineage) for row, lineage in provenance_candidates if lineage is not None]
            if "affected_workstreams" in action.declared_fields:
                requested_targets = sorted(action.affected_workstreams or [])
                provenance_candidates = [
                    (row, lineage)
                    for row, lineage in provenance_candidates
                    if sorted(parse_workstream_cell(row.get("Affected Workstreams", ""))) == requested_targets
                ]
            if len(provenance_candidates) == 1:
                matched, source_lineage = provenance_candidates[0]
                result["match_method"] = "action-owner-due-closure-plus-provenance"
            else:
                exact_candidates = [
                    row for row in base_candidates
                    if "affected_workstreams" in action.declared_fields
                    and sorted(parse_workstream_cell(row.get("Affected Workstreams", ""))) == sorted(action.affected_workstreams or [])
                ]
                lineage_candidates = [
                    (row, legacy_action_artifact_lineage(memory_root, action, row))
                    for row in exact_candidates
                ]
                lineage_candidates = [(row, lineage) for row, lineage in lineage_candidates if lineage is not None]
                if len(lineage_candidates) == 1:
                    matched, legacy_lineage = lineage_candidates[0]
                    source_lineage = legacy_lineage[0]
                    daily_lineage = legacy_lineage[1]
                    route_lineage = program_action_route_lineage(
                        action,
                        matched,
                        daily_lineage,
                        matched.get("Action ID"),
                    )
                    result["match_method"] = "action-owner-due-closure-affected-plus-artifact-lineage"
                else:
                    result["reason"] = "action composite did not resolve exactly once"
                    result["candidate_action_ids"] = sorted(row.get("Action ID", "") for row in exact_candidates)
                    return result
    if matched is None:
        result["reason"] = "canonical ledger action was not found"
        return result
    result["matched_action_id"] = matched.get("Action ID")
    discrepancies: list[dict[str, Any]] = []
    for field_name in sorted(action.declared_fields):
        if field_name == "status":
            if not action_status_is_satisfied(action.status, matched):
                discrepancies.append({"field": field_name, "requested": action.status, "current": matched.get("Status", "")})
            continue
        requested = normalized_reconciliation_value(field_name, requested_action_value(action, field_name))
        current = normalized_reconciliation_value(field_name, matched.get(ACTION_RECONCILIATION_ROW_FIELDS[field_name], ""))
        if requested != current:
            if field_name in {"source", "owning_workflow"} and source_lineage is not None:
                continue
            if field_name == "workstream" and route_lineage is not None:
                continue
            discrepancies.append({"field": field_name, "requested": requested, "current": current})
    current_revision = action_revision(matched)
    result["current_action_revision"] = current_revision
    if action.expected_revision is not None and current_revision < action.expected_revision:
        discrepancies.append(
            {"field": "action_revision", "requested_minimum": action.expected_revision, "current": current_revision}
        )
    revision_lineage = action_revision_lineage(memory_root, action, matched, discrepancies)
    if discrepancies and revision_lineage is None:
        result["reason"] = "canonical action facts do not satisfy the historical command"
        result["discrepancies"] = discrepancies
        return result
    result["satisfied"] = True
    lineage_evidence = [*legacy_lineage]
    for item in (source_lineage, route_lineage):
        if item is not None and item not in lineage_evidence:
            lineage_evidence.append(item)
    if revision_lineage:
        lineage_evidence.extend(item for item in revision_lineage if item not in lineage_evidence)
        result["superseded_fields"] = sorted(str(item.get("field") or "") for item in discrepancies)
    if lineage_evidence:
        result["satisfied_by"] = "superseded-lineage"
        result["lineage_evidence"] = lineage_evidence
    else:
        result["satisfied_by"] = "current-fact"
    return result


def load_reconciliation_wdr(memory_root: Path, workstream_id: str) -> dict[str, Any]:
    record_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
    state_path = wdr_state_path(record_path)
    if not record_path.is_file() or not state_path.is_file():
        raise StatusSyncContractError(
            "INTAKE_RECONCILIATION_LINEAGE_MISSING",
            f"current WDR lineage is missing for {workstream_id}",
        )
    state = load_existing_json_object(state_path, "INTAKE_RECONCILIATION_LINEAGE_INVALID", "WDR state")
    errors = validate_wdr_state(record_path, state)
    if errors:
        raise StatusSyncContractError(
            "INTAKE_RECONCILIATION_LINEAGE_INVALID",
            f"current WDR lineage is invalid for {workstream_id}: " + "; ".join(errors),
        )
    text = record_path.read_text(encoding="utf-8")
    return {"record_path": record_path, "state_path": state_path, "state": state, "text": text}


def canonical_wdr_field_value(markdown: str, section_title: str, label: str) -> tuple[str | None, str | None]:
    lines = markdown.splitlines()
    start, end = find_section(lines, section_title)
    if start is None:
        return None, "canonical section is missing"
    pattern = re.compile(rf"^\s*-\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE)
    matches = [pattern.match(lines[index]).group(1).strip() for index in range(start + 1, end) if pattern.match(lines[index])]
    if not matches:
        return None, "canonical field is missing"
    if len(matches) != 1:
        return None, "canonical field occurs more than once"
    return matches[0], None


def resolve_receipt_input_path(memory_root: Path, raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    direct = Path(raw_path).expanduser()
    if direct.is_file():
        return direct.resolve()
    if not direct.is_absolute():
        relative_candidate = (memory_root / direct).resolve()
        try:
            relative_candidate.relative_to(memory_root.resolve())
        except ValueError:
            relative_candidate = memory_root / "__invalid__"
        if relative_candidate.is_file():
            return relative_candidate
    parts = [part for part in raw_path.replace("\\", "/").split("/") if part]
    anchor = ["_bmad-output", "adp", "memory"]
    folded = [part.casefold() for part in parts]
    for index in range(len(parts) - len(anchor) + 1):
        if folded[index:index + len(anchor)] == anchor:
            candidate = memory_root.joinpath(*parts[index + len(anchor):])
            return candidate.resolve() if candidate.is_file() else None
    return None


def historical_input_change_migration_valid(
    memory_root: Path,
    receipt: dict[str, Any],
    input_path: Path,
    current_payload: Any,
    current_updates: list[StatusUpdate],
) -> bool:
    migration = receipt.get("migration") if isinstance(receipt.get("migration"), dict) else {}
    original_hash = str(migration.get("original_input_hash") or "").strip().lower()
    current_hash = str(migration.get("current_input_hash") or "").strip().lower()
    snapshot_path = resolve_receipt_input_path(memory_root, migration.get("original_input_snapshot_path"))
    evidence_path = resolve_receipt_input_path(memory_root, migration.get("evidence_path"))
    if (
        migration.get("migration_kind") != "historical-input-change"
        or migration.get("verification_status") != "verified"
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", original_hash)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", current_hash)
        or original_hash == current_hash
        or current_hash != receipt.get("input_hash")
        or snapshot_path is None
        or evidence_path is None
    ):
        return False
    try:
        snapshot_relative = snapshot_path.resolve().relative_to(memory_root.resolve())
    except ValueError:
        return False
    if snapshot_relative.parts[:3] != HISTORICAL_INPUT_MIGRATION_EVIDENCE_REL.parts:
        return False
    original_bytes = snapshot_path.read_bytes()
    if sha256_bytes(original_bytes) != original_hash:
        return False
    try:
        original_payload, original_updates = load_updates_payload(snapshot_path, "status sync")
        evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
        evidence = historical_input_change_evidence(
            evidence_payload,
            evidence_path,
            input_path,
            original_hash,
            len(original_updates),
            memory_root,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    original_canonical = canonical_executable_payload_from_raw(original_payload)
    current_canonical = canonical_executable_payload_from_raw(current_payload)
    executable_changed_paths = json_diff_paths(original_canonical, current_canonical)
    diff_body = {
        "equal": not executable_changed_paths,
        "changed_paths": executable_changed_paths,
        "non_executable_changed_paths": json_diff_paths(original_payload, current_payload),
    }
    expected_diff = {**diff_body, "diff_id": content_id(diff_body)}
    expected_evidence = {
        **evidence,
        "attested_by": str(migration.get("attested_by") or "").strip(),
    }
    return bool(
        not executable_changed_paths
        and expected_evidence["attested_by"]
        and migration.get("original_input_snapshot_hash") == original_hash
        and migration.get("original_payload_id") == content_id(original_payload)
        and migration.get("current_payload_id") == content_id(current_payload)
        and migration.get("canonical_executable_payload_id") == content_id(original_canonical)
        and migration.get("executable_diff") == expected_diff
        and all(migration.get(key) == value for key, value in expected_evidence.items())
    )


def durable_status_receipt_record(memory_root: Path, receipt_path: Path) -> dict[str, Any] | None:
    receipt = load_json_object(receipt_path)
    if (
        receipt.get("receipt_schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("receipt_type") not in {"execution", "migration", "reconciliation"}
        or receipt.get("ok") is not True
        or receipt.get("status") != "applied"
        or receipt.get("durable") is not True
        or receipt.get("dry_run") is not False
        or receipt.get("mode") != "update"
    ):
        return None
    try:
        applied_at = normalize_required_timestamp(receipt.get("applied_at"), "receipt applied_at")
    except ValueError:
        return None
    input_path = resolve_receipt_input_path(memory_root, receipt.get("input_path"))
    if input_path is None:
        return None
    input_hash = sha256_bytes(input_path.read_bytes())
    if receipt.get("input_hash") != input_hash:
        return None
    try:
        payload, updates = load_updates_payload(input_path, "status sync")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if len(updates) != receipt.get("update_count"):
        return None
    if receipt.get("receipt_type") == "migration":
        migration = receipt.get("migration") if isinstance(receipt.get("migration"), dict) else {}
        if migration.get("migration_kind") == "historical-input-change":
            if not historical_input_change_migration_valid(memory_root, receipt, input_path, payload, updates):
                return None
        else:
            evidence_path = resolve_receipt_input_path(memory_root, migration.get("evidence_path"))
            if evidence_path is None:
                return None
            try:
                evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
                historical_evidence(
                    evidence_payload,
                    evidence_path,
                    input_path,
                    input_hash,
                    len(updates),
                    memory_root,
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return None
    if receipt.get("receipt_type") == "reconciliation":
        raw_input_path = Path(str(receipt.get("input_path")))
        if not reconciliation_receipt_valid(receipt, raw_input_path, input_hash, len(updates)):
            return None
    return {
        "receipt": receipt,
        "receipt_path": receipt_path,
        "input_path": input_path,
        "input_hash": input_hash,
        "payload": payload,
        "updates": updates,
        "applied_at": applied_at,
    }


def durable_receipts_for_input(memory_root: Path, input_path: Path) -> list[dict[str, Any]]:
    expected = input_path.resolve()
    matches: list[dict[str, Any]] = []
    for receipt_path in sorted((memory_root / STATUS_SYNC_RECEIPT_REL).glob("*.json")):
        record = durable_status_receipt_record(memory_root, receipt_path)
        if record is not None and record["input_path"].resolve() == expected:
            matches.append(record)
    return sorted(matches, key=lambda item: item["applied_at"])


def update_reconciliation_field_value(update: StatusUpdate, field_name: str) -> str | None:
    if field_name in update.current_fields_present:
        value = getattr(update, field_name)
        return "; ".join(value) if isinstance(value, list) else str(value or "")
    if field_name == "next_actions" and update.next_actions_provided:
        return "; ".join(update.next_actions)
    return None


def durable_status_receipt_events(memory_root: Path, workstream_id: str, field_name: str) -> list[dict[str, Any]]:
    root = memory_root / STATUS_SYNC_RECEIPT_REL
    events: list[dict[str, Any]] = []
    for receipt_path in sorted(root.glob("ssr-*.json")):
        receipt = load_json_object(receipt_path)
        if (
            receipt.get("receipt_type") not in {"execution", "migration"}
            or receipt.get("ok") is not True
            or receipt.get("status") != "applied"
            or receipt.get("durable") is not True
            or receipt.get("dry_run") is not False
            or receipt.get("mode") != "update"
        ):
            continue
        try:
            applied_at = normalize_required_timestamp(receipt.get("applied_at"), "receipt applied_at")
        except ValueError:
            continue
        input_path = resolve_receipt_input_path(memory_root, receipt.get("input_path"))
        if input_path is None:
            continue
        if receipt.get("input_hash") != sha256_bytes(input_path.read_bytes()):
            continue
        if receipt.get("receipt_type") == "migration":
            migration = receipt.get("migration") if isinstance(receipt.get("migration"), dict) else {}
            evidence_path = resolve_receipt_input_path(memory_root, migration.get("evidence_path"))
            if evidence_path is None:
                continue
            try:
                evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
                historical_evidence(
                    evidence_payload,
                    evidence_path,
                    input_path,
                    str(receipt.get("input_hash")),
                    int(receipt.get("update_count")),
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        try:
            _, updates = load_updates_payload(input_path, "status sync")
        except (ValueError, TypeError):
            continue
        if len(updates) != receipt.get("update_count"):
            continue
        for update in updates:
            if update.workstream_id != workstream_id:
                continue
            value = update_reconciliation_field_value(update, field_name)
            if value is None:
                continue
            paths = [receipt_path]
            try:
                input_path.relative_to(memory_root)
                paths.append(input_path)
            except ValueError:
                pass
            events.append({
                "type": "status-execution-receipt",
                "observed_at": applied_at,
                "value": value,
                "paths": [path.relative_to(memory_root).as_posix() for path in paths],
                "receipt": receipt_path.relative_to(memory_root).as_posix(),
                "input_hash": receipt.get("input_hash"),
            })
    return events


DAILY_LOG_FIELD_LABELS = {
    "progress": "Progress",
    "blockers": "Blockers",
    "risks": "Risks",
    "dependencies": "Dependencies",
    "change_notes": "Change notes",
    "next_actions": "Next actions",
}

WDR_STRUCTURED_METADATA_SUFFIX = (
    re.compile(r"^(?:audit|checkpoint|risk|severity|likelihood|status|metadata|source|gate)\s*[:=]\s*\S(?:.*\S)?$", re.I),
    re.compile(r"^risk_id\s*:\s*RISK-[A-Z0-9][A-Z0-9._-]*$", re.I),
    re.compile(r"^baseline_revision\s*:\s*[1-9]\d*$", re.I),
    re.compile(r"^related_plan_item_ids\s*:\s*[A-Z][A-Z0-9._-]*(?:\s*[+,]\s*[A-Z][A-Z0-9._-]*)*$", re.I),
    re.compile(r"^Candidate\s+CHK-[A-Z0-9][A-Z0-9._-]*\s+from\s+(?:prd|architecture|epics?|stories?|artifact):\S.*$", re.I),
)

RISK_STRUCTURED_METADATA_SUFFIX = WDR_STRUCTURED_METADATA_SUFFIX[1:4]


def is_structured_wdr_metadata(field_name: str, value: str) -> bool:
    patterns = RISK_STRUCTURED_METADATA_SUFFIX if field_name == "risks" else WDR_STRUCTURED_METADATA_SUFFIX
    return any(pattern.fullmatch(value.strip()) for pattern in patterns)


def daily_status_lineage_events(memory_root: Path, workstream_id: str, field_name: str) -> list[dict[str, Any]]:
    label = DAILY_LOG_FIELD_LABELS.get(field_name)
    if not label:
        return []
    canonical_label = VOLATILE_FIELDS[field_name][1]
    events: list[dict[str, Any]] = []
    heading = re.compile(r"^##\s+(\S+)\s+Status sync - (.+?)\s*$")
    for path in sorted((memory_root / "daily").glob("*.md")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        for index, line in enumerate(lines):
            match = heading.match(line)
            if not match or normalize_id(match.group(2)) != workstream_id:
                continue
            try:
                observed_at = normalize_required_timestamp(match.group(1), "daily log timestamp")
            except ValueError:
                continue
            end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            block = lines[index + 1:end]
            changed_line = next((item for item in block if item.startswith("- Changed fields:")), "")
            changed = [item.strip().casefold() for item in changed_line.partition(":")[2].split(",")]
            if canonical_label.casefold() not in changed:
                continue
            marker = f"- {label}:"
            field_index = next((i for i, item in enumerate(block) if item.startswith(marker)), None)
            if field_index is None:
                continue
            suffix = block[field_index].partition(":")[2].strip()
            if suffix:
                value = suffix
            else:
                items: list[str] = []
                for item in block[field_index + 1:]:
                    if item.startswith("  - "):
                        items.append(item[4:].strip())
                    elif item.strip():
                        break
                value = "; ".join(items)
            events.append({
                "type": "daily-log-status-lineage",
                "observed_at": observed_at,
                "value": value,
                "paths": [path.relative_to(memory_root).as_posix()],
                "daily_log": path.relative_to(memory_root).as_posix(),
                "sequence": index,
                "fingerprint": sha256_bytes(path.read_bytes()),
            })
    return events


def daily_status_command_events(memory_root: Path, workstream_id: str, field_name: str) -> list[dict[str, Any]]:
    canonical_label = VOLATILE_FIELDS[field_name][1]
    heading = re.compile(r"^##\s+(\S+)\s+Status sync - (.+?)\s*$")
    observations: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((memory_root / "daily").glob("*.md")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        for index, line in enumerate(lines):
            match = heading.match(line)
            if not match or safe_normalize_id(match.group(2)) != workstream_id:
                continue
            end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            block = lines[index + 1:end]
            source_line = next((item for item in block if item.startswith("- Source:")), "")
            source = source_line.partition(":")[2].strip()
            if not source:
                continue
            changed_line = next((item for item in block if item.startswith("- Changed fields:")), "")
            changed = [item.strip().casefold() for item in changed_line.partition(":")[2].split(",")]
            if canonical_label.casefold() not in changed:
                continue
            try:
                observed_at = normalize_required_timestamp(match.group(1), "daily status command timestamp")
            except ValueError:
                continue
            observations.setdefault(normalize_text_key(source), []).append({
                "observed_at": observed_at,
                "source": source,
                "daily_log": path.relative_to(memory_root).as_posix(),
                "sequence": index,
                "fingerprint": sha256_bytes(path.read_bytes()),
            })
    if not observations:
        return []

    candidates: dict[str, list[dict[str, Any]]] = {}
    intake_root = memory_root / "intake/status-sync"
    for intake_path in sorted(intake_root.glob("*.json")):
        try:
            _, updates = load_updates_payload(intake_path, "status sync")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        for update in updates:
            if update.workstream_id != workstream_id:
                continue
            value = update_reconciliation_field_value(update, field_name)
            if value is None or not update.source.strip():
                continue
            source_key = normalize_text_key(update.source)
            if source_key not in observations:
                continue
            candidates.setdefault(source_key, []).append({
                "value": value,
                "input_path": intake_path,
                "input_hash": sha256_bytes(intake_path.read_bytes()),
            })

    events: list[dict[str, Any]] = []
    for source_key, source_observations in observations.items():
        source_candidates = candidates.get(source_key, [])
        unique = {
            (normalized_reconciliation_value(field_name, item["value"]), item["input_hash"]): item
            for item in source_candidates
        }
        if len(unique) != 1:
            continue
        candidate = next(iter(unique.values()))
        for observation in source_observations:
            events.append({
                "type": "ordered-daily-log-intake-command-lineage",
                "observed_at": observation["observed_at"],
                "value": candidate["value"],
                "source": observation["source"],
                "workstream_id": workstream_id,
                "field": field_name,
                "paths": [
                    observation["daily_log"],
                    candidate["input_path"].relative_to(memory_root).as_posix(),
                ],
                "daily_log": observation["daily_log"],
                "sequence": observation["sequence"],
                "fingerprint": observation["fingerprint"],
                "input_hash": candidate["input_hash"],
            })
    return sorted(events, key=lineage_event_sort_key)


def status_superseded_lineage(
    memory_root: Path,
    workstream_id: str,
    field_name: str,
    requested_value: Any,
    current_value: Any,
    cache: dict[tuple[str, str], list[dict[str, Any]]],
    current_wdr: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    key = (workstream_id, field_name)
    if key not in cache:
        cache[key] = [
            *durable_status_receipt_events(memory_root, workstream_id, field_name),
            *daily_status_lineage_events(memory_root, workstream_id, field_name),
            *daily_status_command_events(memory_root, workstream_id, field_name),
        ]
    events = cache[key]
    requested = normalized_reconciliation_value(field_name, requested_value)
    current = normalized_reconciliation_value(field_name, current_value)
    old_events = sorted(
        (event for event in events if normalized_reconciliation_value(field_name, event["value"]) == requested),
        key=lambda event: event["observed_at"],
    )
    current_events = sorted(
        (event for event in events if normalized_reconciliation_value(field_name, event["value"]) == current),
        key=lambda event: event["observed_at"],
    )
    pairs = [
        (old, newer)
        for old in old_events
        for newer in current_events
        if lineage_event_is_later(old, newer)
    ]
    if not pairs and field_name in {"change_notes", "risks"} and current_wdr:
        requested_parts = [part.strip() for part in str(requested_value or "").split(";") if part.strip()]
        current_parts = [part.strip() for part in str(current_value or "").split(";") if part.strip()]
        suffix = current_parts[len(requested_parts):] if current_parts[:len(requested_parts)] == requested_parts else []
        daily_old_events = [event for event in old_events if event.get("type") == "daily-log-status-lineage"]
        if suffix and daily_old_events and all(is_structured_wdr_metadata(field_name, part) for part in suffix):
            old = daily_old_events[-1]
            state = current_wdr["state"]
            return [old, {
                "type": "current-wdr-structured-append-lineage",
                "workstream_id": workstream_id,
                "field": field_name,
                "appended_metadata": suffix,
                "wdr_revision": state.get("wdr_revision"),
                "file_generation": state.get("file_generation"),
                "wdr_fingerprint": state.get("wdr_fingerprint"),
                "paths": [
                    current_wdr["record_path"].relative_to(memory_root).as_posix(),
                    current_wdr["state_path"].relative_to(memory_root).as_posix(),
                ],
            }]
    if not pairs:
        return None
    old, newer = sorted(pairs, key=lambda pair: (pair[0]["observed_at"], pair[1]["observed_at"]))[-1]
    return [old, newer]


def reconcile_status_field(
    memory_root: Path,
    update: StatusUpdate,
    field_name: str,
    requested_value: Any,
    wdr: dict[str, Any],
    ordinal: int,
    lineage_cache: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    section_title, label = VOLATILE_FIELDS[field_name]
    current, error = canonical_wdr_field_value(wdr["text"], section_title, label)
    result = {
        "command_type": "status-field",
        "command_index": ordinal,
        "workstream_id": update.workstream_id,
        "field": field_name,
        "requested": requested_value,
        "satisfied": False,
        "lineage": {
            "wdr_revision": wdr["state"]["wdr_revision"],
            "file_generation": wdr["state"]["file_generation"],
        },
    }
    if error:
        result["reason"] = error
        return result
    requested = normalized_reconciliation_value(field_name, requested_value)
    actual = normalized_reconciliation_value(field_name, current)
    result["current"] = current
    result["satisfied"] = requested == actual
    if result["satisfied"]:
        result["satisfied_by"] = "current-fact"
        return result
    lineage = status_superseded_lineage(
        memory_root,
        update.workstream_id,
        field_name,
        requested_value,
        current,
        lineage_cache,
        wdr,
    )
    if lineage:
        result["satisfied"] = True
        result["satisfied_by"] = "superseded-lineage"
        result["lineage_evidence"] = lineage
        return result
    result["reason"] = "current canonical WDR fact differs and no durable superseded lineage proves the historical value"
    return result


def reconcile_refresh_actions(
    update: StatusUpdate,
    rows: list[dict[str, str]],
    ledger_state: dict[str, Any],
    wdr: dict[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    result = {
        "command_type": "refresh-actions",
        "command_index": ordinal,
        "workstream_id": update.workstream_id,
        "satisfied": False,
    }
    sidecar_path = wdr["record_path"].with_name(ACTION_PROJECTION_REL)
    if not sidecar_path.is_file():
        result["reason"] = "canonical action projection sidecar is missing"
        return result
    sidecar = load_existing_json_object(
        sidecar_path,
        "INTAKE_RECONCILIATION_LINEAGE_INVALID",
        "action projection",
    )
    expected = build_action_projection_payload(update.workstream_id, rows, ledger_state, wdr["state"])
    if sidecar != expected:
        result["reason"] = "canonical action projection sidecar is not current"
        return result
    current_next_actions, error = canonical_wdr_field_value(wdr["text"], "Project Status", "Next actions")
    if error:
        result["reason"] = error
        return result
    expected_ids = sorted(item["action_id"] for item in expected["actions"])
    current_ids = sorted(
        action_id
        for item in split_next_actions(current_next_actions or "")
        if (action_id := action_summary_id(item))
    )
    if current_ids != expected_ids:
        result["reason"] = "WDR Next actions markers do not match the canonical open-action projection"
        result["expected_action_ids"] = expected_ids
        result["current_action_ids"] = current_ids
        return result
    result.update({"satisfied": True, "action_ids": expected_ids})
    return result


MILESTONE_CORRECTION_INTAKE_REF = re.compile(
    r"(?:_bmad-output/adp/memory/)?(intake/status-sync/[A-Za-z0-9][A-Za-z0-9._-]*\.json)(?:#[^;\s]+)?"
)


def milestone_command_values(milestone: MilestoneUpdate, baseline_revision: int) -> dict[str, str]:
    values = {
        "Status": milestone.status,
        "Baseline Revision": str(baseline_revision),
        "Source": "; ".join(milestone.evidence),
    }
    if milestone.forecast:
        values["Forecast"] = milestone.forecast
    if milestone.actual:
        values["Actual"] = milestone.actual
    return values


def milestone_daily_log_events(
    memory_root: Path,
    workstream_id: str,
    milestone_id: str,
) -> list[dict[str, Any]]:
    heading = re.compile(r"^##\s+(\S+)\s+Status sync - (.+?)\s*$")
    row_pattern = re.compile(
        rf"^\s{{2}}-\s+{re.escape(milestone_id)}:\s+(planned|in-progress|at-risk|done|blocked)(?:\s+\((.*?)\))?\s*$",
        re.I,
    )
    events: list[dict[str, Any]] = []
    for path in sorted((memory_root / "daily").glob("*.md")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        for index, line in enumerate(lines):
            match = heading.match(line)
            if not match or safe_normalize_id(match.group(2)) != workstream_id:
                continue
            end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            block = lines[index + 1:end]
            for block_index, item in enumerate(block):
                row_match = row_pattern.match(item)
                if row_match is None:
                    continue
                details = {"Forecast": "", "Actual": ""}
                for part in [value.strip() for value in str(row_match.group(2) or "").split(",") if value.strip()]:
                    if part.startswith("forecast "):
                        details["Forecast"] = part.removeprefix("forecast ").strip()
                    elif part.startswith("actual "):
                        details["Actual"] = part.removeprefix("actual ").strip()
                evidence: list[str] = []
                for following in block[block_index + 1:]:
                    if following.startswith("    - Evidence:"):
                        evidence.append(following.partition(":")[2].strip())
                    elif following.startswith("  - ") or (following.startswith("-") and following.strip()):
                        break
                try:
                    observed_at = normalize_required_timestamp(match.group(1), "daily milestone timestamp")
                except ValueError:
                    continue
                events.append({
                    "type": "ordered-daily-log-milestone-lineage",
                    "observed_at": observed_at,
                    "workstream_id": workstream_id,
                    "milestone_id": milestone_id,
                    "values": {
                        "Status": row_match.group(1).lower(),
                        "Forecast": details["Forecast"],
                        "Actual": details["Actual"],
                        "Source": "; ".join(evidence),
                    },
                    "source": next((line.partition(":")[2].strip() for line in block if line.startswith("- Source:")), ""),
                    "paths": [path.relative_to(memory_root).as_posix()],
                    "daily_log": path.relative_to(memory_root).as_posix(),
                    "sequence": index + block_index,
                    "fingerprint": sha256_bytes(path.read_bytes()),
                })
    return sorted(events, key=lineage_event_sort_key)


def milestone_receipt_events(
    memory_root: Path,
    workstream_id: str,
    milestone_id: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for receipt_path in sorted((memory_root / STATUS_SYNC_RECEIPT_REL).glob("*.json")):
        record = durable_status_receipt_record(memory_root, receipt_path)
        if record is None:
            continue
        for update in record["updates"]:
            if update.workstream_id != workstream_id:
                continue
            for milestone in update.milestones:
                if milestone.milestone_id != milestone_id:
                    continue
                expected_revision = milestone.expected_baseline_revision
                values = milestone_command_values(milestone, expected_revision or 0)
                events.append({
                    "type": "status-execution-receipt-milestone-lineage",
                    "observed_at": record["applied_at"],
                    "workstream_id": workstream_id,
                    "milestone_id": milestone_id,
                    "values": values,
                    "baseline_revision": expected_revision,
                    "paths": [
                        receipt_path.relative_to(memory_root).as_posix(),
                        *([record["input_path"].relative_to(memory_root).as_posix()]
                          if record["input_path"].is_relative_to(memory_root) else []),
                    ],
                    "receipt": receipt_path.relative_to(memory_root).as_posix(),
                    "input_hash": record["input_hash"],
                })
    return sorted(events, key=lineage_event_sort_key)


def milestone_event_matches(event: dict[str, Any], expected: dict[str, str]) -> bool:
    values = event.get("values") if isinstance(event.get("values"), dict) else {}
    return all(
        normalized_reconciliation_value(field, values.get(field, ""))
        == normalized_reconciliation_value(field, value)
        for field, value in expected.items()
        if field != "Baseline Revision"
    )


def milestone_correction_references(memory_root: Path, source: str) -> list[Path]:
    paths: list[Path] = []
    for match in MILESTONE_CORRECTION_INTAKE_REF.finditer(source):
        path = memory_root / match.group(1)
        if path.is_file() and path not in paths:
            paths.append(path)
    return paths


def milestone_superseded_lineage(
    memory_root: Path,
    update: StatusUpdate,
    milestone: MilestoneUpdate,
    row: dict[str, str],
    historical_revision: int,
    current_revision: int,
    wdr: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    historical_values = milestone_command_values(milestone, historical_revision)
    old_events = sorted([
        event
        for event in [
            *milestone_daily_log_events(memory_root, update.workstream_id, milestone.milestone_id),
            *milestone_receipt_events(memory_root, update.workstream_id, milestone.milestone_id),
        ]
        if milestone_event_matches(event, historical_values)
        and (
            event.get("baseline_revision") in {None, 0, historical_revision}
            or event.get("type") == "ordered-daily-log-milestone-lineage"
        )
    ], key=lineage_event_sort_key)
    current_values = {
        field: row.get(field, "")
        for field in ("Status", "Forecast", "Actual", "Source")
        if field in historical_values or field in {"Status", "Source"}
    }
    correction_paths = milestone_correction_references(memory_root, row.get("Source", ""))
    missing_receipts: list[str] = []
    if not old_events and not correction_paths:
        return None, []
    current_events: list[dict[str, Any]] = []
    if correction_paths:
        for correction_path in correction_paths:
            receipts = durable_receipts_for_input(memory_root, correction_path)
            if not receipts:
                missing_receipts.append(correction_path.relative_to(memory_root).as_posix())
                continue
            for receipt_record in receipts:
                matches = [
                    candidate
                    for candidate_update in receipt_record["updates"]
                    if candidate_update.workstream_id == update.workstream_id
                    for candidate in candidate_update.milestones
                    if candidate.milestone_id == milestone.milestone_id
                ]
                if len(matches) != 1:
                    continue
                candidate = matches[0]
                candidate_values = milestone_command_values(
                    candidate,
                    candidate.expected_baseline_revision or current_revision,
                )
                effective_values = {**current_values, **candidate_values}
                synthetic = {"values": effective_values}
                if not milestone_event_matches(synthetic, current_values):
                    continue
                current_events.append({
                    "type": "receipt-bound-milestone-correction-lineage",
                    "observed_at": receipt_record["applied_at"],
                    "workstream_id": update.workstream_id,
                    "milestone_id": milestone.milestone_id,
                    "baseline_revision": candidate.expected_baseline_revision or current_revision,
                    "correction_intake": correction_path.relative_to(memory_root).as_posix(),
                    "receipt": receipt_record["receipt_path"].relative_to(memory_root).as_posix(),
                    "input_hash": receipt_record["input_hash"],
                    "values": candidate_values,
                    "paths": [
                        correction_path.relative_to(memory_root).as_posix(),
                        receipt_record["receipt_path"].relative_to(memory_root).as_posix(),
                    ],
                })
    else:
        current_events = [
            event
            for event in [
                *milestone_daily_log_events(memory_root, update.workstream_id, milestone.milestone_id),
                *milestone_receipt_events(memory_root, update.workstream_id, milestone.milestone_id),
            ]
            if milestone_event_matches(event, current_values)
            and (
                historical_revision == current_revision
                or event.get("baseline_revision") == current_revision
            )
        ]
    if missing_receipts:
        return None, sorted(missing_receipts)
    if not old_events:
        return None, []
    old = old_events[-1]
    later = [event for event in current_events if lineage_event_is_later(old, event)]
    if not later:
        return None, sorted(missing_receipts)
    newer = sorted(later, key=lineage_event_sort_key)[-1]
    return [old, {
        **newer,
        "roadmap_revision": wdr["state"].get("wdr_revision"),
        "file_generation": wdr["state"].get("file_generation"),
        "current_baseline_revision": current_revision,
        "current_wdr_fingerprint": wdr["state"].get("wdr_fingerprint"),
    }], sorted(missing_receipts)


def reconcile_milestone_command(
    memory_root: Path,
    update: StatusUpdate,
    milestone: MilestoneUpdate,
    baseline: dict[str, Any],
    baseline_revision: int,
    wdr: dict[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    historical_revision = milestone.expected_baseline_revision or baseline_revision
    result: dict[str, Any] = {
        "command_type": "milestone",
        "command_index": ordinal,
        "workstream_id": update.workstream_id,
        "milestone_id": milestone.milestone_id,
        "satisfied": False,
        "historical_baseline_revision": historical_revision,
        "current_baseline_revision": baseline_revision,
        "lineage": {
            "wdr_revision": wdr["state"]["wdr_revision"],
            "file_generation": wdr["state"]["file_generation"],
        },
    }
    if historical_revision > baseline_revision:
        result["reason"] = "historical milestone command targets a future baseline revision"
        return result
    baseline_rows = baseline.get("milestones")
    if not isinstance(baseline_rows, list):
        result["reason"] = "current canonical baseline milestone list is invalid"
        return result
    baseline_matches = [row for row in baseline_rows if isinstance(row, dict) and row.get("id") == milestone.milestone_id]
    if len(baseline_matches) != 1:
        result["reason"] = "milestone ID does not resolve exactly once in the current canonical baseline"
        return result
    baseline_item = baseline_matches[0]
    if normalize_id(str(baseline_item.get("workstream_id") or "")) != update.workstream_id:
        result["reason"] = "milestone belongs to a different current baseline workstream"
        return result
    _, rows, _, _, _ = parse_roadmap_table(wdr["text"])
    matches = [row for row in rows if row.get("Milestone ID") == milestone.milestone_id]
    if len(matches) != 1:
        result["reason"] = "canonical WDR roadmap does not contain exactly one stable milestone row"
        return result
    row = matches[0]
    discrepancies: list[dict[str, Any]] = []
    requested = milestone_command_values(milestone, historical_revision)
    for field_name, expected in requested.items():
        if normalized_reconciliation_value(field_name, expected) != normalized_reconciliation_value(field_name, row.get(field_name, "")):
            discrepancies.append({"field": field_name, "requested": expected, "current": row.get(field_name, "")})
    row_baseline_revision = normalized_reconciliation_value("baseline_revision", row.get("Baseline Revision", ""))
    if row_baseline_revision != baseline_revision:
        discrepancies.append({
            "field": "current_baseline_revision",
            "requested": baseline_revision,
            "current": row_baseline_revision,
        })
    if not discrepancies:
        result.update({"satisfied": True, "satisfied_by": "current-fact"})
        return result
    lineage, missing_receipts = milestone_superseded_lineage(
        memory_root,
        update,
        milestone,
        row,
        historical_revision,
        baseline_revision,
        wdr,
    )
    if lineage:
        result.update({
            "satisfied": True,
            "satisfied_by": "superseded-lineage",
            "superseded_fields": sorted(str(item["field"]) for item in discrepancies),
            "lineage_evidence": lineage,
        })
        return result
    if missing_receipts:
        result["reason"] = "milestone correction lineage is referenced but lacks a durable correction receipt"
        result["missing_correction_receipts"] = missing_receipts
    else:
        result["reason"] = "current milestone facts do not satisfy the historical command"
    result["discrepancies"] = discrepancies
    return result


def reconcile_consumed_intent(
    update: StatusUpdate,
    intent_id: str,
    input_hash: str,
    outbox_rows: dict[str, dict[str, Any]],
    ordinal: int,
) -> dict[str, Any]:
    row = outbox_rows.get(intent_id)
    result = {
        "command_type": "status-intent-consumption",
        "command_index": ordinal,
        "workstream_id": update.workstream_id,
        "intent_id": intent_id,
        "satisfied": False,
    }
    if not row:
        result["reason"] = "canonical status intent outbox row is missing"
        return result
    expected_intent = update.consumed_intents.get(intent_id)
    result["satisfied"] = (
        row.get("intent") == expected_intent
        and row.get("state") == "consumed"
        and row.get("consumed_by") == input_hash
    )
    if not result["satisfied"]:
        result["reason"] = "canonical status intent lineage does not prove this intake consumed the intent"
    return result


def reconciliation_token_path(memory_root: Path, token: str) -> Path:
    return memory_root / INTAKE_RECONCILIATION_TOKEN_REL / f"{sha256_bytes(token.encode('utf-8')).removeprefix('sha256:')}.json"


def reconciliation_binding(snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    return {
        "input_path": snapshot["input_path"],
        "input_hash": snapshot["input_hash"],
        "principal": principal,
        "snapshot_id": snapshot["snapshot_id"],
        "read_set": snapshot["read_set"],
        "command_results_digest": content_id(snapshot["command_results"]),
    }


def issue_reconciliation_token(memory_root: Path, snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    issued = datetime.now(timezone.utc)
    token = f"reconcile_{secrets.token_urlsafe(32)}"
    binding = reconciliation_binding(snapshot, principal)
    state = {
        "schema_version": "1.0.0",
        "token_hash": sha256_bytes(token.encode("utf-8")),
        "principal": principal,
        "binding": binding,
        "binding_digest": content_id(binding),
        "status": "unused",
        "issued_at": issued.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "previous_state_id": None,
    }
    state["state_id"] = content_id(state)
    write_json_atomic(reconciliation_token_path(memory_root, token), state)
    return {"token": token, "token_state": state}


def reconciliation_snapshot(
    memory_root: Path,
    input_path: Path,
    input_hash: str,
    updates: list[StatusUpdate],
) -> dict[str, Any]:
    action_updates = [action for update in updates for action in update.actions]
    needs_ledger = bool(action_updates) or any(update.refresh_actions for update in updates)
    ledger_path = memory_root / ACTION_LEDGER_REL
    ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
    rows: list[dict[str, str]] = []
    ledger_state: dict[str, Any] = {}
    read_paths: set[Path] = set()
    if needs_ledger:
        if not ledger_path.is_file() or not ledger_state_path.is_file():
            raise StatusSyncContractError(
                "INTAKE_RECONCILIATION_LINEAGE_MISSING",
                "canonical action ledger lineage is required for action reconciliation",
            )
        ledger_state = load_existing_json_object(
            ledger_state_path,
            "INTAKE_RECONCILIATION_LINEAGE_INVALID",
            "action ledger state",
        )
        validate_action_ledger_state(ledger_path, ledger_state)
        rows = parse_action_ledger(ledger_path)
        read_paths.update({ledger_path, ledger_state_path})
    applied_commands = {
        str(item.get("command_id")): item
        for item in ledger_state.get("applied_commands", [])
        if isinstance(item, dict) and item.get("command_id")
    }
    needs_baseline = any(update.milestones for update in updates)
    baseline: dict[str, Any] = {}
    baseline_revision = 0
    if needs_baseline:
        baseline_path = memory_root / BASELINE_REL
        baseline = parse_program_baseline(baseline_path)
        baseline_revision = parse_optional_revision(baseline.get("revision"), "program baseline revision") or 0
        read_paths.add(baseline_path)
    outbox_rows: dict[str, dict[str, Any]] = {}
    if any(update.consumed_intent_ids for update in updates):
        outbox_path = memory_root / "state/status-intent-outbox.json"
        outbox = load_existing_json_object(
            outbox_path,
            "INTAKE_RECONCILIATION_LINEAGE_INVALID",
            "status intent outbox",
        )
        raw_rows = outbox.get("intents")
        if not isinstance(raw_rows, list):
            raise StatusSyncContractError("INTAKE_RECONCILIATION_LINEAGE_INVALID", "status intent outbox rows are invalid")
        validate_status_intent_outbox(outbox, raw_rows)
        outbox_rows = {str(row.get("intent_id")): row for row in raw_rows if isinstance(row, dict)}
        read_paths.add(outbox_path)
    wdr_cache: dict[str, dict[str, Any]] = {}
    lineage_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    command_results: list[dict[str, Any]] = []
    ordinal = 0
    for action in action_updates:
        ordinal += 1
        command_results.append(reconcile_action_command(memory_root, action, rows, applied_commands, ordinal))
    for update in updates:
        virtual_scope = scope_contract_module().is_virtual_cli_scope_id(update.workstream_id)
        virtual_wdr_command = bool(
            update.current_fields_present
            or update.next_actions
            or update.refresh_actions
            or update.milestones
        )
        if virtual_scope and virtual_wdr_command:
            raise StatusSyncContractError(
                "ADP-VIRTUAL-SCOPE-NOT-WDR-TARGET",
                "program is a virtual scope and cannot receive status fields, milestone updates, or action refresh",
            )
        needs_wdr = bool(
            update.current_fields_present
            or (update.next_actions_provided and not virtual_scope)
            or update.refresh_actions
            or update.milestones
        )
        wdr = None
        if needs_wdr:
            if update.workstream_id not in wdr_cache:
                wdr_cache[update.workstream_id] = load_reconciliation_wdr(memory_root, update.workstream_id)
            wdr = wdr_cache[update.workstream_id]
            read_paths.update({wdr["record_path"], wdr["state_path"]})
        for field_name in sorted(update.current_fields_present):
            ordinal += 1
            value = getattr(update, field_name)
            requested = "; ".join(value) if isinstance(value, list) else value
            command_results.append(
                reconcile_status_field(memory_root, update, field_name, requested, wdr, ordinal, lineage_cache)
            )
        if update.next_actions_provided and not (virtual_scope and not update.next_actions):
            ordinal += 1
            command_results.append(
                reconcile_status_field(
                    memory_root,
                    update,
                    "next_actions",
                    "; ".join(update.next_actions),
                    wdr,
                    ordinal,
                    lineage_cache,
                )
            )
        if update.refresh_actions:
            ordinal += 1
            command_results.append(reconcile_refresh_actions(update, rows, ledger_state, wdr, ordinal))
            read_paths.add(wdr["record_path"].with_name(ACTION_PROJECTION_REL))
        for milestone in update.milestones:
            ordinal += 1
            command_results.append(
                reconcile_milestone_command(memory_root, update, milestone, baseline, baseline_revision, wdr, ordinal)
            )
        for intent_id in update.consumed_intent_ids:
            ordinal += 1
            command_results.append(reconcile_consumed_intent(update, intent_id, input_hash, outbox_rows, ordinal))
    if not command_results:
        raise ValueError("updates-file contains no action, status, milestone, refresh, or intent-consumption commands")
    for result in command_results:
        for evidence in result.get("lineage_evidence", []):
            if not isinstance(evidence, dict):
                continue
            for relative in evidence.get("paths", []):
                candidate = memory_root / str(relative)
                if candidate.is_file():
                    read_paths.add(candidate)
    read_set = [
        {"path": path.relative_to(memory_root).as_posix(), "fingerprint": optional_sha256_file(path)}
        for path in sorted(read_paths)
    ]
    missing = [result for result in command_results if not result.get("satisfied")]
    body = {
        "input_path": str(input_path),
        "input_hash": input_hash,
        "update_count": len(updates),
        "command_results": command_results,
        "read_set": read_set,
    }
    return {
        **body,
        "snapshot_id": content_id(body),
        "verification_status": "verified" if not missing else "partial",
        "all_satisfied": not missing,
        "missing_commands": missing,
    }


def reconciliation_receipt_valid(receipt: dict[str, Any], input_path: Path, input_hash: str, update_count: int) -> bool:
    receipt_id = receipt.get("receipt_id")
    body = dict(receipt)
    body.pop("receipt_id", None)
    reconciliation = receipt.get("reconciliation")
    return bool(
        receipt.get("receipt_schema_version") == RECEIPT_SCHEMA_VERSION
        and receipt.get("receipt_type") == "reconciliation"
        and receipt.get("ok") is True
        and receipt.get("status") == "applied"
        and receipt.get("durable") is True
        and receipt.get("dry_run") is False
        and receipt.get("mode") == "update"
        and receipt.get("input_path") == str(input_path)
        and receipt.get("input_hash") == input_hash
        and receipt.get("update_count") == update_count
        and isinstance(receipt.get("applied_at"), str)
        and isinstance(reconciliation, dict)
        and reconciliation.get("verification_method") == "canonical-fact-reconciliation"
        and reconciliation.get("verification_status") == "verified"
        and reconciliation.get("all_satisfied") is True
        and reconciliation.get("missing_commands") == []
        and receipt_id == content_id(body)
    )


def existing_reconciliation_receipt(
    memory_root: Path,
    input_path: Path,
    input_hash: str,
    update_count: int,
) -> tuple[Path, dict[str, Any]] | None:
    root = memory_root / STATUS_SYNC_RECEIPT_REL
    if not root.is_dir():
        return None
    for path in sorted(root.glob("ssr-*.json")):
        receipt = load_json_object(path)
        if receipt.get("receipt_type") != "reconciliation" or receipt.get("input_hash") != input_hash:
            continue
        if reconciliation_receipt_valid(receipt, input_path, input_hash, update_count):
            return path, receipt
        raise StatusSyncContractError(
            "INTAKE_RECONCILIATION_RECEIPT_INVALID",
            f"durable reconciliation receipt is invalid: {path}",
        )
    return None


def build_reconciliation_receipt(snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    receipt = status_sync_receipt(
        receipt_type="reconciliation",
        input_path=Path(snapshot["input_path"]),
        input_hash=snapshot["input_hash"],
        update_count=snapshot["update_count"],
        applied_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        dry_run=False,
    )
    receipt["reconciliation"] = {
        "verification_method": "canonical-fact-reconciliation",
        "verification_status": "verified",
        "all_satisfied": True,
        "missing_commands": [],
        "snapshot_id": snapshot["snapshot_id"],
        "principal": principal,
        "read_set": snapshot["read_set"],
        "command_results": snapshot["command_results"],
    }
    receipt["receipt_id"] = content_id(receipt)
    return receipt


def consumed_reconciliation_token_state(
    token_state: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(token_state)
    updated["previous_state_id"] = token_state["state_id"]
    updated["status"] = "consumed"
    updated["consumed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated["receipt_path"] = str(receipt_path)
    updated["receipt_id"] = receipt["receipt_id"]
    updated.pop("state_id", None)
    updated["state_id"] = content_id(updated)
    return updated


def apply_reconciliation_receipt(
    memory_root: Path,
    token_path: Path,
    token_state: dict[str, Any],
    snapshot: dict[str, Any],
    principal: str,
    fail_after_stage: bool,
) -> tuple[Path, dict[str, Any], dict[str, Any], list[str]]:
    receipt = build_reconciliation_receipt(snapshot, principal)
    receipt_rel = receipt_relative_path(receipt)
    receipt_path = memory_root / receipt_rel
    token_rel = token_path.relative_to(memory_root)
    with tempfile.TemporaryDirectory(prefix=".intake-reconciliation-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        copy_memory_tree(memory_root, staged_root)
        write_json_atomic(staged_root / receipt_rel, receipt)
        write_json_atomic(
            staged_root / token_rel,
            consumed_reconciliation_token_state(token_state, receipt_path, receipt),
        )
        changed = changed_staged_files(memory_root, staged_root)
        allowed = {receipt_rel, token_rel}
        unexpected = [path.as_posix() for path in changed if path not in allowed]
        if unexpected:
            raise StatusSyncContractError(
                "INTAKE_RECONCILIATION_TARGET_INVALID",
                "reconciliation staged unexpected targets: " + ", ".join(unexpected),
            )
        if fail_after_stage:
            raise StatusSyncContractError(
                "INTAKE_RECONCILIATION_INJECTED_FAILURE",
                "injected failure after reconciliation staging",
            )
        publication = publish_staged_files(
            memory_root,
            staged_root,
            changed,
            transaction_kind="intake-reconciliation",
        )
    return receipt_path, receipt, publication, [path.as_posix() for path in changed]


def run_reconcile_intake(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    input_path = require_file(args.updates_file, "updates-file")
    input_hash = sha256_bytes(input_path.read_bytes())
    _, updates = load_updates_payload(input_path, args.source)
    principal = " ".join(args.principal.split())
    if not principal:
        raise StatusSyncContractError("INTAKE_RECONCILIATION_PRINCIPAL_INVALID", "principal must not be empty")
    existing = existing_reconciliation_receipt(memory_root, input_path, input_hash, len(updates))
    if existing:
        receipt_path, receipt = existing
        emit(
            {
                "ok": True,
                "mode": "reconcile-intake",
                "dry_run": bool(args.dry_run),
                "verification_status": "verified",
                "all_satisfied": True,
                "reused": True,
                "token": None,
                "missing_commands": [],
                "receipt": receipt,
                "receipt_path": str(receipt_path),
            },
            args.output,
        )
        return 0
    snapshot = reconciliation_snapshot(memory_root, input_path, input_hash, updates)
    if args.dry_run:
        if args.token:
            raise StatusSyncContractError(
                "INTAKE_RECONCILIATION_TOKEN_INVALID",
                "--token is not accepted with --dry-run",
            )
        token = None
        if snapshot["all_satisfied"]:
            memory_root.mkdir(parents=True, exist_ok=True)
            token = issue_reconciliation_token(memory_root, snapshot, principal)["token"]
        emit(
            {
                "ok": True,
                "mode": "reconcile-intake",
                "dry_run": True,
                "verification_status": snapshot["verification_status"],
                "all_satisfied": snapshot["all_satisfied"],
                "input_path": str(input_path),
                "input_hash": input_hash,
                "snapshot_id": snapshot["snapshot_id"],
                "command_results": snapshot["command_results"],
                "missing_commands": snapshot["missing_commands"],
                "token": token,
                "receipt": None,
                "receipt_path": None,
            },
            args.output,
        )
        return 0
    if not args.token:
        raise StatusSyncContractError(
            "INTAKE_RECONCILIATION_TOKEN_REQUIRED",
            "durable reconciliation requires the single-use token from a fully satisfied dry-run",
        )
    token_path = reconciliation_token_path(memory_root, args.token)
    token_state = load_json_object(token_path)
    if not token_state:
        raise StatusSyncContractError("INTAKE_RECONCILIATION_TOKEN_INVALID", "reconciliation token is unknown")
    claimed_state_id = token_state.get("state_id")
    state_body = dict(token_state)
    state_body.pop("state_id", None)
    if claimed_state_id != content_id(state_body) or token_state.get("token_hash") != sha256_bytes(args.token.encode("utf-8")):
        raise StatusSyncContractError("INTAKE_RECONCILIATION_TOKEN_INVALID", "reconciliation token identity is invalid")
    if token_state.get("principal") != principal:
        raise StatusSyncContractError("INTAKE_RECONCILIATION_TOKEN_INVALID", "reconciliation token belongs to another principal")
    if token_state.get("status") == "consumed":
        raise StatusSyncContractError("INTAKE_RECONCILIATION_TOKEN_USED", "reconciliation token was already consumed")
    if token_state.get("status") != "unused":
        raise StatusSyncContractError("INTAKE_RECONCILIATION_TOKEN_INVALID", "reconciliation token has an invalid state")
    expires_at = datetime.fromisoformat(str(token_state.get("expires_at", "")).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="INTAKE_RECONCILIATION_TOKEN_EXPIRED")
        raise StatusSyncContractError("INTAKE_RECONCILIATION_TOKEN_EXPIRED", "reconciliation token expired")
    if not snapshot["all_satisfied"]:
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="INTAKE_RECONCILIATION_FACTS_STALE")
        raise StatusSyncContractError(
            "INTAKE_RECONCILIATION_FACTS_STALE",
            "canonical facts no longer satisfy every historical command",
            {"missing_commands": snapshot["missing_commands"]},
        )
    binding = reconciliation_binding(snapshot, principal)
    if token_state.get("binding") != binding or token_state.get("binding_digest") != content_id(binding):
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="INTAKE_RECONCILIATION_READ_SET_STALE")
        raise StatusSyncContractError(
            "INTAKE_RECONCILIATION_READ_SET_STALE",
            "canonical facts or lineage changed after the reconciliation dry-run",
        )
    receipt_path, receipt, publication, changed = apply_reconciliation_receipt(
        memory_root,
        token_path,
        token_state,
        snapshot,
        principal,
        args.fail_after_stage,
    )
    emit(
        {
            "ok": True,
            "mode": "reconcile-intake",
            "dry_run": False,
            "verification_status": "verified",
            "all_satisfied": True,
            "reused": False,
            "missing_commands": [],
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "publication": publication,
            "changed_paths": changed,
        },
        args.output,
    )
    return 0


def intake_retirement_token_path(memory_root: Path, token: str) -> Path:
    digest = sha256_bytes(token.encode("utf-8")).removeprefix("sha256:")
    return memory_root / INTAKE_RETIREMENT_TOKEN_REL / f"{digest}.json"


def load_retirement_updates_payload(path: Path) -> tuple[Any, list[StatusUpdate], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_LEGACY_SCAN_BLOCKED",
            f"retirement cannot safely parse the historical intake JSON: {exc}",
            {
                "verification_status": "blocked",
                "evidence_scan": {
                    "verification_status": "blocked",
                    "error_code": "INTAKE_RETIREMENT_LEGACY_SCAN_BLOCKED",
                    "error": str(exc),
                    "satisfied_commands": [],
                    "missing_commands": [],
                    "read_set": [],
                },
            },
        ) from exc
    try:
        return payload, updates_from_payload(payload, "status sync"), "current-writer-schema"
    except (ValueError, TypeError) as modern_exc:
        try:
            updates = updates_from_payload(
                payload,
                "status sync",
                allow_legacy_terminal_without_id=True,
            )
        except (ValueError, TypeError) as legacy_exc:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_LEGACY_SCAN_BLOCKED",
                "retirement cannot safely normalize the legacy executable payload for evidence scanning",
                {
                    "verification_status": "blocked",
                    "evidence_scan": {
                        "verification_status": "blocked",
                        "error_code": "INTAKE_RETIREMENT_LEGACY_SCAN_BLOCKED",
                        "error": str(legacy_exc),
                        "legacy_parser_error": str(modern_exc),
                        "satisfied_commands": [],
                        "missing_commands": [],
                        "read_set": [],
                    },
                },
            ) from legacy_exc
        return payload, updates, "legacy-terminal-action-scan"


def resolved_touched_paths(memory_root: Path, raw_paths: Any) -> list[Path] | None:
    if not isinstance(raw_paths, list) or not raw_paths or not all(isinstance(item, str) for item in raw_paths):
        return None
    resolved = [resolve_receipt_input_path(memory_root, item) for item in raw_paths]
    return None if any(path is None for path in resolved) else [path for path in resolved if path is not None]


def meeting_sync_successor_binding(
    memory_root: Path,
    receipt_path: Path,
    input_path: Path,
) -> dict[str, Any]:
    receipt = load_json_object(receipt_path)
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    result_meeting = result.get("meeting") if isinstance(result.get("meeting"), dict) else {}
    touched = result.get("touched") if isinstance(result.get("touched"), dict) else {}
    meeting_instance_id = str(receipt.get("meeting_instance_id") or "").strip()
    plan_fingerprint = str(receipt.get("plan_fingerprint") or "").strip().lower()
    try:
        applied_at = normalize_required_timestamp(receipt.get("applied_at"), "meeting receipt applied_at")
    except ValueError as exc:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "meeting-sync successor receipt has an invalid applied_at timestamp",
        ) from exc
    if (
        receipt.get("schema_version") != 1
        or receipt.get("status") != "applied"
        or result.get("ok") is not True
        or result.get("dry_run") is not False
        or not meeting_instance_id
        or receipt_path.name != f"{meeting_instance_id}.json"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_fingerprint) is None
        or result_meeting.get("meeting_instance_id") != meeting_instance_id
        or str(result_meeting.get("plan_fingerprint") or "").strip().lower() != plan_fingerprint
    ):
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "meeting-sync successor must be an applied non-dry-run receipt with stable meeting and plan identity",
        )
    payload, updates, _ = load_retirement_updates_payload(input_path)
    payload_meeting = payload.get("meeting") if isinstance(payload, dict) and isinstance(payload.get("meeting"), dict) else {}
    if (
        not isinstance(payload, dict)
        or payload.get("generated_by") != "adp-meeting-sync"
        or payload_meeting.get("meeting_instance_id") != meeting_instance_id
        or str(payload_meeting.get("plan_fingerprint") or "").strip().lower() != plan_fingerprint
    ):
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "meeting-sync receipt is not bound to this generated status-sync intake identity",
        )
    intake_paths = resolved_touched_paths(memory_root, touched.get("status_sync_intake_files"))
    if intake_paths is None or len(intake_paths) != 1 or intake_paths[0].resolve() != input_path.resolve():
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "meeting-sync receipt must touch exactly the intake being retired",
        )
    daily_paths = resolved_touched_paths(memory_root, touched.get("daily_logs"))
    workstream_paths = resolved_touched_paths(memory_root, touched.get("workstream_records"))
    if daily_paths is None or workstream_paths is None:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "meeting-sync receipt lacks durable daily-log or WDR write lineage",
        )
    archive = resolve_receipt_input_path(memory_root, receipt.get("archive"))
    if archive is None:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "meeting-sync successor receipt is missing its durable meeting archive",
        )
    return {
        "binding_type": "meeting-sync-receipt",
        "path": receipt_path.relative_to(memory_root).as_posix(),
        "fingerprint": sha256_bytes(receipt_path.read_bytes()),
        "schema_version": 1,
        "meeting_instance_id": meeting_instance_id,
        "plan_fingerprint": plan_fingerprint,
        "applied_at": applied_at,
        "generated_intake": {
            "path": input_path.relative_to(memory_root).as_posix(),
            "fingerprint": sha256_bytes(input_path.read_bytes()),
            "payload_id": content_id(payload),
            "update_count": len(updates),
        },
        "write_lineage": {
            "archive": archive.relative_to(memory_root).as_posix(),
            "daily_logs": sorted(path.relative_to(memory_root).as_posix() for path in daily_paths),
            "workstream_records": sorted(path.relative_to(memory_root).as_posix() for path in workstream_paths),
        },
    }


def meeting_successor_lineage_binding(scan: dict[str, Any]) -> dict[str, Any]:
    satisfied = scan.get("satisfied_commands") if isinstance(scan.get("satisfied_commands"), list) else []
    return {
        "verification_status": scan.get("verification_status"),
        "snapshot_id": scan.get("snapshot_id"),
        "command_count": len(satisfied),
        "command_results_digest": content_id(satisfied),
        "read_set": scan.get("read_set", []),
    }


def meeting_successor_lineage_valid(binding: Any, scan: dict[str, Any]) -> bool:
    if not isinstance(binding, dict) or binding.get("binding_type") != "meeting-sync-receipt":
        return True
    return bool(
        scan.get("verification_status") == "verified"
        and scan.get("missing_commands") == []
        and isinstance(scan.get("satisfied_commands"), list)
        and bool(scan.get("satisfied_commands"))
        and binding.get("lineage_verification") == meeting_successor_lineage_binding(scan)
    )


def retirement_successor_binding(
    project_root: Path,
    memory_root: Path,
    input_path: Path,
    raw_target: str | None,
) -> dict[str, Any] | None:
    if not raw_target:
        return None
    target = Path(raw_target).expanduser()
    if not target.is_absolute():
        project_candidate = (project_root / target).resolve()
        memory_candidate = (memory_root / target).resolve()
        target = project_candidate if project_candidate.is_file() else memory_candidate
    target = target.resolve()
    if not target.is_file():
        raise StatusSyncContractError("INTAKE_RETIREMENT_SUCCESSOR_INVALID", f"superseded-by target not found: {target}")
    try:
        relative = target.relative_to(memory_root)
    except ValueError as exc:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "superseded-by target must be inside ADP memory",
        ) from exc
    if target == input_path:
        raise StatusSyncContractError("INTAKE_RETIREMENT_SUCCESSOR_INVALID", "an intake cannot supersede itself")
    fingerprint = sha256_bytes(target.read_bytes())
    if relative.parts[:2] == ("intake", "status-sync"):
        try:
            payload, updates = load_updates_payload(target, "status sync")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
                "superseded-by intake must be a valid status-sync intake",
            ) from exc
        receipts = durable_receipts_for_input(memory_root, target)
        binding: dict[str, Any] = {
            "binding_type": "intake",
            "path": relative.as_posix(),
            "fingerprint": fingerprint,
            "update_count": len(updates),
            "payload_id": content_id(payload),
        }
        if receipts:
            latest = receipts[-1]
            binding["durable_receipt"] = {
                "path": latest["receipt_path"].relative_to(memory_root).as_posix(),
                "fingerprint": sha256_bytes(latest["receipt_path"].read_bytes()),
                "receipt_type": latest["receipt"].get("receipt_type"),
                "applied_at": latest["applied_at"],
            }
        return binding
    if relative.parts[:2] == ("receipts", "status-sync"):
        record = durable_status_receipt_record(memory_root, target)
        if record is None:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
                "superseded-by receipt is not a valid durable status-sync receipt",
            )
        return {
            "binding_type": "durable-receipt",
            "path": relative.as_posix(),
            "fingerprint": fingerprint,
            "receipt_type": record["receipt"].get("receipt_type"),
            "input_path": record["input_path"].relative_to(memory_root).as_posix()
            if record["input_path"].is_relative_to(memory_root)
            else str(record["input_path"]),
            "input_hash": record["input_hash"],
            "applied_at": record["applied_at"],
        }
    if relative.parts[:2] == MEETING_SYNC_RECEIPT_REL.parts:
        return meeting_sync_successor_binding(memory_root, target, input_path)
    raise StatusSyncContractError(
        "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
        "superseded-by must reference a status-sync intake, durable status-sync receipt, or strictly bound meeting-sync receipt",
    )


def retirement_successor_binding_valid(memory_root: Path, binding: Any, input_path: Path) -> bool:
    if not isinstance(binding, dict):
        return False
    raw_path = str(binding.get("path") or "")
    fingerprint = str(binding.get("fingerprint") or "").strip().lower()
    path = memory_root / raw_path
    try:
        relative = path.resolve().relative_to(memory_root.resolve())
    except ValueError:
        return False
    if (
        not raw_path
        or path.resolve() == input_path.resolve()
        or not path.is_file()
        or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None
        or sha256_bytes(path.read_bytes()) != fingerprint
    ):
        return False
    if binding.get("binding_type") == "intake":
        if relative.parts[:2] != ("intake", "status-sync"):
            return False
        try:
            payload, updates = load_updates_payload(path, "status sync")
        except (ValueError, TypeError, json.JSONDecodeError):
            return False
        if binding.get("update_count") != len(updates) or binding.get("payload_id") != content_id(payload):
            return False
        durable = binding.get("durable_receipt")
        if durable is None:
            return True
        if not isinstance(durable, dict):
            return False
        receipt_path = memory_root / str(durable.get("path") or "")
        record = durable_status_receipt_record(memory_root, receipt_path)
        return bool(
            record is not None
            and sha256_bytes(receipt_path.read_bytes()) == durable.get("fingerprint")
            and record["input_path"].resolve() == path.resolve()
        )
    if binding.get("binding_type") == "durable-receipt":
        if relative.parts[:2] != ("receipts", "status-sync"):
            return False
        record = durable_status_receipt_record(memory_root, path)
        return bool(
            record is not None
            and record["input_hash"] == binding.get("input_hash")
            and record["input_path"].resolve() != input_path.resolve()
        )
    if binding.get("binding_type") == "meeting-sync-receipt":
        if relative.parts[:2] != MEETING_SYNC_RECEIPT_REL.parts:
            return False
        try:
            expected = meeting_sync_successor_binding(memory_root, path, input_path)
        except StatusSyncContractError:
            return False
        lineage = binding.get("lineage_verification")
        return bool(
            isinstance(lineage, dict)
            and all(binding.get(key) == value for key, value in expected.items())
        )
    return False


def retirement_execution_scan(
    memory_root: Path,
    input_path: Path,
    input_hash: str,
    updates: list[StatusUpdate],
) -> dict[str, Any]:
    try:
        reconciliation = reconciliation_snapshot(memory_root, input_path, input_hash, updates)
    except (StatusSyncContractError, ValueError, OSError) as exc:
        return {
            "verification_status": "blocked",
            "error_code": exc.error_code if isinstance(exc, StatusSyncContractError) else "INTAKE_RETIREMENT_EVIDENCE_UNAVAILABLE",
            "error": str(exc),
            "satisfied_commands": [],
            "read_set": [],
        }
    satisfied = [result for result in reconciliation["command_results"] if result.get("satisfied")]
    return {
        "verification_status": reconciliation["verification_status"],
        "snapshot_id": reconciliation["snapshot_id"],
        "satisfied_commands": satisfied,
        "missing_commands": reconciliation["missing_commands"],
        "read_set": reconciliation["read_set"],
    }


def meeting_successor_execution_scan(
    memory_root: Path,
    updates: list[StatusUpdate],
    scan: dict[str, Any],
    successor: dict[str, Any],
) -> dict[str, Any]:
    if scan.get("verification_status") == "blocked":
        return scan
    missing = list(scan.get("missing_commands", []))
    missing_by_index = {
        item.get("command_index"): item
        for item in missing
        if isinstance(item, dict) and item.get("command_type") == "action"
    }
    if not missing_by_index:
        return scan
    try:
        rows = parse_action_ledger(memory_root / ACTION_LEDGER_REL)
    except (OSError, ValueError, TypeError):
        return scan
    receipt_daily_logs = set(successor.get("write_lineage", {}).get("daily_logs", []))
    upgraded: list[dict[str, Any]] = []
    action_ordinal = 0
    for update in updates:
        for action in update.actions:
            action_ordinal += 1
            missing_result = missing_by_index.get(action_ordinal)
            if missing_result is None or not action.action_id:
                continue
            candidates = [row for row in rows if row.get("Action ID") == action.action_id]
            if len(candidates) != 1:
                continue
            row = candidates[0]
            if (
                normalized_reconciliation_value("action", action.action)
                != normalized_reconciliation_value("action", row.get("Action", ""))
                or normalized_reconciliation_value("owner", action.owner)
                != normalized_reconciliation_value("owner", row.get("Owner", ""))
                or normalized_reconciliation_value("workstream", action.workstream)
                != normalized_reconciliation_value("workstream", row.get("Workstream", ""))
                or normalized_reconciliation_value("closure_criteria", action.closure_criteria)
                != normalized_reconciliation_value("closure_criteria", row.get("Closure Criteria", ""))
            ):
                continue
            if (
                "affected_workstreams" in action.declared_fields
                and sorted(action.affected_workstreams or [])
                != sorted(parse_workstream_cell(row.get("Affected Workstreams", "")))
            ):
                continue
            daily = action_daily_log_lineage(memory_root, action, action.action_id)
            daily_paths = set(daily.get("paths", [])) if isinstance(daily, dict) else set()
            if daily is None or not daily_paths or not daily_paths.issubset(receipt_daily_logs):
                continue
            upgraded.append(
                {
                    **missing_result,
                    "satisfied": True,
                    "satisfied_by": "meeting-sync-receipt-lineage",
                    "reason": None,
                    "discrepancies": missing_result.get("discrepancies", []),
                    "lineage_evidence": [
                        {
                            "type": "applied-meeting-sync-receipt",
                            "path": successor.get("path"),
                            "meeting_instance_id": successor.get("meeting_instance_id"),
                            "plan_fingerprint": successor.get("plan_fingerprint"),
                            "generated_intake": successor.get("generated_intake"),
                        },
                        daily,
                    ],
                }
            )
    if not upgraded:
        return scan
    upgraded_indexes = {item.get("command_index") for item in upgraded}
    remaining = [item for item in missing if item.get("command_index") not in upgraded_indexes]
    satisfied = sorted(
        [*scan.get("satisfied_commands", []), *upgraded],
        key=lambda item: int(item.get("command_index") or 0),
    )
    read_set = list(scan.get("read_set", []))
    known_paths = {str(item.get("path")) for item in read_set if isinstance(item, dict)}
    for item in upgraded:
        for evidence in item.get("lineage_evidence", []):
            for relative in evidence.get("paths", []) if isinstance(evidence, dict) else []:
                path = memory_root / str(relative)
                if path.is_file() and str(relative) not in known_paths:
                    read_set.append({"path": str(relative), "fingerprint": sha256_bytes(path.read_bytes())})
                    known_paths.add(str(relative))
    body = {
        "base_snapshot_id": scan.get("snapshot_id"),
        "successor_fingerprint": successor.get("fingerprint"),
        "satisfied_commands": satisfied,
        "missing_commands": remaining,
        "read_set": read_set,
    }
    return {
        "verification_status": "verified" if not remaining else "partial",
        "snapshot_id": content_id(body),
        "base_snapshot_id": scan.get("snapshot_id"),
        "satisfied_commands": satisfied,
        "missing_commands": remaining,
        "read_set": read_set,
    }


def intake_retirement_snapshot(
    project_root: Path,
    memory_root: Path,
    input_path: Path,
    reason: str,
    superseded_by: str | None,
    justification: str | None,
) -> dict[str, Any]:
    if reason not in INTAKE_RETIREMENT_REASONS:
        raise StatusSyncContractError("INTAKE_RETIREMENT_REASON_INVALID", f"unsupported retirement reason: {reason}")
    try:
        intake_relative = input_path.resolve().relative_to((memory_root / "intake/status-sync").resolve())
    except ValueError as exc:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_TARGET_INVALID",
            "updates-file must be a JSON file under memory/intake/status-sync",
        ) from exc
    if len(intake_relative.parts) != 1 or input_path.suffix.lower() != ".json":
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_TARGET_INVALID",
            "updates-file must be one direct JSON intake under memory/intake/status-sync",
        )
    payload, updates, parser_mode = load_retirement_updates_payload(input_path)
    if not isinstance(payload, dict):
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_TARGET_INVALID",
            "historical intake root must be a JSON object",
        )
    input_hash = sha256_bytes(input_path.read_bytes())
    if durable_receipts_for_input(memory_root, input_path):
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_ALREADY_EXECUTED",
            "a durable successful receipt already binds this intake; execution and retirement are distinct semantics",
        )
    if reason == "superseded-by":
        if not superseded_by:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_SUCCESSOR_REQUIRED",
                "reason superseded-by requires --superseded-by",
            )
    elif superseded_by:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
            "--superseded-by is accepted only with reason superseded-by",
        )
    rationale = str(justification or "").strip()
    if reason in {"never-applied", "invalid-proposal"} and not rationale:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_JUSTIFICATION_REQUIRED",
            f"reason {reason} requires an explicit governance --justification",
        )
    successor = retirement_successor_binding(project_root, memory_root, input_path, superseded_by)
    scan = retirement_execution_scan(memory_root, input_path, input_hash, updates)
    scan["payload_parser"] = parser_mode
    if successor and successor.get("binding_type") == "meeting-sync-receipt":
        scan = meeting_successor_execution_scan(memory_root, updates, scan, successor)
        scan["payload_parser"] = parser_mode
        if (
            scan.get("verification_status") != "verified"
            or scan.get("missing_commands") != []
            or not scan.get("satisfied_commands")
        ):
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_SUCCESSOR_INVALID",
                "meeting-sync successor is bound to the intake but canonical action/fact lineage is not fully verified",
                {"evidence_scan": scan},
            )
        successor["lineage_verification"] = meeting_successor_lineage_binding(scan)
    if reason in {"never-applied", "invalid-proposal"}:
        if scan["verification_status"] == "blocked":
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_EVIDENCE_UNAVAILABLE",
                "retirement cannot exclude possible execution because canonical evidence could not be evaluated",
                {"evidence_scan": scan},
            )
        if scan["satisfied_commands"]:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_EXECUTION_POSSIBLE",
                "retirement cannot hide commands already supported by canonical facts or lineage",
                {"satisfied_commands": scan["satisfied_commands"]},
            )
    read_paths = {input_path}
    if successor:
        read_paths.add(memory_root / successor["path"])
        durable = successor.get("durable_receipt") if isinstance(successor.get("durable_receipt"), dict) else None
        if durable:
            read_paths.add(memory_root / durable["path"])
    for item in scan.get("read_set", []):
        if isinstance(item, dict) and item.get("path"):
            candidate = memory_root / str(item["path"])
            if candidate.is_file():
                read_paths.add(candidate)
    read_set = [
        {
            "path": path.relative_to(memory_root).as_posix() if path.is_relative_to(memory_root) else str(path),
            "fingerprint": sha256_bytes(path.read_bytes()),
        }
        for path in sorted(read_paths)
    ]
    body = {
        "input_path": str(input_path),
        "input_hash": input_hash,
        "update_count": len(updates),
        "reason": reason,
        "justification": rationale or None,
        "superseded_by": successor,
        "evidence_scan": scan,
        "read_set": read_set,
    }
    return {**body, "snapshot_id": content_id(body), "payload_id": content_id(payload)}


def intake_retirement_binding(snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    return {
        "input_path": snapshot["input_path"],
        "input_hash": snapshot["input_hash"],
        "principal": principal,
        "reason": snapshot["reason"],
        "snapshot_id": snapshot["snapshot_id"],
        "read_set": snapshot["read_set"],
    }


def issue_intake_retirement_token(memory_root: Path, snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    issued = datetime.now(timezone.utc)
    token = f"retire_{secrets.token_urlsafe(32)}"
    binding = intake_retirement_binding(snapshot, principal)
    state = {
        "schema_version": "1.0.0",
        "token_hash": sha256_bytes(token.encode("utf-8")),
        "principal": principal,
        "binding": binding,
        "binding_digest": content_id(binding),
        "status": "unused",
        "issued_at": issued.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "previous_state_id": None,
    }
    state["state_id"] = content_id(state)
    write_json_atomic(intake_retirement_token_path(memory_root, token), state)
    return {"token": token, "token_state": state}


def build_intake_retirement_receipt(snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    receipt = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_type": "intake-retirement",
        "ok": True,
        "status": "retired",
        "durable": True,
        "dry_run": False,
        "mode": "retire-intake",
        "input_path": snapshot["input_path"],
        "input_hash": snapshot["input_hash"],
        "update_count": snapshot["update_count"],
        "retired_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "reason": snapshot["reason"],
        "principal": principal,
        "governance": {
            "authority_principal": principal,
            "justification": snapshot["justification"],
        },
        "superseded_by": snapshot["superseded_by"],
        "payload_id": snapshot["payload_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "read_set": snapshot["read_set"],
        "evidence_scan": snapshot["evidence_scan"],
    }
    receipt["retirement_id"] = content_id(receipt)
    return receipt


def intake_retirement_receipt_path(memory_root: Path, receipt: dict[str, Any]) -> Path:
    digest = str(receipt["retirement_id"]).removeprefix("sha256:")
    return memory_root / INTAKE_RETIREMENT_RECEIPT_REL / f"irr-{digest[:32]}.json"


def intake_retirement_receipt_valid(
    memory_root: Path,
    receipt: dict[str, Any],
    input_path: Path,
    input_hash: str,
    update_count: int,
) -> bool:
    body = dict(receipt)
    retirement_id = body.pop("retirement_id", None)
    governance = receipt.get("governance") if isinstance(receipt.get("governance"), dict) else {}
    evidence_scan = receipt.get("evidence_scan") if isinstance(receipt.get("evidence_scan"), dict) else {}
    reason = receipt.get("reason")
    try:
        normalize_required_timestamp(receipt.get("retired_at"), "retirement receipt retired_at")
        payload, _, _ = load_retirement_updates_payload(input_path)
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    read_set = receipt.get("read_set")
    read_set_valid = bool(
        isinstance(read_set, list)
        and any(
            isinstance(item, dict)
            and item.get("fingerprint") == input_hash
            and (memory_root / str(item.get("path") or "")).resolve() == input_path.resolve()
            for item in read_set
        )
    )
    snapshot_body = {
        "input_path": receipt.get("input_path"),
        "input_hash": receipt.get("input_hash"),
        "update_count": receipt.get("update_count"),
        "reason": reason,
        "justification": governance.get("justification"),
        "superseded_by": receipt.get("superseded_by"),
        "evidence_scan": evidence_scan,
        "read_set": read_set,
    }
    semantics_valid = bool(
        governance.get("authority_principal") == receipt.get("principal")
        and (
            reason == "superseded-by"
            and retirement_successor_binding_valid(memory_root, receipt.get("superseded_by"), input_path)
            and meeting_successor_lineage_valid(receipt.get("superseded_by"), evidence_scan)
            or reason in {"never-applied", "invalid-proposal"}
            and bool(str(governance.get("justification") or "").strip())
            and receipt.get("superseded_by") is None
            and evidence_scan.get("verification_status") != "blocked"
            and evidence_scan.get("satisfied_commands") == []
        )
    )
    return bool(
        semantics_valid
        and read_set_valid
        and receipt.get("payload_id") == content_id(payload)
        and receipt.get("snapshot_id") == content_id(snapshot_body)
        and receipt.get("receipt_schema_version") == RECEIPT_SCHEMA_VERSION
        and receipt.get("receipt_type") == "intake-retirement"
        and receipt.get("ok") is True
        and receipt.get("status") == "retired"
        and receipt.get("durable") is True
        and receipt.get("dry_run") is False
        and receipt.get("mode") == "retire-intake"
        and receipt.get("input_path") == str(input_path)
        and receipt.get("input_hash") == input_hash
        and receipt.get("update_count") == update_count
        and receipt.get("reason") in INTAKE_RETIREMENT_REASONS
        and isinstance(receipt.get("principal"), str)
        and bool(receipt.get("principal"))
        and isinstance(receipt.get("retired_at"), str)
        and retirement_id == content_id(body)
    )


def existing_intake_retirement_receipt(
    memory_root: Path,
    input_path: Path,
    input_hash: str,
    update_count: int,
) -> tuple[Path, dict[str, Any]] | None:
    for path in sorted((memory_root / INTAKE_RETIREMENT_RECEIPT_REL).glob("irr-*.json")):
        receipt = load_json_object(path)
        if receipt.get("input_hash") != input_hash:
            continue
        if intake_retirement_receipt_valid(memory_root, receipt, input_path, input_hash, update_count):
            return path, receipt
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_RECEIPT_INVALID",
            f"durable intake retirement receipt is invalid: {path}",
        )
    return None


def consumed_intake_retirement_token_state(
    token_state: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(token_state)
    updated["previous_state_id"] = token_state["state_id"]
    updated["status"] = "consumed"
    updated["consumed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated["receipt_path"] = str(receipt_path)
    updated["retirement_id"] = receipt["retirement_id"]
    updated.pop("state_id", None)
    updated["state_id"] = content_id(updated)
    return updated


def apply_intake_retirement_receipt(
    memory_root: Path,
    token_path: Path,
    token_state: dict[str, Any],
    snapshot: dict[str, Any],
    principal: str,
    fail_after_stage: bool,
) -> tuple[Path, dict[str, Any], dict[str, Any], list[str]]:
    receipt = build_intake_retirement_receipt(snapshot, principal)
    receipt_path = intake_retirement_receipt_path(memory_root, receipt)
    receipt_rel = receipt_path.relative_to(memory_root)
    token_rel = token_path.relative_to(memory_root)
    with tempfile.TemporaryDirectory(prefix=".intake-retirement-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        copy_memory_tree(memory_root, staged_root)
        write_json_atomic(staged_root / receipt_rel, receipt)
        write_json_atomic(
            staged_root / token_rel,
            consumed_intake_retirement_token_state(token_state, receipt_path, receipt),
        )
        changed = changed_staged_files(memory_root, staged_root)
        allowed = {receipt_rel, token_rel}
        unexpected = [path.as_posix() for path in changed if path not in allowed]
        if unexpected:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_TARGET_INVALID",
                "retirement staged unexpected targets: " + ", ".join(unexpected),
            )
        if fail_after_stage:
            raise StatusSyncContractError(
                "INTAKE_RETIREMENT_INJECTED_FAILURE",
                "injected failure after retirement staging",
            )
        publication = publish_staged_files(
            memory_root,
            staged_root,
            changed,
            transaction_kind="intake-retirement",
        )
    return receipt_path, receipt, publication, [path.as_posix() for path in changed]


def run_retire_intake(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    input_path = require_file(args.updates_file, "updates-file")
    input_hash = sha256_bytes(input_path.read_bytes())
    _, updates, _ = load_retirement_updates_payload(input_path)
    existing = existing_intake_retirement_receipt(memory_root, input_path, input_hash, len(updates))
    if existing:
        receipt_path, receipt = existing
        emit({
            "ok": True,
            "mode": "retire-intake",
            "dry_run": False,
            "status": "already-retired",
            "reused": True,
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "changed_paths": [],
        }, args.output)
        return 0
    principal = str(args.principal or "").strip()
    snapshot = intake_retirement_snapshot(
        project_root,
        memory_root,
        input_path,
        args.reason,
        args.superseded_by,
        args.justification,
    )
    if args.dry_run:
        if args.token:
            raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_INVALID", "--token is not accepted with --dry-run")
        token = issue_intake_retirement_token(memory_root, snapshot, principal)["token"]
        emit({
            "ok": True,
            "mode": "retire-intake",
            "dry_run": True,
            "verification_status": "verified",
            "input_path": str(input_path),
            "input_hash": input_hash,
            "snapshot_id": snapshot["snapshot_id"],
            "reason": snapshot["reason"],
            "superseded_by": snapshot["superseded_by"],
            "evidence_scan": snapshot["evidence_scan"],
            "token": token,
            "receipt": None,
            "receipt_path": None,
        }, args.output)
        return 0
    if not args.token:
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_TOKEN_REQUIRED",
            "durable retirement requires the single-use token from a verified dry-run",
        )
    token_path = intake_retirement_token_path(memory_root, args.token)
    token_state = load_json_object(token_path)
    if not token_state:
        raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_INVALID", "retirement token is unknown")
    claimed_state_id = token_state.get("state_id")
    state_body = dict(token_state)
    state_body.pop("state_id", None)
    if claimed_state_id != content_id(state_body) or token_state.get("token_hash") != sha256_bytes(args.token.encode("utf-8")):
        raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_INVALID", "retirement token identity is invalid")
    if token_state.get("principal") != principal:
        raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_INVALID", "retirement token belongs to another principal")
    if token_state.get("status") == "consumed":
        raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_USED", "retirement token was already consumed")
    if token_state.get("status") != "unused":
        raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_INVALID", "retirement token has an invalid state")
    expires_at = datetime.fromisoformat(str(token_state.get("expires_at", "")).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="INTAKE_RETIREMENT_TOKEN_EXPIRED")
        raise StatusSyncContractError("INTAKE_RETIREMENT_TOKEN_EXPIRED", "retirement token expired")
    binding = intake_retirement_binding(snapshot, principal)
    if token_state.get("binding") != binding or token_state.get("binding_digest") != content_id(binding):
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="INTAKE_RETIREMENT_READ_SET_STALE")
        raise StatusSyncContractError(
            "INTAKE_RETIREMENT_READ_SET_STALE",
            "intake, successor, or canonical evidence changed after retirement dry-run",
        )
    receipt_path, receipt, publication, changed = apply_intake_retirement_receipt(
        memory_root,
        token_path,
        token_state,
        snapshot,
        principal,
        args.fail_after_stage,
    )
    emit({
        "ok": True,
        "mode": "retire-intake",
        "dry_run": False,
        "status": "retired",
        "reused": False,
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "publication": publication,
        "changed_paths": changed,
    }, args.output)
    return 0


def canonical_wdr_field_spec(section_title: str, label: str) -> tuple[str, str]:
    matches = {
        (section.casefold(), field_label.casefold()): (section, field_label)
        for section, field_label in VOLATILE_FIELDS.values()
    }
    resolved = matches.get((section_title.strip().casefold(), label.strip().casefold()))
    if not resolved:
        raise StatusSyncContractError(
            "WDR_FIELD_REPAIR_TARGET_INVALID",
            f"{section_title}.{label} is not a supported canonical WDR field",
        )
    return resolved


def canonical_field_occurrences(markdown: str, section_title: str, label: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    start, end = find_section(lines, section_title)
    if start is None:
        return []
    pattern = re.compile(rf"^\s*-\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE)
    return [
        {"index": index, "line": index + 1, "value": pattern.match(lines[index]).group(1).strip()}
        for index in range(start + 1, end)
        if pattern.match(lines[index])
    ]


def repaired_canonical_field(markdown: str, section_title: str, label: str, value: str) -> str:
    lines = markdown.splitlines()
    occurrences = canonical_field_occurrences(markdown, section_title, label)
    if len(occurrences) < 2:
        raise StatusSyncContractError("WDR_FIELD_REPAIR_NOT_NEEDED", "canonical field is not duplicated")
    first = occurrences[0]["index"]
    for occurrence in reversed(occurrences):
        del lines[occurrence["index"]]
    lines.insert(first, f"- {label}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def reviewed_canonical_value(raw_path: str | None) -> tuple[Path | None, str | None, str | None]:
    if not raw_path:
        return None, None, None
    path = require_file(raw_path, "canonical-value-file")
    content = path.read_text(encoding="utf-8-sig").strip()
    if not content or "\n" in content or "\r" in content:
        raise StatusSyncContractError(
            "WDR_FIELD_REPAIR_VALUE_INVALID",
            "canonical-value-file must contain exactly one non-empty line",
        )
    return path, content, sha256_bytes(path.read_bytes())


def wdr_field_repair_token_path(memory_root: Path, token: str) -> Path:
    digest = sha256_bytes(token.encode("utf-8")).removeprefix("sha256:")
    return memory_root / WDR_FIELD_REPAIR_TOKEN_REL / f"{digest}.json"


def wdr_field_repair_snapshot(
    memory_root: Path,
    workstream_id: str,
    section_title: str,
    label: str,
    value_file: str | None,
) -> dict[str, Any]:
    workstream_id = normalize_id(workstream_id)
    if scope_contract_module().is_virtual_cli_scope_id(workstream_id):
        raise StatusSyncContractError("WDR_FIELD_REPAIR_TARGET_INVALID", "virtual program has no WDR")
    section_title, label = canonical_wdr_field_spec(section_title, label)
    wdr = load_reconciliation_wdr(memory_root, workstream_id)
    occurrences = canonical_field_occurrences(wdr["text"], section_title, label)
    if len(occurrences) < 2:
        raise StatusSyncContractError("WDR_FIELD_REPAIR_NOT_NEEDED", "canonical field is not duplicated")
    value_path, reviewed_value, value_hash = reviewed_canonical_value(value_file)
    distinct = sorted({item["value"] for item in occurrences})
    if len(distinct) == 1:
        canonical_value = reviewed_value if reviewed_value is not None else distinct[0]
    elif reviewed_value is not None:
        canonical_value = reviewed_value
    else:
        return {
            "verification_status": "blocked",
            "can_apply": False,
            "workstream_id": workstream_id,
            "section": section_title,
            "field": label,
            "occurrences": [{"line": item["line"], "value": item["value"]} for item in occurrences],
            "reason": "conflicting duplicate values require --canonical-value-file",
        }
    ledger_path = memory_root / ACTION_LEDGER_REL
    ledger_state_path = memory_root / ACTION_LEDGER_STATE_REL
    if not ledger_path.is_file() or not ledger_state_path.is_file():
        raise StatusSyncContractError("WDR_FIELD_REPAIR_LINEAGE_MISSING", "action ledger lineage is required")
    ledger_state = load_existing_json_object(ledger_state_path, "WDR_FIELD_REPAIR_LINEAGE_INVALID", "action ledger state")
    validate_action_ledger_state(ledger_path, ledger_state)
    desired = repaired_canonical_field(wdr["text"], section_title, label, canonical_value)
    read_set = {
        "wdr_fingerprint": sha256_bytes(wdr["record_path"].read_bytes()),
        "wdr_state_fingerprint": sha256_bytes(wdr["state_path"].read_bytes()),
        "ledger_fingerprint": sha256_bytes(ledger_path.read_bytes()),
        "ledger_state_fingerprint": sha256_bytes(ledger_state_path.read_bytes()),
        "projection_fingerprint": optional_sha256_file(wdr["record_path"].with_name(ACTION_PROJECTION_REL)),
        "value_path": str(value_path) if value_path else None,
        "value_hash": value_hash,
    }
    body = {
        "workstream_id": workstream_id, "section": section_title, "field": label,
        "canonical_value": canonical_value, "read_set": read_set,
        "desired_wdr_fingerprint": sha256_bytes(desired.encode("utf-8")),
    }
    return {
        **body, "snapshot_id": content_id(body), "verification_status": "verified", "can_apply": True,
        "occurrences": [{"line": item["line"], "value": item["value"]} for item in occurrences],
        "desired_wdr": desired, "wdr": wdr, "ledger_state": ledger_state,
        "rows": parse_action_ledger(ledger_path),
    }


def wdr_field_repair_binding(snapshot: dict[str, Any], principal: str) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "principal": principal,
        "workstream_id": snapshot["workstream_id"],
        "section": snapshot["section"],
        "field": snapshot["field"],
        "canonical_value": snapshot["canonical_value"],
        "read_set": snapshot["read_set"],
    }


def issue_wdr_field_repair_token(memory_root: Path, snapshot: dict[str, Any], principal: str) -> str:
    issued = datetime.now(timezone.utc)
    token = f"wdrfield_{secrets.token_urlsafe(32)}"
    binding = wdr_field_repair_binding(snapshot, principal)
    state = {
        "schema_version": "1.0.0", "token_hash": sha256_bytes(token.encode("utf-8")),
        "principal": principal, "binding": binding, "binding_digest": content_id(binding),
        "status": "unused", "issued_at": issued.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "previous_state_id": None,
    }
    state["state_id"] = content_id(state)
    write_json_atomic(wdr_field_repair_token_path(memory_root, token), state)
    return token


def apply_wdr_field_repair(
    memory_root: Path,
    snapshot: dict[str, Any],
    principal: str,
    token_path: Path,
    token_state: dict[str, Any],
    fail_after_stage: bool,
) -> tuple[dict[str, Any], Path, dict[str, Any], list[str]]:
    receipt_id_seed = content_id({
        "snapshot_id": snapshot["snapshot_id"], "principal": principal,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    })
    receipt_rel = WDR_FIELD_REPAIR_RECEIPT_REL / f"{receipt_id_seed.removeprefix('sha256:')}.json"
    token_rel = token_path.relative_to(memory_root)
    record_rel = Path("workstreams") / snapshot["workstream_id"] / "delivery-record.md"
    state_rel = record_rel.with_name("delivery-record.state.json")
    projection_rel = record_rel.with_name(ACTION_PROJECTION_REL)
    with tempfile.TemporaryDirectory(prefix=".wdr-field-repair-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        copy_memory_tree(memory_root, staged_root)
        staged_record = staged_root / record_rel
        before = staged_record.read_bytes()
        staged_record.write_text(snapshot["desired_wdr"], encoding="utf-8", newline="\n")
        wdr_state = update_wdr_state(staged_record, before, snapshot["desired_wdr"].encode("utf-8"))
        write_action_projection_sidecar(
            staged_root, snapshot["workstream_id"], snapshot["rows"], snapshot["ledger_state"], wdr_state=wdr_state
        )
        receipt = {
            "schema_version": "1.0.0", "receipt_type": "wdr-field-repair",
            "outcome": "committed", "principal": principal, "snapshot_id": snapshot["snapshot_id"],
            "workstream_id": snapshot["workstream_id"], "section": snapshot["section"],
            "field": snapshot["field"], "canonical_value": snapshot["canonical_value"],
            "occurrences": snapshot["occurrences"], "read_set": snapshot["read_set"],
            "before_wdr_fingerprint": snapshot["read_set"]["wdr_fingerprint"],
            "after_wdr_fingerprint": sha256_bytes(staged_record.read_bytes()),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        receipt["receipt_id"] = content_id(receipt)
        write_json_atomic(staged_root / receipt_rel, receipt)
        consumed = dict(token_state)
        consumed["previous_state_id"] = token_state["state_id"]
        consumed["status"] = "consumed"
        consumed["receipt_id"] = receipt["receipt_id"]
        consumed["receipt_path"] = str(memory_root / receipt_rel)
        consumed.pop("state_id", None)
        consumed["state_id"] = content_id(consumed)
        write_json_atomic(staged_root / token_rel, consumed)
        changed = changed_staged_files(memory_root, staged_root)
        allowed = {record_rel, state_rel, projection_rel, receipt_rel, token_rel}
        unexpected = [path.as_posix() for path in changed if path not in allowed]
        if unexpected:
            raise StatusSyncContractError("WDR_FIELD_REPAIR_TARGET_INVALID", "unexpected repair targets: " + ", ".join(unexpected))
        if fail_after_stage:
            raise StatusSyncContractError("WDR_FIELD_REPAIR_INJECTED_FAILURE", "injected failure after repair staging")
        publication = publish_staged_files(memory_root, staged_root, changed, transaction_kind="wdr-field-repair")
    return receipt, memory_root / receipt_rel, publication, [path.as_posix() for path in changed]


def run_wdr_field_repair(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    principal = " ".join(args.principal.split())
    if not principal:
        raise StatusSyncContractError("WDR_FIELD_REPAIR_PRINCIPAL_INVALID", "principal must not be empty")
    snapshot = wdr_field_repair_snapshot(
        memory_root, args.id, args.section, args.field, args.canonical_value_file
    )
    if args.dry_run:
        if args.token:
            raise StatusSyncContractError("WDR_FIELD_REPAIR_TOKEN_INVALID", "--token is not accepted with --dry-run")
        token = issue_wdr_field_repair_token(memory_root, snapshot, principal) if snapshot.get("can_apply") else None
        emit({
            "ok": True, "mode": "repair-wdr-field", "dry_run": True,
            "verification_status": snapshot["verification_status"], "can_apply": snapshot.get("can_apply", False),
            "workstream_id": snapshot["workstream_id"], "section": snapshot["section"],
            "field": snapshot["field"], "occurrences": snapshot["occurrences"],
            "canonical_value": snapshot.get("canonical_value"), "reason": snapshot.get("reason"),
            "token": token, "receipt": None, "receipt_path": None,
        }, args.output)
        return 0
    if not snapshot.get("can_apply"):
        raise StatusSyncContractError("WDR_FIELD_REPAIR_REVIEW_REQUIRED", snapshot["reason"])
    if not args.token:
        raise StatusSyncContractError("WDR_FIELD_REPAIR_TOKEN_REQUIRED", "apply requires the token from dry-run")
    token_path = wdr_field_repair_token_path(memory_root, args.token)
    token_state = load_json_object(token_path)
    if not token_state:
        raise StatusSyncContractError("WDR_FIELD_REPAIR_TOKEN_INVALID", "repair token is unknown")
    claimed = token_state.get("state_id")
    body = dict(token_state); body.pop("state_id", None)
    if claimed != content_id(body) or token_state.get("token_hash") != sha256_bytes(args.token.encode("utf-8")):
        raise StatusSyncContractError("WDR_FIELD_REPAIR_TOKEN_INVALID", "repair token identity is invalid")
    if token_state.get("principal") != principal or token_state.get("status") != "unused":
        code = "WDR_FIELD_REPAIR_TOKEN_USED" if token_state.get("status") == "consumed" else "WDR_FIELD_REPAIR_TOKEN_INVALID"
        raise StatusSyncContractError(code, "repair token is unavailable for this principal")
    expires = datetime.fromisoformat(str(token_state.get("expires_at", "")).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires:
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="WDR_FIELD_REPAIR_TOKEN_EXPIRED")
        raise StatusSyncContractError("WDR_FIELD_REPAIR_TOKEN_EXPIRED", "repair token expired")
    binding = wdr_field_repair_binding(snapshot, principal)
    if token_state.get("binding") != binding or token_state.get("binding_digest") != content_id(binding):
        update_token_state(token_path, token_state, "invalidated", terminal_error_code="WDR_FIELD_REPAIR_READ_SET_STALE")
        raise StatusSyncContractError("WDR_FIELD_REPAIR_READ_SET_STALE", "WDR or lineage changed after dry-run")
    receipt, receipt_path, publication, changed = apply_wdr_field_repair(
        memory_root, snapshot, principal, token_path, token_state, args.fail_after_stage
    )
    emit({
        "ok": True, "mode": "repair-wdr-field", "dry_run": False,
        "verification_status": "verified", "outcome": "committed",
        "receipt": receipt, "receipt_path": str(receipt_path),
        "publication": publication, "changed_paths": changed,
    }, args.output)
    return 0


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
        completed_status_receipt(memory_root, input_hash, len(updates), input_path)
        if input_hash and not args.dry_run
        else None
    )
    if completed_receipt:
        receipt_path, receipt = completed_receipt
        if receipt.get("receipt_type") == "reconciliation":
            replayed_action_ids = sorted(
                {
                    str(item.get("matched_action_id"))
                    for item in receipt.get("reconciliation", {}).get("command_results", [])
                    if isinstance(item, dict) and item.get("matched_action_id")
                }
            )
            intent_convergence = {
                "status": "reconciled",
                "pending_intent_ids": [],
                "consumed_intent_ids": [],
                "outbox": None,
            }
        else:
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
            copy_memory_tree(memory_root, staged_root)
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
                record_path = staged_root / "workstreams" / update.workstream_id / "delivery-record.md"
                sidecar_path = record_path.with_name(ACTION_PROJECTION_REL)
                needs_sidecar_bootstrap = record_path.is_file() and not sidecar_path.is_file()
                if not record_path.is_file() or (not update.refresh_actions and not needs_sidecar_bootstrap):
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
    original_input_path = (
        require_file(args.original_updates_file, "original-updates-file")
        if args.original_updates_file
        else None
    )
    input_bytes = input_path.read_bytes()
    input_hash = sha256_bytes(input_bytes)
    try:
        input_payload, input_updates = load_updates_payload(input_path, "status sync")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError(f"updates-file must contain a valid executable status-sync payload: {exc}") from exc
    try:
        evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"evidence-file must contain valid JSON: {exc}") from exc
    attested_by = " ".join(args.attested_by.split())
    if not attested_by:
        raise ValueError("attested-by must not be empty")
    applied_at = normalize_required_timestamp(args.applied_at, "applied-at")
    original_bytes: bytes | None = None
    historical_binding: dict[str, Any] | None = None
    try:
        if original_input_path is None:
            migration = historical_evidence(
                evidence_payload,
                evidence_path,
                input_path,
                input_hash,
                len(input_updates),
                memory_root,
            )
        else:
            if original_input_path.resolve() in {input_path.resolve(), evidence_path.resolve()}:
                raise ValueError("original-updates-file must be distinct from updates-file and evidence-file")
            original_bytes = original_input_path.read_bytes()
            original_hash = sha256_bytes(original_bytes)
            if original_hash == input_hash:
                raise ValueError(
                    "historical-input-change migration requires different original and current raw-byte hashes"
                )
            try:
                original_payload, original_updates = load_updates_payload(original_input_path, "status sync")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError(
                    f"original-updates-file must contain a valid executable status-sync payload: {exc}"
                ) from exc
            original_canonical = canonical_executable_payload_from_raw(original_payload)
            current_canonical = canonical_executable_payload_from_raw(input_payload)
            executable_changed_paths = json_diff_paths(original_canonical, current_canonical)
            if executable_changed_paths:
                raise ValueError(
                    "historical-input-change executable payload differs at: "
                    + ", ".join(executable_changed_paths)
                )
            evidence = historical_input_change_evidence(
                evidence_payload,
                evidence_path,
                input_path,
                original_hash,
                len(original_updates),
                memory_root,
            )
            digest = original_hash.removeprefix("sha256:")
            snapshot_relative = HISTORICAL_INPUT_MIGRATION_EVIDENCE_REL / f"original-{digest}.json"
            diff_body = {
                "equal": True,
                "changed_paths": [],
                "non_executable_changed_paths": json_diff_paths(original_payload, input_payload),
            }
            executable_diff = {**diff_body, "diff_id": content_id(diff_body)}
            migration = {
                **evidence,
                "migration_kind": "historical-input-change",
                "original_input_path": str(input_path),
                "original_input_hash": original_hash,
                "original_input_source_path": str(original_input_path),
                "original_input_snapshot_path": snapshot_relative.as_posix(),
                "original_input_snapshot_hash": original_hash,
                "current_input_hash": input_hash,
                "original_payload_id": content_id(original_payload),
                "current_payload_id": content_id(input_payload),
                "canonical_executable_payload_id": content_id(original_canonical),
                "executable_diff": executable_diff,
            }
            historical_binding = {
                key: migration[key]
                for key in (
                    "migration_kind",
                    "original_input_hash",
                    "original_input_snapshot_path",
                    "original_payload_id",
                    "current_input_hash",
                    "current_payload_id",
                    "canonical_executable_payload_id",
                    "executable_diff",
                )
            }
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
                "original_input_path": str(original_input_path) if original_input_path else None,
                "evidence_path": str(evidence_path),
                "verified_plan_token": None,
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
        historical_binding,
    )
    if not args.dry_run and args.verified_plan_token != plan_token:
        raise ValueError("durable migration requires the verified-plan-token from an unchanged dry-run")
    receipt = status_sync_receipt(
        receipt_type="migration",
        input_path=input_path,
        input_hash=input_hash,
        update_count=len(input_updates),
        applied_at=None if args.dry_run else applied_at,
        dry_run=args.dry_run,
        migration=migration,
    )
    receipt_path = None if args.dry_run else memory_root / receipt_relative_path(receipt)
    if receipt_path is not None:
        if original_bytes is not None:
            snapshot_path = memory_root / str(migration["original_input_snapshot_path"])
            if snapshot_path.is_file() and snapshot_path.read_bytes() != original_bytes:
                raise ValueError("historical input snapshot path already contains different bytes")
            write_bytes_atomic(snapshot_path, original_bytes)
        write_json_atomic(receipt_path, receipt)
        if durable_status_receipt_record(memory_root, receipt_path) is None:
            receipt_path.unlink(missing_ok=True)
            raise ValueError("written migration receipt failed durable self-validation")
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
        copy_memory_tree(memory_root, staged_root)
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
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                return run_migrate_receipt(args)
        if args.command == "repair-wdr-field":
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_wdr_field_repair(args)
        if args.command == "reconcile-intake":
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_reconcile_intake(args)
        if args.command == "retire-intake":
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_retire_intake(args)
        if args.command == "migrate-authority-state":
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_authority_migration(args)
        if args.command == "repair":
            project_root = require_project_root(args.project_root)
            memory_root = resolve_memory_root(project_root, args.memory_root)
            with fact_write_lock(memory_root):
                recover_status_transactions(memory_root)
                return run_repair(args)
        raise ValueError(f"unknown command: {args.command}")
    except StatusSyncContractError as exc:
        payload = {"ok": False, "error_code": exc.error_code, "error": str(exc)}
        payload.update(exc.details)
        if getattr(args, "command", None) == "reconcile-intake":
            payload.setdefault("verification_status", "blocked")
            payload.setdefault("missing_commands", [])
            payload.setdefault("token", None)
        if getattr(args, "command", None) == "retire-intake":
            payload.setdefault("verification_status", "blocked")
            payload.setdefault("evidence_scan", {
                "verification_status": "blocked",
                "error_code": exc.error_code,
                "error": str(exc),
                "satisfied_commands": [],
                "missing_commands": [],
                "read_set": [],
            })
            payload.setdefault("token", None)
            payload.setdefault("receipt", None)
            payload.setdefault("receipt_path", None)
        emit(payload, getattr(args, "output", None))
        return 2
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        if getattr(args, "command", None) == "reconcile-intake":
            payload.update({"verification_status": "blocked", "missing_commands": [], "token": None})
        if getattr(args, "command", None) == "retire-intake":
            payload.update({
                "verification_status": "blocked",
                "evidence_scan": {
                    "verification_status": "blocked",
                    "error_code": "INTAKE_RETIREMENT_EVIDENCE_UNAVAILABLE",
                    "error": str(exc),
                    "satisfied_commands": [],
                    "missing_commands": [],
                    "read_set": [],
                },
                "token": None,
                "receipt": None,
                "receipt_path": None,
            })
        emit(payload, getattr(args, "output", None))
        return 2


if __name__ == "__main__":
    sys.exit(main())
