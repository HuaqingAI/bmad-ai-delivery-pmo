#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Sync one BMM lifecycle checkpoint into an ADP Workstream Delivery Record."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CHECKPOINTS = {"prd", "architecture", "epic-story", "implementation", "validation", "baseline"}
ARTIFACT_LABELS = {
    "prd": "PRD",
    "architecture": "Architecture",
    "epic": "Epics / stories",
    "epics": "Epics / stories",
    "story": "Epics / stories",
    "stories": "Epics / stories",
    "epic-story": "Epics / stories",
    "code": "Code / PR",
    "pr": "Code / PR",
    "implementation": "Code / PR",
    "deploy": "Code / PR",
    "deployment": "Code / PR",
    "validation": "Validation evidence",
    "evidence": "Validation evidence",
    "test": "Validation evidence",
    "tests": "Validation evidence",
}
DEFAULT_ARTIFACT_KEY = {
    "prd": "prd",
    "architecture": "architecture",
    "epic-story": "epics",
    "implementation": "code",
    "validation": "validation",
    "baseline": "artifact",
}
PHASE_LABEL = {
    "prd": "PRD",
    "architecture": "Architecture",
    "epic-story": "Epic / story",
    "implementation": "Implementation",
    "validation": "Validation",
    "baseline": "Baseline update",
}
ARTIFACT_STATUSES = {"draft", "baseline", "superseded", "changed", "linked"}


@dataclass
class ArtifactUpdate:
    label: str
    path: str
    status: str
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync a BMM checkpoint packet into _bmad/memory/adp/workstreams/{id}.",
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--workstream-id", required=True, help="Workstream id. Normalized to lowercase hyphen-case.")
    parser.add_argument("--checkpoint", required=True, choices=sorted(CHECKPOINTS), help="BMM checkpoint type.")
    parser.add_argument("--summary", required=True, help="Project-level checkpoint summary.")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="[KEY=]PATH",
        help="Artifact link, repeatable. Common keys: prd, architecture, epics, code, validation.",
    )
    parser.add_argument(
        "--artifact-status",
        choices=sorted(ARTIFACT_STATUSES),
        default="linked",
        help="Baseline status to write for artifact rows. Default: linked.",
    )
    parser.add_argument("--scope", action="append", default=[], help="Management-level scope implication.")
    parser.add_argument("--acceptance", action="append", default=[], help="Acceptance criterion or acceptance update.")
    parser.add_argument("--evidence-required", action="append", default=[], help="Evidence expectation.")
    parser.add_argument("--open-question", action="append", default=[], help="Open question or pending clarification.")
    parser.add_argument("--dependency", action="append", default=[], help="Dependency workstream or description.")
    parser.add_argument("--impact", action="append", default=[], help="Impacted workstream or description.")
    parser.add_argument("--l0-reference", action="append", default=[], help="L0 reference or constraint.")
    parser.add_argument("--risk", action="append", default=[], help="Risk exposed by this checkpoint.")
    parser.add_argument("--blocker", action="append", default=[], help="Blocker exposed by this checkpoint.")
    parser.add_argument("--milestone", action="append", default=[], help="Milestone or delivery sequence note.")
    parser.add_argument("--next-action", action="append", default=[], help="Owner/action/trigger next action.")
    parser.add_argument("--business-confirmation", action="append", default=[], help="Business/customer confirmation state.")
    parser.add_argument("--change-note", action="append", default=[], help="Baseline, scope, or artifact change note.")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="NAME|TYPE|LINK|CRITERION|STATUS|GAP",
        help="Evidence row, repeatable.",
    )
    parser.add_argument(
        "--decision",
        action="append",
        default=[],
        metavar="TYPE|TEXT|OWNER|IMPACT|STATUS|LINK",
        help="Workstream decision row, repeatable.",
    )
    parser.add_argument(
        "--readiness-gap",
        action="append",
        default=[],
        metavar="GAP|DIMENSION|OWNER|ACTION|DUE|ESCALATION",
        help="Readiness gap row, repeatable.",
    )
    parser.add_argument("--record-status", choices=["draft", "gap", "ready"], help="Intentional ADP status update.")
    parser.add_argument(
        "--memory-root",
        default="_bmad/memory/adp",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad/memory/adp.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without changing files.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def normalize_id(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if not value:
        raise ValueError("workstream id must contain at least one letter or digit")
    return value


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def table_cell(value: str) -> str:
    cleaned = " ".join(str(value).split())
    return cleaned.replace("|", "\\|") if cleaned else "TBD"


def compact_list(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = " ".join(str(item).split())
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def split_pipe(raw: str, expected: int) -> list[str]:
    parts = [part.strip() for part in raw.split("|")]
    while len(parts) < expected:
        parts.append("TBD")
    return [part or "TBD" for part in parts[:expected]]


def section_bounds(text: str, heading: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
    match = pattern.search(text)
    if not match:
        return None
    next_heading = re.search(r"(?m)^##\s+", text[match.end() :])
    end = match.end() + next_heading.start() if next_heading else len(text)
    return match.start(), end


def replace_section(text: str, heading: str, new_section: str) -> str:
    bounds = section_bounds(text, heading)
    if bounds is None:
        return text.rstrip() + "\n\n" + new_section.rstrip() + "\n"
    start, end = bounds
    return text[:start] + new_section.rstrip() + "\n\n" + text[end:].lstrip("\n")


def get_section(text: str, heading: str) -> str:
    bounds = section_bounds(text, heading)
    if bounds is None:
        return ""
    start, end = bounds
    return text[start:end].rstrip()


def merge_value(existing: str, additions: list[str]) -> str:
    additions = compact_list(additions)
    if not additions:
        return existing
    stripped = existing.strip()
    if not stripped or stripped.upper() == "TBD":
        return "; ".join(additions)
    result = stripped
    for addition in additions:
        if addition not in result:
            result += "; " + addition
    return result


def update_bullet(section: str, label: str, additions: list[str]) -> str:
    additions = compact_list(additions)
    if not additions:
        return section
    pattern = re.compile(rf"(?m)^- {re.escape(label)}:\s*(.*)$")
    match = pattern.search(section)
    if match:
        replacement = f"- {label}: {merge_value(match.group(1), additions)}"
        return section[: match.start()] + replacement + section[match.end() :]
    return section.rstrip() + f"\n- {label}: {'; '.join(additions)}"


def update_identity(section: str, checkpoint: str, record_status: str | None) -> str:
    section = update_bullet(section, "Current BMM phase", [PHASE_LABEL[checkpoint]])
    if record_status:
        pattern = re.compile(r"(?m)^- Current ADP status:\s*(.*)$")
        replacement = f"- Current ADP status: {record_status}"
        if pattern.search(section):
            section = pattern.sub(replacement, section)
        else:
            section = section.rstrip() + "\n" + replacement
    return section


def artifact_label(key: str) -> str:
    normalized = key.strip().lower()
    return ARTIFACT_LABELS.get(normalized, key.strip() or "Artifact")


def parse_artifacts(items: list[str], checkpoint: str, status: str, note: str) -> tuple[list[ArtifactUpdate], list[str]]:
    updates: list[ArtifactUpdate] = []
    warnings: list[str] = []
    for item in items:
        raw = item.strip()
        if not raw:
            continue
        if "=" in raw:
            key, path = raw.split("=", 1)
            key = key.strip()
            path = path.strip()
        else:
            key = DEFAULT_ARTIFACT_KEY[checkpoint]
            path = raw
        if not path:
            warnings.append(f"ignored artifact with empty path: {item}")
            continue
        updates.append(ArtifactUpdate(artifact_label(key), path, status, note))
    return updates, warnings


def artifact_row(update: ArtifactUpdate) -> str:
    return (
        f"| {table_cell(update.label)} | {table_cell(update.path)} | "
        f"{table_cell(update.status)} | {table_cell(update.notes)} |"
    )


def update_artifact_index(section: str, updates: list[ArtifactUpdate]) -> str:
    if not updates:
        return section
    lines = section.splitlines()
    if not any(line.startswith("|") for line in lines):
        lines.extend(
            [
                "",
                "| Artifact | Path / Link | Baseline Status | Notes |",
                "| --- | --- | --- | --- |",
            ],
        )
    existing: dict[str, int] = {}
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] in {"Artifact", "---"}:
            continue
        existing[cells[0]] = index
    append_after = max((i for i, line in enumerate(lines) if line.startswith("|")), default=len(lines) - 1)
    for update in updates:
        row = artifact_row(update)
        if update.label in existing:
            lines[existing[update.label]] = row
        else:
            append_after += 1
            lines.insert(append_after, row)
            existing[update.label] = append_after
    return "\n".join(lines)


def append_bullets_under_label(section: str, label: str, additions: list[str]) -> str:
    additions = compact_list(additions)
    if not additions:
        return section
    lines = section.splitlines()
    label_index = next((i for i, line in enumerate(lines) if line.strip() == f"{label}:"), None)
    if label_index is None:
        lines.extend(["", f"{label}:"])
        label_index = len(lines) - 1
    insert_at = label_index + 1
    while insert_at < len(lines):
        stripped = lines[insert_at].strip()
        if stripped.endswith(":") and not stripped.startswith("-"):
            break
        insert_at += 1
    existing = {line.strip()[2:] for line in lines[label_index + 1 : insert_at] if line.strip().startswith("- ")}
    if "TBD" in existing:
        lines = [
            line
            for idx, line in enumerate(lines)
            if not (label_index < idx < insert_at and line.strip() == "- TBD")
        ]
        insert_at -= 1
        existing.remove("TBD")
    new_lines = [f"- {item}" for item in additions if item not in existing and item != "TBD"]
    if not new_lines:
        return "\n".join(lines)
    if insert_at == label_index + 1 and insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    lines[insert_at:insert_at] = new_lines
    return "\n".join(lines)


def parse_owner(record_text: str) -> str:
    match = re.search(r"(?m)^- FDE owner:\s*(.+)$", record_text)
    if match and match.group(1).strip() and match.group(1).strip().upper() != "TBD":
        return match.group(1).strip()
    return "FDE owner"


def default_gaps(args: argparse.Namespace, owner: str, artifacts: list[ArtifactUpdate]) -> list[list[str]]:
    gaps: list[list[str]] = []
    if args.checkpoint == "prd":
        if not args.acceptance:
            gaps.append(["Acceptance criteria not captured from PRD checkpoint", "Acceptance clarity", owner, "Extract project-level acceptance criteria from the PRD", "Before readiness review", "Project lead if unresolved"])
        if not args.business_confirmation:
            gaps.append(["Business confirmation status not captured from PRD checkpoint", "Acceptance clarity", owner, "Confirm acceptance owner or business sign-off path", "Before validation checkpoint", "Project lead if unresolved"])
    elif args.checkpoint == "architecture":
        if not args.dependency and not args.l0_reference:
            gaps.append(["Architecture dependencies or L0 impacts need confirmation", "Dependency clarity", owner, "Confirm cross-line dependencies and L0 contract references", "Before epic/story planning", "Project lead if unresolved"])
    elif args.checkpoint == "epic-story":
        if not args.milestone and not args.next_action:
            gaps.append(["Delivery milestones or next actions need confirmation", "Next-action executability", owner, "Name milestone sequence and next owner action", "Before implementation", "Project lead if unresolved"])
    elif args.checkpoint == "implementation":
        if not args.evidence and not any(item.label == "Code / PR" for item in artifacts):
            gaps.append(["Implementation evidence links need confirmation", "Evidence completeness", owner, "Link PR, deployment, or implementation proof", "Before validation", "Project lead if unresolved"])
    elif args.checkpoint == "validation":
        if not args.evidence:
            gaps.append(["Validation evidence rows need confirmation", "Evidence completeness", owner, "Map validation proof to acceptance criteria", "Before acceptance review", "Project lead if unresolved"])
        if not args.business_confirmation:
            gaps.append(["Business or customer confirmation status needs confirmation", "Acceptance clarity", owner, "Confirm acceptance result or owner", "Before acceptance review", "Project lead if unresolved"])
    elif args.checkpoint == "baseline":
        if not args.change_note and not args.decision:
            gaps.append(["Baseline change note or decision is missing", "BMM artifact completeness", owner, "Record why the artifact baseline changed", "Before downstream status reporting", "Project lead if unresolved"])
    return gaps


def ensure_table_file(path: Path, title: str, header: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{header}\n", encoding="utf-8", newline="\n")
    return True


def append_table_rows(path: Path, rows: list[str], dry_run: bool, title: str, header: str) -> tuple[list[str], bool]:
    if not rows:
        return [], False
    created = False
    if not dry_run:
        created = ensure_table_file(path, title, header)
        text = path.read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n\n{header}\n"
    added: list[str] = []
    for row in rows:
        if row not in text:
            added.append(row)
            text = text.rstrip() + "\n" + row + "\n"
    if added and not dry_run:
        path.write_text(text, encoding="utf-8", newline="\n")
    return added, created


def readiness_rows(explicit: list[str], generated: list[list[str]]) -> list[str]:
    rows: list[str] = []
    for raw in explicit:
        gap, dimension, owner, action, due, escalation = split_pipe(raw, 6)
        rows.append(
            f"| {table_cell(gap)} | {table_cell(dimension)} | {table_cell(owner)} | "
            f"{table_cell(action)} | {table_cell(due)} | {table_cell(escalation)} |",
        )
    for gap, dimension, owner, action, due, escalation in generated:
        rows.append(
            f"| {table_cell(gap)} | {table_cell(dimension)} | {table_cell(owner)} | "
            f"{table_cell(action)} | {table_cell(due)} | {table_cell(escalation)} |",
        )
    return rows


def explicit_gap_labels(raw_items: list[str]) -> list[str]:
    labels: list[str] = []
    for raw in raw_items:
        gap = split_pipe(raw, 6)[0]
        if gap != "TBD":
            labels.append(gap)
    return labels


def evidence_rows(raw_items: list[str]) -> list[str]:
    rows: list[str] = []
    for raw in raw_items:
        name, evidence_type, link, criterion, status, gap = split_pipe(raw, 6)
        rows.append(
            f"| {table_cell(name)} | {table_cell(evidence_type)} | {table_cell(link)} | "
            f"{table_cell(criterion)} | {table_cell(status)} | {table_cell(gap)} |",
        )
    return rows


def decision_rows(raw_items: list[str], date_str: str) -> list[str]:
    rows: list[str] = []
    for raw in raw_items:
        decision_type, text, owner, impact, status, link = split_pipe(raw, 6)
        rows.append(
            f"| {date_str} | {table_cell(decision_type)} | {table_cell(text)} | {table_cell(owner)} | "
            f"{table_cell(impact)} | {table_cell(status)} | {table_cell(link)} |",
        )
    return rows


def checkpoint_log_section(record_text: str, args: argparse.Namespace, artifacts: list[ArtifactUpdate], gaps: list[list[str]], timestamp: str) -> str:
    lines = [
        f"### {timestamp} {args.checkpoint}",
        f"- Summary: {args.summary}",
    ]
    if artifacts:
        lines.append("- Artifacts: " + "; ".join(f"{item.label}={item.path}" for item in artifacts))
    if args.change_note:
        lines.append("- Change notes: " + "; ".join(compact_list(args.change_note)))
    if gaps:
        lines.append("- Visible gaps: " + "; ".join(row[0] for row in gaps))
    if args.next_action:
        lines.append("- Next actions: " + "; ".join(compact_list(args.next_action)))
    entry = "\n".join(lines)
    section = get_section(record_text, "Checkpoint Sync Log")
    if not section:
        return record_text.rstrip() + "\n\n## Checkpoint Sync Log\n\n" + entry + "\n"
    if entry in section:
        return record_text
    updated_section = section.rstrip() + "\n\n" + entry
    return replace_section(record_text, "Checkpoint Sync Log", updated_section)


def update_record(record_text: str, args: argparse.Namespace, artifacts: list[ArtifactUpdate], gaps: list[list[str]], timestamp: str) -> str:
    identity = get_section(record_text, "Identity")
    if identity:
        record_text = replace_section(record_text, "Identity", update_identity(identity, args.checkpoint, args.record_status))

    artifact_index = get_section(record_text, "BMM Artifact Index")
    if artifact_index:
        record_text = replace_section(record_text, "BMM Artifact Index", update_artifact_index(artifact_index, artifacts))

    scope = get_section(record_text, "Scope")
    if scope:
        scope = update_bullet(scope, "In scope", args.scope)
        scope = update_bullet(scope, "Open questions", args.open_question)
        record_text = replace_section(record_text, "Scope", scope)

    acceptance = get_section(record_text, "Acceptance")
    if acceptance:
        acceptance = update_bullet(acceptance, "Acceptance criteria", args.acceptance)
        acceptance = update_bullet(acceptance, "Evidence required", args.evidence_required)
        acceptance = update_bullet(acceptance, "Current readiness", [args.record_status] if args.record_status else [])
        acceptance = update_bullet(acceptance, "Unclosed gaps", [gap[0] for gap in gaps] + explicit_gap_labels(args.readiness_gap))
        record_text = replace_section(record_text, "Acceptance", acceptance)

    status = get_section(record_text, "Project Status")
    if status:
        status = update_bullet(status, "Progress", [args.summary])
        status = update_bullet(status, "Blockers", args.blocker)
        status = update_bullet(status, "Risks", args.risk)
        status = update_bullet(status, "Dependencies", args.dependency)
        status = update_bullet(status, "Scope or change notes", args.change_note)
        status = update_bullet(status, "Next actions", args.next_action + args.milestone)
        record_text = replace_section(record_text, "Project Status", status)

    links = get_section(record_text, "Cross-Workstream Links")
    if links:
        links = append_bullets_under_label(links, "Depends on", args.dependency)
        links = append_bullets_under_label(links, "Impacts", args.impact)
        links = append_bullets_under_label(links, "L0 references", args.l0_reference)
        record_text = replace_section(record_text, "Cross-Workstream Links", links)

    decisions_evidence = get_section(record_text, "Decisions and Evidence")
    if decisions_evidence:
        decisions_evidence = update_bullet(decisions_evidence, "Customer/business confirmations", args.business_confirmation)
        record_text = replace_section(record_text, "Decisions and Evidence", decisions_evidence)

    return checkpoint_log_section(record_text, args, artifacts, gaps, timestamp)


def append_daily_log(memory_root: Path, args: argparse.Namespace, workstream_id: str, artifacts: list[ArtifactUpdate], gaps: list[list[str]], timestamp: str, dry_run: bool) -> Path:
    date_str = timestamp[:10]
    path = memory_root / "daily" / f"{date_str}.md"
    lines = [
        f"## {timestamp} adp-bmm-checkpoint-sync",
        "",
        f"- Workstream: {workstream_id}",
        f"- Checkpoint: {args.checkpoint}",
        f"- Summary: {args.summary}",
    ]
    if artifacts:
        lines.append("- Artifacts: " + "; ".join(f"{item.label}={item.path}" for item in artifacts))
    if gaps:
        lines.append("- Visible gaps: " + "; ".join(row[0] for row in gaps))
    if args.next_action:
        lines.append("- Next actions: " + "; ".join(compact_list(args.next_action)))
    entry = "\n".join(lines) + "\n"
    if dry_run:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if entry not in text:
            path.write_text(text.rstrip() + "\n\n" + entry, encoding="utf-8", newline="\n")
    else:
        path.write_text(f"# Daily Log - {date_str}\n\n" + entry, encoding="utf-8", newline="\n")
    return path


def next_actions_for(args: argparse.Namespace, gaps: list[list[str]]) -> list[str]:
    actions: list[str] = []
    if gaps or args.evidence or args.acceptance:
        actions.append("Run adp-acceptance-readiness-review when acceptance or evidence coverage needs scoring.")
    if args.risk or args.dependency or args.change_note or args.decision:
        actions.append("Run adp-risk-dependency-change-review for risks, dependencies, changes, or decisions that need normalization.")
    if args.checkpoint in {"prd", "architecture", "epic-story", "implementation"}:
        actions.append("Run this workflow again after the next BMM lifecycle checkpoint is ready.")
    if args.checkpoint == "validation":
        actions.append("Prepare stakeholder acceptance or cutover readiness review once evidence gaps are owned.")
    return actions or ["Use adp-status-sync for lightweight updates between BMM checkpoints."]


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        emit({"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}, args.output)
        return 2
    try:
        workstream_id = normalize_id(args.workstream_id)
    except ValueError as exc:
        emit({"ok": False, "error": str(exc), "raw_workstream_id": args.workstream_id}, args.output)
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    workstream_root = memory_root / "workstreams" / workstream_id
    record_path = workstream_root / "delivery-record.md"
    if not record_path.exists():
        emit(
            {
                "ok": False,
                "error": "delivery-record.md not found; run adp-workstream-register first",
                "workstream_root": str(workstream_root),
                "record_path": str(record_path),
            },
            args.output,
        )
        return 2

    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    date_str = timestamp[:10]
    note = "; ".join(compact_list(args.change_note)) or f"{args.checkpoint} checkpoint sync {timestamp}"
    artifacts, artifact_warnings = parse_artifacts(args.artifact, args.checkpoint, args.artifact_status, note)
    record_before = record_path.read_text(encoding="utf-8")
    owner = parse_owner(record_before)
    generated_gaps = default_gaps(args, owner, artifacts)
    record_after = update_record(record_before, args, artifacts, generated_gaps, timestamp)

    evidence_path = workstream_root / "evidence.md"
    decisions_path = workstream_root / "decisions.md"
    readiness_path = workstream_root / "readiness.md"
    daily_path = memory_root / "daily" / f"{date_str}.md"

    evidence_added, evidence_created = append_table_rows(
        evidence_path,
        evidence_rows(args.evidence),
        args.dry_run,
        "Evidence Index",
        "| Evidence | Type | Link | Acceptance Criterion | Confirmation Status | Gap |\n| --- | --- | --- | --- | --- | --- |",
    )
    decision_added, decision_created = append_table_rows(
        decisions_path,
        decision_rows(args.decision, date_str),
        args.dry_run,
        "Workstream Decisions",
        "| Date | Type | Decision / Question | Owner | Impact | Status | Link |\n| --- | --- | --- | --- | --- | --- | --- |",
    )
    readiness_added, readiness_created = append_table_rows(
        readiness_path,
        readiness_rows(args.readiness_gap, generated_gaps),
        args.dry_run,
        "Readiness",
        "| Gap | Dimension | Owner | Action | Due / Trigger | Escalation |\n| --- | --- | --- | --- | --- | --- |",
    )

    files_updated: list[str] = []
    files_planned: list[str] = []
    if record_after != record_before:
        if args.dry_run:
            files_planned.append(str(record_path))
        else:
            record_path.write_text(record_after, encoding="utf-8", newline="\n")
            files_updated.append(str(record_path))
    if evidence_added or evidence_created:
        (files_planned if args.dry_run else files_updated).append(str(evidence_path))
    if decision_added or decision_created:
        (files_planned if args.dry_run else files_updated).append(str(decisions_path))
    if readiness_added or readiness_created:
        (files_planned if args.dry_run else files_updated).append(str(readiness_path))

    daily_path = append_daily_log(memory_root, args, workstream_id, artifacts, generated_gaps, timestamp, args.dry_run)
    if args.dry_run:
        files_planned.append(str(daily_path))
    else:
        files_updated.append(str(daily_path))

    warnings = artifact_warnings
    if args.checkpoint == "baseline" and any(item.label == "Artifact" for item in artifacts):
        warnings.append("baseline artifact was provided without a key; use --artifact prd=... or another key when possible")
    if args.verbose:
        print(f"Using workstream root: {workstream_root}", file=sys.stderr)

    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "workstream_id": workstream_id,
        "workstream_root": str(workstream_root),
        "checkpoint": args.checkpoint,
        "files_updated": compact_list(files_updated),
        "files_planned": compact_list(files_planned),
        "artifact_updates": [update.__dict__ for update in artifacts],
        "evidence_added": evidence_added,
        "decisions_added": decision_added,
        "readiness_gaps_added": readiness_added,
        "generated_gaps": [gap[0] for gap in generated_gaps],
        "daily_log": str(daily_path),
        "warnings": warnings,
        "next_actions": next_actions_for(args, generated_gaps),
    }
    emit(result, args.output)
    return 0


def emit(result: dict, output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


if __name__ == "__main__":
    sys.exit(main())
