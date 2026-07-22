#!/usr/bin/env python3
"""SUT-neutral transaction, recovery, concurrency, and rollback tests."""
from __future__ import annotations

import ast
import errno
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

import bugate_update_transaction as transaction  # noqa: E402
import bugate_install_contract as contract  # noqa: E402
from tests.test_bugate_update_engine import legacy_manifest, release_tree  # noqa: E402


def file_image(data: bytes, mode: str = "0644") -> dict[str, str]:
    return {"type": "file", "sha256": hashlib.sha256(data).hexdigest(), "mode": mode}


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def _tree_bytes(
    root: Path, *, include_root_timestamps: bool = True
) -> dict[str, tuple[Any, ...]]:
    """Capture content plus persistent metadata for zero-write assertions."""

    def record(
        kind: str,
        path: Path,
        payload: bytes | str,
        *,
        include_timestamps: bool = True,
    ) -> tuple[Any, ...]:
        metadata = os.lstat(path)
        return (
            kind,
            payload,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns if include_timestamps else None,
            metadata.st_ctime_ns if include_timestamps else None,
        )

    snapshot: dict[str, tuple[Any, ...]] = {
        ".": record(
            "directory",
            root,
            "",
            include_timestamps=include_root_timestamps,
        )
    }
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = record("symlink", path, os.readlink(path))
            else:
                snapshot[relative] = record("directory", path, "")
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = record("symlink", path, os.readlink(path))
            else:
                snapshot[relative] = record("file", path, path.read_bytes())
    return snapshot


def _rewrite_sealed(path: Path, **updates: Any) -> None:
    document = json.loads(path.read_bytes())
    document.pop("self_digest", None)
    document.update(updates)
    path.write_bytes(contract.canonical_json_bytes(contract.seal_document(document)))


ABSENT = {"type": "absent"}
DIRECTORY = {"type": "directory", "mode": "0755"}


# Source-synchronized audit catalog. The ordered tuple tracks every call site
# (including the four type-change branches sharing one family); the case map
# below tracks one concrete executable representative for every unique family.
CANONICAL_INJECTION_CALLS = (
    "after_recovery_plan_lock_publish",
    "after_recovery_archive_marker",
    "after_recovery_state_archive_publish",
    "after_archive_reuse_final_marker",
    "after_target_removal:{id}",
    "after_target_removal:{id}",
    "after_target_removal:{id}",
    "after_target_removal:{id}",
    "after_prepare_transaction_publish",
    "after_prepare_transaction_dir_create",
    "after_prepare_bundles_before_journal",
    "after_prepare_transaction_publish",
    "after_prepare",
    "before_mutation:{id}",
    "after_mutation:{id}",
    "after_precommit_verify",
    "before_installed_lock",
    "after_installed_lock",
    "after_verify",
    "after_report_pending",
    "after_journal_commit",
    "after_commit",
    "after_archive_reuse_intent",
    "after_archive_reuse_prepare",
    "before_bootstrap_publish",
    "after_bootstrap_publish",
    "after_archive_reuse_activate",
    "after_gitignore",
    "after_root_state_publish",
    "after_root_state_migration",
    "after_archive_intent",
    "before_bootstrap_settle",
    "before_legacy_archive",
    "before_bootstrap_settle",
    "before_legacy_archive",
    "before_archive_publish",
    "after_archive_publish",
    "after_archive_root_retire",
)
CANONICAL_INJECTION_FAMILIES = frozenset(CANONICAL_INJECTION_CALLS)

INJECTION_POINT_CASES = {
    "after_prepare": "normal",
    "before_mutation:runtime": "normal",
    "after_mutation:runtime": "normal",
    "after_precommit_verify": "normal",
    "before_installed_lock": "normal",
    "after_installed_lock": "normal",
    "after_verify": "normal",
    "after_report_pending": "normal",
    "after_journal_commit": "normal",
    "after_commit": "normal",
    "after_target_removal:type-change": "type-change",
    "before_bootstrap_publish": "bootstrap",
    "after_bootstrap_publish": "bootstrap",
    "after_gitignore": "bootstrap",
    "after_root_state_publish": "bootstrap",
    "after_root_state_migration": "bootstrap",
    "before_bootstrap_settle": "bootstrap",
    "before_archive_publish": "archive",
    "after_archive_publish": "archive",
    "after_archive_root_retire": "archive",
    "after_archive_intent": "rollback-archive",
    "before_legacy_archive": "rollback-archive",
    "after_archive_reuse_intent": "reuse",
    "after_prepare_transaction_dir_create": "reuse",
    "after_prepare_bundles_before_journal": "reuse",
    "after_prepare_transaction_publish": "reuse",
    "after_archive_reuse_prepare": "reuse",
    "after_archive_reuse_activate": "reuse",
    "after_recovery_plan_lock_publish": "recovery-bootstrap",
    "after_recovery_archive_marker": "recovery-bootstrap",
    "after_recovery_state_archive_publish": "recovery-bootstrap",
    "after_archive_reuse_final_marker": "reuse-finalize",
}


MATRIX_WORKER = textwrap.dedent(
    r"""
    import hashlib
    import os
    import sys
    from pathlib import Path

    root = Path(sys.argv[1])
    scenario = sys.argv[2]
    mode = sys.argv[3]
    point = sys.argv[4]
    scripts = sys.argv[5]
    marker = Path(sys.argv[6])
    sys.path.insert(0, scripts)
    from bugate_update_transaction import Operation, TransactionManager

    for key in (
        "BUGATE_UPDATE_FAILPOINT",
        "BUGATE_UPDATE_CRASHPOINT",
        "BUGATE_UPDATE_PAUSEPOINT",
    ):
        os.environ.pop(key, None)

    def image(data):
        return {
            "type": "file",
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": "0644",
        }

    def initialize():
        root.mkdir()
        (root / ".bugate").mkdir()
        (root / "sut-owned.txt").write_bytes(b"operator-owned\n")

    def arm():
        os.environ[
            "BUGATE_UPDATE_FAILPOINT"
            if mode == "fail"
            else "BUGATE_UPDATE_CRASHPOINT"
        ] = point

    def record(name):
        if name == point:
            marker.write_text(name, encoding="utf-8")

    def manager():
        return TransactionManager(root, injector=record)

    def committed_history(identity="1" * 32):
        runtime = root / ".bugate/runtime"
        runtime.write_bytes(b"history-old")
        return TransactionManager(root).apply(
            [Operation("history", ".bugate/runtime", image(b"history-old"), image(b"history-new"))],
            payload_bytes={"history": b"history-new"},
            transaction_id=identity,
        )

    def bootstrap_values():
        old = b"/.bugate/plan.lock\n"
        new = old + b"/.bugate-update/\n"
        (root / ".gitignore").write_bytes(old)
        return old, new

    try:
        if scenario == "normal":
            initialize()
            (root / ".bugate/runtime").write_bytes(b"normal-old")
            operations = [
                Operation("runtime", ".bugate/runtime", image(b"normal-old"), image(b"normal-new")),
                Operation("metadata:installed-lock", ".bugate/bugate.lock.json", {"type": "absent"}, image(b"lock-new")),
            ]
            arm()
            manager().apply(
                operations,
                payload_bytes={"runtime": b"normal-new", "metadata:installed-lock": b"lock-new"},
                transaction_id="a" * 32,
            )
        elif scenario == "type-change":
            initialize()
            (root / ".bugate/type-target").mkdir()
            arm()
            manager().apply(
                [Operation("type-change", ".bugate/type-target", {"type": "directory", "mode": "0755"}, image(b"type-new"))],
                payload_bytes={"type-change": b"type-new"},
                transaction_id="b" * 32,
            )
        elif scenario == "bootstrap":
            initialize()
            old, new = bootstrap_values()
            (root / ".bugate/runtime").write_bytes(b"bootstrap-old")
            operations = [
                Operation("gitignore", ".gitignore", image(old), image(new)),
                Operation("runtime", ".bugate/runtime", image(b"bootstrap-old"), image(b"bootstrap-new")),
            ]
            arm()
            manager().apply(
                operations,
                payload_bytes={"gitignore": new, "runtime": b"bootstrap-new"},
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="c" * 32,
            )
        elif scenario == "archive":
            initialize()
            committed_history()
            arm()
            manager().archive_legacy_rollback_state()
        elif scenario == "rollback-archive":
            initialize()
            report = committed_history()
            arm()
            manager().rollback(report["transaction_id"], archive_legacy=True)
        elif scenario == "reuse":
            initialize()
            committed_history()
            TransactionManager(root).archive_legacy_rollback_state()
            old, new = bootstrap_values()
            arm()
            manager().apply(
                [Operation("gitignore", ".gitignore", image(old), image(new))],
                payload_bytes={"gitignore": new},
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="d" * 32,
            )
        elif scenario == "recovery-bootstrap":
            initialize()
            old, new = bootstrap_values()
            (root / ".bugate/runtime").write_bytes(b"recovery-old")
            operations = [
                Operation("gitignore", ".gitignore", image(old), image(new)),
                Operation("runtime", ".bugate/runtime", image(b"recovery-old"), image(b"recovery-new")),
            ]
            def fail_verify():
                raise RuntimeError("force bootstrap recovery")
            arm()
            manager().apply(
                operations,
                payload_bytes={"gitignore": new, "runtime": b"recovery-new"},
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="e" * 32,
                verify=fail_verify,
            )
        elif scenario == "reuse-prepare":
            initialize()
            committed_history()
            TransactionManager(root).archive_legacy_rollback_state()
            old, new = bootstrap_values()
            os.environ["BUGATE_UPDATE_CRASHPOINT"] = "after_archive_reuse_activate"
            TransactionManager(root).apply(
                [Operation("gitignore", ".gitignore", image(old), image(new))],
                payload_bytes={"gitignore": new},
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="d" * 32,
            )
        elif scenario == "reuse-finalize":
            arm()
            manager().recover()
        else:
            raise RuntimeError("unknown matrix scenario: " + scenario)
    except BaseException as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(86)
    """
)


class TransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="bugate-update-transaction-")
        self.root = Path(self.temporary.name) / "synthetic-repo"
        self.root.mkdir()
        (self.root / ".bugate").mkdir()
        self.manager = transaction.TransactionManager(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_zero_write_snapshot_detects_metadata_mutation_and_restoration(self) -> None:
        sentinel = self.root / ".bugate/sentinel"
        sentinel.write_bytes(b"stable\n")
        original_mode = os.lstat(sentinel).st_mode & 0o777
        before = _tree_bytes(self.root)

        time.sleep(0.01)
        os.chmod(sentinel, original_mode ^ 0o100)
        self.assertNotEqual(_tree_bytes(self.root), before)

        time.sleep(0.01)
        os.chmod(sentinel, original_mode)
        self.assertNotEqual(
            _tree_bytes(self.root),
            before,
            "a chmod-and-restore write must remain visible through ctime",
        )

        workspace_before = _tree_bytes(self.root)
        workspace_mode = os.lstat(self.root).st_mode & 0o777
        time.sleep(0.01)
        os.chmod(self.root, workspace_mode ^ 0o100)
        time.sleep(0.01)
        os.chmod(self.root, workspace_mode)
        self.assertNotEqual(
            _tree_bytes(self.root),
            workspace_before,
            "workspace-root metadata writes must remain visible through ctime",
        )

    def op(
        self,
        identity: str,
        target: str,
        pre: dict,
        post: dict,
    ) -> transaction.Operation:
        return transaction.Operation(identity, target, pre, post)

    def archived_runtime_history(
        self, transaction_id: str = "8" * 32
    ) -> tuple[Path, dict[str, Any]]:
        target = self.root / ".bugate/runtime"
        target.write_bytes(b"archive-old")
        report = self.manager.apply(
            [
                self.op(
                    "runtime",
                    ".bugate/runtime",
                    file_image(b"archive-old"),
                    file_image(b"archive-new"),
                )
            ],
            payload_bytes={"runtime": b"archive-new"},
            transaction_id=transaction_id,
        )
        self.manager.archive_legacy_rollback_state()
        return self.root / ".bugate/plan.lock/bugate-update", report

    def test_same_version_noop_writes_no_state(self) -> None:
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        result = self.manager.apply([])
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(result, {"status": "no-op", "transaction_id": None, "state_written": False})
        self.assertEqual(after, before)
        self.assertFalse((self.root / ".bugate-update").exists())

    def test_same_version_facade_noop_has_committed_audit_schema_and_zero_writes(self) -> None:
        base = Path(self.temporary.name) / "noop-release-base"
        base.mkdir()
        release, manifest = release_tree(base, "0.4.2", b"noop\n")
        installed_lock = self.root / ".bugate/bugate.lock.json"
        original_lock = b"existing deterministic lock bytes\n"
        installed_lock.write_bytes(original_lock)
        verified_input_digest = "e" * 64
        prepared = SimpleNamespace(
            root=release,
            manifest=manifest,
            archive_sha256=verified_input_digest,
            source_kind="archive",
            root_identity=(os.lstat(release).st_dev, os.lstat(release).st_ino),
        )
        plan = {
            "decision": "GO",
            "no_op": True,
            "from_version": "0.4.2",
            "to_version": "0.4.2",
            "release_digest": manifest["self_digest"],
            "manifest_sha256": contract.sha256_bytes(
                contract.canonical_json_bytes(manifest)
            ),
            "target_manifest": manifest,
            "source_kind": "archive",
            "archive_sha256": verified_input_digest,
            "profile_compatibility": {
                "status": "compatible",
                "blocking": False,
                "migration": "migration_available",
            },
            "codex_hook_hash_changed": False,
            "new_session_required": False,
            "rollback_available": False,
        }
        import bugate_update_engine as engine

        with mock.patch.object(engine, "validate_plan_base"):
            report = transaction.apply_update(
                self.root,
                ".bugate",
                prepared,
                plan,
                updater_version="0.4.2",
            )
        common_audit_fields = {
            "schema_version",
            "decision",
            "status",
            "no_op",
            "transaction_id",
            "engine_updated",
            "from_version",
            "to_version",
            "release_digest",
            "archive_sha256",
            "source_kind",
            "manifest_sha256",
            "profile_migration",
            "codex_hook_hash_changed",
            "new_session_required",
            "memory_checked",
            "role_governance_activated",
            "rollback_available",
        }
        self.assertTrue(common_audit_fields.issubset(report))
        self.assertEqual(report["schema_version"], transaction.STATE_SCHEMA)
        self.assertEqual(report["decision"], "GO")
        self.assertEqual(report["status"], "no-op")
        self.assertTrue(report["no_op"])
        self.assertIsNone(report["transaction_id"])
        self.assertFalse(report["state_written"])
        self.assertEqual(report["archive_sha256"], verified_input_digest)
        self.assertEqual(report["source_kind"], "archive")
        self.assertEqual(installed_lock.read_bytes(), original_lock)
        self.assertFalse((self.root / ".bugate-update").exists())

    def test_prepared_source_identity_mismatch_is_zero_write_no_go(self) -> None:
        base = Path(self.temporary.name) / "identity-release-base"
        base.mkdir()
        release, manifest = release_tree(base, "0.4.2", b"identity\n")
        archive_digest = "a" * 64
        prepared = SimpleNamespace(
            root=release,
            manifest=manifest,
            archive_sha256=archive_digest,
            source_kind="archive",
            root_identity=(os.lstat(release).st_dev, os.lstat(release).st_ino),
        )
        valid = {
            "decision": "GO",
            "no_op": True,
            "to_version": "0.4.2",
            "release_digest": manifest["self_digest"],
            "manifest_sha256": contract.sha256_bytes(
                contract.canonical_json_bytes(manifest)
            ),
            "archive_sha256": archive_digest,
            "source_kind": "archive",
            "target_manifest": manifest,
        }
        mismatches = {
            "to_version": "0.4.3",
            "release_digest": "b" * 64,
            "manifest_sha256": "c" * 64,
            "archive_sha256": "d" * 64,
            "source_kind": "unpacked",
            "target_manifest": {},
        }
        import bugate_update_engine as engine

        for field, wrong in mismatches.items():
            with self.subTest(field=field), mock.patch.object(
                engine, "validate_plan_base"
            ):
                plan = dict(valid)
                plan[field] = wrong
                with self.assertRaisesRegex(
                    transaction.TransactionError, "prepared release identity"
                ):
                    transaction.apply_update(
                        self.root,
                        ".bugate",
                        prepared,
                        plan,
                        updater_version="0.4.2",
                    )
                self.assertFalse((self.root / ".bugate-update").exists())

    def test_prepared_release_root_exchange_is_zero_write_no_go(self) -> None:
        base = Path(self.temporary.name) / "root-exchange-release-base"
        base.mkdir()
        release, manifest = release_tree(base, "0.4.2", b"root-bound\n")
        prepared = SimpleNamespace(
            root=release,
            manifest=manifest,
            archive_sha256=None,
            source_kind="unpacked",
            root_identity=(os.lstat(release).st_dev, os.lstat(release).st_ino),
        )
        plan = {
            "decision": "GO",
            "no_op": True,
            "from_version": "0.4.2",
            "to_version": "0.4.2",
            "release_digest": manifest["self_digest"],
            "manifest_sha256": contract.sha256_bytes(
                contract.canonical_json_bytes(manifest)
            ),
            "archive_sha256": None,
            "source_kind": "unpacked",
            "target_manifest": manifest,
            "profile_compatibility": {"status": "compatible"},
            "rollback_available": True,
        }
        moved = base / "moved-release"
        os.rename(release, moved)
        shutil.copytree(moved, release, symlinks=True)
        before = _tree_bytes(self.root)
        with mock.patch(
            "bugate_update_engine.validate_plan_base", return_value=None
        ), self.assertRaisesRegex(
            transaction.TransactionError, "root identity changed"
        ):
            transaction.apply_update(
                self.root,
                ".bugate",
                prepared,
                plan,
                updater_version="0.4.2",
            )
        self.assertEqual(_tree_bytes(self.root), before)
        self.assertFalse((self.root / ".bugate-update").exists())

    def test_workspace_flock_rejects_concurrent_manager(self) -> None:
        with self.manager.workspace_lock():
            with self.assertRaises(transaction.ConcurrentUpdateError):
                with transaction.TransactionManager(self.root).workspace_lock():
                    self.fail("second lock unexpectedly succeeded")

    def test_workspace_lock_rejects_physical_root_replacement(self) -> None:
        displaced = Path(self.temporary.name) / "displaced-root"
        os.rename(self.root, displaced)
        self.root.mkdir()
        (self.root / "operator-owned.txt").write_bytes(b"preserve")
        with self.assertRaisesRegex(
            transaction.UnsafePathError, "root identity changed"
        ):
            with self.manager.workspace_lock():
                self.fail("replacement root unexpectedly acquired the transaction lock")
        self.assertEqual(
            (self.root / "operator-owned.txt").read_bytes(), b"preserve"
        )
        self.assertTrue((displaced / ".bugate").is_dir())

    def test_verified_worker_executes_outside_vendor_during_self_update(self) -> None:
        scripts = self.root / ".bugate/scripts"
        scripts.mkdir()
        old = b"old vendored transaction worker\n"
        new = b"new vendored transaction worker\n"
        target = scripts / "bugate_update_transaction.py"
        target.write_bytes(old)
        operation = self.op(
            "self-update",
            ".bugate/scripts/bugate_update_transaction.py",
            file_image(old),
            file_image(new),
        )
        worker_names = (
            "bugate_update.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_install_contract.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
        )
        report = self.manager.apply(
            [operation],
            payload_bytes={"self-update": new},
            worker_files={name: SCRIPTS / name for name in worker_names},
            execute_worker=True,
            execute_worker_verify=False,
        )
        self.assertEqual(report["status"], "committed")
        self.assertEqual(target.read_bytes(), new)
        tx = self.root / ".bugate-update/transactions" / report["transaction_id"]
        self.assertTrue((tx / "worker/.digests.json").is_file())
        self.assertNotEqual((tx / "worker/bugate_update_transaction.py").read_bytes(), new)

    def test_worker_directory_exchange_before_exec_is_rejected_and_restored(self) -> None:
        old, new = b"worker-bound-old", b"worker-bound-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        operation = self.op(
            "runtime", ".bugate/runtime", file_image(old), file_image(new)
        )
        names = (
            "bugate_update.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_install_contract.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
        )
        moved = Path(self.temporary.name) / "moved-worker"
        moved_before: dict[str, tuple] | None = None
        swapped = False
        original = transaction._validate_bundle

        def exchange(directory: Path) -> None:
            nonlocal moved_before, swapped
            current = Path.cwd()
            if not swapped and current.name == "worker":
                swapped = True
                os.rename(current, moved)
                moved_before = _tree_bytes(moved)
                current.mkdir()
            original(directory)

        with mock.patch.object(
            transaction, "_validate_bundle", side_effect=exchange
        ), self.assertRaises(transaction.JournalError):
            self.manager.apply(
                [operation],
                payload_bytes={"runtime": new},
                worker_files={name: SCRIPTS / name for name in names},
                execute_worker=True,
                execute_worker_verify=False,
            )
        self.assertTrue(swapped)
        self.assertEqual(target.read_bytes(), old)
        self.assertIsNotNone(moved_before)
        self.assertEqual(_tree_bytes(moved), moved_before)

    def test_worker_hard_crash_is_recovered_by_waiting_parent(self) -> None:
        old, new = b"worker-old", b"worker-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        operation = self.op("runtime", ".bugate/runtime", file_image(old), file_image(new))
        names = (
            "bugate_update.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_install_contract.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
        )
        with mock.patch.dict(os.environ, {"BUGATE_UPDATE_CRASHPOINT": "after_mutation:runtime"}):
            with self.assertRaises(transaction.TransactionError):
                self.manager.apply(
                    [operation],
                    payload_bytes={"runtime": new},
                    worker_files={name: SCRIPTS / name for name in names},
                    execute_worker=True,
                    execute_worker_verify=False,
                )
        self.assertEqual(target.read_bytes(), old)
        self.assertIsNone(self.manager.recovery_required())
        reports = list((self.root / ".bugate-update/transactions").glob("*/failure-report.json"))
        self.assertEqual(len(reports), 1)

    def test_worker_sigterm_is_handled_and_restored(self) -> None:
        old, new = b"signal-old", b"signal-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        names = (
            "bugate_update.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_install_contract.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
        )
        worker_literal = {
            name: str(SCRIPTS / name)
            for name in names
        }
        script = f"""
import hashlib, os, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_PAUSEPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},worker_files={{name:Path(path) for name,path in {worker_literal!r}.items()}},execute_worker=True,execute_worker_verify=False)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and target.read_bytes() != new:
            time.sleep(0.02)
        self.assertEqual(target.read_bytes(), new)
        os.killpg(process.pid, signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0, stderr)
        self.assertEqual(target.read_bytes(), old)
        self.assertIsNone(self.manager.recovery_required())
        reports = list((self.root / ".bugate-update/transactions").glob("*/failure-report.json"))
        self.assertEqual(len(reports), 1)
        self.assertIn('"status":"recovered"', reports[0].read_text(encoding="utf-8"))

    def test_atomic_add_update_delete_permission_symlink_and_rollback(self) -> None:
        old = b"old-runtime\n"
        new = b"new-runtime\n"
        stale = b"obsolete\n"
        (self.root / ".bugate" / "runtime.py").write_bytes(old)
        (self.root / ".bugate" / "obsolete.py").write_bytes(stale)
        (self.root / ".bugate" / "runtime-link").symlink_to("runtime.py")
        operations = [
            self.op("update", ".bugate/runtime.py", file_image(old), file_image(new, "0755")),
            self.op("delete", ".bugate/obsolete.py", file_image(stale), ABSENT),
            self.op(
                "link",
                ".bugate/runtime-link",
                {"type": "symlink", "target": "runtime.py", "mode": "0777"},
                {"type": "symlink", "target": "bin/runtime", "mode": "0777"},
            ),
            self.op("add-dir", ".bugate/bin", ABSENT, DIRECTORY),
            self.op("add", ".bugate/bin/runtime", ABSENT, file_image(new, "0755")),
        ]
        result = self.manager.apply(
            operations,
            payload_bytes={"update": new, "add": new},
            transaction_id="1" * 32,
        )
        self.assertEqual(result["status"], "committed")
        self.assertEqual((self.root / ".bugate/runtime.py").read_bytes(), new)
        self.assertEqual(os.stat(self.root / ".bugate/runtime.py").st_mode & 0o777, 0o755)
        self.assertFalse((self.root / ".bugate/obsolete.py").exists())
        self.assertEqual(os.readlink(self.root / ".bugate/runtime-link"), "bin/runtime")

        rolled_back = self.manager.rollback("1" * 32)
        self.assertEqual(rolled_back["kind"], "rollback")
        self.assertEqual((self.root / ".bugate/runtime.py").read_bytes(), old)
        self.assertEqual(os.stat(self.root / ".bugate/runtime.py").st_mode & 0o777, 0o644)
        self.assertEqual((self.root / ".bugate/obsolete.py").read_bytes(), stale)
        self.assertEqual(os.readlink(self.root / ".bugate/runtime-link"), "runtime.py")
        self.assertFalse((self.root / ".bugate/bin/runtime").exists())
        self.assertFalse((self.root / ".bugate/bin").exists())

    def test_shared_file_modes_are_preserved_exactly_across_apply_and_rollback(self) -> None:
        hook_old, hook_new = b'{"hooks":{}}\n', b'{"hooks":{"Stop":[]}}\n'
        ignore_old, ignore_new = b"sut-owned\n", b"sut-owned\n# managed\n"
        hook = self.root / ".codex/hooks.json"
        ignore = self.root / ".gitignore"
        hook.parent.mkdir()
        hook.write_bytes(hook_old)
        ignore.write_bytes(ignore_old)
        os.chmod(hook, 0o600)
        os.chmod(ignore, 0o664)
        operations = [
            self.op(
                "shared-hook",
                ".codex/hooks.json",
                file_image(hook_old, "0600"),
                file_image(hook_new, "0600"),
            ),
            self.op(
                "shared-ignore",
                ".gitignore",
                file_image(ignore_old, "0664"),
                file_image(ignore_new, "0664"),
            ),
        ]
        report = self.manager.apply(
            operations,
            payload_bytes={"shared-hook": hook_new, "shared-ignore": ignore_new},
        )
        self.assertEqual(stat_mode(hook), 0o600)
        self.assertEqual(stat_mode(ignore), 0o664)
        self.manager.rollback(report["transaction_id"])
        self.assertEqual(hook.read_bytes(), hook_old)
        self.assertEqual(ignore.read_bytes(), ignore_old)
        self.assertEqual(stat_mode(hook), 0o600)
        self.assertEqual(stat_mode(ignore), 0o664)

    def test_prepared_worker_source_drift_is_zero_target_write_no_go(self) -> None:
        base = Path(self.temporary.name) / "release-base"
        base.mkdir()
        release, _manifest = release_tree(base, "0.4.2", b"target\n")
        worker_names = (
            "bugate_update.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_install_contract.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
        )
        for name in worker_names:
            shutil.copyfile(SCRIPTS / name, release / "scripts" / name)
        manifest = contract.build_release_manifest(
            release, "0.4.2", updater_minimum_version="0.4.2"
        )
        # The source was valid when prepared, then one worker changed before
        # apply.  The manifest remains the immutable expected baseline.
        (release / "scripts/bugate_core.py").write_bytes(b"drift after prepare\n")
        prepared = SimpleNamespace(
            root=release,
            manifest=manifest,
            archive_sha256=None,
            source_kind="unpacked",
            root_identity=(os.lstat(release).st_dev, os.lstat(release).st_ino),
        )
        plan = {
            "decision": "GO",
            "no_op": False,
            "installed_kind": "locked",
            "profile_compatibility": {},
            "to_version": "0.4.2",
            "release_digest": manifest["self_digest"],
            "manifest_sha256": contract.sha256_bytes(
                contract.canonical_json_bytes(manifest)
            ),
            "archive_sha256": None,
            "source_kind": "unpacked",
            "target_manifest": manifest,
        }
        import bugate_update_engine as engine

        with mock.patch.object(engine, "validate_plan_base"), mock.patch.object(
            engine, "materialize_shared_outputs", return_value={}
        ), mock.patch.object(
            engine,
            "transaction_material",
            return_value={
                "operations": [],
                "payload_sources": {},
                "payload_bytes": {},
                "gitignore_operation_id": None,
            },
        ):
            with self.assertRaisesRegex(transaction.TransactionError, "digest drifted"):
                transaction.apply_update(
                    self.root,
                    ".bugate",
                    prepared,
                    plan,
                    updater_version="0.4.2",
                )
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [".bugate"])

    def test_handled_failure_restores_all_preimages(self) -> None:
        old_one, old_two = b"one-old", b"two-old"
        new_one, new_two = b"one-new", b"two-new"
        (self.root / ".bugate/one").write_bytes(old_one)
        (self.root / ".bugate/two").write_bytes(old_two)

        def fail(name: str) -> None:
            if name == "after_mutation:one":
                raise transaction.InjectedFailure("synthetic failure")

        manager = transaction.TransactionManager(self.root, injector=fail)
        operations = [
            self.op("one", ".bugate/one", file_image(old_one), file_image(new_one)),
            self.op("two", ".bugate/two", file_image(old_two), file_image(new_two)),
        ]
        with self.assertRaisesRegex(transaction.InjectedFailure, "synthetic"):
            manager.apply(operations, payload_bytes={"one": new_one, "two": new_two})
        self.assertEqual((self.root / ".bugate/one").read_bytes(), old_one)
        self.assertEqual((self.root / ".bugate/two").read_bytes(), old_two)
        self.assertIsNone(manager.recovery_required())
        reports = list((self.root / ".bugate-update/transactions").glob("*/failure-report.json"))
        self.assertEqual(len(reports), 1)
        report_text = reports[0].read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), report_text)
        self.assertIn('"status":"recovered"', report_text)

    def test_installed_lock_is_written_only_after_precommit_verify(self) -> None:
        old_content, new_content = b"content-old", b"content-new"
        old_lock, new_lock = b"lock-old", b"lock-new"
        content = self.root / ".bugate/content"
        lock = self.root / ".bugate/bugate.lock.json"
        content.write_bytes(old_content)
        lock.write_bytes(old_lock)
        operations = [
            self.op("content", ".bugate/content", file_image(old_content), file_image(new_content)),
            self.op(
                "metadata:installed-lock",
                ".bugate/bugate.lock.json",
                file_image(old_lock),
                file_image(new_lock),
            ),
        ]
        observations: list[str] = []

        def precommit() -> None:
            self.assertEqual(content.read_bytes(), new_content)
            self.assertEqual(lock.read_bytes(), old_lock)
            observations.append("precommit")

        def final() -> None:
            self.assertEqual(lock.read_bytes(), new_lock)
            observations.append("final")

        self.manager.apply(
            operations,
            payload_bytes={"content": new_content, "metadata:installed-lock": new_lock},
            precommit_verify=precommit,
            verify=final,
        )
        self.assertEqual(observations, ["precommit", "final"])

    def test_directory_with_managed_child_can_change_to_file(self) -> None:
        directory = self.root / ".bugate/node"
        directory.mkdir()
        child_data = b"old child"
        (directory / "child").write_bytes(child_data)
        replacement = b"new leaf"
        operations = [
            self.op("parent", ".bugate/node", DIRECTORY, file_image(replacement)),
            self.op("child", ".bugate/node/child", file_image(child_data), ABSENT),
        ]
        self.manager.apply(operations, payload_bytes={"parent": replacement})
        self.assertTrue((self.root / ".bugate/node").is_file())
        self.assertEqual((self.root / ".bugate/node").read_bytes(), replacement)

    def test_file_can_change_to_directory_before_new_child_is_added(self) -> None:
        old = b"old leaf"
        parent = self.root / ".bugate/node"
        parent.write_bytes(old)
        child = b"new child"
        operations = [
            self.op("parent", ".bugate/node", file_image(old), DIRECTORY),
            self.op("child", ".bugate/node/child", ABSENT, file_image(child)),
        ]
        self.manager.apply(operations, payload_bytes={"child": child})
        self.assertTrue(parent.is_dir())
        self.assertEqual((parent / "child").read_bytes(), child)

    def test_unknown_child_blocks_directory_type_change_and_is_preserved(self) -> None:
        directory = self.root / ".bugate/node"
        directory.mkdir()
        unknown = directory / "operator-owned"
        unknown.write_bytes(b"preserve")
        replacement = b"must-not-win"
        operation = self.op("parent", ".bugate/node", DIRECTORY, file_image(replacement))
        with self.assertRaises(OSError):
            self.manager.apply([operation], payload_bytes={"parent": replacement})
        self.assertTrue(directory.is_dir())
        self.assertEqual(unknown.read_bytes(), b"preserve")
        self.assertIsNone(self.manager.recovery_required())

    def test_crash_leaves_durable_journal_and_next_recovery_restores(self) -> None:
        old, new = b"before-crash", b"after-crash"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={'2' * 32!r})
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 97, completed.stderr)
        status = self.manager.recovery_required()
        self.assertEqual(status["transaction_id"], "2" * 32)
        self.assertEqual(target.read_bytes(), new)
        self.manager.recover()
        self.assertEqual(target.read_bytes(), old)
        self.assertIsNone(self.manager.recovery_required())

    def test_public_recovery_facade_reports_clean_and_recovers_interruption(self) -> None:
        clean = {
            "recovery_required": False,
            "details": None,
            "decision": "GO",
        }
        self.assertEqual(transaction.recovery_status(self.root), clean)

        old, new = b"public-old", b"public-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        identity = "b" * 32
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={identity!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual(target.read_bytes(), new)
        pending = transaction.recovery_status(self.root)
        self.assertTrue(pending["recovery_required"])
        self.assertEqual(pending["decision"], "NO-GO")
        self.assertEqual(pending["details"]["transaction_id"], identity)

        recovered = transaction.recover_pending(self.root)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["transaction_id"], identity)
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(transaction.recovery_status(self.root), clean)

    def test_crash_after_transaction_publish_before_current_is_never_idle(self) -> None:
        old = b"orphan-baseline"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        identity = "0" * 32
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new=b'orphan-postimage'
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_prepare_transaction_publish'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={identity!r})
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 97, completed.stderr)
        state = self.root / ".bugate-update"
        tx = state / "transactions" / identity
        self.assertFalse((state / "current.json").exists())
        self.assertEqual(
            json.loads((tx / "journal.json").read_bytes())["status"], "prepared"
        )

        before_probe = _tree_bytes(self.root)
        pending = self.manager.recovery_required()
        self.assertEqual(
            pending,
            {
                "location": "root",
                "transaction_id": identity,
                "status": "prepared",
                "orphaned_current": True,
            },
        )
        self.assertEqual(_tree_bytes(self.root), before_probe)
        with self.assertRaisesRegex(
            transaction.JournalError, "same-version no-op refused"
        ):
            self.manager.apply([])
        self.assertEqual(_tree_bytes(self.root), before_probe)

        release_base = Path(self.temporary.name) / "orphan-noop-release"
        release_base.mkdir()
        release, manifest = release_tree(release_base, "0.4.2", b"noop\n")
        prepared = SimpleNamespace(
            root=release,
            manifest=manifest,
            archive_sha256=None,
            source_kind="unpacked",
            root_identity=(os.lstat(release).st_dev, os.lstat(release).st_ino),
        )
        plan = {
            "decision": "GO",
            "no_op": True,
            "to_version": "0.4.2",
            "release_digest": manifest["self_digest"],
            "manifest_sha256": contract.sha256_bytes(
                contract.canonical_json_bytes(manifest)
            ),
            "archive_sha256": None,
            "source_kind": "unpacked",
            "target_manifest": manifest,
        }
        with mock.patch(
            "bugate_update_engine.validate_plan_base", return_value=None
        ), self.assertRaisesRegex(
            transaction.JournalError, "same-version no-op refused"
        ):
            transaction.apply_update(
                self.root,
                ".bugate",
                prepared,
                plan,
                updater_version="0.4.2",
            )
        self.assertEqual(_tree_bytes(self.root), before_probe)

        followup = self.op(
            "followup", ".bugate/followup", ABSENT, file_image(b"settled")
        )
        report = self.manager.apply(
            [followup], payload_bytes={"followup": b"settled"}
        )
        self.assertEqual(report["status"], "committed")
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual((self.root / ".bugate/followup").read_bytes(), b"settled")
        self.assertEqual(
            json.loads((tx / "journal.json").read_bytes())["status"], "recovered"
        )
        self.assertTrue((tx / "failure-report.json").is_file())
        self.assertIsNone(self.manager.recovery_required())

    def test_file_directory_type_change_crash_gaps_restore_exact_preimages(self) -> None:
        scenarios = ("file-to-directory", "directory-to-file")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                case_root = Path(self.temporary.name) / scenario
                case_root.mkdir()
                (case_root / ".bugate").mkdir()
                node = case_root / ".bugate/node"
                if scenario == "file-to-directory":
                    old = b"old-parent-file"
                    child = b"new-child"
                    node.write_bytes(old)
                    operations_literal = f"""[
 Operation('parent','.bugate/node',{{'type':'file','sha256':hashlib.sha256({old!r}).hexdigest(),'mode':'0644'}},{{'type':'directory','mode':'0755'}}),
 Operation('child','.bugate/node/child',{{'type':'absent'}},{{'type':'file','sha256':hashlib.sha256({child!r}).hexdigest(),'mode':'0644'}}),
]"""
                    payload_literal = f"{{'child':{child!r}}}"
                else:
                    old = b"old-child"
                    replacement = b"new-parent-file"
                    node.mkdir()
                    (node / "child").write_bytes(old)
                    operations_literal = f"""[
 Operation('parent','.bugate/node',{{'type':'directory','mode':'0755'}},{{'type':'file','sha256':hashlib.sha256({replacement!r}).hexdigest(),'mode':'0644'}}),
 Operation('child','.bugate/node/child',{{'type':'file','sha256':hashlib.sha256({old!r}).hexdigest(),'mode':'0644'}},{{'type':'absent'}}),
]"""
                    payload_literal = f"{{'parent':{replacement!r}}}"
                script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
ops={operations_literal}
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_target_removal:parent'
TransactionManager({str(case_root)!r}).apply(ops,payload_bytes={payload_literal},transaction_id={'e' * 32!r})
"""
                completed = subprocess.run([sys.executable, "-c", script], check=False)
                self.assertEqual(completed.returncode, 97)
                manager = transaction.TransactionManager(case_root)
                manager.recover()
                if scenario == "file-to-directory":
                    self.assertTrue(node.is_file())
                    self.assertEqual(node.read_bytes(), old)
                else:
                    self.assertTrue(node.is_dir())
                    self.assertEqual((node / "child").read_bytes(), old)
                self.assertIsNone(manager.recovery_required())

    def test_directory_install_is_independent_of_restrictive_umask(self) -> None:
        target = self.root / ".bugate/new-directory"
        operation = self.op(
            "directory",
            ".bugate/new-directory",
            ABSENT,
            DIRECTORY,
        )
        previous = os.umask(0o077)
        try:
            report = self.manager.apply([operation])
        finally:
            os.umask(previous)
        self.assertEqual(report["status"], "committed")
        self.assertTrue(target.is_dir())
        self.assertEqual(stat_mode(target), 0o755)

    def test_rollback_failure_restores_directory_mode_under_restrictive_umask(self) -> None:
        target = self.root / ".bugate/node"
        target.write_bytes(b"old-file")
        original = self.manager.apply(
            [
                self.op(
                    "parent",
                    ".bugate/node",
                    file_image(b"old-file"),
                    DIRECTORY,
                )
            ]
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(stat_mode(target), 0o755)

        def fail(name: str) -> None:
            if name == "after_target_removal:rollback:parent":
                raise transaction.InjectedFailure("rollback directory gap")

        interrupted = transaction.TransactionManager(
            self.root, injector=fail
        )
        previous = os.umask(0o077)
        try:
            with self.assertRaises(transaction.InjectedFailure):
                interrupted.rollback(original["transaction_id"])
        finally:
            os.umask(previous)
        self.assertTrue(target.is_dir())
        self.assertEqual(stat_mode(target), 0o755)
        self.assertIsNone(interrupted.recovery_required())

    def test_legacy_rollback_commit_crash_before_archive_is_recoverable(self) -> None:
        old, new = b"legacy-old", b"installed-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        original = self.manager.apply(
            [self.op("runtime", ".bugate/runtime", file_image(old), file_image(new))],
            payload_bytes={"runtime": new},
            transaction_id="f" * 32,
        )
        script = f"""
import os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager
os.environ['BUGATE_UPDATE_CRASHPOINT']='before_legacy_archive'
TransactionManager({str(self.root)!r}).rollback({original['transaction_id']!r},archive_legacy=True)
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual(target.read_bytes(), old)
        pending = self.manager.recovery_required()
        self.assertEqual(pending["status"], "archive_migration_required")
        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "archive_recovered")
        self.assertFalse((self.root / ".bugate-update").exists())
        archived = self.root / ".bugate/plan.lock/bugate-update"
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertIsNone(self.manager.recovery_required())

    def test_crash_after_archive_intent_before_rollback_prepare_cleans_only_intent(self) -> None:
        old, new = b"intent-old", b"intent-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        original = self.manager.apply(
            [self.op("runtime", ".bugate/runtime", file_image(old), file_image(new))],
            payload_bytes={"runtime": new},
            transaction_id="2" * 32,
        )
        script = f"""
import os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_archive_intent'
TransactionManager({str(self.root)!r}).rollback({original['transaction_id']!r},archive_legacy=True)
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual(target.read_bytes(), new)
        pending = self.manager.recovery_required()
        self.assertEqual(pending["status"], "archive_intent_cleanup_required")
        cleaned = self.manager.recover()
        self.assertEqual(cleaned["status"], "archive_intent_cleaned")
        self.assertEqual(target.read_bytes(), new)
        self.assertTrue((self.root / ".bugate-update").is_dir())
        self.assertFalse((self.root / ".bugate/plan.lock").exists())
        self.assertIsNone(self.manager.recovery_required())

    def test_interrupted_legacy_rollback_restores_installed_state_and_clears_intent(self) -> None:
        old, new = b"legacy-base", b"installed-base"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        original = self.manager.apply(
            [self.op("runtime", ".bugate/runtime", file_image(old), file_image(new))],
            payload_bytes={"runtime": new},
            transaction_id="1" * 32,
        )
        script = f"""
import os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:rollback:runtime'
TransactionManager({str(self.root)!r}).rollback({original['transaction_id']!r},archive_legacy=True)
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual(target.read_bytes(), old)
        pending = self.manager.recovery_required()
        self.assertTrue(pending["archive_after_commit"])
        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(target.read_bytes(), new)
        self.assertFalse((self.root / ".bugate-update/archive-transition.json").exists())
        self.assertIsNone(self.manager.recovery_required())

    def test_crash_after_pending_report_rolls_back_without_fake_committed_report(self) -> None:
        old, new = b"pending-old", b"pending-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_report_pending'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={'8' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        tx = self.root / ".bugate-update/transactions" / ("8" * 32)
        self.assertTrue((tx / "report.pending.json").is_file())
        self.assertFalse((tx / "report.json").exists())
        self.manager.recover()
        self.assertEqual(target.read_bytes(), old)
        self.assertFalse((tx / "report.pending.json").exists())
        self.assertFalse((tx / "report.json").exists())

    def test_crash_after_committed_journal_finalizes_report_without_rollback(self) -> None:
        old, new = b"commit-old", b"commit-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_journal_commit'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={'9' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        tx = self.root / ".bugate-update/transactions" / ("9" * 32)
        self.assertEqual(target.read_bytes(), new)
        self.assertFalse((tx / "report.json").exists())
        self.manager.recover()
        self.assertEqual(target.read_bytes(), new)
        self.assertTrue((tx / "report.json").is_file())
        self.assertIsNone(self.manager.recovery_required())

    def test_pending_schema_drift_after_journal_commit_forces_terminal_recovery(self) -> None:
        old, new = b"bound-pending-old", b"bound-pending-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        external = self.root / ".operator-owned"
        external.mkdir()
        (external / "asset").write_bytes(b"preserve")
        external_before = _tree_bytes(external)
        identity = "f" * 32
        tx = self.root / ".bugate-update/transactions" / identity
        tampered = False

        def tamper(name: str) -> None:
            nonlocal tampered
            if name == "after_journal_commit":
                tampered = True
                _rewrite_sealed(
                    tx / "report.pending.json", schema_version=999
                )

        manager = transaction.TransactionManager(self.root, injector=tamper)
        with self.assertRaisesRegex(
            transaction.ReportIntegrityError,
            "pending transaction report changed",
        ):
            manager.apply(
                [
                    self.op(
                        "runtime",
                        ".bugate/runtime",
                        file_image(old),
                        file_image(new),
                    )
                ],
                payload_bytes={"runtime": new},
                transaction_id=identity,
            )
        self.assertTrue(tampered)
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(_tree_bytes(external), external_before)
        self.assertFalse((tx / "report.pending.json").exists())
        self.assertFalse((tx / "report.json").exists())
        self.assertEqual(
            json.loads((tx / "journal.json").read_bytes())["status"],
            "recovered",
        )
        failure = json.loads((tx / "failure-report.json").read_bytes())
        self.assertEqual(failure["schema_version"], transaction.STATE_SCHEMA)
        self.assertEqual(failure["status"], "recovered")
        self.assertIsNone(manager.recovery_required())

    def test_valid_same_name_pending_inode_replacement_forces_terminal_recovery(self) -> None:
        old, new = b"inode-pending-old", b"inode-pending-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        identity = "0" * 32
        tx = self.root / ".bugate-update/transactions" / identity
        replaced_inodes: tuple[int, int] | None = None

        def replace_with_valid_sealed_copy(name: str) -> None:
            nonlocal replaced_inodes
            if name != "after_journal_commit":
                return
            pending = tx / "report.pending.json"
            payload = pending.read_bytes()
            original_inode = os.lstat(pending).st_ino
            pending.unlink()
            pending.write_bytes(payload)
            os.chmod(pending, 0o600)
            replacement_inode = os.lstat(pending).st_ino
            replaced_inodes = (original_inode, replacement_inode)
            document = json.loads(payload)
            contract.validate_self_digest(document)
            self.assertEqual(
                payload, contract.canonical_json_bytes(document)
            )

        manager = transaction.TransactionManager(
            self.root, injector=replace_with_valid_sealed_copy
        )
        with self.assertRaisesRegex(
            transaction.ReportIntegrityError,
            "pending transaction report changed",
        ):
            manager.apply(
                [
                    self.op(
                        "runtime",
                        ".bugate/runtime",
                        file_image(old),
                        file_image(new),
                    )
                ],
                payload_bytes={"runtime": new},
                transaction_id=identity,
            )
        self.assertIsNotNone(replaced_inodes)
        self.assertNotEqual(*replaced_inodes)
        self.assertEqual(target.read_bytes(), old)
        self.assertFalse((tx / "report.pending.json").exists())
        self.assertFalse((tx / "report.json").exists())
        self.assertEqual(
            json.loads((tx / "journal.json").read_bytes())["status"],
            "recovered",
        )
        self.assertEqual(
            json.loads((tx / "failure-report.json").read_bytes())["status"],
            "recovered",
        )
        self.assertIsNone(manager.recovery_required())

    def test_resealed_success_pending_and_failure_wrong_schema_fail_closed(self) -> None:
        for report_kind in ("success", "pending", "failure"):
            with self.subTest(report_kind=report_kind):
                case_root = Path(self.temporary.name) / f"wrong-schema-{report_kind}"
                (case_root / ".bugate").mkdir(parents=True)
                target = case_root / ".bugate/runtime"
                target.write_bytes(b"old")
                identity = {
                    "success": "1" * 32,
                    "pending": "2" * 32,
                    "failure": "3" * 32,
                }[report_kind]
                manager = transaction.TransactionManager(case_root)
                operation = self.op(
                    "runtime",
                    ".bugate/runtime",
                    file_image(b"old"),
                    file_image(b"new"),
                )
                if report_kind == "success":
                    manager.apply(
                        [operation],
                        payload_bytes={"runtime": b"new"},
                        transaction_id=identity,
                    )
                    report_path = (
                        case_root
                        / ".bugate-update/transactions"
                        / identity
                        / "report.json"
                    )
                else:
                    point = (
                        "after_report_pending"
                        if report_kind == "pending"
                        else "after_mutation:runtime"
                    )
                    script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old=b'old'; new=b'new'
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']={point!r}
TransactionManager({str(case_root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={identity!r})
"""
                    completed = subprocess.run(
                        [sys.executable, "-c", script], check=False
                    )
                    self.assertEqual(completed.returncode, 97)
                    tx = case_root / ".bugate-update/transactions" / identity
                    if report_kind == "pending":
                        report_path = tx / "report.pending.json"
                    else:
                        manager.recover()
                        report_path = tx / "failure-report.json"
                _rewrite_sealed(report_path, schema_version=999)
                before = _tree_bytes(case_root)
                pending = manager.recovery_required()
                self.assertEqual(pending["status"], "invalid")
                self.assertIn("report", pending["error"])
                with self.assertRaisesRegex(
                    transaction.JournalError, "same-version no-op refused"
                ):
                    manager.apply([])
                self.assertEqual(_tree_bytes(case_root), before)

    def test_resealed_current_pointer_wrong_schema_is_zero_write_no_go(self) -> None:
        old, new = b"pointer-old", b"pointer-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        identity = "6" * 32
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={identity!r})
"""
        self.assertEqual(subprocess.run([sys.executable, "-c", script]).returncode, 97)
        current = self.root / ".bugate-update/current.json"
        _rewrite_sealed(current, schema_version=999)
        before = _tree_bytes(self.root)
        status = self.manager.recovery_required()
        self.assertEqual(status["status"], "invalid")
        self.assertIn("current transaction pointer", status["error"])
        with self.assertRaisesRegex(
            transaction.JournalError, "same-version no-op refused"
        ):
            self.manager.apply([])
        self.assertEqual(_tree_bytes(self.root), before)
        self.assertEqual(target.read_bytes(), new)

    def test_idle_terminal_history_missing_required_report_is_invalid(self) -> None:
        for terminal in ("committed", "recovered"):
            with self.subTest(terminal=terminal):
                case_root = Path(self.temporary.name) / f"missing-{terminal}-report"
                (case_root / ".bugate").mkdir(parents=True)
                target = case_root / ".bugate/runtime"
                target.write_bytes(b"old")
                manager = transaction.TransactionManager(case_root)
                identity = ("7" if terminal == "committed" else "8") * 32
                operation = self.op(
                    "runtime",
                    ".bugate/runtime",
                    file_image(b"old"),
                    file_image(b"new"),
                )
                if terminal == "committed":
                    manager.apply(
                        [operation],
                        payload_bytes={"runtime": b"new"},
                        transaction_id=identity,
                    )
                    report_name = "report.json"
                else:
                    def fail(name: str) -> None:
                        if name == "after_mutation:runtime":
                            raise transaction.InjectedFailure("recover fixture")

                    with self.assertRaises(transaction.InjectedFailure):
                        transaction.TransactionManager(
                            case_root, injector=fail
                        ).apply(
                            [operation],
                            payload_bytes={"runtime": b"new"},
                            transaction_id=identity,
                        )
                    report_name = "failure-report.json"
                report_path = (
                    case_root
                    / ".bugate-update/transactions"
                    / identity
                    / report_name
                )
                report_path.unlink()
                before = _tree_bytes(case_root)
                status = manager.recovery_required()
                self.assertEqual(status["status"], "invalid")
                self.assertIn("report", status["error"])
                public = transaction.recovery_status(case_root)
                self.assertTrue(public["recovery_required"])
                self.assertEqual(public["decision"], "NO-GO")
                self.assertEqual(public["details"]["status"], "invalid")
                self.assertIn("report", public["details"]["error"])
                with self.assertRaisesRegex(
                    transaction.JournalError, "same-version no-op refused"
                ):
                    manager.apply([])
                self.assertEqual(_tree_bytes(case_root), before)

    def test_applying_pending_and_recovering_failure_coexistence_recovers(self) -> None:
        old, new = b"coexist-old", b"coexist-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        identity = "9" * 32
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_report_pending'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={identity!r})
"""
        self.assertEqual(subprocess.run([sys.executable, "-c", script]).returncode, 97)
        tx = self.root / ".bugate-update/transactions" / identity
        (tx / "failure-report.json").write_bytes(
            contract.canonical_json_bytes(
                contract.seal_document(
                    {
                        "schema_version": transaction.STATE_SCHEMA,
                        "transaction_id": identity,
                        "kind": "apply",
                        "status": "recovering",
                        "error_type": "SyntheticKillWindow",
                        "error": "process terminated before recovery",
                    }
                )
            )
        )
        self.assertTrue((tx / "report.pending.json").is_file())
        self.assertEqual(self.manager.recovery_required()["status"], "applying")
        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(target.read_bytes(), old)
        self.assertFalse((tx / "report.pending.json").exists())
        self.assertEqual(
            json.loads((tx / "failure-report.json").read_bytes())["status"],
            "recovered",
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_recovery_refuses_third_party_drift(self) -> None:
        old, new = b"old", b"new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={'3' * 32!r})
"""
        subprocess.run([sys.executable, "-c", script], check=False)
        target.write_bytes(b"third-party")
        with self.assertRaises(transaction.ThirdPartyDriftError):
            self.manager.recover()
        self.assertEqual(target.read_bytes(), b"third-party")
        self.assertEqual(self.manager.recovery_required()["status"], "recovery_failed")

    def test_recovery_refuses_third_party_file_deletion(self) -> None:
        old, new = b"delete-old", b"delete-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={'a' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        target.unlink()
        with self.assertRaises(transaction.ThirdPartyDriftError):
            self.manager.recover()
        self.assertFalse(target.exists())
        self.assertEqual(self.manager.recovery_required()["status"], "recovery_failed")

    def test_path_escape_and_symlink_parent_are_rejected(self) -> None:
        with self.assertRaises(Exception):
            transaction.Operation.from_mapping(
                {"id": "escape", "target_path": "../outside", "pre": ABSENT, "post": DIRECTORY}
            )
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.root / "linked").symlink_to(outside)
        op = self.op("escape", "linked/file", ABSENT, file_image(b"no"))
        with self.assertRaises(transaction.UnsafePathError):
            self.manager.apply([op], payload_bytes={"escape": b"no"})
        self.assertFalse((outside / "file").exists())

    def test_parent_swap_after_observation_cannot_write_outside_workspace(self) -> None:
        managed = self.root / "managed"
        managed.mkdir()
        target = managed / "item"
        old, new = b"workspace-old", b"workspace-new"
        target.write_bytes(old)
        outside = Path(self.temporary.name) / "outside-managed"
        outside.mkdir()
        outside_target = outside / "item"
        outside_target.write_bytes(b"outside-old")
        displaced = Path(self.temporary.name) / "displaced-managed"
        operation = self.op(
            "item", "managed/item", file_image(old), file_image(new)
        )
        original_observe = transaction._observe_leaf_at
        observations = 0

        def swap_parent_after_install_observation(
            parent_fd: int, leaf: str, relative: str
        ) -> dict:
            nonlocal observations
            result = original_observe(parent_fd, leaf, relative)
            if relative == "managed/item":
                observations += 1
                if observations == 3:
                    os.rename(managed, displaced)
                    managed.symlink_to(outside, target_is_directory=True)
            return result

        with mock.patch.object(
            transaction,
            "_observe_leaf_at",
            side_effect=swap_parent_after_install_observation,
        ), self.assertRaises(transaction.UnsafePathError):
            self.manager.apply([operation], payload_bytes={"item": new})
        self.assertEqual(outside_target.read_bytes(), b"outside-old")
        self.assertEqual((displaced / "item").read_bytes(), old)

        managed.unlink()
        os.rename(displaced, managed)
        self.manager.recover()
        self.assertEqual(target.read_bytes(), old)
        self.assertIsNone(self.manager.recovery_required())

    def test_physical_parent_exchange_after_observation_is_rejected(self) -> None:
        managed = self.root / "managed"
        managed.mkdir()
        old, new = b"owned-old", b"owned-new"
        (managed / "item").write_bytes(old)
        displaced = Path(self.temporary.name) / "displaced-owned-parent"
        swap_source = Path(self.temporary.name) / "operator-parent"
        swap_source.mkdir()
        (swap_source / "item").write_bytes(b"operator-unrelated")
        operation = self.op(
            "item", "managed/item", file_image(old), file_image(new)
        )
        original_observe = transaction._observe_leaf_at
        observations = 0

        def exchange_after_observation(
            parent_fd: int, leaf: str, relative: str
        ) -> dict:
            nonlocal observations
            result = original_observe(parent_fd, leaf, relative)
            if relative == "managed/item":
                observations += 1
                if observations == 3:
                    os.rename(managed, displaced)
                    os.rename(swap_source, managed)
            return result

        with mock.patch.object(
            transaction,
            "_observe_leaf_at",
            side_effect=exchange_after_observation,
        ), self.assertRaises(transaction.ThirdPartyDriftError):
            self.manager.apply([operation], payload_bytes={"item": new})
        self.assertEqual((managed / "item").read_bytes(), b"operator-unrelated")
        self.assertEqual((displaced / "item").read_bytes(), old)

        os.rename(managed, swap_source)
        os.rename(displaced, managed)
        self.manager.recover()
        self.assertEqual((managed / "item").read_bytes(), old)
        self.assertEqual((swap_source / "item").read_bytes(), b"operator-unrelated")
        self.assertIsNone(self.manager.recovery_required())

    def test_exclusive_rename_never_replaces_existing_empty_directory(self) -> None:
        source = Path(self.temporary.name) / "source"
        destination = Path(self.temporary.name) / "destination"
        source.mkdir()
        (source / "sentinel").write_text("ours", encoding="utf-8")
        destination.mkdir()
        with self.assertRaises(FileExistsError):
            transaction._exclusive_rename(source, destination)
        self.assertTrue(source.is_dir())
        self.assertEqual(list(destination.iterdir()), [])

    def test_linux_exclusive_rename_uses_linux_at_fdcwd(self) -> None:
        source = Path(self.temporary.name) / "linux-source"
        destination = Path(self.temporary.name) / "linux-destination"
        source.mkdir()
        calls: list[tuple] = []

        class Function:
            argtypes = None
            restype = None

            def __call__(self, *args):
                calls.append(args)
                return 0

        class LibC:
            renameat2 = Function()

        with mock.patch.object(transaction.platform, "system", return_value="Linux"), mock.patch.object(
            transaction.ctypes, "CDLL", return_value=LibC()
        ):
            transaction._exclusive_rename(source, destination)
        self.assertEqual(calls[0][0], -100)
        self.assertEqual(calls[0][2], -100)
        self.assertEqual(calls[0][4], 1)

    def test_exclusive_rename_fsyncs_source_and_destination_parents(self) -> None:
        source_parent = Path(self.temporary.name) / "source-parent"
        destination_parent = Path(self.temporary.name) / "destination-parent"
        source_parent.mkdir()
        destination_parent.mkdir()
        source = source_parent / "state"
        destination = destination_parent / "state"
        source.mkdir()

        class Function:
            argtypes = None
            restype = None

            def __call__(self, *_args):
                return 0

        class LibC:
            renameat2 = Function()

        with mock.patch.object(transaction.platform, "system", return_value="Linux"), mock.patch.object(
            transaction.ctypes, "CDLL", return_value=LibC()
        ), mock.patch.object(transaction, "_fsync_directory") as synced:
            transaction._exclusive_rename(source, destination)
        self.assertEqual(
            synced.call_args_list,
            [mock.call(source_parent), mock.call(destination_parent)],
        )

    def test_transaction_directory_symlink_is_fail_closed_without_external_write(self) -> None:
        old, new = b"tx-old", b"tx-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old!r}; new={new!r}
op=Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:runtime'
TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'runtime':new}},transaction_id={'d' * 32!r})
"""
        self.assertEqual(subprocess.run([sys.executable, "-c", script]).returncode, 97)
        tx = self.root / ".bugate-update/transactions" / ("d" * 32)
        outside = Path(self.temporary.name) / "outside-transaction"
        os.rename(tx, outside)
        tx.symlink_to(outside, target_is_directory=True)
        before = _tree_bytes(outside)
        status = self.manager.recovery_required()
        self.assertEqual(status["status"], "invalid")
        with self.assertRaises(transaction.JournalError):
            self.manager.recover()
        self.assertEqual(_tree_bytes(outside), before)

    def test_root_state_exchange_before_current_publish_never_writes_moved_state(self) -> None:
        target = self.root / ".bugate/runtime.py"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"old")
        operation = self.op(
            "runtime", ".bugate/runtime.py", file_image(b"old"), file_image(b"new")
        )
        moved = Path(self.temporary.name) / "moved-updater-state"
        original = self.manager._set_current
        moved_before: dict[str, tuple] | None = None

        def exchange(state: Path, transaction_id: str, **kwargs: Any) -> None:
            nonlocal moved_before
            os.rename(state, moved)
            moved_before = _tree_bytes(moved)
            shutil.copytree(moved, state, symlinks=True)
            original(state, transaction_id, **kwargs)

        with mock.patch.object(
            self.manager, "_set_current", side_effect=exchange
        ), self.assertRaises(transaction.JournalError):
            self.manager.apply([operation], payload_bytes={"runtime": b"new"})
        self.assertEqual(target.read_bytes(), b"old")
        self.assertIsNotNone(moved_before)
        self.assertEqual(_tree_bytes(moved), moved_before)

    def test_transaction_directory_physical_exchange_is_rejected(self) -> None:
        target = self.root / ".bugate/runtime.py"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"old")
        operation = self.op(
            "runtime", ".bugate/runtime.py", file_image(b"old"), file_image(b"new")
        )
        identity = "6" * 32
        self.manager.apply(
            [operation],
            payload_bytes={"runtime": b"new"},
            transaction_id=identity,
        )
        tx = self.root / ".bugate-update/transactions" / identity
        moved = Path(self.temporary.name) / "moved-transaction"
        os.rename(tx, moved)
        moved_before = _tree_bytes(moved)
        shutil.copytree(moved, tx, symlinks=True)
        with self.assertRaisesRegex(
            transaction.JournalError, "transaction directory identity changed"
        ):
            self.manager.rollback(identity)
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(_tree_bytes(moved), moved_before)

    def test_transaction_store_physical_exchange_is_rejected(self) -> None:
        target = self.root / ".bugate/runtime.py"
        target.write_bytes(b"old")
        operation = self.op(
            "runtime", ".bugate/runtime.py", file_image(b"old"), file_image(b"new")
        )
        identity = "7" * 32
        self.manager.apply(
            [operation],
            payload_bytes={"runtime": b"new"},
            transaction_id=identity,
        )
        store = self.root / ".bugate-update/transactions"
        moved = Path(self.temporary.name) / "moved-transaction-store"
        os.rename(store, moved)
        moved_before = _tree_bytes(moved)
        shutil.copytree(moved, store, symlinks=True)
        with self.assertRaisesRegex(
            transaction.JournalError, "sentinel belongs to another directory"
        ):
            self.manager.rollback(identity)
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(_tree_bytes(moved), moved_before)

    def test_earlier_same_name_transaction_exchange_during_full_scan_is_rejected(self) -> None:
        first_id, second_id = "4" * 32, "5" * 32
        first_target = self.root / ".bugate/first"
        second_target = self.root / ".bugate/second"
        self.manager.apply(
            [self.op("first", ".bugate/first", ABSENT, file_image(b"first"))],
            payload_bytes={"first": b"first"},
            transaction_id=first_id,
        )
        self.manager.apply(
            [self.op("second", ".bugate/second", ABSENT, file_image(b"second"))],
            payload_bytes={"second": b"second"},
            transaction_id=second_id,
        )
        store = self.root / ".bugate-update/transactions"
        first = store / first_id
        moved = Path(self.temporary.name) / "moved-first-transaction"
        moved_before = _tree_bytes(first, include_root_timestamps=False)
        original = self.manager._validate_journal
        exchanged = False

        def exchange_earlier(tx: Path, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
            nonlocal exchanged
            if kwargs.get("expected_transaction_id") == second_id and not exchanged:
                exchanged = True
                os.rename(first, moved)
                shutil.copytree(moved, first, symlinks=True)
            return original(tx, **kwargs)

        with mock.patch.object(
            self.manager, "_validate_journal", side_effect=exchange_earlier
        ), self.assertRaisesRegex(transaction.JournalError, "replaced during use"):
            self.manager._validate_state_dir(self.manager.state)
        self.assertTrue(exchanged)
        self.assertEqual(first_target.read_bytes(), b"first")
        self.assertEqual(second_target.read_bytes(), b"second")
        self.assertEqual(
            _tree_bytes(moved, include_root_timestamps=False), moved_before
        )

    def test_same_name_transaction_store_exchange_at_final_snapshot_is_rejected(self) -> None:
        identity = "a" * 32
        self.manager.apply(
            [self.op("runtime", ".bugate/runtime", ABSENT, file_image(b"new"))],
            payload_bytes={"runtime": b"new"},
            transaction_id=identity,
        )
        store = self.root / ".bugate-update/transactions"
        moved = Path(self.temporary.name) / "moved-store-during-validation"
        moved_before = _tree_bytes(store, include_root_timestamps=False)
        original = self.manager._validate_transaction_reports
        exchanged = False

        def exchange_store(
            transaction_fd: int,
            journal: Mapping[str, Any],
            *,
            active: bool,
        ) -> None:
            nonlocal exchanged
            original(transaction_fd, journal, active=active)
            if not exchanged:
                exchanged = True
                os.rename(store, moved)
                shutil.copytree(moved, store, symlinks=True)

        with mock.patch.object(
            self.manager,
            "_validate_transaction_reports",
            side_effect=exchange_store,
        ), self.assertRaisesRegex(transaction.JournalError, "transactions"):
            self.manager._validate_state_dir(self.manager.state)
        self.assertTrue(exchanged)
        self.assertEqual(
            _tree_bytes(moved, include_root_timestamps=False), moved_before
        )

    def test_same_name_state_exchange_at_final_snapshot_is_rejected(self) -> None:
        identity = "b" * 32
        self.manager.apply(
            [self.op("runtime", ".bugate/runtime", ABSENT, file_image(b"new"))],
            payload_bytes={"runtime": b"new"},
            transaction_id=identity,
        )
        state = self.root / ".bugate-update"
        moved = Path(self.temporary.name) / "moved-state-during-validation"
        moved_before = _tree_bytes(state, include_root_timestamps=False)
        original = self.manager._validate_transaction_reports
        exchanged = False

        def exchange_state(
            transaction_fd: int,
            journal: Mapping[str, Any],
            *,
            active: bool,
        ) -> None:
            nonlocal exchanged
            original(transaction_fd, journal, active=active)
            if not exchanged:
                exchanged = True
                os.rename(state, moved)
                shutil.copytree(moved, state, symlinks=True)

        with mock.patch.object(
            self.manager,
            "_validate_transaction_reports",
            side_effect=exchange_state,
        ), self.assertRaisesRegex(
            transaction.JournalError, "state directory changed"
        ):
            self.manager._validate_state_dir(self.manager.state)
        self.assertTrue(exchanged)
        self.assertEqual(
            _tree_bytes(moved, include_root_timestamps=False), moved_before
        )

    def test_journal_write_hierarchy_exchanges_restore_through_pinned_backup(self) -> None:
        for layer in ("transaction", "transactions", "state", "workspace"):
            with self.subTest(layer=layer):
                case_root = Path(self.temporary.name) / f"journal-race-{layer}"
                (case_root / ".bugate").mkdir(parents=True)
                target = case_root / ".bugate/runtime"
                target.write_bytes(b"old")
                external = case_root / ".operator-owned"
                external.mkdir()
                (external / "asset").write_bytes(b"do-not-touch")
                external_before = _tree_bytes(external)
                identity = {
                    "transaction": "c" * 32,
                    "transactions": "d" * 32,
                    "state": "e" * 32,
                    "workspace": "a" * 32,
                }[layer]
                manager = transaction.TransactionManager(case_root)
                operation = self.op(
                    "runtime",
                    ".bugate/runtime",
                    file_image(b"old"),
                    file_image(b"new"),
                )
                moved = Path(self.temporary.name) / f"moved-journal-{layer}"
                canonical_after_exchange: dict[str, tuple[Any, ...]] | None = None
                exchanged = False
                original_atomic = transaction._atomic_json_at

                def exchange_during_committed_journal(
                    directory_fd: int,
                    name: str,
                    document: Mapping[str, Any],
                ) -> None:
                    nonlocal canonical_after_exchange, exchanged
                    if (
                        name == "journal.json"
                        and document.get("status") == "committed"
                        and not exchanged
                    ):
                        exchanged = True
                        state = case_root / ".bugate-update"
                        store = state / "transactions"
                        tx = store / identity
                        exchanged_path = {
                            "transaction": tx,
                            "transactions": store,
                            "state": state,
                            "workspace": case_root,
                        }[layer]
                        os.rename(exchanged_path, moved)
                        shutil.copytree(moved, exchanged_path, symlinks=True)
                        canonical_after_exchange = _tree_bytes(exchanged_path)
                    original_atomic(directory_fd, name, document)

                with mock.patch.object(
                    transaction,
                    "_atomic_json_at",
                    side_effect=exchange_during_committed_journal,
                ), self.assertRaisesRegex(
                    transaction.JournalHierarchyBindingError,
                    "hierarchy binding changed",
                ):
                    manager.apply(
                        [operation],
                        payload_bytes={"runtime": b"new"},
                        transaction_id=identity,
                    )

                self.assertTrue(exchanged)
                restored_target = (
                    moved / ".bugate/runtime"
                    if layer == "workspace"
                    else target
                )
                restored_external = (
                    moved / ".operator-owned"
                    if layer == "workspace"
                    else external
                )
                self.assertEqual(restored_target.read_bytes(), b"old")
                self.assertEqual(_tree_bytes(restored_external), external_before)
                self.assertIsNotNone(canonical_after_exchange)
                state = case_root / ".bugate-update"
                canonical_path = {
                    "transaction": state / "transactions" / identity,
                    "transactions": state / "transactions",
                    "state": state,
                    "workspace": case_root,
                }[layer]
                self.assertEqual(
                    _tree_bytes(canonical_path), canonical_after_exchange
                )
                diagnostic_journal = {
                    "transaction": moved / "journal.json",
                    "transactions": moved / identity / "journal.json",
                    "state": moved / "transactions" / identity / "journal.json",
                    "workspace": moved
                    / ".bugate-update"
                    / "transactions"
                    / identity
                    / "journal.json",
                }[layer]
                self.assertEqual(
                    json.loads(diagnostic_journal.read_bytes())["status"],
                    "committed",
                )
                status = manager.recovery_required()
                self.assertEqual(status["status"], "invalid")
                self.assertRegex(
                    status["error"], r"binding|identity|sentinel"
                )

    def test_descriptor_safe_history_limit_fails_closed_without_writes(self) -> None:
        self.assertEqual(transaction.MAX_PINNED_TRANSACTION_HISTORY, 128)
        self.manager.apply(
            [self.op("first", ".bugate/first", ABSENT, file_image(b"first"))],
            payload_bytes={"first": b"first"},
            transaction_id="1" * 32,
        )
        self.manager.apply(
            [self.op("second", ".bugate/second", ABSENT, file_image(b"second"))],
            payload_bytes={"second": b"second"},
            transaction_id="2" * 32,
        )
        before = _tree_bytes(self.root)
        with mock.patch.object(
            transaction, "MAX_PINNED_TRANSACTION_HISTORY", 1
        ):
            with self.assertRaisesRegex(
                transaction.JournalError, "descriptor-safe validation limit"
            ):
                self.manager._validate_state_dir(self.manager.state)
            status = self.manager.recovery_required()
            self.assertEqual(status["status"], "invalid")
            self.assertIn("descriptor-safe validation limit", status["error"])
            with self.assertRaisesRegex(
                transaction.JournalError, "same-version no-op refused"
            ):
                self.manager.apply([])
        self.assertEqual(_tree_bytes(self.root), before)

    def test_history_capacity_rejects_cap_plus_one_before_target_writes(self) -> None:
        with mock.patch.object(
            transaction, "MAX_PINNED_TRANSACTION_HISTORY", 2
        ):
            for index, identity in enumerate(("1" * 32, "2" * 32), start=1):
                payload = f"history-{index}".encode()
                self.manager.apply(
                    [
                        self.op(
                            f"history-{index}",
                            f".bugate/history-{index}",
                            ABSENT,
                            file_image(payload),
                        )
                    ],
                    payload_bytes={f"history-{index}": payload},
                    transaction_id=identity,
                )

            before = _tree_bytes(self.root)
            with self.assertRaisesRegex(
                transaction.JournalError,
                "history has reached the descriptor-safe validation limit",
            ):
                self.manager.apply(
                    [
                        self.op(
                            "history-3",
                            ".bugate/history-3",
                            ABSENT,
                            file_image(b"history-3"),
                        )
                    ],
                    payload_bytes={"history-3": b"history-3"},
                    transaction_id="3" * 32,
                )

            self.assertEqual(_tree_bytes(self.root), before)
            self.assertFalse((self.root / ".bugate/history-3").exists())
            self.assertEqual(
                transaction.recovery_status(self.root),
                {
                    "recovery_required": False,
                    "details": None,
                    "decision": "GO",
                },
            )

    def test_atomic_prepare_rejects_full_history_before_private_staging(self) -> None:
        with mock.patch.object(
            transaction, "MAX_PINNED_TRANSACTION_HISTORY", 2
        ):
            for index, identity in enumerate(("4" * 32, "5" * 32), start=1):
                payload = f"atomic-history-{index}".encode()
                self.manager.apply(
                    [
                        self.op(
                            f"atomic-history-{index}",
                            f".bugate/atomic-history-{index}",
                            ABSENT,
                            file_image(payload),
                        )
                    ],
                    payload_bytes={f"atomic-history-{index}": payload},
                    transaction_id=identity,
                )

            before = _tree_bytes(self.root)
            operation = self.op(
                "atomic-history-3",
                ".bugate/atomic-history-3",
                ABSENT,
                file_image(b"atomic-history-3"),
            )
            with mock.patch.object(
                transaction.tempfile,
                "mkdtemp",
                wraps=transaction.tempfile.mkdtemp,
            ) as make_private_stage:
                with self.manager.workspace_lock() as held_lock:
                    self.assertIsNotNone(held_lock.fd)
                    with self.assertRaisesRegex(
                        transaction.JournalError,
                        "history has reached the descriptor-safe validation limit",
                    ):
                        self.manager._prepare_transaction(
                            self.manager.state,
                            "6" * 32,
                            "apply",
                            [operation],
                            payload_sources=None,
                            payload_bytes={"atomic-history-3": b"atomic-history-3"},
                            worker_files=None,
                            input_files=None,
                            source_transaction=None,
                            root_fd=held_lock.fd,
                            atomic_publish=True,
                        )
                make_private_stage.assert_not_called()

            self.assertEqual(_tree_bytes(self.root), before)
            self.assertFalse((self.root / ".bugate/atomic-history-3").exists())

    def test_archiving_rollback_rejects_full_history_before_intent_write(self) -> None:
        with mock.patch.object(
            transaction, "MAX_PINNED_TRANSACTION_HISTORY", 2
        ):
            runtime = self.root / ".bugate/runtime"
            runtime.write_bytes(b"rollback-old")
            original = self.manager.apply(
                [
                    self.op(
                        "runtime",
                        ".bugate/runtime",
                        file_image(b"rollback-old"),
                        file_image(b"rollback-new"),
                    )
                ],
                payload_bytes={"runtime": b"rollback-new"},
                transaction_id="7" * 32,
            )
            self.manager.apply(
                [
                    self.op(
                        "history-fill",
                        ".bugate/history-fill",
                        ABSENT,
                        file_image(b"history-fill"),
                    )
                ],
                payload_bytes={"history-fill": b"history-fill"},
                transaction_id="8" * 32,
            )

            before = _tree_bytes(self.root)
            with self.assertRaisesRegex(
                transaction.JournalError,
                "history has reached the descriptor-safe validation limit",
            ):
                self.manager.rollback(
                    original["transaction_id"], archive_legacy=True
                )

            self.assertEqual(_tree_bytes(self.root), before)
            self.assertEqual(runtime.read_bytes(), b"rollback-new")
            self.assertFalse(
                (self.manager.state / transaction.ARCHIVE_TRANSITION).exists()
            )
            self.assertEqual(transaction.recovery_status(self.root)["decision"], "GO")

    def test_archived_bootstrap_reuse_rejects_full_history_before_intent_write(
        self,
    ) -> None:
        with mock.patch.object(
            transaction, "MAX_PINNED_TRANSACTION_HISTORY", 2
        ):
            runtime = self.root / ".bugate/runtime"
            runtime.write_bytes(b"archive-old")
            self.manager.apply(
                [
                    self.op(
                        "runtime",
                        ".bugate/runtime",
                        file_image(b"archive-old"),
                        file_image(b"archive-new"),
                    )
                ],
                payload_bytes={"runtime": b"archive-new"},
                transaction_id="9" * 32,
            )
            self.manager.apply(
                [
                    self.op(
                        "archive-fill",
                        ".bugate/archive-fill",
                        ABSENT,
                        file_image(b"archive-fill"),
                    )
                ],
                payload_bytes={"archive-fill": b"archive-fill"},
                transaction_id="a" * 32,
            )
            self.manager.archive_legacy_rollback_state()
            archived_state = self.manager.prelock / transaction.BOOTSTRAP_CHILD
            before = _tree_bytes(self.root)
            gitignore = self.op(
                "gitignore",
                ".gitignore",
                ABSENT,
                file_image(b"/.bugate-update/\n"),
            )

            with mock.patch.object(
                transaction.tempfile,
                "mkdtemp",
                wraps=transaction.tempfile.mkdtemp,
            ) as make_private_stage:
                with self.assertRaisesRegex(
                    transaction.JournalError,
                    "history has reached the descriptor-safe validation limit",
                ):
                    self.manager.apply(
                        [gitignore],
                        payload_bytes={"gitignore": b"/.bugate-update/\n"},
                        bootstrap=True,
                        gitignore_operation_id="gitignore",
                        transaction_id="b" * 32,
                    )
                make_private_stage.assert_not_called()

            self.assertEqual(_tree_bytes(self.root), before)
            self.assertFalse((self.root / ".gitignore").exists())
            self.assertFalse(
                (archived_state / transaction.ARCHIVE_REUSE_TRANSITION).exists()
            )
            self.assertEqual(transaction.recovery_status(self.root)["decision"], "GO")

    def test_interrupted_transaction_at_history_cap_remains_recoverable(self) -> None:
        with mock.patch.object(
            transaction, "MAX_PINNED_TRANSACTION_HISTORY", 2
        ):
            self.manager.apply(
                [
                    self.op(
                        "history-one",
                        ".bugate/history-one",
                        ABSENT,
                        file_image(b"history-one"),
                    )
                ],
                payload_bytes={"history-one": b"history-one"},
                transaction_id="c" * 32,
            )
            target = self.root / ".bugate/cap-recovery"
            target.write_bytes(b"cap-old")
            script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
import bugate_update_transaction as module
module.MAX_PINNED_TRANSACTION_HISTORY = 2
old=b'cap-old'; new=b'cap-new'
op=module.Operation('cap-recovery','.bugate/cap-recovery',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_mutation:cap-recovery'
module.TransactionManager({str(self.root)!r}).apply([op],payload_bytes={{'cap-recovery':new}},transaction_id={'d' * 32!r})
"""
            completed = subprocess.run(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 97, completed.stderr)
            self.assertEqual(target.read_bytes(), b"cap-new")
            pending = self.manager.recovery_required()
            self.assertEqual(pending["transaction_id"], "d" * 32)

            recovered = self.manager.recover()
            self.assertEqual(recovered["status"], "recovered")
            self.assertEqual(target.read_bytes(), b"cap-old")
            self.assertEqual(
                len(list((self.manager.state / "transactions").iterdir())), 2
            )
            self.assertEqual(transaction.recovery_status(self.root)["decision"], "GO")

    def test_stage_directory_exchange_before_install_is_fail_closed(self) -> None:
        target = self.root / ".bugate/runtime.py"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"old")
        operation = self.op(
            "runtime", ".bugate/runtime.py", file_image(b"old"), file_image(b"new")
        )
        moved = Path(self.temporary.name) / "moved-stage"
        original = self.manager._install_one
        moved_before: dict[str, tuple] | None = None
        swapped = False

        def exchange(
            transaction_fd: int,
            directory_bindings: Mapping[str, Any],
            index: int,
            item: transaction.Operation,
            *,
            root_fd: int,
        ) -> None:
            nonlocal moved_before, swapped
            if not swapped:
                swapped = True
                tx = self.root / ".bugate-update/transactions"
                transaction_id = next(tx.iterdir()).name
                stage = tx / transaction_id / "stage"
                os.rename(stage, moved)
                moved_before = _tree_bytes(moved)
                stage.mkdir()
            original(
                transaction_fd,
                directory_bindings,
                index,
                item,
                root_fd=root_fd,
            )

        with mock.patch.object(
            self.manager, "_install_one", side_effect=exchange
        ), self.assertRaises(transaction.JournalError):
            self.manager.apply([operation], payload_bytes={"runtime": b"new"})
        self.assertTrue(swapped)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertIsNotNone(moved_before)
        self.assertEqual(_tree_bytes(moved), moved_before)

    def test_input_directory_exchange_rolls_back_without_external_write(self) -> None:
        target = self.root / ".bugate/runtime.py"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"old")
        operation = self.op(
            "runtime", ".bugate/runtime.py", file_image(b"old"), file_image(b"new")
        )
        moved = Path(self.temporary.name) / "moved-input"
        original = self.manager._load_report_metadata
        moved_before: dict[str, tuple] | None = None

        def exchange(tx: Path, **kwargs: Any) -> dict[str, Any]:
            nonlocal moved_before
            transaction_root = self.root / ".bugate-update/transactions"
            transaction_id = next(transaction_root.iterdir()).name
            input_dir = transaction_root / transaction_id / "input"
            os.rename(input_dir, moved)
            moved_before = _tree_bytes(moved)
            input_dir.mkdir()
            return original(tx, **kwargs)

        with mock.patch.object(
            self.manager, "_load_report_metadata", side_effect=exchange
        ), self.assertRaises(transaction.JournalError):
            self.manager.apply([operation], payload_bytes={"runtime": b"new"})
        self.assertEqual(target.read_bytes(), b"old")
        self.assertIsNotNone(moved_before)
        self.assertEqual(_tree_bytes(moved), moved_before)

    def test_pending_report_symlink_exchange_rolls_back_without_following(self) -> None:
        target = self.root / ".bugate/runtime.py"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"old")
        outside = Path(self.temporary.name) / "operator-report.json"
        outside.write_bytes(b"operator-owned")
        operation = self.op(
            "runtime", ".bugate/runtime.py", file_image(b"old"), file_image(b"new")
        )

        def inject(name: str) -> None:
            if name != "after_report_pending":
                return
            transaction_root = self.root / ".bugate-update/transactions"
            transaction_id = next(transaction_root.iterdir()).name
            pending = transaction_root / transaction_id / "report.pending.json"
            pending.unlink()
            pending.symlink_to(outside)

        manager = transaction.TransactionManager(self.root, injector=inject)
        with self.assertRaises(transaction.JournalError):
            manager.apply([operation], payload_bytes={"runtime": b"new"})
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(outside.read_bytes(), b"operator-owned")

    def test_root_state_symlink_archive_marker_is_never_unlinked(self) -> None:
        outside = Path(self.temporary.name) / "outside-state"
        outside.mkdir()
        marker = outside / "archive-transition.json"
        marker.write_bytes(b"operator-owned\n")
        (self.root / ".bugate-update").symlink_to(outside, target_is_directory=True)
        status = self.manager.recovery_required()
        self.assertEqual(status["status"], "invalid")
        with self.assertRaises(transaction.JournalError):
            self.manager.recover()
        self.assertEqual(marker.read_bytes(), b"operator-owned\n")

    def test_logical_state_digest_includes_directory_symlink_targets(self) -> None:
        first = Path(self.temporary.name) / "digest-first"
        second = Path(self.temporary.name) / "digest-second"
        for root in (first, second):
            (root / "stage/one").mkdir(parents=True)
            (root / "stage/two").mkdir()
        (first / "stage/link").symlink_to("one", target_is_directory=True)
        (second / "stage/link").symlink_to("two", target_is_directory=True)
        self.assertNotEqual(
            transaction._logical_state_digest(first),
            transaction._logical_state_digest(second),
        )

    def test_bootstrap_publishes_state_changes_gitignore_first_and_preserves_report(self) -> None:
        old_block = b"# begin\n/.bugate/plan.lock\n# end\n"
        new_block = b"# begin\n/.bugate/plan.lock\n/.bugate-update/\n# end\n"
        old_runtime, new_runtime = b"legacy", b"current"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        operations = [
            self.op("gitignore", ".gitignore", file_image(old_block), file_image(new_block)),
            self.op("runtime", ".bugate/runtime", file_image(old_runtime), file_image(new_runtime)),
        ]
        report = self.manager.apply(
            operations,
            payload_bytes={"gitignore": new_block, "runtime": new_runtime},
            bootstrap=True,
            gitignore_operation_id="gitignore",
            transaction_id="4" * 32,
        )
        self.assertEqual(report["status"], "committed")
        self.assertEqual((self.root / ".gitignore").read_bytes(), new_block)
        self.assertEqual((self.root / ".bugate/runtime").read_bytes(), new_runtime)
        self.assertFalse((self.root / ".bugate/plan.lock").exists())
        state = self.root / ".bugate-update"
        self.assertTrue((state / "sentinel.json").is_file())
        self.assertTrue((state / "transactions" / ("4" * 32) / "report.json").is_file())

    def test_bootstrap_failure_after_gitignore_restores_and_is_reusable(self) -> None:
        old_block = b"# begin\n/.bugate/plan.lock\n# end\n"
        new_block = b"# begin\n/.bugate/plan.lock\n/.bugate-update/\n# end\n"
        (self.root / ".gitignore").write_bytes(old_block)

        def fail(name: str) -> None:
            if name == "after_gitignore":
                raise transaction.InjectedFailure("bootstrap failure")

        manager = transaction.TransactionManager(self.root, injector=fail)
        operation = self.op("gitignore", ".gitignore", file_image(old_block), file_image(new_block))
        with self.assertRaises(transaction.InjectedFailure):
            manager.apply(
                [operation],
                payload_bytes={"gitignore": new_block},
                bootstrap=True,
                gitignore_operation_id="gitignore",
            )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertEqual(
            manager.recovery_required()["status"], "recovered_pending_cleanup"
        )
        self.assertTrue((self.root / ".bugate/plan.lock/bugate-update/sentinel.json").is_file())
        self.assertEqual(
            len(list((self.root / ".bugate/plan.lock/bugate-update/transactions").glob("*/failure-report.json"))),
            1,
        )

        second = transaction.TransactionManager(self.root)
        result = second.apply(
            [operation],
            payload_bytes={"gitignore": new_block},
            bootstrap=True,
            gitignore_operation_id="gitignore",
        )
        self.assertEqual(result["status"], "committed")
        self.assertFalse((self.root / ".bugate/plan.lock").exists())

    def test_bootstrap_failure_after_root_migration_returns_to_ignored_archive(self) -> None:
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        old_runtime, new_runtime = b"legacy", b"current"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        operations = [
            self.op(
                "gitignore",
                ".gitignore",
                file_image(old_block),
                file_image(new_block),
            ),
            self.op(
                "runtime",
                ".bugate/runtime",
                file_image(old_runtime),
                file_image(new_runtime),
            ),
        ]

        def fail_verify() -> None:
            raise transaction.InjectedFailure("late bootstrap verify")

        with self.assertRaises(transaction.InjectedFailure):
            self.manager.apply(
                operations,
                payload_bytes={
                    "gitignore": new_block,
                    "runtime": new_runtime,
                },
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="3" * 32,
                verify=fail_verify,
            )
        archived = self.root / ".bugate/plan.lock/bugate-update"
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertEqual(
            (self.root / ".bugate/runtime").read_bytes(), old_runtime
        )
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("3" * 32) / "failure-report.json").is_file()
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_recovered_prelock_cleanup_preserves_foreign_sibling_and_refuses(self) -> None:
        old_block = b"# begin\n/.bugate/plan.lock\n# end\n"
        new_block = b"# begin\n/.bugate/plan.lock\n/.bugate-update/\n# end\n"
        (self.root / ".gitignore").write_bytes(old_block)

        def fail(name: str) -> None:
            if name == "after_gitignore":
                raise transaction.InjectedFailure("bootstrap failure")

        manager = transaction.TransactionManager(self.root, injector=fail)
        operation = self.op(
            "gitignore", ".gitignore", file_image(old_block), file_image(new_block)
        )
        with self.assertRaises(transaction.InjectedFailure):
            manager.apply(
                [operation],
                payload_bytes={"gitignore": new_block},
                bootstrap=True,
                gitignore_operation_id="gitignore",
            )
        sibling = self.root / ".bugate/plan.lock/operator-owned.txt"
        sibling.write_bytes(b"preserve")
        with self.assertRaisesRegex(transaction.JournalError, "non-updater entries"):
            manager.recover()
        self.assertEqual(sibling.read_bytes(), b"preserve")
        self.assertTrue(
            (self.root / ".bugate/plan.lock/bugate-update/sentinel.json").is_file()
        )

    def test_hard_crash_after_root_state_publish_recovers_to_ignored_archive(self) -> None:
        old_block = b"# begin\n/.bugate/plan.lock\n# end\n"
        new_block = b"# begin\n/.bugate/plan.lock\n/.bugate-update/\n# end\n"
        old_runtime, new_runtime = b"legacy-root", b"current-root"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old_block={old_block!r}; new_block={new_block!r}
old_runtime={old_runtime!r}; new_runtime={new_runtime!r}
ops=[
 Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old_block).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_block).hexdigest(),'mode':'0644'}}),
 Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old_runtime).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_runtime).hexdigest(),'mode':'0644'}}),
]
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_root_state_publish'
TransactionManager({str(self.root)!r}).apply(ops,payload_bytes={{'gitignore':new_block,'runtime':new_runtime}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'b' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual((self.root / ".gitignore").read_bytes(), new_block)
        self.assertTrue((self.root / ".bugate-update/bootstrap-transition.json").is_file())
        self.assertTrue((self.root / ".bugate/plan.lock").is_dir())

        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertEqual((self.root / ".bugate/runtime").read_bytes(), old_runtime)
        self.assertFalse((self.root / ".bugate-update").exists())
        archived = self.root / ".bugate/plan.lock/bugate-update"
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue((archived / "transactions" / ("b" * 32) / "failure-report.json").is_file())
        self.assertIsNone(self.manager.recovery_required())

        operations = [
            self.op("gitignore", ".gitignore", file_image(old_block), file_image(new_block)),
            self.op("runtime", ".bugate/runtime", file_image(old_runtime), file_image(new_runtime)),
        ]
        report = self.manager.apply(
            operations,
            payload_bytes={"gitignore": new_block, "runtime": new_runtime},
            bootstrap=True,
            gitignore_operation_id="gitignore",
            transaction_id="c" * 32,
        )
        self.assertEqual(report["status"], "committed")
        self.assertEqual((self.root / ".bugate/runtime").read_bytes(), new_runtime)
        self.assertFalse((self.root / ".bugate/plan.lock").exists())
        self.assertFalse((self.root / ".bugate-update/bootstrap-transition.json").exists())

    def test_hard_crash_after_plan_lock_retire_recovers_to_ignored_archive(self) -> None:
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        old_runtime, new_runtime = b"legacy-retired", b"current-retired"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old_block={old_block!r}; new_block={new_block!r}
old_runtime={old_runtime!r}; new_runtime={new_runtime!r}
ops=[
 Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old_block).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_block).hexdigest(),'mode':'0644'}}),
 Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old_runtime).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_runtime).hexdigest(),'mode':'0644'}}),
]
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_root_state_migration'
TransactionManager({str(self.root)!r}).apply(ops,payload_bytes={{'gitignore':new_block,'runtime':new_runtime}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'f' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertTrue((self.root / ".bugate-update").is_dir())
        self.assertFalse((self.root / ".bugate/plan.lock").exists())
        transition = json.loads(
            (self.root / ".bugate-update/bootstrap-transition.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(transition["phase"], "plan-lock-retired")
        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertEqual(
            (self.root / ".bugate/runtime").read_bytes(), old_runtime
        )
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertTrue(
            (self.root / ".bugate/plan.lock/bugate-update/archived-rollback.json").is_file()
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_hard_crash_after_recovery_archive_marker_resumes_archival(self) -> None:
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        old_runtime, new_runtime = b"legacy-archive", b"current-archive"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old_block={old_block!r}; new_block={new_block!r}
old_runtime={old_runtime!r}; new_runtime={new_runtime!r}
ops=[
 Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old_block).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_block).hexdigest(),'mode':'0644'}}),
 Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old_runtime).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_runtime).hexdigest(),'mode':'0644'}}),
]
def fail_verify():
 raise RuntimeError('force recovered bootstrap')
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_recovery_archive_marker'
TransactionManager({str(self.root)!r}).apply(ops,payload_bytes={{'gitignore':new_block,'runtime':new_runtime}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'9' * 32!r},verify=fail_verify)
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertEqual(
            (self.root / ".bugate/runtime").read_bytes(), old_runtime
        )
        root_state = self.root / ".bugate-update"
        self.assertTrue((root_state / "archived-rollback.json").is_file())
        transition = json.loads(
            (root_state / "bootstrap-transition.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(transition["phase"], "returning-to-plan-lock")
        self.assertTrue((self.root / ".bugate/plan.lock").is_dir())
        self.assertEqual(
            {
                path.name
                for path in (self.root / ".bugate/plan.lock").iterdir()
            },
            {"bootstrap-return.json"},
        )
        pending = self.manager.recovery_required()
        self.assertEqual(pending["status"], "bootstrap_cleanup_required")
        self.assertEqual(pending["transaction_status"], "recovered")

        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "bootstrap_state_archived")
        self.assertFalse(root_state.exists())
        archived = self.root / ".bugate/plan.lock/bugate-update"
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("9" * 32) / "failure-report.json").is_file()
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_hard_crash_after_recovery_plan_lock_publish_resumes_archival(self) -> None:
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        old_runtime, new_runtime = b"legacy-plan-lock", b"current-plan-lock"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old_block={old_block!r}; new_block={new_block!r}
old_runtime={old_runtime!r}; new_runtime={new_runtime!r}
ops=[
 Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old_block).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_block).hexdigest(),'mode':'0644'}}),
 Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old_runtime).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_runtime).hexdigest(),'mode':'0644'}}),
]
def fail_verify():
 raise RuntimeError('force recovered bootstrap')
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_recovery_plan_lock_publish'
TransactionManager({str(self.root)!r}).apply(ops,payload_bytes={{'gitignore':new_block,'runtime':new_runtime}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'1' * 32!r},verify=fail_verify)
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertEqual(
            (self.root / ".bugate/runtime").read_bytes(), old_runtime
        )
        transition = json.loads(
            (self.root / ".bugate-update/bootstrap-transition.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(transition["phase"], "returning-to-plan-lock")
        self.assertTrue(
            (self.root / ".bugate/plan.lock/bootstrap-return.json").is_file()
        )
        pending = self.manager.recovery_required()
        self.assertEqual(pending["status"], "bootstrap_cleanup_required")
        self.assertEqual(pending["transaction_status"], "recovered")

        recovered = self.manager.recover()
        self.assertEqual(recovered["status"], "bootstrap_state_archived")
        archived = self.root / ".bugate/plan.lock/bugate-update"
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertFalse(
            (self.root / ".bugate/plan.lock/bootstrap-return.json").exists()
        )
        self.assertTrue(
            (archived / "transactions" / ("1" * 32) / "failure-report.json").is_file()
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_hard_crash_after_recovery_state_archive_cleans_owner_marker(self) -> None:
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
def fail_verify():
 raise RuntimeError('force recovered bootstrap')
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_recovery_state_archive_publish'
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'2' * 32!r},verify=fail_verify)
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        archived = self.root / ".bugate/plan.lock/bugate-update"
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (self.root / ".bugate/plan.lock/bootstrap-return.json").is_file()
        )
        pending = self.manager.recovery_required()
        self.assertEqual(
            pending["status"], "bootstrap_return_cleanup_required"
        )
        cleaned = self.manager.recover()
        self.assertEqual(
            cleaned["status"], "bootstrap_return_marker_cleaned"
        )
        self.assertFalse(
            (self.root / ".bugate/plan.lock/bootstrap-return.json").exists()
        )
        self.assertTrue(
            (archived / "transactions" / ("2" * 32) / "failure-report.json").is_file()
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_crash_after_bootstrap_commit_finishes_transition_without_rollback(self) -> None:
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        old_runtime, new_runtime = b"legacy-commit", b"current-commit"
        (self.root / ".gitignore").write_bytes(old_block)
        (self.root / ".bugate/runtime").write_bytes(old_runtime)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old_block={old_block!r}; new_block={new_block!r}
old_runtime={old_runtime!r}; new_runtime={new_runtime!r}
ops=[
 Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old_block).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_block).hexdigest(),'mode':'0644'}}),
 Operation('runtime','.bugate/runtime',{{'type':'file','sha256':hashlib.sha256(old_runtime).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new_runtime).hexdigest(),'mode':'0644'}}),
]
os.environ['BUGATE_UPDATE_CRASHPOINT']='before_bootstrap_settle'
TransactionManager({str(self.root)!r}).apply(ops,payload_bytes={{'gitignore':new_block,'runtime':new_runtime}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'0' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertEqual((self.root / ".gitignore").read_bytes(), new_block)
        self.assertEqual(
            (self.root / ".bugate/runtime").read_bytes(), new_runtime
        )
        status = self.manager.recovery_required()
        self.assertEqual(status["status"], "bootstrap_cleanup_required")
        self.assertEqual(status["transaction_status"], "committed")
        cleaned = self.manager.recover()
        self.assertEqual(cleaned["status"], "bootstrap_state_cleaned")
        self.assertTrue((self.root / ".bugate-update").is_dir())
        self.assertFalse(
            (self.root / ".bugate-update/bootstrap-transition.json").exists()
        )
        self.assertFalse((self.root / ".bugate/plan.lock").exists())
        self.assertEqual((self.root / ".gitignore").read_bytes(), new_block)
        self.assertEqual(
            (self.root / ".bugate/runtime").read_bytes(), new_runtime
        )
        self.assertIsNone(self.manager.recovery_required())

    def test_installed_wrapper_rollback_uses_persisted_legacy_manifest(self) -> None:
        legacy = legacy_manifest()
        old_runtime = b"legacy\n"
        new_runtime = b"updated\n"
        runtime = self.root / ".bugate/scripts/legacy.py"
        runtime.parent.mkdir()
        runtime.write_bytes(old_runtime)
        hook = next(
            item for item in legacy["installed_projection"]
            if item["scope"] == "shared_json_fragment"
        )
        hook_path = self.root / hook["target_path"]
        hook_path.parent.mkdir()
        hook_path.write_text(
            json.dumps({"hooks": {hook["event"]: [hook["value"]]}}, indent=2) + "\n",
            encoding="utf-8",
        )
        old_block = next(
            item["content"] for item in legacy["installed_projection"]
            if item["scope"] == "marked_text_block"
        ).encode()
        new_block = old_block.replace(b"# end legacy", b"/.bugate-update/\n# end legacy")
        (self.root / ".gitignore").write_bytes(old_block)
        plan = {
            "schema_version": 1,
            "from_version": "0.3.2",
            "to_version": "0.4.2",
            "from_state_manifest": legacy,
            "managed_changes": [],
        }
        plan["plan_digest"] = contract.sha256_bytes(contract.canonical_json_bytes(plan))
        operations = [
            self.op("runtime", ".bugate/scripts/legacy.py", file_image(old_runtime), file_image(new_runtime)),
            self.op("gitignore", ".gitignore", file_image(old_block), file_image(new_block)),
        ]
        self.manager.apply(
            operations,
            payload_bytes={"runtime": new_runtime, "gitignore": new_block},
            worker_files={
                name: SCRIPTS / name
                for name in (
                    "bugate_update.py",
                    "bugate_update_transaction.py",
                    "bugate_update_engine.py",
                    "bugate_update_source.py",
                    "bugate_install_contract.py",
                    "bugate_legacy_manifest.py",
                    "bugate_core.py",
                )
            },
            input_files={"plan.json": contract.canonical_json_bytes(plan)},
            bootstrap=True,
            gitignore_operation_id="gitignore",
            transaction_id="6" * 32,
        )
        result = transaction.rollback_transaction(
            self.root,
            ".bugate",
            "6" * 32,
            updater_version="0.4.2",
            legacy_manifests=(),
        )
        self.assertEqual(result["decision"], "GO")
        self.assertEqual(result["rollback_of"], "6" * 32)
        self.assertEqual(runtime.read_bytes(), old_runtime)
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertTrue((self.root / ".bugate/plan.lock/bugate-update/sentinel.json").is_file())
        durable = self.root / ".bugate/plan.lock/bugate-update/transactions" / ("6" * 32) / "report.json"
        durable_text = durable.read_text(encoding="utf-8")
        durable_report = json.loads(durable_text)
        self.assertEqual(durable_report["from_version"], "0.3.2")
        self.assertEqual(durable_report["to_version"], "0.4.2")
        self.assertFalse(durable_report["memory_checked"])
        self.assertFalse(durable_report["role_governance_activated"])
        self.assertNotIn(str(self.root), durable_text)
        rollback_report = (
            self.root
            / ".bugate/plan.lock/bugate-update/transactions"
            / result["transaction_id"]
            / "report.json"
        )
        persisted_rollback = json.loads(rollback_report.read_text(encoding="utf-8"))
        self.assertEqual(persisted_rollback["decision"], "GO")
        self.assertEqual(persisted_rollback["rollback_of"], "6" * 32)

        # A later external bootstrap safely reuses only the exact archived
        # updater state, carries its history into root state, and never treats
        # an arbitrary idle plan.lock as owned.
        second = transaction.TransactionManager(self.root)
        second.apply(
            operations,
            payload_bytes={"runtime": new_runtime, "gitignore": new_block},
            input_files={"plan.json": contract.canonical_json_bytes(plan)},
            bootstrap=True,
            gitignore_operation_id="gitignore",
            transaction_id="7" * 32,
        )
        self.assertFalse((self.root / ".bugate/plan.lock").exists())
        self.assertTrue(
            (self.root / ".bugate-update/transactions" / ("6" * 32) / "report.json").is_file()
        )
        self.assertTrue(
            (self.root / ".bugate-update/transactions" / ("7" * 32) / "report.json").is_file()
        )

    def test_archived_history_survives_reuse_prepare_failure(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        operation = self.op(
            "gitignore",
            ".gitignore",
            file_image(old_block),
            file_image(new_block),
        )
        manager = transaction.TransactionManager(self.root)
        with mock.patch.object(
            manager,
            "_prepare_transaction",
            side_effect=OSError(errno.ENOSPC, "synthetic state disk full"),
        ), self.assertRaises(OSError):
            manager.apply(
                [operation],
                payload_bytes={"gitignore": new_block},
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="9" * 32,
            )
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertFalse(
            (archived / "transactions" / ("9" * 32)).exists()
        )
        self.assertFalse((archived / "archive-reuse-transition.json").exists())
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertIsNone(
            transaction.TransactionManager(self.root).recovery_required()
        )

    def test_crash_before_archived_reuse_prepare_preserves_history(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_archive_reuse_intent'
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'a' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        manager = transaction.TransactionManager(self.root)
        self.assertEqual(
            manager.recovery_required()["status"],
            "archive_reuse_intent_cleanup_required",
        )
        manager.recover()
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertFalse(
            (archived / "transactions" / ("a" * 32)).exists()
        )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertIsNone(manager.recovery_required())

    def test_crash_after_archived_reuse_prepare_recovers_without_history_loss(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_archive_reuse_prepare'
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'b' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        manager = transaction.TransactionManager(self.root)
        self.assertEqual(
            manager.recovery_required()["status"],
            "archive_reuse_transaction",
        )
        recovered = manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertTrue(
            (archived / "transactions" / ("b" * 32) / "failure-report.json").is_file()
        )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertIsNone(manager.recovery_required())

    def test_crash_after_archived_reuse_activation_recovers_without_marker_loss(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_archive_reuse_activate'
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'d' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        self.assertFalse((archived / "archived-rollback.json").exists())
        self.assertTrue((archived / "archive-reuse-transition.json").is_file())
        manager = transaction.TransactionManager(self.root)
        self.assertEqual(
            manager.recovery_required()["status"],
            "archive_reuse_transaction",
        )
        recovered = manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertTrue(
            (archived / "transactions" / ("d" * 32) / "failure-report.json").is_file()
        )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertIsNone(manager.recovery_required())

    def test_crash_during_private_archive_reuse_prepare_preserves_history(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        for index, crashpoint in enumerate(
            (
                "after_prepare_transaction_dir_create",
                "after_prepare_bundles_before_journal",
            )
        ):
            transaction_id = f"{index + 4:x}" * 32
            script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']={crashpoint!r}
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={transaction_id!r})
"""
            completed = subprocess.run(
                [sys.executable, "-c", script], check=False
            )
            self.assertEqual(completed.returncode, 97)
            manager = transaction.TransactionManager(self.root)
            self.assertEqual(
                manager.recovery_required()["status"],
                "archive_reuse_intent_cleanup_required",
            )
            cleaned = manager.recover()
            self.assertEqual(
                cleaned["status"], "archive_reuse_intent_cleaned"
            )
            self.assertTrue((archived / "archived-rollback.json").is_file())
            self.assertTrue(
                (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
            )
            self.assertFalse(
                (archived / "transactions" / transaction_id).exists()
            )
            self.assertEqual(
                (self.root / ".gitignore").read_bytes(), old_block
            )
            self.assertIsNone(manager.recovery_required())

    def test_crash_after_private_prepare_publish_recovers_complete_transaction(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_prepare_transaction_publish'
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'6' * 32!r})
"""
        completed = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(completed.returncode, 97)
        manager = transaction.TransactionManager(self.root)
        pending = manager.recovery_required()
        self.assertEqual(pending["status"], "archive_reuse_transaction")
        self.assertEqual(pending["transaction_status"], "prepared")
        recovered = manager.recover()
        self.assertEqual(recovered["status"], "recovered")
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertTrue(
            (archived / "transactions" / ("6" * 32) / "failure-report.json").is_file()
        )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertIsNone(manager.recovery_required())

    def test_crash_after_archive_reuse_final_marker_preserves_history(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        apply_script = f"""
import hashlib, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager, Operation
old={old_block!r}; new={new_block!r}
operation=Operation('gitignore','.gitignore',{{'type':'file','sha256':hashlib.sha256(old).hexdigest(),'mode':'0644'}},{{'type':'file','sha256':hashlib.sha256(new).hexdigest(),'mode':'0644'}})
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_archive_reuse_activate'
TransactionManager({str(self.root)!r}).apply([operation],payload_bytes={{'gitignore':new}},bootstrap=True,gitignore_operation_id='gitignore',transaction_id={'7' * 32!r})
"""
        completed = subprocess.run(
            [sys.executable, "-c", apply_script], check=False
        )
        self.assertEqual(completed.returncode, 97)
        recovery_script = f"""
import os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
from bugate_update_transaction import TransactionManager
os.environ['BUGATE_UPDATE_CRASHPOINT']='after_archive_reuse_final_marker'
TransactionManager({str(self.root)!r}).recover()
"""
        completed = subprocess.run(
            [sys.executable, "-c", recovery_script], check=False
        )
        self.assertEqual(completed.returncode, 97)
        transition_path = archived / "archive-reuse-transition.json"
        reuse = json.loads(transition_path.read_text(encoding="utf-8"))
        self.assertEqual(reuse["phase"], "finalizing")
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertTrue(
            (archived / "transactions" / ("7" * 32) / "failure-report.json").is_file()
        )
        manager = transaction.TransactionManager(self.root)
        self.assertEqual(
            manager.recovery_required()["status"],
            "archive_reuse_finalize_required",
        )
        finalized = manager.recover()
        self.assertEqual(finalized["status"], "archive_reuse_finalized")
        self.assertFalse(transition_path.exists())
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertIsNone(manager.recovery_required())

    def test_reused_bootstrap_late_failure_preserves_all_history(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        operation = self.op(
            "gitignore",
            ".gitignore",
            file_image(old_block),
            file_image(new_block),
        )

        def fail_verify() -> None:
            raise transaction.InjectedFailure("late reused bootstrap verify")

        manager = transaction.TransactionManager(self.root)
        with self.assertRaises(transaction.InjectedFailure):
            manager.apply(
                [operation],
                payload_bytes={"gitignore": new_block},
                bootstrap=True,
                gitignore_operation_id="gitignore",
                transaction_id="e" * 32,
                verify=fail_verify,
            )
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertFalse((archived / "archive-reuse-transition.json").exists())
        self.assertTrue(
            (archived / "transactions" / ("8" * 32) / "journal.json").is_file()
        )
        self.assertTrue(
            (archived / "transactions" / ("e" * 32) / "failure-report.json").is_file()
        )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertIsNone(manager.recovery_required())

    def test_bootstrap_rejects_incomplete_archived_history(self) -> None:
        archived, _report = self.archived_runtime_history()
        old_journal = archived / "transactions" / ("8" * 32) / "journal.json"
        old_journal.unlink()
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        operation = self.op(
            "gitignore",
            ".gitignore",
            file_image(old_block),
            file_image(new_block),
        )
        manager = transaction.TransactionManager(self.root)
        self.assertEqual(manager.recovery_required()["status"], "invalid")
        with self.assertRaises(transaction.JournalError):
            manager.apply(
                [operation],
                payload_bytes={"gitignore": new_block},
                bootstrap=True,
                gitignore_operation_id="gitignore",
            )
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertTrue((archived / "archived-rollback.json").is_file())
        self.assertTrue((archived / "transactions" / ("8" * 32)).is_dir())

    def test_archived_marker_detects_tree_drift_outside_journals(self) -> None:
        archived, _report = self.archived_runtime_history()
        (archived / "unexpected.bin").write_bytes(b"drift")
        status = transaction.TransactionManager(self.root).recovery_required()
        self.assertEqual(status["status"], "invalid")
        self.assertIn("tree digest mismatch", status["error"])

    def test_direct_plan_lock_state_is_fail_closed_without_history_deletion(self) -> None:
        target = self.root / ".bugate/runtime"
        target.write_bytes(b"old")
        self.manager.apply(
            [
                self.op(
                    "runtime",
                    ".bugate/runtime",
                    file_image(b"old"),
                    file_image(b"new"),
                )
            ],
            payload_bytes={"runtime": b"new"},
            transaction_id="c" * 32,
        )
        direct = self.root / ".bugate/plan.lock"
        os.rename(self.root / ".bugate-update", direct)
        old_journal = direct / "transactions" / ("c" * 32) / "journal.json"
        old_block = b"/.bugate/plan.lock\n"
        new_block = old_block + b"/.bugate-update/\n"
        (self.root / ".gitignore").write_bytes(old_block)
        operation = self.op(
            "gitignore",
            ".gitignore",
            file_image(old_block),
            file_image(new_block),
        )
        manager = transaction.TransactionManager(self.root)
        self.assertEqual(
            manager.recovery_required()["status"],
            "unsupported_legacy_archive",
        )
        with self.assertRaisesRegex(
            transaction.JournalError, "unsupported_legacy_archive"
        ):
            manager.apply(
                [operation],
                payload_bytes={"gitignore": new_block},
                bootstrap=True,
                gitignore_operation_id="gitignore",
            )
        self.assertTrue(old_journal.is_file())
        self.assertEqual((self.root / ".gitignore").read_bytes(), old_block)
        self.assertFalse((self.root / ".bugate-update").exists())

    def test_crash_window_with_dual_archive_state_is_recoverable(self) -> None:
        old, new = b"archive-old", b"archive-new"
        target = self.root / ".bugate/runtime"
        target.write_bytes(old)
        self.manager.apply(
            [self.op("runtime", ".bugate/runtime", file_image(old), file_image(new))],
            payload_bytes={"runtime": new},
        )

        def fail(name: str) -> None:
            if name == "after_archive_publish":
                raise transaction.InjectedFailure("archive publish crash window")

        interrupted = transaction.TransactionManager(self.root, injector=fail)
        with self.assertRaises(transaction.InjectedFailure):
            interrupted.archive_legacy_rollback_state()
        self.assertTrue((self.root / ".bugate-update/sentinel.json").is_file())
        self.assertTrue(
            (self.root / ".bugate/plan.lock/bugate-update/archived-rollback.json").is_file()
        )
        self.assertEqual(
            self.manager.recovery_required()["status"], "archive_migration_required"
        )
        result = self.manager.recover()
        self.assertEqual(result["status"], "archive_recovered")
        self.assertFalse((self.root / ".bugate-update").exists())
        self.assertIsNone(self.manager.recovery_required())

    def test_tampered_journal_is_reported_without_mutation(self) -> None:
        data = b"old"
        (self.root / ".bugate/runtime").write_bytes(data)
        op = self.op("runtime", ".bugate/runtime", file_image(data), file_image(b"new"))
        self.manager.apply([op], payload_bytes={"runtime": b"new"}, transaction_id="5" * 32)
        journal_path = self.root / ".bugate-update/transactions" / ("5" * 32) / "journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["vendor_dir"] = "elsewhere"
        journal_path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(transaction.JournalError):
            self.manager.rollback("5" * 32)
        self.assertEqual((self.root / ".bugate/runtime").read_bytes(), b"new")

    def test_state_validation_rejects_empty_transaction_store(self) -> None:
        target = self.root / ".bugate/runtime"
        target.write_bytes(b"old")
        self.manager._initialize_state(self.manager.state)

        with self.assertRaisesRegex(transaction.JournalError, "empty"):
            self.manager._validate_state_dir(self.manager.state)
        status = self.manager.recovery_required()
        self.assertEqual(status["status"], "invalid")
        self.assertIn("empty", status["error"])
        with self.assertRaisesRegex(transaction.JournalError, "unsafe prior"):
            self.manager.apply(
                [
                    self.op(
                        "runtime",
                        ".bugate/runtime",
                        file_image(b"old"),
                        file_image(b"new"),
                    )
                ],
                payload_bytes={"runtime": b"new"},
            )
        self.assertEqual(target.read_bytes(), b"old")

    def test_state_validation_rejects_unknown_transaction_store_entries(self) -> None:
        for case in (
            "unknown-file",
            "unknown-directory",
            "orphan-hex-directory",
            "hex-symlink",
        ):
            with self.subTest(case=case):
                root = Path(self.temporary.name) / case
                (root / ".bugate").mkdir(parents=True)
                target = root / ".bugate/runtime"
                target.write_bytes(b"old")
                manager = transaction.TransactionManager(root)
                operation = self.op(
                    "runtime",
                    ".bugate/runtime",
                    file_image(b"old"),
                    file_image(b"new"),
                )
                manager.apply(
                    [operation],
                    payload_bytes={"runtime": b"new"},
                    transaction_id="a" * 32,
                )
                store = root / ".bugate-update/transactions"
                if case == "unknown-file":
                    extra = store / "operator-owned"
                    extra.write_bytes(b"preserve")
                elif case == "unknown-directory":
                    extra = store / "operator-owned"
                    extra.mkdir()
                elif case == "orphan-hex-directory":
                    extra = store / ("f" * 32)
                    extra.mkdir()
                else:
                    extra = store / ("e" * 32)
                    extra.symlink_to("a" * 32, target_is_directory=True)

                with self.assertRaises(transaction.JournalError):
                    manager._validate_state_dir(manager.state)
                status = manager.recovery_required()
                self.assertEqual(status["status"], "invalid")
                with self.assertRaisesRegex(transaction.JournalError, "unsafe prior"):
                    manager.apply(
                        [
                            self.op(
                                "second",
                                ".bugate/second",
                                ABSENT,
                                file_image(b"second"),
                            )
                        ],
                        payload_bytes={"second": b"second"},
                    )
                self.assertEqual(target.read_bytes(), b"new")
                self.assertFalse((root / ".bugate/second").exists())
                self.assertTrue(extra.exists() or extra.is_symlink())


class InjectionPointMatrixTests(unittest.TestCase):
    """Source-synchronized handled-failure and hard-crash acceptance matrix."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="bugate-update-injection-matrix-"
        )
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _family(point: str) -> str:
        for prefix in (
            "before_mutation:",
            "after_mutation:",
            "after_target_removal:",
        ):
            if point.startswith(prefix):
                return prefix + "{id}"
        return point

    @staticmethod
    def _source_injection_calls() -> tuple[str, ...]:
        source = SCRIPTS / "bugate_update_transaction.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        calls: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_inject"
                and node.args
            ):
                continue
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                value = argument.value
            elif isinstance(argument, ast.JoinedStr):
                value = "".join(
                    str(part.value)
                    if isinstance(part, ast.Constant)
                    else "{id}"
                    for part in argument.values
                )
            else:
                raise AssertionError(
                    f"non-canonical injection expression at line {node.lineno}"
                )
            calls.append((node.lineno, value))
        return tuple(value for _line, value in sorted(calls))

    def test_canonical_injection_registry_matches_every_source_call_site(self) -> None:
        source_calls = self._source_injection_calls()
        self.assertEqual(source_calls, CANONICAL_INJECTION_CALLS)
        self.assertEqual(frozenset(source_calls), CANONICAL_INJECTION_FAMILIES)
        self.assertEqual(len(source_calls), 38)
        self.assertEqual(len(CANONICAL_INJECTION_FAMILIES), 32)
        self.assertEqual(len(INJECTION_POINT_CASES), 32)
        self.assertEqual(
            {self._family(point) for point in INJECTION_POINT_CASES},
            CANONICAL_INJECTION_FAMILIES,
        )

    def test_every_failpoint_environment_raises_injected_failure(self) -> None:
        for point in INJECTION_POINT_CASES:
            with self.subTest(point=point):
                seen: list[str] = []
                environment = dict(os.environ)
                for key in (
                    "BUGATE_UPDATE_FAILPOINT",
                    "BUGATE_UPDATE_CRASHPOINT",
                    "BUGATE_UPDATE_PAUSEPOINT",
                ):
                    environment.pop(key, None)
                environment["BUGATE_UPDATE_FAILPOINT"] = point
                manager = transaction.TransactionManager(
                    self.base, injector=seen.append
                )
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(
                        transaction.InjectedFailure, re.escape(point)
                    ):
                        manager._inject(point)
                self.assertEqual(seen, [point])

    def _worker(
        self,
        root: Path,
        scenario: str,
        mode: str,
        point: str,
        marker: Path,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        for key in (
            "BUGATE_UPDATE_FAILPOINT",
            "BUGATE_UPDATE_CRASHPOINT",
            "BUGATE_UPDATE_PAUSEPOINT",
        ):
            environment.pop(key, None)
        return subprocess.run(
            [
                sys.executable,
                "-c",
                MATRIX_WORKER,
                str(root),
                scenario,
                mode,
                point,
                str(SCRIPTS),
                str(marker),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=20,
        )

    def _settle_recovery(self, root: Path) -> None:
        manager = transaction.TransactionManager(root)
        for _attempt in range(12):
            pending = manager.recovery_required()
            if pending is None:
                return
            result = manager.recover()
            self.assertIsNotNone(result)
        self.fail(f"recovery did not settle: {manager.recovery_required()}")

    def _read_canonical_sealed(
        self, path: Path, *, label: str
    ) -> dict[str, Any]:
        self.assertTrue(
            path.is_file() and not path.is_symlink(),
            f"{label} is not a physical regular file: {path}",
        )
        raw = path.read_bytes()
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.fail(f"{label} is not valid JSON: {path}: {exc}")
        self.assertIsInstance(document, dict, f"{label} is not an object: {path}")
        self.assertEqual(
            raw,
            contract.canonical_json_bytes(document),
            f"{label} is not canonical JSON: {path}",
        )
        try:
            contract.validate_self_digest(document)
        except contract.ContractError as exc:
            self.fail(f"{label} self-digest is invalid: {path}: {exc}")
        return document

    def _assert_transaction_reports(
        self, root: Path, *, settled: bool
    ) -> list[dict[str, Any]]:
        journal_paths = sorted(root.rglob("journal.json"))
        journal_set = set(journal_paths)
        allowed_states = {
            root / transaction.ROOT_STATE,
            root
            / ".bugate"
            / "plan.lock"
            / transaction.BOOTSTRAP_CHILD,
        }

        def directory_identity(path: Path, *, label: str) -> dict[str, int]:
            self.assertTrue(
                path.is_dir() and not path.is_symlink(),
                f"{label} is not a physical directory: {path}",
            )
            metadata = os.lstat(path)
            return {"device": metadata.st_dev, "inode": metadata.st_ino}

        root_identity = directory_identity(root, label="workspace root")
        state_identities: dict[Path, tuple[dict[str, int], dict[str, int]]] = {}
        for journal_path in journal_paths:
            state = journal_path.parent.parent.parent
            self.assertIn(
                state,
                allowed_states,
                f"journal is outside a canonical updater state root: {journal_path}",
            )
            if state in state_identities:
                continue
            state_identity = directory_identity(state, label="updater state root")
            transactions_path = state / "transactions"
            transactions_identity = directory_identity(
                transactions_path, label="transaction store"
            )
            sentinel = self._read_canonical_sealed(
                state / "sentinel.json", label="updater state sentinel"
            )
            self.assertEqual(
                sentinel,
                contract.seal_document(
                    {
                        "schema_version": transaction.STATE_SCHEMA,
                        "kind": transaction.SENTINEL_KIND,
                        "root_identity": root_identity,
                        "vendor_dir": ".bugate",
                        "state_identity": state_identity,
                        "transactions_identity": transactions_identity,
                    }
                ),
                f"updater state sentinel is not bound to its physical tree: {state}",
            )
            state_identities[state] = (state_identity, transactions_identity)

        for report_name in (
            "report.json",
            "report.pending.json",
            "failure-report.json",
        ):
            for report_path in root.rglob(report_name):
                self.assertIn(
                    report_path.with_name("journal.json"),
                    journal_set,
                    f"orphan transaction report: {report_path}",
                )

        records: list[dict[str, Any]] = []
        for journal_path in journal_paths:
            state = journal_path.parent.parent.parent
            state_identity, transactions_identity = state_identities[state]
            journal = self._read_canonical_sealed(
                journal_path, label="transaction journal"
            )
            transaction_id = journal.get("transaction_id")
            self.assertEqual(
                journal_path.parent.parent.name,
                "transactions",
                f"journal is outside the canonical transaction store: {journal_path}",
            )
            self.assertEqual(
                journal_path.parent.name,
                transaction_id,
                f"journal identity differs from its directory: {journal_path}",
            )
            self.assertRegex(
                transaction_id or "",
                transaction.TRANSACTION_ID_RE,
                f"invalid journal transaction identity: {journal_path}",
            )
            self.assertEqual(journal.get("root_identity"), root_identity, journal_path)
            self.assertEqual(journal.get("vendor_dir"), ".bugate", journal_path)
            directory_bindings = journal.get("directory_bindings")
            self.assertIsInstance(
                directory_bindings,
                dict,
                f"journal lacks directory bindings: {journal_path}",
            )
            self.assertEqual(
                set(directory_bindings),
                {"state", "transactions", "transaction", "local_directories"},
                journal_path,
            )
            self.assertEqual(directory_bindings["state"], state_identity, journal_path)
            self.assertEqual(
                directory_bindings["transactions"],
                transactions_identity,
                journal_path,
            )
            self.assertEqual(
                directory_bindings["transaction"],
                directory_identity(
                    journal_path.parent, label="transaction directory"
                ),
                journal_path,
            )
            local_directories = directory_bindings["local_directories"]
            self.assertIsInstance(local_directories, dict, journal_path)
            self.assertEqual(
                set(local_directories),
                transaction.LOCAL_TRANSACTION_DIRECTORIES,
                journal_path,
            )
            for directory_name in sorted(transaction.LOCAL_TRANSACTION_DIRECTORIES):
                self.assertEqual(
                    local_directories[directory_name],
                    directory_identity(
                        journal_path.parent / directory_name,
                        label=f"transaction {directory_name} directory",
                    ),
                    journal_path,
                )
            status = journal.get("status")
            if settled:
                self.assertIn(
                    status,
                    {"committed", "recovered"},
                    f"settled matrix retained a non-terminal journal: {journal_path}",
                )

            report_path = journal_path.with_name("report.json")
            pending_path = journal_path.with_name("report.pending.json")
            failure_path = journal_path.with_name("failure-report.json")
            report_present = report_path.exists() or report_path.is_symlink()
            failure_candidates = sorted(
                path
                for path in journal_path.parent.iterdir()
                if path.name.startswith("failure-report")
            )
            failure_present = failure_path.exists() or failure_path.is_symlink()
            self.assertEqual(
                status == "committed",
                report_present,
                f"committed journal/final report mismatch: {journal_path}",
            )
            self.assertEqual(
                status == "recovered",
                failure_present,
                f"recovered journal/failure report mismatch: {journal_path}",
            )
            self.assertEqual(
                failure_candidates,
                [failure_path] if status == "recovered" else [],
                f"transaction has a non-canonical failure report set: {journal_path}",
            )
            if status == "recovered":
                self.assertFalse(
                    pending_path.exists() or pending_path.is_symlink(),
                    f"recovered transaction retained a pending success report: {pending_path}",
                )
            if settled:
                self.assertFalse(
                    pending_path.exists() or pending_path.is_symlink(),
                    f"settled transaction retained a pending report: {pending_path}",
                )

            report = None
            if report_present:
                report = self._read_canonical_sealed(
                    report_path, label="transaction report"
                )
                for field in (
                    "transaction_id",
                    "kind",
                    "operations",
                    "source_transaction",
                ):
                    self.assertEqual(
                        report.get(field),
                        journal.get(field),
                        f"transaction report {field} differs from journal: {report_path}",
                    )
                self.assertEqual(report.get("status"), "committed", report_path)

            if pending_path.is_file():
                pending = self._read_canonical_sealed(
                    pending_path, label="pending transaction report"
                )
                for field in (
                    "transaction_id",
                    "kind",
                    "operations",
                    "source_transaction",
                ):
                    self.assertEqual(
                        pending.get(field),
                        journal.get(field),
                        f"pending report {field} differs from journal: {pending_path}",
                    )
                self.assertEqual(pending.get("status"), "committed", pending_path)

            if failure_present:
                failure = self._read_canonical_sealed(
                    failure_path, label="transaction failure report"
                )
                self.assertEqual(
                    set(failure),
                    {
                        "schema_version",
                        "transaction_id",
                        "kind",
                        "status",
                        "error_type",
                        "error",
                        "self_digest",
                    },
                    failure_path,
                )
                self.assertEqual(
                    failure.get("transaction_id"), transaction_id, failure_path
                )
                self.assertEqual(failure.get("kind"), journal.get("kind"), failure_path)
                self.assertEqual(failure.get("status"), "recovered", failure_path)
                self.assertIsInstance(failure.get("error_type"), str, failure_path)
                self.assertIsInstance(failure.get("error"), str, failure_path)

            records.append(
                {
                    "path": journal_path,
                    "journal": journal,
                    "report": report,
                }
            )
        return records

    def _assert_expected_cutover_history(
        self, root: Path, scenario: str, point: str
    ) -> None:
        records = self._assert_transaction_reports(root, settled=True)
        by_id: dict[str, dict[str, Any]] = {}
        for record in records:
            journal = record["journal"]
            transaction_id = journal["transaction_id"]
            self.assertNotIn(
                transaction_id,
                by_id,
                f"settled transaction is duplicated across state stores: {transaction_id}",
            )
            by_id[transaction_id] = journal

        history_id = "1" * 32
        ignore_old = b"/.bugate/plan.lock\n"
        ignore_new = ignore_old + b"/.bugate-update/\n"
        expected_operations = {
            "a" * 32: [
                {
                    "id": "runtime",
                    "target_path": ".bugate/runtime",
                    "pre": file_image(b"normal-old"),
                    "post": file_image(b"normal-new"),
                },
                {
                    "id": "metadata:installed-lock",
                    "target_path": ".bugate/bugate.lock.json",
                    "pre": ABSENT,
                    "post": file_image(b"lock-new"),
                },
            ],
            "b" * 32: [
                {
                    "id": "type-change",
                    "target_path": ".bugate/type-target",
                    "pre": DIRECTORY,
                    "post": file_image(b"type-new"),
                }
            ],
            "c" * 32: [
                {
                    "id": "gitignore",
                    "target_path": ".gitignore",
                    "pre": file_image(ignore_old),
                    "post": file_image(ignore_new),
                },
                {
                    "id": "runtime",
                    "target_path": ".bugate/runtime",
                    "pre": file_image(b"bootstrap-old"),
                    "post": file_image(b"bootstrap-new"),
                },
            ],
            history_id: [
                {
                    "id": "history",
                    "target_path": ".bugate/runtime",
                    "pre": file_image(b"history-old"),
                    "post": file_image(b"history-new"),
                }
            ],
            "d" * 32: [
                {
                    "id": "gitignore",
                    "target_path": ".gitignore",
                    "pre": file_image(ignore_old),
                    "post": file_image(ignore_new),
                }
            ],
            "e" * 32: [
                {
                    "id": "gitignore",
                    "target_path": ".gitignore",
                    "pre": file_image(ignore_old),
                    "post": file_image(ignore_new),
                },
                {
                    "id": "runtime",
                    "target_path": ".bugate/runtime",
                    "pre": file_image(b"recovery-old"),
                    "post": file_image(b"recovery-new"),
                },
            ],
        }
        rollback_operations = [
            {
                "id": "rollback:history",
                "target_path": ".bugate/runtime",
                "pre": file_image(b"history-new"),
                "post": file_image(b"history-old"),
            }
        ]

        def assert_record(
            transaction_id: str,
            *,
            kind: str,
            status: str,
            source_transaction: str | None,
        ) -> None:
            self.assertIn(transaction_id, by_id)
            journal = by_id[transaction_id]
            self.assertEqual(journal["kind"], kind)
            self.assertEqual(journal["status"], status)
            self.assertEqual(journal["source_transaction"], source_transaction)
            self.assertEqual(
                journal["operations"],
                rollback_operations
                if kind == "rollback"
                else expected_operations[transaction_id],
            )

        if scenario == "normal":
            self.assertEqual(set(by_id), {"a" * 32})
            assert_record(
                "a" * 32,
                kind="apply",
                status=(
                    "committed"
                    if point in {"after_journal_commit", "after_commit"}
                    else "recovered"
                ),
                source_transaction=None,
            )
        elif scenario == "type-change":
            self.assertEqual(set(by_id), {"b" * 32})
            assert_record(
                "b" * 32,
                kind="apply",
                status="recovered",
                source_transaction=None,
            )
        elif scenario == "bootstrap":
            if point in {
                "before_bootstrap_publish",
                "after_bootstrap_publish",
                "after_gitignore",
            }:
                self.assertEqual(by_id, {})
            else:
                self.assertEqual(set(by_id), {"c" * 32})
                assert_record(
                    "c" * 32,
                    kind="apply",
                    status=(
                        "committed"
                        if point == "before_bootstrap_settle"
                        else "recovered"
                    ),
                    source_transaction=None,
                )
        elif scenario == "archive":
            self.assertEqual(set(by_id), {history_id})
            assert_record(
                history_id,
                kind="apply",
                status="committed",
                source_transaction=None,
            )
        elif scenario == "rollback-archive":
            assert_record(
                history_id,
                kind="apply",
                status="committed",
                source_transaction=None,
            )
            rollback_ids = set(by_id) - {history_id}
            if point == "before_legacy_archive":
                self.assertEqual(len(rollback_ids), 1)
                assert_record(
                    rollback_ids.pop(),
                    kind="rollback",
                    status="committed",
                    source_transaction=history_id,
                )
            else:
                self.assertEqual(rollback_ids, set())
        elif scenario == "reuse":
            assert_record(
                history_id,
                kind="apply",
                status="committed",
                source_transaction=None,
            )
            early = {
                "after_archive_reuse_intent",
                "after_prepare_transaction_dir_create",
                "after_prepare_bundles_before_journal",
            }
            expected = {history_id} if point in early else {history_id, "d" * 32}
            self.assertEqual(set(by_id), expected)
            if point not in early:
                assert_record(
                    "d" * 32,
                    kind="apply",
                    status="recovered",
                    source_transaction=None,
                )
        elif scenario == "recovery-bootstrap":
            self.assertEqual(set(by_id), {"e" * 32})
            assert_record(
                "e" * 32,
                kind="apply",
                status="recovered",
                source_transaction=None,
            )
        elif scenario == "reuse-finalize":
            self.assertEqual(set(by_id), {history_id, "d" * 32})
            assert_record(
                history_id,
                kind="apply",
                status="committed",
                source_transaction=None,
            )
            assert_record(
                "d" * 32,
                kind="apply",
                status="recovered",
                source_transaction=None,
            )
        else:
            self.fail(f"unhandled matrix scenario: {scenario}")

    def _assert_target_semantics(self, root: Path, scenario: str, point: str) -> None:
        self.assertEqual((root / "sut-owned.txt").read_bytes(), b"operator-owned\n")
        if scenario == "normal":
            committed = point in {"after_journal_commit", "after_commit"}
            self.assertEqual(
                (root / ".bugate/runtime").read_bytes(),
                b"normal-new" if committed else b"normal-old",
            )
            self.assertEqual(
                (root / ".bugate/bugate.lock.json").exists(), committed
            )
        elif scenario == "type-change":
            self.assertTrue((root / ".bugate/type-target").is_dir())
            self.assertFalse((root / ".bugate/type-target").is_symlink())
        elif scenario == "bootstrap":
            committed = point == "before_bootstrap_settle"
            old = b"/.bugate/plan.lock\n"
            new = old + b"/.bugate-update/\n"
            self.assertEqual((root / ".gitignore").read_bytes(), new if committed else old)
            self.assertEqual(
                (root / ".bugate/runtime").read_bytes(),
                b"bootstrap-new" if committed else b"bootstrap-old",
            )
        elif scenario == "archive":
            self.assertEqual((root / ".bugate/runtime").read_bytes(), b"history-new")
        elif scenario == "rollback-archive":
            expected = (
                b"history-old"
                if point == "before_legacy_archive"
                else b"history-new"
            )
            self.assertEqual((root / ".bugate/runtime").read_bytes(), expected)
        elif scenario == "reuse":
            self.assertEqual((root / ".bugate/runtime").read_bytes(), b"history-new")
            self.assertEqual((root / ".gitignore").read_bytes(), b"/.bugate/plan.lock\n")
        elif scenario == "recovery-bootstrap":
            self.assertEqual((root / ".bugate/runtime").read_bytes(), b"recovery-old")
            self.assertEqual((root / ".gitignore").read_bytes(), b"/.bugate/plan.lock\n")
        elif scenario == "reuse-finalize":
            self.assertEqual((root / ".bugate/runtime").read_bytes(), b"history-new")
            self.assertEqual((root / ".gitignore").read_bytes(), b"/.bugate/plan.lock\n")
        else:
            self.fail(f"unhandled matrix scenario: {scenario}")

    def _assert_final_semantics(self, root: Path, scenario: str, point: str) -> None:
        self._assert_target_semantics(root, scenario, point)
        self.assertFalse(list(root.rglob("report.pending.json")))
        self._assert_expected_cutover_history(root, scenario, point)
        self.assertIsNone(transaction.TransactionManager(root).recovery_required())

    def _run_matrix(self, mode: str) -> None:
        for index, (point, scenario) in enumerate(INJECTION_POINT_CASES.items()):
            with self.subTest(mode=mode, point=point, scenario=scenario):
                root = self.base / f"{mode}-{index:02d}-repo"
                marker = self.base / f"{mode}-{index:02d}-seen"
                outside = self.base / f"{mode}-{index:02d}-outside"
                outside.write_bytes(b"outside-operator-owned\n")
                if scenario == "reuse-finalize":
                    prepared = self._worker(
                        root,
                        "reuse-prepare",
                        "crash",
                        "after_archive_reuse_activate",
                        marker,
                    )
                    self.assertEqual(
                        prepared.returncode,
                        97,
                        prepared.stdout + prepared.stderr,
                    )
                    marker.unlink(missing_ok=True)
                completed = self._worker(root, scenario, mode, point, marker)
                expected_code = (
                    97
                    if mode == "crash"
                    else 0
                    if point in {"after_journal_commit", "after_commit"}
                    else 86
                )
                self.assertEqual(
                    completed.returncode,
                    expected_code,
                    completed.stdout + completed.stderr,
                )
                self.assertTrue(marker.is_file(), completed.stdout + completed.stderr)
                self.assertEqual(marker.read_text(encoding="utf-8"), point)
                self.assertEqual(outside.read_bytes(), b"outside-operator-owned\n")
                if mode == "fail":
                    # Handled failures must already expose either the exact
                    # preimage or a legitimately committed cutover before any
                    # next-run cleanup/finalization is invoked.
                    self._assert_target_semantics(root, scenario, point)
                    self._assert_transaction_reports(root, settled=False)
                self._settle_recovery(root)
                self._assert_final_semantics(root, scenario, point)
                self.assertEqual(outside.read_bytes(), b"outside-operator-owned\n")

    def test_every_failpoint_has_fail_closed_or_finalize_semantics(self) -> None:
        self._run_matrix("fail")

    def test_every_crashpoint_has_next_run_recovery_or_finalize_semantics(self) -> None:
        self._run_matrix("crash")


if __name__ == "__main__":
    unittest.main()
