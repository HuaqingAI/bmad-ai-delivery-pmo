#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Inspect deterministic ADP setup state before prompting or writing files.

Exit codes: 0=success, 1=validation error, 2=runtime error
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print(
        json.dumps(
            {"status": "error", "error": "pyyaml is required (PEP 723 dependency)"},
            indent=2,
        )
    )
    sys.exit(2)


PROJECT_ROOT_TOKEN = "{project-root}"
CORE_KEYS = frozenset(
    {"user_name", "communication_language", "document_output_language", "output_folder"}
)
CORE_USER_KEYS = frozenset({"user_name", "communication_language"})
CORE_DEFAULTS = {
    "user_name": "BMad",
    "communication_language": "English",
    "document_output_language": "English",
    "output_folder": "{project-root}/_bmad-output",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect module install state, effective defaults, and headless readiness."
    )
    parser.add_argument("project_root", help="Project root path")
    parser.add_argument(
        "--module-yaml",
        required=True,
        help="Path to this skill's assets/module.yaml",
    )
    parser.add_argument(
        "--config-path",
        help="Target _bmad/config.yaml path. Defaults under project root.",
    )
    parser.add_argument(
        "--user-config-path",
        help="Target _bmad/config.user.yaml path. Defaults under project root.",
    )
    parser.add_argument(
        "--legacy-dir",
        help="Directory containing legacy module/core config. Defaults to project _bmad.",
    )
    parser.add_argument("-o", "--output", help="Write JSON output to this file")
    parser.add_argument("--verbose", action="store_true", help="Print diagnostics to stderr")
    return parser.parse_args()


def emit(payload: dict[str, Any], output_path: str | None = None) -> None:
    text = json.dumps(payload, indent=2)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def fail(message: str, exit_code: int = 1) -> None:
    emit({"status": "error", "error": message})
    sys.exit(exit_code)


def reject_unresolved_paths(named_paths: list[tuple[str, str | None]]) -> None:
    for name, value in named_paths:
        if value and PROJECT_ROOT_TOKEN in value:
            fail(
                f"Unresolved '{PROJECT_ROOT_TOKEN}' token in {name} path: {value!r}. "
                f"Resolve '{PROJECT_ROOT_TOKEN}' before running this script."
            )


def load_yaml_file(path: Path, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            fail(f"Required YAML file not found: {path}", 1)
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = yaml.safe_load(handle)
    except OSError as error:
        fail(f"Could not read {path}: {error}", 2)
    except yaml.YAMLError as error:
        fail(f"Could not parse YAML {path}: {error}", 1)
    return data if isinstance(data, dict) else {}


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    core_section = normalized.pop("core", None)
    if isinstance(core_section, dict):
        normalized.update(core_section)
    return normalized


def variable_defs(module_yaml: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: value
        for key, value in module_yaml.items()
        if isinstance(value, dict) and "prompt" in value
    }


def extract_module_metadata(module_yaml: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": module_yaml.get("code"),
        "name": module_yaml.get("name"),
        "description": module_yaml.get("description"),
        "version": module_yaml.get("module_version"),
        "default_selected": module_yaml.get("default_selected"),
        "module_greeting": module_yaml.get("module_greeting"),
        "agents": module_yaml.get("agents", []),
    }


def load_legacy_values(
    legacy_dir: Path, module_code: str, variables: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    legacy_core: dict[str, Any] = {}
    legacy_module: dict[str, Any] = {}
    files_found: list[str] = []

    core_path = legacy_dir / "core" / "config.yaml"
    if core_path.exists():
        files_found.append(str(core_path))
        for key, value in load_yaml_file(core_path).items():
            if key in CORE_KEYS:
                legacy_core[key] = value

    module_path = legacy_dir / module_code / "config.yaml"
    if module_path.exists():
        files_found.append(str(module_path))
        for key, value in load_yaml_file(module_path).items():
            if key in CORE_KEYS and key not in legacy_core:
                legacy_core[key] = value
            elif key in variables:
                legacy_module[key] = value

    return legacy_core, legacy_module, files_found


def existing_values(
    config: dict[str, Any],
    user_config: dict[str, Any],
    module_code: str,
    variables: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    core: dict[str, Any] = {}
    module: dict[str, Any] = {}
    module_section = config.get(module_code, {})
    if not isinstance(module_section, dict):
        module_section = {}

    for key in CORE_KEYS:
        if key in CORE_USER_KEYS and key in user_config:
            core[key] = user_config[key]
        elif key in config:
            core[key] = config[key]

    for key, definition in variables.items():
        if definition.get("user_setting") is True and key in user_config:
            module[key] = user_config[key]
        elif key in module_section:
            module[key] = module_section[key]

    return core, module


def choose_defaults(
    variables: dict[str, dict[str, Any]],
    existing_core: dict[str, Any],
    existing_module: dict[str, Any],
    legacy_core: dict[str, Any],
    legacy_module: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]], list[dict[str, str]]]:
    defaults = {"core": {}, "module": {}}
    sources = {"core": {}, "module": {}}
    missing: list[dict[str, str]] = []

    for key, default in CORE_DEFAULTS.items():
        if key in existing_core:
            defaults["core"][key] = existing_core[key]
            sources["core"][key] = "existing"
        elif key in legacy_core:
            defaults["core"][key] = legacy_core[key]
            sources["core"][key] = "legacy"
        else:
            defaults["core"][key] = default
            sources["core"][key] = "module_default"

    for key, definition in variables.items():
        if key in existing_module:
            defaults["module"][key] = existing_module[key]
            sources["module"][key] = "existing"
        elif key in legacy_module:
            defaults["module"][key] = legacy_module[key]
            sources["module"][key] = "legacy"
        elif "default" in definition:
            defaults["module"][key] = definition["default"]
            sources["module"][key] = "module_default"
        else:
            missing.append(
                {
                    "scope": "module",
                    "key": key,
                    "prompt": str(definition.get("prompt", key)),
                }
            )

    return defaults, sources, missing


def apply_result_templates(
    variables: dict[str, dict[str, Any]], module_defaults: dict[str, Any]
) -> dict[str, Any]:
    transformed: dict[str, Any] = {}
    for key, value in module_defaults.items():
        definition = variables.get(key, {})
        if "result" in definition and PROJECT_ROOT_TOKEN not in str(value):
            transformed[key] = str(definition["result"]).replace("{value}", str(value))
        else:
            transformed[key] = value
    return transformed


def collect_project_root_paths(value: Any) -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for item in value.values():
            paths.extend(collect_project_root_paths(item))
        return paths
    if isinstance(value, list):
        paths = []
        for item in value:
            paths.extend(collect_project_root_paths(item))
        return paths
    if isinstance(value, str) and value.startswith(PROJECT_ROOT_TOKEN + "/"):
        return [value]
    return []


def resolve_project_path(project_root: Path, config_value: str) -> str:
    relative = config_value[len(PROJECT_ROOT_TOKEN) :].lstrip("/\\")
    return str((project_root / relative).resolve())


def directories_to_create(
    project_root: Path,
    module_code: str,
    metadata: dict[str, Any],
    defaults: dict[str, dict[str, Any]],
    variables: dict[str, dict[str, Any]],
) -> list[str]:
    config_like: dict[str, Any] = {
        key: value
        for key, value in defaults["core"].items()
        if key not in CORE_USER_KEYS
    }
    module_section = {
        "name": metadata.get("name"),
        "description": metadata.get("description"),
        "version": metadata.get("version"),
        **apply_result_templates(variables, defaults["module"]),
    }
    config_like[module_code] = module_section

    resolved: list[str] = []
    seen: set[str] = set()
    for config_value in collect_project_root_paths(config_like):
        path = resolve_project_path(project_root, config_value)
        if path not in seen:
            seen.add(path)
            resolved.append(path)
    return resolved


def install_state(config: dict[str, Any], module_code: str, legacy_files: list[str]) -> str:
    has_module = isinstance(config.get(module_code), dict)
    has_legacy = bool(legacy_files)
    if has_module and has_legacy:
        return "legacy_migration"
    if has_module:
        return "update"
    if has_legacy:
        return "fresh_install_with_legacy"
    return "fresh_install"


def main() -> None:
    args = parse_args()
    reject_unresolved_paths(
        [
            ("project_root", args.project_root),
            ("--config-path", args.config_path),
            ("--user-config-path", args.user_config_path),
            ("--legacy-dir", args.legacy_dir),
        ]
    )

    project_root = Path(args.project_root).resolve()
    config_path = Path(args.config_path).resolve() if args.config_path else project_root / "_bmad" / "config.yaml"
    user_config_path = (
        Path(args.user_config_path).resolve()
        if args.user_config_path
        else project_root / "_bmad" / "config.user.yaml"
    )
    legacy_dir = Path(args.legacy_dir).resolve() if args.legacy_dir else project_root / "_bmad"

    module_yaml = load_yaml_file(Path(args.module_yaml), required=True)
    module_code = module_yaml.get("code")
    if not module_code:
        fail("module.yaml must contain a 'code' field", 1)

    variables = variable_defs(module_yaml)
    config = normalize_config(load_yaml_file(config_path))
    user_config = load_yaml_file(user_config_path)
    legacy_core, legacy_module, legacy_files = load_legacy_values(
        legacy_dir, str(module_code), variables
    )
    current_core, current_module = existing_values(
        config, user_config, str(module_code), variables
    )
    defaults, sources, missing = choose_defaults(
        variables, current_core, current_module, legacy_core, legacy_module
    )
    metadata = extract_module_metadata(module_yaml)
    gaps = [
        f"Missing required {item['scope']} value: {item['key']}"
        for item in missing
    ]

    result = {
        "status": "success",
        "project_root": str(project_root),
        "module": metadata,
        "install_state": install_state(config, str(module_code), legacy_files),
        "config_paths": {
            "config_path": str(config_path),
            "user_config_path": str(user_config_path),
            "help_path": str(project_root / "_bmad" / "module-help.csv"),
        },
        "existing_values": {"core": current_core, "module": current_module},
        "legacy_values": {
            "core": legacy_core,
            "module": legacy_module,
            "files_found": legacy_files,
        },
        "effective_defaults": defaults,
        "default_sources": sources,
        "answers_template": defaults,
        "missing_required_inputs": missing,
        "headless_ready": not missing,
        "unresolved_gaps": gaps,
        "directories_to_create": directories_to_create(
            project_root, str(module_code), metadata, defaults, variables
        ),
    }
    if args.verbose:
        print(
            f"Install state: {result['install_state']}; headless_ready={result['headless_ready']}",
            file=sys.stderr,
        )
    emit(result, args.output)


if __name__ == "__main__":
    main()
