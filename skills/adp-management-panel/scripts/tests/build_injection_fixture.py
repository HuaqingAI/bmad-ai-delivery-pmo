#!/usr/bin/env python3
"""Write a fully composed canonical-input fixture with hostile source labels."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import panel_model


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_injection_fixture.py OUTPUT.json")
    inputs = panel_model.load_source_fixture()
    labels = panel_model.load_json(panel_model.MALICIOUS_FIXTURE_PATH)["labels"]
    inputs["flow_graph"]["topology"]["nodes"][0]["name"] = labels[0]
    inputs["flow_graph"]["topology"]["nodes"][1]["name"] = labels[1]
    panel_model._recompute_flow_identities(inputs["flow_graph"])
    graph = inputs["flow_graph"]
    for scenario, pack in inputs["meeting_packs"].items():
        selection_id = panel_model.canonical_hash(
            {
                "flow_graph_id": graph["flow_graph_id"],
                "scenario": scenario,
                "scope_id": pack["flow_scope_id"],
                "node_ids": sorted(pack["selected_node_ids"]),
                "edge_ids": sorted(pack["selected_edge_ids"]),
            }
        )
        pack["flow_selection_id"] = selection_id
        pack["flow_subgraph"] = panel_model._flow_selection(
            graph,
            selection_id,
            pack["selected_node_ids"],
            pack["selected_edge_ids"],
            pack["flow_scope_id"],
            scenario,
        )
    inputs["meeting_packs"]["business-biweekly"]["boards"]["business_decisions"][0]["summary"] = labels[-1]
    inputs["meeting_packs"]["business-biweekly"]["boards"]["business_decisions"][0]["Source"] = (
        "{'artifact_path': '../../outside.md'}"
    )
    Path(sys.argv[1]).write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
