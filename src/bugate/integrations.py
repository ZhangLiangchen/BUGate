"""Explicit, ownership-scoped DeepSeek Harness installation lifecycle.

These are installation records, never test quality or execution evidence. Host
package operations use DSH's public CLI; configuration edits preserve all bytes
outside a uniquely owned block. Python dependencies come from the checksum-inventoried
wheelhouse shipped inside the prebuilt bundle (no editable install).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid

PACKAGE = "@bugate/deepseek-harness"
HOST_VERSION = "0.2.2-alpha.2"
STATE_VERSION = "bugate.integration/v1"
MAX_OUTPUT = 1024 * 1024


class IntegrationError(Exception):
    def __init__(self, code, message, exit_code=1, details=None):
        super().__init__(message)
        self.code, self.message, self.exit_code, self.details = code, message, exit_code, details


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError) as exc:
        raise IntegrationError("INTEGRATION_INVALID", f"Cannot read {path}: {exc}") from exc


def _atomic(path: Path, data: bytes):
    _safe(path.parent, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise IntegrationError("PATH_ESCAPE", f"Refusing symlink: {path}", 2)
    fd, name = tempfile.mkstemp(prefix=".bugate-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _write(path: Path, data: dict):
    _atomic(path, (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())


def _safe(root: Path, path: Path) -> Path:
    """Reject symlinks below a caller-approved canonical root, including leaves."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise IntegrationError("PATH_ESCAPE", f"Outside approved root: {path}", 2) from exc
    cursor = root
    for part in relative.parts:
        if part in {"..", "."}:
            raise IntegrationError("PATH_ESCAPE", "Non-canonical path", 2)
        cursor /= part
        if cursor.is_symlink():
            raise IntegrationError("PATH_ESCAPE", f"Symlink in installation path: {cursor}", 2)
    return path


@contextlib.contextmanager
def _lock(root: Path):
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".integration.lock"
    _safe(root, path)
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IntegrationError("CONCURRENT_MODIFICATION", "Another integration operation owns the lock", 2) from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _run(argv: list[str], *, cwd: Path, env: dict, timeout=180, limit=MAX_OUTPUT):
    """Bound output/time and kill the owned process group on timeout/cancel."""
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise IntegrationError("DEPENDENCY_MISSING", f"Cannot start {argv[0]}: {exc}") from exc
    chunks = [bytearray(), bytearray()]
    too_large = threading.Event()
    def drain(pipe, index):
        try:
            while True:
                block = pipe.read(8192)
                if not block:
                    break
                if len(chunks[index]) + len(block) > limit:
                    too_large.set()
                    break
                chunks[index].extend(block)
        finally:
            pipe.close()
    readers = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    end = time.monotonic() + timeout
    error = None
    try:
        while process.poll() is None:
            if too_large.is_set():
                error = IntegrationError("OUTPUT_LIMIT", f"Output exceeded {limit} bytes")
                break
            if time.monotonic() >= end:
                error = IntegrationError("PYTHON_TIMEOUT", f"Command exceeded {timeout}s", 124)
                break
            time.sleep(0.02)
    except KeyboardInterrupt:
        error = IntegrationError("CANCELLED", "Installation cancelled", 130)
    finally:
        if error is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        for reader in readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            # A descendant inherited stdout/stderr after the direct process
            # exited. It remains in our process group and must not survive.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            for reader in readers:
                reader.join(timeout=1)
            if error is None:
                error = IntegrationError("OUTPUT_LIMIT", "Subprocess descendants kept output pipes open; process group terminated")
    if error is not None:
        raise error
    if too_large.is_set() or any(reader.is_alive() for reader in readers):
        raise IntegrationError("OUTPUT_LIMIT", "Output drain failed or exceeded limit")
    stdout, stderr = (bytes(value).decode("utf-8", "replace") for value in chunks)
    if process.returncode:
        raise IntegrationError("HOST_COMMAND_FAILED", f"Command failed ({process.returncode}): {argv[0]}",
                               details={"argv": argv, "returncode": process.returncode,
                                        "stdout": stdout[-8192:], "stderr": stderr[-8192:]})
    return stdout, stderr


def _paths(project, profile, dsh_home=None):
    root = Path(project).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise IntegrationError("INVALID_INPUT", "Project must be a directory", 2)
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", profile) or profile == "node_modules":
        raise IntegrationError("INVALID_INPUT", "Invalid DSH profile name", 2)
    home = Path(dsh_home or os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))).expanduser().resolve()
    state = _safe(root, root / ".bugate" / "integrations" / "deepseek-harness" / profile)
    profile_dir = _safe(home, home / "profiles" / profile)
    return root, home, state, profile_dir


def _environment(home):
    env = os.environ.copy()
    env["DSH_HOME"] = str(home)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _host(dsh, root, env):
    executable = shutil.which(str(dsh))
    if not executable:
        raise IntegrationError("HOST_MISSING", f"DSH executable not found: {dsh}")
    version, _ = _run([executable, "--version"], cwd=root, env=env, timeout=15)
    version = version.strip()
    if version != HOST_VERSION:
        raise IntegrationError("HOST_VERSION_UNSUPPORTED", f"Expected DSH {HOST_VERSION}; found {version}", 2)
    return executable, version


def _unpack(package: Path, destination: Path) -> dict:
    """Only regular package files/directories; bounded extraction, no links."""
    total = 0
    seen = set()
    try:
        with tarfile.open(package, "r:gz") as archive:
            for member in archive:
                parts = Path(member.name).parts
                if not parts or parts[0] != "package" or "\\" in member.name or any(x in {"..", "."} for x in member.name.rstrip("/").split("/")) or Path(member.name).is_absolute():
                    raise IntegrationError("PACKAGE_INVALID", f"Unsafe archive entry: {member.name}", 2)
                relative = Path(*parts[1:])
                if not relative.parts:
                    continue
                if str(relative) in seen:
                    raise IntegrationError("PACKAGE_INVALID", f"Duplicate archive entry: {member.name}", 2)
                seen.add(str(relative))
                target = _safe(destination, destination / relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.size > 100 * 1024 * 1024:
                    raise IntegrationError("PACKAGE_INVALID", f"Unsupported archive entry: {member.name}", 2)
                total += member.size
                if total > 300 * 1024 * 1024:
                    raise IntegrationError("PACKAGE_INVALID", "Archive exceeds extraction budget", 2)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise IntegrationError("PACKAGE_INVALID", "Archive member is unreadable", 2)
                target.write_bytes(source.read())
    except (OSError, tarfile.TarError) as exc:
        raise IntegrationError("PACKAGE_INVALID", str(exc), 2) from exc
    manifest = _json(destination / "package.json")
    if manifest.get("name") != PACKAGE or manifest.get("dsh", {}).get("bundle", {}).get("patch") != "./cordis.patch.yml":
        raise IntegrationError("PACKAGE_INVALID", "Expected BUGate native bundle metadata", 2)
    if manifest.get("engines", {}).get("dsh") != HOST_VERSION:
        raise IntegrationError("HOST_VERSION_UNSUPPORTED", "Bundle target DSH version differs", 2)
    package_inventory = _json(destination / "inventory.json").get("files")
    if not isinstance(package_inventory, dict) or not package_inventory:
        raise IntegrationError("RESOURCES_MISSING", "Bundle inventory is empty")
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    if actual != set(package_inventory) | {"inventory.json"}:
        raise IntegrationError("RESOURCE_DIGEST_MISMATCH", "Bundle membership differs from inventory")
    for name, digest in package_inventory.items():
        target = _safe(destination, destination / name)
        if not target.is_file() or _hash(target.read_bytes()) != digest:
            raise IntegrationError("RESOURCE_DIGEST_MISMATCH", f"Bundle checksum mismatch: {name}")
    inventory = _json(destination / "python" / "inventory.json")
    files = inventory.get("files")
    if not isinstance(files, dict) or not files:
        raise IntegrationError("RESOURCES_MISSING", "Wheel inventory is empty")
    for name, digest in files.items():
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name):
            raise IntegrationError("PACKAGE_INVALID", "Invalid wheel filename", 2)
        path = destination / "python" / "wheels" / name
        if not path.is_file() or _hash(path.read_bytes()) != digest:
            raise IntegrationError("RESOURCE_DIGEST_MISMATCH", f"Wheel checksum mismatch: {name}")
    if set(path.name for path in (destination / "python" / "wheels").glob("*")) != set(files):
        raise IntegrationError("RESOURCE_DIGEST_MISMATCH", "Wheel inventory differs from package")
    if not (destination / "lib" / "index.js").is_file():
        raise IntegrationError("RESOURCES_MISSING", "Compiled native plugin is missing")
    return manifest


def _bundle_core_version(bundle):
    """Each v2 archive pins its own core; upgrade/rollback never reinterpret facts."""
    compatibility = _json(bundle / "resources" / "compatibility.json")
    version = compatibility.get("core_version")
    protocol_version = compatibility.get("protocol_version")
    supported = {"2.0.0": {"2.0.0a1", "2.0.0a2"},
                 "2.1.0-dev.1": {"2.1.0a1"}}
    if (not isinstance(version, str) or not re.fullmatch(r"2\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version)
            or not isinstance(protocol_version, str)
            or version not in supported.get(protocol_version, set())
            or compatibility.get("dsh_version") != HOST_VERSION):
        raise IntegrationError("VERSION_MISMATCH", "Bundle must pin a supported v2 core/protocol and exact DSH version", 2)
    return version


def _marker(root):
    return _hash(str(root).encode())[:16]


def _block(root, python, task_path=None):
    config = {"projectRoot": str(root), "pythonExecutable": str(python)}
    if task_path is not None:
        config["taskPath"] = str(task_path)
    marker = _marker(root)
    return (f"# BEGIN BUGATE {marker}\n- id: bugate\n  config: " + json.dumps(config, ensure_ascii=False) +
            f"\n# END BUGATE {marker}\n").encode()


def _replace_block(current: bytes, root: Path, new: bytes, expected: str | None):
    marker = _marker(root).encode()
    begin = b"# BEGIN BUGATE " + marker + b"\n"
    end = b"# END BUGATE " + marker + b"\n"
    if begin in current:
        if current.count(begin) != 1 or current.count(end) != 1:
            raise IntegrationError("INTEGRATION_CONFLICT", "Duplicate/malformed owned configuration block")
        start = current.index(begin)
        stop = current.index(end, start) + len(end)
        old = current[start:stop]
        if expected is None or _hash(old) != expected:
            raise IntegrationError("INTEGRATION_CONFLICT", "Owned configuration changed; refusing to overwrite")
        return current[:start] + new + current[stop:]
    if expected is not None:
        raise IntegrationError("INTEGRATION_CONFLICT", "Previously installed configuration block is missing")
    if b"# BEGIN BUGATE " in current:
        raise IntegrationError("INTEGRATION_CONFLICT", "This DSH profile is bound to another BUGate project", 2)
    # Default DSH patch is comments followed by []; replace only that empty list.
    meaningful = b"\n".join(line for line in current.splitlines() if line.strip() and not line.lstrip().startswith(b"#"))
    if meaningful.strip() == b"[]":
        current = b"\n".join(line for line in current.splitlines() if line.strip() != b"[]") + b"\n"
    return current + (b"\n" if current and not current.endswith(b"\n") else b"") + new


def _check_wheel_target(python, inventory, root, env):
    code = "import json,platform,sys,sysconfig;print(json.dumps({'python':sys.version.split()[0],'platform':sys.platform,'machine':platform.machine(),'platform_tag':sysconfig.get_platform(),'soabi':sysconfig.get_config_var('SOABI')}))"
    stdout, _ = _run([str(python), "-c", code], cwd=root, env=env, timeout=15)
    try:
        target = json.loads(stdout)
    except ValueError as exc:
        raise IntegrationError("PYTHON_INVALID_RESPONSE", "Cannot determine Python wheel compatibility") from exc
    if any(not name.endswith("-any.whl") for name in inventory["files"]):
        if any(inventory.get(key) != target.get(key) for key in ("platform", "machine", "platform_tag", "soabi")):
            raise IntegrationError("PYTHON_ABI_UNSUPPORTED", "This offline wheelhouse was built for a different Python ABI/platform", 2,
                                   {"supported": {key: value for key, value in inventory.items() if key != "files"}, "actual": target})
    return target


def _python_health(python, root, env, expected_version):
    code = "import importlib.metadata,json; from importlib.resources import files; p=files('bugate').joinpath('resources/protocol/v2'); print(json.dumps({'version':importlib.metadata.version('bugate-core'),'protocol':str(p),'resources':p.joinpath('schemas/prepared_protocol_context.schema.json').is_file()}))"
    stdout, _ = _run([str(python), "-c", code], cwd=root, env=env, timeout=20)
    try:
        value = json.loads(stdout)
        if value.get("version") != expected_version or not value.get("resources"):
            raise ValueError("core version/resources mismatch")
    except (ValueError, AttributeError) as exc:
        raise IntegrationError("PYTHON_INVALID_RESPONSE", "Python package handshake failed") from exc
    return value


def setup(project, dsh_profile, *, package=None, dsh="dsh", dsh_home=None, python=None, task_path=None):
    root, home, state, profile_dir = _paths(project, dsh_profile, dsh_home)
    if task_path is not None:
        requested_task = Path(task_path)
        selected_task = _safe(root, requested_task if requested_task.is_absolute() else root / requested_task)
        if not _safe(root, selected_task / ".bugate" / "protocol.lock.json").is_file():
            raise IntegrationError("BINDING_MISMATCH", "Selected task has no exact protocol binding", 2)
        task_path = str(selected_task)
    if package is None:
        raise IntegrationError("PACKAGE_REQUIRED", "Provide --package with the prebuilt BUGate .tgz", 2)
    source = Path(package).expanduser().resolve(strict=True)
    if not source.is_file():
        raise IntegrationError("PACKAGE_INVALID", "Package must be a prebuilt .tgz", 2)
    package_digest = _hash(source.read_bytes())
    env = _environment(home)
    executable, host_version = _host(dsh, root, env)
    with _lock(state):
        if (state / "pending.json").exists():
            raise IntegrationError("PENDING_RECOVERY_REQUIRED", "An interrupted operation requires remove --rollback reconciliation", 2)
        record_path = state / "installed.json"
        existing = _json(record_path) if record_path.exists() else None
        if existing and existing.get("dsh_home") != str(home):
            raise IntegrationError("INTEGRATION_CONFLICT", "Existing integration belongs to another DSH home", 2)
        if existing and existing.get("package_digest") == package_digest:
            if existing.get("task_path") != task_path:
                raise IntegrationError("BINDING_MISMATCH", "Same package is bound to another task path; remove before rebinding", 2)
            result = doctor(root, dsh_profile=dsh_profile, dsh=executable, dsh_home=home)
            return {"status": "unchanged", "integration": existing, "doctor": result}
        profile_patch = _safe(home, profile_dir / "cordis.patch.yml")
        pre_patch = profile_patch.read_bytes() if profile_patch.exists() else b""
        if existing:
            _replace_block(pre_patch, root, b"", existing["block_digest"])
        elif profile_patch.exists() and b"# BEGIN BUGATE " in pre_patch:
            raise IntegrationError("INTEGRATION_CONFLICT", "Profile already owns a BUGate block", 2)
        profile_manifest = profile_dir / "package.json"
        if not existing and profile_manifest.exists() and PACKAGE in _json(profile_manifest).get("dependencies", {}):
            raise IntegrationError("INTEGRATION_CONFLICT", "Existing package is not owned by this setup; refusing adoption", 2)
        runtime = _safe(root, state / "runtime" / package_digest)
        runtime.mkdir(parents=True, exist_ok=True)
        archive = runtime / "bundle.tgz"
        if not archive.exists():
            _atomic(archive, source.read_bytes())
        bundle = runtime / "bundle"
        bundle.mkdir(exist_ok=True)
        manifest = _unpack(archive, bundle)
        core_version = _bundle_core_version(bundle)
        _check_wheel_target(python or sys.executable, _json(bundle / "python" / "inventory.json"), root, env)
        venv = _safe(root, runtime / "venv")
        python_bin = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python_bin.exists():
            _run([python or sys.executable, "-m", "venv", str(venv)], cwd=root, env=env)
            wheels = bundle / "python" / "wheels"
            _run([str(python_bin), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), f"bugate-core=={core_version}"], cwd=root, env=env)
        health = _python_health(python_bin, root, env, core_version)
        block = _block(root, python_bin, task_path)
        prior = {key: value for key, value in existing.items() if key != "previous"} if existing else None
        record = {"apiVersion": STATE_VERSION, "project": str(root), "dsh_home": str(home),
                  "profile": dsh_profile, "package_name": PACKAGE, "package_version": manifest["version"],
                  "package_digest": package_digest, "archive": str(archive), "python": str(python_bin),
                  "host_version": host_version, "block_digest": _hash(block), "task_path": task_path,
                  "previous": prior, "python_health": health, "core_version": core_version}
        journal = {"apiVersion": STATE_VERSION, "operation": "setup", "status": "pending", "phase": "host_add_started",
                   "target": record, "previous": prior, "profile": dsh_profile, "dsh_home": str(home), "project": str(root)}
        _write(state / "pending.json", journal)
        _run([executable, "plugin", "--profile", dsh_profile, "add", str(archive)], cwd=root, env=env)
        journal["phase"] = "host_added"
        _write(state / "pending.json", journal)
        _safe(home, profile_patch)
        current = profile_patch.read_bytes() if profile_patch.exists() else b""
        # Existing outside-block edits are retained; an owned-block edit conflicts.
        configured = _replace_block(current, root, block, existing["block_digest"] if existing else None)
        journal["phase"] = "patch_write_started"
        _write(state / "pending.json", journal)
        _atomic(profile_patch, configured)
        journal["phase"] = "patch_written"
        _write(state / "pending.json", journal)
        try:
            dump, _ = _run([executable, "--profile", dsh_profile, "--dump-config"], cwd=root, env=env)
            if PACKAGE not in dump or str(python_bin) not in dump:
                raise IntegrationError("CONTEXT_NOT_ENABLED", "Composed DSH configuration lacks BUGate binding")
        except IntegrationError:
            # Restore only our owned block, and keep journal for explicit recovery.
            restored = _replace_block(profile_patch.read_bytes(), root,
                                      _block(root, Path(existing["python"]), existing.get("task_path")) if existing else b"",
                                      _hash(block))
            _atomic(profile_patch, restored)
            raise
        journal["phase"] = "commit_started"
        _write(state / "pending.json", journal)
        _write(record_path, record)
        _write(state / "last-operation.json", {"operation": "setup", "status": "complete", "package_digest": package_digest})
        (state / "pending.json").unlink()
        return {"status": "upgraded" if existing else "installed", "integration": record,
                "verification_level": "package_and_configuration; runtime hydration requires an agent session"}


def doctor(project, *, dsh_profile=None, dsh="dsh", dsh_home=None):
    root = Path(project).expanduser().resolve(strict=True)
    if dsh_profile is None:
        base = _safe(root, root / ".bugate" / "integrations" / "deepseek-harness")
        names = sorted(p.parent.name for p in base.glob("*/installed.json")) if base.exists() else []
        if not names:
            return {"host": "present" if shutil.which(str(dsh)) else "missing", "selected": False,
                    "status": "not_selected", "integrations": []}
        values = [doctor(root, dsh_profile=name, dsh=dsh, dsh_home=dsh_home) for name in names]
        return {"status": "healthy", "integrations": values}
    root, home, state, profile_dir = _paths(root, dsh_profile, dsh_home)
    env = _environment(home)
    executable, version = _host(dsh, root, env)
    record_path = _safe(root, state / "installed.json")
    if not record_path.exists():
        raise IntegrationError("INTEGRATION_MISSING", f"No owned integration record for {dsh_profile}")
    record = _json(record_path)
    if record.get("project") != str(root) or record.get("dsh_home") != str(home):
        raise IntegrationError("BINDING_MISMATCH", "Integration identity differs from requested project/home", 2)
    archive = _safe(root, Path(record["archive"]))
    if not archive.is_file() or _hash(archive.read_bytes()) != record["package_digest"]:
        raise IntegrationError("RESOURCE_DIGEST_MISMATCH", "Installed archive is absent or modified")
    patch = _safe(home, profile_dir / "cordis.patch.yml")
    if not patch.exists():
        raise IntegrationError("CONTEXT_NOT_ENABLED", "DSH profile patch is missing")
    _replace_block(patch.read_bytes(), root, b"", record["block_digest"])
    installed = _json(profile_dir / "node_modules" / "@bugate" / "deepseek-harness" / "package.json")
    if installed.get("name") != PACKAGE or installed.get("version") != record["package_version"]:
        raise IntegrationError("PROJECTION_STALE", "Installed plugin version differs from owned record")
    with tempfile.TemporaryDirectory(prefix="bugate-doctor-") as directory:
        expected_bundle = Path(directory)
        _unpack(archive, expected_bundle)
        package_inventory = _json(expected_bundle / "inventory.json")["files"]
        core_version = _bundle_core_version(expected_bundle)
    installed_bundle = profile_dir / "node_modules" / "@bugate" / "deepseek-harness"
    for relative, digest in package_inventory.items():
        target = installed_bundle / relative
        if not target.is_file() or _hash(target.read_bytes()) != digest:
            raise IntegrationError("PROJECTION_STALE", f"Installed projection differs: {relative}")
    health = _python_health(record["python"], root, env, core_version)
    dump, _ = _run([executable, "--profile", dsh_profile, "--dump-config"], cwd=root, env=env)
    if PACKAGE not in dump or record["python"] not in dump:
        raise IntegrationError("CONTEXT_NOT_ENABLED", "Composed config does not include the owned adapter")
    bindings = []
    tasks = sorted((root / "test-task").glob("*/task.json")) if (root / "test-task").is_dir() else []
    for task_file in tasks:
        stdout, _ = _run([record["python"], "-m", "bugate", "protocol", "prepare", "--task", str(task_file.parent), "--json"], cwd=root, env=env)
        response = json.loads(stdout)
        bindings.append({"task": task_file.parent.name, "status": "valid", "context": response.get("data")})
    return {"status": "healthy", "host": "present", "host_version": version, "plugin": "valid",
            "projection": "current", "python": health, "bindings": bindings,
            "hydration": "unobserved", "runtime": "configuration_verified", "profile": dsh_profile}


def _recover(root, home, state, profile_dir, profile, dsh):
    """Explicitly reconcile the original journal; never blindly repeat setup."""
    env = _environment(home)
    executable, _ = _host(dsh, root, env)
    with _lock(state):
        journal = _json(state / "pending.json")
        if journal.get("project") != str(root) or journal.get("dsh_home") != str(home) or journal.get("profile") != profile:
            raise IntegrationError("BINDING_MISMATCH", "Recovery journal identity differs", 2)
        previous, target = journal.get("previous"), journal.get("target")
        patch = _safe(home, profile_dir / "cordis.patch.yml")
        current = patch.read_bytes() if patch.exists() else b""
        marker = ("# BEGIN BUGATE " + _marker(root) + "\n").encode()
        if marker in current:
            candidates = [item for item in (target, previous) if item]
            matched = None
            for candidate in candidates:
                try:
                    stripped = _replace_block(current, root, b"", candidate["block_digest"])
                    matched = candidate
                    break
                except IntegrationError:
                    continue
            if matched is None:
                raise IntegrationError("INTEGRATION_CONFLICT", "Recovery refuses a modified owned patch block")
        else:
            stripped = current
        manifest_path = profile_dir / "package.json"
        manifest = _json(manifest_path) if manifest_path.exists() else {}
        dependency = manifest.get("dependencies", {}).get(PACKAGE)
        if dependency:
            allowed = [item["archive"] for item in (target, previous) if item]
            if not any(str(dependency) in {value, "file:" + value} for value in allowed):
                raise IntegrationError("INTEGRATION_CONFLICT", "Package dependency changed since the interrupted operation", 2)
        if previous:
            archive = _safe(root, Path(previous["archive"]))
            if not archive.is_file() or _hash(archive.read_bytes()) != previous["package_digest"]:
                raise IntegrationError("RESOURCE_DIGEST_MISMATCH", "Recovery archive is absent or modified")
            _run([executable, "plugin", "--profile", profile, "add", str(archive)], cwd=root, env=env)
            restored_block = _block(root, previous["python"], previous.get("task_path"))
            restored = _replace_block(stripped, root, restored_block, None)
            _atomic(patch, restored)
            _write(state / "installed.json", previous)
        else:
            if dependency:
                _run([executable, "plugin", "--profile", profile, "remove", PACKAGE], cwd=root, env=env)
            if not [line for line in stripped.splitlines() if line.strip() and not line.lstrip().startswith(b"#")]:
                stripped += b"[]\n"
            if patch.exists():
                _atomic(patch, stripped)
            (state / "installed.json").unlink(missing_ok=True)
        _write(state / "recovery.json", {"operation": "rollback", "status": "complete", "journal": journal})
        (state / "pending.json").unlink()
        return {"status": "recovered", "restored": "previous" if previous else "uninstalled", "profile": profile}


def remove(project, dsh_profile, *, dsh="dsh", dsh_home=None, rollback=False):
    root, home, state, profile_dir = _paths(project, dsh_profile, dsh_home)
    record_path = state / "installed.json"
    if (state / "pending.json").exists():
        if not rollback:
            raise IntegrationError("PENDING_RECOVERY_REQUIRED", "Use remove --rollback to reconcile the interrupted installation", 2)
        return _recover(root, home, state, profile_dir, dsh_profile, dsh)
    if not record_path.exists():
        return {"status": "absent", "profile": dsh_profile}
    record = _json(record_path)
    if rollback and record.get("previous"):
        previous = record["previous"]
        # setup performs a checked package/patch replacement; retained archive is immutable.
        result = setup(root, dsh_profile, package=previous["archive"], dsh=dsh, dsh_home=home,
                       task_path=previous.get("task_path"))
        with _lock(state):
            restored = _json(record_path)
            restored["previous"] = None
            _write(record_path, restored)
        return {"status": "rolled_back", "integration": restored}
    env = _environment(home)
    executable, _ = _host(dsh, root, env)
    with _lock(state):
        record = _json(record_path)
        if record.get("dsh_home") != str(home) or record.get("project") != str(root):
            raise IntegrationError("BINDING_MISMATCH", "Integration target differs", 2)
        patch = _safe(home, profile_dir / "cordis.patch.yml")
        current = patch.read_bytes()
        new = _replace_block(current, root, b"", record["block_digest"])
        meaningful = [line for line in new.splitlines() if line.strip() and not line.lstrip().startswith(b"#")]
        if not meaningful:
            new += b"[]\n"
        _write(state / "pending.json", {"operation": "remove", "previous": record, "status": "pending", "phase": "remove_started",
                                             "project": str(root), "dsh_home": str(home), "profile": dsh_profile})
        _atomic(patch, new)
        try:
            _run([executable, "plugin", "--profile", dsh_profile, "remove", PACKAGE], cwd=root, env=env)
        except IntegrationError:
            _atomic(patch, current)
            raise
        _write(state / "removed.json", record)
        record_path.unlink()
        (state / "pending.json").unlink()
        return {"status": "rolled_back" if rollback else "removed", "profile": dsh_profile,
                "preserved": ["user configuration", "other plugins", "task facts", "recovery archives"]}
