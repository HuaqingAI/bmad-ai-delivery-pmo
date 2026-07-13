#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Sync L0 reference implications into ADP memory and scan WDR gaps."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"


L0_FILES = [
    "reference-index.md",
    "extracted-freeze-model.md",
    "extracted-contract-inventory.md",
    "extracted-gates.md",
    "extracted-nfr.md",
    "extracted-evidence-rules.md",
    "extracted-impacts.md",
    "extracted-decision-gates.md",
    "exceptions-and-open-questions.md",
]

SECTION_PRIMARY_FIELDS = {
    "source_artifacts": "artifact",
    "freeze_windows": "window",
    "contracts": "contract",
    "gates": "gate",
    "nfrs": "nfr",
    "evidence_rules": "evidence_type",
    "decision_gates": "gate",
    "impacts": "constraint",
    "exceptions_open_questions": "item",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Write ADP l0/*.md summaries from a JSON sync plan and report workstream "
            "records that do not acknowledge applicable L0 references."
        )
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--plan", help="JSON sync plan with extracted L0 implications.")
    parser.add_argument(
        "--source-artifact",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="Source artifact to register when no plan is needed. Repeatable.",
    )
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--workstream", action="append", default=[], help="Limit gap scan to a workstream id.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without creating files.")
    parser.add_argument("--language", help="Override document_output_language for derived L0 views.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def load_plan(path: str | None, source_artifacts: list[str]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    provided_sections: set[str] = set()
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"plan is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("plan root must be a JSON object")
        plan = normalize_plan(data)
        provided_sections = sections_with_content(plan)
    else:
        plan = normalize_plan({})

    for item in source_artifacts:
        if "=" not in item:
            warnings.append(f"ignored source artifact without LABEL=PATH format: {item}")
            continue
        label, value = item.split("=", 1)
        label = label.strip()
        value = value.strip()
        if not label or not value:
            warnings.append(f"ignored incomplete source artifact: {item}")
            continue
        plan["source_artifacts"].append(
            {
                "artifact": label,
                "path": value,
                "baseline_status": "unknown",
                "owner": "TBD",
                "version": "TBD",
                "notes": "Registered from CLI",
            }
        )
        provided_sections.add("source_artifacts")
    if not provided_sections:
        raise ValueError("provide at least one non-empty L0 sync section or --source-artifact")
    plan["_provided_sections"] = provided_sections
    return plan, warnings


def normalize_plan(data: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "source_artifacts",
        "freeze_windows",
        "rules",
        "contracts",
        "gates",
        "nfrs",
        "evidence_rules",
        "decision_gates",
        "impacts",
        "exceptions_open_questions",
    ]
    plan = {key: list_value(data.get(key, []), key) for key in keys}
    validate_plan_rows(plan)
    plan["generated_at"] = str(data.get("generated_at") or now())
    return plan


def validate_plan_rows(plan: dict[str, Any]) -> None:
    for section, primary_field in SECTION_PRIMARY_FIELDS.items():
        for index, row in enumerate(plan[section], start=1):
            if not isinstance(row, dict):
                raise ValueError(f"{section}[{index}] must be an object")
            if as_text(row.get(primary_field), "") == "":
                raise ValueError(f"{section}[{index}] missing required field: {primary_field}")


def sections_with_content(plan: dict[str, Any]) -> set[str]:
    sections = {
        "source_artifacts",
        "freeze_windows",
        "rules",
        "contracts",
        "gates",
        "nfrs",
        "evidence_rules",
        "decision_gates",
        "impacts",
        "exceptions_open_questions",
    }
    return {section for section in sections if plan.get(section)}


def list_value(value: Any, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError(f"{key} must be a list")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def as_text(value: Any, default: str = "TBD") -> str:
    if value is None:
        return default
    if isinstance(value, list):
        return ", ".join(as_text(item, default) for item in value) or default
    text = str(value).strip()
    return text if text else default


def md_escape(value: Any, default: str = "TBD") -> str:
    return as_text(value, default).replace("|", "\\|").replace("\n", " ")


def bullet_list(items: Iterable[Any]) -> str:
    values = [as_text(item) for item in items if as_text(item) != "TBD"]
    return "\n".join(f"- {item}" for item in values) if values else "- TBD"


def table_rows(items: list[Any], fields: list[tuple[str, str]], empty_row: list[str]) -> str:
    if not items:
        return "| " + " | ".join(empty_row) + " |"
    rows = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        rows.append("| " + " | ".join(md_escape(raw.get(key), fallback) for key, fallback in fields) + " |")
    return "\n".join(rows) if rows else "| " + " | ".join(empty_row) + " |"


def render_reference_index(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["source_artifacts"],
        [
            ("artifact", "TBD"),
            ("path", "TBD"),
            ("baseline_status", "unknown"),
            ("owner", "TBD"),
            ("version", "TBD"),
            ("notes", "TBD"),
        ],
        ["TBD", "TBD", "unknown", "TBD", "TBD", "TBD"],
    )
    return f"""# L0 Reference Index

L0 is a BMM-managed workstream and reference baseline. ADP records source paths and extracted project-level implications; it does not own or rewrite L0 governance.

Last synced: {generated_at}

## Source Artifacts

| Artifact | Path / Link | Baseline Status | Owner | Version | Notes |
| --- | --- | --- | --- | --- | --- |
{rows}

## Extracted Summaries

- Freeze model: `extracted-freeze-model.md`
- Contract inventory: `extracted-contract-inventory.md`
- Gates: `extracted-gates.md`
- NFR: `extracted-nfr.md`
- Evidence rules: `extracted-evidence-rules.md`
- Cross-workstream impacts: `extracted-impacts.md`
- Decision gates: `extracted-decision-gates.md`
- Exceptions and open questions: `exceptions-and-open-questions.md`

## ADP Question

The ADP question is not whether L0 is good. It is: which L0 constraints can workstreams reference, which workstreams are affected, which WDR/readiness files lack references or evidence, and which questions must return to L0 or a business decision process?
"""


def render_freeze_model(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["freeze_windows"],
        [
            ("window", "TBD"),
            ("scope", "TBD"),
            ("start", "TBD"),
            ("end", "TBD"),
            ("owner", "TBD"),
            ("affected_workstreams", "TBD"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "TBD", "TBD"],
    )
    return f"""# Extracted Freeze Model

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

## Freeze Windows

| Window | Scope | Start | End | Owner | Affected Workstreams |
| --- | --- | --- | --- | --- | --- |
{rows}

## Rules

{bullet_list(plan["rules"])}

## Open Questions

- See `exceptions-and-open-questions.md`
"""


def render_contract_inventory(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["contracts"],
        [
            ("contract", "TBD"),
            ("owner", "TBD"),
            ("consumers", "TBD"),
            ("stability", "TBD"),
            ("evidence_required", "TBD"),
            ("notes", "TBD"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "TBD", "TBD"],
    )
    return f"""# Extracted Contract Inventory

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

| Contract / Interface | Owner | Consumers | Stability | Evidence Required | Notes |
| --- | --- | --- | --- | --- | --- |
{rows}

## Open Questions

- See `exceptions-and-open-questions.md`
"""


def render_gates(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["gates"],
        [
            ("gate", "TBD"),
            ("meaning", "TBD"),
            ("required_evidence", "TBD"),
            ("owner", "TBD"),
            ("affected_workstreams", "TBD"),
            ("status", "unknown"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "TBD", "unknown"],
    )
    return f"""# Extracted Gates

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

| Gate | Meaning | Required Evidence | Owner | Affected Workstreams | Status |
| --- | --- | --- | --- | --- | --- |
{rows}

## Gate Gaps

- See `extracted-impacts.md` and `exceptions-and-open-questions.md`
"""


def render_nfr(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["nfrs"],
        [
            ("nfr", "TBD"),
            ("threshold", "TBD"),
            ("primary_owner", "TBD"),
            ("evidence_owner", "TBD"),
            ("gate_impact", "TBD"),
            ("affected_workstreams", "TBD"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "TBD", "TBD"],
    )
    return f"""# Extracted NFR

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

| NFR | Threshold | Primary Owner | Evidence Owner | Gate Impact | Affected Workstreams |
| --- | --- | --- | --- | --- | --- |
{rows}

## NFR Gaps

- See `extracted-impacts.md` and `exceptions-and-open-questions.md`
"""


def render_evidence_rules(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["evidence_rules"],
        [
            ("evidence_type", "TBD"),
            ("required_for", "TBD"),
            ("accepted_form", "TBD"),
            ("owner", "TBD"),
            ("notes", "TBD"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "TBD"],
    )
    return f"""# Extracted Evidence Rules

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

| Evidence Type | Required For | Accepted Form | Owner | Notes |
| --- | --- | --- | --- | --- |
{rows}

## Evidence Gaps

- See `extracted-impacts.md` and downstream WDR gap suggestions from the sync run.
"""


def render_impacts(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["impacts"],
        [
            ("constraint", "TBD"),
            ("affected_workstream", "TBD"),
            ("impact", "TBD"),
            ("required_action", "TBD"),
            ("owner", "TBD"),
            ("status", "unknown"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "TBD", "unknown"],
    )
    return f"""# Extracted L0 Impacts

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

| L0 Constraint | Affected Workstream | Impact | Required Action | Owner | Status |
| --- | --- | --- | --- | --- | --- |
{rows}

## Unmapped Impacts

- See `exceptions-and-open-questions.md`
"""


def render_decision_gates(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["decision_gates"],
        [
            ("gate", "TBD"),
            ("decision", "TBD"),
            ("owner", "TBD"),
            ("affected_workstreams", "TBD"),
            ("status", "open"),
            ("next_action", "TBD"),
        ],
        ["TBD", "TBD", "TBD", "TBD", "open", "TBD"],
    )
    return f"""# Extracted Decision Gates

Source artifact(s): {source_names(plan)}
Last extracted: {generated_at}

| Gate | Decision Needed | Owner | Affected Workstreams | Status | Next Action |
| --- | --- | --- | --- | --- | --- |
{rows}

## Business Decision Routing

- Items an FDE cannot decide alone should become Business Decision Packets.
"""


def render_exceptions(plan: dict[str, Any], generated_at: str) -> str:
    rows = table_rows(
        plan["exceptions_open_questions"],
        [
            ("date", generated_at.split("T")[0]),
            ("item", "TBD"),
            ("type", "question"),
            ("owner", "TBD"),
            ("affected_workstreams", "TBD"),
            ("status", "open"),
            ("next_action", "TBD"),
        ],
        ["TBD", "TBD", "exception/question", "TBD", "TBD", "open", "TBD"],
    )
    return f"""# L0 Exceptions and Open Questions

Last extracted: {generated_at}

| Date | Item | Type | Owner | Affected Workstreams | Status | Next Action |
| --- | --- | --- | --- | --- | --- | --- |
{rows}
"""


def source_names(plan: dict[str, Any]) -> str:
    names = []
    for item in plan["source_artifacts"]:
        if isinstance(item, dict):
            names.append(as_text(item.get("artifact")))
    return ", ".join(name for name in names if name != "TBD") or "TBD"


def render_files(plan: dict[str, Any], generated_at: str) -> dict[str, str]:
    all_rendered = {
        "reference-index.md": render_reference_index(plan, generated_at),
        "extracted-freeze-model.md": render_freeze_model(plan, generated_at),
        "extracted-contract-inventory.md": render_contract_inventory(plan, generated_at),
        "extracted-gates.md": render_gates(plan, generated_at),
        "extracted-nfr.md": render_nfr(plan, generated_at),
        "extracted-evidence-rules.md": render_evidence_rules(plan, generated_at),
        "extracted-impacts.md": render_impacts(plan, generated_at),
        "extracted-decision-gates.md": render_decision_gates(plan, generated_at),
        "exceptions-and-open-questions.md": render_exceptions(plan, generated_at),
    }
    target_files = files_for_sections(plan["_provided_sections"])
    return {filename: all_rendered[filename] for filename in target_files}


L0_SYSTEM_COPY = {
    "# L0 Reference Index": "l0.title.reference_index",
    "# Extracted Freeze Model": "l0.title.freeze_model",
    "# Extracted Contract Inventory": "l0.title.contract_inventory",
    "# Extracted Gates": "l0.title.gates",
    "# Extracted NFR": "l0.title.nfr",
    "# Extracted Evidence Rules": "l0.title.evidence_rules",
    "# Extracted L0 Impacts": "l0.title.impacts",
    "# Extracted Decision Gates": "l0.title.decision_gates",
    "# L0 Exceptions and Open Questions": "l0.title.exceptions",
    "## Source Artifacts": "l0.section.source_artifacts",
    "## Extracted Summaries": "l0.section.extracted_summaries",
    "## ADP Question": "l0.section.adp_question",
    "## Freeze Windows": "l0.section.freeze_windows",
    "## Rules": "l0.section.rules",
    "## Open Questions": "l0.section.open_questions",
    "## Gate Gaps": "l0.section.gate_gaps",
    "## NFR Gaps": "l0.section.nfr_gaps",
    "## Evidence Gaps": "l0.section.evidence_gaps",
    "## Unmapped Impacts": "l0.section.unmapped_impacts",
    "## Business Decision Routing": "l0.section.business_routing",
    "| Artifact | Path / Link | Baseline Status | Owner | Version | Notes |": "l0.table.reference_index",
    "| Window | Scope | Start | End | Owner | Affected Workstreams |": "l0.table.freeze_windows",
    "| Contract / Interface | Owner | Consumers | Stability | Evidence Required | Notes |": "l0.table.contracts",
    "| Gate | Meaning | Required Evidence | Owner | Affected Workstreams | Status |": "l0.table.gates",
    "| NFR | Threshold | Primary Owner | Evidence Owner | Gate Impact | Affected Workstreams |": "l0.table.nfr",
    "| Evidence Type | Required For | Accepted Form | Owner | Notes |": "l0.table.evidence_rules",
    "| L0 Constraint | Affected Workstream | Impact | Required Action | Owner | Status |": "l0.table.impacts",
    "| Gate | Decision Needed | Owner | Affected Workstreams | Status | Next Action |": "l0.table.decision_gates",
    "| Date | Item | Type | Owner | Affected Workstreams | Status | Next Action |": "l0.table.exceptions",
    "L0 is a BMM-managed workstream and reference baseline. ADP records source paths and extracted project-level implications; it does not own or rewrite L0 governance.": "l0.note.ownership",
    "The ADP question is not whether L0 is good. It is: which L0 constraints can workstreams reference, which workstreams are affected, which WDR/readiness files lack references or evidence, and which questions must return to L0 or a business decision process?": "l0.note.adp_question",
    "- See `exceptions-and-open-questions.md`": "l0.note.see_exceptions",
    "- See `extracted-impacts.md` and `exceptions-and-open-questions.md`": "l0.note.see_impacts_exceptions",
    "- See `extracted-impacts.md` and downstream WDR gap suggestions from the sync run.": "l0.note.see_evidence_gaps",
    "- Items an FDE cannot decide alone should become Business Decision Packets.": "l0.note.business_routing",
}

L0_SYSTEM_PREFIXES = {
    "Source artifact(s)": "l0.label.source_artifacts",
    "Last extracted": "l0.label.last_extracted",
    "Last synced": "l0.label.last_synced",
}


def localize_rendered_files(rendered: dict[str, str], locale: str, config_module) -> dict[str, str]:
    if locale == "en":
        return rendered
    localized: dict[str, str] = {}
    for name, content in rendered.items():
        lines: list[str] = []
        for line in content.splitlines():
            key = L0_SYSTEM_COPY.get(line)
            if key:
                lines.append(config_module.message(key, locale))
            else:
                replaced = False
                for prefix, prefix_key in L0_SYSTEM_PREFIXES.items():
                    marker = prefix + ": "
                    if line.startswith(marker):
                        lines.append(f"{config_module.message(prefix_key, locale)}: {line[len(marker):]}")
                        replaced = True
                        break
                if not replaced:
                    lines.append(line)
        localized[name] = "\n".join(lines).rstrip() + "\n"
    return localized


def files_for_sections(sections: set[str]) -> list[str]:
    mapping = {
        "source_artifacts": "reference-index.md",
        "freeze_windows": "extracted-freeze-model.md",
        "rules": "extracted-freeze-model.md",
        "contracts": "extracted-contract-inventory.md",
        "gates": "extracted-gates.md",
        "nfrs": "extracted-nfr.md",
        "evidence_rules": "extracted-evidence-rules.md",
        "impacts": "extracted-impacts.md",
        "decision_gates": "extracted-decision-gates.md",
        "exceptions_open_questions": "exceptions-and-open-questions.md",
    }
    files = []
    for section in sections:
        filename = mapping.get(section)
        if filename and filename not in files:
            files.append(filename)
    return files


def write_outputs(l0_root: Path, rendered: dict[str, str], dry_run: bool) -> tuple[list[str], list[str], list[str]]:
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    if not dry_run:
        l0_root.mkdir(parents=True, exist_ok=True)
    for filename in rendered:
        target = l0_root / filename
        content = rendered[filename]
        if target.exists():
            old = target.read_text(encoding="utf-8")
            if old == content:
                unchanged.append(str(target))
                continue
            if not dry_run:
                target.write_text(content, encoding="utf-8", newline="\n")
            updated.append(str(target))
        else:
            if not dry_run:
                target.write_text(content, encoding="utf-8", newline="\n")
            created.append(str(target))
    return created, updated, unchanged


def constraints_for_scan(plan: dict[str, Any]) -> list[dict[str, str]]:
    constraints: list[dict[str, str]] = []
    for item in plan["contracts"]:
        if isinstance(item, dict):
            constraints.append(
                {
                    "kind": "contract",
                    "name": as_text(item.get("contract")),
                    "affected": as_text(item.get("consumers"), "all"),
                }
            )
    for item in plan["gates"]:
        if isinstance(item, dict):
            constraints.append(
                {
                    "kind": "gate",
                    "name": as_text(item.get("gate")),
                    "affected": as_text(item.get("affected_workstreams"), "all"),
                }
            )
    for item in plan["nfrs"]:
        if isinstance(item, dict):
            constraints.append(
                {
                    "kind": "nfr",
                    "name": as_text(item.get("nfr")),
                    "affected": as_text(item.get("affected_workstreams"), "all"),
                }
            )
    for item in plan["evidence_rules"]:
        if isinstance(item, dict):
            name = as_text(item.get("evidence_type"))
            required_for = as_text(item.get("required_for"), "")
            constraints.append({"kind": "evidence", "name": name, "affected": required_for or "all"})
    for item in plan["impacts"]:
        if isinstance(item, dict):
            constraints.append(
                {
                    "kind": "impact",
                    "name": as_text(item.get("constraint")),
                    "affected": as_text(item.get("affected_workstream"), "all"),
                }
            )
    return [constraint for constraint in constraints if constraint["name"] != "TBD"]


def scan_workstream_gaps(memory_root: Path, plan: dict[str, Any], selected_ids: list[str]) -> list[dict[str, str]]:
    workstreams_root = memory_root / "workstreams"
    if not workstreams_root.exists():
        return []

    selected = {normalize_id(item) for item in selected_ids}
    constraints = constraints_for_scan(plan)
    gaps: list[dict[str, str]] = []

    for record in sorted(workstreams_root.glob("*/delivery-record.md")):
        workstream_id = record.parent.name
        if selected and workstream_id not in selected:
            continue
        text = record.read_text(encoding="utf-8")
        lower = text.lower()
        normalized_lower = lower.replace("\r\n", "\n")
        l0_section_gap = "l0 references:" in normalized_lower and "l0 references:\n\n- tbd" in normalized_lower
        if l0_section_gap:
            gaps.append(
                {
                    "workstream_id": workstream_id,
                    "record": str(record),
                    "kind": "l0-reference",
                    "constraint": "L0 references",
                    "suggestion": "Replace TBD L0 references with applicable contracts, gates, NFRs, or evidence rules.",
                }
            )
        for constraint in constraints:
            if not applies_to_workstream(constraint["affected"], workstream_id, lower):
                continue
            name = constraint["name"]
            if name.lower() not in lower:
                gaps.append(
                    {
                        "workstream_id": workstream_id,
                        "record": str(record),
                        "kind": constraint["kind"],
                        "constraint": name,
                        "suggestion": f"Add or confirm L0 {constraint['kind']} reference: {name}.",
                    }
                )
    return gaps


def applies_to_workstream(affected: str, workstream_id: str, record_lower: str) -> bool:
    value = affected.strip().lower()
    if not value or value in {"all", "*", "tbd"}:
        return True
    tokens = {normalize_id(part) for part in value.replace(";", ",").split(",") if part.strip()}
    if workstream_id in tokens:
        return True
    return any(token and token in record_lower for token in tokens)


def normalize_id(raw: str) -> str:
    normalized = []
    previous_dash = False
    for char in raw.strip().lower():
        if char.isalnum():
            normalized.append(char)
            previous_dash = False
        elif not previous_dash:
            normalized.append("-")
            previous_dash = True
    return "".join(normalized).strip("-")


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        emit({"ok": False, "error": "project_root is not an existing directory", "project_root": str(project_root)}, args.output)
        return 2

    config_module = load_module(Path(args.config_script), "adp_l0_effective_config")
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        emit({"ok": False, "error": config.get("error", "shared ADP effective config could not be resolved")}, args.output)
        return 2
    locale = str(config.get("document_locale") or "en")

    try:
        plan, warnings = load_plan(args.plan, args.source_artifact)
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)}, args.output)
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    l0_root = memory_root / "l0"
    generated_at = plan["generated_at"]
    rendered = localize_rendered_files(render_files(plan, generated_at), locale, config_module)

    if args.verbose:
        print(f"Using memory root: {memory_root}", file=sys.stderr)
        print(f"Using l0 root: {l0_root}", file=sys.stderr)

    files_created, files_updated, files_unchanged = write_outputs(l0_root, rendered, args.dry_run)
    gaps = scan_workstream_gaps(memory_root, plan, args.workstream)
    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "l0_root": str(l0_root),
        "source_artifacts_indexed": len(plan["source_artifacts"]),
        "files_created": files_created,
        "files_updated": files_updated,
        "files_unchanged": files_unchanged,
        "workstream_gap_suggestions": gaps,
        "warnings": warnings,
        "next_actions": next_actions(gaps, plan, lambda key: config_module.message(key, locale)),
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


def next_actions(gaps: list[dict[str, str]], plan: dict[str, Any], message) -> list[str]:
    actions = []
    if gaps:
        actions.append(message("l0.next.confirm_references"))
    if plan["exceptions_open_questions"] or plan["decision_gates"]:
        actions.append(message("l0.next.route_questions"))
    actions.append(message("l0.next.readiness"))
    return actions


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
