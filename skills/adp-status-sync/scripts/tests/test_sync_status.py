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
        record = project_root / "_bmad" / "memory" / "adp" / "workstreams" / workstream_id / "delivery-record.md"
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
                (project_root / "_bmad" / "memory" / "adp" / "workstreams" / "l2-search" / "delivery-record.md").read_text(
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


if __name__ == "__main__":
    unittest.main()
