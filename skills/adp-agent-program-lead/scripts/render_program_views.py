#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Render Program Lead derived views after the ADP state audit gate."""

from __future__ import annotations

import argparse
import json
import locale
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
DEFAULT_PREPASS_SCRIPT = SKILL_ROOT / "scripts" / "adp-state-prepass.py"
DEFAULT_AUDIT_SCRIPT = SKILLS_ROOT / "adp-state-audit" / "scripts" / "audit_state.py"
PLACEHOLDERS = {"", "-", "tbd", "todo", "none", "n/a", "na", "unknown", "see cross-workstream links"}
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate views/project-lead.md and/or views/weekly-report.md from ADP "
            "shared memory. Runs or consumes adp-state-audit first and marks reports "
            "RED when the audit gate has blocking findings."
        )
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--view", choices=["all", "project-lead", "weekly-report"], default="all")
    parser.add_argument(
        "--memory-root",
        default=DEFAULT_MEMORY_ROOT,
        help=f"ADP state root, relative to project root unless absolute. Default: {DEFAULT_MEMORY_ROOT}.",
    )
    parser.add_argument("--prepass-json", help="Existing prepass JSON to render from instead of running the prepass.")
    parser.add_argument("--audit-json", help="Existing audit JSON to use as the gate for every rendered view.")
    parser.add_argument("--prepass-script", default=str(DEFAULT_PREPASS_SCRIPT), help="Path to adp-state-prepass.py.")
    parser.add_argument("--audit-script", default=str(DEFAULT_AUDIT_SCRIPT), help="Path to adp-state-audit audit_state.py.")
    parser.add_argument("--output-dir", help="Output directory. Default: <memory-root>/views.")
    parser.add_argument("--as-of", help="Render date, YYYY-MM-DD. Default: today.")
    parser.add_argument("--period", help="Weekly report period label. Default: ISO week containing --as-of.")
    parser.add_argument("--audience", default="Project leadership", help="Weekly report audience label.")
    parser.add_argument("--max-actions", type=int, default=20, help="Maximum next actions to render.")
    parser.add_argument("--max-workstreams", type=int, default=50, help="Maximum workstreams to render.")
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
    if result.get("ok"):
        return 0
    return 1 if result.get("status") == "blocked" else 2


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return {"ok": False, "status": "error", "error": "project_root is not an existing directory", "project_root": str(project_root)}

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    memory_root = resolve_memory_root(project_root, args.memory_root)
    output_dir = resolve_output_dir(args.output_dir, memory_root)
    views = resolve_views(args.view)

    prepass = load_json(Path(args.prepass_json)) if args.prepass_json else run_prepass(args, project_root, as_of)
    if not prepass.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "error": prepass.get("error", "prepass failed"),
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflow": prepass.get("recommended_workflow") or "adp-project-kickoff",
        }

    memory_root = Path(prepass.get("memory_root") or memory_root).resolve()
    output_dir = resolve_output_dir(args.output_dir, memory_root)
    if not memory_root.exists() or not memory_root.is_dir():
        return {
            "ok": False,
            "status": "blocked",
            "error": "ADP memory root is missing; run adp-project-kickoff or pass --memory-root",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "recommended_workflow": "adp-project-kickoff",
        }

    audit_by_view = audit_gate_by_view(args, project_root, memory_root, prepass, views, as_of)
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: dict[str, str] = {}
    if "project-lead" in views:
        path = output_dir / "project-lead.md"
        path.write_text(
            render_project_lead(prepass, audit_by_view["project-lead"], as_of, args.max_workstreams, args.max_actions),
            encoding="utf-8",
            newline="\n",
        )
        outputs["project_lead"] = str(path)
    if "weekly-report" in views:
        path = output_dir / "weekly-report.md"
        path.write_text(
            render_weekly_report(prepass, audit_by_view["weekly-report"], as_of, args.period, args.audience, args.max_actions),
            encoding="utf-8",
            newline="\n",
        )
        outputs["weekly_report"] = str(path)

    return {
        "ok": True,
        "status": "complete",
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "outputs": outputs,
        "audit_status_by_view": {view: audit_status(audit) for view, audit in audit_by_view.items()},
        "audit_outputs_by_view": {view: audit.get("outputs", {}) for view, audit in audit_by_view.items()},
        "counts": {
            "workstreams": len(prepass.get("workstreams", [])),
            "actions": len(prepass.get("actions", [])),
            "sources_read": len(prepass.get("sources_read", [])),
        },
    }


def run_prepass(args: argparse.Namespace, project_root: Path, as_of: date) -> dict[str, Any]:
    prepass_script = Path(args.prepass_script).resolve()
    if not prepass_script.exists():
        return {"ok": False, "error": f"prepass script not found: {prepass_script}"}
    capability = "Weekly Report Generation" if args.view == "weekly-report" else "Global Project Readout"
    command = [
        sys.executable,
        str(prepass_script),
        str(project_root),
        "--capability",
        capability,
        "--memory-root",
        args.memory_root,
        "--as-of",
        as_of.isoformat(),
    ]
    completed = subprocess.run(command, capture_output=True)
    stdout = decode_process_output(completed.stdout)
    stderr = decode_process_output(completed.stderr)
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "error": (stderr or stdout or "prepass emitted invalid JSON").strip()}
    if completed.returncode != 0 and payload.get("ok") is not True:
        payload.setdefault("ok", False)
        payload.setdefault("error", stderr.strip() or "prepass failed")
    return payload


def audit_gate_by_view(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    prepass: dict[str, Any],
    views: list[str],
    as_of: date,
) -> dict[str, dict[str, Any]]:
    if args.audit_json:
        audit = load_json(Path(args.audit_json))
        return {view: audit for view in views}

    audit_script = Path(args.audit_script).resolve()
    if not audit_script.exists():
        raise FileNotFoundError(f"audit script not found: {audit_script}")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix="-adp-prepass.json", delete=False) as handle:
        json.dump(prepass, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        prepass_path = Path(handle.name)
    try:
        return {
            view: run_audit_script(args, project_root, memory_root, audit_script, prepass_path, view, as_of)
            for view in views
        }
    finally:
        prepass_path.unlink(missing_ok=True)


def run_audit_script(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    audit_script: Path,
    prepass_path: Path,
    view: str,
    as_of: date,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(audit_script),
        str(project_root),
        "--scenario",
        view,
        "--memory-root",
        str(memory_root),
        "--prepass-json",
        str(prepass_path),
        "--as-of",
        as_of.isoformat(),
    ]
    completed = subprocess.run(command, capture_output=True)
    stdout = decode_process_output(completed.stdout)
    stderr = decode_process_output(completed.stderr)
    try:
        result = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"audit emitted invalid JSON for {view}: {stderr or stdout}") from exc
    if completed.returncode != 0 or not result.get("ok"):
        raise RuntimeError(result.get("error") or stderr.strip() or f"audit failed for {view}")
    output_json = result.get("outputs", {}).get("json")
    if output_json and Path(output_json).exists():
        return load_json(Path(output_json))
    return result


def render_project_lead(
    prepass: dict[str, Any],
    audit: dict[str, Any],
    as_of: date,
    max_workstreams: int,
    max_actions: int,
) -> str:
    workstreams = list(prepass.get("workstreams", []))
    actions = active_actions(prepass)[:max_actions]
    status_counts = Counter(normalized_status(ws.get("status", "")) for ws in workstreams)
    blocked = blocked_workstreams(workstreams)
    audit_label = audit_gate_label(audit)
    lines = [
        "# Project Lead View",
        "",
        f"Generated: {generated_at(as_of)}",
        f"Audit gate: {audit_label}",
        f"Memory root: `{prepass.get('memory_root', '')}`",
        "",
        "This is a derived Program Lead view, not a source of truth. Durable updates must go through the owning ADP workflow.",
        "",
        "## Global Status",
        "",
        f"- Workstreams scanned: {len(workstreams)}",
        f"- Active next actions: {len(active_actions(prepass))}",
        f"- Blocked or at-risk workstreams: {len(blocked)}",
        f"- Status distribution: {format_counts(status_counts)}",
        f"- Recommended workflows: {format_workflows(audit)}",
        "",
        "## Workstream Health",
        "",
    ]
    add_table(
        lines,
        ["Workstream", "Status", "Owner", "Progress", "Blockers", "Next action", "Source"],
        [
            {
                "Workstream": ws.get("id", ""),
                "Status": ws.get("status", ""),
                "Owner": ws.get("owner", ""),
                "Progress": ws.get("progress", ""),
                "Blockers": ws.get("blockers", ""),
                "Next action": ws.get("next_actions", ""),
                "Source": ws.get("record", ""),
            }
            for ws in workstreams[:max_workstreams]
        ],
    )
    lines.extend(["## Top Risks And Dependencies", ""])
    risk_rows = risk_dependency_rows(workstreams)
    add_table(lines, ["Workstream", "Owner", "Risk / Dependency", "Source"], risk_rows[:max_workstreams])
    lines.extend(["## Decisions And Escalations", ""])
    add_table(lines, ["Source", "Owner", "Reason", "Recommended workflow"], decision_escalation_rows(audit))
    lines.extend(["## Readiness And Evidence Gaps", ""])
    add_table(lines, ["Source", "Workstream", "Gap", "Recommended workflow"], readiness_gap_rows(audit))
    lines.extend(["## Next Actions", ""])
    add_table(lines, ["Action", "Owner", "Workstream", "Due / Trigger", "Source"], action_rows(actions))
    lines.extend(["## Source Inventory", ""])
    lines.extend(source_inventory_lines(prepass, audit))
    return "\n".join(lines)


def render_weekly_report(
    prepass: dict[str, Any],
    audit: dict[str, Any],
    as_of: date,
    period: str | None,
    audience: str,
    max_actions: int,
) -> str:
    workstreams = list(prepass.get("workstreams", []))
    actions = active_actions(prepass)[:max_actions]
    blocked = blocked_workstreams(workstreams)
    status_counts = Counter(normalized_status(ws.get("status", "")) for ws in workstreams)
    audit_label = audit_gate_label(audit)
    summary_status = weekly_summary_status(audit)
    lines = [
        "# Weekly Report",
        "",
        f"Period: {period or week_period(as_of)}",
        f"Audience: {display(audience)}",
        f"Generated: {generated_at(as_of)}",
        f"Audit gate: {audit_label}",
        "",
        "This weekly report is a derived view. It summarizes ADP shared memory and does not replace WDR, action ledger, decision, readiness, or evidence sources.",
        "",
        "## Status Summary",
        "",
        f"- Overall status: {summary_status}",
        f"- Workstreams scanned: {len(workstreams)}",
        f"- Status distribution: {format_counts(status_counts)}",
        f"- Blocked or at-risk workstreams: {len(blocked)}",
        f"- Active next actions: {len(active_actions(prepass))}",
        "",
        "## Blocked Workstreams",
        "",
    ]
    add_table(
        lines,
        ["Workstream", "Status", "Owner", "Blockers", "Next action", "Source"],
        [
            {
                "Workstream": ws.get("id", ""),
                "Status": ws.get("status", ""),
                "Owner": ws.get("owner", ""),
                "Blockers": ws.get("blockers", ""),
                "Next action": ws.get("next_actions", ""),
                "Source": ws.get("record", ""),
            }
            for ws in blocked
        ],
    )
    lines.extend(["## Risk And Dependency Changes", ""])
    add_table(lines, ["Workstream", "Owner", "Risk / Dependency", "Source"], risk_dependency_rows(workstreams))
    lines.extend(["## Decisions Needed", ""])
    add_table(lines, ["Source", "Owner", "Reason", "Recommended workflow"], decision_escalation_rows(audit))
    lines.extend(["## Readiness Gaps", ""])
    add_table(lines, ["Source", "Workstream", "Gap", "Recommended workflow"], readiness_gap_rows(audit))
    lines.extend(["## Next Actions", ""])
    add_table(lines, ["Action", "Owner", "Workstream", "Due / Trigger", "Source"], action_rows(actions))
    lines.extend(["## Follow-Up Workflows", ""])
    workflows = audit.get("recommended_workflows", [])
    if workflows:
        lines.extend(f"- `{workflow}`" for workflow in workflows)
    else:
        lines.append("- No audit-driven follow-up workflow found.")
    lines.extend(["", "## Source Inventory", ""])
    lines.extend(source_inventory_lines(prepass, audit))
    return "\n".join(lines)


def active_actions(prepass: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    for action in prepass.get("actions", []):
        status = str(action.get("status", "")).lower()
        if status and status not in ACTIVE_ACTION_STATUSES:
            continue
        actions.append(action)
    return actions


def blocked_workstreams(workstreams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for ws in workstreams:
        status = normalized_status(ws.get("status", ""))
        blockers = str(ws.get("blockers", ""))
        gaps = ws.get("gaps", [])
        if status in {"blocked", "at-risk", "red", "amber"} or is_meaningful(blockers) or gaps:
            results.append(ws)
    return results


def risk_dependency_rows(workstreams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for ws in workstreams:
        source = ws.get("record", "")
        for field in ["risks", "dependencies", "change_notes"]:
            value = ws.get(field, "")
            if is_meaningful(value):
                rows.append(
                    {
                        "Workstream": ws.get("id", ""),
                        "Owner": ws.get("owner", ""),
                        "Risk / Dependency": value,
                        "Source": source,
                    }
                )
    return rows


def decision_escalation_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    findings = audit.get("findings", {})
    closure = findings.get("closure", {}) if isinstance(findings, dict) else {}
    items = [*closure.get("open_business_packets", []), *closure.get("escalation_candidates", [])]
    rows = []
    for item in items:
        rows.append(
            {
                "Source": item.get("source") or item.get("path") or item.get("action_id", ""),
                "Owner": item.get("owner", ""),
                "Reason": item.get("reason") or item.get("gap") or item.get("status", "open decision or escalation item"),
                "Recommended workflow": item.get("recommended_workflow", "adp-risk-dependency-change-review"),
            }
        )
    return rows


def readiness_gap_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    findings = audit.get("findings", {})
    completeness = findings.get("completeness", {}) if isinstance(findings, dict) else {}
    candidates = [
        *completeness.get("missing_evidence_items", []),
        *[
            item
            for item in completeness.get("blocking_gaps", [])
            if str(item.get("field", "")) in {"readiness", "evidence", "l0_references"}
        ],
    ]
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for item in candidates:
        key = (str(item.get("source", "")), str(item.get("workstream", "")), str(item.get("gap", "")))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "Source": item.get("source", ""),
                "Workstream": item.get("workstream", ""),
                "Gap": item.get("gap", ""),
                "Recommended workflow": item.get("recommended_workflow", "adp-status-sync"),
            }
        )
    return rows


def action_rows(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "Action": action.get("action", ""),
            "Owner": action.get("owner", ""),
            "Workstream": action.get("workstream", ""),
            "Due / Trigger": action.get("due_or_trigger", ""),
            "Source": action.get("source", ""),
        }
        for action in actions
    ]


def source_inventory_lines(prepass: dict[str, Any], audit: dict[str, Any]) -> list[str]:
    audit_outputs = audit.get("outputs", {}) if isinstance(audit.get("outputs", {}), dict) else {}
    return [
        f"- Sources read by pre-pass: {len(prepass.get('sources_read', []))}",
        f"- Missing sources: {len(prepass.get('missing_sources', []))}",
        f"- Audit status: {audit_status(audit)}",
        f"- Audit JSON: `{display(audit_outputs.get('json', 'not emitted'))}`",
        "",
    ]


def add_table(lines: list[str], headers: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        lines.extend(["No source-backed items found.", ""])
        return
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(cell(row.get(header, "")) for header in headers) + " |")
    lines.append("")


def audit_gate_label(audit: dict[str, Any]) -> str:
    status = audit_status(audit)
    counts = audit.get("counts", {})
    blocking = int(counts.get("blocking_findings", 0) or 0)
    warnings = int(counts.get("warning_findings", 0) or 0)
    if status == "blocked":
        return f"RED - audit gate has {blocking} blocking finding(s) and {warnings} warning(s)"
    if status == "warning":
        return f"AMBER - audit gate has {warnings} warning(s)"
    if status == "pass":
        return "PASS - no audit findings from scanned sources"
    return display(status)


def weekly_summary_status(audit: dict[str, Any]) -> str:
    status = audit_status(audit)
    if status == "blocked":
        return "RED - audit gate has blocking findings; do not report the program as globally normal."
    if status == "warning":
        return "AMBER - audit warnings require follow-up before treating the report as clean."
    if status == "pass":
        return "No audit-blocking findings from scanned sources."
    return display(status)


def audit_status(audit: dict[str, Any]) -> str:
    return str(audit.get("audit_status") or audit.get("status") or "unknown")


def normalized_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text and text not in PLACEHOLDERS else "missing"


def format_counts(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}: {counter[key]}" for key in sorted(counter))


def format_workflows(audit: dict[str, Any]) -> str:
    workflows = audit.get("recommended_workflows", [])
    if not workflows:
        return "none"
    return ", ".join(f"`{workflow}`" for workflow in workflows)


def week_period(as_of: date) -> str:
    start = as_of - timedelta(days=as_of.weekday())
    end = start + timedelta(days=6)
    return f"{start.isoformat()} to {end.isoformat()}"


def generated_at(as_of: date) -> str:
    now = datetime.now(timezone.utc).astimezone()
    return f"{now.isoformat(timespec='seconds')} (as of {as_of.isoformat()})"


def is_meaningful(value: Any) -> bool:
    text = str(value or "").strip().strip("`")
    return text.lower() not in PLACEHOLDERS


def display(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().strip("`") in PLACEHOLDERS:
        return "missing (audit gap)"
    return text


def cell(value: Any) -> str:
    text = display(value)
    if len(text) > 240:
        text = text[:237].rstrip() + "..."
    return text.replace("\n", " ").replace("|", "\\|")


def resolve_views(raw: str) -> list[str]:
    if raw == "all":
        return ["project-lead", "weekly-report"]
    return [raw]


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    path = Path(raw_memory_root)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_output_dir(raw_output_dir: str | None, memory_root: Path) -> Path:
    if not raw_output_dir:
        return (memory_root / "views").resolve()
    path = Path(raw_output_dir)
    if not path.is_absolute():
        path = memory_root / path
    return path.resolve()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


def decode_process_output(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ["utf-8-sig", locale.getpreferredencoding(False), "mbcs"]:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


if __name__ == "__main__":
    sys.exit(main())
