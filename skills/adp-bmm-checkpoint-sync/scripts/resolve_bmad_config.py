#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Resolve BMad language configuration for adp-bmm-checkpoint-sync."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


CONFIG_PATHS = [
    "_bmad/adp/config.yaml",
    "_bmad/config.user.yaml",
    "_bmad/config.yaml",
    "_bmad/core/config.yaml",
    "_bmad/bmm/config.yaml",
    "_bmad/bmb/config.yaml",
]
LANGUAGE_KEYS = ["communication_language", "document_output_language"]
DEFAULT_LANGUAGE = "English"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Target project root containing BMad config files.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def parse_simple_yaml(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    warnings: list[str] = []
    section = ""
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.split("#", 1)[0].rstrip()
        if ":" not in stripped:
            warnings.append(f"{path}: ignored non key-value YAML at line {line_no}")
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = clean_scalar(value)
        if indent == 0 and value == "":
            section = key
            continue
        if indent == 0:
            section = ""
            values[key] = value
        elif section:
            values[f"{section}.{key}"] = value
    return values, warnings


def clean_scalar(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return re.sub(r"\s+", " ", text).strip()


def read_sources(project_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[dict[str, Any]] = []
    warnings: list[str] = []
    for rel_path in CONFIG_PATHS:
        path = project_root / rel_path
        source: dict[str, Any] = {"path": str(path), "relative_path": rel_path, "exists": path.exists()}
        if path.exists():
            try:
                values, parse_warnings = parse_simple_yaml(path)
                source["values"] = values
                source["keys"] = sorted(values)
                warnings.extend(parse_warnings)
            except OSError as exc:
                source["values"] = {}
                source["keys"] = []
                warnings.append(f"{path}: {exc}")
        sources.append(source)
    return sources, warnings


def first_value(sources: list[dict[str, Any]], key: str) -> tuple[str, str | None]:
    nested_keys = [key, f"adp.{key}", f"core.{key}", f"bmm.{key}", f"bmb.{key}"]
    for source in sources:
        if not source.get("exists"):
            continue
        values = source.get("values", {})
        for candidate_key in nested_keys:
            value = values.get(candidate_key)
            if value:
                return value, str(source["path"])
    return DEFAULT_LANGUAGE, None


def resolve_config(project_root: Path) -> tuple[int, dict[str, Any]]:
    project_root = project_root.resolve()
    if not project_root.exists() or not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}

    sources, warnings = read_sources(project_root)
    existing_sources = [source for source in sources if source.get("exists")]
    value_sources: dict[str, str | None] = {}
    result: dict[str, Any] = {
        "ok": True,
        "project_root": str(project_root),
        "primary_source": str(existing_sources[0]["path"]) if existing_sources else None,
        "sources_checked": [{k: v for k, v in source.items() if k != "values"} for source in sources],
        "warnings": warnings,
    }
    for key in LANGUAGE_KEYS:
        value, source = first_value(sources, key)
        result[key] = value
        value_sources[key] = source
        if source is None:
            result["warnings"].append(f"{key} not found; using {DEFAULT_LANGUAGE}")
    result["value_sources"] = value_sources
    if not existing_sources:
        result["warnings"].append("no BMad config file found; using English language defaults")
    return 0, result


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


def main() -> int:
    args = parse_args()
    code, result = resolve_config(Path(args.project_root))
    emit(result, args.output)
    return code


if __name__ == "__main__":
    sys.exit(main())
