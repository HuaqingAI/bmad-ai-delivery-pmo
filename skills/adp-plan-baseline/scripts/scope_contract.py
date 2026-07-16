#!/usr/bin/env python3
"""Resolve physical ADP workstreams and reserved virtual baseline scopes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


SCOPE_CONTRACT_VERSION = "1.0.0"
RESERVED_VIRTUAL_SCOPE_ID = "program"
ACTION_ROUTING_IDS = frozenset({"program", "project", "adp-program"})
STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
WDR_ID_PATTERN = re.compile(r"^-\s*Workstream ID:\s*(\S.*?)\s*$", re.MULTILINE)


def normalize_cli_scope_id(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def is_reserved_virtual_scope_id(scope_id: Any) -> bool:
    """Return true only for the exact canonical baseline ID."""
    return isinstance(scope_id, str) and scope_id == RESERVED_VIRTUAL_SCOPE_ID


def is_virtual_cli_scope_id(raw: Any) -> bool:
    return normalize_cli_scope_id(raw) == RESERVED_VIRTUAL_SCOPE_ID


def is_action_routing_id(raw: Any) -> bool:
    return normalize_cli_scope_id(raw) in ACTION_ROUTING_IDS


def valid_wdr_registry(values: Iterable[Any]) -> list[str]:
    return sorted(
        {
            value
            for value in values
            if isinstance(value, str) and STABLE_ID.fullmatch(value)
        }
    )


def load_canonical_baseline(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
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


def discover_wdr_registry(
    memory_root: Path,
    *,
    include_physical: bool = True,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    legacy_virtual_root = memory_root / "workstreams" / RESERVED_VIRTUAL_SCOPE_ID
    if legacy_virtual_root.is_dir():
        entries.append(
            {
                "scope_id": RESERVED_VIRTUAL_SCOPE_ID,
                "path": (legacy_virtual_root / "delivery-record.md").as_posix(),
            }
        )
    if not include_physical:
        return entries
    for record in sorted((memory_root / "workstreams").glob("*/delivery-record.md")):
        if record.parent.name == RESERVED_VIRTUAL_SCOPE_ID:
            continue
        try:
            match = WDR_ID_PATTERN.search(record.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        scope_id = match.group(1) if match else ""
        if STABLE_ID.fullmatch(scope_id):
            entries.append({"scope_id": scope_id, "path": record.as_posix()})
    return entries


def baseline_scope_ids(canonical_baseline: Any) -> list[str]:
    if not isinstance(canonical_baseline, dict):
        return []
    return sorted(
        {
            str(item.get("workstream_id"))
            for item in canonical_baseline.get("milestones", [])
            if isinstance(item, dict)
            and isinstance(item.get("workstream_id"), str)
            and item["workstream_id"].strip()
        }
    )


def legacy_virtual_wdr_warning() -> dict[str, Any]:
    return {
        "code": "ADP-LEGACY-VIRTUAL-SCOPE-WDR",
        "severity": "warning",
        "directory": "workstreams/program/",
        "risk": (
            "The legacy directory can contain real actions, decisions, or evidence, "
            "but its WDR and BMM fields are not valid sources for the virtual program scope."
        ),
        "manual_cleanup": [
            "Migrate real actions to actions/action-ledger.md and preserve their source references.",
            "Migrate real decisions and evidence to their canonical ADP stores.",
            "Delete workstreams/program/ only after human review; no ADP workflow deletes it automatically.",
            "Rerun Input Audit, Program Status, Roadmap, and Flow Graph after deletion.",
        ],
    }


def resolve_scope_contract(
    canonical_baseline: Any,
    wdr_registry: Iterable[Any],
) -> dict[str, Any]:
    registry = valid_wdr_registry(wdr_registry)
    baseline_scopes = baseline_scope_ids(canonical_baseline)
    registered = [scope_id for scope_id in registry if not is_reserved_virtual_scope_id(scope_id)]
    virtual = []
    if RESERVED_VIRTUAL_SCOPE_ID in baseline_scopes:
        virtual.append(
            {
                "scope_id": RESERVED_VIRTUAL_SCOPE_ID,
                "scope_kind": "virtual",
                "requires_wdr": False,
                "owns_bmm_artifacts": False,
            }
        )
    known = set(registered) | {item["scope_id"] for item in virtual}
    warnings = [legacy_virtual_wdr_warning()] if RESERVED_VIRTUAL_SCOPE_ID in registry else []
    return {
        "scope_contract_version": SCOPE_CONTRACT_VERSION,
        "registered_workstreams": registered,
        "virtual_scopes": virtual,
        "baseline_scope_ids": baseline_scopes,
        "unregistered_baseline_scopes": sorted(set(baseline_scopes) - known),
        "migration_warnings": warnings,
    }


def select_scope_contract(contract: dict[str, Any], requested: Iterable[Any]) -> dict[str, Any]:
    registered = [str(value) for value in contract.get("registered_workstreams", [])]
    virtual = [item for item in contract.get("virtual_scopes", []) if isinstance(item, dict)]
    requested_values = [normalize_cli_scope_id(value) for value in requested]
    requested_values = [value for value in requested_values if value]
    if not requested_values:
        return {
            "registered_workstreams": registered,
            "virtual_scopes": virtual,
            "unknown_scopes": [],
        }

    physical_by_cli = {normalize_cli_scope_id(value): value for value in registered}
    virtual_by_cli = {
        normalize_cli_scope_id(item.get("scope_id")): item
        for item in virtual
        if normalize_cli_scope_id(item.get("scope_id"))
    }
    selected_physical: list[str] = []
    selected_virtual: list[dict[str, Any]] = []
    unknown: list[str] = []
    for value in requested_values:
        if value in physical_by_cli:
            canonical = physical_by_cli[value]
            if canonical not in selected_physical:
                selected_physical.append(canonical)
        elif value in virtual_by_cli:
            item = virtual_by_cli[value]
            if item not in selected_virtual:
                selected_virtual.append(item)
        elif value not in unknown:
            unknown.append(value)
    return {
        "registered_workstreams": sorted(selected_physical),
        "virtual_scopes": sorted(selected_virtual, key=lambda item: str(item.get("scope_id"))),
        "unknown_scopes": sorted(unknown),
    }
