#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

import copy
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adp_effective_config import resolve_effective_config
from baseline import (
    MARKER,
    command_create,
    command_inspect,
    command_update,
    command_validate,
    parse_baseline,
    paths,
    render_markdown,
    stamp_model,
    validate_model,
)


def approved_model() -> dict:
    source = {"type": "approved-plan", "reference": "docs/plan.md#baseline", "confirmed_by": "Program owner"}
    return {
        "schema_version": "1.0",
        "baseline_id": "PROGRAM-BASELINE",
        "confirmation_status": "approved",
        "project": {"name": "迁移项目", "owner": "Program owner", "target_date": "2026-12-31", "source": copy.deepcopy(source)},
        "default_tolerance_days": 0,
        "gates": [
            {
                "id": "GATE-DESIGN",
                "name": "Design approved",
                "planned_date": "2026-08-01",
                "owner": "Architecture owner",
                "confirmation_status": "approved",
                "source": copy.deepcopy(source),
                "dependencies": [],
                "critical_path": True,
            }
        ],
        "milestones": [
            {
                "id": "MS-CHECKOUT",
                "name": "Checkout complete",
                "workstream_id": "checkout",
                "planned_date": "2026-10-15",
                "owner": "Checkout FDE",
                "confirmation_status": "approved",
                "source": copy.deepcopy(source),
                "dependencies": ["GATE-DESIGN"],
                "critical_path": True,
                "completion_criteria": "Acceptance evidence approved",
            }
        ],
        "critical_path": ["GATE-DESIGN", "MS-CHECKOUT"],
        "weighting": {"enabled": False, "completion_measure": None, "source": None},
    }


class BaselineValidationTests(unittest.TestCase):
    def test_stored_model_requires_revision_timestamps_and_item_lineage(self) -> None:
        model = stamp_model(approved_model(), 1, "2026-07-12T00:00:00Z")
        model.pop("created_at")
        model["gates"][0].pop("baseline_revision")

        result = validate_model(model)

        self.assertFalse(result["valid"])
        paths_with_missing_fields = {item["path"] for item in result["findings"] if item["code"] == "field.missing"}
        self.assertIn("created_at", paths_with_missing_fields)
        self.assertIn("gates[0].baseline_revision", paths_with_missing_fields)

    def test_dependency_cycle_is_blocked(self) -> None:
        model = approved_model()
        model["gates"][0]["dependencies"] = ["MS-CHECKOUT"]

        result = validate_model(model, execute=True)

        self.assertFalse(result["valid"])
        self.assertTrue(any(item["code"] == "dependency.cycle" for item in result["findings"]))

    def test_weighting_requires_auditable_total_and_completion_criteria(self) -> None:
        model = approved_model()
        model["weighting"] = {
            "enabled": True,
            "completion_measure": "Accepted scope points",
            "source": {"type": "decision", "reference": "decisions.md#weights", "confirmed_by": "Program owner"},
        }
        model["milestones"][0]["weight"] = 90
        model["milestones"][0].pop("completion_criteria")

        result = validate_model(model, execute=True)

        codes = {item["code"] for item in result["findings"]}
        self.assertIn("weight.total", codes)
        self.assertIn("completion_criteria.missing", codes)

    def test_candidate_state_cannot_execute(self) -> None:
        model = approved_model()
        model["milestones"][0]["confirmation_status"] = "candidate"

        result = validate_model(model, execute=True)

        self.assertFalse(result["valid"])
        self.assertTrue(any(item["code"] == "confirmation.required" for item in result["findings"]))


class BaselineCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "_bmad").mkdir()
        (self.root / "_bmad" / "config.yaml").write_text(
            "document_output_language: Chinese\n"
            "communication_language: Chinese\n"
            "adp:\n"
            "  schedule_variance_tolerance_days: 2\n",
            encoding="utf-8",
        )
        _, self.config = resolve_effective_config(self.root)
        self.input_path = self.root / "baseline-input.json"
        self.input_path.write_text(json.dumps(approved_model(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_args(self, execute: bool, preview_token: str | None = None, as_of: str = "2026-07-12T00:00:00Z") -> Namespace:
        return Namespace(input=str(self.input_path), execute=execute, preview_token=preview_token, as_of=as_of)

    def execute_create(self) -> tuple[int, dict]:
        _, preview = command_create(self.create_args(False), self.root, self.config)
        return command_create(self.create_args(True, preview["preview_token"]), self.root, self.config)

    def write_canonical(self, model: dict) -> None:
        baseline_path, _ = paths(self.root)
        self.write_baseline(baseline_path, model)

    def write_baseline(self, path: Path, model: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{MARKER}\n\n```json\n{json.dumps(model, ensure_ascii=False)}\n```\n", encoding="utf-8")

    def test_create_dry_run_is_non_mutating_then_execute_writes_chinese_view(self) -> None:
        code, dry_run = command_create(self.create_args(False), self.root, self.config)
        baseline_path, _ = paths(self.root)

        self.assertEqual(code, 0)
        self.assertTrue(dry_run["dry_run"])
        self.assertFalse(baseline_path.exists())

        code, executed = command_create(self.create_args(True, dry_run["preview_token"], "2026-07-13T00:00:00Z"), self.root, self.config)

        self.assertEqual(code, 0)
        self.assertEqual(executed["status"], "complete")
        self.assertEqual(executed["preview_token"], dry_run["preview_token"])
        self.assertNotEqual(executed["baseline_fingerprint"], dry_run["baseline_fingerprint"])
        text = baseline_path.read_text(encoding="utf-8")
        self.assertIn("# ADP 项目计划基线", text)
        self.assertIn("已批准", text)
        self.assertIn("2026年12月31日", text)
        self.assertIn("Design approved", text)
        self.assertNotIn("设计已批准", text)
        parsed = parse_baseline(baseline_path)
        self.assertEqual(parsed["revision"], 1)
        self.assertEqual(parsed["confirmation_status"], "approved")
        self.assertEqual(parsed["project"]["target_date"], "2026-12-31")

    def test_existing_baseline_blocks_second_create(self) -> None:
        self.execute_create()

        code, result = command_create(self.create_args(True, "reviewed-token"), self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(result["findings"][0]["code"], "baseline.exists")

    def test_create_execute_requires_the_reviewed_preview_token(self) -> None:
        _, preview = command_create(self.create_args(False), self.root, self.config)
        changed = approved_model()
        changed["project"]["target_date"] = "2027-01-15"
        self.input_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

        code, result = command_create(self.create_args(True, preview["preview_token"]), self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(result["findings"][0]["code"], "preview.token_mismatch")
        self.assertFalse(paths(self.root)[0].exists())

    def test_create_execute_without_preview_token_is_blocked(self) -> None:
        code, result = command_create(self.create_args(True), self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(result["findings"][0]["code"], "preview.token_required")
        self.assertFalse(paths(self.root)[0].exists())

    def test_concurrent_create_allows_exactly_one_writer(self) -> None:
        _, preview = command_create(self.create_args(False), self.root, self.config)
        barrier = threading.Barrier(2)

        def create(_: int) -> tuple[int, dict]:
            barrier.wait()
            return command_create(self.create_args(True, preview["preview_token"]), self.root, self.config)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))

        self.assertEqual([code for code, _ in results].count(0), 1)
        self.assertEqual(parse_baseline(paths(self.root)[0])["revision"], 1)

    def test_update_requires_matching_revision_and_archives_exact_prior_model(self) -> None:
        self.execute_create()
        change_path = self.root / "change.json"
        change_path.write_text(
            json.dumps(
                {
                    "changes": {"project": {"target_date": "2027-01-15"}},
                    "change_reason": "Steering committee approved new target",
                    "decision_source": {"type": "decision", "reference": "decisions.md#D-12", "confirmed_by": "Sponsor"},
                }
            ),
            encoding="utf-8",
        )
        bad_args = Namespace(input=str(change_path), expected_revision=0, execute=True, preview_token=None, as_of="2026-07-13T00:00:00Z")

        code, conflict = command_update(bad_args, self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(conflict["findings"][0]["code"], "revision.conflict")

        preview_args = Namespace(input=str(change_path), expected_revision=1, execute=False, preview_token=None, as_of="2026-07-13T00:00:00Z")
        _, preview = command_update(preview_args, self.root, self.config)
        good_args = Namespace(input=str(change_path), expected_revision=1, execute=True, preview_token=preview["preview_token"], as_of="2026-07-13T00:00:00Z")
        code, updated = command_update(good_args, self.root, self.config)

        self.assertEqual(code, 0)
        self.assertEqual(updated["next_revision"], 2)
        baseline_path, history_path = paths(self.root)
        self.assertEqual(parse_baseline(history_path / "program-baseline-r1.md")["revision"], 1)
        current = parse_baseline(baseline_path)
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["project"]["target_date"], "2027-01-15")
        self.assertTrue(all(item["baseline_revision"] == 2 for item in current["gates"] + current["milestones"]))

    def test_update_with_only_change_control_metadata_is_blocked(self) -> None:
        self.execute_create()
        change_path = self.root / "empty-change.json"
        change_path.write_text(
            json.dumps(
                {
                    "changes": {},
                    "change_reason": "No factual change",
                    "decision_source": {"type": "decision", "reference": "decisions.md#D-13", "confirmed_by": "Sponsor"},
                }
            ),
            encoding="utf-8",
        )
        args = Namespace(input=str(change_path), expected_revision=1, execute=True, preview_token=None, as_of="2026-07-13T00:00:00Z")

        code, result = command_update(args, self.root, self.config)

        self.assertEqual(code, 1)
        self.assertTrue(any(item["code"] == "change.empty" for item in result["findings"]))
        self.assertEqual(parse_baseline(paths(self.root)[0])["revision"], 1)

    def test_update_token_binds_the_reviewed_current_baseline(self) -> None:
        self.execute_create()
        change_path = self.root / "change.json"
        change_path.write_text(
            json.dumps(
                {
                    "changes": {"project": {"target_date": "2027-01-15"}},
                    "change_reason": "Approved target change",
                    "decision_source": {"type": "decision", "reference": "decisions.md#D-12", "confirmed_by": "Sponsor"},
                }
            ),
            encoding="utf-8",
        )
        preview_args = Namespace(input=str(change_path), expected_revision=1, execute=False, preview_token=None, as_of="2026-07-13T00:00:00Z")
        _, preview = command_update(preview_args, self.root, self.config)
        self.assertEqual(len(preview["current_baseline_fingerprint"]), 64)
        current = parse_baseline(paths(self.root)[0])
        current["project"]["owner"] = "Changed after review"
        self.write_canonical(current)

        execute_args = Namespace(input=str(change_path), expected_revision=1, execute=True, preview_token=preview["preview_token"], as_of="2026-07-13T00:00:00Z")
        code, result = command_update(execute_args, self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(result["findings"][0]["code"], "preview.token_mismatch")
        self.assertFalse((paths(self.root)[1] / "program-baseline-r1.md").exists())

    def test_concurrent_update_allows_exactly_one_writer(self) -> None:
        self.execute_create()
        change_path = self.root / "change.json"
        change_path.write_text(
            json.dumps(
                {
                    "changes": {"project": {"target_date": "2027-01-15"}},
                    "change_reason": "Approved target change",
                    "decision_source": {"type": "decision", "reference": "decisions.md#D-12", "confirmed_by": "Sponsor"},
                }
            ),
            encoding="utf-8",
        )

        preview_args = Namespace(input=str(change_path), expected_revision=1, execute=False, preview_token=None, as_of="2026-07-13T00:00:00Z")
        _, preview = command_update(preview_args, self.root, self.config)
        barrier = threading.Barrier(2)

        def update(_: int) -> tuple[int, dict]:
            args = Namespace(input=str(change_path), expected_revision=1, execute=True, preview_token=preview["preview_token"], as_of="2026-07-13T00:00:00Z")
            barrier.wait()
            return command_update(args, self.root, self.config)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, range(2)))

        self.assertEqual([code for code, _ in results].count(0), 1)
        baseline_path, history_path = paths(self.root)
        self.assertEqual(parse_baseline(baseline_path)["revision"], 2)
        self.assertTrue((history_path / "program-baseline-r1.md").is_file())

    def test_render_derives_gate_critical_status_from_root_list(self) -> None:
        model = stamp_model(approved_model(), 1, "2026-07-12T00:00:00Z")
        model["gates"][0].pop("critical_path")

        without_item_flag = render_markdown(model, "en", {"fallbacks": []})
        model["gates"][0]["critical_path"] = False
        contradictory_item_flag = render_markdown(model, "en", {"fallbacks": []})
        model["critical_path"].remove("GATE-DESIGN")
        model["gates"][0]["critical_path"] = True
        absent_from_root = render_markdown(model, "en", {"fallbacks": []})

        self.assertIn("| GATE-DESIGN | Design approved | 2026-08-01 | Architecture owner | - | Yes |", without_item_flag)
        self.assertIn("| GATE-DESIGN | Design approved | 2026-08-01 | Architecture owner | - | Yes |", contradictory_item_flag)
        self.assertIn("| GATE-DESIGN | Design approved | 2026-08-01 | Architecture owner | - | No |", absent_from_root)

    def test_validate_and_inspect_block_missing_stored_lineage(self) -> None:
        self.execute_create()
        model = parse_baseline(paths(self.root)[0])
        model.pop("created_at")
        model.pop("updated_at")
        self.write_canonical(model)

        validate_code, validated = command_validate(Namespace(baseline=None), self.root, self.config)
        inspect_code, inspected = command_inspect(Namespace(revision=None), self.root, self.config)

        self.assertEqual(validate_code, 1)
        self.assertEqual(inspect_code, 1)
        self.assertFalse(validated["valid"])
        self.assertFalse(inspected["valid"])
        self.assertEqual({item["path"] for item in inspected["findings"] if item["code"] == "field.missing"}, {"created_at", "updated_at"})
        self.assertIn("recovery_command", validated)
        self.assertIn("recovery_command", inspected)

    def test_inspect_returns_findings_when_required_render_key_is_missing(self) -> None:
        self.execute_create()
        model = parse_baseline(paths(self.root)[0])
        model["project"].pop("name")
        self.write_canonical(model)

        code, result = command_inspect(Namespace(revision=None), self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(item["code"] == "project.name.missing" for item in result["findings"]))
        self.assertIn("recovery_command", result)

    def test_validate_and_historical_inspect_are_read_only(self) -> None:
        self.execute_create()
        validate_args = Namespace(baseline=None)
        code, validated = command_validate(validate_args, self.root, self.config)
        inspect_args = Namespace(revision=None)
        inspect_code, inspected = command_inspect(inspect_args, self.root, self.config)

        self.assertEqual(code, 0)
        self.assertTrue(validated["valid"])
        self.assertEqual(inspect_code, 0)
        self.assertEqual(inspected["project"]["name"], "迁移项目")
        self.assertIn("ADP 项目计划基线", inspected["summary_markdown"])

    def test_validate_explicit_baseline_uses_its_sibling_lineage(self) -> None:
        custom_baseline = self.root / "custom-memory" / "plans" / "program-baseline.md"
        self.write_baseline(custom_baseline, stamp_model(approved_model(), 1, "2026-07-13T00:00:00Z"))

        code, result = command_validate(Namespace(baseline=str(custom_baseline)), self.root, self.config)

        self.assertEqual(code, 0)
        self.assertTrue(result["valid"])
        self.assertEqual(result["lineage"]["current_path"], str(custom_baseline.resolve()))

    def test_validate_and_inspect_require_contiguous_archive_lineage(self) -> None:
        self.write_canonical(stamp_model(approved_model(), 2, "2026-07-13T00:00:00Z"))

        validate_code, validated = command_validate(Namespace(baseline=None), self.root, self.config)
        inspect_code, inspected = command_inspect(Namespace(revision=None), self.root, self.config)

        self.assertEqual(validate_code, 1)
        self.assertEqual(inspect_code, 1)
        self.assertTrue(any(item["code"] == "lineage.revision_missing" for item in validated["findings"]))
        self.assertTrue(any(item["code"] == "lineage.revision_missing" for item in inspected["findings"]))

    def test_historical_inspect_blocks_filename_revision_mismatch(self) -> None:
        current = stamp_model(approved_model(), 2, "2026-07-13T00:00:00Z")
        self.write_canonical(current)
        _, history_path = paths(self.root)
        self.write_baseline(history_path / "program-baseline-r1.md", stamp_model(approved_model(), 1, "2026-07-12T00:00:00Z"))
        self.write_baseline(history_path / "program-baseline-r9.md", current)

        code, result = command_inspect(Namespace(revision=9), self.root, self.config)

        self.assertEqual(code, 1)
        self.assertEqual(result["baseline_revision"], 2)
        self.assertTrue(any(item["code"] == "lineage.filename_revision_mismatch" for item in result["findings"]))

    def test_validate_checks_item_revisions_in_archives(self) -> None:
        current = stamp_model(approved_model(), 2, "2026-07-13T00:00:00Z")
        self.write_canonical(current)
        archived = stamp_model(approved_model(), 1, "2026-07-12T00:00:00Z")
        archived["gates"][0]["baseline_revision"] = 2
        self.write_baseline(paths(self.root)[1] / "program-baseline-r1.md", archived)

        code, result = command_validate(Namespace(baseline=None), self.root, self.config)

        self.assertEqual(code, 1)
        self.assertTrue(any(item["code"] == "lineage.item_revision_mismatch" for item in result["findings"]))


if __name__ == "__main__":
    unittest.main()
