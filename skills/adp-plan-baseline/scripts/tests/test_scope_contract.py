import tempfile
import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scope_contract import (  # noqa: E402
    discover_wdr_registry,
    is_action_routing_id,
    resolve_scope_contract,
    select_scope_contract,
)


class ScopeContractTests(unittest.TestCase):
    def test_only_exact_baseline_program_is_virtual(self) -> None:
        exact = resolve_scope_contract(
            {"milestones": [{"workstream_id": "program"}]},
            [],
        )
        wrong_case = resolve_scope_contract(
            {"milestones": [{"workstream_id": "Program"}]},
            [],
        )

        self.assertEqual(["program"], [item["scope_id"] for item in exact["virtual_scopes"]])
        self.assertEqual([], wrong_case["virtual_scopes"])
        self.assertEqual(["Program"], wrong_case["unregistered_baseline_scopes"])

    def test_cli_normalizes_program_but_not_action_routing_aliases(self) -> None:
        contract = resolve_scope_contract(
            {"milestones": [{"workstream_id": "program"}]},
            ["L0", "L1"],
        )

        selected = select_scope_contract(contract, ["PROGRAM", "l1"])
        aliases = select_scope_contract(contract, ["project", "adp-program"])

        self.assertEqual(["L1"], selected["registered_workstreams"])
        self.assertEqual(["program"], [item["scope_id"] for item in selected["virtual_scopes"]])
        self.assertEqual(["adp-program", "project"], aliases["unknown_scopes"])
        self.assertTrue(is_action_routing_id("project"))
        self.assertTrue(is_action_routing_id("adp-program"))

    def test_legacy_program_directory_is_detected_without_opening_wdr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            legacy = memory_root / "workstreams/program/delivery-record.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"\xff\xfe")

            registry = discover_wdr_registry(memory_root, include_physical=False)
            contract = resolve_scope_contract(
                {"milestones": [{"workstream_id": "program"}]},
                [item["scope_id"] for item in registry],
            )

            self.assertEqual(["program"], [item["scope_id"] for item in registry])
            self.assertEqual([], contract["registered_workstreams"])
            self.assertEqual(
                ["ADP-LEGACY-VIRTUAL-SCOPE-WDR"],
                [item["code"] for item in contract["migration_warnings"]],
            )

    def test_physical_identity_requires_valid_wdr_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            valid = memory_root / "workstreams/l1/delivery-record.md"
            valid.parent.mkdir(parents=True)
            valid.write_text("# WDR\n\n- Workstream ID: L1\n", encoding="utf-8")
            invalid = memory_root / "workstreams/l2/delivery-record.md"
            invalid.parent.mkdir(parents=True)
            invalid.write_text("# WDR\n\n- Name: Missing identity\n", encoding="utf-8")

            registry = discover_wdr_registry(memory_root)

            self.assertEqual(["L1"], [item["scope_id"] for item in registry])


if __name__ == "__main__":
    unittest.main()
