#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Create starter ADP files for a single FDE workstream idempotently."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "assets" / "workstream-templates"
SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"
DEFAULT_SCOPE_CONTRACT_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "scope_contract.py"
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
        description="Idempotently create ADP workstream record files under _bmad-output/adp/memory/workstreams/{id}.",
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
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument(
        "--allow-partial-memory",
        action="store_true",
        help="Allow writing when kickoff-created ADP core files are missing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without creating files.")
    parser.add_argument("--language", help="Override document_output_language for reviewable output.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
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


def canonical_cross_workstream_ids(items: list[str], label: str) -> list[str]:
    normalized: list[str] = []
    for raw in items:
        value = str(raw).strip()
        if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", value) or value.lower() in {
            "tbd",
            "todo",
            "none",
            "unknown",
        }:
            raise ValueError(f"{label} accepts canonical workstream IDs only: {raw!r}")
        workstream_id = value.lower()
        if workstream_id not in normalized:
            normalized.append(workstream_id)
    return normalized


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


def bullet_list(items: list[str], *, placeholder: bool = True) -> str:
    if items:
        return "\n".join(f"- {item}" for item in items)
    return "- TBD" if placeholder else ""


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


def patch_plan_content(args: argparse.Namespace, workstream_id: str, artifacts: dict[str, str], gaps: list[str], now: str, message) -> str:
    artifact_rows = artifact_table(artifacts)
    depends_on = bullet_list(args.depends_on, placeholder=False)
    impacts = bullet_list(args.impacts, placeholder=False)
    l0_references = bullet_list(args.l0_reference)
    gaps_list = bullet_list(gaps)
    return f"""# {message('workstream.patch.title')}

{message('common.generated')}: {now}
{message('common.workstream')}: {workstream_id} - {args.name}

{message('workstream.patch.note')}

## {message('workstream.patch.identity')}

- {message('workstream.fde_owner')}: {args.owner}
- {message('workstream.business_owner')}: {args.business_owner}
- {message('workstream.bmm_phase')}: {args.phase}
- {message('workstream.proposed_status')}: {args.status}

## {message('workstream.patch.scope')}

- {message('workstream.in_scope')}: {args.scope}

## {message('workstream.patch.artifacts')}

| {message('workstream.artifact')} | {message('workstream.path_link')} | {message('workstream.baseline_status')} | {message('workstream.notes')} |
| --- | --- | --- | --- |
{artifact_rows}

## {message('workstream.patch.links')}

{message('workstream.depends_on')}:

{depends_on}

{message('workstream.impacts')}:

{impacts}

{message('workstream.l0_references')}:

{l0_references}

## {message('workstream.patch.gaps')}

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

    config_module = load_module(Path(args.config_script), "adp_workstream_effective_config")
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        emit({"ok": False, "error": config.get("error", "shared ADP effective config could not be resolved")}, args.output)
        return 2
    locale = str(config.get("document_locale") or "en")

    def message(key: str) -> str:
        return config_module.message(key, locale)

    try:
        workstream_id = normalize_id(args.id)
        scope_module = load_module(DEFAULT_SCOPE_CONTRACT_SCRIPT, "adp_workstream_scope_contract")
        if scope_module.is_virtual_cli_scope_id(args.id):
            emit(
                {
                    "ok": False,
                    "error_code": "ADP-VIRTUAL-SCOPE-NOT-WORKSTREAM",
                    "error": "program is a reserved virtual scope and cannot own a WDR or BMM artifacts",
                    "workstream_id": workstream_id,
                },
                args.output,
            )
            return 2
        args.depends_on = canonical_cross_workstream_ids(args.depends_on, "--depends-on")
        args.impacts = canonical_cross_workstream_ids(args.impacts, "--impacts")
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
        "DEPENDS_ON": bullet_list(args.depends_on, placeholder=False),
        "IMPACTS": bullet_list(args.impacts, placeholder=False),
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
            patch_plan_content(args, workstream_id, artifacts, gaps, now, message),
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
        "next_actions": [message("workstream.next.fill"), message("workstream.next.checkpoint"), message("workstream.next.readiness")],
        "language": language_metadata(config, locale),
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


def emit(result: dict, output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
