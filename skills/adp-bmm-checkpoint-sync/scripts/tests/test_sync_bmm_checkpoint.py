import json
import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_bmm_checkpoint.py"
STATUS_SYNC = Path(__file__).resolve().parents[3] / "adp-status-sync" / "scripts" / "sync_status.py"
BOOTSTRAP = Path(__file__).resolve().parents[3] / "adp-project-kickoff" / "scripts" / "bootstrap_adp.py"
REGISTER = Path(__file__).resolve().parents[3] / "adp-workstream-register" / "scripts" / "register_workstream.py"

sys.path.insert(0, str(SCRIPT.parent))

from sync_bmm_checkpoint import candidate_to_sync_args


class SyncBmmCheckpointTests(unittest.TestCase):
    def register_workstream(self, project_root: Path) -> dict:
        subprocess.run(
            [
                sys.executable,
                str(BOOTSTRAP),
                str(project_root),
                "--project-name",
                "Checkpoint Sync Test",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(REGISTER),
                str(project_root),
                "--id",
                "L1 Checkout",
                "--name",
                "Checkout Migration",
                "--owner",
                "FDE-A",
                "--business-owner",
                "Biz-A",
                "--phase",
                "draft",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def run_command(self, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def run_dry_run_report(self, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        report_path = ""
        for line in completed.stdout.splitlines():
            if line.startswith("report_path:"):
                report_path = line.split(":", 1)[1].strip()
                break
        self.assertTrue(report_path, completed.stdout)
        report = Path(report_path)
        self.assertTrue(report.exists())
        return json.loads(report.read_text(encoding="utf-8"))

    def write_prd(self, project_root: Path) -> Path:
        docs = project_root / "docs"
        docs.mkdir(exist_ok=True)
        prd = docs / "prd.md"
        prd.write_text("# Checkout PRD\n\n## Scope\n\n- Checkout order flow migration\n", encoding="utf-8")
        return prd

    def discover_confirm_candidate(self, project_root: Path, *overrides: str) -> str:
        prd = self.write_prd(project_root)
        discovered = self.run_command(
            "discover",
            str(project_root),
            "--workstream-id",
            "L1 Checkout",
            "--checkpoint",
            "prd",
            "--artifact",
            f"prd={prd}",
            "--summary",
            "Checkout PRD baseline ready for project review",
            "--asserted-by",
            "FDE-A",
        )
        candidate_id = discovered["candidate_id"]
        confirm_args = [
            "confirm",
            str(project_root),
            "--candidate-id",
            candidate_id,
            "--confirmed-by",
            "FDE-A",
            "--override",
            "authority.confirmation_state=confirmed-local",
            "--override",
            'claims.business_confirmation=["Biz-A owns final confirmation"]',
        ]
        for override in overrides:
            confirm_args.extend(["--override", override])
        self.run_command(*confirm_args)
        return candidate_id

    def test_prd_checkpoint_updates_record_and_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)

            result = self.run_script(
                project_root,
                "--workstream-id",
                "L1 Checkout",
                "--checkpoint",
                "prd",
                "--summary",
                "Checkout PRD baseline ready for project review",
                "--artifact",
                "prd=docs/prd-checkout.md",
                "--artifact-status",
                "baseline",
                "--scope",
                "Checkout order flow migration",
                "--acceptance",
                "Business owner confirms checkout parity",
                "--evidence-required",
                "Demo and test proof mapped to checkout criteria",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--next-action",
                "FDE-A extracts architecture dependencies",
            )

            self.assertTrue(result["ok"])
            record = Path(setup["workstream_root"]) / "delivery-record.md"
            readiness = Path(setup["workstream_root"]) / "readiness.md"
            record_text = record.read_text(encoding="utf-8")
            readiness_text = readiness.read_text(encoding="utf-8")
            self.assertIn("| PRD | docs/prd-checkout.md | baseline |", record_text)
            self.assertIn("Checkout order flow migration", record_text)
            self.assertIn("Business owner confirms checkout parity", record_text)
            self.assertNotIn("Acceptance criteria not captured from PRD checkpoint", readiness_text)
            self.assertIn("Daily Log", Path(result["daily_log"]).read_text(encoding="utf-8"))

    def test_validation_checkpoint_appends_evidence_and_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)

            result = self.run_script(
                project_root,
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "validation",
                "--summary",
                "Validation smoke tests passed but customer confirmation is pending",
                "--artifact",
                "validation=reports/checkout-validation.md",
                "--evidence",
                "Smoke test report|test|reports/smoke.md|Checkout parity|confirmed|none",
                "--readiness-gap",
                "Customer confirmation pending|Acceptance clarity|FDE-A|Schedule business review|Before acceptance review|Project lead",
            )

            self.assertTrue(result["ok"])
            root = Path(setup["workstream_root"])
            evidence_text = (root / "evidence.md").read_text(encoding="utf-8")
            readiness_text = (root / "readiness.md").read_text(encoding="utf-8")
            record_text = (root / "delivery-record.md").read_text(encoding="utf-8")
            self.assertIn("Smoke test report", evidence_text)
            self.assertIn("Customer confirmation pending", readiness_text)
            self.assertIn("Business or customer confirmation status needs confirmation", readiness_text)
            self.assertIn("| Validation evidence | reports/checkout-validation.md | linked |", record_text)

    def test_missing_record_fails_with_registration_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    temp_dir,
                    "--workstream-id",
                    "missing-line",
                    "--checkpoint",
                    "prd",
                    "--summary",
                    "Should fail",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertIn("adp-workstream-register", result["error"])

    def test_ready_status_rejected_when_required_facts_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(project_root),
                    "--workstream-id",
                    "L1 Checkout",
                    "--checkpoint",
                    "validation",
                    "--summary",
                    "Validation looks complete in prose",
                    "--record-status",
                    "ready",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 1)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertIn("record-status ready rejected", result["error"])
            self.assertIn("validation checkpoint is missing evidence rows", result["validation_failures"])

    def test_candidate_claims_actions_flow_generates_status_sync_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)
            ledger = project_root / "_bmad-output" / "adp" / "memory" / "actions" / "action-ledger.md"
            if ledger.exists():
                ledger.unlink()
            action = {
                "owner": "FDE-A",
                "workstream": "l1-checkout",
                "affected_workstreams": ["l1-checkout"],
                "action": "Link checkout smoke test evidence",
                "source": "workstreams/l1-checkout/readiness.md#validation-gap",
                "reason": "validation checkpoint readiness gap",
                "due_or_trigger": "before acceptance readiness review",
                "status": "open",
                "closure_criteria": "Evidence row links the smoke test report",
                "owning_workflow": "adp-bmm-checkpoint-sync",
            }
            candidate_id = self.discover_confirm_candidate(
                project_root,
                "claims.actions=" + json.dumps([action], separators=(",", ":")),
            )

            synced = self.run_command("sync", str(project_root), "--candidate-id", candidate_id)

            self.assertTrue(synced["ok"])
            self.assertEqual(synced["action_handoff_audit"]["ledger_ready_actions"], 1)
            intake = Path(synced["status_sync_intake_files"][0])
            self.assertTrue(intake.exists())
            payload = json.loads(intake.read_text(encoding="utf-8"))
            self.assertEqual(payload["updates"][0]["id"], "l1-checkout")
            self.assertEqual(payload["updates"][0]["actions"][0]["action"], "Link checkout smoke test evidence")
            self.assertFalse(ledger.exists())

    def test_candidate_next_actions_do_not_generate_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)
            candidate_id = self.discover_confirm_candidate(
                project_root,
                'claims.next_actions=["FDE-A extracts architecture dependencies"]',
            )

            synced = self.run_command("sync", str(project_root), "--candidate-id", candidate_id)

            self.assertEqual(synced["status_sync_intake_files"], [])
            self.assertEqual(synced["action_handoff_audit"]["actions_seen"], 0)
            record_text = (Path(setup["workstream_root"]) / "delivery-record.md").read_text(encoding="utf-8")
            daily_text = Path(synced["daily_log"]).read_text(encoding="utf-8")
            self.assertIn("FDE-A extracts architecture dependencies", record_text)
            self.assertIn("FDE-A extracts architecture dependencies", daily_text)

    def test_candidate_to_sync_args_preserves_claim_actions(self) -> None:
        candidate = {
            "candidate_id": "CHK-UNIT",
            "workstream_id": "l1-checkout",
            "checkpoint": "validation",
            "artifact": {"path": "gate.json", "kind": "gate", "status": "linked", "source_scope_key": "gate:gate.json"},
            "authority": {"asserted_by": "FDE-A", "confirmation_state": "confirmed-local", "required_confirmers": []},
            "claims": {
                "summary": "Validation passed",
                "next_actions": ["legacy WDR summary only"],
                "actions": [
                    {
                        "owner": "FDE-A",
                        "workstream": "l1-checkout",
                        "action": "Link smoke report",
                        "source": "reports/smoke.md",
                        "due_or_trigger": "before readiness review",
                        "closure_criteria": "Smoke report is linked",
                    }
                ],
                "readiness_gaps": ["Validation evidence rows need confirmation"],
                "business_confirmation": ["Biz-A owns final confirmation"],
                "evidence": ["Smoke report"],
            },
        }
        args = argparse.Namespace(project_root=".", memory_root="_bmad-output/adp/memory", dry_run=False, verbose=False)

        sync_args = candidate_to_sync_args(args, candidate)

        self.assertEqual(sync_args.next_action, ["legacy WDR summary only"])
        self.assertEqual(len(sync_args.handoff_actions), 1)
        self.assertEqual(sync_args.handoff_actions[0]["action"], "Link smoke report")
        self.assertEqual(len(sync_args.readiness_gap), 1)

    def test_candidate_applied_rerun_does_not_generate_new_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)
            action = {
                "owner": "FDE-A",
                "workstream": "l1-checkout",
                "action": "Link PRD review evidence",
                "source": "docs/prd.md#review",
                "due_or_trigger": "before architecture",
                "closure_criteria": "Review evidence is linked",
            }
            candidate_id = self.discover_confirm_candidate(
                project_root,
                "claims.actions=" + json.dumps([action], separators=(",", ":")),
            )
            first = self.run_command("sync", str(project_root), "--candidate-id", candidate_id)
            intake_dir = project_root / "_bmad-output" / "adp" / "memory" / "intake" / "status-sync"
            before = sorted(path.name for path in intake_dir.glob("*.json"))

            second = self.run_command("sync", str(project_root), "--candidate-id", candidate_id)

            self.assertTrue(first["status_sync_intake_files"])
            self.assertTrue(second["no_op"])
            self.assertEqual(second["status_sync_intake_files"], [])
            self.assertEqual(before, sorted(path.name for path in intake_dir.glob("*.json")))

    def test_action_file_cross_workstream_program_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)
            action_file = project_root / "program-actions.json"
            action_file.write_text(
                json.dumps(
                    {
                        "actions": [
                            {
                                "owner": "Project Lead",
                                "workstream": "program",
                                "affected_workstreams": ["l1-checkout", "l2-search"],
                                "action": "Confirm checkout-search dependency owner",
                                "source": "docs/architecture.md#cross-line-impact",
                                "reason": "architecture checkpoint cross-workstream dependency",
                                "due_or_trigger": "before epic/story planning",
                                "closure_criteria": "Both owners confirm the dependency route",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_script(
                project_root,
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "architecture",
                "--summary",
                "Architecture cross-line dependency identified",
                "--action-file",
                str(action_file),
            )

            payload = json.loads(Path(result["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(result["action_handoff_audit"]["fanout_suppressed"], 1)
            self.assertEqual(payload["updates"][0]["id"], "program")
            self.assertEqual(len(payload["updates"][0]["actions"]), 1)
            self.assertEqual(payload["updates"][0]["actions"][0]["affected_workstreams"], ["l1-checkout", "l2-search"])

    def test_action_file_cross_workstream_without_route_uses_program(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)
            action_file = project_root / "program-actions.json"
            action_file.write_text(
                json.dumps(
                    {
                        "actions": [
                            {
                                "owner": "Project Lead",
                                "affected_workstreams": ["l1-checkout", "l2-search"],
                                "action": "Confirm checkout-search dependency owner",
                                "source": "docs/architecture.md#cross-line-impact",
                                "due_or_trigger": "before epic/story planning",
                                "closure_criteria": "Both owners confirm the dependency route",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_script(
                project_root,
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "architecture",
                "--summary",
                "Architecture cross-line dependency identified",
                "--action-file",
                str(action_file),
            )

            payload = json.loads(Path(result["status_sync_intake_files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(result["action_handoff_audit"]["ledger_ready_actions"], 1)
            self.assertEqual(result["action_handoff_audit"]["fanout_suppressed"], 1)
            self.assertEqual(payload["updates"][0]["id"], "program")
            self.assertEqual(payload["updates"][0]["actions"][0]["workstream"], "program")

    def test_freeform_action_cli_is_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)

            result = self.run_script(
                project_root,
                "--workstream-id",
                "L1 Checkout",
                "--checkpoint",
                "prd",
                "--summary",
                "PRD action needs follow-up",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--action",
                "FDE-A|Link PRD review evidence|before architecture|Review evidence is linked",
            )

            action = json.loads(Path(result["status_sync_intake_files"][0]).read_text(encoding="utf-8"))["updates"][0]["actions"][0]
            self.assertEqual(action["workstream"], "l1-checkout")
            self.assertNotIn("affected_workstreams", action)

    def test_readiness_gap_without_closure_is_reported_not_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)

            result = self.run_script(
                project_root,
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "validation",
                "--summary",
                "Validation gap remains",
                "--evidence",
                "Smoke test report|test|reports/smoke.md|Checkout parity|confirmed|none",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--readiness-gap",
                "Customer confirmation pending|Acceptance clarity|FDE-A|Schedule business review|Before acceptance review|Project lead",
            )

            self.assertEqual(result["status_sync_intake_files"], [])
            self.assertEqual(result["action_handoff_audit"]["ledger_ready_actions"], 0)
            self.assertIn("Customer confirmation pending", result["action_handoff_audit"]["handoff_gaps"][0])

    def test_checkpoint_action_intake_can_be_consumed_by_status_sync_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)
            result = self.run_script(
                project_root,
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "validation",
                "--summary",
                "Validation follow-up is ready for ledger",
                "--evidence",
                "Smoke test report|test|reports/smoke.md|Checkout parity|confirmed|none",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--action",
                "FDE-A|Link checkout smoke test evidence|before acceptance readiness review|Evidence row links the smoke test report",
            )
            intake = result["status_sync_intake_files"][0]

            first = subprocess.run(
                [sys.executable, str(STATUS_SYNC), "update", str(project_root), "--updates-file", intake],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            second = subprocess.run(
                [sys.executable, str(STATUS_SYNC), "update", str(project_root), "--updates-file", intake],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertTrue(json.loads(first.stdout)["ok"])
            self.assertTrue(json.loads(second.stdout)["ok"])
            ledger = project_root / "_bmad-output" / "adp" / "memory" / "actions" / "action-ledger.md"
            ledger_text = ledger.read_text(encoding="utf-8")
            self.assertEqual(ledger_text.count("Link checkout smoke test evidence"), 1)
            record_text = (Path(setup["workstream_root"]) / "delivery-record.md").read_text(encoding="utf-8")
            self.assertIn("Link checkout smoke test evidence", record_text)

    def test_dry_run_reports_planned_intake_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)

            result = self.run_dry_run_report(
                str(project_root),
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "validation",
                "--summary",
                "Dry-run validation follow-up",
                "--evidence",
                "Smoke test report|test|reports/smoke.md|Checkout parity|confirmed|none",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--action",
                "FDE-A|Link checkout smoke test evidence|before acceptance readiness review|Evidence row links the smoke test report",
                "--dry-run",
            )

            intake = Path(result["status_sync_intake_files"][0])
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["stdout_only"], False)
            self.assertEqual(result["report_path"], result["dry_run_report_path"])
            self.assertTrue(result["report_exists"])
            self.assertTrue(Path(result["report_path"]).exists())
            self.assertTrue(result["can_apply"])
            self.assertEqual(result["apply_blockers"], [])
            self.assertEqual(result["recommended_next_step"], "review_then_apply")
            self.assertIn("--workstream-id l1-checkout", result["apply_command"])
            self.assertIn("-o", result["apply_command"])
            self.assertFalse(intake.exists())

    def test_legacy_dry_run_explicit_output_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)
            report = project_root / "review" / "legacy-dry-run.json"

            result = self.run_dry_run_report(
                str(project_root),
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "prd",
                "--summary",
                "显式输出路径 dry-run",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--dry-run",
                "-o",
                str(report),
            )

            self.assertEqual(Path(result["report_path"]), report.resolve())
            self.assertTrue(result["report_exists"])
            self.assertFalse(result["stdout_only"])

    def test_candidate_sync_dry_run_writes_candidate_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)
            candidate_id = self.discover_confirm_candidate(project_root)

            result = self.run_dry_run_report("sync", str(project_root), "--candidate-id", candidate_id, "--dry-run")

            report = Path(result["report_path"])
            self.assertEqual(report.name, f"{candidate_id}-sync-dry-run.json")
            self.assertTrue(result["report_exists"])
            self.assertEqual(result["candidate_id"], candidate_id)
            self.assertEqual(result["planned_files"], result["files_planned"])
            self.assertTrue(result["apply_command"].startswith(sys.executable))
            candidate = json.loads(Path(result["candidate_path"]).read_text(encoding="utf-8"))
            self.assertEqual(candidate["status"], "confirmed")

    def test_ready_guardrail_dry_run_report_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.register_workstream(project_root)

            result = self.run_dry_run_report(
                str(project_root),
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "validation",
                "--summary",
                "Validation looks complete in prose",
                "--record-status",
                "ready",
                "--dry-run",
                check=False,
            )

            self.assertFalse(result["ok"])
            self.assertFalse(result["can_apply"])
            self.assertIn("validation checkpoint is missing evidence rows", result["apply_blockers"])
            self.assertEqual(result["recommended_next_step"], "run_readiness_review")

    def test_top_level_help_shows_recommended_subcommands(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertIn("Recommended path", completed.stdout)
        self.assertIn("discover <project-root>", completed.stdout)
        self.assertIn("confirm <project-root>", completed.stdout)
        self.assertIn("sync <project-root> --candidate-id", completed.stdout)
        self.assertIn("packet-sync", completed.stdout)

    def test_language_golden_adds_localized_display_without_changing_checkpoint_facts(self) -> None:
        def run(language: str) -> tuple[dict, str]:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            project_root = Path(temp.name)
            setup = self.register_workstream(project_root)
            result = self.run_script(
                project_root,
                "--workstream-id", "l1-checkout",
                "--checkpoint", "prd",
                "--summary", "Checkout PRD is ready for review.",
                "--artifact", "prd=docs/prd.md",
                "--language", language,
            )
            record = (Path(setup["workstream_root"]) / "delivery-record.md").read_text(encoding="utf-8")
            return result, record

        chinese, chinese_record = run("Chinese")
        english, english_record = run("English")
        self.assertEqual(chinese["language"]["locale"], "zh")
        self.assertEqual(chinese["checkpoint"], "prd")
        self.assertEqual(chinese["display"]["outcome"], "检查点操作已完成。")
        self.assertIn("Checkout PRD is ready for review.", chinese_record)
        self.assertIn("## BMM Artifact Index", chinese_record)
        self.assertEqual(english["language"]["locale"], "en")
        self.assertEqual(english["checkpoint"], "prd")
        self.assertEqual(english["display"]["outcome"], "Checkpoint operation completed.")
        self.assertIn("Checkout PRD is ready for review.", english_record)


if __name__ == "__main__":
    unittest.main()
