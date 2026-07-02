# Cadence

Default cadence: {{DEFAULT_CADENCE}}

## Status Sync

- Default rhythm: {{DEFAULT_CADENCE}}
- Inputs: updated Workstream Delivery Records, meeting notes, decision log, risks, dependencies, evidence gaps
- Output: refreshed project lead view, FDE action list, and daily log entry

## Weekly Report

- Source files: `workstreams/*/delivery-record.md`, `daily/*`, `decisions/decision-log.md`, `views/*`
- Output file: `views/weekly-report.md`
- Required content: status summary, blocked workstreams, risk/dependency changes, decisions needed, readiness gaps, next actions

## Meeting Closure

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

- Confirm recurring meeting names and dates.
- Confirm report recipients.
- Confirm missing-update handling.
