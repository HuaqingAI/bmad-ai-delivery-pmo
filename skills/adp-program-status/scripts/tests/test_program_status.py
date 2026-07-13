import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "program_status.py"
AUDIT_SCRIPT = Path(__file__).resolve().parents[3] / "adp-state-audit/scripts/audit_state.py"
BASELINE_MARKER = "<!-- adp:program-baseline:v1 -->"
AUDIT_GLOBALS = runpy.run_path(str(AUDIT_SCRIPT))
STABLE_INPUT_AUDIT_ID = AUDIT_GLOBALS["stable_input_audit_id"]
AUDIT_CONTENT_HASH = AUDIT_GLOBALS["audit_content_hash"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProgramStatusTests(unittest.TestCase):
    def run_script(self, project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def baseline(
        self,
        *,
        tolerance: int = 0,
        milestones: list[dict] | None = None,
        gates: list[dict] | None = None,
        critical_path: list[str] | None = None,
        weighting: dict | None = None,
    ) -> dict:
        source = {"type": "approved-plan", "reference": "docs/plan.md", "confirmed_by": "Program Owner"}
        default_milestone = {
            "id": "MS-ONE",
            "name": "Milestone one",
            "workstream_id": "ws-one",
            "planned_date": "2026-07-20",
            "owner": "FDE One",
            "confirmation_status": "approved",
            "source": source,
            "dependencies": [],
            "baseline_revision": 1,
            "critical_path": True,
        }
        items = milestones if milestones is not None else [default_milestone]
        return {
            "schema_version": "1.0",
            "baseline_id": "PROGRAM-BASELINE",
            "revision": 1,
            "confirmation_status": "approved",
            "project": {
                "name": "Test Project",
                "owner": "Program Owner",
                "target_date": "2026-12-31",
                "source": source,
            },
            "default_tolerance_days": tolerance,
            "gates": gates or [],
            "milestones": items,
            "critical_path": critical_path if critical_path is not None else [item["id"] for item in items if item.get("critical_path")],
            "weighting": weighting or {"enabled": False, "completion_measure": None, "source": None},
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        }

    def scaffold(self, root: Path, baseline: dict, rows_by_workstream: dict[str, list[dict]] | None = None, language: str = "English") -> Path:
        memory = root / "_bmad-output/adp/memory"
        (memory / "plans").mkdir(parents=True)
        config = root / "_bmad/bmb/config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "\n".join(
                [
                    "communication_language: English",
                    f"document_output_language: {language}",
                    "output_folder: '{project-root}/_bmad-output'",
                    "default_reporting_cadence: weekly",
                    "status_stale_after_days: 7",
                    "schedule_variance_tolerance_days: 0",
                    "meeting_pack_item_limit: 10",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        baseline_path = memory / "plans/program-baseline.md"
        baseline_path.write_text(
            "# Program Baseline\n\n" + BASELINE_MARKER + "\n\n```json\n" + json.dumps(baseline, indent=2) + "\n```\n",
            encoding="utf-8",
        )
        rows_by_workstream = rows_by_workstream or {}
        workstream_ids = sorted({item["workstream_id"] for item in baseline["milestones"]})
        for workstream_id in workstream_ids:
            rows = rows_by_workstream.get(workstream_id, [])
            path = memory / "workstreams" / workstream_id / "delivery-record.md"
            path.parent.mkdir(parents=True)
            lines = [
                "# Workstream Delivery Record",
                "",
                "## Roadmap",
                "",
                "| Milestone ID | Milestone | Type | Status | Planned | Forecast | Actual | Owner | Confidence | Depends On | Source | Baseline Revision |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                values = [
                    row.get("id", "TBD"),
                    row.get("name", row.get("id", "TBD")),
                    "checkpoint",
                    row.get("status", "planned"),
                    row.get("planned", "TBD"),
                    row.get("forecast", "TBD"),
                    row.get("actual", "TBD"),
                    "FDE",
                    "high",
                    "TBD",
                    row.get("source", f"workstreams/{workstream_id}/evidence.md#status"),
                    str(row.get("revision", 1)),
                ]
                lines.append("| " + " | ".join(values) + " |")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return memory

    def write_audit(
        self,
        root: Path,
        memory: Path,
        *,
        as_of: str = "2026-07-13",
        disposition: str = "ready",
        confidence: str = "high",
        name: str = "input-audit.json",
        locale: str = "en",
    ) -> Path:
        sources = [memory / "plans/program-baseline.md", root / "_bmad/bmb/config.yaml"]
        sources.extend(sorted((memory / "workstreams").glob("*/delivery-record.md")))
        fingerprints = {path.relative_to(root).as_posix(): sha256(path) for path in sources}
        audit = {
            "audit_type": "input",
            "audit_schema_version": 1,
            "schema_version": 1,
            "generator_version": "2.0.0",
            "scenario": "global",
            "as_of": as_of,
            "baseline_revision": 1,
            "execution_disposition": disposition,
            "audit_status": "blocked" if disposition == "blocked" else ("warning" if disposition == "degraded" else "pass"),
            "safe_to_generate": disposition != "blocked",
            "safe_to_generate_green_report": disposition == "ready",
            "report_confidence": confidence,
            "locale": locale,
            "locale_fallback": False,
            "source_fingerprints": fingerprints,
            "blocking_gaps": [],
            "warnings": [],
            "recommended_workflows": [],
        }
        audit["input_audit_id"] = STABLE_INPUT_AUDIT_ID(audit)
        audit["audit_content_hash"] = AUDIT_CONTENT_HASH(audit)
        path = root / name
        path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
        return path

    def write_real_audit(self, root: Path, memory: Path) -> Path:
        prepass = root / "prepass.json"
        prepass.write_text(
            json.dumps(
                {
                    "ok": True,
                    "schema_version": 2,
                    "project_root": str(root),
                    "memory_root": str(memory),
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
        audited = subprocess.run(
            [
                sys.executable,
                str(AUDIT_SCRIPT),
                str(root),
                "--prepass-json",
                str(prepass),
                "--as-of",
                "2026-07-13",
                "--output-dir",
                str(root / "audits"),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return Path(json.loads(audited.stdout)["outputs"]["json"])

    def generate(
        self,
        root: Path,
        audit: Path,
        *,
        as_of: str = "2026-07-13",
        period_start: str = "2026-07-07",
        signals: Path | None = None,
        check: bool = True,
        extra: list[str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        args = [
            "--input-audit-json",
            str(audit),
            "--as-of",
            as_of,
            "--period-start",
            period_start,
            "--period-end",
            as_of,
            "--generated-at",
            f"{as_of}T12:00:00Z",
        ]
        if signals:
            args.extend(["--signals-json", str(signals)])
        if extra:
            args.extend(extra)
        completed = self.run_script(root, *args, check=check)
        return completed, json.loads(completed.stdout)

    def test_on_plan_when_forecast_meets_approved_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = self.baseline()
            memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_audit(root, memory)

            _, result = self.generate(root, audit)
            model = json.loads(Path(result["outputs"]["snapshot"]).read_text(encoding="utf-8"))

            self.assertEqual(model["overall_status"], "on-plan")
            self.assertEqual(model["report_confidence"], "high")
            self.assertEqual(model["overall_rule_id"], "PS-OVERALL-CRITICAL-ON-PLAN")
            self.assertIsNone(model["progress"]["weighted_completion_percent"])

    def test_forecast_inside_tolerance_is_at_risk_and_outside_is_off_plan(self) -> None:
        cases = [("2026-07-22", "at-risk"), ("2026-07-26", "off-plan")]
        for forecast, expected in cases:
            with self.subTest(forecast=forecast), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                baseline = self.baseline(tolerance=5)
                memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "forecast": forecast}]})
                audit = self.write_audit(root, memory)
                _, result = self.generate(root, audit)

                self.assertEqual(result["overall_status"], expected)

    def test_confirmed_off_plan_is_not_hidden_by_unknown_or_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.baseline()["milestones"][0]
            second = {
                **first,
                "id": "MS-TWO",
                "name": "Milestone two",
                "workstream_id": "ws-two",
                "planned_date": "2026-07-01",
            }
            baseline = self.baseline(milestones=[first, second])
            memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-30"}]})
            audit = self.write_audit(root, memory, confidence="low", disposition="degraded")

            _, result = self.generate(root, audit)
            model = json.loads(Path(result["outputs"]["snapshot"]).read_text(encoding="utf-8"))

            self.assertEqual(model["overall_status"], "off-plan")
            self.assertEqual(model["report_confidence"], "low")
            self.assertEqual({item["status"] for item in model["critical_path"]}, {"off-plan", "indeterminate", "on-plan"})

    def test_source_backed_at_risk_precedes_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.baseline()["milestones"][0]
            second = {**first, "id": "MS-TWO", "workstream_id": "ws-two", "planned_date": "2026-07-01"}
            baseline = self.baseline(milestones=[first, second])
            memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "status": "at-risk"}]})
            audit = self.write_audit(root, memory)

            _, result = self.generate(root, audit)

            self.assertEqual(result["overall_status"], "at-risk")

    def test_past_due_without_actual_or_forecast_is_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            item = self.baseline()["milestones"][0]
            item["planned_date"] = "2026-07-01"
            memory = self.scaffold(root, self.baseline(milestones=[item]))
            audit = self.write_audit(root, memory)

            _, result = self.generate(root, audit)

            self.assertEqual(result["overall_status"], "indeterminate")
            self.assertEqual(result["report_confidence"], "low")

    def test_noncritical_off_plan_promotes_overall_to_at_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            item = self.baseline()["milestones"][0]
            item["critical_path"] = False
            baseline = self.baseline(milestones=[item], critical_path=[])
            memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-30"}]})
            audit = self.write_audit(root, memory)

            _, result = self.generate(root, audit)

            self.assertEqual(result["overall_status"], "at-risk")

    def test_blocked_or_stale_audit_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = self.baseline()
            memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            blocked = self.write_audit(root, memory, disposition="blocked", name="blocked.json")
            completed, result = self.generate(root, blocked, check=False)
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse((memory / "snapshots/program-status").exists())

            stale = self.write_audit(root, memory, name="stale.json")
            wdr = memory / "workstreams/ws-one/delivery-record.md"
            wdr.write_text(wdr.read_text(encoding="utf-8") + "\nChanged after audit.\n", encoding="utf-8")
            completed, result = self.generate(root, stale, check=False)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("fingerprint is stale", result["reason"])
            self.assertFalse((memory / "snapshots/program-status").exists())

    def test_tampered_input_audit_is_blocked_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_audit(root, memory)
            payload = json.loads(audit.read_text(encoding="utf-8"))
            payload["report_confidence"] = "low"
            audit.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

            completed, result = self.generate(root, audit, check=False)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("integrity validation failed", result["reason"])
            self.assertFalse((memory / "snapshots/program-status").exists())

    def test_snapshot_is_stable_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_audit(root, memory)
            _, first = self.generate(root, audit)
            snapshot = Path(first["outputs"]["snapshot"])
            original = snapshot.read_bytes()

            _, second = self.generate(root, audit, extra=["--generated-at", "2026-07-13T18:00:00Z"])

            self.assertEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertTrue(second["snapshot_reused"])
            self.assertEqual(snapshot.read_bytes(), original)
            history = list((memory / "snapshots/program-status").glob("ps-*.json"))
            self.assertEqual(len(history), 1)

    def test_period_delta_reports_worsening(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            first_audit = self.write_audit(root, memory, as_of="2026-07-13", name="audit-one.json")
            self.generate(root, first_audit)
            wdr = memory / "workstreams/ws-one/delivery-record.md"
            wdr.write_text(wdr.read_text(encoding="utf-8").replace("2026-07-20", "2026-07-30"), encoding="utf-8")
            second_audit = self.write_audit(root, memory, as_of="2026-07-20", name="audit-two.json")

            _, second = self.generate(root, second_audit, as_of="2026-07-20", period_start="2026-07-14")
            model = json.loads(Path(second["outputs"]["snapshot"]).read_text(encoding="utf-8"))

            self.assertEqual(model["period_delta"]["comparison_status"], "compared")
            self.assertIn("milestone:MS-ONE", model["period_delta"]["worsened"])
            self.assertEqual(model["period_delta"]["overall_change"], {"from": "on-plan", "to": "off-plan"})

    def test_chinese_rendering_preserves_canonical_machine_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]}, language="Chinese")
            audit = self.write_audit(root, memory, locale="zh")

            _, result = self.generate(root, audit)
            model = json.loads(Path(result["outputs"]["program_status_json"]).read_text(encoding="utf-8"))
            markdown = Path(result["outputs"]["program_status_markdown"]).read_text(encoding="utf-8")
            weekly = Path(result["outputs"]["weekly_report"]).read_text(encoding="utf-8")
            project_lead = Path(result["outputs"]["project_lead"]).read_text(encoding="utf-8")

            self.assertEqual(model["overall_status"], "on-plan")
            self.assertEqual(model["locale"], "zh")
            self.assertEqual(model["overall_status_label"], "按计划")
            self.assertEqual(model["render_contract"]["coverage_profile"], "adp-program-status-json")
            self.assertTrue(any(key.startswith("status.confidence_reason.") for key in model["render_contract"]["message_keys"]))
            self.assertTrue(any(key.startswith("status.progress_reason.") for key in model["render_contract"]["message_keys"]))
            self.assertIn("# ADP 项目总体状态", markdown)
            self.assertNotIn("# ADP Program Status", markdown)
            visible = "\n".join([markdown, weekly, project_lead])
            self.assertIn("快照 ID", visible)
            self.assertIn("输入审计 ID", visible)
            self.assertIn("生成器版本", markdown)
            self.assertIn("基线修订", project_lead)
            self.assertNotIn("Snapshot ID", visible)
            self.assertNotIn("Input audit ID", visible)
            self.assertNotIn("Generator version", visible)
            self.assertNotIn("Baseline revision", visible)

            artifact_args: list[str] = []
            for key in ["snapshot", "program_status_json", "program_status_markdown", "weekly_report", "project_lead"]:
                artifact_args.extend(["--artifact", result["outputs"][key]])
            validated = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    str(root),
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory),
                    "--input-audit-json",
                    str(audit),
                    "--as-of",
                    "2026-07-13",
                    *artifact_args,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            validation = json.loads(validated.stdout)
            self.assertEqual(validated.returncode, 0, validation)
            self.assertTrue(validation["safe_to_publish"], validation)

    def test_source_backed_readiness_signal_changes_overall_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            evidence = memory / "views/acceptance-readiness.md"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("# Readiness\n\nPayment evidence incomplete.\n", encoding="utf-8")
            signals = root / "signals.json"
            signals.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "signals": [
                            {
                                "id": "SIG-READINESS",
                                "constraint_type": "readiness",
                                "status": "at-risk",
                                "critical": True,
                                "summary": "Payment evidence incomplete",
                                "source": {"type": "readiness", "reference": evidence.relative_to(root).as_posix()},
                                "baseline_revision": 1,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            audit = self.write_audit(root, memory)

            _, result = self.generate(root, audit, signals=signals)
            model = json.loads(Path(result["outputs"]["snapshot"]).read_text(encoding="utf-8"))

            self.assertEqual(model["overall_status"], "at-risk")
            self.assertIn(evidence.relative_to(root).as_posix(), model["source_fingerprints"])
            self.assertIn("PS-SOURCE-BACKED-SIGNAL", model["rule_ids"])

    def test_explicit_gate_signal_resolves_unknown_and_unknown_targets_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = {"type": "approved-plan", "reference": "docs/plan.md", "confirmed_by": "Program Owner"}
            gate = {
                "id": "GATE-ONE",
                "name": "Gate one",
                "planned_date": "2026-07-01",
                "owner": "Program Owner",
                "confirmation_status": "approved",
                "source": source,
                "dependencies": [],
                "baseline_revision": 1,
                "critical_path": True,
            }
            baseline = self.baseline(milestones=[], gates=[gate], critical_path=["GATE-ONE"])
            memory = self.scaffold(root, baseline)
            audit = self.write_audit(root, memory)
            signals = root / "gate-signals.json"
            payload = {
                "schema_version": "1.0",
                "signals": [
                    {
                        "id": "SIG-GATE",
                        "constraint_type": "gate",
                        "constraint_id": "GATE-ONE",
                        "status": "on-plan",
                        "critical": True,
                        "summary": "Gate approved",
                        "source": {"type": "decision", "reference": "docs/gate.md#approved"},
                        "baseline_revision": 1,
                    }
                ],
            }
            signals.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            _, result = self.generate(root, audit, signals=signals)
            self.assertEqual(result["overall_status"], "on-plan")

            payload["signals"][0]["constraint_id"] = "GATE-UNKNOWN"
            signals.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            completed, failed = self.generate(root, audit, signals=signals, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("unknown baseline gate", failed["reason"])

    def test_weighted_progress_requires_approved_weighting_and_actual(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            item = self.baseline()["milestones"][0]
            item["weight"] = 100
            item["completion_criteria"] = "Accepted evidence is linked"
            weighting = {
                "enabled": True,
                "completion_measure": "accepted milestone weight",
                "source": {"type": "approved-plan", "reference": "docs/weights.md", "confirmed_by": "Program Owner"},
            }
            baseline = self.baseline(milestones=[item], weighting=weighting)
            memory = self.scaffold(root, baseline, {"ws-one": [{"id": "MS-ONE", "status": "done", "actual": "2026-07-19"}]})
            audit = self.write_audit(root, memory)

            _, result = self.generate(root, audit)
            model = json.loads(Path(result["outputs"]["snapshot"]).read_text(encoding="utf-8"))

            self.assertEqual(model["progress"]["weighted_completion_percent"], 100.0)

    def test_dry_run_and_inspect_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_audit(root, memory)
            _, dry = self.generate(root, audit, extra=["--dry-run"])
            self.assertTrue(dry["dry_run"])
            self.assertFalse((memory / "snapshots/program-status").exists())
            self.generate(root, audit)

            inspected = self.run_script(root, "--mode", "inspect")
            result = json.loads(inspected.stdout)
            self.assertEqual(result["status"], "inspected")
            self.assertEqual(result["overall_status"], "on-plan")
            self.assertEqual(result["input_audit_id"], json.loads(audit.read_text(encoding="utf-8"))["input_audit_id"])
            self.assertEqual(result["locale"], "en")
            self.assertEqual(result["fallbacks"], [])
            self.assertIn("snapshot", result["outputs"])
            self.assertIn("recommended_workflows", result)

    def test_headless_generation_and_inspection_close_artifact_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_real_audit(root, memory)
            generate_memlog = root / "generate.memlog.md"

            completed, result = self.generate(
                root,
                audit,
                extra=["--headless", "--memlog", str(generate_memlog)],
            )

            self.assertEqual(completed.returncode, 0, result)
            self.assertEqual(result["status"], "complete")
            self.assertTrue(result["safe_to_publish"])
            self.assertTrue(result["artifact_validation_id"])
            self.assertIn("json", result["artifact_validation_reports"])
            self.assertEqual(result["input_audit_id"], json.loads(audit.read_text(encoding="utf-8"))["input_audit_id"])
            self.assertEqual(Path(result["memlog"]), generate_memlog)
            memlog_text = generate_memlog.read_text(encoding="utf-8")
            self.assertIn("status: complete", memlog_text)
            self.assertIn("(assumption)", memlog_text)
            self.assertIn("(decision)", memlog_text)

            inspect_memlog = root / "inspect.memlog.md"
            inspected = self.run_script(
                root,
                "--mode",
                "inspect",
                "--headless",
                "--memlog",
                str(inspect_memlog),
            )
            inspection = json.loads(inspected.stdout)
            self.assertEqual(inspection["status"], "complete")
            self.assertTrue(inspection["safe_to_publish"])
            self.assertEqual(inspection["input_audit_id"], result["input_audit_id"])
            self.assertEqual(inspection["locale"], "en")
            self.assertIn("snapshot", inspection["outputs"])

    def test_headless_blocks_when_artifact_validation_cannot_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_audit(root, memory)
            missing = root / "missing-audit-state.py"

            completed, result = self.generate(
                root,
                audit,
                check=False,
                extra=["--headless", "--artifact-audit-script", str(missing)],
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["safe_to_publish"])
            self.assertEqual(result["dependency_name"], "adp-state-audit artifact validator")
            self.assertEqual(result["missing_path"], str(missing))
            self.assertEqual(result["recommended_workflows"], ["adp-setup", "adp-state-audit"])
            self.assertTrue(Path(result["memlog"]).is_file())

    def test_headless_returns_blocked_artifact_audit_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit = self.write_audit(root, memory)
            validator = root / "blocking-artifact-validator.py"
            validator.write_text(
                "import json\n"
                "def validate_input_audit_integrity(audit):\n"
                "    return []\n"
                "if __name__ == '__main__':\n"
                "    print(json.dumps({'ok': True, 'safe_to_publish': False, 'artifact_validation_id': 'forced-block', 'outputs': {'json': 'forced-block.json'}, 'recommended_workflows': ['owning artifact workflow'], 'reason': 'forced artifact validation block'}))\n",
                encoding="utf-8",
            )

            completed, result = self.generate(
                root,
                audit,
                check=False,
                extra=["--headless", "--artifact-audit-script", str(validator)],
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["safe_to_publish"])
            self.assertTrue(result["artifact_validation_id"])
            self.assertIn("json", result["artifact_validation_reports"])
            self.assertIn("owning artifact workflow", result["recommended_workflows"])
            self.assertEqual(result["outputs"], {})
            self.assertIn("snapshot", result["staged_outputs"])
            self.assertFalse((memory / "snapshots/program-status").exists())
            self.assertFalse((memory / "views/program-status.json").exists())

    def test_missing_sibling_script_returns_dependency_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline())
            audit = self.write_audit(root, memory)
            missing = root / "missing-effective-config.py"

            completed, result = self.generate(root, audit, check=False, extra=["--config-script", str(missing)])

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["dependency_name"], "adp-plan-baseline effective config")
            self.assertEqual(result["missing_path"], str(missing))
            self.assertEqual(result["recommended_workflows"], ["adp-setup"])

    def test_consumes_real_state_audit_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = self.scaffold(root, self.baseline(), {"ws-one": [{"id": "MS-ONE", "forecast": "2026-07-20"}]})
            audit_json = self.write_real_audit(root, memory)

            _, result = self.generate(root, audit_json, check=False)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["overall_status"], "on-plan")
            self.assertEqual(result["input_audit_id"], json.loads(audit_json.read_text(encoding="utf-8"))["input_audit_id"])
            artifact_args = []
            for key in ["snapshot", "program_status_json", "program_status_markdown", "weekly_report", "project_lead"]:
                artifact_args.extend(["--artifact", result["outputs"][key]])
            validated = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    str(root),
                    "--phase",
                    "artifact",
                    "--memory-root",
                    str(memory),
                    "--input-audit-json",
                    str(audit_json),
                    "--as-of",
                    "2026-07-13",
                    *artifact_args,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            validation = json.loads(validated.stdout)
            self.assertEqual(validated.returncode, 0, validation)
            self.assertTrue(validation["safe_to_publish"], validation)


if __name__ == "__main__":
    unittest.main()
