---
name: adp-setup
description: Sets up AI Delivery PMO module in a project. Use when the user requests to 'install adp module', 'configure AI Delivery PMO', or 'setup AI Delivery PMO'.
---

# Module Setup

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `assets/module.yaml` or `scripts/merge-config.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.
- Prefer `uv run` for skill scripts. If `uv` is unavailable, run the same script and arguments with Python 3.10+; `inspect-install-state.py` and `merge-config.py` additionally require an importable PyYAML 6.x and must block with that dependency gap when it is absent.

## Overview

Installs and configures a BMad module into a project. Module identity, prompts, defaults, and greeting come from `assets/module.yaml`. Setup writes:

- `{project-root}/_bmad/config.yaml` - shared project config: root core settings plus an `adp` section. User-only keys (`user_name`, `communication_language`) are never written here.
- `{project-root}/_bmad/config.user.yaml` - personal settings intended to be gitignored: `user_name`, `communication_language`, and module variables marked `user_setting: true`.
- `{project-root}/_bmad/module-help.csv` - module capabilities for the help system.

It never writes or deletes ADP project memory, approved baselines, or status snapshots. Existing memory upgrade needs are reported and routed to `adp-project-kickoff`.

The literal `{project-root}` token stays in config values. Filesystem path arguments (`--*-path`, `--*-dir`, `--target`, and the project root positional argument) must use resolved real paths; scripts reject unresolved `{project-root}` in those arguments.

## On Activation

Resolve the actual project root, then inspect install state:

```bash
uv run "{skill-root}/scripts/inspect-install-state.py" "{project-root}" --module-yaml "{skill-root}/assets/module.yaml" --module-help "{skill-root}/assets/module-help.csv" --installed-skills-dir "{skill-root}/.."
```

Use the JSON as the source of truth for module metadata, `install_state`, effective values and sources, installed-skill/shared-resource inspection, `upgrade_report`, `headless_ready`, output directories, and the installed skills root. Missing or invalid skills, schemas, templates, locale catalogs, panel runtime assets, or ELK version/license/checksum make the installation unready; route that gap to module reinstallation before writing or legacy cleanup. Memory migration notices do not authorize setup to change memory. If arguments provide values (for example `accept all defaults`, `--headless`, or inline core/module values), overlay them on `answers_template` and skip those prompts.

## Headless Contract

`--headless`/`-H` is non-interactive and previews by default. Installation state (config, help, output directories, and legacy cleanup) may change only with `--apply`, a known actual project root, and `inspect-install-state.py --answers {temp-file}` returning `headless_ready: true`. Optional inputs are inline core/module values and an explicit project root. If a required value or filesystem path cannot be resolved, do not write installation state.

The setup-run memlog is the only preview side effect. Initialize `{project-root}/_bmad-output/adp/setup-runs/<timestamp>/.memlog.md` through `{project-root}/_bmad/scripts/memlog.py` before resolving unattended choices. Append each applied default as an `assumption`, each explicit apply or conflict resolution as a `decision`, and the terminal outcome as an `event`, in occurrence order. If the actual project root is unresolved or the memlog cannot be created, return blocked without changing installation state. Return the memlog path on both complete and blocked results once it exists.

In headless mode, stdout is one JSON object and no prose:

```json
{
  "status": "complete|blocked",
  "reason": null,
  "memlog": "<resolved project root>/_bmad-output/adp/setup-runs/<timestamp>/.memlog.md",
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
  "installed_skills_dir": "<resolved installed skills directory>",
  "installation_ready": true,
  "installed_skill_inspection": {},
  "upgrade_report": {},
  "legacy_directories_removed": [],
  "legacy_files_removed_count": 0,
  "unresolved_gaps": [],
  "error": null
}
```

Use `status: "blocked"` with a one-line `reason` and `unresolved_gaps` when input or installed resources are missing or a script fails; retain the script failure in `error`. Interactive mode reports the same script results as a human summary.

## Collect Configuration

Use `effective_defaults`, `default_sources`, `config_warnings`, and `missing_required_inputs` from the inspect JSON. Ask once for missing values or overrides, showing computed defaults in brackets; never tell the user to "press enter" or "leave blank" in chat. The seven ADP settings are the existing cadence/staleness/variance/meeting limit values plus `management_panel_history_periods` (1-52, default 12), `management_panel_default_view` (`project-lead|fde-morning|business-biweekly`), and `management_panel_archive_mode` (`explicit|meeting-only|always`). A one-off archive still requires the runtime distribution profile `internal-full|shareable-summary`; setup never turns that safety boundary into a durable default.

## Write Files

Write a temp JSON file containing only collected overrides; values inside it keep the literal `{project-root}` token. Let `inspect-install-state.py` overlay those overrides on `answers_template` and validate the result:

```bash
uv run "{skill-root}/scripts/inspect-install-state.py" "{project-root}" --module-yaml "{skill-root}/assets/module.yaml" --module-help "{skill-root}/assets/module-help.csv" --installed-skills-dir "{skill-root}/.." --answers {temp-file} --validated-answers-output {validated-answers-file}
```

If the validation JSON has `headless_ready: false`, do not write; surface `missing_required_inputs` and `unresolved_gaps`. When `headless_ready` is true, `validated_answers_output` names a distinct file containing the returned `validated_answers` object unchanged.

Before the first mutation in interactive mode, show one concrete plan from the inspection: config/help files to create or update, output directories to create, legacy config/help files to delete during merge, and legacy package directories to remove during cleanup. Require explicit confirmation for that exact plan. `--headless` without `--apply` returns that plan with `status: "blocked"` and a one-line authorization reason; `--headless --apply` proceeds without a prompt only when `headless_ready: true`.

In the commands below, replace `{project-root}` in every path argument with the actual project root before running; these are filesystem paths, not config values.

```bash
uv run "{skill-root}/scripts/merge-config.py" --config-path "{project-root}/_bmad/config.yaml" --user-config-path "{project-root}/_bmad/config.user.yaml" --module-yaml "{skill-root}/assets/module.yaml" --answers {validated-answers-file} --legacy-dir "{project-root}/_bmad" --create-output-dirs
uv run "{skill-root}/scripts/merge-help-csv.py" --target "{project-root}/_bmad/module-help.csv" --source "{skill-root}/assets/module-help.csv" --legacy-dir "{project-root}/_bmad" --module-code adp
```

Both merge scripts output JSON to stdout. If either exits non-zero, surface the error and stop. Check `legacy_configs_deleted`, `legacy_csvs_deleted`, `directories_to_create`, and `directories_created` in the output. Execute skill-owned scripts with `uv run "{skill-root}/scripts/..."`; do not rely on dot-prefixed script paths, because shell commands usually run from `{project-root}`.

## Cleanup Legacy Directories

After both merge scripts complete successfully, remove the installer's package directories. Skills and agents in these directories are already installed at `installed_skills_dir` from the inspect JSON; `{project-root}/_bmad/` should only contain config files.

```bash
uv run "{skill-root}/scripts/cleanup-legacy.py" --bmad-dir "{project-root}/_bmad" --module-code adp --also-remove _config --skills-dir "{installed_skills_dir}"
```

The script verifies that every skill in the legacy directories exists at the inspected installed skills directory before removing anything. Missing directories are not errors. If the script exits non-zero, surface the error and stop.

## Confirm

Run the install-state inspection again after merge and cleanup. Use the final inspection plus merge/help/cleanup JSON to report module version, config value sources and fallbacks, installed skills and shared resources, ELK version/license/checksum disposition, config paths, help rows replaced, output directories, legacy cleanup, and memory migration needs. State explicitly that the reported preserved memory/baseline/panel paths were untouched, then display `module_greeting`. A v1.2 memory tree missing the v1.3 flow/panel directories remains a non-destructive kickoff migration need, not permission for setup to create a panel.

## Outcome

Once `user_name` and `communication_language` are known from collected input, arguments, or existing config, use them for the remainder of the session.
