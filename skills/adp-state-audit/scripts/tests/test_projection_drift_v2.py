import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "audit_state.py"


def load_module():
    spec = importlib.util.spec_from_file_location("adp_state_audit_projection_v2", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load audit_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectionDriftV2Tests(unittest.TestCase):
    def scaffold_empty_projection(self, memory: Path) -> tuple[Path, dict]:
        record = memory / "workstreams/l1-checkout/delivery-record.md"
        record.parent.mkdir(parents=True)
        record.write_text(
            "# Workstream Delivery Record\n\n## Project Status\n\n- Next actions: TBD\n",
            encoding="utf-8",
        )
        prepass = {
            "registered_workstreams": ["l1-checkout"],
            "virtual_scopes": [],
            "ledger_actions": [],
        }
        ledger_state = memory / "actions/action-ledger.state.json"
        ledger_state.parent.mkdir(parents=True, exist_ok=True)
        ledger_state.write_text(json.dumps({"ledger_revision": 1}), encoding="utf-8")
        return record, prepass

    def test_empty_ledger_still_reports_exact_orphan_action_id(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            record = memory / "workstreams/l1-checkout/delivery-record.md"
            record.parent.mkdir(parents=True)
            record.write_text(
                "# Workstream Delivery Record\n\n"
                "## Project Status\n\n"
                "- Next actions: [action_id:ACT-ORPHAN-001] FDE-A: Legacy item (due: Friday)\n",
                encoding="utf-8",
            )
            prepass = {
                "registered_workstreams": ["l1-checkout"],
                "virtual_scopes": [],
                "ledger_actions": [],
            }

            verdict = module.audit_action_projection_drift(memory, prepass)

            action_findings = [row for row in verdict["findings"] if row.get("action_id")]
            self.assertEqual(len(action_findings), 1)
            finding = action_findings[0]
            self.assertEqual(finding["action_id"], "ACT-ORPHAN-001")
            self.assertEqual(finding["action_diff"]["drift_kind"], "orphan-in-wdr")
            self.assertEqual(finding["repairability"], "repairable")
            self.assertTrue(finding["finding_id"].startswith("sha256:"))
            self.assertEqual(verdict["rows"][0]["wdr_action_ids"], ["ACT-ORPHAN-001"])

    def test_missing_empty_sidecar_is_fail_visible(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            _, prepass = self.scaffold_empty_projection(memory)

            verdict = module.audit_action_projection_drift(memory, prepass)

            self.assertEqual(verdict["overall"], "drift")
            self.assertIn("sidecar-missing", {row["kind"] for row in verdict["findings"]})

    def test_duplicate_sidecar_action_id_and_global_finding_prevent_false_green(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            record, prepass = self.scaffold_empty_projection(memory)
            sidecar = record.with_name("action-projection.json")
            duplicate = {
                "action_id": "ACT-DUP-001",
                "owner": "FDE-A",
                "action": "Resolve duplicate",
                "due_trigger": "Friday",
                "status": "open",
                "action_revision": 1,
                "routing_scope_id": "l1-checkout",
                "affected_workstreams": ["l1-checkout"],
                "rendered_summary": "[action_id:ACT-DUP-001] FDE-A: Resolve duplicate (due: Friday)",
            }
            sidecar.write_text(
                json.dumps({"actions": [duplicate, duplicate], "ledger_fingerprint": None}),
                encoding="utf-8",
            )

            verdict = module.audit_action_projection_drift(memory, prepass)

            kinds = {row["kind"] for row in verdict["findings"]}
            self.assertIn("duplicate-action-id-in-sidecar", kinds)
            self.assertEqual(verdict["overall"], "drift")

    def test_duplicate_active_ledger_action_id_is_blocked_with_exact_action_id(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            _, prepass = self.scaffold_empty_projection(memory)
            ledger = memory / "actions/action-ledger.md"
            ledger.write_text(
                "# Action Ledger\n\n"
                "| Action ID | Status | Owner | Workstream | Action Revision |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| ACT-DUP-ACTIVE-001 | open | FDE-A | l1-checkout | 1 |\n"
                "| ACT-DUP-ACTIVE-001 | blocked | FDE-B | l1-checkout | 2 |\n",
                encoding="utf-8",
            )
            prepass["ledger_actions"] = [
                {
                    "action_id": "ACT-DUP-ACTIVE-001",
                    "status": "open",
                    "owner": "FDE-A",
                    "workstream": "l1-checkout",
                    "action": "Publish evidence",
                },
                {
                    "action_id": "ACT-DUP-ACTIVE-001",
                    "status": "blocked",
                    "owner": "FDE-B",
                    "workstream": "l1-checkout",
                    "action": "Confirm evidence",
                },
            ]

            verdict = module.audit_action_projection_drift(memory, prepass)

            finding = next(
                row
                for row in verdict["findings"]
                if row["kind"] == "duplicate-active-ledger-action-id"
            )
            self.assertEqual(finding["severity"], "blocked")
            self.assertEqual(finding["action_id"], "ACT-DUP-ACTIVE-001")
            self.assertEqual(finding["source_path"], "actions/action-ledger.md")
            self.assertEqual(verdict["overall"], "drift")

    def test_wdr_managed_summary_content_must_match_sidecar(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            record = memory / "workstreams/l1-checkout/delivery-record.md"
            record.parent.mkdir(parents=True)
            record.write_text(
                "# Workstream Delivery Record\n\n## Project Status\n\n"
                "- Next actions: [action_id:ACT-CONTENT-001] Wrong owner: Resolve copy (due: Friday)\n",
                encoding="utf-8",
            )
            action = {
                "action_id": "ACT-CONTENT-001",
                "status": "open",
                "owner": "FDE-A",
                "workstream": "l1-checkout",
                "affected_workstreams": ["l1-checkout"],
                "action": "Resolve copy",
                "due_or_trigger": "Friday",
            }
            expected = module.expected_projection_record(action, {"ACT-CONTENT-001": 1})
            sidecar = record.with_name("action-projection.json")
            sidecar.write_text(
                json.dumps({"actions": [expected], "ledger_fingerprint": None}),
                encoding="utf-8",
            )
            prepass = {
                "registered_workstreams": ["l1-checkout"],
                "virtual_scopes": [],
                "ledger_actions": [action],
            }

            verdict = module.audit_action_projection_drift(memory, prepass)

            content_findings = [
                row for row in verdict["findings"]
                if row.get("action_id") == "ACT-CONTENT-001"
                and row.get("action_diff", {}).get("drift_kind") == "content-mismatch"
            ]
            self.assertTrue(content_findings)
            self.assertTrue(any(row["source_path"].endswith("delivery-record.md") for row in content_findings))

    def test_repair_batches_are_deterministic_and_preserve_exact_action_sets(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            (memory / "actions").mkdir(parents=True)
            (memory / "actions/action-ledger.md").write_text("# Action Ledger\n", encoding="utf-8")
            findings = []
            rows = []
            for workstream_id, action_ids in (
                ("l1-checkout", ["ACT-100", "ACT-200"]),
                ("l2-search", ["ACT-300"]),
            ):
                row_findings = []
                for index, action_id in enumerate(action_ids, start=1):
                    finding = module.action_drift_finding(
                        workstream_id,
                        action_id,
                        "missing-from-wdr",
                        ledger_present=True,
                        wdr_present=False,
                        ledger_revision=index,
                        source_path=f"workstreams/{workstream_id}/delivery-record.md",
                    )
                    findings.append(finding)
                    row_findings.append(finding)
                rows.append(
                    {
                        "workstream_id": workstream_id,
                        "finding_ids": sorted(item["finding_id"] for item in row_findings),
                        "expected_action_ids": action_ids,
                        "projected_action_ids": [],
                        "wdr_action_ids": [],
                        "wdr_revision": 7,
                        "file_generation": 9,
                        "wdr_fingerprint": "sha256:" + "a" * 64,
                    }
                )
            verdict = {
                "verdict_id": "sha256:" + "b" * 64,
                "ledger_fingerprint": "sha256:" + "c" * 64,
                "ledger_revision": 5,
                "findings": list(reversed(findings)),
                "rows": list(reversed(rows)),
            }
            audit_id = "sha256:" + "d" * 64

            first = module.build_repair_contract(audit_id, verdict, memory)
            second = module.build_repair_contract(audit_id, verdict, memory)

            self.assertEqual(first, second)
            self.assertEqual(
                [batch["command"]["workstream_id"] for batch in first["repair_batches"]],
                ["l1-checkout", "l2-search"],
            )
            self.assertEqual(first["repair_batches"][0]["command"]["action_ids"], ["ACT-100", "ACT-200"])
            self.assertEqual(
                [row["action_id"] for row in first["repair_batches"][0]["read_set"]["action_revisions"]],
                ["ACT-100", "ACT-200"],
            )
            by_id = {row["finding_id"]: row for row in first["findings"]}
            for batch in first["repair_batches"]:
                for finding_id in batch["finding_ids"]:
                    mapped = by_id[finding_id]
                    self.assertEqual(mapped["repair_batch_id"], batch["batch_id"])
                    self.assertEqual(
                        [ref["id"] for ref in mapped["entity_refs"] if ref["entity_type"] == "action"],
                        mapped["action_ids"],
                    )


if __name__ == "__main__":
    unittest.main()
