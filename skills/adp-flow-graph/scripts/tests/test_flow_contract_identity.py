import copy
import unittest

from flow_contract_testkit import GOLDEN_PATH, canonical_hash, compute_identities, load_json


class FlowContractIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = load_json(GOLDEN_PATH)
        self.ids = compute_identities(self.graph)

    def test_golden_ids_match_frozen_canonical_inputs(self) -> None:
        self.assertEqual(self.graph["topology"]["topology_id"], self.ids["topology_id"])
        self.assertEqual(self.graph["state"]["state_snapshot_id"], self.ids["state_snapshot_id"])
        self.assertEqual(self.graph["overlays"]["overlay_snapshot_id"], self.ids["overlay_snapshot_id"])
        self.assertEqual(self.graph["flow_graph_id"], self.ids["flow_graph_id"])

    def test_topology_state_and_overlay_changes_have_separate_identity_blast_radius(self) -> None:
        topology_change = copy.deepcopy(self.graph)
        topology_change["topology"]["nodes"][0]["name"] += " revised"
        topology_ids = compute_identities(topology_change)
        self.assertNotEqual(self.ids["topology_id"], topology_ids["topology_id"])
        self.assertNotEqual(self.ids["state_snapshot_id"], topology_ids["state_snapshot_id"])
        self.assertNotEqual(self.ids["overlay_snapshot_id"], topology_ids["overlay_snapshot_id"])

        state_change = copy.deepcopy(self.graph)
        state_change["state"]["nodes"][0]["health"]["value"] = "at-risk"
        state_ids = compute_identities(state_change)
        self.assertEqual(self.ids["topology_id"], state_ids["topology_id"])
        self.assertEqual(self.ids["overlay_snapshot_id"], state_ids["overlay_snapshot_id"])
        self.assertNotEqual(self.ids["state_snapshot_id"], state_ids["state_snapshot_id"])
        self.assertNotEqual(self.ids["flow_graph_id"], state_ids["flow_graph_id"])

        overlay_change = copy.deepcopy(self.graph)
        pending = overlay_change["overlays"]["scopes"][0]["allocations"][0]["counts"]["pending"]
        pending["count"] = 2
        pending["source_refs"].append({"source_kind": "action", "source_id": "A-SECOND", "source_fingerprint": "sha256:" + "8" * 64})
        overlay_ids = compute_identities(overlay_change)
        self.assertEqual(self.ids["topology_id"], overlay_ids["topology_id"])
        self.assertEqual(self.ids["state_snapshot_id"], overlay_ids["state_snapshot_id"])
        self.assertNotEqual(self.ids["overlay_snapshot_id"], overlay_ids["overlay_snapshot_id"])
        self.assertNotEqual(self.ids["flow_graph_id"], overlay_ids["flow_graph_id"])

    def test_set_order_is_stable_and_layout_identity_is_panel_only(self) -> None:
        reordered = copy.deepcopy(self.graph)
        reordered["topology"]["nodes"].reverse()
        reordered["topology"]["edges"].reverse()
        reordered["state"]["nodes"].reverse()
        reordered["state"]["relationships"].reverse()
        reordered["overlays"]["scopes"][0]["allocations"].reverse()
        self.assertEqual(self.ids, compute_identities(reordered))

        layout_a = canonical_hash({"owner": "adp-management-panel", "topology_id": self.ids["topology_id"], "filter": "all", "locale": "en", "node_dimensions": "v1", "elk": "0.9.3"})
        layout_b = canonical_hash({"owner": "adp-management-panel", "topology_id": self.ids["topology_id"], "filter": "all", "locale": "zh-CN", "node_dimensions": "v1", "elk": "0.9.3"})
        self.assertNotEqual(layout_a, layout_b)
        self.assertEqual(self.ids, compute_identities(self.graph))
        self.assertNotIn("layout_id", self.graph)


if __name__ == "__main__":
    unittest.main()
