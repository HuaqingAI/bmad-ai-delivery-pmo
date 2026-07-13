#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read canonical ADP program status for Program Lead interpretation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
DEFAULT_PROGRAM_STATUS_SCRIPT = SKILLS_ROOT / "adp-program-status/scripts/program_status.py"
STATUS_VALUES = {"on-plan", "at-risk", "off-plan", "indeterminate"}
CONFIDENCE_VALUES = {"high", "medium", "low", "unknown"}
INTENTS = {"overall", "period-review", "meeting-preparation", "recovery-routing"}
SCENARIOS = {"fde-morning", "business-biweekly"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consume canonical ADP program-status without recomputing project judgment "
            "or rewriting management views."
        )
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--intent", choices=sorted(INTENTS), default="overall")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="Meeting scenario for meeting-preparation.")
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

    recovery_workflows = unique_strings(inspection.get("recommended_workflows", []))

    return {
        "ok": True,
        "status": "complete",
        "mode": "canonical-consumer",
        "intent": args.intent,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "canonical_status": canonical_summary(model),
        "period_review": model.get("period_delta", {}),
        "recovery_routing": {
            "required": bool(recovery_workflows),
            "recommended_workflows": recovery_workflows,
        },
        "meeting_preparation": meeting_preparation(args, model),
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
