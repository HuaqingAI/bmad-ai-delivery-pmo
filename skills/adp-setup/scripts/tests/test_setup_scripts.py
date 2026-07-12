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

        self.assertEqual(sorted(marketplace["skills"]), expected)
        self.assertEqual(sorted(marketplace["plugins"][0]["skills"]), expected)

    def test_module_help_registers_derived_readout_workflows(self) -> None:
        header, rows = self.read_csv(SKILL_ROOT / "assets" / "module-help.csv")
        skill_index = header.index("skill")
        output_index = header.index("output-location")
        row_by_skill = {row[skill_index]: row for row in rows}

        for skill in ("adp-state-audit", "adp-meeting-pack", "adp-roadmap-sync"):
            self.assertIn(skill, row_by_skill)

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

    def test_inspect_install_state_computes_defaults_and_missing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_yaml = root / "module.yaml"
            bmad_dir = root / "_bmad"
            write_module_yaml(module_yaml)
            (root / "skills").mkdir()
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
