#!/usr/bin/env python3
"""Pure, read-only planning engine for BUGate imported-mode updates.

This module deliberately performs no persistent writes, downloads, Memory Bus
calls, profile migrations, or transaction work.  It observes a synthetic or
real imported workspace, validates release/installed metadata, computes a
deterministic plan, and returns candidate shared-file bytes to the transaction
layer.  The CLI/source/transaction modules own all mutation and network I/O.
"""
from __future__ import annotations

import copy
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

import bugate_install_contract as contract
import bugate_legacy_manifest as legacy_contract
from bugate_core import (
    _canonicalize_config_document as canonicalize_config_document,
    deep_merge,
    parse_nested_yaml,
)


UPDATE_PLAN_SCHEMA_VERSION = 1
_HOOK_ID_RE = re.compile(r"^BUGATE_HOOK_ID='([^']+)'; export BUGATE_HOOK_ID; ")


class UpdateEngineError(RuntimeError):
    """An installed state or update input is unsafe or internally inconsistent."""


class OwnershipConflict(UpdateEngineError):
    """A shared fragment cannot be attributed to BUGate without guessing."""


# Stable CLI-facing compatibility name.
UpdateError = UpdateEngineError


@dataclass(frozen=True)
class MergeResult:
    content: bytes
    changed: bool
    details: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class InstalledState:
    kind: str
    version: str | None
    projection: tuple[dict[str, Any], ...]
    manifest: dict[str, Any] | None
    lock: dict[str, Any] | None
    legacy_manifest: dict[str, Any] | None
    observations: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    go: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "projection": copy.deepcopy(list(self.projection)),
            "manifest": copy.deepcopy(self.manifest),
            "lock": copy.deepcopy(self.lock),
            "legacy_manifest": copy.deepcopy(self.legacy_manifest),
            "observations": copy.deepcopy(list(self.observations)),
            "diagnostics": copy.deepcopy(list(self.diagnostics)),
            "go": self.go,
        }


def _safe_root(project_root: Path) -> Path:
    expanded = project_root.expanduser()
    if expanded.is_symlink():
        raise UpdateEngineError(f"project root must not be a symlink: {expanded}")
    root = expanded.resolve()
    if not root.is_dir():
        raise UpdateEngineError(f"project root is missing, not a directory, or a symlink: {root}")
    return root


def _safe_target(root: Path, relative: str) -> Path:
    contract.validate_relative_path(relative, field="target path")
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        try:
            if current.is_symlink():
                raise OwnershipConflict(f"target parent is a symlink: {relative}")
            if current.exists() and not current.is_dir():
                raise OwnershipConflict(f"target parent is not a directory: {relative}")
        except OSError as exc:
            raise OwnershipConflict(f"cannot inspect target parent for {relative}: {exc}") from exc
    return target


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError as exc:
        raise UpdateEngineError(f"{label} is missing") from exc
    except OSError as exc:
        raise UpdateEngineError(f"{label} is unsafe or unreadable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UpdateEngineError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _json_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise UpdateEngineError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise UpdateEngineError(f"{label} must be a JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_release_manifest(
    path: Path, expected_version: str | None = None
) -> dict[str, Any]:
    """Load an external target manifest under the current ownership catalog."""

    value = _json_object_bytes(_read_regular(path, label="release manifest"), label="release manifest")
    try:
        return contract.validate_current_release_manifest(
            value, expected_version=expected_version
        )
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc


def _read_regular_image(path: Path, *, label: str) -> tuple[bytes, str]:
    """Read one regular non-symlink image and its mode from the same fd."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpdateEngineError(f"{label} is unavailable or unsafe") from exc
    try:
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise UpdateEngineError(f"{label} must be a regular file")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks), _mode(metadata)
        except OSError as exc:
            raise UpdateEngineError(f"{label} is unavailable or unsafe") from exc
    finally:
        os.close(descriptor)


def _open_directory_beneath(root: Path, relative: str) -> int:
    """Open a normalized child directory without following any component."""

    contract.validate_relative_path(relative, field="release directory")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        current = os.open(root, directory_flags)
    except OSError as exc:
        raise UpdateEngineError("release root is unavailable or unsafe") from exc
    try:
        for part in PurePosixPath(relative).parts:
            following = os.open(part, directory_flags, dir_fd=current)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def _read_regular_at(
    directory_fd: int, name: str, *, label: str
) -> tuple[bytes, str]:
    if "/" in name or name in {"", ".", ".."}:
        raise UpdateEngineError(f"{label} name is invalid")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise UpdateEngineError(f"{label} is unavailable or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UpdateEngineError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), _mode(metadata)
    finally:
        os.close(descriptor)


def _open_pinned_workspace_root(
    root: Path, *, expected_identity: tuple[int, int] | None = None
) -> int:
    """Open the workspace root without following a replacement symlink."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        descriptor = os.open(root, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise UpdateEngineError("workspace root is unavailable or unsafe") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise UpdateEngineError("workspace root must be a physical directory")
    if expected_identity is not None and (
        metadata.st_dev,
        metadata.st_ino,
    ) != expected_identity:
        os.close(descriptor)
        raise UpdateEngineError("workspace root identity drift during profile inspection")
    return descriptor


def _read_regular_beneath_at(
    root_fd: int, relative: str, *, label: str
) -> tuple[bytes, str]:
    """Read one in-workspace file through pinned, no-follow parent dirfds."""

    try:
        normalized = contract.validate_relative_path(relative, field=label)
    except contract.ContractError as exc:
        raise UpdateEngineError(f"{label} path is invalid") from exc
    parts = PurePosixPath(normalized).parts
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        current = os.dup(root_fd)
    except OSError as exc:
        raise UpdateEngineError(f"{label} is unavailable or unsafe") from exc
    directory_bindings = [
        (os.fstat(current).st_dev, os.fstat(current).st_ino)
    ]
    try:
        for part in parts[:-1]:
            try:
                following = os.open(part, directory_flags, dir_fd=current)
            except FileNotFoundError as exc:
                raise UpdateEngineError(f"{label} is missing") from exc
            except OSError as exc:
                raise UpdateEngineError(
                    f"{label} parent is unavailable or unsafe"
                ) from exc
            following_metadata = os.fstat(following)
            directory_bindings.append(
                (following_metadata.st_dev, following_metadata.st_ino)
            )
            os.close(current)
            current = following

        leaf = parts[-1]
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(leaf, file_flags, dir_fd=current)
        except FileNotFoundError as exc:
            raise UpdateEngineError(f"{label} is missing") from exc
        except OSError as exc:
            # This diagnostic lookup never grants access: the operation stays
            # failed.  It only preserves the stable non-regular-file reason for
            # a final-component symlink/directory without following it.
            try:
                failed_metadata = os.stat(
                    leaf, dir_fd=current, follow_symlinks=False
                )
            except OSError:
                failed_metadata = None
            if failed_metadata is not None and not stat.S_ISREG(
                failed_metadata.st_mode
            ):
                raise UpdateEngineError(f"{label} must be a regular file") from exc
            raise UpdateEngineError(f"{label} is unavailable or unsafe") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise UpdateEngineError(f"{label} must be a regular file")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
            file_mode = _mode(metadata)
            leaf_identity = (metadata.st_dev, metadata.st_ino)
            _revalidate_regular_beneath_binding(
                root_fd,
                parts,
                directory_bindings,
                leaf_identity,
                contract.sha256_bytes(data),
                file_mode,
                label=label,
            )
            return data, file_mode
        except OSError as exc:
            raise UpdateEngineError(f"{label} is unavailable or unsafe") from exc
        finally:
            os.close(descriptor)
    finally:
        os.close(current)


def _revalidate_regular_beneath_binding(
    root_fd: int,
    parts: tuple[str, ...],
    directory_bindings: Sequence[tuple[int, int]],
    leaf_identity: tuple[int, int],
    leaf_sha256: str,
    leaf_mode: str,
    *,
    label: str,
) -> None:
    """Rebind the pathname from root after reading and compare every inode."""

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    current = os.dup(root_fd)
    try:
        root_metadata = os.fstat(current)
        if (root_metadata.st_dev, root_metadata.st_ino) != directory_bindings[0]:
            raise UpdateEngineError(f"{label} path changed during read")
        for index, part in enumerate(parts[:-1], start=1):
            following = os.open(part, directory_flags, dir_fd=current)
            metadata = os.fstat(following)
            if (metadata.st_dev, metadata.st_ino) != directory_bindings[index]:
                os.close(following)
                raise UpdateEngineError(f"{label} path changed during read")
            os.close(current)
            current = following
        leaf_fd = os.open(parts[-1], file_flags, dir_fd=current)
        try:
            leaf_metadata = os.fstat(leaf_fd)
            if (
                not stat.S_ISREG(leaf_metadata.st_mode)
                or (leaf_metadata.st_dev, leaf_metadata.st_ino) != leaf_identity
            ):
                raise UpdateEngineError(f"{label} path changed during read")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(leaf_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            if (
                contract.sha256_bytes(b"".join(chunks)) != leaf_sha256
                or _mode(leaf_metadata) != leaf_mode
            ):
                raise UpdateEngineError(f"{label} content changed during read")
        finally:
            os.close(leaf_fd)
    except UpdateEngineError:
        raise
    except OSError as exc:
        raise UpdateEngineError(f"{label} path changed during read") from exc
    finally:
        os.close(current)


def load_legacy_manifests(
    release_root: Path | None,
    target_release_manifest: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Load the complete supported pre-lock catalog from a verified release tree."""

    if release_root is None:
        raise UpdateEngineError("release_root is required to load legacy manifests")
    if target_release_manifest is None:
        raise UpdateEngineError(
            "target release manifest is required to bind legacy manifest assets"
        )
    try:
        target_manifest = contract.validate_current_release_manifest(
            target_release_manifest
        )
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    root = release_root.expanduser().resolve()
    manifests: list[dict[str, Any]] = []
    expected_names = {f"{tag}.json" for tag in contract.SUPPORTED_LEGACY_TAGS}
    try:
        directory_fd = _open_directory_beneath(
            root, contract.LEGACY_MANIFEST_DIR
        )
        actual_names = set(os.listdir(directory_fd))
    except OSError as exc:
        raise UpdateEngineError("legacy manifest directory is unavailable") from exc
    try:
        if actual_names != expected_names:
            raise UpdateEngineError(
                "legacy manifest set mismatch: "
                f"missing={sorted(expected_names - actual_names)}, "
                f"unexpected={sorted(actual_names - expected_names)}"
            )
        for tag in contract.SUPPORTED_LEGACY_TAGS:
            try:
                data, actual_mode = _read_regular_at(
                    directory_fd,
                    f"{tag}.json",
                    label=f"legacy manifest {tag}",
                )
                manifests.append(
                    legacy_contract.validate_legacy_manifest_asset(
                        data,
                        expected_tag=tag,
                        target_release_manifest=target_manifest,
                        actual_mode=actual_mode,
                    )
                )
            except contract.ContractError as exc:
                raise UpdateEngineError(str(exc)) from exc
    finally:
        os.close(directory_fd)
    return tuple(manifests)


def _replace_legacy_vendor(value: Any, vendor: str) -> Any:
    if isinstance(value, str):
        value = value.replace("$ROOT/.bugate/", f"$ROOT/{vendor}/")
        value = value.replace("/.bugate/", f"/{vendor}/")
        return value
    if isinstance(value, list):
        return [_replace_legacy_vendor(item, vendor) for item in value]
    if isinstance(value, Mapping):
        return {key: _replace_legacy_vendor(item, vendor) for key, item in value.items()}
    return value


def render_legacy_projection(
    manifest: Mapping[str, Any], vendor_dir: str = ".bugate"
) -> list[dict[str, Any]]:
    """Render an exact historical projection for a deterministic custom vendor dir."""

    try:
        validated = legacy_contract.validate_legacy_manifest(manifest)
        vendor = contract.validate_vendor_dir(vendor_dir)
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    projection = copy.deepcopy(validated["installed_projection"])
    for item in projection:
        scope = item["scope"]
        if scope == "vendor":
            item["target_path"] = f"{vendor}/{item['target_path']}"
        elif item["id"].startswith("skill:"):
            source = f"{vendor}/{item['source_path']}"
            item["target"] = os.path.relpath(
                source, PurePosixPath(item["target_path"]).parent.as_posix()
            ).replace(os.sep, "/")
            contract.validate_symlink_target(item["target_path"], item["target"])
        elif scope == "shared_json_fragment":
            item["value"] = _replace_legacy_vendor(item["value"], vendor)
            item["semantic_digest"] = contract.semantic_digest(
                {"event": item["event"], "value": item["value"]}
            )
        elif scope == "marked_text_block":
            item["content"] = _replace_legacy_vendor(item["content"], vendor)
            item["semantic_digest"] = contract.semantic_digest(
                {key: item[key] for key in ("begin", "end", "content")}
            )
    try:
        contract.validate_installed_projection(projection)
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    return sorted(projection, key=lambda value: value["id"])


def _mode(metadata: os.stat_result) -> str:
    return f"{stat.S_IMODE(metadata.st_mode):04o}"


def _commands(value: Any) -> list[str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("hooks"), list):
        return []
    return [
        hook.get("command", "")
        for hook in value["hooks"]
        if isinstance(hook, Mapping) and isinstance(hook.get("command", ""), str)
    ]


def _identities(value: Any) -> set[str]:
    result: set[str] = set()
    for command in _commands(value):
        match = _HOOK_ID_RE.match(command)
        if match:
            result.add(match.group(1))
    return result


def _load_hook_document(
    root: Path,
    relative: str,
) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    try:
        raw, file_mode = _read_optional_image(root, relative)
        if raw is None:
            return None, None, None, None
        document = _json_object_bytes(raw, label="shared hook file")
        hooks = document.get("hooks", {})
        if not isinstance(hooks, dict):
            return None, "hook file .hooks is not an object", contract.sha256_bytes(raw), file_mode
        return document, None, contract.sha256_bytes(raw), file_mode
    except UpdateEngineError as exc:
        return None, str(exc), None, None


def _validate_global_hook_identities(
    root: Path,
    *projections: Iterable[Mapping[str, Any]],
    container_images: Mapping[str, bytes | None] | None = None,
) -> None:
    """Reject known BUGate hook identities outside their exact owned shape.

    Identity is routing metadata, not ownership by itself. A known identity in
    another event, another runtime file, a mixed command entry, or a duplicate
    occurrence is a spoof/conflict unless the complete event+value digest
    matches an old or target manifest item at that exact container.
    """

    catalog = [
        copy.deepcopy(dict(item))
        for projection in projections
        for item in projection
        if item.get("scope") == "shared_json_fragment"
        and isinstance(item.get("hook_identity"), str)
    ]
    known = {item["hook_identity"] for item in catalog}
    if not known:
        return
    allowed: dict[str, set[tuple[str, str, str]]] = {}
    for item in catalog:
        allowed.setdefault(item["hook_identity"], set()).add(
            (item["target_path"], item["event"], item["semantic_digest"])
        )
    counts = {identity: 0 for identity in known}
    targets = sorted({item["target_path"] for item in catalog})
    for target_path in targets:
        if container_images is not None:
            if target_path not in container_images:
                raise OwnershipConflict(
                    f"hook container image is missing from the bound snapshot: {target_path}"
                )
            raw = container_images[target_path]
            document = (
                None
                if raw is None
                else _json_object_bytes(raw, label=f"shared hook file {target_path}")
            )
        else:
            document, error, _digest, _mode_value = _load_hook_document(
                root, target_path
            )
            if error:
                raise OwnershipConflict(
                    f"unsafe hook container {target_path}: {error}"
                )
        if document is None:
            continue
        hooks = document.get("hooks", {})
        if not isinstance(hooks, Mapping):
            raise OwnershipConflict(f"hook file .hooks is not an object: {target_path}")
        for event, entries in hooks.items():
            if not isinstance(event, str) or not isinstance(entries, list):
                raise OwnershipConflict(f"hook event is invalid: {target_path}")
            for value in entries:
                identities = _identities(value) & known
                if not identities:
                    continue
                digest = contract.semantic_digest({"event": event, "value": value})
                for identity in identities:
                    counts[identity] += 1
                    if (target_path, event, digest) not in allowed[identity]:
                        raise OwnershipConflict(
                            f"known BUGate hook identity has an unowned shape: {target_path}/{event}"
                        )
    duplicated = sorted(identity for identity, count in counts.items() if count > 1)
    if duplicated:
        raise OwnershipConflict(
            "duplicate BUGate hook identity occurrence: " + ", ".join(duplicated)
        )


def _observe_hook(root: Path, item: Mapping[str, Any]) -> dict[str, Any]:
    document, error, file_digest, file_mode = _load_hook_document(
        root, item["target_path"]
    )
    base = {
        "id": item["id"],
        "scope": item["scope"],
        "target_path": item["target_path"],
        "container_sha256": file_digest,
        "container_mode": file_mode,
    }
    if error:
        return {**base, "status": "conflict", "error": error}
    if document is None:
        return {**base, "status": "missing"}
    entries = document.get("hooks", {}).get(item.get("event"), [])
    if not isinstance(entries, list):
        return {**base, "status": "conflict", "error": "hook event is not an array"}
    matching = [
        index
        for index, value in enumerate(entries)
        if contract.semantic_digest({"event": item.get("event"), "value": value})
        == item.get("semantic_digest")
    ]
    identity = item.get("hook_identity")
    spoof = []
    if identity:
        for index, value in enumerate(entries):
            if identity in _identities(value) and index not in matching:
                spoof.append(index)
    if spoof or len(matching) > 1:
        return {
            **base,
            "status": "conflict",
            "matching_indices": matching,
            "spoof_indices": spoof,
            "error": "duplicate or spoofed BUGate hook identity",
        }
    if matching:
        return {
            **base,
            "status": "present",
            "type": "json_fragment",
            "semantic_digest": item["semantic_digest"],
            "matching_index": matching[0],
        }
    return {**base, "status": "missing"}


def _observe_block(root: Path, item: Mapping[str, Any]) -> dict[str, Any]:
    base = {"id": item["id"], "scope": item["scope"], "target_path": item["target_path"]}
    try:
        raw, file_mode = _read_optional_image(root, item["target_path"])
        if raw is None:
            return {
                **base,
                "status": "missing",
                "container_sha256": None,
                "container_mode": None,
            }
        text = raw.decode("utf-8")
    except (UpdateEngineError, UnicodeDecodeError) as exc:
        return {**base, "status": "conflict", "error": str(exc), "container_mode": None}
    begin = item["begin"]
    end = item["end"]
    if text.count(begin) > 1 or text.count(end) > 1 or text.count(begin) != text.count(end):
        return {**base, "status": "conflict", "error": "marked block markers are ambiguous"}
    if begin not in text:
        return {**base, "status": "missing", "container_sha256": contract.sha256_bytes(raw), "container_mode": file_mode}
    start = text.index(begin)
    finish_at = text.index(end, start)
    finish = finish_at + len(end)
    if finish < len(text) and text[finish] == "\n":
        finish += 1
    current = text[start:finish]
    if current != item["content"]:
        return {
            **base,
            "status": "conflict",
            "container_sha256": contract.sha256_bytes(raw),
            "container_mode": file_mode,
            "semantic_digest": contract.semantic_digest(
                {"begin": begin, "end": end, "content": current}
            ),
            "error": "managed marked block differs from its baseline",
        }
    return {
        **base,
        "status": "present",
        "type": "text_fragment",
        "semantic_digest": item["semantic_digest"],
        "container_sha256": contract.sha256_bytes(raw),
        "container_mode": file_mode,
    }


def _observe_path(root: Path, item: Mapping[str, Any]) -> dict[str, Any]:
    base = {"id": item["id"], "scope": item["scope"], "target_path": item["target_path"]}
    try:
        path = _safe_target(root, item["target_path"])
        metadata = os.lstat(path)
    except FileNotFoundError:
        return {**base, "status": "missing"}
    except (OSError, OwnershipConflict) as exc:
        return {**base, "status": "conflict", "error": str(exc)}
    if stat.S_ISLNK(metadata.st_mode):
        return {**base, "status": "present", "type": "symlink", "mode": "0777", "target": os.readlink(path)}
    if stat.S_ISDIR(metadata.st_mode):
        return {**base, "status": "present", "type": "directory", "mode": _mode(metadata)}
    if stat.S_ISREG(metadata.st_mode):
        return {
            **base,
            "status": "present",
            "type": "file",
            "mode": _mode(metadata),
            "sha256": contract.sha256_file(path),
        }
    return {**base, "status": "conflict", "type": "unsupported", "mode": _mode(metadata)}


def observe_projection(
    project_root: Path, projection: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Observe a projection without modifying the project or persistent state."""

    root = _safe_root(project_root)
    items = [copy.deepcopy(dict(item)) for item in projection]
    try:
        contract.validate_installed_projection(items)
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    observed: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: value["id"]):
        scope = item["scope"]
        if scope == "shared_json_fragment":
            observed.append(_observe_hook(root, item))
        elif scope == "marked_text_block":
            observed.append(_observe_block(root, item))
        else:
            observed.append(_observe_path(root, item))
    return observed


def _item_expected(item: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"status": "present", "type": item["type"]}
    for key in ("mode", "sha256", "target", "semantic_digest"):
        if key in item:
            expected[key] = item[key]
    return expected


def _matches(observation: Mapping[str, Any], item: Mapping[str, Any] | None) -> bool:
    if item is None:
        return observation.get("status") == "missing"
    expected = _item_expected(item)
    return all(observation.get(key) == value for key, value in expected.items())


def _diagnostics(
    projection: Sequence[Mapping[str, Any]], observations: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in projection}
    result = []
    for observation in observations:
        expected = by_id[observation["id"]]
        if not _matches(observation, expected):
            result.append(
                {
                    "id": expected["id"],
                    "path": expected["target_path"],
                    "expected": _item_expected(expected),
                    "actual": copy.deepcopy(dict(observation)),
                }
            )
    return result


def _legacy_projection_with_observed_modes(
    projection: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bind only installer-proven historical mode variance to actual state."""

    rendered = [copy.deepcopy(dict(item)) for item in projection]
    by_id = {item["id"]: item for item in rendered}
    for observed in observations:
        item = by_id[observed["id"]]
        policy = item.get("legacy_mode_policy")
        actual_mode = observed.get("mode")
        if policy not in {"created_directory_umask", "copyfile_destination"}:
            continue
        if not isinstance(actual_mode, str) or not re.fullmatch(r"0[0-7]{3}", actual_mode):
            continue
        expected = _item_expected(item)
        expected.pop("mode", None)
        if all(observed.get(key) == value for key, value in expected.items()):
            item["mode"] = actual_mode
    return rendered, _diagnostics(rendered, observations)


def _load_installed_pair(root: Path, vendor: str) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    lock_path = _safe_target(root, f"{vendor}/{contract.INSTALLED_LOCK_PATH}")
    manifest_path = _safe_target(root, f"{vendor}/{contract.INSTALLED_MANIFEST_PATH}")
    lock_bytes = _read_regular(lock_path, label="installed lock")
    lock = _json_object_bytes(lock_bytes, label="installed lock")
    manifest = _json_object_bytes(_read_regular(manifest_path, label="installed manifest"), label="installed manifest")
    try:
        manifest = contract.validate_release_manifest(manifest)
        lock = contract.validate_installed_lock(
            lock, release_manifest=manifest, vendor_dir=vendor
        )
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    if lock_bytes != contract.installed_lock_bytes(lock):
        raise UpdateEngineError("installed lock is not in deterministic canonical form")
    return lock, manifest, lock_bytes


def detect_installed_state(
    project_root: Path,
    vendor_dir: str = ".bugate",
    legacy_manifests: Iterable[Mapping[str, Any]] = (),
) -> InstalledState:
    root = _safe_root(project_root)
    try:
        vendor = contract.validate_vendor_dir(vendor_dir)
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    lock_path = root / vendor / contract.INSTALLED_LOCK_PATH
    installed_manifest_path = root / vendor / contract.INSTALLED_MANIFEST_PATH
    if lock_path.exists() or lock_path.is_symlink():
        try:
            lock, manifest, _raw = _load_installed_pair(root, vendor)
            projection = copy.deepcopy(lock["installed_projection"])
            observations = observe_projection(root, projection)
            diagnostics = _diagnostics(projection, observations)
            try:
                hook_images = _bound_hook_container_images(
                    root, projection, observations
                )
                _validate_global_hook_identities(
                    root, projection, container_images=hook_images
                )
            except (UpdateEngineError, OwnershipConflict) as exc:
                diagnostics.append({"error": str(exc)})
            return InstalledState(
                # A structurally valid lock/manifest pair is the ownership
                # authority even when one of its managed paths has drifted.
                # Status/verify still fail through ``go`` and diagnostics;
                # planning may additionally compare each current item with a
                # separately verified target projection.  Collapsing this to
                # ``conflict`` discarded the trusted old baseline and made an
                # interrupted/already-applied target image impossible to
                # reconcile safely.
                "locked",
                lock["installed_version"],
                tuple(projection),
                manifest,
                lock,
                None,
                tuple(observations),
                tuple(diagnostics),
                not diagnostics,
            )
        except (UpdateEngineError, OwnershipConflict) as exc:
            return InstalledState("conflict", None, (), None, None, None, (), ({"error": str(exc)},), False)
    if installed_manifest_path.exists() or installed_manifest_path.is_symlink():
        return InstalledState(
            "conflict", None, (), None, None, None, (),
            ({"error": "installed manifest exists without installed lock"},), False,
        )

    candidates: list[tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]] = []
    for raw in legacy_manifests:
        try:
            manifest = legacy_contract.validate_legacy_manifest(raw)
            projection = render_legacy_projection(manifest, vendor)
            observations = observe_projection(root, projection)
            projection, diagnostics = _legacy_projection_with_observed_modes(
                projection, observations
            )
        except (contract.ContractError, UpdateEngineError, OwnershipConflict) as exc:
            raise UpdateEngineError(f"invalid legacy detection input: {exc}") from exc
        candidates.append((manifest, projection, observations, diagnostics))
    matches = [candidate for candidate in candidates if not candidate[3]]
    if len(matches) == 1:
        manifest, projection, observations, _ = matches[0]
        return InstalledState(
            "legacy", manifest["bugate_version"], tuple(projection), None, None,
            manifest, tuple(observations), (), True,
        )
    if len(matches) > 1:
        return InstalledState(
            "conflict", None, (), None, None, None, (),
            ({"error": "legacy layout matches multiple release fingerprints", "versions": [item[0]["bugate_version"] for item in matches]},), False,
        )
    vendor_path = root / vendor
    if not (vendor_path.exists() or vendor_path.is_symlink()):
        return InstalledState("absent", None, (), None, None, None, (), (), False)
    ranked = sorted(candidates, key=lambda item: (len(item[3]), item[0]["bugate_version"]))
    details = []
    for manifest, _projection, _observations, diagnostics in ranked[:3]:
        details.append(
            {"candidate_version": manifest["bugate_version"], "mismatch_count": len(diagnostics), "mismatches": diagnostics}
        )
    return InstalledState("conflict", None, (), None, None, None, (), tuple(details or [{"error": "unknown pre-lock layout"}]), False)


def _profile_compatibility(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> dict[str, Any]:
    def observation(relative: str, data: bytes, mode: str) -> dict[str, Any]:
        return {
            "id": f"profile-input:{relative}",
            "scope": "workspace",
            "target_path": relative,
            "status": "present",
            "type": "file",
            "mode": mode,
            "sha256": contract.sha256_bytes(data),
        }

    def required(
        reason: str, base_observations: Sequence[Mapping[str, Any]] = ()
    ) -> dict[str, Any]:
        result = {
            "status": "migration_required",
            "blocking": True,
            "reason": reason,
        }
        if base_observations:
            result["base_observations"] = copy.deepcopy(list(base_observations))
        return result

    root_fd: int | None = None
    try:
        root_fd = _open_pinned_workspace_root(
            root, expected_identity=expected_root_identity
        )
        config_data, config_mode = _read_regular_beneath_at(
            root_fd, "bugate.config.yaml", label="bugate.config.yaml"
        )
        base_observations = [
            observation("bugate.config.yaml", config_data, config_mode)
        ]
        document = parse_nested_yaml(
            config_data.decode("utf-8"),
            strict=True,
            source="bugate.config.yaml",
        )
        if not isinstance(document, Mapping):
            raise ValueError("document root must be a mapping")
        base_config = canonicalize_config_document(
            document, source="bugate.config.yaml"
        )
        profile_name = base_config.get("profile") or base_config.get("active_profile")
        merged = base_config
        if profile_name:
            if not isinstance(profile_name, str) or not profile_name:
                raise ValueError("profile selector must be a non-empty path")
            if profile_name != profile_name.strip():
                raise ValueError("profile selector has unsafe boundary whitespace")
            selector = profile_name
            native_selector = Path(selector)
            if native_selector.is_absolute():
                # Runtime load_config accepts absolute selectors.  Preserve
                # that compatibility only when the lexical canonical path is
                # still inside this pinned workspace; convert it to a stable
                # relative observation and never expose the machine path.
                normalized_absolute = Path(os.path.normpath(selector))
                try:
                    relative_selector = normalized_absolute.relative_to(root)
                except ValueError as exc:
                    raise ValueError(
                        "profile selector must stay inside the project root"
                    ) from exc
                normalized_parts = list(relative_selector.parts)
            else:
                selector_path = PurePosixPath(selector)
                if ".." in selector_path.parts:
                    raise ValueError(
                        "profile selector must stay inside the project root"
                    )
                # ``./profile.yaml`` is a normal runtime spelling and resolves
                # to the same in-root path.
                normalized_parts = [
                    part for part in selector_path.parts if part not in {"", "."}
                ]
            if not normalized_parts:
                raise ValueError("profile selector must name a file")
            try:
                profile_relative = contract.validate_relative_path(
                    PurePosixPath(*normalized_parts).as_posix(),
                    field="profile path",
                )
            except contract.ContractError as exc:
                raise ValueError("profile selector is unsafe") from exc
            try:
                profile_data, profile_mode = _read_regular_beneath_at(
                    root_fd, profile_relative, label="selected profile"
                )
            except UpdateEngineError as exc:
                if str(exc) == "selected profile is missing":
                    raise ValueError("selected profile is missing") from exc
                if str(exc) == "selected profile must be a regular file":
                    raise ValueError(
                        "selected profile must be a regular file"
                    ) from exc
                raise
            base_observations.append(
                observation(profile_relative, profile_data, profile_mode)
            )
            profile = parse_nested_yaml(
                profile_data.decode("utf-8"),
                strict=True,
                source=profile_relative,
            )
            if not isinstance(profile, Mapping):
                raise ValueError("selected profile root must be a mapping")
            # Mirror ``bugate_core.load_config`` over the exact bytes captured
            # above: canonicalize aliases per document, deep-merge, then expose
            # the final aliases. Re-reading here would create a compatibility/hash
            # split-brain window if a file changed and changed back before apply.
            profile_config = canonicalize_config_document(
                profile, source=profile_relative
            )
            merged = canonicalize_config_document(
                deep_merge(base_config, profile_config),
                source="merged BUGate update compatibility config",
            )
        nested = merged.get("bugate")
        if nested is not None and not isinstance(nested, Mapping):
            raise ValueError("bugate must be a mapping")
        value = (
            nested.get("version")
            if isinstance(nested, Mapping) and "version" in nested
            else merged.get("version")
        )
        if value is None:
            value = manifest["profile_schema_compatibility"]["missing_maps_to"]
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value
        ):
            raise ValueError("config schema version is invalid")
        current = tuple(int(part) for part in value.split("."))
        minimum = tuple(
            int(part)
            for part in manifest["profile_schema_compatibility"]["min"].split(".")
        )
        maximum = tuple(
            int(part)
            for part in manifest["profile_schema_compatibility"][
                "max_exclusive"
            ].split(".")
        )
        if not minimum <= current < maximum:
            return {
                **required(
                    "profile schema is outside the release compatibility range",
                    base_observations,
                ),
                "schema_version": value,
            }
        result = {
            "status": "compatible",
            "blocking": False,
            "schema_version": value,
            "base_observations": copy.deepcopy(base_observations),
        }
        try:
            # Import lazily so the read-only updater engine does not impose a
            # role-runtime import on callers that never inspect a profile.
            from role_governance import governance_policy

            policy = governance_policy(merged)
        except Exception as exc:
            raise ValueError("role governance configuration is malformed") from exc
        if policy["mode"] != "required":
            result["migration"] = "migration_available"
        result["role_governance_activated"] = policy["mode"] == "required"
        return result
    except (OSError, UnicodeDecodeError, UpdateEngineError) as exc:
        return required(
            f"profile compatibility input is unreadable ({exc.__class__.__name__})",
            locals().get("base_observations", ()),
        )
    except ValueError as exc:
        return required(
            str(exc), locals().get("base_observations", ())
        )
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _classify(
    observation: Mapping[str, Any], old: Mapping[str, Any] | None, new: Mapping[str, Any] | None
) -> tuple[str, bool]:
    if observation.get("status") == "conflict":
        return "conflict", True
    old_match = _matches(observation, old)
    new_match = _matches(observation, new)
    if old is None:
        if new_match:
            return "unchanged", False
        if observation.get("status") == "missing":
            return "add", False
        return ("type_changed" if observation.get("type") != new.get("type") else "conflict"), True
    if new is None:
        if observation.get("status") == "missing":
            return "unchanged", False
        if old_match:
            return "delete", False
        if observation.get("type") != old.get("type"):
            return "type_changed", True
        return "locally_modified", True
    if new_match:
        return "unchanged", False
    if old_match:
        if old.get("type") == "file" and old.get("sha256") == new.get("sha256") and old.get("mode") != new.get("mode"):
            return "permission_changed", False
        if old.get("type") != new.get("type"):
            return "type_changed", False
        if old.get("scope") == "shared_json_fragment":
            return "hook_refresh", False
        return "update", False
    if observation.get("status") == "missing":
        return "conflict", True
    if observation.get("type") != old.get("type"):
        return "type_changed", True
    if observation.get("mode") != old.get("mode") and all(
        observation.get(key) == old.get(key) for key in ("type", "sha256", "target") if key in old
    ):
        return "permission_changed", True
    return "locally_modified", True


def _observe_update_item(
    root: Path,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Observe one update item against both trusted ownership baselines.

    Ordinary files expose their current hash/type/mode independently of a
    manifest. Shared JSON fragments and marked blocks are semantic fragments,
    so their observers need a candidate item in order to locate owned content.
    Try the trusted old and verified target forms, accepting only an exact
    match to one of them. A third/mixed/spoofed form remains a conflict.
    """

    candidate = old or new
    if candidate is None:
        raise UpdateEngineError("update item has neither an old nor target projection")
    scope = candidate.get("scope")
    if scope not in {"shared_json_fragment", "marked_text_block"}:
        return _observe_path(root, candidate)

    if scope == "marked_text_block" and old is not None and new is not None:
        try:
            merge_marked_block(
                _read_optional(root, candidate["target_path"]),
                prior_item=old,
                new_item=new,
            )
        except (UpdateEngineError, OwnershipConflict) as exc:
            return {
                "id": candidate["id"],
                "scope": scope,
                "target_path": candidate["target_path"],
                "status": "conflict",
                "error": str(exc),
            }

    observations: list[dict[str, Any]] = []
    for item in (old, new):
        if item is None:
            continue
        if item is new and old is not None and item == old:
            continue
        observation = (
            _observe_hook(root, item)
            if scope == "shared_json_fragment"
            else _observe_block(root, item)
        )
        observations.append(observation)
        if _matches(observation, item):
            return observation
    conflicts = [item for item in observations if item.get("status") == "conflict"]
    return copy.deepcopy(conflicts[0] if conflicts else observations[0])


def observe_update_changes(
    project_root: Path, changes: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Observe plan changes with exact old-or-target semantics, read-only."""

    root = _safe_root(project_root)
    normalized = list(changes)
    if any(not isinstance(item, Mapping) for item in normalized):
        raise UpdateEngineError("plan change must be an object")
    old_projection = [item.get("old") for item in normalized if item.get("old") is not None]
    new_projection = [item.get("new") for item in normalized if item.get("new") is not None]
    try:
        contract.validate_installed_projection(old_projection)
        contract.validate_installed_projection(new_projection)
    except contract.ContractError as exc:
        raise UpdateEngineError(f"invalid update projection: {exc}") from exc
    result: list[dict[str, Any]] = []
    for change in normalized:
        if not isinstance(change, Mapping):
            raise UpdateEngineError("plan change must be an object")
        old = change.get("old")
        new = change.get("new")
        if old is not None and not isinstance(old, Mapping):
            raise UpdateEngineError("plan old projection item must be an object")
        if new is not None and not isinstance(new, Mapping):
            raise UpdateEngineError("plan target projection item must be an object")
        result.append(_observe_update_item(root, old, new))
    return result


def _projection_map(projection: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: copy.deepcopy(dict(item)) for item in projection}


def _materialized_target_projection(
    manifest: Mapping[str, Any], vendor: str
) -> list[dict[str, Any]]:
    projection = contract.render_installed_projection(manifest, vendor)
    manifest_sha = contract.sha256_bytes(contract.canonical_json_bytes(manifest))
    for item in projection:
        if item["id"] == "metadata:installed-release-manifest":
            item.pop("digest_ref", None)
            item["sha256"] = manifest_sha
    contract.validate_installed_projection(projection)
    return projection


def _read_optional(root: Path, relative: str) -> bytes | None:
    return _read_optional_image(root, relative)[0]


def _read_optional_image(
    root: Path, relative: str
) -> tuple[bytes | None, str | None]:
    root_fd = _open_pinned_workspace_root(root)
    try:
        try:
            return _read_regular_beneath_at(root_fd, relative, label=relative)
        except UpdateEngineError as exc:
            if str(exc) == f"{relative} is missing":
                return None, None
            raise
    finally:
        os.close(root_fd)


def _assert_shared_base_image(
    changes: Iterable[Mapping[str, Any]],
    target_path: str,
    data: bytes | None,
    mode: str | None,
) -> None:
    relevant = [
        item
        for item in changes
        if item.get("target_path") == target_path
        and item.get("scope") in {"shared_json_fragment", "marked_text_block"}
    ]
    expected_hashes = {
        (item.get("base") or {}).get("container_sha256") for item in relevant
    }
    expected_modes = {
        (item.get("base") or {}).get("container_mode") for item in relevant
    }
    actual_hash = contract.sha256_bytes(data) if data is not None else None
    if expected_hashes != {actual_hash} or expected_modes != {mode}:
        raise OwnershipConflict(
            f"shared container changed during planning/apply: {target_path}"
        )


def _bound_hook_container_images(
    root: Path,
    projection: Iterable[Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, bytes | None]:
    items = list(projection)
    observed = list(observations)
    targets = sorted(
        {
            item["target_path"]
            for item in items
            if item.get("scope") == "shared_json_fragment"
        }
    )
    images: dict[str, bytes | None] = {}
    for target_path in targets:
        data, mode = _read_optional_image(root, target_path)
        synthetic_changes = [
            {
                "scope": "shared_json_fragment",
                "target_path": target_path,
                "base": observation,
            }
            for observation in observed
            if observation.get("scope") == "shared_json_fragment"
            and observation.get("target_path") == target_path
        ]
        _assert_shared_base_image(
            synthetic_changes, target_path, data, mode
        )
        images[target_path] = data
    return images


def build_update_plan(
    project_root: Path,
    vendor_dir: str,
    target_manifest: Mapping[str, Any],
    *,
    legacy_manifests: Iterable[Mapping[str, Any]] = (),
    archive_sha256: str | None = None,
    source_kind: str = "unpacked",
    updater_version: str | None = None,
    recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = _safe_root(project_root)
    root_metadata = os.lstat(root)
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise UpdateEngineError("project root identity is unsafe")
    workspace_root_identity = {
        "device": root_metadata.st_dev,
        "inode": root_metadata.st_ino,
    }
    try:
        manifest = contract.validate_current_release_manifest(target_manifest)
        vendor = contract.validate_vendor_dir(vendor_dir)
        if archive_sha256 is not None:
            contract.validate_sha256(archive_sha256, field="archive_sha256")
        if source_kind not in {"unpacked", "archive", "remote"}:
            raise contract.ContractError(f"unsupported release source kind: {source_kind!r}")
        if source_kind in {"archive", "remote"} and archive_sha256 is None:
            raise contract.ContractError(f"{source_kind} source requires a verified archive digest")
        if updater_version is not None:
            contract.require_updater_compatible(updater_version, manifest["updater_minimum_version"])
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    state = detect_installed_state(root, vendor, legacy_manifests)
    new_projection = _materialized_target_projection(manifest, vendor)
    old_map = _projection_map(state.projection)
    new_map = _projection_map(new_projection)
    same_version = state.version == manifest["bugate_version"]
    same_manifest = bool(
        state.manifest
        and state.manifest.get("self_digest") == manifest["self_digest"]
    )
    identities = sorted(set(old_map) | set(new_map))
    observation_inputs = [
        {"old": old_map.get(identity), "new": new_map.get(identity)}
        for identity in identities
    ]
    observations = (
        observe_update_changes(root, observation_inputs)
        if observation_inputs
        else []
    )
    observation_map = dict(zip(identities, observations, strict=True))
    changes: list[dict[str, Any]] = []
    recovery_required = bool(recovery and recovery.get("recovery_required"))
    baseline_trusted = state.kind in {"locked", "legacy"}
    blocking = not baseline_trusted or recovery_required
    for identity in identities:
        old = old_map.get(identity)
        new = new_map.get(identity)
        observation = observation_map[identity]
        classification, item_blocking = _classify(observation, old, new)
        if (
            identity == "metadata:installed-lock"
            and state.kind == "locked"
            and not same_manifest
        ):
            classification, item_blocking = "update", False
        blocking = blocking or item_blocking
        changes.append(
            {
                "id": identity,
                "scope": (new or old)["scope"],
                "target_path": (new or old)["target_path"],
                "classification": classification,
                "blocking": item_blocking,
                "base": copy.deepcopy(observation),
                "old": old,
                "new": new,
            }
        )
    compatibility = _profile_compatibility(
        root,
        manifest,
        expected_root_identity=(root_metadata.st_dev, root_metadata.st_ino),
    )
    blocking = blocking or compatibility["blocking"]
    hook_changes: list[dict[str, Any]] = []
    hook_images: dict[str, bytes | None] = {}
    for target_path in sorted({item["target_path"] for item in changes if item["scope"] == "shared_json_fragment"}):
        try:
            prior_hooks = _hook_items(list(old_map.values()), target_path)
            target_hooks = _hook_items(list(new_map.values()), target_path)
            before, before_mode = _read_optional_image(root, target_path)
            _assert_shared_base_image(
                changes, target_path, before, before_mode
            )
            hook_images[target_path] = before
            if before is None and prior_hooks and not target_hooks:
                hook_changes.append(
                    {
                        "target_path": target_path,
                        "changed": False,
                        "before_sha256": None,
                        "after_sha256": None,
                    }
                )
                continue
            merged = merge_hook_file(
                before,
                prior_projection=prior_hooks,
                new_projection=target_hooks,
                target_path=target_path,
            )
            hook_changes.append(
                {
                    "target_path": target_path,
                    "changed": merged.changed,
                    "before_sha256": contract.sha256_bytes(before) if before is not None else None,
                    "after_sha256": contract.sha256_bytes(merged.content),
                }
            )
        except (UpdateEngineError, OwnershipConflict) as exc:
            blocking = True
            hook_changes.append({"target_path": target_path, "changed": False, "conflict": str(exc)})
    try:
        _validate_global_hook_identities(
            root,
            old_map.values(),
            new_map.values(),
            container_images=hook_images,
        )
    except (UpdateEngineError, OwnershipConflict) as exc:
        blocking = True
        hook_changes.append(
            {
                "target_path": "shared-hook-catalog",
                "changed": False,
                "conflict": str(exc),
            }
        )
    if same_version and not same_manifest:
        blocking = True
    actionable = any(item["classification"] not in {"unchanged"} for item in changes)
    no_op = state.kind == "locked" and same_manifest and not actionable and not any(item.get("changed") for item in hook_changes)
    if no_op:
        lock_candidate = copy.deepcopy(state.lock)
    else:
        try:
            lock_candidate = contract.build_installed_lock(
                manifest,
                previous_version=state.version,
                archive_sha256=archive_sha256,
                vendor_dir=vendor,
                # The committed lock records the verified target worker that is
                # installed by this release.  ``updater_version`` above is only
                # the launcher admission check against the manifest minimum.
                updater_version=manifest["bugate_version"],
            )
        except contract.ContractError as exc:
            raise UpdateEngineError(str(exc)) from exc
    operations: list[dict[str, Any]] = []
    shared_targets: set[tuple[str, str]] = set()
    for change in changes:
        classification = change["classification"]
        if classification == "unchanged":
            continue
        scope = change["scope"]
        if scope in {"shared_json_fragment", "marked_text_block"}:
            shared_targets.add((scope, change["target_path"]))
            continue
        action = "delete" if change["new"] is None else "replace"
        operations.append(
            {
                "id": change["id"],
                "scope": scope,
                "target_path": change["target_path"],
                "action": action,
                "base": copy.deepcopy(change["base"]),
                "old": copy.deepcopy(change["old"]),
                "new": copy.deepcopy(change["new"]),
                "source_path": change["new"].get("source_path") if change["new"] else None,
            }
        )
    for scope, target_path in sorted(shared_targets):
        operations.append(
            {
                "id": f"shared:{scope}:{target_path}",
                "scope": scope,
                "target_path": target_path,
                "action": "semantic_merge",
                "base": [
                    copy.deepcopy(item["base"])
                    for item in changes
                    if item["scope"] == scope and item["target_path"] == target_path
                ],
                "old": [
                    copy.deepcopy(item["old"])
                    for item in changes
                    if item["scope"] == scope and item["target_path"] == target_path and item["old"] is not None
                ],
                "new": [
                    copy.deepcopy(item["new"])
                    for item in changes
                    if item["scope"] == scope and item["target_path"] == target_path and item["new"] is not None
                ],
                "source_path": None,
            }
        )
    plan: dict[str, Any] = {
        "schema_version": UPDATE_PLAN_SCHEMA_VERSION,
        "workspace_root_identity": workspace_root_identity,
        "from_version": state.version,
        "to_version": manifest["bugate_version"],
        "installed_kind": state.kind,
        "from_state_manifest": copy.deepcopy(
            state.legacy_manifest if state.kind == "legacy" else state.manifest
        ),
        "release_digest": manifest["self_digest"],
        "manifest_sha256": contract.sha256_bytes(contract.canonical_json_bytes(manifest)),
        "target_manifest": copy.deepcopy(manifest),
        "installed_lock_candidate": lock_candidate,
        "archive_sha256": archive_sha256,
        "source_kind": source_kind,
        "base_observations": copy.deepcopy(observations),
        "managed_changes": changes,
        "hook_changes": hook_changes,
        "profile_compatibility": compatibility,
        "profile_base_observations": copy.deepcopy(
            compatibility.get("base_observations", [])
        ),
        "codex_hook_hash_changed": any(item.get("changed") and item["target_path"] == ".codex/hooks.json" for item in hook_changes),
        "new_session_required": any(item.get("changed") for item in hook_changes),
        "rollback_available": state.kind in {"locked", "legacy"} and not no_op,
        "decision": "NO-GO" if blocking else "GO",
        "no_op": no_op,
        "preserve_installed_lock": no_op,
        "state_diagnostics": copy.deepcopy(list(state.diagnostics)),
        "recovery": copy.deepcopy(dict(recovery or {})),
        "transaction_operations": operations,
    }
    plan["stale_managed_files"] = [
        item["target_path"]
        for item in changes
        if item["classification"] == "delete"
    ]
    plan["local_modifications"] = [
        {
            "id": item["id"],
            "target_path": item["target_path"],
            "classification": item["classification"],
            "actual": copy.deepcopy(item["base"]),
        }
        for item in changes
        if item["blocking"]
    ]
    plan["migration_status"] = compatibility.get("migration") or compatibility["status"]
    no_go_reasons: list[str] = []
    if not baseline_trusted and state.kind != "absent":
        no_go_reasons.append("installed_state_conflict")
    if state.kind == "absent":
        no_go_reasons.append("existing_installation_not_found")
    if recovery_required:
        no_go_reasons.append("recovery_required")
    if compatibility["blocking"]:
        no_go_reasons.append("migration_required")
    if same_version and not same_manifest:
        no_go_reasons.append("same_version_release_digest_mismatch")
    if plan["local_modifications"]:
        no_go_reasons.append("managed_local_modification_or_conflict")
    if any("conflict" in item for item in hook_changes):
        no_go_reasons.append("hook_ownership_conflict")
    plan["no_go_reasons"] = list(dict.fromkeys(no_go_reasons))
    plan["go_reasons"] = (
        ["recognized_baseline", "strict_current_release", "profile_compatible"]
        if not blocking
        else []
    )
    plan["plan_digest"] = contract.sha256_bytes(contract.canonical_json_bytes(plan))
    return plan


def validate_plan_base(
    project_root: Path, vendor_dir: str, plan: Mapping[str, Any]
) -> None:
    if plan.get("schema_version") != UPDATE_PLAN_SCHEMA_VERSION:
        raise UpdateEngineError("unsupported update plan schema")
    supplied = plan.get("plan_digest")
    payload = copy.deepcopy(dict(plan))
    payload.pop("plan_digest", None)
    expected = contract.sha256_bytes(contract.canonical_json_bytes(payload))
    if supplied != expected:
        raise UpdateEngineError("plan digest mismatch")
    root = _safe_root(project_root)
    expected_root_identity = plan.get("workspace_root_identity")
    if (
        not isinstance(expected_root_identity, Mapping)
        or set(expected_root_identity) != {"device", "inode"}
        or not all(
            isinstance(value, int) for value in expected_root_identity.values()
        )
    ):
        raise UpdateEngineError("plan workspace root identity is invalid")
    root_metadata = os.lstat(root)
    actual_root_identity = {
        "device": root_metadata.st_dev,
        "inode": root_metadata.st_ino,
    }
    if actual_root_identity != dict(expected_root_identity):
        raise UpdateEngineError("workspace root identity drift detected")
    try:
        vendor = contract.validate_vendor_dir(vendor_dir)
    except contract.ContractError as exc:
        raise UpdateEngineError(str(exc)) from exc
    for item in plan.get("managed_changes", []):
        managed = item.get("old") or item.get("new")
        if managed and managed.get("scope") in {"vendor", "generated_metadata"}:
            if not managed.get("target_path", "").startswith(vendor + "/"):
                raise UpdateEngineError("plan vendor_dir differs from managed target paths")
    changes = plan.get("managed_changes")
    if not isinstance(changes, list):
        raise UpdateEngineError("plan managed_changes must be an array")
    current = observe_update_changes(root, changes)
    expected_base = plan.get("base_observations")
    if current != expected_base:
        raise UpdateEngineError("plan base drift detected")
    profile_base = plan.get("profile_base_observations")
    if not isinstance(profile_base, list) or not profile_base:
        raise UpdateEngineError("plan profile/config base observations are missing")
    current_profile: list[dict[str, Any]] = []
    profile_root_fd = _open_pinned_workspace_root(
        root,
        expected_identity=(
            expected_root_identity["device"],
            expected_root_identity["inode"],
        ),
    )
    try:
        for item in profile_base:
            if not isinstance(item, Mapping):
                raise UpdateEngineError("plan profile/config observation is invalid")
            if item.get("scope") != "workspace" or item.get("type") != "file":
                raise UpdateEngineError("plan profile/config observation type is invalid")
            try:
                relative = contract.validate_relative_path(
                    item.get("target_path"), field="profile/config target"
                )
                data, mode = _read_regular_beneath_at(
                    profile_root_fd, relative, label="profile/config input"
                )
            except (contract.ContractError, UpdateEngineError) as exc:
                raise UpdateEngineError(
                    "plan profile/config base drift detected"
                ) from exc
            current_profile.append(
                {
                    "id": item["id"],
                    "scope": "workspace",
                    "target_path": relative,
                    "status": "present",
                    "type": "file",
                    "mode": mode,
                    "sha256": contract.sha256_bytes(data),
                }
            )
    finally:
        os.close(profile_root_fd)
    if current_profile != profile_base:
        raise UpdateEngineError("plan profile/config base drift detected")


def get_status(
    project_root: Path,
    vendor_dir: str = ".bugate",
    legacy_manifests: Iterable[Mapping[str, Any]] = (),
    recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = _safe_root(project_root)
    state = detect_installed_state(root, vendor_dir, legacy_manifests)
    result = state.to_dict()
    result["schema_version"] = UPDATE_PLAN_SCHEMA_VERSION
    result["vendor_dir"] = vendor_dir
    result["recovery"] = copy.deepcopy(dict(recovery or {}))
    result["recovery_required"] = bool(recovery and recovery.get("recovery_required"))
    result["decision"] = (
        "GO"
        if state.kind in {"locked", "legacy"}
        and state.go
        and not result["recovery_required"]
        else "NO-GO"
    )
    result["no_go_reasons"] = []
    if state.kind == "absent":
        result["no_go_reasons"].append("existing_installation_not_found")
    elif not state.go:
        result["no_go_reasons"].append("installed_state_conflict")
    if result["recovery_required"]:
        result["no_go_reasons"].append("recovery_required")
    return result


def verify_installed(
    project_root: Path,
    vendor_dir: str = ".bugate",
    legacy_manifests: Iterable[Mapping[str, Any]] = (),
    recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = _safe_root(project_root)
    state = detect_installed_state(root, vendor_dir, legacy_manifests)
    failures = copy.deepcopy(list(state.diagnostics))
    if state.kind not in {"locked", "legacy"}:
        failures.append(
            {
                "error": "installed state is neither a verified lock-based nor "
                f"an exact supported legacy installation: {state.kind}"
            }
        )
    recovery_document = copy.deepcopy(dict(recovery or {}))
    recovery_required = bool(recovery_document.get("recovery_required"))
    if recovery_required:
        failures.append({"error": "transaction recovery is required"})
    result = {
        "schema_version": UPDATE_PLAN_SCHEMA_VERSION,
        "status": "passed" if not failures else "failed",
        "decision": "GO" if not failures else "NO-GO",
        "installed_version": state.version,
        "installed_kind": state.kind,
        "lock_based": state.kind == "locked",
        "failures": failures,
        "checked_items": len(state.observations),
        "recovery": recovery_document,
        "recovery_required": recovery_required,
        "no_go_reasons": ["recovery_required"] if recovery_required else [],
    }
    return result


def _hook_items(
    projection: Iterable[Mapping[str, Any]], target_path: str
) -> list[dict[str, Any]]:
    return sorted(
        [
            copy.deepcopy(dict(item))
            for item in projection
            if item.get("scope") == "shared_json_fragment"
            and item.get("target_path") == target_path
        ],
        key=lambda item: item["id"],
    )


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass
class _JsonSpan:
    value: Any
    start: int
    end: int
    kind: str
    members: dict[str, "_JsonSpan"] | None = None
    elements: list["_JsonSpan"] | None = None


class _JsonSpanParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.decoder = json.JSONDecoder(parse_constant=_reject_json_constant)

    def _ws(self, position: int) -> int:
        while position < len(self.text) and self.text[position] in " \t\r\n":
            position += 1
        return position

    def parse(self) -> _JsonSpan:
        node = self._value(0)
        if self._ws(node.end) != len(self.text):
            raise OwnershipConflict("hook JSON has trailing non-whitespace data")
        return node

    def _value(self, position: int) -> _JsonSpan:
        start = self._ws(position)
        if start >= len(self.text):
            raise OwnershipConflict("hook JSON ended unexpectedly")
        token = self.text[start]
        if token == "{":
            return self._object(start)
        if token == "[":
            return self._array(start)
        try:
            value, end = self.decoder.raw_decode(self.text, start)
        except (json.JSONDecodeError, ValueError) as exc:
            raise OwnershipConflict(f"invalid hook JSON: {exc}") from exc
        return _JsonSpan(value, start, end, "scalar")

    def _object(self, start: int) -> _JsonSpan:
        position = self._ws(start + 1)
        value: dict[str, Any] = {}
        members: dict[str, _JsonSpan] = {}
        if position < len(self.text) and self.text[position] == "}":
            return _JsonSpan(value, start, position + 1, "object", members=members)
        while True:
            try:
                key, key_end = self.decoder.raw_decode(self.text, position)
            except (json.JSONDecodeError, ValueError) as exc:
                raise OwnershipConflict(f"invalid hook JSON object key: {exc}") from exc
            if not isinstance(key, str):
                raise OwnershipConflict("hook JSON object key is not text")
            if key in members:
                raise OwnershipConflict(f"duplicate hook JSON key: {key}")
            position = self._ws(key_end)
            if position >= len(self.text) or self.text[position] != ":":
                raise OwnershipConflict("hook JSON object key lacks ':'")
            child = self._value(position + 1)
            value[key] = child.value
            members[key] = child
            position = self._ws(child.end)
            if position >= len(self.text):
                raise OwnershipConflict("hook JSON object is unterminated")
            if self.text[position] == "}":
                return _JsonSpan(value, start, position + 1, "object", members=members)
            if self.text[position] != ",":
                raise OwnershipConflict("hook JSON object lacks ','")
            position = self._ws(position + 1)

    def _array(self, start: int) -> _JsonSpan:
        position = self._ws(start + 1)
        value: list[Any] = []
        elements: list[_JsonSpan] = []
        if position < len(self.text) and self.text[position] == "]":
            return _JsonSpan(value, start, position + 1, "array", elements=elements)
        while True:
            child = self._value(position)
            value.append(child.value)
            elements.append(child)
            position = self._ws(child.end)
            if position >= len(self.text):
                raise OwnershipConflict("hook JSON array is unterminated")
            if self.text[position] == "]":
                return _JsonSpan(value, start, position + 1, "array", elements=elements)
            if self.text[position] != ",":
                raise OwnershipConflict("hook JSON array lacks ','")
            position = self._ws(position + 1)


def _apply_text_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, key=lambda value: value[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _surgical_hook_bytes(
    existing_bytes: bytes,
    original: Mapping[str, Any],
    merged: Mapping[str, Any],
    prior: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> bytes:
    try:
        text = existing_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OwnershipConflict("hook JSON is not UTF-8") from exc
    root = _JsonSpanParser(text).parse()
    if root.kind != "object" or root.members is None:
        raise OwnershipConflict("hook JSON root is not an object")
    hooks_node = root.members.get("hooks")
    merged_hooks = merged.get("hooks", {})
    if hooks_node is None:
        prefix = "," if root.members else ""
        insertion = prefix + _encoded("hooks") + ":" + _encoded(merged_hooks)
        return _apply_text_edits(text, [(root.end - 1, root.end - 1, insertion)]).encode("utf-8")
    if hooks_node.kind != "object" or hooks_node.members is None:
        raise OwnershipConflict("hook JSON .hooks is not an object")
    original_hooks = original.get("hooks", {})
    events = sorted({item["event"] for item in prior + new})
    edits: list[tuple[int, int, str]] = []
    missing: list[str] = []
    for event in events:
        event_node = hooks_node.members.get(event)
        new_items = [item for item in new if item["event"] == event]
        if event_node is None:
            if event in merged_hooks:
                missing.append(
                    _encoded(event)
                    + ":"
                    + _encoded([item["value"] for item in new_items])
                )
            continue
        if event_node.kind != "array" or event_node.elements is None:
            raise OwnershipConflict(f"hook event is not an array: {event}")
        old_digests = {
            item["semantic_digest"]
            for item in prior
            if item["event"] == event
        }
        new_digests = {item["semantic_digest"] for item in new_items}
        retained: list[str] = []
        for value, span in zip(original_hooks.get(event, []), event_node.elements):
            digest = contract.semantic_digest({"event": event, "value": value})
            if digest not in old_digests and digest not in new_digests:
                retained.append(text[span.start:span.end])
        retained.extend(_encoded(item["value"]) for item in new_items)
        edits.append((event_node.start, event_node.end, "[" + ",".join(retained) + "]"))
    if missing:
        prefix = "," if hooks_node.members else ""
        edits.append((hooks_node.end - 1, hooks_node.end - 1, prefix + ",".join(missing)))
    return _apply_text_edits(text, edits).encode("utf-8")


def _hook_merge_document(
    document: dict[str, Any], prior: list[dict[str, Any]], new: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    hooks = document.get("hooks")
    if hooks is None:
        if not new:
            # The target owns no fragments in this container. Absence of the
            # hooks key is already the exact target state and must not cause a
            # formatting-only mutation or a spurious hook re-trust signal.
            return document, []
        hooks = {}
        document["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise OwnershipConflict("hook document .hooks is not an object")
    details: list[dict[str, Any]] = []
    events = sorted({item["event"] for item in prior + new})
    for event in events:
        current = hooks.get(event, [])
        if not isinstance(current, list):
            raise OwnershipConflict(f"hook event is not an array: {event}")
        old_items = [item for item in prior if item["event"] == event]
        new_items = [item for item in new if item["event"] == event]
        old_digests = {item["semantic_digest"]: item for item in old_items}
        new_digests = {item["semantic_digest"]: item for item in new_items}
        known_identities = {
            item["hook_identity"]
            for item in old_items + new_items
            if isinstance(item.get("hook_identity"), str)
        }
        owned_indices: set[int] = set()
        seen_identities: dict[str, int] = {}
        current_digests = [
            contract.semantic_digest({"event": event, "value": value})
            for value in current
        ]
        for index, value in enumerate(current):
            digest = contract.semantic_digest({"event": event, "value": value})
            identities = _identities(value)
            for identity in identities & known_identities:
                if identity in seen_identities:
                    raise OwnershipConflict(f"duplicate BUGate hook identity {identity} in {event}")
                seen_identities[identity] = index
            if digest in old_digests or digest in new_digests:
                owned_indices.add(index)
                continue
            if identities & known_identities:
                raise OwnershipConflict(f"spoofed or locally modified BUGate hook identity in {event}")
        old_complete = all(current_digests.count(item["semantic_digest"]) == 1 for item in old_items)
        new_complete = all(
            current_digests.count(item["semantic_digest"]) == 1
            for item in new_items
        ) and all(
            current_digests.count(item["semantic_digest"]) == 0
            for item in old_items
            if item["semantic_digest"] not in new_digests
        )
        if old_items and not (old_complete or new_complete):
            missing = [
                item["id"]
                for item in old_items
                if current_digests.count(item["semantic_digest"]) != 1
            ]
            raise OwnershipConflict(
                "prior BUGate hook fragment is missing, mixed, or duplicated: "
                + ", ".join(missing)
            )
        if new_complete:
            details.append(
                {
                    "event": event,
                    "owned_removed": 0,
                    "owned_added": 0,
                    "sut_preserved": len(current) - len(new_items),
                    "already_target": True,
                }
            )
            continue
        sut_entries = [value for index, value in enumerate(current) if index not in owned_indices]
        merged = sut_entries + [item["value"] for item in new_items]
        if event in hooks or merged:
            hooks[event] = merged
        details.append({"event": event, "owned_removed": len(owned_indices), "owned_added": len(new_items), "sut_preserved": len(sut_entries)})
    return document, details


def merge_hook_file(
    existing_bytes: bytes | None,
    *,
    prior_projection: Iterable[Mapping[str, Any]],
    new_projection: Iterable[Mapping[str, Any]],
    target_path: str,
) -> MergeResult:
    """Return a semantic hook merge; ownership requires an exact prior/new digest."""

    contract.validate_relative_path(target_path, field="hook target")
    prior = _hook_items(prior_projection, target_path)
    new = _hook_items(new_projection, target_path)
    if existing_bytes is None:
        if prior:
            raise OwnershipConflict(f"shared hook file is missing: {target_path}")
        document: dict[str, Any] = {}
    else:
        document = _json_object_bytes(existing_bytes, label=f"hook file {target_path}")
    merged, details = _hook_merge_document(copy.deepcopy(document), prior, new)
    if existing_bytes is None:
        output = (json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    elif merged == document:
        output = existing_bytes
    else:
        output = _surgical_hook_bytes(existing_bytes, document, merged, prior, new)
        reparsed = _json_object_bytes(output, label=f"merged hook file {target_path}")
        if reparsed != merged:
            raise OwnershipConflict("surgical hook merge changed unintended semantics")
    return MergeResult(output, existing_bytes != output, tuple(details))


def merge_marked_block(
    existing_bytes: bytes | None,
    *,
    prior_item: Mapping[str, Any] | None,
    new_item: Mapping[str, Any] | None,
) -> MergeResult:
    """Surgically add/replace/delete one exact marked block, preserving all other bytes."""

    item = new_item or prior_item
    if item is None or item.get("scope") != "marked_text_block":
        raise UpdateEngineError("marked block merge requires a marked_text_block item")
    try:
        text = (existing_bytes or b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OwnershipConflict("marked block target is not UTF-8") from exc
    marker_pairs: list[tuple[str, str]] = []
    for candidate in (prior_item, new_item):
        if candidate is None:
            continue
        pair = (candidate["begin"], candidate["end"])
        if pair not in marker_pairs:
            marker_pairs.append(pair)
    found: list[tuple[int, int, str]] = []
    for begin, end in marker_pairs:
        if text.count(begin) > 1 or text.count(end) > 1 or text.count(begin) != text.count(end):
            raise OwnershipConflict("marked block markers are duplicate or unbalanced")
        if begin not in text:
            continue
        start = text.index(begin)
        finish = text.index(end, start) + len(end)
        if finish < len(text) and text[finish] == "\n":
            finish += 1
        found.append((start, finish, text[start:finish]))
    if len(found) > 1:
        unique_spans = {(start, finish) for start, finish, _current in found}
        if len(unique_spans) > 1:
            raise OwnershipConflict("both prior and target marked blocks are present")
        found = [found[0]]
    start = finish = None
    current = None
    if found:
        start, finish, current = found[0]
    prior_content = prior_item.get("content") if prior_item else None
    new_content = new_item.get("content") if new_item else None
    if current is not None and current not in {prior_content, new_content}:
        raise OwnershipConflict("managed marked block is locally modified")
    if prior_item is not None and current is None and new_content is None:
        raise OwnershipConflict("prior managed marked block is missing")
    if current is None:
        if prior_item is not None:
            raise OwnershipConflict("prior managed marked block is missing")
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        output_text = text + separator + (new_content or "")
    elif new_content is None:
        output_text = text[:start] + text[finish:]
    else:
        output_text = text[:start] + new_content + text[finish:]
    output = output_text.encode("utf-8")
    return MergeResult(output, output != (existing_bytes or b""), ({"target_path": item["target_path"]},))


def materialize_shared_outputs(
    project_root: Path, plan: Mapping[str, Any]
) -> dict[str, bytes]:
    """Materialize complete hook/block container post-images without writing them."""

    root = _safe_root(project_root)
    changes = plan.get("managed_changes")
    if not isinstance(changes, list):
        raise UpdateEngineError("plan managed_changes must be an array")
    outputs: dict[str, bytes] = {}
    hook_targets = sorted(
        {
            item["target_path"]
            for item in changes
            if item.get("scope") == "shared_json_fragment"
            and item.get("classification") != "unchanged"
        }
    )
    for target_path in hook_targets:
        prior = [
            item["old"]
            for item in changes
            if item.get("scope") == "shared_json_fragment"
            and item.get("target_path") == target_path
            and item.get("old") is not None
        ]
        new = [
            item["new"]
            for item in changes
            if item.get("scope") == "shared_json_fragment"
            and item.get("target_path") == target_path
            and item.get("new") is not None
        ]
        current, current_mode = _read_optional_image(root, target_path)
        _assert_shared_base_image(
            changes, target_path, current, current_mode
        )
        outputs[target_path] = merge_hook_file(
            current,
            prior_projection=prior,
            new_projection=new,
            target_path=target_path,
        ).content
    block_targets = sorted(
        {
            item["target_path"]
            for item in changes
            if item.get("scope") == "marked_text_block"
            and item.get("classification") != "unchanged"
        }
    )
    for target_path in block_targets:
        relevant = [
            item
            for item in changes
            if item.get("scope") == "marked_text_block"
            and item.get("target_path") == target_path
        ]
        prior = next((item["old"] for item in relevant if item.get("old") is not None), None)
        new = next((item["new"] for item in relevant if item.get("new") is not None), None)
        current, current_mode = _read_optional_image(root, target_path)
        _assert_shared_base_image(
            changes, target_path, current, current_mode
        )
        outputs[target_path] = merge_marked_block(
            current, prior_item=prior, new_item=new
        ).content
    return outputs


def _physical_pre(observation: Mapping[str, Any]) -> dict[str, Any]:
    if observation.get("status") == "missing":
        return {"state": "absent"}
    kind = observation.get("type")
    if kind == "file":
        return {"state": "file", "sha256": observation["sha256"], "mode": observation["mode"]}
    if kind == "directory":
        return {"state": "directory", "mode": observation["mode"]}
    if kind == "symlink":
        return {"state": "symlink", "target": observation["target"], "mode": "0777"}
    raise UpdateEngineError(f"cannot materialize unsafe pre-image: {observation.get('id')}")


def _physical_post(item: Mapping[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {"state": "absent"}
    kind = item["type"]
    if kind == "file":
        if "sha256" not in item:
            raise UpdateEngineError(f"file post-image lacks sha256: {item['id']}")
        return {"state": "file", "sha256": item["sha256"], "mode": item["mode"]}
    if kind == "directory":
        return {"state": "directory", "mode": item["mode"]}
    if kind == "symlink":
        return {"state": "symlink", "target": item["target"], "mode": "0777"}
    raise UpdateEngineError(f"cannot materialize post-image type: {kind}")


def transaction_material(
    plan: Mapping[str, Any],
    source_root: Path | None = None,
    *,
    shared_outputs: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Convert a GO plan into transaction physical images and verified payloads."""

    if plan.get("decision") != "GO":
        raise UpdateEngineError("transaction material requires a GO plan")
    operations = plan.get("transaction_operations")
    if not isinstance(operations, list):
        raise UpdateEngineError("plan lacks transaction_operations")
    source = source_root.expanduser().resolve() if source_root is not None else None
    payload_sources: dict[str, str] = {}
    payload_bytes: dict[str, bytes] = {}
    physical: list[dict[str, Any]] = []
    gitignore_operation_id: str | None = None
    manifest = plan.get("target_manifest")
    lock_candidate = plan.get("installed_lock_candidate")
    if not isinstance(manifest, Mapping) or not isinstance(lock_candidate, Mapping):
        raise UpdateEngineError("plan lacks target manifest or installed lock candidate")
    manifest_bytes = contract.canonical_json_bytes(manifest)
    lock_bytes = contract.installed_lock_bytes(lock_candidate)
    for operation in operations:
        identity = operation["id"]
        scope = operation["scope"]
        target_path = operation["target_path"]
        if scope in {"shared_json_fragment", "marked_text_block"}:
            if shared_outputs is None or target_path not in shared_outputs:
                raise UpdateEngineError(f"shared post-image was not materialized: {target_path}")
            content = shared_outputs[target_path]
            if not isinstance(content, bytes):
                raise UpdateEngineError(f"shared post-image is not bytes: {target_path}")
            base_values = operation.get("base") or []
            base = base_values[0] if base_values else {"status": "missing"}
            before_sha = base.get("container_sha256")
            before_mode = base.get("container_mode")
            pre = (
                {"state": "absent"}
                if before_sha is None
                else {"state": "file", "sha256": before_sha, "mode": before_mode or "0644"}
            )
            post = {"state": "file", "sha256": contract.sha256_bytes(content), "mode": before_mode or "0644"}
            payload_bytes[identity] = content
            if scope == "marked_text_block":
                gitignore_operation_id = identity
            physical.append({"id": identity, "target_path": target_path, "pre": pre, "post": post})
            continue
        old = operation.get("old")
        new = operation.get("new")
        pre = _physical_pre(operation["base"])
        if new is not None and new.get("id") == "metadata:installed-release-manifest":
            materialized = copy.deepcopy(new)
            materialized["sha256"] = contract.sha256_bytes(manifest_bytes)
            post = _physical_post(materialized)
            payload_bytes[identity] = manifest_bytes
        elif new is not None and new.get("id") == "metadata:installed-lock":
            materialized = copy.deepcopy(new)
            materialized["sha256"] = contract.sha256_bytes(lock_bytes)
            post = _physical_post(materialized)
            payload_bytes[identity] = lock_bytes
        else:
            post = _physical_post(new)
            source_path = operation.get("source_path")
            if new is not None and new.get("type") == "file":
                if not isinstance(source_path, str):
                    raise UpdateEngineError(f"file operation lacks source_path: {identity}")
                contract.validate_relative_path(source_path, field="payload source")
                payload_sources[identity] = source_path
                if source is not None:
                    candidate = source / source_path
                    data = _read_regular(candidate, label=f"payload {source_path}")
                    if contract.sha256_bytes(data) != new["sha256"]:
                        raise UpdateEngineError(f"payload hash mismatch: {source_path}")
                    payload_bytes[identity] = data
        physical.append({"id": identity, "target_path": target_path, "pre": pre, "post": post})
    return {
        "operations": physical,
        "payload_sources": payload_sources,
        "payload_bytes": payload_bytes,
        "gitignore_operation_id": gitignore_operation_id,
    }
