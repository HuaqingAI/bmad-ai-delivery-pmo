# Cadence

Default cadence: {{DEFAULT_CADENCE}}
Project timezone: {{PROJECT_TIMEZONE}}

## FDE Morning Meeting

- Recurring weekdays: {{FDE_MEETING_DAYS}}
- Long-term override: {{FDE_CADENCE_OVERRIDE}}
- Incremental windows advance only after a successful `adp-meeting-sync` archive, not when a meeting pack is generated.
- First runs, non-recurring weekdays, missed expected meetings, holidays, and temporary reschedules require explicit period confirmation.

## Status Sync

- Default rhythm: {{DEFAULT_CADENCE}}
- Inputs: updated Workstream Delivery Records, meeting notes, decision log, risks, dependencies, evidence gaps
- Output: refreshed project lead view, FDE action list, and daily log entry

## Weekly Report

- Source files: `workstreams/*/delivery-record.md`, `daily/*`, `decisions/decision-log.md`, `views/*`
- Output file: `views/weekly-report.md`
- Required content: status summary, blocked workstreams, risk/dependency changes, decisions needed, readiness gaps, next actions

## State Audit

- Source files: WDRs, action ledger, decisions, daily logs, L0 summaries, and derived views.
- Output folder: `audits/`
- Required before: meeting packs, weekly report, project lead readout, and roadmap rendering.

## Meeting Closure

Meeting packs live under `views/meeting-packs/` and are not source records. Generate them before the meeting; after the meeting, sync the actual notes and outcomes.

Every project meeting, offline sync, or supplemental note should become at least one of:

- daily log entry
- decision log entry
- action item in a relevant view or record
- Workstream Delivery Record update
- Business Decision Packet
- explicit no-op with rationale

## Acceptance Review

- Acceptance readiness should be reviewed before stakeholder acceptance, cutover, or major delivery milestone.
- For migration/cutover projects, acceptance ready and cutover ready are separate judgments.

## Cadence Gaps

- Confirm the project timezone before date-window automation.
- Confirm recurring meeting names and dates.
- Record any approved long-term FDE weekday override with its source.
- Confirm report recipients.
- Confirm missing-update handling.
