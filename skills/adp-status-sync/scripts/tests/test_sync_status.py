import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_status.py"
FLOW_TEST_ROOT = Path(__file__).resolve().parents[3] / "adp-flow-graph/scripts/tests"
sys.path.insert(0, str(FLOW_TEST_ROOT))
from flow_contract_testkit import load_json as load_contract_json, validate_schema  # noqa: E402
ACTION_SCHEMA = Path(__file__).resolve().parents[2] / "assets/action-flow-relation-v1.schema.json"


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
    def test_action_flow_contract_tracks_lifecycle_and_explicit_relations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            updates_file = project_root / "action-flow.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "baseline_revision": 3,
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "action_id": "A-FLOW-1",
                                        "status": "in-progress",
                                        "owner": "FDE-A",
                                        "action": "Close checkout gate",
                                        "source": "meeting#1",
                                        "related_plan_item_ids": ["MS-CHECKOUT-COMPLETE"],
                                        "related_flow_edge_ids": ["E-CHECKOUT-MERGE"],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            started = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            started_result = json.loads(started.stdout)
            action_flow_path = Path(started_result["action_flow"])
            contract = json.loads(action_flow_path.read_text(encoding="utf-8"))
            action = contract["actions"][0]

            self.assertEqual(contract["action_flow_schema_version"], "1.0.0")
            self.assertEqual(action["status"], "in-progress")
            self.assertIsNotNone(action["started_at"])
            self.assertIsNone(action["done_at"])
            self.assertEqual(action["baseline_revision"], 3)
            self.assertEqual(action["related_plan_item_ids"], ["MS-CHECKOUT-COMPLETE"])
            self.assertEqual(action["related_flow_edge_ids"], ["E-CHECKOUT-MERGE"])
            self.assertEqual(validate_schema(contract, load_contract_json(ACTION_SCHEMA)), [])

            updates_file.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "actions": [{"action_id": "A-FLOW-1", "status": "done"}]}]}),
                encoding="utf-8",
            )
            finished = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            finished_action = json.loads(Path(json.loads(finished.stdout)["action_flow"]).read_text(encoding="utf-8"))["actions"][0]
            self.assertEqual(finished_action["status"], "done")
            self.assertIsNotNone(finished_action["done_at"])
            self.assertEqual(finished_action["related_plan_item_ids"], ["MS-CHECKOUT-COMPLETE"])

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

    def create_baseline(self, project_root: Path, revision: int = 3) -> Path:
        baseline = project_root / "_bmad-output" / "adp" / "memory" / "plans" / "program-baseline.md"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        model = {
            "schema_version": "1.0",
            "baseline_id": "PROGRAM-BASELINE",
            "revision": revision,
            "confirmation_status": "approved",
            "project": {"name": "Demo", "owner": "PMO"},
            "default_tolerance_days": 0,
            "gates": [],
            "milestones": [
                {
                    "id": "MS-CHECKOUT-COMPLETE",
                    "name": "Checkout migration complete",
                    "workstream_id": "l1-checkout",
                    "planned_date": "2026-10-15",
                    "owner": "Checkout FDE",
                    "confirmation_status": "approved",
                    "source": {
                        "type": "approved-plan",
                        "reference": "docs/delivery-plan.md#checkout",
                        "confirmed_by": "PMO",
                    },
                    "dependencies": ["GATE-DESIGN-APPROVED"],
                    "baseline_revision": revision,
                }
            ],
            "critical_path": ["MS-CHECKOUT-COMPLETE"],
            "weighting": {"enabled": False, "completion_measure": None, "source": None},
        }
        baseline.write_text(
            "# Program Baseline\n\n<!-- adp:program-baseline:v1 -->\n\n```json\n"
            + json.dumps(model, indent=2)
            + "\n```\n",
            encoding="utf-8",
        )
        return baseline

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

    def test_unsupported_action_status_is_rejected_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            before = record.read_bytes()
            updates_file = project_root / "invalid-action-status.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "owner": "FDE-A",
                                        "action": "Confirm payment evidence",
                                        "status": "almost-done",
                                        "source": "meeting#1",
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
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            memory = project_root / "_bmad-output/adp/memory"

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("action status must be one of", result["error"])
            self.assertEqual(record.read_bytes(), before)
            self.assertFalse((memory / "actions/action-ledger.md").exists())
            self.assertFalse((memory / "daily").exists())

    def test_batch_preflight_failure_keeps_all_canonical_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            before = record.read_bytes()
            updates_file = project_root / "partially-invalid-batch.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "status": "at-risk",
                                "actions": [
                                    {
                                        "owner": "FDE-A",
                                        "action": "Confirm payment evidence",
                                        "source": "meeting#1",
                                    }
                                ],
                            },
                            {"id": "l2-missing", "status": "blocked"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            memory = project_root / "_bmad-output/adp/memory"

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("delivery-record.md not found for workstream l2-missing", result["error"])
            self.assertEqual(record.read_bytes(), before)
            self.assertFalse((memory / "actions/action-ledger.md").exists())
            self.assertFalse((memory / "daily").exists())

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

    def test_milestone_update_maps_to_baseline_and_preserves_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            baseline = self.create_baseline(project_root)
            baseline_before = baseline.read_text(encoding="utf-8")
            updates_file = project_root / "milestones.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "baseline_revision": 3,
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "source": "owner update",
                                "milestones": [
                                    {
                                        "milestone_id": "MS-CHECKOUT-COMPLETE",
                                        "status": "at-risk",
                                        "forecast": "2026-10-20",
                                        "evidence": ["workstreams/l1-checkout/evidence.md#forecast-20261020"],
                                    }
                                ],
                            }
                        ],
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
            self.assertEqual(result["baseline_revision"], 3)
            self.assertEqual(result["updates"][0]["milestones_updated"], ["MS-CHECKOUT-COMPLETE"])
            updated = record.read_text(encoding="utf-8")
            self.assertIn("## Roadmap", updated)
            self.assertIn("| Milestone ID | Milestone |", updated)
            self.assertIn(
                "| MS-CHECKOUT-COMPLETE | Checkout migration complete | checkpoint | at-risk | 2026-10-15 | 2026-10-20 | TBD | Checkout FDE | low | GATE-DESIGN-APPROVED | workstreams/l1-checkout/evidence.md#forecast-20261020 | 3 |",
                updated,
            )
            self.assertEqual(baseline.read_text(encoding="utf-8"), baseline_before)
            daily_log = Path(result["updates"][0]["daily_log"]).read_text(encoding="utf-8")
            self.assertIn("MS-CHECKOUT-COMPLETE: at-risk (forecast 2026-10-20)", daily_log)
            self.assertIn("Evidence: workstreams/l1-checkout/evidence.md#forecast-20261020", daily_log)

    def test_milestone_update_is_idempotent_by_stable_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            self.create_baseline(project_root)
            updates_file = project_root / "milestones.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "milestones": [
                                    {
                                        "milestone_id": "MS-CHECKOUT-COMPLETE",
                                        "status": "done",
                                        "actual": "2026-10-14",
                                        "evidence": "workstreams/l1-checkout/evidence.md#accepted",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            command = [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)]
            subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
            second = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
            second_result = json.loads(second.stdout)

            self.assertEqual(record.read_text(encoding="utf-8").count("MS-CHECKOUT-COMPLETE"), 1)
            self.assertEqual(second_result["updates"][0]["milestones_updated"], [])

    def test_milestone_dry_run_preserves_legacy_roadmap_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            legacy_roadmap = """

## Roadmap

| Milestone | Type | Status | Planned | Forecast | Actual | Owner | Confidence | Depends On | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Legacy release | delivery-window | planned | 2026-09-01 | TBD | TBD | FDE-A | low | TBD | docs/legacy-plan.md#release |
"""
            record = self.create_record(project_root, text=RECORD.replace("\n## Record Rule", legacy_roadmap + "\n## Record Rule"))
            self.create_baseline(project_root)
            before = record.read_text(encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "update",
                    str(project_root),
                    "--id",
                    "l1-checkout",
                    "--milestone-id",
                    "MS-CHECKOUT-COMPLETE",
                    "--milestone-status",
                    "planned",
                    "--milestone-evidence",
                    "owner-update#milestone",
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["updates"][0]["milestones_updated"], ["MS-CHECKOUT-COMPLETE"])
            self.assertEqual(record.read_text(encoding="utf-8"), before)
            self.assertIn("Legacy release", before)
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "daily").exists())

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "update",
                    str(project_root),
                    "--id",
                    "l1-checkout",
                    "--milestone-id",
                    "MS-CHECKOUT-COMPLETE",
                    "--milestone-status",
                    "planned",
                    "--milestone-evidence",
                    "owner-update#milestone",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            migrated = record.read_text(encoding="utf-8")
            self.assertIn("| Milestone ID | Milestone |", migrated)
            self.assertIn("|  | Legacy release | delivery-window |", migrated)
            self.assertIn("| MS-CHECKOUT-COMPLETE | Checkout migration complete |", migrated)

    def test_unknown_milestone_blocks_all_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            self.create_baseline(project_root)
            before = record.read_text(encoding="utf-8")
            updates_file = project_root / "invalid.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "owner": "FDE-A",
                                        "action": "Should not be written",
                                        "source": "meeting#1",
                                    }
                                ],
                                "milestones": [
                                    {
                                        "milestone_id": "MS-UNKNOWN",
                                        "status": "planned",
                                        "evidence": ["owner-update#1"],
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
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unknown baseline milestone MS-UNKNOWN", result["error"])
            self.assertEqual(record.read_text(encoding="utf-8"), before)
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "actions" / "action-ledger.md").exists())
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "daily").exists())

    def test_milestone_workstream_and_revision_must_match_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root, "l2-search", RECORD.replace("l1-checkout", "l2-search"))
            self.create_baseline(project_root, revision=4)
            for workstream_id, expected_revision, expected_error in [
                ("l2-search", 4, "belongs to workstream l1-checkout"),
                ("l1-checkout", 3, "expected baseline revision 3, found 4"),
            ]:
                if workstream_id == "l1-checkout":
                    self.create_record(project_root)
                updates_file = project_root / f"invalid-{workstream_id}.json"
                updates_file.write_text(
                    json.dumps(
                        {
                            "baseline_revision": expected_revision,
                            "updates": [
                                {
                                    "id": workstream_id,
                                    "milestones": [
                                        {
                                            "milestone_id": "MS-CHECKOUT-COMPLETE",
                                            "status": "planned",
                                            "evidence": ["owner-update#1"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertIn(expected_error, json.loads(completed.stdout)["error"])

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
