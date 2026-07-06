# Action Ledger Schema

`actions/action-ledger.md` is the durable ADP source of truth for project-level follow-up actions. Derived views, including `views/fde-actions.md`, must be regenerated from this ledger plus readiness, risk, decision, and stale-sync outputs.

## Fields

| Field | Meaning |
| --- | --- |
| Action ID | Stable identifier in `ACT-YYYYMMDD-NNN` form unless supplied by intake. |
| Status | One of `open`, `in-progress`, `blocked`, `done`, or `cancelled`. |
| Owner | Accountable FDE, lead, or follow-up owner. Use `TBD` only when the gap is real. |
| Workstream | A normalized workstream id, `program` for canonical multi-workstream actions, or `TBD` only when routing is unresolved. |
| Affected Workstreams | Semicolon-separated normalized workstream ids affected by the action. For single-workstream actions this usually repeats `Workstream`; for `program` actions it carries the impacted lines. |
| Action | The concrete follow-up to close. |
| Source | Meeting, WDR, review output, decision packet, or other traceable source. |
| Reason | Why this action exists, including visible gaps when owner, due, or closure criteria are weak. |
| Due / Trigger | Date, cadence, or event that should trigger follow-up. |
| Closure Criteria | Observable condition that closes the action. Use `TBD` when missing and keep the gap visible. |
| Last Updated | Timestamp written by the owning workflow. |
| Owning Workflow | Workflow that registered or last updated the action. |

## Status Rules

- Keep unresolved items in `open`, `in-progress`, or `blocked`.
- Use `done` or `cancelled` only for explicit close/update intake; never delete historical action rows.
- A missing owner, due trigger, or closure criteria is not a status. Record the action and expose the missing fact as a gap.
- Do not duplicate one Source + Action across workstreams. Use one canonical `program` action with `Affected Workstreams` unless each workstream has a distinct owner, due trigger, or deliverable.
- Business decisions remain in Business Decision Packets; the ledger may contain only the FDE follow-up action that points to the packet.

## Intake

Workflows should create status-sync intake JSON under `intake/status-sync/` instead of editing this file directly. `adp-status-sync` owns ledger upsert, deduplication, and WDR `Next actions` refresh.
