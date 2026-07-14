import copy
import json
import unittest

from panel_contract_testkit import (
    MALICIOUS_FIXTURE_PATH,
    MANIFEST_SCHEMA_PATH,
    PANEL_SCHEMA_PATH,
    compose_panel,
    load_json,
    load_source_fixture,
    safe_json_for_script,
    validate_schema,
)


class PanelContractRedactionEmbeddingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = load_source_fixture()
        self.shareable_inputs = copy.deepcopy(self.inputs)
        self.shareable_inputs["request"]["distribution_profile"] = "shareable-summary"
        self.model = compose_panel(self.shareable_inputs)

    def test_shareable_model_conforms_and_records_redaction(self) -> None:
        self.assertEqual([], validate_schema(self.model, load_json(PANEL_SCHEMA_PATH)))
        self.assertEqual([], validate_schema(self.model["manifest"], load_json(MANIFEST_SCHEMA_PATH)))
        redaction = self.model["manifest"]["redaction"]
        self.assertEqual("shareable-summary", redaction["profile"])
        self.assertGreater(redaction["hidden_nodes"], 0)
        self.assertGreater(redaction["hidden_edges"], 0)
        self.assertGreater(redaction["hidden_sources"], 0)
        self.assertGreater(redaction["hidden_counts"], 0)
        self.assertFalse(redaction["topology_reconnected"])

    def test_hidden_topology_is_removed_without_reconnecting_neighbors(self) -> None:
        visible_source_edges = {
            (edge["predecessor"], edge["target"])
            for edge in self.inputs["flow_graph"]["topology"]["edges"]
            if edge["edge_id"] in self.inputs["shareable_policy"]["visible_edge_ids"]
            and edge["predecessor"] in self.inputs["shareable_policy"]["visible_node_ids"]
            and edge["target"] in self.inputs["shareable_policy"]["visible_node_ids"]
        }
        flow = self.model["data"]["flows"]["project-lead"]
        self.assertEqual(len(visible_source_edges), len(flow["edges"]))
        self.assertFalse(any(edge["predecessor"] == edge["target"] for edge in flow["edges"]))
        self.assertFalse(any(edge.get("synthetic") or edge.get("reconnected") for edge in flow["edges"]))
        self.assertNotIn("M-B", json.dumps(self.model, ensure_ascii=False))

    def test_internal_owner_counts_paths_and_source_payloads_do_not_leak(self) -> None:
        encoded = json.dumps(self.model["data"], ensure_ascii=False, sort_keys=True)
        for secret in (
            "internal-owner@example.com",
            "views/program-status.json",
            "views/roadmap.json",
            "views/meeting-packs/fde.json",
            '"allocations"',
            '"source_fingerprints"',
            '"source_refs"',
            '"owner"',
        ):
            self.assertNotIn(secret, encoded)
        self.assertTrue(all(key.startswith("source-") for key in self.model["manifest"]["source_fingerprints"]))

    def test_malicious_labels_are_json_script_safe(self) -> None:
        malicious = load_json(MALICIOUS_FIXTURE_PATH)
        embedded = safe_json_for_script(malicious)
        self.assertNotIn("<", embedded)
        self.assertNotIn(">", embedded)
        self.assertNotIn("&", embedded)
        self.assertNotIn("\u2028", embedded)
        self.assertNotIn("\u2029", embedded)
        self.assertNotIn("</script", embedded.lower())
        self.assertIn("\\u003c/script\\u003e", embedded)
        self.assertEqual(
            ["innerHTML", "foreignObject", "event-attributes", "external-href", "source-css"],
            self.model["manifest"]["safe_embedding"]["forbidden"],
        )


if __name__ == "__main__":
    unittest.main()
