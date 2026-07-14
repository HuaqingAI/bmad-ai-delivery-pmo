# Canonical Flow State Contract v1

`assets/program-status-flow-state-v1.schema.json` freezes the `program-status.flow_state` projection consumed by `adp-flow-graph`. Production projection is Roadmap phase 5; this phase defines only the contract.

Each approved baseline milestone or gate has exactly one state record for the same baseline revision. `execution_state` and `health_state` are independent axes:

- execution: `complete`, `in-progress`, `ready`, `planned`, `not-applicable`
- health: `on-plan`, `at-risk`, `blocked`, `indeterminate`

`ready` means all currently applicable predecessor conditions are satisfied and canonical work has not started. `in-progress` requires explicit canonical start evidence. Neither state is inferred from health, due dates, open actions, or graph position. `complete` requires the same audited completion evidence used by canonical progress. `not-applicable` requires source-backed applicability evidence. Missing or conflicting execution evidence yields `planned` or a blocked finding; missing or conflicting health evidence yields `indeterminate`. One axis never supplies the other.

Both axes carry their own `rule_id` and non-empty canonical `sources`. A source identifies `artifact_id`, `artifact_path`, `field`, and `source_fingerprint`; state records also carry `evaluated_at`. Consumers copy these values and lineage without reclassifying them.

Compatibility is explicit: program-status artifacts without `flow_state_schema_version: 1.0.0` return `ADP-FLOW-STATE-MIGRATION-REQUIRED`. They are not converted from the legacy single `status` field.
