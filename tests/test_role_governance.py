#!/usr/bin/env python3
"""Deterministic acceptance tests for the local Wave 7 role state machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import role_governance as rg  # noqa: E402


PRECODE = [
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
]


@contextmanager
def role_env(role: str, session: str):
    old = {key: os.environ.get(key) for key in ("BUGATE_AGENT_ROLE", "BUGATE_SESSION_ID")}
    os.environ["BUGATE_AGENT_ROLE"] = role
    os.environ["BUGATE_SESSION_ID"] = session
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def fake_memory(ctx: rg.GovernanceContext, transition: dict):
    memory_id = "memory-" + rg.sha256_bytes(rg.canonical_json(transition))[:24]

    def finalize(**kwargs):
        if kwargs["memory_id"] != memory_id or not kwargs["receipt_sha256"]:
            raise AssertionError("bad fake Memory finalization")
        return {
            "namespace": "project:fixture",
            "memory_id": memory_id,
            "verified_at": "2026-07-20T00:00:00Z",
        }

    return {
        "namespace": "project:fixture",
        "memory_id": memory_id,
        "verified_at": "2026-07-20T00:00:00Z",
        "_finalizer": finalize,
    }


class Fixture:
    def __init__(self, tmp: Path, *, mode: str = "required", memory_mode: str = "best_effort"):
        self.root = tmp
        self.artifact = tmp / "usecases" / "UC-001"
        self.artifact.mkdir(parents=True)
        (tmp / "bugate.config.yaml").write_text("profile: bugate.profile.yaml\n", encoding="utf-8")
        (tmp / "bugate.profile.yaml").write_text(
            "\n".join(
                [
                    "artifact_dir_template: usecases/{uc}",
                    "guarded_path_regex:",
                    '  - "^tests/test_(?P<uc>[^/]+)[.]py$"',
                    "required_precode_artifacts:",
                    *[f"  - {name}" for name in PRECODE],
                    "memory:",
                    "  namespace: project:fixture",
                    "role_governance:",
                    f"  mode: {mode}",
                    f"  memory_mode: {memory_mode}",
                    "  evidence_dir: 00_role_evidence",
                    "  session_id_required: true",
                    "  require_distinct_sessions: true",
                    "  human_acceptance_artifacts:",
                    "    - 03b_adversarial_cases.yaml",
                    "  phases:",
                    "    pre_code:",
                    "      allowed_roles: [designer]",
                    "    implementation:",
                    "      allowed_roles: [implementer]",
                    "      requires_handoff_from: [designer]",
                    "    post_run:",
                    "      allowed_roles: [reviewer]",
                    "      requires_handoff_from: [implementer]",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        for name in PRECODE:
            extra = "dispatch_mode: real_peer_dispatch\n" if name == "03b_adversarial_cases.yaml" else ""
            (self.artifact / name).write_text(
                f"gate_status: passed\n{extra}fixture: {name}\n", encoding="utf-8"
            )
        multi = self.artifact / "00_multiview"
        multi.mkdir()
        (multi / "divergence_report.md").write_text(
            "---\ngate_status: passed\ndispatch_mode: real_peer_dispatch\n---\n# Fixture\n",
            encoding="utf-8",
        )
        (tmp / "tests").mkdir()
        self.implementation = tmp / "tests" / "test_UC-001.py"
        self.outside_implementation = tmp / "tests" / "helper.py"


class RoleGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-role-")
        self.tmp = Path(self.tmp_ctx.name)
        self.old_project = os.environ.get("BUGATE_PROJECT_ROOT")
        self.old_profile = os.environ.pop("BUGATE_PROFILE", None)
        os.environ["BUGATE_PROJECT_ROOT"] = str(self.tmp)
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

    def test_policy_rejects_model_or_non_lifecycle_phase_roles(self):
        for bad in ("codex", "builder", "human"):
            with self.assertRaises(rg.RoleConfigError):
                rg.governance_policy(
                    {
                        "role_governance": {
                            "mode": "required",
                            "phases": {"pre_code": {"allowed_roles": [bad]}},
                        }
                    }
                )

    def test_run_command_generates_session_rejects_role_conflict_and_hides_secrets(self):
        env = os.environ.copy()
        env.pop("BUGATE_AGENT_ROLE", None)
        env.pop("BUGATE_SESSION_ID", None)
        env["MCP_API_KEY"] = "fixture-secret-must-not-print"
        child = (
            "import os; "
            "assert os.environ['BUGATE_AGENT_ROLE']=='designer'; "
            "assert os.environ['BUGATE_SESSION_ID']; "
            "assert os.environ['BUGATE_AGENT_RUNTIME']=='unknown'"
        )
        result = subprocess.run(
            [
                str(ROOT / "bin" / "bugate-role"),
                "run",
                "--role",
                "designer",
                "--",
                sys.executable,
                "-c",
                child,
            ],
            cwd=self.tmp,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("role=designer", result.stderr)
        self.assertNotIn("fixture-secret", result.stderr + result.stdout)
        env["BUGATE_AGENT_ROLE"] = "implementer"
        conflict = subprocess.run(
            [
                str(ROOT / "bin" / "bugate-role"),
                "run",
                "--role",
                "designer",
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            cwd=self.tmp,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, conflict.returncode)
        self.assertIn("refusing to replace", conflict.stderr)

    def test_off_mode_is_noop_with_stale_profile(self):
        (self.tmp / "bugate.config.yaml").write_text(
            "profile: missing.yaml\nrole_governance:\n  mode: off\n", encoding="utf-8"
        )
        result = rg.preflight(self.tmp / "anything", "implementation")
        self.assertTrue(result.allowed)
        self.assertEqual("off", result.mode)

    def test_full_chain_drift_idempotency_and_append_only_hashes(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            human = rg.approve(fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        self.assertEqual("human_acceptance", human["event"])
        self.assertEqual("awaiting_implementer_acceptance", rg.load_chain(rg.load_context(fx.artifact))["state"])

        with role_env("implementer", "designer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "distinct session"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=designer_handoff["receipt_sha256"],
                )
        with role_env("implementer", "implementer-session"):
            accepted = rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            retry = rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            self.assertEqual(accepted["receipt_sha256"], retry["receipt_sha256"])
            self.assertTrue(rg.preflight(fx.artifact, "implementation").allowed)
        with role_env("implementer", "other-implementer-session"):
            rebound = rg.preflight(fx.artifact, "implementation")
            self.assertFalse(rebound.allowed)
            self.assertTrue(any("different BUGATE_SESSION_ID" in e for e in rebound.errors))
        self.assertEqual("implementation_unlocked", rg.load_chain(rg.load_context(fx.artifact))["state"])

        profile = fx.root / "bugate.profile.yaml"
        profile_body = profile.read_bytes()
        profile.write_bytes(profile_body + b"# drift\n")
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "profile.*drift"):
                rg.verify_evidence(fx.artifact, phase="implementation")
        profile.write_bytes(profile_body)

        brief = fx.artifact / "01_business_brief.md"
        brief_body = brief.read_bytes()
        brief.write_bytes(brief_body + b"drift\n")
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "artifact drift"):
                rg.verify_evidence(fx.artifact, phase="implementation")
        brief.write_bytes(brief_body)

        fx.implementation.write_text("def test_fixture():\n    assert True\n", encoding="utf-8")
        fx.outside_implementation.write_text("fixture = True\n", encoding="utf-8")
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "does not match"):
                rg.handoff(
                    fx.artifact,
                    phase="implementation",
                    to_role="reviewer",
                    implementation_files=[fx.outside_implementation],
                )
            implementer_handoff = rg.handoff(
                fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fx.implementation],
            )

        with role_env("reviewer", "reviewer-session"):
            reviewer_acceptance = rg.accept(
                fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )
            self.assertEqual("reviewer_acceptance", reviewer_acceptance["event"])
            self.assertTrue(rg.preflight(fx.artifact, "post_run").allowed)
            implementation_body = fx.implementation.read_bytes()
            fx.implementation.write_bytes(implementation_body + b"# drift\n")
            with self.assertRaisesRegex(rg.RoleGovernanceError, "artifact drift"):
                rg.verify_evidence(fx.artifact, phase="post_run")
            fx.implementation.write_bytes(implementation_body)
            for name in sorted(rg.POSTRUN_NAMES):
                (fx.artifact / name).write_text(
                    "gate_status: passed\nfixture: postrun\n", encoding="utf-8"
                )
            log = fx.artifact / "execution.log"
            log.write_text("fixture run\n", encoding="utf-8")
            failed = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=1,
                evidence_files=[log],
                final_gate_status="failed",
            )
            self.assertEqual("post_run_active", failed["resulting_state"])
            rg.verify_chain(rg.load_context(fx.artifact))
            closed = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[log],
                final_gate_status="passed",
            )
            self.assertEqual("closed", closed["resulting_state"])

        with role_env("reviewer", "other-reviewer-session"):
            rebound = rg.preflight(fx.artifact, "post_run")
            self.assertFalse(rebound.allowed)
            self.assertTrue(any("different BUGATE_SESSION_ID" in e for e in rebound.errors))

        ctx = rg.load_context(fx.artifact)
        receipts = rg.verify_chain(ctx)
        receipt_paths = sorted((ctx.evidence_dir / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), len(receipt_paths))
        for item, path in zip(receipts, receipt_paths):
            self.assertIn(item["receipt_sha256"], path.name)
            paths = [artifact["path"] for artifact in item.get("artifacts", [])]
            self.assertEqual(sorted(paths), paths)
            self.assertTrue(all(not value.startswith("/") for value in paths))

        tampered = receipt_paths[1]
        original = tampered.read_text(encoding="utf-8")
        payload = json.loads(original)
        payload["uc"] = "tampered"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(rg.RoleGovernanceError, "hash mismatch"):
            rg.verify_chain(ctx)
        tampered.write_text(original, encoding="utf-8")
        rg.verify_chain(ctx)

        chain_path = ctx.evidence_dir / "chain.json"
        chain_original = chain_path.read_text(encoding="utf-8")
        for label, mutate, error in (
            (
                "head",
                lambda value: value.__setitem__("head_sha256", "0" * 64),
                "chain head hash",
            ),
            (
                "state",
                lambda value: value.__setitem__("state", "implementation_unlocked"),
                "chain state",
            ),
            (
                "latest",
                lambda value: value["latest_receipts"].__setitem__(
                    "reviewer_completion", "missing/receipt.json"
                ),
                "latest_receipts",
            ),
        ):
            with self.subTest(chain_tamper=label):
                chain_value = json.loads(chain_original)
                mutate(chain_value)
                chain_path.write_text(json.dumps(chain_value), encoding="utf-8")
                with self.assertRaisesRegex(rg.RoleGovernanceError, error):
                    rg.verify_chain(ctx)
                chain_path.write_text(chain_original, encoding="utf-8")
        rg.verify_chain(ctx)

    def test_required_memory_failure_publishes_no_handoff(self):
        fx = Fixture(self.tmp, memory_mode="required")
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            before = rg.load_chain(rg.load_context(fx.artifact))["sequence"]
            rg._memory_prepare = lambda ctx, transition: (_ for _ in ()).throw(
                rg.RoleGovernanceError("injected strict Memory failure")
            )
            with self.assertRaisesRegex(rg.RoleGovernanceError, "strict Memory"):
                rg.handoff(fx.artifact, phase="pre_code", to_role="implementer")
        chain = rg.load_chain(rg.load_context(fx.artifact))
        self.assertEqual(before, chain["sequence"])
        self.assertNotIn("designer_handoff", chain["latest_receipts"])

    def test_required_memory_finalize_and_acceptance_failures_publish_nothing(self):
        fx = Fixture(self.tmp, memory_mode="required")
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            before_handoff = rg.load_chain(rg.load_context(fx.artifact))["sequence"]

            def prepare_with_failed_finalize(ctx, transition):
                prepared = fake_memory(ctx, transition)

                def fail_finalize(**kwargs):
                    raise RuntimeError("injected finalize failure")

                prepared["_finalizer"] = fail_finalize
                return prepared

            rg._memory_prepare = prepare_with_failed_finalize
            with self.assertRaisesRegex(rg.RoleGovernanceError, "receipt binding"):
                rg.handoff(fx.artifact, phase="pre_code", to_role="implementer")
            self.assertEqual(
                before_handoff,
                rg.load_chain(rg.load_context(fx.artifact))["sequence"],
            )

            rg._memory_prepare = fake_memory
            handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )

        before_accept = rg.load_chain(rg.load_context(fx.artifact))["sequence"]

        def reject_acceptance(ctx, transition):
            if transition.get("event") == "implementer_acceptance":
                raise rg.RoleGovernanceError("injected acceptance Memory failure")
            return fake_memory(ctx, transition)

        rg._memory_prepare = reject_acceptance
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "acceptance Memory failure"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=handoff["memory"]["memory_id"],
                )
        chain = rg.load_chain(rg.load_context(fx.artifact))
        self.assertEqual(before_accept, chain["sequence"])
        self.assertNotIn("implementer_acceptance", chain["latest_receipts"])

    def test_designer_handoff_requires_prior_human_acceptance(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "human acceptance"):
                rg.handoff(fx.artifact, phase="pre_code", to_role="implementer")
        self.assertEqual(0, rg.load_chain(rg.load_context(fx.artifact))["sequence"])

    def test_same_lifecycle_role_cannot_accept_its_own_handoff(self):
        fx = Fixture(self.tmp)
        profile = fx.root / "bugate.profile.yaml"
        profile.write_text(
            profile.read_text(encoding="utf-8")
            .replace("allowed_roles: [implementer]", "allowed_roles: [designer]")
            .replace("requires_handoff_from: [implementer]", "requires_handoff_from: [designer]"),
            encoding="utf-8",
        )
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="designer"
            )
        with role_env("designer", "different-designer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "cannot accept its own"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=handoff["receipt_sha256"],
                )

    def test_required_memory_accept_uses_exact_memory_id(self):
        fx = Fixture(self.tmp, memory_mode="required")
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(fx.artifact, phase="pre_code", to_role="implementer")
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "exact Memory ID"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=handoff["receipt_sha256"],
                )
            accepted = rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=handoff["memory"]["memory_id"],
            )
        self.assertEqual("implementer_acceptance", accepted["event"])

    def test_designer_handoff_rechecks_semantics_without_writing(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            rg.verify_precode_semantics = self.original_semantics
            with self.assertRaisesRegex(rg.RoleGovernanceError, "semantic verification failed"):
                rg.handoff(fx.artifact, phase="pre_code", to_role="implementer")
        self.assertEqual(1, rg.load_chain(rg.load_context(fx.artifact))["sequence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
