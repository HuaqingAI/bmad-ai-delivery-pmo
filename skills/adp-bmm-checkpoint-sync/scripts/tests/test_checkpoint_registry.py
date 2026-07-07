import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checkpoint_registry import CandidateRegistry, compute_candidate_id


def candidate(source_revision: str, claims: dict | None = None) -> dict:
    claims = claims or {"summary": "PRD checkpoint", "open_questions": []}
    candidate_id = compute_candidate_id("L1 Checkout", "prd", "prd:/tmp/prd.md", source_revision, claims)
    return {
        "candidate_id": candidate_id,
        "status": "discovered",
        "workstream_id": "l1-checkout",
        "checkpoint": "prd",
        "artifact": {
            "kind": "prd",
            "path": "/tmp/prd.md",
            "status": "draft",
            "source_scope_key": "prd:/tmp/prd.md",
            "source_revision": source_revision,
        },
        "claims": claims,
        "authority": {
            "asserted_by": "FDE-A",
            "authority_scope": ["l1-checkout"],
            "affected_workstreams": ["l1-checkout"],
            "required_confirmers": [],
            "confirmation_state": "discovered",
        },
        "source_refs": ["/tmp/prd.md"],
    }


class CheckpointRegistryTests(unittest.TestCase):
    def test_duplicate_discover_returns_existing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = CandidateRegistry(Path(temp_dir))
            first = registry.discover(candidate("sha256:one"), "# preview")
            second = registry.discover(candidate("sha256:one"), "# preview changed")

            self.assertFalse(first["no_op"])
            self.assertTrue(second["no_op"])
            self.assertEqual(first["candidate_id"], second["candidate_id"])
            preview = Path(first["preview_path"]).read_text(encoding="utf-8")
            self.assertNotIn("preview changed", preview)

    def test_changed_source_revision_supersedes_previous_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = CandidateRegistry(Path(temp_dir))
            first = registry.discover(candidate("sha256:one"), "# first")
            second = registry.discover(candidate("sha256:two"), "# second")

            self.assertIn(first["candidate_id"], second["superseded"])
            old = registry.load(first["candidate_id"])
            self.assertEqual(old["status"], "superseded")
            self.assertEqual(old["superseded_by"], second["candidate_id"])


if __name__ == "__main__":
    unittest.main()
