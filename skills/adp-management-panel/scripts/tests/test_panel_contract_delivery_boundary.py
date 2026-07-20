import ast
import unittest
from pathlib import Path

from panel_contract_testkit import SKILL_ROOT


class PanelContractDeliveryBoundaryTests(unittest.TestCase):
    def test_skill_keeps_runtime_boundary_and_exact_operations(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("ADP-PANEL-RENDERER-NOT-IMPLEMENTED", skill)
        for command in (
            "refresh --memory-root {memory-root} --selection-policy <path>",
            "inspect --memory-root {memory-root}",
            "archive --memory-root {memory-root} --selection-policy <path> --distribution-profile",
        ):
            self.assertIn(command, skill)
        self.assertIn("Use only audited canonical inputs", skill)
        self.assertIn("selection policy supplied by the owning workflow or user", skill)
        self.assertIn("Do not bypass a blocked result or modify immutable artifacts", skill)
        self.assertIn("only when changing or diagnosing", skill)
        self.assertIn("Python >=3.10", skill)
        self.assertIn("Return the JSON emitted by `management_panel.py` unchanged", skill)
        self.assertEqual(1, skill.count("references/panel-model-contract-v1.md"))
        self.assertNotIn("copy`, `allowlist`, `stable-sort`, `select`, or `redact", skill)

    def test_phase7_renderer_frontend_and_fixed_elk_asset_exist(self) -> None:
        for relative in (
            "assets/panel-template.html",
            "assets/panel.css",
            "assets/panel.js",
            "assets/elk-resource-v1.json",
            "assets/vendor/elk.bundled-0.9.3.js",
            "assets/vendor/ELK-LICENSE-EPL-2.0.md",
            "scripts/panel_model.py",
            "scripts/management_panel.py",
        ):
            self.assertTrue((SKILL_ROOT / relative).is_file(), relative)
        production_scripts = [
            path
            for path in (SKILL_ROOT / "scripts").glob("*.py")
            if path.is_file()
        ]
        self.assertEqual({"management_panel.py", "panel_model.py"}, {path.name for path in production_scripts})

    def test_contract_testkit_contains_no_business_formula_functions(self) -> None:
        testkit = SKILL_ROOT / "scripts/tests/panel_contract_testkit.py"
        tree = ast.parse(testkit.read_text(encoding="utf-8"))
        function_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        forbidden = {
            "calculate_progress",
            "calculate_completion_gap",
            "calculate_forecast",
            "classify_status",
            "aggregate_counts",
            "infer_topology",
            "select_branch",
        }
        self.assertTrue(forbidden.isdisjoint(function_names))

    def test_scripts_are_limited_to_contract_composition_and_delivery(self) -> None:
        files = sorted(
            path.relative_to(SKILL_ROOT).as_posix()
            for path in (SKILL_ROOT / "scripts").rglob("*.py")
        )
        self.assertTrue(files)
        self.assertTrue(all(path.startswith("scripts/tests/") or path in {"scripts/management_panel.py", "scripts/panel_model.py"} for path in files))


if __name__ == "__main__":
    unittest.main()
