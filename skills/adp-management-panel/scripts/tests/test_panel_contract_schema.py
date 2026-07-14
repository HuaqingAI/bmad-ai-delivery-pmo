import copy
import unittest

from panel_contract_testkit import (
    CATALOG_PATH,
    FLOW_GOLDEN_PATH,
    MANIFEST_SCHEMA_PATH,
    PANEL_SCHEMA_PATH,
    PROGRESS_GOLDEN_PATH,
    VIEW_SECTIONS,
    VISUALIZATION_MODES,
    compose_panel,
    load_json,
    load_source_fixture,
    validate_schema,
)


class PanelContractSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = load_source_fixture()
        self.model = compose_panel(self.inputs)

    def test_internal_model_and_embedded_manifest_conform(self) -> None:
        self.assertEqual([], validate_schema(self.model, load_json(PANEL_SCHEMA_PATH)))
        self.assertEqual([], validate_schema(self.model["manifest"], load_json(MANIFEST_SCHEMA_PATH)))
        self.assertEqual(self.model["panel_model_id"], self.model["manifest"]["panel_model_id"])
        self.assertEqual(self.model["panel_id"], self.model["manifest"]["panel_id"])

    def test_authoritative_progress_and_flow_fixtures_remain_schema_shaped(self) -> None:
        progress_schema = load_json(PROGRESS_GOLDEN_PATH.parent.parent.parent / "program-status-progress-v2.schema.json")
        flow_schema = load_json(FLOW_GOLDEN_PATH.parent.parent.parent / "adp-flow-graph-v1.schema.json")
        self.assertEqual([], validate_schema(self.inputs["program_status"]["progress"], progress_schema))
        self.assertEqual([], validate_schema(self.inputs["flow_graph"], flow_schema))

    def test_view_modes_sections_and_catalog_identifiers_are_frozen(self) -> None:
        catalog = load_json(CATALOG_PATH)
        self.assertEqual(list(VIEW_SECTIONS), catalog["identifiers"]["views"])
        self.assertEqual(VISUALIZATION_MODES, catalog["identifiers"]["visualization_modes"])
        self.assertEqual(VIEW_SECTIONS, catalog["identifiers"]["sections"])
        self.assertEqual(list(VIEW_SECTIONS), [item["view_id"] for item in self.model["views"]])
        for view in self.model["views"]:
            self.assertEqual(VISUALIZATION_MODES, view["visualization_modes"])
            self.assertEqual(VIEW_SECTIONS[view["view_id"]], [item["section_id"] for item in view["sections"]])

    def test_schema_rejects_extra_model_fields_and_invalid_binding_operations(self) -> None:
        extra = copy.deepcopy(self.model)
        extra["computed_progress"] = 50
        self.assertTrue(validate_schema(extra, load_json(PANEL_SCHEMA_PATH)))

        invalid_operation = copy.deepcopy(self.model)
        invalid_operation["views"][0]["sections"][0]["bindings"][0]["operation"] = "calculate"
        self.assertTrue(validate_schema(invalid_operation, load_json(PANEL_SCHEMA_PATH)))

    def test_manifest_schema_names_every_frozen_artifact_identity(self) -> None:
        encoded = MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")
        for field in (
            "panel_model_id",
            "panel_id",
            "program_status_snapshot_id",
            "roadmap_fingerprint",
            "topology_id",
            "state_snapshot_id",
            "overlay_snapshot_id",
            "flow_graph_id",
            "meeting_pack_ids",
            "history_snapshot_ids",
            "future_horizon_dates",
            "flow_selection_ids",
            "layout_id",
            "distribution_profile",
            "redaction",
            "safe_embedding",
            "recovery_status",
        ):
            self.assertIn(f'"{field}"', encoded)


if __name__ == "__main__":
    unittest.main()
