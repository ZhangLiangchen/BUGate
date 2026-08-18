#!/usr/bin/env python3
"""Integrity and strict-Memory regression tests for the self-heal sidecar."""

from __future__ import annotations

import copy
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import role_governance as rg  # noqa: E402
import memory_bus  # noqa: E402
import self_heal_gate as gate  # noqa: E402
import self_heal_sidecar as sidecar  # noqa: E402
from self_heal_policy import self_healing_policy  # noqa: E402
from bugate_selfheal_fixture import (  # noqa: E402
    GOLDEN_LOG_NAME,
    ImportedFixture,
    PASSING_LOG,
    PRECODE,
    gate_result,
)
from test_memory_handoff_strict import fake_memory_service  # noqa: E402


LOG_TEST_CODE = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   NameError: name 'exepcted' is not defined
tests/test_UC-SELFHEAL.py:5: NameError
"""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object at {path}")
    return value


def tree_bytes(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def complete_main_lifecycle(fixture: ImportedFixture) -> dict[str, object]:
    """Publish one authentic reviewer completion on an imported fixture."""

    fixture.write_log(PASSING_LOG)
    reports = fixture.run_postrun(exit_code=0)
    if reports.returncode != 0:
        raise AssertionError(reports.stdout + reports.stderr)
    for name in ("04_execution_report.md", "05_knowledge_update.md"):
        path = fixture.artifact / name
        body = path.read_text(encoding="utf-8")
        path.write_text(
            body.replace("gate_status: draft", "gate_status: passed", 1),
            encoding="utf-8",
        )
    return fixture._role_json(
        [
            "complete",
            fixture.artifact,
            "--phase",
            "post_run",
            "--run-command",
            "reviewed passing fixture run",
            "--exit-code",
            "0",
            "--evidence-file",
            fixture.root / GOLDEN_LOG_NAME,
            "--gate-status",
            "passed",
        ],
        role="reviewer",
        session="reviewer-session",
    )


@contextmanager
def actor(root: Path, role: str, session: str):
    with mock.patch.dict(
        os.environ,
        {
            "BUGATE_PROJECT_ROOT": str(root),
            "BUGATE_AGENT_ROLE": role,
            "BUGATE_SESSION_ID": session,
            "BUGATE_AGENT_RUNTIME": "codex",
        },
        clear=False,
    ):
        yield


@contextmanager
def isolated_unavailable_memory(memory_home: Path):
    """Point Memory at a closed loopback port without changing fixture identity."""

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    keys = (
        "MEMORY_BUS_URL",
        "MEMORY_BUS_PROJECT_TAG",
        "MCP_MEMORY_BASE_DIR",
        "BUGATE_MEMORY_HOME",
        "MCP_API_KEY_AGENT",
        "MCP_API_KEY_HUMAN",
        "MCP_API_KEY",
    )
    old = {key: os.environ.get(key) for key in keys}
    memory_home.mkdir(mode=0o700)
    os.environ["MEMORY_BUS_URL"] = f"http://127.0.0.1:{port}"
    os.environ["MCP_MEMORY_BASE_DIR"] = str(memory_home)
    os.environ["BUGATE_MEMORY_HOME"] = str(memory_home)
    # Let each fixture's committed config determine its namespace and make
    # inherited credentials unreachable as well as undisclosed.
    for key in (
        "MEMORY_BUS_PROJECT_TAG",
        "MCP_API_KEY_AGENT",
        "MCP_API_KEY_HUMAN",
        "MCP_API_KEY",
    ):
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class SidecarIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Build one authentic post-run role chain for all direct-store tests.

        The sidecar anchor now verifies the complete main-chain history before
        any observed anchor can become drift evidence.  A hand-written
        ``chain.json`` with imaginary receipts would make these tests green at
        the wrong control, so the shared template is produced by the real role
        governance CLI and copied into each hermetic fixture.
        """

        cls.role_template_ctx = tempfile.TemporaryDirectory(
            prefix="bugate-sidecar-role-template-"
        )
        base = Path(cls.role_template_ctx.name)
        cls.role_template = ImportedFixture(base / "primary")
        cls.role_template_alt = ImportedFixture(base / "alternate")
        cls.role_template_closed = ImportedFixture(base / "closed")
        for template in (
            cls.role_template,
            cls.role_template_alt,
            cls.role_template_closed,
        ):
            template_env = template.env

            def strict_template_env(
                *,
                role: str | None = None,
                session: str | None = None,
                _template_env: Callable[..., dict[str, str]] = template_env,
            ) -> dict[str, str]:
                env = _template_env(role=role, session=session)
                env["MEMORY_BUS_PROJECT_TAG"] = "project:strict-fixture"
                return env

            template.env = strict_template_env  # type: ignore[method-assign]
            template.drive_to_post_run(ImportedFixture.BROKEN_IMPLEMENTATION)
        complete_main_lifecycle(cls.role_template_closed)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.role_template_ctx.cleanup()

    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-sidecar-integrity-")
        self.tmp = Path(self.tmp_ctx.name)
        self.counter = 0
        # A developer machine may already expose a real Memory service through
        # MEMORY_BUS_URL.  Keep every test hermetic by default; tests that need
        # strict or observable Memory behavior enter fake_memory_service(),
        # which temporarily replaces this deliberately unavailable endpoint.
        self.memory_env = isolated_unavailable_memory(self.tmp / "memory-home")
        self.memory_env.__enter__()
        self.lineage_patch = mock.patch.object(
            sidecar,
            "lineage_identity",
            return_value={"lineage_id": "f" * 64},
        )
        self.lineage_patch.start()

    def tearDown(self) -> None:
        self.lineage_patch.stop()
        self.memory_env.__exit__(None, None, None)
        self.tmp_ctx.cleanup()

    def fixture(
        self,
        *,
        memory_mode: str = "required",
        session_id_required: bool = True,
    ) -> sidecar.SelfHealSidecar:
        self.counter += 1
        root = self.tmp / f"fixture-{self.counter}"
        shutil.copytree(self.role_template.root, root)
        artifact = root / "usecases" / ImportedFixture.uc
        evidence = artifact / "00_role_evidence"
        ctx = rg.GovernanceContext(
            root=root,
            artifact_dir=artifact,
            config={
                "memory": {"namespace": "project:strict-fixture"},
                "required_precode_artifacts": list(PRECODE),
            },
            policy={
                "mode": "required",
                "memory_mode": memory_mode,
                "evidence_dir": "00_role_evidence",
                "session_id_required": session_id_required,
                "require_distinct_sessions": True,
                "human_acceptance_artifacts": ["03b_adversarial_cases.yaml"],
            },
            profile_path=root / "bugate.profile.yaml",
            uc=ImportedFixture.uc,
        )
        return sidecar.SelfHealSidecar(ctx)

    def publish_triage(self, store: sidecar.SelfHealSidecar) -> None:
        with actor(store.ctx.root, "reviewer", "triage-session"):
            store.publish(
                sidecar.EVENT_TRIAGE,
                attempt_id="attempt-1",
                payload={"healing_eligible": True},
            )

    def publish_handoff(self, store: sidecar.SelfHealSidecar) -> dict[str, object]:
        with actor(store.ctx.root, "reviewer", "triage-session"):
            return store.publish(
                sidecar.EVENT_HANDOFF,
                attempt_id="attempt-1",
                payload={"healing_eligible": True},
            )

    def advance_main_chain_validly(self, store: sidecar.SelfHealSidecar) -> None:
        """Replace the main chain with another fully verified post-run history."""

        evidence = store.ctx.evidence_dir
        shutil.rmtree(evidence)
        shutil.copytree(
            self.role_template_alt.artifact / "00_role_evidence",
            evidence,
        )
        # Prove this is a legitimate alternate lifecycle, not a malformed object
        # that would make a drift test green at the envelope validator.
        self.assertEqual(5, len(rg.verify_chain(store.ctx)))

    def close_main_chain_validly(self, store: sidecar.SelfHealSidecar) -> None:
        """Replace the main chain with a fully verified closed lifecycle."""

        evidence = store.ctx.evidence_dir
        shutil.rmtree(evidence)
        shutil.copytree(
            self.role_template_closed.artifact / "00_role_evidence",
            evidence,
        )
        receipts = rg.verify_chain(store.ctx)
        self.assertEqual("reviewer_completion", receipts[-1]["event"])
        self.assertEqual("closed", rg.load_chain(store.ctx)["state"])

    def valid_two_receipt_store(self) -> sidecar.SelfHealSidecar:
        store = self.fixture()
        self.publish_triage(store)
        self.publish_handoff(store)
        self.assertEqual(2, store.load()["sequence"])
        return store

    def kill_after_handoff_receipt(
        self,
        store: sidecar.SelfHealSidecar,
    ) -> subprocess.CompletedProcess[str]:
        script = f"""\
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, {str(ROOT / 'scripts')!r})
import role_governance as rg
import self_heal_sidecar as sidecar

ctx = rg.GovernanceContext(
    root=Path({str(store.ctx.root)!r}),
    artifact_dir=Path({str(store.ctx.artifact_dir)!r}),
    config={store.ctx.config!r},
    policy={store.ctx.policy!r},
    profile_path=Path({str(store.ctx.profile_path)!r}),
    uc={store.ctx.uc!r},
)
sidecar.lineage_identity = lambda _artifact_dir: {{"lineage_id": "f" * 64}}
original_atomic = sidecar._atomic_bytes

def crash_after_receipt(path, body, *, replace, mode=0o600):
    original_atomic(path, body, replace=replace, mode=mode)
    if Path(path).name == "001-self_heal_handoff.json":
        os.kill(os.getpid(), signal.SIGKILL)

sidecar._atomic_bytes = crash_after_receipt
sidecar.SelfHealSidecar(ctx).publish(
    sidecar.EVENT_HANDOFF,
    attempt_id="attempt-1",
    payload={{"healing_eligible": True}},
)
"""
        env = os.environ.copy()
        env.update(
            {
                "BUGATE_PROJECT_ROOT": str(store.ctx.root),
                "BUGATE_AGENT_ROLE": "reviewer",
                "BUGATE_SESSION_ID": "triage-session",
                "BUGATE_AGENT_RUNTIME": "codex",
            }
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def interrupt_after_receipt(
        self,
        store: sidecar.SelfHealSidecar,
        event: str,
        *,
        role: str,
        session: str,
        payload: dict[str, Any],
        resulting_state: str | None = None,
    ) -> tuple[Path, bytes]:
        """Leave the selected next receipt durable but its chain entry absent."""

        attempt_id = store.attempt_id() or "attempt-1"
        receipt_name = f"{store.load()['sequence']:03d}-{event}.json"
        receipt_path = store.dir / receipt_name
        original_atomic = sidecar._atomic_bytes

        def interrupt(
            path: Path,
            body: bytes,
            *,
            replace: bool,
            mode: int = 0o600,
        ) -> None:
            original_atomic(path, body, replace=replace, mode=mode)
            if Path(path).name == receipt_name:
                raise RuntimeError("simulated interruption after receipt durability")

        with mock.patch.object(sidecar, "_atomic_bytes", side_effect=interrupt):
            with actor(store.ctx.root, role, session):
                with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                    store.publish(
                        event,
                        attempt_id=attempt_id,
                        payload=payload,
                        resulting_state=resulting_state,
                    )
        self.assertTrue(receipt_path.is_file())
        return receipt_path, receipt_path.read_bytes()

    def advance_direct_store_to(self, store: sidecar.SelfHealSidecar, event: str) -> None:
        """Publish the strict predecessors of ``event`` without bypassing checks."""

        if event == sidecar.EVENT_TRIAGE:
            return
        self.publish_triage(store)
        if event == sidecar.EVENT_HANDOFF:
            return
        self.publish_handoff(store)
        if event == sidecar.EVENT_HEALER_ACCEPT:
            return
        with actor(store.ctx.root, "implementer", "healer-session"):
            store.publish(
                sidecar.EVENT_HEALER_ACCEPT,
                attempt_id="attempt-1",
                payload={"accepted": True},
            )
        if event == sidecar.EVENT_HEALER_HANDOFF:
            return
        with actor(store.ctx.root, "implementer", "healer-session"):
            store.publish(
                sidecar.EVENT_HEALER_HANDOFF,
                attempt_id="attempt-1",
                payload={"candidate": "verified"},
            )
        if event == sidecar.EVENT_REVIEW:
            return
        with actor(store.ctx.root, "reviewer", "independent-session"):
            store.publish(
                sidecar.EVENT_REVIEW,
                attempt_id="attempt-1",
                payload={"outcome": sidecar.STATE_VERIFIED},
                resulting_state=sidecar.STATE_VERIFIED,
            )

    def assert_integrity_failure(
        self,
        store: sidecar.SelfHealSidecar,
        expected_message: str,
        *,
        via_state: bool = False,
    ) -> None:
        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
            store.state() if via_state else store.load()
        self.assertEqual(sidecar.REASON_INTEGRITY, caught.exception.reason)
        self.assertIn(expected_message, str(caught.exception))

    def verified_cli_fixture(
        self,
        label: str,
        *,
        mode: str = "verify",
    ) -> ImportedFixture:
        fixture = ImportedFixture(
            self.tmp / f"cli-{label}",
            self_healing={
                "mode": mode,
                "allowed_write_regex": [r"^tests/test_.*\.py$"],
                "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
                "falsification_spec": "falsification_spec.yaml",
                "max_attempts": 3,
            },
        )
        fixture.drive_to_post_run(ImportedFixture.BROKEN_IMPLEMENTATION)
        fixture.write_falsification_spec()
        fixture.write_log(LOG_TEST_CODE)

        for args, role, session in (
            (
                (
                    "--pytest-log",
                    GOLDEN_LOG_NAME,
                    "--command",
                    ImportedFixture.VERIFICATION_COMMAND,
                    "--exit-code",
                    "1",
                ),
                "reviewer",
                "reviewer-session",
            ),
            (("--self-heal-step", "handoff"), "reviewer", "reviewer-session"),
            (("--self-heal-step", "accept"), "implementer", "healer-session"),
        ):
            proc = fixture.run_self_heal(*args, role=role, session=session)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

        candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": ImportedFixture.REPAIRED_IMPLEMENTATION}
        )
        proposed = fixture.run_self_heal(
            "--self-heal-step",
            "propose",
            "--candidate-dir",
            str(candidate),
            "--pytest-log",
            GOLDEN_LOG_NAME,
            role="implementer",
            session="healer-session",
        )
        self.assertEqual(0, proposed.returncode, proposed.stdout + proposed.stderr)

        review_name = fixture.write_review()
        reviewed = fixture.run_self_heal(
            "--self-heal-step",
            "review",
            "--review-file",
            review_name,
            role="reviewer",
            session="independent-review-session",
        )
        self.assertEqual(0, reviewed.returncode, reviewed.stdout + reviewed.stderr)
        self.assertEqual(sidecar.STATE_VERIFIED, fixture.sidecar_state())
        return fixture

    def triaged_cli_fixture(self, label: str) -> ImportedFixture:
        fixture = ImportedFixture(
            self.tmp / f"cli-triaged-{label}",
            self_healing={
                "mode": "verify",
                "allowed_write_regex": [r"^tests/test_.*\.py$"],
                "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
                "falsification_spec": "falsification_spec.yaml",
                "max_attempts": 3,
            },
        )
        fixture.drive_to_post_run(ImportedFixture.BROKEN_IMPLEMENTATION)
        fixture.write_log(LOG_TEST_CODE)
        triaged = fixture.run_self_heal(
            "--pytest-log",
            GOLDEN_LOG_NAME,
            "--command",
            ImportedFixture.VERIFICATION_COMMAND,
            "--exit-code",
            "1",
            role="reviewer",
            session="reviewer-session",
        )
        self.assertEqual(0, triaged.returncode, triaged.stdout + triaged.stderr)
        return fixture

    def rewrite_receipt_chain_from(
        self,
        store: sidecar.SelfHealSidecar,
        sequence: int,
        mutate: Callable[[dict[str, Any]], None],
    ) -> None:
        """Make a deliberate self-consistent tamper from one indexed receipt."""

        chain = read_json(store.chain_path)
        parent_hash = ""
        for index, entry in enumerate(chain["entries"], 1):
            receipt_path = store.dir / str(entry["receipt"])
            receipt = read_json(receipt_path)
            if index < sequence:
                parent_hash = str(receipt["receipt_sha256"])
                continue
            if index == sequence:
                mutate(receipt)
            else:
                receipt["parent_receipt_sha256"] = parent_hash
            receipt["receipt_sha256"] = sidecar.receipt_sha256(receipt)
            write_json(receipt_path, receipt)
            entry["receipt_sha256"] = receipt["receipt_sha256"]
            parent_hash = receipt["receipt_sha256"]
        chain["head_sha256"] = parent_hash
        write_json(store.chain_path, chain)

    def test_cli_close_rejects_corrupt_sidecar_without_rewriting_any_bytes(self) -> None:
        def mutate_chain(
            fixture: ImportedFixture,
            mutate: Callable[[dict[str, Any]], None],
        ) -> None:
            chain_path = fixture.artifact / "00_self_healing" / "chain.json"
            chain = read_json(chain_path)
            mutate(chain)
            write_json(chain_path, chain)

        cases: tuple[tuple[str, Callable[[ImportedFixture], None]], ...] = (
            (
                "last_entry_state_changed",
                lambda fixture: mutate_chain(
                    fixture,
                    lambda chain: chain["entries"][-1].__setitem__(
                        "state", sidecar.STATE_REJECTED
                    ),
                ),
            ),
            (
                "last_receipt_sha256_changed",
                lambda fixture: mutate_chain(
                    fixture,
                    lambda chain: chain["entries"][-1].__setitem__(
                        "receipt_sha256", "0" * 64
                    ),
                ),
            ),
            (
                "empty_chain_object",
                lambda fixture: write_json(
                    fixture.artifact / "00_self_healing" / "chain.json", {}
                ),
            ),
            (
                "truncated_chain_json",
                lambda fixture: (
                    fixture.artifact / "00_self_healing" / "chain.json"
                ).write_bytes(b'{"schema": "bugate.self-heal-chain/v1"'),
            ),
        )

        for label, corrupt in cases:
            with self.subTest(corruption=label):
                fixture = self.verified_cli_fixture(label)
                implementation_before = fixture.implementation.read_bytes()
                corrupt(fixture)
                corrupted_artifact_before = tree_bytes(fixture.artifact)

                closed = fixture.run_self_heal(
                    "--self-heal-step",
                    "close",
                    role="reviewer",
                    session="independent-review-session",
                )
                combined_output = closed.stdout + closed.stderr
                self.assertNotIn("Traceback", combined_output)
                self.assertEqual(2, closed.returncode, combined_output)

                parsed = gate_result(closed)
                self.assertEqual(
                    [
                        "status",
                        "exit_code",
                        "blocking_reasons",
                        "artifact_paths",
                        "next_action",
                    ],
                    list(parsed),
                )
                self.assertEqual("blocked", parsed["status"])
                self.assertEqual(2, parsed["exit_code"])
                self.assertEqual(
                    [sidecar.REASON_INTEGRITY], parsed["blocking_reasons"]
                )
                self.assertEqual(
                    implementation_before,
                    fixture.implementation.read_bytes(),
                )
                self.assertEqual(
                    corrupted_artifact_before,
                    tree_bytes(fixture.artifact),
                    "the failed close silently rewrote corrupted sidecar/artifact bytes",
                )

    def test_strict_memory_anchors_all_three_frozen_events_end_to_end(self) -> None:
        with fake_memory_service() as memory:
            store = self.fixture()
            self.publish_triage(store)
            handoff = self.publish_handoff(store)
            with actor(store.ctx.root, "implementer", "healer-session"):
                acceptance = store.publish(
                    sidecar.EVENT_HEALER_ACCEPT,
                    attempt_id="attempt-1",
                    payload={"accepted": True},
                )
                store.publish(
                    sidecar.EVENT_HEALER_HANDOFF,
                    attempt_id="attempt-1",
                    payload={"candidate": "verified"},
                )
            with actor(store.ctx.root, "reviewer", "independent-session"):
                review = store.publish(
                    sidecar.EVENT_REVIEW,
                    attempt_id="attempt-1",
                    payload={"outcome": sidecar.STATE_VERIFIED},
                    resulting_state=sidecar.STATE_VERIFIED,
                )

            for receipt, route in (
                (handoff, ("reviewer", "implementer")),
                (acceptance, ("reviewer", "implementer")),
                (review, ("implementer", "reviewer")),
            ):
                self.assertEqual(sidecar.EVIDENCE_SCHEMA, receipt["schema"])
                self.assertEqual(sidecar.TRANSITION_SCHEMA, receipt["sidecar_transition"]["schema"])
                self.assertEqual("post_run", receipt["phase"])
                self.assertEqual(route, (receipt["from_role"], receipt["to_role"]))
                self.assertTrue(receipt["memory"]["memory_id"])
                self.assertEqual(64, len(receipt["transition_sha256"]))

            self.assertEqual(sidecar.STATE_VERIFIED, store.state())
            self.assertEqual(5, store.load()["sequence"])
            self.assertEqual(3, sum(method == "POST" for method, _path in memory.calls))
            self.assertEqual(3, sum(method == "PUT" for method, _path in memory.calls))
            self.assertEqual(12, sum(method == "GET" for method, _path in memory.calls))

    def test_triage_requires_exact_true_before_memory_or_local_write(self) -> None:
        with fake_memory_service() as memory:
            store = self.fixture()
            before = tree_bytes(store.ctx.artifact_dir)
            memory_calls_before = list(memory.calls)
            cases = (
                ("false", {"healing_eligible": False}),
                ("missing", {}),
                ("non_boolean", {"healing_eligible": 1}),
            )

            for label, payload in cases:
                with self.subTest(case=label), actor(
                    store.ctx.root,
                    "reviewer",
                    "triage-session",
                ):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_TRIAGE,
                            attempt_id="attempt-1",
                            payload=payload,
                        )
                    self.assertEqual(
                        sidecar.REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE,
                        caught.exception.reason,
                    )
                    self.assertIn("payload.healing_eligible", str(caught.exception))
                    self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))
                    self.assertEqual(memory_calls_before, memory.calls)
                    self.assertFalse(store.dir.exists())

            self.assertEqual(sidecar.STATE_NONE, store.state())

    def test_every_event_requires_an_object_payload_before_any_write(self) -> None:
        cases = (
            (
                sidecar.EVENT_HANDOFF,
                "reviewer",
                "triage-session",
                sidecar.STATE_TRIAGE_RECORDED,
            ),
            (
                sidecar.EVENT_HEALER_ACCEPT,
                "implementer",
                "healer-session",
                sidecar.STATE_AWAITING_HEALER,
            ),
            (
                sidecar.EVENT_HEALER_HANDOFF,
                "implementer",
                "healer-session",
                sidecar.STATE_HEALING_ACTIVE,
            ),
        )
        for event, role, session, expected_state in cases:
            with self.subTest(event=event), fake_memory_service() as memory:
                store = self.fixture()
                self.advance_direct_store_to(store, event)
                before = tree_bytes(store.ctx.artifact_dir)
                memory_calls_before = list(memory.calls)
                with actor(store.ctx.root, role, session):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            event,
                            attempt_id="attempt-1",
                            payload=[],  # type: ignore[arg-type]
                        )
                self.assertEqual(
                    sidecar.REASON_EVENT_PAYLOAD_INVALID,
                    caught.exception.reason,
                )
                self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))
                self.assertEqual(memory_calls_before, memory.calls)
                self.assertEqual(expected_state, store.state())

    def test_direct_api_rejects_non_json_payload_and_untyped_identity(self) -> None:
        cyclic: dict[str, Any] = {}
        cyclic["self"] = cyclic
        invalid_payloads = (
            {"binary": b"not-json"},
            {1: "non-string-key"},
            cyclic,
            {"number": float("nan")},
            {"number": 10**5000},
        )
        with fake_memory_service() as memory:
            store = self.fixture(memory_mode="best_effort")
            self.publish_triage(store)
            before = tree_bytes(store.ctx.artifact_dir)
            memory_calls_before = list(memory.calls)
            for payload in invalid_payloads:
                with self.subTest(payload_type=type(next(iter(payload.values()))).__name__), actor(
                    store.ctx.root,
                    "reviewer",
                    "triage-session",
                ):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_HANDOFF,
                            attempt_id="attempt-1",
                            payload=payload,  # type: ignore[arg-type]
                        )
                    self.assertEqual(
                        sidecar.REASON_EVENT_PAYLOAD_INVALID,
                        caught.exception.reason,
                    )
                    self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))
                    self.assertEqual(memory_calls_before, memory.calls)

            for bad_event in ([], {}):
                with self.subTest(bad_event=type(bad_event).__name__):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.preflight_publish(  # type: ignore[arg-type]
                            bad_event,
                            "attempt-1",
                            payload={},
                        )
                    self.assertEqual(sidecar.REASON_TRANSITION, caught.exception.reason)
                    self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))

            for bad_attempt in (1, [], {}, b"attempt-1"):
                with self.subTest(bad_attempt=type(bad_attempt).__name__):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.preflight_publish(  # type: ignore[arg-type]
                            sidecar.EVENT_HANDOFF,
                            bad_attempt,
                            payload={},
                        )
                    self.assertEqual(
                        sidecar.REASON_ATTEMPT_MISMATCH,
                        caught.exception.reason,
                    )
                    self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))

    def test_replay_and_handoff_reject_self_consistent_ineligible_triage(self) -> None:
        fixture = self.triaged_cli_fixture("anchor-main-publication-race")
        self.lineage_patch.stop()
        store = sidecar.SelfHealSidecar(rg.load_context(fixture.artifact))
        chain = read_json(store.chain_path)
        triage_entry = chain["entries"][0]
        triage_path = store.dir / str(triage_entry["receipt"])
        triage = read_json(triage_path)
        triage["payload"]["healing_eligible"] = False
        triage["receipt_sha256"] = sidecar.receipt_sha256(triage)
        write_json(triage_path, triage)
        triage_entry["receipt_sha256"] = triage["receipt_sha256"]
        chain["head_sha256"] = triage["receipt_sha256"]
        write_json(store.chain_path, chain)
        tampered = tree_bytes(store.ctx.artifact_dir)

        with actor(store.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as handoff_error:
                store.publish(
                    sidecar.EVENT_HANDOFF,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )
        self.assertEqual(
            sidecar.REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE,
            handoff_error.exception.reason,
        )
        with self.assertRaises(sidecar.SelfHealSidecarError) as replay_error:
            store.load()
        self.assertEqual(
            sidecar.REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE,
            replay_error.exception.reason,
        )
        self.assertIn("000-triage_recorded.json", str(replay_error.exception))

        with mock.patch.object(
            gate,
            "load_context",
            return_value=store.ctx,
        ), mock.patch.object(
            gate,
            "self_healing_policy",
            return_value={"mode": "verify"},
        ):
            status = gate.run(
                store.ctx.artifact_dir,
                SimpleNamespace(self_heal_step="status"),
            )
        self.assertEqual(
            [
                "status",
                "exit_code",
                "blocking_reasons",
                "artifact_paths",
                "next_action",
            ],
            list(status),
        )
        self.assertEqual("blocked", status["status"])
        self.assertEqual(2, status["exit_code"])
        self.assertEqual(
            [sidecar.REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE],
            status["blocking_reasons"],
        )
        self.assertEqual(tampered, tree_bytes(store.ctx.artifact_dir))

    def test_orphan_replay_rejects_self_consistent_ineligible_triage(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        orphan_path, _original_body = self.interrupt_after_receipt(
            store,
            sidecar.EVENT_TRIAGE,
            role="reviewer",
            session="triage-session",
            payload={"healing_eligible": True},
        )
        orphan = read_json(orphan_path)
        orphan["payload"]["healing_eligible"] = False
        orphan["receipt_sha256"] = sidecar.receipt_sha256(orphan)
        write_json(orphan_path, orphan)
        tampered = tree_bytes(store.ctx.artifact_dir)

        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
            store.load()
        self.assertEqual(
            sidecar.REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE,
            caught.exception.reason,
        )
        self.assertIn("000-triage_recorded.json", str(caught.exception))
        self.assertEqual(tampered, tree_bytes(store.ctx.artifact_dir))
        self.assertFalse(store.chain_path.exists())
        self.assertTrue(orphan_path.is_file())

    def test_review_outcome_mismatch_is_rejected_before_memory_or_local_write(self) -> None:
        with fake_memory_service() as memory:
            store = self.fixture()
            self.advance_direct_store_to(store, sidecar.EVENT_REVIEW)
            before = tree_bytes(store.ctx.artifact_dir)
            memory_calls_before = list(memory.calls)

            cases = (
                (sidecar.STATE_VERIFIED, sidecar.STATE_REJECTED),
                (sidecar.STATE_REJECTED, sidecar.STATE_VERIFIED),
            )
            for outcome, resulting_state in cases:
                with self.subTest(
                    outcome=outcome,
                    resulting_state=resulting_state,
                ), actor(store.ctx.root, "reviewer", "independent-session"):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_REVIEW,
                            attempt_id="attempt-1",
                            payload={"outcome": outcome},
                            resulting_state=resulting_state,
                        )
                    self.assertEqual(
                        sidecar.REASON_REVIEW_OUTCOME_MISMATCH,
                        caught.exception.reason,
                    )
                    self.assertIn("payload.outcome", str(caught.exception))
                    self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))
                    self.assertEqual(memory_calls_before, memory.calls)

            self.assertEqual(sidecar.STATE_AWAITING_REVIEW, store.state())
            self.assertEqual(4, store.load()["sequence"])

    def test_replay_rejects_self_consistent_review_outcome_tamper_without_write(self) -> None:
        with fake_memory_service():
            store = self.fixture()
            self.advance_direct_store_to(store, sidecar.EVENT_REVIEW)
            with actor(store.ctx.root, "reviewer", "independent-session"):
                store.publish(
                    sidecar.EVENT_REVIEW,
                    attempt_id="attempt-1",
                    payload={"outcome": sidecar.STATE_VERIFIED},
                    resulting_state=sidecar.STATE_VERIFIED,
                )

            chain = read_json(store.chain_path)
            review_entry = chain["entries"][-1]
            review_path = store.dir / str(review_entry["receipt"])
            review = read_json(review_path)
            review["payload"]["outcome"] = sidecar.STATE_REJECTED
            review["receipt_sha256"] = sidecar.receipt_sha256(review)
            write_json(review_path, review)
            review_entry["receipt_sha256"] = review["receipt_sha256"]
            chain["head_sha256"] = review["receipt_sha256"]
            write_json(store.chain_path, chain)
            tampered = tree_bytes(store.ctx.artifact_dir)

            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                store.load()
            self.assertEqual(
                sidecar.REASON_REVIEW_OUTCOME_MISMATCH,
                caught.exception.reason,
            )
            self.assertIn("004-independent_review.json", str(caught.exception))
            with mock.patch.object(
                gate,
                "load_context",
                return_value=store.ctx,
            ), mock.patch.object(
                gate,
                "self_healing_policy",
                return_value={"mode": "verify"},
            ):
                status = gate.run(
                    store.ctx.artifact_dir,
                    SimpleNamespace(self_heal_step="status"),
                )
            self.assertEqual(
                [
                    "status",
                    "exit_code",
                    "blocking_reasons",
                    "artifact_paths",
                    "next_action",
                ],
                list(status),
            )
            self.assertEqual("blocked", status["status"])
            self.assertEqual(2, status["exit_code"])
            self.assertEqual(
                [sidecar.REASON_REVIEW_OUTCOME_MISMATCH],
                status["blocking_reasons"],
            )
            self.assertEqual(tampered, tree_bytes(store.ctx.artifact_dir))

    def test_closed_status_is_consistent_with_each_review_outcome(self) -> None:
        cases = (
            (sidecar.STATE_VERIFIED, gate.STATUS_VERIFIED),
            (sidecar.STATE_REJECTED, gate.STATUS_REJECTED),
        )
        for outcome, expected_status in cases:
            with self.subTest(outcome=outcome), fake_memory_service():
                store = self.fixture()
                self.advance_direct_store_to(store, sidecar.EVENT_REVIEW)
                with actor(store.ctx.root, "reviewer", "independent-session"):
                    store.publish(
                        sidecar.EVENT_REVIEW,
                        attempt_id="attempt-1",
                        payload={"outcome": outcome},
                        resulting_state=outcome,
                    )
                    store.publish(
                        sidecar.EVENT_CLOSED,
                        attempt_id="attempt-1",
                        payload={"final_state": outcome},
                    )

                snapshot = store.status_snapshot()
                self.assertEqual(sidecar.STATE_CLOSED, snapshot["state"])
                self.assertEqual(outcome, snapshot["review_outcome"])
                before_status = tree_bytes(store.ctx.artifact_dir)
                status = gate.step_status(store.ctx, {}, SimpleNamespace())
                self.assertEqual(
                    [
                        "status",
                        "exit_code",
                        "blocking_reasons",
                        "artifact_paths",
                        "next_action",
                    ],
                    list(status),
                )
                self.assertEqual(expected_status, status["status"])
                self.assertEqual(before_status, tree_bytes(store.ctx.artifact_dir))

    def test_close_final_state_is_bound_on_publish_and_replay(self) -> None:
        """The immutable close receipt cannot contradict its reviewed predecessor."""

        for reviewed_state, wrong_final in (
            (sidecar.STATE_VERIFIED, sidecar.STATE_REJECTED),
            (sidecar.STATE_REJECTED, sidecar.STATE_VERIFIED),
        ):
            with self.subTest(
                reviewed_state=reviewed_state,
                wrong_final=wrong_final,
            ), fake_memory_service():
                store = self.fixture()
                self.advance_direct_store_to(store, sidecar.EVENT_REVIEW)
                with actor(store.ctx.root, "reviewer", "independent-session"):
                    store.publish(
                        sidecar.EVENT_REVIEW,
                        attempt_id="attempt-1",
                        payload={"outcome": reviewed_state},
                        resulting_state=reviewed_state,
                    )
                before = tree_bytes(store.ctx.artifact_dir)
                with actor(store.ctx.root, "reviewer", "independent-session"):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_CLOSED,
                            attempt_id="attempt-1",
                            payload={"final_state": wrong_final},
                        )
                self.assertEqual(
                    sidecar.REASON_CLOSE_FINAL_STATE_MISMATCH,
                    caught.exception.reason,
                )
                self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))
                self.assertEqual(reviewed_state, store.state())

        with fake_memory_service():
            store = self.fixture()
            self.advance_direct_store_to(store, sidecar.EVENT_CLOSED)
            with actor(store.ctx.root, "reviewer", "independent-session"):
                store.publish(
                    sidecar.EVENT_CLOSED,
                    attempt_id="attempt-1",
                    payload={"final_state": sidecar.STATE_VERIFIED},
                )
            self.rewrite_receipt_chain_from(
                store,
                6,
                lambda receipt: receipt["payload"].__setitem__(
                    "final_state", sidecar.STATE_REJECTED
                ),
            )
            tampered = tree_bytes(store.ctx.artifact_dir)
            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                store.load()
            self.assertEqual(
                sidecar.REASON_CLOSE_FINAL_STATE_MISMATCH,
                caught.exception.reason,
            )
            with mock.patch.object(
                gate, "load_context", return_value=store.ctx
            ), mock.patch.object(
                gate, "self_healing_policy", return_value={"mode": "verify"}
            ):
                status = gate.run(
                    store.ctx.artifact_dir,
                    SimpleNamespace(self_heal_step="status"),
                )
            self.assertEqual(
                [
                    "status",
                    "exit_code",
                    "blocking_reasons",
                    "artifact_paths",
                    "next_action",
                ],
                list(status),
            )
            self.assertEqual("blocked", status["status"])
            self.assertEqual(2, status["exit_code"])
            self.assertEqual(
                [sidecar.REASON_CLOSE_FINAL_STATE_MISMATCH],
                status["blocking_reasons"],
            )
            self.assertEqual(tampered, tree_bytes(store.ctx.artifact_dir))

    def test_memory_failure_reasons_are_distinct_and_leave_sidecar_unchanged(self) -> None:
        cases = (
            ("prepare", sidecar.REASON_MEMORY_PREPARE),
            ("finalize", sidecar.REASON_MEMORY_FINALIZE),
            ("verify", sidecar.REASON_MEMORY_VERIFY),
        )
        for phase, expected_reason in cases:
            with self.subTest(phase=phase), fake_memory_service() as memory:
                store = self.fixture()
                self.publish_triage(store)
                before = tree_bytes(store.dir)
                verify_patch = mock.patch.object(
                    sidecar, "_memory_verify", wraps=sidecar._memory_verify
                )
                if phase == "prepare":
                    memory.post_success_false = True
                elif phase == "finalize":
                    memory.put_success_false = True
                else:
                    verify_patch = mock.patch.object(
                        sidecar,
                        "_memory_verify",
                        side_effect=rg.RoleGovernanceError("injected verify failure"),
                    )
                with verify_patch:
                    with actor(store.ctx.root, "reviewer", "triage-session"):
                        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                            store.publish(
                                sidecar.EVENT_HANDOFF,
                                attempt_id="attempt-1",
                                payload={"healing_eligible": True},
                            )
                self.assertEqual(expected_reason, caught.exception.reason)
                self.assertEqual(before, tree_bytes(store.dir))
                self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())
                self.assertEqual(1, store.load()["sequence"])

    def test_memory_adapter_mapping_shape_fails_before_local_publication(self) -> None:
        cases = (
            ("prepare", "_memory_prepare", sidecar.REASON_MEMORY_PREPARE),
            ("finalize", "_memory_finalize", sidecar.REASON_MEMORY_FINALIZE),
        )
        for phase, target, reason in cases:
            with self.subTest(phase=phase), fake_memory_service():
                store = self.fixture()
                self.publish_triage(store)
                before = tree_bytes(store.dir)
                malformed = {1: "non-string Memory key"}
                with mock.patch.object(
                    sidecar,
                    target,
                    return_value=malformed,
                ), actor(store.ctx.root, "reviewer", "triage-session"):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_HANDOFF,
                            attempt_id="attempt-1",
                            payload={"healing_eligible": True},
                        )
                self.assertEqual(reason, caught.exception.reason)
                self.assertEqual(before, tree_bytes(store.dir))
                self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())

    def test_load_and_state_reject_structural_and_cryptographic_tampering(self) -> None:
        def chain_value(store: sidecar.SelfHealSidecar) -> dict[str, Any]:
            return read_json(store.chain_path)

        cases: tuple[
            tuple[str, Callable[[sidecar.SelfHealSidecar], None], str, bool], ...
        ] = (
            (
                "chain_schema",
                lambda store: self._mutate_chain(
                    store, lambda chain: chain.__setitem__("schema", "bad")
                ),
                "chain schema must be",
                False,
            ),
            (
                "lineage_ref",
                lambda store: self._mutate_chain(
                    store,
                    lambda chain: chain["lineage_ref"].__setitem__(
                        "namespace", "project:attacker-controlled"
                    ),
                ),
                "lineage_ref does not match the active lineage",
                True,
            ),
            (
                "sequence_gap",
                lambda store: self._mutate_chain(
                    store, lambda chain: chain["entries"][1].__setitem__("sequence", 3)
                ),
                "entry sequence is not continuous",
                False,
            ),
            (
                "duplicate_receipt",
                lambda store: self._mutate_chain(
                    store,
                    lambda chain: chain["entries"][1].__setitem__(
                        "receipt", chain["entries"][0]["receipt"]
                    ),
                ),
                "duplicate self-heal receipt reference",
                False,
            ),
            (
                "missing_receipt",
                lambda store: (store.dir / chain_value(store)["entries"][1]["receipt"]).unlink(),
                "self-heal receipt is missing",
                False,
            ),
            (
                "receipt_hash",
                self._tamper_receipt_payload,
                "self-heal receipt hash mismatch",
                True,
            ),
            (
                "parent_link",
                self._tamper_parent_link_consistently,
                "self-heal parent receipt mismatch",
                False,
            ),
            (
                "chain_head",
                lambda store: self._mutate_chain(
                    store, lambda chain: chain.__setitem__("head_sha256", "0" * 64)
                ),
                "chain head does not match",
                False,
            ),
            (
                "entry_receipt_mismatch",
                lambda store: self._mutate_chain(
                    store,
                    lambda chain: chain["entries"][1].__setitem__("recorded_at", "tampered"),
                ),
                "chain entry recorded_at mismatch",
                False,
            ),
            (
                "orphan_receipt",
                lambda store: write_json(store.dir / "999-orphan.json", {}),
                "receipt inventory mismatch",
                False,
            ),
            (
                "empty_memory_binding",
                self._remove_memory_binding_consistently,
                "Memory binding schema is invalid",
                True,
            ),
            (
                "unsupported_drift_marker",
                self._forge_drift_marker_without_main_chain_change,
                "drift record is not supported",
                True,
            ),
        )
        for name, mutate, expected, via_state in cases:
            with self.subTest(tamper=name), fake_memory_service():
                store = self.valid_two_receipt_store()
                mutate(store)
                self.assert_integrity_failure(store, expected, via_state=via_state)

    def test_cli_status_maps_unhashable_sidecar_values_without_traceback(self) -> None:
        """Every JSON shape reaches the frozen five-field error envelope."""

        def mutate_entry_event(fixture: ImportedFixture) -> None:
            chain_path = fixture.artifact / "00_self_healing" / "chain.json"
            chain = read_json(chain_path)
            chain["entries"][0]["event"] = []
            write_json(chain_path, chain)

        def mutate_actor_runtime(fixture: ImportedFixture) -> None:
            sidecar_dir = fixture.artifact / "00_self_healing"
            chain = read_json(sidecar_dir / "chain.json")
            receipt_path = sidecar_dir / str(chain["entries"][0]["receipt"])
            receipt = read_json(receipt_path)
            receipt["actor"]["runtime"] = []
            write_json(receipt_path, receipt)

        def closed_fixture(label: str) -> ImportedFixture:
            fixture = self.verified_cli_fixture(label)
            closed = fixture.run_self_heal(
                "--self-heal-step",
                "close",
                role="reviewer",
                session="independent-review-session",
            )
            self.assertEqual(0, closed.returncode, closed.stdout + closed.stderr)
            return fixture

        def mutate_close_final_state(fixture: ImportedFixture) -> None:
            sidecar_dir = fixture.artifact / "00_self_healing"
            chain = read_json(sidecar_dir / "chain.json")
            receipt_path = sidecar_dir / str(chain["entries"][-1]["receipt"])
            receipt = read_json(receipt_path)
            receipt["payload"]["final_state"] = []
            write_json(receipt_path, receipt)

        cases = (
            (
                "entry-event-list",
                self.triaged_cli_fixture,
                mutate_entry_event,
                sidecar.REASON_INTEGRITY,
            ),
            (
                "actor-runtime-list",
                self.triaged_cli_fixture,
                mutate_actor_runtime,
                sidecar.REASON_INTEGRITY,
            ),
            (
                "close-final-state-list",
                closed_fixture,
                mutate_close_final_state,
                sidecar.REASON_CLOSE_FINAL_STATE_MISMATCH,
            ),
        )
        for label, build, corrupt, reason in cases:
            with self.subTest(corruption=label):
                fixture = build(f"unhashable-sidecar-{label}")
                corrupt(fixture)
                artifact_before = tree_bytes(fixture.artifact)
                status = fixture.run_self_heal(
                    "--self-heal-step",
                    "status",
                    role="reviewer",
                    session="reviewer-session",
                )
                combined = status.stdout + status.stderr
                self.assertNotIn("Traceback", combined)
                self.assertEqual(2, status.returncode, combined)
                parsed = gate_result(status)
                self.assertEqual(
                    [
                        "status",
                        "exit_code",
                        "blocking_reasons",
                        "artifact_paths",
                        "next_action",
                    ],
                    list(parsed),
                )
                self.assertEqual("blocked", parsed["status"])
                self.assertEqual(2, parsed["exit_code"])
                self.assertEqual([reason], parsed["blocking_reasons"])
                self.assertEqual(artifact_before, tree_bytes(fixture.artifact))

    def test_cli_status_maps_oversized_json_integers_without_traceback(self) -> None:
        """Interpreter integer limits are integrity failures, never raw errors."""

        oversized = "9" * 5000

        def replace_first_sequence(path: Path) -> None:
            body = path.read_text(encoding="utf-8")
            marker = '"sequence": '
            start = body.index(marker) + len(marker)
            end = start
            while end < len(body) and body[end].isdigit():
                end += 1
            path.write_text(body[:start] + oversized + body[end:], encoding="utf-8")

        def corrupt_main(fixture: ImportedFixture) -> None:
            replace_first_sequence(
                fixture.artifact / "00_role_evidence" / "chain.json"
            )

        def corrupt_sidecar_chain(fixture: ImportedFixture) -> None:
            replace_first_sequence(
                fixture.artifact / "00_self_healing" / "chain.json"
            )

        def corrupt_sidecar_receipt(fixture: ImportedFixture) -> None:
            sidecar_dir = fixture.artifact / "00_self_healing"
            chain = read_json(sidecar_dir / "chain.json")
            replace_first_sequence(sidecar_dir / str(chain["entries"][0]["receipt"]))

        cases = (
            ("main-chain", corrupt_main, 4, "invalidated", sidecar.REASON_DRIFT),
            (
                "sidecar-chain",
                corrupt_sidecar_chain,
                2,
                "blocked",
                sidecar.REASON_INTEGRITY,
            ),
            (
                "sidecar-receipt",
                corrupt_sidecar_receipt,
                2,
                "blocked",
                sidecar.REASON_INTEGRITY,
            ),
        )
        for label, corrupt, expected_exit, expected_status, reason in cases:
            with self.subTest(corruption=label):
                fixture = self.triaged_cli_fixture(f"oversized-json-{label}")
                corrupt(fixture)
                artifact_before = tree_bytes(fixture.artifact)
                for call in range(2):
                    status = fixture.run_self_heal(
                        "--self-heal-step",
                        "status",
                        role="reviewer",
                        session="reviewer-session",
                    )
                    combined = status.stdout + status.stderr
                    self.assertNotIn("Traceback", combined)
                    self.assertEqual(expected_exit, status.returncode, combined)
                    parsed = gate_result(status)
                    self.assertEqual(
                        [
                            "status",
                            "exit_code",
                            "blocking_reasons",
                            "artifact_paths",
                            "next_action",
                        ],
                        list(parsed),
                    )
                    self.assertEqual(expected_status, parsed["status"])
                    self.assertEqual(expected_exit, parsed["exit_code"])
                    self.assertEqual([reason], parsed["blocking_reasons"])
                    self.assertEqual(
                        artifact_before,
                        tree_bytes(fixture.artifact),
                        f"{label} status call {call + 1} changed evidence",
                    )

    def test_cli_status_maps_excessively_nested_json_without_traceback(self) -> None:
        """Parser recursion limits retain the same stable CLI classifications."""

        nested = "[" * 10000 + "0" + "]" * 10000

        def corrupt_main(fixture: ImportedFixture) -> None:
            (fixture.artifact / "00_role_evidence" / "chain.json").write_text(
                nested,
                encoding="utf-8",
            )

        def corrupt_main_receipt(fixture: ImportedFixture) -> None:
            evidence = fixture.artifact / "00_role_evidence"
            chain = read_json(evidence / "chain.json")
            receipt_path = fixture.root / chain["latest_receipts"][
                "human_acceptance"
            ]
            receipt_path.write_text(nested, encoding="utf-8")

        def corrupt_sidecar_chain(fixture: ImportedFixture) -> None:
            (fixture.artifact / "00_self_healing" / "chain.json").write_text(
                nested,
                encoding="utf-8",
            )

        def corrupt_sidecar_receipt(fixture: ImportedFixture) -> None:
            sidecar_dir = fixture.artifact / "00_self_healing"
            chain = read_json(sidecar_dir / "chain.json")
            (sidecar_dir / str(chain["entries"][0]["receipt"])).write_text(
                nested,
                encoding="utf-8",
            )

        cases = (
            ("main-chain", corrupt_main, 4, "invalidated", sidecar.REASON_DRIFT),
            (
                "main-receipt",
                corrupt_main_receipt,
                4,
                "invalidated",
                sidecar.REASON_DRIFT,
            ),
            (
                "sidecar-chain",
                corrupt_sidecar_chain,
                2,
                "blocked",
                sidecar.REASON_INTEGRITY,
            ),
            (
                "sidecar-receipt",
                corrupt_sidecar_receipt,
                2,
                "blocked",
                sidecar.REASON_INTEGRITY,
            ),
        )
        for label, corrupt, expected_exit, expected_status, reason in cases:
            with self.subTest(corruption=label):
                fixture = self.triaged_cli_fixture(f"nested-json-{label}")
                corrupt(fixture)
                artifact_before = tree_bytes(fixture.artifact)
                for call in range(2):
                    status = fixture.run_self_heal(
                        "--self-heal-step",
                        "status",
                        role="reviewer",
                        session="reviewer-session",
                    )
                    combined = status.stdout + status.stderr
                    self.assertNotIn("Traceback", combined)
                    self.assertEqual(expected_exit, status.returncode, combined)
                    parsed = gate_result(status)
                    self.assertEqual(
                        [
                            "status",
                            "exit_code",
                            "blocking_reasons",
                            "artifact_paths",
                            "next_action",
                        ],
                        list(parsed),
                    )
                    self.assertEqual(expected_status, parsed["status"])
                    self.assertEqual(expected_exit, parsed["exit_code"])
                    self.assertEqual([reason], parsed["blocking_reasons"])
                    self.assertEqual(
                        artifact_before,
                        tree_bytes(fixture.artifact),
                        f"{label} status call {call + 1} changed evidence",
                    )

    def test_cli_status_maps_parsed_but_too_deep_receipt_values(self) -> None:
        """Post-parse hashing/verification recursion also stays in the envelope."""

        nested: object = 0
        for _depth in range(600):
            nested = [nested]

        def corrupt_main_receipt(fixture: ImportedFixture) -> None:
            evidence = fixture.artifact / "00_role_evidence"
            chain = read_json(evidence / "chain.json")
            receipt_path = fixture.root / chain["latest_receipts"][
                "human_acceptance"
            ]
            receipt = read_json(receipt_path)
            receipt["memory"]["malformed_nested"] = nested
            write_json(receipt_path, receipt)

        def corrupt_sidecar_receipt(fixture: ImportedFixture) -> None:
            sidecar_dir = fixture.artifact / "00_self_healing"
            chain = read_json(sidecar_dir / "chain.json")
            receipt_path = sidecar_dir / str(chain["entries"][0]["receipt"])
            receipt = read_json(receipt_path)
            receipt["memory"]["malformed_nested"] = nested
            write_json(receipt_path, receipt)

        cases = (
            (
                "main-receipt",
                corrupt_main_receipt,
                4,
                "invalidated",
                sidecar.REASON_DRIFT,
            ),
            (
                "sidecar-receipt",
                corrupt_sidecar_receipt,
                2,
                "blocked",
                sidecar.REASON_INTEGRITY,
            ),
        )
        for label, corrupt, expected_exit, expected_status, reason in cases:
            with self.subTest(corruption=label):
                fixture = self.triaged_cli_fixture(f"parsed-deep-{label}")
                corrupt(fixture)
                artifact_before = tree_bytes(fixture.artifact)
                status = fixture.run_self_heal(
                    "--self-heal-step",
                    "status",
                    role="reviewer",
                    session="reviewer-session",
                )
                combined = status.stdout + status.stderr
                self.assertNotIn("Traceback", combined)
                self.assertEqual(expected_exit, status.returncode, combined)
                parsed = gate_result(status)
                self.assertEqual(expected_status, parsed["status"])
                self.assertEqual(expected_exit, parsed["exit_code"])
                self.assertEqual([reason], parsed["blocking_reasons"])
                self.assertEqual(artifact_before, tree_bytes(fixture.artifact))

    def test_load_replays_paired_and_fresh_session_constraints(self) -> None:
        with self.subTest(edge="paired"), fake_memory_service():
            store = self.valid_two_receipt_store()
            self._rewrite_latest_session(store, "wrong-reviewer-session")
            self.assert_integrity_failure(store, "session pairing is invalid", via_state=True)

        with self.subTest(edge="fresh"), fake_memory_service():
            store = self.fixture()
            self.publish_triage(store)
            self.publish_handoff(store)
            with actor(store.ctx.root, "implementer", "healer-session"):
                store.publish(
                    sidecar.EVENT_HEALER_ACCEPT,
                    attempt_id="attempt-1",
                    payload={"accepted": True},
                )
                store.publish(
                    sidecar.EVENT_HEALER_HANDOFF,
                    attempt_id="attempt-1",
                    payload={"candidate": "verified"},
                )
            with actor(store.ctx.root, "reviewer", "independent-session"):
                store.publish(
                    sidecar.EVENT_REVIEW,
                    attempt_id="attempt-1",
                    payload={"outcome": sidecar.STATE_VERIFIED},
                    resulting_state=sidecar.STATE_VERIFIED,
                )
            self._rewrite_latest_session(store, "triage-session")
            self.assert_integrity_failure(
                store, "fresh-session constraint is invalid", via_state=True
            )

    def test_optional_session_allows_empty_pair_but_blocks_unprovable_freshness(
        self,
    ) -> None:
        with fake_memory_service():
            store = self.fixture(session_id_required=False)
            with actor(store.ctx.root, "reviewer", ""):
                triage = store.publish(
                    sidecar.EVENT_TRIAGE,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )
                handoff = store.publish(
                    sidecar.EVENT_HANDOFF,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )

            self.assertEqual("", triage["actor"]["session_id"])
            self.assertEqual("", handoff["actor"]["session_id"])
            self.assertEqual(2, store.load()["sequence"])
            self.assertEqual(sidecar.STATE_AWAITING_HEALER, store.state())
            before = tree_bytes(store.dir)

            for session in ("", "healer-session"):
                with self.subTest(fresh_session=session or "empty"), mock.patch.object(
                    sidecar,
                    "_memory_prepare",
                    side_effect=AssertionError("unprovable fresh edge reached Memory"),
                ):
                    with actor(store.ctx.root, "implementer", session):
                        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                            store.publish(
                                sidecar.EVENT_HEALER_ACCEPT,
                                attempt_id="attempt-1",
                                payload={"accepted": True},
                            )
                self.assertEqual(sidecar.REASON_SAME_SESSION, caught.exception.reason)
                self.assertIn("provably fresh", str(caught.exception))
                self.assertEqual(before, tree_bytes(store.dir))

            mismatch = self.fixture(session_id_required=False)
            with actor(mismatch.ctx.root, "reviewer", ""):
                mismatch.publish(
                    sidecar.EVENT_TRIAGE,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )
            with actor(mismatch.ctx.root, "reviewer", "later-session"):
                with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                    mismatch.publish(
                        sidecar.EVENT_HANDOFF,
                        attempt_id="attempt-1",
                        payload={"healing_eligible": True},
                    )
            self.assertEqual(sidecar.REASON_SESSION_MISMATCH, caught.exception.reason)
            self.assertEqual(1, mismatch.load()["sequence"])

    def test_real_kill_orphan_is_reconciled_once_without_main_chain_write(self) -> None:
        with fake_memory_service() as memory:
            store = self.fixture()
            self.publish_triage(store)
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)
            indexed_before = store.chain_path.read_bytes()

            killed = self.kill_after_handoff_receipt(store)
            self.assertEqual(-signal.SIGKILL, killed.returncode, killed.stderr)
            orphan = store.dir / "001-self_heal_handoff.json"
            self.assertTrue(orphan.is_file())
            self.assertEqual(indexed_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))
            calls_after_crash = list(memory.calls)
            records_after_crash = copy.deepcopy(memory.records)

            reconciled = store.load()
            self.assertEqual(2, reconciled["sequence"])
            self.assertEqual(orphan.name, reconciled["entries"][-1]["receipt"])
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))
            self.assertEqual(records_after_crash, memory.records)
            self.assertEqual(
                sum(method in {"POST", "PUT"} for method, _path in calls_after_crash),
                sum(method in {"POST", "PUT"} for method, _path in memory.calls),
            )
            self.assertGreater(
                sum(method == "GET" for method, _path in memory.calls),
                sum(method == "GET" for method, _path in calls_after_crash),
            )

            reconciled_chain = store.chain_path.read_bytes()
            calls_after_reconciliation = list(memory.calls)
            self.assertEqual(reconciled, store.load())
            self.assertEqual(reconciled_chain, store.chain_path.read_bytes())
            self.assertEqual(calls_after_reconciliation, memory.calls)
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

    def test_strict_accept_and_review_orphans_exact_verify_before_index(self) -> None:
        cases = (
            (
                sidecar.EVENT_HEALER_ACCEPT,
                "implementer",
                "healer-session",
                {"accepted": True},
                None,
                sidecar.STATE_HEALING_ACTIVE,
            ),
            (
                sidecar.EVENT_REVIEW,
                "reviewer",
                "independent-session",
                {"outcome": sidecar.STATE_VERIFIED},
                sidecar.STATE_VERIFIED,
                sidecar.STATE_VERIFIED,
            ),
        )
        for event, role, session, payload, resulting, expected_state in cases:
            with self.subTest(event=event), fake_memory_service() as memory:
                store = self.fixture()
                self.advance_direct_store_to(store, event)
                sequence_before = int(store.load()["sequence"])
                role_evidence_before = tree_bytes(store.ctx.evidence_dir)
                orphan_path, _orphan_body = self.interrupt_after_receipt(
                    store,
                    event,
                    role=role,
                    session=session,
                    payload=payload,
                    resulting_state=resulting,
                )
                calls_after_crash = list(memory.calls)
                records_after_crash = copy.deepcopy(memory.records)

                reconciled = store.load()
                self.assertEqual(sequence_before + 1, reconciled["sequence"])
                self.assertEqual(expected_state, store.state())
                self.assertTrue(orphan_path.exists())
                self.assertEqual(
                    orphan_path.name,
                    reconciled["entries"][-1]["receipt"],
                )
                self.assertFalse(
                    list(
                        sidecar.attempts_dir(store.ctx, "attempt-1").glob(
                            f"unindexed_{event}_*.json"
                        )
                    )
                )
                self.assertEqual(records_after_crash, memory.records)
                self.assertEqual(
                    sum(
                        method in {"POST", "PUT"}
                        for method, _path in calls_after_crash
                    ),
                    sum(
                        method in {"POST", "PUT"}
                        for method, _path in memory.calls
                    ),
                )
                self.assertGreater(
                    sum(method == "GET" for method, _path in memory.calls),
                    sum(method == "GET" for method, _path in calls_after_crash),
                )
                self.assertEqual(
                    role_evidence_before,
                    tree_bytes(store.ctx.evidence_dir),
                )

    def test_strict_memory_requires_an_explicit_verified_result(self) -> None:
        invalid_results = (None, {}, 0, {"status": "failed"})
        for result in invalid_results:
            with self.subTest(path="live", result=repr(result)), fake_memory_service():
                store = self.fixture()
                self.publish_triage(store)
                before = tree_bytes(store.dir)
                with mock.patch.object(
                    memory_bus,
                    "verify_role_transition",
                    return_value=result,
                ), actor(store.ctx.root, "reviewer", "triage-session"):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_HANDOFF,
                            attempt_id="attempt-1",
                            payload={"healing_eligible": True},
                        )
                self.assertEqual(sidecar.REASON_MEMORY_VERIFY, caught.exception.reason)
                self.assertEqual(before, tree_bytes(store.dir))
                self.assertEqual(1, store.load()["sequence"])

            with self.subTest(path="orphan", result=repr(result)), fake_memory_service():
                store = self.fixture()
                self.publish_triage(store)
                chain_before = store.chain_path.read_bytes()
                orphan_path, _body = self.interrupt_after_receipt(
                    store,
                    sidecar.EVENT_HANDOFF,
                    role="reviewer",
                    session="triage-session",
                    payload={"healing_eligible": True},
                )
                orphan_before = orphan_path.read_bytes()
                with mock.patch.object(
                    memory_bus,
                    "verify_role_transition",
                    return_value=result,
                ):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.load()
                self.assertEqual(sidecar.REASON_INTEGRITY, caught.exception.reason)
                self.assertEqual(chain_before, store.chain_path.read_bytes())
                self.assertEqual(orphan_before, orphan_path.read_bytes())
                self.assertFalse(
                    list(
                        sidecar.attempts_dir(store.ctx, "attempt-1").glob(
                            "unindexed_self_heal_handoff_*.json"
                        )
                    )
                )

    def test_strict_memory_live_events_bind_the_exact_receipt_memory_id(self) -> None:
        cases = (
            (
                sidecar.EVENT_HANDOFF,
                "reviewer",
                "triage-session",
                {"healing_eligible": True},
                None,
                sidecar.STATE_AWAITING_HEALER,
            ),
            (
                sidecar.EVENT_HEALER_ACCEPT,
                "implementer",
                "healer-session",
                {"accepted": True},
                None,
                sidecar.STATE_HEALING_ACTIVE,
            ),
            (
                sidecar.EVENT_REVIEW,
                "reviewer",
                "independent-session",
                {"outcome": sidecar.STATE_VERIFIED},
                sidecar.STATE_VERIFIED,
                sidecar.STATE_VERIFIED,
            ),
        )
        for event, role, session, payload, resulting_state, expected_state in cases:
            for verifier_result in ("missing_id", "wrong_id", "exact_id"):
                with self.subTest(
                    event=event,
                    verifier_result=verifier_result,
                ), fake_memory_service() as memory:
                    store = self.fixture()
                    self.advance_direct_store_to(store, event)
                    sequence_before = int(store.load()["sequence"])
                    state_before = store.state()
                    sidecar_before = tree_bytes(store.dir)
                    role_evidence_before = tree_bytes(store.ctx.evidence_dir)
                    records_before = copy.deepcopy(memory.records)
                    calls_before = list(memory.calls)
                    real_verify = memory_bus.verify_role_transition
                    observed_calls: list[dict[str, Any]] = []

                    def verifier(
                        *,
                        receipt: dict[str, Any],
                        strict: bool,
                    ) -> dict[str, Any]:
                        result = dict(real_verify(receipt=receipt, strict=strict))
                        expected_id = str(receipt["memory"]["memory_id"])
                        observed_calls.append(
                            {
                                "event": receipt["event"],
                                "strict": strict,
                                "expected_id": expected_id,
                                "verified_id": result.get("memory_id"),
                            }
                        )
                        if verifier_result == "missing_id":
                            result.pop("memory_id", None)
                        elif verifier_result == "wrong_id":
                            result["memory_id"] = "0" * 64
                        return result

                    with mock.patch.object(
                        memory_bus,
                        "verify_role_transition",
                        side_effect=verifier,
                    ), actor(store.ctx.root, role, session):
                        if verifier_result == "exact_id":
                            receipt = store.publish(
                                event,
                                attempt_id="attempt-1",
                                payload=payload,
                                resulting_state=resulting_state,
                            )
                        else:
                            with self.assertRaises(
                                sidecar.SelfHealSidecarError
                            ) as caught:
                                store.publish(
                                    event,
                                    attempt_id="attempt-1",
                                    payload=payload,
                                    resulting_state=resulting_state,
                                )

                    self.assertEqual(1, len(observed_calls))
                    observed = observed_calls[0]
                    self.assertEqual(event, observed["event"])
                    self.assertIs(True, observed["strict"])
                    self.assertTrue(observed["expected_id"])
                    self.assertEqual(
                        observed["expected_id"],
                        observed["verified_id"],
                    )
                    self.assertEqual(
                        role_evidence_before,
                        tree_bytes(store.ctx.evidence_dir),
                    )
                    self.assertEqual(
                        sum(
                            method == "POST" for method, _path in calls_before
                        )
                        + 1,
                        sum(method == "POST" for method, _path in memory.calls),
                    )
                    self.assertEqual(
                        sum(method == "PUT" for method, _path in calls_before) + 1,
                        sum(method == "PUT" for method, _path in memory.calls),
                    )
                    self.assertEqual(len(records_before) + 1, len(memory.records))

                    if verifier_result == "exact_id":
                        self.assertEqual(
                            observed["expected_id"],
                            receipt["memory"]["memory_id"],
                        )
                        self.assertEqual(sequence_before + 1, store.load()["sequence"])
                        self.assertEqual(expected_state, store.state())
                    else:
                        self.assertEqual(
                            sidecar.REASON_MEMORY_VERIFY,
                            caught.exception.reason,
                        )
                        self.assertEqual(sequence_before, store.load()["sequence"])
                        self.assertEqual(state_before, store.state())
                        self.assertEqual(sidecar_before, tree_bytes(store.dir))

    def test_strict_memory_orphans_bind_the_exact_receipt_memory_id(self) -> None:
        cases = (
            (
                sidecar.EVENT_HANDOFF,
                "reviewer",
                "triage-session",
                {"healing_eligible": True},
                None,
                sidecar.STATE_AWAITING_HEALER,
            ),
            (
                sidecar.EVENT_HEALER_ACCEPT,
                "implementer",
                "healer-session",
                {"accepted": True},
                None,
                sidecar.STATE_HEALING_ACTIVE,
            ),
            (
                sidecar.EVENT_REVIEW,
                "reviewer",
                "independent-session",
                {"outcome": sidecar.STATE_VERIFIED},
                sidecar.STATE_VERIFIED,
                sidecar.STATE_VERIFIED,
            ),
        )
        for event, role, session, payload, resulting_state, expected_state in cases:
            for verifier_result in ("missing_id", "wrong_id", "exact_id"):
                with self.subTest(
                    event=event,
                    verifier_result=verifier_result,
                ), fake_memory_service() as memory:
                    store = self.fixture()
                    self.advance_direct_store_to(store, event)
                    sequence_before = int(store.load()["sequence"])
                    state_before = store.state()
                    chain_before = store.chain_path.read_bytes()
                    role_evidence_before = tree_bytes(store.ctx.evidence_dir)
                    orphan_path, orphan_body = self.interrupt_after_receipt(
                        store,
                        event,
                        role=role,
                        session=session,
                        payload=payload,
                        resulting_state=resulting_state,
                    )
                    sidecar_after_crash = tree_bytes(store.dir)
                    records_after_crash = copy.deepcopy(memory.records)
                    calls_after_crash = list(memory.calls)
                    real_verify = memory_bus.verify_role_transition
                    observed_calls: list[dict[str, Any]] = []

                    def verifier(
                        *,
                        receipt: dict[str, Any],
                        strict: bool,
                    ) -> dict[str, Any]:
                        result = dict(real_verify(receipt=receipt, strict=strict))
                        expected_id = str(receipt["memory"]["memory_id"])
                        observed_calls.append(
                            {
                                "event": receipt["event"],
                                "strict": strict,
                                "expected_id": expected_id,
                                "verified_id": result.get("memory_id"),
                            }
                        )
                        if verifier_result == "missing_id":
                            result.pop("memory_id", None)
                        elif verifier_result == "wrong_id":
                            result["memory_id"] = "0" * 64
                        return result

                    with mock.patch.object(
                        memory_bus,
                        "verify_role_transition",
                        side_effect=verifier,
                    ), mock.patch.object(
                        gate,
                        "load_context",
                        return_value=store.ctx,
                    ), mock.patch.object(
                        gate,
                        "self_healing_policy",
                        return_value={"mode": "verify"},
                    ):
                        status = gate.run(
                            store.ctx.artifact_dir,
                            SimpleNamespace(self_heal_step="status"),
                        )

                    self.assertEqual(1, len(observed_calls))
                    observed = observed_calls[0]
                    self.assertEqual(event, observed["event"])
                    self.assertIs(True, observed["strict"])
                    self.assertTrue(observed["expected_id"])
                    self.assertEqual(
                        observed["expected_id"],
                        observed["verified_id"],
                    )
                    self.assertEqual(records_after_crash, memory.records)
                    self.assertEqual(
                        sum(
                            method in {"POST", "PUT"}
                            for method, _path in calls_after_crash
                        ),
                        sum(
                            method in {"POST", "PUT"}
                            for method, _path in memory.calls
                        ),
                    )
                    self.assertEqual(
                        sum(method == "GET" for method, _path in calls_after_crash)
                        + 1,
                        sum(method == "GET" for method, _path in memory.calls),
                    )
                    self.assertEqual(
                        role_evidence_before,
                        tree_bytes(store.ctx.evidence_dir),
                    )

                    if verifier_result == "exact_id":
                        expected_status = (
                            "healing_verified"
                            if event == sidecar.EVENT_REVIEW
                            else "healing_active"
                        )
                        self.assertEqual(expected_status, status["status"])
                        self.assertEqual(0, status["exit_code"])
                        self.assertEqual([], status["blocking_reasons"])
                        reconciled = store.load()
                        self.assertEqual(sequence_before + 1, reconciled["sequence"])
                        self.assertEqual(expected_state, store.state())
                        self.assertEqual(
                            orphan_path.name,
                            reconciled["entries"][-1]["receipt"],
                        )
                        self.assertEqual(orphan_body, orphan_path.read_bytes())
                    else:
                        self.assertEqual(
                            [
                                "status",
                                "exit_code",
                                "blocking_reasons",
                                "artifact_paths",
                                "next_action",
                            ],
                            list(status),
                        )
                        self.assertEqual("blocked", status["status"])
                        self.assertEqual(2, status["exit_code"])
                        self.assertEqual(
                            [sidecar.REASON_INTEGRITY],
                            status["blocking_reasons"],
                        )
                        self.assertEqual(chain_before, store.chain_path.read_bytes())
                        self.assertEqual(orphan_body, orphan_path.read_bytes())
                        self.assertEqual(sidecar_after_crash, tree_bytes(store.dir))
                        unchanged_chain = read_json(store.chain_path)
                        self.assertEqual(sequence_before, unchanged_chain["sequence"])
                        self.assertEqual(
                            state_before,
                            unchanged_chain["entries"][-1]["state"],
                        )

    def test_tampered_crash_orphan_is_not_adopted(self) -> None:
        with fake_memory_service():
            store = self.fixture()
            self.publish_triage(store)
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)

            killed = self.kill_after_handoff_receipt(store)
            self.assertEqual(-signal.SIGKILL, killed.returncode, killed.stderr)
            orphan_path = store.dir / "001-self_heal_handoff.json"
            orphan = read_json(orphan_path)
            orphan["parent_receipt_sha256"] = "0" * 64
            orphan["receipt_sha256"] = sidecar.receipt_sha256(orphan)
            write_json(orphan_path, orphan)

            self.assert_integrity_failure(store, "parent receipt mismatch", via_state=True)
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

    def test_self_consistent_payload_tampered_orphan_fails_exact_memory_check(self) -> None:
        with fake_memory_service() as memory:
            store = self.fixture()
            self.publish_triage(store)
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)

            killed = self.kill_after_handoff_receipt(store)
            self.assertEqual(-signal.SIGKILL, killed.returncode, killed.stderr)
            orphan_path = store.dir / "001-self_heal_handoff.json"
            orphan = read_json(orphan_path)
            orphan["payload"]["healing_eligible"] = False
            orphan["payload"]["self_consistent_tamper"] = True
            orphan["receipt_sha256"] = sidecar.receipt_sha256(orphan)
            write_json(orphan_path, orphan)
            calls_before = list(memory.calls)
            records_before = copy.deepcopy(memory.records)

            self.assert_integrity_failure(
                store,
                "orphan Memory verification failed",
                via_state=True,
            )
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))
            self.assertEqual(records_before, memory.records)
            self.assertEqual(
                sum(method in {"POST", "PUT"} for method, _path in calls_before),
                sum(method in {"POST", "PUT"} for method, _path in memory.calls),
            )

    def test_forged_well_formed_memory_id_orphan_is_not_adopted(self) -> None:
        with fake_memory_service() as memory:
            store = self.fixture()
            self.publish_triage(store)
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)

            killed = self.kill_after_handoff_receipt(store)
            self.assertEqual(-signal.SIGKILL, killed.returncode, killed.stderr)
            orphan_path = store.dir / "001-self_heal_handoff.json"
            orphan = read_json(orphan_path)
            forged_id = "0" * 64
            self.assertNotEqual(forged_id, orphan["memory"]["memory_id"])
            orphan["memory"]["memory_id"] = forged_id
            orphan["receipt_sha256"] = sidecar.receipt_sha256(orphan)
            write_json(orphan_path, orphan)
            calls_before = list(memory.calls)
            records_before = copy.deepcopy(memory.records)

            self.assert_integrity_failure(
                store,
                "orphan Memory verification failed",
                via_state=True,
            )
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))
            self.assertEqual(records_before, memory.records)
            self.assertEqual(
                sum(method in {"POST", "PUT"} for method, _path in calls_before),
                sum(method in {"POST", "PUT"} for method, _path in memory.calls),
            )

    def test_best_effort_anchored_orphan_is_quarantined_then_retried(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        indexed_before = store.load()
        chain_before = store.chain_path.read_bytes()
        role_evidence_before = tree_bytes(store.ctx.evidence_dir)
        orphan_path, orphan_body = self.interrupt_after_receipt(
            store,
            sidecar.EVENT_HANDOFF,
            role="reviewer",
            session="triage-session",
            payload={"healing_eligible": True},
        )

        self.assertEqual(indexed_before, store.load())
        self.assertFalse(orphan_path.exists())
        quarantine = list(
            sidecar.attempts_dir(store.ctx, "attempt-1").glob(
                "unindexed_self_heal_handoff_*.json"
            )
        )
        self.assertEqual(1, len(quarantine))
        self.assertEqual(orphan_body, quarantine[0].read_bytes())
        self.assertEqual(chain_before, store.chain_path.read_bytes())
        self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

        with actor(store.ctx.root, "reviewer", "triage-session"):
            retried = store.publish(
                sidecar.EVENT_HANDOFF,
                attempt_id="attempt-1",
                payload={"healing_eligible": True},
            )
        self.assertEqual(sidecar.STATE_AWAITING_HEALER, store.state())
        self.assertEqual(sidecar.EVENT_HANDOFF, retried["event"])
        self.assertEqual(orphan_body, quarantine[0].read_bytes())
        self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

    def test_unanchored_triage_and_healer_handoff_orphans_quarantine_and_retry(
        self,
    ) -> None:
        cases = (
            (
                sidecar.EVENT_TRIAGE,
                "reviewer",
                "triage-session",
                {"healing_eligible": True},
                None,
                sidecar.STATE_NONE,
                sidecar.STATE_TRIAGE_RECORDED,
            ),
            (
                sidecar.EVENT_HEALER_HANDOFF,
                "implementer",
                "healer-session",
                {"candidate": "verified"},
                None,
                sidecar.STATE_HEALING_ACTIVE,
                sidecar.STATE_AWAITING_REVIEW,
            ),
        )
        for event, role, session, payload, resulting, before_state, after_state in cases:
            with self.subTest(event=event), fake_memory_service():
                store = self.fixture()
                self.advance_direct_store_to(store, event)
                indexed_before = store.load()
                chain_before = (
                    store.chain_path.read_bytes() if store.chain_path.exists() else None
                )
                role_evidence_before = tree_bytes(store.ctx.evidence_dir)
                orphan_path, orphan_body = self.interrupt_after_receipt(
                    store,
                    event,
                    role=role,
                    session=session,
                    payload=payload,
                    resulting_state=resulting,
                )

                self.assertEqual(before_state, store.state())
                self.assertEqual(indexed_before, store.load())
                self.assertFalse(orphan_path.exists())
                quarantine = list(
                    sidecar.attempts_dir(store.ctx, "attempt-1").glob(
                        f"unindexed_{event}_*.json"
                    )
                )
                self.assertEqual(1, len(quarantine))
                self.assertEqual(orphan_body, quarantine[0].read_bytes())
                self.assertEqual(
                    chain_before,
                    store.chain_path.read_bytes() if store.chain_path.exists() else None,
                )
                self.assertEqual(
                    role_evidence_before,
                    tree_bytes(store.ctx.evidence_dir),
                )

                with actor(store.ctx.root, role, session):
                    store.publish(
                        event,
                        attempt_id="attempt-1",
                        payload=payload,
                        resulting_state=resulting,
                    )
                self.assertEqual(after_state, store.state())
                self.assertEqual(orphan_body, quarantine[0].read_bytes())
                self.assertEqual(
                    role_evidence_before,
                    tree_bytes(store.ctx.evidence_dir),
                )

    def test_close_orphan_status_then_resume_then_close_retry_is_reachable(self) -> None:
        fixture = self.verified_cli_fixture(
            "close-orphan-quarantine",
            mode="apply_with_approval",
        )
        # This CLI fixture owns a real deterministic lineage ID; the lightweight
        # unit-fixture override used by setUp would intentionally differ.
        self.lineage_patch.stop()
        ctx = rg.load_context(fixture.artifact)
        policy = self_healing_policy(ctx.config)
        store = sidecar.SelfHealSidecar(ctx)
        self.assertEqual(sidecar.STATE_VERIFIED, store.state())
        attempt_id = store.attempt_id()
        role_evidence_before = tree_bytes(ctx.evidence_dir)
        implementation_before = fixture.implementation.read_bytes()
        receipt_name = f"{store.load()['sequence']:03d}-{sidecar.EVENT_CLOSED}.json"
        original_atomic = sidecar._atomic_bytes

        def interrupt_close_receipt(
            path: Path,
            body: bytes,
            *,
            replace: bool,
            mode: int = 0o600,
        ) -> None:
            original_atomic(path, body, replace=replace, mode=mode)
            if Path(path).name == receipt_name:
                raise RuntimeError("simulated interruption after close receipt durability")

        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ), mock.patch.object(
            sidecar,
            "_atomic_bytes",
            side_effect=interrupt_close_receipt,
        ):
            with self.assertRaisesRegex(RuntimeError, "close receipt durability"):
                gate.step_close(
                    ctx,
                    policy,
                    SimpleNamespace(human_approval="fixture-owner"),
                )

        repaired = fixture.implementation.read_bytes()
        self.assertNotEqual(implementation_before, repaired)
        orphan_path = store.dir / receipt_name
        orphan_body = orphan_path.read_bytes()
        attempt_path = sidecar.attempts_dir(ctx, attempt_id)
        journal = read_json(attempt_path / "apply_journal.json")
        self.assertEqual("applied", journal["state"])
        self.assertEqual(role_evidence_before, tree_bytes(ctx.evidence_dir))

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="independent-review-session",
        )
        self.assertEqual(0, status.returncode, status.stdout + status.stderr)
        self.assertEqual("healing_verified", gate_result(status)["status"])
        self.assertEqual(sidecar.STATE_VERIFIED, store.state())
        self.assertFalse(orphan_path.exists())
        quarantine = list(
            attempt_path.glob("unindexed_attempt_closed_*.json")
        )
        self.assertEqual(1, len(quarantine))
        self.assertEqual(orphan_body, quarantine[0].read_bytes())

        resumed = fixture.run_self_heal(
            "--self-heal-step",
            "resume",
            role="reviewer",
            session="independent-review-session",
        )
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertEqual(implementation_before, fixture.implementation.read_bytes())
        self.assertEqual(
            "rolled_back",
            read_json(attempt_path / "apply_journal.json")["state"],
        )
        self.assertEqual(sidecar.STATE_VERIFIED, store.state())

        retried = fixture.run_self_heal(
            "--self-heal-step",
            "close",
            "--human-approval",
            "fixture-owner",
            role="reviewer",
            session="independent-review-session",
        )
        self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)
        self.assertEqual("healing_verified", gate_result(retried)["status"])
        self.assertEqual(sidecar.STATE_CLOSED, store.state())
        self.assertEqual(repaired, fixture.implementation.read_bytes())
        self.assertEqual(orphan_body, quarantine[0].read_bytes())
        self.assertEqual(role_evidence_before, tree_bytes(ctx.evidence_dir))

    def test_malformed_or_multiple_unindexed_evidence_fails_closed(self) -> None:
        with self.subTest(case="malformed_quarantine"), fake_memory_service():
            store = self.fixture()
            self.publish_triage(store)
            attempt_path = sidecar.attempts_dir(store.ctx, "attempt-1")
            attempt_path.mkdir(parents=True)
            malformed = attempt_path / "unindexed_self_heal_handoff_not-a-hash.json"
            write_json(malformed, {"event": sidecar.EVENT_HANDOFF})
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)
            self.assert_integrity_failure(
                store,
                "malformed self-heal unindexed receipt name",
                via_state=True,
            )
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

        with self.subTest(case="tampered_quarantine"), fake_memory_service():
            store = self.fixture(memory_mode="best_effort")
            self.publish_triage(store)
            self.interrupt_after_receipt(
                store,
                sidecar.EVENT_HANDOFF,
                role="reviewer",
                session="triage-session",
                payload={"healing_eligible": True},
            )
            self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())
            quarantine = next(
                sidecar.attempts_dir(store.ctx, "attempt-1").glob(
                    "unindexed_self_heal_handoff_*.json"
                )
            )
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)
            quarantine.write_bytes(quarantine.read_bytes() + b"\n")
            self.assert_integrity_failure(
                store,
                "unindexed receipt content hash mismatch",
                via_state=True,
            )
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

        with self.subTest(case="multiple_root_orphans"), fake_memory_service():
            store = self.fixture()
            self.publish_triage(store)
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)
            first_path, first_body = self.interrupt_after_receipt(
                store,
                sidecar.EVENT_HANDOFF,
                role="reviewer",
                session="triage-session",
                payload={"healing_eligible": True},
            )
            second = store.dir / "999-unexpected.json"
            second.write_bytes(first_body)
            self.assert_integrity_failure(
                store,
                "receipt inventory mismatch",
                via_state=True,
            )
            self.assertTrue(first_path.exists())
            self.assertTrue(second.exists())
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

        with self.subTest(case="stale_unanchored_orphan"), fake_memory_service():
            store = self.fixture()
            self.publish_triage(store)
            self.publish_handoff(store)
            with actor(store.ctx.root, "implementer", "healer-session"):
                store.publish(
                    sidecar.EVENT_HEALER_ACCEPT,
                    attempt_id="attempt-1",
                    payload={"accepted": True},
                )
            chain_before = store.chain_path.read_bytes()
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)
            orphan_path, _orphan_body = self.interrupt_after_receipt(
                store,
                sidecar.EVENT_HEALER_HANDOFF,
                role="implementer",
                session="healer-session",
                payload={"candidate": "verified"},
            )
            self.advance_main_chain_validly(store)
            advanced_role_evidence = tree_bytes(store.ctx.evidence_dir)
            self.assert_integrity_failure(
                store,
                "orphan role-chain anchor is stale",
                via_state=True,
            )
            self.assertTrue(orphan_path.exists())
            self.assertEqual(chain_before, store.chain_path.read_bytes())
            self.assertNotEqual(role_evidence_before, advanced_role_evidence)
            self.assertEqual(
                advanced_role_evidence,
                tree_bytes(store.ctx.evidence_dir),
            )

    def test_quarantine_is_idempotent_after_destination_write_before_unlink(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        indexed_before = store.load()
        role_evidence_before = tree_bytes(store.ctx.evidence_dir)
        orphan_path, orphan_body = self.interrupt_after_receipt(
            store,
            sidecar.EVENT_HANDOFF,
            role="reviewer",
            session="triage-session",
            payload={"healing_eligible": True},
        )
        orphan = read_json(orphan_path)
        content_sha256 = rg.sha256_bytes(orphan_body)
        quarantine = (
            sidecar.attempts_dir(store.ctx, "attempt-1")
            / f"unindexed_{orphan['event']}_{content_sha256}.json"
        )
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        sidecar._atomic_bytes(quarantine, orphan_body, replace=False)

        self.assertEqual(indexed_before, store.load())
        self.assertFalse(orphan_path.exists())
        self.assertEqual(orphan_body, quarantine.read_bytes())
        self.assertEqual(indexed_before, store.load())
        self.assertEqual(orphan_body, quarantine.read_bytes())
        self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

    def test_live_publisher_and_orphan_reader_are_cross_process_serialized(self) -> None:
        """A live receipt/chain cut is not a crash orphan.

        The publisher pauses after its receipt is durable but before chain.json
        advances.  A second process performs the same verified load used by
        CLI status.  It must wait for the publisher's artifact-directory lock,
        not quarantine/unlink the live receipt and leave the publisher to
        publish a dangling chain entry.
        """

        with fake_memory_service():
            store = self.fixture()
            self.advance_direct_store_to(store, sidecar.EVENT_HEALER_HANDOFF)
            self.assertEqual(sidecar.STATE_HEALING_ACTIVE, store.state())
            role_evidence_before = tree_bytes(store.ctx.evidence_dir)

            ready = store.ctx.root / "publisher-receipt-durable"
            release = store.ctx.root / "release-publisher"
            loader_ready = store.ctx.root / "loader-about-to-load"
            receipt_name = "003-healer_handoff.json"
            receipt_path = store.dir / receipt_name
            context_source = f"""\
ctx = rg.GovernanceContext(
    root=Path({str(store.ctx.root)!r}),
    artifact_dir=Path({str(store.ctx.artifact_dir)!r}),
    config={store.ctx.config!r},
    policy={store.ctx.policy!r},
    profile_path=Path({str(store.ctx.profile_path)!r}),
    uc={store.ctx.uc!r},
)
sidecar.lineage_identity = lambda _artifact_dir: {{"lineage_id": "f" * 64}}
"""
            publisher_source = f"""\
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, {str(ROOT / 'scripts')!r})
import role_governance as rg
import self_heal_sidecar as sidecar

{context_source}
ready = Path({str(ready)!r})
release = Path({str(release)!r})
original_atomic = sidecar._atomic_bytes

def pause_after_receipt(path, body, *, replace, mode=0o600):
    original_atomic(path, body, replace=replace, mode=mode)
    if Path(path).name == {receipt_name!r}:
        ready.write_text("durable\\n", encoding="utf-8")
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("publisher release timed out")
            time.sleep(0.02)

sidecar._atomic_bytes = pause_after_receipt
receipt = sidecar.SelfHealSidecar(ctx).publish(
    sidecar.EVENT_HEALER_HANDOFF,
    attempt_id="attempt-1",
    payload={{"candidate": "verified"}},
)
print(json.dumps({{"event": receipt["event"]}}))
"""
            loader_source = f"""\
import json
import sys
from pathlib import Path

sys.path.insert(0, {str(ROOT / 'scripts')!r})
import role_governance as rg
import self_heal_sidecar as sidecar

{context_source}
Path({str(loader_ready)!r}).write_text("ready\\n", encoding="utf-8")
chain = sidecar.SelfHealSidecar(ctx).load()
print(json.dumps({{"sequence": chain["sequence"], "state": chain["entries"][-1]["state"]}}))
"""
            env = os.environ.copy()
            env.update(
                {
                    "BUGATE_PROJECT_ROOT": str(store.ctx.root),
                    "BUGATE_AGENT_ROLE": "implementer",
                    "BUGATE_SESSION_ID": "healer-session",
                    "BUGATE_AGENT_RUNTIME": "codex",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            publisher = subprocess.Popen(
                [sys.executable, "-c", publisher_source],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            loader: subprocess.Popen[str] | None = None
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    if publisher.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(
                    ready.exists(),
                    "publisher did not reach the durable-receipt cut: "
                    + (publisher.stderr.read() if publisher.poll() is not None else ""),
                )
                self.assertTrue(receipt_path.is_file())

                loader = subprocess.Popen(
                    [sys.executable, "-c", loader_source],
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                deadline = time.monotonic() + 10
                while not loader_ready.exists() and time.monotonic() < deadline:
                    if loader.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(
                    loader_ready.exists(),
                    "loader did not reach the immediate pre-load cut: "
                    + (loader.stderr.read() if loader.poll() is not None else ""),
                )
                self.assertFalse(release.exists())
                time.sleep(0.35)
                self.assertIsNone(
                    loader.poll(),
                    "a concurrent reader treated a live publisher as a crashed orphan",
                )
                self.assertTrue(receipt_path.is_file())
                self.assertEqual(
                    [],
                    list(
                        sidecar.attempts_dir(store.ctx, "attempt-1").glob(
                            "unindexed_healer_handoff_*.json"
                        )
                    ),
                )

                release.write_text("continue\n", encoding="utf-8")
                publisher_stdout, publisher_stderr = publisher.communicate(timeout=15)
                loader_stdout, loader_stderr = loader.communicate(timeout=15)
                self.assertEqual(0, publisher.returncode, publisher_stdout + publisher_stderr)
                self.assertEqual(0, loader.returncode, loader_stdout + loader_stderr)
                self.assertEqual(
                    {"sequence": 4, "state": sidecar.STATE_AWAITING_REVIEW},
                    json.loads(loader_stdout),
                )
            finally:
                release.touch(exist_ok=True)
                for process in (publisher, loader):
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.communicate()

            chain = store.load()
            self.assertEqual(4, chain["sequence"])
            self.assertEqual(sidecar.STATE_AWAITING_REVIEW, store.state())
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))

    def test_symlinked_sidecar_root_cannot_write_outside_artifact(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        external = self.tmp / "external-sidecar-target"
        external.mkdir()
        (external / "sentinel.txt").write_text("unchanged\n", encoding="utf-8")
        external_before = tree_bytes(external)
        role_evidence_before = tree_bytes(store.ctx.evidence_dir)
        store.dir.symlink_to(external, target_is_directory=True)

        with actor(store.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                store.publish(
                    sidecar.EVENT_TRIAGE,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )

        self.assertEqual(sidecar.REASON_INTEGRITY, caught.exception.reason)
        self.assertIn("symlink parent", str(caught.exception))
        self.assertEqual(external_before, tree_bytes(external))
        self.assertEqual(role_evidence_before, tree_bytes(store.ctx.evidence_dir))
        self.assertFalse((external / "000-triage_recorded.json").exists())
        self.assertFalse((external / "chain.json").exists())

    def test_cli_status_rejects_a_symlinked_main_chain_without_any_write(self) -> None:
        """Status validates the anchor leaf even though it skips main preflight."""

        fixture = self.verified_cli_fixture("symlinked-main-chain-status")
        main_chain = fixture.artifact / "00_role_evidence" / "chain.json"
        external_chain = self.tmp / "external-role-chain.json"
        external_chain.write_bytes(main_chain.read_bytes())
        external_before = external_chain.read_bytes()
        sidecar_root = fixture.artifact / "00_self_healing"
        sidecar_before = tree_bytes(sidecar_root)
        role_receipts_before = tree_bytes(
            fixture.artifact / "00_role_evidence" / "receipts"
        )
        main_chain.unlink()
        main_chain.symlink_to(external_chain)
        symlink_target = os.readlink(main_chain)

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="independent-review-session",
        )
        combined = status.stdout + status.stderr
        self.assertNotIn("Traceback", combined)
        self.assertEqual(2, status.returncode, combined)
        parsed = gate_result(status)
        self.assertEqual(
            [
                "status",
                "exit_code",
                "blocking_reasons",
                "artifact_paths",
                "next_action",
            ],
            list(parsed),
        )
        self.assertEqual("blocked", parsed["status"])
        self.assertEqual(2, parsed["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], parsed["blocking_reasons"])
        self.assertTrue(main_chain.is_symlink())
        self.assertEqual(symlink_target, os.readlink(main_chain))
        self.assertEqual(external_before, external_chain.read_bytes())
        self.assertEqual(sidecar_before, tree_bytes(sidecar_root))
        self.assertEqual(
            role_receipts_before,
            tree_bytes(fixture.artifact / "00_role_evidence" / "receipts"),
        )

    def test_cli_status_rejects_a_symlinked_main_receipt_before_reading_it(self) -> None:
        fixture = self.triaged_cli_fixture("symlinked-main-receipt")
        role_chain = read_json(
            fixture.artifact / "00_role_evidence" / "chain.json"
        )
        receipt = fixture.root / role_chain["latest_receipts"]["human_acceptance"]
        outside = self.tmp / "outside-role-receipt.json"
        outside.write_bytes(receipt.read_bytes())
        outside_before = outside.read_bytes()
        receipt.unlink()
        receipt.symlink_to(outside)
        link_target = os.readlink(receipt)
        artifact_before = tree_bytes(fixture.artifact)

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="reviewer-session",
        )
        combined = status.stdout + status.stderr
        self.assertNotIn("Traceback", combined)
        self.assertEqual(2, status.returncode, combined)
        parsed = gate_result(status)
        self.assertEqual(
            [
                "status",
                "exit_code",
                "blocking_reasons",
                "artifact_paths",
                "next_action",
            ],
            list(parsed),
        )
        self.assertEqual("blocked", parsed["status"])
        self.assertEqual(2, parsed["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], parsed["blocking_reasons"])
        self.assertEqual(artifact_before, tree_bytes(fixture.artifact))
        self.assertEqual(outside_before, outside.read_bytes())
        self.assertTrue(receipt.is_symlink())
        self.assertEqual(link_target, os.readlink(receipt))

    def test_cli_status_rejects_an_unknown_symlinked_sidecar_root_leaf(self) -> None:
        """Verified load inventories every direct entry, not only ``*.json``."""

        fixture = self.verified_cli_fixture("unknown-sidecar-root-leaf")
        external = self.tmp / "external-unknown-sidecar-target.bin"
        external.write_bytes(b"outside bytes remain untouched\n")
        external_before = external.read_bytes()
        sidecar_root = fixture.artifact / "00_self_healing"
        surprise = sidecar_root / "surprise.bin"
        surprise.symlink_to(external)
        symlink_target = os.readlink(surprise)
        sidecar_before = tree_bytes(sidecar_root)
        role_evidence_before = tree_bytes(fixture.artifact / "00_role_evidence")

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="independent-review-session",
        )
        combined = status.stdout + status.stderr
        self.assertNotIn("Traceback", combined)
        self.assertEqual(2, status.returncode, combined)
        parsed = gate_result(status)
        self.assertEqual(
            [
                "status",
                "exit_code",
                "blocking_reasons",
                "artifact_paths",
                "next_action",
            ],
            list(parsed),
        )
        self.assertEqual("blocked", parsed["status"])
        self.assertEqual(2, parsed["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], parsed["blocking_reasons"])
        self.assertTrue(surprise.is_symlink())
        self.assertEqual(symlink_target, os.readlink(surprise))
        self.assertEqual(external_before, external.read_bytes())
        self.assertEqual(sidecar_before, tree_bytes(sidecar_root))
        self.assertEqual(
            role_evidence_before,
            tree_bytes(fixture.artifact / "00_role_evidence"),
        )

    def test_cli_status_rejects_a_symlinked_attempt_evidence_leaf(self) -> None:
        """Every governed attempt leaf is safe before status reports health."""

        fixture = self.verified_cli_fixture("symlinked-attempt-evidence")
        attempt = (
            fixture.artifact
            / "00_self_healing"
            / "attempts"
            / fixture.sidecar_attempt_id()
        )
        baseline = attempt / "baseline.json"
        external = self.tmp / "external-baseline.json"
        external.write_bytes(baseline.read_bytes())
        external_before = external.read_bytes()
        baseline.unlink()
        baseline.symlink_to(external)
        symlink_target = os.readlink(baseline)
        sidecar_before = tree_bytes(fixture.artifact / "00_self_healing")
        role_evidence_before = tree_bytes(fixture.artifact / "00_role_evidence")

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="independent-review-session",
        )
        combined = status.stdout + status.stderr
        self.assertNotIn("Traceback", combined)
        self.assertEqual(2, status.returncode, combined)
        parsed = gate_result(status)
        self.assertEqual(
            [
                "status",
                "exit_code",
                "blocking_reasons",
                "artifact_paths",
                "next_action",
            ],
            list(parsed),
        )
        self.assertEqual("blocked", parsed["status"])
        self.assertEqual(2, parsed["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], parsed["blocking_reasons"])
        self.assertTrue(baseline.is_symlink())
        self.assertEqual(symlink_target, os.readlink(baseline))
        self.assertEqual(external_before, external.read_bytes())
        self.assertEqual(
            sidecar_before,
            tree_bytes(fixture.artifact / "00_self_healing"),
        )
        self.assertEqual(
            role_evidence_before,
            tree_bytes(fixture.artifact / "00_role_evidence"),
        )

    def test_non_directory_root_and_symlinked_attempt_parent_fail_closed(self) -> None:
        non_directory = self.fixture(memory_mode="best_effort")
        non_directory.dir.write_text("not a directory\n", encoding="utf-8")
        self.assert_integrity_failure(non_directory, "parent is not a directory")

        linked_target = self.fixture(memory_mode="best_effort")
        target_before = tree_bytes(linked_target.ctx.artifact_dir)
        root_link = self.tmp / "project-root-link"
        root_link.symlink_to(linked_target.ctx.root, target_is_directory=True)
        linked_root_ctx = rg.GovernanceContext(
            root=root_link,
            artifact_dir=root_link / linked_target.ctx.artifact_dir.relative_to(
                linked_target.ctx.root
            ),
            config=linked_target.ctx.config,
            policy=linked_target.ctx.policy,
            profile_path=root_link / "bugate.profile.yaml",
            uc=linked_target.ctx.uc,
        )
        self.assert_integrity_failure(
            sidecar.SelfHealSidecar(linked_root_ctx),
            "symlink parent",
        )
        self.assertEqual(target_before, tree_bytes(linked_target.ctx.artifact_dir))

        artifact_link = linked_target.ctx.root / "usecases" / "UC-LINKED-ARTIFACT"
        artifact_link.symlink_to(linked_target.ctx.artifact_dir, target_is_directory=True)
        linked_artifact_ctx = rg.GovernanceContext(
            root=linked_target.ctx.root,
            artifact_dir=artifact_link,
            config=linked_target.ctx.config,
            policy=linked_target.ctx.policy,
            profile_path=linked_target.ctx.profile_path,
            uc=linked_target.ctx.uc,
        )
        self.assert_integrity_failure(
            sidecar.SelfHealSidecar(linked_artifact_ctx),
            "symlink parent",
        )
        self.assertEqual(target_before, tree_bytes(linked_target.ctx.artifact_dir))

        for link_level in ("attempts", "attempt"):
            with self.subTest(link_level=link_level):
                store = self.fixture(memory_mode="best_effort")
                external = self.tmp / f"external-{link_level}-{self.counter}"
                external.mkdir()
                (external / "sentinel.txt").write_text("unchanged\n", encoding="utf-8")
                external_before = tree_bytes(external)
                store.dir.mkdir()
                attempts = store.dir / "attempts"
                if link_level == "attempts":
                    attempts.symlink_to(external, target_is_directory=True)
                else:
                    attempts.mkdir()
                    (attempts / "attempt-1").symlink_to(
                        external,
                        target_is_directory=True,
                    )

                with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                    sidecar.attempts_dir(store.ctx, "attempt-1")
                self.assertEqual(sidecar.REASON_INTEGRITY, caught.exception.reason)
                self.assertIn("symlink parent", str(caught.exception))
                self.assertEqual(external_before, tree_bytes(external))

    @staticmethod
    def _mutate_chain(
        store: sidecar.SelfHealSidecar,
        mutate: Callable[[dict[str, Any]], None],
    ) -> None:
        chain = read_json(store.chain_path)
        mutate(chain)
        write_json(store.chain_path, chain)

    @staticmethod
    def _second_receipt_path(store: sidecar.SelfHealSidecar) -> Path:
        chain = read_json(store.chain_path)
        return store.dir / str(chain["entries"][1]["receipt"])

    def _tamper_receipt_payload(self, store: sidecar.SelfHealSidecar) -> None:
        path = self._second_receipt_path(store)
        receipt = read_json(path)
        receipt["payload"]["tampered"] = True
        write_json(path, receipt)

    def _tamper_parent_link_consistently(self, store: sidecar.SelfHealSidecar) -> None:
        path = self._second_receipt_path(store)
        receipt = read_json(path)
        receipt["parent_receipt_sha256"] = "0" * 64
        receipt["receipt_sha256"] = sidecar.receipt_sha256(receipt)
        write_json(path, receipt)
        chain = read_json(store.chain_path)
        chain["entries"][1]["receipt_sha256"] = receipt["receipt_sha256"]
        chain["head_sha256"] = receipt["receipt_sha256"]
        write_json(store.chain_path, chain)

    def _remove_memory_binding_consistently(self, store: sidecar.SelfHealSidecar) -> None:
        path = self._second_receipt_path(store)
        receipt = read_json(path)
        receipt["memory"] = {}
        receipt["receipt_sha256"] = sidecar.receipt_sha256(receipt)
        write_json(path, receipt)
        chain = read_json(store.chain_path)
        chain["entries"][1]["receipt_sha256"] = receipt["receipt_sha256"]
        chain["head_sha256"] = receipt["receipt_sha256"]
        write_json(store.chain_path, chain)

    @staticmethod
    def _forge_drift_marker_without_main_chain_change(
        store: sidecar.SelfHealSidecar,
    ) -> None:
        chain = read_json(store.chain_path)
        latest = read_json(store.dir / str(chain["entries"][-1]["receipt"]))
        observed = dict(latest["role_chain_anchor"])
        observed["chain_sha256"] = "0" * 64
        chain["drift"] = {
            "detected_at": "2026-08-12T00:00:00Z",
            "expected_role_chain_anchor": latest["role_chain_anchor"],
            "observed_role_chain_anchor": observed,
            "state": sidecar.STATE_INVALIDATED,
        }
        write_json(store.chain_path, chain)

    @staticmethod
    def _rewrite_latest_session(store: sidecar.SelfHealSidecar, session_id: str) -> None:
        """Forge a self-consistent latest receipt to prove semantic replay."""

        chain = read_json(store.chain_path)
        entry = chain["entries"][-1]
        path = store.dir / str(entry["receipt"])
        receipt = read_json(path)
        receipt["actor"]["session_id"] = session_id
        if receipt["event"] in sidecar.MEMORY_ANCHORED_EVENTS:
            receipt["sidecar_transition"]["actor"]["session_id"] = session_id
            envelope = {
                "schema": sidecar.ROLE_TRANSITION_SCHEMA,
                "event": receipt["event"],
                "uc": receipt["uc"],
                "artifact_dir": receipt["artifact_dir"],
                "phase": receipt["phase"],
                "from_role": receipt["from_role"],
                "to_role": receipt["to_role"],
                "actor": receipt["actor"],
                "sidecar_transition": receipt["sidecar_transition"],
            }
            receipt["transition_sha256"] = rg.sha256_bytes(rg.canonical_json(envelope))
        receipt["receipt_sha256"] = sidecar.receipt_sha256(receipt)
        write_json(path, receipt)
        entry["receipt_sha256"] = receipt["receipt_sha256"]
        entry["session_id"] = session_id
        chain["head_sha256"] = receipt["receipt_sha256"]
        write_json(store.chain_path, chain)

    def test_preflight_publish_is_read_only_including_lifecycle_drift(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        before = tree_bytes(store.dir)
        with mock.patch.object(
            sidecar,
            "_memory_prepare",
            side_effect=AssertionError("read-only preflight reached Memory"),
        ):
            with actor(store.ctx.root, "reviewer", "triage-session"):
                validated = store.preflight_publish(
                    sidecar.EVENT_HANDOFF,
                    "attempt-1",
                )
        self.assertEqual(sidecar.STATE_AWAITING_HEALER, validated["resulting_state"])
        self.assertEqual(before, tree_bytes(store.dir))

        self.advance_main_chain_validly(store)
        before_drift = tree_bytes(store.dir)
        with mock.patch.object(
            sidecar,
            "_memory_prepare",
            side_effect=AssertionError("drift preflight reached Memory"),
        ):
            with actor(store.ctx.root, "reviewer", "triage-session"):
                with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                    store.preflight_publish(sidecar.EVENT_HANDOFF, "attempt-1")
        self.assertEqual(sidecar.REASON_DRIFT, caught.exception.reason)
        self.assertEqual(before_drift, tree_bytes(store.dir))
        self.assertNotIn("drift", store.load())

    def test_second_triage_rejection_is_zero_write_before_ordinary_reports(self) -> None:
        fixture = self.triaged_cli_fixture("second-triage-zero-write")
        replacement_log = (
            LOG_TEST_CODE
            + "tests/test_UC-SELFHEAL.py: secondary eligible trace\n"
        )
        fixture.write_log(replacement_log)
        log_bytes = (fixture.root / GOLDEN_LOG_NAME).read_bytes()
        after_input = tree_bytes(fixture.artifact)

        second = fixture.run_self_heal(
            "--pytest-log",
            GOLDEN_LOG_NAME,
            "--command",
            "python3 alternate_runner.py",
            "--exit-code",
            "1",
            role="reviewer",
            session="reviewer-session",
        )
        combined = second.stdout + second.stderr
        self.assertNotIn("Traceback", combined)
        self.assertEqual(2, second.returncode, combined)
        parsed = gate_result(second)
        self.assertEqual("blocked", parsed["status"])
        self.assertEqual(2, parsed["exit_code"])
        self.assertEqual(
            [sidecar.REASON_TRANSITION], parsed["blocking_reasons"]
        )
        self.assertEqual(after_input, tree_bytes(fixture.artifact))
        self.assertEqual(log_bytes, (fixture.root / GOLDEN_LOG_NAME).read_bytes())

    def test_preflight_reuses_publish_identity_attempt_and_state_validation(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        before = tree_bytes(store.dir)
        cases = (
            (
                "wrong_role",
                "implementer",
                "healer-session",
                sidecar.EVENT_HANDOFF,
                "attempt-1",
                sidecar.REASON_ROLE,
            ),
            (
                "wrong_session",
                "reviewer",
                "wrong-reviewer-session",
                sidecar.EVENT_HANDOFF,
                "attempt-1",
                sidecar.REASON_SESSION_MISMATCH,
            ),
            (
                "wrong_attempt",
                "reviewer",
                "triage-session",
                sidecar.EVENT_HANDOFF,
                "attempt-2",
                sidecar.REASON_ATTEMPT_MISMATCH,
            ),
            (
                "wrong_state",
                "implementer",
                "healer-session",
                sidecar.EVENT_HEALER_ACCEPT,
                "attempt-1",
                sidecar.REASON_TRANSITION,
            ),
        )
        with mock.patch.object(
            sidecar,
            "_memory_prepare",
            side_effect=AssertionError("preflight reached Memory prepare"),
        ), mock.patch.object(
            sidecar,
            "_memory_verify",
            side_effect=AssertionError("preflight reached Memory verify"),
        ):
            for name, role, session, event, attempt_id, expected_reason in cases:
                with self.subTest(case=name), actor(store.ctx.root, role, session):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.preflight_publish(event, attempt_id)
                    self.assertEqual(expected_reason, caught.exception.reason)
                    self.assertEqual(before, tree_bytes(store.dir))

        closed_lifecycle = self.fixture(memory_mode="best_effort")
        self.close_main_chain_validly(closed_lifecycle)
        before_closed = tree_bytes(closed_lifecycle.dir)
        with actor(closed_lifecycle.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                closed_lifecycle.preflight_publish(sidecar.EVENT_TRIAGE, "attempt-1")
        self.assertEqual(sidecar.REASON_LIFECYCLE_STATE, caught.exception.reason)
        self.assertEqual(before_closed, tree_bytes(closed_lifecycle.dir))

    def test_attempt_identity_requires_a_full_safe_token_before_any_write(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        before = tree_bytes(store.ctx.artifact_dir)
        with actor(store.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                store.publish(
                    sidecar.EVENT_TRIAGE,
                    attempt_id="attempt-ok\n",
                    payload={"healing_eligible": True},
                )
        self.assertEqual(sidecar.REASON_ATTEMPT_MISMATCH, caught.exception.reason)
        self.assertEqual(before, tree_bytes(store.ctx.artifact_dir))
        self.assertFalse(store.dir.exists())

    def test_required_preflight_stays_local_after_an_anchored_receipt(self) -> None:
        with fake_memory_service() as memory:
            store = self.valid_two_receipt_store()
            before_tree = tree_bytes(store.dir)
            before_calls = list(memory.calls)
            with mock.patch.object(
                sidecar,
                "_memory_prepare",
                side_effect=AssertionError("preflight reached Memory prepare"),
            ), mock.patch.object(
                sidecar,
                "_memory_verify",
                side_effect=AssertionError("preflight reached Memory verify"),
            ):
                with actor(store.ctx.root, "implementer", "healer-session"):
                    validated = store.preflight_publish(
                        sidecar.EVENT_HEALER_ACCEPT,
                        "attempt-1",
                    )
            self.assertEqual(sidecar.STATE_HEALING_ACTIVE, validated["resulting_state"])
            self.assertEqual(before_tree, tree_bytes(store.dir))
            self.assertEqual(before_calls, memory.calls)

    def test_persisted_drift_blocks_non_triage_but_can_be_retriaged(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        self.advance_main_chain_validly(store)

        with actor(store.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                store.publish(
                    sidecar.EVENT_HANDOFF,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )
        self.assertEqual(sidecar.REASON_DRIFT, caught.exception.reason)
        self.assertEqual(sidecar.STATE_INVALIDATED, store.state())
        invalidated = tree_bytes(store.dir)

        with actor(store.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as blocked:
                store.preflight_publish(sidecar.EVENT_HANDOFF, "attempt-1")
            validated = store.preflight_publish(sidecar.EVENT_TRIAGE, "attempt-2")
        self.assertEqual(sidecar.REASON_DRIFT, blocked.exception.reason)
        self.assertEqual(sidecar.STATE_INVALIDATED, validated["prior_state"])
        self.assertEqual(invalidated, tree_bytes(store.dir))

        with actor(store.ctx.root, "reviewer", "new-triage-session"):
            receipt = store.publish(
                sidecar.EVENT_TRIAGE,
                attempt_id="attempt-2",
                payload={"healing_eligible": True},
            )
        self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())
        self.assertNotIn("drift", store.load())
        self.assertEqual(
            sidecar.STATE_INVALIDATED,
            receipt["payload"]["superseded_lifecycle_drift"]["state"],
        )

    def test_main_chain_json_reformatting_does_not_create_lifecycle_drift(self) -> None:
        fixture = self.triaged_cli_fixture("main-chain-json-reformatting")
        store = sidecar.SelfHealSidecar(rg.load_context(fixture.artifact))
        anchor_before = sidecar.read_role_chain_anchor(store.ctx)
        sidecar_before = tree_bytes(store.dir)
        main_chain_path = store.ctx.evidence_dir / "chain.json"
        main_chain_before = main_chain_path.read_bytes()
        main_chain = read_json(main_chain_path)

        main_chain_path.write_bytes(
            (
                json.dumps(
                    main_chain,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )

        self.assertNotEqual(main_chain_before, main_chain_path.read_bytes())
        self.assertEqual(main_chain, read_json(main_chain_path))
        role_verify = fixture.run(
            [
                sys.executable,
                ROOT / "scripts" / "role_governance.py",
                "verify",
                fixture.artifact,
                "--phase",
                "post_run",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assertEqual(
            0,
            role_verify.returncode,
            role_verify.stdout + role_verify.stderr,
        )
        self.assertIn("PASS: role evidence is valid", role_verify.stdout)

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="reviewer-session",
        )
        parsed = gate_result(status)
        self.assertEqual(0, status.returncode, status.stdout + status.stderr)
        self.assertEqual("triaged", parsed["status"])
        self.assertEqual(0, parsed["exit_code"])
        self.assertEqual([], parsed["blocking_reasons"])
        self.assertEqual(anchor_before, sidecar.read_role_chain_anchor(store.ctx))
        self.assertEqual(sidecar_before, tree_bytes(store.dir))
        self.assertNotIn("drift", read_json(store.chain_path))

    def test_verified_head_sequence_and_state_drift_is_still_invalidated(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        expected_anchor = sidecar.read_role_chain_anchor(store.ctx)

        self.close_main_chain_validly(store)
        observed_anchor = sidecar.read_role_chain_anchor(store.ctx)
        for field in ("chain_sha256", "sequence", "lifecycle_state"):
            with self.subTest(anchor_field=field):
                self.assertNotEqual(expected_anchor[field], observed_anchor[field])

        status = gate.step_status(store.ctx, {}, SimpleNamespace(), store)
        self.assertEqual("invalidated", status["status"])
        self.assertEqual(4, status["exit_code"])
        self.assertEqual([sidecar.REASON_DRIFT], status["blocking_reasons"])
        chain = store.load()
        self.assertEqual(sidecar.STATE_INVALIDATED, store.state())
        self.assertEqual(expected_anchor, chain["drift"]["expected_role_chain_anchor"])
        self.assertEqual(observed_anchor, chain["drift"]["observed_role_chain_anchor"])

    def test_stale_drift_writer_cannot_corrupt_a_retriaged_chain(self) -> None:
        """The anchor comparison is a CAS token, not a later blind write."""

        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        self.advance_main_chain_validly(store)

        drifted, stale_recorded, stale_observed = store.anchor_drifted()
        self.assertTrue(drifted)

        # Process B wins the race: it records the same drift and opens a new,
        # current attempt before process A reaches invalidate().
        store.invalidate(stale_recorded, stale_observed)
        with actor(store.ctx.root, "reviewer", "new-triage-session"):
            store.publish(
                sidecar.EVENT_TRIAGE,
                attempt_id="attempt-2",
                payload={"healing_eligible": True},
            )
        self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())
        self.assertNotIn("drift", store.load())
        retriaged_before = tree_bytes(store.dir)

        # Process A must not bind its stale expected anchor to attempt-2's head.
        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
            store.invalidate(stale_recorded, stale_observed)
        self.assertEqual(sidecar.REASON_DRIFT, caught.exception.reason)
        self.assertIn("chain advanced", str(caught.exception))
        self.assertEqual(retriaged_before, tree_bytes(store.dir))
        self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())
        self.assertNotIn("drift", store.load())

    def test_missing_main_chain_is_repeatably_invalidated_without_sidecar_write(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        main_chain = store.ctx.evidence_dir / "chain.json"
        main_chain.unlink()
        sidecar_before = tree_bytes(store.dir)

        for attempt in range(2):
            with self.subTest(status_call=attempt + 1):
                status = gate.step_status(store.ctx, {}, SimpleNamespace())
                self.assertEqual("invalidated", status["status"])
                self.assertEqual(4, status["exit_code"])
                self.assertEqual([sidecar.REASON_DRIFT], status["blocking_reasons"])
                self.assertEqual(sidecar_before, tree_bytes(store.dir))
                self.assertEqual(1, store.load()["sequence"])
                self.assertNotIn("drift", store.load())

        drifted, recorded, observed = store.anchor_drifted()
        self.assertTrue(drifted)
        self.assertEqual("", observed["chain_sha256"])
        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
            store.invalidate(recorded, observed)
        self.assertEqual(sidecar.REASON_DRIFT, caught.exception.reason)
        self.assertEqual(sidecar_before, tree_bytes(store.dir))

    def test_persisted_drift_anchor_rejects_an_empty_lifecycle_state(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        self.advance_main_chain_validly(store)
        self.assertTrue(store.invalidate_if_drifted())
        chain = read_json(store.chain_path)
        chain["drift"]["observed_role_chain_anchor"]["lifecycle_state"] = ""
        write_json(store.chain_path, chain)
        tampered = tree_bytes(store.ctx.artifact_dir)

        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
            store.load()
        self.assertEqual(sidecar.REASON_INTEGRITY, caught.exception.reason)
        self.assertIn(
            "observed role-chain anchor values are invalid",
            str(caught.exception),
        )
        with mock.patch.object(
            gate, "load_context", return_value=store.ctx
        ), mock.patch.object(
            gate, "self_healing_policy", return_value={"mode": "verify"}
        ):
            status = gate.run(
                store.ctx.artifact_dir,
                SimpleNamespace(self_heal_step="status"),
            )
        self.assertEqual(
            [
                "status",
                "exit_code",
                "blocking_reasons",
                "artifact_paths",
                "next_action",
            ],
            list(status),
        )
        self.assertEqual("blocked", status["status"])
        self.assertEqual(2, status["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], status["blocking_reasons"])
        self.assertEqual(tampered, tree_bytes(store.ctx.artifact_dir))

    def test_indexed_receipt_anchor_requires_post_run_active(self) -> None:
        fixture = self.triaged_cli_fixture("closed-receipt-anchor")
        complete_main_lifecycle(fixture)
        self.lineage_patch.stop()
        ctx = rg.load_context(fixture.artifact)
        store = sidecar.SelfHealSidecar(ctx)
        observed = sidecar.read_role_chain_anchor(ctx)
        self.rewrite_receipt_chain_from(
            store,
            1,
            lambda receipt: receipt.__setitem__(
                "role_chain_anchor", copy.deepcopy(observed)
            ),
        )
        artifact_before = tree_bytes(fixture.artifact)

        status = fixture.run_self_heal(
            "--self-heal-step",
            "status",
            role="reviewer",
            session="reviewer-session",
        )
        self.assertEqual(2, status.returncode, status.stdout + status.stderr)
        parsed = gate_result(status)
        self.assertEqual("blocked", parsed["status"])
        self.assertEqual(2, parsed["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], parsed["blocking_reasons"])
        self.assertEqual(artifact_before, tree_bytes(fixture.artifact))

    def test_indexed_receipt_anchors_cannot_change_without_drift(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        self.publish_handoff(store)
        self.rewrite_receipt_chain_from(
            store,
            1,
            lambda receipt: receipt["role_chain_anchor"].__setitem__(
                "chain_sha256", "b" * 64
            ),
        )
        tampered = tree_bytes(store.ctx.artifact_dir)

        with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
            store.load()
        self.assertEqual(sidecar.REASON_INTEGRITY, caught.exception.reason)
        self.assertIn("anchor changed without indexed lifecycle drift", str(caught.exception))
        with mock.patch.object(
            gate,
            "load_context",
            return_value=store.ctx,
        ), mock.patch.object(
            gate,
            "self_healing_policy",
            return_value={"mode": "verify"},
        ):
            status = gate.run(
                store.ctx.artifact_dir,
                SimpleNamespace(self_heal_step="status"),
            )
        self.assertEqual("blocked", status["status"])
        self.assertEqual(2, status["exit_code"])
        self.assertEqual([sidecar.REASON_INTEGRITY], status["blocking_reasons"])
        self.assertEqual(tampered, tree_bytes(store.ctx.artifact_dir))

    def test_malformed_main_anchor_is_repeatably_invalidated_without_write(self) -> None:
        def mutate_sequence(fixture: ImportedFixture) -> None:
            path = fixture.artifact / "00_role_evidence" / "chain.json"
            value = read_json(path)
            value["sequence"] = "oops"
            write_json(path, value)

        def mutate_schema(fixture: ImportedFixture) -> None:
            path = fixture.artifact / "00_role_evidence" / "chain.json"
            value = read_json(path)
            value["schema"] = "attacker.bad/v0"
            write_json(path, value)

        def omit_authenticated_history(fixture: ImportedFixture) -> None:
            path = fixture.artifact / "00_role_evidence" / "chain.json"
            value = read_json(path)
            value["latest_receipts"] = {
                "reviewer_acceptance": value["latest_receipts"][
                    "reviewer_acceptance"
                ]
            }
            write_json(path, value)

        def mutate_receipt_event_type(fixture: ImportedFixture) -> None:
            chain = read_json(
                fixture.artifact / "00_role_evidence" / "chain.json"
            )
            receipt_path = fixture.root / chain["latest_receipts"]["human_acceptance"]
            receipt = read_json(receipt_path)
            receipt["event"] = ["human_acceptance"]
            write_json(receipt_path, receipt)

        cases = (
            ("sequence_type", mutate_sequence),
            ("schema", mutate_schema),
            ("missing_latest_history", omit_authenticated_history),
            ("unhashable_receipt_event", mutate_receipt_event_type),
        )
        for label, corrupt in cases:
            with self.subTest(corruption=label):
                fixture = self.triaged_cli_fixture(f"malformed-main-anchor-{label}")
                corrupt(fixture)
                artifact_before = tree_bytes(fixture.artifact)
                for call in range(2):
                    status = fixture.run_self_heal(
                        "--self-heal-step",
                        "status",
                        role="reviewer",
                        session="reviewer-session",
                    )
                    combined = status.stdout + status.stderr
                    self.assertNotIn("Traceback", combined)
                    self.assertEqual(4, status.returncode, combined)
                    parsed = gate_result(status)
                    self.assertEqual(
                        [
                            "status",
                            "exit_code",
                            "blocking_reasons",
                            "artifact_paths",
                            "next_action",
                        ],
                        list(parsed),
                    )
                    self.assertEqual("invalidated", parsed["status"])
                    self.assertEqual(4, parsed["exit_code"])
                    self.assertEqual(
                        [sidecar.REASON_DRIFT], parsed["blocking_reasons"]
                    )
                    self.assertEqual(
                        artifact_before,
                        tree_bytes(fixture.artifact),
                        f"{label} status call {call + 1} changed evidence",
                    )

    def test_anchor_comparison_serializes_with_a_main_chain_publisher(self) -> None:
        """One status comparison observes one linearized pair of chain snapshots."""

        fixture = ImportedFixture(self.tmp / "anchor-main-publisher")
        fixture.drive_to_post_run(ImportedFixture.BROKEN_IMPLEMENTATION)
        fixture.write_log(LOG_TEST_CODE)
        reports = fixture.run_postrun(exit_code=1)
        self.assertEqual(0, reports.returncode, reports.stdout + reports.stderr)
        store = sidecar.SelfHealSidecar(rg.load_context(fixture.artifact))
        self.publish_triage(store)
        sidecar_before = tree_bytes(store.dir)
        child_ready = store.ctx.root / "main-publisher-ready"
        observed_ready = store.ctx.root / "sidecar-observed-main-anchor"
        publisher_attempting = store.ctx.root / "main-publisher-attempting-lock"
        publisher_acquired = store.ctx.root / "main-publisher-acquired-lock"
        completion_evidence = store.ctx.root / "concurrent-main-publication.log"
        completion_evidence.write_text("reviewed failed run\n", encoding="utf-8")
        child_source = f"""\
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, {str(ROOT / 'scripts')!r})
import role_governance as rg

child_ready = Path({str(child_ready)!r})
observed_ready = Path({str(observed_ready)!r})
publisher_attempting = Path({str(publisher_attempting)!r})
publisher_acquired = Path({str(publisher_acquired)!r})
child_ready.write_text("ready\\n", encoding="utf-8")
deadline = time.monotonic() + 15
while not observed_ready.exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("sidecar comparison did not reach the anchor cut")
    time.sleep(0.02)

original_transition_lock = rg._transition_lock
@contextmanager
def marked_transition_lock(ctx):
    publisher_attempting.write_text("attempting\\n", encoding="utf-8")
    with original_transition_lock(ctx):
        publisher_acquired.write_text("locked\\n", encoding="utf-8")
        yield

rg._transition_lock = marked_transition_lock
rg.complete(
    Path({str(store.ctx.artifact_dir)!r}),
    phase="post_run",
    run_command="concurrent reviewed failure",
    exit_code=1,
    evidence_files=[Path({str(completion_evidence)!r})],
    final_gate_status="failed",
)
"""
        env = fixture.env(role="reviewer", session="reviewer-session")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        publisher = subprocess.Popen(
            [sys.executable, "-c", child_source],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not child_ready.exists() and time.monotonic() < deadline:
                if publisher.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(
                child_ready.exists(),
                "main-chain publisher did not reach readiness: "
                + (publisher.stderr.read() if publisher.poll() is not None else ""),
            )

            original_anchor = sidecar.read_role_chain_anchor

            def pause_after_observed(ctx: rg.GovernanceContext) -> dict[str, Any]:
                observed = original_anchor(ctx)
                observed_ready.write_text("observed\n", encoding="utf-8")
                # The cooperative main-chain publisher is already runnable.  It
                # confirms it observed this cut immediately before taking the
                # transition lock. It must then remain blocked on the same
                # artifact-directory inode until anchor_drifted has also loaded
                # the sidecar snapshot.
                deadline = time.monotonic() + 10
                while (
                    not publisher_attempting.exists()
                    and time.monotonic() < deadline
                ):
                    if publisher.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(
                    publisher_attempting.exists(),
                    "main-chain publisher did not reach the immediate lock cut: "
                    + (publisher.stderr.read() if publisher.poll() is not None else ""),
                )
                # Give a lock-free implementation enough time to run after its
                # immediate-before-lock marker.  Remaining unfinished after
                # this cut is evidence of serialization, not scheduler luck.
                time.sleep(0.35)
                self.assertFalse(
                    publisher_acquired.exists(),
                    "main-chain publication interleaved between the two anchor reads",
                )
                return observed

            with mock.patch.object(
                sidecar, "read_role_chain_anchor", side_effect=pause_after_observed
            ):
                drifted, _recorded, _observed = store.anchor_drifted()
            self.assertFalse(drifted)

            publisher_stdout, publisher_stderr = publisher.communicate(timeout=15)
            self.assertEqual(0, publisher.returncode, publisher_stdout + publisher_stderr)
            self.assertTrue(publisher_acquired.exists())
        finally:
            if publisher.poll() is None:
                publisher.kill()
                publisher.communicate()

        self.assertEqual(sidecar_before, tree_bytes(store.dir))
        drifted, _recorded, _observed = store.anchor_drifted()
        self.assertTrue(drifted)

    def test_cli_status_uses_one_snapshot_across_a_concurrent_retriage(self) -> None:
        """Status never combines a closed attempt with its successor's identity."""

        store = self.fixture(memory_mode="best_effort")
        self.advance_direct_store_to(store, sidecar.EVENT_CLOSED)
        with actor(store.ctx.root, "reviewer", "independent-session"):
            store.publish(
                sidecar.EVENT_CLOSED,
                attempt_id="attempt-1",
                payload={"final_state": sidecar.STATE_VERIFIED},
            )
        self.assertEqual(sidecar.STATE_CLOSED, store.state())

        status_loaded = store.ctx.root / "status-loaded-closed-attempt"
        peer_attempting = store.ctx.root / "peer-retriage-attempting"
        peer_acquired = store.ctx.root / "peer-retriage-published"
        context_source = f"""\
ctx = rg.GovernanceContext(
    root=Path({str(store.ctx.root)!r}),
    artifact_dir=Path({str(store.ctx.artifact_dir)!r}),
    config={store.ctx.config!r},
    policy={store.ctx.policy!r},
    profile_path=Path({str(store.ctx.profile_path)!r}),
    uc={store.ctx.uc!r},
)
sidecar.lineage_identity = lambda _artifact_dir: {{"lineage_id": "f" * 64}}
"""
        peer_source = f"""\
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, {str(ROOT / 'scripts')!r})
import role_governance as rg
import self_heal_sidecar as sidecar

{context_source}
status_loaded = Path({str(status_loaded)!r})
peer_attempting = Path({str(peer_attempting)!r})
peer_acquired = Path({str(peer_acquired)!r})
deadline = time.monotonic() + 15
while not status_loaded.exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("status did not load the closed snapshot")
    time.sleep(0.02)
peer_attempting.write_text("attempting\\n", encoding="utf-8")
sidecar.SelfHealSidecar(ctx).publish(
    sidecar.EVENT_TRIAGE,
    attempt_id="attempt-2",
    payload={{"healing_eligible": True}},
)
peer_acquired.write_text("published\\n", encoding="utf-8")
"""
        env = os.environ.copy()
        env.update(
            {
                "BUGATE_PROJECT_ROOT": str(store.ctx.root),
                "BUGATE_AGENT_ROLE": "reviewer",
                "BUGATE_SESSION_ID": "new-triage-session",
                "BUGATE_AGENT_RUNTIME": "codex",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        peer = subprocess.Popen(
            [sys.executable, "-c", peer_source],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        original_load = sidecar.SelfHealSidecar._load_verified
        paused = False

        def pause_after_closed_load(
            instance: sidecar.SelfHealSidecar, *args: Any, **kwargs: Any
        ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
            nonlocal paused
            snapshot = original_load(instance, *args, **kwargs)
            chain = snapshot[0]
            if (
                not paused
                and chain["entries"]
                and chain["entries"][-1].get("state") == sidecar.STATE_CLOSED
            ):
                paused = True
                status_loaded.write_text("loaded\n", encoding="utf-8")
                deadline = time.monotonic() + 10
                while not peer_attempting.exists() and time.monotonic() < deadline:
                    if peer.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(
                    peer_attempting.exists(),
                    "peer did not reach the immediate retriage cut: "
                    + (peer.stderr.read() if peer.poll() is not None else ""),
                )
                time.sleep(0.35)
                self.assertFalse(peer_acquired.exists())
            return snapshot

        try:
            with mock.patch.object(
                sidecar.SelfHealSidecar,
                "_load_verified",
                autospec=True,
                side_effect=pause_after_closed_load,
            ):
                status = gate.step_status(store.ctx, {}, SimpleNamespace())
            self.assertEqual("healing_verified", status["status"])
            self.assertIn("attempt-1", status["next_action"])
            self.assertIn(sidecar.STATE_CLOSED, status["next_action"])

            peer_stdout, peer_stderr = peer.communicate(timeout=15)
            self.assertEqual(0, peer.returncode, peer_stdout + peer_stderr)
        finally:
            if peer.poll() is None:
                peer.kill()
                peer.communicate()

        self.assertEqual(sidecar.STATE_TRIAGE_RECORDED, store.state())
        self.assertEqual("attempt-2", store.attempt_id())

    def test_retriage_rejects_reusing_a_prior_attempt_id(self) -> None:
        store = self.fixture(memory_mode="best_effort")
        self.publish_triage(store)
        self.advance_main_chain_validly(store)
        with actor(store.ctx.root, "reviewer", "triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError):
                store.publish(
                    sidecar.EVENT_HANDOFF,
                    attempt_id="attempt-1",
                    payload={"healing_eligible": True},
                )
        before = tree_bytes(store.dir)
        with actor(store.ctx.root, "reviewer", "new-triage-session"):
            with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                store.preflight_publish(sidecar.EVENT_TRIAGE, "attempt-1")
        self.assertEqual(sidecar.REASON_ATTEMPT_MISMATCH, caught.exception.reason)
        self.assertEqual(before, tree_bytes(store.dir))

        chain = read_json(store.chain_path)
        chain.pop("drift")
        write_json(store.chain_path, chain)
        receipt_path = store.dir / str(chain["entries"][0]["receipt"])
        prior = read_json(receipt_path)
        forged = dict(prior)
        forged["sequence"] = 2
        forged["prior_state"] = sidecar.STATE_INVALIDATED
        forged["parent_receipt_sha256"] = prior["receipt_sha256"]
        forged["payload"] = {
            "healing_eligible": True,
            "superseded_lifecycle_drift": {
                "detected_at": "2026-08-12T00:00:00Z",
                "expected_role_chain_anchor": prior["role_chain_anchor"],
                "observed_role_chain_anchor": sidecar.read_role_chain_anchor(store.ctx),
                "state": sidecar.STATE_INVALIDATED,
            },
        }
        forged["role_chain_anchor"] = sidecar.read_role_chain_anchor(store.ctx)
        forged["recorded_at"] = "2026-08-12T00:00:01Z"
        forged["receipt_sha256"] = sidecar.receipt_sha256(forged)
        forged_name = "001-triage_recorded.json"
        write_json(store.dir / forged_name, forged)
        chain["entries"].append(
            {
                "sequence": 2,
                "event": sidecar.EVENT_TRIAGE,
                "state": sidecar.STATE_TRIAGE_RECORDED,
                "attempt_id": "attempt-1",
                "receipt": forged_name,
                "receipt_sha256": forged["receipt_sha256"],
                "recorded_at": forged["recorded_at"],
                "session_id": forged["actor"]["session_id"],
            }
        )
        chain["head_sha256"] = forged["receipt_sha256"]
        chain["sequence"] = 2
        write_json(store.chain_path, chain)
        self.assert_integrity_failure(store, "attempt id is reused", via_state=True)

    def test_publish_rechecks_anchor_after_memory_before_local_receipt(self) -> None:
        with fake_memory_service():
            store = self.fixture()
            self.publish_triage(store)
            original_verify = sidecar._memory_verify

            def verify_then_move_main_chain(
                ctx: rg.GovernanceContext, receipt: dict[str, Any]
            ) -> None:
                original_verify(ctx, receipt)
                self.advance_main_chain_validly(store)

            with mock.patch.object(
                sidecar,
                "_memory_verify",
                side_effect=verify_then_move_main_chain,
            ):
                with actor(store.ctx.root, "reviewer", "triage-session"):
                    with self.assertRaises(sidecar.SelfHealSidecarError) as caught:
                        store.publish(
                            sidecar.EVENT_HANDOFF,
                            attempt_id="attempt-1",
                            payload={"healing_eligible": True},
                        )
            self.assertEqual(sidecar.REASON_DRIFT, caught.exception.reason)
            self.assertEqual(1, store.load()["sequence"])
            self.assertEqual(sidecar.STATE_INVALIDATED, store.state())
            self.assertFalse((store.dir / "001-self_heal_handoff.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
