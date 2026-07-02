import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "register_workstream.py"


class RegisterWorkstreamTests(unittest.TestCase):
    def seed_memory(self, project_root: Path) -> None:
        memory = project_root / "_bmad" / "memory" / "adp"
        (memory / "schemas").mkdir(parents=True)
        for rel in [
            "index.md",
            "project-charter.md",
            "cadence.md",
            "schemas/workstream-delivery-record.md",
        ]:
            path = memory / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("seed\n", encoding="utf-8")

    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_creates_workstream_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed_memory(Path(temp_dir))
            result = self.run_script(
                Path(temp_dir),
                "--id",
                "L1 Checkout",
                "--name",
                "Checkout Migration",
                "--owner",
                "FDE-A",
                "--business-owner",
                "Biz-A",
                "--phase",
                "PRD",
                "--scope",
                "Checkout flow migration",
                "--artifact",
                "prd=docs/prd.md",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["workstream_id"], "l1-checkout")
            root = Path(result["workstream_root"])
            self.assertTrue((root / "delivery-record.md").exists())
            self.assertTrue((root / "evidence.md").exists())
            self.assertTrue((root / "decisions.md").exists())
            self.assertTrue((root / "readiness.md").exists())
            self.assertIn("PRD", result["artifacts"])

    def test_second_run_preserves_existing_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.seed_memory(project_root)
            first = self.run_script(project_root, "--id", "L2", "--name", "Line 2", "--owner", "FDE-B")
            record = Path(first["workstream_root"]) / "delivery-record.md"
            record.write_text("custom record\n", encoding="utf-8")

            second = self.run_script(
                project_root,
                "--id",
                "L2",
                "--name",
                "Line 2",
                "--owner",
                "FDE-B",
                "--artifact",
                "prd=docs/prd.md",
            )

            self.assertTrue(second["ok"])
            self.assertEqual(second["mode"], "update")
            self.assertEqual(record.read_text(encoding="utf-8"), "custom record\n")
            self.assertIn(str(record), second["files_existing"])
            patch_plan = Path(second["patch_plan"])
            self.assertTrue(patch_plan.exists())
            self.assertIn("docs/prd.md", patch_plan.read_text(encoding="utf-8"))

    def test_requires_kickoff_memory_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    temp_dir,
                    "--id",
                    "L3",
                    "--name",
                    "Line 3",
                    "--owner",
                    "FDE-C",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertIn("missing_core_files", result)


if __name__ == "__main__":
    unittest.main()
