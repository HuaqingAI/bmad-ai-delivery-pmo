from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = SKILL_ROOT / "assets/program-status-progress-v2.schema.json"
FIXTURE_ROOT = SKILL_ROOT / "assets/fixtures/progress-v2"
GOLDEN_PATH = FIXTURE_ROOT / "golden-measurable-boundary.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise AssertionError(f"contract test validator only supports local refs: {reference}")
    value: Any = root
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise AssertionError(f"unsupported schema type in contract test: {expected}")


def validate_schema(instance: Any, schema: dict[str, Any], root: dict[str, Any] | None = None, path: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        return validate_schema(instance, _resolve_ref(root, schema["$ref"]), root, path)

    errors: list[str] = []
    for branch in schema.get("allOf", []):
        errors.extend(validate_schema(instance, branch, root, path))
    if "if" in schema:
        condition_matches = not validate_schema(instance, schema["if"], root, path)
        selected = schema.get("then") if condition_matches else schema.get("else")
        if selected:
            errors.extend(validate_schema(instance, selected, root, path))
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        candidates = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(instance, item) for item in candidates):
            errors.append(f"{path}: expected type {candidates!r}, got {type(instance).__name__}")
            return errors
        if instance is None:
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_schema(value, properties[key], root, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")

    if isinstance(instance, list):
        if len(instance) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append(f"{path}: expected at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, item_schema, root, f"{path}[{index}]"))
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")

    if isinstance(instance, str):
        if len(instance) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {instance} is not above {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {instance} is not below {schema['exclusiveMaximum']}")
    return errors


def round2(value: Decimal | int | float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def evaluate_formula_case(case: dict[str, Any]) -> dict[str, Any]:
    as_of = date.fromisoformat(case["as_of"])
    milestones = case["milestones"]
    total_weight = sum(Decimal(str(item["weight_percent"])) for item in milestones)
    if total_weight != Decimal("100"):
        raise AssertionError(f"fixture {case['name']} must have exactly 100 project weight")

    eligible: list[dict[str, Any]] = []
    excluded: dict[str, str] = {}
    for item in milestones:
        actual = date.fromisoformat(item["actual_date"]) if item.get("actual_date") else None
        if actual and actual <= as_of and item["completion_criteria_defined"] and item["evidence_audited"]:
            eligible.append(item)
        elif actual and actual > as_of:
            excluded[item["id"]] = "future-actual"
        elif actual and not item["completion_criteria_defined"]:
            excluded[item["id"]] = "missing-completion-criteria"
        elif actual and not item["evidence_audited"]:
            excluded[item["id"]] = "unaudited-actual"

    completed_ids = {item["id"] for item in eligible}
    completed_weight = sum(Decimal(str(item["weight_percent"])) for item in eligible)
    planned_weight = sum(
        Decimal(str(item["weight_percent"]))
        for item in milestones
        if date.fromisoformat(item["planned_date"]) <= as_of
    )
    actual_percent = round2(Decimal("100") * completed_weight / total_weight)
    planned_percent = round2(Decimal("100") * planned_weight / total_weight)

    remaining = [item for item in milestones if item["id"] not in completed_ids]
    remaining_weight = sum(Decimal(str(item["weight_percent"])) for item in remaining)
    covered = [item for item in remaining if item.get("forecast_date")]
    covered_weight = sum(Decimal(str(item["weight_percent"])) for item in covered)

    forecast_points: list[dict[str, Any]] = []
    for horizon_text in case["horizons"]:
        horizon = date.fromisoformat(horizon_text)
        forecast_due_weight = sum(
            Decimal(str(item["weight_percent"]))
            for item in covered
            if date.fromisoformat(item["forecast_date"]) <= horizon
        )
        forecast_percent = round2(Decimal("100") * (completed_weight + forecast_due_weight) / total_weight)
        if remaining_weight == 0:
            coverage_percent = None
            coverage_status = "complete"
        else:
            coverage_percent = round2(Decimal("100") * covered_weight / remaining_weight)
            coverage_status = "full" if coverage_percent == 100 else ("none" if coverage_percent == 0 else "partial")
        forecast_points.append(
            {
                "horizon_date": horizon_text,
                "forecast_completion_percent": forecast_percent,
                "forecast_coverage_percent": coverage_percent,
                "forecast_coverage_status": coverage_status,
            }
        )

    by_workstream: dict[str, dict[str, float]] = {}
    for workstream_id in sorted({item["workstream_id"] for item in milestones}):
        scope = [item for item in milestones if item["workstream_id"] == workstream_id]
        scope_weight = sum(Decimal(str(item["weight_percent"])) for item in scope)
        scope_completed = sum(
            Decimal(str(item["weight_percent"])) for item in eligible if item["workstream_id"] == workstream_id
        )
        scope_planned = sum(
            Decimal(str(item["weight_percent"]))
            for item in scope
            if date.fromisoformat(item["planned_date"]) <= as_of
        )
        scope_actual_percent = round2(Decimal("100") * scope_completed / scope_weight)
        scope_planned_percent = round2(Decimal("100") * scope_planned / scope_weight)
        by_workstream[workstream_id] = {
            "actual_completion_percent": scope_actual_percent,
            "planned_completion_percent": scope_planned_percent,
            "completion_gap_pp": round2(Decimal(str(scope_actual_percent)) - Decimal(str(scope_planned_percent))),
            "project_weight_percent": round2(scope_weight),
            "completed_contribution_pp": round2(scope_completed),
        }

    return {
        "actual_completion_percent": actual_percent,
        "planned_completion_percent": planned_percent,
        "completion_gap_pp": round2(Decimal(str(actual_percent)) - Decimal(str(planned_percent))),
        "forecast_points": forecast_points,
        "eligible_actual_ids": sorted(completed_ids),
        "excluded_actuals": dict(sorted(excluded.items())),
        "by_workstream": by_workstream,
    }


def comparability_for(case: dict[str, Any]) -> dict[str, Any]:
    before = case["previous"]
    after = case["current"]
    if case.get("rebase_id"):
        disposition = "rebased"
    elif before["baseline_revision"] != after["baseline_revision"]:
        disposition = "baseline-revision-changed"
    elif (
        before["scope_revision"] != after["scope_revision"]
        or before["scope_fingerprint"] != after["scope_fingerprint"]
        or before["weighting_fingerprint"] != after["weighting_fingerprint"]
    ):
        disposition = "scope-changed"
    else:
        disposition = "comparable"
    continuous = disposition in {"comparable", "rebased"}
    return {"disposition": disposition, "continuous_trend": continuous, "delta_allowed": continuous}
