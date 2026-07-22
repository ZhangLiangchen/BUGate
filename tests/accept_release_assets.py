#!/usr/bin/env python3
"""Archive-native acceptance for one formal BUGate release asset set.

This is a release gate, not a fixture generator for a real SUT.  It consumes
the tar, zip, and SHA256SUMS produced by ``build_release_archives.py`` and
creates two entirely synthetic imported repositories beneath one
``TemporaryDirectory``.  The v0.3.2 baseline is reconstructed from BUGate's
annotated Core tag plus the release's bound legacy manifest; no external SUT
repository is read, copied, cloned, or mounted.

The final stdout document is always machine-readable JSON.  Exit status 0 is
reserved for a complete GO; validation, update, rollback, or smoke failures
return 1, while malformed CLI arguments return 2.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import http.server
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402
import bugate_legacy_manifest as legacy_contract  # noqa: E402
import bugate_update_engine as engine  # noqa: E402
import bugate_update_source as update_source  # noqa: E402


SCHEMA_VERSION = 1
LEGACY_TAG = "v0.3.2"
VENDOR_DIR = ".bugate"
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
HEX_TRANSACTION = re.compile(r"[0-9a-f]{32}")
FORBIDDEN_ARCHIVE_NAMES = {
    "__pycache__",
    ".DS_Store",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    ".env",
    "id_rsa",
    "id_ed25519",
}
SECRET_PATTERNS = (
    re.compile(br"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(br"AKIA[0-9A-Z]{16}"),
    re.compile(br"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(br"sk-[A-Za-z0-9]{20,}"),
)


class AcceptanceError(RuntimeError):
    """A fail-closed release-acceptance failure."""


class ArgumentError(AcceptanceError):
    """A stable machine-readable argument failure."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_input(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_file():
        raise AcceptanceError(f"{label} must be a regular, non-symlink file")
    return expanded.resolve(strict=True)


def _parse_checksums(
    checksums: Path,
    assets: Sequence[Path],
) -> dict[str, str]:
    """Require the exact two public archive checksums without ambiguity."""

    try:
        lines = checksums.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise AcceptanceError("checksum asset is not readable strict ASCII") from exc
    records: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\s]+)", line)
        if match is None:
            raise AcceptanceError("checksum asset contains a malformed or unsafe line")
        digest, name = match.groups()
        if name in records:
            raise AcceptanceError(f"checksum asset repeats filename: {name}")
        records[name] = digest
    expected = {path.name for path in assets}
    if set(records) != expected or len(records) != 2:
        raise AcceptanceError(
            "checksum asset must name exactly the supplied tar and zip archives"
        )
    for asset in assets:
        actual = _sha256(asset)
        if records[asset.name] != actual:
            raise AcceptanceError(f"checksum mismatch: {asset.name}")
    return records


def _git(repo: Path, *arguments: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )


def _verify_annotated_legacy_tag(repo: Path, manifest: Mapping[str, Any]) -> str:
    kind = _git(repo, "cat-file", "-t", f"refs/tags/{LEGACY_TAG}")
    if kind.returncode != 0 or kind.stdout.strip() != "tag":
        raise AcceptanceError(f"{LEGACY_TAG} is not an available annotated Core tag")
    peeled = _git(repo, "rev-parse", f"{LEGACY_TAG}^{{commit}}")
    commit = peeled.stdout.strip() if peeled.returncode == 0 else ""
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise AcceptanceError(f"cannot peel {LEGACY_TAG} to a Core commit")
    if manifest.get("source_tag") != LEGACY_TAG:
        raise AcceptanceError("release legacy manifest source tag mismatch")
    if manifest.get("source_commit") != commit:
        raise AcceptanceError("release legacy manifest is not bound to the formal tag commit")
    return commit


def _read_updater_version(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise AcceptanceError("archive bootstrap updater cannot be parsed") from exc
    values: list[Any] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "UPDATER_VERSION"
            for target in targets
        ):
            try:
                values.append(ast.literal_eval(value))
            except (TypeError, ValueError) as exc:
                raise AcceptanceError("archive updater version is not a literal") from exc
    if len(values) != 1:
        raise AcceptanceError("archive updater must declare exactly one version")
    try:
        return contract.validate_semver(values[0])
    except contract.ContractError as exc:
        raise AcceptanceError(str(exc)) from exc


def _verify_release_identity(
    root: Path,
    manifest: Mapping[str, Any],
    version: str,
) -> None:
    if manifest.get("bugate_version") != version:
        raise AcceptanceError("release manifest version differs from requested version")
    if manifest.get("archive_prefix") != f"bugate-{version}":
        raise AcceptanceError("release manifest archive prefix/version mismatch")
    plugin_versions: dict[str, Any] = {}
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        try:
            document = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AcceptanceError(f"archive plugin manifest is unreadable: {relative}") from exc
        plugin_versions[relative] = document.get("version")
    if any(value != version for value in plugin_versions.values()):
        raise AcceptanceError("archive plugin manifests disagree with target version")
    if _read_updater_version(root / "scripts/bugate_update.py") != version:
        raise AcceptanceError("archive updater version differs from target version")
    wrapper = root / "bin/bugate-update"
    if (
        wrapper.is_symlink()
        or not wrapper.is_file()
        or stat.S_IMODE(os.lstat(wrapper).st_mode) != 0o755
        or wrapper.read_bytes() != contract.BUGATE_UPDATE_WRAPPER_BYTES
    ):
        raise AcceptanceError("archive updater wrapper contract or executable mode mismatch")


def _scan_release_tree(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    machine_markers: Iterable[bytes],
) -> dict[str, Any]:
    """Reject caches, credentials, and paths from the accepting machine."""

    scanned_files = 0
    scanned_bytes = 0
    markers = tuple(marker for marker in machine_markers if len(marker) >= 6)
    for item in manifest.get("archive_inventory", []):
        relative = str(item.get("path", ""))
        components = Path(relative).parts
        lowered = {part.casefold() for part in components}
        if any(name.casefold() in lowered for name in FORBIDDEN_ARCHIVE_NAMES):
            raise AcceptanceError(f"archive contains forbidden cache/secret path: {relative}")
        if any(part.endswith(".pyc") for part in components):
            raise AcceptanceError(f"archive contains Python cache bytecode: {relative}")
        if components and components[0] in {".git", "dist", ".bugate-update"}:
            raise AcceptanceError(f"archive contains forbidden local state: {relative}")
        if item.get("type") != "file":
            continue
        path = root / relative
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise AcceptanceError(f"cannot scan archive file: {relative}") from exc
        scanned_files += 1
        scanned_bytes += len(payload)
        if any(pattern.search(payload) for pattern in SECRET_PATTERNS):
            raise AcceptanceError(f"archive contains credential-shaped payload: {relative}")
        if any(marker in payload for marker in markers):
            raise AcceptanceError(f"archive embeds an accepting-machine path: {relative}")
    return {
        "status": "passed",
        "files_scanned": scanned_files,
        "bytes_scanned": scanned_bytes,
        "cache_secret_machine_path_findings": 0,
    }


def _path_image(path: Path) -> tuple[str, bytes | str, int]:
    if not (path.exists() or path.is_symlink()):
        return ("absent", b"", 0)
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode):
        return ("symlink", os.readlink(path), stat.S_IMODE(details.st_mode))
    if stat.S_ISDIR(details.st_mode):
        return ("directory", b"", stat.S_IMODE(details.st_mode))
    if stat.S_ISREG(details.st_mode):
        return ("file", path.read_bytes(), stat.S_IMODE(details.st_mode))
    return ("special", b"", stat.S_IMODE(details.st_mode))


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    result: dict[str, tuple[str, bytes | str, int]] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            path = current_path / name
            result[path.relative_to(root).as_posix()] = _path_image(path)
            if not path.is_symlink():
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = current_path / name
            result[path.relative_to(root).as_posix()] = _path_image(path)
    return result


def _selected_snapshot(
    root: Path,
    relatives: Iterable[str],
) -> dict[str, tuple[str, bytes | str, int] | dict[str, tuple[str, bytes | str, int]]]:
    result: dict[
        str,
        tuple[str, bytes | str, int] | dict[str, tuple[str, bytes | str, int]],
    ] = {}
    for relative in relatives:
        path = root / relative
        result[relative] = (
            _tree_snapshot(path)
            if path.is_dir() and not path.is_symlink()
            else _path_image(path)
        )
    return result


def _projection_snapshot(
    project: Path,
    projection: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[str, bytes | str, int]]:
    return {
        relative: _path_image(project / relative)
        for relative in sorted({str(item["target_path"]) for item in projection})
    }


def _write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, mode)


def _materialize_legacy(
    repo: Path,
    project: Path,
    legacy_manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Build only the manifest-declared v0.3.2 imported layout from Core."""

    archived = _git(repo, "archive", "--format=tar", LEGACY_TAG, binary=True)
    if archived.returncode != 0:
        diagnostic = archived.stderr.decode("utf-8", "replace").strip()
        raise AcceptanceError(f"cannot read formal legacy Core archive: {diagnostic}")
    projection = engine.render_legacy_projection(legacy_manifest, VENDOR_DIR)
    with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
        members = {
            member.name[:-1]
            if member.isdir() and member.name.endswith("/")
            else member.name: member
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
            target = project / str(item["target_path"])
            source_path = str(item["source_path"])
            member = members.get(source_path)
            if member is None:
                raise AcceptanceError(f"formal tag lacks legacy source: {source_path}")
            if item["type"] == "directory":
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, int(str(item["mode"]), 8))
            elif item["type"] == "symlink":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(str(item["target"]))
            elif item["type"] == "file":
                stream = archive.extractfile(member)
                if stream is None:
                    raise AcceptanceError(f"legacy file payload is unavailable: {source_path}")
                payload = stream.read()
                if contract.sha256_bytes(payload) != item["sha256"]:
                    raise AcceptanceError(f"legacy tag/manifest hash mismatch: {source_path}")
                _write(target, payload, int(str(item["mode"]), 8))
            else:
                raise AcceptanceError(f"unsupported legacy projection type: {source_path}")

    hook_expectations: dict[str, dict[str, Any]] = {}
    hook_targets = sorted(
        {
            str(item["target_path"])
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
            "synthetic_owned": {"preserve": True, "order": [3, 1, 2]},
            "hooks": {},
        }
        for item in items:
            document["hooks"].setdefault(item["event"], []).append(item["value"])
        document["hooks"].setdefault("PreToolUse", []).insert(0, sut_entry)
        raw = (json.dumps(document, ensure_ascii=False, indent=4) + "\n").encode()
        raw = raw.replace(
            b'"matcher": "synthetic-owned"',
            b'"matcher" : "synthetic-owned"',
        ).replace(b'"synthetic_owned": {', b'"synthetic_owned" : {')
        _write(project / target_path, raw)
        hook_expectations[target_path] = {
            "sut_semantic_projection": _sut_hook_semantic_projection(
                raw,
                projection,
                target_path=target_path,
            ),
            "markers": (
                b'"matcher" : "synthetic-owned"',
                b'"synthetic_owned" : {',
            ),
        }

    marked = next(
        item for item in projection if item["scope"] == "marked_text_block"
    )
    _write(
        project / str(marked["target_path"]),
        (
            "# synthetic owner prefix\n/synthetic-cache/\n\n"
            + str(marked["content"])
            + "\n# synthetic owner suffix\n"
        ).encode(),
    )
    return projection, hook_expectations, marked


def _populate_sut_owned(project: Path) -> tuple[str, ...]:
    _write(
        project / "bugate.config.yaml",
        b"bugate:\n  version: '0.1'\nprofile: bugate.profile.yaml\n",
    )
    _write(
        project / "bugate.profile.yaml",
        (
            b"guarded_path_regex: []\nrole_governance:\n  mode: off\n"
            b"memory:\n  namespace: project:synthetic-release-acceptance\n"
        ),
    )
    _write(
        project / "docs/usecases/SYN-001/requirement.md",
        b"# Synthetic requirement\n",
    )
    _write(
        project / "docs/usecases/SYN-001/00_role_evidence/receipt.json",
        b'{"synthetic":"preserve"}\n',
    )
    _write(
        project / "00_role_evidence/root-receipt.json",
        b'{"synthetic":"preserve-root"}\n',
    )
    _write(
        project / "tests/test_synthetic_sut.py",
        b"def test_synthetic_sut():\n    assert True\n",
    )
    _write(project / "bin/bugate-auto", b"#!/bin/sh\nexit 0\n", 0o755)
    _write(project / "AGENTS.md", b"# Synthetic operator rules\n")
    (project / "CLAUDE.md").symlink_to("AGENTS.md")
    _write(
        project / ".memory_bus/state.json",
        b'{"namespace":"project:synthetic-release-acceptance","records":["keep"]}\n',
    )
    _write(project / ".codex/agents/sut-owned.toml", b'name = "synthetic"\n')
    _write(
        project / ".claude/skills/sut-owned/SKILL.md",
        b"# Synthetic SUT-owned skill\n",
    )
    _write(
        project / ".agents/skills/sut-owned/SKILL.md",
        b"# Synthetic SUT-owned skill\n",
    )
    _write(project / "synthetic-owned/dirty.txt", b"committed\n")
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
        ".codex/agents/sut-owned.toml",
        ".claude/skills/sut-owned",
        ".agents/skills/sut-owned",
        "synthetic-owned/dirty.txt",
    )


def _initialize_dirty_git_repo(project: Path) -> None:
    commands = (
        ("init", "-q"),
        ("add", "-A"),
        (
            "-c",
            "user.name=Synthetic Release Acceptance",
            "-c",
            "user.email=synthetic@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "synthetic v0.3.2 imported baseline",
        ),
    )
    for command in commands:
        result = _git(project, *command)
        if result.returncode != 0:
            raise AcceptanceError(
                "cannot initialize synthetic Git fixture: "
                + (result.stderr.strip() or "unknown git error")
            )
    _write(project / "synthetic-owned/dirty.txt", b"locally dirty\n")


def _assert_dirty_file(project: Path) -> None:
    result = _git(project, "status", "--short", "--", "synthetic-owned/dirty.txt")
    if result.returncode != 0 or result.stdout.strip() != "M synthetic-owned/dirty.txt":
        raise AcceptanceError("unrelated synthetic dirty file was not preserved")


def _outside_marked_block(payload: bytes, item: Mapping[str, Any]) -> bytes:
    text = payload.decode("utf-8")
    try:
        start = text.index(str(item["begin"]))
        finish = text.index(str(item["end"]), start) + len(str(item["end"]))
    except ValueError as exc:
        raise AcceptanceError("BUGate-owned gitignore marker block is missing") from exc
    if finish < len(text) and text[finish] == "\n":
        finish += 1
    return (text[:start] + text[finish:]).encode()


def _sut_hook_semantic_projection(
    raw: bytes,
    owned_projection: Iterable[Mapping[str, Any]],
    *,
    target_path: str,
) -> dict[str, Any]:
    """Remove exactly one copy of every BUGate-owned hook, then seal SUT state.

    An equality/membership-only check is insufficient: it misses duplicated
    SUT entries and can accept a second copy of an owned entry.  This helper
    therefore requires every declared BUGate semantic digest exactly once and
    preserves the remaining values, order, per-event counts, and digests as the
    SUT-owned semantic projection.
    """

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = child
        return value

    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AcceptanceError(f"updated hook document is invalid: {target_path}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("hooks"), dict):
        raise AcceptanceError(f"updated hook document lacks a hooks object: {target_path}")

    hooks: dict[str, list[Any]] = {}
    for event, values in document["hooks"].items():
        if not isinstance(event, str) or not isinstance(values, list):
            raise AcceptanceError(f"updated hook event is invalid: {target_path}")
        hooks[event] = copy.deepcopy(values)

    owned = [
        item
        for item in owned_projection
        if item.get("scope") == "shared_json_fragment"
        and item.get("target_path") == target_path
    ]
    if not owned:
        raise AcceptanceError(f"BUGate hook projection is missing: {target_path}")
    declared: set[tuple[str, str]] = set()
    for item in owned:
        event = item.get("event")
        digest = item.get("semantic_digest")
        if not isinstance(event, str) or not isinstance(digest, str):
            raise AcceptanceError(f"BUGate hook projection is invalid: {target_path}")
        identity = (event, digest)
        if identity in declared:
            raise AcceptanceError(f"BUGate hook projection is duplicated: {target_path}")
        declared.add(identity)
        values = hooks.get(event, [])
        matches = [
            index
            for index, value in enumerate(values)
            if contract.semantic_digest({"event": event, "value": value}) == digest
        ]
        if len(matches) != 1:
            raise AcceptanceError(
                f"BUGate-owned hook occurrence count is {len(matches)}, expected 1: "
                f"{target_path} {event}"
            )
        del values[matches[0]]

    remaining = {
        event: values
        for event, values in hooks.items()
        if values
    }
    counts = {event: len(values) for event, values in remaining.items()}
    digests = {
        event: [contract.semantic_digest(value) for value in values]
        for event, values in remaining.items()
    }
    return {
        "top_level": {
            key: copy.deepcopy(value)
            for key, value in document.items()
            if key != "hooks"
        },
        "hooks": remaining,
        "event_entry_counts": counts,
        "entry_count": sum(counts.values()),
        "entry_digests": digests,
    }


def _assert_hook_preservation(
    project: Path,
    expectations: Mapping[str, Mapping[str, Any]],
    owned_projection: Iterable[Mapping[str, Any]],
) -> None:
    for relative, expected in expectations.items():
        raw = (project / relative).read_bytes()
        actual = _sut_hook_semantic_projection(
            raw,
            owned_projection,
            target_path=relative,
        )
        if actual != expected["sut_semantic_projection"]:
            raise AcceptanceError(
                f"SUT-owned hook semantic projection or entry count changed: {relative}"
            )
        if any(marker not in raw for marker in expected["markers"]):
            raise AcceptanceError(f"SUT-owned hook formatting was needlessly changed: {relative}")


def _assert_sut_owned_state(
    project: Path,
    *,
    sut_paths: Iterable[str],
    sut_before: Mapping[
        str,
        tuple[str, bytes | str, int]
        | dict[str, tuple[str, bytes | str, int]],
    ],
    hook_expectations: Mapping[str, Mapping[str, Any]],
    owned_hook_projection: Iterable[Mapping[str, Any]],
    marked: Mapping[str, Any],
    gitignore_outside: bytes,
    context: str,
) -> None:
    if _selected_snapshot(project, sut_paths) != sut_before:
        raise AcceptanceError(f"SUT-owned assets changed {context}")
    _assert_hook_preservation(
        project,
        hook_expectations,
        owned_hook_projection,
    )
    if _outside_marked_block(
        (project / str(marked["target_path"])).read_bytes(), marked
    ) != gitignore_outside:
        raise AcceptanceError(f"SUT-owned gitignore content changed {context}")
    _assert_dirty_file(project)


def _clean_environment(base: Path, memory_url: str) -> dict[str, str]:
    environment = os.environ.copy()
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
        "MCP_API_KEY_AGENT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "HOME": str(base / "home"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "MEMORY_BUS_URL": memory_url,
            "MEMORY_BUS_PROJECT_TAG": "project:synthetic-release-acceptance",
            "MCP_MEMORY_BASE_DIR": str(base / "memory-home"),
            "BUGATE_MEMORY_HOME": str(base / "memory-home"),
        }
    )
    Path(environment["HOME"]).mkdir(parents=True, exist_ok=True)
    return environment


def _run_json(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    action: str,
    expected: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rendered = [str(item) for item in command]
    completed = subprocess.run(
        rendered,
        cwd=cwd,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=300,
    )
    record = {
        "action": action,
        "executable": Path(rendered[0]).name,
        "exit_code": completed.returncode,
    }
    if completed.returncode != expected:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        tail = detail[-1] if detail else "no diagnostic"
        raise AcceptanceError(
            f"{action} exited {completed.returncode}, expected {expected}: {tail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"{action} did not emit one JSON object") from exc
    if not isinstance(payload, dict):
        raise AcceptanceError(f"{action} JSON result is not an object")
    return payload, record


class MemoryState:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.lock = threading.Lock()


class MemoryHandler(http.server.BaseHTTPRequestHandler):
    server: "MemoryServer"

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AcceptanceError("synthetic Memory request must be an object")
        return value

    def _send(self, status: int, value: object) -> None:
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
            self._send(200, {"status": "healthy"})
            return
        if path == "/api/memories":
            with self.server.state.lock:
                records = copy.deepcopy(list(self.server.state.records.values()))
            self._send(200, {"memories": records})
            return
        if path.startswith("/api/memories/"):
            with self.server.state.lock:
                record = copy.deepcopy(self.server.state.records.get(identity or ""))
            if record is None:
                self._send(404, {"detail": "Memory not found"})
            else:
                self._send(200, record)
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/api/search", "/api/search/by-tag"}:
            self._body()
            with self.server.state.lock:
                self.server.state.calls.append(("POST", path, None))
                records = copy.deepcopy(list(self.server.state.records.values()))
            self._send(200, {"memories": records})
            return
        if path != "/api/memories":
            self._send(404, {"detail": "not found"})
            return
        request = self._body()
        content = str(request.get("content") or "")
        identity = hashlib.sha256(content.encode()).hexdigest()
        with self.server.state.lock:
            self.server.state.calls.append(("POST", path, identity))
            record = self.server.state.records.setdefault(
                identity,
                {
                    "content": content,
                    "content_hash": identity,
                    "tags": copy.deepcopy(request.get("tags") or []),
                    "memory_type": request.get("memory_type"),
                    "metadata": copy.deepcopy(request.get("metadata") or {}),
                    "created_at_iso": "2026-07-22T00:00:00Z",
                },
            )
            stored = copy.deepcopy(record)
        self._send(
            200,
            {
                "success": True,
                "message": "stored",
                "content_hash": identity,
                "memory": stored,
            },
        )

    def do_PUT(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        prefix = "/api/memories/"
        if not path.startswith(prefix):
            self._send(404, {"detail": "not found"})
            return
        identity = unquote(path[len(prefix) :])
        request = self._body()
        with self.server.state.lock:
            self.server.state.calls.append(("PUT", path, identity))
            record = self.server.state.records.get(identity)
            if record is None:
                self._send(404, {"detail": "Memory not found"})
                return
            record["metadata"] = copy.deepcopy(request.get("metadata") or {})
            stored = copy.deepcopy(record)
        self._send(
            200,
            {
                "success": True,
                "message": "updated",
                "content_hash": identity,
                "memory": stored,
            },
        )


class MemoryServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), MemoryHandler)
        self.state = MemoryState()


def _run_imported_full_check(
    project: Path,
    base: Path,
    environment: Mapping[str, str],
    server: MemoryServer,
    *,
    mode: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    runner = (
        project
        / VENDOR_DIR
        / ".shared/skills/bugate-full-check/scripts/run_full_check.py"
    )
    if mode not in {"smoke", "full"}:
        raise AcceptanceError(f"unsupported imported full-check mode: {mode}")
    if mode == "smoke":
        fake_home = base / "full-check-home"
        fake_bin = base / "full-check-bin"
        fake_bin.mkdir(parents=True)
        for name in ("codex", "claude"):
            _write(
                fake_bin / name,
                (
                    "#!/bin/sh\n"
                    f"if [ \"${{1:-}}\" = \"--version\" ]; then echo synthetic-{name}; "
                    "else echo ok; fi\n"
                ).encode(),
                0o755,
            )
        _write(
            fake_home / ".cache/mcp_memory/onnx_models/synthetic.onnx",
            b"synthetic model presence marker\n",
        )
        check_environment = dict(environment)
        check_environment.update(
            {
                "HOME": str(fake_home),
                "PATH": f"{fake_bin}:{check_environment.get('PATH', '')}",
                "MEMORY_BUS_URL": f"http://127.0.0.1:{server.server_port}",
                "MEMORY_BUS_PROJECT_TAG": "project:synthetic-release-full-check",
                "MCP_MEMORY_BASE_DIR": str(base / "full-check-memory-home"),
                "BUGATE_MEMORY_HOME": str(base / "full-check-memory-home"),
            }
        )
    else:
        # Formal local acceptance deliberately keeps the operator's real
        # Codex/Claude authentication and Memory endpoint.  Only workspace
        # selection variables are stripped so the synthetic imported repo is
        # the unambiguous target.  No credential value is rendered or stored in
        # the machine-readable report.
        check_environment = os.environ.copy()
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
        ):
            check_environment.pop(name, None)
        check_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--mode",
            mode,
            "--timeout-seconds",
            str(timeout_seconds),
        ],
        cwd=project,
        env=check_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout_seconds + 180,
    )
    required = [
        "| Imported installed-state verification | PASS |",
        "Strict Memory exact-ID verification and closed chain | PASS",
        "Result: PASS",
    ]
    if mode == "full":
        required.extend(
            (
                "| Codex auth/model call | PASS |",
                "| Claude auth/model call | PASS |",
                "| Real multi-view dispatch | PASS |",
                "| Real adversarial dispatch | PASS |",
            )
        )
    if completed.returncode != 0 or any(marker not in completed.stdout for marker in required):
        diagnostic = (completed.stderr or completed.stdout).strip().splitlines()
        tail = diagnostic[-1] if diagnostic else "no diagnostic"
        raise AcceptanceError(
            f"imported {mode} full-check failed with exit {completed.returncode}: {tail}"
        )
    if mode == "smoke":
        with server.state.lock:
            records = copy.deepcopy(server.state.records)
            calls = list(server.state.calls)
        transitions = [
            value
            for value in records.values()
            if isinstance(value.get("metadata"), dict)
            and isinstance(value["metadata"].get("role_transition"), dict)
        ]
        if len(transitions) != 6:
            raise AcceptanceError(
                "imported smoke did not close six strict Memory transitions"
            )
        memory_call_count: int | None = len(calls)
        transition_evidence = "synthetic_memory_server_records"
    else:
        # The real Memory service is intentionally not enumerated or copied
        # into this release report.  The full-check PASS row above is the
        # evidence that its exact-ID six-receipt flow closed successfully.
        transitions = [None] * 6
        memory_call_count = None
        transition_evidence = "real_full_check_pass_row"
    return {
        "status": "passed",
        "mode": mode,
        "runtime": "real_codex_claude_memory" if mode == "full" else "synthetic_ci_runtime",
        "exit_code": completed.returncode,
        "strict_memory_transition_count": len(transitions),
        "memory_call_count": memory_call_count,
        "transition_evidence": transition_evidence,
        "result": "PASS",
    }


def _run_imported_full_check_with_preservation(
    project: Path,
    base: Path,
    environment: Mapping[str, str],
    server: MemoryServer,
    *,
    mode: str,
    timeout_seconds: int,
    sut_paths: Iterable[str],
    sut_before: Mapping[
        str,
        tuple[str, bytes | str, int]
        | dict[str, tuple[str, bytes | str, int]],
    ],
    hook_expectations: Mapping[str, Mapping[str, Any]],
    owned_hook_projection: Iterable[Mapping[str, Any]],
    marked: Mapping[str, Any],
    gitignore_outside: bytes,
) -> dict[str, Any]:
    result = _run_imported_full_check(
        project,
        base,
        environment,
        server,
        mode=mode,
        timeout_seconds=timeout_seconds,
    )
    _assert_sut_owned_state(
        project,
        sut_paths=sut_paths,
        sut_before=sut_before,
        hook_expectations=hook_expectations,
        owned_hook_projection=owned_hook_projection,
        marked=marked,
        gitignore_outside=gitignore_outside,
        context="after imported full-check",
    )
    verified = dict(result)
    verified.update(
        {
            "post_check_sut_snapshot": "passed",
            "post_check_hook_semantics_and_counts": "passed",
            "post_check_gitignore_outside_marker": "passed",
            "post_check_profile_role_evidence_memory_namespace": "passed",
            "post_check_unrelated_dirty_file": "passed",
        }
    )
    return verified


def _workflow(
    *,
    repo: Path,
    prepared: update_source.PreparedRelease,
    archive: Path,
    checksums: Path,
    version: str,
    base: Path,
    full_check_mode: str | None,
    full_check_timeout: int,
) -> dict[str, Any]:
    base.mkdir(parents=True, exist_ok=False)
    legacy_manifests = engine.load_legacy_manifests(prepared.root, prepared.manifest)
    legacy_manifest = next(
        (item for item in legacy_manifests if item.get("source_tag") == LEGACY_TAG),
        None,
    )
    if legacy_manifest is None:
        raise AcceptanceError(f"release lacks the required {LEGACY_TAG} legacy manifest")
    tag_commit = _verify_annotated_legacy_tag(repo, legacy_manifest)

    project = base / "synthetic-imported-repo"
    project.mkdir()
    projection, hook_expectations, marked = _materialize_legacy(
        repo, project, legacy_manifest
    )
    target_projection = contract.render_installed_projection(
        prepared.manifest,
        VENDOR_DIR,
    )
    sut_paths = _populate_sut_owned(project)
    _initialize_dirty_git_repo(project)
    sut_before = _selected_snapshot(project, sut_paths)
    legacy_before = _projection_snapshot(project, projection)
    gitignore_outside = _outside_marked_block(
        (project / str(marked["target_path"])).read_bytes(), marked
    )

    server = MemoryServer()
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        name="bugate-release-acceptance-memory",
        daemon=True,
    )
    thread.start()
    environment = _clean_environment(
        base, f"http://127.0.0.1:{server.server_port}"
    )
    bootstrap = prepared.root / "scripts/bugate_update.py"
    source_args: list[str | Path] = [
        "--archive",
        archive,
        "--checksums",
        checksums,
        "--to",
        version,
    ]
    records: list[dict[str, Any]] = []
    try:
        before_plan = _tree_snapshot(project)
        plan, record = _run_json(
            [
                sys.executable,
                bootstrap,
                "plan",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                *source_args,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="bootstrap_plan",
        )
        records.append(record)
        if _tree_snapshot(project) != before_plan:
            raise AcceptanceError("archive bootstrap plan wrote persistent target state")
        if (
            plan.get("decision") != "GO"
            or plan.get("installed_kind") != "legacy"
            or plan.get("from_version") != "0.3.2"
            or plan.get("to_version") != version
            or plan.get("release_digest") != prepared.manifest.get("self_digest")
        ):
            raise AcceptanceError("archive bootstrap plan contract mismatch")

        applied, record = _run_json(
            [
                sys.executable,
                bootstrap,
                "apply",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                *source_args,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="bootstrap_apply",
        )
        records.append(record)
        transaction_id = str(applied.get("transaction_id") or "")
        if (
            applied.get("decision") != "GO"
            or applied.get("status") != "committed"
            or HEX_TRANSACTION.fullmatch(transaction_id) is None
        ):
            raise AcceptanceError("archive bootstrap apply did not commit a transaction")
        if applied.get("memory_checked") is not False:
            raise AcceptanceError("engine update unexpectedly claimed a Memory check")
        if applied.get("role_governance_activated") is not False:
            raise AcceptanceError("engine update unexpectedly activated role governance")
        with server.state.lock:
            if server.state.calls:
                raise AcceptanceError("engine update contacted Memory")

        lock_path = project / VENDOR_DIR / contract.INSTALLED_LOCK_PATH
        try:
            installed_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AcceptanceError("committed update lacks a readable installed lock") from exc
        if (
            installed_lock.get("installed_version") != version
            or installed_lock.get("previous_version") != "0.3.2"
            or installed_lock.get("archive_sha256") != _sha256(archive)
            or installed_lock.get("archive_verification") != "sha256"
            or installed_lock.get("verified_release_digest")
            != prepared.manifest.get("self_digest")
        ):
            raise AcceptanceError("installed lock is not bound to the verified archive")

        wrapper = project / VENDOR_DIR / "bin/bugate-update"
        verified, record = _run_json(
            [
                wrapper,
                "verify",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="installed_verify",
        )
        records.append(record)
        if (
            verified.get("decision") != "GO"
            or verified.get("installed_kind") != "locked"
            or verified.get("installed_version") != version
            or verified.get("lock_based") is not True
        ):
            raise AcceptanceError("installed updater verification contract mismatch")
        _assert_sut_owned_state(
            project,
            sut_paths=sut_paths,
            sut_before=sut_before,
            hook_expectations=hook_expectations,
            owned_hook_projection=target_projection,
            marked=marked,
            gitignore_outside=gitignore_outside,
            context="during archive update",
        )

        before_idempotent = _tree_snapshot(project)
        repeated, record = _run_json(
            [
                wrapper,
                "apply",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                *source_args,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="same_version_apply",
        )
        records.append(record)
        if (
            repeated.get("decision") != "GO"
            or repeated.get("status") != "no-op"
            or repeated.get("transaction_id") is not None
            or repeated.get("no_op") is not True
            or _tree_snapshot(project) != before_idempotent
        ):
            raise AcceptanceError("same-version archive update is not byte-idempotent")

        rolled_back, record = _run_json(
            [
                wrapper,
                "rollback",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--transaction",
                transaction_id,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="rollback",
        )
        records.append(record)
        if (
            rolled_back.get("decision") != "GO"
            or rolled_back.get("kind") != "rollback"
            or rolled_back.get("rollback_of") != transaction_id
        ):
            raise AcceptanceError("explicit rollback result contract mismatch")
        if lock_path.exists() or lock_path.is_symlink():
            raise AcceptanceError("rollback retained the v0.4 installed lock")
        if _projection_snapshot(project, projection) != legacy_before:
            raise AcceptanceError("rollback did not restore the exact legacy projection")
        _assert_sut_owned_state(
            project,
            sut_paths=sut_paths,
            sut_before=sut_before,
            hook_expectations=hook_expectations,
            owned_hook_projection=projection,
            marked=marked,
            gitignore_outside=gitignore_outside,
            context="during rollback",
        )

        legacy_verified, record = _run_json(
            [
                sys.executable,
                bootstrap,
                "verify",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="legacy_verify_after_rollback",
        )
        records.append(record)
        if (
            legacy_verified.get("decision") != "GO"
            or legacy_verified.get("installed_kind") != "legacy"
            or legacy_verified.get("installed_version") != "0.3.2"
        ):
            raise AcceptanceError("rollback legacy verification contract mismatch")

        reapplied, record = _run_json(
            [
                sys.executable,
                bootstrap,
                "apply",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                *source_args,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="reapply_after_rollback",
        )
        records.append(record)
        if (
            reapplied.get("decision") != "GO"
            or reapplied.get("status") != "committed"
            or reapplied.get("transaction_id") == transaction_id
        ):
            raise AcceptanceError("reapply after rollback did not commit independently")
        final_wrapper = project / VENDOR_DIR / "bin/bugate-update"
        final_verified, record = _run_json(
            [
                final_wrapper,
                "verify",
                project,
                "--vendor-dir",
                VENDOR_DIR,
                "--json",
            ],
            cwd=project,
            environment=environment,
            action="final_installed_verify",
        )
        records.append(record)
        if (
            final_verified.get("decision") != "GO"
            or final_verified.get("installed_kind") != "locked"
            or final_verified.get("installed_version") != version
        ):
            raise AcceptanceError("reapplied installation did not verify")
        _assert_sut_owned_state(
            project,
            sut_paths=sut_paths,
            sut_before=sut_before,
            hook_expectations=hook_expectations,
            owned_hook_projection=target_projection,
            marked=marked,
            gitignore_outside=gitignore_outside,
            context="after rollback/reapply",
        )

        with server.state.lock:
            updater_memory_calls = len(server.state.calls)
        if updater_memory_calls != 0:
            raise AcceptanceError("updater operations touched Memory state")
        full_check = (
            _run_imported_full_check_with_preservation(
                project,
                base,
                environment,
                server,
                mode=full_check_mode,
                timeout_seconds=full_check_timeout,
                sut_paths=sut_paths,
                sut_before=sut_before,
                hook_expectations=hook_expectations,
                owned_hook_projection=target_projection,
                marked=marked,
                gitignore_outside=gitignore_outside,
            )
            if full_check_mode is not None
            else {"status": "not_run_for_secondary_archive"}
        )
        return {
            "status": "passed",
            "archive": archive.name,
            "legacy_tag": LEGACY_TAG,
            "legacy_commit": tag_commit,
            "plan_zero_write": True,
            "from_version": "0.3.2",
            "to_version": version,
            "transaction_id_shape": "32-lowercase-hex",
            "installed_lock_archive_sha256": installed_lock["archive_sha256"],
            "installed_verify": "passed",
            "same_version_idempotent": True,
            "rollback_restored_legacy_preimage": True,
            "reapply_verified": True,
            "sut_owned_assets_preserved": True,
            "sut_owned_hooks_preserved": True,
            "profile_and_memory_namespace_preserved": True,
            "updater_memory_call_count": updater_memory_calls,
            "commands": records,
            "imported_full_check": full_check,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_acceptance(
    *,
    tar_path: Path,
    zip_path: Path,
    checksums_path: Path,
    version: str,
    repo: Path,
    full_check_mode: str = "smoke",
    full_check_archive: str = "tar",
    full_check_timeout: int = 120,
) -> dict[str, Any]:
    try:
        version = contract.validate_semver(version)
    except contract.ContractError as exc:
        raise AcceptanceError(str(exc)) from exc
    if full_check_mode not in {"smoke", "full"}:
        raise AcceptanceError("full-check mode must be smoke or full")
    if full_check_archive not in {"tar", "zip", "both"}:
        raise AcceptanceError("full-check archive selection must be tar, zip, or both")
    if full_check_timeout < 60:
        raise AcceptanceError("full-check timeout must be at least 60 seconds")
    tar_path = _regular_input(tar_path, label="tar archive")
    zip_path = _regular_input(zip_path, label="zip archive")
    checksums_path = _regular_input(checksums_path, label="checksum asset")
    repo = repo.expanduser().resolve(strict=True)
    if not repo.is_dir() or not (repo / ".git").exists():
        raise AcceptanceError("--repo-root must be a BUGate Core Git checkout")
    expected_names = {
        tar_path.name: f"bugate-{version}.tar.gz",
        zip_path.name: f"bugate-{version}.zip",
        checksums_path.name: f"bugate-{version}.SHA256SUMS",
    }
    if any(actual != expected for actual, expected in expected_names.items()):
        raise AcceptanceError("release asset filename/version contract mismatch")
    checksums = _parse_checksums(checksums_path, (tar_path, zip_path))

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "decision": "NO-GO",
        "full_check_request": {
            "mode": full_check_mode,
            "archive": full_check_archive,
            "timeout_seconds": full_check_timeout,
        },
        "assets": {
            tar_path.name: {"sha256": checksums[tar_path.name]},
            zip_path.name: {"sha256": checksums[zip_path.name]},
            checksums_path.name: {"entry_count": 2},
        },
    }
    with tempfile.TemporaryDirectory(prefix="bugate-release-assets-acceptance-") as raw:
        base = Path(raw)
        prepared: list[update_source.PreparedRelease] = []
        archive_reports: list[dict[str, Any]] = []
        for label, archive in (("tar", tar_path), ("zip", zip_path)):
            stage = base / f"extract-{label}"
            stage.mkdir()
            try:
                item = update_source.prepare_archive(
                    archive,
                    checksums_path,
                    stage,
                    expected_version=version,
                )
            except update_source.UpdateSourceError as exc:
                raise AcceptanceError(f"{label} source verification failed: {exc}") from exc
            _verify_release_identity(item.root, item.manifest, version)
            scan = _scan_release_tree(
                item.root,
                item.manifest,
                machine_markers=(
                    str(repo).encode(),
                    str(Path.home().resolve()).encode(),
                ),
            )
            manifests = engine.load_legacy_manifests(item.root, item.manifest)
            v032 = next(
                (value for value in manifests if value.get("source_tag") == LEGACY_TAG),
                None,
            )
            if v032 is None:
                raise AcceptanceError(f"{label} lacks bound {LEGACY_TAG} metadata")
            legacy_contract.validate_legacy_manifest(v032, expected_tag=LEGACY_TAG)
            prepared.append(item)
            archive_reports.append(
                {
                    "format": label,
                    "archive": archive.name,
                    "archive_sha256": item.archive_sha256,
                    "release_manifest_digest": item.manifest["self_digest"],
                    "archive_inventory_count": len(item.manifest["archive_inventory"]),
                    "legacy_manifest_count": len(manifests),
                    "pollution_scan": scan,
                    "status": "passed",
                }
            )
        if contract.canonical_json_bytes(prepared[0].manifest) != contract.canonical_json_bytes(
            prepared[1].manifest
        ):
            raise AcceptanceError("tar and zip contain different release manifests")

        report["archive_validation"] = archive_reports
        report["workflows"] = [
            _workflow(
                repo=repo,
                prepared=prepared[0],
                archive=tar_path,
                checksums=checksums_path,
                version=version,
                base=base / "tar-workflow",
                full_check_mode=(
                    full_check_mode
                    if full_check_archive in {"tar", "both"}
                    else None
                ),
                full_check_timeout=full_check_timeout,
            ),
            _workflow(
                repo=repo,
                prepared=prepared[1],
                archive=zip_path,
                checksums=checksums_path,
                version=version,
                base=base / "zip-workflow",
                full_check_mode=(
                    full_check_mode
                    if full_check_archive in {"zip", "both"}
                    else None
                ),
                full_check_timeout=full_check_timeout,
            ),
        ]
    report["decision"] = "GO"
    report["status"] = "passed"
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--tar", required=True, help="formal bugate-VERSION.tar.gz")
    parser.add_argument("--zip", required=True, help="formal bugate-VERSION.zip")
    parser.add_argument("--checksums", required=True, help="formal SHA256SUMS asset")
    parser.add_argument("--version", required=True, help="expected semantic version")
    parser.add_argument(
        "--repo-root",
        default=str(REPO),
        help="BUGate Core checkout containing formal annotated legacy tags",
    )
    parser.add_argument(
        "--full-check-mode",
        choices=("smoke", "full"),
        default="smoke",
        help="smoke uses synthetic CI runtimes; full requires real Codex, Claude, and Memory",
    )
    parser.add_argument(
        "--full-check-archive",
        choices=("tar", "zip", "both"),
        default="tar",
        help="archive workflow(s) on which to run imported full-check (default: tar)",
    )
    parser.add_argument(
        "--full-check-timeout",
        type=int,
        default=120,
        help="per-command full-check timeout in seconds (minimum: 60)",
    )
    parser.add_argument(
        "--report",
        help="optional path for the same JSON report (keep it outside the checkout)",
    )
    return parser


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _future_resolved_path(path: Path) -> Path:
    """Resolve existing ancestors without creating any missing component."""

    current = path
    missing: list[str] = []
    while not os.path.lexists(current):
        if current.parent == current:
            raise AcceptanceError("acceptance report path has no resolvable ancestor")
        missing.append(current.name)
        current = current.parent
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise AcceptanceError("acceptance report ancestor is unsafe") from exc
    for name in reversed(missing):
        resolved /= name
    return resolved


def _safe_report_destination(report_path: str) -> Path:
    """Validate lexical and symlink-resolved boundaries before any mkdir."""

    requested = Path(report_path).expanduser()
    lexical = Path(os.path.abspath(os.fspath(requested)))
    repo = REPO.resolve(strict=True)
    if _within(lexical, repo):
        raise AcceptanceError("acceptance report must be written outside the Core checkout")
    resolved_future = _future_resolved_path(lexical)
    if _within(resolved_future, repo):
        raise AcceptanceError("acceptance report resolves inside the Core checkout")
    if os.path.lexists(lexical) and (
        lexical.is_symlink() or not lexical.is_file()
    ):
        raise AcceptanceError("acceptance report destination is unsafe")

    # Creation is permitted only after both zero-write checks above.  Resolve
    # again after mkdir to close ordinary symlink/ancestor drift before the
    # temporary report file is opened.
    lexical.parent.mkdir(parents=True, exist_ok=True)
    try:
        resolved_parent = lexical.parent.resolve(strict=True)
    except OSError as exc:
        raise AcceptanceError("acceptance report parent is unsafe") from exc
    destination = resolved_parent / lexical.name
    if _within(destination, repo):
        raise AcceptanceError("acceptance report resolves inside the Core checkout")
    if os.path.lexists(destination) and (
        destination.is_symlink() or not destination.is_file()
    ):
        raise AcceptanceError("acceptance report destination is unsafe")
    return destination


def _emit(report: Mapping[str, Any], report_path: str | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if report_path:
        destination = _safe_report_destination(report_path)
        temporary = destination.parent / f".{destination.name}.tmp-{os.getpid()}"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    sys.stdout.write(rendered)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(raw)
    except ArgumentError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "decision": "NO-GO",
            "status": "argument_error",
            "errors": [str(exc)],
        }
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
        return 2
    try:
        report = run_acceptance(
            tar_path=Path(args.tar),
            zip_path=Path(args.zip),
            checksums_path=Path(args.checksums),
            version=args.version,
            repo=Path(args.repo_root),
            full_check_mode=args.full_check_mode,
            full_check_archive=args.full_check_archive,
            full_check_timeout=args.full_check_timeout,
        )
        _emit(report, args.report)
        return 0
    except Exception as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "version": args.version,
            "decision": "NO-GO",
            "status": "failed",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        try:
            _emit(report, args.report)
        except AcceptanceError as report_error:
            report["errors"].append(str(report_error))
            sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
