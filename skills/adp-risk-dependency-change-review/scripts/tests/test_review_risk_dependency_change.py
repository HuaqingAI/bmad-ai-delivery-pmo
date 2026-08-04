from __future__ import annotations

import hashlib
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
    def test_relation_writer_previews_then_atomically_applies_wdr_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            make_relation_context(project, memory)
            risk_flow = json.loads((memory / "views/risk-flow.json").read_text(encoding="utf-8"))
            risk = next(item for item in risk_flow["risks"] if item["sources"][0]["field"] == "Project Status.risk")
            record = memory / "workstreams/alpha/delivery-record.md"
            before = record.read_bytes()
            updates_path = project / "approved-risk-relations.json"
            write_relation_updates(
                updates_path,
                risk_id=risk["risk_id"],
                source_path="workstreams/alpha/delivery-record.md",
                source_fingerprint=fingerprint(record),
                source_field="Project Status.risk",
            )

            preview = run_script(project, "--relation-updates-file", str(updates_path))

            self.assertEqual(preview["status"], "preview")
            self.assertTrue(preview["dry_run"])
            self.assertEqual(record.read_bytes(), before)
            self.assertIsNone(preview["receipt_path"])
            rejected = run_script_failure(
                project,
                "--relation-updates-file",
                str(updates_path),
                "--apply-relations",
            )
            self.assertIn("verified plan token", rejected["error"])
            self.assertEqual(record.read_bytes(), before)

            applied = run_script(
                project,
                "--relation-updates-file",
                str(updates_path),
                "--apply-relations",
                "--verified-plan-token",
                preview["verified_plan_token"],
            )

            self.assertEqual(applied["status"], "applied")
            self.assertTrue(Path(applied["receipt_path"]).is_file())
            record_text = record.read_text(encoding="utf-8")
            self.assertIn(f"risk_id:{risk['risk_id']}", record_text)
            self.assertIn("baseline_revision:2", record_text)
            self.assertIn("related_plan_item_ids:MS-PAYMENT", record_text)
            updated_flow = json.loads((memory / "views/risk-flow.json").read_text(encoding="utf-8"))
            updated_risk = next(item for item in updated_flow["risks"] if item["risk_id"] == risk["risk_id"])
            self.assertEqual(updated_risk["related_plan_item_ids"], ["MS-PAYMENT"])
            self.assertEqual(updated_risk["related_flow_edge_ids"], [])
            repeated = run_script(project, "--relation-updates-file", str(updates_path))
            self.assertEqual(repeated["status"], "already-applied")

    def test_relation_writer_rebinds_an_older_risk_to_current_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            make_relation_context(project, memory)
            risk_flow_path = memory / "views/risk-flow.json"
            risk_flow = json.loads(risk_flow_path.read_text(encoding="utf-8"))
            risk = next(item for item in risk_flow["risks"] if item["sources"][0]["field"] == "Project Status.risk")
            risk["baseline_revision"] = 1
            risk_flow_path.write_text(json.dumps(risk_flow), encoding="utf-8")
            record = memory / "workstreams/alpha/delivery-record.md"
            updates_path = project / "approved-risk-rebind.json"
            write_relation_updates(
                updates_path,
                risk_id=risk["risk_id"],
                source_path="workstreams/alpha/delivery-record.md",
                source_fingerprint=fingerprint(record),
                source_field="Project Status.risk",
            )

            preview = run_script(project, "--relation-updates-file", str(updates_path))

            self.assertEqual("preview", preview["status"])
            self.assertEqual(2, preview["baseline_revision"])

    def test_relation_writer_updates_decision_row_and_uses_decision_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            make_relation_context(project, memory)
            risk_flow = json.loads((memory / "views/risk-flow.json").read_text(encoding="utf-8"))
            risk = next(item for item in risk_flow["risks"] if item["sources"][0]["field"] == "Decision / Question")
            decision_path = memory / "workstreams/alpha/decisions.md"
            self.assertEqual(risk["sources"][0]["artifact_path"], "workstreams/alpha/decisions.md")
            self.assertEqual(risk["sources"][0]["source_fingerprint"], fingerprint(decision_path))
            updates_path = project / "approved-decision-risk-relations.json"
            write_relation_updates(
                updates_path,
                risk_id=risk["risk_id"],
                source_path="workstreams/alpha/decisions.md",
                source_fingerprint=fingerprint(decision_path),
                source_field="Decision / Question",
            )

            preview = run_script(project, "--relation-updates-file", str(updates_path))
            applied = run_script(
                project,
                "--relation-updates-file",
                str(updates_path),
                "--apply-relations",
                "--verified-plan-token",
                preview["verified_plan_token"],
            )

            self.assertEqual(applied["status"], "applied")
            self.assertIn(f"risk_id:{risk['risk_id']}", decision_path.read_text(encoding="utf-8"))
            updated_flow = json.loads((memory / "views/risk-flow.json").read_text(encoding="utf-8"))
            updated_risk = next(item for item in updated_flow["risks"] if item["risk_id"] == risk["risk_id"])
            self.assertEqual(updated_risk["related_plan_item_ids"], ["MS-PAYMENT"])
            self.assertEqual(updated_risk["sources"][0]["artifact_path"], "workstreams/alpha/decisions.md")
            self.assertEqual(updated_risk["sources"][0]["field"], "Decision / Question")

    def test_relation_writer_updates_legacy_decision_row_after_non_table_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            decision_path = memory / "workstreams/alpha/decisions.md"
            decision_path.write_text(
                decision_path.read_text(encoding="utf-8")
                + "\n## Decision Rules\n\n- Preserve legacy rows.\n"
                + "| 2026-07-02 | scope | Delayed scope row | Dana | alpha | open | TBD |\n",
                encoding="utf-8",
                newline="\n",
            )
            make_relation_context(project, memory)
            semantic = "Delayed scope row"
            digest = hashlib.sha256(f"alpha\ndecision/change\n{semantic.casefold()}".encode("utf-8")).hexdigest()[:16]
            risk_id = f"RISK-{digest}"
            risk_flow = json.loads((memory / "views/risk-flow.json").read_text(encoding="utf-8"))
            self.assertIn(risk_id, {item["risk_id"] for item in risk_flow["risks"]})
            updates_path = project / "approved-legacy-decision-risk-relations.json"
            write_relation_updates(
                updates_path,
                risk_id=risk_id,
                source_path="workstreams/alpha/decisions.md",
                source_fingerprint=fingerprint(decision_path),
                source_field="Decision / Question",
            )

            preview = run_script(project, "--relation-updates-file", str(updates_path))
            applied = run_script(
                project,
                "--relation-updates-file",
                str(updates_path),
                "--apply-relations",
                "--verified-plan-token",
                preview["verified_plan_token"],
            )

            self.assertEqual(applied["status"], "applied")
            self.assertIn(
                f"Delayed scope row; risk_id:{risk_id}; baseline_revision:2; related_plan_item_ids:MS-PAYMENT",
                decision_path.read_text(encoding="utf-8"),
            )

    def test_relation_writer_rejects_unapproved_unknown_and_stale_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            make_relation_context(project, memory)
            risk_flow = json.loads((memory / "views/risk-flow.json").read_text(encoding="utf-8"))
            risk = next(item for item in risk_flow["risks"] if item["sources"][0]["field"] == "Project Status.risk")
            record = memory / "workstreams/alpha/delivery-record.md"
            updates_path = project / "invalid-risk-relations.json"
            payload = relation_update_payload(
                risk_id=risk["risk_id"],
                source_path="workstreams/alpha/delivery-record.md",
                source_fingerprint=fingerprint(record),
                source_field="Project Status.risk",
            )
            payload["approval_status"] = "pending"
            updates_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIn("approval_status must be approved", run_script_failure(project, "--relation-updates-file", str(updates_path))["error"])

            payload["approval_status"] = "approved"
            payload["updates"][0]["related_plan_item_ids"] = ["UNKNOWN"]
            updates_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIn("unknown plan items", run_script_failure(project, "--relation-updates-file", str(updates_path))["error"])

            payload["updates"][0]["related_plan_item_ids"] = ["MS-PAYMENT"]
            payload["updates"][0]["source_fingerprint"] = "sha256:" + "f" * 64
            updates_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIn("source fingerprint", run_script_failure(project, "--relation-updates-file", str(updates_path))["error"])

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
            risk_flow = json.loads(
                (memory / "views" / "risk-flow.json").read_text(encoding="utf-8")
            )
            dependency_text = (memory / "views" / "dependency-map.md").read_text(encoding="utf-8")
            self.assertIn("Payment API contract not baseline", risk_text)
            self.assertIn("critical", risk_text)
            for risk in risk_flow["risks"]:
                self.assertIn(f"| {risk['risk_id']} |", risk_text)
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

    def test_descriptive_cross_link_entries_remain_facts_not_workstream_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            record = memory / "workstreams/alpha/delivery-record.md"
            record.write_text(
                record.read_text(encoding="utf-8")
                .replace("- beta", "- beta\n- L3 cutover remains gated by payment confirmation")
                .replace("- gamma", "- gamma\n- L8B taxonomy readiness follows catalog freeze"),
                encoding="utf-8",
            )

            run_script(project)
            dependency_text = (memory / "views/dependency-map.md").read_text(encoding="utf-8")

            self.assertIn("| alpha | depends on | beta |", dependency_text)
            self.assertIn("| alpha | dependency fact | L3 cutover remains gated by payment confirmation |", dependency_text)
            self.assertIn("| alpha | impact fact | L8B taxonomy readiness follows catalog freeze |", dependency_text)
            self.assertNotIn("| alpha | depends on | L3 cutover", dependency_text)
            self.assertNotIn("| alpha | impacts | L8B taxonomy", dependency_text)

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

    def test_updates_existing_packet_without_creating_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            created = run_script(
                project,
                "--packet-title",
                "Refund Scope Approval",
                "--packet-question",
                "Should refunds be included in alpha scope?",
                "--packet-option",
                "Include refunds now",
                "--packet-recommendation",
                "Defer refunds",
                "--packet-owner",
                "Business lead",
            )
            packet = Path(created["business_decision_packet_path"])
            relative_packet = packet.resolve().relative_to(memory.resolve())

            updated = run_script(
                project,
                "--update-packet",
                str(relative_packet),
                "--packet-deadline",
                "Before the 2026-08-15 business review",
                "--packet-owner",
                "Portfolio lead",
            )

            self.assertEqual(Path(updated["business_decision_packet_path"]), packet)
            self.assertEqual(updated["business_decision_packet_operation"], "updated")
            self.assertEqual(len(list(packet.parent.glob("*.md"))), 1)
            text = packet.read_text(encoding="utf-8")
            self.assertIn("Should refunds be included in alpha scope?", text)
            self.assertIn("Include refunds now", text)
            self.assertIn("Defer refunds", text)
            self.assertIn("Before the 2026-08-15 business review", text)
            self.assertIn("Portfolio lead", text)
            self.assertNotIn("Business lead", text)

    def test_update_packet_rejects_missing_or_outside_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            memory = make_memory(project)
            make_workstream(memory, "alpha")
            outside = project / "outside.md"
            outside.write_text("# Outside\n", encoding="utf-8")

            outside_result = run_script_failure(project, "--update-packet", str(outside))
            missing_result = run_script_failure(
                project,
                "--update-packet",
                "decisions/business-decision-packets/missing.md",
            )

            self.assertIn("must stay inside", outside_result["error"])
            self.assertIn("existing Markdown packet", missing_result["error"])

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


def run_script_failure(project: Path, *extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(project), *extra],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {proc.stdout}")
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


def fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def make_relation_context(project: Path, memory: Path) -> None:
    baseline = memory / "plans/program-baseline.md"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        "# Baseline\n\n<!-- adp:program-baseline:v1 -->\n\n```json\n"
        + json.dumps({"revision": 2})
        + "\n```\n",
        encoding="utf-8",
    )
    flow_graph = {
        "flow_graph_id": "sha256:" + "a" * 64,
        "topology": {
            "baseline_revision": 2,
            "nodes": [{"node_id": "MS-PAYMENT"}, {"node_id": "G-PAYMENT"}],
            "edges": [{"edge_id": "E-PAYMENT"}],
        },
    }
    (memory / "views/flow-graph.json").write_text(json.dumps(flow_graph), encoding="utf-8")
    run_script(project)


def relation_update_payload(
    *,
    risk_id: str,
    source_path: str,
    source_fingerprint: str,
    source_field: str,
) -> dict:
    return {
        "risk_relation_update_schema_version": "1.0.0",
        "_control": {"execute_allowed": True},
        "proposal_only": False,
        "approval_status": "approved",
        "approved_by": "Program owner",
        "approved_at": "2026-07-20T08:00:00Z",
        "flow_graph_id": "sha256:" + "a" * 64,
        "baseline_revision": 2,
        "updates": [
            {
                "risk_id": risk_id,
                "workstream_id": "alpha",
                "source_artifact_path": source_path,
                "source_fingerprint": source_fingerprint,
                "source_field": source_field,
                "related_plan_item_ids": ["MS-PAYMENT"],
                "related_flow_edge_ids": [],
            }
        ],
    }


def write_relation_updates(path: Path, **kwargs) -> None:
    path.write_text(json.dumps(relation_update_payload(**kwargs)), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
