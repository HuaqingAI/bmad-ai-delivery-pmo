#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Render ADP meeting packs from state audit and prepass JSON."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_ROOT.parent
SKILLS_ROOT = SKILL_ROOT.parent
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
DEFAULT_MEETING_PACK_OUTPUT_PATH = "{memory_root}/views/meeting-packs"
DEFAULT_RUN_FOLDER_PATTERN = "{scenario}"
DEFAULT_PREPASS_SCRIPT = SKILLS_ROOT / "adp-agent-program-lead" / "scripts" / "adp-state-prepass.py"
DEFAULT_AUDIT_SCRIPT = SKILLS_ROOT / "adp-state-audit" / "scripts" / "audit_state.py"
SCENARIO_CAPABILITIES = {
    "fde-morning": "FDE Action List",
    "business-biweekly": "Global Project Readout",
}
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
PLACEHOLDERS = {"", "-", "tbd", "todo", "none", "n/a", "na", "unknown", "null"}
READINESS_FIELDS = {"readiness", "evidence", "l0_references"}
READINESS_VIEW_PATHS = {"views/acceptance-readiness.md", "views/cutover-readiness.md"}
BUSINESS_DECISION_CLOSED_STATUSES = {
    "accepted",
    "cancelled",
    "closed",
    "done",
    "rejected",
    "superseded",
}
ROADMAP_DATE_FIELDS = ["planned", "forecast", "actual"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a Markdown ADP meeting pack from audit and prepass state.",
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIO_CAPABILITIES),
        default="fde-morning",
        help="Meeting scenario to render. Default: fde-morning.",
    )
    parser.add_argument("--date", help="Pack date, YYYY-MM-DD. Default: today.")
    parser.add_argument("--workstream", action="append", default=[], help="Workstream id to include. Repeatable.")
    parser.add_argument(
        "--memory-root",
        default=DEFAULT_MEMORY_ROOT,
        help=f"ADP state root, relative to project root unless absolute. Default: {DEFAULT_MEMORY_ROOT}.",
    )
    parser.add_argument("--audit", help="Existing audit JSON to consume.")
    parser.add_argument("--prepass-json", help="Existing prepass JSON to consume.")
    parser.add_argument("--prepass-script", default=str(DEFAULT_PREPASS_SCRIPT), help="Path to adp-state-prepass.py.")
    parser.add_argument("--audit-script", default=str(DEFAULT_AUDIT_SCRIPT), help="Path to audit_state.py.")
    parser.add_argument("--max-age-days", type=int, default=7, help="Freshness threshold in days. Default: 7.")
    parser.add_argument(
        "--meeting-pack-output-path",
        default=DEFAULT_MEETING_PACK_OUTPUT_PATH,
        help=f"Base output path. Tokens: {{project_root}}, {{memory_root}}, {{scenario}}, {{date}}. Default: {DEFAULT_MEETING_PACK_OUTPUT_PATH}.",
    )
    parser.add_argument(
        "--run-folder-pattern",
        default=DEFAULT_RUN_FOLDER_PATTERN,
        help=f"Folder pattern below meeting-pack-output-path. Tokens: {{scenario}}, {{date}}. Default: {DEFAULT_RUN_FOLDER_PATTERN}.",
    )
    parser.add_argument("--output-dir", help="One-run meeting pack output directory override.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing Markdown/JSON pack pair at the planned destination.")
    parser.add_argument("--audit-output-dir", help="Audit output directory. Default: <memory-root>/audits.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON run result to this file instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        result = {"ok": False, "status": "error", "error": str(exc)}
    emit(result, args.output)
    return 0 if result.get("ok") else 1


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return {"ok": False, "status": "error", "reason": "project_root is not an existing directory", "project_root": str(project_root)}

    pack_date = date.fromisoformat(args.date) if args.date else date.today()
    memory_root = resolve_memory_root(project_root, args.memory_root)
    if not memory_root.exists() or not memory_root.is_dir():
        return {
            "ok": False,
            "status": "blocked",
            "reason": "ADP memory root is missing",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflows": ["adp-project-kickoff"],
        }

    output_dir = resolve_output_dir(
        args.output_dir,
        args.meeting_pack_output_path,
        args.run_folder_pattern,
        project_root,
        memory_root,
        args.scenario,
        pack_date,
    )
    markdown_path = output_dir / f"{pack_date.isoformat()}.md"
    distillate_path = markdown_path.with_suffix(".json")
    collisions = [str(path) for path in (markdown_path, distillate_path) if path.exists()]
    if collisions and not args.replace:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "meeting pack output collision",
            "scenario": args.scenario,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "planned_outputs": {
                "markdown": str(markdown_path),
                "distillate": str(distillate_path),
            },
            "collisions": collisions,
            "recovery": {
                "replace": "rerun with --replace",
                "new_destination": "rerun with --output-dir <unique-directory>",
            },
        }

    prepass = load_or_run_prepass(args, project_root, memory_root, pack_date)
    if not prepass.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "reason": prepass.get("error", "prepass failed"),
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflows": [prepass.get("recommended_workflow") or "adp-project-kickoff"],
        }

    audit, audit_run = load_or_run_audit(args, project_root, memory_root, pack_date, prepass)
    if not audit.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "reason": audit.get("error", "audit failed"),
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflows": audit.get("recommended_workflows", ["adp-state-audit"]),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    render_context = build_context(args.scenario, pack_date, project_root, memory_root, prepass, audit, audit_run)
    recommended_workflows = next_workflows(audit)
    markdown_path.write_text(render_markdown(render_context), encoding="utf-8", newline="\n")
    distillate_path.write_text(
        json.dumps(build_distillate(render_context, markdown_path, distillate_path, recommended_workflows), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return {
        "ok": True,
        "status": "complete",
        "scenario": args.scenario,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "audit_status": audit.get("audit_status", ""),
        "outputs": {
            "markdown": str(markdown_path),
            "distillate": str(distillate_path),
            "audit": render_context["audit_path"],
        },
        "counts": {
            "sources_read": len(prepass.get("sources_read", [])),
            "workstreams": len(prepass.get("workstreams", [])),
            "actions_in_board": render_context["action_board_count"],
            "actions_excluded": len(render_context["excluded_actions"]),
            "red_amber_items": len(render_context["red_items"]) + len(render_context["amber_items"]),
            "business_decisions": len(render_context.get("business", {}).get("decision_items", [])),
            "roadmap_timeline_items": len(render_context.get("business", {}).get("roadmap_timeline_items", [])),
            "roadmap_unscheduled_items": len(render_context.get("business", {}).get("roadmap_unscheduled_items", [])),
        },
        "recommended_workflows": recommended_workflows,
    }


def load_or_run_prepass(args: argparse.Namespace, project_root: Path, memory_root: Path, pack_date: date) -> dict[str, Any]:
    if args.prepass_json:
        return load_json(resolve_project_path(project_root, args.prepass_json))

    prepass_script = Path(args.prepass_script).resolve()
    if not prepass_script.exists():
        return {"ok": False, "error": f"prepass script not found: {prepass_script}"}

    command = [
        sys.executable,
        str(prepass_script),
        str(project_root),
        "--capability",
        SCENARIO_CAPABILITIES[args.scenario],
        "--memory-root",
        str(memory_root),
        "--as-of",
        pack_date.isoformat(),
        "--max-age-days",
        str(args.max_age_days),
    ]
    for workstream_id in args.workstream:
        command.extend(["--workstream", workstream_id])
    completed = subprocess.run(command, capture_output=True)
    stdout = decode_process_output(completed.stdout)
    stderr = decode_process_output(completed.stderr)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (stderr or stdout or "prepass emitted invalid JSON").strip()}
    if completed.returncode != 0:
        payload.setdefault("ok", False)
        payload.setdefault("error", stderr.strip() or "prepass failed")
    return payload


def load_or_run_audit(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    pack_date: date,
    prepass: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.audit:
        audit_path = resolve_project_path(project_root, args.audit)
        audit = load_json(audit_path)
        return audit, {"outputs": {"json": str(audit_path)}}

    audit_script = Path(args.audit_script).resolve()
    if not audit_script.exists():
        return {"ok": False, "error": f"audit script not found: {audit_script}"}, {}

    audit_output_dir = resolve_audit_output_dir(args.audit_output_dir, memory_root)
    audit_output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adp-meeting-pack-") as temp_dir:
        prepass_path = Path(temp_dir) / "prepass.json"
        prepass_path.write_text(json.dumps(prepass, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            str(audit_script),
            str(project_root),
            "--scenario",
            args.scenario,
            "--memory-root",
            str(memory_root),
            "--prepass-json",
            str(prepass_path),
            "--as-of",
            pack_date.isoformat(),
            "--max-age-days",
            str(args.max_age_days),
            "--output-dir",
            str(audit_output_dir),
        ]
        for workstream_id in args.workstream:
            command.extend(["--workstream", workstream_id])
        completed = subprocess.run(command, capture_output=True)
        stdout = decode_process_output(completed.stdout)
        stderr = decode_process_output(completed.stderr)
    try:
        audit_run = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (stderr or stdout or "audit emitted invalid JSON").strip()}, {}
    if completed.returncode != 0 or not audit_run.get("ok"):
        audit_run.setdefault("ok", False)
        audit_run.setdefault("error", stderr.strip() or "audit failed")
        return audit_run, audit_run
    audit_path = Path(audit_run.get("outputs", {}).get("json", ""))
    if not audit_path.exists():
        return {"ok": False, "error": "audit did not write JSON output"}, audit_run
    return load_json(audit_path), audit_run


def build_context(
    scenario: str,
    pack_date: date,
    project_root: Path,
    memory_root: Path,
    prepass: dict[str, Any],
    audit: dict[str, Any],
    audit_run: dict[str, Any],
) -> dict[str, Any]:
    action_groups, excluded_actions = action_board(prepass.get("ledger_actions", []))
    red_items, amber_items = red_amber_items(audit)
    dependency_items = dependency_board(prepass)
    readiness_items = readiness_exceptions(audit, prepass)
    escalation_items = decision_escalations(audit)
    roundtable_items = workstream_roundtable(prepass.get("workstreams", []))
    audit_path = audit_run.get("outputs", {}).get("json", "") or audit.get("outputs", {}).get("json", "")
    business_context = build_business_biweekly_context(memory_root, audit, prepass) if scenario == "business-biweekly" else {}
    context = {
        "scenario": scenario,
        "date": pack_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "prepass": prepass,
        "audit": audit,
        "audit_path": audit_path,
        "action_groups": action_groups,
        "action_board_count": sum(len(items) for items in action_groups.values()),
        "excluded_actions": excluded_actions,
        "red_items": red_items,
        "amber_items": amber_items,
        "dependency_items": dependency_items,
        "readiness_items": readiness_items,
        "escalation_items": escalation_items,
        "roundtable_items": roundtable_items,
        "source_rows": source_rows(audit, prepass),
        "roadmap_available": roadmap_available(memory_root),
        "business": business_context,
    }
    return context


def build_distillate(
    context: dict[str, Any],
    markdown_path: Path,
    distillate_path: Path,
    recommended_workflows: list[str],
) -> dict[str, Any]:
    red_rows = finding_table_rows(context["red_items"], "red")
    amber_rows = finding_table_rows(context["amber_items"], "amber")
    readiness_rows = finding_table_rows(context["readiness_items"], "")
    escalation_rows = finding_table_rows(context["escalation_items"], "")
    action_rows = action_board_rows(context["action_groups"])
    business = context.get("business", {})
    roadmap_version = business.get("roadmap_summary", {}).get("version", "")
    if not roadmap_version:
        roadmap_version = "unavailable" if context["scenario"] == "business-biweekly" else "not-applicable"
    lineage = {
        "meeting_pack_id": f"{context['date']}-{context['scenario']}",
        "meeting_pack_path": str(markdown_path),
        "scenario": context["scenario"],
        "audit_path": context["audit_path"],
        "roadmap_version": roadmap_version,
    }
    return {
        "schema_version": 1,
        **lineage,
        "date": context["date"],
        "generated_at": context["generated_at"],
        "paths": {
            "markdown": str(markdown_path),
            "distillate": str(distillate_path),
            "audit": context["audit_path"],
            "memory_root": context["memory_root"],
        },
        "audit": {
            "status": context["audit"].get("audit_status", "unknown"),
            "path": context["audit_path"],
        },
        "sources": context["source_rows"],
        "boards": {
            "actions": action_rows,
            "red": red_rows,
            "amber": amber_rows,
            "dependencies": context["dependency_items"],
            "readiness": readiness_rows,
            "escalations": escalation_rows,
            "roundtable": context["roundtable_items"],
            "business_decisions": business.get("decision_items", []),
            "scope_change": business.get("scope_change_items", []),
            "business_readiness": business.get("readiness_items", []),
            "roadmap_timeline": business.get("roadmap_timeline_items", []),
            "roadmap_unscheduled": business.get("roadmap_unscheduled_items", []),
            "roadmap_blocked_decisions": business.get("roadmap_decision_blocks", []),
            "roadmap_blocked_dependencies": business.get("roadmap_dependency_blocks", []),
            "cross_line_business_impact": business.get("business_impact_items", []),
            "last_meeting_closure": business.get("last_meeting_closure_items", []),
        },
        "gaps": {
            "action_quality": context["excluded_actions"],
            "readiness": readiness_rows,
            "red_amber": [*red_rows, *amber_rows],
            "escalations": escalation_rows,
        },
        "counts": {
            "sources_read": len(context["prepass"].get("sources_read", [])),
            "workstreams": len(context["prepass"].get("workstreams", [])),
            "actions_in_board": len(action_rows),
            "actions_excluded": len(context["excluded_actions"]),
            "red_amber_items": len(red_rows) + len(amber_rows),
            "business_decisions": len(business.get("decision_items", [])),
            "scope_change_items": len(business.get("scope_change_items", [])),
            "business_readiness_items": len(business.get("readiness_items", [])),
            "roadmap_timeline_items": len(business.get("roadmap_timeline_items", [])),
            "roadmap_unscheduled_items": len(business.get("roadmap_unscheduled_items", [])),
        },
        "roadmap": business.get("roadmap_summary", {}),
        "next_workflows": recommended_workflows,
        "next_workflow_payload": {
            **lineage,
            "date": context["date"],
            "meeting_pack": str(markdown_path),
            "distillate": str(distillate_path),
            "audit": context["audit_path"],
            "lineage": lineage,
            "recommended_workflows": recommended_workflows,
        },
    }


def build_business_biweekly_context(memory_root: Path, audit: dict[str, Any], prepass: dict[str, Any]) -> dict[str, Any]:
    roadmap = load_roadmap(memory_root)
    audit_status = str(audit.get("audit_status", "unknown")).lower()
    audit_allows_dates = audit_status == "pass"
    decision_items = business_decision_board(memory_root)
    scope_change_items = scope_change_board(audit, prepass)
    readiness_items = business_readiness_board(memory_root, audit, prepass)
    timeline_items = roadmap_timeline_rows(roadmap, audit_allows_dates)
    unscheduled_items = roadmap_unscheduled_rows(roadmap, audit_allows_dates, audit_status)
    decision_blocks = roadmap_decision_block_rows(roadmap)
    dependency_blocks = roadmap_dependency_block_rows(roadmap)
    business_impact_items = cross_line_business_impact_rows(
        dependency_blocks,
        dependency_board(prepass),
        audit=audit,
        prepass=prepass,
    )
    last_closure_items = last_meeting_closure_rows(memory_root, audit, prepass)
    return {
        "decision_items": decision_items,
        "scope_change_items": scope_change_items,
        "readiness_items": readiness_items,
        "roadmap_timeline_items": timeline_items,
        "roadmap_unscheduled_items": unscheduled_items,
        "roadmap_decision_blocks": decision_blocks,
        "roadmap_dependency_blocks": dependency_blocks,
        "business_impact_items": business_impact_items,
        "last_meeting_closure_items": last_closure_items,
        "roadmap_summary": {
            "available": roadmap["available"],
            "path": roadmap["path"],
            "version": clean(roadmap.get("data", {}).get("generated_at")),
            "status": roadmap_status_label(roadmap, audit_status),
            "audit_status": audit_status,
            "dates_visible": audit_allows_dates and roadmap["available"],
        },
    }


def business_decision_board(memory_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    packet_root = memory_root / "decisions" / "business-decision-packets"
    if packet_root.exists():
        for path in sorted(packet_root.glob("*.md")):
            packet = parse_business_packet(memory_root, path)
            if is_closed_business_decision_status(packet["Status"]):
                continue
            key = (packet["Source"], packet["Decision"])
            seen.add(key)
            rows.append(packet)

    decision_log = memory_root / "decisions" / "decision-log.md"
    for row in decision_log_open_rows(memory_root, decision_log):
        key = (row["Source"], row["Decision"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows[:20]


def parse_business_packet(memory_root: Path, path: Path) -> dict[str, str]:
    text = read_text(path)
    title = heading_title(text) or path.stem
    decision = section_text(text, "Decision Needed") or title
    risks = section_text(text, "Risks and Trade-offs")
    return {
        "Source": rel_to_memory(memory_root, path),
        "Source Type": "business-decision-packet",
        "Decision": clean(decision),
        "Workstreams": clean(extract_colon_field(text, "Affected workstreams")) or "TBD",
        "Owner": clean(extract_colon_field(text, "Confirming owner") or extract_colon_field(text, "Confirmer")) or "TBD",
        "Deadline / Trigger": clean(extract_colon_field(text, "Deadline / trigger")) or "TBD",
        "Status": clean(extract_colon_field(text, "Status")) or "open",
        "Background": clean(section_text(text, "Background")) or "TBD",
        "Options": clean(section_text(text, "Options")) or "TBD",
        "Impact": clean(risks or section_text(text, "Impact")) or "TBD",
        "Recommendation": clean(section_text(text, "Recommendation")) or "TBD",
    }


def decision_log_open_rows(memory_root: Path, path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for row in parse_first_table(read_text(path).splitlines()):
        decision = first_value(row, "decision / question", "decision", "question")
        if not has_value(decision):
            continue
        status = first_value(row, "status") or "open"
        if is_closed_business_decision_status(status):
            continue
        source = first_value(row, "link", "source") or rel_to_memory(memory_root, path)
        rows.append(
            {
                "Source": clean(source),
                "Source Type": "decision-log",
                "Decision": clean(decision),
                "Workstreams": clean(first_value(row, "affected workstreams")) or "TBD",
                "Owner": clean(first_value(row, "confirmer", "owner")) or "TBD",
                "Deadline / Trigger": clean(first_value(row, "date", "deadline", "deadline / trigger")) or "TBD",
                "Status": clean(status),
                "Background": "TBD",
                "Options": "TBD",
                "Impact": "TBD",
                "Recommendation": "TBD",
            }
        )
    return rows


def scope_change_board(audit: dict[str, Any], prepass: dict[str, Any]) -> list[dict[str, str]]:
    explicit = explicit_board_rows(audit, "scope_change") or explicit_board_rows(prepass, "scope_change")
    if explicit:
        return [normalize_scope_change_row(item) for item in explicit][:20]

    rows: list[dict[str, str]] = []
    for ws in prepass.get("workstreams", []):
        change_notes = text_or_tbd(ws.get("change_notes"))
        if not has_value(change_notes):
            continue
        rows.append(
            {
                "Source": text_or_tbd(ws.get("record")),
                "Workstream": text_or_tbd(ws.get("id")),
                "Owner": first_meaningful(ws.get("business_owner"), ws.get("owner")),
                "Type": "WDR scope/change note",
                "Item": change_notes,
                "Status": text_or_tbd(ws.get("status")),
            }
        )
    return rows[:20]


def normalize_scope_change_row(item: dict[str, Any]) -> dict[str, str]:
    return {
        "Source": text_or_tbd(item.get("Source") or item.get("source")),
        "Workstream": text_or_tbd(item.get("Workstream") or item.get("workstream") or item.get("affected_workstreams")),
        "Owner": text_or_tbd(item.get("Owner") or item.get("owner")),
        "Type": text_or_tbd(item.get("Type") or item.get("type") or item.get("change_type")),
        "Item": text_or_tbd(item.get("Item") or item.get("item") or item.get("note")),
        "Status": text_or_tbd(item.get("Status") or item.get("status")),
    }


def business_readiness_board(memory_root: Path, audit: dict[str, Any], prepass: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows.extend(readiness_view_rows(memory_root, "views/acceptance-readiness.md", "acceptance"))
    rows.extend(readiness_view_rows(memory_root, "views/cutover-readiness.md", "cutover"))
    if rows:
        return rows[:20]

    fallback: list[dict[str, str]] = []
    for row in finding_table_rows(readiness_exceptions(audit, prepass), ""):
        fallback.append(
            {
                "Source": row["Source"],
                "Gate": "readiness",
                "Workstream": row["Workstream"],
                "Status": "gap",
                "Score": "TBD",
                "Missing Evidence": row["Gap"],
                "Unclosed Criteria": "TBD",
                "Business Confirmation": "TBD",
            }
        )
    return fallback[:20]


def readiness_view_rows(memory_root: Path, rel_path: str, gate: str) -> list[dict[str, str]]:
    path = memory_root / rel_path
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for row in parse_first_table(read_text(path).splitlines()):
        if not any(has_value(value) for value in row.values()):
            continue
        rows.append(
            {
                "Source": rel_path,
                "Gate": gate,
                "Workstream": clean(first_value(row, "workstream")) or "TBD",
                "Status": clean(first_value(row, "status", "readiness status")) or "TBD",
                "Score": clean(first_value(row, "readiness score", "score")) or "TBD",
                "Missing Evidence": clean(first_value(row, "missing evidence")) or "TBD",
                "Unclosed Criteria": clean(first_value(row, "unclosed criteria")) or "TBD",
                "Business Confirmation": clean(first_value(row, "business confirmation")) or "TBD",
            }
        )
    return rows


def load_roadmap(memory_root: Path) -> dict[str, Any]:
    json_path = memory_root / "views" / "roadmap.json"
    markdown_path = memory_root / "views" / "roadmap.md"
    if json_path.exists():
        try:
            data = load_json(json_path)
        except json.JSONDecodeError as exc:
            return {"available": False, "path": rel_to_memory(memory_root, json_path), "data": {}, "error": f"roadmap JSON is invalid: {exc}"}
        if roadmap_has_generated_content(data):
            return {"available": True, "path": rel_to_memory(memory_root, json_path), "data": data, "error": ""}
        return {
            "available": False,
            "path": rel_to_memory(memory_root, json_path),
            "data": data,
            "error": "roadmap JSON exists but has no generated source-backed items; run adp-roadmap-sync",
        }
    if markdown_path.exists():
        return {"available": False, "path": rel_to_memory(memory_root, markdown_path), "data": {}, "error": "roadmap Markdown exists but roadmap JSON is missing"}
    return {"available": False, "path": "views/roadmap.md|views/roadmap.json", "data": {}, "error": "roadmap view is missing"}


def roadmap_has_generated_content(data: dict[str, Any]) -> bool:
    source_inventory = data.get("source_inventory", {}) if isinstance(data.get("source_inventory"), dict) else {}
    if as_list(source_inventory.get("sources_read")):
        return True
    for section in [
        "milestone_timeline",
        "unscheduled_milestones",
        "at_risk_dates",
        "blocked_by_decisions",
        "blocked_by_dependencies",
        "changed_since_last_roadmap",
        "excluded_items",
    ]:
        if as_list(data.get(section)):
            return True
    return False


def roadmap_timeline_rows(roadmap: dict[str, Any], audit_allows_dates: bool) -> list[dict[str, str]]:
    if not roadmap["available"] or not audit_allows_dates:
        return []
    rows: list[dict[str, str]] = []
    for item in as_list(roadmap["data"].get("milestone_timeline")):
        if not isinstance(item, dict) or not has_value(item.get("source")):
            continue
        rows.append(
            {
                "Milestone": text_or_tbd(item.get("milestone")),
                "Type": text_or_tbd(item.get("type")),
                "Status": text_or_tbd(item.get("status")),
                "Planned": text_or_tbd(item.get("planned")),
                "Forecast": text_or_tbd(item.get("forecast")),
                "Actual": text_or_tbd(item.get("actual")),
                "Owner": text_or_tbd(item.get("owner")),
                "Confidence": text_or_tbd(item.get("confidence")),
                "Source": text_or_tbd(item.get("source")),
            }
        )
    return rows[:15]


def roadmap_unscheduled_rows(roadmap: dict[str, Any], audit_allows_dates: bool, audit_status: str) -> list[dict[str, str]]:
    if not roadmap["available"]:
        return [
            {
                "Milestone": "TBD / unscheduled",
                "Type": "TBD",
                "Status": "TBD",
                "Owner": "TBD",
                "Confidence": "low",
                "Source": roadmap["path"],
                "Note": roadmap.get("error") or "roadmap is not available",
            }
        ]
    items = list(as_list(roadmap["data"].get("unscheduled_milestones")))
    if not audit_allows_dates:
        items.extend(as_list(roadmap["data"].get("milestone_timeline")))
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        note = "; ".join(clean(value) for value in as_list(item.get("notes")) if has_value(value))
        if not audit_allows_dates:
            note = "; ".join(part for part in [note, f"date fields hidden until audit status is pass; current audit status: {audit_status}"] if part)
        rows.append(
            {
                "Milestone": text_or_tbd(item.get("milestone")),
                "Type": text_or_tbd(item.get("type")),
                "Status": text_or_tbd(item.get("status")),
                "Owner": text_or_tbd(item.get("owner")),
                "Confidence": text_or_tbd(item.get("confidence")),
                "Source": text_or_tbd(item.get("source")),
                "Note": note or "TBD",
            }
        )
    return rows[:20]


def roadmap_decision_block_rows(roadmap: dict[str, Any]) -> list[dict[str, str]]:
    if not roadmap["available"]:
        return []
    rows: list[dict[str, str]] = []
    for item in as_list(roadmap["data"].get("blocked_by_decisions")):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "Source": text_or_tbd(item.get("source")),
                "Decision": text_or_tbd(item.get("decision")),
                "Owner": text_or_tbd(item.get("owner")),
                "Status": text_or_tbd(item.get("status")),
                "Workstreams": format_value(item.get("workstreams")),
            }
        )
    return rows[:20]


def roadmap_dependency_block_rows(roadmap: dict[str, Any]) -> list[dict[str, str]]:
    if not roadmap["available"]:
        return []
    rows: list[dict[str, str]] = []
    for item in as_list(roadmap["data"].get("blocked_by_dependencies")):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "Source": text_or_tbd(item.get("source")),
                "Workstream": text_or_tbd(item.get("workstream")),
                "Type": text_or_tbd(item.get("type")),
                "Item": text_or_tbd(item.get("item")),
                "Owner": text_or_tbd(item.get("owner")),
            }
        )
    return rows[:20]


def cross_line_business_impact_rows(
    roadmap_blocks: list[dict[str, str]],
    dependency_items: list[dict[str, str]],
    *,
    audit: dict[str, Any] | None = None,
    prepass: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    explicit = preferred_board_rows(audit, prepass, "business_impact")
    if explicit is not None:
        return [normalize_business_impact_row(item) for item in explicit][:15]

    rows: list[dict[str, str]] = []
    for item in roadmap_blocks:
        rows.append(
            {
                "Source": item["Source"],
                "Workstream": item["Workstream"],
                "Dependency / Blocker": item["Item"],
                "Risk": "TBD",
                "Business Impact": "TBD",
                "Owner": item["Owner"],
                "Status": "TBD",
            }
        )
    if rows:
        return rows[:15]
    for item in dependency_items:
        rows.append(
            {
                "Source": item.get("Source", "TBD"),
                "Workstream": item.get("Workstream", "TBD"),
                "Dependency / Blocker": item.get("Dependency / blocker", "TBD"),
                "Risk": item.get("Risk", "TBD"),
                "Business Impact": "TBD",
                "Owner": item.get("Owner", "TBD"),
                "Status": "TBD",
            }
        )
    return rows[:15]


def normalize_business_impact_row(item: dict[str, Any]) -> dict[str, str]:
    return {
        "Source": text_or_tbd(item.get("Source") or item.get("source")),
        "Workstream": text_or_tbd(item.get("Workstream") or item.get("workstream")),
        "Business Impact": text_or_tbd(item.get("Business Impact") or item.get("business_impact")),
        "Owner": text_or_tbd(item.get("Owner") or item.get("owner")),
        "Status": text_or_tbd(item.get("Status") or item.get("status")),
    }


def last_meeting_closure_rows(memory_root: Path, audit: dict[str, Any], prepass: dict[str, Any] | None = None) -> list[dict[str, str]]:
    explicit = preferred_board_rows(audit, prepass, "last_meeting_closure")
    if explicit is not None:
        return [normalize_last_meeting_closure_row(item) for item in explicit if has_typed_meeting_origin(item)][-15:]

    rows: list[dict[str, str]] = []
    action_path = memory_root / "actions" / "action-ledger.md"
    if action_path.exists():
        for row in parse_first_table(read_text(action_path).splitlines()):
            source = first_value(row, "source")
            if not has_canonical_meetings_segment(source):
                continue
            rows.append(
                {
                    "Type": "action",
                    "Source": clean(source) or rel_to_memory(memory_root, action_path),
                    "Workstream": clean(first_value(row, "workstream", "affected workstreams")) or "TBD",
                    "Item": clean(first_value(row, "action id")) + " " + clean(first_value(row, "action")),
                    "Owner": clean(first_value(row, "owner")) or "TBD",
                    "Status": clean(first_value(row, "status")) or "TBD",
                    "Closure / Decision": clean(first_value(row, "closure criteria")) or "TBD",
                }
            )
    decision_log = memory_root / "decisions" / "decision-log.md"
    if decision_log.exists():
        for row in parse_first_table(read_text(decision_log).splitlines()):
            source = first_value(row, "source", "link")
            if not has_canonical_meetings_segment(source):
                continue
            rows.append(
                {
                    "Type": "decision",
                    "Source": clean(source) or rel_to_memory(memory_root, decision_log),
                    "Workstream": clean(first_value(row, "affected workstreams")) or "TBD",
                    "Item": clean(first_value(row, "decision / question", "decision", "question")) or "TBD",
                    "Owner": clean(first_value(row, "confirmer", "owner")) or "TBD",
                    "Status": clean(first_value(row, "status")) or "TBD",
                    "Closure / Decision": clean(first_value(row, "date")) or "TBD",
                }
            )
    return rows[-15:]


def normalize_last_meeting_closure_row(item: dict[str, Any]) -> dict[str, str]:
    return {
        "Type": text_or_tbd(item.get("Type") or item.get("type")),
        "Source": text_or_tbd(item.get("Source") or item.get("source")),
        "Workstream": text_or_tbd(item.get("Workstream") or item.get("workstream")),
        "Item": text_or_tbd(item.get("Item") or item.get("item")),
        "Owner": text_or_tbd(item.get("Owner") or item.get("owner")),
        "Status": text_or_tbd(item.get("Status") or item.get("status")),
        "Closure / Decision": text_or_tbd(item.get("Closure / Decision") or item.get("closure_decision")),
    }


def roadmap_status_label(roadmap: dict[str, Any], audit_status: str) -> str:
    if not roadmap["available"]:
        return f"TBD / unscheduled; {roadmap.get('error') or 'roadmap is not available'}"
    if audit_status != "pass":
        return f"available but date fields hidden because audit status is {audit_status}"
    return "available"


def action_board_rows(action_groups: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for owner in sorted(action_groups):
        for item in action_groups[owner]:
            rows.append({"Owner": owner, **item})
    return rows


def render_markdown(context: dict[str, Any]) -> str:
    if context["scenario"] == "business-biweekly":
        return render_business_biweekly(context)
    return render_fde_morning(context)


def render_fde_morning(context: dict[str, Any]) -> str:
    lines = header_lines(context, "FDE Morning Meeting Pack")
    lines.extend(section_table("Red / Amber Board", ["Severity", "Source", "Workstream", "Item", "Recommended workflow"], [
        *finding_table_rows(context["red_items"], "red"),
        *finding_table_rows(context["amber_items"], "amber"),
    ]))
    lines.extend(action_board_section(context))
    lines.extend(section_table("Cross-Line Dependency Board", ["Workstream", "Owner", "Dependency / blocker", "Risk", "Source"], context["dependency_items"]))
    lines.extend(section_table("Readiness Exceptions", ["Source", "Workstream", "Gap", "Recommended workflow"], finding_table_rows(context["readiness_items"], "")))
    lines.extend(section_table("Decision / Escalation Needed", ["Source", "Workstream", "Gap", "Recommended workflow"], finding_table_rows(context["escalation_items"], "")))
    lines.extend(section_table("Workstream Roundtable", ["Workstream", "Owner", "Status", "Progress", "Blocker", "Risk", "Next action", "Source"], context["roundtable_items"]))
    lines.extend(post_meeting_checklist(context))
    lines.extend(source_inventory(context))
    return "\n".join(lines).rstrip() + "\n"


def render_business_biweekly(context: dict[str, Any]) -> str:
    business = context["business"]
    roadmap = business["roadmap_summary"]
    lines = header_lines(context, "Business Biweekly Meeting Pack")
    lines.extend(
        [
            "## Executive Snapshot",
            "",
            f"- Audit status: `{context['audit'].get('audit_status', 'unknown')}`",
            f"- Business decision items: {len(business['decision_items'])}",
            f"- Scope / change items: {len(business['scope_change_items'])}",
            f"- Readiness rows: {len(business['readiness_items'])}",
            f"- Roadmap status: {roadmap['status']}",
            "",
        ]
    )
    lines.extend(
        section_table(
            "Decision Board",
            ["Source", "Source Type", "Decision", "Workstreams", "Owner", "Deadline / Trigger", "Status", "Options", "Impact", "Recommendation"],
            business["decision_items"],
        )
    )
    lines.extend(section_table("Scope / Change Board", ["Source", "Workstream", "Owner", "Type", "Item", "Status"], business["scope_change_items"]))
    lines.extend(
        section_table(
            "Readiness Board",
            ["Source", "Gate", "Workstream", "Status", "Score", "Missing Evidence", "Unclosed Criteria", "Business Confirmation"],
            business["readiness_items"],
        )
    )
    lines.extend(["## Roadmap / Timeline", ""])
    if roadmap["dates_visible"]:
        lines.extend(["Dates below come from `views/roadmap.json`; action due triggers are not promoted into milestones.", ""])
    else:
        lines.extend(["Timeline dates are `TBD / unscheduled` unless the source-backed roadmap exists and the audit gate passes. Do not infer dates from action due triggers.", ""])
    lines.extend(
        section_table(
            "Milestone Timeline",
            ["Milestone", "Type", "Status", "Planned", "Forecast", "Actual", "Owner", "Confidence", "Source"],
            business["roadmap_timeline_items"],
        )
    )
    lines.extend(
        section_table(
            "Unscheduled Roadmap Items",
            ["Milestone", "Type", "Status", "Owner", "Confidence", "Source", "Note"],
            business["roadmap_unscheduled_items"],
        )
    )
    lines.extend(section_table("Blocked By Decisions", ["Source", "Decision", "Owner", "Status", "Workstreams"], business["roadmap_decision_blocks"]))
    lines.extend(section_table("Cross-Line Business Impact", ["Source", "Workstream", "Business Impact", "Owner", "Status"], business["business_impact_items"]))
    lines.extend(section_table("Last Meeting Closure", ["Type", "Source", "Workstream", "Item", "Owner", "Status", "Closure / Decision"], business["last_meeting_closure_items"]))
    lines.extend(post_meeting_checklist(context))
    lines.extend(source_inventory(context))
    return "\n".join(lines).rstrip() + "\n"


def header_lines(context: dict[str, Any], title: str) -> list[str]:
    audit = context["audit"]
    prepass = context["prepass"]
    outputs = [
        f"# {title}",
        "",
        f"Generated: {context['generated_at']}",
        f"Meeting date: {context['date']}",
        f"Scenario: `{context['scenario']}`",
        f"Memory root: `{context['memory_root']}`",
        "",
        "This meeting pack is not a source of truth. It is a derived view for meeting preparation; after the meeting, write outcomes back through ADP durable workflows.",
        "",
        "## Audit Status",
        "",
        f"- Audit status: `{audit.get('audit_status', 'unknown')}`",
        f"- Audit JSON: `{context['audit_path'] or 'not supplied'}`",
        f"- Sources read: {len(prepass.get('sources_read', []))}",
        f"- Workstreams covered: {len(prepass.get('workstreams', []))}",
        f"- Active ledger actions read: {len(prepass.get('ledger_actions', []))}",
        f"- Blocking findings: {audit.get('counts', {}).get('blocking_findings', 0)}",
        f"- Warning findings: {audit.get('counts', {}).get('warning_findings', 0)}",
        "",
    ]
    return outputs


def action_board_section(context: dict[str, Any]) -> list[str]:
    lines = ["## Today's FDE Action Board", ""]
    groups: dict[str, list[dict[str, str]]] = context["action_groups"]
    if not groups:
        lines.extend(["No board-ready active actions with source and closure criteria.", ""])
    for owner in sorted(groups):
        lines.extend([f"### {owner}", ""])
        lines.extend(
            section_table(
                "",
                ["Action ID", "Status", "Workstream", "Action", "Due / Trigger", "Closure Criteria", "Source"],
                groups[owner],
                include_heading=False,
            )
        )
    excluded = context["excluded_actions"]
    if excluded:
        lines.extend(section_table("Action Quality Gaps", ["Action ID", "Owner", "Workstream", "Gap", "Source"], excluded))
    return lines


def post_meeting_checklist(context: dict[str, Any]) -> list[str]:
    scenario = context["scenario"]
    return [
        "## Post-Meeting Capture Checklist",
        "",
        "- Capture raw meeting notes, decisions, no-ops, and owner corrections through `adp-meeting-sync`.",
        "- Run `adp-status-sync` for generated or changed action-ledger intake.",
        "- Refresh affected readiness, risk, dependency, roadmap, or Program Lead views only after durable state is updated.",
        f"- Regenerate this pack with `adp-meeting-pack --scenario {scenario}` before using it again.",
        "",
    ]


def source_inventory(context: dict[str, Any]) -> list[str]:
    rows = context["source_rows"]
    lines = section_table("Source Inventory", ["Path", "Modified", "Bytes"], rows)
    if len(rows) >= 30:
        lines.extend(["Source inventory truncated to the first 30 entries; see the audit JSON for the full list.", ""])
    return lines


def action_board(actions: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    excluded: list[dict[str, str]] = []
    for action in actions:
        status = str(action.get("status", "")).strip().lower()
        if status not in ACTIVE_ACTION_STATUSES:
            continue
        owner = text_or_tbd(action.get("owner"))
        source = text_or_tbd(action.get("source"))
        due = text_or_tbd(action.get("due_or_trigger"))
        closure = text_or_tbd(action.get("closure_criteria"))
        gaps = []
        if not has_value(owner):
            gaps.append("owner missing")
        if not has_value(source):
            gaps.append("source missing")
        if not has_value(due):
            gaps.append("due trigger missing")
        if not has_value(closure):
            gaps.append("closure criteria missing")
        elif action.get("closure_criteria_verifiable") is not True:
            gaps.append("closure criteria verifiability not confirmed")
        if gaps:
            excluded.append(
                {
                    "Action ID": text_or_tbd(action.get("action_id")),
                    "Owner": owner,
                    "Workstream": text_or_tbd(action.get("workstream")),
                    "Gap": "; ".join(gaps),
                    "Source": source,
                }
            )
            continue
        groups[owner].append(
            {
                "Action ID": text_or_tbd(action.get("action_id")),
                "Status": text_or_tbd(action.get("status")),
                "Workstream": text_or_tbd(action.get("workstream")),
                "Action": text_or_tbd(action.get("action")),
                "Due / Trigger": due,
                "Closure Criteria": closure,
                "Source": source,
            }
        )
    for owner_actions in groups.values():
        owner_actions.sort(key=lambda item: (status_sort(item["Status"]), item["Due / Trigger"], item["Action ID"]))
    return dict(groups), excluded


def red_amber_items(audit: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings = audit.get("findings", {})
    freshness = findings.get("freshness", {})
    completeness = findings.get("completeness", {})
    consistency = findings.get("consistency", {})
    closure = findings.get("closure", {})
    merge_quality = findings.get("merge_quality", {})
    red = [
        *as_list(completeness.get("blocking_gaps")),
        *as_list(consistency.get("source_disagreements")),
        *as_list(closure.get("unconsumed_intake_files")),
        *as_list(merge_quality.get("conflict_candidates")),
    ]
    amber = [
        *as_list(freshness.get("stale_workstreams")),
        *as_list(freshness.get("stale_actions")),
        *as_list(freshness.get("views_requiring_refresh")),
        *as_list(completeness.get("non_blocking_gaps")),
        *as_list(consistency.get("consistency_warnings")),
        *as_list(consistency.get("recommended_refreshes")),
        *as_list(closure.get("open_business_packets")),
        *as_list(closure.get("escalation_candidates")),
        *as_list(merge_quality.get("duplicate_candidates")),
        *as_list(merge_quality.get("overlap_candidates")),
    ]
    return red[:20], amber[:20]


def dependency_board(prepass: dict[str, Any]) -> list[dict[str, str]]:
    explicit = explicit_board_rows(prepass, "dependencies")
    if explicit:
        return [normalize_dependency_row(item) for item in explicit][:20]

    rows = []
    for ws in prepass.get("workstreams", []):
        blocker = text_or_tbd(ws.get("blockers"))
        risk = text_or_tbd(ws.get("risks"))
        dependency = text_or_tbd(ws.get("dependencies"))
        links = ws.get("links", {}) if isinstance(ws.get("links"), dict) else {}
        linked = ", ".join([*as_list(links.get("depends_on")), *as_list(links.get("impacts"))])
        has_dependency = has_value(dependency) or has_value(linked)
        has_blocker = has_value(blocker)
        if not (has_dependency or has_blocker):
            continue
        rows.append(
            {
                "Workstream": text_or_tbd(ws.get("id")),
                "Owner": text_or_tbd(ws.get("owner")),
                "Dependency / blocker": "; ".join(item for item in [dependency if has_value(dependency) else "", linked if has_value(linked) else "", blocker if has_blocker else ""] if item),
                "Risk": risk if has_value(risk) else "TBD",
                "Source": text_or_tbd(ws.get("record")),
            }
        )
    return rows[:20]


def normalize_dependency_row(item: dict[str, Any]) -> dict[str, str]:
    return {
        "Workstream": text_or_tbd(item.get("Workstream") or item.get("workstream")),
        "Owner": text_or_tbd(item.get("Owner") or item.get("owner")),
        "Dependency / blocker": text_or_tbd(item.get("Dependency / blocker") or item.get("dependency") or item.get("blocker")),
        "Risk": text_or_tbd(item.get("Risk") or item.get("risk")),
        "Source": text_or_tbd(item.get("Source") or item.get("source")),
    }


def readiness_exceptions(audit: dict[str, Any], prepass: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = explicit_board_rows(audit, "readiness") or explicit_board_rows(prepass, "readiness")
    if explicit:
        return explicit[:20]

    findings = audit.get("findings", {})
    completeness = findings.get("completeness", {})
    freshness = findings.get("freshness", {})
    candidates = [
        *as_list(completeness.get("blocking_gaps")),
        *as_list(completeness.get("non_blocking_gaps")),
        *as_list(freshness.get("views_requiring_refresh")),
    ]
    return [item for item in candidates if is_readiness_item(item)][:20]


def is_readiness_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    field = str(item.get("field", "")).strip()
    source = str(item.get("source") or item.get("path") or "").strip()
    return field in READINESS_FIELDS or source in READINESS_VIEW_PATHS


def explicit_board_rows(payload: dict[str, Any], board_name: str) -> list[dict[str, Any]]:
    rows = typed_board_rows(payload, board_name)
    return rows if rows is not None else []


def preferred_board_rows(
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
    board_name: str,
) -> list[dict[str, Any]] | None:
    for payload in (primary, secondary):
        rows = typed_board_rows(payload, board_name)
        if rows is not None:
            return rows
    return None


def typed_board_rows(payload: dict[str, Any] | None, board_name: str) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    boards = payload.get("meeting_pack_boards", {})
    if not isinstance(boards, dict) or board_name not in boards:
        return None
    rows = boards.get(board_name)
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def decision_escalations(audit: dict[str, Any]) -> list[dict[str, Any]]:
    findings = audit.get("findings", {})
    closure = findings.get("closure", {})
    consistency = findings.get("consistency", {})
    merge_quality = findings.get("merge_quality", {})
    return [
        *as_list(closure.get("open_business_packets")),
        *as_list(closure.get("escalation_candidates")),
        *as_list(consistency.get("source_disagreements")),
        *as_list(merge_quality.get("conflict_candidates")),
    ][:20]


def workstream_roundtable(workstreams: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for ws in workstreams:
        rows.append(
            {
                "Workstream": text_or_tbd(ws.get("id")),
                "Owner": text_or_tbd(ws.get("owner")),
                "Status": text_or_tbd(ws.get("status")),
                "Progress": text_or_tbd(ws.get("progress")),
                "Blocker": text_or_tbd(ws.get("blockers")),
                "Risk": text_or_tbd(ws.get("risks")),
                "Next action": text_or_tbd(ws.get("next_actions")),
                "Source": text_or_tbd(ws.get("record")),
            }
        )
    return rows


def source_rows(audit: dict[str, Any], prepass: dict[str, Any]) -> list[dict[str, str]]:
    inventory = audit.get("source_inventory", {})
    sources = inventory.get("sources_read") if isinstance(inventory, dict) else None
    if not sources:
        sources = prepass.get("sources_read", [])
    rows = []
    for source in as_list(sources)[:30]:
        if not isinstance(source, dict):
            continue
        rows.append(
            {
                "Path": text_or_tbd(source.get("path")),
                "Modified": text_or_tbd(source.get("modified")),
                "Bytes": text_or_tbd(source.get("bytes")),
            }
        )
    return rows


def roadmap_available(memory_root: Path) -> bool:
    return (memory_root / "views" / "roadmap.md").exists() or (memory_root / "views" / "roadmap.json").exists()


def finding_table_rows(items: list[dict[str, Any]], severity: str) -> list[dict[str, str]]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            item = {"gap": str(item)}
        row = {
            "Source": text_or_tbd(item.get("source") or item.get("path") or item.get("Source")),
            "Workstream": text_or_tbd(item.get("workstream") or item.get("affected_workstreams") or item.get("Workstream")),
            "Gap": text_or_tbd(item.get("gap") or item.get("reason") or item.get("action") or item.get("status") or item.get("Gap")),
            "Recommended workflow": text_or_tbd(item.get("recommended_workflow") or item.get("Recommended workflow")),
        }
        if severity:
            row = {"Severity": severity, **row}
            row["Item"] = row.pop("Gap")
        rows.append(row)
    return rows


def section_table(
    heading: str,
    headers: list[str],
    rows: list[dict[str, str]],
    include_heading: bool = True,
) -> list[str]:
    lines: list[str] = []
    if include_heading and heading:
        lines.extend([f"## {heading}", ""])
    if not rows:
        lines.extend(["No items.", ""])
        return lines
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(markdown_cell(row.get(header, "")) for header in headers) + " |")
    lines.append("")
    return lines


def next_workflows(audit: dict[str, Any]) -> list[str]:
    workflows = [item for item in audit.get("recommended_workflows", []) if item]
    for workflow in ["adp-meeting-sync", "adp-status-sync"]:
        if workflow not in workflows:
            workflows.append(workflow)
    return workflows


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    path = Path(raw_memory_root)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_project_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_output_dir(
    raw_output_dir: str | None,
    meeting_pack_output_path: str,
    run_folder_pattern: str,
    project_root: Path,
    memory_root: Path,
    scenario: str,
    pack_date: date,
) -> Path:
    tokens = {
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "scenario": scenario,
        "date": pack_date.isoformat(),
    }
    if not raw_output_dir:
        base = resolve_templated_path(meeting_pack_output_path, memory_root, tokens)
        folder = render_template(run_folder_pattern, tokens).strip()
        return (base / folder).resolve() if folder else base.resolve()
    return resolve_templated_path(raw_output_dir, memory_root, tokens)


def resolve_templated_path(raw_path: str, base: Path, tokens: dict[str, str]) -> Path:
    path = Path(render_template(raw_path, tokens))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def render_template(value: str, tokens: dict[str, str]) -> str:
    rendered = value
    for key, replacement in tokens.items():
        rendered = rendered.replace("{" + key + "}", replacement)
    return rendered


def resolve_audit_output_dir(raw_output_dir: str | None, memory_root: Path) -> Path:
    if not raw_output_dir:
        return memory_root / "audits"
    path = Path(raw_output_dir)
    if not path.is_absolute():
        path = memory_root / path
    return path.resolve()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def rel_to_memory(memory_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(memory_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def heading_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def extract_colon_field(text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def section_text(text: str, heading: str) -> str:
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
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= start_level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def parse_first_table(lines: list[str]) -> list[dict[str, str]]:
    table = [line.strip() for line in lines if line.strip().startswith("|")]
    if len(table) < 2:
        return []
    headers = [normalize_header(cell) for cell in split_markdown_row(table[0])]
    rows: list[dict[str, str]] = []
    for line in table[1:]:
        cells = split_markdown_row(line)
        if all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        if any(has_value(value) for value in row.values()):
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


def normalize_header(value: str) -> str:
    value = value.lower().replace("_", " ")
    value = re.sub(r"[^a-z0-9/ ]+", " ", value)
    return " ".join(value.split())


def first_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(normalize_header(name), "")
        if value:
            return value.strip()
    return ""


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_or_tbd(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text if text else "TBD"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def has_value(value: Any) -> bool:
    text = clean(value).strip("`")
    return text.lower() not in PLACEHOLDERS and not (text.startswith("{{") and text.endswith("}}"))


def first_meaningful(*values: Any) -> str:
    for value in values:
        if has_value(value):
            return clean(value)
    return "TBD"


def normalized_status(value: Any) -> str:
    return clean(value).lower()


def normalize_business_decision_status(value: Any) -> str:
    status = normalized_status(value).strip("`")
    return re.sub(r"[\s_]+", "-", status)


def is_closed_business_decision_status(value: Any) -> bool:
    return normalize_business_decision_status(value) in BUSINESS_DECISION_CLOSED_STATUSES


def has_typed_meeting_origin(item: dict[str, Any]) -> bool:
    origin_type = clean(item.get("Origin Type") or item.get("origin_type")).lower()
    return origin_type == "meeting" or has_value(item.get("Meeting ID") or item.get("meeting_id"))


def has_canonical_meetings_segment(value: Any) -> bool:
    path = clean(value).split("#", 1)[0].replace("\\", "/")
    return any(part.lower() == "meetings" for part in path.split("/") if part)


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "TBD"
    return text_or_tbd(value)


def status_sort(value: str) -> int:
    return {"blocked": 0, "in-progress": 1, "open": 2}.get(value.lower(), 3)


def markdown_cell(value: Any) -> str:
    text = text_or_tbd(value)
    return text.replace("\n", " ").replace("|", "\\|")


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


def decode_process_output(raw: bytes) -> str:
    for encoding in ["utf-8-sig", sys.getdefaultencoding(), "mbcs"]:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


if __name__ == "__main__":
    sys.exit(main())
