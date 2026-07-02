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
            self.assertTrue((memory_root / "l0" / "reference-index.md").exists())
            self.assertTrue((memory_root / "views" / "weekly-report.md").exists())

    def test_second_run_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            first = self.run_script(project_root)
            charter = Path(first["memory_root"]) / "project-charter.md"
            charter.write_text("custom content\n", encoding="utf-8")

            second = self.run_script(project_root)

            self.assertTrue(second["ok"])
            self.assertEqual(charter.read_text(encoding="utf-8"), "custom content\n")
            self.assertIn(str(charter), second["files_existing"])

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

            self.assertEqual(result["language"]["communication_language"], "Chinese")
            self.assertEqual(result["language"]["document_output_language"], "Chinese")
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
            self.assertTrue(blocked["confirmation_required"])
            self.assertFalse((project_root / "_bmad-output" / "adp" / "memory" / "project-charter.md").exists())

            confirmed = self.run_script(project_root, "--yes")

            self.assertTrue(confirmed["ok"])
            self.assertTrue((project_root / "_bmad-output" / "adp" / "memory" / "project-charter.md").exists())

    def test_requires_workstream_plan_before_writing_when_existing_prds_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            planning = project_root / "_bmad-output" / "planning-artifacts"
            planning.mkdir(parents=True)
            (planning / "existing-prd.md").write_text("# PRD\n", encoding="utf-8")

            blocked = self.run_script(project_root, check=False)
            bypass_attempt = self.run_script(project_root, "--yes", check=False)

            self.assertFalse(blocked["ok"])
            self.assertTrue(blocked["workstream_plan_required"])
            self.assertIn("candidate workstreams", blocked["next_actions"][0])
            self.assertIn("--workstream-plan", blocked["next_actions"][0])
            self.assertFalse(bypass_attempt["ok"])
            self.assertTrue(bypass_attempt["workstream_plan_required"])
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


if __name__ == "__main__":
    unittest.main()
