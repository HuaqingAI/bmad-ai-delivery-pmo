---
name: adp-setup
description: Sets up AI Delivery PMO module in a project. Use when the user requests to 'install adp module', 'configure AI Delivery PMO', or 'setup AI Delivery PMO'.
---

# Module Setup

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `assets/module.yaml` or `scripts/merge-config.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.

## Overview

Installs and configures a BMad module into a project. Module identity, prompts, defaults, and greeting come from `assets/module.yaml`. Setup writes:

- `{project-root}/_bmad/config.yaml` - shared project config: root core settings plus an `adp` section. User-only keys (`user_name`, `communication_language`) are never written here.
- `{project-root}/_bmad/config.user.yaml` - personal settings intended to be gitignored: `user_name`, `communication_language`, and module variables marked `user_setting: true`.
- `{project-root}/_bmad/module-help.csv` - module capabilities for the help system.

The literal `{project-root}` token stays in config values. Filesystem path arguments (`--*-path`, `--*-dir`, `--target`, and the project root positional argument) must use resolved real paths; scripts reject unresolved `{project-root}` in those arguments.

## On Activation

Resolve the actual project root, then inspect install state:

```bash
uv run "{skill-root}/scripts/inspect-install-state.py" "{project-root}" --module-yaml "{skill-root}/assets/module.yaml"
```

Use the JSON as the source of truth for module metadata, `install_state`, `effective_defaults`, `default_sources`, `answers_template`, `missing_required_inputs`, `headless_ready`, and `directories_to_create`. If `status` is not `success`, surface the error and stop. If arguments provide values (for example `accept all defaults`, `--headless`, or `user name is BMad, I speak Swahili`), overlay them on `answers_template` and skip interactive prompting.

## Headless Contract

`--headless`/`-H` is non-interactive. It may write without confirmation when the actual project root is known, `inspect-install-state.py` succeeds, and no `missing_required_inputs` remain after applying inline values. Optional inputs are inline core/module values and an explicit project root. If a required value or filesystem path cannot be resolved, do not write.

In headless mode, stdout is one JSON object and no prose:

```json
{
  "status": "complete|blocked|error",
  "install_state": "fresh_install|fresh_install_with_legacy|update|legacy_migration",
  "config_path": "<resolved project root>/_bmad/config.yaml",
  "user_config_path": "<resolved project root>/_bmad/config.user.yaml",
  "help_path": "<resolved project root>/_bmad/module-help.csv",
  "help_rows_added": 0,
  "legacy_configs_deleted": [],
  "legacy_csvs_deleted": [],
  "directories_to_create": [],
  "directories_created": [],
  "directories_existing": [],
  "legacy_directories_removed": [],
  "legacy_files_removed_count": 0,
  "unresolved_gaps": [],
  "error": null
}
```

Use `status: "blocked"` with `unresolved_gaps` when input is missing. Use `status: "error"` with `error` when a script fails. Interactive mode reports the same script results as a human summary.

## Collect Configuration

Use `effective_defaults`, `default_sources`, and `missing_required_inputs` from the inspect JSON. Ask once for missing values or overrides, showing computed defaults in brackets; never tell the user to "press enter" or "leave blank" in chat.

## Write Files

Write a temp JSON file from `answers_template` overlaid with collected values. Values inside this JSON keep the literal `{project-root}` token.

In the commands below, replace `{project-root}` in every path argument with the actual project root before running; these are filesystem paths, not config values.

```bash
uv run "{skill-root}/scripts/merge-config.py" --config-path "{project-root}/_bmad/config.yaml" --user-config-path "{project-root}/_bmad/config.user.yaml" --module-yaml "{skill-root}/assets/module.yaml" --answers {temp-file} --legacy-dir "{project-root}/_bmad" --create-output-dirs
uv run "{skill-root}/scripts/merge-help-csv.py" --target "{project-root}/_bmad/module-help.csv" --source "{skill-root}/assets/module-help.csv" --legacy-dir "{project-root}/_bmad" --module-code adp
```

Both merge scripts output JSON to stdout. If either exits non-zero, surface the error and stop. Check `legacy_configs_deleted`, `legacy_csvs_deleted`, `directories_to_create`, and `directories_created` in the output. Execute skill-owned scripts with `uv run "{skill-root}/scripts/..."`; do not rely on dot-prefixed script paths, because shell commands usually run from `{project-root}`.

Run `uv run "{skill-root}/scripts/inspect-install-state.py" --help`, `uv run "{skill-root}/scripts/merge-config.py" --help`, or `uv run "{skill-root}/scripts/merge-help-csv.py" --help` for full usage.

## Cleanup Legacy Directories

After both merge scripts complete successfully, remove the installer's package directories. Skills and agents in these directories are already installed at `{project-root}/.claude/skills/`; `{project-root}/_bmad/` should only contain config files.

```bash
uv run "{skill-root}/scripts/cleanup-legacy.py" --bmad-dir "{project-root}/_bmad" --module-code adp --also-remove _config --skills-dir "{project-root}/.claude/skills"
```

The script verifies that every skill in the legacy directories exists at `.claude/skills/` before removing anything. Missing directories are not errors. If the script exits non-zero, surface the error and stop.

Run `uv run "{skill-root}/scripts/cleanup-legacy.py" --help` for full usage.

## Confirm

Use the inspect, merge, help, and cleanup JSON to report install state, config paths, user keys written, help rows added, output directories created or already present, legacy files deleted, and legacy package cleanup counts. Then display `module_greeting` from the inspect JSON.

## Outcome

Once `user_name` and `communication_language` are known from collected input, arguments, or existing config, use them for the remainder of the session.
