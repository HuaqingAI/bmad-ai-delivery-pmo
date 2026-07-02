import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "adp-state-prepass.py"


RECORD = """# Workstream Delivery Record

## Identity

- Workstream ID: l1-checkout
- Name: Checkout
- FDE owner: FDE-A
- Business owner: Biz-A
- Current BMM phase: validation
- Current ADP status: at-risk

## Project Status

- Progress: Validation running
- Blockers: Payment owner confirmation missing
- Risks: Acceptance may slip; severity: high; likelihood: medium
- Dependencies: see cross-workstream links
- Scope or change notes: Payment flow changed
- Next actions: FDE-A confirm payment owner by 2026-07-05
- Last status sync: 2026-07-01T09:00:00+08:00

## Cross-Workstream Links

Depends on:

- l2-payments

Impacts:

- l3-settlement

L0 references:

- gate-payments
"""


class AdpStatePrepassTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def scaffold(self, project_root: Path) -> Path:
        memory_root = project_root / "_bmad" / "memory" / "adp"
        for rel in ["daily", "decisions", "l0", "views", "workstreams/l1-checkout"]:
            (memory_root / rel).mkdir(parents=True, exist_ok=True)
        for rel in ["index.md", "project-charter.md", "cadence.md"]:
            (memory_root / rel).write_text(f"# {rel}\n", encoding="utf-8")
        record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
        record.write_text(RECORD, encoding="utf-8")
        (record.parent / "evidence.md").write_text("# Evidence\n\n- Criterion: payment success proof pending\n", encoding="utf-8")
        (record.parent / "decisions.md").write_text(
            "# Decisions\n\n| Date | Type | Decision / Question | Owner | Status |\n| --- | --- | --- | --- | --- |\n| 2026-07-01 | Business | Confirm payment owner | Biz-A | open |\n",
            encoding="utf-8",
        )
        (record.parent / "readiness.md").write_text("# Readiness\n\n- Acceptance: gap - confirmer missing\n", encoding="utf-8")
        return memory_root

    def test_extracts_workstream_state_actions_and_cross_reference_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(
                project_root,
                "--capability",
                "Global Project Readout",
                "--as-of",
                "2026-07-02",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"]["workstreams"], 1)
            self.assertEqual(result["workstreams"][0]["owner"], "FDE-A")
            self.assertEqual(result["actions"][0]["due_or_trigger"], "2026-07-05")
            targets = {item["target"] for item in result["cross_reference_gaps"]}
            self.assertEqual(targets, {"l2-payments", "l3-settlement"})
            self.assertIn("adp-risk-dependency-change-review", result["workflow_triggers"])

    def test_missing_memory_root_returns_kickoff_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self.run_script(Path(temp_dir), check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["recommended_workflow"], "adp-project-kickoff")

    def test_reports_stale_and_missing_workstream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(
                project_root,
                "--workstream",
                "l1-checkout",
                "--workstream",
                "missing-line",
                "--as-of",
                "2026-07-20",
            )
            result = json.loads(completed.stdout)

            gaps = [item["gap"] for item in result["gaps"]]
            self.assertIn("last status sync is older than 7 days", gaps)
            self.assertIn("requested workstream was not found", gaps)
            self.assertIn("adp-status-sync", result["workflow_triggers"])


if __name__ == "__main__":
    unittest.main()
