#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Plan, apply, resume, and inspect end-to-end ADP management-panel refreshes."""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
DEFAULT_MEMORY_ROOT = "_bmad-output/adp/memory"
STATUS_REL = Path("state/panel-refresh-status.json")
RUNS_REL = Path("state/panel-refresh/runs")
RECEIPTS_REL = Path("receipts/panel-refresh")
POLICY_CANDIDATES_REL = Path("state/panel-refresh/selection-policy-candidates.json")
POLICIES_REL = Path("state/panel-refresh/policies")
FACT_LOCK_REL = Path("state/fact-write.lock")
REFRESH_ID_PATTERN = re.compile(r"refresh-[0-9a-f]{24}")
WINDOWS_LOCK_RETRY_SECONDS = 0.05
WINDOWS_LOCK_CONTENTION_ERRORS = {
    error
    for error in (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", None))
    if error is not None
}
WINDOWS_LOCK_CONTENTION_WINERRORS = {33, 36}
SOURCE_PREFIXES = (
    "actions/",
    "cadence.md",
    "daily/",
    "decisions/",
    "evidence/",
    "intake/",
    "l0/",
    "meetings/",
    "plans/",
    "readiness/",
    "state/fact-generation.json",
    "state/status-intent-outbox.json",
    "workstreams/",
)
DERIVED_PREFIXES = ("audits/", "snapshots/", "views/")
PUBLISHABLE_STATE_PREFIXES = (
    "state/management-panel",
    "state/panel-current",
    "state/panel-state",
)
NODE_ORDER = (
    "state-audit",
    "program-status",
    "roadmap",
    "flow-graph",
    "meeting-pack:fde-morning",
    "meeting-pack:business-biweekly",
    "management-panel",
)
SCRIPT_PATHS = {
    "state-audit": SKILLS_ROOT / "adp-state-audit/scripts/audit_state.py",
    "program-status": SKILLS_ROOT / "adp-program-status/scripts/program_status.py",
    "roadmap": SKILLS_ROOT / "adp-roadmap-sync/scripts/render_roadmap.py",
    "flow-graph": SKILLS_ROOT / "adp-flow-graph/scripts/flow_graph.py",
    "meeting-pack": SKILLS_ROOT / "adp-meeting-pack/scripts/render_meeting_pack.py",
    "management-panel": SKILLS_ROOT / "adp-management-panel/scripts/management_panel.py",
    "effective-config": SKILLS_ROOT / "adp-plan-baseline/scripts/adp_effective_config.py",
}


class RefreshError(RuntimeError):
    def __init__(self, code: str, message: str, *, node: str | None = None) -> None:
        self.code = code
        self.node = node
        super().__init__(message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("policy", "detect", "plan", "apply", "inspect"))
    parser.add_argument("project_root")
    parser.add_argument("--memory-root", default=DEFAULT_MEMORY_ROOT)
    parser.add_argument("--as-of", help="Source date in YYYY-MM-DD. Default: today.")
    parser.add_argument("--period-start", help="Program-status period start. Default: as-of minus six days.")
    parser.add_argument("--period-end", help="Program-status period end. Default: as-of.")
    parser.add_argument("--fde-period-start", help="Confirmed FDE meeting window start.")
    parser.add_argument("--fde-period-end", help="Confirmed FDE meeting window end.")
    parser.add_argument("--selection-policy", help="Explicit management-panel selection policy JSON.")
    parser.add_argument("--plan", help="Durable refresh plan JSON returned by plan.")
    parser.add_argument("--fixture", action="store_true", help="Run the frozen panel fixture path for tests.")
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--fail-after-node", help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("-o", "--output")
    return parser.parse_args(argv)


def resolve_project_root(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise RefreshError("PROJECT_ROOT_MISSING", f"project root does not exist: {path}")
    return path


def resolve_memory_root(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def parse_day(value: str | None, label: str, default: date) -> date:
    if not value:
        return default
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RefreshError("DATE_INVALID", f"{label} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise RefreshError("DATE_INVALID", f"{label} must use canonical YYYY-MM-DD")
    return parsed


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_id(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def optional_file_fingerprint(path: Path) -> str | None:
    return file_fingerprint(path) if path.is_file() else None


def is_runtime_lock_path(path: Path) -> bool:
    return path.name.lower().endswith(".lock")


def ignore_runtime_lock_files(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if is_runtime_lock_path(Path(name))}


def resolve_external_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RefreshError("JSON_MISSING", f"required JSON is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RefreshError("JSON_INVALID", f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefreshError("JSON_INVALID", f"JSON must be an object: {path}")
    return value


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_effective_config_module() -> Any:
    path = SCRIPT_PATHS["effective-config"]
    if not path.is_file():
        raise RefreshError(
            "EFFECTIVE_CONFIG_UNAVAILABLE",
            f"shared effective-config resolver is missing: {path}",
        )
    spec = importlib.util.spec_from_file_location("adp_effective_config_for_refresh", path)
    if spec is None or spec.loader is None:
        raise RefreshError(
            "EFFECTIVE_CONFIG_UNAVAILABLE",
            f"cannot load shared effective-config resolver: {path}",
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def effective_config_inventory(project_root: Path) -> dict[str, str]:
    module = load_effective_config_module()
    code, resolved = module.resolve_effective_config(project_root)
    if code != 0 or not resolved.get("ok"):
        raise RefreshError(
            "EFFECTIVE_CONFIG_UNAVAILABLE",
            str(resolved.get("error") or "shared effective config could not be resolved"),
        )
    inventory: dict[str, str] = {}
    for source in resolved.get("sources_checked", []):
        if not isinstance(source, dict) or source.get("exists") is not True:
            continue
        path = Path(str(source.get("path") or ""))
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(project_root).as_posix()
        except ValueError as exc:
            raise RefreshError(
                "EFFECTIVE_CONFIG_INVALID",
                f"effective-config source is outside project root: {path}",
            ) from exc
        inventory[relative] = file_fingerprint(path)
    return inventory


def source_inventory(
    memory_root: Path,
    project_root: Path | None = None,
) -> dict[str, str]:
    if not memory_root.is_dir():
        raise RefreshError("MEMORY_ROOT_MISSING", f"ADP memory root does not exist: {memory_root}")
    inventory: dict[str, str] = {}
    for path in sorted(memory_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(memory_root).as_posix()
        if is_runtime_lock_path(path) or any(
            part.startswith(".") for part in Path(relative).parts
        ):
            continue
        if relative == "state/status-intent-outbox.json" or any(
            relative == prefix or relative.startswith(prefix)
            for prefix in SOURCE_PREFIXES
        ):
            inventory[relative] = file_fingerprint(path)
    if project_root is not None:
        inventory.update(effective_config_inventory(project_root.resolve()))
    return dict(sorted(inventory.items()))


def pending_intent_ids(memory_root: Path) -> list[str]:
    path = memory_root / "state/status-intent-outbox.json"
    if not path.is_file():
        return []
    try:
        outbox = load_json(path)
    except RefreshError as exc:
        raise RefreshError("STATUS_INTENT_OUTBOX_INVALID", str(exc)) from exc
    rows = outbox.get("intents")
    if (
        outbox.get("schema_version") != "1.0.0"
        or not isinstance(rows, list)
        or not all(isinstance(outbox.get(key), list) for key in ("pending", "consumed", "failed", "waived"))
        or outbox.get("failed") != []
        or outbox.get("waived") != []
    ):
        raise RefreshError("STATUS_INTENT_OUTBOX_INVALID", f"status intent outbox structure is invalid: {path}")
    body = dict(outbox)
    claimed_state_id = body.pop("state_id", None)
    if claimed_state_id != content_id(body):
        raise RefreshError("STATUS_INTENT_OUTBOX_INVALID", f"status intent outbox identity is invalid: {path}")
    pending: list[str] = []
    consumed: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RefreshError("STATUS_INTENT_OUTBOX_INVALID", "status intent outbox contains a non-object row")
        intent_id = str(row.get("intent_id") or "")
        intent = row.get("intent")
        state = row.get("state")
        if (
            not intent_id
            or intent_id in seen
            or state not in {"pending", "consumed"}
            or not isinstance(intent, dict)
            or intent.get("intent_id") != intent_id
            or row.get("payload_hash") != content_id(intent)
        ):
            raise RefreshError("STATUS_INTENT_OUTBOX_INVALID", f"status intent outbox row is invalid: {intent_id}")
        seen.add(intent_id)
        (pending if state == "pending" else consumed).append(intent_id)
    if outbox["pending"] != sorted(pending) or outbox["consumed"] != sorted(consumed):
        raise RefreshError("STATUS_INTENT_OUTBOX_INVALID", "status intent outbox indexes do not match row states")
    return sorted(pending)


def last_successful_receipt(memory_root: Path) -> dict[str, Any]:
    status = load_optional_json(memory_root / STATUS_REL)
    raw_path = status.get("last_successful_receipt")
    if not isinstance(raw_path, str) or not raw_path:
        return {}
    path = memory_root / raw_path
    return load_optional_json(path)


def current_policy_path(memory_root: Path, explicit: str | None, project_root: Path) -> Path | None:
    if explicit:
        return resolve_external_path(project_root, explicit)
    status = load_optional_json(memory_root / STATUS_REL)
    value = status.get("selection_policy")
    return resolve_external_path(project_root, value) if isinstance(value, str) and value else None


def published_policy_candidate(
    memory_root: Path,
    project_root: Path,
) -> tuple[str | None, str | None]:
    receipt = last_successful_receipt(memory_root)
    if receipt.get("status") != "published":
        return None, None
    raw_path = receipt.get("selection_policy")
    claimed_id = receipt.get("selection_policy_id")
    if raw_path is None and claimed_id is None:
        return None, None
    if not isinstance(raw_path, str) or not raw_path or not isinstance(claimed_id, str) or not claimed_id:
        raise RefreshError(
            "REFRESH_RECEIPT_INVALID",
            "published receipt selection policy binding is incomplete",
        )
    path = resolve_external_path(project_root, raw_path)
    policy = load_json(path)
    policy_id = content_id(policy)
    if policy_id != claimed_id:
        raise RefreshError(
            "REFRESH_RECEIPT_INVALID",
            "published receipt selection policy content identity is stale",
        )
    try:
        validate_policy(memory_root, path)
    except RefreshError as exc:
        if exc.code == "SELECTION_POLICY_INVALID":
            return None, None
        raise
    return str(path), policy_id


def load_management_panel_module() -> Any:
    path = SCRIPT_PATHS["management-panel"]
    spec = importlib.util.spec_from_file_location("adp_management_panel_for_refresh", path)
    if spec is None or spec.loader is None:
        raise RefreshError("SELECTION_POLICY_VALIDATOR_UNAVAILABLE", f"cannot load selection-policy validator: {path}")
    module = importlib.util.module_from_spec(spec)
    script_dir = str(path.parent)
    inserted = script_dir not in sys.path
    if inserted:
        sys.path.insert(0, script_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            sys.path.remove(script_dir)
    return module


def policy_sources(memory_root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    graph = load_json(memory_root / "views/flow-graph.json")
    status = load_json(memory_root / "views/program-status.json")
    current_snapshot_id = status.get("snapshot_id")
    history: list[dict[str, Any]] = []
    history_root = memory_root / "snapshots/program-status"
    for path in sorted(history_root.glob("*.json")) if history_root.is_dir() else []:
        item = load_json(path)
        snapshot_id = item.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id or snapshot_id == current_snapshot_id:
            continue
        history.append(
            {
                "snapshot_id": snapshot_id,
                "as_of": item.get("as_of"),
                "generated_at": item.get("generated_at"),
                "path": path.relative_to(memory_root).as_posix(),
            }
        )
    history.sort(key=lambda item: (str(item.get("as_of") or ""), item["snapshot_id"]))
    return graph, status, history


def policy_candidates(memory_root: Path) -> dict[str, Any]:
    graph, status, history = policy_sources(memory_root)
    nodes = sorted(
        str(item["node_id"])
        for item in graph.get("topology", {}).get("nodes", [])
        if isinstance(item, dict) and item.get("node_id")
    )
    edges = sorted(
        str(item["edge_id"])
        for item in graph.get("topology", {}).get("edges", [])
        if isinstance(item, dict) and item.get("edge_id")
    )
    scopes = sorted(
        str(item["scope_id"])
        for item in graph.get("overlays", {}).get("scopes", [])
        if isinstance(item, dict) and item.get("scope_id")
    )
    if not scopes:
        raise RefreshError("SELECTION_POLICY_CONTEXT_INVALID", "canonical flow graph has no selectable scope IDs")
    candidate_policy = {
        "policy_version": "1.0.0",
        "flow_graph_id": graph.get("flow_graph_id"),
        "history_snapshot_ids": [item["snapshot_id"] for item in history],
        "project_lead": {"scope_id": scopes[0], "node_ids": nodes, "edge_ids": edges},
        "shareable": {"visible_node_ids": nodes, "visible_edge_ids": edges},
    }
    return {
        "schema_version": "1.0.0",
        "operation": "policy",
        "flow_graph_id": graph.get("flow_graph_id"),
        "current_program_status_snapshot_id": status.get("snapshot_id"),
        "available_history_snapshots": history,
        "available_scope_ids": scopes,
        "available_node_ids": nodes,
        "available_edge_ids": edges,
        "candidate_policy": candidate_policy,
        "review_required": True,
    }


def validate_policy(
    memory_root: Path,
    path: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    policy = load_json(path)
    graph, status, history = policy_sources(memory_root)
    available_history_ids = {item["snapshot_id"] for item in history}
    try:
        validated = load_management_panel_module().validate_selection_policy(
            graph, policy, available_history_ids
        )
    except Exception as exc:
        raise RefreshError("SELECTION_POLICY_INVALID", str(exc)) from exc
    return policy, content_id(policy), {
        "validated_selection": validated,
        "flow_graph_id": graph.get("flow_graph_id"),
        "program_status_snapshot_id": status.get("snapshot_id"),
    }


def prepare_policy(args: argparse.Namespace, project_root: Path, memory_root: Path) -> dict[str, Any]:
    resume = interrupted_plan(memory_root)
    if resume.get("resume_status") == "awaiting-policy":
        plan_path = Path(str(resume["resume_plan_path"]))
        plan = load_json(plan_path)
        if args.selection_policy:
            bound = bind_policy_to_plan(
                args,
                project_root,
                memory_root,
                plan,
                plan_path,
                workspace_for(memory_root, plan["refresh_id"]) / "memory",
                next_status="planned",
            )
            return {**bound, "operation": "policy", "ok": True}
        return {**awaiting_policy_result(plan, plan_path), "operation": "policy", "ok": True}

    try:
        candidates = policy_candidates(memory_root)
    except RefreshError as exc:
        if exc.code != "JSON_MISSING" or args.selection_policy:
            raise
        planned = plan_refresh(args, project_root, memory_root)
        apply_args = argparse.Namespace(**{**vars(args), "operation": "apply", "plan": planned["plan_path"]})
        result = apply_refresh(apply_args, project_root, memory_root)
        return {**result, "operation": "policy"}
    candidates_path = memory_root / POLICY_CANDIDATES_REL
    atomic_json(candidates_path, candidates)
    result: dict[str, Any] = {
        **candidates,
        "ok": True,
        "candidate_path": str(candidates_path),
        "selection_policy": None,
        "selection_policy_id": None,
        "policy_validated": False,
    }
    if args.selection_policy:
        path = resolve_external_path(project_root, args.selection_policy)
        _, policy_id, context = validate_policy(memory_root, path)
        result.update(
            {
                "selection_policy": str(path),
                "selection_policy_id": policy_id,
                "policy_validated": True,
                **context,
            }
        )
    return result


def interrupted_plan(memory_root: Path) -> dict[str, Any]:
    status = load_optional_json(memory_root / STATUS_REL)
    run_id = status.get("current_run_id")
    active_statuses = {"planned", "refreshing", "dirty", "awaiting-policy"}
    path = memory_root / RUNS_REL / f"{run_id}.json" if isinstance(run_id, str) and run_id else None
    plan = load_optional_json(path) if path else {}
    if plan.get("status") not in active_statuses:
        candidates = []
        runs_root = memory_root / RUNS_REL
        for candidate_path in sorted(runs_root.glob("*.json")) if runs_root.is_dir() else []:
            candidate = load_optional_json(candidate_path)
            if candidate.get("status") in active_statuses:
                candidates.append((candidate_path, candidate))
        if not candidates:
            return {}
        if len(candidates) > 1:
            ids = ", ".join(str(item.get("refresh_id") or candidate_path.stem) for candidate_path, item in candidates)
            raise RefreshError("REFRESH_RESUME_AMBIGUOUS", f"multiple nonterminal refresh plans require explicit --plan: {ids}")
        path, plan = candidates[0]
        run_id = str(plan.get("refresh_id") or path.stem)
    assert path is not None
    return {
        "resume_plan_path": str(path),
        "resume_refresh_id": run_id,
        "retry_from_instance_key": plan.get("retry_from_instance_key"),
        "resume_status": plan.get("status"),
    }


def awaiting_policy_result(plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    waiting = plan.get("awaiting_policy") if isinstance(plan.get("awaiting_policy"), dict) else {}
    raw_candidates = waiting.get("candidate_path")
    if not isinstance(raw_candidates, str) or not raw_candidates:
        raise RefreshError("SELECTION_POLICY_CANDIDATE_MISSING", "awaiting-policy plan has no candidate artifact")
    candidates_path = Path(raw_candidates).resolve()
    candidates = load_json(candidates_path)
    return {
        **candidates,
        "status": "awaiting-policy",
        "policy_validated": False,
        "candidate_path": str(candidates_path),
        "candidate_policy_path": waiting.get("candidate_policy_path"),
        "resume_plan_path": str(plan_path),
        "resume_refresh_id": plan.get("refresh_id"),
        "retry_from_instance_key": plan.get("retry_from_instance_key"),
        "next_command_args": [
            "policy",
            "<project-root>",
            "--memory-root",
            "<memory-root>",
            "--selection-policy",
            str(waiting.get("candidate_policy_path") or "<reviewed-policy.json>"),
        ],
    }


def pause_for_policy(
    memory_root: Path,
    staged_root: Path,
    workspace: Path,
    plan: dict[str, Any],
    plan_path: Path,
) -> dict[str, Any]:
    candidates = policy_candidates(staged_root)
    candidates_path = workspace / "selection-policy-candidates.json"
    candidate_policy_path = workspace / "selection-policy.json"
    atomic_json(candidates_path, candidates)
    atomic_json(candidate_policy_path, candidates["candidate_policy"])
    plan["selection_policy"] = None
    plan["selection_policy_id"] = None
    plan["selection_policy_source"] = None
    plan["status"] = "awaiting-policy"
    plan["retry_from_instance_key"] = "meeting-pack:fde-morning"
    plan["awaiting_policy"] = {
        "candidate_path": str(candidates_path),
        "candidate_policy_path": str(candidate_policy_path),
    }
    atomic_json(plan_path, plan)
    status = load_optional_json(memory_root / STATUS_REL)
    status.update(
        {
            "schema_version": "1.0.0",
            "current_run_id": plan["refresh_id"],
            "current_status": "awaiting-policy",
            "retry_from_instance_key": plan["retry_from_instance_key"],
            "selection_policy": None,
            "selection_policy_id": None,
            "selection_policy_source": None,
            "pending_invalidations": plan.get("nodes", []),
            "metrics": status.get("metrics", default_metrics()),
        }
    )
    status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
    atomic_json(memory_root / STATUS_REL, status)
    return awaiting_policy_result(plan, plan_path)


def bind_policy_to_plan(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    plan: dict[str, Any],
    plan_path: Path,
    validation_root: Path,
    *,
    next_status: str,
) -> dict[str, Any]:
    if not args.selection_policy:
        raise RefreshError("SELECTION_POLICY_MISSING", "resume requires --selection-policy")
    source_path = resolve_external_path(project_root, args.selection_policy)
    policy, policy_id, context = validate_policy(validation_root, source_path)
    durable_path = memory_root / POLICIES_REL / (policy_id.removeprefix("sha256:") + ".json")
    existing = load_optional_json(durable_path)
    if existing and existing != policy:
        raise RefreshError("SELECTION_POLICY_ID_CONFLICT", f"policy identity collision: {durable_path}")
    if not existing:
        atomic_json(durable_path, policy)
    plan["selection_policy"] = str(durable_path)
    plan["selection_policy_id"] = policy_id
    plan["selection_policy_source"] = "staged"
    plan["status"] = next_status
    plan["retry_from_instance_key"] = "meeting-pack:fde-morning"
    plan.pop("awaiting_policy", None)
    atomic_json(plan_path, plan)
    status = load_optional_json(memory_root / STATUS_REL)
    status.update(
        {
            "schema_version": "1.0.0",
            "current_run_id": plan["refresh_id"],
            "current_status": next_status,
            "retry_from_instance_key": plan["retry_from_instance_key"],
            "selection_policy": str(durable_path),
            "selection_policy_id": policy_id,
            "selection_policy_source": "staged",
            "pending_invalidations": plan.get("nodes", []),
            "metrics": status.get("metrics", default_metrics()),
        }
    )
    status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
    atomic_json(memory_root / STATUS_REL, status)
    return {
        "status": next_status,
        "policy_validated": True,
        "selection_policy": str(durable_path),
        "selection_policy_id": policy_id,
        "resume_plan_path": str(plan_path),
        "resume_refresh_id": plan.get("refresh_id"),
        "retry_from_instance_key": plan.get("retry_from_instance_key"),
        "next_command_args": ["apply", "<project-root>", "--memory-root", "<memory-root>", "--plan", str(plan_path)],
        **context,
    }


def invalidated_nodes(
    changed_sources: list[str],
    fixture: bool,
    *,
    policy_changed: bool = False,
    policy_checkpoint_required: bool = False,
) -> list[str]:
    if fixture:
        return ["management-panel"] if changed_sources or policy_changed else []
    if policy_checkpoint_required:
        return list(NODE_ORDER)
    if policy_changed and not changed_sources:
        return ["management-panel"]
    if not changed_sources:
        return []
    nodes = {"state-audit", "program-status", "roadmap", "flow-graph", "management-panel"}
    nodes.update({"meeting-pack:fde-morning", "meeting-pack:business-biweekly"})
    return [node for node in NODE_ORDER if node in nodes]


def audit_drift_details(audit: dict[str, Any]) -> tuple[int, list[str], list[dict[str, Any]]]:
    drift_count = int(audit.get("counts", {}).get("action_projection_drift", 0) or 0)
    contract = audit.get("repair_contract") if isinstance(audit.get("repair_contract"), dict) else {}
    findings = contract.get("findings") if isinstance(contract.get("findings"), list) else []
    drift_findings = [
        row
        for row in findings
        if isinstance(row, dict) and row.get("kind") == "action-projection-drift"
    ]
    action_ids = sorted(
        {
            str(action_id)
            for row in drift_findings
            for action_id in row.get("action_ids", [])
            if str(action_id)
        }
    )
    batch_ids = {
        str(row.get("repair_batch_id"))
        for row in drift_findings
        if row.get("repair_batch_id")
    }
    raw_batches = contract.get("repair_batches") if isinstance(contract.get("repair_batches"), list) else []
    repair_batches: list[dict[str, Any]] = []
    for batch in raw_batches:
        if not isinstance(batch, dict) or str(batch.get("batch_id")) not in batch_ids:
            continue
        command = batch.get("command") if isinstance(batch.get("command"), dict) else {}
        repair_batches.append(
            {
                "repair_batch_id": batch.get("batch_id"),
                "action_ids": sorted(str(item) for item in command.get("action_ids", []) if str(item)),
                "workstream_id": command.get("workstream_id"),
                "workflow": command.get("workflow"),
            }
        )
    return drift_count, action_ids, sorted(repair_batches, key=lambda row: str(row["repair_batch_id"]))


def drift_audit_matches_live(memory_root: Path, audit: dict[str, Any]) -> bool:
    verdict = audit.get("action_projection_drift")
    if not isinstance(verdict, dict):
        # Older audit fixtures did not persist the read-set fingerprints.
        return True
    if verdict.get("ledger_fingerprint") != optional_file_fingerprint(
        memory_root / "actions/action-ledger.md"
    ):
        return False
    rows = verdict.get("rows")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("workstream_id"), str):
            return False
        workstreams_root = (memory_root / "workstreams").resolve(strict=False)
        workstream_root = (workstreams_root / row["workstream_id"]).resolve(strict=False)
        try:
            workstream_root.relative_to(workstreams_root)
        except ValueError:
            return False
        observed = {
            "wdr_fingerprint": optional_file_fingerprint(workstream_root / "delivery-record.md"),
            "wdr_state_fingerprint": optional_file_fingerprint(
                workstream_root / "delivery-record.state.json"
            ),
            "sidecar_fingerprint": optional_file_fingerprint(
                workstream_root / "action-projection.json"
            ),
        }
        if any(row.get(key) != fingerprint for key, fingerprint in observed.items()):
            return False
    return True


def detect(
    project_root: Path,
    memory_root: Path,
    *,
    fixture: bool = False,
    selection_policy: str | None = None,
) -> dict[str, Any]:
    live = source_inventory(memory_root, project_root)
    previous = last_successful_receipt(memory_root)
    prior = previous.get("source_fingerprints") if isinstance(previous.get("source_fingerprints"), dict) else {}
    changed = sorted(
        path
        for path in set(live) | set(prior)
        if live.get(path) != prior.get(path)
    )
    pending = pending_intent_ids(memory_root)
    audit_path = latest_audit_path(memory_root)
    audit = load_optional_json(audit_path) if audit_path else {}
    drift_audit_stale = bool(audit_path and not drift_audit_matches_live(memory_root, audit))
    if drift_audit_stale:
        drift_count, drift_action_ids, repair_batches = 0, [], []
    else:
        drift_count, drift_action_ids, repair_batches = audit_drift_details(audit)
    if selection_policy:
        policy_path = resolve_external_path(project_root, selection_policy)
        policy_id = content_id(load_json(policy_path)) if policy_path.is_file() else None
    elif fixture:
        policy_path = current_policy_path(memory_root, None, project_root)
        policy_id = content_id(load_json(policy_path)) if policy_path and policy_path.is_file() else None
    else:
        published_path, policy_id = published_policy_candidate(memory_root, project_root)
        policy_path = Path(published_path) if published_path else None
    prior_policy_id = previous.get("selection_policy_id")
    policy_changed = bool(previous and prior_policy_id != policy_id)
    policy_checkpoint_required = bool(
        not fixture
        and selection_policy is None
        and isinstance(prior_policy_id, str)
        and prior_policy_id
        and policy_id is None
    )
    nodes = invalidated_nodes(
        changed,
        fixture,
        policy_changed=policy_changed,
        policy_checkpoint_required=policy_checkpoint_required,
    )
    resume = interrupted_plan(memory_root)
    blocked_reasons: list[str] = []
    if pending:
        blocked_reasons.append("pending status intents must converge through adp-status-sync")
    if drift_count:
        blocked_reasons.append("action projection drift must be repaired through adp-status-sync")
    return {
        "ok": True,
        "operation": "detect",
        "memory_root": str(memory_root),
        "source_fingerprints": live,
        "previous_generation_id": previous.get("generation_id"),
        "changed_sources": changed,
        "selection_policy": str(policy_path) if policy_path else None,
        "selection_policy_id": policy_id,
        "selection_policy_changed": policy_changed,
        "selection_policy_checkpoint_required": policy_checkpoint_required,
        "pending_intent_ids": pending,
        "drift_count": drift_count,
        "drift_action_ids": drift_action_ids,
        "repair_batches": repair_batches,
        "drift_audit_path": str(audit_path) if audit_path else None,
        "drift_audit_stale": drift_audit_stale,
        "invalidated_nodes": nodes,
        "recommended_mode": "blocked" if blocked_reasons else (
            "full"
            if changed or policy_checkpoint_required
            else ("panel-only" if policy_changed else "reuse")
        ),
        "blocked_reasons": blocked_reasons,
        "recommended_workflows": ["adp-status-sync"] if blocked_reasons else [],
        **resume,
    }


def supersede_stale_nonterminal_plan(
    detection: dict[str, Any],
    plan: dict[str, Any],
    plan_path: Path,
) -> dict[str, str]:
    raw_path = detection.get("resume_plan_path")
    if (
        detection.get("resume_status") not in {"planned", "refreshing", "dirty", "awaiting-policy"}
        or detection.get("blocked_reasons")
        or not isinstance(raw_path, str)
    ):
        return {}
    previous_path = Path(raw_path).resolve()
    try:
        previous_path.relative_to(plan_path.parent.resolve())
    except ValueError as exc:
        raise RefreshError(
            "REFRESH_PLAN_INVALID", "dirty refresh plan is outside the durable runs directory"
        ) from exc
    previous = load_optional_json(previous_path)
    if (
        previous.get("status") not in {"planned", "refreshing", "dirty", "awaiting-policy"}
        or previous.get("refresh_id") == plan.get("refresh_id")
        or previous.get("source_fingerprints") == detection.get("source_fingerprints")
    ):
        return {}
    previous.update(
        {
            "status": "superseded",
            "retry_from_instance_key": None,
            "superseded_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "superseded_by_refresh_id": plan["refresh_id"],
            "superseded_by_plan_id": plan["plan_id"],
            "superseded_by_plan_path": str(plan_path),
            "supersede_reason": "bound sources changed after the nonterminal refresh plan",
        }
    )
    atomic_json(previous_path, previous)
    return {
        "superseded_refresh_id": str(previous.get("refresh_id") or previous_path.stem),
        "superseded_plan_path": str(previous_path),
    }


def plan_refresh(args: argparse.Namespace, project_root: Path, memory_root: Path) -> dict[str, Any]:
    detection = detect(
        project_root,
        memory_root,
        fixture=args.fixture,
        selection_policy=args.selection_policy,
    )
    as_of = parse_day(args.as_of, "as-of", datetime.now(timezone.utc).date())
    period_end = parse_day(args.period_end, "period-end", as_of)
    period_start = parse_day(args.period_start, "period-start", period_end - timedelta(days=6))
    if period_start > period_end or period_end > as_of:
        raise RefreshError("PERIOD_INVALID", "period must satisfy period-start <= period-end <= as-of")
    selection_policy = None
    selection_policy_id = None
    selection_policy_source = None
    if args.selection_policy:
        policy_path = resolve_external_path(project_root, args.selection_policy)
        selection_policy = str(policy_path)
        _, selection_policy_id, _ = validate_policy(memory_root, policy_path)
        selection_policy_source = "explicit"
    elif not args.fixture:
        selection_policy = detection.get("selection_policy")
        selection_policy_id = detection.get("selection_policy_id")
        selection_policy_source = "published" if selection_policy and selection_policy_id else None
    blocked = list(detection["blocked_reasons"])
    previous_receipt = last_successful_receipt(memory_root)
    if args.fixture:
        planned_nodes = ["management-panel"] if (
            detection["changed_sources"] or detection["selection_policy_changed"] or args.force_full or not previous_receipt
        ) else []
    elif (
        detection["changed_sources"]
        or detection["selection_policy_checkpoint_required"]
        or args.force_full
        or not previous_receipt
    ):
        planned_nodes = list(NODE_ORDER)
    elif detection["selection_policy_changed"]:
        planned_nodes = ["management-panel"]
    else:
        planned_nodes = []
    plan_body = {
        "schema_version": "1.0.0",
        "source_as_of": as_of.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "fde_period_start": args.fde_period_start,
        "fde_period_end": args.fde_period_end,
        "selection_policy": selection_policy,
        "selection_policy_id": selection_policy_id,
        "selection_policy_source": selection_policy_source,
        "fixture": bool(args.fixture),
        "source_fingerprints": detection["source_fingerprints"],
        "changed_sources": detection["changed_sources"],
        "pending_intent_ids": detection["pending_intent_ids"],
        "mode": "full" if (
            args.force_full
            or detection["changed_sources"]
            or detection["selection_policy_checkpoint_required"]
        ) else ("panel-only" if detection["selection_policy_changed"] else "reuse"),
        "blocked_reasons": blocked,
        "nodes": [
            {"instance_key": node, "status": "pending", "output": None, "error": None}
            for node in planned_nodes
        ],
    }
    refresh_id = "refresh-" + hashlib.sha256(canonical_bytes(plan_body)).hexdigest()[:24]
    plan_identity = {
        **plan_body,
        "refresh_id": refresh_id,
        "status": "blocked" if blocked else "planned",
        "retry_from_instance_key": plan_body["nodes"][0]["instance_key"] if plan_body["nodes"] else None,
    }
    plan_id = content_id(plan_identity)
    path = memory_root / RUNS_REL / f"{refresh_id}.json"
    existing = load_optional_json(path)
    if existing:
        if existing.get("plan_id") != plan_id:
            raise RefreshError("REFRESH_PLAN_CONFLICT", f"refresh plan identity collision: {path}")
        plan = existing
    else:
        plan = {
            **plan_identity,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "plan_id": plan_id,
        }
        atomic_json(path, plan)
    superseded = supersede_stale_nonterminal_plan(detection, plan, path)
    status = load_optional_json(memory_root / STATUS_REL)
    status.update(
        {
            "schema_version": "1.0.0",
            "current_run_id": refresh_id,
            "current_status": plan["status"],
            "pending_invalidations": plan["nodes"],
            "selection_policy": selection_policy,
            "selection_policy_id": selection_policy_id,
            "selection_policy_source": selection_policy_source,
            "retry_from_instance_key": plan["retry_from_instance_key"],
            "metrics": status.get("metrics", default_metrics()),
        }
    )
    if superseded:
        status["last_superseded_run_id"] = superseded["superseded_refresh_id"]
    status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
    atomic_json(memory_root / STATUS_REL, status)
    return {
        **plan,
        **superseded,
        "ok": not blocked,
        "operation": "plan",
        "plan_path": str(path),
    }


def default_metrics() -> dict[str, int]:
    return {"refresh_success": 0, "refresh_failure": 0, "refresh_reuse": 0, "inspect": 0}


@contextmanager
def refresh_lock(memory_root: Path) -> Iterator[None]:
    lock_path = memory_root / "state/panel-refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        acquire_file_lock(handle, shared=False, nonblocking=True)
        try:
            yield
        finally:
            release_file_lock(handle)


@contextmanager
def fact_read_lock(memory_root: Path) -> Iterator[None]:
    lock_path = memory_root / FACT_LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        acquire_file_lock(handle, shared=True, nonblocking=False)
        try:
            yield
        finally:
            release_file_lock(handle)


def acquire_file_lock(handle: BinaryIO, *, shared: bool, nonblocking: bool) -> None:
    if sys.platform != "win32":
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if nonblocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), operation)
        except BlockingIOError as exc:
            raise RefreshError("REFRESH_ALREADY_RUNNING", "another panel refresh holds the publication lock") from exc
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if (
                exc.errno not in WINDOWS_LOCK_CONTENTION_ERRORS
                and getattr(exc, "winerror", None) not in WINDOWS_LOCK_CONTENTION_WINERRORS
            ):
                raise
            if nonblocking:
                raise RefreshError("REFRESH_ALREADY_RUNNING", "another panel refresh holds the publication lock") from exc
            time.sleep(WINDOWS_LOCK_RETRY_SECONDS)


def release_file_lock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def workspace_for(memory_root: Path, refresh_id: str) -> Path:
    if REFRESH_ID_PATTERN.fullmatch(refresh_id) is None:
        raise RefreshError("REFRESH_ID_INVALID", "refresh_id must match refresh-[0-9a-f]{24}")
    staging_root = memory_root.parent / ".adp-panel-refresh-staging"
    if staging_root.is_symlink():
        raise RefreshError("REFRESH_STAGING_INVALID", "panel refresh staging root must not be a symlink")
    resolved_root = staging_root.resolve(strict=False)
    workspace = resolved_root / refresh_id
    try:
        workspace.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise RefreshError("REFRESH_STAGING_INVALID", "refresh workspace escapes the staging root") from exc
    return workspace


def prepare_staging(memory_root: Path, workspace: Path, plan: dict[str, Any]) -> Path:
    expected_workspace = workspace_for(memory_root, str(plan.get("refresh_id") or ""))
    if workspace.resolve(strict=False) != expected_workspace.resolve(strict=False):
        raise RefreshError("REFRESH_STAGING_INVALID", "refresh workspace is outside its durable staging slot")
    staged = workspace / "memory"
    metadata = workspace / "plan-id"
    if staged.is_dir():
        if not metadata.is_file() or metadata.read_text(encoding="utf-8").strip() != plan["plan_id"]:
            raise RefreshError("REFRESH_STAGING_CONFLICT", f"staging belongs to another plan: {workspace}")
        return staged
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(memory_root, staged, ignore=ignore_runtime_lock_files)
    metadata.write_text(plan["plan_id"] + "\n", encoding="utf-8", newline="\n")
    return staged


def run_json_command(
    command: list[str],
    output_path: Path,
    node: str,
    verbose: bool,
    *,
    output_flag: str = "-o",
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command + [output_flag, str(output_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if verbose and completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    result = load_optional_json(output_path)
    if completed.returncode != 0 or not result.get("ok", False):
        reason = result.get("reason") or result.get("error") or completed.stderr.strip() or "producer failed"
        raise RefreshError("REFRESH_NODE_BLOCKED", f"{node}: {reason}", node=node)
    if str(result.get("status", "")).lower() in {"blocked", "error", "conflict", "needs_confirmation"}:
        raise RefreshError("REFRESH_NODE_BLOCKED", f"{node}: {result.get('reason') or result.get('error') or result.get('status')}", node=node)
    return result


def artifact_audit(
    project_root: Path,
    staged_root: Path,
    input_audit: str,
    artifacts: list[str],
    result_path: Path,
    verbose: bool,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SCRIPT_PATHS["state-audit"]),
        str(project_root),
        "--phase",
        "artifact",
        "--memory-root",
        str(staged_root),
        "--input-audit-json",
        input_audit,
        "--output-dir",
        str(staged_root / "audits"),
    ]
    for artifact in artifacts:
        command.extend(["--artifact", artifact])
    return run_json_command(command, result_path, "artifact-audit", verbose)


def node_command(
    node: str,
    args: argparse.Namespace,
    plan: dict[str, Any],
    project_root: Path,
    staged_root: Path,
    workspace: Path,
    results: dict[str, dict[str, Any]],
) -> list[str]:
    common_root = [str(project_root), "--memory-root", str(staged_root)]
    if node == "state-audit":
        return [
            sys.executable,
            str(SCRIPT_PATHS[node]),
            *common_root,
            "--scenario",
            "global",
            "--as-of",
            plan["source_as_of"],
            "--output-dir",
            str(staged_root / "audits"),
            "--headless",
        ]
    if node == "program-status":
        audit_path = str(results["state-audit"].get("outputs", {}).get("json") or "")
        if not audit_path:
            raise RefreshError("REFRESH_RESULT_INVALID", "state-audit returned no JSON path", node=node)
        return [
            sys.executable,
            str(SCRIPT_PATHS[node]),
            *common_root,
            "--input-audit-json",
            audit_path,
            "--as-of",
            plan["source_as_of"],
            "--period-start",
            plan["period_start"],
            "--period-end",
            plan["period_end"],
            "--headless",
            "--memlog",
            str(workspace / "program-status.memlog.md"),
        ]
    if node == "roadmap":
        return [sys.executable, str(SCRIPT_PATHS[node]), *common_root, "--as-of", plan["source_as_of"]]
    if node == "flow-graph":
        return [sys.executable, str(SCRIPT_PATHS[node]), *common_root]
    if node.startswith("meeting-pack:"):
        scenario = node.split(":", 1)[1]
        command = [
            sys.executable,
            str(SCRIPT_PATHS["meeting-pack"]),
            *common_root,
            "--scenario",
            scenario,
            "--date",
            plan["source_as_of"],
            "--headless",
            "--replace",
        ]
        if scenario == "fde-morning" and plan.get("fde_period_start") and plan.get("fde_period_end"):
            command.extend(["--period-start", plan["fde_period_start"], "--period-end", plan["fde_period_end"]])
        return command
    if node == "management-panel":
        command = [sys.executable, str(SCRIPT_PATHS[node]), str(project_root), "refresh", "--memory-root", str(staged_root)]
        if plan.get("fixture"):
            command.append("--fixture")
        else:
            command.extend(["--selection-policy", str(plan["selection_policy"])])
        return command
    raise RefreshError("REFRESH_NODE_UNKNOWN", f"unknown refresh node: {node}", node=node)


def producer_outputs(node: str, result: dict[str, Any], staged_root: Path) -> tuple[list[str], str | None]:
    if node == "roadmap":
        outputs = result.get("outputs", {})
        return [str(outputs.get("json")), str(outputs.get("markdown"))], str(result.get("audit_path") or "")
    if node == "flow-graph":
        candidates = [
            result.get("outputs", {}).get("current"),
            result.get("outputs", {}).get("json"),
            str(staged_root / "views/flow-graph.json"),
        ]
        return [next(str(item) for item in candidates if item and Path(str(item)).is_file())], None
    if node.startswith("meeting-pack:"):
        outputs = result.get("outputs", {})
        return [str(outputs.get("distillate")), str(outputs.get("markdown"))], str(outputs.get("audit") or "")
    return [], None


def execute_node(
    node: str,
    args: argparse.Namespace,
    plan: dict[str, Any],
    project_root: Path,
    staged_root: Path,
    workspace: Path,
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result_path = workspace / "results" / (node.replace(":", "-") + ".json")
    command = node_command(node, args, plan, project_root, staged_root, workspace, results)
    result = run_json_command(
        command,
        result_path,
        node,
        args.verbose,
        output_flag="--output" if node == "management-panel" else "-o",
    )
    artifacts, input_audit = producer_outputs(node, result, staged_root)
    artifacts = [item for item in artifacts if item and item != "None" and Path(item).is_file()]
    if artifacts and node in {"roadmap", "flow-graph"} | {item for item in NODE_ORDER if item.startswith("meeting-pack:")}:
        input_audit = input_audit or str(results["state-audit"].get("outputs", {}).get("json") or "")
        artifact_audit(
            project_root,
            staged_root,
            input_audit,
            artifacts,
            workspace / "results" / (node.replace(":", "-") + "-artifact-audit.json"),
            args.verbose,
        )
    return result


def relative_file_map(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if is_runtime_lock_path(relative):
            continue
        if path.is_file() and not path.is_symlink():
            result[relative.as_posix()] = file_fingerprint(path)
    return result


def publishable_changes(
    memory_root: Path,
    staged_root: Path,
    *,
    allow_fixture_sources: bool = False,
) -> list[str]:
    before = relative_file_map(memory_root)
    after = relative_file_map(staged_root)
    changed = sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
        and not path.startswith("state/panel-refresh/")
        and path not in {"state/panel-refresh-status.json", "state/panel-refresh.lock", "state/fact-write.lock"}
        and not path.startswith("receipts/panel-refresh/")
        and not path.startswith("state/transactions/")
    )
    illegal = [
        path
        for path in changed
        if not (
            path.startswith(DERIVED_PREFIXES)
            or path.startswith(PUBLISHABLE_STATE_PREFIXES)
            or (allow_fixture_sources and any(path == prefix or path.startswith(prefix) for prefix in SOURCE_PREFIXES))
        )
    ]
    if illegal:
        raise RefreshError(
            "REFRESH_FACT_WRITE_FORBIDDEN",
            "projection refresh attempted to modify fact/runtime-owned paths: " + ", ".join(illegal[:10]),
        )
    return changed


def source_binding_mismatches(audit_sources: Any, expected_sources: Any) -> list[str]:
    if not isinstance(audit_sources, dict) or not isinstance(expected_sources, dict):
        return ["<source-map-missing>"]
    return sorted(
        path
        for path in set(audit_sources) | set(expected_sources)
        if audit_sources.get(path) != expected_sources.get(path)
    )


def strict_audit_readiness(audit: dict[str, Any], binding_mismatches: list[str]) -> str:
    if (
        audit.get("ok") is False
        or audit.get("audit_status") == "blocked"
        or audit.get("execution_disposition") == "blocked"
        or audit.get("safe_to_generate") is False
        or int(audit.get("counts", {}).get("blocking_findings", 0) or 0) > 0
    ):
        return "blocked"
    if binding_mismatches:
        return "stale"
    if audit.get("audit_status") == "pass" and audit.get("execution_disposition") == "ready":
        return "ready"
    if audit.get("execution_disposition") == "degraded":
        return "degraded"
    return "unverified"


def validate_staged_publication(
    args: argparse.Namespace,
    project_root: Path,
    staged_root: Path,
    workspace: Path,
    plan: dict[str, Any],
    panel_result: dict[str, Any],
) -> dict[str, Any]:
    pending = pending_intent_ids(staged_root)
    if pending:
        raise RefreshError(
            "REFRESH_PUBLICATION_INELIGIBLE",
            "staged status intents are still pending: " + ", ".join(pending),
        )
    audit_path = latest_audit_path(staged_root)
    if audit_path is None:
        raise RefreshError("REFRESH_PUBLICATION_INELIGIBLE", "staged panel has no management-panel input audit")
    audit = load_json(audit_path)
    drift_count, drift_action_ids, repair_batches = audit_drift_details(audit)
    if drift_count or drift_action_ids:
        raise RefreshError(
            "REFRESH_PUBLICATION_INELIGIBLE",
            "staged action projection drift must be repaired before publication",
        )
    expected_sources = (
        source_inventory(staged_root, project_root)
        if plan.get("fixture")
        else plan.get("source_fingerprints")
    )
    binding_mismatches = source_binding_mismatches(audit.get("source_fingerprints"), expected_sources)
    readiness = strict_audit_readiness(audit, binding_mismatches)
    if readiness != "ready":
        raise RefreshError(
            "REFRESH_PUBLICATION_INELIGIBLE",
            f"staged audit is not strictly ready: {readiness}",
        )
    command = [
        sys.executable,
        str(SCRIPT_PATHS["management-panel"]),
        str(project_root),
        "inspect",
        "--memory-root",
        str(staged_root),
    ]
    expected_panel_id = panel_result.get("panel_id")
    if isinstance(expected_panel_id, str) and expected_panel_id:
        command.extend(["--expected-panel-id", expected_panel_id])
    inspected = run_json_command(
        command,
        workspace / "results/prepublish-management-panel-inspect.json",
        "management-panel-prepublish-inspect",
        args.verbose,
        output_flag="--output",
    )
    return {
        "audit_path": str(audit_path),
        "audit_readiness": readiness,
        "audit_binding_mismatches": binding_mismatches,
        "drift_count": drift_count,
        "drift_action_ids": drift_action_ids,
        "repair_batches": repair_batches,
        "pending_intent_ids": pending,
        "panel_inspect": inspected,
    }


def atomic_publish(
    memory_root: Path,
    staged_root: Path,
    changed: list[str],
    workspace: Path,
    plan_id: str,
) -> dict[str, Any]:
    del workspace
    base_transaction_id = "publish-" + hashlib.sha256(plan_id.encode("utf-8")).hexdigest()[:24]
    transaction_id = next_publication_transaction_id(memory_root, base_transaction_id)
    journal = memory_root / "state/transactions" / transaction_id
    backups = journal / "before"
    journal.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0.0",
        "kind": "panel-publication",
        "transaction_id": transaction_id,
        "plan_id": plan_id,
        "status": "prepared",
        "applied_count": 0,
        "targets": [],
    }
    for relative in changed:
        target = memory_root / relative
        source = staged_root / relative
        before = target.read_bytes() if target.is_file() else None
        after = source.read_bytes() if source.is_file() else None
        entry = {
            "path": relative,
            "before_sha256": "sha256:" + hashlib.sha256(before).hexdigest() if before is not None else None,
            "after_sha256": "sha256:" + hashlib.sha256(after).hexdigest() if after is not None else None,
        }
        manifest["targets"].append(entry)
        if before is not None:
            backup = backups / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(before)
    atomic_json(journal / "manifest.json", manifest)
    applied: list[str] = []
    try:
        for relative in changed:
            target = memory_root / relative
            source = staged_root / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor, raw_temp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
                temporary = Path(raw_temp)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(source.read_bytes())
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                target.unlink(missing_ok=True)
            applied.append(relative)
            manifest["applied_count"] = len(applied)
            atomic_json(journal / "manifest.json", manifest)
        manifest["status"] = "committed"
        atomic_json(journal / "manifest.json", manifest)
    except Exception:
        for relative in reversed(applied):
            target = memory_root / relative
            backup = backups / relative
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_bytes(target, backup.read_bytes())
            else:
                target.unlink(missing_ok=True)
        manifest["status"] = "rolled-back"
        atomic_json(journal / "manifest.json", manifest)
        raise
    return manifest


def next_publication_transaction_id(memory_root: Path, base: str) -> str:
    root = memory_root / "state/transactions"
    candidate = base
    attempt = 0
    while (root / candidate).exists():
        attempt += 1
        candidate = f"{base}-r{attempt}"
    return candidate


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validated_journal_target(root: Path, raw_path: Any) -> tuple[Path, Path]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RefreshError("REFRESH_JOURNAL_CORRUPT", "publication target path is invalid")
    relative = Path(raw_path)
    if relative.is_absolute() or relative == Path(".") or ".." in relative.parts:
        raise RefreshError(
            "REFRESH_JOURNAL_CORRUPT",
            f"publication target must be a safe relative path: {raw_path}",
        )
    root_resolved = root.resolve()
    target = root / relative
    try:
        target.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise RefreshError(
            "REFRESH_JOURNAL_CORRUPT",
            f"publication target escapes its recovery root: {raw_path}",
        ) from exc
    return relative, target


def recover_publication_transactions(memory_root: Path) -> list[str]:
    transactions = memory_root / "state/transactions"
    recovered: list[str] = []
    if not transactions.is_dir():
        return recovered
    for manifest_path in sorted(transactions.glob("publish-*/manifest.json")):
        manifest = load_optional_json(manifest_path)
        if manifest.get("kind") != "panel-publication" or manifest.get("status") != "prepared":
            continue
        journal = manifest_path.parent
        targets = manifest.get("targets")
        if not isinstance(targets, list):
            raise RefreshError("REFRESH_JOURNAL_CORRUPT", f"publication journal has invalid targets: {manifest_path}")
        for entry in targets:
            if not isinstance(entry, dict):
                raise RefreshError("REFRESH_JOURNAL_CORRUPT", "publication journal target must be an object")
            relative, target = validated_journal_target(memory_root, entry.get("path"))
            actual_hash = file_fingerprint(target) if target.is_file() else None
            if actual_hash not in {entry.get("before_sha256"), entry.get("after_sha256")}:
                raise RefreshError(
                    "REFRESH_JOURNAL_CORRUPT",
                    f"publication target contains bytes outside journal before/after images: {relative.as_posix()}",
                )
        for entry in reversed(targets):
            relative, target = validated_journal_target(memory_root, entry["path"])
            _, backup = validated_journal_target(journal / "before", relative.as_posix())
            if entry.get("before_sha256") is None:
                target.unlink(missing_ok=True)
            elif backup.is_file() and file_fingerprint(backup) == entry.get("before_sha256"):
                atomic_bytes(target, backup.read_bytes())
            else:
                raise RefreshError(
                    "REFRESH_JOURNAL_CORRUPT",
                    f"publication backup is missing or invalid: {relative.as_posix()}",
                )
        manifest["status"] = "rolled-back"
        manifest["recovered"] = True
        atomic_json(manifest_path, manifest)
        recovered.append(str(manifest.get("transaction_id")))
    return recovered


def committed_publication_for_plan(memory_root: Path, plan_id: str) -> dict[str, Any] | None:
    transactions = memory_root / "state/transactions"
    if not transactions.is_dir():
        return None
    matches: list[dict[str, Any]] = []
    for manifest_path in sorted(transactions.glob("publish-*/manifest.json")):
        manifest = load_optional_json(manifest_path)
        if (
            manifest.get("kind") != "panel-publication"
            or manifest.get("plan_id") != plan_id
            or manifest.get("status") != "committed"
        ):
            continue
        targets = manifest.get("targets")
        if not isinstance(targets, list) or not targets:
            continue
        matches_after = True
        for entry in targets:
            if not isinstance(entry, dict):
                matches_after = False
                break
            _, target = validated_journal_target(memory_root, entry.get("path"))
            actual = file_fingerprint(target) if target.is_file() else None
            if actual != entry.get("after_sha256"):
                matches_after = False
                break
        if matches_after:
            matches.append(manifest)
    return matches[-1] if matches else None


def load_or_create_plan(args: argparse.Namespace, project_root: Path, memory_root: Path) -> tuple[dict[str, Any], Path]:
    if args.plan:
        path = Path(args.plan).expanduser().resolve()
        plan = load_json(path)
        if path.parent.resolve() != (memory_root / RUNS_REL).resolve():
            raise RefreshError("REFRESH_PLAN_PATH_INVALID", "refresh plan must be under the memory-root run registry")
        return plan, path
    resume = interrupted_plan(memory_root)
    if resume.get("resume_plan_path"):
        path = Path(str(resume["resume_plan_path"]))
        return load_json(path), path
    planned = plan_refresh(args, project_root, memory_root)
    return planned, Path(planned["plan_path"])


def parse_required_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RefreshError("REFRESH_PLAN_INVALID", f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RefreshError("REFRESH_PLAN_INVALID", f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RefreshError("REFRESH_PLAN_INVALID", f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_plan_freshness(memory_root: Path, plan: dict[str, Any], plan_path: Path) -> None:
    refresh_id = str(plan.get("refresh_id") or "")
    workspace_for(memory_root, refresh_id)
    if plan_path.stem != refresh_id:
        raise RefreshError("REFRESH_PLAN_INVALID", "refresh plan filename does not match refresh_id")
    latest = last_successful_receipt(memory_root)
    if not latest or latest.get("plan_id") == plan.get("plan_id"):
        return
    if latest.get("status") != "published":
        return
    created_at = parse_required_utc_timestamp(plan.get("created_at"), "refresh plan created_at")
    published_at = parse_required_utc_timestamp(latest.get("published_at"), "latest publication published_at")
    if created_at < published_at:
        raise RefreshError(
            "REFRESH_PLAN_SUPERSEDED",
            "refresh plan predates the latest successful publication; run plan again",
        )


def flow_graph_node_state(plan: dict[str, Any]) -> dict[str, Any] | None:
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        raise RefreshError("REFRESH_PLAN_INVALID", "refresh plan nodes must be an array")
    matches = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("instance_key") == "flow-graph"
    ]
    if len(matches) > 1:
        raise RefreshError("REFRESH_PLAN_INVALID", "refresh plan contains duplicate flow-graph nodes")
    return matches[0] if matches else None


def selection_policy_validation_root(memory_root: Path, plan: dict[str, Any]) -> Path | None:
    flow_node = flow_graph_node_state(plan)
    if flow_node is None:
        return memory_root
    if flow_node.get("status") != "completed":
        return None

    workspace = workspace_for(memory_root, str(plan.get("refresh_id") or ""))
    plan_id = plan.get("plan_id")
    if not workspace.is_dir():
        publication = (
            committed_publication_for_plan(memory_root, plan_id)
            if isinstance(plan_id, str) and plan_id
            else None
        )
        if publication:
            return memory_root
        raise RefreshError(
            "REFRESH_STAGING_INVALID",
            "completed flow-graph node is missing its plan-bound staging workspace",
        )
    metadata = workspace / "plan-id"
    if (
        not isinstance(plan_id, str)
        or not plan_id
        or not metadata.is_file()
        or metadata.is_symlink()
    ):
        raise RefreshError(
            "REFRESH_STAGING_INVALID",
            "completed flow-graph node is missing its workspace plan binding",
        )
    try:
        workspace_plan_id = metadata.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RefreshError(
            "REFRESH_STAGING_INVALID",
            "cannot read the staged workspace plan binding",
        ) from exc
    if workspace_plan_id != plan_id:
        raise RefreshError(
            "REFRESH_STAGING_CONFLICT",
            "completed flow-graph staging belongs to another refresh plan",
        )

    staged_root = workspace / "memory"
    graph_path = staged_root / "views/flow-graph.json"
    result_path = workspace / "results/flow-graph.json"
    raw_node_output = flow_node.get("output")
    if (
        not staged_root.is_dir()
        or staged_root.is_symlink()
        or not graph_path.is_file()
        or graph_path.is_symlink()
        or not result_path.is_file()
        or result_path.is_symlink()
        or not isinstance(raw_node_output, str)
        or not raw_node_output
        or not Path(raw_node_output).is_absolute()
        or Path(raw_node_output).resolve() != result_path.resolve()
    ):
        raise RefreshError(
            "REFRESH_RESULT_INVALID",
            "completed flow-graph node is not bound to the current staged graph result",
            node="flow-graph",
        )

    result = load_json(result_path)
    outputs = result.get("outputs")
    raw_current = outputs.get("current") if isinstance(outputs, dict) else None
    graph = load_json(graph_path)
    staged_graph_id = graph.get("flow_graph_id")
    if (
        result.get("ok") is not True
        or not isinstance(raw_current, str)
        or not raw_current
        or not Path(raw_current).is_absolute()
        or Path(raw_current).resolve() != graph_path.resolve()
        or not isinstance(staged_graph_id, str)
        or not staged_graph_id
        or result.get("flow_graph_id") != staged_graph_id
    ):
        raise RefreshError(
            "REFRESH_RESULT_INVALID",
            "completed flow-graph result identity does not match the current staged graph",
            node="flow-graph",
        )
    return staged_root


def validate_plan_selection_policy(
    project_root: Path,
    memory_root: Path,
    plan: dict[str, Any],
) -> dict[str, Any] | None:
    raw_policy_path = plan.get("selection_policy")
    if not isinstance(raw_policy_path, str) or not raw_policy_path:
        return None
    policy_path = resolve_external_path(project_root, raw_policy_path)
    policy = load_json(policy_path)
    policy_id = content_id(policy)
    if policy_id != plan.get("selection_policy_id"):
        raise RefreshError(
            "SELECTION_POLICY_CHANGED_SINCE_PLAN",
            "selection policy changed after refresh planning; run plan again",
        )
    validation_root = selection_policy_validation_root(memory_root, plan)
    if validation_root is None:
        return None
    _, validated_policy_id, context = validate_policy(validation_root, policy_path)
    if validated_policy_id != policy_id:
        raise RefreshError(
            "SELECTION_POLICY_CHANGED_SINCE_PLAN",
            "selection policy changed while it was being revalidated",
        )
    return context


def pause_for_rejected_published_policy(
    memory_root: Path,
    plan: dict[str, Any],
    plan_path: Path,
    error: RefreshError,
) -> dict[str, Any]:
    workspace = workspace_for(memory_root, str(plan.get("refresh_id") or ""))
    plan["rejected_published_selection_policy"] = {
        "selection_policy": plan.get("selection_policy"),
        "selection_policy_id": plan.get("selection_policy_id"),
        "reason": str(error),
    }
    return pause_for_policy(
        memory_root,
        workspace / "memory",
        workspace,
        plan,
        plan_path,
    )


def apply_refresh(args: argparse.Namespace, project_root: Path, memory_root: Path) -> dict[str, Any]:
    plan, plan_path = load_or_create_plan(args, project_root, memory_root)
    validate_plan_freshness(memory_root, plan, plan_path)
    if plan.get("status") == "superseded":
        raise RefreshError(
            "REFRESH_PLAN_SUPERSEDED",
            "refresh plan was replaced after its fact sources changed; apply its replacement plan",
        )
    if plan.get("blocked_reasons"):
        raise RefreshError("REFRESH_PLAN_BLOCKED", "; ".join(plan["blocked_reasons"]))
    if plan.get("status") == "awaiting-policy":
        if not args.selection_policy:
            return {**awaiting_policy_result(plan, plan_path), "ok": True, "operation": "apply"}
        bind_policy_to_plan(
            args,
            project_root,
            memory_root,
            plan,
            plan_path,
            workspace_for(memory_root, plan["refresh_id"]) / "memory",
            next_status="planned",
        )
    live = source_inventory(memory_root, project_root)
    if live != plan.get("source_fingerprints"):
        raise RefreshError("SOURCE_CHANGED_SINCE_PLAN", "bound sources changed after refresh planning; run plan again")
    if not plan.get("fixture"):
        try:
            validate_plan_selection_policy(project_root, memory_root, plan)
        except RefreshError as exc:
            if (
                exc.code == "SELECTION_POLICY_INVALID"
                and plan.get("selection_policy_source") == "published"
            ):
                return {
                    **pause_for_rejected_published_policy(
                        memory_root,
                        plan,
                        plan_path,
                        exc,
                    ),
                    "ok": True,
                    "operation": "apply",
                }
            raise
    receipt_path = memory_root / RECEIPTS_REL / f"{plan['refresh_id']}.json"
    existing_receipt = load_optional_json(receipt_path)
    if existing_receipt.get("status") == "published":
        if existing_receipt.get("plan_id") != plan.get("plan_id"):
            raise RefreshError("REFRESH_RECEIPT_CONFLICT", "published receipt belongs to a different refresh plan")
        receipt_body = dict(existing_receipt)
        claimed_receipt_id = receipt_body.pop("receipt_id", None)
        if claimed_receipt_id != content_id(receipt_body):
            raise RefreshError("REFRESH_RECEIPT_INVALID", "published receipt content identity is invalid")
        finalize_refresh_state(memory_root, plan, plan_path, receipt_path, existing_receipt)
        inspected = inspect_refresh(args, project_root, memory_root)
        return {**existing_receipt, "operation": "apply", "reused": True, "inspect": inspected}
    with refresh_lock(memory_root):
        recover_publication_transactions(memory_root)
        workspace = workspace_for(memory_root, plan["refresh_id"])
        staged_root = prepare_staging(memory_root, workspace, plan)
        results: dict[str, dict[str, Any]] = {}
        for node_state in plan.get("nodes", []):
            node = node_state["instance_key"]
            result_path = workspace / "results" / (node.replace(":", "-") + ".json")
            if node_state.get("status") == "completed" and result_path.is_file():
                results[node] = load_json(result_path)
                if node == "flow-graph" and not plan.get("selection_policy"):
                    if args.selection_policy:
                        bind_policy_to_plan(
                            args,
                            project_root,
                            memory_root,
                            plan,
                            plan_path,
                            staged_root,
                            next_status="refreshing",
                        )
                    else:
                        return {**pause_for_policy(memory_root, staged_root, workspace, plan, plan_path), "ok": True, "operation": "apply"}
                continue
            node_state["status"] = "running"
            plan["status"] = "refreshing"
            plan["retry_from_instance_key"] = node
            atomic_json(plan_path, plan)
            try:
                results[node] = execute_node(node, args, plan, project_root, staged_root, workspace, results)
            except Exception as exc:
                node_state["status"] = "blocked"
                node_state["error"] = str(exc)
                plan["status"] = "dirty"
                atomic_json(plan_path, plan)
                update_failure_status(memory_root, plan, node, str(exc))
                raise
            node_state["status"] = "completed"
            node_state["output"] = str(result_path)
            node_state["error"] = None
            atomic_json(plan_path, plan)
            if node == "flow-graph" and plan.get("selection_policy") and not plan.get("fixture"):
                try:
                    validate_plan_selection_policy(project_root, memory_root, plan)
                except Exception as exc:
                    if (
                        isinstance(exc, RefreshError)
                        and exc.code == "SELECTION_POLICY_INVALID"
                        and plan.get("selection_policy_source") == "published"
                    ):
                        return {
                            **pause_for_rejected_published_policy(
                                memory_root,
                                plan,
                                plan_path,
                                exc,
                            ),
                            "ok": True,
                            "operation": "apply",
                        }
                    plan["status"] = "dirty"
                    plan["retry_from_instance_key"] = "meeting-pack:fde-morning"
                    atomic_json(plan_path, plan)
                    update_failure_status(
                        memory_root,
                        plan,
                        "meeting-pack:fde-morning",
                        str(exc),
                    )
                    raise
            if node == "flow-graph" and not plan.get("selection_policy"):
                if args.selection_policy:
                    bind_policy_to_plan(
                        args,
                        project_root,
                        memory_root,
                        plan,
                        plan_path,
                        staged_root,
                        next_status="refreshing",
                    )
                else:
                    return {**pause_for_policy(memory_root, staged_root, workspace, plan, plan_path), "ok": True, "operation": "apply"}
            if args.fail_after_node == node or os.environ.get("ADP_PANEL_REFRESH_FAIL_AFTER_NODE") == node:
                raise RefreshError("INJECTED_REFRESH_CRASH", f"injected crash after {node}", node=node)

        if not plan.get("nodes"):
            status = load_optional_json(memory_root / STATUS_REL)
            metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else default_metrics()
            metrics["refresh_reuse"] = int(metrics.get("refresh_reuse", 0)) + 1
            plan["status"] = "published"
            plan["retry_from_instance_key"] = None
            atomic_json(plan_path, plan)
            status.update(
                {
                    "schema_version": "1.0.0",
                    "current_run_id": plan["refresh_id"],
                    "current_status": "published",
                    "pending_invalidations": [],
                    "retry_from_instance_key": None,
                    "metrics": metrics,
                }
            )
            status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
            atomic_json(memory_root / STATUS_REL, status)
            return {"ok": True, "operation": "apply", "status": "reused", "refresh_id": plan["refresh_id"]}

        panel_result = results.get("management-panel", {})
        with fact_read_lock(memory_root):
            if source_inventory(memory_root, project_root) != plan["source_fingerprints"]:
                raise RefreshError("SOURCE_CHANGED_DURING_REFRESH", "bound sources changed while projections were rebuilding")
            prepublish_validation = validate_staged_publication(
                args,
                project_root,
                staged_root,
                workspace,
                plan,
                panel_result,
            )
            publication = committed_publication_for_plan(memory_root, plan["plan_id"])
            if publication:
                changed = [str(entry["path"]) for entry in publication.get("targets", [])]
            else:
                changed = publishable_changes(
                    memory_root,
                    staged_root,
                    allow_fixture_sources=bool(plan.get("fixture")),
                )
                publication = atomic_publish(memory_root, staged_root, changed, workspace, plan["plan_id"])
            committed_sources = (
                source_inventory(memory_root, project_root)
                if plan.get("fixture")
                else plan["source_fingerprints"]
            )
        generation_id = content_id(
            {
                "refresh_id": plan["refresh_id"],
                "source_fingerprints": committed_sources,
                "selection_policy_id": plan.get("selection_policy_id"),
                "panel_id": panel_result.get("panel_id"),
            }
        )
        receipt = {
            "ok": True,
            "schema_version": "1.0.0",
            "operation": "apply",
            "status": "published",
            "refresh_id": plan["refresh_id"],
            "plan_id": plan["plan_id"],
            "generation_id": generation_id,
            "source_as_of": plan["source_as_of"],
            "source_fingerprints": committed_sources,
            "selection_policy": plan.get("selection_policy"),
            "selection_policy_id": plan.get("selection_policy_id"),
            "panel_id": panel_result.get("panel_id"),
            "nodes": plan["nodes"],
            "publication_transaction": publication,
            "prepublish_validation": prepublish_validation,
            "published_paths": changed,
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        receipt["receipt_id"] = content_id(receipt)
        atomic_json(receipt_path, receipt)
        finalize_refresh_state(memory_root, plan, plan_path, receipt_path, receipt)
        inspected = inspect_refresh(args, project_root, memory_root, refresh_locked=True)
        receipt["inspect"] = inspected
        receipt.pop("receipt_id", None)
        receipt["receipt_id"] = content_id(receipt)
        atomic_json(receipt_path, receipt)
        shutil.rmtree(workspace, ignore_errors=True)
        return receipt


def finalize_refresh_state(
    memory_root: Path,
    plan: dict[str, Any],
    plan_path: Path,
    receipt_path: Path,
    receipt: dict[str, Any],
) -> None:
    plan["status"] = "published"
    plan["retry_from_instance_key"] = None
    atomic_json(plan_path, plan)
    status = load_optional_json(memory_root / STATUS_REL)
    metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else default_metrics()
    if status.get("last_successful_generation_id") != receipt.get("generation_id"):
        metrics["refresh_success"] = int(metrics.get("refresh_success", 0)) + 1
    status.update(
        {
            "schema_version": "1.0.0",
            "current_run_id": plan["refresh_id"],
            "current_status": "published",
            "last_successful_generation_id": receipt["generation_id"],
            "last_successful_refresh_at": receipt["published_at"],
            "last_successful_receipt": receipt_path.relative_to(memory_root).as_posix(),
            "pending_invalidations": [],
            "selection_policy": plan.get("selection_policy"),
            "selection_policy_id": plan.get("selection_policy_id"),
            "metrics": metrics,
        }
    )
    status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
    atomic_json(memory_root / STATUS_REL, status)


def update_failure_status(memory_root: Path, plan: dict[str, Any], node: str, error: str) -> None:
    status = load_optional_json(memory_root / STATUS_REL)
    metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else default_metrics()
    metrics["refresh_failure"] = int(metrics.get("refresh_failure", 0)) + 1
    status.update(
        {
            "schema_version": "1.0.0",
            "current_run_id": plan.get("refresh_id"),
            "current_status": "dirty",
            "pending_invalidations": plan.get("nodes", []),
            "retry_from_instance_key": node,
            "last_error": error,
            "metrics": metrics,
        }
    )
    status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
    atomic_json(memory_root / STATUS_REL, status)


def inspect_refresh(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    *,
    refresh_locked: bool = False,
) -> dict[str, Any]:
    if refresh_locked:
        with fact_read_lock(memory_root):
            return _inspect_refresh_unlocked(args, project_root, memory_root)
    with refresh_lock(memory_root), fact_read_lock(memory_root):
        return _inspect_refresh_unlocked(args, project_root, memory_root)


def _inspect_refresh_unlocked(args: argparse.Namespace, project_root: Path, memory_root: Path) -> dict[str, Any]:
    receipt = last_successful_receipt(memory_root)
    live = source_inventory(memory_root, project_root)
    pending = pending_intent_ids(memory_root)
    changed = sorted(
        path
        for path in set(live) | set(receipt.get("source_fingerprints", {}))
        if live.get(path) != receipt.get("source_fingerprints", {}).get(path)
    ) if receipt else sorted(live)
    policy_path = current_policy_path(memory_root, args.selection_policy, project_root)
    policy_id = content_id(load_json(policy_path)) if policy_path and policy_path.is_file() else None
    policy_changed = bool(receipt and policy_id != receipt.get("selection_policy_id"))
    panel_output = memory_root / "state/panel-refresh/inspect-panel.json"
    command = [
        sys.executable,
        str(SCRIPT_PATHS["management-panel"]),
        str(project_root),
        "inspect",
        "--memory-root",
        str(memory_root),
    ]
    if receipt and isinstance(receipt.get("panel_id"), str) and receipt["panel_id"]:
        command.extend(["--expected-panel-id", receipt["panel_id"]])
    artifact_integrity = "unverifiable"
    panel_result: dict[str, Any] = {}
    try:
        panel_result = run_json_command(
            command,
            panel_output,
            "management-panel-inspect",
            args.verbose,
            output_flag="--output",
        )
        artifact_integrity = "pass"
    except RefreshError as exc:
        panel_result = {"ok": False, "error": str(exc), "code": exc.code}
        artifact_integrity = "fail"
    business_freshness = (
        "fresh"
        if receipt and not changed and not policy_changed
        else ("stale" if receipt else "migration-required")
    )
    drift_count = 0
    audit_readiness = "missing"
    audit_binding_mismatches: list[str] = []
    audit_path = latest_audit_path(memory_root)
    if audit_path:
        audit = load_optional_json(audit_path)
        drift_count, drift_action_ids, repair_batches = audit_drift_details(audit)
        audit_binding_mismatches = source_binding_mismatches(
            audit.get("source_fingerprints"),
            receipt.get("source_fingerprints") if receipt else None,
        )
        audit_readiness = strict_audit_readiness(audit, audit_binding_mismatches)
    else:
        drift_action_ids = []
        repair_batches = []
    receipt_integrity = False
    if receipt and receipt.get("receipt_id"):
        receipt_body = dict(receipt)
        claimed_receipt_id = receipt_body.pop("receipt_id")
        receipt_integrity = claimed_receipt_id == content_id(receipt_body)
    eligible = (
        artifact_integrity == "pass"
        and business_freshness == "fresh"
        and not pending
        and drift_count == 0
        and audit_readiness == "ready"
        and receipt_integrity
    )
    result = {
        "ok": artifact_integrity == "pass",
        "operation": "inspect",
        "artifact_integrity": artifact_integrity,
        "business_freshness": business_freshness,
        "publication_eligibility": "eligible" if eligible else "blocked",
        "generation_id": receipt.get("generation_id") if receipt else None,
        "panel_id": panel_result.get("panel_id"),
        "changed_sources": changed,
        "selection_policy": str(policy_path) if policy_path else None,
        "selection_policy_id": policy_id,
        "published_selection_policy_id": receipt.get("selection_policy_id") if receipt else None,
        "selection_policy_changed": policy_changed,
        "pending_intent_ids": pending,
        "drift_count": drift_count,
        "drift_action_ids": drift_action_ids,
        "repair_batches": repair_batches,
        "audit_path": str(audit_path) if audit_path else None,
        "audit_readiness": audit_readiness,
        "audit_binding_mismatches": audit_binding_mismatches,
        "receipt_integrity": "pass" if receipt_integrity else "fail",
        "panel_inspect": panel_result,
        "recommended_workflows": (
            []
            if eligible
            else (["adp-status-sync"] if pending or drift_count else ["adp-panel-refresh"])
        ),
        **interrupted_plan(memory_root),
    }
    status = load_optional_json(memory_root / STATUS_REL)
    metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else default_metrics()
    metrics["inspect"] = int(metrics.get("inspect", 0)) + 1
    status["latest_inspect"] = result
    status["metrics"] = metrics
    status["state_id"] = content_id({key: value for key, value in status.items() if key != "state_id"})
    atomic_json(memory_root / STATUS_REL, status)
    return result


def audit_live_binding_rank(memory_root: Path, audit: dict[str, Any]) -> int:
    return int(
        isinstance(audit.get("action_projection_drift"), dict)
        and drift_audit_matches_live(memory_root, audit)
    )


def audit_generated_timestamp(path: Path, audit: dict[str, Any]) -> float:
    generated_at = audit.get("generated_at")
    if isinstance(generated_at, str):
        try:
            parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
    try:
        return path.stat().st_mtime
    except OSError:
        return float("-inf")


def latest_audit_path(memory_root: Path) -> Path | None:
    audits_root = memory_root / "audits"
    if not audits_root.is_dir():
        return None
    candidates: list[tuple[tuple[int, float, str], Path]] = []
    for path in audits_root.rglob("*.json"):
        payload = load_optional_json(path)
        if not (
            payload.get("audit_type") == "panel-input"
            or (
                payload.get("audit_type") == "input"
                and payload.get("scenario") in {"global", "management-panel"}
            )
        ):
            continue
        stable_audit_id = str(
            payload.get("input_audit_id")
            or payload.get("panel_input_audit_id")
            or payload.get("audit_id")
            or ""
        )
        candidates.append(
            (
                (
                    audit_live_binding_rank(memory_root, payload),
                    audit_generated_timestamp(path, payload),
                    stable_audit_id,
                ),
                path,
            )
        )
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def run(args: argparse.Namespace) -> dict[str, Any]:
    project_root = resolve_project_root(args.project_root)
    memory_root = resolve_memory_root(project_root, args.memory_root)
    if args.operation == "policy":
        return prepare_policy(args, project_root, memory_root)
    if args.operation == "detect":
        return detect(
            project_root,
            memory_root,
            fixture=args.fixture,
            selection_policy=args.selection_policy,
        )
    if args.operation == "plan":
        return plan_refresh(args, project_root, memory_root)
    if args.operation == "apply":
        return apply_refresh(args, project_root, memory_root)
    return inspect_refresh(args, project_root, memory_root)


def emit(result: dict[str, Any], output: str | None) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except RefreshError as exc:
        result = {
            "ok": False,
            "operation": args.operation,
            "status": "blocked",
            "error_code": exc.code,
            "error": str(exc),
            "retry_from_instance_key": exc.node,
        }
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive CLI boundary
        result = {
            "ok": False,
            "operation": args.operation,
            "status": "error",
            "error_code": "PANEL_REFRESH_INTERNAL_ERROR",
            "error": str(exc),
        }
    emit(result, args.output)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
