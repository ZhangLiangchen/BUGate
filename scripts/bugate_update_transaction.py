#!/usr/bin/env python3
"""POSIX transaction boundary for the imported-mode BUGate updater.

The planner owns policy and supplies exact pre/post images.  This module owns
the narrower durability problem: an exclusive workspace lock, ignored local
state, durable journals, atomic per-path replacement, crash recovery, and
explicit rollback.  It never discovers or expands BUGate ownership itself.

There is deliberately no fallback from the platform's exclusive rename
primitive.  A portable ``lstat(); rename()`` sequence can replace a directory
created between those calls and therefore cannot satisfy the pre-lock
bootstrap contract.
"""
from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping

# A transaction worker executes from durable local state; importing its sibling
# modules must not create undeclared cache files in that verified bundle.
sys.dont_write_bytecode = True

import bugate_install_contract as contract


STATE_SCHEMA = 1
SENTINEL_KIND = "bugate-imported-update-state"
ROOT_STATE = ".bugate-update"
BOOTSTRAP_CHILD = "bugate-update"
ARCHIVED_MARKER = "archived-rollback.json"
ARCHIVE_TRANSITION = "archive-transition.json"
ARCHIVE_REUSE_TRANSITION = "archive-reuse-transition.json"
BOOTSTRAP_TRANSITION = "bootstrap-transition.json"
BOOTSTRAP_RETURN_MARKER = "bootstrap-return.json"
TRANSACTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
IMAGE_TYPES = {"absent", "file", "directory", "symlink"}
JOURNAL_STATUSES = {
    "prepared",
    "applying",
    "committed",
    "recovered",
    "recovery_failed",
}
REPORT_METADATA_FIELDS = {
    "decision",
    "engine_updated",
    "rollback_of",
    "codex_hook_hash_changed",
    "new_session_required",
    "profile_migration",
    "memory_checked",
    "role_governance_activated",
    "rollback_available",
}
SUCCESS_REPORT_OPTIONAL_FIELDS = {
    *REPORT_METADATA_FIELDS,
    "from_version",
    "to_version",
    "release_digest",
    "archive_sha256",
    "manifest_sha256",
    "source_kind",
    "managed_summary",
    "hook_changes",
}
LOCAL_TRANSACTION_DIRECTORIES = {"stage", "worker", "input", "backup"}
# Complete-store validation deliberately pins every transaction inode until
# its final name snapshot.  Bound that security cost explicitly; operators can
# archive settled legacy history instead of degrading to an unpinned scan.
MAX_PINNED_TRANSACTION_HISTORY = 128
ARCHIVE_REUSE_PHASES = {"preparing", "active", "finalizing"}
BOOTSTRAP_PHASES = {
    "published",
    "retiring-plan-lock",
    "plan-lock-retired",
    "returning-to-plan-lock",
}


class TransactionError(RuntimeError):
    """Base error for transaction safety failures."""


class ConcurrentUpdateError(TransactionError):
    """Another cooperating updater holds the workspace lock."""


class UnsafePathError(TransactionError):
    """A target or state path crosses the declared workspace boundary."""


class JournalError(TransactionError):
    """Persistent updater state is malformed or inconsistent."""


class JournalHierarchyBindingError(JournalError):
    """A descriptor-bound journal hierarchy moved during a write."""


class ReportIntegrityError(JournalError):
    """A descriptor-bound transaction report changed during publication."""


class ThirdPartyDriftError(TransactionError):
    """A path differs from both transaction pre- and post-images."""


class UnsupportedAtomicRename(TransactionError):
    """The platform cannot publish bootstrap state without replacement."""


class InjectedFailure(TransactionError):
    """A test-only deterministic failure point fired."""


class UpdateInterrupted(TransactionError):
    """SIGINT or SIGTERM interrupted a pre-commit transaction."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mode(metadata: os.stat_result) -> str:
    return f"{stat.S_IMODE(metadata.st_mode):04o}"


def _canonical(document: Mapping[str, Any]) -> bytes:
    return contract.canonical_json_bytes(document)


def _sealed(document: Mapping[str, Any]) -> dict[str, Any]:
    return contract.seal_document(document)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    directories: list[Path] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(current)
        directories.append(directory)
        for name in filenames:
            path = directory / name
            if path.is_symlink():
                continue
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]
    for directory in reversed(directories):
        _fsync_directory(directory)


def _tree_digest(root: Path, *, ignored: Iterable[str] = ()) -> str:
    ignored_set = set(ignored)
    records: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(current)
        relative_dir = directory.relative_to(root).as_posix()
        if relative_dir != ".":
            records.append({"path": relative_dir, "type": "directory", "mode": _mode(os.lstat(directory))})
        physical_directories: list[str] = []
        for name in sorted(dirnames):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                if relative not in ignored_set:
                    records.append(
                        {
                            "path": relative,
                            "type": "symlink",
                            "target": os.readlink(path),
                        }
                    )
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise JournalError(
                    f"unsupported archive-state object: {relative}"
                )
            physical_directories.append(name)
        for name in sorted(filenames):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if relative in ignored_set:
                continue
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                records.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
            elif stat.S_ISREG(metadata.st_mode):
                records.append({"path": relative, "type": "file", "sha256": _sha256_file(path), "mode": _mode(metadata)})
            else:
                raise JournalError(f"unsupported archive-state object: {relative}")
        dirnames[:] = physical_directories
    return contract.sha256_bytes(_canonical({"records": sorted(records, key=lambda item: item["path"])}))


def _logical_state_digest(root: Path) -> str:
    """Compare state copies while excluding inode-binding transport fields."""

    records: list[dict[str, Any]] = []
    ignored = {
        ARCHIVE_TRANSITION,
        ARCHIVED_MARKER,
        ARCHIVE_REUSE_TRANSITION,
    }
    for current, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        directory = Path(current)
        relative_dir = directory.relative_to(root).as_posix()
        if relative_dir != ".":
            records.append(
                {
                    "path": relative_dir,
                    "type": "directory",
                    "mode": _mode(os.lstat(directory)),
                }
            )
        physical_directories: list[str] = []
        for name in sorted(dirnames):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                if relative not in ignored:
                    records.append(
                        {
                            "path": relative,
                            "type": "symlink",
                            "target": os.readlink(path),
                        }
                    )
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise JournalError(
                    f"unsupported archive-state object: {relative}"
                )
            physical_directories.append(name)
        for name in sorted(filenames):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if relative in ignored:
                continue
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                records.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "target": os.readlink(path),
                    }
                )
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise JournalError(
                    f"unsupported archive-state object: {relative}"
                )
            if relative == "sentinel.json" or relative.endswith("/journal.json"):
                document = _read_canonical_json(
                    path, label=f"logical state document {relative}"
                )
                normalized = dict(document)
                normalized.pop("self_digest", None)
                if relative == "sentinel.json":
                    normalized.pop("state_identity", None)
                    normalized.pop("transactions_identity", None)
                else:
                    normalized.pop("directory_bindings", None)
                digest = contract.sha256_bytes(_canonical(normalized))
            else:
                digest = _sha256_file(path)
            records.append(
                {
                    "path": relative,
                    "type": "file",
                    "sha256": digest,
                    "mode": _mode(metadata),
                }
            )
        dirnames[:] = physical_directories
    return contract.sha256_bytes(
        _canonical({"records": sorted(records, key=lambda item: item["path"])})
    )


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.urandom(8).hex()}")
    payload = _canonical(document)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _read_canonical_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise JournalError(f"{label} is not a regular file")
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise JournalError(f"invalid {label}") from exc
    if not isinstance(value, dict) or payload != _canonical(value):
        raise JournalError(f"{label} is not canonical JSON")
    try:
        contract.validate_self_digest(value)
    except contract.ContractError as exc:
        raise JournalError(f"{label} self-digest mismatch") from exc
    return value


def _directory_identity(metadata: os.stat_result) -> dict[str, int]:
    return {"device": metadata.st_dev, "inode": metadata.st_ino}


def _fd_identity(descriptor: int) -> dict[str, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise JournalError("updater local-state descriptor is not a directory")
    return _directory_identity(metadata)


@contextlib.contextmanager
def _descriptor_cwd(descriptor: int):
    """Temporarily resolve relative paths beneath one already-open directory."""

    previous = os.open(
        ".",
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fchdir(descriptor)
        yield Path(".")
    finally:
        os.fchdir(previous)
        os.close(previous)


def _atomic_json_at(
    directory_fd: int, name: str, document: Mapping[str, Any]
) -> None:
    if not name or "/" in name or name in {".", ".."}:
        raise JournalError("invalid updater local-state JSON name")
    temporary = name + f".tmp-{os.urandom(8).hex()}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        payload = _canonical(document)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def _read_canonical_json_at(
    directory_fd: int, name: str, *, label: str
) -> dict[str, Any]:
    if not name or "/" in name or name in {".", ".."}:
        raise JournalError(f"invalid {label}")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise JournalError(f"{label} is not a regular file")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            payload = b"".join(chunks)
        finally:
            os.close(descriptor)
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise JournalError(f"invalid {label}") from exc
    if not isinstance(value, dict) or payload != _canonical(value):
        raise JournalError(f"{label} is not canonical JSON")
    try:
        contract.validate_self_digest(value)
    except contract.ContractError as exc:
        raise JournalError(f"{label} self-digest mismatch") from exc
    return value


def _open_child_directory(
    parent_fd: int,
    name: str,
    *,
    expected_identity: Mapping[str, int] | None = None,
) -> int:
    if not name or "/" in name or name in {".", ".."}:
        raise JournalError("invalid updater local-state directory name")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise JournalError(
            f"updater local-state directory is missing, replaced, or unsafe: {name}"
        ) from exc
    if expected_identity is not None and _fd_identity(descriptor) != dict(
        expected_identity
    ):
        os.close(descriptor)
        raise JournalError(f"updater local-state directory identity changed: {name}")
    return descriptor


def _create_child_directory(parent_fd: int, name: str, mode: int = 0o700) -> int:
    try:
        os.mkdir(name, mode, dir_fd=parent_fd)
    except OSError as exc:
        raise JournalError(f"cannot create updater local-state directory: {name}") from exc
    descriptor = _open_child_directory(parent_fd, name)
    os.fchmod(descriptor, mode)
    os.fsync(descriptor)
    os.fsync(parent_fd)
    return descriptor


def _assert_child_binding(parent_fd: int, name: str, descriptor: int) -> None:
    current = _open_child_directory(parent_fd, name)
    try:
        if _fd_identity(current) != _fd_identity(descriptor):
            raise JournalError(
                f"updater local-state directory was replaced during use: {name}"
            )
    finally:
        os.close(current)


def _open_regular_child(parent_fd: int, name: str, *, label: str) -> int:
    if not name or "/" in name or name in {".", ".."}:
        raise JournalError(f"invalid {label}")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise JournalError(f"{label} is missing, replaced, or unsafe") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise JournalError(f"{label} is not a regular file")
    return descriptor


def _assert_regular_child_binding(
    parent_fd: int, name: str, descriptor: int, *, label: str
) -> None:
    current = _open_regular_child(parent_fd, name, label=label)
    try:
        expected = os.fstat(descriptor)
        actual = os.fstat(current)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise JournalError(f"{label} was replaced during publication")
    finally:
        os.close(current)


def _remove_tree_at(
    parent_fd: int,
    name: str,
    *,
    expected_identity: Mapping[str, int] | None = None,
) -> None:
    """Delete one bound updater-owned directory without following replacements."""

    descriptor = _open_child_directory(
        parent_fd, name, expected_identity=expected_identity
    )
    try:
        for child in os.listdir(descriptor):
            metadata = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                _remove_tree_at(
                    descriptor,
                    child,
                    expected_identity=_directory_identity(metadata),
                )
            else:
                os.unlink(child, dir_fd=descriptor)
                os.fsync(descriptor)
        _assert_child_binding(parent_fd, name, descriptor)
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(descriptor)


def _clear_directory_at(
    parent_fd: int,
    name: str,
    *,
    expected_identity: Mapping[str, int],
) -> None:
    descriptor = _open_child_directory(
        parent_fd, name, expected_identity=expected_identity
    )
    try:
        for child in os.listdir(descriptor):
            metadata = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                _remove_tree_at(
                    descriptor,
                    child,
                    expected_identity=_directory_identity(metadata),
                )
            else:
                os.unlink(child, dir_fd=descriptor)
                os.fsync(descriptor)
        _assert_child_binding(parent_fd, name, descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_physical_tree(
    directory: Path,
    *,
    label: str,
    allow_leaf_symlinks_under: Iterable[str] = (),
) -> None:
    try:
        metadata = os.lstat(directory)
    except OSError as exc:
        raise JournalError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise JournalError(f"{label} is not a physical directory")
    allowed_symlink_roots = set(allow_leaf_symlinks_under)
    for current, dirnames, filenames in os.walk(
        directory, topdown=True, followlinks=False
    ):
        parent = Path(current)
        for name in [*dirnames, *filenames]:
            path = parent / name
            child = os.lstat(path)
            if stat.S_ISLNK(child.st_mode):
                relative = path.relative_to(directory)
                if (
                    relative.parts
                    and relative.parts[0] in allowed_symlink_roots
                    and len(relative.parts) == 2
                ):
                    if name in dirnames:
                        dirnames.remove(name)
                    continue
                raise JournalError(f"{label} contains an unexpected symlink")
            if name in dirnames and not stat.S_ISDIR(child.st_mode):
                raise JournalError(f"{label} contains a non-directory parent")
            if name in filenames and not stat.S_ISREG(child.st_mode):
                raise JournalError(f"{label} contains an unsupported object")


def _validate_bundle(directory: Path) -> None:
    _validate_physical_tree(directory, label="worker bundle")
    manifest = _read_canonical_json(directory / ".digests.json", label="worker digest manifest")
    if set(manifest) != {"schema_version", "files", "self_digest"}:
        raise JournalError("worker digest manifest fields differ from its contract")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise JournalError("worker digest manifest file map is invalid")
    expected = {".digests.json", *files.keys()}
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != expected:
        raise JournalError("worker bundle contains missing or undeclared files")
    for relative, digest in files.items():
        contract.validate_relative_path(relative, field="worker path")
        contract.validate_sha256(digest, field="worker digest")
        path = directory / relative
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise JournalError(f"worker is not a regular file: {relative}")
        if _sha256_file(path) != digest:
            raise JournalError(f"worker digest mismatch: {relative}")


def _bundle_sources(directory: Path) -> dict[str, Path]:
    _validate_bundle(directory)
    manifest = _read_canonical_json(
        directory / ".digests.json", label="worker digest manifest"
    )
    return {
        relative: directory / relative
        for relative in sorted(manifest["files"])
    }


def _validate_image(raw: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if isinstance(raw, Mapping) and "type" not in raw and "state" in raw:
        raw = {"type" if key == "state" else key: value for key, value in raw.items()}
    if not isinstance(raw, Mapping) or raw.get("type") not in IMAGE_TYPES:
        raise JournalError(f"invalid {label} image")
    kind = raw["type"]
    allowed = {"type"}
    if kind == "file":
        allowed |= {"sha256", "mode"}
        contract.validate_sha256(raw.get("sha256"), field=f"{label}.sha256")
        mode = raw.get("mode")
        if not isinstance(mode, str) or re.fullmatch(r"0[0-7]{3}", mode) is None:
            raise JournalError(f"invalid {label} file mode")
    elif kind == "directory":
        allowed.add("mode")
        if raw.get("mode") != "0755":
            raise JournalError(f"invalid {label} directory mode")
    elif kind == "symlink":
        allowed |= {"target", "mode"}
        if raw.get("mode") != "0777" or not isinstance(raw.get("target"), str):
            raise JournalError(f"invalid {label} symlink")
    if set(raw) != allowed:
        raise JournalError(f"unknown fields in {label} image")
    return dict(raw)


def _observe(path: Path, relative: str) -> dict[str, Any]:
    try:
        metadata = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return {"type": "absent"}
    if stat.S_ISREG(metadata.st_mode):
        return {"type": "file", "sha256": _sha256_file(path), "mode": _mode(metadata)}
    if stat.S_ISDIR(metadata.st_mode):
        return {"type": "directory", "mode": _mode(metadata)}
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        try:
            contract.validate_symlink_target(relative, target)
        except contract.ContractError as exc:
            raise UnsafePathError(f"unsafe symlink at {relative}") from exc
        return {"type": "symlink", "target": target, "mode": "0777"}
    raise UnsafePathError(f"unsupported filesystem object at {relative}")


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _open_parent_at(
    root_fd: int,
    relative: str,
    *,
    allow_missing_parents: bool = False,
    allow_non_directory_parents: bool = False,
) -> tuple[int | None, str]:
    try:
        normalized = contract.validate_relative_path(
            relative, field="transaction target"
        )
    except contract.ContractError as exc:
        raise UnsafePathError(str(exc)) from exc
    parts = PurePosixPath(normalized).parts
    descriptor = os.dup(root_fd)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if allow_missing_parents:
                    os.close(descriptor)
                    return None, parts[-1]
                raise UnsafePathError(
                    f"missing transaction target parent: {relative}"
                )
            except OSError as exc:
                try:
                    details = os.stat(
                        part, dir_fd=descriptor, follow_symlinks=False
                    )
                except OSError:
                    details = None
                if details is not None and stat.S_ISLNK(details.st_mode):
                    raise UnsafePathError(
                        f"transaction target parent is a symlink: {relative}"
                    ) from exc
                if allow_non_directory_parents and details is not None:
                    os.close(descriptor)
                    return None, parts[-1]
                raise UnsafePathError(
                    f"transaction target parent is not a physical directory: {relative}"
                ) from exc
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _observe_at(
    root_fd: int,
    relative: str,
    *,
    allow_missing_parents: bool = False,
    allow_non_directory_parents: bool = False,
) -> dict[str, Any]:
    parent_fd, leaf = _open_parent_at(
        root_fd,
        relative,
        allow_missing_parents=allow_missing_parents,
        allow_non_directory_parents=allow_non_directory_parents,
    )
    if parent_fd is None:
        return {"type": "absent"}
    try:
        return _observe_leaf_at(parent_fd, leaf, relative)
    finally:
        os.close(parent_fd)


def _observe_leaf_at(parent_fd: int, leaf: str, relative: str) -> dict[str, Any]:
    try:
        metadata = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return {"type": "absent"}
    if stat.S_ISREG(metadata.st_mode):
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(leaf, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise ThirdPartyDriftError(
                f"transaction target changed while observed: {relative}"
            ) from exc
        try:
            pinned = os.fstat(descriptor)
            if (
                not stat.S_ISREG(pinned.st_mode)
                or (pinned.st_dev, pinned.st_ino)
                != (metadata.st_dev, metadata.st_ino)
            ):
                raise ThirdPartyDriftError(
                    f"transaction target changed while observed: {relative}"
                )
            return {
                "type": "file",
                "sha256": _sha256_fd(descriptor),
                "mode": _mode(pinned),
            }
        finally:
            os.close(descriptor)
    if stat.S_ISDIR(metadata.st_mode):
        return {"type": "directory", "mode": _mode(metadata)}
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(leaf, dir_fd=parent_fd)
        try:
            contract.validate_symlink_target(relative, target)
        except contract.ContractError as exc:
            raise UnsafePathError(f"unsafe symlink at {relative}") from exc
        return {"type": "symlink", "target": target, "mode": "0777"}
    raise UnsafePathError(f"unsupported filesystem object at {relative}")


def _read_file_at(root_fd: int, relative: str) -> bytes:
    parent_fd, leaf = _open_parent_at(root_fd, relative)
    if parent_fd is None:
        raise UnsafePathError(f"transaction file parent is absent: {relative}")
    try:
        descriptor = os.open(
            leaf,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise UnsafePathError(f"transaction target is not a file: {relative}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _ensure_parent_at(
    root_fd: int, relative: str, *, directory_mode: int = 0o700
) -> tuple[int, str]:
    try:
        normalized = contract.validate_relative_path(
            relative, field="updater local-state file"
        )
    except contract.ContractError as exc:
        raise UnsafePathError(str(exc)) from exc
    parts = PurePosixPath(normalized).parts
    descriptor = os.dup(root_fd)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for part in parts[:-1]:
            created = False
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(part, directory_mode, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=descriptor)
                if created:
                    os.fchmod(child, directory_mode)
                    os.fsync(child)
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _write_bytes_at(
    root_fd: int,
    relative: str,
    payload: bytes,
    *,
    mode: int,
) -> None:
    parent_fd, leaf = _ensure_parent_at(root_fd, relative)
    try:
        descriptor = os.open(
            leaf,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_fd,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _copy_fd_at(
    source_fd: int,
    destination_root_fd: int,
    relative: str,
    *,
    mode: int,
) -> None:
    parent_fd, leaf = _ensure_parent_at(destination_root_fd, relative)
    try:
        descriptor = os.open(
            leaf,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_fd,
        )
        try:
            os.lseek(source_fd, 0, os.SEEK_SET)
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _read_regular_source(path: Path) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePathError(f"payload is not a regular file: {path.name}")
        return descriptor, metadata
    except BaseException:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise


def _assert_parent_binding(root_fd: int, relative: str, parent_fd: int) -> None:
    current_fd, _leaf = _open_parent_at(root_fd, relative)
    if current_fd is None:
        raise ThirdPartyDriftError(
            f"transaction target parent disappeared: {relative}"
        )
    try:
        expected = os.fstat(parent_fd)
        current = os.fstat(current_fd)
        if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
            raise ThirdPartyDriftError(
                f"transaction target parent changed: {relative}"
            )
    finally:
        os.close(current_fd)


def _assert_safe_parent(
    root: Path,
    relative: str,
    *,
    allow_missing_parents: bool = False,
    allow_non_directory_parents: bool = False,
) -> Path:
    try:
        normalized = contract.validate_relative_path(relative, field="transaction target")
    except contract.ContractError as exc:
        raise UnsafePathError(str(exc)) from exc
    candidate = root / normalized
    current = root
    for part in Path(normalized).parts[:-1]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            if allow_missing_parents:
                return candidate
            raise UnsafePathError(f"missing target parent: {current.relative_to(root)}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePathError(f"target parent is not a physical directory: {current.relative_to(root)}")
        if not stat.S_ISDIR(metadata.st_mode):
            if allow_non_directory_parents:
                return candidate
            raise UnsafePathError(f"target parent is not a physical directory: {current.relative_to(root)}")
    return candidate


def _open_directory_at(root_fd: int, relative: str) -> int:
    try:
        normalized = contract.validate_relative_path(
            relative, field="updater state directory"
        )
    except contract.ContractError as exc:
        raise UnsafePathError(str(exc)) from exc
    descriptor = os.dup(root_fd)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for part in PurePosixPath(normalized).parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise UnsafePathError(
            f"updater state directory is missing, replaced, or unsafe: {relative}"
        ) from exc


@contextlib.contextmanager
def _pinned_directory_cwd(
    workspace_root: Path, directory: Path, root_fd: int
):
    try:
        relative = directory.relative_to(workspace_root).as_posix()
    except ValueError:
        relative = None
    if relative is None:
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    elif relative == ".":
        descriptor = os.dup(root_fd)
    else:
        descriptor = _open_directory_at(root_fd, relative)
    previous = os.open(
        ".",
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fchdir(descriptor)
        yield Path(".")
    finally:
        os.fchdir(previous)
        os.close(previous)
        os.close(descriptor)


def _exclusive_rename(source: Path, destination: Path) -> None:
    """Rename a directory only if *destination* does not exist.

    Darwin and Linux expose the required primitive under different names.  No
    check-then-rename fallback is safe enough for the bootstrap ownership
    sentinel, so every unsupported/error case fails closed.
    """

    if os.lstat(source).st_dev != os.lstat(destination.parent).st_dev:
        raise UnsupportedAtomicRename("exclusive rename crosses filesystems")
    system = platform.system()
    libc = ctypes.CDLL(None, use_errno=True)
    src = os.fsencode(source)
    dst = os.fsencode(destination)
    if system == "Darwin" and hasattr(libc, "renameatx_np"):
        at_fdcwd = -2
        function = libc.renameatx_np
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(at_fdcwd, src, at_fdcwd, dst, 0x00000004)  # RENAME_EXCL
    elif system == "Linux" and hasattr(libc, "renameat2"):
        at_fdcwd = -100
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(at_fdcwd, src, at_fdcwd, dst, 0x00000001)  # RENAME_NOREPLACE
    else:
        raise UnsupportedAtomicRename(f"exclusive directory rename is unsupported on {system}")
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), destination)
        if error == errno.EXDEV:
            raise UnsupportedAtomicRename("exclusive rename crosses filesystems")
        raise OSError(error, os.strerror(error), destination)
    _fsync_directory(source.parent)
    if destination.parent != source.parent:
        _fsync_directory(destination.parent)


def _exclusive_rename_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    """Descriptor-anchored no-replace directory publication."""

    if not source_name or "/" in source_name or source_name in {".", ".."}:
        raise UnsafePathError("invalid exclusive-rename source name")
    if (
        not destination_name
        or "/" in destination_name
        or destination_name in {".", ".."}
    ):
        raise UnsafePathError("invalid exclusive-rename destination name")
    if os.fstat(source_parent_fd).st_dev != os.fstat(destination_parent_fd).st_dev:
        raise UnsupportedAtomicRename("exclusive rename crosses filesystems")
    system = platform.system()
    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    if system == "Darwin" and hasattr(libc, "renameatx_np"):
        function = libc.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination,
            0x00000004,
        )
    elif system == "Linux" and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination,
            0x00000001,
        )
    else:
        raise UnsupportedAtomicRename(
            f"exclusive directory rename is unsupported on {system}"
        )
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), destination_name)
        if error == errno.EXDEV:
            raise UnsupportedAtomicRename("exclusive rename crosses filesystems")
        raise OSError(error, os.strerror(error), destination_name)
    os.fsync(source_parent_fd)
    if destination_parent_fd != source_parent_fd:
        os.fsync(destination_parent_fd)


@dataclass(frozen=True)
class Operation:
    """One exact workspace-relative pre/post transition."""

    id: str
    target_path: str
    pre: Mapping[str, Any]
    post: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Operation":
        identity = raw.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise JournalError("invalid operation id")
        target = contract.validate_relative_path(raw.get("target_path"), field="operation target")
        pre = _validate_image(raw.get("pre") or raw.get("base"), label=f"{identity}.pre")
        post = _validate_image(raw.get("post") or raw.get("new"), label=f"{identity}.post")
        return cls(identity, target, pre, post)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_path": self.target_path,
            "pre": dict(self.pre),
            "post": dict(self.post),
        }


class WorkspaceLock:
    """Advisory lock bound to the canonical workspace directory inode."""

    def __init__(
        self,
        root: Path,
        *,
        expected_identity: Mapping[str, int],
        shared: bool = False,
    ) -> None:
        self.root = root
        self.expected_identity = dict(expected_identity)
        self.shared = shared
        self.fd: int | None = None

    def __enter__(self) -> "WorkspaceLock":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(self.root, flags)
        opened = os.fstat(self.fd)
        if {
            "device": opened.st_dev,
            "inode": opened.st_ino,
        } != self.expected_identity:
            os.close(self.fd)
            self.fd = None
            raise UnsafePathError("workspace root identity changed before lock acquisition")
        operation = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
        try:
            fcntl.flock(self.fd, operation | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise ConcurrentUpdateError("another BUGate updater holds the workspace lock") from exc
        return self

    def make_inheritable(self) -> int:
        if self.fd is None:
            raise TransactionError("workspace lock is not held")
        os.set_inheritable(self.fd, True)
        return self.fd

    def __exit__(self, *_args: object) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


class TransactionManager:
    """Execute planner-authorized operations under one durable journal."""

    def __init__(
        self,
        root: Path | str,
        vendor_dir: str = ".bugate",
        *,
        injector: Callable[[str], None] | None = None,
    ) -> None:
        expanded = Path(root).expanduser()
        try:
            supplied_metadata = os.lstat(expanded)
        except OSError as exc:
            raise UnsafePathError("workspace root is missing or unreadable") from exc
        if stat.S_ISLNK(supplied_metadata.st_mode):
            raise UnsafePathError("workspace root must not be a symlink")
        resolved = expanded.resolve(strict=True)
        metadata = os.lstat(resolved)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise UnsafePathError("workspace root must be a physical directory")
        self.root = resolved
        self.vendor_dir = contract.validate_vendor_dir(vendor_dir)
        self.root_identity = {"device": metadata.st_dev, "inode": metadata.st_ino}
        self.state = self.root / ROOT_STATE
        self.prelock = self.root / self.vendor_dir / "plan.lock"
        self.injector = injector

    def workspace_lock(self, *, shared: bool = False) -> WorkspaceLock:
        return WorkspaceLock(
            self.root,
            expected_identity=self.root_identity,
            shared=shared,
        )

    def _assert_state_binding(
        self, state: Path, root_fd: int, state_fd: int
    ) -> None:
        try:
            relative = state.relative_to(self.root).as_posix()
        except ValueError:
            parent_fd = os.open(
                state.parent,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                _assert_child_binding(parent_fd, state.name, state_fd)
            finally:
                os.close(parent_fd)
            return
        current = _open_directory_at(root_fd, relative)
        try:
            if _fd_identity(current) != _fd_identity(state_fd):
                raise JournalError("updater state directory changed during use")
        finally:
            os.close(current)

    def _inject(self, name: str) -> None:
        if self.injector is not None:
            self.injector(name)
        if os.environ.get("BUGATE_UPDATE_FAILPOINT") == name:
            raise InjectedFailure(f"injected failure: {name}")
        if os.environ.get("BUGATE_UPDATE_PAUSEPOINT") == name:
            signal.pause()
        if os.environ.get("BUGATE_UPDATE_CRASHPOINT") == name:
            os._exit(97)

    def _sentinel(
        self,
        state_identity: Mapping[str, int],
        transactions_identity: Mapping[str, int],
    ) -> dict[str, Any]:
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": SENTINEL_KIND,
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "state_identity": dict(state_identity),
                "transactions_identity": dict(transactions_identity),
            }
        )

    def _archive_transition_marker(self) -> dict[str, Any]:
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "legacy-rollback-state-archive",
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
            }
        )

    def _archive_marker(self, state_digest: str) -> dict[str, Any]:
        try:
            contract.validate_sha256(
                state_digest, field="archived state digest"
            )
        except contract.ContractError as exc:
            raise JournalError("archived state digest is invalid") from exc
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "legacy-rollback-state-archive",
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "state_digest": state_digest,
            }
        )

    def _archive_reuse_transition(
        self,
        transaction_id: str,
        archive_digest: str,
        phase: str,
    ) -> dict[str, Any]:
        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid archive reuse transaction id")
        try:
            contract.validate_sha256(
                archive_digest, field="archive reuse digest"
            )
        except contract.ContractError as exc:
            raise JournalError("archive reuse digest is invalid") from exc
        if phase not in ARCHIVE_REUSE_PHASES:
            raise JournalError("archive reuse phase is invalid")
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "bootstrap-archive-reuse-transition",
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "transaction_id": transaction_id,
                "archive_digest": archive_digest,
                "phase": phase,
            }
        )

    def _rollback_archive_transition(
        self, transaction_id: str, source_transaction: str
    ) -> dict[str, Any]:
        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid rollback archive transaction id")
        if not TRANSACTION_ID_RE.fullmatch(source_transaction):
            raise JournalError("invalid rollback archive source transaction id")
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "legacy-rollback-archive-transition",
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "transaction_id": transaction_id,
                "source_transaction": source_transaction,
            }
        )

    def _validate_archive_transition(
        self, *, state_fd: int | None = None
    ) -> dict[str, Any]:
        marker = (
            _read_canonical_json_at(
                state_fd,
                ARCHIVE_TRANSITION,
                label="rollback archive transition marker",
            )
            if state_fd is not None
            else _read_canonical_json(
                self.state / ARCHIVE_TRANSITION,
                label="rollback archive transition marker",
            )
        )
        if marker == self._archive_transition_marker():
            return marker
        transaction_id = marker.get("transaction_id")
        source_transaction = marker.get("source_transaction")
        if not isinstance(transaction_id, str) or not isinstance(
            source_transaction, str
        ):
            raise JournalError("rollback archive transition identity is invalid")
        if marker != self._rollback_archive_transition(
            transaction_id, source_transaction
        ):
            raise JournalError("rollback archive transition marker is invalid")
        return marker

    def _clear_archive_transition(self, *, root_fd: int | None = None) -> None:
        with self._open_state_handles(self.state, root_fd=root_fd) as (
            state_fd,
            _transactions_fd,
        ):
            self._validate_archive_transition(state_fd=state_fd)
            os.unlink(ARCHIVE_TRANSITION, dir_fd=state_fd)
            os.fsync(state_fd)

    def _report_metadata(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(fields, Mapping) or not set(fields).issubset(
            REPORT_METADATA_FIELDS
        ):
            raise JournalError("transaction report metadata fields are invalid")
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "transaction-report-metadata",
                "fields": dict(fields),
            }
        )

    def _load_report_metadata(
        self,
        tx: Path,
        *,
        transaction_fd: int | None = None,
        directory_bindings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if transaction_fd is not None:
            if directory_bindings is None:
                raise JournalError("transaction report metadata lacks directory bindings")
            input_fd = _open_child_directory(
                transaction_fd,
                "input",
                expected_identity=directory_bindings["local_directories"]["input"],
            )
            try:
                try:
                    metadata = os.stat(
                        "report-metadata.json",
                        dir_fd=input_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    return {}
                if not stat.S_ISREG(metadata.st_mode):
                    raise JournalError(
                        "transaction report metadata is not a regular file"
                    )
                document = _read_canonical_json_at(
                    input_fd,
                    "report-metadata.json",
                    label="transaction report metadata",
                )
            finally:
                os.close(input_fd)
        else:
            path = tx / "input" / "report-metadata.json"
            if not (path.exists() or path.is_symlink()):
                return {}
            document = _read_canonical_json(
                path, label="transaction report metadata"
            )
        fields = document.get("fields")
        if (
            set(document) != {"schema_version", "kind", "fields", "self_digest"}
            or document.get("schema_version") != STATE_SCHEMA
            or document.get("kind") != "transaction-report-metadata"
            or not isinstance(fields, Mapping)
            or not set(fields).issubset(REPORT_METADATA_FIELDS)
            or document != self._report_metadata(fields)
        ):
            raise JournalError("transaction report metadata differs from its contract")
        return dict(fields)

    def _bootstrap_transition(
        self, transaction_id: str, phase: str = "published"
    ) -> dict[str, Any]:
        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid bootstrap transition transaction id")
        if phase not in BOOTSTRAP_PHASES:
            raise JournalError("invalid bootstrap transition phase")
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "bootstrap-root-state-transition",
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "transaction_id": transaction_id,
                "phase": phase,
            }
        )

    def _bootstrap_transition_document(
        self, *, state_fd: int | None = None
    ) -> dict[str, Any]:
        marker = (
            _read_canonical_json_at(
                state_fd,
                BOOTSTRAP_TRANSITION,
                label="bootstrap transition marker",
            )
            if state_fd is not None
            else _read_canonical_json(
                self.state / BOOTSTRAP_TRANSITION,
                label="bootstrap transition marker",
            )
        )
        transaction_id = marker.get("transaction_id")
        phase = marker.get("phase")
        if not isinstance(transaction_id, str) or not isinstance(phase, str):
            raise JournalError("bootstrap transition identity is invalid")
        if marker != self._bootstrap_transition(transaction_id, phase):
            raise JournalError("bootstrap transition marker is invalid")
        return marker

    def _validate_bootstrap_transition(
        self, *, state_fd: int | None = None
    ) -> str:
        return self._bootstrap_transition_document(
            state_fd=state_fd
        )["transaction_id"]

    def _bootstrap_return_marker(
        self,
        transaction_id: str,
        plan_lock_identity: Mapping[str, int],
    ) -> dict[str, Any]:
        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid bootstrap return transaction id")
        if (
            set(plan_lock_identity) != {"device", "inode"}
            or not all(
                isinstance(value, int)
                for value in plan_lock_identity.values()
            )
        ):
            raise JournalError("invalid bootstrap return directory identity")
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "kind": "bootstrap-return-plan-lock",
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "transaction_id": transaction_id,
                "plan_lock_identity": dict(plan_lock_identity),
            }
        )

    def _validate_bootstrap_return_plan_lock_at(
        self,
        plan_lock_fd: int,
        transaction_id: str,
        *,
        archived: bool,
    ) -> None:
        marker = _read_canonical_json_at(
            plan_lock_fd,
            BOOTSTRAP_RETURN_MARKER,
            label="bootstrap return plan.lock marker",
        )
        if marker != self._bootstrap_return_marker(
            transaction_id, _fd_identity(plan_lock_fd)
        ):
            raise JournalError("bootstrap return plan.lock marker is invalid")
        expected = {BOOTSTRAP_RETURN_MARKER}
        if archived:
            expected.add(BOOTSTRAP_CHILD)
        if set(os.listdir(plan_lock_fd)) != expected:
            raise JournalError(
                "bootstrap return plan.lock contains non-updater entries"
            )

    @contextlib.contextmanager
    def _open_bootstrap_return_plan_lock(
        self,
        transaction_id: str,
        *,
        archived: bool,
        root_fd: int | None = None,
    ):
        close_root = False
        if root_fd is None:
            root_fd = os.open(
                self.root,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            close_root = True
            if _fd_identity(root_fd) != self.root_identity:
                os.close(root_fd)
                raise UnsafePathError(
                    "workspace root identity changed during bootstrap recovery"
                )
        parent_fd = None
        plan_lock_fd = None
        try:
            parent_fd, leaf = _open_parent_at(
                root_fd, f"{self.vendor_dir}/plan.lock"
            )
            assert parent_fd is not None
            plan_lock_fd = _open_child_directory(parent_fd, leaf)
            self._validate_bootstrap_return_plan_lock_at(
                plan_lock_fd, transaction_id, archived=archived
            )
            _assert_child_binding(parent_fd, leaf, plan_lock_fd)
            yield parent_fd, leaf, plan_lock_fd
        finally:
            if plan_lock_fd is not None:
                os.close(plan_lock_fd)
            if parent_fd is not None:
                os.close(parent_fd)
            if close_root:
                os.close(root_fd)

    def _publish_bootstrap_return_plan_lock(
        self, transaction_id: str, *, root_fd: int
    ) -> None:
        parent_fd, leaf = _open_parent_at(
            root_fd, f"{self.vendor_dir}/plan.lock"
        )
        assert parent_fd is not None
        try:
            try:
                existing = os.stat(
                    leaf, dir_fd=parent_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISDIR(existing.st_mode):
                    raise JournalError(
                        "bootstrap return plan.lock target is unsafe"
                    )
                existing_fd = _open_child_directory(parent_fd, leaf)
                try:
                    self._validate_bootstrap_return_plan_lock_at(
                        existing_fd, transaction_id, archived=False
                    )
                finally:
                    os.close(existing_fd)
                return

            raw = Path(
                tempfile.mkdtemp(
                    prefix=f".{self.root.name}.bugate-return-",
                    dir=self.root.parent,
                )
            )
            raw_parent_fd = os.open(
                self.root.parent,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            raw_fd = _open_child_directory(raw_parent_fd, raw.name)
            os.fchmod(raw_fd, 0o700)
            os.fsync(raw_fd)
            raw_identity = _fd_identity(raw_fd)
            plan_lock_fd = None
            try:
                plan_lock_fd = _create_child_directory(
                    raw_fd, "plan.lock", 0o700
                )
                _atomic_json_at(
                    plan_lock_fd,
                    BOOTSTRAP_RETURN_MARKER,
                    self._bootstrap_return_marker(
                        transaction_id, _fd_identity(plan_lock_fd)
                    ),
                )
                self._validate_bootstrap_return_plan_lock_at(
                    plan_lock_fd, transaction_id, archived=False
                )
                _exclusive_rename_at(
                    raw_fd, "plan.lock", parent_fd, leaf
                )
                _assert_child_binding(parent_fd, leaf, plan_lock_fd)
            finally:
                if plan_lock_fd is not None:
                    os.close(plan_lock_fd)
                try:
                    _remove_tree_at(
                        raw_parent_fd,
                        raw.name,
                        expected_identity=raw_identity,
                    )
                finally:
                    os.close(raw_fd)
                    os.close(raw_parent_fd)
            with self._open_bootstrap_return_plan_lock(
                transaction_id, archived=False, root_fd=root_fd
            ):
                pass
        finally:
            os.close(parent_fd)

    def _cleanup_bootstrap_transition(
        self,
        transaction_id: str,
        *,
        root_fd: int | None = None,
        retain_transition: bool = False,
    ) -> None:
        close_root = False
        if root_fd is None:
            root_fd = os.open(
                self.root,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            close_root = True
        try:
            with self._open_state_handles(self.state, root_fd=root_fd) as (
                state_fd,
                _transactions_fd,
            ):
                bootstrap = self._bootstrap_transition_document(
                    state_fd=state_fd
                )
                if bootstrap["transaction_id"] != transaction_id:
                    raise JournalError("bootstrap transition identity mismatch")
                phase = bootstrap["phase"]
                if retain_transition and phase == "published":
                    _atomic_json_at(
                        state_fd,
                        BOOTSTRAP_TRANSITION,
                        self._bootstrap_transition(
                            transaction_id, "retiring-plan-lock"
                        ),
                    )
                    phase = "retiring-plan-lock"
                parent_fd, leaf = _open_parent_at(
                    root_fd,
                    f"{self.vendor_dir}/plan.lock",
                    allow_missing_parents=True,
                )
                if parent_fd is not None:
                    try:
                        try:
                            metadata = os.stat(
                                leaf,
                                dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            metadata = None
                        if metadata is not None:
                            if phase == "plan-lock-retired":
                                raise JournalError(
                                    "operator plan.lock appeared after bootstrap retirement"
                                )
                            if not stat.S_ISDIR(metadata.st_mode):
                                raise JournalError(
                                    "bootstrap plan.lock cleanup target is unsafe"
                                )
                            prelock_fd = _open_child_directory(parent_fd, leaf)
                            try:
                                if os.listdir(prelock_fd):
                                    raise JournalError(
                                        "bootstrap plan.lock cleanup target is not empty"
                                    )
                                _assert_child_binding(parent_fd, leaf, prelock_fd)
                                os.rmdir(leaf, dir_fd=parent_fd)
                                os.fsync(parent_fd)
                            finally:
                                os.close(prelock_fd)
                    finally:
                        os.close(parent_fd)
                if retain_transition:
                    if phase != "plan-lock-retired":
                        _atomic_json_at(
                            state_fd,
                            BOOTSTRAP_TRANSITION,
                            self._bootstrap_transition(
                                transaction_id, "plan-lock-retired"
                            ),
                        )
                    return
                try:
                    reuse_metadata = os.stat(
                        ARCHIVE_REUSE_TRANSITION,
                        dir_fd=state_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    reuse_metadata = None
                if reuse_metadata is not None:
                    if not stat.S_ISREG(reuse_metadata.st_mode):
                        raise JournalError(
                            "bootstrap archive reuse transition is unsafe"
                        )
                    reuse = self._validate_archive_reuse_transition_at(
                        state_fd
                    )
                    if reuse["transaction_id"] != transaction_id:
                        raise JournalError(
                            "bootstrap archive reuse identity mismatch"
                        )
                    os.unlink(ARCHIVE_REUSE_TRANSITION, dir_fd=state_fd)
                    os.fsync(state_fd)
                os.unlink(BOOTSTRAP_TRANSITION, dir_fd=state_fd)
                os.fsync(state_fd)
        finally:
            if close_root:
                os.close(root_fd)

    def _archive_recovered_bootstrap_state(
        self, transaction_id: str, *, root_fd: int | None = None
    ) -> None:
        """Return a failed bootstrap's diagnostics to the legacy ignored tree.

        Once the root-state rename has happened, recovery can restore the old
        marked ``.gitignore`` bytes.  Leaving ``.bugate-update`` at the root
        after that restore would expose updater state that the restored ignore
        block does not cover.  The bootstrap marker proves ownership of the
        root state and the still-empty ``plan.lock`` parent; publish an archive
        marker before the no-replace move so every crash cut remains
        recognizable and reusable by a later bootstrap.
        """

        close_root = False
        if root_fd is None:
            root_fd = os.open(
                self.root,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            close_root = True
        try:
            with self._open_state_handles(self.state, root_fd=root_fd) as (
                state_fd,
                transactions_fd,
            ):
                bootstrap = self._bootstrap_transition_document(
                    state_fd=state_fd
                )
                if bootstrap["transaction_id"] != transaction_id:
                    raise JournalError("bootstrap transition identity mismatch")
                bootstrap_phase = bootstrap["phase"]
                if self._current(
                    self.state, state_fd=state_fd, root_fd=root_fd
                ) is not None:
                    raise JournalError(
                        "cannot archive an active recovered bootstrap"
                    )
                with self._open_transaction_handles(
                    self.state, transaction_id, root_fd=root_fd
                ) as (
                    _opened_state_fd,
                    _opened_transactions_fd,
                    _transaction_fd,
                    _anchored_tx,
                    journal,
                    _operations,
                ):
                    if journal["status"] != "recovered":
                        raise JournalError(
                            "only a recovered bootstrap can return to plan.lock"
                        )
                try:
                    reuse_metadata = os.stat(
                        ARCHIVE_REUSE_TRANSITION,
                        dir_fd=state_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    reuse_metadata = None
                if reuse_metadata is not None:
                    if not stat.S_ISREG(reuse_metadata.st_mode):
                        raise JournalError(
                            "bootstrap archive reuse transition is unsafe"
                        )
                    reuse = self._validate_archive_reuse_transition_at(
                        state_fd
                    )
                    if reuse["transaction_id"] != transaction_id:
                        raise JournalError(
                            "bootstrap archive reuse identity mismatch"
                        )
                    os.unlink(ARCHIVE_REUSE_TRANSITION, dir_fd=state_fd)
                    os.fsync(state_fd)
                if bootstrap_phase in {"published", "retiring-plan-lock"}:
                    self._cleanup_bootstrap_transition(
                        transaction_id,
                        root_fd=root_fd,
                        retain_transition=True,
                    )
                    bootstrap = self._bootstrap_transition_document(
                        state_fd=state_fd
                    )
                    bootstrap_phase = bootstrap["phase"]
                if bootstrap_phase not in {
                    "plan-lock-retired",
                    "returning-to-plan-lock",
                }:
                    raise JournalError(
                        "recovered bootstrap is not ready to return to plan.lock"
                    )
                if bootstrap_phase == "plan-lock-retired":
                    parent_fd, leaf = _open_parent_at(
                        root_fd, f"{self.vendor_dir}/plan.lock"
                    )
                    assert parent_fd is not None
                    try:
                        try:
                            os.stat(
                                leaf,
                                dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            pass
                        else:
                            raise JournalError(
                                "operator plan.lock appeared after bootstrap retirement"
                            )
                    finally:
                        os.close(parent_fd)
                    _atomic_json_at(
                        state_fd,
                        BOOTSTRAP_TRANSITION,
                        self._bootstrap_transition(
                            transaction_id,
                            "returning-to-plan-lock",
                        ),
                    )
                    bootstrap_phase = "returning-to-plan-lock"
                self._publish_bootstrap_return_plan_lock(
                    transaction_id, root_fd=root_fd
                )
                self._inject("after_recovery_plan_lock_publish")
                with self._open_bootstrap_return_plan_lock(
                    transaction_id,
                    archived=False,
                    root_fd=root_fd,
                ) as (parent_fd, leaf, prelock_fd):
                    self._publish_archive_marker_open(
                        self.state, state_fd, transactions_fd
                    )
                    self._inject("after_recovery_archive_marker")
                    _assert_child_binding(root_fd, ROOT_STATE, state_fd)
                    _assert_child_binding(parent_fd, leaf, prelock_fd)
                    _exclusive_rename_at(
                        root_fd,
                        ROOT_STATE,
                        prelock_fd,
                        BOOTSTRAP_CHILD,
                    )
                    self._inject("after_recovery_state_archive_publish")
                    self._validate_bootstrap_return_plan_lock_at(
                        prelock_fd, transaction_id, archived=True
                    )
                    os.unlink(
                        BOOTSTRAP_RETURN_MARKER, dir_fd=prelock_fd
                    )
                    os.fsync(prelock_fd)
                    _assert_child_binding(
                        prelock_fd, BOOTSTRAP_CHILD, state_fd
                    )
        finally:
            if close_root:
                os.close(root_fd)

    def _cleanup_bootstrap_return_marker(
        self, transaction_id: str, *, root_fd: int
    ) -> None:
        archived_state = self.prelock / BOOTSTRAP_CHILD
        with self._open_bootstrap_return_plan_lock(
            transaction_id, archived=True, root_fd=root_fd
        ) as (_parent_fd, _leaf, plan_lock_fd):
            with self._open_state_handles(
                archived_state, root_fd=root_fd
            ) as (state_fd, transactions_fd):
                bootstrap = self._bootstrap_transition_document(
                    state_fd=state_fd
                )
                if (
                    bootstrap["transaction_id"] != transaction_id
                    or bootstrap["phase"] != "returning-to-plan-lock"
                ):
                    raise JournalError(
                        "archived bootstrap return transition is invalid"
                    )
                if self._current(
                    archived_state,
                    state_fd=state_fd,
                    root_fd=root_fd,
                ) is not None:
                    raise JournalError(
                        "archived bootstrap return still has an active transaction"
                    )
                self._validate_archive_marker(
                    archived_state,
                    state_fd=state_fd,
                    transactions_fd=transactions_fd,
                )
                _assert_child_binding(
                    plan_lock_fd, BOOTSTRAP_CHILD, state_fd
                )
            os.unlink(BOOTSTRAP_RETURN_MARKER, dir_fd=plan_lock_fd)
            os.fsync(plan_lock_fd)

    def _finish_bootstrap_transition(
        self,
        transaction_id: str,
        transaction_status: str,
        *,
        root_fd: int | None = None,
    ) -> None:
        if transaction_status == "committed":
            self._cleanup_bootstrap_transition(
                transaction_id, root_fd=root_fd
            )
            return
        if transaction_status == "recovered":
            self._archive_recovered_bootstrap_state(
                transaction_id, root_fd=root_fd
            )
            return
        raise JournalError(
            f"bootstrap transition ended in unsupported state: {transaction_status}"
        )

    def _settle_bootstrap_execution(
        self, transaction_id: str, *, root_fd: int
    ) -> str:
        current = self._current(self.state, root_fd=root_fd)
        if current is not None:
            if current != transaction_id:
                raise JournalError(
                    "bootstrap settlement/current identity mismatch"
                )
            tx = self._transaction_path(self.state, transaction_id)
            journal = self._recover_transaction(
                self.state, tx, root_fd=root_fd
            )
            status = journal["status"]
        else:
            with self._open_transaction_handles(
                self.state, transaction_id, root_fd=root_fd
            ) as (
                _state_fd,
                _transactions_fd,
                _transaction_fd,
                _anchored_tx,
                journal,
                _operations,
            ):
                status = journal["status"]
        if status not in {"committed", "recovered"}:
            raise JournalError(
                "bootstrap execution did not reach a terminal journal state"
            )
        self._finish_bootstrap_transition(
            transaction_id, status, root_fd=root_fd
        )
        return status

    def _remove_recovered_prelock_state(
        self, *, root_fd: int | None = None
    ) -> None:
        state = self.prelock / BOOTSTRAP_CHILD
        close_root = False
        if root_fd is None:
            root_fd = os.open(
                self.root,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            close_root = True
        try:
            state_identity = self._validate_state_dir(state, root_fd=root_fd)[
                "state"
            ]
            parent_fd, leaf = _open_parent_at(
                root_fd, f"{self.vendor_dir}/plan.lock"
            )
            assert parent_fd is not None
            try:
                prelock_fd = _open_child_directory(parent_fd, leaf)
                try:
                    if set(os.listdir(prelock_fd)) != {BOOTSTRAP_CHILD}:
                        raise JournalError(
                            "recovered plan.lock contains non-updater entries; cleanup refused"
                        )
                    _remove_tree_at(
                        prelock_fd,
                        BOOTSTRAP_CHILD,
                        expected_identity=state_identity,
                    )
                    _assert_child_binding(parent_fd, leaf, prelock_fd)
                    os.rmdir(leaf, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                finally:
                    os.close(prelock_fd)
            finally:
                os.close(parent_fd)
        finally:
            if close_root:
                os.close(root_fd)

    def _logical_state_digest_at(self, state_fd: int) -> str:
        with _descriptor_cwd(state_fd) as anchored:
            return _logical_state_digest(anchored)

    def _read_archive_marker_at(self, state_fd: int) -> dict[str, Any]:
        marker = _read_canonical_json_at(
            state_fd, ARCHIVED_MARKER, label="rollback archive marker"
        )
        state_digest = marker.get("state_digest")
        if not isinstance(state_digest, str) or marker != self._archive_marker(
            state_digest
        ):
            raise JournalError(
                "rollback archive marker belongs to another workspace or lacks its tree digest"
            )
        return marker

    def _validate_archive_reuse_transition_at(
        self, state_fd: int
    ) -> dict[str, Any]:
        transition = _read_canonical_json_at(
            state_fd,
            ARCHIVE_REUSE_TRANSITION,
            label="archive reuse transition",
        )
        transaction_id = transition.get("transaction_id")
        archive_digest = transition.get("archive_digest")
        phase = transition.get("phase")
        if (
            not isinstance(transaction_id, str)
            or not isinstance(archive_digest, str)
            or not isinstance(phase, str)
            or transition
            != self._archive_reuse_transition(
                transaction_id, archive_digest, phase
            )
        ):
            raise JournalError("archive reuse transition is invalid")
        return transition

    def _validate_archived_history_open(
        self,
        state: Path,
        state_fd: int,
        transactions_fd: int,
    ) -> None:
        if self._current(state, state_fd=state_fd) is not None:
            raise JournalError("rollback archive still has an active transaction")
        transaction_ids = sorted(os.listdir(transactions_fd))
        if not transaction_ids:
            raise JournalError("rollback archive has no transaction history")
        for transaction_id in transaction_ids:
            if not TRANSACTION_ID_RE.fullmatch(transaction_id):
                raise JournalError(
                    "rollback archive contains an invalid transaction directory"
                )
            transaction_fd = _open_child_directory(
                transactions_fd, transaction_id
            )
            try:
                with _descriptor_cwd(transaction_fd) as anchored:
                    journal, operations = self._validate_journal(
                        anchored,
                        transaction_fd=transaction_fd,
                        state_fd=state_fd,
                        transactions_fd=transactions_fd,
                        expected_transaction_id=transaction_id,
                    )
                if journal["status"] not in {"committed", "recovered"}:
                    raise JournalError(
                        "idle rollback archive contains a non-terminal transaction"
                    )
                bindings = journal["directory_bindings"]
                for bundle_name in ("worker", "input"):
                    bundle_fd = _open_child_directory(
                        transaction_fd,
                        bundle_name,
                        expected_identity=bindings["local_directories"][
                            bundle_name
                        ],
                    )
                    try:
                        with _descriptor_cwd(bundle_fd) as anchored_bundle:
                            _validate_bundle(anchored_bundle)
                    finally:
                        os.close(bundle_fd)
                backup_fd = _open_child_directory(
                    transaction_fd,
                    "backup",
                    expected_identity=bindings["local_directories"]["backup"],
                )
                try:
                    expected_backups = {
                        f"{index:06d}"
                        for index, operation in enumerate(operations)
                        if operation.pre["type"] == "file"
                    }
                    if set(os.listdir(backup_fd)) != expected_backups:
                        raise JournalError(
                            "rollback archive backup set is incomplete"
                        )
                    for index, operation in enumerate(operations):
                        if operation.pre["type"] != "file":
                            continue
                        name = f"{index:06d}"
                        if _observe_leaf_at(backup_fd, name, name) != dict(
                            operation.pre
                        ):
                            raise JournalError(
                                "rollback archive backup differs from its journal"
                            )
                finally:
                    os.close(backup_fd)
                expected_report = (
                    "report.json"
                    if journal["status"] == "committed"
                    else "failure-report.json"
                )
                expected_top_level = {
                    *LOCAL_TRANSACTION_DIRECTORIES,
                    "journal.json",
                    expected_report,
                }
                if set(os.listdir(transaction_fd)) != expected_top_level:
                    raise JournalError(
                        "rollback archive transaction files are incomplete"
                    )
                self._validate_transaction_reports(
                    transaction_fd, journal, active=False
                )
                _assert_child_binding(
                    transactions_fd, transaction_id, transaction_fd
                )
            finally:
                os.close(transaction_fd)
        _assert_child_binding(state_fd, "transactions", transactions_fd)

    def _validate_archive_marker(
        self,
        state: Path,
        *,
        state_fd: int | None = None,
        transactions_fd: int | None = None,
    ) -> str:
        if state_fd is None:
            with self._open_state_handles(state) as (
                opened_state_fd,
                opened_transactions_fd,
            ):
                return self._validate_archive_marker(
                    state,
                    state_fd=opened_state_fd,
                    transactions_fd=opened_transactions_fd,
                )
        close_transactions = False
        if transactions_fd is None:
            transactions_fd = _open_child_directory(state_fd, "transactions")
            close_transactions = True
        try:
            for transition_name in (
                ARCHIVE_TRANSITION,
                ARCHIVE_REUSE_TRANSITION,
            ):
                try:
                    transition_metadata = os.stat(
                        transition_name,
                        dir_fd=state_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    transition_metadata = None
                if transition_metadata is not None:
                    raise JournalError(
                        "rollback archive has an active transition"
                    )
            marker = self._read_archive_marker_at(state_fd)
            self._validate_archived_history_open(
                state, state_fd, transactions_fd
            )
            actual_digest = self._logical_state_digest_at(state_fd)
            if marker["state_digest"] != actual_digest:
                raise JournalError("rollback archive tree digest mismatch")
            return actual_digest
        finally:
            if close_transactions:
                os.close(transactions_fd)

    def _publish_archive_marker_open(
        self,
        state: Path,
        state_fd: int,
        transactions_fd: int,
    ) -> str:
        self._validate_archived_history_open(
            state, state_fd, transactions_fd
        )
        state_digest = self._logical_state_digest_at(state_fd)
        _atomic_json_at(
            state_fd,
            ARCHIVED_MARKER,
            self._archive_marker(state_digest),
        )
        return self._validate_archive_marker(
            state,
            state_fd=state_fd,
            transactions_fd=transactions_fd,
        )

    def _begin_archive_reuse_open(
        self,
        state: Path,
        state_fd: int,
        transactions_fd: int,
        transaction_id: str,
    ) -> str:
        archive_digest = self._validate_archive_marker(
            state,
            state_fd=state_fd,
            transactions_fd=transactions_fd,
        )
        # Capacity is part of the public bootstrap preflight.  Check it before
        # publishing the archive-reuse intent so a full history is an exact
        # zero-write rejection rather than a marker write followed by cleanup.
        self._require_transaction_capacity(transactions_fd)
        try:
            os.stat(
                transaction_id,
                dir_fd=transactions_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise JournalError(
                "archive reuse transaction id already exists"
            )
        _atomic_json_at(
            state_fd,
            ARCHIVE_REUSE_TRANSITION,
            self._archive_reuse_transition(
                transaction_id, archive_digest, "preparing"
            ),
        )
        return archive_digest

    def _activate_archive_reuse_open(
        self,
        state_fd: int,
        transaction_id: str,
    ) -> None:
        transition = self._validate_archive_reuse_transition_at(state_fd)
        if (
            transition["transaction_id"] != transaction_id
            or transition["phase"] != "preparing"
        ):
            raise JournalError("archive reuse preparation identity mismatch")
        if self._read_archive_marker_at(state_fd)["state_digest"] != transition[
            "archive_digest"
        ]:
            raise JournalError("archive reuse baseline marker changed")
        _atomic_json_at(
            state_fd,
            ARCHIVE_REUSE_TRANSITION,
            self._archive_reuse_transition(
                transaction_id,
                transition["archive_digest"],
                "active",
            ),
        )
        os.unlink(ARCHIVED_MARKER, dir_fd=state_fd)
        os.fsync(state_fd)

    def _cancel_archive_reuse_intent(
        self, state: Path, *, root_fd: int
    ) -> None:
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            transition = self._validate_archive_reuse_transition_at(state_fd)
            if transition["phase"] != "preparing":
                raise JournalError("active archive reuse cannot be cancelled")
            if self._current(state, state_fd=state_fd, root_fd=root_fd) is not None:
                raise JournalError(
                    "prepared archive reuse must be recovered, not cancelled"
                )
            try:
                os.stat(
                    transition["transaction_id"],
                    dir_fd=transactions_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise JournalError(
                    "partial archive reuse transaction requires fail-closed inspection"
                )
            marker = self._read_archive_marker_at(state_fd)
            if (
                marker["state_digest"] != transition["archive_digest"]
                or self._logical_state_digest_at(state_fd)
                != transition["archive_digest"]
            ):
                raise JournalError("archive reuse baseline changed during prepare")
            os.unlink(ARCHIVE_REUSE_TRANSITION, dir_fd=state_fd)
            os.fsync(state_fd)
            self._validate_archive_marker(
                state,
                state_fd=state_fd,
                transactions_fd=transactions_fd,
            )

    def _finalize_archive_reuse(
        self, state: Path, *, root_fd: int
    ) -> str:
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            transition = self._validate_archive_reuse_transition_at(state_fd)
            if self._current(
                state, state_fd=state_fd, root_fd=root_fd
            ) is not None:
                raise JournalError("cannot finalize an active archive reuse")
            transaction_id = transition["transaction_id"]
            transaction_fd = _open_child_directory(
                transactions_fd, transaction_id
            )
            try:
                with _descriptor_cwd(transaction_fd) as anchored:
                    journal, _operations = self._validate_journal(
                        anchored,
                        transaction_fd=transaction_fd,
                        state_fd=state_fd,
                        transactions_fd=transactions_fd,
                        expected_transaction_id=transaction_id,
                    )
                if journal["status"] != "recovered":
                    raise JournalError(
                        "archive reuse did not recover its bootstrap transaction"
                    )
            finally:
                os.close(transaction_fd)
            if transition["phase"] != "finalizing":
                _atomic_json_at(
                    state_fd,
                    ARCHIVE_REUSE_TRANSITION,
                    self._archive_reuse_transition(
                        transaction_id,
                        transition["archive_digest"],
                        "finalizing",
                    ),
                )
            self._validate_archived_history_open(
                state, state_fd, transactions_fd
            )
            state_digest = self._logical_state_digest_at(state_fd)
            _atomic_json_at(
                state_fd,
                ARCHIVED_MARKER,
                self._archive_marker(state_digest),
            )
            if (
                self._read_archive_marker_at(state_fd)["state_digest"]
                != state_digest
                or self._logical_state_digest_at(state_fd) != state_digest
            ):
                raise JournalError(
                    "archive reuse final marker does not bind its history"
                )
            self._inject("after_archive_reuse_final_marker")
            os.unlink(ARCHIVE_REUSE_TRANSITION, dir_fd=state_fd)
            os.fsync(state_fd)
            return self._validate_archive_marker(
                state,
                state_fd=state_fd,
                transactions_fd=transactions_fd,
            )

    @contextlib.contextmanager
    def _open_state_handles(
        self, state: Path, *, root_fd: int | None = None
    ):
        if root_fd is not None:
            try:
                relative = state.relative_to(self.root).as_posix()
            except ValueError:
                relative = None
            if relative is not None:
                state_fd = _open_directory_at(root_fd, relative)
            else:
                try:
                    state_fd = os.open(
                        state,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                except OSError as exc:
                    raise JournalError(
                        "updater staged state is not a physical directory"
                    ) from exc
        else:
            try:
                state_fd = os.open(
                    state,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError as exc:
                raise JournalError("updater state is not a physical directory") from exc
        try:
            transactions_fd = _open_child_directory(state_fd, "transactions")
            try:
                sentinel = _read_canonical_json_at(
                    state_fd, "sentinel.json", label="updater sentinel"
                )
                expected = self._sentinel(
                    _fd_identity(state_fd), _fd_identity(transactions_fd)
                )
                if sentinel != expected:
                    raise JournalError("updater sentinel belongs to another directory")
                yield state_fd, transactions_fd
            finally:
                os.close(transactions_fd)
        finally:
            os.close(state_fd)

    def _validate_sentinel(
        self, state: Path, *, root_fd: int | None = None
    ) -> dict[str, Any]:
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            sentinel = _read_canonical_json_at(
                state_fd, "sentinel.json", label="updater sentinel"
            )
            if set(sentinel) != {
                "schema_version",
                "kind",
                "root_identity",
                "vendor_dir",
                "state_identity",
                "transactions_identity",
                "self_digest",
            }:
                raise JournalError("updater sentinel fields differ from its contract")
            return sentinel

    @staticmethod
    def _require_transaction_capacity(transactions_fd: int) -> None:
        """Refuse a new journal before pinned history would exceed its cap."""

        if len(os.listdir(transactions_fd)) >= MAX_PINNED_TRANSACTION_HISTORY:
            raise JournalError(
                "updater transaction history has reached the descriptor-safe "
                f"validation limit ({MAX_PINNED_TRANSACTION_HISTORY}); "
                "refusing to create another transaction"
            )

    def _validate_state_dir(
        self, state: Path, *, root_fd: int | None = None
    ) -> dict[str, Any]:
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            transaction_names = sorted(os.listdir(transactions_fd))
            if not transaction_names:
                raise JournalError("updater transaction store is empty")
            if len(transaction_names) > MAX_PINNED_TRANSACTION_HISTORY:
                raise JournalError(
                    "updater transaction history exceeds the descriptor-safe "
                    f"validation limit ({MAX_PINNED_TRANSACTION_HISTORY})"
                )
            current = self._current(state, state_fd=state_fd)
            journal_identities: list[str] = []
            journal_statuses: dict[str, str] = {}
            # Keep every child descriptor live until the complete store has
            # been validated.  Otherwise an attacker can exchange an already
            # checked directory for a same-named copy while a later sibling is
            # being checked and evade the final name-only snapshot.
            transaction_fds: list[tuple[str, int]] = []
            try:
                for transaction_id in transaction_names:
                    if not TRANSACTION_ID_RE.fullmatch(transaction_id):
                        raise JournalError(
                            "updater transaction store contains an invalid entry: "
                            + transaction_id
                        )
                    transaction_fd = _open_child_directory(
                        transactions_fd, transaction_id
                    )
                    transaction_fds.append((transaction_id, transaction_fd))
                    _assert_child_binding(
                        transactions_fd, transaction_id, transaction_fd
                    )
                    with _descriptor_cwd(transaction_fd) as anchored:
                        journal, _operations = self._validate_journal(
                            anchored,
                            transaction_fd=transaction_fd,
                            state_fd=state_fd,
                            transactions_fd=transactions_fd,
                            expected_transaction_id=transaction_id,
                        )
                    self._validate_transaction_reports(
                        transaction_fd,
                        journal,
                        active=current == transaction_id,
                    )
                    journal_identities.append(journal["transaction_id"])
                    journal_statuses[transaction_id] = journal["status"]
                    _assert_child_binding(
                        transactions_fd, transaction_id, transaction_fd
                    )
                if sorted(os.listdir(transactions_fd)) != transaction_names:
                    raise JournalError(
                        "updater transaction store changed during validation"
                    )
                # Reassert every retained inode binding after the final name
                # snapshot; equal directory names are not equal identities.
                for transaction_id, transaction_fd in transaction_fds:
                    _assert_child_binding(
                        transactions_fd, transaction_id, transaction_fd
                    )
                _assert_child_binding(state_fd, "transactions", transactions_fd)
                validation_root_fd = root_fd
                close_validation_root = False
                if validation_root_fd is None:
                    validation_root_fd = os.open(
                        self.root,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                    close_validation_root = True
                try:
                    if _fd_identity(validation_root_fd) != self.root_identity:
                        raise JournalError(
                            "workspace root identity changed during state validation"
                        )
                    self._assert_state_binding(
                        state, validation_root_fd, state_fd
                    )
                finally:
                    if close_validation_root:
                        os.close(validation_root_fd)
                if sorted(journal_identities) != transaction_names:
                    raise JournalError(
                        "updater transaction store differs from journal history"
                    )
                return {
                    "state": _fd_identity(state_fd),
                    "transactions": _fd_identity(transactions_fd),
                    "journals": journal_statuses,
                    "current": current,
                }
            finally:
                for _transaction_id, transaction_fd in reversed(transaction_fds):
                    os.close(transaction_fd)

    def _validate_transaction_reports(
        self,
        transaction_fd: int,
        journal: Mapping[str, Any],
        *,
        active: bool,
    ) -> None:
        """Validate runtime reports as a journal-aligned state machine."""

        documents: dict[str, dict[str, Any] | None] = {}
        labels = {
            "report.json": "transaction report",
            "report.pending.json": "pending transaction report",
            "failure-report.json": "transaction failure report",
        }
        for name, label in labels.items():
            try:
                metadata = os.stat(name, dir_fd=transaction_fd, follow_symlinks=False)
            except FileNotFoundError:
                documents[name] = None
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise JournalError(f"{label} is not a regular file")
            documents[name] = _read_canonical_json_at(
                transaction_fd, name, label=label
            )

        final = documents["report.json"]
        pending = documents["report.pending.json"]
        failure = documents["failure-report.json"]
        status = journal["status"]

        for label, report in (
            ("transaction report", final),
            ("pending transaction report", pending),
        ):
            if report is None:
                continue
            required = {
                "schema_version",
                "transaction_id",
                "kind",
                "status",
                "no_op",
                "source_transaction",
                "operations",
                "self_digest",
            }
            if (
                not required.issubset(report)
                or not set(report).issubset(required | SUCCESS_REPORT_OPTIONAL_FIELDS)
                or report.get("schema_version") != STATE_SCHEMA
                or report.get("transaction_id") != journal["transaction_id"]
                or report.get("kind") != journal["kind"]
                or report.get("status") != "committed"
                or report.get("no_op") is not False
                or report.get("source_transaction") != journal["source_transaction"]
                or report.get("operations") != journal["operations"]
            ):
                raise JournalError(f"{label} differs from its journal contract")

        if failure is not None:
            expected_failure_status = {
                "applying": "recovering",
                "recovered": "recovered",
                "recovery_failed": "recovery_failed",
            }.get(status)
            if (
                set(failure)
                != {
                    "schema_version",
                    "transaction_id",
                    "kind",
                    "status",
                    "error_type",
                    "error",
                    "self_digest",
                }
                or failure.get("schema_version") != STATE_SCHEMA
                or failure.get("transaction_id") != journal["transaction_id"]
                or failure.get("kind") != journal["kind"]
                or failure.get("status") != expected_failure_status
                or not isinstance(failure.get("error_type"), str)
                or not isinstance(failure.get("error"), str)
            ):
                raise JournalError(
                    "transaction failure report differs from its journal contract"
                )

        if status == "committed":
            if failure is not None or (final is not None and pending is not None):
                raise JournalError("committed transaction report set is inconsistent")
            if active:
                if final is None and pending is None:
                    raise JournalError(
                        "committed transaction lacks a durable report candidate"
                    )
            elif final is None or pending is not None:
                raise JournalError("idle committed transaction lacks its final report")
        elif status == "recovered":
            if final is not None or pending is not None or failure is None:
                raise JournalError("recovered transaction lacks its failure report")
        elif status == "recovery_failed":
            if final is not None or pending is not None or failure is None:
                raise JournalError("failed recovery report set is inconsistent")
        elif status == "applying":
            # pending+failure is a valid crash window: the success candidate
            # may already be durable when exception handling records a
            # recovering failure, before recovery removes the stale pending
            # candidate.
            if final is not None:
                raise JournalError("applying transaction report set is inconsistent")
        elif final is not None or pending is not None or failure is not None:
            raise JournalError("prepared transaction unexpectedly has a report")

    def _assert_report_candidate(
        self,
        transaction_fd: int,
        name: str,
        report_fd: int,
        expected: Mapping[str, Any],
    ) -> None:
        label = "pending transaction report" if name == "report.pending.json" else "transaction report"
        try:
            _assert_regular_child_binding(
                transaction_fd, name, report_fd, label=label
            )
            if _sha256_fd(report_fd) != contract.sha256_bytes(
                _canonical(expected)
            ):
                raise JournalError(f"{label} content changed during publication")
        except (OSError, TransactionError) as exc:
            raise ReportIntegrityError(
                f"{label} changed during publication"
            ) from exc

    def _initialize_state(
        self, state: Path, *, root_fd: int | None = None
    ) -> None:
        if root_fd is not None:
            try:
                relative = state.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise JournalError("updater state is outside the workspace") from exc
            parent_fd, leaf = _ensure_parent_at(root_fd, relative)
            try:
                state_fd = _create_child_directory(parent_fd, leaf)
            finally:
                os.close(parent_fd)
        else:
            state.mkdir(mode=0o700)
            state_fd = os.open(
                state,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            os.fchmod(state_fd, 0o700)
            os.fsync(state_fd)
        try:
            transactions_fd = _create_child_directory(state_fd, "transactions")
            try:
                _atomic_json_at(
                    state_fd,
                    "sentinel.json",
                    self._sentinel(
                        _fd_identity(state_fd), _fd_identity(transactions_fd)
                    ),
                )
                os.fsync(state_fd)
            finally:
                os.close(transactions_fd)
        finally:
            os.close(state_fd)
        if root_fd is None:
            _fsync_directory(state.parent)

    def _rebind_private_state_copy(self, state: Path) -> None:
        """Bind a verified private archive copy to its newly allocated inodes."""

        state_fd = os.open(
            state,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            transactions_fd = _open_child_directory(state_fd, "transactions")
            try:
                _atomic_json_at(
                    state_fd,
                    "sentinel.json",
                    self._sentinel(
                        _fd_identity(state_fd), _fd_identity(transactions_fd)
                    ),
                )
                for transaction_id in sorted(os.listdir(transactions_fd)):
                    if not TRANSACTION_ID_RE.fullmatch(transaction_id):
                        raise JournalError(
                            "private rollback archive contains an invalid transaction directory"
                        )
                    transaction_fd = _open_child_directory(
                        transactions_fd, transaction_id
                    )
                    try:
                        journal = _read_canonical_json_at(
                            transaction_fd,
                            "journal.json",
                            label="transaction journal",
                        )
                        local: dict[str, dict[str, int]] = {}
                        for name in sorted(LOCAL_TRANSACTION_DIRECTORIES):
                            local_fd = _open_child_directory(transaction_fd, name)
                            try:
                                local[name] = _fd_identity(local_fd)
                            finally:
                                os.close(local_fd)
                        rebound = dict(journal)
                        rebound.pop("self_digest", None)
                        rebound["directory_bindings"] = self._directory_bindings(
                            state_fd,
                            transactions_fd,
                            transaction_fd,
                            local,
                        )
                        _atomic_json_at(
                            transaction_fd,
                            "journal.json",
                            _sealed(rebound),
                        )
                    finally:
                        os.close(transaction_fd)
                os.fsync(transactions_fd)
            finally:
                os.close(transactions_fd)
            os.fsync(state_fd)
        finally:
            os.close(state_fd)

    def _new_id(self) -> str:
        return os.urandom(16).hex()

    def _transaction_path(self, state: Path, transaction_id: str) -> Path:
        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid transaction id")
        path = state / "transactions" / transaction_id
        if path.exists() or path.is_symlink():
            _validate_physical_tree(
                path,
                label="transaction directory",
                allow_leaf_symlinks_under={"stage"},
            )
        return path

    def _directory_bindings(
        self,
        state_fd: int,
        transactions_fd: int,
        transaction_fd: int,
        local_directories: Mapping[str, Mapping[str, int]],
    ) -> dict[str, Any]:
        if set(local_directories) != LOCAL_TRANSACTION_DIRECTORIES:
            raise JournalError("transaction local-directory binding set is incomplete")
        return {
            "state": _fd_identity(state_fd),
            "transactions": _fd_identity(transactions_fd),
            "transaction": _fd_identity(transaction_fd),
            "local_directories": {
                name: dict(local_directories[name])
                for name in sorted(local_directories)
            },
        }

    def _validate_directory_bindings(
        self,
        raw: object,
        *,
        transaction_fd: int,
        state_fd: int | None = None,
        transactions_fd: int | None = None,
        required_local_directories: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping) or set(raw) != {
            "state",
            "transactions",
            "transaction",
            "local_directories",
        }:
            raise JournalError("transaction directory bindings are invalid")
        for key in ("state", "transactions", "transaction"):
            identity = raw.get(key)
            if (
                not isinstance(identity, Mapping)
                or set(identity) != {"device", "inode"}
                or not all(isinstance(value, int) for value in identity.values())
            ):
                raise JournalError("transaction directory identity is invalid")
        local = raw.get("local_directories")
        if not isinstance(local, Mapping) or set(local) != LOCAL_TRANSACTION_DIRECTORIES:
            raise JournalError("transaction local-directory bindings are invalid")
        for name, identity in local.items():
            if (
                not isinstance(identity, Mapping)
                or set(identity) != {"device", "inode"}
                or not all(isinstance(value, int) for value in identity.values())
            ):
                raise JournalError(
                    f"transaction local-directory identity is invalid: {name}"
                )
        if dict(raw["transaction"]) != _fd_identity(transaction_fd):
            raise JournalError("transaction directory identity changed")
        if state_fd is not None and dict(raw["state"]) != _fd_identity(state_fd):
            raise JournalError("updater state directory identity changed")
        if transactions_fd is not None and dict(
            raw["transactions"]
        ) != _fd_identity(transactions_fd):
            raise JournalError("transaction store directory identity changed")
        required = (
            set(LOCAL_TRANSACTION_DIRECTORIES)
            if required_local_directories is None
            else set(required_local_directories)
        )
        if not required.issubset(LOCAL_TRANSACTION_DIRECTORIES):
            raise JournalError("unknown required transaction local directory")
        for name in sorted(required):
            descriptor = _open_child_directory(
                transaction_fd,
                name,
                expected_identity=local[name],
            )
            os.close(descriptor)
        return {
            "state": dict(raw["state"]),
            "transactions": dict(raw["transactions"]),
            "transaction": dict(raw["transaction"]),
            "local_directories": {
                name: dict(identity) for name, identity in local.items()
            },
        }

    @contextlib.contextmanager
    def _open_transaction_handles(
        self,
        state: Path,
        transaction_id: str,
        *,
        root_fd: int | None,
        required_local_directories: Iterable[str] | None = None,
        validate_tree: bool = True,
    ):
        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid transaction id")
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            transaction_fd = _open_child_directory(
                transactions_fd, transaction_id
            )
            try:
                with _descriptor_cwd(transaction_fd) as anchored:
                    journal, operations = self._validate_journal(
                        anchored,
                        transaction_fd=transaction_fd,
                        state_fd=state_fd,
                        transactions_fd=transactions_fd,
                        expected_transaction_id=transaction_id,
                        required_local_directories=required_local_directories,
                        validate_tree=validate_tree,
                    )
                    self._validate_transaction_reports(
                        transaction_fd,
                        journal,
                        active=self._current(
                            state, state_fd=state_fd, root_fd=root_fd
                        )
                        == transaction_id,
                    )
                    yield (
                        state_fd,
                        transactions_fd,
                        transaction_fd,
                        anchored,
                        journal,
                        operations,
                    )
            finally:
                os.close(transaction_fd)

    def _journal_document(
        self,
        transaction_id: str,
        kind: str,
        status: str,
        operations: Iterable[Operation],
        *,
        source_transaction: str | None = None,
        error: str | None = None,
        directory_bindings: Mapping[str, Any],
    ) -> dict[str, Any]:
        safe_error = self._safe_error(error) if error is not None else None
        return _sealed(
            {
                "schema_version": STATE_SCHEMA,
                "transaction_id": transaction_id,
                "kind": kind,
                "status": status,
                "root_identity": dict(self.root_identity),
                "vendor_dir": self.vendor_dir,
                "source_transaction": source_transaction,
                "directory_bindings": dict(directory_bindings),
                "operations": [item.to_dict() for item in operations],
                "error": safe_error,
            }
        )

    def _safe_error(self, value: object) -> str:
        text = str(value).replace(str(self.root), "<workspace>")
        text = text.replace(str(Path.home()), "<home>")
        text = re.sub(
            r"(?i)(token|secret|password|credential)(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            text,
        )
        return text[:2048]

    def _validate_journal(
        self,
        tx: Path,
        *,
        transaction_fd: int | None = None,
        state_fd: int | None = None,
        transactions_fd: int | None = None,
        expected_transaction_id: str | None = None,
        required_local_directories: Iterable[str] | None = None,
        validate_tree: bool = True,
    ) -> tuple[dict[str, Any], list[Operation]]:
        if validate_tree:
            _validate_physical_tree(
                tx,
                label="transaction directory",
                allow_leaf_symlinks_under={"stage"},
            )
        journal = (
            _read_canonical_json_at(
                transaction_fd, "journal.json", label="transaction journal"
            )
            if transaction_fd is not None
            else _read_canonical_json(
                tx / "journal.json", label="transaction journal"
            )
        )
        expected_fields = {
            "schema_version", "transaction_id", "kind", "status", "root_identity",
            "vendor_dir", "source_transaction", "directory_bindings", "operations",
            "error", "self_digest",
        }
        if set(journal) != expected_fields or journal.get("schema_version") != STATE_SCHEMA:
            raise JournalError("transaction journal fields differ from its contract")
        transaction_name = expected_transaction_id or tx.name
        if (
            transaction_name != journal.get("transaction_id")
            or not TRANSACTION_ID_RE.fullmatch(transaction_name)
        ):
            raise JournalError("transaction journal identity mismatch")
        if journal.get("kind") not in {"apply", "rollback"}:
            raise JournalError("invalid transaction kind")
        if journal.get("status") not in JOURNAL_STATUSES:
            raise JournalError("invalid transaction status")
        if journal.get("root_identity") != self.root_identity or journal.get("vendor_dir") != self.vendor_dir:
            raise JournalError("transaction journal belongs to another workspace")
        source = journal.get("source_transaction")
        if source is not None and not TRANSACTION_ID_RE.fullmatch(source):
            raise JournalError("invalid source transaction id")
        if journal.get("error") is not None and not isinstance(journal["error"], str):
            raise JournalError("invalid transaction error")
        if transaction_fd is None:
            transaction_fd = os.open(
                tx,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            close_transaction_fd = True
        else:
            close_transaction_fd = False
        try:
            self._validate_directory_bindings(
                journal.get("directory_bindings"),
                transaction_fd=transaction_fd,
                state_fd=state_fd,
                transactions_fd=transactions_fd,
                required_local_directories=required_local_directories,
            )
        finally:
            if close_transaction_fd:
                os.close(transaction_fd)
        raw_operations = journal.get("operations")
        if not isinstance(raw_operations, list):
            raise JournalError("transaction operations are invalid")
        operations = [Operation.from_mapping(item) for item in raw_operations]
        if len({item.id for item in operations}) != len(operations):
            raise JournalError("duplicate transaction operation id")
        if len({item.target_path.casefold() for item in operations}) != len(operations):
            raise JournalError("duplicate transaction target")
        return journal, operations

    def _write_journal(
        self,
        tx: Path,
        document: Mapping[str, Any],
        *,
        state: Path | None = None,
        state_fd: int | None = None,
        root_fd: int | None = None,
        transaction_fd: int | None = None,
        transactions_fd: int | None = None,
        transaction_id: str | None = None,
    ) -> None:
        if transaction_fd is None:
            _validate_physical_tree(
                tx,
                label="transaction directory",
                allow_leaf_symlinks_under={"stage"},
            )
            _atomic_json(tx / "journal.json", document)
        else:
            if (
                state is None
                or state_fd is None
                or root_fd is None
                or transactions_fd is None
                or transaction_id is None
            ):
                raise JournalError(
                    "pinned journal write lacks complete hierarchy bindings"
                )
            self._assert_journal_hierarchy_binding(
                state,
                state_fd,
                transactions_fd,
                transaction_id,
                transaction_fd,
                root_fd=root_fd,
            )
            _atomic_json_at(transaction_fd, "journal.json", document)
            self._assert_journal_hierarchy_binding(
                state,
                state_fd,
                transactions_fd,
                transaction_id,
                transaction_fd,
                root_fd=root_fd,
            )

    def _assert_journal_hierarchy_binding(
        self,
        state: Path,
        state_fd: int,
        transactions_fd: int,
        transaction_id: str,
        transaction_fd: int,
        *,
        root_fd: int,
    ) -> None:
        canonical_root_fd: int | None = None
        try:
            canonical_root_fd = os.open(
                self.root,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            pinned_root_identity = _fd_identity(root_fd)
            canonical_root_identity = _fd_identity(canonical_root_fd)
            if (
                pinned_root_identity != self.root_identity
                or canonical_root_identity != self.root_identity
                or canonical_root_identity != pinned_root_identity
            ):
                raise JournalError(
                    "workspace root identity changed during journal write"
                )
            _assert_child_binding(
                transactions_fd, transaction_id, transaction_fd
            )
            _assert_child_binding(state_fd, "transactions", transactions_fd)
            self._assert_state_binding(state, root_fd, state_fd)
        except (OSError, TransactionError) as exc:
            raise JournalHierarchyBindingError(
                "journal hierarchy binding changed during write"
            ) from exc
        finally:
            if canonical_root_fd is not None:
                os.close(canonical_root_fd)

    def _current(
        self,
        state: Path,
        *,
        root_fd: int | None = None,
        state_fd: int | None = None,
    ) -> str | None:
        if state_fd is not None:
            if root_fd is not None:
                self._assert_state_binding(state, root_fd, state_fd)
            try:
                metadata = os.stat(
                    "current.json", dir_fd=state_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(metadata.st_mode):
                raise JournalError("invalid current transaction pointer")
            value = _read_canonical_json_at(
                state_fd,
                "current.json",
                label="current transaction pointer",
            )
            if (
                set(value) != {"schema_version", "transaction_id", "self_digest"}
                or value.get("schema_version") != STATE_SCHEMA
            ):
                raise JournalError("invalid current transaction pointer")
            identity = value.get("transaction_id")
            if not isinstance(identity, str) or not TRANSACTION_ID_RE.fullmatch(
                identity
            ):
                raise JournalError("invalid current transaction identity")
            return identity
        if root_fd is not None:
            with self._open_state_handles(state, root_fd=root_fd) as (
                opened_state_fd,
                _transactions_fd,
            ):
                return self._current(
                    state, state_fd=opened_state_fd, root_fd=root_fd
                )
        path = state / "current.json"
        if not (path.exists() or path.is_symlink()):
            return None
        value = _read_canonical_json(path, label="current transaction pointer")
        if (
            set(value) != {"schema_version", "transaction_id", "self_digest"}
            or value.get("schema_version") != STATE_SCHEMA
        ):
            raise JournalError("invalid current transaction pointer")
        identity = value.get("transaction_id")
        if not isinstance(identity, str) or not TRANSACTION_ID_RE.fullmatch(identity):
            raise JournalError("invalid current transaction identity")
        return identity

    def _set_current(
        self,
        state: Path,
        transaction_id: str,
        *,
        root_fd: int | None = None,
        state_fd: int | None = None,
    ) -> None:
        if state_fd is not None:
            if root_fd is not None:
                self._assert_state_binding(state, root_fd, state_fd)
            _atomic_json_at(
                state_fd,
                "current.json",
                _sealed(
                    {
                        "schema_version": STATE_SCHEMA,
                        "transaction_id": transaction_id,
                    }
                ),
            )
            return
        if root_fd is not None:
            with self._open_state_handles(state, root_fd=root_fd) as (
                opened_state_fd,
                _transactions_fd,
            ):
                self._set_current(
                    state,
                    transaction_id,
                    state_fd=opened_state_fd,
                    root_fd=root_fd,
                )
            return
        _atomic_json(
            state / "current.json",
            _sealed({"schema_version": STATE_SCHEMA, "transaction_id": transaction_id}),
        )

    def _clear_current(
        self,
        state: Path,
        *,
        root_fd: int | None = None,
        state_fd: int | None = None,
    ) -> None:
        if state_fd is not None:
            if root_fd is not None:
                self._assert_state_binding(state, root_fd, state_fd)
            try:
                metadata = os.stat(
                    "current.json", dir_fd=state_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                return
            if not stat.S_ISREG(metadata.st_mode):
                raise JournalError("invalid current transaction pointer")
            os.unlink("current.json", dir_fd=state_fd)
            os.fsync(state_fd)
            return
        if root_fd is not None:
            with self._open_state_handles(state, root_fd=root_fd) as (
                opened_state_fd,
                _transactions_fd,
            ):
                self._clear_current(
                    state, state_fd=opened_state_fd, root_fd=root_fd
                )
            return
        path = state / "current.json"
        if path.exists() or path.is_symlink():
            path.unlink()
            _fsync_directory(state)

    def recovery_required(self) -> dict[str, Any] | None:
        """Return read-only recovery metadata without modifying state."""

        bootstrap_transition = self.state / BOOTSTRAP_TRANSITION
        if bootstrap_transition.is_file() and not bootstrap_transition.is_symlink():
            try:
                self._validate_state_dir(self.state)
                bootstrap = self._bootstrap_transition_document()
                transaction_id = bootstrap["transaction_id"]
                bootstrap_phase = bootstrap["phase"]
                if self.prelock.exists() or self.prelock.is_symlink():
                    if bootstrap_phase == "returning-to-plan-lock":
                        with self._open_bootstrap_return_plan_lock(
                            transaction_id, archived=False
                        ):
                            pass
                    else:
                        metadata = os.lstat(self.prelock)
                        if (
                            stat.S_ISLNK(metadata.st_mode)
                            or not stat.S_ISDIR(metadata.st_mode)
                            or any(self.prelock.iterdir())
                        ):
                            raise JournalError(
                                "bootstrap transition plan.lock is not an empty physical directory"
                            )
                current = self._current(self.state)
                if current is None:
                    tx = self._transaction_path(self.state, transaction_id)
                    journal, _ = self._validate_journal(tx)
                    if journal["status"] not in {"committed", "recovered"}:
                        raise JournalError(
                            "bootstrap transition lacks an active transaction"
                        )
                    return {
                        "location": "root",
                        "status": "bootstrap_cleanup_required",
                        "transaction_id": transaction_id,
                        "transaction_status": journal["status"],
                    }
                if current != transaction_id:
                    raise JournalError("bootstrap transition/current identity mismatch")
                tx = self._transaction_path(self.state, current)
                journal, _ = self._validate_journal(tx)
                return {
                    "location": "root",
                    "transaction_id": current,
                    "status": journal["status"],
                    "bootstrap_cleanup_required": True,
                }
            except (OSError, TransactionError) as exc:
                return {
                    "location": "root",
                    "status": "invalid",
                    "error": self._safe_error(exc),
                }
        archived_state = self.prelock / BOOTSTRAP_CHILD
        reuse_transition = archived_state / ARCHIVE_REUSE_TRANSITION
        archived_ready = (
            self.prelock.is_dir()
            and not self.prelock.is_symlink()
            and (archived_state / ARCHIVED_MARKER).is_file()
        )
        bootstrap_return_marker = self.prelock / BOOTSTRAP_RETURN_MARKER
        if (
            bootstrap_return_marker.exists()
            or bootstrap_return_marker.is_symlink()
        ):
            try:
                raw_return = _read_canonical_json(
                    bootstrap_return_marker,
                    label="bootstrap return plan.lock marker",
                )
                transaction_id = raw_return.get("transaction_id")
                if not isinstance(transaction_id, str):
                    raise JournalError(
                        "bootstrap return transaction identity is missing"
                    )
                with self._open_bootstrap_return_plan_lock(
                    transaction_id, archived=True
                ):
                    pass
                with self._open_state_handles(archived_state) as (
                    archived_state_fd,
                    archived_transactions_fd,
                ):
                    bootstrap = self._bootstrap_transition_document(
                        state_fd=archived_state_fd
                    )
                    if (
                        bootstrap["transaction_id"] != transaction_id
                        or bootstrap["phase"]
                        != "returning-to-plan-lock"
                    ):
                        raise JournalError(
                            "archived bootstrap return transition is invalid"
                        )
                    if self._current(
                        archived_state, state_fd=archived_state_fd
                    ) is not None:
                        raise JournalError(
                            "archived bootstrap return is still active"
                        )
                    self._validate_archive_marker(
                        archived_state,
                        state_fd=archived_state_fd,
                        transactions_fd=archived_transactions_fd,
                    )
                return {
                    "location": "bootstrap-return",
                    "status": "bootstrap_return_cleanup_required",
                    "transaction_id": transaction_id,
                }
            except (OSError, TransactionError) as exc:
                return {
                    "location": "bootstrap-return",
                    "status": "invalid",
                    "error": self._safe_error(exc),
                }
        if reuse_transition.exists() or reuse_transition.is_symlink():
            try:
                if not self.prelock.is_dir() or self.prelock.is_symlink():
                    raise JournalError(
                        "archive reuse plan.lock is not a physical directory"
                    )
                with self._open_state_handles(archived_state) as (
                    archived_state_fd,
                    archived_transactions_fd,
                ):
                    reuse = self._validate_archive_reuse_transition_at(
                        archived_state_fd
                    )
                    try:
                        marker_metadata = os.stat(
                            ARCHIVED_MARKER,
                            dir_fd=archived_state_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        marker_metadata = None
                    if marker_metadata is not None:
                        if not stat.S_ISREG(marker_metadata.st_mode):
                            raise JournalError(
                                "archive reuse baseline marker is unsafe"
                            )
                        marker_digest = self._read_archive_marker_at(
                            archived_state_fd
                        )["state_digest"]
                        if reuse["phase"] == "finalizing":
                            if marker_digest != reuse["archive_digest"]:
                                self._validate_archived_history_open(
                                    archived_state,
                                    archived_state_fd,
                                    archived_transactions_fd,
                                )
                                if (
                                    marker_digest
                                    != self._logical_state_digest_at(
                                        archived_state_fd
                                    )
                                ):
                                    raise JournalError(
                                        "archive reuse final marker changed"
                                    )
                        elif marker_digest != reuse["archive_digest"]:
                            raise JournalError(
                                "archive reuse baseline marker changed"
                            )
                    elif reuse["phase"] == "preparing":
                        raise JournalError(
                            "preparing archive reuse lost its baseline marker"
                        )
                    current = self._current(
                        archived_state, state_fd=archived_state_fd
                    )
                    transaction_id = reuse["transaction_id"]
                    try:
                        os.stat(
                            transaction_id,
                            dir_fd=archived_transactions_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        transaction_exists = False
                    else:
                        transaction_exists = True
                    if current is None and not transaction_exists:
                        if reuse["phase"] != "preparing":
                            raise JournalError(
                                "active archive reuse lost its transaction"
                            )
                        return {
                            "location": "archive-reuse",
                            "status": "archive_reuse_intent_cleanup_required",
                            "transaction_id": transaction_id,
                        }
                    if current is not None and current != transaction_id:
                        raise JournalError(
                            "archive reuse/current identity mismatch"
                        )
                with self._open_transaction_handles(
                    archived_state, transaction_id, root_fd=None
                ) as (
                    _state_fd,
                    _transactions_fd,
                    _transaction_fd,
                    _anchored_tx,
                    journal,
                    _operations,
                ):
                    transaction_status = journal["status"]
                if current is None:
                    if (
                        transaction_status == "prepared"
                        and reuse["phase"] == "preparing"
                    ):
                        return {
                            "location": "archive-reuse",
                            "status": "archive_reuse_transaction",
                            "transaction_id": transaction_id,
                            "transaction_status": transaction_status,
                        }
                    if transaction_status != "recovered":
                        raise JournalError(
                            "inactive archive reuse transaction is not recovered"
                        )
                    return {
                        "location": "archive-reuse",
                        "status": "archive_reuse_finalize_required",
                        "transaction_id": transaction_id,
                    }
                return {
                    "location": "archive-reuse",
                    "status": "archive_reuse_transaction",
                    "transaction_id": transaction_id,
                    "transaction_status": transaction_status,
                }
            except (OSError, TransactionError) as exc:
                return {
                    "location": "archive-reuse",
                    "status": "invalid",
                    "error": self._safe_error(exc),
                }
        transition = self.state / ARCHIVE_TRANSITION
        if archived_ready and self.state.exists():
            try:
                self._validate_archive_marker(archived_state)
            except (OSError, TransactionError) as exc:
                return {
                    "location": "archive",
                    "status": "invalid",
                    "error": self._safe_error(exc),
                }
            return {
                "location": "archive",
                "status": "archive_migration_required",
            }
        if transition.exists() or transition.is_symlink():
            try:
                if transition.is_symlink() or not transition.is_file():
                    raise JournalError(
                        "rollback archive transition is not a regular file"
                    )
                self._validate_state_dir(self.state)
                marker = self._validate_archive_transition()
                if marker.get("kind") == "legacy-rollback-state-archive":
                    if self._current(self.state) is not None:
                        raise JournalError(
                            "generic rollback archive transition has an active transaction"
                        )
                    return {
                        "location": "archive",
                        "status": "archive_migration_required",
                    }
                transaction_id = marker["transaction_id"]
                source_transaction = marker["source_transaction"]
                current = self._current(self.state)
                tx = self._transaction_path(self.state, transaction_id)
                if current is None and not tx.exists():
                    return {
                        "location": "root",
                        "status": "archive_intent_cleanup_required",
                        "transaction_id": transaction_id,
                    }
                journal, _ = self._validate_journal(tx)
                if (
                    journal["kind"] != "rollback"
                    or journal["source_transaction"] != source_transaction
                ):
                    raise JournalError(
                        "rollback archive transition does not match its transaction"
                    )
                if current is not None:
                    if current != transaction_id:
                        raise JournalError(
                            "rollback archive transition/current identity mismatch"
                        )
                    return {
                        "location": "root",
                        "status": journal["status"],
                        "transaction_id": transaction_id,
                        "archive_after_commit": True,
                    }
                if journal["status"] == "committed":
                    return {
                        "location": "archive",
                        "status": "archive_migration_required",
                        "transaction_id": transaction_id,
                    }
                if journal["status"] == "recovered":
                    return {
                        "location": "root",
                        "status": "archive_intent_cleanup_required",
                        "transaction_id": transaction_id,
                    }
                raise JournalError(
                    "inactive rollback archive transaction is neither committed nor recovered"
                )
            except (OSError, TransactionError) as exc:
                return {
                    "location": "archive",
                    "status": "invalid",
                    "error": self._safe_error(exc),
                }
        locations: list[tuple[str, Path]] = []
        if self.state.exists() or self.state.is_symlink():
            locations.append(("root", self.state))
        if self.prelock.exists() or self.prelock.is_symlink():
            if not self.prelock.is_dir() or self.prelock.is_symlink():
                return {"location": "prelock", "status": "foreign_plan_lock"}
            if archived_ready:
                try:
                    self._validate_archive_marker(archived_state)
                    # A complete idle rollback archive is stable state, not an
                    # interrupted transaction.
                    return None
                except (OSError, TransactionError) as exc:
                    return {
                        "location": "archive",
                        "status": "invalid",
                        "error": self._safe_error(exc),
                    }
            if (self.prelock / "sentinel.json").is_file():
                return {
                    "location": "rollback-prelock",
                    "status": "unsupported_legacy_archive",
                    "error": (
                        "direct plan.lock rollback state is unsupported; "
                        "history migration is required"
                    ),
                }
            else:
                locations.append(("prelock", self.prelock / BOOTSTRAP_CHILD))
        for label, state in locations:
            try:
                validated = self._validate_state_dir(state)
                identity = validated["current"]
                if identity is None:
                    nonterminal = [
                        transaction_id
                        for transaction_id, status in validated["journals"].items()
                        if status not in {"committed", "recovered"}
                    ]
                    if len(nonterminal) == 1:
                        orphan = nonterminal[0]
                        orphan_status = validated["journals"][orphan]
                        if orphan_status != "prepared":
                            raise JournalError(
                                "inactive transaction has a non-prepared status"
                            )
                        return {
                            "location": label,
                            "transaction_id": orphan,
                            "status": orphan_status,
                            "orphaned_current": True,
                        }
                    if nonterminal:
                        raise JournalError(
                            "updater state has multiple inactive non-terminal transactions"
                        )
                    if label == "prelock":
                        return {
                            "location": "prelock",
                            "status": "recovered_pending_cleanup",
                        }
                    continue
                tx = self._transaction_path(state, identity)
                journal, _ = self._validate_journal(tx)
                return {
                    "location": label,
                    "transaction_id": identity,
                    "status": journal["status"],
                }
            except (OSError, TransactionError) as exc:
                return {"location": label, "status": "invalid", "error": self._safe_error(exc)}
        return None

    def _copy_file(self, source: Path, destination: Path, expected: Mapping[str, Any]) -> None:
        metadata = os.lstat(source)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UnsafePathError(f"payload is not a regular file: {source.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, int(expected["mode"], 8))
        try:
            with source.open("rb") as incoming, os.fdopen(descriptor, "wb", closefd=False) as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                outgoing.flush()
                os.fchmod(descriptor, int(expected["mode"], 8))
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if _observe(destination, destination.name) != dict(expected):
            raise ThirdPartyDriftError(f"staged payload differs for {destination.name}")
        _fsync_directory(destination.parent)

    def _copy_descriptor(
        self, source_fd: int, destination: Path, expected: Mapping[str, Any]
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            int(expected["mode"], 8),
        )
        try:
            os.lseek(source_fd, 0, os.SEEK_SET)
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            os.fchmod(descriptor, int(expected["mode"], 8))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if _observe(destination, destination.name) != dict(expected):
            raise ThirdPartyDriftError(
                f"staged descriptor differs for {destination.name}"
            )
        _fsync_directory(destination.parent)

    def _stage_payloads(
        self,
        transaction_fd: int,
        operations: list[Operation],
        payload_sources: Mapping[str, Path] | None,
        payload_bytes: Mapping[str, bytes] | None,
    ) -> dict[str, int]:
        stage_fd = _create_child_directory(transaction_fd, "stage")
        try:
            sources = payload_sources or {}
            byte_values = payload_bytes or {}
            for index, operation in enumerate(operations):
                post = operation.post
                if post["type"] != "file":
                    continue
                name = f"{index:06d}"
                if operation.id in byte_values:
                    payload = byte_values[operation.id]
                    if not isinstance(payload, bytes):
                        raise TransactionError(
                            f"payload bytes are invalid for {operation.id}"
                        )
                    _write_bytes_at(
                        stage_fd,
                        name,
                        payload,
                        mode=int(post["mode"], 8),
                    )
                elif operation.id in sources:
                    source_fd, metadata = _read_regular_source(
                        Path(sources[operation.id])
                    )
                    try:
                        actual_source = {
                            "type": "file",
                            "sha256": _sha256_fd(source_fd),
                            "mode": _mode(metadata),
                        }
                        if actual_source != dict(post):
                            raise ThirdPartyDriftError(
                                f"payload source differs for {operation.id}"
                            )
                        _copy_fd_at(
                            source_fd,
                            stage_fd,
                            name,
                            mode=int(post["mode"], 8),
                        )
                    finally:
                        os.close(source_fd)
                else:
                    raise TransactionError(
                        f"file operation lacks a payload: {operation.id}"
                    )
                if _observe_leaf_at(stage_fd, name, name) != dict(post):
                    raise ThirdPartyDriftError(
                        f"staged payload differs for {operation.id}"
                    )
                _assert_child_binding(transaction_fd, "stage", stage_fd)
            os.fsync(stage_fd)
            return _fd_identity(stage_fd)
        finally:
            os.close(stage_fd)

    def _copy_bundle(
        self,
        transaction_fd: int,
        name: str,
        files: Mapping[str, Path | bytes] | None,
    ) -> dict[str, int]:
        if name not in {"worker", "input"}:
            raise JournalError("invalid transaction bundle directory")
        directory_fd = _create_child_directory(transaction_fd, name)
        try:
            digests: dict[str, str] = {}
            for relative, source in sorted((files or {}).items()):
                rel = contract.validate_relative_path(relative, field=f"{name} path")
                if isinstance(source, bytes):
                    _write_bytes_at(directory_fd, rel, source, mode=0o600)
                    digest = contract.sha256_bytes(source)
                else:
                    source_fd, _metadata = _read_regular_source(Path(source))
                    try:
                        digest = _sha256_fd(source_fd)
                        _copy_fd_at(
                            source_fd,
                            directory_fd,
                            rel,
                            mode=0o600,
                        )
                        if digest != _sha256_fd(source_fd):
                            raise ThirdPartyDriftError(
                                f"{name} source changed during copy: {rel}"
                            )
                    finally:
                        os.close(source_fd)
                copied = _read_file_at(directory_fd, rel)
                if contract.sha256_bytes(copied) != digest:
                    raise ThirdPartyDriftError(
                        f"{name} copy digest mismatch: {rel}"
                    )
                digests[rel] = digest
                _assert_child_binding(transaction_fd, name, directory_fd)
            _atomic_json_at(
                directory_fd,
                ".digests.json",
                _sealed({"schema_version": STATE_SCHEMA, "files": digests}),
            )
            os.fsync(directory_fd)
            return _fd_identity(directory_fd)
        finally:
            os.close(directory_fd)

    def _bound_bundle_bytes(
        self,
        transaction_fd: int,
        directory_bindings: Mapping[str, Any],
        name: str,
    ) -> dict[str, bytes]:
        directory_fd = _open_child_directory(
            transaction_fd,
            name,
            expected_identity=directory_bindings["local_directories"][name],
        )
        try:
            with _descriptor_cwd(directory_fd) as anchored:
                _validate_bundle(anchored)
            manifest = _read_canonical_json_at(
                directory_fd,
                ".digests.json",
                label=f"{name} digest manifest",
            )
            result: dict[str, bytes] = {}
            for relative, expected_digest in sorted(manifest["files"].items()):
                payload = _read_file_at(directory_fd, relative)
                if contract.sha256_bytes(payload) != expected_digest:
                    raise JournalError(
                        f"{name} bundle changed while frozen: {relative}"
                    )
                result[relative] = payload
            _assert_child_binding(transaction_fd, name, directory_fd)
            return result
        finally:
            os.close(directory_fd)

    def _backup(
        self, transaction_fd: int, operations: list[Operation], *, root_fd: int
    ) -> dict[str, int]:
        backup_fd = _create_child_directory(transaction_fd, "backup")
        try:
            for index, operation in enumerate(operations):
                parent_fd, leaf = _open_parent_at(
                    root_fd,
                    operation.target_path,
                    allow_missing_parents=True,
                    allow_non_directory_parents=operation.pre["type"] == "absent",
                )
                actual = (
                    {"type": "absent"}
                    if parent_fd is None
                    else _observe_leaf_at(parent_fd, leaf, operation.target_path)
                )
                if actual != dict(operation.pre):
                    if parent_fd is not None:
                        os.close(parent_fd)
                    raise ThirdPartyDriftError(
                        f"base drift before backup: {operation.target_path}"
                    )
                if actual["type"] != "file":
                    if parent_fd is not None:
                        os.close(parent_fd)
                    continue
                assert parent_fd is not None
                try:
                    _assert_parent_binding(
                        root_fd, operation.target_path, parent_fd
                    )
                    source_fd = os.open(
                        leaf,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                    try:
                        pinned = os.fstat(source_fd)
                        if {
                            "type": "file",
                            "sha256": _sha256_fd(source_fd),
                            "mode": _mode(pinned),
                        } != actual:
                            raise ThirdPartyDriftError(
                                f"base drift during backup: {operation.target_path}"
                            )
                        _copy_fd_at(
                            source_fd,
                            backup_fd,
                            f"{index:06d}",
                            mode=int(actual["mode"], 8),
                        )
                    finally:
                        os.close(source_fd)
                finally:
                    os.close(parent_fd)
                _assert_child_binding(transaction_fd, "backup", backup_fd)
            os.fsync(backup_fd)
            return _fd_identity(backup_fd)
        finally:
            os.close(backup_fd)

    def _restore_file(
        self,
        transaction_fd: int,
        stage_fd: int,
        directory_bindings: Mapping[str, Any],
        index: int,
        operation: Operation,
    ) -> str:
        local = directory_bindings["local_directories"]
        backup_fd = _open_child_directory(
            transaction_fd,
            "backup",
            expected_identity=local["backup"],
        )
        source_name = f"{index:06d}"
        temporary_name = f"restore-{index:06d}-{os.urandom(4).hex()}"
        try:
            source_fd = os.open(
                source_name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=backup_fd,
            )
            try:
                metadata = os.fstat(source_fd)
                actual = {
                    "type": "file",
                    "sha256": _sha256_fd(source_fd),
                    "mode": _mode(metadata),
                }
                if actual != dict(operation.pre):
                    raise ThirdPartyDriftError(
                        f"transaction backup drifted: {operation.target_path}"
                    )
                _copy_fd_at(
                    source_fd,
                    stage_fd,
                    temporary_name,
                    mode=int(operation.pre["mode"], 8),
                )
            finally:
                os.close(source_fd)
            _assert_child_binding(transaction_fd, "backup", backup_fd)
            return temporary_name
        finally:
            os.close(backup_fd)

    def _install_one(
        self,
        transaction_fd: int,
        directory_bindings: Mapping[str, Any],
        index: int,
        operation: Operation,
        *,
        root_fd: int,
    ) -> None:
        parent_fd, leaf = _open_parent_at(root_fd, operation.target_path)
        if parent_fd is None:
            raise UnsafePathError(
                f"transaction target parent is absent: {operation.target_path}"
            )
        actual = _observe_leaf_at(
            parent_fd, leaf, operation.target_path
        )
        if actual != dict(operation.pre):
            os.close(parent_fd)
            raise ThirdPartyDriftError(
                f"base drift before mutation: {operation.target_path}"
            )
        try:
            stage_fd = _open_child_directory(
                transaction_fd,
                "stage",
                expected_identity=directory_bindings["local_directories"]["stage"],
            )
        except BaseException:
            os.close(parent_fd)
            raise
        staged_name = f"{index:06d}"
        post = operation.post
        try:
            _assert_parent_binding(
                root_fd, operation.target_path, parent_fd
            )
            _assert_child_binding(transaction_fd, "stage", stage_fd)
            if post["type"] == "absent":
                if actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                elif actual["type"] != "absent":
                    os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
                return
            if post["type"] == "file":
                if actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                    self._inject(f"after_target_removal:{operation.id}")
                os.replace(
                    staged_name,
                    leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=parent_fd,
                )
            elif post["type"] == "symlink":
                os.symlink(post["target"], staged_name, dir_fd=stage_fd)
                if actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                    self._inject(f"after_target_removal:{operation.id}")
                os.replace(
                    staged_name,
                    leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=parent_fd,
                )
            elif post["type"] == "directory":
                os.mkdir(staged_name, 0o755, dir_fd=stage_fd)
                staged_directory_fd = _open_child_directory(
                    stage_fd, staged_name
                )
                try:
                    os.fchmod(staged_directory_fd, 0o755)
                    os.fsync(staged_directory_fd)
                finally:
                    os.close(staged_directory_fd)
                if actual["type"] in {"file", "symlink"}:
                    os.unlink(leaf, dir_fd=parent_fd)
                    self._inject(f"after_target_removal:{operation.id}")
                elif actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                    self._inject(f"after_target_removal:{operation.id}")
                os.replace(
                    staged_name,
                    leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=parent_fd,
                )
            os.fsync(parent_fd)
            os.fsync(stage_fd)
            _assert_parent_binding(
                root_fd, operation.target_path, parent_fd
            )
            _assert_child_binding(transaction_fd, "stage", stage_fd)
            if _observe_leaf_at(
                parent_fd, leaf, operation.target_path
            ) != dict(post):
                raise ThirdPartyDriftError(
                    f"post-image verification failed: {operation.target_path}"
                )
        finally:
            os.close(stage_fd)
            os.close(parent_fd)

    def _restore_one(
        self,
        transaction_fd: int,
        directory_bindings: Mapping[str, Any],
        index: int,
        operation: Operation,
        *,
        root_fd: int,
    ) -> None:
        parent_fd, leaf = _open_parent_at(
            root_fd,
            operation.target_path,
            allow_missing_parents=True,
            allow_non_directory_parents=operation.pre["type"] == "absent",
        )
        actual = (
            {"type": "absent"}
            if parent_fd is None
            else _observe_leaf_at(parent_fd, leaf, operation.target_path)
        )
        pre = dict(operation.pre)
        post = dict(operation.post)
        if actual == pre:
            if parent_fd is not None:
                os.close(parent_fd)
            return
        # Absence is transaction-internal only for directory replacement,
        # whose implementation must rmdir/unlink before the atomic rename.
        # File/symlink-to-file/symlink replacement uses os.replace directly;
        # an absent leaf there is third-party drift and must never be silently
        # recreated during recovery.
        internal_absence = actual.get("type") == "absent" and (
            pre.get("type") == "directory" or post.get("type") == "directory"
        )
        if actual != post and not internal_absence:
            if parent_fd is not None:
                os.close(parent_fd)
            raise ThirdPartyDriftError(f"third-party drift blocks recovery: {operation.target_path}")
        if parent_fd is None:
            raise UnsafePathError(
                f"recovery target parent is absent: {operation.target_path}"
            )
        stage_fd = _open_child_directory(
            transaction_fd,
            "stage",
            expected_identity=directory_bindings["local_directories"]["stage"],
        )
        try:
            _assert_parent_binding(
                root_fd, operation.target_path, parent_fd
            )
            _assert_child_binding(transaction_fd, "stage", stage_fd)
            if pre["type"] == "absent":
                if actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                elif actual["type"] != "absent":
                    os.unlink(leaf, dir_fd=parent_fd)
            elif pre["type"] == "file":
                staged_name = self._restore_file(
                    transaction_fd,
                    stage_fd,
                    directory_bindings,
                    index,
                    operation,
                )
                if actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                os.replace(
                    staged_name,
                    leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=parent_fd,
                )
            elif pre["type"] == "symlink":
                staged_name = f"restore-link-{index:06d}-{os.urandom(4).hex()}"
                os.symlink(pre["target"], staged_name, dir_fd=stage_fd)
                if actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                os.replace(
                    staged_name,
                    leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=parent_fd,
                )
            elif pre["type"] == "directory":
                staged_name = f"restore-dir-{index:06d}-{os.urandom(4).hex()}"
                os.mkdir(staged_name, 0o755, dir_fd=stage_fd)
                staged_directory_fd = _open_child_directory(
                    stage_fd, staged_name
                )
                try:
                    os.fchmod(staged_directory_fd, 0o755)
                    os.fsync(staged_directory_fd)
                finally:
                    os.close(staged_directory_fd)
                if actual["type"] in {"file", "symlink"}:
                    os.unlink(leaf, dir_fd=parent_fd)
                elif actual["type"] == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                os.replace(
                    staged_name,
                    leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=parent_fd,
                )
            os.fsync(parent_fd)
            os.fsync(stage_fd)
            _assert_parent_binding(
                root_fd, operation.target_path, parent_fd
            )
            _assert_child_binding(transaction_fd, "stage", stage_fd)
            if _observe_leaf_at(
                parent_fd, leaf, operation.target_path
            ) != pre:
                raise ThirdPartyDriftError(
                    f"recovery verification failed: {operation.target_path}"
                )
        finally:
            os.close(stage_fd)
            os.close(parent_fd)

    def _ordered(self, operations: list[Operation]) -> list[tuple[int, Operation]]:
        indexed = list(enumerate(operations))
        def key(value: tuple[int, Operation]) -> tuple[int, int, int]:
            index, operation = value
            depth = operation.target_path.count("/")
            if operation.id == "metadata:installed-lock" or operation.target_path == (
                f"{self.vendor_dir}/{contract.INSTALLED_LOCK_PATH}"
            ):
                return (5, depth, index)
            pre_kind = operation.pre["type"]
            post_kind = operation.post["type"]
            # Release old tree leaves before changing/removing their directory
            # ancestor.  Unknown leaves are never operations, so the later
            # parent rmdir fails closed instead of recursively deleting them.
            if post_kind == "absent" and pre_kind != "directory":
                return (0, -depth, index)
            # Establish a directory before creating any of its new children.
            if post_kind == "directory":
                return (1, depth, index)
            if pre_kind != "directory" and post_kind != "absent":
                return (2, depth, index)
            # directory -> file/symlink happens after old child deletions.
            if pre_kind == "directory" and post_kind != "absent":
                return (3, -depth, index)
            # Obsolete managed directories are removed deepest-first and only
            # after every declared stale leaf has been removed.
            return (4, -depth, index)
        return sorted(indexed, key=key)

    @contextlib.contextmanager
    def _signals(self):
        previous: dict[int, Any] = {}
        def handler(signum: int, _frame: Any) -> None:
            raise UpdateInterrupted(f"update interrupted by signal {signum}")
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)
        try:
            yield
        finally:
            for signum, old in previous.items():
                signal.signal(signum, old)

    def _recover_transaction(
        self, state: Path, tx: Path, *, root_fd: int
    ) -> dict[str, Any]:
        transaction_id = tx.name
        with self._open_transaction_handles(
            state,
            transaction_id,
            root_fd=root_fd,
            required_local_directories={"stage", "backup"},
            validate_tree=False,
        ) as (
            state_fd,
            transactions_fd,
            transaction_fd,
            anchored,
            journal,
            operations,
        ):
            return self._recover_transaction_open(
                state,
                state_fd,
                transactions_fd,
                transaction_fd,
                anchored,
                journal,
                operations,
                root_fd=root_fd,
            )

    def _recover_transaction_open(
        self,
        state: Path,
        state_fd: int,
        transactions_fd: int,
        transaction_fd: int,
        tx: Path,
        journal: Mapping[str, Any],
        operations: list[Operation],
        *,
        root_fd: int,
        force_rollback_committed: bool = False,
    ) -> dict[str, Any]:
        directory_bindings = journal["directory_bindings"]
        if journal["status"] == "committed" and not force_rollback_committed:
            for operation in operations:
                actual = _observe_at(
                    root_fd,
                    operation.target_path,
                    allow_missing_parents=True,
                    allow_non_directory_parents=operation.post["type"] == "absent",
                )
                if actual != dict(operation.post):
                    raise ThirdPartyDriftError(
                        f"committed transaction post-image drifted: {operation.target_path}"
                    )
            try:
                final_metadata = os.stat(
                    "report.json",
                    dir_fd=transaction_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                final_metadata = None
            if final_metadata is None:
                try:
                    pending_metadata = os.stat(
                        "report.pending.json",
                        dir_fd=transaction_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    raise JournalError(
                        "committed transaction lacks a durable report candidate"
                    ) from exc
                if not stat.S_ISREG(pending_metadata.st_mode):
                    raise JournalError("committed transaction lacks a durable report candidate")
                _read_canonical_json_at(
                    transaction_fd,
                    "report.pending.json",
                    label="pending transaction report",
                )
                os.replace(
                    "report.pending.json",
                    "report.json",
                    src_dir_fd=transaction_fd,
                    dst_dir_fd=transaction_fd,
                )
                os.fsync(transaction_fd)
            else:
                if not stat.S_ISREG(final_metadata.st_mode):
                    raise JournalError("transaction report is not a regular file")
                _read_canonical_json_at(
                    transaction_fd,
                    "report.json",
                    label="transaction report",
                )
            self._clear_current(
                state, state_fd=state_fd, root_fd=root_fd
            )
            return dict(journal)
        try:
            self._restore_preimages_open(
                transaction_fd,
                directory_bindings,
                operations,
                root_fd=root_fd,
            )
            recovered = self._journal_document(
                journal["transaction_id"], journal["kind"], "recovered", operations,
                source_transaction=journal["source_transaction"],
                error=journal.get("error") or "recovered after interrupted transaction",
                directory_bindings=directory_bindings,
            )
            self._write_journal(
                tx,
                recovered,
                state=state,
                state_fd=state_fd,
                root_fd=root_fd,
                transaction_fd=transaction_fd,
                transactions_fd=transactions_fd,
                transaction_id=journal["transaction_id"],
            )
            for stale_report in ("report.pending.json", "report.json"):
                try:
                    os.unlink(stale_report, dir_fd=transaction_fd)
                except FileNotFoundError:
                    pass
            os.fsync(transaction_fd)
            _atomic_json_at(
                transaction_fd,
                "failure-report.json",
                _sealed(
                    {
                        "schema_version": STATE_SCHEMA,
                        "transaction_id": journal["transaction_id"],
                        "kind": journal["kind"],
                        "status": "recovered",
                        "error_type": "InterruptedTransaction",
                        "error": recovered["error"],
                    }
                ),
            )
            self._clear_current(
                state, state_fd=state_fd, root_fd=root_fd
            )
            return recovered
        except BaseException as exc:
            failed = self._journal_document(
                journal["transaction_id"], journal["kind"], "recovery_failed", operations,
                source_transaction=journal["source_transaction"], error=str(exc),
                directory_bindings=directory_bindings,
            )
            self._write_journal(
                tx,
                failed,
                state=state,
                state_fd=state_fd,
                root_fd=root_fd,
                transaction_fd=transaction_fd,
                transactions_fd=transactions_fd,
                transaction_id=journal["transaction_id"],
            )
            _atomic_json_at(
                transaction_fd,
                "failure-report.json",
                _sealed(
                    {
                        "schema_version": STATE_SCHEMA,
                        "transaction_id": journal["transaction_id"],
                        "kind": journal["kind"],
                        "status": "recovery_failed",
                        "error_type": type(exc).__name__,
                        "error": self._safe_error(exc),
                    }
                ),
            )
            raise

    def _restore_preimages_open(
        self,
        transaction_fd: int,
        directory_bindings: Mapping[str, Any],
        operations: list[Operation],
        *,
        root_fd: int,
    ) -> None:
        """Restore through pinned backup fds without consulting state paths."""

        for index, operation in reversed(self._ordered(operations)):
            self._restore_one(
                transaction_fd,
                directory_bindings,
                index,
                operation,
                root_fd=root_fd,
            )

    def recover(self) -> dict[str, Any] | None:
        """Mutating recovery entry point; caller need not pre-acquire the lock."""

        with self.workspace_lock() as held_lock:
            root_fd = held_lock.fd
            if root_fd is None:
                raise TransactionError("workspace lock descriptor is unavailable")
            location = self.recovery_required()
            if location is None:
                return None
            if location.get("status") in {
                "foreign_plan_lock",
                "invalid",
                "unsupported_legacy_archive",
            }:
                raise JournalError(f"unsafe recovery state: {location}")
            if (
                location.get("status")
                == "archive_reuse_intent_cleanup_required"
            ):
                self._cancel_archive_reuse_intent(
                    self.prelock / BOOTSTRAP_CHILD,
                    root_fd=root_fd,
                )
                return {
                    "status": "archive_reuse_intent_cleaned",
                    "location": "archive",
                }
            if location.get("status") == "bootstrap_return_cleanup_required":
                transaction_id = location.get("transaction_id")
                if not isinstance(transaction_id, str):
                    raise JournalError(
                        "bootstrap return cleanup identity is missing"
                    )
                self._cleanup_bootstrap_return_marker(
                    transaction_id, root_fd=root_fd
                )
                return {
                    "status": "bootstrap_return_marker_cleaned",
                    "location": "prelock",
                }
            if location.get("status") == "archive_reuse_finalize_required":
                self._finalize_archive_reuse(
                    self.prelock / BOOTSTRAP_CHILD,
                    root_fd=root_fd,
                )
                return {
                    "status": "archive_reuse_finalized",
                    "location": "archive",
                }
            if location.get("status") == "archive_reuse_transaction":
                state = self.prelock / BOOTSTRAP_CHILD
                transaction_id = location.get("transaction_id")
                if not isinstance(transaction_id, str):
                    raise JournalError(
                        "archive reuse recovery identity is missing"
                    )
                result = self._recover_transaction(
                    state,
                    self._transaction_path(state, transaction_id),
                    root_fd=root_fd,
                )
                if result["status"] != "recovered":
                    raise JournalError(
                        "archive reuse recovery did not restore its baseline"
                    )
                self._finalize_archive_reuse(state, root_fd=root_fd)
                return result
            if location.get("status") == "bootstrap_cleanup_required":
                transaction_id = location.get("transaction_id")
                if not isinstance(transaction_id, str):
                    raise JournalError("bootstrap cleanup identity is missing")
                transaction_status = location.get("transaction_status")
                if not isinstance(transaction_status, str):
                    raise JournalError("bootstrap cleanup status is missing")
                self._finish_bootstrap_transition(
                    transaction_id, transaction_status, root_fd=root_fd
                )
                return {
                    "status": (
                        "bootstrap_state_cleaned"
                        if transaction_status == "committed"
                        else "bootstrap_state_archived"
                    ),
                    "location": "root",
                }
            if location.get("status") == "recovered_pending_cleanup":
                self._remove_recovered_prelock_state(root_fd=root_fd)
                return {"status": "recovered_state_cleaned", "location": "prelock"}
            if location.get("status") == "archive_intent_cleanup_required":
                self._clear_archive_transition(root_fd=root_fd)
                return {"status": "archive_intent_cleaned", "location": "root"}
            if location.get("status") == "archive_migration_required":
                archived = self.prelock / BOOTSTRAP_CHILD
                if archived.exists() or archived.is_symlink():
                    with self._open_state_handles(
                        archived, root_fd=root_fd
                    ) as (archived_fd, archived_transactions_fd):
                        archived_digest = self._validate_archive_marker(
                            archived,
                            state_fd=archived_fd,
                            transactions_fd=archived_transactions_fd,
                        )
                else:
                    self._archive_legacy_locked(root_fd=root_fd)
                    return {"status": "archive_recovered", "location": "prelock"}
                try:
                    root_state_metadata = os.stat(
                        ROOT_STATE, dir_fd=root_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    return {"status": "archive_ready", "location": "prelock"}
                if not stat.S_ISDIR(root_state_metadata.st_mode):
                    raise JournalError("root rollback state is unsafe")
                with self._open_state_handles(
                    self.state, root_fd=root_fd
                ) as (state_fd, _state_transactions_fd):
                    with _descriptor_cwd(state_fd) as anchored_state:
                        root_digest = _logical_state_digest(anchored_state)
                    if root_digest != archived_digest:
                        raise JournalError("dual rollback archive states differ")
                    raw = Path(
                        tempfile.mkdtemp(
                            prefix=f".{self.root.name}.bugate-retire-",
                            dir=self.root.parent,
                        )
                    )
                    parent_fd = os.open(
                        self.root.parent,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                    raw_fd = _open_child_directory(parent_fd, raw.name)
                    os.fchmod(raw_fd, 0o700)
                    os.fsync(raw_fd)
                    raw_identity = _fd_identity(raw_fd)
                    try:
                        _assert_child_binding(root_fd, ROOT_STATE, state_fd)
                        os.rename(
                            ROOT_STATE,
                            "retired-root-state",
                            src_dir_fd=root_fd,
                            dst_dir_fd=raw_fd,
                        )
                        os.fsync(root_fd)
                        os.fsync(raw_fd)
                        _remove_tree_at(
                            raw_fd,
                            "retired-root-state",
                            expected_identity=_fd_identity(state_fd),
                        )
                    finally:
                        try:
                            _remove_tree_at(
                                parent_fd,
                                raw.name,
                                expected_identity=raw_identity,
                            )
                        finally:
                            os.close(raw_fd)
                            os.close(parent_fd)
                return {"status": "archive_recovered", "location": "prelock"}
            state = self.state if location["location"] == "root" else self.prelock / BOOTSTRAP_CHILD
            identity = location["transaction_id"]
            tx = self._transaction_path(state, identity)
            result = self._recover_transaction(state, tx, root_fd=root_fd)
            if location.get("bootstrap_cleanup_required"):
                self._finish_bootstrap_transition(
                        identity, result["status"], root_fd=root_fd
                )
            if location.get("archive_after_commit"):
                if result["status"] == "committed":
                    self._archive_legacy_locked(root_fd=root_fd)
                elif result["status"] == "recovered":
                    self._clear_archive_transition(root_fd=root_fd)
                else:
                    raise JournalError(
                        "rollback recovery did not reach a terminal archive state"
                    )
            return result

    def _prepare_transaction(
        self,
        state: Path,
        transaction_id: str,
        kind: str,
        operations: list[Operation],
        *,
        payload_sources: Mapping[str, Path] | None,
        payload_bytes: Mapping[str, bytes] | None,
        worker_files: Mapping[str, Path | bytes] | None,
        input_files: Mapping[str, Path | bytes] | None,
        source_transaction: str | None,
        root_fd: int,
        atomic_publish: bool = False,
    ) -> Path:
        if atomic_publish:
            return self._prepare_transaction_atomically(
                state,
                transaction_id,
                kind,
                operations,
                payload_sources=payload_sources,
                payload_bytes=payload_bytes,
                worker_files=worker_files,
                input_files=input_files,
                source_transaction=source_transaction,
                root_fd=root_fd,
            )
        tx = self._transaction_path(state, transaction_id)
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            if self._current(
                state, state_fd=state_fd, root_fd=root_fd
            ) is not None:
                raise JournalError("updater state already has an active transaction")
            self._require_transaction_capacity(transactions_fd)
            transaction_fd = _create_child_directory(
                transactions_fd, transaction_id
            )
            current_published = False
            try:
                local_directories = {
                    "stage": self._stage_payloads(
                        transaction_fd,
                        operations,
                        payload_sources,
                        payload_bytes,
                    ),
                    "worker": self._copy_bundle(
                        transaction_fd, "worker", worker_files
                    ),
                    "input": self._copy_bundle(
                        transaction_fd, "input", input_files
                    ),
                    "backup": self._backup(
                        transaction_fd, operations, root_fd=root_fd
                    ),
                }
                directory_bindings = self._directory_bindings(
                    state_fd,
                    transactions_fd,
                    transaction_fd,
                    local_directories,
                )
                journal = self._journal_document(
                    transaction_id,
                    kind,
                    "prepared",
                    operations,
                    source_transaction=source_transaction,
                    directory_bindings=directory_bindings,
                )
                self._write_journal(
                    Path("."),
                    journal,
                    state=state,
                    state_fd=state_fd,
                    root_fd=root_fd,
                    transaction_fd=transaction_fd,
                    transactions_fd=transactions_fd,
                    transaction_id=transaction_id,
                )
                self._inject("after_prepare_transaction_publish")
                self._set_current(
                    state,
                    transaction_id,
                    state_fd=state_fd,
                    root_fd=root_fd,
                )
                current_published = True
                os.fsync(transaction_fd)
                os.fsync(transactions_fd)
            except BaseException:
                if not current_published:
                    try:
                        self._assert_state_binding(
                            state, root_fd, state_fd
                        )
                        _assert_child_binding(
                            transactions_fd,
                            transaction_id,
                            transaction_fd,
                        )
                        _remove_tree_at(
                            transactions_fd,
                            transaction_id,
                            expected_identity=_fd_identity(transaction_fd),
                        )
                    except BaseException:
                        # A changed binding is deliberately retained for
                        # diagnosis; never follow or delete its replacement.
                        pass
                raise
            finally:
                os.close(transaction_fd)
        return tx

    def _prepare_transaction_atomically(
        self,
        state: Path,
        transaction_id: str,
        kind: str,
        operations: list[Operation],
        *,
        payload_sources: Mapping[str, Path] | None,
        payload_bytes: Mapping[str, bytes] | None,
        worker_files: Mapping[str, Path | bytes] | None,
        input_files: Mapping[str, Path | bytes] | None,
        source_transaction: str | None,
        root_fd: int,
    ) -> Path:
        """Prepare privately, then publish one complete transaction tree."""

        tx = self._transaction_path(state, transaction_id)
        with self._open_state_handles(state, root_fd=root_fd) as (
            state_fd,
            transactions_fd,
        ):
            if self._current(
                state, state_fd=state_fd, root_fd=root_fd
            ) is not None:
                raise JournalError(
                    "updater state already has an active transaction"
                )
            self._require_transaction_capacity(transactions_fd)
            try:
                os.stat(
                    transaction_id,
                    dir_fd=transactions_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise JournalError("transaction id already exists")

            raw = Path(
                tempfile.mkdtemp(
                    prefix=f".{self.root.name}.bugate-prepare-",
                    dir=self.root.parent,
                )
            )
            raw_parent_fd = os.open(
                self.root.parent,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            raw_fd = _open_child_directory(raw_parent_fd, raw.name)
            os.fchmod(raw_fd, 0o700)
            os.fsync(raw_fd)
            raw_identity = _fd_identity(raw_fd)
            transaction_fd = _create_child_directory(
                raw_fd, transaction_id
            )
            transaction_identity = _fd_identity(transaction_fd)
            transaction_published = False
            try:
                self._inject("after_prepare_transaction_dir_create")
                local_directories = {
                    "stage": self._stage_payloads(
                        transaction_fd,
                        operations,
                        payload_sources,
                        payload_bytes,
                    ),
                    "worker": self._copy_bundle(
                        transaction_fd, "worker", worker_files
                    ),
                    "input": self._copy_bundle(
                        transaction_fd, "input", input_files
                    ),
                    "backup": self._backup(
                        transaction_fd, operations, root_fd=root_fd
                    ),
                }
                self._inject("after_prepare_bundles_before_journal")
                directory_bindings = self._directory_bindings(
                    state_fd,
                    transactions_fd,
                    transaction_fd,
                    local_directories,
                )
                journal = self._journal_document(
                    transaction_id,
                    kind,
                    "prepared",
                    operations,
                    source_transaction=source_transaction,
                    directory_bindings=directory_bindings,
                )
                _atomic_json_at(transaction_fd, "journal.json", journal)
                with _descriptor_cwd(transaction_fd) as anchored:
                    self._validate_journal(
                        anchored,
                        transaction_fd=transaction_fd,
                        state_fd=state_fd,
                        transactions_fd=transactions_fd,
                        expected_transaction_id=transaction_id,
                    )
                os.fsync(transaction_fd)
                os.fsync(raw_fd)
                _exclusive_rename_at(
                    raw_fd,
                    transaction_id,
                    transactions_fd,
                    transaction_id,
                )
                transaction_published = True
                _assert_child_binding(
                    transactions_fd, transaction_id, transaction_fd
                )
                self._inject("after_prepare_transaction_publish")
                self._set_current(
                    state,
                    transaction_id,
                    state_fd=state_fd,
                    root_fd=root_fd,
                )
                os.fsync(transaction_fd)
                os.fsync(transactions_fd)
            finally:
                os.close(transaction_fd)
                if not transaction_published:
                    try:
                        _remove_tree_at(
                            raw_fd,
                            transaction_id,
                            expected_identity=transaction_identity,
                        )
                    except BaseException:
                        # Preserve a changed private binding for inspection.
                        pass
                try:
                    if not os.listdir(raw_fd):
                        _remove_tree_at(
                            raw_parent_fd,
                            raw.name,
                            expected_identity=raw_identity,
                        )
                finally:
                    os.close(raw_fd)
                    os.close(raw_parent_fd)
        return tx

    def _execute_prepared(
        self,
        state: Path,
        transaction_id: str,
        *,
        skip_operation_ids: Iterable[str] = (),
        precommit_verify: Callable[[], None] | None = None,
        final_verify: Callable[[], None] | None = None,
        root_fd: int,
    ) -> dict[str, Any]:
        """Execute a durable prepared journal, writing installed lock last."""

        with self._open_transaction_handles(
            state, transaction_id, root_fd=root_fd
        ) as (
            state_fd,
            transactions_fd,
            transaction_fd,
            tx,
            journal,
            operations,
        ):
            return self._execute_prepared_open(
                state,
                state_fd,
                transactions_fd,
                transaction_fd,
                tx,
                journal,
                operations,
                transaction_id=transaction_id,
                skip_operation_ids=skip_operation_ids,
                precommit_verify=precommit_verify,
                final_verify=final_verify,
                root_fd=root_fd,
            )

    def _execute_prepared_open(
        self,
        state: Path,
        state_fd: int,
        transactions_fd: int,
        transaction_fd: int,
        tx: Path,
        journal: Mapping[str, Any],
        operations: list[Operation],
        *,
        transaction_id: str,
        skip_operation_ids: Iterable[str],
        precommit_verify: Callable[[], None] | None,
        final_verify: Callable[[], None] | None,
        root_fd: int,
    ) -> dict[str, Any]:
        if journal["status"] not in {"prepared", "applying"}:
            raise JournalError("worker requires a prepared/applying transaction")
        directory_bindings = journal["directory_bindings"]
        skipped = set(skip_operation_ids)
        ordered = [item for item in self._ordered(operations) if item[1].id not in skipped]
        lock_items = [
            item
            for item in ordered
            if item[1].id == "metadata:installed-lock"
            or item[1].target_path == f"{self.vendor_dir}/{contract.INSTALLED_LOCK_PATH}"
        ]
        if len(lock_items) > 1:
            raise JournalError("transaction contains multiple installed-lock operations")
        content_items = [item for item in ordered if item not in lock_items]
        applying = self._journal_document(
            transaction_id,
            journal["kind"],
            "applying",
            operations,
            source_transaction=journal["source_transaction"],
            error=journal.get("error"),
            directory_bindings=directory_bindings,
        )
        self._write_journal(
            tx,
            applying,
            state=state,
            state_fd=state_fd,
            root_fd=root_fd,
            transaction_fd=transaction_fd,
            transactions_fd=transactions_fd,
            transaction_id=transaction_id,
        )
        try:
            self._inject("after_prepare")
            for index, operation in content_items:
                self._inject(f"before_mutation:{operation.id}")
                self._install_one(
                    transaction_fd,
                    directory_bindings,
                    index,
                    operation,
                    root_fd=root_fd,
                )
                self._inject(f"after_mutation:{operation.id}")
            # The precommit gate verifies every content/hook post-image while
            # the old installed lock is still the visible committed baseline.
            for _index, operation in content_items:
                actual = _observe_at(
                    root_fd,
                    operation.target_path,
                    allow_missing_parents=True,
                    allow_non_directory_parents=operation.post["type"] == "absent",
                )
                if actual != dict(operation.post):
                    raise ThirdPartyDriftError(
                        f"precommit post-image drifted: {operation.target_path}"
                    )
            if precommit_verify is not None:
                precommit_verify()
            self._inject("after_precommit_verify")
            for index, operation in lock_items:
                self._inject("before_installed_lock")
                self._install_one(
                    transaction_fd,
                    directory_bindings,
                    index,
                    operation,
                    root_fd=root_fd,
                )
                self._inject("after_installed_lock")
            if final_verify is not None:
                final_verify()
            self._inject("after_verify")
            committed = self._journal_document(
                transaction_id,
                journal["kind"],
                "committed",
                operations,
                source_transaction=journal["source_transaction"],
                directory_bindings=directory_bindings,
            )
            report = {
                "schema_version": STATE_SCHEMA,
                "transaction_id": transaction_id,
                "kind": journal["kind"],
                "status": "committed",
                "no_op": False,
                "source_transaction": journal["source_transaction"],
                "operations": [item.to_dict() for item in operations],
            }
            input_fd = _open_child_directory(
                transaction_fd,
                "input",
                expected_identity=directory_bindings["local_directories"]["input"],
            )
            try:
                try:
                    plan_metadata = os.stat(
                        "plan.json", dir_fd=input_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    plan_metadata = None
                if plan_metadata is not None:
                    if not stat.S_ISREG(plan_metadata.st_mode):
                        raise JournalError(
                            "verified transaction plan input is not a regular file"
                        )
                    plan_bytes = _read_file_at(input_fd, "plan.json")
                    try:
                        plan = json.loads(plan_bytes)
                    except json.JSONDecodeError as exc:
                        raise JournalError(
                            "verified transaction plan input is invalid"
                        ) from exc
                    if not isinstance(plan, dict) or plan_bytes != _canonical(plan):
                        raise JournalError(
                            "verified transaction plan input is not canonical"
                        )
                    supplied_digest = plan.get("plan_digest")
                    digest_payload = dict(plan)
                    digest_payload.pop("plan_digest", None)
                    if supplied_digest != contract.sha256_bytes(
                        _canonical(digest_payload)
                    ):
                        raise JournalError("verified transaction plan digest mismatch")
                    managed_summary: dict[str, int] = {}
                    for change in plan.get("managed_changes", []):
                        classification = change.get("classification", "unknown")
                        managed_summary[classification] = managed_summary.get(
                            classification, 0
                        ) + 1
                    report.update(
                        {
                            "decision": "GO",
                            "engine_updated": not bool(plan.get("no_op")),
                            "from_version": plan.get("from_version"),
                            "to_version": plan.get("to_version"),
                            "release_digest": plan.get("release_digest"),
                            "archive_sha256": plan.get("archive_sha256"),
                            "manifest_sha256": plan.get("manifest_sha256"),
                            "source_kind": plan.get("source_kind"),
                            "managed_summary": managed_summary,
                            "hook_changes": plan.get("hook_changes", []),
                            "profile_migration": plan.get("profile_compatibility"),
                            "codex_hook_hash_changed": bool(
                                plan.get("codex_hook_hash_changed")
                            ),
                            "new_session_required": bool(
                                plan.get("new_session_required")
                            ),
                            "rollback_available": True,
                            "memory_checked": False,
                            "role_governance_activated": False,
                        }
                    )
            finally:
                os.close(input_fd)
            report.update(
                self._load_report_metadata(
                    tx,
                    transaction_fd=transaction_fd,
                    directory_bindings=directory_bindings,
                )
            )
            sealed_report = _sealed(report)
            _atomic_json_at(
                transaction_fd, "report.pending.json", sealed_report
            )
            pending_fd = _open_regular_child(
                transaction_fd,
                "report.pending.json",
                label="pending transaction report",
            )
            try:
                self._assert_report_candidate(
                    transaction_fd,
                    "report.pending.json",
                    pending_fd,
                    sealed_report,
                )
                self._inject("after_report_pending")
                self._assert_report_candidate(
                    transaction_fd,
                    "report.pending.json",
                    pending_fd,
                    sealed_report,
                )
                self._write_journal(
                    tx,
                    committed,
                    state=state,
                    state_fd=state_fd,
                    root_fd=root_fd,
                    transaction_fd=transaction_fd,
                    transactions_fd=transactions_fd,
                    transaction_id=transaction_id,
                )
                self._assert_report_candidate(
                    transaction_fd,
                    "report.pending.json",
                    pending_fd,
                    sealed_report,
                )
                self._inject("after_journal_commit")
                self._assert_report_candidate(
                    transaction_fd,
                    "report.pending.json",
                    pending_fd,
                    sealed_report,
                )
                os.replace(
                    "report.pending.json",
                    "report.json",
                    src_dir_fd=transaction_fd,
                    dst_dir_fd=transaction_fd,
                )
                os.fsync(transaction_fd)
                self._assert_report_candidate(
                    transaction_fd,
                    "report.json",
                    pending_fd,
                    sealed_report,
                )
                self._inject("after_commit")
                self._assert_report_candidate(
                    transaction_fd,
                    "report.json",
                    pending_fd,
                    sealed_report,
                )
                self._clear_current(
                    state, state_fd=state_fd, root_fd=root_fd
                )
                self._assert_report_candidate(
                    transaction_fd,
                    "report.json",
                    pending_fd,
                    sealed_report,
                )
            finally:
                os.close(pending_fd)
            try:
                _clear_directory_at(
                    transaction_fd,
                    "stage",
                    expected_identity=directory_bindings["local_directories"]["stage"],
                )
                os.fsync(transaction_fd)
            except (OSError, TransactionError):
                # Committed state is authoritative; retained staging is
                # diagnostic local state, not a reason to relabel success.
                pass
            return report
        except BaseException as exc:
            if isinstance(exc, JournalHierarchyBindingError):
                # Canonical current.json may now refer to a same-named copy.
                # Roll back strictly through the transaction/backup fds that
                # were pinned before mutation; never consult or write the
                # replacement hierarchy.
                self._restore_preimages_open(
                    transaction_fd,
                    directory_bindings,
                    operations,
                    root_fd=root_fd,
                )
                raise
            if isinstance(exc, ReportIntegrityError):
                # Report publication is part of commit.  A changed candidate
                # invalidates success even when the committed journal bytes
                # are already durable, so force an in-place terminal recovery.
                durable, durable_operations = self._validate_journal(
                    tx,
                    transaction_fd=transaction_fd,
                    state_fd=state_fd,
                    transactions_fd=transactions_fd,
                    expected_transaction_id=transaction_id,
                    required_local_directories={"stage", "backup"},
                    validate_tree=False,
                )
                self._recover_transaction_open(
                    state,
                    state_fd,
                    transactions_fd,
                    transaction_fd,
                    tx,
                    durable,
                    durable_operations,
                    root_fd=root_fd,
                    force_rollback_committed=True,
                )
                raise
            current = self._current(
                state, state_fd=state_fd, root_fd=root_fd
            )
            if current == transaction_id:
                durable, _durable_operations = self._validate_journal(
                    tx,
                    transaction_fd=transaction_fd,
                    state_fd=state_fd,
                    transactions_fd=transactions_fd,
                    expected_transaction_id=transaction_id,
                    required_local_directories={"stage", "backup"},
                    validate_tree=False,
                )
                if durable["status"] == "committed":
                    self._recover_transaction_open(
                        state,
                        state_fd,
                        transactions_fd,
                        transaction_fd,
                        tx,
                        durable,
                        _durable_operations,
                        root_fd=root_fd,
                    )
                    final = _read_canonical_json_at(
                        transaction_fd,
                        "report.json",
                        label="transaction report",
                    )
                    return {key: value for key, value in final.items() if key != "self_digest"}
                failed = self._journal_document(
                    transaction_id,
                    journal["kind"],
                    "applying",
                    operations,
                    source_transaction=journal["source_transaction"],
                    error=str(exc),
                    directory_bindings=directory_bindings,
                )
                self._write_journal(
                    tx,
                    failed,
                    state=state,
                    state_fd=state_fd,
                    root_fd=root_fd,
                    transaction_fd=transaction_fd,
                    transactions_fd=transactions_fd,
                    transaction_id=transaction_id,
                )
                _atomic_json_at(
                    transaction_fd,
                    "failure-report.json",
                    _sealed(
                        {
                            "schema_version": STATE_SCHEMA,
                            "transaction_id": transaction_id,
                            "kind": journal["kind"],
                            "status": "recovering",
                            "error_type": type(exc).__name__,
                            "error": self._safe_error(exc),
                        }
                    ),
                )
                self._recover_transaction_open(
                    state,
                    state_fd,
                    transactions_fd,
                    transaction_fd,
                    tx,
                    failed,
                    operations,
                    root_fd=root_fd,
                )
            raise

    def apply(
        self,
        operations: Iterable[Operation | Mapping[str, Any]],
        *,
        payload_sources: Mapping[str, Path] | None = None,
        payload_bytes: Mapping[str, bytes] | None = None,
        worker_files: Mapping[str, Path | bytes] | None = None,
        input_files: Mapping[str, Path | bytes] | None = None,
        base_verify: Callable[[], None] | None = None,
        precommit_verify: Callable[[], None] | None = None,
        verify: Callable[[], None] | None = None,
        execute_worker: bool = False,
        execute_worker_verify: bool = True,
        bootstrap: bool = False,
        gitignore_operation_id: str | None = None,
        transaction_id: str | None = None,
        kind: str = "apply",
        source_transaction: str | None = None,
        report_metadata: Mapping[str, Any] | None = None,
        archive_after_commit: bool = False,
    ) -> dict[str, Any]:
        """Apply exact operations and return the committed transaction report.

        An empty operation sequence is the same-version no-op fast path and
        performs no state write and acquires no persistent state.
        """

        normalized = [
            item if isinstance(item, Operation) else Operation.from_mapping(item)
            for item in operations
        ]
        if not normalized:
            # A same-version plan is write-free, not state-blind.  Refuse any
            # malformed or recoverable updater state and leave explicit
            # recovery to the caller; a genuinely clean no-op persists
            # nothing.
            with self.workspace_lock(shared=True):
                pending = self.recovery_required()
            if pending is not None:
                raise JournalError(
                    f"same-version no-op refused unsafe prior transaction state: {pending}"
                )
            return {"status": "no-op", "transaction_id": None, "state_written": False}
        if len({item.id for item in normalized}) != len(normalized):
            raise JournalError("duplicate operation id")
        if len({item.target_path.casefold() for item in normalized}) != len(normalized):
            raise JournalError("duplicate operation target")
        identity = transaction_id or self._new_id()
        if not TRANSACTION_ID_RE.fullmatch(identity):
            raise JournalError("invalid transaction id")
        if archive_after_commit and (
            kind != "rollback"
            or bootstrap
            or not isinstance(source_transaction, str)
            or not TRANSACTION_ID_RE.fullmatch(source_transaction)
        ):
            raise JournalError(
                "legacy archival is valid only for an identified rollback transaction"
            )
        prepared_inputs: dict[str, Path | bytes] = dict(input_files or {})
        if report_metadata is not None:
            if "report-metadata.json" in prepared_inputs:
                raise JournalError("transaction report metadata input is duplicated")
            prepared_inputs["report-metadata.json"] = _canonical(
                self._report_metadata(report_metadata)
            )
        with self.workspace_lock() as held_lock, self._signals():
            root_fd = held_lock.fd
            if root_fd is None:
                raise TransactionError("workspace lock descriptor is unavailable")
            pending = self.recovery_required()
            if pending is not None:
                if pending.get("status") in {
                    "foreign_plan_lock",
                    "invalid",
                    "unsupported_legacy_archive",
                }:
                    raise JournalError(f"unsafe prior transaction state: {pending}")
                if (
                    pending.get("status")
                    == "archive_reuse_intent_cleanup_required"
                ):
                    self._cancel_archive_reuse_intent(
                        self.prelock / BOOTSTRAP_CHILD,
                        root_fd=root_fd,
                    )
                elif (
                    pending.get("status")
                    == "bootstrap_return_cleanup_required"
                ):
                    cleanup_identity = pending.get("transaction_id")
                    if not isinstance(cleanup_identity, str):
                        raise JournalError(
                            "bootstrap return cleanup identity is missing"
                        )
                    self._cleanup_bootstrap_return_marker(
                        cleanup_identity, root_fd=root_fd
                    )
                elif pending.get("status") == "archive_reuse_finalize_required":
                    self._finalize_archive_reuse(
                        self.prelock / BOOTSTRAP_CHILD,
                        root_fd=root_fd,
                    )
                elif pending.get("status") == "archive_reuse_transaction":
                    reuse_state = self.prelock / BOOTSTRAP_CHILD
                    reuse_identity = pending.get("transaction_id")
                    if not isinstance(reuse_identity, str):
                        raise JournalError(
                            "archive reuse recovery identity is missing"
                        )
                    recovered = self._recover_transaction(
                        reuse_state,
                        self._transaction_path(
                            reuse_state, reuse_identity
                        ),
                        root_fd=root_fd,
                    )
                    if recovered["status"] != "recovered":
                        raise JournalError(
                            "archive reuse recovery did not restore its baseline"
                        )
                    self._finalize_archive_reuse(
                        reuse_state, root_fd=root_fd
                    )
                elif pending.get("status") == "recovered_pending_cleanup":
                    self._validate_state_dir(self.prelock / BOOTSTRAP_CHILD)
                elif pending.get("status") == "bootstrap_cleanup_required":
                    cleanup_identity = pending.get("transaction_id")
                    if not isinstance(cleanup_identity, str):
                        raise JournalError("bootstrap cleanup identity is missing")
                    cleanup_status = pending.get("transaction_status")
                    if not isinstance(cleanup_status, str):
                        raise JournalError("bootstrap cleanup status is missing")
                    self._finish_bootstrap_transition(
                        cleanup_identity, cleanup_status, root_fd=root_fd
                    )
                elif pending.get("status") in {
                    "archive_intent_cleanup_required",
                    "archive_migration_required",
                }:
                    raise JournalError(
                        "prior legacy archive transition requires recovery before apply"
                    )
                else:
                    state_for_recovery = self.state if pending["location"] == "root" else self.prelock / BOOTSTRAP_CHILD
                    recovered = self._recover_transaction(
                        state_for_recovery,
                        self._transaction_path(state_for_recovery, pending["transaction_id"]),
                        root_fd=root_fd,
                    )
                    if pending.get("bootstrap_cleanup_required"):
                        self._finish_bootstrap_transition(
                            pending["transaction_id"],
                            recovered["status"],
                            root_fd=root_fd,
                        )
                    if pending.get("archive_after_commit"):
                        if recovered["status"] == "committed":
                            self._archive_legacy_locked(root_fd=root_fd)
                        elif recovered["status"] == "recovered":
                            self._clear_archive_transition(root_fd=root_fd)
                        else:
                            raise JournalError(
                                "rollback recovery did not reach a terminal archive state"
                            )
                if pending["location"] == "prelock":
                    # A recovered bootstrap journal is no longer active.  Its
                    # exact sentinel granted ownership for this cleanup only;
                    # it must never be reused as a new transaction container.
                    self._remove_recovered_prelock_state(root_fd=root_fd)
            if base_verify is not None:
                base_verify()
            for operation in normalized:
                actual = _observe_at(
                    root_fd,
                    operation.target_path,
                    allow_missing_parents=True,
                    allow_non_directory_parents=operation.pre["type"] == "absent",
                )
                if actual != dict(operation.pre):
                    raise ThirdPartyDriftError(f"plan base drifted: {operation.target_path}")

            state = self.state
            if bootstrap:
                if gitignore_operation_id is None:
                    raise TransactionError("bootstrap requires the marked .gitignore operation")
                git_index = next(
                    (index for index, item in enumerate(normalized) if item.id == gitignore_operation_id),
                    None,
                )
                if git_index is None or normalized[git_index].target_path != ".gitignore":
                    raise TransactionError("bootstrap .gitignore operation is absent")
                if self.state.exists() or self.state.is_symlink():
                    raise JournalError("root updater state already exists during bootstrap")
                archived_state = self.prelock / BOOTSTRAP_CHILD
                reuse_archived = (
                    archived_state.is_dir()
                    and not archived_state.is_symlink()
                    and (archived_state / ARCHIVED_MARKER).is_file()
                )
                if reuse_archived:
                    with self._open_state_handles(
                        archived_state, root_fd=root_fd
                    ) as (archived_state_fd, archived_transactions_fd):
                        self._begin_archive_reuse_open(
                            archived_state,
                            archived_state_fd,
                            archived_transactions_fd,
                            identity,
                        )
                    try:
                        self._inject("after_archive_reuse_intent")
                        tx = self._prepare_transaction(
                            archived_state, identity, kind, normalized,
                            payload_sources=payload_sources, payload_bytes=payload_bytes,
                            worker_files=worker_files, input_files=prepared_inputs,
                            source_transaction=source_transaction,
                            root_fd=root_fd,
                            atomic_publish=True,
                        )
                        self._inject("after_archive_reuse_prepare")
                    except BaseException:
                        pending_reuse = self.recovery_required()
                        if (
                            pending_reuse is not None
                            and pending_reuse.get("status")
                            == "archive_reuse_intent_cleanup_required"
                        ):
                            self._cancel_archive_reuse_intent(
                                archived_state, root_fd=root_fd
                            )
                        elif (
                            pending_reuse is not None
                            and pending_reuse.get("status")
                            == "archive_reuse_transaction"
                        ):
                            recovered_reuse = self._recover_transaction(
                                archived_state,
                                self._transaction_path(
                                    archived_state, identity
                                ),
                                root_fd=root_fd,
                            )
                            if recovered_reuse["status"] != "recovered":
                                raise JournalError(
                                    "archive reuse prepare failure did not recover"
                                )
                            self._finalize_archive_reuse(
                                archived_state, root_fd=root_fd
                            )
                        raise
                if (self.prelock / "sentinel.json").is_file():
                    raise JournalError(
                        "direct plan.lock rollback state is unsupported; "
                        "history migration is required"
                    )
                if not reuse_archived and (self.prelock.exists() or self.prelock.is_symlink()):
                    raise JournalError("existing plan.lock is not an active recoverable transaction")
                if not reuse_archived:
                    parent = self.root.parent
                    if os.lstat(parent).st_dev != os.lstat(self.root).st_dev:
                        raise UnsupportedAtomicRename("no same-filesystem staging parent outside repo")
                    raw = Path(tempfile.mkdtemp(prefix=f".{self.root.name}.bugate-bootstrap-", dir=parent))
                    parent_fd = os.open(
                        parent,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                    raw_fd = _open_child_directory(parent_fd, raw.name)
                    os.fchmod(raw_fd, 0o700)
                    os.fsync(raw_fd)
                    raw_identity = _fd_identity(raw_fd)
                    staged_plan_lock = raw / "plan.lock"
                    staged_state = staged_plan_lock / BOOTSTRAP_CHILD
                    staged_plan_lock.mkdir(mode=0o700)
                    os.chmod(staged_plan_lock, 0o700)
                    self._initialize_state(staged_state)
                    tx = self._prepare_transaction(
                        staged_state, identity, kind, normalized,
                        payload_sources=payload_sources, payload_bytes=payload_bytes,
                        worker_files=worker_files, input_files=prepared_inputs,
                        source_transaction=source_transaction,
                        root_fd=root_fd,
                    )
                    _fsync_directory(staged_plan_lock)
                    self._inject("before_bootstrap_publish")
                    try:
                        staged_plan_lock_fd = _open_child_directory(
                            raw_fd, "plan.lock"
                        )
                        destination_parent_fd, destination_leaf = _open_parent_at(
                            root_fd, f"{self.vendor_dir}/plan.lock"
                        )
                        assert destination_parent_fd is not None
                        try:
                            _assert_child_binding(
                                raw_fd, "plan.lock", staged_plan_lock_fd
                            )
                            _exclusive_rename_at(
                                raw_fd,
                                "plan.lock",
                                destination_parent_fd,
                                destination_leaf,
                            )
                        finally:
                            os.close(destination_parent_fd)
                            os.close(staged_plan_lock_fd)
                    finally:
                        try:
                            _remove_tree_at(
                                parent_fd,
                                raw.name,
                                expected_identity=raw_identity,
                            )
                        finally:
                            os.close(raw_fd)
                            os.close(parent_fd)
                    self._inject("after_bootstrap_publish")
                state = self.prelock / BOOTSTRAP_CHILD
                tx = self._transaction_path(state, identity)
                try:
                    if reuse_archived:
                        with self._open_state_handles(
                            state, root_fd=root_fd
                        ) as (reuse_state_fd, _reuse_transactions_fd):
                            self._activate_archive_reuse_open(
                                reuse_state_fd, identity
                            )
                        self._inject("after_archive_reuse_activate")
                    with self._open_transaction_handles(
                        state, identity, root_fd=root_fd
                    ) as (
                        state_fd,
                        transactions_fd,
                        transaction_fd,
                        anchored_tx,
                        prepared_journal,
                        prepared_operations,
                    ):
                        journal = self._journal_document(
                            identity,
                            kind,
                            "applying",
                            prepared_operations,
                            source_transaction=source_transaction,
                            directory_bindings=prepared_journal[
                                "directory_bindings"
                            ],
                        )
                        self._write_journal(
                            anchored_tx,
                            journal,
                            state=state,
                            state_fd=state_fd,
                            root_fd=root_fd,
                            transaction_fd=transaction_fd,
                            transactions_fd=transactions_fd,
                            transaction_id=identity,
                        )
                        self._install_one(
                            transaction_fd,
                            journal["directory_bindings"],
                            git_index,
                            normalized[git_index],
                            root_fd=root_fd,
                        )
                        self._inject("after_gitignore")
                        if b"/.bugate-update/" not in _read_file_at(
                            root_fd, ".gitignore"
                        ):
                            raise TransactionError(
                                "bootstrap gitignore post-image does not ignore root state"
                            )
                        _atomic_json_at(
                            state_fd,
                            BOOTSTRAP_TRANSITION,
                            self._bootstrap_transition(identity),
                        )
                        prelock_parent_fd, prelock_leaf = _open_parent_at(
                            root_fd, f"{self.vendor_dir}/plan.lock"
                        )
                        assert prelock_parent_fd is not None
                        try:
                            prelock_fd = _open_child_directory(
                                prelock_parent_fd, prelock_leaf
                            )
                            try:
                                _assert_child_binding(
                                    prelock_fd, BOOTSTRAP_CHILD, state_fd
                                )
                                _exclusive_rename_at(
                                    prelock_fd,
                                    BOOTSTRAP_CHILD,
                                    root_fd,
                                    ROOT_STATE,
                                )
                            finally:
                                os.close(prelock_fd)
                        finally:
                            os.close(prelock_parent_fd)
                    self._inject("after_root_state_publish")
                    self._cleanup_bootstrap_transition(
                        identity,
                        root_fd=root_fd,
                        retain_transition=True,
                    )
                    state = self.state
                    tx = self._transaction_path(state, identity)
                    self._inject("after_root_state_migration")
                except BaseException as exc:
                    active_state = self.state if self.state.exists() else self.prelock / BOOTSTRAP_CHILD
                    active_tx = self._transaction_path(active_state, identity)
                    if self._current(active_state, root_fd=root_fd) == identity:
                        with self._open_transaction_handles(
                            active_state, identity, root_fd=root_fd
                        ) as (
                            active_state_fd,
                            transactions_fd,
                            transaction_fd,
                            anchored_tx,
                            active_journal,
                            active_operations,
                        ):
                            failed = self._journal_document(
                                identity,
                                kind,
                                "applying",
                                active_operations,
                                source_transaction=source_transaction,
                                error=str(exc),
                                directory_bindings=active_journal[
                                    "directory_bindings"
                                ],
                            )
                            self._write_journal(
                                anchored_tx,
                                failed,
                                state=active_state,
                                state_fd=active_state_fd,
                                root_fd=root_fd,
                                transaction_fd=transaction_fd,
                                transactions_fd=transactions_fd,
                                transaction_id=identity,
                            )
                        recovered = self._recover_transaction(
                            active_state, active_tx, root_fd=root_fd
                        )
                        if active_state == self.state:
                            self._finish_bootstrap_transition(
                                identity,
                                recovered["status"],
                                root_fd=root_fd,
                            )
                        elif reuse_archived:
                            if recovered["status"] != "recovered":
                                raise JournalError(
                                    "archive reuse bootstrap failure did not recover"
                                )
                            self._finalize_archive_reuse(
                                active_state, root_fd=root_fd
                            )
                    raise
                skipped = {normalized[git_index].id}
            else:
                if self.state.exists() or self.state.is_symlink():
                    self._validate_state_dir(self.state, root_fd=root_fd)
                else:
                    self._initialize_state(self.state, root_fd=root_fd)
                if archive_after_commit:
                    with self._open_state_handles(
                        self.state, root_fd=root_fd
                    ) as (archive_state_fd, archive_transactions_fd):
                        # The rollback archive intent is persistent state.  A
                        # full history must fail before that marker is written.
                        self._require_transaction_capacity(
                            archive_transactions_fd
                        )
                        _atomic_json_at(
                            archive_state_fd,
                            ARCHIVE_TRANSITION,
                            self._rollback_archive_transition(
                                identity, source_transaction
                            ),
                        )
                    self._inject("after_archive_intent")
                tx = self._prepare_transaction(
                    self.state, identity, kind, normalized,
                    payload_sources=payload_sources, payload_bytes=payload_bytes,
                    worker_files=worker_files, input_files=prepared_inputs,
                    source_transaction=source_transaction,
                    root_fd=root_fd,
                )
                skipped = set()

            if execute_worker:
                with self._open_transaction_handles(
                    state, identity, root_fd=root_fd
                ) as (
                    state_fd,
                    transactions_fd,
                    transaction_fd,
                    anchored_tx,
                    prepared_journal,
                    _prepared_operations,
                ):
                    directory_bindings = prepared_journal["directory_bindings"]
                    worker_fd = _open_child_directory(
                        transaction_fd,
                        "worker",
                        expected_identity=directory_bindings[
                            "local_directories"
                        ]["worker"],
                    )
                    lock_fd = held_lock.make_inheritable()
                    command = [
                        sys.executable,
                        "bugate_update_transaction.py",
                        "__transaction-worker",
                        str(self.root),
                        self.vendor_dir,
                        identity,
                        str(lock_fd),
                        ",".join(sorted(skipped)),
                        "verify" if execute_worker_verify else "physical-only",
                    ]

                    def recover_worker_failure(error: BaseException) -> None:
                        if self._current(
                            state, state_fd=state_fd, root_fd=root_fd
                        ) != identity:
                            return
                        active, active_operations = self._validate_journal(
                            anchored_tx,
                            transaction_fd=transaction_fd,
                            state_fd=state_fd,
                            transactions_fd=transactions_fd,
                            expected_transaction_id=identity,
                            required_local_directories={"stage", "backup"},
                            validate_tree=False,
                        )
                        if active["status"] == "committed":
                            self._recover_transaction_open(
                                state,
                                state_fd,
                                transactions_fd,
                                transaction_fd,
                                anchored_tx,
                                active,
                                active_operations,
                                root_fd=root_fd,
                            )
                            return
                        failed = self._journal_document(
                            identity,
                            active["kind"],
                            "applying",
                            active_operations,
                            source_transaction=active["source_transaction"],
                            error=self._safe_error(error),
                            directory_bindings=active[
                                "directory_bindings"
                            ],
                        )
                        self._write_journal(
                            anchored_tx,
                            failed,
                            state=state,
                            state_fd=state_fd,
                            root_fd=root_fd,
                            transaction_fd=transaction_fd,
                            transactions_fd=transactions_fd,
                            transaction_id=identity,
                        )
                        _atomic_json_at(
                            transaction_fd,
                            "failure-report.json",
                            _sealed(
                                {
                                    "schema_version": STATE_SCHEMA,
                                    "transaction_id": identity,
                                    "kind": active["kind"],
                                    "status": "recovering",
                                    "error_type": type(error).__name__,
                                    "error": self._safe_error(error),
                                }
                            ),
                        )
                        self._recover_transaction_open(
                            state,
                            state_fd,
                            transactions_fd,
                            transaction_fd,
                            anchored_tx,
                            failed,
                            active_operations,
                            root_fd=root_fd,
                        )

                    try:
                        with _descriptor_cwd(worker_fd):
                            _validate_bundle(Path("."))
                            worker_metadata = os.stat(
                                "bugate_update_transaction.py",
                                dir_fd=worker_fd,
                                follow_symlinks=False,
                            )
                            if not stat.S_ISREG(worker_metadata.st_mode):
                                raise JournalError(
                                    "verified transaction worker is absent"
                                )
                            _assert_child_binding(
                                transaction_fd, "worker", worker_fd
                            )
                            completed = subprocess.run(
                                command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                check=False,
                                pass_fds=(lock_fd,),
                            )
                    except BaseException as exc:
                        recover_worker_failure(exc)
                        if bootstrap:
                            self._settle_bootstrap_execution(
                                identity, root_fd=root_fd
                            )
                        raise
                    finally:
                        os.close(worker_fd)
                    if completed.returncode != 0:
                        failure = TransactionError(
                            completed.stderr.strip()
                            or "transaction worker failed"
                        )
                        recover_worker_failure(failure)
                        if bootstrap:
                            self._settle_bootstrap_execution(
                                identity, root_fd=root_fd
                            )
                        raise failure
                    report = _read_canonical_json_at(
                        transaction_fd,
                        "report.json",
                        label="transaction report",
                    )
                    result = {
                        key: value
                        for key, value in report.items()
                        if key != "self_digest"
                    }
                if bootstrap:
                    self._inject("before_bootstrap_settle")
                    if self._settle_bootstrap_execution(
                        identity, root_fd=root_fd
                    ) != "committed":
                        raise JournalError(
                            "successful bootstrap worker was not committed"
                        )
                if archive_after_commit:
                    self._inject("before_legacy_archive")
                    self._archive_legacy_locked(root_fd=root_fd)
                return result
            try:
                report = self._execute_prepared(
                    state,
                    identity,
                    skip_operation_ids=skipped,
                    precommit_verify=precommit_verify,
                    final_verify=verify,
                    root_fd=root_fd,
                )
            except BaseException:
                if bootstrap:
                    self._settle_bootstrap_execution(
                        identity, root_fd=root_fd
                    )
                raise
            if bootstrap:
                self._inject("before_bootstrap_settle")
                if self._settle_bootstrap_execution(
                    identity, root_fd=root_fd
                ) != "committed":
                    raise JournalError(
                        "successful bootstrap transaction was not committed"
                    )
            if archive_after_commit:
                self._inject("before_legacy_archive")
                self._archive_legacy_locked(root_fd=root_fd)
            return report

    def rollback(
        self,
        transaction_id: str,
        *,
        verify: Callable[[], None] | None = None,
        archive_legacy: bool = False,
        legacy_manifest: Mapping[str, Any] | None = None,
        use_persisted_worker: bool = False,
    ) -> dict[str, Any]:
        """Create and commit an independent inverse transaction."""

        if not TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise JournalError("invalid rollback transaction id")
        root_fd = os.open(
            self.root,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if _fd_identity(root_fd) != self.root_identity:
                raise UnsafePathError("workspace root identity changed before rollback")
            with self._open_transaction_handles(
                self.state, transaction_id, root_fd=root_fd
            ) as (
                _state_fd,
                _transactions_fd,
                transaction_fd,
                _anchored_tx,
                original,
                operations,
            ):
                if original["status"] != "committed":
                    raise JournalError(
                        "only a committed transaction can be rolled back"
                    )
                directory_bindings = original["directory_bindings"]
                backup_fd = _open_child_directory(
                    transaction_fd,
                    "backup",
                    expected_identity=directory_bindings[
                        "local_directories"
                    ]["backup"],
                )
                inverse: list[Operation] = []
                payload_bytes: dict[str, bytes] = {}
                try:
                    for index, operation in enumerate(operations):
                        actual = _observe_at(
                            root_fd,
                            operation.target_path,
                            allow_missing_parents=True,
                            allow_non_directory_parents=(
                                operation.post["type"] == "absent"
                            ),
                        )
                        if actual != dict(operation.post):
                            raise ThirdPartyDriftError(
                                "rollback transaction is stale: "
                                + operation.target_path
                            )
                        identity = f"rollback:{operation.id}"
                        inverse.append(
                            Operation(
                                identity,
                                operation.target_path,
                                operation.post,
                                operation.pre,
                            )
                        )
                        if operation.pre["type"] == "file":
                            payload = _read_file_at(
                                backup_fd, f"{index:06d}"
                            )
                            if contract.sha256_bytes(payload) != operation.pre[
                                "sha256"
                            ]:
                                raise ThirdPartyDriftError(
                                    "rollback backup differs: "
                                    + operation.target_path
                                )
                            payload_bytes[identity] = payload
                    _assert_child_binding(
                        transaction_fd, "backup", backup_fd
                    )
                finally:
                    os.close(backup_fd)
                persisted_worker = (
                    self._bound_bundle_bytes(
                        transaction_fd,
                        directory_bindings,
                        "worker",
                    )
                    if use_persisted_worker
                    else None
                )
        finally:
            os.close(root_fd)
        report_metadata = {
            "decision": "GO",
            "engine_updated": False,
            "rollback_of": transaction_id,
            "codex_hook_hash_changed": any(
                item.target_path == ".codex/hooks.json" for item in inverse
            ),
            "new_session_required": any(
                item.target_path in {".codex/hooks.json", ".claude/settings.json"}
                for item in inverse
            ),
            "profile_migration": None,
            "memory_checked": False,
            "role_governance_activated": False,
            "rollback_available": False,
        }
        rollback_inputs: dict[str, bytes] = {}
        if legacy_manifest is not None:
            rollback_inputs["rollback-legacy-manifest.json"] = _canonical(
                legacy_manifest
            )
        return self.apply(
            inverse,
            payload_bytes=payload_bytes,
            worker_files=persisted_worker,
            input_files=rollback_inputs,
            verify=verify,
            execute_worker=use_persisted_worker,
            execute_worker_verify=use_persisted_worker,
            kind="rollback",
            source_transaction=transaction_id,
            report_metadata=report_metadata,
            archive_after_commit=archive_legacy,
        )

    def archive_legacy_rollback_state(self) -> None:
        """Move committed root state back under the legacy ignored plan lock."""

        with self.workspace_lock() as held_lock:
            root_fd = held_lock.fd
            if root_fd is None:
                raise TransactionError("workspace lock descriptor is unavailable")
            self._archive_legacy_locked(root_fd=root_fd)

    def _archive_legacy_locked(self, *, root_fd: int | None = None) -> None:
        close_root = False
        if root_fd is None:
            root_fd = os.open(
                self.root,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            close_root = True
        if _fd_identity(root_fd) != self.root_identity:
            if close_root:
                os.close(root_fd)
            raise UnsafePathError("workspace root identity changed during archival")
        if self.prelock.exists() or self.prelock.is_symlink():
            if close_root:
                os.close(root_fd)
            raise JournalError("plan.lock blocks rollback-state archival")
        parent = self.root.parent
        if os.lstat(parent).st_dev != os.lstat(self.root).st_dev:
            if close_root:
                os.close(root_fd)
            raise UnsupportedAtomicRename("rollback archive staging crosses filesystems")
        try:
            with self._open_state_handles(
                self.state, root_fd=root_fd
            ) as (state_fd, transactions_fd):
                if self._current(
                    self.state, state_fd=state_fd, root_fd=root_fd
                ) is not None:
                    raise JournalError(
                        "cannot archive state with an active transaction"
                    )
                self._validate_archived_history_open(
                    self.state, state_fd, transactions_fd
                )
                try:
                    transition_metadata = os.stat(
                        ARCHIVE_TRANSITION,
                        dir_fd=state_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    transition_metadata = None
                if transition_metadata is not None:
                    if not stat.S_ISREG(transition_metadata.st_mode):
                        raise JournalError("rollback archive transition is unsafe")
                    self._validate_archive_transition(state_fd=state_fd)
                else:
                    _atomic_json_at(
                        state_fd,
                        ARCHIVE_TRANSITION,
                        self._archive_transition_marker(),
                    )
                raw = Path(
                    tempfile.mkdtemp(
                        prefix=f".{self.root.name}.bugate-archive-",
                        dir=parent,
                    )
                )
                parent_fd = os.open(
                    parent,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                raw_fd = _open_child_directory(parent_fd, raw.name)
                os.fchmod(raw_fd, 0o700)
                os.fsync(raw_fd)
                raw_identity = _fd_identity(raw_fd)
                wrapper = raw / "plan.lock"
                archived = wrapper / BOOTSTRAP_CHILD
                try:
                    wrapper.mkdir(mode=0o700)
                    os.chmod(wrapper, 0o700)
                    with _descriptor_cwd(state_fd) as anchored_state:
                        shutil.copytree(
                            anchored_state, archived, symlinks=True
                        )
                        if _tree_digest(anchored_state) != _tree_digest(
                            archived
                        ):
                            raise JournalError(
                                "rollback archive copy differs from root state"
                            )
                    self._rebind_private_state_copy(archived)
                    with self._open_state_handles(archived) as (
                        archived_fd,
                        archived_transactions_fd,
                    ):
                        try:
                            os.unlink(
                                ARCHIVE_TRANSITION, dir_fd=archived_fd
                            )
                        except FileNotFoundError:
                            pass
                        self._publish_archive_marker_open(
                            archived,
                            archived_fd,
                            archived_transactions_fd,
                        )
                    _fsync_tree(wrapper)
                    self._inject("before_archive_publish")
                    destination_parent_fd, destination_leaf = _open_parent_at(
                        root_fd, f"{self.vendor_dir}/plan.lock"
                    )
                    assert destination_parent_fd is not None
                    try:
                        _exclusive_rename_at(
                            raw_fd,
                            "plan.lock",
                            destination_parent_fd,
                            destination_leaf,
                        )
                    finally:
                        os.close(destination_parent_fd)
                    self._inject("after_archive_publish")
                    _assert_child_binding(root_fd, ROOT_STATE, state_fd)
                    os.rename(
                        ROOT_STATE,
                        "retired-root-state",
                        src_dir_fd=root_fd,
                        dst_dir_fd=raw_fd,
                    )
                    os.fsync(root_fd)
                    os.fsync(raw_fd)
                    self._inject("after_archive_root_retire")
                    _remove_tree_at(
                        raw_fd,
                        "retired-root-state",
                        expected_identity=_fd_identity(state_fd),
                    )
                finally:
                    try:
                        _remove_tree_at(
                            parent_fd,
                            raw.name,
                            expected_identity=raw_identity,
                        )
                    finally:
                        os.close(raw_fd)
                        os.close(parent_fd)
        finally:
            if close_root:
                os.close(root_fd)


def recovery_status(root: Path | str, vendor_dir: str = ".bugate") -> dict[str, Any]:
    """Return the transaction recovery gate without persistent writes."""

    details = TransactionManager(root, vendor_dir).recovery_required()
    return {
        "recovery_required": details is not None,
        "details": details,
        "decision": "NO-GO" if details is not None else "GO",
    }


def recover_pending(root: Path | str, vendor_dir: str = ".bugate") -> dict[str, Any] | None:
    """Recover an interrupted apply/rollback transaction under the workspace lock."""

    return TransactionManager(root, vendor_dir).recover()


def _worker_bundle(
    release_root: Path,
    release_manifest: Mapping[str, Any],
    *,
    release_fd: int | None = None,
) -> dict[str, bytes]:
    """Freeze worker bytes only after binding them to the prepared manifest."""

    names = (
        "bugate_update.py",
        "bugate_update_transaction.py",
        "bugate_update_engine.py",
        "bugate_update_source.py",
        "bugate_install_contract.py",
        "bugate_legacy_manifest.py",
        "bugate_core.py",
    )
    inventory = {
        item.get("path"): item
        for item in release_manifest.get("archive_inventory", [])
        if isinstance(item, Mapping)
    }
    result: dict[str, bytes] = {}
    for name in names:
        relative = f"scripts/{name}"
        expected = inventory.get(relative)
        if (
            not isinstance(expected, Mapping)
            or expected.get("type") != "file"
            or not isinstance(expected.get("sha256"), str)
            or expected.get("mode") not in {"0644", "0755"}
        ):
            raise TransactionError(
                f"release manifest lacks a regular worker contract: {relative}"
            )
        try:
            contract.validate_sha256(expected["sha256"], field=f"{relative}.sha256")
            if release_fd is None:
                candidate = _assert_safe_parent(release_root, relative)
                descriptor = os.open(
                    candidate,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            else:
                parent_fd, leaf = _open_parent_at(release_fd, relative)
                assert parent_fd is not None
                try:
                    descriptor = os.open(
                        leaf,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                finally:
                    os.close(parent_fd)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise TransactionError(f"worker source is not regular: {relative}")
                if _mode(metadata) != expected["mode"]:
                    raise TransactionError(f"worker source mode drifted: {relative}")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                payload = b"".join(chunks)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise TransactionError(f"worker source is unsafe or unreadable: {relative}") from exc
        if contract.sha256_bytes(payload) != expected["sha256"]:
            raise TransactionError(f"worker source digest drifted: {relative}")
        result[name] = payload
    return result


def apply_update(
    root: Path | str,
    vendor_dir: str,
    prepared_release: Any,
    plan: Mapping[str, Any],
    *,
    updater_version: str,
) -> dict[str, Any]:
    """Bridge verified source + GO plan into the durable transaction layer.

    ``prepared_release`` is intentionally duck typed: source verification owns
    its concrete dataclass and exposes ``root``, ``manifest``, and
    ``archive_sha256``.  The engine owns plan materialization.
    """

    import bugate_update_engine as engine

    try:
        project = engine._safe_root(Path(root))
    except engine.UpdateEngineError as exc:
        raise TransactionError(str(exc)) from exc
    version = contract.validate_semver(updater_version)
    manifest = getattr(prepared_release, "manifest", None)
    source_root = getattr(prepared_release, "root", None)
    prepared_root_identity = getattr(prepared_release, "root_identity", None)
    if (
        not isinstance(manifest, Mapping)
        or source_root is None
        or not isinstance(prepared_root_identity, tuple)
        or len(prepared_root_identity) != 2
        or not all(isinstance(value, int) for value in prepared_root_identity)
    ):
        raise TransactionError(
            "prepared release lacks verified root/manifest identity"
        )
    source_root = Path(source_root)
    try:
        manifest = contract.validate_current_release_manifest(manifest)
        contract.require_updater_compatible(version, manifest["updater_minimum_version"])
        engine.validate_plan_base(project, vendor_dir, plan)
    except (contract.ContractError, engine.UpdateEngineError) as exc:
        raise TransactionError(str(exc)) from exc
    if plan.get("decision") != "GO":
        raise TransactionError("refusing to apply a NO-GO update plan")
    prepared_archive_sha256 = getattr(prepared_release, "archive_sha256", None)
    prepared_source_kind = getattr(prepared_release, "source_kind", None)
    source_identity = {
        "to_version": manifest["bugate_version"],
        "release_digest": manifest["self_digest"],
        "manifest_sha256": contract.sha256_bytes(
            contract.canonical_json_bytes(manifest)
        ),
        "archive_sha256": prepared_archive_sha256,
        "source_kind": prepared_source_kind,
        "target_manifest": manifest,
    }
    mismatched = [
        field for field, expected in source_identity.items()
        if plan.get(field) != expected
    ]
    if mismatched:
        raise TransactionError(
            "prepared release identity differs from the reviewed plan: "
            + ", ".join(sorted(mismatched))
        )
    try:
        release_fd = os.open(
            source_root,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise TransactionError(
            "prepared release root is unavailable or unsafe"
        ) from exc
    release_metadata = os.fstat(release_fd)
    if (release_metadata.st_dev, release_metadata.st_ino) != prepared_root_identity:
        os.close(release_fd)
        raise TransactionError("prepared release root identity changed")
    if plan.get("no_op"):
        try:
            current = os.lstat(source_root)
            if (
                stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != prepared_root_identity
            ):
                raise TransactionError("prepared release root identity changed")
        finally:
            os.close(release_fd)
        result = TransactionManager(project, vendor_dir).apply([])
        result.update(
            {
                "schema_version": STATE_SCHEMA,
                "decision": "GO",
                "no_op": True,
                "engine_updated": False,
                "from_version": plan.get("from_version"),
                "to_version": plan.get("to_version"),
                "release_digest": plan.get("release_digest"),
                "archive_sha256": prepared_archive_sha256,
                "source_kind": prepared_source_kind,
                "manifest_sha256": plan.get("manifest_sha256"),
                "codex_hook_hash_changed": bool(plan.get("codex_hook_hash_changed")),
                "new_session_required": bool(plan.get("new_session_required")),
                "profile_migration": plan.get("profile_compatibility"),
                "memory_checked": False,
                "role_governance_activated": False,
                "rollback_available": bool(plan.get("rollback_available")),
            }
        )
        return result
    try:
        shared = engine.materialize_shared_outputs(project, plan)
        material = engine.transaction_material(
            plan,
            Path(source_root),
            shared_outputs=shared,
        )
    except engine.UpdateEngineError as exc:
        os.close(release_fd)
        raise TransactionError(str(exc)) from exc
    def freeze_release_material() -> tuple[dict[str, bytes], dict[str, bytes]]:
        shared_targets = {
            item.get("target_path")
            for item in plan.get("transaction_operations", [])
            if item.get("scope")
            in {"shared_json_fragment", "marked_text_block"}
        }
        for operation in material.get("operations") or []:
            if operation.get("target_path") not in shared_targets:
                continue
            pre = operation.get("pre")
            post = operation.get("post")
            if not isinstance(pre, dict) or not isinstance(post, dict):
                raise TransactionError("shared transaction image is malformed")
            pre_kind = pre.get("type", pre.get("state"))
            if pre_kind == "absent":
                continue
            target = _assert_safe_parent(project, operation["target_path"])
            metadata = os.lstat(target)
            if not stat.S_ISREG(metadata.st_mode):
                raise TransactionError(
                    "shared target is not a regular file: "
                    + str(operation["target_path"])
                )
            actual_mode = _mode(metadata)
            pre["mode"] = actual_mode
            if post.get("type", post.get("state")) == "file":
                post["mode"] = actual_mode
        raw_sources = material.get("payload_sources") or {}
        frozen_payloads = dict(material.get("payload_bytes") or {})
        operations_by_id = {
            operation.get("id"): operation
            for operation in material.get("operations") or []
            if isinstance(operation, Mapping)
        }
        for identity, relative in raw_sources.items():
            operation = operations_by_id.get(identity)
            if not isinstance(operation, Mapping) or not isinstance(
                operation.get("post"), Mapping
            ):
                raise TransactionError(
                    f"release payload operation is missing: {identity}"
                )
            expected_post = _validate_image(
                operation["post"], label=f"release payload {identity}"
            )
            observed = _observe_at(release_fd, relative)
            if observed != expected_post:
                raise TransactionError(
                    "prepared release payload drifted: "
                    f"{relative}; expected={expected_post!r}; "
                    f"actual={observed!r}"
                )
            payload = _read_file_at(release_fd, relative)
            if contract.sha256_bytes(payload) != expected_post.get("sha256"):
                raise TransactionError(
                    f"prepared release payload changed while frozen: {relative}"
                )
            frozen_payloads[identity] = payload
        frozen_worker = _worker_bundle(
            source_root, manifest, release_fd=release_fd
        )
        current = os.lstat(source_root)
        if (
            stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != prepared_root_identity
        ):
            raise TransactionError("prepared release root identity changed")
        return frozen_payloads, frozen_worker

    try:
        frozen_payloads, frozen_worker = freeze_release_material()
    finally:
        os.close(release_fd)
    manager = TransactionManager(project, vendor_dir)
    installed_kind = plan.get("installed_kind")

    def verify_installed() -> None:
        result = engine.verify_installed(
            project,
            vendor_dir,
            recovery={"recovery_required": False},
        )
        if result.get("decision") != "GO":
            raise TransactionError(f"post-update verify failed: {result.get('failures')}")

    report = manager.apply(
        material.get("operations") or [],
        payload_bytes=frozen_payloads,
        worker_files=frozen_worker,
        input_files={
            "release-manifest.json": contract.canonical_json_bytes(manifest),
            "plan.json": contract.canonical_json_bytes(plan),
        },
        base_verify=lambda: engine.validate_plan_base(project, vendor_dir, plan),
        verify=verify_installed,
        execute_worker=True,
        bootstrap=installed_kind == "legacy",
        gitignore_operation_id=material.get("gitignore_operation_id"),
    )
    report.update(
        {
            "decision": "GO",
            "no_op": False,
            "engine_updated": True,
            "from_version": plan.get("from_version"),
            "to_version": plan.get("to_version"),
            "release_digest": plan.get("release_digest"),
            "archive_sha256": getattr(prepared_release, "archive_sha256", None),
            "codex_hook_hash_changed": bool(plan.get("codex_hook_hash_changed")),
            "new_session_required": bool(plan.get("new_session_required")),
            "profile_migration": plan.get("profile_compatibility"),
            "memory_checked": False,
            "role_governance_activated": False,
            "rollback_available": True,
        }
    )
    return report


def rollback_transaction(
    root: Path | str,
    vendor_dir: str,
    transaction_id: str,
    *,
    updater_version: str,
    legacy_manifests: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Rollback one committed transaction and verify its restored baseline."""

    import bugate_update_engine as engine

    contract.validate_semver(updater_version)
    try:
        project = engine._safe_root(Path(root))
    except engine.UpdateEngineError as exc:
        raise TransactionError(str(exc)) from exc
    manager = TransactionManager(project, vendor_dir)
    pending = manager.recovery_required()
    if pending is not None:
        manager.recover()
    persisted_prior: Mapping[str, Any] | None = None
    root_fd = os.open(
        manager.root,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if _fd_identity(root_fd) != manager.root_identity:
            raise UnsafePathError("workspace root identity changed before rollback")
        with manager._open_transaction_handles(
            manager.state, transaction_id, root_fd=root_fd
        ) as (
            _state_fd,
            _transactions_fd,
            transaction_fd,
            _anchored_tx,
            original_journal,
            _operations,
        ):
            input_fd = _open_child_directory(
                transaction_fd,
                "input",
                expected_identity=original_journal["directory_bindings"][
                    "local_directories"
                ]["input"],
            )
            try:
                try:
                    plan_metadata = os.stat(
                        "plan.json", dir_fd=input_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    plan_metadata = None
                if plan_metadata is not None:
                    if not stat.S_ISREG(plan_metadata.st_mode):
                        raise JournalError(
                            "rollback transaction plan input is invalid"
                        )
                    plan_bytes = _read_file_at(input_fd, "plan.json")
                    try:
                        persisted_plan = json.loads(plan_bytes)
                    except json.JSONDecodeError as exc:
                        raise JournalError(
                            "rollback transaction plan input is invalid"
                        ) from exc
                    if (
                        not isinstance(persisted_plan, dict)
                        or plan_bytes != _canonical(persisted_plan)
                    ):
                        raise JournalError(
                            "rollback transaction plan input is not canonical"
                        )
                    supplied = persisted_plan.get("plan_digest")
                    digest_payload = dict(persisted_plan)
                    digest_payload.pop("plan_digest", None)
                    if supplied != contract.sha256_bytes(
                        _canonical(digest_payload)
                    ):
                        raise JournalError(
                            "rollback transaction plan digest mismatch"
                        )
                    candidate = persisted_plan.get("from_state_manifest")
                    if candidate is not None:
                        if not isinstance(candidate, Mapping):
                            raise JournalError(
                                "rollback prior-state manifest is invalid"
                            )
                        persisted_prior = candidate
            finally:
                os.close(input_fd)
    finally:
        os.close(root_fd)
    catalogs = list(legacy_manifests)
    if persisted_prior is not None and persisted_prior.get("manifest_kind") == "prelock-installed-projection":
        prior_digest = persisted_prior.get("self_digest")
        catalogs = [item for item in catalogs if item.get("self_digest") != prior_digest]
        catalogs.insert(0, persisted_prior)

    def verify_restored() -> None:
        state = engine.detect_installed_state(project, vendor_dir, catalogs)
        if not state.go:
            raise TransactionError(
                f"rollback verification failed: {list(state.diagnostics)}"
            )

    return manager.rollback(
        transaction_id,
        verify=verify_restored,
        archive_legacy=(
            persisted_prior is not None
            and persisted_prior.get("manifest_kind")
            == "prelock-installed-projection"
        ),
        legacy_manifest=(
            persisted_prior
            if persisted_prior is not None
            and persisted_prior.get("manifest_kind")
            == "prelock-installed-projection"
            else None
        ),
        use_persisted_worker=True,
    )


def _transaction_worker_main(argv: list[str]) -> int:
    if len(argv) != 6:
        raise TransactionError("invalid transaction worker invocation")
    raw_root, vendor_dir, transaction_id, raw_lock_fd, raw_skipped, verify_mode = argv
    if verify_mode not in {"verify", "physical-only"}:
        raise TransactionError("invalid transaction worker verification mode")
    manager = TransactionManager(raw_root, vendor_dir)
    try:
        lock_fd = int(raw_lock_fd)
        metadata = os.fstat(lock_fd)
    except (OSError, ValueError) as exc:
        raise TransactionError("transaction worker did not inherit the workspace lock") from exc
    if {"device": metadata.st_dev, "inode": metadata.st_ino} != manager.root_identity:
        raise TransactionError("inherited workspace lock is bound to another root")
    worker_fd = os.open(
        ".",
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        with _descriptor_cwd(worker_fd) as anchored_worker:
            _validate_bundle(anchored_worker)
    finally:
        os.close(worker_fd)
    skipped = [value for value in raw_skipped.split(",") if value]

    legacy_manifests: list[Mapping[str, Any]] = []
    with manager._open_transaction_handles(
        manager.state, transaction_id, root_fd=lock_fd
    ) as (
        _state_fd,
        _transactions_fd,
        transaction_fd,
        _anchored_tx,
        journal,
        _operations,
    ):
        input_fd = _open_child_directory(
            transaction_fd,
            "input",
            expected_identity=journal["directory_bindings"][
                "local_directories"
            ]["input"],
        )
        try:
            try:
                legacy_metadata = os.stat(
                    "rollback-legacy-manifest.json",
                    dir_fd=input_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                legacy_metadata = None
            if legacy_metadata is not None:
                if not stat.S_ISREG(legacy_metadata.st_mode):
                    raise JournalError("rollback legacy manifest is invalid")
                legacy_manifests.append(
                    _read_canonical_json_at(
                        input_fd,
                        "rollback-legacy-manifest.json",
                        label="rollback legacy manifest",
                    )
                )
        finally:
            os.close(input_fd)

    def final_verify() -> None:
        import bugate_update_engine as engine

        result = engine.verify_installed(
            manager.root,
            manager.vendor_dir,
            legacy_manifests=legacy_manifests,
            recovery={"recovery_required": False},
        )
        if result.get("decision") != "GO":
            raise TransactionError(f"worker post-update verify failed: {result.get('failures')}")

    with manager._signals():
        manager._execute_prepared(
            manager.state,
            transaction_id,
            skip_operation_ids=skipped,
            final_verify=final_verify if verify_mode == "verify" else None,
            root_fd=lock_fd,
        )
    return 0


__all__ = [
    "ConcurrentUpdateError",
    "InjectedFailure",
    "JournalHierarchyBindingError",
    "JournalError",
    "Operation",
    "ReportIntegrityError",
    "ThirdPartyDriftError",
    "TransactionError",
    "TransactionManager",
    "UnsafePathError",
    "UnsupportedAtomicRename",
    "UpdateInterrupted",
    "WorkspaceLock",
    "apply_update",
    "recover_pending",
    "recovery_status",
    "rollback_transaction",
]


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "__transaction-worker":
        try:
            raise SystemExit(_transaction_worker_main(sys.argv[2:]))
        except TransactionError as exc:
            sys.stderr.write(f"transaction worker failed: {exc}\n")
            raise SystemExit(2)
    raise SystemExit("bugate_update_transaction.py is an internal module")
