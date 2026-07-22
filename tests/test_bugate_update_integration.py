#!/usr/bin/env python3
"""Subprocess E2E for a synthetic legacy imported-mode update transaction."""
from __future__ import annotations

import io
import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402
import bugate_legacy_manifest as legacy  # noqa: E402
import bugate_update_engine as engine  # noqa: E402
import bugate_update_source as source  # noqa: E402


VERSION = "0.4.2"
VENDOR_DIR = ".bugate"
LEGACY_TAG = "v0.3.2"
LEGACY_MATRIX_TAGS = (
    "v0.3.0",
    "v0.3.1",
    "v0.3.2",
    "v0.3.4",
    "v0.3.5",
    "v0.4.0",
    "v0.4.1",
)
EXPECTED_OBSOLETE_TARGETS = {
    "v0.3.0": (),
    "v0.3.1": (),
    "v0.3.2": (".bugate/docs/IMPORT-FIELD-GUIDE.md",),
    "v0.3.4": (),
    "v0.3.5": (),
    "v0.4.0": (),
    "v0.4.1": (),
}
_HOOK_ID_RE = re.compile(r"BUGATE_HOOK_ID='([^']+)'")
_NOISE_NAMES = {"__pycache__", ".DS_Store"}


def _ignored_name(name: str) -> bool:
    return name in _NOISE_NAMES or name.endswith(".pyc")


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if _ignored_name(name)}


def _write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


def _copy_entry(source_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source_path.is_symlink():
        destination.symlink_to(os.readlink(source_path))
    elif source_path.is_dir():
        shutil.copytree(
            source_path,
            destination,
            symlinks=True,
            copy_function=shutil.copy2,
            ignore=_copy_ignore,
        )
    else:
        shutil.copy2(source_path, destination, follow_symlinks=False)


def _physical_paths(root: Path) -> list[str]:
    paths: list[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(dirnames):
            if _ignored_name(name):
                continue
            path = current_path / name
            paths.append(path.relative_to(root).as_posix())
            if not path.is_symlink():
                kept_directories.append(name)
        dirnames[:] = kept_directories
        for name in sorted(filenames):
            if not _ignored_name(name):
                paths.append((current_path / name).relative_to(root).as_posix())
    return sorted(paths)


def _path_image(path: Path) -> tuple[str, bytes | str, int]:
    if not (path.exists() or path.is_symlink()):
        return ("absent", b"", 0)
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        return ("symlink", os.readlink(path), 0o777)
    if stat.S_ISDIR(metadata.st_mode):
        return ("directory", b"", stat.S_IMODE(metadata.st_mode))
    return ("file", path.read_bytes(), stat.S_IMODE(metadata.st_mode))


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    snapshot: dict[str, tuple[str, bytes | str, int]] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(dirnames):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = _path_image(path)
            if not path.is_symlink():
                kept_directories.append(name)
        dirnames[:] = kept_directories
        for name in sorted(filenames):
            path = current_path / name
            snapshot[path.relative_to(root).as_posix()] = _path_image(path)
    return snapshot


def _projection_snapshot(
    project: Path, projection: Iterable[Mapping[str, Any]]
) -> dict[str, tuple[str, bytes | str, int]]:
    targets = sorted({str(item["target_path"]) for item in projection})
    return {relative: _path_image(project / relative) for relative in targets}


def _selected_snapshot(
    project: Path, relative_paths: Iterable[str]
) -> dict[str, tuple[str, bytes | str, int] | dict[str, tuple[str, bytes | str, int]]]:
    snapshot: dict[
        str, tuple[str, bytes | str, int] | dict[str, tuple[str, bytes | str, int]]
    ] = {}
    for relative in relative_paths:
        path = project / relative
        if path.is_dir() and not path.is_symlink():
            snapshot[relative] = _tree_snapshot(path)
        else:
            snapshot[relative] = _path_image(path)
    return snapshot


def _outside_marked_block(data: bytes, item: Mapping[str, Any]) -> bytes:
    text = data.decode("utf-8")
    start = text.index(str(item["begin"]))
    finish = text.index(str(item["end"]), start) + len(str(item["end"]))
    if finish < len(text) and text[finish] == "\n":
        finish += 1
    return (text[:start] + text[finish:]).encode("utf-8")


def _hook_identities(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_HOOK_ID_RE.findall(value))
    if isinstance(value, Mapping):
        identities: set[str] = set()
        for child in value.values():
            identities.update(_hook_identities(child))
        return identities
    if isinstance(value, (list, tuple)):
        identities = set()
        for child in value:
            identities.update(_hook_identities(child))
        return identities
    return set()


class _MemoryTrapHandler(http.server.BaseHTTPRequestHandler):
    """Record any updater attempt to contact a Memory service."""

    def _reject(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.requests.append((self.command, self.path, body))  # type: ignore[attr-defined]
        payload = b'{"error":"updater must not contact Memory"}\n'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _reject
    do_POST = _reject
    do_PUT = _reject
    do_DELETE = _reject
    do_PATCH = _reject

    def log_message(self, _format: str, *args: object) -> None:
        del args


class ImportedUpdateIntegrationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="bugate-update-integration-"
        )
        self.base = Path(self.temporary.name)
        self.release_root = self.base / f"bugate-{VERSION}"
        self.project = self.base / "synthetic-imported-repo"
        self.home = self.base / "home"
        self.release_root.mkdir()
        self.project.mkdir()
        self.home.mkdir()
        self.memory_requests: list[tuple[str, str, bytes]] = []
        self.memory_server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _MemoryTrapHandler
        )
        self.memory_server.requests = self.memory_requests  # type: ignore[attr-defined]
        self.memory_thread = threading.Thread(
            target=self.memory_server.serve_forever,
            name="bugate-updater-memory-trap",
            daemon=True,
        )
        self.memory_thread.start()
        host, port = self.memory_server.server_address
        self.memory_url = f"http://{host}:{port}"
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.memory_url}/trap-armed", timeout=2)
        self.assertEqual(raised.exception.code, 503)
        self.assertEqual(self.memory_requests, [("GET", "/trap-armed", b"")])
        self.memory_requests.clear()

    def tearDown(self) -> None:
        self.memory_server.shutdown()
        self.memory_server.server_close()
        self.memory_thread.join(timeout=5)
        self.temporary.cleanup()

    def _build_release(self) -> tuple[dict[str, Any], dict[str, Any]]:
        for relative in contract.VENDOR_TREE_ROOTS:
            _copy_entry(ROOT / relative, self.release_root / relative)
        for relative in contract.VENDOR_SINGLE_FILES:
            _copy_entry(ROOT / relative, self.release_root / relative)

        for relative in (
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
        ):
            document = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            document["version"] = VERSION
            _write(
                self.release_root / relative,
                (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
            )
        _copy_entry(ROOT / "bugate.config.yaml", self.release_root / "bugate.config.yaml")

        overlays = legacy.generate_all_legacy_manifests(ROOT)
        self.assertEqual(
            set(overlays),
            {
                f"{contract.LEGACY_MANIFEST_DIR}/{tag}.json"
                for tag in contract.SUPPORTED_LEGACY_TAGS
            },
        )
        manifest = contract.build_release_manifest(
            self.release_root,
            VERSION,
            selected_paths=_physical_paths(self.release_root),
            overlay_files=overlays,
            updater_minimum_version=VERSION,
        )
        for relative, payload in overlays.items():
            _write(self.release_root / relative, payload)
        _write(
            self.release_root / contract.RELEASE_MANIFEST_PATH,
            contract.canonical_json_bytes(manifest),
        )
        prepared = source.prepare_unpacked(self.release_root, VERSION)
        self.assertEqual(prepared.manifest, manifest)
        self.assertEqual(prepared.source_kind, "unpacked")
        self.assertIsNone(prepared.archive_sha256)
        legacy_manifest = json.loads(
            overlays[f"{contract.LEGACY_MANIFEST_DIR}/{LEGACY_TAG}.json"]
        )
        return manifest, legacy_manifest

    def _load_legacy_manifest(self, tag: str) -> dict[str, Any]:
        path = self.release_root / contract.LEGACY_MANIFEST_DIR / f"{tag}.json"
        document = json.loads(path.read_bytes())
        self.assertIsInstance(document, dict)
        self.assertEqual(document["source_tag"], tag)
        self.assertEqual(document["bugate_version"], tag.removeprefix("v"))
        return document

    def _materialize_legacy(
        self, manifest: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        source_tag = str(manifest["source_tag"])
        self.assertIn(source_tag, LEGACY_MATRIX_TAGS)
        self.assertEqual(manifest["bugate_version"], source_tag.removeprefix("v"))
        archived = subprocess.run(
            ["git", "archive", "--format=tar", source_tag],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            archived.returncode,
            0,
            archived.stderr.decode("utf-8", "replace"),
        )
        projection = engine.render_legacy_projection(manifest, VENDOR_DIR)
        with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
            members = {
                (
                    member.name[:-1]
                    if member.isdir() and member.name.endswith("/")
                    else member.name
                ): member
                for member in archive.getmembers()
            }
            for item in sorted(
                projection,
                key=lambda value: (
                    value["scope"] not in {"vendor", "workspace"},
                    value["type"] != "directory",
                    str(value["target_path"]).count("/"),
                    str(value["target_path"]),
                ),
            ):
                if item["scope"] not in {"vendor", "workspace"}:
                    continue
                target = self.project / item["target_path"]
                source_path = str(item["source_path"])
                self.assertIn(source_path, members)
                if item["type"] == "directory":
                    target.mkdir(parents=True, exist_ok=True)
                    os.chmod(target, int(item["mode"], 8))
                elif item["type"] == "symlink":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(item["target"])
                else:
                    stream = archive.extractfile(members[source_path])
                    self.assertIsNotNone(stream)
                    assert stream is not None
                    payload = stream.read()
                    self.assertEqual(contract.sha256_bytes(payload), item["sha256"])
                    _write(target, payload, int(item["mode"], 8))

        hook_expectations: dict[str, dict[str, Any]] = {}
        hook_targets = sorted(
            {
                item["target_path"]
                for item in projection
                if item["scope"] == "shared_json_fragment"
            }
        )
        for target_path in hook_targets:
            items = [
                item
                for item in projection
                if item["scope"] == "shared_json_fragment"
                and item["target_path"] == target_path
            ]
            sut_entry = {
                "matcher": "synthetic-owned",
                "hooks": [
                    {"type": "command", "command": "./bin/synthetic-hook --check"}
                ],
            }
            document: dict[str, Any] = {
                "synthetic_owned": {"preserve": True},
                "hooks": {},
            }
            for item in items:
                document["hooks"].setdefault(item["event"], []).append(item["value"])
            document["hooks"].setdefault("PreToolUse", []).insert(0, sut_entry)
            raw = (json.dumps(document, ensure_ascii=False, indent=4) + "\n").encode(
                "utf-8"
            )
            raw = raw.replace(
                b'"matcher": "synthetic-owned"',
                b'"matcher" : "synthetic-owned"',
            ).replace(
                b'"synthetic_owned": {',
                b'"synthetic_owned" : {',
            )
            _write(self.project / target_path, raw)
            hook_expectations[target_path] = {
                "sut_entry": sut_entry,
                "format_markers": (
                    b'"matcher" : "synthetic-owned"',
                    b'"synthetic_owned" : {',
                ),
                "top_level": document["synthetic_owned"],
            }

        block = next(
            item for item in projection if item["scope"] == "marked_text_block"
        )
        _write(
            self.project / block["target_path"],
            (
                "# synthetic owner prefix\n/synthetic-cache/\n\n"
                + block["content"]
                + "\n# synthetic owner suffix\n"
            ).encode("utf-8"),
        )
        return projection, hook_expectations

    def _populate_sut_owned(self) -> tuple[str, ...]:
        _write(
            self.project / "bugate.config.yaml",
            b"bugate:\n  version: '0.1'\nprofile: bugate.profile.yaml\n",
        )
        _write(
            self.project / "bugate.profile.yaml",
            (
                b"role_governance:\n  mode: off\n"
                b"memory:\n  namespace: project:synthetic-updater-e2e\n"
            ),
        )
        _write(
            self.project / "docs/usecases/SYN-001/requirement.md",
            b"# Synthetic requirement\n",
        )
        _write(
            self.project / "docs/usecases/SYN-001/00_role_evidence/receipt.json",
            b'{"synthetic":"preserve"}\n',
        )
        _write(
            self.project / "00_role_evidence/root-receipt.json",
            b'{"synthetic":"preserve-root"}\n',
        )
        _write(
            self.project / "tests/test_synthetic_sut.py",
            b"def test_synthetic_sut():\n    assert True\n",
        )
        _write(
            self.project / "bin/bugate-auto",
            b"#!/bin/sh\nexit 0\n",
            0o755,
        )
        _write(self.project / "AGENTS.md", b"# Synthetic operator rules\n")
        (self.project / "CLAUDE.md").symlink_to("AGENTS.md")
        _write(
            self.project / ".memory_bus/state.json",
            b'{"namespace":"project:synthetic-updater-e2e","records":["keep"]}\n',
        )
        _write(self.project / "synthetic-owned/dirty.txt", b"clean\n")
        return (
            "bugate.config.yaml",
            "bugate.profile.yaml",
            "docs/usecases",
            "00_role_evidence",
            "tests/test_synthetic_sut.py",
            "bin/bugate-auto",
            "AGENTS.md",
            "CLAUDE.md",
            ".memory_bus",
            "synthetic-owned/dirty.txt",
        )

    def _make_dirty_git_repo(self) -> None:
        commands = (
            ["git", "init", "-q"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                "user.name=Synthetic Tester",
                "-c",
                "user.email=synthetic@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "synthetic legacy baseline",
            ],
        )
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=self.project,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        _write(self.project / "synthetic-owned/dirty.txt", b"locally dirty\n")
        status = subprocess.run(
            ["git", "status", "--short", "--", "synthetic-owned/dirty.txt"],
            cwd=self.project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "M synthetic-owned/dirty.txt")

    def _run_cli(
        self,
        command: str,
        *arguments: str,
        expected_returncode: int = 0,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["HOME"] = str(self.home)
        for name in (
            "BUGATE_PROJECT_ROOT",
            "BUGATE_PROFILE",
            "MEMORY_BUS_PROJECT_TAG",
            "BUGATE_AGENT_ROLE",
            "BUGATE_ROLE_SESSION",
        ):
            environment.pop(name, None)
        environment["MEMORY_BUS_URL"] = self.memory_url
        environment["MEMORY_BUS_PROJECT_TAG"] = "project:updater-memory-trap"
        environment["MCP_MEMORY_BASE_DIR"] = str(self.base / "memory-home-trap")
        environment["BUGATE_MEMORY_HOME"] = str(self.base / "memory-home-trap")
        completed = subprocess.run(
            [
                sys.executable,
                str(self.release_root / "scripts/bugate_update.py"),
                command,
                str(self.project),
                "--vendor-dir",
                VENDOR_DIR,
                *arguments,
                "--json",
            ],
            cwd=self.project,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected_returncode,
            f"command={command}\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"command={command} did not emit one JSON document: "
                f"{exc}\nstdout={completed.stdout}\nstderr={completed.stderr}"
            )
        self.assertIsInstance(payload, dict)
        return payload

    def _run_installed_cli(self, command: str) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["HOME"] = str(self.home)
        for name in (
            "PYTHONPATH",
            "BUGATE_PROJECT_ROOT",
            "BUGATE_PROFILE",
            "MEMORY_BUS_PROJECT_TAG",
            "BUGATE_AGENT_ROLE",
            "BUGATE_ROLE_SESSION",
        ):
            environment.pop(name, None)
        environment["MEMORY_BUS_URL"] = self.memory_url
        environment["MEMORY_BUS_PROJECT_TAG"] = "project:updater-memory-trap"
        environment["MCP_MEMORY_BASE_DIR"] = str(self.base / "memory-home-trap")
        environment["BUGATE_MEMORY_HOME"] = str(self.base / "memory-home-trap")
        completed = subprocess.run(
            [
                str(self.project / VENDOR_DIR / "bin/bugate-update"),
                command,
                "--json",
            ],
            cwd=self.project,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"installed command={command}\nstdout={completed.stdout}\n"
            f"stderr={completed.stderr}",
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"installed command={command} did not emit JSON: {exc}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        self.assertIsInstance(payload, dict)
        return payload

    def _assert_hook_ownership_preserved(
        self, expectations: Mapping[str, Mapping[str, Any]]
    ) -> None:
        for target_path, expected in expectations.items():
            raw = (self.project / target_path).read_bytes()
            document = json.loads(raw)
            self.assertEqual(document["synthetic_owned"], expected["top_level"])
            self.assertIn(
                expected["sut_entry"], document["hooks"].get("PreToolUse", [])
            )
            for marker in expected["format_markers"]:
                self.assertIn(marker, raw)

    def _assert_hook_projection_state(
        self,
        *,
        present_projection: Iterable[Mapping[str, Any]],
        absent_projection: Iterable[Mapping[str, Any]],
        expectations: Mapping[str, Mapping[str, Any]],
    ) -> None:
        present = [
            item
            for item in present_projection
            if item.get("scope") == "shared_json_fragment"
        ]
        absent = [
            item
            for item in absent_projection
            if item.get("scope") == "shared_json_fragment"
        ]
        self.assertTrue(present)
        present_keys = {
            (item["target_path"], item["event"], item["semantic_digest"])
            for item in present
        }
        absent_keys = {
            (item["target_path"], item["event"], item["semantic_digest"])
            for item in absent
        }
        self.assertTrue(
            present_keys.isdisjoint(absent_keys),
            "legacy and identity-bearing current hook shapes must be distinct",
        )

        targets = sorted(
            {
                str(item["target_path"])
                for item in present + absent
            }
        )
        documents: dict[str, dict[str, Any]] = {}
        identity_locations: dict[str, list[tuple[str, str, int]]] = {}
        for target_path in targets:
            document = json.loads((self.project / target_path).read_bytes())
            self.assertIsInstance(document, dict)
            hooks = document.get("hooks")
            self.assertIsInstance(hooks, dict)
            documents[target_path] = document
            for event, entries in hooks.items():
                self.assertIsInstance(event, str)
                self.assertIsInstance(entries, list)
                for index, value in enumerate(entries):
                    for identity in _hook_identities(value):
                        identity_locations.setdefault(identity, []).append(
                            (target_path, event, index)
                        )

        def occurrence_count(item: Mapping[str, Any]) -> int:
            entries = documents[str(item["target_path"])]["hooks"].get(
                item["event"], []
            )
            return sum(
                contract.semantic_digest(
                    {"event": item["event"], "value": value}
                )
                == item["semantic_digest"]
                for value in entries
            )

        for item in present:
            self.assertEqual(
                occurrence_count(item),
                1,
                f"owned hook is not unique: {item['id']}",
            )
        for item in absent:
            self.assertEqual(
                occurrence_count(item),
                0,
                f"retired hook is still installed: {item['id']}",
            )

        expected_identities = {
            str(item["hook_identity"])
            for item in present
            if isinstance(item.get("hook_identity"), str)
        }
        self.assertEqual(set(identity_locations), expected_identities)
        for identity in expected_identities:
            self.assertEqual(
                len(identity_locations[identity]),
                1,
                f"hook identity occurs in more than one entry: {identity}",
            )
        self._assert_hook_ownership_preserved(expectations)

    def _assert_dirty_file_remains_dirty(self) -> None:
        status = subprocess.run(
            ["git", "status", "--short", "--", "synthetic-owned/dirty.txt"],
            cwd=self.project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "M synthetic-owned/dirty.txt")

    def _assert_sut_owned_preserved(
        self,
        paths: Iterable[str],
        expected: Mapping[
            str,
            tuple[str, bytes | str, int]
            | dict[str, tuple[str, bytes | str, int]],
        ],
    ) -> None:
        current = _selected_snapshot(self.project, paths)
        self.assertEqual(set(current), set(expected))
        for relative, expected_image in expected.items():
            self.assertEqual(
                current[relative],
                expected_image,
                f"SUT-owned evidence changed: {relative}",
            )
        self._assert_dirty_file_remains_dirty()

    def _assert_memory_not_contacted(self) -> None:
        self.assertEqual(
            self.memory_requests,
            [],
            "engine update must not call, probe, rebuild, or migrate Memory",
        )
        self.assertFalse(
            (self.base / "memory-home-trap").exists(),
            "engine update must not create machine-level Memory state",
        )

    def test_supported_legacy_tag_transaction_matrix(self) -> None:
        self.assertEqual(tuple(contract.SUPPORTED_LEGACY_TAGS), LEGACY_MATRIX_TAGS)
        self.assertEqual(set(EXPECTED_OBSOLETE_TARGETS), set(LEGACY_MATRIX_TAGS))
        target_manifest, _ = self._build_release()
        legacy_manifests = {
            tag: self._load_legacy_manifest(tag) for tag in LEGACY_MATRIX_TAGS
        }
        target_projection = engine._materialized_target_projection(
            target_manifest, VENDOR_DIR
        )

        for tag in LEGACY_MATRIX_TAGS:
            with self.subTest(tag=tag):
                version = tag.removeprefix("v")
                self.project = self.base / f"synthetic-imported-repo-{tag}"
                self.home = self.base / f"home-{tag}"
                self.project.mkdir()
                self.home.mkdir()

                legacy_manifest = legacy_manifests[tag]
                legacy_projection, hook_expectations = self._materialize_legacy(
                    legacy_manifest
                )
                sut_paths = self._populate_sut_owned()
                self._make_dirty_git_repo()
                sut_before = _selected_snapshot(self.project, sut_paths)
                legacy_before = _projection_snapshot(
                    self.project, legacy_projection
                )
                expected_obsolete = set(EXPECTED_OBSOLETE_TARGETS[tag])
                obsolete_before = {
                    relative: _path_image(self.project / relative)
                    for relative in expected_obsolete
                }
                for relative, image in obsolete_before.items():
                    self.assertNotEqual(
                        image[0], "absent", f"obsolete baseline is missing: {relative}"
                    )

                installed_lock_path = (
                    self.project / VENDOR_DIR / contract.INSTALLED_LOCK_PATH
                )
                installed_manifest_path = (
                    self.project / VENDOR_DIR / contract.INSTALLED_MANIFEST_PATH
                )
                self.assertFalse(
                    installed_lock_path.exists() or installed_lock_path.is_symlink()
                )
                self.assertFalse(
                    installed_manifest_path.exists()
                    or installed_manifest_path.is_symlink()
                )
                self._assert_hook_projection_state(
                    present_projection=legacy_projection,
                    absent_projection=target_projection,
                    expectations=hook_expectations,
                )

                tree_before_read_only = _tree_snapshot(self.project)
                detected = engine.detect_installed_state(
                    self.project,
                    VENDOR_DIR,
                    legacy_manifests.values(),
                )
                self.assertEqual(detected.kind, "legacy")
                self.assertEqual(detected.version, version)
                self.assertTrue(detected.go)
                self.assertEqual(detected.diagnostics, ())
                self.assertIsNotNone(detected.legacy_manifest)
                assert detected.legacy_manifest is not None
                self.assertEqual(detected.legacy_manifest["source_tag"], tag)

                initial_status = self._run_cli("status")
                self.assertEqual(initial_status["decision"], "GO")
                self.assertEqual(initial_status["kind"], "legacy")
                self.assertEqual(initial_status["version"], version)
                self.assertTrue(initial_status["go"])
                self.assertIsNone(initial_status["lock"])
                self.assertEqual(
                    initial_status["legacy_manifest"]["source_tag"], tag
                )
                self.assertFalse(initial_status["recovery_required"])
                self.assertEqual(
                    _tree_snapshot(self.project), tree_before_read_only
                )
                self._assert_sut_owned_preserved(sut_paths, sut_before)
                self._assert_memory_not_contacted()

                plan = self._run_cli("plan")
                self.assertEqual(_tree_snapshot(self.project), tree_before_read_only)
                self.assertEqual(plan["decision"], "GO")
                self.assertEqual(plan["installed_kind"], "legacy")
                self.assertEqual(plan["from_version"], version)
                self.assertEqual(plan["to_version"], VERSION)
                self.assertEqual(
                    plan["release_digest"], target_manifest["self_digest"]
                )
                self.assertFalse(plan["no_op"])
                self.assertTrue(plan["rollback_available"])
                self.assertEqual(
                    plan["migration_status"], "migration_available"
                )
                legacy_hook_targets = {
                    str(item["target_path"])
                    for item in legacy_projection
                    if item["scope"] == "shared_json_fragment"
                }
                self.assertEqual(
                    set(plan["stale_managed_files"]),
                    expected_obsolete | legacy_hook_targets,
                )
                self.assertEqual(
                    {
                        item["target_path"]
                        for item in plan["managed_changes"]
                        if item["classification"] == "delete"
                        and item["scope"] != "shared_json_fragment"
                    },
                    expected_obsolete,
                )
                self.assertEqual(
                    {
                        item["target_path"]
                        for item in plan["transaction_operations"]
                        if item["action"] == "delete"
                    },
                    expected_obsolete,
                )
                operations_by_id = {
                    item["id"]: item for item in plan["transaction_operations"]
                }
                for identity, target_path in (
                    (
                        "metadata:installed-release-manifest",
                        f"{VENDOR_DIR}/{contract.INSTALLED_MANIFEST_PATH}",
                    ),
                    (
                        "metadata:installed-lock",
                        f"{VENDOR_DIR}/{contract.INSTALLED_LOCK_PATH}",
                    ),
                ):
                    operation = operations_by_id[identity]
                    self.assertEqual(operation["target_path"], target_path)
                    self.assertEqual(operation["action"], "replace")
                    self.assertEqual(operation["base"]["status"], "missing")
                    self.assertIsNone(operation["old"])
                    self.assertIsNotNone(operation["new"])
                self.assertFalse(
                    installed_lock_path.exists() or installed_lock_path.is_symlink()
                )
                self.assertFalse(
                    installed_manifest_path.exists()
                    or installed_manifest_path.is_symlink()
                )
                self._assert_hook_projection_state(
                    present_projection=legacy_projection,
                    absent_projection=target_projection,
                    expectations=hook_expectations,
                )
                self._assert_sut_owned_preserved(sut_paths, sut_before)
                self._assert_memory_not_contacted()

                applied = self._run_cli("apply")
                self.assertEqual(applied["decision"], "GO")
                self.assertEqual(applied["status"], "committed")
                self.assertEqual(applied["from_version"], version)
                self.assertEqual(applied["to_version"], VERSION)
                self.assertTrue(applied["engine_updated"])
                self.assertFalse(applied["memory_checked"])
                self.assertFalse(applied["role_governance_activated"])
                transaction_id = applied["transaction_id"]
                self.assertRegex(transaction_id, r"^[0-9a-f]{32}$")
                self.assertEqual(
                    {
                        item["target_path"]
                        for item in applied["operations"]
                        if item["post"]["type"] == "absent"
                    },
                    expected_obsolete,
                )
                for relative in expected_obsolete:
                    self.assertEqual(
                        _path_image(self.project / relative)[0], "absent"
                    )

                self.assertTrue(installed_lock_path.is_file())
                self.assertTrue(installed_manifest_path.is_file())
                installed_lock = json.loads(installed_lock_path.read_bytes())
                self.assertEqual(installed_lock["installed_version"], VERSION)
                self.assertEqual(installed_lock["previous_version"], version)
                self.assertEqual(
                    installed_lock["verified_release_digest"],
                    target_manifest["self_digest"],
                )
                self.assertEqual(
                    installed_manifest_path.read_bytes(),
                    contract.canonical_json_bytes(target_manifest),
                )
                self._assert_hook_projection_state(
                    present_projection=installed_lock["installed_projection"],
                    absent_projection=legacy_projection,
                    expectations=hook_expectations,
                )
                self._assert_sut_owned_preserved(sut_paths, sut_before)
                self._assert_memory_not_contacted()

                installed_status = self._run_installed_cli("status")
                self.assertEqual(installed_status["decision"], "GO")
                self.assertEqual(installed_status["kind"], "locked")
                self.assertEqual(installed_status["version"], VERSION)
                verified = self._run_installed_cli("verify")
                self.assertEqual(verified["decision"], "GO")
                self.assertEqual(verified["status"], "passed")
                self.assertEqual(verified["installed_version"], VERSION)
                self.assertEqual(verified["installed_kind"], "locked")
                self.assertTrue(verified["lock_based"])
                self._assert_sut_owned_preserved(sut_paths, sut_before)
                self._assert_memory_not_contacted()

                rolled_back = self._run_cli(
                    "rollback", "--transaction", transaction_id
                )
                self.assertEqual(rolled_back["decision"], "GO")
                self.assertEqual(rolled_back["kind"], "rollback")
                self.assertEqual(rolled_back["rollback_of"], transaction_id)
                self.assertRegex(
                    rolled_back["transaction_id"], r"^[0-9a-f]{32}$"
                )
                self.assertFalse(
                    installed_lock_path.exists() or installed_lock_path.is_symlink()
                )
                self.assertFalse(
                    installed_manifest_path.exists()
                    or installed_manifest_path.is_symlink()
                )
                self.assertEqual(
                    _projection_snapshot(self.project, legacy_projection),
                    legacy_before,
                )
                for relative, expected_image in obsolete_before.items():
                    self.assertEqual(
                        _path_image(self.project / relative), expected_image
                    )
                self._assert_hook_projection_state(
                    present_projection=legacy_projection,
                    absent_projection=target_projection,
                    expectations=hook_expectations,
                )
                self._assert_sut_owned_preserved(sut_paths, sut_before)
                self._assert_memory_not_contacted()

                restored = engine.detect_installed_state(
                    self.project,
                    VENDOR_DIR,
                    legacy_manifests.values(),
                )
                self.assertEqual(restored.kind, "legacy")
                self.assertEqual(restored.version, version)
                self.assertTrue(restored.go)
                restored_status = self._run_cli("status")
                self.assertEqual(restored_status["decision"], "GO")
                self.assertEqual(restored_status["kind"], "legacy")
                self.assertEqual(restored_status["version"], version)
                restored_verified = self._run_cli("verify")
                self.assertEqual(restored_verified["decision"], "GO")
                self.assertEqual(restored_verified["status"], "passed")
                self.assertEqual(restored_verified["installed_kind"], "legacy")
                self.assertEqual(restored_verified["installed_version"], version)
                self.assertFalse(restored_verified["lock_based"])
                self._assert_sut_owned_preserved(sut_paths, sut_before)
                self._assert_memory_not_contacted()

    def test_v032_bootstrap_apply_verify_idempotence_and_rollback(self) -> None:
        target_manifest, legacy_manifest = self._build_release()
        legacy_projection, hook_expectations = self._materialize_legacy(
            legacy_manifest
        )
        sut_paths = self._populate_sut_owned()
        self._make_dirty_git_repo()

        sut_before = _selected_snapshot(self.project, sut_paths)
        namespace_before = (self.project / "bugate.profile.yaml").read_bytes()
        legacy_before = _projection_snapshot(self.project, legacy_projection)
        block = next(
            item
            for item in legacy_projection
            if item["scope"] == "marked_text_block"
        )
        gitignore_outside_before = _outside_marked_block(
            (self.project / block["target_path"]).read_bytes(), block
        )
        tree_before_plan = _tree_snapshot(self.project)

        plan = self._run_cli("plan")

        self.assertEqual(_tree_snapshot(self.project), tree_before_plan)
        self._assert_memory_not_contacted()
        self.assertEqual(plan["decision"], "GO")
        self.assertEqual(plan["installed_kind"], "legacy")
        self.assertEqual(plan["from_version"], "0.3.2")
        self.assertEqual(plan["to_version"], VERSION)
        self.assertEqual(plan["release_digest"], target_manifest["self_digest"])
        self.assertFalse(plan["no_op"])
        self.assertTrue(plan["rollback_available"])
        self.assertEqual(plan["migration_status"], "migration_available")
        codex_change = next(
            item
            for item in plan["hook_changes"]
            if item["target_path"] == ".codex/hooks.json"
        )
        expected_codex_change = (
            codex_change["before_sha256"] != codex_change["after_sha256"]
        )
        self.assertTrue(expected_codex_change)
        self.assertEqual(
            plan["codex_hook_hash_changed"], expected_codex_change
        )

        applied = self._run_cli("apply")

        self.assertEqual(applied["decision"], "GO")
        self.assertEqual(applied["status"], "committed")
        self.assertTrue(applied["engine_updated"])
        self.assertFalse(applied["memory_checked"])
        self.assertFalse(applied["role_governance_activated"])
        self._assert_memory_not_contacted()
        self.assertEqual(applied["codex_hook_hash_changed"], expected_codex_change)
        transaction_id = applied["transaction_id"]
        self.assertRegex(transaction_id, r"^[0-9a-f]{32}$")

        installed_lock_path = self.project / VENDOR_DIR / contract.INSTALLED_LOCK_PATH
        installed_manifest_path = (
            self.project / VENDOR_DIR / contract.INSTALLED_MANIFEST_PATH
        )
        self.assertTrue(installed_lock_path.is_file())
        self.assertTrue(installed_manifest_path.is_file())
        installed_lock = json.loads(installed_lock_path.read_bytes())
        self.assertEqual(installed_lock["installed_version"], VERSION)
        self.assertEqual(installed_lock["previous_version"], "0.3.2")
        self.assertEqual(
            installed_lock["verified_release_digest"], target_manifest["self_digest"]
        )
        self.assertIsNone(installed_lock["archive_sha256"])
        self.assertEqual(
            installed_lock["archive_verification"],
            "unavailable-from-unpacked-source",
        )
        self.assertEqual(
            installed_manifest_path.read_bytes(),
            contract.canonical_json_bytes(target_manifest),
        )
        installed_status = self._run_installed_cli("status")
        self.assertEqual(installed_status["decision"], "GO")
        self.assertEqual(installed_status["kind"], "locked")
        self.assertEqual(installed_status["version"], VERSION)
        installed_verified = self._run_installed_cli("verify")
        self.assertEqual(installed_verified["decision"], "GO")
        self.assertEqual(installed_verified["installed_kind"], "locked")
        self.assertTrue(installed_verified["lock_based"])
        verified = self._run_cli("verify")
        self.assertEqual(verified["decision"], "GO")
        self.assertEqual(verified["status"], "passed")
        self.assertEqual(verified["installed_version"], VERSION)
        self.assertEqual(verified["installed_kind"], "locked")
        self.assertTrue(verified["lock_based"])

        self.assertEqual(_selected_snapshot(self.project, sut_paths), sut_before)
        self.assertEqual(
            (self.project / "bugate.profile.yaml").read_bytes(), namespace_before
        )
        self._assert_hook_ownership_preserved(hook_expectations)
        self.assertEqual(
            _outside_marked_block(
                (self.project / block["target_path"]).read_bytes(), block
            ),
            gitignore_outside_before,
        )
        profile = (self.project / "bugate.profile.yaml").read_text(encoding="utf-8")
        self.assertIn("mode: off", profile)
        self.assertIn("namespace: project:synthetic-updater-e2e", profile)

        tree_before_rerun = _tree_snapshot(self.project)
        rerun = self._run_cli("apply")
        self.assertEqual(rerun["decision"], "GO")
        self.assertEqual(rerun["status"], "no-op")
        self.assertIsNone(rerun["transaction_id"])
        self.assertFalse(rerun["engine_updated"])
        self.assertEqual(_tree_snapshot(self.project), tree_before_rerun)
        self.assertTrue(rerun["no_op"])
        self._assert_memory_not_contacted()

        rolled_back = self._run_cli(
            "rollback", "--transaction", transaction_id
        )
        self.assertEqual(rolled_back["decision"], "GO")
        self.assertEqual(rolled_back["kind"], "rollback")
        self.assertEqual(rolled_back["rollback_of"], transaction_id)
        rollback_transaction_id = rolled_back["transaction_id"]
        self.assertRegex(rollback_transaction_id, r"^[0-9a-f]{32}$")
        self.assertFalse(installed_lock_path.exists())
        self.assertFalse(installed_manifest_path.exists())
        self.assertEqual(
            _projection_snapshot(self.project, legacy_projection), legacy_before
        )
        self.assertEqual(_selected_snapshot(self.project, sut_paths), sut_before)
        self.assertEqual(
            (self.project / "bugate.profile.yaml").read_bytes(), namespace_before
        )
        self._assert_memory_not_contacted()
        self._assert_hook_ownership_preserved(hook_expectations)
        self.assertEqual(
            _outside_marked_block(
                (self.project / block["target_path"]).read_bytes(), block
            ),
            gitignore_outside_before,
        )

        root_state = self.project / ".bugate-update"
        archive_parent = self.project / VENDOR_DIR / "plan.lock"
        archived_state = archive_parent / "bugate-update"
        self.assertFalse(root_state.exists() or root_state.is_symlink())
        self.assertTrue(archive_parent.is_dir())
        self.assertFalse(archive_parent.is_symlink())
        self.assertEqual(
            {path.name for path in archive_parent.iterdir()}, {"bugate-update"}
        )
        self.assertEqual(
            {path.name for path in archived_state.iterdir()},
            {"archived-rollback.json", "sentinel.json", "transactions"},
        )
        original_report = (
            archived_state / "transactions" / transaction_id / "report.json"
        )
        rollback_report = (
            archived_state
            / "transactions"
            / rollback_transaction_id
            / "report.json"
        )
        self.assertTrue(original_report.is_file())
        self.assertTrue(rollback_report.is_file())
        rollback_transaction_root = rollback_report.parent
        self.assertTrue(
            (rollback_transaction_root / "worker/bugate_update_transaction.py").is_file()
        )
        self.assertTrue(
            (rollback_transaction_root / "input/rollback-legacy-manifest.json").is_file()
        )
        self.assertTrue(
            (rollback_transaction_root / "input/report-metadata.json").is_file()
        )
        original_report_bytes = original_report.read_bytes()
        rollback_report_bytes = rollback_report.read_bytes()
        ignored = subprocess.run(
            [
                "git",
                "check-ignore",
                "--quiet",
                ".bugate/plan.lock/bugate-update/sentinel.json",
            ],
            cwd=self.project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        state_status = subprocess.run(
            [
                "git",
                "status",
                "--short",
                "--untracked-files=all",
                "--",
                ".bugate-update",
                ".bugate/plan.lock",
            ],
            cwd=self.project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(state_status.returncode, 0, state_status.stderr)
        self.assertEqual(state_status.stdout, "")

        legacy_verified = self._run_cli("verify")
        self.assertEqual(legacy_verified["decision"], "GO")
        self.assertEqual(legacy_verified["status"], "passed")
        self.assertEqual(legacy_verified["installed_version"], "0.3.2")
        self.assertEqual(legacy_verified["installed_kind"], "legacy")
        self.assertFalse(legacy_verified["lock_based"])
        self._assert_memory_not_contacted()

        tree_before_second_plan = _tree_snapshot(self.project)
        second_plan = self._run_cli("plan")
        self.assertEqual(_tree_snapshot(self.project), tree_before_second_plan)
        self.assertEqual(second_plan["decision"], "GO")
        self.assertEqual(second_plan["installed_kind"], "legacy")
        self.assertEqual(second_plan["from_version"], "0.3.2")
        self.assertEqual(second_plan["to_version"], VERSION)

        reapplied = self._run_cli("apply")
        self.assertEqual(reapplied["decision"], "GO")
        self.assertEqual(reapplied["status"], "committed")
        self.assertFalse(reapplied["no_op"])
        self.assertTrue(reapplied["engine_updated"])
        self.assertNotEqual(reapplied["transaction_id"], transaction_id)
        self.assertFalse(archive_parent.exists() or archive_parent.is_symlink())
        self.assertTrue(root_state.is_dir())
        self.assertEqual(
            (
                root_state
                / "transactions"
                / transaction_id
                / "report.json"
            ).read_bytes(),
            original_report_bytes,
        )
        self.assertEqual(
            (
                root_state
                / "transactions"
                / rollback_transaction_id
                / "report.json"
            ).read_bytes(),
            rollback_report_bytes,
        )
        self.assertEqual(_selected_snapshot(self.project, sut_paths), sut_before)
        self.assertEqual(
            (self.project / "bugate.profile.yaml").read_bytes(), namespace_before
        )
        self._assert_hook_ownership_preserved(hook_expectations)
        reverified = self._run_cli("verify")
        self.assertEqual(reverified["decision"], "GO")
        self.assertEqual(reverified["installed_kind"], "locked")
        self.assertTrue(reverified["lock_based"])
        self.assertEqual(_selected_snapshot(self.project, sut_paths), sut_before)
        self.assertEqual(
            (self.project / "bugate.profile.yaml").read_bytes(), namespace_before
        )
        self._assert_memory_not_contacted()


if __name__ == "__main__":
    unittest.main()
