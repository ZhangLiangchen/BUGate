#!/usr/bin/env python3
"""Hermetic real-CLI acceptances for locked and archive-bootstrap updates.

Every governed repository in this module is synthetic and created beneath a
system temporary directory.  No external SUT, Memory service, or network input
is read.  The release payloads contain the real BUGate runtime from this
checkout; only their temporary version declarations are rewritten.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for entry in (SCRIPTS, TESTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import bugate_install_contract as contract  # noqa: E402
import bugate_legacy_manifest as legacy  # noqa: E402
import bugate_update_engine as engine  # noqa: E402
import bugate_update_source as source  # noqa: E402
from test_bugate_update_engine import materialize_install  # noqa: E402


VERSION_OLD = "0.4.2"
VERSION_NEW = "0.4.3"
VENDOR_DIR = ".bugate"
IGNORED_NAMES = {"__pycache__", ".DS_Store"}


def ignored_name(name: str) -> bool:
    return name in IGNORED_NAMES or name.endswith(".pyc")


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if ignored_name(name)}


def write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


def copy_entry(source_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source_path.is_symlink():
        destination.symlink_to(os.readlink(source_path))
    elif source_path.is_dir():
        shutil.copytree(
            source_path,
            destination,
            symlinks=True,
            copy_function=shutil.copy2,
            ignore=copy_ignore,
        )
    else:
        shutil.copy2(source_path, destination, follow_symlinks=False)


def physical_paths(root: Path) -> list[str]:
    paths: list[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            if ignored_name(name):
                continue
            path = current_path / name
            paths.append(path.relative_to(root).as_posix())
            if not path.is_symlink():
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if not ignored_name(name):
                paths.append((current_path / name).relative_to(root).as_posix())
    return sorted(paths)


def build_functional_release(
    base: Path,
    version: str,
    overlays: Mapping[str, bytes],
) -> tuple[Path, dict[str, Any]]:
    release_root = base / f"bugate-{version}"
    for relative in contract.VENDOR_TREE_ROOTS:
        copy_entry(ROOT / relative, release_root / relative)
    for relative in contract.VENDOR_SINGLE_FILES:
        copy_entry(ROOT / relative, release_root / relative)
    for relative in (
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
    ):
        document = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        document["version"] = version
        write(
            release_root / relative,
            (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(),
        )
    copy_entry(ROOT / "bugate.config.yaml", release_root / "bugate.config.yaml")

    updater_path = release_root / "scripts/bugate_update.py"
    updater = updater_path.read_text(encoding="utf-8")
    old_literal = 'UPDATER_VERSION = "0.4.3"'
    if updater.count(old_literal) != 1:
        raise AssertionError("temporary release expected one updater version literal")
    updater_path.write_text(
        updater.replace(old_literal, f'UPDATER_VERSION = "{version}"'),
        encoding="utf-8",
    )
    worker_path = release_root / "scripts/bugate_update_transaction.py"
    worker = worker_path.read_text(encoding="utf-8")
    future = "from __future__ import annotations\n"
    if worker.count(future) != 1:
        raise AssertionError("temporary transaction worker lacks its future import")
    if version == VERSION_OLD:
        worker_probe = r'''
# Synthetic acceptance poison: the installed source module may orchestrate an
# update, but it must never execute as the target transaction worker.
if len(__import__("sys").argv) >= 2 and __import__("sys").argv[1] == "__transaction-worker":
    if __import__("os").environ.get("BUGATE_POISON_SOURCE_WORKER") == "1":
        raise SystemExit(91)
'''
    else:
        worker_probe = r'''
# Synthetic acceptance receipt: only the verified target worker contains this.
if len(__import__("sys").argv) >= 2 and __import__("sys").argv[1] == "__transaction-worker":
    _receipt = __import__("os").environ.get("BUGATE_TARGET_WORKER_RECEIPT")
    if _receipt:
        with open(_receipt, "a", encoding="utf-8") as _stream:
            _stream.write("target-worker-0.4.3\n")
'''
    worker_path.write_text(
        worker.replace(future, future + worker_probe + "\n"),
        encoding="utf-8",
    )

    manifest = contract.build_release_manifest(
        release_root,
        version,
        selected_paths=physical_paths(release_root),
        overlay_files=overlays,
        updater_minimum_version=VERSION_OLD,
    )
    for relative, payload in overlays.items():
        write(release_root / relative, payload)
    write(
        release_root / contract.RELEASE_MANIFEST_PATH,
        contract.canonical_json_bytes(manifest),
    )
    prepared = source.prepare_unpacked(release_root, expected_version=version)
    if prepared.manifest != manifest:
        raise AssertionError("temporary functional release did not self-verify")
    return release_root, manifest


def write_tar_and_sums(
    release_root: Path,
    manifest: Mapping[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    version = str(manifest["bugate_version"])
    prefix = str(manifest["archive_prefix"])
    tar_path = output_dir / f"bugate-{version}.tar.gz"
    sums_path = output_dir / f"bugate-{version}.SHA256SUMS"
    output_dir.mkdir(parents=True, exist_ok=True)
    with tar_path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                for item in manifest["archive_inventory"]:
                    relative = str(item["path"])
                    info = tarfile.TarInfo(f"{prefix}/{relative}")
                    info.mode = int(str(item["mode"]), 8)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    kind = item["type"]
                    if kind == "directory":
                        info.type = tarfile.DIRTYPE
                        archive.addfile(info)
                    elif kind == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = str(item["target"])
                        archive.addfile(info)
                    elif kind == "file":
                        payload = (release_root / relative).read_bytes()
                        expected_hash = item.get("sha256")
                        if (
                            expected_hash is not None
                            and contract.sha256_bytes(payload) != expected_hash
                        ):
                            raise AssertionError(f"archive source hash drift: {relative}")
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                    else:
                        raise AssertionError(f"unsupported inventory type: {kind}")
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    sums_path.write_text(f"{digest}  {tar_path.name}\n", encoding="utf-8")
    verification_stage = output_dir / f"verify-{version}"
    verification_stage.mkdir()
    prepared = source.prepare_archive(
        tar_path,
        sums_path,
        verification_stage,
        expected_version=version,
    )
    if prepared.manifest != manifest or prepared.archive_sha256 != digest:
        raise AssertionError("temporary archive did not pass source verification")
    return tar_path, sums_path


def path_image(path: Path) -> tuple[str, bytes | str, int]:
    if not (path.exists() or path.is_symlink()):
        return ("absent", b"", 0)
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode):
        return ("symlink", os.readlink(path), stat.S_IMODE(details.st_mode))
    if stat.S_ISDIR(details.st_mode):
        return ("directory", b"", stat.S_IMODE(details.st_mode))
    return ("file", path.read_bytes(), stat.S_IMODE(details.st_mode))


def tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    result: dict[str, tuple[str, bytes | str, int]] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            path = current_path / name
            result[path.relative_to(root).as_posix()] = path_image(path)
            if not path.is_symlink():
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = current_path / name
            result[path.relative_to(root).as_posix()] = path_image(path)
    return result


def projection_snapshot(
    project: Path, projection: Iterable[Mapping[str, Any]]
) -> dict[str, tuple[str, bytes | str, int]]:
    return {
        relative: path_image(project / relative)
        for relative in sorted({str(item["target_path"]) for item in projection})
    }


def materialize_legacy(
    project: Path,
    manifest: Mapping[str, Any],
    tag: str,
) -> list[dict[str, Any]]:
    archived = subprocess.run(
        ["git", "archive", "--format=tar", tag],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if archived.returncode != 0:
        raise AssertionError(archived.stderr.decode("utf-8", "replace"))
    projection = engine.render_legacy_projection(manifest, VENDOR_DIR)
    with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
        members = {
            member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name: member
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
            target = project / item["target_path"]
            source_path = str(item["source_path"])
            if item["type"] == "directory":
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, int(item["mode"], 8))
            elif item["type"] == "symlink":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(item["target"])
            else:
                stream = archive.extractfile(members[source_path])
                if stream is None:
                    raise AssertionError(f"legacy payload missing: {source_path}")
                payload = stream.read()
                if contract.sha256_bytes(payload) != item["sha256"]:
                    raise AssertionError(f"legacy payload hash mismatch: {source_path}")
                write(target, payload, int(item["mode"], 8))

    for target_path in sorted(
        {
            item["target_path"]
            for item in projection
            if item["scope"] == "shared_json_fragment"
        }
    ):
        items = [
            item
            for item in projection
            if item["scope"] == "shared_json_fragment"
            and item["target_path"] == target_path
        ]
        document: dict[str, Any] = {"hooks": {}}
        for item in items:
            document["hooks"].setdefault(item["event"], []).append(item["value"])
        write(
            project / target_path,
            (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(),
        )
    block = next(item for item in projection if item["scope"] == "marked_text_block")
    write(project / block["target_path"], block["content"].encode())
    return projection


class FullCheckMemoryState:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.lock = threading.Lock()


class FullCheckMemoryHandler(http.server.BaseHTTPRequestHandler):
    server: "FullCheckMemoryServer"

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AssertionError("fake Memory request must be an object")
        return value

    def send_json(self, status: int, value: object) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        identity = (
            unquote(path[len("/api/memories/") :])
            if path.startswith("/api/memories/")
            else None
        )
        with self.server.state.lock:
            self.server.state.calls.append(("GET", path, identity))
        if path == "/api/health":
            self.send_json(200, {"status": "healthy"})
            return
        if path == "/api/memories":
            with self.server.state.lock:
                records = copy.deepcopy(list(self.server.state.records.values()))
            self.send_json(200, {"memories": records})
            return
        prefix = "/api/memories/"
        if path.startswith(prefix):
            exact_id = unquote(path[len(prefix) :])
            with self.server.state.lock:
                record = copy.deepcopy(self.server.state.records.get(exact_id))
            if record is None:
                self.send_json(404, {"detail": "Memory not found"})
            else:
                self.send_json(200, record)
            return
        self.send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/api/search", "/api/search/by-tag"}:
            self.body()
            with self.server.state.lock:
                self.server.state.calls.append(("POST", path, None))
                records = copy.deepcopy(list(self.server.state.records.values()))
            self.send_json(200, {"memories": records})
            return
        if path != "/api/memories":
            self.send_json(404, {"detail": "not found"})
            return
        request = self.body()
        content = str(request.get("content") or "")
        exact_id = hashlib.sha256(content.encode()).hexdigest()
        with self.server.state.lock:
            self.server.state.calls.append(("POST", path, exact_id))
            record = self.server.state.records.setdefault(
                exact_id,
                {
                    "content": content,
                    "content_hash": exact_id,
                    "tags": copy.deepcopy(request.get("tags") or []),
                    "memory_type": request.get("memory_type"),
                    "metadata": copy.deepcopy(request.get("metadata") or {}),
                    "created_at_iso": "2026-07-21T00:00:00Z",
                },
            )
            stored = copy.deepcopy(record)
        self.send_json(
            200,
            {
                "success": True,
                "message": "stored",
                "content_hash": exact_id,
                "memory": stored,
            },
        )

    def do_PUT(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        prefix = "/api/memories/"
        if not path.startswith(prefix):
            self.send_json(404, {"detail": "not found"})
            return
        exact_id = unquote(path[len(prefix) :])
        request = self.body()
        with self.server.state.lock:
            self.server.state.calls.append(("PUT", path, exact_id))
            record = self.server.state.records.get(exact_id)
            if record is None:
                self.send_json(404, {"detail": "Memory not found"})
                return
            record["metadata"] = copy.deepcopy(request.get("metadata") or {})
            stored = copy.deepcopy(record)
        self.send_json(
            200,
            {
                "success": True,
                "message": "updated",
                "content_hash": exact_id,
                "memory": stored,
            },
        )


class FullCheckMemoryServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FullCheckMemoryHandler)
        self.state = FullCheckMemoryState()


class RealUpdaterAcceptanceTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="bugate-update-acceptance-")
        cls.base = Path(cls.temporary.name)
        cls.home = cls.base / "home"
        cls.home.mkdir()
        overlays = legacy.generate_all_legacy_manifests(ROOT)
        cls.release_old, cls.manifest_old = build_functional_release(
            cls.base, VERSION_OLD, overlays
        )
        cls.release_new, cls.manifest_new = build_functional_release(
            cls.base, VERSION_NEW, overlays
        )
        cls.tar_old, cls.sums_old = write_tar_and_sums(
            cls.release_old, cls.manifest_old, cls.base / "assets-old"
        )
        cls.tar_new, cls.sums_new = write_tar_and_sums(
            cls.release_new, cls.manifest_new, cls.base / "assets-new"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for name in (
            "PYTHONPATH",
            "BUGATE_PROJECT_ROOT",
            "BUGATE_ENGINE_ROOT",
            "BUGATE_VENDOR_DIR",
            "BUGATE_PROFILE",
            "MEMORY_BUS_PROJECT_TAG",
            "BUGATE_AGENT_ROLE",
            "BUGATE_SESSION_ID",
            "BUGATE_ROLE_SESSION",
            "BUGATE_POISON_SOURCE_WORKER",
            "BUGATE_TARGET_WORKER_RECEIPT",
        ):
            environment.pop(name, None)
        if getattr(self, "poison_source_worker", False):
            environment["BUGATE_POISON_SOURCE_WORKER"] = "1"
        receipt = getattr(self, "target_worker_receipt", None)
        if receipt is not None:
            environment["BUGATE_TARGET_WORKER_RECEIPT"] = str(receipt)
        return environment

    def run_json(
        self,
        command: Iterable[str | Path],
        *,
        cwd: Path,
        expected: int = 0,
    ) -> dict[str, Any]:
        rendered = [str(item) for item in command]
        completed = subprocess.run(
            rendered,
            cwd=cwd,
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            f"command={rendered}\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"command emitted non-JSON output: {exc}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        self.assertIsInstance(payload, dict)
        return payload

    def run_human(self, command: Iterable[str | Path], *, cwd: Path) -> str:
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=cwd,
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def new_locked_project(self, name: str) -> Path:
        project = self.base / name
        project.mkdir()
        materialize_install(project, self.release_old, self.manifest_old)
        write(project / "sut-owned.txt", b"preserve\n")
        return project

    def update_arguments(self, project: Path, command: str) -> list[str | Path]:
        return [
            project / VENDOR_DIR / "bin/bugate-update",
            command,
            project,
            "--vendor-dir",
            VENDOR_DIR,
            "--archive",
            self.tar_new,
            "--checksums",
            self.sums_new,
            "--to",
            VERSION_NEW,
        ]

    def test_locked_forward_update_saved_plan_drifts_and_rollback(self) -> None:
        project = self.new_locked_project("locked-forward")
        for relative in (".claude/settings.json", ".codex/hooks.json"):
            hook_path = project / relative
            document = json.loads(hook_path.read_bytes())
            document["sut_owned_top_level"] = {
                "preserve": relative,
                "ordered": [3, 1, 2],
            }
            document["hooks"].setdefault("PreToolUse", []).insert(
                0,
                {
                    "matcher": "synthetic-sut-owned",
                    "hooks": [
                        {"type": "command", "command": "./bin/sut-hook --check"}
                    ],
                },
            )
            hook_path.write_text(
                json.dumps(document, ensure_ascii=False, indent=4) + "\n",
                encoding="utf-8",
            )
        old_lock = json.loads(
            (project / VENDOR_DIR / contract.INSTALLED_LOCK_PATH).read_bytes()
        )
        old_projection_image = projection_snapshot(
            project, old_lock["installed_projection"]
        )
        shared_before = {
            relative: (project / relative).read_bytes()
            for relative in (".claude/settings.json", ".codex/hooks.json")
        }
        self.poison_source_worker = True
        self.target_worker_receipt = self.base / "target-worker-receipt.log"
        self.target_worker_receipt.unlink(missing_ok=True)
        wrapper = project / VENDOR_DIR / "bin/bugate-update"
        status = self.run_json(
            [wrapper, "status", project, "--vendor-dir", VENDOR_DIR, "--json"],
            cwd=project,
        )
        self.assertEqual((status["kind"], status["version"]), ("locked", VERSION_OLD))

        plan = self.run_json([*self.update_arguments(project, "plan"), "--json"], cwd=project)
        self.assertEqual(plan["decision"], "GO")
        self.assertEqual((plan["from_version"], plan["to_version"]), (VERSION_OLD, VERSION_NEW))
        self.assertFalse(plan["codex_hook_hash_changed"])
        self.assertFalse(plan["new_session_required"])
        human = self.run_human(self.update_arguments(project, "plan"), cwd=project)
        self.assertNotIn("Codex hook hash changed: re-trust required", human)

        saved = self.base / "locked-forward-plan.json"
        saved.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        applied = self.run_json(
            [*self.update_arguments(project, "apply"), "--plan", saved, "--json"],
            cwd=project,
        )
        self.assertEqual((applied["decision"], applied["status"]), ("GO", "committed"))
        self.assertFalse(applied["codex_hook_hash_changed"])
        self.assertTrue(self.target_worker_receipt.is_file())
        self.assertEqual(
            self.target_worker_receipt.read_text(encoding="utf-8").splitlines(),
            ["target-worker-0.4.3"],
        )
        transaction_id = applied["transaction_id"]
        self.assertRegex(transaction_id, r"^[0-9a-f]{32}$")
        self.assertEqual((project / "sut-owned.txt").read_bytes(), b"preserve\n")
        self.assertEqual(
            {
                relative: (project / relative).read_bytes()
                for relative in shared_before
            },
            shared_before,
        )

        updated_wrapper = project / VENDOR_DIR / "bin/bugate-update"
        verified_new = self.run_json(
            [updated_wrapper, "verify", project, "--vendor-dir", VENDOR_DIR, "--json"],
            cwd=project,
        )
        self.assertEqual(verified_new["decision"], "GO")
        self.assertEqual(verified_new["installed_version"], VERSION_NEW)
        self.assertTrue(verified_new["lock_based"])

        rolled_back = self.run_json(
            [
                updated_wrapper,
                "rollback",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--transaction",
                transaction_id,
                "--json",
            ],
            cwd=project,
        )
        self.assertEqual((rolled_back["decision"], rolled_back["kind"]), ("GO", "rollback"))
        restored_wrapper = project / VENDOR_DIR / "bin/bugate-update"
        verified_old = self.run_json(
            [restored_wrapper, "verify", project, "--vendor-dir", VENDOR_DIR, "--json"],
            cwd=project,
        )
        self.assertEqual(verified_old["installed_version"], VERSION_OLD)
        self.assertEqual(verified_old["decision"], "GO")
        self.assertEqual((project / "sut-owned.txt").read_bytes(), b"preserve\n")
        self.assertEqual(
            projection_snapshot(project, old_lock["installed_projection"]),
            old_projection_image,
        )
        self.assertEqual(
            {
                relative: (project / relative).read_bytes()
                for relative in shared_before
            },
            shared_before,
        )

        drift_cases = ("config", "profile", "hook")
        for kind in drift_cases:
            with self.subTest(saved_plan_drift=kind):
                drift_project = self.new_locked_project(f"saved-drift-{kind}")
                drift_plan = self.run_json(
                    [*self.update_arguments(drift_project, "plan"), "--json"],
                    cwd=drift_project,
                )
                drift_saved = self.base / f"saved-drift-{kind}.json"
                drift_saved.write_text(
                    json.dumps(drift_plan, ensure_ascii=False), encoding="utf-8"
                )
                if kind == "config":
                    target = drift_project / "bugate.config.yaml"
                    target.write_bytes(target.read_bytes() + b"# operator config drift\n")
                elif kind == "profile":
                    target = drift_project / "bugate.profile.yaml"
                    target.write_bytes(target.read_bytes() + b"# operator profile drift\n")
                else:
                    target = drift_project / ".codex/hooks.json"
                    document = json.loads(target.read_bytes())
                    document["sut_owned_drift"] = True
                    target.write_text(
                        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                after_operator_drift = tree_snapshot(drift_project)
                rejected = self.run_json(
                    [
                        *self.update_arguments(drift_project, "apply"),
                        "--plan",
                        drift_saved,
                        "--json",
                    ],
                    cwd=drift_project,
                    expected=1,
                )
                self.assertEqual(rejected["decision"], "NO-GO")
                self.assertIn("saved plan is stale", rejected["errors"][0])
                self.assertEqual(tree_snapshot(drift_project), after_operator_drift)
                self.assertFalse((drift_project / ".bugate-update").exists())
                lock = json.loads(
                    (drift_project / VENDOR_DIR / contract.INSTALLED_LOCK_PATH).read_bytes()
                )
                self.assertEqual(lock["installed_version"], VERSION_OLD)

    def test_archive_bootstrap_from_v032_and_real_imported_full_check_preflight(self) -> None:
        project = self.base / "archive-bootstrap-v032"
        project.mkdir()
        legacy_asset = json.loads(
            (
                self.release_old
                / contract.LEGACY_MANIFEST_DIR
                / "v0.3.2.json"
            ).read_bytes()
        )
        materialize_legacy(project, legacy_asset, "v0.3.2")
        write(
            project / "bugate.config.yaml",
            b"bugate:\n  version: '0.1'\nprofile: bugate.profile.yaml\n",
        )
        write(
            project / "bugate.profile.yaml",
            b"guarded_path_regex: []\nrole_governance:\n  mode: off\n"
            b"memory:\n  namespace: project:synthetic-archive-bootstrap\n",
        )
        write(project / "sut-owned.txt", b"archive bootstrap preserve\n")

        extraction_stage = self.base / "bootstrap-extracted"
        extraction_stage.mkdir()
        unpacked = source.prepare_archive(
            self.tar_old,
            self.sums_old,
            extraction_stage,
            expected_version=VERSION_OLD,
        )
        bootstrap = unpacked.root / "scripts/bugate_update.py"
        before_plan = tree_snapshot(project)
        plan = self.run_json(
            [
                sys.executable,
                bootstrap,
                "plan",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--archive",
                self.tar_old,
                "--checksums",
                self.sums_old,
                "--to",
                VERSION_OLD,
                "--json",
            ],
            cwd=project,
        )
        self.assertEqual((plan["installed_kind"], plan["from_version"]), ("legacy", "0.3.2"))
        self.assertEqual(plan["to_version"], VERSION_OLD)
        self.assertEqual(tree_snapshot(project), before_plan)

        applied = self.run_json(
            [
                sys.executable,
                bootstrap,
                "apply",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--archive",
                self.tar_old,
                "--checksums",
                self.sums_old,
                "--to",
                VERSION_OLD,
                "--json",
            ],
            cwd=project,
        )
        self.assertEqual((applied["decision"], applied["status"]), ("GO", "committed"))
        self.assertEqual((project / "sut-owned.txt").read_bytes(), b"archive bootstrap preserve\n")
        lock = json.loads(
            (project / VENDOR_DIR / contract.INSTALLED_LOCK_PATH).read_bytes()
        )
        self.assertEqual(lock["installed_version"], VERSION_OLD)
        self.assertEqual(lock["previous_version"], "0.3.2")
        self.assertEqual(lock["archive_sha256"], hashlib.sha256(self.tar_old.read_bytes()).hexdigest())
        self.assertEqual(lock["archive_verification"], "sha256")

        runner = (
            project
            / VENDOR_DIR
            / ".shared/skills/bugate-full-check/scripts/run_full_check.py"
        )
        spec = importlib.util.spec_from_file_location(
            "bugate_archive_acceptance_full_check", runner
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        assert spec is not None and spec.loader is not None
        sys.modules[spec.name] = module
        isolated_names = (
            "BUGATE_PROJECT_ROOT",
            "BUGATE_ENGINE_ROOT",
            "BUGATE_VENDOR_DIR",
            "BUGATE_PROFILE",
        )
        inherited = {name: os.environ.pop(name, None) for name in isolated_names}
        try:
            spec.loader.exec_module(module)
            root, selected_engine, layout = module.find_roots(project)
            checks: list[Any] = []
            module.verify_imported_installed_state(
                checks,
                root,
                selected_engine,
                layout,
                timeout=60,
            )
        finally:
            sys.modules.pop(spec.name, None)
            for name, value in inherited.items():
                if value is not None:
                    os.environ[name] = value
        self.assertEqual(layout, "imported")
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "PASS", checks[0].detail)
        self.assertIn("installed_kind='locked'/'locked'", checks[0].detail)

        # Run the actual imported smoke full-check without depending on a
        # machine service or optional model authentication. Smoke intentionally
        # checks only CLI discovery (not model dispatch); a small local Memory
        # API implements the real strict POST/GET/PUT role-transition contract.
        server = FullCheckMemoryServer()
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01),
            name="bugate-full-check-memory-fixture",
            daemon=True,
        )
        thread.start()
        fake_home = self.base / "full-check-home"
        fake_bin = self.base / "full-check-bin"
        fake_bin.mkdir()
        for name in ("codex", "claude"):
            write(
                fake_bin / name,
                (
                    "#!/bin/sh\n"
                    f"if [ \"${{1:-}}\" = \"--version\" ]; then echo synthetic-{name}; "
                    "else echo ok; fi\n"
                ).encode(),
                0o755,
            )
        onnx = fake_home / ".cache/mcp_memory/onnx_models/synthetic.onnx"
        write(onnx, b"synthetic model presence marker\n")
        memory_home = self.base / "full-check-memory-home"
        full_environment = self.environment()
        full_environment.update(
            {
                "HOME": str(fake_home),
                "PATH": f"{fake_bin}:{full_environment.get('PATH', '')}",
                "MEMORY_BUS_URL": f"http://127.0.0.1:{server.server_port}",
                "MEMORY_BUS_PROJECT_TAG": "project:synthetic-full-check",
                "MCP_MEMORY_BASE_DIR": str(memory_home),
                "BUGATE_MEMORY_HOME": str(memory_home),
            }
        )
        try:
            full = subprocess.run(
                [
                    sys.executable,
                    str(runner),
                    "--mode",
                    "smoke",
                    "--timeout-seconds",
                    "120",
                ],
                cwd=project,
                env=full_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=180,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(
            full.returncode,
            0,
            f"stdout={full.stdout}\nstderr={full.stderr}",
        )
        self.assertIn("| Imported installed-state verification | PASS |", full.stdout)
        self.assertIn("Strict Memory exact-ID verification and closed chain | PASS", full.stdout)
        self.assertIn("Result: PASS", full.stdout)
        with server.state.lock:
            records = copy.deepcopy(server.state.records)
            calls = list(server.state.calls)
        transitions = {
            identity: record["metadata"]["role_transition"]
            for identity, record in records.items()
            if isinstance(record.get("metadata"), dict)
            and isinstance(record["metadata"].get("role_transition"), dict)
        }
        expected_events = [
            "evidence_recovery",
            "human_acceptance",
            "designer_handoff",
            "implementer_acceptance",
            "implementer_handoff",
            "reviewer_acceptance",
            "reviewer_completion",
        ]
        self.assertEqual(len(transitions), 7)
        self.assertCountEqual(
            expected_events,
            [str(transition.get("event") or "") for transition in transitions.values()],
        )
        for identity in sorted(transitions):
            trace = [
                (method, path)
                for method, path, call_identity in calls
                if call_identity == identity
            ]
            self.assertTrue(trace, identity)
            self.assertEqual(trace[0], ("POST", "/api/memories"), trace)
            put_positions = [
                index for index, (method, _path) in enumerate(trace) if method == "PUT"
            ]
            self.assertEqual(len(put_positions), 1, trace)
            put_index = put_positions[0]
            exact_path = f"/api/memories/{identity}"
            self.assertGreaterEqual(
                sum(
                    method == "GET" and path == exact_path
                    for method, path in trace[:put_index]
                ),
                2,
                trace,
            )
            # One exact GET verifies the metadata bind immediately after PUT;
            # another comes from the final strict receipt-chain verification.
            self.assertGreaterEqual(
                sum(
                    method == "GET" and path == exact_path
                    for method, path in trace[put_index + 1 :]
                ),
                2,
                trace,
            )


if __name__ == "__main__":
    unittest.main()
