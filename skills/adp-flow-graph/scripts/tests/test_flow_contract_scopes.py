import unittest

from flow_contract_testkit import FIXTURE_ROOT, evaluate_scoped_counts, load_json


class FlowContractScopeTests(unittest.TestCase):
    def test_active_processed_risk_blocked_counts_match_canonical_sources(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "scoped-overlay-counts.json")
        actual = evaluate_scoped_counts(fixture)
        self.assertEqual(fixture["expected"], actual)

    def test_processed_window_is_half_open_and_cancelled_is_never_processed(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "scoped-overlay-counts.json")
        actual = evaluate_scoped_counts(fixture)
        processed = actual["E-A-MERGE"]["processed"]
        self.assertIn("A-DONE-START", processed)
        self.assertNotIn("A-DONE-END", processed)
        self.assertFalse(any("A-CANCELLED" in counts["processed"] for target, counts in actual.items() if target != "unmapped"))

    def test_missing_and_unknown_relations_are_preserved_unmapped(self) -> None:
        fixture = load_json(FIXTURE_ROOT / "scoped-overlay-counts.json")
        self.assertEqual(["A-UNMAPPED", "R-UNKNOWN"], evaluate_scoped_counts(fixture)["unmapped"])


if __name__ == "__main__":
    unittest.main()
