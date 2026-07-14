from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from flow_graph import (  # noqa: E402
    TopologyBlocked,
    build_flow_graph,
    graph_semantic_errors,
    publish_graph,
    topology_projection,
)
from flow_contract_testkit import FLOW_SCHEMA_PATH, load_json, validate_schema  # noqa: E402


FIXTURES = Path(__file__).resolve().parents[2] / "assets/fixtures/flow-contract-v1"


def source_inputs() -> tuple[dict, dict, dict, dict]:
    sources = load_json(FIXTURES / "source-contract-golden.json")
    golden = load_json(FIXTURES / "golden-parallel-aggregation-conditional-rework.json")
    node_ids = {item["id"] for item in [*sources["baseline"]["gates"], *sources["baseline"]["milestones"]]}
    status = {
        "reporting_period": {"start": "2026-07-01", "end": "2026-07-13"},
        "flow_state": {
            "flow_state_schema_version": "1.0.0",
            "baseline_id": sources["baseline"]["baseline_id"],
            "baseline_revision": sources["baseline"]["revision"],
            "as_of": golden["state"]["as_of"],
            "node_states": [
                {
                    "node_id": item["node_id"],
                    "baseline_revision": 2,
                    "evaluated_at": golden["state"]["as_of"],
                    "execution": item["execution"],
                    "health": item["health"],
                }
                for item in golden["state"]["nodes"]
                if item["node_id"] in node_ids
            ],
            "compatibility": {"strategy": "version-required", "migration_error_code": "ADP-FLOW-STATE-MIGRATION-REQUIRED"},
        },
    }
    return sources["baseline"], status, sources["actions"], sources["risks"]


class FlowGraphProjectionTests(unittest.TestCase):
    def test_projection_conforms_to_frozen_schema_and_semantics(self) -> None:
        baseline, status, actions, risks = source_inputs()

        graph = build_flow_graph(baseline, status, actions, risks)

        self.assertEqual(validate_schema(graph, load_json(FLOW_SCHEMA_PATH)), [])
        self.assertEqual(graph_semantic_errors(graph), [])
        self.assertNotIn("layout_id", graph)
        states = {item["node_id"]: item for item in graph["state"]["nodes"]}
        self.assertEqual(states["G-MERGE"]["execution"]["value"], "ready")
        self.assertEqual(states["G-MERGE"]["health"]["value"], "blocked")

    def test_identity_layers_have_exact_blast_radius(self) -> None:
        baseline, status, actions, risks = source_inputs()
        original = build_flow_graph(baseline, status, actions, risks)

        changed_state = copy.deepcopy(status)
        changed_state["flow_state"]["node_states"][0]["health"]["value"] = "at-risk"
        state_graph = build_flow_graph(baseline, changed_state, actions, risks)
        changed_actions = copy.deepcopy(actions)
        changed_actions["actions"][0]["status"] = "blocked"
        changed_actions["actions"][0]["done_at"] = None
        overlay_graph = build_flow_graph(baseline, status, changed_actions, risks)
        changed_baseline = copy.deepcopy(baseline)
        changed_baseline["milestones"][0]["name"] = "Lane A accepted"
        topology_graph = build_flow_graph(changed_baseline, status, actions, risks)

        self.assertEqual(original["topology"]["topology_id"], state_graph["topology"]["topology_id"])
        self.assertNotEqual(original["state"]["state_snapshot_id"], state_graph["state"]["state_snapshot_id"])
        self.assertEqual(original["overlays"]["overlay_snapshot_id"], state_graph["overlays"]["overlay_snapshot_id"])
        self.assertEqual(original["topology"]["topology_id"], overlay_graph["topology"]["topology_id"])
        self.assertEqual(original["state"]["state_snapshot_id"], overlay_graph["state"]["state_snapshot_id"])
        self.assertNotEqual(original["overlays"]["overlay_snapshot_id"], overlay_graph["overlays"]["overlay_snapshot_id"])
        self.assertNotEqual(original["topology"]["topology_id"], topology_graph["topology"]["topology_id"])

    def test_scoped_counts_are_source_equal_half_open_and_unmapped_visible(self) -> None:
        baseline, status, actions, risks = source_inputs()
        fingerprint = actions["actions"][0]["source"]["source_fingerprint"]
        actions["actions"].extend(
            [
                {
                    "action_id": "A-BLOCKED",
                    "status": "blocked",
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "started_at": "2026-07-02T00:00:00Z",
                    "done_at": None,
                    "cancelled_at": None,
                    "baseline_revision": 2,
                    "related_plan_item_ids": ["M-A"],
                    "related_flow_edge_ids": [],
                    "source": {"artifact_id": "ACTION-LEDGER-1", "artifact_path": "actions/action-ledger.md", "source_fingerprint": fingerprint},
                },
                {
                    "action_id": "A-END",
                    "status": "done",
                    "created_at": "2026-06-01T00:00:00Z",
                    "updated_at": "2026-07-14T00:00:00Z",
                    "started_at": None,
                    "done_at": "2026-07-14T00:00:00Z",
                    "cancelled_at": None,
                    "baseline_revision": 2,
                    "related_plan_item_ids": ["M-A"],
                    "related_flow_edge_ids": [],
                    "source": {"artifact_id": "ACTION-LEDGER-1", "artifact_path": "actions/action-ledger.md", "source_fingerprint": fingerprint},
                },
                {
                    "action_id": "A-UNMAPPED",
                    "status": "open",
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "started_at": None,
                    "done_at": None,
                    "cancelled_at": None,
                    "baseline_revision": 2,
                    "related_plan_item_ids": [],
                    "related_flow_edge_ids": [],
                    "source": {"artifact_id": "ACTION-LEDGER-1", "artifact_path": "actions/action-ledger.md", "source_fingerprint": fingerprint},
                },
            ]
        )
        graph = build_flow_graph(baseline, status, actions, risks)
        active = next(item for item in graph["overlays"]["scopes"] if item["scope_kind"] == "active-as-of")
        allocation = next(item for item in active["allocations"] if item["target_id"] == "M-A")

        self.assertEqual(allocation["counts"]["pending"]["count"], 1)
        self.assertEqual(allocation["counts"]["blocked"]["count"], 1)
        self.assertEqual(allocation["counts"]["processed"]["count"], 1)
        self.assertEqual([item["source_id"] for item in allocation["counts"]["processed"]["source_refs"]], ["A-DONE"])
        self.assertEqual([item["source_id"] for item in graph["overlays"]["unmapped"]], ["A-UNMAPPED"])

    def test_invalid_topology_blocks_without_reusing_a_graph(self) -> None:
        baseline, _, _, _ = source_inputs()
        baseline["milestones"][0]["dependencies"] = [
            {
                "edge_id": "E-UNKNOWN",
                "predecessor": "UNKNOWN",
                "relationship_type": "dependency",
                "source": baseline["milestones"][0]["source"],
                "baseline_revision": 2,
            }
        ]

        with self.assertRaises(TopologyBlocked) as raised:
            topology_projection(baseline, "plans/program-baseline.md")

        self.assertEqual(raised.exception.findings[0]["code"], "flow.reference.unknown")

    def test_current_and_immutable_publication_is_idempotent_and_rolls_back_pair(self) -> None:
        baseline, status, actions, risks = source_inputs()
        graph = build_flow_graph(baseline, status, actions, risks)
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            first = publish_graph(memory, graph)
            second = publish_graph(memory, graph)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(Path(first["current"]).read_text(encoding="utf-8")), graph)
            before_current = Path(first["current"]).read_bytes()
            before_latest = Path(first["latest"]).read_bytes()
            changed_status = copy.deepcopy(status)
            changed_status["flow_state"]["node_states"][0]["health"]["value"] = "at-risk"
            changed = build_flow_graph(baseline, changed_status, actions, risks)
            original_replace = __import__("flow_graph").os.replace
            failure = {"injected": False}

            def fail_latest(source, target):
                if Path(target).name == "latest.json" and not failure["injected"]:
                    failure["injected"] = True
                    raise OSError("injected latest failure")
                return original_replace(source, target)

            with patch("flow_graph.os.replace", side_effect=fail_latest), self.assertRaises(OSError):
                publish_graph(memory, changed)
            self.assertEqual(Path(first["current"]).read_bytes(), before_current)
            self.assertEqual(Path(first["latest"]).read_bytes(), before_latest)


if __name__ == "__main__":
    unittest.main()
