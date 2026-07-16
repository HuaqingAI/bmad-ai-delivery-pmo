#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Compute canonical ADP program status and persist immutable snapshots."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from progress_projection import (
    ProgressContractError,
    build_progress_projection,
    validate_progress_projection,
)


GENERATOR_VERSION = "2.0.2"
SCHEMA_VERSION = "1.0"
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
DEFAULT_CONFIG_SCRIPT = Path(__file__).resolve().parents[2] / "adp-plan-baseline/scripts/adp_effective_config.py"
DEFAULT_BASELINE_SCRIPT = Path(__file__).resolve().parents[2] / "adp-plan-baseline/scripts/baseline.py"
DEFAULT_ARTIFACT_AUDIT_SCRIPT = Path(__file__).resolve().parents[2] / "adp-state-audit/scripts/audit_state.py"
DEFAULT_MEMLOG_SCRIPT = Path(__file__).resolve().parents[3] / "_bmad/scripts/memlog.py"
STATUS_VALUES = {"on-plan", "at-risk", "off-plan", "indeterminate"}
STATUS_RANK = {"on-plan": 0, "indeterminate": 1, "at-risk": 2, "off-plan": 3}
CONFIDENCE_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
MILESTONE_STATUSES = {"planned", "in-progress", "at-risk", "done", "blocked"}
MISSING_VALUES = {"", "tbd", "n/a", "na", "none", "unknown", "-"}
SIGNAL_TYPES = {"gate", "milestone", "dependency", "readiness", "project"}
SNAPSHOT_FILE = re.compile(r"^ps-[0-9a-f]{16}\.json$")


class ContractError(ValueError):
    """Raised when input cannot safely produce a canonical status."""


class DependencyError(ImportError):
    """Raised when an installed sibling ADP contract is unavailable."""

    def __init__(self, dependency_name: str, missing_path: Path, recommended_workflows: list[str]) -> None:
        self.dependency_name = dependency_name
        self.missing_path = missing_path.expanduser().resolve()
        self.recommended_workflows = recommended_workflows
        super().__init__(f"required {dependency_name} script not found: {self.missing_path}")


class RenderCatalog:
    def __init__(self, config_module: Any, locale: str, catalog_fingerprint: str) -> None:
        self.config_module = config_module
        self.locale = locale
        self.catalog_fingerprint = catalog_fingerprint
        self.message_keys: list[str] = []

    def __call__(self, key: str, **values: Any) -> str:
        if key not in self.message_keys:
            self.message_keys.append(key)
        return self.config_module.message(key, self.locale, **values)

    def contract(self, coverage_profile: str) -> dict[str, Any]:
        return render_contract_for_keys(
            coverage_profile,
            self.message_keys,
            self.locale,
            self.catalog_fingerprint,
            self.config_module,
        )


def render_contract_for_keys(
    coverage_profile: str,
    message_keys: list[str],
    locale: str,
    catalog_fingerprint: str,
    config_module: Any,
) -> dict[str, Any]:
    keys = list(dict.fromkeys(message_keys))
    return {
        "coverage_profile": coverage_profile,
        "catalog_locale": locale,
        "catalog_fingerprint": catalog_fingerprint,
        "message_keys": keys,
        "unresolved_message_keys": [],
        "source_fact_translation_persisted": False,
        "localized_system_text": [config_module.message(key, locale) for key in keys],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", help="Project root containing ADP memory and BMad configuration.")
    parser.add_argument("--mode", choices=["generate", "inspect"], default="generate")
    parser.add_argument("--memory-root", default=DEFAULT_MEMORY_ROOT, help="ADP memory root; relative paths resolve from project root.")
    parser.add_argument("--input-audit-json", help="Pre-generation adp-state-audit JSON. Required for generate mode.")
    parser.add_argument("--signals-json", help="Optional canonical source-backed gate, readiness, dependency, or project signals.")
    parser.add_argument("--as-of", help="Status date in ISO YYYY-MM-DD. Default: today.")
    parser.add_argument("--period-start", help="Reporting period start in ISO YYYY-MM-DD.")
    parser.add_argument("--period-end", help="Reporting period end in ISO YYYY-MM-DD. Default: as-of.")
    parser.add_argument("--previous-snapshot", help="Optional previous immutable snapshot JSON for period comparison.")
    parser.add_argument("--language", help="Override document_output_language for this derived view.")
    parser.add_argument("--generated-at", help="Generation timestamp in ISO-8601; primarily for reproducible tests.")
    parser.add_argument("--dry-run", action="store_true", help="Compute and validate without writing snapshots or views.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
    parser.add_argument("--baseline-script", default=str(DEFAULT_BASELINE_SCRIPT), help="adp-plan-baseline deterministic contract implementation.")
    parser.add_argument("--artifact-audit-script", default=str(DEFAULT_ARTIFACT_AUDIT_SCRIPT), help="adp-state-audit artifact validator.")
    parser.add_argument("--headless", action="store_true", help="Return one complete/blocked result after artifact validation and persist decisions in a memlog.")
    parser.add_argument("--memlog", help="Headless memlog path. Relative paths resolve from the project root.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("-o", "--output", help="Write result JSON to this file instead of stdout.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.provided_options = {item.split("=", 1)[0] for item in sys.argv[1:] if item.startswith("--")}
    memlog: Path | None = None
    try:
        if args.headless:
            memlog = initialize_headless_memlog(args)
            append_headless_context(args, memlog)
        result = run(args)
    except DependencyError as exc:
        result = dependency_failure_result(args, exc)
    except (ContractError, ProgressContractError, OSError, json.JSONDecodeError, ImportError) as exc:
        result = failure_result(args, str(exc))
    if args.headless:
        result = finalize_headless_result(args, result, memlog or resolve_headless_memlog(args))
    code = 0 if result.get("ok") else (1 if result.get("status") in {"blocked", "missing"} else 2)
    emit(result, args.output)
    return code


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise ContractError("project_root is not an existing directory")
    memory_root = resolve_path(project_root, args.memory_root)
    if args.mode == "inspect":
        return inspect_latest(project_root, memory_root)
    if not args.input_audit_json:
        raise ContractError("--input-audit-json is required in generate mode; run adp-state-audit phase=input first")

    config_module = load_module(
        Path(args.config_script),
        "adp_effective_config",
        dependency_name="adp-plan-baseline effective config",
        recommended_workflows=["adp-setup"],
    )
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        raise ContractError(str(config.get("error") or "shared ADP effective config could not be resolved"))
    locale = str(config.get("document_locale") or "en")
    as_of = parse_date(args.as_of or date.today().isoformat(), "as-of")
    period = resolve_period(args, config, as_of)
    generated_at = parse_timestamp(args.generated_at) if args.generated_at else datetime.now(timezone.utc).replace(microsecond=0)

    baseline_module = load_baseline_module(Path(args.baseline_script), config_module)
    baseline_path = memory_root / "plans/program-baseline.md"
    if not baseline_path.is_file():
        return blocked_result(project_root, memory_root, locale, config, "program baseline is missing", ["adp-plan-baseline"])
    baseline = baseline_module.parse_baseline(baseline_path)
    validation = baseline_module.validate_model(baseline, execute=True, stored=True)
    if not validation.get("valid"):
        return blocked_result(
            project_root,
            memory_root,
            locale,
            config,
            "program baseline is not valid for execution",
            ["adp-plan-baseline"],
            findings=validation.get("findings", []),
        )

    audit_path = Path(args.input_audit_json).expanduser().resolve()
    audit = load_json(audit_path)
    audit_module = load_module(
        Path(args.artifact_audit_script),
        "adp_program_status_audit_contract",
        dependency_name="adp-state-audit artifact validator",
        recommended_workflows=["adp-setup", "adp-state-audit"],
    )
    integrity_issues = audit_module.validate_input_audit_integrity(audit)
    if integrity_issues:
        return blocked_result(
            project_root,
            memory_root,
            locale,
            config,
            "input audit integrity validation failed: " + "; ".join(integrity_issues),
            ["adp-state-audit"],
        )
    audit_issues = validate_audit(
        audit,
        baseline,
        as_of,
        locale,
        "document_output_language" in config.get("fallbacks", []),
    )
    if audit.get("execution_disposition") == "blocked" or audit.get("safe_to_generate") is False:
        audit_issues.append("input audit execution_disposition is blocked")
    if audit_issues:
        return blocked_result(
            project_root,
            memory_root,
            locale,
            config,
            "; ".join(sorted(set(audit_issues))),
            recommended_from_audit(audit) or ["adp-state-audit"],
        )

    findings: list[dict[str, Any]] = []
    source_inventory: list[dict[str, Any]] = []
    source_fingerprints: dict[str, str] = {}
    add_file_source(project_root, baseline_path, "program-baseline", source_inventory, source_fingerprints)
    add_file_source(project_root, audit_path, "input-audit", source_inventory, source_fingerprints)
    add_config_sources(project_root, config, source_inventory, source_fingerprints)
    locale_catalog_path = Path(args.config_script).expanduser().resolve().parent.parent / "assets/locale-catalog.json"
    if not locale_catalog_path.is_file():
        raise DependencyError("ADP locale catalog", locale_catalog_path, ["adp-setup"])
    add_file_source(project_root, locale_catalog_path, "locale-catalog", source_inventory, source_fingerprints)

    rows, row_sources, row_findings = collect_milestone_rows(project_root, memory_root, baseline)
    findings.extend(row_findings)
    for path in row_sources:
        add_file_source(project_root, path, "workstream-delivery-record", source_inventory, source_fingerprints)
    blocking_rows = [item for item in findings if item.get("severity") == "blocked"]
    if blocking_rows:
        return blocked_result(
            project_root,
            memory_root,
            locale,
            config,
            "; ".join(item["summary"] for item in blocking_rows),
            ["adp-status-sync", "adp-state-audit"],
            findings=findings,
        )

    stale_issues = compare_audit_fingerprints(project_root, audit, source_fingerprints, baseline_path, row_sources, config)
    if stale_issues:
        return blocked_result(
            project_root,
            memory_root,
            locale,
            config,
            "; ".join(stale_issues),
            ["adp-state-audit"],
        )
    for key, value in audit.get("source_fingerprints", {}).items():
        normalized_key = normalize_path_key(str(key))
        if any(normalize_path_key(existing) == normalized_key for existing in source_fingerprints):
            continue
        source_fingerprints[normalized_key] = normalize_hash(value)

    signals: list[dict[str, Any]] = []
    if args.signals_json:
        signal_path = Path(args.signals_json).expanduser().resolve()
        signals = validate_signals(load_json(signal_path), baseline)
        add_file_source(project_root, signal_path, "status-signals", source_inventory, source_fingerprints)
        add_signal_sources(project_root, signals, source_inventory, source_fingerprints)
        signals, signal_findings = filter_current_signals(
            signals,
            as_of,
            int(config.get("values", {}).get("status_stale_after_days", 7)),
        )
        findings.extend(signal_findings)

    previous = resolve_previous_snapshot(memory_root, args.previous_snapshot, period, exclude_id=None)
    model = compute_model(
        project_root=project_root,
        memory_root=memory_root,
        baseline=baseline,
        rows=rows,
        signals=signals,
        audit=audit,
        config=config,
        locale=locale,
        as_of=as_of,
        period=period,
        generated_at=generated_at,
        findings=findings,
        source_inventory=source_inventory,
        source_fingerprints=source_fingerprints,
        previous=previous,
        config_module=config_module,
    )
    catalog_fingerprint = file_sha256(locale_catalog_path)
    model["title"] = config_module.message("status.title", locale)
    model_message_keys = [
        "status.title",
        f"enum.program_status.{model['overall_status']}",
        f"enum.report_confidence.{model['report_confidence']}",
        *model["confidence_reason_keys"],
        model["progress"]["reason_key"],
        *(
            f"enum.program_status.{item['status']}"
            for item in [
                *model["gates"],
                *model["milestones"],
                *model["signals"],
                model["project"]["target_assessment"],
            ]
        ),
    ]
    model["render_contract"] = render_contract_for_keys(
        "adp-program-status-json",
        model_message_keys,
        locale,
        catalog_fingerprint,
        config_module,
    )

    snapshot_path = memory_root / "snapshots/program-status" / f"{model['snapshot_id']}.json"
    if snapshot_path.is_file():
        stored = load_json(snapshot_path)
        verify_existing_snapshot(stored, model)
        model = stored
        reused = True
    else:
        reused = False

    canonical_outputs = output_paths(memory_root, model["snapshot_id"])
    outputs = staging_output_paths(memory_root, model["snapshot_id"]) if args.headless else canonical_outputs
    if not args.dry_run:
        persist_outputs(outputs, model, locale, config_module)
    result = {
        "ok": True,
        "status": "degraded" if model["report_confidence"] in {"low", "unknown"} else "generated",
        "mode": "generate",
        "dry_run": bool(args.dry_run),
        "snapshot_reused": reused,
        "snapshot_id": model["snapshot_id"],
        "overall_status": model["overall_status"],
        "report_confidence": model["report_confidence"],
        "baseline_revision": model["baseline_revision"],
        "input_audit_id": model["input_audit_id"],
        "as_of": model["as_of"],
        "period_delta": model["period_delta"],
        "progress_schema_version": model["progress"]["progress_schema_version"],
        "progress_measurement_status": model["progress"]["measurement_status"],
        "locale": locale,
        "fallbacks": config.get("fallbacks", []),
        "warnings": config.get("warnings", []),
        "outputs": {} if args.dry_run else {key: str(path) for key, path in outputs.items()},
        "planned_outputs": {key: str(path) for key, path in canonical_outputs.items()} if args.dry_run or args.headless else {},
        "publication_pending": bool(args.headless and not args.dry_run),
        "recommended_workflows": recovery_workflows(model),
    }
    return result


def compute_model(
    *,
    project_root: Path,
    memory_root: Path,
    baseline: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    signals: list[dict[str, Any]],
    audit: dict[str, Any],
    config: dict[str, Any],
    locale: str,
    as_of: date,
    period: dict[str, str],
    generated_at: datetime,
    findings: list[dict[str, Any]],
    source_inventory: list[dict[str, Any]],
    source_fingerprints: dict[str, str],
    previous: dict[str, Any] | None,
    config_module: Any,
) -> dict[str, Any]:
    critical_ids = set(str(value) for value in baseline.get("critical_path", []))
    milestone_signals = index_target_signals(signals, "milestone")
    gate_signals = index_target_signals(signals, "gate")
    milestones = [
        assess_milestone(
            item,
            rows.get(str(item["id"])),
            milestone_signals.get(str(item["id"]), []),
            baseline,
            critical_ids,
            as_of,
            locale,
            config_module,
            findings,
            audit,
        )
        for item in baseline.get("milestones", [])
    ]
    gates = [
        assess_gate(item, gate_signals.get(str(item["id"]), []), baseline, critical_ids, as_of, locale, config_module)
        for item in baseline.get("gates", [])
    ]
    project_target = assess_project_target(baseline, signals, as_of, locale, config_module)
    standalone_signals = [signal_constraint(item, locale, config_module) for item in signals if item["constraint_type"] in {"dependency", "readiness"}]
    constraints = [*gates, *milestones, project_target, *standalone_signals]
    overall_status, overall_rule = overall_judgment(constraints)
    confidence, confidence_reason_keys = compute_confidence(audit, constraints, config)
    variances = sorted_variances(constraints)
    rule_ids = sorted({overall_rule, *(str(item["rule_id"]) for item in constraints)})
    inventory = sorted(unique_dicts(source_inventory, "path"), key=lambda item: (str(item.get("type")), str(item.get("path"))))
    fingerprints = dict(sorted(source_fingerprints.items()))
    progress = build_progress_projection(
        baseline=baseline,
        assessed_milestones=milestones,
        assessed_gates=gates,
        rows=rows,
        audit=audit,
        as_of=as_of,
        reporting_period=period,
        source_fingerprints=fingerprints,
        previous_snapshot=previous,
    )
    snapshot_id = stable_snapshot_id(period, as_of, int(baseline["revision"]), fingerprints, locale, previous)
    flow_state = build_flow_state(
        baseline=baseline,
        assessed_milestones=milestones,
        assessed_gates=gates,
        snapshot_id=snapshot_id,
        as_of=as_of,
    )
    model: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of": as_of.isoformat(),
        "reporting_period": period,
        "baseline_revision": int(baseline["revision"]),
        "baseline_id": baseline["baseline_id"],
        "source_inventory": inventory,
        "source_fingerprints": fingerprints,
        "input_audit_id": audit["input_audit_id"],
        "input_audit_disposition": audit.get("execution_disposition", "ready"),
        "generator_version": GENERATOR_VERSION,
        "locale": locale,
        "locale_fallback": "document_output_language" in config.get("fallbacks", []),
        "scenario": str(audit.get("scenario") or "global"),
        "overall_status": overall_status,
        "overall_status_label": config_module.display_label("program_status", overall_status, locale),
        "overall_rule_id": overall_rule,
        "report_confidence": confidence,
        "report_confidence_label": config_module.display_label("report_confidence", confidence, locale),
        "confidence_reason_keys": confidence_reason_keys,
        "confidence_reasons": [config_module.message(key, locale) for key in confidence_reason_keys],
        "rule_ids": rule_ids,
        "project": {
            "name": baseline["project"]["name"],
            "owner": baseline["project"]["owner"],
            "target_date": baseline["project"]["target_date"],
            "target_assessment": project_target,
        },
        "progress": progress,
        "flow_state": flow_state,
        "progress_reason_label": config_module.message(progress["reason_key"], locale),
        "milestones": milestones,
        "gates": gates,
        "critical_path": [item for item in constraints if item.get("critical")],
        "signals": standalone_signals,
        "variances": variances,
        "findings": findings,
        "audit_summary": audit_summary(audit),
        "period_delta": {},
    }
    model["period_delta"] = compare_period(previous, model)
    return model


def build_flow_state(
    *,
    baseline: dict[str, Any],
    assessed_milestones: list[dict[str, Any]],
    assessed_gates: list[dict[str, Any]],
    snapshot_id: str,
    as_of: date,
) -> dict[str, Any]:
    assessed = {str(item["id"]): item for item in [*assessed_gates, *assessed_milestones]}
    baseline_items = {
        str(item["id"]): item
        for collection in ("gates", "milestones")
        for item in baseline.get(collection, [])
        if isinstance(item, dict) and item.get("id")
    }
    if set(assessed) != set(baseline_items):
        raise ContractError("flow state requires one assessed record for every baseline milestone and gate")
    timestamp = datetime.combine(as_of, time(23, 59, 59), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    executions: dict[str, str] = {}
    for node_id, item in assessed.items():
        source_status = str(item.get("source_status") or "planned")
        if item.get("actual_date"):
            executions[node_id] = "complete"
        elif source_status == "in-progress":
            executions[node_id] = "in-progress"
        else:
            executions[node_id] = "planned"

    predecessors: dict[str, list[tuple[str, str]]] = {node_id: [] for node_id in baseline_items}
    for target, item in baseline_items.items():
        for supplied in item.get("dependencies", []) if isinstance(item.get("dependencies"), list) else []:
            if isinstance(supplied, str):
                predecessors[target].append((supplied, "dependency"))
            elif isinstance(supplied, dict):
                predecessors[target].append((str(supplied.get("predecessor") or ""), str(supplied.get("relationship_type") or "")))
    changed = True
    while changed:
        changed = False
        for node_id in sorted(executions):
            if executions[node_id] != "planned":
                continue
            blocking = [(pred, edge_type) for pred, edge_type in predecessors[node_id] if edge_type not in {"informational", "rework", "conditional"}]
            has_unconfirmed_branch = any(edge_type in {"conditional", "rework"} for _, edge_type in predecessors[node_id])
            if has_unconfirmed_branch:
                continue
            if not blocking or all(executions.get(pred) == "complete" for pred, _ in blocking):
                executions[node_id] = "ready"
                changed = True

    node_states: list[dict[str, Any]] = []
    for node_id in sorted(assessed):
        assessed_item = assessed[node_id]
        canonical_assessment = {key: value for key, value in assessed_item.items() if key != "status_label"}
        source_fingerprint = "sha256:" + hashlib.sha256(canonical_bytes(canonical_assessment)).hexdigest()
        source = {
            "artifact_id": f"PROGRAM-STATUS-R{baseline['revision']}",
            "artifact_path": "views/program-status.json",
            "field": f"flow_state.{node_id}",
            "source_fingerprint": source_fingerprint,
        }
        execution = executions[node_id]
        if execution == "complete":
            execution_rule = "EXEC-ACTUAL-EVIDENCE"
        elif execution == "in-progress":
            execution_rule = "EXEC-STARTED"
        elif execution == "ready":
            execution_rule = "EXEC-PREDECESSORS-SATISFIED"
        else:
            execution_rule = "EXEC-PLANNED"
        source_status = str(assessed_item.get("source_status") or "")
        status = str(assessed_item.get("status") or "indeterminate")
        if source_status == "blocked":
            health, health_rule = "blocked", "HEALTH-SOURCE-BLOCKED"
        elif status in {"at-risk", "off-plan"}:
            health, health_rule = "at-risk", "HEALTH-PLAN-RISK"
        elif status == "on-plan":
            health, health_rule = "on-plan", "HEALTH-ON-PLAN"
        else:
            health, health_rule = "indeterminate", "HEALTH-INDETERMINATE"
        node_states.append(
            {
                "node_id": node_id,
                "baseline_revision": int(baseline["revision"]),
                "evaluated_at": timestamp,
                "execution": {"value": execution, "rule_id": execution_rule, "sources": [source]},
                "health": {"value": health, "rule_id": health_rule, "sources": [source]},
            }
        )
    return {
        "flow_state_schema_version": "1.0.0",
        "baseline_id": str(baseline["baseline_id"]),
        "baseline_revision": int(baseline["revision"]),
        "as_of": timestamp,
        "node_states": node_states,
        "compatibility": {"strategy": "version-required", "migration_error_code": "ADP-FLOW-STATE-MIGRATION-REQUIRED"},
    }


def assess_milestone(
    item: dict[str, Any],
    row: dict[str, Any] | None,
    signals: list[dict[str, Any]],
    baseline: dict[str, Any],
    critical_ids: set[str],
    as_of: date,
    locale: str,
    config_module: Any,
    findings: list[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, Any]:
    planned = parse_date(str(item["planned_date"]), f"milestone {item['id']} planned date")
    tolerance = item.get("tolerance_days", baseline.get("default_tolerance_days", 0))
    tolerance = int(tolerance)
    forecast = optional_date((row or {}).get("forecast"), f"milestone {item['id']} forecast")
    actual = optional_date((row or {}).get("actual"), f"milestone {item['id']} actual")
    excluded_actual = actual
    actual_exclusion_reason: str | None = None
    if actual and actual > as_of:
        actual_exclusion_reason = "future-actual"
    elif actual and not str(item.get("completion_criteria") or "").strip():
        actual_exclusion_reason = "missing-completion-criteria"
    elif actual and not (row or {}).get("source_references"):
        actual_exclusion_reason = "missing-evidence"
    elif actual and normalize_path_key(str((row or {}).get("wdr_path") or "")) not in {
        normalize_path_key(str(path)) for path in audit.get("source_fingerprints", {})
    }:
        actual_exclusion_reason = "unaudited-actual"
    if actual_exclusion_reason:
        findings.append(
            program_finding(
                "status.future_actual" if actual_exclusion_reason == "future-actual" else "status.actual_ineligible",
                "warning",
                f"milestone {item['id']} actual date {actual.isoformat()} was excluded by {actual_exclusion_reason}",
                str(item["id"]),
            )
        )
        actual = None
    source_status = normalized_value((row or {}).get("status")) or "planned"
    if source_status not in MILESTONE_STATUSES:
        raise ContractError(f"milestone {item['id']} has unsupported source status {source_status!r}")
    allowed = planned + timedelta(days=tolerance)
    variance: int | None = None
    if actual:
        variance = (actual - planned).days
        status, rule = ("off-plan", "PS-MS-ACTUAL-OVER-TOLERANCE") if actual > allowed else ("on-plan", "PS-MS-ACTUAL-WITHIN-TOLERANCE")
    elif forecast:
        variance = (forecast - planned).days
        if forecast > allowed:
            status, rule = "off-plan", "PS-MS-FORECAST-OVER-TOLERANCE"
        elif forecast > planned:
            status, rule = "at-risk", "PS-MS-FORECAST-WITHIN-TOLERANCE"
        else:
            status, rule = "on-plan", "PS-MS-FORECAST-ON-TIME"
    elif source_status == "blocked":
        status, rule = ("off-plan", "PS-MS-BLOCKED-PAST-TOLERANCE") if as_of > allowed else ("at-risk", "PS-MS-BLOCKED")
    elif source_status == "at-risk":
        status, rule = "at-risk", "PS-MS-SOURCE-AT-RISK"
    elif source_status == "in-progress":
        status, rule = "on-plan", "PS-MS-SOURCE-IN-PROGRESS"
    elif source_status == "done":
        status, rule = "indeterminate", "PS-MS-DONE-WITHOUT-ACTUAL"
    elif as_of > allowed:
        status, rule = "indeterminate", "PS-MS-PAST-DUE-WITHOUT-ACTUAL"
    else:
        status, rule = "on-plan", "PS-MS-FUTURE-ACTUAL-NOT-APPLICABLE"
    status, rule, signal_refs = merge_signals(status, rule, signals)
    source_refs = list((row or {}).get("source_references", [])) + signal_refs + [source_reference(item.get("source"))]
    result = {
        "constraint_type": "milestone",
        "id": item["id"],
        "name": item["name"],
        "workstream_id": item["workstream_id"],
        "critical": item["id"] in critical_ids or bool(item.get("critical_path")),
        "planned_date": planned.isoformat(),
        "forecast_date": forecast.isoformat() if forecast else None,
        "actual_date": actual.isoformat() if actual else None,
        "excluded_future_actual_date": excluded_actual.isoformat() if excluded_actual and actual_exclusion_reason == "future-actual" else None,
        "excluded_actual_date": excluded_actual.isoformat() if excluded_actual and actual_exclusion_reason else None,
        "actual_exclusion_reason": actual_exclusion_reason,
        "tolerance_days": tolerance,
        "variance_days": variance,
        "source_status": source_status,
        "status": status,
        "status_label": config_module.display_label("program_status", status, locale),
        "rule_id": rule,
        "source_references": sorted(set(filter(None, source_refs))),
    }
    return result


def assess_gate(
    item: dict[str, Any],
    signals: list[dict[str, Any]],
    baseline: dict[str, Any],
    critical_ids: set[str],
    as_of: date,
    locale: str,
    config_module: Any,
) -> dict[str, Any]:
    planned = parse_date(str(item["planned_date"]), f"gate {item['id']} planned date")
    tolerance = int(item.get("tolerance_days", baseline.get("default_tolerance_days", 0)))
    allowed = planned + timedelta(days=tolerance)
    if as_of > allowed:
        status, rule = "indeterminate", "PS-GATE-PAST-DUE-WITHOUT-SIGNAL"
    else:
        status, rule = "on-plan", "PS-GATE-FUTURE-ACTUAL-NOT-APPLICABLE"
    status, rule, signal_refs = merge_signals(status, rule, signals)
    return {
        "constraint_type": "gate",
        "id": item["id"],
        "name": item["name"],
        "critical": item["id"] in critical_ids or bool(item.get("critical_path")),
        "planned_date": planned.isoformat(),
        "forecast_date": None,
        "actual_date": None,
        "tolerance_days": tolerance,
        "variance_days": None,
        "source_status": None,
        "status": status,
        "status_label": config_module.display_label("program_status", status, locale),
        "rule_id": rule,
        "source_references": sorted(set([source_reference(item.get("source")), *signal_refs])),
    }


def assess_project_target(
    baseline: dict[str, Any], signals: list[dict[str, Any]], as_of: date, locale: str, config_module: Any
) -> dict[str, Any]:
    project = baseline["project"]
    target = parse_date(str(project["target_date"]), "project target date")
    applicable = [item for item in signals if item["constraint_type"] == "project"]
    if as_of > target:
        status, rule = "indeterminate", "PS-PROJECT-TARGET-PAST-WITHOUT-SIGNAL"
    else:
        status, rule = "on-plan", "PS-PROJECT-TARGET-FUTURE"
    status, rule, refs = merge_signals(status, rule, applicable)
    return {
        "constraint_type": "project",
        "id": "PROJECT-TARGET",
        "name": project["name"],
        "critical": True,
        "planned_date": target.isoformat(),
        "forecast_date": None,
        "actual_date": None,
        "tolerance_days": 0,
        "variance_days": None,
        "source_status": None,
        "status": status,
        "status_label": config_module.display_label("program_status", status, locale),
        "rule_id": rule,
        "source_references": sorted(set([source_reference(project.get("source")), *refs])),
    }


def signal_constraint(signal: dict[str, Any], locale: str, config_module: Any) -> dict[str, Any]:
    return {
        "constraint_type": signal["constraint_type"],
        "id": signal.get("constraint_id") or signal["id"],
        "name": signal.get("summary") or signal["id"],
        "critical": signal["critical"],
        "planned_date": None,
        "forecast_date": None,
        "actual_date": None,
        "tolerance_days": None,
        "variance_days": None,
        "source_status": signal["status"],
        "status": signal["status"],
        "status_label": config_module.display_label("program_status", signal["status"], locale),
        "rule_id": "PS-SOURCE-BACKED-SIGNAL",
        "source_references": [signal["source"]["reference"]],
        "signal_id": signal["id"],
    }


def merge_signals(status: str, rule: str, signals: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    refs: list[str] = []
    selected_status: str | None = None
    for signal in sorted(signals, key=lambda item: item["id"]):
        refs.append(signal["source"]["reference"])
        if selected_status is None or STATUS_RANK[signal["status"]] > STATUS_RANK[selected_status]:
            selected_status = signal["status"]
    if selected_status is not None and (
        status == "indeterminate" or STATUS_RANK[selected_status] > STATUS_RANK[status]
    ):
        status = selected_status
        rule = "PS-SOURCE-BACKED-SIGNAL"
    return status, rule, refs


def overall_judgment(constraints: list[dict[str, Any]]) -> tuple[str, str]:
    critical = [item for item in constraints if item.get("critical")]
    considered = critical or constraints
    for status, rule in [
        ("off-plan", "PS-OVERALL-CRITICAL-OFF-PLAN"),
        ("at-risk", "PS-OVERALL-CRITICAL-AT-RISK"),
        ("indeterminate", "PS-OVERALL-CRITICAL-INDETERMINATE"),
    ]:
        if any(item["status"] == status for item in considered):
            return status, rule
    noncritical_problem = any(item["status"] in {"off-plan", "at-risk"} for item in constraints if not item.get("critical"))
    if noncritical_problem:
        return "at-risk", "PS-OVERALL-NONCRITICAL-VARIANCE"
    if not considered:
        return "indeterminate", "PS-OVERALL-NO-APPLICABLE-CONSTRAINTS"
    return "on-plan", "PS-OVERALL-CRITICAL-ON-PLAN"


def compute_confidence(
    audit: dict[str, Any], constraints: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    audit_confidence = str(audit.get("report_confidence") or "unknown")
    if audit_confidence not in CONFIDENCE_RANK:
        audit_confidence = "unknown"
    confidence = audit_confidence
    if audit.get("execution_disposition") == "degraded":
        confidence = lower_confidence(confidence, "low")
        reasons.append("status.confidence_reason.audit_degraded")
    critical = [item for item in constraints if item.get("critical")]
    if not critical:
        confidence = "unknown"
        reasons.append("status.confidence_reason.no_critical_constraints")
    elif any(item["status"] == "indeterminate" for item in critical):
        confidence = lower_confidence(confidence, "low")
        reasons.append("status.confidence_reason.critical_indeterminate")
    elif any(item["rule_id"].endswith("ACTUAL-NOT-APPLICABLE") for item in critical):
        confidence = lower_confidence(confidence, "medium")
        reasons.append("status.confidence_reason.future_without_forecast")
    if "document_output_language" in config.get("fallbacks", []):
        confidence = lower_confidence(confidence, "low")
        reasons.append("status.confidence_reason.locale_fallback")
    if not reasons:
        reasons.append("status.confidence_reason.supported")
    return confidence, reasons


def lower_confidence(current: str, ceiling: str) -> str:
    current_rank = CONFIDENCE_RANK.get(current, 0)
    return current if current_rank <= CONFIDENCE_RANK[ceiling] else ceiling


def sorted_variances(constraints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [item for item in constraints if item["status"] != "on-plan" or item.get("variance_days") not in {None, 0}]
    return sorted(
        selected,
        key=lambda item: (
            not bool(item.get("critical")),
            -STATUS_RANK[item["status"]],
            -(item.get("variance_days") or 0),
            str(item["id"]),
        ),
    )


def compare_period(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return {
            "comparison_status": "no-previous-snapshot",
            "previous_snapshot_id": None,
            "overall_change": None,
            "new_items": [],
            "completed": [],
            "worsened": [],
            "improved": [],
            "changed": [],
        }
    before = constraint_index(previous)
    after = constraint_index(current)
    new_items = sorted(set(after) - set(before))
    completed: list[str] = []
    worsened: list[str] = []
    improved: list[str] = []
    changed: list[str] = []
    for key in sorted(set(before) & set(after)):
        old = before[key]
        new = after[key]
        if not old.get("actual_date") and new.get("actual_date"):
            completed.append(key)
        if old.get("status") != new.get("status"):
            changed.append(key)
            if STATUS_RANK[new["status"]] > STATUS_RANK[old["status"]]:
                worsened.append(key)
            else:
                improved.append(key)
    overall_change = None
    if previous.get("overall_status") != current.get("overall_status"):
        overall_change = {"from": previous.get("overall_status"), "to": current.get("overall_status")}
    return {
        "comparison_status": "compared",
        "previous_snapshot_id": previous.get("snapshot_id"),
        "overall_change": overall_change,
        "new_items": new_items,
        "completed": completed,
        "worsened": worsened,
        "improved": improved,
        "changed": changed,
    }


def constraint_index(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group in ("gates", "milestones", "signals"):
        for item in model.get(group, []) if isinstance(model.get(group), list) else []:
            result[f"{item.get('constraint_type', group[:-1])}:{item.get('id')}"] = item
    target = model.get("project", {}).get("target_assessment")
    if isinstance(target, dict):
        result["project:PROJECT-TARGET"] = target
    return result


def collect_milestone_rows(
    project_root: Path, memory_root: Path, baseline: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[Path], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    sources: list[Path] = []
    findings: list[dict[str, Any]] = []
    workstreams = sorted({str(item["workstream_id"]) for item in baseline.get("milestones", [])})
    revision = int(baseline["revision"])
    for workstream_id in workstreams:
        path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
        if not path.is_file():
            findings.append(program_finding("actual.wdr_missing", "warning", f"workstream {workstream_id} delivery record is missing", str(path)))
            continue
        sources.append(path)
        for row in parse_roadmap_table(path.read_text(encoding="utf-8-sig")):
            milestone_id = row_value(row, "milestone id")
            if is_missing(milestone_id):
                continue
            if milestone_id in rows:
                findings.append(program_finding("actual.duplicate", "blocked", f"milestone {milestone_id} occurs in more than one WDR row", relative_path(project_root, path)))
                continue
            row_revision = row_value(row, "baseline revision")
            if is_missing(row_revision) or not row_revision.isdigit() or int(row_revision) != revision:
                findings.append(program_finding("actual.revision_mismatch", "blocked", f"milestone {milestone_id} actual row does not match baseline revision {revision}", relative_path(project_root, path)))
                continue
            rows[milestone_id] = {
                "status": row_value(row, "status"),
                "forecast": row_value(row, "forecast"),
                "actual": row_value(row, "actual"),
                "source_references": split_references(row_value(row, "source")),
                "wdr_path": relative_path(project_root, path),
                "correction_id": row_value(row, "correction id"),
                "correction_kind": row_value(row, "correction kind"),
                "correction_audit_id": row_value(row, "correction audit id"),
                "correction_source": row_value(row, "correction source"),
                "previous_actual": row_value(row, "previous actual"),
            }
    return rows, sources, findings


def parse_roadmap_table(markdown: str) -> list[dict[str, str]]:
    lines = markdown.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip().casefold() == "## roadmap"), None)
    if start is None:
        return []
    header_index = next((index for index in range(start + 1, len(lines)) if lines[index].strip().startswith("|")), None)
    if header_index is None or header_index + 1 >= len(lines):
        return []
    headers = [normalize_header(value) for value in split_markdown_row(lines[header_index])]
    rows: list[dict[str, str]] = []
    for line in lines[header_index + 2 :]:
        if line.startswith("## "):
            break
        if not line.strip().startswith("|"):
            if rows and line.strip():
                break
            continue
        values = split_markdown_row(line)
        if len(values) != len(headers):
            raise ContractError("Roadmap table contains a malformed row")
        rows.append(dict(zip(headers, values)))
    return rows


def split_markdown_row(line: str) -> list[str]:
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|") and not content.endswith("\\|"):
        content = content[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", content)]


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def row_value(row: dict[str, str], name: str) -> str:
    return str(row.get(normalize_header(name), "")).strip()


def validate_audit(
    audit: dict[str, Any],
    baseline: dict[str, Any],
    as_of: date,
    locale: str,
    locale_fallback: bool,
) -> list[str]:
    issues: list[str] = []
    if not isinstance(audit, dict):
        return ["input audit must be a JSON object"]
    if not audit.get("input_audit_id"):
        issues.append("input audit is missing input_audit_id")
    if audit.get("execution_disposition") not in {"ready", "degraded", "blocked"}:
        issues.append("input audit has no canonical execution_disposition")
    revision = audit.get("baseline_revision")
    if revision is not None and revision != baseline.get("revision"):
        issues.append(f"input audit baseline revision {revision!r} does not match {baseline.get('revision')!r}")
    if audit.get("as_of") and audit.get("as_of") != as_of.isoformat():
        issues.append(f"input audit as_of {audit.get('as_of')!r} does not match {as_of.isoformat()!r}")
    if not isinstance(audit.get("source_fingerprints"), dict):
        issues.append("input audit is missing source_fingerprints")
    if audit.get("locale") != locale:
        issues.append(f"input audit locale {audit.get('locale')!r} does not match effective locale {locale!r}")
    if audit.get("locale_fallback") != locale_fallback:
        issues.append("input audit locale_fallback does not match effective configuration")
    return issues


def compare_audit_fingerprints(
    project_root: Path,
    audit: dict[str, Any],
    current: dict[str, str],
    baseline_path: Path,
    row_sources: list[Path],
    config: dict[str, Any],
) -> list[str]:
    audited = {normalize_path_key(str(key)): normalize_hash(value) for key, value in audit.get("source_fingerprints", {}).items()}
    required_paths = [baseline_path, *row_sources]
    for source in config.get("sources_checked", []):
        path = Path(str(source.get("path", "")))
        if path.is_file():
            required_paths.append(path)
    issues: list[str] = []
    for path in required_paths:
        project_key = normalize_path_key(relative_path(project_root, path))
        current_hash = normalize_hash(current.get(relative_path(project_root, path), ""))
        candidates = audit_path_candidates(project_key)
        audited_key = next((key for key in candidates if key in audited), None)
        if audited_key is None:
            issues.append(f"input audit has no fingerprint for {project_key}")
        elif audited[audited_key] != current_hash:
            issues.append(f"input audit fingerprint is stale for {project_key}")
    return sorted(set(issues))


def audit_path_candidates(project_relative: str) -> list[str]:
    candidates = [project_relative]
    marker = "_bmad-output/adp/memory/"
    if marker in project_relative:
        candidates.append(project_relative.split(marker, 1)[1])
    return candidates


def validate_signals(payload: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") != "1.0" or not isinstance(payload.get("signals"), list):
        raise ContractError("status signals require schema_version 1.0 and a signals array")
    baseline_revision = int(baseline["revision"])
    target_ids = {
        "gate": {str(item["id"]) for item in baseline.get("gates", [])},
        "milestone": {str(item["id"]) for item in baseline.get("milestones", [])},
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload["signals"]):
        if not isinstance(raw, dict):
            raise ContractError(f"signals[{index}] must be an object")
        signal_id = str(raw.get("id") or "").strip()
        kind = str(raw.get("constraint_type") or "").strip()
        status = str(raw.get("status") or "").strip()
        source = raw.get("source")
        if not signal_id or signal_id in seen:
            raise ContractError(f"signals[{index}].id must be non-empty and unique")
        if kind not in SIGNAL_TYPES:
            raise ContractError(f"signals[{index}].constraint_type must be one of {', '.join(sorted(SIGNAL_TYPES))}")
        if status not in STATUS_VALUES:
            raise ContractError(f"signals[{index}].status must be canonical")
        if not isinstance(raw.get("critical"), bool):
            raise ContractError(f"signals[{index}].critical must be boolean")
        if not isinstance(source, dict) or not str(source.get("reference") or "").strip():
            raise ContractError(f"signals[{index}].source.reference is required")
        if kind in {"gate", "milestone"} and not str(raw.get("constraint_id") or "").strip():
            raise ContractError(f"signals[{index}].constraint_id is required for {kind}")
        if kind in target_ids and str(raw.get("constraint_id") or "").strip() not in target_ids[kind]:
            raise ContractError(f"signal {signal_id} references unknown baseline {kind} {raw.get('constraint_id')!r}")
        if raw.get("baseline_revision") not in {None, baseline_revision}:
            raise ContractError(f"signal {signal_id} baseline_revision does not match {baseline_revision}")
        result.append(
            {
                "id": signal_id,
                "constraint_type": kind,
                "constraint_id": str(raw.get("constraint_id") or "").strip() or None,
                "status": status,
                "critical": raw["critical"],
                "summary": str(raw.get("summary") or signal_id).strip(),
                "source": {"reference": str(source["reference"]).strip(), "type": str(source.get("type") or "explicit-signal")},
                "observed_at": raw.get("observed_at"),
            }
        )
        seen.add(signal_id)
    return result


def filter_current_signals(
    signals: list[dict[str, Any]], as_of: date, stale_after_days: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for signal in signals:
        observed_at = signal.get("observed_at")
        if not observed_at:
            current.append(signal)
            continue
        observed_date = parse_observed_date(str(observed_at), str(signal["id"]))
        if observed_date > as_of:
            findings.append(
                program_finding(
                    "status.signal_future",
                    "warning",
                    f"signal {signal['id']} observed_at {observed_date.isoformat()} is after as-of {as_of.isoformat()} and was excluded",
                    str(signal["id"]),
                )
            )
        elif (as_of - observed_date).days > stale_after_days:
            findings.append(
                program_finding(
                    "status.signal_stale",
                    "warning",
                    f"signal {signal['id']} is older than status_stale_after_days={stale_after_days} and was excluded",
                    str(signal["id"]),
                )
            )
        else:
            current.append(signal)
    return current, findings


def parse_observed_date(value: str, signal_id: str) -> date:
    try:
        if "T" not in value:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone is required")
        return parsed.astimezone(timezone.utc).date()
    except ValueError as exc:
        raise ContractError(f"signal {signal_id} observed_at must be an ISO date or timezone-aware timestamp") from exc


def index_target_signals(signals: list[dict[str, Any]], kind: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        if signal["constraint_type"] == kind:
            result.setdefault(str(signal["constraint_id"]), []).append(signal)
    return result


def resolve_period(args: argparse.Namespace, config: dict[str, Any], as_of: date) -> dict[str, str]:
    end = parse_date(args.period_end or as_of.isoformat(), "period end")
    cadence = str(config.get("values", {}).get("default_reporting_cadence") or "weekly")
    if args.period_start:
        start = parse_date(args.period_start, "period start")
    elif cadence == "weekly":
        start = end - timedelta(days=6)
    elif cadence == "biweekly":
        start = end - timedelta(days=13)
    else:
        raise ContractError("--period-start is required when default_reporting_cadence is custom")
    if start > end:
        raise ContractError("period start cannot be after period end")
    if end > as_of:
        raise ContractError("period end cannot be after as-of")
    return {"start": start.isoformat(), "end": end.isoformat(), "cadence": cadence}


def resolve_previous_snapshot(
    memory_root: Path, raw_path: str | None, period: dict[str, str], exclude_id: str | None
) -> dict[str, Any] | None:
    if raw_path:
        previous = load_json(Path(raw_path).expanduser().resolve())
        if previous.get("snapshot_id") == exclude_id:
            return None
        return previous
    folder = memory_root / "snapshots/program-status"
    if not folder.is_dir():
        return None
    candidates: list[dict[str, Any]] = []
    for path in folder.glob("ps-*.json"):
        if not SNAPSHOT_FILE.match(path.name):
            continue
        try:
            item = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if item.get("snapshot_id") == exclude_id:
            continue
        item_period = item.get("reporting_period", {})
        if str(item_period.get("end") or "") < period["end"]:
            candidates.append(item)
    return max(candidates, key=lambda item: (str(item.get("reporting_period", {}).get("end", "")), str(item.get("as_of", "")), str(item.get("snapshot_id", ""))), default=None)


def stable_snapshot_id(
    period: dict[str, str],
    as_of: date,
    revision: int,
    fingerprints: dict[str, str],
    locale: str,
    previous: dict[str, Any] | None,
) -> str:
    payload = {
        "reporting_period": period,
        "as_of": as_of.isoformat(),
        "baseline_revision": revision,
        "source_fingerprints": fingerprints,
        "locale": locale,
        "generator_version": GENERATOR_VERSION,
        "previous_snapshot_fingerprint": hashlib.sha256(canonical_bytes(previous)).hexdigest() if previous else None,
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:16]
    return f"ps-{digest}"


def verify_existing_snapshot(stored: dict[str, Any], candidate: dict[str, Any]) -> None:
    if stored.get("snapshot_id") != candidate.get("snapshot_id"):
        raise ContractError("existing snapshot path does not match its snapshot_id")
    left = dict(stored)
    right = dict(candidate)
    left.pop("generated_at", None)
    right.pop("generated_at", None)
    left.pop("period_delta", None)
    right.pop("period_delta", None)
    if canonical_bytes(left) != canonical_bytes(right):
        raise ContractError("immutable snapshot collision: stable ID exists with different canonical content")


def output_paths(memory_root: Path, snapshot_id: str) -> dict[str, Path]:
    return {
        "snapshot": memory_root / "snapshots/program-status" / f"{snapshot_id}.json",
        "latest": memory_root / "snapshots/program-status/latest.json",
        "program_status_json": memory_root / "views/program-status.json",
        "program_status_markdown": memory_root / "views/program-status.md",
        "weekly_report": memory_root / "views/weekly-report.md",
        "project_lead": memory_root / "views/project-lead.md",
    }


def staging_output_paths(memory_root: Path, snapshot_id: str) -> dict[str, Path]:
    root = memory_root / "audits/program-status-staging" / snapshot_id
    return {
        "snapshot": root / f"{snapshot_id}.json",
        "latest": root / "latest.json",
        "program_status_json": root / "program-status.json",
        "program_status_markdown": root / "program-status.md",
        "weekly_report": root / "weekly-report.md",
        "project_lead": root / "project-lead.md",
    }


def persist_outputs(outputs: dict[str, Path], model: dict[str, Any], locale: str, config_module: Any) -> None:
    snapshot_text = canonical_text(model)
    create_immutable(outputs["snapshot"], snapshot_text, model)
    latest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": model["snapshot_id"],
        "snapshot_path": f"snapshots/program-status/{model['snapshot_id']}.json",
        "as_of": model["as_of"],
        "reporting_period": model["reporting_period"],
        "baseline_revision": model["baseline_revision"],
    }
    atomic_write(outputs["latest"], canonical_text(latest))
    atomic_write(outputs["program_status_json"], snapshot_text)
    atomic_write(outputs["program_status_markdown"], render_program_status(model, locale, config_module))
    atomic_write(outputs["weekly_report"], render_weekly_report(model, locale, config_module))
    atomic_write(outputs["project_lead"], render_project_lead(model, locale, config_module))


def publish_staged_outputs(operation: dict[str, Any]) -> dict[str, str]:
    staged = {key: Path(value) for key, value in operation.get("outputs", {}).items()}
    canonical = {key: Path(value) for key, value in operation.get("planned_outputs", {}).items()}
    required = {"snapshot", "latest", "program_status_json", "program_status_markdown", "weekly_report", "project_lead"}
    if set(staged) != required or set(canonical) != required:
        raise ContractError("headless publication requires complete staged and canonical output maps")
    missing = [str(path) for path in staged.values() if not path.is_file()]
    if missing:
        raise ContractError("headless publication is missing staged artifacts: " + ", ".join(missing))

    model = load_json(staged["snapshot"])
    create_immutable(canonical["snapshot"], staged["snapshot"].read_text(encoding="utf-8"), model)
    for key in ["program_status_json", "program_status_markdown", "weekly_report", "project_lead", "latest"]:
        atomic_write(canonical[key], staged[key].read_text(encoding="utf-8"))
    return {key: str(path) for key, path in canonical.items()}


def create_immutable(path: Path, text: str, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(handle.fileno(), 0o644)
            else:
                os.chmod(temp_path, 0o644)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            verify_existing_snapshot(load_json(path), model)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def render_program_status(model: dict[str, Any], locale: str, config_module: Any) -> str:
    m = RenderCatalog(config_module, locale, model["render_contract"]["catalog_fingerprint"])
    status_label = m(f"enum.program_status.{model['overall_status']}")
    confidence_label = m(f"enum.report_confidence.{model['report_confidence']}")
    lines = [
        f"# {m('status.title')}",
        "",
        f"- {m('status.overall')}: **{status_label}** (`{model['overall_status']}`)",
        f"- {m('status.confidence')}: **{confidence_label}** (`{model['report_confidence']}`)",
        f"- {m('status.as_of')}: {config_module.format_date(model['as_of'], locale)}",
        f"- {m('status.period')}: {config_module.format_date(model['reporting_period']['start'], locale)} - {config_module.format_date(model['reporting_period']['end'], locale)}",
        f"- {m('field.revision')}: {model['baseline_revision']}",
        f"- {m('status.snapshot_id')}: `{model['snapshot_id']}`",
        "",
        f"## {m('status.executive_summary')}",
        "",
        m(f"status.summary.{model['overall_status']}"),
        "",
        f"## {m('status.progress_title')}",
        "",
        *render_progress(model["progress"], m),
        "",
        f"## {m('status.critical_constraints')}",
        "",
    ]
    lines.extend(render_constraint_table(model["critical_path"], locale, config_module, m))
    lines.extend(["", f"## {m('status.top_variances')}", ""])
    lines.extend(render_constraint_table(model["variances"][:10], locale, config_module, m))
    lines.extend(["", f"## {m('status.period_changes')}", ""])
    lines.extend(render_delta(model["period_delta"], m))
    lines.extend(["", f"## {m('status.confidence_basis')}", ""])
    lines.extend(f"- {m(key)}" for key in model["confidence_reason_keys"])
    lines.extend(["", f"## {m('status.lineage')}", ""])
    lines.extend(
        [
            f"- {m('status.input_audit_id')}: `{model['input_audit_id']}`",
            f"- {m('status.generator_version')}: `{model['generator_version']}`",
            f"- {m('status.rule_ids')}: {', '.join(f'`{value}`' for value in model['rule_ids'])}",
        ]
    )
    append_artifact_metadata(lines, model, m.contract("adp-program-status-markdown"))
    return "\n".join(lines).rstrip() + "\n"


def render_weekly_report(model: dict[str, Any], locale: str, config_module: Any) -> str:
    m = RenderCatalog(config_module, locale, model["render_contract"]["catalog_fingerprint"])
    delta = model["period_delta"]
    summary = m(f"status.summary.{model['overall_status']}")
    status_label = m(f"enum.program_status.{model['overall_status']}")
    confidence_label = m(f"enum.report_confidence.{model['report_confidence']}")
    lines = [
        f"# {m('status.weekly_title')}",
        "",
        f"## {m('status.executive_summary')}",
        "",
        f"**{status_label}** / {confidence_label}. {summary}",
        "",
        f"## {m('status.progress_title')}",
        "",
        *render_progress(model["progress"], m),
        "",
        f"## {m('status.period_changes')}",
        "",
        *render_delta(delta, m),
        "",
        f"## {m('status.top_variances')}",
        "",
        *render_constraint_table(model["variances"][:10], locale, config_module, m),
        "",
        f"## {m('status.upcoming')}",
        "",
        *render_constraint_table(upcoming_constraints(model), locale, config_module, m),
        "",
        f"## {m('status.lineage')}",
        "",
        f"- {m('status.snapshot_id')}: `{model['snapshot_id']}`",
        f"- {m('status.input_audit_id')}: `{model['input_audit_id']}`",
    ]
    append_artifact_metadata(lines, model, m.contract("adp-weekly-report-markdown"))
    return "\n".join(lines).rstrip() + "\n"


def render_project_lead(model: dict[str, Any], locale: str, config_module: Any) -> str:
    m = RenderCatalog(config_module, locale, model["render_contract"]["catalog_fingerprint"])
    status_label = m(f"enum.program_status.{model['overall_status']}")
    confidence_label = m(f"enum.report_confidence.{model['report_confidence']}")
    lines = [
        f"# {m('status.project_lead_title')}",
        "",
        f"- {m('status.overall')}: **{status_label}**",
        f"- {m('status.confidence')}: **{confidence_label}**",
        f"- {m('field.owner')}: {model['project']['owner']}",
        f"- {m('field.target_date')}: {config_module.format_date(model['project']['target_date'], locale)}",
        "",
        f"## {m('status.progress_title')}",
        "",
        *render_progress(model["progress"], m),
        "",
        f"## {m('baseline.gates')}",
        "",
        *render_constraint_table(model["gates"], locale, config_module, m),
        "",
        f"## {m('baseline.milestones')}",
        "",
        *render_constraint_table(model["milestones"], locale, config_module, m),
        "",
        f"## {m('status.recovery')}",
        "",
    ]
    workflows = recovery_workflows(model)
    lines.extend(f"- `{workflow}`" for workflow in workflows)
    if not workflows:
        lines.append(f"- {m('status.no_recovery')}")
    lines.extend(["", f"## {m('status.lineage')}", "", f"- {m('status.snapshot_id')}: `{model['snapshot_id']}`", f"- {m('status.baseline_revision')}: `{model['baseline_revision']}`", f"- {m('status.input_audit_id')}: `{model['input_audit_id']}`"])
    append_artifact_metadata(lines, model, m.contract("adp-project-lead-markdown"))
    return "\n".join(lines).rstrip() + "\n"


def append_artifact_metadata(lines: list[str], model: dict[str, Any], render_contract: dict[str, Any]) -> None:
    metadata = {
        "snapshot_id": model["snapshot_id"],
        "generated_at": model["generated_at"],
        "as_of": model["as_of"],
        "reporting_period": model["reporting_period"],
        "report_confidence": model["report_confidence"],
        "scenario": model["scenario"],
        "input_audit_id": model["input_audit_id"],
        "baseline_revision": model["baseline_revision"],
        "source_fingerprints": model["source_fingerprints"],
        "locale": model["locale"],
        "locale_fallback": model["locale_fallback"],
        "render_contract": render_contract,
        "generator_version": model["generator_version"],
        "progress_schema_version": model["progress"]["progress_schema_version"],
        "progress_scope_identity": model["progress"]["scope_identity"],
        "flow_state_schema_version": model["flow_state"]["flow_state_schema_version"],
    }
    lines.extend(
        [
            "",
            "<!-- adp:artifact-metadata:v1 -->",
            "",
            "```json",
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    )


def render_progress(progress: dict[str, Any], m: RenderCatalog) -> list[str]:
    overall = progress["overall"]
    current = overall["current"]
    forecast = overall["forecast_summary"]
    lines = [
        f"- {m('status.progress_schema')}: `{progress['progress_schema_version']}`",
        f"- {m('status.progress_measurement')}: `{progress['measurement_status']}`",
        f"- {m('status.progress_actual')}: {display_progress_value(current['actual_completion_percent'], '%')}",
        f"- {m('status.progress_planned')}: {display_progress_value(current['planned_completion_percent'], '%')}",
        f"- {m('status.progress_gap')}: {display_progress_value(current['completion_gap_pp'], ' pp')}",
        f"- {m('status.progress_forecast')}: {display_progress_value(forecast['forecast_completion_percent'], '%')}",
        f"- {m('status.progress_coverage')}: {display_progress_value(forecast['forecast_coverage_percent'], '%')} (`{forecast['forecast_coverage_status']}`)",
        f"- {m('status.progress_comparability')}: `{overall['comparability']['disposition']}`",
        "",
        f"### {m('status.progress_workstreams')}",
        "",
    ]
    headers = [
        m("field.workstream"),
        m("status.progress_kind"),
        m("status.progress_measurement"),
        m("status.progress_actual"),
        m("status.progress_planned"),
        m("status.progress_gap"),
        m("status.progress_project_weight"),
        m("status.progress_contribution"),
    ]
    rows = []
    for item in progress["by_workstream"]:
        values = item["current"]
        rows.append(
            [
                item["workstream_id"],
                item["progress_kind"],
                item["measurement_status"],
                display_progress_value(values["actual_completion_percent"], "%"),
                display_progress_value(values["planned_completion_percent"], "%"),
                display_progress_value(values["completion_gap_pp"], " pp"),
                display_progress_value(values["project_weight_percent"], "%"),
                display_progress_value(values["completed_contribution_pp"], " pp"),
            ]
        )
    lines.extend(markdown_table(headers, rows))
    return lines


def display_progress_value(value: Any, suffix: str) -> str:
    return "-" if value is None else f"{float(value):.2f}{suffix}"


def render_constraint_table(
    items: list[dict[str, Any]], locale: str, config_module: Any, m: RenderCatalog
) -> list[str]:
    if not items:
        return [m("baseline.no_items")]
    headers = [m("field.id"), m("field.name"), m("status.item_status"), m("field.planned_date"), m("status.forecast"), m("status.actual"), m("status.variance"), m("status.rule")]
    rows: list[list[str]] = []
    for item in items:
        rows.append(
            [
                str(item["id"]),
                str(item["name"]),
                m(f"enum.program_status.{item['status']}"),
                display_date(item.get("planned_date"), locale, config_module),
                display_date(item.get("forecast_date"), locale, config_module),
                display_date(item.get("actual_date"), locale, config_module),
                str(item.get("variance_days")) if item.get("variance_days") is not None else "-",
                str(item["rule_id"]),
            ]
        )
    return markdown_table(headers, rows)


def render_delta(delta: dict[str, Any], m: RenderCatalog) -> list[str]:
    if delta.get("comparison_status") != "compared":
        return [m("status.no_previous")]
    lines: list[str] = []
    change = delta.get("overall_change")
    if change:
        before = m(f"enum.program_status.{change['from']}")
        after = m(f"enum.program_status.{change['to']}")
        lines.append(f"- {m('status.overall_change')}: {before} -> {after}")
    else:
        lines.append(f"- {m('status.overall_change')}: {m('status.unchanged')}")
    for key, label_key in [("worsened", "status.worsened"), ("improved", "status.improved"), ("completed", "status.completed")]:
        values = delta.get(key, [])
        lines.append(f"- {m(label_key)}: {', '.join(values) if values else '-'}")
    return lines


def upcoming_constraints(model: dict[str, Any]) -> list[dict[str, Any]]:
    as_of = parse_date(model["as_of"], "as-of")
    candidates = [item for item in [*model["gates"], *model["milestones"]] if item.get("planned_date") and parse_date(item["planned_date"], "planned") >= as_of]
    return sorted(candidates, key=lambda item: (item["planned_date"], str(item["id"])))[:10]


def markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    def clean(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    return [
        "| " + " | ".join(clean(value) for value in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(clean(value) for value in row) + " |" for row in rows),
    ]


def display_date(value: Any, locale: str, config_module: Any) -> str:
    return config_module.format_date(str(value), locale) if value else "-"


def inspect_latest(project_root: Path, memory_root: Path) -> dict[str, Any]:
    path = memory_root / "views/program-status.json"
    if not path.is_file():
        return {
            "ok": False,
            "status": "missing",
            "mode": "inspect",
            "project_root": str(project_root),
            "reason": "canonical program-status view is missing",
            "recommended_workflows": ["adp-plan-baseline", "adp-state-audit", "adp-program-status"],
            "outputs": {},
        }
    model = load_json(path)
    if not isinstance(model.get("progress"), dict):
        raise ContractError("ADP-PROGRESS-MIGRATION-REQUIRED: canonical program-status progress v2 is missing")
    validate_progress_projection(model["progress"])
    outputs = output_paths(memory_root, str(model.get("snapshot_id")))
    input_audit_path = next(
        (
            resolve_path(project_root, str(item.get("path")))
            for item in model.get("source_inventory", [])
            if item.get("type") == "input-audit" and item.get("path")
        ),
        None,
    )
    return {
        "ok": True,
        "status": "inspected",
        "mode": "inspect",
        "snapshot_id": model.get("snapshot_id"),
        "overall_status": model.get("overall_status"),
        "report_confidence": model.get("report_confidence"),
        "baseline_revision": model.get("baseline_revision"),
        "input_audit_id": model.get("input_audit_id"),
        "input_audit_path": str(input_audit_path) if input_audit_path else None,
        "as_of": model.get("as_of"),
        "period_delta": model.get("period_delta"),
        "progress_schema_version": model["progress"]["progress_schema_version"],
        "progress_measurement_status": model["progress"]["measurement_status"],
        "locale": model.get("locale"),
        "fallbacks": ["document_output_language"] if model.get("locale_fallback") else [],
        "warnings": [],
        "outputs": {key: str(value) for key, value in outputs.items()},
        "recommended_workflows": recovery_workflows(model),
    }


def resolve_headless_memlog(args: argparse.Namespace) -> Path:
    project_root = Path(args.project_root).expanduser().resolve()
    if args.memlog:
        path = Path(args.memlog).expanduser()
        return (path if path.is_absolute() else project_root / path).resolve()
    try:
        run_date = date.fromisoformat(args.as_of).isoformat() if args.as_of else date.today().isoformat()
    except ValueError:
        run_date = date.today().isoformat()
    return (project_root / "_bmad-output/adp/program-status-runs" / f"{run_date}-{args.mode}" / ".memlog.md").resolve()


def memlog_helper(project_root: Path) -> Path | None:
    for path in [project_root / "_bmad/scripts/memlog.py", DEFAULT_MEMLOG_SCRIPT]:
        if path.is_file():
            return path.resolve()
    return None


def run_memlog(helper: Path, *arguments: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(helper), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown memlog failure"
        raise RuntimeError(detail)


def complete_memlog(helper: Path, memlog: Path) -> None:
    try:
        run_memlog(helper, "set-complete", "--path", str(memlog))
    except RuntimeError:
        run_memlog(helper, "set", "--path", str(memlog), "--key", "status", "--value", "complete")


def append_fallback_memlog(path: Path, entry_type: str, text: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            "topic: ADP program status\n"
            "goal: Preserve headless status assumptions and decisions\n"
            f"updated: {datetime.now(timezone.utc).isoformat(timespec='minutes')}\n"
            "---\n\n",
            encoding="utf-8",
        )
    needs_newline = path.stat().st_size > 0 and not path.read_text(encoding="utf-8").endswith("\n")
    with path.open("a", encoding="utf-8") as stream:
        if needs_newline:
            stream.write("\n")
        stream.write(f"- ({entry_type}) {' '.join(text.split())}\n")


def append_memlog(args: argparse.Namespace, memlog: Path, entry_type: str, text: str) -> None:
    helper = memlog_helper(Path(args.project_root).expanduser().resolve())
    if helper:
        try:
            run_memlog(helper, "append", "--path", str(memlog), "--type", entry_type, "--text", text)
            return
        except (OSError, RuntimeError):
            pass
    append_fallback_memlog(memlog, entry_type, text)


def initialize_headless_memlog(args: argparse.Namespace) -> Path:
    memlog = resolve_headless_memlog(args)
    helper = memlog_helper(Path(args.project_root).expanduser().resolve())
    if not memlog.exists() and helper:
        try:
            run_memlog(
                helper,
                "init",
                "--path",
                str(memlog),
                "--field",
                "topic=ADP program status",
                "--field",
                "goal=Preserve headless status assumptions and decisions",
            )
        except (OSError, RuntimeError) as exc:
            append_fallback_memlog(memlog, "event", f"Memlog helper unavailable; using fallback trail: {exc}")
    elif not memlog.exists():
        append_fallback_memlog(memlog, "event", "Memlog helper unavailable; using fallback trail.")
    if not memlog.is_file():
        raise OSError(f"memlog is not a readable file: {memlog}")
    return memlog


def append_headless_context(args: argparse.Namespace, memlog: Path) -> None:
    provided = set(getattr(args, "provided_options", set()))
    defaults = [
        value
        for option, value in [
            ("--mode", "mode=generate"),
            ("--memory-root", f"memory_root={DEFAULT_MEMORY_ROOT}"),
            ("--as-of", f"as_of={date.today().isoformat()}"),
        ]
        if option not in provided
    ]
    scope = {
        "mode": args.mode,
        "project_root": str(Path(args.project_root).expanduser().resolve()),
        "memory_root": str(resolve_path(Path(args.project_root).expanduser().resolve(), args.memory_root)),
        "as_of": args.as_of or date.today().isoformat(),
        "period_start": args.period_start,
        "period_end": args.period_end,
        "dry_run": bool(args.dry_run),
    }
    append_memlog(
        args,
        memlog,
        "assumption",
        f"Resolved headless scope: {json.dumps(scope, sort_keys=True)}; defaults applied: {', '.join(defaults) or 'none'}.",
    )
    append_memlog(
        args,
        memlog,
        "decision",
        f"Use {Path(args.artifact_audit_script).expanduser().resolve()} for the mandatory post-generation artifact disposition.",
    )


def run_artifact_audit(args: argparse.Namespace, operation: dict[str, Any]) -> dict[str, Any]:
    script = Path(args.artifact_audit_script).expanduser().resolve()
    if not script.is_file():
        error = DependencyError("adp-state-audit artifact validator", script, ["adp-setup", "adp-state-audit"])
        return dependency_failure_result(args, error)
    input_audit = args.input_audit_json or operation.get("input_audit_path")
    if not input_audit:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "artifact validation requires the canonical input audit path",
            "outputs": {},
            "recommended_workflows": ["adp-state-audit"],
        }
    artifact_keys = ["snapshot", "program_status_json", "program_status_markdown", "weekly_report", "project_lead"]
    artifacts = [operation.get("outputs", {}).get(key) for key in artifact_keys]
    if any(not value for value in artifacts):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "artifact validation requires the snapshot and all canonical views",
            "outputs": {},
            "recommended_workflows": ["adp-program-status"],
        }
    command = [
        sys.executable,
        str(script),
        str(Path(args.project_root).expanduser().resolve()),
        "--phase",
        "artifact",
        "--memory-root",
        str(resolve_path(Path(args.project_root).expanduser().resolve(), args.memory_root)),
        "--input-audit-json",
        str(input_audit),
        "--as-of",
        str(operation.get("as_of") or args.as_of or date.today().isoformat()),
        "--headless",
    ]
    for artifact in artifacts:
        command.extend(["--artifact", str(artifact)])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "status": "error",
            "reason": f"cannot run adp-state-audit artifact validation: {exc}",
            "outputs": {},
            "recommended_workflows": ["adp-setup", "adp-state-audit"],
        }
    try:
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise json.JSONDecodeError("result must be a JSON object", completed.stdout, 0)
        return result
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "error",
            "reason": completed.stderr.strip() or "adp-state-audit returned invalid JSON",
            "outputs": {},
            "recommended_workflows": ["adp-state-audit"],
        }


def finalize_headless_result(args: argparse.Namespace, operation: dict[str, Any], memlog: Path) -> dict[str, Any]:
    audit: dict[str, Any] = {}
    published_outputs: dict[str, str] = {}
    reason = str(operation.get("reason") or operation.get("error") or "")
    if operation.get("ok") and args.dry_run:
        reason = "headless generation cannot complete artifact validation in dry-run mode"
    elif operation.get("ok"):
        audit = run_artifact_audit(args, operation)
        if not audit.get("ok") or not audit.get("safe_to_publish"):
            reason = str(audit.get("reason") or audit.get("error") or "artifact validation did not approve publication")
        elif operation.get("publication_pending"):
            try:
                published_outputs = publish_staged_outputs(operation)
            except (ContractError, OSError, json.JSONDecodeError) as exc:
                reason = f"artifact validation passed but canonical publication failed: {exc}"
        else:
            published_outputs = dict(operation.get("outputs", {}))

    complete = bool(
        operation.get("ok")
        and not args.dry_run
        and audit.get("ok")
        and audit.get("safe_to_publish")
        and published_outputs
    )
    workflows = list(
        dict.fromkeys(
            [
                *operation.get("recommended_workflows", []),
                *audit.get("recommended_workflows", []),
            ]
        )
    )
    result = {
        "ok": complete,
        "status": "complete" if complete else "blocked",
        "mode": args.mode,
        "safe_to_publish": complete,
        "snapshot_id": operation.get("snapshot_id"),
        "overall_status": operation.get("overall_status"),
        "report_confidence": operation.get("report_confidence"),
        "baseline_revision": operation.get("baseline_revision"),
        "input_audit_id": operation.get("input_audit_id") or audit.get("input_audit_id"),
        "locale": operation.get("locale"),
        "fallbacks": operation.get("fallbacks", []),
        "warnings": operation.get("warnings", []),
        "outputs": published_outputs if complete else {},
        "artifact_validation_id": audit.get("artifact_validation_id"),
        "artifact_validation_reports": audit.get("outputs", {}),
        "recommended_workflows": workflows,
        "memlog": str(memlog),
    }
    for key in ["dependency_name", "missing_path"]:
        if operation.get(key) or audit.get(key):
            result[key] = operation.get(key) or audit.get(key)
    if not complete:
        result["reason"] = reason or "program status did not reach a publishable terminal state"
        if operation.get("publication_pending") and operation.get("outputs"):
            result["staged_outputs"] = operation["outputs"]
    append_memlog(
        args,
        memlog,
        "decision",
        f"Headless result is {result['status']}; safe_to_publish={str(result['safe_to_publish']).lower()}; reason={result.get('reason', 'none')}.",
    )
    helper = memlog_helper(Path(args.project_root).expanduser().resolve())
    if complete and helper:
        try:
            complete_memlog(helper, memlog)
        except (OSError, RuntimeError):
            append_fallback_memlog(memlog, "event", "Could not mark the memlog complete through the helper.")
    return result


def load_module(
    path: Path,
    name: str,
    *,
    dependency_name: str,
    recommended_workflows: list[str],
) -> Any:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise DependencyError(dependency_name, path, recommended_workflows)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load required script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_baseline_module(path: Path, config_module: Any) -> Any:
    sys.modules["adp_effective_config"] = config_module
    return load_module(
        path,
        "adp_program_status_baseline_contract",
        dependency_name="adp-plan-baseline baseline contract",
        recommended_workflows=["adp-setup", "adp-plan-baseline"],
    )


def add_file_source(
    project_root: Path,
    path: Path,
    source_type: str,
    inventory: list[dict[str, Any]],
    fingerprints: dict[str, str],
) -> None:
    path = path.resolve()
    relative = relative_path(project_root, path)
    inventory.append({"type": source_type, "path": relative, "exists": path.is_file()})
    if path.is_file():
        fingerprints[relative] = file_sha256(path)


def add_config_sources(
    project_root: Path, config: dict[str, Any], inventory: list[dict[str, Any]], fingerprints: dict[str, str]
) -> None:
    for source in config.get("sources_checked", []):
        path = Path(str(source.get("path", "")))
        if path.is_file():
            add_file_source(project_root, path, "effective-config", inventory, fingerprints)


def add_signal_sources(
    project_root: Path, signals: list[dict[str, Any]], inventory: list[dict[str, Any]], fingerprints: dict[str, str]
) -> None:
    for signal in signals:
        reference = signal["source"]["reference"].split("#", 1)[0]
        path = Path(reference)
        if not path.is_absolute():
            path = project_root / path
        if path.is_file():
            add_file_source(project_root, path, "signal-evidence", inventory, fingerprints)
        else:
            inventory.append({"type": "signal-reference", "path": signal["source"]["reference"], "exists": False})


def audit_summary(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_status": audit.get("audit_status"),
        "execution_disposition": audit.get("execution_disposition"),
        "report_confidence": audit.get("report_confidence"),
        "recommended_workflows": recommended_from_audit(audit),
    }


def recommended_from_audit(audit: dict[str, Any]) -> list[str]:
    values = audit.get("recommended_workflows", [])
    return sorted(set(str(value) for value in values if str(value).strip())) if isinstance(values, list) else []


def recovery_workflows(model: dict[str, Any]) -> list[str]:
    workflows = set(model.get("audit_summary", {}).get("recommended_workflows", []))
    workflows.update(model.get("progress", {}).get("recovery", {}).get("workflows", []))
    for item in [*model.get("milestones", []), *model.get("gates", []), *model.get("signals", [])]:
        if item.get("status") == "indeterminate":
            workflows.add("adp-status-sync" if item.get("constraint_type") == "milestone" else "adp-state-audit")
        elif item.get("status") in {"at-risk", "off-plan"}:
            workflows.add("adp-risk-dependency-change-review")
    return sorted(workflows)


def blocked_result(
    project_root: Path,
    memory_root: Path,
    locale: str,
    config: dict[str, Any],
    reason: str,
    recommended: list[str],
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "mode": "generate",
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "reason": reason,
        "findings": findings or [],
        "locale": locale,
        "fallbacks": config.get("fallbacks", []),
        "outputs": {},
        "recommended_workflows": sorted(set(recommended)),
    }


def failure_result(args: argparse.Namespace, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "mode": getattr(args, "mode", "generate"),
        "reason": reason,
        "outputs": {},
        "recommended_workflows": ["adp-state-audit", "adp-program-status"],
    }


def dependency_failure_result(args: argparse.Namespace, error: DependencyError) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "mode": getattr(args, "mode", "generate"),
        "reason": str(error),
        "dependency_name": error.dependency_name,
        "missing_path": str(error.missing_path),
        "outputs": {},
        "recommended_workflows": list(dict.fromkeys(error.recommended_workflows)),
    }


def program_finding(code: str, severity: str, summary: str, source: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "summary": summary, "source": source}


def resolve_path(project_root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{label} must be ISO YYYY-MM-DD: {value!r}") from exc


def optional_date(value: Any, label: str) -> date | None:
    if is_missing(value):
        return None
    return parse_date(str(value).strip(), label)


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"generated-at must be ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return data


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def relative_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def normalize_path_key(value: str) -> str:
    normalized = value.replace("\\", "/").removeprefix("./")
    memory_marker = "_bmad-output/adp/memory/"
    if memory_marker in normalized:
        return normalized.split(memory_marker, 1)[1]
    return normalized


def normalize_hash(value: Any) -> str:
    return str(value or "").lower().removeprefix("sha256:")


def normalized_value(value: Any) -> str:
    return str(value or "").strip().casefold()


def is_missing(value: Any) -> bool:
    return normalized_value(value) in MISSING_VALUES


def source_reference(source: Any) -> str:
    return str(source.get("reference") or "") if isinstance(source, dict) else ""


def split_references(value: str) -> list[str]:
    if is_missing(value):
        return []
    return [item.strip() for item in re.split(r"\s*[;,]\s*", value) if item.strip()]


def unique_dicts(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        result[str(item.get(key))] = item
    return list(result.values())


def emit(result: dict[str, Any], output: str | None) -> None:
    text = canonical_text(result)
    if output:
        atomic_write(Path(output).expanduser().resolve(), text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    sys.exit(main())
