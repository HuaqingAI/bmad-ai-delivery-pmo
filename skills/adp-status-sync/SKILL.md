---
name: adp-status-sync
description: Refreshes AI Delivery PMO workstream status. Use when the user says "adp-status-sync" or "sync workstream status".
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

## On Activation

Resolve the target project, language, and ADP memory state before user-facing output:

```bash
uv run "{skill-root}/scripts/sync_status.py" context "{project-root}"
```

Consume its resolved language values, memory path/existence, config source, and diagnostics. If the memory root is missing, route to `adp-project-kickoff`; an explicit `--memory-root` may select an existing non-default root.

Read only the records needed for the requested sync. Do not scan every PRD, architecture, story, code branch, or validation artifact to answer a lightweight status question.

## Sync

Accept concise owner notes, batch updates, or outputs from `adp-meeting-sync`. Map only explicitly supplied facts to the writer arguments below; ask for a stable workstream or milestone ID when it cannot be inferred safely. For structured actions, judge whether the owner is accountable and closure criteria are verifiable, and pass any semantic deficiencies as explicit `unresolved_gaps`; the writer checks only structural absence or `TBD`.

Run the deterministic writer after the status delta is clear:

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --id <workstream-id>
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
- `--milestone-id <baseline-milestone-id>` with `--milestone-status <planned|in-progress|at-risk|done|blocked>`
- `--milestone-forecast YYYY-MM-DD` and/or `--milestone-actual YYYY-MM-DD`
- `--milestone-evidence "<traceable source>"`; repeat as needed
- `--baseline-revision <expected-revision>` to reject stale updates
- `--source "<owner update|meeting-sync|daily sync|other>"`
- `--memory-root <path>` for non-default ADP memory
- `--dry-run` to preview without writing

For multiple workstreams, workflow-produced actions, updates-file execution, or receipt migration, load `references/batch-status-updates.md`; it owns the payload contract, preview acceptance, atomic apply, durable receipts, and historical migration.

If `uv` or Python is unavailable, manual fallback is valid only for one named workstream's volatile WDR fields plus one daily-log append. Batch files, milestones, structured actions, receipt-required intake, and any atomic multi-file update are blocked; preserve their input unchanged for retry.

For milestone updates, the script reads `plans/program-baseline.md` and validates the current revision, exact case-sensitive milestone ID, and owning workstream before any write. Unknown milestones never become implicit plan entries. It writes forecast, actual, status, evidence, and baseline lineage to the targeted WDR `Roadmap` row; planned date, name, owner, and dependencies continue to come from the baseline. Every milestone update requires traceable evidence. The baseline itself is never modified.

The writer preflights every target, stages coupled files atomically, and returns changed fields, milestone lineage, action IDs, and unresolved gaps. Any milestone mapping failure blocks the command before publication.

## Versioned Action Flow Relations

When handling structured action-relation fields, canonical graph overlays, or legacy migration, load `references/action-flow-relation-contract-v1.md` and `assets/action-flow-relation-v1.schema.json`. Missing stable identity, timestamps, or explicit relation IDs remains unmapped and is never inferred.

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

Return a result the FDE owner, project lead, and later ADP reports can use directly: applied changes or an explicit no-op/stale result; milestone/action evidence and baseline lineage; a durable receipt when applicable; and gaps that blocked updates with the heavier workflow they require. A status refresh is not readiness and must never be reported as such.

## Headless

With `--headless`, require `{project-root}` plus either one unambiguous workstream ID and delta or an updates file. Never infer missing facts or ask questions. Return `{"status":"blocked","reason":"<one line>"}` when requirements are incomplete; otherwise return `{"status":"complete","result":<writer JSON>}`. Mutating an interpreted batch requires explicit apply authorization; without it, return the dry-run result as blocked pending acceptance.

## Guardrails

- Update only volatile project-status fields unless the user explicitly asks for deeper review.
- `adp-plan-baseline` is the only baseline writer. Status sync records actual-state facts and never changes planned facts.
- BMM artifacts remain the source of truth; status sync stores links and short management-level deltas only.
- `actions/action-ledger.md` is the ADP action source of truth. `views/fde-actions.md` is a derived view, and WDR `Next actions` is a merged active-action summary.
- Preserve existing user content outside the targeted WDR fields and daily-log append.
- Never treat a dry-run, wrapper attestation, nested receipt binding, or unbound historical report as proof that an intake was applied.
- Make no-op explicit when a status note contains no reliable change.
