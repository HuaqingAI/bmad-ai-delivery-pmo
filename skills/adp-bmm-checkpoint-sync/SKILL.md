---
name: adp-bmm-checkpoint-sync
description: Discovers BMM checkpoints for ADP sync. Use when the user says "adp-bmm-checkpoint-sync" or "sync BMM checkpoint".
---

# adp-bmm-checkpoint-sync

## Overview

This workflow turns BMM and TEA artifacts into project-level checkpoint candidates, confirms their authority boundary, then syncs confirmed facts into an AI Delivery PMO Workstream Delivery Record. Act as a delivery-state facilitator: BMM/TEA artifacts stay the fact source; ADP records carry indexes, summaries, gaps, decisions, evidence, and coordination state.

The consumers are the FDE owner, project lead, readiness reviewer, risk/dependency reviewer, and later ADP reports. They need the workstream's stage, artifact baseline, acceptance path, dependencies, evidence, risks, decisions, and next actions to be visible without asking the FDE to write a separate management report.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/sync_bmm_checkpoint.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## Configuration and Language

Resolve the target `{project-root}` before any user-facing output. This is the project where ADP is installed or being run, not the module build repository.

The sync entrypoint resolves the shared ADP effective config. Use `communication_language` for conversation and status output and `document_output_language` for review output; `--language` is a one-run override. Surface resolver warnings and explicit English fallback. Language switching changes only the display layer: checkpoint facts, WDR field names, canonical statuses, candidate authority, and source lineage stay unchanged.

On Windows, set `PYTHONIOENCODING=utf-8` before running the sync script. For dry-runs, prefer `-o` when the caller needs a specific review path; otherwise the script writes a default dry-run report under ADP memory and prints only a short stdout summary.

## On Activation

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; if a sync target workstream record is missing, tell the user to run `adp-workstream-register` first. Continue only when a workstream id and checkpoint are known, or when the user supplied a `candidate_id` for confirm/sync.

Infer the checkpoint from the user's artifact or wording when it is obvious: `prd`, `architecture`, `epic-story`, `implementation`, `validation`, or `baseline`. If several apply, ask which BMM stage they finished before writing.

Route by intent:

| Intent | Outcome |
| --- | --- |
| `discover` | Generate or reuse a candidate from BMM/TEA artifacts. Does not modify WDR. |
| `confirm` | Attach authority scope, confirmation state, corrections, gaps, and next actions. |
| `sync` | Write a confirmed, unapplied candidate into ADP memory idempotently. |

Legacy direct packet sync remains available for callers that already have reliable checkpoint facts, but prefer discover -> confirm -> sync for new work.

## Candidate Lifecycle

For candidate storage, source priority, discovery, or confirmation, load `references/candidate-lifecycle.md`. It owns the candidate contract and stops discover at explicit confirmation; continue below only for a confirmed candidate or reliable compatibility packet.

## Sync

Sync only consumes `confirmed` candidates that are not yet `applied`:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" sync "{project-root}" --candidate-id CHK-...
```

The sync writes WDR artifact rows, project status, cross-workstream links, evidence, decisions, readiness gaps, and the checkpoint daily log using the existing writer. `Depends on` and `Impacts` receive only canonical workstream IDs; descriptive dependency facts remain in Project Status, descriptive impact facts remain in the checkpoint log, and `cross_workstream_link_audit` reports both channels. After that writer succeeds, it emits status-sync intake for ledger-ready `claims.actions`; the current physical Workstream update explicitly sets `refresh_actions: true`, while program or cross-scope action updates do not request a WDR projection. An already `applied` candidate returns `no_op=true` and does not rewrite WDR / evidence / decisions / readiness / daily or emit new intake.

Candidate sync dry-run writes a review report by default at `intake/bmm-checkpoints/dry-runs/{candidate-id}-sync-dry-run.json`. The JSON carries `report_path`, `report_exists`, `stdout_only: false`, `planned_files`, `can_apply`, `apply_blockers`, `recommended_next_step`, and `apply_command`.

Compatibility mode still accepts the old packet shape:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" "{project-root}" --workstream-id <workstream-id> --checkpoint <checkpoint> --summary "<project-level summary>"
```

Use compatibility mode only when the caller already supplied reliable facts. Run it with `--dry-run` first, review the planned files, generated gaps, record status, and action handoff audit, then execute the same packet only after explicit confirmation; automators should stay on candidate sync unless they already hold a confirmed packet. Add optional facts only when the source supports them; run `uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" --help` for the current flag surface.

Compatibility dry-run writes a review report by default at `intake/bmm-checkpoints/dry-runs/{date}-{workstream}-{checkpoint}-{hash}.json`; explicit `-o` overrides that path and must be reflected in `report_path`. If `record-status=ready` is blocked, treat `can_apply=false` and `apply_blockers` as the authoritative next-step signal.

`--action-file <path>` accepts the same action objects as `claims.actions`, either as a list or `{ "actions": [...] }`. Use it for `program` and cross-workstream actions with `affected_workstreams`. `--action "owner|action|due_or_trigger|closure_criteria"` is a local convenience scoped only to the current `--workstream-id`; it cannot express fanout. Do not treat free-form `--next-action` or `claims.next_actions` as ledger-ready, and do not convert ordinary `--readiness-gap` rows into actions because that row schema has no `closure_criteria`.

If `uv`, Python, or the sync script cannot run, load `assets/checkpoint-templates/checkpoint-packet.md`, `assets/checkpoint-templates/evidence-row.md`, `assets/checkpoint-templates/decision-row.md`, and `assets/checkpoint-templates/readiness-gap-row.md`. Capture only source-supported facts into a manual update packet and state that idempotent registry/write automation is unavailable until the script path works.

## Ready Guardrails

Never write `record-status=ready` automatically when any of these are true:

- the checkpoint affects another workstream and the required confirmer is missing
- acceptance owner or business confirmation is unknown
- evidence gaps are open
- TEA gate evidence is missing or not `PASS` / equivalent release approval
- validation is based only on owner prose rather than a real test run

Prefer `gap` over invented certainty. The script rejects `--record-status ready` when deterministic blockers are present; `status: final`, `Status: done`, or an artifact link alone never proves project-level readiness.

## Output Contract

After discover, report the candidate id, `candidate_path`, `preview_path`, status, superseded candidates, warnings, and the confirmation checklist. Verify paths exist before saying files were generated; dry-run report paths are separate from candidate review paths.

After confirm, report candidate id, status, whether it was a no-op, and the candidate path.

After sync, report the workstream folder path, files updated, `planned_files` for dry-run, artifact rows changed, visible gaps added to readiness, decisions/evidence rows created, daily log path, candidate status, `candidate_path`, `preview_path`, `dry_run_report_path`, `status_sync_intake_files`, `action_handoff_audit`, `cross_workstream_link_audit`, and the next useful workflow. Planned files are not generated files. Usually the next workflow is `adp-acceptance-readiness-review` for evidence gaps or `adp-risk-dependency-change-review` for risks, dependencies, changes, or business decisions.

When `status_sync_intake_files` is non-empty, surface both command shapes:

```bash
adp-status-sync update "{project-root}" --updates-file "<generated-intake-file>"
```

```bash
uv run "{status-sync-skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file "<generated-intake-file>"
```

`adp-status-sync update ...` is a runner alias. If the runner does not expose it, resolve the installed `adp-status-sync` skill root and use the direct script form.

Do not call a checkpoint complete because an artifact link exists. Complete means the project-level implication is visible, missing facts are marked as gaps, and the next owner/action is clear.

## Guardrails

- BMM artifacts remain the source of truth; ADP records carry indexes, summaries, gaps, and coordination state.
- Do not paste full PRD, architecture, story, code, or validation content into the Workstream Delivery Record.
- `discover` never modifies WDR; `confirm` never modifies WDR; only `sync` writes ADP memory.
- Keep status sync separate: use `adp-status-sync` for lightweight recurring updates between BMM checkpoints.
- Action ledger writes belong to `adp-status-sync`; checkpoint sync only creates status-sync intake.
- The checkpoint daily entry remains checkpoint-owned. The status-sync daily entry appears only when the generated action intake is consumed.
- Route business decisions, risk acceptances, and scope changes to `adp-risk-dependency-change-review` when the FDE cannot decide alone.
