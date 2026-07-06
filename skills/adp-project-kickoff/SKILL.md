---
name: adp-project-kickoff
description: Bootstraps shared ADP project memory. Use when the user says "adp-project-kickoff" or "start ADP project".
---

## Overview

This workflow initializes an AI Delivery PMO project by creating the shared ADP memory structure, starter schemas, L0 reference placeholders, decision logs, daily logs, and report views. Act as a delivery setup facilitator: keep startup cheap, preserve existing work, and leave the user with a usable project state surface rather than a long questionnaire.

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

## Build

Run the scaffold script:

```bash
uv run "{skill-root}/scripts/bootstrap_adp.py" "{project-root}"
```

Use optional flags only when the user gives the facts:

- `--project-name "<name>"` for the charter/index heading.
- `--profile generic-delivery|migration-cutover` for the project profile.
- `--cadence weekly|biweekly|custom` for the default rhythm.
- `--memory-root <path>` when ADP memory should live outside `{project-root}/_bmad-output/adp/memory`.
- `--source "<brief or path summary>"` to record the kickoff source.
- `--workstream-plan <json-file>` after the user confirms which discovered PRD lines should be written to the registration intake plan.
- `--yes` or `--headless` only when the user explicitly wants non-interactive kickoff after discovery.
- `--dry-run` to preview without writing.

The workstream plan JSON is a confirmed input, not an auto-detected guess. Use this shape:

```json
{
  "workstreams": [
    {
      "id": "l1-checkout",
      "name": "Checkout Migration",
      "fde_owner": "TBD",
      "business_owner": "TBD",
      "phase": "PRD",
      "status": "draft",
      "scope": "Project-level summary or TBD",
      "acceptance": "Acceptance summary or TBD",
      "prd_path": "{project-root}/_bmad-output/planning-artifacts/checkout-prd.md",
      "dependencies": [],
      "impacts": [],
      "l0_references": [],
      "risks": [],
      "open_questions": [],
      "next_actions": ["Confirm PRD-derived baseline with the FDE owner."]
    }
  ]
}
```

If the script cannot run, create the same folders and files from `assets/adp-memory-templates/` manually. Never overwrite an existing file during fallback; report it as existing instead.

## Output Contract

In headless mode, return the script JSON directly: `status: complete` on success, or `status: blocked` with `candidate_workstreams` and `next_required_input` when PRDs require a workstream plan.

After setup, report:

- the ADP memory root
- created files and already-existing files
- the workstream registration plan path, when confirmed PRD lines were supplied
- any script errors or skipped writes
- the next useful action, usually running `adp-workstream-register` for each confirmed line in the plan

Do not claim the project is ready just because the scaffold exists. The kickoff is complete when the memory structure exists and the user can see which project facts still need to be filled in.

## Files Created

The scaffold writes the files and folders represented by `assets/adp-memory-templates/` into the ADP memory root, preserving existing files. Use the script JSON for exact created/existing paths. `actions/action-ledger.md` remains the durable action source of truth; `views/fde-actions.md` is derived. When a confirmed PRD workstream plan is supplied, the script also writes `intake/workstream-registration-plan.json` and `intake/workstream-registration-plan.md` without overwriting existing files.

## Guardrails

- Preserve existing user content. Existing files are inputs, not defects.
- Keep ADP as a coordination layer over BMM; do not duplicate PRD, architecture, story, code, or validation details into kickoff templates.
- Keep kickoff out of workstream file ownership. It may create registration intake, but `adp-workstream-register` creates or normalizes Workstream Delivery Records and starter workstream files.
- Make gaps visible rather than filling them with invented defaults.
- For migration or cutover projects, initialize the same structure and mark the profile; readiness and L0 workflows own detailed cutover judgment later.
