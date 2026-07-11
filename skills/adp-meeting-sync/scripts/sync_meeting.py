#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Apply an ADP meeting sync JSON plan to shared project memory."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]

CLASSIFICATIONS = {
    "fact",
    "decision",
    "action",
    "wdr_update",
    "business_decision_needed",
    "no_op",
}

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
)

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

    try:
        plan = load_plan(args.plan)
        normalized = normalize_plan(plan)
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

    result = apply_plan(project_root, memory_root, normalized, templates, args.dry_run)
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
        item["affected_workstreams"] = normalize_workstreams(item.get("affected_workstreams"))
        item["owner"] = string_value(item.get("owner")) or "TBD"
        item["due"] = string_value(item.get("due")) or string_value(item.get("trigger")) or "TBD"
        item["decision_type"] = (
            string_value(item.get("decision_type"))
            or DECISION_TYPE_DEFAULTS.get(item["classification"], "TBD")
        )
        item["confirmer"] = string_value(item.get("confirmer")) or "TBD"
        item["status"] = string_value(item.get("status")) or default_status(item["classification"])
        item["wdr_update"] = string_value(item.get("wdr_update"))
        item["no_op_reason"] = string_value(item.get("no_op_reason"))
        item["closure_criteria"] = string_value(item.get("closure_criteria"))
        item["status_confirmation"] = string_value(item.get("status_confirmation"))
        item["owner_gap"] = string_value(item.get("owner_gap"))
        item["confirmer_gap"] = string_value(item.get("confirmer_gap"))
        item["speaker_label_gap"] = string_value(item.get("speaker_label_gap"))
        item["gap"] = string_value(item.get("gap"))
        item["packet"] = item.get("packet") if isinstance(item.get("packet"), dict) else {}
        normalized_items.append(item)

    raw_lineage = meeting.get("lineage") if isinstance(meeting.get("lineage"), dict) else {}
    lineage = {
        field: string_value(raw_lineage.get(field) or meeting.get(field))
        for field in MEETING_LINEAGE_FIELDS
    }
    if not any(lineage.values()):
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
            "lineage": lineage,
        },
        "items": normalized_items,
    }


def validate_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    meeting = plan["meeting"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting["date"]):
        errors.append("meeting.date must use YYYY-MM-DD")
    lineage = meeting.get("lineage", {})
    if lineage:
        missing_lineage = [field for field in MEETING_LINEAGE_FIELDS if not lineage.get(field)]
        if missing_lineage:
            errors.append("meeting.lineage is missing: " + ", ".join(missing_lineage))

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
    return errors


def apply_plan(
    project_root: Path,
    memory_root: Path,
    plan: dict[str, Any],
    templates: dict[str, Path],
    dry_run: bool,
) -> dict[str, Any]:
    meeting = plan["meeting"]
    items = plan["items"]
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
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
    }
    unresolved_gaps: list[str] = []

    meeting_path = unique_path(
        memory_root / "meetings",
        f"{meeting['date']}-{slugify(meeting['type'])}-{slugify(meeting['title'])}.md",
        dry_run,
    )
    raw_evidence_path, raw_evidence_gap = copy_raw_evidence(project_root, memory_root, meeting, dry_run)
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

    status_sync_intake, action_quality_audit = build_status_sync_intake(memory_root, meeting_path, meeting, items)
    if status_sync_intake:
        intake_path = status_sync_intake_path(memory_root, meeting, dry_run)
        write_file(
            intake_path,
            json.dumps(status_sync_intake, ensure_ascii=False, indent=2),
            dry_run,
        )
        touched["status_sync_intake_files"].append(str(intake_path))

    return {
        "ok": True,
        "dry_run": dry_run,
        "memory_root": str(memory_root),
        "meeting": meeting,
        "touched": dedupe_touched(touched),
        "unresolved_gaps": sorted(set(unresolved_gaps)),
        "action_quality_audit": action_quality_audit,
        "next_actions": next_actions(
            project_root,
            memory_root,
            items,
            touched["status_sync_intake_files"],
            action_quality_audit,
        ),
    }


def ensure_directories(memory_root: Path, dry_run: bool) -> None:
    for rel in [
        "meetings",
        "meetings/raw",
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
        f"{slugify(meeting['raw_evidence_label'])}{source.suffix or '.txt'}"
    )
    target = unique_path(memory_root / "meetings" / "raw", target_name, dry_run)
    if not dry_run:
        target.write_bytes(source.read_bytes())
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
        gap = item_gap(item)
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


def item_gap(item: dict[str, Any]) -> str:
    gaps: list[str] = []
    if item["classification"] in {"action", "wdr_update"} and not item["affected_workstreams"]:
        gaps.append("affected workstream is missing")
    if item["classification"] == "action":
        if is_generic_owner(item["owner"]):
            gaps.append("action owner is missing or generic")
        if is_missing_due(item["due"]):
            gaps.append("action due trigger is missing")
        if is_generic_closure_criteria(item["closure_criteria"]):
            gaps.append("action closure criteria is missing or not verifiable")
    if item["classification"] in {"decision", "business_decision_needed"} and item["confirmer"] == "TBD":
        gaps.append("confirmer is missing")
    if item["classification"] == "wdr_update" and not item["wdr_update"]:
        gaps.append("wdr_update text is missing")
    gaps.extend(
        gap
        for gap in [
            item["owner_gap"],
            item["confirmer_gap"],
            item["speaker_label_gap"],
            item["gap"],
        ]
        if gap
    )
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
        f"- {field.replace('_', ' ').title()}: `{value}`"
        for field, value in meeting.get("lineage", {}).items()
    ]
    return "\n".join(
        [
            f"## Meeting Sync: {meeting['title']}",
            "",
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
            *[f"- {field}: `{lineage[field]}`" for field in MEETING_LINEAGE_FIELDS],
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
        path.write_text(default_decision_log() + "\n", encoding="utf-8", newline="\n")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    lines = [line for line in lines if not is_placeholder_decision_row(line)]
    insert_at = find_decision_table_insert_index(lines)
    for row in reversed(rows):
        lines.insert(insert_at, row)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


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
    write_file(packet_path, content, dry_run)
    return packet_path


def business_packet_path(
    memory_root: Path,
    meeting: dict[str, Any],
    item: dict[str, Any],
    dry_run: bool,
) -> Path:
    title = decision_text(item)
    filename = f"{meeting['date']}-{slugify(item['id'])}-{slugify(title)}.md"
    return unique_path(memory_root / "decisions" / "business-decision-packets", filename, dry_run)


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
) -> tuple[dict[str, Any], dict[str, Any]]:
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
        "status_calibrated_count": 0,
        "blocked_actions": [],
        "status_calibrations": [],
    }
    canonical_actions: dict[str, dict[str, Any]] = {}

    for item in items:
        if item["classification"] != "action":
            continue
        audit["actions_seen"] += 1
        source = f"{rel_to_memory(memory_root, meeting_path)}#{item['id']}"
        affected_workstreams = item["affected_workstreams"]
        blocking_gaps = action_blocking_gaps(item)
        closure_criteria = item["closure_criteria"]
        closure_gap = is_generic_closure_criteria(closure_criteria)
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

        original_status = normalize_action_status(item["status"])
        status = original_status
        reason_parts = [item["wdr_update"] or f"Meeting action from {meeting['title']}"]
        if affected_workstreams:
            reason_parts.append(f"Affected workstreams: {', '.join(affected_workstreams)}")
        if closure_gap:
            reason_parts.append("Closure criteria is missing or not verifiable")
            if status == "open":
                status = "blocked"
                audit["status_calibrated_count"] += 1
                audit["status_calibrations"].append(
                    {"id": item["id"], "from": "open", "to": "blocked", "reason": "closure criteria gap"}
                )

        due_date = parse_due_date(item["due"])
        past_due_needs_confirmation = (
            due_date is not None
            and due_date < date.today()
            and original_status == "open"
            and not item["status_confirmation"]
        )
        if past_due_needs_confirmation:
            audit["past_due_open_count"] += 1
            reason_parts.append("Past due and needs status confirmation")
            if status == "open":
                audit["status_calibrated_count"] += 1
                status = "blocked"
                audit["status_calibrations"].append(
                    {"id": item["id"], "from": "open", "to": "blocked", "reason": "past due without status confirmation"}
                )

        key = canonical_action_key(source, item)
        action = canonical_actions.get(key)
        if action:
            audit["duplicate_actions_merged"] += 1
            action["affected_workstreams"] = merge_values(action["affected_workstreams"], affected_workstreams)
            action["reason_parts"] = merge_values(action["reason_parts"], reason_parts)
            continue

        canonical_actions[key] = {
            "owner": item["owner"],
            "action": item["text"],
            "source": source,
            "reason_parts": reason_parts,
            "due": item["due"],
            "status": status,
            "closure_criteria": closure_criteria or "TBD",
            "owning_workflow": "adp-meeting-sync",
            "affected_workstreams": affected_workstreams,
        }

    updates_by_workstream: dict[str, dict[str, Any]] = {}
    for action in canonical_actions.values():
        affected_workstreams = action["affected_workstreams"]
        action_workstream = canonical_action_workstream(affected_workstreams)
        action["workstream"] = action_workstream
        action["reason"] = "; ".join(action.pop("reason_parts"))
        if len(affected_workstreams) > 1:
            audit["fanout_suppressed"] += len(affected_workstreams) - 1
        update = updates_by_workstream.setdefault(
            action_workstream,
            {
                "id": action_workstream,
                "source": "adp-meeting-sync",
                "next_actions": [],
                "actions": [],
            },
        )
        if action_workstream not in {"program", "project", "adp-program"}:
            if action["action"] not in update["next_actions"]:
                update["next_actions"].append(action["action"])
        update["actions"].append(action)

    audit["canonical_actions"] = len(canonical_actions)
    audit["ledger_ready_actions"] = sum(len(update["actions"]) for update in updates_by_workstream.values())
    if not updates_by_workstream:
        return {}, audit
    meeting_payload = {
        "date": meeting["date"],
        "title": meeting["title"],
        "source": meeting["source"],
        "archive": rel_to_memory(memory_root, meeting_path),
    }
    if meeting.get("lineage"):
        meeting_payload["lineage"] = dict(meeting["lineage"])
    return {
        "generated_by": "adp-meeting-sync",
        "meeting": meeting_payload,
        "action_quality_audit": audit,
        "updates": list(updates_by_workstream.values()),
    }, audit


def action_blocking_gaps(item: dict[str, Any]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    if not item["affected_workstreams"]:
        gaps.append({"type": "workstream", "message": "affected workstream is missing"})
    if is_generic_owner(item["owner"]) or item["owner_gap"]:
        gaps.append({"type": "owner", "message": item["owner_gap"] or "action owner is missing or generic"})
    if is_missing_due(item["due"]):
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


def canonical_action_workstream(affected_workstreams: list[str]) -> str:
    if len(affected_workstreams) == 1:
        return affected_workstreams[0]
    if len(affected_workstreams) > 1:
        return "program"
    return "TBD"


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
    return "open"


def status_sync_intake_path(memory_root: Path, meeting: dict[str, Any], dry_run: bool) -> Path:
    filename = f"{meeting['date']}-{slugify(meeting['title'])}-actions.json"
    return unique_path(memory_root / "intake" / "status-sync", filename, dry_run)


def write_file(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def append_file(path: Path, block: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {path.stem.replace('-', ' ').title()}\n\n", encoding="utf-8", newline="\n")
    existing = path.read_text(encoding="utf-8").rstrip()
    path.write_text(existing + "\n\n" + block.rstrip() + "\n", encoding="utf-8", newline="\n")


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


def is_missing_due(value: str) -> bool:
    due = string_value(value)
    return not due or due == "TBD"


def is_missing_owner(value: str) -> bool:
    owner = string_value(value)
    return not owner or owner == "TBD"


def is_generic_owner(value: str) -> bool:
    owner = normalize_text_key(value)
    if not owner or owner in {"tbd", "owner", "participants", "meeting participants", "all participants"}:
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
    return any(phrase in owner for phrase in generic_phrases)


def is_generic_closure_criteria(value: str) -> bool:
    criteria = normalize_text_key(value)
    if not criteria or criteria == "tbd":
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
    return any(phrase in criteria for phrase in generic_phrases)


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
        actions.append(f'Run adp-status-sync update "{project_root}" --updates-file "{path}" to register meeting actions.')
    if action_quality_audit.get("blocked_actions"):
        actions.append("Resolve blocked meeting action gaps before ledger registration: owner, workstream, and due must be specific.")
    if action_quality_audit.get("status_calibrated_count"):
        actions.append("Review blocked past-due or weak-closure meeting actions and confirm whether they are done, cancelled, or still active.")
    if has_missing_workstream_route(memory_root, items):
        actions.append("Run adp-workstream-register or correct workstream ids for missing WDR references.")
    if any(item["classification"] == "business_decision_needed" for item in items):
        actions.append("Run adp-risk-dependency-change-review for open business decision packets.")
    has_wdr_update = any(item["classification"] == "wdr_update" for item in items)
    has_ready_actions = bool(action_quality_audit.get("ledger_ready_actions"))
    if not status_sync_intake_files and (has_wdr_update or has_ready_actions):
        actions.append("Run adp-status-sync to refresh recurring status views from the new meeting updates.")
    if not actions:
        actions.append("Review the meeting archive and continue with the next ADP workflow only if gaps remain.")
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


if __name__ == "__main__":
    sys.exit(main())
