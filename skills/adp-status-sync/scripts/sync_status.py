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
        default="_bmad/memory/adp",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad/memory/adp.",
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
        default="_bmad/memory/adp",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad/memory/adp.",
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
        source=str(item.get("source") or default_source),
    )


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


def apply_update(memory_root: Path, update: StatusUpdate, dry_run: bool) -> dict[str, Any]:
    record_path = memory_root / "workstreams" / update.workstream_id / "delivery-record.md"
    if not record_path.exists():
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
            "action_candidates": [],
            "unresolved_gaps": unresolved_gaps(update),
            "no_op": True,
        }

    values = update_values(update, timestamp)
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
        "action_candidates": update.next_actions,
        "unresolved_gaps": unresolved_gaps(update),
    }


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
    daily_path.write_text(content + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return daily_path


def extend_items(lines: list[str], label: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"- {label}:")
    lines.extend(f"  - {item}" for item in items)


def unresolved_gaps(update: StatusUpdate) -> list[str]:
    gaps: list[str] = []
    if not any([update.status, update.progress, update.blockers, update.risks, update.dependencies, update.next_actions]):
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
    results = [apply_update(memory_root, update, args.dry_run) for update in updates]
    errors = [item for item in results if not item.get("ok")]
    payload = {
        "ok": not errors,
        "mode": "update",
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "updates": results,
    }
    emit(payload, args.output)
    return 0 if not errors else 1


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
