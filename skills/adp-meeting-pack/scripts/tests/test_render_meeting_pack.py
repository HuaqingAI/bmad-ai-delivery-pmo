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
        for rel in ["index.md", "project-charter.md", "cadence.md"]:
            (memory_root / rel).write_text(f"# {rel}\n", encoding="utf-8")
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
        return memory_root

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
            "schema_version": 1,
            "generated_at": "2026-07-10T08:00:00+08:00",
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

            result = self.run_script(project_root, "--scenario", "fde-morning", "--date", "2026-07-10")

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
            self.assertIn("Today's FDE Action Board", text)
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
            self.assertEqual(
                distillate["next_workflow_payload"]["lineage"],
                {
                    "meeting_pack_id": distillate["meeting_pack_id"],
                    "meeting_pack_path": distillate["meeting_pack_path"],
                    "scenario": distillate["scenario"],
                    "audit_path": distillate["audit_path"],
                    "roadmap_version": distillate["roadmap_version"],
                },
            )

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
            self.assertIn("date fields hidden", text)
            self.assertIn("Checkout validation complete", text)
            self.assertNotIn("2026-07-15", text)


if __name__ == "__main__":
    unittest.main()
