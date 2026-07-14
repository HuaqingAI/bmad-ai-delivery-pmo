import unittest
from decimal import Decimal

from progress_contract_testkit import FIXTURE_ROOT, GOLDEN_PATH, load_json


class ProgressContractGoldenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.golden = load_json(GOLDEN_PATH)

    def test_consumer_values_are_direct_and_formula_complete(self) -> None:
        overall = self.golden["overall"]
        current = overall["current"]
        self.assertEqual(self.golden["measurement_status"], overall["measurement_status"])
        self.assertEqual(-40, current["completion_gap_pp"])
        self.assertEqual(current["actual_completion_percent"] - current["planned_completion_percent"], current["completion_gap_pp"])
        self.assertEqual(overall["series"]["forecast_points"][0]["horizon_date"], overall["forecast_summary"]["horizon_date"])
        self.assertEqual(overall["series"]["forecast_points"][0]["forecast_completion_percent"], overall["forecast_summary"]["forecast_completion_percent"])
        self.assertEqual(overall["series"]["forecast_points"][0]["forecast_coverage_percent"], overall["forecast_summary"]["forecast_coverage_percent"])
        self.assertEqual(current["actual_completion_percent"], self.golden["weighted_completion_percent"])

    def test_workstream_rollup_and_l0_boundary_are_explicit(self) -> None:
        workstreams = {item["workstream_id"]: item for item in self.golden["by_workstream"]}
        contribution = sum(
            Decimal(str(item["current"]["completed_contribution_pp"]))
            for item in workstreams.values()
            if item["progress_kind"] == "weighted-milestone"
        )
        self.assertEqual(Decimal(str(self.golden["overall"]["current"]["actual_completion_percent"])), contribution)
        self.assertEqual(Decimal("100"), sum(Decimal(str(workstreams[item]["current"]["project_weight_percent"])) for item in ("L1", "L2")))

        l0 = workstreams["L0"]
        self.assertEqual("gate-readiness", l0["progress_kind"])
        self.assertEqual("not-measurable", l0["measurement_status"])
        self.assertEqual("l0-gate-only", l0["measurement_reasons"][0]["reason_code"])
        self.assertTrue(all(value is None for value in l0["current"].values()))
        self.assertEqual("blocked", l0["gate_readiness"]["readiness_status"])

    def test_actual_eligibility_and_forecast_coverage_are_auditable(self) -> None:
        eligibility = self.golden["eligibility"]
        self.assertEqual(["MS-L1-A"], [item["milestone_id"] for item in eligibility["eligible_actuals"]])
        self.assertEqual("future-actual", eligibility["excluded_actuals"][0]["reason_code"])
        self.assertEqual("partial", self.golden["overall"]["forecast_summary"]["forecast_coverage_status"])
        self.assertEqual(33.33, self.golden["overall"]["forecast_summary"]["forecast_coverage_percent"])
        self.assertEqual("none", next(item for item in self.golden["by_workstream"] if item["workstream_id"] == "L2")["forecast_summary"]["forecast_coverage_status"])

    def test_audited_correction_is_the_only_fixture_that_allows_a_decrease(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "correction-lineage.json")
        previous = fixture["previous"]["actual_points"][-1]["completion_percent"]
        current = fixture["current"]["actual_points"][-1]["completion_percent"]
        self.assertLess(current, previous)
        correction = fixture["current"]["correction"]
        lineage = fixture["current"]["lineage"]
        self.assertEqual(fixture["expected"]["required_correction_id"], correction["correction_id"])
        self.assertEqual(correction["correction_id"], lineage["correction_id"])
        self.assertEqual(fixture["expected"]["required_audit_id"], correction["audit_id"])
        self.assertEqual(correction["audit_id"], lineage["audit_id"])

    def test_unmeasurable_and_blocked_cases_never_encode_unknown_as_zero(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "unmeasurable-recovery.json")
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                self.assertIsNone(case["current_values"])
                if case["measurement_status"] == "blocked":
                    self.assertEqual("required", case["recovery"]["status"])
                    self.assertTrue(case["recovery"]["workflows"])


if __name__ == "__main__":
    unittest.main()
