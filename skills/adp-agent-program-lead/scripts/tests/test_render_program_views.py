import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_program_views.py"


RECORD = """# Workstream Delivery Record

## Identity

- Workstream ID: l1-checkout
- Name: Checkout
- FDE owner: FDE-A
- Business owner: Biz-A
- Current BMM phase: validation
- Current ADP status: ready

## Project Status

- Progress: Validation running
- Blockers: TBD
- Risks: Acceptance may slip; severity: high; likelihood: medium
- Dependencies: see cross-workstream links
- Scope or change notes: Payment flow changed
- Next actions: TBD
- Last status sync: 2026-07-01T09:00:00+08:00

## Cross-Workstream Links

Depends on:

- l2-payments

Impacts:

- l3-settlement

L0 references:

- gate-payments
"""


class RenderProgramViewsTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def scaffold(self, project_root: Path) -> Path:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        for rel in [
            "actions",
            "daily",
            "decisions/business-decision-packets",
            "intake/status-sync",
            "l0",
            "meetings",
            "views",
            "workstreams/l1-checkout",
        ]:
            (memory_root / rel).mkdir(parents=True, exist_ok=True)
        for rel in ["index.md", "project-charter.md", "cadence.md"]:
            (memory_root / rel).write_text(f"# {rel}\n", encoding="utf-8")
        for rel in [
            "reference-index.md",
            "extracted-freeze-model.md",
            "extracted-contract-inventory.md",
            "extracted-gates.md",
            "extracted-nfr.md",
            "extracted-evidence-rules.md",
            "extracted-impacts.md",
            "extracted-decision-gates.md",
            "exceptions-and-open-questions.md",
        ]:
            (memory_root / "l0" / rel).write_text(f"# {rel}\n\n- gate-payments\n", encoding="utf-8")
        for rel in [
            "acceptance-readiness.md",
            "cutover-readiness.md",
            "dependency-map.md",
            "fde-actions.md",
            "risk-matrix.md",
            "roadmap.md",
        ]:
            (memory_root / "views" / rel).write_text(f"# {rel}\n\nGenerated view.\n", encoding="utf-8")
        (memory_root / "views" / "project-lead.md").write_text(
            "# Project Lead View\n\n- Overall status: TBD\n",
            encoding="utf-8",
        )
        (memory_root / "views" / "weekly-report.md").write_text(
            "# Weekly Report\n\n## Summary\n\nTBD\n",
            encoding="utf-8",
        )
        workstream_root = memory_root / "workstreams" / "l1-checkout"
        (workstream_root / "delivery-record.md").write_text(RECORD, encoding="utf-8")
        (workstream_root / "evidence.md").write_text("# Evidence\n\n- TBD\n", encoding="utf-8")
        (workstream_root / "readiness.md").write_text("# Readiness\n\n- TBD\n", encoding="utf-8")
        (workstream_root / "decisions.md").write_text(
            "# Decisions\n\n| Date | Type | Decision / Question | Owner | Status |\n| --- | --- | --- | --- | --- |\n| 2026-07-01 | Business | Confirm payment owner | Biz-A | open |\n",
            encoding="utf-8",
        )
        (memory_root / "actions" / "action-ledger.md").write_text(
            "\n".join(
                [
                    "# Action Ledger",
                    "",
                    "| Action ID | Status | Owner | Workstream | Affected Workstreams | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| ACT-20260702-001 | open | FDE-A | l1-checkout | l1-checkout | Add checkout validation evidence | meetings/2026-07-02-sync.md#M-001 | Meeting action | 2026-07-05 | Evidence linked | 2026-07-02T09:00:00+08:00 | adp-status-sync |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "decisions" / "decision-log.md").write_text("# Decision Log\n", encoding="utf-8")
        (memory_root / "decisions" / "business-decision-packets" / "2026-07-01-payment-owner.md").write_text(
            "\n".join(
                [
                    "# Business Decision Packet: Payment Owner",
                    "",
                    "Created: 2026-07-01",
                    "Source meeting: meetings/2026-07-01-sync.md",
                    "Affected workstreams: l1-checkout",
                    "Status: open",
                    "Confirming owner: Biz-A",
                    "Deadline / trigger: 2026-07-03",
                    "",
                    "## Background",
                    "",
                    "Payment owner is not confirmed.",
                    "",
                    "## Decision Needed",
                    "",
                    "Who signs off payment acceptance?",
                    "",
                    "## Options",
                    "",
                    "- Biz-A signs off",
                    "",
                    "## Recommendation",
                    "",
                    "Confirm Biz-A as signer.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return memory_root

    def test_generates_project_lead_and_weekly_views_after_audit_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            project_lead = Path(result["outputs"]["project_lead"]).read_text(encoding="utf-8")
            weekly = Path(result["outputs"]["weekly_report"]).read_text(encoding="utf-8")

            self.assertIn("## Global Status", project_lead)
            self.assertIn("## Workstream Health", project_lead)
            self.assertIn("## Status Summary", weekly)
            self.assertIn("## Blocked Workstreams", weekly)
            self.assertIn("## Risk And Dependency Changes", weekly)
            self.assertIn("## Decisions Needed", weekly)
            self.assertIn("## Readiness Gaps", weekly)
            self.assertIn("## Next Actions", weekly)
            self.assertIn("RED - audit gate has", weekly)
            self.assertIn("do not report the program as globally normal", weekly)
            self.assertIn("Add checkout validation evidence", weekly)
            self.assertNotIn("| TBD | TBD |", weekly)

    def test_weekly_report_generation_reads_action_ledger_when_run_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(project_root, "--view", "weekly-report", "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            weekly = Path(result["outputs"]["weekly_report"]).read_text(encoding="utf-8")

            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"]["actions"], 1)
            self.assertIn("Add checkout validation evidence", weekly)

    def test_missing_memory_root_blocks_without_writing_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self.run_script(Path(temp_dir), check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["recommended_workflow"], "adp-project-kickoff")


if __name__ == "__main__":
    unittest.main()
