import copy
import unittest

from panel_contract_testkit import (
    RECOVERY_FIXTURE_PATH,
    RECOVERY_ORDER,
    apply_mutation,
    evaluate_recovery,
    load_json,
    load_source_fixture,
)


class PanelContractRecoveryTests(unittest.TestCase):
    def test_frozen_recovery_cases_have_stable_disposition_and_workflow_order(self) -> None:
        for case in load_json(RECOVERY_FIXTURE_PATH)["cases"]:
            with self.subTest(case=case["name"]):
                inputs = load_source_fixture()
                apply_mutation(inputs, case["mutation"])
                recovery = evaluate_recovery(inputs)
                self.assertEqual(case["expected_status"], recovery["status"])
                self.assertIn(case["expected_code"], [item["code"] for item in recovery["findings"]])
                for workflow in case["expected_workflows"]:
                    self.assertIn(workflow, recovery["workflows"])
                self.assertEqual(
                    recovery["workflows"],
                    [workflow for workflow in RECOVERY_ORDER if workflow in recovery["workflows"]],
                )
                self.assertFalse(recovery["lower_level_fallback_used"])

    def test_cross_input_baseline_and_meeting_flow_mismatch_fail_closed(self) -> None:
        baseline = load_source_fixture()
        baseline["roadmap"]["baseline_revision"] = 5
        baseline_recovery = evaluate_recovery(baseline)
        self.assertEqual("blocked", baseline_recovery["status"])
        self.assertIn("panel.input.roadmap.identity-mismatch", [item["code"] for item in baseline_recovery["findings"]])

        flow = load_source_fixture()
        flow["meeting_packs"]["fde-morning"]["flow_subgraph"]["flow_graph_id"] = "sha256:" + "0" * 64
        flow_recovery = evaluate_recovery(flow)
        self.assertEqual("blocked", flow_recovery["status"])
        self.assertIn("panel.input.meeting-pack.flow-mismatch", [item["code"] for item in flow_recovery["findings"]])
        self.assertEqual(["adp-meeting-pack"], flow_recovery["workflows"])

    def test_recovery_never_reconstructs_from_lower_level_facts(self) -> None:
        inputs = load_source_fixture()
        del inputs["program_status"]
        inputs["baseline"] = {"milestones": [{"weight": 100}]}
        inputs["wdr"] = {"actual_completion_percent": 100}
        recovery = evaluate_recovery(inputs)
        self.assertEqual("blocked", recovery["status"])
        self.assertFalse(recovery["lower_level_fallback_used"])
        self.assertNotIn("adp-plan-baseline", recovery["workflows"])
        self.assertNotIn("adp-status-sync", recovery["workflows"])


if __name__ == "__main__":
    unittest.main()
