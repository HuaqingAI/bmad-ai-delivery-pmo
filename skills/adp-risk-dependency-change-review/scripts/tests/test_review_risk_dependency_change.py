from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "review_risk_dependency_change.py"
FLOW_TEST_ROOT = Path(__file__).resolve().parents[3] / "adp-flow-graph/scripts/tests"
sys.path.insert(0, str(FLOW_TEST_ROOT))
from flow_contract_testkit import load_json as load_contract_json, validate_schema  # noqa: E402
RISK_SCHEMA = Path(__file__).resolve().parents[2] / "assets/risk-flow-relation-v1.schema.json"


class ReviewRiskDependencyChangeTests(unittest.TestCase):
    def test_risk_flow_contract_has_stable_identity_lifecycle_and_relations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            record = memory / "workstreams/alpha/delivery-record.md"
            record.write_text(
                record.read_text(encoding="utf-8")
                .replace(
                    "Payment API contract not baseline; severity: critical; likelihood: likely",
                    "Payment API contract not baseline; severity: critical; likelihood: likely; risk_id: R-PAYMENT; lifecycle: mitigating; relation_state: blocked; related_plan_item_ids: MS-PAYMENT+G-PAYMENT; related_flow_edge_ids: E-PAYMENT",
                )
                .replace("- Next actions: Confirm payment contract and refund scope", "- Next actions: Confirm payment contract and refund scope\n- Last status sync: 2026-07-13T08:00:00Z"),
                encoding="utf-8",
            )
            baseline = memory / "plans/program-baseline.md"
            baseline.parent.mkdir(parents=True)
            baseline.write_text(
                "# Baseline\n\n<!-- adp:program-baseline:v1 -->\n\n```json\n"
                + json.dumps({"revision": 2})
                + "\n```\n",
                encoding="utf-8",
            )

            first = run_script(project)
            first_contract = json.loads(Path(first["risk_flow_path"]).read_text(encoding="utf-8"))
            second = run_script(project)
            second_contract = json.loads(Path(second["risk_flow_path"]).read_text(encoding="utf-8"))
            risk = next(item for item in first_contract["risks"] if item["risk_id"] == "R-PAYMENT")

            self.assertEqual(first_contract, second_contract)
            self.assertEqual(risk["lifecycle"], "mitigating")
            self.assertEqual(risk["relation_state"], "blocked")
            self.assertEqual(risk["baseline_revision"], 2)
            self.assertEqual(risk["related_plan_item_ids"], ["G-PAYMENT", "MS-PAYMENT"])
            self.assertEqual(risk["related_flow_edge_ids"], ["E-PAYMENT"])
            self.assertEqual(risk["observed_at"], "2026-07-13T08:00:00Z")
            self.assertEqual(validate_schema(first_contract, load_contract_json(RISK_SCHEMA)), [])

    def test_writes_risk_matrix_and_dependency_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")

            result = run_script(project)

            self.assertTrue(result["ok"])
            self.assertEqual(result["workstreams_scanned"], ["alpha"])
            self.assertGreaterEqual(result["counts"]["risk_entries"], 3)
            self.assertGreaterEqual(result["counts"]["dependency_entries"], 3)
            risk_text = (memory / "views" / "risk-matrix.md").read_text(encoding="utf-8")
            dependency_text = (memory / "views" / "dependency-map.md").read_text(encoding="utf-8")
            self.assertIn("Payment API contract not baseline", risk_text)
            self.assertIn("critical", risk_text)
            self.assertNotIn("| high | current |", risk_text)
            self.assertTrue(any("blocker severity is missing" in gap for gap in result["review_gaps"]))
            self.assertTrue(any("risk escalation path is missing" in gap for gap in result["review_gaps"]))
            self.assertIn("dependency note", dependency_text)
            self.assertIn("depends on", dependency_text)
            self.assertIn("l0 reference", dependency_text)

    def test_dry_run_does_not_write_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")

            result = run_script(project, "--dry-run")

            self.assertTrue(result["ok"])
            self.assertFalse((memory / "views" / "risk-matrix.md").exists())
            self.assertFalse((memory / "views" / "dependency-map.md").exists())
            self.assertFalse((memory / "views" / "risk-flow.json").exists())

    def test_creates_business_decision_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")

            result = run_script(
                project,
                "--packet-title",
                "Refund Scope Approval",
                "--packet-question",
                "Should refunds be included in alpha scope?",
                "--packet-background",
                "Alpha scope changed after PRD baseline.",
                "--packet-option",
                "Include refunds now",
                "--packet-option",
                "Defer refunds",
                "--packet-impact",
                "Affects beta dependency",
                "--packet-recommendation",
                "Defer refunds unless business accepts date impact.",
                "--packet-deadline",
                "Before next business review",
                "--packet-owner",
                "Business lead",
                "--packet-workstream",
                "alpha",
            )

            packet_path = Path(result["business_decision_packet_path"])
            self.assertTrue(packet_path.exists())
            packet_text = packet_path.read_text(encoding="utf-8")
            self.assertIn("Should refunds be included", packet_text)
            self.assertIn("Business lead", packet_text)
            self.assertTrue(packet_path.resolve().is_relative_to(memory.resolve()))

    def test_packet_filename_is_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")

            first = run_script(project, "--packet-title", "Refund Scope Approval")
            second = run_script(project, "--packet-title", "Refund Scope Approval")

            self.assertNotEqual(first["business_decision_packet_path"], second["business_decision_packet_path"])
            self.assertTrue(Path(first["business_decision_packet_path"]).exists())
            self.assertTrue(Path(second["business_decision_packet_path"]).exists())

    def test_empty_memory_reports_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)

            result = run_script(project)

            self.assertTrue(result["ok"])
            self.assertEqual(result["workstreams_scanned"], [])
            self.assertIn("no workstream records found", result["review_gaps"][0])
            risk_text = (memory / "views" / "risk-matrix.md").read_text(encoding="utf-8")
            self.assertIn("no workstream records found", risk_text)

    def test_language_golden_localizes_views_and_preserves_source_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")

            chinese = run_script(project, "--language", "Chinese")
            risk_text = (memory / "views" / "risk-matrix.md").read_text(encoding="utf-8")
            self.assertEqual(chinese["language"]["locale"], "zh")
            self.assertIn("# ADP 风险矩阵", risk_text)
            self.assertIn("Payment API contract not baseline", risk_text)
            self.assertIn("critical", risk_text)

            english = run_script(project, "--language", "English")
            english_text = (memory / "views" / "risk-matrix.md").read_text(encoding="utf-8")
            self.assertEqual(english["language"]["locale"], "en")
            self.assertIn("# ADP Risk Matrix", english_text)
            self.assertIn("Payment API contract not baseline", english_text)


def run_script(project: Path, *extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(project), *extra],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(proc.stdout)


def make_memory(project: Path) -> Path:
    memory = project / "_bmad-output" / "adp" / "memory"
    (memory / "workstreams").mkdir(parents=True)
    (memory / "views").mkdir()
    (memory / "decisions" / "business-decision-packets").mkdir(parents=True)
    return memory


def make_workstream(memory: Path, workstream_id: str) -> None:
    root = memory / "workstreams" / workstream_id
    root.mkdir(parents=True)
    (root / "delivery-record.md").write_text(
        f"""# Workstream Delivery Record

## Identity

- Workstream ID: {workstream_id}
- Name: Alpha Checkout
- FDE owner: Dana
- Business owner: Morgan
- Current BMM phase: architecture
- Current ADP status: gap

## Project Status

- Progress: Architecture drafted
- Blockers: Business owner has not approved cutoff
- Risks: Payment API contract not baseline; severity: critical; likelihood: likely
- Dependencies: Payment API contract depends on beta baseline
- Scope or change notes: Scope expanded to include refunds
- Next actions: Confirm payment contract and refund scope

## Cross-Workstream Links

Depends on:

- beta

Impacts:

- gamma

L0 references:

- G19-A gate
""",
        encoding="utf-8",
        newline="\n",
    )
    (root / "decisions.md").write_text(
        """# Workstream Decisions

| Date | Type | Decision / Question | Owner | Impact | Status | Link |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 | scope change | Refunds entered scope | Dana | alpha, beta | open | TBD |
""",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    unittest.main()
