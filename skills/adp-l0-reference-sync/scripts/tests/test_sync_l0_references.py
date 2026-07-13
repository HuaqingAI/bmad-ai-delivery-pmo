import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_l0_references.py"


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


if __name__ == "__main__":
    unittest.main()
