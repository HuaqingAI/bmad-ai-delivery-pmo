#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "skills/adp-plan-baseline/scripts"))
sys.path.insert(0, str(REPO_ROOT / "skills/adp-flow-graph/scripts"))

from baseline import parse_baseline  # noqa: E402
from flow_graph import topology_projection  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify real-project Phase 12 identity breaks.")
    parser.add_argument("--revision-1-baseline", type=Path, required=True)
    parser.add_argument("--revision-2-baseline", type=Path, required=True)
    parser.add_argument("--accepted-flow", type=Path, required=True)
    parser.add_argument("--flow-snapshots", type=Path, required=True)
    parser.add_argument("--accepted-panel", type=Path, required=True)
    parser.add_argument("--revision-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline_1 = parse_baseline(args.revision_1_baseline)
    baseline_2 = parse_baseline(args.revision_2_baseline)
    topology_1 = topology_projection(baseline_1, "plans/program-baseline.md")
    topology_2 = topology_projection(baseline_2, "plans/program-baseline.md")
    accepted_flow = load_json(args.accepted_flow)
    accepted_panel = load_json(args.accepted_panel)
    revision_audit_result = load_json(args.revision_audit)
    revision_audit = load_json(Path(revision_audit_result["outputs"]["json"]))
    variants = [load_json(path) for path in sorted(args.flow_snapshots.glob("fg-*.json"))]

    topology_ids = {item["topology"]["topology_id"] for item in variants}
    state_ids = {item["state"]["state_snapshot_id"] for item in variants}
    overlay_ids = {item["overlays"]["overlay_snapshot_id"] for item in variants}
    flow_ids = {item["flow_graph_id"] for item in variants}
    blocking_codes = sorted({
        str(item.get("code") or item.get("gap_type") or item.get("id"))
        for item in revision_audit.get("blocking_gaps", [])
    })

    checks = {
        "accepted_flow_matches_revision_1": accepted_flow["topology"]["topology_id"] == topology_1["topology_id"],
        "revision_incremented": baseline_1["revision"] == 1 and baseline_2["revision"] == 2,
        "topology_identity_changed_at_revision_break": topology_1["topology_id"] != topology_2["topology_id"],
        "overlay_only_variants_preserve_topology": len(topology_ids) == 1,
        "overlay_only_variants_preserve_state": len(state_ids) == 1,
        "overlay_only_variants_change_overlay_and_flow": len(overlay_ids) > 1 and len(flow_ids) > 1,
        "stale_revision_consumers_blocked": revision_audit_result["execution_disposition"] == "blocked",
        "accepted_panel_still_binds_revision_1": (
            accepted_panel["manifest"]["baseline_revision"] == 1
            and accepted_panel["manifest"]["topology_id"] == topology_1["topology_id"]
        ),
    }
    result = {
        "status": "complete" if all(checks.values()) else "failed",
        "checks": checks,
        "revision_break": {
            "from_revision": baseline_1["revision"],
            "to_revision": baseline_2["revision"],
            "from_topology_id": topology_1["topology_id"],
            "to_topology_id": topology_2["topology_id"],
            "node_counts": [len(topology_1["nodes"]), len(topology_2["nodes"])],
            "edge_counts": [len(topology_1["edges"]), len(topology_2["edges"])],
            "post_update_audit_id": revision_audit_result["input_audit_id"],
            "post_update_disposition": revision_audit_result["execution_disposition"],
            "blocking_codes": blocking_codes,
        },
        "overlay_only_variants": {
            "topology_ids": sorted(topology_ids),
            "state_snapshot_ids": sorted(state_ids),
            "overlay_snapshot_ids": sorted(overlay_ids),
            "flow_graph_ids": sorted(flow_ids),
        },
        "accepted_panel_preserved": {
            "panel_id": accepted_panel["panel_id"],
            "layout_id": accepted_panel["manifest"]["layout_id"],
            "baseline_revision": accepted_panel["manifest"]["baseline_revision"],
            "topology_id": accepted_panel["manifest"]["topology_id"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
