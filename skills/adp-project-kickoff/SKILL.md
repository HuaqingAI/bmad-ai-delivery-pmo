---
name: adp-project-kickoff
description: Bootstraps shared ADP project memory. Use when the user says "adp-project-kickoff" or "start ADP project".
---

## Overview

This workflow initializes shared ADP memory, including plan and snapshot directories, schemas, baseline intake, L0 placeholders, decision logs, audits, meeting-pack folders, flow-graph and management-panel directories, and starter views. Act as a delivery setup facilitator: preserve existing work and leave a usable state surface without a long questionnaire.

The consumer is the FDE team, project lead, and later ADP workflows. They need files that make project state consistent across workstreams without replacing BMM lifecycle artifacts. BMM outputs remain the source of truth; Workstream Delivery Records are the project-level synchronization surface.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/bootstrap_adp.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## Configuration and Language

Resolve the target `{project-root}` before any user-facing output. This is the project where ADP is installed or being run, not the module build repository.

Load BMad configuration from the target project in this order:

1. `{project-root}/_bmad/adp/config.yaml` (primary ADP install-time config)
2. `{project-root}/_bmad/config.user.yaml` and `{project-root}/_bmad/config.yaml` when present
3. `{project-root}/_bmad/core/config.yaml`
4. `{project-root}/_bmad/bmm/config.yaml` or `{project-root}/_bmad/bmb/config.yaml` as compatibility fallbacks

Use `communication_language` for all conversation and status output. Use `document_output_language` for generated project documents and report text. If no config file exists, say that explicitly and fall back to English.

## On Activation

Infer the project root from the current workspace unless the user gives a path. If `{project-root}/_bmad-output/adp/memory/` already exists, treat the run as an idempotent refresh: preserve content, report what already exists, and create only missing recommended files.

If legacy ADP memory exists at `{project-root}/_bmad/memory/adp/` and the new default root does not intentionally use it, do not silently create a second empty state tree. Tell the user to migrate the legacy folder to `{project-root}/_bmad-output/adp/memory/` or rerun with `--memory-root {project-root}/_bmad/memory/adp` to keep using the legacy location.

Before writing anything, run the deterministic discovery prepass:

```bash
uv run "{skill-root}/scripts/bootstrap_adp.py" "{project-root}" --dry-run
```

Judge and summarize the returned JSON; do not independently search raw folders for planning or implementation artifacts. If existing BMad artifacts are found and the user did not pass `--headless`, `--yes`, or an already-confirmed workstream plan, summarize the discovered context and ask for confirmation before initializing or refreshing ADP memory. Explain that kickoff will create an ADP coordination layer from the existing project state and will not overwrite or duplicate the BMM source artifacts. If the user declines, stop without writing files.

When existing PRDs are found, treat each PRD as a candidate FDE workstream, not as generic project background. Show the candidate line list with suggested workstream id, name, and PRD path, then ask which lines should be included in ADP memory and whether any candidates should be renamed, merged, split, or excluded. Do not create or update Workstream Delivery Records during kickoff; `adp-workstream-register` owns workstream files.

For confirmed PRD lines, quickly inspect each selected PRD for project-level coordination facts only: scope summary, acceptance path, business owner or confirmer, visible dependencies, L0 references, risks, open questions, and next checkpoint. Keep detailed requirements inside the PRD. Present the extracted facts as a short confirmation table; when facts are weak, mark them `TBD` or gap rather than guessing. After the user confirms, pass those lines to the scaffold script with `--workstream-plan <json-file>` so kickoff persists `intake/workstream-registration-plan.json` and `.md` for `adp-workstream-register` to consume.

Headless callers get the script JSON directly. If PRDs are discovered without `--workstream-plan`, return the blocked JSON with `candidate_workstreams` and `next_required_input`; do not enter confirmation prose.

Accept a brief when the user provides one, but do not block setup for missing details. Combine the brief with discovered artifact paths as kickoff source context. Capture only reliable facts into `project-charter.md`; leave unknown objectives, stakeholders, cadence exceptions, L0 references, and escalation paths as explicit placeholders.

Kickoff never creates `plans/program-baseline.md`; that file is owned by `adp-plan-baseline`. When it is absent, report `baseline_onboarding.status: gap`, point to `intake/program-baseline-candidate.json`, and route the user to confirm source-backed targets, gates, milestones, dependencies, and critical-path facts. A missing baseline is not evidence of delay.

## Build

Run the scaffold script:

```bash
uv run "{skill-root}/scripts/bootstrap_adp.py" "{project-root}"
```

Use optional flags only when the user gives the facts:

- `--project-name "<name>"` for the charter/index heading.
- `--profile generic-delivery|migration-cutover` for the project profile.
- `--cadence weekly|biweekly|custom` for the default rhythm.
- `--timezone "<IANA timezone or project label>"` for date-window calculations.
- `--fde-days "Monday,Wednesday,Friday"` for a confirmed recurring FDE weekday schedule; the default is Monday/Wednesday/Friday.
- `--fde-cadence-override "<source-backed note>"` only for a confirmed long-term departure from those weekdays.
- `--memory-root <path>` when ADP memory should live outside `{project-root}/_bmad-output/adp/memory`.
- `--source "<brief or path summary>"` to record the kickoff source.
- `--workstream-plan <json-file>` after the user confirms which discovered PRD lines should be written to the registration intake plan.
- `--yes` or `--headless` only when the user explicitly wants non-interactive kickoff after discovery.
- `--dry-run` to preview without writing.

The workstream plan is confirmed input, never an auto-detected guess. Start from `assets/workstream-plan.example.json` and retain source-backed facts or explicit `TBD` values.

If the script cannot run, create the same folders and files from `assets/adp-memory-templates/` manually. Never overwrite an existing file during fallback; report it as existing instead.

## Output Contract

In headless mode, return the script JSON directly: `status: complete` on success, or `status: blocked` with `candidate_workstreams` and `next_required_input` when PRDs require a workstream plan.

After setup, report:

- the ADP memory root
- created files and already-existing files
- the workstream registration plan path, when confirmed PRD lines were supplied
- baseline onboarding status and its `adp-plan-baseline` recovery action
- project timezone, recurring FDE weekdays, and any long-term cadence override
- any script errors or skipped writes
- the next useful action, usually running `adp-workstream-register` for each confirmed line in the plan

Do not claim the project is ready just because the scaffold exists. The kickoff is complete when the memory structure exists and the user can see which project facts still need to be filled in.

## Files Created

The scaffold copies `assets/adp-memory-templates/` into ADP memory while preserving existing files; use script JSON for exact paths. `actions/action-ledger.md` remains the durable action source; `views/fde-actions.md`, `views/meeting-packs/*`, `views/program-status.*`, and `views/roadmap.*` are derived. It creates empty `snapshots/flow-graph/`, `snapshots/management-panel/`, and `views/management-panel/` directories but no placeholder `flow-graph.json`, panel bundle, or `index.html`; their owner workflows publish only after audited canonical inputs are ready. A confirmed PRD plan additionally creates the two `intake/workstream-registration-plan.*` files without overwriting either.

## Guardrails

- Preserve existing user content. Existing files are inputs, not defects.
- Keep ADP as a coordination layer over BMM; do not duplicate PRD, architecture, story, code, or validation details into kickoff templates.
- Keep kickoff out of workstream file ownership. It may create registration intake, but `adp-workstream-register` creates or normalizes Workstream Delivery Records and starter workstream files.
- Make gaps visible rather than filling them with invented defaults.
- Preserve baseline ownership: kickoff scaffolds intake and history only; `adp-plan-baseline` alone writes or versions the approved baseline.
- Preserve snapshot ownership: kickoff creates the snapshot directory and guidance only; `adp-program-status` alone writes immutable status snapshots.
- Preserve flow and panel ownership: kickoff only creates their directories; `adp-flow-graph` and `adp-management-panel` alone publish graph, bundle, HTML, and archive artifacts.
- Upgrade non-destructively: a v1.2 tree gains only missing v1.3 directories and templates; existing baselines, current views, immutable snapshots, panel HTML, manifests, and receipts remain byte-for-byte unchanged.
- For migration or cutover projects, initialize the same structure and mark the profile; readiness and L0 workflows own detailed cutover judgment later.
