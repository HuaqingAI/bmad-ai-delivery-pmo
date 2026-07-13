#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = SCRIPT_ROOT.parent
INSPECT_STATE = SCRIPT_ROOT / "inspect-install-state.py"
MERGE_CONFIG = SCRIPT_ROOT / "merge-config.py"
MERGE_HELP = SCRIPT_ROOT / "merge-help-csv.py"
CLEANUP_LEGACY = SCRIPT_ROOT / "cleanup-legacy.py"


def script_command(script: Path, *args: str) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", str(script), *args]
    return [sys.executable, str(script), *args]


def run_script(script: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        script_command(script, *args),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def write_module_yaml(path: Path, include_required: bool = False) -> None:
    lines = [
        "code: adp",
        "name: AI Delivery PMO",
        "description: Demo module",
        "module_version: 1.0.0",
        "default_selected: false",
        "delivery_root:",
        "  prompt: Delivery root?",
        "  default: adp/memory",
        '  result: "{project-root}/_bmad-output/{value}"',
        "personal_note:",
        "  prompt: Personal note?",
        "  default: default note",
        "  user_setting: true",
    ]
    if include_required:
        lines.extend(["required_value:", "  prompt: Required value?"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    help_path = path.with_name("module-help.csv")
    with help_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "module", "skill", "display-name", "menu-code", "description",
                "action", "args", "phase", "preceded-by", "followed-by", "required",
                "output-location", "outputs",
            ]
        )
        writer.writerow(
            [
                "AI Delivery PMO", "adp-setup", "Setup", "SU", "Install ADP.",
                "configure", "", "anytime", "", "", "false", "{project-root}/_bmad", "config",
            ]
        )
    installed_skill = path.parent / "skills" / "adp-setup"
    installed_skill.mkdir(parents=True, exist_ok=True)
    (installed_skill / "SKILL.md").write_text("# Setup\n", encoding="utf-8")


class AdpSetupScriptTests(unittest.TestCase):
    def test_marketplace_registers_all_adp_skills(self) -> None:
        repo_root = SKILL_ROOT.parents[1]
        marketplace = json.loads(
            (repo_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        expected = sorted(
            f"./skills/{path.name}"
            for path in SKILL_ROOT.parent.glob("adp-*")
            if (path / "SKILL.md").is_file()
        )

        expected_order = [
            "adp-setup",
            "adp-project-kickoff",
            "adp-plan-baseline",
            "adp-workstream-register",
            "adp-bmm-checkpoint-sync",
            "adp-meeting-sync",
            "adp-status-sync",
            "adp-risk-dependency-change-review",
            "adp-l0-reference-sync",
            "adp-acceptance-readiness-review",
            "adp-state-audit",
            "adp-program-status",
            "adp-roadmap-sync",
            "adp-meeting-pack",
            "adp-agent-program-lead",
        ]

        self.assertEqual(sorted(marketplace["skills"]), expected)
        self.assertEqual(sorted(marketplace["plugins"][0]["skills"]), expected)
        self.assertEqual([Path(path).name for path in marketplace["skills"]], expected_order)
        self.assertEqual(marketplace["version"], "1.2.0")
        self.assertEqual(marketplace["plugins"][0]["version"], "1.2.0")

    def test_module_help_registers_all_skills_in_lifecycle_order(self) -> None:
        header, rows = self.read_csv(SKILL_ROOT / "assets" / "module-help.csv")
        skill_index = header.index("skill")
        args_index = header.index("args")
        output_index = header.index("output-location")
        outputs_index = header.index("outputs")
        row_by_skill = {row[skill_index]: row for row in rows}

        expected_order = [
            "adp-setup",
            "adp-project-kickoff",
            "adp-plan-baseline",
            "adp-workstream-register",
            "adp-bmm-checkpoint-sync",
            "adp-meeting-sync",
            "adp-status-sync",
            "adp-risk-dependency-change-review",
            "adp-l0-reference-sync",
            "adp-acceptance-readiness-review",
            "adp-state-audit",
            "adp-program-status",
            "adp-roadmap-sync",
            "adp-meeting-pack",
            "adp-agent-program-lead",
        ]
        self.assertEqual([row[skill_index] for row in rows], expected_order)
        self.assertEqual(set(row_by_skill), set(expected_order))

        self.assertEqual(
            row_by_skill["adp-state-audit"][output_index],
            "{project-root}/_bmad-output/adp/memory/audits",
        )
        self.assertEqual(
            row_by_skill["adp-meeting-pack"][output_index],
            "{project-root}/_bmad-output/adp/memory/views/meeting-packs",
        )
        self.assertEqual(
            row_by_skill["adp-roadmap-sync"][output_index],
            "{project-root}/_bmad-output/adp/memory/views",
        )
        self.assertEqual(
            row_by_skill["adp-plan-baseline"][output_index],
            "{project-root}/_bmad-output/adp/memory/plans",
        )
        self.assertIn("immutable program-status snapshot", row_by_skill["adp-program-status"][-1])
        self.assertIn("--candidate-id <id>", row_by_skill["adp-bmm-checkpoint-sync"][args_index])
        self.assertNotIn("--execute", row_by_skill["adp-status-sync"][args_index])
        self.assertEqual(row_by_skill["adp-agent-program-lead"][output_index], "")
        self.assertNotIn("project lead and weekly views", row_by_skill["adp-agent-program-lead"][outputs_index])

    def test_vnext_module_defaults_and_installed_resources_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = json.loads(
                run_script(
                    INSPECT_STATE,
                    temp_dir,
                    "--module-yaml",
                    str(SKILL_ROOT / "assets" / "module.yaml"),
                    "--module-help",
                    str(SKILL_ROOT / "assets" / "module-help.csv"),
                    "--installed-skills-dir",
                    str(SKILL_ROOT.parent),
                ).stdout
            )

            self.assertEqual(result["module"]["version"], "1.2.0")
            self.assertEqual(
                result["effective_defaults"]["module"],
                {
                    "default_reporting_cadence": "weekly",
                    "status_stale_after_days": 7,
                    "schedule_variance_tolerance_days": 0,
                    "meeting_pack_item_limit": 10,
                },
            )
            self.assertTrue(result["headless_ready"])
            self.assertTrue(result["installation_ready"])
            self.assertEqual(result["upgrade_report"]["version_status"], "fresh_install")
            self.assertEqual(result["upgrade_report"]["memory"]["status"], "not_initialized")
            self.assertEqual(result["installed_skill_inspection"]["missing_skills"], [])
            self.assertEqual(result["installed_skill_inspection"]["missing_shared_resources"], [])

    def test_inspect_reports_update_and_preserves_existing_team_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bmad_dir = root / "_bmad"
            bmad_dir.mkdir()
            (bmad_dir / "config.yaml").write_text(
                "\n".join(
                    [
                        "output_folder: '{project-root}/_bmad-output'",
                        "adp:",
                        "  version: 1.1.0",
                        "  default_reporting_cadence: biweekly",
                        "  status_stale_after_days: 14",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = json.loads(
                run_script(
                    INSPECT_STATE,
                    str(root),
                    "--module-yaml",
                    str(SKILL_ROOT / "assets" / "module.yaml"),
                    "--installed-skills-dir",
                    str(SKILL_ROOT.parent),
                ).stdout
            )

            self.assertEqual(result["install_state"], "update")
            self.assertEqual(result["upgrade_report"]["version_status"], "upgrade")
            self.assertEqual(result["effective_defaults"]["module"]["default_reporting_cadence"], "biweekly")
            self.assertEqual(result["default_sources"]["module"]["status_stale_after_days"], "existing")
            self.assertEqual(
                result["upgrade_report"]["defaulted_module_variables"],
                ["meeting_pack_item_limit", "schedule_variance_tolerance_days"],
            )

    def test_inspect_blocks_incomplete_installed_skill_or_shared_resource_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skills_dir = root / "installed-skills"
            skills_dir.mkdir()
            header, rows = self.read_csv(SKILL_ROOT / "assets" / "module-help.csv")
            skill_index = header.index("skill")
            for row in rows:
                skill_dir = skills_dir / row[skill_index]
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
            (skills_dir / "adp-program-status" / "SKILL.md").unlink()

            result = json.loads(
                run_script(
                    INSPECT_STATE,
                    str(root),
                    "--module-yaml",
                    str(SKILL_ROOT / "assets" / "module.yaml"),
                    "--installed-skills-dir",
                    str(skills_dir),
                ).stdout
            )

            self.assertFalse(result["headless_ready"])
            self.assertFalse(result["installation_ready"])
            self.assertEqual(result["installed_skill_inspection"]["missing_skills"], ["adp-program-status"])
            self.assertEqual(
                result["installed_skill_inspection"]["missing_shared_resources"],
                [
                    "adp-plan-baseline/scripts/adp_effective_config.py",
                    "adp-plan-baseline/assets/locale-catalog.json",
                ],
            )
            self.assertTrue(any("Missing installed skills" in gap for gap in result["unresolved_gaps"]))

    def test_inspect_install_state_computes_defaults_and_missing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            bmad_dir = root / "_bmad"
            write_module_yaml(module_yaml)
            (root / "skills").mkdir(exist_ok=True)
            (bmad_dir / "adp").mkdir(parents=True)
            (bmad_dir / "core").mkdir(parents=True)
            (bmad_dir / "config.yaml").write_text(
                "output_folder: '{project-root}/existing-output'\nadp:\n  delivery_root: '{project-root}/existing-memory'\n",
                encoding="utf-8",
            )
            (bmad_dir / "config.user.yaml").write_text("user_name: Ada\n", encoding="utf-8")
            (bmad_dir / "core" / "config.yaml").write_text(
                "communication_language: French\ndocument_output_language: French\n",
                encoding="utf-8",
            )
            (bmad_dir / "adp" / "config.yaml").write_text(
                "personal_note: legacy note\nremoved_key: ignored\n",
                encoding="utf-8",
            )

            result = json.loads(
                run_script(INSPECT_STATE, str(root), "--module-yaml", str(module_yaml)).stdout
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["install_state"], "legacy_migration")
            self.assertTrue(result["headless_ready"])
            self.assertEqual(result["missing_required_inputs"], [])
            self.assertEqual(result["effective_defaults"]["core"]["user_name"], "Ada")
            self.assertEqual(result["effective_defaults"]["core"]["communication_language"], "French")
            self.assertEqual(result["effective_defaults"]["module"]["delivery_root"], "{project-root}/existing-memory")
            self.assertEqual(result["effective_defaults"]["module"]["personal_note"], "legacy note")
            self.assertIn(str((root / "existing-output").resolve()), result["directories_to_create"])
            self.assertIn(str((root / "existing-memory").resolve()), result["directories_to_create"])
            self.assertEqual(result["installed_skills_dir"], str((root / "skills").resolve()))
            self.assertEqual(result["installed_skills_dir_source"], "default")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            write_module_yaml(module_yaml, include_required=True)

            result = json.loads(
                run_script(INSPECT_STATE, str(root), "--module-yaml", str(module_yaml)).stdout
            )

            self.assertFalse(result["headless_ready"])
            self.assertEqual(result["missing_required_inputs"][0]["key"], "required_value")
            self.assertIn("required_value", result["unresolved_gaps"][0])

            answers = root / "answers.json"
            answers.write_text(
                json.dumps({"module": {"required_value": "provided"}}),
                encoding="utf-8",
            )
            validated = json.loads(
                run_script(
                    INSPECT_STATE,
                    str(root),
                    "--module-yaml",
                    str(module_yaml),
                    "--answers",
                    str(answers),
                ).stdout
            )

            self.assertTrue(validated["headless_ready"])
            self.assertEqual(validated["missing_required_inputs"], [])
            self.assertEqual(validated["validated_answers"]["module"]["required_value"], "provided")

    def test_scripts_reject_unknown_answer_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            answers = root / "answers.json"
            write_module_yaml(module_yaml)
            answers.write_text(
                json.dumps(
                    {
                        "core": {"unknown_core": "ignored"},
                        "module": {"delivery_rooot": "typo"},
                    }
                ),
                encoding="utf-8",
            )

            inspected = run_script(
                INSPECT_STATE,
                str(root),
                "--module-yaml",
                str(module_yaml),
                "--answers",
                str(answers),
                check=False,
            )
            inspect_result = json.loads(inspected.stdout)

            self.assertEqual(inspected.returncode, 1)
            self.assertEqual(inspect_result["status"], "error")
            self.assertIn("unknown_core", inspect_result["error"])
            self.assertIn("delivery_rooot", inspect_result["error"])

            config_path = root / "_bmad" / "config.yaml"
            user_config_path = root / "_bmad" / "config.user.yaml"
            merged = run_script(
                MERGE_CONFIG,
                "--config-path",
                str(config_path),
                "--user-config-path",
                str(user_config_path),
                "--module-yaml",
                str(module_yaml),
                "--answers",
                str(answers),
                check=False,
            )
            merge_result = json.loads(merged.stdout)

            self.assertEqual(merged.returncode, 1)
            self.assertEqual(merge_result["status"], "error")
            self.assertIn("unknown_core", merge_result["error"])
            self.assertIn("delivery_rooot", merge_result["error"])
            self.assertFalse(config_path.exists())
            self.assertFalse(user_config_path.exists())

    def test_vnext_module_values_reject_invalid_ranges_and_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            answers = root / "answers.json"
            answers.write_text(
                json.dumps(
                    {
                        "module": {
                            "default_reporting_cadence": "daily",
                            "status_stale_after_days": 0,
                            "schedule_variance_tolerance_days": 91,
                            "meeting_pack_item_limit": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )

            inspected = run_script(
                INSPECT_STATE,
                str(root),
                "--module-yaml",
                str(SKILL_ROOT / "assets" / "module.yaml"),
                "--installed-skills-dir",
                str(SKILL_ROOT.parent),
                "--answers",
                str(answers),
                check=False,
            )
            result = json.loads(inspected.stdout)

            self.assertEqual(inspected.returncode, 1)
            self.assertIn("default_reporting_cadence must be one of", result["error"])
            self.assertIn("status_stale_after_days must be at least 1", result["error"])
            self.assertIn("schedule_variance_tolerance_days must be at most 90", result["error"])
            self.assertIn("meeting_pack_item_limit must be at least 3", result["error"])

    def test_module_yaml_user_facing_fields_are_readable_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = json.loads(
                run_script(
                    INSPECT_STATE,
                    temp_dir,
                    "--module-yaml",
                    str(SKILL_ROOT / "assets" / "module.yaml"),
                ).stdout
            )
            module_text = json.dumps(result["module"], ensure_ascii=False)
            mojibake_signatures = ["�", "鈥", "銆", "馃", "鐨", "瑁", "鍒", "鐒", "骞", "搳"]

            self.assertIn("帮助多条 FDE 工作线", result["module"]["description"])
            self.assertEqual(result["module"]["agents"][0]["icon"], "📊")
            customize = tomllib.loads(
                (SKILL_ROOT.parent / "adp-agent-program-lead" / "customize.toml").read_text(encoding="utf-8")
            )["agent"]
            roster = result["module"]["agents"][0]
            for key in ("code", "name", "title", "icon", "description", "agent_type"):
                self.assertEqual(roster[key], customize[key])
            for signature in mojibake_signatures:
                self.assertNotIn(signature, module_text)

    def test_merge_config_accepts_bom_answers_and_creates_output_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            answers = root / "answers.json"
            config_path = root / "_bmad" / "config.yaml"
            user_config_path = root / "_bmad" / "config.user.yaml"
            write_module_yaml(module_yaml)
            config_path.parent.mkdir()
            config_path.write_text(
                "output_folder: '{project-root}/old-output'\nadp:\n  stale_key: stale\n",
                encoding="utf-8",
            )
            answers.write_bytes(
                b"\xef\xbb\xbf"
                + json.dumps(
                    {
                        "core": {
                            "user_name": "Ada",
                            "communication_language": "English",
                            "document_output_language": "English",
                            "output_folder": "{project-root}/_bmad-output",
                        },
                        "module": {
                            "delivery_root": "adp/memory",
                            "personal_note": "private",
                        },
                    }
                ).encode("utf-8")
            )

            completed = run_script(
                MERGE_CONFIG,
                "--config-path",
                str(config_path),
                "--user-config-path",
                str(user_config_path),
                "--module-yaml",
                str(module_yaml),
                "--answers",
                str(answers),
                "--create-output-dirs",
            )
            result = json.loads(completed.stdout)
            output_dir = (root / "_bmad-output").resolve()
            delivery_dir = (root / "_bmad-output" / "adp" / "memory").resolve()

            config_text = config_path.read_text(encoding="utf-8")
            user_text = user_config_path.read_text(encoding="utf-8")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["user_keys"], ["user_name", "communication_language", "personal_note"])
            self.assertIn("document_output_language: English", config_text)
            self.assertIn("delivery_root: '{project-root}/_bmad-output/adp/memory'", config_text)
            self.assertNotIn("stale_key", config_text)
            self.assertNotIn("user_name", config_text)
            self.assertIn("user_name: Ada", user_text)
            self.assertIn("personal_note: private", user_text)
            self.assertTrue(output_dir.is_dir())
            self.assertTrue(delivery_dir.is_dir())
            self.assertEqual(result["directories_to_create"], [str(output_dir), str(delivery_dir)])
            self.assertEqual(result["directories_created"], [str(output_dir), str(delivery_dir)])

    def test_merge_config_migrates_legacy_defaults_and_deletes_legacy_configs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            answers = root / "answers.json"
            bmad_dir = root / "_bmad"
            legacy_core = bmad_dir / "core" / "config.yaml"
            legacy_adp = bmad_dir / "adp" / "config.yaml"
            write_module_yaml(module_yaml)
            legacy_core.parent.mkdir(parents=True)
            legacy_adp.parent.mkdir(parents=True)
            legacy_core.write_text(
                "\n".join(
                    [
                        "user_name: Legacy User",
                        "communication_language: French",
                        "document_output_language: French",
                        "output_folder: '{project-root}/legacy-output'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            legacy_adp.write_text(
                "delivery_root: legacy-memory\npersonal_note: legacy note\nremoved_key: ignored\n",
                encoding="utf-8",
            )
            answers.write_text(json.dumps({"module": {"personal_note": "fresh note"}}), encoding="utf-8")

            completed = run_script(
                MERGE_CONFIG,
                "--config-path",
                str(bmad_dir / "config.yaml"),
                "--user-config-path",
                str(bmad_dir / "config.user.yaml"),
                "--module-yaml",
                str(module_yaml),
                "--answers",
                str(answers),
                "--legacy-dir",
                str(bmad_dir),
            )
            result = json.loads(completed.stdout)

            config_text = (bmad_dir / "config.yaml").read_text(encoding="utf-8")
            user_text = (bmad_dir / "config.user.yaml").read_text(encoding="utf-8")
            self.assertEqual(result["status"], "success")
            self.assertEqual(sorted(Path(path).parent.name for path in result["legacy_configs_deleted"]), ["adp", "core"])
            self.assertFalse(legacy_core.exists())
            self.assertFalse(legacy_adp.exists())
            self.assertIn("output_folder: '{project-root}/legacy-output'", config_text)
            self.assertIn("delivery_root: '{project-root}/_bmad-output/legacy-memory'", config_text)
            self.assertIn("user_name: Legacy User", user_text)
            self.assertIn("personal_note: fresh note", user_text)
            self.assertNotIn("removed_key", config_text)

    def test_vnext_legacy_team_config_migrates_with_sources_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bmad_dir = root / "_bmad"
            legacy_adp = bmad_dir / "adp" / "config.yaml"
            legacy_adp.parent.mkdir(parents=True)
            legacy_adp.write_text(
                "\n".join(
                    [
                        "default_reporting_cadence: custom",
                        "status_stale_after_days: 21",
                        "schedule_variance_tolerance_days: 4",
                        "meeting_pack_item_limit: 12",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            inspected = json.loads(
                run_script(
                    INSPECT_STATE,
                    str(root),
                    "--module-yaml",
                    str(SKILL_ROOT / "assets" / "module.yaml"),
                    "--installed-skills-dir",
                    str(SKILL_ROOT.parent),
                ).stdout
            )
            answers = root / "answers.json"
            answers.write_text(json.dumps(inspected["validated_answers"]), encoding="utf-8")

            merged = json.loads(
                run_script(
                    MERGE_CONFIG,
                    "--config-path",
                    str(bmad_dir / "config.yaml"),
                    "--user-config-path",
                    str(bmad_dir / "config.user.yaml"),
                    "--module-yaml",
                    str(SKILL_ROOT / "assets" / "module.yaml"),
                    "--answers",
                    str(answers),
                    "--legacy-dir",
                    str(bmad_dir),
                ).stdout
            )
            config_text = (bmad_dir / "config.yaml").read_text(encoding="utf-8")

            self.assertEqual(inspected["install_state"], "fresh_install_with_legacy")
            self.assertTrue(all(source == "legacy" for source in inspected["default_sources"]["module"].values()))
            self.assertEqual(merged["status"], "success")
            self.assertIn("version: 1.2.0", config_text)
            self.assertIn("default_reporting_cadence: custom", config_text)
            self.assertIn("status_stale_after_days: 21", config_text)
            self.assertIn("meeting_pack_item_limit: 12", config_text)
            self.assertFalse(legacy_adp.exists())

    def test_merge_config_creates_output_dirs_before_writing_configs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            answers = root / "answers.json"
            config_path = root / "_bmad" / "config.yaml"
            user_config_path = root / "_bmad" / "config.user.yaml"
            blocked_output = root / "_bmad-output"
            write_module_yaml(module_yaml)
            blocked_output.write_text("not a directory", encoding="utf-8")
            answers.write_text(
                json.dumps(
                    {
                        "core": {
                            "user_name": "Ada",
                            "communication_language": "English",
                            "document_output_language": "English",
                            "output_folder": "{project-root}/_bmad-output",
                        },
                        "module": {"delivery_root": "adp/memory"},
                    }
                ),
                encoding="utf-8",
            )

            completed = run_script(
                MERGE_CONFIG,
                "--config-path",
                str(config_path),
                "--user-config-path",
                str(user_config_path),
                "--module-yaml",
                str(module_yaml),
                "--answers",
                str(answers),
                "--create-output-dirs",
                check=False,
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(result["status"], "error")
            self.assertIn("Failed to create output directory", result["error"])
            self.assertFalse(config_path.exists())
            self.assertFalse(user_config_path.exists())

    def test_scripts_reject_unresolved_project_root_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            answers = root / "answers.json"
            source_csv = root / "source.csv"
            write_module_yaml(module_yaml)
            answers.write_text(json.dumps({"module": {}}), encoding="utf-8")
            self.write_csv(source_csv, [["AI Delivery PMO", "adp-setup", "Setup", "SU", "desc", "configure", "", "anytime", "", "", "false", "{project-root}/_bmad", "config"]])

            cases = [
                (
                    INSPECT_STATE,
                    [
                        "{project-root}",
                        "--module-yaml",
                        str(module_yaml),
                    ],
                ),
                (
                    MERGE_CONFIG,
                    [
                        "--config-path",
                        "{project-root}/_bmad/config.yaml",
                        "--user-config-path",
                        str(root / "user.yaml"),
                        "--module-yaml",
                        str(module_yaml),
                        "--answers",
                        str(answers),
                    ],
                ),
                (
                    MERGE_HELP,
                    [
                        "--target",
                        "{project-root}/_bmad/module-help.csv",
                        "--source",
                        str(source_csv),
                    ],
                ),
                (
                    CLEANUP_LEGACY,
                    [
                        "--bmad-dir",
                        "{project-root}/_bmad",
                        "--module-code",
                        "adp",
                    ],
                ),
            ]

            for script, args in cases:
                with self.subTest(script=script.name):
                    completed = run_script(script, *args, check=False)
                    combined_output = completed.stdout + completed.stderr
                    self.assertEqual(completed.returncode, 1)
                    self.assertIn("Unresolved '{project-root}' token", combined_output)

    def test_merge_help_preserves_row_width_and_cleans_legacy_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bmad_dir = root / "_bmad"
            target = bmad_dir / "module-help.csv"
            source = root / "module-help.csv"
            legacy_adp = bmad_dir / "adp" / "module-help.csv"
            legacy_core = bmad_dir / "core" / "module-help.csv"
            rows = [
                ["Other Module", "other-skill", "Other", "OT", "desc", "run", "", "anytime", "", "", "false", "{project-root}/other", "other output"],
                ["AI Delivery PMO", "old-adp", "Old", "OA", "stale", "run", "", "anytime", "", "", "false", "{project-root}/old", ""],
            ]
            source_row = ["AI Delivery PMO", "adp-setup", "Setup", "SU", "desc, with comma", "configure", "{-H: headless mode}", "anytime", "", "adp-project-kickoff:kickoff", "false", "{project-root}/_bmad", "config.yaml, module-help.csv"]
            self.write_csv(target, rows)
            self.write_csv(source, [source_row])
            legacy_adp.parent.mkdir(parents=True)
            legacy_core.parent.mkdir(parents=True)
            legacy_adp.write_text("legacy\n", encoding="utf-8")
            legacy_core.write_text("legacy\n", encoding="utf-8")

            completed = run_script(
                MERGE_HELP,
                "--target",
                str(target),
                "--source",
                str(source),
                "--legacy-dir",
                str(bmad_dir),
                "--module-code",
                "adp",
            )
            result = json.loads(completed.stdout)
            header, merged_rows = self.read_csv(target)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["rows_removed"], 1)
            self.assertEqual(result["rows_added"], 1)
            self.assertFalse(legacy_adp.exists())
            self.assertFalse(legacy_core.exists())
            self.assertEqual(len(merged_rows), 2)
            self.assertTrue(all(len(row) == len(header) for row in merged_rows))
            self.assertEqual(merged_rows[1], source_row)

    def test_cleanup_legacy_removes_verified_dirs_and_blocks_missing_installed_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bmad_dir = root / "_bmad"
            skills_dir = root / ".claude" / "skills"
            legacy_skill = bmad_dir / "adp" / "skills" / "adp-example"
            installed_skill = skills_dir / "adp-example"
            legacy_skill.mkdir(parents=True)
            installed_skill.mkdir(parents=True)
            (legacy_skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
            (legacy_skill / "file.txt").write_text("legacy\n", encoding="utf-8")
            (bmad_dir / "core").mkdir(parents=True)
            (bmad_dir / "core" / "note.txt").write_text("legacy\n", encoding="utf-8")
            (bmad_dir / "_config").mkdir(parents=True)
            (bmad_dir / "_config" / "settings.toml").write_text("legacy\n", encoding="utf-8")

            completed = run_script(
                CLEANUP_LEGACY,
                "--bmad-dir",
                str(bmad_dir),
                "--module-code",
                "adp",
                "--also-remove",
                "_config",
                "--skills-dir",
                str(skills_dir),
            )
            result = json.loads(completed.stdout)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["directories_removed"], ["adp", "core", "_config"])
            self.assertEqual(result["safety_checks"]["verified_skills"], ["adp-example"])
            self.assertFalse((bmad_dir / "adp").exists())
            self.assertFalse((bmad_dir / "core").exists())
            self.assertFalse((bmad_dir / "_config").exists())

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bmad_dir = root / "_bmad"
            skills_dir = root / ".claude" / "skills"
            legacy_skill = bmad_dir / "adp" / "skills" / "missing-skill"
            legacy_skill.mkdir(parents=True)
            skills_dir.mkdir(parents=True)
            (legacy_skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

            completed = run_script(
                CLEANUP_LEGACY,
                "--bmad-dir",
                str(bmad_dir),
                "--module-code",
                "adp",
                "--skills-dir",
                str(skills_dir),
                check=False,
            )
            result = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["missing_skills"], ["missing-skill"])
            self.assertTrue((bmad_dir / "adp").exists())

    def write_csv(self, path: Path, rows: list[list[str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "module",
                    "skill",
                    "display-name",
                    "menu-code",
                    "description",
                    "action",
                    "args",
                    "phase",
                    "preceded-by",
                    "followed-by",
                    "required",
                    "output-location",
                    "outputs",
                ]
            )
            writer.writerows(rows)

    def read_csv(self, path: Path) -> tuple[list[str], list[list[str]]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        return rows[0], rows[1:]


if __name__ == "__main__":
    unittest.main()
