#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Inventory ADP shared project state for Program Lead synthesis."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


PLACEHOLDERS = {
    "",
    "-",
    "tbd",
    "todo",
    "none",
    "n/a",
    "na",
    "unknown",
    "see cross-workstream links",
}

CAPABILITY_FILES = {
    "global project readout": {
        "core",
        "workstreams",
        "actions",
        "daily",
        "decisions",
        "l0",
        "views",
    },
    "fde action list": {"core", "workstreams", "actions", "daily"},
    "acceptance readiness view": {"core", "workstreams", "l0", "views"},
    "risk and dependency synthesis": {"core", "workstreams", "decisions", "l0", "views"},
    "weekly report generation": {"core", "workstreams", "daily", "decisions", "l0", "views"},
    "gap-driven coaching": {"core", "workstreams"},
    "l0 impact sweep": {"core", "workstreams", "l0"},
    "decision closure review": {"core", "workstreams", "daily", "decisions", "meetings"},
}

CORE_FILES = ["index.md", "project-charter.md", "cadence.md"]
L0_FILES = [
    "reference-index.md",
    "extracted-freeze-model.md",
    "extracted-contract-inventory.md",
    "extracted-gates.md",
    "extracted-nfr.md",
    "extracted-evidence-rules.md",
    "extracted-impacts.md",
    "extracted-decision-gates.md",
    "exceptions-and-open-questions.md",
]
VIEW_FILES = [
    "project-lead.md",
    "fde-actions.md",
    "acceptance-readiness.md",
    "risk-matrix.md",
    "dependency-map.md",
    "weekly-report.md",
]
ACTION_LEDGER_REL = Path("actions") / "action-ledger.md"
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}


@dataclass
class Workstream:
    workstream_id: str
    path: Path
    name: str = "TBD"
    owner: str = "TBD"
    business_owner: str = "TBD"
    phase: str = "TBD"
    status: str = "TBD"
    progress: str = "TBD"
    blockers: str = "TBD"
    risks: str = "TBD"
    dependencies: str = "TBD"
    change_notes: str = "TBD"
    next_actions: str = "TBD"
    last_status_sync: str = "TBD"
    depends_on: list[str] = field(default_factory=list)
    impacts: list[str] = field(default_factory=list)
    l0_references: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch, parse, count, and cross-reference ADP state before Program Lead "
            "readout synthesis. Emits JSON only; makes no readiness or severity judgment."
        )
    )
    parser.add_argument("project_root", help="Project root containing ADP state.")
    parser.add_argument("--capability", default="", help="Capability or readout name to scope file discovery.")
    parser.add_argument("--workstream", action="append", default=[], help="Workstream id to include. Repeatable.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP state root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--max-age-days", type=int, default=7, help="Staleness threshold for WDR syncs. Default: 7.")
    parser.add_argument("--as-of", help="Date for staleness checks, YYYY-MM-DD. Default: today.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def normalize_id(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def normalize_capability(raw: str) -> str:
    value = re.sub(r"\s+", " ", raw.strip().lower())
    return value


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def is_meaningful(value: Any) -> bool:
    text = str(value or "").strip().strip("`")
    return text.lower() not in PLACEHOLDERS


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def rel_to_memory(memory_root: Path, path: Path) -> str:
    try:
        return path.relative_to(memory_root).as_posix()
    except ValueError:
        return path.as_posix()


def section(lines: list[str], heading: str) -> list[str]:
    start = None
    marker = f"## {heading}".lower()
    for index, line in enumerate(lines):
        if line.strip().lower() == marker:
            start = index + 1
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return lines[start:end]


def parse_key_bullets(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, value = stripped[2:].split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def parse_list_after_label(lines: list[str], label: str) -> list[str]:
    out: list[str] = []
    active = False
    label_marker = f"{label}:".lower()
    section_labels = {"depends on:", "impacts:", "l0 references:"}
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower == label_marker:
            active = True
            continue
        if active and lower in section_labels:
            break
        if active and stripped.startswith(("- ", "* ")):
            item = re.sub(r"^[-*]\s+", "", stripped).strip()
            if is_meaningful(item):
                out.append(item)
    return out


def parse_markdown_table(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    table_lines = [line.strip() for line in read_text(path).splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return []
    headers = [cell.strip().lower() for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[1:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        if any(is_meaningful(value) for value in row.values()):
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


def parse_action_ledger(memory_root: Path) -> list[dict[str, str]]:
    path = memory_root / ACTION_LEDGER_REL
    if not path.exists():
        return []
    table_lines = [line.strip() for line in read_text(path).splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return []
    headers = [cell.strip().lower() for cell in split_markdown_row(table_lines[0])]
    rows: list[dict[str, str]] = []
    for line in table_lines[1:]:
        cells = split_markdown_row(line)
        if all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        raw = dict(zip(headers, cells, strict=True))
        row = {
            "action_id": raw.get("action id", ""),
            "status": raw.get("status", ""),
            "owner": raw.get("owner", "TBD"),
            "workstream": normalize_id(raw.get("workstream", "")) or raw.get("workstream", "TBD"),
            "action": raw.get("action", ""),
            "source": raw.get("source", rel_to_memory(memory_root, path)),
            "reason": raw.get("reason", ""),
            "due_or_trigger": raw.get("due / trigger", "TBD"),
            "closure_criteria": raw.get("closure criteria", "TBD"),
            "last_updated": raw.get("last updated", ""),
            "owning_workflow": raw.get("owning workflow", ""),
        }
        if any(is_meaningful(value) for value in row.values()):
            rows.append(row)
    return rows


def active_ledger_actions(memory_root: Path) -> list[dict[str, str]]:
    actions = []
    for row in parse_action_ledger(memory_root):
        if row.get("status", "").lower() not in ACTIVE_ACTION_STATUSES:
            continue
        actions.append(row)
    return actions


def count_meaningful_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or set(stripped.replace(" ", "")) <= {"-", "|", ":"}:
            continue
        if is_meaningful(stripped.strip("-* |")):
            count += 1
    return count


def discover_workstream_records(memory_root: Path, selected: list[str]) -> tuple[list[Path], list[str]]:
    workstreams_root = memory_root / "workstreams"
    selected_ids = {normalize_id(item) for item in selected if normalize_id(item)}
    missing: list[str] = []
    records: list[Path] = []

    if selected_ids:
        for workstream_id in sorted(selected_ids):
            record = workstreams_root / workstream_id / "delivery-record.md"
            if record.exists():
                records.append(record)
            else:
                missing.append(workstream_id)
        return records, missing

    if not workstreams_root.exists():
        return [], []
    return sorted(workstreams_root.glob("*/delivery-record.md")), []


def parse_workstream(record_path: Path, memory_root: Path, as_of: date, max_age_days: int) -> Workstream:
    lines = read_text(record_path).splitlines()
    identity = parse_key_bullets(section(lines, "Identity"))
    status = parse_key_bullets(section(lines, "Project Status"))
    cross = section(lines, "Cross-Workstream Links")
    workstream_id = normalize_id(identity.get("workstream id") or record_path.parent.name) or record_path.parent.name
    ws = Workstream(
        workstream_id=workstream_id,
        path=record_path,
        name=identity.get("name", "TBD"),
        owner=identity.get("fde owner", "TBD"),
        business_owner=identity.get("business owner", "TBD"),
        phase=identity.get("current bmm phase", "TBD"),
        status=identity.get("current adp status", "TBD"),
        progress=status.get("progress", "TBD"),
        blockers=status.get("blockers", "TBD"),
        risks=status.get("risks", "TBD"),
        dependencies=status.get("dependencies", "TBD"),
        change_notes=status.get("scope or change notes", "TBD"),
        next_actions=status.get("next actions", "TBD"),
        last_status_sync=status.get("last status sync", "TBD"),
        depends_on=parse_list_after_label(cross, "Depends on"),
        impacts=parse_list_after_label(cross, "Impacts"),
        l0_references=parse_list_after_label(cross, "L0 references"),
    )
    scan_workstream_sidecars(ws, memory_root)
    collect_workstream_gaps(ws, as_of, max_age_days)
    collect_wdr_actions(ws)
    return ws


def scan_workstream_sidecars(ws: Workstream, memory_root: Path) -> None:
    root = ws.path.parent
    sidecars = {
        "delivery_record": ws.path,
        "evidence": root / "evidence.md",
        "decisions": root / "decisions.md",
        "readiness": root / "readiness.md",
    }
    for key, path in sidecars.items():
        if path.exists():
            ws.files[key] = rel_to_memory(memory_root, path)
        else:
            ws.missing_files.append(path.name)
    ws.counts = {
        "evidence_lines": count_meaningful_lines(root / "evidence.md"),
        "decision_rows": len(parse_markdown_table(root / "decisions.md")),
        "readiness_lines": count_meaningful_lines(root / "readiness.md"),
    }


def collect_workstream_gaps(ws: Workstream, as_of: date, max_age_days: int) -> None:
    required = {
        "owner": ws.owner,
        "status": ws.status,
        "phase": ws.phase,
        "progress": ws.progress,
        "next action": ws.next_actions,
    }
    for label, value in required.items():
        if not is_meaningful(value):
            ws.gaps.append(f"{label} is missing or TBD")
    if not is_meaningful(ws.blockers):
        ws.gaps.append("blocker status is missing or TBD")
    if not is_meaningful(ws.risks):
        ws.gaps.append("risk exposure is missing or TBD")
    if not is_meaningful(ws.dependencies) and not ws.depends_on and not ws.impacts:
        ws.gaps.append("dependencies are missing or TBD")
    if "evidence.md" in ws.missing_files:
        ws.gaps.append("evidence.md is missing")
    elif ws.counts["evidence_lines"] == 0:
        ws.gaps.append("evidence has no meaningful entries")
    if "readiness.md" in ws.missing_files:
        ws.gaps.append("readiness.md is missing")
    elif ws.counts["readiness_lines"] == 0:
        ws.gaps.append("readiness has no meaningful entries")
    if "decisions.md" in ws.missing_files:
        ws.gaps.append("decisions.md is missing")
    if not ws.l0_references:
        ws.gaps.append("L0 references are missing or TBD")
    add_staleness_gap(ws, as_of, max_age_days)


def add_staleness_gap(ws: Workstream, as_of: date, max_age_days: int) -> None:
    if not is_meaningful(ws.last_status_sync):
        ws.gaps.append("last status sync is missing")
        return
    parsed = parse_date(ws.last_status_sync)
    if parsed is None:
        ws.gaps.append("last status sync is unparseable")
        return
    age_days = (as_of - parsed).days
    if age_days > max_age_days:
        ws.gaps.append(f"last status sync is older than {max_age_days} days")


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


def collect_wdr_actions(ws: Workstream) -> None:
    if is_meaningful(ws.next_actions):
        ws.actions.append(
            {
                "owner": ws.owner if is_meaningful(ws.owner) else "TBD",
                "workstream": ws.workstream_id,
                "action": ws.next_actions,
                "source": "delivery-record.md",
                "due_or_trigger": extract_labeled_due_or_trigger(ws.next_actions),
            }
        )


def extract_labeled_due_or_trigger(text: str) -> str:
    pattern = r"(?:^|[;\n|])\s*(?:due|trigger)\s*[:=]\s*([^.;\n|]+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        if is_meaningful(value):
            return value
    return "TBD"


def file_item(path: Path, memory_root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": rel_to_memory(memory_root, path),
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
    }


def collect_files(memory_root: Path, requested_groups: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[dict[str, Any]] = []
    missing: list[str] = []
    if "core" in requested_groups:
        for rel in CORE_FILES:
            add_optional_file(memory_root / rel, memory_root, sources, missing)
    if "l0" in requested_groups:
        for rel in L0_FILES:
            add_optional_file(memory_root / "l0" / rel, memory_root, sources, missing)
    if "views" in requested_groups:
        for rel in VIEW_FILES:
            add_optional_file(memory_root / "views" / rel, memory_root, sources, missing)
    if "actions" in requested_groups:
        add_optional_file(memory_root / ACTION_LEDGER_REL, memory_root, sources, missing)
    if "daily" in requested_groups:
        sources.extend(file_item(path, memory_root) for path in sorted((memory_root / "daily").glob("*.md")))
    if "meetings" in requested_groups:
        sources.extend(file_item(path, memory_root) for path in sorted((memory_root / "meetings").glob("*.md")))
    if "decisions" in requested_groups:
        decisions_root = memory_root / "decisions"
        for path in sorted(decisions_root.glob("*.md")):
            sources.append(file_item(path, memory_root))
        packets = decisions_root / "business-decision-packets"
        sources.extend(file_item(path, memory_root) for path in sorted(packets.glob("*.md")))
    return sources, missing


def add_optional_file(path: Path, memory_root: Path, sources: list[dict[str, Any]], missing: list[str]) -> None:
    if path.exists():
        sources.append(file_item(path, memory_root))
    else:
        missing.append(rel_to_memory(memory_root, path))


def requested_groups(capability: str) -> set[str]:
    normalized = normalize_capability(capability)
    if not normalized:
        return {"core", "workstreams"}
    if normalized in CAPABILITY_FILES:
        return set(CAPABILITY_FILES[normalized])
    for key, groups in CAPABILITY_FILES.items():
        if normalized in key or key in normalized:
            return set(groups)
    return {"core", "workstreams", "daily", "decisions", "l0", "views"}


def cross_reference_gaps(workstreams: list[Workstream]) -> list[dict[str, str]]:
    known = {ws.workstream_id for ws in workstreams}
    gaps: list[dict[str, str]] = []
    for ws in workstreams:
        for relationship, targets in [("depends_on", ws.depends_on), ("impacts", ws.impacts)]:
            for target in targets:
                normalized = normalize_id(target)
                if normalized and normalized not in known:
                    gaps.append(
                        {
                            "workstream": ws.workstream_id,
                            "relationship": relationship,
                            "target": target,
                            "gap": "referenced workstream was not found in scanned WDRs",
                        }
                    )
    return gaps


def action_cross_check_evidence(
    memory_root: Path,
    workstreams: list[Workstream],
    ledger_actions: list[dict[str, str]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    actions_by_workstream: dict[str, list[dict[str, str]]] = {}
    for action in ledger_actions:
        actions_by_workstream.setdefault(normalize_id(action.get("workstream", "")), []).append(action)

    for ws in workstreams:
        ledger_for_workstream = actions_by_workstream.get(ws.workstream_id, [])
        wdr_items = [
            {
                "action": item,
                "normalized_action": normalize_match_text(item),
                "action_ids": extract_action_ids(item),
                "source": ws.files.get("delivery_record", rel_to_memory(memory_root, ws.path)),
            }
            for item in split_next_actions(ws.next_actions)
        ]
        ledger_items = [
            {
                "action_id": action.get("action_id", ""),
                "action": action.get("action", ""),
                "normalized_action": normalize_match_text(action.get("action", "")),
                "source": action.get("source", ACTION_LEDGER_REL.as_posix()),
                "due_or_trigger": action.get("due_or_trigger", "TBD"),
            }
            for action in ledger_for_workstream
        ]
        wdr_action_ids = sorted({action_id for item in wdr_items for action_id in item["action_ids"]})
        ledger_action_ids = sorted({item["action_id"] for item in ledger_items if is_meaningful(item["action_id"])})
        if not wdr_items and not ledger_items:
            continue
        evidence.append(
            {
                "workstream": ws.workstream_id,
                "wdr_next_actions": wdr_items,
                "ledger_open_actions": ledger_items,
                "exact_action_id_matches": sorted(set(wdr_action_ids) & set(ledger_action_ids)),
                "ledger_action_ids_without_wdr_reference": sorted(set(ledger_action_ids) - set(wdr_action_ids)),
                "wdr_action_ids_without_open_ledger_reference": sorted(set(wdr_action_ids) - set(ledger_action_ids)),
            }
        )
    return evidence


def extract_action_ids(text: str) -> list[str]:
    return sorted({match.upper() for match in re.findall(r"\bACT-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", text or "", flags=re.IGNORECASE)})


def normalize_match_text(value: str) -> str:
    text = re.sub(r"[\W_]+", " ", str(value or "").lower(), flags=re.UNICODE)
    return " ".join(text.split())


def split_next_actions(value: str) -> list[str]:
    placeholders = {"", "tbd", "todo", "none", "n/a", "na", "fill missing state"}
    items = [item.strip() for item in re.split(r"\s*;\s*", value or "")]
    return [item for item in items if item and item.lower() not in placeholders]


def merge_actions(
    ledger_actions: list[dict[str, str]],
    wdr_actions: list[dict[str, str]],
) -> list[dict[str, str]]:
    merged = list(ledger_actions)
    seen = {
        action_match_key(action.get("owner", ""), action.get("workstream", ""), action.get("action", ""))
        for action in ledger_actions
    }
    for action in wdr_actions:
        key = action_match_key(action.get("owner", ""), action.get("workstream", ""), action.get("action", ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(action)
    return merged


def action_match_key(owner: str, workstream: str, action: str) -> str:
    return "|".join(normalize_match_text(value) for value in [owner, workstream, action])


def workstream_payload(ws: Workstream, memory_root: Path) -> dict[str, Any]:
    return {
        "id": ws.workstream_id,
        "name": ws.name,
        "owner": ws.owner,
        "business_owner": ws.business_owner,
        "phase": ws.phase,
        "status": ws.status,
        "progress": ws.progress,
        "blockers": ws.blockers,
        "risks": ws.risks,
        "dependencies": ws.dependencies,
        "change_notes": ws.change_notes,
        "next_actions": ws.next_actions,
        "last_status_sync": ws.last_status_sync,
        "links": {
            "depends_on": ws.depends_on,
            "impacts": ws.impacts,
            "l0_references": ws.l0_references,
        },
        "files": ws.files,
        "missing_files": ws.missing_files,
        "counts": ws.counts,
        "gaps": ws.gaps,
        "actions": ws.actions,
        "record": rel_to_memory(memory_root, ws.path),
    }


def summarize_counts(
    workstreams: list[Workstream],
    sources: list[dict[str, Any]],
    action_count: int | None = None,
) -> dict[str, int]:
    return {
        "sources_read": len(sources),
        "workstreams": len(workstreams),
        "actions": action_count if action_count is not None else sum(len(ws.actions) for ws in workstreams),
        "gaps": sum(len(ws.gaps) for ws in workstreams),
    }


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}

    memory_root = resolve_memory_root(project_root, args.memory_root)
    capability = normalize_capability(args.capability)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    if not memory_root.exists() or not memory_root.is_dir():
        return (
            1,
            {
                "ok": False,
                "error": "ADP state root is missing; run adp-project-kickoff or pass --memory-root",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "recommended_workflow": "adp-project-kickoff",
            },
        )

    groups = requested_groups(capability)
    sources, missing_sources = collect_files(memory_root, groups)
    records, missing_workstreams = discover_workstream_records(memory_root, args.workstream)
    workstreams = [parse_workstream(path, memory_root, as_of, args.max_age_days) for path in records]
    workstream_sources = [file_item(ws.path, memory_root) for ws in workstreams]
    all_sources = [*sources, *workstream_sources]
    ledger_actions = active_ledger_actions(memory_root) if "actions" in groups else []
    wdr_actions = [action for ws in workstreams for action in ws.actions]
    actions = merge_actions(ledger_actions, wdr_actions)
    gaps = [
        {"workstream": ws.workstream_id, "gap": gap, "source": ws.files.get("delivery_record", rel_to_memory(memory_root, ws.path))}
        for ws in workstreams
        for gap in ws.gaps
    ]
    gaps.extend(
        {"workstream": item, "gap": "requested workstream was not found", "source": "workstreams/"}
        for item in missing_workstreams
    )
    xref_gaps = cross_reference_gaps(workstreams)
    action_xref_evidence = action_cross_check_evidence(memory_root, workstreams, ledger_actions) if ledger_actions else []

    payload = {
        "ok": True,
        "schema_version": 2,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "capability": args.capability,
        "scope": {
            "workstreams_requested": args.workstream,
            "groups_scanned": sorted(groups),
            "as_of": as_of.isoformat(),
            "max_age_days": args.max_age_days,
        },
        "sources_read": all_sources,
        "missing_sources": sorted(set(missing_sources)),
        "workstreams": [workstream_payload(ws, memory_root) for ws in workstreams],
        "cross_reference_gaps": xref_gaps,
        "action_cross_check": action_xref_evidence,
        "gaps": gaps,
        "ledger_actions": ledger_actions,
        "actions": actions,
        "owners": sorted({ws.owner for ws in workstreams if is_meaningful(ws.owner)}),
        "due_triggers": sorted({action["due_or_trigger"] for action in actions if is_meaningful(action["due_or_trigger"])}),
        "recommended_workflow": "",
        "counts": summarize_counts(workstreams, all_sources, len(actions)),
    }
    return 0, payload


def emit(payload: dict[str, Any], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)


def main() -> int:
    args = parse_args()
    try:
        code, payload = run(args)
    except ValueError as exc:
        payload = {"ok": False, "error": str(exc)}
        code = 2
    if args.verbose:
        print(f"status: {code}", file=sys.stderr)
    emit(payload, args.output)
    return code


if __name__ == "__main__":
    sys.exit(main())
