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

## On Activation

Use `{project-root}/_bmad/memory/adp` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow status sync when the user provides `--memory-root`.

Read only the records needed for the requested sync. Do not scan every PRD, architecture, story, code branch, or validation artifact to answer a lightweight status question.

## Sync

Accept concise owner notes, batch updates, or outputs from `adp-meeting-sync`. If the input is natural language, first identify only the facts the user actually supplied: workstream id, current ADP status, progress, blockers, risks, dependency changes, scope/change notes, next actions, owner, due date, and source. Ask for the missing workstream id or owner only when it cannot be inferred safely.

Run the deterministic writer after the status delta is clear:

```bash
uv run scripts/sync_status.py update {project-root} --id <workstream-id>
```

Add only fields that are reliable:

- `--status "<status>"`
- `--phase "<bmm-phase>"`
- `--progress "<summary>"`
- `--blocker "<blocker>"`; repeat as needed
- `--risk "<risk>"`; repeat as needed
- `--dependency "<dependency change>"`
- `--change-note "<scope/change note>"`
- `--next-action "<owner/action/due>"`
- `--source "<owner update|meeting-sync|daily sync|other>"`
- `--memory-root <path>` for non-default ADP memory
- `--dry-run` to preview without writing

For multiple workstreams, prefer a JSON updates file and run:

```bash
uv run scripts/sync_status.py update {project-root} --updates-file <path>
```

The script updates `workstreams/{id}/delivery-record.md`, appends `daily/YYYY-MM-DD.md`, and returns JSON with changed fields, unresolved gaps, and action candidates.

## Staleness

To find records that need an owner follow-up, run:

```bash
uv run scripts/sync_status.py stale {project-root} --max-age-days 7
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
- action candidates grouped by owner when available
- unresolved questions that block a reliable update
- heavier ADP workflows that should run next, if any

Do not call a workstream ready because its status field was refreshed. Readiness requires the readiness workflow and evidence closure.

## Guardrails

- Update only volatile project-status fields unless the user explicitly asks for deeper review.
- BMM artifacts remain the source of truth; status sync stores links and short management-level deltas only.
- Preserve existing user content outside the targeted WDR fields and daily-log append.
- Make no-op explicit when a status note contains no reliable change.
