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

Resolve BMad language configuration with:

```bash
uv run "{skill-root}/scripts/resolve_bmad_config.py" "{project-root}"
```

Use the JSON `communication_language` for conversation and status output, `document_output_language` for generated project documents and report text, and surface any resolver warnings.

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

## Candidate Intake

Discover writes candidates under:

```text
_bmad-output/adp/memory/intake/bmm-checkpoints/
  index.jsonl
  candidates/
    {candidate-id}.json
    {candidate-id}.preview.md
```

Candidate status is one of `discovered`, `confirmed`, `applied`, `superseded`, or `dismissed`.

The stable id is derived from `workstream_id`, `checkpoint`, `source_scope_key`, `source_revision`, and normalized discovered claims. The hard rule: repeated discovery of the same source revision and claims returns the existing candidate; it never creates a second accepted event. If the same `source_scope_key` has a new `source_revision`, the old active candidate is marked `superseded` and a new `discovered` candidate is written.

Every candidate carries deterministic `source_prepass` facts: frontmatter, sections, tables, JSON fields, source paths, and line refs. Do not treat those parsed facts as decisions, risks, readiness gaps, or project implications until confirm classifies them.

Every candidate carries `authority.asserted_by`, `authority.authority_scope`, `authority.affected_workstreams`, `authority.required_confirmers`, and `authority.confirmation_state`. A single workstream owner can confirm only inside their authority scope; cross-line impact stays `cross-line-pending` until the required confirmer is recorded.

Candidate action handoff uses `claims.actions`, a structured JSON array whose rows carry `owner`, `workstream`, optional `affected_workstreams`, `action`, `source`, `reason`, `due_or_trigger`, `status`, `closure_criteria`, and `owning_workflow`. `claims.next_actions` remains a free-form WDR/daily summary input only; it must not create action-ledger intake.

## Discover

Run discover when the user gives a BMM/TEA artifact or asks to sync a checkpoint without already supplying a candidate:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" discover "{project-root}" --workstream-id <workstream-id> --checkpoint <checkpoint> --artifact <key=path-or-url> --summary "<project-level summary>"
```

Use optional authority flags when known: `--asserted-by`, `--authority-scope`, `--affected-workstream`, and `--required-confirmer`. Use `--dry-run` to preview.

Discover returns a confirmation checklist with `confirmation_required`, `selected_artifacts`, `ignored_artifacts`, authority scope, review paths, and confirm/dismiss commands. After discover, show that checklist and stop for explicit scope confirmation; do not sync a discovered candidate. Headless callers must treat `confirmation_required: true` as the next-state signal. If several artifacts are supplied, only the first existing artifact is bound unless the script explicitly supports multi-source; surface `ignored_artifacts` and use `packet-sync` for multi-source baseline packets.

Fact source priority within a checkpoint:

| Checkpoint | Prefer |
| --- | --- |
| `prd` | `SPEC.md + .memlog.md`, then `prd.md + .memlog.md`, `brief.md + .memlog.md`, `prfaq-*.md + distillate` |
| `architecture` | `ARCHITECTURE-SPINE.md + .memlog.md`, then reviewer outputs |
| `epic-story` | `epics.md`, story files, `sprint-status.yaml`, readiness reports |
| `implementation` | story/spec file, review findings, `deferred-work.md`, `sprint-status.yaml`, test summaries |
| `validation` | `gate-decision.json`, `e2e-trace-summary.json`, trace matrix, NFR/test review, CI artifacts |

Prefer machine-readable outputs over prose, `.memlog.md` over raw prose where available, stable document sections over loose notes, and owner supplementation last.

## Confirm

Show the preview and confirm only the project-level facts the owner can actually assert: scope, impacts, gaps, required confirmers, business confirmation, and next actions. Use `source_prepass` to classify parsed facts; do not convert single-line understanding into cross-line confirmed project truth.

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" confirm "{project-root}" --candidate-id CHK-... --decision confirm --confirmed-by "<owner>" --override authority.confirmation_state=confirmed-local
```

Use `--override path=value` for candidate corrections. Repeating the same confirmation is a no-op; different overrides append a confirmation event instead of silently replacing history. Use `--decision dismiss` for a candidate that should not be synced.

Add or replace candidate actions with whole-field overrides, not list-index patches. Prefer `--overrides-file` for action payloads, using a JSON object such as `{"claims.actions":[...]}`.

## Sync

Sync only consumes `confirmed` candidates that are not yet `applied`:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" sync "{project-root}" --candidate-id CHK-...
```

The sync writes WDR artifact rows, project status, cross-workstream links, evidence, decisions, readiness gaps, and the checkpoint daily log using the existing writer. After that writer succeeds, it emits status-sync intake for ledger-ready `claims.actions`; an already `applied` candidate returns `no_op=true` and does not rewrite WDR / evidence / decisions / readiness / daily or emit new intake.

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

After sync, report the workstream folder path, files updated, `planned_files` for dry-run, artifact rows changed, visible gaps added to readiness, decisions/evidence rows created, daily log path, candidate status, `candidate_path`, `preview_path`, `dry_run_report_path`, `status_sync_intake_files`, `action_handoff_audit`, and the next useful workflow. Planned files are not generated files. Usually the next workflow is `adp-acceptance-readiness-review` for evidence gaps or `adp-risk-dependency-change-review` for risks, dependencies, changes, or business decisions.

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
