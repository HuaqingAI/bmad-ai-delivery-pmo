import json
import random
import unittest
from datetime import date, timedelta
from decimal import Decimal

from progress_contract_testkit import FIXTURE_ROOT, comparability_for, evaluate_formula_case, load_json


class ProgressContractPropertyTests(unittest.TestCase):
    def test_normative_formula_cases_match_golden_expectations(self) -> None:
        payload = load_json(FIXTURE_ROOT / "formula-boundaries.json")
        self.assertEqual("3.0.0", payload["contract_version"])
        for case in payload["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(case["expected"], evaluate_formula_case(case))

    def test_deterministic_random_cases_preserve_ranges_gap_and_rollup(self) -> None:
        rng = random.Random(20260713)
        origin = date(2026, 7, 13)
        for case_index in range(200):
            count = rng.randint(2, 8)
            cuts = sorted(rng.sample(range(1, 100), count - 1))
            weights = [cuts[0], *[cuts[index] - cuts[index - 1] for index in range(1, len(cuts))], 100 - cuts[-1]]
            milestones = []
            for index, weight in enumerate(weights):
                planned = origin + timedelta(days=rng.randint(-14, 21))
                actual_choice = rng.choice([None, -7, 0, 1, 7])
                forecast_choice = rng.choice([None, 7, 14, 21])
                milestones.append(
                    {
                        "id": f"MS-{case_index}-{index}",
                        "workstream_id": f"L{1 + index % 3}",
                        "weight_percent": weight,
                        "planned_date": planned.isoformat(),
                        "forecast_date": (origin + timedelta(days=forecast_choice)).isoformat() if forecast_choice is not None else None,
                        "actual_date": (origin + timedelta(days=actual_choice)).isoformat() if actual_choice is not None else None,
                        "completion_criteria_defined": True,
                        "evidence_audited": True,
                    }
                )
            case = {"name": f"generated-{case_index}", "as_of": origin.isoformat(), "horizons": [(origin + timedelta(days=7)).isoformat()], "milestones": milestones}
            first = evaluate_formula_case(case)
            second = evaluate_formula_case(json.loads(json.dumps(case)))
            self.assertEqual(first, second)
            self.assertGreaterEqual(first["actual_completion_percent"], 0)
            self.assertLessEqual(first["actual_completion_percent"], 100)
            self.assertGreaterEqual(first["planned_completion_percent"], 0)
            self.assertLessEqual(first["planned_completion_percent"], 100)
            self.assertAlmostEqual(
                first["completion_gap_pp"],
                first["actual_completion_percent"] - first["planned_completion_percent"],
                places=2,
            )
            contribution = sum(Decimal(str(item["completed_contribution_pp"])) for item in first["by_workstream"].values())
            self.assertEqual(Decimal(str(first["actual_completion_percent"])), contribution)

    def test_missing_forecast_never_uses_planned_date(self) -> None:
        payload = load_json(FIXTURE_ROOT / "formula-boundaries.json")
        case = next(item for item in payload["cases"] if item["name"] == "missing-forecast-never-falls-back-to-planned")
        result = evaluate_formula_case(case)
        self.assertEqual([25.0, 25.0], [item["forecast_completion_percent"] for item in result["forecast_points"]])
        self.assertEqual(["none", "none"], [item["forecast_coverage_status"] for item in result["forecast_points"]])

    def test_zero_remaining_weight_has_complete_null_coverage(self) -> None:
        payload = load_json(FIXTURE_ROOT / "formula-boundaries.json")
        case = next(item for item in payload["cases"] if item["name"] == "zero-remaining-weight-is-complete-not-ordinary-full")
        point = evaluate_formula_case(case)["forecast_points"][0]
        self.assertEqual("complete", point["forecast_coverage_status"])
        self.assertIsNone(point["forecast_coverage_percent"])

    def test_revision_cases_break_or_restore_continuity_deterministically(self) -> None:
        payload = load_json(FIXTURE_ROOT / "revision-comparability.json")
        for case in payload["cases"]:
            with self.subTest(case=case["name"]):
                expected = {key: case["expected"][key] for key in ("disposition", "continuous_trend", "delta_allowed")}
                self.assertEqual(expected, comparability_for(case))


if __name__ == "__main__":
    unittest.main()
