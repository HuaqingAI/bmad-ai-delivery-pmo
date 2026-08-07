# Canonical Progress Contract v3

This reference freezes the `program-status.progress` contract that roadmap, meeting-pack, management-panel, and Program Lead consumers will read. The machine contract is `assets/program-status-progress-v3.schema.json`. Contract fixtures under `assets/fixtures/progress-v3/` are normative examples. A consumer may format, filter, or select a supplied horizon; it must not aggregate milestone weights, substitute dates, infer comparability, or repair missing values.

## Version and units

- `progress_schema_version` is exactly `3.0.0`; `basis` is exactly `weighted-milestone`.
- Fields ending in `_percent` use a `0..100` scale. `completion_gap_pp`, `actual_delta_pp`, and `completed_contribution_pp` are percentage points; date variance remains `variance_days` outside this object.
- Approved milestone weights are project percentage points and sum to `100.00` across the applicable project scope. Calculations retain source precision in decimal arithmetic and serialize to two decimal places with round-half-up.
- `completion_gap_pp` is the serialized actual percent minus the serialized planned percent. It is never a schedule-days metric.
- `by_scope[].current.project_weight_percent` is the scope's share of project weight. `completed_contribution_pp` is completed project weight, not the within-scope completion rate. Overall actual equals the sum of measurable weighted scope contributions. Scope contributions use deterministic largest-remainder allocation at `0.01` percentage-point precision so their serialized sum equals the independently rounded true overall; tie-breaking is by ascending canonical `scope_id`.

## Measurement status and nulls

`measurement_status` is one of `measurable`, `partial`, `not-measurable`, or `blocked`.

- `measurable`: scope weighting, audit, and identity are complete. Current percentages and gap are numbers.
- `partial`: some child workstreams are measurable but the containing scope is not safe for a precise rollup. Current percentages and forecast values for that containing scope are `null`; child scopes retain their own disposition.
- `not-measurable`: the scope has no approved completion basis, such as weighting disabled, invalid/incomplete weighting, no applicable milestones, or L0 gate-only semantics. Numeric progress values are `null`.
- `blocked`: freshness, audit integrity, or scope identity prevents consumption. Numeric progress values are `null` and recovery is required.

Every non-measurable state carries at least one structured `measurement_reason`. `0` means measured zero only when status is `measurable`; it never stands in for unknown. A scope that is not measurable has empty series, a `not-applicable` forecast summary, and no continuous delta.

## Actual eligibility

An actual contributes only when all conditions hold:

- its milestone belongs to the approved scope and has a finite approved weight;
- completion criteria are defined;
- `actual_date <= as_of`;
- actual source and evidence are present in the accepted input audit.

`eligibility.eligible_actuals` and `eligibility.excluded_actuals` expose the decision. An exclusion preserves milestone ID, supplied actual date, rule ID, sources, and recovery. Future actuals, unaudited actuals, missing evidence or criteria, scope mismatches, invalid weights, and retractions never contribute silently. A correction or retraction may lower historical actual only when its correction ID, audit ID, source, and rule appear in `corrections` and value lineage.

## Step formulas

For a measurable scope with denominator `scope_weight`:

```text
actual(h)  = 100 * sum(weight where eligible actual_date <= h) / scope_weight
planned(h) = 100 * sum(weight where planned_date <= h) / scope_weight
forecast(h)= 100 * (completed_as_of_weight
                    + sum(uncompleted weight where valid forecast_date <= h))
                    / scope_weight
gap(as_of) = actual(as_of) - planned(as_of)
```

Actual, planned, and forecast are milestone steps; there is no interpolation. Boundary comparison is inclusive. Forecast never substitutes `planned_date` for a missing or invalid forecast. Forecast points are sorted by ascending horizon and carry their own lineage.

Forecast coverage uses all uncompleted weight in the same scope as denominator and uncompleted weight with a valid forecast as numerator:

- `full`: coverage is `100`;
- `partial`: coverage is greater than `0` and less than `100`;
- `none`: coverage is `0`;
- `complete`: remaining weight is zero, coverage percent is `null`, and no division occurs;
- `not-applicable`: the progress scope or horizon is not measurable.

`forecast_summary` is the first supplied future horizon, already copied into display-ready fields. Consumers do not derive it from planned dates or choose a replacement horizon.

## Scope, Workstream, and L0 boundary

`by_scope` is the canonical scope projection. Each entry declares `scope_id` and `scope_kind: physical|virtual`. Physical L1 and higher scopes use `progress_kind: weighted-milestone`; their actual, planned, gap, and forecast percentages are normalized by that Workstream's approved milestone weight while project weight and completed contribution stay on the project scale.

L0 defaults to `progress_kind: gate-readiness`. It carries `gate_readiness`, has `measurement_status: not-measurable` with reason `l0-gate-only`, and all completion fields are `null`. L0 enters weighted progress only when its upstream source supplies approved milestone weights, completion criteria, audited actual evidence, and the same scope identity; then it uses `weighted-milestone` exactly like any other workstream.

The reserved `program` scope appears only in `by_scope` with `scope_kind: virtual`; it has no `workstream_id`, `workstream_kind`, WDR, or gate-readiness projection. Its weighted eligibility uses canonical virtual milestone evidence from Program Status, including aggregation or source-backed signal lineage. It never appears in `by_workstream`.

## Comparability and corrections

`comparability.disposition` is `no-predecessor`, `comparable`, `baseline-revision-changed`, `scope-changed`, or `rebased`.

- `comparable` requires identical baseline revision, scope revision/fingerprint, and weighting fingerprint. It may carry a continuous `actual_delta_pp`.
- `baseline-revision-changed` or `scope-changed` sets `continuous_trend` to false and `actual_delta_pp` to `null`. A weighting fingerprint change is a `scope-changed` disposition with reason `weighting-fingerprint-changed`.
- `no-predecessor` has no previous snapshot and no delta.
- `rebased` restores continuity only with an explicit `rebase_id` and rebase lineage.

Within a comparable scope and without correction lineage, actual step points never decrease. A decrease is valid only when `corrections` and the affected value lineage identify the audited correction or retraction.

## Compatibility and recovery

The v3 object retains `weighted_completion_percent`, `completion_measure`, and `reason_key` as read-only compatibility aliases. The weighted alias equals `overall.current.actual_completion_percent` when the overall scope is measurable and is `null` otherwise. `by_workstream` is a physical-only compatibility projection of the physical entries in `by_scope`; it must never contain `program` or any other virtual scope. `compatibility.strategy` is `physical-by-workstream-alias`; consumers requiring v3 reject a missing or different `progress_schema_version` with `ADP-PROGRESS-MIGRATION-REQUIRED` rather than guessing fields.

`recovery.status` is `not-required`, `available`, or `required`. Non-measurable conditions may offer recovery; blocked conditions require at least one reason code and workflow. Recovery routes to the owner of the missing contract (`adp-plan-baseline`, `adp-status-sync`, or `adp-state-audit`) and never computes a substitute percentage.

## Consumer invariants

- Read `overall` and `by_scope` values directly; use `by_workstream` only for physical compatibility and never sum milestones or infer L0 completion.
- Use `forecast_summary` or a supplied `forecast_points` entry; never fill forecast from planned.
- Break trend lines when `continuous_trend` is false.
- Show correction lineage when actual decreases.
- Keep completion and overall plan health as separate conclusions.
- Preserve `value_lineage`, measurement reasons, and recovery in every projection.
