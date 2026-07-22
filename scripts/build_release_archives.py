#!/usr/bin/env python3
"""Build, self-verify, and atomically publish deterministic BUGate archives.

The complete Git release tree is preserved.  Generated release and legacy
manifests are added as an overlay; they alone define the narrower imported-mode
write catalog.  All work is staged and verified before the three public assets
are transactionally replaced.
"""
from __future__ import annotations

import argparse
import ast
import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

# A clean release command must not dirty its own tree merely by importing its
# sibling contract modules (synthetic release fixtures need not carry a broad
# Python-cache ignore policy).
sys.dont_write_bytecode = True

import bugate_install_contract as contract
import bugate_legacy_manifest as legacy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "dist"
DEFAULT_EPOCH = 0
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class SourceEntry:
    path: str
    mode: str
    kind: str


def run_git(*args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"JSON metadata must be an object: {path}")
    return value


def manifest_version() -> str:
    values = []
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        value = read_json(ROOT / relative).get("version")
        try:
            values.append(contract.validate_semver(value))
        except contract.ContractError as exc:
            raise SystemExit(str(exc)) from exc
    if values[0] != values[1]:
        raise SystemExit(
            f"plugin manifest versions differ: codex={values[0]!r}, claude={values[1]!r}"
        )
    return values[0]


def updater_version() -> str:
    path = ROOT / "scripts" / "bugate_update.py"
    if not path.is_file():
        raise SystemExit("release is missing bootstrap scripts/bugate_update.py")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise SystemExit(f"cannot inspect updater version: {exc}") from exc
    value: Any = None
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "UPDATER_VERSION"
            for target in targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (TypeError, ValueError) as exc:
                raise SystemExit("UPDATER_VERSION must be a module-level string literal") from exc
            break
    if value is None:
        raise SystemExit("scripts/bugate_update.py must declare literal UPDATER_VERSION")
    try:
        return contract.validate_semver(value)
    except contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc


def validate_updater_worker_bundle() -> None:
    missing: list[str] = []
    for relative in contract.UPDATER_WORKER_FILES:
        path = ROOT / relative
        try:
            details = os.lstat(path)
        except OSError:
            missing.append(relative)
            continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            missing.append(relative)
    if missing:
        raise SystemExit(
            "release is missing the complete updater worker bundle: "
            + ", ".join(missing)
        )

    updater = ROOT / "scripts/bugate_update.py"
    try:
        tree = ast.parse(updater.read_text(encoding="utf-8"), filename=str(updater))
    except (OSError, SyntaxError) as exc:
        raise SystemExit(f"cannot inspect updater worker contract: {exc}") from exc
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    required_imports = {
        "bugate_install_contract",
        "bugate_update_engine",
        "bugate_update_source",
        "bugate_update_transaction",
    }
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if not required_imports.issubset(imported) or not {"build_parser", "main"}.issubset(functions):
        raise SystemExit(
            "scripts/bugate_update.py does not expose the complete bootstrap CLI contract"
        )


def is_dirty() -> bool:
    result = run_git("status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git status failed")
    return bool(result.stdout.strip())


def _decode_git_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit("release paths must be valid UTF-8") from exc
    try:
        return contract.validate_relative_path(value, field="git path")
    except contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc


def _tracked_modes() -> dict[str, str]:
    result = run_git("ls-files", "--stage", "-z", binary=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
    modes: dict[str, str] = {}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, name = raw.split(b"\t", 1)
            mode, _object_id, stage = metadata.split()
        except ValueError as exc:
            raise SystemExit("invalid git index entry") from exc
        if stage != b"0":
            raise SystemExit("unmerged git index entries cannot be released")
        path = _decode_git_path(name)
        raw_mode = mode.decode("ascii")
        if raw_mode not in {"100644", "100755", "120000"}:
            raise SystemExit(f"unsupported git mode {raw_mode} for {path}")
        modes[path] = raw_mode
    return modes


def source_entries(include_untracked: bool) -> list[SourceEntry]:
    tracked = _tracked_modes()
    selected = set(tracked)
    if include_untracked:
        result = run_git("ls-files", "-z", "--others", "--exclude-standard", binary=True)
        if result.returncode != 0:
            raise SystemExit(result.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
        selected.update(_decode_git_path(raw) for raw in result.stdout.split(b"\0") if raw)

    entries: list[SourceEntry] = []
    folded: set[str] = set()
    for relative in sorted(selected):
        if relative.casefold() in folded:
            raise SystemExit(f"case-conflicting release path: {relative}")
        folded.add(relative.casefold())
        path = ROOT / relative
        if not (path.exists() or path.is_symlink()):
            raise SystemExit(f"selected release path is missing: {relative}")
        st = os.lstat(path)
        indexed = tracked.get(relative)
        if stat.S_ISLNK(st.st_mode):
            if indexed not in {None, "120000"}:
                raise SystemExit(f"git/file type mismatch for symlink: {relative}")
            try:
                contract.validate_symlink_target(relative, os.readlink(path))
            except contract.ContractError as exc:
                raise SystemExit(str(exc)) from exc
            entries.append(SourceEntry(relative, "0777", "symlink"))
        elif stat.S_ISREG(st.st_mode):
            mode = "0755" if stat.S_IMODE(st.st_mode) & 0o111 else "0644"
            expected = {"100644": "0644", "100755": "0755", None: mode}.get(indexed)
            if expected is None or mode != expected:
                raise SystemExit(f"git/file executable mode mismatch for {relative}")
            entries.append(SourceEntry(relative, mode, "file"))
        else:
            raise SystemExit(f"unsupported release source type: {relative}")
    if not entries:
        raise SystemExit("no files selected for archive")
    return entries


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_bytes(
    relative: str,
    overlays: Mapping[str, bytes],
) -> bytes:
    if relative in overlays:
        return overlays[relative]
    return (ROOT / relative).read_bytes()


def _add_tar_item(
    archive: tarfile.TarFile,
    item: Mapping[str, Any],
    prefix: str,
    overlays: Mapping[str, bytes],
) -> None:
    relative = item["path"]
    name = f"{prefix}/{relative}"
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = DEFAULT_EPOCH
    kind = item["type"]
    if kind == "directory":
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
        archive.addfile(info)
    elif kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.mode = 0o777
        info.linkname = item["target"]
        archive.addfile(info)
    elif kind == "file":
        payload = _payload_bytes(relative, overlays)
        info.type = tarfile.REGTYPE
        info.mode = int(item["mode"], 8)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    else:
        raise SystemExit(f"unsupported manifest archive type: {relative}")


def _zip_info(name: str, kind: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name + ("/" if kind == "directory" else ""))
    info.date_time = ZIP_EPOCH
    info.create_system = 3
    file_type = {
        "directory": stat.S_IFDIR,
        "symlink": stat.S_IFLNK,
        "file": stat.S_IFREG,
    }[kind]
    info.external_attr = (file_type | mode) << 16
    if kind == "directory":
        info.external_attr |= 0x10
    return info


def _add_zip_item(
    archive: zipfile.ZipFile,
    item: Mapping[str, Any],
    prefix: str,
    overlays: Mapping[str, bytes],
) -> None:
    relative = item["path"]
    kind = item["type"]
    mode = int(item["mode"], 8)
    info = _zip_info(f"{prefix}/{relative}", kind, mode)
    if kind == "directory":
        payload = b""
    elif kind == "symlink":
        payload = item["target"].encode("utf-8")
    else:
        payload = _payload_bytes(relative, overlays)
    archive.writestr(
        info,
        payload,
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def _expected_records(
    manifest: Mapping[str, Any], manifest_bytes: bytes
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw in manifest["archive_inventory"]:
        item = copy.deepcopy(raw)
        item.pop("roles", None)
        if item.pop("digest_ref", None) == "self_digest":
            item["sha256"] = contract.sha256_bytes(manifest_bytes)
        records[item.pop("path")] = item
    return records


def _tar_records(path: Path, prefix: str) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    records: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            expected_prefix = prefix + "/"
            if not member.name.startswith(expected_prefix):
                raise SystemExit(f"tar entry is outside archive prefix: {member.name}")
            relative = member.name[len(expected_prefix) :].rstrip("/")
            try:
                contract.validate_relative_path(relative, field="tar path")
            except contract.ContractError as exc:
                raise SystemExit(str(exc)) from exc
            if relative in records:
                raise SystemExit(f"duplicate tar entry: {relative}")
            if member.isdir():
                records[relative] = {"type": "directory", "mode": "0755"}
            elif member.issym():
                contract.validate_symlink_target(relative, member.linkname)
                records[relative] = {
                    "type": "symlink",
                    "mode": "0777",
                    "target": member.linkname,
                }
            elif member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise SystemExit(f"cannot read tar entry: {relative}")
                data = stream.read()
                payloads[relative] = data
                records[relative] = {
                    "type": "file",
                    "mode": f"{stat.S_IMODE(member.mode):04o}",
                    "sha256": contract.sha256_bytes(data),
                }
            else:
                raise SystemExit(f"unsafe tar entry type: {relative}")
            if member.mtime != 0 or member.uid != 0 or member.gid != 0:
                raise SystemExit(f"non-deterministic tar metadata: {relative}")
    return records, payloads


def _zip_records(path: Path, prefix: str) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    records: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            expected_prefix = prefix + "/"
            if not info.filename.startswith(expected_prefix):
                raise SystemExit(f"zip entry is outside archive prefix: {info.filename}")
            relative = info.filename[len(expected_prefix) :].rstrip("/")
            try:
                contract.validate_relative_path(relative, field="zip path")
            except contract.ContractError as exc:
                raise SystemExit(str(exc)) from exc
            if relative in records:
                raise SystemExit(f"duplicate zip entry: {relative}")
            raw_mode = info.external_attr >> 16
            kind_bits = stat.S_IFMT(raw_mode)
            mode = stat.S_IMODE(raw_mode)
            data = archive.read(info)
            if kind_bits == stat.S_IFDIR:
                if data:
                    raise SystemExit(f"zip directory has payload: {relative}")
                records[relative] = {"type": "directory", "mode": f"{mode:04o}"}
            elif kind_bits == stat.S_IFLNK:
                try:
                    target = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SystemExit(f"zip symlink target is not UTF-8: {relative}") from exc
                contract.validate_symlink_target(relative, target)
                records[relative] = {"type": "symlink", "mode": f"{mode:04o}", "target": target}
            elif kind_bits == stat.S_IFREG:
                payloads[relative] = data
                records[relative] = {
                    "type": "file",
                    "mode": f"{mode:04o}",
                    "sha256": contract.sha256_bytes(data),
                }
            else:
                raise SystemExit(f"unsafe zip entry type: {relative}")
            if info.date_time != ZIP_EPOCH:
                raise SystemExit(f"non-deterministic zip timestamp: {relative}")
    return records, payloads


def verify_archives(
    tar_path: Path,
    zip_path: Path,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
    legacy_overlays: Mapping[str, bytes],
) -> None:
    version = manifest["bugate_version"]
    prefix = f"bugate-{version}"
    expected = _expected_records(manifest, manifest_bytes)
    tar_records, tar_payloads = _tar_records(tar_path, prefix)
    zip_records, zip_payloads = _zip_records(zip_path, prefix)
    if tar_records != expected or zip_records != expected:
        raise SystemExit("archive entries do not exactly match the release manifest")
    if tar_payloads != zip_payloads:
        raise SystemExit("tar and zip file payloads differ")
    if tar_payloads.get(contract.RELEASE_MANIFEST_PATH) != manifest_bytes:
        raise SystemExit("archive release manifest bytes differ from canonical overlay")
    try:
        archived_manifest = json.loads(manifest_bytes)
        contract.validate_current_release_manifest(
            archived_manifest, expected_version=version
        )
    except (json.JSONDecodeError, contract.ContractError) as exc:
        raise SystemExit(f"archive release manifest self-check failed: {exc}") from exc
    for path, expected_bytes in legacy_overlays.items():
        if tar_payloads.get(path) != expected_bytes:
            raise SystemExit(f"legacy manifest overlay mismatch: {path}")
        try:
            legacy.validate_legacy_manifest(json.loads(expected_bytes))
        except (json.JSONDecodeError, contract.ContractError) as exc:
            raise SystemExit(f"legacy manifest self-check failed for {path}: {exc}") from exc


def write_checksums(path: Path, archives: tuple[Path, Path]) -> None:
    path.write_bytes(
        "".join(f"{file_sha256(archive)}  {archive.name}\n" for archive in archives).encode(
            "ascii"
        )
    )


PUBLISH_JOURNAL_SCHEMA = 1


def _publish_state_dir(out_dir: Path) -> Path:
    return out_dir.parent / f".{out_dir.name}.bugate-release-publish"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_publish_journal(state: Path, journal: Mapping[str, Any]) -> None:
    temporary = state / "journal.json.tmp"
    final = state / "journal.json"
    with temporary.open("wb") as stream:
        stream.write(contract.canonical_json_bytes(journal))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, final)
    _fsync_directory(state)


def _load_publish_journal(state: Path, out_dir: Path) -> dict[str, Any]:
    path = state / "journal.json"
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"release publish recovery required but journal is invalid: {state}"
        ) from exc
    if (
        not isinstance(journal, dict)
        or set(journal) != {
            "schema_version",
            "status",
            "out_dir_name",
            "out_dir_existed",
            "assets",
        }
        or journal.get("schema_version") != PUBLISH_JOURNAL_SCHEMA
        or journal.get("out_dir_name") != out_dir.name
        or journal.get("status") not in {"publishing", "committed"}
        or not isinstance(journal.get("out_dir_existed"), bool)
        or not isinstance(journal.get("assets"), list)
        or len(journal["assets"]) != 3
    ):
        raise SystemExit(f"release publish journal contract mismatch: {state}")
    names: set[str] = set()
    for record in journal["assets"]:
        if not isinstance(record, dict) or set(record) != {
            "name",
            "old_exists",
            "old_sha256",
            "new_sha256",
        }:
            raise SystemExit(f"release publish journal asset is invalid: {state}")
        name = record["name"]
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise SystemExit(f"release publish journal filename is invalid: {state}")
        names.add(name)
        if not isinstance(record["old_exists"], bool):
            raise SystemExit(f"release publish journal old_exists is invalid: {state}")
        if record["old_exists"]:
            contract.validate_sha256(record["old_sha256"], field="old asset sha256")
        elif record["old_sha256"] is not None:
            raise SystemExit(f"release publish journal has impossible old digest: {state}")
        contract.validate_sha256(record["new_sha256"], field="new asset sha256")
    return journal


def _regular_sha256(path: Path, *, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise OSError(f"{label} is missing or not a regular file: {path}")
    return file_sha256(path)


def _cleanup_publish_state(state: Path) -> None:
    shutil.rmtree(state)
    _fsync_directory(state.parent)


def _restore_publish(out_dir: Path, state: Path, journal: Mapping[str, Any]) -> None:
    backup = state / "backup"
    for record in journal["assets"]:
        destination = out_dir / record["name"]
        saved = backup / record["name"]
        if record["old_exists"]:
            if saved.exists() or saved.is_symlink():
                actual = _regular_sha256(saved, label="release backup")
                if actual != record["old_sha256"]:
                    raise OSError(f"release backup digest mismatch: {saved}")
                os.replace(saved, destination)
            elif not (
                destination.is_file()
                and not destination.is_symlink()
                and file_sha256(destination) == record["old_sha256"]
            ):
                raise OSError(
                    f"release backup is missing and destination is not restored: {destination}"
                )
        elif destination.exists() or destination.is_symlink():
            actual = _regular_sha256(destination, label="partially published asset")
            if actual != record["new_sha256"]:
                raise OSError(
                    f"unowned file blocks release recovery: {destination}"
                )
            destination.unlink()

    for record in journal["assets"]:
        destination = out_dir / record["name"]
        if record["old_exists"]:
            if _regular_sha256(destination, label="restored release asset") != record["old_sha256"]:
                raise OSError(f"restored release asset digest mismatch: {destination}")
        elif destination.exists() or destination.is_symlink():
            raise OSError(f"new release asset remains after recovery: {destination}")
    if not journal.get("out_dir_existed", True):
        try:
            out_dir.rmdir()
        except OSError:
            pass
    _cleanup_publish_state(state)


def _recover_pending_publish(out_dir: Path) -> None:
    state = _publish_state_dir(out_dir)
    if not state.exists():
        return
    if not state.is_dir() or state.is_symlink():
        raise SystemExit(f"release publish recovery state is unsafe: {state}")
    journal = _load_publish_journal(state, out_dir)
    if journal["status"] == "committed":
        for record in journal["assets"]:
            destination = out_dir / record["name"]
            try:
                actual = _regular_sha256(destination, label="committed release asset")
            except OSError as exc:
                raise SystemExit(
                    f"committed release publish state cannot be finalized; retained {state}: {exc}"
                ) from exc
            if actual != record["new_sha256"]:
                raise SystemExit(
                    f"committed release asset drifted; recovery state retained: {state}"
                )
        _cleanup_publish_state(state)
        return
    try:
        _restore_publish(out_dir, state, journal)
    except BaseException as exc:
        raise SystemExit(
            f"release publish recovery failed; backup and journal retained at {state}: {exc}"
        ) from exc


def _publish_atomically(staged: tuple[Path, Path, Path], out_dir: Path) -> tuple[Path, Path, Path]:
    parent = out_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    _recover_pending_publish(out_dir)
    if len({path.name for path in staged}) != 3:
        raise SystemExit("release publication requires three uniquely named assets")
    for path in staged:
        _regular_sha256(path, label="staged release asset")

    state = _publish_state_dir(out_dir)
    try:
        state.mkdir()
        (state / "backup").mkdir()
    except FileExistsError as exc:
        raise SystemExit(f"release publish recovery state already exists: {state}") from exc
    out_existed = out_dir.exists()
    destinations = tuple(out_dir / path.name for path in staged)
    assets: list[dict[str, Any]] = []
    for source, destination in zip(staged, destinations):
        old_exists = destination.exists() or destination.is_symlink()
        old_sha = (
            _regular_sha256(destination, label="previous release asset")
            if old_exists
            else None
        )
        assets.append(
            {
                "name": source.name,
                "old_exists": old_exists,
                "old_sha256": old_sha,
                "new_sha256": file_sha256(source),
            }
        )
    journal: dict[str, Any] = {
        "schema_version": PUBLISH_JOURNAL_SCHEMA,
        "status": "publishing",
        "out_dir_name": out_dir.name,
        "out_dir_existed": out_existed,
        "assets": assets,
    }
    # out_dir_existed is intentionally part of the strict journal contract but
    # does not contain an absolute machine path.
    _write_publish_journal(state, journal)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for record in assets:
            destination = out_dir / record["name"]
            if record["old_exists"]:
                os.replace(destination, state / "backup" / record["name"])
        for source, destination in zip(staged, destinations):
            os.replace(source, destination)
        for record in assets:
            destination = out_dir / record["name"]
            if _regular_sha256(destination, label="published release asset") != record["new_sha256"]:
                raise OSError(f"published release asset digest mismatch: {destination}")
        journal["status"] = "committed"
        _write_publish_journal(state, journal)
    except BaseException as publish_error:
        try:
            _restore_publish(out_dir, state, journal)
        except BaseException as restore_error:
            raise RuntimeError(
                f"release publish failed and restore failed; backup and journal retained at {state}: "
                f"publish={publish_error}; restore={restore_error}"
            ) from restore_error
        raise
    _cleanup_publish_state(state)
    return destinations


def build(
    version: str,
    out_dir: Path,
    *,
    include_untracked: bool,
    allow_dirty: bool,
) -> tuple[Path, Path, Path]:
    out_dir = out_dir.resolve()
    # A prior interrupted three-asset publication must be recovered before Git
    # dirtiness or version checks can mask its durable sibling journal.
    _recover_pending_publish(out_dir)
    try:
        release_version = contract.validate_semver(version)
    except contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc
    if not allow_dirty and is_dirty():
        raise SystemExit(
            "refusing to build release archives from a dirty tree; commit first or pass --allow-dirty"
        )
    required_version = manifest_version()
    cli_version = updater_version()
    if release_version != required_version or cli_version != required_version:
        raise SystemExit(
            "release version mismatch: "
            f"requested={release_version!r}, plugins={required_version!r}, updater={cli_version!r}"
        )
    validate_updater_worker_bundle()
    wrapper = ROOT / "bin" / "bugate-update"
    if not wrapper.is_file() or wrapper.is_symlink() or not os.access(wrapper, os.X_OK):
        raise SystemExit("release is missing executable bin/bugate-update")
    if wrapper.read_bytes() != contract.BUGATE_UPDATE_WRAPPER_BYTES:
        raise SystemExit(
            "bin/bugate-update does not match the canonical updater dispatch contract"
        )

    selected = source_entries(include_untracked)
    selected_paths = [entry.path for entry in selected]
    legacy_overlays = legacy.generate_all_legacy_manifests(ROOT)
    try:
        manifest = contract.build_release_manifest(
            ROOT,
            release_version,
            selected_paths=selected_paths,
            overlay_files=legacy_overlays,
            updater_minimum_version=contract.UPDATER_PROTOCOL_MINIMUM_VERSION,
        )
        contract.validate_current_release_manifest(
            manifest, expected_version=release_version
        )
    except contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc
    manifest_bytes = contract.canonical_json_bytes(manifest)
    overlays = dict(legacy_overlays)
    overlays[contract.RELEASE_MANIFEST_PATH] = manifest_bytes

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"bugate-{release_version}"
    with tempfile.TemporaryDirectory(
        prefix=".bugate-release-stage-", dir=out_dir.parent
    ) as raw_stage:
        stage = Path(raw_stage)
        tar_path = stage / f"{prefix}.tar.gz"
        zip_path = stage / f"{prefix}.zip"
        sums_path = stage / f"{prefix}.SHA256SUMS"
        with tar_path.open("wb") as raw_tar:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_tar,
                mtime=DEFAULT_EPOCH,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as archive:
                    for item in manifest["archive_inventory"]:
                        _add_tar_item(archive, item, prefix, overlays)
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for item in manifest["archive_inventory"]:
                _add_zip_item(archive, item, prefix, overlays)
        verify_archives(
            tar_path,
            zip_path,
            manifest,
            manifest_bytes,
            legacy_overlays,
        )
        write_checksums(sums_path, (tar_path, zip_path))
        # Re-read the checksum file before publication; malformed/ambiguous
        # output can never escape staging.
        lines = sums_path.read_text(encoding="ascii").splitlines()
        if len(lines) != 2 or len({line.split("  ", 1)[1] for line in lines}) != 2:
            raise SystemExit("generated checksum asset is ambiguous")
        published = _publish_atomically((tar_path, zip_path, sums_path), out_dir)
    return published


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="release version (default: plugin manifest version)")
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory (default: dist)"
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow tracked or untracked changes (development preview only)",
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="include untracked non-ignored files (development preview only)",
    )
    args = parser.parse_args(argv)

    required_version = manifest_version()
    requested = args.version.strip() if args.version is not None else required_version
    version = requested[1:] if requested.startswith("v") else requested
    try:
        contract.validate_semver(version)
    except contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc
    if version != required_version:
        raise SystemExit(
            f"requested version {version!r} does not match both plugin manifests "
            f"({required_version!r})"
        )
    assets = build(
        version,
        Path(args.out_dir),
        include_untracked=args.include_untracked,
        allow_dirty=args.allow_dirty,
    )
    for path in assets:
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path
        print(f"built {display}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
