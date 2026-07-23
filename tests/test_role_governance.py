#!/usr/bin/env python3
"""Deterministic acceptance tests for the local Wave 7 role state machine."""

from __future__ import annotations

import errno
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

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


def transition_lock_probe(
    root: str,
    artifact: str,
    temp_root: str,
    attempting,
    entered,
    release,
    results,
):
    """Independent-interpreter probe for the filesystem-object UC lock."""

    os.environ["BUGATE_PROJECT_ROOT"] = root
    os.environ.pop("BUGATE_PROFILE", None)
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = temp_root
    try:
        ctx = rg.load_context(Path(artifact))
        attempting.set()
        with rg._transition_lock(ctx):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("transition lock probe release timed out")
        results.put(("ok", rg._transition_lock_key(ctx)))
    except Exception as exc:  # pragma: no cover - rendered by parent assertion
        results.put(("error", repr(exc)))


def kernel_transition_lock_is_contended(artifact: Path) -> bool:
    """Use nonblocking flock to prove another process holds the UC inode."""

    if rg.fcntl is None:
        raise AssertionError("POSIX fcntl is required by the transition lock contract")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(artifact.resolve(), flags)
    acquired = False
    try:
        try:
            rg.fcntl.flock(fd, rg.fcntl.LOCK_EX | rg.fcntl.LOCK_NB)
            acquired = True
            return False
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                return True
            raise
    finally:
        if acquired:
            rg.fcntl.flock(fd, rg.fcntl.LOCK_UN)
        os.close(fd)


class Fixture:
    def __init__(
        self,
        tmp: Path,
        *,
        mode: str = "required",
        memory_mode: str = "best_effort",
        initialize_lineage: bool = True,
    ):
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
        if mode != "off" and initialize_lineage:
            identity = rg.lineage_identity(self.artifact)
            rg.lineage_init(self.artifact, lineage_id=identity["lineage_id"])


class RoleGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-role-")
        self.tmp = Path(self.tmp_ctx.name)
        self.old_project = os.environ.get("BUGATE_PROJECT_ROOT")
        self.old_profile = os.environ.pop("BUGATE_PROFILE", None)
        self.old_memory_homes = {
            key: os.environ.get(key)
            for key in ("MCP_MEMORY_BASE_DIR", "BUGATE_MEMORY_HOME")
        }
        memory_home = self.tmp / "memory-home"
        memory_home.mkdir(mode=0o700)
        os.environ["MCP_MEMORY_BASE_DIR"] = str(memory_home)
        os.environ["BUGATE_MEMORY_HOME"] = str(memory_home)
        os.environ["BUGATE_PROJECT_ROOT"] = str(self.tmp)
        self.original_prepare = rg._memory_prepare
        self.original_verify = rg._memory_verify
        self.original_lineage_root = rg._memory_ensure_lineage_root
        self.original_lineage_probe = rg._memory_probe_lineage_root
        self.original_checkpoint_create = rg._memory_create_checkpoint
        self.original_checkpoint_get = rg._memory_get_checkpoint
        self.original_semantics = rg.verify_precode_semantics
        self.fake_checkpoints: dict[str, dict] = {}
        self.fake_lineage_roots: dict[str, dict] = {}

        def fake_lineage_root(ctx, key):
            payload = {
                "schema": "bugate.role-lineage-root/v1",
                "lineage_key": key.as_dict(),
                "lineage_id": key.lineage_id,
            }
            exact_id = rg.sha256_bytes(rg.canonical_json(payload))
            result = {
                "namespace": key.namespace,
                "lineage_id": key.lineage_id,
                "lineage_root_id": exact_id,
                "memory_id": exact_id,
                "payload": payload,
                "status": "verified",
            }
            self.fake_lineage_roots[key.lineage_id] = result
            return result

        def fake_lineage_probe(ctx, key):
            del ctx
            value = self.fake_lineage_roots.get(key.lineage_id)
            return json.loads(json.dumps(value)) if value is not None else None

        def fake_checkpoint(ctx, payload):
            exact_id = rg.sha256_bytes(rg.canonical_json(payload))
            result = {
                "namespace": payload["lineage_key"]["namespace"],
                "lineage_id": payload["lineage_id"],
                "lineage_root_id": payload["lineage_root_id"],
                "checkpoint_id": exact_id,
                "memory_id": exact_id,
                "payload": json.loads(json.dumps(payload)),
                "status": "verified",
            }
            self.fake_checkpoints[exact_id] = result
            return result

        rg._memory_prepare = fake_memory
        rg._memory_verify = lambda ctx, receipt: None
        rg._memory_ensure_lineage_root = fake_lineage_root
        rg._memory_probe_lineage_root = fake_lineage_probe
        rg._memory_create_checkpoint = fake_checkpoint
        rg._memory_get_checkpoint = lambda ctx, checkpoint_id: json.loads(
            json.dumps(self.fake_checkpoints[checkpoint_id])
        )
        rg.verify_precode_semantics = lambda ctx: None

    def tearDown(self):
        rg._memory_prepare = self.original_prepare
        rg._memory_verify = self.original_verify
        rg._memory_ensure_lineage_root = self.original_lineage_root
        rg._memory_probe_lineage_root = self.original_lineage_probe
        rg._memory_create_checkpoint = self.original_checkpoint_create
        rg._memory_get_checkpoint = self.original_checkpoint_get
        rg.verify_precode_semantics = self.original_semantics
        if self.old_project is None:
            os.environ.pop("BUGATE_PROJECT_ROOT", None)
        else:
            os.environ["BUGATE_PROJECT_ROOT"] = self.old_project
        if self.old_profile is not None:
            os.environ["BUGATE_PROFILE"] = self.old_profile
        for key, value in self.old_memory_homes.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.pop("BUGATE_AGENT_ROLE", None)
        os.environ.pop("BUGATE_SESSION_ID", None)
        self.tmp_ctx.cleanup()

    def test_canonical_cache_refreshes_after_ancestor_case_only_rename(self):
        fx = Fixture(self.tmp)
        rg._canonical_existing_path_cached.cache_clear()
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
        before = rg._canonical_existing_path(fx.artifact)
        self.assertEqual("usecases", before.parent.name)

        renamed_parent = fx.root / "UseCases"
        os.rename(fx.artifact.parent, renamed_parent)
        old_case_alias = fx.root / "usecases" / "UC-001"
        actual_leaf = renamed_parent / "UC-001"
        if not old_case_alias.exists() or not os.path.samefile(
            old_case_alias, actual_leaf
        ):
            self.skipTest("filesystem does not preserve a case-only path alias")

        after = rg._canonical_existing_path(old_case_alias)
        self.assertEqual("UseCases", after.parent.name)
        status = rg.status_data(old_case_alias)
        self.assertFalse(status["ok"])
        self.assertIn("artifact_dir does not match active context", status["error"])

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

    def test_policy_rejects_noncanonical_phase_ownership(self):
        with self.assertRaisesRegex(
            rg.RoleConfigError,
            "canonical lifecycle ownership",
        ):
            rg.governance_policy(
                {
                    "role_governance": {
                        "mode": "required",
                        "phases": {
                            "pre_code": {"allowed_roles": ["designer"]},
                            "implementation": {
                                "allowed_roles": ["reviewer"],
                                "requires_handoff_from": ["designer"],
                            },
                            "post_run": {
                                "allowed_roles": ["implementer"],
                                "requires_handoff_from": ["reviewer"],
                            },
                        },
                    }
                }
            )

    def test_concurrent_transition_publication_is_serialized_per_uc(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("deterministic cross-process transition probe requires fork")
        fx = Fixture(self.tmp)
        mp = multiprocessing.get_context("fork")
        first_prepare = mp.Event()
        release_first = mp.Event()
        lock_attempts = {label: mp.Event() for label in ("first", "second")}
        lock_entries = {label: mp.Event() for label in ("first", "second")}
        prepare_calls = mp.Value("i", 0)
        results = mp.Queue()

        def slow_prepare(ctx, transition):
            with prepare_calls.get_lock():
                prepare_calls.value += 1
                call_number = prepare_calls.value
            if call_number == 1:
                first_prepare.set()
                if not release_first.wait(10):
                    raise RuntimeError("concurrency fixture release timed out")
            return fake_memory(ctx, transition)

        original_transition_lock = rg._transition_lock

        @contextmanager
        def observed_transition_lock(ctx):
            label = os.environ["BUGATE_TEST_LOCK_LABEL"]
            lock_attempts[label].set()
            with original_transition_lock(ctx):
                lock_entries[label].set()
                yield

        def publish_approval(label, artifact, temp_root):
            try:
                os.environ["BUGATE_TEST_LOCK_LABEL"] = label
                for key in ("TMPDIR", "TEMP", "TMP"):
                    os.environ[key] = str(temp_root)
                receipt = rg.approve(artifact, approved_by="qa-owner")
                results.put(("ok", receipt["receipt_sha256"]))
            except Exception as exc:  # pragma: no cover - rendered by assertion
                results.put(("error", repr(exc)))

        temp_one = self.tmp / "temp-one"
        temp_two = self.tmp / "temp-two"
        temp_one.mkdir()
        temp_two.mkdir()
        # Use a resolving alias for the end-to-end publication probe so both
        # callers retain the same canonical UC string.  The independent lock
        # probe below separately exercises case-only spellings on APFS.
        alias = fx.root / "artifact-lock-alias"
        alias.symlink_to(fx.artifact, target_is_directory=True)
        rg._memory_prepare = slow_prepare
        rg._transition_lock = observed_transition_lock
        self.addCleanup(setattr, rg, "_transition_lock", original_transition_lock)
        with role_env("designer", "designer-session"):
            first = mp.Process(
                target=publish_approval,
                args=("first", fx.artifact, temp_one),
            )
            second = mp.Process(
                target=publish_approval,
                args=("second", alias, temp_two),
            )

            def cleanup_publishers():
                release_first.set()
                for process in (first, second):
                    if process.pid is None:
                        continue
                    process.join(timeout=2)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=2)

            self.addCleanup(cleanup_publishers)
            first.start()
            self.assertTrue(lock_attempts["first"].wait(5))
            self.assertTrue(lock_entries["first"].wait(5))
            self.assertTrue(first_prepare.wait(5), "first publisher never reached Memory")
            self.assertTrue(
                kernel_transition_lock_is_contended(alias),
                "the first publisher did not hold the kernel UC lock",
            )
            second.start()
            self.assertTrue(
                lock_attempts["second"].wait(5),
                "second publisher never attempted the UC lock",
            )
            self.assertFalse(
                lock_entries["second"].wait(0.5),
                "second publisher entered while the first held the UC lock",
            )
            release_first.set()
            self.assertTrue(
                lock_entries["second"].wait(5),
                "second publisher did not enter after the first released the UC lock",
            )
            first.join(timeout=10)
            second.join(timeout=10)
        rg._transition_lock = original_transition_lock
        self.assertEqual(0, first.exitcode)
        self.assertEqual(0, second.exitcode)
        observed = [results.get(timeout=2), results.get(timeout=2)]
        self.assertTrue(all(item[0] == "ok" for item in observed), observed)
        self.assertEqual(observed[0][1], observed[1][1])
        self.assertEqual(1, prepare_calls.value)
        ctx = rg.load_context(fx.artifact)
        self.assertEqual(1, rg.load_chain(ctx)["sequence"])
        self.assertEqual(1, len(rg.verify_chain(ctx)))

    def test_transition_lock_is_shared_by_aliases_and_temp_environments(self):
        fx = Fixture(self.tmp)
        mp = multiprocessing.get_context("spawn")
        temp_one = self.tmp / "spawn-temp-one"
        temp_two = self.tmp / "spawn-temp-two"
        temp_one.mkdir()
        temp_two.mkdir()
        alias = fx.artifact.with_name(fx.artifact.name.swapcase())
        if not (alias.exists() and os.path.samefile(alias, fx.artifact)):
            alias = fx.root / "spawn-artifact-lock-alias"
            alias.symlink_to(fx.artifact, target_is_directory=True)

        first_attempt = mp.Event()
        first_entered = mp.Event()
        first_release = mp.Event()
        second_attempt = mp.Event()
        second_entered = mp.Event()
        second_release = mp.Event()
        results = mp.Queue()
        first = mp.Process(
            target=transition_lock_probe,
            args=(
                str(fx.root),
                str(fx.artifact),
                str(temp_one),
                first_attempt,
                first_entered,
                first_release,
                results,
            ),
        )
        second = mp.Process(
            target=transition_lock_probe,
            args=(
                str(fx.root),
                str(alias),
                str(temp_two),
                second_attempt,
                second_entered,
                second_release,
                results,
            ),
        )

        def cleanup_probes():
            first_release.set()
            second_release.set()
            for process in (first, second):
                if process.pid is None:
                    continue
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)

        self.addCleanup(cleanup_probes)
        first.start()
        self.assertTrue(first_attempt.wait(5))
        self.assertTrue(first_entered.wait(5))
        self.assertTrue(
            kernel_transition_lock_is_contended(alias),
            "independent interpreter did not hold the kernel UC lock",
        )
        second.start()
        self.assertTrue(second_attempt.wait(5))
        self.assertFalse(
            second_entered.wait(0.5),
            "independent interpreter bypassed the filesystem-object UC lock",
        )
        first_release.set()
        self.assertTrue(second_entered.wait(5))
        second_release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        self.assertEqual(0, first.exitcode)
        self.assertEqual(0, second.exitcode)
        observed = [results.get(timeout=2), results.get(timeout=2)]
        self.assertTrue(all(item[0] == "ok" for item in observed), observed)
        self.assertEqual(observed[0][1], observed[1][1])

    def test_transition_lock_closes_fd_when_identity_validation_fails(self):
        fx = Fixture(self.tmp)
        ctx = rg.load_context(fx.artifact)
        real_fstat = rg.os.fstat
        opened: list[int] = []

        def mismatched_fstat(fd: int):
            actual = real_fstat(fd)
            opened.append(fd)
            fake = mock.Mock()
            fake.st_mode = actual.st_mode
            fake.st_dev = actual.st_dev
            fake.st_ino = actual.st_ino + 1
            return fake

        with mock.patch.object(rg.os, "fstat", side_effect=mismatched_fstat):
            with self.assertRaisesRegex(
                rg.RoleGovernanceError,
                "lock path changed during acquisition",
            ):
                with rg._transition_lock(ctx):
                    self.fail("identity-mismatched lock unexpectedly entered")
        self.assertEqual(1, len(opened))
        with self.assertRaises(OSError) as caught:
            real_fstat(opened[0])
        self.assertEqual(errno.EBADF, caught.exception.errno)

    def test_context_identity_is_canonical_across_path_alias_spelling(self):
        fx = Fixture(self.tmp)
        alias = fx.artifact.with_name(fx.artifact.name.swapcase())
        if not (alias.exists() and os.path.samefile(alias, fx.artifact)):
            alias = fx.root / "context-artifact-alias"
            alias.symlink_to(fx.artifact, target_is_directory=True)
        with role_env("designer", "designer-session"):
            receipt = rg.approve(alias, approved_by="qa-owner")
        canonical_ctx = rg.load_context(fx.artifact)
        alias_ctx = rg.load_context(alias)
        self.assertEqual("UC-001", canonical_ctx.uc)
        self.assertEqual(canonical_ctx.uc, alias_ctx.uc)
        self.assertEqual("usecases/UC-001", receipt["artifact_dir"])
        self.assertEqual(canonical_ctx.artifact_dir, alias_ctx.artifact_dir)
        self.assertEqual(1, len(rg.verify_chain(canonical_ctx)))
        self.assertTrue(rg.status_data(fx.artifact)["ok"])
        self.assertTrue(rg.status_data(alias)["ok"])

    def test_copied_receipt_chain_cannot_unlock_another_uc(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "implementer-session"):
            rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            fx.implementation.write_text(
                "def test_fixture(): pass\n", encoding="utf-8"
            )
            implementer_handoff = rg.handoff(
                fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fx.implementation],
            )
        with role_env("reviewer", "reviewer-session"):
            rg.accept(
                fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )

        copied_artifact = fx.root / "usecases" / "UC-002"
        copied_artifact.mkdir()
        source_evidence = fx.artifact / "00_role_evidence"
        copied_evidence = copied_artifact / "00_role_evidence"
        shutil.copytree(source_evidence, copied_evidence)
        copied_chain_path = copied_evidence / "chain.json"
        copied_chain = json.loads(copied_chain_path.read_text(encoding="utf-8"))
        copied_chain["latest_receipts"] = {
            event: path.replace("usecases/UC-001/", "usecases/UC-002/")
            for event, path in copied_chain["latest_receipts"].items()
        }
        copied_chain_path.write_text(
            json.dumps(copied_chain, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )

        status = rg.status_data(copied_artifact)
        self.assertFalse(status["ok"])
        self.assertIn("receipt UC does not match", status["error"])
        with role_env("implementer", "implementer-session"):
            implementation = rg.preflight(copied_artifact, "implementation")
        self.assertFalse(implementation.allowed)
        self.assertTrue(
            any("receipt UC does not match" in item for item in implementation.errors)
        )
        with role_env("reviewer", "reviewer-session"):
            postrun = rg.preflight(copied_artifact, "post_run")
        self.assertFalse(postrun.allowed)
        self.assertTrue(any("receipt UC does not match" in item for item in postrun.errors))

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

    def test_off_mode_rejects_lifecycle_completion_publication(self):
        fx = Fixture(self.tmp, mode="off")
        for name in sorted(rg.POSTRUN_NAMES):
            (fx.artifact / name).write_text(
                "gate_status: passed\nfixture: postrun\n", encoding="utf-8"
            )
        log = fx.artifact / "execution.log"
        log.write_text("fixture run\n", encoding="utf-8")
        with role_env("reviewer", "reviewer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "governance is off"):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture runner",
                    exit_code=0,
                    evidence_files=[log],
                    final_gate_status="passed",
                )
        self.assertEqual(
            0,
            rg.load_chain(
                rg.load_context(fx.artifact), allow_uninitialized=True
            )["sequence"],
        )
        self.assertFalse((fx.artifact / "00_role_evidence").exists())

    def test_accept_rejects_when_effective_base_mode_turns_off(self):
        fx = Fixture(self.tmp)
        profile = self.tmp / "bugate.profile.yaml"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace("  mode: required\n", ""),
            encoding="utf-8",
        )
        base = self.tmp / "bugate.config.yaml"
        base.write_text(
            "profile: bugate.profile.yaml\nrole_governance:\n  mode: required\n",
            encoding="utf-8",
        )
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        before = rg.load_chain(rg.load_context(fx.artifact))["sequence"]
        profile_bytes = profile.read_bytes()
        base.write_text(
            "profile: bugate.profile.yaml\nrole_governance:\n  mode: off\n",
            encoding="utf-8",
        )
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "governance is off"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=handoff["receipt_sha256"],
                )
        self.assertEqual(profile_bytes, profile.read_bytes())
        self.assertEqual(before, rg.load_chain(rg.load_context(fx.artifact))["sequence"])

    def test_effective_base_policy_drift_relocks_strict_handoff(self):
        fx = Fixture(self.tmp, memory_mode="required")
        profile = self.tmp / "bugate.profile.yaml"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "  memory_mode: required\n", ""
            ),
            encoding="utf-8",
        )
        base = self.tmp / "bugate.config.yaml"
        base.write_text(
            "profile: bugate.profile.yaml\nrole_governance:\n  memory_mode: required\n",
            encoding="utf-8",
        )
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        before = rg.load_chain(rg.load_context(fx.artifact))["sequence"]
        profile_bytes = profile.read_bytes()
        base.write_text(
            "profile: bugate.profile.yaml\nrole_governance:\n  memory_mode: best_effort\n",
            encoding="utf-8",
        )
        with role_env("implementer", "implementer-session"):
            with self.assertRaisesRegex(
                rg.RoleGovernanceError, "effective config|memory_mode"
            ):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=handoff["receipt_sha256"],
                )
        self.assertEqual(profile_bytes, profile.read_bytes())
        self.assertEqual(before, rg.load_chain(rg.load_context(fx.artifact))["sequence"])

    def test_legacy_profile_snapshot_shape_parses_relocks_and_appends_recovery(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            receipt = rg.approve(fx.artifact, approved_by="qa-owner")
        ctx = rg.load_context(fx.artifact)
        original_path = next((ctx.evidence_dir / "receipts").glob("*.json"))
        legacy = json.loads(json.dumps(receipt))
        # v0.4.0-v0.4.2 receipts predate the v0.4.3 lineage precondition.
        # Adoption must keep those exact legacy bytes readable rather than
        # requiring or synthesizing the new field.
        legacy.pop("lineage", None)
        legacy["profile"].pop("effective_config_sha256")
        idempotency_base = {
            key: legacy[key]
            for key in (
                "event",
                "phase",
                "from_role",
                "to_role",
                "actor",
                "profile",
                "artifacts",
                "dispatch",
                "human_acceptance",
                "approved_by",
                "decision",
            )
        }
        legacy["idempotency_sha256"] = rg._idempotency_payload(idempotency_base)
        legacy["transition_sha256"] = rg.sha256_bytes(
            rg.canonical_json(rg._transition_from_receipt(legacy))
        )
        legacy["memory"]["memory_id"] = "memory-" + rg.sha256_bytes(
            rg.canonical_json(
                {
                    **rg._transition_from_receipt(legacy),
                    "transition_sha256": legacy["transition_sha256"],
                }
            )
        )[:24]
        legacy["receipt_sha256"] = rg.receipt_sha256(legacy)
        rg._validate_receipt_contract(legacy, Path("legacy-receipt.json"))
        legacy_path = original_path.with_name(
            f"000001-human-acceptance-{legacy['receipt_sha256']}.json"
        )
        original_path.unlink()
        legacy_path.write_text(
            json.dumps(legacy, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        chain_path = ctx.evidence_dir / "chain.json"
        chain = json.loads(chain_path.read_text(encoding="utf-8"))
        chain["head_sha256"] = legacy["receipt_sha256"]
        chain["latest_receipts"]["human_acceptance"] = rg.workspace_rel(
            legacy_path, ctx.root
        )
        chain_path.write_text(
            json.dumps(chain, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(1, len(rg.verify_chain(ctx)))
        with self.assertRaisesRegex(rg.RoleGovernanceError, "effective config"):
            rg._verify_snapshot(ctx, legacy)
        legacy_bytes = legacy_path.read_bytes()
        registry_path = rg.lineage_registry.registry_path()
        for candidate in (
            registry_path,
            Path(str(registry_path) + "-wal"),
            Path(str(registry_path) + "-shm"),
        ):
            candidate.unlink(missing_ok=True)
        status = rg.status_data(fx.artifact)
        self.assertEqual("migration_required", status["integrity_state"])
        identity = rg.lineage_identity(fx.artifact)
        adopted = rg.lineage_adopt(
            fx.artifact,
            lineage_id=identity["lineage_id"],
            expected_head=legacy["receipt_sha256"],
        )
        self.assertEqual(0, adopted["receipts_rewritten"])
        self.assertEqual(legacy_bytes, legacy_path.read_bytes())
        with role_env("designer", "designer-session"):
            replacement = rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        recovered = rg.load_chain(rg.load_context(fx.artifact))
        self.assertEqual(3, recovered["sequence"])
        self.assertEqual("awaiting_implementer_acceptance", recovered["state"])
        self.assertNotEqual(legacy["receipt_sha256"], replacement["receipt_sha256"])
        self.assertEqual(handoff["receipt_sha256"], recovered["head_sha256"])

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
            other_artifact = fx.root / "usecases" / "UC-002"
            other_artifact.mkdir()
            for name in PRECODE:
                (other_artifact / name).write_text(
                    f"gate_status: passed\nfixture: other {name}\n",
                    encoding="utf-8",
                )
            for name in sorted(rg.POSTRUN_NAMES):
                (other_artifact / name).write_text(
                    "gate_status: passed\nfixture: other postrun\n",
                    encoding="utf-8",
                )
            other_implementation = fx.root / "tests" / "test_UC-002.py"
            other_implementation.write_text("fixture = 'other'\n", encoding="utf-8")
            other_aliases = fx.root / "other-uc-phase-aliases"
            other_aliases.mkdir()
            other_precode_alias = other_aliases / "brief.md"
            other_postrun_alias = other_aliases / "report.md"
            other_implementation_alias = other_aliases / "implementation.py"
            os.link(other_artifact / "01_business_brief.md", other_precode_alias)
            os.link(
                other_artifact / "04_execution_report.md",
                other_postrun_alias,
            )
            os.link(other_implementation, other_implementation_alias)
            implementation_candidates = [
                other_implementation,
                other_implementation_alias,
            ]
            tests_dir = fx.root / "tests"
            actual_case_tests_dir = fx.root / "Tests"
            os.rename(tests_dir, actual_case_tests_dir)
            actual_case_implementation = (
                actual_case_tests_dir / other_implementation.name
            )
            if tests_dir.exists() and os.path.samefile(
                tests_dir, actual_case_tests_dir
            ):
                implementation_candidates.insert(0, actual_case_implementation)
            else:
                os.rename(actual_case_tests_dir, tests_dir)
            other_role_evidence = (
                other_artifact / "00_role_evidence" / "chain.json"
            )
            other_receipts = other_role_evidence.parent / "receipts"
            other_receipts.mkdir(parents=True)
            other_role_evidence.write_text("{}\n", encoding="utf-8")
            malformed_owner_receipt = other_receipts / "malformed-owner.json"
            unrelated_owner_payload = {
                "artifacts": [
                    {
                        "path": "unrelated/path",
                        "sha256": "0" * 64,
                        "gate_status": "passed",
                        "metadata": ["passed"],
                    }
                ]
            }
            malformed_owner_receipt.write_text(
                json.dumps(unrelated_owner_payload)
                + "\n",
                encoding="utf-8",
            )
            (other_receipts / "broken-unrelated.json").write_text(
                '{"run":{"command_summary":"passed"},\n',
                encoding="utf-8",
            )
            ordinary_named_like_metadata = fx.root / "passed"
            ordinary_named_like_metadata.write_text(
                "ordinary completion evidence\n",
                encoding="utf-8",
            )
            ordinary_completion = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture ordinary evidence",
                exit_code=1,
                evidence_files=[ordinary_named_like_metadata],
                final_gate_status="failed",
            )
            self.assertEqual(
                "post_run_active", ordinary_completion["resulting_state"]
            )
            malformed_owned_evidence = fx.root / "malformed-owned.log"
            malformed_owned_evidence.write_text(
                "owned by a malformed sibling receipt\n",
                encoding="utf-8",
            )
            malformed_owner_receipt.write_text(
                json.dumps({"artifacts": "malformed-owned.log"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                rg.RoleGovernanceError, "chain.json"
            ):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture malformed owner",
                    exit_code=1,
                    evidence_files=[malformed_owned_evidence],
                    final_gate_status="failed",
                )
            malformed_owner_receipt.write_bytes(
                b'\xff{"artifacts":"malformed-owned.log"}'
            )
            with self.assertRaisesRegex(
                rg.RoleGovernanceError, "chain.json"
            ):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture invalid encoding owner",
                    exit_code=1,
                    evidence_files=[malformed_owned_evidence],
                    final_gate_status="failed",
                )
            escaped_slash_evidence = fx.root / "raw-owned" / "malformed-owned.log"
            escaped_slash_evidence.parent.mkdir(exist_ok=True)
            escaped_slash_evidence.write_text(
                "owned through escaped slash path\n",
                encoding="utf-8",
            )
            for raw_shape, raw_body, owned_evidence in (
                (
                    "escaped ownership keys",
                    b'{"\\u0061rtifacts":{"\\u0070ath":"malformed-owned.log"},',
                    malformed_owned_evidence,
                ),
                (
                    "escaped slash value",
                    b'{"artifacts":"raw-owned\\/malformed-owned.log",',
                    escaped_slash_evidence,
                ),
                (
                    "unicode escaped value",
                    b'{"artifacts":"\\u006dalformed-owned.log",',
                    malformed_owned_evidence,
                ),
                (
                    "duplicate ownership key",
                    b'{"artifacts":"malformed-owned.log","artifacts":[]}',
                    malformed_owned_evidence,
                ),
            ):
                malformed_owner_receipt.write_bytes(raw_body)
                with self.subTest(raw_malformed_owner=raw_shape):
                    with self.assertRaisesRegex(
                        rg.RoleGovernanceError, "chain.json"
                    ):
                        rg.complete(
                            fx.artifact,
                            phase="post_run",
                            run_command=f"fixture {raw_shape}",
                            exit_code=1,
                            evidence_files=[owned_evidence],
                            final_gate_status="failed",
                        )

            for non_owner_slot in ("run", "metadata"):
                non_owner_evidence = fx.root / f"{non_owner_slot}-path.log"
                non_owner_evidence.write_text(
                    "ordinary non-owner path fixture\n",
                    encoding="utf-8",
                )
                malformed_owner_receipt.write_bytes(
                    (
                        '{"'
                        + non_owner_slot
                        + '":{"path":"'
                        + non_owner_evidence.name
                        + '"},'
                    ).encode("utf-8")
                )
                with self.subTest(raw_non_owner_slot=non_owner_slot):
                    completion = rg.complete(
                        fx.artifact,
                        phase="post_run",
                        run_command=f"fixture non-owner {non_owner_slot}",
                        exit_code=1,
                        evidence_files=[non_owner_evidence],
                        final_gate_status="failed",
                    )
                    self.assertEqual(
                        "post_run_active", completion["resulting_state"]
                    )
            for non_owner_slot in ("run", "metadata"):
                non_owner_evidence = (
                    fx.root / f"duplicate-{non_owner_slot}-path.log"
                )
                non_owner_evidence.write_text(
                    "ordinary duplicate non-owner path fixture\n",
                    encoding="utf-8",
                )
                malformed_owner_receipt.write_text(
                    json.dumps(
                        {non_owner_slot: {"path": non_owner_evidence.name}}
                    )[:-1]
                    + f',"{non_owner_slot}":{{}}}}',
                    encoding="utf-8",
                )
                with self.subTest(duplicate_non_owner_slot=non_owner_slot):
                    completion = rg.complete(
                        fx.artifact,
                        phase="post_run",
                        run_command=f"fixture duplicate non-owner {non_owner_slot}",
                        exit_code=1,
                        evidence_files=[non_owner_evidence],
                        final_gate_status="failed",
                    )
                    self.assertEqual(
                        "post_run_active", completion["resulting_state"]
                    )
            malformed_owner_receipt.write_text(
                json.dumps(unrelated_owner_payload) + "\n",
                encoding="utf-8",
            )
            before_invalid_evidence = rg.load_chain(
                rg.load_context(fx.artifact)
            )["sequence"]
            for candidate in (
                other_artifact / "01_business_brief.md",
                other_precode_alias,
                other_artifact / "04_execution_report.md",
                other_postrun_alias,
                *implementation_candidates,
            ):
                with self.subTest(
                    other_uc_phase_evidence=candidate.as_posix()
                ):
                    with self.assertRaisesRegex(
                        rg.RoleGovernanceError, "phase-owned path"
                    ):
                        rg.complete(
                            fx.artifact,
                            phase="post_run",
                            run_command="fixture runner",
                            exit_code=1,
                            evidence_files=[candidate],
                            final_gate_status="failed",
                        )
            self.assertEqual(
                before_invalid_evidence,
                rg.load_chain(rg.load_context(fx.artifact))["sequence"],
            )
            other_role_evidence.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(rg.RoleGovernanceError, "role evidence directory"):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture runner",
                    exit_code=0,
                    evidence_files=[other_role_evidence],
                    final_gate_status="passed",
                )
            self.assertEqual(
                before_invalid_evidence,
                rg.load_chain(rg.load_context(fx.artifact))["sequence"],
            )
            with self.assertRaisesRegex(rg.RoleGovernanceError, "role evidence directory"):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture runner",
                    exit_code=0,
                    evidence_files=[
                        fx.artifact / "00_role_evidence" / "chain.json"
                    ],
                    final_gate_status="passed",
                )
            self.assertEqual(
                before_invalid_evidence,
                rg.load_chain(rg.load_context(fx.artifact))["sequence"],
            )
            with self.assertRaisesRegex(rg.RoleGovernanceError, "phase-owned path"):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture runner",
                    exit_code=0,
                    evidence_files=[fx.implementation],
                    final_gate_status="passed",
                )
            with self.assertRaisesRegex(rg.RoleGovernanceError, "phase-owned path"):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture runner",
                    exit_code=0,
                    evidence_files=[fx.root / "bugate.config.yaml"],
                    final_gate_status="passed",
                )
            identity_aliases = fx.root / "completion-identity-aliases"
            identity_aliases.mkdir()
            implementation_hardlink = identity_aliases / "implementation.py"
            precode_hardlink = identity_aliases / "brief.md"
            receipt_hardlink = identity_aliases / "receipt.json"
            os.link(fx.implementation, implementation_hardlink)
            os.link(fx.artifact / "01_business_brief.md", precode_hardlink)
            receipt_source = sorted(
                (fx.artifact / "00_role_evidence" / "receipts").glob("*.json")
            )[0]
            os.link(receipt_source, receipt_hardlink)
            for hardlink in (implementation_hardlink, precode_hardlink):
                with self.assertRaisesRegex(
                    rg.RoleGovernanceError, "phase-owned path"
                ):
                    rg.complete(
                        fx.artifact,
                        phase="post_run",
                        run_command="fixture runner",
                        exit_code=0,
                        evidence_files=[hardlink],
                        final_gate_status="passed",
                    )
            with self.assertRaisesRegex(
                rg.RoleGovernanceError, "role evidence directory"
            ):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="fixture runner",
                    exit_code=0,
                    evidence_files=[receipt_hardlink],
                    final_gate_status="passed",
                )
            self.assertEqual(
                before_invalid_evidence,
                rg.load_chain(rg.load_context(fx.artifact))["sequence"],
            )
            arbitrary_one = fx.root / "runlogs" / "01_execution.log"
            arbitrary_two = fx.root / "runlogs" / "04_execution" / "raw.log"
            arbitrary_one.parent.mkdir()
            arbitrary_two.parent.mkdir()
            arbitrary_one.write_text("fixture arbitrary one\n", encoding="utf-8")
            arbitrary_two.write_text("fixture arbitrary two\n", encoding="utf-8")
            named_evidence = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture named evidence",
                exit_code=1,
                evidence_files=[arbitrary_one, arbitrary_two],
                final_gate_status="failed",
            )
            self.assertEqual("post_run_active", named_evidence["resulting_state"])
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
            retry = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[log],
                final_gate_status="passed",
            )
            self.assertEqual(closed["receipt_sha256"], retry["receipt_sha256"])
            self.assertTrue(rg.status_data(fx.artifact)["ok"])
            self.assertFalse(rg.preflight(fx.artifact, "post_run").allowed)

            report = fx.artifact / "04_execution_report.md"
            report_body = report.read_bytes()
            report.write_bytes(report_body + b"drift\n")
            with self.assertRaisesRegex(rg.RoleGovernanceError, "artifact drift"):
                rg.verify_evidence(fx.artifact, phase="post_run")
            with self.assertRaisesRegex(rg.RoleGovernanceError, "artifact drift"):
                rg.verify_evidence(fx.artifact)
            self.assertFalse(rg.status_data(fx.artifact)["ok"])
            status_cli = subprocess.run(
                [str(ROOT / "bin" / "bugate-role"), "status", str(fx.artifact), "--json"],
                cwd=fx.root,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, status_cli.returncode, status_cli.stdout)
            self.assertIn("artifact drift", status_cli.stdout)
            verify_cli = subprocess.run(
                [
                    str(ROOT / "bin" / "bugate-role"),
                    "verify",
                    str(fx.artifact),
                    "--phase",
                    "post_run",
                ],
                cwd=fx.root,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, verify_cli.returncode, verify_cli.stderr)
            self.assertIn("artifact drift", verify_cli.stderr)
            report.write_bytes(report_body)

            log_body = log.read_bytes()
            log.write_bytes(log_body + b"drift\n")
            with self.assertRaisesRegex(rg.RoleGovernanceError, "artifact drift"):
                rg.verify_evidence(fx.artifact, phase="post_run")
            self.assertFalse(rg.status_data(fx.artifact)["ok"])
            log.write_bytes(log_body)
            self.assertTrue(rg.status_data(fx.artifact)["ok"])

            with self.assertRaisesRegex(rg.RoleGovernanceError, "already closed"):
                rg.complete(
                    fx.artifact,
                    phase="post_run",
                    run_command="different fixture runner",
                    exit_code=0,
                    evidence_files=[log],
                    final_gate_status="passed",
                )

        first_closed_sequence = rg.load_chain(rg.load_context(fx.artifact))["sequence"]
        with role_env("implementer", "implementer-session"):
            fx.implementation.write_text(
                "def test_fixture():\n    assert 1 == 1\n", encoding="utf-8"
            )
            next_implementer_handoff = rg.handoff(
                fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fx.implementation],
            )
        with role_env("reviewer", "reviewer-session"):
            rg.accept(
                fx.artifact,
                phase="post_run",
                handoff_id=next_implementer_handoff["receipt_sha256"],
            )
            next_closed = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[log],
                final_gate_status="passed",
            )
            next_retry = rg.complete(
                fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[log],
                final_gate_status="passed",
            )
        next_chain = rg.load_chain(rg.load_context(fx.artifact))
        self.assertEqual("closed", next_chain["state"])
        self.assertEqual(first_closed_sequence + 3, next_chain["sequence"])
        self.assertNotEqual(closed["receipt_sha256"], next_closed["receipt_sha256"])
        self.assertEqual(next_closed["receipt_sha256"], next_retry["receipt_sha256"])

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

    def test_deleted_complete_evidence_is_history_missing_not_a_new_empty_root(self):
        """Regression: deleting append-only evidence can never reset one UC."""

        fx = Fixture(self.tmp, memory_mode="required")
        memory_transitions: list[dict] = []

        def recording_memory(ctx, transition):
            memory_transitions.append(json.loads(json.dumps(transition)))
            return fake_memory(ctx, transition)

        rg._memory_prepare = recording_memory
        with role_env("designer", "designer-session-before-delete"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fx.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        self.assertEqual(2, handoff["sequence"])
        expected_head = handoff["receipt_sha256"]
        calls_before_delete = len(memory_transitions)

        shutil.rmtree(fx.artifact / "00_role_evidence")

        status = rg.status_data(fx.artifact)
        self.assertFalse(status["ok"], status)
        self.assertEqual("history_missing", status["integrity_state"])
        self.assertEqual(
            "awaiting_implementer_acceptance",
            status["lifecycle_state"],
        )
        self.assertEqual(expected_head, status["registry_head_sha256"])

        with role_env("designer", "designer-session-after-delete"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "history_missing"):
                rg.approve(fx.artifact, approved_by="qa-owner")
            with self.assertRaisesRegex(rg.RoleGovernanceError, "history_missing"):
                rg.handoff(
                    fx.artifact,
                    phase="pre_code",
                    to_role="implementer",
                )

        self.assertEqual(calls_before_delete, len(memory_transitions))
        self.assertFalse((fx.artifact / "00_role_evidence").exists())

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
            pending_status = rg.status_data(fx.artifact)
            self.assertEqual("recovery_pending", pending_status["integrity_state"])

            rg._memory_prepare = fake_memory
            recovered = rg.recover(
                fx.artifact,
                lineage_id=pending_status["lineage_id"],
                expected_head=pending_status["registry_head_sha256"],
            )
            self.assertEqual(
                "awaiting_implementer_acceptance",
                recovered["recovery_receipt"]["resulting_state"],
            )
            handoff = next(
                receipt
                for receipt in rg.verify_chain(rg.load_context(fx.artifact))
                if receipt["event"] == "designer_handoff"
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

    def test_best_effort_finalize_failure_is_persisted_in_receipt(self):
        fx = Fixture(self.tmp, memory_mode="best_effort")

        def prepare_with_failed_finalize(ctx, transition):
            prepared = fake_memory(ctx, transition)

            def fail_finalize(**kwargs):
                raise RuntimeError("injected best-effort finalize failure")

            prepared["_finalizer"] = fail_finalize
            return prepared

        rg._memory_prepare = prepare_with_failed_finalize
        with role_env("designer", "designer-session"):
            receipt = rg.approve(fx.artifact, approved_by="qa-owner")
        self.assertEqual(
            "best_effort_finalize_failed", receipt["memory"].get("status")
        )
        self.assertNotIn("_finalizer", receipt["memory"])
        verified = rg.verify_chain(rg.load_context(fx.artifact))
        self.assertEqual(receipt["receipt_sha256"], verified[-1]["receipt_sha256"])

    def test_designer_handoff_requires_prior_human_acceptance(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "human acceptance"):
                rg.handoff(fx.artifact, phase="pre_code", to_role="implementer")
        self.assertEqual(0, rg.load_chain(rg.load_context(fx.artifact))["sequence"])

    def test_same_lifecycle_role_cannot_accept_its_own_handoff(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-session"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("designer", "different-designer-session"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "not allowed in phase"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=handoff["receipt_sha256"],
                )

    def test_acceptance_must_track_latest_handoff_generation(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-1"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            designer_handoff_1 = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "implementer-1"):
            rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff_1["receipt_sha256"],
            )
        with role_env("designer", "designer-2"):
            designer_handoff_2 = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        self.assertNotEqual(
            designer_handoff_1["receipt_sha256"],
            designer_handoff_2["receipt_sha256"],
        )
        with role_env("implementer", "implementer-1"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "acceptance is stale"):
                rg.verify_evidence(fx.artifact, phase="implementation")
        with role_env("implementer", "implementer-2"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "handoff is stale"):
                rg.accept(
                    fx.artifact,
                    phase="implementation",
                    handoff_id=designer_handoff_1["receipt_sha256"],
                )
            rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff_2["receipt_sha256"],
            )
            fx.implementation.write_text(
                "def test_fixture(): pass\n", encoding="utf-8"
            )
            implementer_handoff_1 = rg.handoff(
                fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fx.implementation],
            )
        with role_env("reviewer", "reviewer-1"):
            rg.accept(
                fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff_1["receipt_sha256"],
            )
        with role_env("implementer", "implementer-2"):
            fx.implementation.write_text(
                "def test_fixture():\n    assert True\n", encoding="utf-8"
            )
            implementer_handoff_2 = rg.handoff(
                fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fx.implementation],
            )
        with role_env("reviewer", "reviewer-1"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "post-run is locked"):
                rg.verify_evidence(fx.artifact, phase="post_run")
            self.assertEqual(
                "awaiting_reviewer_acceptance",
                rg.load_chain(rg.load_context(fx.artifact))["state"],
            )
        with role_env("reviewer", "reviewer-2"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "handoff is stale"):
                rg.accept(
                    fx.artifact,
                    phase="post_run",
                    handoff_id=implementer_handoff_1["receipt_sha256"],
                )
            rg.accept(
                fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff_2["receipt_sha256"],
            )
            self.assertTrue(rg.verify_evidence(fx.artifact, phase="post_run"))

    def test_upstream_generation_relocks_postrun_after_reviewer_acceptance(self):
        fx = Fixture(self.tmp)
        with role_env("designer", "designer-1"):
            rg.approve(fx.artifact, approved_by="qa-owner")
            designer_handoff_1 = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "implementer-1"):
            rg.accept(
                fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff_1["receipt_sha256"],
            )
            fx.implementation.write_text(
                "def test_fixture(): pass\n", encoding="utf-8"
            )
            implementer_handoff = rg.handoff(
                fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fx.implementation],
            )
        with role_env("reviewer", "reviewer-1"):
            rg.accept(
                fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )
            self.assertTrue(rg.preflight(fx.artifact, "post_run").allowed)

        with role_env("designer", "designer-2"):
            designer_handoff_2 = rg.handoff(
                fx.artifact, phase="pre_code", to_role="implementer"
            )
        self.assertNotEqual(
            designer_handoff_1["receipt_sha256"],
            designer_handoff_2["receipt_sha256"],
        )
        self.assertEqual(
            "awaiting_implementer_acceptance",
            rg.load_chain(rg.load_context(fx.artifact))["state"],
        )
        with role_env("reviewer", "reviewer-1"):
            with self.assertRaisesRegex(rg.RoleGovernanceError, "post-run is locked"):
                rg.verify_evidence(fx.artifact, phase="post_run")
            denied = rg.preflight(fx.artifact, "post_run")
            self.assertFalse(denied.allowed)
            self.assertTrue(any("post-run is locked" in item for item in denied.errors))

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
