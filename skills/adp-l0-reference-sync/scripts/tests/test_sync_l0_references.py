import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_l0_references.py"
REPAIR_SCRIPT = Path(__file__).resolve().parents[1] / "repair_wdr_l0_reference.py"
STATUS_SCRIPT = SCRIPT.parents[2] / "adp-status-sync/scripts/sync_status.py"


class SyncL0ReferencesTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def write_plan(self, project_root: Path, plan: dict) -> Path:
        path = project_root / "l0-plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def test_writes_l0_summary_files_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan_path = self.write_plan(
                project_root,
                {
                    "generated_at": "2026-07-01T10:00:00+00:00",
                    "source_artifacts": [{"artifact": "L0 PRD", "path": "docs/l0-prd.md", "baseline_status": "draft"}],
                    "contracts": [{"contract": "Checkout API", "owner": "L0", "consumers": ["checkout"]}],
                    "gates": [{"gate": "G19-A", "required_evidence": "contract evidence"}],
                    "nfrs": [{"nfr": "Latency", "threshold": "p95 < 500ms"}],
                    "evidence_rules": [{"evidence_type": "Contract test", "required_for": "checkout"}],
                },
            )

            result = self.run_script(project_root, "--plan", str(plan_path))

            self.assertTrue(result["ok"])
            l0_root = Path(result["l0_root"])
            self.assertTrue((l0_root / "reference-index.md").exists())
            self.assertTrue((l0_root / "extracted-contract-inventory.md").exists())
            self.assertIn("Checkout API", (l0_root / "extracted-contract-inventory.md").read_text(encoding="utf-8"))
            self.assertIn("G19-A", (l0_root / "extracted-gates.md").read_text(encoding="utf-8"))

    def test_registers_source_artifact_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)

            result = self.run_script(project_root, "--source-artifact", "L0 PRD=docs/l0.md")

            self.assertTrue(result["ok"])
            index = Path(result["l0_root"]) / "reference-index.md"
            self.assertIn("docs/l0.md", index.read_text(encoding="utf-8"))
            self.assertFalse((Path(result["l0_root"]) / "extracted-gates.md").exists())

    def test_plan_only_updates_provided_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            l0_root = project_root / "_bmad-output" / "adp" / "memory" / "l0"
            l0_root.mkdir(parents=True)
            gates = l0_root / "extracted-gates.md"
            gates.write_text("custom gates\n", encoding="utf-8")
            plan_path = self.write_plan(project_root, {"contracts": [{"contract": "Checkout API"}]})

            result = self.run_script(project_root, "--plan", str(plan_path))

            self.assertTrue(result["ok"])
            self.assertEqual(gates.read_text(encoding="utf-8"), "custom gates\n")
            self.assertTrue((l0_root / "extracted-contract-inventory.md").exists())

    def test_scans_workstream_l0_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            workstream = project_root / "_bmad-output" / "adp" / "memory" / "workstreams" / "checkout"
            workstream.mkdir(parents=True)
            (workstream / "delivery-record.md").write_text(
                "# Workstream Delivery Record\n\nL0 references:\n\n- TBD\n",
                encoding="utf-8",
            )
            plan_path = self.write_plan(
                project_root,
                {
                    "contracts": [{"contract": "Checkout API", "consumers": ["checkout"]}],
                    "gates": [{"gate": "G19-A", "affected_workstreams": ["checkout"]}],
                },
            )

            result = self.run_script(project_root, "--plan", str(plan_path))

            constraints = {gap["constraint"] for gap in result["workstream_gap_suggestions"]}
            self.assertIn("L0 references", constraints)
            self.assertIn("Checkout API", constraints)
            self.assertIn("G19-A", constraints)

    def test_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan_path = self.write_plan(project_root, {"source_artifacts": [{"artifact": "L0 PRD", "path": "docs/l0.md"}]})

            result = self.run_script(project_root, "--plan", str(plan_path), "--dry-run")

            self.assertTrue(result["ok"])
            self.assertFalse((Path(result["l0_root"]) / "reference-index.md").exists())
            self.assertTrue(result["files_created"])

    def test_empty_plan_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan_path = self.write_plan(project_root, {})

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(project_root), "--plan", str(plan_path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            self.assertFalse(json.loads(completed.stdout)["ok"])

    def test_malformed_plan_row_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan_path = self.write_plan(project_root, {"contracts": ["Checkout API"]})

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(project_root), "--plan", str(plan_path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(payload["ok"])
            self.assertIn("contracts[1] must be an object", payload["error"])

    def test_missing_primary_field_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan_path = self.write_plan(project_root, {"contracts": [{"owner": "L0"}]})

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(project_root), "--plan", str(plan_path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(payload["ok"])
            self.assertIn("contracts[1] missing required field: contract", payload["error"])

    def test_language_golden_localizes_system_copy_and_preserves_l0_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            plan_path = self.write_plan(
                project_root,
                {"contracts": [{"contract": "Checkout API", "owner": "L0", "stability": "candidate"}]},
            )

            chinese = self.run_script(project_root, "--plan", str(plan_path), "--language", "Chinese")
            contract_path = Path(chinese["l0_root"]) / "extracted-contract-inventory.md"
            chinese_text = contract_path.read_text(encoding="utf-8")
            self.assertEqual(chinese["language"]["locale"], "zh")
            self.assertIn("# 提取的契约清单", chinese_text)
            self.assertIn("Checkout API", chinese_text)
            self.assertIn("candidate", chinese_text)

            english = self.run_script(project_root, "--plan", str(plan_path), "--language", "English")
            english_text = contract_path.read_text(encoding="utf-8")
            self.assertEqual(english["language"]["locale"], "en")
            self.assertIn("# Extracted Contract Inventory", english_text)
            self.assertIn("Checkout API", english_text)


    def test_repairs_existing_wdr_l0_references_with_token_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir); memory=root/"_bmad-output/adp/memory"; record=memory/"workstreams/l8b/delivery-record.md"; record.parent.mkdir(parents=True)
            record.write_text("""# WDR

## Identity

- Workstream ID: l8b
- Current BMM phase: PRD
- Current ADP status: draft

## Project Status

- Progress: TBD
- Blockers: TBD
- Risks: TBD
- Dependencies: see cross-workstream links
- Scope or change notes: TBD
- Next actions: fill missing state

## Cross-Workstream Links

Depends on:

Impacts:

L0 references:

- TBD

## Record Rule

Keep details.
""",encoding="utf-8")
            update=root/"seed-action.json"; update.write_text(json.dumps({"updates":[{"id":"l8b","refresh_actions":True,"actions":[{"operation":"create","command_id":"CMD-L0","action_id":"ACT-L0","owner":"FDE-A","action":"Seed lineage","source":"meeting#1","due":"Friday","closure_criteria":"Done","evidence":[{"source":"meeting#1"}]}]}]}),encoding="utf-8")
            subprocess.run([sys.executable,str(STATUS_SCRIPT),"update",str(root),"--updates-file",str(update)],check=True,capture_output=True,text=True)
            command=[sys.executable,str(REPAIR_SCRIPT),str(root),"--id","l8b","--l0-reference","GATE-IAM-01"]
            preview=json.loads(subprocess.run([*command,"--dry-run"],check=True,capture_output=True,text=True).stdout)
            applied=json.loads(subprocess.run([*command,"--token",preview["token"]],check=True,capture_output=True,text=True).stdout)
            text=record.read_text(encoding="utf-8")
            self.assertIn("- GATE-IAM-01",text); self.assertNotIn("- TBD",text.split("L0 references:",1)[1].split("##",1)[0])
            self.assertTrue(Path(applied["receipt_path"]).is_file())
            projection=json.loads(record.with_name("action-projection.json").read_text(encoding="utf-8")); state=json.loads(record.with_name("delivery-record.state.json").read_text(encoding="utf-8")); self.assertEqual(projection["wdr_revision"],state["wdr_revision"])

if __name__ == "__main__":
    unittest.main()
