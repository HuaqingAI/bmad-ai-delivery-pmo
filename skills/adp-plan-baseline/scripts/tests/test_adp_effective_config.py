#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adp_effective_config import display_label, format_date, preserve_source_fact, resolve_effective_config


class EffectiveConfigTests(unittest.TestCase):
    def test_activation_routing_is_derived_from_filesystem_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.mkdir(exist_ok=True)

            _, absent = resolve_effective_config(root)
            memory = root / "_bmad-output" / "adp" / "memory"
            memory.mkdir(parents=True)
            _, memory_only = resolve_effective_config(root)
            baseline = memory / "plans" / "program-baseline.md"
            baseline.parent.mkdir()
            baseline.write_text("baseline", encoding="utf-8")
            _, ready = resolve_effective_config(root)

            self.assertEqual(absent["routing_state"], "kickoff_required")
            self.assertFalse(absent["memory_exists"])
            self.assertEqual(memory_only["routing_state"], "baseline_missing")
            self.assertTrue(memory_only["memory_exists"])
            self.assertFalse(memory_only["baseline_exists"])
            self.assertEqual(ready["routing_state"], "baseline_ready")
            self.assertTrue(ready["baseline_exists"])
            self.assertEqual(ready["baseline_path"], str(baseline))

    def test_shared_config_and_adp_section_resolve_with_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "_bmad").mkdir()
            config = root / "_bmad" / "config.yaml"
            config.write_text(
                "document_output_language: Chinese\n"
                "communication_language: English\n"
                "adp:\n"
                "  status_stale_after_days: 12\n"
                "  schedule_variance_tolerance_days: 3\n",
                encoding="utf-8",
            )

            code, result = resolve_effective_config(root)

            self.assertEqual(code, 0)
            self.assertEqual(result["document_locale"], "zh")
            self.assertEqual(result["communication_locale"], "en")
            self.assertEqual(result["values"]["status_stale_after_days"], 12)
            self.assertEqual(result["values"]["schedule_variance_tolerance_days"], 3)
            self.assertEqual(result["value_sources"]["status_stale_after_days"], str(config))

    def test_legacy_adp_config_precedes_shared_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "_bmad" / "adp").mkdir(parents=True)
            (root / "_bmad" / "config.yaml").write_text("document_output_language: English\n", encoding="utf-8")
            legacy = root / "_bmad" / "adp" / "config.yaml"
            legacy.write_text("document_output_language: Chinese\n", encoding="utf-8")

            _, result = resolve_effective_config(root)

            self.assertEqual(result["document_locale"], "zh")
            self.assertEqual(result["value_sources"]["document_output_language"], str(legacy))

    def test_unknown_language_and_invalid_module_value_fall_back_visibly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "_bmad").mkdir()
            (root / "_bmad" / "config.yaml").write_text(
                "document_output_language: Klingon\n"
                "adp:\n"
                "  meeting_pack_item_limit: 100\n",
                encoding="utf-8",
            )

            _, result = resolve_effective_config(root)

            self.assertEqual(result["document_locale"], "en")
            self.assertEqual(result["values"]["meeting_pack_item_limit"], 10)
            self.assertIn("document_output_language", result["fallbacks"])
            self.assertIn("meeting_pack_item_limit", result["fallbacks"])
            self.assertTrue(any("unsupported document_output_language" in warning for warning in result["warnings"]))

    def test_source_fact_translation_is_explicit_and_derived_only(self) -> None:
        source = {"type": "charter", "reference": "charter.md#目标"}
        result = preserve_source_fact("目标日期待批准", source, "Target date pending approval", "en")

        self.assertEqual(result["original"], "目标日期待批准")
        self.assertEqual(result["source"], source)
        self.assertEqual(result["display_translation"]["persistence"], "derived-view-only")

    def test_enum_and_date_display_are_localized_without_changing_canonical_values(self) -> None:
        canonical = "off-plan"

        self.assertEqual(display_label("program_status", canonical, "zh"), "偏离计划")
        self.assertEqual(display_label("program_status", canonical, "en"), "Off plan")
        self.assertEqual(canonical, "off-plan")
        self.assertEqual(format_date("2026-08-31", "zh"), "2026年08月31日")


if __name__ == "__main__":
    unittest.main()
