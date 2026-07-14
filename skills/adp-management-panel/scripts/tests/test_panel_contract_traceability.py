import copy
import unittest

from panel_contract_testkit import (
    _flow_selection,
    _recompute_flow_identities,
    binding_errors,
    compose_panel,
    load_source_fixture,
)


def sync_progress(inputs: dict, progress: dict) -> None:
    inputs["program_status"]["progress"] = copy.deepcopy(progress)
    inputs["roadmap"]["progress"] = copy.deepcopy(progress)
    for pack in inputs["meeting_packs"].values():
        pack["program_status"]["progress"] = copy.deepcopy(progress)


class PanelContractTraceabilityTests(unittest.TestCase):
    def test_every_copy_binding_resolves_to_an_equal_canonical_value(self) -> None:
        inputs = load_source_fixture()
        model = compose_panel(inputs)
        self.assertEqual([], binding_errors(model, inputs))
        operations = {
            binding["operation"]
            for view in model["views"]
            for section in view["sections"]
            for binding in section["bindings"]
        }
        self.assertLessEqual(operations, {"copy", "allowlist", "stable-sort", "select", "redact"})

    def test_progress_values_are_copied_even_when_arithmetically_unusual(self) -> None:
        inputs = load_source_fixture()
        progress = copy.deepcopy(inputs["program_status"]["progress"])
        current = progress["overall"]["current"]
        current["actual_completion_percent"] = 99
        current["planned_completion_percent"] = 1
        current["completion_gap_pp"] = 777
        sync_progress(inputs, progress)
        model = compose_panel(inputs)
        displayed = model["data"]["status"]["progress"]["overall"]["current"]
        self.assertEqual({"actual_completion_percent": 99, "planned_completion_percent": 1, "completion_gap_pp": 777}, {
            key: displayed[key]
            for key in ("actual_completion_percent", "planned_completion_percent", "completion_gap_pp")
        })
        self.assertEqual(progress, model["data"]["roadmap"]["progress"])

    def test_state_counts_and_branch_state_are_copied_without_inference(self) -> None:
        inputs = load_source_fixture()
        graph = inputs["flow_graph"]
        graph["state"]["relationships"][0]["state"]["value"] = "pending-confirmation"
        allocation = graph["overlays"]["scopes"][0]["allocations"][0]["counts"]["pending"]
        allocation["count"] = 9
        allocation["source_refs"] = [
            {"source_kind": "action", "source_id": f"A-{index}", "source_fingerprint": "sha256:" + f"{index:x}" * 64}
            for index in range(1, 10)
        ]
        _recompute_flow_identities(graph)
        for scenario, pack in inputs["meeting_packs"].items():
            pack["flow_subgraph"] = _flow_selection(
                graph,
                pack["flow_selection_id"],
                pack["selected_node_ids"],
                pack["selected_edge_ids"],
                pack["flow_scope_id"],
                scenario,
            )
            pack["flow_subgraph"]["flow_graph_id"] = graph["flow_graph_id"]
        model = compose_panel(inputs)
        displayed = model["data"]["flows"]["project-lead"]
        self.assertEqual("pending-confirmation", displayed["relationship_states"][0]["state"]["value"])
        displayed_allocation = next(item for item in displayed["allocations"] if item["target_id"] == "M-A")
        self.assertEqual(9, displayed_allocation["counts"]["pending"]["count"])
        self.assertEqual(graph["topology"]["edges"], inputs["flow_graph"]["topology"]["edges"])

    def test_meeting_views_copy_owner_selected_subgraphs_and_information_budgets(self) -> None:
        inputs = load_source_fixture()
        model = compose_panel(inputs)
        for scenario in ("fde-morning", "business-biweekly"):
            self.assertEqual(inputs["meeting_packs"][scenario]["flow_subgraph"], model["data"]["flows"][scenario])
            self.assertEqual(inputs["meeting_packs"][scenario]["boards"], model["data"]["meetings"][scenario]["boards"])
            self.assertEqual(inputs["meeting_packs"][scenario]["flow_selection_id"], model["selection"]["flow_scopes"][scenario]["selection_id"])


if __name__ == "__main__":
    unittest.main()
