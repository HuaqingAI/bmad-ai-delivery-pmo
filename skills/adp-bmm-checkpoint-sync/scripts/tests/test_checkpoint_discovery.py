import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checkpoint_extractors import discover_candidate


SCRIPT = Path(__file__).resolve().parents[1] / "sync_bmm_checkpoint.py"
BOOTSTRAP = Path(__file__).resolve().parents[3] / "adp-project-kickoff" / "scripts" / "bootstrap_adp.py"
REGISTER = Path(__file__).resolve().parents[3] / "adp-workstream-register" / "scripts" / "register_workstream.py"


class CheckpointDiscoveryTests(unittest.TestCase):
    def register_workstream(self, project_root: Path) -> dict:
        subprocess.run(
            [sys.executable, str(BOOTSTRAP), str(project_root), "--project-name", "Candidate Sync Test"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(REGISTER),
                str(project_root),
                "--id",
                "L1 Checkout",
                "--name",
                "Checkout Migration",
                "--owner",
                "FDE-A",
                "--business-owner",
                "Biz-A",
                "--phase",
                "draft",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def run_command(self, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_discover_confirm_sync_candidate_and_applied_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)
            docs = project_root / "docs"
            docs.mkdir()
            (docs / "prd.md").write_text(
                """---
status: baseline
---

# Checkout PRD

## In Scope

- Checkout order flow migration

## Acceptance Criteria

- Business owner confirms checkout parity
""",
                encoding="utf-8",
            )

            discovered = self.run_command(
                "discover",
                str(project_root),
                "--workstream-id",
                "L1 Checkout",
                "--checkpoint",
                "prd",
                "--artifact",
                "prd=docs/prd.md",
                "--summary",
                "Checkout PRD baseline ready for project review",
                "--asserted-by",
                "FDE-A",
            )
            candidate_id = discovered["candidate_id"]
            confirmed = self.run_command(
                "confirm",
                str(project_root),
                "--candidate-id",
                candidate_id,
                "--confirmed-by",
                "FDE-A",
                "--override",
                "authority.confirmation_state=confirmed-local",
                "--override",
                'claims.business_confirmation=["Biz-A owns final confirmation"]',
            )
            synced = self.run_command("sync", str(project_root), "--candidate-id", candidate_id)
            synced_again = self.run_command("sync", str(project_root), "--candidate-id", candidate_id)

            self.assertEqual(discovered["status"], "discovered")
            self.assertEqual(confirmed["status"], "confirmed")
            self.assertEqual(synced["candidate_status"], "applied")
            self.assertTrue(synced_again["no_op"])
            record_text = (Path(setup["workstream_root"]) / "delivery-record.md").read_text(encoding="utf-8")
            self.assertIn("Checkout PRD baseline ready for project review", record_text)
            self.assertIn("| PRD |", record_text)

    def test_cross_line_pending_sync_records_gap_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)
            docs = project_root / "docs"
            docs.mkdir()
            (docs / "prd.md").write_text(
                "# Checkout PRD\n\n## Acceptance Criteria\n\n- Checkout parity is demonstrable\n",
                encoding="utf-8",
            )

            discovered = self.run_command(
                "discover",
                str(project_root),
                "--workstream-id",
                "L1 Checkout",
                "--checkpoint",
                "prd",
                "--artifact",
                "prd=docs/prd.md",
                "--summary",
                "Checkout PRD has L2 payment impact",
                "--asserted-by",
                "FDE-A",
                "--authority-scope",
                "L1 Checkout",
                "--affected-workstream",
                "L2 Payments",
                "--required-confirmer",
                "L2 owner",
            )
            candidate_id = discovered["candidate_id"]
            self.run_command("confirm", str(project_root), "--candidate-id", candidate_id, "--confirmed-by", "FDE-A")
            self.run_command("sync", str(project_root), "--candidate-id", candidate_id)

            root = Path(setup["workstream_root"])
            record_text = (root / "delivery-record.md").read_text(encoding="utf-8")
            readiness_text = (root / "readiness.md").read_text(encoding="utf-8")
            self.assertIn("- Current ADP status: gap", record_text)
            self.assertIn("Confirmation state is cross-line-pending", readiness_text)
            self.assertNotIn("- Current ADP status: ready", record_text)

    def test_extractor_happy_paths_for_architecture_story_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            arch = project_root / "ARCHITECTURE-SPINE.md"
            story = project_root / "story.md"
            trace = project_root / "gate-decision.json"
            arch.write_text("# Spine\n\n## Decisions\n\n- AD-1 Use event intake\n\n## Deferred\n\n- L2 contract TBD\n", encoding="utf-8")
            story.write_text("# Story\n\n## Acceptance Criteria\n\n- Given checkout, then payment confirms\n", encoding="utf-8")
            trace.write_text(json.dumps({"gate": "PASS", "coverage": {"statements": 91}}), encoding="utf-8")

            arch_candidate, _preview, _warnings = discover_candidate(
                project_root, "l1-checkout", "architecture", [str(arch)], summary="Architecture candidate"
            )
            story_candidate, _preview, _warnings = discover_candidate(
                project_root, "l1-checkout", "epic-story", [str(story)], summary="Story candidate"
            )
            trace_candidate, _preview, _warnings = discover_candidate(
                project_root, "l1-checkout", "validation", [str(trace)], summary="Trace candidate"
            )

            arch_sections = {section["heading"]: section for section in arch_candidate["source_prepass"]["sections"]}
            story_sections = {section["heading"]: section for section in story_candidate["source_prepass"]["sections"]}
            trace_fields = trace_candidate["source_prepass"]["json"]["fields"]

            self.assertEqual(arch_candidate["claims"]["decisions"], [])
            self.assertEqual(arch_candidate["claims"]["readiness_gaps"], [])
            self.assertIn({"line": 5, "text": "AD-1 Use event intake"}, arch_sections["Decisions"]["items"])
            self.assertIn({"line": 9, "text": "L2 contract TBD"}, arch_sections["Deferred"]["items"])
            self.assertIn(
                {"line": 5, "text": "Given checkout, then payment confirms"},
                story_sections["Acceptance Criteria"]["items"],
            )
            self.assertIn({"path": "$.gate", "value": "PASS", "line": 1}, trace_fields)
            self.assertIn({"path": "$.coverage.statements", "value": "91", "line": 1}, trace_fields)


if __name__ == "__main__":
    unittest.main()
