#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Apply an ADP meeting sync JSON plan to shared project memory."""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"
LANGUAGE_CONTEXT: dict[str, Any] = {"locale": "en", "module": None, "metadata": {}}
STATUS_INTENT_OUTBOX_REL = Path("state") / "status-intent-outbox.json"
FACT_LOCK_REL = Path("state") / "fact-write.lock"

CLASSIFICATIONS = {
    "fact",
    "decision",
    "action",
    "wdr_update",
    "business_decision_needed",
    "no_op",
}
MILESTONE_STATUSES = {"planned", "at-risk", "done", "blocked"}

DECISION_TYPE_DEFAULTS = {
    "decision": "FDE internal decision",
    "business_decision_needed": "Business decision",
}

MEETING_LINEAGE_FIELDS = (
    "meeting_pack_id",
    "meeting_pack_path",
    "scenario",
    "audit_path",
    "roadmap_version",
    "program_status_snapshot_id",
    "baseline_revision",
    "source_fingerprints",
    "input_audit_id",
    "generator_version",
)

GENERATOR_VERSION = "2.0.0"
PANEL_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PANEL_PROFILES = {"internal-full", "shareable-summary"}
WINDOWS_LOCK_RETRY_SECONDS = 0.05
WINDOWS_LOCK_CONTENTION_ERRORS = {
    error
    for error in (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", None))
    if error is not None
}
WINDOWS_LOCK_CONTENTION_WINERRORS = {33, 36}


class MeetingSyncConflict(RuntimeError):
    """Raised when replay would change an already landed destination."""

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a structured meeting archive and apply classified meeting "
            "items to ADP daily logs, decisions, WDRs, and business packets."
        ),
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument(
        "--plan",
        required=True,
        help="Path to meeting sync JSON plan, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--meeting-pack-distillate",
        help="Meeting-pack distillate JSON whose next_workflow_payload.lineage is injected and verified.",
    )
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report writes without modifying files.")
    parser.add_argument(
        "--meeting-note-template",
        required=True,
        help="Meeting archive template path. Relative paths resolve from the skill root.",
    )
    parser.add_argument(
        "--business-decision-packet-template",
        required=True,
        help="Business Decision Packet template path. Relative paths resolve from the skill root.",
    )
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("--language", help="Override document_output_language for rendered meeting artifacts.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        emit(
            {
                "ok": False,
                "error": "project_root is not an existing directory",
                "project_root": str(project_root),
            },
            args.output,
        )
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    if not memory_root.exists() or not memory_root.is_dir():
        emit(
            {
                "ok": False,
                "error": "ADP memory root is missing; run adp-project-kickoff or pass --memory-root",
                "memory_root": str(memory_root),
            },
            args.output,
        )
        return 2

    config_module = load_module(Path(args.config_script), "adp_meeting_sync_effective_config")
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        emit({"ok": False, "error": config.get("error", "shared ADP effective config could not be resolved")}, args.output)
        return 2
    locale = str(config.get("document_locale") or "en")
    LANGUAGE_CONTEXT.update({"locale": locale, "module": config_module, "metadata": language_metadata(config, locale)})

    try:
        plan = load_plan(args.plan)
        if args.meeting_pack_distillate:
            merge_meeting_pack_lineage(plan, project_root, args.meeting_pack_distillate)
        normalized = normalize_plan(plan)
        attach_meeting_identity(normalized)
        templates = resolve_templates(args.meeting_note_template, args.business_decision_packet_template)
    except ValueError as exc:
        emit({"ok": False, "error": str(exc)}, args.output)
        return 2

    hard_errors = validate_plan(normalized)
    if hard_errors:
        emit(
            {
                "ok": False,
                "error": "meeting sync plan has blocking validation errors",
                "validation_errors": hard_errors,
            },
            args.output,
        )
        return 2

    if args.verbose:
        print(f"Using memory root: {memory_root}", file=sys.stderr)

    try:
        result = apply_plan(project_root, memory_root, normalized, templates, args.dry_run)
    except ValueError as exc:
        emit({"ok": False, "error": str(exc)}, args.output)
        return 2
    result["language"] = LANGUAGE_CONTEXT["metadata"]
    emit(result, args.output)
    return 0 if result["ok"] else 1


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def resolve_templates(meeting_template: str, packet_template: str) -> dict[str, Path]:
    templates = {
        "meeting_note": resolve_skill_path(meeting_template),
        "business_decision_packet": resolve_skill_path(packet_template),
    }
    for label, path in templates.items():
        if not path.exists() or not path.is_file():
            raise ValueError(f"{label} template file not found: {path}")
    return templates


def resolve_skill_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = SKILL_ROOT / path
    return path.resolve()


def load_plan(raw_plan: str) -> dict[str, Any]:
    if raw_plan == "-":
        text = sys.stdin.read()
    else:
        path = Path(raw_plan)
        if not path.exists():
            raise ValueError(f"plan file not found: {path}")
        text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"plan is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("plan root must be a JSON object")
    return data


def merge_meeting_pack_lineage(plan: dict[str, Any], project_root: Path, raw_path: str) -> None:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"meeting-pack distillate file not found: {path}")
    try:
        distillate = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"meeting-pack distillate is not valid JSON: {exc}") from exc
    if not isinstance(distillate, dict):
        raise ValueError("meeting-pack distillate root must be a JSON object")
    payload = distillate.get("next_workflow_payload")
    if not isinstance(payload, dict):
        raise ValueError("meeting-pack distillate needs next_workflow_payload")
    source_lineage = payload.get("lineage")
    if not isinstance(source_lineage, dict):
        raise ValueError("meeting-pack distillate needs next_workflow_payload.lineage")
    missing = [field for field in MEETING_LINEAGE_FIELDS if not source_lineage.get(field)]
    if missing:
        raise ValueError("meeting-pack distillate lineage is missing: " + ", ".join(missing))
    lineage = {field: source_lineage[field] for field in MEETING_LINEAGE_FIELDS}
    for label, container in (("distillate", distillate), ("next_workflow_payload", payload)):
        conflicts = [field for field in MEETING_LINEAGE_FIELDS if field in container and container[field] != lineage[field]]
        if conflicts:
            raise ValueError(
                f"meeting-pack {label} lineage conflicts with next_workflow_payload.lineage: {', '.join(conflicts)}"
            )

    meeting = plan.get("meeting")
    if not isinstance(meeting, dict):
        raise ValueError("plan.meeting must be an object")
    existing = meeting.get("lineage")
    if existing not in (None, {}) and existing != lineage:
        raise ValueError("plan meeting.lineage conflicts with meeting-pack distillate")
    meeting["lineage"] = lineage


def normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    meeting = plan.get("meeting")
    items = plan.get("items")
    if not isinstance(meeting, dict):
        raise ValueError("plan.meeting must be an object")
    if not isinstance(items, list):
        raise ValueError("plan.items must be a list")

    normalized_items = []
    for index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"items[{index}] must be an object")
        item = dict(raw_item)
        item["id"] = string_value(item.get("id")) or f"M-{index:03d}"
        item["classification"] = normalize_classification(item.get("classification"))
        item["text"] = string_value(item.get("text"))
        item["action_id"] = string_value(item.get("action_id"))
        item["action_operation"] = string_value(item.get("action_operation") or item.get("operation"))
        item["expected_action_revision"] = item.get(
            "expected_action_revision", item.get("expected_revision")
        )
        item["action_patch_text"] = string_value(item.get("action"))
        item["action_patch_affected_workstreams"] = normalize_workstreams(
            item.get("action_affected_workstreams")
        )
        item["action_field_presence"] = sorted(
            field_name
            for field_name, aliases in {
                "status": ("status",),
                "owner": ("owner",),
                "action": ("action",),
                "due_or_trigger": ("due", "trigger"),
                "closure_criteria": ("closure_criteria",),
                "affected_workstreams": ("action_affected_workstreams",),
            }.items()
            if any(alias in raw_item for alias in aliases)
        )
        raw_status_intent = item.get("status_intent", item.get("current_field_update"))
        if raw_status_intent in (None, {}):
            item["status_intent"] = {}
        elif isinstance(raw_status_intent, dict):
            item["status_intent"] = normalize_status_intent(raw_status_intent)
        else:
            raise ValueError(f"items[{index}].status_intent must be an object")
        item["affected_workstreams"] = normalize_workstreams(item.get("affected_workstreams"))
        item["owner"] = string_value(item.get("owner")) or "TBD"
        item["due"] = string_value(item.get("due")) or string_value(item.get("trigger")) or "TBD"
        item["decision_type"] = (
            string_value(item.get("decision_type"))
            or DECISION_TYPE_DEFAULTS.get(item["classification"], "TBD")
        )
        item["confirmer"] = string_value(item.get("confirmer")) or "TBD"
        item["status"] = string_value(item.get("status")) or default_status(item["classification"])
        if item["classification"] == "action":
            item["status"] = normalize_action_status(item["status"])
        item["wdr_update"] = string_value(item.get("wdr_update"))
        item["no_op_reason"] = string_value(item.get("no_op_reason"))
        item["closure_criteria"] = string_value(item.get("closure_criteria"))
        item["status_confirmation"] = string_value(item.get("status_confirmation"))
        item["owner_gap"] = string_value(item.get("owner_gap"))
        item["closure_gap"] = string_value(item.get("closure_gap"))
        item["confirmer_gap"] = string_value(item.get("confirmer_gap"))
        item["speaker_label_gap"] = string_value(item.get("speaker_label_gap"))
        item["gap"] = string_value(item.get("gap"))
        item["milestones"] = normalize_milestone_updates(
            item.get("milestones", item.get("milestone_updates", item.get("milestone")))
        )
        item["packet"] = item.get("packet") if isinstance(item.get("packet"), dict) else {}
        normalized_items.append(item)

    raw_lineage = meeting.get("lineage") if isinstance(meeting.get("lineage"), dict) else {}
    lineage: dict[str, Any] = {}
    for field in MEETING_LINEAGE_FIELDS:
        raw_value = raw_lineage.get(field, meeting.get(field))
        if field == "source_fingerprints":
            lineage[field] = normalize_fingerprints(raw_value)
        elif field == "baseline_revision":
            lineage[field] = raw_value if isinstance(raw_value, int) and not isinstance(raw_value, bool) else string_value(raw_value)
        else:
            lineage[field] = string_value(raw_value)
    if not any(value not in (None, "", {}) for value in lineage.values()):
        lineage = {}

    return {
        "meeting": {
            "date": string_value(meeting.get("date")),
            "type": string_value(meeting.get("type")) or "meeting",
            "title": string_value(meeting.get("title")) or "ADP meeting sync",
            "source": string_value(meeting.get("source")) or "TBD",
            "raw_evidence_path": string_value(meeting.get("raw_evidence_path")),
            "raw_evidence_label": string_value(meeting.get("raw_evidence_label")) or "raw-evidence",
            "participants": normalize_people(meeting.get("participants")),
            "participant_gaps": normalize_gap_list(meeting.get("participant_gaps")),
            "summary": string_value(meeting.get("summary")) or "TBD",
            "meeting_instance_id": string_value(meeting.get("meeting_instance_id") or meeting.get("instance_id")),
            "started_at": string_value(meeting.get("started_at") or meeting.get("actual_started_at")),
            "ended_at": string_value(meeting.get("ended_at") or meeting.get("actual_ended_at")),
            "lineage": lineage,
            "panel_archive": normalize_panel_archive(meeting.get("panel_archive")),
        },
        "items": normalized_items,
    }


def attach_meeting_identity(plan: dict[str, Any]) -> None:
    meeting = plan["meeting"]
    if not meeting["meeting_instance_id"]:
        identity = {
            "date": meeting["date"],
            "type": meeting["type"],
            "title": meeting["title"],
            "source": meeting["source"],
            "started_at": meeting["started_at"],
            "ended_at": meeting["ended_at"],
            "scenario": meeting.get("lineage", {}).get("scenario", ""),
            "meeting_pack_id": meeting.get("lineage", {}).get("meeting_pack_id", ""),
        }
        identity_hash = fingerprint_json(identity).split(":", 1)[1][:12]
        identity_label = slugify(meeting.get("lineage", {}).get("scenario") or meeting["type"])
        meeting["meeting_instance_id"] = f"mi-{meeting['date']}-{identity_label}-{identity_hash}"
    meeting["plan_fingerprint"] = fingerprint_json(plan)


def validate_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    meeting = plan["meeting"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting["date"]):
        errors.append("meeting.date must use YYYY-MM-DD")
    elif parse_iso_date(meeting["date"]) is None:
        errors.append("meeting.date must be a real calendar date")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", meeting["meeting_instance_id"]):
        errors.append("meeting.meeting_instance_id must be a path-safe identifier")
    lineage = meeting.get("lineage", {})
    if lineage:
        missing_lineage = [field for field in MEETING_LINEAGE_FIELDS if not lineage.get(field)]
        if missing_lineage:
            errors.append("meeting.lineage is missing: " + ", ".join(missing_lineage))
        if not meeting["started_at"] or not meeting["ended_at"]:
            errors.append("meeting.started_at and meeting.ended_at are required for meeting-pack lineage")
        if not isinstance(lineage.get("source_fingerprints"), dict):
            errors.append("meeting.lineage.source_fingerprints must be an object")
    if meeting["started_at"] or meeting["ended_at"]:
        started_at = parse_timestamp(meeting["started_at"])
        ended_at = parse_timestamp(meeting["ended_at"])
        if started_at is None or ended_at is None:
            errors.append("meeting.started_at and meeting.ended_at must be ISO-8601 timestamps")
        elif ended_at < started_at:
            errors.append("meeting.ended_at must not be before meeting.started_at")
    panel_archive = meeting.get("panel_archive")
    if panel_archive:
        if not PANEL_ID_RE.fullmatch(panel_archive.get("panel_id", "")):
            errors.append("meeting.panel_archive.panel_id must be a sha256 panel ID")
        if not panel_archive.get("archive"):
            errors.append("meeting.panel_archive.archive is required")
        if panel_archive.get("distribution_profile") not in PANEL_PROFILES:
            errors.append("meeting.panel_archive.distribution_profile is invalid")

    item_ids: set[str] = set()
    for item in plan["items"]:
        prefix = f"item {item['id']}: "
        if item["id"] in item_ids:
            errors.append(prefix + "duplicate id")
        item_ids.add(item["id"])
        if item["classification"] not in CLASSIFICATIONS:
            errors.append(prefix + f"unknown classification {item['classification']!r}")
        if not item["text"]:
            errors.append(prefix + "text is required")
        if item["classification"] == "no_op" and not item["no_op_reason"]:
            errors.append(prefix + "no_op requires no_op_reason")
        if item["classification"] == "business_decision_needed":
            packet = item["packet"]
            decision_needed = string_value(packet.get("decision_needed"))
            if not decision_needed:
                errors.append(prefix + "business_decision_needed requires packet.decision_needed")
        if item["action_operation"] not in {"", "create", "patch"}:
            errors.append(prefix + f"unsupported action operation {item['action_operation']!r}")
        if item["action_id"] or item["action_operation"] == "patch":
            if item["classification"] != "action":
                errors.append(prefix + "action_id/action_operation is only valid for action items")
            if not item["action_id"]:
                errors.append(prefix + "action patch requires action_id")
            revision = item["expected_action_revision"]
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                errors.append(prefix + "action patch requires positive expected_action_revision")
            if not item["action_field_presence"]:
                errors.append(prefix + "action patch must include at least one mutable field")
        if item["status_intent"] and not item["affected_workstreams"]:
            errors.append(prefix + "status_intent requires an affected workstream")
    return errors


def normalize_status_intent(raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "status",
        "phase",
        "progress",
        "blockers",
        "risks",
        "dependencies",
        "change_notes",
        "refresh_actions",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError("status_intent contains unsupported fields: " + ", ".join(unknown))
    normalized: dict[str, Any] = {}
    for field_name, value in raw.items():
        if field_name == "refresh_actions":
            if value is not True:
                raise ValueError("status_intent.refresh_actions must be true when supplied")
            normalized[field_name] = True
        elif field_name in {"blockers", "risks", "dependencies", "change_notes"}:
            if not isinstance(value, list):
                raise ValueError(f"status_intent.{field_name} must be a list")
            normalized[field_name] = [string_value(item) for item in value if string_value(item)]
        else:
            text = string_value(value)
            if not text:
                raise ValueError(f"status_intent.{field_name} must be non-empty")
            normalized[field_name] = text
    if not normalized:
        raise ValueError("status_intent must contain at least one field")
    return normalized


@contextmanager
def meeting_sync_lock(memory_root: Path):
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

    # msvcrt cannot lock a byte beyond the current end of the file.
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


def apply_plan(
    project_root: Path,
    memory_root: Path,
    plan: dict[str, Any],
    templates: dict[str, Path],
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return _apply_plan_locked(project_root, memory_root, plan, templates, True)
    with meeting_sync_lock(memory_root):
        return _apply_plan_locked(project_root, memory_root, plan, templates, False)


def _apply_plan_locked(
    project_root: Path,
    memory_root: Path,
    plan: dict[str, Any],
    templates: dict[str, Path],
    dry_run: bool,
) -> dict[str, Any]:
    meeting = plan["meeting"]
    try:
        verified_panel_archive = verify_panel_archive(memory_root, meeting)
    except MeetingSyncConflict as exc:
        return {
            "ok": False,
            "dry_run": dry_run,
            "status": "blocked",
            "error": str(exc),
            "meeting": meeting,
        }
    receipt_path = meeting_receipt_path(memory_root, meeting)
    try:
        existing_receipt = load_json_object(receipt_path)
    except MeetingSyncConflict as exc:
        return {
            "ok": False,
            "dry_run": dry_run,
            "status": "conflict",
            "error": str(exc),
            "meeting": meeting,
            "receipt": str(receipt_path),
        }
    if existing_receipt:
        if existing_receipt.get("plan_fingerprint") != meeting["plan_fingerprint"]:
            return replay_conflict_result(memory_root, meeting, receipt_path, existing_receipt)
        if existing_receipt.get("status") == "applied":
            try:
                cursor = advance_meeting_cursor(memory_root, meeting, existing_receipt, dry_run)
            except MeetingSyncConflict as exc:
                return {
                    "ok": False,
                    "dry_run": dry_run,
                    "status": "conflict",
                    "error": str(exc),
                    "meeting": meeting,
                    "receipt": str(receipt_path),
                }
            result = dict(existing_receipt.get("result", {}))
            if verified_panel_archive and not existing_receipt.get("official_panel_archive") and not dry_run:
                association = official_panel_association(verified_panel_archive, existing_receipt.get("applied_at"))
                existing_receipt["official_panel_archive"] = association
                result["official_panel_archive"] = association
                existing_receipt["result"] = result
                write_json_atomic(receipt_path, existing_receipt)
            result.update(
                {
                    "ok": True,
                    "dry_run": dry_run,
                    "replay_status": "idempotent-no-op",
                    "meeting": meeting,
                    "receipt": str(receipt_path),
                    "cursor": cursor,
                }
            )
            return result

    run_timestamp = string_value(existing_receipt.get("started_at")) if existing_receipt else ""
    if not run_timestamp:
        run_timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if not dry_run:
        write_json_atomic(
            receipt_path,
            {
                "schema_version": 1,
                "meeting_instance_id": meeting["meeting_instance_id"],
                "plan_fingerprint": meeting["plan_fingerprint"],
                "input_hash": meeting["plan_fingerprint"],
                "status": "applying",
                "started_at": run_timestamp,
                "lineage": meeting.get("lineage", {}),
                "generator_version": GENERATOR_VERSION,
            },
        )

    try:
        result = _apply_plan_once(project_root, memory_root, plan, templates, dry_run, run_timestamp)
    except MeetingSyncConflict as exc:
        conflict = {
            "ok": False,
            "dry_run": dry_run,
            "status": "conflict",
            "error": str(exc),
            "meeting": meeting,
            "receipt": str(receipt_path),
        }
        if not dry_run:
            write_json_atomic(
                receipt_path,
                {
                    "schema_version": 1,
                    "meeting_instance_id": meeting["meeting_instance_id"],
                    "plan_fingerprint": meeting["plan_fingerprint"],
                    "input_hash": meeting["plan_fingerprint"],
                    "status": "conflict",
                    "started_at": run_timestamp,
                    "conflict": str(exc),
                    "lineage": meeting.get("lineage", {}),
                    "generator_version": GENERATOR_VERSION,
                },
            )
        return conflict

    if dry_run:
        result.update(
            {
                "replay_status": "planned",
                "planned_receipt": str(receipt_path),
                "planned_cursor": planned_cursor_path(memory_root, meeting),
            }
        )
        return result

    applied_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    receipt = {
        "schema_version": 1,
        "meeting_instance_id": meeting["meeting_instance_id"],
        "plan_fingerprint": meeting["plan_fingerprint"],
        "input_hash": meeting["plan_fingerprint"],
        "status": "applied",
        "started_at": run_timestamp,
        "applied_at": applied_at,
        "archive": rel_to_memory(memory_root, Path(result["touched"]["meeting_archives"][0])),
        "lineage": meeting.get("lineage", {}),
        "generator_version": GENERATOR_VERSION,
        "result": result,
    }
    write_json_atomic(receipt_path, receipt)
    try:
        cursor = advance_meeting_cursor(memory_root, meeting, receipt, False)
    except MeetingSyncConflict as exc:
        receipt["sync_status"] = "failed"
        receipt["cursor"] = {"status": "conflict", "error": str(exc)}
        write_json_atomic(receipt_path, receipt)
        return {
            "ok": False,
            "dry_run": False,
            "status": "conflict",
            "error": str(exc),
            "meeting": meeting,
            "receipt": str(receipt_path),
            "replay_status": "applied-with-cursor-conflict",
        }
    result.update(
        {
            "replay_status": "applied" if not existing_receipt else "resumed",
            "receipt": str(receipt_path),
            "cursor": cursor,
        }
    )
    receipt["sync_status"] = "complete"
    if verified_panel_archive:
        association = official_panel_association(verified_panel_archive, applied_at)
        result["official_panel_archive"] = association
        receipt["official_panel_archive"] = association
    result.setdefault("touched", {}).setdefault("write_receipts", []).append(str(receipt_path))
    if cursor.get("path") and cursor.get("status") in {"advanced", "unchanged", "repaired"}:
        result["touched"].setdefault("meeting_cursors", []).append(cursor["path"])
    result["touched"] = dedupe_touched(result["touched"])
    receipt["result"] = result
    receipt["cursor"] = cursor
    write_json_atomic(receipt_path, receipt)
    return result


def normalize_panel_archive(raw: Any) -> dict[str, str]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("meeting.panel_archive must be an object")
    return {
        "panel_id": string_value(raw.get("panel_id")),
        "archive": string_value(raw.get("archive") or raw.get("archive_html")),
        "distribution_profile": string_value(raw.get("distribution_profile")),
    }


def verify_panel_archive(memory_root: Path, meeting: dict[str, Any]) -> dict[str, str] | None:
    archive = meeting.get("panel_archive")
    if not archive:
        return None
    raw_path = Path(archive["archive"])
    if raw_path.is_absolute() or ".." in raw_path.parts:
        raise MeetingSyncConflict("meeting panel archive must be a path below ADP memory")
    path = (memory_root / raw_path).resolve()
    snapshot_root = (memory_root / "snapshots/management-panel").resolve()
    if not path.is_relative_to(snapshot_root) or not path.is_file():
        raise MeetingSyncConflict("meeting panel archive is missing from snapshots/management-panel")
    text = path.read_text(encoding="utf-8")
    opener = '<script type="application/json" id="adp-panel-manifest">'
    start = text.find(opener)
    end = text.find("</script>", start + len(opener)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise MeetingSyncConflict("meeting panel archive has no embedded manifest")
    try:
        manifest = json.loads(text[start + len(opener) : end])
    except json.JSONDecodeError as exc:
        raise MeetingSyncConflict(f"meeting panel archive manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise MeetingSyncConflict("meeting panel archive manifest must be an object")
    if manifest.get("panel_id") != archive["panel_id"]:
        raise MeetingSyncConflict("meeting panel archive ID does not match its embedded manifest")
    if manifest.get("distribution_profile") != archive["distribution_profile"]:
        raise MeetingSyncConflict("meeting panel archive profile does not match its embedded manifest")
    return {
        "panel_id": archive["panel_id"],
        "archive": rel_to_memory(memory_root, path),
        "distribution_profile": archive["distribution_profile"],
    }


def official_panel_association(archive: dict[str, str], applied_at: Any) -> dict[str, str]:
    return {
        **archive,
        "receipt_status": "applied",
        "associated_at": string_value(applied_at),
    }


def _apply_plan_once(
    project_root: Path,
    memory_root: Path,
    plan: dict[str, Any],
    templates: dict[str, Path],
    dry_run: bool,
    run_timestamp: str,
) -> dict[str, Any]:
    meeting = plan["meeting"]
    items = plan["items"]
    now = run_timestamp
    ensure_directories(memory_root, dry_run)

    touched: dict[str, list[str]] = {
        "meeting_archives": [],
        "daily_logs": [],
        "decision_logs": [],
        "raw_evidence_files": [],
        "business_decision_packets": [],
        "status_sync_intake_files": [],
        "workstream_records": [],
        "workstream_decisions": [],
        "status_intent_outbox": [],
    }
    unresolved_gaps: list[str] = []

    instance_suffix = meeting_instance_suffix(meeting)
    meeting_path = memory_root / "meetings" / (
        f"{meeting['date']}-{slugify(meeting['type'])}-{slugify(meeting['title'])}-{instance_suffix}.md"
    )
    raw_evidence_path, raw_evidence_gap = copy_raw_evidence(project_root, memory_root, meeting, True)
    if raw_evidence_path:
        meeting["raw_evidence"] = rel_to_memory(memory_root, raw_evidence_path)
        touched["raw_evidence_files"].append(str(raw_evidence_path))
    if raw_evidence_gap:
        unresolved_gaps.append(f"meeting: {raw_evidence_gap}")
    for gap in meeting["participant_gaps"]:
        unresolved_gaps.append(f"meeting: {gap}")

    for item in items:
        if item["classification"] == "business_decision_needed":
            item["packet_path"] = business_packet_path(memory_root, meeting, item, dry_run)

    status_sync_intake, action_quality_audit, milestone_quality_audit = build_status_sync_intake(
        memory_root, meeting_path, meeting, items
    )
    unresolved_gaps.extend(
        f"{item['item_id']}: {gap}"
        for item in milestone_quality_audit["blocked_milestones"]
        for gap in item["gaps"]
    )
    if raw_evidence_path and not dry_run:
        copy_raw_evidence(project_root, memory_root, meeting, False)

    closure_rows, item_details, item_destinations = render_item_outputs(
        memory_root,
        meeting_path,
        meeting,
        items,
        unresolved_gaps,
    )
    meeting_content = render_template(
        templates["meeting_note"],
        {
            "MEETING_TITLE": meeting["title"],
            "MEETING_DATE": meeting["date"],
            "MEETING_TYPE": meeting["type"],
            "SOURCE": meeting["source"],
            "RAW_EVIDENCE": meeting.get("raw_evidence", "TBD"),
            "PARTICIPANTS": ", ".join(meeting["participants"]) or "TBD",
            "GENERATED_AT": now,
            "SUMMARY": meeting["summary"],
            "CLOSURE_ROWS": "\n".join(closure_rows),
            "ITEM_DETAILS": "\n\n".join(item_details),
        },
    )
    lineage_block = render_meeting_lineage(meeting)
    if lineage_block:
        meeting_content = meeting_content.rstrip() + "\n\n" + lineage_block + "\n"
    identity_block = render_meeting_identity(meeting)
    meeting_content = meeting_content.rstrip() + "\n\n" + identity_block + "\n"
    meeting_content = localize_system_copy(meeting_content)
    write_file(meeting_path, meeting_content, dry_run)
    touched["meeting_archives"].append(str(meeting_path))

    daily_path = memory_root / "daily" / f"{meeting['date']}.md"
    append_file(daily_path, render_daily_block(memory_root, meeting_path, meeting, items), dry_run)
    touched["daily_logs"].append(str(daily_path))

    decision_items = [
        item
        for item in items
        if item["classification"] in {"decision", "business_decision_needed"}
    ]
    if decision_items:
        decision_log = memory_root / "decisions" / "decision-log.md"
        rows = [
            decision_log_row(memory_root, meeting_path, item, item_destinations.get(item["id"], []))
            for item in decision_items
        ]
        upsert_decision_rows(decision_log, rows, dry_run)
        touched["decision_logs"].append(str(decision_log))

    for item in items:
        if item["classification"] == "business_decision_needed":
            packet_path = create_business_packet(
                memory_root,
                meeting_path,
                meeting,
                item,
                templates["business_decision_packet"],
                now,
                dry_run,
            )
            touched["business_decision_packets"].append(str(packet_path))
            item_destinations.setdefault(item["id"], []).append(rel_to_memory(memory_root, packet_path))

        if item["classification"] in {"decision", "business_decision_needed"}:
            for workstream_id in item["affected_workstreams"]:
                decisions_path = memory_root / "workstreams" / workstream_id / "decisions.md"
                if decisions_path.exists():
                    append_file(
                        decisions_path,
                        render_workstream_decision_block(memory_root, meeting_path, meeting, item),
                        dry_run,
                    )
                    touched["workstream_decisions"].append(str(decisions_path))

        if should_append_wdr(item):
            for workstream_id in item["affected_workstreams"]:
                record_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
                if record_path.exists():
                    append_file(
                        record_path,
                        render_wdr_block(memory_root, meeting_path, meeting, item),
                        dry_run,
                    )
                    touched["workstream_records"].append(str(record_path))
                else:
                    unresolved_gaps.append(
                        f"{item['id']}: WDR update references missing workstream {workstream_id}"
                    )

    if status_sync_intake:
        intake_path = status_sync_intake_path(memory_root, meeting, dry_run)
        write_file(
            intake_path,
            json.dumps(status_sync_intake, ensure_ascii=False, indent=2),
            dry_run,
        )
        touched["status_sync_intake_files"].append(str(intake_path))
        if status_sync_intake.get("status_intents"):
            outbox_path = memory_root / STATUS_INTENT_OUTBOX_REL
            append_pending_status_intents(
                outbox_path,
                status_sync_intake["status_intents"],
                meeting["meeting_instance_id"],
                dry_run,
            )
            touched["status_intent_outbox"].append(str(outbox_path))

    intake_files = touched["status_sync_intake_files"]
    next_command_args = (
        [
            "adp-status-sync",
            "update",
            str(project_root),
            "--memory-root",
            str(memory_root),
            "--updates-file",
            str(intake_files[0]),
        ]
        if intake_files
        else []
    )
    result = {
        "ok": True,
        "dry_run": dry_run,
        "memory_root": str(memory_root),
        "meeting": meeting,
        "touched": dedupe_touched(touched),
        "unresolved_gaps": sorted(set(unresolved_gaps)),
        "action_quality_audit": action_quality_audit,
        "milestone_quality_audit": milestone_quality_audit,
        "next_actions": next_actions(
            project_root,
            memory_root,
            items,
            touched["status_sync_intake_files"],
            action_quality_audit,
        ),
        "refresh_required": not dry_run,
        "dirty_hints": sorted(
            rel_to_memory(memory_root, Path(path))
            for paths in touched.values()
            for path in paths
        ),
        "next_command": shlex.join(next_command_args) if next_command_args else None,
        "next_command_args": next_command_args,
    }
    return result


def ensure_directories(memory_root: Path, dry_run: bool) -> None:
    for rel in [
        "meetings",
        "meetings/raw",
        "meetings/receipts",
        "meetings/cursors",
        "daily",
        "decisions",
        "decisions/business-decision-packets",
        "intake/status-sync",
        "workstreams",
    ]:
        target = memory_root / rel
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)


def copy_raw_evidence(
    project_root: Path,
    memory_root: Path,
    meeting: dict[str, Any],
    dry_run: bool,
) -> tuple[Path | None, str]:
    raw_path = string_value(meeting.get("raw_evidence_path"))
    if not raw_path:
        return None, ""

    source = Path(raw_path)
    if not source.is_absolute():
        source = project_root / source
    source = source.resolve()
    if not source.exists() or not source.is_file():
        return None, f"raw evidence file not found: {raw_path}"

    target_name = (
        f"{meeting['date']}-{slugify(meeting['type'])}-{slugify(meeting['title'])}-"
        f"{slugify(meeting['raw_evidence_label'])}-{meeting_instance_suffix(meeting)}{source.suffix or '.txt'}"
    )
    target = memory_root / "meetings" / "raw" / target_name
    if not dry_run:
        write_bytes_once(target, source.read_bytes())
    return target, ""


def render_item_outputs(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    items: list[dict[str, Any]],
    unresolved_gaps: list[str],
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    closure_rows: list[str] = []
    details: list[str] = []
    destinations: dict[str, list[str]] = {}

    for item in items:
        item_destinations = planned_destinations(memory_root, meeting_path, meeting, item)
        destinations[item["id"]] = item_destinations
        gap = item_gap(meeting, item)
        if gap:
            unresolved_gaps.append(f"{item['id']}: {gap}")
        closure_rows.append(
            "| {id} | {classification} | {workstreams} | {owner} | {due} | {destinations} | {gap} |".format(
                id=cell(item["id"]),
                classification=cell(item["classification"]),
                workstreams=cell(", ".join(item["affected_workstreams"]) or "TBD"),
                owner=cell(item["owner"]),
                due=cell(item["due"]),
                destinations=cell(", ".join(item_destinations)),
                gap=cell(gap or ""),
            ),
        )
        details.append(render_item_detail(item, item_destinations, gap))

    return closure_rows, details, destinations


def planned_destinations(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    item: dict[str, Any],
) -> list[str]:
    destinations = [
        rel_to_memory(memory_root, meeting_path),
        f"daily/{meeting['date']}.md",
    ]
    if item["classification"] in {"decision", "business_decision_needed"}:
        destinations.append("decisions/decision-log.md")
    if item["classification"] == "business_decision_needed":
        packet_path = item.get("packet_path")
        if isinstance(packet_path, Path):
            destinations.append(rel_to_memory(memory_root, packet_path))
        else:
            destinations.append("decisions/business-decision-packets/{generated}.md")
    if should_append_wdr(item):
        for workstream_id in item["affected_workstreams"]:
            destinations.append(f"workstreams/{workstream_id}/delivery-record.md")
    if item["classification"] in {"decision", "business_decision_needed"}:
        for workstream_id in item["affected_workstreams"]:
            decisions_path = memory_root / "workstreams" / workstream_id / "decisions.md"
            if decisions_path.exists():
                destinations.append(f"workstreams/{workstream_id}/decisions.md")
    return destinations


def item_gap(meeting: dict[str, Any], item: dict[str, Any]) -> str:
    gaps: list[str] = []
    if item["classification"] in {"action", "wdr_update"} and not item["affected_workstreams"]:
        gaps.append("affected workstream is missing")
    if item["classification"] == "action":
        if is_missing_owner(item["owner"]):
            gaps.append("action owner is missing")
        if is_missing_due(item["due"]):
            gaps.append("action due trigger is missing")
        if is_missing_closure_criteria(item["closure_criteria"]):
            gaps.append("action closure criteria is missing")
    if item["classification"] in {"decision", "business_decision_needed"} and item["confirmer"] == "TBD":
        gaps.append("confirmer is missing")
    if item["classification"] == "wdr_update" and not item["wdr_update"]:
        gaps.append("wdr_update text is missing")
    gaps.extend(
        gap
        for gap in [
            item["owner_gap"],
            item["closure_gap"],
            item["confirmer_gap"],
            item["speaker_label_gap"],
            item["gap"],
        ]
        if gap
    )
    for milestone in item["milestones"]:
        gaps.extend(milestone_handoff_gaps(meeting, item, milestone))
    return "; ".join(gaps)


def render_item_detail(item: dict[str, Any], destinations: list[str], gap: str) -> str:
    lines = [
        f"### {item['id']} - {item['classification']}",
        "",
        item["text"],
        "",
        f"- Affected workstreams: {', '.join(item['affected_workstreams']) or 'TBD'}",
        f"- Owner: {item['owner']}",
        f"- Due / trigger: {item['due']}",
        f"- Decision type: {item['decision_type']}",
        f"- Confirmer: {item['confirmer']}",
        f"- Status: {item['status']}",
        f"- Destinations: {', '.join(destinations)}",
    ]
    if item["wdr_update"]:
        lines.append(f"- WDR update: {item['wdr_update']}")
    if item["no_op_reason"]:
        lines.append(f"- No-op reason: {item['no_op_reason']}")
    if gap:
        lines.append(f"- Gap: {gap}")
    return "\n".join(lines)


def render_daily_block(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    items: list[dict[str, Any]],
) -> str:
    rows = []
    for item in items:
        rows.append(
            "| {id} | {classification} | {workstreams} | {owner} | {due} | {text} |".format(
                id=cell(item["id"]),
                classification=cell(item["classification"]),
                workstreams=cell(", ".join(item["affected_workstreams"]) or "TBD"),
                owner=cell(item["owner"]),
                due=cell(item["due"]),
                text=cell(item["text"]),
            ),
        )
    lineage_lines = [
        f"- {field.replace('_', ' ').title()}: `{render_metadata_value(value)}`"
        for field, value in meeting.get("lineage", {}).items()
    ]
    return "\n".join(
        [
            operation_marker(meeting, "daily"),
            f"## Meeting Sync: {meeting['title']}",
            "",
            f"- Meeting instance ID: `{meeting['meeting_instance_id']}`",
            f"- Actual start: `{meeting.get('started_at') or 'TBD'}`",
            f"- Actual end: `{meeting.get('ended_at') or 'TBD'}`",
            f"- Type: {meeting['type']}",
            f"- Source: {meeting['source']}",
            *lineage_lines,
            f"- Raw evidence: {meeting.get('raw_evidence', 'TBD')}",
            f"- Archive: `{rel_to_memory(memory_root, meeting_path)}`",
            f"- Participants: {', '.join(meeting['participants']) or 'TBD'}",
            "",
            meeting["summary"],
            "",
            "| ID | Classification | Workstreams | Owner | Due / Trigger | Item |",
            "| --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
        ],
    )


def render_meeting_lineage(meeting: dict[str, Any]) -> str:
    lineage = meeting.get("lineage", {})
    if not lineage:
        return ""
    return "\n".join(
        [
            "## Meeting Pack Lineage",
            "",
            *[f"- {field}: `{render_metadata_value(lineage[field])}`" for field in MEETING_LINEAGE_FIELDS],
        ]
    )


def render_meeting_identity(meeting: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## Meeting Instance",
            "",
            f"- meeting_instance_id: `{meeting['meeting_instance_id']}`",
            f"- plan_fingerprint: `{meeting['plan_fingerprint']}`",
            f"- started_at: `{meeting.get('started_at') or 'TBD'}`",
            f"- ended_at: `{meeting.get('ended_at') or 'TBD'}`",
            f"- generator_version: `{GENERATOR_VERSION}`",
        ]
    )


def decision_log_row(
    memory_root: Path,
    meeting_path: Path,
    item: dict[str, Any],
    destinations: list[str],
) -> str:
    link = rel_to_memory(memory_root, meeting_path)
    packet_links = [dest for dest in destinations if dest.startswith("decisions/business-decision-packets/")]
    if packet_links:
        link = packet_links[0]
    return "| {date} | {type} | {decision} | {source} | {workstreams} | {confirmer} | {status} | {link} |".format(
        date=cell(today_from_path(meeting_path)),
        type=cell(item["decision_type"]),
        decision=cell(decision_text(item)),
        source=cell(rel_to_memory(memory_root, meeting_path)),
        workstreams=cell(", ".join(item["affected_workstreams"]) or "TBD"),
        confirmer=cell(item["confirmer"]),
        status=cell(item["status"]),
        link=cell(link),
    )


def upsert_decision_rows(path: Path, rows: list[str], dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_text_atomic(path, default_decision_log() + "\n")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    lines = [line for line in lines if not is_placeholder_decision_row(line)]
    existing_rows = set(lines)
    rows = [row for row in rows if row not in existing_rows]
    insert_at = find_decision_table_insert_index(lines)
    for row in reversed(rows):
        lines.insert(insert_at, row)
    write_text_atomic(path, "\n".join(lines).rstrip() + "\n")


def find_decision_table_insert_index(lines: list[str]) -> int:
    header_index = next(
        (i for i, line in enumerate(lines) if line.startswith("| Date | Type |")),
        None,
    )
    if header_index is None:
        return len(lines)
    index = header_index + 2
    while index < len(lines) and lines[index].startswith("|"):
        index += 1
    return index


def is_placeholder_decision_row(line: str) -> bool:
    if not line.startswith("|"):
        return False
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return len(cells) >= 8 and all(cell == "TBD" for cell in cells[:5]) and cells[6] == "open"


def default_decision_log() -> str:
    return "\n".join(
        [
            "# Decision Log",
            "",
            "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ],
    )


def create_business_packet(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    item: dict[str, Any],
    template_path: Path,
    created_at: str,
    dry_run: bool,
) -> Path:
    packet = item["packet"]
    title = decision_text(item)
    packet_path = item.get("packet_path")
    if not isinstance(packet_path, Path):
        packet_path = business_packet_path(memory_root, meeting, item, dry_run)
    content = render_template(
        template_path,
        {
            "TITLE": title,
            "CREATED_AT": created_at,
            "SOURCE_MEETING": rel_to_memory(memory_root, meeting_path),
            "AFFECTED_WORKSTREAMS": ", ".join(item["affected_workstreams"]) or "TBD",
            "STATUS": item["status"],
            "CONFIRMING_OWNER": string_value(packet.get("confirming_owner")) or item["confirmer"],
            "DEADLINE": string_value(packet.get("deadline")) or item["due"],
            "BACKGROUND": string_value(packet.get("background")) or item["text"],
            "DECISION_NEEDED": title,
            "OPTIONS": bullet_list(packet.get("options")),
            "RECOMMENDATION": string_value(packet.get("recommendation")) or "TBD",
            "RISKS_TRADEOFFS": string_value(packet.get("risks_tradeoffs")) or "TBD",
        },
    )
    content = localize_system_copy(content)
    write_file(packet_path, content, dry_run)
    return packet_path


def business_packet_path(
    memory_root: Path,
    meeting: dict[str, Any],
    item: dict[str, Any],
    dry_run: bool,
) -> Path:
    title = decision_text(item)
    filename = f"{meeting['date']}-{slugify(item['id'])}-{slugify(title)}-{meeting_instance_suffix(meeting)}.md"
    return memory_root / "decisions" / "business-decision-packets" / filename


def decision_text(item: dict[str, Any]) -> str:
    if item["classification"] == "business_decision_needed":
        decision_needed = string_value(item["packet"].get("decision_needed"))
        if decision_needed:
            return decision_needed
    return item["text"]


def render_workstream_decision_block(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    item: dict[str, Any],
) -> str:
    return "\n".join(
        [
            operation_marker(meeting, f"workstream-decision:{item['id']}"),
            f"## Meeting Decision: {meeting['date']} - {item['id']}",
            "",
            f"- Source: `{rel_to_memory(memory_root, meeting_path)}`",
            f"- Type: {item['decision_type']}",
            f"- Decision / question: {item['text']}",
            f"- Confirmer: {item['confirmer']}",
            f"- Status: {item['status']}",
            f"- Due / trigger: {item['due']}",
            "",
        ],
    )


def render_wdr_block(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    item: dict[str, Any],
) -> str:
    update = item["wdr_update"] or item["text"]
    return "\n".join(
        [
            operation_marker(meeting, f"wdr:{item['id']}"),
            f"## Meeting Sync Update: {meeting['date']} - {item['id']}",
            "",
            f"- Source: `{rel_to_memory(memory_root, meeting_path)}`",
            f"- Classification: {item['classification']}",
            f"- Update: {update}",
            f"- Owner: {item['owner']}",
            f"- Due / trigger: {item['due']}",
            f"- Status: {item['status']}",
            "",
        ],
    )


def should_append_wdr(item: dict[str, Any]) -> bool:
    return item["classification"] in {"fact", "action", "wdr_update", "decision"} and bool(
        item["affected_workstreams"]
    )


def build_status_sync_intake(
    memory_root: Path,
    meeting_path: Path,
    meeting: dict[str, Any],
    items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    today = date.fromisoformat(meeting["date"])
    audit = {
        "actions_seen": 0,
        "canonical_actions": 0,
        "ledger_ready_actions": 0,
        "fanout_suppressed": 0,
        "duplicate_actions_merged": 0,
        "owner_gap_count": 0,
        "due_gap_count": 0,
        "workstream_gap_count": 0,
        "closure_gap_count": 0,
        "past_due_open_count": 0,
        "status_review_required_count": 0,
        "blocked_actions": [],
        "action_quality_signals": [],
    }
    canonical_actions: dict[str, dict[str, Any]] = {}
    status_intents: list[dict[str, Any]] = []
    milestone_audit = {
        "milestones_seen": 0,
        "ledger_ready_milestones": 0,
        "blocked_milestones": [],
    }

    for item in items:
        if item["classification"] != "action":
            continue
        audit["actions_seen"] += 1
        source = f"{rel_to_memory(memory_root, meeting_path)}#{item['id']}"
        affected_workstreams = item["affected_workstreams"]
        is_patch = bool(item["action_id"] or item["action_operation"] == "patch")
        routing_workstreams = (
            affected_workstreams
            or (existing_action_routing(memory_root, item["action_id"]) if is_patch else [])
        )
        blocking_gaps = action_blocking_gaps(item, patch=is_patch)
        closure_criteria = item["closure_criteria"]
        closure_gap = (not is_patch) and (
            is_missing_closure_criteria(closure_criteria) or bool(item["closure_gap"])
        )
        if closure_gap:
            audit["closure_gap_count"] += 1
            closure_criteria = "TBD"

        for gap in blocking_gaps:
            if gap["type"] == "owner":
                audit["owner_gap_count"] += 1
            elif gap["type"] == "due":
                audit["due_gap_count"] += 1
            elif gap["type"] == "workstream":
                audit["workstream_gap_count"] += 1
        if blocking_gaps:
            audit["blocked_actions"].append(
                {
                    "id": item["id"],
                    "source": source,
                    "action": item["text"],
                    "gaps": [gap["message"] for gap in blocking_gaps],
                }
            )
            continue

        original_status = (
            normalize_action_status(item["status"])
            if not is_patch or "status" in item["action_field_presence"]
            else None
        )
        status = original_status
        reason_parts = [item["wdr_update"] or f"Meeting action from {meeting['title']}"]
        if affected_workstreams:
            reason_parts.append(f"Affected workstreams: {', '.join(affected_workstreams)}")
        if closure_gap:
            reason_parts.append(item["closure_gap"] or "Closure criteria is missing")

        due_date = parse_due_date(item["due"])
        past_due_needs_confirmation = (
            due_date is not None
            and due_date < today
            and original_status == "open"
            and not item["status_confirmation"]
        )
        if past_due_needs_confirmation:
            audit["past_due_open_count"] += 1
            reason_parts.append("Past due and needs status confirmation")
        if closure_gap or past_due_needs_confirmation:
            audit["status_review_required_count"] += 1
            audit["action_quality_signals"].append(
                {
                    "id": item["id"],
                    "source": source,
                    "supplied_status": original_status,
                    "closure_missing": closure_gap,
                    "due_date": due_date.isoformat() if due_date else None,
                    "overdue": bool(due_date and due_date < today),
                    "status_confirmation_missing": past_due_needs_confirmation,
                }
            )

        key = item["action_id"] if is_patch else canonical_action_key(source, item)
        action = canonical_actions.get(key)
        if action:
            if is_patch:
                raise ValueError(f"meeting contains duplicate patch for action {item['action_id']}")
            audit["duplicate_actions_merged"] += 1
            action["affected_workstreams"] = merge_values(action["affected_workstreams"], affected_workstreams)
            action["reason_parts"] = merge_values(action["reason_parts"], reason_parts)
            continue

        command_id = stable_command_id(meeting, item, "action")
        if is_patch:
            patch_payload: dict[str, Any] = {
                "operation": "patch",
                "command_id": command_id,
                "action_id": item["action_id"],
                "expected_action_revision": item["expected_action_revision"],
                "evidence": [meeting_evidence(meeting, source)],
            }
            field_map = {
                "status": status,
                "owner": item["owner"],
                "action": item["action_patch_text"],
                "due_or_trigger": item["due"],
                "closure_criteria": closure_criteria,
                "affected_workstreams": item["action_patch_affected_workstreams"],
            }
            for field_name in item["action_field_presence"]:
                patch_payload[field_name] = field_map[field_name]
            canonical_actions[key] = patch_payload
            canonical_actions[key]["_routing_workstreams"] = routing_workstreams
        else:
            canonical_actions[key] = {
                "operation": "create",
                "command_id": command_id,
                "action_id": stable_meeting_action_id(meeting, item),
                "owner": item["owner"],
                "action": item["text"],
                "source": source,
                "reason_parts": reason_parts,
                "due": item["due"],
                "status": status,
                "closure_criteria": closure_criteria or "TBD",
                "owning_workflow": "adp-meeting-sync",
                "affected_workstreams": affected_workstreams,
                "evidence": [meeting_evidence(meeting, source)],
                "_routing_workstreams": affected_workstreams,
            }

    updates_by_workstream: dict[str, dict[str, Any]] = {}
    for action in canonical_actions.values():
        routing_workstreams = action.pop("_routing_workstreams", action.get("affected_workstreams", []))
        action_workstream = canonical_action_workstream(routing_workstreams)
        if action["operation"] == "create":
            action["workstream"] = action_workstream
            action["reason"] = "; ".join(action.pop("reason_parts"))
        if len(routing_workstreams) > 1:
            audit["fanout_suppressed"] += len(routing_workstreams) - 1
        mutation_refreshes_wdr = len(routing_workstreams) == 1
        update = updates_by_workstream.setdefault(
            action_workstream,
            {
                "id": action_workstream,
                "source": "adp-meeting-sync",
                "next_actions": [],
                "refresh_actions": mutation_refreshes_wdr,
                "actions": [],
            },
        )
        if (
            action["operation"] == "create"
            and action_workstream not in {"program", "project", "adp-program"}
            and action["action"] not in update["next_actions"]
        ):
            update["next_actions"].append(action["action"])
        update["actions"].append(action)
        if not mutation_refreshes_wdr:
            for workstream_id in routing_workstreams:
                projection_update = updates_by_workstream.setdefault(
                    workstream_id,
                    {
                        "id": workstream_id,
                        "source": "adp-meeting-sync",
                        "next_actions": [],
                        "refresh_actions": True,
                        "actions": [],
                    },
                )
                projection_update["refresh_actions"] = True

    for item in items:
        for milestone in item["milestones"]:
            milestone_audit["milestones_seen"] += 1
            gaps = milestone_handoff_gaps(meeting, item, milestone)
            if gaps:
                milestone_audit["blocked_milestones"].append(
                    {"item_id": item["id"], "milestone_id": milestone.get("milestone_id"), "gaps": gaps}
                )
                continue
            workstream_id = item["affected_workstreams"][0]
            payload = dict(milestone)
            if payload.get("baseline_revision") in (None, ""):
                payload["baseline_revision"] = meeting.get("lineage", {}).get("baseline_revision")
            payload["source"] = f"{rel_to_memory(memory_root, meeting_path)}#{item['id']}"
            update = updates_by_workstream.setdefault(
                workstream_id,
                {"id": workstream_id, "source": "adp-meeting-sync", "next_actions": [], "actions": []},
            )
            update.setdefault("milestones", []).append(payload)
            milestone_audit["ledger_ready_milestones"] += 1

    for item in items:
        if not item["status_intent"]:
            continue
        for workstream_id in item["affected_workstreams"]:
            source = f"{rel_to_memory(memory_root, meeting_path)}#{item['id']}"
            intent = {
                "intent_id": stable_command_id(meeting, item, f"status-{workstream_id}"),
                "origin_producer": "adp-meeting-sync",
                "workstream_id": workstream_id,
                "set": dict(item["status_intent"]),
                "evidence": [meeting_evidence(meeting, source)],
            }
            status_intents.append(intent)
            update = updates_by_workstream.setdefault(
                workstream_id,
                {"id": workstream_id, "source": "adp-meeting-sync", "next_actions": [], "actions": []},
            )
            for field_name, value in intent["set"].items():
                if field_name in update and update[field_name] != value:
                    raise ValueError(
                        f"meeting status intents conflict for {workstream_id}.{field_name}"
                    )
                update[field_name] = value

    audit["canonical_actions"] = len(canonical_actions)
    audit["ledger_ready_actions"] = sum(len(update["actions"]) for update in updates_by_workstream.values())
    if not updates_by_workstream:
        return {}, audit, milestone_audit
    meeting_payload = {
        "meeting_instance_id": meeting["meeting_instance_id"],
        "plan_fingerprint": meeting["plan_fingerprint"],
        "date": meeting["date"],
        "started_at": meeting.get("started_at"),
        "ended_at": meeting.get("ended_at"),
        "title": meeting["title"],
        "source": meeting["source"],
        "archive": rel_to_memory(memory_root, meeting_path),
    }
    if meeting.get("lineage"):
        meeting_payload["lineage"] = dict(meeting["lineage"])
    return {
        "generated_by": "adp-meeting-sync",
        "schema_version": "2.0.0",
        "meeting": meeting_payload,
        "action_quality_audit": audit,
        "milestone_quality_audit": milestone_audit,
        "updates": list(updates_by_workstream.values()),
        "action_commands": sorted(canonical_actions.values(), key=lambda item: item["command_id"]),
        "status_intents": sorted(status_intents, key=lambda item: item["intent_id"]),
        "outbox": {
            "pending_intent_ids": sorted(item["intent_id"] for item in status_intents),
            "consumed_intent_ids": [],
        },
    }, audit, milestone_audit


def action_blocking_gaps(item: dict[str, Any], patch: bool = False) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    if not patch and not item["affected_workstreams"]:
        gaps.append({"type": "workstream", "message": "affected workstream is missing"})
    if not patch and (is_missing_owner(item["owner"]) or item["owner_gap"]):
        gaps.append({"type": "owner", "message": item["owner_gap"] or "action owner is missing"})
    if not patch and is_missing_due(item["due"]):
        gaps.append({"type": "due", "message": "action due trigger is missing"})
    return gaps


def canonical_action_key(source: str, item: dict[str, Any]) -> str:
    return "|".join(
        [
            normalize_text_key(source),
            normalize_text_key(item["text"]),
            normalize_text_key(item["owner"]),
            normalize_text_key(item["due"]),
            normalize_text_key(item["closure_criteria"]),
        ]
    )


def stable_command_id(meeting: dict[str, Any], item: dict[str, Any], purpose: str) -> str:
    digest = hashlib.sha256(
        f"{meeting['meeting_instance_id']}\0{item['id']}\0{purpose}".encode()
    ).hexdigest()[:24]
    return f"cmd-meeting-{digest}"


def stable_meeting_action_id(meeting: dict[str, Any], item: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "meeting_instance_id": meeting["meeting_instance_id"],
                "item_id": item["id"],
                "text": item["text"],
                "affected_workstreams": item["affected_workstreams"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16].upper()
    return f"ACT-MTG-{digest}"


def meeting_evidence(meeting: dict[str, Any], source: str) -> dict[str, str]:
    observed_at = meeting.get("ended_at") or meeting.get("started_at") or f"{meeting['date']}T23:59:59Z"
    parsed = parse_timestamp(observed_at)
    if parsed is None:
        raise ValueError("meeting evidence timestamp is invalid")
    return {
        "source": source,
        "observed_at": parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_fingerprint": meeting["plan_fingerprint"],
    }


def canonical_action_workstream(affected_workstreams: list[str]) -> str:
    if len(affected_workstreams) == 1:
        return affected_workstreams[0]
    if len(affected_workstreams) > 1:
        return "program"
    return "program"


def existing_action_routing(memory_root: Path, action_id: str) -> list[str]:
    ledger_path = memory_root / "actions/action-ledger.md"
    if not action_id or not ledger_path.is_file():
        return []
    lines = [line for line in ledger_path.read_text(encoding="utf-8-sig").splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return []
    headers = split_markdown_row(lines[0])
    matches: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = split_markdown_row(line)
        if len(cells) != len(headers) or all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        row = dict(zip(headers, cells, strict=True))
        if row.get("Action ID") == action_id:
            matches.append(row)
    if len(matches) > 1:
        raise MeetingSyncConflict(f"action ledger contains duplicate action ID {action_id}")
    if not matches:
        return []
    row = matches[0]
    raw_targets = [
        row.get("Workstream", ""),
        *re.split(r"\s*[,;]\s*", row.get("Affected Workstreams", "")),
    ]
    return sorted(
        {
            normalized
            for raw in raw_targets
            if (normalized := slugify(raw)) not in {"", "program", "project", "adp-program", "tbd"}
        }
    )


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def merge_values(existing: list[str], new_values: list[str]) -> list[str]:
    merged = list(existing)
    seen = {normalize_text_key(item) for item in merged}
    for value in new_values:
        key = normalize_text_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(value)
    return merged


def normalize_action_status(raw: Any) -> str:
    status = string_value(raw).lower().replace("_", "-")
    if status in {"open", "in-progress", "blocked", "done", "cancelled"}:
        return status
    if status in {"in progress", "inprogress"}:
        return "in-progress"
    raise ValueError("action status must be one of: blocked, cancelled, done, in-progress, open")


def meeting_receipt_path(memory_root: Path, meeting: dict[str, Any]) -> Path:
    return memory_root / "meetings" / "receipts" / f"{meeting['meeting_instance_id']}.json"


def planned_cursor_path(memory_root: Path, meeting: dict[str, Any]) -> str | None:
    scenario = string_value(meeting.get("lineage", {}).get("scenario"))
    if not scenario or not meeting.get("started_at") or not meeting.get("ended_at"):
        return None
    return str(memory_root / "meetings" / "cursors" / f"{slugify(scenario)}.json")


def advance_meeting_cursor(
    memory_root: Path,
    meeting: dict[str, Any],
    receipt: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    raw_path = planned_cursor_path(memory_root, meeting)
    if raw_path is None:
        return {"status": "not-applicable", "reason": "meeting has no scenario lineage with actual timestamps"}
    path = Path(raw_path)
    archive = string_value(receipt.get("archive"))
    if not archive:
        raise MeetingSyncConflict("applied receipt is missing its meeting archive")
    archive_path = memory_root / archive
    if not archive_path.is_file():
        raise MeetingSyncConflict(f"applied receipt archive is missing: {archive_path}")

    cursor = {
        "schema_version": 1,
        "scenario": meeting["lineage"]["scenario"],
        "meeting_instance_id": meeting["meeting_instance_id"],
        "meeting_date": meeting["date"],
        "started_at": meeting["started_at"],
        "ended_at": meeting["ended_at"],
        "archive": archive,
        "receipt": rel_to_memory(memory_root, meeting_receipt_path(memory_root, meeting)),
        "plan_fingerprint": meeting["plan_fingerprint"],
        "lineage": meeting["lineage"],
        "advanced_at": string_value(receipt.get("applied_at")),
        "generator_version": GENERATOR_VERSION,
    }
    existing = load_json_object(path)
    if existing:
        if existing.get("meeting_instance_id") == meeting["meeting_instance_id"]:
            if existing.get("plan_fingerprint") != meeting["plan_fingerprint"]:
                raise MeetingSyncConflict(f"scenario cursor points to the same meeting instance with a different plan: {path}")
            if existing == cursor:
                return {"status": "unchanged", "path": str(path), "meeting_instance_id": meeting["meeting_instance_id"]}
            if dry_run:
                return {"status": "planned-repair", "path": str(path), "meeting_instance_id": meeting["meeting_instance_id"]}
            write_json_atomic(path, cursor)
            return {"status": "repaired", "path": str(path), "meeting_instance_id": meeting["meeting_instance_id"]}
        existing_end = parse_timestamp(string_value(existing.get("ended_at")))
        new_end = parse_timestamp(meeting["ended_at"])
        if existing_end is None:
            raise MeetingSyncConflict(f"scenario cursor has an invalid ended_at timestamp: {path}")
        existing_key = (existing_end, string_value(existing.get("meeting_instance_id")))
        new_key = (new_end, meeting["meeting_instance_id"]) if new_end is not None else None
        if new_key is not None and existing_key > new_key:
            return {
                "status": "not-advanced",
                "reason": "an older meeting cannot move the scenario cursor backwards",
                "path": str(path),
                "meeting_instance_id": existing.get("meeting_instance_id"),
            }
    if dry_run:
        return {"status": "planned", "path": str(path), "meeting_instance_id": meeting["meeting_instance_id"]}
    write_json_atomic(path, cursor)
    return {"status": "advanced", "path": str(path), "meeting_instance_id": meeting["meeting_instance_id"]}


def replay_conflict_result(
    memory_root: Path,
    meeting: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "dry_run": False,
        "status": "conflict",
        "error": "meeting instance already exists with a different plan fingerprint",
        "memory_root": str(memory_root),
        "meeting": meeting,
        "receipt": str(receipt_path),
        "existing_plan_fingerprint": receipt.get("plan_fingerprint"),
        "incoming_plan_fingerprint": meeting["plan_fingerprint"],
    }


def status_sync_intake_path(memory_root: Path, meeting: dict[str, Any], dry_run: bool) -> Path:
    filename = f"{meeting['date']}-{slugify(meeting['title'])}-{meeting_instance_suffix(meeting)}-actions.json"
    return memory_root / "intake" / "status-sync" / filename


def write_file(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.rstrip() + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == normalized:
            return
        raise MeetingSyncConflict(f"destination already exists with different content: {path}")
    write_text_atomic(path, normalized)


def write_bytes_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content:
            return
        raise MeetingSyncConflict(f"destination already exists with different content: {path}")
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, content)


def append_pending_status_intents(
    path: Path,
    intents: list[dict[str, Any]],
    producer_receipt_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    current = load_json_object(path) if path.is_file() else {}
    existing_rows = current.get("intents", []) if isinstance(current.get("intents"), list) else []
    by_id = {
        str(row.get("intent_id")): dict(row)
        for row in existing_rows
        if isinstance(row, dict) and row.get("intent_id")
    }
    for intent in intents:
        intent_id = str(intent["intent_id"])
        payload_hash = fingerprint_json(intent)
        existing = by_id.get(intent_id)
        if existing:
            if existing.get("payload_hash") != payload_hash:
                raise MeetingSyncConflict(f"status intent {intent_id} already exists with different bytes")
            continue
        by_id[intent_id] = {
            "intent_id": intent_id,
            "state": "pending",
            "payload_hash": payload_hash,
            "producer": "adp-meeting-sync",
            "producer_receipt_id": producer_receipt_id,
            "intent": intent,
            "consumed_by": None,
            "consumed_at": None,
        }
    payload = {
        "schema_version": "1.0.0",
        "pending": sorted(key for key, row in by_id.items() if row.get("state") == "pending"),
        "consumed": sorted(key for key, row in by_id.items() if row.get("state") == "consumed"),
        "failed": [],
        "waived": [],
        "intents": [by_id[key] for key in sorted(by_id)],
    }
    payload["state_id"] = fingerprint_json(payload)
    if not dry_run:
        write_json_atomic(path, payload)
    return payload


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeetingSyncConflict(f"durable JSON state is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MeetingSyncConflict(f"durable JSON state must be an object: {path}")
    return payload


def append_file(path: Path, block: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_text_atomic(path, f"# {path.stem.replace('-', ' ').title()}\n\n")
    existing = path.read_text(encoding="utf-8").rstrip()
    first_line = block.splitlines()[0] if block.splitlines() else ""
    if first_line.startswith("<!-- adp-meeting-sync:") and first_line in existing:
        return
    write_text_atomic(path, existing + "\n\n" + block.rstrip() + "\n")


def unique_path(directory: Path, filename: str, dry_run: bool = False) -> Path:
    if not dry_run:
        directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def render_template(path: Path, values: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def bullet_list(raw_items: Any) -> str:
    if isinstance(raw_items, list):
        values = [string_value(item) for item in raw_items if string_value(item)]
    else:
        values = [string_value(raw_items)] if string_value(raw_items) else []
    return "\n".join(f"- {value}" for value in values) if values else "- TBD"


def rel_to_memory(memory_root: Path, path: Path) -> str:
    try:
        return path.relative_to(memory_root).as_posix()
    except ValueError:
        return path.as_posix()


def today_from_path(meeting_path: Path) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", meeting_path.name)
    return match.group(1) if match else "TBD"


def normalize_classification(raw: Any) -> str:
    value = string_value(raw).strip().lower().replace("-", "_").replace(" ", "_")
    return value


def normalize_workstreams(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [string_value(item) for item in raw]
    else:
        values = [string_value(raw)]
    return [slugify(value) for value in values if string_value(value)]


def normalize_people(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [part.strip() for part in re.split(r"[,;]", raw)]
    elif isinstance(raw, list):
        parts = [string_value(item) for item in raw]
    else:
        parts = [string_value(raw)]
    return [part for part in parts if part]


def normalize_gap_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [string_value(item) for item in raw]
    else:
        values = [string_value(raw)]
    return [value for value in values if value]


def normalize_milestone_updates(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    milestones: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            milestones.append({"milestone_id": "", "status": "", "forecast": "", "actual": "", "evidence": []})
            continue
        revision = value.get("baseline_revision")
        milestones.append(
            {
                "milestone_id": string_value(value.get("milestone_id") or value.get("id")),
                "status": string_value(value.get("status")).lower().replace("_", "-"),
                "forecast": string_value(value.get("forecast")),
                "actual": string_value(value.get("actual")),
                "evidence": normalize_gap_list(value.get("evidence", value.get("sources", value.get("source")))),
                "baseline_revision": revision if isinstance(revision, int) and not isinstance(revision, bool) else string_value(revision),
            }
        )
    return milestones


def milestone_handoff_gaps(
    meeting: dict[str, Any],
    item: dict[str, Any],
    milestone: dict[str, Any],
) -> list[str]:
    gaps: list[str] = []
    if len(item["affected_workstreams"]) != 1:
        gaps.append("milestone update requires exactly one affected workstream")
    if not milestone.get("milestone_id"):
        gaps.append("milestone_id is missing")
    if milestone.get("status") not in MILESTONE_STATUSES:
        gaps.append("milestone status is missing or invalid")
    if not milestone.get("evidence"):
        gaps.append("milestone evidence is missing")
    for field in ("forecast", "actual"):
        value = string_value(milestone.get(field))
        if value and parse_iso_date(value) is None:
            gaps.append(f"milestone {field} must use YYYY-MM-DD")
    explicit_revision = milestone.get("baseline_revision")
    revision = (
        meeting.get("lineage", {}).get("baseline_revision")
        if explicit_revision in (None, "")
        else explicit_revision
    )
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        gaps.append("milestone baseline_revision is missing or invalid")
    return gaps


def normalize_fingerprints(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        string_value(key): string_value(value)
        for key, value in sorted(raw.items(), key=lambda item: str(item[0]))
        if string_value(key) and string_value(value)
    }


def is_missing_due(value: str) -> bool:
    due = string_value(value)
    return not due or due == "TBD"


def is_missing_owner(value: str) -> bool:
    owner = string_value(value)
    return not owner or owner == "TBD"


def is_missing_closure_criteria(value: str) -> bool:
    criteria = string_value(value)
    return not criteria or criteria == "TBD"


def parse_due_date(value: str) -> date | None:
    due = string_value(value)
    match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", due)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_iso_date(value: str) -> date | None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def normalize_text_key(value: str) -> str:
    text = string_value(value).lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def fingerprint_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def meeting_instance_suffix(meeting: dict[str, Any]) -> str:
    return meeting["meeting_instance_id"].rsplit("-", 1)[-1]


def operation_marker(meeting: dict[str, Any], operation: str) -> str:
    operation_hash = hashlib.sha256(operation.encode("utf-8")).hexdigest()[:12]
    return f"<!-- adp-meeting-sync:{meeting['meeting_instance_id']}:{slugify(operation)}-{operation_hash} -->"


def render_metadata_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return string_value(value)


def default_status(classification: str) -> str:
    if classification == "no_op":
        return "no-op"
    return "open"


def slugify(value: str) -> str:
    value = string_value(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:80] or "item"


def cell(value: str) -> str:
    return string_value(value).replace("|", "\\|").replace("\n", " ")


def dedupe_touched(touched: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: sorted(set(values)) for key, values in touched.items()}


def next_actions(
    project_root: Path,
    memory_root: Path,
    items: list[dict[str, Any]],
    status_sync_intake_files: list[str],
    action_quality_audit: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    for path in status_sync_intake_files:
        actions.append(
            runtime_message(
                "meeting_sync.next.status_sync",
                project_root=project_root,
                memory_root=memory_root,
                path=path,
            )
        )
    if action_quality_audit.get("blocked_actions"):
        actions.append(runtime_message("meeting_sync.next.resolve_actions"))
    if action_quality_audit.get("status_review_required_count"):
        actions.append(runtime_message("meeting_sync.next.review_actions"))
    if has_missing_workstream_route(memory_root, items):
        actions.append(runtime_message("meeting_sync.next.workstream"))
    if any(item["classification"] == "business_decision_needed" for item in items):
        actions.append(runtime_message("meeting_sync.next.risk_review"))
    has_wdr_update = any(item["classification"] == "wdr_update" for item in items)
    has_ready_actions = bool(action_quality_audit.get("ledger_ready_actions"))
    if not status_sync_intake_files and (has_wdr_update or has_ready_actions):
        actions.append(runtime_message("meeting_sync.next.refresh_status"))
    if not actions:
        actions.append(runtime_message("meeting_sync.next.review_archive"))
    return actions


def has_missing_workstream_route(memory_root: Path, items: list[dict[str, Any]]) -> bool:
    for item in items:
        if item["classification"] in {"action", "wdr_update"} and not item["affected_workstreams"]:
            return True
        if should_append_wdr(item):
            for workstream_id in item["affected_workstreams"]:
                record_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
                if not record_path.exists():
                    return True
    return False


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


MEETING_SYSTEM_LINES = {
    "## Summary": "meeting_sync.summary",
    "## Closure Ledger": "meeting_sync.closure_ledger",
    "## Items": "meeting_sync.items",
    "## Meeting Rule": "meeting_sync.meeting_rule",
    "| ID | Classification | Workstreams | Owner | Due / Trigger | Destination | Gap |": "meeting_sync.closure_header",
    "Every item above must close into daily log, decision, action, WDR update, Business Decision Packet, or explicit no-op.": "meeting_sync.meeting_rule_text",
    "## Background": "meeting_sync.background",
    "## Decision Needed": "meeting_sync.decision_needed",
    "## Options": "meeting_sync.options",
    "## Recommendation": "meeting_sync.recommendation",
    "## Risks and Trade-offs": "meeting_sync.risks_tradeoffs",
    "## Final Decision": "meeting_sync.final_decision",
    "## Follow-up": "meeting_sync.follow_up",
    "- Update affected WDRs after confirmation.": "meeting_sync.follow_up_wdr",
    "- Update `decisions/decision-log.md` when the final decision is confirmed.": "meeting_sync.follow_up_decision",
}

MEETING_SYSTEM_PREFIXES = {
    "Date": "meeting_sync.date",
    "Type": "common.type",
    "Source": "common.source",
    "Raw evidence": "meeting_sync.raw_evidence",
    "Participants": "meeting_sync.participants",
    "Generated": "common.generated",
    "Created": "meeting_sync.created",
    "Source meeting": "meeting_sync.source_meeting",
    "Affected workstreams": "meeting_sync.affected_workstreams",
    "Status": "common.status",
    "Confirming owner": "meeting_sync.confirming_owner",
    "Deadline / trigger": "common.due_trigger",
    "- Affected workstreams": "meeting_sync.affected_workstreams_bullet",
    "- Owner": "meeting_sync.owner_bullet",
    "- Due / trigger": "meeting_sync.due_bullet",
    "- Decision type": "meeting_sync.decision_type_bullet",
    "- Confirmer": "meeting_sync.confirmer_bullet",
    "- Status": "meeting_sync.status_bullet",
    "- Destinations": "meeting_sync.destinations_bullet",
    "- WDR update": "meeting_sync.wdr_update_bullet",
    "- No-op reason": "meeting_sync.no_op_bullet",
    "- Gap": "meeting_sync.gap_bullet",
}

# These headings are canonical fact-layer structure consumed by replay, audit, and sync readers.
MEETING_CANONICAL_FACT_COPY = {
    "## Meeting Pack Lineage",
    "## Meeting Instance",
    "## Meeting Sync:",
    "## Meeting Decision:",
    "## Meeting Sync Update:",
    "# Decision Log",
    "| ID | Classification | Workstreams | Owner | Due / Trigger | Item |",
    "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
    "| Date | Type |",
}


def localize_system_copy(content: str) -> str:
    locale = str(LANGUAGE_CONTEXT.get("locale") or "en")
    module = LANGUAGE_CONTEXT.get("module")
    if locale == "en" or module is None:
        return content
    lines: list[str] = []
    for line in content.splitlines():
        key = MEETING_SYSTEM_LINES.get(line)
        if key:
            lines.append(module.message(key, locale))
            continue
        replaced = False
        for prefix, prefix_key in MEETING_SYSTEM_PREFIXES.items():
            marker = prefix + ":"
            if line.startswith(marker):
                lines.append(module.message(prefix_key, locale) + ":" + line[len(marker):])
                replaced = True
                break
        if not replaced:
            lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def runtime_message(key: str, **values: Any) -> str:
    locale = str(LANGUAGE_CONTEXT.get("locale") or "en")
    module = LANGUAGE_CONTEXT.get("module")
    return module.message(key, locale, **values) if module is not None else key


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path.resolve())
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load shared ADP config module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def language_metadata(config: dict[str, Any], locale: str) -> dict[str, Any]:
    return {
        "locale": locale,
        "document_output_language": config.get("values", {}).get("document_output_language", "English"),
        "fallback": "document_output_language" in config.get("fallbacks", []),
        "warnings": config.get("warnings", []),
    }


if __name__ == "__main__":
    sys.exit(main())
