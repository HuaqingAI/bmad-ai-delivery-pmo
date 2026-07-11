import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_meeting.py"
SKILL_ROOT = SCRIPT.parents[1]
DEFAULT_MEETING_TEMPLATE = SKILL_ROOT / "assets" / "meeting-sync-templates" / "meeting-note.md"
DEFAULT_PACKET_TEMPLATE = SKILL_ROOT / "assets" / "meeting-sync-templates" / "business-decision-packet.md"


class SyncMeetingTests(unittest.TestCase):
    def run_script(self, project_root: Path, plan: dict) -> dict:
        plan_path = project_root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(project_root),
                "--plan",
                str(plan_path),
                "--meeting-note-template",
                str(DEFAULT_MEETING_TEMPLATE),
                "--business-decision-packet-template",
                str(DEFAULT_PACKET_TEMPLATE),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def run_script_with_args(self, project_root: Path, plan: dict, *args: str) -> dict:
        plan_path = project_root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        command_args = list(args)
        if "--meeting-note-template" not in command_args:
            command_args.extend(["--meeting-note-template", str(DEFAULT_MEETING_TEMPLATE)])
        if "--business-decision-packet-template" not in command_args:
            command_args.extend(["--business-decision-packet-template", str(DEFAULT_PACKET_TEMPLATE)])
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), "--plan", str(plan_path), *command_args],
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
            "workstreams/l2-search",
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
        (memory_root / "workstreams" / "l2-search" / "delivery-record.md").write_text(
            "# Workstream Delivery Record\n",
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
            self.assertTrue(result["touched"]["status_sync_intake_files"])
            self.assertTrue((memory_root / "daily" / "2026-07-01.md").exists())
            intake = json.loads(Path(result["touched"]["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(intake["updates"][0]["id"], "l1-checkout")
            self.assertEqual(intake["updates"][0]["source"], "adp-meeting-sync")
            self.assertEqual(intake["updates"][0]["actions"][0]["owner"], "FDE-A")
            self.assertEqual(intake["updates"][0]["actions"][0]["source"], "meetings/2026-07-01-fde-internal-sync-checkout-blockers.md#M-001")
            self.assertIn("--updates-file", result["next_actions"][0])
            decision_log = (memory_root / "decisions" / "decision-log.md").read_text(encoding="utf-8")
            self.assertIn("Choose checkout fallback copy", decision_log)
            self.assertTrue(result["touched"]["business_decision_packets"])
            record = (memory_root / "workstreams" / "l1-checkout" / "delivery-record.md").read_text(
                encoding="utf-8",
            )
            self.assertIn("Meeting Sync Update", record)

    def test_meeting_pack_lineage_is_preserved_in_all_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-10",
                    "type": "business biweekly",
                    "title": "Roadmap decisions",
                    "source": "meeting pack",
                    "participants": ["Biz-A", "FDE-A"],
                    "summary": "Close the current roadmap decisions.",
                    "lineage": {
                        "meeting_pack_id": "2026-07-10-business-biweekly",
                        "meeting_pack_path": "views/meeting-packs/business-biweekly/2026-07-10.md",
                        "scenario": "business-biweekly",
                        "audit_path": "audits/2026-07-10-business-biweekly-audit.json",
                        "roadmap_version": "2026-07-10T08:00:00+08:00",
                    },
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "action",
                        "text": "Publish the accepted launch window.",
                        "affected_workstreams": ["l1-checkout"],
                        "owner": "FDE-A",
                        "due": "2026-07-11",
                        "closure_criteria": "Launch window is linked from the WDR.",
                    }
                ],
            }

            result = self.run_script(project_root, plan)

            expected_lineage = {
                "meeting_pack_id": "2026-07-10-business-biweekly",
                "meeting_pack_path": "views/meeting-packs/business-biweekly/2026-07-10.md",
                "scenario": "business-biweekly",
                "audit_path": "audits/2026-07-10-business-biweekly-audit.json",
                "roadmap_version": "2026-07-10T08:00:00+08:00",
            }
            self.assertEqual(result["meeting"]["lineage"], expected_lineage)
            archive = Path(result["touched"]["meeting_archives"][0]).read_text(encoding="utf-8")
            daily = (memory_root / "daily" / "2026-07-10.md").read_text(encoding="utf-8")
            self.assertIn("## Meeting Pack Lineage", archive)
            self.assertIn("2026-07-10-business-biweekly", archive)
            self.assertIn("2026-07-10-business-biweekly", daily)
            intake = json.loads(Path(result["touched"]["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(intake["meeting"]["lineage"], expected_lineage)

    def test_sync_preserves_raw_evidence_reports_gaps_and_cleans_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            raw = project_root / ".tmp" / "meeting.txt"
            raw.parent.mkdir(parents=True)
            raw.write_text("raw transcript", encoding="utf-8")
            with (memory_root / "decisions" / "decision-log.md").open("a", encoding="utf-8") as handle:
                handle.write("| TBD | TBD | TBD | TBD | TBD | TBD | open | TBD |\n")

            plan = {
                "meeting": {
                    "date": "2026-07-02",
                    "type": "FDE internal sync",
                    "title": "Generic owner check",
                    "source": "DingTalk taskUuid=abc; evidence=transcription",
                    "raw_evidence_path": str(raw),
                    "raw_evidence_label": "transcription",
                    "participants": ["发言人 1"],
                    "participant_gaps": ["participant uses unresolved speaker label 发言人 1"],
                    "summary": "Needs cleanup.",
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "action",
                        "text": "Someone needs to follow up.",
                        "affected_workstreams": ["l1-checkout"],
                        "owner": "各条线 FDE owner",
                        "owner_gap": "action owner is generic",
                    },
                    {
                        "id": "M-002",
                        "classification": "decision",
                        "text": "Accepted generic rule.",
                        "affected_workstreams": ["l1-checkout"],
                        "confirmer": "Biz-A",
                    },
                ],
            }

            result = self.run_script(project_root, plan)

            self.assertTrue(result["ok"])
            self.assertTrue(result["touched"]["raw_evidence_files"])
            raw_copy = Path(result["touched"]["raw_evidence_files"][0])
            self.assertTrue(raw_copy.exists())
            self.assertEqual("raw transcript", raw_copy.read_text(encoding="utf-8"))
            gaps = "\n".join(result["unresolved_gaps"])
            self.assertIn("participant uses unresolved speaker label", gaps)
            self.assertIn("action owner is generic", gaps)
            self.assertIn("action due trigger is missing", gaps)
            self.assertEqual(result["touched"]["status_sync_intake_files"], [])
            self.assertEqual(result["action_quality_audit"]["owner_gap_count"], 1)
            self.assertEqual(result["action_quality_audit"]["due_gap_count"], 1)
            decision_log = (memory_root / "decisions" / "decision-log.md").read_text(encoding="utf-8")
            self.assertNotIn("| TBD | TBD | TBD | TBD | TBD | TBD | open | TBD |", decision_log)

    def test_multi_workstream_action_is_canonical_program_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-05",
                    "type": "FDE internal sync",
                    "title": "ADP trial rollout",
                    "source": "notes.md",
                    "participants": ["FDE-A"],
                    "summary": "Program action affects multiple workstreams.",
                },
                "items": [
                    {
                        "id": "M-007",
                        "classification": "action",
                        "text": "Start ADP trial and return rollout feedback.",
                        "affected_workstreams": ["l1-checkout", "l2-search"],
                        "owner": "PMO-A",
                        "due": "2099-07-15",
                        "closure_criteria": "Rollout feedback summary is linked and reviewed by PMO-A.",
                    }
                ],
            }

            result = self.run_script(project_root, plan)

            self.assertTrue(result["ok"])
            audit = result["action_quality_audit"]
            self.assertEqual(audit["actions_seen"], 1)
            self.assertEqual(audit["canonical_actions"], 1)
            self.assertEqual(audit["ledger_ready_actions"], 1)
            self.assertEqual(audit["fanout_suppressed"], 1)
            intake = json.loads(Path(result["touched"]["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(len(intake["updates"]), 1)
            self.assertEqual(intake["updates"][0]["id"], "program")
            self.assertEqual(intake["updates"][0]["next_actions"], [])
            action = intake["updates"][0]["actions"][0]
            self.assertEqual(action["workstream"], "program")
            self.assertEqual(action["affected_workstreams"], ["l1-checkout", "l2-search"])
            self.assertIn("Affected workstreams: l1-checkout, l2-search", action["reason"])

    def test_invalid_plan_fails_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan_path = project_root / "plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "meeting": {
                            "date": "2026-07-01",
                            "lineage": {"meeting_pack_id": "2026-07-01-fde-morning"},
                        },
                        "items": [{"id": "M-001", "classification": "no_op", "text": "skip"}],
                    },
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(project_root),
                    "--plan",
                    str(plan_path),
                    "--meeting-note-template",
                    str(DEFAULT_MEETING_TEMPLATE),
                    "--business-decision-packet-template",
                    str(DEFAULT_PACKET_TEMPLATE),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            errors = "\n".join(result["validation_errors"])
            self.assertIn("no_op requires no_op_reason", errors)
            self.assertIn("meeting.lineage is missing", errors)

    def test_sync_without_actions_does_not_write_status_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-03",
                    "type": "FDE internal sync",
                    "title": "Facts only",
                    "source": "notes.md",
                    "participants": ["FDE-A"],
                    "summary": "No actions.",
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "fact",
                        "text": "Checkout validation is running.",
                        "affected_workstreams": ["l1-checkout"],
                    }
                ],
            }

            result = self.run_script(project_root, plan)

            self.assertTrue(result["ok"])
            self.assertEqual(result["touched"]["status_sync_intake_files"], [])
            self.assertEqual(list((memory_root / "intake" / "status-sync").glob("*.json")), [])

    def test_missing_workstream_next_action_routes_from_item_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-04",
                    "type": "FDE internal sync",
                    "title": "Missing workstream",
                    "source": "notes.md",
                    "participants": ["FDE-A"],
                    "summary": "Action needs a workstream.",
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "action",
                        "text": "FDE-A will find the owning workstream.",
                        "owner": "FDE-A",
                        "due": "Friday",
                    }
                ],
            }

            result = self.run_script(project_root, plan)

            self.assertTrue(result["ok"])
            self.assertIn("affected workstream is missing", "\n".join(result["unresolved_gaps"]))
            self.assertTrue(any("adp-workstream-register" in action for action in result["next_actions"]))

    def test_custom_templates_are_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            custom = project_root / "custom"
            custom.mkdir()
            meeting_template = custom / "meeting.md"
            packet_template = custom / "packet.md"
            meeting_template.write_text("CUSTOM MEETING {{MEETING_TITLE}}\n{{ITEM_DETAILS}}\n", encoding="utf-8")
            packet_template.write_text("CUSTOM PACKET {{TITLE}}\n{{DECISION_NEEDED}}\n", encoding="utf-8")
            plan = {
                "meeting": {
                    "date": "2026-07-04",
                    "type": "FDE internal sync",
                    "title": "Template check",
                    "source": "notes.md",
                    "participants": ["FDE-A"],
                    "summary": "Uses custom templates.",
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "business_decision_needed",
                        "text": "Business must decide rollout.",
                        "affected_workstreams": ["l1-checkout"],
                        "confirmer": "Biz-A",
                        "packet": {"decision_needed": "Decide rollout"},
                    }
                ],
            }

            result = self.run_script_with_args(
                project_root,
                plan,
                "--meeting-note-template",
                str(meeting_template),
                "--business-decision-packet-template",
                str(packet_template),
            )

            self.assertTrue(result["ok"])
            meeting = Path(result["touched"]["meeting_archives"][0]).read_text(encoding="utf-8")
            packet = Path(result["touched"]["business_decision_packets"][0]).read_text(encoding="utf-8")
            self.assertIn("CUSTOM MEETING Template check", meeting)
            self.assertIn("CUSTOM PACKET Decide rollout", packet)


if __name__ == "__main__":
    unittest.main()
