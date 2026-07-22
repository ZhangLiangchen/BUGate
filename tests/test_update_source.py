#!/usr/bin/env python3
"""Tests for safe, SUT-neutral release-source preparation."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402
import bugate_update_source as source  # noqa: E402


VERSION = "0.4.2"
PREFIX = f"bugate-{VERSION}"


def _write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _all_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    snapshot: dict[str, tuple[str, bytes | str, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode):
            snapshot[relative] = ("symlink", os.readlink(path), 0o777)
        elif stat.S_ISDIR(details.st_mode):
            snapshot[relative] = (
                "directory",
                b"",
                stat.S_IMODE(details.st_mode),
            )
        else:
            snapshot[relative] = (
                "file",
                path.read_bytes(),
                stat.S_IMODE(details.st_mode),
            )
    return snapshot


class SyntheticRelease:
    def __init__(
        self,
        root: Path,
        version: str = VERSION,
        *,
        updater_source: bytes | None = None,
    ) -> None:
        self.root = root
        self.version = version
        self.prefix = f"bugate-{version}"
        self._populate(
            updater_source
            if updater_source is not None
            else f'UPDATER_VERSION = "{version}"\n'.encode()
        )
        self.manifest = contract.build_release_manifest(
            root,
            version,
            selected_paths=_all_files(root),
            updater_minimum_version=version,
        )
        contract.validate_current_release_manifest(
            self.manifest, expected_version=version
        )
        self.manifest_bytes = contract.canonical_json_bytes(self.manifest)

    def _populate(self, updater_source: bytes) -> None:
        for tree in contract.VENDOR_TREE_ROOTS:
            (self.root / tree).mkdir(parents=True, exist_ok=True)
        for name in (
            "bugate_install_contract.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
            "check_bugate.py",
            "check_plan_lock.py",
            "check_role_evidence.py",
            "check_agent_role_paths.py",
            "bugate_prompt_reminder.py",
            "memory_bus.py",
        ):
            _write(self.root / "scripts" / name, f"# synthetic {name}\n".encode())
        _write(self.root / "scripts/bugate_update.py", updater_source)
        _write(
            self.root / "bin/bugate-update",
            contract.BUGATE_UPDATE_WRAPPER_BYTES,
            0o755,
        )
        _write(self.root / "bin/bugate-role", b"#!/bin/sh\nexit 0\n", 0o755)
        for skill in contract.SKILL_NAMES:
            _write(
                self.root / ".shared/skills" / skill / "SKILL.md",
                f"# synthetic {skill}\n".encode(),
            )
        for name in contract.CODEX_GATE_AGENT_NAMES:
            _write(
                self.root / contract.CODEX_GATE_AGENT_SOURCE_DIR / name,
                f'name = "synthetic-{name}"\n'.encode(),
            )
        for relative in contract.VENDOR_SINGLE_FILES:
            _write(self.root / relative, b"# synthetic setup\n")
        plugin = json.dumps(
            {"name": "bugate", "version": self.version}, sort_keys=True
        ).encode() + b"\n"
        _write(self.root / ".codex-plugin/plugin.json", plugin)
        _write(self.root / ".claude-plugin/plugin.json", plugin)
        _write(self.root / "README.md", b"# Synthetic release\n")
        _write(self.root / "bugate.config.yaml", b"bugate:\n  version: '0.1'\n")
        (self.root / "CLAUDE.md").symlink_to("README.md")

    def payload(self, item: Mapping[str, Any]) -> bytes:
        path = item["path"]
        if path == contract.RELEASE_MANIFEST_PATH:
            return self.manifest_bytes
        if item["type"] == "symlink":
            return item["target"].encode("utf-8")
        if item["type"] == "directory":
            return b""
        return (self.root / path).read_bytes()

    def write_tar(
        self,
        path: Path,
        *,
        overrides: Mapping[str, bytes] | None = None,
        omit: set[str] | None = None,
        prefix: str | None = None,
    ) -> None:
        overrides = dict(overrides or {})
        omitted = set(omit or set())
        archive_prefix = prefix or self.prefix
        with tarfile.open(path, "w:gz") as archive:
            for item in self.manifest["archive_inventory"]:
                relative = item["path"]
                if relative in omitted:
                    continue
                info = tarfile.TarInfo(f"{archive_prefix}/{relative}")
                info.mode = int(item["mode"], 8)
                if item["type"] == "directory":
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                elif item["type"] == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = item["target"]
                    archive.addfile(info)
                else:
                    payload = overrides.get(relative, self.payload(item))
                    info.type = tarfile.REGTYPE
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))

    def write_zip(
        self,
        path: Path,
        *,
        overrides: Mapping[str, bytes] | None = None,
        omit: set[str] | None = None,
        prefix: str | None = None,
    ) -> None:
        overrides = dict(overrides or {})
        omitted = set(omit or set())
        archive_prefix = prefix or self.prefix
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in self.manifest["archive_inventory"]:
                relative = item["path"]
                if relative in omitted:
                    continue
                kind = item["type"]
                name = f"{archive_prefix}/{relative}" + (
                    "/" if kind == "directory" else ""
                )
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                file_type = {
                    "directory": stat.S_IFDIR,
                    "file": stat.S_IFREG,
                    "symlink": stat.S_IFLNK,
                }[kind]
                info.external_attr = (file_type | int(item["mode"], 8)) << 16
                if kind == "directory":
                    info.external_attr |= 0x10
                payload = overrides.get(relative, self.payload(item))
                archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED)


def _checksum(path: Path, *, digest: str | None = None) -> Path:
    checksum = path.with_name(f"bugate-{VERSION}.SHA256SUMS")
    value = digest or hashlib.sha256(path.read_bytes()).hexdigest()
    checksum.write_text(f"{value}  {path.name}\n", encoding="ascii")
    return checksum


def _raw_tar(path: Path, members: list[dict[str, Any]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for raw in members:
            info = tarfile.TarInfo(raw["name"])
            info.mode = raw.get("mode", 0o644)
            kind = raw.get("type", "file")
            if kind == "directory":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = raw["target"]
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = raw["target"]
                archive.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)
            else:
                payload = raw.get("data", b"x")
                info.type = tarfile.REGTYPE
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))


def _raw_zip(path: Path, members: list[dict[str, Any]]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            for raw in members:
                kind = raw.get("type", "file")
                name = raw["name"] + ("/" if kind == "directory" else "")
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                type_bits = {
                    "file": stat.S_IFREG,
                    "directory": stat.S_IFDIR,
                    "symlink": stat.S_IFLNK,
                    "fifo": stat.S_IFIFO,
                }[kind]
                mode = raw.get("mode", 0o755 if kind == "directory" else 0o644)
                if kind == "symlink":
                    mode = raw.get("mode", 0o777)
                info.external_attr = (type_bits | mode) << 16
                if kind == "directory":
                    info.external_attr |= 0x10
                payload = raw.get("data", b"")
                if kind == "symlink":
                    payload = raw["target"].encode("utf-8")
                archive.writestr(info, payload)


def _mark_zip_encrypted(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    signatures = ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8))
    for signature, flag_offset in signatures:
        start = 0
        found = False
        while True:
            index = payload.find(signature, start)
            if index < 0:
                break
            found = True
            offset = index + flag_offset
            flags = int.from_bytes(payload[offset : offset + 2], "little") | 0x1
            payload[offset : offset + 2] = flags.to_bytes(2, "little")
            start = index + len(signature)
        if not found:
            raise AssertionError(f"missing zip signature {signature!r}")
    path.write_bytes(payload)


class UpdateSourceTests(unittest.TestCase):
    def test_checksum_leaf_swap_to_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bugate-checksum-race-") as raw:
            base = Path(raw)
            checksums = base / f"{PREFIX}.SHA256SUMS"
            checksums.write_text(
                f"{'a' * 64}  {PREFIX}.tar.gz\n", encoding="ascii"
            )
            external = base / "external-checksums"
            external.write_text(
                f"{'b' * 64}  {PREFIX}.tar.gz\n", encoding="ascii"
            )
            real_open = source.os.open
            swapped = False

            def swap_before_open(path: Any, flags: int, *args: Any, **kwargs: Any):
                nonlocal swapped
                if Path(path) == checksums and not swapped:
                    swapped = True
                    checksums.unlink()
                    checksums.symlink_to(external)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(source.os, "open", side_effect=swap_before_open), self.assertRaises(
                source.ChecksumError
            ):
                source.parse_checksum_asset(checksums)
            self.assertTrue(swapped)
            self.assertEqual(
                external.read_text(encoding="ascii"),
                f"{'b' * 64}  {PREFIX}.tar.gz\n",
            )

    def test_archive_snapshot_path_replacement_cannot_change_verified_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bugate-source-snapshot-race-") as raw:
            base = Path(raw)
            release_a = SyntheticRelease(base / "release-a")
            release_b = SyntheticRelease(base / "release-b")
            _write(release_b.root / "README.md", b"# Distinct synthetic release B\n")
            release_b = SyntheticRelease.__new__(SyntheticRelease)
            # Rebuild B cleanly after changing one inventoried byte.
            release_b.root = base / "release-b"
            release_b.version = VERSION
            release_b.prefix = PREFIX
            release_b.manifest = contract.build_release_manifest(
                release_b.root,
                VERSION,
                selected_paths=_all_files(release_b.root),
                updater_minimum_version=VERSION,
            )
            release_b.manifest_bytes = contract.canonical_json_bytes(
                release_b.manifest
            )
            archive_a = base / "a" / f"bugate-{VERSION}.tar.gz"
            archive_b = base / "b" / f"bugate-{VERSION}.tar.gz"
            archive_a.parent.mkdir()
            archive_b.parent.mkdir()
            release_a.write_tar(archive_a)
            release_b.write_tar(archive_b)
            checksums = _checksum(archive_a)
            stage = base / "stage"
            stage.mkdir()
            original_copy = source._copy_snapshot

            def replace_snapshot(
                input_path: Path, destination: Path, **kwargs: Any
            ) -> tuple[str, int]:
                digest, descriptor = original_copy(
                    input_path, destination, **kwargs
                )
                destination.unlink()
                destination.write_bytes(archive_b.read_bytes())
                return digest, descriptor

            with mock.patch.object(
                source, "_copy_snapshot", side_effect=replace_snapshot
            ), self.assertRaisesRegex(
                source.StagingError, "snapshot path changed"
            ):
                source.prepare_archive(
                    archive_a, checksums, stage, expected_version=VERSION
                )
            self.assertEqual(list(stage.iterdir()), [])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="bugate-update-source-test-"
        )
        self.base = Path(self._temporary.name)
        self.core = self.base / "synthetic-core"
        self.core.mkdir()
        self.release = SyntheticRelease(self.core)
        self.target = self.base / "synthetic-target"
        self.target.mkdir()
        _write(self.target / "SUT-owned.txt", b"untouched\n")
        self.target_before = _tree_snapshot(self.target)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def stage(self, name: str = "stage") -> Path:
        path = self.base / name
        path.mkdir()
        return path

    def raw_archive(self, label: str, archive_format: str) -> Path:
        directory = self.base / label
        directory.mkdir()
        return directory / f"{PREFIX}.{archive_format}"

    def assert_target_unchanged(self) -> None:
        self.assertEqual(_tree_snapshot(self.target), self.target_before)

    def test_checksum_parser_and_verifier_accept_exact_records(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        other = "f" * 64
        checksums = self.base / f"{PREFIX}.SHA256SUMS"
        checksums.write_text(
            f"{digest}  {archive.name}\n{other}  {PREFIX}.zip\n",
            encoding="ascii",
        )

        parsed = source.parse_checksum_asset(checksums)
        self.assertEqual(parsed[archive.name], digest)
        self.assertEqual(source.verify_archive_checksum(archive, checksums), digest)

    def test_checksum_parser_rejects_malformed_records(self) -> None:
        digest = "a" * 64
        invalid = (
            b"",
            f"{digest.upper()}  {PREFIX}.zip\n".encode(),
            f"{digest} {PREFIX}.zip\n".encode(),
            f"{digest}  nested/{PREFIX}.zip\n".encode(),
            f"{digest}  {PREFIX}.zip\n\n".encode(),
            f"# {digest}  {PREFIX}.zip\n".encode(),
            b"\xff\n",
        )
        for payload in invalid:
            with self.subTest(payload=payload[:20]):
                with self.assertRaises(source.ChecksumError):
                    source.parse_checksum_bytes(payload)

    def test_checksum_parser_rejects_duplicate_case_ambiguity(self) -> None:
        digest = "a" * 64
        for second in (f"{PREFIX}.zip", f"{PREFIX.upper()}.ZIP"):
            payload = (
                f"{digest}  {PREFIX}.zip\n"
                f"{'b' * 64}  {second}\n"
            ).encode()
            with self.subTest(second=second):
                with self.assertRaises(source.ChecksumError):
                    source.parse_checksum_bytes(payload)

    def test_checksum_missing_mismatch_and_version_mismatch_leave_no_stage(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        cases = (
            ("missing", f"{'a' * 64}  {PREFIX}.zip\n", f"{PREFIX}.SHA256SUMS"),
            ("mismatch", f"{'a' * 64}  {archive.name}\n", f"{PREFIX}.SHA256SUMS"),
            (
                "version",
                f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
                "bugate-0.4.3.SHA256SUMS",
            ),
        )
        for index, (label, content, checksum_name) in enumerate(cases):
            checksums = self.base / f"{index}-{checksum_name}"
            # The checksum filename itself is normative, so use a subdirectory
            # to keep each case's basename intact.
            case_dir = self.base / f"checksum-{index}"
            case_dir.mkdir()
            checksums = case_dir / checksum_name
            checksums.write_text(content, encoding="ascii")
            stage = self.stage(f"stage-{index}")
            with self.subTest(case=label):
                with self.assertRaises(source.UpdateSourceError):
                    source.prepare_archive(archive, checksums, stage)
                self.assertEqual(list(stage.iterdir()), [])
                self.assert_target_unchanged()

    def test_prepare_archive_accepts_tar_and_zip_and_preserves_contract(self) -> None:
        for archive_format in ("tar.gz", "zip"):
            archive = self.base / f"{PREFIX}.{archive_format}"
            if archive_format == "tar.gz":
                self.release.write_tar(archive)
            else:
                self.release.write_zip(archive)
            checksums = _checksum(archive)
            stage = self.stage(f"stage-{archive_format.replace('.', '-')}")

            prepared = source.prepare_archive(
                archive, checksums, stage, expected_version=VERSION
            )

            self.assertIsInstance(prepared, source.PreparedRelease)
            self.assertEqual(prepared.source_kind, "archive")
            self.assertEqual(prepared.manifest, self.release.manifest)
            self.assertEqual(
                prepared.archive_sha256,
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            self.assertEqual(prepared.root, stage.resolve() / PREFIX)
            self.assertEqual(
                (prepared.root / contract.RELEASE_MANIFEST_PATH).read_bytes(),
                self.release.manifest_bytes,
            )
            self.assertEqual(
                stat.S_IMODE(os.lstat(prepared.root / "bin/bugate-update").st_mode),
                0o755,
            )
            self.assertTrue((prepared.root / "CLAUDE.md").is_symlink())
            self.assertEqual(os.readlink(prepared.root / "CLAUDE.md"), "README.md")
            self.assertEqual({path.name for path in stage.iterdir()}, {PREFIX})
            self.assert_target_unchanged()

    def test_staging_path_replacement_cleanup_never_deletes_replacement(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        stage = self.stage("stage-root-race")
        displaced = self.base / "displaced-owned-stage"

        def replace_stage_then_fail(*_args: Any, **_kwargs: Any) -> None:
            os.rename(stage, displaced)
            stage.mkdir()
            _write(stage / "operator-owned.txt", b"preserve\n")
            raise source.StagingError("synthetic extraction failure")

        with mock.patch.object(
            source, "_extract_tar", side_effect=replace_stage_then_fail
        ), self.assertRaisesRegex(source.StagingError, "synthetic extraction"):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertEqual(
            (stage / "operator-owned.txt").read_bytes(), b"preserve\n"
        )
        self.assertEqual(list(displaced.iterdir()), [])
        self.assert_target_unchanged()

    def test_staged_parent_symlink_swap_cannot_write_outside_pinned_root(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        stage = self.stage("stage-parent-race")
        outside = self.base / "outside-parent"
        outside.mkdir()
        _write(outside / "operator-owned.txt", b"outside-preserved\n")
        saved = self.base / "saved-scripts"
        original_parent = source._parent_directory_fd
        swapped = False

        def swap_parent(
            root_fd: int,
            relative: str,
            **kwargs: Any,
        ) -> tuple[int, str]:
            nonlocal swapped
            if relative.startswith("scripts/") and not swapped:
                swapped = True
                scripts = stage / PREFIX / "scripts"
                os.rename(scripts, saved)
                scripts.symlink_to(outside, target_is_directory=True)
            return original_parent(root_fd, relative, **kwargs)

        with mock.patch.object(
            source, "_parent_directory_fd", side_effect=swap_parent
        ), self.assertRaises(source.StagingError):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertTrue(swapped)
        self.assertEqual(
            (outside / "operator-owned.txt").read_bytes(),
            b"outside-preserved\n",
        )
        self.assertFalse((outside / "bugate_update.py").exists())
        self.assert_target_unchanged()

    def test_staged_physical_directory_exchange_is_preserved_not_deleted(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        displaced = self.base / "displaced-created-scripts"
        operator = self.base / "operator-physical-scripts"
        operator.mkdir()
        _write(operator / "operator-owned.txt", b"preserve-physical\n")
        operator_before = _tree_snapshot(operator)
        original_parent = source._parent_directory_fd
        swapped = False

        def exchange_parent(
            root_fd: int,
            relative: str,
            **kwargs: Any,
        ) -> tuple[int, str]:
            nonlocal swapped
            if relative.startswith("scripts/") and not swapped:
                swapped = True
                scripts = stage / PREFIX / "scripts"
                os.rename(scripts, displaced)
                os.rename(operator, scripts)
            return original_parent(root_fd, relative, **kwargs)

        with tempfile.TemporaryDirectory(
            prefix="stage-physical-parent-race-",
            dir=self.base,
        ) as raw_stage:
            stage = Path(raw_stage)
            with mock.patch.object(
                source, "_parent_directory_fd", side_effect=exchange_parent
            ), self.assertRaises(source.StagingError) as caught:
                source.prepare_archive(archive, _checksum(archive), stage)

            self.assertTrue(swapped)
            notes = getattr(caught.exception, "__notes__", [])
            preservation_note = next(
                note for note in notes if "were preserved at " in note
            )
            preservation_root = Path(
                preservation_note.split("were preserved at ", 1)[1].split(": ", 1)[0]
            )
            preserved = list(preservation_root.glob("**/operator-owned.txt"))
            self.assertEqual(len(preserved), 1)
            preserved_root = preserved[0].parent
            self.assertFalse((stage / PREFIX / "scripts").exists())

        # The caller's later TemporaryDirectory cleanup must not erase the
        # raced physical tree that source cleanup preserved beside staging.
        self.assertTrue(preserved_root.is_dir())
        self.assertEqual(_tree_snapshot(preserved_root), operator_before)
        preserved[0].unlink()
        preserved_root.rmdir()
        preservation_root.rmdir()
        self.assert_target_unchanged()

    def test_staged_release_root_exchange_is_rejected_before_return(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        stage = self.stage("release-root-exchange")
        displaced = self.base / "displaced-verified-release"
        operator = self.base / "operator-release-root"
        operator.mkdir()
        _write(operator / "operator-owned.txt", b"keep\n")
        operator_before = _tree_snapshot(operator)
        original = source._verify_unpacked
        swapped = False

        def exchange(root: Path, *, expected_version: str | None):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(stage / PREFIX, displaced)
                (stage / PREFIX).symlink_to(operator, target_is_directory=True)
            return original(root, expected_version=expected_version)

        with mock.patch.object(
            source, "_verify_unpacked", side_effect=exchange
        ), self.assertRaises(source.StagingError):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertTrue(swapped)
        self.assertEqual(_tree_snapshot(operator), operator_before)
        self.assertFalse((stage / PREFIX).exists())
        self.assert_target_unchanged()

    def test_success_finalization_never_unlinks_exchanged_snapshot(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        displaced = self.base / "displaced-source-snapshot"
        operator_bytes = b"operator-owned-snapshot\n"
        original_retire = source._retire_or_preserve_at
        swapped = False

        with tempfile.TemporaryDirectory(
            prefix="stage-snapshot-finalize-race-",
            dir=self.base,
        ) as raw_stage:
            stage = Path(raw_stage)

            def exchange_snapshot(
                parent_fd: int,
                name: str,
                **kwargs: Any,
            ) -> bool:
                nonlocal swapped
                if name == ".bugate-source.tar.gz" and not swapped:
                    swapped = True
                    os.rename(stage / name, displaced)
                    _write(stage / name, operator_bytes, 0o600)
                return original_retire(parent_fd, name, **kwargs)

            with mock.patch.object(
                source,
                "_retire_or_preserve_at",
                side_effect=exchange_snapshot,
            ), self.assertRaisesRegex(
                source.StagingError,
                "snapshot changed during finalization",
            ) as caught:
                source.prepare_archive(archive, _checksum(archive), stage)

            self.assertTrue(swapped)
            preservation_root = Path(
                str(caught.exception).rsplit("preserved at ", 1)[1]
            )
            preserved = list(preservation_root.iterdir())
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0].read_bytes(), operator_bytes)

        self.assertTrue(preserved[0].is_file())
        self.assertEqual(preserved[0].read_bytes(), operator_bytes)
        preserved[0].unlink()
        preservation_root.rmdir()
        self.assertTrue(displaced.is_file())
        self.assert_target_unchanged()

    def test_release_root_exchange_after_snapshot_retire_is_rejected(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        displaced = self.base / "displaced-final-release-root"
        operator = self.base / "operator-final-release-root"
        operator.mkdir()
        _write(operator / "operator-owned.txt", b"preserve-final-root\n")
        operator_before = _tree_snapshot(operator)
        original_retire = source._retire_or_preserve_at
        swapped = False

        with tempfile.TemporaryDirectory(
            prefix="stage-final-root-race-",
            dir=self.base,
        ) as raw_stage:
            stage = Path(raw_stage)

            def exchange_after_snapshot(
                parent_fd: int,
                name: str,
                **kwargs: Any,
            ) -> bool:
                nonlocal swapped
                retired = original_retire(parent_fd, name, **kwargs)
                if name == ".bugate-source.tar.gz" and retired and not swapped:
                    swapped = True
                    os.rename(stage / PREFIX, displaced)
                    os.rename(operator, stage / PREFIX)
                return retired

            with mock.patch.object(
                source,
                "_retire_or_preserve_at",
                side_effect=exchange_after_snapshot,
            ), self.assertRaisesRegex(
                source.StagingError,
                "release root path changed",
            ) as caught:
                source.prepare_archive(archive, _checksum(archive), stage)

            notes = getattr(caught.exception, "__notes__", [])
            preservation_note = next(
                note for note in notes if "were preserved at " in note
            )
            preservation_root = Path(
                preservation_note.split("were preserved at ", 1)[1].split(": ", 1)[0]
            )
            preserved = list(preservation_root.glob("**/operator-owned.txt"))
            self.assertEqual(len(preserved), 1)
            preserved_tree = preserved[0].parent

        self.assertTrue(swapped)
        self.assertEqual(_tree_snapshot(preserved_tree), operator_before)
        preserved[0].unlink()
        preserved_tree.rmdir()
        preservation_root.rmdir()
        self.assert_target_unchanged()

    def test_release_leaf_exchange_after_snapshot_retire_is_rejected(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        displaced = self.base / "displaced-final-readme"
        operator_bytes = b"operator-final-readme\n"
        original_retire = source._retire_or_preserve_at
        swapped = False

        with tempfile.TemporaryDirectory(
            prefix="stage-final-leaf-race-",
            dir=self.base,
        ) as raw_stage:
            stage = Path(raw_stage)

            def exchange_after_snapshot(
                parent_fd: int,
                name: str,
                **kwargs: Any,
            ) -> bool:
                nonlocal swapped
                retired = original_retire(parent_fd, name, **kwargs)
                if name == ".bugate-source.tar.gz" and retired and not swapped:
                    swapped = True
                    readme = stage / PREFIX / "README.md"
                    os.rename(readme, displaced)
                    _write(readme, operator_bytes)
                return retired

            with mock.patch.object(
                source,
                "_retire_or_preserve_at",
                side_effect=exchange_after_snapshot,
            ), self.assertRaises(source.UpdateSourceError) as caught:
                source.prepare_archive(archive, _checksum(archive), stage)

            notes = getattr(caught.exception, "__notes__", [])
            preservation_note = next(
                note for note in notes if "were preserved at " in note
            )
            preservation_root = Path(
                preservation_note.split("were preserved at ", 1)[1].split(": ", 1)[0]
            )
            preserved = list(preservation_root.iterdir())
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0].read_bytes(), operator_bytes)

        self.assertTrue(swapped)
        self.assertEqual(preserved[0].read_bytes(), operator_bytes)
        preserved[0].unlink()
        preservation_root.rmdir()
        self.assert_target_unchanged()

    def test_prepare_unpacked_is_read_only_and_reports_no_archive_digest(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        prepared_archive = source.prepare_archive(
            archive, _checksum(archive), self.stage("archive-stage")
        )
        before = _tree_snapshot(prepared_archive.root)

        prepared = source.prepare_unpacked(
            prepared_archive.root, expected_version=VERSION
        )

        self.assertEqual(prepared.source_kind, "unpacked")
        self.assertIsNone(prepared.archive_sha256)
        self.assertEqual(prepared.manifest, self.release.manifest)
        self.assertEqual(_tree_snapshot(prepared.root), before)
        self.assert_target_unchanged()

    def test_requested_archive_prefix_and_filename_versions_must_agree(self) -> None:
        normal = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(normal)
        with self.assertRaises(source.ManifestError):
            source.inspect_archive(normal, expected_version="0.4.3")

        wrong_prefix = self.base / f"{PREFIX}.zip"
        self.release.write_zip(wrong_prefix, prefix="bugate-0.4.3")
        with self.assertRaises(source.ArchiveSafetyError):
            source.inspect_archive(wrong_prefix)

        renamed = self.base / "bugate-0.4.3.tar.gz"
        self.release.write_tar(renamed)
        with self.assertRaises(source.ArchiveSafetyError):
            source.inspect_archive(renamed)

    def test_plugin_version_disagreement_is_rejected_before_extraction(self) -> None:
        archive = self.base / f"{PREFIX}.zip"
        wrong_plugin = json.dumps(
            {"name": "bugate", "version": "0.4.3"}
        ).encode() + b"\n"
        self.release.write_zip(
            archive, overrides={".codex-plugin/plugin.json": wrong_plugin}
        )
        stage = self.stage()
        with self.assertRaisesRegex(source.ManifestError, "plugin/release"):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertEqual(list(stage.iterdir()), [])
        self.assert_target_unchanged()

    def test_updater_literal_must_match_release_without_target_writes(self) -> None:
        cases = (
            ("missing", b"# no updater version\n", "exactly one"),
            (
                "dynamic",
                b'RELEASE_VERSION = "0.4.2"\nUPDATER_VERSION = RELEASE_VERSION\n',
                "string literal",
            ),
            ("mismatch", b'UPDATER_VERSION = "0.4.3"\n', "updater/release"),
        )
        for index, (label, updater_source, message) in enumerate(cases):
            core = self.base / f"updater-{index}-{label}"
            core.mkdir()
            release = SyntheticRelease(core, updater_source=updater_source)
            directory = self.base / f"updater-archive-{index}"
            directory.mkdir()
            archive = directory / f"{PREFIX}.tar.gz"
            release.write_tar(archive)
            stage = self.stage(f"updater-stage-{index}")
            with self.subTest(case=label), self.assertRaisesRegex(
                source.ManifestError, message
            ):
                source.prepare_archive(archive, _checksum(archive), stage)
            self.assertEqual(list(stage.iterdir()), [])
            self.assert_target_unchanged()

    def test_updater_must_satisfy_manifest_minimum_without_target_writes(self) -> None:
        manifest = copy.deepcopy(self.release.manifest)
        manifest["updater_minimum_version"] = "0.4.3"
        manifest = contract.seal_document(manifest)
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(
            archive,
            overrides={
                contract.RELEASE_MANIFEST_PATH: contract.canonical_json_bytes(manifest)
            },
        )
        stage = self.stage("updater-minimum-stage")
        with self.assertRaisesRegex(source.ManifestError, "incompatible"):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertEqual(list(stage.iterdir()), [])
        self.assert_target_unchanged()

    def test_release_manifest_version_must_match_archive_filename(self) -> None:
        mismatched = copy.deepcopy(self.release.manifest)
        mismatched["bugate_version"] = "0.4.3"
        mismatched["archive_prefix"] = "bugate-0.4.3"
        mismatched["updater_minimum_version"] = "0.4.3"
        mismatched = contract.seal_document(mismatched)
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(
            archive,
            overrides={
                contract.RELEASE_MANIFEST_PATH: contract.canonical_json_bytes(
                    mismatched
                )
            },
        )
        stage = self.stage()
        with self.assertRaises(source.ManifestError):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertEqual(list(stage.iterdir()), [])
        self.assert_target_unchanged()

    def test_manifest_must_be_canonical_unique_key_json(self) -> None:
        noncanonical = json.dumps(self.release.manifest, indent=2).encode() + b"\n"
        duplicate = b'{"schema_version":1,"schema_version":1}\n'
        for index, payload in enumerate((noncanonical, duplicate)):
            archive = self.base / f"{PREFIX}.zip"
            self.release.write_zip(
                archive, overrides={contract.RELEASE_MANIFEST_PATH: payload}
            )
            stage = self.stage(f"manifest-stage-{index}")
            with self.subTest(case=index):
                with self.assertRaises(source.ManifestError):
                    source.prepare_archive(archive, _checksum(archive), stage)
                self.assertEqual(list(stage.iterdir()), [])

    def test_full_archive_inventory_bytes_are_verified(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive, overrides={"README.md": b"tampered\n"})
        stage = self.stage()
        with self.assertRaisesRegex(source.ManifestError, "inventory differs"):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertEqual(list(stage.iterdir()), [])
        self.assert_target_unchanged()

    def test_missing_release_manifest_is_rejected(self) -> None:
        archive = self.base / f"{PREFIX}.zip"
        self.release.write_zip(archive, omit={contract.RELEASE_MANIFEST_PATH})
        stage = self.stage()
        with self.assertRaisesRegex(source.ManifestError, "metadata is missing"):
            source.prepare_archive(archive, _checksum(archive), stage)
        self.assertEqual(list(stage.iterdir()), [])

    def test_tar_absolute_parent_and_empty_components_are_rejected(self) -> None:
        unsafe_names = (
            "/absolute",
            f"{PREFIX}/../escape",
            f"{PREFIX}/a//b",
            f"{PREFIX}/./dot",
            f"{PREFIX}/regular-file/",
        )
        for index, name in enumerate(unsafe_names):
            archive = self.raw_archive(f"unsafe-tar-{index}", "tar.gz")
            _raw_tar(archive, [{"name": name}])
            with self.subTest(name=name), self.assertRaises(
                source.ArchiveSafetyError
            ):
                source.inspect_archive(archive)

    def test_zip_absolute_parent_and_empty_components_are_rejected(self) -> None:
        unsafe_names = (
            "/absolute",
            f"{PREFIX}/../escape",
            f"{PREFIX}/a//b",
            f"{PREFIX}/./dot",
        )
        for index, name in enumerate(unsafe_names):
            archive = self.raw_archive(f"unsafe-zip-{index}", "zip")
            _raw_zip(archive, [{"name": name}])
            with self.subTest(name=name), self.assertRaises(
                source.ArchiveSafetyError
            ):
                source.inspect_archive(archive)

    def test_tar_and_zip_duplicate_case_and_ancestor_conflicts_are_rejected(self) -> None:
        cases = (
            (
                "duplicate",
                [
                    {"name": f"{PREFIX}/item"},
                    {"name": f"{PREFIX}/item"},
                ],
            ),
            (
                "case",
                [
                    {"name": f"{PREFIX}/Item"},
                    {"name": f"{PREFIX}/item"},
                ],
            ),
            (
                "ancestor",
                [
                    {"name": f"{PREFIX}/owned"},
                    {"name": f"{PREFIX}/owned/child"},
                ],
            ),
        )
        for archive_format in ("tar.gz", "zip"):
            for index, (label, members) in enumerate(cases):
                archive = self.raw_archive(
                    f"conflict-{archive_format.replace('.', '-')}-{index}",
                    archive_format,
                )
                if archive_format == "tar.gz":
                    _raw_tar(archive, members)
                else:
                    _raw_zip(archive, members)
                with self.subTest(format=archive_format, case=label), self.assertRaises(
                    source.ArchiveSafetyError
                ):
                    source.inspect_archive(archive)

    def test_tar_and_zip_symlink_escapes_are_rejected(self) -> None:
        for archive_format in ("tar.gz", "zip"):
            archive = self.raw_archive(
                f"symlink-escape-{archive_format.replace('.', '-')}",
                archive_format,
            )
            members = [
                {
                    "name": f"{PREFIX}/links/escape",
                    "type": "symlink",
                    "target": "../../../outside",
                    "mode": 0o777,
                }
            ]
            if archive_format == "tar.gz":
                _raw_tar(archive, members)
            else:
                _raw_zip(archive, members)
            with self.subTest(format=archive_format), self.assertRaises(
                source.ArchiveSafetyError
            ):
                source.inspect_archive(archive)

    def test_all_tar_hardlinks_are_rejected_including_escape(self) -> None:
        for index, target in enumerate(
            (f"{PREFIX}/README.md", "../../outside", "/absolute")
        ):
            archive = self.raw_archive(f"hardlink-{index}", "tar.gz")
            _raw_tar(
                archive,
                [
                    {
                        "name": f"{PREFIX}/hardlink",
                        "type": "hardlink",
                        "target": target,
                    }
                ],
            )
            with self.subTest(target=target), self.assertRaises(
                source.ArchiveSafetyError
            ):
                source.inspect_archive(archive)

    def test_unknown_zip_entry_type_is_rejected(self) -> None:
        archive = self.raw_archive("unknown-zip", "zip")
        _raw_zip(
            archive,
            [{"name": f"{PREFIX}/pipe", "type": "fifo", "mode": 0o644}],
        )
        with self.assertRaises(source.ArchiveSafetyError):
            source.inspect_archive(archive)

    def test_unknown_tar_entry_type_is_rejected(self) -> None:
        archive = self.raw_archive("unknown-tar", "tar.gz")
        _raw_tar(
            archive,
            [{"name": f"{PREFIX}/pipe", "type": "fifo", "mode": 0o644}],
        )
        with self.assertRaises(source.ArchiveSafetyError):
            source.inspect_archive(archive)

    def test_encrypted_zip_entry_is_rejected_before_payload_read(self) -> None:
        archive = self.raw_archive("encrypted-zip", "zip")
        _raw_zip(archive, [{"name": f"{PREFIX}/secret", "data": b"ciphertext"}])
        _mark_zip_encrypted(archive)
        with self.assertRaisesRegex(source.ArchiveSafetyError, "encrypted"):
            source.inspect_archive(archive)

    def test_entry_count_limit_is_enforced_before_metadata_use(self) -> None:
        archive = self.raw_archive("limited-tar", "tar.gz")
        _raw_tar(
            archive,
            [
                {"name": f"{PREFIX}/one"},
                {"name": f"{PREFIX}/two"},
            ],
        )
        with mock.patch.object(source, "MAX_ARCHIVE_ENTRIES", 1):
            with self.assertRaises(source.ArchiveSafetyError):
                source.inspect_archive(archive)

    def test_nonempty_or_symlink_staging_directory_is_rejected_without_writes(self) -> None:
        archive = self.base / f"{PREFIX}.tar.gz"
        self.release.write_tar(archive)
        checksums = _checksum(archive)
        nonempty = self.stage("nonempty")
        _write(nonempty / "operator-owned.txt", b"keep\n")
        before = _tree_snapshot(nonempty)
        with self.assertRaises(source.StagingError):
            source.prepare_archive(archive, checksums, nonempty)
        self.assertEqual(_tree_snapshot(nonempty), before)

        real = self.stage("real-stage")
        link = self.base / "linked-stage"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(source.StagingError):
            source.prepare_archive(archive, checksums, link)
        self.assertEqual(list(real.iterdir()), [])
        self.assert_target_unchanged()

    def test_symlink_archive_input_is_rejected_before_staging(self) -> None:
        real_dir = self.base / "real-archive"
        link_dir = self.base / "linked-archive"
        real_dir.mkdir()
        link_dir.mkdir()
        real_archive = real_dir / f"{PREFIX}.tar.gz"
        self.release.write_tar(real_archive)
        linked_archive = link_dir / real_archive.name
        linked_archive.symlink_to(real_archive)
        checksums = link_dir / f"{PREFIX}.SHA256SUMS"
        checksums.write_text(
            f"{hashlib.sha256(real_archive.read_bytes()).hexdigest()}  {linked_archive.name}\n",
            encoding="ascii",
        )
        stage = self.stage()
        with self.assertRaises(source.StagingError):
            source.prepare_archive(linked_archive, checksums, stage)
        self.assertEqual(list(stage.iterdir()), [])
        self.assert_target_unchanged()

    def test_prepare_unpacked_rejects_extra_and_unsafe_entries_without_repair(self) -> None:
        archive = self.base / f"{PREFIX}.zip"
        self.release.write_zip(archive)
        prepared = source.prepare_archive(
            archive, _checksum(archive), self.stage("unpacked-stage")
        )
        _write(prepared.root / "unknown.txt", b"unknown\n")
        before = _tree_snapshot(prepared.root)
        with self.assertRaises(source.ManifestError):
            source.prepare_unpacked(prepared.root)
        self.assertEqual(_tree_snapshot(prepared.root), before)
        self.assert_target_unchanged()


if __name__ == "__main__":
    unittest.main()
