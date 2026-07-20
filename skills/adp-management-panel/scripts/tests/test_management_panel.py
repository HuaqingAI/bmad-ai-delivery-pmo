import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import management_panel
import panel_model


class ManagementPanelTests(unittest.TestCase):
    def write_artifact_audit(
        self,
        memory_root: Path,
        artifact_path: Path,
        payload: dict,
        source_name: str,
    ) -> Path:
        audit_module = management_panel.load_artifact_audit_module()
        report = {
            "audit_type": "artifact",
            "artifact_validation_schema_version": 1,
            "audit_schema_version": 1,
            "schema_version": 1,
            "generator_version": "test",
            "generated_at": "2026-07-13T09:00:00Z",
            "as_of": payload.get("as_of", "2026-07-13"),
            "scenario": payload.get("scenario", "global"),
            "input_audit_id": payload["input_audit_id"],
            "baseline_revision": payload["baseline_revision"],
            "locale": payload.get("locale", "en"),
            "execution_disposition": "ready",
            "audit_status": "pass",
            "safe_to_publish": True,
            "report_confidence": "high",
            "recommended_workflows": [],
            "artifacts": [
                {
                    "path": str(memory_root / "audits/staging" / artifact_path.name),
                    "fingerprint": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                    "metadata": payload,
                }
            ],
            "blocking_gaps": [],
            "warnings": [],
        }
        report["artifact_validation_id"] = audit_module.stable_artifact_validation_id(report)
        report["audit_content_hash"] = audit_module.audit_content_hash(report)
        path = memory_root / "audits" / f"2026-07-13-{source_name}-{report['artifact_validation_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_artifact_audit_is_resolved_by_integrity_fingerprint_and_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            memory_root = Path(folder)
            artifact_path = memory_root / "views/program-status.json"
            artifact_path.parent.mkdir(parents=True)
            payload = {
                "snapshot_id": "ps-real",
                "input_audit_id": "input-audit-real",
                "baseline_revision": 3,
                "as_of": "2026-07-13",
                "scenario": "global",
                "locale": "en",
                "source_fingerprints": {"source.md": "a" * 64},
            }
            artifact_path.write_text(json.dumps(payload), encoding="utf-8")
            audit_path = self.write_artifact_audit(memory_root, artifact_path, payload, "program-status")

            attached, selected_audit = management_panel.attach_artifact_audit(
                memory_root, artifact_path, payload, "program-status"
            )

            report = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(attached["artifact_audit_id"], report["artifact_validation_id"])
            self.assertEqual(selected_audit, audit_path)
            self.assertEqual(attached["source_fingerprints"]["source.md"], "sha256:" + "a" * 64)

    def test_tampered_artifact_audit_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            memory_root = Path(folder)
            artifact_path = memory_root / "views/roadmap.json"
            artifact_path.parent.mkdir(parents=True)
            payload = {
                "program_status_snapshot_id": "ps-real",
                "input_audit_id": "input-audit-roadmap",
                "baseline_revision": 3,
                "as_of": "2026-07-13",
                "scenario": "roadmap",
                "locale": "en",
                "source_fingerprints": {"source.md": "sha256:" + "b" * 64},
            }
            artifact_path.write_text(json.dumps(payload), encoding="utf-8")
            audit_path = self.write_artifact_audit(memory_root, artifact_path, payload, "roadmap")
            report = json.loads(audit_path.read_text(encoding="utf-8"))
            report["safe_to_publish"] = False
            audit_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(management_panel.PanelError, "no publishable immutable artifact audit"):
                management_panel.attach_artifact_audit(memory_root, artifact_path, payload, "roadmap")

    def args(self, root: Path, operation: str = "refresh", **overrides):
        values = {
            "project_root": str(root),
            "operation": operation,
            "memory_root": str(root / "memory"),
            "fixture": True,
            "input_bundle": None,
            "selection_policy": None,
            "locale": "zh-CN",
            "default_view": "project-lead",
            "max_age_days": 7,
            "distribution_profile": None,
            "expected_panel_id": None,
            "generated_at": "2026-07-13T09:05:00Z",
            "output": None,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_static_fallback_preserves_canonical_empty_scope_evidence(self):
        model = {
            "data": {
                "status": {
                    "as_of": "2026-07-14",
                    "overall_status": "off-plan",
                    "report_confidence": "low",
                    "progress": {"overall": {"current": {}, "forecast_summary": {}}},
                },
                "meetings": {
                    "fde-morning": {
                        "meeting_pack_id": "mp-fde-real",
                        "meeting_window": {"start": "2026-07-13", "end": "2026-07-14", "status": "confirmed"},
                        "readiness": "degraded",
                        "lifecycle": "pre-meeting-snapshot",
                    }
                },
                "flows": {
                    "project-lead": {"nodes": [], "node_states": []},
                    "fde-morning": {
                        "nodes": [],
                        "edges": [],
                        "node_states": [],
                        "empty_state": {
                            "confirmed": True,
                            "meeting_window": {},
                            "node_count": 0,
                            "edge_count": 0,
                            "unmapped_count": 20,
                            "recovery": ["Add explicit canonical relations."],
                            "source_details": [
                                {"source_kind": "risk", "source_id": "R-1", "reason": "missing related IDs"}
                            ],
                        },
                    },
                    "business-biweekly": {"nodes": [], "node_states": []},
                },
            },
            "manifest": {
                "source_fingerprints": {},
                "redaction": {"hidden_nodes": 0, "hidden_edges": 0},
            },
        }

        fallback = management_panel.static_fallback(model)

        self.assertIn("No explicitly related plan items in this confirmed scope", fallback)
        self.assertIn("Window 2026-07-13 to 2026-07-14 selected 0 canonical nodes and 0 canonical edges", fallback)
        self.assertIn("Unmapped overlays</dt><dd>20", fallback)
        self.assertIn("Recovery: Add explicit canonical relations.", fallback)
        self.assertIn("risk / R-1", fallback)
        fde_fallback = fallback.split('<section id="fde-morning"', 1)[1].split('<section id="business-biweekly"', 1)[0]
        self.assertNotIn('<ol class="stage-list"></ol>', fde_fallback)

    def test_refresh_publishes_bundle_before_atomic_current_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = management_panel.run(self.args(root))
            self.assertTrue(first["ok"])
            self.assertEqual("created", first["bundle_state"])
            bundle = Path(first["immutable_bundle"])
            current = Path(first["current_html"])
            self.assertTrue(bundle.is_file())
            self.assertTrue(current.is_file())
            self.assertIn("A-OPEN", (root / "memory/actions/action-ledger.md").read_text(encoding="utf-8"))
            self.assertIn("Gate evidence", (root / "memory/workstreams/L1/delivery-record.md").read_text(encoding="utf-8"))
            previews = management_panel.extract_script(
                current.read_text(encoding="utf-8"), "adp-source-previews"
            )
            self.assertEqual(
                [
                    "actions/action-ledger.md",
                    "views/risk-matrix.md",
                    "workstreams/L1/delivery-record.md",
                ],
                [item["path"] for item in previews],
            )
            self.assertIn("A-OPEN", previews[0]["content"])
            self.assertIn("R-OPEN", previews[1]["content"])
            self.assertTrue(Path(first["panel_input_audit"]).is_file())
            self.assertTrue(Path(first["panel_artifact_audit"]).is_file())
            self.assertEqual(
                f"{panel_model.panel_artifact_basename(first['panel_id'])}.json",
                bundle.name,
            )
            self.assertNotIn(":", bundle.name)
            second = management_panel.run(self.args(root, generated_at="2026-07-14T09:05:00Z"))
            self.assertEqual(first["panel_id"], second["panel_id"])
            self.assertEqual("reused", second["bundle_state"])
            inspected = management_panel.inspect_current(root / "memory", first["panel_id"])
            self.assertEqual(first["panel_id"], inspected["panel_id"])
            self.assertEqual(bundle.resolve(), Path(inspected["immutable_bundle"]).resolve())
            self.assertFalse(any(path.name == ".sha256" for path in (root / "memory").rglob("*")))

    def test_panel_artifact_basename_validates_logical_identity(self):
        panel_id = "sha256:" + "a" * 64
        self.assertEqual("sha256-" + "a" * 64, panel_model.panel_artifact_basename(panel_id))
        for invalid in ("sha256-" + "a" * 64, "sha256:ABC", "not-a-panel-id"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "panel_id must match"):
                panel_model.panel_artifact_basename(invalid)

    @unittest.skipIf(os.name == "nt", "legacy colon-named artifacts exist only on POSIX")
    def test_inspect_reads_legacy_bundle_only_when_safe_bundle_is_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = management_panel.run(self.args(root))
            safe = Path(first["immutable_bundle"])
            legacy = safe.with_name(f"{first['panel_id']}.json")
            legacy.write_bytes(safe.read_bytes())
            safe.unlink()
            before = legacy.read_bytes()

            inspected = management_panel.inspect_current(root / "memory", first["panel_id"])

            self.assertEqual(legacy.resolve(), Path(inspected["immutable_bundle"]).resolve())
            self.assertEqual(before, legacy.read_bytes())
            self.assertFalse(safe.exists())

            safe.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(management_panel.PanelError, "collision between safe and legacy"):
                management_panel.inspect_current(root / "memory", first["panel_id"])
            self.assertEqual(before, legacy.read_bytes())

    @unittest.skipIf(os.name == "nt", "legacy colon-named artifacts exist only on POSIX")
    def test_legacy_only_refresh_reuses_exact_bundle_and_current_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = management_panel.run(self.args(root))
            safe = Path(first["immutable_bundle"])
            legacy = safe.with_name(f"{first['panel_id']}.json")
            safe.replace(legacy)
            legacy_before = legacy.read_bytes()
            current = Path(first["current_html"])
            current_before = current.read_bytes()

            second = management_panel.run(self.args(root, generated_at="2026-07-14T09:05:00Z"))

            safe_twin = Path(second["immutable_bundle"])
            reused = json.loads(safe_twin.read_text(encoding="utf-8"))
            self.assertEqual(first["panel_id"], second["panel_id"])
            self.assertEqual(legacy_before, safe_twin.read_bytes())
            self.assertEqual(legacy_before, legacy.read_bytes())
            self.assertEqual(current_before, current.read_bytes())
            self.assertEqual("2026-07-13T09:05:00Z", reused["manifest"]["generated_at"])

    @unittest.skipIf(os.name == "nt", "legacy colon-named artifacts exist only on POSIX")
    def test_legacy_only_archive_reuses_bundle_and_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = management_panel.run(
                self.args(root, "archive", distribution_profile="internal-full")
            )
            safe = Path(first["immutable_bundle"])
            legacy = safe.with_name(f"{first['panel_id']}.json")
            safe.replace(legacy)
            legacy_before = legacy.read_bytes()
            archive = Path(first["archive_html"])
            archive_before = archive.read_bytes()

            second = management_panel.run(
                self.args(
                    root,
                    "archive",
                    distribution_profile="internal-full",
                    generated_at="2026-07-14T09:05:00Z",
                )
            )

            self.assertEqual(first["panel_id"], second["panel_id"])
            self.assertEqual(legacy_before, Path(second["immutable_bundle"]).read_bytes())
            self.assertEqual(legacy_before, legacy.read_bytes())
            self.assertEqual("reused", second["archive_state"])
            self.assertEqual(archive_before, archive.read_bytes())

    @unittest.skipIf(os.name == "nt", "legacy colon-named artifacts exist only on POSIX")
    def test_safe_legacy_collision_fails_without_panel_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = management_panel.run(self.args(root))
            safe = Path(first["immutable_bundle"])
            legacy = safe.with_name(f"{first['panel_id']}.json")
            legacy_model = json.loads(safe.read_text(encoding="utf-8"))
            legacy_model["manifest"]["generated_at"] = "2026-07-14T09:05:00Z"
            legacy.write_bytes(management_panel.canonical_json_bytes(legacy_model))
            safe_before = safe.read_bytes()
            legacy_before = legacy.read_bytes()
            current = Path(first["current_html"])
            current_before = current.read_bytes()

            with self.assertRaisesRegex(management_panel.PanelError, "collision between safe and legacy"):
                management_panel.run(self.args(root, generated_at="2026-07-15T09:05:00Z"))

            self.assertEqual(safe_before, safe.read_bytes())
            self.assertEqual(legacy_before, legacy.read_bytes())
            self.assertEqual(current_before, current.read_bytes())

    @unittest.skipIf(os.name == "nt", "legacy colon-named artifacts exist only on POSIX")
    def test_legacy_identity_collision_does_not_create_safe_twin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = management_panel.run(self.args(root))
            safe = Path(first["immutable_bundle"])
            legacy = safe.with_name(f"{first['panel_id']}.json")
            safe.replace(legacy)
            invalid = json.loads(legacy.read_text(encoding="utf-8"))
            invalid["panel_model_id"] = "sha256:" + "f" * 64
            legacy.write_bytes(management_panel.canonical_json_bytes(invalid))
            legacy_before = legacy.read_bytes()
            current = Path(first["current_html"])
            current_before = current.read_bytes()

            with self.assertRaisesRegex(management_panel.PanelError, "identity collision"):
                management_panel.run(self.args(root, generated_at="2026-07-14T09:05:00Z"))

            self.assertFalse(safe.exists())
            self.assertEqual(legacy_before, legacy.read_bytes())
            self.assertEqual(current_before, current.read_bytes())

    def test_selection_policy_validates_scope_history_membership_and_order(self):
        inputs = panel_model.load_source_fixture()
        policy = {
            "policy_version": "1.0.0",
            "flow_graph_id": inputs["flow_graph"]["flow_graph_id"],
            "history_snapshot_ids": ["ps-history-2026-06-29", "ps-history-2026-07-06"],
            "project_lead": {
                "scope_id": "ACTIVE-2026-07-13",
                "node_ids": ["M-B", "M-A", "G-MERGE"],
                "edge_ids": ["E-B-MERGE", "E-A-MERGE"],
            },
            "shareable": {
                "visible_node_ids": ["G-MERGE", "M-A"],
                "visible_edge_ids": ["E-A-MERGE"],
            },
        }
        history_ids = {item["snapshot_id"] for item in inputs["history"]}

        selected = management_panel.validate_selection_policy(inputs["flow_graph"], policy, history_ids)

        self.assertEqual(policy["history_snapshot_ids"], selected["history_snapshot_ids"])
        self.assertEqual("ACTIVE-2026-07-13", selected["project_lead_scope_id"])
        self.assertEqual(["G-MERGE", "M-A", "M-B"], selected["project_lead_node_ids"])
        self.assertEqual(["E-A-MERGE", "E-B-MERGE"], selected["project_lead_edge_ids"])
        self.assertEqual(["G-MERGE", "M-A"], selected["shareable_policy"]["visible_node_ids"])
        broken = json.loads(json.dumps(policy))
        broken["shareable"]["visible_node_ids"] = ["M-A"]
        with self.assertRaisesRegex(management_panel.PanelError, "not closed over selected nodes"):
            management_panel.validate_selection_policy(inputs["flow_graph"], broken, history_ids)
        broken = json.loads(json.dumps(policy))
        broken["project_lead"]["node_ids"].append("UNKNOWN")
        with self.assertRaisesRegex(management_panel.PanelError, "unknown IDs: UNKNOWN"):
            management_panel.validate_selection_policy(inputs["flow_graph"], broken, history_ids)
        broken = json.loads(json.dumps(policy))
        broken["project_lead"]["scope_id"] = "UNKNOWN"
        with self.assertRaisesRegex(management_panel.PanelError, "scope_id is unknown: UNKNOWN"):
            management_panel.validate_selection_policy(inputs["flow_graph"], broken, history_ids)
        broken = json.loads(json.dumps(policy))
        broken["history_snapshot_ids"] = ["missing-history"]
        with self.assertRaisesRegex(management_panel.PanelError, "history_snapshot_ids contains unknown IDs"):
            management_panel.validate_selection_policy(inputs["flow_graph"], broken, history_ids)
        broken["history_snapshot_ids"] = ["ps-history-2026-07-06"] * 2
        with self.assertRaisesRegex(management_panel.PanelError, "history_snapshot_ids contains duplicate IDs"):
            management_panel.validate_selection_policy(inputs["flow_graph"], broken, history_ids)

    def test_history_snapshot_index_uses_embedded_identity_not_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            memory_root = Path(folder)
            history_root = memory_root / "snapshots/program-status"
            history_root.mkdir(parents=True)
            for filename, snapshot_id in (
                ("z-looks-new.json", "history-older"),
                ("a-looks-old.json", "history-newer"),
            ):
                (history_root / filename).write_text(
                    json.dumps({"snapshot_id": snapshot_id, "as_of": "2026-07-01"}),
                    encoding="utf-8",
                )

            indexed, paths = management_panel.history_snapshot_index(memory_root, "current")

            requested = ["history-newer", "history-older"]
            self.assertEqual(requested, [indexed[item]["snapshot_id"] for item in requested])
            self.assertEqual("a-looks-old.json", paths["history-newer"].name)

    def test_cross_platform_refresh_reuse_inspect_archive_journey(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            memory_root = root / "memory"

            def invoke(operation: str, *arguments: str) -> dict:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(Path(management_panel.__file__).resolve()),
                        str(root),
                        operation,
                        "--memory-root",
                        str(memory_root),
                        *arguments,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
                return json.loads(completed.stdout)

            first = invoke("refresh", "--fixture", "--generated-at", "2026-07-13T09:05:00Z")
            second = invoke("refresh", "--fixture", "--generated-at", "2026-07-14T09:05:00Z")
            inspected = invoke("inspect", "--expected-panel-id", first["panel_id"])
            archived = invoke(
                "archive",
                "--fixture",
                "--distribution-profile",
                "internal-full",
                "--generated-at",
                "2026-07-15T09:05:00Z",
            )

            self.assertEqual(first["panel_id"], second["panel_id"])
            self.assertEqual(first["panel_id"], inspected["panel_id"])
            self.assertEqual(first["panel_id"], archived["panel_id"])
            self.assertEqual("reused", second["bundle_state"])
            self.assertNotIn(":", Path(first["immutable_bundle"]).name)
            self.assertNotIn(":", Path(archived["archive_html"]).name)
            self.assertTrue((memory_root / "views/management-panel/index.html").is_file())
            empty_digests = [
                path
                for path in memory_root.rglob("*.sha256")
                if path.is_file() and path.stat().st_size == 0
            ]
            self.assertEqual([], empty_digests)

    def test_meeting_pack_selection_uses_structured_identity_not_filename_or_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scenario_root = root / "fde-morning"
            scenario_root.mkdir()
            older = scenario_root / "newest-fde-morning-copy.json"
            newer = scenario_root / "opaque.json"
            unrelated = scenario_root / "fde-morning-archive.json"
            older.write_text(json.dumps({
                "scenario": "fde-morning",
                "meeting_pack_id": "mp-old",
                "generated_at": "2026-07-13T10:00:00Z",
                "lifecycle": "pre-meeting-snapshot",
            }), encoding="utf-8")
            newer.write_text(json.dumps({
                "scenario": "fde-morning",
                "meeting_pack_id": "mp-current",
                "generated_at": "2026-07-13T11:00:00Z",
                "lifecycle": "pre-meeting-snapshot",
            }), encoding="utf-8")
            unrelated.write_text(json.dumps({"scenario": "business-biweekly"}), encoding="utf-8")
            older.touch()

            path, pack = management_panel.resolve_current_meeting_pack(root, "fde-morning")

            self.assertEqual(newer, path)
            self.assertEqual("mp-current", pack["meeting_pack_id"])
            duplicate = scenario_root / "another-opaque.json"
            duplicate.write_text(json.dumps({
                "scenario": "fde-morning",
                "meeting_pack_id": "mp-ambiguous",
                "generated_at": "2026-07-13T11:00:00Z",
                "lifecycle": "pre-meeting-snapshot",
            }), encoding="utf-8")
            with self.assertRaisesRegex(management_panel.PanelError, "identity is ambiguous"):
                management_panel.resolve_current_meeting_pack(root, "fde-morning")

    def test_refresh_never_advances_or_repairs_meeting_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cursor = root / "memory/meetings/cursors/fde-morning.json"
            cursor.parent.mkdir(parents=True)
            cursor.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scenario": "fde-morning",
                        "meeting_instance_id": "mi-before-refresh",
                        "ended_at": "2026-07-13T09:20:00+08:00",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            before = cursor.read_bytes()

            result = management_panel.run(self.args(root))

            self.assertTrue(result["ok"])
            self.assertEqual(cursor.read_bytes(), before)
            self.assertEqual(list(cursor.parent.glob("*.tmp")), [])

    def test_pack_lifecycle_comes_only_from_matching_receipt_state(self):
        inputs = panel_model.load_source_fixture()
        pack = inputs["meeting_packs"]["fde-morning"]
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            receipt_root = memory_root / "meetings/receipts"
            receipt_root.mkdir(parents=True)

            pre = management_panel.enrich_pack(pack, memory_root)
            self.assertEqual(pre["lifecycle"], "pre-meeting-snapshot")

            receipt_path = receipt_root / "mi-phase9.json"
            receipt = {
                "status": "conflict",
                "lineage": {"meeting_pack_id": pack["meeting_pack_id"]},
                "started_at": "2026-07-13T09:00:00Z",
            }
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            failed = management_panel.enrich_pack(pack, memory_root)
            self.assertEqual(failed["lifecycle"], "sync-failed")
            self.assertNotIn("official_panel_archive", failed)

            receipt.update(
                {
                    "status": "applied",
                    "applied_at": "2026-07-13T09:30:00Z",
                    "official_panel_archive": {
                        "panel_id": "sha256:" + "a" * 64,
                        "archive": "snapshots/management-panel/panel.html",
                        "distribution_profile": "internal-full",
                        "receipt_status": "applied",
                    },
                }
            )
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            official = management_panel.enrich_pack(pack, memory_root)
            self.assertEqual(official["lifecycle"], "post-sync-official")
            self.assertEqual(official["official_panel_archive"]["panel_id"], "sha256:" + "a" * 64)

    def test_archive_requires_profile_and_shareable_does_not_replace_current(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(management_panel.PanelError):
                management_panel.run(self.args(root, "archive"))
            result = management_panel.run(self.args(root, "archive", distribution_profile="shareable-summary"))
            self.assertEqual("shareable-summary", result["distribution_profile"])
            self.assertTrue(Path(result["archive_html"]).is_file())
            self.assertNotIn(":", Path(result["archive_html"]).name)
            self.assertNotIn(":", Path(result["immutable_bundle"]).name)
            self.assertFalse((root / "memory/views/management-panel/index.html").exists())
            model = json.loads(Path(result["immutable_bundle"]).read_text(encoding="utf-8"))
            self.assertGreater(model["manifest"]["redaction"]["hidden_nodes"], 0)
            self.assertFalse(model["manifest"]["redaction"]["topology_reconnected"])
            archive_text = Path(result["archive_html"]).read_text(encoding="utf-8")
            self.assertEqual([], management_panel.extract_script(archive_text, "adp-source-previews"))
            self.assertNotIn("# Action Ledger", archive_text)

    def test_source_preview_is_safe_identity_bound_and_shareable_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "memory/actions/action-ledger.md"
            source.parent.mkdir(parents=True)
            source.write_text("# A-OPEN\n\n</script><script>alert('preview')</script>\n", encoding="utf-8")

            first = management_panel.run(self.args(root))
            first_html = Path(first["current_html"]).read_text(encoding="utf-8")
            previews = management_panel.extract_script(first_html, "adp-source-previews")
            action = next(item for item in previews if item["path"] == "actions/action-ledger.md")
            self.assertIn("</script><script>", action["content"])
            self.assertIn("\\u003c/script\\u003e", first_html)
            self.assertEqual(
                action["source_sha256"],
                management_panel.extract_script(first_html, "adp-panel-manifest")[
                    "source_fingerprints"
                ]["source-preview/actions/action-ledger.md"],
            )

            source.write_text("# A-OPEN\n\nRevised source content.\n", encoding="utf-8")
            second = management_panel.run(self.args(root))
            self.assertNotEqual(first["panel_id"], second["panel_id"])

            shareable = management_panel.run(
                self.args(root, "archive", distribution_profile="shareable-summary")
            )
            shareable_html = Path(shareable["archive_html"]).read_text(encoding="utf-8")
            self.assertEqual(
                [], management_panel.extract_script(shareable_html, "adp-source-previews")
            )
            self.assertNotIn("Revised source content", shareable_html)

    def test_source_preview_limit_keeps_action_ledger_first(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = root / "actions/action-ledger.md"
            ledger.parent.mkdir(parents=True)
            ledger.write_text("# Action Ledger\n", encoding="utf-8")
            risk_matrix = root / "views/risk-matrix.md"
            risk_matrix.parent.mkdir(parents=True)
            risk_matrix.write_text("# Risk Matrix\n", encoding="utf-8")
            items = []
            for index in range(panel_model.SOURCE_PREVIEW_MAX_FILES + 2):
                relative = f"00-sources/source-{index:02d}.md"
                source = root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# Source {index}\n", encoding="utf-8")
                items.append({"Source": relative})
            inputs = {"meeting_packs": {"fde-morning": {"boards": {"items": items}}}}

            previews = management_panel.build_source_previews(
                inputs, root, "internal-full"
            )

            self.assertEqual(panel_model.SOURCE_PREVIEW_MAX_FILES, len(previews))
            self.assertEqual("actions/action-ledger.md", previews[0]["path"])
            self.assertEqual("views/risk-matrix.md", previews[1]["path"])

    def test_fixed_elk_metadata_matches_shipped_bytes_and_license(self):
        resource, _ = management_panel.verify_layout_resource()
        bundle = panel_model.SKILL_ROOT / resource["bundle"]
        canonical = management_panel.canonical_utf8_lf_bytes(bundle.read_bytes(), bundle)
        actual = "sha256:" + hashlib.sha256(canonical).hexdigest()
        self.assertEqual(resource["engine_sha256"], actual)
        self.assertEqual("utf8-lf", resource["engine_sha256_mode"])
        self.assertEqual("EPL-2.0", resource["engine_license"])
        license_path = panel_model.SKILL_ROOT / resource["license"]
        canonical_license = management_panel.canonical_utf8_lf_bytes(license_path.read_bytes(), license_path)
        license_hash = "sha256:" + hashlib.sha256(canonical_license).hexdigest()
        self.assertEqual(resource["license_sha256"], license_hash)
        self.assertEqual("utf8-lf", resource["license_sha256_mode"])

    def test_fixed_markdown_renderer_matches_shipped_bytes_and_rejects_tampering(self):
        resource, markdown_js = management_panel.verify_markdown_resource()
        bundle = panel_model.SKILL_ROOT / resource["bundle"]
        license_path = panel_model.SKILL_ROOT / resource["license"]
        self.assertEqual("markdown-it", resource["renderer"])
        self.assertEqual("14.1.0", resource["renderer_version"])
        self.assertEqual("MIT", resource["renderer_license"])
        self.assertEqual(
            resource["renderer_sha256"],
            management_panel.sha256_bytes(
                management_panel.canonical_utf8_lf_bytes(bundle.read_bytes(), bundle)
            ),
        )
        self.assertEqual(
            resource["license_sha256"],
            management_panel.sha256_bytes(
                management_panel.canonical_utf8_lf_bytes(
                    license_path.read_bytes(), license_path
                )
            ),
        )
        self.assertIn("markdownit", markdown_js)

        with tempfile.TemporaryDirectory() as folder:
            skill_root = Path(folder)
            resource_path = skill_root / "assets/markdown-resource-v1.json"
            copied_bundle = skill_root / resource["bundle"]
            copied_license = skill_root / resource["license"]
            resource_path.parent.mkdir(parents=True)
            copied_bundle.parent.mkdir(parents=True)
            copied_license.parent.mkdir(parents=True, exist_ok=True)
            resource_path.write_text(json.dumps(resource), encoding="utf-8")
            copied_bundle.write_bytes(bundle.read_bytes().replace(b"\n", b"\r\n"))
            copied_license.write_bytes(license_path.read_bytes().replace(b"\n", b"\r\n"))
            management_panel.verify_markdown_resource(resource_path, skill_root)
            copied_bundle.write_bytes(copied_bundle.read_bytes() + b"tampered")
            with self.assertRaisesRegex(management_panel.PanelError, "checksum mismatch"):
                management_panel.verify_markdown_resource(resource_path, skill_root)

    def test_fixed_elk_accepts_windows_crlf_checkout_and_rejects_other_changes(self):
        source_resource = management_panel.load_json(management_panel.RESOURCE_PATH)
        source_bundle = panel_model.SKILL_ROOT / source_resource["bundle"]
        source_license = panel_model.SKILL_ROOT / source_resource["license"]
        with tempfile.TemporaryDirectory() as folder:
            skill_root = Path(folder)
            resource_path = skill_root / "assets/elk-resource-v1.json"
            bundle = skill_root / source_resource["bundle"]
            license_path = skill_root / source_resource["license"]
            resource_path.parent.mkdir(parents=True)
            bundle.parent.mkdir(parents=True)
            license_path.parent.mkdir(parents=True, exist_ok=True)
            resource_path.write_text(json.dumps(source_resource), encoding="utf-8")
            bundle.write_bytes(source_bundle.read_bytes().replace(b"\n", b"\r\n"))
            license_bytes = source_license.read_bytes()
            license_path.write_bytes(license_bytes.replace(b"\n", b"\r\n"))

            resource, elk_js = management_panel.verify_layout_resource(resource_path, skill_root)

            self.assertEqual(source_resource["engine_sha256"], resource["engine_sha256"])
            self.assertEqual(source_resource["license_sha256"], resource["license_sha256"])
            self.assertNotIn("\r\n", elk_js)
            bundle.write_bytes(bundle.read_bytes() + b"tampered")
            with self.assertRaisesRegex(management_panel.PanelError, "checksum mismatch"):
                management_panel.verify_layout_resource(resource_path, skill_root)

            bundle.write_bytes(source_bundle.read_bytes().replace(b"\n", b"\r\n"))
            for index in [0, len(license_bytes) // 2, len(license_bytes) - 1]:
                with self.subTest(license_byte=index):
                    tampered = bytearray(license_bytes)
                    tampered[index] ^= 1
                    license_path.write_bytes(tampered)
                    with self.assertRaisesRegex(management_panel.PanelError, "license checksum mismatch"):
                        management_panel.verify_layout_resource(resource_path, skill_root)

            license_path.write_bytes(license_bytes)
            resource_path.write_text(
                json.dumps({**source_resource, "engine_license": "EPL-2.0-modified"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(management_panel.PanelError, "engine_license must be EPL-2.0"):
                management_panel.verify_layout_resource(resource_path, skill_root)

    def test_blocked_inputs_write_no_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = panel_model.load_source_fixture()
            inputs["program_status"]["progress"]["progress_schema_version"] = "1.0.0"
            bundle_path = root / "inputs.json"
            bundle_path.write_text(json.dumps(inputs), encoding="utf-8")
            args = self.args(root, fixture=False, input_bundle=str(bundle_path))
            with self.assertRaises(management_panel.PanelError) as caught:
                management_panel.run(args)
            self.assertEqual(
                ("adp-program-status", "adp-roadmap-sync", "adp-meeting-pack"),
                caught.exception.recommended_workflows,
            )
            self.assertFalse((root / "memory/snapshots/management-panel").exists())
            self.assertFalse((root / "memory/views/management-panel").exists())
            self.assertTrue(any((root / "memory/audits/management-panel").glob("panel-input-audit-*.json")))

    def test_precompose_failures_route_to_owning_producers(self):
        with tempfile.TemporaryDirectory() as folder:
            memory_root = Path(folder)
            views = memory_root / "views"
            views.mkdir()
            audit_path = memory_root / "audits/source.json"

            with self.assertRaises(management_panel.PanelError) as caught:
                management_panel.load_memory_inputs(memory_root, {})
            self.assertEqual(
                management_panel.PROGRAM_STATUS_RECOVERY,
                caught.exception.recommended_workflows,
            )
            policy_path = memory_root / "panel-policy.json"
            policy_path.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = management_panel.main(
                    [
                        folder,
                        "refresh",
                        "--memory-root",
                        folder,
                        "--selection-policy",
                        str(policy_path),
                    ]
                )
            result = json.loads(output.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual(
                ["adp-state-audit", "adp-program-status"],
                result["recommended_workflows"],
            )
            self.assertFalse((memory_root / "snapshots/management-panel").exists())
            self.assertFalse((views / "management-panel").exists())

            (views / "program-status.json").write_text("{}", encoding="utf-8")
            status = {"snapshot_id": "ps-current"}
            with patch.object(management_panel, "attach_artifact_audit", return_value=(status, audit_path)):
                with self.assertRaises(management_panel.PanelError) as caught:
                    management_panel.load_memory_inputs(memory_root, {})
            self.assertEqual(management_panel.ROADMAP_RECOVERY, caught.exception.recommended_workflows)

            (views / "roadmap.json").write_text("{}", encoding="utf-8")
            with patch.object(
                management_panel,
                "attach_artifact_audit",
                side_effect=[(status, audit_path), ({}, audit_path)],
            ):
                with self.assertRaises(management_panel.PanelError) as caught:
                    management_panel.load_memory_inputs(memory_root, {})
            self.assertEqual(management_panel.FLOW_GRAPH_RECOVERY, caught.exception.recommended_workflows)

            (views / "flow-graph.json").write_text("{}", encoding="utf-8")
            with patch.object(
                management_panel,
                "attach_artifact_audit",
                side_effect=[(status, audit_path), ({}, audit_path)],
            ):
                with self.assertRaises(management_panel.PanelError) as caught:
                    management_panel.load_memory_inputs(memory_root, {})
            self.assertEqual(management_panel.MEETING_PACK_RECOVERY, caught.exception.recommended_workflows)

    def test_main_defaults_unclassified_failures_to_generic_recovery(self):
        error = OSError("unexpected filesystem failure")
        output = io.StringIO()
        with patch.object(management_panel, "run", side_effect=error), redirect_stdout(output):
            exit_code = management_panel.main(["/project", "refresh"])
        result = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertEqual(str(error), result["reason"])
        self.assertEqual(
            ["adp-state-audit", "adp-management-panel"],
            result["recommended_workflows"],
        )

    def test_missing_selection_policy_routes_to_panel_caller_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root, fixture=False, selection_policy=None)
            with self.assertRaises(management_panel.PanelError) as caught:
                management_panel.load_inputs(args, {}, "internal-full", root / "memory")
            self.assertEqual(
                management_panel.SELECTION_POLICY_RECOVERY,
                caught.exception.recommended_workflows,
            )

    def test_malicious_source_text_is_inert_in_html_and_svg_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = panel_model.load_source_fixture()
            malicious = panel_model.load_json(panel_model.MALICIOUS_FIXTURE_PATH)
            inputs["flow_graph"]["topology"]["nodes"][0]["name"] = malicious["labels"][0]
            panel_model._recompute_flow_identities(inputs["flow_graph"])
            for pack in inputs["meeting_packs"].values():
                pack["flow_subgraph"] = panel_model._flow_selection(
                    inputs["flow_graph"],
                    pack["flow_selection_id"],
                    pack["selected_node_ids"],
                    pack["selected_edge_ids"],
                    pack["flow_scope_id"],
                    pack["scenario"],
                )
            inputs["meeting_packs"]["business-biweekly"]["boards"]["business_decisions"][0]["summary"] = malicious["labels"][-1]
            source = root / "inputs.json"
            source.write_text(json.dumps(inputs), encoding="utf-8")
            result = management_panel.run(self.args(root, fixture=False, input_bundle=str(source)))
            rendered = Path(result["current_html"]).read_text(encoding="utf-8")
            self.assertNotIn("<img src=x", rendered.lower())
            self.assertNotIn("</script><script>", rendered.lower())
            self.assertNotIn("<foreignobject", rendered.lower())
            self.assertIn("\\u003c", rendered)

    def test_runtime_module_contains_no_business_formula_functions(self):
        source = (panel_model.SKILL_ROOT / "scripts/panel_model.py").read_text(encoding="utf-8")
        for name in ("calculate_progress", "classify_status", "aggregate_counts", "select_branch"):
            self.assertNotIn("def " + name, source)


if __name__ == "__main__":
    unittest.main()
