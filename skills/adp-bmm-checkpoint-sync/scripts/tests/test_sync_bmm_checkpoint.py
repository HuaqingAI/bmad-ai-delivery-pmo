import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sync_bmm_checkpoint.py"
BOOTSTRAP = Path(__file__).resolve().parents[3] / "adp-project-kickoff" / "scripts" / "bootstrap_adp.py"
REGISTER = Path(__file__).resolve().parents[3] / "adp-workstream-register" / "scripts" / "register_workstream.py"


class SyncBmmCheckpointTests(unittest.TestCase):
    def register_workstream(self, project_root: Path) -> dict:
        subprocess.run(
            [
                sys.executable,
                str(BOOTSTRAP),
                str(project_root),
                "--project-name",
                "Checkpoint Sync Test",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(REGISTER),
                str(project_root),
                "--id",
                "L1 Checkout",
                "--name",
                "Checkout Migration",
                "--owner",
                "FDE-A",
                "--business-owner",
                "Biz-A",
                "--phase",
                "draft",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_prd_checkpoint_updates_record_and_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)

            result = self.run_script(
                project_root,
                "--workstream-id",
                "L1 Checkout",
                "--checkpoint",
                "prd",
                "--summary",
                "Checkout PRD baseline ready for project review",
                "--artifact",
                "prd=docs/prd-checkout.md",
                "--artifact-status",
                "baseline",
                "--scope",
                "Checkout order flow migration",
                "--acceptance",
                "Business owner confirms checkout parity",
                "--evidence-required",
                "Demo and test proof mapped to checkout criteria",
                "--business-confirmation",
                "Biz-A owns final confirmation",
                "--next-action",
                "FDE-A extracts architecture dependencies",
            )

            self.assertTrue(result["ok"])
            record = Path(setup["workstream_root"]) / "delivery-record.md"
            readiness = Path(setup["workstream_root"]) / "readiness.md"
            record_text = record.read_text(encoding="utf-8")
            readiness_text = readiness.read_text(encoding="utf-8")
            self.assertIn("| PRD | docs/prd-checkout.md | baseline |", record_text)
            self.assertIn("Checkout order flow migration", record_text)
            self.assertIn("Business owner confirms checkout parity", record_text)
            self.assertNotIn("Acceptance criteria not captured from PRD checkpoint", readiness_text)
            self.assertIn("Daily Log", Path(result["daily_log"]).read_text(encoding="utf-8"))

    def test_validation_checkpoint_appends_evidence_and_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            setup = self.register_workstream(project_root)

            result = self.run_script(
                project_root,
                "--workstream-id",
                "l1-checkout",
                "--checkpoint",
                "validation",
                "--summary",
                "Validation smoke tests passed but customer confirmation is pending",
                "--artifact",
                "validation=reports/checkout-validation.md",
                "--evidence",
                "Smoke test report|test|reports/smoke.md|Checkout parity|confirmed|none",
                "--readiness-gap",
                "Customer confirmation pending|Acceptance clarity|FDE-A|Schedule business review|Before acceptance review|Project lead",
            )

            self.assertTrue(result["ok"])
            root = Path(setup["workstream_root"])
            evidence_text = (root / "evidence.md").read_text(encoding="utf-8")
            readiness_text = (root / "readiness.md").read_text(encoding="utf-8")
            record_text = (root / "delivery-record.md").read_text(encoding="utf-8")
            self.assertIn("Smoke test report", evidence_text)
            self.assertIn("Customer confirmation pending", readiness_text)
            self.assertIn("Business or customer confirmation status needs confirmation", readiness_text)
            self.assertIn("| Validation evidence | reports/checkout-validation.md | linked |", record_text)

    def test_missing_record_fails_with_registration_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    temp_dir,
                    "--workstream-id",
                    "missing-line",
                    "--checkpoint",
                    "prd",
                    "--summary",
                    "Should fail",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertIn("adp-workstream-register", result["error"])


if __name__ == "__main__":
    unittest.main()
