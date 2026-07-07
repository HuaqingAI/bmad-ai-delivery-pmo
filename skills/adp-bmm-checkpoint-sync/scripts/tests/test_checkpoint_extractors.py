import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checkpoint_extractors import discover_candidate


class CheckpointExtractorTests(unittest.TestCase):
    def test_validation_gate_json_extracts_status_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            gate = project_root / "gate-decision.json"
            gate.write_text(json.dumps({"gate": "PASS", "coverage": {"statements": 92}}), encoding="utf-8")

            candidate, preview, warnings = discover_candidate(
                project_root,
                "l1-checkout",
                "validation",
                [str(gate)],
                summary="Validation candidate",
            )

            self.assertFalse(warnings)
            self.assertEqual(candidate["artifact"]["kind"], "gate")
            self.assertEqual(candidate["claims"]["decisions"], [])
            fields = candidate["source_prepass"]["json"]["fields"]
            self.assertIn({"path": "$.gate", "value": "PASS", "line": 1}, fields)
            self.assertIn({"path": "$.coverage.statements", "value": "92", "line": 1}, fields)
            self.assertIn(candidate["candidate_id"], preview)
            self.assertIn("JSON fields parsed", preview)


if __name__ == "__main__":
    unittest.main()
