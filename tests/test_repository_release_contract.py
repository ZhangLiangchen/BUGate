#!/usr/bin/env python3
"""Pin the repository-level version and release-document contract."""
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+$")


def updater_version() -> str:
    tree = ast.parse(
        (ROOT / "scripts/bugate_update.py").read_text(encoding="utf-8")
    )
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "UPDATER_VERSION"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1:
        raise AssertionError("bugate_update.py must declare one literal UPDATER_VERSION")
    return values[0]


class RepositoryReleaseContractTests(unittest.TestCase):
    def test_plugin_updater_and_ci_versions_are_identical(self) -> None:
        codex = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        claude = json.loads(
            (ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
        )
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        ci_match = re.search(
            r"(?m)^\s*BUGATE_RELEASE_VERSION:\s*[\"']?([^\s\"']+)",
            workflow,
        )
        self.assertIsNotNone(ci_match, "CI must declare BUGATE_RELEASE_VERSION")
        versions = {
            str(codex.get("version")),
            str(claude.get("version")),
            updater_version(),
            ci_match.group(1) if ci_match else "",
        }
        self.assertEqual(len(versions), 1, versions)
        version = versions.pop()
        self.assertRegex(version, VERSION_RE)

    def test_current_release_has_bilingual_notes_and_exact_asset_names(self) -> None:
        version = updater_version()
        expected_assets = {
            f"bugate-{version}.tar.gz",
            f"bugate-{version}.zip",
            f"bugate-{version}.SHA256SUMS",
        }
        for suffix in ("", ".zh-CN"):
            path = ROOT / f"docs/releases/v{version}{suffix}.md"
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                for asset in expected_assets:
                    self.assertIn(asset, text)

    def test_codex_default_prompt_routes_existing_installs_to_the_updater(self) -> None:
        plugin = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        prompts = plugin.get("interface", {}).get("defaultPrompt", [])
        self.assertIsInstance(prompts, list)
        rendered = "\n".join(str(item) for item in prompts).lower()
        self.assertIn("fresh", rendered)
        self.assertRegex(rendered, r"bugate[-_]update|bugate-update")
        self.assertIn("existing", rendered)


if __name__ == "__main__":
    unittest.main()
