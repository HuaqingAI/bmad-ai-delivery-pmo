import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resolve_bmad_config import resolve_config


class ResolveBmadConfigTests(unittest.TestCase):
    def test_adp_config_precedes_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "_bmad" / "adp").mkdir(parents=True)
            (project_root / "_bmad").mkdir(exist_ok=True)
            (project_root / "_bmad" / "config.yaml").write_text(
                "communication_language: Spanish\ndocument_output_language: Spanish\n",
                encoding="utf-8",
            )
            adp_config = project_root / "_bmad" / "adp" / "config.yaml"
            adp_config.write_text(
                "communication_language: Chinese\ndocument_output_language: Chinese\n",
                encoding="utf-8",
            )

            code, result = resolve_config(project_root)

            self.assertEqual(code, 0)
            self.assertEqual(result["communication_language"], "Chinese")
            self.assertEqual(result["document_output_language"], "Chinese")
            self.assertEqual(
                result["value_sources"]["communication_language"],
                str(adp_config.resolve()),
            )

    def test_missing_config_defaults_to_english_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code, result = resolve_config(Path(temp_dir))

            self.assertEqual(code, 0)
            self.assertEqual(result["communication_language"], "English")
            self.assertEqual(result["document_output_language"], "English")
            self.assertTrue(any("no BMad config file found" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
