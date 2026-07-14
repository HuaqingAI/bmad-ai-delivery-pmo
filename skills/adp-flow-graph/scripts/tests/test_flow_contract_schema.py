import copy
import unittest

from flow_contract_testkit import (
    FLOW_SCHEMA_PATH,
    GOLDEN_PATH,
    SOURCE_FIXTURE_PATH,
    SOURCE_SCHEMAS,
    load_json,
    validate_schema,
)


class FlowContractSchemaTests(unittest.TestCase):
    def test_golden_graph_conforms_and_forbids_layout_identity(self) -> None:
        schema = load_json(FLOW_SCHEMA_PATH)
        golden = load_json(GOLDEN_PATH)
        self.assertEqual([], validate_schema(golden, schema))
        with_layout = copy.deepcopy(golden)
        with_layout["layout_id"] = "sha256:" + "0" * 64
        self.assertTrue(validate_schema(with_layout, schema))
        self.assertEqual("adp-management-panel", golden["layout_identity_owner"])

    def test_each_source_owner_golden_conforms_to_its_schema(self) -> None:
        fixture = load_json(SOURCE_FIXTURE_PATH)
        for owner, path in SOURCE_SCHEMAS.items():
            with self.subTest(owner=owner):
                self.assertEqual([], validate_schema(fixture[owner], load_json(path)))

    def test_source_schemas_reject_axis_enum_duplicate_relation_and_condition_guess(self) -> None:
        fixture = load_json(SOURCE_FIXTURE_PATH)

        invalid_state = copy.deepcopy(fixture["program_status"])
        invalid_state["node_states"][0]["execution"]["value"] = "active"
        self.assertTrue(validate_schema(invalid_state, load_json(SOURCE_SCHEMAS["program_status"])))

        duplicate_relation = copy.deepcopy(fixture["actions"])
        duplicate_relation["actions"][0]["related_plan_item_ids"] = ["M-A", "M-A"]
        self.assertTrue(validate_schema(duplicate_relation, load_json(SOURCE_SCHEMAS["actions"])))

        invalid_risk = copy.deepcopy(fixture["risks"])
        invalid_risk["risks"][0]["lifecycle"] = "probably-open"
        self.assertTrue(validate_schema(invalid_risk, load_json(SOURCE_SCHEMAS["risks"])))

        baseline = copy.deepcopy(fixture["baseline"])
        baseline["milestones"][0]["dependencies"] = [
            {
                "edge_id": "E-CONDITION",
                "predecessor": "M-B",
                "relationship_type": "conditional",
                "source": baseline["milestones"][0]["source"],
                "baseline_revision": 2,
            }
        ]
        self.assertTrue(validate_schema(baseline, load_json(SOURCE_SCHEMAS["baseline"])))

    def test_schema_names_every_frozen_boundary(self) -> None:
        encoded = "\n".join(path.read_text(encoding="utf-8") for path in [FLOW_SCHEMA_PATH, *SOURCE_SCHEMAS.values()])
        for field in (
            "predecessor_rule",
            "aggregation",
            "conditional",
            "rework",
            "execution",
            "health",
            "started_at",
            "done_at",
            "related_plan_item_ids",
            "related_flow_edge_ids",
            "risk_id",
            "processed_window",
            "unmapped",
            "topology_id",
            "state_snapshot_id",
            "overlay_snapshot_id",
            "flow_graph_id",
        ):
            self.assertIn(f'"{field}"', encoded)


if __name__ == "__main__":
    unittest.main()
