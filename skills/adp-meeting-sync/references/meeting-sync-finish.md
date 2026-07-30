# Meeting Sync Finish

Use this contract after `sync_meeting.py` returns. The writer result and its dry-run/execute reports are authoritative; keep user-facing text in `{communication_language}`.

Report the meeting archive and durable raw-evidence paths; daily log, decision log, WDR, workstream-decision, and Business Decision Packet writes; generated status-sync intake files; meeting instance ID, replay status, applied receipt, scenario cursor disposition; and every unresolved gap. Report an official panel association only when the applied receipt contains its panel ID and archive. Name the next owning workflow, normally `adp-status-sync` for current fields/actions, `adp-risk-dependency-change-review` for open risk/change/business decisions, or `adp-workstream-register` for unknown workstreams.

A meeting is closed only when every item has a classification, destination, accountable owner where required, and either a durable write or a visible gap. A meeting note alone is not closure.

ADP stores project-level coordination state; do not copy full PRD, architecture, story, code, or validation detail out of BMM artifacts. At the terminal stage after reporting, run the already resolved `{workflow.on_complete}` when non-empty.
