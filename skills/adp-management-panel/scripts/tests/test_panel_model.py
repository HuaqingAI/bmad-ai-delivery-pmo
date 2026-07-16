import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import panel_model


class ProductionPanelModelTests(unittest.TestCase):
    def test_recovery_deduplicates_identical_pre_render_reasons(self):
        inputs = panel_model.load_source_fixture()
        inputs["request"] = {
            "panel_input_audit_disposition": "degraded",
            "panel_input_audit_findings": ["panel.input.first", "panel.input.second"],
            "panel_input_audit_workflows": ["adp-meeting-pack"],
        }

        recovery = panel_model.evaluate_recovery(inputs)
        repeated = [
            item for item in recovery["findings"]
            if item["message"] == "The panel pre-render audit requires this disposition."
        ]

        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]["code"], "panel.input.first")

    def test_empty_flow_state_preserves_zero_scope_and_unmapped_recovery(self):
        flow = {
            "nodes": [],
            "edges": [],
            "scope_id": "FDE-2026-07-13-2026-07-14",
            "selection_id": "sha256:" + "a" * 64,
            "meeting_window": {
                "start": "2026-07-13",
                "end": "2026-07-14",
                "status": "confirmed",
                "confirmation_mode": "explicit",
            },
            "unmapped": [
                {
                    "source_kind": "risk",
                    "source_id": "RISK-1",
                    "reason": "missing-related-ids",
                    "finding_code": "flow.overlay.unmapped",
                    "recovery": "Add explicit related plan-item or flow-edge IDs.",
                },
                {
                    "source_kind": "risk",
                    "source_id": "RISK-2",
                    "reason": "missing-related-ids",
                    "finding_code": "flow.overlay.unmapped",
                    "recovery": "Add explicit related plan-item or flow-edge IDs.",
                },
            ],
            "recovery": [],
        }

        empty = panel_model.flow_empty_state(flow)

        self.assertEqual(empty["node_count"], 0)
        self.assertEqual(empty["edge_count"], 0)
        self.assertEqual(empty["unmapped_count"], 2)
        self.assertTrue(empty["confirmed"])
        self.assertEqual(empty["recovery"], ["Add explicit related plan-item or flow-edge IDs."])
        self.assertEqual([item["source_id"] for item in empty["source_details"]], ["RISK-1", "RISK-2"])

    def test_manifest_projects_reporting_period_identity_without_dropping_canonical_cadence(self):
        inputs = panel_model.load_source_fixture()
        inputs["program_status"]["reporting_period"]["cadence"] = "weekly"

        model = panel_model.compose_panel(inputs)

        self.assertEqual(
            model["manifest"]["reporting_period"],
            {
                "start": inputs["program_status"]["reporting_period"]["start"],
                "end": inputs["program_status"]["reporting_period"]["end"],
            },
        )
        self.assertEqual(model["data"]["status"]["reporting_period"]["cadence"], "weekly")

    def test_production_composer_matches_frozen_schema(self):
        inputs = panel_model.load_source_fixture()
        model = panel_model.compose_panel(inputs)
        self.assertEqual([], panel_model.validate_schema(model, panel_model.load_json(panel_model.PANEL_SCHEMA_PATH)))
        self.assertEqual(inputs["program_status"]["progress"], model["data"]["status"]["progress"])

    def test_production_composer_preserves_unusual_canonical_values(self):
        inputs = panel_model.load_source_fixture()
        inputs["program_status"]["progress"]["overall"]["current"]["completion_gap_pp"] = 77.25
        inputs["roadmap"]["progress"] = copy.deepcopy(inputs["program_status"]["progress"])
        model = panel_model.compose_panel(inputs)
        self.assertEqual(77.25, model["data"]["status"]["progress"]["overall"]["current"]["completion_gap_pp"])

    def test_three_views_preserve_one_snapshot_state_health_and_scoped_counts(self):
        inputs = panel_model.load_source_fixture()
        model = panel_model.compose_panel(inputs)
        graph_states = {item["node_id"]: item for item in inputs["flow_graph"]["state"]["nodes"]}
        graph_scopes = {
            item["scope_id"]: item for item in inputs["flow_graph"]["overlays"]["scopes"]
        }

        self.assertEqual(model["data"]["status"]["snapshot_id"], inputs["program_status"]["snapshot_id"])
        self.assertEqual(model["data"]["status"]["progress"], inputs["program_status"]["progress"])
        self.assertEqual(
            inputs["request"]["project_lead_scope_id"],
            model["data"]["flows"]["project-lead"]["scope_id"],
        )
        for view_id, flow in model["data"]["flows"].items():
            for state in flow["node_states"]:
                self.assertEqual(state, graph_states[state["node_id"]], view_id)
            scope = graph_scopes.get(flow.get("scope_id"))
            if scope is not None:
                node_ids = {node["node_id"] for node in flow["nodes"]}
                edge_ids = {edge["edge_id"] for edge in flow["edges"]}
                expected = [
                    item
                    for item in scope["allocations"]
                    if (item["target_type"] == "node" and item["target_id"] in node_ids)
                    or (item["target_type"] == "edge" and item["target_id"] in edge_ids)
                ]
                expected.sort(key=lambda item: (item["target_type"], item["target_id"]))
                self.assertEqual(flow["allocations"], expected, view_id)
        for scenario, meeting in model["data"]["meetings"].items():
            source = inputs["meeting_packs"][scenario]
            self.assertEqual(meeting["meeting_pack_id"], source["meeting_pack_id"])
            self.assertEqual(meeting["meeting_window"], source["meeting_window"])
            self.assertEqual(meeting["readiness"], source["readiness"])
            self.assertEqual(meeting["lifecycle"], source["lifecycle"])


if __name__ == "__main__":
    unittest.main()
