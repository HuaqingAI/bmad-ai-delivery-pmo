#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Create the AI Delivery PMO shared memory scaffold idempotently."""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "assets" / "adp-memory-templates"

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

SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__"}

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
    return {
        "planning": planning,
        "implementation": implementation,
        "counts": {
            "planning": len(planning),
            "implementation": len(implementation),
            "total": len(planning) + len(implementation),
        },
    }


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
    config_values, config_sources = load_bmad_config(project_root)
    discovered_artifacts = discover_bmad_artifacts(project_root, config_values)
    project_name = args.project_name or project_root.name

    if (
        discovered_artifacts["counts"]["total"] > 0
        and not args.dry_run
        and not args.yes
        and not args.headless
    ):
        result = {
            "ok": False,
            "confirmation_required": True,
            "dry_run": False,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
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
                "Summarize discovered BMad artifacts to the user and confirm ADP kickoff should initialize a coordination layer.",
                "Rerun with --yes or --headless after confirmation.",
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

    result = {
        "ok": not errors,
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
