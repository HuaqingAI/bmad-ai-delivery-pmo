#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Extract deterministic checkpoint candidate facts from BMM and TEA artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import argparse
import sys
from pathlib import Path
from typing import Any

from checkpoint_registry import compute_candidate_id, normalize_id


CHECKPOINTS = {"prd", "architecture", "epic-story", "implementation", "validation", "baseline"}
SOURCE_GLOBS = {
    "prd": [
        "_bmad-output/planning-artifacts/**/SPEC.md",
        "_bmad-output/planning-artifacts/**/prd.md",
        "_bmad-output/planning-artifacts/**/brief.md",
        "_bmad-output/planning-artifacts/**/prfaq-*.md",
    ],
    "architecture": [
        "_bmad-output/planning-artifacts/**/ARCHITECTURE-SPINE.md",
        "_bmad-output/planning-artifacts/**/*architecture*.md",
    ],
    "epic-story": [
        "_bmad-output/planning-artifacts/**/epics.md",
        "_bmad-output/implementation-artifacts/**/*story*.md",
        "_bmad-output/implementation-artifacts/**/sprint-status.yaml",
    ],
    "implementation": [
        "_bmad-output/implementation-artifacts/**/*story*.md",
        "_bmad-output/implementation-artifacts/**/deferred-work.md",
        "_bmad-output/implementation-artifacts/**/sprint-status.yaml",
    ],
    "validation": [
        "_bmad-output/test-artifacts/**/gate-decision.json",
        "_bmad-output/test-artifacts/**/e2e-trace-summary.json",
        "_bmad-output/test-artifacts/**/traceability-matrix.md",
        "_bmad-output/test-artifacts/**/nfr-assessment.md",
        "_bmad-output/test-artifacts/**/test-review.md",
    ],
    "baseline": [
        "_bmad-output/planning-artifacts/**/*.md",
        "_bmad-output/implementation-artifacts/**/*.md",
        "_bmad-output/test-artifacts/**/*.md",
        "_bmad-output/test-artifacts/**/*.json",
    ],
}


def discover_candidate(
    project_root: Path,
    workstream_id: str,
    checkpoint: str,
    artifact_args: list[str],
    *,
    summary: str = "",
    asserted_by: str = "",
    authority_scope: list[str] | None = None,
    affected_workstreams: list[str] | None = None,
    required_confirmers: list[str] | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"unsupported checkpoint: {checkpoint}")
    project_root = project_root.resolve()
    normalized_workstream = normalize_id(workstream_id)
    artifact, warnings = select_artifact(project_root, checkpoint, artifact_args)
    if not artifact["exists"]:
        raise ValueError(f"artifact not found: {artifact['path']}")

    artifact_path = Path(artifact["path"])
    text = read_text(artifact_path)
    claims = extract_claims(checkpoint, artifact["kind"], artifact_path, text, summary)
    source_prepass = build_source_prepass(checkpoint, artifact["kind"], artifact_path, text)
    source_revision = compute_source_revision(checkpoint, artifact["kind"], artifact_path, text)
    source_scope_key = f"{artifact['kind']}:{portable_path(artifact_path)}"
    candidate_id = compute_candidate_id(normalized_workstream, checkpoint, source_scope_key, source_revision, claims)
    authority = build_authority(
        normalized_workstream,
        asserted_by=asserted_by,
        authority_scope=authority_scope or [],
        affected_workstreams=affected_workstreams or [],
        required_confirmers=required_confirmers or [],
    )
    candidate = {
        "candidate_id": candidate_id,
        "status": "discovered",
        "workstream_id": normalized_workstream,
        "checkpoint": checkpoint,
        "artifact": {
            "kind": artifact["kind"],
            "path": portable_path(artifact_path),
            "status": artifact_status(text),
            "source_scope_key": source_scope_key,
            "source_revision": source_revision,
        },
        "claims": claims,
        "source_prepass": source_prepass,
        "authority": authority,
        "source_refs": source_refs(artifact_path),
    }
    return candidate, render_preview(candidate), warnings


def select_artifact(project_root: Path, checkpoint: str, artifact_args: list[str]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if artifact_args:
        for raw in artifact_args:
            key, raw_path = parse_artifact_arg(raw)
            path = resolve_artifact_path(project_root, raw_path)
            kind = infer_kind(key, path, checkpoint)
            if path.exists():
                return {"kind": kind, "path": str(path), "exists": True}, warnings
            warnings.append(f"ignored missing artifact: {raw_path}")
        key, raw_path = parse_artifact_arg(artifact_args[0])
        path = resolve_artifact_path(project_root, raw_path)
        return {"kind": infer_kind(key, path, checkpoint), "path": str(path), "exists": False}, warnings

    for pattern in SOURCE_GLOBS.get(checkpoint, []):
        matches = sorted(project_root.glob(pattern))
        for path in matches:
            if path.is_file():
                return {"kind": infer_kind("", path, checkpoint), "path": str(path), "exists": True}, warnings
    return {"kind": checkpoint, "path": str(project_root / SOURCE_GLOBS.get(checkpoint, [checkpoint])[0]), "exists": False}, warnings


def parse_artifact_arg(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if "=" not in text:
        return "", text
    key, value = text.split("=", 1)
    return key.strip().lower(), value.strip()


def resolve_artifact_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def infer_kind(key: str, path: Path, checkpoint: str) -> str:
    if key:
        return key
    name = path.name.lower()
    if name == "spec.md":
        return "spec"
    if name == "prd.md" or "prd" in name:
        return "prd"
    if "architecture" in name or name == "architecture-spine.md":
        return "architecture"
    if "epic" in name:
        return "epics"
    if "story" in name:
        return "story"
    if name == "gate-decision.json":
        return "gate"
    if name == "e2e-trace-summary.json" or "trace" in name:
        return "trace"
    if "nfr" in name:
        return "nfr"
    if "test" in name or "validation" in name:
        return "validation"
    return checkpoint


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def portable_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def artifact_status(text: str) -> str:
    frontmatter = parse_frontmatter(text)
    status = frontmatter.get("status") or frontmatter.get("state")
    if status:
        return str(status).strip()
    match = re.search(r"(?im)^\s*-\s*Status\s*:\s*(.+)$", text)
    return match.group(1).strip() if match else "unknown"


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip().strip('"')
    return data


def compute_source_revision(checkpoint: str, kind: str, path: Path, text: str) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    memlog = path.parent / ".memlog.md"
    if kind in {"prd", "spec", "architecture", "brief"} and memlog.exists():
        digest.update(memlog.read_bytes())
    distillate = path.parent / "distillate.md"
    if kind == "prfaq" and distillate.exists():
        digest.update(distillate.read_bytes())
    if kind == "story":
        digest.update(extract_story_revision_bits(text).encode("utf-8"))
    if path.name == "sprint-status.yaml":
        digest.update(extract_yaml_value(text, "last_updated").encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def extract_story_revision_bits(text: str) -> str:
    baseline = extract_yaml_value(text, "baseline_commit")
    status = artifact_status(text)
    return f"baseline_commit={baseline};status={status}"


def extract_yaml_value(text: str, key: str) -> str:
    pattern = re.compile(rf"(?im)^\s*{re.escape(key)}\s*:\s*(.+?)\s*$")
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def extract_claims(checkpoint: str, kind: str, path: Path, text: str, summary: str) -> dict[str, Any]:
    claims = empty_claims(summary or fallback_summary(checkpoint, path))
    return normalize_claims(claims)


def empty_claims(summary: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "scope": {
            "in": [],
            "out": [],
            "assumptions": [],
            "non_goals": [],
        },
        "acceptance": {
            "criteria": [],
            "owner": "",
            "evidence_required": [],
            "success_metrics": [],
        },
        "decisions": [],
        "open_questions": [],
        "dependencies": [],
        "impacts": [],
        "risks": [],
        "business_confirmation": [],
        "readiness_gaps": [],
        "next_actions": [],
        "actions": [],
        "evidence": [],
    }


def default_claims(summary: str = "") -> dict[str, Any]:
    return empty_claims(summary)


def fallback_summary(checkpoint: str, path: Path) -> str:
    return f"{checkpoint} checkpoint facts discovered from {path.name}"


def build_source_prepass(checkpoint: str, kind: str, path: Path, text: str) -> dict[str, Any]:
    prepass: dict[str, Any] = {
        "checkpoint": checkpoint,
        "kind": kind,
        "path": portable_path(path),
        "frontmatter": parse_frontmatter(text),
        "status": artifact_status(text),
        "companion_sources": companion_sources(path),
    }
    if path.suffix.lower() == ".json":
        prepass["json"] = json_prepass(text)
    else:
        prepass["sections"] = markdown_section_facts(text)
    return prepass


def companion_sources(path: Path) -> list[str]:
    refs: list[str] = []
    for name in [".memlog.md", "distillate.md"]:
        companion = path.parent / name
        if companion.exists():
            refs.append(portable_path(companion))
    return refs


def json_prepass(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "valid": False,
            "error": exc.msg,
            "line": exc.lineno,
            "column": exc.colno,
            "fields": [],
        }
    return {
        "valid": True,
        "root_type": type(payload).__name__,
        "fields": json_fields(payload, text),
    }


def json_fields(value: Any, source_text: str, path: str = "$") -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            fields.extend(json_fields(child, source_text, child_path))
        return fields
    if isinstance(value, list):
        for index, child in enumerate(value):
            fields.extend(json_fields(child, source_text, f"{path}[{index}]"))
        return fields
    fields.append({"path": path, "value": compact_value(value), "line": json_field_line(source_text, path)})
    return fields


def json_field_line(source_text: str, path: str) -> int:
    key_match = re.search(r"\.([A-Za-z0-9_-]+)(?:\[\d+\])?$", path)
    if not key_match:
        return 1
    key = key_match.group(1)
    match = re.search(rf'"{re.escape(key)}"\s*:', source_text)
    return source_text.count("\n", 0, match.start()) + 1 if match else 1


def markdown_section_facts(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip().strip("#").strip()
        end = len(text)
        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= level:
                end = next_match.start()
                break
        raw_body = text[match.end() : end]
        first_body_index = match.end() + len(raw_body) - len(raw_body.lstrip("\n"))
        body = raw_body.strip("\n")
        body_start_line = line_number_at(text, first_body_index)
        sections.append(
            {
                "level": level,
                "heading": title,
                "line": line_number_at(text, match.start()),
                "items": markdown_items(body, body_start_line),
                "tables": markdown_tables(body, body_start_line),
            }
        )
    return sections


def line_number_at(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def markdown_items(body: str, base_line: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for offset, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|"):
            continue
        match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", stripped)
        if match:
            items.append({"line": base_line + offset, "text": clean_text(match.group(1))})
        elif len(stripped) <= 220 and not stripped.startswith("#"):
            items.append({"line": base_line + offset, "text": clean_text(stripped)})
    return items


def markdown_tables(body: str, base_line: int) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    current: list[tuple[int, list[str]]] = []

    def flush() -> None:
        nonlocal current
        rows = [(line_no, cells) for line_no, cells in current if not table_separator(cells)]
        if rows:
            headers = rows[0][1]
            data_rows = [{"line": line_no, "cells": cells} for line_no, cells in rows[1:]]
            tables.append({"line": rows[0][0], "headers": headers, "rows": data_rows})
        current = []

    for offset, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if stripped.startswith("|"):
            current.append((base_line + offset, [cell.strip() for cell in stripped.strip("|").split("|")]))
        elif current:
            flush()
    if current:
        flush()
    return tables


def table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def normalize_claims(claims: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(claims, ensure_ascii=False))
    normalized["summary"] = clean_text(normalized.get("summary", ""))
    for key in ["decisions", "open_questions", "dependencies", "impacts", "risks", "business_confirmation", "readiness_gaps", "next_actions", "evidence"]:
        normalized[key] = clean_list(normalized.get(key, []))
    normalized["actions"] = clean_action_claims(normalized.get("actions", []))
    scope = normalized.get("scope", {})
    for key in ["in", "out", "assumptions", "non_goals"]:
        scope[key] = clean_list(scope.get(key, []))
    acceptance = normalized.get("acceptance", {})
    for key in ["criteria", "evidence_required", "success_metrics"]:
        acceptance[key] = clean_list(acceptance.get(key, []))
    acceptance["owner"] = clean_text(acceptance.get("owner", ""))
    return normalized


def clean_action_claims(value: Any) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if value is None:
        return actions
    raw_items = value if isinstance(value, list) else [value]
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        action: dict[str, Any] = {}
        for key in [
            "owner",
            "workstream",
            "action",
            "source",
            "reason",
            "due_or_trigger",
            "due",
            "trigger",
            "status",
            "closure_criteria",
            "owning_workflow",
        ]:
            if key in item:
                action[key] = clean_text(item.get(key, ""))
        affected = clean_list(item.get("affected_workstreams", item.get("affectedWorkstreams", [])))
        if affected:
            action["affected_workstreams"] = affected
        if "id" in item:
            action["id"] = clean_text(item.get("id", ""))
        if "action_id" in item:
            action["action_id"] = clean_text(item.get("action_id", ""))
        key = json.dumps(action, ensure_ascii=False, sort_keys=True)
        if action and key not in seen:
            actions.append(action)
            seen.add(key)
    return actions


def clean_list(items: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in listify(items):
        text = clean_text(item)
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def clean_text(value: Any) -> str:
    return " ".join(str(value).split())


def compact_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return clean_text(value)


def build_authority(
    workstream_id: str,
    *,
    asserted_by: str,
    authority_scope: list[str],
    affected_workstreams: list[str],
    required_confirmers: list[str],
) -> dict[str, Any]:
    scope = normalize_workstreams(authority_scope) or [workstream_id]
    affected = normalize_workstreams(affected_workstreams) or scope[:]
    requires_cross_line = any(item not in scope for item in affected)
    confirmation_state = "cross-line-pending" if requires_cross_line or required_confirmers else "discovered"
    return {
        "asserted_by": asserted_by or "TBD",
        "authority_scope": scope,
        "affected_workstreams": affected,
        "required_confirmers": clean_list(required_confirmers),
        "confirmation_state": confirmation_state,
    }


def normalize_workstreams(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        try:
            result.append(normalize_id(item))
        except ValueError:
            text = clean_text(item)
            if text:
                result.append(text)
    return clean_list(result)


def source_refs(path: Path) -> list[str]:
    refs = [portable_path(path)]
    if path.parent.joinpath(".memlog.md").exists():
        refs.append(portable_path(path.parent / ".memlog.md"))
    return refs


def render_preview(candidate: dict[str, Any]) -> str:
    claims = candidate["claims"]
    authority = candidate["authority"]
    prepass = candidate.get("source_prepass", {})
    lines = [
        f"# BMM Checkpoint Candidate {candidate['candidate_id']}",
        "",
        f"- Status: {candidate['status']}",
        f"- Workstream: {candidate['workstream_id']}",
        f"- Checkpoint: {candidate['checkpoint']}",
        f"- Artifact: {candidate['artifact']['path']}",
        f"- Source revision: {candidate['artifact']['source_revision']}",
        "",
        "## Summary",
        "",
        claims.get("summary", "TBD") or "TBD",
        "",
        "## Authority",
        "",
        f"- Asserted by: {authority.get('asserted_by', 'TBD')}",
        f"- Authority scope: {', '.join(authority.get('authority_scope', [])) or 'TBD'}",
        f"- Affected workstreams: {', '.join(authority.get('affected_workstreams', [])) or 'TBD'}",
        f"- Required confirmers: {', '.join(authority.get('required_confirmers', [])) or 'none'}",
        f"- Confirmation state: {authority.get('confirmation_state', 'discovered')}",
        "",
        "## Parsed Source",
        "",
    ]
    add_prepass_summary(lines, prepass)
    return "\n".join(lines)


def add_prepass_summary(lines: list[str], prepass: dict[str, Any]) -> None:
    frontmatter = prepass.get("frontmatter", {})
    if frontmatter:
        lines.append("- Frontmatter keys: " + ", ".join(sorted(frontmatter)))
    if prepass.get("companion_sources"):
        lines.append("- Companion sources: " + "; ".join(prepass["companion_sources"]))
    if "json" in prepass:
        parsed = prepass["json"]
        if parsed.get("valid"):
            fields = parsed.get("fields", [])
            lines.append(f"- JSON fields parsed: {len(fields)}")
            for field in fields[:8]:
                lines.append(f"  - line {field.get('line')}: {field.get('path')} = {field.get('value')}")
        else:
            lines.append(
                f"- JSON parse error at line {parsed.get('line')}, column {parsed.get('column')}: {parsed.get('error')}"
            )
        return
    sections = prepass.get("sections", [])
    lines.append(f"- Markdown sections parsed: {len(sections)}")
    for section in sections[:8]:
        item_count = len(section.get("items", []))
        table_count = len(section.get("tables", []))
        lines.append(
            f"  - line {section.get('line')}: {section.get('heading')} "
            f"({item_count} items, {table_count} tables)"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract a checkpoint candidate from BMM/TEA artifacts without writing intake state.")
    parser.add_argument("project_root", help="Project root containing BMM/TEA artifacts.")
    parser.add_argument("--workstream-id", required=True, help="Workstream id for candidate id calculation.")
    parser.add_argument("--checkpoint", required=True, choices=sorted(CHECKPOINTS), help="BMM checkpoint type.")
    parser.add_argument("--artifact", action="append", default=[], metavar="[KEY=]PATH", help="Source artifact, repeatable.")
    parser.add_argument("--summary", default="", help="Project-level summary to carry into the candidate.")
    parser.add_argument("--asserted-by", default="", help="Owner or source asserting the discovered facts.")
    parser.add_argument("--authority-scope", action="append", default=[], help="Workstream the asserter can confirm.")
    parser.add_argument("--affected-workstream", action="append", default=[], help="Workstream affected by this candidate.")
    parser.add_argument("--required-confirmer", action="append", default=[], help="Required confirmer before project-level ready.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    try:
        candidate, preview, warnings = discover_candidate(
            project_root,
            args.workstream_id,
            args.checkpoint,
            args.artifact,
            summary=args.summary,
            asserted_by=args.asserted_by,
            authority_scope=args.authority_scope,
            affected_workstreams=args.affected_workstream,
            required_confirmers=args.required_confirmer,
        )
        emit({"ok": True, "candidate": candidate, "preview": preview, "warnings": warnings}, args.output)
        return 0
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "project_root": str(project_root)}, args.output)
        return 2


if __name__ == "__main__":
    sys.exit(main())
