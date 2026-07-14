import copy
import unittest

from progress_contract_testkit import GOLDEN_PATH, SCHEMA_PATH, load_json, validate_schema


class ProgressContractSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(SCHEMA_PATH)
        self.golden = load_json(GOLDEN_PATH)

    def test_golden_projection_conforms_to_frozen_schema(self) -> None:
        self.assertEqual([], validate_schema(self.golden, self.schema))

    def test_schema_rejects_missing_version_unknown_fields_and_invalid_ranges(self) -> None:
        missing_version = copy.deepcopy(self.golden)
        del missing_version["progress_schema_version"]
        self.assertTrue(validate_schema(missing_version, self.schema))

        unknown_field = copy.deepcopy(self.golden)
        unknown_field["overall"]["current"]["schedule_gap_pp"] = -40
        self.assertTrue(validate_schema(unknown_field, self.schema))

        invalid_percent = copy.deepcopy(self.golden)
        invalid_percent["overall"]["current"]["actual_completion_percent"] = 100.01
        self.assertTrue(validate_schema(invalid_percent, self.schema))

        invalid_disposition = copy.deepcopy(self.golden)
        invalid_disposition["overall"]["comparability"]["disposition"] = "probably-comparable"
        self.assertTrue(validate_schema(invalid_disposition, self.schema))

        fake_l0_percent = copy.deepcopy(self.golden)
        fake_l0_percent["by_workstream"][0]["current"]["actual_completion_percent"] = 0
        self.assertTrue(validate_schema(fake_l0_percent, self.schema))

        discontinuous_delta = copy.deepcopy(self.golden)
        discontinuous_delta["overall"]["comparability"].update(
            {"disposition": "scope-changed", "continuous_trend": False, "actual_delta_pp": -10}
        )
        self.assertTrue(validate_schema(discontinuous_delta, self.schema))

    def test_schema_freezes_units_reasons_compatibility_and_recovery_fields(self) -> None:
        encoded = SCHEMA_PATH.read_text(encoding="utf-8")
        for field in (
            "actual_completion_percent",
            "planned_completion_percent",
            "completion_gap_pp",
            "forecast_coverage_percent",
            "project_weight_percent",
            "completed_contribution_pp",
            "measurement_reasons",
            "comparability",
            "weighted_completion_percent",
            "migration_error_code",
            "recovery",
        ):
            self.assertIn(f'"{field}"', encoded)
        self.assertNotIn("schedule_gap_pp", encoded)


if __name__ == "__main__":
    unittest.main()
