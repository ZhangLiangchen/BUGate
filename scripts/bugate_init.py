#!/usr/bin/env python3
"""bugate init — perform a fresh BUGate imported-mode installation.

Sets up the DEFAULT usage mode (imported governance layer): the SUT test repo
stays the project root; the engine is vendored into it; the governance contract
(config + profile) is created there to be COMMITTED with the tests it guards.

    python3 scripts/bugate_init.py <sut-repo> [--vendor-dir .bugate]
                                   [--dry-run]

What it does, in order:

  1. loads the release-managed ownership projection and vendors its BUGate kit
     into ``<sut-repo>/<vendor-dir>/``;
  2. links runtime skill discovery: ``.claude/skills/<skill>`` and
     ``.agents/skills/<skill>`` → the vendored skill trees, keeps
     ``.codex/skills/<skill>`` as a legacy Codex compatibility bridge, and
     copies the Codex gate-review agents into ``.codex/agents/``;
  3. merges the BUGate hook blocks into the repo's ``.claude/settings.json``
     and ``.codex/hooks.json`` (the repo's own hooks are preserved; ours are
     appended when absent using stable BUGate ownership identities);
  4. scaffolds a committed ``bugate.config.yaml`` (the workspace-root marker)
     and ``bugate.profile.yaml`` (inert until ``guarded_path_regex`` is filled);
  5. creates the ``docs/usecases/`` skeleton;
  6. appends a marked, idempotent ignore block to the repo's root
     ``.gitignore`` (creating it if absent) so the default scorer outputs and
     local agent/memory state don't litter the SUT repo's ``git status`` — the
     SUT's own lines and the committed governance contract are left untouched;
  7. ensures the MACHINE-LEVEL memory bus (reuse-first, ADR-BUGATE-003): all
     governed repos on a machine share one running ``mcp-memory-service``
     instance, isolated by namespace tag — init never scaffolds a per-repo
     service, but it does reuse/restart/install-once the shared service through
     ``bin/memory-bus-ensure`` when needed;
  8. prints the acceptance steps — including the Codex re-trust caveat (hooks
     stay silently inactive until the changed hook hash is re-trusted) and the
     R4 negative control.

Everything is stdlib-only.  This command is intentionally fresh-install only:
if the vendor path already exists in any form, it exits before any target or
machine-state write and directs the operator to ``bugate_update.py`` /
``bugate-update``.  Upgrades have one implementation and one audit boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

# Importing the installer must not dirty either a release tree or a target with
# Python bytecode before the existing-install preflight has run.
sys.dont_write_bytecode = True

import bugate_install_contract as install_contract
import bugate_update_engine as update_engine
from bugate_core import find_engine_root

# Compatibility aliases used by the SUT-neutral purity guard and historical
# tooling.  The ownership catalog itself lives only in install_contract.
KIT_DIRS = list(install_contract.VENDOR_TREE_ROOTS)
KIT_FILES = list(install_contract.VENDOR_SINGLE_FILES)

# Codex plugins package the shared skills/hooks/MCP surface. BUGate still wires
# the Codex gate-review agents through the project-local installer channel so
# each governed SUT repo can review and commit the exact agent cards that govern
# its tests. The agent TOMLs travel inside the vendored kit and reference the
# skill through the official .agents/skills/bugate symlink this installer also
# creates, so one file resolves in the engine repo and in any SUT repo
# regardless of vendor dir.
CODEX_AGENTS_KIT_REL = install_contract.CODEX_GATE_AGENT_SOURCE_DIR

# Hook commands are templated on the vendor dir. ROOT is the governed WORKSPACE
# root, found via the committed config this installer scaffolds; the engine is
# then addressed at its known vendored location beneath it. When no config
# marks a workspace above CWD, the hook exits 0 (inert) — the same lazy-guard
# contract as the plugin channel's hooks.json, so both channels degrade
# identically instead of hard-blocking every write with a resolver error.
_ROOT_SNIPPET = (
    "ROOT=\"$(/usr/bin/env python3 -c 'import os; from pathlib import Path; "
    "p=Path.cwd(); print(os.environ.get(\"BUGATE_PROJECT_ROOT\") or "
    "next((str(c) for c in [p,*p.parents] if (c/\"bugate.config.yaml\").exists()), \"\"))')\"; "
    "[ -n \"$ROOT\" ] || exit 0; "
)


def _cmd(vendor_dir: str, script: str, *args: str) -> str:
    tail = (" " + " ".join(args)) if args else ""
    return _ROOT_SNIPPET + f'/usr/bin/env python3 "$ROOT/{vendor_dir}/scripts/{script}"{tail}'


def _bin_cmd(vendor_dir: str, command: str, *args: str) -> str:
    """Build a hook command for an executable vendored under ``bin/``."""

    tail = (" " + " ".join(args)) if args else ""
    return _ROOT_SNIPPET + f'"$ROOT/{vendor_dir}/bin/{command}"{tail}'


def hook_blocks(vendor_dir: str, runtime: str) -> dict:
    """Return the canonical identity-bearing hook fragments."""

    try:
        return install_contract.hook_fragments(vendor_dir, runtime)
    except install_contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc


def merge_hooks(existing: dict, blocks: dict, vendor_dir: str) -> tuple[dict, list[str]]:
    """Compatibility object API backed by the updater's semantic merger.

    Fresh init has no prior BUGate-owned projection.  Legacy hook adoption is
    deliberately left to the updater; command-string heuristics are never used
    as an ownership decision here.
    """

    runtime = next(
        (
            name
            for name in install_contract.SHARED_HOOK_TARGETS
            if blocks == install_contract.hook_fragments(vendor_dir, name)
        ),
        None,
    )
    if runtime is None:
        raise SystemExit("hook blocks are not the canonical install contract")
    target_path = install_contract.SHARED_HOOK_TARGETS[runtime]
    projection = [
        item
        for item in install_contract._hook_projection(vendor_dir)
        if item["runtime"] == runtime
    ]
    known = {item["hook_identity"] for item in projection}
    hooks = existing.get("hooks", {}) if isinstance(existing, Mapping) else {}
    found = {
        identity
        for entries in hooks.values()
        if isinstance(entries, list)
        for value in entries
        for identity in update_engine._identities(value) & known
    } if isinstance(hooks, Mapping) else set()
    if found:
        raise SystemExit(
            "fresh install cannot adopt existing canonical BUGate hook "
            "identities without prior lock authority; use bugate-update: "
            + ", ".join(sorted(found))
        )
    before = (json.dumps(existing, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        result = update_engine.merge_hook_file(
            before,
            prior_projection=(),
            new_projection=projection,
            target_path=target_path,
        )
    except (update_engine.UpdateEngineError, update_engine.OwnershipConflict) as exc:
        raise SystemExit(f"unsafe hook ownership in {target_path}: {exc}") from exc
    merged = json.loads(result.content)
    changed_events = [
        detail["event"]
        for detail in result.details
        if not detail.get("already_target")
    ]
    return merged, changed_events


CONFIG_SCAFFOLD = """\
# BUGate governed-workspace config — COMMIT this file (CHARTER §2.2 R2).
# It marks the workspace root: the gate engine (vendored at {vendor_dir}/)
# finds this repo by walking up from CWD to the nearest bugate.config.yaml.
profile: bugate.profile.yaml
"""

# Raw string: the sut_identity_terms example must reach the scaffolded file as
# a literal backslash-b (the simple YAML parser does not unescape), never as a
# 0x08 control character.
PROFILE_SCAFFOLD = r"""# BUGate SUT profile — COMMIT this file beside the tests it governs.
# Schema: {vendor_dir}/.shared/skills/bugate/references/profile-schema.md

# Per-UC fail-closed binding: each guarded test file maps to its own
# docs/usecases/<uc>/ artifact dir via the {{uc}} capture below.
artifact_dir_template: docs/usecases/{{uc}}/

# INERT until filled: add regexes (with a (?P<uc>...) capture) matching this
# repo's test layout to turn the physical write guard on. The layout is YOURS,
# not BUGate's — derive the binding with the vendored bugate-import skill
# ({vendor_dir}/.shared/skills/bugate-import/SKILL.md: matching rules, worked
# bindings for pytest / TS specs / Java CamelCase / Gherkin, verification
# protocol). Framework-neutral shape:
#   - "(^|/)<test-tree>/(?P<uc>uc[-_][a-z0-9_-]+)[.]<ext>$"
guarded_path_regex: []

required_precode_artifacts:
  - 01_business_brief.md
  - 02_testability.md
  - 03_inventory.yaml
  - 03a_test_cases.md
  - 03b_adversarial_cases.yaml

# Backward-compatible default: existing v0.3.x profiles remain unlocked unless
# this block is deliberately migrated to `mode: required` (example below).
role_governance:
  mode: off

# De-SUT identity defense (CHARTER A1): list THIS SUT's identity terms
# (product / internal-system / account names, as case-insensitive regexes) so
# the guard keeps them from seeping into the reusable vendored kit at
# {vendor_dir}/. This repo's own files are not the scan surface. The simple
# YAML parser does not unescape, so write \b literally (single backslash):
# sut_identity_terms:
#   - "\bmy-product-name\b"

# --- Optional waves (dormant until configured; recipes: IMPORT_PROMPT
# --- appendix and {vendor_dir}/.shared/skills/bugate-import/references/field-guide.md) ---
# Wave 7 role isolation: uncomment and adapt, then run with
# BUGATE_AGENT_ROLE=<role>. Bare list = forbidden for read AND write;
# read:/write: sub-lists scope each side. Role names lowercase.
# agent_roles:
#   implementer:
#     - "^docs/raw/source_code/.*"
#   designer:
#     write:
#       - "^tests/.*"
#
# Wave 7 auditable lifecycle governance is separate from `agent_roles` path
# isolation. To migrate, replace the active `role_governance` block above with
# this shape. Existing passed UCs are NOT grandfathered: after a real human
# accepts 03B, create fresh handoff/acceptance receipts in three distinct role
# sessions. `memory_mode: required` makes each transition require an exact
# Memory handoff ID; ordinary edits verify the local hash-linked receipts only.
# role_governance:
#   mode: required
#   memory_mode: required
#   evidence_dir: 00_role_evidence
#   session_id_required: true
#   require_distinct_sessions: true
#   human_acceptance_artifacts:
#     - 03b_adversarial_cases.yaml
#   phases:
#     pre_code:
#       allowed_roles:
#         - designer
#     implementation:
#       allowed_roles:
#         - implementer
#       requires_handoff_from:
#         - designer
#     post_run:
#       allowed_roles:
#         - reviewer
#       requires_handoff_from:
#         - implementer
# Wave 8 oracle falsification: point at a real spec once captured evidence
# exists (evidence paths inside the spec resolve relative to the spec file).
# falsification_spec: <path/to/falsification_spec.yaml>
# falsification_threshold: 0.7
# wave8_evidence_glob: <workspace-relative glob>
# wave8_reports_dir: <workspace-relative dir, prefer gitignored>
# wave8_artifact_root: <inventory scan root>

# Memory-bus namespace on the MACHINE-LEVEL shared service (ADR-BUGATE-003):
# every governed repo on this machine shares one mcp-memory-service instance
# (data home ~/.bugate/memory-bus), isolated by this tag. Declaring the
# namespace is ALL the memory setup this repo needs — never scaffold or start
# a per-repo service dir.
memory:
  namespace: project:{name}
"""

# Shared surfaces and their marker text are catalog data, not installer-owned
# copies.  Keep these names as compatibility aliases for focused tests.
GITIGNORE_BEGIN = install_contract.GITIGNORE_BEGIN
GITIGNORE_END = install_contract.GITIGNORE_END
GITIGNORE_BLOCK = install_contract.GITIGNORE_BLOCK_TEMPLATE


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _mode(metadata: os.stat_result) -> str:
    return f"{stat.S_IMODE(metadata.st_mode):04o}"


def _safe_relative_path(root: Path, relative: str) -> Path:
    try:
        value = install_contract.validate_relative_path(relative)
    except install_contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc
    return root / value


def _check_real_directory_chain(root: Path, relative: str, *, include_leaf: bool = False) -> None:
    """Reject symlink/special ancestors before a fresh-install target write."""

    parts = Path(install_contract.validate_relative_path(relative)).parts
    limit = len(parts) if include_leaf else max(0, len(parts) - 1)
    current = root
    for part in parts[:limit]:
        current = current / part
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SystemExit(f"cannot inspect target path {current}: {exc}") from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise SystemExit(f"unsafe non-directory target ancestor: {current}")


def _read_regular_target(path: Path, *, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        # A raced FIFO/device must fail type validation rather than block init.
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"cannot open {label} as a non-symlink file: {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SystemExit(f"{label} must be a regular non-symlink file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise SystemExit(f"{label} changed while it was being read: {path}")
        try:
            bound = os.lstat(path)
        except OSError as exc:
            raise SystemExit(f"{label} path changed while it was being read: {path}") from exc
        if (
            stat.S_ISLNK(bound.st_mode)
            or not stat.S_ISREG(bound.st_mode)
            or (bound.st_dev, bound.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise SystemExit(f"{label} path binding changed while it was being read: {path}")
        return b"".join(chunks), after
    except OSError as exc:
        raise SystemExit(f"cannot read {label}: {path}: {exc}") from exc
    finally:
        os.close(descriptor)


def _installer_version() -> str:
    from bugate_update import UPDATER_VERSION

    try:
        return install_contract.validate_semver(UPDATER_VERSION)
    except install_contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc


def load_install_manifest(engine_root: Path) -> tuple[dict[str, Any], str]:
    """Load a formal release manifest or build an explicit development image."""

    version = _installer_version()
    path = engine_root / install_contract.RELEASE_MANIFEST_PATH
    if _lexists(path):
        try:
            manifest = update_engine.load_release_manifest(
                path,
                expected_version=version,
            )
        except update_engine.UpdateEngineError as exc:
            raise SystemExit(f"invalid formal release manifest: {exc}") from exc
        return manifest, "formal-release-manifest"
    try:
        manifest = install_contract.build_release_manifest(engine_root, version)
        manifest = install_contract.validate_current_release_manifest(
            manifest,
            expected_version=version,
        )
    except (OSError, install_contract.ContractError) as exc:
        raise SystemExit(f"cannot build deterministic development manifest: {exc}") from exc
    return manifest, "deterministic-development-fallback"


def _freeze_release_payloads(
    engine_root: Path,
    manifest: Mapping[str, Any],
    projection: Iterable[Mapping[str, Any]],
) -> dict[str, bytes]:
    """Verify and freeze every physical install source before target writes."""

    inventory = {item["path"]: item for item in manifest["archive_inventory"]}
    source_paths = sorted(
        {
            item["source_path"]
            for item in projection
            if item.get("scope") in {"vendor", "workspace"}
            and isinstance(item.get("source_path"), str)
        }
    )
    payloads: dict[str, bytes] = {}
    for relative in source_paths:
        expected = inventory.get(relative)
        if expected is None:
            raise SystemExit(f"manifest projection source is absent from inventory: {relative}")
        _check_real_directory_chain(engine_root, relative)
        path = _safe_relative_path(engine_root, relative)
        try:
            details = os.lstat(path)
        except OSError as exc:
            raise SystemExit(f"release payload is missing: {relative}: {exc}") from exc
        kind = expected["type"]
        if kind == "directory":
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SystemExit(f"release payload type mismatch: {relative}")
            continue
        if kind == "symlink":
            if not stat.S_ISLNK(details.st_mode) or os.readlink(path) != expected["target"]:
                raise SystemExit(f"release payload symlink mismatch: {relative}")
            continue
        if kind != "file" or stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise SystemExit(f"release payload type mismatch: {relative}")
        data, after = _read_regular_target(path, label="release payload")
        actual_mode = "0755" if stat.S_IMODE(after.st_mode) & 0o111 else "0644"
        if actual_mode != expected["mode"]:
            raise SystemExit(
                f"release payload mode mismatch: {relative}: "
                f"expected {expected['mode']}, actual {actual_mode}"
            )
        actual_hash = install_contract.sha256_bytes(data)
        if actual_hash != expected["sha256"]:
            raise SystemExit(
                f"release payload hash mismatch: {relative}: "
                f"expected {expected['sha256']}, actual {actual_hash}"
            )
        payloads[relative] = data
    return payloads


def _target_matches_item(path: Path, item: Mapping[str, Any]) -> bool:
    try:
        details = os.lstat(path)
    except OSError:
        return False
    kind = item["type"]
    if kind == "directory":
        return (
            not stat.S_ISLNK(details.st_mode)
            and stat.S_ISDIR(details.st_mode)
            and _mode(details) == item["mode"]
        )
    if kind == "symlink":
        return stat.S_ISLNK(details.st_mode) and os.readlink(path) == item["target"]
    if kind == "file":
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            return False
        data, details = _read_regular_target(path, label="existing managed target")
        return (
            _mode(details) == item["mode"]
            and install_contract.sha256_bytes(data) == item.get("sha256")
        )
    return False


def _preflight_physical_targets(
    target: Path, projection: Iterable[Mapping[str, Any]]
) -> None:
    for item in projection:
        if item.get("scope") != "workspace":
            continue
        relative = item["target_path"]
        _check_real_directory_chain(target, relative)
        path = _safe_relative_path(target, relative)
        if _lexists(path):
            raise SystemExit(
                "fresh install cannot adopt an existing BUGate-exclusive workspace "
                f"target without prior lock authority: {relative}; use bugate-update"
            )


def _read_optional_shared_target(
    target: Path, relative: str
) -> tuple[bytes | None, dict[str, Any]]:
    _check_real_directory_chain(target, relative)
    path = _safe_relative_path(target, relative)
    parent_details: os.stat_result | None = None
    if _lexists(path.parent):
        parent_details = os.lstat(path.parent)
        if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
            raise SystemExit(f"shared managed parent is unsafe: {path.parent}")
    if not _lexists(path):
        base: dict[str, Any] = {"state": "absent"}
        if parent_details is not None:
            base.update(
                parent_device=parent_details.st_dev,
                parent_inode=parent_details.st_ino,
            )
        return None, base
    data, details = _read_regular_target(path, label="shared managed container")
    if parent_details is None:
        raise SystemExit(f"shared managed parent disappeared: {path.parent}")
    return data, {
        "state": "file",
        "parent_device": parent_details.st_dev,
        "parent_inode": parent_details.st_ino,
        "device": details.st_dev,
        "inode": details.st_ino,
        "mode": _mode(details),
        "sha256": install_contract.sha256_bytes(data),
    }


def prepare_shared_outputs(
    target: Path,
    projection: Iterable[Mapping[str, Any]],
    *,
    vendor_dir: str = ".bugate",
) -> list[dict[str, Any]]:
    """Preflight surgical hook/block merges without changing the target."""

    items = [dict(item) for item in projection]
    prepared: list[dict[str, Any]] = []
    hook_images: dict[str, bytes | None] = {}
    hook_bases: dict[str, dict[str, Any]] = {}
    hook_targets = sorted(
        {
            item["target_path"]
            for item in items
            if item.get("scope") == "shared_json_fragment"
        }
    )
    for target_path in hook_targets:
        existing, base = _read_optional_shared_target(target, target_path)
        hook_images[target_path] = existing
        hook_bases[target_path] = base
    known_identities = {
        item["hook_identity"]
        for item in items
        if item.get("scope") == "shared_json_fragment"
        and isinstance(item.get("hook_identity"), str)
    }
    vendor = install_contract.validate_vendor_dir(vendor_dir)
    legacy_markers = (f"{vendor}/scripts/", f"{vendor}/bin/")
    for target_path, existing in hook_images.items():
        if existing is None:
            continue
        try:
            document = update_engine._json_object_bytes(
                existing,
                label=f"hook file {target_path}",
            )
        except update_engine.UpdateEngineError as exc:
            raise SystemExit(f"unsafe hook ownership in {target_path}: {exc}") from exc
        hooks = document.get("hooks", {})
        if isinstance(hooks, Mapping):
            found = {
                identity
                for entries in hooks.values()
                if isinstance(entries, list)
                for value in entries
                for identity in update_engine._identities(value) & known_identities
            }
            if found:
                raise SystemExit(
                    "fresh install cannot adopt existing canonical BUGate hook "
                    "identities without prior lock authority; use bugate-update: "
                    + ", ".join(sorted(found))
                )
            # A complete no-ID entry whose every command invokes the reserved
            # vendor runtime is a legacy/partial-install signal, not fresh SUT
            # wiring.  This heuristic only refuses init; it never establishes
            # ownership or rewrites the entry. Mixed entries remain SUT-owned
            # and are preserved alongside independent canonical fragments.
            legacy_events = [
                str(event)
                for event, entries in hooks.items()
                if isinstance(entries, list)
                for value in entries
                if (commands := update_engine._commands(value))
                and all(
                    any(marker in command for marker in legacy_markers)
                    for command in commands
                )
            ]
            if legacy_events:
                raise SystemExit(
                    "fresh install found a legacy BUGate-only hook entry without "
                    "lock authority; use bugate-update: "
                    + ", ".join(sorted(set(legacy_events)))
                )
    for target_path in hook_targets:
        existing = hook_images[target_path]
        base = hook_bases[target_path]
        try:
            merged = update_engine.merge_hook_file(
                existing,
                prior_projection=(),
                new_projection=items,
                target_path=target_path,
            )
        except (update_engine.UpdateEngineError, update_engine.OwnershipConflict) as exc:
            raise SystemExit(f"unsafe hook ownership in {target_path}: {exc}") from exc
        prepared.append(
            {
                "target_path": target_path,
                "base": base,
                "content": merged.content,
                "changed": merged.changed,
                "kind": "hook",
            }
        )
    try:
        # The updater's global check catches a known ID moved to another event,
        # runtime file, mixed command entry, or duplicate occurrence.
        update_engine._validate_global_hook_identities(
            target,
            items,
            container_images=hook_images,
        )
    except (update_engine.UpdateEngineError, update_engine.OwnershipConflict) as exc:
        raise SystemExit(f"unsafe hook ownership identity: {exc}") from exc

    block = next(
        item for item in items if item.get("scope") == "marked_text_block"
    )
    existing, base = _read_optional_shared_target(target, block["target_path"])
    try:
        merged = update_engine.merge_marked_block(
            existing,
            prior_item=None,
            new_item=block,
        )
    except (update_engine.UpdateEngineError, update_engine.OwnershipConflict) as exc:
        raise SystemExit(f"unsafe managed .gitignore block: {exc}") from exc
    prepared.append(
        {
            "target_path": block["target_path"],
            "base": base,
            "content": merged.content,
            "changed": merged.changed,
            "kind": "gitignore",
        }
    )
    return prepared


def _mkdir_real_parents(root: Path, relative: str) -> None:
    parts = Path(install_contract.validate_relative_path(relative)).parts[:-1]
    current = root
    for part in parts:
        current = current / part
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o755)
            except FileExistsError:
                details = os.lstat(current)
                if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                    raise SystemExit(f"unsafe target parent appeared during install: {current}")
            else:
                os.chmod(current, 0o755)
                continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise SystemExit(f"unsafe non-directory target ancestor: {current}")


def _write_new_file(path: Path, data: bytes, mode: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, int(mode, 8))
    except OSError as exc:
        raise SystemExit(f"managed target appeared or is unsafe: {path}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, int(mode, 8))
    finally:
        os.close(descriptor)


def _read_regular_at(
    parent_fd: int, name: str, *, label: str
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SystemExit(f"{label} is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
            if getattr(before, field) != getattr(after, field):
                raise SystemExit(f"{label} changed while it was being read")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _write_new_file_at(
    parent_fd: int, name: str, data: bytes, mode: str
) -> os.stat_result:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, int(mode, 8), dir_fd=parent_fd)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short managed file write")
            view = view[written:]
        os.fchmod(descriptor, int(mode, 8))
        os.fsync(descriptor)
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def install_physical_projection(
    target: Path,
    projection: Iterable[Mapping[str, Any]],
    payloads: Mapping[str, bytes],
    *,
    vendor_dir: str,
    dry: bool,
) -> list[str]:
    items = [
        dict(item)
        for item in projection
        if item.get("scope") in {"vendor", "workspace"}
    ]
    items.sort(
        key=lambda item: (
            len(Path(item["target_path"]).parts),
            0 if item["type"] == "directory" else 1,
            item["target_path"],
        )
    )
    vendor = install_contract.validate_vendor_dir(vendor_dir)
    vendor_path = target / vendor
    notes: list[str] = [f"create exclusive vendor root {vendor}/"]
    vendor_identity: tuple[int, int] | None = None
    if not dry:
        _mkdir_real_parents(target, vendor)
        try:
            vendor_path.mkdir(mode=0o755)
        except FileExistsError as exc:
            raise SystemExit(
                "vendor root appeared after fresh-install preflight; refusing adoption: "
                f"{vendor_path}"
            ) from exc
        os.chmod(vendor_path, 0o755)
        vendor_details = os.lstat(vendor_path)
        vendor_identity = (vendor_details.st_dev, vendor_details.st_ino)
    for item in items:
        relative = item["target_path"]
        path = _safe_relative_path(target, relative)
        notes.append(f"install managed {item['type']} {relative}")
        if dry:
            continue
        if item["scope"] == "vendor":
            current_vendor = os.lstat(vendor_path)
            if (
                stat.S_ISLNK(current_vendor.st_mode)
                or not stat.S_ISDIR(current_vendor.st_mode)
                or (current_vendor.st_dev, current_vendor.st_ino) != vendor_identity
            ):
                raise SystemExit("vendor root identity changed during fresh install")
        if _lexists(path):
            if item["scope"] == "workspace":
                raise SystemExit(
                    "BUGate-exclusive workspace target appeared during install: "
                    f"{relative}"
                )
            if item["type"] == "directory" and _target_matches_item(path, item):
                continue
            raise SystemExit(f"managed vendor target appeared during install: {relative}")
        _mkdir_real_parents(target, relative)
        if item["type"] == "directory":
            try:
                path.mkdir(mode=0o755)
            except FileExistsError as exc:
                raise SystemExit(f"managed directory appeared during install: {relative}") from exc
            os.chmod(path, 0o755)
        elif item["type"] == "symlink":
            try:
                path.symlink_to(item["target"])
            except OSError as exc:
                raise SystemExit(f"cannot create managed symlink {relative}: {exc}") from exc
        elif item["type"] == "file":
            source = item.get("source_path")
            if source not in payloads:
                raise SystemExit(f"frozen release payload is missing: {source}")
            data = payloads[source]
            if install_contract.sha256_bytes(data) != item["sha256"]:
                raise SystemExit(f"frozen release payload hash mismatch: {source}")
            _write_new_file(path, data, item["mode"])
        else:
            raise SystemExit(f"unsupported managed item type: {item['type']}")
    return notes


def _base_image_matches(
    data: bytes, details: os.stat_result, base: Mapping[str, Any]
) -> bool:
    return (
        base.get("state") == "file"
        and (details.st_dev, details.st_ino) == (base["device"], base["inode"])
        and _mode(details) == base["mode"]
        and install_contract.sha256_bytes(data) == base["sha256"]
    )


def _leaf_exists_at(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _open_bound_parent(path: Path, base: Mapping[str, Any]) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path.parent, flags)
    details = os.fstat(descriptor)
    expected = (base.get("parent_device"), base.get("parent_inode"))
    if None not in expected and (details.st_dev, details.st_ino) != expected:
        os.close(descriptor)
        raise SystemExit(f"shared managed parent changed after preflight: {path.parent}")
    bound = os.lstat(path.parent)
    if (
        stat.S_ISLNK(bound.st_mode)
        or not stat.S_ISDIR(bound.st_mode)
        or (bound.st_dev, bound.st_ino) != (details.st_dev, details.st_ino)
    ):
        os.close(descriptor)
        raise SystemExit(f"shared managed parent binding changed: {path.parent}")
    return descriptor


def _restore_backup_at(parent_fd: int, backup: str, leaf: str) -> bool:
    """Restore by no-replace hard link; never overwrite a concurrent SUT path."""

    try:
        os.link(
            backup,
            leaf,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        return False
    os.unlink(backup, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return True


def _replace_bound_file(path: Path, content: bytes, base: Mapping[str, Any]) -> None:
    """Replace a shared path without overwriting a late pathname writer.

    Existing content is first renamed to an fd-anchored backup and verified.
    The post-image is fully written/fsynced to an O_EXCL staged leaf, then
    hard-linked no-replace, so a SUT path created after the rename wins and
    init fails closed. POSIX cannot fence a process that keeps
    an already-open descriptor to the renamed inode; a detected change is
    restored or retained for diagnosis rather than discarded.
    """

    parent_fd = _open_bound_parent(path, base)
    leaf = path.name
    try:
        if base["state"] == "absent":
            if _leaf_exists_at(parent_fd, leaf):
                raise SystemExit(
                    f"shared managed container appeared after preflight: {path}"
                )
            staged = f".{leaf}.bugate-init-new-{secrets.token_hex(16)}"
            try:
                _write_new_file_at(parent_fd, staged, content, "0644")
            except BaseException as exc:
                raise SystemExit(
                    f"shared managed staged write failed: {path}; "
                    f"diagnostic staged leaf retained as {staged}"
                ) from exc
            try:
                os.link(
                    staged,
                    leaf,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except BaseException as exc:
                raise SystemExit(
                    f"shared managed container appeared during install: {path}; "
                    f"diagnostic staged leaf retained as {staged}"
                ) from exc
            os.unlink(staged, dir_fd=parent_fd)
            os.fsync(parent_fd)
            return

        try:
            current, current_details = _read_regular_at(
                parent_fd,
                leaf,
                label="shared managed container",
            )
        except (FileNotFoundError, OSError, SystemExit) as exc:
            raise SystemExit(
                f"shared managed container changed after preflight: {path}"
            ) from exc
        if not _base_image_matches(current, current_details, base):
            raise SystemExit(f"shared managed container changed after preflight: {path}")

        backup = f".{leaf}.bugate-init-backup-{secrets.token_hex(16)}"
        if _leaf_exists_at(parent_fd, backup):
            raise SystemExit(f"installer backup path collision beside {path}")
        os.rename(
            leaf,
            backup,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        backup_data, backup_details = _read_regular_at(
            parent_fd,
            backup,
            label="shared managed backup",
        )
        if not _base_image_matches(backup_data, backup_details, base):
            restored = _restore_backup_at(parent_fd, backup, leaf)
            suffix = "" if restored else f"; backup retained as {backup}"
            raise SystemExit(
                f"shared managed container changed during install: {path}{suffix}"
            )

        staged = f".{leaf}.bugate-init-new-{secrets.token_hex(16)}"
        if _leaf_exists_at(parent_fd, staged):
            _restore_backup_at(parent_fd, backup, leaf)
            raise SystemExit(f"installer staged path collision beside {path}")
        try:
            installed_details = _write_new_file_at(
                parent_fd,
                staged,
                content,
                base["mode"],
            )
        except BaseException as exc:
            # Never unlink a failed staged pathname after an inode check: no
            # POSIX compare-and-unlink exists, so that would reintroduce a
            # stat/unlink race. Retain it diagnostically and restore the final
            # SUT path no-replace.
            restored = _restore_backup_at(parent_fd, backup, leaf)
            suffix = "" if restored else f"; backup retained as {backup}"
            raise SystemExit(
                f"shared managed staged write failed: {path}{suffix}; "
                f"diagnostic staged leaf retained as {staged}"
            ) from exc
        try:
            # hard-link publication is no-replace: a pathname writer arriving
            # after the backup rename wins, and its content is never removed.
            os.link(
                staged,
                leaf,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except BaseException as exc:
            backup_data, backup_details = _read_regular_at(
                parent_fd,
                backup,
                label="shared managed backup",
            )
            if _base_image_matches(backup_data, backup_details, base):
                if _leaf_exists_at(parent_fd, leaf):
                    os.unlink(backup, dir_fd=parent_fd)
                else:
                    _restore_backup_at(parent_fd, backup, leaf)
            raise SystemExit(
                f"shared managed container raced during install: {path}; "
                f"diagnostic staged leaf retained as {staged}"
            ) from exc
        os.unlink(staged, dir_fd=parent_fd)
        os.fsync(parent_fd)

        backup_data, backup_details = _read_regular_at(
            parent_fd,
            backup,
            label="shared managed backup",
        )
        if not _base_image_matches(backup_data, backup_details, base):
            # There is no POSIX compare-and-unlink for the final pathname.
            # Never remove it after an identity/hash observation: a late SUT
            # pathname writer could replace it in that gap. Retain both the
            # current final image and the changed backup for diagnosis.
            raise SystemExit(
                f"shared managed container changed during install: {path}; "
                f"backup retained as {backup}"
            )
        os.unlink(backup, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def write_shared_outputs(
    target: Path, prepared: Iterable[Mapping[str, Any]], *, dry: bool
) -> list[str]:
    notes: list[str] = []
    for item in prepared:
        relative = item["target_path"]
        action = "update" if item["changed"] else "keep"
        notes.append(f"{action} {relative} ({item['kind']})")
        if dry or not item["changed"]:
            continue
        _mkdir_real_parents(target, relative)
        _replace_bound_file(
            _safe_relative_path(target, relative),
            item["content"],
            item["base"],
        )
    return notes


def scaffold_gitignore(target: Path, vendor_dir: str, dry: bool) -> list[str]:
    """Compatibility wrapper using the updater's exact marked-block merge."""

    manifest, _source = load_install_manifest(find_engine_root())
    projection = install_contract.render_installed_projection(manifest, vendor_dir)
    prepared = [
        item
        for item in prepare_shared_outputs(
            target,
            projection,
            vendor_dir=vendor_dir,
        )
        if item["kind"] == "gitignore"
    ]
    return write_shared_outputs(target, prepared, dry=dry)


def wire_hooks(target: Path, vendor_dir: str, dry: bool) -> list[str]:
    """Compatibility wrapper using canonical surgical hook merges."""

    manifest, _source = load_install_manifest(find_engine_root())
    projection = install_contract.render_installed_projection(manifest, vendor_dir)
    prepared = [
        item
        for item in prepare_shared_outputs(
            target,
            projection,
            vendor_dir=vendor_dir,
        )
        if item["kind"] == "hook"
    ]
    return write_shared_outputs(target, prepared, dry=dry)


NAMESPACE_REGISTRY = Path.home() / ".bugate" / "namespaces.tsv"


def _parse_namespace_registry(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit("BUGate namespace registry is not UTF-8") from exc
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if "\t" in line:
            namespace, path = line.split("\t", 1)
            entries[namespace.strip()] = path.strip()
    return entries


def _read_namespace_registry() -> dict[str, str]:
    if _lexists(NAMESPACE_REGISTRY):
        data, _details = _read_regular_target(
            NAMESPACE_REGISTRY,
            label="BUGate namespace registry",
        )
        return _parse_namespace_registry(data)
    return {}


def _memory_namespace(target: Path) -> tuple[str, bool]:
    """Collision-guarded default namespace for the machine-level shared bus.

    The bus is ONE service per machine isolated only by namespace tags, so two
    governed repos whose directories share a basename (e.g. two checkouts both
    named `backend`) would silently share `project:backend` and cross-pollute
    each other's memory. A tiny machine-local registry maps namespace -> repo
    path: the first repo keeps the plain name; a DIFFERENT repo hitting a taken
    name gets a short path-hash suffix. Deterministic and offline; re-running
    init on the same repo is idempotent. Returns (namespace, was_suffixed).
    """
    me = str(target.resolve())
    base = f"project:{target.resolve().name}"
    entries = _read_namespace_registry()
    if entries.get(base) in (None, me):
        return base, False
    suffix = hashlib.sha1(me.encode("utf-8")).hexdigest()[:4]
    return f"{base}-{suffix}", True


def _register_namespace(namespace: str, target: Path) -> None:
    me = str(target.resolve())
    parent = NAMESPACE_REGISTRY.parent
    if _lexists(parent):
        parent_details = os.lstat(parent)
        if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
            raise SystemExit(f"unsafe BUGate namespace registry parent: {parent}")
    else:
        parent.mkdir(mode=0o700, parents=True)
        parent_details = os.lstat(parent)
    before: bytes | None = None
    details: os.stat_result | None = None
    if _lexists(NAMESPACE_REGISTRY):
        before, details = _read_regular_target(
            NAMESPACE_REGISTRY,
            label="BUGate namespace registry",
        )
        entries = _parse_namespace_registry(before)
    else:
        entries = {}
    claimed = entries.get(namespace)
    if claimed == me:
        return
    if claimed is not None:
        raise SystemExit(
            "BUGate namespace was claimed by another repository during install; "
            "refusing overwrite"
        )
    entries[namespace] = me
    payload = "".join(
        f"{ns}\t{path}\n" for ns, path in sorted(entries.items())
    ).encode("utf-8")
    base: dict[str, Any] = {
        "state": "absent" if before is None else "file",
        "parent_device": parent_details.st_dev,
        "parent_inode": parent_details.st_ino,
    }
    if before is not None and details is not None:
        base.update(
            device=details.st_dev,
            inode=details.st_ino,
            mode=_mode(details),
            sha256=install_contract.sha256_bytes(before),
        )
    _replace_bound_file(NAMESPACE_REGISTRY, payload, base)


def preflight_scaffold(target: Path) -> None:
    """Validate every fresh-only scaffold target without writing it."""

    for relative in ("bugate.config.yaml", "bugate.profile.yaml"):
        _check_real_directory_chain(target, relative)
        path = target / relative
        if _lexists(path):
            _read_regular_target(path, label=f"existing {relative}")
    for relative in ("docs", "docs/usecases"):
        _check_real_directory_chain(target, relative, include_leaf=True)
        path = target / relative
        if _lexists(path):
            details = os.lstat(path)
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SystemExit(f"fresh scaffold path is not a real directory: {relative}")
    keep = target / "docs" / "usecases" / ".gitkeep"
    if _lexists(keep):
        _read_regular_target(keep, label="existing docs/usecases/.gitkeep")


def scaffold(
    target: Path,
    vendor_dir: str,
    dry: bool,
    *,
    namespace: str | None = None,
    register_namespace: bool = True,
) -> list[str]:
    notes = []
    resolved_namespace, suffixed = (
        _memory_namespace(target)
        if namespace is None
        else (namespace, namespace != f"project:{target.resolve().name}")
    )
    if suffixed:
        notes.append(
            f"memory.namespace: `{resolved_namespace}` (basename already claimed by another repo "
            f"in {NAMESPACE_REGISTRY} — path-hash suffix added to prevent cross-repo "
            "memory pollution; edit the profile if you prefer another tag)")
    files = {
        target / "bugate.config.yaml": CONFIG_SCAFFOLD.format(vendor_dir=vendor_dir),
        target / "bugate.profile.yaml": PROFILE_SCAFFOLD.format(
            vendor_dir=vendor_dir,
            name=resolved_namespace.removeprefix("project:"),
        ),
    }
    for path, body in files.items():
        if path.exists():
            notes.append(f"keep existing {path.name}")
            continue
        notes.append(f"scaffold {path.name}")
        if not dry:
            _write_new_file(path, body.encode("utf-8"), "0644")
            if path.name == "bugate.profile.yaml" and register_namespace:
                _register_namespace(resolved_namespace, target)
    skeleton = target / "docs" / "usecases"
    notes.append("mkdir docs/usecases/")
    if not dry:
        _mkdir_real_parents(target, "docs/usecases/.gitkeep")
        if not _lexists(skeleton):
            skeleton.mkdir(mode=0o755)
            os.chmod(skeleton, 0o755)
        keep = skeleton / ".gitkeep"
        if not _lexists(keep):
            _write_new_file(keep, b"", "0644")
    return notes


def bus_ensure(engine_root: Path, dry: bool) -> list[str]:
    """Ensure the REQUIRED machine-level memory bus is up (ADR-BUGATE-003).

    The memory bus is a CORE BUGate component (long-term memory, dual-agent
    progress sync + relay, memory promotion) — a BUGate setup is incomplete
    without it, so init treats it as a first-class step, not an optional probe.
    It is ONE service per machine shared by every governed repo (namespace-tag
    isolation): if it is already running, reuse it; if not, bring it up —
    ``bin/memory-bus-ensure`` reuses/restarts it, or installs it once
    (machine-level) on a first run. Still never blocks the import: install/start
    proceeds in the background and a slow first-time setup is reported, not fatal.
    """
    try:
        import memory_bus  # sibling module; loads client.env system-home-first

        memory_bus.load_local_env()
        url = memory_bus.base_url()
        home = memory_bus.memory_home()
        if memory_bus.service_available():
            return [
                f"memory-bus: RUNNING at {url} (data home {home}) — reusing the "
                "required machine-level shared instance; this repo only declares "
                "memory.namespace in its profile"
            ]
        if dry:
            return [f"memory-bus: not running at {url} — would install/start the required service via bin/memory-bus-ensure (machine-level, once)"]
        ensure = engine_root / "bin" / "memory-bus-ensure"
        if not ensure.exists():
            return [f"memory-bus: not running and {ensure} missing — engine tree incomplete; install per docs/SETUP-OPTIONAL.md §2"]
        try:
            proc = subprocess.run([str(ensure)], capture_output=True, text=True, timeout=120)
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = tail[-1] if tail else "(no output)"
        except Exception as exc:  # ensure must never block an import
            detail = f"{exc.__class__.__name__}: {exc}"
        if memory_bus.service_available():
            return [f"memory-bus: brought up the required machine-level service at {url}"]
        return [
            "memory-bus: required service not up yet — first-time install/start is "
            f"running in the background ({detail}). Watch with bin/memory-bus-status; "
            "it self-heals on the next session. BUGate is incomplete until it is up"
        ]
    except Exception as exc:  # never block an import
        return [f"memory-bus: ensure step had an issue ({exc.__class__.__name__}: {exc}) — required component; run bin/memory-bus-ensure and see docs/SETUP-OPTIONAL.md §2"]


NEXT_STEPS = """\
Imported-mode setup written. Next steps (CHARTER §2.2):

  1. Fill bugate.profile.yaml: add `guarded_path_regex` for this repo's test
     layout (keep the (?P<uc>...) capture). The scaffold has exactly one active
     guard key and keeps `role_governance.mode: off` for v0.3.x compatibility.
     To opt in, replace that block with the commented `mode: required` example;
     do not keep two active role_governance blocks.
  2. COMMIT: bugate.config.yaml, bugate.profile.yaml, {vendor_dir}/,
     .claude/ + .codex/ hook wiring, .claude/skills/, .agents/skills/,
     .codex/skills/ (legacy compatibility), .codex/agents/ (the Codex gate
     agents), docs/usecases/, and the updated .gitignore (a marked block backstops the
     default scorer outputs + local agent/memory state out of git status) — the
     governance contract reviews and versions with the tests it guards.
  3. Codex only: RE-TRUST the changed hook hash in the Codex hook-management
     UI after init. Until then Codex hooks are SILENTLY inactive (known
     behavior); do not claim Wave 7 is active. The
     .agents/skills/ skills and .codex/agents/ gate agents are picked up on the
     next Codex session (no re-trust needed — they are skills/agents, not hooks).
  4. Acceptance — R4 negative control: pick a guarded test path whose UC has no
     passed pre-code artifacts and confirm the block:
       python3 {vendor_dir}/scripts/check_bugate.py <a-guarded-test-file> </dev/null
       # expect exit 2 and the missing-artifact list (any language/extension)
  5. Per-UC setup from here on (do not combine --init and --auto; --init exits
     after scaffolding):
       python3 {vendor_dir}/scripts/sdtd_orchestrator.py docs/usecases/<UC> --init
  6. Required mode uses THREE independent role sessions. Start each from a
     clean terminal/Desktop launch environment; a hook subprocess cannot
     export identity back into its parent:
       {vendor_dir}/bin/bugate-role run --role designer -- <claude-or-codex-command>
       {vendor_dir}/bin/bugate-role run --role implementer -- <claude-or-codex-command>
       {vendor_dir}/bin/bugate-role run --role reviewer -- <claude-or-codex-command>
     The designer runs pre-code `--auto` only after `--init` has completed.
     The peer bridge may leave 03B pending; an agent MUST NOT approve it or
     impersonate a human. After a real human explicitly reviews and accepts
     03B, the designer records that already-made decision with the human's
     identifier (the CLI does not edit 03B), then hands off. Run these INSIDE
     the designer session:
       {vendor_dir}/bin/bugate-role approve docs/usecases/<UC> --approved-by <human-id>
       {vendor_dir}/bin/bugate-role handoff docs/usecases/<UC> --phase pre_code --to implementer
     Do NOT re-run `--auto` after human acceptance because it may overwrite
     03B; finalize/handoff directly.
     In a NEW implementer session, accept with the exact Memory ID printed by
     the handoff before Layer 4. After implementation, hand off the concrete
     guarded files:
       {vendor_dir}/bin/bugate-role accept docs/usecases/<UC> --phase implementation --handoff-id <exact-memory-id>
       {vendor_dir}/bin/bugate-role handoff docs/usecases/<UC> --phase implementation --to reviewer --implementation-file <guarded-test-file>
     In a NEW reviewer session, accept the second exact Memory ID before
     post-run / 04 / 05 and reviewer completion:
       {vendor_dir}/bin/bugate-role accept docs/usecases/<UC> --phase post_run --handoff-id <exact-memory-id>
  7. Memory bus (REQUIRED, machine-level): a BUGate setup is incomplete without
     it (long-term memory, dual-agent progress sync + relay, memory promotion).
     Init already ensured it above — ONE shared mcp-memory-service per machine,
     auto-installed once if it was absent; this repo just declares its profile
     namespace. Check with {vendor_dir}/bin/memory-bus-status; if a first-time
     install is still finishing it self-heals on the next session
     ({vendor_dir}/bin/memory-bus-ensure re-checks). Offline/locked-down machine:
     BUGATE_MEMORY_NO_INSTALL=1 skips auto-install (then install manually per
     docs/SETUP-OPTIONAL.md §2).
     With `memory_mode: required`, Memory failure blocks the next lifecycle
     transition before any local unlock receipt is published. SessionStart
     recall and Stop heartbeat remain best-effort, and ordinary edits validate
     local receipts without a live Memory request.
  8. ALL post-import guidance lives under ONE vendored skill —
     {vendor_dir}/.shared/skills/bugate-import/ (also linked into
     .claude/.agents/.codex skills):
       SKILL.md                          adaptation principle + layout wiring
       references/using-bugate.md        day-to-day operator manual (中文:
                                         using-bugate.zh-CN.md) — HAND THIS
                                         TO THE USER
       references/field-guide.md         operations & diagnosis
     Machine runtime setup (peer CLIs / memory service / offline fallback):
     {vendor_dir}/docs/SETUP-OPTIONAL.md. Optional one-shot self-check:
       python3 {vendor_dir}/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke

Future upgrades do NOT re-run bugate_init.py. From this repo root use:
  {vendor_dir}/bin/bugate-update status
  {vendor_dir}/bin/bugate-update plan --to <version>
  {vendor_dir}/bin/bugate-update apply --to <version>
  {vendor_dir}/bin/bugate-update verify
"""


def session_alignment_note(target: Path) -> list[str]:
    """Warn when the import target is not the git toplevel (monorepo subdir).

    Hook wiring loads from the workspace an agent SESSION is rooted at. If the
    target sits below a larger repo's root, a session opened at that root never
    loads the target's .claude/settings.json / .codex/hooks.json and the
    physical guard is silently absent. Detect the misalignment at install time
    and tell the operator loudly; git-less targets are fine (no signal).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []  # not a git repo: alignment is the operator's call, no signal
    toplevel = Path(result.stdout.strip()).resolve()
    if toplevel == target.resolve():
        return []
    return [
        "!! SESSION ALIGNMENT WARNING: import target is a SUBDIRECTORY of a larger "
        f"git repo (target={target}, git root={toplevel}). Agent sessions MUST open "
        f"{target} as their project root, or the hook wiring installed here is never "
        "loaded and the physical write guard is SILENTLY ABSENT. Alternatives: open "
        f"sessions at {target}, or export BUGATE_PROJECT_ROOT={target} in the agent's "
        "environment. See the vendored bugate-import skill, 'Session/workspace alignment'.",
    ]


def _existing_install_message(target: Path, vendor_dir: str) -> str:
    return f"""\
existing BUGate vendor path detected: {target / vendor_dir}
bugate_init.py is fresh-install only; no target or machine state was changed.

For a supported v0.3.x imported installation, bootstrap from an unpacked
v0.4.x release (do not re-run init):
  cd {target}
  python3 <unpacked-v0.4.x>/scripts/bugate_update.py plan . --vendor-dir {vendor_dir}
  python3 <unpacked-v0.4.x>/scripts/bugate_update.py apply . --vendor-dir {vendor_dir}

For a v0.4+ lock-based installation, use the vendored updater:
  cd {target}
  {vendor_dir}/bin/bugate-update status
  {vendor_dir}/bin/bugate-update plan --to <version>
  {vendor_dir}/bin/bugate-update apply --to <version>
  {vendor_dir}/bin/bugate-update verify
"""


def _verify_prelock_projection(
    target: Path,
    projection: Iterable[Mapping[str, Any]],
    *,
    vendor_dir: str,
) -> None:
    projection_items = [dict(item) for item in projection]
    _verify_no_unknown_vendor_paths(
        target,
        vendor_dir,
        projection_items,
        lock_expected=False,
    )
    expected = [
        dict(item)
        for item in projection_items
        if item.get("id") != "metadata:installed-lock"
    ]
    try:
        observed = update_engine.observe_projection(target, expected)
        diagnostics = update_engine._diagnostics(expected, observed)
        update_engine._validate_global_hook_identities(target, expected)
    except (update_engine.UpdateEngineError, update_engine.OwnershipConflict) as exc:
        raise SystemExit(f"fresh install pre-lock verification failed: {exc}") from exc
    if diagnostics:
        raise SystemExit(
            "fresh install pre-lock verification failed: "
            + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        )


def _verify_no_unknown_vendor_paths(
    target: Path,
    vendor_dir: str,
    projection: Iterable[Mapping[str, Any]],
    *,
    lock_expected: bool,
) -> None:
    """Reject unmanifested leaves/directories inside a fresh vendor root."""

    vendor = install_contract.validate_vendor_dir(vendor_dir)
    vendor_root = target / vendor
    expected = {
        item["target_path"]
        for item in projection
        if item.get("scope") == "vendor"
    }
    expected.add(f"{vendor}/{install_contract.INSTALLED_MANIFEST_PATH}")
    if lock_expected:
        expected.add(f"{vendor}/{install_contract.INSTALLED_LOCK_PATH}")
    actual: set[str] = set()
    for current, directories, filenames in os.walk(vendor_root, followlinks=False):
        current_path = Path(current)
        for name in directories + filenames:
            path = current_path / name
            actual.add(path.relative_to(target).as_posix())
            if path.is_symlink() and name in directories:
                directories.remove(name)
    unknown = sorted(actual - expected)
    if unknown:
        raise SystemExit(
            "unmanifested path appeared inside fresh vendor root: "
            + ", ".join(unknown)
        )


def _assert_root_identity(target: Path, identity: tuple[int, int]) -> None:
    try:
        details = os.lstat(target)
    except OSError as exc:
        raise SystemExit(f"target root became unavailable: {target}: {exc}") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or (details.st_dev, details.st_ino) != identity
    ):
        raise SystemExit("target root identity changed during fresh-install preflight")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="path to the SUT automation test repo")
    parser.add_argument("--vendor-dir", default=".bugate",
                        help="directory inside the SUT repo receiving the kit (default: .bugate)")
    parser.add_argument("--dry-run", action="store_true", help="print actions without writing")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().resolve()
    try:
        target_details = os.lstat(target)
    except OSError:
        raise SystemExit(f"target is not a directory: {target}")
    if stat.S_ISLNK(target_details.st_mode) or not stat.S_ISDIR(target_details.st_mode):
        raise SystemExit(f"target is not a real directory: {target}")
    try:
        vendor_dir = install_contract.validate_vendor_dir(args.vendor_dir)
    except install_contract.ContractError as exc:
        raise SystemExit(str(exc)) from exc
    _check_real_directory_chain(target, vendor_dir)
    vendor_path = target / vendor_dir
    # This check intentionally uses lexists/lstat semantics. A dangling
    # symlink, regular file, FIFO, unknown directory, or lock-based install all
    # have the same fresh-only result and no migration heuristics run here.
    if _lexists(vendor_path):
        try:
            os.lstat(vendor_path)
        except OSError as exc:
            raise SystemExit(f"cannot safely inspect existing vendor path: {exc}") from exc
        raise SystemExit(_existing_install_message(target, vendor_dir))

    root_identity = (target_details.st_dev, target_details.st_ino)
    engine_root = find_engine_root().resolve()
    if target == engine_root.resolve():
        raise SystemExit("target is the engine tree itself; run against the SUT repo")

    manifest, manifest_source = load_install_manifest(engine_root)
    try:
        lock = install_contract.build_installed_lock(
            manifest,
            previous_version=None,
            archive_sha256=None,
            vendor_dir=vendor_dir,
            updater_version=manifest["bugate_version"],
        )
    except install_contract.ContractError as exc:
        raise SystemExit(f"cannot build fresh installed lock: {exc}") from exc
    projection = lock["installed_projection"]
    payloads = _freeze_release_payloads(engine_root, manifest, projection)
    _preflight_physical_targets(target, projection)
    shared_outputs = prepare_shared_outputs(
        target,
        projection,
        vendor_dir=vendor_dir,
    )
    preflight_scaffold(target)
    namespace, _suffixed = _memory_namespace(target)
    profile_was_absent = not _lexists(target / "bugate.profile.yaml")
    _assert_root_identity(target, root_identity)

    notes: list[str] = [
        f"release manifest: {manifest_source} ({manifest['bugate_version']})",
        "installed archive digest: unavailable-from-unpacked-source",
    ]
    notes += session_alignment_note(target)
    notes += install_physical_projection(
        target,
        projection,
        payloads,
        vendor_dir=vendor_dir,
        dry=args.dry_run,
    )
    notes += write_shared_outputs(target, shared_outputs, dry=args.dry_run)
    notes += scaffold(
        target,
        vendor_dir,
        args.dry_run,
        namespace=namespace,
        register_namespace=False,
    )

    if not args.dry_run:
        manifest_path = target / vendor_dir / install_contract.INSTALLED_MANIFEST_PATH
        _write_new_file(
            manifest_path,
            install_contract.canonical_json_bytes(manifest),
            "0644",
        )
        _verify_prelock_projection(
            target,
            projection,
            vendor_dir=vendor_dir,
        )
        if profile_was_absent:
            _register_namespace(namespace, target)
        # The committed installed lock is the final target-repo write. All
        # managed files/shared fragments have already passed their post-image
        # verification at this point.
        lock_path = target / vendor_dir / install_contract.INSTALLED_LOCK_PATH
        _write_new_file(
            lock_path,
            install_contract.installed_lock_bytes(lock),
            "0644",
        )
        verified = update_engine.verify_installed(target, vendor_dir)
        _verify_no_unknown_vendor_paths(
            target,
            vendor_dir,
            projection,
            lock_expected=True,
        )
        if verified.get("decision") != "GO":
            raise SystemExit(
                "fresh install postcondition failed: "
                + json.dumps(verified, ensure_ascii=False, sort_keys=True)
            )
        notes.append(
            f"verify installed lock: GO ({verified['checked_items']} managed items)"
        )
    else:
        notes.append(f"would write {vendor_dir}/{install_contract.INSTALLED_MANIFEST_PATH}")
        notes.append(
            f"would write {vendor_dir}/{install_contract.INSTALLED_LOCK_PATH} last"
        )

    notes += bus_ensure(engine_root, args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    for note in notes:
        print(f"{prefix}{note}")
    print()
    print(NEXT_STEPS.format(vendor_dir=vendor_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
