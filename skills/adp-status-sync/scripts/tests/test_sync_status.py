import hashlib
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
    def test_context_resolves_config_precedence_and_memory_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            primary = project_root / "_bmad/adp/config.yaml"
            primary.parent.mkdir(parents=True)
            primary.write_text("communication_language: Chinese\n", encoding="utf-8")
            fallback = project_root / "_bmad/config.yaml"
            fallback.write_text("document_output_language: English\n", encoding="utf-8")
            memory_root = project_root / "_bmad-output/adp/memory"
            memory_root.mkdir(parents=True)

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "context", str(project_root)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["config_path"], str(primary.resolve()))
            self.assertEqual(result["communication_language"], "Chinese")
            self.assertEqual(result["document_output_language"], "English")
            self.assertEqual(result["language_sources"]["communication_language"], str(primary.resolve()))
            self.assertEqual(result["language_sources"]["document_output_language"], str(fallback.resolve()))
            self.assertEqual(result["memory_root"], str(memory_root.resolve()))
            self.assertTrue(result["memory_root_exists"])

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
                                        "operation": "create",
                                        "command_id": "CMD-A-FLOW-1",
                                        "action_id": "A-FLOW-1",
                                        "status": "in-progress",
                                        "owner": "FDE-A",
                                        "action": "Close checkout gate",
                                        "source": "meeting#1",
                                        "evidence": [{"source": "meeting#1"}],
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

    def test_action_flow_fails_closed_for_missing_or_nonmonotonic_lifecycle_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            updates_file = project_root / "action-flow-invalid-time.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "baseline_revision": 3,
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "operation": "create",
                                        "command_id": "CMD-A-OPEN-TIME",
                                        "action_id": "A-OPEN-TIME",
                                        "status": "open",
                                        "owner": "FDE-A",
                                        "action": "Open action",
                                        "source": "meeting#time",
                                        "evidence": [{"source": "meeting#time#open"}],
                                        "related_plan_item_ids": ["MS-CHECKOUT-COMPLETE"],
                                    },
                                    {
                                        "operation": "create",
                                        "command_id": "CMD-A-BLOCKED-TIME",
                                        "action_id": "A-BLOCKED-TIME",
                                        "status": "blocked",
                                        "owner": "FDE-A",
                                        "action": "Blocked action",
                                        "source": "meeting#time",
                                        "evidence": [{"source": "meeting#time#blocked"}],
                                        "related_plan_item_ids": ["MS-CHECKOUT-COMPLETE"],
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            first = self.run_script(project_root, "update", str(project_root), "--updates-file", str(updates_file))
            first_contract = json.loads(Path(first["action_flow"]).read_text(encoding="utf-8"))
            blocked = next(item for item in first_contract["actions"] if item["action_id"] == "A-BLOCKED-TIME")
            self.assertIsNotNone(blocked["started_at"])

            ledger = Path(first["action_ledger"])
            lines = ledger.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if line.startswith("| A-BLOCKED-TIME |"):
                    cells = [cell.strip() for cell in line.strip("|").split("|")]
                    cells[12] = ""
                    lines[index] = "| " + " | ".join(cells) + " |"
                elif line.startswith("| A-OPEN-TIME |"):
                    cells = [cell.strip() for cell in line.strip("|").split("|")]
                    cells[11] = "2099-01-01T00:00:00Z"
                    lines[index] = "| " + " | ".join(cells) + " |"
            ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

            updates_file.write_text(
                json.dumps(
                    {
                        "baseline_revision": 3,
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "operation": "create",
                                        "command_id": "CMD-A-VALID-TIME",
                                        "action_id": "A-VALID-TIME",
                                        "status": "open",
                                        "owner": "FDE-A",
                                        "action": "Valid action",
                                        "source": "meeting#time",
                                        "evidence": [{"source": "meeting#time#valid"}],
                                        "related_plan_item_ids": ["MS-CHECKOUT-COMPLETE"],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            second = self.run_script(
                project_root,
                "update",
                str(project_root),
                "--updates-file",
                str(updates_file),
                check=False,
            )
            self.assertFalse(second["ok"])
            self.assertEqual(second["error_code"], "ACTION_LEDGER_STATE_MISMATCH")
            action_ids = {item["action_id"] for item in first_contract["actions"]}
            self.assertNotIn("A-VALID-TIME", action_ids)

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
            self.assertEqual(result["input_path"], str(updates_file.resolve()))
            self.assertEqual(
                result["input_hash"],
                f"sha256:{hashlib.sha256(updates_file.read_bytes()).hexdigest()}",
            )
            receipt_path = Path(result["receipt_path"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["receipt_schema_version"], 1)
            self.assertEqual(receipt["receipt_type"], "execution")
            self.assertEqual(receipt["status"], "applied")
            self.assertFalse(receipt["dry_run"])
            self.assertTrue(receipt["durable"])
            self.assertEqual(receipt["input_path"], str(updates_file.resolve()))
            self.assertEqual(receipt["input_hash"], f"sha256:{hashlib.sha256(updates_file.read_bytes()).hexdigest()}")
            self.assertEqual(receipt["mode"], "update")
            self.assertEqual(receipt["update_count"], 2)
            self.assertTrue(receipt["applied_at"])
            self.assertIn(
                "- Current ADP status: blocked",
                (project_root / "_bmad-output" / "adp" / "memory" / "workstreams" / "l2-search" / "delivery-record.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_updates_file_dry_run_returns_only_preview_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            before = record.read_bytes()
            updates_file = project_root / "preview-updates.json"
            updates_file.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "progress": "Preview only"}]}) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "update",
                    str(project_root),
                    "--updates-file",
                    str(updates_file),
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertEqual(record.read_bytes(), before)
            self.assertIsNone(result["receipt_path"])
            self.assertEqual(result["receipt"]["status"], "preview")
            self.assertTrue(result["receipt"]["dry_run"])
            self.assertFalse(result["receipt"]["durable"])
            self.assertIsNone(result["receipt"]["applied_at"])
            receipt_root = project_root / "_bmad-output/adp/memory/receipts/status-sync"
            self.assertFalse(receipt_root.exists())

    def test_historical_success_requires_explicit_migration_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            updates_file = project_root / "historical-updates.json"
            updates_file.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "progress": "Historically applied"}]}) + "\n",
                encoding="utf-8",
            )
            evidence_file = project_root / "historical-report.json"
            input_hash = f"sha256:{hashlib.sha256(updates_file.read_bytes()).hexdigest()}"
            evidence_file.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "update",
                        "dry_run": False,
                        "updates": [{"ok": True}],
                        "input_path": str(updates_file.resolve()),
                        "input_hash": input_hash,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(SCRIPT),
                "migrate-receipt",
                str(project_root),
                "--updates-file",
                str(updates_file),
                "--evidence-file",
                str(evidence_file),
                "--applied-at",
                "2026-07-10T10:00:00+08:00",
                "--attested-by",
                "PMO-A",
            ]
            preview = subprocess.run(
                [*command, "--dry-run"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            preview_result = json.loads(preview.stdout)
            self.assertEqual(preview_result["verification_status"], "verified")
            self.assertTrue(preview_result["dry_run"])
            self.assertIsNone(preview_result["receipt_path"])
            self.assertFalse((project_root / "_bmad-output/adp/memory/receipts/status-sync").exists())

            missing_token = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(missing_token.returncode, 0)
            self.assertIn("verified-plan-token", json.loads(missing_token.stdout)["error"])

            completed = subprocess.run(
                [*command, "--verified-plan-token", preview_result["verified_plan_token"]],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))

            self.assertEqual(result["verification_status"], "verified")
            self.assertEqual(receipt["receipt_type"], "migration")
            self.assertEqual(receipt["migration"]["attested_by"], "PMO-A")
            self.assertEqual(receipt["migration"]["verification_status"], "verified")
            self.assertEqual(receipt["migration"]["evidence_input_path"], str(updates_file.resolve()))
            self.assertEqual(receipt["migration"]["evidence_input_hash"], input_hash)
            self.assertEqual(receipt["migration"]["evidence_path"], str(evidence_file.resolve()))
            self.assertEqual(
                receipt["migration"]["evidence_hash"],
                f"sha256:{hashlib.sha256(evidence_file.read_bytes()).hexdigest()}",
            )

            same_name_elsewhere = project_root / "elsewhere" / updates_file.name
            same_name_elsewhere.parent.mkdir()
            same_name_elsewhere.write_bytes(updates_file.read_bytes())
            evidence_file.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "update",
                        "dry_run": False,
                        "updates": [{"ok": True}],
                        "input_path": str(same_name_elsewhere.resolve()),
                        "input_hash": input_hash,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            unverified = subprocess.run(
                [*command, "--dry-run"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            unverified_result = json.loads(unverified.stdout)
            self.assertEqual(unverified_result["verification_status"], "unverified")
            self.assertIn("exact updates-file path", unverified_result["reason"])
            self.assertIsNone(unverified_result["receipt"])

    def test_wrapper_attestation_cannot_self_prove_historical_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            updates_file = project_root / "historical-updates.json"
            updates_file.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "progress": "Historically applied"}]}) + "\n",
                encoding="utf-8",
            )
            original_report = {
                "ok": True,
                "mode": "update",
                "dry_run": False,
                "updates": [{"ok": True}],
            }
            wrapper = {
                **original_report,
                "receipt": {
                    "input_path": str(updates_file.resolve()),
                    "input_hash": f"sha256:{hashlib.sha256(updates_file.read_bytes()).hexdigest()}",
                    "attested_by": "PMO-A",
                },
            }
            evidence_file = project_root / "post-hoc-attestation.json"
            evidence_file.write_text(json.dumps(wrapper) + "\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "migrate-receipt",
                    str(project_root),
                    "--updates-file",
                    str(updates_file),
                    "--evidence-file",
                    str(evidence_file),
                    "--applied-at",
                    "2026-07-10T10:00:00+08:00",
                    "--attested-by",
                    "PMO-A",
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertEqual(result["verification_status"], "unverified")
            self.assertIn("directly declare", result["reason"])
            self.assertIsNone(result["receipt"])
            self.assertFalse((project_root / "_bmad-output/adp/memory/receipts/status-sync").exists())

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

    def test_legacy_create_replay_is_noop_but_changed_input_gets_new_identity(self) -> None:
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
                                        "closure_criteria_verifiable": True,
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
            ledger = project_root / "_bmad-output" / "adp" / "memory" / "actions" / "action-ledger.md"
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            second_result = json.loads(second.stdout)
            changed_registration = json.loads(updates_file.read_text(encoding="utf-8"))
            changed_registration["updates"][0]["actions"][0]["status"] = "blocked"
            updates_file.write_text(json.dumps(changed_registration), encoding="utf-8")
            third = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            third_result = json.loads(third.stdout)

            self.assertTrue(first_result["ok"])
            self.assertEqual(len(first_result["actions_registered"]), 1)
            self.assertEqual(second_result["status"], "already-applied")
            self.assertEqual(second_result["actions_registered"], [])
            self.assertEqual(second_result["actions_updated"], [])
            self.assertEqual(len(third_result["actions_registered"]), 1)
            self.assertEqual(third_result["actions_updated"], [])
            action_ids = [
                first_result["actions_registered"][0],
                third_result["actions_registered"][0],
            ]
            self.assertEqual(len(set(action_ids)), 2)
            ledger_text = ledger.read_text(encoding="utf-8")
            self.assertEqual(ledger_text.count("Add checkout validation evidence"), 2)
            self.assertIn(f"| {first_result['actions_registered'][0]} | open |", ledger_text)
            self.assertIn(f"| {third_result['actions_registered'][0]} | blocked |", ledger_text)
            self.assertIn("Closure Criteria Verifiable", ledger_text)
            self.assertIn("| true |", ledger_text)
            updated = record.read_text(encoding="utf-8")
            self.assertIn("FDE-A send summary", updated)
            self.assertNotIn("Add checkout validation evidence", updated)

    def test_command_id_replays_generated_action_id_without_text_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            updates_file = project_root / "command-create.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "actions": [
                                    {
                                        "command_id": "CMD-GENERATED-ID-001",
                                        "owner": "FDE-A",
                                        "action": "Publish command-bound evidence",
                                        "source": "meeting#command",
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
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            first_result = json.loads(first.stdout)
            second_result = json.loads(second.stdout)
            action_id = first_result["actions_registered"][0]
            ledger = Path(first_result["action_ledger"])

            self.assertEqual(second_result["actions_registered"], [])
            self.assertEqual(second_result["actions_no_op"], [action_id])
            self.assertEqual(ledger.read_text(encoding="utf-8").count("Publish command-bound evidence"), 1)

    def test_explicit_empty_next_actions_clears_without_generated_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            updates = project_root / "updates.json"
            updates.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "next_actions": []}]}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            updated = record.read_text(encoding="utf-8")

            self.assertTrue(result["ok"])
            self.assertIn("- Next actions: \n", updated)
            self.assertNotIn("fill missing state", updated)
            self.assertNotIn("Add checkout validation evidence", updated)

    def test_action_summary_always_renders_structured_owner_and_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            updates_file = project_root / "summary.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "refresh_actions": True,
                                "actions": [
                                    {
                                        "owner": "Ann",
                                        "action": "Planning review Friday",
                                        "source": "meeting#summary",
                                        "due": "Friday",
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
            action_id = result["actions_registered"][0]

            self.assertIn(
                f"[action_id:{action_id}] Ann: Planning review Friday (due: Friday)",
                record.read_text(encoding="utf-8"),
            )

    def test_refresh_actions_only_is_a_reliable_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--id", "l1-checkout", "--refresh-actions", "--dry-run"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertEqual([], result["updates"][0]["unresolved_gaps"])

    def test_mistyped_action_id_does_not_fall_back_to_matching_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            registration_file = project_root / "register.json"
            action = {
                "owner": "FDE-A",
                "workstream": "l1-checkout",
                "action": "Publish checkout evidence",
                "source": "meeting#identity",
                "due": "Friday",
            }
            registration_file.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "refresh_actions": True, "actions": [action]}]}),
                encoding="utf-8",
            )
            registered = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(registration_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            action_id = json.loads(registered.stdout)["actions_registered"][0]
            close_file = project_root / "close-wrong-id.json"
            close_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "refresh_actions": True,
                                "actions": [{**action, "action_id": "ACT-WRONG", "status": "done"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(close_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            ledger = project_root / "_bmad-output/adp/memory/actions/action-ledger.md"
            record = project_root / "_bmad-output/adp/memory/workstreams/l1-checkout/delivery-record.md"

            self.assertEqual(result["actions_closed"], [])
            self.assertIn(f"| {action_id} | open |", ledger.read_text(encoding="utf-8"))
            self.assertNotIn("| ACT-WRONG |", ledger.read_text(encoding="utf-8"))
            self.assertIn(f"[action_id:{action_id}]", record.read_text(encoding="utf-8"))
            self.assertTrue(any("ACT-WRONG: close/update action was not found" in gap for gap in result["unresolved_gaps"]))

    def test_terminal_action_update_requires_action_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            original = record.read_bytes()
            updates_file = project_root / "close-without-id.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "refresh_actions": True,
                                "actions": [
                                    {
                                        "status": "cancelled",
                                        "action": "Publish checkout evidence",
                                        "source": "meeting#identity",
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

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("cancelled action update requires action_id", json.loads(completed.stdout)["error"])
            self.assertEqual(record.read_bytes(), original)
            self.assertFalse((project_root / "_bmad-output/adp/memory/actions/action-ledger.md").exists())

    def test_action_quality_gaps_come_from_prompt_not_phrase_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            updates_file = project_root / "interpreted-actions.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "l1-checkout",
                                "unresolved_gaps": [
                                    "Owner is a role label; confirm an accountable person",
                                    "Closure criteria needs a verifiable artifact",
                                ],
                                "actions": [
                                    {
                                        "owner": "FDE owner",
                                        "action": "Publish checkout status",
                                        "source": "meeting#1",
                                        "due": "Friday",
                                        "closure_criteria": "daily log",
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
            gaps = json.loads(completed.stdout)["updates"][0]["unresolved_gaps"]

            self.assertIn("Owner is a role label; confirm an accountable person", gaps)
            self.assertIn("Closure criteria needs a verifiable artifact", gaps)
            self.assertNotIn("Owner is missing", "\n".join(gaps))
            self.assertNotIn("Closure Criteria is missing", "\n".join(gaps))

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

    def test_done_action_removes_only_stable_id_summary_and_preserves_legacy_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(
                project_root,
                text=RECORD.replace(
                    "- Next actions: fill missing state",
                    "- Next actions: [action_id:ACT-20260701-001] FDE-A: Add checkout validation evidence (due: Friday); "
                    "FDE-B: Add checkout validation evidence for production (due: Monday)",
                ),
            )
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
                                "refresh_actions": True,
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
            self.assertNotIn("[action_id:ACT-20260701-001]", updated)
            self.assertIn("FDE-B: Add checkout validation evidence for production (due: Monday)", updated)

    def test_dependency_only_preserves_next_actions_in_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(
                project_root,
                text=RECORD.replace("- Next actions: fill missing state", "- Next actions: Human wording; punctuation stays!"),
            )
            original = record.read_bytes()

            dry = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--id", "l1-checkout", "--dependency", "l2 ready", "--dry-run"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(original, record.read_bytes())
            dry_fields = {item["field"] for item in json.loads(dry.stdout)["updates"][0]["changed_fields"]}
            self.assertEqual({"Dependencies", "Last status sync"}, dry_fields)

            applied = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--id", "l1-checkout", "--dependency", "l2 ready"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            updated = record.read_text(encoding="utf-8")
            self.assertIn("- Next actions: Human wording; punctuation stays!", updated)
            fields = {item["field"] for item in json.loads(applied.stdout)["updates"][0]["changed_fields"]}
            self.assertEqual({"Dependencies", "Last status sync"}, fields)

    def test_structured_action_requires_refresh_to_change_wdr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record = self.create_record(project_root)
            original = record.read_bytes()
            updates_file = project_root / "action-only.json"
            updates_file.write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "actions": [{"owner": "Ann", "action": "Publish evidence", "source": "meeting#1"}]}]}),
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

            self.assertEqual(original, record.read_bytes())
            self.assertEqual([], result["updates"][0]["changed_fields"])
            self.assertTrue(Path(result["updates"][0]["daily_log"]).is_file())
            self.assertEqual(1, len(result["actions_registered"]))

    def test_program_action_only_is_allowed_but_wdr_projection_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            updates_file = project_root / "program-action.json"
            updates_file.write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "id": "PROGRAM",
                                "next_actions": [],
                                "actions": [
                                    {"owner": "PMO", "action": "Publish program note", "source": "meeting#program"}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            allowed = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(updates_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            allowed_result = json.loads(allowed.stdout)
            memory = project_root / "_bmad-output/adp/memory"
            self.assertTrue(allowed_result["ok"])
            self.assertTrue((memory / "actions/action-ledger.md").is_file())
            self.assertTrue(any((memory / "daily").glob("*.md")))
            self.assertFalse((memory / "workstreams/program/delivery-record.md").exists())

            rejected = subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--id", "program", "--refresh-actions"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            rejected_result = json.loads(rejected.stdout)
            self.assertEqual(2, rejected.returncode)
            self.assertEqual("ADP-VIRTUAL-SCOPE-NOT-WDR-TARGET", rejected_result["error_code"])


    def test_reconcile_intake_validates_milestone_baseline_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.create_record(project_root)
            baseline = self.create_baseline(project_root)
            payload = {
                "baseline_revision": 3,
                "updates": [{"id": "l1-checkout", "milestones": [{
                    "milestone_id": "MS-CHECKOUT-COMPLETE",
                    "status": "at-risk",
                    "forecast": "2026-10-20",
                    "evidence": ["workstreams/l1-checkout/evidence.md#forecast-20261020"],
                }]}],
            }
            applied_input = project_root / "applied-milestone.json"
            applied_input.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(SCRIPT), "update", str(project_root), "--updates-file", str(applied_input)],
                check=True, capture_output=True, text=True, encoding="utf-8",
            )
            intake = project_root / "historical-milestone.json"
            intake.write_text(json.dumps(payload), encoding="utf-8")
            preview = json.loads(subprocess.run(
                [sys.executable, str(SCRIPT), "reconcile-intake", str(project_root), "--updates-file", str(intake), "--dry-run"],
                check=True, capture_output=True, text=True, encoding="utf-8",
            ).stdout)
            self.assertTrue(preview["all_satisfied"])

            baseline.write_text(baseline.read_text(encoding="utf-8").replace('"revision": 3', '"revision": 4', 1), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "reconcile-intake", str(project_root), "--updates-file", str(intake), "--token", preview["token"]],
                check=False, capture_output=True, text=True, encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "INTAKE_RECONCILIATION_FACTS_STALE")
            self.assertEqual(result["missing_commands"][0]["command_type"], "milestone")

    def test_writer_merges_identical_duplicate_canonical_fields_before_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            duplicate = RECORD.replace(
                "- Progress: TBD",
                "- Progress: TBD\n- Progress: TBD",
            ).replace(
                "- Blockers: TBD",
                "- Blockers: TBD\n- Blockers: TBD",
            )
            record = self.create_record(project_root, text=duplicate)

            result = self.run_script(project_root, "update", str(project_root), "--id", "l1-checkout", "--progress", "Merged")
            self.assertTrue(result["ok"])
            text = record.read_text(encoding="utf-8")
            self.assertEqual(text.count("- Progress:"), 1)
            self.assertEqual(text.count("- Blockers:"), 1)
            repairs = [item for item in result["updates"][0]["changed_fields"] if item.get("repair")]
            self.assertEqual({item["field"] for item in repairs}, {"Progress", "Blockers"})

    def test_writer_fails_closed_for_conflicting_duplicate_canonical_fields(self) -> None:
        for label in ("Next actions", "Progress", "Blockers"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                marker = next(line for line in RECORD.splitlines() if line.startswith(f"- {label}:"))
                duplicate = RECORD.replace(marker, marker + f"\n- {label}: conflicting historical value")
                record = self.create_record(project_root, text=duplicate)
                before = record.read_bytes()

                completed = subprocess.run(
                    [sys.executable, str(SCRIPT), "update", str(project_root), "--id", "l1-checkout", "--status", "active"],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                result = json.loads(completed.stdout)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(result["error_code"], "WDR_DUPLICATE_CANONICAL_FIELD")
                self.assertEqual(result["field"], label)
                self.assertEqual(result["repair_plan"]["operation"], "deduplicate-canonical-field")
                self.assertEqual(record.read_bytes(), before)

if __name__ == "__main__":
    unittest.main()
