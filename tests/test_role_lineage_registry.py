#!/usr/bin/env python3
"""Deterministic unit tests for the machine-level role-lineage registry."""

from __future__ import annotations

import hashlib
import base64
import json
import multiprocessing
import os
import sqlite3
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import role_lineage as rl  # noqa: E402


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _root_payload(key: rl.LineageKey) -> dict[str, object]:
    return {
        "schema": "bugate.role-lineage-root/v1",
        "lineage_key": key.as_dict(),
        "lineage_id": key.lineage_id,
    }


def _content_id(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _root_id(key: rl.LineageKey) -> str:
    return _content_id(_root_payload(key))


def _checkpoint_payload(
    key: rl.LineageKey,
    *,
    sequence: int,
    head_sha256: str,
    state: str,
    revision: int,
    previous_checkpoint_id: str = "",
    previous_receipt_sha256: str = "",
) -> dict[str, object]:
    receipt_path = (
        f"{key.artifact_dir}/00_role_evidence/receipts/"
        f"{sequence:06d}-fixture-event-{head_sha256}.json"
    )
    receipt = {
        "schema": "bugate.role-evidence/v1",
        "event": "fixture_event",
        "uc": key.uc,
        "artifact_dir": key.artifact_dir,
        "sequence": sequence,
        "previous_receipt_sha256": previous_receipt_sha256,
        "receipt_sha256": head_sha256,
        "resulting_state": state,
    }
    chain = {
        "schema": "bugate.role-chain/v1",
        "sequence": sequence,
        "head_sha256": head_sha256,
        "state": state,
        "latest_receipts": {"fixture_event": receipt_path},
    }

    def envelope(path: str, parsed: dict[str, object]) -> dict[str, object]:
        raw = _canonical(parsed)
        return {
            "path": path,
            "mode": 0o600,
            "bytes_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes_base64": base64.b64encode(raw).decode("ascii"),
            "parsed": parsed,
        }

    return {
        "schema": "bugate.role-lineage-checkpoint/v1",
        "lineage_key": key.as_dict(),
        "lineage_id": key.lineage_id,
        "lineage_root_id": _root_id(key),
        "sequence": sequence,
        "previous_checkpoint_id": previous_checkpoint_id,
        "previous_receipt_sha256": previous_receipt_sha256,
        "receipt_sha256": head_sha256,
        "resulting_state": state,
        "registry_revision": revision,
        "receipt_envelope": envelope(
            receipt_path,
            receipt,
        ),
        "chain_envelope": envelope(
            f"{key.artifact_dir}/00_role_evidence/chain.json",
            chain,
        ),
    }


def _recovery_transition(
    key: rl.LineageKey,
    lineage: rl.LineageRecord,
) -> dict[str, object]:
    transition: dict[str, object] = {
        "schema": "bugate.role-transition/v1",
        "event": "evidence_recovery",
        "uc": key.uc,
        "artifact_dir": key.artifact_dir,
        "previous_receipt_sha256": lineage.head_sha256,
        "lineage": {
            "schema": "bugate.role-lineage-precondition/v1",
            "lineage_id": lineage.lineage_id,
            "expected_head_sha256": lineage.head_sha256,
            "expected_sequence": lineage.sequence,
            "expected_revision": lineage.revision,
            "previous_checkpoint_memory_id": lineage.checkpoint_memory_id,
        },
        "recovery": {
            "source": "fixture",
            "recovered_head_sha256": lineage.head_sha256,
            "recovered_sequence": lineage.sequence,
            "preserved_lifecycle_state": lineage.lifecycle_state,
        },
    }
    transition["transition_sha256"] = _content_id(transition)
    return transition


def _concurrent_cas_worker(
    home: str,
    lineage_id: str,
    tx_id: str,
    target_head: str,
    ready: object,
    start: object,
    results: object,
) -> None:
    """Race one best-effort transaction from the shared sequence-zero head."""

    try:
        registry = rl.LineageRegistry(home)
        ready.put(tx_id)
        if not start.wait(15):
            results.put(("error", tx_id, "start timeout"))
            return
        transaction = registry.begin_pending(
            lineage_id,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state=f"won_{tx_id.replace('-', '_')}",
            tx_id=tx_id,
            transition_payload={"schema": "test.transition/v1", "tx_id": tx_id},
        )
        receipt_bytes = _canonical(
            {"schema": "test.receipt/v1", "receipt_sha256": target_head}
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
            new_stage=rl.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=target_head,
            receipt_path=f"usecases/UC-RACE/evidence/{tx_id}.json",
            receipt_bytes=receipt_bytes,
            receipt_mode=0o600,
            receipt_sha256=target_head,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_BOUND,
            new_stage=rl.TX_STAGE_MEMORY_FINALIZED,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_FINALIZED,
            new_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        committed = registry.compare_and_swap_head(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_REGISTRY_COMMITTED,
            new_stage=rl.TX_STAGE_RECEIPT_WRITTEN,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_WRITTEN,
            new_stage=rl.TX_STAGE_CHAIN_REPLACED,
        )
        registry.complete(transaction.tx_id)
        results.put(
            (
                "winner",
                tx_id,
                committed.lineage.head_sha256,
                committed.lineage.sequence,
                committed.lineage.revision,
            )
        )
    except (rl.PendingTransactionError, rl.LineageConflictError) as exc:
        results.put(("loser", tx_id, type(exc).__name__))
        raise SystemExit(23)
    except Exception as exc:  # pragma: no cover - surfaced by the parent assertion.
        results.put(("error", tx_id, type(exc).__name__, str(exc)))
        raise SystemExit(70)


class RoleLineageRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(prefix="bugate-role-lineage-registry-")
        self.root = Path(self.temp.name)
        self.home = self.root / "memory-home"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def key(suffix: str = "001") -> rl.LineageKey:
        return rl.build_lineage_key(
            "project:registry-fixture",
            f"UC-REGISTRY-{suffix}",
            f"usecases/UC-REGISTRY-{suffix}",
        )

    def registry(self) -> rl.LineageRegistry:
        return rl.LineageRegistry(self.home, create=True)

    @staticmethod
    def initialize_best_effort(
        registry: rl.LineageRegistry,
        key: rl.LineageKey,
    ) -> rl.LineageRecord:
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="best_effort",
        )
        registry.mark_initialization_root_absence_verified(intent.init_id)
        registry.bind_initialization_root(intent.init_id, root_memory_id="")
        record = registry.commit_initialization(intent.init_id)
        registry.mark_initialization_chain_written(intent.init_id)
        registry.complete_initialization(intent.init_id)
        return record

    @staticmethod
    def initialize_required(
        registry: rl.LineageRegistry,
        key: rl.LineageKey,
    ) -> rl.LineageRecord:
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
        )
        registry.mark_initialization_root_absence_verified(intent.init_id)
        registry.bind_initialization_root(
            intent.init_id,
            root_memory_id=_root_id(key),
        )
        record = registry.commit_initialization(intent.init_id)
        registry.mark_initialization_chain_written(intent.init_id)
        registry.complete_initialization(intent.init_id)
        return record

    @staticmethod
    def stage_required_transaction(
        registry: rl.LineageRegistry,
        key: rl.LineageKey,
        *,
        target_head: str = HASH_B,
        tx_id: str = "tx-required",
    ) -> tuple[rl.TransactionRecord, dict[str, object]]:
        transition = {
            "schema": "test.transition/v1",
            "event": "human_acceptance",
        }
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id=tx_id,
            transition_payload=transition,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
            transition_memory_id="transition-memory-exact-id",
        )
        receipt_bytes = _canonical(
            {"schema": "test.receipt/v1", "receipt_sha256": target_head}
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
            new_stage=rl.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=target_head,
            receipt_path="usecases/UC-REGISTRY-001/00_role_evidence/receipts/receipt.json",
            receipt_bytes=receipt_bytes,
            receipt_mode=0o600,
            receipt_sha256=target_head,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_BOUND,
            new_stage=rl.TX_STAGE_MEMORY_FINALIZED,
        )
        checkpoint = _checkpoint_payload(
            key,
            sequence=1,
            head_sha256=target_head,
            state="ready_for_designer_handoff",
            revision=1,
        )
        checkpoint_id = _content_id(checkpoint)
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_FINALIZED,
            new_stage=rl.TX_STAGE_CHECKPOINT_VERIFIED,
            checkpoint_memory_id=checkpoint_id,
            checkpoint_payload=checkpoint,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_CHECKPOINT_VERIFIED,
            new_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        return transaction, checkpoint

    def test_read_paths_never_create_registry_or_memory_home(self) -> None:
        absent_home = self.root / "absent-memory-home"
        expected_path = absent_home / rl.REGISTRY_FILENAME

        self.assertEqual(expected_path, rl.registry_path(absent_home))
        self.assertFalse(absent_home.exists())
        with self.assertRaisesRegex(rl.RegistryNotFoundError, "not initialized"):
            rl.LineageRegistry(absent_home)
        self.assertFalse(absent_home.exists())
        self.assertFalse(expected_path.exists())

        with self.assertRaisesRegex(rl.LineageValidationError, "must be absolute"):
            rl.LineageRegistry(Path("relative-memory-home"), create=True)
        self.assertFalse((ROOT / "relative-memory-home").exists())

    def test_lineage_key_and_id_are_exact_and_deterministic(self) -> None:
        key = self.key()
        expected = {
            "schema": "bugate.role-lineage-key/v1",
            "namespace": "project:registry-fixture",
            "uc": "UC-REGISTRY-001",
            "artifact_dir": "usecases/UC-REGISTRY-001",
        }
        expected_id = hashlib.sha256(_canonical(expected)).hexdigest()

        self.assertEqual(expected, key.as_dict())
        self.assertEqual(_canonical(expected), key.canonical_bytes)
        self.assertEqual(expected_id, key.lineage_id)
        self.assertEqual(expected_id, rl.lineage_id(key))
        self.assertEqual(
            expected_id,
            rl.lineage_id(
                namespace=key.namespace,
                uc=key.uc,
                artifact_dir=key.artifact_dir,
            ),
        )
        self.assertEqual(key, rl.LineageKey.from_json(key.canonical_bytes))
        self.assertNotEqual(
            key.lineage_id,
            rl.build_lineage_key(
                key.namespace,
                key.uc.lower(),
                key.artifact_dir,
            ).lineage_id,
        )

    def test_registry_mode_schema_and_reopen_contract(self) -> None:
        registry = self.registry()
        path = self.home / rl.REGISTRY_FILENAME

        self.assertEqual(0o700, stat.S_IMODE(self.home.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        connection = sqlite3.connect(path)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            journal_mode = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(rl.REGISTRY_SCHEMA_VERSION, version)
        self.assertEqual("wal", journal_mode)
        self.assertTrue(
            {
                "lineages",
                "lineage_initializations",
                "lineage_transactions",
                "lineage_checkpoints",
            }.issubset(tables)
        )
        self.assertIn("lineage_one_active_initialization", indexes)
        self.assertIn("lineage_one_active_transaction", indexes)
        self.assertEqual([], registry.list_lineages())
        self.assertEqual([], rl.LineageRegistry(self.home).list_lineages())

    def test_required_sequence_zero_initialization_has_root_and_empty_head(self) -> None:
        registry = self.registry()
        key = self.key()
        record = self.initialize_required(registry, key)

        self.assertEqual(0, record.sequence)
        self.assertEqual("", record.head_sha256)
        self.assertEqual(0, record.revision)
        self.assertEqual("required", record.memory_mode)
        self.assertEqual(_root_id(key), record.root_memory_id)
        self.assertEqual("", record.checkpoint_memory_id)
        self.assertEqual(record, registry.require_lineage(key))
        with self.assertRaises(rl.LineageAlreadyExistsError):
            self.initialize_required(registry, key)

        missing_root = self.key("002")
        intent = registry.begin_initialization(
            missing_root,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
        )
        registry.mark_initialization_root_absence_verified(intent.init_id)
        with self.assertRaisesRegex(rl.LineageValidationError, "Memory root"):
            registry.bind_initialization_root(
                intent.init_id,
                root_memory_id="",
            )
        self.assertIsNone(registry.get_lineage(missing_root))

    def test_required_positive_adoption_needs_exact_checkpoint_pair(self) -> None:
        registry = self.registry()
        key = self.key()

        with self.assertRaisesRegex(rl.LineageValidationError, "checkpoint"):
            registry.adopt(
                key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=2,
                head_sha256=HASH_B,
                memory_mode="required",
                root_memory_id=_root_id(key),
            )
        with self.assertRaisesRegex(rl.LineageValidationError, "supplied together"):
            registry.adopt(
                key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=2,
                head_sha256=HASH_B,
                memory_mode="required",
                root_memory_id=_root_id(key),
                checkpoint_memory_id=HASH_A,
            )
        self.assertIsNone(registry.get_lineage(key))

        checkpoint = _checkpoint_payload(
            key,
            sequence=1,
            head_sha256=HASH_A,
            state="ready_for_designer_handoff",
            revision=0,
        )
        first_checkpoint_id = _content_id(checkpoint)
        final_checkpoint = _checkpoint_payload(
            key,
            sequence=2,
            head_sha256=HASH_B,
            state="awaiting_implementer_acceptance",
            revision=0,
            previous_checkpoint_id=first_checkpoint_id,
            previous_receipt_sha256=HASH_A,
        )
        checkpoint_id = _content_id(final_checkpoint)
        record = registry.adopt(
            key,
            lifecycle_state="awaiting_implementer_acceptance",
            sequence=2,
            head_sha256=HASH_B,
            memory_mode="required",
            root_memory_id=_root_id(key),
            checkpoint_memory_id=checkpoint_id,
            checkpoint_payload=final_checkpoint,
            checkpoint_history=[checkpoint, final_checkpoint],
        )
        self.assertEqual(2, record.sequence)
        self.assertEqual(checkpoint_id, record.checkpoint_memory_id)
        self.assertEqual(
            _canonical(final_checkpoint),
            registry.get_checkpoint_payload(
                checkpoint_id, lineage=key
            ),
        )
        retained = registry.get_checkpoint_for_head(key, HASH_B)
        self.assertIsNotNone(retained)
        self.assertEqual(_canonical(final_checkpoint), retained.payload)

    def test_best_effort_durability_is_explicitly_weaker(self) -> None:
        registry = self.registry()
        empty_key = self.key("001")
        empty = self.initialize_best_effort(registry, empty_key)
        adopted_key = self.key("002")
        adopted = registry.adopt(
            adopted_key,
            lifecycle_state="awaiting_implementer_acceptance",
            sequence=2,
            head_sha256=HASH_B,
            memory_mode="best_effort",
        )

        self.assertEqual(("", ""), (empty.root_memory_id, empty.checkpoint_memory_id))
        self.assertEqual(("", ""), (adopted.root_memory_id, adopted.checkpoint_memory_id))
        transaction = registry.begin_pending(
            empty_key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            transition_payload={"schema": "test.transition/v1"},
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
            new_stage=rl.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=HASH_A,
            receipt_path="usecases/UC-REGISTRY-001/evidence/receipt.json",
            receipt_bytes=b"{\"receipt\":1}",
            receipt_mode=0o600,
            receipt_sha256=HASH_A,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_BOUND,
            new_stage=rl.TX_STAGE_MEMORY_FINALIZED,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_FINALIZED,
            new_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        committed = registry.compare_and_swap_head(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        self.assertEqual(1, committed.lineage.sequence)
        self.assertEqual("", committed.lineage.checkpoint_memory_id)
        self.assertEqual("", committed.transaction.transition_memory_id)

    def test_required_transaction_real_cas_increments_revision_once(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction, checkpoint = self.stage_required_transaction(registry, key)

        committed = registry.compare_and_swap_head(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        self.assertEqual(HASH_B, committed.lineage.head_sha256)
        self.assertEqual(1, committed.lineage.sequence)
        self.assertEqual(1, committed.lineage.revision)
        self.assertEqual(
            _content_id(checkpoint),
            committed.lineage.checkpoint_memory_id,
        )
        self.assertEqual(rl.TX_STAGE_REGISTRY_COMMITTED, committed.transaction.stage)
        self.assertEqual(
            _canonical(checkpoint),
            registry.get_checkpoint_payload(_content_id(checkpoint), lineage=key),
        )
        stored = registry.get_transaction(transaction.tx_id)
        self.assertEqual(
            _canonical({"schema": "test.transition/v1", "event": "human_acceptance"}),
            stored.transition_payload,
        )
        self.assertEqual(0o600, stored.receipt_mode)
        self.assertEqual(HASH_B, stored.receipt_sha256)

        stored = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_REGISTRY_COMMITTED,
            new_stage=rl.TX_STAGE_RECEIPT_WRITTEN,
        )
        stored = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_WRITTEN,
            new_stage=rl.TX_STAGE_CHAIN_REPLACED,
        )
        completed = registry.complete(stored.tx_id)
        self.assertEqual("completed", completed.status)
        self.assertEqual("completed", completed.stage)
        self.assertEqual(1, registry.require_lineage(key).revision)
        with self.assertRaises(rl.TransactionStateError):
            registry.compare_and_swap_head(
                transaction.tx_id,
                expected_stage=rl.TX_STAGE_READY_FOR_CAS,
            )

    def test_two_processes_same_head_have_one_winner_and_no_fork(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_best_effort(registry, key)
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_concurrent_cas_worker,
                args=(
                    str(self.home),
                    key.lineage_id,
                    tx_id,
                    target_head,
                    ready,
                    start,
                    results,
                ),
            )
            for tx_id, target_head in (("worker-a", HASH_A), ("worker-b", HASH_B))
        ]
        for process in processes:
            process.start()
        self.assertEqual({"worker-a", "worker-b"}, {ready.get(timeout=15), ready.get(timeout=15)})
        start.set()
        for process in processes:
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            self.assertFalse(process.is_alive(), "concurrent registry worker did not exit")

        self.assertEqual([0, 23], sorted(process.exitcode for process in processes))

        outcomes = [results.get(timeout=5), results.get(timeout=5)]
        self.assertEqual(1, sum(item[0] == "winner" for item in outcomes), outcomes)
        self.assertEqual(1, sum(item[0] == "loser" for item in outcomes), outcomes)
        self.assertFalse(any(item[0] == "error" for item in outcomes), outcomes)
        record = rl.LineageRegistry(self.home).require_lineage(key)
        self.assertEqual(1, record.sequence)
        self.assertEqual(1, record.revision)
        self.assertIn(record.head_sha256, {HASH_A, HASH_B})
        connection = sqlite3.connect(self.home / rl.REGISTRY_FILENAME)
        try:
            transaction_count = int(
                connection.execute(
                    "SELECT count(*) FROM lineage_transactions"
                ).fetchone()[0]
            )
            checkpoint_count = int(
                connection.execute(
                    "SELECT count(*) FROM lineage_checkpoints"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(1, transaction_count)
        self.assertEqual(0, checkpoint_count)

    def test_transition_receipt_and_checkpoint_fields_are_bind_once(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id="tx-bind-once",
        )
        first_transition = {"schema": "test.transition/v1", "value": 1}
        bound = registry.bind_transition_payload(
            transaction.tx_id,
            transition_payload=first_transition,
        )
        rebound = registry.bind_transition_payload(
            transaction.tx_id,
            transition_payload=_canonical(first_transition),
        )
        self.assertEqual(_canonical(first_transition), bound.transition_payload)
        self.assertEqual(bound.transition_payload, rebound.transition_payload)
        with self.assertRaisesRegex(rl.TransactionStateError, "immutable"):
            registry.bind_transition_payload(
                transaction.tx_id,
                transition_payload={"schema": "test.transition/v1", "value": 2},
            )

        prepared = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
            transition_memory_id="transition-memory-exact-id",
        )
        with self.assertRaisesRegex(rl.LineageValidationError, "bound atomically"):
            registry.update_stage(
                transaction.tx_id,
                expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
                new_stage=rl.TX_STAGE_RECEIPT_BOUND,
                receipt_path="usecases/UC-REGISTRY-001/evidence/receipt.json",
            )
        self.assertEqual(prepared, registry.get_transaction(transaction.tx_id))

        receipt_bytes = b"{\"receipt\":1}"
        receipt_bound = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
            new_stage=rl.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=HASH_A,
            receipt_path="usecases/UC-REGISTRY-001/evidence/receipt.json",
            receipt_bytes=receipt_bytes,
            receipt_mode=0o600,
            receipt_sha256=HASH_A,
        )
        with self.assertRaisesRegex(rl.TransactionStateError, "only at receipt_bound"):
            registry.update_stage(
                transaction.tx_id,
                expected_stage=rl.TX_STAGE_RECEIPT_BOUND,
                new_stage=rl.TX_STAGE_MEMORY_FINALIZED,
                receipt_bytes=b"different bytes",
            )
        unchanged = registry.get_transaction(transaction.tx_id)
        self.assertEqual(rl.TX_STAGE_RECEIPT_BOUND, unchanged.stage)
        self.assertEqual(receipt_bytes, unchanged.receipt_bytes)
        self.assertEqual(receipt_bound.receipt_sha256, unchanged.receipt_sha256)

    def test_recovery_claim_is_exclusive_and_token_guarded(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id="tx-recovery",
            transition_payload={"schema": "test.transition/v1"},
        )
        marked = registry.mark_incomplete(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            error="injected transition interruption",
        )
        claim = registry.claim_recovery(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
        )
        self.assertEqual("recovering", claim.transaction.status)
        self.assertRegex(claim.claim_token, rf"^r1:{os.getpid()}:[0-9a-f]{{32}}$")
        self.assertEqual(marked.error, claim.transaction.error)
        with self.assertRaises(rl.RecoveryClaimError):
            registry.claim_recovery(transaction.tx_id)
        with self.assertRaises(rl.RecoveryClaimError):
            registry.mark_recovery_stage(
                transaction.tx_id,
                claim_token="wrong-claim",
                expected_stage=rl.TX_STAGE_PENDING,
                new_stage=rl.TX_STAGE_MEMORY_PREPARED,
                transition_memory_id="transition-memory-exact-id",
            )
        recovered = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=claim.claim_token,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
            transition_memory_id="transition-memory-exact-id",
        )
        self.assertEqual(rl.TX_STAGE_MEMORY_PREPARED, recovered.stage)
        with self.assertRaises(rl.RecoveryClaimError):
            registry.release_recovery(
                transaction.tx_id,
                claim_token="wrong-claim",
                error="retry later",
            )
        released = registry.release_recovery(
            transaction.tx_id,
            claim_token=claim.claim_token,
            error="retry later",
        )
        self.assertEqual("pending", released.status)
        self.assertEqual("", released.recovery_token)
        second = registry.claim_recovery(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
        )
        self.assertNotEqual(claim.claim_token, second.claim_token)
        self.assertRegex(second.claim_token, rf"^r1:{os.getpid()}:[0-9a-f]{{32}}$")

    def test_recovery_claim_survives_hard_exit_and_dead_owner_is_taken_over(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id="tx-hard-crash",
            transition_payload={"schema": "test.transition/v1"},
        )
        child = "\n".join(
            (
                "import os, sys",
                f"sys.path.insert(0, {str(ROOT / 'scripts')!r})",
                "import role_lineage as rl",
                "registry = rl.LineageRegistry(sys.argv[1])",
                "registry.claim_recovery(sys.argv[2], expected_stage=rl.TX_STAGE_PENDING)",
                "os._exit(73)",
            )
        )
        completed = subprocess.run(
            [sys.executable, "-c", child, str(self.home), transaction.tx_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(73, completed.returncode, completed.stderr)
        stranded = registry.get_transaction(transaction.tx_id)
        self.assertEqual("recovering", stranded.status)
        self.assertRegex(stranded.recovery_token, r"^r1:[1-9][0-9]*:[0-9a-f]{32}$")

        takeover = registry.claim_recovery(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
        )
        self.assertEqual("recovering", takeover.transaction.status)
        self.assertNotEqual(stranded.recovery_token, takeover.claim_token)
        self.assertRegex(takeover.claim_token, rf"^r1:{os.getpid()}:[0-9a-f]{{32}}$")

    def test_live_subprocess_recovery_claim_cannot_be_stolen(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id="tx-live-owner",
            transition_payload={"schema": "test.transition/v1"},
        )
        child = "\n".join(
            (
                "import sys",
                f"sys.path.insert(0, {str(ROOT / 'scripts')!r})",
                "import role_lineage as rl",
                "registry = rl.LineageRegistry(sys.argv[1])",
                "claim = registry.claim_recovery(sys.argv[2], expected_stage=rl.TX_STAGE_PENDING)",
                "print(claim.claim_token, flush=True)",
                "sys.stdin.readline()",
                "registry.release_recovery(sys.argv[2], claim_token=claim.claim_token, error='child_done')",
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-c", child, str(self.home), transaction.tx_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            child_token = process.stdout.readline().strip()
            self.assertRegex(child_token, rf"^r1:{process.pid}:[0-9a-f]{{32}}$")
            with self.assertRaisesRegex(rl.RecoveryClaimError, "active recovery claimant"):
                registry.claim_recovery(transaction.tx_id)
            self.assertEqual(child_token, registry.get_transaction(transaction.tx_id).recovery_token)
            assert process.stdin is not None
            process.stdin.write("release\n")
            process.stdin.flush()
            _, stderr = process.communicate(timeout=20)
            self.assertEqual(0, process.returncode, stderr)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_release_recovery_persists_only_path_free_stable_diagnostics(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id="tx-path-error",
            transition_payload={"schema": "test.transition/v1"},
        )
        claim = registry.claim_recovery(transaction.tx_id)
        released = registry.release_recovery(
            transaction.tx_id,
            claim_token=claim.claim_token,
            error=FileNotFoundError(str(self.root / "private" / "receipt.json")),
        )
        self.assertEqual("pending", released.status)
        self.assertEqual("recovery_error:FileNotFoundError", released.error)
        self.assertNotIn(str(self.root), released.error)
        second = registry.claim_recovery(transaction.tx_id)
        released = registry.release_recovery(
            transaction.tx_id,
            claim_token=second.claim_token,
            error=f"ValueError: invalid archive {self.root / 'private.json'}",
        )
        self.assertEqual("recovery_error:ValueError", released.error)
        self.assertNotIn(str(self.root), released.error)

    def test_initialization_intent_is_durable_resumable_and_atomically_registers(self) -> None:
        registry = self.registry()
        key = self.key()
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
            init_id="init-required",
        )
        self.assertEqual(rl.INIT_STAGE_PENDING, intent.stage)
        self.assertEqual("pending", intent.status)
        self.assertIsNone(registry.get_lineage(key))
        self.assertEqual(intent, registry.get_initialization(intent.init_id))
        self.assertEqual([intent], registry.list_active_initializations(key))
        self.assertEqual(
            intent,
            registry.begin_initialization(
                key,
                lifecycle_state="awaiting_human_acceptance",
                memory_mode="required",
            ),
        )
        self.assertEqual(
            "recovery_pending",
            rl.classify_integrity(
                None,
                local_sequence=0,
                local_head_sha256="",
                has_local_history=False,
                active_initialization=intent,
            ),
        )

        absence = registry.mark_initialization_root_absence_verified(intent.init_id)
        self.assertEqual(rl.INIT_STAGE_ROOT_ABSENCE_VERIFIED, absence.stage)
        root = registry.bind_initialization_root(
            intent.init_id,
            root_memory_id=_root_id(key),
        )
        self.assertEqual(rl.INIT_STAGE_ROOT_VERIFIED, root.stage)
        lineage = registry.commit_initialization(intent.init_id)
        self.assertEqual((0, "", 0), (lineage.sequence, lineage.head_sha256, lineage.revision))
        self.assertEqual(_root_id(key), lineage.root_memory_id)
        registered = registry.get_initialization(intent.init_id)
        self.assertEqual(rl.INIT_STAGE_REGISTRY_INITIALIZED, registered.stage)
        self.assertEqual(lineage, registry.commit_initialization(intent.init_id))
        with self.assertRaises(rl.InitializationStateError):
            registry.begin_pending(
                key,
                event="human_acceptance",
                expected_head_sha256="",
                expected_sequence=0,
                expected_revision=0,
                expected_checkpoint_memory_id="",
                target_lifecycle_state="ready_for_designer_handoff",
            )

        written = registry.mark_initialization_chain_written(intent.init_id)
        self.assertEqual(rl.INIT_STAGE_CHAIN_WRITTEN, written.stage)
        completed = registry.complete_initialization(intent.init_id)
        self.assertEqual("completed", completed.status)
        self.assertEqual(rl.INIT_STAGE_COMPLETED, completed.stage)
        self.assertEqual([], registry.list_active_initializations(key))
        self.assertEqual(0o600, stat.S_IMODE((self.home / rl.REGISTRY_FILENAME).stat().st_mode))

    def test_initialization_root_cannot_bind_before_absence_and_mismatches_write_nothing(self) -> None:
        registry = self.registry()
        key = self.key()
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
        )
        with self.assertRaises(rl.InitializationStateError):
            registry.bind_initialization_root(
                intent.init_id,
                root_memory_id=_root_id(key),
            )
        with self.assertRaises(rl.InitializationConflictError):
            registry.begin_initialization(
                key,
                lifecycle_state="different_state",
                memory_mode="required",
            )
        unchanged = registry.get_initialization(intent.init_id)
        self.assertEqual(rl.INIT_STAGE_PENDING, unchanged.stage)
        self.assertEqual("", unchanged.root_memory_id)
        self.assertIsNone(registry.get_lineage(key))

        registry.mark_initialization_root_absence_verified(intent.init_id)
        registry.bind_initialization_root(
            intent.init_id,
            root_memory_id=_root_id(key),
        )
        with self.assertRaisesRegex(rl.LineageValidationError, "content"):
            registry.bind_initialization_root(
                intent.init_id,
                root_memory_id=HASH_D,
            )
        self.assertEqual(_root_id(key), registry.get_initialization(intent.init_id).root_memory_id)
        self.assertIsNone(registry.get_lineage(key))

    def test_initialization_intent_cannot_be_bypassed_or_restarted_as_empty(self) -> None:
        registry = self.registry()
        key = self.key()
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
        )
        with self.assertRaises(rl.InitializationStateError):
            registry.initialize(
                key,
                lifecycle_state="awaiting_human_acceptance",
                memory_mode="required",
                root_memory_id=_root_id(key),
            )
        aborted = registry.abort_initialization(
            intent.init_id,
            error="root_preexisting",
        )
        self.assertEqual("aborted", aborted.status)
        self.assertEqual("recovery_error:root_preexisting", aborted.error)
        with self.assertRaises(rl.InitializationConflictError):
            registry.begin_initialization(
                key,
                lifecycle_state="awaiting_human_acceptance",
                memory_mode="required",
            )
        with self.assertRaises(rl.InitializationStateError):
            registry.initialize(
                key,
                lifecycle_state="awaiting_human_acceptance",
                memory_mode="required",
                root_memory_id=_root_id(key),
            )
        self.assertIsNone(registry.get_lineage(key))

    def test_best_effort_initialization_still_uses_full_journal(self) -> None:
        registry = self.registry()
        key = self.key()
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="best_effort",
        )
        registry.mark_initialization_root_absence_verified(intent.init_id)
        root = registry.bind_initialization_root(intent.init_id, root_memory_id="")
        self.assertEqual(rl.INIT_STAGE_ROOT_VERIFIED, root.stage)
        lineage = registry.commit_initialization(intent.init_id)
        self.assertEqual("", lineage.root_memory_id)
        self.assertEqual("best_effort", lineage.memory_mode)
        registry.mark_initialization_chain_written(intent.init_id)
        registry.complete_initialization(intent.init_id)

    def test_wrong_expected_head_sequence_revision_or_checkpoint_writes_nothing(self) -> None:
        registry = self.registry()
        key = self.key()
        record = registry.adopt(
            key,
            lifecycle_state="awaiting_implementer_acceptance",
            sequence=1,
            head_sha256=HASH_A,
            memory_mode="best_effort",
        )
        wrong_expectations = (
            {"expected_head_sha256": HASH_B},
            {"expected_sequence": 2},
            {"expected_revision": 1},
            {"expected_checkpoint_memory_id": "unexpected-checkpoint"},
        )
        base = {
            "expected_head_sha256": record.head_sha256,
            "expected_sequence": record.sequence,
            "expected_revision": record.revision,
            "expected_checkpoint_memory_id": record.checkpoint_memory_id,
        }
        for index, override in enumerate(wrong_expectations):
            with self.subTest(override=override):
                expected = {**base, **override}
                with self.assertRaises(rl.LineageConflictError):
                    registry.begin_pending(
                        key,
                        event="designer_handoff",
                        target_lifecycle_state="awaiting_implementer_acceptance",
                        tx_id=f"wrong-{index}",
                        transition_payload={"schema": "test.transition/v1"},
                        **expected,
                    )
                self.assertEqual([], registry.list_active_transactions(key))
                self.assertEqual(record, registry.require_lineage(key))

        active = registry.begin_pending(
            key,
            event="designer_handoff",
            target_lifecycle_state="awaiting_implementer_acceptance",
            tx_id="correct-head",
            transition_payload={"schema": "test.transition/v1"},
            **base,
        )
        with self.assertRaises(rl.PendingTransactionError):
            registry.begin_pending(
                key,
                event="designer_handoff",
                target_lifecycle_state="awaiting_implementer_acceptance",
                tx_id="same-head-second",
                transition_payload={"schema": "test.transition/v1"},
                **base,
            )
        self.assertEqual([active], registry.list_active_transactions(key))

    def test_path_and_symlink_inputs_fail_without_target_mutation(self) -> None:
        invalid_keys = (
            ("project:fixture", "UC-1", "/absolute/usecase"),
            ("project:fixture", "UC-1", "../escaping/usecase"),
            ("project:fixture", "UC-1", "C:/windows/usecase"),
            ("project:fixture", "UC-1", "usecases//noncanonical"),
            ("project:\nfixture", "UC-1", "usecases/UC-1"),
        )
        for values in invalid_keys:
            with self.subTest(values=values):
                with self.assertRaises(rl.LineageValidationError):
                    rl.build_lineage_key(*values)

        symlink_home = self.root / "symlink-home"
        symlink_home.mkdir(mode=0o700)
        target = self.root / "unrelated-target.sqlite3"
        target.write_bytes(b"preserve-me")
        registry_leaf = symlink_home / rl.REGISTRY_FILENAME
        registry_leaf.symlink_to(target)
        for create in (False, True):
            with self.subTest(create=create):
                with self.assertRaisesRegex(rl.RegistryIntegrityError, "symlink"):
                    rl.LineageRegistry(symlink_home, create=create)
        self.assertEqual(b"preserve-me", target.read_bytes())
        self.assertTrue(registry_leaf.is_symlink())

        safe_home = self.root / "safe-home"
        registry = rl.LineageRegistry(safe_home, create=True)
        key = self.key()
        self.initialize_required(registry, key)
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            tx_id="invalid-receipt-path",
            transition_payload={"schema": "test.transition/v1"},
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
            transition_memory_id="transition-memory-exact-id",
        )
        invalid_receipt_paths = (
            "/tmp/receipt.json",
            "../receipt.json",
            "C:/receipt.json",
            "usecases//receipt.json",
        )
        for value in invalid_receipt_paths:
            with self.subTest(receipt_path=value):
                with self.assertRaises(rl.LineageValidationError):
                    registry.update_stage(
                        transaction.tx_id,
                        expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
                        new_stage=rl.TX_STAGE_RECEIPT_BOUND,
                        target_head_sha256=HASH_A,
                        receipt_path=value,
                        receipt_bytes=b"{\"receipt\":1}",
                        receipt_mode=0o600,
                        receipt_sha256=HASH_A,
                    )
                unchanged = registry.get_transaction(transaction.tx_id)
                self.assertEqual(rl.TX_STAGE_MEMORY_PREPARED, unchanged.stage)
                self.assertEqual("", unchanged.receipt_path)
                self.assertIsNone(unchanged.receipt_bytes)

    def test_classify_integrity_covers_every_state_independent_of_lifecycle(self) -> None:
        registry = self.registry()
        key = self.key()
        lineage = self.initialize_best_effort(registry, key)
        states = {
            rl.classify_integrity(
                None,
                local_sequence=None,
                local_head_sha256=None,
                has_local_history=False,
                registry_available=False,
            ),
            rl.classify_integrity(
                None,
                local_sequence=None,
                local_head_sha256=None,
                has_local_history=False,
            ),
            rl.classify_integrity(
                None,
                local_sequence=2,
                local_head_sha256=HASH_B,
                has_local_history=True,
            ),
            rl.classify_integrity(
                lineage,
                local_sequence=None,
                local_head_sha256=None,
                has_local_history=True,
            ),
            rl.classify_integrity(
                lineage,
                local_sequence=1,
                local_head_sha256=HASH_A,
                has_local_history=True,
            ),
            rl.classify_integrity(
                lineage,
                local_sequence=0,
                local_head_sha256="",
                has_local_history=False,
            ),
        }
        transaction = registry.begin_pending(
            key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            transition_payload={"schema": "test.transition/v1"},
        )
        states.add(
            rl.classify_integrity(
                lineage,
                local_sequence=0,
                local_head_sha256="",
                has_local_history=False,
                active_transaction=transaction,
            )
        )

        self.assertEqual(set(rl.INTEGRITY_STATES), states)
        self.assertEqual(
            "awaiting_human_acceptance",
            registry.require_lineage(key).lifecycle_state,
        )

    def test_public_initialize_cannot_bypass_first_use_journal(self) -> None:
        registry = self.registry()
        key = self.key()
        with self.assertRaisesRegex(
            rl.InitializationStateError,
            "initialization journal",
        ):
            registry.initialize(
                key,
                lifecycle_state="awaiting_human_acceptance",
                memory_mode="best_effort",
            )
        self.assertIsNone(registry.get_lineage(key))
        self.assertEqual([], registry.list_active_initializations(key))

    def test_update_stage_rejects_every_non_edge_jump(self) -> None:
        registry = self.registry()
        key = self.key()
        lineage = self.initialize_best_effort(registry, key)
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
        receipt = b'{"receipt":1}'
        with self.assertRaisesRegex(rl.TransactionStateError, "next stage"):
            registry.update_stage(
                transaction.tx_id,
                expected_stage=rl.TX_STAGE_PENDING,
                new_stage=rl.TX_STAGE_READY_FOR_CAS,
                target_head_sha256=HASH_A,
                receipt_path="usecases/UC-REGISTRY-001/evidence/receipt.json",
                receipt_bytes=receipt,
                receipt_mode=0o600,
                receipt_sha256=HASH_A,
            )
        self.assertEqual(rl.TX_STAGE_PENDING, registry.get_transaction(transaction.tx_id).stage)

    def test_required_root_and_checkpoint_are_exact_content_addresses(self) -> None:
        registry = self.registry()
        key = self.key()
        intent = registry.begin_initialization(
            key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
        )
        registry.mark_initialization_root_absence_verified(intent.init_id)
        with self.assertRaisesRegex(rl.LineageValidationError, "root.*content"):
            registry.bind_initialization_root(
                intent.init_id,
                root_memory_id=HASH_A,
            )
        self.assertEqual(
            rl.INIT_STAGE_ROOT_ABSENCE_VERIFIED,
            registry.get_initialization(intent.init_id).stage,
        )

        adopt_key = self.key("099")
        checkpoint = _checkpoint_payload(
            adopt_key,
            sequence=1,
            head_sha256=HASH_A,
            state="awaiting_implementer_acceptance",
            revision=0,
        )
        with self.assertRaisesRegex(rl.LineageValidationError, "checkpoint.*content"):
            registry.adopt(
                adopt_key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=1,
                head_sha256=HASH_A,
                memory_mode="required",
                root_memory_id=_root_id(adopt_key),
                checkpoint_memory_id=HASH_B,
                checkpoint_payload=checkpoint,
            )
        checkpoint["resulting_state"] = "wrong_state"
        with self.assertRaisesRegex(rl.LineageValidationError, "state"):
            registry.adopt(
                adopt_key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=1,
                head_sha256=HASH_A,
                memory_mode="required",
                root_memory_id=_root_id(adopt_key),
                checkpoint_memory_id=_content_id(checkpoint),
                checkpoint_payload=checkpoint,
            )
        extra = _checkpoint_payload(
            adopt_key,
            sequence=1,
            head_sha256=HASH_A,
            state="awaiting_implementer_acceptance",
            revision=0,
        )
        extra["unexpected"] = True
        with self.assertRaisesRegex(rl.LineageValidationError, "non-exact"):
            registry.adopt(
                adopt_key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=1,
                head_sha256=HASH_A,
                memory_mode="required",
                root_memory_id=_root_id(adopt_key),
                checkpoint_memory_id=_content_id(extra),
                checkpoint_payload=extra,
            )
        bad_envelope = _checkpoint_payload(
            adopt_key,
            sequence=1,
            head_sha256=HASH_A,
            state="awaiting_implementer_acceptance",
            revision=0,
        )
        bad_envelope["chain_envelope"]["parsed"]["head_sha256"] = HASH_B
        with self.assertRaisesRegex(rl.LineageValidationError, "parsed value"):
            registry.adopt(
                adopt_key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=1,
                head_sha256=HASH_A,
                memory_mode="required",
                root_memory_id=_root_id(adopt_key),
                checkpoint_memory_id=_content_id(bad_envelope),
                checkpoint_payload=bad_envelope,
            )
        self.assertIsNone(registry.get_lineage(adopt_key))

    def test_memory_mode_rejects_cross_mode_roots_and_checkpoints(self) -> None:
        registry = self.registry()
        best_effort_key = self.key("091")
        checkpoint = _checkpoint_payload(
            best_effort_key,
            sequence=1,
            head_sha256=HASH_A,
            state="awaiting_implementer_acceptance",
            revision=0,
        )
        with self.assertRaisesRegex(rl.LineageValidationError, "best_effort"):
            registry.adopt(
                best_effort_key,
                lifecycle_state="awaiting_implementer_acceptance",
                sequence=1,
                head_sha256=HASH_A,
                memory_mode="best_effort",
                root_memory_id=_root_id(best_effort_key),
                checkpoint_memory_id=_content_id(checkpoint),
                checkpoint_payload=checkpoint,
            )
        self.assertIsNone(registry.get_lineage(best_effort_key))

        required_key = self.key("092")
        intent = registry.begin_initialization(
            required_key,
            lifecycle_state="awaiting_human_acceptance",
            memory_mode="required",
        )
        registry.mark_initialization_root_absence_verified(intent.init_id)
        registry.bind_initialization_root(
            intent.init_id,
            root_memory_id=_root_id(required_key),
        )
        lineage = registry.commit_initialization(intent.init_id)
        registry.mark_initialization_chain_written(intent.init_id)
        registry.complete_initialization(intent.init_id)
        connection = sqlite3.connect(self.home / rl.REGISTRY_FILENAME)
        try:
            connection.execute(
                "UPDATE lineages SET checkpoint_memory_id=? WHERE lineage_id=?",
                (HASH_C, lineage.lineage_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(rl.RegistryIntegrityError, "sequence-zero.*checkpoint"):
            registry.get_lineage(required_key)

    def test_checkpoint_binds_exact_lineage_key_head_sequence_and_state(self) -> None:
        registry = self.registry()
        key = self.key("094")
        mutations = {
            "lineage ID": lambda value: value.__setitem__("lineage_id", HASH_D),
            "lineage key": lambda value: value["lineage_key"].__setitem__(
                "uc", "UC-OTHER"
            ),
            "sequence": lambda value: value.__setitem__("sequence", 2),
            "receipt_sha256": lambda value: value.__setitem__(
                "receipt_sha256", HASH_B
            ),
            "state": lambda value: value.__setitem__(
                "resulting_state", "wrong_state"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                checkpoint = _checkpoint_payload(
                    key,
                    sequence=1,
                    head_sha256=HASH_A,
                    state="awaiting_implementer_acceptance",
                    revision=0,
                )
                mutate(checkpoint)
                with self.assertRaises(rl.LineageValidationError):
                    registry.adopt(
                        key,
                        lifecycle_state="awaiting_implementer_acceptance",
                        sequence=1,
                        head_sha256=HASH_A,
                        memory_mode="required",
                        root_memory_id=_root_id(key),
                        checkpoint_memory_id=_content_id(checkpoint),
                        checkpoint_payload=checkpoint,
                    )
                self.assertIsNone(registry.get_lineage(key))

    def test_checkpoint_rejects_non_role_evidence_envelopes_without_adoption(self) -> None:
        registry = self.registry()
        key = self.key("095")

        def replace_parsed(envelope: dict[str, object], parsed: dict[str, object]) -> None:
            raw = _canonical(parsed)
            envelope["parsed"] = parsed
            envelope["bytes_sha256"] = hashlib.sha256(raw).hexdigest()
            envelope["bytes_base64"] = base64.b64encode(raw).decode("ascii")

        def wrong_receipt_schema(value: dict[str, object]) -> None:
            envelope = value["receipt_envelope"]
            parsed = dict(envelope["parsed"])
            parsed["schema"] = "fixture.receipt/v1"
            replace_parsed(envelope, parsed)

        def wrong_chain_schema(value: dict[str, object]) -> None:
            envelope = value["chain_envelope"]
            parsed = dict(envelope["parsed"])
            parsed["schema"] = "fixture.chain/v1"
            replace_parsed(envelope, parsed)

        mutations = {
            "receipt schema": wrong_receipt_schema,
            "chain schema": wrong_chain_schema,
            "receipt outside artifact": lambda value: value["receipt_envelope"].__setitem__(
                "path", "other/00_role_evidence/receipts/000001-fixture-event-" + HASH_A + ".json"
            ),
            "chain outside artifact": lambda value: value["chain_envelope"].__setitem__(
                "path", "other/00_role_evidence/chain.json"
            ),
            "chain basename": lambda value: value["chain_envelope"].__setitem__(
                "path", f"{key.artifact_dir}/00_role_evidence/head.json"
            ),
            "receipt parent": lambda value: value["receipt_envelope"].__setitem__(
                "path", f"{key.artifact_dir}/00_role_evidence/archive/000001-fixture-event-{HASH_A}.json"
            ),
            "different evidence dirs": lambda value: value["receipt_envelope"].__setitem__(
                "path", f"{key.artifact_dir}/other_evidence/receipts/000001-fixture-event-{HASH_A}.json"
            ),
            "receipt event filename": lambda value: value["receipt_envelope"].__setitem__(
                "path", f"{key.artifact_dir}/00_role_evidence/receipts/000001-other-event-{HASH_A}.json"
            ),
            "receipt sequence filename": lambda value: value["receipt_envelope"].__setitem__(
                "path", f"{key.artifact_dir}/00_role_evidence/receipts/000002-fixture-event-{HASH_A}.json"
            ),
            "receipt hash filename": lambda value: value["receipt_envelope"].__setitem__(
                "path", f"{key.artifact_dir}/00_role_evidence/receipts/000001-fixture-event-{HASH_B}.json"
            ),
            "receipt mode": lambda value: value["receipt_envelope"].__setitem__(
                "mode", 0o644
            ),
            "chain mode": lambda value: value["chain_envelope"].__setitem__(
                "mode", 0o666
            ),
        }
        for index, (label, mutate) in enumerate(mutations.items(), 1):
            with self.subTest(case=label):
                key = self.key(f"095{index:02d}")
                checkpoint = _checkpoint_payload(
                    key,
                    sequence=1,
                    head_sha256=HASH_A,
                    state="awaiting_implementer_acceptance",
                    revision=0,
                )
                before = {
                    path.name: path.read_bytes()
                    for path in self.home.glob(f"{rl.REGISTRY_FILENAME}*")
                    if path.is_file()
                }
                mutate(checkpoint)
                with self.assertRaisesRegex(
                    rl.LineageValidationError,
                    "schema|artifact|chain|receipt|evidence|filename|path",
                ):
                    registry.adopt(
                        key,
                        lifecycle_state="awaiting_implementer_acceptance",
                        sequence=1,
                        head_sha256=HASH_A,
                        memory_mode="required",
                        root_memory_id=_root_id(key),
                        checkpoint_memory_id=_content_id(checkpoint),
                        checkpoint_payload=checkpoint,
                    )
                after = {
                    path.name: path.read_bytes()
                    for path in self.home.glob(f"{rl.REGISTRY_FILENAME}*")
                    if path.is_file()
                }
                self.assertEqual(before, after)
                self.assertIsNone(registry.get_lineage(key))
                self.assertEqual([], registry.list_lineages())


    def test_namespace_and_uc_reject_absolute_path_like_identity(self) -> None:
        invalid = (
            ("/private/project", "UC-1"),
            ("project:/private/project", "UC-1"),
            ("C:\\private\\project", "UC-1"),
            ("project:C:\\private\\project", "UC-1"),
            ("project scope /private/project", "UC-1"),
            ("project:fixture", "/private/UC-1"),
            ("project:fixture", "C:/private/UC-1"),
            ("file:///private/workspace", "UC-1"),
            ("FILE:///private/workspace", "UC-1"),
            ("project:file:///Users/alice/repo", "UC-1"),
            ("project:fixture", "project:FiLe:///private/UC-1"),
            ("file:%2Fprivate%2Frepo", "UC-1"),
            ("project:file:%2FUsers%2Falice%2Frepo", "UC-1"),
        )
        for namespace, uc in invalid:
            with self.subTest(namespace=namespace, uc=uc):
                with self.assertRaisesRegex(rl.LineageValidationError, "absolute path"):
                    rl.build_lineage_key(namespace, uc, "usecases/UC-1")

        for artifact_dir in (
            "file:/private/usecase",
            "file:///private/usecase",
            "FiLe:///Users/alice/usecase",
        ):
            with self.subTest(artifact_dir=artifact_dir):
                with self.assertRaisesRegex(rl.LineageValidationError, "URI|relative"):
                    rl.build_lineage_key(
                        "project:fixture",
                        "UC-1",
                        artifact_dir,
                    )
        self.assertFalse(self.home.exists(), "identity rejection must not create registry state")

    def test_abort_pre_cas_refuses_checkpoint_ready_stages(self) -> None:
        registry = self.registry()
        key = self.key()
        self.initialize_required(registry, key)
        transaction, _ = self.stage_required_transaction(registry, key)
        claim = registry.claim_recovery(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        with self.assertRaisesRegex(rl.TransactionStateError, "checkpoint"):
            registry.abort_pre_cas(
                transaction.tx_id,
                expected_stage=rl.TX_STAGE_READY_FOR_CAS,
                error="operator_abort",
                recovery_token=claim.claim_token,
            )
        self.assertEqual("recovering", registry.get_transaction(transaction.tx_id).status)

        second_key = self.key("093")
        self.initialize_required(registry, second_key)
        second = registry.begin_pending(
            second_key,
            event="human_acceptance",
            expected_head_sha256="",
            expected_sequence=0,
            expected_revision=0,
            expected_checkpoint_memory_id="",
            target_lifecycle_state="ready_for_designer_handoff",
            transition_payload={"schema": "test.transition/v1"},
        )
        second = registry.update_stage(
            second.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
            transition_memory_id="transition-memory-exact-id",
        )
        second = registry.update_stage(
            second.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
            new_stage=rl.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=HASH_A,
            receipt_path="usecases/UC-REGISTRY-093/evidence/receipt.json",
            receipt_bytes=b'{"receipt":1}',
            receipt_mode=0o600,
            receipt_sha256=HASH_A,
        )
        second = registry.update_stage(
            second.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_BOUND,
            new_stage=rl.TX_STAGE_MEMORY_FINALIZED,
        )
        second_claim = registry.claim_recovery(second.tx_id)
        with self.assertRaisesRegex(rl.TransactionStateError, "must be resumed"):
            registry.abort_pre_cas(
                second.tx_id,
                expected_stage=rl.TX_STAGE_MEMORY_FINALIZED,
                error="operator_abort",
                recovery_token=second_claim.claim_token,
            )

    def test_atomic_recovery_handoff_has_no_aligned_without_audit_gap(self) -> None:
        registry = self.registry()
        key = self.key()
        lineage = self.initialize_best_effort(registry, key)
        restore = registry.begin_pending(
            key,
            event="recovery_restore",
            expected_head_sha256=lineage.head_sha256,
            expected_sequence=lineage.sequence,
            expected_revision=lineage.revision,
            expected_checkpoint_memory_id=lineage.checkpoint_memory_id,
            target_lifecycle_state=lineage.lifecycle_state,
            transition_payload={"schema": "test.recovery-restore/v1"},
        )
        claim = registry.claim_recovery(restore.tx_id, expected_stage=rl.TX_STAGE_PENDING)
        payload = _recovery_transition(key, lineage)
        handoff = registry.handoff_recovery_successor(
            restore.tx_id,
            claim_token=claim.claim_token,
            transition_payload=payload,
            successor_tx_id="evidence-recovery-successor",
        )
        self.assertEqual("aborted", handoff.terminal_transaction.status)
        self.assertEqual("evidence_recovery", handoff.successor_transaction.event)
        self.assertEqual("pending", handoff.successor_transaction.status)
        self.assertEqual(_canonical(payload), handoff.successor_transaction.transition_payload)
        self.assertEqual(lineage.head_sha256, handoff.successor_transaction.expected_head_sha256)
        self.assertEqual(lineage.sequence, handoff.successor_transaction.expected_sequence)
        self.assertEqual(lineage.revision, handoff.successor_transaction.expected_revision)
        self.assertEqual([handoff.successor_transaction], registry.list_active_transactions(key))

    def test_atomic_recovery_handoff_rolls_back_terminalization_on_insert_conflict(self) -> None:
        registry = self.registry()
        key = self.key()
        lineage = self.initialize_best_effort(registry, key)
        prior = registry.begin_pending(
            key,
            event="recovery_restore",
            expected_head_sha256=lineage.head_sha256,
            expected_sequence=lineage.sequence,
            expected_revision=lineage.revision,
            expected_checkpoint_memory_id=lineage.checkpoint_memory_id,
            target_lifecycle_state=lineage.lifecycle_state,
            tx_id="reserved-successor-id",
            transition_payload={"schema": "test.recovery-restore/v1"},
        )
        prior_claim = registry.claim_recovery(prior.tx_id)
        registry.abort_pre_cas(
            prior.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            error="fixture_complete",
            recovery_token=prior_claim.claim_token,
        )
        current = registry.begin_pending(
            key,
            event="recovery_restore",
            expected_head_sha256=lineage.head_sha256,
            expected_sequence=lineage.sequence,
            expected_revision=lineage.revision,
            expected_checkpoint_memory_id=lineage.checkpoint_memory_id,
            target_lifecycle_state=lineage.lifecycle_state,
            transition_payload={"schema": "test.recovery-restore/v1"},
        )
        claim = registry.claim_recovery(current.tx_id)
        with self.assertRaisesRegex(
            rl.LineageValidationError,
            "lineage precondition",
        ):
            registry.handoff_recovery_successor(
                current.tx_id,
                claim_token=claim.claim_token,
                transition_payload={"event": "evidence_recovery"},
            )
        still_claimed = registry.get_transaction(current.tx_id)
        self.assertEqual("recovering", still_claimed.status)
        self.assertEqual(claim.claim_token, still_claimed.recovery_token)
        with self.assertRaises(rl.RegistryIntegrityError):
            registry.handoff_recovery_successor(
                current.tx_id,
                claim_token=claim.claim_token,
                transition_payload=_recovery_transition(key, lineage),
                successor_tx_id="reserved-successor-id",
            )
        unchanged = registry.get_transaction(current.tx_id)
        self.assertEqual("recovering", unchanged.status)
        self.assertEqual(rl.TX_STAGE_PENDING, unchanged.stage)
        self.assertEqual([unchanged], registry.list_active_transactions(key))

    def test_atomic_recovery_handoff_completes_chain_replaced_original(self) -> None:
        registry = self.registry()
        key = self.key()
        lineage = self.initialize_best_effort(registry, key)
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
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_PENDING,
            new_stage=rl.TX_STAGE_MEMORY_PREPARED,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_PREPARED,
            new_stage=rl.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=HASH_A,
            receipt_path="usecases/UC-REGISTRY-001/evidence/receipt.json",
            receipt_bytes=b'{"receipt":1}',
            receipt_mode=0o600,
            receipt_sha256=HASH_A,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_BOUND,
            new_stage=rl.TX_STAGE_MEMORY_FINALIZED,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_MEMORY_FINALIZED,
            new_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        committed = registry.compare_and_swap_head(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_READY_FOR_CAS,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_REGISTRY_COMMITTED,
            new_stage=rl.TX_STAGE_RECEIPT_WRITTEN,
        )
        transaction = registry.update_stage(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_RECEIPT_WRITTEN,
            new_stage=rl.TX_STAGE_CHAIN_REPLACED,
        )
        claim = registry.claim_recovery(
            transaction.tx_id,
            expected_stage=rl.TX_STAGE_CHAIN_REPLACED,
        )
        handoff = registry.handoff_recovery_successor(
            transaction.tx_id,
            claim_token=claim.claim_token,
            transition_payload=_recovery_transition(key, committed.lineage),
        )
        self.assertEqual("completed", handoff.terminal_transaction.status)
        self.assertEqual("pending", handoff.successor_transaction.status)
        self.assertEqual(HASH_A, handoff.successor_transaction.expected_head_sha256)
        self.assertEqual(1, handoff.successor_transaction.expected_sequence)
        self.assertEqual(1, handoff.successor_transaction.expected_revision)


if __name__ == "__main__":
    unittest.main(verbosity=2)
