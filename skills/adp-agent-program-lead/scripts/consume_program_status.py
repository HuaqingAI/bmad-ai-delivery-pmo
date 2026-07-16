#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read canonical ADP program status for Program Lead interpretation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
DEFAULT_PROGRAM_STATUS_SCRIPT = SKILLS_ROOT / "adp-program-status/scripts/program_status.py"
PANEL_MODEL_SCRIPT = SKILLS_ROOT / "adp-management-panel/scripts/panel_model.py"
STATUS_VALUES = {"on-plan", "at-risk", "off-plan", "indeterminate"}
CONFIDENCE_VALUES = {"high", "medium", "low", "unknown"}
INTENTS = {
    "overall",
    "period-review",
    "meeting-preparation",
    "recovery-routing",
    "panel-readiness",
    "panel-refresh",
    "panel-open",
    "panel-archive",
}
SCENARIOS = {"fde-morning", "business-biweekly"}
PANEL_VIEWS = {"project-lead", "fde-morning", "business-biweekly"}
PANEL_INTENTS = {"panel-readiness", "panel-refresh", "panel-open", "panel-archive"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consume canonical ADP program-status without recomputing project judgment "
            "or rewriting management views."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Legacy render_program_views.py compatibility:\n"
            "  project_root, --view, --memory-root, --as-of, and -o/--output remain read-only aliases.\n"
            "  Retired renderer-only options return ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED;\n"
            "  regenerate canonical Markdown with adp-program-status, then consume it here."
        ),
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--intent", choices=sorted(INTENTS), default="overall")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="Meeting scenario for meeting-preparation.")
    parser.add_argument("--panel-view", choices=sorted(PANEL_VIEWS), default="project-lead")
    parser.add_argument("--distribution-profile", choices=["internal-full", "shareable-summary"])
    parser.add_argument(
        "--view",
        choices=["all", "project-lead", "weekly-report"],
        default="all",
        help="Canonical management view paths to require. Retained for compatibility with the former renderer.",
    )
    parser.add_argument(
        "--memory-root",
        default=DEFAULT_MEMORY_ROOT,
        help=f"ADP state root, relative to project root unless absolute. Default: {DEFAULT_MEMORY_ROOT}.",
    )
    parser.add_argument(
        "--program-status-script",
        default=str(DEFAULT_PROGRAM_STATUS_SCRIPT),
        help="Installed adp-program-status deterministic contract implementation.",
    )
    parser.add_argument(
        "--as-of",
        help="Require the canonical status to use this ISO YYYY-MM-DD as-of date; never recalculates status.",
    )
    parser.add_argument("-o", "--output", help="Write result JSON to this file instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "ok": False,
            "status": "error",
            "reason": str(exc),
            "recommended_workflows": [],
        }
    emit(result, args.output)
    if result.get("ok"):
        return 0
    return 1 if result.get("status") == "blocked" else 2


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError("project_root is not an existing directory")
    memory_root = resolve_path(project_root, args.memory_root)
    if not memory_root.is_dir():
        return blocked(
            project_root,
            memory_root,
            "ADP state root is missing",
            ["adp-project-kickoff"],
        )
    if args.intent == "meeting-preparation" and not args.scenario:
        raise ValueError("--scenario is required for meeting-preparation")
    if args.intent == "panel-archive" and not args.distribution_profile:
        raise ValueError("--distribution-profile is required for panel-archive")

    inspection = inspect_canonical(args, project_root)
    if not inspection.get("ok"):
        return blocked(
            project_root,
            memory_root,
            str(inspection.get("reason") or "canonical program status is unavailable"),
            list(inspection.get("recommended_workflows") or ["adp-program-status"]),
            inspection=inspection,
        )

    status_path = Path(str(inspection["outputs"]["program_status_json"])).resolve()
    model = load_json(status_path)
    validation_errors = validate_model(model)
    snapshot_id = str(model.get("snapshot_id") or "")
    snapshot_path = memory_root / "snapshots" / "program-status" / f"{snapshot_id}.json"
    latest_path = memory_root / "snapshots" / "program-status" / "latest.json"
    validation_errors.extend(validate_lineage(model, snapshot_path, latest_path))
    if validation_errors:
        return blocked(
            project_root,
            memory_root,
            "; ".join(validation_errors),
            ["adp-state-audit", "adp-program-status"],
        )

    if args.as_of:
        requested_as_of = date.fromisoformat(args.as_of).isoformat()
        if model["as_of"] != requested_as_of:
            return blocked(
                project_root,
                memory_root,
                f"canonical program status as_of is {model['as_of']}, not requested {requested_as_of}",
                ["adp-state-audit", "adp-program-status"],
                canonical_status=canonical_summary(model),
            )

    required_views = requested_views(args.view, memory_root)
    missing_views = [name for name, path in required_views.items() if not path.is_file()]
    if missing_views:
        return blocked(
            project_root,
            memory_root,
            "canonical management views are missing: " + ", ".join(missing_views),
            ["adp-program-status"],
            canonical_status=canonical_summary(model),
        )
    metadata_errors: list[str] = []
    missing_lineage = False
    for name, path in required_views.items():
        errors, missing = validate_management_markdown(name, path, model)
        metadata_errors.extend(errors)
        missing_lineage = missing_lineage or missing
    if metadata_errors:
        return blocked(
            project_root,
            memory_root,
            "; ".join(metadata_errors),
            ["adp-state-audit", "adp-program-status"],
            error_code=(
                "ADP-PL-MANAGEMENT-MARKDOWN-LINEAGE-MISSING"
                if missing_lineage
                else "ADP-PL-MANAGEMENT-MARKDOWN-LINEAGE-MISMATCH"
            ),
            canonical_status=canonical_summary(model),
        )

    recovery_workflows = unique_strings(inspection.get("recommended_workflows", []))

    summary = canonical_summary(model)
    panel = panel_journey(args, memory_root, summary)
    return {
        "ok": True,
        "status": "complete",
        "mode": "canonical-consumer",
        "intent": args.intent,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "canonical_status": summary,
        "period_review": model.get("period_delta", {}),
        "recovery_routing": {
            "required": bool(recovery_workflows),
            "recommended_workflows": recovery_workflows,
        },
        "meeting_preparation": meeting_preparation(args, model),
        "panel_journey": panel,
        "management_markdown_lineage": {
            "status": "verified",
            "snapshot_id": model.get("snapshot_id"),
            "views": {
                name: {
                    "path": str(path),
                    "coverage_profile": (
                        "adp-project-lead-markdown" if name == "project_lead" else "adp-weekly-report-markdown"
                    ),
                }
                for name, path in required_views.items()
            },
        },
        "outputs": {name: str(path) for name, path in required_views.items()},
        "source": {
            "program_status_json": str(status_path),
            "snapshot": str(snapshot_path),
            "latest": str(latest_path),
        },
        "writes_performed": [],
    }


def inspect_canonical(args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    script = Path(args.program_status_script).expanduser().resolve()
    if not script.is_file():
        return {
            "ok": False,
            "reason": f"adp-program-status script is missing: {script}",
            "recommended_workflows": ["adp-setup"],
        }
    command = [
        sys.executable,
        str(script),
        str(project_root),
        "--mode",
        "inspect",
        "--memory-root",
        args.memory_root,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "reason": (completed.stderr or completed.stdout or "adp-program-status emitted invalid JSON").strip(),
            "recommended_workflows": ["adp-program-status"],
        }
    if completed.returncode != 0 or not payload.get("ok"):
        payload.setdefault("ok", False)
        payload.setdefault("reason", payload.get("error") or completed.stderr.strip() or "canonical inspection failed")
    return payload


def validate_model(model: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = [
        "snapshot_id",
        "overall_status",
        "report_confidence",
        "baseline_revision",
        "input_audit_id",
        "as_of",
        "reporting_period",
        "period_delta",
        "generator_version",
        "locale",
    ]
    missing = [key for key in required if model.get(key) is None or model.get(key) == ""]
    if missing:
        errors.append("canonical program status is missing fields: " + ", ".join(missing))
    if model.get("overall_status") not in STATUS_VALUES:
        errors.append("canonical program status has an invalid overall_status")
    if model.get("report_confidence") not in CONFIDENCE_VALUES:
        errors.append("canonical program status has an invalid report_confidence")
    return errors


def validate_lineage(model: dict[str, Any], snapshot_path: Path, latest_path: Path) -> list[str]:
    errors: list[str] = []
    if not snapshot_path.is_file():
        errors.append("immutable program-status snapshot is missing")
    else:
        snapshot = load_json(snapshot_path)
        if snapshot != model:
            errors.append("canonical program-status view does not match its immutable snapshot")
    if not latest_path.is_file():
        errors.append("program-status latest pointer is missing")
    else:
        latest = load_json(latest_path)
        if latest.get("snapshot_id") != model.get("snapshot_id"):
            errors.append("program-status latest pointer references a different snapshot")
        if latest.get("baseline_revision") != model.get("baseline_revision"):
            errors.append("program-status latest pointer references a different baseline revision")
    return errors


def canonical_summary(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": model.get("snapshot_id"),
        "overall_status": model.get("overall_status"),
        "overall_status_label": model.get("overall_status_label"),
        "overall_rule_id": model.get("overall_rule_id"),
        "report_confidence": model.get("report_confidence"),
        "report_confidence_label": model.get("report_confidence_label"),
        "confidence_reasons": model.get("confidence_reasons", []),
        "baseline_revision": model.get("baseline_revision"),
        "input_audit_id": model.get("input_audit_id"),
        "as_of": model.get("as_of"),
        "reporting_period": model.get("reporting_period"),
        "project": model.get("project", {}),
        "progress": model.get("progress"),
        "critical_path": model.get("critical_path", []),
        "variances": model.get("variances", []),
        "rule_ids": model.get("rule_ids", []),
        "locale": model.get("locale"),
        "locale_fallback": bool(model.get("locale_fallback")),
        "generator_version": model.get("generator_version"),
        "source_fingerprints": model.get("source_fingerprints", {}),
    }


def meeting_preparation(args: argparse.Namespace, model: dict[str, Any]) -> dict[str, Any] | None:
    if args.intent != "meeting-preparation":
        return None
    return {
        "scenario": args.scenario,
        "status": "route_required",
        "owning_workflow": "adp-meeting-pack",
        "lineage": {
            "program_status_snapshot_id": model.get("snapshot_id"),
            "baseline_revision": model.get("baseline_revision"),
            "input_audit_id": model.get("input_audit_id"),
            "generator_version": model.get("generator_version"),
            "source_fingerprints": model.get("source_fingerprints", {}),
        },
    }


def panel_journey(
    args: argparse.Namespace, memory_root: Path, canonical_status: dict[str, Any]
) -> dict[str, Any] | None:
    if args.intent not in PANEL_INTENTS:
        return None
    operation = args.intent.removeprefix("panel-")
    base = {
        "operation": operation,
        "view": args.panel_view,
        "owning_workflow": "adp-management-panel",
        "canonical_snapshot_id": canonical_status.get("snapshot_id"),
    }
    if operation == "refresh":
        return {
            **base,
            "status": "route-required",
            "route": {"operation": "refresh", "view": args.panel_view},
            "explanation": canonical_view_summary(args.panel_view, canonical_status),
            "writes_performed": [],
        }
    if operation == "archive":
        return {
            **base,
            "status": "route-required",
            "distribution_profile": args.distribution_profile,
            "route": {
                "operation": "archive",
                "view": args.panel_view,
                "distribution_profile": args.distribution_profile,
            },
            "official_association": {
                "status": "pending-successful-meeting-sync",
                "owning_workflow": "adp-meeting-sync",
                "rule": "The archive becomes post-sync-official only when an applied meeting-sync receipt records its panel ID.",
            },
            "explanation": canonical_view_summary(args.panel_view, canonical_status),
            "writes_performed": [],
        }

    inspection = inspect_panel(memory_root)
    if not inspection.get("ok"):
        return {
            **base,
            "status": "blocked",
            "reason": inspection["reason"],
            "recommended_workflows": ["adp-management-panel"],
            "writes_performed": [],
        }
    manifest = inspection["manifest"]
    model = inspection["model"]
    if manifest.get("program_status_snapshot_id") != canonical_status.get("snapshot_id"):
        return {
            **base,
            "status": "blocked",
            "reason": "current panel snapshot does not match canonical program status",
            "panel_id": manifest.get("panel_id"),
            "panel_snapshot_id": manifest.get("program_status_snapshot_id"),
            "recommended_workflows": ["adp-management-panel"],
            "writes_performed": [],
        }
    explanation = view_specific_explanation(args.panel_view, model)
    readiness = panel_view_readiness(args.panel_view, manifest, explanation)
    result = {
        **base,
        "status": readiness,
        "panel_id": manifest.get("panel_id"),
        "panel_model_id": manifest.get("panel_model_id"),
        "recovery_status": manifest.get("recovery_status"),
        "current_html": inspection["current_html"],
        "explanation": explanation,
        "recommended_workflows": list(model.get("recovery", {}).get("workflows", [])),
        "writes_performed": [],
    }
    if operation == "open":
        result["open_hash"] = f"#v=1&view={args.panel_view}&mode=quantitative-progress"
    return result


def canonical_view_summary(view: str, status: dict[str, Any]) -> dict[str, Any]:
    result = {
        "view": view,
        "snapshot_id": status.get("snapshot_id"),
        "overall_status": status.get("overall_status"),
        "report_confidence": status.get("report_confidence"),
    }
    progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
    if view == "project-lead":
        result["progress"] = progress
        result["critical_path"] = status.get("critical_path", [])
    elif view == "business-biweekly":
        result["progress_current"] = progress.get("overall", {}).get("current")
        result["forecast_summary"] = progress.get("overall", {}).get("forecast_summary")
    return result


def inspect_panel(memory_root: Path) -> dict[str, Any]:
    current = memory_root / "views/management-panel/index.html"
    if not current.is_file():
        return {"ok": False, "reason": "current management panel is missing; route refresh"}
    text = current.read_text(encoding="utf-8")
    try:
        manifest = embedded_json(text, "adp-panel-manifest")
        model = embedded_json(text, "adp-panel-model")
    except (ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"current management panel is invalid: {exc}"}
    if manifest != model.get("manifest"):
        return {"ok": False, "reason": "current panel manifest differs from its embedded model"}
    spec = importlib.util.spec_from_file_location("adp_program_lead_panel_model", PANEL_MODEL_SCRIPT)
    if spec is None or spec.loader is None:
        return {"ok": False, "reason": "management panel artifact contract is unavailable"}
    panel_model_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel_model_module)
    try:
        bundle = panel_model_module.existing_panel_bundle_path(
            memory_root / "snapshots/management-panel", manifest.get("panel_id")
        )
    except ValueError as exc:
        return {"ok": False, "reason": f"current management panel is invalid: {exc}"}
    if not bundle.is_file() or load_json(bundle) != model:
        return {"ok": False, "reason": "current panel does not match its immutable bundle"}
    return {
        "ok": True,
        "manifest": manifest,
        "model": model,
        "current_html": str(current),
        "immutable_bundle": str(bundle),
    }


def embedded_json(text: str, element_id: str) -> dict[str, Any]:
    opener = f'<script type="application/json" id="{element_id}">'
    start = text.find(opener)
    if start < 0:
        raise ValueError(f"embedded {element_id} is missing")
    start += len(opener)
    end = text.find("</script>", start)
    if end < 0:
        raise ValueError(f"embedded {element_id} is unterminated")
    value = json.loads(text[start:end])
    if not isinstance(value, dict):
        raise ValueError(f"embedded {element_id} must be an object")
    return value


def view_specific_explanation(view: str, model: dict[str, Any]) -> dict[str, Any]:
    status = model.get("data", {}).get("status", {})
    flow = model.get("data", {}).get("flows", {}).get(view, {})
    common = {
        "view": view,
        "snapshot_id": status.get("snapshot_id"),
        "overall_status": status.get("overall_status"),
        "report_confidence": status.get("report_confidence"),
        "flow_selection_id": flow.get("selection_id"),
        "execution_health": [
            {
                "node_id": item.get("node_id"),
                "execution": item.get("execution"),
                "health": item.get("health"),
            }
            for item in flow.get("node_states", [])
        ],
        "scoped_counts": flow.get("allocations", []),
    }
    if view == "project-lead":
        common["progress"] = status.get("progress")
        common["critical_path"] = status.get("critical_path", [])
        return common
    meeting = model.get("data", {}).get("meetings", {}).get(view, {})
    common.update(
        {
            "meeting_pack_id": meeting.get("meeting_pack_id"),
            "meeting_window": meeting.get("meeting_window"),
            "meeting_readiness": meeting.get("readiness"),
            "artifact_lifecycle": meeting.get("lifecycle"),
        }
    )
    boards = meeting.get("boards", {})
    if view == "fde-morning":
        common["current_completion_gap_pp"] = status.get("progress", {}).get("overall", {}).get("current", {}).get("completion_gap_pp")
        common["progress_delta"] = boards.get("fde_progress_delta", boards.get("fde_period_delta", []))
        common["forecast_milestones"] = boards.get("fde_forecast_milestones", [])
        common["blockers"] = boards.get("fde_blockers", [])
        common["commitments"] = boards.get("fde_commitments", [])
    else:
        overall = status.get("progress", {}).get("overall", {})
        common["progress_current"] = overall.get("current")
        common["forecast_summary"] = overall.get("forecast_summary")
        common["decisions"] = boards.get("business_decisions", [])
        common["business_readiness"] = boards.get("business_readiness", [])
    return common


def panel_view_readiness(view: str, manifest: dict[str, Any], explanation: dict[str, Any]) -> str:
    if manifest.get("recovery_status") == "blocked":
        return "blocked"
    if view == "project-lead":
        return "degraded" if manifest.get("recovery_status") == "degraded" else "ready"
    meeting = explanation.get("meeting_readiness")
    lifecycle = explanation.get("artifact_lifecycle")
    if meeting == "blocked":
        return "blocked"
    if meeting != "ready" or lifecycle == "sync-failed" or manifest.get("recovery_status") == "degraded":
        return "degraded"
    return "ready"


def requested_views(raw: str, memory_root: Path) -> dict[str, Path]:
    all_views = {
        "project_lead": memory_root / "views" / "project-lead.md",
        "weekly_report": memory_root / "views" / "weekly-report.md",
    }
    if raw == "project-lead":
        return {"project_lead": all_views["project_lead"]}
    if raw == "weekly-report":
        return {"weekly_report": all_views["weekly_report"]}
    return all_views


def markdown_artifact_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    marker = "<!-- adp:artifact-metadata:v1 -->"
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError(f"{path.name} is missing {marker}")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker_index:], re.DOTALL)
    if not match:
        raise ValueError(f"{path.name} is missing metadata JSON after {marker}")
    metadata = json.loads(match.group(1))
    if not isinstance(metadata, dict):
        raise ValueError(f"{path.name} metadata must be an object")
    return metadata


def validate_management_markdown(name: str, path: Path, model: dict[str, Any]) -> tuple[list[str], bool]:
    try:
        metadata = markdown_artifact_metadata(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)], True
    progress = model.get("progress") if isinstance(model.get("progress"), dict) else {}
    flow_state = model.get("flow_state") if isinstance(model.get("flow_state"), dict) else {}
    expected = {
        "snapshot_id": model.get("snapshot_id"),
        "generated_at": model.get("generated_at"),
        "as_of": model.get("as_of"),
        "reporting_period": model.get("reporting_period"),
        "report_confidence": model.get("report_confidence"),
        "scenario": model.get("scenario"),
        "input_audit_id": model.get("input_audit_id"),
        "baseline_revision": model.get("baseline_revision"),
        "source_fingerprints": model.get("source_fingerprints"),
        "locale": model.get("locale"),
        "locale_fallback": bool(model.get("locale_fallback")),
        "generator_version": model.get("generator_version"),
        "progress_schema_version": progress.get("progress_schema_version"),
        "progress_scope_identity": progress.get("scope_identity"),
        "flow_state_schema_version": flow_state.get("flow_state_schema_version"),
    }
    errors = [
        f"{path.name} lineage {key} does not match canonical snapshot"
        for key, value in expected.items()
        if metadata.get(key) != value
    ]
    render_contract = metadata.get("render_contract")
    profile = "adp-project-lead-markdown" if name == "project_lead" else "adp-weekly-report-markdown"
    canonical_render = model.get("render_contract") if isinstance(model.get("render_contract"), dict) else {}
    if not isinstance(render_contract, dict):
        errors.append(f"{path.name} render_contract is missing")
    else:
        render_expected = {
            "coverage_profile": profile,
            "catalog_locale": model.get("locale"),
            "catalog_fingerprint": canonical_render.get("catalog_fingerprint"),
            "unresolved_message_keys": [],
            "source_fact_translation_persisted": False,
        }
        errors.extend(
            f"{path.name} render_contract {key} does not match canonical snapshot"
            for key, value in render_expected.items()
            if render_contract.get(key) != value
        )
    return errors, False


def blocked(
    project_root: Path,
    memory_root: Path,
    reason: str,
    recommended_workflows: list[str],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "mode": "canonical-consumer",
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "reason": reason,
        "recommended_workflows": unique_strings(recommended_workflows),
        "writes_performed": [],
        **extra,
    }


def unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def resolve_path(project_root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def emit(payload: dict[str, Any], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(text)


if __name__ == "__main__":
    sys.exit(main())
