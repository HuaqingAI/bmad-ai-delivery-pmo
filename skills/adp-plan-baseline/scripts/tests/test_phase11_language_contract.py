from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


SKILLS = Path(__file__).resolve().parents[3]
CATALOG = SKILLS / "adp-plan-baseline" / "assets" / "locale-catalog.json"
TARGETS = {
    "workstream": SKILLS / "adp-workstream-register" / "scripts" / "register_workstream.py",
    "checkpoint": SKILLS / "adp-bmm-checkpoint-sync" / "scripts" / "sync_bmm_checkpoint.py",
    "meeting": SKILLS / "adp-meeting-sync" / "scripts" / "sync_meeting.py",
    "risk": SKILLS / "adp-risk-dependency-change-review" / "scripts" / "review_risk_dependency_change.py",
    "l0": SKILLS / "adp-l0-reference-sync" / "scripts" / "sync_l0_references.py",
    "readiness": SKILLS / "adp-acceptance-readiness-review" / "scripts" / "render_readiness_report.py",
}
EXEMPTION_REGISTRIES = {
    "meeting": {"MEETING_SYSTEM_LINES", "MEETING_SYSTEM_PREFIXES", "MEETING_CANONICAL_FACT_COPY"},
    "risk": {"RISK_CANONICAL_SOURCE_SECTIONS"},
    "l0": {"L0_SYSTEM_COPY", "L0_SYSTEM_PREFIXES"},
}
SYSTEM_PREFIX = re.compile(r"^(?:#{1,3} |\| [A-Z]|(?:Generated|Created|Source|Date|Status|Owner):)")


def assigned_string_keys(tree: ast.AST, names: set[str]) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target_names = {target.id for target in targets if isinstance(target, ast.Name)}
        if not target_names.intersection(names):
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            values.update(key.value for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))
        elif isinstance(value, ast.Set):
            values.update(item.value for item in value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return values


class Phase11LanguageContractTests(unittest.TestCase):
    def test_catalog_keys_are_symmetric(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(set(catalog["en"]), set(catalog["zh"]))

    def test_all_phase11_entrypoints_use_shared_config_and_language_override(self) -> None:
        for name, path in TARGETS.items():
            with self.subTest(skill=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("DEFAULT_CONFIG_SCRIPT", source)
                self.assertIn('"--language"', source)
                self.assertIn("resolve_effective_config", source)
                self.assertIn("language_metadata", source)

    def test_derived_view_system_copy_is_catalog_backed_or_explicitly_exempted(self) -> None:
        findings: list[str] = []
        for name in ["workstream", "meeting", "risk", "l0", "readiness"]:
            path = TARGETS[name]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            exemptions = assigned_string_keys(tree, EXEMPTION_REGISTRIES.get(name, set()))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                for value in (line.strip() for line in node.value.splitlines()):
                    if not SYSTEM_PREFIX.match(value) or not re.search(r"[A-Za-z]{3}", value):
                        continue
                    if value in exemptions or value.rstrip(":") in exemptions:
                        continue
                    placeholder_tokens = set(value.replace("|", " ").replace("-", " ").split())
                    if placeholder_tokens.issubset({"TBD", "open", "gap"}):
                        continue
                    if "{" in value or "message(" in value:
                        continue
                    findings.append(f"{name}:{node.lineno}:{value[:100]}")
        self.assertEqual(findings, [], "unmapped user-visible English system copy:\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
