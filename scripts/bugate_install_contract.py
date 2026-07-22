#!/usr/bin/env python3
"""SUT-neutral ownership, manifest, and installed-lock contract for BUGate.

The release archive contains the complete BUGate Core checkout, but an imported
installation owns a deliberately smaller projection.  This module is the one
catalog for that projection.  The fresh installer and incremental updater must
consume it instead of maintaining independent file lists.

Only Python's standard library is used.  No function in this module reads a SUT
profile, test, use case, Memory namespace, credential, or machine identity.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


RELEASE_SCHEMA_VERSION = 1
INSTALLED_LOCK_SCHEMA_VERSION = 1
MANAGED_LAYOUT_VERSION = 1
HOOK_CONTRACT_VERSION = 1
# Minimum updater that understands layout/manifest schema 1.  This is a
# protocol floor, not an alias for the target release version: compatible
# later releases remain directly reachable from the first v0.4.x updater.
UPDATER_PROTOCOL_MINIMUM_VERSION = "0.4.2"
PROFILE_SCHEMA_COMPATIBILITY = {
    "source": "bugate.config.yaml:bugate.version",
    "min": "0.1",
    "max_exclusive": "0.2",
    "missing_maps_to": "0.1",
}
RELEASE_MANIFEST_PATH = ".bugate-release/manifest.json"
LEGACY_MANIFEST_DIR = ".bugate-release/legacy"
INSTALLED_LOCK_PATH = "bugate.lock.json"
INSTALLED_MANIFEST_PATH = "bugate.release.json"

BUGATE_UPDATE_WRAPPER_BYTES = b"""#!/bin/sh
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
exec /usr/bin/env python3 "$HERE/../scripts/bugate_update.py" "$@"
"""
BUGATE_UPDATE_WRAPPER_SHA256 = hashlib.sha256(BUGATE_UPDATE_WRAPPER_BYTES).hexdigest()

ARCHIVE_ROLES = (
    "installable_payload",
    "release_metadata",
    "validated_extra",
)
PROJECTION_SCOPES = (
    "vendor",
    "workspace",
    "shared_json_fragment",
    "marked_text_block",
    "generated_metadata",
)

SUPPORTED_LEGACY_TAGS = (
    "v0.3.0",
    "v0.3.1",
    "v0.3.2",
    "v0.3.4",
    "v0.3.5",
    "v0.4.0",
    "v0.4.1",
)

# This is the imported runtime surface copied beneath <vendor-dir>.  It is not
# the complete release archive and intentionally excludes tests, Core-only
# docs, plugin manifests, repository policy, and all SUT-owned artifacts.
VENDOR_TREE_ROOTS = (
    "scripts",
    "bin",
    ".shared/skills/bugate",
    ".shared/skills/bugate-full-check",
    ".shared/skills/bugate-import",
)
VENDOR_SINGLE_FILES = ("docs/SETUP-OPTIONAL.md",)
UPDATER_WORKER_FILES = (
    "scripts/bugate_update.py",
    "scripts/bugate_update_transaction.py",
    "scripts/bugate_update_engine.py",
    "scripts/bugate_update_source.py",
    "scripts/bugate_install_contract.py",
    "scripts/bugate_legacy_manifest.py",
    "scripts/bugate_core.py",
)
SKILL_NAMES = ("bugate", "bugate-full-check", "bugate-import")
SKILL_RUNTIMES = (".claude", ".agents", ".codex")
CODEX_GATE_AGENT_SOURCE_DIR = ".shared/skills/bugate/adapters/codex/agents"
CODEX_GATE_AGENT_NAMES = (
    "brief-gate.toml",
    "inventory-gate.toml",
    "testability-gate.toml",
)

SHARED_HOOK_TARGETS = {
    "claude": ".claude/settings.json",
    "codex": ".codex/hooks.json",
}
GITIGNORE_BEGIN = "# >>> BUGate imported-mode ignores (managed by bugate_init.py) >>>"
GITIGNORE_END = "# <<< BUGate imported-mode ignores <<<"
GITIGNORE_BLOCK_TEMPLATE = """\
{begin}
# Default scorer outputs written to the repo root when run without --*-output
# (oracle_falsification.py / check_prd_health.py /
# generate_assertion_coverage_matrix.py / self_healing_mvp.py).
/oracle_falsification_result.json
/oracle_falsification_result.md
/prd_health_result.json
/prd_health_report.md
/assertion_coverage_matrix.md
/self_healing.json
/self_healing.md
/self_healing_repair_plan.md
# Local agent + memory state — machine-local, never committed.
/{vendor_dir}/plan.lock
/.bugate-update/
/.memory_bus/
/.claude/memory/
/.codex/memories/
{end}
"""

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IGNORED_NAMES = {"__pycache__", ".DS_Store"}


class ContractError(ValueError):
    """Raised when release or installed-state data violates the contract."""


def validate_semver(version: str) -> str:
    """Return *version* when it is strict SemVer 2.0, otherwise fail closed."""

    if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version):
        raise ContractError(f"invalid semantic version: {version!r}")
    return version


def compare_semver(left: str, right: str) -> int:
    """Compare SemVer precedence, deliberately ignoring build metadata."""

    left = validate_semver(left)
    right = validate_semver(right)

    def parts(value: str) -> tuple[tuple[int, int, int], list[str] | None]:
        without_build = value.split("+", 1)[0]
        core, separator, prerelease = without_build.partition("-")
        numbers = tuple(int(part) for part in core.split("."))
        return numbers, prerelease.split(".") if separator else None

    left_core, left_pre = parts(left)
    right_core, right_pre = parts(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_id, right_id in zip(left_pre, right_pre):
        if left_id == right_id:
            continue
        left_numeric = left_id.isdigit()
        right_numeric = right_id.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_id) < int(right_id) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_id < right_id else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def require_updater_compatible(updater_version: str, minimum_version: str) -> None:
    if compare_semver(updater_version, minimum_version) < 0:
        raise ContractError(
            f"updater version {updater_version} is below required minimum {minimum_version}"
        )


def validate_sha256(value: str, *, field: str = "sha256") -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ContractError(f"{field} must be a lowercase SHA-256 digest")
    return value


def validate_relative_path(value: str, *, field: str = "path") -> str:
    """Validate and return a normalized, non-empty POSIX relative path."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ContractError(f"{field} must be a non-empty relative POSIX path")
    if "\\" in value or value.startswith("/"):
        raise ContractError(f"{field} must be a relative POSIX path: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ContractError(f"{field} contains an empty, '.' or '..' component: {value!r}")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value:
        raise ContractError(f"{field} is not normalized: {value!r}")
    return value


def validate_vendor_dir(value: str) -> str:
    """Validate the vendor path subset safe for generated shell hook text."""

    vendor = validate_relative_path(value, field="vendor_dir")
    component = re.compile(r"^[A-Za-z0-9._-]+$")
    if any(component.fullmatch(part) is None for part in vendor.split("/")):
        raise ContractError(
            "vendor_dir components may contain only letters, digits, '.', '_' and '-'"
        )
    return vendor


def _normalize_join(path: PurePosixPath, target: str) -> tuple[str, ...]:
    stack = list(path.parent.parts)
    for part in target.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise ContractError(
                    f"symlink target escapes its managed root: {path.as_posix()} -> {target}"
                )
            stack.pop()
        else:
            stack.append(part)
    return tuple(stack)


def validate_symlink_target(link_path: str, target: str) -> str:
    """Validate a relative symlink target that stays within its catalog root."""

    validate_relative_path(link_path, field="symlink path")
    if not isinstance(target, str) or not target or "\x00" in target:
        raise ContractError("symlink target must be a non-empty relative path")
    if target.startswith("/") or "\\" in target:
        raise ContractError(f"symlink target must be relative POSIX text: {target!r}")
    _normalize_join(PurePosixPath(link_path), target)
    return target


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    """Return the single canonical JSON encoding used by manifests and locks."""

    try:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(f"document is not canonical-JSON serializable: {exc}") from exc
    return (encoded + "\n").encode("utf-8")


def compute_self_digest(document: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(document))
    payload.pop("self_digest", None)
    return sha256_bytes(canonical_json_bytes(payload))


def seal_document(document: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(document))
    payload.pop("self_digest", None)
    payload["self_digest"] = compute_self_digest(payload)
    return payload


def validate_self_digest(document: Mapping[str, Any]) -> str:
    actual = document.get("self_digest")
    validate_sha256(actual, field="self_digest")
    expected = compute_self_digest(document)
    if actual != expected:
        raise ContractError(
            f"manifest self_digest mismatch: expected {expected}, actual {actual}"
        )
    return actual


def _rooted_resolver() -> str:
    return (
        "ROOT=\"$(/usr/bin/env python3 -c 'import os; from pathlib import Path; "
        "p=Path.cwd(); print(os.environ.get(\"BUGATE_PROJECT_ROOT\") or "
        "next((str(c) for c in [p,*p.parents] if (c/\"bugate.config.yaml\").exists()), \"\"))')\"; "
        "[ -n \"$ROOT\" ] || exit 0; "
    )


def _hook_command(identity: str, vendor_dir: str, relative_command: str, *args: str) -> str:
    vendor = validate_vendor_dir(vendor_dir)
    command = validate_relative_path(relative_command, field="hook command")
    tail = (" " + " ".join(args)) if args else ""
    prefix = f"BUGATE_HOOK_ID='{identity}'; export BUGATE_HOOK_ID; "
    if command.startswith("scripts/"):
        invocation = f'/usr/bin/env python3 "$ROOT/{vendor}/{command}"{tail}'
    else:
        invocation = f'"$ROOT/{vendor}/{command}"{tail}'
    return prefix + _rooted_resolver() + invocation


def hook_fragments(vendor_dir: str, runtime: str) -> dict[str, list[dict[str, Any]]]:
    """Return canonical, identity-bearing BUGate hook entries for one runtime."""

    if runtime not in SHARED_HOOK_TARGETS:
        raise ContractError(f"unsupported hook runtime: {runtime!r}")

    def hooks(identity: str, commands: Iterable[tuple[str, tuple[str, ...]]]) -> list[dict[str, str]]:
        return [
            {
                "type": "command",
                "command": _hook_command(identity, vendor_dir, command, *args),
            }
            for command, args in commands
        ]

    write = (
        ("scripts/check_bugate.py", ()),
        ("scripts/check_plan_lock.py", ()),
        ("scripts/check_role_evidence.py", ()),
    )
    role = (("scripts/check_agent_role_paths.py", ()),)
    if runtime == "claude":
        pre = [
            {
                "matcher": "Edit|Write",
                "hooks": hooks("bugate.claude.pre.write.v1", write),
            },
            {
                "matcher": "Read|Edit|Write",
                "hooks": hooks("bugate.claude.pre.role.v1", role),
            },
        ]
    else:
        codex_write = (write[0], write[1], role[0], write[2])
        pre = [
            {
                "matcher": "apply_patch",
                "hooks": hooks("bugate.codex.pre.write.v1", codex_write),
            }
        ]

    prompt_id = f"bugate.{runtime}.prompt.v1"
    session_id = f"bugate.{runtime}.session-start.v1"
    stop_id = f"bugate.{runtime}.stop.v1"
    return {
        "PreToolUse": pre,
        "UserPromptSubmit": [
            {
                "hooks": hooks(
                    prompt_id,
                    (("scripts/bugate_prompt_reminder.py", ()),),
                )
            }
        ],
        "SessionStart": [
            {
                "hooks": hooks(
                    session_id,
                    (
                        ("scripts/memory_bus.py", ("session-start", "--agent", "agent")),
                        ("bin/bugate-role", ("session-start",)),
                    ),
                )
            }
        ],
        "Stop": [
            {
                "hooks": hooks(
                    stop_id,
                    (
                        (
                            "scripts/memory_bus.py",
                            ("stop", "--agent", '"${BUGATE_AGENT_ROLE:-agent}"'),
                        ),
                    ),
                )
            }
        ],
    }


def gitignore_block(vendor_dir: str) -> str:
    vendor = validate_vendor_dir(vendor_dir)
    return GITIGNORE_BLOCK_TEMPLATE.format(
        begin=GITIGNORE_BEGIN,
        end=GITIGNORE_END,
        vendor_dir=vendor,
    )


def skill_link_entries(vendor_dir: str = ".bugate") -> list[dict[str, str]]:
    vendor = validate_vendor_dir(vendor_dir)
    entries: list[dict[str, str]] = []
    for runtime in SKILL_RUNTIMES:
        for skill in SKILL_NAMES:
            path = f"{runtime}/skills/{skill}"
            target = f"../../{vendor}/.shared/skills/{skill}"
            validate_relative_path(path)
            validate_symlink_target(path, target)
            entries.append(
                {
                    "path": path,
                    "type": "symlink",
                    "mode": "0777",
                    "target": target,
                    "vendor_source": f".shared/skills/{skill}",
                }
            )
    return entries


def _should_ignore(path: Path) -> bool:
    return any(part in _IGNORED_NAMES for part in path.parts) or path.suffix == ".pyc"


def _mode_for_file(path: Path) -> str:
    return "0755" if stat.S_IMODE(os.lstat(path).st_mode) & 0o111 else "0644"


def _managed_entry(root: Path, relative: str) -> dict[str, Any]:
    validate_relative_path(relative)
    path = root / relative
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        target = os.readlink(path)
        validate_symlink_target(relative, target)
        return {"path": relative, "type": "symlink", "mode": "0777", "target": target}
    if stat.S_ISDIR(st.st_mode):
        return {"path": relative, "type": "directory", "mode": "0755"}
    if stat.S_ISREG(st.st_mode):
        return {
            "path": relative,
            "type": "file",
            "mode": _mode_for_file(path),
            "sha256": sha256_file(path),
        }
    raise ContractError(f"unsupported managed path type: {relative}")


def scan_managed_paths(
    source_root: Path,
    *,
    selected_paths: Iterable[str] | None = None,
    tree_roots: Iterable[str] = VENDOR_TREE_ROOTS,
    single_files: Iterable[str] = VENDOR_SINGLE_FILES,
) -> list[dict[str, Any]]:
    """Project release sources into vendor-relative manifest entries.

    ``selected_paths`` binds the projection to the builder's archive inventory;
    ignored or unrelated working-tree files can never leak into the manifest.
    Direct callers may omit it for an already-extracted, trusted tag tree.
    """

    root = source_root.resolve()
    selected = None
    if selected_paths is not None:
        selected = {validate_relative_path(path) for path in selected_paths}
    paths: set[str] = set()

    for raw_root in tree_roots:
        rel_root = validate_relative_path(raw_root, field="vendor tree root")
        physical = root / rel_root
        if not physical.is_dir() or physical.is_symlink():
            raise ContractError(f"required vendor tree root is missing or not a directory: {rel_root}")
        paths.add(rel_root)
        for current, dirnames, filenames in os.walk(physical, followlinks=False):
            current_path = Path(current)
            dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED_NAMES)
            filenames = sorted(filenames)
            current_rel = current_path.relative_to(root)
            if current_rel != Path(rel_root):
                paths.add(current_rel.as_posix())
            for name in filenames:
                rel = (current_rel / name)
                if _should_ignore(rel):
                    continue
                rel_text = rel.as_posix()
                if selected is None or rel_text in selected:
                    paths.add(rel_text)
            # os.walk lists symlinked directories in dirnames.  Record them as
            # symlinks and remove them so they are never followed.
            for name in list(dirnames):
                candidate = current_path / name
                if candidate.is_symlink():
                    rel_text = (current_rel / name).as_posix()
                    if selected is None or rel_text in selected:
                        paths.add(rel_text)
                    dirnames.remove(name)

    for raw_file in single_files:
        rel = validate_relative_path(raw_file, field="vendor single file")
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise ContractError(f"required vendor file is missing or not regular: {rel}")
        if selected is not None and rel not in selected:
            raise ContractError(f"required vendor file is absent from archive inventory: {rel}")
        paths.add(rel)

    entries = [_managed_entry(root, rel) for rel in sorted(paths)]
    validate_managed_paths(entries)
    return entries


def validate_managed_paths(entries: Iterable[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    folded: set[str] = set()
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise ContractError("every managed path entry must be an object")
        path = validate_relative_path(raw.get("path"), field="managed path")
        if path in seen or path.casefold() in folded:
            raise ContractError(f"duplicate or case-conflicting managed path: {path}")
        seen.add(path)
        folded.add(path.casefold())
        kind = raw.get("type")
        mode = raw.get("mode")
        if kind == "file":
            if mode not in {"0644", "0755"}:
                raise ContractError(f"invalid file mode for {path}: {mode!r}")
            validate_sha256(raw.get("sha256"), field=f"{path}.sha256")
            if "target" in raw:
                raise ContractError(f"file entry must not declare a symlink target: {path}")
        elif kind == "directory":
            if mode != "0755":
                raise ContractError(f"invalid directory mode for {path}: {mode!r}")
            if "sha256" in raw or "target" in raw:
                raise ContractError(f"directory entry has file-only fields: {path}")
        elif kind == "symlink":
            if mode != "0777":
                raise ContractError(f"invalid symlink mode for {path}: {mode!r}")
            validate_symlink_target(path, raw.get("target"))
            if "sha256" in raw:
                raise ContractError(f"symlink entry must not declare sha256: {path}")
        else:
            raise ContractError(f"invalid managed path type for {path}: {kind!r}")


def _reject_non_directory_ancestors(
    path_types: Mapping[str, str], *, label: str
) -> None:
    """Reject a file/symlink/fragment that is used as another item's parent."""

    folded = {path.casefold(): (path, kind) for path, kind in path_types.items()}
    for path in path_types:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            found = folded.get(parent.as_posix().casefold())
            if found is not None and found[1] != "directory":
                raise ContractError(
                    f"{label} has non-directory ancestor conflict: "
                    f"{found[0]} ({found[1]}) -> {path}"
                )
            parent = parent.parent


def semantic_digest(value: Any) -> str:
    """Digest a complete semantic value; an identity label is never enough."""

    return sha256_bytes(canonical_json_bytes({"value": value}))


def _is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _archive_roles(path: str) -> list[str]:
    roles: list[str] = []
    installable_roots = (*VENDOR_TREE_ROOTS, *VENDOR_SINGLE_FILES)
    if any(
        _is_within(path, root) or _is_within(root, path)
        for root in installable_roots
    ):
        roles.append("installable_payload")
    metadata_paths = (
        "scripts/bugate_update.py",
        ".bugate-release",
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
    )
    if (
        path == "scripts/bugate_update.py"
        or any(
            _is_within(path, metadata) or _is_within(metadata, path)
            for metadata in metadata_paths[1:]
        )
    ):
        roles.append("release_metadata")
    if not roles:
        roles.append("validated_extra")
    return roles


def _default_release_paths(root: Path) -> set[str]:
    """Development-only inventory fallback; the release builder passes Git paths."""

    excluded = {".git", ".venv", "dist", ".memory_bus", "__pycache__"}
    paths: set[str] = set()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(name for name in dirnames if name not in excluded)
        for name in sorted(filenames):
            path = current_path / name
            rel = path.relative_to(root)
            if _should_ignore(rel):
                continue
            paths.add(rel.as_posix())
        for name in list(dirnames):
            path = current_path / name
            if path.is_symlink():
                paths.add(path.relative_to(root).as_posix())
                dirnames.remove(name)
    return paths


def _inventory_physical_entry(root: Path, relative: str) -> dict[str, Any]:
    entry = _managed_entry(root, relative)
    entry["roles"] = _archive_roles(relative)
    return entry


def build_archive_inventory(
    source_root: Path,
    *,
    selected_paths: Iterable[str] | None = None,
    overlay_files: Mapping[str, bytes] | None = None,
    include_manifest_placeholder: bool = True,
) -> list[dict[str, Any]]:
    """Build the complete typed archive inventory, including generated overlay."""

    root = source_root.resolve()
    selected = (
        {validate_relative_path(path, field="archive path") for path in selected_paths}
        if selected_paths is not None
        else _default_release_paths(root)
    )
    overlays = dict(overlay_files or {})
    for path, data in overlays.items():
        validate_relative_path(path, field="overlay path")
        if not isinstance(data, bytes):
            raise ContractError(f"overlay payload must be bytes: {path}")
        if path in selected:
            raise ContractError(f"overlay conflicts with source archive path: {path}")
    if include_manifest_placeholder and RELEASE_MANIFEST_PATH in selected | overlays.keys():
        raise ContractError(
            f"reserved release manifest overlay collides with source: {RELEASE_MANIFEST_PATH}"
        )

    all_paths = set(selected) | set(overlays)
    if include_manifest_placeholder:
        all_paths.add(RELEASE_MANIFEST_PATH)
    for path in list(all_paths):
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            all_paths.add(parent.as_posix())
            parent = parent.parent

    entries: list[dict[str, Any]] = []
    for relative in sorted(all_paths):
        if relative == RELEASE_MANIFEST_PATH and include_manifest_placeholder:
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": "0644",
                    "digest_ref": "self_digest",
                    "roles": ["release_metadata"],
                }
            )
            continue
        if relative in overlays:
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": "0644",
                    "sha256": sha256_bytes(overlays[relative]),
                    "roles": _archive_roles(relative),
                }
            )
            continue
        physical = root / relative
        if relative not in selected:
            if not physical.exists() or physical.is_symlink() or not physical.is_dir():
                # Overlay-only parent directories have no working-tree source.
                entries.append(
                    {
                        "path": relative,
                        "type": "directory",
                        "mode": "0755",
                        "roles": _archive_roles(relative),
                    }
                )
            else:
                entries.append(_inventory_physical_entry(root, relative))
            continue
        if not (physical.exists() or physical.is_symlink()):
            raise ContractError(f"selected archive path is missing: {relative}")
        entries.append(_inventory_physical_entry(root, relative))

    validate_archive_inventory(entries)
    return entries


def validate_archive_inventory(
    entries: Iterable[Mapping[str, Any]], *, strict_current: bool = False
) -> None:
    items = list(entries)
    validate_managed_paths(
        [
            {key: value for key, value in item.items() if key not in {"roles", "digest_ref"}}
            for item in items
            if item.get("digest_ref") != "self_digest"
        ]
    )
    seen: set[str] = set()
    folded: set[str] = set()
    for item in items:
        path = validate_relative_path(item.get("path"), field="archive path")
        if path in seen or path.casefold() in folded:
            raise ContractError(f"duplicate or case-conflicting archive path: {path}")
        seen.add(path)
        folded.add(path.casefold())
        roles = item.get("roles")
        if (
            not isinstance(roles, list)
            or not roles
            or len(roles) != len(set(roles))
            or any(role not in ARCHIVE_ROLES for role in roles)
        ):
            raise ContractError(f"invalid archive roles for {path}: {roles!r}")
        expected_order = [role for role in ARCHIVE_ROLES if role in roles]
        if roles != expected_order:
            raise ContractError(f"archive roles are not canonical for {path}: {roles!r}")
        if strict_current and roles != _archive_roles(path):
            raise ContractError(f"archive roles do not match the ownership catalog: {path}")
        if item.get("digest_ref") is not None:
            if path != RELEASE_MANIFEST_PATH or item.get("digest_ref") != "self_digest":
                raise ContractError(f"invalid archive digest reference: {path}")
            if item.get("type") != "file" or item.get("mode") != "0644" or "sha256" in item:
                raise ContractError("release manifest placeholder must be a 0644 file without sha256")
    _reject_non_directory_ancestors(
        {item["path"]: item["type"] for item in items},
        label="archive inventory",
    )


def _projection_copy(item: Mapping[str, Any]) -> dict[str, Any]:
    path = item["path"]
    result = {
        "id": f"vendor:{path}",
        "scope": "vendor",
        "source_path": path,
        "target_path": path,
        "type": item["type"],
        "mode": item["mode"],
    }
    if item["type"] == "file":
        result["sha256"] = item["sha256"]
    elif item["type"] == "symlink":
        result["target"] = item["target"]
    return result


def _hook_projection(vendor_dir: str) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    identity_re = re.compile(r"^BUGATE_HOOK_ID='([^']+)'; export BUGATE_HOOK_ID; ")
    for runtime, target_path in SHARED_HOOK_TARGETS.items():
        fragments = hook_fragments(vendor_dir, runtime)
        for event, entries in fragments.items():
            for value in entries:
                commands = [hook.get("command", "") for hook in value.get("hooks", [])]
                identities = {
                    match.group(1)
                    for command in commands
                    if (match := identity_re.match(command)) is not None
                }
                if len(identities) != 1 or len(commands) != len(value.get("hooks", [])):
                    raise ContractError(f"canonical hook entry has inconsistent identity: {runtime}/{event}")
                identity = identities.pop()
                semantic_value = {"event": event, "value": value}
                projected.append(
                    {
                        "id": f"hook:{identity}",
                        "scope": "shared_json_fragment",
                        "runtime": runtime,
                        "hook_identity": identity,
                        "target_path": target_path,
                        "event": event,
                        "type": "json_fragment",
                        "value": copy.deepcopy(value),
                        "semantic_digest": semantic_digest(semantic_value),
                    }
                )
    return projected


def build_installed_projection(
    archive_inventory: Iterable[Mapping[str, Any]], *, vendor_dir: str = ".bugate"
) -> list[dict[str, Any]]:
    """Build the complete flat imported-install write catalog."""

    vendor = validate_vendor_dir(vendor_dir)
    inventory = [copy.deepcopy(dict(item)) for item in archive_inventory]
    validate_archive_inventory(inventory)
    by_path = {item["path"]: item for item in inventory}
    projection = [
        _projection_copy(item)
        for item in inventory
        if "installable_payload" in item["roles"]
    ]

    for runtime in SKILL_RUNTIMES:
        runtime_name = runtime.removeprefix(".")
        for skill in SKILL_NAMES:
            source = f".shared/skills/{skill}"
            if source not in by_path:
                raise ContractError(f"missing skill source in archive inventory: {source}")
            path = f"{runtime}/skills/{skill}"
            target = f"../../{vendor}/{source}"
            validate_symlink_target(path, target)
            projection.append(
                {
                    "id": f"skill:{runtime_name}:{skill}",
                    "scope": "workspace",
                    "source_path": source,
                    "target_path": path,
                    "type": "symlink",
                    "mode": "0777",
                    "target": target,
                    "skill_name": skill,
                }
            )

    for name in CODEX_GATE_AGENT_NAMES:
        source = f"{CODEX_GATE_AGENT_SOURCE_DIR}/{name}"
        source_item = by_path.get(source)
        if source_item is None or source_item.get("type") != "file":
            raise ContractError(f"missing Codex gate-agent source in archive inventory: {source}")
        projection.append(
            {
                "id": f"agent:codex:{name.removesuffix('.toml')}",
                "scope": "workspace",
                "source_path": source,
                "target_path": f".codex/agents/{name}",
                "type": "file",
                "mode": source_item["mode"],
                "sha256": source_item["sha256"],
            }
        )

    projection.extend(_hook_projection(vendor))
    block = gitignore_block(vendor)
    block_value = {
        "begin": GITIGNORE_BEGIN,
        "end": GITIGNORE_END,
        "content": block,
    }
    projection.append(
        {
            "id": "gitignore:bugate-imported-mode",
            "scope": "marked_text_block",
            "target_path": ".gitignore",
            "type": "text_fragment",
            **block_value,
            "semantic_digest": semantic_digest(block_value),
        }
    )
    projection.extend(
        [
            {
                "id": "metadata:installed-release-manifest",
                "scope": "generated_metadata",
                "source_path": RELEASE_MANIFEST_PATH,
                "target_path": INSTALLED_MANIFEST_PATH,
                "type": "file",
                "mode": "0644",
                "derivation": "canonical_release_manifest",
                "digest_ref": "canonical_manifest_sha256",
            },
            {
                "id": "metadata:installed-lock",
                "scope": "generated_metadata",
                "source_path": RELEASE_MANIFEST_PATH,
                "target_path": INSTALLED_LOCK_PATH,
                "type": "file",
                "mode": "0644",
                "derivation": "installed_lock_from_verified_manifest",
                "schema_version": INSTALLED_LOCK_SCHEMA_VERSION,
            },
        ]
    )
    validate_installed_projection(projection, archive_inventory=inventory)
    return sorted(projection, key=lambda item: item["id"])


def _validate_projection_source_fields(
    projection: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    identity: str,
) -> None:
    """Bind a projected physical item to the exact archived source image."""

    fields = ["type", "mode"]
    source_type = source.get("type")
    if source_type == "file":
        fields.append("sha256")
    elif source_type == "symlink":
        fields.append("target")
    for field in fields:
        if projection.get(field) != source.get(field):
            raise ContractError(
                f"projection {identity} does not match archive source {source.get('path')}: "
                f"{field} differs"
            )


def _validate_skill_discovery_source(
    projection: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    identity: str,
) -> None:
    """Allow only the explicit runtime/skills/name -> vendor skill-dir mapping."""

    source_path = projection["source_path"]
    target_path = projection["target_path"]
    source_parts = PurePosixPath(source_path).parts
    target_parts = PurePosixPath(target_path).parts
    if (
        source.get("type") != "directory"
        or len(source_parts) != 3
        or source_parts[:2] != (".shared", "skills")
        or len(target_parts) != 3
        or target_parts[0] not in SKILL_RUNTIMES
        or target_parts[1] != "skills"
    ):
        raise ContractError(
            f"workspace symlink is not an explicit skill-directory mapping: {identity}"
        )
    skill_name = source_parts[2]
    runtime_name = target_parts[0].removeprefix(".")
    if (
        target_parts[2] != skill_name
        or projection.get("skill_name") != skill_name
        or identity != f"skill:{runtime_name}:{skill_name}"
    ):
        raise ContractError(
            f"workspace skill identity/source/target mismatch: {identity}"
        )
    resolved = _normalize_join(
        PurePosixPath(target_path), projection.get("target")
    )
    if resolved != (".bugate", *source_parts):
        raise ContractError(
            f"workspace skill target is not bound to its archived source directory: {identity}"
        )


def validate_installed_projection(
    projection: Iterable[Mapping[str, Any]],
    *,
    archive_inventory: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    items = list(projection)
    inventory_items = list(archive_inventory) if archive_inventory is not None else None
    if inventory_items is not None:
        validate_archive_inventory(inventory_items)
    inventory = (
        {item["path"]: item for item in inventory_items}
        if inventory_items is not None
        else None
    )
    seen_ids: set[str] = set()
    folded_ids: set[str] = set()
    target_nodes: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    source_nodes: dict[str, list[tuple[str, str, str]]] = {}
    for raw in items:
        if not isinstance(raw, Mapping):
            raise ContractError("every installed projection entry must be an object")
        identity = raw.get("id")
        if (
            not isinstance(identity, str)
            or not identity
            or identity in seen_ids
            or identity.casefold() in folded_ids
        ):
            raise ContractError(f"duplicate or invalid projection id: {identity!r}")
        seen_ids.add(identity)
        folded_ids.add(identity.casefold())
        scope = raw.get("scope")
        if scope not in PROJECTION_SCOPES:
            raise ContractError(f"invalid projection scope for {identity}: {scope!r}")
        target_path = validate_relative_path(
            raw.get("target_path"), field=f"{identity}.target_path"
        )
        target_group = "vendor" if scope in {"vendor", "generated_metadata"} else "workspace"
        target_key = (target_group, target_path.casefold())
        existing_targets = target_nodes.setdefault(target_key, [])
        if existing_targets and not (
            scope == "shared_json_fragment"
            and all(existing[0] == "shared_json_fragment" for existing in existing_targets)
        ):
            raise ContractError(f"duplicate projection target: {target_path}")

        source = raw.get("source_path")
        source_item: Mapping[str, Any] | None = None
        if source is not None:
            source = validate_relative_path(source, field=f"{identity}.source_path")
            if inventory is not None:
                source_item = inventory.get(source)
                if source_item is None:
                    raise ContractError(f"projection source is absent from archive: {source}")
                if scope == "generated_metadata":
                    if "release_metadata" not in source_item.get("roles", []):
                        raise ContractError(f"generated metadata source lacks metadata role: {source}")
                elif scope in {"vendor", "workspace"} and "installable_payload" not in source_item.get("roles", []):
                    raise ContractError(f"projection source is not installable payload: {source}")

        kind = raw.get("type")
        if scope in {"vendor", "workspace", "generated_metadata"}:
            if kind == "file":
                if raw.get("mode") not in {"0644", "0755"}:
                    raise ContractError(f"invalid projected file mode: {identity}")
                if scope != "generated_metadata":
                    validate_sha256(raw.get("sha256"), field=f"{identity}.sha256")
            elif kind == "directory":
                if scope != "vendor" or raw.get("mode") != "0755":
                    raise ContractError(f"invalid projected directory: {identity}")
            elif kind == "symlink":
                if raw.get("mode") != "0777":
                    raise ContractError(f"invalid projected symlink mode: {identity}")
                validate_symlink_target(target_path, raw.get("target"))
            else:
                raise ContractError(f"invalid projected path type: {identity}")
            if scope == "generated_metadata":
                expected_derivations = {
                    "metadata:installed-release-manifest": (
                        "canonical_release_manifest",
                        "canonical_manifest_sha256",
                    ),
                    "metadata:installed-lock": (
                        "installed_lock_from_verified_manifest",
                        None,
                    ),
                }
                expected = expected_derivations.get(identity)
                if expected is None or raw.get("derivation") != expected[0]:
                    raise ContractError(f"unverified generated metadata derivation: {identity}")
                if expected[1] is not None and raw.get("digest_ref") not in {
                    expected[1],
                    None,
                }:
                    raise ContractError(f"invalid generated metadata digest reference: {identity}")
            if inventory is not None and scope == "vendor":
                if source_item is None:
                    raise ContractError(f"projection lacks an archived source: {identity}")
                _validate_projection_source_fields(
                    raw, source_item, identity=identity
                )
            elif inventory is not None and scope == "workspace":
                if source_item is None:
                    raise ContractError(f"projection lacks an archived source: {identity}")
                if kind == "file":
                    _validate_projection_source_fields(
                        raw, source_item, identity=identity
                    )
                elif kind == "symlink":
                    _validate_skill_discovery_source(
                        raw, source_item, identity=identity
                    )
        elif scope == "shared_json_fragment":
            if kind != "json_fragment" or not isinstance(raw.get("value"), Mapping):
                raise ContractError(f"invalid shared JSON fragment: {identity}")
            expected = semantic_digest({"event": raw.get("event"), "value": raw["value"]})
            if raw.get("semantic_digest") != expected:
                raise ContractError(f"hook semantic digest mismatch: {identity}")
        elif scope == "marked_text_block":
            value = {key: raw.get(key) for key in ("begin", "end", "content")}
            if raw.get("semantic_digest") != semantic_digest(value):
                raise ContractError(f"marked block semantic digest mismatch: {identity}")
        conflict_type = (
            "directory"
            if scope in {"vendor", "workspace", "generated_metadata"}
            and kind == "directory"
            else "file"
        )
        existing_targets.append((scope, conflict_type, target_path))
        if source is not None and scope in {"vendor", "workspace"}:
            source_nodes.setdefault(source.casefold(), []).append(
                (scope, kind, source)
            )

    for target_group in ("vendor", "workspace"):
        path_types = {
            values[0][2]: values[0][1]
            for (group, _folded), values in target_nodes.items()
            if group == target_group
        }
        _reject_non_directory_ancestors(
            path_types,
            label=f"{target_group} projection targets",
        )

    source_path_types: dict[str, str] = {}
    for values in source_nodes.values():
        path = values[0][2]
        if inventory is not None and path in inventory:
            source_type = inventory[path]["type"]
        else:
            vendor_values = [value for value in values if value[0] == "vendor"]
            candidates = vendor_values or values
            candidate_types = {value[1] for value in candidates}
            if len(candidate_types) != 1:
                raise ContractError(f"projection source type conflict: {path}")
            source_type = candidates[0][1]
        source_path_types[path] = source_type
    _reject_non_directory_ancestors(
        source_path_types,
        label="projection sources",
    )


def build_release_manifest(
    source_root: Path,
    version: str,
    *,
    selected_paths: Iterable[str] | None = None,
    overlay_files: Mapping[str, bytes] | None = None,
    updater_minimum_version: str | None = None,
) -> dict[str, Any]:
    """Generate and seal a full archive inventory plus narrow write catalog."""

    release_version = validate_semver(version)
    minimum = validate_semver(
        updater_minimum_version or UPDATER_PROTOCOL_MINIMUM_VERSION
    )
    inventory = build_archive_inventory(
        source_root,
        selected_paths=selected_paths,
        overlay_files=overlay_files,
    )
    projection = build_installed_projection(inventory)
    payload = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "bugate_version": release_version,
        "archive_prefix": f"bugate-{release_version}",
        "layout_version": MANAGED_LAYOUT_VERSION,
        "hook_contract_version": HOOK_CONTRACT_VERSION,
        "profile_schema_compatibility": dict(PROFILE_SCHEMA_COMPATIBILITY),
        "updater_minimum_version": minimum,
        "archive_inventory": inventory,
        "installed_projection": projection,
    }
    return seal_document(payload)


def validate_release_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_version: str | None = None,
    strict_current: bool = False,
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ContractError("release manifest must be an object")
    if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ContractError("unsupported release manifest schema_version")
    version = validate_semver(manifest.get("bugate_version"))
    if expected_version is not None and version != validate_semver(expected_version):
        raise ContractError(
            f"release manifest version mismatch: expected {expected_version}, actual {version}"
        )
    if manifest.get("archive_prefix") != f"bugate-{version}":
        raise ContractError("release manifest archive_prefix/version mismatch")
    layout_version = manifest.get("layout_version")
    hook_contract_version = manifest.get("hook_contract_version")
    if not isinstance(layout_version, int) or isinstance(layout_version, bool) or layout_version < 1:
        raise ContractError("managed layout version must be a positive integer")
    if (
        not isinstance(hook_contract_version, int)
        or isinstance(hook_contract_version, bool)
        or hook_contract_version < 1
    ):
        raise ContractError("hook contract version must be a positive integer")
    compatibility = manifest.get("profile_schema_compatibility")
    if not isinstance(compatibility, Mapping) or set(compatibility) != {
        "source",
        "min",
        "max_exclusive",
        "missing_maps_to",
    }:
        raise ContractError("profile schema compatibility shape is invalid")
    if compatibility.get("source") != "bugate.config.yaml:bugate.version":
        raise ContractError("profile schema compatibility source is invalid")
    schema_version_re = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    for key in ("min", "max_exclusive", "missing_maps_to"):
        if not isinstance(compatibility.get(key), str) or not schema_version_re.fullmatch(
            compatibility[key]
        ):
            raise ContractError(f"profile schema compatibility {key} is invalid")
    validate_semver(manifest.get("updater_minimum_version"))
    inventory = manifest.get("archive_inventory")
    projection = manifest.get("installed_projection")
    if not isinstance(inventory, list) or not inventory:
        raise ContractError("release manifest archive_inventory must be non-empty")
    if not isinstance(projection, list) or not projection:
        raise ContractError("release manifest installed_projection must be non-empty")
    validate_archive_inventory(inventory, strict_current=strict_current)
    validate_installed_projection(projection, archive_inventory=inventory)
    if sum(1 for item in inventory if item["path"] == RELEASE_MANIFEST_PATH) != 1:
        raise ContractError("release manifest inventory must contain its reserved overlay")
    installable_sources = {
        item["path"]
        for item in inventory
        if "installable_payload" in item.get("roles", [])
    }
    vendor_sources = {
        item["source_path"]
        for item in projection
        if item.get("scope") == "vendor"
        and isinstance(item.get("source_path"), str)
    }
    if installable_sources != vendor_sources:
        raise ContractError(
            "installed projection does not exactly cover embedded installable payload"
        )
    if strict_current:
        if layout_version != MANAGED_LAYOUT_VERSION:
            raise ContractError("release uses a non-current managed layout version")
        if hook_contract_version != HOOK_CONTRACT_VERSION:
            raise ContractError("release uses a non-current hook contract version")
        if compatibility != PROFILE_SCHEMA_COMPATIBILITY:
            raise ContractError("release uses a non-current profile compatibility range")
        inventory_by_path = {item["path"]: item for item in inventory}
        missing_workers = [
            path
            for path in UPDATER_WORKER_FILES
            if path not in inventory_by_path
            or inventory_by_path[path].get("type") != "file"
            or "installable_payload"
            not in inventory_by_path[path].get("roles", [])
        ]
        if missing_workers:
            raise ContractError(
                "release lacks the complete updater worker bundle: "
                + ", ".join(missing_workers)
            )
        wrapper = inventory_by_path.get("bin/bugate-update")
        if wrapper != {
            "path": "bin/bugate-update",
            "type": "file",
            "mode": "0755",
            "sha256": BUGATE_UPDATE_WRAPPER_SHA256,
            "roles": ["installable_payload"],
        }:
            raise ContractError(
                "release does not contain the canonical bin/bugate-update wrapper"
            )
        expected_projection = build_installed_projection(inventory)
        if projection != expected_projection:
            raise ContractError(
                "installed projection differs from the current ownership catalog"
            )
    validate_self_digest(manifest)
    return copy.deepcopy(dict(manifest))


def validate_current_release_manifest(
    manifest: Mapping[str, Any], *, expected_version: str | None = None
) -> dict[str, Any]:
    """Validate schema plus the build-time catalog shipped by this checkout."""

    return validate_release_manifest(
        manifest,
        expected_version=expected_version,
        strict_current=True,
    )


def render_installed_projection(
    release_manifest: Mapping[str, Any], vendor_dir: str = ".bugate"
) -> list[dict[str, Any]]:
    """Render parameterized target paths and fragments for an imported repo."""

    manifest = validate_release_manifest(release_manifest)
    vendor = validate_vendor_dir(vendor_dir)
    rendered = copy.deepcopy(manifest["installed_projection"])

    def replace_vendor(value: Any, old: str, new: str) -> Any:
        if isinstance(value, str):
            return value.replace(f"$ROOT/{old}/", f"$ROOT/{new}/")
        if isinstance(value, list):
            return [replace_vendor(child, old, new) for child in value]
        if isinstance(value, Mapping):
            return {
                key: replace_vendor(child, old, new)
                for key, child in value.items()
            }
        return value

    embedded_vendor = ".bugate"
    for item in rendered:
        scope = item["scope"]
        if scope in {"vendor", "generated_metadata"}:
            item["target_path"] = f"{vendor}/{item['target_path']}"
        if item["id"].startswith("skill:"):
            source_in_workspace = f"{vendor}/{item['source_path']}"
            item["target"] = posixpath.relpath(
                source_in_workspace,
                PurePosixPath(item["target_path"]).parent.as_posix(),
            )
            validate_symlink_target(item["target_path"], item["target"])
        elif scope == "shared_json_fragment":
            item["value"] = replace_vendor(
                item["value"], embedded_vendor, vendor
            )
            item["semantic_digest"] = semantic_digest(
                {"event": item["event"], "value": item["value"]}
            )
        elif scope == "marked_text_block":
            item["content"] = item["content"].replace(
                f"/{embedded_vendor}/", f"/{vendor}/"
            )
            value = {
                "begin": item["begin"],
                "end": item["end"],
                "content": item["content"],
            }
            item["semantic_digest"] = semantic_digest(value)
    validate_installed_projection(rendered)
    return sorted(rendered, key=lambda item: item["id"])


def build_installed_lock(
    release_manifest: Mapping[str, Any],
    *,
    previous_version: str | None,
    archive_sha256: str | None,
    vendor_dir: str = ".bugate",
    updater_version: str | None = None,
) -> dict[str, Any]:
    """Build deterministic committed installed state from a verified manifest."""

    manifest = validate_release_manifest(release_manifest)
    installed_version = manifest["bugate_version"]
    if previous_version is not None:
        validate_semver(previous_version)
    if archive_sha256 is not None:
        validate_sha256(archive_sha256, field="archive_sha256")
    updater = validate_semver(updater_version or installed_version)
    minimum_updater = validate_semver(manifest["updater_minimum_version"])
    require_updater_compatible(updater, minimum_updater)
    vendor = validate_vendor_dir(vendor_dir)
    manifest_sha = sha256_bytes(canonical_json_bytes(manifest))
    projection = render_installed_projection(manifest, vendor)
    for item in projection:
        if item["id"] == "metadata:installed-release-manifest":
            item.pop("digest_ref", None)
            item["sha256"] = manifest_sha

    compatibility = manifest["profile_schema_compatibility"]
    lock = {
        "schema_version": INSTALLED_LOCK_SCHEMA_VERSION,
        "installed_version": installed_version,
        "previous_version": previous_version,
        "verified_release_digest": manifest["self_digest"],
        "archive_sha256": archive_sha256,
        "archive_verification": (
            "sha256" if archive_sha256 else "unavailable-from-unpacked-source"
        ),
        "release_manifest_sha256": manifest_sha,
        "layout_version": manifest["layout_version"],
        "hook_contract_version": manifest["hook_contract_version"],
        "profile_schema_compatibility": {
            "min": compatibility["min"],
            "max_exclusive": compatibility["max_exclusive"],
        },
        "updater_version": updater,
        "updater_minimum_version": minimum_updater,
        "installed_manifest": {
            "path": f"{vendor}/{INSTALLED_MANIFEST_PATH}",
            "sha256": manifest_sha,
        },
        "installed_projection": projection,
    }
    validate_installed_lock(lock, release_manifest=manifest, vendor_dir=vendor)
    return lock


def validate_installed_lock(
    lock: Mapping[str, Any],
    *,
    release_manifest: Mapping[str, Any] | None = None,
    vendor_dir: str = ".bugate",
    strict_current: bool = False,
) -> dict[str, Any]:
    if not isinstance(lock, Mapping) or lock.get("schema_version") != INSTALLED_LOCK_SCHEMA_VERSION:
        raise ContractError("unsupported installed lock schema")
    allowed_fields = {
        "schema_version",
        "installed_version",
        "previous_version",
        "verified_release_digest",
        "archive_sha256",
        "archive_verification",
        "release_manifest_sha256",
        "layout_version",
        "hook_contract_version",
        "profile_schema_compatibility",
        "updater_version",
        "updater_minimum_version",
        "installed_manifest",
        "installed_projection",
    }
    extras = set(lock) - allowed_fields
    if extras:
        raise ContractError(f"installed lock contains unknown/private fields: {sorted(extras)}")

    forbidden_nested_keys = {
        "timestamp",
        "created_at",
        "updated_at",
        "user",
        "username",
        "hostname",
        "credential",
        "credentials",
        "token",
        "secret",
    }

    def reject_private_keys(value: Any, location: str) -> None:
        if isinstance(value, Mapping):
            present = forbidden_nested_keys.intersection(value)
            if present:
                raise ContractError(
                    f"installed lock contains unstable/private fields at {location}: {sorted(present)}"
                )
            for key, child in value.items():
                reject_private_keys(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_private_keys(child, f"{location}[{index}]")

    reject_private_keys(lock, "lock")
    layout_version = lock.get("layout_version")
    hook_contract_version = lock.get("hook_contract_version")
    if not isinstance(layout_version, int) or isinstance(layout_version, bool) or layout_version < 1:
        raise ContractError("installed lock layout version is invalid")
    if (
        not isinstance(hook_contract_version, int)
        or isinstance(hook_contract_version, bool)
        or hook_contract_version < 1
    ):
        raise ContractError("installed lock hook contract version is invalid")
    compatibility = lock.get("profile_schema_compatibility")
    if not isinstance(compatibility, Mapping) or set(compatibility) != {
        "min",
        "max_exclusive",
    }:
        raise ContractError("installed lock profile compatibility is invalid")
    if strict_current:
        if layout_version != MANAGED_LAYOUT_VERSION:
            raise ContractError("installed lock uses a non-current layout version")
        if hook_contract_version != HOOK_CONTRACT_VERSION:
            raise ContractError("installed lock uses a non-current hook contract version")
        if compatibility != {
            "min": PROFILE_SCHEMA_COMPATIBILITY["min"],
            "max_exclusive": PROFILE_SCHEMA_COMPATIBILITY["max_exclusive"],
        }:
            raise ContractError("installed lock uses non-current profile compatibility")
    validate_semver(lock.get("installed_version"))
    if lock.get("previous_version") is not None:
        validate_semver(lock.get("previous_version"))
    updater = validate_semver(lock.get("updater_version"))
    minimum_updater = validate_semver(lock.get("updater_minimum_version"))
    require_updater_compatible(updater, minimum_updater)
    validate_sha256(lock.get("verified_release_digest"), field="verified_release_digest")
    validate_sha256(lock.get("release_manifest_sha256"), field="release_manifest_sha256")
    archive_digest = lock.get("archive_sha256")
    if archive_digest is None:
        if lock.get("archive_verification") != "unavailable-from-unpacked-source":
            raise ContractError("null archive digest must be unavailable-from-unpacked-source")
    else:
        validate_sha256(archive_digest, field="archive_sha256")
        if lock.get("archive_verification") != "sha256":
            raise ContractError("present archive digest must use sha256 verification")
    installed_manifest = lock.get("installed_manifest")
    if not isinstance(installed_manifest, Mapping):
        raise ContractError("installed lock lacks installed_manifest")
    if set(installed_manifest) != {"path", "sha256"}:
        raise ContractError("installed_manifest has unknown or missing fields")
    validate_relative_path(installed_manifest.get("path"), field="installed manifest path")
    validate_sha256(installed_manifest.get("sha256"), field="installed manifest sha256")
    projection = lock.get("installed_projection")
    if not isinstance(projection, list) or not projection:
        raise ContractError("installed lock lacks complete installed_projection")
    validate_installed_projection(projection)
    manifest_projection = next(
        (
            item
            for item in projection
            if item.get("id") == "metadata:installed-release-manifest"
        ),
        None,
    )
    if (
        manifest_projection is None
        or manifest_projection.get("target_path") != installed_manifest.get("path")
        or manifest_projection.get("sha256") != installed_manifest.get("sha256")
        or "digest_ref" in manifest_projection
    ):
        raise ContractError("installed manifest projection is not materialized exactly")

    if release_manifest is not None:
        manifest = validate_release_manifest(
            release_manifest, strict_current=strict_current
        )
        vendor = validate_vendor_dir(vendor_dir)
        manifest_sha = sha256_bytes(canonical_json_bytes(manifest))
        if lock.get("installed_version") != manifest["bugate_version"]:
            raise ContractError("installed lock/release manifest version mismatch")
        if minimum_updater != manifest["updater_minimum_version"]:
            raise ContractError("installed lock updater minimum differs from manifest")
        if layout_version != manifest["layout_version"]:
            raise ContractError("installed lock layout differs from manifest")
        if hook_contract_version != manifest["hook_contract_version"]:
            raise ContractError("installed lock hook contract differs from manifest")
        manifest_compatibility = manifest["profile_schema_compatibility"]
        if compatibility != {
            "min": manifest_compatibility["min"],
            "max_exclusive": manifest_compatibility["max_exclusive"],
        }:
            raise ContractError("installed lock profile compatibility differs from manifest")
        if lock.get("verified_release_digest") != manifest["self_digest"]:
            raise ContractError("installed lock/release manifest digest mismatch")
        if lock.get("release_manifest_sha256") != manifest_sha:
            raise ContractError("installed lock canonical manifest hash mismatch")
        if installed_manifest != {
            "path": f"{vendor}/{INSTALLED_MANIFEST_PATH}",
            "sha256": manifest_sha,
        }:
            raise ContractError("installed manifest metadata mismatch")
        expected = render_installed_projection(manifest, vendor)
        for item in expected:
            if item["id"] == "metadata:installed-release-manifest":
                item.pop("digest_ref", None)
                item["sha256"] = manifest_sha
        if projection != expected:
            raise ContractError("installed lock projection differs from rendered manifest")
    return copy.deepcopy(dict(lock))


def installed_lock_bytes(lock: Mapping[str, Any]) -> bytes:
    """Return deterministic bytes after rejecting unstable or unsafe fields."""

    validate_installed_lock(lock)
    return canonical_json_bytes(lock)
