import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "consume_program_status.py"
PANEL_SCRIPT = SCRIPT.parents[2] / "adp-management-panel" / "scripts" / "management_panel.py"
PROGRESS_GOLDEN = SCRIPT.parents[2] / "adp-program-status" / "assets" / "fixtures" / "progress-v3" / "golden-measurable-boundary.json"


def progress_fixture(as_of: str, revision: int, period: dict[str, str]) -> dict:
    payload = json.loads(PROGRESS_GOLDEN.read_text(encoding="utf-8"))
    payload["as_of"] = as_of
    payload["reporting_period"] = {"start": period["start"], "end": period["end"]}
    payload["scope_identity"]["baseline_revision"] = revision
    payload["scope_identity"]["scope_revision"] = f"BASE-ADP:r{revision}"
    return payload


def management_markdown(model: dict, profile: str) -> str:
    metadata = {
        "snapshot_id": model["snapshot_id"],
        "generated_at": model["generated_at"],
        "as_of": model["as_of"],
        "reporting_period": model["reporting_period"],
        "report_confidence": model["report_confidence"],
        "scenario": model["scenario"],
        "input_audit_id": model["input_audit_id"],
        "baseline_revision": model["baseline_revision"],
        "source_fingerprints": model["source_fingerprints"],
        "locale": model["locale"],
        "locale_fallback": model["locale_fallback"],
        "render_contract": {
            "coverage_profile": profile,
            "catalog_locale": model["locale"],
            "catalog_fingerprint": model["render_contract"]["catalog_fingerprint"],
            "message_keys": [],
            "unresolved_message_keys": [],
            "source_fact_translation_persisted": False,
            "localized_system_text": [],
        },
        "generator_version": model["generator_version"],
        "progress_schema_version": model["progress"]["progress_schema_version"],
        "progress_scope_identity": model["progress"]["scope_identity"],
        "flow_state_schema_version": model["flow_state"]["flow_state_schema_version"],
    }
    return (
        "# Canonical Management View\n\n"
        f"Snapshot: `{model['snapshot_id']}`\n\n"
        "<!-- adp:artifact-metadata:v1 -->\n\n```json\n"
        + json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n"
    )


class ConsumeProgramStatusTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def create_fixture_panel(self, project_root: Path) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                str(PANEL_SCRIPT),
                str(project_root),
                "--fixture",
                "--generated-at",
                "2026-07-13T09:05:00Z",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def align_canonical_status_to_panel(self, memory_root: Path, panel: dict) -> None:
        bundle = json.loads(Path(panel["immutable_bundle"]).read_text(encoding="utf-8"))
        panel_status = bundle["data"]["status"]
        current_path = memory_root / "views/program-status.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        current.update(panel_status)
        snapshot_root = memory_root / "snapshots/program-status"
        snapshot_path = snapshot_root / f"{current['snapshot_id']}.json"
        payload = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
        current_path.write_text(payload, encoding="utf-8")
        snapshot_path.write_text(payload, encoding="utf-8")
        (snapshot_root / "latest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "snapshot_id": current["snapshot_id"],
                    "snapshot_path": f"snapshots/program-status/{current['snapshot_id']}.json",
                    "as_of": current["as_of"],
                    "reporting_period": current["reporting_period"],
                    "baseline_revision": current["baseline_revision"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "views/project-lead.md").write_text(
            management_markdown(current, "adp-project-lead-markdown"), encoding="utf-8"
        )
        (memory_root / "views/weekly-report.md").write_text(
            management_markdown(current, "adp-weekly-report-markdown"), encoding="utf-8"
        )

    def scaffold(self, project_root: Path) -> tuple[Path, dict]:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        snapshot_root = memory_root / "snapshots" / "program-status"
        views_root = memory_root / "views"
        snapshot_root.mkdir(parents=True)
        views_root.mkdir(parents=True)
        reporting_period = {"start": "2026-07-06", "end": "2026-07-10", "cadence": "weekly"}
        model = {
            "schema_version": "1.0",
            "snapshot_id": "ps-1234567890abcdef",
            "generated_at": "2026-07-10T00:00:00Z",
            "as_of": "2026-07-10",
            "reporting_period": reporting_period,
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
            "progress": progress_fixture("2026-07-10", 3, reporting_period),
            "flow_state": {"flow_state_schema_version": "1.0.0"},
            "render_contract": {"catalog_fingerprint": "catalog-sha256-abc"},
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
        (views_root / "project-lead.md").write_text(
            management_markdown(model, "adp-project-lead-markdown"), encoding="utf-8"
        )
        (views_root / "weekly-report.md").write_text(
            management_markdown(model, "adp-weekly-report-markdown"), encoding="utf-8"
        )
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
            self.assertEqual(result["management_markdown_lineage"]["status"], "verified")
            self.assertEqual(result["management_markdown_lineage"]["snapshot_id"], model["snapshot_id"])
            self.assertEqual(
                set(result["management_markdown_lineage"]["views"]),
                {"project_lead", "weekly_report"},
            )
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

    def test_panel_refresh_routes_without_rendering_or_touching_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, _ = self.scaffold(project_root)
            cursor = memory_root / "meetings/cursors/fde-morning.json"
            cursor.parent.mkdir(parents=True)
            cursor.write_text('{"meeting_instance_id":"mi-before"}\n', encoding="utf-8")
            before = cursor.read_bytes()

            completed = self.run_script(
                project_root,
                "--intent",
                "panel-refresh",
                "--panel-view",
                "project-lead",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(result["panel_journey"]["owning_workflow"], "adp-management-panel")
            self.assertEqual(result["panel_journey"]["operation"], "refresh")
            self.assertEqual(result["panel_journey"]["status"], "route-required")
            self.assertEqual(result["panel_journey"]["explanation"]["overall_status"], "off-plan")
            self.assertEqual(cursor.read_bytes(), before)
            self.assertEqual(result["writes_performed"], [])

    def test_panel_open_reads_manifest_and_explains_requested_meeting_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, _ = self.scaffold(project_root)
            panel = self.create_fixture_panel(project_root)
            self.align_canonical_status_to_panel(memory_root, panel)

            completed = self.run_script(
                project_root,
                "--intent",
                "panel-open",
                "--panel-view",
                "fde-morning",
            )
            result = json.loads(completed.stdout)

            journey = result["panel_journey"]
            self.assertEqual(journey["operation"], "open")
            self.assertEqual(journey["panel_id"], panel["panel_id"])
            self.assertEqual(journey["open_hash"], "#v=1&view=fde-morning&mode=quantitative-progress")
            self.assertEqual(journey["explanation"]["meeting_pack_id"], "2026-07-13-fde-morning")
            self.assertEqual(journey["explanation"]["meeting_window"]["status"], "confirmed")
            self.assertNotIn("forecast_summary", journey["explanation"])

    def test_panel_archive_requires_profile_and_routes_official_association_to_meeting_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            missing = self.run_script(project_root, "--intent", "panel-archive", check=False)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("distribution-profile", json.loads(missing.stdout)["reason"])

            completed = self.run_script(
                project_root,
                "--intent",
                "panel-archive",
                "--panel-view",
                "business-biweekly",
                "--distribution-profile",
                "shareable-summary",
            )
            journey = json.loads(completed.stdout)["panel_journey"]
            self.assertEqual(journey["operation"], "archive")
            self.assertEqual(journey["distribution_profile"], "shareable-summary")
            self.assertEqual(journey["official_association"]["status"], "pending-successful-meeting-sync")
            self.assertEqual(journey["official_association"]["owning_workflow"], "adp-meeting-sync")

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

    def test_stale_management_markdown_snapshot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, _ = self.scaffold(project_root)
            view_path = memory_root / "views/project-lead.md"
            text = view_path.read_text(encoding="utf-8").replace(
                '"snapshot_id": "ps-1234567890abcdef"',
                '"snapshot_id": "ps-stale000000000"',
            )
            view_path.write_text(text, encoding="utf-8")

            completed = self.run_script(project_root, "--view", "project-lead", check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "ADP-PL-MANAGEMENT-MARKDOWN-LINEAGE-MISMATCH")
            self.assertIn("snapshot_id", result["reason"])
            self.assertEqual(result["recommended_workflows"], ["adp-state-audit", "adp-program-status"])

    def test_management_markdown_source_lineage_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, _ = self.scaffold(project_root)
            view_path = memory_root / "views/weekly-report.md"
            text = view_path.read_text(encoding="utf-8").replace(
                '"plans/program-baseline.md": "sha256:abc"',
                '"plans/program-baseline.md": "sha256:wrong"',
            )
            view_path.write_text(text, encoding="utf-8")

            completed = self.run_script(project_root, "--view", "weekly-report", check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "ADP-PL-MANAGEMENT-MARKDOWN-LINEAGE-MISMATCH")
            self.assertIn("source_fingerprints", result["reason"])

    def test_management_markdown_without_machine_lineage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root, _ = self.scaffold(project_root)
            (memory_root / "views/project-lead.md").write_text("# copied old report\n", encoding="utf-8")

            completed = self.run_script(project_root, "--view", "project-lead", check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "ADP-PL-MANAGEMENT-MARKDOWN-LINEAGE-MISSING")

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
