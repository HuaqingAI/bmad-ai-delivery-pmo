#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Create the AI Delivery PMO shared memory scaffold idempotently."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "assets" / "adp-memory-templates"

DIRECTORIES = [
    "",
    "schemas",
    "l0",
    "meetings",
    "decisions",
    "decisions/business-decision-packets",
    "workstreams",
    "views",
    "daily",
]

TEMPLATE_FILES = [
    "index.md",
    "project-charter.md",
    "cadence.md",
    "schemas/workstream-delivery-record.md",
    "schemas/readiness-scorecard.md",
    "schemas/status-taxonomy.md",
    "schemas/meeting-sync.md",
    "schemas/decision-taxonomy.md",
    "l0/reference-index.md",
    "l0/extracted-freeze-model.md",
    "l0/extracted-contract-inventory.md",
    "l0/extracted-gates.md",
    "l0/extracted-nfr.md",
    "l0/extracted-evidence-rules.md",
    "l0/extracted-impacts.md",
    "l0/extracted-decision-gates.md",
    "l0/exceptions-and-open-questions.md",
    "decisions/decision-log.md",
    "views/project-lead.md",
    "views/fde-actions.md",
    "views/acceptance-readiness.md",
    "views/risk-matrix.md",
    "views/dependency-map.md",
    "views/weekly-report.md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently create _bmad/memory/adp project memory files.",
    )
    parser.add_argument("project_root", help="Project root where ADP memory should be created.")
    parser.add_argument(
        "--memory-root",
        default="_bmad/memory/adp",
        help="Memory root, relative to project root unless absolute. Default: _bmad/memory/adp.",
    )
    parser.add_argument("--project-name", default="", help="Project name for generated headings.")
    parser.add_argument(
        "--profile",
        choices=["generic-delivery", "migration-cutover"],
        default="generic-delivery",
        help="Project profile marker for starter templates.",
    )
    parser.add_argument(
        "--cadence",
        choices=["weekly", "biweekly", "custom"],
        default="weekly",
        help="Default status cadence marker.",
    )
    parser.add_argument("--source", default="", help="Brief, file path, or note describing kickoff source.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without creating files.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def render_template(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def ensure_directories(root: Path, directories: Iterable[str], dry_run: bool) -> tuple[list[str], list[str]]:
    created: list[str] = []
    existing: list[str] = []
    for rel in directories:
        target = root / rel if rel else root
        if target.exists():
            existing.append(str(target))
            continue
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
        created.append(str(target))
    return created, existing


def write_templates(
    root: Path,
    template_files: Iterable[str],
    values: dict[str, str],
    dry_run: bool,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    created: list[str] = []
    existing: list[str] = []
    errors: list[dict[str, str]] = []

    for rel in template_files:
        source = TEMPLATE_ROOT / rel
        target = root / rel
        if target.exists():
            existing.append(str(target))
            continue
        if not source.exists():
            errors.append({"path": str(source), "error": "template missing"})
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            content = render_template(source.read_text(encoding="utf-8"), values)
            target.write_text(content, encoding="utf-8", newline="\n")
        created.append(str(target))
    return created, existing, errors


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        result = {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}
        emit(result, args.output)
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    project_name = args.project_name or project_root.name
    values = {
        "PROJECT_NAME": project_name,
        "PROJECT_PROFILE": args.profile,
        "DEFAULT_CADENCE": args.cadence,
        "SOURCE_NOTE": args.source or "TBD",
        "CREATED_AT": now,
        "MEMORY_ROOT": str(memory_root),
    }

    if args.verbose:
        print(f"Using template root: {TEMPLATE_ROOT}", file=sys.stderr)
        print(f"Using memory root: {memory_root}", file=sys.stderr)

    dirs_created, dirs_existing = ensure_directories(memory_root, DIRECTORIES, args.dry_run)
    files_created, files_existing, errors = write_templates(memory_root, TEMPLATE_FILES, values, args.dry_run)

    result = {
        "ok": not errors,
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "project_name": project_name,
        "profile": args.profile,
        "cadence": args.cadence,
        "directories_created": dirs_created,
        "directories_existing": dirs_existing,
        "files_created": files_created,
        "files_existing": files_existing,
        "errors": errors,
        "next_actions": [
            "Fill project-charter.md with objective, stakeholders, scope boundaries, and escalation path.",
            "Run adp-workstream-register for each active FDE workstream.",
            "Run adp-l0-reference-sync when L0 source artifacts exist.",
        ],
    }
    emit(result, args.output)
    return 0 if result["ok"] else 1


def emit(result: dict, output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


if __name__ == "__main__":
    sys.exit(main())
