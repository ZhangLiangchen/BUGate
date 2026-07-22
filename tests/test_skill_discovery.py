#!/usr/bin/env python3
"""Keep every shipped BUGate skill discoverable on each supported surface."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402


class SkillDiscoveryTests(unittest.TestCase):
    def test_core_and_runtime_discovery_links_cover_the_install_contract(self) -> None:
        surfaces = {
            "skills": "../.shared/skills/{skill}",
            ".agents/skills": "../../.shared/skills/{skill}",
            ".claude/skills": "../../.shared/skills/{skill}",
            ".codex/skills": "../../.shared/skills/{skill}",
        }
        self.assertEqual(
            contract.SKILL_NAMES,
            ("bugate", "bugate-full-check", "bugate-import"),
        )

        for surface, target_template in surfaces.items():
            for skill in contract.SKILL_NAMES:
                with self.subTest(surface=surface, skill=skill):
                    link = ROOT / surface / skill
                    self.assertTrue(link.is_symlink(), f"missing discovery link: {link}")
                    self.assertEqual(
                        os.readlink(link), target_template.format(skill=skill)
                    )
                    self.assertTrue(
                        (link.resolve(strict=True) / "SKILL.md").is_file(),
                        f"discovery target lacks SKILL.md: {link}",
                    )

    def test_import_skill_is_present_in_the_vendored_runtime_roots(self) -> None:
        self.assertIn(".shared/skills/bugate-import", contract.VENDOR_TREE_ROOTS)
        projected = contract.skill_link_entries(".bugate")
        import_links = [
            item for item in projected if item["path"].endswith("/bugate-import")
        ]
        self.assertEqual(len(import_links), len(contract.SKILL_RUNTIMES))
        self.assertEqual(
            {item["path"].split("/", 1)[0] for item in import_links},
            set(contract.SKILL_RUNTIMES),
        )


if __name__ == "__main__":
    unittest.main()
