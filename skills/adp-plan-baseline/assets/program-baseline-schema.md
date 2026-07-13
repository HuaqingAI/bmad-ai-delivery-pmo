# Program Baseline Contract

`plans/program-baseline.md` is a localized human view followed by one canonical JSON block marked with `<!-- adp:program-baseline:v1 -->`. Downstream tools parse only that marked JSON block.

Required root keys are `schema_version`, `baseline_id`, `revision`, `confirmation_status`, `project`, `default_tolerance_days`, `gates`, `milestones`, `critical_path`, `weighting`, `created_at`, and `updated_at`.

Every gate and milestone requires `id`, `name`, `planned_date`, `owner`, `confirmation_status`, `source`, `dependencies`, and `baseline_revision`. Milestones also require `workstream_id`. `source` requires `type`, `reference`, and `confirmed_by` for approved facts.

Canonical confirmation values are `candidate`, `confirmed`, and `approved`. Executed baselines require the root and every plan item to be `confirmed` or `approved`.

IDs are case-sensitive stable tokens matching `[A-Za-z0-9][A-Za-z0-9._-]*`. Dependencies and `critical_path` entries reference those IDs. Dates use ISO `YYYY-MM-DD`. Item tolerance overrides are integers from 0 through 90.

Weighting is disabled by default. When enabled, `completion_measure` and `source` are required, every milestone needs a numeric `weight` and non-empty `completion_criteria`, and weights total exactly 100.

Update input uses JSON Merge Patch semantics for objects; `null` deletes a field and arrays replace the entire prior array. It also requires root siblings `change_reason` and `decision_source`, where the latter follows the source contract and represents approved change authority.
