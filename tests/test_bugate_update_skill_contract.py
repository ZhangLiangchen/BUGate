#!/usr/bin/env python3
"""Contract tests for the agent-facing BUGate update entrypoints.

The tests are SUT-neutral and read only BUGate Core metadata/documentation.
They pin discoverability, the plan/apply authority boundary, and imported
projection ownership without constructing or opening a real imported SUT.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402


SKILL = ROOT / ".shared/skills/bugate-update/SKILL.md"
OPENAI_METADATA = ROOT / ".shared/skills/bugate-update/agents/openai.yaml"
PROMPTS = (
    ROOT / "UPDATE_PROMPT.md",
    ROOT / "UPDATE_PROMPT.zh-CN.md",
)


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(?P<body>.*?)\n---\n", text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"{path} has no exact YAML frontmatter block")
    result: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        key, separator, value = raw_line.partition(":")
        if not separator:
            raise AssertionError(f"{path} has malformed frontmatter: {raw_line!r}")
        result[key.strip()] = value.strip().strip('"')
    return result


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class BugateUpdateSkillContractTests(unittest.TestCase):
    def test_skill_metadata_explicitly_triggers_version_upgrade_requests(self) -> None:
        metadata = _frontmatter(SKILL)
        self.assertEqual(metadata, {
            "name": "bugate-update",
            "description": metadata.get("description", ""),
        })
        description = metadata["description"].lower()
        for marker in (
            "update bugate",
            "upgrade bugate",
            "bugate version",
            "existing imported",
            "升级 bugate 版本",
            "升级bugate版本",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, description)

    def test_skill_is_implicitly_invocable_in_codex_ui_metadata(self) -> None:
        text = OPENAI_METADATA.read_text(encoding="utf-8")
        self.assertIn('display_name: "BUGate Update"', text)
        self.assertRegex(text, r'default_prompt:\s+"[^"]*[$]bugate-update[^"]*"')
        self.assertIn("allow_implicit_invocation: true", text)

    def test_bilingual_prompts_are_linked_and_plan_only_by_default(self) -> None:
        english = PROMPTS[0].read_text(encoding="utf-8")
        chinese = PROMPTS[1].read_text(encoding="utf-8")
        self.assertIn("[简体中文](UPDATE_PROMPT.zh-CN.md)", english)
        self.assertIn("[English](UPDATE_PROMPT.md)", chinese)
        for path in PROMPTS:
            with self.subTest(path=path.name):
                text = _normalized(path)
                self.assertRegex(text, r"PLAN_ONLY|只读规划")
                self.assertRegex(text.lower(), r"explicit approval|明确批准")
                self.assertRegex(text.lower(), r"status.*plan.*apply.*verify")
                self.assertRegex(text.lower(), r"do not.*commit|不得.*commit")
                self.assertRegex(text.lower(), r"do not.*push|不得.*push")
                self.assertRegex(text.lower(), r"bugate_init[.]py")
                self.assertRegex(text.lower(), r"lineage")
                self.assertRegex(text.lower(), r"profile")

    def test_prompts_classify_by_lock_and_launcher_and_forbid_unsafe_shortcuts(self) -> None:
        classifier = re.compile(
            r'test -f "[$]BUGATE_VENDOR_DIR/bugate[.]lock[.]json" [\\] '
            r'&& test -x "[$]BUGATE_VENDOR_DIR/bin/bugate-update"'
        )
        for path in PROMPTS:
            with self.subTest(path=path.name):
                text = _normalized(path)
                self.assertRegex(text, classifier)
                self.assertIn("locked-in-repo-update", text)
                self.assertIn("external-bootstrap-candidate", text)
                self.assertRegex(text.lower(), r"no implicit `?latest`?|不得隐式.*latest")
                self.assertRegex(text.lower(), r"no broad `?--force`?|不得.*--force")
                self.assertRegex(text.lower(), r"plan.*go")

    def test_skill_and_prompts_are_part_of_the_imported_projection(self) -> None:
        self.assertIn("bugate-update", contract.SKILL_NAMES)
        self.assertIn(".shared/skills/bugate-update", contract.VENDOR_TREE_ROOTS)
        self.assertIn("UPDATE_PROMPT.md", contract.VENDOR_SINGLE_FILES)
        self.assertIn("UPDATE_PROMPT.zh-CN.md", contract.VENDOR_SINGLE_FILES)

    def test_codex_plugin_routes_natural_language_upgrade_to_the_skill(self) -> None:
        plugin = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        prompts = plugin.get("interface", {}).get("defaultPrompt", [])
        rendered = "\n".join(str(item) for item in prompts).lower()
        self.assertIn("bugate-update skill", rendered)
        self.assertRegex(rendered, r"upgrade (the )?bugate version|升级 bugate 版本")
        self.assertIn("升级bugate版本", rendered)


if __name__ == "__main__":
    unittest.main()
