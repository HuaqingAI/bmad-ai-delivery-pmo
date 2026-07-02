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
            (planning / "existing-prd.md").write_text("# PRD\n", encoding="utf-8")

            blocked = self.run_script(project_root, check=False)

            self.assertFalse(blocked["ok"])
            self.assertTrue(blocked["confirmation_required"])
            self.assertFalse((project_root / "_bmad" / "memory" / "adp" / "project-charter.md").exists())

            confirmed = self.run_script(project_root, "--yes")

            self.assertTrue(confirmed["ok"])
            self.assertTrue((project_root / "_bmad" / "memory" / "adp" / "project-charter.md").exists())


if __name__ == "__main__":
    unittest.main()
