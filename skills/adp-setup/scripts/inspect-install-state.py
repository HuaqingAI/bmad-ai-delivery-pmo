#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Inspect deterministic ADP setup state before prompting or writing files.

Exit codes: 0=success, 1=validation error, 2=runtime error
"""

import argparse
import csv
import hashlib
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
    parser.add_argument(
        "--answers",
        help="Optional answers JSON to validate after overlaying inline or collected values.",
    )
    parser.add_argument(
        "--module-help",
        help="Source module-help.csv. Defaults beside module.yaml.",
    )
    parser.add_argument(
        "--installed-skills-dir",
        help="Installed module skill root. Defaults from project config and conventional locations.",
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


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except OSError as error:
        fail(f"Could not read {path}: {error}", 2)
    except json.JSONDecodeError as error:
        fail(f"Could not parse JSON {path}: {error}", 1)
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


def variable_value_error(key: str, value: Any, definition: dict[str, Any]) -> str | None:
    expected_type = definition.get("type")
    if expected_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return f"{key} must be an integer"
    if expected_type == "string" and not isinstance(value, str):
        return f"{key} must be a string"
    choices = definition.get("choices")
    if isinstance(choices, list) and value not in choices:
        return f"{key} must be one of: {', '.join(str(item) for item in choices)}"
    if isinstance(value, int) and not isinstance(value, bool):
        minimum = definition.get("minimum")
        maximum = definition.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            return f"{key} must be at least {minimum}"
        if isinstance(maximum, int) and value > maximum:
            return f"{key} must be at most {maximum}"
    return None


def choose_defaults(
    variables: dict[str, dict[str, Any]],
    existing_core: dict[str, Any],
    existing_module: dict[str, Any],
    legacy_core: dict[str, Any],
    legacy_module: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, str]],
    list[dict[str, str]],
    list[str],
]:
    defaults = {"core": {}, "module": {}}
    sources = {"core": {}, "module": {}}
    missing: list[dict[str, str]] = []
    warnings: list[str] = []

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
        selected = False
        for source, candidate_values in (
            ("existing", existing_module),
            ("legacy", legacy_module),
        ):
            if key not in candidate_values:
                continue
            value = candidate_values[key]
            error = variable_value_error(key, value, definition)
            if error:
                warnings.append(f"Ignored invalid {source} value: {error}")
                continue
            defaults["module"][key] = value
            sources["module"][key] = source
            selected = True
            break
        if selected:
            continue
        if "default" in definition:
            value = definition["default"]
            error = variable_value_error(key, value, definition)
            if error:
                fail(f"Invalid module default: {error}", 1)
            defaults["module"][key] = value
            sources["module"][key] = "module_default"
        else:
            missing.append(
                {
                    "scope": "module",
                    "key": key,
                    "prompt": str(definition.get("prompt", key)),
                }
            )

    return defaults, sources, missing, warnings


def overlay_answers(
    defaults: dict[str, dict[str, Any]], answers: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    overlaid = {
        "core": dict(defaults.get("core", {})),
        "module": dict(defaults.get("module", {})),
    }
    for scope in ("core", "module"):
        values = answers.get(scope, {})
        if isinstance(values, dict):
            overlaid[scope].update(values)
    return overlaid


def answer_validation_errors(
    answers: dict[str, Any], variables: dict[str, dict[str, Any]]
) -> list[str]:
    allowed = {"core": CORE_KEYS, "module": frozenset(variables)}
    errors: list[str] = []
    for scope, values in answers.items():
        if scope not in allowed:
            errors.append(f"Unknown answer scope: {scope}")
            continue
        if not isinstance(values, dict):
            errors.append(f"Answer scope '{scope}' must be an object")
            continue
        unknown_keys = sorted(set(values) - allowed[scope])
        if unknown_keys:
            errors.append(f"Unknown {scope} answer keys: {', '.join(unknown_keys)}")
        if scope == "module":
            for key, value in values.items():
                if key in variables:
                    error = variable_value_error(key, value, variables[key])
                    if error:
                        errors.append(error)
    return errors


def remaining_missing_inputs(
    missing: list[dict[str, str]], answers: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    remaining = []
    for item in missing:
        value = answers.get(item["scope"], {}).get(item["key"])
        if value is None or value == "":
            remaining.append(item)
    return remaining


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


def resolve_project_token_path(project_root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(PROJECT_ROOT_TOKEN + "/"):
        return Path(resolve_project_path(project_root, value))
    return Path(value).expanduser().resolve()


def installed_skills_dir(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    configured = resolve_project_token_path(project_root, config.get("bmad_builder_output_folder"))
    candidates = []
    if configured:
        candidates.append(("config", configured))
    candidates.extend(
        [
            ("default", project_root / "skills"),
            ("agents", project_root / ".agents" / "skills"),
            ("claude", project_root / ".claude" / "skills"),
        ]
    )

    for source, path in candidates:
        if path.is_dir():
            return {"path": str(path.resolve()), "source": source}

    source, path = candidates[0]
    return {"path": str(path.resolve()), "source": f"{source}_default"}


def expected_skill_order(module_help_path: Path) -> list[str]:
    if not module_help_path.is_file():
        return []
    try:
        with module_help_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            ordered: list[str] = []
            for row in rows:
                skill = str(row.get("skill") or "").strip()
                if skill and skill not in ordered:
                    ordered.append(skill)
            return ordered
    except OSError as error:
        fail(f"Could not read {module_help_path}: {error}", 2)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_resource_contract(
    resource_path: Path,
    item: dict[str, Any],
) -> tuple[str, dict[str, Any], list[str], str | None]:
    if not resource_path.is_file():
        return "missing", {}, [], None

    findings: list[str] = []
    actual_sha256 = sha256_file(resource_path)
    expected_sha256 = str(item.get("sha256") or "").removeprefix("sha256:")
    if expected_sha256 and actual_sha256 != expected_sha256:
        findings.append(
            f"checksum mismatch: expected sha256:{expected_sha256}, got sha256:{actual_sha256}"
        )

    expected_contract = item.get("contract")
    actual_contract: dict[str, Any] = {}
    if isinstance(expected_contract, dict):
        try:
            with resource_path.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            findings.append(f"contract JSON unreadable: {error}")
        else:
            if not isinstance(payload, dict):
                findings.append("contract JSON must be an object")
            else:
                actual_contract = {
                    key: payload.get(key)
                    for key in expected_contract
                }
                for key, expected in expected_contract.items():
                    actual = payload.get(key)
                    if actual != expected:
                        findings.append(
                            f"contract field {key!r} mismatch: expected {expected!r}, got {actual!r}"
                        )

    if findings:
        integrity = "invalid"
    elif expected_sha256 or isinstance(expected_contract, dict):
        integrity = "verified"
    else:
        integrity = "present"
    return integrity, actual_contract, findings, actual_sha256


def inspect_installed_components(
    skills_root: Path,
    expected_skills: list[str],
    shared_resources: Any,
    module_help_path: Path,
) -> dict[str, Any]:
    skill_rows = []
    for skill in expected_skills:
        skill_path = skills_root / skill
        skill_md = skill_path / "SKILL.md"
        skill_rows.append(
            {
                "skill": skill,
                "path": str(skill_path.resolve()),
                "installed": skill_path.is_dir() and skill_md.is_file(),
            }
        )

    resource_rows = []
    if isinstance(shared_resources, list):
        for item in shared_resources:
            if not isinstance(item, dict):
                continue
            owner = str(item.get("owner_skill") or "").strip()
            relative = str(item.get("path") or "").strip()
            resource_path = skills_root / owner / Path(relative)
            integrity, contract, findings, actual_sha256 = inspect_resource_contract(
                resource_path, item
            )
            resource_rows.append(
                {
                    "owner_skill": owner,
                    "path": relative,
                    "purpose": str(item.get("purpose") or ""),
                    "resolved_path": str(resource_path.resolve()),
                    "installed": bool(owner and relative and resource_path.is_file()),
                    "integrity": integrity,
                    "contract": contract,
                    "expected_sha256": item.get("sha256"),
                    "actual_sha256": actual_sha256,
                    "findings": findings,
                }
            )

    missing_skills = [item["skill"] for item in skill_rows if not item["installed"]]
    missing_resources = [
        f"{item['owner_skill']}/{item['path']}"
        for item in resource_rows
        if not item["installed"]
    ]
    invalid_resources = [
        f"{item['owner_skill']}/{item['path']}"
        for item in resource_rows
        if item["integrity"] == "invalid"
    ]
    help_available = module_help_path.is_file()
    return {
        "ready": help_available and not missing_skills and not missing_resources and not invalid_resources,
        "module_help_path": str(module_help_path.resolve()),
        "module_help_available": help_available,
        "expected_skill_order": expected_skills,
        "skills": skill_rows,
        "missing_skills": missing_skills,
        "shared_resources": resource_rows,
        "missing_shared_resources": missing_resources,
        "invalid_shared_resources": invalid_resources,
    }


def version_tuple(value: Any) -> tuple[int, int, int] | None:
    parts = str(value or "").split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def memory_upgrade_report(project_root: Path) -> dict[str, Any]:
    memory_root = project_root / "_bmad-output" / "adp" / "memory"
    required_paths = [
        "plans",
        "plans/baseline-history",
        "schemas/program-baseline.md",
        "schemas/program-status.md",
        "snapshots/program-status",
        "snapshots/flow-graph",
        "snapshots/management-panel",
        "views/management-panel",
        "views/program-status.json",
        "views/program-status.md",
        "intake/program-baseline-candidate.json",
    ]
    if not memory_root.is_dir():
        return {
            "root": str(memory_root.resolve()),
            "status": "not_initialized",
            "missing_paths": required_paths,
            "recommended_workflow": "adp-project-kickoff",
            "preserved_paths": [],
        }
    missing = [item for item in required_paths if not (memory_root / item).exists()]
    preserved = [str(memory_root.resolve())]
    baseline = memory_root / "plans" / "program-baseline.md"
    snapshots = memory_root / "snapshots" / "program-status"
    if baseline.exists():
        preserved.append(str(baseline.resolve()))
    if snapshots.exists():
        preserved.append(str(snapshots.resolve()))
    return {
        "root": str(memory_root.resolve()),
        "status": "migration_required" if missing else "current",
        "missing_paths": missing,
        "recommended_workflow": "adp-project-kickoff" if missing else None,
        "preserved_paths": preserved,
    }


def build_upgrade_report(
    config: dict[str, Any],
    module_code: str,
    target_version: Any,
    state: str,
    sources: dict[str, dict[str, str]],
    installation: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    module_section = config.get(module_code, {})
    installed_version = module_section.get("version") if isinstance(module_section, dict) else None
    installed_tuple = version_tuple(installed_version)
    target_tuple = version_tuple(target_version)
    if installed_version is None:
        version_status = "fresh_install"
    elif installed_tuple is None or target_tuple is None:
        version_status = "unknown"
    elif installed_tuple < target_tuple:
        version_status = "upgrade"
    elif installed_tuple > target_tuple:
        version_status = "newer_installed"
    else:
        version_status = "current"

    defaulted_variables = sorted(
        key
        for key, source in sources.get("module", {}).items()
        if source == "module_default"
    )
    return {
        "install_state": state,
        "installed_version": installed_version,
        "target_version": target_version,
        "version_status": version_status,
        "defaulted_module_variables": defaulted_variables,
        "installed_components": installation,
        "memory": memory_upgrade_report(project_root),
        "project_state_policy": "config/help/resources only; existing ADP memory and baseline are never deleted or overwritten",
    }


def main() -> None:
    args = parse_args()
    reject_unresolved_paths(
        [
            ("project_root", args.project_root),
            ("--config-path", args.config_path),
            ("--user-config-path", args.user_config_path),
            ("--legacy-dir", args.legacy_dir),
            ("--answers", args.answers),
            ("--module-help", args.module_help),
            ("--installed-skills-dir", args.installed_skills_dir),
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

    module_yaml_path = Path(args.module_yaml).resolve()
    module_yaml = load_yaml_file(module_yaml_path, required=True)
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
    defaults, sources, missing, config_warnings = choose_defaults(
        variables, current_core, current_module, legacy_core, legacy_module
    )
    provided_answers = load_json_file(Path(args.answers)) if args.answers else {}
    validation_errors = answer_validation_errors(provided_answers, variables)
    if validation_errors:
        fail("; ".join(validation_errors), 1)
    validated_answers = overlay_answers(defaults, provided_answers)
    remaining_missing = remaining_missing_inputs(missing, validated_answers)
    metadata = extract_module_metadata(module_yaml)
    state = install_state(config, str(module_code), legacy_files)
    module_help_path = (
        Path(args.module_help).resolve()
        if args.module_help
        else module_yaml_path.parent / "module-help.csv"
    )
    if args.installed_skills_dir:
        skills_dir = {
            "path": str(Path(args.installed_skills_dir).resolve()),
            "source": "explicit",
        }
    else:
        skills_dir = installed_skills_dir(project_root, config)
    installation = inspect_installed_components(
        Path(skills_dir["path"]),
        expected_skill_order(module_help_path),
        module_yaml.get("shared_resources", []),
        module_help_path,
    )
    gaps = [
        f"Missing required {item['scope']} value: {item['key']}"
        for item in remaining_missing
    ]
    if not installation["module_help_available"]:
        gaps.append(f"Module help source is missing: {installation['module_help_path']}")
    if installation["missing_skills"]:
        gaps.append("Missing installed skills: " + ", ".join(installation["missing_skills"]))
    if installation["missing_shared_resources"]:
        gaps.append(
            "Missing shared resources: "
            + ", ".join(installation["missing_shared_resources"])
        )
    if installation["invalid_shared_resources"]:
        invalid_details = []
        for item in installation["shared_resources"]:
            if item["integrity"] == "invalid":
                invalid_details.append(
                    f"{item['owner_skill']}/{item['path']}: {'; '.join(item['findings'])}"
                )
        gaps.append("Invalid shared resources: " + " | ".join(invalid_details))
    upgrade_report = build_upgrade_report(
        config,
        str(module_code),
        metadata.get("version"),
        state,
        sources,
        installation,
        project_root,
    )

    result = {
        "status": "success",
        "project_root": str(project_root),
        "module": metadata,
        "install_state": state,
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
        "config_warnings": config_warnings,
        "answers_template": defaults,
        "provided_answers": provided_answers,
        "validated_answers": validated_answers,
        "pre_overlay_missing_required_inputs": missing,
        "missing_required_inputs": remaining_missing,
        "headless_ready": not remaining_missing and installation["ready"],
        "installation_ready": installation["ready"],
        "unresolved_gaps": gaps,
        "directories_to_create": directories_to_create(
            project_root, str(module_code), metadata, validated_answers, variables
        ),
        "installed_skills_dir": skills_dir["path"],
        "installed_skills_dir_source": skills_dir["source"],
        "installed_skill_inspection": installation,
        "upgrade_report": upgrade_report,
    }
    if args.verbose:
        print(
            f"Install state: {result['install_state']}; headless_ready={result['headless_ready']}",
            file=sys.stderr,
        )
    emit(result, args.output)


if __name__ == "__main__":
    main()
