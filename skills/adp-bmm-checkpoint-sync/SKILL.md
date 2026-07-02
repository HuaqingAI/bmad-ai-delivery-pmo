---
name: adp-bmm-checkpoint-sync
description: Syncs BMM checkpoints into ADP records. Use when the user says "adp-bmm-checkpoint-sync" or "sync BMM checkpoint".
---

# adp-bmm-checkpoint-sync

## Overview

This workflow syncs one BMM lifecycle checkpoint into an AI Delivery PMO Workstream Delivery Record. Act as a delivery-state facilitator: link the BMM artifact, extract only project-level coordination facts, expose gaps, and leave detailed requirements, architecture, stories, code, and validation content in the BMM source artifacts.

The consumers are the FDE owner, project lead, readiness reviewer, risk/dependency reviewer, and later ADP reports. They need the workstream's stage, artifact baseline, acceptance path, dependencies, evidence, risks, decisions, and next actions to be visible without asking the FDE to write a separate management report.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/sync_bmm_checkpoint.py`) resolve from this skill's installed directory.
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

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; if the target workstream record is missing, tell the user to run `adp-workstream-register` first. Continue only when a workstream id and checkpoint are known.

Infer the checkpoint from the user's artifact or wording when it is obvious: `prd`, `architecture`, `epic-story`, `implementation`, `validation`, or `baseline`. If several apply, ask which BMM stage they finished before writing.

## Sync

Get the smallest reliable checkpoint packet:

- workstream id
- checkpoint type
- BMM artifact path or link, with baseline status if known
- stage summary in project-level language
- acceptance, dependency, L0, risk, blocker, evidence, decision, and next-action updates only when the source actually supports them

Run the deterministic writer:

```bash
uv run scripts/sync_bmm_checkpoint.py {project-root} --workstream-id <workstream-id> --checkpoint <checkpoint> --summary "<project-level summary>"
```

Add optional flags only for reliable facts:

- `--artifact [key=]<path-or-url>`; repeat as needed. Common keys: `prd`, `architecture`, `epics`, `code`, `validation`.
- `--artifact-status draft|baseline|superseded|changed`
- `--scope "<management-level scope implication>"`
- `--acceptance "<criterion or acceptance update>"`; repeat as needed.
- `--evidence-required "<proof expectation>"`; repeat as needed.
- `--open-question "<question>"`; repeat as needed.
- `--dependency <workstream-id-or-description>`; `--impact <workstream-id-or-description>`; `--l0-reference "<constraint>"`; repeat as needed.
- `--risk "<risk>"`, `--blocker "<blocker>"`, `--milestone "<milestone>"`, `--next-action "<owner/action/trigger>"`; repeat as needed.
- `--business-confirmation "<confirmation state or owner>"`; repeat as needed.
- `--change-note "<baseline or scope change note>"`; repeat as needed.
- `--evidence "name|type|link|acceptance criterion|confirmation status|gap"`; repeat as needed.
- `--decision "type|decision or question|owner|impact|status|link"`; repeat as needed.
- `--readiness-gap "gap|dimension|owner|action|due or trigger|escalation"`; repeat as needed.
- `--record-status draft|gap|ready` only when the status is intentional.
- `--memory-root <path>` for non-default ADP memory.
- `--dry-run` to preview.

If the script cannot run, manually update the same files using `assets/checkpoint-templates/`. Preserve existing user content; append checkpoint facts and gap rows instead of rewriting the workstream from scratch.

## Checkpoint Modes

| Checkpoint | Sync outcome |
| --- | --- |
| `prd` | Link the PRD, capture scope implications, acceptance criteria, unresolved questions, dependencies, business confirmation state, and readiness gaps. |
| `architecture` | Link the architecture, capture technical dependencies, L0 contract or gate references, NFR impacts, risks, decisions, and next actions. |
| `epic-story` | Link epics/stories, capture delivery sequence, milestones, blockers, dependency shifts, and next actions. |
| `implementation` | Link code, PRs, deployment notes, implementation risks, blockers, and delivery evidence that changes project-level state. |
| `validation` | Link test results, demos, screenshots, validation notes, evidence rows, acceptance gaps, and business/customer confirmation state. |
| `baseline` | Mark a linked artifact as draft, baseline, superseded, or changed, and capture the change note or decision that explains why. |

## Output Contract

After syncing, report:

- the workstream folder path
- files updated or planned in dry-run
- artifact rows changed
- visible gaps added to readiness
- decisions, evidence rows, and daily log entries created
- next useful workflow, usually `adp-acceptance-readiness-review` for evidence gaps or `adp-risk-dependency-change-review` for risks, dependencies, changes, or business decisions

Do not call a checkpoint complete because an artifact link exists. Complete means the project-level implication is visible, missing facts are marked as gaps, and the next owner/action is clear.

## Guardrails

- BMM artifacts remain the source of truth; ADP records carry indexes, summaries, gaps, and coordination state.
- Do not paste full PRD, architecture, story, code, or validation content into the Workstream Delivery Record.
- Prefer `gap` over invented certainty when acceptance, evidence, business confirmation, L0 impact, or dependencies are unclear.
- Keep status sync separate: use `adp-status-sync` for lightweight recurring updates between BMM checkpoints.
- Route business decisions, risk acceptances, and scope changes to `adp-risk-dependency-change-review` when the FDE cannot decide alone.
