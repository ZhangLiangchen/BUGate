#!/usr/bin/env python3
"""Verify and stage BUGate release sources without touching an imported repo.

The updater engine supplies either an official archive plus its checksum asset,
or an already-unpacked release root.  This module validates the complete source
before returning it.  Its only writes are beneath the caller-provided empty
staging directory used by :func:`prepare_archive`.

SHA-256 makes a chosen release tamper-evident; it is not publisher identity or
a signed supply-chain guarantee.
"""
from __future__ import annotations

import ast
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Mapping

import bugate_install_contract as contract


MAX_CHECKSUM_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_SYMLINK_BYTES = 16 * 1024
_CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  ([^\x00\r\n\t ]+)$")
_METADATA_PATHS = {
    contract.RELEASE_MANIFEST_PATH,
    contract.INSTALLED_MANIFEST_PATH,
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    "scripts/bugate_update.py",
}


class UpdateSourceError(RuntimeError):
    """Base class for stable updater-source validation failures."""


class ChecksumError(UpdateSourceError):
    """The checksum asset or archive digest is invalid or ambiguous."""


class ArchiveSafetyError(UpdateSourceError):
    """The archive layout, entry type, link, or resource use is unsafe."""


class ManifestError(UpdateSourceError):
    """Release metadata is absent, malformed, inconsistent, or incomplete."""


class StagingError(UpdateSourceError):
    """The caller-provided staging boundary cannot be used safely."""


def _note_failure(failure: BaseException, note: str) -> None:
    """Attach diagnostics while retaining Python 3.10 compatibility."""

    add_note = getattr(failure, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    try:
        current = str(failure)
        tail = tuple(getattr(failure, "args", ()))[1:]
        failure.args = (f"{current}; {note}", *tail)
    except (AttributeError, TypeError):
        pass


@dataclass(frozen=True)
class SourceEntry:
    """A verified release-root entry, relative to the archive prefix."""

    path: str
    type: str
    mode: str
    sha256: str | None = None
    target: str | None = None
    size: int = 0

    def manifest_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {"type": self.type, "mode": self.mode}
        if self.type == "file":
            record["sha256"] = self.sha256
        elif self.type == "symlink":
            record["target"] = self.target
        return record


@dataclass(frozen=True)
class ArchiveInspection:
    """Safety-checked archive inventory and the metadata needed for validation."""

    archive_format: str
    prefix: str
    version: str
    entries: tuple[SourceEntry, ...]
    manifest_bytes: bytes
    codex_plugin_bytes: bytes
    claude_plugin_bytes: bytes
    updater_bytes: bytes


@dataclass(frozen=True)
class PreparedRelease:
    """A fully verified release source ready for the updater planner."""

    root: Path
    manifest: dict[str, Any]
    archive_sha256: str | None
    source_kind: str
    root_identity: tuple[int, int]


def _regular_file(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise UpdateSourceError(f"{label} is not readable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise UpdateSourceError(f"{label} must be a regular non-symlink file: {path}")
    return metadata


def _read_small_regular(path: Path, *, label: str, limit: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpdateSourceError(f"{label} cannot be opened safely: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UpdateSourceError(f"{label} must be a regular file: {path}")
        if metadata.st_size > limit:
            raise UpdateSourceError(
                f"{label} exceeds the {limit}-byte limit: {path}"
            )
        chunks: list[bytes] = []
        seen = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - seen))
            if not chunk:
                break
            chunks.append(chunk)
            seen += len(chunk)
            if seen > limit:
                raise UpdateSourceError(
                    f"{label} exceeds the {limit}-byte limit: {path}"
                )
        return b"".join(chunks)
    except OSError as exc:
        raise UpdateSourceError(f"{label} cannot be read: {path}") from exc
    finally:
        os.close(descriptor)


def _archive_identity(filename: str) -> tuple[str, str, str]:
    if filename.endswith(".tar.gz"):
        archive_format = "tar.gz"
        suffix = ".tar.gz"
    elif filename.endswith(".zip"):
        archive_format = "zip"
        suffix = ".zip"
    else:
        raise ArchiveSafetyError(
            f"archive filename must end in .tar.gz or .zip: {filename!r}"
        )
    if not filename.startswith("bugate-"):
        raise ArchiveSafetyError(f"archive filename lacks bugate- prefix: {filename!r}")
    version = filename[len("bugate-") : -len(suffix)]
    try:
        contract.validate_semver(version)
    except contract.ContractError as exc:
        raise ArchiveSafetyError(f"archive filename has invalid version: {filename!r}") from exc
    return archive_format, version, f"bugate-{version}"


def _checksum_version(filename: str) -> str:
    suffix = ".SHA256SUMS"
    if not filename.startswith("bugate-") or not filename.endswith(suffix):
        raise ChecksumError(
            f"checksum filename must be bugate-<version>.SHA256SUMS: {filename!r}"
        )
    version = filename[len("bugate-") : -len(suffix)]
    try:
        contract.validate_semver(version)
    except contract.ContractError as exc:
        raise ChecksumError(f"checksum filename has invalid version: {filename!r}") from exc
    return version


def parse_checksum_bytes(data: bytes) -> dict[str, str]:
    """Parse strict GNU-style two-space SHA-256 records."""

    if not isinstance(data, bytes) or not data or len(data) > MAX_CHECKSUM_BYTES:
        raise ChecksumError("checksum asset is empty or exceeds its size limit")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ChecksumError("checksum asset must be ASCII") from exc
    lines = text.splitlines()
    if not lines or any(not line for line in lines):
        raise ChecksumError("checksum asset contains an empty record")
    records: dict[str, str] = {}
    folded: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        match = _CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ChecksumError(f"malformed checksum record at line {line_number}")
        digest, filename = match.groups()
        try:
            contract.validate_relative_path(filename, field="checksum filename")
        except contract.ContractError as exc:
            raise ChecksumError(f"unsafe checksum filename at line {line_number}") from exc
        if len(PurePosixPath(filename).parts) != 1:
            raise ChecksumError(f"checksum filename must be a basename: {filename!r}")
        folded_name = filename.casefold()
        if filename in records or folded_name in folded:
            raise ChecksumError(f"duplicate or ambiguous checksum record: {filename}")
        records[filename] = digest
        folded.add(folded_name)
    return records


def parse_checksum_asset(path: Path | str) -> dict[str, str]:
    checksum_path = Path(path)
    try:
        data = _read_small_regular(
            checksum_path, label="checksum asset", limit=MAX_CHECKSUM_BYTES
        )
    except UpdateSourceError as exc:
        if isinstance(exc, ChecksumError):
            raise
        raise ChecksumError(str(exc)) from exc
    return parse_checksum_bytes(data)


def _sha256_regular(path: Path, *, label: str) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpdateSourceError(f"{label} cannot be opened safely: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UpdateSourceError(f"{label} must be a regular file: {path}")
        if metadata.st_size > MAX_ARCHIVE_BYTES:
            raise UpdateSourceError(
                f"{label} exceeds the compressed-size limit: {path}"
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    except OSError as exc:
        raise UpdateSourceError(f"{label} cannot be read: {path}") from exc
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _checksum_expectation(
    archive: Path, checksums: Path
) -> tuple[str, str, str]:
    archive_format, archive_version, _prefix = _archive_identity(archive.name)
    checksum_version = _checksum_version(checksums.name)
    if checksum_version != archive_version:
        raise ChecksumError(
            "checksum/archive version mismatch: "
            f"checksums={checksum_version}, archive={archive_version}"
        )
    records = parse_checksum_asset(checksums)
    for filename in records:
        try:
            _format, version, _archive_prefix = _archive_identity(filename)
        except ArchiveSafetyError as exc:
            raise ChecksumError(f"checksum asset names a non-release archive: {filename}") from exc
        if version != archive_version:
            raise ChecksumError(
                f"checksum asset mixes release versions: {archive_version} and {version}"
            )
    expected = records.get(archive.name)
    if expected is None:
        raise ChecksumError(f"checksum record is missing for {archive.name}")
    return expected, archive_format, archive_version


def verify_archive_checksum(archive: Path | str, checksums: Path | str) -> str:
    """Verify one regular archive against its unambiguous checksum record."""

    archive_path = Path(archive)
    checksum_path = Path(checksums)
    expected, _format, _version = _checksum_expectation(archive_path, checksum_path)
    try:
        actual = _sha256_regular(archive_path, label="release archive")
    except UpdateSourceError as exc:
        raise ChecksumError(str(exc)) from exc
    if actual != expected:
        raise ChecksumError(
            f"archive SHA-256 mismatch for {archive_path.name}: expected {expected}, actual {actual}"
        )
    return actual


def _relative_member(raw_name: str, prefix: str, *, label: str) -> str:
    if not isinstance(raw_name, str) or not raw_name or "\x00" in raw_name:
        raise ArchiveSafetyError(f"{label} has an empty or NUL-containing name")
    name = raw_name[:-1] if raw_name.endswith("/") else raw_name
    if not name or name.endswith("/"):
        raise ArchiveSafetyError(f"{label} has an empty path component: {raw_name!r}")
    try:
        contract.validate_relative_path(name, field=f"{label} name")
    except contract.ContractError as exc:
        raise ArchiveSafetyError(f"unsafe {label} name: {raw_name!r}") from exc
    expected = prefix + "/"
    if not name.startswith(expected):
        raise ArchiveSafetyError(f"{label} is outside archive prefix: {raw_name!r}")
    relative = name[len(expected) :]
    try:
        return contract.validate_relative_path(relative, field=f"{label} relative path")
    except contract.ContractError as exc:
        raise ArchiveSafetyError(f"unsafe {label} relative path: {raw_name!r}") from exc


def _consume(
    stream: BinaryIO,
    *,
    expected_size: int,
    retain: bool,
    label: str,
) -> tuple[str, bytes | None]:
    if expected_size < 0 or expected_size > MAX_ENTRY_BYTES:
        raise ArchiveSafetyError(f"{label} exceeds the per-entry size limit")
    digest = hashlib.sha256()
    kept = bytearray() if retain else None
    seen = 0
    while True:
        chunk = stream.read(min(1024 * 1024, expected_size - seen + 1))
        if not chunk:
            break
        seen += len(chunk)
        if seen > expected_size or seen > MAX_ENTRY_BYTES:
            raise ArchiveSafetyError(f"{label} expands beyond its declared size")
        digest.update(chunk)
        if kept is not None:
            if seen > MAX_METADATA_BYTES:
                raise ArchiveSafetyError(f"{label} metadata exceeds its size limit")
            kept.extend(chunk)
    if seen != expected_size:
        raise ArchiveSafetyError(
            f"{label} size mismatch: declared {expected_size}, read {seen}"
        )
    return digest.hexdigest(), bytes(kept) if kept is not None else None


def _validate_source_entries(entries: Iterable[SourceEntry]) -> tuple[SourceEntry, ...]:
    ordered = tuple(sorted(entries, key=lambda entry: entry.path))
    if not ordered or len(ordered) > MAX_ARCHIVE_ENTRIES:
        raise ArchiveSafetyError("archive has no entries or exceeds the entry limit")
    seen: set[str] = set()
    folded: set[str] = set()
    by_path: dict[str, SourceEntry] = {}
    total = 0
    for entry in ordered:
        try:
            contract.validate_relative_path(entry.path, field="archive entry path")
        except contract.ContractError as exc:
            raise ArchiveSafetyError(f"unsafe archive entry path: {entry.path!r}") from exc
        if entry.path in seen or entry.path.casefold() in folded:
            raise ArchiveSafetyError(
                f"duplicate or case-conflicting archive entry: {entry.path}"
            )
        seen.add(entry.path)
        folded.add(entry.path.casefold())
        by_path[entry.path.casefold()] = entry
        if entry.type == "file":
            if entry.mode not in {"0644", "0755"} or entry.sha256 is None:
                raise ArchiveSafetyError(f"invalid file metadata: {entry.path}")
            total += entry.size
        elif entry.type == "directory":
            if entry.mode != "0755" or entry.size != 0:
                raise ArchiveSafetyError(f"invalid directory metadata: {entry.path}")
        elif entry.type == "symlink":
            if entry.mode != "0777" or entry.target is None:
                raise ArchiveSafetyError(f"invalid symlink metadata: {entry.path}")
            try:
                contract.validate_symlink_target(entry.path, entry.target)
            except contract.ContractError as exc:
                raise ArchiveSafetyError(f"unsafe symlink target: {entry.path}") from exc
        else:
            raise ArchiveSafetyError(f"unsupported archive entry type: {entry.path}")
        parent = PurePosixPath(entry.path).parent
        while parent != PurePosixPath("."):
            ancestor = by_path.get(parent.as_posix().casefold())
            if ancestor is not None and ancestor.type != "directory":
                raise ArchiveSafetyError(
                    f"non-directory archive ancestor: {ancestor.path} -> {entry.path}"
                )
            parent = parent.parent
    # The first loop only sees earlier lexical entries; this second pass is
    # independent of archive ordering.
    for entry in ordered:
        parent = PurePosixPath(entry.path).parent
        while parent != PurePosixPath("."):
            ancestor = by_path.get(parent.as_posix().casefold())
            if ancestor is not None and ancestor.type != "directory":
                raise ArchiveSafetyError(
                    f"non-directory archive ancestor: {ancestor.path} -> {entry.path}"
                )
            parent = parent.parent
    if total > MAX_TOTAL_BYTES:
        raise ArchiveSafetyError("archive exceeds the total expanded-size limit")
    return ordered


def _metadata_tuple(
    metadata: Mapping[str, bytes],
) -> tuple[bytes, bytes, bytes, bytes]:
    try:
        return (
            metadata[contract.RELEASE_MANIFEST_PATH],
            metadata[".codex-plugin/plugin.json"],
            metadata[".claude-plugin/plugin.json"],
            metadata["scripts/bugate_update.py"],
        )
    except KeyError as exc:
        raise ManifestError(f"release metadata is missing: {exc.args[0]}") from exc


def _inspect_tar(
    source: Path | BinaryIO,
    prefix: str,
    version: str,
    *,
    label: str,
) -> ArchiveInspection:
    entries: list[SourceEntry] = []
    metadata: dict[str, bytes] = {}
    try:
        with tarfile.open(
            source if isinstance(source, Path) else None,
            mode="r:gz",
            fileobj=None if isinstance(source, Path) else source,
        ) as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise ArchiveSafetyError("tar archive exceeds the entry limit")
            for member in members:
                if member.name.endswith("/") and not member.isdir():
                    raise ArchiveSafetyError(
                        f"non-directory tar entry has a trailing slash: {member.name}"
                    )
                relative = _relative_member(member.name, prefix, label="tar entry")
                mode = f"{stat.S_IMODE(member.mode):04o}"
                if member.islnk():
                    linkname = member.linkname
                    try:
                        contract.validate_relative_path(linkname, field="tar hardlink target")
                    except contract.ContractError as exc:
                        raise ArchiveSafetyError(
                            f"unsafe tar hardlink target: {member.name} -> {linkname}"
                        ) from exc
                    if not linkname.startswith(prefix + "/"):
                        raise ArchiveSafetyError(
                            f"tar hardlink target escapes archive prefix: {member.name} -> {linkname}"
                        )
                    raise ArchiveSafetyError(
                        f"tar hardlinks are not permitted by the release manifest: {member.name}"
                    )
                if member.isdir():
                    entries.append(SourceEntry(relative, "directory", mode))
                elif member.issym():
                    target = member.linkname
                    if len(target.encode("utf-8", errors="surrogatepass")) > MAX_SYMLINK_BYTES:
                        raise ArchiveSafetyError(f"tar symlink target is too large: {relative}")
                    entries.append(
                        SourceEntry(relative, "symlink", mode, target=target)
                    )
                elif member.isfile():
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ArchiveSafetyError(f"tar file cannot be read: {relative}")
                    with stream:
                        digest, retained = _consume(
                            stream,
                            expected_size=member.size,
                            retain=relative in _METADATA_PATHS,
                            label=f"tar entry {relative}",
                        )
                    entries.append(
                        SourceEntry(relative, "file", mode, digest, size=member.size)
                    )
                    if retained is not None:
                        metadata[relative] = retained
                else:
                    raise ArchiveSafetyError(f"unsupported tar entry type: {relative}")
    except ArchiveSafetyError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveSafetyError(f"invalid tar.gz archive: {label}") from exc
    ordered = _validate_source_entries(entries)
    manifest_bytes, codex_bytes, claude_bytes, updater_bytes = _metadata_tuple(metadata)
    return ArchiveInspection(
        "tar.gz",
        prefix,
        version,
        ordered,
        manifest_bytes,
        codex_bytes,
        claude_bytes,
        updater_bytes,
    )


def _zip_kind(info: zipfile.ZipInfo) -> tuple[str, str]:
    raw_mode = info.external_attr >> 16
    kind_bits = stat.S_IFMT(raw_mode)
    mode = f"{stat.S_IMODE(raw_mode):04o}"
    if kind_bits == stat.S_IFDIR and info.is_dir():
        return "directory", mode
    if kind_bits == stat.S_IFREG and not info.is_dir():
        return "file", mode
    if kind_bits == stat.S_IFLNK and not info.is_dir():
        return "symlink", mode
    raise ArchiveSafetyError(f"unsupported or conflicting zip entry type: {info.filename}")


def _inspect_zip(
    source: Path | BinaryIO,
    prefix: str,
    version: str,
    *,
    label: str,
) -> ArchiveInspection:
    entries: list[SourceEntry] = []
    metadata: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(source, mode="r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ArchiveSafetyError("zip archive exceeds the entry limit")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise ArchiveSafetyError(f"encrypted zip entry is not supported: {info.filename}")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ArchiveSafetyError(
                        f"unsupported zip compression method: {info.filename}"
                    )
                relative = _relative_member(info.filename, prefix, label="zip entry")
                kind, mode = _zip_kind(info)
                if info.file_size < 0 or info.file_size > MAX_ENTRY_BYTES:
                    raise ArchiveSafetyError(f"zip entry exceeds size limit: {relative}")
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size > info.compress_size * 500
                ):
                    raise ArchiveSafetyError(
                        f"zip entry has an excessive compression ratio: {relative}"
                    )
                with archive.open(info, mode="r") as stream:
                    digest, retained = _consume(
                        stream,
                        expected_size=info.file_size,
                        retain=relative in _METADATA_PATHS or kind == "symlink",
                        label=f"zip entry {relative}",
                    )
                if kind == "directory":
                    if info.file_size != 0:
                        raise ArchiveSafetyError(f"zip directory has payload: {relative}")
                    entries.append(SourceEntry(relative, kind, mode))
                elif kind == "symlink":
                    if retained is None or len(retained) > MAX_SYMLINK_BYTES:
                        raise ArchiveSafetyError(f"invalid zip symlink payload: {relative}")
                    try:
                        target = retained.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ArchiveSafetyError(
                            f"zip symlink target is not UTF-8: {relative}"
                        ) from exc
                    entries.append(
                        SourceEntry(relative, kind, mode, target=target)
                    )
                else:
                    entries.append(
                        SourceEntry(relative, kind, mode, digest, size=info.file_size)
                    )
                    if retained is not None:
                        metadata[relative] = retained
    except ArchiveSafetyError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ArchiveSafetyError(f"invalid zip archive: {label}") from exc
    ordered = _validate_source_entries(entries)
    manifest_bytes, codex_bytes, claude_bytes, updater_bytes = _metadata_tuple(metadata)
    return ArchiveInspection(
        "zip",
        prefix,
        version,
        ordered,
        manifest_bytes,
        codex_bytes,
        claude_bytes,
        updater_bytes,
    )


def _inspect_archive(
    archive: Path | str,
    *,
    expected_version: str | None = None,
    logical_name: str | None = None,
) -> ArchiveInspection:
    archive_path = Path(archive)
    _regular_file(archive_path, label="release archive")
    filename = logical_name or archive_path.name
    archive_format, version, prefix = _archive_identity(filename)
    if expected_version is not None:
        try:
            expected = contract.validate_semver(expected_version)
        except contract.ContractError as exc:
            raise ManifestError(f"invalid requested release version: {expected_version!r}") from exc
        if version != expected:
            raise ManifestError(
                f"requested/archive version mismatch: requested={expected}, archive={version}"
            )
    if archive_format == "tar.gz":
        return _inspect_tar(
            archive_path, prefix, version, label=archive_path.name
        )
    return _inspect_zip(
        archive_path, prefix, version, label=archive_path.name
    )


def inspect_archive(
    archive: Path | str, *, expected_version: str | None = None
) -> ArchiveInspection:
    """Read and safety-check a canonically named archive without extracting it."""

    return _inspect_archive(archive, expected_version=expected_version)


def _strict_json(data: bytes, *, label: str) -> dict[str, Any]:
    if not data or len(data) > MAX_METADATA_BYTES:
        raise ManifestError(f"{label} is empty or exceeds its size limit")

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ManifestError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ManifestError(f"{label} contains non-finite JSON value: {value}")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except ManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must contain a JSON object")
    return value


def _validated_manifest(data: bytes, *, expected_version: str | None) -> dict[str, Any]:
    manifest = _strict_json(data, label="release manifest")
    try:
        canonical = contract.canonical_json_bytes(manifest)
        if canonical != data:
            raise ManifestError("release manifest is not canonical JSON")
        return contract.validate_current_release_manifest(
            manifest, expected_version=expected_version
        )
    except contract.ContractError as exc:
        raise ManifestError(f"release manifest validation failed: {exc}") from exc


def _plugin_version(data: bytes, *, label: str) -> str:
    document = _strict_json(data, label=label)
    version = document.get("version")
    try:
        return contract.validate_semver(version)
    except contract.ContractError as exc:
        raise ManifestError(f"{label} has invalid version") from exc


def _updater_version(data: bytes) -> str:
    """Extract the one direct module-level literal updater version without exec."""

    if not data or len(data) > MAX_METADATA_BYTES:
        raise ManifestError("bootstrap updater is empty or exceeds its size limit")
    try:
        text = data.decode("utf-8")
        tree = ast.parse(text, filename="scripts/bugate_update.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ManifestError("bootstrap updater is not valid UTF-8 Python") from exc

    assignments: list[ast.expr | None] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "UPDATER_VERSION"
                for target in node.targets
            ):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    raise ManifestError(
                        "bootstrap updater UPDATER_VERSION must be one direct module-level literal"
                    )
                assignments.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "UPDATER_VERSION"
        ):
            assignments.append(node.value)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "UPDATER_VERSION"
        ):
            raise ManifestError(
                "bootstrap updater UPDATER_VERSION must be one direct module-level literal"
            )

    if len(assignments) != 1:
        raise ManifestError(
            "bootstrap updater must define exactly one module-level UPDATER_VERSION literal"
        )
    try:
        value = ast.literal_eval(assignments[0])
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            "bootstrap updater UPDATER_VERSION must be a string literal"
        ) from exc
    if not isinstance(value, str):
        raise ManifestError(
            "bootstrap updater UPDATER_VERSION must be a string literal"
        )
    try:
        return contract.validate_semver(value)
    except contract.ContractError as exc:
        raise ManifestError("bootstrap updater has invalid UPDATER_VERSION") from exc


def _expected_inventory(
    manifest: Mapping[str, Any], manifest_bytes: bytes
) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for raw in manifest["archive_inventory"]:
        item = dict(raw)
        path = item.pop("path")
        item.pop("roles", None)
        digest_ref = item.pop("digest_ref", None)
        if digest_ref is not None:
            if digest_ref != "self_digest" or path != contract.RELEASE_MANIFEST_PATH:
                raise ManifestError(f"invalid manifest digest reference: {path}")
            item["sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        expected[path] = item
    return expected


def _verify_release_metadata(
    inspection: ArchiveInspection,
    *,
    expected_version: str | None,
) -> dict[str, Any]:
    requested = expected_version or inspection.version
    manifest = _validated_manifest(
        inspection.manifest_bytes, expected_version=requested
    )
    version = manifest["bugate_version"]
    if version != inspection.version or manifest.get("archive_prefix") != inspection.prefix:
        raise ManifestError(
            "archive prefix/version differs from the release manifest"
        )
    codex = _plugin_version(
        inspection.codex_plugin_bytes, label="Codex plugin manifest"
    )
    claude = _plugin_version(
        inspection.claude_plugin_bytes, label="Claude plugin manifest"
    )
    if codex != version or claude != version:
        raise ManifestError(
            f"plugin/release version mismatch: release={version}, codex={codex}, claude={claude}"
        )
    actual = {entry.path: entry.manifest_record() for entry in inspection.entries}
    expected = _expected_inventory(manifest, inspection.manifest_bytes)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            path for path in set(actual) & set(expected) if actual[path] != expected[path]
        )
        raise ManifestError(
            "archive inventory differs from release manifest: "
            f"missing={missing}, extra={extra}, changed={changed}"
        )
    updater = _updater_version(inspection.updater_bytes)
    if updater != version:
        raise ManifestError(
            f"updater/release version mismatch: release={version}, updater={updater}"
        )
    try:
        contract.require_updater_compatible(
            updater, manifest["updater_minimum_version"]
        )
    except contract.ContractError as exc:
        raise ManifestError(f"bootstrap updater is incompatible: {exc}") from exc
    return manifest


def _safe_root(root: Path | str, *, require_empty: bool) -> Path:
    path = Path(root)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StagingError(f"directory is not accessible: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise StagingError(f"path must be a real directory, not a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
        if require_empty and any(path.iterdir()):
            raise StagingError(f"staging directory must be empty: {path}")
    except StagingError:
        raise
    except OSError as exc:
        raise StagingError(f"directory cannot be inspected: {path}") from exc
    return resolved


def _open_pinned_directory(
    root: Path | str, *, require_empty: bool, label: str
) -> tuple[Path, int, tuple[int, int]]:
    path = Path(root)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise StagingError(f"{label} must be a physical directory: {path}")
        if require_empty and os.listdir(descriptor):
            raise StagingError(f"{label} must be empty: {path}")
        resolved = path.resolve(strict=True)
        current = os.lstat(resolved)
        identity = (metadata.st_dev, metadata.st_ino)
        if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            raise StagingError(f"{label} path changed while it was opened: {path}")
        return resolved, descriptor, identity
    except StagingError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise StagingError(f"{label} cannot be opened safely: {path}") from exc


def _assert_pinned_path(path: Path, descriptor: int, *, label: str) -> None:
    try:
        pinned = os.fstat(descriptor)
        current = os.lstat(path)
    except OSError as exc:
        raise StagingError(f"{label} path is unavailable") from exc
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise StagingError(f"{label} path changed during validation")


def _directory_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise StagingError("staging descriptor is not a directory")
    return metadata.st_dev, metadata.st_ino


@contextlib.contextmanager
def _pinned_directory_cwd(descriptor: int):
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


def _open_directory_beneath(
    root_fd: int,
    relative: str,
    *,
    directory_identities: Mapping[str, tuple[int, int]] | None = None,
) -> int:
    if relative in {"", "."}:
        descriptor = os.dup(root_fd)
        if (
            directory_identities is not None
            and directory_identities.get(".") != _directory_identity(descriptor)
        ):
            os.close(descriptor)
            raise StagingError("staged release root identity changed")
        return descriptor
    try:
        normalized = contract.validate_relative_path(
            relative, field="staging directory path"
        )
    except contract.ContractError as exc:
        raise StagingError(str(exc)) from exc
    descriptor = os.dup(root_fd)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        traversed: list[str] = []
        for part in PurePosixPath(normalized).parts:
            child = os.open(part, flags, dir_fd=descriptor)
            traversed.append(part)
            current_relative = PurePosixPath(*traversed).as_posix()
            if (
                directory_identities is not None
                and directory_identities.get(current_relative)
                != _directory_identity(child)
            ):
                os.close(child)
                raise StagingError(
                    f"staged directory identity changed: {current_relative}"
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except StagingError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise StagingError(
            f"staging parent is missing, replaced, or unsafe: {relative}"
        ) from exc


def _parent_directory_fd(
    root_fd: int,
    relative: str,
    *,
    directory_identities: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[int, str]:
    normalized = contract.validate_relative_path(
        relative, field="staging entry path"
    )
    parsed = PurePosixPath(normalized)
    parent = parsed.parent.as_posix()
    return (
        _open_directory_beneath(
            root_fd,
            parent,
            directory_identities=directory_identities,
        ),
        parsed.name,
    )


def _copy_snapshot(
    source: Path,
    destination: Path,
    *,
    destination_dir_fd: int | None = None,
    created_identities: dict[str, tuple[int, int]] | None = None,
) -> tuple[str, int]:
    """Copy and hash an archive, returning a pinned read descriptor.

    The descriptor remains bound to the exact inode that was hashed. Callers
    must inspect and extract through duplicates of it rather than reopening the
    staging pathname, which could be replaced between verification phases.
    """

    try:
        source_metadata = os.lstat(source)
    except OSError as exc:
        raise StagingError(f"release archive cannot be inspected: {source}") from exc
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(source_metadata.st_mode):
        raise StagingError(f"release archive must be a regular non-symlink file: {source}")
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    digest = hashlib.sha256()
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise StagingError(f"release archive cannot be opened safely: {source}") from exc
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise StagingError(f"release archive is not a regular file: {source}")
        if source_stat.st_size > MAX_ARCHIVE_BYTES:
            raise StagingError(
                f"release archive exceeds the compressed-size limit: {source}"
            )
        destination_fd = os.open(
            destination.name if destination_dir_fd is not None else destination,
            destination_flags,
            0o600,
            dir_fd=destination_dir_fd,
        )
        destination_stat = os.fstat(destination_fd)
        if created_identities is not None:
            created_identities[destination.name] = _entry_identity(destination_stat)
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fsync(destination_fd)
            os.lseek(destination_fd, 0, os.SEEK_SET)
        except BaseException:
            os.close(destination_fd)
            raise
    except OSError as exc:
        raise StagingError(f"release archive snapshot failed: {source}") from exc
    finally:
        os.close(source_fd)
    return digest.hexdigest(), destination_fd


def _pinned_stream(descriptor: int) -> BinaryIO:
    """Return an independent seekable stream for a pinned archive inode."""

    duplicate = os.dup(descriptor)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        return os.fdopen(duplicate, "rb")
    except BaseException:
        os.close(duplicate)
        raise


def _destination(root: Path, relative: str) -> Path:
    return root.joinpath(*PurePosixPath(relative).parts)


def _assert_directory(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StagingError(f"{label} directory is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise StagingError(f"{label} parent is not a real directory: {path}")


def _write_stream(
    destination: Path,
    stream: BinaryIO,
    entry: SourceEntry,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, int(entry.mode, 8))
    except OSError as exc:
        raise StagingError(f"cannot create staged file: {entry.path}") from exc
    digest = hashlib.sha256()
    seen = 0
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            seen += len(chunk)
            if seen > entry.size:
                raise StagingError(f"staged file exceeds verified size: {entry.path}")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
        os.fchmod(descriptor, int(entry.mode, 8))
    finally:
        os.close(descriptor)
    if seen != entry.size or digest.hexdigest() != entry.sha256:
        raise StagingError(f"staged file differs from verified archive: {entry.path}")


def _write_stream_at(
    root_fd: int,
    stream: BinaryIO,
    entry: SourceEntry,
    *,
    directory_identities: Mapping[str, tuple[int, int]],
    leaf_identities: dict[str, tuple[int, int]],
) -> None:
    parent_fd, name = _parent_directory_fd(
        root_fd,
        entry.path,
        directory_identities=directory_identities,
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            descriptor = os.open(name, flags, int(entry.mode, 8), dir_fd=parent_fd)
        except OSError as exc:
            raise StagingError(f"cannot create staged file: {entry.path}") from exc
        digest = hashlib.sha256()
        seen = 0
        try:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                seen += len(chunk)
                if seen > entry.size:
                    raise StagingError(
                        f"staged file exceeds verified size: {entry.path}"
                    )
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            os.fchmod(descriptor, int(entry.mode, 8))
            os.fsync(descriptor)
            written_metadata = os.fstat(descriptor)
            leaf_identities[entry.path] = (
                written_metadata.st_dev,
                written_metadata.st_ino,
            )
        finally:
            os.close(descriptor)
        if seen != entry.size or digest.hexdigest() != entry.sha256:
            raise StagingError(
                f"staged file differs from verified archive: {entry.path}"
            )
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _create_stage_directories(root: Path, entries: Iterable[SourceEntry]) -> None:
    root.mkdir(mode=0o755)
    directories = sorted(
        (entry for entry in entries if entry.type == "directory"),
        key=lambda entry: (len(PurePosixPath(entry.path).parts), entry.path),
    )
    declared = {entry.path for entry in directories}
    for entry in directories:
        parent = PurePosixPath(entry.path).parent
        if parent != PurePosixPath(".") and parent.as_posix() not in declared:
            raise StagingError(f"archive omits declared parent directory: {entry.path}")
        destination = _destination(root, entry.path)
        _assert_directory(destination.parent, label="staging")
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
    for entry in entries:
        parent = PurePosixPath(entry.path).parent
        if parent != PurePosixPath(".") and parent.as_posix() not in declared:
            raise StagingError(f"archive omits parent directory: {entry.path}")


def _create_stage_directories_at(
    root_fd: int,
    entries: Iterable[SourceEntry],
    directory_identities: dict[str, tuple[int, int]],
) -> None:
    entries = tuple(entries)
    root_identity = _directory_identity(root_fd)
    if directory_identities.get(".", root_identity) != root_identity:
        raise StagingError("staged release root identity changed")
    directory_identities["."] = root_identity
    directories = sorted(
        (entry for entry in entries if entry.type == "directory"),
        key=lambda entry: (len(PurePosixPath(entry.path).parts), entry.path),
    )
    declared = {entry.path for entry in directories}
    for entry in directories:
        parent = PurePosixPath(entry.path).parent
        if parent != PurePosixPath(".") and parent.as_posix() not in declared:
            raise StagingError(
                f"archive omits declared parent directory: {entry.path}"
            )
        parent_fd, name = _parent_directory_fd(
            root_fd,
            entry.path,
            directory_identities=directory_identities,
        )
        try:
            try:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
                child_fd = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                try:
                    os.fchmod(child_fd, 0o755)
                    os.fsync(child_fd)
                    directory_identities[entry.path] = _directory_identity(
                        child_fd
                    )
                finally:
                    os.close(child_fd)
                os.fsync(parent_fd)
            except OSError as exc:
                raise StagingError(
                    f"cannot create staged directory: {entry.path}"
                ) from exc
        finally:
            os.close(parent_fd)
    for entry in entries:
        parent = PurePosixPath(entry.path).parent
        if parent != PurePosixPath(".") and parent.as_posix() not in declared:
            raise StagingError(f"archive omits parent directory: {entry.path}")


def _extract_tar(
    snapshot: Path | BinaryIO,
    root: Path,
    entries: tuple[SourceEntry, ...],
    prefix: str,
    *,
    root_fd: int | None = None,
    directory_identities: dict[str, tuple[int, int]] | None = None,
    leaf_identities: dict[str, tuple[int, int]] | None = None,
) -> None:
    by_path = {entry.path: entry for entry in entries}
    if root_fd is None:
        _create_stage_directories(root, entries)
    else:
        if directory_identities is None:
            directory_identities = {".": _directory_identity(root_fd)}
        if leaf_identities is None:
            leaf_identities = {}
        _create_stage_directories_at(
            root_fd, entries, directory_identities
        )
    try:
        with tarfile.open(
            snapshot if isinstance(snapshot, Path) else None,
            mode="r:gz",
            fileobj=None if isinstance(snapshot, Path) else snapshot,
        ) as archive:
            members: dict[str, tarfile.TarInfo] = {}
            for member in archive.getmembers():
                relative = _relative_member(member.name, prefix, label="tar entry")
                members[relative] = member
            for entry in entries:
                if entry.type == "directory":
                    continue
                if entry.type == "symlink":
                    if root_fd is None:
                        destination = _destination(root, entry.path)
                        _assert_directory(destination.parent, label="staging")
                        os.symlink(entry.target, destination)
                    else:
                        parent_fd, name = _parent_directory_fd(
                            root_fd,
                            entry.path,
                            directory_identities=directory_identities,
                        )
                        try:
                            os.symlink(entry.target, name, dir_fd=parent_fd)
                            symlink_metadata = os.stat(
                                name,
                                dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                            leaf_identities[entry.path] = (
                                symlink_metadata.st_dev,
                                symlink_metadata.st_ino,
                            )
                            os.fsync(parent_fd)
                        finally:
                            os.close(parent_fd)
                else:
                    member = members[entry.path]
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise StagingError(f"cannot reopen tar entry: {entry.path}")
                    with stream:
                        if root_fd is None:
                            destination = _destination(root, entry.path)
                            _assert_directory(destination.parent, label="staging")
                            _write_stream(destination, stream, entry)
                        else:
                            _write_stream_at(
                                root_fd,
                                stream,
                                entry,
                                directory_identities=directory_identities,
                                leaf_identities=leaf_identities,
                            )
    except StagingError:
        raise
    except (KeyError, OSError, tarfile.TarError) as exc:
        raise StagingError("controlled tar extraction failed") from exc


def _extract_zip(
    snapshot: Path | BinaryIO,
    root: Path,
    entries: tuple[SourceEntry, ...],
    prefix: str,
    *,
    root_fd: int | None = None,
    directory_identities: dict[str, tuple[int, int]] | None = None,
    leaf_identities: dict[str, tuple[int, int]] | None = None,
) -> None:
    if root_fd is None:
        _create_stage_directories(root, entries)
    else:
        if directory_identities is None:
            directory_identities = {".": _directory_identity(root_fd)}
        if leaf_identities is None:
            leaf_identities = {}
        _create_stage_directories_at(
            root_fd, entries, directory_identities
        )
    try:
        with zipfile.ZipFile(snapshot, mode="r") as archive:
            infos: dict[str, zipfile.ZipInfo] = {}
            for info in archive.infolist():
                relative = _relative_member(info.filename, prefix, label="zip entry")
                infos[relative] = info
            for entry in entries:
                if entry.type == "directory":
                    continue
                if entry.type == "symlink":
                    if root_fd is None:
                        destination = _destination(root, entry.path)
                        _assert_directory(destination.parent, label="staging")
                        os.symlink(entry.target, destination)
                    else:
                        parent_fd, name = _parent_directory_fd(
                            root_fd,
                            entry.path,
                            directory_identities=directory_identities,
                        )
                        try:
                            os.symlink(entry.target, name, dir_fd=parent_fd)
                            symlink_metadata = os.stat(
                                name,
                                dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                            leaf_identities[entry.path] = (
                                symlink_metadata.st_dev,
                                symlink_metadata.st_ino,
                            )
                            os.fsync(parent_fd)
                        finally:
                            os.close(parent_fd)
                else:
                    with archive.open(infos[entry.path], mode="r") as stream:
                        if root_fd is None:
                            destination = _destination(root, entry.path)
                            _assert_directory(destination.parent, label="staging")
                            _write_stream(destination, stream, entry)
                        else:
                            _write_stream_at(
                                root_fd,
                                stream,
                                entry,
                                directory_identities=directory_identities,
                                leaf_identities=leaf_identities,
                            )
    except StagingError:
        raise
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise StagingError("controlled zip extraction failed") from exc


def _physical_scan(root: Path) -> tuple[tuple[SourceEntry, ...], dict[str, bytes]]:
    entries: list[SourceEntry] = []
    metadata: dict[str, bytes] = {}
    total = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()
        for name in list(dirnames):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            details = os.lstat(path)
            if stat.S_ISLNK(details.st_mode):
                target = os.readlink(path)
                entries.append(SourceEntry(relative, "symlink", "0777", target=target))
                dirnames.remove(name)
            elif stat.S_ISDIR(details.st_mode):
                entries.append(
                    SourceEntry(
                        relative,
                        "directory",
                        f"{stat.S_IMODE(details.st_mode):04o}",
                    )
                )
            else:
                raise ArchiveSafetyError(f"unsupported unpacked entry type: {relative}")
        for name in filenames:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            details = os.lstat(path)
            if stat.S_ISLNK(details.st_mode):
                target = os.readlink(path)
                entries.append(SourceEntry(relative, "symlink", "0777", target=target))
                continue
            if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                raise ArchiveSafetyError(f"unpacked file is not independent regular data: {relative}")
            if details.st_size > MAX_ENTRY_BYTES:
                raise ArchiveSafetyError(f"unpacked file exceeds size limit: {relative}")
            digest = hashlib.sha256()
            retained = bytearray() if relative in _METADATA_PATHS else None
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    if retained is not None:
                        if len(retained) + len(chunk) > MAX_METADATA_BYTES:
                            raise ArchiveSafetyError(f"unpacked metadata is too large: {relative}")
                        retained.extend(chunk)
            total += details.st_size
            entries.append(
                SourceEntry(
                    relative,
                    "file",
                    f"{stat.S_IMODE(details.st_mode):04o}",
                    digest.hexdigest(),
                    size=details.st_size,
                )
            )
            if retained is not None:
                metadata[relative] = bytes(retained)
    if total > MAX_TOTAL_BYTES:
        raise ArchiveSafetyError("unpacked release exceeds total size limit")
    return _validate_source_entries(entries), metadata


def _verify_unpacked(
    root: Path,
    *,
    expected_version: str | None,
) -> dict[str, Any]:
    try:
        entries, metadata = _physical_scan(root)
    except UpdateSourceError:
        raise
    except OSError as exc:
        raise ArchiveSafetyError("unpacked release cannot be read safely") from exc
    manifest_bytes, codex_bytes, claude_bytes, updater_bytes = _metadata_tuple(metadata)
    manifest = _validated_manifest(manifest_bytes, expected_version=expected_version)
    inspection = ArchiveInspection(
        "unpacked",
        manifest["archive_prefix"],
        manifest["bugate_version"],
        entries,
        manifest_bytes,
        codex_bytes,
        claude_bytes,
        updater_bytes,
    )
    return _verify_release_metadata(
        inspection, expected_version=expected_version
    )


def prepare_unpacked(
    root: Path | str, expected_version: str | None = None
) -> PreparedRelease:
    """Read-only validation of an already-unpacked full release tree."""

    release_root, release_fd, identity = _open_pinned_directory(
        root, require_empty=False, label="unpacked release root"
    )
    try:
        with _pinned_directory_cwd(release_fd) as anchored:
            manifest = _verify_unpacked(
                anchored, expected_version=expected_version
            )
        _assert_pinned_path(
            release_root, release_fd, label="unpacked release root"
        )
    finally:
        os.close(release_fd)
    return PreparedRelease(
        release_root, manifest, None, "unpacked", identity
    )


def _clean_staging(stage: Path) -> None:
    for child in list(stage.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def _exclusive_rename_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    """Move one entry without replacing a concurrently-created destination."""

    for name in (source_name, destination_name):
        if not name or "/" in name or name in {".", ".."}:
            raise StagingError("invalid staging cleanup entry name")
    if os.fstat(source_parent_fd).st_dev != os.fstat(destination_parent_fd).st_dev:
        raise StagingError("staging cleanup cannot cross filesystems")
    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    system = platform.system()
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
            0x00000004,  # RENAME_EXCL
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
            0x00000001,  # RENAME_NOREPLACE
        )
    else:
        raise StagingError(
            f"exclusive staging cleanup rename is unsupported on {system}"
        )
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.ENOENT:
            raise FileNotFoundError(error, os.strerror(error), source_name)
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), destination_name)
        raise StagingError("exclusive staging cleanup rename failed") from OSError(
            error, os.strerror(error), source_name
        )


class _CleanupArea:
    """Private sibling directory used to retire or preserve raced entries."""

    def __init__(self, base: Path, base_fd: int) -> None:
        self.parent_fd = os.dup(base_fd)
        for _ in range(128):
            name = f".bugate-update-preserved-{secrets.token_hex(16)}"
            try:
                os.mkdir(name, 0o700, dir_fd=self.parent_fd)
                break
            except FileExistsError:
                continue
        else:
            os.close(self.parent_fd)
            raise StagingError("cannot allocate a private staging cleanup directory")
        self.name = name
        self.path = base / name
        try:
            self.fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.parent_fd,
            )
        except BaseException:
            try:
                os.rmdir(name, dir_fd=self.parent_fd)
            finally:
                os.close(self.parent_fd)
            raise
        self.identity = _directory_identity(self.fd)
        os.fchmod(self.fd, 0o700)
        os.fsync(self.fd)
        os.fsync(self.parent_fd)
        self.counter = 0
        self.preserved: list[tuple[str, str]] = []

    def destination(self, source_label: str) -> str:
        self.counter += 1
        basename = PurePosixPath(source_label).name[:80] or "entry"
        return f"{self.counter:06d}-{basename}"

    def close(self) -> None:
        os.close(self.fd)
        if not self.preserved:
            try:
                current = os.stat(
                    self.name,
                    dir_fd=self.parent_fd,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISDIR(current.st_mode)
                    and _entry_identity(current) == self.identity
                ):
                    os.rmdir(self.name, dir_fd=self.parent_fd)
                    os.fsync(self.parent_fd)
            except OSError:
                pass
        os.close(self.parent_fd)


def _entry_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _retire_or_preserve_at(
    parent_fd: int,
    name: str,
    *,
    expected_identity: tuple[int, int] | None,
    expected_directory: bool,
    source_label: str,
    cleanup: _CleanupArea,
) -> bool:
    """Move first, then delete only an exact updater-created inode.

    Moving through an exclusive descriptor-anchored rename closes the lstat /
    unlink race: if a concurrent actor exchanges the source, their entry is
    moved to the preservation directory and is never recursively deleted.
    """

    destination = cleanup.destination(source_label)
    try:
        _exclusive_rename_at(parent_fd, name, cleanup.fd, destination)
    except FileNotFoundError:
        return False
    os.fsync(parent_fd)
    os.fsync(cleanup.fd)
    moved = os.stat(destination, dir_fd=cleanup.fd, follow_symlinks=False)
    moved_identity = _entry_identity(moved)
    exact = expected_identity is not None and moved_identity == expected_identity
    if exact and expected_directory and stat.S_ISDIR(moved.st_mode):
        try:
            os.rmdir(destination, dir_fd=cleanup.fd)
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise
            exact = False
    elif exact and not expected_directory and not stat.S_ISDIR(moved.st_mode):
        os.unlink(destination, dir_fd=cleanup.fd)
    else:
        exact = False
    if not exact:
        cleanup.preserved.append((source_label, destination))
    os.fsync(cleanup.fd)
    return exact


def _clean_directory_fd(
    directory_fd: int,
    *,
    directory_identities: Mapping[str, tuple[int, int]],
    leaf_identities: Mapping[str, tuple[int, int]],
    cleanup: _CleanupArea,
    relative: str = ".",
) -> None:
    """Clean a created tree without following or deleting exchanged entries."""

    expected_root = directory_identities.get(relative)
    if expected_root != _directory_identity(directory_fd):
        raise StagingError(f"staging cleanup root identity changed: {relative}")
    prefix = "" if relative == "." else f"{relative}/"
    child_directories = {
        path[len(prefix) :]
        for path in directory_identities
        if path != relative
        and path.startswith(prefix)
        and "/" not in path[len(prefix) :]
    }
    child_leaves = {
        path[len(prefix) :]
        for path in leaf_identities
        if path.startswith(prefix) and "/" not in path[len(prefix) :]
    }
    for name in list(os.listdir(directory_fd)):
        child_relative = name if relative == "." else f"{relative}/{name}"
        if name in child_directories:
            expected = directory_identities[child_relative]
            try:
                child_fd = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
            except OSError:
                child_fd = None
            if child_fd is not None:
                try:
                    if _directory_identity(child_fd) == expected:
                        _clean_directory_fd(
                            child_fd,
                            directory_identities=directory_identities,
                            leaf_identities=leaf_identities,
                            cleanup=cleanup,
                            relative=child_relative,
                        )
                finally:
                    os.close(child_fd)
            _retire_or_preserve_at(
                directory_fd,
                name,
                expected_identity=expected,
                expected_directory=True,
                source_label=child_relative,
                cleanup=cleanup,
            )
        elif name in child_leaves:
            _retire_or_preserve_at(
                directory_fd,
                name,
                expected_identity=leaf_identities[child_relative],
                expected_directory=False,
                source_label=child_relative,
                cleanup=cleanup,
            )
        else:
            _retire_or_preserve_at(
                directory_fd,
                name,
                expected_identity=None,
                expected_directory=False,
                source_label=child_relative,
                cleanup=cleanup,
            )
    os.fsync(directory_fd)


def _verify_created_identities(
    root_fd: int,
    directory_identities: Mapping[str, tuple[int, int]],
    leaf_identities: Mapping[str, tuple[int, int]],
) -> None:
    for relative in sorted(
        directory_identities,
        key=lambda value: (len(PurePosixPath(value).parts), value),
    ):
        descriptor = _open_directory_beneath(
            root_fd,
            relative,
            directory_identities=directory_identities,
        )
        os.close(descriptor)
    for relative, expected in sorted(leaf_identities.items()):
        parent_fd, name = _parent_directory_fd(
            root_fd,
            relative,
            directory_identities=directory_identities,
        )
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise StagingError(f"staged leaf identity changed: {relative}") from exc
        finally:
            os.close(parent_fd)
        if _entry_identity(current) != expected:
            raise StagingError(f"staged leaf identity changed: {relative}")


def prepare_archive(
    archive: Path | str,
    checksums: Path | str,
    staging_dir: Path | str,
    expected_version: str | None = None,
) -> PreparedRelease:
    """Verify an archive and extract it only beneath an empty temporary root."""

    archive_path = Path(archive)
    checksum_path = Path(checksums)
    expected_digest, archive_format, archive_version = _checksum_expectation(
        archive_path, checksum_path
    )
    if expected_version is not None:
        try:
            requested = contract.validate_semver(expected_version)
        except contract.ContractError as exc:
            raise ManifestError(f"invalid requested release version: {expected_version!r}") from exc
        if requested != archive_version:
            raise ManifestError(
                f"requested/archive version mismatch: requested={requested}, archive={archive_version}"
            )
    stage, stage_fd, stage_identity = _open_pinned_directory(
        staging_dir, require_empty=True, label="staging directory"
    )
    try:
        cleanup_base, cleanup_base_fd, cleanup_base_identity = _open_pinned_directory(
            tempfile.gettempdir(),
            require_empty=False,
            label="system temporary directory",
        )
    except BaseException:
        os.close(stage_fd)
        raise
    if (
        stage_identity[0] != cleanup_base_identity[0]
        or cleanup_base == stage
        or stage in cleanup_base.parents
    ):
        os.close(cleanup_base_fd)
        os.close(stage_fd)
        raise StagingError(
            "staging must share a filesystem with an independent system "
            "temporary preservation root"
        )
    snapshot = stage / (".bugate-source.tar.gz" if archive_format == "tar.gz" else ".bugate-source.zip")
    release_root = stage / f"bugate-{archive_version}"
    snapshot_fd: int | None = None
    release_fd: int | None = None
    release_identity: tuple[int, int] | None = None
    stage_leaf_identities: dict[str, tuple[int, int]] = {}
    release_directory_identities: dict[str, tuple[int, int]] = {}
    release_leaf_identities: dict[str, tuple[int, int]] = {}
    try:
        copied_digest, snapshot_fd = _copy_snapshot(
            archive_path,
            snapshot,
            destination_dir_fd=stage_fd,
            created_identities=stage_leaf_identities,
        )
        if copied_digest != expected_digest:
            raise ChecksumError(
                f"archive SHA-256 mismatch for {archive_path.name}: "
                f"expected {expected_digest}, actual {copied_digest}"
            )
        with _pinned_stream(snapshot_fd) as stream:
            if archive_format == "tar.gz":
                inspection = _inspect_tar(
                    stream,
                    f"bugate-{archive_version}",
                    archive_version,
                    label=archive_path.name,
                )
            else:
                inspection = _inspect_zip(
                    stream,
                    f"bugate-{archive_version}",
                    archive_version,
                    label=archive_path.name,
                )
        manifest = _verify_release_metadata(
            inspection, expected_version=expected_version
        )
        try:
            os.mkdir(f"bugate-{archive_version}", 0o755, dir_fd=stage_fd)
            release_fd = os.open(
                f"bugate-{archive_version}",
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=stage_fd,
            )
            release_identity = _directory_identity(release_fd)
            release_directory_identities["."] = release_identity
            os.fsync(stage_fd)
        except OSError as exc:
            raise StagingError("cannot create the staged release root") from exc
        with _pinned_stream(snapshot_fd) as stream:
            if inspection.archive_format == "tar.gz":
                _extract_tar(
                    stream,
                    release_root,
                    inspection.entries,
                    inspection.prefix,
                    root_fd=release_fd,
                    directory_identities=release_directory_identities,
                    leaf_identities=release_leaf_identities,
                )
            else:
                _extract_zip(
                    stream,
                    release_root,
                    inspection.entries,
                    inspection.prefix,
                    root_fd=release_fd,
                    directory_identities=release_directory_identities,
                    leaf_identities=release_leaf_identities,
                )
        _assert_pinned_path(stage, stage_fd, label="staging directory")
        release_stat = os.fstat(release_fd)
        release_current = os.stat(
            f"bugate-{archive_version}",
            dir_fd=stage_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(release_current.st_mode)
            or (release_stat.st_dev, release_stat.st_ino)
            != (release_current.st_dev, release_current.st_ino)
        ):
            raise StagingError("staged release root path changed during extraction")
        # The private archive snapshot is no longer needed after extraction.
        # Retire it before the final release verification so any exchange at
        # this boundary is detected by the validation that immediately follows.
        pinned = os.fstat(snapshot_fd)
        current = os.stat(
            snapshot.name, dir_fd=stage_fd, follow_symlinks=False
        )
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise StagingError("release archive snapshot path changed during validation")
        snapshot_cleanup = _CleanupArea(cleanup_base, cleanup_base_fd)
        try:
            snapshot_retired = _retire_or_preserve_at(
                stage_fd,
                snapshot.name,
                expected_identity=_entry_identity(pinned),
                expected_directory=False,
                source_label=snapshot.name,
                cleanup=snapshot_cleanup,
            )
            if not snapshot_retired:
                raise StagingError(
                    "release archive snapshot changed during finalization; "
                    f"replacement preserved at {snapshot_cleanup.path}"
                )
        finally:
            snapshot_cleanup.close()
        os.close(snapshot_fd)
        snapshot_fd = None

        # Re-read every staged byte/type/mode/link after all source-side writes
        # and snapshot finalization, then bind every created inode once more
        # immediately before exposing PreparedRelease.
        _assert_pinned_path(stage, stage_fd, label="staging directory")
        release_current = os.stat(
            f"bugate-{archive_version}",
            dir_fd=stage_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(release_current.st_mode)
            or (release_stat.st_dev, release_stat.st_ino)
            != (release_current.st_dev, release_current.st_ino)
        ):
            raise StagingError(
                "staged release root path changed during finalization"
            )
        with _pinned_directory_cwd(release_fd) as anchored_release:
            staged_manifest = _verify_unpacked(
                anchored_release, expected_version=expected_version
            )
        if staged_manifest != manifest:
            raise StagingError("staged manifest differs from the verified archive")
        _verify_created_identities(
            release_fd,
            release_directory_identities,
            release_leaf_identities,
        )
        release_current = os.stat(
            f"bugate-{archive_version}",
            dir_fd=stage_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(release_current.st_mode)
            or (release_stat.st_dev, release_stat.st_ino)
            != (release_current.st_dev, release_current.st_ino)
        ):
            raise StagingError(
                "staged release root path changed during validation"
            )
        _assert_pinned_path(stage, stage_fd, label="staging directory")
        os.close(release_fd)
        release_fd = None
        os.close(stage_fd)
        os.close(cleanup_base_fd)
        return PreparedRelease(
            release_root,
            manifest,
            copied_digest,
            "archive",
            (release_stat.st_dev, release_stat.st_ino),
        )
    except BaseException as failure:
        cleanup: _CleanupArea | None = None
        try:
            cleanup = _CleanupArea(cleanup_base, cleanup_base_fd)
            if release_fd is not None and release_directory_identities:
                _clean_directory_fd(
                    release_fd,
                    directory_identities=release_directory_identities,
                    leaf_identities=release_leaf_identities,
                    cleanup=cleanup,
                )
            stage_directory_identities = {".": stage_identity}
            if release_identity is not None:
                stage_directory_identities[f"bugate-{archive_version}"] = (
                    release_identity
                )
            _clean_directory_fd(
                stage_fd,
                directory_identities=stage_directory_identities,
                leaf_identities=stage_leaf_identities,
                cleanup=cleanup,
            )
            if cleanup.preserved:
                preserved = ", ".join(
                    source_label for source_label, _ in cleanup.preserved
                )
                _note_failure(
                    failure,
                    "concurrent or unknown staging entries were preserved at "
                    f"{cleanup.path}: {preserved}",
                )
        except BaseException as cleanup_failure:
            _note_failure(
                failure,
                "identity-safe staging cleanup was incomplete: "
                f"{cleanup_failure}",
            )
        finally:
            if snapshot_fd is not None:
                os.close(snapshot_fd)
            if release_fd is not None:
                os.close(release_fd)
            if cleanup is not None:
                cleanup.close()
            os.close(cleanup_base_fd)
            os.close(stage_fd)
        raise


__all__ = [
    "ArchiveInspection",
    "ArchiveSafetyError",
    "ChecksumError",
    "ManifestError",
    "PreparedRelease",
    "SourceEntry",
    "StagingError",
    "UpdateSourceError",
    "inspect_archive",
    "parse_checksum_asset",
    "parse_checksum_bytes",
    "prepare_archive",
    "prepare_unpacked",
    "verify_archive_checksum",
]
