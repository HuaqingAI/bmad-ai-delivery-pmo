import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap_adp.py"


class BootstrapAdpTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_creates_expected_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_script(Path(temp_dir), "--project-name", "Demo ADP")

            self.assertTrue(result["ok"])
            memory_root = Path(result["memory_root"])
            self.assertTrue((memory_root / "project-charter.md").exists())
            self.assertTrue((memory_root / "schemas" / "workstream-delivery-record.md").exists())
            self.assertTrue((memory_root / "schemas" / "action-ledger.md").exists())
            self.assertTrue((memory_root / "l0" / "reference-index.md").exists())
            self.assertTrue((memory_root / "actions" / "action-ledger.md").exists())
            self.assertTrue((memory_root / "audits").is_dir())
            self.assertTrue((memory_root / "audits" / "README.md").exists())
            self.assertTrue((memory_root / "views" / "meeting-packs" / "fde-morning").is_dir())
            self.assertTrue((memory_root / "views" / "meeting-packs" / "business-biweekly").is_dir())
            self.assertTrue((memory_root / "views" / "meeting-packs" / "README.md").exists())
            self.assertTrue((memory_root / "views" / "weekly-report.md").exists())
            self.assertTrue((memory_root / "views" / "roadmap.md").exists())
            self.assertTrue((memory_root / "views" / "roadmap.json").exists())
            self.assertTrue((memory_root / "plans" / "baseline-history").is_dir())
            self.assertTrue((memory_root / "plans" / "README.md").exists())
            self.assertFalse((memory_root / "plans" / "program-baseline.md").exists())
            self.assertTrue((memory_root / "schemas" / "program-baseline.md").exists())
            self.assertTrue((memory_root / "schemas" / "program-status.md").exists())
            self.assertTrue((memory_root / "snapshots" / "program-status" / "README.md").exists())
            self.assertTrue((memory_root / "intake" / "program-baseline-candidate.json").exists())
            self.assertTrue((memory_root / "intake" / "program-baseline-intake.md").exists())
            candidate = json.loads(
                (memory_root / "intake" / "program-baseline-candidate.json").read_text(encoding="utf-8")
            )
            self.assertEqual(candidate["project"]["name"], "Demo ADP")
            self.assertEqual(candidate["confirmation_status"], "candidate")
            self.assertTrue((memory_root / "views" / "program-status.md").exists())
            self.assertTrue((memory_root / "views" / "program-status.json").exists())
            self.assertEqual(result["baseline_onboarding"]["status"], "gap")
            self.assertFalse(result["baseline_onboarding"]["baseline_exists"])
            self.assertEqual(result["baseline_onboarding"]["owner_skill"], "adp-plan-baseline")

    def test_second_run_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            first = self.run_script(project_root)
            charter = Path(first["memory_root"]) / "project-charter.md"
            ledger = Path(first["memory_root"]) / "actions" / "action-ledger.md"
            charter.write_text("custom content\n", encoding="utf-8")
            ledger.write_text("custom ledger\n", encoding="utf-8")

            second = self.run_script(project_root)

            self.assertTrue(second["ok"])
            self.assertEqual(charter.read_text(encoding="utf-8"), "custom content\n")
            self.assertEqual(ledger.read_text(encoding="utf-8"), "custom ledger\n")
            self.assertIn(str(charter), second["files_existing"])
            self.assertIn(str(ledger), second["files_existing"])

    def test_update_adds_vnext_scaffold_without_overwriting_existing_baseline_or_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "_bmad-output" / "adp" / "memory"
            baseline = memory_root / "plans" / "program-baseline.md"
            status_view = memory_root / "views" / "program-status.json"
            baseline.parent.mkdir(parents=True)
            status_view.parent.mkdir(parents=True)
            baseline.write_text("approved baseline\n", encoding="utf-8")
            status_view.write_text('{"custom": true}\n', encoding="utf-8")

            result = self.run_script(project_root)

            self.assertTrue(result["ok"])
            self.assertEqual(result["baseline_onboarding"]["status"], "ready")
            self.assertTrue(result["baseline_onboarding"]["baseline_exists"])
            self.assertEqual(baseline.read_text(encoding="utf-8"), "approved baseline\n")
            self.assertEqual(status_view.read_text(encoding="utf-8"), '{"custom": true}\n')
            self.assertIn(str(status_view), result["files_existing"])
            self.assertTrue((memory_root / "schemas" / "program-status.md").exists())
            self.assertTrue((memory_root / "snapshots" / "program-status").is_dir())

    def test_captures_project_timezone_and_fde_recurring_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_script(
                Path(temp_dir),
                "--timezone",
                "Asia/Shanghai",
                "--fde-days",
                "Tue,Thu",
                "--fde-cadence-override",
                "Approved in decision D-17.",
            )
            memory_root = Path(result["memory_root"])
            cadence = (memory_root / "cadence.md").read_text(encoding="utf-8")

            self.assertEqual(result["meeting_cadence"]["project_timezone"], "Asia/Shanghai")
            self.assertEqual(result["meeting_cadence"]["fde_meeting_days"], ["Tuesday", "Thursday"])
            self.assertEqual(result["meeting_cadence"]["long_term_override"], "Approved in decision D-17.")
            self.assertIn("Project timezone: Asia/Shanghai", cadence)
            self.assertIn("Recurring weekdays: Tuesday, Thursday", cadence)
            self.assertIn("Long-term override: Approved in decision D-17.", cadence)

    def test_discovers_target_adp_config_language_and_bmad_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            adp_config = project_root / "_bmad" / "adp" / "config.yaml"
            planning = project_root / "_bmad-output" / "planning-artifacts"
            implementation = project_root / "_bmad-output" / "implementation-artifacts"
            adp_config.parent.mkdir(parents=True)
            planning.mkdir(parents=True)
            implementation.mkdir(parents=True)
            adp_config.write_text(
                "\n".join(
                    [
                        "user_name: hth",
                        "project_name: shopify-migration",
                        "communication_language: Chinese",
                        "document_output_language: Chinese",
                        'planning_artifacts: "{project-root}/_bmad-output/planning-artifacts"',
                        'implementation_artifacts: "{project-root}/_bmad-output/implementation-artifacts"',
                        "adp:",
                        "  default_reporting_cadence: biweekly",
                        "  project_timezone: Asia/Shanghai",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            prd = planning / "shopify-migration-prd.md"
            story = implementation / "story-1-1-import-products.md"
            prd.write_text("# PRD\n", encoding="utf-8")
            story.write_text("# Story\n", encoding="utf-8")

            result = self.run_script(project_root, "--dry-run")

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["language"]["communication_language"], "Chinese")
            self.assertEqual(result["language"]["document_output_language"], "Chinese")
            self.assertEqual(result["cadence"], "biweekly")
            self.assertEqual(result["meeting_cadence"]["project_timezone"], "Asia/Shanghai")
            self.assertEqual(
                result["meeting_cadence"]["fde_meeting_days"],
                ["Monday", "Wednesday", "Friday"],
            )
            self.assertEqual(result["config_sources"], [str(adp_config)])
            self.assertEqual(result["discovered_bmad_artifacts"]["counts"]["planning"], 1)
            self.assertEqual(result["discovered_bmad_artifacts"]["counts"]["implementation"], 1)
            self.assertEqual(len(result["discovered_bmad_artifacts"]["candidate_workstreams"]), 1)
            self.assertEqual(
                result["discovered_bmad_artifacts"]["candidate_workstreams"][0]["id"],
                "shopify-migration",
            )
            artifact_paths = {
                item["path"]
                for group in ("planning", "implementation")
                for item in result["discovered_bmad_artifacts"][group]
            }
            self.assertIn(str(prd), artifact_paths)
            self.assertIn(str(story), artifact_paths)

    def test_requires_confirmation_before_writing_when_existing_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            planning = project_root / "_bmad-output" / "planning-artifacts"
            planning.mkdir(parents=True)
            (planning / "existing-architecture.md").write_text("# Architecture\n", encoding="utf-8")

            blocked = self.run_script(project_root, check=False)

            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked")
            self.assertTrue(blocked["confirmation_required"])
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "project-charter.md").exists())

            confirmed = self.run_script(project_root, "--yes")

            self.assertTrue(confirmed["ok"])
            self.assertEqual(confirmed["status"], "complete")
            self.assertTrue((project_root / "_bmad-output" / "adp" / "memory" / "project-charter.md").exists())

    def test_requires_workstream_plan_before_writing_when_existing_prds_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            planning = project_root / "_bmad-output" / "planning-artifacts"
            planning.mkdir(parents=True)
            (planning / "existing-prd.md").write_text("# PRD\n", encoding="utf-8")

            blocked = self.run_script(project_root, check=False)
            bypass_attempt = self.run_script(project_root, "--yes", check=False)
            headless_attempt = self.run_script(project_root, "--headless", check=False)

            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked")
            self.assertTrue(blocked["workstream_plan_required"])
            self.assertEqual(blocked["next_required_input"]["flag"], "--workstream-plan")
            self.assertEqual(blocked["candidate_workstreams"][0]["id"], "existing")
            self.assertIn("candidate workstreams", blocked["next_actions"][0])
            self.assertIn("--workstream-plan", blocked["next_actions"][0])
            self.assertFalse(bypass_attempt["ok"])
            self.assertEqual(bypass_attempt["status"], "blocked")
            self.assertEqual(bypass_attempt["next_required_input"]["flag"], "--workstream-plan")
            self.assertEqual(bypass_attempt["candidate_workstreams"][0]["id"], "existing")
            self.assertTrue(bypass_attempt["workstream_plan_required"])
            self.assertFalse(headless_attempt["ok"])
            self.assertEqual(headless_attempt["status"], "blocked")
            self.assertEqual(headless_attempt["next_required_input"]["flag"], "--workstream-plan")
            self.assertEqual(headless_attempt["candidate_workstreams"][0]["id"], "existing")
            self.assertTrue(headless_attempt["workstream_plan_required"])
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "project-charter.md").exists())

    def test_persists_registration_plan_for_confirmed_prd_workstreams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            planning = project_root / "_bmad-output" / "planning-artifacts"
            planning.mkdir(parents=True)
            prd = planning / "checkout-prd.md"
            prd.write_text("# Checkout PRD\n", encoding="utf-8")
            plan = project_root / "adp-workstream-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "workstreams": [
                            {
                                "id": "l1-checkout",
                                "name": "Checkout Migration",
                                "fde_owner": "FDE-A",
                                "business_owner": "Biz-A",
                                "phase": "PRD",
                                "scope": "Checkout flow migration",
                                "acceptance": "Business confirms migrated checkout flow.",
                                "prd_path": str(prd),
                                "dependencies": ["l0-platform"],
                                "risks": ["Payment callback contract needs confirmation."],
                                "open_questions": ["Who signs off checkout acceptance?"],
                                "next_actions": ["Confirm PRD-derived baseline."],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.run_script(project_root, "--workstream-plan", str(plan))

            self.assertTrue(result["ok"])
            self.assertEqual(result["workstream_registration_plan"]["count"], 1)
            memory_root = Path(result["memory_root"])
            plan_json = memory_root / "intake" / "workstream-registration-plan.json"
            plan_md = memory_root / "intake" / "workstream-registration-plan.md"
            self.assertTrue(plan_json.exists())
            self.assertTrue(plan_md.exists())
            self.assertFalse((memory_root / "workstreams" / "l1-checkout" / "delivery-record.md").exists())
            plan_payload = json.loads(plan_json.read_text(encoding="utf-8"))
            self.assertEqual(plan_payload["owner_skill"], "adp-workstream-register")
            self.assertEqual(plan_payload["workstreams"][0]["id"], "l1-checkout")
            self.assertEqual(plan_payload["workstreams"][0]["artifacts"]["PRD"], str(prd))
            plan_text = plan_md.read_text(encoding="utf-8")
            self.assertIn("adp-workstream-register", plan_text)
            self.assertIn("l0-platform", plan_json.read_text(encoding="utf-8"))

    def test_registration_plan_preserves_existing_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory = project_root / "_bmad-output" / "adp" / "memory"
            intake = memory / "intake"
            intake.mkdir(parents=True)
            plan_md = intake / "workstream-registration-plan.md"
            plan_md.write_text("custom plan\n", encoding="utf-8")
            plan = project_root / "adp-workstream-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "workstreams": [
                            {
                                "id": "existing-line",
                                "name": "Existing Line",
                                "fde_owner": "FDE-A",
                                "prd_path": "docs/existing-prd.md",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.run_script(project_root, "--workstream-plan", str(plan))

            self.assertTrue(result["ok"])
            self.assertEqual(plan_md.read_text(encoding="utf-8"), "custom plan\n")
            self.assertIn(str(plan_md), result["files_existing"])
            self.assertTrue((intake / "workstream-registration-plan.json").exists())
            self.assertFalse((memory / "workstreams" / "existing-line" / "delivery-record.md").exists())

    def test_workstream_plan_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan = project_root / "adp-workstream-plan.json"
            plan.write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "workstreams": [
                            {
                                "id": "bom-line",
                                "name": "BOM Line",
                                "fde_owner": "FDE-A",
                                "prd_path": "docs/bom-prd.md",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.run_script(project_root, "--workstream-plan", str(plan))

            self.assertTrue(result["ok"])
            self.assertEqual(result["workstream_registration_plan"]["workstreams"][0]["id"], "bom-line")

    def test_legacy_memory_requires_confirmation_before_new_default_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            legacy = project_root / "_bmad" / "memory" / "adp"
            legacy.mkdir(parents=True)
            (legacy / "project-charter.md").write_text("legacy memory\n", encoding="utf-8")

            blocked = self.run_script(project_root, check=False)

            self.assertFalse(blocked["ok"])
            self.assertTrue(blocked["legacy_memory_confirmation_required"])
            self.assertTrue(blocked["legacy_memory"]["legacy_memory_exists"])
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "project-charter.md").exists())

            keep_legacy = self.run_script(project_root, "--memory-root", "_bmad/memory/adp")

            self.assertTrue(keep_legacy["ok"])
            self.assertTrue(keep_legacy["legacy_memory"]["using_legacy_memory_root"])
            self.assertEqual((legacy / "project-charter.md").read_text(encoding="utf-8"), "legacy memory\n")

    def test_dry_run_reports_vnext_starter_files_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_script(Path(temp_dir), "--dry-run")
            memory_root = Path(result["memory_root"])

            self.assertTrue(result["ok"])
            self.assertIn(str(memory_root / "audits" / "README.md"), result["files_created"])
            self.assertIn(
                str(memory_root / "views" / "meeting-packs" / "README.md"),
                result["files_created"],
            )
            self.assertIn(str(memory_root / "views" / "roadmap.md"), result["files_created"])
            self.assertIn(str(memory_root / "views" / "roadmap.json"), result["files_created"])
            self.assertIn(str(memory_root / "plans" / "baseline-history"), result["directories_created"])
            self.assertIn(str(memory_root / "snapshots" / "program-status"), result["directories_created"])
            self.assertIn(str(memory_root / "schemas" / "program-baseline.md"), result["files_created"])
            self.assertIn(str(memory_root / "schemas" / "program-status.md"), result["files_created"])
            self.assertIn(str(memory_root / "intake" / "program-baseline-candidate.json"), result["files_created"])
            self.assertIn(str(memory_root / "views" / "program-status.json"), result["files_created"])
            self.assertEqual(result["baseline_onboarding"]["status"], "gap")
            self.assertFalse((memory_root / "audits" / "README.md").exists())
            self.assertFalse((memory_root / "views" / "roadmap.md").exists())
            self.assertFalse((memory_root / "plans" / "baseline-history").exists())
            self.assertFalse((memory_root / "snapshots" / "program-status").exists())


if __name__ == "__main__":
    unittest.main()
