#!/usr/bin/env python3
"""Pin the safe operator route after an updater rollback.

This contract is repository-only and SUT-neutral. It reads documentation and
the local CLI parser; it never opens or constructs a real imported SUT.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_update  # noqa: E402


PRIMARY_ROUTE_DOCS = (
    ".codex/README.md",
    "README.md",
    "README.zh-CN.md",
    "INIT.md",
    "INIT.zh-CN.md",
    "CAPABILITIES.md",
    "IMPORT_PROMPT.md",
    "IMPORT_PROMPT.zh-CN.md",
    ".shared/skills/bugate-import/SKILL.md",
    ".shared/skills/bugate-import/references/updating-bugate.md",
    ".shared/skills/bugate-import/references/updating-bugate.zh-CN.md",
    ".shared/skills/bugate-import/references/field-guide.md",
    "docs/USING-BUGATE.md",
    "docs/USING-BUGATE.zh-CN.md",
    "docs/IMPORT-FIELD-GUIDE.md",
    "docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.md",
    "docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.zh-CN.md",
    "docs/releases/v0.4.2.md",
    "docs/releases/v0.4.2.zh-CN.md",
)

EXACT_FALLBACK_DOCS = (
    "README.md",
    "README.zh-CN.md",
    "INIT.md",
    "INIT.zh-CN.md",
    "IMPORT_PROMPT.md",
    "IMPORT_PROMPT.zh-CN.md",
    ".shared/skills/bugate-import/references/updating-bugate.md",
    ".shared/skills/bugate-import/references/updating-bugate.zh-CN.md",
    "docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.md",
    "docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.zh-CN.md",
    "docs/releases/v0.4.2.md",
    "docs/releases/v0.4.2.zh-CN.md",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalized(relative: str) -> str:
    return " ".join(_text(relative).split())


class UpdaterOperatorDocsContractTests(unittest.TestCase):
    def test_external_status_verify_and_rollback_forms_are_real_cli_syntax(self) -> None:
        parser = bugate_update.build_parser()
        verify = parser.parse_args(["verify", ".", "--vendor-dir", ".bugate"])
        self.assertEqual(verify.command, "verify")
        self.assertEqual(verify.target, ".")
        self.assertEqual(verify.vendor_dir, ".bugate")

        status = parser.parse_args(["status", ".", "--vendor-dir", ".bugate"])
        self.assertEqual(status.command, "status")
        rollback = parser.parse_args(
            [
                "rollback",
                ".",
                "--vendor-dir",
                ".bugate",
                "--transaction",
                "0" * 32,
            ]
        )
        self.assertEqual(rollback.command, "rollback")
        self.assertEqual(rollback.transaction, "0" * 32)

        help_result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/bugate_update.py"), "verify", "--help"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--vendor-dir", help_result.stdout)
        self.assertIn("imported SUT repo root", help_result.stdout)

    def test_primary_docs_route_by_lock_and_launcher_not_version_only(self) -> None:
        for relative in PRIMARY_ROUTE_DOCS:
            with self.subTest(document=relative):
                text = _normalized(relative)
                self.assertRegex(
                    text.lower(),
                    r"bugate[.]lock[.]json|installed lock|lock[+]launcher|lock/updater",
                )
                self.assertRegex(
                    text.lower(),
                    r"bin/bugate-update|vendored `bugate-update`|launcher",
                )
                self.assertRegex(text, r"pre-lock v0[.]4(?:[.]0/v0[.]4[.]1|[.]x|[.]0|[.]1)")
                self.assertRegex(text.lower(), r"external|unpacked|解包|外部")
                self.assertRegex(text.lower(), r"retain|keep|保留")

    def test_operator_runbooks_preserve_the_external_verify_fallback(self) -> None:
        fallback = re.compile(
            r'python3 ["`]?[$]BOOTSTRAP["`]? verify [.] --vendor-dir '
            r'(?:[.]bugate|["`]?[$]BUGATE_VENDOR_DIR["`]?)'
        )
        for relative in EXACT_FALLBACK_DOCS:
            with self.subTest(document=relative):
                self.assertRegex(_normalized(relative), fallback)

    def test_no_doc_reintroduces_unconditional_vendored_verify_after_rollback(self) -> None:
        vendored_verify = re.compile(
            r"(?:[.]bugate|[$]BUGATE_VENDOR_DIR|<vendor>)/bin/bugate-update"
            r"[^\n]*verify"
        )
        for relative in PRIMARY_ROUTE_DOCS:
            lines = _text(relative).splitlines()
            for index, line in enumerate(lines):
                if "rollback --transaction" not in line:
                    continue
                following = next(
                    (
                        candidate.strip()
                        for candidate in lines[index + 1 :]
                        if candidate.strip() and not candidate.lstrip().startswith("#")
                    ),
                    "",
                )
                with self.subTest(document=relative, line=index + 1):
                    self.assertNotRegex(following, vendored_verify)

    def test_import_prompt_classifier_requires_both_lock_and_launcher(self) -> None:
        for relative in ("IMPORT_PROMPT.md", "IMPORT_PROMPT.zh-CN.md"):
            with self.subTest(document=relative):
                text = _normalized(relative)
                self.assertRegex(
                    text,
                    r'test -f "[$]BUGATE_VENDOR_DIR/bugate[.]lock[.]json" [\\] '
                    r'&& test -x "[$]BUGATE_VENDOR_DIR/bin/bugate-update"',
                )
                self.assertIn("BUGATE_ROUTE=locked-in-repo-update", text)
                self.assertIn("BUGATE_ROUTE=external-bootstrap-candidate", text)


if __name__ == "__main__":
    unittest.main()
