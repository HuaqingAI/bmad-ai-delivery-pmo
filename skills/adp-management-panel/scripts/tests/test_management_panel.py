import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

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
            "locale": "zh-CN",
            "default_view": "project-lead",
            "history_limit": 12,
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
            self.assertTrue(Path(first["panel_input_audit"]).is_file())
            self.assertTrue(Path(first["panel_artifact_audit"]).is_file())
            second = management_panel.run(self.args(root, generated_at="2026-07-14T09:05:00Z"))
            self.assertEqual(first["panel_id"], second["panel_id"])
            self.assertEqual("reused", second["bundle_state"])
            inspected = management_panel.inspect_current(root / "memory", first["panel_id"])
            self.assertEqual(first["panel_id"], inspected["panel_id"])

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
            self.assertFalse((root / "memory/views/management-panel/index.html").exists())
            model = json.loads(Path(result["immutable_bundle"]).read_text(encoding="utf-8"))
            self.assertGreater(model["manifest"]["redaction"]["hidden_nodes"], 0)
            self.assertFalse(model["manifest"]["redaction"]["topology_reconnected"])

    def test_fixed_elk_metadata_matches_shipped_bytes_and_license(self):
        resource, _ = management_panel.verify_layout_resource()
        bundle = panel_model.SKILL_ROOT / resource["bundle"]
        canonical = management_panel.canonical_utf8_lf_bytes(bundle.read_bytes(), bundle)
        actual = "sha256:" + hashlib.sha256(canonical).hexdigest()
        self.assertEqual(resource["engine_sha256"], actual)
        self.assertEqual("utf8-lf", resource["engine_sha256_mode"])
        self.assertEqual("EPL-2.0", resource["engine_license"])
        license_path = panel_model.SKILL_ROOT / resource["license"]
        license_hash = "sha256:" + hashlib.sha256(license_path.read_bytes()).hexdigest()
        self.assertEqual(resource["license_sha256"], license_hash)

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
            license_path.write_bytes(license_bytes)

            resource, elk_js = management_panel.verify_layout_resource(resource_path, skill_root)

            self.assertEqual(source_resource["engine_sha256"], resource["engine_sha256"])
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
            with self.assertRaises(management_panel.PanelError):
                management_panel.run(args)
            self.assertFalse((root / "memory/snapshots/management-panel").exists())
            self.assertFalse((root / "memory/views/management-panel").exists())
            self.assertTrue(any((root / "memory/audits/management-panel").glob("panel-input-audit-*.json")))

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
