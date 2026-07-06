#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Apply lightweight ADP status updates to Workstream Delivery Records."""

from __future__ import annotations

import argparse
import json
import re
import sys
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
ACTION_STATUSES = {"open", "in-progress", "blocked", "done", "cancelled"}
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
PROJECT_ACTION_IDS = {"program", "project", "adp-program"}
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
            ]
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

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


def updates_from_args(args: argparse.Namespace) -> list[StatusUpdate]:
    updates: list[StatusUpdate] = []
    if args.updates_file:
        payload = json.loads(Path(args.updates_file).read_text(encoding="utf-8"))
        items = payload.get("updates", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValueError("updates-file must contain a list or an object with an 'updates' list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each batch update must be a JSON object")
            updates.append(update_from_mapping(item, default_source=args.source))

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
                source=args.source,
            )
        )

    if not updates:
        raise ValueError("provide --id with status fields or --updates-file")
    return updates


def update_from_mapping(item: dict[str, Any], default_source: str) -> StatusUpdate:
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
        actions=actions_from_mapping(item, default_workstream=normalize_id(str(raw_id)), default_source=default_source),
        source=str(item.get("source") or default_source),
    )


def actions_from_mapping(
    item: dict[str, Any],
    default_workstream: str,
    default_source: str,
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
            )
        )
    return actions


def normalize_action_status(raw: Any) -> str:
    status = clean_optional(raw)
    if not status:
        return "open"
    normalized = status.lower().strip().replace("_", "-")
    if normalized in {"in progress", "inprogress"}:
        normalized = "in-progress"
    return normalized if normalized in ACTION_STATUSES else "open"


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
    if action_update.action_id:
        for row in rows:
            if row.get("Action ID") == action_update.action_id:
                return row
    strong_key = action_key(action_update.owner, action_update.workstream, action_update.action, action_update.source)
    if action_update.action and action_update.source:
        for row in rows:
            if action_key(row.get("Owner", ""), row.get("Workstream", ""), row.get("Action", ""), row.get("Source", "")) == strong_key:
                return row
        for row in rows:
            if row.get("Status", "").lower() not in ACTIVE_ACTION_STATUSES:
                continue
            if normalize_text_key(row.get("Action", "")) != normalize_text_key(action_update.action):
                continue
            if normalize_text_key(row.get("Source", "")) != normalize_text_key(action_update.source):
                continue
            if normalize_text_key(row.get("Owner", "")) != normalize_text_key(action_update.owner):
                continue
            if normalize_text_key(row.get("Due / Trigger", "")) != normalize_text_key(action_update.due_or_trigger):
                continue
            return row
    weak_key = action_key(action_update.owner, action_update.workstream, action_update.action, "")
    if action_update.action:
        for row in rows:
            if row.get("Status", "").lower() not in ACTIVE_ACTION_STATUSES:
                continue
            if action_key(row.get("Owner", ""), row.get("Workstream", ""), row.get("Action", ""), "") == weak_key:
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
    row["Last Updated"] = timestamp
    assign_if_meaningful(row, "Owning Workflow", action_update.owning_workflow)


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
    if is_generic_owner(row.get("Owner", "")):
        gaps.append(f"{action_id}: Owner is missing or generic")
    if is_missing_workstream(row.get("Workstream", "")) and not parse_workstream_cell(row.get("Affected Workstreams", "")):
        gaps.append(f"{action_id}: Workstream is missing")
    if is_missing_action_value(row.get("Due / Trigger", "")):
        gaps.append(f"{action_id}: Due / Trigger is missing")
    if is_generic_closure_criteria(row.get("Closure Criteria", "")):
        gaps.append(f"{action_id}: Closure Criteria is missing or not verifiable")
    return gaps


def is_missing_action_value(value: str) -> bool:
    text = str(value or "").strip()
    return not text or text.upper() == "TBD"


def is_missing_workstream(value: str) -> bool:
    text = str(value or "").strip()
    return not text or text.upper() == "TBD"


def is_generic_owner(value: str) -> bool:
    text = normalize_text_key(value)
    if not text or text in {"tbd", "owner", "participants", "meeting participants", "all participants"}:
        return True
    generic_phrases = [
        "each workstream",
        "fde owner",
        "workstream fde owner",
        "all fdes",
        "attendees",
        "各条线",
        "各线",
        "参会人员",
        "负责人",
        "待定",
    ]
    return any(phrase in text for phrase in generic_phrases)


def is_generic_closure_criteria(value: str) -> bool:
    text = normalize_text_key(value)
    if not text or text == "tbd":
        return True
    generic_phrases = [
        "update completion status",
        "updates completion status",
        "wdr daily log or status sync",
        "wdr daily log status sync",
        "status sync update",
        "更新完成状态",
        "后续 status sync",
        "对应 wdr",
        "daily log",
    ]
    return any(phrase in text for phrase in generic_phrases)


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
        owner = row.get("Owner", "").strip()
        due = row.get("Due / Trigger", "").strip()
        summary = action
        if owner and owner.upper() != "TBD" and normalize_text_key(owner) not in normalize_text_key(summary):
            summary = f"{owner}: {summary}"
        if due and due.upper() != "TBD" and normalize_text_key(due) not in normalize_text_key(summary):
            summary = f"{summary} (due: {due})"
        summaries.append(summary)
    return summaries


def safe_normalize_id(value: str) -> str:
    try:
        return normalize_id(value)
    except ValueError:
        return ""


def remove_closed_action_summaries(existing_actions: list[str], action_updates: list[ActionUpdate]) -> list[str]:
    closed_actions = [
        action.action
        for action in action_updates
        if action.status in {"done", "cancelled"} and action.action
    ]
    if not closed_actions:
        return existing_actions
    kept: list[str] = []
    for summary in existing_actions:
        if any(action_summary_matches(action, summary) for action in closed_actions):
            continue
        kept.append(summary)
    return kept


def action_summary_matches(action: str, summary: str) -> bool:
    action_key = normalize_text_key(action)
    summary_key = normalize_text_key(summary)
    return bool(action_key and (action_key in summary_key or summary_key in action_key))


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


def apply_update(
    memory_root: Path,
    update: StatusUpdate,
    ledger_actions: list[dict[str, str]],
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
    if update.next_actions or active_summaries or update.actions:
        existing_actions = split_next_actions(existing_field_value(original, "Project Status", "Next actions"))
        existing_actions = remove_closed_action_summaries(existing_actions, update.actions)
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

    daily_log = append_daily_log(memory_root, update, changed_fields, timestamp, dry_run)
    if changed_fields and not dry_run:
        record_path.write_text(updated, encoding="utf-8", newline="\n")

    return {
        "ok": True,
        "workstream_id": update.workstream_id,
        "record": str(record_path),
        "daily_log": str(daily_log),
        "changed_fields": changed_fields,
        "action_candidates": active_summaries or update.next_actions,
        "actions_registered": [],
        "actions_updated": [],
        "actions_closed": [],
        "unresolved_gaps": unresolved_gaps(update),
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
    daily_path.write_text(content + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return daily_path


def extend_items(lines: list[str], label: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"- {label}:")
    lines.extend(f"  - {item}" for item in items)


def unresolved_gaps(update: StatusUpdate) -> list[str]:
    gaps: list[str] = []
    if not any([update.status, update.progress, update.blockers, update.risks, update.dependencies, update.next_actions, update.actions]):
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


def run_update(args: argparse.Namespace) -> int:
    project_root = require_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    updates = updates_from_args(args)
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    action_updates = [action for update in updates for action in update.actions]
    ledger_path = memory_root / ACTION_LEDGER_REL
    if action_updates:
        ledger_path = ensure_action_ledger(memory_root, args.dry_run)
        ledger_result = upsert_actions(ledger_path, action_updates, timestamp, args.dry_run)
    else:
        ledger_result = {
            "rows": parse_action_ledger(ledger_path),
            "actions_registered": [],
            "actions_updated": [],
            "actions_closed": [],
            "unresolved_gaps": [],
        }
    ledger_rows = ledger_result["rows"]
    hydrate_action_updates_from_ledger(updates, ledger_rows)
    results = [apply_update(memory_root, update, ledger_rows, args.dry_run) for update in updates]
    for result, update in zip(results, updates, strict=True):
        update_action_ids = {action.action_id for action in update.actions if action.action_id}
        update_keys = {
            action_key(action.owner, action.workstream, action.action, action.source)
            for action in update.actions
        }
        related_registered = related_action_ids(ledger_rows, ledger_result["actions_registered"], update_action_ids, update_keys)
        related_updated = related_action_ids(ledger_rows, ledger_result["actions_updated"], update_action_ids, update_keys)
        related_closed = related_action_ids(ledger_rows, ledger_result["actions_closed"], update_action_ids, update_keys)
        result["actions_registered"] = related_registered
        result["actions_updated"] = related_updated
        result["actions_closed"] = related_closed
        result["unresolved_gaps"] = sorted(set([*result.get("unresolved_gaps", []), *ledger_result["unresolved_gaps"]]))
    errors = [item for item in results if not item.get("ok")]
    payload = {
        "ok": not errors,
        "mode": "update",
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "action_ledger": str(ledger_path),
        "actions_registered": ledger_result["actions_registered"],
        "actions_updated": ledger_result["actions_updated"],
        "actions_closed": ledger_result["actions_closed"],
        "unresolved_gaps": ledger_result["unresolved_gaps"],
        "updates": results,
    }
    emit(payload, args.output)
    return 0 if not errors else 1


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
        if args.command == "update":
            return run_update(args)
        if args.command == "stale":
            return run_stale(args)
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        emit(payload, getattr(args, "output", None))
        return 2


if __name__ == "__main__":
    sys.exit(main())
