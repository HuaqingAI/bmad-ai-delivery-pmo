#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Candidate registry for BMM checkpoint intake."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INTAKE_REL = Path("intake") / "bmm-checkpoints"
CANDIDATE_STATUSES = {"discovered", "confirmed", "applied", "superseded", "dismissed"}
CONFIRM_DECISIONS = {"confirm", "confirmed"}
DISMISS_DECISIONS = {"dismiss", "dismissed"}
SUPERSEDE_DECISIONS = {"supersede", "superseded"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_id(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value:
        raise ValueError("workstream id must contain at least one letter or digit")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_candidate_id(
    workstream_id: str,
    checkpoint: str,
    source_scope_key: str,
    source_revision: str,
    normalized_claims: dict[str, Any],
) -> str:
    payload = {
        "workstream_id": normalize_id(workstream_id),
        "checkpoint": checkpoint,
        "source_scope_key": source_scope_key,
        "source_revision": source_revision,
        "normalized_claims": normalized_claims,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return "CHK-" + digest[:16].upper()


def parse_override_value(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def apply_overrides(candidate: dict[str, Any], overrides: dict[str, Any]) -> None:
    for dotted_key, value in overrides.items():
        parts = [part for part in dotted_key.split(".") if part]
        if not parts:
            continue
        cursor: dict[str, Any] = candidate
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[parts[-1]] = value


class CandidateRegistry:
    def __init__(self, memory_root: Path):
        self.memory_root = memory_root.resolve()
        self.root = self.memory_root / INTAKE_REL
        self.candidates_dir = self.root / "candidates"
        self.index_path = self.root / "index.jsonl"

    def candidate_path(self, candidate_id: str) -> Path:
        return self.candidates_dir / f"{candidate_id}.json"

    def preview_path(self, candidate_id: str) -> Path:
        return self.candidates_dir / f"{candidate_id}.preview.md"

    def load(self, candidate_id: str) -> dict[str, Any] | None:
        path = self.candidate_path(candidate_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def list_candidates(self) -> list[dict[str, Any]]:
        if not self.candidates_dir.exists():
            return []
        candidates: list[dict[str, Any]] = []
        for path in sorted(self.candidates_dir.glob("CHK-*.json")):
            try:
                candidates.append(json.loads(path.read_text(encoding="utf-8-sig")))
            except json.JSONDecodeError:
                continue
        return candidates

    def write_candidate(self, candidate: dict[str, Any]) -> None:
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        path = self.candidate_path(candidate["candidate_id"])
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(path)

    def write_preview(self, candidate_id: str, preview: str) -> None:
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        path = self.preview_path(candidate_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(preview.rstrip() + "\n", encoding="utf-8", newline="\n")
        tmp.replace(path)

    def append_event(self, event: str, candidate: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": now_iso(),
            "event": event,
            "candidate_id": candidate.get("candidate_id"),
            "status": candidate.get("status"),
            "workstream_id": candidate.get("workstream_id"),
            "checkpoint": candidate.get("checkpoint"),
            "source_scope_key": candidate.get("artifact", {}).get("source_scope_key"),
            "source_revision": candidate.get("artifact", {}).get("source_revision"),
        }
        if extra:
            payload.update(extra)
        with self.index_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def discover(self, candidate: dict[str, Any], preview: str, dry_run: bool = False) -> dict[str, Any]:
        candidate = copy.deepcopy(candidate)
        candidate_id = candidate["candidate_id"]
        existing = self.load(candidate_id)
        if existing:
            return self._result(
                existing,
                no_op=True,
                dry_run=dry_run,
                event="duplicate-discover",
                superseded=[],
            )

        now = now_iso()
        candidate.setdefault("status", "discovered")
        candidate.setdefault("created_at", now)
        candidate["updated_at"] = now

        superseded = self._supersede_previous(candidate, dry_run=dry_run)
        if not dry_run:
            self.write_candidate(candidate)
            self.write_preview(candidate_id, preview)
            self.append_event("discovered", candidate, {"superseded": superseded})
        return self._result(candidate, no_op=False, dry_run=dry_run, event="discovered", superseded=superseded)

    def confirm(
        self,
        candidate_id: str,
        decision: str,
        overrides: dict[str, Any],
        confirmed_by: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        candidate = self.load(candidate_id)
        if not candidate:
            return {"ok": False, "error": "candidate not found", "candidate_id": candidate_id}

        normalized_decision = decision.strip().lower()
        if normalized_decision not in CONFIRM_DECISIONS | DISMISS_DECISIONS | SUPERSEDE_DECISIONS:
            return {"ok": False, "error": f"unsupported decision: {decision}", "candidate_id": candidate_id}

        event_key = canonical_json({"decision": normalized_decision, "overrides": overrides, "confirmed_by": confirmed_by})
        for event in candidate.get("confirmation_events", []):
            previous_key = canonical_json(
                {
                    "decision": event.get("decision"),
                    "overrides": event.get("overrides", {}),
                    "confirmed_by": event.get("confirmed_by", ""),
                }
            )
            if previous_key == event_key:
                return self._result(candidate, no_op=True, dry_run=dry_run, event="duplicate-confirm", superseded=[])

        updated = copy.deepcopy(candidate)
        if normalized_decision in CONFIRM_DECISIONS:
            updated["status"] = "confirmed"
        elif normalized_decision in DISMISS_DECISIONS:
            updated["status"] = "dismissed"
        else:
            updated["status"] = "superseded"

        apply_overrides(updated, overrides)
        updated.setdefault("confirmation_events", []).append(
            {
                "timestamp": now_iso(),
                "decision": normalized_decision,
                "confirmed_by": confirmed_by or "TBD",
                "overrides": overrides,
            }
        )
        updated["updated_at"] = now_iso()

        if not dry_run:
            self.write_candidate(updated)
            self.append_event(updated["status"], updated, {"confirmed_by": confirmed_by or "TBD"})
        return self._result(updated, no_op=False, dry_run=dry_run, event=updated["status"], superseded=[])

    def mark_applied(self, candidate_id: str, sync_result: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        candidate = self.load(candidate_id)
        if not candidate:
            return {"ok": False, "error": "candidate not found", "candidate_id": candidate_id}
        if candidate.get("status") == "applied":
            return self._result(candidate, no_op=True, dry_run=dry_run, event="already-applied", superseded=[])

        updated = copy.deepcopy(candidate)
        updated["status"] = "applied"
        updated["applied_at"] = now_iso()
        updated["sync_result"] = {
            "files_updated": sync_result.get("files_updated", []),
            "files_planned": sync_result.get("files_planned", []),
            "daily_log": sync_result.get("daily_log", ""),
        }
        updated["updated_at"] = now_iso()
        if not dry_run:
            self.write_candidate(updated)
            self.append_event("applied", updated)
        return self._result(updated, no_op=False, dry_run=dry_run, event="applied", superseded=[])

    def _supersede_previous(self, candidate: dict[str, Any], dry_run: bool) -> list[str]:
        artifact = candidate.get("artifact", {})
        source_scope_key = artifact.get("source_scope_key")
        source_revision = artifact.get("source_revision")
        if not source_scope_key or not source_revision:
            return []

        superseded: list[str] = []
        for existing in self.list_candidates():
            if existing.get("candidate_id") == candidate.get("candidate_id"):
                continue
            existing_artifact = existing.get("artifact", {})
            if existing.get("workstream_id") != candidate.get("workstream_id"):
                continue
            if existing.get("checkpoint") != candidate.get("checkpoint"):
                continue
            if existing_artifact.get("source_scope_key") != source_scope_key:
                continue
            if existing_artifact.get("source_revision") == source_revision:
                continue
            if existing.get("status") in {"superseded", "dismissed"}:
                continue

            updated = copy.deepcopy(existing)
            updated["status"] = "superseded"
            updated["superseded_by"] = candidate["candidate_id"]
            updated["updated_at"] = now_iso()
            superseded.append(updated["candidate_id"])
            if not dry_run:
                self.write_candidate(updated)
                self.append_event("superseded", updated, {"superseded_by": candidate["candidate_id"]})
        return superseded

    def _result(
        self,
        candidate: dict[str, Any],
        *,
        no_op: bool,
        dry_run: bool,
        event: str,
        superseded: list[str],
    ) -> dict[str, Any]:
        candidate_id = candidate["candidate_id"]
        return {
            "ok": True,
            "dry_run": dry_run,
            "event": event,
            "no_op": no_op,
            "candidate_id": candidate_id,
            "status": candidate.get("status"),
            "candidate": candidate,
            "candidate_path": str(self.candidate_path(candidate_id)),
            "preview_path": str(self.preview_path(candidate_id)),
            "index_path": str(self.index_path),
            "superseded": superseded,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the BMM checkpoint candidate intake registry.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="List candidates in a memory root.")
    list_parser.add_argument("memory_root", help="ADP memory root containing intake/bmm-checkpoints.")
    list_parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    show_parser = subparsers.add_parser("show", help="Show one candidate by id.")
    show_parser.add_argument("memory_root", help="ADP memory root containing intake/bmm-checkpoints.")
    show_parser.add_argument("--candidate-id", required=True, help="Candidate id to read.")
    show_parser.add_argument("-o", "--output", help="Write JSON result to this file instead of stdout.")
    return parser.parse_args()


def emit(result: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    else:
        print(payload)


def main() -> int:
    args = parse_args()
    registry = CandidateRegistry(Path(args.memory_root))
    if args.command == "list":
        candidates = registry.list_candidates()
        emit(
            {
                "ok": True,
                "memory_root": str(registry.memory_root),
                "candidate_count": len(candidates),
                "candidates": [
                    {
                        "candidate_id": item.get("candidate_id"),
                        "status": item.get("status"),
                        "workstream_id": item.get("workstream_id"),
                        "checkpoint": item.get("checkpoint"),
                    }
                    for item in candidates
                ],
            },
            args.output,
        )
        return 0
    if args.command == "show":
        candidate = registry.load(args.candidate_id)
        if not candidate:
            emit({"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}, args.output)
            return 2
        emit({"ok": True, "candidate": candidate}, args.output)
        return 0
    emit({"ok": False, "error": f"unknown command: {args.command}"}, getattr(args, "output", None))
    return 2


if __name__ == "__main__":
    sys.exit(main())
