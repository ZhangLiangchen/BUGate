#!/usr/bin/env python3
"""Claude/Codex hook tests for role phase classification and drift relocking."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import role_governance as rg  # noqa: E402
from test_role_governance import Fixture, fake_memory, role_env  # noqa: E402


HOOK = ROOT / "scripts" / "check_role_evidence.py"


def run_hook(root: Path, payload: dict, *, role: str = "", session: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["BUGATE_PROJECT_ROOT"] = str(root)
    env.pop("BUGATE_PROFILE", None)
    if role:
        env["BUGATE_AGENT_ROLE"] = role
    else:
        env.pop("BUGATE_AGENT_ROLE", None)
    if session:
        env["BUGATE_SESSION_ID"] = session
    else:
        env.pop("BUGATE_SESSION_ID", None)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )


def claude(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


def codex(path: str) -> dict:
    patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-old\n+new\n*** End Patch"
    return {"tool_name": "apply_patch", "tool_input": {"input": patch}}


class RoleEvidenceHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-role-hook-")
        self.root = Path(self.tmp_ctx.name)
        self.fx = Fixture(self.root)
        self.old_project = os.environ.get("BUGATE_PROJECT_ROOT")
        self.old_profile = os.environ.pop("BUGATE_PROFILE", None)
        os.environ["BUGATE_PROJECT_ROOT"] = str(self.root)
        self.original_prepare = rg._memory_prepare
        self.original_verify = rg._memory_verify
        self.original_semantics = rg.verify_precode_semantics
        rg._memory_prepare = fake_memory
        rg._memory_verify = lambda ctx, receipt: None
        rg.verify_precode_semantics = lambda ctx: None

    def tearDown(self):
        rg._memory_prepare = self.original_prepare
        rg._memory_verify = self.original_verify
        rg.verify_precode_semantics = self.original_semantics
        if self.old_project is None:
            os.environ.pop("BUGATE_PROJECT_ROOT", None)
        else:
            os.environ["BUGATE_PROJECT_ROOT"] = self.old_project
        if self.old_profile is not None:
            os.environ["BUGATE_PROFILE"] = self.old_profile
        os.environ.pop("BUGATE_AGENT_ROLE", None)
        os.environ.pop("BUGATE_SESSION_ID", None)
        self.tmp_ctx.cleanup()

    def test_precode_unset_wrong_role_and_both_payload_shapes(self):
        target = "usecases/UC-001/01_business_brief.md"
        unset = run_hook(self.root, claude(target))
        self.assertEqual(2, unset.returncode)
        self.assertIn("BUGATE_AGENT_ROLE is unset", unset.stderr)
        wrong = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, wrong.returncode)
        self.assertIn("not allowed in pre_code", wrong.stderr)
        for payload in (claude(target), codex(target)):
            allowed = run_hook(self.root, payload, role="designer", session="d-1")
            self.assertEqual(0, allowed.returncode, allowed.stderr)

    def test_evidence_direct_edit_always_blocked_when_enabled(self):
        target = "usecases/UC-001/00_role_evidence/chain.json"
        for payload in (claude(target), codex(target)):
            result = run_hook(self.root, payload, role="designer", session="d-1")
            self.assertEqual(2, result.returncode)
            self.assertIn("direct edits", result.stderr)
        multi = {
            "tool_name": "apply_patch",
            "tool_input": {
                "input": (
                    "*** Begin Patch\n"
                    "*** Update File: notes.md\n@@\n-old\n+new\n"
                    f"*** Update File: {target}\n@@\n-old\n+new\n"
                    "*** End Patch"
                )
            },
        }
        result = run_hook(self.root, multi, role="designer", session="d-1")
        self.assertEqual(2, result.returncode)
        self.assertIn(target, result.stderr)

    def test_implementation_unlock_and_artifact_profile_drift_relock(self):
        target = "tests/test_UC-001.py"
        locked = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, locked.returncode)
        self.assertIn("acceptance missing", locked.stderr)

        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(self.fx.artifact, phase="pre_code", to_role="implementer")
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["receipt_sha256"],
            )
        allowed = run_hook(self.root, claude(target), role="implementer", session="i-1")
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        stolen = run_hook(self.root, codex(target), role="implementer", session="i-2")
        self.assertEqual(2, stolen.returncode)
        self.assertIn("different BUGATE_SESSION_ID", stolen.stderr)

        brief = self.fx.artifact / "01_business_brief.md"
        old = brief.read_bytes()
        brief.write_bytes(old + b"drift\n")
        drift = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, drift.returncode)
        self.assertIn("artifact drift", drift.stderr)
        brief.write_bytes(old)

        profile = self.root / "bugate.profile.yaml"
        old_profile = profile.read_bytes()
        profile.write_bytes(old_profile + b"# drift\n")
        drift = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, drift.returncode)
        self.assertIn("profile hash/path drifted", drift.stderr)

    def test_postrun_requires_reviewer_acceptance(self):
        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                self.fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            self.fx.implementation.write_text("def test_fixture(): pass\n", encoding="utf-8")
            implementer_handoff = rg.handoff(
                self.fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[self.fx.implementation],
            )
        target = "usecases/UC-001/04_execution_report.md"
        locked = run_hook(self.root, claude(target), role="reviewer", session="r-1")
        self.assertEqual(2, locked.returncode)
        self.assertIn("reviewer acceptance missing", locked.stderr)
        with role_env("reviewer", "r-1"):
            rg.accept(
                self.fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )
        allowed = run_hook(self.root, codex(target), role="reviewer", session="r-1")
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        stolen = run_hook(self.root, claude(target), role="reviewer", session="r-2")
        self.assertEqual(2, stolen.returncode)
        self.assertIn("different BUGATE_SESSION_ID", stolen.stderr)

    def test_advisory_warns_but_evidence_protection_remains_hard(self):
        profile = self.root / "bugate.profile.yaml"
        body = profile.read_text(encoding="utf-8").replace(
            "  mode: required", "  mode: advisory"
        )
        profile.write_text(body, encoding="utf-8")
        ordinary = run_hook(
            self.root,
            claude("usecases/UC-001/01_business_brief.md"),
            role="implementer",
            session="i-1",
        )
        self.assertEqual(0, ordinary.returncode, ordinary.stderr)
        self.assertIn("WARNING", ordinary.stderr)
        evidence = run_hook(
            self.root,
            codex("usecases/UC-001/00_role_evidence/chain.json"),
            role="implementer",
            session="i-1",
        )
        self.assertEqual(2, evidence.returncode)

    def test_malformed_required_fails_closed_and_advisory_warns(self):
        profile = self.root / "bugate.profile.yaml"
        original = profile.read_text(encoding="utf-8")
        malformed = original.replace("  session_id_required: true", "  session_id_required: yes")
        profile.write_text(malformed, encoding="utf-8")
        required = run_hook(
            self.root,
            claude("usecases/UC-001/01_business_brief.md"),
            role="designer",
            session="d-1",
        )
        self.assertEqual(2, required.returncode)
        self.assertIn("must be boolean", required.stderr)
        profile.write_text(malformed.replace("  mode: required", "  mode: advisory"), encoding="utf-8")
        advisory = run_hook(
            self.root,
            claude("usecases/UC-001/01_business_brief.md"),
            role="designer",
            session="d-1",
        )
        self.assertEqual(0, advisory.returncode, advisory.stderr)
        self.assertIn("malformed advisory", advisory.stderr)

    def test_mode_off_is_noop_including_role_evidence_path(self):
        profile = self.root / "bugate.profile.yaml"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace("  mode: required", "  mode: off"),
            encoding="utf-8",
        )
        result = run_hook(
            self.root,
            codex("usecases/UC-001/00_role_evidence/chain.json"),
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
