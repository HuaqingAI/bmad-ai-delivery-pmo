import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_roadmap.py"
KICKOFF_TEMPLATE_ROOT = SCRIPT.parents[2] / "adp-project-kickoff" / "assets" / "adp-memory-templates"
SCRIPT_GLOBALS = runpy.run_path(str(SCRIPT))
NORMALIZE_DATE_FIELD = SCRIPT_GLOBALS["normalize_date_field"]
ROADMAP_ITEM = SCRIPT_GLOBALS["RoadmapItem"]
DEDUPE_ITEMS = SCRIPT_GLOBALS["dedupe_items"]
CANONICAL_RENDER_SOURCE_INVENTORY = SCRIPT_GLOBALS["canonical_render_source_inventory"]
PARSE_FIRST_TABLE = SCRIPT_GLOBALS["parse_first_table"]


RECORD = """# Workstream Delivery Record

## Identity

- Workstream ID: l1-checkout
- Name: Checkout
- FDE owner: FDE-A
- Business owner: Biz-A
- Current BMM phase: validation
- Current ADP status: gap

## Project Status

- Progress: Validation running
- Blockers: Payment callback owner missing
- Risks: Acceptance date may slip
- Dependencies: l2-payments
- Scope or change notes: TBD
- Next actions: ACT-20260710-001 confirm callback owner
- Last status sync: 2026-07-09

## Cross-Workstream Links

Depends on:

- l2-payments

Impacts:

- l3-settlement

L0 references:

- gate-checkout

### Roadmap

| Milestone | Type | Status | Planned | Forecast | Actual | Owner | Confidence | Depends On | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Checkout validation complete | checkpoint | planned | 2026-07-15 | TBD | TBD | FDE-A | medium | l2-payments | workstreams/l1-checkout/delivery-record.md#roadmap |
| Theme launch window | delivery-window | planned | TBD | TBD | TBD | FDE-A | low | TBD | workstreams/l1-checkout/delivery-record.md#roadmap |
| Unsourced launch guess | delivery-window | planned | 2026-07-20 | TBD | TBD | FDE-A | low | TBD | TBD |
"""


class RenderRoadmapTests(unittest.TestCase):
    def test_date_normalization_requires_complete_iso_value(self) -> None:
        cases = [
            ("2026-07-15", ("2026-07-15", "")),
            ("2026-07-15T10:00:00+08:00", ("2026-07-15", "")),
            ("not before 2026-07-15", ("TBD", "unparseable date left as TBD: not before 2026-07-15")),
            ("week of 2026-07-15", ("TBD", "unparseable date left as TBD: week of 2026-07-15")),
            ("2026/07/15", ("TBD", "unparseable date left as TBD: 2026/07/15")),
            ("2026-07-15T10:00:00", ("TBD", "unparseable date left as TBD: 2026-07-15T10:00:00")),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(NORMALIZE_DATE_FIELD(value), expected)

    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def scaffold(self, project_root: Path) -> Path:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        for rel in [
            "actions",
            "decisions/business-decision-packets",
            "intake/bmm-checkpoints/candidates",
            "l0",
            "views",
            "workstreams/l1-checkout",
        ]:
            (memory_root / rel).mkdir(parents=True, exist_ok=True)
        (memory_root / "workstreams" / "l1-checkout" / "delivery-record.md").write_text(RECORD, encoding="utf-8")
        (memory_root / "actions" / "action-ledger.md").write_text(
            "\n".join(
                [
                    "# Action Ledger",
                    "",
                    "| Action ID | Status | Owner | Workstream | Affected Workstreams | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| ACT-20260710-001 | open | FDE-A | l1-checkout | l1-checkout | Confirm callback owner | meetings/2026-07-10.md#A1 | Meeting follow-up | 2026-07-12 | Owner confirmed | 2026-07-10 | adp-status-sync |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "decisions" / "decision-log.md").write_text(
            "\n".join(
                [
                    "# Decision Log",
                    "",
                    "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| 2026-07-08 | Business | Payment signoff owner confirmed | meetings/2026-07-08.md | l1-checkout | Biz-A | closed | decisions/decision-log.md#payment |",
                    "| TBD | Business | Confirm launch window | meetings/2026-07-09.md | l1-checkout | Biz-A | open | decisions/business-decision-packets/launch-window.md |",
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
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        candidate = {
            "candidate_id": "CHK-L1-VALIDATION",
            "status": "applied",
            "workstream_id": "l1-checkout",
            "checkpoint": "validation",
            "applied_at": "2026-07-09T10:00:00+08:00",
            "claims": {"summary": "Validation evidence baseline synced"},
            "authority": {"asserted_by": "FDE-A"},
        }
        (memory_root / "intake" / "bmm-checkpoints" / "candidates" / "CHK-L1-VALIDATION.json").write_text(
            json.dumps(candidate, indent=2) + "\n",
            encoding="utf-8",
        )
        (memory_root / "views" / "acceptance-readiness.md").write_text(
            "\n".join(
                [
                    "# Acceptance",
                    "",
                    "This view discusses an evidence gap without using prose as status.",
                    "",
                    "| Workstream | Readiness Score | Missing Evidence | Unclosed Criteria | Business Confirmation | Status | Roadmap Status |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                    "| l1-checkout | 60 | callback evidence | TBD | pending | criteria-defined | planned |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "l0" / "extracted-gates.md").write_text(
            "\n".join(
                [
                    "# Gates",
                    "",
                    "| Gate | Meaning | Required Evidence | Owner | Affected Workstreams | Status |",
                    "| --- | --- | --- | --- | --- | --- |",
                    "| gate-checkout | Checkout validation | callback evidence | FDE-A | l1-checkout | open |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return memory_root

    def write_audit(
        self,
        memory_root: Path,
        *,
        status: str = "pass",
        schema_version: int = 1,
        scenario: str = "roadmap",
        generated_at: str | None = None,
        audit_memory_root: Path | None = None,
        scope_workstreams: list[str] | None = None,
        inventory_workstreams: list[str] | None = None,
        as_of: str | None = None,
    ) -> Path:
        audit_root = memory_root / "audits"
        audit_root.mkdir(parents=True, exist_ok=True)
        audit_path = audit_root / f"fixture-{status}.json"
        selected = set(scope_workstreams or [])
        inventory = CANONICAL_RENDER_SOURCE_INVENTORY(memory_root, selected)
        sources_read = [
            {key: item[key] for key in ["path", "bytes", "modified", "modified_ns"]}
            for item in inventory.values()
            if item["status"] == "read"
        ]
        missing_sources = [item["path"] for item in inventory.values() if item["status"] == "missing"]
        payload = {
            "ok": True,
            "audit_schema_version": schema_version,
            "schema_version": schema_version,
            "prepass_schema_version": 2,
            "audit_status": status,
            "safe_to_generate": True,
            "safe_to_generate_green_report": status == "pass",
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scenario": scenario,
            "memory_root": str(audit_memory_root or memory_root),
            "report_confidence": {"pass": "high", "warning": "medium", "blocked": "low"}.get(status, "low"),
            "prepass": {
                "schema_version": 2,
                "capability": "Global Project Readout",
                "scope": {
                    "workstreams_requested": scope_workstreams or [],
                    "groups_scanned": ["actions", "checkpoints", "core", "decisions", "l0", "views", "workstreams"],
                    "as_of": as_of or date.today().isoformat(),
                    "max_age_days": 7,
                },
                "counts": {
                    "sources_read": len(sources_read),
                    "workstreams": len(inventory_workstreams or []),
                },
            },
            "source_inventory": {
                "sources_read": sources_read,
                "missing_sources": missing_sources,
                "workstreams": inventory_workstreams
                if inventory_workstreams is not None
                else sorted(path.parent.name for path in memory_root.glob("workstreams/*/delivery-record.md")),
            },
            "source_inventory_items": [
                {
                    "path": item["path"],
                    "kind": "fixture",
                    "modified": item["modified"],
                    "status": item["status"],
                }
                for item in inventory.values()
            ],
            "recommended_workflows": [],
        }
        audit_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return audit_path

    def run_with_pass_audit(
        self,
        project_root: Path,
        memory_root: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        scope_workstreams = [args[index + 1] for index, value in enumerate(args) if value == "--workstream"]
        as_of = next(
            (
                args[index + 1]
                for index, value in enumerate(args)
                if value in {"--date", "--as-of"}
            ),
            date.today().isoformat(),
        )
        available = sorted(path.parent.name for path in memory_root.glob("workstreams/*/delivery-record.md"))
        audit_path = self.write_audit(
            memory_root,
            scope_workstreams=scope_workstreams,
            inventory_workstreams=scope_workstreams or available,
            as_of=as_of,
        )
        return self.run_script(project_root, "--audit", str(audit_path), *args, check=check)

    def test_writes_roadmap_and_keeps_action_due_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_with_pass_audit(project_root, memory_root, "--date", "2026-07-10")
            result = json.loads(completed.stdout)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertTrue((memory_root / "views" / "roadmap.md").exists())
            timeline_names = {item["milestone"] for item in roadmap["milestone_timeline"]}
            unscheduled_names = {item["milestone"] for item in roadmap["unscheduled_milestones"]}
            excluded_items = {item["item"] for item in roadmap["excluded_items"]}

            self.assertIn("Checkout validation complete", timeline_names)
            self.assertIn("Payment signoff owner confirmed", timeline_names)
            self.assertIn("Validation checkpoint: Validation evidence baseline synced", timeline_names)
            self.assertIn("Theme launch window", unscheduled_names)
            self.assertIn("Unsourced launch guess", excluded_items)
            self.assertIn("Confirm callback owner", excluded_items)
            self.assertTrue(roadmap["blocked_by_decisions"])
            self.assertNotIn("blocked_by_dependencies", roadmap)
            self.assertTrue(
                any(item["source_type"] == "wdr-project-status" for item in roadmap["excluded_items"])
            )
            checkpoint = next(
                item
                for item in roadmap["milestone_timeline"]
                if item["source_type"] == "checkpoint-candidate"
            )
            decision = next(
                item for item in roadmap["milestone_timeline"] if item["source_type"] == "decision-log"
            )
            self.assertEqual(checkpoint["confidence"], "TBD")
            self.assertEqual(decision["confidence"], "TBD")
            markdown = (memory_root / "views" / "roadmap.md").read_text(encoding="utf-8")
            self.assertIn("Source Type", markdown)
            self.assertIn("wdr-roadmap", markdown)

    def test_unknown_workstream_blocks_with_available_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_with_pass_audit(
                project_root,
                memory_root,
                "--workstream",
                "l9-missing",
                check=False,
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("l9-missing", result["error"])
            self.assertIn("l1-checkout", result["error"])
            self.assertFalse((memory_root / "views" / "roadmap.json").exists())

    def test_readiness_prose_gap_does_not_set_at_risk_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            self.run_with_pass_audit(project_root, memory_root)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
            readiness_items = [
                item
                for item in roadmap["unscheduled_milestones"]
                if item["source_type"] == "readiness-gate" and item["source"] == "views/acceptance-readiness.md"
            ]

            self.assertEqual(len(readiness_items), 1)
            self.assertEqual(readiness_items[0]["status"], "planned")
            self.assertEqual(readiness_items[0]["confidence"], "TBD")
            self.assertEqual(readiness_items[0]["workstreams"], ["l1-checkout"])

    def test_mixed_readiness_without_canonical_status_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            (memory_root / "views" / "acceptance-readiness.md").write_text(
                "\n".join(
                    [
                        "# Acceptance",
                        "",
                        "| Workstream | Status |",
                        "| --- | --- |",
                        "| l1-checkout | confirmed |",
                        "| l2-payments | confirmation-pending |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_with_pass_audit(project_root, memory_root)
            result = json.loads(completed.stdout)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
            readiness_items = [
                item for item in roadmap["unscheduled_milestones"] if item["source_type"] == "readiness-gate"
            ]
            exclusions = [
                item
                for item in roadmap["excluded_items"]
                if item["source"] == "views/acceptance-readiness.md"
            ]

            self.assertFalse(readiness_items)
            self.assertEqual(len(exclusions), 1)
            self.assertEqual(exclusions[0]["code"], "table_schema_error")
            self.assertIn("missing required header 'roadmap status'", exclusions[0]["reason"])
            self.assertTrue(any("missing required header 'roadmap status'" in warning for warning in result["warnings"]))

    def test_readiness_preserves_row_level_status_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            second_record = memory_root / "workstreams" / "l2-payments" / "delivery-record.md"
            second_record.parent.mkdir(parents=True, exist_ok=True)
            second_record.write_text(
                "# Workstream Delivery Record\n\n## Identity\n\n- Workstream ID: l2-payments\n",
                encoding="utf-8",
            )
            (memory_root / "views" / "acceptance-readiness.md").write_text(
                "\n".join(
                    [
                        "# Acceptance",
                        "",
                        "| Workstream | Status | Roadmap Status |",
                        "| --- | --- | --- |",
                        "| l1-checkout | confirmed | done |",
                        "| l2-payments | confirmation-pending | at-risk |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.run_with_pass_audit(project_root, memory_root)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
            readiness_items = [
                item for item in roadmap["unscheduled_milestones"] if item["source_type"] == "readiness-gate"
            ]

            self.assertEqual(len(readiness_items), 2)
            self.assertEqual(
                {(item["workstreams"][0], item["status"]) for item in readiness_items},
                {("l1-checkout", "done"), ("l2-payments", "at-risk")},
            )

    def test_invalid_wdr_roadmap_enums_are_excluded_with_diagnostics(self) -> None:
        valid_row = (
            "| Checkout validation complete | checkpoint | planned | 2026-07-15 | TBD | TBD | "
            "FDE-A | medium | l2-payments | workstreams/l1-checkout/delivery-record.md#roadmap |"
        )
        cases = [
            ("type", valid_row.replace("| checkpoint |", "| release gate |"), "invalid Type enum 'release gate'"),
            ("status", valid_row.replace("| planned |", "| delayed |"), "invalid Status enum 'delayed'"),
            ("confidence", valid_row.replace("| medium |", "| certain |"), "invalid Confidence enum 'certain'"),
        ]
        for label, invalid_row, expected_reason in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                record_path = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
                record_path.write_text(RECORD.replace(valid_row, invalid_row), encoding="utf-8")

                completed = self.run_with_pass_audit(project_root, memory_root)
                result = json.loads(completed.stdout)
                roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
                rendered = roadmap["milestone_timeline"] + roadmap["unscheduled_milestones"]
                exclusions = [
                    item for item in roadmap["excluded_items"] if item["item"] == "Checkout validation complete"
                ]

                self.assertNotIn("Checkout validation complete", {item["milestone"] for item in rendered})
                self.assertEqual(len(exclusions), 1)
                self.assertEqual(exclusions[0]["code"], "invalid_enum")
                self.assertTrue(exclusions[0]["risk"])
                self.assertTrue(roadmap["risk_bearing"])
                self.assertEqual(result["status"], "warning")
                self.assertIn(expected_reason, exclusions[0]["reason"])
                self.assertTrue(any(expected_reason in warning for warning in result["warnings"]))

    def test_terminal_decision_statuses_never_block_roadmap(self) -> None:
        for status in ["accepted", "closed", "done", "cancelled", "rejected", "superseded"]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                (memory_root / "decisions" / "decision-log.md").write_text(
                    "\n".join(
                        [
                            "# Decision Log",
                            "",
                            "| Date | Type | Decision / Question | Source | Affected Workstreams | Confirmer | Status | Link |",
                            "| --- | --- | --- | --- | --- | --- | --- | --- |",
                            f"| 2026-07-10 | Business | Terminal decision | meetings/source.md | l1-checkout | Biz-A | {status} | decisions/decision-log.md#terminal |",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (memory_root / "decisions" / "business-decision-packets" / "launch-window.md").write_text(
                    f"# Launch Window\n\nStatus: {status}\nConfirming owner: Biz-A\n",
                    encoding="utf-8",
                )

                self.run_with_pass_audit(project_root, memory_root)
                roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

                self.assertFalse(roadmap["blocked_by_decisions"])

    def test_fresh_kickoff_templates_create_no_milestones(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "_bmad-output" / "adp" / "memory"
            for rel in ["views", "l0"]:
                (memory_root / rel).mkdir(parents=True, exist_ok=True)
            for rel in [
                "views/acceptance-readiness.md",
                "l0/extracted-gates.md",
                "l0/extracted-decision-gates.md",
            ]:
                (memory_root / rel).write_text((KICKOFF_TEMPLATE_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")

            self.run_with_pass_audit(project_root, memory_root)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertFalse(roadmap["milestone_timeline"])
            self.assertFalse(roadmap["unscheduled_milestones"])

    def test_audit_status_controls_report_risk_state(self) -> None:
        expected_status = {"pass": "complete", "warning": "warning", "blocked": "blocked"}
        for audit_status in ["pass", "warning", "blocked"]:
            with self.subTest(audit_status=audit_status), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                record_path = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
                record_path.write_text(
                    RECORD.replace(
                        "| Unsourced launch guess | delivery-window | planned | 2026-07-20 | TBD | TBD | FDE-A | low | TBD | TBD |\n",
                        "",
                    ),
                    encoding="utf-8",
                )
                audit_path = self.write_audit(memory_root, status=audit_status)

                completed = self.run_script(project_root, "--audit", str(audit_path))
                result = json.loads(completed.stdout)
                roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
                markdown = (memory_root / "views" / "roadmap.md").read_text(encoding="utf-8")

                self.assertEqual(result["status"], expected_status[audit_status])
                self.assertEqual(roadmap["audit_status"], audit_status)
                self.assertEqual(roadmap["risk_bearing"], audit_status != "pass")
                self.assertEqual(roadmap["audit_path"], str(audit_path.resolve()))
                self.assertEqual("RISK-BEARING ROADMAP" in markdown, audit_status != "pass")

    def test_invalid_stale_and_wrong_root_audits_block_rendering(self) -> None:
        cases = [
            ("invalid schema", {"schema_version": 2}, "schema version"),
            ("wrong scenario", {"scenario": "global"}, "scenario"),
            ("wrong memory root", {"audit_memory_root": Path("C:/wrong-memory")}, "memory_root"),
            (
                "stale",
                {"generated_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")},
                "stale",
            ),
        ]
        for label, overrides, expected_error in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                audit_path = self.write_audit(memory_root, **overrides)

                completed = self.run_script(project_root, "--audit", str(audit_path), check=False)
                result = json.loads(completed.stdout)

                self.assertEqual(completed.returncode, 1)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "blocked")
                self.assertIn(expected_error, result["error"])

    def test_audit_scope_date_inventory_and_confidence_must_match(self) -> None:
        cases = [
            (
                "scope",
                {"scope_workstreams": ["l2-payments"], "inventory_workstreams": ["l1-checkout"]},
                None,
                "workstreams_requested",
            ),
            (
                "as of",
                {"as_of": "2026-07-09"},
                None,
                "scope.as_of",
            ),
            (
                "inventory",
                {"inventory_workstreams": []},
                None,
                "source_inventory.workstreams",
            ),
            (
                "missing confidence",
                {},
                "report_confidence",
                "report_confidence must explicitly",
            ),
            (
                "missing prepass scope",
                {},
                "prepass",
                "prepass must be an object",
            ),
            (
                "missing source inventory",
                {},
                "source_inventory",
                "source_inventory must be an object",
            ),
        ]
        for label, audit_args, field_to_remove, expected_error in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                audit_options = {"as_of": "2026-07-10", **audit_args}
                audit_path = self.write_audit(memory_root, **audit_options)
                if field_to_remove:
                    payload = json.loads(audit_path.read_text(encoding="utf-8"))
                    payload.pop(field_to_remove)
                    audit_path.write_text(json.dumps(payload), encoding="utf-8")

                completed = self.run_script(
                    project_root,
                    "--audit",
                    str(audit_path),
                    "--date",
                    "2026-07-10",
                    check=False,
                )
                result = json.loads(completed.stdout)

                self.assertEqual(completed.returncode, 1)
                self.assertEqual(result["status"], "blocked")
                self.assertIn(expected_error, result["error"])

    def test_scoped_render_uses_qualified_path_and_filters_gate_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.run_with_pass_audit(project_root, memory_root, "--date", "2026-07-10")
            canonical_path = memory_root / "views" / "roadmap.json"
            canonical_before = canonical_path.read_text(encoding="utf-8")
            readiness_path = memory_root / "views" / "acceptance-readiness.md"
            readiness_path.write_text(
                readiness_path.read_text(encoding="utf-8")
                + "| l2-payments | 10 | missing | open | pending | unknown | mystery |\n",
                encoding="utf-8",
            )

            completed = self.run_with_pass_audit(
                project_root,
                memory_root,
                "--date",
                "2026-07-10",
                "--workstream",
                "l1-checkout",
            )
            result = json.loads(completed.stdout)
            scoped_path = memory_root / "views" / "roadmaps" / "l1-checkout" / "roadmap.json"
            roadmap = json.loads(scoped_path.read_text(encoding="utf-8"))

            self.assertEqual(Path(result["outputs"]["json"]), scoped_path)
            self.assertEqual(canonical_path.read_text(encoding="utf-8"), canonical_before)
            self.assertEqual(roadmap["scope"]["selected_workstreams"], ["l1-checkout"])
            self.assertTrue(
                any(item["source_type"] == "readiness-gate" for item in roadmap["unscheduled_milestones"])
            )
            self.assertTrue(
                any(item["source_type"] == "l0-gate" for item in roadmap["unscheduled_milestones"])
            )
            l0_item = next(
                item for item in roadmap["unscheduled_milestones"] if item["source_type"] == "l0-gate"
            )
            self.assertEqual(l0_item["status"], "planned")
            self.assertEqual(l0_item["confidence"], "TBD")
            self.assertEqual(l0_item["workstreams"], ["l1-checkout"])
            self.assertFalse(any("mystery" in warning for warning in result["warnings"]))
            self.assertFalse(
                any("l2-payments" in item.get("workstreams", []) for item in roadmap["excluded_items"])
            )
            self.assertFalse(roadmap["changed_since_last_roadmap"])

    def test_dry_run_returns_complete_preview_and_would_write_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_with_pass_audit(
                project_root,
                memory_root,
                "--date",
                "2026-07-10",
                "--dry-run",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["dry_run"])
            self.assertNotIn("outputs", result)
            self.assertIn("would_write", result)
            self.assertIn("roadmap", result["preview"])
            self.assertIn("markdown", result["preview"])
            self.assertIn("milestone_timeline", result["preview"]["roadmap"])
            self.assertIn("# ADP Roadmap", result["preview"]["markdown"])
            self.assertFalse((memory_root / "views" / "roadmap.json").exists())
            self.assertFalse((memory_root / "views" / "roadmap.md").exists())

    def test_unknown_decision_states_are_excluded_not_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            (memory_root / "decisions" / "decision-log.md").write_text(
                "# Decisions\n\n"
                "| Decision / Question | Affected Workstreams | Status | Link |\n"
                "| --- | --- | --- | --- |\n"
                "| Missing state | l1-checkout | TBD | decisions/decision-log.md#missing |\n"
                "| Unknown state | l1-checkout | waiting | decisions/decision-log.md#unknown |\n",
                encoding="utf-8",
            )
            (memory_root / "decisions" / "business-decision-packets" / "launch-window.md").write_text(
                "# Launch Window\n\nAffected workstreams: l1-checkout\n",
                encoding="utf-8",
            )

            self.run_with_pass_audit(project_root, memory_root)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertFalse(roadmap["blocked_by_decisions"])
            decision_exclusions = [
                item
                for item in roadmap["excluded_items"]
                if item["source_type"] in {"decision-log", "business-decision-packet"}
            ]
            self.assertEqual(len(decision_exclusions), 3)
            self.assertTrue(all("must explicitly be open" in item["reason"] for item in decision_exclusions))

    def test_negated_blocker_prose_is_never_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            record_path = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record_path.write_text(
                RECORD.replace("Payment callback owner missing", "No blockers").replace(
                    "- Dependencies: l2-payments",
                    "- Dependencies: not blocked",
                ),
                encoding="utf-8",
            )

            self.run_with_pass_audit(project_root, memory_root)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertNotIn("blocked_by_dependencies", roadmap)
            context = [item for item in roadmap["excluded_items"] if item["source_type"] == "wdr-project-status"]
            self.assertEqual(len(context), 2)
            self.assertTrue(any("No blockers" in item["item"] for item in context))
            self.assertTrue(any("not blocked" in item["item"] for item in context))

    def test_malformed_candidates_are_visible_and_make_preview_risk_bearing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            candidates = memory_root / "intake" / "bmm-checkpoints" / "candidates"
            (candidates / "CHK-L1-VALIDATION.json").write_text("{bad json", encoding="utf-8")
            (candidates / "CHK-NONOBJECT.json").write_text("[]", encoding="utf-8")
            (candidates / "CHK-UNKNOWN.json").write_text(
                json.dumps(
                    {
                        "candidate_id": "CHK-UNKNOWN",
                        "status": "pending",
                        "workstream_id": "l1-checkout",
                    }
                ),
                encoding="utf-8",
            )

            completed = self.run_with_pass_audit(project_root, memory_root)
            result = json.loads(completed.stdout)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
            candidate_exclusions = [
                item for item in roadmap["excluded_items"] if item["source_type"] == "checkpoint-candidate"
            ]

            self.assertEqual(len(candidate_exclusions), 3)
            self.assertTrue(roadmap["risk_bearing"])
            self.assertTrue(result["risk_bearing"])
            self.assertEqual(result["status"], "warning")
            self.assertTrue(any("malformed candidate JSON" in warning for warning in result["warnings"]))

    def test_scoped_malformed_candidate_remains_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            candidate_path = (
                memory_root
                / "intake"
                / "bmm-checkpoints"
                / "candidates"
                / "CHK-UNREADABLE.json"
            )
            candidate_path.write_text("{bad json", encoding="utf-8")

            completed = self.run_with_pass_audit(
                project_root,
                memory_root,
                "--workstream",
                "l1-checkout",
            )
            result = json.loads(completed.stdout)
            roadmap = result["preview"]["roadmap"] if result["dry_run"] else json.loads(
                Path(result["outputs"]["json"]).read_text(encoding="utf-8")
            )

            self.assertTrue(result["risk_bearing"])
            self.assertTrue(
                any(
                    item["source"].endswith("CHK-UNREADABLE.json")
                    and "malformed candidate JSON" in item["reason"]
                    for item in roadmap["excluded_items"]
                )
            )

    def test_l0_rows_require_explicit_mapped_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            (memory_root / "l0" / "extracted-gates.md").write_text(
                "# Gates\n\n"
                "| Gate | Owner | Affected Workstreams | Status |\n"
                "| --- | --- | --- | --- |\n"
                "| blocked-gate | FDE-A | l1-checkout | blocked |\n"
                "| mystery-gate | FDE-A | l1-checkout | waiting |\n",
                encoding="utf-8",
            )

            self.run_with_pass_audit(project_root, memory_root)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))
            l0_items = [
                item for item in roadmap["unscheduled_milestones"] if item["source_type"] == "l0-gate"
            ]
            l0_exclusions = [item for item in roadmap["excluded_items"] if item["source_type"] == "l0-gate"]

            self.assertEqual([(item["milestone"], item["status"]) for item in l0_items], [("blocked-gate", "blocked")])
            self.assertEqual(len(l0_exclusions), 1)
            self.assertIn("invalid L0 Status enum", l0_exclusions[0]["reason"])

    def test_malformed_markdown_row_is_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            record_path = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record_path.write_text(RECORD + "| too | few | cells |\n", encoding="utf-8")

            completed = self.run_with_pass_audit(project_root, memory_root)
            result = json.loads(completed.stdout)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertTrue(
                any("malformed markdown table" in item["reason"] for item in roadmap["excluded_items"])
            )
            self.assertTrue(roadmap["risk_bearing"])
            self.assertTrue(any("expected 10" in warning for warning in result["warnings"]))

    def test_corrupt_previous_roadmap_omits_diff_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.run_with_pass_audit(project_root, memory_root)
            roadmap_path = memory_root / "views" / "roadmap.json"
            roadmap_path.write_text("[]", encoding="utf-8")

            completed = self.run_with_pass_audit(project_root, memory_root)
            result = json.loads(completed.stdout)
            roadmap = json.loads(roadmap_path.read_text(encoding="utf-8"))

            self.assertFalse(roadmap["changed_since_last_roadmap"])
            self.assertTrue(any("root is not an object" in warning for warning in result["warnings"]))

    def test_diff_is_omitted_when_explicit_output_reuses_another_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.run_with_pass_audit(project_root, memory_root, "--output-dir", "views/shared")

            completed = self.run_with_pass_audit(
                project_root,
                memory_root,
                "--workstream",
                "l1-checkout",
                "--output-dir",
                "views/shared",
            )
            result = json.loads(completed.stdout)
            roadmap = json.loads((memory_root / "views" / "shared" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertFalse(roadmap["changed_since_last_roadmap"])
            self.assertTrue(any("scope does not match" in warning for warning in result["warnings"]))

    def test_conflicting_duplicate_facts_are_rejected(self) -> None:
        common = {
            "id": "RM-CONFLICT",
            "milestone": "Conflicting milestone",
            "type": "checkpoint",
            "planned": "2026-07-15",
            "forecast": "TBD",
            "actual": "TBD",
            "owner": "FDE-A",
            "confidence": "medium",
            "depends_on": "TBD",
            "source": "workstreams/l1-checkout/delivery-record.md#roadmap",
            "source_type": "wdr-roadmap",
            "workstreams": ["l1-checkout"],
        }
        rendered, excluded = DEDUPE_ITEMS(
            [
                ROADMAP_ITEM(status="planned", **common),
                ROADMAP_ITEM(status="blocked", **common),
            ]
        )

        self.assertFalse(rendered)
        self.assertEqual(len(excluded), 1)
        self.assertIn("status", excluded[0].reason)

    def test_missing_audit_runs_roadmap_state_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_script(project_root, "--date", "2026-07-10")
            result = json.loads(completed.stdout)
            roadmap = json.loads((memory_root / "views" / "roadmap.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["audit_path"]).exists())
            self.assertEqual(roadmap["audit_status"], result["audit_status"])
            self.assertEqual(roadmap["audit_path"], result["audit_path"])

    def test_generated_audit_matches_scoped_dry_run_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_script(
                project_root,
                "--workstream",
                "l1-checkout",
                "--date",
                "2026-07-10",
                "--dry-run",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(Path(result["audit_path"]).exists())
            self.assertEqual(
                result["preview"]["roadmap"]["scope"]["selected_workstreams"],
                ["l1-checkout"],
            )
            self.assertIn("views\\roadmaps\\l1-checkout", result["would_write"]["json"])
            self.assertFalse((memory_root / "views" / "roadmap.json").exists())

    def test_audit_requires_complete_schema_and_current_source_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            audit_path = self.write_audit(memory_root)
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
            del payload["safe_to_generate_green_report"]
            audit_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

            incomplete = self.run_script(project_root, "--audit", str(audit_path), check=False)
            self.assertIn("safe_to_generate_green_report must be a boolean", json.loads(incomplete.stdout)["error"])

            audit_path = self.write_audit(memory_root)
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.write_text(record.read_text(encoding="utf-8") + "\nchanged after audit\n", encoding="utf-8")
            stale = self.run_script(project_root, "--audit", str(audit_path), check=False)
            self.assertIn("render source inventory does not match", json.loads(stale.stdout)["error"])

    def test_supplied_prepass_is_identity_bound_to_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            audit_path = self.write_audit(memory_root)
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            prepass = {
                "ok": True,
                "schema_version": 2,
                "capability": audit["prepass"]["capability"],
                "memory_root": audit["memory_root"],
                "scope": audit["prepass"]["scope"],
                "counts": audit["prepass"]["counts"],
                "sources_read": audit["source_inventory"]["sources_read"],
                "missing_sources": audit["source_inventory"]["missing_sources"],
            }
            prepass_path = project_root / "prepass.json"
            prepass_path.write_text(json.dumps(prepass, indent=2) + "\n", encoding="utf-8")
            valid = self.run_script(
                project_root, "--audit", str(audit_path), "--prepass-json", str(prepass_path), "--dry-run"
            )
            self.assertTrue(json.loads(valid.stdout)["ok"])

            prepass["counts"] = {**prepass["counts"], "sources_read": 999}
            prepass_path.write_text(json.dumps(prepass, indent=2) + "\n", encoding="utf-8")
            mismatch = self.run_script(
                project_root,
                "--audit",
                str(audit_path),
                "--prepass-json",
                str(prepass_path),
                "--dry-run",
                check=False,
            )
            self.assertIn("prepass counts do not match", json.loads(mismatch.stdout)["error"])

    def test_scoped_shared_table_failures_propagate_to_risk(self) -> None:
        cases = [
            ("decisions/decision-log.md", "# Decisions\n\n| Status | Status |\n| --- | --- |\n| open | closed |\n"),
            ("views/acceptance-readiness.md", "# Acceptance\n\n| Workstream | Roadmap Status |\n| broken |\n"),
            ("l0/extracted-gates.md", "# Gates\n\n| Gate | Status |\n| --- | --- |\n| gate | open |\n"),
            ("actions/action-ledger.md", "# Actions\n\n| Status | Action |\n| --- |\n| open | follow up |\n"),
        ]
        for rel, content in cases:
            with self.subTest(source=rel), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                path = memory_root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                completed = self.run_with_pass_audit(
                    project_root, memory_root, "--workstream", "l1-checkout", "--dry-run"
                )
                roadmap = json.loads(completed.stdout)["preview"]["roadmap"]
                failures = [item for item in roadmap["excluded_items"] if item["source"] == rel and item["risk"]]
                self.assertTrue(failures)
                self.assertTrue(roadmap["risk_bearing"])
                self.assertIn(rel, {item["path"] for item in roadmap["source_inventory"]["sources_read"]})

    def test_checkpoint_candidates_require_explicit_semantic_identity(self) -> None:
        malformed_candidates = [
            {"candidate_id": "CHK-MISSING", "status": "confirmed", "confidence": "high"},
            {
                "candidate_id": "CHK-CLAIMS-LIST",
                "status": "confirmed",
                "confidence": "high",
                "workstream_id": "l1-checkout",
                "checkpoint": "validation",
                "claims": [],
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            candidates = memory_root / "intake" / "bmm-checkpoints" / "candidates"
            for index, candidate in enumerate(malformed_candidates):
                (candidates / f"CHK-MALFORMED-{index}.json").write_text(
                    json.dumps(candidate) + "\n", encoding="utf-8"
                )
            completed = self.run_with_pass_audit(project_root, memory_root, "--dry-run")
            roadmap = json.loads(completed.stdout)["preview"]["roadmap"]
            exclusions = [
                item for item in roadmap["excluded_items"] if item["item"] in {"CHK-MISSING", "CHK-CLAIMS-LIST"}
            ]
            self.assertEqual(len(exclusions), 2)
            self.assertTrue(all(item["risk"] for item in exclusions))
            self.assertFalse(
                any("checkpoint checkpoint" in item["milestone"].lower() for item in roadmap["unscheduled_milestones"])
            )

    def test_placeholder_workstreams_are_excluded_not_promoted(self) -> None:
        placeholders = ["TBD", "unknown", "none", "n/a", "na", "todo"]
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            rows = "\n".join(f"| {value} | planned | high |" for value in placeholders)
            (memory_root / "views" / "acceptance-readiness.md").write_text(
                "# Acceptance\n\n| Workstream | Roadmap Status | Confidence |\n"
                "| --- | --- | --- |\n"
                f"{rows}\n",
                encoding="utf-8",
            )
            completed = self.run_with_pass_audit(project_root, memory_root, "--dry-run")
            roadmap = json.loads(completed.stdout)["preview"]["roadmap"]
            readiness_items = [
                item for item in roadmap["unscheduled_milestones"] if item["source_type"] == "readiness-gate"
            ]
            exclusions = [item for item in roadmap["excluded_items"] if item["code"] == "invalid_workstream"]
            self.assertFalse(readiness_items)
            self.assertEqual(len(exclusions), len(placeholders))

    def test_markdown_parser_rejects_conflicting_headers_and_stops_after_first_table(self) -> None:
        diagnostics: list[str] = []
        rows = PARSE_FIRST_TABLE(
            ["| Item | Status | Status |", "| --- | --- | --- |", "| gate | planned | blocked |"],
            diagnostics,
        )
        self.assertFalse(rows)
        self.assertIn("duplicate headers: status", diagnostics[0])

        diagnostics = []
        rows = PARSE_FIRST_TABLE(
            [
                "| Item | Status |",
                "| --- | --- |",
                "| first | planned |",
                "",
                "| Other | Value | Extra |",
                "| --- | --- | --- |",
                "| second | ignored | ignored |",
            ],
            diagnostics,
        )
        self.assertEqual(rows, [{"item": "first", "status": "planned"}])
        self.assertFalse(diagnostics)

    def test_previous_roadmap_rejects_foreign_provenance_and_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            self.run_with_pass_audit(project_root, memory_root)
            roadmap_path = memory_root / "views" / "roadmap.json"
            previous = json.loads(roadmap_path.read_text(encoding="utf-8"))
            duplicate = dict(previous["milestone_timeline"][0])
            duplicate["status"] = "blocked"
            previous["milestone_timeline"].append(duplicate)
            roadmap_path.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
            duplicate_result = self.run_with_pass_audit(project_root, memory_root)
            self.assertTrue(
                any("duplicate item id" in warning for warning in json.loads(duplicate_result.stdout)["warnings"])
            )

            previous = json.loads(roadmap_path.read_text(encoding="utf-8"))
            previous["project_root"] = str(project_root / "foreign")
            roadmap_path.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
            foreign_result = self.run_with_pass_audit(project_root, memory_root)
            self.assertTrue(
                any("project_root provenance" in warning for warning in json.loads(foreign_result.stdout)["warnings"])
            )

    def test_missing_memory_root_blocks_with_kickoff_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self.run_script(Path(temp_dir), check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            self.assertIn("adp-project-kickoff", result["recommended_workflows"])


if __name__ == "__main__":
    unittest.main()
