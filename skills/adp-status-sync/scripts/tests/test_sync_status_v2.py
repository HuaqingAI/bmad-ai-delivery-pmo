import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "sync_status.py"
SKILL_ROOT = SCRIPT.parents[1]
MEMORY_REL = Path("_bmad-output/adp/memory")


def canonical_id(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_id(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_module():
    spec = importlib.util.spec_from_file_location("adp_status_sync_v2_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sync_status.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def record_text(workstream_id: str) -> str:
    return f"""# Workstream Delivery Record

## Identity

- Workstream ID: {workstream_id}
- Name: {workstream_id}
- FDE owner: FDE-A
- Business owner: Biz-A
- Current BMM phase: PRD
- Current ADP status: draft

## Project Status

- Progress: TBD
- Blockers: TBD
- Risks: TBD
- Dependencies: TBD
- Scope or change notes: TBD
- Next actions: fill missing state

## Record Rule

Keep details in BMM artifacts.
"""


class StatusSyncV2Tests(unittest.TestCase):
    def symlink_or_skip(self, link: Path, target: Path, *, target_is_directory: bool = True) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows user lacks symbolic-link creation privilege")
            raise

    def run_cli(self, *args: str, check: bool = True) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed, json.loads(completed.stdout)

    def create_record(self, root: Path, workstream_id: str) -> Path:
        path = root / MEMORY_REL / "workstreams" / workstream_id / "delivery-record.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record_text(workstream_id), encoding="utf-8")
        return path

    def write_updates(self, root: Path, name: str, updates: list[dict]) -> Path:
        path = root / name
        path.write_text(json.dumps({"updates": updates}), encoding="utf-8")
        return path

    def action_row(self, ledger: Path, action_id: str) -> dict[str, str]:
        lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.startswith("|")]
        headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
        for line in lines[2:]:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            row = dict(zip(headers, cells, strict=True))
            if row.get("Action ID") == action_id:
                return row
        raise AssertionError(f"action row missing: {action_id}")

    def create_action(self, root: Path, workstream_id: str, action_id: str, owner: str = "FDE-A") -> dict:
        path = self.write_updates(
            root,
            f"create-{workstream_id}.json",
            [
                {
                    "id": workstream_id,
                    "refresh_actions": True,
                    "actions": [
                        {
                            "operation": "create",
                            "command_id": f"CMD-CREATE-{action_id}",
                            "action_id": action_id,
                            "owner": owner,
                            "status": "open",
                            "action": f"Publish evidence for {workstream_id}",
                            "source": "meeting#1",
                            "due": "Friday",
                            "closure_criteria": "Evidence link is reviewed",
                            "evidence": [
                                {
                                    "source": "meeting#1",
                                    "observed_at": "2026-07-30T01:00:00Z",
                                }
                            ],
                        }
                    ],
                }
            ],
        )
        return self.run_cli("update", str(root), "--updates-file", str(path))[1]

    def patch_action(
        self,
        root: Path,
        workstream_id: str,
        action_id: str,
        command_id: str,
        expected_revision: int,
        patch: dict,
        *,
        check: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        action = {
            "operation": "patch",
            "command_id": command_id,
            "action_id": action_id,
            "expected_action_revision": expected_revision,
            "evidence": [
                {
                    "source": f"patch/{command_id}",
                }
            ],
            **patch,
        }
        path = self.write_updates(
            root,
            f"patch-{command_id}.json",
            [{"id": workstream_id, "actions": [action]}],
        )
        return self.run_cli("update", str(root), "--updates-file", str(path), check=check)

    def test_typed_patch_preserves_omitted_fields_and_enforces_revision_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            created = self.create_action(root, "l1-checkout", "ACT-V2-001")
            ledger = Path(created["action_ledger"])

            _, patched = self.patch_action(
                root,
                "l1-checkout",
                "ACT-V2-001",
                "CMD-OWNER-1",
                1,
                {"owner": "FDE-B"},
            )
            row = self.action_row(ledger, "ACT-V2-001")
            self.assertEqual(row["Owner"], "FDE-B")
            self.assertEqual(row["Status"], "open")
            self.assertEqual(row["Due / Trigger"], "Friday")
            self.assertEqual(row["Action"], "Publish evidence for l1-checkout")
            self.assertEqual(row["Action Revision"], "2")
            self.assertEqual(patched["actions_updated"], ["ACT-V2-001"])

            _, replay = self.patch_action(
                root,
                "l1-checkout",
                "ACT-V2-001",
                "CMD-OWNER-1",
                1,
                {"owner": "FDE-B"},
            )
            self.assertEqual(replay["actions_no_op"], ["ACT-V2-001"])
            self.assertEqual(self.action_row(ledger, "ACT-V2-001")["Action Revision"], "2")

            stale_completed, stale = self.patch_action(
                root,
                "l1-checkout",
                "ACT-V2-001",
                "CMD-STALE",
                1,
                {"owner": "FDE-C"},
                check=False,
            )
            self.assertEqual(stale_completed.returncode, 2)
            self.assertEqual(stale["error_code"], "ACTION_REVISION_CONFLICT")

            self.patch_action(
                root,
                "l1-checkout",
                "ACT-V2-001",
                "CMD-DONE",
                2,
                {"status": "done"},
            )
            reopen_completed, reopen = self.patch_action(
                root,
                "l1-checkout",
                "ACT-V2-001",
                "CMD-REOPEN",
                3,
                {"status": "open"},
                check=False,
            )
            self.assertEqual(reopen_completed.returncode, 2)
            self.assertIn("cannot transition", reopen["error"])

    def test_typed_commands_require_stable_command_and_action_ids(self) -> None:
        module = load_module()
        base = {
            "actions": [
                {
                    "operation": "create",
                    "action_id": "ACT-TYPED-001",
                    "action": "Publish evidence",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "stable command_id"):
            module.actions_from_mapping(base, "l1-checkout", "meeting#1")

        missing_action_id = {
            "actions": [
                {
                    "operation": "create",
                    "command_id": "CMD-TYPED-001",
                    "action": "Publish evidence",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "stable action_id"):
            module.actions_from_mapping(missing_action_id, "l1-checkout", "meeting#1")

        missing_evidence = {
            "actions": [
                {
                    "operation": "create",
                    "command_id": "CMD-TYPED-002",
                    "action_id": "ACT-TYPED-002",
                    "action": "Publish evidence",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "non-empty evidence"):
            module.actions_from_mapping(missing_evidence, "l1-checkout", "meeting#1")

    def test_status_intent_allowlist_and_actual_update_binding_fail_closed(self) -> None:
        module = load_module()
        update = module.update_from_mapping(
            {"id": "l1-checkout", "progress": "Validation is 80% complete"},
            "meeting#1",
        )
        with self.assertRaisesRegex(ValueError, "unsupported fields: surprise"):
            module.bind_status_intents(
                [update],
                [
                    {
                        "intent_id": "INTENT-UNKNOWN",
                        "workstream_id": "l1-checkout",
                        "set": {"surprise": "ignored before this fix"},
                    }
                ],
            )
        with self.assertRaisesRegex(ValueError, "does not match its StatusUpdate"):
            module.bind_status_intents(
                [update],
                [
                    {
                        "intent_id": "INTENT-MISMATCH",
                        "workstream_id": "l1-checkout",
                        "set": {"progress": "Validation is complete"},
                    }
                ],
            )

    def test_completed_updates_file_replay_reuses_receipt_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            first = self.create_action(root, "l1-checkout", "ACT-REPLAY-001")
            memory = root / MEMORY_REL
            before = {
                path.relative_to(memory).as_posix(): path.read_bytes()
                for path in memory.rglob("*")
                if path.is_file()
            }

            _, replay = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(root / "create-l1-checkout.json"),
            )
            after = {
                path.relative_to(memory).as_posix(): path.read_bytes()
                for path in memory.rglob("*")
                if path.is_file()
            }

            self.assertEqual(replay["status"], "already-applied")
            self.assertTrue(replay["reused"])
            self.assertFalse(replay["refresh_required"])
            self.assertEqual(replay["receipt_path"], first["receipt_path"])
            self.assertEqual(before, after)
            self.assertEqual(len(list((memory / "receipts/status-sync").glob("*.json"))), 1)

    def test_action_replay_identity_binds_full_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            self.create_action(root, "l1-checkout", "ACT-EVIDENCE-001")
            update_path = root / "create-l1-checkout.json"
            payload = json.loads(update_path.read_text(encoding="utf-8"))
            payload["updates"][0]["actions"][0]["evidence"][0]["source"] = "meeting#forged"
            update_path.write_text(json.dumps(payload), encoding="utf-8")

            completed, result = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(update_path),
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "ACTION_COMMAND_REPLAY_CONFLICT")

    def test_malformed_existing_ledger_state_is_not_treated_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            result = self.create_action(root, "l1-checkout", "ACT-STATE-001")
            Path(result["action_ledger_state"]).write_text("{malformed", encoding="utf-8")

            completed, error = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(root / "create-l1-checkout.json"),
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(error["error_code"], "ACTION_LEDGER_STATE_MISMATCH")

    def test_sidecars_pin_actual_installed_contract_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            result = self.create_action(root, "l1-checkout", "ACT-PIN-001")
            memory = root / MEMORY_REL
            schema_hash = file_id(SKILL_ROOT / "assets/panel-sync-contracts.schema.json")
            registry_hash = file_id(SKILL_ROOT / "assets/CONTRACT-REGISTRY.json")
            for path in (
                Path(result["action_ledger_state"]),
                memory / "workstreams/l1-checkout/action-projection.json",
            ):
                contract = json.loads(path.read_text(encoding="utf-8"))["contract"]
                self.assertEqual(contract["schema_sha256"], schema_hash)
                self.assertEqual(contract["registry_sha256"], registry_hash)
                self.assertNotEqual(contract["schema_sha256"], "sha256:" + "0" * 64)

    def test_staging_copy_ignores_runtime_lock_files(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            staged = root / "staged"
            (memory / "state").mkdir(parents=True)
            (memory / "state/fact-write.lock").write_bytes(b"locked")
            (memory / "state/panel-refresh.lock").write_bytes(b"locked")
            (memory / "state/fact-generation.json").write_text("{}\n", encoding="utf-8")

            module.copy_memory_tree(memory, staged)

            self.assertTrue((staged / "state/fact-generation.json").is_file())
            self.assertFalse((staged / "state/fact-write.lock").exists())
            self.assertFalse((staged / "state/panel-refresh.lock").exists())

    def test_missing_projection_sidecar_bootstraps_on_regular_status_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            self.create_action(root, "l1-checkout", "ACT-BOOTSTRAP-001")
            sidecar = root / MEMORY_REL / "workstreams/l1-checkout/action-projection.json"
            sidecar.unlink()
            updates = self.write_updates(
                root,
                "progress-update.json",
                [{"id": "l1-checkout", "progress": "Bootstrap validation is complete"}],
            )

            _, result = self.run_cli("update", str(root), "--updates-file", str(updates))

            self.assertTrue(sidecar.is_file())
            self.assertEqual(
                Path(result["updates"][0]["action_projection"]).resolve(),
                sidecar.resolve(),
            )
            projection = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["action_id"] for item in projection["actions"]],
                ["ACT-BOOTSTRAP-001"],
            )

    def test_replay_rejects_ledger_bytes_not_bound_by_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            created = self.create_action(root, "l1-checkout", "ACT-BIND-001")
            ledger = Path(created["action_ledger"])
            ledger.write_text(ledger.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            replay_path = root / "create-l1-checkout.json"

            completed, result = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(replay_path),
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "ACTION_LEDGER_STATE_MISMATCH")

    def test_status_intents_for_one_workstream_cannot_be_partially_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = self.create_record(root, "l1-checkout")
            original_record = record.read_text(encoding="utf-8")
            memory = root / MEMORY_REL
            outbox_path = memory / "state/status-intent-outbox.json"
            outbox_path.parent.mkdir(parents=True, exist_ok=True)
            intents = [
                {
                    "intent_id": "INTENT-PROGRESS",
                    "workstream_id": "l1-checkout",
                    "set": {"progress": "Validation is 80% complete"},
                },
                {
                    "intent_id": "INTENT-BLOCKER",
                    "workstream_id": "l1-checkout",
                    "set": {"blockers": ["Business approval pending"]},
                },
            ]
            outbox = {
                "schema_version": "1.0.0",
                "pending": sorted(intent["intent_id"] for intent in intents),
                "consumed": [],
                "failed": [],
                "waived": [],
                "intents": [
                    {
                        "intent_id": intent["intent_id"],
                        "state": "pending",
                        "payload_hash": canonical_id(intent),
                        "producer": "adp-meeting-sync",
                        "producer_receipt_id": "MEETING-001",
                        "intent": intent,
                        "consumed_by": None,
                        "consumed_at": None,
                    }
                    for intent in intents
                ],
            }
            outbox["state_id"] = canonical_id(outbox)
            outbox_path.write_text(json.dumps(outbox, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            original_outbox = outbox_path.read_text(encoding="utf-8")

            intake_path = root / "partial-intake.json"
            intake_path.write_text(json.dumps({"status_intents": [intents[0]]}), encoding="utf-8")
            completed, result = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(intake_path),
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "STATUS_INTENT_PARTIAL_CONSUMPTION")
            self.assertEqual(record.read_text(encoding="utf-8"), original_record)
            self.assertEqual(outbox_path.read_text(encoding="utf-8"), original_outbox)

    def test_status_intent_payload_must_match_durable_outbox_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = self.create_record(root, "l1-checkout")
            original_record = record.read_text(encoding="utf-8")
            memory = root / MEMORY_REL
            outbox_path = memory / "state/status-intent-outbox.json"
            outbox_path.parent.mkdir(parents=True, exist_ok=True)
            durable_intent = {
                "intent_id": "INTENT-PROGRESS",
                "origin_producer": "adp-meeting-sync",
                "workstream_id": "l1-checkout",
                "set": {"progress": "Validation is 80% complete"},
                "evidence": [{"source": "meeting.md", "observed_at": "2026-07-30T01:00:00Z"}],
            }
            outbox = {
                "schema_version": "1.0.0",
                "pending": [durable_intent["intent_id"]],
                "consumed": [],
                "failed": [],
                "waived": [],
                "intents": [
                    {
                        "intent_id": durable_intent["intent_id"],
                        "state": "pending",
                        "payload_hash": canonical_id(durable_intent),
                        "producer": "adp-meeting-sync",
                        "producer_receipt_id": "MEETING-001",
                        "intent": durable_intent,
                        "consumed_by": None,
                        "consumed_at": None,
                    }
                ],
            }
            outbox["state_id"] = canonical_id(outbox)
            outbox_path.write_text(json.dumps(outbox, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            original_outbox = outbox_path.read_text(encoding="utf-8")
            forged = json.loads(json.dumps(durable_intent))
            forged["set"]["progress"] = "Validation is complete"
            intake_path = root / "forged-intake.json"
            intake_path.write_text(json.dumps({"status_intents": [forged]}), encoding="utf-8")

            completed, result = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(intake_path),
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "STATUS_INTENT_BINDING_MISMATCH")
            self.assertEqual(record.read_text(encoding="utf-8"), original_record)
            self.assertEqual(outbox_path.read_text(encoding="utf-8"), original_outbox)

    def remove_managed_projection(self, root: Path, workstream_id: str, action_id: str) -> None:
        memory = root / MEMORY_REL
        record = memory / "workstreams" / workstream_id / "delivery-record.md"
        text = record.read_text(encoding="utf-8")
        marker = f"[action_id:{action_id}]"
        self.assertIn(marker, text)
        line = next(line for line in text.splitlines() if line.startswith("- Next actions:"))
        text = text.replace(line, "- Next actions: fill missing state")
        record.write_text(text, encoding="utf-8")
        state_path = record.with_name("delivery-record.state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["wdr_fingerprint"] = file_id(record)
        state.pop("state_id", None)
        state["state_id"] = canonical_id(state)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def repair_batch(self, root: Path, audit_id: str, workstream_id: str, action_id: str) -> tuple[dict, dict]:
        memory = root / MEMORY_REL
        ledger = memory / "actions/action-ledger.md"
        ledger_state = json.loads((memory / "actions/action-ledger.state.json").read_text(encoding="utf-8"))
        record = memory / "workstreams" / workstream_id / "delivery-record.md"
        state = json.loads(record.with_name("delivery-record.state.json").read_text(encoding="utf-8"))
        row = self.action_row(ledger, action_id)
        finding_body = {
            "kind": "action-projection-drift",
            "repairability": "repairable",
            "severity": "blocked",
            "workstream_id": workstream_id,
            "action_id": action_id,
            "action_diff": {
                "action_id": action_id,
                "drift_kind": "missing-from-wdr",
                "ledger_present": True,
                "wdr_present": False,
                "ledger_revision": int(row["Action Revision"]),
                "wdr_rendered_sha256": None,
            },
            "source_path": f"workstreams/{workstream_id}/delivery-record.md",
            "source_line": None,
        }
        finding_id = canonical_id(finding_body)
        mapped = {
            "finding_id": finding_id,
            "kind": finding_body["kind"],
            "severity": "blocked",
            "workflow": "adp-status-sync",
            "workstream_id": workstream_id,
            "operation": "refresh_actions",
            "entity_refs": [
                {"entity_type": "workstream", "id": workstream_id},
                {"entity_type": "action", "id": action_id},
            ],
            "action_ids": [action_id],
            "source_path": finding_body["source_path"],
            "source_line": None,
        }
        body = {
            "based_on_audit_id": audit_id,
            "finding_ids": [finding_id],
            "command": {
                "workflow": "adp-status-sync",
                "workstream_id": workstream_id,
                "operation": "refresh_actions",
                "expected_wdr_revision": state["wdr_revision"],
                "expected_file_generation": state["file_generation"],
                "action_ids": [action_id],
            },
            "read_set": {
                "ledger_fingerprint": file_id(ledger),
                "ledger_revision": ledger_state["ledger_revision"],
                "action_revisions": [
                    {"action_id": action_id, "expected_present": True, "revision": int(row["Action Revision"])}
                ],
                "wdr_revisions": [],
                "source_records": [],
                "fact_generation": 1,
            },
        }
        body["read_set"]["wdr_revisions"] = [
            {
                "workstream_id": workstream_id,
                "wdr_revision": state["wdr_revision"],
                "file_generation": state["file_generation"],
                "fingerprint": state["wdr_fingerprint"],
            }
        ]
        root_instance_id = "ri_" + hashlib.sha256(str(memory.resolve()).encode("utf-8")).hexdigest()
        source_paths = [
            "actions/action-ledger.md",
            "actions/action-ledger.state.json",
            f"workstreams/{workstream_id}/delivery-record.md",
            f"workstreams/{workstream_id}/delivery-record.state.json",
            f"workstreams/{workstream_id}/action-projection.json",
        ]
        body["read_set"]["source_records"] = [
            {
                "root_instance_id": root_instance_id,
                "path": source_path,
                "fingerprint": (
                    file_id(memory / source_path)
                    if (memory / source_path).is_file()
                    else "sha256:" + "0" * 64
                ),
            }
            for source_path in source_paths
        ]
        batch_id = canonical_id(body)
        batch = {"batch_id": batch_id, **body}
        batch["batch_digest"] = canonical_id(batch)
        mapped["repair_batch_id"] = batch_id
        return mapped, batch

    def write_audit(self, root: Path, workstreams: list[tuple[str, str]], suffix: str) -> tuple[Path, dict[str, dict]]:
        audit_id = canonical_id({"audit": suffix, "workstreams": workstreams})
        findings = []
        batches = []
        by_workstream = {}
        for workstream_id, action_id in workstreams:
            finding, batch = self.repair_batch(root, audit_id, workstream_id, action_id)
            findings.append(finding)
            batches.append(batch)
            by_workstream[workstream_id] = batch
        payload = {
            "input_audit_id": audit_id,
            "repair_contract": {
                "schema_version": "2.0.0",
                "audit_id": audit_id,
                "drift_verdict_id": canonical_id({"drift": suffix}),
                "findings": findings,
                "repair_batches": sorted(batches, key=lambda row: row["batch_id"]),
            },
        }
        path = root / f"audit-{suffix}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path, by_workstream

    def repair_dry_run(self, root: Path, audit: Path, batch_id: str) -> dict:
        completed, payload = self.run_cli(
            "repair",
            str(root),
            "--audit-json",
            str(audit),
            "--batch-id",
            batch_id,
            "--dry-run",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, payload)
        return payload

    def repair_apply(self, root: Path, audit: Path, batch_id: str, token: str, *, check: bool = True):
        return self.run_cli(
            "repair",
            str(root),
            "--audit-json",
            str(audit),
            "--batch-id",
            batch_id,
            "--token",
            token,
            check=check,
        )

    def authority_migration_dry_run(self, root: Path) -> dict:
        completed, payload = self.run_cli(
            "migrate-authority-state",
            str(root),
            "--dry-run",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, payload)
        return payload

    def authority_migration_apply(
        self,
        root: Path,
        token: str,
        *,
        fail_after_stage: bool = False,
        check: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        args = ["migrate-authority-state", str(root), "--token", token]
        if fail_after_stage:
            args.append("--fail-after-stage")
        return self.run_cli(*args, check=check)

    def test_authority_state_migration_bootstraps_legacy_project_and_is_idempotent(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / MEMORY_REL
            record = self.create_record(root, "l1-checkout")
            self.create_action(root, "l1-checkout", "ACT-MIGRATE-001")
            ledger = memory / "actions/action-ledger.md"
            ledger.write_bytes(ledger.read_bytes() + b"\n")
            record.with_name("delivery-record.state.json").unlink()
            projection = record.with_name("action-projection.json")
            projection.write_text('{"stale": true}\n', encoding="utf-8")
            intake = self.write_updates(
                root,
                "legacy-follow-up.json",
                [{"id": "l1-checkout", "progress": "Migration verification"}],
            )

            failed_update, failed_payload = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(intake),
                "--dry-run",
                check=False,
            )
            self.assertEqual(failed_update.returncode, 2)
            self.assertEqual(failed_payload["error_code"], "ACTION_LEDGER_STATE_MISMATCH")
            with self.assertRaises(module.StatusSyncContractError) as missing_state:
                module.repair_live_snapshot(
                    memory,
                    {"command": {"workstream_id": "l1-checkout"}, "read_set": {}},
                )
            self.assertEqual(missing_state.exception.error_code, "REPAIR_READ_SET_STALE")

            preview = self.authority_migration_dry_run(root)

            self.assertEqual(preview["status"], "ready-to-apply")
            self.assertTrue(preview["token"].startswith("authority_"))
            by_path = {item["path"]: item for item in preview["authority_artifacts"]}
            self.assertEqual(by_path["actions/action-ledger.state.json"]["status"], "stale")
            self.assertEqual(
                by_path["workstreams/l1-checkout/delivery-record.state.json"]["status"],
                "missing",
            )
            self.assertEqual(
                by_path["workstreams/l1-checkout/action-projection.json"]["status"],
                "stale",
            )
            original_bindings = dict(preview["source_fingerprints"])

            completed, applied = self.authority_migration_apply(root, preview["token"], check=False)

            self.assertEqual(completed.returncode, 0, applied)
            self.assertEqual(applied["status"], "committed")
            self.assertTrue(Path(applied["receipt_path"]).is_file())
            self.assertEqual(applied["receipt"]["source_fingerprints"], original_bindings)
            ledger_state = json.loads((memory / "actions/action-ledger.state.json").read_text(encoding="utf-8"))
            module.validate_action_ledger_state(ledger, ledger_state)
            wdr_state = json.loads(record.with_name("delivery-record.state.json").read_text(encoding="utf-8"))
            self.assertEqual(module.validate_wdr_state(record, wdr_state), [])
            projection_payload = json.loads(projection.read_text(encoding="utf-8"))
            self.assertEqual(projection_payload["ledger_fingerprint"], file_id(ledger))
            self.assertEqual(projection_payload["wdr_revision"], wdr_state["wdr_revision"])
            reused_token_completed, reused_token = self.authority_migration_apply(
                root,
                preview["token"],
                check=False,
            )
            self.assertEqual(reused_token_completed.returncode, 2)
            self.assertEqual(reused_token["error_code"], "AUTHORITY_MIGRATION_TOKEN_USED")

            update_completed, update_payload = self.run_cli(
                "update",
                str(root),
                "--updates-file",
                str(intake),
                "--dry-run",
                check=False,
            )
            self.assertEqual(update_completed.returncode, 0, update_payload)

            second = self.authority_migration_dry_run(root)

            self.assertEqual(second["status"], "already-migrated")
            self.assertTrue(second["reused"])
            self.assertIsNone(second["token"])
            self.assertEqual(second["planned_changed_paths"], [])
            self.assertEqual(second["receipt_path"], applied["receipt_path"])

            self.remove_managed_projection(root, "l1-checkout", "ACT-MIGRATE-001")
            audit, batches = self.write_audit(
                root,
                [("l1-checkout", "ACT-MIGRATE-001")],
                "post-authority-migration",
            )
            repair_preview = self.repair_dry_run(
                root,
                audit,
                batches["l1-checkout"]["batch_id"],
            )
            self.assertEqual(repair_preview["outcome"], "applicable")

    def test_authority_state_migration_token_binds_original_file_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / MEMORY_REL
            record = self.create_record(root, "l1-checkout")
            (memory / "actions").mkdir(parents=True)
            module = load_module()
            (memory / "actions/action-ledger.md").write_text(module.default_action_ledger(), encoding="utf-8")
            preview = self.authority_migration_dry_run(root)
            record.write_bytes(record.read_bytes() + b"\n")

            completed, payload = self.authority_migration_apply(
                root,
                preview["token"],
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["error_code"], "AUTHORITY_MIGRATION_READ_SET_STALE")
            self.assertFalse(record.with_name("delivery-record.state.json").exists())
            receipt_root = memory / module.AUTHORITY_MIGRATION_RECEIPT_REL
            self.assertFalse(receipt_root.exists())

    def test_authority_state_migration_staging_failure_publishes_nothing(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / MEMORY_REL
            record = self.create_record(root, "l1-checkout")
            (memory / "actions").mkdir(parents=True)
            ledger = memory / "actions/action-ledger.md"
            ledger.write_text(module.default_action_ledger(), encoding="utf-8")
            stale_state = memory / "actions/action-ledger.state.json"
            stale_state.write_text('{"stale": true}\n', encoding="utf-8")
            before = stale_state.read_bytes()
            preview = self.authority_migration_dry_run(root)

            completed, payload = self.authority_migration_apply(
                root,
                preview["token"],
                fail_after_stage=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["error_code"], "AUTHORITY_MIGRATION_INJECTED_FAILURE")
            self.assertEqual(stale_state.read_bytes(), before)
            self.assertFalse(record.with_name("delivery-record.state.json").exists())
            self.assertFalse((memory / module.AUTHORITY_MIGRATION_RECEIPT_REL).exists())

    def test_repair_batches_preserve_commits_invalidate_failure_and_retry_from_fresh_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / MEMORY_REL
            for workstream_id, action_id in (("l1-checkout", "ACT-R-001"), ("l2-search", "ACT-R-002")):
                self.create_record(root, workstream_id)
                self.create_action(root, workstream_id, action_id)
                self.remove_managed_projection(root, workstream_id, action_id)

            audit, batches = self.write_audit(
                root,
                [("l1-checkout", "ACT-R-001"), ("l2-search", "ACT-R-002")],
                "initial",
            )
            first = self.repair_dry_run(root, audit, batches["l1-checkout"]["batch_id"])
            self.assertTrue(first["token"].startswith("repair_"))
            _, first_apply = self.repair_apply(
                root,
                audit,
                batches["l1-checkout"]["batch_id"],
                first["token"],
            )
            self.assertEqual(first_apply["outcome"], "committed")
            first_record = memory / "workstreams/l1-checkout/delivery-record.md"
            self.assertIn("[action_id:ACT-R-001]", first_record.read_text(encoding="utf-8"))

            second = self.repair_dry_run(root, audit, batches["l2-search"]["batch_id"])
            self.patch_action(
                root,
                "l2-search",
                "ACT-R-002",
                "CMD-R-OWNER",
                1,
                {"owner": "FDE-B"},
            )
            failed_completed, failed = self.repair_apply(
                root,
                audit,
                batches["l2-search"]["batch_id"],
                second["token"],
                check=False,
            )
            self.assertEqual(failed_completed.returncode, 2)
            self.assertEqual(failed["error_code"], "REPAIR_READ_SET_STALE")
            self.assertIn("[action_id:ACT-R-001]", first_record.read_text(encoding="utf-8"))
            attempts = json.loads((memory / "state/repair-attempt-ledger.json").read_text(encoding="utf-8"))["attempts"]
            self.assertEqual([row["outcome"] for row in attempts], ["committed", "rolled-back"])

            retry_audit, retry_batches = self.write_audit(
                root,
                [("l2-search", "ACT-R-002")],
                "retry",
            )
            retry = self.repair_dry_run(root, retry_audit, retry_batches["l2-search"]["batch_id"])
            self.assertNotEqual(retry["token"], second["token"])
            _, retried = self.repair_apply(
                root,
                retry_audit,
                retry_batches["l2-search"]["batch_id"],
                retry["token"],
            )
            self.assertEqual(retried["outcome"], "committed")
            second_record = memory / "workstreams/l2-search/delivery-record.md"
            self.assertIn("[action_id:ACT-R-002] FDE-B", second_record.read_text(encoding="utf-8"))

            reused_completed, reused = self.repair_apply(
                root,
                retry_audit,
                retry_batches["l2-search"]["batch_id"],
                retry["token"],
                check=False,
            )
            self.assertEqual(reused_completed.returncode, 2)
            self.assertEqual(reused["error_code"], "REPAIR_TOKEN_USED")

    def test_prepared_status_transaction_recovers_all_before_images(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            first = memory / "workstreams/l1/delivery-record.md"
            second = memory / "actions/action-ledger.md"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"before-first\n")
            second.write_bytes(b"before-second\n")
            transaction_id = "status-mutation-crash"
            journal = memory / module.TRANSACTION_REL / transaction_id
            for target in (first, second):
                relative = target.relative_to(memory)
                module.write_bytes_atomic(journal / "before" / relative, target.read_bytes())
            targets = [
                {
                    "path": first.relative_to(memory).as_posix(),
                    "before_sha256": module.sha256_bytes(b"before-first\n"),
                    "after_sha256": module.sha256_bytes(b"after-first\n"),
                },
                {
                    "path": second.relative_to(memory).as_posix(),
                    "before_sha256": module.sha256_bytes(b"before-second\n"),
                    "after_sha256": module.sha256_bytes(b"after-second\n"),
                },
            ]
            first.write_bytes(b"after-first\n")
            module.write_json_atomic(
                journal / "manifest.json",
                {
                    "schema_version": "1.0.0",
                    "kind": "status-mutation",
                    "transaction_id": transaction_id,
                    "status": "prepared",
                    "applied_count": 1,
                    "targets": targets,
                },
            )

            recovered = module.recover_status_transactions(memory)

            self.assertEqual(recovered, [transaction_id])
            self.assertEqual(first.read_text(encoding="utf-8"), "before-first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "before-second\n")
            manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "rolled-back")

    def test_prepared_status_transaction_removes_early_receipt_on_recovery(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir)
            record = memory / "workstreams/l1/delivery-record.md"
            receipt = memory / "receipts/status-sync/ssr-crash.json"
            record.parent.mkdir(parents=True)
            receipt.parent.mkdir(parents=True)
            record.write_bytes(b"before\n")
            receipt.write_bytes(b'{"status":"applied"}\n')

            transaction_id = "status-receipt-crash"
            journal = memory / module.TRANSACTION_REL / transaction_id
            module.write_bytes_atomic(journal / "before/workstreams/l1/delivery-record.md", b"before\n")
            module.write_json_atomic(
                journal / "manifest.json",
                {
                    "schema_version": "1.0.0",
                    "kind": "status-mutation",
                    "transaction_id": transaction_id,
                    "status": "prepared",
                    "applied_count": 1,
                    "targets": [
                        {
                            "path": "receipts/status-sync/ssr-crash.json",
                            "before_sha256": None,
                            "after_sha256": module.sha256_bytes(receipt.read_bytes()),
                        },
                        {
                            "path": "workstreams/l1/delivery-record.md",
                            "before_sha256": module.sha256_bytes(b"before\n"),
                            "after_sha256": module.sha256_bytes(b"after\n"),
                        },
                    ],
                },
            )

            recovered = module.recover_status_transactions(memory)

            self.assertEqual(recovered, [transaction_id])
            self.assertFalse(receipt.exists())
            self.assertEqual(record.read_bytes(), b"before\n")
            manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "rolled-back")

    def test_recovery_rejects_absolute_parent_and_symlink_escape_targets(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            outside = root / "outside"
            memory.mkdir()
            outside.mkdir()
            self.symlink_or_skip(memory / "escape-link", outside)
            for raw_path in (
                str((outside / "absolute.txt").resolve()),
                "../outside/parent.txt",
                "escape-link/symlink.txt",
            ):
                with self.subTest(raw_path=raw_path), self.assertRaisesRegex(
                    module.StatusSyncContractError, "transaction target"
                ):
                    module.validated_recovery_target(memory, raw_path)

    def test_repair_dry_run_revalidates_source_records_and_fact_generation(self) -> None:
        for mutation in ("source", "generation"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                memory = root / MEMORY_REL
                self.create_record(root, "l1-checkout")
                self.create_action(root, "l1-checkout", "ACT-READSET-001")
                self.remove_managed_projection(root, "l1-checkout", "ACT-READSET-001")
                audit, batches = self.write_audit(
                    root,
                    [("l1-checkout", "ACT-READSET-001")],
                    f"read-set-{mutation}",
                )
                batch_id = batches["l1-checkout"]["batch_id"]
                if mutation == "source":
                    sidecar = memory / "workstreams/l1-checkout/action-projection.json"
                    sidecar.write_bytes(sidecar.read_bytes() + b"\n")
                else:
                    generation = memory / "state/fact-generation.json"
                    generation.parent.mkdir(parents=True, exist_ok=True)
                    generation.write_text('{"generation": 2}\n', encoding="utf-8")

                completed, result = self.run_cli(
                    "repair",
                    str(root),
                    "--audit-json",
                    str(audit),
                    "--batch-id",
                    batch_id,
                    "--dry-run",
                    check=False,
                )

                self.assertEqual(completed.returncode, 2)
                self.assertEqual(result["error_code"], "REPAIR_READ_SET_STALE")

    def test_repair_apply_rejects_tampered_token_state_identity(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / MEMORY_REL
            self.create_record(root, "l1-checkout")
            self.create_action(root, "l1-checkout", "ACT-TOKEN-001")
            self.remove_managed_projection(root, "l1-checkout", "ACT-TOKEN-001")
            audit, batches = self.write_audit(root, [("l1-checkout", "ACT-TOKEN-001")], "token")
            batch_id = batches["l1-checkout"]["batch_id"]
            preview = self.repair_dry_run(root, audit, batch_id)
            token_path = module.repair_token_path(memory, preview["token"])
            token_state = json.loads(token_path.read_text(encoding="utf-8"))
            token_state["principal"] = "tampered-principal"
            token_path.write_text(json.dumps(token_state), encoding="utf-8")

            completed, result = self.repair_apply(
                root,
                audit,
                batch_id,
                preview["token"],
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "REPAIR_TOKEN_INVALID")

    def test_reserved_repair_finishes_from_committed_business_journal(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / MEMORY_REL
            self.create_record(root, "l1-checkout")
            self.create_action(root, "l1-checkout", "ACT-RECOVER-001")
            self.remove_managed_projection(root, "l1-checkout", "ACT-RECOVER-001")
            audit, batches = self.write_audit(
                root,
                [("l1-checkout", "ACT-RECOVER-001")],
                "business-crash",
            )
            batch = batches["l1-checkout"]
            dry_run = self.repair_dry_run(root, audit, batch["batch_id"])
            token_path = module.repair_token_path(memory, dry_run["token"])
            token_state = json.loads(token_path.read_text(encoding="utf-8"))
            snapshot = module.repair_live_snapshot(memory, batch)
            transaction_id = module.next_status_transaction_id(
                memory,
                "repair-business-" + batch["batch_id"].removeprefix("sha256:")[:24],
            )
            module.update_token_state(
                token_path,
                token_state,
                "reserved",
                business_transaction_id=transaction_id,
            )
            module.apply_repair_snapshot(memory, batch, snapshot, False, transaction_id)

            completed, recovered = self.repair_apply(
                root,
                audit,
                batch["batch_id"],
                dry_run["token"],
                check=False,
            )

            self.assertEqual(completed.returncode, 0, recovered)
            self.assertTrue(recovered["reused"])
            self.assertEqual(recovered["outcome"], "committed")
            self.assertIn(
                "[action_id:ACT-RECOVER-001]",
                (memory / "workstreams/l1-checkout/delivery-record.md").read_text(encoding="utf-8"),
            )
            attempts = json.loads((memory / module.REPAIR_ATTEMPT_LEDGER_REL).read_text(encoding="utf-8"))
            self.assertEqual(len(attempts["attempts"]), 1)
            self.assertEqual(attempts["attempts"][0]["outcome"], "committed")


    def test_reconcile_intake_matches_legacy_action_by_full_composite_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            created = self.create_action(root, "l1-checkout", "ACT-RECON-001")
            ledger = Path(created["action_ledger"])
            before = ledger.read_bytes()
            intake = self.write_updates(
                root,
                "historical-intake.json",
                [{"id": "l1-checkout", "actions": [{
                    "owner": "FDE-A",
                    "action": "Publish evidence for l1-checkout",
                    "source": "meeting#1",
                    "due": "Friday",
                    "closure_criteria": "Evidence link is reviewed",
                }]}],
            )

            _, preview = self.run_cli(
                "reconcile-intake", str(root), "--updates-file", str(intake), "--dry-run"
            )
            self.assertTrue(preview["all_satisfied"])
            self.assertEqual(preview["command_results"][0]["match_method"], "action-owner-source-due-closure")
            self.assertTrue(preview["token"])

            _, applied = self.run_cli(
                "reconcile-intake", str(root), "--updates-file", str(intake), "--token", preview["token"]
            )
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(applied["receipt"]["receipt_type"], "reconciliation")
            self.assertTrue(Path(applied["receipt_path"]).is_file())
            replay, error = self.run_cli(
                "reconcile-intake", str(root), "--updates-file", str(intake), "--token", preview["token"], check=False
            )
            self.assertEqual(replay.returncode, 0, error)
            self.assertTrue(error["reused"])

    def test_reconcile_intake_apply_is_atomic_after_staging_failure(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            self.create_action(root, "l1-checkout", "ACT-ATOMIC-001")
            intake = self.write_updates(
                root,
                "atomic-intake.json",
                [{"id": "l1-checkout", "actions": [{"action_id": "ACT-ATOMIC-001", "action": "Publish evidence for l1-checkout"}]}],
            )
            _, preview = self.run_cli("reconcile-intake", str(root), "--updates-file", str(intake), "--dry-run")
            token_path = module.reconciliation_token_path(root / MEMORY_REL, preview["token"])
            before_token = token_path.read_bytes()
            before_receipts = sorted((root / MEMORY_REL / "receipts/status-sync").glob("*.json"))

            completed, result = self.run_cli(
                "reconcile-intake", str(root), "--updates-file", str(intake), "--token", preview["token"],
                "--fail-after-stage", check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "INTAKE_RECONCILIATION_INJECTED_FAILURE")
            self.assertEqual(token_path.read_bytes(), before_token)
            self.assertEqual(sorted((root / MEMORY_REL / "receipts/status-sync").glob("*.json")), before_receipts)

    def test_reconcile_intake_partial_match_lists_missing_commands_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_record(root, "l1-checkout")
            created = self.create_action(root, "l1-checkout", "ACT-RECON-001")
            receipt_root = root / MEMORY_REL / "receipts/status-sync"
            existing_receipts = sorted(receipt_root.glob("*.json"))
            intake = self.write_updates(
                root,
                "partial-intake.json",
                [{"id": "l1-checkout", "actions": [
                    {"action_id": "ACT-RECON-001", "action": "Publish evidence for l1-checkout"},
                    {"action_id": "ACT-MISSING-999", "action": "Missing historical command"},
                ]}],
            )

            _, preview = self.run_cli(
                "reconcile-intake", str(root), "--updates-file", str(intake), "--dry-run"
            )
            self.assertEqual(preview["verification_status"], "partial")
            self.assertFalse(preview["all_satisfied"])
            self.assertIsNone(preview["token"])
            self.assertEqual([item["requested_action_id"] for item in preview["missing_commands"]], ["ACT-MISSING-999"])
            self.assertEqual(sorted(receipt_root.glob("*.json")), existing_receipts)
            self.assertEqual(Path(created["action_ledger"]).read_text(encoding="utf-8").count("ACT-MISSING-999"), 0)

    def test_reconcile_intake_rejects_stale_status_lineage_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = self.create_record(root, "l1-checkout")
            matching = self.write_updates(root, "matching-status.json", [{"id": "l1-checkout", "progress": "Ready"}])
            self.run_cli("update", str(root), "--updates-file", str(matching))
            intake = self.write_updates(root, "historical-status.json", [{"id": "l1-checkout", "progress": "Ready"}])
            _, preview = self.run_cli("reconcile-intake", str(root), "--updates-file", str(intake), "--dry-run")
            newer = self.write_updates(root, "newer-status.json", [{"id": "l1-checkout", "progress": "Newer fact"}])
            self.run_cli("update", str(root), "--updates-file", str(newer))

            completed, result = self.run_cli(
                "reconcile-intake", str(root), "--updates-file", str(intake), "--token", preview["token"], check=False
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["error_code"], "INTAKE_RECONCILIATION_FACTS_STALE")
            text = record.read_text(encoding="utf-8")
            self.assertIn("- Progress: Newer fact", text)
            self.assertNotIn("- Progress: Ready", text)

if __name__ == "__main__":
    unittest.main()
