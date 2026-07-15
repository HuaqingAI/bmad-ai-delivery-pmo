import copy
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SCRIPT_ROOT.parents[1]
PANEL_SCRIPTS = SKILLS_ROOT / "adp-management-panel/scripts"
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(PANEL_SCRIPTS))

import panel_audit
import management_panel
import panel_model


class PanelAuditTests(unittest.TestCase):
    def inputs(self, *, profile: str = "internal-full") -> dict:
        inputs = panel_model.load_source_fixture()
        resource, _ = management_panel.verify_layout_resource()
        args = Namespace(
            generated_at="2026-07-13T09:05:00Z",
            locale="zh-CN",
            history_limit=12,
        )
        inputs["request"] = management_panel.build_request(inputs, resource, args, profile)
        return inputs

    @staticmethod
    def inject_input_audit(inputs: dict, audit: dict) -> None:
        request = inputs["request"]
        request["panel_input_audit_id"] = audit["panel_input_audit_id"]
        request["panel_input_audit_disposition"] = audit["execution_disposition"]
        request["panel_input_audit_findings"] = [
            item["code"] for item in [*audit["blocking_gaps"], *audit["warnings"]]
        ]
        request["panel_input_audit_workflows"] = audit["recommended_workflows"]

    def candidate(self, inputs: dict):
        input_audit = panel_audit.audit_panel_inputs(inputs, as_of=date(2026, 7, 13))
        self.assertNotEqual("blocked", input_audit["execution_disposition"])
        self.inject_input_audit(inputs, input_audit)
        model = panel_model.compose_panel(inputs)
        _, elk_js = management_panel.verify_layout_resource()
        bundle = management_panel.canonical_json_bytes(model)
        rendered = management_panel.render_html(model, elk_js, "project-lead")
        return input_audit, model, bundle, rendered

    def codes(self, audit: dict) -> set[str]:
        return {item["code"] for item in [*audit["blocking_gaps"], *audit["warnings"]]}

    def test_pre_render_gate_seals_fresh_canonical_inputs_and_meeting_trace(self):
        inputs = self.inputs()
        inputs["meeting_packs"]["business-biweekly"]["readiness"] = "ready"
        audit = panel_audit.audit_panel_inputs(inputs, as_of=date(2026, 7, 13))
        self.assertEqual("ready", audit["execution_disposition"])
        self.assertTrue(audit["safe_to_render"])
        self.assertEqual("pre-meeting-snapshot", audit["meeting_trace"]["fde-morning"]["lifecycle"])
        self.assertEqual(
            inputs["meeting_packs"]["fde-morning"]["flow_selection_id"],
            audit["meeting_trace"]["fde-morning"]["flow_selection_id"],
        )
        self.assertEqual(inputs["flow_graph"].get("layout_id"), None)

    def test_resource_audit_accepts_windows_crlf_checkout_and_rejects_tampering(self):
        inputs = self.inputs()
        source_resource = panel_audit.load_json(panel_audit.RESOURCE_PATH)
        source_bundle = panel_model.SKILL_ROOT / source_resource["bundle"]
        source_license = panel_model.SKILL_ROOT / source_resource["license"]
        with tempfile.TemporaryDirectory() as folder:
            panel_root = Path(folder)
            resource_path = panel_root / "assets/elk-resource-v1.json"
            bundle = panel_root / source_resource["bundle"]
            license_path = panel_root / source_resource["license"]
            resource_path.parent.mkdir(parents=True)
            bundle.parent.mkdir(parents=True)
            license_path.parent.mkdir(parents=True, exist_ok=True)
            resource_path.write_text(json.dumps(source_resource), encoding="utf-8")
            bundle.write_bytes(source_bundle.read_bytes().replace(b"\n", b"\r\n"))
            license_bytes = source_license.read_bytes()
            license_path.write_bytes(license_bytes)

            _, errors, evidence = panel_audit._resource_validation(
                inputs["request"], resource_path, panel_root
            )

            self.assertEqual([], errors)
            self.assertEqual(source_resource["engine_sha256"], evidence["elk_bundle_sha256"])
            self.assertEqual(source_resource["license_sha256"], evidence["elk_license_sha256"])
            bundle.write_bytes(bundle.read_bytes() + b"tampered")
            _, errors, _ = panel_audit._resource_validation(inputs["request"], resource_path, panel_root)
            self.assertIn("ELK bundle checksum does not match resource metadata", errors)

            bundle.write_bytes(source_bundle.read_bytes().replace(b"\n", b"\r\n"))
            for index in [0, len(license_bytes) // 2, len(license_bytes) - 1]:
                with self.subTest(license_byte=index):
                    tampered = bytearray(license_bytes)
                    tampered[index] ^= 1
                    license_path.write_bytes(tampered)
                    _, errors, _ = panel_audit._resource_validation(
                        inputs["request"], resource_path, panel_root
                    )
                    self.assertIn("ELK license checksum does not match resource metadata", errors)

            license_path.write_bytes(license_bytes)
            resource_path.write_text(
                json.dumps({**source_resource, "engine_license": "EPL-2.0-modified"}),
                encoding="utf-8",
            )
            _, errors, _ = panel_audit._resource_validation(inputs["request"], resource_path, panel_root)
            self.assertIn("ELK resource engine_license must be EPL-2.0", errors)

    def test_pre_render_failure_and_recovery_matrix(self):
        cases = []

        stale = self.inputs()
        stale["program_status"]["generated_at"] = "2026-06-01T00:00:00Z"
        cases.append(("stale", stale, "blocked", "panel.input.freshness.stale", "adp-management-panel"))

        missing = self.inputs()
        del missing["roadmap"]
        cases.append(("missing", missing, "blocked", "panel.input.roadmap.missing", "adp-roadmap-sync"))

        bad_hash = self.inputs()
        bad_hash["program_status"]["source_fingerprints"]["views/program-status.json"] = "tampered"
        cases.append(("hash", bad_hash, "blocked", "panel.input.source-lineage.invalid", "adp-program-status"))

        progress = self.inputs()
        progress["roadmap"]["progress"]["overall"]["current"]["completion_gap_pp"] = 99
        cases.append(("progress", progress, "blocked", "panel.input.progress.identity-mismatch", "adp-roadmap-sync"))

        graph = self.inputs()
        graph["flow_graph"]["state"]["state_snapshot_id"] = "sha256:" + "0" * 64
        cases.append(("graph", graph, "blocked", "panel.input.flow-graph.schema-mismatch", "adp-flow-graph"))

        elk = self.inputs()
        elk["request"]["layout"]["engine_sha256"] = "sha256:" + "0" * 64
        cases.append(("elk", elk, "blocked", "panel.input.elk-asset.mismatch", "adp-setup"))

        lifecycle = self.inputs()
        lifecycle["meeting_packs"]["fde-morning"]["lifecycle"] = "invented"
        cases.append(("lifecycle", lifecycle, "blocked", "panel.input.meeting-lifecycle.invalid", "adp-meeting-pack"))

        false_official = self.inputs()
        false_official["meeting_packs"]["fde-morning"]["lifecycle"] = "post-sync-official"
        cases.append(("official", false_official, "blocked", "panel.input.meeting-lifecycle.official-association-invalid", "adp-meeting-sync"))

        scope = self.inputs()
        scope["meeting_packs"]["fde-morning"]["selected_node_ids"] = ["M-A"]
        cases.append(("scope", scope, "blocked", "panel.input.meeting-pack.flow-scope-mismatch", "adp-meeting-pack"))

        locale = self.inputs()
        locale["request"]["locale"] = "fr-FR"
        cases.append(("locale", locale, "degraded", "panel.locale.fallback", None))

        unsafe = self.inputs()
        unsafe["meeting_packs"]["business-biweekly"]["boards"]["business_decisions"][0]["summary"] = "</script><script>alert(1)</script>"
        cases.append(("unsafe", unsafe, "degraded", "panel.input.unsafe-source", "adp-management-panel"))

        for name, inputs, disposition, code, workflow in cases:
            with self.subTest(name=name):
                audit = panel_audit.audit_panel_inputs(inputs, as_of=date(2026, 7, 13))
                self.assertEqual(disposition, audit["execution_disposition"])
                self.assertIn(code, self.codes(audit))
                if workflow:
                    self.assertIn(workflow, audit["recommended_workflows"])

    def test_post_render_validates_embedded_model_runtime_svg_fallback_and_lineage(self):
        inputs = self.inputs()
        input_audit, model, bundle, rendered = self.candidate(inputs)
        audit = panel_audit.audit_panel_artifacts(
            model,
            bundle,
            rendered,
            input_audit=input_audit,
            source_inputs=inputs,
        )
        self.assertEqual("ready", audit["execution_disposition"])
        self.assertTrue(audit["safe_to_publish"])
        self.assertEqual(model["panel_id"], audit["panel_id"])
        self.assertEqual(
            model["data"]["meetings"]["fde-morning"]["readiness"],
            audit["meeting_trace"]["fde-morning"]["readiness"],
        )

    def test_post_render_rejects_tampered_manifest_schema_and_elk_runtime(self):
        for mutation, code in (
            ("manifest", "panel.artifact.embedded-model.mismatch"),
            ("schema", "panel.artifact.schema-mismatch"),
            ("elk", "panel.artifact.elk-embedded.mismatch"),
        ):
            with self.subTest(mutation=mutation):
                inputs = self.inputs()
                input_audit, model, bundle, rendered = self.candidate(inputs)
                if mutation == "manifest":
                    rendered = rendered.replace(
                        model["manifest"]["panel_id"].encode(),
                        ("sha256:" + "0" * 64).encode(),
                        1,
                    )
                elif mutation == "schema":
                    model = copy.deepcopy(model)
                    del model["manifest"]["layout_id"]
                    bundle = management_panel.canonical_json_bytes(model)
                    _, elk_js = management_panel.verify_layout_resource()
                    rendered = management_panel.render_html(model, elk_js, "project-lead")
                else:
                    rendered = rendered.replace(b"(function(f)", b"(function(x)", 1)
                audit = panel_audit.audit_panel_artifacts(
                    model,
                    bundle,
                    rendered,
                    input_audit=input_audit,
                    source_inputs=inputs,
                )
                self.assertEqual("blocked", audit["execution_disposition"])
                self.assertIn(code, self.codes(audit))

    def test_unsafe_source_remains_inert_and_traceable_after_render(self):
        inputs = self.inputs()
        attack = "</script><script>alert('x')</script><foreignObject onload=boom>"
        inputs["flow_graph"]["topology"]["nodes"][0]["name"] = attack
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
        input_audit, model, bundle, rendered = self.candidate(inputs)
        self.assertIn("panel.input.unsafe-source", self.codes(input_audit))
        audit = panel_audit.audit_panel_artifacts(
            model, bundle, rendered, input_audit=input_audit, source_inputs=inputs
        )
        self.assertEqual("ready", audit["execution_disposition"])
        self.assertNotIn(b"</script><script>alert", rendered.lower())
        self.assertNotIn(b"<foreignobject", rendered.lower())

    def test_archive_collision_is_blocked_without_rewriting_and_equal_archive_passes(self):
        inputs = self.inputs()
        input_audit, model, bundle, rendered = self.candidate(inputs)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle_path = root / f"{model['panel_id']}.json"
            html_path = root / f"{model['panel_id']}.html"
            bundle_path.write_bytes(bundle)
            html_path.write_bytes(rendered)
            before = html_path.read_bytes()
            passed = panel_audit.audit_panel_artifacts(
                model,
                bundle,
                rendered,
                input_audit=input_audit,
                source_inputs=inputs,
                publication_targets={"bundle": bundle_path, "html": html_path},
            )
            self.assertEqual("ready", passed["execution_disposition"])
            html_path.write_bytes(b"tampered archive")
            tampered = html_path.read_bytes()
            blocked = panel_audit.audit_panel_artifacts(
                model,
                bundle,
                rendered,
                input_audit=input_audit,
                source_inputs=inputs,
                publication_targets={"bundle": bundle_path, "html": html_path},
            )
            self.assertEqual("blocked", blocked["execution_disposition"])
            self.assertIn("panel.artifact.immutable-collision", self.codes(blocked))
            self.assertEqual(tampered, html_path.read_bytes())
            self.assertNotEqual(before, html_path.read_bytes())

    def test_post_render_detects_source_file_change_after_pre_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "canonical.json"
            source.write_text('{"version":1}\n', encoding="utf-8")
            inputs = self.inputs()
            inputs["_panel_source_paths"] = {"canonical": str(source)}
            input_audit, model, bundle, rendered = self.candidate(inputs)
            source.write_text('{"version":2}\n', encoding="utf-8")
            audit = panel_audit.audit_panel_artifacts(
                model, bundle, rendered, input_audit=input_audit, source_inputs=inputs
            )
            self.assertEqual("blocked", audit["execution_disposition"])
            self.assertIn("panel.artifact.input-source-hash.mismatch", self.codes(audit))

    def test_shareable_distribution_redaction_manifest_is_auditable(self):
        inputs = self.inputs(profile="shareable-summary")
        input_audit, model, bundle, rendered = self.candidate(inputs)
        audit = panel_audit.audit_panel_artifacts(
            model, bundle, rendered, input_audit=input_audit, source_inputs=inputs
        )
        self.assertEqual("ready", audit["execution_disposition"])
        self.assertEqual("shareable-summary", audit["distribution"]["profile"])
        self.assertFalse(audit["distribution"]["redaction"]["topology_reconnected"])
        exposed = json.dumps(model["data"], ensure_ascii=False)
        self.assertNotIn("G-MERGE", exposed)
        self.assertNotIn("internal-owner@example.com", exposed)

    def test_state_audit_cli_exposes_pre_and_post_panel_gates(self):
        inputs = self.inputs()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            memory = root / "memory"
            source = root / "panel-inputs.json"
            source.write_bytes(panel_audit.canonical_json_bytes(inputs))
            audit_script = SCRIPT_ROOT / "audit_state.py"
            pre = subprocess.run(
                [
                    sys.executable,
                    str(audit_script),
                    str(root),
                    "--scenario",
                    "management-panel",
                    "--panel-input-bundle",
                    str(source),
                    "--memory-root",
                    str(memory),
                    "--as-of",
                    "2026-07-13",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            pre_result = json.loads(pre.stdout)
            self.assertEqual("panel-input", pre_result["audit_type"])
            input_audit_path = Path(pre_result["outputs"]["json"])
            input_audit = json.loads(input_audit_path.read_text(encoding="utf-8"))
            self.inject_input_audit(inputs, input_audit)
            model = panel_model.compose_panel(inputs)
            _, elk_js = management_panel.verify_layout_resource()
            model_path = root / f"{model['panel_id']}.json"
            html_path = root / f"{model['panel_id']}.html"
            model_path.write_bytes(management_panel.canonical_json_bytes(model))
            html_path.write_bytes(management_panel.render_html(model, elk_js, "project-lead"))
            post = subprocess.run(
                [
                    sys.executable,
                    str(audit_script),
                    str(root),
                    "--phase",
                    "artifact",
                    "--scenario",
                    "management-panel",
                    "--panel-model",
                    str(model_path),
                    "--input-audit-json",
                    str(input_audit_path),
                    "--panel-input-bundle",
                    str(source),
                    "--artifact",
                    str(html_path),
                    "--memory-root",
                    str(memory),
                    "--as-of",
                    "2026-07-13",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            post_result = json.loads(post.stdout)
            self.assertEqual("panel-artifact", post_result["audit_type"])
            self.assertTrue(post_result["safe_to_publish"])


if __name__ == "__main__":
    unittest.main()
