import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_readiness_report.py"


class RenderReadinessReportTests(unittest.TestCase):
    def write_packet(self, root: Path) -> Path:
        packet = {
            "project_name": "Demo ADP",
            "generated_at": "2026-07-01T00:00:00+08:00",
            "summary": "One line has acceptance gaps.",
            "workstreams": [
                {
                    "id": "l1-checkout",
                    "name": "Checkout Migration",
                    "owner": "FDE-A",
                    "acceptance": {
                        "score": 18,
                        "max_score": 24,
                        "status": "gap",
                        "roadmap_status": "at-risk",
                        "dimensions": [
                            {
                                "dimension": "Evidence completeness",
                                "score": 1,
                                "gap": "Payment proof missing",
                                "owner": "FDE-A",
                                "action": "Link test evidence",
                                "due": "Before review",
                                "severity": "high",
                            },
                        ],
                        "gaps": [
                            {
                                "gap": "Payment proof missing",
                                "dimension": "Evidence completeness",
                                "owner": "FDE-A",
                                "action": "Link test evidence",
                                "due": "Before review",
                                "severity": "high",
                                "escalation": "Project lead if unresolved",
                            },
                        ],
                    },
                    "cutover": {
                        "score": 12,
                        "max_score": 21,
                        "status": "no-go",
                        "roadmap_status": "blocked",
                        "go_no_go": "No-go",
                        "dimensions": [],
                        "gaps": [],
                    },
                    "evidence": [
                        {
                            "criterion": "Checkout payment succeeds",
                            "proof": "TBD",
                            "status": "missing",
                            "gap": "No linked proof",
                        },
                    ],
                    "confirmations": [],
                },
            ],
        }
        path = root / "scorecard.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return path

    def run_script(self, project_root: Path, packet_path: Path, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root), "--input", str(packet_path), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_renders_markdown_and_html_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_path = self.write_packet(root)

            result = self.run_script(root, packet_path, "--mode", "both")

            self.assertTrue(result["ok"])
            paths = [Path(item["path"]) for item in result["reports"]]
            self.assertIn(root / "_bmad-output" / "adp" / "memory" / "views" / "acceptance-readiness.md", paths)
            self.assertIn(root / "_bmad-output" / "adp" / "memory" / "views" / "cutover-readiness.html", paths)
            acceptance = root / "_bmad-output" / "adp" / "memory" / "views" / "acceptance-readiness.md"
            content = acceptance.read_text(encoding="utf-8")
            self.assertIn("Payment proof missing", content)
            self.assertIn("Roadmap Status", content)
            self.assertIn("at-risk", content)

    def test_second_run_reports_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_path = self.write_packet(root)

            self.run_script(root, packet_path, "--mode", "acceptance")
            second = self.run_script(root, packet_path, "--mode", "acceptance")

            statuses = {item["status"] for item in second["reports"]}
            self.assertEqual(statuses, {"unchanged"})

    def test_accepts_utf8_bom_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_path = self.write_packet(root)
            content = packet_path.read_text(encoding="utf-8")
            packet_path.write_text(content, encoding="utf-8-sig")

            result = self.run_script(root, packet_path, "--mode", "acceptance")

            self.assertTrue(result["ok"])

    def test_writes_workstream_readiness_generated_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_path = self.write_packet(root)
            readiness = root / "_bmad-output" / "adp" / "memory" / "workstreams" / "l1-checkout" / "readiness.md"
            readiness.parent.mkdir(parents=True)
            readiness.write_text("# Readiness\n\nManual note stays.\n", encoding="utf-8")

            result = self.run_script(root, packet_path, "--mode", "acceptance", "--write-workstream-readiness")

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["readiness_updates"]), 1)
            content = readiness.read_text(encoding="utf-8")
            self.assertIn("Manual note stays.", content)
            self.assertIn("<!-- ADP readiness generated: start -->", content)
            self.assertIn("Payment proof missing", content)

    def test_rejects_missing_or_invalid_roadmap_status(self) -> None:
        for value in [None, "delayed"]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                packet_path = self.write_packet(root)
                packet = json.loads(packet_path.read_text(encoding="utf-8"))
                if value is None:
                    del packet["workstreams"][0]["acceptance"]["roadmap_status"]
                else:
                    packet["workstreams"][0]["acceptance"]["roadmap_status"] = value
                packet_path.write_text(json.dumps(packet), encoding="utf-8")

                completed = subprocess.run(
                    [sys.executable, str(SCRIPT), str(root), "--input", str(packet_path), "--mode", "acceptance"],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                result = json.loads(completed.stdout)

                self.assertEqual(completed.returncode, 2)
                self.assertFalse(result["ok"])
                self.assertIn("acceptance.roadmap_status", result["error"])

    def test_language_golden_localizes_reports_and_preserves_canonical_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_path = self.write_packet(root)
            report_path = root / "_bmad-output" / "adp" / "memory" / "views" / "acceptance-readiness.md"

            chinese = self.run_script(root, packet_path, "--mode", "acceptance", "--language", "Chinese")
            chinese_text = report_path.read_text(encoding="utf-8")
            self.assertEqual(chinese["language"]["locale"], "zh")
            self.assertIn("# 验收就绪度报告", chinese_text)
            self.assertIn("Payment proof missing", chinese_text)
            self.assertIn("at-risk", chinese_text)

            english = self.run_script(root, packet_path, "--mode", "acceptance", "--language", "English")
            english_text = report_path.read_text(encoding="utf-8")
            self.assertEqual(english["language"]["locale"], "en")
            self.assertIn("# Acceptance Readiness Report", english_text)
            self.assertIn("Payment proof missing", english_text)
            self.assertIn("at-risk", english_text)


if __name__ == "__main__":
    unittest.main()
