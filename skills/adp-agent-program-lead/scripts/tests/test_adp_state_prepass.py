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
            memory_root = self.scaffold(project_root)
            candidate = memory_root / "intake" / "bmm-checkpoints" / "candidates" / "CHK-L1.json"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("{}\n", encoding="utf-8")

            completed = self.run_script(
                project_root,
                "--capability",
                "global-project-readout",
                "--as-of",
                "2026-07-02",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["counts"]["workstreams"], 1)
            self.assertEqual(result["workstreams"][0]["owner"], "FDE-A")
            self.assertEqual(result["actions"][0]["due_or_trigger"], "TBD")
            targets = {item["target"] for item in result["cross_reference_gaps"]}
            self.assertEqual(targets, {"l2-payments", "l3-settlement"})
            self.assertNotIn("workflow_triggers", result)
            self.assertEqual(result["recommended_workflow"], "")
            self.assertIn(
                "intake/bmm-checkpoints/candidates/CHK-L1.json",
                {item["path"] for item in result["sources_read"]},
            )
            self.assertIn("decisions/decision-log.md", result["missing_sources"])
            self.assertTrue(all(isinstance(item.get("blocking"), bool) for item in result["gaps"]))
            self.assertTrue(all(isinstance(item.get("blocking"), bool) for item in result["cross_reference_gaps"]))

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

    def test_program_only_scope_does_not_open_any_wdr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            baseline = {
                "milestones": [
                    {"id": "M-P", "workstream_id": "program"},
                    {"id": "M-L1", "workstream_id": "l1-checkout"},
                ]
            }
            baseline_path = memory_root / "plans/program-baseline.md"
            baseline_path.parent.mkdir(parents=True)
            baseline_path.write_text(
                "# Baseline\n\n<!-- adp:program-baseline:v1 -->\n\n```json\n"
                + json.dumps(baseline)
                + "\n```\n",
                encoding="utf-8",
            )
            (memory_root / "workstreams/l1-checkout/delivery-record.md").write_bytes(b"\xff\xfe")
            legacy = memory_root / "workstreams/program/delivery-record.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"\xff\xfe")

            completed = self.run_script(project_root, "--workstream", "PROGRAM")
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual([], result["workstreams"])
            self.assertEqual([], result["registered_workstreams"])
            self.assertEqual(["program"], [item["scope_id"] for item in result["virtual_scopes"]])
            self.assertFalse(any("delivery-record.md" in item["path"] for item in result["sources_read"]))

    def test_cross_workstream_ids_and_descriptive_facts_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.write_text(
                RECORD.replace(
                    "- l2-payments",
                    "- L2-Payments\n- L3 checkout sequencing remains gated by payment-owner confirmation",
                ).replace(
                    "- l3-settlement",
                    "- missing-line\n- L8B taxonomy readiness follows the catalog freeze",
                ),
                encoding="utf-8",
            )
            registered = memory_root / "workstreams" / "l2-payments" / "delivery-record.md"
            registered.parent.mkdir(parents=True)
            registered.write_text(
                RECORD.replace("l1-checkout", "l2-payments")
                .replace("- l2-payments", "- TBD")
                .replace("- l3-settlement", "- TBD"),
                encoding="utf-8",
            )

            completed = self.run_script(
                project_root,
                "--workstream",
                "l1-checkout",
                "--as-of",
                "2026-07-02",
            )
            result = json.loads(completed.stdout)
            links = result["workstreams"][0]["links"]

            self.assertEqual(links["depends_on"], ["l2-payments"])
            self.assertEqual(links["impacts"], ["missing-line"])
            self.assertEqual(
                links["dependency_facts"],
                ["L3 checkout sequencing remains gated by payment-owner confirmation"],
            )
            self.assertEqual(
                links["impact_facts"],
                ["L8B taxonomy readiness follows the catalog freeze"],
            )
            missing = [
                item for item in result["cross_reference_gaps"] if item["gap_type"] == "missing_reference"
            ]
            migrations = [
                item
                for item in result["cross_reference_gaps"]
                if item["gap_type"] == "noncanonical_cross_link_entry"
            ]
            self.assertEqual([item["target"] for item in missing], ["missing-line"])
            self.assertEqual(len(migrations), 2)
            self.assertTrue(all(not item["blocking"] for item in migrations))
            self.assertEqual(
                [(item["source_path"], item["source_line"]) for item in migrations],
                [
                    ("workstreams/l1-checkout/delivery-record.md", 27),
                    ("workstreams/l1-checkout/delivery-record.md", 32),
                ],
            )
            self.assertEqual(
                [item["source"] for item in migrations],
                [
                    "workstreams/l1-checkout/delivery-record.md:27",
                    "workstreams/l1-checkout/delivery-record.md:32",
                ],
            )
            self.assertNotIn(
                "l2-payments",
                {item["target"] for item in result["cross_reference_gaps"]},
            )

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
            stale_gap = next(item for item in result["gaps"] if item["gap"] == "last status sync is older than 7 days")
            self.assertEqual(stale_gap["category"], "freshness")
            self.assertEqual(stale_gap["gap_type"], "stale")
            self.assertFalse(stale_gap["blocking"])
            self.assertEqual(stale_gap["field"], "last_status_sync")
            self.assertEqual(stale_gap["policy_rule_id"], "wdr-last-status-sync-freshness")
            missing_gap = next(item for item in result["gaps"] if item["gap"] == "requested workstream was not found")
            self.assertEqual(missing_gap["category"], "completeness")
            self.assertEqual(missing_gap["gap_type"], "missing_workstream")
            self.assertTrue(missing_gap["blocking"])
            self.assertEqual(missing_gap["policy_rule_id"], "requested-workstream-exists")
            self.assertEqual(missing_gap["recommended_workflow"], "adp-project-kickoff")
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
                "fde-action-list",
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

    def test_program_action_uses_affected_workstreams_for_cross_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            ledger = memory_root / "actions" / "action-ledger.md"
            ledger.parent.mkdir(parents=True)
            ledger.write_text(
                "\n".join(
                    [
                        "# Action Ledger",
                        "",
                        "| Action ID | Status | Owner | Workstream | Affected Workstreams | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                        "| ACT-20260705-001 | open | PMO-A | program | l1-checkout; l2-payments | Start ADP trial and return rollout feedback | meetings/2026-07-05-sync.md#M-007 | Meeting action | 2099-07-15 | Feedback summary linked | 2026-07-05T09:00:00+08:00 | adp-meeting-sync |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(
                project_root,
                "--capability",
                "fde-action-list",
                "--as-of",
                "2026-07-05",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["ledger_actions"][0]["workstream"], "program")
            self.assertEqual(result["ledger_actions"][0]["affected_workstreams"], "l1-checkout; l2-payments")
            self.assertEqual(result["action_cross_check"][0]["workstream"], "l1-checkout")
            self.assertEqual(result["action_cross_check"][0]["ledger_open_actions"][0]["action_id"], "ACT-20260705-001")

    def test_activation_resolves_config_precedence_and_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "_bmad" / "adp").mkdir(parents=True)
            (project_root / "_bmad" / "config.user.yaml").write_text(
                "communication_language: French\ndocument_output_language: German\n",
                encoding="utf-8",
            )
            (project_root / "_bmad" / "adp" / "config.yaml").write_text(
                "communication_language: Chinese\nadp_memory_root: '{project-root}/state/adp'\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--activation")
            result = json.loads(completed.stdout)

            self.assertEqual(result["mode"], "activation")
            self.assertEqual(result["resolved"]["communication_language"], "Chinese")
            self.assertEqual(result["resolved"]["document_output_language"], "German")
            self.assertEqual(
                Path(result["resolved"]["adp_state_root"]).resolve(),
                (project_root / "state" / "adp").resolve(),
            )
            self.assertTrue(result["config_found"])
            self.assertEqual(result["configuration_errors"], [])
            self.assertFalse(result["state_exists"])

            overridden = json.loads(
                self.run_script(project_root, "--activation", "--memory-root", "explicit-memory").stdout
            )
            self.assertEqual(
                Path(overridden["resolved"]["adp_state_root"]).resolve(),
                (project_root / "explicit-memory").resolve(),
            )
            self.assertEqual(overridden["value_sources"]["adp_state_root"], "cli --memory-root")

    def test_capability_rejects_noncanonical_wording(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self.run_script(
                Path(temp_dir),
                "--capability",
                "short action list",
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("invalid choice", completed.stderr)


if __name__ == "__main__":
    unittest.main()
