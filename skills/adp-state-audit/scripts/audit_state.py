#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Audit ADP shared project state quality from the deterministic prepass."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import locale
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SCRIPT_ROOT.parent
DEFAULT_PREPASS_SCRIPT = SKILLS_ROOT / "adp-agent-program-lead" / "scripts" / "adp-state-prepass.py"
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"
DEFAULT_LOCALE_CATALOG_PATH = SKILLS_ROOT / "adp-plan-baseline" / "assets" / "locale-catalog.json"
DEFAULT_BASELINE_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "baseline.py"
DEFAULT_FLOW_GRAPH_SCRIPT = SKILLS_ROOT / "adp-flow-graph" / "scripts" / "flow_graph.py"
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
DEFAULT_AUDIT_OUTPUT_PATH = "audits"
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
GENERATOR_VERSION = "2.2.0"
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}
TERMINAL_DECISION_STATUSES = {"accepted", "closed", "done", "cancelled", "rejected", "superseded"}
NON_EXECUTABLE_INTAKE_STATES = {
    "candidate",
    "cancelled",
    "dismissed",
    "dry-run",
    "preview",
    "proposal",
    "rejected",
}
STATUS_SYNC_RECEIPT_SCHEMA_VERSION = 1
STATUS_SYNC_RECEIPT_REL = Path("receipts") / "status-sync"
INTAKE_PARTIAL_CLOSURE_RECEIPT_REL = Path("receipts") / "status-sync-partial-closure"
INTAKE_RETIREMENT_RECEIPT_REL = Path("receipts") / "status-sync-retirement"
INTAKE_RETIREMENT_REASONS = {"never-applied", "superseded-by", "invalid-proposal"}
ATTESTATION_WRAPPER_FIELDS = {
    "attestation",
    "attested_at",
    "attested_by",
    "execution_report",
    "original_report",
    "wrapper_attestation",
}
PLACEHOLDERS = {
    "",
    "-",
    "tbd",
    "todo",
    "none",
    "n/a",
    "na",
    "unknown",
    "待定",
    "待确认",
    "未指定",
    "未知",
    "暂无",
    "不适用",
    "无",
}
BUSINESS_PACKET_FIELD_ALIASES = {
    "background": {"en": ("Background",), "zh": ("背景",)},
    "decision": {"en": ("Decision Needed",), "zh": ("待决策事项", "决策事项")},
    "options": {"en": ("Options",), "zh": ("选项",)},
    "recommendation": {"en": ("Recommendation",), "zh": ("建议", "推荐方案")},
    "status": {"en": ("Status",), "zh": ("状态",)},
    "deadline": {
        "en": ("Deadline / trigger",),
        "zh": (
            "到期日 / 触发条件",
            "截止日期 / 触发条件",
            "截止日或触发条件",
            "到期日或触发条件",
            "截止日 / 触发条件",
        ),
    },
    "owner": {
        "en": ("Confirming owner", "Confirmer", "Requested decision owner"),
        "zh": ("确认负责人", "请求决策人", "决策负责人"),
    },
    "workstreams": {"en": ("Affected workstreams",), "zh": ("受影响工作线", "受影响工作流")},
}
REQUIRED_PREPASS_GAP_FIELDS = {"gap", "category", "gap_type", "blocking", "field", "recommended_workflow"}
REQUIRED_PREPASS_COLLECTIONS = {
    "sources_read",
    "missing_sources",
    "workstreams",
    "gaps",
    "cross_reference_gaps",
    "action_cross_check",
    "ledger_actions",
}
SUPPORTED_PREPASS_SCHEMA_VERSION = 2
VALID_GAP_CATEGORIES = {"freshness", "completeness", "consistency", "closure", "merge_quality"}
SCENARIO_CAPABILITIES = {
    "global": "global-project-readout",
    "fde-morning": "fde-action-list",
    "business-biweekly": "global-project-readout",
    "weekly-report": "weekly-report-consumption",
    "project-lead": "global-project-readout",
    "roadmap": "global-project-readout",
    "management-panel": "global-project-readout",
}
VIEW_OWNER_WORKFLOWS = {
    "views/project-lead.md": "adp-agent-program-lead",
    "views/weekly-report.md": "adp-agent-program-lead",
    "views/fde-actions.md": "adp-agent-program-lead",
    "views/acceptance-readiness.md": "adp-acceptance-readiness-review",
    "views/cutover-readiness.md": "adp-acceptance-readiness-review",
    "views/risk-matrix.md": "adp-risk-dependency-change-review",
    "views/dependency-map.md": "adp-risk-dependency-change-review",
    "views/roadmap.md": "adp-roadmap-sync",
    "views/flow-graph.json": "adp-flow-graph",
    "views/action-flow.json": "adp-status-sync",
    "views/risk-flow.json": "adp-risk-dependency-change-review",
}
VIEW_SOURCE_PATTERNS = {
    "views/project-lead.md": (
        "workstreams/*/delivery-record.md",
        "actions/action-ledger.md",
        "daily/*.md",
        "decisions/**/*.md",
        "l0/*.md",
    ),
    "views/weekly-report.md": (
        "workstreams/*/delivery-record.md",
        "actions/action-ledger.md",
        "daily/*.md",
        "decisions/**/*.md",
        "l0/*.md",
    ),
    "views/fde-actions.md": (
        "workstreams/*/delivery-record.md",
        "actions/action-ledger.md",
        "decisions/**/*.md",
    ),
    "views/acceptance-readiness.md": ("workstreams/*/delivery-record.md", "l0/*.md"),
    "views/cutover-readiness.md": ("workstreams/*/delivery-record.md", "l0/*.md"),
    "views/risk-matrix.md": ("workstreams/*/delivery-record.md", "decisions/**/*.md", "l0/*.md"),
    "views/dependency-map.md": ("workstreams/*/delivery-record.md", "decisions/**/*.md", "l0/*.md"),
    "views/roadmap.md": ("workstreams/*/delivery-record.md", "decisions/**/*.md", "l0/*.md"),
}
RENDER_COVERAGE_PROFILES = {
    "adp-program-status-json": {
        "required": {"status.title"},
        "prefixes": {
            "enum.program_status.",
            "enum.report_confidence.",
            "status.confidence_reason.",
            "status.progress_reason.",
        },
    },
    "adp-program-status-markdown": {
        "required": {
            "status.title",
            "status.overall",
            "status.confidence",
            "status.as_of",
            "status.period",
            "field.revision",
            "status.snapshot_id",
            "status.executive_summary",
            "status.critical_constraints",
            "status.top_variances",
            "status.period_changes",
            "status.confidence_basis",
            "status.lineage",
            "status.input_audit_id",
            "status.generator_version",
            "status.rule_ids",
        },
        "prefixes": {"enum.program_status.", "enum.report_confidence.", "status.summary."},
    },
    "adp-weekly-report-markdown": {
        "required": {
            "status.weekly_title",
            "status.executive_summary",
            "status.period_changes",
            "status.top_variances",
            "status.upcoming",
            "status.lineage",
            "status.snapshot_id",
            "status.input_audit_id",
        },
        "prefixes": {"enum.program_status.", "enum.report_confidence.", "status.summary."},
    },
    "adp-project-lead-markdown": {
        "required": {
            "status.project_lead_title",
            "status.overall",
            "status.confidence",
            "field.owner",
            "field.target_date",
            "baseline.gates",
            "baseline.milestones",
            "status.recovery",
            "status.lineage",
            "status.snapshot_id",
            "status.baseline_revision",
            "status.input_audit_id",
        },
        "prefixes": {"enum.program_status.", "enum.report_confidence."},
    },
}


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        arguments = sys.argv[1:]
        scenario = cli_option_value(arguments, "--scenario") or "global"
        error = f"invalid arguments: {message}"
        emit(
            failure_envelope(
                status="error",
                scenario=scenario,
                error=error,
            ),
            None,
        )
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = ContractArgumentParser(
        description=(
            "Run the ADP state prepass, then audit freshness, completeness, "
            "consistency, closure, and merge quality. Writes audit JSON and Markdown."
        )
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument(
        "--phase",
        choices=["input", "artifact"],
        default="input",
        help="Run pre-generation input audit or post-generation artifact validation. Default: input.",
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIO_CAPABILITIES),
        default="global",
        help="Scenario label for output and default prepass capability.",
    )
    parser.add_argument("--capability", help="Override the prepass capability.")
    parser.add_argument("--workstream", action="append", default=[], help="Workstream id to include. Repeatable.")
    parser.add_argument(
        "--memory-root",
        default=DEFAULT_MEMORY_ROOT,
        help=f"ADP state root, relative to project root unless absolute. Default: {DEFAULT_MEMORY_ROOT}.",
    )
    parser.add_argument("--prepass-json", help="Existing prepass JSON to audit instead of running the prepass.")
    parser.add_argument("--prepass-script", default=str(DEFAULT_PREPASS_SCRIPT), help="Path to adp-state-prepass.py.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Path to shared ADP effective-config resolver.")
    parser.add_argument("--baseline-script", default=str(DEFAULT_BASELINE_SCRIPT), help="Path to adp-plan-baseline baseline.py.")
    parser.add_argument("--artifact", action="append", default=[], help="Generated artifact to validate. Repeatable for artifact phase.")
    parser.add_argument("--input-audit-json", help="Input audit JSON that the artifact declares through input_audit_id.")
    parser.add_argument("--panel-input-bundle", help="Canonical panel input bundle for the management-panel pre-render gate.")
    parser.add_argument("--panel-model", help="Rendered panel model/bundle JSON for management-panel artifact validation.")
    parser.add_argument("--max-age-days", type=int, default=7, help="Freshness threshold in days. Default: 7.")
    parser.add_argument("--as-of", help="Audit date, YYYY-MM-DD. Default: today.")
    parser.add_argument("--output-dir", help="Audit output directory. Default: <memory-root>/audits.")
    parser.add_argument("--run-folder-pattern", default="", help="Optional output subfolder pattern. Supports {date} and {scenario}.")
    parser.add_argument("--headless", action="store_true", help="Mark a non-interactive invocation; output is unchanged.")
    parser.add_argument(
        "--execution-mode",
        choices=["direct-python", "python-fallback", "uv"],
        default="direct-python",
        help="Runtime used for the audit; recorded in the structured result envelope.",
    )
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write run result JSON to this file instead of stdout.")
    args = parser.parse_args()
    args.provided_options = {
        item.split("=", 1)[0]
        for item in sys.argv[1:]
        if item.startswith("--")
    }
    return args


def cli_option_value(arguments: list[str], option: str) -> str:
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{option}="):
            return argument.split("=", 1)[1]
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
    return ""


def failure_envelope(
    *,
    status: str,
    scenario: str,
    recommended_workflows: list[str] | None = None,
    error: str | None = None,
    reason: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "status": status,
        "scenario": scenario,
        "outputs": {},
        "recommended_workflows": list(dict.fromkeys(recommended_workflows or [])),
    }
    if error:
        result["error"] = error
    if reason:
        result["reason"] = reason
    result.update(details)
    return result


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        result = (
            artifact_failure_envelope(args.scenario, status="error", error=str(exc))
            if args.phase == "artifact"
            else failure_envelope(status="error", scenario=args.scenario, error=str(exc))
        )
        if args.verbose:
            print(f"audit failed: {exc}", file=sys.stderr)
    try:
        emit(result, args.output)
    except OSError as exc:
        result = failure_envelope(
            status="error",
            scenario=args.scenario,
            error=f"failed to write run result: {exc}",
        )
        emit(result, None)
        return 2
    if not result.get("ok"):
        return 1 if result.get("status") == "blocked" else 2
    return 0


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return failure_envelope(
            status="error",
            scenario=args.scenario,
            error="project_root is not an existing directory",
            project_root=str(project_root),
        )

    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError:
        return failure_envelope(
            status="error",
            scenario=args.scenario,
            error="as_of must use YYYY-MM-DD",
            project_root=str(project_root),
        )
    memory_root = resolve_memory_root(project_root, args.memory_root)
    if args.scenario == "management-panel":
        return run_panel_validation(args, project_root, memory_root, as_of)
    if args.phase == "artifact":
        return run_artifact_validation(args, project_root, memory_root, as_of)
    if args.prepass_json:
        try:
            prepass = load_json(Path(args.prepass_json))
        except (OSError, json.JSONDecodeError) as exc:
            return failure_envelope(
                status="error",
                scenario=args.scenario,
                error=f"cannot read prepass JSON: {exc}",
                recommended_workflows=["adp-agent-program-lead"],
                project_root=str(project_root),
                memory_root=str(memory_root),
            )
        if not isinstance(prepass, dict):
            return failure_envelope(
                status="error",
                scenario=args.scenario,
                error="prepass JSON must contain an object",
                recommended_workflows=["adp-agent-program-lead"],
                project_root=str(project_root),
                memory_root=str(memory_root),
            )
        memory_root = Path(prepass.get("memory_root") or memory_root).resolve()
    else:
        prepass = run_prepass(args, project_root, as_of)

    if not prepass.get("ok"):
        return failure_envelope(
            status="blocked",
            scenario=args.scenario,
            error=prepass.get("error", "prepass failed"),
            recommended_workflows=[prepass.get("recommended_workflow") or "adp-project-kickoff"],
            project_root=str(project_root),
            memory_root=str(memory_root),
        )

    if not memory_root.exists() or not memory_root.is_dir():
        return failure_envelope(
            status="blocked",
            scenario=args.scenario,
            error="ADP memory root is missing; run adp-project-kickoff or pass --memory-root",
            recommended_workflows=["adp-project-kickoff"],
            project_root=str(project_root),
            memory_root=str(memory_root),
        )

    prepass_errors = validate_prepass_contract(prepass)
    if prepass_errors:
        return failure_envelope(
            status="blocked",
            scenario=args.scenario,
            error="prepass JSON lacks the typed gap contract required by adp-state-audit",
            recommended_workflows=["adp-agent-program-lead"],
            details=prepass_errors[:10],
            project_root=str(project_root),
            memory_root=str(memory_root),
        )

    audit = build_audit(
        prepass,
        project_root,
        memory_root,
        args.scenario,
        as_of,
        args.max_age_days,
        Path(args.config_script).resolve(),
        Path(args.baseline_script).resolve(),
    )
    output_dir = resolve_output_dir(args.output_dir, memory_root, args.run_folder_pattern, as_of, args.scenario)
    execution = execution_record(args, project_root, memory_root, as_of, args.scenario, output_dir)
    try:
        output_paths = write_audit_outputs(
            audit,
            output_dir,
            as_of,
            args.scenario,
            Path(args.config_script).resolve().parent.parent / "assets" / "locale-catalog.json",
        )
    except OSError as exc:
        return failure_envelope(
            status="error",
            scenario=args.scenario,
            error=f"cannot write audit outputs: {exc}",
            recommended_workflows=audit["recommended_workflows"],
            project_root=str(project_root),
            memory_root=str(memory_root),
        )
    audit["outputs"] = output_paths
    result = {
        "ok": True,
        "status": "complete",
        "audit_schema_version": audit["audit_schema_version"],
        "audit_status": audit["audit_status"],
        "audit_type": audit["audit_type"],
        "input_audit_id": audit["input_audit_id"],
        "execution_disposition": audit["execution_disposition"],
        "safe_to_generate": audit["safe_to_generate"],
        "safe_to_generate_green_report": audit["safe_to_generate_green_report"],
        "report_confidence": audit["report_confidence"],
        "communication_locale": audit["effective_config"]["communication_locale"],
        "document_locale": audit["locale"],
        "language_fallbacks": audit["effective_config"]["fallbacks"],
        "config_warnings": audit["effective_config"]["warnings"],
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "scenario": args.scenario,
        "outputs": output_paths,
        "counts": audit["counts"],
        "execution": execution,
        "recommended_workflows": audit["recommended_workflows"],
    }
    return result


def load_panel_audit_module() -> Any:
    path = SCRIPT_ROOT / "scripts/panel_audit.py"
    spec = importlib.util.spec_from_file_location("adp_state_audit_panel", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load panel audit contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_panel_validation(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    as_of: date,
) -> dict[str, Any]:
    module = load_panel_audit_module()
    output_dir = resolve_output_dir(args.output_dir, memory_root, args.run_folder_pattern, as_of, args.scenario)
    if args.phase == "input":
        if not args.panel_input_bundle:
            return failure_envelope(
                status="error",
                scenario=args.scenario,
                error="management-panel input phase requires --panel-input-bundle",
                recommended_workflows=["adp-management-panel"],
            )
        source_path = resolve_project_path(project_root, args.panel_input_bundle)
        try:
            inputs = load_json(source_path)
        except (OSError, json.JSONDecodeError) as exc:
            return failure_envelope(
                status="error",
                scenario=args.scenario,
                error=f"cannot read panel input bundle: {exc}",
                recommended_workflows=["adp-management-panel"],
            )
        audit = module.audit_panel_inputs(inputs, as_of=as_of, max_age_days=args.max_age_days)
        execution = execution_record(args, project_root, memory_root, as_of, args.scenario, output_dir)
        path = module.write_audit_record(audit, output_dir)
        result = {
            "ok": True,
            "status": "complete",
            "audit_type": "panel-input",
            "panel_input_audit_id": audit["panel_input_audit_id"],
            "execution_disposition": audit["execution_disposition"],
            "safe_to_render": audit["safe_to_render"],
            "scenario": args.scenario,
            "counts": audit["counts"],
            "execution": execution,
            "recommended_workflows": audit["recommended_workflows"],
            "outputs": {"json": str(path)},
        }
    else:
        if not args.panel_model or not args.artifact or not args.input_audit_json:
            return artifact_failure_envelope(
                args.scenario,
                status="error",
                error="management-panel artifact phase requires --panel-model, --input-audit-json, and --artifact HTML",
                recommended_workflows=["adp-management-panel"],
            )
        model_path = resolve_project_path(project_root, args.panel_model)
        html_path = resolve_project_path(project_root, args.artifact[0])
        input_audit_path = resolve_project_path(project_root, args.input_audit_json)
        try:
            model = load_json(model_path)
            input_audit = load_json(input_audit_path)
            source_inputs = load_json(resolve_project_path(project_root, args.panel_input_bundle)) if args.panel_input_bundle else None
            if source_inputs is not None:
                request = source_inputs.setdefault("request", {})
                request["panel_input_audit_id"] = input_audit.get("panel_input_audit_id")
                request["panel_input_audit_disposition"] = input_audit.get("execution_disposition")
                request["panel_input_audit_findings"] = [
                    item.get("code")
                    for item in [*input_audit.get("blocking_gaps", []), *input_audit.get("warnings", [])]
                ]
                request["panel_input_audit_workflows"] = list(input_audit.get("recommended_workflows", []))
            html_bytes = html_path.read_bytes()
            bundle_bytes = model_path.read_bytes()
        except (OSError, json.JSONDecodeError) as exc:
            return artifact_failure_envelope(
                args.scenario,
                status="error",
                error=f"cannot read panel artifact inputs: {exc}",
                recommended_workflows=["adp-management-panel"],
            )
        audit = module.audit_panel_artifacts(
            model,
            bundle_bytes,
            html_bytes,
            input_audit=input_audit,
            source_inputs=source_inputs,
            publication_targets={"bundle": model_path, "html": html_path},
        )
        execution = execution_record(args, project_root, memory_root, as_of, args.scenario, output_dir)
        path = module.write_audit_record(audit, output_dir)
        result = {
            "ok": True,
            "status": "complete",
            "audit_type": "panel-artifact",
            "panel_artifact_audit_id": audit["panel_artifact_audit_id"],
            "panel_input_audit_id": audit["panel_input_audit_id"],
            "execution_disposition": audit["execution_disposition"],
            "safe_to_publish": audit["safe_to_publish"],
            "scenario": args.scenario,
            "counts": audit["counts"],
            "execution": execution,
            "recommended_workflows": audit["recommended_workflows"],
            "outputs": {"json": str(path)},
        }
    return result


def run_artifact_validation(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    as_of: date,
) -> dict[str, Any]:
    if not args.input_audit_json:
        return artifact_failure_envelope(
            args.scenario,
            status="error",
            error="artifact phase requires --input-audit-json",
            recommended_workflows=["adp-state-audit"],
        )
    if not args.artifact:
        return artifact_failure_envelope(
            args.scenario,
            status="error",
            error="artifact phase requires at least one --artifact",
            recommended_workflows=["owning artifact workflow"],
        )
    input_audit_path = resolve_project_path(project_root, args.input_audit_json)
    try:
        input_audit = load_json(input_audit_path)
    except (OSError, json.JSONDecodeError) as exc:
        return artifact_failure_envelope(
            args.scenario,
            status="error",
            error=f"cannot read input audit JSON: {exc}",
            recommended_workflows=["adp-state-audit"],
        )
    integrity_errors = validate_input_audit_integrity(input_audit)
    if integrity_errors:
        return artifact_failure_envelope(
            args.scenario,
            status="blocked",
            reason="input audit integrity validation failed",
            recommended_workflows=["adp-state-audit"],
            details=integrity_errors,
        )
    input_scenario = str(input_audit.get("scenario") or "global")
    scenario_was_explicit = "--scenario" in set(getattr(args, "provided_options", set()))
    effective_scenario = args.scenario if scenario_was_explicit else input_scenario
    scenario_mismatch = scenario_was_explicit and args.scenario != input_scenario
    artifact_paths = [resolve_project_path(project_root, raw) for raw in args.artifact]
    validation = build_artifact_validation(
        input_audit,
        input_audit_path,
        artifact_paths,
        project_root,
        memory_root,
        effective_scenario,
        as_of,
        args.max_age_days,
        scenario_mismatch=scenario_mismatch,
        locale_catalog_path=Path(args.config_script).resolve().parent.parent / "assets" / "locale-catalog.json",
    )
    output_dir = resolve_output_dir(args.output_dir, memory_root, args.run_folder_pattern, as_of, effective_scenario)
    execution = execution_record(
        args, project_root, memory_root, as_of, effective_scenario, output_dir
    )
    try:
        output_paths = write_audit_outputs(
            validation,
            output_dir,
            as_of,
            effective_scenario,
            Path(args.config_script).resolve().parent.parent / "assets" / "locale-catalog.json",
        )
    except OSError as exc:
        return artifact_failure_envelope(
            effective_scenario,
            status="error",
            error=f"cannot write artifact validation outputs: {exc}",
            recommended_workflows=validation["recommended_workflows"],
        )
    validation["outputs"] = output_paths
    result = {
        "ok": True,
        "status": "complete",
        "audit_type": "artifact",
        "artifact_validation_id": validation["artifact_validation_id"],
        "input_audit_id": validation["input_audit_id"],
        "audit_status": validation["audit_status"],
        "execution_disposition": validation["execution_disposition"],
        "safe_to_publish": validation["safe_to_publish"],
        "communication_locale": validation["effective_config"]["communication_locale"],
        "document_locale": validation["locale"],
        "language_fallbacks": validation["effective_config"]["fallbacks"],
        "config_warnings": validation["effective_config"]["warnings"],
        "scenario": effective_scenario,
        "outputs": output_paths,
        "counts": validation["counts"],
        "execution": execution,
        "recommended_workflows": validation["recommended_workflows"],
    }
    return result


def artifact_failure_envelope(
    scenario: str,
    *,
    status: str,
    recommended_workflows: list[str] | None = None,
    error: str | None = None,
    reason: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    return failure_envelope(
        status=status,
        scenario=scenario,
        recommended_workflows=recommended_workflows,
        error=error,
        reason=reason,
        phase="artifact",
        audit_type="artifact",
        execution_disposition="blocked",
        safe_to_publish=False,
        **details,
    )


def build_artifact_validation(
    input_audit: dict[str, Any],
    input_audit_path: Path,
    artifact_paths: list[Path],
    project_root: Path,
    memory_root: Path,
    scenario: str,
    as_of: date,
    max_age_days: int,
    *,
    scenario_mismatch: bool = False,
    locale_catalog_path: Path,
) -> dict[str, Any]:
    raw_findings: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    expected_audit_id = str(input_audit.get("input_audit_id"))
    expected_revision = input_audit.get("baseline_revision")
    expected_locale = str(input_audit.get("locale") or "en")
    input_effective_config = input_audit.get("effective_config")
    if not isinstance(input_effective_config, dict):
        input_effective_config = {}
    effective_config = {
        "document_locale": str(input_effective_config.get("document_locale") or expected_locale),
        "communication_locale": str(input_effective_config.get("communication_locale") or expected_locale),
        "fallbacks": list(input_effective_config.get("fallbacks", []))
        if isinstance(input_effective_config.get("fallbacks"), list)
        else [],
        "value_sources": dict(input_effective_config.get("value_sources", {}))
        if isinstance(input_effective_config.get("value_sources"), dict)
        else {},
        "warnings": list(input_effective_config.get("warnings", []))
        if isinstance(input_effective_config.get("warnings"), list)
        else [],
    }
    expected_fingerprints = input_audit.get("source_fingerprints") if isinstance(input_audit.get("source_fingerprints"), dict) else {}
    try:
        locale_catalog = load_json(locale_catalog_path)
        locale_catalog_fingerprint = file_sha256(locale_catalog_path)
    except (OSError, json.JSONDecodeError) as exc:
        locale_catalog = {}
        locale_catalog_fingerprint = ""
        raw_findings.append(
            artifact_finding(
                "artifact.locale_catalog_unavailable",
                "blocking",
                "blocked",
                f"shared locale catalog could not be loaded: {exc}",
                str(locale_catalog_path),
                "adp-setup",
            )
        )
    if input_audit.get("execution_disposition") == "blocked":
        raw_findings.append(
            artifact_finding(
                "input_audit.blocked",
                "blocking",
                "blocked",
                "artifact was generated from an input audit that blocked execution",
                str(input_audit_path),
                "adp-state-audit",
            )
        )
    elif input_audit.get("execution_disposition") == "degraded":
        raw_findings.append(
            artifact_finding(
                "input_audit.degraded",
                "warning",
                "degraded",
                "artifact inherits degraded confidence from its input audit",
                str(input_audit_path),
                "adp-state-audit",
            )
        )
    if scenario_mismatch:
        raw_findings.append(
            artifact_finding(
                "artifact.scenario_mismatch",
                "blocking",
                "blocked",
                f"explicit validation scenario {scenario!r} does not match input audit scenario {input_audit.get('scenario')!r}",
                str(input_audit_path),
                "owning artifact workflow",
            )
        )
    changed_sources: list[str] = []
    for source, expected in expected_fingerprints.items():
        if is_derived_lineage_path(str(source)):
            continue
        current_path = resolve_fingerprint_source(project_root, memory_root, str(source))
        if current_path is None or not current_path.is_file() or not fingerprints_equal(file_sha256(current_path), expected):
            changed_sources.append(str(source))
    if changed_sources:
        raw_findings.append(
            artifact_finding(
                "artifact.input_sources_changed",
                "warning",
                "degraded",
                f"{len(changed_sources)} source(s) changed or disappeared after the input audit",
                ", ".join(changed_sources),
                "adp-state-audit",
            )
        )
    for path in artifact_paths:
        if not path.is_file():
            raw_findings.append(
                artifact_finding(
                    "artifact.missing",
                    "blocking",
                    "blocked",
                    "generated artifact is missing",
                    str(path),
                    "owning artifact workflow",
                )
            )
            continue
        before_hash = file_sha256(path)
        try:
            metadata = artifact_metadata(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raw_findings.append(
                artifact_finding(
                    "artifact.unreadable",
                    "blocking",
                    "blocked",
                    str(exc),
                    str(path),
                    "owning artifact workflow",
                )
            )
            continue
        artifacts.append({"path": str(path), "fingerprint": before_hash, "metadata": metadata})
        if metadata.get("flow_graph_schema_version") is not None:
            graph_errors = [
                *validate_flow_graph_contract(metadata),
                *validate_flow_graph_publication(memory_root, path, metadata),
            ]
            if metadata.get("topology", {}).get("baseline_revision") != expected_revision:
                graph_errors.append("topology baseline revision does not match input audit")
            for error in sorted(set(graph_errors)):
                raw_findings.append(
                    artifact_finding(
                        "artifact.flow_graph_invalid",
                        "blocking",
                        "blocked",
                        error,
                        str(path),
                        "adp-flow-graph",
                    )
                )
            if file_sha256(path) != before_hash:
                raise RuntimeError(f"artifact validation modified immutable input: {path}")
            continue
        required = [
            "generated_at",
            "as_of",
            "reporting_period",
            "report_confidence",
            "scenario",
            "input_audit_id",
            "baseline_revision",
            "source_fingerprints",
            "locale",
            "locale_fallback",
            "render_contract",
            "generator_version",
        ]
        for field_name in required:
            if field_name == "source_fingerprints":
                missing = not isinstance(metadata.get(field_name), dict) or not metadata[field_name]
            elif field_name == "locale_fallback":
                missing = not isinstance(metadata.get(field_name), bool)
            elif field_name == "reporting_period":
                missing = not isinstance(metadata.get(field_name), (str, dict)) or not metadata[field_name]
            elif field_name == "render_contract":
                missing = not isinstance(metadata.get(field_name), dict) or not metadata[field_name]
            else:
                missing = not is_meaningful(metadata.get(field_name))
            if missing:
                raw_findings.append(
                    artifact_finding(
                        f"artifact.{field_name}_missing",
                        "blocking",
                        "blocked",
                        f"artifact metadata is missing {field_name}",
                        str(path),
                        "owning artifact workflow",
                    )
                )
        if str(metadata.get("input_audit_id") or "") != expected_audit_id:
            raw_findings.append(
                artifact_finding(
                    "artifact.input_audit_mismatch",
                    "blocking",
                    "blocked",
                    f"artifact input_audit_id does not match {expected_audit_id}",
                    str(path),
                    "owning artifact workflow",
                )
            )
        if metadata.get("baseline_revision") != expected_revision:
            raw_findings.append(
                artifact_finding(
                    "artifact.baseline_revision_mismatch",
                    "blocking",
                    "blocked",
                    f"artifact baseline revision does not match {expected_revision}",
                    str(path),
                    "owning artifact workflow",
                )
            )
        artifact_locale = str(metadata.get("locale") or "")
        if artifact_locale and artifact_locale != expected_locale:
            raw_findings.append(
                artifact_finding(
                    "artifact.locale_mismatch",
                    "blocking",
                    "blocked",
                    f"artifact locale {artifact_locale!r} does not match input audit locale {expected_locale!r}",
                    str(path),
                    "owning artifact workflow",
                )
            )
        render_contract_errors = validate_render_contract(
            metadata.get("render_contract"),
            artifact_locale,
            artifact_render_text(path, metadata),
            locale_catalog,
            locale_catalog_fingerprint,
        )
        if render_contract_errors:
            raw_findings.append(
                artifact_finding(
                    "artifact.render_contract_invalid",
                    "blocking",
                    "blocked",
                    "; ".join(render_contract_errors),
                    str(path),
                    "owning artifact workflow",
                )
            )
        if isinstance(metadata.get("locale_fallback"), bool) and metadata.get("locale_fallback") != bool(input_audit.get("locale_fallback")):
            raw_findings.append(
                artifact_finding(
                    "artifact.locale_fallback_mismatch",
                    "blocking",
                    "blocked",
                    "artifact locale_fallback disclosure does not match the input audit",
                    str(path),
                    "owning artifact workflow",
                )
            )
        elif metadata.get("locale_fallback") is True:
            raw_findings.append(
                artifact_finding(
                    "artifact.locale_fallback",
                    "warning",
                    "degraded",
                    "artifact explicitly disclosed locale fallback",
                    str(path),
                    "adp-setup",
                )
            )
        generated = parse_datetime(str(metadata.get("generated_at") or ""))
        if generated is None:
            if is_meaningful(metadata.get("generated_at")):
                raw_findings.append(
                    artifact_finding(
                        "artifact.generated_at_invalid",
                        "blocking",
                        "blocked",
                        "artifact generated_at is not a valid ISO timestamp",
                        str(path),
                        "owning artifact workflow",
                    )
                )
        elif generated.date() > as_of:
            raw_findings.append(
                artifact_finding(
                    "artifact.generated_at_future",
                    "blocking",
                    "blocked",
                    "artifact generated_at is later than the validation as_of date",
                    str(path),
                    "owning artifact workflow",
                )
            )
        elif (as_of - generated.date()).days > max_age_days:
            raw_findings.append(
                artifact_finding(
                    "artifact.stale_snapshot",
                    "warning",
                    "degraded",
                    f"artifact is older than {max_age_days} days",
                    str(path),
                    "owning artifact workflow",
                )
            )
        artifact_as_of = parse_date(metadata.get("as_of"))
        if is_meaningful(metadata.get("as_of")) and artifact_as_of is None:
            raw_findings.append(
                artifact_finding(
                    "artifact.as_of_invalid",
                    "blocking",
                    "blocked",
                    "artifact as_of is not a valid date",
                    str(path),
                    "owning artifact workflow",
                )
            )
        if str(metadata.get("scenario") or "") != str(input_audit.get("scenario") or "global"):
            raw_findings.append(
                artifact_finding(
                    "artifact.scenario_lineage_mismatch",
                    "blocking",
                    "blocked",
                    "artifact scenario does not match the input audit scenario",
                    str(path),
                    "owning artifact workflow",
                )
            )
        confidence = str(metadata.get("report_confidence") or "").lower()
        if confidence and confidence not in {"high", "medium", "low"}:
            raw_findings.append(
                artifact_finding(
                    "artifact.report_confidence_invalid",
                    "blocking",
                    "blocked",
                    "artifact report_confidence must be high, medium, or low",
                    str(path),
                    "owning artifact workflow",
                )
            )
        declared_fingerprints = metadata.get("source_fingerprints") if isinstance(metadata.get("source_fingerprints"), dict) else {}
        stale_sources = [
            source
            for source, expected in expected_fingerprints.items()
            if not fingerprints_equal(declared_fingerprints.get(source), expected)
        ]
        if stale_sources:
            raw_findings.append(
                artifact_finding(
                    "artifact.source_fingerprint_mismatch",
                    "warning",
                    "degraded",
                    f"artifact source fingerprints are stale or incomplete for {len(stale_sources)} source(s)",
                    str(path),
                    "owning artifact workflow",
                )
            )
        if file_sha256(path) != before_hash:
            raise RuntimeError(f"artifact validation modified immutable input: {path}")

    canonical = []
    for item in raw_findings:
        finding = canonical_finding(item, str(item["severity"]), "artifact_contract")
        finding["code"] = str(item["code"])
        finding["execution_disposition"] = str(item["execution_disposition"])
        canonical.append(finding)
    blocking = [item for item in canonical if item["severity"] == "blocking"]
    warnings = [item for item in canonical if item["severity"] != "blocking"]
    warning_findings = require_unique_finding_ids(warnings, "warning_findings")
    if any(item["execution_disposition"] == "blocked" for item in canonical):
        disposition = "blocked"
    elif canonical:
        disposition = "degraded"
    else:
        disposition = "ready"
    audit_status = "blocked" if blocking else ("warning" if warnings else "pass")
    recommended = sorted({str(item.get("recommended_workflow")) for item in canonical if item.get("recommended_workflow")})
    validation = {
        "ok": True,
        "audit_type": "artifact",
        "artifact_validation_schema_version": 1,
        "audit_schema_version": 1,
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "scenario": scenario,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "input_audit_id": expected_audit_id,
        "input_audit_path": str(input_audit_path),
        "baseline_revision": expected_revision,
        "locale": expected_locale,
        "effective_config": effective_config,
        "audit_status": audit_status,
        "execution_disposition": disposition,
        "safe_to_generate": disposition != "blocked",
        "safe_to_generate_green_report": disposition == "ready",
        "safe_to_publish": disposition != "blocked",
        "report_confidence": {"pass": "high", "warning": "medium", "blocked": "low"}[audit_status],
        "artifacts": artifacts,
        "blocking_gaps": blocking,
        "warnings": warnings,
        "warning_findings": warning_findings,
        "duplicate_candidates": [],
        "overlap_claims": [],
        "conflicts": [],
        "stale_items": [item for item in warnings if item.get("code") in {"artifact.stale_snapshot", "artifact.source_fingerprint_mismatch"}],
        "counts": {
            "artifacts": len(artifacts),
            "blocking_findings": len(blocking),
            "warning_findings": len(warning_findings),
        },
        "recommended_workflows": recommended,
    }
    validation["artifact_validation_id"] = stable_artifact_validation_id(validation)
    validation["audit_content_hash"] = audit_content_hash(validation)
    return validation


def artifact_finding(
    code: str,
    severity: str,
    disposition: str,
    summary: str,
    source: str,
    workflow: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "execution_disposition": disposition,
        "summary": summary,
        "source": source,
        "recommended_workflow": workflow,
        "category": "artifact_validation",
        "gap_type": code,
    }


def load_flow_graph_module() -> Any:
    spec = importlib.util.spec_from_file_location("adp_state_audit_flow_graph", DEFAULT_FLOW_GRAPH_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load flow graph contract: {DEFAULT_FLOW_GRAPH_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_flow_graph_contract(graph: Any) -> list[str]:
    if not isinstance(graph, dict):
        return ["flow graph must be an object"]
    errors: list[str] = []
    if graph.get("flow_graph_schema_version") != "1.0.0":
        errors.append("flow_graph_schema_version must be 1.0.0")
    if graph.get("layout_identity_owner") != "adp-management-panel" or "layout_id" in graph:
        errors.append("layout identity must remain panel-owned")
    try:
        module = load_flow_graph_module()
        errors.extend(module.graph_semantic_errors(graph))
    except (ImportError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"flow graph semantic validation failed: {exc}")
        return sorted(set(errors))
    topology = graph.get("topology", {})
    revision = topology.get("baseline_revision")
    nodes = topology.get("nodes", [])
    edges = topology.get("edges", [])
    if any(item.get("baseline_revision") != revision for item in [*nodes, *edges] if isinstance(item, dict)):
        errors.append("topology contains a cross-revision node or edge")
    node_ids = [item.get("node_id") for item in nodes if isinstance(item, dict)]
    edge_ids = [item.get("edge_id") for item in edges if isinstance(item, dict)]
    if len(node_ids) != len(set(node_ids)):
        errors.append("topology contains duplicate node IDs")
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("topology contains duplicate edge IDs")
    for item in graph.get("state", {}).get("nodes", []):
        if item.get("execution", {}).get("value") not in {"complete", "in-progress", "ready", "planned", "not-applicable"}:
            errors.append("node execution state is invalid")
        if item.get("health", {}).get("value") not in {"on-plan", "at-risk", "blocked", "indeterminate"}:
            errors.append("node health state is invalid")
    findings = graph.get("findings", [])
    required_recovery = bool(findings)
    recovery = graph.get("recovery", {})
    if (recovery.get("status") == "required") != required_recovery:
        errors.append("recovery status does not match findings")
    return sorted(set(errors))


def validate_flow_graph_publication(memory_root: Path, artifact_path: Path, graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    current = memory_root / "views/flow-graph.json"
    if artifact_path.resolve() != current.resolve():
        return errors
    latest_path = memory_root / "snapshots/flow-graph/latest.json"
    if not latest_path.is_file():
        return ["flow graph latest pointer is missing"]
    try:
        latest = load_json(latest_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"flow graph latest pointer is unreadable: {exc}"]
    for field_name in ("flow_graph_id", "topology_id", "state_snapshot_id", "overlay_snapshot_id"):
        expected = graph.get("flow_graph_id") if field_name == "flow_graph_id" else (
            graph.get("topology", {}).get("topology_id") if field_name == "topology_id" else (
                graph.get("state", {}).get("state_snapshot_id") if field_name == "state_snapshot_id" else graph.get("overlays", {}).get("overlay_snapshot_id")
            )
        )
        if latest.get(field_name) != expected:
            errors.append(f"latest pointer {field_name} does not match current graph")
    raw_snapshot = str(latest.get("snapshot_path") or "")
    snapshot = resolve_contained_path(memory_root, raw_snapshot)
    if snapshot is None or not snapshot.is_file():
        errors.append("immutable flow graph snapshot is missing or outside memory root")
    else:
        try:
            if json.dumps(load_json(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")) != json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")):
                errors.append("immutable flow graph snapshot differs from current graph")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"immutable flow graph snapshot is unreadable: {exc}")
    return sorted(set(errors))


def validate_render_contract(
    value: Any,
    locale_value: str,
    rendered_text: str,
    locale_catalog: dict[str, Any],
    locale_catalog_fingerprint: str,
) -> list[str]:
    if not isinstance(value, dict):
        return ["render_contract must be an object"]
    errors: list[str] = []
    if value.get("catalog_locale") != locale_value:
        errors.append("render_contract catalog_locale must match artifact locale")
    if value.get("catalog_fingerprint") != locale_catalog_fingerprint:
        errors.append("render_contract catalog_fingerprint does not match the shared locale catalog")
    message_keys = value.get("message_keys")
    if not isinstance(message_keys, list) or not message_keys or any(not is_meaningful(item) for item in message_keys):
        errors.append("render_contract message_keys must be a non-empty array")
    coverage_profile = value.get("coverage_profile")
    if coverage_profile is not None:
        profile = RENDER_COVERAGE_PROFILES.get(str(coverage_profile))
        if profile is None:
            errors.append("render_contract coverage_profile is not supported")
        elif isinstance(message_keys, list):
            declared = {str(key) for key in message_keys}
            missing = sorted(profile["required"] - declared)
            if missing:
                errors.append("render_contract coverage_profile is missing required message keys: " + ", ".join(missing))
            missing_prefixes = sorted(prefix for prefix in profile["prefixes"] if not any(key.startswith(prefix) for key in declared))
            if missing_prefixes:
                errors.append("render_contract coverage_profile is missing message-key families: " + ", ".join(missing_prefixes))
    unresolved = value.get("unresolved_message_keys")
    if not isinstance(unresolved, list):
        errors.append("render_contract unresolved_message_keys must be an array")
    elif unresolved:
        errors.append("render_contract contains unresolved message keys")
    if value.get("source_fact_translation_persisted") is not False:
        errors.append("render_contract must confirm source facts were not persistently translated")
    selected_catalog = locale_catalog.get(locale_value) if isinstance(locale_catalog.get(locale_value), dict) else {}
    resolved = [selected_catalog.get(str(key)) for key in message_keys] if isinstance(message_keys, list) else []
    if resolved and any(not isinstance(text, str) or not text for text in resolved):
        errors.append("render_contract message_keys are not fully resolved by the shared locale catalog")
    samples = value.get("localized_system_text")
    if resolved and samples != resolved:
        errors.append("render_contract localized_system_text does not match shared catalog values")
    elif resolved and any(str(sample) not in rendered_text for sample in resolved):
        errors.append("resolved localized system text is not present in the rendered artifact")
    return errors


def artifact_render_text(path: Path, metadata: dict[str, Any]) -> str:
    if path.suffix.lower() == ".json":
        visible = {key: value for key, value in metadata.items() if key != "render_contract"}
        return json.dumps(visible, ensure_ascii=False, sort_keys=True)
    text = read_text(path)
    return text.split("<!-- adp:artifact-metadata:v1 -->", 1)[0]


def artifact_metadata(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        payload = load_json(path)
        return payload
    text = read_text(path)
    marker = "<!-- adp:artifact-metadata:v1 -->"
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError(f"Markdown artifact is missing stable machine metadata marker {marker}")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker_index:], re.DOTALL)
    if not match:
        raise ValueError("Markdown artifact is missing machine metadata JSON after its marker")
    metadata = json.loads(match.group(1))
    if not isinstance(metadata, dict):
        raise ValueError("Markdown artifact machine metadata must be a JSON object")
    return metadata


def stable_artifact_validation_id(validation: dict[str, Any]) -> str:
    identity = {
        "audit_type": validation.get("audit_type"),
        "artifact_validation_schema_version": validation.get("artifact_validation_schema_version"),
        "audit_schema_version": validation.get("audit_schema_version"),
        "schema_version": validation.get("schema_version"),
        "generator_version": validation.get("generator_version"),
        "input_audit_id": validation.get("input_audit_id"),
        "scenario": validation.get("scenario"),
        "as_of": validation.get("as_of"),
        "baseline_revision": validation.get("baseline_revision"),
        "locale": validation.get("locale"),
        "execution_disposition": validation.get("execution_disposition"),
        "audit_status": validation.get("audit_status"),
        "safe_to_publish": validation.get("safe_to_publish"),
        "report_confidence": validation.get("report_confidence"),
        "recommended_workflows": validation.get("recommended_workflows", []),
        "artifacts": [
            {"path": item.get("path"), "fingerprint": item.get("fingerprint")}
            for item in validation.get("artifacts", [])
        ],
        "findings": sorted(
            canonical_finding_identity(item)
            for item in [*validation.get("blocking_gaps", []), *validation.get("warnings", [])]
            if isinstance(item, dict)
        ),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"artifact-validation-{digest[:16]}"


def resolve_project_path(project_root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def resolve_fingerprint_source(project_root: Path, memory_root: Path, raw: str) -> Path | None:
    path = Path(raw).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    elif raw.replace("\\", "/").startswith("_bmad/"):
        resolved = (project_root / path).resolve()
    else:
        resolved = (memory_root / path).resolve()
    project = project_root.resolve()
    memory = memory_root.resolve()
    try:
        resolved.relative_to(project)
        return resolved
    except ValueError:
        try:
            resolved.relative_to(memory)
            return resolved
        except ValueError:
            return None


def execution_record(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    as_of: date,
    scenario: str,
    output_dir: Path,
) -> dict[str, Any]:
    provided = set(getattr(args, "provided_options", set()))
    defaults = [
        name
        for option, name in [
            ("--phase", f"phase={args.phase}"),
            ("--scenario", f"scenario={scenario}"),
            ("--as-of", f"as_of={as_of.isoformat()}"),
            ("--max-age-days", f"max_age_days={args.max_age_days}"),
            ("--memory-root", f"memory_root={memory_root}"),
            ("--output-dir", f"audit_output_path={args.output_dir or DEFAULT_AUDIT_OUTPUT_PATH}"),
            ("--run-folder-pattern", f"run_folder_pattern={args.run_folder_pattern or '<empty>'}"),
        ]
        if option not in provided
    ]
    return {
        "execution_mode": args.execution_mode,
        "fallback_reason": "uv executable unavailable" if args.execution_mode == "python-fallback" else None,
        "scope": {
            "phase": args.phase,
            "scenario": scenario,
            "capability": args.capability or SCENARIO_CAPABILITIES[scenario],
            "workstreams": args.workstream or ["all"],
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "as_of": as_of.isoformat(),
            "max_age_days": args.max_age_days,
            "artifacts": args.artifact or [],
            "input_audit_json": args.input_audit_json or None,
        },
        "defaults_applied": defaults,
        "output_route": str(output_dir),
        "audit_output_path": args.output_dir or DEFAULT_AUDIT_OUTPUT_PATH,
        "run_folder_pattern": args.run_folder_pattern,
        "prepass": str(Path(args.prepass_json).resolve()) if args.prepass_json else None,
    }


def run_prepass(args: argparse.Namespace, project_root: Path, as_of: date) -> dict[str, Any]:
    prepass_script = Path(args.prepass_script).resolve()
    if not prepass_script.exists():
        return {"ok": False, "error": f"prepass script not found: {prepass_script}"}
    capability = args.capability or SCENARIO_CAPABILITIES[args.scenario]
    command = [
        sys.executable,
        str(prepass_script),
        str(project_root),
        "--capability",
        capability,
        "--memory-root",
        args.memory_root,
        "--max-age-days",
        str(args.max_age_days),
        "--as-of",
        as_of.isoformat(),
    ]
    for workstream in args.workstream:
        command.extend(["--workstream", workstream])
    completed = subprocess.run(command, capture_output=True)
    stdout = decode_process_output(completed.stdout)
    stderr = decode_process_output(completed.stderr)
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "error": (stderr or stdout or "prepass emitted invalid JSON").strip()}
    if completed.returncode != 0 or payload.get("status") in {"blocked", "error"}:
        payload["ok"] = False
        payload.setdefault("error", stderr.strip() or f"prepass exited with status {payload.get('status') or completed.returncode}")
    return payload


def audit_action_projection_drift(memory_root: Path, prepass: dict[str, Any]) -> dict[str, Any]:
    ledger_path = memory_root / "actions/action-ledger.md"
    ledger_state_path = memory_root / "actions/action-ledger.state.json"
    ledger_fingerprint = sha256_file_id(ledger_path)
    ledger_state = load_optional_json(ledger_state_path)
    ledger_revision = ledger_state.get("ledger_revision") if isinstance(ledger_state.get("ledger_revision"), int) else 0
    revision_by_action = parse_action_revisions(ledger_path)
    active_actions = [
        item
        for item in prepass.get("ledger_actions", [])
        if isinstance(item, dict) and str(item.get("status", "")).lower() in ACTIVE_ACTION_STATUSES
    ]
    registered = prepass.get("registered_workstreams")
    virtual_scopes = prepass.get("virtual_scopes")
    if isinstance(registered, list) and registered:
        workstream_ids = sorted({str(item) for item in registered if str(item)})
    elif isinstance(virtual_scopes, list) and virtual_scopes:
        workstream_ids = []
    else:
        workstream_ids = sorted(
            path.parent.name
            for path in (memory_root / "workstreams").glob("*/delivery-record.md")
            if path.is_file() and not path.parent.name.startswith(".")
        )
    findings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    active_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for action in active_actions:
        action_id = str(action.get("action_id") or "")
        if action_id:
            active_by_id[action_id].append(action)
    for action_id, duplicates in sorted(active_by_id.items()):
        if len(duplicates) < 2:
            continue
        target_ids = sorted(
            {
                workstream_id
                for action in duplicates
                for workstream_id in workstream_ids
                if action_targets_workstream(action, workstream_id)
            }
        ) or ([workstream_ids[0]] if workstream_ids else ["program"])
        for workstream_id in target_ids:
            findings.append(
                drift_finding(
                    kind="duplicate-active-ledger-action-id",
                    repairability="non-repairable",
                    severity="blocked",
                    workstream_id=workstream_id,
                    action_id=action_id,
                    action_diff={
                        "action_id": action_id,
                        "drift_kind": "duplicate-active-ledger-action-id",
                        "ledger_present": True,
                        "wdr_present": False,
                        "ledger_revision": revision_by_action.get(action_id),
                        "wdr_rendered_sha256": None,
                    },
                    source_path="actions/action-ledger.md",
                )
            )

    if ledger_path.is_file() and (
        not ledger_state
        or ledger_state.get("ledger_fingerprint") != ledger_fingerprint
    ):
        for workstream_id in workstream_ids:
            findings.append(
                drift_finding(
                    kind="ledger-fingerprint-mismatch",
                    repairability="non-repairable",
                    severity="warning",
                    workstream_id=workstream_id,
                    action_id=None,
                    action_diff=None,
                    source_path="actions/action-ledger.state.json",
                )
            )

    for workstream_id in workstream_ids:
        wdr_path = memory_root / "workstreams" / workstream_id / "delivery-record.md"
        wdr_state_path = wdr_path.with_name("delivery-record.state.json")
        sidecar_path = wdr_path.with_name("action-projection.json")
        wdr_state = load_optional_json(wdr_state_path)
        sidecar = load_optional_json(sidecar_path)
        expected = {
            str(action.get("action_id")): expected_projection_record(action, revision_by_action)
            for action in active_actions
            if action.get("action_id") and action_targets_workstream(action, workstream_id)
        }
        expected = {key: value for key, value in expected.items() if value is not None}
        actual_rows = sidecar.get("actions", []) if isinstance(sidecar.get("actions"), list) else []
        actual_ids = [
            str(item.get("action_id"))
            for item in actual_rows
            if isinstance(item, dict) and item.get("action_id")
        ]
        actual = {
            str(item.get("action_id")): item
            for item in actual_rows
            if isinstance(item, dict) and item.get("action_id")
        }
        row_findings: list[dict[str, Any]] = []

        for action_id in sorted({item for item in actual_ids if actual_ids.count(item) > 1}):
            row_findings.append(
                drift_finding(
                    kind="duplicate-action-id-in-sidecar",
                    repairability="non-repairable",
                    severity="blocked",
                    workstream_id=workstream_id,
                    action_id=action_id,
                    action_diff=None,
                    source_path=f"workstreams/{workstream_id}/action-projection.json",
                )
            )

        if not sidecar and (expected or ledger_state):
            row_findings.append(
                drift_finding(
                    kind="sidecar-missing",
                    repairability="non-repairable",
                    severity="blocked",
                    workstream_id=workstream_id,
                    action_id=None,
                    action_diff=None,
                    source_path=f"workstreams/{workstream_id}/action-projection.json",
                )
            )
            for action_id in sorted(expected):
                row_findings.append(
                    action_drift_finding(
                        workstream_id,
                        action_id,
                        "missing-from-wdr",
                        ledger_present=True,
                        wdr_present=False,
                        ledger_revision=revision_by_action.get(action_id),
                        source_path=f"workstreams/{workstream_id}/action-projection.json",
                    )
                )
        else:
            if sidecar.get("ledger_fingerprint") != ledger_fingerprint:
                row_findings.append(
                    drift_finding(
                        kind="ledger-fingerprint-mismatch",
                        repairability="non-repairable",
                        severity="warning",
                        workstream_id=workstream_id,
                        action_id=None,
                        action_diff=None,
                        source_path=f"workstreams/{workstream_id}/action-projection.json",
                    )
                )
            if ledger_revision and sidecar.get("ledger_revision") != ledger_revision:
                row_findings.append(
                    drift_finding(
                        kind="ledger-revision-mismatch",
                        repairability="non-repairable",
                        severity="warning",
                        workstream_id=workstream_id,
                        action_id=None,
                        action_diff=None,
                        source_path=f"workstreams/{workstream_id}/action-projection.json",
                    )
                )
            if not wdr_state or wdr_state.get("wdr_fingerprint") != sha256_file_id(wdr_path):
                row_findings.append(
                    drift_finding(
                        kind="wdr-lineage-mismatch",
                        repairability="non-repairable",
                        severity="warning",
                        workstream_id=workstream_id,
                        action_id=None,
                        action_diff=None,
                        source_path=f"workstreams/{workstream_id}/delivery-record.state.json",
                    )
                )
            elif (
                sidecar.get("wdr_revision") != wdr_state.get("wdr_revision")
                or sidecar.get("file_generation") != wdr_state.get("file_generation")
            ):
                row_findings.append(
                    drift_finding(
                        kind="wdr-lineage-mismatch",
                        repairability="non-repairable",
                        severity="warning",
                        workstream_id=workstream_id,
                        action_id=None,
                        action_diff=None,
                        source_path=f"workstreams/{workstream_id}/action-projection.json",
                    )
                )
            for action_id in sorted(set(expected) - set(actual)):
                row_findings.append(
                    action_drift_finding(
                        workstream_id,
                        action_id,
                        "missing-from-wdr",
                        ledger_present=True,
                        wdr_present=False,
                        ledger_revision=revision_by_action.get(action_id),
                        source_path=f"workstreams/{workstream_id}/action-projection.json",
                    )
                )
            for action_id in sorted(set(actual) - set(expected)):
                row_findings.append(
                    action_drift_finding(
                        workstream_id,
                        action_id,
                        "orphan-in-wdr",
                        ledger_present=False,
                        wdr_present=True,
                        ledger_revision=None,
                        source_path=f"workstreams/{workstream_id}/action-projection.json",
                        rendered_hash=content_hash(actual[action_id]),
                    )
                )
            for action_id in sorted(set(expected) & set(actual)):
                if actual[action_id] != expected[action_id]:
                    row_findings.append(
                        action_drift_finding(
                            workstream_id,
                            action_id,
                            "content-mismatch",
                            ledger_present=True,
                            wdr_present=True,
                            ledger_revision=revision_by_action.get(action_id),
                            source_path=f"workstreams/{workstream_id}/action-projection.json",
                            rendered_hash=content_hash(actual[action_id]),
                        )
                    )

        wdr_summary_rows = parse_wdr_action_summaries(wdr_path)
        wdr_ids = set(wdr_summary_rows)
        projected_ids = set(actual)
        for action_id in sorted(projected_ids - wdr_ids):
            if not any(item.get("action_id") == action_id for item in row_findings):
                row_findings.append(
                    action_drift_finding(
                        workstream_id,
                        action_id,
                        "missing-from-wdr",
                        ledger_present=action_id in expected,
                        wdr_present=False,
                        ledger_revision=revision_by_action.get(action_id),
                        source_path=f"workstreams/{workstream_id}/delivery-record.md",
                    )
                )
        for action_id in sorted(projected_ids & wdr_ids):
            projected_summary = str(actual[action_id].get("rendered_summary") or "")
            wdr_summaries = wdr_summary_rows[action_id]
            if (len(wdr_summaries) != 1 or wdr_summaries[0] != projected_summary) and not any(
                    item.get("action_id") == action_id
                    and item.get("action_diff", {}).get("drift_kind") == "content-mismatch"
                    and item.get("source_path", "").endswith("delivery-record.md")
                    for item in row_findings
            ):
                row_findings.append(
                    action_drift_finding(
                        workstream_id,
                        action_id,
                        "content-mismatch",
                        ledger_present=action_id in expected,
                        wdr_present=True,
                        ledger_revision=revision_by_action.get(action_id),
                        source_path=f"workstreams/{workstream_id}/delivery-record.md",
                        rendered_hash=content_hash(wdr_summaries),
                    )
                )
        for action_id in sorted(wdr_ids - projected_ids):
            if not any(item.get("action_id") == action_id for item in row_findings):
                row_findings.append(
                    action_drift_finding(
                        workstream_id,
                        action_id,
                        "orphan-in-wdr",
                        ledger_present=action_id in expected,
                        wdr_present=True,
                        ledger_revision=revision_by_action.get(action_id),
                        source_path=f"workstreams/{workstream_id}/delivery-record.md",
                    )
                )
        findings.extend(row_findings)
        rows.append(
            {
                "workstream_id": workstream_id,
                "status": "in-sync" if not row_findings else "drift",
                "finding_ids": sorted(item["finding_id"] for item in row_findings),
                "expected_action_ids": sorted(expected),
                "projected_action_ids": sorted(actual),
                "wdr_action_ids": sorted(wdr_ids),
                "wdr_revision": wdr_state.get("wdr_revision") if isinstance(wdr_state.get("wdr_revision"), int) else 1,
                "file_generation": wdr_state.get("file_generation") if isinstance(wdr_state.get("file_generation"), int) else 1,
                "wdr_fingerprint": sha256_file_id(wdr_path),
                "wdr_state_fingerprint": sha256_file_id(wdr_state_path),
                "sidecar_fingerprint": sha256_file_id(sidecar_path),
            }
        )

    findings = sorted(
        {item["finding_id"]: item for item in findings}.values(),
        key=lambda item: item["finding_id"],
    )
    verdict = {
        "schema_version": "1.0.0",
        "selected_workstreams": workstream_ids,
        "ledger_fingerprint": ledger_fingerprint,
        "ledger_revision": ledger_revision,
        "rows": rows,
        "findings": findings,
        "overall": (
            "in-sync"
            if workstream_ids and not findings and all(row["status"] == "in-sync" for row in rows)
            else "drift"
        ),
    }
    verdict["verdict_id"] = content_hash(verdict)
    return verdict


def expected_projection_record(action: dict[str, Any], revisions: dict[str, int]) -> dict[str, Any] | None:
    action_id = str(action.get("action_id") or "")
    revision = revisions.get(action_id)
    status = str(action.get("status") or "").lower()
    if not action_id or revision is None or status not in ACTIVE_ACTION_STATUSES:
        return None
    owner = str(action.get("owner") or "TBD")
    text = str(action.get("action") or "TBD")
    due = str(action.get("due_or_trigger") or "TBD")
    affected = split_id_list(action.get("affected_workstreams"))
    routing_scope = normalize_workstream_id(str(action.get("workstream") or "")) or "program"
    return {
        "action_id": action_id,
        "owner": owner,
        "action": text,
        "due_trigger": due,
        "status": status,
        "action_revision": revision,
        "routing_scope_id": routing_scope,
        "affected_workstreams": sorted(affected),
        "rendered_summary": f"[action_id:{action_id}] {owner}: {text} (due: {due})",
    }


def action_targets_workstream(action: dict[str, Any], workstream_id: str) -> bool:
    routing = normalize_workstream_id(str(action.get("workstream") or ""))
    return routing == workstream_id or workstream_id in split_id_list(action.get("affected_workstreams"))


def split_id_list(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"\s*[,;]\s*", str(value or ""))
    return sorted({normalize_workstream_id(str(item)) for item in raw if normalize_workstream_id(str(item))})


def parse_action_revisions(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return {}
    headers = [re.sub(r"\s+", " ", cell.strip()).lower() for cell in split_markdown_row(lines[0])]
    revisions: dict[str, int] = {}
    for line in lines[1:]:
        cells = split_markdown_row(line)
        if len(cells) != len(headers) or all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        row = dict(zip(headers, cells, strict=True))
        action_id = row.get("action id", "").strip()
        raw_revision = row.get("action revision", "").strip() or "1"
        if action_id and raw_revision.isdigit() and int(raw_revision) >= 1:
            revisions[action_id] = int(raw_revision)
    return revisions


def parse_wdr_action_ids(path: Path) -> set[str]:
    return set(parse_wdr_action_summaries(path))


def parse_wdr_action_summaries(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"^\s*-\s*Next actions\s*:\s*(.*)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return {}
    value = match.group(1).strip()
    markers = list(re.finditer(r"\[action_id:([^\]]+)\]", value))
    summaries: dict[str, list[str]] = defaultdict(list)
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        summary = value[marker.start():end].strip().rstrip(";").strip()
        summaries[marker.group(1).strip()].append(summary)
    return dict(summaries)


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256_file_id(path: Path) -> str:
    return "sha256:" + (hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "0" * 64)


def content_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def drift_finding(
    *,
    kind: str,
    repairability: str,
    severity: str,
    workstream_id: str,
    action_id: str | None,
    action_diff: dict[str, Any] | None,
    source_path: str,
) -> dict[str, Any]:
    body = {
        "kind": kind,
        "repairability": repairability,
        "severity": severity,
        "workstream_id": workstream_id,
        "action_id": action_id,
        "action_diff": action_diff,
        "source_path": source_path,
        "source_line": None,
    }
    return {"finding_id": content_hash(body), **body}


def action_drift_finding(
    workstream_id: str,
    action_id: str,
    drift_kind: str,
    *,
    ledger_present: bool,
    wdr_present: bool,
    ledger_revision: int | None,
    source_path: str,
    rendered_hash: str | None = None,
) -> dict[str, Any]:
    return drift_finding(
        kind="action-projection-drift",
        repairability="repairable",
        severity="blocked",
        workstream_id=workstream_id,
        action_id=action_id,
        action_diff={
            "action_id": action_id,
            "drift_kind": drift_kind,
            "ledger_present": ledger_present,
            "wdr_present": wdr_present,
            "ledger_revision": ledger_revision,
            "wdr_rendered_sha256": rendered_hash,
        },
        source_path=source_path,
    )


def build_repair_contract(audit_id: str, verdict: dict[str, Any], memory_root: Path) -> dict[str, Any]:
    repairable = [
        item
        for item in verdict.get("findings", [])
        if item.get("repairability") == "repairable" and item.get("action_id")
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in repairable:
        grouped[str(finding["workstream_id"])].append(finding)
    batches: list[dict[str, Any]] = []
    for workstream_id in sorted(grouped):
        findings = sorted(grouped[workstream_id], key=lambda item: item["finding_id"])
        action_ids = sorted({str(item["action_id"]) for item in findings})
        row = next(
            (item for item in verdict.get("rows", []) if item.get("workstream_id") == workstream_id),
            {},
        )
        command = {
            "workflow": "adp-status-sync",
            "workstream_id": workstream_id,
            "operation": "refresh_actions",
            "expected_wdr_revision": row.get("wdr_revision", 1),
            "expected_file_generation": row.get("file_generation", 1),
            "action_ids": action_ids,
        }
        action_revisions = []
        for action_id in action_ids:
            matching = [item for item in findings if item.get("action_id") == action_id]
            revisions = {
                item.get("action_diff", {}).get("ledger_revision")
                for item in matching
                if isinstance(item.get("action_diff"), dict)
                and item.get("action_diff", {}).get("ledger_revision") is not None
            }
            revision = next(iter(revisions)) if len(revisions) == 1 else None
            action_revisions.append(
                {
                    "action_id": action_id,
                    "expected_present": revision is not None,
                    "revision": revision,
                }
            )
        source_paths = [
            "actions/action-ledger.md",
            "actions/action-ledger.state.json",
            f"workstreams/{workstream_id}/delivery-record.md",
            f"workstreams/{workstream_id}/delivery-record.state.json",
            f"workstreams/{workstream_id}/action-projection.json",
        ]
        root_instance_id = "ri_" + hashlib.sha256(str(memory_root.resolve()).encode("utf-8")).hexdigest()
        source_records = [
            {
                "root_instance_id": root_instance_id,
                "path": path,
                "fingerprint": sha256_file_id(memory_root / path),
            }
            for path in source_paths
        ]
        fact_generation = load_optional_json(memory_root / "state/fact-generation.json").get("generation", 1)
        if not isinstance(fact_generation, int) or isinstance(fact_generation, bool) or fact_generation < 1:
            fact_generation = 1
        batch_body = {
            "based_on_audit_id": audit_id,
            "finding_ids": [item["finding_id"] for item in findings],
            "command": command,
            "read_set": {
                "ledger_fingerprint": verdict.get("ledger_fingerprint"),
                "ledger_revision": verdict.get("ledger_revision"),
                "action_revisions": action_revisions,
                "wdr_revisions": [
                    {
                        "workstream_id": workstream_id,
                        "wdr_revision": row.get("wdr_revision", 1),
                        "file_generation": row.get("file_generation", 1),
                        "fingerprint": row.get("wdr_fingerprint"),
                    }
                ],
                "source_records": source_records,
                "fact_generation": fact_generation,
            },
        }
        batch_id = content_hash(batch_body)
        batch = {"batch_id": batch_id, **batch_body}
        batch["batch_digest"] = content_hash(batch)
        batches.append(batch)
    mapped_findings = []
    batch_by_finding = {
        finding_id: batch["batch_id"]
        for batch in batches
        for finding_id in batch["finding_ids"]
    }
    for finding in verdict.get("findings", []):
        action_ids = [finding["action_id"]] if finding.get("action_id") else []
        refs = [{"entity_type": "workstream", "id": finding["workstream_id"]}]
        refs.extend({"entity_type": "action", "id": action_id} for action_id in action_ids)
        mapped_findings.append(
            {
                "finding_id": finding["finding_id"],
                "kind": finding["kind"],
                "severity": finding["severity"],
                "workflow": "adp-status-sync",
                "workstream_id": finding["workstream_id"],
                "operation": "refresh_actions",
                "entity_refs": refs,
                "action_ids": action_ids,
                "source_path": finding["source_path"],
                "source_line": finding["source_line"],
                "repair_batch_id": batch_by_finding.get(finding["finding_id"]),
            }
        )
    return {
        "schema_version": "2.0.0",
        "audit_id": audit_id,
        "drift_verdict_id": verdict["verdict_id"],
        "findings": mapped_findings,
        "repair_batches": batches,
    }


def build_audit(
    prepass: dict[str, Any],
    project_root: Path,
    memory_root: Path,
    scenario: str,
    as_of: date,
    max_age_days: int,
    config_script: Path,
    baseline_script: Path,
) -> dict[str, Any]:
    sources = list(prepass.get("sources_read", []))
    workstreams = list(prepass.get("workstreams", []))
    ledger_actions = list(prepass.get("ledger_actions", []))
    gaps = list(prepass.get("gaps", []))

    freshness = audit_freshness(prepass, memory_root, as_of, max_age_days)
    requested_scopes = prepass.get("scope", {}).get("workstreams_requested", []) if isinstance(prepass.get("scope"), dict) else []
    skip_wdr_registry = bool(requested_scopes) and not prepass.get("registered_workstreams") and bool(prepass.get("virtual_scopes"))
    selected_scope_ids = {
        str(item.get("scope_id"))
        for item in prepass.get("virtual_scopes", [])
        if isinstance(item, dict)
    } | {str(value) for value in prepass.get("registered_workstreams", [])}
    vnext = audit_vnext_inputs(
        project_root,
        memory_root,
        as_of,
        config_script,
        baseline_script,
        skip_wdr_registry=skip_wdr_registry,
        selected_scope_ids=selected_scope_ids or None,
    )
    document_locale = str(vnext.get("locale") or "en")
    completeness = audit_completeness(prepass, memory_root, as_of, max_age_days, document_locale)
    consistency = audit_consistency(prepass, freshness)
    closure = audit_closure(prepass, memory_root, as_of, document_locale)
    merge_quality = audit_merge_quality(prepass)
    action_projection = audit_action_projection_drift(memory_root, prepass)

    contract_findings = canonical_findings(
        freshness,
        completeness,
        consistency,
        closure,
        merge_quality,
    )
    if scenario == "roadmap":
        completeness_warnings = [
            {**finding, "severity": "warning"}
            for finding in contract_findings["blocking_gaps"]
            if finding.get("kind") == "completeness"
        ]
        contract_findings["blocking_gaps"] = [
            finding
            for finding in contract_findings["blocking_gaps"]
            if finding.get("kind") != "completeness"
        ]
        contract_findings["warnings"].extend(completeness_warnings)
    for finding in canonicalize_vnext_findings(vnext["findings"]):
        group = "blocking_gaps" if finding["severity"] == "blocking" else "warnings"
        contract_findings[group].append(finding)
    for drift_finding in action_projection["findings"]:
        severity = "blocking" if drift_finding["severity"] == "blocked" else "warning"
        group = "blocking_gaps" if severity == "blocking" else "warnings"
        contract_findings[group].append(
            canonical_finding(
                {
                    **drift_finding,
                    "gap": drift_finding["kind"],
                    "source": drift_finding["source_path"],
                    "workstream": drift_finding["workstream_id"],
                    "recommended_workflow": "adp-status-sync",
                    "execution_disposition": "blocked" if severity == "blocking" else "degraded",
                },
                severity,
                "action_projection_drift",
            )
        )
    for group in ["blocking_gaps", "warnings"]:
        contract_findings[group] = dedupe_canonical_findings(contract_findings[group])
    warning_findings = warning_findings_from_groups(contract_findings)
    blocking_count = len(contract_findings["blocking_gaps"]) + len(contract_findings["conflicts"])
    warning_count = len(warning_findings)
    audit_status = "blocked" if blocking_count else ("warning" if warning_count else "pass")
    all_findings = [item for group in contract_findings.values() for item in group]
    if any(item.get("execution_disposition") == "blocked" for item in all_findings):
        execution_disposition = "blocked"
    elif all_findings:
        execution_disposition = "degraded"
    else:
        execution_disposition = "ready"
    recommended = recommend_workflows(contract_findings, prepass)
    source_inventory_items = canonical_source_inventory(sources, prepass.get("missing_sources", []))

    source_fingerprints = collect_source_fingerprints(memory_root, sources, vnext["source_fingerprints"])
    scope_contract = vnext.get("scope_contract", {})
    registered_workstreams = prepass.get("registered_workstreams")
    if not isinstance(registered_workstreams, list):
        registered_workstreams = [str(item.get("id", "")) for item in workstreams if item.get("id")]
    virtual_scopes = prepass.get("virtual_scopes")
    if not isinstance(virtual_scopes, list):
        virtual_scopes = []
    audit = {
        "ok": True,
        "audit_type": "input",
        "audit_schema_version": 1,
        "schema_version": 1,
        "prepass_schema_version": prepass.get("schema_version"),
        "audit_status": audit_status,
        "execution_disposition": execution_disposition,
        "safe_to_generate": execution_disposition != "blocked",
        "safe_to_generate_green_report": execution_disposition == "ready" and audit_status == "pass",
        "report_confidence": {"pass": "high", "warning": "medium", "blocked": "low"}[audit_status],
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "scenario": scenario,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "prepass": {
            "schema_version": prepass.get("schema_version"),
            "capability": prepass.get("capability", ""),
            "scope": prepass.get("scope", {}),
            "counts": prepass.get("counts", {}),
        },
        "scope_contract": scope_contract,
        "registered_workstreams": registered_workstreams,
        "virtual_scopes": virtual_scopes,
        "migration_warnings": scope_contract.get("migration_warnings", []) if isinstance(scope_contract, dict) else [],
        "source_inventory": {
            "sources_read": sources,
            "missing_sources": list(prepass.get("missing_sources", [])),
            "workstreams": [item.get("id", "") for item in workstreams],
            "registered_workstreams": registered_workstreams,
            "virtual_scopes": virtual_scopes,
        },
        "source_inventory_items": source_inventory_items,
        "source_fingerprints": source_fingerprints,
        "baseline_revision": vnext["baseline_revision"],
        "baseline_fingerprint": vnext["baseline_fingerprint"],
        "locale": vnext["locale"],
        "locale_fallback": vnext["locale_fallback"],
        "effective_config": vnext["effective_config"],
        **contract_findings,
        "warning_findings": warning_findings,
        "merge_review_evidence": {
            "action_field_collisions": merge_quality["action_field_collision_evidence"],
            "shared_references": merge_quality["shared_reference_evidence"],
            "readiness_gap_pairs": merge_quality["readiness_gap_evidence"],
        },
        "findings": {
            "freshness": freshness,
            "completeness": completeness,
            "consistency": consistency,
            "closure": closure,
            "merge_quality": merge_quality,
            "action_projection": action_projection,
        },
        "action_projection_drift": action_projection,
        "counts": {
            "sources_read": len(sources),
            "missing_sources": len(prepass.get("missing_sources", [])),
            "workstreams": len(workstreams),
            "virtual_scopes": len(virtual_scopes),
            "active_ledger_actions": sum(
                str(item.get("status", "")).lower() in ACTIVE_ACTION_STATUSES
                for item in ledger_actions
            ),
            "prepass_gaps": len(gaps),
            "blocking_findings": blocking_count,
            "warning_findings": warning_count,
            "action_projection_drift": len(action_projection["findings"]),
        },
        "recommended_workflows": recommended,
    }
    audit["input_audit_id"] = stable_input_audit_id(audit)
    audit["repair_contract"] = build_repair_contract(
        audit["input_audit_id"],
        action_projection,
        memory_root,
    )
    repair_batch_by_finding = {
        finding_id: batch["batch_id"]
        for batch in audit["repair_contract"]["repair_batches"]
        for finding_id in batch["finding_ids"]
    }
    for group in ["blocking_gaps", "warnings"]:
        for finding in audit[group]:
            finding["repair_batch_id"] = repair_batch_by_finding.get(finding.get("finding_id"))
    audit["audit_content_hash"] = audit_content_hash(audit)
    return audit


def audit_vnext_inputs(
    project_root: Path,
    memory_root: Path,
    as_of: date,
    config_script: Path,
    baseline_script: Path,
    *,
    skip_wdr_registry: bool = False,
    selected_scope_ids: set[str] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    fingerprints: dict[str, str] = {}
    config = run_shared_json_script(config_script, [str(project_root)])
    locale = str(config.get("document_locale") or "en") if config.get("ok") else "en"
    locale_fallback = bool(config.get("ok") and "document_output_language" in config.get("fallbacks", []))
    if not config.get("ok"):
        findings.append(
            vnext_finding(
                "config.unavailable",
                "blocking",
                "blocked",
                "shared effective config could not be resolved",
                str(config_script),
                "adp-setup",
            )
        )
    elif locale_fallback:
        findings.append(
            vnext_finding(
                "locale.fallback",
                "warning",
                "degraded",
                "document output language fell back to English",
                str(config.get("value_sources", {}).get("document_output_language", "effective config")),
                "adp-setup",
            )
        )
    for source in config.get("sources_checked", []) if isinstance(config.get("sources_checked"), list) else []:
        path = Path(str(source.get("path", "")))
        if path.is_file():
            fingerprints[project_relative(project_root, path)] = file_sha256(path)

    baseline_path = memory_root / "plans" / "program-baseline.md"
    if not baseline_path.is_file():
        findings.append(
            vnext_finding(
                "baseline.missing",
                "blocking",
                "blocked",
                "approved program baseline is missing",
                rel_to_memory(memory_root, baseline_path),
                "adp-plan-baseline",
            )
        )
        return {
            "findings": findings,
            "baseline_revision": None,
            "baseline_fingerprint": "",
            "locale": locale,
            "locale_fallback": locale_fallback,
            "effective_config": public_effective_config(config),
            "source_fingerprints": fingerprints,
            "scope_contract": {},
        }

    baseline_fingerprint = file_sha256(baseline_path)
    fingerprints[rel_to_memory(memory_root, baseline_path)] = baseline_fingerprint
    validation_arguments = ["validate", str(project_root), "--baseline", str(baseline_path)]
    if skip_wdr_registry:
        validation_arguments.append("--skip-wdr-registry")
    validation = run_shared_json_script(
        baseline_script,
        validation_arguments,
        allow_nonzero=True,
    )
    if not validation.get("valid"):
        details = validation.get("findings") if isinstance(validation.get("findings"), list) else []
        summary = "; ".join(str(item.get("message", "invalid baseline")) for item in details[:3] if isinstance(item, dict))
        findings.append(
            vnext_finding(
                "baseline.invalid",
                "blocking",
                "blocked",
                summary or str(validation.get("error") or "program baseline validation failed"),
                rel_to_memory(memory_root, baseline_path),
                "adp-plan-baseline",
            )
        )
        baseline = parse_program_baseline(baseline_path, tolerate_errors=True)
    else:
        baseline = parse_program_baseline(baseline_path)
    baseline_revision = baseline.get("revision") if isinstance(baseline, dict) else validation.get("baseline_revision")
    scope_contract = validation.get("scope_contract") if isinstance(validation.get("scope_contract"), dict) else {}
    for warning in scope_contract.get("migration_warnings", []) if isinstance(scope_contract, dict) else []:
        findings.append(
            vnext_finding(
                str(warning.get("code") or "ADP-LEGACY-VIRTUAL-SCOPE-WDR"),
                "warning",
                "degraded",
                str(warning.get("risk") or "legacy virtual-scope WDR requires manual migration review"),
                str(warning.get("directory") or "workstreams/program/"),
                "adp-state-audit",
                workstream="program",
            )
        )
    if isinstance(baseline, dict):
        mapping_findings, wdr_fingerprints = audit_plan_actual_mapping(
            baseline,
            memory_root,
            as_of,
            scope_contract,
            selected_scope_ids=selected_scope_ids,
            scan_wdrs=not skip_wdr_registry,
        )
        findings.extend(mapping_findings)
        fingerprints.update(wdr_fingerprints)
    return {
        "findings": findings,
        "baseline_revision": baseline_revision,
        "baseline_fingerprint": baseline_fingerprint,
        "locale": locale,
        "locale_fallback": locale_fallback,
        "effective_config": public_effective_config(config),
        "source_fingerprints": fingerprints,
        "scope_contract": scope_contract,
    }


def vnext_finding(
    code: str,
    severity: str,
    disposition: str,
    summary: str,
    source: str,
    workflow: str,
    *,
    workstream: str = "",
    gap_type: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "execution_disposition": disposition,
        "summary": summary,
        "source": source,
        "recommended_workflow": workflow,
        "workstream": workstream,
        "gap_type": gap_type or code,
        "category": "vnext_input",
    }


def run_shared_json_script(script: Path, arguments: list[str], allow_nonzero: bool = False) -> dict[str, Any]:
    if not script.is_file():
        return {"ok": False, "error": f"required shared script not found: {script}"}
    completed = subprocess.run([sys.executable, str(script), *arguments], capture_output=True)
    stdout = decode_process_output(completed.stdout)
    stderr = decode_process_output(completed.stderr)
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": (stderr or stdout or "shared script emitted invalid JSON").strip()}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "shared script JSON must be an object"}
    if completed.returncode and not allow_nonzero:
        payload["ok"] = False
        payload.setdefault("error", stderr.strip() or f"shared script exited {completed.returncode}")
    return payload


def public_effective_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_locale": str(config.get("document_locale") or "en"),
        "communication_locale": str(config.get("communication_locale") or "en"),
        "fallbacks": list(config.get("fallbacks", [])) if isinstance(config.get("fallbacks"), list) else [],
        "value_sources": dict(config.get("value_sources", {})) if isinstance(config.get("value_sources"), dict) else {},
        "warnings": list(config.get("warnings", [])) if isinstance(config.get("warnings"), list) else [],
    }


def parse_program_baseline(path: Path, tolerate_errors: bool = False) -> dict[str, Any]:
    try:
        text = read_text(path)
        marker_index = text.find(BASELINE_MARKER)
        if marker_index < 0:
            raise ValueError(f"baseline marker missing: {BASELINE_MARKER}")
        match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker_index:], re.DOTALL)
        if not match:
            raise ValueError("canonical baseline JSON block is missing")
        value = json.loads(match.group(1))
        if not isinstance(value, dict):
            raise ValueError("canonical baseline JSON must be an object")
        return value
    except (OSError, ValueError, json.JSONDecodeError):
        if tolerate_errors:
            return {}
        raise


def audit_plan_actual_mapping(
    baseline: dict[str, Any],
    memory_root: Path,
    as_of: date,
    scope_contract: dict[str, Any],
    *,
    selected_scope_ids: set[str] | None = None,
    scan_wdrs: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    findings: list[dict[str, Any]] = []
    fingerprints: dict[str, str] = {}
    milestones = {
        str(item.get("id")): item
        for item in baseline.get("milestones", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
        and (selected_scope_ids is None or str(item.get("workstream_id")) in selected_scope_ids)
    }
    gates = {
        str(item.get("id")): item
        for item in baseline.get("gates", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    plan_items = {**gates, **milestones}
    baseline_revision = baseline.get("revision")
    mapped_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    virtual_scope_ids = {
        str(item.get("scope_id"))
        for item in scope_contract.get("virtual_scopes", [])
        if isinstance(item, dict)
    }
    virtual_normalized = {normalize_workstream_id(value) for value in virtual_scope_ids}
    records = sorted((memory_root / "workstreams").glob("*/delivery-record.md")) if scan_wdrs else []
    for record in records:
        workstream_id = normalize_workstream_id(record.parent.name)
        alias_sidecar = record.parent / "workstream-alias.json"
        retired_alias = False
        if alias_sidecar.is_file():
            try:
                alias_payload = load_json(alias_sidecar)
                retired_alias = bool(
                    isinstance(alias_payload, dict)
                    and alias_payload.get("status") == "retired-alias"
                    and alias_payload.get("alias_workstream_id") == record.parent.name
                    and isinstance(alias_payload.get("canonical_workstream_id"), str)
                )
            except (OSError, json.JSONDecodeError):
                retired_alias = False
        if workstream_id in virtual_normalized or retired_alias:
            continue
        rel = rel_to_memory(memory_root, record)
        fingerprints[rel] = file_sha256(record)
        for row in roadmap_rows(record):
            milestone_id = row_value(row, "milestone id", "milestone_id", "id")
            status = normalize_status(row_value(row, "status"))
            forecast = row_value(row, "forecast", "forecast date")
            actual = row_value(row, "actual", "actual date", "completed")
            has_actual_state = is_meaningful(forecast) or is_meaningful(actual) or status in {"at-risk", "done", "blocked"}
            if not is_meaningful(milestone_id) and has_actual_state:
                findings.append(
                    vnext_finding(
                        "actual.unmapped",
                        "blocking",
                        "blocked",
                        "roadmap actual state has no baseline milestone ID",
                        rel,
                        "adp-status-sync",
                        workstream=workstream_id,
                    )
                )
                continue
            if not is_meaningful(milestone_id):
                continue
            baseline_item = plan_items.get(milestone_id)
            if baseline_item is None and has_actual_state:
                findings.append(
                    vnext_finding(
                        "actual.unmapped",
                        "blocking",
                        "blocked",
                        f"actual state references unknown baseline milestone {milestone_id}",
                        rel,
                        "adp-status-sync",
                        workstream=workstream_id,
                    )
                )
                continue
            if baseline_item is not None:
                row["__source"] = rel
                row["__workstream"] = workstream_id
                mapped_rows[milestone_id].append(row)
                expected_workstream = baseline_item_workstream(baseline_item)
                if expected_workstream and expected_workstream != workstream_id:
                    findings.append(
                        vnext_finding(
                            "actual.workstream_mismatch",
                            "blocking",
                            "blocked",
                            f"milestone {milestone_id} belongs to {expected_workstream}, not {workstream_id}",
                            rel,
                            "adp-status-sync",
                            workstream=workstream_id,
                        )
                    )
                row_revision = row_value(row, "baseline revision", "baseline_revision")
                if has_actual_state and str(row_revision) != str(baseline_revision):
                    findings.append(
                        vnext_finding(
                            "actual.baseline_revision_mismatch",
                            "blocking",
                            "blocked",
                            f"milestone {milestone_id} actual state declares baseline revision {row_revision or 'missing'}, expected {baseline_revision}",
                            rel,
                            "adp-status-sync",
                            workstream=workstream_id,
                        )
                    )
            for field_name, value in [("forecast", forecast), ("actual", actual)]:
                if is_meaningful(value) and parse_date(value) is None:
                    findings.append(
                        vnext_finding(
                            f"actual.{field_name}_invalid",
                            "blocking",
                            "blocked",
                            f"milestone {milestone_id} has invalid {field_name} date {value!r}",
                            rel,
                            "adp-status-sync",
                            workstream=workstream_id,
                        )
                    )

    for milestone_id, rows in mapped_rows.items():
        if len(rows) > 1:
            findings.append(
                vnext_finding(
                    "actual.duplicate_mapping",
                    "blocking",
                    "blocked",
                    f"milestone {milestone_id} has {len(rows)} actual-state rows",
                    ", ".join(str(row.get("__source", "")) for row in rows),
                    "adp-status-sync",
                    workstream=str(rows[0].get("__workstream", "")),
                )
            )

    for milestone_id, milestone in milestones.items():
        if str(milestone.get("workstream_id")) in virtual_scope_ids:
            continue
        planned = parse_date(milestone.get("planned_date"))
        raw_tolerance = milestone.get("tolerance_days", baseline.get("default_tolerance_days", 0))
        tolerance = raw_tolerance if isinstance(raw_tolerance, int) and not isinstance(raw_tolerance, bool) else 0
        actual_due = planned + timedelta(days=tolerance) if planned is not None else None
        if actual_due is None or as_of <= actual_due:
            continue
        expected_workstream = normalize_workstream_id(str(milestone.get("workstream_id", "")))
        matching = [
            row
            for row in mapped_rows.get(milestone_id, [])
            if row.get("__workstream") == expected_workstream
        ]
        actual = row_value(matching[0], "actual", "actual date", "completed") if matching else ""
        if not is_meaningful(actual):
            findings.append(
                vnext_finding(
                    "actual.missing",
                    "warning",
                    "degraded",
                    f"milestone {milestone_id} passed actual-date boundary {actual_due.isoformat()} without an actual date",
                    str(matching[0].get("__source")) if matching else f"workstreams/{expected_workstream}/delivery-record.md",
                    "adp-status-sync",
                    workstream=expected_workstream,
                )
            )
    return findings, fingerprints


def baseline_item_workstream(item: dict[str, Any]) -> str:
    workstream_id = normalize_workstream_id(str(item.get("workstream_id", "")))
    if workstream_id:
        return workstream_id
    lane = item.get("lane") if isinstance(item.get("lane"), dict) else {}
    if lane.get("lane_type") == "workstream":
        return normalize_workstream_id(str(lane.get("lane_id", "")))
    return ""


def roadmap_rows(path: Path) -> list[dict[str, str]]:
    lines = section_text(read_text(path), "Roadmap").splitlines()
    table_start = next((index for index, line in enumerate(lines) if line.strip().startswith("|")), None)
    if table_start is None or table_start + 1 >= len(lines):
        return []
    headers = [normalize_text_key(value) for value in split_markdown_row(lines[table_start])]
    rows: list[dict[str, str]] = []
    for line in lines[table_start + 2 :]:
        if not line.strip().startswith("|"):
            break
        cells = split_markdown_row(line)
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def split_markdown_row(line: str) -> list[str]:
    text = line.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def row_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(normalize_text_key(name), "").strip()
        if value:
            return value
    return ""


def normalize_workstream_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")


def canonicalize_vnext_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in items:
        finding = canonical_finding(item, str(item.get("severity") or "warning"), "input_contract")
        finding["code"] = str(item.get("code") or "input.unknown")
        finding["execution_disposition"] = str(item.get("execution_disposition") or "degraded")
        findings.append(finding)
    return findings


def collect_source_fingerprints(
    memory_root: Path,
    sources: list[dict[str, Any]],
    initial: dict[str, str],
) -> dict[str, str]:
    fingerprints = dict(initial)
    for source in sources:
        rel = str(source.get("path", ""))
        if is_derived_lineage_path(rel):
            continue
        path = resolve_contained_path(memory_root, rel)
        if path is not None and path.is_file():
            fingerprints[rel] = file_sha256(path)
    return dict(sorted(fingerprints.items()))


def is_derived_lineage_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized.startswith("views/") or normalized.startswith("snapshots/") or normalized.startswith("audits/")


def stable_input_audit_id(audit: dict[str, Any]) -> str:
    identity = {
        "audit_type": audit.get("audit_type"),
        "audit_schema_version": audit.get("audit_schema_version"),
        "schema_version": audit.get("schema_version"),
        "generator_version": audit.get("generator_version"),
        "scenario": audit.get("scenario"),
        "as_of": audit.get("as_of"),
        "baseline_revision": audit.get("baseline_revision"),
        "baseline_fingerprint": audit.get("baseline_fingerprint"),
        "locale": audit.get("locale"),
        "locale_fallback": audit.get("locale_fallback"),
        "execution_disposition": audit.get("execution_disposition"),
        "audit_status": audit.get("audit_status"),
        "safe_to_generate": audit.get("safe_to_generate"),
        "safe_to_generate_green_report": audit.get("safe_to_generate_green_report"),
        "report_confidence": audit.get("report_confidence"),
        "recommended_workflows": audit.get("recommended_workflows", []),
        "source_fingerprints": audit.get("source_fingerprints", {}),
        "findings": sorted(
            canonical_finding_identity(item)
            for group in ["blocking_gaps", "warnings", "duplicate_candidates", "overlap_claims", "conflicts", "stale_items"]
            for item in audit.get(group, [])
            if isinstance(item, dict)
        ),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"input-audit-{digest[:16]}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprints_equal(left: Any, right: Any) -> bool:
    def normalized(value: Any) -> str:
        return str(value or "").strip().lower().removeprefix("sha256:")

    left_value = normalized(left)
    right_value = normalized(right)
    return bool(re.fullmatch(r"[0-9a-f]{64}", left_value)) and left_value == right_value


def canonical_finding_identity(finding: dict[str, Any]) -> str:
    value = {
        key: finding.get(key)
        for key in [
            "id",
            "code",
            "severity",
            "execution_disposition",
            "kind",
            "source_type",
            "sources",
            "workstreams",
            "owner",
            "summary",
            "category",
            "gap_type",
            "recommended_workflow",
            "source_path",
            "source_line",
        ]
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def audit_content_hash(audit: dict[str, Any]) -> str:
    payload = {key: value for key, value in audit.items() if key not in {"audit_content_hash", "outputs"}}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_input_audit_integrity(audit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "audit_type": "input",
        "audit_schema_version": 1,
        "schema_version": 1,
    }
    for field_name, expected in required.items():
        if audit.get(field_name) != expected:
            errors.append(f"{field_name} must be {expected!r}")
    for field_name in [
        "input_audit_id",
        "audit_content_hash",
        "generator_version",
        "scenario",
        "as_of",
        "execution_disposition",
        "audit_status",
        "safe_to_generate",
        "safe_to_generate_green_report",
        "report_confidence",
        "baseline_revision",
        "locale",
        "locale_fallback",
        "source_fingerprints",
        "blocking_gaps",
        "warnings",
        "recommended_workflows",
    ]:
        if field_name not in audit:
            errors.append(f"{field_name} is required")
    if "source_fingerprints" in audit and not isinstance(audit["source_fingerprints"], dict):
        errors.append("source_fingerprints must be an object")
    if "locale_fallback" in audit and not isinstance(audit["locale_fallback"], bool):
        errors.append("locale_fallback must be boolean")
    if audit.get("execution_disposition") not in {"ready", "degraded", "blocked"}:
        errors.append("execution_disposition is invalid")
    warning_findings = audit.get("warning_findings")
    if warning_findings is not None:
        if not isinstance(warning_findings, list):
            errors.append("warning_findings must be an array")
        else:
            finding_ids = [item.get("finding_id") for item in warning_findings if isinstance(item, dict)]
            if len(finding_ids) != len(warning_findings) or any(not isinstance(value, str) or not value for value in finding_ids):
                errors.append("warning_findings entries require finding_id")
            elif len(finding_ids) != len(set(finding_ids)):
                errors.append("warning_findings finding_id values must be unique")
            counts = audit.get("counts") if isinstance(audit.get("counts"), dict) else {}
            if counts.get("warning_findings") != len(warning_findings):
                errors.append("counts.warning_findings must equal warning_findings length")
    if not errors and stable_input_audit_id(audit) != audit.get("input_audit_id"):
        errors.append("input_audit_id does not match canonical audit content")
    if not errors and audit_content_hash(audit) != audit.get("audit_content_hash"):
        errors.append("audit_content_hash does not match stored audit content")
    return errors


def project_relative(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def audit_freshness(
    prepass: dict[str, Any],
    memory_root: Path,
    as_of: date,
    max_age_days: int,
) -> dict[str, list[dict[str, Any]]]:
    blocking_gaps: list[dict[str, Any]] = []
    stale_workstreams: list[dict[str, Any]] = []
    for gap in prepass_gaps(prepass, category="freshness"):
        item = prepass_gap_finding(gap, "freshness")
        (blocking_gaps if gap.get("blocking") else stale_workstreams).append(item)

    stale_actions = []
    for action in prepass.get("ledger_actions", []):
        status = str(action.get("status", "")).lower()
        if status not in ACTIVE_ACTION_STATUSES:
            continue
        last_updated = str(action.get("last_updated", "")).strip()
        parsed = parse_date(last_updated)
        reason = ""
        if not is_meaningful(last_updated):
            reason = "last updated is missing"
        elif parsed is None:
            reason = "last updated is unparseable"
        elif (as_of - parsed).days > max_age_days:
            reason = f"last updated is older than {max_age_days} days"
        if reason:
            stale_actions.append(action_item(action, reason, "freshness"))

    views_requiring_refresh = audit_views_requiring_refresh(prepass, memory_root)
    return {
        "blocking_gaps": blocking_gaps,
        "stale_sources": [],
        "stale_workstreams": stale_workstreams,
        "stale_actions": stale_actions,
        "views_requiring_refresh": views_requiring_refresh,
    }


def audit_views_requiring_refresh(prepass: dict[str, Any], memory_root: Path) -> list[dict[str, Any]]:
    sources = list(prepass.get("sources_read", []))
    results: list[dict[str, Any]] = []
    for source in sources:
        rel = str(source.get("path", ""))
        if not rel.startswith("views/"):
            continue
        path = resolve_contained_path(memory_root, rel)
        reasons: list[str] = []
        owner = VIEW_OWNER_WORKFLOWS.get(rel, "owning view workflow")
        if path is None:
            results.append(
                {
                    "path": rel,
                    "reason": "view path escapes the ADP memory root",
                    "recommended_workflow": owner,
                    "category": "freshness",
                }
            )
            continue
        if path.exists() and view_has_explicit_placeholder(path):
            reasons.append("view is still an ungenerated placeholder template")
        if path.exists():
            reasons.extend(view_lineage_gaps(path, rel, source, sources, memory_root))
        if reasons:
            results.append(
                {
                    "path": rel,
                    "reason": "; ".join(reasons),
                    "recommended_workflow": owner,
                    "category": "freshness",
                }
            )
    for rel in ["views/project-lead.md", "views/weekly-report.md"]:
        path = memory_root / rel
        if path.exists() and view_has_explicit_placeholder(path) and not any(item["path"] == rel for item in results):
            results.append(
                {
                    "path": rel,
                    "reason": "view is still an ungenerated placeholder template",
                    "recommended_workflow": VIEW_OWNER_WORKFLOWS[rel],
                    "category": "freshness",
                }
            )
    return results


def audit_completeness(
    prepass: dict[str, Any],
    memory_root: Path,
    as_of: date,
    max_age_days: int,
    document_locale: str = "en",
) -> dict[str, list[dict[str, Any]]]:
    blocking_gaps: list[dict[str, Any]] = []
    non_blocking_gaps: list[dict[str, Any]] = []
    missing_owner_items: list[dict[str, Any]] = []
    missing_evidence_items: list[dict[str, Any]] = []

    for gap in prepass_gaps(prepass):
        category = str(gap.get("category", ""))
        if category != "completeness":
            continue
        item = prepass_gap_finding(gap, category)
        if bool(gap.get("blocking")):
            blocking_gaps.append(item)
        else:
            non_blocking_gaps.append(item)
        field_name = str(gap.get("field", ""))
        if field_name in {"owner", "business_owner"}:
            missing_owner_items.append(item)
        if field_name in {"evidence", "readiness"}:
            missing_evidence_items.append(item)

    for source in prepass.get("missing_sources", []):
        item = {"source": source, "gap": "expected ADP source file is missing", "category": "missing"}
        blocking_gaps.append(item)

    for action in prepass.get("ledger_actions", []):
        for gap in action_field_gaps(action, as_of, max_age_days):
            item = action_item(action, gap["gap"], "completeness")
            item["field"] = gap["field"]
            item["gap_type"] = gap["gap_type"]
            blocking_gaps.append(item)
            if gap["field"] == "owner":
                missing_owner_items.append(item)

    packet_gaps, packet_owner_gaps = business_packet_field_gaps(memory_root, document_locale)
    blocking_gaps.extend(packet_gaps)
    missing_owner_items.extend(packet_owner_gaps)

    return {
        "blocking_gaps": blocking_gaps,
        "non_blocking_gaps": non_blocking_gaps,
        "missing_owner_items": missing_owner_items,
        "missing_evidence_items": missing_evidence_items,
    }


def audit_consistency(prepass: dict[str, Any], freshness: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    recommended_refreshes: list[dict[str, Any]] = []

    for gap in prepass_gaps(prepass, category="consistency"):
        item = prepass_gap_finding(gap, "consistency")
        (disagreements if gap.get("blocking") else warnings).append(item)

    for gap in prepass.get("cross_reference_gaps", []):
        item = prepass_gap_finding(gap, "consistency")
        item["relationship"] = gap.get("relationship", "")
        item["target"] = gap.get("target", "")
        (disagreements if gap.get("blocking") else warnings).append(item)

    for evidence in prepass.get("action_cross_check", []):
        for action_id in evidence.get("ledger_action_ids_without_wdr_reference", []):
            disagreements.append(
                {
                    "workstream": evidence.get("workstream", ""),
                    "action_id": action_id,
                    "gap": "open ledger action is not referenced by WDR Next actions",
                    "source": "actions/action-ledger.md",
                    "recommended_workflow": "adp-status-sync",
                    "category": "consistency",
                }
            )
        for action_id in evidence.get("wdr_action_ids_without_open_ledger_reference", []):
            disagreements.append(
                {
                    "workstream": evidence.get("workstream", ""),
                    "action_id": action_id,
                    "gap": "WDR Next actions references an action id that is not open in the ledger",
                    "source": "workstreams/*/delivery-record.md",
                    "recommended_workflow": "adp-status-sync",
                    "category": "consistency",
                }
            )

    for item in freshness.get("views_requiring_refresh", []):
        recommended_refreshes.append(
            {
                "source": item.get("path", ""),
                "gap": item.get("reason", ""),
                "recommended_workflow": item.get("recommended_workflow", ""),
                "category": "consistency",
            }
        )

    return {
        "consistency_warnings": warnings,
        "source_disagreements": disagreements,
        "recommended_refreshes": recommended_refreshes,
    }


def audit_closure(
    prepass: dict[str, Any],
    memory_root: Path,
    as_of: date,
    document_locale: str = "en",
) -> dict[str, list[dict[str, Any]]]:
    blocking_gaps: list[dict[str, Any]] = []
    non_blocking_gaps: list[dict[str, Any]] = []
    for gap in prepass_gaps(prepass, category="closure"):
        item = prepass_gap_finding(gap, "closure")
        (blocking_gaps if gap.get("blocking") else non_blocking_gaps).append(item)

    unconsumed = pending_status_sync_intakes(memory_root)
    pending_intents = pending_status_intents(memory_root)
    for intent in pending_intents:
        blocking_gaps.append(
            {
                "gap": f"status intent {intent['intent_id']} is pending",
                "action_ids": [],
                "workstream": intent.get("workstream_id", ""),
                "source": "state/status-intent-outbox.json",
                "source_path": "state/status-intent-outbox.json",
                "recommended_workflow": "adp-status-sync",
                "category": "closure",
                "gap_type": "pending-status-intent",
                "execution_disposition": "blocked",
            }
        )
    packets = business_packets(memory_root, as_of, document_locale)
    open_packets = [
        public_packet(packet)
        for packet in packets
        if not decision_status_is_terminal(packet.get("status", ""))
    ]
    escalation = []
    for packet in open_packets:
        if packet.get("overdue"):
            escalation.append(
                {
                    "source": packet["path"],
                    "reason": "business decision packet is open past its deadline",
                    "owner": packet.get("owner", "TBD"),
                    "recommended_workflow": "adp-risk-dependency-change-review",
                    "category": "closure",
                }
            )
    for action in prepass.get("ledger_actions", []):
        if str(action.get("status", "")).lower() == "blocked":
            escalation.append(action_item(action, "blocked active action needs escalation path or owner confirmation", "closure"))

    return {
        "blocking_gaps": blocking_gaps,
        "non_blocking_gaps": non_blocking_gaps,
        "unclosed_meeting_items": [],
        "open_business_packets": open_packets,
        "unconsumed_intake_files": unconsumed,
        "pending_status_intents": pending_intents,
        "escalation_candidates": escalation,
    }


def pending_status_intents(memory_root: Path) -> list[dict[str, str]]:
    outbox = load_optional_json(memory_root / "state/status-intent-outbox.json")
    rows = outbox.get("intents", []) if isinstance(outbox.get("intents"), list) else []
    pending: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("state") != "pending":
            continue
        intent = row.get("intent") if isinstance(row.get("intent"), dict) else {}
        pending.append(
            {
                "intent_id": str(row.get("intent_id") or ""),
                "workstream_id": str(intent.get("workstream_id") or ""),
                "producer": str(row.get("producer") or ""),
            }
        )
    return sorted(pending, key=lambda item: item["intent_id"])


def pending_status_sync_intakes(memory_root: Path) -> list[dict[str, Any]]:
    root = memory_root / "intake" / "status-sync"
    receipts = load_status_sync_receipts(memory_root)
    partial_closures = load_intake_partial_closure_receipts(memory_root)
    retirements = load_intake_retirement_receipts(memory_root)
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            results.append(
                {
                    "path": rel_to_memory(memory_root, path),
                    "reason": f"malformed status-sync intake JSON: {exc}",
                    "recommended_workflow": "adp-status-sync",
                    "category": "closure",
                    "status": "malformed",
                }
            )
            continue
        if not isinstance(payload, dict):
            results.append(
                {
                    "path": rel_to_memory(memory_root, path),
                    "reason": "malformed status-sync intake JSON: root must be an object",
                    "recommended_workflow": "adp-status-sync",
                    "category": "closure",
                    "status": "malformed",
                }
            )
            continue
        if not is_canonical_status_sync_intake(payload):
            continue
        lifecycle_status = intake_lifecycle_status(payload)
        if has_successful_intake_receipt(path, payload, receipts):
            continue
        if has_valid_intake_partial_closure(path, payload, partial_closures, memory_root):
            continue
        if has_valid_intake_retirement(path, payload, retirements, memory_root):
            continue
        results.append(
            {
                "path": rel_to_memory(memory_root, path),
                "reason": "pending canonical status-sync intake has no successful durable receipt",
                "recommended_workflow": "adp-status-sync",
                "category": "closure",
                "status": lifecycle_status or "pending",
            }
        )
    return results


def is_canonical_status_sync_intake(payload: dict[str, Any]) -> bool:
    control = payload.get("_control") if isinstance(payload.get("_control"), dict) else {}
    if control.get("execute_allowed") is False or payload.get("execute_allowed") is False:
        return False
    if payload.get("dry_run") is True:
        return False
    if isinstance(payload.get("ok"), bool) and str(payload.get("mode", "")).lower() in {"update", "stale"}:
        return False
    markers = [
        payload.get("status"),
        payload.get("kind"),
        payload.get("artifact_type"),
        payload.get("disposition"),
        control.get("mode"),
        control.get("disposition"),
    ]
    lifecycle = payload.get("lifecycle") if isinstance(payload.get("lifecycle"), dict) else {}
    markers.append(lifecycle.get("status"))
    if any(normalize_status(value) in NON_EXECUTABLE_INTAKE_STATES for value in markers):
        return False
    updates = payload.get("updates")
    return isinstance(updates, list) and bool(updates)


def intake_lifecycle_status(payload: dict[str, Any]) -> str:
    lifecycle = payload.get("lifecycle") if isinstance(payload.get("lifecycle"), dict) else {}
    status = payload.get("status") or lifecycle.get("status")
    if payload.get("superseded") is True:
        status = "superseded"
    return normalize_status(status)


def has_successful_intake_receipt(
    intake_path: Path,
    intake_payload: dict[str, Any],
    receipts: dict[Path, dict[str, Any]],
) -> bool:
    return any(successful_receipt_payload(receipt, intake_path, intake_payload) for receipt in receipts.values())


def load_status_sync_receipts(memory_root: Path) -> dict[Path, dict[str, Any]]:
    root = memory_root / STATUS_SYNC_RECEIPT_REL
    receipts: dict[Path, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            receipts[path.resolve()] = payload
    return receipts


def load_intake_retirement_receipts(memory_root: Path) -> dict[Path, dict[str, Any]]:
    root = memory_root / INTAKE_RETIREMENT_RECEIPT_REL
    receipts: dict[Path, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            receipts[path.resolve()] = payload
    return receipts


def load_intake_partial_closure_receipts(memory_root: Path) -> dict[Path, dict[str, Any]]:
    root = memory_root / INTAKE_PARTIAL_CLOSURE_RECEIPT_REL
    receipts: dict[Path, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            receipts[path.resolve()] = payload
    return receipts


def has_valid_intake_partial_closure(
    intake_path: Path,
    intake_payload: dict[str, Any],
    receipts: dict[Path, dict[str, Any]],
    memory_root: Path,
) -> bool:
    return any(
        valid_intake_partial_closure_receipt(receipt, intake_path, intake_payload, memory_root)
        for receipt in receipts.values()
    )


def has_valid_intake_retirement(
    intake_path: Path,
    intake_payload: dict[str, Any],
    receipts: dict[Path, dict[str, Any]],
    memory_root: Path,
) -> bool:
    return any(
        valid_intake_retirement_receipt(receipt, intake_path, intake_payload, memory_root)
        for receipt in receipts.values()
    )


def normalized_receipt_timestamp(value: Any) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolved_touched_paths(memory_root: Path, raw_paths: Any) -> list[Path] | None:
    if not isinstance(raw_paths, list) or not raw_paths or not all(isinstance(item, str) for item in raw_paths):
        return None
    resolved = [resolve_portable_memory_path(item, memory_root) for item in raw_paths]
    return None if any(not path.is_file() for path in resolved) else resolved


def valid_meeting_sync_retirement_successor(
    binding: dict[str, Any],
    receipt_path: Path,
    memory_root: Path,
    intake_path: Path,
) -> bool:
    try:
        receipt = load_json(receipt_path)
        intake_payload = load_json(intake_path)
    except (OSError, json.JSONDecodeError):
        return False
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    meeting = result.get("meeting") if isinstance(result.get("meeting"), dict) else {}
    touched = result.get("touched") if isinstance(result.get("touched"), dict) else {}
    payload_meeting = (
        intake_payload.get("meeting")
        if isinstance(intake_payload, dict) and isinstance(intake_payload.get("meeting"), dict)
        else {}
    )
    meeting_instance_id = str(receipt.get("meeting_instance_id") or "").strip()
    plan_fingerprint = str(receipt.get("plan_fingerprint") or "").strip().lower()
    applied_at = normalized_receipt_timestamp(receipt.get("applied_at"))
    if (
        receipt.get("schema_version") != 1
        or receipt.get("status") != "applied"
        or result.get("ok") is not True
        or result.get("dry_run") is not False
        or not meeting_instance_id
        or receipt_path.name != f"{meeting_instance_id}.json"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_fingerprint) is None
        or applied_at is None
        or meeting.get("meeting_instance_id") != meeting_instance_id
        or str(meeting.get("plan_fingerprint") or "").strip().lower() != plan_fingerprint
        or not isinstance(intake_payload, dict)
        or intake_payload.get("generated_by") != "adp-meeting-sync"
        or payload_meeting.get("meeting_instance_id") != meeting_instance_id
        or str(payload_meeting.get("plan_fingerprint") or "").strip().lower() != plan_fingerprint
    ):
        return False
    intake_paths = resolved_touched_paths(memory_root, touched.get("status_sync_intake_files"))
    daily_paths = resolved_touched_paths(memory_root, touched.get("daily_logs"))
    workstream_paths = resolved_touched_paths(memory_root, touched.get("workstream_records"))
    archive = resolve_portable_memory_path(str(receipt.get("archive") or ""), memory_root)
    updates = intake_payload.get("updates")
    if (
        intake_paths is None
        or len(intake_paths) != 1
        or intake_paths[0].resolve() != intake_path.resolve()
        or daily_paths is None
        or workstream_paths is None
        or not archive.is_file()
        or not isinstance(updates, list)
        or not updates
    ):
        return False
    expected = {
        "binding_type": "meeting-sync-receipt",
        "path": receipt_path.resolve().relative_to(memory_root.resolve()).as_posix(),
        "fingerprint": "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "schema_version": 1,
        "meeting_instance_id": meeting_instance_id,
        "plan_fingerprint": plan_fingerprint,
        "applied_at": applied_at,
        "generated_intake": {
            "path": intake_path.resolve().relative_to(memory_root.resolve()).as_posix(),
            "fingerprint": "sha256:" + hashlib.sha256(intake_path.read_bytes()).hexdigest(),
            "payload_id": receipt_content_id(intake_payload),
            "update_count": len(updates),
        },
        "write_lineage": {
            "archive": archive.resolve().relative_to(memory_root.resolve()).as_posix(),
            "daily_logs": sorted(path.resolve().relative_to(memory_root.resolve()).as_posix() for path in daily_paths),
            "workstream_records": sorted(
                path.resolve().relative_to(memory_root.resolve()).as_posix() for path in workstream_paths
            ),
        },
    }
    return all(binding.get(key) == value for key, value in expected.items())


def meeting_successor_lineage_binding(scan: dict[str, Any]) -> dict[str, Any]:
    satisfied = scan.get("satisfied_commands") if isinstance(scan.get("satisfied_commands"), list) else []
    return {
        "verification_status": scan.get("verification_status"),
        "snapshot_id": scan.get("snapshot_id"),
        "command_count": len(satisfied),
        "command_results_digest": receipt_content_id(satisfied),
        "read_set": scan.get("read_set", []),
    }


def valid_retirement_successor(binding: Any, memory_root: Path, intake_path: Path) -> bool:
    if not isinstance(binding, dict):
        return False
    raw_path = binding.get("path")
    fingerprint = str(binding.get("fingerprint") or "").strip().lower()
    if not isinstance(raw_path, str) or not raw_path.strip() or not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
        return False
    raw_candidate = Path(raw_path).expanduser()
    path = (
        (memory_root / raw_candidate).resolve()
        if not raw_candidate.is_absolute()
        else resolve_portable_memory_path(raw_path, memory_root)
    )
    try:
        relative = path.resolve().relative_to(memory_root.resolve())
    except ValueError:
        return False
    if not path.is_file() or path.resolve() == intake_path.resolve():
        return False
    if fingerprint.removeprefix("sha256:") != hashlib.sha256(path.read_bytes()).hexdigest():
        return False
    binding_type = binding.get("binding_type")
    if binding_type == "intake":
        if relative.parts[:2] != ("intake", "status-sync"):
            return False
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            return False
        updates = payload.get("updates") if isinstance(payload, dict) else None
        if not isinstance(updates, list) or not updates:
            return False
        if binding.get("update_count") != len(updates) or binding.get("payload_id") != receipt_content_id(payload):
            return False
        durable = binding.get("durable_receipt")
        if durable is None:
            return True
        if not isinstance(durable, dict):
            return False
        durable_raw = str(durable.get("path") or "")
        durable_candidate = Path(durable_raw).expanduser()
        durable_path = (
            (memory_root / durable_candidate).resolve()
            if not durable_candidate.is_absolute()
            else resolve_portable_memory_path(durable_raw, memory_root)
        )
        durable_hash = str(durable.get("fingerprint") or "").strip().lower()
        if not durable_path.is_file() or not re.fullmatch(r"sha256:[0-9a-f]{64}", durable_hash):
            return False
        if durable_hash.removeprefix("sha256:") != hashlib.sha256(durable_path.read_bytes()).hexdigest():
            return False
        try:
            durable_receipt = load_json(durable_path)
        except (OSError, json.JSONDecodeError):
            return False
        return successful_receipt_payload(durable_receipt, path, payload)
    if binding_type == "durable-receipt":
        if relative.parts[:2] != ("receipts", "status-sync"):
            return False
        try:
            durable_receipt = load_json(path)
        except (OSError, json.JSONDecodeError):
            return False
        target_input = resolve_portable_memory_path(str(durable_receipt.get("input_path") or ""), memory_root)
        if not target_input.is_file() or target_input.resolve() == intake_path.resolve():
            return False
        try:
            target_payload = load_json(target_input)
        except (OSError, json.JSONDecodeError):
            return False
        return successful_receipt_payload(durable_receipt, target_input, target_payload)
    if binding_type == "meeting-sync-receipt":
        if relative.parts[:2] != ("meetings", "receipts"):
            return False
        return valid_meeting_sync_retirement_successor(binding, path, memory_root, intake_path)
    return False


def valid_intake_retirement_receipt(
    receipt: dict[str, Any],
    intake_path: Path,
    intake_payload: dict[str, Any],
    memory_root: Path,
) -> bool:
    if (
        receipt.get("receipt_schema_version") != STATUS_SYNC_RECEIPT_SCHEMA_VERSION
        or receipt.get("receipt_type") != "intake-retirement"
        or receipt.get("ok") is not True
        or receipt.get("status") != "retired"
        or receipt.get("durable") is not True
        or receipt.get("dry_run") is not False
        or receipt.get("mode") != "retire-intake"
        or receipt.get("reason") not in INTAKE_RETIREMENT_REASONS
        or not valid_receipt_timestamp(receipt.get("retired_at"))
    ):
        return False
    updates = intake_payload.get("updates")
    if not isinstance(updates, list) or not updates or receipt.get("update_count") != len(updates):
        return False
    input_path = receipt.get("input_path")
    if not isinstance(input_path, str) or not portable_memory_path_matches(input_path, intake_path, memory_root):
        return False
    input_hash = str(receipt.get("input_hash") or "").strip().lower()
    raw_hash = hashlib.sha256(intake_path.read_bytes()).hexdigest()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", input_hash) or input_hash.removeprefix("sha256:") != raw_hash:
        return False
    if receipt.get("payload_id") != receipt_content_id(intake_payload):
        return False
    principal = str(receipt.get("principal") or "").strip()
    governance = receipt.get("governance") if isinstance(receipt.get("governance"), dict) else {}
    if not principal or governance.get("authority_principal") != principal:
        return False
    reason = receipt.get("reason")
    justification = str(governance.get("justification") or "").strip()
    evidence_scan = receipt.get("evidence_scan") if isinstance(receipt.get("evidence_scan"), dict) else {}
    if reason in {"never-applied", "invalid-proposal"}:
        if not justification or evidence_scan.get("verification_status") == "blocked":
            return False
        if evidence_scan.get("satisfied_commands") != []:
            return False
        if receipt.get("superseded_by") is not None:
            return False
    elif reason == "superseded-by":
        successor = receipt.get("superseded_by")
        if not valid_retirement_successor(successor, memory_root, intake_path):
            return False
        if isinstance(successor, dict) and successor.get("binding_type") == "meeting-sync-receipt":
            if (
                evidence_scan.get("verification_status") != "verified"
                or evidence_scan.get("missing_commands") != []
                or not isinstance(evidence_scan.get("satisfied_commands"), list)
                or not evidence_scan.get("satisfied_commands")
                or successor.get("lineage_verification") != meeting_successor_lineage_binding(evidence_scan)
            ):
                return False
    read_set = receipt.get("read_set")
    if not isinstance(read_set, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("fingerprint") or ""))
        for item in read_set
    ):
        return False
    if not any(
        (memory_root / str(item.get("path"))).resolve() == intake_path.resolve()
        and fingerprints_equal(item.get("fingerprint"), input_hash)
        for item in read_set
    ):
        return False
    successor = receipt.get("superseded_by") if isinstance(receipt.get("superseded_by"), dict) else None
    if successor and not any(
        str(item.get("path")) == str(successor.get("path"))
        and fingerprints_equal(item.get("fingerprint"), successor.get("fingerprint"))
        for item in read_set
    ):
        return False
    snapshot_body = {
        "input_path": receipt.get("input_path"),
        "input_hash": receipt.get("input_hash"),
        "update_count": receipt.get("update_count"),
        "reason": reason,
        "justification": governance.get("justification"),
        "superseded_by": receipt.get("superseded_by"),
        "evidence_scan": evidence_scan,
        "read_set": read_set,
    }
    if receipt.get("snapshot_id") != receipt_content_id(snapshot_body):
        return False
    body = dict(receipt)
    retirement_id = body.pop("retirement_id", None)
    return retirement_id == receipt_content_id(body)


def valid_intake_partial_closure_receipt(
    receipt: dict[str, Any],
    intake_path: Path,
    intake_payload: dict[str, Any],
    memory_root: Path,
) -> bool:
    if (
        receipt.get("receipt_schema_version") != STATUS_SYNC_RECEIPT_SCHEMA_VERSION
        or receipt.get("receipt_type") != "partial-closure"
        or receipt.get("ok") is not True
        or receipt.get("status") != "closed"
        or receipt.get("durable") is not True
        or receipt.get("dry_run") is not False
        or receipt.get("mode") != "reconcile-intake"
        or not valid_receipt_timestamp(receipt.get("closed_at"))
    ):
        return False
    updates = intake_payload.get("updates")
    if not isinstance(updates, list) or not updates or receipt.get("update_count") != len(updates):
        return False
    raw_input_path = receipt.get("input_path")
    if not isinstance(raw_input_path, str) or not portable_memory_path_matches(raw_input_path, intake_path, memory_root):
        return False
    input_hash = str(receipt.get("input_hash") or "").strip().lower()
    raw_hash = hashlib.sha256(intake_path.read_bytes()).hexdigest()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", input_hash) or input_hash.removeprefix("sha256:") != raw_hash:
        return False
    if receipt.get("payload_id") != receipt_content_id(intake_payload):
        return False
    if not str(receipt.get("principal") or "").strip():
        return False
    closure = receipt.get("partial_closure") if isinstance(receipt.get("partial_closure"), dict) else {}
    plan = closure.get("plan") if isinstance(closure.get("plan"), dict) else {}
    command_results = closure.get("command_results")
    read_set = receipt.get("read_set")
    if not isinstance(command_results, list) or not command_results or not isinstance(read_set, list):
        return False
    reconciled = [item for item in command_results if isinstance(item, dict) and item.get("satisfied") is True]
    retired = [item for item in command_results if isinstance(item, dict) and item.get("satisfied") is False]
    if len(reconciled) + len(retired) != len(command_results) or not reconciled or not retired:
        return False
    try:
        indexes = [int(item["command_index"]) for item in command_results]
        reconciled_indexes = [int(item["command_index"]) for item in reconciled]
        retired_indexes = [int(item["command_index"]) for item in retired]
    except (ValueError, TypeError, KeyError):
        return False
    partition = {
        "reconciled_command_indexes": reconciled_indexes,
        "retired_command_indexes": retired_indexes,
    }
    snapshot_body = {
        "input_path": receipt.get("input_path"),
        "input_hash": receipt.get("input_hash"),
        "update_count": receipt.get("update_count"),
        "command_results": command_results,
        "read_set": read_set,
    }
    body = dict(receipt)
    receipt_id = body.pop("receipt_id", None)
    return bool(
        len(indexes) == len(set(indexes))
        and sorted(indexes) == list(range(1, len(indexes) + 1))
        and receipt.get("snapshot_id") == receipt_content_id(snapshot_body)
        and closure.get("verification_method") == "canonical-fact-reconciliation-plus-command-retirement"
        and closure.get("verification_status") == "verified"
        and closure.get("all_commands_disposed") is True
        and closure.get("reconciled_commands") == reconciled
        and closure.get("retired_commands") == retired
        and plan.get("closure_kind") == "legacy-non-atomic-partial-execution"
        and plan.get("retirement_reason") == "not-executed-and-unsafe-to-replay"
        and bool(str(plan.get("retirement_justification") or "").strip())
        and plan.get("payload_parser") in {"current-writer-schema", "legacy-terminal-action-scan"}
        and plan.get("replay_policy") == "closed-intake-must-not-be-replayed"
        and plan.get("reconciled_command_indexes") == reconciled_indexes
        and plan.get("retired_command_indexes") == retired_indexes
        and plan.get("command_partition_digest") == receipt_content_id(partition)
        and receipt_id == receipt_content_id(body)
    )


def successful_receipt_payload(receipt: dict[str, Any], intake_path: Path, intake_payload: dict[str, Any]) -> bool:
    if not receipt or receipt.get("receipt_schema_version") != STATUS_SYNC_RECEIPT_SCHEMA_VERSION:
        return False
    if receipt.get("receipt_type") not in {"execution", "migration", "reconciliation"}:
        return False
    if receipt.get("dry_run") is not False or receipt.get("durable") is not True:
        return False
    status = normalize_status(receipt.get("status") or receipt.get("lifecycle_status"))
    if receipt.get("ok") is not True or status != "applied" or str(receipt.get("mode", "")).lower() != "update":
        return False
    if not valid_receipt_timestamp(receipt.get("applied_at")):
        return False
    expected_hash = str(receipt.get("input_hash", "")).strip().lower()
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", expected_hash):
        return False
    raw_hash = hashlib.sha256(intake_path.read_bytes()).hexdigest()
    if expected_hash.removeprefix("sha256:") != raw_hash:
        return False
    input_path = receipt.get("input_path") or receipt.get("updates_file")
    if not isinstance(input_path, str) or not input_path.strip():
        return False
    memory_root = intake_path.resolve().parents[2]
    if not portable_memory_path_matches(input_path, intake_path, memory_root):
        return False
    updates = intake_payload.get("updates")
    if not isinstance(updates, list) or receipt.get("update_count") != len(updates):
        return False
    if receipt.get("receipt_type") == "migration" and not valid_migration_receipt(
        receipt,
        memory_root,
        intake_path,
        intake_payload,
    ):
        return False
    if receipt.get("receipt_type") == "reconciliation" and not valid_reconciliation_receipt(receipt):
        return False
    return True


def valid_receipt_timestamp(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def portable_memory_relative_path(value: str) -> Path | None:
    parts = [part for part in value.replace("\\", "/").split("/") if part not in {"", "."}]
    anchor = ["_bmad-output", "adp", "memory"]
    folded = [part.casefold() for part in parts]
    for index in range(len(parts) - len(anchor) + 1):
        if folded[index : index + len(anchor)] != anchor:
            continue
        relative_parts = parts[index + len(anchor) :]
        if not relative_parts or any(part == ".." for part in relative_parts):
            return None
        return Path(*relative_parts)
    return None


def resolve_portable_memory_path(value: str, memory_root: Path) -> Path:
    raw = Path(value).expanduser()
    relative = portable_memory_relative_path(value)
    if relative is not None:
        resolved = (memory_root.resolve() / relative).resolve()
        try:
            resolved.relative_to(memory_root.resolve())
        except ValueError:
            pass
        else:
            return resolved
    if not raw.is_absolute():
        candidate = (memory_root.resolve() / raw).resolve()
        try:
            candidate.relative_to(memory_root.resolve())
        except ValueError:
            pass
        else:
            return candidate
    direct = raw.resolve()
    return direct


def portable_memory_path_matches(value: str, expected: Path, memory_root: Path) -> bool:
    raw = Path(value).expanduser()
    direct = raw.resolve()
    if direct == expected.resolve():
        return True
    return resolve_portable_memory_path(value, memory_root) == expected.resolve()


def receipt_content_id(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def canonical_executable_payload_from_raw(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    executable_keys = (
        "baseline_revision",
        "refresh_actions",
        "status_intents",
        "action_commands",
        "updates",
    )
    projection = {key: payload[key] for key in executable_keys if key in payload}
    if not any(key in projection for key in ("updates", "status_intents", "action_commands")):
        return None
    return projection


def json_pointer_segment(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def json_diff_paths(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "/"]
    if isinstance(left, dict):
        differences: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            child = f"{path}/{json_pointer_segment(key)}"
            if key not in left or key not in right:
                differences.append(child)
            else:
                differences.extend(json_diff_paths(left[key], right[key], child))
        return differences
    if isinstance(left, list):
        differences = []
        for index in range(max(len(left), len(right))):
            child = f"{path}/{index}"
            if index >= len(left) or index >= len(right):
                differences.append(child)
            else:
                differences.extend(json_diff_paths(left[index], right[index], child))
        return differences
    return [] if left == right else [path or "/"]


def valid_reconciliation_receipt(receipt: dict[str, Any]) -> bool:
    reconciliation = receipt.get("reconciliation")
    if not isinstance(reconciliation, dict):
        return False
    command_results = reconciliation.get("command_results")
    read_set = reconciliation.get("read_set")
    if (
        reconciliation.get("verification_method") != "canonical-fact-reconciliation"
        or reconciliation.get("verification_status") != "verified"
        or reconciliation.get("all_satisfied") is not True
        or reconciliation.get("missing_commands") != []
        or not isinstance(command_results, list)
        or not command_results
        or not all(isinstance(item, dict) and item.get("satisfied") is True for item in command_results)
        or not isinstance(read_set, list)
    ):
        return False
    snapshot_body = {
        "input_path": receipt.get("input_path"),
        "input_hash": receipt.get("input_hash"),
        "update_count": receipt.get("update_count"),
        "command_results": command_results,
        "read_set": read_set,
    }
    if reconciliation.get("snapshot_id") != receipt_content_id(snapshot_body):
        return False
    receipt_body = dict(receipt)
    receipt_id = receipt_body.pop("receipt_id", None)
    return receipt_id == receipt_content_id(receipt_body)


def valid_historical_input_change_migration(
    receipt: dict[str, Any],
    memory_root: Path,
    intake_path: Path,
    current_payload: dict[str, Any],
) -> bool:
    migration = receipt.get("migration") if isinstance(receipt.get("migration"), dict) else {}
    original_hash = str(migration.get("original_input_hash") or "").strip().lower()
    current_hash = str(migration.get("current_input_hash") or "").strip().lower()
    snapshot_raw = migration.get("original_input_snapshot_path")
    evidence_raw = migration.get("evidence_path")
    if (
        migration.get("migration_kind") != "historical-input-change"
        or migration.get("verification_status") != "verified"
        or not isinstance(snapshot_raw, str)
        or not isinstance(evidence_raw, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", original_hash)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", current_hash)
        or original_hash == current_hash
        or current_hash != receipt.get("input_hash")
        or not str(migration.get("attested_by") or "").strip()
    ):
        return False
    snapshot = resolve_portable_memory_path(snapshot_raw, memory_root)
    evidence = resolve_portable_memory_path(evidence_raw, memory_root)
    try:
        snapshot_relative = snapshot.resolve().relative_to(memory_root.resolve())
    except ValueError:
        return False
    if snapshot_relative.parts[:3] != ("receipts", "status-sync-input-migration", "originals"):
        return False
    try:
        original_bytes = snapshot.read_bytes()
        evidence_bytes = evidence.read_bytes()
        original_payload = json.loads(original_bytes.decode("utf-8-sig"))
        evidence_payload = json.loads(evidence_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    evidence_claims = original_historical_execution_evidence_claims(
        evidence_payload,
        evidence,
        intake_path,
        original_hash,
        original_payload,
        memory_root,
    )
    if (
        "sha256:" + hashlib.sha256(original_bytes).hexdigest() != original_hash
        or migration.get("original_input_snapshot_hash") != original_hash
        or not fingerprints_equal(migration.get("evidence_hash"), hashlib.sha256(evidence_bytes).hexdigest())
        or evidence_claims is None
    ):
        return False
    original_canonical = canonical_executable_payload_from_raw(original_payload)
    current_canonical = canonical_executable_payload_from_raw(current_payload)
    if original_canonical is None or current_canonical is None:
        return False
    executable_changed_paths = json_diff_paths(original_canonical, current_canonical)
    diff_body = {
        "equal": not executable_changed_paths,
        "changed_paths": executable_changed_paths,
        "non_executable_changed_paths": json_diff_paths(original_payload, current_payload),
    }
    expected_diff = {**diff_body, "diff_id": receipt_content_id(diff_body)}
    return bool(
        not executable_changed_paths
        and migration.get("evidence_mode") == "update"
        and (
            migration.get("evidence_kind") == evidence_claims["evidence_kind"]
            or evidence_claims["evidence_kind"] == "execution-report"
            and migration.get("evidence_kind") is None
        )
        and (
            evidence_claims["evidence_kind"] != "execution-receipt"
            or migration.get("evidence_execution_id") == evidence_claims["evidence_execution_id"]
            and migration.get("evidence_applied_at") == evidence_claims["evidence_applied_at"]
            and normalized_receipt_timestamp(receipt.get("applied_at")) == evidence_claims["evidence_applied_at"]
        )
        and isinstance(migration.get("evidence_input_path"), str)
        and portable_memory_path_matches(migration["evidence_input_path"], intake_path, memory_root)
        and migration.get("evidence_input_hash") == original_hash
        and isinstance(migration.get("original_input_path"), str)
        and portable_memory_path_matches(migration["original_input_path"], intake_path, memory_root)
        and migration.get("original_payload_id") == receipt_content_id(original_payload)
        and migration.get("current_payload_id") == receipt_content_id(current_payload)
        and migration.get("canonical_executable_payload_id") == receipt_content_id(original_canonical)
        and migration.get("executable_diff") == expected_diff
    )


def original_historical_execution_evidence_claims(
    payload: Any,
    evidence_path: Path,
    logical_input_path: Path,
    original_hash: str,
    original_payload: Any,
    memory_root: Path,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    original_updates = original_payload.get("updates") if isinstance(original_payload, dict) else None
    if not isinstance(original_updates, list) or not original_updates:
        return None
    if payload.get("receipt_type") is not None:
        execution_id = str(payload.get("execution_id") or "").strip()
        evidence_applied_at = normalized_receipt_timestamp(payload.get("applied_at"))
        expected_path = memory_root / STATUS_SYNC_RECEIPT_REL / f"{execution_id}.json"
        if (
            payload.get("receipt_schema_version") != STATUS_SYNC_RECEIPT_SCHEMA_VERSION
            or payload.get("receipt_type") != "execution"
            or re.fullmatch(r"ssr-[0-9a-f]{32}", execution_id) is None
            or evidence_path.resolve() != expected_path.resolve()
            or payload.get("ok") is not True
            or payload.get("status") != "applied"
            or payload.get("durable") is not True
            or payload.get("dry_run") is not False
            or payload.get("mode") != "update"
            or payload.get("update_count") != len(original_updates)
            or evidence_applied_at is None
            or not fingerprints_equal(payload.get("input_hash"), original_hash)
        ):
            return None
        declared_path = payload.get("input_path")
        if not isinstance(declared_path, str) or not portable_memory_path_matches(
            declared_path,
            logical_input_path,
            memory_root,
        ):
            return None
        return {
            "evidence_kind": "execution-receipt",
            "evidence_execution_id": execution_id,
            "evidence_applied_at": evidence_applied_at,
        }
    if ATTESTATION_WRAPPER_FIELDS.intersection(payload):
        return None
    if payload.get("ok") is not True or payload.get("dry_run") is not False:
        return None
    if str(payload.get("mode") or "").strip().lower() != "update":
        return None
    status = normalize_status(payload.get("status") or payload.get("lifecycle_status"))
    if status and status != "applied":
        return None
    updates = payload.get("updates")
    if (
        not isinstance(updates, list)
        or not updates
        or len(updates) != len(original_updates)
        or not fingerprints_equal(payload.get("input_hash"), original_hash)
    ):
        return None
    declared_path = payload.get("input_path")
    if not (
        isinstance(declared_path, str)
        and portable_memory_path_matches(declared_path, logical_input_path, memory_root)
    ):
        return None
    return {"evidence_kind": "execution-report"}


def valid_migration_receipt(
    receipt: dict[str, Any],
    memory_root: Path,
    intake_path: Path,
    intake_payload: dict[str, Any],
) -> bool:
    migration = receipt.get("migration") if isinstance(receipt.get("migration"), dict) else {}
    if migration.get("migration_kind") == "historical-input-change":
        return valid_historical_input_change_migration(
            receipt,
            memory_root,
            intake_path,
            intake_payload,
        )
    evidence_path = migration.get("evidence_path")
    evidence_hash = str(migration.get("evidence_hash") or "").strip().lower()
    attested_by = str(migration.get("attested_by") or "").strip()
    if (
        not isinstance(evidence_path, str)
        or not evidence_path.strip()
        or not attested_by
        or migration.get("verification_status") != "verified"
    ):
        return False
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", evidence_hash):
        return False
    path = resolve_portable_memory_path(evidence_path, memory_root)
    if not path.is_file():
        return False
    try:
        evidence_bytes = path.read_bytes()
        evidence_payload = json.loads(evidence_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if evidence_hash.removeprefix("sha256:") != hashlib.sha256(evidence_bytes).hexdigest():
        return False
    if not valid_original_execution_report(evidence_payload, receipt, path, memory_root):
        return False
    evidence_input_path = migration.get("evidence_input_path")
    evidence_input_hash = str(migration.get("evidence_input_hash") or "").strip().lower()
    receipt_input_path = receipt.get("input_path")
    receipt_input_hash = str(receipt.get("input_hash") or "").strip().lower()
    if not isinstance(evidence_input_path, str) or not isinstance(receipt_input_path, str):
        return False
    if Path(evidence_input_path).expanduser().resolve() != Path(receipt_input_path).expanduser().resolve():
        return False
    return fingerprints_equal(evidence_input_hash, receipt_input_hash)


def valid_original_execution_report(
    payload: Any,
    receipt: dict[str, Any],
    evidence_path: Path,
    memory_root: Path,
) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("receipt_type") is not None:
        execution_id = str(payload.get("execution_id") or "").strip()
        declared_path = payload.get("input_path")
        receipt_path = receipt.get("input_path")
        expected_input = (
            resolve_portable_memory_path(receipt_path, memory_root)
            if isinstance(receipt_path, str)
            else None
        )
        return bool(
            payload.get("receipt_schema_version") == STATUS_SYNC_RECEIPT_SCHEMA_VERSION
            and payload.get("receipt_type") == "execution"
            and re.fullmatch(r"ssr-[0-9a-f]{32}", execution_id) is not None
            and evidence_path.resolve() == (memory_root / STATUS_SYNC_RECEIPT_REL / f"{execution_id}.json").resolve()
            and payload.get("ok") is True
            and payload.get("status") == "applied"
            and payload.get("durable") is True
            and payload.get("dry_run") is False
            and payload.get("mode") == "update"
            and payload.get("update_count") == receipt.get("update_count")
            and normalized_receipt_timestamp(payload.get("applied_at"))
            == normalized_receipt_timestamp(receipt.get("applied_at"))
            and isinstance(declared_path, str)
            and expected_input is not None
            and portable_memory_path_matches(declared_path, expected_input, memory_root)
            and fingerprints_equal(payload.get("input_hash"), receipt.get("input_hash"))
        )
    if ATTESTATION_WRAPPER_FIELDS.intersection(payload):
        return False
    if payload.get("ok") is not True or payload.get("dry_run") is not False:
        return False
    if str(payload.get("mode") or "").strip().lower() != "update":
        return False
    status = normalize_status(payload.get("status") or payload.get("lifecycle_status"))
    if status and status != "applied":
        return False
    updates = payload.get("updates")
    if not isinstance(updates, list) or not updates or len(updates) != receipt.get("update_count"):
        return False
    declared_path = payload.get("input_path")
    receipt_path = receipt.get("input_path")
    if not isinstance(declared_path, str) or not isinstance(receipt_path, str):
        return False
    if Path(declared_path).expanduser().resolve() != Path(receipt_path).expanduser().resolve():
        return False
    return fingerprints_equal(payload.get("input_hash"), receipt.get("input_hash"))


def audit_merge_quality(prepass: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    blocking_gaps: list[dict[str, Any]] = []
    non_blocking_gaps: list[dict[str, Any]] = []
    for gap in prepass_gaps(prepass, category="merge_quality"):
        item = prepass_gap_finding(gap, "merge_quality")
        (blocking_gaps if gap.get("blocking") else non_blocking_gaps).append(item)

    actions = [
        action
        for action in prepass.get("ledger_actions", [])
        if str(action.get("status", "")).lower() in ACTIVE_ACTION_STATUSES
    ]
    collision_evidence: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for action in actions:
        key = tuple(
            str(action.get(field, "")).strip()
            for field in ("action", "owner", "due_or_trigger")
        )
        if any(key):
            grouped[key].append(action)
    for group in grouped.values():
        if len(group) < 2:
            continue
        collision_evidence.append(
            {
                "action_ids": sorted(str(item.get("action_id", "")) for item in group),
                "owner": group[0].get("owner", ""),
                "due_or_trigger": group[0].get("due_or_trigger", ""),
                "action": group[0].get("action", ""),
                "evidence_type": "exact-action-field-collision",
            }
        )

    return {
        "blocking_gaps": blocking_gaps,
        "non_blocking_gaps": non_blocking_gaps,
        "duplicate_candidates": [],
        "overlap_candidates": [],
        "conflict_candidates": [],
        "action_field_collision_evidence": collision_evidence,
        "shared_reference_evidence": shared_references_from_workstreams(prepass.get("workstreams", [])),
        "readiness_gap_evidence": readiness_gaps_from_workstreams(prepass.get("workstreams", [])),
    }


def shared_references_from_workstreams(workstreams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_l0: dict[str, list[tuple[str, str]]] = defaultdict(list)
    by_dependency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ws in workstreams:
        ws_id = str(ws.get("id", ""))
        links = ws.get("links", {}) if isinstance(ws.get("links"), dict) else {}
        for ref in links.get("l0_references", []):
            raw = str(ref).strip()
            key = raw
            if key:
                by_l0[key].append((ws_id, raw))
        raw_dependency = str(ws.get("dependencies", "")).strip()
        if is_meaningful(raw_dependency):
            key = normalize_text_key(raw_dependency)
            if key:
                by_dependency[key].append((ws_id, raw_dependency))

    results: list[dict[str, Any]] = []
    for label, grouped in [("l0_reference", by_l0), ("dependency_statement", by_dependency)]:
        for key, matches in grouped.items():
            unique_ids = sorted({ws_id for ws_id, _ in matches if ws_id})
            if len(unique_ids) > 1:
                results.append(
                    {
                        "evidence_type": label,
                        "match_key": key,
                        "raw_values": sorted({raw for _, raw in matches}),
                        "workstreams": unique_ids,
                    }
                )
    return results


def readiness_gaps_from_workstreams(workstreams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for ws in workstreams:
        status = normalize_status(ws.get("status", ""))
        gaps = [gap for gap in ws.get("gaps", []) if isinstance(gap, dict)]
        if status == "ready" and gaps:
            results.append(
                {
                    "workstream": ws.get("id", ""),
                    "status": ws.get("status", ""),
                    "typed_gaps": gaps,
                }
            )
    return results


def action_field_gaps(action: dict[str, Any], as_of: date, max_age_days: int) -> list[dict[str, str]]:
    if str(action.get("status", "")).lower() not in ACTIVE_ACTION_STATUSES:
        return []
    gaps: list[dict[str, str]] = []
    if is_missing_owner(str(action.get("owner", ""))):
        gaps.append({"gap": "action owner is missing", "field": "owner", "gap_type": "missing"})
    if not is_meaningful(action.get("source", "")):
        gaps.append({"gap": "action source is missing", "field": "source", "gap_type": "missing"})
    if is_missing_due(action.get("due_or_trigger", "")):
        gaps.append({"gap": "action due trigger is missing", "field": "due_or_trigger", "gap_type": "missing"})
    if is_missing_closure_criteria(action.get("closure_criteria", "")):
        gaps.append({"gap": "action closure criteria is missing", "field": "closure_criteria", "gap_type": "missing"})
    affected = str(action.get("affected_workstreams", "")).strip()
    workstream = str(action.get("workstream", "")).strip().lower()
    if workstream in {"program", "project", "adp-program"} and not is_meaningful(affected):
        gaps.append({"gap": "program action affected workstreams are missing", "field": "affected_workstreams", "gap_type": "missing"})
    return gaps


def business_packet_aliases(field: str, document_locale: str) -> tuple[str, ...]:
    aliases = BUSINESS_PACKET_FIELD_ALIASES[field]
    preferred = "zh" if str(document_locale).lower().startswith("zh") else "en"
    fallback = "en" if preferred == "zh" else "zh"
    return tuple(dict.fromkeys((*aliases[preferred], *aliases[fallback])))


def business_packet_value(text: str, field: str, document_locale: str) -> str:
    aliases = business_packet_aliases(field, document_locale)
    for alias in aliases:
        value = extract_colon_field(text, alias)
        if value:
            return value
    for alias in aliases:
        value = section_text(text, alias)
        if value:
            return value
    return ""


def business_packet_field_gaps(
    memory_root: Path,
    document_locale: str = "en",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gaps: list[dict[str, Any]] = []
    owner_gaps: list[dict[str, Any]] = []
    for packet in business_packets(memory_root, date.max, document_locale):
        if decision_status_is_terminal(packet.get("status", "")):
            continue
        path = packet["path"]
        text = packet.get("_text", "")
        required = {
            "background": business_packet_value(text, "background", document_locale),
            "decision": business_packet_value(text, "decision", document_locale),
            "options": business_packet_value(text, "options", document_locale),
            "recommendation": business_packet_value(text, "recommendation", document_locale),
            "deadline": packet.get("deadline", ""),
            "owner": packet.get("owner", ""),
            "workstreams": packet.get("affected_workstreams", ""),
        }
        for field, value in required.items():
            if is_meaningful(value):
                continue
            item = {
                "source": path,
                "gap": f"business decision packet {field} is missing or TBD",
                "category": "missing",
                "recommended_workflow": "adp-risk-dependency-change-review",
            }
            gaps.append(item)
            if field == "owner":
                owner_gaps.append(item)
    return gaps, owner_gaps


def business_packets(
    memory_root: Path,
    as_of: date,
    document_locale: str = "en",
) -> list[dict[str, Any]]:
    results = []
    root = memory_root / "decisions" / "business-decision-packets"
    for path in sorted(root.glob("*.md")):
        text = read_text(path)
        status = business_packet_value(text, "status", document_locale) or "open"
        deadline = business_packet_value(text, "deadline", document_locale)
        owner = business_packet_value(text, "owner", document_locale)
        affected = business_packet_value(text, "workstreams", document_locale)
        deadline_date = parse_date(deadline)
        results.append(
            {
                "path": rel_to_memory(memory_root, path),
                "status": status,
                "deadline": deadline or "TBD",
                "owner": owner or "TBD",
                "affected_workstreams": affected or "TBD",
                "overdue": bool(deadline_date and deadline_date < as_of and not decision_status_is_terminal(status)),
                "category": "closure",
                "recommended_workflow": "adp-risk-dependency-change-review",
                "_text": text,
            }
        )
    return results


def public_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in packet.items() if not key.startswith("_")}


def validate_prepass_contract(prepass: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if prepass.get("schema_version") != SUPPORTED_PREPASS_SCHEMA_VERSION:
        errors.append(f"schema_version must be {SUPPORTED_PREPASS_SCHEMA_VERSION}")
    for field in sorted(REQUIRED_PREPASS_COLLECTIONS):
        if not isinstance(prepass.get(field), list):
            errors.append(f"{field} must be an array")
    gaps = prepass.get("gaps") if isinstance(prepass.get("gaps"), list) else []
    cross_reference_gaps = (
        prepass.get("cross_reference_gaps")
        if isinstance(prepass.get("cross_reference_gaps"), list)
        else []
    )
    workstreams = prepass.get("workstreams") if isinstance(prepass.get("workstreams"), list) else []
    for index, gap in enumerate(gaps):
        errors.extend(validate_gap_item(gap, f"gaps[{index}]"))
    for index, gap in enumerate(cross_reference_gaps):
        errors.extend(validate_gap_item(gap, f"cross_reference_gaps[{index}]"))
    for ws_index, workstream in enumerate(workstreams):
        if not isinstance(workstream, dict):
            errors.append(f"workstreams[{ws_index}] must be an object")
            continue
        workstream_gaps = workstream.get("gaps", [])
        if not isinstance(workstream_gaps, list):
            errors.append(f"workstreams[{ws_index}].gaps must be an array")
            continue
        for gap_index, gap in enumerate(workstream_gaps):
            errors.extend(validate_gap_item(gap, f"workstreams[{ws_index}].gaps[{gap_index}]"))
    for field in ["sources_read", "action_cross_check", "ledger_actions"]:
        values = prepass.get(field) if isinstance(prepass.get(field), list) else []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                errors.append(f"{field}[{index}] must be an object")
    return errors


def validate_gap_item(gap: Any, location: str) -> list[str]:
    if not isinstance(gap, dict):
        return [f"{location} must be an object with typed fields"]
    missing = sorted(field for field in REQUIRED_PREPASS_GAP_FIELDS if field not in gap)
    errors = [f"{location} missing {', '.join(missing)}"] if missing else []
    category = gap.get("category")
    if category not in VALID_GAP_CATEGORIES:
        errors.append(f"{location}.category must be one of {sorted(VALID_GAP_CATEGORIES)}")
    if not isinstance(gap.get("blocking"), bool):
        errors.append(f"{location}.blocking must be boolean")
    return errors


def prepass_gaps(prepass: dict[str, Any], category: str | None = None) -> list[dict[str, Any]]:
    gaps = [gap for gap in prepass.get("gaps", []) if isinstance(gap, dict)]
    if category is None:
        return gaps
    return [gap for gap in gaps if gap.get("category") == category]


def prepass_gap_finding(gap: dict[str, Any], category: str) -> dict[str, Any]:
    finding = {
        "workstream": gap.get("workstream", ""),
        "source": gap.get("source", ""),
        "gap": gap.get("gap", ""),
        "category": gap.get("category", category),
        "gap_type": gap.get("gap_type", ""),
        "field": gap.get("field", ""),
        "blocking": bool(gap.get("blocking")),
        "recommended_workflow": gap.get("recommended_workflow", ""),
    }
    if isinstance(gap.get("source_path"), str) and gap["source_path"].strip():
        finding["source_path"] = gap["source_path"].strip()
    if isinstance(gap.get("source_line"), int) and gap["source_line"] > 0:
        finding["source_line"] = gap["source_line"]
    return finding


def canonical_source_inventory(sources: list[dict[str, Any]], missing_sources: Any) -> list[dict[str, Any]]:
    items = [
        {
            "path": str(source.get("path", "")),
            "kind": source_kind(str(source.get("path", ""))),
            "modified": str(source.get("modified", "")),
            "status": "read",
        }
        for source in sources
        if isinstance(source, dict)
    ]
    items.extend(
        {
            "path": str(path),
            "kind": source_kind(str(path)),
            "modified": "",
            "status": "missing",
        }
        for path in missing_sources
    )
    return items


def canonical_findings(
    freshness: dict[str, Any],
    completeness: dict[str, Any],
    consistency: dict[str, Any],
    closure: dict[str, Any],
    merge_quality: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    blocking = [
        *canonicalize_items(freshness.get("blocking_gaps", []), "blocking", "freshness"),
        *canonicalize_items(completeness.get("blocking_gaps", []), "blocking", "completeness"),
        *canonicalize_items(consistency.get("source_disagreements", []), "blocking", "consistency"),
        *canonicalize_items(closure.get("blocking_gaps", []), "blocking", "closure"),
        *canonicalize_items(closure.get("unconsumed_intake_files", []), "blocking", "closure"),
        *canonicalize_items(merge_quality.get("blocking_gaps", []), "blocking", "merge_quality"),
    ]
    warnings = [
        *canonicalize_items(completeness.get("non_blocking_gaps", []), "warning", "completeness"),
        *canonicalize_items(consistency.get("consistency_warnings", []), "warning", "consistency"),
        *canonicalize_items(closure.get("non_blocking_gaps", []), "warning", "closure"),
        *canonicalize_items(closure.get("open_business_packets", []), "warning", "closure"),
        *canonicalize_items(closure.get("escalation_candidates", []), "warning", "closure"),
        *canonicalize_items(merge_quality.get("non_blocking_gaps", []), "warning", "merge_quality"),
    ]
    stale_items = canonicalize_items(
        [
            *freshness.get("stale_workstreams", []),
            *freshness.get("stale_actions", []),
            *freshness.get("views_requiring_refresh", []),
        ],
        "warning",
        "freshness",
    )
    groups = {
        "blocking_gaps": blocking,
        "warnings": warnings,
        "duplicate_candidates": canonicalize_items(
            merge_quality.get("duplicate_candidates", []), "warning", "duplicate"
        ),
        "overlap_claims": canonicalize_items(
            merge_quality.get("overlap_candidates", []), "warning", "overlap"
        ),
        "conflicts": canonicalize_items(
            merge_quality.get("conflict_candidates", []), "blocking", "conflict"
        ),
        "stale_items": stale_items,
    }
    return {name: dedupe_canonical_findings(items) for name, items in groups.items()}


def warning_findings_from_groups(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return require_unique_finding_ids(
        [
            item
            for group in ["warnings", "duplicate_candidates", "overlap_claims", "stale_items"]
            for item in groups.get(group, [])
        ],
        "warning_findings",
    )


def require_unique_finding_ids(items: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    finding_ids = [str(item.get("finding_id") or "") for item in items]
    if any(not finding_id for finding_id in finding_ids):
        raise ValueError(f"{label} entries require finding_id")
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError(f"{label} finding_id values must be unique")
    return list(items)


def dedupe_canonical_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique.setdefault(str(item.get("id") or canonical_finding_identity(item)), item)
    return list(unique.values())


def canonicalize_items(items: Any, severity: str, kind: str) -> list[dict[str, Any]]:
    return [canonical_finding(item, severity, kind) for item in items if isinstance(item, dict)]


def canonical_finding(item: dict[str, Any], severity: str, kind: str) -> dict[str, Any]:
    sources = finding_sources(item)
    workstreams = finding_workstreams(item)
    summary = str(
        item.get("summary")
        or item.get("gap")
        or item.get("reason")
        or item.get("normalized_claim")
        or "review item"
    )
    identity = json.dumps(
        {
            "kind": kind,
            "sources": sources,
            "workstreams": workstreams,
            "summary": summary,
            "details": finding_identity_details(item),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    finding_id = str(item.get("finding_id") or f"adp-{kind}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}")
    action_ids = finding_action_ids(item)
    entity_refs = [
        {"entity_type": "workstream", "id": workstream_id}
        for workstream_id in workstreams
    ]
    entity_refs.extend({"entity_type": "action", "id": action_id} for action_id in action_ids)
    finding = {
        "id": finding_id,
        "finding_id": finding_id,
        "severity": severity,
        "kind": kind,
        "source_type": finding_source_type(item, kind, sources),
        "sources": sources,
        "workstreams": workstreams,
        "owner": str(item.get("owner") or "TBD"),
        "summary": summary,
        "category": str(item.get("category") or kind),
        "gap_type": str(item.get("gap_type") or ""),
        "recommended_workflow": str(item.get("recommended_workflow") or ""),
        "execution_disposition": str(item.get("execution_disposition") or "degraded"),
        "action_id": action_ids[0] if len(action_ids) == 1 else None,
        "action_ids": action_ids,
        "entity_refs": entity_refs,
        "repair_batch_id": item.get("repair_batch_id"),
    }
    if kind == "conflict":
        finding["details"] = {
            "status": item.get("status", ""),
            "gaps": item.get("gaps", []),
        }
    if isinstance(item.get("source_path"), str) and item["source_path"].strip():
        finding["source_path"] = item["source_path"].strip()
    if isinstance(item.get("source_line"), int) and item["source_line"] > 0:
        finding["source_line"] = item["source_line"]
    return finding


def finding_action_ids(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    raw_ids = item.get("action_ids")
    if isinstance(raw_ids, list):
        values.extend(str(value).strip() for value in raw_ids if str(value).strip())
    action_id = item.get("action_id")
    if isinstance(action_id, str) and action_id.strip():
        values.append(action_id.strip())
    action_diff = item.get("action_diff")
    if isinstance(action_diff, dict) and isinstance(action_diff.get("action_id"), str):
        values.append(action_diff["action_id"].strip())
    return sorted({value for value in values if value})


def finding_identity_details(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "action_id",
        "action_ids",
        "action",
        "normalized_claim",
        "type",
        "status",
        "field",
        "target",
        "relationship",
        "due_or_trigger",
    )
    return {key: item[key] for key in keys if key in item}


def finding_sources(item: dict[str, Any]) -> list[str]:
    raw_sources = item.get("sources")
    sources = [str(value) for value in raw_sources if str(value).strip()] if isinstance(raw_sources, list) else []
    for key in ("source", "path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(value.strip())
    for gap in item.get("gaps", []) if isinstance(item.get("gaps"), list) else []:
        if isinstance(gap, dict) and isinstance(gap.get("source"), str) and gap["source"].strip():
            sources.append(gap["source"].strip())
    return list(dict.fromkeys(sources))


def finding_workstreams(item: dict[str, Any]) -> list[str]:
    raw_workstreams = item.get("workstreams")
    values = [str(value) for value in raw_workstreams if str(value).strip()] if isinstance(raw_workstreams, list) else []
    workstream = item.get("workstream")
    if isinstance(workstream, str) and workstream.strip():
        values.append(workstream.strip())
    affected = item.get("affected_workstreams")
    if isinstance(affected, str):
        values.extend(part.strip() for part in re.split(r"[,;]", affected) if part.strip())
    elif isinstance(affected, list):
        values.extend(str(part).strip() for part in affected if str(part).strip())
    return list(dict.fromkeys(values))


def finding_source_type(item: dict[str, Any], kind: str, sources: list[str]) -> str:
    if kind == "duplicate":
        return "structural"
    if str(item.get("gap_type", "")).lower().startswith("missing") or str(item.get("category", "")) == "missing":
        return "missing"
    if any(source.startswith("views/") for source in sources):
        return "derived"
    return "fact"


def source_kind(path: str) -> str:
    if path.startswith("views/"):
        return "derived-view"
    if path.startswith("workstreams/"):
        return "workstream-delivery-record"
    if path.startswith("actions/"):
        return "action-ledger"
    if path.startswith("decisions/"):
        return "decision"
    if path.startswith("l0/"):
        return "l0-reference"
    if path.startswith("daily/"):
        return "daily-log"
    if path.startswith("meetings/"):
        return "meeting-archive"
    return "adp-source"


def recommend_workflows(
    findings: dict[str, list[dict[str, Any]]],
    prepass: dict[str, Any],
) -> list[str]:
    workflows = [
        str(item.get("recommended_workflow", "")).strip()
        for group in findings.values()
        for item in group
        if str(item.get("recommended_workflow", "")).strip()
    ]
    if prepass.get("missing_sources"):
        workflows.append("adp-project-kickoff")
    return sorted(set(workflows))


def write_audit_outputs(
    audit: dict[str, Any],
    output_dir: Path,
    as_of: date,
    scenario: str,
    locale_catalog_path: Path = DEFAULT_LOCALE_CATALOG_PATH,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if audit.get("audit_type") == "artifact":
        audit_id = str(audit["artifact_validation_id"])
        stem = f"{as_of.isoformat()}-{slugify(scenario)}-{audit_id}"
        id_field = "artifact_validation_id"
    else:
        audit_id = str(audit["input_audit_id"])
        stem = f"{as_of.isoformat()}-{slugify(scenario)}-{audit_id}"
        id_field = "input_audit_id"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_exists = json_path.exists()
    markdown_exists = markdown_path.exists()
    if markdown_exists and not json_exists:
        raise OSError(f"immutable audit pair is incomplete: {markdown_path}")
    render_source = audit
    if json_path.exists():
        existing = load_json(json_path)
        if existing.get(id_field) != audit_id:
            raise OSError(f"immutable audit path collision: {json_path}")
        expected_id = (
            stable_artifact_validation_id(existing)
            if existing.get("audit_type") == "artifact"
            else stable_input_audit_id(existing)
        )
        if expected_id != audit_id or audit_content_hash(existing) != existing.get("audit_content_hash"):
            raise OSError(f"immutable audit content failed integrity validation: {json_path}")
        render_source = existing
    if markdown_path.exists():
        if read_text(markdown_path) != render_markdown(render_source, locale_catalog_path):
            raise OSError(f"immutable audit Markdown failed integrity validation: {markdown_path}")
    json_text = json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    markdown_text = render_markdown(render_source, locale_catalog_path)
    if not json_exists and not markdown_exists:
        atomic_write_pair(json_path, json_text, markdown_path, markdown_text)
    elif not markdown_exists:
        atomic_write_text(markdown_path, markdown_text)
    else:
        pass
    return {"json": str(json_path), "markdown": str(markdown_path)}


def atomic_write_pair(json_path: Path, json_text: str, markdown_path: Path, markdown_text: str) -> None:
    json_temp: str | None = None
    markdown_temp: str | None = None
    json_published = False
    markdown_published = False
    try:
        json_temp = write_temp_text(json_path, json_text)
        markdown_temp = write_temp_text(markdown_path, markdown_text)
        os.replace(markdown_temp, markdown_path)
        markdown_published = True
        os.replace(json_temp, json_path)
        json_published = True
    except OSError:
        if json_published:
            json_path.unlink(missing_ok=True)
        if markdown_published:
            markdown_path.unlink(missing_ok=True)
        raise
    finally:
        if json_temp:
            Path(json_temp).unlink(missing_ok=True)
        if markdown_temp:
            Path(markdown_temp).unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    temp = write_temp_text(path, text)
    try:
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def write_temp_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        return handle.name


def load_render_catalog(path: Path) -> dict[str, dict[str, str]]:
    try:
        value = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise OSError(f"cannot load shared locale catalog {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("en"), dict):
        raise OSError(f"shared locale catalog is invalid: {path}")
    return value


def render_message(catalog: dict[str, dict[str, str]], locale_value: str, key: str, **values: Any) -> str:
    selected = catalog.get(locale_value) if isinstance(catalog.get(locale_value), dict) else catalog["en"]
    template = selected.get(key) or catalog["en"].get(key) or key
    return str(template).format(**values)


def render_enum(
    catalog: dict[str, dict[str, str]],
    locale_value: str,
    enum_name: str,
    canonical_value: Any,
) -> str:
    value = str(canonical_value)
    key = f"enum.{enum_name}.{value}"
    rendered = render_message(catalog, locale_value, key)
    return value if rendered == key else rendered


def render_markdown(
    audit: dict[str, Any],
    locale_catalog_path: Path = DEFAULT_LOCALE_CATALOG_PATH,
) -> str:
    catalog = load_render_catalog(locale_catalog_path)
    if audit.get("audit_type") == "artifact":
        return render_artifact_validation_markdown(audit, catalog)
    locale_value = str(audit.get("locale") or "en")

    def text(key: str) -> str:
        return render_message(catalog, locale_value, key)

    def yes_no(value: Any) -> str:
        return text("value.yes") if value else text("value.no")
    finding_headers = ["Source", "Workstream", "Category", "Gap", "Recommended workflow"]
    finding_header_labels = [
        text("common.source"),
        text("common.workstream"),
        text("common.type"),
        text("common.gap"),
        text("audit.label.recommended_workflow"),
    ]
    no_findings = text("audit.value.no_findings")
    review_item = text("audit.value.review_item")
    blocking_findings = [*audit["blocking_gaps"], *audit["conflicts"]]
    warning_findings = audit.get("warning_findings")
    if warning_findings is None:
        warning_findings = [
            *audit["warnings"],
            *audit["duplicate_candidates"],
            *audit["overlap_claims"],
            *audit["stale_items"],
        ]
    lines = [
        f"# {text('audit.title.state')}",
        "",
        f"{text('common.generated')}: {audit['generated_at']}",
        f"{text('status.input_audit_id')}: {audit['input_audit_id']}",
        f"{text('audit.label.scenario')}: {audit['scenario']}",
        f"{text('audit.label.audit_status')}: {render_enum(catalog, locale_value, 'audit_status', audit['audit_status'])}",
        f"{text('audit.label.execution_disposition')}: {render_enum(catalog, locale_value, 'execution_disposition', audit['execution_disposition'])}",
        f"{text('audit.label.safe_to_generate')}: {yes_no(audit['safe_to_generate'])}",
        f"{text('audit.label.safe_to_generate_green')}: {yes_no(audit['safe_to_generate_green_report'])}",
        f"{text('status.confidence')}: {render_enum(catalog, locale_value, 'report_confidence', audit['report_confidence'])}",
        f"{text('status.baseline_revision')}: {audit['baseline_revision'] if audit['baseline_revision'] is not None else text('audit.value.missing')}",
        f"{text('audit.label.locale')}: {audit['locale']}",
        f"{text('audit.label.locale_fallback')}: {yes_no(audit['locale_fallback'])}",
        f"{text('status.generator_version')}: {audit['generator_version']}",
        f"{text('audit.label.memory_root')}: `{audit['memory_root']}`",
        "",
        f"## {text('audit.section.quality_gate')}",
        "",
        f"- {text('audit.metric.blocking_gaps')}: {audit['counts']['blocking_findings']}",
        f"- {text('audit.metric.warnings')}: {audit['counts']['warning_findings']}",
        "",
        f"## {text('audit.section.source_inventory')}",
        "",
        f"- {text('audit.metric.sources_read')}: {audit['counts']['sources_read']}",
        f"- {text('audit.metric.missing_sources')}: {audit['counts']['missing_sources']}",
        f"- {text('common.workstreams')}: {audit['counts']['workstreams']}",
        f"- {text('audit.metric.active_actions')}: {audit['counts']['active_ledger_actions']}",
        "",
    ]
    if audit["source_inventory"]["missing_sources"]:
        lines.extend([f"| {text('audit.label.missing_source')} |", "| --- |"])
        lines.extend(f"| {cell(item)} |" for item in audit["source_inventory"]["missing_sources"])
        lines.append("")

    add_table(lines, text("audit.section.blocking_gaps"), finding_headers, flatten_findings(blocking_findings, review_item), finding_header_labels, no_findings)
    add_table(lines, text("audit.metric.warnings"), finding_headers, flatten_findings(warning_findings, review_item), finding_header_labels, no_findings)

    lines.extend([f"## {text('audit.section.recommended_workflows')}", ""])
    if audit["recommended_workflows"]:
        lines.extend(f"- `{workflow}`" for workflow in audit["recommended_workflows"])
    else:
        lines.append(f"- {text('audit.value.no_audit_follow_up')}")
    lines.append("")
    return "\n".join(lines)


def render_artifact_validation_markdown(
    validation: dict[str, Any],
    catalog: dict[str, dict[str, str]],
) -> str:
    locale_value = str(validation.get("locale") or "en")

    def text(key: str) -> str:
        return render_message(catalog, locale_value, key)

    def yes_no(value: Any) -> str:
        return text("value.yes") if value else text("value.no")
    finding_headers = ["Source", "Workstream", "Gap", "Recommended workflow"]
    finding_header_labels = [
        text("common.source"),
        text("common.workstream"),
        text("common.gap"),
        text("audit.label.recommended_workflow"),
    ]
    no_findings = text("audit.value.no_findings")
    review_item = text("audit.value.review_item")
    lines = [
        f"# {text('audit.title.artifact_validation')}",
        "",
        f"{text('common.generated')}: {validation['generated_at']}",
        f"{text('audit.label.artifact_validation_id')}: {validation['artifact_validation_id']}",
        f"{text('status.input_audit_id')}: {validation['input_audit_id']}",
        f"{text('audit.label.scenario')}: {validation['scenario']}",
        f"{text('audit.label.audit_status')}: {render_enum(catalog, locale_value, 'audit_status', validation['audit_status'])}",
        f"{text('audit.label.execution_disposition')}: {render_enum(catalog, locale_value, 'execution_disposition', validation['execution_disposition'])}",
        f"{text('audit.label.safe_to_publish')}: {yes_no(validation['safe_to_publish'])}",
        f"{text('status.confidence')}: {render_enum(catalog, locale_value, 'report_confidence', validation['report_confidence'])}",
        f"{text('status.baseline_revision')}: {validation['baseline_revision']}",
        f"{text('audit.label.locale')}: {validation['locale']}",
        f"{text('status.generator_version')}: {validation['generator_version']}",
        "",
        f"## {text('audit.section.validated_artifacts')}",
        "",
    ]
    if validation["artifacts"]:
        lines.extend(f"- `{item['path']}` ({item['fingerprint']})" for item in validation["artifacts"])
    else:
        lines.append(f"- {text('audit.value.no_readable_artifacts')}")
    lines.append("")
    add_table(lines, text("audit.section.blocking_gaps"), finding_headers, flatten_findings(validation["blocking_gaps"], review_item), finding_header_labels, no_findings)
    warning_findings = validation.get("warning_findings", validation["warnings"])
    add_table(lines, text("audit.metric.warnings"), finding_headers, flatten_findings(warning_findings, review_item), finding_header_labels, no_findings)
    lines.extend([f"## {text('audit.section.recommended_workflows')}", ""])
    if validation["recommended_workflows"]:
        lines.extend(f"- `{workflow}`" for workflow in validation["recommended_workflows"])
    else:
        lines.append(f"- {text('audit.value.no_validation_follow_up')}")
    lines.append("")
    return "\n".join(lines)


def flatten_findings(items: list[dict[str, Any]], review_item: str = "review item") -> list[dict[str, str]]:
    rows = []
    for item in items:
        action_ids = item.get("action_ids")
        gap = (
            item.get("gap")
            or item.get("reason")
            or (f"duplicate action candidates: {', '.join(action_ids)}" if action_ids else "")
            or item.get("normalized_claim")
            or item.get("summary")
            or review_item
        )
        raw_sources = item.get("sources") if isinstance(item.get("sources"), list) else []
        rows.append(
            {
                "Source": str(item.get("source") or item.get("path") or ", ".join(raw_sources) or item.get("action_id") or ""),
                "Workstream": str(item.get("workstream") or ", ".join(item.get("workstreams", [])) if isinstance(item.get("workstreams"), list) else item.get("workstream", "")),
                "Category": str(item.get("category") or item.get("kind") or ""),
                "Gap": str(gap),
                "Recommended workflow": str(item.get("recommended_workflow", "")),
            }
        )
    return rows


def add_table(
    lines: list[str],
    title: str,
    headers: list[str],
    rows: list[dict[str, str]],
    header_labels: list[str] | None = None,
    empty_message: str = "No findings.",
) -> None:
    lines.extend([f"## {title}", ""])
    if not rows:
        lines.extend([empty_message, ""])
        return
    lines.append("| " + " | ".join(header_labels or headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(cell(row.get(header, "")) for header in headers) + " |")
    lines.append("")


def view_has_explicit_placeholder(path: Path) -> bool:
    text = read_text(path)
    metadata = view_metadata(text)
    template_status = normalize_status(metadata.get("template_status"))
    if template_status == "placeholder":
        return True
    if template_status in {"generated", "complete"} or parse_datetime(str(metadata.get("generated_at", ""))):
        return False
    return not view_has_data_rows(text)


def view_lineage_gaps(
    path: Path,
    rel: str,
    view_source: dict[str, Any],
    sources: list[dict[str, Any]],
    memory_root: Path,
) -> list[str]:
    metadata = view_metadata(read_text(path))
    reasons: list[str] = []
    source_paths = metadata.get("source_paths", [])
    relevant = relevant_view_sources(rel, source_paths, sources)
    view_time = parse_datetime(str(metadata.get("generated_at", ""))) or parse_datetime(
        str(view_source.get("modified", ""))
    )
    source_times = [parse_datetime(str(source.get("modified", ""))) for source in relevant]
    latest_source = max((value for value in source_times if value), default=None)
    if latest_source and view_time and latest_source > view_time:
        reasons.append("view is older than one or more lineage source records")

    missing_lineage_sources: set[str] = set()
    for source_path in source_paths:
        source_file = resolve_contained_path(memory_root, source_path)
        if source_file is None:
            reasons.append(f"lineage source path escapes memory root: {source_path}")
            missing_lineage_sources.add(source_path)
        elif not source_file.exists():
            reasons.append(f"lineage source is missing: {source_path}")
            missing_lineage_sources.add(source_path)
    for source_path, expected_hash in metadata.get("source_hashes", {}).items():
        source_file = resolve_contained_path(memory_root, source_path)
        if source_file is None:
            if source_path not in missing_lineage_sources:
                reasons.append(f"lineage source path escapes memory root: {source_path}")
            continue
        if not source_file.exists():
            if source_path not in missing_lineage_sources:
                reasons.append(f"lineage source is missing: {source_path}")
            continue
        actual_hash = hashlib.sha256(source_file.read_bytes()).hexdigest()
        if str(expected_hash).lower().removeprefix("sha256:") != actual_hash:
            reasons.append(f"lineage source hash changed: {source_path}")
    return reasons


def relevant_view_sources(
    rel: str,
    explicit_paths: Any,
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(explicit_paths, list) and explicit_paths:
        selected = {str(path) for path in explicit_paths}
        return [source for source in sources if str(source.get("path", "")) in selected]
    patterns = VIEW_SOURCE_PATTERNS.get(rel, ())
    return [
        source
        for source in sources
        if any(source_path_matches(str(source.get("path", "")), pattern) for pattern in patterns)
    ]


def source_path_matches(path: str, pattern: str) -> bool:
    if "/**/" in pattern:
        prefix, suffix = pattern.split("/**/", 1)
        return path.startswith(f"{prefix}/") and Path(path).match(f"**/{suffix}")
    return Path(path).match(pattern)


def view_metadata(text: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    labels = {
        "generated": "generated_at",
        "generated_at": "generated_at",
        "template status": "template_status",
        "template_status": "template_status",
        "source paths": "source_paths",
        "source_paths": "source_paths",
        "source hashes": "source_hashes",
        "source_hashes": "source_hashes",
    }
    for line in text.splitlines():
        match = re.match(r"^\s*(?:-\s*)?([A-Za-z_ ]+)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        key = labels.get(match.group(1).strip().lower())
        if key:
            fields[key] = match.group(2).strip()
    return {
        "generated_at": fields.get("generated_at", ""),
        "template_status": fields.get("template_status", ""),
        "source_paths": parse_string_list(fields.get("source_paths", "")),
        "source_hashes": parse_string_map(fields.get("source_hashes", "")),
    }


def parse_string_list(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in value.split(",")]
    if not isinstance(parsed, list):
        return []
    return [str(part).strip() for part in parsed if str(part).strip()]


def parse_string_map(value: str) -> dict[str, str]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(item) for key, item in parsed.items()}


def view_has_data_rows(text: str) -> bool:
    in_table = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [part.strip() for part in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                in_table = True
                continue
            if in_table and any(is_meaningful(cell) for cell in cells):
                return True
            continue
        in_table = False
    return False


def action_item(action: dict[str, Any], reason: str, category: str) -> dict[str, Any]:
    return {
        "action_id": action.get("action_id", ""),
        "status": action.get("status", ""),
        "owner": action.get("owner", ""),
        "workstream": action.get("workstream", ""),
        "source": action.get("source", "actions/action-ledger.md"),
        "action": action.get("action", ""),
        "gap": reason,
        "recommended_workflow": "adp-status-sync",
        "category": category,
    }


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    path = Path(raw_memory_root)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_contained_path(root: Path, raw_path: str) -> Path | None:
    path = Path(raw_path)
    if path.is_absolute():
        return None
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def resolve_output_dir(
    raw_output_dir: str | None,
    memory_root: Path,
    run_folder_pattern: str,
    as_of: date,
    scenario: str,
) -> Path:
    if not raw_output_dir:
        path = memory_root / "audits"
    else:
        path = Path(raw_output_dir)
        if not path.is_absolute():
            path = memory_root / path
    path = path.resolve()
    run_folder = format_run_folder(run_folder_pattern, as_of, scenario)
    if run_folder:
        resolved_run_folder = resolve_contained_path(path, run_folder)
        if resolved_run_folder is None:
            raise ValueError("run_folder_pattern must resolve inside the audit output directory")
        path = resolved_run_folder
    return path


def format_run_folder(pattern: str, as_of: date, scenario: str) -> str:
    text = str(pattern or "").strip().strip("/\\")
    if not text:
        return ""
    return (
        text.replace("{date}", as_of.isoformat())
        .replace("{scenario}", slugify(scenario))
        .replace("{scenario_raw}", scenario)
        .strip("/\\")
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def rel_to_memory(memory_root: Path, path: Path) -> str:
    try:
        return path.relative_to(memory_root).as_posix()
    except ValueError:
        return path.as_posix()


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in [text, text.replace("Z", "+00:00")]:
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            pass
    match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def extract_colon_field(text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*(?:[-*+]\s+)?{re.escape(label)}\s*[:：]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def section_text(text: str, heading: str) -> str:
    lines = text.splitlines()
    marker = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.IGNORECASE)
    start = None
    for index, line in enumerate(lines):
        if marker.match(line.strip()):
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if re.match(r"^##\s+", lines[index].strip()):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def is_meaningful(value: Any) -> bool:
    text = str(value or "").strip().strip("`")
    text = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", text).strip().strip("`")
    return text.lower() not in PLACEHOLDERS


def is_missing_due(value: Any) -> bool:
    return not is_meaningful(value)


def is_missing_owner(value: str) -> bool:
    return not is_meaningful(value)


def is_missing_closure_criteria(value: Any) -> bool:
    return not is_meaningful(value)


def normalize_status(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return "cancelled" if normalized == "canceled" else normalized


def decision_status_is_terminal(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    localized = {
        "已接受": "accepted",
        "已关闭": "closed",
        "已完成": "done",
        "已取消": "cancelled",
        "已拒绝": "rejected",
        "已取代": "superseded",
        "已废弃": "superseded",
    }
    return localized.get(raw, normalize_status(raw)) in TERMINAL_DECISION_STATUSES


def normalize_text_key(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def slugify(value: str) -> str:
    value = normalize_text_key(value).replace(" ", "-")
    return value[:80] or "item"


def cell(value: Any) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|")


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


def decode_process_output(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ["utf-8-sig", locale.getpreferredencoding(False), "mbcs"]:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


if __name__ == "__main__":
    sys.exit(main())
