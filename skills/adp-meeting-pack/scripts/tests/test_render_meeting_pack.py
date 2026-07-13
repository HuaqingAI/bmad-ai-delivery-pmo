import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_meeting_pack.py"


def load_renderer_module():
    spec = importlib.util.spec_from_file_location("render_meeting_pack", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RECORD = """# Workstream Delivery Record

## Identity

- Workstream ID: l1-checkout
- Name: Checkout
- FDE owner: FDE-A
- Business owner: Biz-A
- Current BMM phase: validation
- Current ADP status: ready

## Project Status

- Progress: Validation running
- Blockers: Payment callback evidence is blocked by Biz-A confirmation
- Risks: Acceptance could slip if callback evidence stays open
- Dependencies: l2-payments release confirmation
- Scope or change notes: Payment callback scope changed
- Next actions: FDE-A add checkout validation evidence; due: 2026-07-11
- Last status sync: 2026-07-09T09:00:00+08:00

## Cross-Workstream Links

Depends on:

- l2-payments

Impacts:

- l3-settlement

L0 references:

- gate-payments
"""


class RenderMeetingPackTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def scaffold(self, project_root: Path) -> Path:
        config_path = project_root / "_bmad" / "adp" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "communication_language: English\ndocument_output_language: English\nmeeting_pack_item_limit: 10\n",
            encoding="utf-8",
        )
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        for rel in [
            "actions",
            "audits",
            "daily",
            "decisions/business-decision-packets",
            "intake/status-sync",
            "views",
            "workstreams/l1-checkout",
        ]:
            (memory_root / rel).mkdir(parents=True, exist_ok=True)
        (memory_root / "index.md").write_text("# index.md\n", encoding="utf-8")
        (memory_root / "project-charter.md").write_text("# project-charter.md\n", encoding="utf-8")
        (memory_root / "cadence.md").write_text(
            "# Cadence\n\nProject timezone: Asia/Shanghai\n\n## FDE Morning Meeting\n\n- Recurring weekdays: Monday, Wednesday, Friday\n",
            encoding="utf-8",
        )
        workstream_root = memory_root / "workstreams" / "l1-checkout"
        (workstream_root / "delivery-record.md").write_text(RECORD, encoding="utf-8")
        (workstream_root / "evidence.md").write_text("# Evidence\n\n- Checkout callback evidence linked.\n", encoding="utf-8")
        (workstream_root / "readiness.md").write_text("# Readiness\n\n- Acceptance evidence pending Biz-A confirmation.\n", encoding="utf-8")
        (workstream_root / "decisions.md").write_text("# Decisions\n\nNo open decisions.\n", encoding="utf-8")
        (memory_root / "actions" / "action-ledger.md").write_text(
            "\n".join(
                [
                    "# Action Ledger",
                    "",
                    "| Action ID | Status | Owner | Workstream | Affected Workstreams | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| ACT-20260710-001 | open | FDE-A | l1-checkout | l1-checkout | Add checkout validation evidence | meetings/2026-07-10-sync.md#M-001 | Meeting action | 2026-07-11 | Evidence linked in workstream evidence.md | 2026-07-10T09:00:00+08:00 | adp-status-sync |",
                    "| ACT-20260710-002 | open | TBD | l1-checkout | l1-checkout | Resolve generic follow-up | TBD | Meeting action | TBD | TBD | 2026-07-10T09:00:00+08:00 | adp-status-sync |",
                    "| ACT-20260710-003 | open | FDE-C | l1-checkout | l1-checkout | Confirm residual risk posture | meetings/2026-07-10-sync.md#M-003 | Meeting action | 2026-07-12 | No known risk remains after Biz-A confirmation | 2026-07-10T09:00:00+08:00 | adp-status-sync |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "decisions" / "decision-log.md").write_text("# Decision Log\n", encoding="utf-8")
        self.add_program_status(memory_root)
        return memory_root

    def add_program_status(self, memory_root: Path) -> None:
        model = {
            "schema_version": "1.0",
            "snapshot_id": "ps-meeting-pack-fixture",
            "generated_at": "2026-07-10T08:00:00Z",
            "as_of": "2026-07-10",
            "reporting_period": {"start": "2026-07-06", "end": "2026-07-10"},
            "baseline_revision": 2,
            "baseline_id": "PROGRAM-BASELINE",
            "source_inventory": [],
            "source_fingerprints": {"plans/program-baseline.md": "sha256:fixture"},
            "input_audit_id": "audit-program-status-fixture",
            "input_audit_disposition": "ready",
            "generator_version": "1.0.0",
            "locale": "en",
            "overall_status": "at-risk",
            "overall_status_label": "At risk",
            "overall_rule_id": "PS-OVERALL-CRITICAL-AT-RISK",
            "report_confidence": "medium",
            "report_confidence_label": "Medium",
            "confidence_reasons": ["fixture"],
            "rule_ids": ["PS-MS-FORECAST-RISK"],
            "project": {
                "name": "Checkout migration",
                "owner": "PMO",
                "target_date": "2026-08-01",
                "target_assessment": {
                    "id": "PROJECT-TARGET",
                    "name": "Checkout migration",
                    "critical": True,
                    "status": "at-risk",
                    "planned_date": "2026-08-01",
                    "forecast_date": "2026-08-03",
                    "actual_date": None,
                    "variance_days": 2,
                    "rule_id": "PS-PROJECT-FORECAST-RISK",
                    "source_references": ["plans/program-baseline.md#target"],
                },
            },
            "progress": {"weighted_completion_percent": None},
            "milestones": [],
            "gates": [
                {
                    "id": "GATE-VALIDATION",
                    "name": "Validation gate",
                    "critical": True,
                    "status": "at-risk",
                    "planned_date": "2026-07-15",
                    "forecast_date": "2026-07-17",
                    "actual_date": None,
                    "variance_days": 2,
                    "rule_id": "PS-GATE-FORECAST-RISK",
                    "source_references": ["plans/program-baseline.md#GATE-VALIDATION"],
                }
            ],
            "critical_path": [],
            "signals": [],
            "variances": [],
            "findings": [],
            "audit_summary": {},
            "period_delta": {
                "comparison_status": "compared",
                "previous_snapshot_id": "ps-previous",
                "overall_change": {"from": "on-plan", "to": "at-risk"},
                "new_items": ["gate:GATE-VALIDATION"],
                "completed": [],
                "worsened": ["gate:GATE-VALIDATION"],
                "improved": [],
                "changed": ["gate:GATE-VALIDATION"],
            },
        }
        model["critical_path"] = [model["project"]["target_assessment"], *model["gates"]]
        model["variances"] = [model["project"]["target_assessment"], *model["gates"]]
        path = memory_root / "views" / "program-status.json"
        path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")

    def write_gate_inputs(self, memory_root: Path, audit_status: str = "pass") -> tuple[Path, Path]:
        prepass = {
            "ok": True,
            "schema_version": 2,
            "sources_read": [{"path": "decisions/decision-log.md", "modified": "2026-07-10T09:00:00+08:00", "bytes": 100}],
            "workstreams": [
                {
                    "id": "l1-checkout",
                    "owner": "FDE-A",
                    "business_owner": "Biz-A",
                    "status": "ready",
                    "change_notes": "Payment callback scope changed for Day-1 support.",
                    "record": "workstreams/l1-checkout/delivery-record.md",
                }
            ],
            "ledger_actions": [],
        }
        audit = {
            "ok": True,
            "audit_status": audit_status,
            "counts": {
                "blocking_findings": 0 if audit_status == "pass" else 1,
                "warning_findings": 0,
            },
            "source_inventory": {"sources_read": prepass["sources_read"]},
            "findings": {
                "freshness": {"stale_workstreams": [], "stale_actions": [], "views_requiring_refresh": []},
                "completeness": {"blocking_gaps": [], "non_blocking_gaps": []},
                "consistency": {"source_disagreements": [], "consistency_warnings": [], "recommended_refreshes": []},
                "closure": {"open_business_packets": [], "escalation_candidates": [], "unconsumed_intake_files": []},
                "merge_quality": {"conflict_candidates": [], "duplicate_candidates": [], "overlap_candidates": []},
            },
            "recommended_workflows": [],
        }
        prepass_path = memory_root / "audits" / f"prepass-{audit_status}.json"
        audit_path = memory_root / "audits" / f"audit-{audit_status}.json"
        prepass_path.write_text(json.dumps(prepass, indent=2) + "\n", encoding="utf-8")
        audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
        return prepass_path, audit_path

    def add_business_biweekly_sources(self, memory_root: Path) -> None:
        (memory_root / "decisions" / "decision-log.md").write_text(
            "\n".join(
                [
                    "# Decision Log",
                    "",
                    "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| 2026-07-08 | Business | Payment signoff owner confirmed | meetings/2026-07-08-business.md | l1-checkout | Biz-A | closed | decisions/decision-log.md#payment |",
                    "| TBD | Business | Confirm launch window | meetings/2026-07-09-business.md | l1-checkout | Biz-A | open | decisions/business-decision-packets/launch-window.md |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "decisions" / "business-decision-packets" / "launch-window.md").write_text(
            "\n".join(
                [
                    "# Business Decision Packet: Launch Window",
                    "",
                    "Affected workstreams: l1-checkout",
                    "Status: open",
                    "Confirming owner: Biz-A",
                    "Deadline / trigger: 2026-07-14",
                    "",
                    "## Background",
                    "",
                    "Launch timing changes Day-1 staffing.",
                    "",
                    "## Decision Needed",
                    "",
                    "Confirm launch window",
                    "",
                    "## Options",
                    "",
                    "- Option A: launch on the planned window.",
                    "- Option B: hold until support staffing is confirmed.",
                    "",
                    "## Recommendation",
                    "",
                    "Choose Option B unless staffing is confirmed.",
                    "",
                    "## Risks and Trade-offs",
                    "",
                    "Day-1 support coverage risk.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "views" / "acceptance-readiness.md").write_text(
            "\n".join(
                [
                    "# Acceptance Readiness View",
                    "",
                    "| Workstream | Readiness Score | Missing Evidence | Unclosed Criteria | Business Confirmation | Status |",
                    "| --- | --- | --- | --- | --- | --- |",
                    "| l1-checkout | 82 | Callback evidence | Biz signoff | Biz-A | at-risk |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "views" / "cutover-readiness.md").write_text(
            "\n".join(
                [
                    "# Cutover Readiness View",
                    "",
                    "| Workstream | Readiness Score | Missing Evidence | Unclosed Criteria | Business Confirmation | Status |",
                    "| --- | --- | --- | --- | --- | --- |",
                    "| l1-checkout | 75 | Rollback rehearsal | Freeze approval | Biz-A | blocked |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        roadmap = {
            "ok": True,
            "schema_version": 2,
            "generated_at": "2026-07-10T08:00:00+08:00",
            "baseline_revision": 2,
            "program_status": {
                "snapshot_id": "ps-meeting-pack-fixture",
                "as_of": "2026-07-10",
                "overall_status": "at-risk",
                "report_confidence": "medium",
                "input_audit_id": "audit-program-status-fixture",
                "generator_version": "1.0.0",
                "source": "views/program-status.json",
            },
            "milestone_timeline": [
                {
                    "milestone": "Checkout validation complete",
                    "type": "checkpoint",
                    "status": "planned",
                    "planned": "2026-07-15",
                    "forecast": "TBD",
                    "actual": "TBD",
                    "owner": "FDE-A",
                    "confidence": "medium",
                    "depends_on": "l2-payments",
                    "source": "workstreams/l1-checkout/delivery-record.md#roadmap",
                }
            ],
            "unscheduled_milestones": [
                {
                    "milestone": "Theme launch window",
                    "type": "delivery-window",
                    "status": "planned",
                    "planned": "TBD",
                    "forecast": "TBD",
                    "actual": "TBD",
                    "owner": "FDE-A",
                    "confidence": "low",
                    "source": "workstreams/l1-checkout/delivery-record.md#roadmap",
                    "notes": ["No sourced date yet."],
                }
            ],
            "blocked_by_decisions": [
                {
                    "source": "decisions/business-decision-packets/launch-window.md",
                    "decision": "Confirm launch window",
                    "owner": "Biz-A",
                    "status": "open",
                    "workstreams": ["l1-checkout"],
                }
            ],
            "blocked_by_dependencies": [
                {
                    "source": "workstreams/l1-checkout/delivery-record.md",
                    "workstream": "l1-checkout",
                    "type": "blockers",
                    "item": "Payment callback evidence blocked by Biz-A confirmation",
                    "owner": "FDE-A",
                }
            ],
            "changed_since_last_roadmap": [],
            "excluded_items": [],
        }
        (memory_root / "views" / "roadmap.json").write_text(json.dumps(roadmap, indent=2) + "\n", encoding="utf-8")
        (memory_root / "views" / "roadmap.md").write_text("# ADP Roadmap\n", encoding="utf-8")

    def test_renders_fde_morning_pack_with_audit_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-10",
                "--period-start",
                "2026-07-08",
                "--period-end",
                "2026-07-10",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["counts"]["actions_in_board"], 0)
            self.assertEqual(result["counts"]["actions_excluded"], 3)
            markdown_path = Path(result["outputs"]["markdown"])
            distillate_path = Path(result["outputs"]["distillate"])
            audit_path = Path(result["outputs"]["audit"])
            self.assertTrue(markdown_path.exists())
            self.assertTrue(distillate_path.exists())
            self.assertTrue(audit_path.exists())
            text = markdown_path.read_text(encoding="utf-8")
            self.assertIn("This meeting pack is not a source of truth", text)
            self.assertIn("Confirmed Incremental Window", text)
            self.assertIn("Changes Since the Previous Cycle", text)
            self.assertNotIn("Workstream Roundtable", text)
            self.assertIn("ACT-20260710-001", text)
            self.assertIn("ACT-20260710-003", text)
            self.assertIn("Action Quality Gaps", text)
            self.assertIn("closure criteria verifiability not confirmed", text)
            self.assertIn("Post-Meeting Capture Checklist", text)
            self.assertIn("Source Inventory", text)
            distillate = json.loads(distillate_path.read_text(encoding="utf-8"))
            self.assertEqual(distillate["scenario"], "fde-morning")
            self.assertEqual(distillate["meeting_pack_id"], "2026-07-10-fde-morning")
            self.assertEqual(distillate["meeting_pack_path"], str(markdown_path))
            self.assertEqual(distillate["audit_path"], str(audit_path))
            self.assertEqual(distillate["roadmap_version"], "not-applicable")
            self.assertEqual(len(distillate["boards"]["actions"]), 0)
            self.assertEqual(len(distillate["gaps"]["action_quality"]), 3)
            action_gaps = {item["Action ID"]: item["Gap"] for item in distillate["gaps"]["action_quality"]}
            self.assertIn("closure criteria verifiability not confirmed", action_gaps["ACT-20260710-001"])
            self.assertIn("due trigger missing", action_gaps["ACT-20260710-002"])
            self.assertIn("adp-meeting-sync", distillate["next_workflow_payload"]["recommended_workflows"])
            self.assertEqual(
                distillate["next_workflow_payload"]["meeting_pack_id"],
                distillate["meeting_pack_id"],
            )
            self.assertEqual(
                distillate["next_workflow_payload"]["meeting_pack_path"],
                str(markdown_path),
            )
            self.assertEqual(distillate["next_workflow_payload"]["scenario"], "fde-morning")
            self.assertEqual(distillate["next_workflow_payload"]["audit_path"], str(audit_path))
            self.assertEqual(distillate["next_workflow_payload"]["roadmap_version"], "not-applicable")
            lineage = distillate["next_workflow_payload"]["lineage"]
            self.assertEqual(lineage["meeting_pack_id"], distillate["meeting_pack_id"])
            self.assertEqual(lineage["meeting_pack_path"], distillate["meeting_pack_path"])
            self.assertEqual(lineage["scenario"], distillate["scenario"])
            self.assertEqual(lineage["audit_path"], distillate["audit_path"])
            self.assertEqual(lineage["roadmap_version"], distillate["roadmap_version"])
            self.assertEqual(lineage["program_status_snapshot_id"], "ps-meeting-pack-fixture")
            self.assertEqual(lineage["baseline_revision"], 2)
            self.assertEqual(lineage["input_audit_id"], "audit-program-status-fixture")

    def test_custom_output_path_and_run_folder_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)
            output_base = project_root / "packs"

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-10",
                "--period-start",
                "2026-07-08",
                "--period-end",
                "2026-07-10",
                "--meeting-pack-output-path",
                str(output_base),
                "--run-folder-pattern",
                "{date}-{scenario}",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["outputs"]["markdown"]).parent, output_base / "2026-07-10-fde-morning")

    def test_existing_pack_pair_blocks_until_replacement_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            prepass_path, audit_path = self.write_gate_inputs(memory_root)
            args = (
                "--date",
                "2026-07-10",
                "--period-start",
                "2026-07-08",
                "--period-end",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            first = self.run_script(project_root, *args)
            markdown_path = Path(first["outputs"]["markdown"])
            distillate_path = Path(first["outputs"]["distillate"])
            original_markdown = markdown_path.read_text(encoding="utf-8")
            original_distillate = distillate_path.read_text(encoding="utf-8")

            blocked = self.run_script(project_root, *args, check=False)

            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["reason"], "meeting pack output collision")
            self.assertEqual(set(blocked["collisions"]), {str(markdown_path), str(distillate_path)})
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), original_markdown)
            self.assertEqual(distillate_path.read_text(encoding="utf-8"), original_distillate)

            replaced = self.run_script(project_root, *args, "--replace")
            self.assertTrue(replaced["ok"])
            self.assertEqual(replaced["status"], "complete")

    def test_board_selection_uses_typed_fields_not_keyword_matches(self) -> None:
        renderer = load_renderer_module()

        dependency_rows = renderer.dependency_board(
            {
                "workstreams": [
                    {
                        "id": "l1-checkout",
                        "owner": "FDE-A",
                        "blockers": "TBD",
                        "risks": "red delay waiting on owner",
                        "dependencies": "TBD",
                        "links": {},
                        "record": "workstreams/l1-checkout/delivery-record.md",
                    }
                ]
            }
        )
        self.assertEqual(dependency_rows, [])

        audit = {
            "findings": {
                "completeness": {
                    "blocking_gaps": [
                        {"field": "readiness", "gap": "file missing", "source": "workstreams/l1/readiness.md"},
                        {"gap": "readiness evidence pending", "source": "workstreams/l2/delivery-record.md"},
                    ],
                    "non_blocking_gaps": [],
                },
                "freshness": {
                    "views_requiring_refresh": [
                        {"path": "views/acceptance-readiness.md", "reason": "durable source newer"},
                        {"path": "views/risk-matrix.md", "reason": "durable source newer"},
                    ]
                },
            }
        }
        readiness_rows = renderer.readiness_exceptions(audit, {})
        self.assertEqual(len(readiness_rows), 2)
        self.assertEqual(readiness_rows[0]["field"], "readiness")
        self.assertEqual(readiness_rows[1]["path"], "views/acceptance-readiness.md")

    def test_action_board_requires_typed_closure_verifiability_verdict(self) -> None:
        renderer = load_renderer_module()
        base_action = {
            "status": "open",
            "owner": "FDE-A",
            "source": "meetings/2026-07-10-sync.md#M-001",
            "due_or_trigger": "2026-07-11",
            "closure_criteria": "done",
            "workstream": "l1-checkout",
            "action": "Add checkout evidence",
        }

        groups, excluded = renderer.action_board(
            [
                {**base_action, "action_id": "A-untyped"},
                {**base_action, "action_id": "A-false", "closure_criteria_verifiable": False},
                {**base_action, "action_id": "A-typed", "closure_criteria_verifiable": True},
            ]
        )

        self.assertEqual([item["Action ID"] for item in groups["FDE-A"]], ["A-typed"])
        self.assertEqual({item["Action ID"] for item in excluded}, {"A-untyped", "A-false"})
        self.assertTrue(all("verifiability not confirmed" in item["Gap"] for item in excluded))

    def test_business_impact_requires_typed_business_semantics(self) -> None:
        renderer = load_renderer_module()
        dependency = {
            "Source": "workstreams/l1-checkout/delivery-record.md",
            "Workstream": "l1-checkout",
            "Dependency / blocker": "Waiting for payment callback evidence",
            "Owner": "FDE-A",
            "Risk": "Acceptance could slip",
        }

        fallback = renderer.cross_line_business_impact_rows([], [dependency])

        self.assertEqual(fallback[0]["Dependency / Blocker"], dependency["Dependency / blocker"])
        self.assertEqual(fallback[0]["Risk"], dependency["Risk"])
        self.assertEqual(fallback[0]["Business Impact"], "TBD")
        self.assertEqual(fallback[0]["Status"], "TBD")

        prepass = {
            "meeting_pack_boards": {
                "business_impact": [
                    {
                        "source": dependency["Source"],
                        "workstream": "l1-checkout",
                        "business_impact": "Day-1 support staffing must move by one week",
                        "owner": "Biz-A",
                        "status": "at-risk",
                    }
                ]
            }
        }
        typed = renderer.cross_line_business_impact_rows([], [dependency], prepass=prepass)
        self.assertEqual(
            typed,
            [
                {
                    "Source": dependency["Source"],
                    "Workstream": "l1-checkout",
                    "Business Impact": "Day-1 support staffing must move by one week",
                    "Owner": "Biz-A",
                    "Status": "at-risk",
                }
            ],
        )

    def test_last_meeting_closure_uses_typed_origin_or_exact_meetings_segment(self) -> None:
        renderer = load_renderer_module()
        audit = {"findings": {"closure": {}}}
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            action_root = memory_root / "actions"
            action_root.mkdir(parents=True)
            (action_root / "action-ledger.md").write_text(
                "\n".join(
                    [
                        "| Action ID | Status | Owner | Workstream | Action | Source | Closure Criteria |",
                        "| --- | --- | --- | --- | --- | --- | --- |",
                        "| A-1 | closed | FDE-A | l1 | Canonical | meetings/weekly-sync.md | Done |",
                        "| A-2 | closed | FDE-B | l2 | False positive | notes/customer-meeting-summary.md | Done |",
                        "| A-3 | closed | FDE-C | l3 | Ambiguous legacy | weekly-sync.md | Done |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            legacy = renderer.last_meeting_closure_rows(memory_root, audit)
            self.assertEqual([row["Item"].strip() for row in legacy], ["A-1 Canonical"])

            prepass = {
                "meeting_pack_boards": {
                    "last_meeting_closure": [
                        {
                            "origin_type": "meeting",
                            "source": "weekly-sync.md",
                            "type": "action",
                            "workstream": "l3",
                            "item": "A-3 Ambiguous legacy",
                            "owner": "FDE-C",
                            "status": "closed",
                            "closure_decision": "Done",
                        },
                        {
                            "origin_type": "document",
                            "source": "notes/customer-meeting-summary.md",
                            "type": "action",
                            "item": "A-2 False positive",
                        },
                    ]
                }
            }
            typed = renderer.last_meeting_closure_rows(memory_root, audit, prepass)
            self.assertEqual([row["Item"] for row in typed], ["A-3 Ambiguous legacy"])

            (action_root / "action-ledger.md").write_text(
                "\n".join(
                    [
                        "| Action ID | Status | Owner | Workstream | Action | Source | Closure Criteria |",
                        "| --- | --- | --- | --- | --- | --- | --- |",
                        "| A-2 | closed | FDE-B | l2 | False positive | notes/customer-meeting-summary.md | Done |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            generic_findings = {
                "findings": {
                    "closure": {
                        "open_business_packets": [{"source": "decisions/business-decision-packets/open.md"}],
                        "escalation_candidates": [{"source": "actions/action-ledger.md"}],
                        "unconsumed_intake_files": [{"path": "intake/status-sync/pending.json"}],
                    }
                }
            }
            self.assertEqual(renderer.last_meeting_closure_rows(memory_root, generic_findings), [])

    def test_missing_memory_root_blocks_with_kickoff_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_script(Path(temp_dir), check=False)

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            self.assertIn("adp-project-kickoff", result["recommended_workflows"])

    def test_closed_business_decision_statuses_are_excluded_from_board(self) -> None:
        renderer = load_renderer_module()
        closed_statuses = ["accepted", "closed", "done", "cancelled", "rejected", "superseded"]
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            packet_root = memory_root / "decisions" / "business-decision-packets"
            packet_root.mkdir(parents=True)
            log_rows = []
            for status in [*closed_statuses, "open"]:
                (packet_root / f"packet-{status}.md").write_text(
                    "\n".join(
                        [
                            f"# Business Decision Packet: Packet {status}",
                            "",
                            f"Status: {status.upper()}",
                            "Confirming owner: Biz-A",
                            "",
                            "## Decision Needed",
                            "",
                            f"Packet decision {status}",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                log_rows.append(
                    f"| 2026-07-10 | Business | Log decision {status} | meeting.md | l1-checkout | Biz-A | {status} | decisions/log-{status}.md |"
                )
            (memory_root / "decisions" / "decision-log.md").write_text(
                "\n".join(
                    [
                        "# Decision Log",
                        "",
                        "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
                        "| --- | --- | --- | --- | --- | --- | --- | --- |",
                        *log_rows,
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rows = renderer.business_decision_board(memory_root)

            self.assertEqual({row["Decision"] for row in rows}, {"Packet decision open", "Log decision open"})

    def test_renders_business_biweekly_pack_from_decisions_readiness_and_roadmap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.add_business_biweekly_sources(memory_root)
            prepass_path, audit_path = self.write_gate_inputs(memory_root, "pass")

            result = self.run_script(
                project_root,
                "--scenario",
                "business-biweekly",
                "--date",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"]["business_decisions"], 1)
            self.assertEqual(result["counts"]["roadmap_timeline_items"], 1)
            markdown_path = Path(result["outputs"]["markdown"])
            distillate_path = Path(result["outputs"]["distillate"])
            text = markdown_path.read_text(encoding="utf-8")
            self.assertIn("Decision Board", text)
            self.assertIn("Confirm launch window", text)
            self.assertIn("Option A: launch on the planned window.", text)
            self.assertIn("Payment callback scope changed for Day-1 support.", text)
            self.assertIn("Readiness Board", text)
            self.assertIn("views/acceptance-readiness.md", text)
            self.assertIn("Roadmap / Timeline", text)
            self.assertIn("Checkout validation complete", text)
            self.assertIn("2026-07-15", text)
            self.assertIn("Unscheduled Roadmap Items", text)
            self.assertIn("Theme launch window", text)
            self.assertIn("Last Meeting Closure", text)
            self.assertIn("ACT-20260710-001", text)
            self.assertNotIn("gantt", text.lower())

            distillate = json.loads(distillate_path.read_text(encoding="utf-8"))
            self.assertEqual(len(distillate["boards"]["business_decisions"]), 1)
            self.assertEqual(len(distillate["boards"]["scope_change"]), 1)
            self.assertEqual(distillate["boards"]["scope_change"][0]["Item"], "Payment callback scope changed for Day-1 support.")
            self.assertEqual(len(distillate["boards"]["business_readiness"]), 2)
            self.assertEqual(len(distillate["boards"]["roadmap_timeline"]), 1)
            self.assertTrue(distillate["roadmap"]["dates_visible"])
            self.assertEqual(distillate["roadmap_version"], "2026-07-10T08:00:00+08:00")
            self.assertEqual(
                distillate["next_workflow_payload"]["roadmap_version"],
                "2026-07-10T08:00:00+08:00",
            )

    def test_business_biweekly_uses_explicit_scope_change_board(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.add_business_biweekly_sources(memory_root)
            prepass_path, audit_path = self.write_gate_inputs(memory_root, "pass")
            prepass = json.loads(prepass_path.read_text(encoding="utf-8"))
            prepass["meeting_pack_boards"] = {
                "scope_change": [
                    {
                        "source": "decisions/business-decision-packets/launch-window.md",
                        "workstream": "l1-checkout",
                        "owner": "Biz-A",
                        "type": "decision-packet metadata",
                        "item": "Launch support coverage scope change",
                        "status": "open",
                    }
                ]
            }
            prepass_path.write_text(json.dumps(prepass, indent=2) + "\n", encoding="utf-8")

            result = self.run_script(
                project_root,
                "--scenario",
                "business-biweekly",
                "--date",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            distillate = json.loads(Path(result["outputs"]["distillate"]).read_text(encoding="utf-8"))
            self.assertEqual(
                distillate["boards"]["scope_change"],
                [
                    {
                        "Source": "decisions/business-decision-packets/launch-window.md",
                        "Workstream": "l1-checkout",
                        "Owner": "Biz-A",
                        "Type": "decision-packet metadata",
                        "Item": "Launch support coverage scope change",
                        "Status": "open",
                    }
                ],
            )

    def test_business_biweekly_hides_roadmap_dates_when_audit_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.add_business_biweekly_sources(memory_root)
            prepass_path, audit_path = self.write_gate_inputs(memory_root, "blocked")

            result = self.run_script(
                project_root,
                "--scenario",
                "business-biweekly",
                "--date",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"]["roadmap_timeline_items"], 0)
            self.assertGreaterEqual(result["counts"]["roadmap_unscheduled_items"], 2)
            text = Path(result["outputs"]["markdown"]).read_text(encoding="utf-8")
            self.assertIn("Timeline dates remain hidden", text)
            self.assertIn("Checkout validation complete", text)
            self.assertIn("## Milestone Timeline\n\nNo items.", text)

    def test_fde_window_advances_only_from_previous_archived_recurring_meeting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            meeting_root = memory_root / "meetings"
            meeting_root.mkdir()
            (meeting_root / "2026-07-06-fde-morning.md").write_text(
                "# FDE Morning\n\nDate: 2026-07-06\nType: Project sync\n\n## Meeting Pack Lineage\n\n- scenario: `fde-morning`\n",
                encoding="utf-8",
            )
            prepass_path, audit_path = self.write_gate_inputs(memory_root)

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-08",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            self.assertEqual(result["meeting_window"]["confirmation_mode"], "automatic-from-archive")
            self.assertEqual(result["meeting_window"]["start"], "2026-07-06")
            self.assertEqual(result["meeting_window"]["end"], "2026-07-08")
            text = Path(result["outputs"]["markdown"]).read_text(encoding="utf-8")
            self.assertIn("Changes Since the Previous Cycle", text)
            self.assertNotIn("Workstream Roundtable", text)
            distillate = json.loads(Path(result["outputs"]["distillate"]).read_text(encoding="utf-8"))
            self.assertTrue(distillate["boards"]["fde_period_delta"])

    def test_fde_window_prefers_applied_meeting_cursor_over_unreceipted_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            meeting_root = memory_root / "meetings"
            cursor_root = meeting_root / "cursors"
            receipt_root = meeting_root / "receipts"
            cursor_root.mkdir(parents=True)
            receipt_root.mkdir(parents=True)
            applied_archive = meeting_root / "2026-07-06-fde-morning-applied.md"
            applied_archive.write_text("# Applied meeting\n", encoding="utf-8")
            (meeting_root / "2026-07-07-fde-morning-unreceipted.md").write_text(
                "# Unreceipted meeting\n\nDate: 2026-07-07\nType: FDE morning\n",
                encoding="utf-8",
            )
            receipt = {
                "status": "applied",
                "meeting_instance_id": "mi-cursor-fixture",
                "plan_fingerprint": "sha256:fixture",
            }
            (receipt_root / "mi-cursor-fixture.json").write_text(json.dumps(receipt), encoding="utf-8")
            cursor = {
                "scenario": "fde-morning",
                "meeting_instance_id": "mi-cursor-fixture",
                "meeting_date": "2026-07-06",
                "started_at": "2026-07-06T09:00:00+08:00",
                "ended_at": "2026-07-06T09:20:00+08:00",
                "archive": "meetings/2026-07-06-fde-morning-applied.md",
                "receipt": "meetings/receipts/mi-cursor-fixture.json",
                "plan_fingerprint": "sha256:fixture",
            }
            (cursor_root / "fde-morning.json").write_text(json.dumps(cursor), encoding="utf-8")
            prepass_path, audit_path = self.write_gate_inputs(memory_root)

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-08",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            self.assertEqual(result["meeting_window"]["confirmation_mode"], "automatic-from-archive")
            self.assertEqual(result["meeting_window"]["last_archived_meeting"], "2026-07-06")
            self.assertEqual(result["meeting_window"]["last_archived_meeting_ended_at"], "2026-07-06T09:20:00+08:00")
            self.assertEqual(result["meeting_window"]["last_archived_meeting_path"], "meetings/2026-07-06-fde-morning-applied.md")

    def test_vnext_archive_without_applied_receipt_is_not_a_legacy_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            meeting_root = memory_root / "meetings"
            receipt_root = meeting_root / "receipts"
            receipt_root.mkdir(parents=True)
            (meeting_root / "2026-07-06-fde-morning-partial.md").write_text(
                "\n".join(
                    [
                        "# Partial FDE meeting",
                        "",
                        "Date: 2026-07-06",
                        "Type: FDE morning",
                        "",
                        "- meeting_instance_id: `mi-partial-fixture`",
                        "- plan_fingerprint: `sha256:partial`",
                    ]
                ),
                encoding="utf-8",
            )
            (receipt_root / "mi-partial-fixture.json").write_text(
                json.dumps({"status": "applying", "meeting_instance_id": "mi-partial-fixture", "plan_fingerprint": "sha256:partial"}),
                encoding="utf-8",
            )
            malicious_id = "../../../fake"
            (meeting_root / "2026-07-06-fde-morning-malicious.md").write_text(
                "\n".join(
                    [
                        "# Malicious FDE meeting",
                        "",
                        "Date: 2026-07-06",
                        "Type: FDE morning",
                        f"- meeting_instance_id: `{malicious_id}`",
                        "- plan_fingerprint: `sha256:outside`",
                    ]
                ),
                encoding="utf-8",
            )
            outside_receipt = (receipt_root / f"{malicious_id}.json").resolve()
            outside_receipt.parent.mkdir(parents=True, exist_ok=True)
            outside_receipt.write_text(
                json.dumps({"status": "applied", "meeting_instance_id": malicious_id, "plan_fingerprint": "sha256:outside"}),
                encoding="utf-8",
            )

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-08",
                "--headless",
                check=False,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "needs_confirmation")
            self.assertIsNone(result["meeting_window"]["last_archived_meeting"])

    def test_invalid_cursor_json_shape_returns_needs_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            cursor_path = memory_root / "meetings" / "cursors" / "fde-morning.json"
            cursor_path.parent.mkdir(parents=True)
            cursor_path.write_text("[]\n", encoding="utf-8")

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-08",
                "--headless",
                check=False,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "needs_confirmation")
            self.assertIsNone(result["meeting_window"]["last_archived_meeting"])

    def test_chinese_legacy_archive_can_anchor_fde_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            meeting_root = memory_root / "meetings"
            meeting_root.mkdir()
            (meeting_root / "2026-07-06-fde-morning.md").write_text(
                "# FDE 晨会\n\n日期: 2026-07-06\n类型: FDE 晨会\n",
                encoding="utf-8",
            )
            prepass_path, audit_path = self.write_gate_inputs(memory_root)

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-08",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            self.assertEqual(result["meeting_window"]["confirmation_mode"], "automatic-from-archive")
            self.assertEqual(result["meeting_window"]["start"], "2026-07-06")

    def test_headless_abnormal_fde_window_requires_explicit_period(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            pending = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-10",
                "--headless",
                check=False,
            )

            self.assertFalse(pending["ok"])
            self.assertEqual(pending["status"], "needs_confirmation")
            self.assertIn("no successful adp-meeting-sync", pending["reason"])
            self.assertFalse((memory_root / "views" / "meeting-packs" / "fde-morning").exists())
            self.assertFalse((memory_root / "meetings" / "cursors" / "fde-morning.json").exists())

            prepass_path, audit_path = self.write_gate_inputs(memory_root)
            confirmed = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-10",
                "--headless",
                "--period-start",
                "2026-07-08",
                "--period-end",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )
            self.assertTrue(confirmed["ok"])
            self.assertEqual(confirmed["meeting_window"]["confirmation_mode"], "explicit")

    def test_chinese_business_pack_localizes_system_text_and_preserves_source_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            (project_root / "_bmad" / "adp" / "config.yaml").write_text(
                "communication_language: Chinese\ndocument_output_language: Chinese\nmeeting_pack_item_limit: 10\n",
                encoding="utf-8",
            )
            self.add_business_biweekly_sources(memory_root)
            prepass_path, audit_path = self.write_gate_inputs(memory_root)

            result = self.run_script(
                project_root,
                "--scenario",
                "business-biweekly",
                "--date",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            text = Path(result["outputs"]["markdown"]).read_text(encoding="utf-8")
            self.assertIn("# 业务双周会会议包", text)
            self.assertIn("## 管理层快照", text)
            self.assertIn("## 主要偏差", text)
            self.assertIn("Confirm launch window", text)
            self.assertNotIn("Executive Snapshot", text)
            self.assertNotIn("No items.", text)
            distillate = json.loads(Path(result["outputs"]["distillate"]).read_text(encoding="utf-8"))
            self.assertEqual(distillate["locale"], "zh")
            self.assertEqual(distillate["program_status"]["overall_status"], "at-risk")
            self.assertEqual(distillate["program_status"]["overall_status_label"], "存在风险")

    def test_information_budget_is_stable_and_keeps_omitted_rows_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            (project_root / "_bmad" / "adp" / "config.yaml").write_text(
                "communication_language: English\ndocument_output_language: English\nmeeting_pack_item_limit: 3\n",
                encoding="utf-8",
            )
            self.add_business_biweekly_sources(memory_root)
            packet_root = memory_root / "decisions" / "business-decision-packets"
            for index in range(5):
                (packet_root / f"budget-{index}.md").write_text(
                    "\n".join(
                        [
                            f"# Business Decision Packet: Budget {index}",
                            "",
                            "Status: open",
                            "Confirming owner: Biz-A",
                            "Deadline / trigger: 2026-07-14",
                            "",
                            "## Decision Needed",
                            "",
                            f"Choose budget option {index}",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            prepass_path, audit_path = self.write_gate_inputs(memory_root)

            payloads = []
            for suffix in ["one", "two"]:
                result = self.run_script(
                    project_root,
                    "--scenario",
                    "business-biweekly",
                    "--date",
                    "2026-07-10",
                    "--output-dir",
                    str(project_root / suffix),
                    "--prepass-json",
                    str(prepass_path),
                    "--audit",
                    str(audit_path),
                )
                payloads.append(json.loads(Path(result["outputs"]["distillate"]).read_text(encoding="utf-8")))

            first, second = payloads
            self.assertEqual(len(first["boards"]["business_decisions"]), 3)
            self.assertGreaterEqual(len(first["appendix"]["omitted"]["business_decisions"]), 3)
            self.assertEqual(first["boards"]["business_decisions"], second["boards"]["business_decisions"])
            self.assertEqual(first["appendix"]["omitted"]["business_decisions"], second["appendix"]["omitted"]["business_decisions"])
            self.assertTrue(all(value <= 3 for value in first["information_budget"]["displayed"].values()))

    def test_business_pack_blocks_stale_roadmap_program_status_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.add_business_biweekly_sources(memory_root)
            roadmap_path = memory_root / "views" / "roadmap.json"
            roadmap = json.loads(roadmap_path.read_text(encoding="utf-8"))
            roadmap["program_status"]["snapshot_id"] = "ps-stale"
            roadmap_path.write_text(json.dumps(roadmap, indent=2) + "\n", encoding="utf-8")

            result = self.run_script(
                project_root,
                "--scenario",
                "business-biweekly",
                "--date",
                "2026-07-10",
                check=False,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            self.assertIn("snapshot", result["reason"])
            self.assertEqual(result["recommended_workflows"], ["adp-roadmap-sync"])

    def test_unknown_language_falls_back_to_english_and_discloses_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            (project_root / "_bmad" / "adp" / "config.yaml").write_text(
                "communication_language: English\ndocument_output_language: Klingon\nmeeting_pack_item_limit: 10\n",
                encoding="utf-8",
            )
            prepass_path, audit_path = self.write_gate_inputs(memory_root)

            result = self.run_script(
                project_root,
                "--scenario",
                "fde-morning",
                "--date",
                "2026-07-10",
                "--period-start",
                "2026-07-08",
                "--period-end",
                "2026-07-10",
                "--prepass-json",
                str(prepass_path),
                "--audit",
                str(audit_path),
            )

            self.assertTrue(result["language"]["fallback"])
            self.assertEqual(result["language"]["locale"], "en")
            text = Path(result["outputs"]["markdown"]).read_text(encoding="utf-8")
            self.assertIn("English fallback is active", text)
            distillate = json.loads(Path(result["outputs"]["distillate"]).read_text(encoding="utf-8"))
            self.assertTrue(distillate["language"]["fallback"])


if __name__ == "__main__":
    unittest.main()
