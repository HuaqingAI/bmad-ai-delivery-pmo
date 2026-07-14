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
    inputs["meeting_packs"]["business-biweekly"]["boards"]["business_decisions"][0]["summary"] = labels[-1]
    Path(sys.argv[1]).write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
