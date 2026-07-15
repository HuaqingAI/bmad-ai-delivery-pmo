# Program Baseline Contract

`plans/program-baseline.md` is a localized human view followed by one canonical JSON block marked with `<!-- adp:program-baseline:v1 -->`. Downstream tools parse only that marked JSON block.

Required root keys are `schema_version`, `baseline_id`, `revision`, `confirmation_status`, `project`, `default_tolerance_days`, `gates`, `milestones`, `critical_path`, `weighting`, `created_at`, and `updated_at`.

Every gate and milestone requires `id`, `name`, `planned_date`, `owner`, `confirmation_status`, `source`, `dependencies`, and `baseline_revision`. Milestones also require `workstream_id`. `source` requires `type`, `reference`, and `confirmed_by` for approved facts.

Canonical confirmation values are `candidate`, `confirmed`, and `approved`. Executed baselines require the root and every plan item to be `confirmed` or `approved`.

IDs are case-sensitive stable tokens matching `[A-Za-z0-9][A-Za-z0-9._-]*`. A milestone `workstream_id` must equal a current WDR's canonical `Workstream ID`; `program` is the only registry-free value. Dependencies reference plan-item IDs. `critical_path` is an ordered hard-dependency chain, not a set of attention nodes: every adjacent pair must be a `dependency` or `aggregation` edge in predecessor-to-target order. Dates use ISO `YYYY-MM-DD`, and a hard dependency's predecessor cannot be planned after its target. Item tolerance overrides are integers from 0 through 90.

Weighting is disabled by default. When enabled, `completion_measure` and `source` are required, every milestone needs a numeric `weight` and non-empty `completion_criteria`, and weights total exactly 100.

Update input uses JSON Merge Patch semantics for objects; `null` deletes a field and arrays replace the entire prior array. It also requires root siblings `change_reason` and `decision_source`, where the latter follows the source contract and represents approved change authority.

## Flow dependency vNext contract

`assets/program-baseline-flow-vnext.schema.json` freezes the flow-bearing extension for the next baseline schema. Only `milestone` and `gate` items become flow nodes. A milestone belongs to its `workstream` lane. A gate declares either a `workstream` lane or the single `program` lane; no other baseline record becomes a node.

Canonical dependencies are objects on the target node. They require `edge_id`, `predecessor`, `relationship_type`, `source`, and `baseline_revision`. `relationship_type` is one of `dependency`, `aggregation`, `conditional`, `rework`, or `informational`. Conditional relationships also require a source-backed `condition`; non-conditional relationships cannot carry one. The target node may declare `predecessor_rule: all` only when it has at least two incoming aggregation relationships; no other predecessor rule is valid.

Legacy string dependencies remain accepted only as compatibility input. Normalize each string to a `dependency` object before validation or identity calculation. Its stable edge ID is `legacy-` plus the first 20 lowercase hex characters of SHA-256 over the UTF-8 string `baseline_id + "\n" + revision + "\n" + predecessor + "\n" + target`. The containing node supplies the target. A consumer never guesses an edge ID by another rule.

Every node and dependency object carries the same positive `baseline_revision` as the root. Unknown nodes, duplicate node or edge IDs, cross-revision references, reversed hard-dependency dates, disconnected critical-path pairs, missing conditional facts, and cycles containing any non-`rework` relationship block canonical topology publication. A cycle made entirely of explicit `rework` relationships is valid and remains visible as a rework loop. Recovery codes and deterministic dispositions are owned by `adp-flow-graph/references/flow-graph-contract-v1.md`.
