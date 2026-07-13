# Meeting Sync Schema

Meetings are high-frequency project state inputs. Every meeting item should close into project memory or become an explicit no-op.

## Meeting Types

- FDE internal sync
- business sync
- stakeholder review
- workstream deep dive
- offline supplemental note
- cutover / acceptance room

## Item Classification

| Type | Destination |
| --- | --- |
| Fact | `daily/YYYY-MM-DD.md` and possibly a WDR update |
| Decision | `decisions/decision-log.md` or a workstream decision file |
| Action | relevant WDR, FDE action view, or daily log |
| WDR update | `workstreams/{id}/delivery-record.md` |
| Business decision needed | `decisions/business-decision-packets/{id}.md` |
| No-op | meeting note with rationale |

## Closure Check

For each item, record:

- source meeting
- affected workstream(s)
- classification
- destination file
- owner
- due date or trigger when applicable
- unresolved gap when not closed

When the meeting starts from `adp-meeting-pack`, preserve `meeting_pack_id`, `meeting_pack_path`, `scenario`, `audit_path`, and `roadmap_version` from the pack distillate in the meeting archive, daily log, and any status-sync intake.

For vNext meeting packs, also preserve `program_status_snapshot_id`, `baseline_revision`, `source_fingerprints`, `input_audit_id`, and `generator_version`, plus the actual meeting `started_at` and `ended_at`. The actual meeting has a stable `meeting_instance_id` and canonical `plan_fingerprint`.

## Replay and Cursor Contract

- `meetings/receipts/<meeting-instance-id>.json` is the durable write receipt. `applied` means every classified destination was written or replay-safely confirmed.
- Same instance plus same plan fingerprint is a no-op or resume. Same instance plus a different fingerprint is a conflict.
- `meetings/cursors/<scenario>.json` points only to the latest successfully applied actual meeting. Generating a meeting pack or running meeting-sync dry-run never advances it.
- Append destinations carry meeting-instance operation markers so interrupted executions can resume without duplicating daily, WDR, or workstream-decision blocks.
- Source-backed milestone status/forecast/actual handoffs use the exact baseline milestone ID, one affected workstream, evidence, and baseline revision. Invalid mappings remain visible gaps and never create implicit baseline entries.

## Required Output

A meeting sync is incomplete if any item remains unclassified without an explicit reason.
