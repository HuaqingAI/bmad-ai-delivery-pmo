#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Compatibility entry point for the read-only Program Lead status consumer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from consume_program_status import main as consume_main


MIGRATION_ERROR = "ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED"
RETIRED_RENDERER_OPTIONS = {
    "--prepass-json",
    "--audit-json",
    "--prepass-script",
    "--audit-script",
    "--output-dir",
    "--period",
    "--audience",
    "--max-actions",
    "--max-workstreams",
}


def retired_options(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    for option in sorted(RETIRED_RENDERER_OPTIONS):
        parser.add_argument(option)
    namespace, _ = parser.parse_known_args(argv)
    return sorted(
        option
        for option in RETIRED_RENDERER_OPTIONS
        if getattr(namespace, option.removeprefix("--").replace("-", "_")) is not None
    )


def result_output_path(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value.startswith("--output="):
            return value.split("=", 1)[1]
        if value in {"-o", "--output"} and index + 1 < len(argv):
            return argv[index + 1]
    return None


def migration_result(options: list[str]) -> dict[str, object]:
    return {
        "ok": False,
        "status": "migration-required",
        "mode": "canonical-consumer",
        "error_code": MIGRATION_ERROR,
        "unsupported_options": sorted(set(options)),
        "reason": "render_program_views.py no longer renders management Markdown from prepass or audit inputs",
        "replacement": {
            "producer": "adp-program-status",
            "producer_action": "generate canonical JSON, immutable snapshot, project-lead.md, and weekly-report.md",
            "consumer": "adp-agent-program-lead",
            "consumer_action": "read lineage-validated canonical management views",
        },
        "compatible_options": ["project_root", "--view", "--memory-root", "--as-of", "-o", "--output"],
        "writes_performed": [],
    }


def emit_migration(payload: dict[str, object], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        with Path(output).expanduser().open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def main() -> int:
    argv = sys.argv[1:]
    retired = retired_options(argv)
    if retired:
        emit_migration(migration_result(retired), result_output_path(argv))
        return 2
    return consume_main()


if __name__ == "__main__":
    sys.exit(main())
