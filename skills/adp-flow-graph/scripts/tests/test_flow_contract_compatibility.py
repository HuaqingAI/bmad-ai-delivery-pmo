import unittest

from flow_contract_testkit import FIXTURE_ROOT, load_json, normalize_legacy_dependency


class FlowContractCompatibilityTests(unittest.TestCase):
    def test_legacy_dependency_normalization_is_stable_and_exact(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "legacy-dependency-normalization.json")
        for case in fixture["cases"]:
            normalized = normalize_legacy_dependency(
                fixture["baseline_id"],
                fixture["revision"],
                case["input"],
                fixture["target"],
                fixture["source"],
            )
            self.assertEqual(case["expected"], normalized)
            self.assertEqual(normalized, normalize_legacy_dependency(fixture["baseline_id"], fixture["revision"], case["input"], fixture["target"], fixture["source"]))

    def test_missing_vnext_sources_have_deterministic_migration_codes(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "source-contract-golden.json")
        self.assertEqual("ADP-FLOW-STATE-MIGRATION-REQUIRED", fixture["program_status"]["compatibility"]["migration_error_code"])
        self.assertEqual("ADP-ACTION-FLOW-MIGRATION-REQUIRED", fixture["actions"]["compatibility"]["migration_error_code"])
        self.assertEqual("ADP-RISK-FLOW-MIGRATION-REQUIRED", fixture["risks"]["compatibility"]["migration_error_code"])


if __name__ == "__main__":
    unittest.main()
