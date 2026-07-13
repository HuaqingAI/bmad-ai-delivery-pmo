import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "audit_state.py"
SCRIPT_GLOBALS = runpy.run_path(str(SCRIPT))
SCENARIO_CAPABILITIES = SCRIPT_GLOBALS["SCENARIO_CAPABILITIES"]
STABLE_INPUT_AUDIT_ID = SCRIPT_GLOBALS["stable_input_audit_id"]
AUDIT_CONTENT_HASH = SCRIPT_GLOBALS["audit_content_hash"]
VALIDATE_RENDER_CONTRACT = SCRIPT_GLOBALS["validate_render_contract"]
PREPASS_SCRIPT = SCRIPT.parents[2] / "adp-agent-program-lead" / "scripts" / "adp-state-prepass.py"
LOCALE_CATALOG_PATH = SCRIPT.parents[2] / "adp-plan-baseline" / "assets" / "locale-catalog.json"
LOCALE_CATALOG = json.loads(LOCALE_CATALOG_PATH.read_text(encoding="utf-8"))
LOCALE_CATALOG_FINGERPRINT = hashlib.sha256(LOCALE_CATALOG_PATH.read_bytes()).hexdigest()


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
- Blockers: TBD
- Risks: TBD
- Dependencies: see cross-workstream links
- Scope or change notes: Payment flow changed
- Next actions: ACT-MISSING-001 close stale item; FDE-A add checkout evidence
- Last status sync: 2026-07-01T09:00:00+08:00

## Cross-Workstream Links

Depends on:

- l2-payments

Impacts:

- l3-settlement

L0 references:

- gate-payments
"""


class AdpStateAuditTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def assert_failure_contract(self, result: dict, *, scenario: str = "global", headless: bool = False) -> None:
        self.assertFalse(result["ok"])
        self.assertIn(result["status"], {"blocked", "error"})
        self.assertEqual(result["scenario"], scenario)
        self.assertEqual(result["outputs"], {})
        self.assertIsInstance(result["recommended_workflows"], list)
        self.assertTrue(result.get("error") or result.get("reason"))
        if headless:
            self.assertTrue(result["memlog"])
            self.assertTrue(Path(result["memlog"]).exists())

    def test_scenario_capabilities_are_accepted_by_installed_prepass(self) -> None:
        project_root = SCRIPT.parents[3]
        for scenario, capability in SCENARIO_CAPABILITIES.items():
            with self.subTest(scenario=scenario, capability=capability):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(PREPASS_SCRIPT),
                        str(project_root),
                        "--activation",
                        "--capability",
                        capability,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @staticmethod
    def typed_gap(category: str, blocking: bool, workflow: str) -> dict:
        return {
            "gap": f"{category} test gap",
            "category": category,
            "gap_type": f"{category}_test",
            "blocking": blocking,
            "field": f"{category}_field",
            "recommended_workflow": workflow,
        }

    def scaffold(self, project_root: Path) -> Path:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        dirs = [
            "actions",
            "daily",
            "decisions/business-decision-packets",
            "intake/status-sync",
            "l0",
            "meetings",
            "views",
            "workstreams/l1-checkout",
        ]
        for rel in dirs:
            (memory_root / rel).mkdir(parents=True, exist_ok=True)
        for rel in ["index.md", "project-charter.md", "cadence.md"]:
            (memory_root / rel).write_text(f"# {rel}\n", encoding="utf-8")
        for rel in [
            "reference-index.md",
            "extracted-freeze-model.md",
            "extracted-contract-inventory.md",
            "extracted-gates.md",
            "extracted-nfr.md",
            "extracted-evidence-rules.md",
            "extracted-impacts.md",
            "extracted-decision-gates.md",
            "exceptions-and-open-questions.md",
        ]:
            (memory_root / "l0" / rel).write_text(f"# {rel}\n\n- gate-payments\n", encoding="utf-8")
        for rel in ["acceptance-readiness.md", "risk-matrix.md", "dependency-map.md", "fde-actions.md"]:
            (memory_root / "views" / rel).write_text(f"# {rel}\n\nGenerated view.\n", encoding="utf-8")
        (memory_root / "views" / "project-lead.md").write_text(
            "# Project Lead View\n\n- Overall status: TBD\n- Active workstreams: TBD\n",
            encoding="utf-8",
        )
        (memory_root / "views" / "weekly-report.md").write_text(
            "# Weekly Report\n\n## Summary\n\nTBD\n\n| Workstream | Status |\n| --- | --- |\n| TBD | TBD |\n",
            encoding="utf-8",
        )
        workstream_root = memory_root / "workstreams" / "l1-checkout"
        (workstream_root / "delivery-record.md").write_text(RECORD, encoding="utf-8")
        (workstream_root / "evidence.md").write_text("# Evidence\n\n- TBD\n", encoding="utf-8")
        (workstream_root / "readiness.md").write_text("# Readiness\n\n- TBD\n", encoding="utf-8")
        (workstream_root / "decisions.md").write_text(
            "# Decisions\n\n| Date | Type | Decision / Question | Owner | Status |\n| --- | --- | --- | --- | --- |\n| 2026-07-01 | Business | Confirm payment owner | Biz-A | open |\n",
            encoding="utf-8",
        )
        (memory_root / "actions" / "action-ledger.md").write_text(
            "\n".join(
                [
                    "# Action Ledger",
                    "",
                    "| Action ID | Status | Owner | Workstream | Affected Workstreams | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| ACT-20260702-001 | open | FDE-A | l1-checkout | l1-checkout | Add checkout validation evidence | meetings/2026-07-02-sync.md#M-001 | Meeting action | 2026-07-05 | Evidence linked | 2026-07-02T09:00:00+08:00 | adp-status-sync |",
                    "| ACT-20260702-002 | open | FDE-A | l1-checkout | l1-checkout | Add checkout validation evidence | meetings/2026-07-02-sync.md#M-002 | Meeting action | 2026-07-05 | Evidence linked | 2026-07-02T09:00:00+08:00 | adp-status-sync |",
                    "| ACT-20260702-003 | blocked | TBD | program | TBD | Resolve business signoff | meetings/2026-07-02-sync.md#M-003 | Meeting action | TBD | TBD | 2026-07-02T09:00:00+08:00 | adp-status-sync |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "decisions" / "decision-log.md").write_text("# Decision Log\n", encoding="utf-8")
        (memory_root / "decisions" / "business-decision-packets" / "2026-07-01-payment-owner.md").write_text(
            "\n".join(
                [
                    "# Business Decision Packet: Payment Owner",
                    "",
                    "Created: 2026-07-01",
                    "Source meeting: meetings/2026-07-01-sync.md",
                    "Affected workstreams: l1-checkout",
                    "Status: open",
                    "Confirming owner: TBD",
                    "Deadline / trigger: 2026-07-03",
                    "",
                    "## Background",
                    "",
                    "TBD",
                    "",
                    "## Decision Needed",
                    "",
                    "Who signs off payment acceptance?",
                    "",
                    "## Options",
                    "",
                    "- TBD",
                    "",
                    "## Recommendation",
                    "",
                    "TBD",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (memory_root / "intake" / "status-sync" / "pending-actions.json").write_text(
            json.dumps(
                {
                    "status": "pending",
                    "updates": [{"id": "l1-checkout", "next_actions": ["Confirm payment owner"]}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return memory_root

    def write_valid_baseline(
        self,
        project_root: Path,
        memory_root: Path,
        milestones: list[dict] | None = None,
        *,
        configure_language: bool = True,
    ) -> Path:
        if configure_language:
            config = project_root / "_bmad" / "adp" / "config.yaml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                "communication_language: English\ndocument_output_language: English\n",
                encoding="utf-8",
            )
        model = {
            "schema_version": "1.0",
            "baseline_id": "PROGRAM-BASELINE",
            "revision": 1,
            "confirmation_status": "approved",
            "project": {
                "name": "Audit test",
                "owner": "PMO",
                "target_date": "2026-12-31",
                "source": {
                    "type": "charter",
                    "reference": "project-charter.md#target",
                    "confirmed_by": "PMO",
                },
            },
            "default_tolerance_days": 0,
            "gates": [],
            "milestones": milestones or [],
            "critical_path": [item["id"] for item in (milestones or []) if item.get("critical_path")],
            "weighting": {"enabled": False, "completion_measure": None, "source": None},
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        }
        baseline = memory_root / "plans" / "program-baseline.md"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            "# Program Baseline\n\n<!-- adp:program-baseline:v1 -->\n\n```json\n"
            + json.dumps(model, indent=2)
            + "\n```\n",
            encoding="utf-8",
        )
        return baseline

    @staticmethod
    def milestone(
        milestone_id: str,
        workstream_id: str,
        planned_date: str,
    ) -> dict:
        return {
            "id": milestone_id,
            "name": milestone_id.replace("-", " ").title(),
            "workstream_id": workstream_id,
            "planned_date": planned_date,
            "owner": "FDE-A",
            "confirmation_status": "approved",
            "source": {
                "type": "approved-plan",
                "reference": f"project-charter.md#{milestone_id.lower()}",
                "confirmed_by": "PMO",
            },
            "dependencies": [],
            "critical_path": True,
            "baseline_revision": 1,
        }

    def write_minimal_prepass(self, project_root: Path, memory_root: Path, **overrides: object) -> Path:
        payload = {
            "ok": True,
            "schema_version": 2,
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "sources_read": [],
            "missing_sources": [],
            "workstreams": [],
            "gaps": [],
            "cross_reference_gaps": [],
            "action_cross_check": [],
            "ledger_actions": [],
            "counts": {},
        }
        payload.update(overrides)
        path = project_root / "prepass-vnext.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def seal_input_audit(payload: dict) -> dict:
        sealed = {
            "audit_type": "input",
            "audit_schema_version": 1,
            "schema_version": 1,
            "generator_version": "2.0.0",
            "scenario": "global",
            "as_of": "2026-07-10",
            "execution_disposition": "ready",
            "audit_status": "pass",
            "safe_to_generate": True,
            "safe_to_generate_green_report": True,
            "report_confidence": "high",
            "recommended_workflows": [],
            "baseline_revision": 1,
            "baseline_fingerprint": "baseline-hash",
            "locale": "en",
            "locale_fallback": False,
            "source_fingerprints": {"source.md": "source-hash"},
            "blocking_gaps": [],
            "warnings": [],
            "duplicate_candidates": [],
            "overlap_claims": [],
            "conflicts": [],
            "stale_items": [],
            **payload,
        }
        sealed["input_audit_id"] = STABLE_INPUT_AUDIT_ID(sealed)
        sealed["audit_content_hash"] = AUDIT_CONTENT_HASH(sealed)
        return sealed

    @staticmethod
    def render_contract(locale_value: str) -> dict:
        title = LOCALE_CATALOG[locale_value]["status.title"]
        return {
            "catalog_locale": locale_value,
            "catalog_fingerprint": LOCALE_CATALOG_FINGERPRINT,
            "message_keys": ["status.title"],
            "unresolved_message_keys": [],
            "source_fact_translation_persisted": False,
            "localized_system_text": [title],
        }

    def test_missing_baseline_blocks_input_audit_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            prepass = self.write_minimal_prepass(project_root, memory_root)

            completed = self.run_script(
                project_root,
                "--memory-root",
                str(memory_root),
                "--prepass-json",
                str(prepass),
                "--as-of",
                "2026-07-10",
            )
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["audit_type"], "input")
            self.assertEqual(result["execution_disposition"], "blocked")
            self.assertFalse(audit["safe_to_generate"])
            baseline_findings = [item for item in audit["blocking_gaps"] if item.get("code") == "baseline.missing"]
            self.assertEqual(len(baseline_findings), 1)
            self.assertEqual(baseline_findings[0]["recommended_workflow"], "adp-plan-baseline")

    def test_due_actual_missing_degrades_but_future_actual_is_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(
                project_root,
                memory_root,
                [
                    self.milestone("MS-DUE", "l1-checkout", "2026-07-01"),
                    self.milestone("MS-TODAY", "l1-checkout", "2026-07-10"),
                    {**self.milestone("MS-TOLERANCE", "l1-checkout", "2026-07-05"), "tolerance_days": 7},
                    self.milestone("MS-FUTURE", "l1-checkout", "2026-08-01"),
                ],
            )
            prepass = self.write_minimal_prepass(project_root, memory_root)

            completed = self.run_script(
                project_root,
                "--memory-root",
                str(memory_root),
                "--prepass-json",
                str(prepass),
                "--as-of",
                "2026-07-10",
            )
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            missing = [item for item in audit["warnings"] if item.get("code") == "actual.missing"]

            self.assertEqual(result["audit_status"], "warning")
            self.assertEqual(result["execution_disposition"], "degraded")
            self.assertTrue(audit["safe_to_generate"])
            self.assertEqual(len(missing), 1)
            self.assertIn("MS-DUE", missing[0]["summary"])
            self.assertNotIn("MS-FUTURE", "\n".join(item["summary"] for item in audit["warnings"]))
            self.assertNotIn("MS-TODAY", "\n".join(item["summary"] for item in audit["warnings"]))
            self.assertNotIn("MS-TOLERANCE", "\n".join(item["summary"] for item in audit["warnings"]))

    def test_unmapped_actual_blocks_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(
                project_root,
                memory_root,
                [self.milestone("MS-DUE", "l1-checkout", "2026-07-01")],
            )
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.parent.mkdir(parents=True)
            record.write_text(
                "# WDR\n\n## Roadmap\n\n"
                "| Milestone ID | Milestone | Status | Forecast | Actual | Source |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| MS-UNKNOWN | Unknown | done | 2026-07-02 | 2026-07-02 | evidence.md#done |\n",
                encoding="utf-8",
            )
            prepass = self.write_minimal_prepass(project_root, memory_root)

            completed = self.run_script(project_root, "--memory-root", str(memory_root), "--prepass-json", str(prepass), "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["execution_disposition"], "blocked")
            self.assertFalse(audit["safe_to_generate"])
            self.assertIn("actual.unmapped", {item.get("code") for item in audit["blocking_gaps"]})

    def test_mapped_actual_satisfies_due_milestone_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(
                project_root,
                memory_root,
                [self.milestone("MS-DUE", "l1-checkout", "2026-07-01")],
            )
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.parent.mkdir(parents=True)
            record.write_text(
                "# WDR\n\n## Roadmap\n\n"
                "| Milestone ID | Milestone | Status | Forecast | Actual | Source | Baseline Revision |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                "| MS-DUE | Due | done | 2026-07-01 | 2026-07-01 | evidence.md#done | 1 |\n",
                encoding="utf-8",
            )
            prepass = self.write_minimal_prepass(project_root, memory_root)

            completed = self.run_script(project_root, "--memory-root", str(memory_root), "--prepass-json", str(prepass), "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["audit_status"], "pass")
            self.assertEqual(result["execution_disposition"], "ready")
            self.assertTrue(audit["safe_to_generate_green_report"])
            self.assertNotIn("actual.missing", {item.get("code") for item in audit["warnings"]})

    def test_mapped_actual_must_match_current_baseline_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root, [self.milestone("MS-DUE", "l1-checkout", "2026-07-01")])
            record = memory_root / "workstreams" / "l1-checkout" / "delivery-record.md"
            record.parent.mkdir(parents=True)
            record.write_text(
                "# WDR\n\n## Roadmap\n\n"
                "| Milestone ID | Status | Actual | Source | Baseline Revision |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| MS-DUE | done | 2026-07-01 | evidence.md#done | 0 |\n",
                encoding="utf-8",
            )
            prepass = self.write_minimal_prepass(project_root, memory_root)

            result = json.loads(
                self.run_script(project_root, "--memory-root", str(memory_root), "--prepass-json", str(prepass), "--as-of", "2026-07-10").stdout
            )
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["execution_disposition"], "blocked")
            self.assertIn("actual.baseline_revision_mismatch", {item.get("code") for item in audit["blocking_gaps"]})

    def test_locale_fallback_is_explicit_degraded_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root, configure_language=False)
            prepass = self.write_minimal_prepass(project_root, memory_root)

            completed = self.run_script(project_root, "--memory-root", str(memory_root), "--prepass-json", str(prepass))
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["execution_disposition"], "degraded")
            self.assertTrue(audit["locale_fallback"])
            self.assertIn("locale.fallback", {item.get("code") for item in audit["warnings"]})

    def test_artifact_validation_detects_stale_snapshot_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            audits = memory_root / "audits"
            snapshots = memory_root / "snapshots" / "program-status"
            audits.mkdir(parents=True)
            snapshots.mkdir(parents=True)
            input_audit = audits / "input.json"
            sealed_audit = self.seal_input_audit(
                {
                    "baseline_revision": 2,
                    "source_fingerprints": {"plans/program-baseline.md": "abc123"},
                }
            )
            input_audit.write_text(
                json.dumps(sealed_audit),
                encoding="utf-8",
            )
            artifact = snapshots / "snapshot.json"
            artifact.write_text(
                json.dumps(
                    {
                        "title": LOCALE_CATALOG["en"]["status.title"],
                        "generated_at": "2026-06-01T00:00:00Z",
                        "as_of": "2026-06-01",
                        "reporting_period": {"start": "2026-05-25", "end": "2026-06-01"},
                        "report_confidence": "high",
                        "scenario": "global",
                        "input_audit_id": sealed_audit["input_audit_id"],
                        "baseline_revision": 2,
                        "source_fingerprints": {"plans/program-baseline.md": "abc123"},
                        "locale": "en",
                        "locale_fallback": False,
                        "render_contract": self.render_contract("en"),
                        "generator_version": "1.0.0",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            before = artifact.read_bytes()

            completed = self.run_script(
                project_root,
                "--phase",
                "artifact",
                "--memory-root",
                str(memory_root),
                "--input-audit-json",
                str(input_audit),
                "--artifact",
                str(artifact),
                "--as-of",
                "2026-07-10",
                "--max-age-days",
                "7",
            )
            result = json.loads(completed.stdout)
            validation = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["audit_type"], "artifact")
            self.assertEqual(result["execution_disposition"], "degraded")
            self.assertTrue(result["safe_to_publish"])
            self.assertIn("artifact.stale_snapshot", {item.get("code") for item in validation["warnings"]})
            self.assertEqual(artifact.read_bytes(), before)
            self.assertNotEqual(Path(result["outputs"]["json"]), artifact)

    def test_artifact_locale_and_lineage_mismatch_block_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            input_audit = project_root / "input.json"
            sealed_audit = self.seal_input_audit(
                {
                    "baseline_revision": 1,
                    "locale": "zh",
                    "source_fingerprints": {"source.md": "expected"},
                }
            )
            input_audit.write_text(
                json.dumps(sealed_audit),
                encoding="utf-8",
            )
            artifact = project_root / "artifact.json"
            artifact.write_text(
                json.dumps(
                    {
                        "title": LOCALE_CATALOG["en"]["status.title"],
                        "generated_at": "2026-07-10T00:00:00Z",
                        "as_of": "2026-07-10",
                        "reporting_period": "2026-07-04/2026-07-10",
                        "report_confidence": "high",
                        "scenario": "global",
                        "input_audit_id": "input-audit-wrong",
                        "baseline_revision": 1,
                        "source_fingerprints": {"source.md": "expected"},
                        "locale": "en",
                        "locale_fallback": False,
                        "render_contract": self.render_contract("en"),
                        "generator_version": "1.0.0",
                    }
                ),
                encoding="utf-8",
            )

            completed = self.run_script(
                project_root,
                "--phase",
                "artifact",
                "--memory-root",
                str(memory_root),
                "--input-audit-json",
                str(input_audit),
                "--artifact",
                str(artifact),
                check=True,
            )
            result = json.loads(completed.stdout)
            validation = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["execution_disposition"], "blocked")
            self.assertFalse(result["safe_to_publish"])
            codes = {item.get("code") for item in validation["blocking_gaps"]}
            self.assertTrue({"artifact.input_audit_mismatch", "artifact.locale_mismatch"}.issubset(codes))

    def test_artifact_validation_detects_sources_changed_after_input_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            source = memory_root / "source.md"
            source.write_text("current", encoding="utf-8")
            sealed_audit = self.seal_input_audit({"source_fingerprints": {"source.md": "stale-hash"}})
            input_audit = project_root / "input.json"
            input_audit.write_text(json.dumps(sealed_audit), encoding="utf-8")
            artifact = project_root / "artifact.json"
            artifact.write_text(
                json.dumps(
                    {
                        "title": LOCALE_CATALOG["en"]["status.title"],
                        "generated_at": "2026-07-10T00:00:00Z",
                        "as_of": "2026-07-10",
                        "reporting_period": "2026-07-04/2026-07-10",
                        "report_confidence": "high",
                        "scenario": "global",
                        "input_audit_id": sealed_audit["input_audit_id"],
                        "baseline_revision": 1,
                        "source_fingerprints": {"source.md": "stale-hash"},
                        "locale": "en",
                        "locale_fallback": False,
                        "render_contract": self.render_contract("en"),
                        "generator_version": "1.0.0",
                    }
                ),
                encoding="utf-8",
            )

            result = json.loads(
                self.run_script(
                    project_root,
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory_root),
                    "--input-audit-json",
                    str(input_audit),
                    "--artifact",
                    str(artifact),
                    "--as-of",
                    "2026-07-10",
                ).stdout
            )
            validation = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["execution_disposition"], "degraded")
            self.assertIn("artifact.input_sources_changed", {item.get("code") for item in validation["warnings"]})

    def test_localized_markdown_uses_stable_machine_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            source = memory_root / "source.md"
            source.write_text("事实", encoding="utf-8")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            sealed_audit = self.seal_input_audit(
                {"locale": "zh", "scenario": "roadmap", "source_fingerprints": {"source.md": source_hash}}
            )
            input_audit = project_root / "input.json"
            input_audit.write_text(json.dumps(sealed_audit, ensure_ascii=False), encoding="utf-8")
            metadata = {
                "generated_at": "2026-07-10T00:00:00Z",
                "as_of": "2026-07-10",
                "reporting_period": "2026-07-04/2026-07-10",
                "report_confidence": "high",
                "scenario": "roadmap",
                "input_audit_id": sealed_audit["input_audit_id"],
                "baseline_revision": 1,
                "source_fingerprints": {"source.md": source_hash},
                "locale": "zh",
                "locale_fallback": False,
                "render_contract": self.render_contract("zh"),
                "generator_version": "1.0.0",
            }
            artifact = project_root / "状态.md"
            artifact.write_text(
                f"# {LOCALE_CATALOG['zh']['status.title']}\n\n<!-- adp:artifact-metadata:v1 -->\n\n```json\n"
                + json.dumps(metadata, ensure_ascii=False, indent=2)
                + "\n```\n",
                encoding="utf-8",
            )

            result = json.loads(
                self.run_script(
                    project_root,
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory_root),
                    "--input-audit-json",
                    str(input_audit),
                    "--artifact",
                    str(artifact),
                    "--as-of",
                    "2026-07-10",
                ).stdout
            )

            self.assertEqual(result["audit_status"], "pass")
            self.assertEqual(result["execution_disposition"], "ready")
            self.assertEqual(result["scenario"], "roadmap")
            self.assertTrue(result["safe_to_publish"])

            artifact.write_text(
                "# Project Status\n\n<!-- adp:artifact-metadata:v1 -->\n\n```json\n"
                + json.dumps(metadata, ensure_ascii=False, indent=2)
                + "\n```\n",
                encoding="utf-8",
            )
            negative = json.loads(
                self.run_script(
                    project_root,
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory_root),
                    "--input-audit-json",
                    str(input_audit),
                    "--artifact",
                    str(artifact),
                    "--as-of",
                    "2026-07-10",
                ).stdout
            )
            negative_validation = json.loads(Path(negative["outputs"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(negative["execution_disposition"], "blocked")
            self.assertIn("artifact.render_contract_invalid", {item.get("code") for item in negative_validation["blocking_gaps"]})

    def test_known_render_profile_rejects_underdeclared_message_coverage(self) -> None:
        contract = self.render_contract("en")
        contract["coverage_profile"] = "adp-program-status-markdown"

        errors = VALIDATE_RENDER_CONTRACT(
            contract,
            "en",
            LOCALE_CATALOG["en"]["status.title"],
            LOCALE_CATALOG,
            LOCALE_CATALOG_FINGERPRINT,
        )

        self.assertTrue(any("missing required message keys" in error for error in errors))
        self.assertTrue(any("missing message-key families" in error for error in errors))

    def test_forged_input_audit_and_invalid_generated_at_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            sealed_audit = self.seal_input_audit({})
            input_audit = project_root / "input.json"
            forged = dict(sealed_audit)
            forged["locale"] = "zh"
            input_audit.write_text(json.dumps(forged), encoding="utf-8")
            artifact = project_root / "artifact.json"
            artifact.write_text("{}", encoding="utf-8")

            forged_result = json.loads(
                self.run_script(
                    project_root,
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory_root),
                    "--input-audit-json",
                    str(input_audit),
                    "--artifact",
                    str(artifact),
                    check=False,
                ).stdout
            )
            self.assert_failure_contract(forged_result)
            self.assertEqual(forged_result["audit_type"], "artifact")
            self.assertFalse(forged_result["safe_to_publish"])

            input_audit.write_text(json.dumps(sealed_audit), encoding="utf-8")
            metadata = {
                "title": LOCALE_CATALOG["en"]["status.title"],
                "generated_at": "not-a-time",
                "as_of": "2026-07-10",
                "reporting_period": "2026-07-04/2026-07-10",
                "report_confidence": "high",
                "scenario": "global",
                "input_audit_id": sealed_audit["input_audit_id"],
                "baseline_revision": 1,
                "source_fingerprints": {"source.md": "source-hash"},
                "locale": "en",
                "locale_fallback": False,
                "render_contract": self.render_contract("en"),
                "generator_version": "1.0.0",
            }
            artifact.write_text(json.dumps(metadata), encoding="utf-8")
            invalid_result = json.loads(
                self.run_script(
                    project_root,
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory_root),
                    "--input-audit-json",
                    str(input_audit),
                    "--artifact",
                    str(artifact),
                    "--as-of",
                    "2026-07-10",
                ).stdout
            )
            validation = json.loads(Path(invalid_result["outputs"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(invalid_result["execution_disposition"], "blocked")
            self.assertIn("artifact.generated_at_invalid", {item.get("code") for item in validation["blocking_gaps"]})

            metadata["generated_at"] = "2026-07-11T00:00:00Z"
            artifact.write_text(json.dumps(metadata), encoding="utf-8")
            future_result = json.loads(
                self.run_script(
                    project_root,
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory_root),
                    "--input-audit-json",
                    str(input_audit),
                    "--artifact",
                    str(artifact),
                    "--as-of",
                    "2026-07-10",
                ).stdout
            )
            future_validation = json.loads(Path(future_result["outputs"]["json"]).read_text(encoding="utf-8"))
            self.assertIn("artifact.generated_at_future", {item.get("code") for item in future_validation["blocking_gaps"]})

    def test_tampered_immutable_audit_is_rejected_on_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root)
            prepass = self.write_minimal_prepass(project_root, memory_root)
            args = ("--memory-root", str(memory_root), "--prepass-json", str(prepass), "--as-of", "2026-07-10")
            first = json.loads(self.run_script(project_root, *args).stdout)
            audit_path = Path(first["outputs"]["json"])
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
            payload["execution_disposition"] = "blocked"
            audit_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

            second_run = self.run_script(project_root, *args, check=False)
            second = json.loads(second_run.stdout)

            self.assertEqual(second_run.returncode, 2)
            self.assert_failure_contract(second)
            self.assertIn("integrity validation", second["error"])

    def test_stable_audit_id_changes_when_gate_semantics_change(self) -> None:
        base = self.seal_input_audit(
            {
                "audit_status": "warning",
                "execution_disposition": "degraded",
                "warnings": [
                    {
                        "id": "finding-1",
                        "code": "actual.missing",
                        "severity": "warning",
                        "execution_disposition": "degraded",
                        "kind": "input_contract",
                        "source_type": "fact",
                        "sources": ["workstreams/l1/delivery-record.md"],
                        "workstreams": ["l1"],
                        "owner": "FDE-A",
                        "summary": "actual is missing",
                        "category": "vnext_input",
                        "gap_type": "actual.missing",
                        "recommended_workflow": "adp-status-sync",
                    }
                ],
            }
        )
        changed = json.loads(json.dumps(base))
        changed["warnings"][0]["execution_disposition"] = "blocked"

        self.assertNotEqual(STABLE_INPUT_AUDIT_ID(base), STABLE_INPUT_AUDIT_ID(changed))

    def test_input_audit_is_stable_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root)
            prepass = self.write_minimal_prepass(project_root, memory_root)
            args = ("--memory-root", str(memory_root), "--prepass-json", str(prepass), "--as-of", "2026-07-10")

            first = json.loads(self.run_script(project_root, *args).stdout)
            first_path = Path(first["outputs"]["json"])
            first_bytes = first_path.read_bytes()
            second = json.loads(self.run_script(project_root, *args).stdout)

            self.assertEqual(first["input_audit_id"], second["input_audit_id"])
            self.assertEqual(first["outputs"], second["outputs"])
            self.assertEqual(first_path.read_bytes(), first_bytes)

    def test_input_audit_fingerprints_only_durable_fact_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            (memory_root / "views").mkdir(parents=True)
            (memory_root / "decisions").mkdir(parents=True)
            (memory_root / "views" / "program-status.json").write_text("{}", encoding="utf-8")
            (memory_root / "decisions" / "decision-log.md").write_text("# Decisions", encoding="utf-8")
            self.write_valid_baseline(project_root, memory_root)
            prepass = self.write_minimal_prepass(
                project_root,
                memory_root,
                sources_read=[
                    {"path": "views/program-status.json", "modified": "2026-07-10"},
                    {"path": "decisions/decision-log.md", "modified": "2026-07-10"},
                ],
            )

            result = json.loads(self.run_script(project_root, "--memory-root", str(memory_root), "--prepass-json", str(prepass)).stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertNotIn("views/program-status.json", audit["source_fingerprints"])
            self.assertIn("decisions/decision-log.md", audit["source_fingerprints"])

    def test_writes_audit_artifacts_and_reports_quality_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["audit_status"], "blocked")
            self.assertTrue(Path(result["outputs"]["markdown"]).exists())
            self.assertEqual(audit["counts"]["workstreams"], 1)
            self.assertTrue(audit["findings"]["freshness"]["views_requiring_refresh"])
            self.assertTrue(audit["findings"]["closure"]["unconsumed_intake_files"])
            self.assertTrue(audit["findings"]["closure"]["open_business_packets"])
            self.assertTrue(audit["findings"]["merge_quality"]["duplicate_candidates"])
            self.assertEqual(audit["findings"]["merge_quality"]["conflict_candidates"], [])
            self.assertTrue(audit["merge_review_evidence"]["readiness_gap_pairs"])
            self.assertTrue(audit["findings"]["consistency"]["source_disagreements"])
            self.assertEqual(audit["audit_schema_version"], 1)
            self.assertFalse(audit["safe_to_generate_green_report"])
            self.assertEqual(audit["report_confidence"], "low")
            self.assertIsInstance(audit["source_inventory_items"], list)
            required_finding_fields = {
                "id",
                "severity",
                "execution_disposition",
                "kind",
                "source_type",
                "sources",
                "workstreams",
                "owner",
                "summary",
            }
            canonical_groups = [
                "blocking_gaps",
                "warnings",
                "duplicate_candidates",
                "overlap_claims",
                "conflicts",
                "stale_items",
            ]
            for group in canonical_groups:
                for finding in audit[group]:
                    self.assertTrue(required_finding_fields.issubset(finding), (group, finding))
            self.assertIn("adp-status-sync", result["recommended_workflows"])
            self.assertEqual(Path(result["outputs"]["json"]).parent, memory_root / "audits")
            self.assertTrue(audit["input_audit_id"].startswith("input-audit-"))

    def test_terminal_business_decision_statuses_are_closed(self) -> None:
        terminal_statuses = ["accepted", "closed", "done", "cancelled", "rejected", "superseded"]
        for status in terminal_statuses:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                packet = memory_root / "decisions" / "business-decision-packets" / "2026-07-01-payment-owner.md"
                packet.write_text(packet.read_text(encoding="utf-8").replace("Status: open", f"Status: {status}"), encoding="utf-8")

                completed = self.run_script(project_root, "--as-of", "2026-07-10")
                result = json.loads(completed.stdout)
                audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

                self.assertEqual(audit["findings"]["closure"]["open_business_packets"], [])
                packet_gaps = [
                    item
                    for item in audit["findings"]["completeness"]["blocking_gaps"]
                    if item.get("source", "").endswith("payment-owner.md")
                ]
                self.assertEqual(packet_gaps, [])

    def test_applied_intake_and_reports_are_not_unconsumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            intake_root = memory_root / "intake" / "status-sync"
            (intake_root / "pending-actions.json").unlink()
            (intake_root / "applied-actions.json").write_text(
                json.dumps({"status": "applied", "updates": [{"id": "l1-checkout", "progress": "Done"}]}) + "\n",
                encoding="utf-8",
            )
            (intake_root / "legacy-actions.json").write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "progress": "Applied"}]}) + "\n",
                encoding="utf-8",
            )
            (intake_root / "legacy-actions-report.json").write_text(
                json.dumps({"ok": True, "mode": "update", "dry_run": False, "updates": [{"ok": True}]}) + "\n",
                encoding="utf-8",
            )
            receipt_intake = intake_root / "receipt-actions.json"
            receipt_intake.write_text(
                json.dumps({"status": "pending", "updates": [{"id": "l1-checkout", "progress": "Receipted"}]})
                + "\n",
                encoding="utf-8",
            )
            (intake_root / "receipt-actions-receipt.json").write_text(
                json.dumps(
                    {
                        "status": "applied",
                        "applied_at": "2026-07-10T10:00:00+08:00",
                        "input_hash": f"sha256:{hashlib.sha256(receipt_intake.read_bytes()).hexdigest()}",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (intake_root / "2026-07-07-2026-07-07-review-actions.json").write_text(
                json.dumps({"updates": [{"id": "l1-checkout", "progress": "Reviewed"}]}) + "\n",
                encoding="utf-8",
            )
            (intake_root / "2026-07-07-review-actions-report.json").write_text(
                json.dumps({"ok": True, "mode": "update", "dry_run": False, "updates": [{"ok": True}]}) + "\n",
                encoding="utf-8",
            )
            (intake_root / "migration-dry-run-report.json").write_text(
                json.dumps({"ok": True, "mode": "update", "dry_run": True, "updates": []}) + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(audit["findings"]["closure"]["unconsumed_intake_files"], [])

    def test_pending_canonical_intake_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            unconsumed = audit["findings"]["closure"]["unconsumed_intake_files"]

            self.assertEqual(result["audit_status"], "blocked")
            self.assertEqual([item["path"] for item in unconsumed], ["intake/status-sync/pending-actions.json"])

    def test_legitimate_tbd_does_not_mark_generated_view_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            (memory_root / "views" / "risk-matrix.md").write_text(
                "\n".join(
                    [
                        "# ADP Risk Matrix",
                        "",
                        f"Generated: {generated_at}",
                        "",
                        "| Risk | Severity | Owner | Mitigation |",
                        "| --- | --- | --- | --- |",
                        "| Payment cutover | high | Biz-A | TBD |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            refresh_paths = {
                item["path"] for item in audit["findings"]["freshness"]["views_requiring_refresh"]
            }

            self.assertNotIn("views/risk-matrix.md", refresh_paths)

    def test_free_text_tbd_does_not_make_ungenerated_view_substantive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            refresh_paths = {
                item["path"] for item in audit["findings"]["freshness"]["views_requiring_refresh"]
            }

            self.assertIn("views/project-lead.md", refresh_paths)

    def test_markdown_bullet_tbd_is_an_explicit_packet_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            packet_gaps = [
                item["gap"]
                for item in audit["findings"]["completeness"]["blocking_gaps"]
                if item.get("source", "").endswith("payment-owner.md")
            ]

            self.assertIn("business decision packet options is missing or TBD", packet_gaps)

    def test_structured_view_rows_distinguish_all_placeholders_from_mixed_content(self) -> None:
        cases = [
            ("| TBD | TBD |", True),
            ("| Payment cutover | TBD |", False),
        ]
        for row, expected_refresh in cases:
            with self.subTest(row=row), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                memory_root = self.scaffold(project_root)
                view_path = memory_root / "views" / "risk-matrix.md"
                view_path.write_text(
                    "# Risk Matrix\n\n| Risk | Owner |\n| --- | --- |\n" + row + "\n",
                    encoding="utf-8",
                )
                prepass_path = project_root / "prepass.json"
                prepass_path.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "schema_version": 2,
                            "project_root": str(project_root),
                            "memory_root": str(memory_root),
                            "sources_read": [{"path": "views/risk-matrix.md", "modified": ""}],
                            "missing_sources": [],
                            "workstreams": [],
                            "gaps": [],
                            "cross_reference_gaps": [],
                            "action_cross_check": [],
                            "ledger_actions": [],
                            "counts": {},
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                completed = self.run_script(project_root, "--prepass-json", str(prepass_path))
                result = json.loads(completed.stdout)
                audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
                refresh_paths = {
                    item["path"] for item in audit["findings"]["freshness"]["views_requiring_refresh"]
                }

                self.assertEqual("views/risk-matrix.md" in refresh_paths, expected_refresh)

    def test_view_lineage_hash_change_marks_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            source_path = "workstreams/l1-checkout/delivery-record.md"
            (memory_root / "views" / "risk-matrix.md").write_text(
                "\n".join(
                    [
                        "# ADP Risk Matrix",
                        "",
                        f"Generated: {generated_at}",
                        f'Source paths: ["{source_path}"]',
                        f'Source hashes: {{"{source_path}": "sha256:deadbeef"}}',
                        "",
                        "| Risk | Severity | Owner | Mitigation |",
                        "| --- | --- | --- | --- |",
                        "| Payment cutover | high | Biz-A | Confirm rollback |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            refresh = {
                item["path"]: item["reason"]
                for item in audit["findings"]["freshness"]["views_requiring_refresh"]
            }

            self.assertIn("views/risk-matrix.md", refresh)
            self.assertIn("lineage source hash changed", refresh["views/risk-matrix.md"])

    def test_missing_declared_lineage_source_marks_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            missing_source = "workstreams/l9-missing/delivery-record.md"
            (memory_root / "views" / "risk-matrix.md").write_text(
                "\n".join(
                    [
                        "# ADP Risk Matrix",
                        "",
                        f"Generated: {generated_at}",
                        f'Source paths: ["{missing_source}"]',
                        "",
                        "| Risk | Severity | Owner | Mitigation |",
                        "| --- | --- | --- | --- |",
                        "| Payment cutover | high | Biz-A | Confirm rollback |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            refresh = {
                item["path"]: item["reason"]
                for item in audit["findings"]["freshness"]["views_requiring_refresh"]
            }

            self.assertIn("lineage source is missing", refresh["views/risk-matrix.md"])

    def test_lineage_source_cannot_escape_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            (memory_root / "views" / "risk-matrix.md").write_text(
                "\n".join(
                    [
                        "# ADP Risk Matrix",
                        "",
                        f"Generated: {generated_at}",
                        'Source paths: ["../../outside.txt"]',
                        "",
                        "| Risk | Severity | Owner | Mitigation |",
                        "| --- | --- | --- | --- |",
                        "| Payment cutover | high | Biz-A | Confirm rollback |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            refresh = {
                item["path"]: item["reason"]
                for item in audit["findings"]["freshness"]["views_requiring_refresh"]
            }

            self.assertIn("lineage source path escapes memory root", refresh["views/risk-matrix.md"])

    def test_blocked_action_alone_prevents_green_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root)
            prepass_path = project_root / "prepass.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 2,
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [],
                        "gaps": [],
                        "cross_reference_gaps": [],
                        "action_cross_check": [],
                        "ledger_actions": [
                            {
                                "action_id": "ACT-001",
                                "status": "blocked",
                                "owner": "FDE-A",
                                "workstream": "l1-checkout",
                                "affected_workstreams": "l1-checkout",
                                "action": "Confirm rollback evidence",
                                "source": "meetings/sync.md#M-001",
                                "due_or_trigger": "2026-07-11",
                                "closure_criteria": "Evidence linked",
                                "last_updated": "2026-07-10T09:00:00+08:00",
                            }
                        ],
                        "counts": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(
                project_root,
                "--memory-root",
                str(memory_root),
                "--prepass-json",
                str(prepass_path),
                "--as-of",
                "2026-07-10",
            )
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(audit["audit_status"], "warning")
            self.assertFalse(audit["safe_to_generate_green_report"])
            self.assertEqual(audit["report_confidence"], "medium")

    def test_active_action_count_excludes_terminal_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            prepass_path = project_root / "prepass.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 2,
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [],
                        "gaps": [],
                        "cross_reference_gaps": [],
                        "action_cross_check": [],
                        "ledger_actions": [
                            {"action_id": "ACT-OPEN", "status": "open"},
                            {"action_id": "ACT-DONE", "status": "done"},
                        ],
                        "counts": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--prepass-json", str(prepass_path))
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(audit["counts"]["active_ledger_actions"], 1)

    def test_duplicate_finding_ids_are_unique_across_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            ledger = memory_root / "actions" / "action-ledger.md"
            with ledger.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    "| ACT-20260702-004 | open | FDE-B | l1-checkout | l1-checkout | Review tax evidence | meetings/sync.md#M-004 | Meeting action | 2026-07-06 | Evidence linked | 2026-07-02T09:00:00+08:00 | adp-status-sync |\n"
                    "| ACT-20260702-005 | open | FDE-B | l1-checkout | l1-checkout | Review tax evidence | meetings/sync.md#M-005 | Meeting action | 2026-07-06 | Evidence linked | 2026-07-02T09:00:00+08:00 | adp-status-sync |\n"
                )

            completed = self.run_script(project_root, "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
            ids = [item["id"] for item in audit["duplicate_candidates"]]

            self.assertGreaterEqual(len(ids), 2)
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(all(item["source_type"] == "structural" for item in audit["duplicate_candidates"]))

    def test_shared_references_and_ready_gaps_are_non_gating_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root)
            typed_gap = {
                "gap": "owner is missing",
                "category": "completeness",
                "gap_type": "missing",
                "blocking": True,
                "field": "owner",
                "recommended_workflow": "adp-status-sync",
            }
            workstreams = [
                {
                    "id": ws_id,
                    "status": "ready",
                    "dependencies": "Shared payment release",
                    "links": {"l0_references": ["gate-payments"]},
                    "gaps": [typed_gap],
                }
                for ws_id in ["l1-checkout", "l2-payments"]
            ]
            prepass_path = project_root / "prepass.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 2,
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": workstreams,
                        "gaps": [],
                        "cross_reference_gaps": [],
                        "action_cross_check": [],
                        "ledger_actions": [],
                        "counts": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--prepass-json", str(prepass_path), "--as-of", "2026-07-10")
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["audit_status"], "pass")
            self.assertEqual(audit["overlap_claims"], [])
            self.assertEqual(audit["conflicts"], [])
            self.assertEqual(len(audit["merge_review_evidence"]["shared_references"]), 2)
            self.assertEqual(len(audit["merge_review_evidence"]["readiness_gap_pairs"]), 2)

    def test_every_typed_gap_category_reaches_gate_and_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            self.write_valid_baseline(project_root, memory_root)
            gaps = [
                self.typed_gap("freshness", True, "wf-freshness"),
                self.typed_gap("completeness", False, "wf-completeness"),
                self.typed_gap("consistency", True, "wf-consistency"),
                self.typed_gap("closure", False, "wf-closure"),
                self.typed_gap("merge_quality", True, "wf-merge"),
            ]
            prepass_path = project_root / "prepass.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 2,
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [],
                        "gaps": gaps,
                        "cross_reference_gaps": [],
                        "action_cross_check": [],
                        "ledger_actions": [],
                        "counts": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--prepass-json", str(prepass_path))
            result = json.loads(completed.stdout)
            audit = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["audit_status"], "blocked")
            self.assertEqual(audit["counts"]["blocking_findings"], 3)
            self.assertEqual(audit["counts"]["warning_findings"], 2)
            self.assertEqual(
                result["recommended_workflows"],
                ["wf-closure", "wf-completeness", "wf-consistency", "wf-freshness", "wf-merge"],
            )
            self.assertEqual(
                {item["gap_type"] for item in audit["blocking_gaps"]},
                {"freshness_test", "consistency_test", "merge_quality_test"},
            )

    def test_run_folder_pattern_places_artifacts_under_configured_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)

            completed = self.run_script(
                project_root,
                "--as-of",
                "2026-07-10",
                "--run-folder-pattern",
                "{date}-{scenario}",
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["outputs"]["json"]).parent, memory_root / "audits" / "2026-07-10-global")

    def test_run_folder_pattern_cannot_escape_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            memlog = project_root / "trail" / ".memlog.md"

            completed = self.run_script(
                project_root,
                "--run-folder-pattern",
                "..",
                "--headless",
                "--memlog",
                str(memlog),
                check=False,
            )
            result = json.loads(completed.stdout)
            trail = memlog.read_text(encoding="utf-8")

            self.assertEqual(completed.returncode, 2)
            self.assert_failure_contract(result, headless=True)
            self.assertIn("run_folder_pattern must resolve inside", result["error"])
            self.assertIn("run_folder_pattern must resolve inside", trail)
            self.assertFalse((memory_root / "audits").exists())

    def test_legacy_prose_only_prepass_blocks_without_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.scaffold(project_root)
            prepass_path = project_root / "legacy-prepass.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 1,
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [{"id": "l1-checkout", "gaps": ["owner is missing"]}],
                        "gaps": [{"workstream": "l1-checkout", "gap": "owner is missing", "source": "workstreams/l1-checkout/delivery-record.md"}],
                        "ledger_actions": [],
                        "counts": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--prepass-json", str(prepass_path), check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assert_failure_contract(result)
            self.assertIn("typed gap contract", result["error"])

    def test_empty_legacy_prepass_still_fails_typed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            prepass_path = project_root / "legacy-empty.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 1,
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [],
                        "gaps": [],
                        "cross_reference_gaps": [],
                        "action_cross_check": [],
                        "ledger_actions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--prepass-json", str(prepass_path), check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assert_failure_contract(result)
            self.assertIn("schema_version must be 2", result["details"])

    def test_invalid_prepass_collection_type_fails_typed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = project_root / "memory"
            memory_root.mkdir()
            prepass_path = project_root / "invalid-collections.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 2,
                        "memory_root": str(memory_root),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [],
                        "gaps": {},
                        "cross_reference_gaps": [],
                        "action_cross_check": [],
                        "ledger_actions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            completed = self.run_script(project_root, "--prepass-json", str(prepass_path), check=False)
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assert_failure_contract(result)
            self.assertIn("gaps must be an array", result["details"])

    def test_missing_memory_root_blocks_with_kickoff_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            missing_memory = project_root / "missing-memory"
            prepass_path = project_root / "prepass.json"
            prepass_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 2,
                        "project_root": str(project_root),
                        "memory_root": str(missing_memory),
                        "sources_read": [],
                        "missing_sources": [],
                        "workstreams": [],
                        "gaps": [],
                        "ledger_actions": [],
                        "counts": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            memlog = project_root / "trail" / ".memlog.md"
            completed = self.run_script(
                project_root,
                "--prepass-json",
                str(prepass_path),
                "--headless",
                "--memlog",
                str(memlog),
                check=False,
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assert_failure_contract(result, headless=True)
            self.assertIn("adp-project-kickoff", result["recommended_workflows"])

    def test_prepass_failure_has_complete_blocked_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memlog = project_root / "trail" / ".memlog.md"
            completed = self.run_script(
                project_root,
                "--prepass-script",
                str(project_root / "missing-prepass.py"),
                "--headless",
                "--memlog",
                str(memlog),
                check=False,
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assert_failure_contract(result, headless=True)
            self.assertIn("prepass script not found", result["error"])

    def test_contradictory_prepass_process_results_are_blocked(self) -> None:
        cases = [
            ({"ok": True}, 1),
            ({"ok": True, "status": "blocked"}, 0),
            ({"ok": True, "status": "error"}, 0),
        ]
        for payload, exit_code in cases:
            with self.subTest(payload=payload, exit_code=exit_code), tempfile.TemporaryDirectory() as temp_dir:
                project_root = Path(temp_dir)
                prepass_script = project_root / "fake-prepass.py"
                prepass_script.write_text(
                    "import json\n"
                    f"print(json.dumps({payload!r}))\n"
                    f"raise SystemExit({exit_code})\n",
                    encoding="utf-8",
                )

                completed = self.run_script(
                    project_root,
                    "--prepass-script",
                    str(prepass_script),
                    check=False,
                )
                result = json.loads(completed.stdout)

                self.assertEqual(completed.returncode, 1)
                self.assert_failure_contract(result)

    def test_missing_and_malformed_prepass_json_have_complete_error_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            malformed = project_root / "malformed.json"
            malformed.write_text("{not-json", encoding="utf-8")
            for prepass_path in [project_root / "missing.json", malformed]:
                with self.subTest(prepass_path=prepass_path):
                    memlog = project_root / f"{prepass_path.stem}-trail" / ".memlog.md"
                    completed = self.run_script(
                        project_root,
                        "--prepass-json",
                        str(prepass_path),
                        "--headless",
                        "--memlog",
                        str(memlog),
                        check=False,
                    )
                    result = json.loads(completed.stdout)

                    self.assertEqual(completed.returncode, 2)
                    self.assert_failure_contract(result, headless=True)
                    self.assertIn("adp-agent-program-lead", result["recommended_workflows"])

    def test_invalid_project_root_and_date_have_complete_headless_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid_root = root / "missing-project"
            cases = [
                (invalid_root, [], "invalid-root"),
                (root, ["--as-of", "2026-99-99"], "invalid-date"),
            ]
            for project_root, extra_args, label in cases:
                with self.subTest(label=label):
                    memlog = root / label / ".memlog.md"
                    completed = self.run_script(
                        project_root,
                        *extra_args,
                        "--headless",
                        "--memlog",
                        str(memlog),
                        check=False,
                    )
                    result = json.loads(completed.stdout)

                    self.assertEqual(completed.returncode, 2)
                    self.assert_failure_contract(result, headless=True)

    def test_unwritable_output_target_has_complete_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)
            output_file = project_root / "not-a-directory"
            output_file.write_text("occupied", encoding="utf-8")
            memlog = project_root / "trail" / ".memlog.md"

            completed = self.run_script(
                project_root,
                "--output-dir",
                str(output_file),
                "--headless",
                "--memlog",
                str(memlog),
                check=False,
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 2)
            self.assert_failure_contract(result, headless=True)
            self.assertIn("cannot write audit outputs", result["error"])

    def test_argument_errors_use_the_stable_json_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            commands = [
                [sys.executable, str(SCRIPT)],
                [sys.executable, str(SCRIPT), str(project_root), "--scenario", "invalid"],
                [sys.executable, str(SCRIPT), str(project_root), "--max-age-days", "many"],
            ]
            for command in commands:
                with self.subTest(command=command):
                    completed = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                    )
                    result = json.loads(completed.stdout)

                    self.assertEqual(completed.returncode, 2)
                    self.assert_failure_contract(
                        result,
                        scenario="invalid" if "invalid" in command else "global",
                    )

    def test_headless_argument_error_returns_a_readable_memlog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memlog = project_root / "trail" / ".memlog.md"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--headless",
                    "--max-age-days",
                    "many",
                    "--memlog",
                    str(memlog),
                ],
                cwd=project_root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            result = json.loads(completed.stdout)
            trail = Path(result["memlog"]).read_text(encoding="utf-8")

            self.assertEqual(completed.returncode, 2)
            self.assert_failure_contract(result, headless=True)
            self.assertIn("(event) invalid arguments:", trail)

    def test_memlog_initialization_failures_return_existing_trails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            occupied = project_root / "not-a-directory"
            occupied.write_text("occupied", encoding="utf-8")
            invalid_memlog = occupied / ".memlog.md"

            invalid_destination = self.run_script(
                project_root,
                "--headless",
                "--memlog",
                str(invalid_memlog),
                check=False,
            )
            invalid_result = json.loads(invalid_destination.stdout)

            self.assert_failure_contract(invalid_result, headless=True)
            self.assertNotEqual(Path(invalid_result["memlog"]), invalid_memlog)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            helper = project_root / "_bmad" / "scripts" / "memlog.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("raise SystemExit(2)\n", encoding="utf-8")
            memlog = project_root / "trail" / ".memlog.md"

            helper_failure = self.run_script(
                project_root,
                "--headless",
                "--memlog",
                str(memlog),
                check=False,
            )
            helper_result = json.loads(helper_failure.stdout)
            trail = Path(helper_result["memlog"]).read_text(encoding="utf-8")

            self.assert_failure_contract(helper_result, headless=True)
            self.assertEqual(Path(helper_result["memlog"]), memlog)
            self.assertIn("Memlog helper initialization failed", trail)

    def test_headless_success_returns_audit_trail_with_effective_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.scaffold(project_root)
            memlog = project_root / "trail" / ".memlog.md"

            completed = self.run_script(
                project_root,
                "--headless",
                "--memlog",
                str(memlog),
                "--execution-mode",
                "python-fallback",
            )
            result = json.loads(completed.stdout)
            trail = memlog.read_text(encoding="utf-8")

            self.assertTrue(result["ok"])
            self.assertEqual(result["memlog"], str(memlog))
            self.assertIn("(assumption) Resolved headless scope and effective audit parameters", trail)
            self.assertIn("(decision) Resolved headless execution and output routing", trail)
            self.assertIn('"execution_mode": "python-fallback"', trail)
            self.assertIn('"fallback_reason": "uv executable unavailable"', trail)
            self.assertIn('"python_version":', trail)
            self.assertIn('"executable":', trail)


if __name__ == "__main__":
    unittest.main()
