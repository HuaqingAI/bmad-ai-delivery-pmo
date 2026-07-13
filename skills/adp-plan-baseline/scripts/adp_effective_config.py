#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Resolve ADP effective config, locale, and activation routing."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


CONFIG_PATHS = (
    "_bmad/adp/config.yaml",
    "_bmad/config.user.yaml",
    "_bmad/config.yaml",
    "_bmad/core/config.yaml",
    "_bmad/bmm/config.yaml",
    "_bmad/bmb/config.yaml",
)
MEMORY_RELATIVE = Path("_bmad-output/adp/memory")
BASELINE_RELATIVE = MEMORY_RELATIVE / "plans/program-baseline.md"

DEFAULTS: dict[str, Any] = {
    "communication_language": "English",
    "document_output_language": "English",
    "output_folder": "{project-root}/_bmad-output",
    "default_reporting_cadence": "weekly",
    "status_stale_after_days": 7,
    "schedule_variance_tolerance_days": 0,
    "meeting_pack_item_limit": 10,
}

MODULE_KEYS = {
    "default_reporting_cadence",
    "status_stale_after_days",
    "schedule_variance_tolerance_days",
    "meeting_pack_item_limit",
}

LANGUAGE_ALIASES = {
    "chinese": "zh",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "简体中文": "zh",
    "中文": "zh",
    "english": "en",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
}


def clean_scalar(value: str) -> Any:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    text = re.sub(r"\s+", " ", text).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def parse_simple_yaml(path: Path) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    warnings: list[str] = []
    sections: list[tuple[int, str]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        content = raw.split("#", 1)[0].rstrip()
        if ":" not in content:
            warnings.append(f"{path}: ignored non key-value YAML at line {line_no}")
            continue
        indent = len(content) - len(content.lstrip(" "))
        key, raw_value = content.strip().split(":", 1)
        while sections and indent <= sections[-1][0]:
            sections.pop()
        value = clean_scalar(raw_value)
        if value == "":
            sections.append((indent, key.strip()))
            continue
        dotted = ".".join([section for _, section in sections] + [key.strip()])
        values[dotted] = value
    return values, warnings


def _candidate_keys(key: str) -> tuple[str, ...]:
    if key in MODULE_KEYS:
        return (f"adp.{key}", key)
    return (key, f"core.{key}", f"adp.{key}", f"bmm.{key}", f"bmb.{key}")


def _normalize_locale(value: Any) -> str | None:
    return LANGUAGE_ALIASES.get(str(value).strip().lower())


def _validate_value(key: str, value: Any) -> str | None:
    if key == "default_reporting_cadence" and value not in {"weekly", "biweekly", "custom"}:
        return "must be weekly, biweekly, or custom"
    ranges = {
        "status_stale_after_days": (1, 90),
        "schedule_variance_tolerance_days": (0, 90),
        "meeting_pack_item_limit": (3, 30),
    }
    if key in ranges:
        low, high = ranges[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            return f"must be an integer from {low} through {high}"
    return None


def resolve_effective_config(
    project_root: Path, overrides: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    project_root = project_root.resolve()
    if not project_root.is_dir():
        return 2, {"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}

    warnings: list[str] = []
    sources: list[dict[str, Any]] = []
    for relative_path in CONFIG_PATHS:
        path = project_root / relative_path
        record: dict[str, Any] = {"relative_path": relative_path, "path": str(path), "exists": path.is_file()}
        if path.is_file():
            try:
                record["values"], parse_warnings = parse_simple_yaml(path)
                warnings.extend(parse_warnings)
            except OSError as exc:
                record["values"] = {}
                warnings.append(f"{path}: {exc}")
        else:
            record["values"] = {}
        sources.append(record)

    effective: dict[str, Any] = {}
    value_sources: dict[str, str] = {}
    fallbacks: list[str] = []
    overrides = overrides or {}
    for key, default in DEFAULTS.items():
        if key in overrides:
            value = overrides[key]
            source = "cli"
        else:
            value = None
            source = ""
            for record in sources:
                for candidate in _candidate_keys(key):
                    candidate_value = record["values"].get(candidate)
                    if candidate_value not in {None, ""}:
                        value = candidate_value
                        source = record["path"]
                        break
                if source:
                    break
            if not source:
                value = default
                source = "built-in default"
                fallbacks.append(key)
                warnings.append(f"{key} not found; using {default!r} from built-in defaults")
        error = _validate_value(key, value)
        if error:
            warnings.append(f"{key} {error}; using built-in default {default!r}")
            value = default
            source = "built-in default after invalid value"
            if key not in fallbacks:
                fallbacks.append(key)
        effective[key] = value
        value_sources[key] = source

    locales: dict[str, str] = {}
    for key, output_key in (
        ("communication_language", "communication_locale"),
        ("document_output_language", "document_locale"),
    ):
        locale = _normalize_locale(effective[key])
        if locale is None:
            warnings.append(f"unsupported {key} {effective[key]!r}; falling back to English")
            locale = "en"
            value_sources[key] = f"{value_sources[key]} (unsupported; English fallback)"
            if key not in fallbacks:
                fallbacks.append(key)
        locales[output_key] = locale

    memory_path = project_root / MEMORY_RELATIVE
    baseline_path = project_root / BASELINE_RELATIVE
    memory_exists = memory_path.is_dir()
    baseline_exists = baseline_path.is_file()
    routing_state = "kickoff_required" if not memory_exists else "baseline_ready" if baseline_exists else "baseline_missing"
    public_sources = [{k: v for k, v in source.items() if k != "values"} for source in sources]
    return 0, {
        "ok": True,
        "project_root": str(project_root),
        "values": effective,
        "value_sources": value_sources,
        **locales,
        "fallbacks": fallbacks,
        "warnings": warnings,
        "sources_checked": public_sources,
        "memory_path": str(memory_path),
        "baseline_path": str(baseline_path),
        "memory_exists": memory_exists,
        "baseline_exists": baseline_exists,
        "routing_state": routing_state,
    }


def load_catalog() -> dict[str, dict[str, str]]:
    path = Path(__file__).resolve().parent.parent / "assets" / "locale-catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def message(key: str, locale: str, **values: Any) -> str:
    catalog = load_catalog()
    selected = catalog.get(locale) or catalog["en"]
    template = selected.get(key) or catalog["en"].get(key) or key
    return template.format(**values)


def display_label(enum_name: str, canonical_value: str, locale: str) -> str:
    return message(f"enum.{enum_name}.{canonical_value}", locale)


def format_date(value: str, locale: str) -> str:
    parsed = date.fromisoformat(value)
    return message(
        "date.display",
        locale,
        year=f"{parsed.year:04d}",
        month=f"{parsed.month:02d}",
        day=f"{parsed.day:02d}",
    )


def preserve_source_fact(
    text: str,
    source: dict[str, Any],
    translated_text: str | None = None,
    translated_locale: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"original": text, "source": source}
    if translated_text is not None:
        result["display_translation"] = {
            "text": translated_text,
            "locale": translated_locale or "und",
            "persistence": "derived-view-only",
        }
    return result


def _parse_override(pair: str) -> tuple[str, Any]:
    if "=" not in pair:
        raise ValueError(f"override must be key=value: {pair!r}")
    key, value = pair.split("=", 1)
    if key not in DEFAULTS:
        raise ValueError(f"unknown override {key!r}")
    return key, clean_scalar(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Target project root containing BMad configuration.")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Runtime override; repeat as needed.")
    parser.add_argument("-o", "--output", help="Write JSON to this path instead of stdout.")
    args = parser.parse_args()
    try:
        overrides = dict(_parse_override(pair) for pair in args.set)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    code, result = resolve_effective_config(Path(args.project_root), overrides)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(payload)
    return code


if __name__ == "__main__":
    sys.exit(main())
