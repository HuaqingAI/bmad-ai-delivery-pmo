#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Prepare deterministic DingTalk AI Minutes intake for ADP meeting sync."""

from __future__ import annotations

import argparse
import json
import shutil
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TEXT_EXTENSIONS = {".md", ".txt", ".json"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List DingTalk AI Minutes candidates, mark already-processed meetings from "
            "ADP memory, and fetch durable transcript evidence for an exact taskUuid."
        ),
    )
    parser.add_argument("project_root", help="Project root containing ADP memory.")
    parser.add_argument(
        "--memory-root",
        default="_bmad-output/adp/memory",
        help="ADP memory root, relative to project root unless absolute. Default: _bmad-output/adp/memory.",
    )
    parser.add_argument("--task-uuid", help="Exact DingTalk AI Minutes taskUuid to fetch.")
    parser.add_argument("--raw-evidence", help="Existing raw evidence file to preserve under ADP memory.")
    parser.add_argument("--raw-evidence-label", default="raw-evidence", help="Label for a supplied raw evidence file.")
    parser.add_argument("--query", help="Optional DingTalk minutes query hint.")
    parser.add_argument("--start", help="Optional DingTalk minutes start-date hint.")
    parser.add_argument("--end", help="Optional DingTalk minutes end-date hint.")
    parser.add_argument("--max", type=int, default=10, help="Maximum candidates to list. Default: 10.")
    parser.add_argument(
        "--dws-command",
        default="dws",
        help="Command used to invoke dws. May include quoted executable parts for tests.",
    )
    parser.add_argument("--verbose", action="store_true", help="Write diagnostics to stderr.")
    parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        emit(
            {
                "ok": False,
                "error": "project_root is not an existing directory",
                "project_root": str(project_root),
            },
            args.output,
        )
        return 2

    memory_root = resolve_memory_root(project_root, args.memory_root)
    if not memory_root.exists() or not memory_root.is_dir():
        emit(
            {
                "ok": False,
                "error": "ADP memory root is missing; run adp-project-kickoff or pass --memory-root",
                "memory_root": str(memory_root),
            },
            args.output,
        )
        return 2

    if args.raw_evidence:
        result = preserve_supplied_raw_evidence(project_root, memory_root, args.raw_evidence, args.raw_evidence_label)
        emit(result, args.output)
        return 0 if result["ok"] else 2

    try:
        result = run_dingtalk_intake(args, project_root, memory_root)
    except DwsError as exc:
        emit(
            {
                "ok": False,
                "mode": "dingtalk",
                "error": str(exc),
                "memory_root": str(memory_root),
                "candidates": [],
                "gaps": ["DingTalk intake failed; use supplied raw evidence instead."],
                "next_actions": ["Ask the user for raw transcript, chat excerpt, offline notes, or a raw evidence path."],
            },
            args.output,
        )
        return 1

    if args.verbose:
        print(f"DingTalk intake mode: {result['mode']}", file=sys.stderr)
    emit(result, args.output)
    return 0 if result["ok"] else 1


def resolve_memory_root(project_root: Path, raw_memory_root: str) -> Path:
    memory_root = Path(raw_memory_root)
    if not memory_root.is_absolute():
        memory_root = project_root / memory_root
    return memory_root.resolve()


def preserve_supplied_raw_evidence(
    project_root: Path,
    memory_root: Path,
    raw_evidence: str,
    label: str,
) -> dict[str, Any]:
    source = resolve_project_path(project_root, raw_evidence)
    if not source.exists() or not source.is_file():
        return {
            "ok": False,
            "mode": "raw-evidence",
            "error": f"raw evidence file not found: {raw_evidence}",
            "memory_root": str(memory_root),
        }
    target = store_evidence_file(memory_root, source, label)
    return {
        "ok": True,
        "mode": "raw-evidence",
        "memory_root": str(memory_root),
        "raw_evidence_path": str(target),
        "raw_evidence_label": label,
        "candidates": [],
        "selected": {},
        "gaps": [],
        "next_actions": ["Use raw_evidence_path in the meeting sync plan."],
    }


def run_dingtalk_intake(args: argparse.Namespace, project_root: Path, memory_root: Path) -> dict[str, Any]:
    command = parse_command(args.dws_command)
    raw_list = dws_json(command, list_args(args))
    candidates = mark_processed(normalize_candidates(raw_list), memory_root)

    if not args.task_uuid:
        unprocessed = [candidate for candidate in candidates if not candidate["processed"]]
        return {
            "ok": True,
            "mode": "discover",
            "memory_root": str(memory_root),
            "candidates": candidates,
            "selected": {},
            "raw_evidence_path": "",
            "raw_evidence_label": "",
            "gaps": [] if unprocessed else ["No unprocessed DingTalk candidates found."],
            "next_actions": ["Choose an unprocessed taskUuid and rerun with --task-uuid."] if unprocessed else [
                "Ask the user for raw transcript, chat excerpt, offline notes, or a raw evidence path."
            ],
        }

    selected = next((candidate for candidate in candidates if candidate["taskUuid"] == args.task_uuid), None)
    info = dws_json(command, ["minutes", "get", "info", "--id", args.task_uuid, "--format", "json"])
    transcript = fetch_transcription(command, args.task_uuid)
    selected = selected or normalize_candidate(info)
    selected = merge_candidate_info(selected, info)

    raw_evidence_path = ""
    gaps: list[str] = []
    if transcript["text"]:
        raw_evidence_path = str(write_transcript(memory_root, selected, args.task_uuid, transcript["text"]))
    else:
        gaps.append("DingTalk transcription returned no transcript text.")
    if not transcript["complete"]:
        gaps.append("DingTalk transcription is incomplete; next token remained after pagination.")

    return {
        "ok": True,
        "mode": "fetch",
        "memory_root": str(memory_root),
        "candidates": candidates,
        "selected": selected,
        "raw_evidence_path": raw_evidence_path,
        "raw_evidence_label": "transcription",
        "transcript": {
            "complete": transcript["complete"],
            "page_count": transcript["page_count"],
            "segment_count": transcript["segment_count"],
            "next_token": transcript["next_token"],
        },
        "gaps": gaps,
        "next_actions": ["Use raw_evidence_path and selected metadata in the meeting sync plan."]
        if raw_evidence_path
        else ["Ask the user for raw meeting content instead of classifying from DingTalk summary."],
    }


def list_args(args: argparse.Namespace) -> list[str]:
    command = ["minutes", "list", "all", "--max", str(args.max), "--format", "json"]
    if args.query:
        command.extend(["--query", args.query])
    if args.start:
        command.extend(["--start", args.start])
    if args.end:
        command.extend(["--end", args.end])
    return command


def parse_command(raw_command: str) -> list[str]:
    command = shlex.split(raw_command)
    if not command:
        raise DwsError("dws command is empty")
    return command


def dws_json(command: list[str], args: list[str]) -> Any:
    completed = subprocess.run(
        [*command, *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise DwsError(f"dws command failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DwsError(f"dws command did not return JSON: {exc}") from exc


def fetch_transcription(command: list[str], task_uuid: str) -> dict[str, Any]:
    pages: list[Any] = []
    lines: list[str] = []
    next_token = ""
    seen_tokens: set[str] = set()

    while True:
        args = ["minutes", "get", "transcription", "--id", task_uuid, "--format", "json"]
        if next_token:
            if next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            args.extend(["--next-token", next_token])
        page = dws_json(command, args)
        pages.append(page)
        lines.extend(extract_transcript_lines(page))
        next_token = extract_next_token(page)
        if not next_token:
            break

    return {
        "text": "\n".join(line for line in lines if line).strip(),
        "complete": not next_token,
        "page_count": len(pages),
        "segment_count": len(lines),
        "next_token": next_token,
    }


def normalize_candidates(raw: Any) -> list[dict[str, Any]]:
    return [normalize_candidate(item) for item in unwrap_items(raw) if isinstance(item, dict)]


def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    task_uuid = first_string(raw, ["taskUuid", "task_uuid", "uuid", "id", "taskId", "task_id"])
    title = first_string(raw, ["title", "subject", "name", "meetingTitle", "meeting_title"]) or "Untitled meeting"
    time_value = first_value(raw, ["startTime", "start_time", "createTime", "create_time", "time", "date"])
    formatted_time, date = normalize_time(time_value)
    url = first_string(raw, ["aiMinutesUrl", "ai_minutes_url", "minutesUrl", "minutes_url", "url"])
    keywords = normalize_keywords(first_value(raw, ["keywords", "tags", "keyWords", "key_words"]))
    return {
        "taskUuid": task_uuid,
        "title": title,
        "time": formatted_time,
        "date": date,
        "ai_minutes_url": url,
        "keywords": keywords,
        "processed": False,
        "processed_reason": "",
        "possible_matches": [],
    }


def merge_candidate_info(candidate: dict[str, Any], info: Any) -> dict[str, Any]:
    if not isinstance(info, dict):
        return candidate
    info_candidate = normalize_candidate(info)
    merged = dict(candidate)
    for key, value in info_candidate.items():
        if value and key not in {"processed", "processed_reason", "possible_matches"}:
            merged[key] = value
    if not merged.get("taskUuid"):
        merged["taskUuid"] = info_candidate.get("taskUuid", "")
    return merged


def unwrap_items(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    for key in ("items", "list", "records", "minutes", "data", "result"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = unwrap_items(value)
            if nested:
                return nested
    return []


def mark_processed(candidates: list[dict[str, Any]], memory_root: Path) -> list[dict[str, Any]]:
    memory_texts = list(read_memory_texts(memory_root))
    marked: list[dict[str, Any]] = []
    for candidate in candidates:
        processed, reason = processed_reason(candidate, memory_texts)
        item = dict(candidate)
        item["processed"] = processed
        item["processed_reason"] = reason or "unprocessed"
        item["possible_matches"] = [] if processed else possible_processed_matches(candidate, memory_texts)
        marked.append(item)
    return marked


def read_memory_texts(memory_root: Path) -> list[tuple[Path, str]]:
    texts: list[tuple[Path, str]] = []
    if not memory_root.exists():
        return texts
    for path in memory_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            texts.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return texts


def processed_reason(candidate: dict[str, Any], memory_texts: list[tuple[Path, str]]) -> tuple[bool, str]:
    task_uuid = candidate.get("taskUuid", "")
    url = candidate.get("ai_minutes_url", "")
    for path, text in memory_texts:
        if task_uuid and task_uuid in text:
            return True, f"taskUuid found in {path.as_posix()}"
        if url and url in text:
            return True, f"AI Minutes URL found in {path.as_posix()}"
    return False, ""


def possible_processed_matches(candidate: dict[str, Any], memory_texts: list[tuple[Path, str]]) -> list[dict[str, str]]:
    title = candidate.get("title", "")
    date = candidate.get("date", "")
    matches: list[dict[str, str]] = []
    if not date or not title:
        return matches
    for path, text in memory_texts:
        if date in text and title in text:
            matches.append(
                {
                    "kind": "same_date_same_title",
                    "path": path.as_posix(),
                    "date": date,
                    "title": title,
                }
            )
    return matches


def write_transcript(memory_root: Path, candidate: dict[str, Any], task_uuid: str, transcript_text: str) -> Path:
    date = candidate.get("date") or datetime.now(timezone.utc).date().isoformat()
    title = candidate.get("title") or task_uuid
    filename = f"{date}-{slugify(title)}-{slugify(task_uuid)}-transcription.txt"
    target = unique_path(memory_root / "meetings" / "raw", filename)
    target.write_text(
        "\n".join(
            [
                f"source: DingTalk AI Minutes taskUuid={task_uuid}; evidence=transcription",
                f"title: {title}",
                f"time: {candidate.get('time', '')}",
                "",
                transcript_text,
            ],
        ).rstrip()
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def store_evidence_file(memory_root: Path, source: Path, label: str) -> Path:
    try:
        source.relative_to(memory_root)
        return source
    except ValueError:
        pass
    target = unique_path(memory_root / "meetings" / "raw", f"{slugify(source.stem)}-{slugify(label)}{source.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def extract_transcript_lines(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [line for item in raw for line in extract_transcript_lines(item)]
    if not isinstance(raw, dict):
        return []

    for key in ("transcription", "transcript", "sentences", "segments", "records", "items", "list", "data", "result"):
        value = raw.get(key)
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, dict)):
            lines = extract_transcript_lines(value)
            if lines:
                return lines

    text = first_string(raw, ["text", "content", "sentence", "utterance", "words"])
    if not text:
        return []
    speaker = first_string(raw, ["speaker", "speakerName", "speaker_name", "userName", "user_name"])
    timestamp = first_string(raw, ["time", "timestamp", "startTime", "start_time"])
    prefix_parts = [part for part in [timestamp, speaker] if part]
    prefix = " ".join(prefix_parts)
    return [f"{prefix}: {text}" if prefix else text]


def extract_next_token(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("nextToken", "next_token", "nextPageToken", "next_page_token", "next"):
            value = raw.get(key)
            if value:
                return str(value)
        for key in ("data", "result"):
            value = raw.get(key)
            token = extract_next_token(value)
            if token:
                return token
    return ""


def normalize_time(raw: Any) -> tuple[str, str]:
    if raw is None:
        return "", ""
    if isinstance(raw, (int, float)):
        timestamp = float(raw)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
        return dt.isoformat(timespec="seconds"), dt.date().isoformat()
    value = str(raw).strip()
    if not value:
        return "", ""
    date = value[:10] if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-" else ""
    return value, date


def normalize_keywords(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def first_value(raw: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def first_string(raw: dict[str, Any], keys: list[str]) -> str:
    value = first_value(raw, keys)
    return str(value).strip() if value not in (None, "") else ""


def resolve_project_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def slugify(value: str) -> str:
    import re

    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:80] or "item"


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))


class DwsError(RuntimeError):
    pass


if __name__ == "__main__":
    sys.exit(main())
