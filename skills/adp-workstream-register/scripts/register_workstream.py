#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Create starter ADP files for a single FDE workstream idempotently."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "assets" / "workstream-templates"
TEMPLATE_FILES = ["delivery-record.md", "evidence.md", "decisions.md", "readiness.md"]
CORE_MEMORY_FILES = [
    "index.md",
    "project-charter.md",
    "cadence.md",
    "schemas/workstream-delivery-record.md",
]
ALLOWED_ARTIFACT_KEYS = {
    "prd": "PRD",
    "architecture": "Architecture",
    "epics": "Epics / stories",
    "stories": "Epics / stories",
    "code": "Code / PR",
    "validation": "Validation evidence",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently create ADP workstream record files under _bmad/memory/adp/workstreams/{id}.",
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--id", required=True, help="Workstream id. Normalized to lowercase hyphen-case.")
    parser.add_argument("--name", required=True, help="Human-readable workstream name.")
    parser.add_argument("--owner", required=True, help="FDE owner.")
    parser.add_argument("--business-owner", default="TBD", help="Business owner or confirmer.")
    parser.add_argument("--phase", default="TBD", help="Current BMM phase.")
    parser.add_argument("--status", choices=["draft", "gap", "ready"], default="draft", help="Initial ADP status.")
    parser.add_argument("--scope", default="TBD", help="Short management-level scope summary.")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="KEY=PATH",
        help="Artifact link, repeatable. Common keys: prd, architecture, epics, code, validation.",
    )
    parser.add_argument("--depends-on", action="append", default=[], help="Upstream workstream id, repeatable.")
    parser.add_argument("--impacts", action="append", default=[], help="Downstream impacted workstream id, repeatable.")
    parser.add_argument("--l0-reference", action="append", default=[], help="L0 reference or constraint, repeatable.")
    parser.add_argument(
        "--memory-root",
        default="_bmad/memory/adp",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad/memory/adp.",
    )
    parser.add_argument(
        "--allow-partial-memory",
        action="store_true",
        help="Allow writing when kickoff-created ADP core files are missing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without creating files.")
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


def missing_core_memory_files(memory_root: Path) -> list[str]:
    return [rel for rel in CORE_MEMORY_FILES if not (memory_root / rel).exists()]


def parse_artifacts(items: list[str]) -> tuple[dict[str, str], list[str]]:
    artifacts: dict[str, str] = {}
    warnings: list[str] = []
    for item in items:
        if "=" not in item:
            warnings.append(f"ignored artifact without KEY=PATH format: {item}")
            continue
        key, value = item.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key or not value:
            warnings.append(f"ignored incomplete artifact: {item}")
            continue
        label = ALLOWED_ARTIFACT_KEYS.get(key, key)
        artifacts[label] = value
    return artifacts, warnings


def render_template(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- TBD"


def artifact_table(artifacts: dict[str, str]) -> str:
    rows = []
    labels = ["PRD", "Architecture", "Epics / stories", "Code / PR", "Validation evidence"]
    extra_labels = [label for label in artifacts if label not in labels]
    for label in [*labels, *extra_labels]:
        path = artifacts.get(label, "TBD")
        status = "linked" if path != "TBD" else "draft"
        rows.append(f"| {label} | {path} | {status} | TBD |")
    return "\n".join(rows)


def patch_plan_name(workstream_root: Path) -> str:
    base = "registration-patch-plan.md"
    if not (workstream_root / base).exists():
        return base
    index = 2
    while (workstream_root / f"registration-patch-plan-{index}.md").exists():
        index += 1
    return f"registration-patch-plan-{index}.md"


def patch_plan_content(args: argparse.Namespace, workstream_id: str, artifacts: dict[str, str], gaps: list[str], now: str) -> str:
    artifact_rows = artifact_table(artifacts)
    depends_on = bullet_list(args.depends_on)
    impacts = bullet_list(args.impacts)
    l0_references = bullet_list(args.l0_reference)
    gaps_list = bullet_list(gaps)
    return f"""# Registration Patch Plan

Generated: {now}
Workstream: {workstream_id} - {args.name}

This plan captures supplied registration facts for an existing workstream. Review and apply the relevant items to `delivery-record.md`, `evidence.md`, `decisions.md`, and `readiness.md`; the script did not overwrite existing user state.

## Identity Updates

- FDE owner: {args.owner}
- Business owner: {args.business_owner}
- Current BMM phase: {args.phase}
- Proposed ADP status: {args.status}

## Scope Update

- In scope: {args.scope}

## BMM Artifact Index Candidates

| Artifact | Path / Link | Baseline Status | Notes |
| --- | --- | --- | --- |
{artifact_rows}

## Cross-Workstream Link Candidates

Depends on:

{depends_on}

Impacts:

{impacts}

L0 references:

{l0_references}

## Visible Gaps

{gaps_list}
"""


def write_templates(workstream_root: Path, values: dict[str, str], dry_run: bool) -> tuple[list[str], list[str], list[dict[str, str]]]:
    created: list[str] = []
    existing: list[str] = []
    errors: list[dict[str, str]] = []
    for rel in TEMPLATE_FILES:
        source = TEMPLATE_ROOT / rel
        target = workstream_root / rel
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


def write_patch_plan(workstream_root: Path, content: str, dry_run: bool) -> str:
    rel = patch_plan_name(workstream_root)
    target = workstream_root / rel
    if not dry_run:
        workstream_root.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    return str(target)


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        emit({"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}, args.output)
        return 2

    try:
        workstream_id = normalize_id(args.id)
    except ValueError as exc:
        emit({"ok": False, "error": str(exc), "raw_id": args.id}, args.output)
        return 2

    artifacts, warnings = parse_artifacts(args.artifact)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    missing_core = missing_core_memory_files(memory_root)
    if missing_core and not args.allow_partial_memory:
        emit(
            {
                "ok": False,
                "error": "ADP memory root is missing kickoff-created core files; run adp-project-kickoff or pass --allow-partial-memory.",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "missing_core_files": missing_core,
            },
            args.output,
        )
        return 2

    workstream_root = memory_root / "workstreams" / workstream_id
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    values = {
        "WORKSTREAM_ID": workstream_id,
        "WORKSTREAM_NAME": args.name,
        "FDE_OWNER": args.owner,
        "BUSINESS_OWNER": args.business_owner,
        "BMM_PHASE": args.phase,
        "ADP_STATUS": args.status,
        "SCOPE_SUMMARY": args.scope,
        "ARTIFACT_TABLE": artifact_table(artifacts),
        "DEPENDS_ON": bullet_list(args.depends_on),
        "IMPACTS": bullet_list(args.impacts),
        "L0_REFERENCES": bullet_list(args.l0_reference),
        "CREATED_AT": now,
    }

    if args.verbose:
        print(f"Using template root: {TEMPLATE_ROOT}", file=sys.stderr)
        print(f"Using workstream root: {workstream_root}", file=sys.stderr)

    is_update = (workstream_root / "delivery-record.md").exists()
    if not args.dry_run:
        workstream_root.mkdir(parents=True, exist_ok=True)

    gaps = compute_initial_gaps(args, artifacts)
    files_created, files_existing, errors = write_templates(workstream_root, values, args.dry_run)
    if is_update:
        patch_plan_path = write_patch_plan(
            workstream_root,
            patch_plan_content(args, workstream_id, artifacts, gaps, now),
            args.dry_run,
        )
        files_created.append(patch_plan_path)
    else:
        patch_plan_path = ""

    result = {
        "ok": not errors,
        "dry_run": args.dry_run,
        "mode": "update" if is_update else "create",
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "missing_core_files": missing_core,
        "workstream_id": workstream_id,
        "workstream_root": str(workstream_root),
        "files_created": files_created,
        "files_existing": files_existing,
        "patch_plan": patch_plan_path,
        "artifacts": artifacts,
        "warnings": warnings,
        "visible_gaps": gaps,
        "errors": errors,
        "next_actions": [
            "Fill delivery-record.md scope, acceptance, dependency, and next-action gaps.",
            "Run adp-bmm-checkpoint-sync when a PRD, architecture, epic/story, or validation artifact is ready to sync.",
            "Run adp-acceptance-readiness-review before stakeholder acceptance or cutover review.",
        ],
    }
    emit(result, args.output)
    return 0 if result["ok"] else 1


def compute_initial_gaps(args: argparse.Namespace, artifacts: dict[str, str]) -> list[str]:
    gaps: list[str] = []
    if args.business_owner == "TBD":
        gaps.append("business owner is not identified")
    if args.phase == "TBD":
        gaps.append("current BMM phase is not identified")
    if args.scope == "TBD":
        gaps.append("scope summary is missing")
    if not artifacts:
        gaps.append("no BMM artifact links captured yet")
    if not args.l0_reference:
        gaps.append("L0 references are not captured yet")
    gaps.append("acceptance criteria and evidence expectations need confirmation")
    return gaps


def emit(result: dict, output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


if __name__ == "__main__":
    sys.exit(main())
