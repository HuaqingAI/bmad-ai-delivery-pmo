#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    skill_root = project_root / "skills/adp-management-panel"
    sys.path.insert(0, str(skill_root / "scripts"))
    import panel_model  # type: ignore

    inputs = panel_model.load_source_fixture()
    model = panel_model.compose_panel(inputs)
    sources = panel_model.binding_sources(inputs)
    checks: list[dict[str, Any]] = []
    for view in model["views"]:
        for section in view["sections"]:
            for binding in section["bindings"]:
                target = panel_model.resolve_pointer(model, binding["target_pointer"])
                source = panel_model.resolve_pointer(sources[binding["source_artifact"]], binding["source_pointer"])
                checks.append(
                    {
                        "view_id": view["view_id"],
                        "section_id": section["section_id"],
                        "operation": binding["operation"],
                        "target_pointer": binding["target_pointer"],
                        "source_artifact": binding["source_artifact"],
                        "source_pointer": binding["source_pointer"],
                        "target_sha256": digest(target),
                        "source_sha256": digest(source),
                        "copy_equal": binding["operation"] != "copy" or target == source,
                    }
                )
    payload = {
        "fixture_schema_version": "1.0.0",
        "source_fixture_path": "skills/adp-management-panel/assets/fixtures/panel-contract-v1/panel-source-fixture.json",
        "source_fixture_sha256": "sha256:" + hashlib.sha256(panel_model.SOURCE_FIXTURE_PATH.read_bytes()).hexdigest(),
        "composition_inputs": inputs,
        "model_v1": model,
        "flow_graph_v1": inputs["flow_graph"],
        "consumer_binding_checks": checks,
        "required_view_ids": ["project-lead", "fde-morning", "business-biweekly"],
        "required_data_keys": ["status", "roadmap", "flows", "meetings", "history"],
        "required_flow_keys": ["project-lead", "fde-morning", "business-biweekly"],
        "required_meeting_keys": ["fde-morning", "business-biweekly"],
        "required_board_keys": {
            "fde-morning": sorted(model["data"]["meetings"]["fde-morning"]["boards"]),
            "business-biweekly": sorted(model["data"]["meetings"]["business-biweekly"]["boards"]),
        },
    }
    Path(args.output).resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
