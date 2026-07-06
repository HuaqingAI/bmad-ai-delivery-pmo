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
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
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
            self.assertEqual(result["actions"][0]["due_or_trigger"], "TBD")
            targets = {item["target"] for item in result["cross_reference_gaps"]}
            self.assertEqual(targets, {"l2-payments", "l3-settlement"})
            self.assertNotIn("workflow_triggers", result)
            self.assertEqual(result["recommended_workflow"], "")

    def test_parses_only_labeled_wdr_due_or_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.write_text(
                RECORD.replace(
                    "- Next actions: FDE-A confirm payment owner by 2026-07-05",
                    "- Next actions: FDE-A confirm payment owner; Due: 2026-07-05",
                ),
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--as-of", "2026-07-02")
            result = json.loads(completed.stdout)

            self.assertEqual(result["actions"][0]["due_or_trigger"], "2026-07-05")

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
            self.assertNotIn("workflow_triggers", result)

    def test_fde_action_list_reads_action_ledger_before_wdr_next_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.write_text(RECORD.replace("- Next actions: FDE-A confirm payment owner by 2026-07-05", "- Next actions: TBD"), encoding="utf-8")
            ledger = memory_root / "actions" / "action-ledger.md"
            ledger.parent.mkdir(parents=True)
            ledger.write_text(
                "\n".join(
                    [
                        "# Action Ledger",
                        "",
                        "| Action ID | Status | Owner | Workstream | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                        "| ACT-20260702-001 | open | FDE-A | l1-checkout | Add checkout validation evidence | meetings/2026-07-02-sync.md#M-001 | Meeting action | 2026-07-05 | Evidence linked | 2026-07-02T09:00:00+08:00 | adp-status-sync |",
                        "| ACT-20260702-002 | done | FDE-B | l1-checkout | Closed historical task | meetings/2026-07-02-sync.md#M-002 | Meeting action | 2026-07-03 | Closed | 2026-07-02T09:00:00+08:00 | adp-status-sync |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(
                project_root,
                "--capability",
                "FDE Action List",
                "--as-of",
                "2026-07-02",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"]["actions"], 1)
            self.assertEqual(result["ledger_actions"][0]["action_id"], "ACT-20260702-001")
            self.assertEqual(result["actions"][0]["action"], "Add checkout validation evidence")
            gaps = [item["gap"] for item in result["cross_reference_gaps"]]
            self.assertNotIn("ledger open action is missing from WDR Next actions", gaps)
            self.assertEqual(result["action_cross_check"][0]["ledger_action_ids_without_wdr_reference"], ["ACT-20260702-001"])
            self.assertNotIn("Closed historical task", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
