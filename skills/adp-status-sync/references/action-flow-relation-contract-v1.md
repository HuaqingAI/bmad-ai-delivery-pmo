# Action Flow Relation Contract v1

`assets/action-flow-relation-v1.schema.json` freezes the action fields that `adp-status-sync` will publish for canonical flow overlays in Roadmap phase 5.

An action has a stable `action_id`, canonical `status`, timestamps, and explicit `related_plan_item_ids` and `related_flow_edge_ids`. IDs are case-sensitive, unique within their arrays, and never inferred from action text, owner, workstream, or dates. Missing related IDs keep the action in the graph's `unmapped` collection.

Statuses are `open`, `in-progress`, `blocked`, `done`, and `cancelled`. `created_at` and `updated_at` are always required. `in-progress`, `blocked`, and `done` require `started_at`; `done` requires `done_at`; `cancelled` requires `cancelled_at`. A terminal action cannot carry both `done_at` and `cancelled_at`, and all timestamps form a nondecreasing sequence.

Scope evaluation is exact:

- `pending` at an as-of instant means status `open`, `in-progress`, or `blocked`, created no later than the instant, and not terminal at or before it.
- `processed` in any declared window means status `done` and `window.start_inclusive <= done_at < window.end_exclusive`.
- `cancelled` is never `processed` and is not `pending` at or after `cancelled_at`.
- An action with status `blocked` may count in both `pending` and `blocked`; the categories are independent, not a partition.

Compatibility is fail-closed. A legacy action without stable ID, timestamps, or explicit relation arrays remains usable by existing Markdown consumers but is emitted as `unmapped` with `ADP-ACTION-FLOW-MIGRATION-REQUIRED`; graph consumers never synthesize the missing facts.
