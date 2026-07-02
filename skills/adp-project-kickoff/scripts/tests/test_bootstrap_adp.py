import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap_adp.py"


class BootstrapAdpTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
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


if __name__ == "__main__":
    unittest.main()
