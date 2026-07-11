#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Render ADP readiness scorecard JSON into Markdown and HTML reports."""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_TITLES = {
    "acceptance": "Acceptance Readiness Report",
    "cutover": "Cutover Readiness Report",
}
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


def primary_gap(section: dict[str, Any]) -> str:
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
    return "None reported"


def rows(items: list[Any], columns: list[tuple[str, str]]) -> list[str]:
    rendered = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rendered.append("| " + " | ".join(escape_md(item.get(key)) for key, _ in columns) + " |")
    return rendered


def table(title: str, items: list[Any], columns: list[tuple[str, str]]) -> list[str]:
    if not items:
        return [f"### {title}", "", "- None reported.", ""]
    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    return [f"### {title}", "", header, sep, *rows(items, columns), ""]


def render_markdown(packet: dict[str, Any], report_type: str) -> str:
    title = REPORT_TITLES[report_type]
    generated = text(
        packet.get("generated_at"),
        datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )
    lines = [
        f"# {title}",
        "",
        f"- Project: {text(packet.get('project_name'), 'ADP project')}",
        f"- Generated: {generated}",
        f"- Source: {text(packet.get('source'), 'ADP readiness review')}",
        "",
        "## Summary",
        "",
        text(packet.get(f"{report_type}_summary") or packet.get("summary"), "No summary provided."),
        "",
        "## Workstreams",
        "",
        "| Workstream | Owner | Score | Status | Roadmap Status | Primary gap |",
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
                    escape_md(primary_gap(section)),
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
                f"- Owner: {text(workstream.get('owner'))}",
                f"- Score: {score_text(section)}",
                f"- Status: {text(section.get('status'))}",
                f"- Roadmap status: {text(section.get('roadmap_status'))}",
            ],
        )
        if report_type == "cutover":
            lines.append(f"- Go/no-go: {text(section.get('go_no_go'))}")
        lines.append("")
        lines.extend(
            table(
                "Dimension Scores",
                section.get("dimensions") or [],
                [
                    ("dimension", "Dimension"),
                    ("score", "Score"),
                    ("gap", "Gap"),
                    ("owner", "Owner"),
                    ("action", "Action"),
                    ("due", "Due / Trigger"),
                    ("severity", "Severity"),
                ],
            ),
        )
        lines.extend(
            table(
                "Evidence Coverage",
                workstream.get("evidence") or [],
                [
                    ("criterion", "Acceptance Criterion"),
                    ("proof", "Proof"),
                    ("status", "Status"),
                    ("gap", "Gap"),
                ],
            ),
        )
        lines.extend(
            table(
                "Pending Confirmations",
                workstream.get("confirmations") or [],
                [
                    ("item", "Item"),
                    ("owner", "Owner"),
                    ("status", "Status"),
                    ("action", "Action"),
                ],
            ),
        )
        lines.extend(
            table(
                "Gap Actions",
                section.get("gaps") or [],
                [
                    ("gap", "Gap"),
                    ("dimension", "Dimension"),
                    ("owner", "Owner"),
                    ("action", "Action"),
                    ("due", "Due / Trigger"),
                    ("severity", "Severity"),
                    ("escalation", "Escalation"),
                ],
            ),
        )
    return "\n".join(lines).rstrip() + "\n"


def render_readiness_block(workstream: dict[str, Any]) -> str:
    acceptance = workstream.get("acceptance") if isinstance(workstream.get("acceptance"), dict) else {}
    cutover = workstream.get("cutover") if isinstance(workstream.get("cutover"), dict) else {}
    lines = [
        GENERATED_START,
        "",
        "## Generated Readiness Review",
        "",
        f"- Workstream: {text(workstream.get('id'))} - {text(workstream.get('name'))}",
        f"- Owner: {text(workstream.get('owner'))}",
        f"- Acceptance score: {score_text(acceptance)}",
        f"- Acceptance status: {text(acceptance.get('status'))}",
        f"- Acceptance roadmap status: {text(acceptance.get('roadmap_status'))}",
    ]
    if cutover:
        lines.extend(
            [
                f"- Cutover score: {score_text(cutover)}",
                f"- Cutover status: {text(cutover.get('status'))}",
                f"- Cutover roadmap status: {text(cutover.get('roadmap_status'))}",
                f"- Go/no-go: {text(cutover.get('go_no_go'))}",
            ],
        )
    lines.append("")
    for title, section in [("Acceptance Dimensions", acceptance), ("Cutover Dimensions", cutover)]:
        if not section:
            continue
        lines.extend(
            table(
                title,
                section.get("dimensions") or [],
                [
                    ("dimension", "Dimension"),
                    ("score", "Score"),
                    ("gap", "Gap"),
                    ("owner", "Owner"),
                    ("action", "Action"),
                    ("due", "Due / Trigger"),
                    ("severity", "Severity"),
                ],
            ),
        )
        lines.extend(
            table(
                title.replace("Dimensions", "Gap Actions"),
                section.get("gaps") or [],
                [
                    ("gap", "Gap"),
                    ("dimension", "Dimension"),
                    ("owner", "Owner"),
                    ("action", "Action"),
                    ("due", "Due / Trigger"),
                    ("severity", "Severity"),
                    ("escalation", "Escalation"),
                ],
            ),
        )
    lines.extend(
        table(
            "Evidence Coverage",
            workstream.get("evidence") or [],
            [
                ("criterion", "Acceptance Criterion"),
                ("proof", "Proof"),
                ("status", "Status"),
                ("gap", "Gap"),
            ],
        ),
    )
    lines.extend(
        table(
            "Pending Confirmations",
            workstream.get("confirmations") or [],
            [
                ("item", "Item"),
                ("owner", "Owner"),
                ("status", "Status"),
                ("action", "Action"),
            ],
        ),
    )
    lines.append(GENERATED_END)
    return "\n".join(lines).rstrip() + "\n"


def replace_generated_block(existing: str, generated: str) -> str:
    if GENERATED_START in existing and GENERATED_END in existing:
        before = existing.split(GENERATED_START, 1)[0].rstrip()
        after = existing.split(GENERATED_END, 1)[1].lstrip()
        parts = [part for part in [before, generated.rstrip(), after.rstrip()] if part]
        return "\n\n".join(parts) + "\n"
    if existing.strip():
        return existing.rstrip() + "\n\n" + generated
    return "# Readiness\n\n" + generated


def write_workstream_readiness(memory_root: Path, workstream: dict[str, Any], dry_run: bool) -> dict[str, str]:
    workstream_id = text(workstream.get("id"), "")
    if not workstream_id:
        raise ValueError("workstream id is required to write readiness.md")
    path = memory_root / "workstreams" / workstream_id / "readiness.md"
    generated = render_readiness_block(workstream)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    content = replace_generated_block(existing, generated)
    status = write_report(path, content, dry_run)
    return {"workstream_id": workstream_id, "path": str(path), "status": status}


def render_html(markdown: str, title: str) -> str:
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
<html lang="en">
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
        print(payload)


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        emit({"ok": False, "error": "project_root is not an existing directory"}, args.output)
        return 2
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
        markdown = render_markdown(packet, report_type)
        html_doc = render_html(markdown, REPORT_TITLES[report_type])
        for suffix, content in [("md", markdown), ("html", html_doc)]:
            path = output_dir / f"{report_type}-readiness.{suffix}"
            status = write_report(path, content, args.dry_run)
            reports.append({"type": report_type, "format": suffix, "path": str(path), "status": status})
            if args.verbose:
                print(f"{status}: {path}", file=sys.stderr)
    readiness_updates = []
    if args.write_workstream_readiness:
        for workstream in packet["workstreams"]:
            readiness_updates.append(write_workstream_readiness(memory_root, workstream, args.dry_run))
    emit(
        {
            "ok": True,
            "dry_run": args.dry_run,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "output_dir": str(output_dir),
            "reports": reports,
            "readiness_updates": readiness_updates,
        },
        args.output,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
