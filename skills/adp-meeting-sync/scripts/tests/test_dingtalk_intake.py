import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "dingtalk_intake.py"


class DingTalkIntakeTests(unittest.TestCase):
    def make_memory(self, project_root: Path) -> Path:
        memory_root = project_root / "_bmad-output" / "adp" / "memory"
        (memory_root / "meetings").mkdir(parents=True, exist_ok=True)
        (memory_root / "daily").mkdir(parents=True, exist_ok=True)
        (memory_root / "meetings" / "existing.md").write_text(
            "source: DingTalk AI Minutes taskUuid=task-processed; evidence=transcription\n",
            encoding="utf-8",
        )
        return memory_root

    def write_fake_dws(self, project_root: Path) -> Path:
        fake = project_root / "fake_dws.py"
        fake.write_text(
            textwrap.dedent(
                """
                import json
                import sys

                args = sys.argv[1:]

                if args[:3] == ["minutes", "list", "all"]:
                    if "--start" in args or "--end" in args:
                        start = args[args.index("--start") + 1] if "--start" in args else ""
                        end = args[args.index("--end") + 1] if "--end" in args else ""
                        if start == "2026-07-06" or end == "2026-07-06":
                            print(json.dumps({"error": "date-only filter was not expanded"}))
                            sys.exit(3)
                        print(json.dumps({"items": []}))
                        sys.exit(0)
                    print(json.dumps({
                        "items": [
                            {
                                "taskUuid": "task-processed",
                                "title": "Processed sync",
                                "startTime": "2026-07-01T09:00:00+08:00",
                                "aiMinutesUrl": "https://minutes.example/processed",
                                "keywords": ["checkout"]
                            },
                            {
                                "taskUuid": "task-new",
                                "title": "Checkout sync",
                                "startTime": "2026-07-02T09:00:00+08:00",
                                "aiMinutesUrl": "https://minutes.example/new",
                                "keywords": ["checkout", "risk"]
                            },
                            {
                                "taskUuid": "task-date",
                                "title": "Morning sync",
                                "startTime": "2026-07-06T09:34:00+08:00",
                                "aiMinutesUrl": "https://minutes.example/date",
                                "keywords": ["评论模块", "数据回流"]
                            }
                        ]
                    }))
                elif args[:3] == ["minutes", "get", "info"]:
                    task_id = args[args.index("--id") + 1]
                    print(json.dumps({
                        "taskUuid": task_id,
                        "title": "Paragraph sync" if task_id == "task-paragraph" else "Checkout sync",
                        "startTime": "2026-07-02T09:00:00+08:00",
                        "aiMinutesUrl": "https://minutes.example/new"
                    }))
                elif args[:3] == ["minutes", "get", "transcription"]:
                    task_id = args[args.index("--id") + 1]
                    if task_id == "task-paragraph" and "--next-token" in args:
                        print(json.dumps({
                            "paragraphList": [
                                {
                                    "speakerName": "PM-B",
                                    "sentenceList": [
                                        {"text": "Second paragraph action."}
                                    ]
                                }
                            ]
                        }))
                    elif task_id == "task-paragraph":
                        print(json.dumps({
                            "paragraphList": [
                                {
                                    "speakerName": "FDE-A",
                                    "sentenceList": [
                                        {"text": "First paragraph fact."},
                                        {"text": "First paragraph decision."}
                                    ]
                                }
                            ],
                            "nextToken": "page-2"
                        }))
                    elif "--next-token" in args:
                        print(json.dumps({
                            "segments": [
                                {"speaker": "FDE-A", "text": "Second page action."}
                            ]
                        }))
                    else:
                        print(json.dumps({
                            "segments": [
                                {"speaker": "FDE-A", "text": "First page fact."}
                            ],
                            "nextToken": "page-2"
                        }))
                else:
                    print(json.dumps({"error": args}))
                    sys.exit(2)
                """
            ).lstrip(),
            encoding="utf-8",
        )
        return fake

    def run_script(self, project_root: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_discovery_marks_processed_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            fake_dws = self.write_fake_dws(project_root)

            result = self.run_script(
                project_root,
                "--dws-command",
                f'"{sys.executable}" "{fake_dws}"',
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "discover")
            processed = [item for item in result["candidates"] if item["taskUuid"] == "task-processed"][0]
            unprocessed = [item for item in result["candidates"] if item["taskUuid"] == "task-new"][0]
            self.assertTrue(processed["processed"])
            self.assertIn("taskUuid found", processed["processed_reason"])
            self.assertFalse(unprocessed["processed"])
            self.assertEqual(unprocessed["processed_reason"], "unprocessed")
            self.assertIn("--task-uuid", result["next_actions"][0])

    def test_date_only_filter_expands_and_falls_back_to_local_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.make_memory(project_root)
            fake_dws = self.write_fake_dws(project_root)

            result = self.run_script(
                project_root,
                "--start",
                "2026-07-06",
                "--end",
                "2026-07-06",
                "--dws-command",
                f'"{sys.executable}" "{fake_dws}"',
            )

            self.assertTrue(result["ok"])
            list_args = result["diagnostics"]["list_args"]
            self.assertTrue(list_args[list_args.index("--start") + 1].startswith("2026-07-06T00:00:00"))
            self.assertTrue(list_args[list_args.index("--end") + 1].startswith("2026-07-06T23:59:59"))
            self.assertTrue(result["diagnostics"]["date_filter_fallback_used"])
            self.assertEqual([item["taskUuid"] for item in result["candidates"]], ["task-date"])

    def test_same_date_title_is_possible_match_not_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            (memory_root / "daily" / "2026-07-02.md").write_text(
                "2026-07-02 Checkout sync may already be archived.\n",
                encoding="utf-8",
            )
            fake_dws = self.write_fake_dws(project_root)

            result = self.run_script(
                project_root,
                "--dws-command",
                f'"{sys.executable}" "{fake_dws}"',
            )

            candidate = [item for item in result["candidates"] if item["taskUuid"] == "task-new"][0]
            self.assertFalse(candidate["processed"])
            self.assertEqual(candidate["processed_reason"], "unprocessed")
            self.assertEqual(candidate["possible_matches"][0]["kind"], "same_date_same_title")
            self.assertIn("2026-07-02.md", candidate["possible_matches"][0]["path"])

    def test_fetch_paginates_and_saves_raw_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            fake_dws = self.write_fake_dws(project_root)

            result = self.run_script(
                project_root,
                "--task-uuid",
                "task-new",
                "--dws-command",
                f'"{sys.executable}" "{fake_dws}"',
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "fetch")
            self.assertTrue(result["transcript"]["complete"])
            self.assertEqual(result["transcript"]["page_count"], 2)
            raw_path = Path(result["raw_evidence_path"])
            self.assertTrue(raw_path.exists())
            self.assertTrue(raw_path.is_relative_to(memory_root))
            raw_text = raw_path.read_text(encoding="utf-8")
            self.assertIn("taskUuid=task-new", raw_text)
            self.assertIn("First page fact.", raw_text)
            self.assertIn("Second page action.", raw_text)

    def test_fetch_reads_paragraph_list_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            fake_dws = self.write_fake_dws(project_root)

            result = self.run_script(
                project_root,
                "--task-uuid",
                "task-paragraph",
                "--dws-command",
                f'"{sys.executable}" "{fake_dws}"',
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["transcript"]["complete"])
            self.assertEqual(result["transcript"]["page_count"], 2)
            self.assertEqual(result["transcript"]["segment_count"], 3)
            raw_path = Path(result["raw_evidence_path"])
            self.assertTrue(raw_path.exists())
            self.assertTrue(raw_path.is_relative_to(memory_root))
            raw_text = raw_path.read_text(encoding="utf-8")
            self.assertIn("FDE-A: First paragraph fact.", raw_text)
            self.assertIn("FDE-A: First paragraph decision.", raw_text)
            self.assertIn("PM-B: Second paragraph action.", raw_text)

    def test_supplied_raw_evidence_is_preserved_under_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            memory_root = self.make_memory(project_root)
            raw = project_root / "notes.txt"
            raw.write_text("raw notes", encoding="utf-8")

            result = self.run_script(project_root, "--raw-evidence", str(raw), "--raw-evidence-label", "notes")

            self.assertTrue(result["ok"])
            preserved = Path(result["raw_evidence_path"])
            self.assertTrue(preserved.exists())
            self.assertTrue(preserved.is_relative_to(memory_root))
            self.assertEqual(preserved.read_text(encoding="utf-8"), "raw notes")


if __name__ == "__main__":
    unittest.main()
