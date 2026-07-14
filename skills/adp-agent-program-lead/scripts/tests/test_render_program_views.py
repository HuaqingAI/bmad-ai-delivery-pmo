import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_program_views.py"
REPO_ROOT = SCRIPT.parents[3]
MODULE_HELP = REPO_ROOT / "skills/adp-setup/assets/module-help.csv"
RELEASE_NOTES = REPO_ROOT / "skills/reports/adp-v1.3-release-notes.md"


class RenderProgramViewsCompatibilityTests(unittest.TestCase):
    def test_legacy_entry_point_is_a_read_only_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "_bmad-output" / "adp" / "memory").mkdir(parents=True)

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(project_root)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["mode"], "canonical-consumer")
            self.assertIn("adp-program-status", result["recommended_workflows"])
            self.assertEqual(result["writes_performed"], [])

    def test_legacy_read_only_core_flags_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "custom-memory"
            memory_root.mkdir()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(project_root),
                    "--view",
                    "project-lead",
                    "--memory-root",
                    str(memory_root),
                    "--as-of",
                    "2026-07-14",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["mode"], "canonical-consumer")
            self.assertNotIn("ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED", completed.stdout)

    def test_retired_legacy_renderer_flags_return_deterministic_migration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            output = project_root / "migration.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(project_root),
                    "--prepass-json",
                    "old-prepass.json",
                    "--max-actions=10",
                    "-o",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(result["status"], "migration-required")
            self.assertEqual(result["error_code"], "ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED")
            self.assertEqual(result["unsupported_options"], ["--max-actions", "--prepass-json"])
            self.assertEqual(result["replacement"]["producer"], "adp-program-status")
            self.assertEqual(result["replacement"]["consumer"], "adp-agent-program-lead")
            self.assertEqual(result["writes_performed"], [])

    def test_help_documents_legacy_compatibility_and_migration(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("Legacy render_program_views.py compatibility", completed.stdout)
        self.assertIn("ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED", completed.stdout)

    def test_module_help_and_release_notes_publish_phase10_operations(self) -> None:
        module_help = MODULE_HELP.read_text(encoding="utf-8")
        release_notes = RELEASE_NOTES.read_text(encoding="utf-8")

        self.assertIn("lock-inspect|lock-recover", module_help)
        self.assertIn("lineage-validates canonical management Markdown", module_help)
        self.assertIn("ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED", module_help)
        self.assertIn("stale baseline lock", release_notes)
        self.assertIn("management Markdown lineage", release_notes)
        self.assertIn("ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED", release_notes)
        self.assertIn("Phase 11", release_notes)


if __name__ == "__main__":
    unittest.main()
