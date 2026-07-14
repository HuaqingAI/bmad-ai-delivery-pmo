import unittest

from flow_contract_testkit import FIXTURE_ROOT, finding_for_case, load_json


class FlowContractRecoveryTests(unittest.TestCase):
    def test_reference_cycle_condition_and_aggregation_recovery_is_deterministic(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "recovery-cases.json")
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(case["expected_code"], finding_for_case(case))
                self.assertEqual("ready" if case["expected_code"] is None else "blocked", case["expected_disposition"])

    def test_only_all_rework_cycles_are_accepted(self) -> None:
        cases = {item["name"]: item for item in load_json(FIXTURE_ROOT / "recovery-cases.json")["cases"]}
        self.assertEqual("flow.cycle.illegal", finding_for_case(cases["illegal-cycle"]))
        self.assertIsNone(finding_for_case(cases["explicit-rework-cycle"]))


if __name__ == "__main__":
    unittest.main()
