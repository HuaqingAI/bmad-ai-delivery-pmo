import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "panel_refresh.py"
MEMORY_REL = Path("_bmad-output/adp/memory")


def load_module():
    spec = importlib.util.spec_from_file_location("adp_panel_refresh_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load panel_refresh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_content_id_without_receipt(payload: dict) -> str:
    module = load_module()
    body = dict(payload)
    body.pop("receipt_id", None)
    return module.content_id(body)


class PanelRefreshTests(unittest.TestCase):
    def scaffold(self, root: Path) -> Path:
        memory = root / MEMORY_REL
        memory.mkdir(parents=True)
        return memory

    def run_cli(self, *args: str, check: bool = True) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed, json.loads(completed.stdout)

    def plan(self, root: Path) -> dict:
        completed, result = self.run_cli(
            "plan",
            str(root),
            "--fixture",
            "--force-full",
            "--as-of",
            "2026-07-30",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, result)
        self.assertTrue(result["ok"])
        return result

    def make_staged_audit_ready(self, root: Path, plan: dict) -> None:
        module = load_module()
        memory = root / MEMORY_REL
        staged = module.workspace_for(memory.resolve(), plan["refresh_id"]) / "memory"
        audit_path = next((staged / "audits/management-panel").glob("panel-input-audit-*.json"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit.update(
            {
                "ok": True,
                "audit_status": "pass",
                "execution_disposition": "ready",
                "safe_to_generate": True,
                "source_fingerprints": module.source_inventory(staged),
                "warnings": [],
            }
        )
        audit.setdefault("counts", {})["blocking_findings"] = 0
        audit["counts"]["action_projection_drift"] = 0
        audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def apply_ready_fixture(self, root: Path, plan: dict) -> dict:
        completed, crashed = self.run_cli(
            "apply",
            str(root),
            "--plan",
            plan["plan_path"],
            "--fail-after-node",
            "management-panel",
            check=False,
        )
        self.assertEqual(completed.returncode, 1, crashed)
        self.assertEqual(crashed["error_code"], "INJECTED_REFRESH_CRASH")
        self.make_staged_audit_ready(root, plan)
        resumed, result = self.run_cli("apply", str(root), "--plan", plan["plan_path"], check=False)
        self.assertEqual(resumed.returncode, 0, result)
        return result

    def scaffold_policy_sources(self, memory: Path) -> dict:
        graph = {
            "flow_graph_id": "flow-001",
            "topology": {
                "nodes": [{"node_id": "N-1"}, {"node_id": "N-2"}],
                "edges": [{"edge_id": "E-1", "predecessor": "N-1", "target": "N-2"}],
            },
            "overlays": {"scopes": [{"scope_id": "S-1", "allocations": []}]},
        }
        (memory / "views").mkdir(parents=True, exist_ok=True)
        (memory / "views/flow-graph.json").write_text(json.dumps(graph), encoding="utf-8")
        (memory / "views/program-status.json").write_text(
            json.dumps({"snapshot_id": "PS-CURRENT", "as_of": "2026-07-30"}),
            encoding="utf-8",
        )
        history = memory / "snapshots/program-status"
        history.mkdir(parents=True, exist_ok=True)
        for index, snapshot_id in enumerate(("PS-HISTORY-1", "PS-HISTORY-2"), start=1):
            (history / f"history-{index}.json").write_text(
                json.dumps({"snapshot_id": snapshot_id, "as_of": f"2026-07-{index:02d}"}),
                encoding="utf-8",
            )
        return graph

    def test_fixture_refresh_publishes_and_inspects_fresh_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            plan = self.plan(root)

            applied = self.apply_ready_fixture(root, plan)

            self.assertEqual(applied["status"], "published")
            self.assertTrue((memory / "views/management-panel/index.html").is_file())
            self.assertEqual(applied["inspect"]["artifact_integrity"], "pass")
            self.assertEqual(applied["inspect"]["business_freshness"], "fresh")
            self.assertEqual(applied["inspect"]["publication_eligibility"], "eligible")
            self.assertEqual(applied["inspect"]["pending_intent_ids"], [])
            self.assertEqual(applied["inspect"]["drift_count"], 0)
            self.assertEqual(
                applied["receipt_id"],
                module_content_id_without_receipt(applied),
            )

            (memory / "actions/action-ledger.md").write_text("# Changed after publication\n", encoding="utf-8")
            _, inspected = self.run_cli("inspect", str(root), check=False)
            self.assertEqual(inspected["artifact_integrity"], "pass")
            self.assertEqual(inspected["business_freshness"], "stale")
            self.assertEqual(inspected["publication_eligibility"], "blocked")
            self.assertEqual(inspected["changed_sources"], ["actions/action-ledger.md"])
            self.assertEqual(inspected["recommended_workflows"], ["adp-panel-refresh"])

    def test_completed_node_resumes_after_injected_crash_without_early_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            plan = self.plan(root)

            crashed_completed, crashed = self.run_cli(
                "apply",
                str(root),
                "--plan",
                plan["plan_path"],
                "--fail-after-node",
                "management-panel",
                check=False,
            )
            self.assertEqual(crashed_completed.returncode, 1)
            self.assertEqual(crashed["error_code"], "INJECTED_REFRESH_CRASH")
            self.assertFalse((memory / "views/management-panel/index.html").exists())
            durable_plan = json.loads(Path(plan["plan_path"]).read_text(encoding="utf-8"))
            self.assertEqual(durable_plan["nodes"][0]["status"], "completed")

            self.make_staged_audit_ready(root, plan)
            _, resumed = self.run_cli("apply", str(root), "--plan", plan["plan_path"])
            self.assertEqual(resumed["status"], "published")
            self.assertEqual(resumed["inspect"]["publication_eligibility"], "eligible")
            self.assertTrue((memory / "views/management-panel/index.html").is_file())

    def test_pending_intents_block_plan_before_any_panel_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            outbox = memory / "state/status-intent-outbox.json"
            outbox.parent.mkdir(parents=True)
            intent = {
                "intent_id": "intent-001",
                "workstream_id": "l1-checkout",
                "set": {"progress": "80%"},
            }
            outbox_payload = {
                "schema_version": "1.0.0",
                "pending": ["intent-001"],
                "consumed": [],
                "failed": [],
                "waived": [],
                "intents": [
                    {
                        "intent_id": "intent-001",
                        "state": "pending",
                        "payload_hash": load_module().content_id(intent),
                        "intent": intent,
                    }
                ],
            }
            outbox_payload["state_id"] = load_module().content_id(outbox_payload)
            outbox.write_text(json.dumps(outbox_payload), encoding="utf-8")

            completed, planned = self.run_cli(
                "plan",
                str(root),
                "--fixture",
                "--force-full",
                "--as-of",
                "2026-07-30",
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertFalse(planned["ok"])
            self.assertEqual(planned["status"], "blocked")
            self.assertEqual(planned["pending_intent_ids"], ["intent-001"])
            self.assertIn("pending status intents", planned["blocked_reasons"][0])
            self.assertFalse((memory / "views/management-panel/index.html").exists())

    def test_malformed_status_intent_outbox_blocks_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            outbox = memory / "state/status-intent-outbox.json"
            outbox.parent.mkdir(parents=True)
            outbox.write_text("{malformed", encoding="utf-8")

            completed, result = self.run_cli("detect", str(root), check=False)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "STATUS_INTENT_OUTBOX_INVALID")

    def test_projection_drift_blocks_plan_and_routes_exact_audit_to_status_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            audit_path = memory / "audits/2026-07-30/input-audit.json"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(
                json.dumps(
                    {
                        "audit_type": "input",
                        "scenario": "management-panel",
                        "counts": {"action_projection_drift": 2},
                        "repair_contract": {
                            "findings": [
                                {
                                    "kind": "action-projection-drift",
                                    "action_ids": ["ACT-001"],
                                    "repair_batch_id": "batch-001",
                                },
                                {
                                    "kind": "action-projection-drift",
                                    "action_ids": ["ACT-002"],
                                    "repair_batch_id": "batch-002",
                                },
                            ],
                            "repair_batches": [
                                {
                                    "batch_id": "batch-001",
                                    "command": {
                                        "workflow": "adp-status-sync",
                                        "workstream_id": "l1-checkout",
                                        "action_ids": ["ACT-001"],
                                    },
                                },
                                {
                                    "batch_id": "batch-002",
                                    "command": {
                                        "workflow": "adp-status-sync",
                                        "workstream_id": "l2-search",
                                        "action_ids": ["ACT-002"],
                                    },
                                },
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            _, detected = self.run_cli("detect", str(root), "--fixture")
            self.assertEqual(detected["drift_count"], 2)
            self.assertEqual(detected["drift_action_ids"], ["ACT-001", "ACT-002"])
            self.assertEqual(
                [row["repair_batch_id"] for row in detected["repair_batches"]],
                ["batch-001", "batch-002"],
            )
            self.assertEqual(detected["drift_audit_path"], str(audit_path.resolve()))
            self.assertEqual(detected["recommended_mode"], "blocked")
            self.assertEqual(detected["recommended_workflows"], ["adp-status-sync"])
            _, inspected = self.run_cli("inspect", str(root), check=False)
            self.assertEqual(inspected["drift_action_ids"], ["ACT-001", "ACT-002"])
            self.assertEqual(
                [row["repair_batch_id"] for row in inspected["repair_batches"]],
                ["batch-001", "batch-002"],
            )

            completed, planned = self.run_cli(
                "plan",
                str(root),
                "--fixture",
                "--force-full",
                "--as-of",
                "2026-07-30",
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("action projection drift", planned["blocked_reasons"][0])

    def test_repaired_facts_make_prior_drift_audit_stale(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            ledger = memory / "actions/action-ledger.md"
            workstream = memory / "workstreams/l1-checkout"
            ledger.parent.mkdir(parents=True)
            workstream.mkdir(parents=True)
            ledger.write_text("# Action ledger\n", encoding="utf-8")
            wdr = workstream / "delivery-record.md"
            wdr_state = workstream / "delivery-record.state.json"
            sidecar = workstream / "action-projection.json"
            wdr.write_text("# Delivery record\n", encoding="utf-8")
            wdr_state.write_text("{}\n", encoding="utf-8")
            sidecar.write_text("{}\n", encoding="utf-8")
            audit_path = memory / "audits/2026-07-30/input-audit.json"
            audit_path.parent.mkdir(parents=True)
            audit_path.write_text(
                json.dumps(
                    {
                        "audit_type": "input",
                        "scenario": "global",
                        "counts": {"action_projection_drift": 1},
                        "action_projection_drift": {
                            "ledger_fingerprint": module.file_fingerprint(ledger),
                            "rows": [
                                {
                                    "workstream_id": "l1-checkout",
                                    "wdr_fingerprint": module.file_fingerprint(wdr),
                                    "wdr_state_fingerprint": module.file_fingerprint(wdr_state),
                                    "sidecar_fingerprint": module.file_fingerprint(sidecar),
                                }
                            ],
                        },
                        "repair_contract": {
                            "findings": [
                                {
                                    "kind": "action-projection-drift",
                                    "action_ids": ["ACT-001"],
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            current = module.detect(root.resolve(), memory.resolve(), fixture=True)
            self.assertFalse(current["drift_audit_stale"])
            self.assertEqual(current["drift_count"], 1)

            ledger.write_text("# Repaired action ledger\n", encoding="utf-8")
            repaired = module.detect(root.resolve(), memory.resolve(), fixture=True)

            self.assertTrue(repaired["drift_audit_stale"])
            self.assertEqual(repaired["drift_count"], 0)
            self.assertEqual(repaired["drift_action_ids"], [])
            self.assertEqual(repaired["repair_batches"], [])
            self.assertEqual(repaired["blocked_reasons"], [])

    def test_fact_repair_supersedes_source_stale_dirty_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            first = self.plan(root)
            first_path = Path(first["plan_path"])
            dirty = json.loads(first_path.read_text(encoding="utf-8"))
            dirty["status"] = "dirty"
            dirty["retry_from_instance_key"] = "management-panel"
            dirty["nodes"][0].update({"status": "blocked", "error": "stale fact input"})
            first_path.write_text(json.dumps(dirty), encoding="utf-8")
            status_path = memory / "state/panel-refresh-status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update(
                {
                    "current_run_id": first["refresh_id"],
                    "current_status": "dirty",
                    "retry_from_instance_key": "stale-retry-node",
                }
            )
            status_path.write_text(json.dumps(status), encoding="utf-8")
            workspace_marker = (
                memory.parent
                / ".adp-panel-refresh-staging"
                / first["refresh_id"]
                / "failed-node.txt"
            )
            workspace_marker.parent.mkdir(parents=True)
            workspace_marker.write_text("preserve evidence\n", encoding="utf-8")

            repaired_fact = memory / "actions/action-ledger.md"
            repaired_fact.parent.mkdir(parents=True)
            repaired_fact.write_text("# Repaired facts\n", encoding="utf-8")
            replacement = self.plan(root)

            self.assertNotEqual(replacement["refresh_id"], first["refresh_id"])
            self.assertEqual(replacement["superseded_refresh_id"], first["refresh_id"])
            self.assertEqual(replacement["superseded_plan_path"], str(first_path.resolve()))
            superseded = json.loads(first_path.read_text(encoding="utf-8"))
            self.assertEqual(superseded["status"], "superseded")
            self.assertIsNone(superseded["retry_from_instance_key"])
            self.assertEqual(
                superseded["superseded_by_refresh_id"], replacement["refresh_id"]
            )
            self.assertEqual(superseded["nodes"][0]["error"], "stale fact input")
            self.assertTrue(workspace_marker.is_file())
            current_status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(current_status["current_run_id"], replacement["refresh_id"])
            self.assertEqual(
                current_status["retry_from_instance_key"],
                replacement["retry_from_instance_key"],
            )

            completed, rejected = self.run_cli(
                "apply", str(root), "--plan", str(first_path), check=False
            )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(rejected["error_code"], "REFRESH_PLAN_SUPERSEDED")

            _, detected = self.run_cli("detect", str(root), "--fixture")
            self.assertEqual(detected["resume_refresh_id"], replacement["refresh_id"])
            self.assertEqual(detected["resume_status"], "planned")

    def test_source_change_during_refresh_blocks_publication(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            args = module.parse_args(
                ["plan", str(root), "--fixture", "--force-full", "--as-of", "2026-07-30"]
            )
            plan = module.plan_refresh(args, root.resolve(), memory.resolve())
            apply_args = module.parse_args(["apply", str(root), "--plan", plan["plan_path"]])
            original_execute = module.execute_node

            def execute_and_mutate(*call_args, **call_kwargs):
                result = original_execute(*call_args, **call_kwargs)
                path = memory / "actions/live-change.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("changed during refresh\n", encoding="utf-8")
                return result

            with (
                patch.object(module, "execute_node", side_effect=execute_and_mutate),
                self.assertRaises(module.RefreshError) as raised,
            ):
                module.apply_refresh(apply_args, root.resolve(), memory.resolve())

            self.assertEqual(raised.exception.code, "SOURCE_CHANGED_DURING_REFRESH")
            self.assertFalse((memory / "views/management-panel/index.html").exists())
            lock_path = memory / "state/panel-refresh.lock"
            self.assertTrue(lock_path.is_file())
            with module.refresh_lock(memory):
                pass

    def test_identical_plan_reuses_durable_identity_across_wall_clock_seconds(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            args = module.parse_args(
                ["plan", str(root), "--fixture", "--force-full", "--as-of", "2026-07-30"]
            )
            first = module.plan_refresh(args, root.resolve(), memory.resolve())
            time.sleep(1.05)
            second = module.plan_refresh(args, root.resolve(), memory.resolve())

            self.assertEqual(second["refresh_id"], first["refresh_id"])
            self.assertEqual(second["plan_id"], first["plan_id"])
            self.assertEqual(second["created_at"], first["created_at"])

    def test_inspect_binds_current_panel_to_published_receipt(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            plan = self.plan(root)
            self.apply_ready_fixture(root, plan)
            receipt_path = memory / "receipts/panel-refresh" / f"{plan['refresh_id']}.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["panel_id"] = "panel-forged"
            receipt.pop("receipt_id", None)
            receipt["receipt_id"] = module.content_id(receipt)
            receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            _, inspected = self.run_cli("inspect", str(root), check=False)

            self.assertEqual(inspected["artifact_integrity"], "fail")
            self.assertEqual(inspected["publication_eligibility"], "blocked")
            self.assertIn("panel id mismatch", inspected["panel_inspect"]["error"])

    def test_blocked_audit_cannot_become_eligible_with_zero_drift_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            plan = self.plan(root)
            self.apply_ready_fixture(root, plan)
            audit_path = memory / "audits/zzzz/input-audit.json"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "audit_type": "input",
                        "scenario": "management-panel",
                        "audit_status": "blocked",
                        "execution_disposition": "blocked",
                        "safe_to_generate": False,
                        "counts": {"action_projection_drift": 0, "blocking_findings": 1},
                    }
                ),
                encoding="utf-8",
            )

            _, inspected = self.run_cli("inspect", str(root))

            self.assertEqual(inspected["audit_readiness"], "blocked")
            self.assertEqual(inspected["publication_eligibility"], "blocked")

    def test_degraded_staged_audit_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            plan = self.plan(root)

            completed, result = self.run_cli(
                "apply",
                str(root),
                "--plan",
                plan["plan_path"],
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "REFRESH_PUBLICATION_INELIGIBLE")
            self.assertFalse((memory / "views/management-panel/index.html").exists())

    def test_public_inspect_rejects_degraded_and_asymmetric_audit_source_maps(self) -> None:
        for scenario in ("degraded", "missing-audit-source"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                memory = self.scaffold(root)
                source = memory / "actions/action-ledger.md"
                source.parent.mkdir(parents=True)
                source.write_text("# Ledger\n", encoding="utf-8")
                plan = self.plan(root)
                self.apply_ready_fixture(root, plan)
                receipt = load_module().last_successful_receipt(memory)
                audit_path = memory / "audits/zzzz/strict-readiness.json"
                audit_path.parent.mkdir(parents=True, exist_ok=True)
                audit = {
                    "ok": True,
                    "audit_type": "input",
                    "scenario": "management-panel",
                    "audit_status": "warning" if scenario == "degraded" else "pass",
                    "execution_disposition": "degraded" if scenario == "degraded" else "ready",
                    "safe_to_generate": True,
                    "counts": {"action_projection_drift": 0, "blocking_findings": 0},
                    "source_fingerprints": (
                        receipt["source_fingerprints"] if scenario == "degraded" else {}
                    ),
                }
                audit_path.write_text(json.dumps(audit), encoding="utf-8")

                _, inspected = self.run_cli("inspect", str(root), check=False)

                expected_readiness = "degraded" if scenario == "degraded" else "stale"
                self.assertEqual(inspected["audit_readiness"], expected_readiness)
                self.assertEqual(inspected["publication_eligibility"], "blocked")
                if scenario == "missing-audit-source":
                    self.assertEqual(
                        inspected["audit_binding_mismatches"],
                        ["actions/action-ledger.md", "workstreams/L1/delivery-record.md"],
                    )

    def test_public_inspect_uses_refresh_then_fact_lock(self) -> None:
        module = load_module()
        events: list[str] = []

        @contextmanager
        def recorded(name: str):
            events.append(name + "-enter")
            try:
                yield
            finally:
                events.append(name + "-exit")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            memory = self.scaffold(root).resolve()
            args = module.parse_args(["inspect", str(root)])
            with (
                patch.object(module, "refresh_lock", side_effect=lambda _: recorded("refresh")),
                patch.object(module, "fact_read_lock", side_effect=lambda _: recorded("fact")),
                patch.object(
                    module,
                    "_inspect_refresh_unlocked",
                    side_effect=lambda *_: events.append("inspect") or {"ok": True},
                ),
            ):
                module.inspect_refresh(args, root, memory)

        self.assertEqual(
            events,
            ["refresh-enter", "fact-enter", "inspect", "fact-exit", "refresh-exit"],
        )
    def test_policy_operation_generates_candidates_and_validates_caller_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            self.scaffold_policy_sources(memory)

            _, candidates = self.run_cli("policy", str(root))
            candidate_path = Path(candidates["candidate_path"])
            self.assertTrue(candidate_path.is_file())
            self.assertEqual(candidates["current_program_status_snapshot_id"], "PS-CURRENT")
            self.assertEqual(
                [item["snapshot_id"] for item in candidates["available_history_snapshots"]],
                ["PS-HISTORY-1", "PS-HISTORY-2"],
            )
            policy_path = root / "selection-policy.json"
            policy_path.write_text(json.dumps(candidates["candidate_policy"]), encoding="utf-8")

            _, validated = self.run_cli(
                "policy", str(root), "--selection-policy", str(policy_path)
            )
            self.assertTrue(validated["policy_validated"])
            self.assertTrue(validated["selection_policy_id"].startswith("sha256:"))
            self.assertEqual(validated["validated_selection"]["project_lead_scope_id"], "S-1")

            invalid = dict(candidates["candidate_policy"])
            invalid["flow_graph_id"] = "wrong"
            policy_path.write_text(json.dumps(invalid), encoding="utf-8")
            completed, result = self.run_cli(
                "policy", str(root), "--selection-policy", str(policy_path), check=False
            )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "SELECTION_POLICY_INVALID")

    def test_first_policy_stages_upstreams_then_binds_reviewed_policy_to_same_run(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)

            def fake_execute(node, args, plan, project_root, staged_root, workspace, results):
                if node == "program-status":
                    path = staged_root / "views/program-status.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps({"snapshot_id": "PS-FIRST", "as_of": "2026-07-30"}),
                        encoding="utf-8",
                    )
                if node == "flow-graph":
                    path = staged_root / "views/flow-graph.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(
                            {
                                "flow_graph_id": "FLOW-FIRST",
                                "topology": {
                                    "nodes": [{"node_id": "N-1"}, {"node_id": "N-2"}],
                                    "edges": [
                                        {"edge_id": "E-1", "predecessor": "N-1", "target": "N-2"}
                                    ],
                                },
                                "overlays": {"scopes": [{"scope_id": "S-1", "allocations": []}]},
                            }
                        ),
                        encoding="utf-8",
                    )
                result = {"ok": True, "node": node}
                result_path = workspace / "results" / (node.replace(":", "-") + ".json")
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(json.dumps(result), encoding="utf-8")
                return result

            args = module.parse_args(["policy", str(root), "--as-of", "2026-07-30"])
            with patch.object(module, "execute_node", side_effect=fake_execute):
                waiting = module.prepare_policy(args, root.resolve(), memory.resolve())

            self.assertEqual(waiting["status"], "awaiting-policy")
            self.assertTrue(Path(waiting["candidate_policy_path"]).is_file())
            plan_path = Path(waiting["resume_plan_path"])
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(plan["status"], "awaiting-policy")
            self.assertEqual(plan["retry_from_instance_key"], "meeting-pack:fde-morning")
            self.assertFalse((memory / "views/flow-graph.json").exists())

            validate_args = module.parse_args(
                [
                    "policy",
                    str(root),
                    "--selection-policy",
                    waiting["candidate_policy_path"],
                ]
            )
            validated = module.prepare_policy(
                validate_args, root.resolve(), memory.resolve()
            )
            self.assertTrue(validated["policy_validated"])
            self.assertEqual(validated["resume_plan_path"], str(plan_path))
            self.assertTrue(Path(validated["selection_policy"]).is_file())
            rebound = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(rebound["status"], "planned")
            self.assertEqual(rebound["selection_policy_id"], validated["selection_policy_id"])

    def test_policy_change_invalidates_panel_when_fact_fingerprints_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            self.scaffold_policy_sources(memory)
            _, candidates = self.run_cli("policy", str(root))
            policy_a = root / "policy-a.json"
            policy_b = root / "policy-b.json"
            policy_a.write_text(json.dumps(candidates["candidate_policy"]), encoding="utf-8")
            changed_policy = json.loads(json.dumps(candidates["candidate_policy"]))
            changed_policy["shareable"] = {"visible_node_ids": [], "visible_edge_ids": []}
            policy_b.write_text(json.dumps(changed_policy), encoding="utf-8")
            _, validated_a = self.run_cli(
                "policy", str(root), "--selection-policy", str(policy_a)
            )
            receipt_path = memory / "receipts/panel-refresh/previous.json"
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "published",
                        "generation_id": "generation-1",
                        "source_fingerprints": {},
                        "selection_policy": str(policy_a),
                        "selection_policy_id": validated_a["selection_policy_id"],
                    }
                ),
                encoding="utf-8",
            )
            status_path = memory / "state/panel-refresh-status.json"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(
                json.dumps(
                    {
                        "last_successful_receipt": "receipts/panel-refresh/previous.json",
                        "selection_policy": str(policy_a),
                    }
                ),
                encoding="utf-8",
            )

            _, detected = self.run_cli(
                "detect", str(root), "--selection-policy", str(policy_b)
            )
            self.assertEqual(detected["changed_sources"], [])
            self.assertTrue(detected["selection_policy_changed"])
            self.assertEqual(detected["invalidated_nodes"], ["management-panel"])
            self.assertEqual(detected["recommended_mode"], "panel-only")

            _, planned = self.run_cli(
                "plan",
                str(root),
                "--selection-policy",
                str(policy_b),
                "--as-of",
                "2026-07-30",
            )
            self.assertEqual(planned["mode"], "panel-only")
            self.assertEqual(
                [item["instance_key"] for item in planned["nodes"]], ["management-panel"]
            )
            self.assertNotEqual(planned["selection_policy_id"], validated_a["selection_policy_id"])

            module = load_module()
            inspect_args = module.parse_args(
                ["inspect", str(root), "--selection-policy", str(policy_b)]
            )
            with patch.object(
                module,
                "run_json_command",
                return_value={"ok": True, "panel_id": "panel-current"},
            ):
                inspected = module.inspect_refresh(
                    inspect_args, root.resolve(), memory.resolve()
                )
            self.assertTrue(inspected["selection_policy_changed"])
            self.assertEqual(inspected["business_freshness"], "stale")
            self.assertEqual(inspected["publication_eligibility"], "blocked")

    def test_detect_returns_exact_interrupted_plan_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            run_id = "refresh-interrupted"
            plan_path = memory / "state/panel-refresh/runs" / f"{run_id}.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps(
                    {
                        "refresh_id": run_id,
                        "status": "dirty",
                        "retry_from_instance_key": "roadmap",
                    }
                ),
                encoding="utf-8",
            )
            (memory / "state/panel-refresh-status.json").write_text(
                json.dumps({"current_run_id": run_id, "current_status": "dirty"}),
                encoding="utf-8",
            )

            _, detected = self.run_cli("detect", str(root))
            self.assertEqual(detected["resume_plan_path"], str(plan_path.resolve()))
            self.assertEqual(detected["retry_from_instance_key"], "roadmap")
            self.assertEqual(detected["resume_status"], "dirty")

    def test_interrupted_plan_survives_missing_pointer_and_rejects_ambiguity(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            runs = memory / "state/panel-refresh/runs"
            runs.mkdir(parents=True)
            first = runs / "refresh-first.json"
            first.write_text(
                json.dumps(
                    {
                        "refresh_id": "refresh-first",
                        "status": "dirty",
                        "retry_from_instance_key": "flow-graph",
                    }
                ),
                encoding="utf-8",
            )

            recovered = module.interrupted_plan(memory)
            self.assertEqual(recovered["resume_plan_path"], str(first))
            self.assertEqual(recovered["retry_from_instance_key"], "flow-graph")

            (runs / "refresh-second.json").write_text(
                json.dumps({"refresh_id": "refresh-second", "status": "planned"}),
                encoding="utf-8",
            )
            with self.assertRaises(module.RefreshError) as raised:
                module.interrupted_plan(memory)
            self.assertEqual(raised.exception.code, "REFRESH_RESUME_AMBIGUOUS")

    def test_prepared_publication_journal_rolls_back_on_restart(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            target = memory / "views/current.json"
            target.parent.mkdir(parents=True)
            target.write_text("before\n", encoding="utf-8")
            transaction_id = "publish-crash"
            journal = memory / "state/transactions" / transaction_id
            module.atomic_bytes(journal / "before/views/current.json", b"before\n")
            target.write_text("after\n", encoding="utf-8")
            module.atomic_json(
                journal / "manifest.json",
                {
                    "schema_version": "1.0.0",
                    "kind": "panel-publication",
                    "transaction_id": transaction_id,
                    "plan_id": "sha256:" + "a" * 64,
                    "status": "prepared",
                    "applied_count": 1,
                    "targets": [
                        {
                            "path": "views/current.json",
                            "before_sha256": module.file_fingerprint(journal / "before/views/current.json"),
                            "after_sha256": module.file_fingerprint(target),
                        }
                    ],
                },
            )

            recovered = module.recover_publication_transactions(memory)

            self.assertEqual(recovered, [transaction_id])
            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "rolled-back")

    def test_publication_recovery_rejects_absolute_parent_and_symlink_escape_targets(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            outside = root / "outside"
            memory.mkdir()
            outside.mkdir()
            (memory / "escape-link").symlink_to(outside, target_is_directory=True)
            for raw_path in (
                str((outside / "absolute.json").resolve()),
                "../outside/parent.json",
                "escape-link/symlink.json",
            ):
                with self.subTest(raw_path=raw_path), self.assertRaisesRegex(
                    module.RefreshError, "publication target"
                ):
                    module.validated_journal_target(memory, raw_path)

    def test_workspace_requires_durable_refresh_id_and_stays_in_staging_root(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            memory.mkdir()
            for refresh_id in ("refresh-short", "../escape", "/tmp/escape"):
                with self.subTest(refresh_id=refresh_id):
                    with self.assertRaises(module.RefreshError) as raised:
                        module.workspace_for(memory, refresh_id)
                    self.assertEqual(raised.exception.code, "REFRESH_ID_INVALID")
            staging = root / ".adp-panel-refresh-staging"
            outside = root / "outside"
            outside.mkdir()
            staging.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(module.RefreshError) as raised:
                module.workspace_for(memory, "refresh-" + "a" * 24)
            self.assertEqual(raised.exception.code, "REFRESH_STAGING_INVALID")

    def test_staging_ignores_runtime_locks_and_initial_audit_is_global(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            memory = self.scaffold(root).resolve()
            fact = memory / "actions/action-ledger.md"
            action_lock = memory / "actions/ledger-write.LOCK"
            fact_lock = memory / "state/fact-write.lock"
            refresh_lock = memory / "state/panel-refresh.lock"
            fact.parent.mkdir(parents=True)
            fact_lock.parent.mkdir(parents=True)
            fact.write_text("# Facts\n", encoding="utf-8")
            for lock_path in (action_lock, fact_lock, refresh_lock):
                lock_path.write_text("locked\n", encoding="utf-8")
            refresh_id = "refresh-" + "a" * 24
            plan = {
                "refresh_id": refresh_id,
                "plan_id": "sha256:" + "b" * 64,
                "source_as_of": "2026-07-30",
            }
            workspace = module.workspace_for(memory, refresh_id)

            staged = module.prepare_staging(memory, workspace, plan)

            self.assertTrue((staged / "actions/action-ledger.md").is_file())
            self.assertFalse((staged / "actions/ledger-write.LOCK").exists())
            self.assertFalse((staged / "state/fact-write.lock").exists())
            self.assertFalse((staged / "state/panel-refresh.lock").exists())
            self.assertNotIn("actions/ledger-write.LOCK", module.source_inventory(memory))
            args = module.parse_args(["plan", str(root)])
            command = module.node_command(
                "state-audit", args, plan, root, staged, workspace, {}
            )
            self.assertEqual(command[command.index("--scenario") + 1], "global")

    def test_reuse_plan_becomes_terminal_and_recomputes_status_identity(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            initial = self.plan(root)
            self.apply_ready_fixture(root, initial)
            _, reuse_plan = self.run_cli(
                "plan",
                str(root),
                "--fixture",
                "--as-of",
                "2026-07-30",
            )
            self.assertEqual(reuse_plan["nodes"], [])

            _, reused = self.run_cli("apply", str(root), "--plan", reuse_plan["plan_path"])

            self.assertEqual(reused["status"], "reused")
            durable_plan = json.loads(Path(reuse_plan["plan_path"]).read_text(encoding="utf-8"))
            self.assertEqual(durable_plan["status"], "published")
            self.assertIsNone(durable_plan["retry_from_instance_key"])
            status = json.loads((memory / "state/panel-refresh-status.json").read_text(encoding="utf-8"))
            body = dict(status)
            claimed = body.pop("state_id")
            self.assertEqual(claimed, module.content_id(body))
            self.assertEqual(status["current_status"], "published")
            self.assertIsNone(status["retry_from_instance_key"])

    def test_apply_rejects_plan_older_than_latest_successful_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = self.scaffold(root)
            latest = self.plan(root)
            self.apply_ready_fixture(root, latest)
            refresh_id = "refresh-" + "b" * 24
            old_path = memory / "state/panel-refresh/runs" / f"{refresh_id}.json"
            old_plan = {
                "schema_version": "1.0.0",
                "refresh_id": refresh_id,
                "plan_id": "sha256:" + "c" * 64,
                "created_at": "2020-01-01T00:00:00Z",
                "status": "planned",
                "blocked_reasons": [],
                "source_fingerprints": load_module().source_inventory(memory),
                "nodes": [],
                "fixture": True,
            }
            old_path.write_text(json.dumps(old_plan), encoding="utf-8")

            completed, result = self.run_cli(
                "apply",
                str(root),
                "--plan",
                str(old_path),
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["error_code"], "REFRESH_PLAN_SUPERSEDED")

    def test_publication_transaction_identity_includes_plan(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            staged = root / "staged"
            workspace = root / "workspace"
            target = memory / "views/current.json"
            staged_target = staged / "views/current.json"
            target.parent.mkdir(parents=True)
            staged_target.parent.mkdir(parents=True)
            target.write_text("before\n", encoding="utf-8")
            staged_target.write_text("after-one\n", encoding="utf-8")
            first = module.atomic_publish(
                memory,
                staged,
                ["views/current.json"],
                workspace,
                "sha256:" + "1" * 64,
            )
            staged_target.write_text("after-two\n", encoding="utf-8")
            second = module.atomic_publish(
                memory,
                staged,
                ["views/current.json"],
                workspace,
                "sha256:" + "2" * 64,
            )

            self.assertNotEqual(first["transaction_id"], second["transaction_id"])
            self.assertEqual(target.read_text(encoding="utf-8"), "after-two\n")


if __name__ == "__main__":
    unittest.main()
