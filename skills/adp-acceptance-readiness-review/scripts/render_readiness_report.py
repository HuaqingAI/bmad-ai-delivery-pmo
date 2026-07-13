#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Render ADP readiness scorecard JSON into Markdown and HTML reports."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_SCRIPT = SKILLS_ROOT / "adp-plan-baseline" / "scripts" / "adp_effective_config.py"
VALID_ROADMAP_STATUSES = {"planned", "at-risk", "done", "blocked"}
GENERATED_START = "<!-- ADP readiness generated: start -->"
GENERATED_END = "<!-- ADP readiness generated: end -->"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render already-scored ADP readiness data into Markdown and HTML reports.",
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument("--input", required=True, help="Scorecard JSON produced by the readiness workflow.")
    parser.add_argument(
        "--mode",
        choices=["acceptance", "cutover", "both"],
        default="both",
        help="Report type to render. Default: both.",
    )
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument(
        "--output-dir",
        help="Output folder, relative to project root unless absolute. Default: {memory-root}/views.",
    )
    parser.add_argument(
        "--write-workstream-readiness",
        action="store_true",
        help="Also update workstreams/{id}/readiness.md with a generated score/gap block.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Return planned report paths without writing files.")
    parser.add_argument("--language", help="Override document_output_language for this derived view.")
    parser.add_argument("--config-script", default=str(DEFAULT_CONFIG_SCRIPT), help="Shared ADP effective-config resolver.")
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def load_packet(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as handle:
        packet = json.load(handle)
    if not isinstance(packet, dict):
        raise ValueError("scorecard JSON must be an object")
    if not isinstance(packet.get("workstreams"), list) or not packet["workstreams"]:
        raise ValueError("scorecard JSON must include a non-empty workstreams array")
    for index, workstream in enumerate(packet["workstreams"]):
        if not isinstance(workstream, dict):
            raise ValueError(f"workstreams[{index}] must be an object")
        if not text(workstream.get("id"), ""):
            raise ValueError(f"workstreams[{index}] must include id")
        for report_type in ["acceptance", "cutover"]:
            section = workstream.get(report_type)
            if section is None and report_type == "cutover":
                continue
            if not isinstance(section, dict):
                raise ValueError(f"workstreams[{index}].{report_type} must be an object")
            roadmap_status = text(section.get("roadmap_status"), "")
            if roadmap_status not in VALID_ROADMAP_STATUSES:
                allowed = ", ".join(sorted(VALID_ROADMAP_STATUSES))
                display = roadmap_status or "<missing>"
                raise ValueError(
                    f"workstreams[{index}].{report_type}.roadmap_status {display!r} is invalid; allowed: {allowed}"
                )
    return packet


def text(value: Any, default: str = "TBD") -> str:
    if value is None:
        return default
    value_text = str(value).strip()
    return value_text if value_text else default


def escape_md(value: Any) -> str:
    return text(value).replace("\n", " ").replace("|", "\\|")


def score_text(section: dict[str, Any]) -> str:
    score = text(section.get("score"))
    max_score = text(section.get("max_score"), "")
    return f"{score} / {max_score}" if max_score else score


def primary_gap(section: dict[str, Any], message) -> str:
    gaps = section.get("gaps") or []
    if gaps:
        first = gaps[0]
        if isinstance(first, dict):
            return text(first.get("gap") or first.get("name") or first.get("action"))
        return text(first)
    dimensions = section.get("dimensions") or []
    for item in dimensions:
        if isinstance(item, dict) and text(item.get("gap"), "") not in {"", "TBD", "None"}:
            return text(item.get("gap"))
    return message("common.none_reported")


def rows(items: list[Any], columns: list[tuple[str, str]]) -> list[str]:
    rendered = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rendered.append("| " + " | ".join(escape_md(item.get(key)) for key, _ in columns) + " |")
    return rendered


def table(title: str, items: list[Any], columns: list[tuple[str, str]], message) -> list[str]:
    if not items:
        return [f"### {title}", "", f"- {message('common.none_reported')}", ""]
    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    return [f"### {title}", "", header, sep, *rows(items, columns), ""]


def render_markdown(packet: dict[str, Any], report_type: str, message) -> str:
    title = message(f"readiness.title.{report_type}")
    generated = text(
        packet.get("generated_at"),
        datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )
    lines = [
        f"# {title}",
        "",
        f"- {message('common.project')}: {text(packet.get('project_name'), message('common.adp_project'))}",
        f"- {message('common.generated')}: {generated}",
        f"- {message('common.source')}: {text(packet.get('source'), message('readiness.default_source'))}",
        "",
        f"## {message('common.summary')}",
        "",
        text(packet.get(f"{report_type}_summary") or packet.get("summary"), message("common.no_summary")),
        "",
        f"## {message('common.workstreams')}",
        "",
        "| " + " | ".join(message(key) for key in ["common.workstream", "common.owner", "common.score", "common.status", "readiness.roadmap_status", "readiness.primary_gap"]) + " |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for workstream in packet["workstreams"]:
        section = workstream.get(report_type) if isinstance(workstream.get(report_type), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(f"{text(workstream.get('id'))} - {text(workstream.get('name'))}"),
                    escape_md(workstream.get("owner")),
                    escape_md(score_text(section)),
                    escape_md(section.get("status")),
                    escape_md(section.get("roadmap_status")),
                    escape_md(primary_gap(section, message)),
                ],
            )
            + " |",
        )
    lines.append("")
    for workstream in packet["workstreams"]:
        section = workstream.get(report_type) if isinstance(workstream.get(report_type), dict) else {}
        lines.extend(
            [
                f"## {text(workstream.get('id'))} - {text(workstream.get('name'))}",
                "",
                f"- {message('common.owner')}: {text(workstream.get('owner'))}",
                f"- {message('common.score')}: {score_text(section)}",
                f"- {message('common.status')}: {text(section.get('status'))}",
                f"- {message('readiness.roadmap_status')}: {text(section.get('roadmap_status'))}",
            ],
        )
        if report_type == "cutover":
            lines.append(f"- {message('readiness.go_no_go')}: {text(section.get('go_no_go'))}")
        lines.append("")
        lines.extend(
            table(
                message("readiness.dimension_scores"),
                section.get("dimensions") or [],
                [
                    ("dimension", message("common.dimension")),
                    ("score", message("common.score")),
                    ("gap", message("common.gap")),
                    ("owner", message("common.owner")),
                    ("action", message("common.action")),
                    ("due", message("common.due_trigger")),
                    ("severity", message("common.severity")),
                ],
                message,
            ),
        )
        lines.extend(
            table(
                message("readiness.evidence_coverage"),
                workstream.get("evidence") or [],
                [
                    ("criterion", message("readiness.acceptance_criterion")),
                    ("proof", message("readiness.proof")),
                    ("status", message("common.status")),
                    ("gap", message("common.gap")),
                ],
                message,
            ),
        )
        lines.extend(
            table(
                message("readiness.pending_confirmations"),
                workstream.get("confirmations") or [],
                [
                    ("item", message("common.item")),
                    ("owner", message("common.owner")),
                    ("status", message("common.status")),
                    ("action", message("common.action")),
                ],
                message,
            ),
        )
        lines.extend(
            table(
                message("readiness.gap_actions"),
                section.get("gaps") or [],
                [
                    ("gap", message("common.gap")),
                    ("dimension", message("common.dimension")),
                    ("owner", message("common.owner")),
                    ("action", message("common.action")),
                    ("due", message("common.due_trigger")),
                    ("severity", message("common.severity")),
                    ("escalation", message("common.escalation")),
                ],
                message,
            ),
        )
    return "\n".join(lines).rstrip() + "\n"


def render_readiness_block(workstream: dict[str, Any], message) -> str:
    acceptance = workstream.get("acceptance") if isinstance(workstream.get("acceptance"), dict) else {}
    cutover = workstream.get("cutover") if isinstance(workstream.get("cutover"), dict) else {}
    lines = [
        GENERATED_START,
        "",
        f"## {message('readiness.generated_review')}",
        "",
        f"- {message('common.workstream')}: {text(workstream.get('id'))} - {text(workstream.get('name'))}",
        f"- {message('common.owner')}: {text(workstream.get('owner'))}",
        f"- {message('readiness.acceptance_score')}: {score_text(acceptance)}",
        f"- {message('readiness.acceptance_status')}: {text(acceptance.get('status'))}",
        f"- {message('readiness.acceptance_roadmap_status')}: {text(acceptance.get('roadmap_status'))}",
    ]
    if cutover:
        lines.extend(
            [
                f"- {message('readiness.cutover_score')}: {score_text(cutover)}",
                f"- {message('readiness.cutover_status')}: {text(cutover.get('status'))}",
                f"- {message('readiness.cutover_roadmap_status')}: {text(cutover.get('roadmap_status'))}",
                f"- {message('readiness.go_no_go')}: {text(cutover.get('go_no_go'))}",
            ],
        )
    lines.append("")
    for title_key, gap_title_key, section in [("readiness.acceptance_dimensions", "readiness.acceptance_gap_actions", acceptance), ("readiness.cutover_dimensions", "readiness.cutover_gap_actions", cutover)]:
        if not section:
            continue
        lines.extend(
            table(
                message(title_key),
                section.get("dimensions") or [],
                [
                    ("dimension", message("common.dimension")),
                    ("score", message("common.score")),
                    ("gap", message("common.gap")),
                    ("owner", message("common.owner")),
                    ("action", message("common.action")),
                    ("due", message("common.due_trigger")),
                    ("severity", message("common.severity")),
                ],
                message,
            ),
        )
        lines.extend(
            table(
                message(gap_title_key),
                section.get("gaps") or [],
                [
                    ("gap", message("common.gap")),
                    ("dimension", message("common.dimension")),
                    ("owner", message("common.owner")),
                    ("action", message("common.action")),
                    ("due", message("common.due_trigger")),
                    ("severity", message("common.severity")),
                    ("escalation", message("common.escalation")),
                ],
                message,
            ),
        )
    lines.extend(
        table(
            message("readiness.evidence_coverage"),
            workstream.get("evidence") or [],
            [
                ("criterion", message("readiness.acceptance_criterion")),
                ("proof", message("readiness.proof")),
                ("status", message("common.status")),
                ("gap", message("common.gap")),
            ],
            message,
        ),
    )
    lines.extend(
        table(
            message("readiness.pending_confirmations"),
            workstream.get("confirmations") or [],
            [
                ("item", message("common.item")),
                ("owner", message("common.owner")),
                ("status", message("common.status")),
                ("action", message("common.action")),
            ],
            message,
        ),
    )
    lines.append(GENERATED_END)
    return "\n".join(lines).rstrip() + "\n"


def replace_generated_block(existing: str, generated: str, message) -> str:
    if GENERATED_START in existing and GENERATED_END in existing:
        before = existing.split(GENERATED_START, 1)[0].rstrip()
        after = existing.split(GENERATED_END, 1)[1].lstrip()
        parts = [part for part in [before, generated.rstrip(), after.rstrip()] if part]
        return "\n\n".join(parts) + "\n"
    if existing.strip():
        return existing.rstrip() + "\n\n" + generated
    return f"# {message('readiness.readiness')}\n\n" + generated


def write_workstream_readiness(memory_root: Path, workstream: dict[str, Any], dry_run: bool, message) -> dict[str, str]:
    workstream_id = text(workstream.get("id"), "")
    if not workstream_id:
        raise ValueError("workstream id is required to write readiness.md")
    path = memory_root / "workstreams" / workstream_id / "readiness.md"
    generated = render_readiness_block(workstream, message)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    content = replace_generated_block(existing, generated, message)
    status = write_report(path, content, dry_run)
    return {"workstream_id": workstream_id, "path": str(path), "status": status}


def render_html(markdown: str, title: str, locale: str) -> str:
    body_lines = []
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("# "):
            if in_table:
                body_lines.append("</table>")
                in_table = False
            body_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_table:
                body_lines.append("</table>")
                in_table = False
            body_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_table:
                body_lines.append("</table>")
                in_table = False
            body_lines.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("| "):
            cells = [html.escape(cell.strip().replace("\\|", "|")) for cell in line.strip("|").split("|")]
            if all(cell == "---" for cell in cells):
                continue
            if not in_table:
                body_lines.append("<table>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            body_lines.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
        elif line.startswith("- "):
            if in_table:
                body_lines.append("</table>")
                in_table = False
            body_lines.append(f"<p>{html.escape(line)}</p>")
        elif line.strip():
            if in_table:
                body_lines.append("</table>")
                in_table = False
            body_lines.append(f"<p>{html.escape(line)}</p>")
    if in_table:
        body_lines.append("</table>")
    body = "\n".join(body_lines)
    return f"""<!doctype html>
<html lang="{locale}">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #202124; }}
    h1, h2, h3 {{ color: #1f2937; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; vertical-align: top; }}
    th {{ background: #f6f8fa; text-align: left; }}
    p {{ line-height: 1.45; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def write_report(path: Path, content: str, dry_run: bool) -> str:
    if dry_run:
        return "planned"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return "unchanged"
    path.write_text(content, encoding="utf-8", newline="\n")
    return "updated" if previous is not None else "created"


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        emit({"ok": False, "error": "project_root is not an existing directory"}, args.output)
        return 2
    config_module = load_module(Path(args.config_script), "adp_readiness_effective_config")
    overrides = {"document_output_language": args.language} if args.language else None
    config_code, config = config_module.resolve_effective_config(project_root, overrides)
    if config_code != 0 or not config.get("ok"):
        emit({"ok": False, "error": config.get("error", "shared ADP effective config could not be resolved")}, args.output)
        return 2
    locale = str(config.get("document_locale") or "en")
    message = lambda key: config_module.message(key, locale)
    try:
        packet = load_packet(resolve_path(project_root, args.input))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        emit({"ok": False, "error": str(exc)}, args.output)
        return 2

    memory_root = resolve_path(project_root, args.memory_root)
    output_dir = resolve_path(project_root, args.output_dir) if args.output_dir else memory_root / "views"
    report_types = ["acceptance", "cutover"] if args.mode == "both" else [args.mode]
    reports = []
    for report_type in report_types:
        markdown = render_markdown(packet, report_type, message)
        html_doc = render_html(markdown, message(f"readiness.title.{report_type}"), locale)
        for suffix, content in [("md", markdown), ("html", html_doc)]:
            path = output_dir / f"{report_type}-readiness.{suffix}"
            status = write_report(path, content, args.dry_run)
            reports.append({"type": report_type, "format": suffix, "path": str(path), "status": status})
            if args.verbose:
                print(f"{status}: {path}", file=sys.stderr)
    readiness_updates = []
    if args.write_workstream_readiness:
        for workstream in packet["workstreams"]:
            readiness_updates.append(write_workstream_readiness(memory_root, workstream, args.dry_run, message))
    emit(
        {
            "ok": True,
            "dry_run": args.dry_run,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "output_dir": str(output_dir),
            "reports": reports,
            "readiness_updates": readiness_updates,
            "language": language_metadata(config, locale),
        },
        args.output,
    )
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


if __name__ == "__main__":
    sys.exit(main())
