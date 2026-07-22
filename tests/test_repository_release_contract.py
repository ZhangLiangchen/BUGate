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

    def test_updater_release_gate_is_provider_neutral(self) -> None:
        version = updater_version()
        documents = (
            ROOT / f"docs/releases/v{version}.md",
            ROOT / f"docs/releases/v{version}.zh-CN.md",
            ROOT / "docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.md",
            ROOT / "docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.zh-CN.md",
        )
        for path in documents:
            with self.subTest(path=path.name):
                text = " ".join(path.read_text(encoding="utf-8").split()).lower()
                self.assertIn("--full-check-mode smoke --full-check-archive both", text)
                self.assertRegex(text, r"same-provider|同源")
                self.assertRegex(text, r"newly spawned|新建")
                self.assertIn("placeholder", text)
                self.assertNotIn("required for the exact release archives", text)
                self.assertNotIn("必须完成的 real codex/claude/memory", text)

        defect = (
            ROOT
            / "docs/defects/BUGATE-CORE-2026-07-22-ROLE-GOVERNANCE-STATE-INTEGRITY.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Status: fixed and closed", defect)
        self.assertIn("archive-native `smoke + both`", defect)
        self.assertIn("378/378 PASS", defect)
        self.assertIn("final merged-main bytes", defect)
        self.assertIn("main and annotated-tag CI pass", defect)
        self.assertIn("downloaded, checksum-verified, and reaccepted", defect)
        self.assertIn("documentation-only candidate", defect)
        self.assertIn("fresh final archive build", defect)

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
