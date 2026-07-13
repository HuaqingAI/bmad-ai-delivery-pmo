import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "consume_program_status.py"


class ConsumeProgramStatusTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def scaffold(self, project_root: Path) -> tuple[Path, dict]:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        snapshot_root = memory_root / "snapshots" / "program-status"
        views_root = memory_root / "views"
        snapshot_root.mkdir(parents=True)
        views_root.mkdir(parents=True)
        model = {
            "schema_version": "1.0",
            "snapshot_id": "ps-1234567890abcdef",
            "generated_at": "2026-07-10T00:00:00Z",
            "as_of": "2026-07-10",
            "reporting_period": {"start": "2026-07-06", "end": "2026-07-10", "cadence": "weekly"},
            "baseline_revision": 3,
            "baseline_id": "BASE-ADP",
            "source_inventory": [],
            "source_fingerprints": {"plans/program-baseline.md": "sha256:abc"},
            "input_audit_id": "audit-123",
            "input_audit_disposition": "ready",
            "generator_version": "1.0.0",
            "locale": "zh-CN",
            "locale_fallback": False,
            "scenario": "global",
            "overall_status": "off-plan",
            "overall_status_label": "偏离计划",
            "overall_rule_id": "overall-critical-off-plan",
            "report_confidence": "low",
            "report_confidence_label": "低",
            "confidence_reasons": ["部分实际状态陈旧"],
            "rule_ids": ["overall-critical-off-plan", "milestone-forecast-outside-tolerance"],
            "project": {"name": "Migration", "owner": "Lead-A", "target_date": "2026-08-31"},
            "progress": None,
            "milestones": [],
            "gates": [],
            "critical_path": [{"id": "MS-1", "status": "off-plan", "critical": True}],
            "signals": [],
            "variances": [{"id": "MS-1", "status": "off-plan", "variance_days": 5}],
            "findings": [],
            "audit_summary": {
                "audit_status": "degraded",
                "execution_disposition": "ready_with_warnings",
                "report_confidence": "low",
                "recommended_workflows": ["adp-status-sync"],
            },
            "period_delta": {
                "comparison_status": "compared",
                "previous_snapshot_id": "ps-previous0000000",
                "overall_change": {"from": "at-risk", "to": "off-plan"},
                "worsened": ["milestone:MS-1"],
                "improved": [],
                "unchanged": [],
            },
        }
        text = json.dumps(model, ensure_ascii=False, indent=2) + "\n"
        (snapshot_root / f"{model['snapshot_id']}.json").write_text(text, encoding="utf-8")
        (snapshot_root / "latest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "snapshot_id": model["snapshot_id"],
                    "snapshot_path": f"snapshots/program-status/{model['snapshot_id']}.json",
                    "as_of": model["as_of"],
                    "reporting_period": model["reporting_period"],
                    "baseline_revision": model["baseline_revision"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (views_root / "program-status.json").write_text(text, encoding="utf-8")
        (views_root / "project-lead.md").write_text("# Canonical Project Lead\n", encoding="utf-8")
        (views_root / "weekly-report.md").write_text("# Canonical Weekly Report\n", encoding="utf-8")
        return memory_root, model

    def test_consumes_canonical_judgment_without_rewriting_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, model = self.scaffold(project_root)
            project_view = memory_root / "views" / "project-lead.md"
            before = project_view.read_bytes()

            completed = self.run_script(project_root, "--intent", "overall", "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["canonical_status"]["overall_status"], model["overall_status"])
            self.assertEqual(result["canonical_status"]["report_confidence"], model["report_confidence"])
            self.assertEqual(result["canonical_status"]["snapshot_id"], model["snapshot_id"])
            self.assertEqual(result["period_review"], model["period_delta"])
            self.assertEqual(result["recovery_routing"]["recommended_workflows"], ["adp-status-sync"])
            self.assertEqual(result["writes_performed"], [])
            self.assertEqual(project_view.read_bytes(), before)

    def test_meeting_preparation_routes_with_canonical_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            _, model = self.scaffold(project_root)

            completed = self.run_script(
                project_root,
                "--intent",
                "meeting-preparation",
                "--scenario",
                "business-biweekly",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            meeting = result["meeting_preparation"]
            self.assertEqual(meeting["owning_workflow"], "adp-meeting-pack")
            self.assertEqual(meeting["lineage"]["program_status_snapshot_id"], model["snapshot_id"])
            self.assertEqual(meeting["lineage"]["source_fingerprints"], model["source_fingerprints"])
            self.assertNotIn("adp-meeting-pack", result["recovery_routing"]["recommended_workflows"])

    def test_missing_canonical_status_only_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "_bmad-output" / "adp" / "memory").mkdir(parents=True)

            completed = self.run_script(project_root, check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            self.assertNotIn("canonical_status", result)
            self.assertIn("adp-program-status", result["recommended_workflows"])
            self.assertEqual(result["writes_performed"], [])

    def test_snapshot_mismatch_blocks_without_substitute_judgment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, _ = self.scaffold(project_root)
            view_path = memory_root / "views" / "program-status.json"
            view = json.loads(view_path.read_text(encoding="utf-8"))
            view["overall_status"] = "on-plan"
            view_path.write_text(json.dumps(view, ensure_ascii=False) + "\n", encoding="utf-8")

            completed = self.run_script(project_root, check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertIn("does not match its immutable snapshot", result["reason"])
            self.assertNotIn("canonical_status", result)
            self.assertEqual(result["recommended_workflows"], ["adp-state-audit", "adp-program-status"])

    def test_requested_as_of_mismatch_routes_instead_of_recomputing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(project_root, "--as-of", "2026-07-11", check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["canonical_status"]["as_of"], "2026-07-10")
            self.assertIn("not requested 2026-07-11", result["reason"])


if __name__ == "__main__":
    unittest.main()
