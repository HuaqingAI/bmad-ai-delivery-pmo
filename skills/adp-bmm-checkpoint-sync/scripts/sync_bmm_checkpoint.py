#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Sync one BMM lifecycle checkpoint into an ADP Workstream Delivery Record."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from checkpoint_discovery import run_discovery
from checkpoint_registry import CandidateRegistry, parse_override_value


SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync a BMM checkpoint packet into _bmad-output/adp/memory/workstreams/{id}.",
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
    parser.add_argument(
        "--action-file",
        help="JSON action handoff payload for adp-status-sync. Accepts a list or {'actions': [...]} object.",
    )
    parser.add_argument(
        "--action",
        action="append",
        default=[],
        metavar="OWNER|ACTION|DUE_OR_TRIGGER|CLOSURE_CRITERIA",
        help="Local-only action handoff scoped to --workstream-id. Repeatable.",
    )
    parser.add_argument("--record-status", choices=["draft", "gap", "ready"], help="Intentional ADP status update.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without changing files.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    add_language_args(parser)
    args = parser.parse_args(argv)
    args.command = "legacy-sync"
    args.candidate_id = ""
    args.handoff_actions = []
    return args


def parse_discover_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover a BMM checkpoint candidate and write intake registry.")
    parser.add_argument("project_root", help="Project root containing ADP memory and BMM/TEA artifacts.")
    parser.add_argument("--workstream-id", required=True, help="Workstream id. Normalized to lowercase hyphen-case.")
    parser.add_argument("--checkpoint", required=True, choices=sorted(CHECKPOINTS), help="BMM checkpoint type.")
    parser.add_argument("--artifact", action="append", default=[], metavar="[KEY=]PATH", help="BMM/TEA source artifact.")
    parser.add_argument("--summary", default="", help="Project-level summary to carry into the candidate.")
    parser.add_argument("--asserted-by", default="", help="Owner or source asserting the discovered facts.")
    parser.add_argument("--authority-scope", action="append", default=[], help="Workstream the asserter can confirm.")
    parser.add_argument("--affected-workstream", action="append", default=[], help="Workstream affected by this candidate.")
    parser.add_argument("--required-confirmer", action="append", default=[], help="Required confirmer before project-level ready.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned candidate writes without changing files.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    add_language_args(parser)
    args = parser.parse_args(argv)
    args.command = "discover"
    return args


def parse_confirm_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Confirm, dismiss, or supersede a BMM checkpoint candidate.")
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--candidate-id", required=True, help="Candidate id returned by discover.")
    parser.add_argument("--decision", choices=["confirm", "dismiss", "supersede"], default="confirm")
    parser.add_argument("--confirmed-by", default="", help="Owner or workflow making this confirmation.")
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="Dot-path override to apply to the candidate, repeatable.",
    )
    parser.add_argument("--overrides-file", help="JSON object containing dot-path overrides.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned confirmation without changing files.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    add_language_args(parser)
    args = parser.parse_args(argv)
    args.command = "confirm"
    return args


def parse_candidate_sync_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync a confirmed BMM checkpoint candidate into ADP memory.")
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--candidate-id", required=True, help="Confirmed candidate id to sync.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without changing files.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    add_language_args(parser)
    args = parser.parse_args(argv)
    args.command = "candidate-sync"
    return args


def add_language_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", help="Override document_output_language for review output.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")


def print_top_level_help() -> None:
    print(
        """usage: sync_bmm_checkpoint.py <command> ...

Recommended path:
  sync_bmm_checkpoint.py discover <project-root> --workstream-id ID --checkpoint CHECKPOINT --artifact key=path
  sync_bmm_checkpoint.py confirm <project-root> --candidate-id CHK-... --decision confirm --confirmed-by OWNER
  sync_bmm_checkpoint.py sync <project-root> --candidate-id CHK-...

Commands:
  discover      Generate or reuse a checkpoint candidate; does not write WDR.
  confirm       Confirm, dismiss, or supersede a candidate.
  sync          Sync a confirmed candidate into ADP memory.
  packet-sync   Compatibility mode for direct checkpoint packets.
  legacy-sync   Alias for packet-sync.

Compatibility:
  The historical bare form is still accepted:
  sync_bmm_checkpoint.py <project-root> --workstream-id ID --checkpoint CHECKPOINT --summary TEXT

Run a command with --help for its full flag surface."""
    )


def parse_command_line(argv: list[str] | None = None) -> argparse.Namespace:
    items = list(sys.argv[1:] if argv is None else argv)
    if not items or items[0] in {"-h", "--help"}:
        print_top_level_help()
        raise SystemExit(0)
    command = items[0]
    if command in {"packet-sync", "legacy-sync"}:
        args = parse_args(items[1:])
        args.command = "legacy-sync"
        return args
    if command == "discover":
        return parse_discover_args(items[1:])
    if command == "confirm":
        return parse_confirm_args(items[1:])
    if command == "sync":
        if "--candidate-id" in items[1:] or "-h" in items[1:] or "--help" in items[1:]:
            return parse_candidate_sync_args(items[1:])
        args = parse_args(items[1:])
        args.command = "legacy-sync"
        return args
    return parse_args(items)

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


def shell_command(parts: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([str(part)]) for part in parts)


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


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_digest(value: Any, length: int = 12) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def configure_stdio() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in [sys.stdout, sys.stderr]:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def is_missing_value(value: Any) -> bool:
    text = clean_text(value).lower()
    return text in {"", "tbd", "todo", "unknown", "n/a", "na", "none", "unassigned"}


def normalize_status(raw: Any) -> str:
    status = clean_text(raw).lower().replace("_", "-")
    if status in {"in progress", "inprogress"}:
        status = "in-progress"
    return status if status in {"open", "in-progress", "blocked", "done", "cancelled"} else "open"


def normalize_workstream_items(value: Any) -> list[str]:
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
        text = clean_text(item)
        if is_missing_value(text):
            continue
        try:
            normalized_id = normalize_id(text)
        except ValueError:
            continue
        if normalized_id in seen:
            continue
        normalized.append(normalized_id)
        seen.add(normalized_id)
    return normalized


def normalize_handoff_action(raw: dict[str, Any], default_workstream: str, default_source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("handoff action must be a JSON object")
    affected = normalize_workstream_items(
        raw.get("affected_workstreams")
        or raw.get("affectedWorkstreams")
        or raw.get("impacts")
    )
    raw_workstream = clean_text(raw.get("workstream") or raw.get("workstream_id"))
    if not raw_workstream and len(affected) > 1:
        workstream = "program"
    else:
        workstream = raw_workstream or default_workstream
        try:
            workstream = normalize_id(workstream)
        except ValueError:
            workstream = clean_text(workstream)
    action: dict[str, Any] = {
        "owner": clean_text(raw.get("owner")),
        "workstream": workstream,
        "action": clean_text(raw.get("action") or raw.get("text") or raw.get("next_action")),
        "source": clean_text(raw.get("source")) or default_source,
        "reason": clean_text(raw.get("reason")) or f"{default_workstream} {workstream} checkpoint action",
        "due_or_trigger": clean_text(raw.get("due_or_trigger") or raw.get("due") or raw.get("trigger")),
        "status": normalize_status(raw.get("status")),
        "closure_criteria": clean_text(raw.get("closure_criteria")),
        "owning_workflow": clean_text(raw.get("owning_workflow")) or "adp-bmm-checkpoint-sync",
    }
    if affected:
        action["affected_workstreams"] = affected
    action_id = clean_text(raw.get("action_id") or raw.get("id"))
    if action_id:
        action["action_id"] = action_id
    return action


def claim_actions(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    claims = candidate.get("claims", {})
    raw_actions = claims.get("actions", []) if isinstance(claims, dict) else []
    if raw_actions is None:
        return []
    if not isinstance(raw_actions, list):
        raw_actions = [raw_actions]
    default_workstream = normalize_id(str(candidate.get("workstream_id", "")))
    default_source = f"intake/bmm-checkpoints/candidates/{candidate.get('candidate_id', 'unknown')}.json#claims.actions"
    return [normalize_handoff_action(item, default_workstream, default_source) for item in raw_actions]


def load_action_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and "actions" in payload:
        payload = payload["actions"]
    elif isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("--action-file must contain a JSON list or an object with an actions list")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("--action-file actions must be JSON objects")
    return payload


def parse_local_action_specs(raw_items: list[str], workstream_id: str, source: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for raw in raw_items:
        owner, action, due_or_trigger, closure_criteria = split_pipe(raw, 4)
        actions.append(
            normalize_handoff_action(
                {
                    "owner": owner,
                    "workstream": workstream_id,
                    "action": action,
                    "due_or_trigger": due_or_trigger,
                    "closure_criteria": closure_criteria,
                    "source": source,
                    "reason": "BMM checkpoint local action",
                    "status": "open",
                    "owning_workflow": "adp-bmm-checkpoint-sync",
                },
                workstream_id,
                source,
            )
        )
    return actions


def handoff_action_issues(action: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    generic_owners = {"fde owner", "owner", "team", "project team", "someone", "unknown", "unassigned"}
    owner = clean_text(action.get("owner"))
    if is_missing_value(owner) or owner.lower() in generic_owners:
        issues.append("missing specific owner")
    workstream = clean_text(action.get("workstream"))
    if is_missing_value(workstream):
        issues.append("missing workstream route")
    affected = normalize_workstream_items(action.get("affected_workstreams", []))
    if workstream == "program" and not affected:
        issues.append("program action missing affected_workstreams")
    if workstream != "program" and len(affected) > 1:
        issues.append("cross-workstream action must use workstream program")
    if not affected and workstream != "program" and len(normalize_workstream_items(action.get("impacts", []))) > 1:
        issues.append("cross-workstream action missing affected_workstreams")
    if is_missing_value(action.get("action")):
        issues.append("missing observable action")
    if is_missing_value(action.get("due_or_trigger")):
        issues.append("missing due_or_trigger")
    if is_missing_value(action.get("closure_criteria")):
        issues.append("missing closure_criteria")
    if is_missing_value(action.get("source")):
        issues.append("missing source")
    return issues


def readiness_row_gap_message(row: Any) -> str | None:
    if isinstance(row, str):
        gap, _dimension, owner, action, due, _escalation = split_pipe(row, 6)
    elif isinstance(row, list) and len(row) >= 6:
        gap, _dimension, owner, action, due, _escalation = [clean_text(item) or "TBD" for item in row[:6]]
    else:
        return None
    if is_missing_value(action) and is_missing_value(due):
        return None
    if is_missing_value(owner) or is_missing_value(action) or is_missing_value(due):
        return None
    return f"readiness gap '{gap}' has action/due but no closure_criteria"


def audit_handoff_actions(
    actions: list[dict[str, Any]],
    readiness_rows: list[Any],
    checkpoint_context: dict[str, Any],
) -> dict[str, Any]:
    blocked: list[dict[str, str]] = []
    ready_count = 0
    fanout_suppressed = 0
    for action in actions:
        issues = handoff_action_issues(action)
        if issues:
            blocked.append(
                {
                    "action": clean_text(action.get("action")) or "(missing action)",
                    "reason": "; ".join(issues),
                    "source": clean_text(action.get("source")) or checkpoint_context.get("source", "checkpoint action"),
                }
            )
            continue
        ready_count += 1
        affected = normalize_workstream_items(action.get("affected_workstreams", []))
        if clean_text(action.get("workstream")) == "program" and len(affected) > 1:
            fanout_suppressed += len(affected) - 1
    handoff_gaps = compact_list([message for row in readiness_rows if (message := readiness_row_gap_message(row))])
    return {
        "actions_seen": len(actions),
        "ledger_ready_actions": ready_count,
        "blocked_actions": blocked,
        "handoff_gaps": handoff_gaps,
        "fanout_suppressed": fanout_suppressed,
        "no_op": False,
    }


def ledger_ready_handoff_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [action for action in actions if not handoff_action_issues(action)]


def status_sync_intake_path(memory_root: Path, workstream_id: str, checkpoint: str, stable_key: str, dry_run: bool) -> Path:
    _ = (workstream_id, checkpoint, dry_run)
    return memory_root / "intake" / "status-sync" / f"{stable_key}-actions.json"


def status_sync_intake_payload(actions: list[dict[str, Any]]) -> dict[str, Any]:
    updates: list[dict[str, Any]] = []
    by_id: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for action in actions:
        update_id = clean_text(action.get("workstream")) or "program"
        if update_id not in by_id:
            by_id[update_id] = []
            order.append(update_id)
        by_id[update_id].append(action)
    for update_id in order:
        updates.append({"id": update_id, "source": "adp-bmm-checkpoint-sync", "actions": by_id[update_id]})
    return {"updates": updates}


def load_canonical_json(path: Path) -> str | None:
    try:
        return canonical_json(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError):
        return None


def write_status_sync_intake(
    memory_root: Path,
    workstream_id: str,
    checkpoint: str,
    actions: list[dict[str, Any]],
    stable_key: str,
    dry_run: bool,
) -> tuple[Path, bool]:
    path = status_sync_intake_path(memory_root, workstream_id, checkpoint, stable_key, dry_run)
    payload = status_sync_intake_payload(actions)
    canonical = canonical_json(payload)
    if dry_run:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path
    existing = load_canonical_json(target) if target.exists() else None
    if existing == canonical:
        return target, True
    if existing is not None and existing != canonical:
        digest = stable_digest(payload, 8)
        target = target.with_name(f"{target.stem}-{digest}{target.suffix}")
        existing = load_canonical_json(target) if target.exists() else None
        if existing == canonical:
            return target, True
        if existing is not None and existing != canonical:
            counter = 2
            while target.exists():
                candidate = path.with_name(f"{path.stem}-{digest}-{counter}{path.suffix}")
                existing = load_canonical_json(candidate) if candidate.exists() else None
                if existing == canonical:
                    return candidate, True
                if existing is None:
                    target = candidate
                    break
                counter += 1
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target, False


def status_sync_command_hints(project_root: Path, intake_path: Path) -> list[str]:
    return [
        f'If runner alias exists: adp-status-sync update "{project_root}" --updates-file "{intake_path}"',
        f'Otherwise resolve adp-status-sync skill root and run: uv run "{{status-sync-skill-root}}/scripts/sync_status.py" update "{project_root}" --updates-file "{intake_path}"',
    ]


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


def ready_validation_failures(
    args: argparse.Namespace,
    generated_gaps: list[list[str]],
    artifacts: list[ArtifactUpdate],
) -> list[str]:
    if args.record_status != "ready":
        return []
    failures = [gap[0] for gap in generated_gaps]
    if args.readiness_gap:
        failures.append("explicit readiness gaps are present")
    if not args.business_confirmation:
        failures.append("business/customer confirmation is missing")
    if args.impact and not args.business_confirmation:
        failures.append("impacted workstream confirmation is missing")
    if args.evidence_required and not args.evidence:
        failures.append("evidence is required but no evidence rows were supplied")
    if args.checkpoint == "validation" and not args.evidence:
        failures.append("validation checkpoint is missing evidence rows")
    if args.checkpoint == "implementation" and not args.evidence and not any(item.label == "Code / PR" for item in artifacts):
        failures.append("implementation checkpoint is missing implementation evidence")
    return compact_list(failures)


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


def action_file_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def handoff_actions_from_args(
    args: argparse.Namespace,
    project_root: Path,
    workstream_id: str,
    default_source: str,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for raw in getattr(args, "handoff_actions", []) or []:
        actions.append(normalize_handoff_action(raw, workstream_id, default_source))
    action_file = getattr(args, "action_file", None)
    if action_file:
        for raw in load_action_file(action_file_path(project_root, action_file)):
            actions.append(normalize_handoff_action(raw, workstream_id, default_source))
    actions.extend(parse_local_action_specs(getattr(args, "action", []) or [], workstream_id, default_source))
    return actions


def legacy_handoff_stable_key(args: argparse.Namespace, workstream_id: str, checkpoint: str, date_str: str, actions: list[dict[str, Any]]) -> str:
    payload = {
        "workstream_id": workstream_id,
        "checkpoint": checkpoint,
        "summary": getattr(args, "summary", ""),
        "artifact": getattr(args, "artifact", []),
        "artifact_status": getattr(args, "artifact_status", ""),
        "scope": getattr(args, "scope", []),
        "acceptance": getattr(args, "acceptance", []),
        "evidence_required": getattr(args, "evidence_required", []),
        "open_question": getattr(args, "open_question", []),
        "dependency": getattr(args, "dependency", []),
        "impact": getattr(args, "impact", []),
        "l0_reference": getattr(args, "l0_reference", []),
        "risk": getattr(args, "risk", []),
        "blocker": getattr(args, "blocker", []),
        "milestone": getattr(args, "milestone", []),
        "next_action": getattr(args, "next_action", []),
        "business_confirmation": getattr(args, "business_confirmation", []),
        "change_note": getattr(args, "change_note", []),
        "evidence": getattr(args, "evidence", []),
        "decision": getattr(args, "decision", []),
        "readiness_gap": getattr(args, "readiness_gap", []),
        "record_status": getattr(args, "record_status", ""),
        "actions": actions,
    }
    payload_hash = stable_digest(payload, 16)
    return f"{date_str}-bmm-checkpoint-{workstream_id}-{checkpoint}-{payload_hash}"


def action_handoff_stable_key(args: argparse.Namespace, workstream_id: str, checkpoint: str, date_str: str, actions: list[dict[str, Any]]) -> str:
    candidate_id = clean_text(getattr(args, "candidate_id", ""))
    if candidate_id:
        return f"{date_str}-bmm-checkpoint-{workstream_id}-{checkpoint}-{candidate_id}"
    return legacy_handoff_stable_key(args, workstream_id, checkpoint, date_str, actions)


def append_repeat(parts: list[str], flag: str, values: list[Any]) -> None:
    for value in values or []:
        parts.extend([flag, str(value)])


def default_execute_report_path(report_path: Path) -> Path:
    stem = report_path.stem
    if stem.endswith("-dry-run"):
        stem = stem[: -len("-dry-run")]
    return report_path.with_name(f"{stem}-execute-report.json")


def legacy_apply_command(args: argparse.Namespace, project_root: Path, execute_report_path: Path) -> str:
    script_path = Path(__file__).resolve()
    parts = [
        sys.executable,
        str(script_path),
        str(project_root),
        "--workstream-id",
        getattr(args, "workstream_id", ""),
        "--checkpoint",
        getattr(args, "checkpoint", ""),
        "--summary",
        getattr(args, "summary", ""),
        "--artifact-status",
        getattr(args, "artifact_status", "linked"),
    ]
    append_repeat(parts, "--artifact", getattr(args, "artifact", []))
    append_repeat(parts, "--scope", getattr(args, "scope", []))
    append_repeat(parts, "--acceptance", getattr(args, "acceptance", []))
    append_repeat(parts, "--evidence-required", getattr(args, "evidence_required", []))
    append_repeat(parts, "--open-question", getattr(args, "open_question", []))
    append_repeat(parts, "--dependency", getattr(args, "dependency", []))
    append_repeat(parts, "--impact", getattr(args, "impact", []))
    append_repeat(parts, "--l0-reference", getattr(args, "l0_reference", []))
    append_repeat(parts, "--risk", getattr(args, "risk", []))
    append_repeat(parts, "--blocker", getattr(args, "blocker", []))
    append_repeat(parts, "--milestone", getattr(args, "milestone", []))
    append_repeat(parts, "--next-action", getattr(args, "next_action", []))
    append_repeat(parts, "--business-confirmation", getattr(args, "business_confirmation", []))
    append_repeat(parts, "--change-note", getattr(args, "change_note", []))
    append_repeat(parts, "--evidence", getattr(args, "evidence", []))
    append_repeat(parts, "--decision", getattr(args, "decision", []))
    append_repeat(parts, "--readiness-gap", getattr(args, "readiness_gap", []))
    append_repeat(parts, "--action", getattr(args, "action", []))
    if getattr(args, "action_file", None):
        parts.extend(["--action-file", getattr(args, "action_file")])
    if getattr(args, "record_status", None):
        parts.extend(["--record-status", getattr(args, "record_status")])
    if getattr(args, "memory_root", None):
        parts.extend(["--memory-root", getattr(args, "memory_root")])
    if getattr(args, "verbose", False):
        parts.append("--verbose")
    parts.extend(["-o", str(execute_report_path)])
    return shell_command(parts)


def candidate_apply_command(args: argparse.Namespace, project_root: Path, execute_report_path: Path) -> str:
    parts = [
        sys.executable,
        str(Path(__file__).resolve()),
        "sync",
        str(project_root),
        "--candidate-id",
        getattr(args, "candidate_id", ""),
    ]
    if getattr(args, "memory_root", None):
        parts.extend(["--memory-root", getattr(args, "memory_root")])
    if getattr(args, "verbose", False):
        parts.append("--verbose")
    parts.extend(["-o", str(execute_report_path)])
    return shell_command(parts)


def merge_action_handoff_result(
    result: dict[str, Any],
    *,
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    workstream_id: str,
    checkpoint: str,
    date_str: str,
    readiness_rows_for_audit: list[Any],
) -> None:
    default_source = (
        f"intake/bmm-checkpoints/candidates/{args.candidate_id}.json#claims.actions"
        if clean_text(getattr(args, "candidate_id", ""))
        else f"legacy-bmm-checkpoint:{workstream_id}:{checkpoint}"
    )
    handoff_actions = handoff_actions_from_args(args, project_root, workstream_id, default_source)
    audit = audit_handoff_actions(
        handoff_actions,
        readiness_rows_for_audit,
        {"source": default_source, "workstream_id": workstream_id, "checkpoint": checkpoint},
    )
    ready_actions = ledger_ready_handoff_actions(handoff_actions)
    status_sync_intake_files: list[str] = []
    if ready_actions:
        stable_key = action_handoff_stable_key(args, workstream_id, checkpoint, date_str, ready_actions)
        intake_path, no_op = write_status_sync_intake(
            memory_root,
            workstream_id,
            checkpoint,
            ready_actions,
            stable_key,
            args.dry_run,
        )
        audit["no_op"] = no_op
        status_sync_intake_files.append(str(intake_path))
        result["next_actions"] = compact_list([*result.get("next_actions", []), *status_sync_command_hints(project_root, intake_path)])
    result["status_sync_intake_files"] = status_sync_intake_files
    result["action_handoff_audit"] = audit


def run_legacy_sync(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}
    try:
        workstream_id = normalize_id(args.workstream_id)
    except ValueError as exc:
        return 2, {"ok": False, "error": str(exc), "raw_workstream_id": args.workstream_id}

    memory_root = resolve_memory_root(project_root, args.memory_root)
    workstream_root = memory_root / "workstreams" / workstream_id
    record_path = workstream_root / "delivery-record.md"
    if not record_path.exists():
        return 2, {
            "ok": False,
            "error": "delivery-record.md not found; run adp-workstream-register first",
            "workstream_root": str(workstream_root),
            "record_path": str(record_path),
        }

    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    date_str = timestamp[:10]
    note = "; ".join(compact_list(args.change_note)) or f"{args.checkpoint} checkpoint sync {timestamp}"
    artifacts, artifact_warnings = parse_artifacts(args.artifact, args.checkpoint, args.artifact_status, note)
    record_before = record_path.read_text(encoding="utf-8")
    owner = parse_owner(record_before)
    generated_gaps = default_gaps(args, owner, artifacts)
    ready_failures = ready_validation_failures(args, generated_gaps, artifacts)
    if ready_failures:
        return 1, {
            "ok": False,
            "dry_run": args.dry_run,
            "error": "record-status ready rejected; deterministic readiness blockers are present",
            "record_status": args.record_status,
            "validation_failures": ready_failures,
            "can_apply": False,
            "apply_blockers": ready_failures,
            "recommended_next_step": "run_readiness_review",
            "apply_command": "",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "workstream_id": workstream_id,
            "workstream_root": str(workstream_root),
            "checkpoint": args.checkpoint,
        }
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
        "planned_files": compact_list(files_planned),
        "artifact_updates": [update.__dict__ for update in artifacts],
        "evidence_added": evidence_added,
        "decisions_added": decision_added,
        "readiness_gaps_added": readiness_added,
        "generated_gaps": [gap[0] for gap in generated_gaps],
        "ready_validation_failures": [],
        "daily_log": str(daily_path),
        "warnings": warnings,
        "next_actions": next_actions_for(args, generated_gaps),
    }
    if args.dry_run:
        result["can_apply"] = True
        result["apply_blockers"] = []
        result["recommended_next_step"] = "review_then_apply"
        result["apply_command"] = ""
    merge_action_handoff_result(
        result,
        args=args,
        project_root=project_root,
        memory_root=memory_root,
        workstream_id=workstream_id,
        checkpoint=args.checkpoint,
        date_str=date_str,
        readiness_rows_for_audit=[*args.readiness_gap, *generated_gaps],
    )
    return 0, result


def overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.overrides_file:
        payload = json.loads(Path(args.overrides_file).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("--overrides-file must contain a JSON object")
        overrides.update(payload)
    for raw in args.override:
        if "=" not in raw:
            raise ValueError(f"override must use PATH=VALUE: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"override path is empty: {raw}")
        overrides[key] = parse_override_value(value)
    return overrides


def run_confirm(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}
    try:
        overrides = overrides_from_args(args)
        registry = CandidateRegistry(resolve_memory_root(project_root, args.memory_root))
        result = registry.confirm(
            args.candidate_id,
            args.decision,
            overrides,
            confirmed_by=args.confirmed_by,
            dry_run=args.dry_run,
        )
        return (0 if result.get("ok") else 2), result
    except Exception as exc:
        return 2, {"ok": False, "error": str(exc), "candidate_id": args.candidate_id}


def claim_list(claims: dict[str, Any], key: str) -> list[str]:
    value = claims.get(key, [])
    if isinstance(value, list):
        return compact_list([str(item) for item in value])
    if value:
        return compact_list([str(value)])
    return []


def candidate_readiness_gaps(candidate: dict[str, Any]) -> list[str]:
    claims = candidate.get("claims", {})
    authority = candidate.get("authority", {})
    gaps = claim_list(claims, "readiness_gaps")
    gaps.extend(claim_list(claims, "open_questions"))
    confirmation_state = authority.get("confirmation_state", "")
    if confirmation_state in {"discovered", "cross-line-pending", "business-pending"}:
        gaps.append(f"Confirmation state is {confirmation_state}; do not mark record ready.")
    required_confirmers = compact_list([str(item) for item in authority.get("required_confirmers", [])])
    confirmed_by = {
        str(event.get("confirmed_by", "")).strip()
        for event in candidate.get("confirmation_events", [])
        if str(event.get("confirmed_by", "")).strip()
    }
    missing_confirmers = [item for item in required_confirmers if item not in confirmed_by]
    if missing_confirmers:
        gaps.append("Required confirmers missing: " + ", ".join(missing_confirmers))
    if not claim_list(claims, "business_confirmation"):
        gaps.append("Business/customer confirmation is missing.")
    if candidate.get("checkpoint") == "validation" and not claim_list(claims, "evidence"):
        gaps.append("Validation checkpoint is missing evidence rows.")
    return compact_list(gaps)


def candidate_to_sync_args(args: argparse.Namespace, candidate: dict[str, Any]) -> argparse.Namespace:
    claims = candidate.get("claims", {})
    scope = claims.get("scope", {}) if isinstance(claims.get("scope"), dict) else {}
    acceptance = claims.get("acceptance", {}) if isinstance(claims.get("acceptance"), dict) else {}
    artifact = candidate.get("artifact", {})
    authority = candidate.get("authority", {})
    artifact_path = str(artifact.get("path", ""))
    artifact_kind = str(artifact.get("kind", candidate.get("checkpoint", "artifact")))
    artifact_status = str(artifact.get("status", "linked")).lower()
    if artifact_status not in ARTIFACT_STATUSES:
        artifact_status = "linked"

    asserted_by = str(authority.get("asserted_by") or "TBD")
    source_link = artifact_path or str(candidate.get("candidate_id"))
    readiness_gaps = candidate_readiness_gaps(candidate)
    record_status = "gap" if readiness_gaps else None

    evidence_rows = [
        f"{item}|source|{source_link}|TBD|candidate|TBD"
        for item in claim_list(claims, "evidence")
    ]
    decision_rows = [
        f"checkpoint|{item}|{asserted_by}|{candidate.get('checkpoint')} checkpoint|candidate|{source_link}"
        for item in claim_list(claims, "decisions")
    ]
    readiness_rows_for_sync = [
        f"{gap}|Checkpoint readiness|{asserted_by}|Resolve before record-status ready|Before readiness review|Project lead if unresolved"
        for gap in readiness_gaps
    ]
    business_confirmation = claim_list(claims, "business_confirmation")
    confirmation_state = str(authority.get("confirmation_state") or "")
    if confirmation_state:
        business_confirmation.append(f"candidate confirmation state: {confirmation_state}")

    return argparse.Namespace(
        command="candidate-sync-inner",
        project_root=args.project_root,
        workstream_id=candidate.get("workstream_id", ""),
        checkpoint=candidate.get("checkpoint", ""),
        summary=claims.get("summary") or f"{candidate.get('checkpoint')} checkpoint candidate {candidate.get('candidate_id')}",
        artifact=[f"{artifact_kind}={artifact_path}"] if artifact_path else [],
        artifact_status=artifact_status,
        scope=compact_list([str(item) for item in scope.get("in", [])]),
        acceptance=compact_list([str(item) for item in acceptance.get("criteria", [])]),
        evidence_required=compact_list([str(item) for item in acceptance.get("evidence_required", [])]),
        open_question=claim_list(claims, "open_questions"),
        dependency=claim_list(claims, "dependencies"),
        impact=claim_list(claims, "impacts"),
        l0_reference=[],
        risk=claim_list(claims, "risks"),
        blocker=[],
        milestone=[],
        next_action=claim_list(claims, "next_actions"),
        business_confirmation=compact_list(business_confirmation),
        change_note=[f"Candidate {candidate.get('candidate_id')} from {artifact.get('source_scope_key', 'unknown source')}"],
        evidence=evidence_rows,
        decision=decision_rows,
        readiness_gap=readiness_rows_for_sync,
        record_status=record_status,
        memory_root=args.memory_root,
        dry_run=args.dry_run,
        verbose=args.verbose,
        output=None,
        candidate_id=candidate.get("candidate_id", ""),
        action_file=None,
        action=[],
        handoff_actions=claim_actions(candidate),
    )


def run_candidate_sync(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}
    registry = CandidateRegistry(resolve_memory_root(project_root, args.memory_root))
    candidate = registry.load(args.candidate_id)
    if not candidate:
        return 2, {"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}
    if candidate.get("status") == "applied":
        return 0, {
            "ok": True,
            "dry_run": args.dry_run,
            "no_op": True,
            "candidate_id": args.candidate_id,
            "status": "applied",
            "candidate_path": str(registry.candidate_path(args.candidate_id)),
            "preview_path": str(registry.preview_path(args.candidate_id)),
            "status_sync_intake_files": [],
            "action_handoff_audit": {
                "actions_seen": 0,
                "ledger_ready_actions": 0,
                "blocked_actions": [],
                "handoff_gaps": [],
                "fanout_suppressed": 0,
                "no_op": True,
            },
        }
    if candidate.get("status") != "confirmed":
        return 2, {
            "ok": False,
            "error": "candidate must be confirmed before sync",
            "candidate_id": args.candidate_id,
            "status": candidate.get("status"),
            "candidate_path": str(registry.candidate_path(args.candidate_id)),
            "preview_path": str(registry.preview_path(args.candidate_id)),
        }

    sync_args = candidate_to_sync_args(args, candidate)
    code, result = run_legacy_sync(sync_args)
    result["candidate_id"] = args.candidate_id
    result["candidate_path"] = str(registry.candidate_path(args.candidate_id))
    result["preview_path"] = str(registry.preview_path(args.candidate_id))
    if code == 0:
        applied = registry.mark_applied(args.candidate_id, result, dry_run=args.dry_run)
        result["candidate_status"] = applied.get("status")
        result["candidate_no_op"] = applied.get("no_op", False)
    return code, result


def default_dry_run_report_path(args: argparse.Namespace, result: dict[str, Any]) -> Path | None:
    memory_root_value = result.get("memory_root")
    if not memory_root_value:
        project_root_value = result.get("project_root") or getattr(args, "project_root", "")
        if project_root_value:
            memory_root_value = str(resolve_memory_root(Path(project_root_value).resolve(), getattr(args, "memory_root", "_bmad-output/adp/memory")))
    if not memory_root_value:
        return None
    root = Path(memory_root_value) / "intake" / "bmm-checkpoints" / "dry-runs"
    command = getattr(args, "command", "")
    candidate_id = clean_text(result.get("candidate_id") or getattr(args, "candidate_id", ""))
    if command == "candidate-sync" and candidate_id:
        return root / f"{candidate_id}-sync-dry-run.json"
    if command == "discover" and candidate_id:
        return root / f"{candidate_id}-discover-dry-run.json"
    if command == "confirm" and candidate_id:
        return root / f"{candidate_id}-confirm-dry-run.json"
    workstream_id = clean_text(result.get("workstream_id") or getattr(args, "workstream_id", "workstream"))
    checkpoint = clean_text(result.get("checkpoint") or getattr(args, "checkpoint", "checkpoint"))
    try:
        workstream_id = normalize_id(workstream_id)
    except ValueError:
        workstream_id = "workstream"
    date_str = datetime.now().astimezone().date().isoformat()
    digest = stable_digest(
        {
            "command": command or "legacy-sync",
            "workstream_id": workstream_id,
            "checkpoint": checkpoint,
            "summary": getattr(args, "summary", ""),
            "artifact": getattr(args, "artifact", []),
            "scope": getattr(args, "scope", []),
            "acceptance": getattr(args, "acceptance", []),
            "evidence": getattr(args, "evidence", []),
            "decision": getattr(args, "decision", []),
            "readiness_gap": getattr(args, "readiness_gap", []),
            "actions": getattr(args, "action", []),
            "action_file": getattr(args, "action_file", ""),
            "record_status": getattr(args, "record_status", ""),
        },
        12,
    )
    return root / f"{date_str}-{workstream_id}-{checkpoint}-{digest}.json"


def output_report_path(args: argparse.Namespace, result: dict[str, Any]) -> Path | None:
    output = getattr(args, "output", None)
    if output:
        return Path(output).resolve()
    if getattr(args, "dry_run", False):
        return default_dry_run_report_path(args, result)
    return None


def annotate_output_contract(args: argparse.Namespace, result: dict[str, Any], report_path: Path | None) -> None:
    result["stdout_only"] = report_path is None
    result["report_path"] = str(report_path) if report_path else ""
    result["report_exists"] = False
    if not getattr(args, "dry_run", False):
        return
    if report_path:
        result["dry_run_report_path"] = str(report_path)
    result["planned_files"] = compact_list(result.get("files_planned", []))
    execute_report = default_execute_report_path(report_path) if report_path else Path("execute-report.json")
    command = getattr(args, "command", "")
    if result.get("can_apply") is False:
        result.setdefault("apply_command", "")
        return
    if command == "candidate-sync":
        result["can_apply"] = True
        result.setdefault("apply_blockers", [])
        result["recommended_next_step"] = "review_then_apply"
        result["apply_command"] = candidate_apply_command(args, Path(args.project_root).resolve(), execute_report)
    elif command in {"legacy-sync", "candidate-sync-inner"}:
        result["can_apply"] = True
        result.setdefault("apply_blockers", [])
        result["recommended_next_step"] = "review_then_apply"
        result["apply_command"] = legacy_apply_command(args, Path(args.project_root).resolve(), execute_report)
    elif command == "discover":
        result["can_apply"] = False
        result["apply_blockers"] = ["candidate must be confirmed before sync"]
        result["recommended_next_step"] = "confirm_candidate"
    elif command == "confirm":
        result["recommended_next_step"] = "sync_candidate" if result.get("status") == "confirmed" else "dismiss_candidate"


def write_json_report(path: Path, result: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path.exists()


def safe_stdout(text: str, fallback: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            print(fallback, file=sys.stderr)
        except UnicodeEncodeError:
            pass


def dry_run_summary(result: dict[str, Any]) -> str:
    status = "ok" if result.get("ok") else "failed"
    report_path = result.get("report_path") or "(not written)"
    can_apply = result.get("can_apply")
    lines = [f"dry-run {status}", f"report_path: {report_path}", f"report_exists: {bool(result.get('report_exists'))}"]
    if can_apply is not None:
        lines.append(f"can_apply: {bool(can_apply)}")
    next_step = result.get("recommended_next_step")
    if next_step:
        lines.append(f"recommended_next_step: {next_step}")
    return "\n".join(lines)


def emit(result: dict, output: Path | None, *, summary_only: bool = False) -> None:
    if output:
        report_exists = write_json_report(output, result)
        result["report_exists"] = report_exists
        if report_exists:
            write_json_report(output, result)
        if summary_only:
            safe_stdout(
                dry_run_summary(result),
                "JSON report was written; stdout summary failed.",
            )
        return
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    safe_stdout(payload, "JSON was not written; rerun with -o <path>.")


def main() -> int:
    configure_stdio()
    args = parse_command_line()
    project_root = Path(args.project_root).resolve()
    config_module = load_module(Path(args.config_script), "adp_checkpoint_effective_config")
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        result = {"ok": False, "error": config.get("error", "shared ADP effective config could not be resolved")}
        emit(result, Path(args.output).resolve() if args.output else None)
        return 2
    locale = str(config.get("document_locale") or "en")
    if args.command == "discover":
        code, result = run_discovery(args)
    elif args.command == "confirm":
        code, result = run_confirm(args)
    elif args.command == "candidate-sync":
        code, result = run_candidate_sync(args)
    else:
        code, result = run_legacy_sync(args)
    result["language"] = language_metadata(config, locale)
    result["display"] = {
        "outcome": config_module.message("checkpoint.outcome.ok" if result.get("ok") else "checkpoint.outcome.failed", locale),
        "recommended_next_step": config_module.message(
            f"checkpoint.next.{result.get('recommended_next_step')}", locale
        ) if result.get("recommended_next_step") else "",
    }
    report_path = output_report_path(args, result)
    annotate_output_contract(args, result, report_path)
    emit(result, report_path, summary_only=bool(getattr(args, "dry_run", False)))
    return code


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
