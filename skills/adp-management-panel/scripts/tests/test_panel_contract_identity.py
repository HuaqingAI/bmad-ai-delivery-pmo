import copy
import unittest

from panel_contract_testkit import (
    IDENTITY_GOLDEN_PATH,
    _flow_selection,
    _recompute_flow_identities,
    canonical_hash,
    compose_panel,
    load_json,
    load_source_fixture,
)


def refresh_pack_flow_selections(inputs: dict) -> None:
    graph = inputs["flow_graph"]
    for scenario, pack in inputs["meeting_packs"].items():
        selection_id = canonical_hash(
            {
                "flow_graph_id": graph["flow_graph_id"],
                "scenario": scenario,
                "scope_id": pack["flow_scope_id"],
                "node_ids": sorted(pack["selected_node_ids"]),
                "edge_ids": sorted(pack["selected_edge_ids"]),
            }
        )
        pack["flow_selection_id"] = selection_id
        pack["flow_subgraph"] = _flow_selection(
            graph,
            selection_id,
            pack["selected_node_ids"],
            pack["selected_edge_ids"],
            pack["flow_scope_id"],
            scenario,
        )


class PanelContractIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = load_source_fixture()
        self.base = compose_panel(self.inputs)

    def assert_model_changed_layout_stable(self, changed: dict) -> None:
        model = compose_panel(changed)
        self.assertNotEqual(self.base["panel_model_id"], model["panel_model_id"])
        self.assertNotEqual(self.base["panel_id"], model["panel_id"])
        self.assertEqual(self.base["manifest"]["layout_id"], model["manifest"]["layout_id"])

    def test_same_inputs_are_byte_stable_and_match_golden_identities(self) -> None:
        self.assertEqual(self.base, compose_panel(copy.deepcopy(self.inputs)))
        golden = load_json(IDENTITY_GOLDEN_PATH)
        self.assertEqual(golden["internal-full"], {
            "panel_model_id": self.base["panel_model_id"],
            "layout_id": self.base["manifest"]["layout_id"],
            "panel_id": self.base["panel_id"],
        })

    def test_status_snapshot_meeting_pack_history_and_future_do_not_collide(self) -> None:
        status = copy.deepcopy(self.inputs)
        status["program_status"]["snapshot_id"] = "ps-current-reissued"
        status["roadmap"]["program_status_snapshot_id"] = "ps-current-reissued"
        for pack in status["meeting_packs"].values():
            pack["program_status_snapshot_id"] = "ps-current-reissued"
            pack["program_status"]["snapshot_id"] = "ps-current-reissued"
        self.assert_model_changed_layout_stable(status)

        meeting = copy.deepcopy(self.inputs)
        meeting["meeting_packs"]["fde-morning"]["meeting_pack_id"] = "2026-07-13-fde-morning-reissued"
        self.assert_model_changed_layout_stable(meeting)

        history = copy.deepcopy(self.inputs)
        history["request"]["history_snapshot_ids"] = ["ps-history-2026-06-29"]
        self.assert_model_changed_layout_stable(history)

        future = copy.deepcopy(self.inputs)
        future["request"]["future_horizon_dates"] = []
        self.assert_model_changed_layout_stable(future)

    def test_state_only_change_preserves_topology_and_layout(self) -> None:
        changed = copy.deepcopy(self.inputs)
        graph = changed["flow_graph"]
        topology_id = graph["topology"]["topology_id"]
        graph["state"]["nodes"][0]["health"]["value"] = "at-risk"
        _recompute_flow_identities(graph)
        refresh_pack_flow_selections(changed)
        model = compose_panel(changed)
        self.assertEqual(topology_id, graph["topology"]["topology_id"])
        self.assertNotEqual(self.base["manifest"]["state_snapshot_id"], model["manifest"]["state_snapshot_id"])
        self.assertEqual(self.base["manifest"]["layout_id"], model["manifest"]["layout_id"])
        self.assertNotEqual(self.base["panel_model_id"], model["panel_model_id"])
        self.assertNotEqual(self.base["panel_id"], model["panel_id"])

    def test_topology_or_flow_scope_change_changes_layout_identity(self) -> None:
        topology = copy.deepcopy(self.inputs)
        topology["flow_graph"]["topology"]["nodes"][0]["name"] += " revised"
        _recompute_flow_identities(topology["flow_graph"])
        refresh_pack_flow_selections(topology)
        topology_model = compose_panel(topology)
        self.assertNotEqual(self.base["manifest"]["topology_id"], topology_model["manifest"]["topology_id"])
        self.assertNotEqual(self.base["manifest"]["layout_id"], topology_model["manifest"]["layout_id"])

        scope = copy.deepcopy(self.inputs)
        scope["request"]["project_lead_node_ids"] = ["M-A", "G-MERGE"]
        scope["request"]["project_lead_edge_ids"] = ["E-A-MERGE"]
        scope_model = compose_panel(scope)
        self.assertNotEqual(self.base["selection"]["flow_scopes"]["project-lead"]["selection_id"], scope_model["selection"]["flow_scopes"]["project-lead"]["selection_id"])
        self.assertNotEqual(self.base["manifest"]["layout_id"], scope_model["manifest"]["layout_id"])
        self.assertNotEqual(self.base["panel_id"], scope_model["panel_id"])

    def test_locale_and_layout_resources_change_only_panel_identity_layers(self) -> None:
        locale = copy.deepcopy(self.inputs)
        locale["request"]["locale"] = "en"
        locale_model = compose_panel(locale)
        self.assertNotEqual(self.base["manifest"]["layout_id"], locale_model["manifest"]["layout_id"])
        self.assertNotEqual(self.base["panel_model_id"], locale_model["panel_model_id"])
        self.assertNotEqual(self.base["panel_id"], locale_model["panel_id"])
        for key in ("topology_id", "state_snapshot_id", "overlay_snapshot_id", "flow_graph_id"):
            self.assertEqual(self.base["manifest"][key], locale_model["manifest"][key])

        dimensions = copy.deepcopy(self.inputs)
        dimensions["request"]["layout"]["node_dimensions_version"] = "panel-node-dimensions-v2"
        dimensions_model = compose_panel(dimensions)
        self.assertEqual(self.base["panel_model_id"], dimensions_model["panel_model_id"])
        self.assertNotEqual(self.base["manifest"]["layout_id"], dimensions_model["manifest"]["layout_id"])
        self.assertNotEqual(self.base["panel_id"], dimensions_model["panel_id"])

        config = copy.deepcopy(self.inputs)
        config["request"]["layout"]["config_sha256"] = "sha256:" + "9" * 64
        config_model = compose_panel(config)
        self.assertEqual(self.base["panel_model_id"], config_model["panel_model_id"])
        self.assertNotEqual(self.base["manifest"]["layout_id"], config_model["manifest"]["layout_id"])

    def test_shareable_profile_matches_frozen_identity(self) -> None:
        shareable = copy.deepcopy(self.inputs)
        shareable["request"]["distribution_profile"] = "shareable-summary"
        model = compose_panel(shareable)
        golden = load_json(IDENTITY_GOLDEN_PATH)
        self.assertEqual(golden["shareable-summary"], {
            "panel_model_id": model["panel_model_id"],
            "layout_id": model["manifest"]["layout_id"],
            "panel_id": model["panel_id"],
        })


if __name__ == "__main__":
    unittest.main()
