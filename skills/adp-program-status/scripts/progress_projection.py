#!/usr/bin/env python3
"""Build and validate the canonical ADP progress v2 projection."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


PROGRESS_SCHEMA_VERSION = "3.0.0"
PROGRESS_BASIS = "weighted-milestone"
MIGRATION_ERROR_CODE = "ADP-PROGRESS-MIGRATION-REQUIRED"
ZERO = Decimal("0")
HUNDRED = Decimal("100")


class ProgressContractError(ValueError):
    """Raised when a generated progress projection violates the frozen contract."""


def build_progress_projection(
    *,
    baseline: dict[str, Any],
    scope_contract: dict[str, Any],
    assessed_milestones: list[dict[str, Any]],
    assessed_gates: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    audit: dict[str, Any],
    as_of: date,
    reporting_period: dict[str, str],
    source_fingerprints: dict[str, str],
    previous_snapshot: dict[str, Any] | None,
    audited_source_keys: set[str] | None = None,
) -> dict[str, Any]:
    baseline_items = {str(item["id"]): item for item in baseline.get("milestones", [])}
    assessed_items = {str(item["id"]): item for item in assessed_milestones}
    identity = build_scope_identity(baseline)
    baseline_lineage = lineage_for_baseline(baseline, source_fingerprints)
    audit_lineage = lineage_for_audit(audit)
    progress_rows, eligible_actuals, excluded_actuals = evaluate_actual_eligibility(
        baseline_items,
        assessed_items,
        rows,
        audit,
        as_of,
        source_fingerprints,
        scope_contract,
        audited_source_keys,
    )
    corrections = collect_corrections(rows, baseline_items, audit, source_fingerprints)
    horizons = forecast_horizons(progress_rows, as_of)
    comparability = comparability_for(identity, previous_snapshot, audit)
    weighting_status, weighting_reasons = validate_weighting(baseline_items, baseline.get("weighting", {}))

    virtual_scope_ids = {
        str(item.get("scope_id"))
        for item in scope_contract.get("virtual_scopes", [])
        if isinstance(item, dict)
    }
    workstream_ids = sorted(
        {
            str(item.get("workstream_id") or "")
            for item in baseline_items.values()
            if str(item.get("workstream_id") or "") not in virtual_scope_ids
        }
        - {""}
    )
    has_physical_l0_scope = any(
        workstream_id.casefold() == "l0" or workstream_id.casefold().startswith("l0-")
        for workstream_id in workstream_ids
    )
    if "L0" not in workstream_ids and not has_physical_l0_scope:
        workstream_ids.insert(0, "L0")

    by_workstream: list[dict[str, Any]] = []
    for workstream_id in workstream_ids:
        scope_items = [item for item in progress_rows if item["workstream_id"] == workstream_id]
        if workstream_id == "L0" and not scope_items:
            by_workstream.append(
                gate_readiness_projection(
                    assessed_gates,
                    baseline_lineage,
                    comparability_for_scope(comparability, previous_snapshot, workstream_id),
                )
            )
            continue
        scope_status, scope_reasons = validate_scope_weights(scope_items, weighting_status, weighting_reasons)
        by_workstream.append(
            as_workstream_projection(
                build_scope_projection(
                scope_id=workstream_id,
                items=scope_items,
                measurement_status=scope_status,
                measurement_reasons=scope_reasons,
                as_of=as_of,
                reporting_period=reporting_period,
                horizons=horizons,
                project_scale=True,
                baseline_lineage=baseline_lineage,
                audit_lineage=audit_lineage,
                    comparability=comparability_for_scope(comparability, previous_snapshot, workstream_id),
                ),
                workstream_id,
            )
        )

    by_scope: list[dict[str, Any]] = [as_physical_scope_projection(item) for item in by_workstream]
    for scope_id in sorted(virtual_scope_ids):
        scope_items = [item for item in progress_rows if item["workstream_id"] == scope_id]
        scope_status, scope_reasons = validate_scope_weights(scope_items, weighting_status, weighting_reasons)
        by_scope.append(
            as_virtual_scope_projection(
                build_scope_projection(
                    scope_id=scope_id,
                    items=scope_items,
                    measurement_status=scope_status,
                    measurement_reasons=scope_reasons,
                    as_of=as_of,
                    reporting_period=reporting_period,
                    horizons=horizons,
                    project_scale=True,
                    baseline_lineage=baseline_lineage,
                    audit_lineage=audit_lineage,
                    comparability=comparability_for_scope(comparability, previous_snapshot, scope_id),
                )
            )
        )

    weighted_workstreams = [item for item in by_workstream if item["progress_kind"] == PROGRESS_BASIS]
    if weighting_status == "measurable":
        overall_status = "measurable"
        overall_reasons = [measurement_reason("eligible", "PROGRESS-WEIGHTING-APPROVED", "Approved milestone weighting and audited actual evidence are available.", [baseline_lineage["source_reference"]], [])]
    elif any(item["measurement_status"] == "measurable" for item in weighted_workstreams):
        overall_status = "partial"
        overall_reasons = [measurement_reason("partial-workstream-coverage", "PROGRESS-PARTIAL-WORKSTREAM-COVERAGE", "Some workstreams are measurable but the program rollup is not safe.", [baseline_lineage["source_reference"]], ["adp-plan-baseline"])]
    else:
        overall_status = weighting_status
        overall_reasons = weighting_reasons

    overall = build_scope_projection(
        scope_id="program",
        items=progress_rows,
        measurement_status=overall_status,
        measurement_reasons=overall_reasons,
        as_of=as_of,
        reporting_period=reporting_period,
        horizons=horizons,
        project_scale=False,
        baseline_lineage=baseline_lineage,
        audit_lineage=audit_lineage,
        comparability=comparability,
    )

    previous_actual = previous_progress_actual(previous_snapshot)
    current_actual = overall["current"]["actual_completion_percent"]
    correction_workstreams = {
        str(baseline_items[item["milestone_id"]]["workstream_id"]): item
        for item in corrections
        if item["milestone_id"] in baseline_items
    }
    blocked_workstreams: list[str] = []
    for index, workstream in enumerate(by_workstream):
        current_scope_actual = workstream["current"]["actual_completion_percent"]
        previous_scope = previous_scope_actual(previous_snapshot, workstream["workstream_id"])
        if (
            workstream["comparability"]["continuous_trend"]
            and previous_scope is not None
            and current_scope_actual is not None
            and Decimal(str(current_scope_actual)) < Decimal(str(previous_scope))
        ):
            correction = correction_workstreams.get(workstream["workstream_id"])
            if correction:
                correction_lineage = lineage_for_correction(correction, source_fingerprints)
                workstream["value_lineage"]["actual_completion_percent"] = unique_lineage(
                    [*workstream["value_lineage"]["actual_completion_percent"], correction_lineage]
                )
                for point in workstream["series"]["actual_points"]:
                    point["value_lineage"] = unique_lineage([*point["value_lineage"], correction_lineage])
            else:
                blocked_workstreams.append(workstream["workstream_id"])
                by_workstream[index] = blocked_projection(
                    workstream,
                    [
                        measurement_reason(
                            "audit-blocked",
                            "PROGRESS-ACTUAL-DECREASE-WITHOUT-CORRECTION",
                            "Comparable workstream actual completion decreased without audited correction lineage.",
                            [audit_lineage["source_reference"]],
                            ["adp-status-sync", "adp-state-audit"],
                        )
                    ],
                )
    if (
        comparability["continuous_trend"]
        and previous_actual is not None
        and current_actual is not None
        and Decimal(str(current_actual)) < Decimal(str(previous_actual))
    ):
        if corrections:
            correction_lineages = [lineage_for_correction(item, source_fingerprints) for item in corrections]
            overall["value_lineage"]["actual_completion_percent"].extend(correction_lineages)
            for point in overall["series"]["actual_points"]:
                point["value_lineage"].extend(correction_lineages)
        else:
            reason = measurement_reason(
                "audit-blocked",
                "PROGRESS-ACTUAL-DECREASE-WITHOUT-CORRECTION",
                "Comparable actual completion decreased without audited correction lineage.",
                [audit_lineage["source_reference"]],
                ["adp-status-sync", "adp-state-audit"],
            )
            overall = blocked_projection(overall, [reason])
            overall_status = "blocked"
    if blocked_workstreams and overall_status != "blocked":
        reason = measurement_reason(
            "audit-blocked",
            "PROGRESS-WORKSTREAM-DECREASE-WITHOUT-CORRECTION",
            "One or more comparable workstreams decreased without audited correction lineage: " + ", ".join(sorted(blocked_workstreams)),
            [audit_lineage["source_reference"]],
            ["adp-status-sync", "adp-state-audit"],
        )
        overall = blocked_projection(overall, [reason])
        overall_status = "blocked"

    projection = {
        "progress_schema_version": PROGRESS_SCHEMA_VERSION,
        "basis": PROGRESS_BASIS,
        "as_of": as_of.isoformat(),
        "reporting_period": {"start": reporting_period["start"], "end": reporting_period["end"]},
        "scope_identity": identity,
        "measurement_status": overall_status,
        "measurement_reasons": overall["measurement_reasons"],
        "overall": overall,
        "by_scope": by_scope,
        "by_workstream": by_workstream,
        "eligibility": {
            "as_of": as_of.isoformat(),
            "input_audit_id": str(audit["input_audit_id"]),
            "eligible_actuals": eligible_actuals,
            "excluded_actuals": excluded_actuals,
        },
        "corrections": corrections,
        "weighted_completion_percent": overall["current"]["actual_completion_percent"] if overall_status == "measurable" else None,
        "completion_measure": str(baseline.get("weighting", {}).get("completion_measure") or "") or None,
        "reason_key": "status.progress_reason.weighted_actuals" if overall_status == "measurable" else "status.progress_reason.weighting_disabled",
        "compatibility": {
            "legacy_progress_version": "2.0",
            "strategy": "physical-by-workstream-alias",
            "migration_error_code": MIGRATION_ERROR_CODE,
        },
        "recovery": build_recovery(overall_status, overall["measurement_reasons"], excluded_actuals),
    }
    apply_actual_deltas(projection, previous_snapshot)
    validate_progress_projection(projection)
    return projection


def evaluate_actual_eligibility(
    baseline_items: dict[str, dict[str, Any]],
    assessed_items: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    audit: dict[str, Any],
    as_of: date,
    source_fingerprints: dict[str, str],
    scope_contract: dict[str, Any],
    audited_source_keys: set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted_sources = (
        audited_source_keys
        if audited_source_keys is not None
        else {normalize_path(path) for path in audit.get("source_fingerprints", {})}
    )
    projection_rows: list[dict[str, Any]] = []
    eligible_actuals: list[dict[str, Any]] = []
    excluded_actuals: list[dict[str, Any]] = []
    virtual_scope_ids = {
        str(item.get("scope_id"))
        for item in scope_contract.get("virtual_scopes", [])
        if isinstance(item, dict)
    }
    for milestone_id in sorted(baseline_items):
        item = baseline_items[milestone_id]
        row = rows.get(milestone_id, {})
        assessed = assessed_items[milestone_id]
        workstream_id = str(item["workstream_id"])
        is_virtual = workstream_id in virtual_scope_ids
        raw_actual = parse_optional_date(assessed.get("actual_date") if is_virtual else row.get("actual"))
        weight = decimal_or_none(item.get("weight"))
        wdr_path = str(row.get("wdr_path") or "")
        evidence_source = assessed.get("source_references", []) if is_virtual else row.get("source_references", [])
        evidence = sorted(set(str(value) for value in evidence_source if str(value).strip()))
        exclusion: str | None = None
        if raw_actual is not None and raw_actual > as_of:
            exclusion = "future-actual"
        elif raw_actual is not None and not str(item.get("completion_criteria") or "").strip():
            exclusion = "missing-completion-criteria"
        elif raw_actual is not None and not evidence:
            exclusion = "missing-evidence"
        elif raw_actual is not None and not is_virtual and normalize_path(wdr_path) not in accepted_sources:
            exclusion = "unaudited-actual"
        elif raw_actual is not None and (weight is None or weight <= ZERO or not weight.is_finite()):
            exclusion = "invalid-weight"

        eligible_date = raw_actual if raw_actual is not None and exclusion is None else None
        actual_lineage = (
            lineage_for_virtual(assessed, str(audit["input_audit_id"]))
            if is_virtual
            else lineage_for_wdr(wdr_path, source_fingerprints, str(audit["input_audit_id"]))
        )
        if eligible_date is not None:
            eligible_actuals.append(
                {
                    "milestone_id": milestone_id,
                    "workstream_id": workstream_id,
                    "actual_date": eligible_date.isoformat(),
                    "weight_percent": round2(weight or ZERO),
                    "completion_criteria_reference": str(item["completion_criteria"]),
                    "evidence_references": evidence,
                    "audit_id": str(audit["input_audit_id"]),
                    "rule_id": "PROGRESS-VIRTUAL-ACTUAL-ELIGIBLE" if is_virtual else "PROGRESS-ACTUAL-ELIGIBLE",
                }
            )
        elif raw_actual is not None:
            workflows = ["adp-status-sync"] if exclusion in {"future-actual", "missing-evidence", "missing-completion-criteria"} else ["adp-state-audit"]
            excluded_actuals.append(
                {
                    "milestone_id": milestone_id,
                    "workstream_id": workstream_id,
                    "actual_date": raw_actual.isoformat(),
                    "reason_code": exclusion,
                    "rule_id": f"PROGRESS-{str(exclusion).upper()}",
                    "source_references": sorted(set([wdr_path, *evidence]) - {""}),
                    "recovery_workflows": workflows,
                }
            )

        projection_rows.append(
            {
                "milestone_id": milestone_id,
                "workstream_id": workstream_id,
                "weight": weight,
                "planned_date": date.fromisoformat(str(item["planned_date"])),
                "forecast_date": parse_optional_date(assessed.get("forecast_date")),
                "actual_date": eligible_date,
                "completed": eligible_date is not None,
                "baseline_source": item.get("source"),
                "actual_lineage": actual_lineage,
            }
        )
    return projection_rows, eligible_actuals, excluded_actuals


def validate_weighting(
    baseline_items: dict[str, dict[str, Any]], weighting: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    source = source_reference(weighting.get("source")) or "plans/program-baseline.md"
    if not weighting.get("enabled"):
        return "not-measurable", [measurement_reason("weighting-disabled", "PROGRESS-WEIGHTING-DISABLED", "Approved milestone weighting is disabled.", [source], ["adp-plan-baseline"])]
    if not baseline_items:
        return "not-measurable", [measurement_reason("no-applicable-milestones", "PROGRESS-NO-MILESTONES", "No approved milestones are available for weighted completion.", [source], ["adp-plan-baseline"])]
    weights = [decimal_or_none(item.get("weight")) for item in baseline_items.values()]
    if any(value is None for value in weights):
        return "not-measurable", [measurement_reason("incomplete-weighting", "PROGRESS-WEIGHTING-INCOMPLETE", "At least one approved milestone has no weight.", [source], ["adp-plan-baseline"])]
    if any(not value.is_finite() or value <= ZERO or value > HUNDRED for value in weights if value is not None):
        return "blocked", [measurement_reason("weighting-invalid", "PROGRESS-WEIGHTING-INVALID", "Approved milestone weights must be finite and within (0, 100].", [source], ["adp-plan-baseline"])]
    if sum((value for value in weights if value is not None), ZERO) != HUNDRED:
        return "blocked", [measurement_reason("weighting-invalid", "PROGRESS-WEIGHTING-SUM", "Approved milestone weights must sum to exactly 100.00.", [source], ["adp-plan-baseline"])]
    return "measurable", []


def validate_scope_weights(
    items: list[dict[str, Any]],
    weighting_status: str,
    weighting_reasons: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    if not items:
        return "not-measurable", [measurement_reason("no-applicable-milestones", "PROGRESS-SCOPE-NO-MILESTONES", "The scope has no approved weighted milestones.", ["plans/program-baseline.md"], ["adp-plan-baseline"])]
    if weighting_status != "measurable":
        return weighting_status, weighting_reasons
    if any(item["weight"] is None for item in items):
        return "not-measurable", [measurement_reason("incomplete-weighting", "PROGRESS-SCOPE-WEIGHTING-INCOMPLETE", "The scope has incomplete approved weighting.", ["plans/program-baseline.md"], ["adp-plan-baseline"])]
    return "measurable", [measurement_reason("eligible", "PROGRESS-SCOPE-ELIGIBLE", "The scope has approved weighting and auditable milestone state.", ["plans/program-baseline.md"], [])]


def build_scope_projection(
    *,
    scope_id: str,
    items: list[dict[str, Any]],
    measurement_status: str,
    measurement_reasons: list[dict[str, Any]],
    as_of: date,
    reporting_period: dict[str, str],
    horizons: list[date],
    project_scale: bool,
    baseline_lineage: dict[str, Any],
    audit_lineage: dict[str, Any],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    base = {
        "scope_id": scope_id,
        "progress_kind": PROGRESS_BASIS,
        "measurement_status": measurement_status,
        "measurement_reasons": measurement_reasons,
        "current": null_current(),
        "forecast_summary": null_forecast_summary(),
        "series": {"actual_points": [], "planned_points": [], "forecast_points": []},
        "milestone_counts": {
            "applicable": len(items),
            "eligible_actual": sum(1 for item in items if item["actual_date"] is not None),
            "completed": sum(1 for item in items if item["completed"]),
            "with_valid_forecast": sum(1 for item in items if not item["completed"] and item["forecast_date"] is not None),
        },
        "comparability": comparability,
        "gate_readiness": None,
        "value_lineage": null_value_lineage(baseline_lineage, audit_lineage),
    }
    if measurement_status != "measurable":
        return base

    denominator = sum((item["weight"] for item in items if item["weight"] is not None), ZERO)
    completed = sum((item["weight"] for item in items if item["completed"] and item["weight"] is not None), ZERO)
    planned = sum((item["weight"] for item in items if item["planned_date"] <= as_of and item["weight"] is not None), ZERO)
    actual_percent = percent(completed, denominator)
    planned_percent = percent(planned, denominator)
    project_weight = denominator if project_scale else HUNDRED
    completed_contribution = completed if project_scale else Decimal(str(actual_percent))
    actual_lineage = unique_lineage([item["actual_lineage"] for item in items if item["actual_date"] is not None] or [audit_lineage])
    base["current"] = {
        "actual_completion_percent": actual_percent,
        "planned_completion_percent": planned_percent,
        "completion_gap_pp": round2(Decimal(str(actual_percent)) - Decimal(str(planned_percent))),
        "project_weight_percent": round2(project_weight),
        "completed_contribution_pp": round2(completed_contribution),
    }
    base["value_lineage"] = {
        "actual_completion_percent": actual_lineage,
        "planned_completion_percent": [baseline_lineage],
        "completion_gap_pp": unique_lineage([*actual_lineage, baseline_lineage]),
        "project_weight_percent": [baseline_lineage],
        "completed_contribution_pp": actual_lineage,
        "forecast_completion_percent": unique_lineage([baseline_lineage, audit_lineage]),
        "forecast_coverage_percent": unique_lineage([baseline_lineage, audit_lineage]),
    }

    point_dates = sorted({date.fromisoformat(reporting_period["start"]), date.fromisoformat(reporting_period["end"])})
    for point_date in point_dates:
        actual_ids = sorted(item["milestone_id"] for item in items if item["actual_date"] is not None and item["actual_date"] <= point_date)
        planned_ids = sorted(item["milestone_id"] for item in items if item["planned_date"] <= point_date)
        actual_weight = sum((item["weight"] for item in items if item["milestone_id"] in actual_ids and item["weight"] is not None), ZERO)
        planned_weight = sum((item["weight"] for item in items if item["milestone_id"] in planned_ids and item["weight"] is not None), ZERO)
        base["series"]["actual_points"].append({"horizon_date": point_date.isoformat(), "completion_percent": percent(actual_weight, denominator), "milestone_ids": actual_ids, "value_lineage": actual_lineage})
        base["series"]["planned_points"].append({"horizon_date": point_date.isoformat(), "completion_percent": percent(planned_weight, denominator), "milestone_ids": planned_ids, "value_lineage": [baseline_lineage]})

    remaining = [item for item in items if not item["completed"]]
    remaining_weight = sum((item["weight"] for item in remaining if item["weight"] is not None), ZERO)
    covered = [item for item in remaining if item["forecast_date"] is not None]
    covered_weight = sum((item["weight"] for item in covered if item["weight"] is not None), ZERO)
    coverage_percent, coverage_status = forecast_coverage(covered_weight, remaining_weight)
    for horizon in horizons:
        forecast_ids = sorted(item["milestone_id"] for item in covered if item["forecast_date"] is not None and item["forecast_date"] <= horizon)
        forecast_weight = sum((item["weight"] for item in covered if item["milestone_id"] in forecast_ids and item["weight"] is not None), ZERO)
        point = {
            "horizon_date": horizon.isoformat(),
            "forecast_completion_percent": percent(completed + forecast_weight, denominator),
            "forecast_coverage_percent": coverage_percent,
            "forecast_coverage_status": coverage_status,
            "remaining_weight_percent": percent(remaining_weight, denominator),
            "covered_remaining_weight_percent": percent(covered_weight, denominator),
            "milestone_ids": forecast_ids,
            "value_lineage": unique_lineage([baseline_lineage, audit_lineage, *[item["actual_lineage"] for item in covered if item["milestone_id"] in forecast_ids]]),
        }
        base["series"]["forecast_points"].append(point)
    if base["series"]["forecast_points"]:
        first = base["series"]["forecast_points"][0]
        base["forecast_summary"] = {
            "horizon_date": first["horizon_date"],
            "forecast_completion_percent": first["forecast_completion_percent"],
            "forecast_coverage_percent": first["forecast_coverage_percent"],
            "forecast_coverage_status": first["forecast_coverage_status"],
            "remaining_weight_percent": first["remaining_weight_percent"],
            "covered_remaining_weight_percent": first["covered_remaining_weight_percent"],
            "next_milestone_ids": first["milestone_ids"],
        }
    return base


def gate_readiness_projection(
    gates: list[dict[str, Any]],
    baseline_lineage: dict[str, Any],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    statuses = [str(item.get("status")) for item in gates]
    readiness = "indeterminate"
    if any(value == "off-plan" for value in statuses):
        readiness = "blocked"
    elif any(value in {"at-risk", "indeterminate"} for value in statuses):
        readiness = "degraded"
    elif statuses:
        readiness = "ready"
    return {
        "workstream_id": "L0",
        "workstream_kind": "L0",
        "progress_kind": "gate-readiness",
        "measurement_status": "not-measurable",
        "measurement_reasons": [measurement_reason("l0-gate-only", "PROGRESS-L0-GATE-ONLY", "L0 is represented as gate readiness and has no approved weighted completion basis.", [baseline_lineage["source_reference"]], [])],
        "current": null_current(),
        "forecast_summary": null_forecast_summary(),
        "series": {"actual_points": [], "planned_points": [], "forecast_points": []},
        "milestone_counts": {"applicable": 0, "eligible_actual": 0, "completed": 0, "with_valid_forecast": 0},
        "comparability": comparability,
        "gate_readiness": {
            "applicable_gate_count": len(gates),
            "satisfied_gate_count": sum(1 for value in statuses if value == "on-plan"),
            "blocked_gate_count": sum(1 for value in statuses if value == "off-plan"),
            "readiness_status": readiness,
        },
        "value_lineage": null_value_lineage(baseline_lineage, baseline_lineage),
    }


def blocked_projection(projection: dict[str, Any], reasons: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(projection)
    result["measurement_status"] = "blocked"
    result["measurement_reasons"] = reasons
    result["current"] = null_current()
    result["forecast_summary"] = null_forecast_summary()
    result["series"] = {"actual_points": [], "planned_points": [], "forecast_points": []}
    result["comparability"] = {**result["comparability"], "actual_delta_pp": None}
    return result


def as_workstream_projection(projection: dict[str, Any], workstream_id: str) -> dict[str, Any]:
    result = dict(projection)
    result.pop("scope_id", None)
    result["workstream_id"] = workstream_id
    result["workstream_kind"] = "L0" if workstream_id == "L0" else "delivery"
    return result


def as_physical_scope_projection(projection: dict[str, Any]) -> dict[str, Any]:
    result = dict(projection)
    result["scope_id"] = str(projection["workstream_id"])
    result["scope_kind"] = "physical"
    return result


def as_virtual_scope_projection(projection: dict[str, Any]) -> dict[str, Any]:
    result = dict(projection)
    result["scope_kind"] = "virtual"
    return result


def comparability_for(
    identity: dict[str, Any], previous_snapshot: dict[str, Any] | None, audit: dict[str, Any]
) -> dict[str, Any]:
    previous_id = previous_snapshot.get("snapshot_id") if isinstance(previous_snapshot, dict) else None
    previous_progress = previous_snapshot.get("progress") if isinstance(previous_snapshot, dict) else None
    if not isinstance(previous_progress, dict) or previous_progress.get("progress_schema_version") != PROGRESS_SCHEMA_VERSION:
        reasons = ["no-predecessor"] if previous_snapshot is None else ["legacy-predecessor-progress-schema", MIGRATION_ERROR_CODE]
        return {"disposition": "no-predecessor", "previous_snapshot_id": previous_id, "rebase_id": None, "continuous_trend": False, "actual_delta_pp": None, "reason_codes": reasons}
    previous_identity = previous_progress.get("scope_identity", {})
    rebase = audit.get("progress_rebase") if isinstance(audit.get("progress_rebase"), dict) else {}
    rebase_id = str(rebase.get("rebase_id") or "") or None
    if rebase_id:
        disposition, reasons = "rebased", ["explicit-rebase"]
    elif previous_identity.get("baseline_revision") != identity["baseline_revision"]:
        disposition, reasons = "baseline-revision-changed", ["baseline-revision-changed"]
    elif previous_identity.get("scope_revision") != identity["scope_revision"] or previous_identity.get("scope_fingerprint") != identity["scope_fingerprint"]:
        disposition, reasons = "scope-changed", ["scope-changed"]
    elif previous_identity.get("weighting_fingerprint") != identity["weighting_fingerprint"]:
        disposition, reasons = "scope-changed", ["weighting-fingerprint-changed"]
    else:
        disposition, reasons = "comparable", []
    continuous = disposition in {"comparable", "rebased"}
    previous_actual = previous_progress_actual(previous_snapshot)
    return {
        "disposition": disposition,
        "previous_snapshot_id": previous_id,
        "rebase_id": rebase_id if disposition == "rebased" else None,
        "continuous_trend": continuous,
        "actual_delta_pp": None if not continuous or previous_actual is None else 0.0,
        "reason_codes": reasons,
    }


def comparability_for_scope(
    base: dict[str, Any], previous_snapshot: dict[str, Any] | None, scope_id: str
) -> dict[str, Any]:
    result = dict(base)
    previous = previous_scope_actual(previous_snapshot, scope_id)
    result["actual_delta_pp"] = 0.0 if result["continuous_trend"] and previous is not None else None
    return result


def apply_actual_deltas(projection: dict[str, Any], previous_snapshot: dict[str, Any] | None) -> None:
    overall = projection["overall"]
    previous = previous_progress_actual(previous_snapshot)
    current = overall["current"]["actual_completion_percent"]
    if overall["comparability"]["continuous_trend"] and previous is not None and current is not None:
        overall["comparability"]["actual_delta_pp"] = round2(Decimal(str(current)) - Decimal(str(previous)))
    for item in projection["by_workstream"]:
        previous = previous_scope_actual(previous_snapshot, item["workstream_id"])
        current = item["current"]["actual_completion_percent"]
        if item["comparability"]["continuous_trend"] and previous is not None and current is not None:
            item["comparability"]["actual_delta_pp"] = round2(Decimal(str(current)) - Decimal(str(previous)))
    for item in projection["by_scope"]:
        if item.get("scope_kind") != "virtual":
            continue
        previous = previous_scope_actual(previous_snapshot, item["scope_id"])
        current = item["current"]["actual_completion_percent"]
        if item["comparability"]["continuous_trend"] and previous is not None and current is not None:
            item["comparability"]["actual_delta_pp"] = round2(Decimal(str(current)) - Decimal(str(previous)))


def build_scope_identity(baseline: dict[str, Any]) -> dict[str, Any]:
    revision = int(baseline["revision"])
    scope_payload = {
        "baseline_id": baseline["baseline_id"],
        "revision": revision,
        "milestones": [
            {"id": item["id"], "workstream_id": item["workstream_id"]}
            for item in sorted(baseline.get("milestones", []), key=lambda row: str(row["id"]))
        ],
    }
    weighting_payload = {
        "enabled": bool(baseline.get("weighting", {}).get("enabled")),
        "completion_measure": baseline.get("weighting", {}).get("completion_measure"),
        "weights": [
            {"id": item["id"], "weight": canonical_decimal(item.get("weight"))}
            for item in sorted(baseline.get("milestones", []), key=lambda row: str(row["id"]))
        ],
    }
    return {
        "baseline_revision": revision,
        "scope_revision": f"{baseline['baseline_id']}:r{revision}",
        "scope_fingerprint": digest(scope_payload),
        "weighting_fingerprint": digest(weighting_payload),
    }


def collect_corrections(
    rows: dict[str, dict[str, Any]],
    baseline_items: dict[str, dict[str, Any]],
    audit: dict[str, Any],
    source_fingerprints: dict[str, str],
) -> list[dict[str, Any]]:
    corrections: list[dict[str, Any]] = []
    for milestone_id in sorted(rows):
        row = rows[milestone_id]
        correction_id = str(row.get("correction_id") or "").strip()
        if not correction_id:
            continue
        if milestone_id not in baseline_items:
            continue
        source_refs = sorted(set([str(row.get("correction_source") or "").strip(), str(row.get("wdr_path") or "").strip()]) - {""})
        corrections.append(
            {
                "correction_id": correction_id,
                "milestone_id": milestone_id,
                "kind": str(row.get("correction_kind") or "actual-date-correction"),
                "previous_actual_date": iso_optional_date(row.get("previous_actual")),
                "corrected_actual_date": iso_optional_date(row.get("actual")),
                "audit_id": str(row.get("correction_audit_id") or audit["input_audit_id"]),
                "rule_id": "PROGRESS-AUDITED-ACTUAL-CORRECTION",
                "source_references": source_refs,
            }
        )
    return corrections


def validate_progress_projection(value: dict[str, Any]) -> None:
    required = {"progress_schema_version", "basis", "as_of", "reporting_period", "scope_identity", "measurement_status", "measurement_reasons", "overall", "by_scope", "by_workstream", "eligibility", "corrections", "weighted_completion_percent", "completion_measure", "reason_key", "compatibility", "recovery"}
    missing = sorted(required - set(value))
    if missing:
        raise ProgressContractError("progress projection missing fields: " + ", ".join(missing))
    if value["progress_schema_version"] != PROGRESS_SCHEMA_VERSION or value["basis"] != PROGRESS_BASIS:
        raise ProgressContractError(MIGRATION_ERROR_CODE)
    status = value["measurement_status"]
    if status not in {"measurable", "partial", "not-measurable", "blocked"}:
        raise ProgressContractError(f"invalid measurement_status {status!r}")
    if not isinstance(value["by_scope"], list) or not isinstance(value["by_workstream"], list):
        raise ProgressContractError("by_scope and by_workstream must be arrays")
    virtual_ids = {
        str(item.get("scope_id"))
        for item in value["by_scope"]
        if isinstance(item, dict) and item.get("scope_kind") == "virtual"
    }
    if any(item.get("workstream_id") in virtual_ids for item in value["by_workstream"] if isinstance(item, dict)):
        raise ProgressContractError("virtual scopes must not appear in by_workstream")
    for scope in [value["overall"], *value["by_scope"]]:
        current = scope["current"]
        numeric = [current["actual_completion_percent"], current["planned_completion_percent"], current["project_weight_percent"], current["completed_contribution_pp"]]
        if scope["measurement_status"] == "measurable":
            if any(item is None or not 0 <= item <= 100 for item in numeric):
                raise ProgressContractError(f"scope {scope.get('scope_id') or scope.get('workstream_id')} has invalid measurable percentages")
            expected_gap = round2(Decimal(str(current["actual_completion_percent"])) - Decimal(str(current["planned_completion_percent"])))
            if current["completion_gap_pp"] != expected_gap:
                raise ProgressContractError("completion_gap_pp does not equal actual minus planned")
        elif any(item is not None for item in current.values()):
            raise ProgressContractError("non-measurable scope must encode progress values as null")
        for point in scope["series"]["forecast_points"]:
            if point["forecast_coverage_status"] == "complete" and point["forecast_coverage_percent"] is not None:
                raise ProgressContractError("complete forecast coverage must have a null percent")
    measurable = [item for item in value["by_scope"] if item["progress_kind"] == PROGRESS_BASIS and item["measurement_status"] == "measurable"]
    if status == "measurable":
        contribution = round2(sum((Decimal(str(item["current"]["completed_contribution_pp"])) for item in measurable), ZERO))
        if contribution != value["overall"]["current"]["actual_completion_percent"]:
            raise ProgressContractError("overall actual does not equal workstream completed contribution")
    if status == "blocked" and value["recovery"]["status"] != "required":
        raise ProgressContractError("blocked progress requires recovery")


def build_recovery(
    status: str, reasons: list[dict[str, Any]], excluded_actuals: list[dict[str, Any]]
) -> dict[str, Any]:
    reason_codes = sorted({str(item["reason_code"]) for item in reasons} | {str(item["reason_code"]) for item in excluded_actuals})
    workflows = sorted({str(value) for item in reasons for value in item["recovery_workflows"]} | {str(value) for item in excluded_actuals for value in item["recovery_workflows"]})
    if status == "blocked":
        return {"status": "required", "reason_codes": reason_codes or ["audit-blocked"], "workflows": workflows or ["adp-state-audit"]}
    if reason_codes or workflows:
        return {"status": "available", "reason_codes": reason_codes, "workflows": workflows}
    return {"status": "not-required", "reason_codes": [], "workflows": []}


def measurement_reason(
    code: str, rule_id: str, detail: str, sources: list[str], workflows: list[str]
) -> dict[str, Any]:
    return {"reason_code": code, "rule_id": rule_id, "detail": detail, "source_references": sorted(set(sources)), "recovery_workflows": sorted(set(workflows))}


def forecast_horizons(items: list[dict[str, Any]], as_of: date) -> list[date]:
    return sorted({value for item in items for value in (item["planned_date"], item["forecast_date"]) if value is not None and value > as_of})


def forecast_coverage(covered: Decimal, remaining: Decimal) -> tuple[float | None, str]:
    if remaining == ZERO:
        return None, "complete"
    value = percent(covered, remaining)
    if value == 100:
        return value, "full"
    if value == 0:
        return value, "none"
    return value, "partial"


def null_current() -> dict[str, None]:
    return {"actual_completion_percent": None, "planned_completion_percent": None, "completion_gap_pp": None, "project_weight_percent": None, "completed_contribution_pp": None}


def null_forecast_summary() -> dict[str, Any]:
    return {"horizon_date": None, "forecast_completion_percent": None, "forecast_coverage_percent": None, "forecast_coverage_status": "not-applicable", "remaining_weight_percent": None, "covered_remaining_weight_percent": None, "next_milestone_ids": []}


def null_value_lineage(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "actual_completion_percent": [secondary],
        "planned_completion_percent": [primary],
        "completion_gap_pp": unique_lineage([primary, secondary]),
        "project_weight_percent": [primary],
        "completed_contribution_pp": [secondary],
        "forecast_completion_percent": unique_lineage([primary, secondary]),
        "forecast_coverage_percent": unique_lineage([primary, secondary]),
    }


def lineage_for_baseline(baseline: dict[str, Any], source_fingerprints: dict[str, str]) -> dict[str, Any]:
    reference = source_reference(baseline.get("weighting", {}).get("source")) or source_reference(baseline.get("project", {}).get("source")) or "plans/program-baseline.md"
    return lineage("baseline", reference, fingerprint_for(reference, source_fingerprints), "PROGRESS-APPROVED-BASELINE")


def lineage_for_audit(audit: dict[str, Any]) -> dict[str, Any]:
    reference = f"input-audit:{audit['input_audit_id']}"
    return lineage("input-audit", reference, digest(audit), "PROGRESS-INPUT-AUDIT", audit_id=str(audit["input_audit_id"]))


def lineage_for_wdr(reference: str, source_fingerprints: dict[str, str], audit_id: str) -> dict[str, Any]:
    return lineage("wdr", reference or "delivery-record:missing", fingerprint_for(reference, source_fingerprints), "PROGRESS-WDR-ACTUAL", audit_id=audit_id)


def lineage_for_virtual(assessed: dict[str, Any], audit_id: str) -> dict[str, Any]:
    references = [str(value) for value in assessed.get("source_references", []) if str(value).strip()]
    reference = next(
        (value for value in references if value.startswith("snapshots/program-status/")),
        references[0] if references else f"virtual-scope:{assessed.get('workstream_id', 'program')}",
    )
    return lineage(
        "virtual-aggregation",
        reference,
        digest({"references": references, "aggregation": assessed.get("aggregation")}),
        str(assessed.get("rule_id") or "PROGRESS-VIRTUAL-SCOPE"),
        audit_id=audit_id,
    )


def lineage_for_correction(item: dict[str, Any], source_fingerprints: dict[str, str]) -> dict[str, Any]:
    reference = item["source_references"][0]
    return lineage("correction", reference, fingerprint_for(reference, source_fingerprints), item["rule_id"], audit_id=item["audit_id"], correction_id=item["correction_id"])


def lineage(
    source_type: str,
    source_reference_value: str,
    fingerprint: str,
    rule_id: str,
    *,
    audit_id: str | None = None,
    correction_id: str | None = None,
    rebase_id: str | None = None,
) -> dict[str, Any]:
    return {"source_type": source_type, "source_reference": source_reference_value, "fingerprint": fingerprint, "rule_id": rule_id, "audit_id": audit_id, "correction_id": correction_id, "rebase_id": rebase_id}


def unique_lineage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def previous_progress_actual(snapshot: dict[str, Any] | None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    progress = snapshot.get("progress")
    if not isinstance(progress, dict) or progress.get("progress_schema_version") != PROGRESS_SCHEMA_VERSION:
        return None
    return progress.get("overall", {}).get("current", {}).get("actual_completion_percent")


def previous_scope_actual(snapshot: dict[str, Any] | None, scope_id: str) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    progress = snapshot.get("progress")
    if not isinstance(progress, dict) or progress.get("progress_schema_version") != PROGRESS_SCHEMA_VERSION:
        return None
    for item in progress.get("by_scope", []):
        if item.get("scope_id") == scope_id:
            return item.get("current", {}).get("actual_completion_percent")
    for item in progress.get("by_workstream", []):
        if item.get("workstream_id") == scope_id:
            return item.get("current", {}).get("actual_completion_percent")
    return None


def percent(numerator: Decimal, denominator: Decimal) -> float:
    if denominator <= ZERO:
        raise ProgressContractError("progress denominator must be positive")
    return round2(HUNDRED * numerator / denominator)


def round2(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def canonical_decimal(value: Any) -> str | None:
    parsed = decimal_or_none(value)
    return None if parsed is None else format(parsed, "f")


def parse_optional_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text.casefold() in {"tbd", "n/a", "na", "none", "unknown", "-"}:
        return None
    return date.fromisoformat(text)


def iso_optional_date(value: Any) -> str | None:
    parsed = parse_optional_date(value)
    return parsed.isoformat() if parsed else None


def source_reference(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("reference") or "").strip()
    return str(value or "").strip()


def fingerprint_for(reference: str, source_fingerprints: dict[str, str]) -> str:
    normalized = normalize_path(reference)
    for path, fingerprint in source_fingerprints.items():
        if normalize_path(path) == normalized or normalize_path(path).endswith(normalized):
            return str(fingerprint).removeprefix("sha256:")
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def normalize_path(value: Any) -> str:
    normalized = str(value or "").strip().replace("\\", "/").lstrip("./")
    memory_marker = "_bmad-output/adp/memory/"
    if memory_marker in normalized:
        return normalized.split(memory_marker, 1)[1]
    return normalized


def digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
