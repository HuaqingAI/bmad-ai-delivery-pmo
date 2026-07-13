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
    def vnext_lineage(self, scenario: str = "fde-morning") -> dict:
        return {
            "meeting_pack_id": f"2026-07-10-{scenario}",
            "meeting_pack_path": f"views/meeting-packs/{scenario}/2026-07-10.md",
            "scenario": scenario,
            "audit_path": f"audits/2026-07-10-{scenario}-audit.json",
            "roadmap_version": "2026-07-10T08:00:00+08:00" if scenario == "business-biweekly" else "not-applicable",
            "program_status_snapshot_id": "ps-meeting-pack-fixture",
            "baseline_revision": 2,
            "source_fingerprints": {"plans/program-baseline.md": "sha256:fixture"},
            "input_audit_id": "audit-program-status-fixture",
            "generator_version": "2.0.0",
        }

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
            archive_rel = Path(result["touched"]["meeting_archives"][0]).relative_to(memory_root).as_posix()
            self.assertEqual(intake["updates"][0]["actions"][0]["source"], f"{archive_rel}#M-001")
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
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:30:00+08:00",
                    "lineage": self.vnext_lineage("business-biweekly"),
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

            expected_lineage = self.vnext_lineage("business-biweekly")
            self.assertEqual(result["meeting"]["lineage"], expected_lineage)
            archive = Path(result["touched"]["meeting_archives"][0]).read_text(encoding="utf-8")
            daily = (memory_root / "daily" / "2026-07-10.md").read_text(encoding="utf-8")
            self.assertIn("## Meeting Pack Lineage", archive)
            self.assertIn("2026-07-10-business-biweekly", archive)
            self.assertIn("2026-07-10-business-biweekly", daily)
            intake = json.loads(Path(result["touched"]["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(intake["meeting"]["lineage"], expected_lineage)
            self.assertEqual(intake["meeting"]["meeting_instance_id"], result["meeting"]["meeting_instance_id"])
            receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "applied")
            self.assertEqual(receipt["lineage"], expected_lineage)
            cursor = json.loads(Path(result["cursor"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(cursor["meeting_instance_id"], result["meeting"]["meeting_instance_id"])
            self.assertEqual(cursor["ended_at"], "2026-07-10T09:30:00+08:00")

    def test_meeting_pack_distillate_injects_verified_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            lineage = self.vnext_lineage()
            distillate_path = project_root / "meeting-pack.json"
            distillate_path.write_text(
                json.dumps(
                    {
                        **lineage,
                        "next_workflow_payload": {
                            **lineage,
                            "lineage": lineage,
                        },
                    }
                ),
                encoding="utf-8",
            )
            plan = {
                "meeting": {
                    "date": "2026-07-10",
                    "type": "FDE morning",
                    "title": "Lineage injection",
                    "source": "meeting pack",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                },
                "items": [{"id": "M-001", "classification": "no_op", "text": "No state change", "no_op_reason": "Fixture"}],
            }

            result = self.run_script_with_args(
                project_root,
                plan,
                "--meeting-pack-distillate",
                str(distillate_path),
            )

            self.assertEqual(result["meeting"]["lineage"], lineage)

    def test_meeting_pack_distillate_rejects_conflicting_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            lineage = self.vnext_lineage()
            distillate_path = project_root / "meeting-pack.json"
            distillate_path.write_text(
                json.dumps(
                    {
                        **lineage,
                        "meeting_pack_id": "conflicting-pack-id",
                        "next_workflow_payload": {**lineage, "lineage": lineage},
                    }
                ),
                encoding="utf-8",
            )
            plan_path = project_root / "plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "meeting": {
                            "date": "2026-07-10",
                            "started_at": "2026-07-10T09:00:00+08:00",
                            "ended_at": "2026-07-10T09:20:00+08:00",
                        },
                        "items": [{"id": "M-001", "classification": "no_op", "text": "No change", "no_op_reason": "Fixture"}],
                    }
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
                    "--meeting-pack-distillate",
                    str(distillate_path),
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

            result = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("distillate lineage conflicts", result["error"])

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

    def test_action_quality_uses_explicit_gaps_not_phrase_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-13",
                    "type": "FDE internal sync",
                    "title": "Action judgment boundary",
                    "source": "notes.md",
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "action",
                        "text": "Publish the signed acceptance record.",
                        "affected_workstreams": ["l1-checkout"],
                        "owner": "Project delivery team",
                        "due": "2099-07-15",
                        "closure_criteria": "Daily log contains the signed acceptance record and checksum.",
                    },
                    {
                        "id": "M-002",
                        "classification": "action",
                        "text": "Close the rollout evidence gap.",
                        "affected_workstreams": ["l2-search"],
                        "owner": "FDE-A",
                        "due": "2099-07-16",
                        "closure_criteria": "Owner updates WDR/daily/status-sync.",
                        "closure_gap": "Process updates do not identify the deliverable that proves completion.",
                    },
                ],
            }

            result = self.run_script(project_root, plan)

            audit = result["action_quality_audit"]
            self.assertEqual(audit["owner_gap_count"], 0)
            self.assertEqual(audit["closure_gap_count"], 1)
            intake = json.loads(Path(result["touched"]["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            actions = {action["action"]: action for update in intake["updates"] for action in update["actions"]}
            self.assertEqual(actions["Publish the signed acceptance record."]["status"], "open")
            self.assertEqual(actions["Close the rollout evidence gap."]["status"], "blocked")
            self.assertEqual(actions["Close the rollout evidence gap."]["closure_criteria"], "TBD")

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

    def test_dry_run_does_not_create_receipt_cursor_or_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-10",
                    "type": "FDE morning",
                    "title": "Dry run",
                    "source": "meeting pack",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                    "lineage": self.vnext_lineage(),
                },
                "items": [{"id": "M-001", "classification": "no_op", "text": "No change", "no_op_reason": "Dry run fixture"}],
            }

            result = self.run_script_with_args(project_root, plan, "--dry-run")

            self.assertTrue(result["ok"])
            self.assertEqual(result["replay_status"], "planned")
            self.assertFalse(Path(result["planned_receipt"]).exists())
            self.assertFalse(Path(result["planned_cursor"]).exists())
            self.assertEqual(list((memory_root / "meetings").glob("*.md")), [])

    def test_same_instance_replay_is_noop_without_duplicate_appends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "meeting_instance_id": "mi-replay-fixture",
                    "date": "2026-07-10",
                    "type": "FDE morning",
                    "title": "Replay safety",
                    "source": "meeting pack",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                    "lineage": self.vnext_lineage(),
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "decision",
                        "text": "Keep the rollout gate.",
                        "affected_workstreams": ["l1-checkout"],
                        "confirmer": "FDE-A",
                    }
                ],
            }

            first = self.run_script(project_root, plan)
            daily_path = memory_root / "daily" / "2026-07-10.md"
            wdr_path = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            decision_path = memory_root / "decisions" / "decision-log.md"
            before = (daily_path.read_text(encoding="utf-8"), wdr_path.read_text(encoding="utf-8"), decision_path.read_text(encoding="utf-8"))
            cursor_path = Path(first["cursor"]["path"])
            cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
            cursor["archive"] = "meetings/missing.md"
            cursor_path.write_text(json.dumps(cursor), encoding="utf-8")
            second = self.run_script(project_root, plan)

            self.assertEqual(first["replay_status"], "applied")
            self.assertEqual(second["replay_status"], "idempotent-no-op")
            self.assertEqual(second["cursor"]["status"], "repaired")
            repaired_cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
            self.assertTrue((memory_root / repaired_cursor["archive"]).is_file())
            self.assertEqual(before, (daily_path.read_text(encoding="utf-8"), wdr_path.read_text(encoding="utf-8"), decision_path.read_text(encoding="utf-8")))
            self.assertEqual(len(list((memory_root / "meetings").glob("*.md"))), 1)
            self.assertEqual(len(list((memory_root / "meetings" / "receipts").glob("*.json"))), 1)

    def test_same_instance_changed_plan_is_explicit_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan = {
                "meeting": {
                    "meeting_instance_id": "mi-conflict-fixture",
                    "date": "2026-07-10",
                    "type": "FDE morning",
                    "title": "Conflict safety",
                    "source": "meeting pack",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                    "lineage": self.vnext_lineage(),
                },
                "items": [{"id": "M-001", "classification": "fact", "text": "Original fact"}],
            }
            self.run_script(project_root, plan)
            plan["items"][0]["text"] = "Changed fact"
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
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            result = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "conflict")
            self.assertIn("different plan fingerprint", result["error"])

    def test_applying_receipt_resumes_without_duplicate_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "meeting_instance_id": "mi-resume-fixture",
                    "date": "2026-07-10",
                    "type": "FDE morning",
                    "title": "Resume safety",
                    "source": "meeting pack",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                    "lineage": self.vnext_lineage(),
                },
                "items": [{"id": "M-001", "classification": "fact", "text": "Resume fact", "affected_workstreams": ["l1-checkout"]}],
            }
            first = self.run_script(project_root, plan)
            receipt_path = Path(first["receipt"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["status"] = "applying"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            daily_path = memory_root / "daily" / "2026-07-10.md"
            before = daily_path.read_text(encoding="utf-8")

            resumed = self.run_script(project_root, plan)

            self.assertEqual(resumed["replay_status"], "resumed")
            self.assertEqual(before, daily_path.read_text(encoding="utf-8"))
            final_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(final_receipt["status"], "applied")

    def test_older_successful_meeting_does_not_move_cursor_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            base_meeting = {
                "type": "FDE morning",
                "title": "Cursor ordering",
                "source": "meeting pack",
                "lineage": self.vnext_lineage(),
            }
            newer = {
                "meeting": {
                    **base_meeting,
                    "meeting_instance_id": "mi-newer-fixture",
                    "date": "2026-07-10",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                },
                "items": [{"id": "M-001", "classification": "no_op", "text": "Newer", "no_op_reason": "Fixture"}],
            }
            older = {
                "meeting": {
                    **base_meeting,
                    "meeting_instance_id": "mi-older-fixture",
                    "date": "2026-07-09",
                    "started_at": "2026-07-09T09:00:00+08:00",
                    "ended_at": "2026-07-09T09:20:00+08:00",
                },
                "items": [{"id": "M-001", "classification": "no_op", "text": "Older", "no_op_reason": "Fixture"}],
            }

            first = self.run_script(project_root, newer)
            second = self.run_script(project_root, older)

            self.assertEqual(second["cursor"]["status"], "not-advanced")
            cursor = json.loads(Path(first["cursor"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(cursor["meeting_instance_id"], "mi-newer-fixture")

    def test_non_ascii_item_ids_get_distinct_append_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-10",
                    "type": "FDE internal sync",
                    "title": "中文条目",
                    "source": "notes.md",
                },
                "items": [
                    {"id": "决定一", "classification": "decision", "text": "保留门禁一。", "affected_workstreams": ["l1-checkout"], "confirmer": "FDE-A"},
                    {"id": "决定二", "classification": "decision", "text": "保留门禁二。", "affected_workstreams": ["l1-checkout"], "confirmer": "FDE-A"},
                ],
            }

            self.run_script(project_root, plan)

            wdr = (memory_root / "workstreams" / "l1-checkout" / "delivery-record.md").read_text(encoding="utf-8")
            decisions = (memory_root / "workstreams" / "l1-checkout" / "decisions.md").read_text(encoding="utf-8")
            self.assertIn("保留门禁一。", wdr)
            self.assertIn("保留门禁二。", wdr)
            self.assertIn("保留门禁一。", decisions)
            self.assertIn("保留门禁二。", decisions)

    def test_source_backed_milestone_actual_and_forecast_enter_status_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-10",
                    "type": "FDE morning",
                    "title": "Milestone handoff",
                    "source": "meeting pack",
                    "started_at": "2026-07-10T09:00:00+08:00",
                    "ended_at": "2026-07-10T09:20:00+08:00",
                    "lineage": self.vnext_lineage(),
                },
                "items": [
                    {
                        "id": "M-001",
                        "classification": "fact",
                        "text": "Checkout milestone completed and the next forecast moved.",
                        "affected_workstreams": ["l1-checkout"],
                        "milestones": [
                            {
                                "milestone_id": "MS-CHECKOUT-COMPLETE",
                                "status": "done",
                                "forecast": "2026-07-10",
                                "actual": "2026-07-10",
                                "evidence": ["meetings/raw/transcript.txt#checkout-complete"],
                            }
                        ],
                    },
                    {
                        "id": "M-002",
                        "classification": "fact",
                        "text": "An unverified milestone claim was mentioned.",
                        "affected_workstreams": ["l1-checkout"],
                        "milestone": {"milestone_id": "MS-UNVERIFIED", "status": "at-risk"},
                    },
                    {
                        "id": "M-003",
                        "classification": "fact",
                        "text": "A milestone supplied an invalid explicit revision.",
                        "affected_workstreams": ["l1-checkout"],
                        "milestone": {
                            "milestone_id": "MS-BAD-REVISION",
                            "status": "planned",
                            "evidence": ["meeting#bad-revision"],
                            "baseline_revision": 0,
                        },
                    },
                ],
            }

            result = self.run_script(project_root, plan)

            intake = json.loads(Path(result["touched"]["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            milestone = intake["updates"][0]["milestones"][0]
            self.assertEqual(milestone["milestone_id"], "MS-CHECKOUT-COMPLETE")
            self.assertEqual(milestone["actual"], "2026-07-10")
            self.assertEqual(milestone["baseline_revision"], 2)
            self.assertTrue(milestone["source"].endswith("#M-001"))
            self.assertEqual(result["milestone_quality_audit"]["milestones_seen"], 3)
            self.assertEqual(result["milestone_quality_audit"]["ledger_ready_milestones"], 1)
            self.assertIn("milestone evidence is missing", "\n".join(result["unresolved_gaps"]))
            self.assertIn("milestone baseline_revision is missing or invalid", "\n".join(result["unresolved_gaps"]))

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

    def test_language_golden_localizes_archive_and_preserves_lineage_and_facts(self) -> None:
        def run(language: str) -> tuple[dict, str]:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            project_root = Path(temp.name)
            self.make_memory(project_root)
            plan = {
                "meeting": {
                    "date": "2026-07-13",
                    "type": "FDE internal sync",
                    "title": "Checkout facts",
                    "source": "notes.md",
                    "participants": ["FDE-A"],
                    "summary": "Checkout validation is running.",
                    "started_at": "2026-07-13T09:00:00+08:00",
                    "ended_at": "2026-07-13T09:20:00+08:00",
                    "lineage": self.vnext_lineage(),
                },
                "items": [{"id": "M-001", "classification": "fact", "text": "Payment API remains candidate.", "affected_workstreams": ["l1-checkout"]}],
            }
            result = self.run_script_with_args(project_root, plan, "--language", language)
            archive = Path(result["touched"]["meeting_archives"][0]).read_text(encoding="utf-8")
            return result, archive

        chinese, chinese_text = run("Chinese")
        english, english_text = run("English")
        self.assertEqual(chinese["language"]["locale"], "zh")
        self.assertIn("## 摘要", chinese_text)
        self.assertIn("Payment API remains candidate.", chinese_text)
        self.assertIn("| M-001 | fact |", chinese_text)
        self.assertIn("ps-meeting-pack-fixture", chinese_text)
        self.assertEqual(english["language"]["locale"], "en")
        self.assertIn("## Summary", english_text)
        self.assertIn("Payment API remains candidate.", english_text)
        self.assertIn("ps-meeting-pack-fixture", english_text)


if __name__ == "__main__":
    unittest.main()
