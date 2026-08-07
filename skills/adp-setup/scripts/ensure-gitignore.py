#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Preview or install ADP panel-refresh runtime ignore rules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RULES = (
    "_bmad-output/adp/.adp-panel-refresh-staging/",
    "_bmad-output/adp/memory/state/panel-refresh/inspect-panel.json",
    "_bmad-output/adp/memory/state/panel-refresh/selection-policy-candidates.json",
)
MARKER = "# ADP panel-refresh runtime"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict:
    if "{project-root}" in args.project_root:
        return {"ok": False, "error": "project_root must be resolved", "status": "blocked"}
    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        return {"ok": False, "error": "project_root is not a directory", "status": "blocked"}
    path = root / ".gitignore"
    before = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    existing = {line.strip() for line in before.splitlines()}
    missing = [rule for rule in RULES if rule not in existing]
    changed = bool(missing and args.apply)
    if changed:
        suffix = "" if not before or before.endswith("\n") else "\n"
        block = "\n".join([MARKER, *missing]) + "\n"
        path.write_text(before + suffix + block, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "status": "complete" if args.apply else "preview",
        "gitignore": str(path),
        "apply": bool(args.apply),
        "changed": changed,
        "rules": list(RULES),
        "missing_before": missing,
    }


def main() -> int:
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
