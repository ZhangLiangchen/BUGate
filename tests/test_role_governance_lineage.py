#!/usr/bin/env python3
"""End-to-end lineage integrity tests for Wave 7 role governance."""

from __future__ import annotations

import base64
import copy
import json
import multiprocessing
import os
import shutil
import sqlite3
import stat
import sys
import threading
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import role_governance as rg  # noqa: E402
from test_role_governance import Fixture, fake_memory, role_env  # noqa: E402


TOKEN_ENV = ("MCP_API_KEY_AGENT", "MCP_API_KEY_HUMAN", "MCP_API_KEY")
MEMORY_ENV = ("MCP_MEMORY_BASE_DIR", "BUGATE_MEMORY_HOME")


class FakeMemoryLedger:
    """In-process exact Memory substitute with explicit read/write counters."""

    def __init__(self) -> None:
        self.roots: dict[str, dict[str, object]] = {}
        self.checkpoints: dict[str, dict[str, object]] = {}
        self.prepare_calls: list[dict[str, object]] = []
        self.finalize_calls: list[dict[str, object]] = []
        self.root_ensure_calls: list[str] = []
        self.root_probe_calls: list[str] = []
        self.checkpoint_create_calls: list[dict[str, object]] = []
        self.checkpoint_get_calls: list[str] = []
        self.checkpoint_probe_calls: list[str] = []
        self.fail_prepare = False
        self.fail_checkpoint = False

    def prepare(
        self, ctx: rg.GovernanceContext, transition: dict[str, object]
    ) -> dict[str, object]:
        self.prepare_calls.append(copy.deepcopy(transition))
        if self.fail_prepare:
            raise rg.RoleGovernanceError("injected strict Memory prepare outage")
        prepared = fake_memory(ctx, transition)
        original_finalize = prepared["_finalizer"]

        def finalize(**kwargs: object) -> dict[str, object]:
            self.finalize_calls.append(copy.deepcopy(kwargs))
            return original_finalize(**kwargs)

        prepared["_finalizer"] = finalize
        return prepared

    def ensure_root(self, ctx: rg.GovernanceContext, key: object) -> dict[str, object]:
        del ctx
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
            "content_sha256": exact_id,
            "payload": payload,
            "status": "verified",
        }
        self.root_ensure_calls.append(key.lineage_id)
        self.roots[key.lineage_id] = copy.deepcopy(result)
        return result

    def probe_root(
        self, ctx: rg.GovernanceContext, key: object
    ) -> dict[str, object] | None:
        del ctx
        self.root_probe_calls.append(key.lineage_id)
        if key.lineage_id not in self.roots:
            return None
        return copy.deepcopy(self.roots[key.lineage_id])

    def create_checkpoint(
        self, ctx: rg.GovernanceContext, payload: dict[str, object]
    ) -> dict[str, object]:
        del ctx
        self.checkpoint_create_calls.append(copy.deepcopy(payload))
        if self.fail_checkpoint:
            raise rg.RoleGovernanceError("injected strict Memory checkpoint outage")
        exact_id = rg.sha256_bytes(rg.canonical_json(payload))
        result = {
            "namespace": payload["lineage_key"]["namespace"],
            "lineage_id": payload["lineage_id"],
            "lineage_root_id": payload["lineage_root_id"],
            "checkpoint_id": exact_id,
            "memory_id": exact_id,
            "content_sha256": exact_id,
            "sequence": payload["sequence"],
            "registry_revision": payload["registry_revision"],
            "resulting_state": payload["resulting_state"],
            "payload": copy.deepcopy(payload),
            "status": "verified",
        }
        self.checkpoints[exact_id] = copy.deepcopy(result)
        return result

    def get_checkpoint(
        self, ctx: rg.GovernanceContext, checkpoint_id: str
    ) -> dict[str, object]:
        del ctx
        self.checkpoint_get_calls.append(checkpoint_id)
        if checkpoint_id not in self.checkpoints:
            raise rg.RoleGovernanceError("exact fake checkpoint is absent")
        return copy.deepcopy(self.checkpoints[checkpoint_id])

    def probe_checkpoint(
        self,
        ctx: rg.GovernanceContext,
        checkpoint_id: str,
    ) -> dict[str, object] | None:
        del ctx
        self.checkpoint_probe_calls.append(checkpoint_id)
        if checkpoint_id not in self.checkpoints:
            return None
        return copy.deepcopy(self.checkpoints[checkpoint_id])

    def write_counts(self) -> tuple[int, int, int, int]:
        return (
            len(self.root_ensure_calls),
            len(self.prepare_calls),
            len(self.finalize_calls),
            len(self.checkpoint_create_calls),
        )


def _publisher_process(
    workspace: str,
    artifact: str,
    memory_home: str,
    label: str,
    ready: object,
    start: object,
    results: object,
) -> None:
    """Race distinct workspace inodes that resolve to one deterministic lineage."""

    for name in TOKEN_ENV:
        os.environ.pop(name, None)
    os.environ["BUGATE_PROJECT_ROOT"] = workspace
    os.environ.pop("BUGATE_PROFILE", None)
    os.environ["MCP_MEMORY_BASE_DIR"] = memory_home
    os.environ["BUGATE_MEMORY_HOME"] = memory_home
    os.environ["BUGATE_AGENT_ROLE"] = "designer"
    os.environ["BUGATE_SESSION_ID"] = f"designer-{label}"
    prepare_count = 0

    def counted_prepare(
        ctx: rg.GovernanceContext, transition: dict[str, object]
    ) -> dict[str, object]:
        nonlocal prepare_count
        prepare_count += 1
        return fake_memory(ctx, transition)

    rg._memory_prepare = counted_prepare
    rg._memory_verify = lambda ctx, receipt: None
    rg.verify_precode_semantics = lambda ctx: None
    try:
        ready.put(label)
        if not start.wait(15):
            results.put(("error", label, "start timeout", prepare_count))
            return
        receipt = rg.approve(
            Path(artifact),
            approved_by="qa-owner",
            role="designer",
            session_id=f"designer-{label}",
        )
        results.put(
            (
                "winner",
                label,
                receipt["receipt_sha256"],
                receipt["sequence"],
                prepare_count,
            )
        )
    except rg.RoleGovernanceError as exc:
        message = str(exc)
        expected_conflict = any(
            token in message
            for token in (
                "active transaction",
                "integrity_state=history_diverged",
                "integrity_state=recovery_pending",
                "head/sequence/revision/checkpoint no longer matches",
            )
        )
        outcome = "loser" if expected_conflict else "error"
        results.put((outcome, label, type(exc).__name__, message, prepare_count))
        raise SystemExit(2 if outcome == "loser" else 3)
    except Exception as exc:
        results.put(("error", label, type(exc).__name__, str(exc), prepare_count))
        raise SystemExit(4)


class RoleGovernanceLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(prefix="bugate-role-governance-lineage-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @contextmanager
    def activated(
        self,
        workspace: Path,
        memory_home: Path,
        ledger: FakeMemoryLedger,
    ):
        memory_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        keys = (
            "BUGATE_PROJECT_ROOT",
            "BUGATE_PROFILE",
            "BUGATE_AGENT_ROLE",
            "BUGATE_SESSION_ID",
            *MEMORY_ENV,
            *TOKEN_ENV,
        )
        old = {key: os.environ.get(key) for key in keys}
        os.environ["BUGATE_PROJECT_ROOT"] = str(workspace)
        os.environ.pop("BUGATE_PROFILE", None)
        os.environ.pop("BUGATE_AGENT_ROLE", None)
        os.environ.pop("BUGATE_SESSION_ID", None)
        os.environ["MCP_MEMORY_BASE_DIR"] = str(memory_home)
        os.environ["BUGATE_MEMORY_HOME"] = str(memory_home)
        for key in TOKEN_ENV:
            os.environ.pop(key, None)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(rg, "_memory_prepare", ledger.prepare))
            stack.enter_context(mock.patch.object(rg, "_memory_verify", lambda ctx, receipt: None))
            stack.enter_context(
                mock.patch.object(rg, "_memory_ensure_lineage_root", ledger.ensure_root)
            )
            stack.enter_context(
                mock.patch.object(rg, "_memory_probe_lineage_root", ledger.probe_root)
            )
            stack.enter_context(
                mock.patch.object(rg, "_memory_create_checkpoint", ledger.create_checkpoint)
            )
            stack.enter_context(
                mock.patch.object(rg, "_memory_get_checkpoint", ledger.get_checkpoint)
            )
            stack.enter_context(
                mock.patch.object(rg, "_memory_probe_checkpoint", ledger.probe_checkpoint)
            )
            stack.enter_context(
                mock.patch.object(rg, "verify_precode_semantics", lambda ctx: None)
            )
            try:
                yield
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    @contextmanager
    def fixture_context(
        self,
        label: str,
        *,
        memory_mode: str = "required",
    ):
        scenario = self.root / label
        workspace = scenario / "workspace"
        memory_home = scenario / "memory-home"
        ledger = FakeMemoryLedger()
        with self.activated(workspace, memory_home, ledger):
            fixture = Fixture(workspace, memory_mode=memory_mode)
            yield fixture, ledger, memory_home, scenario

    @staticmethod
    def tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes | str]]:
        snapshot: dict[str, tuple[str, int, bytes | str]] = {}
        if not root.exists():
            return snapshot
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if path.is_symlink():
                snapshot[rel] = ("symlink", mode, os.readlink(path))
            elif path.is_dir():
                snapshot[rel] = ("dir", mode, b"")
            else:
                snapshot[rel] = ("file", mode, path.read_bytes())
        return snapshot

    @staticmethod
    def registry_state(
        fixture: Fixture, memory_home: Path
    ) -> tuple[object, tuple[object, ...]]:
        context = rg.load_context(fixture.artifact)
        key = rg._lineage_key(context)
        registry = rg.lineage_registry.LineageRegistry(memory_home)
        return (
            registry.require_lineage(key),
            tuple(registry.list_active_transactions(key)),
        )

    @staticmethod
    def publish_sequence_two(fixture: Fixture) -> tuple[dict, dict]:
        with role_env("designer", "designer-sequence-two"):
            approval = rg.approve(fixture.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                fixture.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        return approval, handoff

    @staticmethod
    def publish_sequence_three(fixture: Fixture) -> tuple[dict, dict, dict]:
        approval, handoff = RoleGovernanceLineageTests.publish_sequence_two(fixture)
        with role_env("implementer", "implementer-sequence-three"):
            acceptance = rg.accept(
                fixture.artifact,
                phase="implementation",
                handoff_id=handoff["memory"]["memory_id"],
            )
        return approval, handoff, acceptance

    @staticmethod
    def publish_full_lifecycle(fixture: Fixture) -> list[dict]:
        with role_env("designer", "designer-full-lifecycle"):
            rg.approve(fixture.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                fixture.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        with role_env("implementer", "implementer-full-lifecycle"):
            rg.accept(
                fixture.artifact,
                phase="implementation",
                handoff_id=designer_handoff["memory"]["memory_id"],
            )
            fixture.implementation.write_text(
                "def test_fixture():\n    assert True\n",
                encoding="utf-8",
            )
            implementer_handoff = rg.handoff(
                fixture.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[fixture.implementation],
            )
        with role_env("reviewer", "reviewer-full-lifecycle"):
            rg.accept(
                fixture.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["memory"]["memory_id"],
            )
            for name in sorted(rg.POSTRUN_NAMES):
                (fixture.artifact / name).write_text(
                    "gate_status: passed\nfixture: post-run\n",
                    encoding="utf-8",
                )
            evidence = fixture.artifact / "execution.log"
            evidence.write_text("fixture execution\n", encoding="utf-8")
            rg.complete(
                fixture.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[evidence],
                final_gate_status="passed",
            )
        return copy.deepcopy(rg.verify_chain(rg.load_context(fixture.artifact)))

    @staticmethod
    def remove_registry(memory_home: Path) -> None:
        for candidate in memory_home.glob("role-lineage.sqlite3*"):
            candidate.unlink()

    @staticmethod
    def rewrite_as_legacy_history(
        fixture: Fixture,
        source_receipts: list[dict],
    ) -> tuple[list[dict], dict]:
        """Write a v0.4.x-style, fully rehashed chain in the supplied order."""

        ctx = rg.load_context(fixture.artifact)
        if ctx.evidence_dir.exists():
            shutil.rmtree(ctx.evidence_dir)
        receipt_dir = ctx.evidence_dir / "receipts"
        receipt_dir.mkdir(mode=0o700, parents=True)
        remapped_hashes: dict[str, str] = {}
        previous = ""
        latest: dict[str, str] = {}
        rewritten: list[dict] = []
        for sequence, source in enumerate(source_receipts, 1):
            receipt = copy.deepcopy(source)
            original_hash = str(receipt["receipt_sha256"])
            receipt.pop("lineage", None)
            if isinstance(receipt.get("profile"), dict):
                receipt["profile"].pop("effective_config_sha256", None)
            for field in (
                "handoff_receipt_sha256",
                "accepted_handoff_receipt_sha256",
            ):
                value = receipt.get(field)
                if isinstance(value, str) and value in remapped_hashes:
                    receipt[field] = remapped_hashes[value]
            human = receipt.get("human_acceptance")
            if isinstance(human, dict):
                value = human.get("receipt_sha256")
                if isinstance(value, str) and value in remapped_hashes:
                    human["receipt_sha256"] = remapped_hashes[value]
            recovery = receipt.get("recovery")
            if isinstance(recovery, dict):
                value = recovery.get("recovered_head_sha256")
                if isinstance(value, str) and value in remapped_hashes:
                    recovery["recovered_head_sha256"] = remapped_hashes[value]
            receipt["sequence"] = sequence
            receipt["previous_receipt_sha256"] = previous
            receipt["transition_sha256"] = rg.sha256_bytes(
                rg.canonical_json(rg._transition_from_receipt(receipt))
            )
            receipt["receipt_sha256"] = rg.receipt_sha256(receipt)
            remapped_hashes[original_hash] = receipt["receipt_sha256"]
            event = str(receipt["event"])
            path = receipt_dir / (
                f"{sequence:06d}-{event.replace('_', '-')}-"
                f"{receipt['receipt_sha256']}.json"
            )
            path.write_bytes(rg._json_bytes(receipt))
            os.chmod(path, 0o600)
            latest[event] = rg.workspace_rel(path, ctx.root)
            previous = receipt["receipt_sha256"]
            rewritten.append(receipt)
        chain = {
            "schema": rg.CHAIN_SCHEMA,
            "state": (
                rewritten[-1]["resulting_state"]
                if rewritten
                else rg.INITIAL_STATE
            ),
            "sequence": len(rewritten),
            "head_sha256": previous,
            "latest_receipts": latest,
        }
        chain_path = ctx.evidence_dir / "chain.json"
        chain_path.write_bytes(rg._json_bytes(chain))
        os.chmod(chain_path, 0o600)
        return rewritten, chain

    @staticmethod
    def captured_history(
        fixture: Fixture,
    ) -> tuple[
        dict[str, tuple[bytes, int, dict]],
        tuple[bytes, int, dict],
    ]:
        context = rg.load_context(fixture.artifact)
        receipts = rg.verify_chain(context)
        captured: dict[str, tuple[bytes, int, dict]] = {}
        for path, receipt in zip(
            sorted((context.evidence_dir / "receipts").glob("*.json")),
            receipts,
        ):
            captured[path.name] = (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
                copy.deepcopy(receipt),
            )
        chain_path = context.evidence_dir / "chain.json"
        return captured, (
            chain_path.read_bytes(),
            stat.S_IMODE(chain_path.stat().st_mode),
            rg.load_chain(context),
        )

    @staticmethod
    def write_archive(
        fixture: Fixture,
        path: Path,
        *,
        lineage_id: str,
        expected_head: str,
    ) -> bytes:
        context = rg.load_context(fixture.artifact)
        receipts = rg.verify_chain(context)
        receipt_paths = sorted((context.evidence_dir / "receipts").glob("*.json"))
        receipt_envelopes = [
            rg._checkpoint_envelope(
                context,
                receipt_path,
                receipt_path.read_bytes(),
                receipt,
                mode=stat.S_IMODE(receipt_path.stat().st_mode),
            )
            for receipt_path, receipt in zip(receipt_paths, receipts)
        ]
        chain_path = context.evidence_dir / "chain.json"
        chain = rg.load_chain(context)
        archive = {
            "schema": rg.RECOVERY_ARCHIVE_SCHEMA,
            "lineage_id": lineage_id,
            "expected_head_sha256": expected_head,
            "receipt_envelopes": receipt_envelopes,
            "chain_envelope": rg._checkpoint_envelope(
                context,
                chain_path,
                chain_path.read_bytes(),
                chain,
                mode=stat.S_IMODE(chain_path.stat().st_mode),
            ),
        }
        body = rg._json_bytes(archive)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(body)
        os.chmod(path, 0o600)
        return body

    @staticmethod
    def mutate_archive_envelope(
        path: Path,
        *,
        target: str,
        mutation: str,
    ) -> bytes:
        archive = json.loads(path.read_bytes())
        envelope = (
            archive["receipt_envelopes"][0]
            if target == "receipt"
            else archive["chain_envelope"]
        )
        if mutation == "wrong_mode":
            envelope["mode"] = 0o644
        elif mutation == "noncanonical_bytes":
            raw = base64.b64decode(envelope["bytes_base64"])
            raw = b" " + raw
            envelope["bytes_base64"] = base64.b64encode(raw).decode("ascii")
            envelope["bytes_sha256"] = rg.sha256_bytes(raw)
        else:  # pragma: no cover - test helper contract
            raise AssertionError(f"unknown recovery-envelope mutation: {mutation}")
        body = rg._json_bytes(archive)
        path.write_bytes(body)
        os.chmod(path, 0o600)
        return body

    def assert_original_receipts_exact(
        self,
        fixture: Fixture,
        original: dict[str, tuple[bytes, int, dict]],
    ) -> None:
        receipt_dir = fixture.artifact / "00_role_evidence" / "receipts"
        for name, (body, mode, parsed) in original.items():
            path = receipt_dir / name
            self.assertEqual(body, path.read_bytes(), name)
            self.assertEqual(mode, stat.S_IMODE(path.stat().st_mode), name)
            self.assertEqual(parsed, json.loads(path.read_bytes()), name)
            self.assertEqual(parsed["receipt_sha256"], rg.receipt_sha256(parsed), name)

    def test_deletion_matrix_is_nonzero_and_all_publishers_are_fail_closed(self) -> None:
        mutations = {
            "chain": lambda evidence: (evidence / "chain.json").unlink(),
            "all_receipts": lambda evidence: [
                path.unlink() for path in (evidence / "receipts").glob("*.json")
            ],
            "single_receipt": lambda evidence: sorted(
                (evidence / "receipts").glob("*.json")
            )[0].unlink(),
            "evidence_dir": lambda evidence: shutil.rmtree(evidence),
            "recreated_empty_dir": lambda evidence: (
                shutil.rmtree(evidence),
                evidence.mkdir(mode=0o700),
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                with self.fixture_context(f"deletion-{label}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    _approval, handoff = self.publish_sequence_two(fixture)
                    record_before, active_before = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual((), active_before)
                    writes_before = ledger.write_counts()
                    evidence = fixture.artifact / "00_role_evidence"
                    mutate(evidence)
                    after_delete = self.tree_snapshot(fixture.root)

                    stdout = StringIO()
                    stderr = StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        exit_code = rg.main(
                            ["status", str(fixture.artifact), "--json"]
                        )
                    status = json.loads(stdout.getvalue())
                    self.assertEqual(2, exit_code)
                    self.assertFalse(status["ok"])
                    self.assertEqual("history_missing", status["integrity_state"])
                    self.assertEqual(
                        "awaiting_implementer_acceptance",
                        status["lifecycle_state"],
                    )
                    self.assertEqual(
                        handoff["receipt_sha256"],
                        status["registry_head_sha256"],
                    )

                    with role_env("designer", "designer-after-deletion"):
                        with self.assertRaises(rg.RoleGovernanceError):
                            rg.approve(fixture.artifact, approved_by="qa-owner")
                        with self.assertRaises(rg.RoleGovernanceError):
                            rg.handoff(
                                fixture.artifact,
                                phase="pre_code",
                                to_role="implementer",
                            )
                    self.assertEqual(after_delete, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    record_after, active_after = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(record_before, record_after)
                    self.assertEqual((), active_after)

    def test_preflight_uses_one_registry_snapshot_during_cross_workspace_commit(self) -> None:
        with self.fixture_context(
            "integrity-snapshot-race", memory_mode="best_effort"
        ) as (fixture, _ledger, memory_home, scenario):
            context = rg.load_context(fixture.artifact)
            key = rg._lineage_key(context)
            registry = rg.lineage_registry.LineageRegistry(memory_home)
            lineage = registry.require_lineage(key)
            transaction = registry.begin_pending(
                key,
                event="human_acceptance",
                expected_head_sha256=lineage.head_sha256,
                expected_sequence=lineage.sequence,
                expected_revision=lineage.revision,
                expected_checkpoint_memory_id=lineage.checkpoint_memory_id,
                target_lifecycle_state="ready_for_designer_handoff",
                transition_payload={"schema": "test.transition/v1"},
            )
            first_read = threading.Event()
            writer_done = threading.Event()
            writer_errors: list[BaseException] = []
            reader_ident = threading.get_ident()
            original_select = rg.lineage_registry.LineageRegistry._select_lineage

            def barrier_select(connection: sqlite3.Connection, lineage_id: str):
                record = original_select(connection, lineage_id)
                if threading.get_ident() == reader_ident and not first_read.is_set():
                    first_read.set()
                    self.assertTrue(writer_done.wait(10), "registry writer timed out")
                return record

            def commit_head() -> None:
                try:
                    self.assertTrue(first_read.wait(10), "integrity reader timed out")
                    current = registry.update_stage(
                        transaction.tx_id,
                        expected_stage=rg.lineage_registry.TX_STAGE_PENDING,
                        new_stage=rg.lineage_registry.TX_STAGE_MEMORY_PREPARED,
                    )
                    current = registry.update_stage(
                        current.tx_id,
                        expected_stage=current.stage,
                        new_stage=rg.lineage_registry.TX_STAGE_RECEIPT_BOUND,
                        target_head_sha256="a" * 64,
                        receipt_path=(
                            f"{key.artifact_dir}/00_role_evidence/receipts/"
                            f"000001-human-acceptance-{'a' * 64}.json"
                        ),
                        receipt_bytes=b'{"fixture":1}',
                        receipt_mode=0o600,
                        receipt_sha256="a" * 64,
                    )
                    current = registry.update_stage(
                        current.tx_id,
                        expected_stage=current.stage,
                        new_stage=rg.lineage_registry.TX_STAGE_MEMORY_FINALIZED,
                    )
                    current = registry.update_stage(
                        current.tx_id,
                        expected_stage=current.stage,
                        new_stage=rg.lineage_registry.TX_STAGE_READY_FOR_CAS,
                    )
                    committed = registry.compare_and_swap_head(
                        current.tx_id,
                        expected_stage=current.stage,
                    )
                    current = registry.update_stage(
                        committed.transaction.tx_id,
                        expected_stage=committed.transaction.stage,
                        new_stage=rg.lineage_registry.TX_STAGE_RECEIPT_WRITTEN,
                    )
                    current = registry.update_stage(
                        current.tx_id,
                        expected_stage=current.stage,
                        new_stage=rg.lineage_registry.TX_STAGE_CHAIN_REPLACED,
                    )
                    registry.complete(current.tx_id)
                except BaseException as exc:  # pragma: no cover - surfaced below
                    writer_errors.append(exc)
                finally:
                    writer_done.set()

            writer = threading.Thread(target=commit_head, daemon=True)
            writer.start()
            with mock.patch.object(
                rg.lineage_registry.LineageRegistry,
                "_select_lineage",
                side_effect=barrier_select,
            ), mock.patch.object(rg, "load_context", return_value=context), role_env(
                "designer", "designer-snapshot-race"
            ):
                result = rg.preflight(
                    fixture.artifact,
                    "pre_code",
                    require_acceptance=False,
                )
            writer.join(timeout=10)
            self.assertFalse(writer.is_alive())
            self.assertEqual([], writer_errors)
            self.assertFalse(result.allowed, result)
            self.assertTrue(
                any("integrity_state=recovery_pending" in item for item in result.errors),
                result.errors,
            )
            self.assertEqual(1, registry.require_lineage(key).sequence)
            self.assertEqual([], registry.list_active_transactions(key))

    def test_required_checkpoint_detects_exact_byte_and_mode_drift_locally(self) -> None:
        mutations = {
            "receipt whitespace": lambda receipt, chain: receipt.write_text(
                json.dumps(json.loads(receipt.read_bytes()), separators=(",", ":")),
                encoding="utf-8",
            ),
            "chain whitespace": lambda receipt, chain: chain.write_text(
                json.dumps(json.loads(chain.read_bytes()), separators=(",", ":")),
                encoding="utf-8",
            ),
            "receipt mode": lambda receipt, chain: os.chmod(receipt, 0o644),
            "chain mode": lambda receipt, chain: os.chmod(chain, 0o666),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                with self.fixture_context(f"checkpoint-local-{label.replace(' ', '-')}") as (
                    fixture,
                    ledger,
                    _memory_home,
                    _scenario,
                ):
                    with role_env("designer", "designer-checkpoint-local"):
                        approval = rg.approve(
                            fixture.artifact,
                            approved_by="qa-owner",
                        )
                    evidence = fixture.artifact / "00_role_evidence"
                    receipt = next((evidence / "receipts").glob("*.json"))
                    chain = evidence / "chain.json"
                    self.assertEqual(
                        approval["receipt_sha256"],
                        json.loads(receipt.read_bytes())["receipt_sha256"],
                    )
                    calls_before = ledger.write_counts()
                    mutate(receipt, chain)
                    changed = self.tree_snapshot(fixture.root)
                    status = rg.status_data(fixture.artifact)
                    self.assertFalse(status["ok"])
                    self.assertEqual("history_diverged", status["integrity_state"])
                    self.assertIn("checkpoint", status["error"])
                    self.assertEqual(calls_before, ledger.write_counts())
                    self.assertEqual(changed, self.tree_snapshot(fixture.root))

    def test_required_checkpoint_detects_older_receipt_byte_and_mode_drift(self) -> None:
        mutations = {
            "older receipt whitespace": lambda path: path.write_text(
                json.dumps(json.loads(path.read_bytes()), separators=(",", ":")),
                encoding="utf-8",
            ),
            "older receipt mode": lambda path: os.chmod(path, 0o644),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                with self.fixture_context(f"checkpoint-history-{label.replace(' ', '-')}") as (
                    fixture,
                    ledger,
                    _memory_home,
                    _scenario,
                ):
                    self.publish_sequence_two(fixture)
                    receipts = sorted(
                        (fixture.artifact / "00_role_evidence" / "receipts").glob(
                            "*.json"
                        )
                    )
                    self.assertEqual(2, len(receipts))
                    calls_before = ledger.write_counts()
                    mutate(receipts[0])
                    changed = self.tree_snapshot(fixture.root)
                    status = rg.status_data(fixture.artifact)
                    self.assertFalse(status["ok"])
                    self.assertEqual("history_diverged", status["integrity_state"])
                    self.assertIn("receipt[1]", status["error"])
                    self.assertIn("checkpoint", status["error"])
                    self.assertEqual(calls_before, ledger.write_counts())
                    self.assertEqual(changed, self.tree_snapshot(fixture.root))

    def test_role_evidence_symlinks_fail_closed_without_following_targets(self) -> None:
        for label in ("chain", "evidence", "receipts"):
            with self.subTest(path=label):
                with self.fixture_context(f"local-symlink-{label}") as (
                    fixture,
                    ledger,
                    _memory_home,
                    scenario,
                ):
                    if label != "chain":
                        with role_env("designer", "designer-symlink-local"):
                            rg.approve(fixture.artifact, approved_by="qa-owner")
                    evidence = fixture.artifact / "00_role_evidence"
                    target = {
                        "chain": evidence / "chain.json",
                        "evidence": evidence,
                        "receipts": evidence / "receipts",
                    }[label]
                    external = scenario / f"external-{label}"
                    target.rename(external)
                    target.symlink_to(external, target_is_directory=external.is_dir())
                    calls_before = ledger.write_counts()
                    external_before = self.tree_snapshot(external)
                    status = rg.status_data(fixture.artifact)
                    self.assertFalse(status["ok"])
                    self.assertEqual("history_diverged", status["integrity_state"])
                    self.assertIn("symlink", status["error"])
                    self.assertEqual(calls_before, ledger.write_counts())
                    self.assertEqual(external_before, self.tree_snapshot(external))

    def test_human_readable_status_labels_integrity_and_lifecycle_separately(self) -> None:
        with self.fixture_context("human-readable-integrity-status") as (
            fixture,
            ledger,
            _memory_home,
            _scenario,
        ):
            _approval, _handoff = self.publish_sequence_two(fixture)
            shutil.rmtree(fixture.artifact / "00_role_evidence")
            memory_calls_before = ledger.write_counts()

            for command in ("status", "lineage-status"):
                with self.subTest(command=command):
                    stdout = StringIO()
                    stderr = StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        exit_code = rg.main([command, str(fixture.artifact)])
                    self.assertEqual(2, exit_code)
                    rendered = stdout.getvalue()
                    self.assertIn("integrity=history_missing", rendered)
                    self.assertIn(
                        "lifecycle=awaiting_implementer_acceptance",
                        rendered,
                    )
                    self.assertNotIn(" state=", rendered)
                    self.assertIn("integrity_state=history_missing", stderr.getvalue())

            self.assertEqual(memory_calls_before, ledger.write_counts())

    def test_legacy_adoption_rejects_semantically_invalid_lifecycle_graphs(self) -> None:
        labels = (
            "acceptance_without_handoff",
            "implementer_handoff_without_acceptance",
            "reviewer_acceptance_out_of_order",
            "completion_without_reviewer_acceptance",
            "designer_handoff_unknown_human_reference",
            "acceptance_missing_reference",
            "acceptance_unknown_reference",
            "acceptance_references_wrong_event",
            "acceptance_memory_reference_mismatch",
            "implementer_handoff_missing_accepted_reference",
            "reviewer_acceptance_references_wrong_event",
            "repeated_implementer_acceptance",
            "repeated_reviewer_acceptance",
            "repeated_terminal_completion",
            "non_string_session_ids",
            "empty_required_session_id",
            "invalid_actor_runtime",
            "non_string_actor_runtime",
            "implementer_handoff_actor_mismatch",
            "reviewer_completion_actor_mismatch",
            "implementer_handoff_profile_divergence",
            "implementer_handoff_dispatch_divergence",
            "implementer_handoff_artifact_divergence",
            "designer_handoff_missing_required_precode",
            "designer_handoff_malformed_snapshot_item",
            "completion_missing_postrun_reports",
            "closed_completion_report_not_passed",
        )
        for label in labels:
            with self.subTest(graph=label):
                with self.fixture_context(
                    f"legacy-invalid-{label}",
                    memory_mode="best_effort",
                ) as (fixture, ledger, memory_home, _scenario):
                    valid = self.publish_full_lifecycle(fixture)
                    if label == "acceptance_without_handoff":
                        candidate = [copy.deepcopy(valid[2])]
                    elif label == "implementer_handoff_without_acceptance":
                        candidate = copy.deepcopy(valid[:2] + [valid[3]])
                    elif label == "reviewer_acceptance_out_of_order":
                        candidate = copy.deepcopy([valid[0], valid[4]])
                    elif label == "completion_without_reviewer_acceptance":
                        candidate = copy.deepcopy(valid[:4] + [valid[5]])
                    elif label == "designer_handoff_unknown_human_reference":
                        candidate = copy.deepcopy(valid[:2])
                        candidate[-1]["human_acceptance"]["receipt_sha256"] = "f" * 64
                    elif label == "acceptance_missing_reference":
                        candidate = copy.deepcopy(valid[:3])
                        candidate[-1].pop("handoff_receipt_sha256")
                    elif label == "acceptance_unknown_reference":
                        candidate = copy.deepcopy(valid[:3])
                        candidate[-1]["handoff_receipt_sha256"] = "f" * 64
                    elif label == "acceptance_references_wrong_event":
                        candidate = copy.deepcopy(valid[:3])
                        candidate[-1]["handoff_receipt_sha256"] = valid[0][
                            "receipt_sha256"
                        ]
                    elif label == "acceptance_memory_reference_mismatch":
                        candidate = copy.deepcopy(valid[:3])
                        candidate[-1]["handoff_memory_id"] = "wrong-memory-id"
                    elif label == "implementer_handoff_missing_accepted_reference":
                        candidate = copy.deepcopy(valid[:4])
                        candidate[-1].pop("accepted_handoff_receipt_sha256")
                    elif label == "reviewer_acceptance_references_wrong_event":
                        candidate = copy.deepcopy(valid[:5])
                        candidate[-1]["handoff_receipt_sha256"] = valid[1][
                            "receipt_sha256"
                        ]
                    elif label == "repeated_implementer_acceptance":
                        candidate = copy.deepcopy(valid[:3] + [valid[2]])
                    elif label == "repeated_reviewer_acceptance":
                        candidate = copy.deepcopy(valid[:5] + [valid[4]])
                    elif label == "repeated_terminal_completion":
                        candidate = copy.deepcopy(valid + [valid[5]])
                    elif label == "non_string_session_ids":
                        candidate = copy.deepcopy(valid[:3])
                        candidate[1]["actor"]["session_id"] = 1
                        candidate[2]["actor"]["session_id"] = 2
                    elif label == "empty_required_session_id":
                        candidate = copy.deepcopy(valid[:2])
                        candidate[-1]["actor"]["session_id"] = ""
                    elif label == "invalid_actor_runtime":
                        candidate = copy.deepcopy(valid[:2])
                        candidate[-1]["actor"]["runtime"] = "untrusted-runtime"
                    elif label == "non_string_actor_runtime":
                        candidate = copy.deepcopy(valid[:2])
                        candidate[-1]["actor"]["runtime"] = 7
                    elif label == "implementer_handoff_actor_mismatch":
                        candidate = copy.deepcopy(valid[:4])
                        candidate[-1]["actor"]["session_id"] = (
                            "different-implementer-session"
                        )
                    elif label == "reviewer_completion_actor_mismatch":
                        candidate = copy.deepcopy(valid)
                        candidate[-1]["actor"]["session_id"] = (
                            "different-reviewer-session"
                        )
                    elif label == "implementer_handoff_profile_divergence":
                        candidate = copy.deepcopy(valid[:4])
                        candidate[-1]["profile"]["sha256"] = "e" * 64
                    elif label == "implementer_handoff_dispatch_divergence":
                        candidate = copy.deepcopy(valid[:4])
                        candidate[-1]["dispatch"] = {"unexpected": "replacement"}
                    elif label == "implementer_handoff_artifact_divergence":
                        candidate = copy.deepcopy(valid[:4])
                        candidate[-1]["artifacts"] = copy.deepcopy(
                            candidate[-1]["implementation_files"]
                        )
                    elif label == "designer_handoff_missing_required_precode":
                        candidate = copy.deepcopy(valid[:2])
                        candidate[-1]["artifacts"] = []
                    elif label == "designer_handoff_malformed_snapshot_item":
                        candidate = copy.deepcopy(valid[:2])
                        candidate[-1]["artifacts"][0]["unexpected"] = True
                    elif label == "completion_missing_postrun_reports":
                        candidate = copy.deepcopy(valid)
                        candidate[-1]["artifacts"] = copy.deepcopy(
                            candidate[-1]["run"]["evidence"]
                        )
                    else:
                        candidate = copy.deepcopy(valid)
                        report = next(
                            item
                            for item in candidate[-1]["artifacts"]
                            if item.get("gate_status") == "passed"
                        )
                        report["gate_status"] = "pending"

                    self.remove_registry(memory_home)
                    rewritten, chain = self.rewrite_as_legacy_history(
                        fixture,
                        candidate,
                    )
                    local_before = self.tree_snapshot(fixture.root)
                    writes_before = ledger.write_counts()
                    self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))
                    identity = rg.lineage_identity(fixture.artifact)

                    with self.assertRaisesRegex(
                        rg.RoleGovernanceError,
                        "lifecycle|predecessor|reference|required|acceptance|actor|runtime|session|artifact|profile|dispatch|completion|report|snapshot",
                    ):
                        rg.verify_chain(rg.load_context(fixture.artifact))
                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))

                    with self.assertRaisesRegex(
                        rg.RoleGovernanceError,
                        "lifecycle|predecessor|reference|required|acceptance|actor|runtime|session|artifact|profile|dispatch|completion|report|snapshot",
                    ):
                        rg.lineage_adopt(
                            fixture.artifact,
                            lineage_id=identity["lineage_id"],
                            expected_head=chain["head_sha256"],
                        )
                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))
                    self.assertEqual(
                        [receipt["receipt_sha256"] for receipt in rewritten],
                        [
                            json.loads(path.read_bytes())["receipt_sha256"]
                            for path in sorted(
                                (
                                    fixture.artifact
                                    / "00_role_evidence"
                                    / "receipts"
                                ).glob("*.json")
                            )
                        ],
                    )

    def test_required_legacy_adoption_rejects_non_private_modes_before_writes(self) -> None:
        mutations = {
            "receipt": lambda evidence: os.chmod(
                sorted((evidence / "receipts").glob("*.json"))[0],
                0o644,
            ),
            "chain": lambda evidence: os.chmod(
                evidence / "chain.json",
                0o666,
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(path=label):
                with self.fixture_context(f"legacy-adopt-mode-{label}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    valid = list(self.publish_sequence_two(fixture))
                    self.remove_registry(memory_home)
                    _rewritten, chain = self.rewrite_as_legacy_history(
                        fixture,
                        valid,
                    )
                    identity = rg.lineage_identity(fixture.artifact)
                    evidence = fixture.artifact / "00_role_evidence"
                    mutate(evidence)
                    local_before = self.tree_snapshot(fixture.root)
                    writes_before = ledger.write_counts()
                    roots_before = copy.deepcopy(ledger.roots)
                    checkpoints_before = copy.deepcopy(ledger.checkpoints)

                    with self.assertRaisesRegex(
                        rg.RoleGovernanceError,
                        r"lineage-adopt requires legacy .* mode 0600",
                    ):
                        rg.lineage_adopt(
                            fixture.artifact,
                            lineage_id=identity["lineage_id"],
                            expected_head=chain["head_sha256"],
                        )

                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    self.assertEqual(roots_before, ledger.roots)
                    self.assertEqual(checkpoints_before, ledger.checkpoints)
                    self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))

    def test_valid_v040_full_lifecycle_adopts_without_rewriting_receipts(self) -> None:
        with self.fixture_context(
            "legacy-valid-full-lifecycle",
            memory_mode="best_effort",
        ) as (fixture, ledger, memory_home, _scenario):
            valid = self.publish_full_lifecycle(fixture)
            self.remove_registry(memory_home)
            rewritten, chain = self.rewrite_as_legacy_history(fixture, valid)
            local_before = self.tree_snapshot(fixture.root)
            writes_before = ledger.write_counts()
            verified = rg.verify_chain(rg.load_context(fixture.artifact))
            self.assertEqual(6, len(verified))
            self.assertTrue(all("lineage" not in receipt for receipt in verified))
            identity = rg.lineage_identity(fixture.artifact)

            with mock.patch.object(
                rg,
                "_memory_verify",
                side_effect=AssertionError(
                    "best_effort lineage-adopt must not strict-verify Memory"
                ),
            ) as strict_verify:
                adopted = rg.lineage_adopt(
                    fixture.artifact,
                    lineage_id=identity["lineage_id"],
                    expected_head=chain["head_sha256"],
                )

            self.assertEqual("aligned", adopted["integrity_state"])
            self.assertEqual(0, strict_verify.call_count)
            self.assertEqual(0, adopted["receipts_rewritten"])
            self.assertEqual(local_before, self.tree_snapshot(fixture.root))
            self.assertEqual(writes_before, ledger.write_counts())
            record = rg.lineage_registry.LineageRegistry(memory_home).require_lineage(
                identity["lineage_id"]
            )
            self.assertEqual(6, record.sequence)
            self.assertEqual(chain["head_sha256"], record.head_sha256)
            self.assertEqual(
                [receipt["receipt_sha256"] for receipt in rewritten],
                [receipt["receipt_sha256"] for receipt in verified],
            )

    def test_required_legacy_adoption_exact_verifies_every_transition_first(self) -> None:
        with self.fixture_context("legacy-required-exact-memory") as (
            fixture,
            ledger,
            memory_home,
            _scenario,
        ):
            valid = self.publish_full_lifecycle(fixture)
            self.remove_registry(memory_home)
            rewritten, chain = self.rewrite_as_legacy_history(fixture, valid)
            identity = rg.lineage_identity(fixture.artifact)
            verified: list[str] = []

            def exact_verify(_ctx: object, receipt: dict) -> None:
                verified.append(receipt["receipt_sha256"])

            with mock.patch.object(rg, "_memory_verify", exact_verify):
                adopted = rg.lineage_adopt(
                    fixture.artifact,
                    lineage_id=identity["lineage_id"],
                    expected_head=chain["head_sha256"],
                )

            self.assertEqual("aligned", adopted["integrity_state"])
            self.assertEqual(
                [receipt["receipt_sha256"] for receipt in rewritten],
                verified,
            )

    def test_required_legacy_adoption_memory_failures_are_zero_write(self) -> None:
        for failure in ("forged", "missing", "outage"):
            with self.subTest(failure=failure):
                with self.fixture_context(f"legacy-memory-{failure}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    valid = self.publish_full_lifecycle(fixture)
                    if failure == "forged":
                        valid[0]["memory"]["memory_id"] = "forged-memory-anchor"
                    elif failure == "missing":
                        valid[0]["memory"]["memory_id"] = ""
                    self.remove_registry(memory_home)
                    rewritten, chain = self.rewrite_as_legacy_history(fixture, valid)
                    identity = rg.lineage_identity(fixture.artifact)
                    verify_calls: list[str] = []

                    def reject_anchor(_ctx: object, receipt: dict) -> None:
                        verify_calls.append(receipt["receipt_sha256"])
                        if failure == "outage":
                            raise rg.RoleGovernanceError(
                                "injected strict Memory verification outage"
                            )
                        memory_id = str(receipt.get("memory", {}).get("memory_id") or "")
                        if not memory_id:
                            raise rg.RoleGovernanceError(
                                "injected missing transition Memory anchor"
                            )
                        if memory_id == "forged-memory-anchor":
                            raise rg.RoleGovernanceError(
                                "injected forged transition Memory anchor"
                            )

                    local_before = self.tree_snapshot(fixture.root)
                    writes_before = ledger.write_counts()
                    roots_before = copy.deepcopy(ledger.roots)
                    checkpoints_before = copy.deepcopy(ledger.checkpoints)
                    self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))

                    with mock.patch.object(rg, "_memory_verify", reject_anchor):
                        with self.assertRaisesRegex(
                            rg.RoleGovernanceError,
                            "strict Memory|transition Memory anchor",
                        ):
                            rg.lineage_adopt(
                                fixture.artifact,
                                lineage_id=identity["lineage_id"],
                                expected_head=chain["head_sha256"],
                            )

                    self.assertEqual(
                        [rewritten[0]["receipt_sha256"]],
                        verify_calls,
                    )
                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    self.assertEqual(roots_before, ledger.roots)
                    self.assertEqual(checkpoints_before, ledger.checkpoints)
                    self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))

    def test_runtime_drift_publishers_fail_before_any_durable_write(self) -> None:
        for transition in ("implementer_handoff", "reviewer_completion"):
            with self.subTest(transition=transition):
                with self.fixture_context(f"runtime-drift-{transition}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    approval, designer_handoff = self.publish_sequence_two(fixture)
                    del approval
                    with mock.patch.dict(
                        os.environ,
                        {"BUGATE_AGENT_RUNTIME": "codex"},
                    ):
                        with role_env("implementer", "implementer-runtime-session"):
                            rg.accept(
                                fixture.artifact,
                                phase="implementation",
                                handoff_id=designer_handoff["memory"]["memory_id"],
                            )
                    fixture.implementation.write_text(
                        "def test_fixture():\n    assert True\n",
                        encoding="utf-8",
                    )

                    if transition == "reviewer_completion":
                        with mock.patch.dict(
                            os.environ,
                            {"BUGATE_AGENT_RUNTIME": "codex"},
                        ):
                            with role_env(
                                "implementer", "implementer-runtime-session"
                            ):
                                implementer_handoff = rg.handoff(
                                    fixture.artifact,
                                    phase="implementation",
                                    to_role="reviewer",
                                    implementation_files=[fixture.implementation],
                                )
                            with role_env("reviewer", "reviewer-runtime-session"):
                                rg.accept(
                                    fixture.artifact,
                                    phase="post_run",
                                    handoff_id=implementer_handoff["memory"]["memory_id"],
                                )
                        for name in sorted(rg.POSTRUN_NAMES):
                            (fixture.artifact / name).write_text(
                                "gate_status: passed\nfixture: post-run\n",
                                encoding="utf-8",
                            )
                        evidence = fixture.artifact / "execution.log"
                        evidence.write_text("fixture execution\n", encoding="utf-8")

                    local_before = self.tree_snapshot(fixture.root)
                    record_before, transactions_before = self.registry_state(
                        fixture, memory_home
                    )
                    writes_before = ledger.write_counts()
                    roots_before = copy.deepcopy(ledger.roots)
                    checkpoints_before = copy.deepcopy(ledger.checkpoints)

                    with mock.patch.dict(
                        os.environ,
                        {"BUGATE_AGENT_RUNTIME": "claude"},
                    ):
                        if transition == "implementer_handoff":
                            publish = lambda: rg.handoff(
                                fixture.artifact,
                                phase="implementation",
                                to_role="reviewer",
                                implementation_files=[fixture.implementation],
                            )
                            role, session = (
                                "implementer",
                                "implementer-runtime-session",
                            )
                        else:
                            publish = lambda: rg.complete(
                                fixture.artifact,
                                phase="post_run",
                                run_command="fixture runner",
                                exit_code=0,
                                evidence_files=[evidence],
                                final_gate_status="passed",
                            )
                            role, session = "reviewer", "reviewer-runtime-session"
                        with role_env(role, session):
                            with self.assertRaisesRegex(
                                rg.RoleGovernanceError,
                                "actor|runtime|acceptance",
                            ):
                                publish()

                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    self.assertEqual(roots_before, ledger.roots)
                    self.assertEqual(checkpoints_before, ledger.checkpoints)
                    record_after, transactions_after = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(record_before, record_after)
                    self.assertEqual(transactions_before, transactions_after)

    def test_duplicate_completion_evidence_identity_is_zero_write(self) -> None:
        for alias_kind in ("same_path", "symlink", "hardlink"):
            with self.subTest(alias=alias_kind):
                with self.fixture_context(f"duplicate-evidence-{alias_kind}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    _approval, designer_handoff = self.publish_sequence_two(fixture)
                    with role_env("implementer", "implementer-evidence-session"):
                        rg.accept(
                            fixture.artifact,
                            phase="implementation",
                            handoff_id=designer_handoff["memory"]["memory_id"],
                        )
                        fixture.implementation.write_text(
                            "def test_fixture():\n    assert True\n",
                            encoding="utf-8",
                        )
                        implementer_handoff = rg.handoff(
                            fixture.artifact,
                            phase="implementation",
                            to_role="reviewer",
                            implementation_files=[fixture.implementation],
                        )
                    with role_env("reviewer", "reviewer-evidence-session"):
                        rg.accept(
                            fixture.artifact,
                            phase="post_run",
                            handoff_id=implementer_handoff["memory"]["memory_id"],
                        )
                    for name in sorted(rg.POSTRUN_NAMES):
                        (fixture.artifact / name).write_text(
                            "gate_status: passed\nfixture: post-run\n",
                            encoding="utf-8",
                        )
                    evidence = fixture.artifact / "execution.log"
                    evidence.write_text("fixture execution\n", encoding="utf-8")
                    alias = fixture.root / f"{alias_kind}-execution.log"
                    if alias_kind == "same_path":
                        supplied = [evidence, evidence]
                    elif alias_kind == "symlink":
                        alias.symlink_to(evidence)
                        supplied = [evidence, alias]
                    else:
                        os.link(evidence, alias)
                        supplied = [evidence, alias]

                    local_before = self.tree_snapshot(fixture.root)
                    record_before, transactions_before = self.registry_state(
                        fixture, memory_home
                    )
                    writes_before = ledger.write_counts()
                    roots_before = copy.deepcopy(ledger.roots)
                    checkpoints_before = copy.deepcopy(ledger.checkpoints)

                    with role_env("reviewer", "reviewer-evidence-session"):
                        with self.assertRaisesRegex(
                            rg.RoleGovernanceError,
                            "duplicate|filesystem identity|evidence",
                        ):
                            rg.complete(
                                fixture.artifact,
                                phase="post_run",
                                run_command="fixture runner",
                                exit_code=0,
                                evidence_files=supplied,
                                final_gate_status="passed",
                            )

                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(writes_before, ledger.write_counts())
                    self.assertEqual(roots_before, ledger.roots)
                    self.assertEqual(checkpoints_before, ledger.checkpoints)
                    record_after, transactions_after = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(record_before, record_after)
                    self.assertEqual(record_before.head_sha256, record_after.head_sha256)
                    self.assertEqual(transactions_before, transactions_after)

    def test_generic_publish_candidate_validation_precedes_pending_write(self) -> None:
        with self.fixture_context("invalid-publication-candidate") as (
            fixture,
            ledger,
            memory_home,
            _scenario,
        ):
            ctx = rg.load_context(fixture.artifact)
            invalid_base = {
                "event": "human_acceptance",
                "phase": "pre_code",
                "from_role": "human",
                "to_role": "designer",
                "actor": {
                    "role": "designer",
                    "runtime": "unknown",
                    "session_id": "candidate-session",
                },
                "profile": rg.profile_snapshot(ctx),
                "artifacts": [],
                "dispatch": {},
                "human_acceptance": {"required": True},
                "approved_by": "qa-owner",
                "decision": "accepted",
            }
            local_before = self.tree_snapshot(fixture.root)
            record_before, transactions_before = self.registry_state(
                fixture, memory_home
            )
            writes_before = ledger.write_counts()

            with rg._transition_lock(ctx):
                with self.assertRaisesRegex(
                    rg.RoleGovernanceError,
                    "artifact inventory|profile provenance|required",
                ):
                    rg._publish(
                        ctx,
                        invalid_base,
                        rg.EVENT_STATES["human_acceptance"],
                    )

            self.assertEqual(local_before, self.tree_snapshot(fixture.root))
            self.assertEqual(writes_before, ledger.write_counts())
            record_after, transactions_after = self.registry_state(
                fixture, memory_home
            )
            self.assertEqual(record_before, record_after)
            self.assertEqual(transactions_before, transactions_after)

    def test_strict_root_prevents_empty_reinit_after_workspace_and_registry_loss(self) -> None:
        with self.fixture_context("strict-root-reset-guard") as (
            fixture,
            ledger,
            memory_home,
            _scenario,
        ):
            _approval, handoff = self.publish_sequence_two(fixture)
            identity = rg.lineage_identity(fixture.artifact)
            self.assertIn(identity["lineage_id"], ledger.roots)
            ensure_calls_before = tuple(ledger.root_ensure_calls)
            writes_before = ledger.write_counts()

            shutil.rmtree(fixture.artifact / "00_role_evidence")
            for path in memory_home.glob("role-lineage.sqlite3*"):
                path.unlink()
            after_loss = self.tree_snapshot(fixture.root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = rg.main(
                    ["lineage-status", str(fixture.artifact), "--json"]
                )
            status = json.loads(stdout.getvalue())
            self.assertEqual(2, exit_code)
            self.assertFalse(status["ok"])
            self.assertEqual("migration_required", status["integrity_state"])
            self.assertEqual(identity["lineage_id"], status["lineage_id"])
            self.assertEqual(
                ledger.roots[identity["lineage_id"]]["lineage_root_id"],
                status["lineage_root_memory_id"],
            )

            with self.assertRaisesRegex(
                rg.RoleGovernanceError,
                "existing deterministic strict Memory root",
            ):
                rg.lineage_init(
                    fixture.artifact,
                    lineage_id=identity["lineage_id"],
                )

            self.assertEqual(after_loss, self.tree_snapshot(fixture.root))
            self.assertEqual(ensure_calls_before, tuple(ledger.root_ensure_calls))
            self.assertEqual(writes_before, ledger.write_counts())
            registry = rg.lineage_registry.LineageRegistry(memory_home)
            context = rg.load_context(fixture.artifact)
            key = rg._lineage_key(context)
            self.assertIsNone(registry.get_lineage(key))
            self.assertEqual([], registry.list_active_initializations(key))
            self.assertEqual(
                handoff["receipt_sha256"],
                ledger.checkpoints[
                    next(reversed(ledger.checkpoints))
                ]["payload"]["receipt_sha256"],
            )

    def test_lineage_init_crash_windows_resume_the_exact_first_use_intent(self) -> None:
        expected_stages = {
            "after_lineage_init_intent": "pending",
            "after_lineage_init_root_absence_probe": "pending",
            "after_lineage_init_root_absence_journal": "root_absence_verified",
            "after_lineage_init_root_http": "root_absence_verified",
            "after_lineage_init_root_bind": "root_verified",
            "after_lineage_init_registry_commit": "registry_initialized",
            "after_lineage_init_chain_write": "registry_initialized",
            "after_lineage_init_chain_journal": "chain_written",
        }
        for failure_name, expected_stage in expected_stages.items():
            with self.subTest(failure_point=failure_name):
                scenario = self.root / f"lineage-init-crash-{failure_name}"
                workspace = scenario / "workspace"
                memory_home = scenario / "memory-home"
                ledger = FakeMemoryLedger()
                with self.activated(workspace, memory_home, ledger):
                    fixture = Fixture(
                        workspace,
                        memory_mode="required",
                        initialize_lineage=False,
                    )
                    identity = rg.lineage_identity(fixture.artifact)

                    def fail_at(name: str, ctx: object, init_id: str) -> None:
                        del ctx, init_id
                        if name == failure_name:
                            raise RuntimeError(f"injected init crash at {name}")

                    with mock.patch.object(rg, "_failure_point", fail_at):
                        with self.assertRaisesRegex(RuntimeError, failure_name):
                            rg.lineage_init(
                                fixture.artifact,
                                lineage_id=identity["lineage_id"],
                            )

                    pending = rg.status_data(fixture.artifact)
                    self.assertFalse(pending["ok"])
                    self.assertEqual("recovery_pending", pending["integrity_state"])
                    self.assertEqual(
                        expected_stage,
                        pending["active_initialization"]["stage"],
                    )
                    registry = rg.lineage_registry.LineageRegistry(memory_home)
                    active = registry.list_active_initializations(identity["lineage_id"])
                    self.assertEqual(1, len(active))
                    init_id = active[0].init_id

                    result = rg.lineage_init(
                        fixture.artifact,
                        lineage_id=identity["lineage_id"],
                    )
                    self.assertTrue(result["ok"])
                    self.assertEqual(identity["lineage_id"], result["lineage_id"])
                    final = rg.status_data(fixture.artifact)
                    self.assertTrue(final["ok"], final)
                    self.assertEqual("aligned", final["integrity_state"])
                    self.assertEqual(0, final["sequence"])
                    self.assertEqual({}, final["latest_receipts"])
                    self.assertIsNone(final["active_initialization"])
                    completed = registry.get_initialization(init_id)
                    self.assertIsNotNone(completed)
                    self.assertEqual("completed", completed.status)
                    self.assertEqual("completed", completed.stage)
                    chain = fixture.artifact / "00_role_evidence" / "chain.json"
                    self.assertEqual(rg._json_bytes(rg._empty_chain()), chain.read_bytes())
                    self.assertEqual(0o600, stat.S_IMODE(chain.stat().st_mode))
                    self.assertEqual([], list((chain.parent / "receipts").glob("*.json")))
                    self.assertEqual(1, len(ledger.roots))
                    self.assertTrue(
                        all(
                            lineage_id == identity["lineage_id"]
                            for lineage_id in ledger.root_ensure_calls
                        )
                    )
                    self.assertEqual(
                        2 if failure_name == "after_lineage_init_root_http" else 1,
                        len(ledger.root_ensure_calls),
                    )

    def test_strict_memory_prepare_and_checkpoint_outages_leave_recovery_pending(self) -> None:
        scenarios = {
            "prepare": ("pending", True, False),
            "checkpoint": ("memory_finalized", False, True),
        }
        for label, (expected_stage, fail_prepare, fail_checkpoint) in scenarios.items():
            with self.subTest(outage=label):
                with self.fixture_context(f"outage-{label}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    local_before = self.tree_snapshot(fixture.root)
                    record_before, active_before = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual((), active_before)
                    counts_before = ledger.write_counts()
                    ledger.fail_prepare = fail_prepare
                    ledger.fail_checkpoint = fail_checkpoint
                    with role_env("designer", f"designer-{label}-outage"):
                        with self.assertRaises(rg.RoleGovernanceError):
                            rg.approve(fixture.artifact, approved_by="qa-owner")

                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    record_after, active_after = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(record_before, record_after)
                    self.assertEqual(1, len(active_after))
                    self.assertEqual(expected_stage, active_after[0].stage)
                    self.assertEqual("pending", active_after[0].status)
                    status = rg.status_data(fixture.artifact)
                    self.assertEqual("recovery_pending", status["integrity_state"])
                    self.assertEqual("", status["registry_head_sha256"])
                    self.assertEqual(0, status["registry_sequence"])
                    self.assertEqual(0, status["registry_revision"])
                    self.assertEqual("", record_after.checkpoint_memory_id)
                    self.assertEqual({}, ledger.checkpoints)
                    self.assertEqual(counts_before[1] + 1, len(ledger.prepare_calls))
                    self.assertEqual(
                        counts_before[2] + (0 if fail_prepare else 1),
                        len(ledger.finalize_calls),
                    )
                    self.assertEqual(
                        counts_before[3] + (1 if fail_checkpoint else 0),
                        len(ledger.checkpoint_create_calls),
                    )

    def test_strict_publish_exact_verifies_root_and_predecessor_after_pending(self) -> None:
        for corruption in ("root_missing", "predecessor_diverged"):
            with self.subTest(corruption=corruption):
                with self.fixture_context(f"strict-predecessor-{corruption}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    if corruption == "predecessor_diverged":
                        with role_env("designer", "designer-predecessor-base"):
                            rg.approve(fixture.artifact, approved_by="qa-owner")
                        record_before, _active = self.registry_state(
                            fixture, memory_home
                        )
                        remote = ledger.checkpoints[record_before.checkpoint_memory_id]
                        remote["payload"]["resulting_state"] = "closed"
                        publish = lambda: rg.handoff(
                            fixture.artifact,
                            phase="pre_code",
                            to_role="implementer",
                        )
                    else:
                        record_before, _active = self.registry_state(
                            fixture, memory_home
                        )
                        ledger.roots.clear()
                        publish = lambda: rg.approve(
                            fixture.artifact,
                            approved_by="qa-owner",
                        )

                    local_before = self.tree_snapshot(fixture.root)
                    prepares_before = len(ledger.prepare_calls)
                    checkpoints_before = len(ledger.checkpoint_create_calls)
                    with role_env("designer", f"designer-{corruption}"):
                        with self.assertRaises(rg.RoleGovernanceError):
                            publish()

                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    record_after, active_after = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(record_before, record_after)
                    self.assertEqual(1, len(active_after))
                    self.assertEqual("pending", active_after[0].stage)
                    self.assertEqual(prepares_before, len(ledger.prepare_calls))
                    self.assertEqual(
                        checkpoints_before,
                        len(ledger.checkpoint_create_calls),
                    )
                    status = rg.status_data(fixture.artifact)
                    self.assertFalse(status["ok"])
                    self.assertEqual("recovery_pending", status["integrity_state"])

    def test_strict_resume_reverifies_authority_for_every_pre_cas_stage(self) -> None:
        for corruption in ("root_missing", "predecessor_missing"):
            with self.subTest(corruption=corruption):
                with self.fixture_context(f"strict-resume-{corruption}") as (
                    fixture,
                    ledger,
                    memory_home,
                    scenario,
                ):
                    with role_env("designer", "designer-resume-base"):
                        approval = rg.approve(
                            fixture.artifact,
                            approved_by="qa-owner",
                        )
                    record_before, _active = self.registry_state(
                        fixture,
                        memory_home,
                    )
                    archive = scenario / "archives" / "base.json"
                    self.write_archive(
                        fixture,
                        archive,
                        lineage_id=record_before.lineage_id,
                        expected_head=record_before.head_sha256,
                    )

                    def fail_after_memory(name: str, ctx: object, tx_id: str) -> None:
                        del ctx, tx_id
                        if name == "after_memory_transition":
                            raise RuntimeError("injected pre-CAS crash")

                    with role_env("designer", "designer-resume-handoff"), mock.patch.object(
                        rg,
                        "_failure_point",
                        fail_after_memory,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "pre-CAS crash"):
                            rg.handoff(
                                fixture.artifact,
                                phase="pre_code",
                                to_role="implementer",
                            )

                    _record, active = self.registry_state(fixture, memory_home)
                    self.assertEqual(1, len(active))
                    self.assertEqual("memory_prepared", active[0].stage)
                    if corruption == "root_missing":
                        ledger.roots.clear()
                    else:
                        del ledger.checkpoints[record_before.checkpoint_memory_id]
                    local_before = self.tree_snapshot(fixture.root)
                    prepares_before = len(ledger.prepare_calls)
                    checkpoint_writes_before = len(ledger.checkpoint_create_calls)

                    with self.assertRaises(rg.RoleGovernanceError):
                        rg.recover(
                            fixture.artifact,
                            lineage_id=record_before.lineage_id,
                            expected_head=approval["receipt_sha256"],
                            archive=archive,
                        )

                    self.assertEqual(local_before, self.tree_snapshot(fixture.root))
                    record_after, active_after = self.registry_state(
                        fixture,
                        memory_home,
                    )
                    self.assertEqual(record_before, record_after)
                    self.assertEqual(1, len(active_after))
                    self.assertEqual("pending", active_after[0].status)
                    self.assertEqual("memory_prepared", active_after[0].stage)
                    self.assertEqual(prepares_before, len(ledger.prepare_calls))
                    self.assertEqual(
                        checkpoint_writes_before,
                        len(ledger.checkpoint_create_calls),
                    )

    def test_path_bearing_publish_error_is_sanitized_and_recoverable(self) -> None:
        with self.fixture_context("sanitized-publish-error") as (
            fixture,
            _ledger,
            memory_home,
            scenario,
        ):
            leaked_path = scenario / "operator-secret" / "source.json"

            def fail_with_path(name: str, ctx: object, tx_id: str) -> None:
                del ctx, tx_id
                if name == "after_memory_prepare_http":
                    raise RuntimeError(f"cannot read {leaked_path}")

            with role_env("designer", "designer-sanitized-error"), mock.patch.object(
                rg, "_failure_point", fail_with_path
            ):
                with self.assertRaisesRegex(RuntimeError, "operator-secret"):
                    rg.approve(fixture.artifact, approved_by="qa-owner")

            _record, active = self.registry_state(fixture, memory_home)
            self.assertEqual(1, len(active))
            self.assertEqual("pending", active[0].stage)
            self.assertTrue(active[0].error)
            self.assertNotIn(str(scenario), active[0].error)
            self.assertNotIn("operator-secret", active[0].error)
            self.assertEqual("recovery_pending", rg.status_data(fixture.artifact)["integrity_state"])

    def test_best_effort_recovery_preserves_durable_unanchored_decision(self) -> None:
        with self.fixture_context(
            "best-effort-availability-flip",
            memory_mode="best_effort",
        ) as (fixture, ledger, memory_home, _scenario):
            def unavailable(
                ctx: rg.GovernanceContext,
                transition: dict[str, object],
            ) -> dict[str, object]:
                del transition
                return {
                    "namespace": rg._memory_namespace(ctx),
                    "memory_id": "",
                    "verified_at": "",
                    "status": "best_effort_unavailable",
                }

            def fail_after_durable_marker(name: str, ctx: object, tx_id: str) -> None:
                del ctx, tx_id
                if name == "after_memory_transition":
                    raise RuntimeError("injected best-effort crash")

            with role_env("designer", "designer-best-effort-flip"), mock.patch.object(
                rg, "_memory_prepare", unavailable
            ), mock.patch.object(rg, "_failure_point", fail_after_durable_marker):
                with self.assertRaisesRegex(RuntimeError, "best-effort crash"):
                    rg.approve(fixture.artifact, approved_by="qa-owner")

            pending = rg.status_data(fixture.artifact)
            self.assertEqual("recovery_pending", pending["integrity_state"])
            _record, active = self.registry_state(fixture, memory_home)
            self.assertEqual(1, len(active))
            self.assertEqual("memory_prepared", active[0].stage)
            self.assertEqual("", active[0].transition_memory_id)
            replayed_original_calls = 0

            def available_only_for_recovery_receipt(
                ctx: rg.GovernanceContext,
                transition: dict[str, object],
            ) -> dict[str, object]:
                nonlocal replayed_original_calls
                if transition.get("event") == "human_acceptance":
                    replayed_original_calls += 1
                    raise AssertionError(
                        "durable best-effort unanchored transition was replayed over HTTP"
                    )
                return ledger.prepare(ctx, transition)

            with mock.patch.object(
                rg,
                "_memory_prepare",
                available_only_for_recovery_receipt,
            ):
                recovered = rg.recover(
                    fixture.artifact,
                    lineage_id=pending["lineage_id"],
                    expected_head="EMPTY",
                )

            self.assertEqual(0, replayed_original_calls)
            receipts = rg.verify_chain(rg.load_context(fixture.artifact))
            self.assertEqual(["human_acceptance", "evidence_recovery"], [
                receipt["event"] for receipt in receipts
            ])
            self.assertEqual(
                {
                    "namespace": "project:fixture",
                    "memory_id": "",
                    "verified_at": "",
                    "status": "best_effort_unavailable",
                },
                receipts[0]["memory"],
            )
            self.assertEqual(
                "ready_for_designer_handoff",
                recovered["recovery_receipt"]["resulting_state"],
            )
            final = rg.status_data(fixture.artifact)
            self.assertTrue(final["ok"], final)
            self.assertEqual(2, final["sequence"])

    def test_status_serializes_lineage_identity_validation_failures(self) -> None:
        with self.fixture_context("status-lineage-validation") as (
            fixture,
            _ledger,
            _memory_home,
            _scenario,
        ):
            with mock.patch.object(
                rg,
                "_lineage_key",
                side_effect=rg.lineage_registry.RoleLineageError(
                    "synthetic malformed lineage identity"
                ),
            ):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    exit_code = rg.main(["status", str(fixture.artifact), "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(2, exit_code)
            self.assertFalse(payload["ok"])
            self.assertEqual("registry_unavailable", payload["integrity_state"])
            self.assertIn("malformed lineage identity", payload["error"])

    def test_status_rejects_absolute_like_namespace_as_machine_readable_json(self) -> None:
        scenario = self.root / "absolute-like-namespace"
        workspace = scenario / "workspace"
        memory_home = scenario / "memory-home"
        ledger = FakeMemoryLedger()
        with self.activated(workspace, memory_home, ledger):
            fixture = Fixture(
                workspace,
                memory_mode="required",
                initialize_lineage=False,
            )
            profile = workspace / "bugate.profile.yaml"
            profile.write_text(
                profile.read_text(encoding="utf-8").replace(
                    "namespace: project:fixture",
                    "namespace: /private/machine-specific",
                ),
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = rg.main(["status", str(fixture.artifact), "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(2, exit_code)
            self.assertFalse(payload["ok"])
            self.assertEqual("registry_unavailable", payload["integrity_state"])
            self.assertIn("namespace", payload["error"])
            self.assertFalse(any(memory_home.glob("role-lineage.sqlite3*")))

    def test_every_publish_crash_window_is_detected_and_exactly_recoverable(self) -> None:
        failure_stages = {
            "after_memory_prepare_http": ("pending", False),
            "after_memory_transition": ("memory_prepared", False),
            "after_memory_finalize_http": ("memory_prepared", False),
            "after_receipt_bind": ("receipt_bound", False),
            "after_checkpoint_http": ("memory_finalized", False),
            "after_checkpoint": ("checkpoint_verified", False),
            "after_registry_cas": ("registry_committed", True),
            "after_receipt_write": ("registry_committed", True),
            "before_chain_replace": ("receipt_written", True),
            "after_chain_replace": ("receipt_written", True),
        }
        for failure_name, (expected_stage, cas_committed) in failure_stages.items():
            with self.subTest(failure_point=failure_name):
                with self.fixture_context(f"publish-crash-{failure_name}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    def fail_at(name: str, ctx: object, tx_id: str) -> None:
                        del ctx, tx_id
                        if name == failure_name:
                            raise RuntimeError(f"injected crash at {name}")

                    with role_env("designer", f"designer-{failure_name}"), mock.patch.object(
                        rg, "_failure_point", fail_at
                    ):
                        with self.assertRaisesRegex(RuntimeError, failure_name):
                            rg.approve(fixture.artifact, approved_by="qa-owner")

                    status = rg.status_data(fixture.artifact)
                    self.assertFalse(status["ok"])
                    self.assertEqual("recovery_pending", status["integrity_state"])
                    self.assertEqual(expected_stage, status["active_transaction"]["stage"])
                    record, active = self.registry_state(fixture, memory_home)
                    self.assertEqual(1, len(active))
                    transaction = active[0]
                    self.assertEqual(expected_stage, transaction.stage)
                    self.assertEqual(1 if cas_committed else 0, record.sequence)
                    self.assertEqual(1 if cas_committed else 0, record.revision)
                    self.assertEqual(
                        transaction.target_head_sha256 if cas_committed else "",
                        record.head_sha256,
                    )
                    if cas_committed:
                        self.assertIsNotNone(transaction.receipt_bytes)
                        self.assertEqual(0o600, transaction.receipt_mode)
                    checkpoint_ids_before_recovery = set(ledger.checkpoints)

                    recovered = rg.recover(
                        fixture.artifact,
                        lineage_id=status["lineage_id"],
                        expected_head=status["registry_head_sha256"] or "EMPTY",
                    )
                    recovery_receipt = recovered["recovery_receipt"]
                    self.assertEqual("evidence_recovery", recovery_receipt["event"])
                    self.assertEqual(
                        "ready_for_designer_handoff",
                        recovery_receipt["resulting_state"],
                    )
                    final_status = rg.status_data(fixture.artifact)
                    self.assertTrue(final_status["ok"], final_status)
                    self.assertEqual("aligned", final_status["integrity_state"])
                    self.assertEqual(2, final_status["sequence"])
                    self.assertEqual(final_status["sequence"], final_status["registry_sequence"])
                    self.assertEqual(final_status["sequence"], final_status["registry_revision"])
                    final_record, final_active = self.registry_state(fixture, memory_home)
                    self.assertEqual((), final_active)
                    self.assertEqual(final_status["head_sha256"], final_record.head_sha256)
                    verified = rg.verify_chain(rg.load_context(fixture.artifact))
                    self.assertEqual(final_status["sequence"], len(verified))
                    completed_transaction = rg.lineage_registry.LineageRegistry(
                        memory_home
                    ).get_transaction(transaction.tx_id)
                    self.assertIsNotNone(completed_transaction)
                    attempted_path = fixture.root / completed_transaction.receipt_path
                    self.assertEqual(
                        completed_transaction.receipt_bytes,
                        attempted_path.read_bytes(),
                    )
                    self.assertEqual(
                        completed_transaction.receipt_mode,
                        stat.S_IMODE(attempted_path.stat().st_mode),
                    )
                    self.assertEqual(1, len(ledger.root_ensure_calls))
                    self.assertGreaterEqual(len(ledger.root_probe_calls), 2)
                    self.assertEqual(
                        [1, 2],
                        sorted(
                            int(item["sequence"])
                            for item in ledger.checkpoints.values()
                        ),
                    )
                    self.assertEqual(2, len(ledger.checkpoint_create_calls))
                    if failure_name in {
                        "after_checkpoint_http",
                        "after_checkpoint",
                    }:
                        self.assertTrue(checkpoint_ids_before_recovery)
                        self.assertTrue(
                            checkpoint_ids_before_recovery.issubset(ledger.checkpoints)
                        )

    def test_recovery_write_crash_windows_are_retryable_without_byte_drift(self) -> None:
        for failure_name in ("recovery_receipt_write", "recovery_chain_replace"):
            with self.subTest(failure_point=failure_name):
                with self.fixture_context(f"recover-crash-{failure_name}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    _approval, handoff = self.publish_sequence_two(fixture)
                    original_receipts, original_chain = self.captured_history(fixture)
                    shutil.rmtree(fixture.artifact / "00_role_evidence")
                    status = rg.status_data(fixture.artifact)
                    record_before, _active = self.registry_state(fixture, memory_home)
                    writes_before = ledger.write_counts()

                    def fail_at(name: str, ctx: object, tx_id: str) -> None:
                        del ctx, tx_id
                        if name == failure_name:
                            raise RuntimeError(f"injected crash at {name}")

                    with mock.patch.object(rg, "_failure_point", fail_at):
                        with self.assertRaisesRegex(RuntimeError, failure_name):
                            rg.recover(
                                fixture.artifact,
                                lineage_id=status["lineage_id"],
                                expected_head=handoff["receipt_sha256"],
                            )
                    pending = rg.status_data(fixture.artifact)
                    self.assertEqual("recovery_pending", pending["integrity_state"])
                    record_pending, active_pending = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(record_before, record_pending)
                    self.assertEqual(1, len(active_pending))
                    self.assertEqual(writes_before, ledger.write_counts())

                    recovered = rg.recover(
                        fixture.artifact,
                        lineage_id=status["lineage_id"],
                        expected_head=handoff["receipt_sha256"],
                    )
                    self.assertEqual(
                        "awaiting_implementer_acceptance",
                        recovered["recovery_receipt"]["resulting_state"],
                    )
                    self.assert_original_receipts_exact(fixture, original_receipts)
                    final = rg.status_data(fixture.artifact)
                    self.assertTrue(final["ok"], final)
                    self.assertEqual(3, final["sequence"])
                    self.assertEqual(3, final["registry_revision"])
                    self.assertEqual((), self.registry_state(fixture, memory_home)[1])
                    self.assertEqual(6, len(ledger.root_probe_calls))
                    self.assertEqual(8, len(ledger.checkpoint_get_calls))
                    self.assertEqual(3, len(ledger.prepare_calls))
                    self.assertEqual(3, len(ledger.finalize_calls))
                    self.assertEqual(3, len(ledger.checkpoint_create_calls))
                    checkpoint_two = ledger.checkpoints[
                        record_before.checkpoint_memory_id
                    ]["payload"]
                    self.assertEqual(
                        original_chain[0],
                        base64.b64decode(
                            checkpoint_two["chain_envelope"]["bytes_base64"]
                        ),
                    )

    def test_hard_crash_after_atomic_recovery_handoff_resumes_one_successor(self) -> None:
        if not hasattr(os, "fork"):
            self.skipTest("hard-crash recovery acceptance requires POSIX fork")
        with self.fixture_context("atomic-recovery-successor-hard-crash") as (
            fixture,
            ledger,
            memory_home,
            _scenario,
        ):
            _approval, handoff = self.publish_sequence_two(fixture)
            original_receipts, _original_chain = self.captured_history(fixture)
            record_before, active_before = self.registry_state(fixture, memory_home)
            self.assertEqual((), active_before)
            shutil.rmtree(fixture.artifact / "00_role_evidence")
            missing = rg.status_data(fixture.artifact)
            self.assertEqual("history_missing", missing["integrity_state"])

            child_pid = os.fork()
            if child_pid == 0:  # pragma: no cover - assertions execute in parent
                def hard_crash(name: str, ctx: object, tx_id: str) -> None:
                    del ctx, tx_id
                    if name == "after_recovery_successor_handoff":
                        os._exit(79)

                try:
                    with mock.patch.object(rg, "_failure_point", hard_crash):
                        rg.recover(
                            fixture.artifact,
                            lineage_id=missing["lineage_id"],
                            expected_head=handoff["receipt_sha256"],
                        )
                except BaseException:
                    os._exit(78)
                os._exit(77)

            waited_pid, wait_status = os.waitpid(child_pid, 0)
            self.assertEqual(child_pid, waited_pid)
            self.assertEqual(79, os.waitstatus_to_exitcode(wait_status))

            pending = rg.status_data(fixture.artifact)
            self.assertEqual("recovery_pending", pending["integrity_state"])
            self.assertEqual("evidence_recovery", pending["active_transaction"]["event"])
            self.assertEqual("pending", pending["active_transaction"]["stage"])
            record_pending, active_pending = self.registry_state(
                fixture, memory_home
            )
            self.assertEqual(record_before, record_pending)
            self.assertEqual(1, len(active_pending))
            self.assertEqual("evidence_recovery", active_pending[0].event)
            self.assertEqual("pending", active_pending[0].status)

            connection = sqlite3.connect(memory_home / "role-lineage.sqlite3")
            try:
                recovery_rows = connection.execute(
                    """
                    SELECT event, status, stage, count(*)
                    FROM lineage_transactions
                    WHERE event IN ('recovery_restore', 'evidence_recovery')
                    GROUP BY event, status, stage
                    ORDER BY event
                    """
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(
                [
                    ("evidence_recovery", "pending", "pending", 1),
                    ("recovery_restore", "aborted", "aborted", 1),
                ],
                recovery_rows,
            )

            recovered = rg.recover(
                fixture.artifact,
                lineage_id=missing["lineage_id"],
                expected_head=record_pending.head_sha256,
            )
            self.assertEqual("evidence_recovery", recovered["recovery_receipt"]["event"])
            self.assertEqual(
                "awaiting_implementer_acceptance",
                recovered["recovery_receipt"]["resulting_state"],
            )
            self.assert_original_receipts_exact(fixture, original_receipts)
            receipts = rg.verify_chain(rg.load_context(fixture.artifact))
            self.assertEqual(
                ["human_acceptance", "designer_handoff", "evidence_recovery"],
                [receipt["event"] for receipt in receipts],
            )
            self.assertEqual(
                1,
                sum(receipt["event"] == "evidence_recovery" for receipt in receipts),
            )
            final_record, final_active = self.registry_state(fixture, memory_home)
            self.assertEqual((), final_active)
            self.assertEqual(3, final_record.sequence)
            self.assertEqual(3, final_record.revision)
            self.assertEqual(3, len(ledger.checkpoint_create_calls))

    def test_recovery_successor_crash_matrix_never_duplicates_audit_event(self) -> None:
        failure_stages = {
            "after_memory_prepare_http": ("pending", False),
            "after_memory_transition": ("memory_prepared", False),
            "after_memory_finalize_http": ("memory_prepared", False),
            "after_receipt_bind": ("receipt_bound", False),
            "after_checkpoint_http": ("memory_finalized", False),
            "after_checkpoint": ("checkpoint_verified", False),
            "after_registry_cas": ("registry_committed", True),
            "after_receipt_write": ("registry_committed", True),
            "before_chain_replace": ("receipt_written", True),
            "after_chain_replace": ("receipt_written", True),
        }
        for failure_name, (expected_stage, cas_committed) in failure_stages.items():
            with self.subTest(failure_point=failure_name):
                with self.fixture_context(f"recovery-successor-{failure_name}") as (
                    fixture,
                    ledger,
                    memory_home,
                    _scenario,
                ):
                    _approval, handoff = self.publish_sequence_two(fixture)
                    original_receipts, _chain = self.captured_history(fixture)
                    record_before, _active = self.registry_state(fixture, memory_home)
                    shutil.rmtree(fixture.artifact / "00_role_evidence")
                    missing = rg.status_data(fixture.artifact)

                    def fail_at(name: str, ctx: object, tx_id: str) -> None:
                        del ctx, tx_id
                        if name == failure_name:
                            raise RuntimeError(f"injected recovery crash at {name}")

                    with mock.patch.object(rg, "_failure_point", fail_at):
                        with self.assertRaisesRegex(RuntimeError, failure_name):
                            rg.recover(
                                fixture.artifact,
                                lineage_id=missing["lineage_id"],
                                expected_head=handoff["receipt_sha256"],
                            )

                    pending = rg.status_data(fixture.artifact)
                    self.assertEqual("recovery_pending", pending["integrity_state"])
                    self.assertEqual(
                        "evidence_recovery",
                        pending["active_transaction"]["event"],
                    )
                    self.assertEqual(expected_stage, pending["active_transaction"]["stage"])
                    record_pending, active_pending = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual(1, len(active_pending))
                    self.assertEqual("pending", active_pending[0].status)
                    self.assertEqual(expected_stage, active_pending[0].stage)
                    self.assertEqual(3 if cas_committed else 2, record_pending.sequence)
                    self.assertEqual(3 if cas_committed else 2, record_pending.revision)

                    connection = sqlite3.connect(memory_home / "role-lineage.sqlite3")
                    try:
                        self.assertEqual(
                            1,
                            int(
                                connection.execute(
                                    """
                                    SELECT count(*) FROM lineage_transactions
                                    WHERE event='evidence_recovery'
                                    """
                                ).fetchone()[0]
                            ),
                        )
                    finally:
                        connection.close()

                    recovered = rg.recover(
                        fixture.artifact,
                        lineage_id=missing["lineage_id"],
                        expected_head=record_pending.head_sha256,
                    )
                    self.assertEqual(
                        "evidence_recovery",
                        recovered["recovery_receipt"]["event"],
                    )
                    self.assert_original_receipts_exact(fixture, original_receipts)
                    receipts = rg.verify_chain(rg.load_context(fixture.artifact))
                    self.assertEqual(
                        [
                            "human_acceptance",
                            "designer_handoff",
                            "evidence_recovery",
                        ],
                        [receipt["event"] for receipt in receipts],
                    )
                    self.assertEqual(
                        1,
                        sum(
                            receipt["event"] == "evidence_recovery"
                            for receipt in receipts
                        ),
                    )
                    final_record, final_active = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual((), final_active)
                    self.assertEqual(3, final_record.sequence)
                    self.assertEqual(3, final_record.revision)
                    self.assertEqual(3, len(ledger.checkpoint_create_calls))
                    self.assertEqual(record_before.lifecycle_state, final_record.lifecycle_state)

    def test_sequence_three_deletion_replays_all_receipts_and_preserves_lifecycle(self) -> None:
        with self.fixture_context("full-sequence-recovery") as (
            fixture,
            ledger,
            memory_home,
            _scenario,
        ):
            _approval, _handoff, acceptance = self.publish_sequence_three(fixture)
            self.assertEqual(3, acceptance["sequence"])
            original_receipts, original_chain = self.captured_history(fixture)
            record_before, active_before = self.registry_state(fixture, memory_home)
            self.assertEqual((), active_before)
            self.assertEqual("implementation_unlocked", record_before.lifecycle_state)
            shutil.rmtree(fixture.artifact / "00_role_evidence")
            missing = rg.status_data(fixture.artifact)
            self.assertEqual("history_missing", missing["integrity_state"])

            recovered = rg.recover(
                fixture.artifact,
                lineage_id=missing["lineage_id"],
                expected_head=record_before.head_sha256,
            )
            recovery_receipt = recovered["recovery_receipt"]
            self.assertEqual("evidence_recovery", recovery_receipt["event"])
            self.assertEqual(4, recovery_receipt["sequence"])
            self.assertEqual("implementation_unlocked", recovery_receipt["resulting_state"])
            self.assertEqual(
                {
                    "source": "strict_memory",
                    "recovered_head_sha256": record_before.head_sha256,
                    "recovered_sequence": 3,
                    "preserved_lifecycle_state": "implementation_unlocked",
                },
                recovery_receipt["recovery"],
            )
            self.assert_original_receipts_exact(fixture, original_receipts)
            final = rg.status_data(fixture.artifact)
            self.assertTrue(final["ok"], final)
            self.assertEqual("implementation_unlocked", final["lifecycle_state"])
            self.assertEqual(4, final["sequence"])
            self.assertEqual(4, final["registry_revision"])
            self.assertEqual(4, len(ledger.checkpoint_create_calls))
            self.assertEqual(8, len(ledger.checkpoint_get_calls))
            checkpoint_three = ledger.checkpoints[
                record_before.checkpoint_memory_id
            ]["payload"]
            self.assertEqual(
                original_chain[0],
                base64.b64decode(
                    checkpoint_three["chain_envelope"]["bytes_base64"]
                ),
            )
            self.assertEqual(original_chain[1], checkpoint_three["chain_envelope"]["mode"])

    def test_invalid_recovery_inputs_have_zero_unexpected_writes(self) -> None:
        scenarios = ("wrong_head", "malformed_archive", "receipt_conflict")
        for label in scenarios:
            with self.subTest(scenario=label):
                with self.fixture_context(f"invalid-recovery-{label}") as (
                    fixture,
                    ledger,
                    memory_home,
                    scenario,
                ):
                    _approval, handoff = self.publish_sequence_two(fixture)
                    original_receipts, _chain = self.captured_history(fixture)
                    shutil.rmtree(fixture.artifact / "00_role_evidence")
                    archive: Path | None = None
                    expected_head = handoff["receipt_sha256"]
                    if label == "wrong_head":
                        expected_head = "f" * 64
                    elif label == "malformed_archive":
                        archive = scenario / "archives" / "malformed.json"
                        archive.parent.mkdir(mode=0o700, parents=True)
                        archive.write_bytes(rg._json_bytes({"schema": "wrong"}))
                        os.chmod(archive, 0o600)
                    else:
                        receipt_dir = fixture.artifact / "00_role_evidence" / "receipts"
                        receipt_dir.mkdir(mode=0o700, parents=True)
                        first_name = sorted(original_receipts)[0]
                        (receipt_dir / first_name).write_bytes(b"conflicting bytes")
                        os.chmod(receipt_dir / first_name, 0o600)

                    target_before = self.tree_snapshot(fixture.root)
                    record_before, active_before = self.registry_state(
                        fixture, memory_home
                    )
                    self.assertEqual((), active_before)
                    writes_before = ledger.write_counts()
                    with self.assertRaises(rg.RoleGovernanceError):
                        rg.recover(
                            fixture.artifact,
                            lineage_id=record_before.lineage_id,
                            expected_head=expected_head,
                            archive=archive,
                        )
                    self.assertEqual(target_before, self.tree_snapshot(fixture.root))
                    self.assertEqual(record_before, self.registry_state(fixture, memory_home)[0])
                    self.assertEqual((), self.registry_state(fixture, memory_home)[1])
                    self.assertEqual(writes_before, ledger.write_counts())
                    if archive is not None:
                        self.assertEqual(
                            rg._json_bytes({"schema": "wrong"}), archive.read_bytes()
                        )
                    if label == "receipt_conflict":
                        self.assertGreater(len(ledger.checkpoint_get_calls), 0)

    def test_recovery_rejects_nonexact_envelopes_before_any_state_write(self) -> None:
        for memory_mode in ("required", "best_effort"):
            for target in ("receipt", "chain"):
                for mutation, expected_error in (
                    ("wrong_mode", "mode must be 0600"),
                    ("noncanonical_bytes", "non-canonical exact bytes"),
                ):
                    label = f"{memory_mode}-{target}-{mutation}"
                    with self.subTest(
                        memory_mode=memory_mode,
                        target=target,
                        mutation=mutation,
                    ):
                        with self.fixture_context(
                            f"nonexact-recovery-{label}",
                            memory_mode=memory_mode,
                        ) as (fixture, ledger, memory_home, scenario):
                            with role_env("designer", f"designer-{label}"):
                                receipt = rg.approve(
                                    fixture.artifact,
                                    approved_by="qa-owner",
                                )
                            record_before, active_before = self.registry_state(
                                fixture, memory_home
                            )
                            self.assertEqual((), active_before)
                            archive = scenario / "archives" / "history.json"
                            self.write_archive(
                                fixture,
                                archive,
                                lineage_id=record_before.lineage_id,
                                expected_head=record_before.head_sha256,
                            )
                            archive_body = self.mutate_archive_envelope(
                                archive,
                                target=target,
                                mutation=mutation,
                            )
                            shutil.rmtree(fixture.artifact / "00_role_evidence")
                            target_before = self.tree_snapshot(fixture.root)
                            writes_before = ledger.write_counts()
                            checkpoints_before = copy.deepcopy(ledger.checkpoints)

                            with self.assertRaisesRegex(
                                rg.RoleGovernanceError,
                                expected_error,
                            ):
                                rg.recover(
                                    fixture.artifact,
                                    lineage_id=record_before.lineage_id,
                                    expected_head=receipt["receipt_sha256"],
                                    archive=archive,
                                )

                            self.assertEqual(
                                target_before,
                                self.tree_snapshot(fixture.root),
                            )
                            self.assertEqual(
                                (record_before, ()),
                                self.registry_state(fixture, memory_home),
                            )
                            self.assertEqual(writes_before, ledger.write_counts())
                            self.assertEqual(checkpoints_before, ledger.checkpoints)
                            self.assertEqual(archive_body, archive.read_bytes())

    def test_required_archive_must_exactly_match_retained_checkpoint_history(self) -> None:
        with self.fixture_context("required-archive-retained-exactness") as (
            fixture,
            ledger,
            memory_home,
            scenario,
        ):
            with role_env("designer", "designer-required-archive-exactness"):
                receipt = rg.approve(fixture.artifact, approved_by="qa-owner")
            record_before, active_before = self.registry_state(fixture, memory_home)
            self.assertEqual((), active_before)
            archive = scenario / "archives" / "history.json"
            archive_body = self.write_archive(
                fixture,
                archive,
                lineage_id=record_before.lineage_id,
                expected_head=record_before.head_sha256,
            )
            retained = ledger.checkpoints[record_before.checkpoint_memory_id]
            retained["payload"]["chain_envelope"]["mode"] = 0o400
            shutil.rmtree(fixture.artifact / "00_role_evidence")
            target_before = self.tree_snapshot(fixture.root)
            writes_before = ledger.write_counts()
            checkpoints_before = copy.deepcopy(ledger.checkpoints)

            with self.assertRaisesRegex(
                rg.RoleGovernanceError,
                "does not exactly match retained strict checkpoint history",
            ):
                rg.recover(
                    fixture.artifact,
                    lineage_id=record_before.lineage_id,
                    expected_head=receipt["receipt_sha256"],
                    archive=archive,
                )

            self.assertEqual(target_before, self.tree_snapshot(fixture.root))
            self.assertEqual(
                (record_before, ()),
                self.registry_state(fixture, memory_home),
            )
            self.assertEqual(writes_before, ledger.write_counts())
            self.assertEqual(checkpoints_before, ledger.checkpoints)
            self.assertEqual(archive_body, archive.read_bytes())

    def test_required_and_best_effort_recovery_durability_are_distinct(self) -> None:
        for memory_mode in ("required", "best_effort"):
            with self.subTest(memory_mode=memory_mode):
                with self.fixture_context(
                    f"durability-{memory_mode}", memory_mode=memory_mode
                ) as (fixture, ledger, memory_home, scenario):
                    with role_env("designer", f"designer-{memory_mode}"):
                        approval = rg.approve(
                            fixture.artifact,
                            approved_by="qa-owner",
                        )
                    context = rg.load_context(fixture.artifact)
                    record_before, _active = self.registry_state(fixture, memory_home)
                    archive = scenario / "archives" / "history.json"
                    archive_body = self.write_archive(
                        fixture,
                        archive,
                        lineage_id=record_before.lineage_id,
                        expected_head=record_before.head_sha256,
                    )
                    shutil.rmtree(context.evidence_dir)
                    missing_snapshot = self.tree_snapshot(fixture.root)

                    if memory_mode == "best_effort":
                        self.assertEqual("", record_before.root_memory_id)
                        self.assertEqual("", record_before.checkpoint_memory_id)
                        with self.assertRaisesRegex(
                            rg.RoleGovernanceError, "provide --archive"
                        ):
                            rg.recover(
                                fixture.artifact,
                                lineage_id=record_before.lineage_id,
                                expected_head=approval["receipt_sha256"],
                            )
                        self.assertEqual(
                            missing_snapshot,
                            self.tree_snapshot(fixture.root),
                        )
                        recovered = rg.recover(
                            fixture.artifact,
                            lineage_id=record_before.lineage_id,
                            expected_head=approval["receipt_sha256"],
                            archive=archive,
                        )
                        self.assertEqual(0, len(ledger.root_ensure_calls))
                        self.assertEqual(0, len(ledger.root_probe_calls))
                        self.assertEqual(0, len(ledger.checkpoint_create_calls))
                        self.assertEqual(0, len(ledger.checkpoint_get_calls))
                    else:
                        self.assertTrue(record_before.root_memory_id)
                        self.assertTrue(record_before.checkpoint_memory_id)
                        recovered = rg.recover(
                            fixture.artifact,
                            lineage_id=record_before.lineage_id,
                            expected_head=approval["receipt_sha256"],
                        )
                        self.assertEqual(1, len(ledger.root_ensure_calls))
                        self.assertGreaterEqual(len(ledger.root_probe_calls), 1)
                        self.assertEqual(2, len(ledger.checkpoint_create_calls))
                        self.assertEqual(4, len(ledger.checkpoint_get_calls))

                    self.assertEqual(archive_body, archive.read_bytes())
                    self.assertEqual("aligned", recovered["integrity_state"])
                    self.assertEqual(
                        "ready_for_designer_handoff",
                        recovered["recovery_receipt"]["resulting_state"],
                    )
                    final = rg.status_data(fixture.artifact)
                    self.assertTrue(final["ok"], final)
                    self.assertEqual("aligned", final["integrity_state"])
                    self.assertEqual(
                        recovered["lifecycle_state"], final["lifecycle_state"]
                    )
                    self.assertEqual(recovered["lineage_id"], final["lineage_id"])
                    self.assertEqual(2, final["sequence"])
                    self.assertEqual(2, final["registry_revision"])

    def test_cross_workspace_same_lineage_has_exactly_one_publisher_winner(self) -> None:
        scenario = self.root / "cross-workspace-race"
        memory_home = scenario / "memory-home"
        workspace_a = scenario / "workspace-a"
        workspace_b = scenario / "workspace-b"
        ledger = FakeMemoryLedger()
        with self.activated(workspace_a, memory_home, ledger):
            fixture_a = Fixture(
                workspace_a,
                memory_mode="best_effort",
                initialize_lineage=True,
            )
            identity_a = rg.lineage_identity(fixture_a.artifact)
        with self.activated(workspace_b, memory_home, ledger):
            fixture_b = Fixture(
                workspace_b,
                memory_mode="best_effort",
                initialize_lineage=False,
            )
            shutil.copytree(
                fixture_a.artifact / "00_role_evidence",
                fixture_b.artifact / "00_role_evidence",
            )
            identity_b = rg.lineage_identity(fixture_b.artifact)
            self.assertTrue(rg.status_data(fixture_b.artifact)["ok"])

        self.assertEqual(identity_a, identity_b)
        self.assertNotEqual(
            fixture_a.artifact.stat().st_ino,
            fixture_b.artifact.stat().st_ino,
        )
        self.assertEqual(
            (fixture_a.artifact / "00_role_evidence" / "chain.json").read_bytes(),
            (fixture_b.artifact / "00_role_evidence" / "chain.json").read_bytes(),
        )

        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_publisher_process,
                args=(
                    str(workspace),
                    str(fixture.artifact),
                    str(memory_home),
                    label,
                    ready,
                    start,
                    results,
                ),
            )
            for workspace, fixture, label in (
                (workspace_a, fixture_a, "a"),
                (workspace_b, fixture_b, "b"),
            )
        ]
        for process in processes:
            process.start()
        self.assertEqual({"a", "b"}, {ready.get(timeout=15), ready.get(timeout=15)})
        start.set()
        exit_codes: dict[str, int | None] = {}
        for process in processes:
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            self.assertFalse(process.is_alive(), "publisher process did not exit")
            exit_codes[process.name] = process.exitcode
        outcomes = [results.get(timeout=5), results.get(timeout=5)]
        winners = [item for item in outcomes if item[0] == "winner"]
        losers = [item for item in outcomes if item[0] == "loser"]
        self.assertEqual(1, len(winners), outcomes)
        self.assertEqual(1, len(losers), outcomes)
        self.assertEqual([0, 2], sorted(code for code in exit_codes.values() if code is not None))
        process_by_label = dict(zip(("a", "b"), processes))
        self.assertEqual(0, process_by_label[winners[0][1]].exitcode)
        self.assertEqual(2, process_by_label[losers[0][1]].exitcode)
        self.assertEqual(1, sum(int(item[-1]) for item in outcomes), outcomes)
        self.assertEqual(1, winners[0][-1])
        self.assertEqual(0, losers[0][-1])

        with self.activated(workspace_a, memory_home, ledger):
            context_a = rg.load_context(fixture_a.artifact)
            chain_a = rg.load_chain(context_a)
            key = rg._lineage_key(context_a)
            registry = rg.lineage_registry.LineageRegistry(memory_home)
            record = registry.require_lineage(key)
            active = registry.list_active_transactions(key)
        with self.activated(workspace_b, memory_home, ledger):
            chain_b = rg.load_chain(rg.load_context(fixture_b.artifact))

        self.assertEqual([], active)
        self.assertEqual(1, record.sequence)
        self.assertEqual(1, record.revision)
        self.assertEqual(record.head_sha256, winners[0][2])
        self.assertEqual(1, winners[0][3])
        self.assertEqual([0, 1], sorted([chain_a["sequence"], chain_b["sequence"]]))
        self.assertEqual(
            ["", record.head_sha256],
            sorted([chain_a["head_sha256"], chain_b["head_sha256"]]),
        )
        receipt_counts = [
            len(list((fixture.artifact / "00_role_evidence" / "receipts").glob("*.json")))
            if (fixture.artifact / "00_role_evidence" / "receipts").exists()
            else 0
            for fixture in (fixture_a, fixture_b)
        ]
        self.assertEqual([0, 1], sorted(receipt_counts))
        connection = sqlite3.connect(memory_home / "role-lineage.sqlite3")
        try:
            transaction_count = int(
                connection.execute(
                    "SELECT count(*) FROM lineage_transactions"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(1, transaction_count)
        self.assertEqual({}, ledger.checkpoints)


if __name__ == "__main__":
    unittest.main(verbosity=2)
