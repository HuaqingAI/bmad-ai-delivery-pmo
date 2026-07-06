#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Create the AI Delivery PMO shared memory scaffold idempotently."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "assets" / "adp-memory-templates"
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
LEGACY_MEMORY_ROOT = "_bmad/memory/adp"

CONFIG_PATHS = [
    "_bmad/adp/config.yaml",
    "_bmad/config.user.yaml",
    "_bmad/config.yaml",
    "_bmad/core/config.yaml",
    "_bmad/bmm/config.yaml",
    "_bmad/bmb/config.yaml",
]

PLANNING_PATTERNS = {
    "prd": ["*prd*.md"],
    "architecture": ["*architecture*.md", "*architect*.md"],
    "epics": ["*epic*.md", "*epics*.md"],
    "ux_design": ["*ux*.md", "*design*.md"],
    "brief": ["*brief*.md"],
    "research": ["*research*.md"],
    "requirements": ["*requirements*.md", "*requirement*.md"],
}

IMPLEMENTATION_PATTERNS = {
    "story": ["*story*.md", "*stories*.md"],
    "sprint_status": ["*sprint-status*.yaml", "*sprint-status*.yml"],
    "validation": ["*validation*.md", "*validate*.md"],
    "project_context": ["*project-context.md"],
}

ARTIFACT_LABELS = {
    "prd": "PRD",
    "prd_path": "PRD",
    "architecture": "Architecture",
    "architecture_path": "Architecture",
    "epics": "Epics / stories",
    "stories": "Epics / stories",
    "epics_path": "Epics / stories",
    "stories_path": "Epics / stories",
    "code": "Code / PR",
    "code_path": "Code / PR",
    "pr": "Code / PR",
    "validation": "Validation evidence",
    "validation_path": "Validation evidence",
    "evidence": "Validation evidence",
}

ADP_STATUSES = {"draft", "gap", "ready"}

SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__"}

DIRECTORIES = [
    "",
    "schemas",
    "l0",
    "meetings",
    "decisions",
    "decisions/business-decision-packets",
    "intake",
    "intake/status-sync",
    "actions",
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
    "schemas/action-ledger.md",
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
    "actions/action-ledger.md",
    "views/project-lead.md",
    "views/fde-actions.md",
    "views/acceptance-readiness.md",
    "views/risk-matrix.md",
    "views/dependency-map.md",
    "views/weekly-report.md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently create _bmad-output/adp/memory project memory files.",
    )
    parser.add_argument("project_root", help="Project root where ADP memory should be created.")
    parser.add_argument(
        "--memory-root",
        default=DEFAULT_MEMORY_ROOT,
        help=f"Memory root, relative to project root unless absolute. Default: {DEFAULT_MEMORY_ROOT}.",
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
    parser.add_argument(
        "--workstream-plan",
        default="",
        help=(
            "JSON plan with confirmed workstreams to persist as an intake registration plan. "
            "Relative paths resolve from project root."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Non-interactive run after caller confirmation.")
    parser.add_argument("--headless", action="store_true", help="Alias for non-interactive run after caller confirmation.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without creating files.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def legacy_memory_status(project_root: Path, memory_root: Path) -> dict[str, str | bool]:
    legacy_root = resolve_memory_root(project_root, LEGACY_MEMORY_ROOT)
    legacy_exists = legacy_root.exists()
    using_legacy = legacy_root == memory_root
    migration_note = ""
    if legacy_exists and not using_legacy:
        migration_note = (
            "Legacy ADP memory exists under _bmad/memory/adp. Migrate it to "
            "_bmad-output/adp/memory, or rerun with --memory-root _bmad/memory/adp "
            "to continue using the existing memory."
        )
    return {
        "legacy_memory_root": str(legacy_root),
        "legacy_memory_exists": legacy_exists,
        "using_legacy_memory_root": using_legacy,
        "migration_note": migration_note,
    }


def render_template(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def parse_config_file(path: Path) -> dict[str, str]:
    """Parse simple top-level YAML scalars used by BMad config files."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or not value:
            continue
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def load_bmad_config(project_root: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    sources: list[str] = []
    for rel in CONFIG_PATHS:
        path = project_root / rel
        if not path.exists():
            continue
        sources.append(str(path))
        try:
            parsed = parse_config_file(path)
        except UnicodeDecodeError:
            continue
        for key, value in parsed.items():
            values.setdefault(key, value)
    return values, sources


def resolve_project_path(project_root: Path, raw_path: str) -> Path:
    value = raw_path.replace("{project-root}", str(project_root)).strip()
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def unique_existing_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen or not path.exists():
            continue
        seen.add(key)
        result.append(path)
    return result


def iter_candidate_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def match_category(path: Path, patterns_by_category: dict[str, list[str]]) -> str | None:
    name = path.name.lower()
    for category, patterns in patterns_by_category.items():
        if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
            return category
    return None


def collect_artifacts(
    roots: Iterable[Path],
    patterns_by_category: dict[str, list[str]],
    kind: str,
    limit: int = 50,
) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        for path in iter_candidate_files(root):
            category = match_category(path, patterns_by_category)
            if not category:
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            artifacts.append({"kind": kind, "category": category, "path": str(path)})
            if len(artifacts) >= limit:
                return artifacts
    return artifacts


def normalize_workstream_id(raw: str, fallback: str = "") -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if not value and fallback:
        value = fallback
    if not value:
        raise ValueError("workstream id must contain at least one letter or digit")
    return value


def title_from_path(path: str) -> str:
    stem = Path(path).stem
    stem = re.sub(r"(?i)(^|[-_\s])prd($|[-_\s])", " ", stem)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = " ".join(stem.split())
    return stem.title() if stem else Path(path).stem


def candidate_workstreams_from_artifacts(discovered_artifacts: dict[str, object]) -> list[dict[str, str]]:
    planning = discovered_artifacts.get("planning", [])
    if not isinstance(planning, list):
        return []

    candidates: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for item in planning:
        if not isinstance(item, dict) or item.get("category") != "prd":
            continue
        path = str(item.get("path", ""))
        if not path:
            continue
        base_name = title_from_path(path)
        try:
            workstream_id = normalize_workstream_id(base_name, f"workstream-{len(candidates) + 1}")
        except ValueError:
            continue
        original_id = workstream_id
        suffix = 2
        while workstream_id in used_ids:
            workstream_id = f"{original_id}-{suffix}"
            suffix += 1
        used_ids.add(workstream_id)
        candidates.append(
            {
                "id": workstream_id,
                "name": base_name,
                "prd_path": path,
                "suggested_phase": "PRD",
                "suggested_status": "draft",
            }
        )
    return candidates


def discover_bmad_artifacts(project_root: Path, config_values: dict[str, str]) -> dict[str, object]:
    planning_roots: list[Path] = []
    implementation_roots: list[Path] = []

    if config_values.get("planning_artifacts"):
        planning_roots.append(resolve_project_path(project_root, config_values["planning_artifacts"]))
    if config_values.get("implementation_artifacts"):
        implementation_roots.append(resolve_project_path(project_root, config_values["implementation_artifacts"]))

    planning_roots.extend(
        [
            project_root / "_bmad-output" / "planning-artifacts",
            project_root / "docs",
            project_root / "doc",
        ]
    )
    implementation_roots.extend(
        [
            project_root / "_bmad-output" / "implementation-artifacts",
            project_root / "docs",
            project_root / "doc",
        ]
    )

    planning = collect_artifacts(unique_existing_paths(planning_roots), PLANNING_PATTERNS, "planning")
    implementation = collect_artifacts(
        unique_existing_paths(implementation_roots),
        IMPLEMENTATION_PATTERNS,
        "implementation",
    )
    discovered = {
        "planning": planning,
        "implementation": implementation,
        "counts": {
            "planning": len(planning),
            "implementation": len(implementation),
            "total": len(planning) + len(implementation),
        },
    }
    discovered["candidate_workstreams"] = candidate_workstreams_from_artifacts(discovered)
    return discovered


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


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_or_tbd(value: Any) -> str:
    if value is None:
        return "TBD"
    text = str(value).strip()
    return text if text else "TBD"


def markdown_escape_table_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")


def normalize_artifacts(raw_workstream: dict[str, Any], project_root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    raw_artifacts = raw_workstream.get("artifacts", {})
    if isinstance(raw_artifacts, dict):
        items = raw_artifacts.items()
    else:
        items = []

    for raw_key, raw_value in items:
        if raw_value in (None, ""):
            continue
        key = str(raw_key).strip().lower().replace("-", "_")
        label = ARTIFACT_LABELS.get(key, str(raw_key).strip())
        artifacts[label] = resolve_artifact_link(project_root, str(raw_value))

    for key, label in ARTIFACT_LABELS.items():
        if key not in raw_workstream or raw_workstream[key] in (None, ""):
            continue
        artifacts[label] = resolve_artifact_link(project_root, str(raw_workstream[key]))
    return artifacts


def resolve_artifact_link(project_root: Path, raw_value: str) -> str:
    value = raw_value.strip().replace("{project-root}", str(project_root))
    if not value or "://" in value or value.startswith("#"):
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((project_root / path).resolve())


def load_workstream_plan(project_root: Path, raw_plan: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not raw_plan:
        return [], []

    plan_path = Path(raw_plan)
    if not plan_path.is_absolute():
        plan_path = project_root / plan_path
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [{"path": str(plan_path), "error": f"cannot read workstream plan: {exc}"}]

    if isinstance(payload, dict):
        raw_workstreams = payload.get("workstreams", [])
    elif isinstance(payload, list):
        raw_workstreams = payload
    else:
        return [], [{"path": str(plan_path), "error": "workstream plan must be a JSON object or array"}]

    if not isinstance(raw_workstreams, list):
        return [], [{"path": str(plan_path), "error": "workstreams must be a list"}]

    workstreams: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for index, raw_workstream in enumerate(raw_workstreams, start=1):
        if not isinstance(raw_workstream, dict):
            errors.append({"path": str(plan_path), "error": f"workstream #{index} is not an object"})
            continue
        try:
            workstream_id = normalize_workstream_id(
                text_or_tbd(raw_workstream.get("id") or raw_workstream.get("name")),
                f"workstream-{index}",
            )
        except ValueError as exc:
            errors.append({"path": str(plan_path), "error": f"workstream #{index}: {exc}"})
            continue
        if workstream_id in used_ids:
            errors.append({"path": str(plan_path), "error": f"duplicate workstream id: {workstream_id}"})
            continue
        used_ids.add(workstream_id)

        status = text_or_tbd(raw_workstream.get("status") or raw_workstream.get("adp_status")).lower()
        if status == "tbd":
            status = "draft"
        if status not in ADP_STATUSES:
            errors.append(
                {
                    "path": str(plan_path),
                    "error": f"workstream {workstream_id}: status must be one of draft, gap, ready",
                }
            )
            continue

        workstreams.append(
            {
                "id": workstream_id,
                "name": text_or_tbd(raw_workstream.get("name") or workstream_id),
                "fde_owner": text_or_tbd(raw_workstream.get("fde_owner") or raw_workstream.get("owner")),
                "business_owner": text_or_tbd(raw_workstream.get("business_owner")),
                "phase": text_or_tbd(raw_workstream.get("phase") or raw_workstream.get("bmm_phase") or "PRD"),
                "status": status,
                "scope": text_or_tbd(raw_workstream.get("scope") or raw_workstream.get("scope_summary")),
                "acceptance": text_or_tbd(raw_workstream.get("acceptance") or raw_workstream.get("acceptance_summary")),
                "open_questions": [text_or_tbd(item) for item in as_list(raw_workstream.get("open_questions"))],
                "risks": [text_or_tbd(item) for item in as_list(raw_workstream.get("risks"))],
                "dependencies": [text_or_tbd(item) for item in as_list(raw_workstream.get("dependencies"))],
                "impacts": [text_or_tbd(item) for item in as_list(raw_workstream.get("impacts"))],
                "l0_references": [text_or_tbd(item) for item in as_list(raw_workstream.get("l0_references"))],
                "next_actions": [text_or_tbd(item) for item in as_list(raw_workstream.get("next_actions"))],
                "analysis_notes": [text_or_tbd(item) for item in as_list(raw_workstream.get("analysis_notes"))],
                "artifacts": normalize_artifacts(raw_workstream, project_root),
            }
        )
    return workstreams, errors


def register_input_block(workstream: dict[str, Any]) -> str:
    return json.dumps(workstream, ensure_ascii=False, indent=2)


def workstream_summary_row(workstream: dict[str, Any]) -> str:
    prd_path = workstream["artifacts"].get("PRD", "TBD")
    return (
        f"| {markdown_escape_table_cell(workstream['id'])} "
        f"| {markdown_escape_table_cell(workstream['name'])} "
        f"| {markdown_escape_table_cell(workstream['fde_owner'])} "
        f"| {markdown_escape_table_cell(workstream['phase'])} "
        f"| {markdown_escape_table_cell(prd_path)} "
        f"| {markdown_escape_table_cell(workstream['scope'])} |"
    )


def render_registration_plan_markdown(workstreams: list[dict[str, Any]], now: str) -> str:
    rows = "\n".join(workstream_summary_row(workstream) for workstream in workstreams)
    if not rows:
        rows = "| TBD | TBD | TBD | TBD | TBD | No PRD lines included by user confirmation. |"
    input_blocks = "\n\n".join(
        f"### {workstream['id']}\n\n```json\n{register_input_block(workstream)}\n```" for workstream in workstreams
    )
    if not input_blocks:
        input_blocks = "No register inputs because no workstreams were confirmed."
    return f"""# Workstream Registration Plan

Generated: {now}
Source: adp-project-kickoff confirmed PRD intake

This plan is kickoff intake for `adp-workstream-register`. It does not create or normalize Workstream Delivery Records. Use it to register each confirmed FDE workstream through `adp-workstream-register`, then run checkpoint sync when the PRD baseline is accepted.

## Confirmed Workstreams

| Workstream ID | Name | FDE owner | BMM phase | PRD path | Scope summary |
| --- | --- | --- | --- | --- | --- |
{rows}

## Register Inputs

{input_blocks}
"""


def write_registration_plan(
    memory_root: Path,
    workstreams: list[dict[str, Any]],
    now: str,
    dry_run: bool,
) -> tuple[list[str], list[str], list[dict[str, str]], dict[str, Any]]:
    created: list[str] = []
    existing: list[str] = []
    errors: list[dict[str, str]] = []
    plan_root = memory_root / "intake"
    json_path = plan_root / "workstream-registration-plan.json"
    markdown_path = plan_root / "workstream-registration-plan.md"
    summary = {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "count": len(workstreams),
        "workstreams": [
            {
                "id": workstream["id"],
                "name": workstream["name"],
                "prd_path": workstream["artifacts"].get("PRD", ""),
                "status": workstream["status"],
            }
            for workstream in workstreams
        ],
    }
    if not workstreams:
        return created, existing, errors, {}

    payload = {
        "generated_at": now,
        "source": "adp-project-kickoff confirmed PRD intake",
        "owner_skill": "adp-workstream-register",
        "workstreams": workstreams,
    }
    for target, content in [
        (json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
        (markdown_path, render_registration_plan_markdown(workstreams, now)),
    ]:
        if target.exists():
            existing.append(str(target))
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        created.append(str(target))
    return created, existing, errors, summary


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        result = {
            "ok": False,
            "status": "blocked",
            "reason": "project_root is not an existing directory",
            "error": "project_root is not an existing directory",
            "project_root": str(project_root),
        }
        emit(result, args.output)
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    legacy_status = legacy_memory_status(project_root, memory_root)
    config_values, config_sources = load_bmad_config(project_root)
    discovered_artifacts = discover_bmad_artifacts(project_root, config_values)
    confirmed_workstreams, workstream_plan_errors = load_workstream_plan(project_root, args.workstream_plan)
    project_name = args.project_name or project_root.name

    if workstream_plan_errors:
        result = {
            "ok": False,
            "status": "blocked",
            "reason": "workstream plan JSON could not be loaded",
            "dry_run": args.dry_run,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "project_name": project_name,
            "profile": args.profile,
            "cadence": args.cadence,
            "non_interactive": bool(args.yes or args.headless),
            "config_sources": config_sources,
            "language": {
                "communication_language": config_values.get("communication_language", "English"),
                "document_output_language": config_values.get("document_output_language", "English"),
            },
            "discovered_bmad_artifacts": discovered_artifacts,
            "confirmed_workstreams": [],
            "workstream_registration_plan": {},
            "directories_created": [],
            "directories_existing": [],
            "files_created": [],
            "files_existing": [],
            "errors": workstream_plan_errors,
            "next_actions": ["Fix the workstream plan JSON, then rerun kickoff."],
        }
        emit(result, args.output)
        return 2

    if (
        legacy_status["legacy_memory_exists"]
        and not legacy_status["using_legacy_memory_root"]
        and args.memory_root == DEFAULT_MEMORY_ROOT
        and not args.dry_run
        and not args.yes
        and not args.headless
    ):
        result = {
            "ok": False,
            "status": "blocked",
            "reason": "legacy ADP memory found outside the default output root",
            "confirmation_required": True,
            "legacy_memory_confirmation_required": True,
            "dry_run": False,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "legacy_memory": legacy_status,
            "project_name": project_name,
            "profile": args.profile,
            "cadence": args.cadence,
            "non_interactive": False,
            "config_sources": config_sources,
            "language": {
                "communication_language": config_values.get("communication_language", "English"),
                "document_output_language": config_values.get("document_output_language", "English"),
            },
            "discovered_bmad_artifacts": discovered_artifacts,
            "confirmed_workstreams": confirmed_workstreams,
            "workstream_registration_plan": {},
            "directories_created": [],
            "directories_existing": [],
            "files_created": [],
            "files_existing": [],
            "errors": [
                {
                    "path": str(project_root),
                    "error": "legacy ADP memory found; confirm migration target or pass --memory-root _bmad/memory/adp",
                }
            ],
            "next_actions": [
                str(legacy_status["migration_note"]),
                "Rerun with --yes or --headless after confirming that new ADP memory should be created under _bmad-output/adp/memory.",
            ],
        }
        emit(result, args.output)
        return 4

    if (
        discovered_artifacts.get("candidate_workstreams")
        and not args.workstream_plan
        and not args.dry_run
    ):
        candidate_workstreams = discovered_artifacts.get("candidate_workstreams", [])
        result = {
            "ok": False,
            "status": "blocked",
            "reason": "existing PRD artifacts require a confirmed workstream plan before kickoff writes memory",
            "confirmation_required": True,
            "workstream_plan_required": True,
            "dry_run": False,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "legacy_memory": legacy_status,
            "project_name": project_name,
            "profile": args.profile,
            "cadence": args.cadence,
            "non_interactive": bool(args.yes or args.headless),
            "config_sources": config_sources,
            "language": {
                "communication_language": config_values.get("communication_language", "English"),
                "document_output_language": config_values.get("document_output_language", "English"),
            },
            "discovered_bmad_artifacts": discovered_artifacts,
            "candidate_workstreams": candidate_workstreams,
            "next_required_input": {
                "flag": "--workstream-plan",
                "type": "json_file",
                "description": "Confirmed PRD-derived workstreams, or an empty workstreams array when every PRD candidate is intentionally excluded.",
            },
            "confirmed_workstreams": [],
            "workstream_registration_plan": {},
            "directories_created": [],
            "directories_existing": [],
            "files_created": [],
            "files_existing": [],
            "errors": [
                {
                    "path": str(project_root),
                    "error": "existing PRD artifacts found; confirm included workstreams and pass --workstream-plan before persisting kickoff memory",
                }
            ],
            "next_actions": [
                "Summarize candidate workstreams from discovered PRDs, confirm which lines to include, quickly analyze selected PRDs, then rerun with --workstream-plan <json-file> to persist the registration plan.",
                "Use an empty workstream plan only if the user intentionally excludes every discovered PRD line.",
            ],
        }
        emit(result, args.output)
        return 3

    if (
        discovered_artifacts["counts"]["total"] > 0
        and not args.workstream_plan
        and not args.dry_run
        and not args.yes
        and not args.headless
    ):
        result = {
            "ok": False,
            "status": "blocked",
            "reason": "existing BMad artifacts require caller confirmation before kickoff writes memory",
            "confirmation_required": True,
            "dry_run": False,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "legacy_memory": legacy_status,
            "project_name": project_name,
            "profile": args.profile,
            "cadence": args.cadence,
            "non_interactive": False,
            "config_sources": config_sources,
            "language": {
                "communication_language": config_values.get("communication_language", "English"),
                "document_output_language": config_values.get("document_output_language", "English"),
            },
            "discovered_bmad_artifacts": discovered_artifacts,
            "confirmed_workstreams": [],
            "workstream_registration_plan": {},
            "directories_created": [],
            "directories_existing": [],
            "files_created": [],
            "files_existing": [],
            "errors": [
                {
                    "path": str(project_root),
                    "error": "existing BMad artifacts found; rerun with --dry-run for preview or --yes after user confirmation",
                }
            ],
            "next_actions": [
                *(
                    [
                        "Summarize candidate workstreams from discovered PRDs, confirm which lines to include, quickly analyze selected PRDs, then rerun with --workstream-plan <json-file> to persist the registration plan.",
                    ]
                    if discovered_artifacts.get("candidate_workstreams")
                    else [
                        "Summarize discovered BMad artifacts to the user and confirm ADP kickoff should initialize a coordination layer.",
                        "Rerun with --yes or --headless after confirmation.",
                    ]
                ),
                *([] if not legacy_status["migration_note"] else [str(legacy_status["migration_note"])]),
            ],
        }
        emit(result, args.output)
        return 3

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
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
        print(f"Config sources: {config_sources}", file=sys.stderr)

    dirs_created, dirs_existing = ensure_directories(memory_root, DIRECTORIES, args.dry_run)
    files_created, files_existing, errors = write_templates(memory_root, TEMPLATE_FILES, values, args.dry_run)
    plan_files_created, plan_files_existing, plan_errors, registration_plan = write_registration_plan(
        memory_root,
        confirmed_workstreams,
        now,
        args.dry_run,
    )
    files_created.extend(plan_files_created)
    files_existing.extend(plan_files_existing)
    errors.extend(plan_errors)

    result = {
        "ok": not errors,
        "status": "complete" if not errors else "blocked",
        "reason": "" if not errors else "one or more scaffold writes failed",
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "legacy_memory": legacy_status,
        "project_name": project_name,
        "profile": args.profile,
        "cadence": args.cadence,
        "non_interactive": bool(args.yes or args.headless),
        "config_sources": config_sources,
        "language": {
            "communication_language": config_values.get("communication_language", "English"),
            "document_output_language": config_values.get("document_output_language", "English"),
        },
        "discovered_bmad_artifacts": discovered_artifacts,
        "confirmed_workstreams": confirmed_workstreams,
        "workstream_registration_plan": registration_plan,
        "directories_created": dirs_created,
        "directories_existing": dirs_existing,
        "files_created": files_created,
        "files_existing": files_existing,
        "errors": errors,
        "next_actions": [
            "Fill project-charter.md with objective, stakeholders, scope boundaries, and escalation path.",
            *([] if not legacy_status["migration_note"] else [str(legacy_status["migration_note"])]),
            *(
                [
                    "Review intake/workstream-registration-plan.md.",
                    "Run adp-workstream-register for each confirmed FDE workstream in the plan.",
                ]
                if registration_plan
                else ["Run adp-workstream-register for each active FDE workstream."]
            ),
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
