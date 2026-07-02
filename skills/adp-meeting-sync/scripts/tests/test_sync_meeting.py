import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_meeting.py"


class SyncMeetingTests(unittest.TestCase):
    def run_script(self, project_root: Path, plan: dict) -> dict:
        plan_path = project_root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), "--plan", str(plan_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def make_memory(self, project_root: Path) -> Path:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        for rel in [
            "meetings",
            "daily",
            "decisions/business-decision-packets",
            "workstreams/l1-checkout",
        ]:
            (memory_root / rel).mkdir(parents=True, exist_ok=True)
        (memory_root / "decisions" / "decision-log.md").write_text(
            "\n".join(
                [
                    "# Decision Log",
                    "",
                    "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                    "",
                    "## Rules",
                    "",
                ],
            ),
            encoding="utf-8",
        )
        (memory_root / "workstreams" / "l1-checkout" / "delivery-record.md").write_text(
            "# Workstream Delivery Record\n",
            encoding="utf-8",
        )
        (memory_root / "workstreams" / "l1-checkout" / "decisions.md").write_text(
            "# Decisions\n",
            encoding="utf-8",
        )
        return memory_root

    def test_sync_writes_meeting_daily_decision_packet_and_wdr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-01",
                    "type": "FDE internal sync",
                    "title": "Checkout blockers",
                    "source": "notes.md",
                    "participants": ["FDE-A", "PM"],
                    "summary": "Checkout line needs business confirmation.",
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "action",
                        "text": "FDE-A will add validation evidence.",
                        "affected_workstreams": ["L1 Checkout"],
                        "owner": "FDE-A",
                        "due": "Friday",
                    },
                    {
                        "id": "M-002",
                        "classification": "business_decision_needed",
                        "text": "Business must choose fallback copy.",
                        "affected_workstreams": ["l1-checkout"],
                        "confirmer": "Biz-A",
                        "packet": {
                            "decision_needed": "Choose checkout fallback copy",
                            "options": ["Use current copy", "Use new migration copy"],
                            "confirming_owner": "Biz-A",
                        },
                    },
                ],
            }

            result = self.run_script(project_root, plan)

            self.assertTrue(result["ok"])
            self.assertTrue(result["touched"]["meeting_archives"])
            self.assertTrue((memory_root / "daily" / "2026-07-01.md").exists())
            decision_log = (memory_root / "decisions" / "decision-log.md").read_text(encoding="utf-8")
            self.assertIn("Choose checkout fallback copy", decision_log)
            self.assertTrue(result["touched"]["business_decision_packets"])
            record = (memory_root / "workstreams" / "l1-checkout" / "delivery-record.md").read_text(
                encoding="utf-8",
            )
            self.assertIn("Meeting Sync Update", record)

    def test_invalid_plan_fails_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan_path = project_root / "plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "meeting": {"date": "2026-07-01"},
                        "items": [{"id": "M-001", "classification": "no_op", "text": "skip"}],
                    },
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(project_root), "--plan", str(plan_path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertIn("no_op requires no_op_reason", "\n".join(result["validation_errors"]))


if __name__ == "__main__":
    unittest.main()
