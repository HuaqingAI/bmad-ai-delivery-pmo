import unittest

from flow_contract_testkit import GOLDEN_PATH, graph_semantic_errors, load_json


class FlowContractGoldenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = load_json(GOLDEN_PATH)

    def test_parallel_aggregation_conditional_rework_and_cross_lane_are_explicit(self) -> None:
        topology = self.graph["topology"]
        nodes = {item["node_id"]: item for item in topology["nodes"]}
        edges = {item["edge_id"]: item for item in topology["edges"]}
        self.assertEqual("L1", nodes["M-A"]["lane"]["lane_id"])
        self.assertEqual("L2", nodes["M-B"]["lane"]["lane_id"])
        self.assertEqual("program", nodes["G-MERGE"]["lane"]["lane_type"])
        self.assertEqual("all", nodes["G-MERGE"]["predecessor_rule"])
        self.assertEqual(2, sum(item["target"] == "G-MERGE" and item["relationship_type"] == "aggregation" for item in edges.values()))
        self.assertEqual("DECISION-1", edges["E-CONDITION"]["condition"]["fact_id"])
        self.assertEqual("rework", edges["E-REWORK"]["relationship_type"])
        self.assertNotEqual(nodes[edges["E-INFO"]["predecessor"]]["lane"], nodes[edges["E-INFO"]["target"]]["lane"])

    def test_execution_health_and_relationship_axes_are_orthogonal_and_traceable(self) -> None:
        states = {item["node_id"]: item for item in self.graph["state"]["nodes"]}
        self.assertEqual(("ready", "blocked"), (states["G-MERGE"]["execution"]["value"], states["G-MERGE"]["health"]["value"]))
        self.assertEqual(("in-progress", "at-risk"), (states["M-A"]["execution"]["value"], states["M-A"]["health"]["value"]))
        self.assertNotEqual(states["G-MERGE"]["execution"]["value"], "in-progress")
        for item in states.values():
            for axis in ("execution", "health"):
                self.assertTrue(item[axis]["rule_id"])
                self.assertTrue(item[axis]["sources"])

        relationships = {item["edge_id"]: item for item in self.graph["state"]["relationships"]}
        self.assertEqual("pending-confirmation", relationships["E-CONDITION"]["state"]["value"])
        self.assertEqual("indeterminate", relationships["E-CONDITION"]["health"]["value"])
        self.assertEqual("inactive", relationships["E-REWORK"]["state"]["value"])
        for item in relationships.values():
            self.assertTrue(item["state"]["sources"])
            self.assertTrue(item["health"]["sources"])

    def test_every_topology_state_relation_and_count_is_source_backed(self) -> None:
        self.assertEqual([], graph_semantic_errors(self.graph))
        for node in self.graph["topology"]["nodes"]:
            self.assertEqual(self.graph["topology"]["baseline_revision"], node["baseline_revision"])
            self.assertTrue(node["source"]["source_fingerprint"])
        for edge in self.graph["topology"]["edges"]:
            self.assertEqual(self.graph["topology"]["baseline_revision"], edge["baseline_revision"])
            self.assertTrue(edge["source"]["source_fingerprint"])


if __name__ == "__main__":
    unittest.main()
