import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "register_workstream.py"
ALIAS_SCRIPT = Path(__file__).resolve().parents[1] / "manage_workstream_alias.py"
SCOPE_SCRIPT = SCRIPT.parents[2] / "adp-plan-baseline/scripts/scope_contract.py"


class RegisterWorkstreamTests(unittest.TestCase):
    def seed_memory(self, project_root: Path) -> None:
        memory = project_root / "_bmad-output" / "adp" / "memory"
        (memory / "schemas").mkdir(parents=True)
        for rel in [
            "index.md",
            "project-charter.md",
            "cadence.md",
            "schemas/workstream-delivery-record.md",
        ]:
            path = memory / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("seed\n", encoding="utf-8")

    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_creates_workstream_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed_memory(Path(temp_dir))
            result = self.run_script(
                Path(temp_dir),
                "--id",
                "L1 Checkout",
                "--name",
                "Checkout Migration",
                "--owner",
                "FDE-A",
                "--business-owner",
                "Biz-A",
                "--phase",
                "PRD",
                "--scope",
                "Checkout flow migration",
                "--artifact",
                "prd=docs/prd.md",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["workstream_id"], "l1-checkout")
            root = Path(result["workstream_root"])
            self.assertTrue((root / "delivery-record.md").exists())
            self.assertTrue((root / "evidence.md").exists())
            self.assertTrue((root / "decisions.md").exists())
            self.assertTrue((root / "readiness.md").exists())
            self.assertIn("PRD", result["artifacts"])

    def test_second_run_preserves_existing_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.seed_memory(project_root)
            first = self.run_script(project_root, "--id", "L2", "--name", "Line 2", "--owner", "FDE-B")
            record = Path(first["workstream_root"]) / "delivery-record.md"
            record.write_text("custom record\n", encoding="utf-8")

            second = self.run_script(
                project_root,
                "--id",
                "L2",
                "--name",
                "Line 2",
                "--owner",
                "FDE-B",
                "--artifact",
                "prd=docs/prd.md",
            )

            self.assertTrue(second["ok"])
            self.assertEqual(second["mode"], "update")
            self.assertEqual(record.read_text(encoding="utf-8"), "custom record\n")
            self.assertIn(str(record), second["files_existing"])
            patch_plan = Path(second["patch_plan"])
            self.assertTrue(patch_plan.exists())
            self.assertIn("docs/prd.md", patch_plan.read_text(encoding="utf-8"))

    def test_cross_workstream_lists_write_only_canonical_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.seed_memory(project_root)
            result = self.run_script(
                project_root,
                "--id",
                "L1 Checkout",
                "--name",
                "Checkout Migration",
                "--owner",
                "FDE-A",
                "--depends-on",
                "L2-Payments",
                "--impacts",
                "L3-Settlement",
            )
            record = (Path(result["workstream_root"]) / "delivery-record.md").read_text(encoding="utf-8")

            self.assertIn("Depends on:\n\n- l2-payments", record)
            self.assertIn("Impacts:\n\n- l3-settlement", record)
            self.assertNotIn("Depends on:\n\n- TBD", record)
            self.assertNotIn("Impacts:\n\n- TBD", record)

            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(project_root),
                    "--id",
                    "L4",
                    "--name",
                    "Line 4",
                    "--owner",
                    "FDE-D",
                    "--depends-on",
                    "L8B taxonomy readiness follows the catalog freeze",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("canonical workstream IDs only", json.loads(rejected.stdout)["error"])

    def test_requires_kickoff_memory_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    temp_dir,
                    "--id",
                    "L3",
                    "--name",
                    "Line 3",
                    "--owner",
                    "FDE-C",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertIn("missing_core_files", result)

    def test_program_is_rejected_as_reserved_virtual_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.seed_memory(project_root)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(project_root),
                    "--id",
                    "PROGRAM",
                    "--name",
                    "Program",
                    "--owner",
                    "PMO",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)

            self.assertEqual(2, completed.returncode)
            self.assertEqual("ADP-VIRTUAL-SCOPE-NOT-WORKSTREAM", result["error_code"])
            self.assertFalse((project_root / "_bmad-output/adp/memory/workstreams/program").exists())

    def test_language_golden_localizes_patch_plan_without_changing_wdr_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.seed_memory(project_root)
            first = self.run_script(
                project_root, "--id", "L1 Checkout", "--name", "Checkout Migration", "--owner", "FDE-A", "--language", "English"
            )
            record = Path(first["workstream_root"]) / "delivery-record.md"
            canonical_before = record.read_text(encoding="utf-8")

            second = self.run_script(
                project_root, "--id", "L1 Checkout", "--name", "Checkout Migration", "--owner", "FDE-A", "--language", "Chinese"
            )
            patch = Path(second["patch_plan"]).read_text(encoding="utf-8")

            self.assertEqual(second["language"]["locale"], "zh")
            self.assertIn("# 工作线注册补丁计划", patch)
            self.assertIn("Checkout Migration", patch)
            self.assertEqual(record.read_text(encoding="utf-8"), canonical_before)
            self.assertIn("## Identity", canonical_before)


    def test_retires_empty_duplicate_as_alias_without_deleting_directory(self) -> None:
        import importlib.util
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir); self.seed_memory(root)
            self.run_script(root, "--id", "l13-workforce-identity-access", "--name", "IAM full", "--owner", "FDE-A", "--scope", "Full IAM scope")
            self.run_script(root, "--id", "l13-iam", "--name", "IAM shell", "--owner", "TBD")
            command=[sys.executable,str(ALIAS_SCRIPT),str(root),"--canonical","l13-workforce-identity-access","--alias","l13-iam"]
            preview=json.loads(subprocess.run([*command,"--dry-run"],check=True,capture_output=True,text=True).stdout)
            self.assertTrue(preview["can_apply"])
            applied=json.loads(subprocess.run([*command,"--token",preview["token"]],check=True,capture_output=True,text=True).stdout)
            memory=root/"_bmad-output/adp/memory"
            self.assertTrue((memory/"workstreams/l13-iam/delivery-record.md").is_file())
            self.assertTrue((memory/"workstreams/l13-iam/workstream-alias.json").is_file())
            self.assertTrue(Path(applied["receipt_path"]).is_file())
            spec=importlib.util.spec_from_file_location("scope_alias_test",SCOPE_SCRIPT); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            ids=[x["scope_id"] for x in module.discover_wdr_registry(memory)]
            self.assertIn("l13-workforce-identity-access",ids)
            self.assertNotIn("l13-iam",ids)

if __name__ == "__main__":
    unittest.main()
