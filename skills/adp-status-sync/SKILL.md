---
name: adp-status-sync
description: Refreshes ADP workstream status. Use when the user says "adp-status-sync" or "sync workstream status".
---

# adp-status-sync

## Overview

This workflow keeps AI Delivery PMO workstreams current between BMM checkpoints by applying small, reliable status deltas to Workstream Delivery Records and daily logs. Act as a low-friction delivery-state operator: accept owner updates, meeting-sync outputs, or short status notes, separate volatile status from deeper review work, and leave the project lead and Program Lead agent with a current coordination surface.

The consumer is the FDE owner, project lead, and later ADP reports. They need to see what changed, which workstreams are stale, who owns the next action, and which issues need a heavier workflow.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/sync_status.py`) resolve from this skill's installed directory.
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

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow status sync when the user provides `--memory-root`.

Read only the records needed for the requested sync. Do not scan every PRD, architecture, story, code branch, or validation artifact to answer a lightweight status question.

## Sync

Accept concise owner notes, batch updates, or outputs from `adp-meeting-sync`. If the input is natural language, first identify only the facts the user actually supplied: workstream id, current ADP status, progress, blockers, risks, dependency changes, scope/change notes, next actions, milestone id/status/forecast/actual/evidence, owner, due date, and source. Ask for a missing stable milestone or workstream id when it cannot be inferred safely.

Run the deterministic writer after the status delta is clear:

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --id <workstream-id>
```

If `uv` or Python execution is unavailable, manually edit only the targeted WDR volatile fields and append `daily/YYYY-MM-DD.md`, preserving all other content.

Add only fields that are reliable:

- `--status "<status>"`
- `--phase "<bmm-phase>"`
- `--progress "<summary>"`
- `--blocker "<blocker>"`; repeat as needed
- `--risk "<risk>"`; repeat as needed
- `--dependency "<dependency change>"`
- `--change-note "<scope/change note>"`
- `--next-action "<owner/action/due>"`
- `--milestone-id <baseline-milestone-id>` with `--milestone-status <planned|in-progress|at-risk|done|blocked>`
- `--milestone-forecast YYYY-MM-DD` and/or `--milestone-actual YYYY-MM-DD`
- `--milestone-evidence "<traceable source>"`; repeat as needed
- `--baseline-revision <expected-revision>` to reject stale updates
- `--source "<owner update|meeting-sync|daily sync|other>"`
- `--memory-root <path>` for non-default ADP memory
- `--dry-run` to preview without writing

For multiple workstreams or workflow-produced action intake, prefer a JSON updates file and run:

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file <path>
```

The updates file may include baseline-mapped `milestones` and structured `actions` alongside legacy `next_actions`:

```json
{
  "updates": [
    {
      "id": "l1-checkout",
      "next_actions": ["FDE-A add checkout validation evidence"],
      "milestones": [
        {
          "milestone_id": "MS-CHECKOUT-COMPLETE",
          "status": "at-risk",
          "forecast": "2026-10-20",
          "evidence": ["workstreams/l1-checkout/evidence.md#forecast-20261020"]
        }
      ],
      "actions": [
        {
          "owner": "FDE-A",
          "workstream": "l1-checkout",
          "affected_workstreams": ["l1-checkout"],
          "action": "Add checkout validation evidence",
          "source": "meetings/sync-notes-20260701.md#M-001",
          "reason": "Meeting action",
          "due": "Friday",
          "status": "open",
          "closure_criteria": "Evidence is linked in evidence.md",
          "owning_workflow": "adp-meeting-sync"
        }
      ]
    }
  ]
}
```

For milestone updates, the script reads `plans/program-baseline.md` and validates the current revision, exact case-sensitive milestone ID, and owning workstream before any write. Unknown milestones never become implicit plan entries. It writes forecast, actual, status, evidence, and baseline lineage to the targeted WDR `Roadmap` row; planned date, name, owner, and dependencies continue to come from the baseline. Every milestone update requires traceable evidence. The baseline itself is never modified.

For one Source + Action that affects many workstreams, send one canonical action with `workstream: "program"` and `affected_workstreams`; do not repeat the same action under every workstream unless owner, due trigger, or deliverable differs. `program` actions update the ledger and daily log without requiring a `workstreams/program/delivery-record.md`.

The script updates `workstreams/{id}/delivery-record.md`, appends `daily/YYYY-MM-DD.md`, upserts `actions/action-ledger.md`, and returns JSON with changed fields, milestone IDs, baseline revision/path, action results, unresolved gaps, and action candidates. Any milestone mapping failure blocks the whole command before WDR, daily-log, or action-ledger writes. Legacy `next_actions` remain supported; structured `actions` are the durable source for the FDE action list.

## Versioned Action Flow Relations

`references/action-flow-relation-contract-v1.md` and `assets/action-flow-relation-v1.schema.json` own stable action identity, timestamps, explicit related plan-item/flow-edge IDs, half-open processed windows, and unmapped migration behavior for canonical graph overlays. Structured actions may supply `created_at`, `started_at`, `done_at`, `cancelled_at`, `baseline_revision`, `related_plan_item_ids`, and `related_flow_edge_ids`. The writer preserves those fields in the ledger and publishes `views/action-flow.json`; terminal actions cannot silently reopen. Legacy rows remain readable but are omitted from the canonical relation file until migrated, never inferred.

## Staleness

To find records that need an owner follow-up, run:

```bash
uv run "{skill-root}/scripts/sync_status.py" stale "{project-root}" --max-age-days 7
```

Treat missing `Last status sync` as stale unless the user is still registering the workstream. Staleness creates follow-up candidates; it does not prove delivery risk by itself.

## Escalation

Stay out of deeper workflows unless the update exposes their trigger:

- BMM artifact changed, new PRD/architecture/epic/story/validation evidence exists -> route to `adp-bmm-checkpoint-sync`.
- Meeting notes need item-by-item closure -> route to `adp-meeting-sync`.
- Risk acceptance, scope change, unresolved cross-line dependency, or business decision is needed -> route to `adp-risk-dependency-change-review`.
- Evidence coverage, acceptance confirmation, readiness scoring, cutover readiness, or go/no-go judgment is needed -> route to `adp-acceptance-readiness-review`.
- L0 gates, NFRs, evidence rules, or contracts changed -> route to `adp-l0-reference-sync`.

## Output Contract

After a sync, report:

- workstreams updated or found stale
- fields changed and fields intentionally left untouched
- milestones updated with the baseline revision and evidence lineage
- action ledger path and actions registered, updated, or closed
- action candidates grouped by owner when available
- unresolved questions that block a reliable update
- heavier ADP workflows that should run next, if any

Do not call a workstream ready because its status field was refreshed. Readiness requires the readiness workflow and evidence closure.

## Guardrails

- Update only volatile project-status fields unless the user explicitly asks for deeper review.
- `adp-plan-baseline` is the only baseline writer. Status sync records actual-state facts and never changes planned facts.
- BMM artifacts remain the source of truth; status sync stores links and short management-level deltas only.
- `actions/action-ledger.md` is the ADP action source of truth. `views/fde-actions.md` is a derived view, and WDR `Next actions` is a merged active-action summary.
- Preserve existing user content outside the targeted WDR fields and daily-log append.
- Make no-op explicit when a status note contains no reliable change.
