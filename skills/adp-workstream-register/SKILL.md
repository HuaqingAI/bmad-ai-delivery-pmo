---
name: adp-workstream-register
description: Registers ADP workstream records. Use when the user says "adp-workstream-register" or "register workstream".
---

# adp-workstream-register

## Overview

This workflow creates or normalizes one FDE workstream in AI Delivery PMO by producing a lightweight Workstream Delivery Record and starter evidence, decision, and readiness files. Act as a delivery-state facilitator: capture enough project-level state for coordination, expose gaps plainly, and keep BMM artifacts as the source of truth for delivery detail.

The consumer is the FDE owner, project lead, readiness reviewer, and later ADP workflows. They need a consistent workstream surface that answers what the line is, where its BMM artifacts live, what it affects, what blocks it, and what must happen next.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/register_workstream.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.

## Configuration and Language

Resolve the target `{project-root}` before any user-facing output. This is the project where ADP is installed or being run, not the module build repository.

Load BMad configuration from the target project in this order:

1. `{project-root}/_bmad/adp/config.yaml` (primary ADP install-time config)
2. `{project-root}/_bmad/config.user.yaml` and `{project-root}/_bmad/config.yaml` when present
3. `{project-root}/_bmad/core/config.yaml`
4. `{project-root}/_bmad/bmm/config.yaml` or `{project-root}/_bmad/bmb/config.yaml` as compatibility fallbacks

Use `communication_language` for all conversation and status output. Use `document_output_language` for generated project documents and report text. If no config file exists, say that explicitly and fall back to English.

## On Activation

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing or lacks kickoff core files, tell the user to run `adp-project-kickoff`; allow partial creation only when the user explicitly accepts an incomplete ADP memory substrate.

Infer create vs update from whether `workstreams/{workstream-id}/delivery-record.md` exists. Existing files are user state: preserve them, write a reviewable `registration-patch-plan*.md` with supplied normalization facts, and report which starter files already exist.

## Register

Get the minimum facts needed for a useful draft:

- workstream id and name
- FDE owner
- business owner, if known
- current BMM phase
- short scope summary
- known BMM artifact links
- dependencies, impacted workstreams, or L0 references, if known

Run the deterministic writer:

```bash
uv run scripts/register_workstream.py {project-root} --id <workstream-id> --name "<name>" --owner "<fde-owner>"
```

Add optional flags only for reliable facts:

- `--business-owner "<name>"`
- `--phase "<bmm-phase>"`
- `--status draft|gap|ready`
- `--scope "<summary>"`
- `--artifact prd=<path-or-url>`; repeat for `architecture`, `epics`, `code`, `validation`, or another artifact key
- `--depends-on <workstream-id>`; repeat as needed
- `--impacts <workstream-id>`; repeat as needed
- `--l0-reference <reference>`; repeat as needed
- `--memory-root <path>` for non-default ADP memory
- `--allow-partial-memory` only when the user intentionally wants to write without kickoff-created core files
- `--dry-run` to preview

If the script cannot run, create `delivery-record.md`, `evidence.md`, `decisions.md`, and `readiness.md` from `assets/workstream-templates/` manually. Preserve existing files.

## Normalize Existing Lines

When the user brings an existing workstream, do not rewrite BMM artifacts or overwrite the WDR. Index supplied paths and project-level facts into a patch plan the user can apply after review, and mark missing owners, scope boundaries, acceptance criteria, dependencies, evidence, or confirmations as gaps.

Use `gap` status when a missing fact blocks coordination, readiness, or stakeholder judgment. Use `draft` when the missing fact is expected and does not block current coordination.

## Output Contract

After registration, report:

- the workstream folder path
- files created and files preserved
- patch plan path when updating an existing workstream
- visible gaps the FDE should fill next
- artifact links captured
- next useful workflow, usually `adp-bmm-checkpoint-sync` when a BMM stage has an artifact to sync

Do not call a workstream ready because files exist. Ready requires project-level state, acceptance path, evidence expectations, and blocking decisions to be clear or owned.

## Guardrails

- The Workstream Delivery Record is a synchronization surface, not a replacement PRD or architecture.
- Keep scope and acceptance at management level; link to BMM for detail.
- Make cross-line dependencies and L0 references explicit even when incomplete.
- Keep readiness as score plus gaps, not just color status.
