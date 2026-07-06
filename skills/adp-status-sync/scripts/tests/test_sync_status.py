import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_status.py"


RECORD = """# Workstream Delivery Record

## Identity

- Workstream ID: l1-checkout
- Name: Checkout
- FDE owner: FDE-A
- Business owner: Biz-A
- Current BMM phase: PRD
- Current ADP status: draft

## Project Status

- Progress: TBD
- Blockers: TBD
- Risks: TBD
- Dependencies: see cross-workstream links
- Scope or change notes: TBD
- Next actions: fill missing state

## Record Rule

Keep details in BMM artifacts.
"""


class SyncStatusTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args, str(project_root)]
            if args and args[0] == "stale"
            else [sys.executable, str(SCRIPT), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def create_record(self, project_root: Path, workstream_id: str = "l1-checkout", text: str = RECORD) -> Path:
        record = project_root / "_bmad-output" / "adp" / "memory" / "workstreams" / workstream_id / "delivery-record.md"
        record.parent.mkdir(parents=True)
        record.write_text(text, encoding="utf-8")
        return record

    def test_update_replaces_volatile_fields_and_appends_daily_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "update",
                    str(project_root),
                    "--id",
                    "L1 Checkout",
                    "--status",
                    "at-risk",
                    "--progress",
                    "PRD baseline pending business confirmation",
                    "--blocker",
                    "Payment owner unavailable",
                    "--risk",
                    "Checkout acceptance may slip",
                    "--next-action",
                    "FDE-A confirm payment owner by Friday",
                    "--source",
                    "owner update",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            updated = record.read_text(encoding="utf-8")
            self.assertIn("- Current ADP status: at-risk", updated)
            self.assertIn("- Progress: PRD baseline pending business confirmation", updated)
            self.assertIn("- Blockers: Payment owner unavailable", updated)
            self.assertIn("- Risks: Checkout acceptance may slip", updated)
            self.assertIn("- Next actions: FDE-A confirm payment owner by Friday", updated)
            self.assertIn("- Last status sync:", updated)
            daily_log = Path(result["updates"][0]["daily_log"])
            self.assertTrue(daily_log.exists())
            self.assertIn("Status sync - l1-checkout", daily_log.read_text(encoding="utf-8"))
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "actions" / "action-ledger.md").exists())

    def test_stale_reports_missing_and_old_syncs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            old = RECORD.replace("- Next actions: fill missing state", "- Next actions: fill missing state\n- Last status sync: 2026-06-01T00:00:00+00:00")
            self.create_record(project_root, "l2-search", old)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "stale",
                    str(project_root),
                    "--max-age-days",
                    "7",
                    "--as-of",
                    "2026-06-10",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            stale_ids = {item["workstream_id"] for item in result["stale_workstreams"]}
            self.assertEqual(stale_ids, {"l1-checkout", "l2-search"})

    def test_batch_file_updates_multiple_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root, "l1-checkout")
            self.create_record(project_root, "l2-search", RECORD.replace("l1-checkout", "l2-search"))
            updates_file = project_root / "updates.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {"id": "l1-checkout", "status": "on-track", "next_actions": ["FDE-A send summary"]},
                            {"id": "l2-search", "status": "blocked", "blockers": ["API contract missing"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["updates"]), 2)
            self.assertIn(
                "- Current ADP status: blocked",
                (project_root / "_bmad-output" / "adp" / "memory" / "workstreams" / "l2-search" / "delivery-record.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_noop_update_does_not_refresh_last_status_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            before = record.read_text(encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "update",
                    str(project_root),
                    "--id",
                    "l1-checkout",
                    "--source",
                    "empty owner note",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertTrue(result["updates"][0]["no_op"])
            self.assertEqual(before, record.read_text(encoding="utf-8"))
            self.assertIn("no reliable volatile field update", result["updates"][0]["unresolved_gaps"][0])
            daily_log = Path(result["updates"][0]["daily_log"])
            self.assertTrue(daily_log.exists())
            self.assertIn("no reliable field change", daily_log.read_text(encoding="utf-8"))

    def test_updates_file_registers_actions_in_ledger_and_merges_wdr_next_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            updates_file = project_root / "updates.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "status": "in-progress",
                                "next_actions": ["FDE-A send summary"],
                                "actions": [
                                    {
                                        "owner": "FDE-A",
                                        "workstream": "l1-checkout",
                                        "action": "Add checkout validation evidence",
                                        "source": "meetings/2026-07-01-sync.md#M-001",
                                        "reason": "Meeting action",
                                        "due": "Friday",
                                        "closure_criteria": "Evidence linked in evidence.md",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            first = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            first_result = json.loads(first.stdout)
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            second_result = json.loads(second.stdout)

            self.assertTrue(first_result["ok"])
            self.assertEqual(len(first_result["actions_registered"]), 1)
            self.assertEqual(second_result["actions_registered"], [])
            self.assertEqual(len(second_result["actions_updated"]), 1)
            ledger = project_root / "_bmad-output" / "adp" / "memory" / "actions" / "action-ledger.md"
            ledger_text = ledger.read_text(encoding="utf-8")
            self.assertEqual(ledger_text.count("Add checkout validation evidence"), 1)
            updated = record.read_text(encoding="utf-8")
            self.assertIn("FDE-A send summary", updated)
            self.assertIn("Add checkout validation evidence", updated)
            self.assertIn("due: Friday", updated)

    def test_program_action_registers_without_workstream_record_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "_bmad-output" / "adp" / "memory"
            memory_root.mkdir(parents=True)
            updates_file = project_root / "program-actions.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "program",
                                "source": "adp-meeting-sync",
                                "actions": [
                                    {
                                        "owner": "PMO-A",
                                        "workstream": "program",
                                        "affected_workstreams": ["l1-checkout", "l2-search"],
                                        "action": "Start ADP trial and return rollout feedback.",
                                        "source": "meetings/2026-07-05-sync.md#M-007",
                                        "reason": "Meeting action; Affected workstreams: l1-checkout, l2-search",
                                        "due": "2099-07-15",
                                        "closure_criteria": "Rollout feedback summary is linked and reviewed by PMO-A.",
                                        "owning_workflow": "adp-meeting-sync",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["actions_registered"]), 1)
            self.assertFalse(result["updates"][0]["wdr_missing"])
            self.assertTrue(result["updates"][0]["project_action_scope"])
            self.assertNotIn("delivery-record.md not found", "\n".join(result["unresolved_gaps"]))
            ledger = memory_root / "actions" / "action-ledger.md"
            ledger_text = ledger.read_text(encoding="utf-8")
            self.assertIn("Affected Workstreams", ledger_text)
            self.assertIn("l1-checkout; l2-search", ledger_text)

    def test_done_action_is_removed_from_wdr_summary_but_kept_in_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root, text=RECORD.replace("- Next actions: fill missing state", "- Next actions: FDE-A: Add checkout validation evidence (due: Friday)"))
            memory_root = project_root / "_bmad-output" / "adp" / "memory"
            ledger = memory_root / "actions" / "action-ledger.md"
            ledger.parent.mkdir(parents=True)
            ledger.write_text(
                "\n".join(
                    [
                        "# Action Ledger",
                        "",
                        "| Action ID | Status | Owner | Workstream | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                        "| ACT-20260701-001 | open | FDE-A | l1-checkout | Add checkout validation evidence | meetings/2026-07-01-sync.md#M-001 | Meeting action | Friday | Evidence linked in evidence.md | 2026-07-01T09:00:00+08:00 | adp-status-sync |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            updates_file = project_root / "close.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "action_id": "ACT-20260701-001",
                                        "status": "done",
                                        "source": "workstreams/l1-checkout/evidence.md#proof",
                                        "reason": "Evidence accepted",
                                        "closure_criteria": "Evidence linked in evidence.md",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["actions_closed"], ["ACT-20260701-001"])
            self.assertIn("| ACT-20260701-001 | done |", ledger.read_text(encoding="utf-8"))
            updated = record.read_text(encoding="utf-8")
            self.assertIn("- Next actions: fill missing state", updated)
            self.assertNotIn("FDE-A: Add checkout validation evidence (due: Friday)", updated)


if __name__ == "__main__":
    unittest.main()
