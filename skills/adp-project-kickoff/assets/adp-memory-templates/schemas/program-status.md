# Program Status Contract

`adp-program-status` owns the canonical project-level status calculation. Other workflows consume its output and must not recompute the overall judgment.

Every generated status artifact records `schema_version`, `snapshot_id`, `generated_at`, `as_of`, `reporting_period`, `baseline_revision`, `source_inventory`, `source_fingerprints`, `input_audit_id`, `generator_version`, `locale`, `overall_status`, `report_confidence`, `rule_ids`, `milestones`, `gates`, `critical_path`, `variances`, and `period_delta`.

Canonical `overall_status` values are `on-plan`, `at-risk`, `off-plan`, and `indeterminate`. Status and report confidence are independent. Confirmed critical delay remains `off-plan` even when confidence is low; missing facts alone never imply delay or an unconditional green state.

Machine keys and canonical enum values remain English across locales. Localized display labels are additive and never replace the canonical values or source facts.

Snapshots under `snapshots/program-status/` are immutable. A stable snapshot ID is derived from reporting period, as-of time, baseline revision, and input fingerprints. `views/program-status.json` and `views/program-status.md` are replaceable projections of the latest snapshot, not fact sources.
