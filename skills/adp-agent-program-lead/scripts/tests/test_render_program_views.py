import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_program_views.py"


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


if __name__ == "__main__":
    unittest.main()
