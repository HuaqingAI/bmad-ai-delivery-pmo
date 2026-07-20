#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Generate ADP risk matrix, dependency map, and optional decision packet."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"


PLACEHOLDERS = {
    "",
    "-",
    "tbd",
    "todo",
    "none",
    "n/a",
    "na",
    "unknown",
    "see cross-workstream links",
    "待定",
    "无",
    "暂无",
    "未知",
}

MISSING_WORKSTREAM_GAP = "no workstream records found under ADP memory root"
RISK_RELATION_UPDATE_SCHEMA_VERSION = "1.0.0"
RISK_RELATION_RECEIPT_SCHEMA_VERSION = "1.0.0"
RISK_RELATION_RECEIPT_ROOT = Path("receipts") / "risk-relations"
RISK_SOURCE_FIELDS = {
    "Project Status.blocker": ("Blockers", "blocker"),
    "Project Status.risk": ("Risks", "risk"),
    "Project Status.change": ("Scope or change notes", "change"),
}
DECISION_SOURCE_FIELDS = {"Decision / Question", "Project Status.decision/change"}


@dataclass
class Workstream:
    path: Path
    workstream_id: str
    name: str = "TBD"
    owner: str = "TBD"
    business_owner: str = "TBD"
    phase: str = "TBD"
    status: str = "TBD"
    risks: str = "TBD"
    blockers: str = "TBD"
    dependencies_note: str = "TBD"
    change_notes: str = "TBD"
    next_actions: str = "TBD"
    last_status_sync: str = "TBD"
    depends_on: list[str] = field(default_factory=list)
    impacts: list[str] = field(default_factory=list)
    dependency_facts: list[str] = field(default_factory=list)
    impact_facts: list[str] = field(default_factory=list)
    l0_references: list[str] = field(default_factory=list)
    decision_rows: list[dict[str, str]] = field(default_factory=list)
    decision_path: Path | None = None
    decision_fingerprint: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review ADP workstream records for risks, dependencies, blockers, and changes.",
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--workstream", action="append", default=[], help="Workstream id to include. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="Report findings without writing derived files.")
    parser.add_argument("--packet-title", default="", help="Business Decision Packet title.")
    parser.add_argument("--packet-background", default="", help="Business Decision Packet background.")
    parser.add_argument("--packet-question", default="", help="Unresolved business question.")
    parser.add_argument("--packet-option", action="append", default=[], help="Decision option. Repeatable.")
    parser.add_argument("--packet-impact", action="append", default=[], help="Impact statement. Repeatable.")
    parser.add_argument("--packet-recommendation", default="", help="Recommended decision.")
    parser.add_argument("--packet-deadline", default="", help="Deadline or trigger.")
    parser.add_argument("--packet-owner", default="", help="Requested decision owner.")
    parser.add_argument("--packet-workstream", action="append", default=[], help="Affected workstream id. Repeatable.")
    parser.add_argument("--language", help="Override document_output_language for derived views.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
    parser.add_argument("--relation-updates-file", help="Approved structured risk relation updates JSON.")
    parser.add_argument("--apply-relations", action="store_true", help="Apply a previously previewed relation update plan.")
    parser.add_argument("--verified-plan-token", help="Exact token returned by the unchanged relation preview.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def normalize_id(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def split_cross_link_entries(items: list[str]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    facts: list[str] = []
    for item in items:
        value = item.strip()
        if re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", value):
            normalized = value.lower()
            if normalized not in ids:
                ids.append(normalized)
        elif value not in facts:
            facts.append(value)
    return ids, facts


def slugify(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "business-decision"


def is_meaningful(value: str) -> bool:
    clean = value.strip().strip("`").strip()
    return clean.lower() not in PLACEHOLDERS


def first_meaningful(*values: str) -> str:
    for value in values:
        if is_meaningful(value):
            return value.strip()
    return "TBD"


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def clean_bullet(line: str) -> str:
    value = line.strip()
    value = re.sub(r"^[-*]\s+", "", value)
    return value.strip()


def parse_identity(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, value = stripped[2:].split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def section(lines: list[str], heading: str) -> list[str]:
    start = None
    marker = f"## {heading}".lower()
    for index, line in enumerate(lines):
        if line.strip().lower() == marker:
            start = index + 1
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return lines[start:end]


def parse_key_bullets(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, value = stripped[2:].split(":", 1)
        result[key.strip().lower()] = value.strip()
    return result


def parse_cross_links(lines: list[str], label: str) -> list[str]:
    out: list[str] = []
    active = False
    label_marker = f"{label}:".lower()
    section_labels = {"depends on:", "impacts:", "l0 references:"}
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower == label_marker:
            active = True
            continue
        if active and lower in section_labels:
            break
        if active and stripped.startswith(("- ", "* ")):
            item = clean_bullet(stripped)
            if is_meaningful(item):
                out.append(item)
    return out


def parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    table_lines = [line.strip() for line in lines if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return []
    headers = [cell.strip().lower() for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[1:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        if any(is_meaningful(value) for value in row.values()):
            rows.append(row)
    return rows


def extract_inline_field(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = re.compile(rf"(?:^|[;|,])\s*{re.escape(label)}\s*[:=]\s*([^;|,\n]+)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            value = match.group(1).strip()
            if is_meaningful(value):
                return value
    return "TBD"


def strip_inline_fields(text: str, labels: list[str]) -> str:
    cleaned = text
    for label in labels:
        cleaned = re.sub(
            rf"(?:^|[;|,])\s*{re.escape(label)}\s*[:=]\s*[^;|,\n]+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = cleaned.strip(" ;,|")
    return cleaned if is_meaningful(cleaned) else text


def parse_workstream(record_path: Path) -> Workstream:
    lines = record_path.read_text(encoding="utf-8").splitlines()
    identity = parse_identity(section(lines, "Identity"))
    status = parse_key_bullets(section(lines, "Project Status"))
    cross = section(lines, "Cross-Workstream Links")
    depends_on, dependency_facts = split_cross_link_entries(parse_cross_links(cross, "Depends on"))
    impacts, impact_facts = split_cross_link_entries(parse_cross_links(cross, "Impacts"))
    workstream_id = identity.get("workstream id") or record_path.parent.name
    workstream = Workstream(
        path=record_path,
        workstream_id=normalize_id(workstream_id) or record_path.parent.name,
        name=identity.get("name", "TBD"),
        owner=identity.get("fde owner", "TBD"),
        business_owner=identity.get("business owner", "TBD"),
        phase=identity.get("current bmm phase", "TBD"),
        status=identity.get("current adp status", "TBD"),
        risks=status.get("risks", "TBD"),
        blockers=status.get("blockers", "TBD"),
        dependencies_note=status.get("dependencies", "TBD"),
        change_notes=status.get("scope or change notes", "TBD"),
        next_actions=status.get("next actions", "TBD"),
        last_status_sync=status.get("last status sync", "TBD"),
        depends_on=depends_on,
        impacts=impacts,
        dependency_facts=dependency_facts,
        impact_facts=impact_facts,
        l0_references=parse_cross_links(cross, "L0 references"),
    )
    decision_file = record_path.parent / "decisions.md"
    if decision_file.exists():
        workstream.decision_rows = parse_markdown_table(decision_file.read_text(encoding="utf-8").splitlines())
        workstream.decision_path = decision_file
        workstream.decision_fingerprint = sha256_bytes(decision_file.read_bytes())
    return workstream


def discover_records(memory_root: Path, selected: list[str]) -> tuple[list[Path], list[str]]:
    workstreams_root = memory_root / "workstreams"
    selected_ids = {normalize_id(item) for item in selected}
    missing: list[str] = []
    records: list[Path] = []

    if selected_ids:
        for workstream_id in sorted(selected_ids):
            path = workstreams_root / workstream_id / "delivery-record.md"
            if path.exists():
                records.append(path)
            else:
                missing.append(workstream_id)
        return records, missing

    if not workstreams_root.exists():
        return [], []
    return sorted(workstreams_root.glob("*/delivery-record.md")), []


def risk_entries(workstreams: list[Workstream], baseline_revision: int | None = None, memory_root: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    gaps: list[str] = []
    if not workstreams:
        return [], [MISSING_WORKSTREAM_GAP]

    for ws in workstreams:
        affected = ", ".join([*ws.impacts, ws.workstream_id]) if ws.impacts else ws.workstream_id
        if is_meaningful(ws.blockers):
            entries.append(make_risk(ws, "blocker", ws.blockers, affected, baseline_revision, memory_root))
        else:
            gaps.append(f"{ws.workstream_id}: blocker status is missing or TBD")

        if is_meaningful(ws.risks):
            entries.append(make_risk(ws, "risk", ws.risks, affected, baseline_revision, memory_root))
        else:
            gaps.append(f"{ws.workstream_id}: risk exposure is missing or TBD")

        if is_meaningful(ws.change_notes):
            entries.append(make_risk(ws, "change", ws.change_notes, affected, baseline_revision, memory_root))

        if not ws.depends_on and not ws.l0_references and not is_meaningful(ws.dependencies_note):
            gaps.append(f"{ws.workstream_id}: dependencies are missing or TBD")

        for row in ws.decision_rows:
            row_type = row.get("type", "").lower()
            decision = row.get("decision / question", "")
            if not is_meaningful(decision):
                continue
            if any(token in row_type for token in ["change", "scope", "risk acceptance", "business decision"]):
                impact = row.get("impact", "TBD")
                owner = row.get("owner", ws.owner)
                entry = {
                        "type": "decision/change",
                        "workstream": ws.workstream_id,
                        "description": decision,
                        "severity": first_meaningful(row.get("severity", ""), extract_inline_field(decision, ["severity", "严重度"])),
                        "likelihood": first_meaningful(row.get("likelihood", ""), extract_inline_field(decision, ["likelihood", "可能性"])),
                        "owner": owner or ws.owner,
                        "affected": impact if is_meaningful(impact) else ws.workstream_id,
                        "next_action": f"Close decision row with status {row.get('status', 'TBD')}",
                        "escalation": first_meaningful(
                            row.get("escalation", ""),
                            extract_inline_field(decision, ["escalation", "upgrade", "升级"]),
                        ),
                }
                entry.update(
                    canonical_risk_fields(
                        ws,
                        "decision/change",
                        decision,
                        baseline_revision,
                        memory_root,
                        source_path=ws.decision_path,
                        source_field="Decision / Question",
                        artifact_id=f"WORKSTREAM-DECISIONS-{ws.workstream_id.upper()}",
                        source_fingerprint=ws.decision_fingerprint,
                    )
                )
                entries.append(entry)
    gaps.extend(risk_detail_gaps(entries))
    return entries, gaps


def make_risk(
    ws: Workstream,
    entry_type: str,
    description: str,
    affected: str,
    baseline_revision: int | None = None,
    memory_root: Path | None = None,
) -> dict[str, Any]:
    labels = [
        "severity", "likelihood", "escalation", "严重度", "可能性", "升级",
        "risk_id", "lifecycle", "relation_state", "observed_at", "terminal_at",
        "baseline_revision", "related_plan_item_ids", "related_flow_edge_ids",
    ]
    next_action = ws.next_actions if is_meaningful(ws.next_actions) else "Assign concrete next action"
    entry: dict[str, Any] = {
        "type": entry_type,
        "workstream": ws.workstream_id,
        "description": strip_inline_fields(description, labels),
        "severity": extract_inline_field(description, ["severity", "严重度"]),
        "likelihood": extract_inline_field(description, ["likelihood", "可能性"]),
        "owner": ws.owner,
        "affected": affected,
        "next_action": next_action,
        "escalation": extract_inline_field(description, ["escalation", "upgrade", "升级"]),
    }
    entry.update(canonical_risk_fields(ws, entry_type, description, baseline_revision, memory_root))
    return entry


def canonical_risk_fields(
    ws: Workstream,
    entry_type: str,
    description: str,
    baseline_revision: int | None,
    memory_root: Path | None,
    *,
    source_path: Path | None = None,
    source_field: str | None = None,
    artifact_id: str | None = None,
    source_fingerprint: str | None = None,
) -> dict[str, Any]:
    explicit_id = extract_inline_field(description, ["risk_id"])
    semantic_description = strip_inline_fields(
        description,
        ["risk_id", "lifecycle", "relation_state", "observed_at", "terminal_at", "baseline_revision", "related_plan_item_ids", "related_flow_edge_ids"],
    )
    if is_meaningful(explicit_id):
        risk_id = explicit_id.strip()
    else:
        digest = hashlib.sha256(f"{ws.workstream_id}\n{entry_type}\n{semantic_description.strip().casefold()}".encode("utf-8")).hexdigest()[:16]
        risk_id = f"RISK-{digest}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", risk_id):
        risk_id = "RISK-" + hashlib.sha256(risk_id.encode("utf-8")).hexdigest()[:16]
    lifecycle = extract_inline_field(description, ["lifecycle"])
    default_lifecycle = "mitigating" if entry_type == "blocker" else ("monitoring" if "change" in entry_type else "open")
    lifecycle = lifecycle.strip().lower() if is_meaningful(lifecycle) else default_lifecycle
    if lifecycle not in {"open", "monitoring", "mitigating", "accepted", "closed", "cancelled"}:
        lifecycle = default_lifecycle
    relation_state = extract_inline_field(description, ["relation_state"])
    default_relation = "blocked" if entry_type == "blocker" else ("watching" if "change" in entry_type else "at-risk")
    relation_state = relation_state.strip().lower() if is_meaningful(relation_state) else default_relation
    if relation_state not in {"watching", "at-risk", "blocked", "resolved", "not-applicable"}:
        relation_state = default_relation
    observed = extract_inline_field(description, ["observed_at"])
    observed_at = normalize_contract_timestamp(observed if is_meaningful(observed) else ws.last_status_sync)
    terminal = extract_inline_field(description, ["terminal_at"])
    terminal_at = normalize_contract_timestamp(terminal) if is_meaningful(terminal) else None
    explicit_revision = extract_inline_field(description, ["baseline_revision"])
    revision = int(explicit_revision) if is_meaningful(explicit_revision) and explicit_revision.isdigit() else baseline_revision
    related_nodes = parse_related_ids(extract_inline_field(description, ["related_plan_item_ids"]))
    related_edges = parse_related_ids(extract_inline_field(description, ["related_flow_edge_ids"]))
    resolved_source_path = source_path or ws.path
    contract_source_path = resolved_source_path.as_posix()
    if memory_root is not None:
        try:
            contract_source_path = resolved_source_path.resolve().relative_to(memory_root.resolve()).as_posix()
        except ValueError:
            pass
    resolved_fingerprint = source_fingerprint or sha256_bytes(resolved_source_path.read_bytes())
    return {
        "risk_id": risk_id,
        "lifecycle": lifecycle,
        "relation_state": relation_state,
        "observed_at": observed_at,
        "terminal_at": terminal_at,
        "baseline_revision": revision,
        "related_plan_item_ids": related_nodes,
        "related_flow_edge_ids": related_edges,
        "rule_id": "RISK-" + relation_state.upper(),
        "sources": [
            {
                "artifact_id": artifact_id or f"WDR-{ws.workstream_id.upper()}",
                "artifact_path": contract_source_path,
                "field": source_field or f"Project Status.{entry_type}",
                "source_fingerprint": resolved_fingerprint,
            }
        ],
    }


def normalize_contract_timestamp(value: str) -> str:
    text = str(value or "").strip().strip("`")
    if not is_meaningful(text):
        return "1970-01-01T00:00:00Z"
    try:
        if "T" not in text:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return "1970-01-01T00:00:00Z"
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_related_ids(value: str) -> list[str]:
    if not is_meaningful(value):
        return []
    return sorted(
        {
            item.strip()
            for item in re.split(r"\s*[;/+]\s*", value)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item.strip())
        }
    )


def baseline_revision(memory_root: Path) -> int | None:
    path = memory_root / "plans/program-baseline.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig")
    marker = text.find("<!-- adp:program-baseline:v1 -->")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text[marker:], re.DOTALL) if marker >= 0 else None
    if not match:
        return None
    value = json.loads(match.group(1)).get("revision")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def risk_flow_contract(entries: list[dict[str, Any]]) -> dict[str, Any]:
    risks = [
        {
            key: entry[key]
            for key in (
                "risk_id", "lifecycle", "relation_state", "observed_at", "terminal_at",
                "baseline_revision", "related_plan_item_ids", "related_flow_edge_ids", "rule_id", "sources",
            )
        }
        for entry in entries
        if isinstance(entry.get("baseline_revision"), int)
    ]
    return {
        "risk_flow_schema_version": "1.0.0",
        "risks": sorted(risks, key=lambda item: item["risk_id"]),
        "compatibility": {"strategy": "preserve-unmapped", "migration_error_code": "ADP-RISK-FLOW-MIGRATION-REQUIRED"},
    }


def risk_detail_gaps(entries: list[dict[str, str]]) -> list[str]:
    gaps: list[str] = []
    for entry in entries:
        prefix = f"{entry['workstream']}: {entry['type']}"
        if not is_meaningful(entry.get("severity", "")):
            gaps.append(f"{prefix} severity is missing")
        if not is_meaningful(entry.get("likelihood", "")):
            gaps.append(f"{prefix} likelihood is missing")
        if not is_meaningful(entry.get("escalation", "")):
            gaps.append(f"{prefix} escalation path is missing")
    return gaps


def dependency_entries(workstreams: list[Workstream]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for ws in workstreams:
        if is_meaningful(ws.dependencies_note):
            entries.append(make_dependency(ws, "dependency note", ws.dependencies_note))
        for target in ws.depends_on:
            entries.append(make_dependency(ws, "depends on", target))
        for target in ws.impacts:
            entries.append(make_dependency(ws, "impacts", target))
        for fact in ws.dependency_facts:
            entries.append(make_dependency(ws, "dependency fact", fact))
        for fact in ws.impact_facts:
            entries.append(make_dependency(ws, "impact fact", fact))
        for target in ws.l0_references:
            entries.append(make_dependency(ws, "l0 reference", target))
    return entries


def make_dependency(ws: Workstream, relationship: str, target: str) -> dict[str, str]:
    return {
        "source": ws.workstream_id,
        "relationship": relationship,
        "target": target,
        "owner": ws.owner,
        "status": "open",
        "next_action": ws.next_actions if is_meaningful(ws.next_actions) else "Confirm dependency owner and closure condition",
    }


def normalize_approved_at(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("approved_at is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("approved_at must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("approved_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stable_id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item):
            raise ValueError(f"{label} contains invalid stable ID {item!r}")
        if item in result:
            raise ValueError(f"{label} contains duplicate stable ID {item!r}")
        result.append(item)
    return sorted(result)


def require_exact_keys(value: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - allowed)
    if missing:
        raise ValueError(f"{label} is missing required fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(extra)}")


def safe_memory_path(memory_root: Path, raw_path: Any) -> tuple[Path, str]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("source_artifact_path is required")
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        raise ValueError("source_artifact_path must be relative to the ADP memory root")
    resolved = (memory_root / candidate).resolve()
    try:
        relative = resolved.relative_to(memory_root.resolve())
    except ValueError as exc:
        raise ValueError("source_artifact_path escapes the ADP memory root") from exc
    if not resolved.is_file():
        raise ValueError(f"source artifact does not exist: {relative.as_posix()}")
    return resolved, relative.as_posix()


def validate_relation_payload(
    payload: Any,
    memory_root: Path,
    flow_graph: dict[str, Any],
    risk_flow: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("relation updates file must contain a JSON object")
    root_fields = {
        "risk_relation_update_schema_version",
        "_control",
        "proposal_only",
        "approval_status",
        "approved_by",
        "approved_at",
        "flow_graph_id",
        "baseline_revision",
        "updates",
    }
    require_exact_keys(payload, root_fields, root_fields, "relation update payload")
    if payload["risk_relation_update_schema_version"] != RISK_RELATION_UPDATE_SCHEMA_VERSION:
        raise ValueError("unsupported risk_relation_update_schema_version")
    control = payload["_control"]
    if not isinstance(control, dict) or control.get("execute_allowed") is not True:
        raise ValueError("relation update payload requires _control.execute_allowed=true")
    if payload["proposal_only"] is not False:
        raise ValueError("proposal_only must be false for an executable relation intake")
    if payload["approval_status"] != "approved":
        raise ValueError("approval_status must be approved")
    approved_by = str(payload["approved_by"] or "").strip()
    if not approved_by:
        raise ValueError("approved_by is required")
    approved_at = normalize_approved_at(payload["approved_at"])
    if payload["flow_graph_id"] != flow_graph.get("flow_graph_id"):
        raise ValueError("flow_graph_id does not match the current canonical graph")
    current_revision = flow_graph.get("topology", {}).get("baseline_revision")
    revision = payload["baseline_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("baseline_revision must be a positive integer")
    if revision != current_revision:
        raise ValueError("baseline_revision does not match the current canonical graph")
    raw_updates = payload["updates"]
    if not isinstance(raw_updates, list) or not raw_updates:
        raise ValueError("updates must be a non-empty array")

    node_ids = {str(item["node_id"]) for item in flow_graph["topology"]["nodes"]}
    edge_ids = {str(item["edge_id"]) for item in flow_graph["topology"]["edges"]}
    current_risks = {str(item["risk_id"]): item for item in risk_flow.get("risks", [])}
    normalized_updates: list[dict[str, Any]] = []
    seen_risks: set[str] = set()
    update_fields = {
        "risk_id",
        "workstream_id",
        "source_artifact_path",
        "source_fingerprint",
        "source_field",
        "related_plan_item_ids",
        "related_flow_edge_ids",
    }
    for index, raw_update in enumerate(raw_updates):
        if not isinstance(raw_update, dict):
            raise ValueError(f"updates[{index}] must be an object")
        require_exact_keys(raw_update, update_fields, update_fields, f"updates[{index}]")
        risk_id = str(raw_update["risk_id"] or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", risk_id):
            raise ValueError(f"updates[{index}].risk_id is invalid")
        if risk_id in seen_risks:
            raise ValueError(f"duplicate risk update: {risk_id}")
        seen_risks.add(risk_id)
        current_risk = current_risks.get(risk_id)
        if current_risk is None:
            raise ValueError(f"risk_id is not present in current risk-flow: {risk_id}")
        current_risk_revision = current_risk.get("baseline_revision")
        if (
            not isinstance(current_risk_revision, int)
            or isinstance(current_risk_revision, bool)
            or current_risk_revision < 1
            or current_risk_revision > revision
        ):
            raise ValueError(f"risk {risk_id} baseline revision cannot be rebound to the intake revision")
        workstream_id = normalize_id(str(raw_update["workstream_id"] or ""))
        if not workstream_id:
            raise ValueError(f"updates[{index}].workstream_id is required")
        source_path, source_relative = safe_memory_path(memory_root, raw_update["source_artifact_path"])
        expected_parent = Path("workstreams") / workstream_id
        if Path(source_relative).parent != expected_parent:
            raise ValueError(f"risk {risk_id} source path does not match workstream_id")
        source_field = str(raw_update["source_field"] or "").strip()
        if source_field in RISK_SOURCE_FIELDS:
            if source_path.name != "delivery-record.md":
                raise ValueError(f"risk {risk_id} Project Status source must be delivery-record.md")
        elif source_field in DECISION_SOURCE_FIELDS:
            if source_path.name != "decisions.md":
                raise ValueError(f"risk {risk_id} decision source must be decisions.md")
        else:
            raise ValueError(f"risk {risk_id} has unsupported source_field {source_field!r}")
        fingerprint = str(raw_update["source_fingerprint"] or "").strip().lower()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
            raise ValueError(f"risk {risk_id} source_fingerprint is invalid")
        if sha256_bytes(source_path.read_bytes()) != fingerprint:
            raise ValueError(f"risk {risk_id} source fingerprint does not match current bytes")
        related_nodes = stable_id_list(raw_update["related_plan_item_ids"], f"risk {risk_id} related_plan_item_ids")
        related_edges = stable_id_list(raw_update["related_flow_edge_ids"], f"risk {risk_id} related_flow_edge_ids")
        if not related_nodes and not related_edges:
            raise ValueError(f"risk {risk_id} requires at least one explicit relation ID")
        unknown_nodes = sorted(set(related_nodes) - node_ids)
        unknown_edges = sorted(set(related_edges) - edge_ids)
        if unknown_nodes:
            raise ValueError(f"risk {risk_id} references unknown plan items: {', '.join(unknown_nodes)}")
        if unknown_edges:
            raise ValueError(f"risk {risk_id} references unknown flow edges: {', '.join(unknown_edges)}")
        normalized_updates.append(
            {
                "risk_id": risk_id,
                "workstream_id": workstream_id,
                "source_artifact_path": source_relative,
                "source_fingerprint": fingerprint,
                "source_field": source_field,
                "related_plan_item_ids": related_nodes,
                "related_flow_edge_ids": related_edges,
            }
        )
    return {
        **payload,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "updates": sorted(normalized_updates, key=lambda item: item["risk_id"]),
    }


def relation_inline_text(update: dict[str, Any], baseline_revision_value: int, description: str) -> str:
    semantic = strip_inline_fields(
        description,
        ["risk_id", "baseline_revision", "related_plan_item_ids", "related_flow_edge_ids"],
    ).strip(" ;,|")
    fields = [
        semantic,
        f"risk_id:{update['risk_id']}",
        f"baseline_revision:{baseline_revision_value}",
    ]
    if update["related_plan_item_ids"]:
        fields.append(f"related_plan_item_ids:{'+'.join(update['related_plan_item_ids'])}")
    if update["related_flow_edge_ids"]:
        fields.append(f"related_flow_edge_ids:{'+'.join(update['related_flow_edge_ids'])}")
    return "; ".join(item for item in fields if item)


def update_project_status_relation(
    record_path: Path,
    update: dict[str, Any],
    baseline_revision_value: int,
    memory_root: Path,
) -> None:
    field_label, entry_type = RISK_SOURCE_FIELDS[update["source_field"]]
    workstream = parse_workstream(record_path)
    lines = record_path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip().lower() == "## project status"), None)
    if start is None:
        raise ValueError(f"risk {update['risk_id']} source has no Project Status section")
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    pattern = re.compile(rf"^(\s*-\s*{re.escape(field_label)}\s*:\s*)(.*)$", re.IGNORECASE)
    matches = [(index, pattern.match(lines[index])) for index in range(start + 1, end)]
    matches = [(index, match) for index, match in matches if match]
    if len(matches) != 1:
        raise ValueError(f"risk {update['risk_id']} source field must occur exactly once")
    index, match = matches[0]
    assert match is not None
    description = match.group(2).strip()
    current = canonical_risk_fields(workstream, entry_type, description, baseline_revision_value, memory_root)
    if current["risk_id"] != update["risk_id"]:
        raise ValueError(f"risk {update['risk_id']} does not match the selected Project Status field")
    lines[index] = match.group(1) + relation_inline_text(update, baseline_revision_value, description)
    record_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) and stripped[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def markdown_table_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|").strip()


def update_decision_relation(
    decision_path: Path,
    record_path: Path,
    update: dict[str, Any],
    baseline_revision_value: int,
    memory_root: Path,
) -> None:
    workstream = parse_workstream(record_path)
    lines = decision_path.read_text(encoding="utf-8").splitlines()
    header_index = None
    headers: list[str] = []
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        candidate = [cell.lower() for cell in split_markdown_row(line)]
        if "decision / question" in candidate and "type" in candidate:
            header_index = index
            headers = candidate
            break
    if header_index is None:
        raise ValueError(f"risk {update['risk_id']} decision source has no supported table")
    decision_index = headers.index("decision / question")
    type_index = headers.index("type")
    matches: list[tuple[int, list[str]]] = []
    for index in range(header_index + 1, len(lines)):
        if not lines[index].strip().startswith("|"):
            continue
        cells = split_markdown_row(lines[index])
        if len(cells) != len(headers) or all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        row_type = cells[type_index].lower()
        if not any(token in row_type for token in ["change", "scope", "risk acceptance", "business decision"]):
            continue
        description = cells[decision_index]
        current = canonical_risk_fields(workstream, "decision/change", description, baseline_revision_value, memory_root)
        if current["risk_id"] == update["risk_id"]:
            matches.append((index, cells))
    if len(matches) != 1:
        raise ValueError(f"risk {update['risk_id']} must match exactly one decision row")
    line_index, cells = matches[0]
    cells[decision_index] = relation_inline_text(update, baseline_revision_value, cells[decision_index])
    lines[line_index] = "| " + " | ".join(markdown_table_cell(cell) for cell in cells) + " |"
    decision_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def apply_relation_to_staged_source(
    staged_root: Path,
    update: dict[str, Any],
    baseline_revision_value: int,
) -> None:
    source_path = staged_root / update["source_artifact_path"]
    record_path = staged_root / "workstreams" / update["workstream_id"] / "delivery-record.md"
    if update["source_field"] in RISK_SOURCE_FIELDS:
        update_project_status_relation(source_path, update, baseline_revision_value, staged_root)
    else:
        update_decision_relation(source_path, record_path, update, baseline_revision_value, staged_root)


def changed_staged_files(memory_root: Path, staged_root: Path) -> list[Path]:
    changed: list[Path] = []
    for staged_path in sorted(path for path in staged_root.rglob("*") if path.is_file()):
        relative = staged_path.relative_to(staged_root)
        canonical_path = memory_root / relative
        if not canonical_path.is_file() or canonical_path.read_bytes() != staged_path.read_bytes():
            changed.append(relative)
    return changed


def write_temp_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def publish_staged_files(memory_root: Path, staged_root: Path, relatives: list[Path]) -> None:
    originals = {
        relative: (memory_root / relative).read_bytes() if (memory_root / relative).is_file() else None
        for relative in relatives
    }
    prepared: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for relative in relatives:
            prepared[relative] = write_temp_bytes(memory_root / relative, (staged_root / relative).read_bytes())
        for relative in relatives:
            os.replace(prepared[relative], memory_root / relative)
            committed.append(relative)
    except BaseException:
        for relative in reversed(committed):
            canonical_path = memory_root / relative
            original = originals[relative]
            if original is None:
                canonical_path.unlink(missing_ok=True)
            else:
                restore_temp = write_temp_bytes(canonical_path, original)
                os.replace(restore_temp, canonical_path)
        raise
    finally:
        for temp_path in prepared.values():
            temp_path.unlink(missing_ok=True)


def relation_change_manifest(memory_root: Path, staged_root: Path, relatives: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "artifact_path": relative.as_posix(),
            "before_fingerprint": sha256_bytes((memory_root / relative).read_bytes()) if (memory_root / relative).is_file() else None,
            "after_fingerprint": sha256_bytes((staged_root / relative).read_bytes()),
        }
        for relative in relatives
    ]


def relation_receipt_id(input_hash: str, flow_graph_id: str) -> str:
    digest = hashlib.sha256(f"{input_hash}\n{flow_graph_id}".encode("utf-8")).hexdigest()[:32]
    return f"rrr-{digest}"


def run_relation_updates(
    args: argparse.Namespace,
    project_root: Path,
    memory_root: Path,
    message,
    language: dict[str, Any],
) -> dict[str, Any]:
    if args.dry_run and args.apply_relations:
        raise ValueError("--dry-run cannot be combined with --apply-relations")
    input_path = Path(args.relation_updates_file).expanduser().resolve()
    if not input_path.is_file():
        raise ValueError("relation-updates-file is not an existing file")
    input_bytes = input_path.read_bytes()
    input_hash = sha256_bytes(input_bytes)
    try:
        raw_payload = json.loads(input_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("relation-updates-file must contain valid UTF-8 JSON") from exc
    flow_path = memory_root / "views/flow-graph.json"
    risk_flow_path = memory_root / "views/risk-flow.json"
    if not flow_path.is_file() or not risk_flow_path.is_file():
        raise ValueError("current flow-graph.json and risk-flow.json are required")
    flow_graph = json.loads(flow_path.read_text(encoding="utf-8"))
    current_risk_flow = json.loads(risk_flow_path.read_text(encoding="utf-8"))
    raw_flow_graph_id = raw_payload.get("flow_graph_id") if isinstance(raw_payload, dict) else None
    receipt_id = relation_receipt_id(input_hash, str(raw_flow_graph_id))
    receipt_relative = RISK_RELATION_RECEIPT_ROOT / f"{receipt_id}.json"
    receipt_path = memory_root / receipt_relative
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if raw_flow_graph_id != flow_graph.get("flow_graph_id"):
            raise ValueError("existing receipt belongs to a non-current flow graph; create a new relation intake")
        if (
            receipt.get("input_path") != str(input_path)
            or receipt.get("input_hash") != input_hash
            or receipt.get("flow_graph_id") != raw_flow_graph_id
        ):
            raise ValueError(f"existing receipt binding conflicts at {receipt_path}")
        return {
            "ok": True,
            "mode": "risk-relation-update",
            "status": "already-applied",
            "dry_run": False,
            "input_path": str(input_path),
            "input_hash": input_hash,
            "receipt": receipt,
            "receipt_path": str(receipt_path),
        }
    payload = validate_relation_payload(raw_payload, memory_root, flow_graph, current_risk_flow)

    with tempfile.TemporaryDirectory(prefix=".risk-relations-", dir=memory_root.parent) as temp_dir:
        staged_root = Path(temp_dir) / "memory"
        shutil.copytree(memory_root, staged_root)
        for update in payload["updates"]:
            apply_relation_to_staged_source(staged_root, update, payload["baseline_revision"])

        records, missing = discover_records(staged_root, [])
        if missing:
            raise ValueError(f"staged relation update has missing workstreams: {', '.join(missing)}")
        workstreams = [parse_workstream(path) for path in records]
        risks, gaps = risk_entries(workstreams, payload["baseline_revision"], staged_root)
        dependencies = dependency_entries(workstreams)
        staged_risk_path = staged_root / "views/risk-matrix.md"
        staged_dependency_path = staged_root / "views/dependency-map.md"
        staged_risk_flow_path = staged_root / "views/risk-flow.json"
        write_text(staged_risk_path, render_risk_matrix(risks, gaps, payload["approved_at"], message), False)
        write_text(staged_dependency_path, render_dependency_map(dependencies, payload["approved_at"], message), False)
        staged_contract = risk_flow_contract(risks)
        write_text(
            staged_risk_flow_path,
            json.dumps(staged_contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            False,
        )
        staged_risks = {item["risk_id"]: item for item in staged_contract["risks"]}
        for update in payload["updates"]:
            staged_risk = staged_risks.get(update["risk_id"])
            if staged_risk is None:
                raise ValueError(f"risk {update['risk_id']} disappeared after staged regeneration")
            if staged_risk["related_plan_item_ids"] != update["related_plan_item_ids"]:
                raise ValueError(f"risk {update['risk_id']} staged plan-item relations do not match")
            if staged_risk["related_flow_edge_ids"] != update["related_flow_edge_ids"]:
                raise ValueError(f"risk {update['risk_id']} staged flow-edge relations do not match")
            if staged_risk["baseline_revision"] != payload["baseline_revision"]:
                raise ValueError(f"risk {update['risk_id']} staged baseline revision does not match")

        changed = changed_staged_files(memory_root, staged_root)
        changes = relation_change_manifest(memory_root, staged_root, changed)
        token_seed = {
            "input_path": str(input_path),
            "input_hash": input_hash,
            "flow_graph_id": payload["flow_graph_id"],
            "baseline_revision": payload["baseline_revision"],
            "approved_by": payload["approved_by"],
            "approved_at": payload["approved_at"],
            "updates": payload["updates"],
            "changes": changes,
        }
        plan_token = "sha256:" + hashlib.sha256(canonical_json_bytes(token_seed)).hexdigest()
        if args.apply_relations:
            if args.verified_plan_token != plan_token:
                raise ValueError("verified plan token is missing or does not match the current relation preview")
            applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            receipt = {
                "risk_relation_receipt_schema_version": RISK_RELATION_RECEIPT_SCHEMA_VERSION,
                "receipt_id": receipt_id,
                "status": "applied",
                "input_path": str(input_path),
                "input_hash": input_hash,
                "verified_plan_token": plan_token,
                "flow_graph_id": payload["flow_graph_id"],
                "baseline_revision": payload["baseline_revision"],
                "approved_by": payload["approved_by"],
                "approved_at": payload["approved_at"],
                "applied_at": applied_at,
                "risk_ids": [item["risk_id"] for item in payload["updates"]],
                "changes": changes,
            }
            staged_receipt = staged_root / receipt_relative
            write_text(staged_receipt, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", False)
            publish_staged_files(memory_root, staged_root, [*changed, receipt_relative])
            status = "applied"
        else:
            receipt = None
            status = "preview"
    return {
        "ok": True,
        "mode": "risk-relation-update",
        "status": status,
        "dry_run": status == "preview",
        "apply_authorized": status == "applied",
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "input_path": str(input_path),
        "input_hash": input_hash,
        "flow_graph_id": payload["flow_graph_id"],
        "baseline_revision": payload["baseline_revision"],
        "risk_ids": [item["risk_id"] for item in payload["updates"]],
        "verified_plan_token": plan_token,
        "changes": changes,
        "receipt": receipt,
        "receipt_path": str(receipt_path) if status == "applied" else None,
        "recommended_workflows": ["adp-flow-graph", "adp-state-audit", "adp-meeting-pack", "adp-management-panel"],
        "language": language,
    }


def render_risk_matrix(entries: list[dict[str, str]], gaps: list[str], generated_at: str, message) -> str:
    rows = [
        f"# {message('risk.title.matrix')}",
        "",
        f"{message('common.generated')}: {generated_at}",
        "",
        "| " + " | ".join(message(key) for key in ["common.id", "common.workstream", "common.type", "common.description", "common.severity", "risk.likelihood", "common.owner", "risk.affected", "risk.next_action", "common.escalation"]) + " |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if entries:
        for index, entry in enumerate(entries, start=1):
            rows.append(
                "| "
                + " | ".join(
                    [
                        f"R-{index:03d}",
                        md(entry["workstream"]),
                        md(entry["type"]),
                        md(entry["description"]),
                        md(entry["severity"]),
                        md(entry["likelihood"]),
                        md(entry["owner"]),
                        md(entry["affected"]),
                        md(entry["next_action"]),
                        md(entry["escalation"]),
                    ],
                )
                + " |",
            )
    else:
        rows.append(f"| TBD | TBD | gap | {message('risk.no_entries')} | TBD | TBD | TBD | TBD | {message('risk.review_wdr')} | TBD |")

    rows.extend(["", f"## {message('risk.review_gaps')}", ""])
    if gaps:
        rows.extend(f"- {gap}" for gap in gaps)
    else:
        rows.append(f"- {message('risk.no_structural_gaps')}")
    return "\n".join(rows) + "\n"


def render_dependency_map(entries: list[dict[str, str]], generated_at: str, message) -> str:
    rows = [
        f"# {message('risk.title.dependencies')}",
        "",
        f"{message('common.generated')}: {generated_at}",
        "",
        "| " + " | ".join(message(key) for key in ["risk.source_workstream", "risk.relationship", "risk.target_reference", "common.owner", "common.status", "risk.next_action"]) + " |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if entries:
        for entry in entries:
            rows.append(
                "| "
                + " | ".join(
                    [
                        md(entry["source"]),
                        md(entry["relationship"]),
                        md(entry["target"]),
                        md(entry["owner"]),
                        md(entry["status"]),
                        md(entry["next_action"]),
                    ],
                )
                + " |",
            )
    else:
        rows.append(f"| TBD | gap | {message('risk.no_dependencies')} | TBD | open | {message('risk.confirm_dependencies')} |")
    return "\n".join(rows) + "\n"


def render_packet(args: argparse.Namespace, generated_at: str, message) -> str:
    title = args.packet_title or args.packet_question or message("risk.business_decision")
    options = args.packet_option or ["TBD"]
    impacts = args.packet_impact or ["TBD"]
    workstreams = args.packet_workstream or ["TBD"]
    return "\n".join(
        [
            f"# {title}",
            "",
            f"{message('common.generated')}: {generated_at}",
            "",
            f"## {message('risk.background')}",
            "",
            args.packet_background or "TBD",
            "",
            f"## {message('risk.decision_needed')}",
            "",
            args.packet_question or "TBD",
            "",
            f"## {message('risk.options')}",
            "",
            *[f"- {item}" for item in options],
            "",
            f"## {message('risk.impacts')}",
            "",
            *[f"- {item}" for item in impacts],
            "",
            f"## {message('risk.recommendation')}",
            "",
            args.packet_recommendation or "TBD",
            "",
            f"## {message('risk.deadline_trigger')}",
            "",
            args.packet_deadline or "TBD",
            "",
            f"## {message('risk.affected_workstreams')}",
            "",
            *[f"- {item}" for item in workstreams],
            "",
            f"## {message('risk.requested_owner')}",
            "",
            args.packet_owner or "TBD",
            "",
            f"## {message('risk.closure_rule')}",
            "",
            message("risk.closure_instruction"),
            "",
        ],
    )


def md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def write_text(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find available filename for {path}")


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        emit({"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}, args.output)
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    if not memory_root.exists():
        emit({"ok": False, "error": "ADP memory root does not exist", "memory_root": str(memory_root)}, args.output)
        return 2
    config_module = load_module(Path(args.config_script), "adp_risk_effective_config")
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        emit({"ok": False, "error": config.get("error", "shared ADP effective config could not be resolved")}, args.output)
        return 2
    locale = str(config.get("document_locale") or "en")

    def message(key: str) -> str:
        return config_module.message(key, locale)

    if args.relation_updates_file:
        incompatible = any(
            [
                args.workstream,
                args.packet_title,
                args.packet_question,
                args.packet_background,
                args.packet_option,
                args.packet_impact,
                args.packet_recommendation,
                args.packet_deadline,
                args.packet_owner,
                args.packet_workstream,
            ]
        )
        if incompatible:
            emit({"ok": False, "error": "relation update mode cannot be combined with review selection or packet fields"}, args.output)
            return 2
        try:
            result = run_relation_updates(
                args,
                project_root,
                memory_root,
                message,
                language_metadata(config, locale),
            )
        except Exception as exc:
            emit({"ok": False, "mode": "risk-relation-update", "error": str(exc)}, args.output)
            return 2
        emit(result, args.output)
        return 0
    if args.apply_relations or args.verified_plan_token:
        emit({"ok": False, "error": "--apply-relations and --verified-plan-token require --relation-updates-file"}, args.output)
        return 2

    records, missing = discover_records(memory_root, args.workstream)
    workstreams = [parse_workstream(path) for path in records]
    current_revision = baseline_revision(memory_root)
    risks, gaps = risk_entries(workstreams, current_revision, memory_root)
    dependencies = dependency_entries(workstreams)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    risk_path = memory_root / "views" / "risk-matrix.md"
    dependency_path = memory_root / "views" / "dependency-map.md"
    risk_flow_path = memory_root / "views" / "risk-flow.json"
    write_text(risk_path, render_risk_matrix(risks, gaps, generated_at, message), args.dry_run)
    write_text(dependency_path, render_dependency_map(dependencies, generated_at, message), args.dry_run)
    write_text(risk_flow_path, json.dumps(risk_flow_contract(risks), ensure_ascii=False, indent=2, sort_keys=True) + "\n", args.dry_run)

    packet_path = None
    if args.packet_title or args.packet_question:
        title = args.packet_title or args.packet_question
        packet_name = f"{generated_at[:10]}-{slugify(title)}.md"
        packet = unique_path(memory_root / "decisions" / "business-decision-packets" / packet_name)
        write_text(packet, render_packet(args, generated_at, message), args.dry_run)
        packet_path = str(packet)

    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "workstreams_scanned": [ws.workstream_id for ws in workstreams],
        "missing_workstreams": missing,
        "risk_matrix_path": str(risk_path),
        "dependency_map_path": str(dependency_path),
        "risk_flow_path": str(risk_flow_path),
        "baseline_revision": current_revision,
        "business_decision_packet_path": packet_path,
        "counts": {
            "risk_entries": len(risks),
            "dependency_entries": len(dependencies),
            "review_gaps": len(gaps),
        },
        "review_gaps": gaps,
        "next_actions": [
            message("risk.next.review_matrix"),
            message("risk.next.review_dependencies"),
            message("risk.next.create_packets"),
        ],
        "language": language_metadata(config, locale),
    }
    emit(result, args.output)
    return 0


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path.resolve())
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load shared ADP config module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def language_metadata(config: dict[str, Any], locale: str) -> dict[str, Any]:
    return {
        "locale": locale,
        "document_output_language": config.get("values", {}).get("document_output_language", "English"),
        "fallback": "document_output_language" in config.get("fallbacks", []),
        "warnings": config.get("warnings", []),
    }


def emit(result: dict, output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
