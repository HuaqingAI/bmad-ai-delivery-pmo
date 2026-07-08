#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Discover BMM checkpoint candidates and write the intake registry."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from checkpoint_extractors import CHECKPOINTS, discover_candidate, render_preview
from checkpoint_registry import CandidateRegistry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Project root containing ADP memory and BMM/TEA artifacts.")
    parser.add_argument("--workstream-id", required=True, help="Workstream id. Normalized to lowercase hyphen-case.")
    parser.add_argument("--checkpoint", required=True, choices=sorted(CHECKPOINTS), help="BMM checkpoint type.")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="[KEY=]PATH",
        help="BMM/TEA source artifact, repeatable. If omitted, the script searches known artifact roots.",
    )
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
    return parser.parse_args()


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def shell_command(parts: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([str(part)]) for part in parts)


def confirmation_checklist(candidate: dict[str, Any], registry: CandidateRegistry, project_root: Path) -> dict[str, Any]:
    candidate_id = candidate["candidate_id"]
    authority = candidate.get("authority", {})
    script_path = Path(__file__).with_name("sync_bmm_checkpoint.py").resolve()
    return {
        "confirmation_required": True,
        "selected_artifacts": candidate.get("selected_artifacts", []),
        "ignored_artifacts": candidate.get("ignored_artifacts", []),
        "source_scope_key": candidate.get("artifact", {}).get("source_scope_key", ""),
        "authority_scope": authority.get("authority_scope", []),
        "affected_workstreams": authority.get("affected_workstreams", []),
        "required_confirmers": authority.get("required_confirmers", []),
        "confirmation_state": authority.get("confirmation_state", "discovered"),
        "review_paths": {
            "candidate_json": str(registry.candidate_path(candidate_id)),
            "preview_md": str(registry.preview_path(candidate_id)),
        },
        "next_commands": {
            "confirm": shell_command(
                [
                    sys.executable,
                    str(script_path),
                    "confirm",
                    str(project_root),
                    "--candidate-id",
                    candidate_id,
                    "--decision",
                    "confirm",
                    "--confirmed-by",
                    "<owner>",
                    "--override",
                    "authority.confirmation_state=confirmed-local",
                ]
            ),
            "dismiss": shell_command(
                [
                    sys.executable,
                    str(script_path),
                    "confirm",
                    str(project_root),
                    "--candidate-id",
                    candidate_id,
                    "--decision",
                    "dismiss",
                    "--confirmed-by",
                    "<owner>",
                ]
            ),
        },
    }


def run_discovery(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}
    try:
        candidate, preview, warnings = discover_candidate(
            project_root,
            args.workstream_id,
            args.checkpoint,
            args.artifact,
            summary=args.summary,
            asserted_by=args.asserted_by,
            authority_scope=args.authority_scope,
            affected_workstreams=args.affected_workstream,
            required_confirmers=args.required_confirmer,
        )
        registry = CandidateRegistry(resolve_memory_root(project_root, args.memory_root))
        checklist = confirmation_checklist(candidate, registry, project_root)
        candidate["confirmation_checklist"] = checklist
        preview = render_preview(candidate)
        result = registry.discover(candidate, preview, dry_run=args.dry_run)
        result.update(checklist)
        result["warnings"] = warnings
        return 0, result
    except Exception as exc:
        return 2, {"ok": False, "error": str(exc), "project_root": str(project_root)}


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


def main() -> int:
    args = parse_args()
    code, result = run_discovery(args)
    emit(result, args.output)
    return code


if __name__ == "__main__":
    sys.exit(main())
