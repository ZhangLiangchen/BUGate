#!/usr/bin/env python3
"""Common Core writer cannot bypass Wave 7 path classification."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from bugate_core import write_text  # noqa: E402
from test_role_governance import Fixture, role_env  # noqa: E402


class GovernedWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-writer-")
        self.root = Path(self.tmp_ctx.name)
        self.fixture = Fixture(self.root)
        self.old_root = os.environ.get("BUGATE_PROJECT_ROOT")
        self.old_profile = os.environ.pop("BUGATE_PROFILE", None)
        os.environ["BUGATE_PROJECT_ROOT"] = str(self.root)

    def tearDown(self):
        if self.old_root is None:
            os.environ.pop("BUGATE_PROJECT_ROOT", None)
        else:
            os.environ["BUGATE_PROJECT_ROOT"] = self.old_root
        if self.old_profile is not None:
            os.environ["BUGATE_PROFILE"] = self.old_profile
        os.environ.pop("BUGATE_AGENT_ROLE", None)
        os.environ.pop("BUGATE_SESSION_ID", None)
        self.tmp_ctx.cleanup()

    def test_canonical_output_is_blocked_before_write_without_designer(self):
        target = self.fixture.artifact / "01_business_brief.md"
        original = target.read_bytes()
        with self.assertRaisesRegex(PermissionError, "BUGATE_AGENT_ROLE is unset"):
            write_text(target, "must not land\n")
        self.assertEqual(original, target.read_bytes())

    def test_designer_can_write_precode_but_not_receipts(self):
        with role_env("designer", "designer-session"):
            target = self.fixture.artifact / "01_business_brief.md"
            write_text(target, "gate_status: passed\nupdated: true\n")
            self.assertIn("updated", target.read_text(encoding="utf-8"))
            evidence = self.fixture.artifact / "00_role_evidence" / "chain.json"
            with self.assertRaisesRegex(PermissionError, "direct edits"):
                write_text(evidence, "{}\n")
            self.assertFalse(evidence.exists())

    def test_non_governance_output_remains_available(self):
        target = self.root / "artifacts" / "diagnostic.txt"
        write_text(target, "ok\n")
        self.assertEqual("ok\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
