#!/usr/bin/env python3
"""Auditable, SUT-neutral BUGate lifecycle role governance.

The module owns the local append-only role-evidence state machine.  Hooks and
the orchestrator call :func:`preflight`; humans and lifecycle actors advance
the state only through this CLI.  Local edit checks never contact the Memory
Service.  Transition commands do, and ``memory_mode: required`` fails before a
local receipt is published when the strict backend is unavailable.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - BUGate's hook/runtime surface is POSIX.
    fcntl = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bugate_core import (  # noqa: E402
    as_bool,
    find_root,
    gate_status,
    load_config,
    parse_nested_yaml,
    read_text,
    required_precode_artifacts,
)
import role_lineage as lineage_registry  # noqa: E402


ROLE_SCHEMA = "bugate.role-evidence/v1"
CHAIN_SCHEMA = "bugate.role-chain/v1"
TRANSITION_SCHEMA = "bugate.role-transition/v1"
LINEAGE_PRECONDITION_SCHEMA = "bugate.role-lineage-precondition/v1"
RECOVERY_ARCHIVE_SCHEMA = "bugate.role-recovery-archive/v1"
PHASES = ("pre_code", "implementation", "post_run")
BUGATE_ROLES = {"builder", "designer", "implementer", "reviewer", "human", "agent"}
LIFECYCLE_ROLES = {"designer", "implementer", "reviewer"}
MEMORY_MODES = {"best_effort", "required"}
GOVERNANCE_MODES = {"off", "advisory", "required"}
DISPATCH_MODES = {
    "real_peer_dispatch",
    "partial_real_peer_dispatch",
    "fallback_placeholder",
    "not_required",
}
DEFAULT_PHASES: dict[str, dict[str, list[str]]] = {
    "pre_code": {"allowed_roles": ["designer"], "requires_handoff_from": []},
    "implementation": {
        "allowed_roles": ["implementer"],
        "requires_handoff_from": ["designer"],
    },
    "post_run": {
        "allowed_roles": ["reviewer"],
        "requires_handoff_from": ["implementer"],
    },
}
EVENT_STATES = {
    "human_acceptance": "ready_for_designer_handoff",
    "designer_handoff": "awaiting_implementer_acceptance",
    "implementer_acceptance": "implementation_unlocked",
    "implementer_handoff": "awaiting_reviewer_acceptance",
    "reviewer_acceptance": "post_run_active",
    "reviewer_completion": "closed",
}
RECOVERY_EVENT = "evidence_recovery"
INITIAL_STATE = "awaiting_human_acceptance"
POSTRUN_NAMES = {"04_execution_report.md", "05_knowledge_update.md"}
PRECODE_PREFIX_RE = re.compile(r"^(?:01|02|03)(?:[ab])?[_-]", re.I)

_TRANSITION_THREAD_LOCKS: dict[str, threading.RLock] = {}
_TRANSITION_THREAD_LOCKS_GUARD = threading.Lock()
_TRANSITION_LOCK_STATE = threading.local()


class RoleGovernanceError(RuntimeError):
    """A fail-closed configuration, identity, transition, or evidence error."""


class RoleConfigError(RoleGovernanceError):
    """The role_governance contract is malformed."""


class _MemoryCheckpointNotFound(RoleGovernanceError):
    """Typed exact-checkpoint absence; outages and auth failures never use it."""


@dataclass(frozen=True)
class GovernanceContext:
    root: Path
    artifact_dir: Path
    config: dict[str, Any]
    policy: dict[str, Any]
    profile_path: Path
    uc: str

    @property
    def evidence_dir(self) -> Path:
        return self.artifact_dir / self.policy["evidence_dir"]

    @property
    def mode(self) -> str:
        return str(self.policy["mode"])


@dataclass
class GovernanceResult:
    allowed: bool
    mode: str
    phase: str
    state: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LineageIntegrity:
    """Local/registry alignment, deliberately separate from lifecycle state."""

    integrity_state: str
    lifecycle_state: str
    lineage_key: lineage_registry.LineageKey
    lineage_id: str
    registry: lineage_registry.LineageRegistry | None
    record: lineage_registry.LineageRecord | None
    active_transaction: lineage_registry.TransactionRecord | None
    chain: dict[str, Any] | None
    receipts: tuple[dict[str, Any], ...]
    local_error: str = ""
    registry_error: str = ""
    active_initialization: lineage_registry.InitializationRecord | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _transition_lock_key(ctx: GovernanceContext) -> str:
    try:
        root_stat = ctx.root.resolve().stat()
        artifact_stat = ctx.artifact_dir.resolve().stat()
    except OSError as exc:
        raise RoleGovernanceError(
            f"cannot identify role transition workspace: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_stat.st_mode) or not stat.S_ISDIR(artifact_stat.st_mode):
        raise RoleGovernanceError(
            "role transition workspace and artifact path must be directories"
        )
    identity = (
        f"{root_stat.st_dev}:{root_stat.st_ino}\0"
        f"{artifact_stat.st_dev}:{artifact_stat.st_ino}"
    ).encode("ascii")
    return sha256_bytes(identity)


def _thread_transition_lock(key: str) -> threading.RLock:
    with _TRANSITION_THREAD_LOCKS_GUARD:
        return _TRANSITION_THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _transition_lock(ctx: GovernanceContext):
    """Serialize one UC's complete transition/Memory/publication critical section."""

    if fcntl is None:
        raise RoleGovernanceError(
            "role transition locking requires the POSIX fcntl standard-library module"
        )
    key = _transition_lock_key(ctx)
    with _thread_transition_lock(key):
        artifact = ctx.artifact_dir.resolve()
        fd = -1
        locked = False
        registered = False
        try:
            try:
                # Lock the governed artifact directory itself.  Directory
                # flock is keyed by the filesystem object, so case/symlink
                # aliases, distinct TMPDIR settings, and cooperating OS users
                # all serialize on one UC without a persistent SUT lock file.
                flags = os.O_RDONLY
                if hasattr(os, "O_DIRECTORY"):
                    flags |= os.O_DIRECTORY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(artifact, flags)
                lock_stat = os.fstat(fd)
                artifact_stat = artifact.stat()
                if (
                    not stat.S_ISDIR(lock_stat.st_mode)
                    or (lock_stat.st_dev, lock_stat.st_ino)
                    != (artifact_stat.st_dev, artifact_stat.st_ino)
                ):
                    raise RoleGovernanceError(
                        "role transition lock path changed during acquisition"
                    )
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            except RoleGovernanceError:
                raise
            except OSError as exc:
                raise RoleGovernanceError(
                    f"cannot prepare role transition lock: {exc}"
                ) from exc
            held = set(getattr(_TRANSITION_LOCK_STATE, "keys", set()))
            held.add(key)
            _TRANSITION_LOCK_STATE.keys = held
            registered = True
            yield
        finally:
            if registered:
                held = set(getattr(_TRANSITION_LOCK_STATE, "keys", set()))
                held.discard(key)
                _TRANSITION_LOCK_STATE.keys = held
            if fd >= 0:
                if locked:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
                else:
                    os.close(fd)


def _serialized_transition(function: Callable[..., dict[str, Any]]):
    @wraps(function)
    def wrapped(artifact_dir: str | Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        ctx = load_context(artifact_dir)
        if ctx.mode == "off":
            return function(artifact_dir, *args, **kwargs)
        with _transition_lock(ctx):
            return function(artifact_dir, *args, **kwargs)

    return wrapped


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def receipt_sha256(receipt: dict[str, Any]) -> str:
    payload = copy.deepcopy(receipt)
    payload.pop("receipt_sha256", None)
    return sha256_bytes(canonical_json(payload))


def _same_existing_path(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _alternate_case(name: str) -> str | None:
    for index, char in enumerate(name):
        swapped = char.swapcase()
        if swapped != char:
            return name[:index] + swapped + name[index + 1 :]
    return None


def _filesystem_case_insensitive(path: Path) -> bool:
    """Probe actual directory lookup semantics on the workspace filesystem."""

    root = path.resolve()
    try:
        device = root.stat().st_dev
    except OSError:
        return False
    for directory in (root, *root.parents):
        try:
            if directory.stat().st_dev != device or not directory.is_dir():
                continue
            entries = list(directory.iterdir())
        except OSError:
            continue
        names = {entry.name for entry in entries}
        for entry in entries:
            alternate = _alternate_case(entry.name)
            if alternate is None or alternate in names:
                continue
            alias = directory / alternate
            try:
                alias_stat = alias.lstat()
            except OSError:
                return False
            if stat.S_ISLNK(alias_stat.st_mode):
                continue
            if _same_existing_path(entry, alias):
                return True
    return False


@lru_cache(maxsize=4096)
def _canonical_existing_path_cached(
    raw: str,
    device: int,
    inode: int,
    parent_mtime_ns: int,
    ancestor_fingerprint: tuple[tuple[int, int, int], ...],
) -> str:
    # Identity and directory metadata are cache-invalidation inputs; the names
    # make their purpose explicit even though traversal only needs ``raw``.
    # Every ancestor is included because renaming an ancestor entry changes its
    # parent directory, not necessarily the leaf or immediate parent inode.
    del device, inode, parent_mtime_ns, ancestor_fingerprint
    resolved = Path(raw)
    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        requested = current / part
        if not requested.exists() or not current.is_dir():
            current = requested
            continue
        try:
            entries = list(current.iterdir())
        except OSError:
            current = requested
            continue
        exact = next((entry for entry in entries if entry.name == part), None)
        if exact is not None:
            current = exact
            continue
        identities = [entry for entry in entries if _same_existing_path(entry, requested)]
        folded = [entry for entry in identities if entry.name.casefold() == part.casefold()]
        current = folded[0] if len(folded) == 1 else (
            identities[0] if len(identities) == 1 else requested
        )
    return current.as_posix()


def _canonical_existing_path(path: Path) -> Path:
    """Recover actual directory-entry spelling for an existing path.

    ``Path.resolve`` removes symlinks but preserves caller case on common APFS
    volumes.  Lifecycle identity must not depend on that spelling.  Cache keys
    include leaf identity plus every ancestor directory's identity/mtime so a
    replacement or case-only rename at any level refreshes the traversal.
    """

    resolved = path.resolve()
    try:
        identity = resolved.stat()
        parent = resolved.parent.stat()
        ancestor_fingerprint: list[tuple[int, int, int]] = []
        ancestor = resolved.parent
        while True:
            item = ancestor.stat()
            ancestor_fingerprint.append(
                (item.st_dev, item.st_ino, item.st_mtime_ns)
            )
            if ancestor.parent == ancestor:
                break
            ancestor = ancestor.parent
    except OSError:
        return resolved
    return Path(
        _canonical_existing_path_cached(
            resolved.as_posix(),
            identity.st_dev,
            identity.st_ino,
            parent.st_mtime_ns,
            tuple(ancestor_fingerprint),
        )
    )


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def workspace_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise RoleGovernanceError(f"path is outside BUGate workspace: {path}") from exc


def _list_of_roles(
    value: Any,
    where: str,
    *,
    allow_empty: bool,
    valid_roles: set[str] = BUGATE_ROLES,
) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        suffix = " (non-empty)" if not allow_empty else ""
        raise RoleConfigError(f"{where} must be a list{suffix}")
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            raise RoleConfigError(f"{where} entries must be non-empty role tokens")
        role = raw.strip().lower()
        if role not in valid_roles:
            raise RoleConfigError(
                f"{where} contains invalid BUGate role {raw!r}; model/runtime names are not roles"
            )
        if role not in out:
            out.append(role)
    return out


def _strict_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise RoleConfigError(f"{where} must be boolean")
    return value


def governance_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Return a validated policy with deterministic canonical defaults."""

    raw = config.get("role_governance")
    if raw is None:
        raw = {"mode": "off"}
    if not isinstance(raw, dict):
        raise RoleConfigError("role_governance must be a mapping")
    mode = raw.get("mode", "off")
    if not isinstance(mode, str) or mode not in GOVERNANCE_MODES:
        raise RoleConfigError("role_governance.mode must be off, advisory, or required")
    memory_mode = raw.get("memory_mode", "best_effort")
    if not isinstance(memory_mode, str) or memory_mode not in MEMORY_MODES:
        raise RoleConfigError(
            "role_governance.memory_mode must be best_effort or required"
        )
    evidence_dir = raw.get("evidence_dir", "00_role_evidence")
    if not isinstance(evidence_dir, str) or not evidence_dir.strip():
        raise RoleConfigError("role_governance.evidence_dir must be a relative path")
    evidence_path = Path(evidence_dir)
    if evidence_path.is_absolute() or ".." in evidence_path.parts or evidence_path == Path("."):
        raise RoleConfigError(
            "role_governance.evidence_dir must stay inside each artifact directory"
        )
    session_required = raw.get("session_id_required", True)
    distinct_sessions = raw.get("require_distinct_sessions", True)
    session_required = _strict_bool(session_required, "role_governance.session_id_required")
    distinct_sessions = _strict_bool(
        distinct_sessions, "role_governance.require_distinct_sessions"
    )
    human = raw.get("human_acceptance_artifacts", ["03b_adversarial_cases.yaml"])
    if not isinstance(human, list):
        raise RoleConfigError("role_governance.human_acceptance_artifacts must be a list")
    human_artifacts: list[str] = []
    for item in human:
        if not isinstance(item, str) or not item.strip():
            raise RoleConfigError("human acceptance artifact names must be non-empty strings")
        item_path = Path(item)
        if item_path.is_absolute() or ".." in item_path.parts:
            raise RoleConfigError("human acceptance artifacts must stay inside artifact_dir")
        human_artifacts.append(item_path.as_posix())

    phases_raw = raw.get("phases", {})
    if not isinstance(phases_raw, dict):
        raise RoleConfigError("role_governance.phases must be a mapping")
    unknown_phases = sorted(set(phases_raw) - set(PHASES))
    if unknown_phases:
        raise RoleConfigError(
            "role_governance.phases has unknown phase(s): " + ", ".join(unknown_phases)
        )
    phases: dict[str, dict[str, list[str]]] = {}
    for phase in PHASES:
        supplied = phases_raw.get(phase, {})
        if not isinstance(supplied, dict):
            raise RoleConfigError(f"role_governance.phases.{phase} must be a mapping")
        unknown = sorted(set(supplied) - {"allowed_roles", "requires_handoff_from"})
        if unknown:
            raise RoleConfigError(
                f"role_governance.phases.{phase} has unknown key(s): {', '.join(unknown)}"
            )
        default = DEFAULT_PHASES[phase]
        phases[phase] = {
            "allowed_roles": _list_of_roles(
                supplied.get("allowed_roles", default["allowed_roles"]),
                f"role_governance.phases.{phase}.allowed_roles",
                allow_empty=False,
                valid_roles=LIFECYCLE_ROLES,
            ),
            "requires_handoff_from": _list_of_roles(
                supplied.get(
                    "requires_handoff_from", default["requires_handoff_from"]
                ),
                f"role_governance.phases.{phase}.requires_handoff_from",
                allow_empty=True,
                valid_roles=LIFECYCLE_ROLES,
            ),
        }
    if phases != DEFAULT_PHASES:
        raise RoleConfigError(
            "role_governance.phases must preserve canonical lifecycle ownership: "
            "pre_code=designer, implementation=implementer, post_run=reviewer"
        )
    for phase, prior in (("implementation", "pre_code"), ("post_run", "implementation")):
        invalid = set(phases[phase]["requires_handoff_from"]) - set(
            phases[prior]["allowed_roles"]
        )
        if invalid:
            raise RoleConfigError(
                f"{phase}.requires_handoff_from must name an allowed {prior} role: "
                + ", ".join(sorted(invalid))
            )

    patterns = config.get("guarded_path_regex") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list):
        raise RoleConfigError("guarded_path_regex must be a string or list")
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise RoleConfigError("guarded_path_regex entries must be strings")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RoleConfigError(f"invalid guarded_path_regex {pattern!r}: {exc}") from exc

    return {
        "mode": mode,
        "memory_mode": memory_mode,
        "evidence_dir": evidence_path.as_posix(),
        "session_id_required": session_required,
        "require_distinct_sessions": distinct_sessions,
        "human_acceptance_artifacts": human_artifacts,
        "phases": phases,
    }


def governance_mode_hint(config: dict[str, Any] | None) -> str:
    """Read only the declared mode for malformed-config advisory handling."""

    raw = (config or {}).get("role_governance") if isinstance(config, dict) else None
    if raw is None:
        return "off"
    if isinstance(raw, dict) and raw.get("mode") in GOVERNANCE_MODES:
        return str(raw["mode"])
    # A malformed enabled policy cannot safely be treated as off/advisory.
    return "required"


def _base_config(root: Path) -> dict[str, Any]:
    path = root / "bugate.config.yaml"
    if not path.exists():
        return {}
    parsed = parse_nested_yaml(read_text(path))
    return parsed if isinstance(parsed, dict) else {}


def active_profile_path(root: Path, config: dict[str, Any]) -> Path:
    base = _base_config(root)
    selected = (
        os.environ.get("BUGATE_PROFILE", "").strip()
        or base.get("profile")
        or base.get("active_profile")
    )
    if selected:
        path = Path(str(selected))
        path = path if path.is_absolute() else root / path
    else:
        path = root / "bugate.config.yaml"
    if (not path.exists() or not path.is_file()) and governance_policy(config)["mode"] == "off":
        # Legacy/off profiles remain a complete no-op even when a stale optional
        # profile selector points at a file that is no longer present.
        return (root / "bugate.config.yaml").resolve()
    if not path.exists() or not path.is_file():
        raise RoleConfigError(f"active BUGate profile does not exist: {path}")
    if not _within(path, root):
        raise RoleConfigError("active BUGate profile must be inside the governed workspace")
    return path.resolve()


def _template_uc(root: Path, artifact_dir: Path, template: str) -> str | None:
    rel = workspace_rel(artifact_dir, root)
    marker = "{uc}"
    if marker not in template:
        return None
    before, after = template.split(marker, 1)
    pattern = "^" + re.escape(before.strip("/"))
    if before.strip("/"):
        pattern += "/"
    pattern += r"(?P<uc>[^/]+)" + re.escape(after.rstrip("/")) + "/?$"
    match = re.match(pattern, rel)
    return match.group("uc") if match else None


def resolve_uc(root: Path, artifact_dir: Path, config: dict[str, Any]) -> str:
    root = _canonical_existing_path(root)
    artifact_dir = _canonical_existing_path(artifact_dir)
    configured = config.get("uc") or config.get("use_case_id")
    template = config.get("artifact_dir_template")
    parsed = _template_uc(root, artifact_dir, str(template)) if template else None
    if parsed:
        if configured and str(configured) != parsed:
            raise RoleConfigError(
                f"profile UC {configured!r} disagrees with artifact_dir_template UC {parsed!r}"
            )
        return parsed
    configured_dir = config.get("artifact_dir") or config.get("artifact_root")
    if configured_dir:
        path = Path(str(configured_dir))
        path = path if path.is_absolute() else root / path
        if _canonical_existing_path(path) != artifact_dir:
            raise RoleConfigError(
                f"artifact dir {workspace_rel(artifact_dir, root)} does not match active profile "
                f"artifact_dir {workspace_rel(path, root)}"
            )
    if configured:
        return str(configured)
    # The exact directory token is an auditable parse, not an inferred product ID.
    if not artifact_dir.name:
        raise RoleConfigError("cannot parse UC from artifact directory")
    return artifact_dir.name


def load_context(
    artifact_dir: str | Path,
    *,
    root: Path | None = None,
    config: dict[str, Any] | None = None,
    profile: str | None = None,
) -> GovernanceContext:
    artifact = Path(artifact_dir)
    if root is None:
        root = find_root(artifact.resolve() if artifact.exists() else Path.cwd())
    root = _canonical_existing_path(root)
    artifact = artifact if artifact.is_absolute() else root / artifact
    artifact = _canonical_existing_path(artifact)
    if not _within(artifact, root):
        raise RoleConfigError("artifact_dir must be inside the governed workspace")
    try:
        cfg = (
            config
            if config is not None
            else load_config(root, profile or os.environ.get("BUGATE_PROFILE"))
        )
    except (OSError, ValueError) as exc:
        raise RoleConfigError(f"invalid BUGate role-governance configuration: {exc}") from exc
    if not isinstance(cfg, dict):
        raise RoleConfigError("BUGate configuration must be a mapping")
    policy = governance_policy(cfg)
    profile_path = active_profile_path(root, cfg)
    uc = resolve_uc(root, artifact, cfg)
    return GovernanceContext(root, artifact, cfg, policy, profile_path, uc)


def _snapshot(path: Path, ctx: GovernanceContext, *, with_gate: bool = False) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise RoleGovernanceError(f"required evidence file is missing: {workspace_rel(path, ctx.root)}")
    item: dict[str, Any] = {
        "path": workspace_rel(path, ctx.root),
        "sha256": sha256_file(path),
    }
    if with_gate:
        item["gate_status"] = gate_status(path)
    return item


def profile_snapshot(ctx: GovernanceContext) -> dict[str, str]:
    snap = _snapshot(ctx.profile_path, ctx)
    return {
        "path": snap["path"],
        "sha256": snap["sha256"],
        "effective_config_sha256": sha256_bytes(canonical_json(ctx.config)),
    }


def _precode_snapshot(ctx: GovernanceContext) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in required_precode_artifacts(ctx.config):
        path = ctx.artifact_dir / str(name)
        item = _snapshot(path, ctx, with_gate=True)
        if item["gate_status"] != "passed":
            raise RoleGovernanceError(
                f"{item['path']} gate_status must be passed, got {item['gate_status'] or '<missing>'}"
            )
        items.append(item)
        seen.add(item["path"])
    multiview = ctx.artifact_dir / "00_multiview"
    if multiview.exists():
        for path in sorted(p for p in multiview.rglob("*") if p.is_file()):
            item = _snapshot(path, ctx, with_gate=path.name.endswith((".md", ".yaml", ".yml")))
            if item["path"] not in seen:
                items.append(item)
                seen.add(item["path"])
    return sorted(items, key=lambda item: item["path"])


def _parse_dispatch(path: Path) -> str:
    if not path.exists():
        return ""
    match = re.search(r"(?m)^dispatch_mode:\s*([^\s#]+)", read_text(path))
    return match.group(1).strip() if match else ""


def dispatch_snapshot(ctx: GovernanceContext) -> dict[str, str]:
    multi_path = ctx.artifact_dir / "00_multiview" / "divergence_report.md"
    multi = _parse_dispatch(multi_path)
    if not multi:
        multi = "not_required" if not as_bool(ctx.config.get("require_multiview")) else ""
    adversarial_name = "03b_adversarial_cases.yaml"
    adversarial = _parse_dispatch(ctx.artifact_dir / adversarial_name)
    if not adversarial:
        adversarial = (
            "not_required"
            if adversarial_name not in required_precode_artifacts(ctx.config)
            else ""
        )
    for label, value in (("multiview", multi), ("adversarial", adversarial)):
        if value not in DISPATCH_MODES:
            raise RoleGovernanceError(
                f"{label} dispatch provenance is missing or invalid: {value or '<missing>'}"
            )
    return {"multiview": multi, "adversarial": adversarial}


def verify_precode_semantics(ctx: GovernanceContext) -> None:
    """Re-run the shipped semantic chain without regenerating any artifact."""

    checker = Path(__file__).resolve().parent / "check_bugate_v13_semantics.py"
    if not checker.exists():
        raise RoleGovernanceError(f"pre-code semantic checker is missing: {checker}")
    command = [
        sys.executable,
        str(checker),
        str(ctx.artifact_dir),
        "--scope",
        "pre-code",
        "--require-passed",
        "--profile",
        str(ctx.profile_path),
    ]
    if as_bool(ctx.config.get("require_multiview")):
        command.append("--require-multiview")
    env = os.environ.copy()
    env["BUGATE_PROJECT_ROOT"] = str(ctx.root)
    env["BUGATE_PROFILE"] = str(ctx.profile_path)
    result = subprocess.run(
        command,
        cwd=ctx.root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        # Semantic output contains artifact facts only; cap it to keep hook/CLI
        # diagnostics readable without hiding the first concrete failure.
        if len(detail) > 8000:
            detail = detail[:8000] + "\n... (truncated)"
        raise RoleGovernanceError(
            "pre-code semantic verification failed before designer handoff"
            + (f":\n{detail}" if detail else "")
        )


def _chain_path(ctx: GovernanceContext) -> Path:
    return ctx.evidence_dir / "chain.json"


def _receipt_dir(ctx: GovernanceContext) -> Path:
    return ctx.evidence_dir / "receipts"


def _local_evidence_structure_error(ctx: GovernanceContext) -> str:
    """Reject local evidence aliases before any JSON read follows them."""

    artifact = ctx.artifact_dir
    evidence = ctx.evidence_dir
    try:
        relative = evidence.relative_to(artifact)
    except ValueError:
        return "role evidence path is outside the governed artifact directory"
    if not relative.parts:
        return "role evidence path must be below the governed artifact directory"

    artifact_real = artifact.resolve()

    def containment_error(path: Path, label: str) -> str:
        try:
            Path(os.path.realpath(path)).relative_to(artifact_real)
        except (OSError, ValueError):
            return f"role evidence {label} realpath escapes the artifact directory"
        return ""

    current = artifact
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return ""
        except OSError as exc:
            return f"cannot lstat role evidence directory: {exc}"
        if stat.S_ISLNK(info.st_mode):
            return "role evidence directory must not be a symlink"
        if not stat.S_ISDIR(info.st_mode):
            return "role evidence path must be a directory"
        escaped = containment_error(current, "directory")
        if escaped:
            return escaped

    chain_path = _chain_path(ctx)
    try:
        chain_info = chain_path.lstat()
    except FileNotFoundError:
        chain_info = None
    except OSError as exc:
        return f"cannot lstat role evidence chain: {exc}"
    if chain_info is not None:
        if stat.S_ISLNK(chain_info.st_mode):
            return "role evidence chain.json must not be a symlink"
        if not stat.S_ISREG(chain_info.st_mode):
            return "role evidence chain.json must be a regular file"
        escaped = containment_error(chain_path, "chain.json")
        if escaped:
            return escaped

    receipt_dir = _receipt_dir(ctx)
    try:
        receipt_dir_info = receipt_dir.lstat()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"cannot lstat role receipt directory: {exc}"
    if stat.S_ISLNK(receipt_dir_info.st_mode):
        return "role receipt directory must not be a symlink"
    if not stat.S_ISDIR(receipt_dir_info.st_mode):
        return "role receipt path must be a directory"
    escaped = containment_error(receipt_dir, "receipt directory")
    if escaped:
        return escaped
    try:
        with os.scandir(receipt_dir) as entries:
            for entry in entries:
                if not entry.name.endswith(".json"):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    return f"role receipt {entry.name} must be a regular non-symlink file"
                escaped = containment_error(Path(entry.path), f"receipt {entry.name}")
                if escaped:
                    return escaped
    except OSError as exc:
        return f"cannot inspect role receipt directory: {exc}"
    return ""


def _required_checkpoint_local_error(
    ctx: GovernanceContext,
    record: lineage_registry.LineageRecord,
    checkpoint_payloads: tuple[bytes, ...],
    receipt_paths: list[Path],
) -> str:
    """Compare every local receipt and current chain to one DB snapshot."""

    chain_path = _chain_path(ctx)
    if record.sequence == 0:
        try:
            info = chain_path.lstat()
            body = chain_path.read_bytes()
        except OSError as exc:
            return f"required sequence-zero chain cannot be verified exactly: {exc}"
        if body != _json_bytes(_empty_chain()) or stat.S_IMODE(info.st_mode) != 0o600:
            return "required sequence-zero chain bytes/mode diverge from initialization"
        return ""
    if len(checkpoint_payloads) != record.sequence:
        return "required checkpoint history is absent or incomplete in registry snapshot"
    if len(receipt_paths) != record.sequence:
        return "required local receipt history is incomplete"

    checkpoints: list[dict[str, Any]] = []
    for expected_sequence, payload in enumerate(checkpoint_payloads, 1):
        try:
            checkpoint = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return f"required checkpoint[{expected_sequence}] payload is invalid: {exc}"
        if not isinstance(checkpoint, dict) or checkpoint.get("sequence") != expected_sequence:
            return f"required checkpoint[{expected_sequence}] payload is not exact"
        checkpoints.append(checkpoint)

    comparisons: list[tuple[str, dict[str, Any], Path, str]] = []
    for expected_sequence, (checkpoint, receipt_path) in enumerate(
        zip(checkpoints, receipt_paths), 1
    ):
        envelope = checkpoint.get("receipt_envelope")
        if not isinstance(envelope, dict):
            return f"required checkpoint[{expected_sequence}] receipt envelope is absent"
        comparisons.append(
            (
                f"receipt[{expected_sequence}]",
                envelope,
                receipt_path,
                workspace_rel(receipt_path, ctx.root),
            )
        )
    chain_envelope = checkpoints[-1].get("chain_envelope")
    if not isinstance(chain_envelope, dict):
        return "required current checkpoint chain envelope is absent"
    comparisons.append(
        ("chain", chain_envelope, chain_path, workspace_rel(chain_path, ctx.root))
    )

    for label, envelope, actual_path, expected_path in comparisons:
        if envelope.get("path") != expected_path:
            return f"local {label} path diverges from retained checkpoint"
        encoded = envelope.get("bytes_base64")
        mode = envelope.get("mode")
        if not isinstance(encoded, str) or isinstance(mode, bool) or not isinstance(mode, int):
            return f"required checkpoint {label} envelope is malformed"
        try:
            expected_body = base64.b64decode(encoded.encode("ascii"), validate=True)
            info = actual_path.lstat()
            actual_body = actual_path.read_bytes()
        except (OSError, UnicodeEncodeError, ValueError, binascii.Error) as exc:
            return f"local {label} cannot be compared to retained checkpoint: {exc}"
        if actual_body != expected_body or stat.S_IMODE(info.st_mode) != mode:
            return f"local {label} bytes/mode diverge from retained checkpoint"
    return ""


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        # The durable SQLite journal still makes an interrupted publication
        # detectable on filesystems that do not implement directory fsync.
        pass


def _atomic_bytes(
    path: Path,
    body: bytes,
    *,
    replace: bool,
    mode: int = 0o600,
) -> None:
    """Publish bytes atomically; append-only publication never replaces a peer.

    The old existence-check followed by ``os.replace`` had a TOCTOU window in
    which two writers could both replace the same receipt.  A same-directory
    hard link gives POSIX no-replace semantics: exactly one link can win.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        if path.read_bytes() == body:
            return
        raise RoleGovernanceError(f"append-only role receipt already exists: {path}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(tmp, path)
        else:
            try:
                os.link(tmp, path, follow_symlinks=False)
            except FileExistsError:
                if path.is_file() and path.read_bytes() == body:
                    return
                raise RoleGovernanceError(
                    f"append-only role receipt already exists: {path}"
                )
            finally:
                if tmp.exists():
                    tmp.unlink()
        _fsync_directory(path.parent)
    finally:
        if tmp.exists():
            tmp.unlink()


def _atomic_json(path: Path, data: dict[str, Any], *, replace: bool) -> None:
    _atomic_bytes(path, _json_bytes(data), replace=replace)


def _empty_chain() -> dict[str, Any]:
    return {
        "schema": CHAIN_SCHEMA,
        "state": INITIAL_STATE,
        "sequence": 0,
        "head_sha256": "",
        "latest_receipts": {},
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleGovernanceError(f"invalid JSON evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RoleGovernanceError(f"JSON evidence must be an object: {path}")
    return value


def load_chain(
    ctx: GovernanceContext,
    *,
    allow_uninitialized: bool = False,
) -> dict[str, Any]:
    path = _chain_path(ctx)
    if not path.exists():
        if _receipt_dir(ctx).exists() and any(_receipt_dir(ctx).glob("*.json")):
            raise RoleGovernanceError("role receipts exist without chain.json")
        if allow_uninitialized:
            return _empty_chain()
        raise RoleGovernanceError("role evidence chain.json is missing")
    return _read_json(path)


_TRANSITION_OPTIONAL_FIELDS = (
    "approved_by",
    "decision",
    "handoff_receipt_sha256",
    "handoff_memory_id",
    "accepted_handoff_receipt_sha256",
    "implementation_files",
    "run",
    "lineage",
    "recovery",
)


def _transition_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    transition = {
        "schema": TRANSITION_SCHEMA,
        "event": receipt.get("event"),
        "uc": receipt.get("uc"),
        "artifact_dir": receipt.get("artifact_dir"),
        "phase": receipt.get("phase"),
        "from_role": receipt.get("from_role"),
        "to_role": receipt.get("to_role"),
        "actor": receipt.get("actor"),
        "profile": receipt.get("profile"),
        "artifacts": receipt.get("artifacts"),
        "dispatch": receipt.get("dispatch"),
        "human_acceptance": receipt.get("human_acceptance"),
        "previous_receipt_sha256": receipt.get("previous_receipt_sha256"),
        "idempotency_sha256": receipt.get("idempotency_sha256"),
    }
    for key in _TRANSITION_OPTIONAL_FIELDS:
        if key in receipt:
            transition[key] = receipt[key]
    return transition


def _validate_lineage_precondition(value: Any, path: Path) -> None:
    if not isinstance(value, dict):
        raise RoleGovernanceError(f"invalid lineage precondition in {path.name}")
    required = {
        "schema",
        "lineage_id",
        "expected_head_sha256",
        "expected_sequence",
        "expected_revision",
        "previous_checkpoint_memory_id",
        "receipt_created_at",
    }
    if set(value) != required or value.get("schema") != LINEAGE_PRECONDITION_SCHEMA:
        raise RoleGovernanceError(f"invalid lineage precondition schema in {path.name}")
    lineage_id = str(value.get("lineage_id") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", lineage_id):
        raise RoleGovernanceError(f"invalid lineage ID in {path.name}")
    expected_head = str(value.get("expected_head_sha256") or "")
    if expected_head and not re.fullmatch(r"[0-9a-f]{64}", expected_head):
        raise RoleGovernanceError(f"invalid expected lineage head in {path.name}")
    if (
        isinstance(value.get("expected_sequence"), bool)
        or not isinstance(value.get("expected_sequence"), int)
        or value["expected_sequence"] < 0
        or isinstance(value.get("expected_revision"), bool)
        or not isinstance(value.get("expected_revision"), int)
        or value["expected_revision"] < 0
    ):
        raise RoleGovernanceError(f"invalid lineage sequence/revision in {path.name}")
    checkpoint = str(value.get("previous_checkpoint_memory_id") or "")
    if checkpoint and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", checkpoint):
        raise RoleGovernanceError(f"invalid previous checkpoint ID in {path.name}")
    if not isinstance(value.get("receipt_created_at"), str) or not value["receipt_created_at"]:
        raise RoleGovernanceError(f"invalid lineage receipt timestamp in {path.name}")


def _required_receipt_hash(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RoleGovernanceError(
            f"{field} requires an exact receipt SHA-256 in {path.name}"
        )
    return value


def _validate_snapshot_item(
    value: Any,
    path: Path,
    label: str,
    *,
    gate: bool | None,
) -> str:
    """Validate the immutable shape emitted by ``_snapshot``."""

    allowed_shapes = (
        ({"path", "sha256"}, {"path", "sha256", "gate_status"})
        if gate is None
        else (
            ({"path", "sha256", "gate_status"},)
            if gate
            else ({"path", "sha256"},)
        )
    )
    if not isinstance(value, dict) or set(value) not in allowed_shapes:
        raise RoleGovernanceError(f"{label} snapshot schema is invalid in {path.name}")
    item_path = value.get("path")
    if not isinstance(item_path, str) or not item_path:
        raise RoleGovernanceError(f"{label} snapshot path is invalid in {path.name}")
    relative = Path(item_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != item_path
        or relative.as_posix() in {"", "."}
        or ".." in relative.parts
        or "\\" in item_path
    ):
        raise RoleGovernanceError(f"{label} snapshot path is unsafe in {path.name}")
    digest = value.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RoleGovernanceError(f"{label} snapshot hash is invalid in {path.name}")
    if "gate_status" in value:
        status = value.get("gate_status")
        if (
            not isinstance(status, str)
            or status != status.strip().lower()
        ):
            raise RoleGovernanceError(
                f"{label} snapshot gate_status is invalid in {path.name}"
            )
    return item_path


def _validate_snapshot_list(
    value: Any,
    path: Path,
    label: str,
    *,
    gate: bool | None,
    nonempty: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = " non-empty" if nonempty else ""
        raise RoleGovernanceError(
            f"{label} must be a{qualifier} snapshot list in {path.name}"
        )
    paths = [
        _validate_snapshot_item(item, path, label, gate=gate)
        for item in value
    ]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RoleGovernanceError(
            f"{label} snapshots must have unique path-sorted entries in {path.name}"
        )
    return value


def _expected_artifact_paths(
    ctx: GovernanceContext,
    names: Iterable[str],
) -> list[str]:
    return sorted(
        workspace_rel(ctx.artifact_dir / str(name), ctx.root) for name in names
    )


def _validate_human_acceptance_anchor(value: Any, path: Path) -> None:
    if not isinstance(value, dict) or set(value) != {
        "required",
        "receipt_sha256",
        "artifact_sha256",
    }:
        raise RoleGovernanceError(
            f"human acceptance reference schema is invalid in {path.name}"
        )
    required = value.get("required")
    receipt_hash = value.get("receipt_sha256")
    artifact_hash = value.get("artifact_sha256")
    if not isinstance(required, bool):
        raise RoleGovernanceError(
            f"human acceptance reference required flag is invalid in {path.name}"
        )
    if required:
        _required_receipt_hash(receipt_hash, "human acceptance reference", path)
        _required_receipt_hash(artifact_hash, "human acceptance artifact", path)
    elif receipt_hash != "" or artifact_hash != "":
        raise RoleGovernanceError(
            f"optional human acceptance reference must be empty in {path.name}"
        )


def _validate_event_receipt_contract(
    receipt: dict[str, Any],
    path: Path,
) -> None:
    """Validate event-owned fields without consulting mutable workspace state."""

    event = str(receipt.get("event") or "")
    actor = receipt["actor"]
    canonical_route = {
        "human_acceptance": ("pre_code", "human", "designer", "designer"),
        "designer_handoff": ("pre_code", "designer", "implementer", "designer"),
        "implementer_acceptance": (
            "implementation",
            "designer",
            "implementer",
            "implementer",
        ),
        "implementer_handoff": (
            "implementation",
            "implementer",
            "reviewer",
            "implementer",
        ),
        "reviewer_acceptance": (
            "post_run",
            "implementer",
            "reviewer",
            "reviewer",
        ),
        "reviewer_completion": ("post_run", "reviewer", "", "reviewer"),
    }
    if event in canonical_route:
        phase, from_role, to_role, actor_role = canonical_route[event]
        if (
            receipt.get("phase") != phase
            or receipt.get("from_role") != from_role
            or receipt.get("to_role") != to_role
            or actor.get("role") != actor_role
        ):
            raise RoleGovernanceError(
                f"{event} lifecycle route is invalid in {path.name}"
            )

    if not isinstance(receipt.get("dispatch"), dict):
        raise RoleGovernanceError(f"receipt dispatch must be an object in {path.name}")
    if not isinstance(receipt.get("human_acceptance"), dict):
        raise RoleGovernanceError(
            f"receipt human_acceptance must be an object in {path.name}"
        )

    if event == "human_acceptance":
        approved_by = receipt.get("approved_by")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise RoleGovernanceError(
                f"human_acceptance requires approved_by in {path.name}"
            )
        if receipt.get("decision") != "accepted":
            raise RoleGovernanceError(
                f"human_acceptance requires decision=accepted in {path.name}"
            )
        if receipt.get("human_acceptance") != {"required": True}:
            raise RoleGovernanceError(
                f"human_acceptance event metadata is invalid in {path.name}"
            )
        if receipt.get("dispatch") != {}:
            raise RoleGovernanceError(
                f"human_acceptance dispatch must be empty in {path.name}"
            )
    elif event == "designer_handoff":
        _validate_human_acceptance_anchor(receipt.get("human_acceptance"), path)
    elif event in {"implementer_acceptance", "reviewer_acceptance"}:
        _required_receipt_hash(
            receipt.get("handoff_receipt_sha256"),
            f"{event} handoff reference",
            path,
        )
        if not isinstance(receipt.get("handoff_memory_id"), str):
            raise RoleGovernanceError(
                f"{event} requires a handoff Memory reference in {path.name}"
            )
        _validate_human_acceptance_anchor(receipt.get("human_acceptance"), path)
    elif event == "implementer_handoff":
        _required_receipt_hash(
            receipt.get("accepted_handoff_receipt_sha256"),
            "implementer_handoff accepted-handoff reference",
            path,
        )
        _validate_snapshot_list(
            receipt.get("implementation_files"),
            path,
            "implementer_handoff implementation",
            gate=False,
            nonempty=True,
        )
        _validate_human_acceptance_anchor(receipt.get("human_acceptance"), path)
    elif event == "reviewer_completion":
        run = receipt.get("run")
        if not isinstance(run, dict) or set(run) != {
            "command_summary",
            "exit_code",
            "evidence",
            "gate_status",
        }:
            raise RoleGovernanceError(
                f"reviewer_completion requires a canonical run record in {path.name}"
            )
        evidence = run.get("evidence")
        if (
            not isinstance(run.get("command_summary"), str)
            or not run["command_summary"].strip()
            or isinstance(run.get("exit_code"), bool)
            or not isinstance(run.get("exit_code"), int)
            or run.get("gate_status") not in {"passed", "failed"}
        ):
            raise RoleGovernanceError(
                f"reviewer_completion run record values are invalid in {path.name}"
            )
        _validate_snapshot_list(
            evidence,
            path,
            "reviewer_completion run evidence",
            gate=False,
            nonempty=True,
        )
        if receipt.get("dispatch") != {} or receipt.get("human_acceptance") != {}:
            raise RoleGovernanceError(
                f"reviewer_completion provenance fields must be empty in {path.name}"
            )
        expected_state = (
            "closed" if run["gate_status"] == "passed" else "post_run_active"
        )
        if receipt.get("resulting_state") != expected_state or (
            run["gate_status"] == "passed" and run["exit_code"] != 0
        ):
            raise RoleGovernanceError(
                f"reviewer_completion run/result lifecycle is invalid in {path.name}"
            )
    elif event == RECOVERY_EVENT:
        if (
            receipt.get("from_role") != "agent"
            or receipt.get("to_role") != ""
            or actor.get("role") != "agent"
        ):
            raise RoleGovernanceError(
                f"evidence_recovery lifecycle route is invalid in {path.name}"
            )
        if (
            receipt.get("artifacts") != []
            or receipt.get("dispatch") != {}
            or receipt.get("human_acceptance") != {}
        ):
            raise RoleGovernanceError(
                f"evidence_recovery provenance fields must be empty in {path.name}"
            )


def _validate_receipt_contract(receipt: dict[str, Any], path: Path) -> None:
    required = {
        "schema",
        "event",
        "sequence",
        "uc",
        "artifact_dir",
        "phase",
        "from_role",
        "to_role",
        "actor",
        "created_at",
        "profile",
        "artifacts",
        "dispatch",
        "human_acceptance",
        "previous_receipt_sha256",
        "transition_sha256",
        "idempotency_sha256",
        "memory",
        "resulting_state",
        "receipt_sha256",
    }
    missing = sorted(required - set(receipt))
    if missing:
        raise RoleGovernanceError(
            f"receipt schema fields missing in {path.name}: {', '.join(missing)}"
        )
    sequence = receipt.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise RoleGovernanceError(f"invalid receipt sequence in {path.name}")
    previous_hash = receipt.get("previous_receipt_sha256")
    if not isinstance(previous_hash, str) or (
        previous_hash and not re.fullmatch(r"[0-9a-f]{64}", previous_hash)
    ):
        raise RoleGovernanceError(f"invalid previous receipt hash in {path.name}")
    supplied_hash = receipt.get("receipt_sha256")
    if not isinstance(supplied_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", supplied_hash
    ):
        raise RoleGovernanceError(f"invalid receipt hash in {path.name}")
    event = receipt.get("event")
    if event not in EVENT_STATES and event != RECOVERY_EVENT:
        raise RoleGovernanceError(f"unknown role receipt event in {path.name}: {event!r}")
    if receipt.get("phase") not in PHASES:
        raise RoleGovernanceError(f"invalid receipt phase in {path.name}")
    actor = receipt.get("actor")
    if not isinstance(actor, dict) or set(actor) != {"role", "runtime", "session_id"}:
        raise RoleGovernanceError(f"invalid receipt actor schema in {path.name}")
    allowed_actor_roles = BUGATE_ROLES if event == RECOVERY_EVENT else LIFECYCLE_ROLES
    if actor.get("role") not in allowed_actor_roles:
        raise RoleGovernanceError(f"invalid lifecycle actor in {path.name}")
    runtime = actor.get("runtime")
    session_id = actor.get("session_id")
    if not isinstance(runtime, str) or runtime not in {"codex", "claude", "unknown"}:
        raise RoleGovernanceError(f"invalid lifecycle actor runtime in {path.name}")
    if not isinstance(session_id, str):
        raise RoleGovernanceError(f"invalid lifecycle actor session in {path.name}")
    profile = receipt.get("profile")
    valid_profile_fields = (
        {"path", "sha256"},
        {"path", "sha256", "effective_config_sha256"},
    )
    if not isinstance(profile, dict) or set(profile) not in valid_profile_fields:
        raise RoleGovernanceError(f"invalid receipt profile schema in {path.name}")
    if not all(isinstance(value, str) and value for value in profile.values()):
        raise RoleGovernanceError(f"invalid receipt profile values in {path.name}")
    profile_path = profile.get("path")
    profile_digest = profile.get("sha256")
    if (
        not isinstance(profile_path, str)
        or Path(profile_path).is_absolute()
        or Path(profile_path).as_posix() != profile_path
        or profile_path in {"", "."}
        or ".." in Path(profile_path).parts
        or "\\" in profile_path
        or not isinstance(profile_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", profile_digest) is None
        or (
            "effective_config_sha256" in profile
            and re.fullmatch(
                r"[0-9a-f]{64}", profile["effective_config_sha256"]
            )
            is None
        )
    ):
        raise RoleGovernanceError(f"invalid receipt profile snapshot in {path.name}")
    artifacts = receipt.get("artifacts")
    _validate_snapshot_list(
        artifacts,
        path,
        "receipt artifact",
        gate=None,
    )
    if "lineage" in receipt:
        _validate_lineage_precondition(receipt["lineage"], path)
        lineage_value = receipt["lineage"]
        if (
            lineage_value["expected_sequence"] != sequence - 1
            or lineage_value["expected_head_sha256"] != previous_hash
            or lineage_value["receipt_created_at"] != receipt.get("created_at")
        ):
            raise RoleGovernanceError(
                f"receipt lineage predecessor does not match receipt in {path.name}"
            )
    memory_anchor = receipt.get("memory")
    if not isinstance(memory_anchor, dict):
        raise RoleGovernanceError(f"invalid receipt Memory anchor in {path.name}")
    if event == RECOVERY_EVENT:
        recovery = receipt.get("recovery")
        if not isinstance(recovery, dict) or set(recovery) != {
            "source",
            "recovered_head_sha256",
            "recovered_sequence",
            "preserved_lifecycle_state",
        }:
            raise RoleGovernanceError(f"invalid recovery receipt schema in {path.name}")
        recovered_sequence = recovery.get("recovered_sequence")
        recovered_head = recovery.get("recovered_head_sha256")
        if (
            not isinstance(recovery.get("source"), str)
            or not recovery["source"]
            or isinstance(recovered_sequence, bool)
            or not isinstance(recovered_sequence, int)
            or recovered_sequence < 0
            or not isinstance(recovered_head, str)
            or (recovered_sequence == 0 and recovered_head != "")
            or (
                recovered_sequence > 0
                and re.fullmatch(r"[0-9a-f]{64}", recovered_head) is None
            )
            or not isinstance(recovery.get("preserved_lifecycle_state"), str)
            or not recovery["preserved_lifecycle_state"]
        ):
            raise RoleGovernanceError(f"invalid recovery receipt values in {path.name}")
        if receipt.get("resulting_state") != recovery.get("preserved_lifecycle_state"):
            raise RoleGovernanceError(
                f"recovery receipt changed lifecycle state in {path.name}"
            )
    _validate_event_receipt_contract(receipt, path)
    transition = _transition_from_receipt(receipt)
    transition_hash = sha256_bytes(canonical_json(transition))
    if receipt.get("transition_sha256") != transition_hash:
        raise RoleGovernanceError(f"transition hash mismatch: {path.name}")
    state = receipt.get("resulting_state")
    if event == RECOVERY_EVENT:
        allowed_states = {str((receipt.get("recovery") or {}).get("preserved_lifecycle_state") or "")}
    else:
        allowed_states = {EVENT_STATES[event]}
        if event == "reviewer_completion":
            allowed_states.add("post_run_active")
    if state not in allowed_states:
        raise RoleGovernanceError(f"invalid resulting state for {event}: {state!r}")


def _exact_prior_event_receipt(
    prior_by_hash: dict[str, dict[str, Any]],
    reference: Any,
    *,
    event: str,
    label: str,
    path: Path,
) -> dict[str, Any]:
    receipt = prior_by_hash.get(str(reference or ""))
    if receipt is None:
        raise RoleGovernanceError(
            f"{label} must reference an earlier exact receipt in {path.name}"
        )
    if receipt.get("event") != event:
        raise RoleGovernanceError(
            f"{label} references {receipt.get('event')!r}, not {event!r}, in {path.name}"
        )
    return receipt


def _human_artifact_hash(receipt: dict[str, Any], path: Path) -> str:
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RoleGovernanceError(
            f"human acceptance reference has no artifact snapshot in {path.name}"
        )
    if len(artifacts) == 1:
        value = artifacts[0]
        digest = value.get("sha256") if isinstance(value, dict) else None
        return _required_receipt_hash(digest, "human acceptance artifact", path)
    return sha256_bytes(canonical_json(artifacts))


def _verify_profile_snapshot_path(
    ctx: GovernanceContext,
    receipt: dict[str, Any],
    path: Path,
) -> None:
    expected = workspace_rel(ctx.profile_path, ctx.root)
    if receipt.get("profile", {}).get("path") != expected:
        raise RoleGovernanceError(
            f"receipt profile path does not match the active profile in {path.name}"
        )


def _verify_human_acceptance_inventory(
    ctx: GovernanceContext,
    receipt: dict[str, Any],
    path: Path,
) -> None:
    expected = _expected_artifact_paths(
        ctx, ctx.policy["human_acceptance_artifacts"]
    )
    artifacts = receipt["artifacts"]
    actual = [item["path"] for item in artifacts]
    if actual != expected or any(
        item.get("gate_status") != "passed" for item in artifacts
    ):
        raise RoleGovernanceError(
            f"human_acceptance artifact inventory must exactly match passed profile provenance in {path.name}"
        )
    _verify_profile_snapshot_path(ctx, receipt, path)


def _verify_designer_handoff_inventory(
    ctx: GovernanceContext,
    receipt: dict[str, Any],
    path: Path,
) -> None:
    artifacts = receipt["artifacts"]
    by_path = {item["path"]: item for item in artifacts}
    required_paths = _expected_artifact_paths(
        ctx, required_precode_artifacts(ctx.config)
    )
    if as_bool(ctx.config.get("require_multiview")):
        required_paths.append(
            workspace_rel(
                ctx.artifact_dir / "00_multiview" / "divergence_report.md",
                ctx.root,
            )
        )
        required_paths.sort()
    missing = [name for name in required_paths if name not in by_path]
    not_passed = [
        name
        for name in required_paths
        if name in by_path and by_path[name].get("gate_status") != "passed"
    ]
    if missing or not_passed:
        detail = ", ".join(missing + not_passed)
        raise RoleGovernanceError(
            f"designer_handoff requires every configured passed pre-code artifact snapshot in {path.name}: {detail}"
        )

    dispatch = receipt.get("dispatch")
    if (
        not isinstance(dispatch, dict)
        or set(dispatch) != {"multiview", "adversarial"}
        or any(value not in DISPATCH_MODES for value in dispatch.values())
        or (
            as_bool(ctx.config.get("require_multiview"))
            and dispatch.get("multiview") == "not_required"
        )
        or (
            "03b_adversarial_cases.yaml"
            in required_precode_artifacts(ctx.config)
            and dispatch.get("adversarial") == "not_required"
        )
    ):
        raise RoleGovernanceError(
            f"designer_handoff dispatch provenance conflicts with the active profile in {path.name}"
        )
    _verify_profile_snapshot_path(ctx, receipt, path)


def _verify_designer_human_reference(
    ctx: GovernanceContext,
    handoff: dict[str, Any],
    *,
    prior_by_hash: dict[str, dict[str, Any]],
    latest_by_event: dict[str, dict[str, Any]],
    path: Path,
) -> None:
    anchor = handoff["human_acceptance"]
    configured_required = bool(ctx.policy["human_acceptance_artifacts"])
    if anchor.get("required") != configured_required:
        raise RoleGovernanceError(
            f"designer_handoff human acceptance requirement conflicts with the active profile in {path.name}"
        )
    if not configured_required:
        return
    human = _exact_prior_event_receipt(
        prior_by_hash,
        anchor.get("receipt_sha256"),
        event="human_acceptance",
        label="designer_handoff human acceptance reference",
        path=path,
    )
    latest_human = latest_by_event.get("human_acceptance")
    if latest_human is None or latest_human.get("receipt_sha256") != human.get(
        "receipt_sha256"
    ):
        raise RoleGovernanceError(
            f"designer_handoff must reference the latest prior human acceptance in {path.name}"
        )
    if anchor.get("artifact_sha256") != _human_artifact_hash(human, path):
        raise RoleGovernanceError(
            f"designer_handoff human artifact reference diverges in {path.name}"
        )
    _verify_human_acceptance_inventory(ctx, human, path)
    if handoff.get("profile") != human.get("profile"):
        raise RoleGovernanceError(
            f"designer_handoff profile diverges from human acceptance in {path.name}"
        )


def _verify_reviewer_completion_provenance(
    ctx: GovernanceContext,
    receipt: dict[str, Any],
    acceptance: dict[str, Any],
    path: Path,
) -> None:
    if receipt.get("actor") != acceptance.get("actor"):
        raise RoleGovernanceError(
            f"reviewer_completion actor/session diverges from reviewer acceptance in {path.name}"
        )
    if receipt.get("profile") != acceptance.get("profile"):
        raise RoleGovernanceError(
            f"reviewer_completion profile diverges from reviewer acceptance in {path.name}"
        )
    evidence = receipt["run"]["evidence"]
    artifacts = receipt["artifacts"]
    by_path = {item["path"]: item for item in artifacts}
    report_paths = _expected_artifact_paths(ctx, sorted(POSTRUN_NAMES))
    if any(name not in by_path for name in report_paths):
        raise RoleGovernanceError(
            f"reviewer_completion is missing required 04/05 report snapshots in {path.name}"
        )
    reports = [by_path[name] for name in report_paths]
    if any(set(item) != {"path", "sha256", "gate_status"} for item in reports):
        raise RoleGovernanceError(
            f"reviewer_completion 04/05 report snapshot schema is invalid in {path.name}"
        )
    expected_artifacts = sorted(reports + evidence, key=lambda item: item["path"])
    if artifacts != expected_artifacts:
        raise RoleGovernanceError(
            f"reviewer_completion artifacts must exactly bind 04/05 reports and run evidence in {path.name}"
        )
    if receipt["run"]["gate_status"] == "passed" and any(
        item.get("gate_status") != "passed" for item in reports
    ):
        raise RoleGovernanceError(
            f"closed reviewer_completion requires passed 04/05 reports in {path.name}"
        )


def _require_exact_acceptance_actor(
    acceptance: dict[str, Any],
    actor: dict[str, str],
    *,
    transition: str,
) -> None:
    """Reject role/session/runtime drift before a successor is journaled."""

    if acceptance.get("actor") != actor:
        raise RoleGovernanceError(
            f"{transition} actor role/session/runtime must exactly match its acceptance"
        )


def _verify_acceptance_reference(
    ctx: GovernanceContext,
    receipt: dict[str, Any],
    *,
    expected_event: str,
    prior_by_hash: dict[str, dict[str, Any]],
    latest_by_event: dict[str, dict[str, Any]],
    path: Path,
) -> dict[str, Any]:
    event = str(receipt["event"])
    handoff = _exact_prior_event_receipt(
        prior_by_hash,
        receipt.get("handoff_receipt_sha256"),
        event=expected_event,
        label=f"{event} handoff reference",
        path=path,
    )
    latest_handoff = latest_by_event.get(expected_event)
    if latest_handoff is None or latest_handoff.get("receipt_sha256") != handoff.get(
        "receipt_sha256"
    ):
        raise RoleGovernanceError(
            f"{event} must reference the latest prior {expected_event} in {path.name}"
        )
    for field in ("profile", "artifacts", "dispatch", "human_acceptance"):
        if receipt.get(field) != handoff.get(field):
            raise RoleGovernanceError(
                f"{event} {field} diverges from its exact handoff reference in {path.name}"
            )
    if (
        receipt.get("from_role") != handoff.get("from_role")
        or receipt.get("to_role") != handoff.get("to_role")
    ):
        raise RoleGovernanceError(
            f"{event} role route diverges from its exact handoff reference in {path.name}"
        )
    memory = handoff.get("memory")
    expected_memory_id = (
        str(memory.get("memory_id") or "") if isinstance(memory, dict) else ""
    )
    if receipt.get("handoff_memory_id") != expected_memory_id:
        raise RoleGovernanceError(
            f"{event} Memory ID diverges from its exact handoff reference in {path.name}"
        )
    if ctx.policy["require_distinct_sessions"] and (
        receipt.get("actor", {}).get("session_id")
        == handoff.get("actor", {}).get("session_id")
    ):
        raise RoleGovernanceError(
            f"{event} requires a session distinct from its handoff in {path.name}"
        )
    if expected_event == "designer_handoff":
        _verify_designer_human_reference(
            ctx,
            handoff,
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
            path=path,
        )
    return handoff


def _verify_lifecycle_graph_edge(
    ctx: GovernanceContext,
    receipt: dict[str, Any],
    path: Path,
    *,
    predecessor_state: str,
    predecessor_hash: str,
    prior_by_hash: dict[str, dict[str, Any]],
    latest_by_event: dict[str, dict[str, Any]],
) -> str:
    """Validate one semantic edge over already hash-verified prior receipts."""

    event = str(receipt["event"])
    resulting_state = str(receipt["resulting_state"])
    actor = receipt["actor"]
    actor_session = actor["session_id"]
    if event != RECOVERY_EVENT and (
        actor_session != actor_session.strip()
        or (ctx.policy["session_id_required"] and not actor_session)
    ):
        raise RoleGovernanceError(
            f"{event} requires a canonical non-empty actor session in {path.name}"
        )
    if event == "human_acceptance":
        _verify_human_acceptance_inventory(ctx, receipt, path)
        return resulting_state
    if event == "designer_handoff":
        _verify_designer_handoff_inventory(ctx, receipt, path)
        _verify_designer_human_reference(
            ctx,
            receipt,
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
            path=path,
        )
        return resulting_state
    if event == "implementer_acceptance":
        if predecessor_state != "awaiting_implementer_acceptance":
            raise RoleGovernanceError(
                "implementer_acceptance lifecycle predecessor must be "
                f"awaiting_implementer_acceptance in {path.name}"
            )
        _verify_acceptance_reference(
            ctx,
            receipt,
            expected_event="designer_handoff",
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
            path=path,
        )
        return resulting_state
    if event == "implementer_handoff":
        if predecessor_state not in {
            "implementation_unlocked",
            "awaiting_reviewer_acceptance",
            "post_run_active",
            "closed",
        }:
            raise RoleGovernanceError(
                f"implementer_handoff lifecycle predecessor is invalid in {path.name}"
            )
        designer_handoff = _exact_prior_event_receipt(
            prior_by_hash,
            receipt.get("accepted_handoff_receipt_sha256"),
            event="designer_handoff",
            label="implementer_handoff accepted-handoff reference",
            path=path,
        )
        latest_designer = latest_by_event.get("designer_handoff")
        acceptance = latest_by_event.get("implementer_acceptance")
        if (
            latest_designer is None
            or latest_designer.get("receipt_sha256")
            != designer_handoff.get("receipt_sha256")
            or acceptance is None
            or acceptance.get("handoff_receipt_sha256")
            != designer_handoff.get("receipt_sha256")
        ):
            raise RoleGovernanceError(
                f"implementer_handoff requires the latest accepted designer handoff in {path.name}"
            )
        _verify_designer_human_reference(
            ctx,
            designer_handoff,
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
            path=path,
        )
        if receipt.get("human_acceptance") != designer_handoff.get(
            "human_acceptance"
        ):
            raise RoleGovernanceError(
                f"implementer_handoff human reference diverges in {path.name}"
            )
        if receipt.get("actor") != acceptance.get("actor"):
            raise RoleGovernanceError(
                f"implementer_handoff actor/session diverges from implementer acceptance in {path.name}"
            )
        for field in ("profile", "dispatch"):
            if receipt.get(field) != designer_handoff.get(field):
                raise RoleGovernanceError(
                    f"implementer_handoff {field} diverges from accepted designer handoff in {path.name}"
                )
        expected_artifacts = sorted(
            designer_handoff["artifacts"] + receipt["implementation_files"],
            key=lambda item: item["path"],
        )
        if receipt.get("artifacts") != expected_artifacts:
            raise RoleGovernanceError(
                f"implementer_handoff artifacts diverge from accepted pre-code plus implementation snapshots in {path.name}"
            )
        return resulting_state
    if event == "reviewer_acceptance":
        if predecessor_state != "awaiting_reviewer_acceptance":
            raise RoleGovernanceError(
                "reviewer_acceptance lifecycle predecessor must be "
                f"awaiting_reviewer_acceptance in {path.name}"
            )
        _verify_acceptance_reference(
            ctx,
            receipt,
            expected_event="implementer_handoff",
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
            path=path,
        )
        return resulting_state
    if event == "reviewer_completion":
        acceptance = latest_by_event.get("reviewer_acceptance")
        if predecessor_state != "post_run_active" or acceptance is None:
            raise RoleGovernanceError(
                f"reviewer_completion requires a prior active reviewer acceptance in {path.name}"
            )
        _verify_reviewer_completion_provenance(
            ctx,
            receipt,
            acceptance,
            path,
        )
        return resulting_state
    if event == RECOVERY_EVENT:
        recovery = receipt["recovery"]
        if (
            recovery.get("recovered_head_sha256") != predecessor_hash
            or recovery.get("recovered_sequence") != receipt["sequence"] - 1
            or recovery.get("preserved_lifecycle_state") != predecessor_state
            or resulting_state != predecessor_state
            or receipt.get("phase") != _recovery_phase(predecessor_state)
        ):
            raise RoleGovernanceError(
                f"evidence_recovery predecessor reference/lifecycle diverges in {path.name}"
            )
        return predecessor_state
    raise RoleGovernanceError(f"unsupported lifecycle event in {path.name}: {event}")


def verify_chain(ctx: GovernanceContext) -> list[dict[str, Any]]:
    chain = load_chain(ctx)
    expected_artifact_dir = workspace_rel(ctx.artifact_dir, ctx.root)
    required_keys = {"schema", "state", "sequence", "head_sha256", "latest_receipts"}
    if set(chain) != required_keys:
        raise RoleGovernanceError("chain.json contains missing or non-minimal keys")
    if chain["schema"] != CHAIN_SCHEMA:
        raise RoleGovernanceError("unsupported role chain schema")
    if not isinstance(chain["sequence"], int) or chain["sequence"] < 0:
        raise RoleGovernanceError("chain sequence must be a non-negative integer")
    if not isinstance(chain["latest_receipts"], dict):
        raise RoleGovernanceError("chain latest_receipts must be a mapping")
    paths = sorted(_receipt_dir(ctx).glob("*.json")) if _receipt_dir(ctx).exists() else []
    if len(paths) != chain["sequence"]:
        raise RoleGovernanceError(
            f"receipt count {len(paths)} does not match chain sequence {chain['sequence']}"
        )
    receipts: list[dict[str, Any]] = []
    previous = ""
    latest: dict[str, str] = {}
    prior_by_hash: dict[str, dict[str, Any]] = {}
    latest_by_event: dict[str, dict[str, Any]] = {}
    lifecycle_state = INITIAL_STATE
    for expected, path in enumerate(paths, 1):
        receipt = _read_json(path)
        _validate_receipt_contract(receipt, path)
        if receipt.get("uc") != ctx.uc:
            raise RoleGovernanceError(
                f"receipt UC does not match active context: {path.name}"
            )
        if receipt.get("artifact_dir") != expected_artifact_dir:
            raise RoleGovernanceError(
                f"receipt artifact_dir does not match active context: {path.name}"
            )
        lineage_value = receipt.get("lineage")
        if isinstance(lineage_value, dict):
            expected_lineage = lineage_registry.lineage_id(
                lineage_registry.build_lineage_key(
                    _memory_namespace(ctx),
                    ctx.uc,
                    expected_artifact_dir,
                )
            )
            if lineage_value.get("lineage_id") != expected_lineage:
                raise RoleGovernanceError(
                    f"receipt lineage ID does not match active context: {path.name}"
                )
        if receipt.get("schema") != ROLE_SCHEMA or receipt.get("sequence") != expected:
            raise RoleGovernanceError(f"invalid receipt schema/sequence: {path.name}")
        actual = receipt_sha256(receipt)
        if receipt.get("receipt_sha256") != actual:
            raise RoleGovernanceError(f"receipt hash mismatch: {path.name}")
        if receipt.get("previous_receipt_sha256", "") != previous:
            raise RoleGovernanceError(f"receipt chain link mismatch: {path.name}")
        event = str(receipt.get("event") or "")
        expected_prefix = f"{expected:06d}-{event.replace('_', '-')}-{actual}.json"
        if path.name != expected_prefix:
            raise RoleGovernanceError(f"receipt filename/hash mismatch: {path.name}")
        lifecycle_state = _verify_lifecycle_graph_edge(
            ctx,
            receipt,
            path,
            predecessor_state=lifecycle_state,
            predecessor_hash=previous,
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
        )
        latest[event] = workspace_rel(path, ctx.root)
        previous = actual
        prior_by_hash[actual] = receipt
        latest_by_event[event] = receipt
        receipts.append(receipt)
    if chain["head_sha256"] != previous:
        raise RoleGovernanceError("chain head hash does not match the latest receipt")
    if chain["latest_receipts"] != latest:
        raise RoleGovernanceError("chain latest_receipts does not match receipt history")
    if chain["state"] != lifecycle_state:
        raise RoleGovernanceError("chain state does not match the latest transition")
    return receipts


def _lineage_key(ctx: GovernanceContext) -> lineage_registry.LineageKey:
    return lineage_registry.build_lineage_key(
        _memory_namespace(ctx),
        ctx.uc,
        workspace_rel(ctx.artifact_dir, ctx.root),
    )


def lineage_identity(artifact_dir: str | Path) -> dict[str, Any]:
    """Return the deterministic operator confirmation value without mutation."""

    ctx = load_context(artifact_dir)
    key = _lineage_key(ctx)
    return {"lineage_key": key.as_dict(), "lineage_id": key.lineage_id}


def _lineage_integrity(ctx: GovernanceContext) -> LineageIntegrity:
    key = _lineage_key(ctx)
    chain_path = _chain_path(ctx)
    structure_error = _local_evidence_structure_error(ctx)
    receipt_paths = []
    chain: dict[str, Any] | None = None
    receipts: tuple[dict[str, Any], ...] = ()
    local_error = structure_error
    if not structure_error and _receipt_dir(ctx).exists():
        receipt_paths = sorted(_receipt_dir(ctx).glob("*.json"))
    if not structure_error and chain_path.exists():
        try:
            verified = verify_chain(ctx)
            chain = load_chain(ctx)
            receipts = tuple(verified)
        except RoleGovernanceError as exc:
            local_error = str(exc)
    elif not structure_error and receipt_paths:
        local_error = "role receipts exist without chain.json"

    has_local_history = bool(receipt_paths) or bool(
        chain and isinstance(chain.get("sequence"), int) and chain["sequence"] > 0
    )
    try:
        registry = lineage_registry.LineageRegistry()
    except lineage_registry.RegistryNotFoundError:
        state = "migration_required" if has_local_history else "uninitialized"
        lifecycle = str(chain.get("state") or INITIAL_STATE) if chain else INITIAL_STATE
        return LineageIntegrity(
            state,
            lifecycle,
            key,
            key.lineage_id,
            None,
            None,
            None,
            chain,
            receipts,
            local_error=local_error,
        )
    except lineage_registry.RoleLineageError as exc:
        lifecycle = str(chain.get("state") or INITIAL_STATE) if chain else INITIAL_STATE
        return LineageIntegrity(
            "registry_unavailable",
            lifecycle,
            key,
            key.lineage_id,
            None,
            None,
            None,
            chain,
            receipts,
            local_error=local_error,
            registry_error=str(exc),
        )

    try:
        snapshot = registry.get_integrity_snapshot(key)
        record = snapshot.record
        active = snapshot.active_transaction
        active_initialization = snapshot.active_initialization
        local_sequence = int(chain["sequence"]) if chain is not None else None
        local_head = str(chain["head_sha256"]) if chain is not None else None
        state = lineage_registry.classify_integrity(
            record,
            local_sequence=local_sequence,
            local_head_sha256=local_head,
            has_local_history=has_local_history,
            active_transaction=active,
            active_initialization=active_initialization,
        )
        if record is not None and state == "aligned":
            if chain is None:
                state = "history_missing"
            elif chain.get("state") != record.lifecycle_state:
                state = "history_diverged"
                local_error = "local lifecycle state does not match lineage registry"
            elif record.memory_mode != ctx.policy["memory_mode"]:
                state = "history_diverged"
                local_error = "profile memory_mode does not match adopted lineage"
            elif record.memory_mode == "required":
                exact_error = _required_checkpoint_local_error(
                    ctx,
                    record,
                    snapshot.checkpoint_payloads,
                    receipt_paths,
                )
                if exact_error:
                    state = "history_diverged"
                    local_error = exact_error
        if record is not None and local_error and state != "recovery_pending":
            missing_tokens = ("missing", "without chain.json", "receipt count")
            state = (
                "history_missing"
                if any(token in local_error for token in missing_tokens)
                else "history_diverged"
            )
        lifecycle = (
            record.lifecycle_state
            if record is not None
            else (str(chain.get("state") or INITIAL_STATE) if chain else INITIAL_STATE)
        )
        return LineageIntegrity(
            state,
            lifecycle,
            key,
            key.lineage_id,
            registry,
            record,
            active,
            chain,
            receipts,
            local_error=local_error,
            active_initialization=active_initialization,
        )
    except lineage_registry.RoleLineageError as exc:
        lifecycle = str(chain.get("state") or INITIAL_STATE) if chain else INITIAL_STATE
        return LineageIntegrity(
            "registry_unavailable",
            lifecycle,
            key,
            key.lineage_id,
            registry,
            None,
            None,
            chain,
            receipts,
            local_error=local_error,
            registry_error=str(exc),
        )


def _require_aligned_lineage(ctx: GovernanceContext) -> LineageIntegrity:
    integrity = _lineage_integrity(ctx)
    if integrity.integrity_state != "aligned":
        detail = integrity.local_error or integrity.registry_error
        suffix = f": {detail}" if detail else ""
        raise RoleGovernanceError(
            f"integrity_state={integrity.integrity_state}; lifecycle publication is blocked{suffix}"
        )
    if integrity.registry is None or integrity.record is None or integrity.chain is None:
        raise RoleGovernanceError("integrity_state=registry_unavailable; lineage authority is absent")
    return integrity


def _latest(receipts: Iterable[dict[str, Any]], event: str) -> dict[str, Any] | None:
    for receipt in reversed(list(receipts)):
        if receipt.get("event") == event:
            return receipt
    return None


def _find_handoff(receipts: list[dict[str, Any]], handoff_id: str) -> dict[str, Any]:
    wanted = handoff_id.strip()
    matches = [
        r
        for r in receipts
        if r.get("event", "").endswith("_handoff")
        and wanted
        in {
            str(r.get("receipt_sha256") or ""),
            str((r.get("memory") or {}).get("memory_id") or ""),
            str(r.get("transition_sha256") or ""),
        }
    ]
    if len(matches) != 1:
        raise RoleGovernanceError(
            f"handoff id must resolve to exactly one local receipt, got {len(matches)}"
        )
    return matches[0]


def _actor(
    ctx: GovernanceContext,
    phase: str,
    *,
    role: str | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    env_role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
    env_session = os.environ.get("BUGATE_SESSION_ID", "").strip()
    if role is not None and role.strip().lower() != env_role:
        raise RoleGovernanceError(
            "CLI --role must exactly match BUGATE_AGENT_ROLE; use bugate-role run to start a role session"
        )
    if session_id is not None and session_id.strip() != env_session:
        raise RoleGovernanceError("CLI --session-id must exactly match BUGATE_SESSION_ID")
    if not env_role:
        raise RoleGovernanceError("BUGATE_AGENT_ROLE is required for a role transition")
    if env_role not in BUGATE_ROLES:
        raise RoleGovernanceError(f"invalid BUGATE_AGENT_ROLE: {env_role}")
    allowed = ctx.policy["phases"][phase]["allowed_roles"]
    if env_role not in allowed:
        raise RoleGovernanceError(
            f"role {env_role!r} is not allowed in phase {phase}; expected {allowed}"
        )
    if ctx.policy["session_id_required"] and not env_session:
        raise RoleGovernanceError("BUGATE_SESSION_ID is required for this profile")
    runtime = os.environ.get("BUGATE_AGENT_RUNTIME", "").strip().lower()
    if runtime not in {"codex", "claude"}:
        runtime = "unknown"
    return {"role": env_role, "runtime": runtime, "session_id": env_session}


def _memory_namespace(ctx: GovernanceContext) -> str:
    env = os.environ.get("MEMORY_BUS_PROJECT_TAG", "").strip()
    if env:
        return env
    memory = ctx.config.get("memory")
    if isinstance(memory, dict) and str(memory.get("namespace") or "").strip():
        return str(memory["namespace"]).strip()
    return str(ctx.config.get("namespace") or "project:bugate").strip()


def _memory_function(module: Any, names: tuple[str, ...]) -> Callable[..., Any] | None:
    for name in names:
        value = getattr(module, name, None)
        if callable(value):
            return value
    return None


def _memory_prepare(ctx: GovernanceContext, transition: dict[str, Any]) -> dict[str, Any]:
    """Call the strict role-transition adapter when installed by memory_bus.

    The adapter contract is intentionally small: ``prepare_role_transition``
    (or ``record_role_transition``) accepts ``payload=`` and ``strict=`` and
    returns a mapping containing ``memory_id``, ``namespace`` and
    ``verified_at``.  A required profile fails closed if that callable is not
    present; best-effort profiles record an explicit unanchored marker.
    """

    strict = ctx.policy["memory_mode"] == "required"
    namespace = _memory_namespace(ctx)
    try:
        import memory_bus  # type: ignore

        loader = getattr(memory_bus, "load_local_env", None)
        if callable(loader):
            loader()  # system-home client credentials; never print their values

        prepare = _memory_function(
            memory_bus, ("prepare_role_transition", "record_role_transition")
        )
        finalize = _memory_function(
            memory_bus, ("finalize_role_transition", "bind_role_receipt")
        )
        if prepare is None or finalize is None:
            raise RuntimeError("strict role-transition Memory adapter is unavailable")
        result = prepare(payload=transition, strict=strict)
        if not isinstance(result, dict) or not str(result.get("memory_id") or ""):
            raise RuntimeError("Memory prepare did not return memory_id")
        result.setdefault("namespace", namespace)
        result.setdefault("verified_at", utc_now())
        result["_finalizer"] = finalize
        return result
    except Exception as exc:
        if strict:
            raise RoleGovernanceError(
                f"strict Memory transition failed before local receipt publication: {exc}"
            ) from exc
        return {
            "namespace": namespace,
            "memory_id": "",
            "verified_at": "",
            "status": "best_effort_unavailable",
        }


def _memory_finalize(
    ctx: GovernanceContext,
    prepared: dict[str, Any],
    receipt_hash: str,
    transition: dict[str, Any],
) -> dict[str, Any]:
    finalizer = prepared.pop("_finalizer", None)
    strict = ctx.policy["memory_mode"] == "required"
    if finalizer is None:
        return prepared
    try:
        result = finalizer(
            memory_id=prepared["memory_id"],
            receipt_sha256=receipt_hash,
            expected=transition,
            strict=strict,
        )
        if isinstance(result, dict):
            for key in ("namespace", "memory_id", "verified_at"):
                if result.get(key) and result.get(key) != prepared.get(key):
                    raise RuntimeError(f"Memory finalize changed stable field {key}")
        return prepared
    except Exception as exc:
        if strict:
            raise RoleGovernanceError(
                f"strict Memory receipt binding failed before local publication: {exc}"
            ) from exc
        prepared["status"] = "best_effort_finalize_failed"
        return prepared


def _memory_verify(ctx: GovernanceContext, receipt: dict[str, Any]) -> None:
    try:
        import memory_bus  # type: ignore

        loader = getattr(memory_bus, "load_local_env", None)
        if callable(loader):
            loader()

        verifier = _memory_function(memory_bus, ("verify_role_transition",))
        if verifier is None:
            raise RuntimeError("verify_role_transition adapter is unavailable")
        result = verifier(receipt=receipt, strict=True)
        if result is False:
            raise RuntimeError("Memory verification returned false")
    except Exception as exc:
        raise RoleGovernanceError(f"strict Memory verification failed: {exc}") from exc


def _memory_ensure_lineage_root(
    ctx: GovernanceContext,
    key: lineage_registry.LineageKey,
) -> dict[str, Any]:
    try:
        import memory_bus  # type: ignore

        loader = getattr(memory_bus, "load_local_env", None)
        if callable(loader):
            loader()
        ensure = _memory_function(memory_bus, ("ensure_role_lineage_root",))
        if ensure is None:
            raise RuntimeError("strict lineage-root Memory adapter is unavailable")
        result = ensure(key.as_dict(), lineage_id=key.lineage_id)
        if not isinstance(result, dict):
            raise RuntimeError("lineage-root Memory adapter returned no result")
        if result.get("lineage_id") != key.lineage_id:
            raise RuntimeError("lineage-root Memory adapter changed lineage identity")
        return result
    except Exception as exc:
        raise RoleGovernanceError(f"strict Memory lineage root failed: {exc}") from exc


def _memory_probe_lineage_root(
    ctx: GovernanceContext,
    key: lineage_registry.LineageKey,
) -> dict[str, Any] | None:
    try:
        import memory_bus  # type: ignore

        loader = getattr(memory_bus, "load_local_env", None)
        if callable(loader):
            loader()
        probe = _memory_function(memory_bus, ("probe_role_lineage_root",))
        if probe is None:
            raise RuntimeError("strict lineage-root probe is unavailable")
        result = probe(key.as_dict(), lineage_id=key.lineage_id)
        if result is None:
            return None
        if not isinstance(result, dict) or result.get("lineage_id") != key.lineage_id:
            raise RuntimeError("exact lineage root is mismatched")
        return result
    except Exception as exc:
        raise RoleGovernanceError(f"strict Memory lineage root verification failed: {exc}") from exc


def _memory_create_checkpoint(
    ctx: GovernanceContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        import memory_bus  # type: ignore

        loader = getattr(memory_bus, "load_local_env", None)
        if callable(loader):
            loader()
        create = _memory_function(memory_bus, ("create_role_lineage_checkpoint",))
        if create is None:
            raise RuntimeError("strict lineage-checkpoint Memory adapter is unavailable")
        result = create(payload)
        if not isinstance(result, dict) or not str(result.get("checkpoint_id") or ""):
            raise RuntimeError("lineage checkpoint did not return an exact ID")
        if result.get("payload") != payload:
            raise RuntimeError("lineage checkpoint payload changed during exact verification")
        return result
    except Exception as exc:
        raise RoleGovernanceError(f"strict Memory lineage checkpoint failed: {exc}") from exc


def _memory_get_checkpoint(
    ctx: GovernanceContext,
    checkpoint_id: str,
) -> dict[str, Any]:
    try:
        import memory_bus  # type: ignore

        loader = getattr(memory_bus, "load_local_env", None)
        if callable(loader):
            loader()
        getter = _memory_function(memory_bus, ("get_role_lineage_checkpoint",))
        if getter is None:
            raise RuntimeError("strict lineage-checkpoint exact GET is unavailable")
        result = getter(checkpoint_id)
        if not isinstance(result, dict) or result.get("checkpoint_id") != checkpoint_id:
            raise RuntimeError("lineage checkpoint exact ID mismatch")
        return result
    except Exception as exc:
        not_found = getattr(locals().get("memory_bus"), "MemoryNotFound", None)
        if isinstance(not_found, type) and isinstance(exc, not_found):
            raise _MemoryCheckpointNotFound(
                "strict Memory lineage checkpoint is absent"
            ) from exc
        raise RoleGovernanceError(
            f"strict Memory lineage checkpoint exact verification failed: {exc}"
        ) from exc


def _memory_probe_checkpoint(
    ctx: GovernanceContext,
    checkpoint_id: str,
) -> dict[str, Any] | None:
    """Exact-GET a deterministic checkpoint; only Memory's typed 404 is absent."""

    try:
        return _memory_get_checkpoint(ctx, checkpoint_id)
    except (_MemoryCheckpointNotFound, KeyError):
        # KeyError is accepted only from the in-process synthetic adapter used
        # by unit fixtures.  The production adapter maps only a typed HTTP 404
        # to _MemoryCheckpointNotFound; auth/outage failures remain fatal.
        return None
    except Exception as exc:
        raise RoleGovernanceError(
            f"strict Memory lineage checkpoint probe failed: {exc}"
        ) from exc


def _verify_checkpoint_result(
    result: dict[str, Any],
    expected_payload: dict[str, Any],
    *,
    checkpoint_id: str,
    label: str,
) -> None:
    if (
        result.get("checkpoint_id") != checkpoint_id
        or result.get("memory_id") not in (None, "", checkpoint_id)
        or result.get("content_sha256") not in (None, "", checkpoint_id)
        or result.get("payload") != expected_payload
    ):
        raise RoleGovernanceError(f"{label} exact checkpoint does not match canonical payload")


def _verify_strict_lineage_predecessor(
    ctx: GovernanceContext,
    registry: lineage_registry.LineageRegistry,
    key: lineage_registry.LineageKey,
    record: lineage_registry.LineageRecord,
) -> None:
    """Verify the deterministic root and exact committed predecessor after pending."""

    if record.memory_mode != "required":
        return
    expected_root_payload = {
        "schema": "bugate.role-lineage-root/v1",
        "lineage_key": key.as_dict(),
        "lineage_id": key.lineage_id,
    }
    expected_root_id = sha256_bytes(canonical_json(expected_root_payload))
    if record.root_memory_id != expected_root_id:
        raise RoleGovernanceError(
            "strict lineage registry root is not the deterministic lineage root"
        )
    root = _memory_probe_lineage_root(ctx, key)
    if root is None:
        raise RoleGovernanceError("strict Memory deterministic lineage root is absent")
    if (
        root.get("lineage_id") != key.lineage_id
        or root.get("lineage_root_id") != expected_root_id
        or root.get("memory_id") not in (None, "", expected_root_id)
        or root.get("content_sha256") not in (None, "", expected_root_id)
        or root.get("payload") != expected_root_payload
    ):
        raise RoleGovernanceError("strict Memory deterministic lineage root diverged")

    if record.sequence == 0:
        if record.head_sha256 or record.checkpoint_memory_id:
            raise RoleGovernanceError(
                "strict empty lineage has an unexpected head or predecessor checkpoint"
            )
        return
    if not record.head_sha256 or not record.checkpoint_memory_id:
        raise RoleGovernanceError("strict lineage predecessor checkpoint is missing")
    retained = registry.get_checkpoint_payload(
        record.checkpoint_memory_id,
        lineage=key,
    )
    if retained is None:
        raise RoleGovernanceError(
            "strict lineage registry did not retain the predecessor checkpoint"
        )
    try:
        expected_checkpoint = json.loads(retained.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleGovernanceError(
            "strict lineage registry predecessor checkpoint is invalid"
        ) from exc
    if not isinstance(expected_checkpoint, dict) or canonical_json(expected_checkpoint) != retained:
        raise RoleGovernanceError(
            "strict lineage registry predecessor checkpoint is non-canonical"
        )
    checkpoint = _memory_get_checkpoint(ctx, record.checkpoint_memory_id)
    _verify_checkpoint_result(
        checkpoint,
        expected_checkpoint,
        checkpoint_id=record.checkpoint_memory_id,
        label="strict predecessor",
    )
    checks = {
        "lineage_key": key.as_dict(),
        "lineage_id": key.lineage_id,
        "lineage_root_id": record.root_memory_id,
        "sequence": record.sequence,
        "receipt_sha256": record.head_sha256,
        "resulting_state": record.lifecycle_state,
        "registry_revision": record.revision,
    }
    if any(expected_checkpoint.get(field) != value for field, value in checks.items()):
        raise RoleGovernanceError(
            "strict predecessor checkpoint does not match registry head/sequence/state"
        )


def _journal_error(category: str, exc: BaseException) -> str:
    """Return a path-free, one-line machine diagnostic safe for the registry."""

    safe_category = re.sub(r"[^a-z0-9_]+", "_", category.strip().lower()).strip("_")
    safe_type = re.sub(r"[^A-Za-z0-9_]+", "_", type(exc).__name__).strip("_")
    return f"{safe_category or 'operation'}:{safe_type or 'Exception'}"


def _failure_point(name: str, ctx: GovernanceContext, tx_id: str) -> None:
    """Stable no-op seam used by crash-window acceptance tests."""

    del name, ctx, tx_id


def _idempotency_payload(base: dict[str, Any]) -> str:
    value = copy.deepcopy(base)
    for key in (
        "created_at",
        "sequence",
        "previous_receipt_sha256",
        "receipt_sha256",
        "memory",
        "transition_sha256",
        "idempotency_sha256",
    ):
        value.pop(key, None)
    return sha256_bytes(canonical_json(value))


def _checkpoint_envelope(
    ctx: GovernanceContext,
    path: Path,
    body: bytes,
    parsed: dict[str, Any],
    *,
    mode: int = 0o600,
) -> dict[str, Any]:
    return {
        "path": workspace_rel(path, ctx.root),
        "mode": mode,
        "bytes_sha256": sha256_bytes(body),
        "bytes_base64": base64.b64encode(body).decode("ascii"),
        "parsed": copy.deepcopy(parsed),
    }


def _receipt_from_transition(
    transition: dict[str, Any],
    memory_public: dict[str, Any],
    resulting_state: str,
) -> dict[str, Any]:
    lineage = transition.get("lineage")
    created_at = (
        str(lineage.get("receipt_created_at") or "")
        if isinstance(lineage, dict)
        else ""
    )
    if not created_at:
        raise RoleGovernanceError("lineage transition is missing receipt_created_at")
    receipt = {
        key: copy.deepcopy(value)
        for key, value in transition.items()
        if key not in {"schema", "transition_sha256"}
    }
    receipt.update(
        {
            "schema": ROLE_SCHEMA,
            "sequence": int(lineage["expected_sequence"]) + 1,
            "created_at": created_at,
            "transition_sha256": transition["transition_sha256"],
            "memory": copy.deepcopy(memory_public),
            "resulting_state": resulting_state,
        }
    )
    receipt["receipt_sha256"] = receipt_sha256(receipt)
    return receipt


def _new_chain(
    ctx: GovernanceContext,
    previous: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
    state: str,
) -> dict[str, Any]:
    latest = dict(previous["latest_receipts"])
    latest[str(receipt["event"])] = workspace_rel(receipt_path, ctx.root)
    return {
        "schema": CHAIN_SCHEMA,
        "state": state,
        "sequence": receipt["sequence"],
        "head_sha256": receipt["receipt_sha256"],
        "latest_receipts": latest,
    }


def _transition_for_publication(
    ctx: GovernanceContext,
    key: lineage_registry.LineageKey,
    record: lineage_registry.LineageRecord,
    base: dict[str, Any],
) -> dict[str, Any]:
    receipt_created_at = utc_now()
    transition = {
        "schema": TRANSITION_SCHEMA,
        "event": str(base["event"]),
        "uc": ctx.uc,
        "artifact_dir": workspace_rel(ctx.artifact_dir, ctx.root),
        "phase": base["phase"],
        "from_role": base.get("from_role", ""),
        "to_role": base.get("to_role", ""),
        "actor": base["actor"],
        "profile": base["profile"],
        "artifacts": base.get("artifacts", []),
        "dispatch": base.get("dispatch", {}),
        "human_acceptance": base.get("human_acceptance", {}),
        "previous_receipt_sha256": record.head_sha256,
        "idempotency_sha256": _idempotency_payload(base),
        "lineage": {
            "schema": LINEAGE_PRECONDITION_SCHEMA,
            "lineage_id": key.lineage_id,
            "expected_head_sha256": record.head_sha256,
            "expected_sequence": record.sequence,
            "expected_revision": record.revision,
            "previous_checkpoint_memory_id": record.checkpoint_memory_id,
            "receipt_created_at": receipt_created_at,
        },
    }
    for field in (
        "approved_by",
        "decision",
        "handoff_receipt_sha256",
        "handoff_memory_id",
        "accepted_handoff_receipt_sha256",
        "implementation_files",
        "run",
        "recovery",
    ):
        if field in base:
            transition[field] = copy.deepcopy(base[field])
    transition["transition_sha256"] = sha256_bytes(canonical_json(transition))
    return transition


def _validate_publication_candidate(
    ctx: GovernanceContext,
    receipts: list[dict[str, Any]],
    transition: dict[str, Any],
    state: str,
) -> None:
    """Prove a candidate is verifier-acceptable before any durable journal."""

    event = str(transition.get("event") or "candidate")
    path = Path(f"unpublished-{event}-candidate.json")
    candidate = _receipt_from_transition(transition, {}, state)
    _validate_receipt_contract(candidate, path)

    prior_by_hash: dict[str, dict[str, Any]] = {}
    latest_by_event: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        receipt_hash = str(receipt["receipt_sha256"])
        prior_by_hash[receipt_hash] = receipt
        latest_by_event[str(receipt["event"])] = receipt
    predecessor_state = (
        str(receipts[-1]["resulting_state"]) if receipts else INITIAL_STATE
    )
    resulting_state = _verify_lifecycle_graph_edge(
        ctx,
        candidate,
        path,
        predecessor_state=predecessor_state,
        predecessor_hash=str(transition.get("previous_receipt_sha256") or ""),
        prior_by_hash=prior_by_hash,
        latest_by_event=latest_by_event,
    )
    if resulting_state != state:
        raise RoleGovernanceError(
            "unpublished lifecycle candidate does not preserve its target state"
        )


def _publish(ctx: GovernanceContext, base: dict[str, Any], state: str) -> dict[str, Any]:
    if _transition_lock_key(ctx) not in set(
        getattr(_TRANSITION_LOCK_STATE, "keys", set())
    ):
        raise RoleGovernanceError(
            "role transition publication requires the per-UC transition lock"
        )
    integrity = _require_aligned_lineage(ctx)
    receipts = list(integrity.receipts)
    event = str(base["event"])
    idem = _idempotency_payload(base)
    prior = _latest(receipts, event)
    chain = integrity.chain
    record = integrity.record
    registry = integrity.registry
    assert chain is not None and record is not None and registry is not None
    if (
        prior
        and prior.get("idempotency_sha256") == idem
        and prior.get("receipt_sha256") == chain["head_sha256"]
    ):
        return prior
    transition = _transition_for_publication(
        ctx,
        integrity.lineage_key,
        record,
        base,
    )
    _validate_publication_candidate(ctx, receipts, transition, state)
    tx: lineage_registry.TransactionRecord | None = None
    try:
        tx = registry.begin_pending(
            integrity.lineage_key,
            event=event,
            expected_head_sha256=record.head_sha256,
            expected_sequence=record.sequence,
            expected_revision=record.revision,
            expected_checkpoint_memory_id=record.checkpoint_memory_id,
            target_lifecycle_state=state,
            transition_payload=transition,
        )
        _verify_strict_lineage_predecessor(
            ctx,
            registry,
            integrity.lineage_key,
            record,
        )
        prepared = _memory_prepare(ctx, transition)
        _failure_point("after_memory_prepare_http", ctx, tx.tx_id)
        transition_memory_id = str(prepared.get("memory_id") or "")
        stage_kwargs: dict[str, Any] = {}
        if transition_memory_id:
            stage_kwargs["transition_memory_id"] = transition_memory_id
        tx = registry.update_stage(
            tx.tx_id,
            expected_stage=lineage_registry.TX_STAGE_PENDING,
            new_stage=lineage_registry.TX_STAGE_MEMORY_PREPARED,
            **stage_kwargs,
        )
        _failure_point("after_memory_transition", ctx, tx.tx_id)

        memory_public = {
            key: value for key, value in prepared.items() if not key.startswith("_")
        }
        receipt = _receipt_from_transition(transition, memory_public, state)
        finalized_memory = _memory_finalize(
            ctx,
            prepared,
            receipt["receipt_sha256"],
            transition,
        )
        _failure_point("after_memory_finalize_http", ctx, tx.tx_id)
        finalized_public = {
            key: value
            for key, value in finalized_memory.items()
            if not key.startswith("_")
        }
        if finalized_public != receipt["memory"]:
            receipt["memory"] = finalized_public
            receipt["receipt_sha256"] = receipt_sha256(receipt)
        filename = (
            f"{receipt['sequence']:06d}-{event.replace('_', '-')}-"
            f"{receipt['receipt_sha256']}.json"
        )
        receipt_path = _receipt_dir(ctx) / filename
        receipt_body = _json_bytes(receipt)
        new_chain = _new_chain(ctx, chain, receipt_path, receipt, state)
        chain_body = _json_bytes(new_chain)
        tx = registry.update_stage(
            tx.tx_id,
            expected_stage=lineage_registry.TX_STAGE_MEMORY_PREPARED,
            new_stage=lineage_registry.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=receipt["receipt_sha256"],
            receipt_path=workspace_rel(receipt_path, ctx.root),
            receipt_bytes=receipt_body,
            receipt_mode=0o600,
            receipt_sha256=receipt["receipt_sha256"],
        )
        _failure_point("after_receipt_bind", ctx, tx.tx_id)
        tx = registry.update_stage(
            tx.tx_id,
            expected_stage=lineage_registry.TX_STAGE_RECEIPT_BOUND,
            new_stage=lineage_registry.TX_STAGE_MEMORY_FINALIZED,
        )

        if ctx.policy["memory_mode"] == "required":
            checkpoint_payload = {
                "schema": "bugate.role-lineage-checkpoint/v1",
                "lineage_key": integrity.lineage_key.as_dict(),
                "lineage_id": integrity.lineage_id,
                "lineage_root_id": record.root_memory_id,
                "sequence": receipt["sequence"],
                "previous_checkpoint_id": record.checkpoint_memory_id,
                "previous_receipt_sha256": receipt["previous_receipt_sha256"],
                "receipt_sha256": receipt["receipt_sha256"],
                "resulting_state": state,
                "registry_revision": record.revision + 1,
                "receipt_envelope": _checkpoint_envelope(
                    ctx, receipt_path, receipt_body, receipt
                ),
                "chain_envelope": _checkpoint_envelope(
                    ctx, _chain_path(ctx), chain_body, new_chain
                ),
            }
            checkpoint = _memory_create_checkpoint(ctx, checkpoint_payload)
            _failure_point("after_checkpoint_http", ctx, tx.tx_id)
            tx = registry.update_stage(
                tx.tx_id,
                expected_stage=lineage_registry.TX_STAGE_MEMORY_FINALIZED,
                new_stage=lineage_registry.TX_STAGE_CHECKPOINT_VERIFIED,
                checkpoint_memory_id=checkpoint["checkpoint_id"],
                checkpoint_payload=checkpoint_payload,
            )
            _failure_point("after_checkpoint", ctx, tx.tx_id)
            tx = registry.update_stage(
                tx.tx_id,
                expected_stage=lineage_registry.TX_STAGE_CHECKPOINT_VERIFIED,
                new_stage=lineage_registry.TX_STAGE_READY_FOR_CAS,
            )
        else:
            tx = registry.update_stage(
                tx.tx_id,
                expected_stage=lineage_registry.TX_STAGE_MEMORY_FINALIZED,
                new_stage=lineage_registry.TX_STAGE_READY_FOR_CAS,
            )

        committed = registry.compare_and_swap_head(
            tx.tx_id,
            expected_stage=lineage_registry.TX_STAGE_READY_FOR_CAS,
        )
        tx = committed.transaction
        _failure_point("after_registry_cas", ctx, tx.tx_id)
        _atomic_bytes(receipt_path, receipt_body, replace=False, mode=0o600)
        _failure_point("after_receipt_write", ctx, tx.tx_id)
        tx = registry.update_stage(
            tx.tx_id,
            expected_stage=lineage_registry.TX_STAGE_REGISTRY_COMMITTED,
            new_stage=lineage_registry.TX_STAGE_RECEIPT_WRITTEN,
        )
        _failure_point("before_chain_replace", ctx, tx.tx_id)
        _atomic_bytes(_chain_path(ctx), chain_body, replace=True, mode=0o600)
        _failure_point("after_chain_replace", ctx, tx.tx_id)
        tx = registry.update_stage(
            tx.tx_id,
            expected_stage=lineage_registry.TX_STAGE_RECEIPT_WRITTEN,
            new_stage=lineage_registry.TX_STAGE_CHAIN_REPLACED,
        )
        registry.complete(tx.tx_id)
        return receipt
    except Exception as exc:
        if tx is not None:
            try:
                current = registry.get_transaction(tx.tx_id)
                if current is not None and current.status in lineage_registry.ACTIVE_TX_STATUSES:
                    registry.mark_incomplete(
                        current.tx_id,
                        expected_stage=current.stage,
                        error=_journal_error("publish_failed", exc),
                    )
            except Exception:
                pass
        if isinstance(exc, RoleGovernanceError):
            raise
        if isinstance(exc, lineage_registry.RoleLineageError):
            raise RoleGovernanceError(f"lineage transaction failed: {exc}") from exc
        raise


def _human_acceptance_ref(
    ctx: GovernanceContext, receipts: list[dict[str, Any]]
) -> dict[str, Any]:
    required = bool(ctx.policy["human_acceptance_artifacts"])
    if not required:
        return {"required": False, "receipt_sha256": "", "artifact_sha256": ""}
    receipt = _latest(receipts, "human_acceptance")
    if not receipt:
        raise RoleGovernanceError("required human acceptance receipt is missing")
    if receipt.get("profile") != profile_snapshot(ctx):
        raise RoleGovernanceError(
            "profile drifted after human acceptance; record a new human acceptance generation"
        )
    current = []
    for name in ctx.policy["human_acceptance_artifacts"]:
        current.append(_snapshot(ctx.artifact_dir / name, ctx, with_gate=True))
    accepted = receipt.get("artifacts")
    if accepted != sorted(current, key=lambda item: item["path"]):
        raise RoleGovernanceError("human-accepted artifact hash drifted; approve a new generation")
    if any(item.get("gate_status") != "passed" for item in current):
        raise RoleGovernanceError("human acceptance artifact is no longer gate_status: passed")
    artifact_hash = (
        current[0]["sha256"]
        if len(current) == 1
        else sha256_bytes(canonical_json(current))
    )
    return {
        "required": True,
        "receipt_sha256": receipt["receipt_sha256"],
        "artifact_sha256": artifact_hash,
    }


def _require_exact_lineage_id(ctx: GovernanceContext, supplied: str) -> lineage_registry.LineageKey:
    key = _lineage_key(ctx)
    if supplied.strip() != key.lineage_id:
        raise RoleGovernanceError(
            f"exact lineage ID mismatch: expected {key.lineage_id}, got {supplied.strip() or '<missing>'}"
        )
    return key


@_serialized_transition
def lineage_init(
    artifact_dir: str | Path,
    *,
    lineage_id: str,
) -> dict[str, Any]:
    """Explicitly assert true first use; no lifecycle publisher may do this."""

    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    key = _require_exact_lineage_id(ctx, lineage_id)
    integrity = _lineage_integrity(ctx)
    if integrity.local_error:
        raise RoleGovernanceError(
            f"lineage-init refuses malformed local evidence: {integrity.local_error}"
        )
    if integrity.chain is not None and integrity.chain != _empty_chain():
        raise RoleGovernanceError("lineage-init refuses non-empty local history")

    registry = integrity.registry or lineage_registry.LineageRegistry(create=True)
    intent = integrity.active_initialization
    if intent is None:
        if integrity.integrity_state != "uninitialized":
            raise RoleGovernanceError(
                "lineage-init requires integrity_state=uninitialized or its exact "
                f"recovery_pending intent, got {integrity.integrity_state}"
            )
        intent = registry.begin_initialization(
            key,
            lifecycle_state=INITIAL_STATE,
            memory_mode=ctx.policy["memory_mode"],
        )
        _failure_point("after_lineage_init_intent", ctx, intent.init_id)
    elif (
        intent.key != key
        or intent.lifecycle_state != INITIAL_STATE
        or intent.memory_mode != ctx.policy["memory_mode"]
    ):
        raise RoleGovernanceError(
            "lineage-init active initialization does not match the exact lineage contract"
        )

    if intent.stage == lineage_registry.INIT_STAGE_PENDING:
        existing_root = None
        if ctx.policy["memory_mode"] == "required":
            existing_root = _memory_probe_lineage_root(ctx, key)
        _failure_point("after_lineage_init_root_absence_probe", ctx, intent.init_id)
        if existing_root is not None:
            registry.abort_initialization(intent.init_id, error="root_preexisting")
            raise RoleGovernanceError(
                "lineage-init refuses an existing deterministic strict Memory root; "
                "integrity_state=migration_required and explicit operator recovery/adoption "
                "is required"
            )
        intent = registry.mark_initialization_root_absence_verified(intent.init_id)
        _failure_point("after_lineage_init_root_absence_journal", ctx, intent.init_id)

    if intent.stage == lineage_registry.INIT_STAGE_ROOT_ABSENCE_VERIFIED:
        root_memory_id = ""
        if ctx.policy["memory_mode"] == "required":
            root = _memory_ensure_lineage_root(ctx, key)
            expected_payload = {
                "schema": "bugate.role-lineage-root/v1",
                "lineage_key": key.as_dict(),
                "lineage_id": key.lineage_id,
            }
            expected_id = sha256_bytes(canonical_json(expected_payload))
            if (
                root.get("lineage_id") != key.lineage_id
                or root.get("lineage_root_id") != expected_id
                or root.get("memory_id") not in (None, "", expected_id)
                or root.get("content_sha256") not in (None, "", expected_id)
                or root.get("payload") != expected_payload
            ):
                raise RoleGovernanceError(
                    "lineage-init strict Memory root does not match deterministic identity"
                )
            root_memory_id = expected_id
        _failure_point("after_lineage_init_root_http", ctx, intent.init_id)
        intent = registry.bind_initialization_root(
            intent.init_id,
            root_memory_id=root_memory_id,
        )
        _failure_point("after_lineage_init_root_bind", ctx, intent.init_id)

    if intent.stage == lineage_registry.INIT_STAGE_ROOT_VERIFIED:
        record = registry.commit_initialization(intent.init_id)
        _failure_point("after_lineage_init_registry_commit", ctx, intent.init_id)
        intent = registry.get_initialization(intent.init_id)
        if intent is None:  # pragma: no cover - same durable registry.
            raise RoleGovernanceError("lineage initialization journal disappeared")

    if intent.stage == lineage_registry.INIT_STAGE_REGISTRY_INITIALIZED:
        empty_body = _json_bytes(_empty_chain())
        _atomic_bytes(_chain_path(ctx), empty_body, replace=False, mode=0o600)
        if (
            _chain_path(ctx).read_bytes() != empty_body
            or stat.S_IMODE(_chain_path(ctx).stat().st_mode) != 0o600
        ):
            raise RoleGovernanceError(
                "lineage-init empty chain bytes or mode failed exact verification"
            )
        _failure_point("after_lineage_init_chain_write", ctx, intent.init_id)
        intent = registry.mark_initialization_chain_written(intent.init_id)
        _failure_point("after_lineage_init_chain_journal", ctx, intent.init_id)

    if intent.stage == lineage_registry.INIT_STAGE_CHAIN_WRITTEN:
        registry.complete_initialization(intent.init_id)

    record = registry.require_lineage(key)
    empty = _empty_chain()
    if load_chain(ctx) != empty:
        raise RoleGovernanceError("lineage-init completed without the exact empty chain")
    return {
        "ok": True,
        "integrity_state": "aligned",
        "lifecycle_state": INITIAL_STATE,
        "lineage_id": record.lineage_id,
        "lineage_key": record.key.as_dict(),
        "registry_head_sha256": record.head_sha256,
        "registry_sequence": record.sequence,
        "registry_revision": record.revision,
        "lineage_root_memory_id": record.root_memory_id,
    }


def _adoption_checkpoint_payloads(
    ctx: GovernanceContext,
    key: lineage_registry.LineageKey,
    root_memory_id: str,
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    latest: dict[str, str] = {}
    previous_checkpoint = ""
    current_chain_path = _chain_path(ctx)
    current_chain_body = current_chain_path.read_bytes()
    current_chain_mode = stat.S_IMODE(current_chain_path.lstat().st_mode)
    if current_chain_mode != 0o600:
        raise RoleGovernanceError(
            "lineage-adopt requires legacy chain.json mode 0600"
        )
    for receipt in receipts:
        event = str(receipt["event"])
        receipt_path = _receipt_dir(ctx) / (
            f"{receipt['sequence']:06d}-{event.replace('_', '-')}-"
            f"{receipt['receipt_sha256']}.json"
        )
        receipt_body = receipt_path.read_bytes()
        receipt_mode = stat.S_IMODE(receipt_path.lstat().st_mode)
        if receipt_mode != 0o600:
            raise RoleGovernanceError(
                f"lineage-adopt requires legacy receipt mode 0600: {receipt_path.name}"
            )
        latest[event] = workspace_rel(receipt_path, ctx.root)
        chain = {
            "schema": CHAIN_SCHEMA,
            "state": receipt["resulting_state"],
            "sequence": receipt["sequence"],
            "head_sha256": receipt["receipt_sha256"],
            "latest_receipts": dict(latest),
        }
        is_current_head = int(receipt["sequence"]) == len(receipts)
        chain_body = current_chain_body if is_current_head else _json_bytes(chain)
        chain_mode = current_chain_mode if is_current_head else 0o600
        payload = {
            "schema": "bugate.role-lineage-checkpoint/v1",
            "lineage_key": key.as_dict(),
            "lineage_id": key.lineage_id,
            "lineage_root_id": root_memory_id,
            "sequence": receipt["sequence"],
            "previous_checkpoint_id": previous_checkpoint,
            "previous_receipt_sha256": receipt["previous_receipt_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "resulting_state": receipt["resulting_state"],
            "registry_revision": 0,
            "receipt_envelope": _checkpoint_envelope(
                ctx, receipt_path, receipt_body, receipt, mode=receipt_mode
            ),
            "chain_envelope": _checkpoint_envelope(
                ctx, current_chain_path, chain_body, chain, mode=chain_mode
            ),
        }
        payloads.append(payload)
        previous_checkpoint = sha256_bytes(canonical_json(payload))
    return payloads


@_serialized_transition
def lineage_adopt(
    artifact_dir: str | Path,
    *,
    lineage_id: str,
    expected_head: str,
) -> dict[str, Any]:
    """Adopt a verified legacy v1 chain without rewriting one receipt byte."""

    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    key = _require_exact_lineage_id(ctx, lineage_id)
    integrity = _lineage_integrity(ctx)
    if integrity.integrity_state != "migration_required":
        raise RoleGovernanceError(
            "lineage-adopt requires integrity_state=migration_required, got "
            f"{integrity.integrity_state}"
        )
    receipts = verify_chain(ctx)
    chain = load_chain(ctx)
    if not receipts or chain["sequence"] < 1:
        raise RoleGovernanceError("lineage-adopt requires a non-empty legacy chain")
    if expected_head.strip() != chain["head_sha256"]:
        raise RoleGovernanceError(
            "lineage-adopt expected head mismatch; no registry row was created"
        )

    root_memory_id = ""
    checkpoint_memory_id = ""
    checkpoint_payload: dict[str, Any] | None = None
    checkpoint_history: list[dict[str, Any]] = []
    if ctx.policy["memory_mode"] == "required":
        checkpoint_history = _adoption_checkpoint_payloads(
            ctx,
            key,
            lineage_registry.lineage_root_id(key),
            receipts,
        )
        for receipt in receipts:
            _memory_verify(ctx, receipt)
        root = _memory_ensure_lineage_root(ctx, key)
        root_memory_id = str(root["lineage_root_id"])
        if root_memory_id != lineage_registry.lineage_root_id(key):
            raise RoleGovernanceError(
                "strict Memory lineage root differs from prevalidated adoption payloads"
            )
        for payload in checkpoint_history:
            created = _memory_create_checkpoint(ctx, payload)
            checkpoint_memory_id = str(created["checkpoint_id"])
            checkpoint_payload = payload
    registry = lineage_registry.LineageRegistry(create=True)
    record = registry.adopt(
        key,
        lifecycle_state=str(chain["state"]),
        sequence=int(chain["sequence"]),
        head_sha256=str(chain["head_sha256"]),
        memory_mode=ctx.policy["memory_mode"],
        root_memory_id=root_memory_id,
        checkpoint_memory_id=checkpoint_memory_id,
        checkpoint_payload=checkpoint_payload,
        checkpoint_history=checkpoint_history or None,
    )
    return {
        "ok": True,
        "integrity_state": "aligned",
        "lifecycle_state": record.lifecycle_state,
        "lineage_id": record.lineage_id,
        "registry_head_sha256": record.head_sha256,
        "registry_sequence": record.sequence,
        "registry_revision": record.revision,
        "checkpoint_memory_id": record.checkpoint_memory_id,
        "receipts_rewritten": 0,
    }


def _decode_recovery_envelope(envelope: Any, label: str) -> tuple[Path, bytes, int, dict[str, Any]]:
    if not isinstance(envelope, dict) or set(envelope) != {
        "path",
        "mode",
        "bytes_sha256",
        "bytes_base64",
        "parsed",
    }:
        raise RoleGovernanceError(f"invalid {label} envelope schema")
    path_value = envelope.get("path")
    if not isinstance(path_value, str):
        raise RoleGovernanceError(f"invalid {label} envelope path")
    path = Path(path_value)
    if path.is_absolute() or path.as_posix() != path_value or ".." in path.parts:
        raise RoleGovernanceError(f"unsafe {label} envelope path")
    mode = envelope.get("mode")
    if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o7777:
        raise RoleGovernanceError(f"invalid {label} envelope mode")
    if mode != 0o600:
        raise RoleGovernanceError(f"{label} envelope mode must be 0600")
    encoded = envelope.get("bytes_base64")
    if not isinstance(encoded, str) or not encoded:
        raise RoleGovernanceError(f"invalid {label} envelope bytes")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except Exception as exc:
        raise RoleGovernanceError(f"invalid {label} envelope base64") from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise RoleGovernanceError(f"non-canonical {label} envelope base64")
    if envelope.get("bytes_sha256") != sha256_bytes(raw):
        raise RoleGovernanceError(f"{label} envelope byte hash mismatch")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleGovernanceError(f"{label} envelope is not UTF-8 JSON") from exc
    if not isinstance(parsed, dict) or parsed != envelope.get("parsed"):
        raise RoleGovernanceError(f"{label} envelope parsed JSON mismatch")
    if raw != _json_bytes(parsed):
        raise RoleGovernanceError(f"{label} envelope has non-canonical exact bytes")
    return path, raw, mode, parsed


def _validate_recovery_history(
    ctx: GovernanceContext,
    receipt_envelopes: list[dict[str, Any]],
    chain_envelope: dict[str, Any],
    *,
    expected_head: str,
    expected_sequence: int,
) -> tuple[list[tuple[Path, bytes, int, dict[str, Any]]], tuple[Path, bytes, int, dict[str, Any]]]:
    decoded_receipts = [
        _decode_recovery_envelope(value, f"receipt[{index}]")
        for index, value in enumerate(receipt_envelopes, 1)
    ]
    decoded_chain = _decode_recovery_envelope(chain_envelope, "chain")
    if len(decoded_receipts) != expected_sequence:
        raise RoleGovernanceError(
            "recovery history receipt count does not match registry sequence"
        )
    expected_artifact = workspace_rel(ctx.artifact_dir, ctx.root)
    previous = ""
    latest: dict[str, str] = {}
    prior_by_hash: dict[str, dict[str, Any]] = {}
    latest_by_event: dict[str, dict[str, Any]] = {}
    lifecycle_state = INITIAL_STATE
    for expected, (path, raw, _mode, receipt) in enumerate(decoded_receipts, 1):
        _validate_receipt_contract(receipt, path)
        if receipt.get("sequence") != expected or receipt.get("schema") != ROLE_SCHEMA:
            raise RoleGovernanceError("recovery receipt sequence/schema mismatch")
        if receipt.get("uc") != ctx.uc or receipt.get("artifact_dir") != expected_artifact:
            raise RoleGovernanceError("recovery receipt belongs to another lineage context")
        actual = receipt_sha256(receipt)
        if receipt.get("receipt_sha256") != actual:
            raise RoleGovernanceError("recovery receipt semantic hash mismatch")
        if receipt.get("previous_receipt_sha256") != previous:
            raise RoleGovernanceError("recovery receipt chain link mismatch")
        expected_path = _receipt_dir(ctx) / (
            f"{expected:06d}-{receipt['event'].replace('_', '-')}-{actual}.json"
        )
        if path != Path(workspace_rel(expected_path, ctx.root)):
            raise RoleGovernanceError("recovery receipt path/filename mismatch")
        if json.loads(raw) != receipt:
            raise RoleGovernanceError("recovery receipt bytes changed during validation")
        event = str(receipt["event"])
        lifecycle_state = _verify_lifecycle_graph_edge(
            ctx,
            receipt,
            path,
            predecessor_state=lifecycle_state,
            predecessor_hash=previous,
            prior_by_hash=prior_by_hash,
            latest_by_event=latest_by_event,
        )
        latest[event] = path.as_posix()
        previous = actual
        prior_by_hash[actual] = receipt
        latest_by_event[event] = receipt
    chain_path, _chain_raw, _chain_mode, chain = decoded_chain
    if chain_path != Path(workspace_rel(_chain_path(ctx), ctx.root)):
        raise RoleGovernanceError("recovery chain path mismatch")
    if set(chain) != {"schema", "state", "sequence", "head_sha256", "latest_receipts"}:
        raise RoleGovernanceError("recovery chain has a non-minimal schema")
    if (
        chain.get("schema") != CHAIN_SCHEMA
        or chain.get("sequence") != expected_sequence
        or chain.get("head_sha256") != expected_head
        or previous != expected_head
        or chain.get("latest_receipts") != latest
    ):
        raise RoleGovernanceError("recovery chain does not match exact registry head")
    if chain.get("state") != lifecycle_state:
        raise RoleGovernanceError("recovery chain lifecycle state mismatch")
    return decoded_receipts, decoded_chain


def _checkpoint_recovery_history(
    ctx: GovernanceContext,
    record: lineage_registry.LineageRecord,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if record.memory_mode != "required":
        raise RoleGovernanceError(
            "best_effort lineage has no strict checkpoint history; provide --archive"
        )
    if _memory_probe_lineage_root(ctx, record.key) is None:
        raise RoleGovernanceError("strict Memory lineage root is absent")
    if record.sequence == 0:
        empty = _empty_chain()
        body = _json_bytes(empty)
        return [], _checkpoint_envelope(ctx, _chain_path(ctx), body, empty), "strict_memory"
    if not record.checkpoint_memory_id:
        raise RoleGovernanceError("required lineage registry is missing its checkpoint head")
    expected_sequence = record.sequence
    checkpoint_id = record.checkpoint_memory_id
    reverse_receipts: list[dict[str, Any]] = []
    latest_chain: dict[str, Any] | None = None
    seen: set[str] = set()
    while expected_sequence > 0:
        if not checkpoint_id or checkpoint_id in seen:
            raise RoleGovernanceError("checkpoint lineage is truncated or cyclic")
        seen.add(checkpoint_id)
        result = _memory_get_checkpoint(ctx, checkpoint_id)
        payload = result.get("payload")
        if not isinstance(payload, dict):
            raise RoleGovernanceError("checkpoint exact GET omitted canonical payload")
        if (
            payload.get("lineage_id") != record.lineage_id
            or payload.get("lineage_key") != record.key.as_dict()
            or payload.get("lineage_root_id") != record.root_memory_id
            or payload.get("sequence") != expected_sequence
        ):
            raise RoleGovernanceError("checkpoint lineage identity/sequence mismatch")
        reverse_receipts.append(copy.deepcopy(payload["receipt_envelope"]))
        if latest_chain is None:
            latest_chain = copy.deepcopy(payload["chain_envelope"])
        checkpoint_id = str(payload.get("previous_checkpoint_id") or "")
        expected_sequence -= 1
    if checkpoint_id:
        raise RoleGovernanceError("checkpoint lineage has an unexpected predecessor")
    assert latest_chain is not None
    return list(reversed(reverse_receipts)), latest_chain, "strict_memory"


def _archive_recovery_history(
    archive: Path,
    *,
    lineage_id: str,
    expected_head: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    value = _read_json(archive)
    if set(value) != {
        "schema",
        "lineage_id",
        "expected_head_sha256",
        "receipt_envelopes",
        "chain_envelope",
    } or value.get("schema") != RECOVERY_ARCHIVE_SCHEMA:
        raise RoleGovernanceError("operator recovery archive schema is invalid")
    if value.get("lineage_id") != lineage_id or value.get("expected_head_sha256") != expected_head:
        raise RoleGovernanceError("operator recovery archive lineage/head mismatch")
    receipts = value.get("receipt_envelopes")
    chain = value.get("chain_envelope")
    if not isinstance(receipts, list) or not isinstance(chain, dict):
        raise RoleGovernanceError("operator recovery archive envelopes are invalid")
    return copy.deepcopy(receipts), copy.deepcopy(chain), "operator_archive"


def _local_predecessor_recovery_history(
    ctx: GovernanceContext,
    receipts: tuple[dict[str, Any], ...],
    chain: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Capture an already verified best-effort predecessor for tx replay."""

    paths = sorted(_receipt_dir(ctx).glob("*.json")) if _receipt_dir(ctx).exists() else []
    if len(paths) != len(receipts):
        raise RoleGovernanceError("local predecessor receipt count changed during recovery")
    envelopes = [
        _checkpoint_envelope(
            ctx,
            path,
            path.read_bytes(),
            receipt,
            mode=stat.S_IMODE(path.stat().st_mode),
        )
        for path, receipt in zip(paths, receipts)
    ]
    chain_path = _chain_path(ctx)
    return (
        envelopes,
        _checkpoint_envelope(
            ctx,
            chain_path,
            chain_path.read_bytes(),
            chain,
            mode=stat.S_IMODE(chain_path.stat().st_mode),
        ),
        "local_predecessor",
    )


def _preflight_recovery_writes(
    ctx: GovernanceContext,
    receipts: list[tuple[Path, bytes, int, dict[str, Any]]],
    chain: tuple[Path, bytes, int, dict[str, Any]],
) -> None:
    expected_paths = {path for path, _raw, _mode, _parsed in receipts}
    if ctx.evidence_dir.exists() and ctx.evidence_dir.is_symlink():
        raise RoleGovernanceError("recovery refuses a symlink role-evidence directory")
    if _receipt_dir(ctx).exists() and _receipt_dir(ctx).is_symlink():
        raise RoleGovernanceError("recovery refuses a symlink receipt directory")
    existing = (
        sorted(_receipt_dir(ctx).glob("*.json")) if _receipt_dir(ctx).exists() else []
    )
    for path in existing:
        rel = Path(workspace_rel(path, ctx.root))
        if rel not in expected_paths:
            raise RoleGovernanceError("recovery found an unexpected existing receipt")
    for rel, raw, _mode, _parsed in receipts:
        target = ctx.root / rel
        if target.is_symlink():
            raise RoleGovernanceError("recovery refuses a symlink receipt target")
        if target.exists() and (not target.is_file() or target.read_bytes() != raw):
            raise RoleGovernanceError(
                "recovery existing receipt byte conflict; no target writes performed"
            )
    chain_rel, _raw, _mode, _parsed = chain
    chain_target = ctx.root / chain_rel
    if chain_target.is_symlink():
        raise RoleGovernanceError("recovery refuses a symlink chain target")
    if chain_target.exists() and not chain_target.is_file():
        raise RoleGovernanceError("recovery chain target is not a regular file")


def _apply_recovery_history(
    ctx: GovernanceContext,
    receipts: list[tuple[Path, bytes, int, dict[str, Any]]],
    chain: tuple[Path, bytes, int, dict[str, Any]],
    *,
    tx_id: str,
) -> None:
    for rel, raw, mode, _parsed in receipts:
        target = ctx.root / rel
        _atomic_bytes(target, raw, replace=False, mode=mode)
        os.chmod(target, mode, follow_symlinks=False)
        _failure_point("recovery_receipt_write", ctx, tx_id)
    chain_rel, chain_raw, chain_mode, _parsed = chain
    target_chain = ctx.root / chain_rel
    _atomic_bytes(target_chain, chain_raw, replace=True, mode=chain_mode)
    os.chmod(target_chain, chain_mode, follow_symlinks=False)
    _failure_point("recovery_chain_replace", ctx, tx_id)


def _recovery_phase(lifecycle_state: str) -> str:
    if lifecycle_state in {"implementation_unlocked", "awaiting_reviewer_acceptance"}:
        return "implementation"
    if lifecycle_state in {"post_run_active", "closed"}:
        return "post_run"
    return "pre_code"


def _transaction_transition(
    transaction: lineage_registry.TransactionRecord,
) -> dict[str, Any]:
    raw = transaction.transition_payload
    if raw is None:
        raise RoleGovernanceError("recovery transaction has no durable transition")
    try:
        transition = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleGovernanceError("recovery transition is not canonical JSON") from exc
    if not isinstance(transition, dict) or canonical_json(transition) != raw:
        raise RoleGovernanceError("recovery transition bytes are not canonical")
    supplied_hash = str(transition.get("transition_sha256") or "")
    unhashed = copy.deepcopy(transition)
    unhashed.pop("transition_sha256", None)
    if supplied_hash != sha256_bytes(canonical_json(unhashed)):
        raise RoleGovernanceError("recovery transition semantic hash mismatch")
    lineage = transition.get("lineage")
    if not isinstance(lineage, dict):
        raise RoleGovernanceError("recovery transition is missing lineage precondition")
    checks = {
        "lineage_id": transaction.lineage_id,
        "expected_head_sha256": transaction.expected_head_sha256,
        "expected_sequence": transaction.expected_sequence,
        "expected_revision": transaction.expected_revision,
        "previous_checkpoint_memory_id": transaction.expected_checkpoint_memory_id,
    }
    if any(lineage.get(field) != value for field, value in checks.items()):
        raise RoleGovernanceError("recovery transition lineage precondition diverged")
    if (
        transition.get("schema") != TRANSITION_SCHEMA
        or transition.get("event") != transaction.event
    ):
        raise RoleGovernanceError("recovery transition schema/event diverged")
    return transition


def _transaction_receipt(
    ctx: GovernanceContext,
    transaction: lineage_registry.TransactionRecord,
) -> tuple[Path, bytes, dict[str, Any]]:
    if (
        not transaction.receipt_path
        or transaction.receipt_bytes is None
        or transaction.receipt_mode is None
        or not transaction.receipt_sha256
    ):
        raise RoleGovernanceError("recovery transaction lacks an exact receipt journal")
    relative_path = Path(transaction.receipt_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RoleGovernanceError("recovery transaction receipt path is unsafe")
    target = ctx.root / relative_path
    try:
        receipt = json.loads(transaction.receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleGovernanceError("recovery transaction receipt is not JSON") from exc
    if not isinstance(receipt, dict) or _json_bytes(receipt) != transaction.receipt_bytes:
        raise RoleGovernanceError("recovery transaction receipt bytes are non-canonical")
    _validate_receipt_contract(receipt, target)
    transition = _transaction_transition(transaction)
    receipt_transition = copy.deepcopy(transition)
    receipt_transition.pop("transition_sha256", None)
    expected_name = (
        f"{transaction.target_sequence:06d}-{transaction.event.replace('_', '-')}-"
        f"{transaction.receipt_sha256}.json"
    )
    if (
        target.parent != _receipt_dir(ctx)
        or target.name != expected_name
        or receipt.get("receipt_sha256") != transaction.receipt_sha256
        or receipt_sha256(receipt) != transaction.receipt_sha256
        or receipt.get("sequence") != transaction.target_sequence
        or receipt.get("previous_receipt_sha256") != transaction.expected_head_sha256
        or receipt.get("resulting_state") != transaction.target_lifecycle_state
        or _transition_from_receipt(receipt) != receipt_transition
    ):
        raise RoleGovernanceError("recovery transaction receipt journal diverged")
    return target, transaction.receipt_bytes, receipt


def _transaction_chain(
    ctx: GovernanceContext,
    transaction: lineage_registry.TransactionRecord,
    receipt_path: Path,
    receipt: dict[str, Any],
) -> tuple[bytes, int, dict[str, Any]]:
    current = load_chain(ctx)
    if (
        current.get("sequence") == transaction.target_sequence
        and current.get("head_sha256") == transaction.target_head_sha256
        and current.get("state") == transaction.target_lifecycle_state
    ):
        candidate = current
    elif (
        current.get("sequence") == transaction.expected_sequence
        and current.get("head_sha256") == transaction.expected_head_sha256
    ):
        candidate = _new_chain(
            ctx,
            current,
            receipt_path,
            receipt,
            transaction.target_lifecycle_state,
        )
    else:
        raise RoleGovernanceError(
            "recovery local chain matches neither transaction predecessor nor target"
        )
    body = _json_bytes(candidate)
    mode = 0o600
    if transaction.checkpoint_payload is not None:
        try:
            checkpoint = json.loads(transaction.checkpoint_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RoleGovernanceError(
                "recovery transaction checkpoint journal is not JSON"
            ) from exc
        if (
            not isinstance(checkpoint, dict)
            or canonical_json(checkpoint) != transaction.checkpoint_payload
            or sha256_bytes(transaction.checkpoint_payload)
            != transaction.checkpoint_memory_id
        ):
            raise RoleGovernanceError(
                "recovery transaction checkpoint journal is non-canonical"
            )
        checkpoint_checks = {
            "schema": "bugate.role-lineage-checkpoint/v1",
            "lineage_id": transaction.lineage_id,
            "sequence": transaction.target_sequence,
            "previous_checkpoint_id": transaction.expected_checkpoint_memory_id,
            "previous_receipt_sha256": transaction.expected_head_sha256,
            "receipt_sha256": transaction.receipt_sha256,
            "resulting_state": transaction.target_lifecycle_state,
            "registry_revision": transaction.expected_revision + 1,
        }
        if any(checkpoint.get(field) != value for field, value in checkpoint_checks.items()):
            raise RoleGovernanceError(
                "recovery transaction checkpoint precondition diverged"
            )
        receipt_rel, checkpoint_receipt, checkpoint_receipt_mode, parsed_receipt = (
            _decode_recovery_envelope(
                checkpoint.get("receipt_envelope"),
                "transaction checkpoint receipt",
            )
        )
        if (
            receipt_rel != Path(workspace_rel(receipt_path, ctx.root))
            or checkpoint_receipt != transaction.receipt_bytes
            or checkpoint_receipt_mode != transaction.receipt_mode
            or parsed_receipt != receipt
        ):
            raise RoleGovernanceError(
                "recovery transaction checkpoint receipt envelope diverged"
            )
        rel, checkpoint_body, checkpoint_mode, parsed = _decode_recovery_envelope(
            checkpoint.get("chain_envelope"),
            "transaction checkpoint chain",
        )
        if rel != Path(workspace_rel(_chain_path(ctx), ctx.root)):
            raise RoleGovernanceError("recovery transaction checkpoint chain path diverged")
        if checkpoint_body != body or parsed != candidate:
            raise RoleGovernanceError("recovery transaction checkpoint chain bytes diverged")
        mode = checkpoint_mode
    return body, mode, candidate


def _checkpoint_payload_for_transaction(
    ctx: GovernanceContext,
    key: lineage_registry.LineageKey,
    record: lineage_registry.LineageRecord,
    transaction: lineage_registry.TransactionRecord,
    receipt_path: Path,
    receipt_body: bytes,
    receipt: dict[str, Any],
    chain_body: bytes,
    chain: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "bugate.role-lineage-checkpoint/v1",
        "lineage_key": key.as_dict(),
        "lineage_id": key.lineage_id,
        "lineage_root_id": record.root_memory_id,
        "sequence": transaction.target_sequence,
        "previous_checkpoint_id": transaction.expected_checkpoint_memory_id,
        "previous_receipt_sha256": transaction.expected_head_sha256,
        "receipt_sha256": transaction.receipt_sha256,
        "resulting_state": transaction.target_lifecycle_state,
        "registry_revision": transaction.expected_revision + 1,
        "receipt_envelope": _checkpoint_envelope(
            ctx,
            receipt_path,
            receipt_body,
            receipt,
            mode=transaction.receipt_mode or 0o600,
        ),
        "chain_envelope": _checkpoint_envelope(
            ctx,
            _chain_path(ctx),
            chain_body,
            chain,
        ),
    }


def _resume_lifecycle_transaction(
    ctx: GovernanceContext,
    registry: lineage_registry.LineageRegistry,
    key: lineage_registry.LineageKey,
    record: lineage_registry.LineageRecord,
    claim: lineage_registry.RecoveryClaim,
) -> tuple[dict[str, Any], lineage_registry.TransactionRecord]:
    """Replay one journaled event through exact local publication.

    The claimed transaction deliberately remains active at ``chain_replaced``.
    Recovery either completes an existing evidence-recovery event or atomically
    hands an original/restore transaction to its evidence-recovery successor.
    Ending the source here would recreate an aligned-without-audit crash gap.
    """

    token = claim.claim_token
    transaction = claim.transaction
    transition = _transaction_transition(transaction)
    prepared: dict[str, Any] | None = None
    pre_cas = (
        lineage_registry.TX_STAGES.index(transaction.stage)
        < lineage_registry.TX_STAGES.index(
            lineage_registry.TX_STAGE_REGISTRY_COMMITTED
        )
    )
    if pre_cas:
        if (
            record.head_sha256 != transaction.expected_head_sha256
            or record.sequence != transaction.expected_sequence
            or record.revision != transaction.expected_revision
            or record.checkpoint_memory_id
            != transaction.expected_checkpoint_memory_id
        ):
            raise RoleGovernanceError(
                "recovery transaction predecessor no longer matches registry authority"
            )
        _verify_strict_lineage_predecessor(ctx, registry, key, record)

    if transaction.stage == lineage_registry.TX_STAGE_PENDING:
        prepared = _memory_prepare(ctx, transition)
        _failure_point("after_memory_prepare_http", ctx, transaction.tx_id)
        memory_id = str(prepared.get("memory_id") or "")
        transaction = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=token,
            expected_stage=transaction.stage,
            new_stage=lineage_registry.TX_STAGE_MEMORY_PREPARED,
            **({"transition_memory_id": memory_id} if memory_id else {}),
        )
        _failure_point("after_memory_transition", ctx, transaction.tx_id)

    if transaction.stage == lineage_registry.TX_STAGE_MEMORY_PREPARED:
        if prepared is None:
            if record.memory_mode == "best_effort" and not transaction.transition_memory_id:
                # The empty ID is itself the durable decision that this event
                # was unanchored.  An availability flip must not rewrite the
                # receipt's Memory semantics during crash replay.
                prepared = {
                    "namespace": _memory_namespace(ctx),
                    "memory_id": "",
                    "verified_at": "",
                    "status": "best_effort_unavailable",
                }
            else:
                prepared = _memory_prepare(ctx, transition)
        if str(prepared.get("memory_id") or "") != transaction.transition_memory_id:
            raise RoleGovernanceError(
                "recovery Memory transition exact ID differs from durable journal"
            )
        memory_public = {
            field: value for field, value in prepared.items() if not field.startswith("_")
        }
        receipt = _receipt_from_transition(
            transition,
            memory_public,
            transaction.target_lifecycle_state,
        )
        finalized = _memory_finalize(
            ctx,
            prepared,
            receipt["receipt_sha256"],
            transition,
        )
        _failure_point("after_memory_finalize_http", ctx, transaction.tx_id)
        finalized_public = {
            field: value for field, value in finalized.items() if not field.startswith("_")
        }
        if finalized_public != receipt["memory"]:
            receipt["memory"] = finalized_public
            receipt["receipt_sha256"] = receipt_sha256(receipt)
        receipt_path = _receipt_dir(ctx) / (
            f"{receipt['sequence']:06d}-{transaction.event.replace('_', '-')}-"
            f"{receipt['receipt_sha256']}.json"
        )
        receipt_body = _json_bytes(receipt)
        transaction = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=token,
            expected_stage=transaction.stage,
            new_stage=lineage_registry.TX_STAGE_RECEIPT_BOUND,
            target_head_sha256=receipt["receipt_sha256"],
            receipt_path=workspace_rel(receipt_path, ctx.root),
            receipt_bytes=receipt_body,
            receipt_mode=0o600,
            receipt_sha256=receipt["receipt_sha256"],
        )
        _failure_point("after_receipt_bind", ctx, transaction.tx_id)

    if transaction.stage == lineage_registry.TX_STAGE_RECEIPT_BOUND:
        transaction = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=token,
            expected_stage=transaction.stage,
            new_stage=lineage_registry.TX_STAGE_MEMORY_FINALIZED,
        )

    if transaction.stage == lineage_registry.TX_STAGE_MEMORY_FINALIZED:
        receipt_path, receipt_body, receipt = _transaction_receipt(ctx, transaction)
        chain_body, _chain_mode, chain = _transaction_chain(
            ctx,
            transaction,
            receipt_path,
            receipt,
        )
        if record.memory_mode == "required":
            payload = _checkpoint_payload_for_transaction(
                ctx,
                key,
                record,
                transaction,
                receipt_path,
                receipt_body,
                receipt,
                chain_body,
                chain,
            )
            checkpoint_id = sha256_bytes(canonical_json(payload))
            checkpoint = _memory_probe_checkpoint(ctx, checkpoint_id)
            if checkpoint is None:
                checkpoint = _memory_create_checkpoint(ctx, payload)
            _failure_point("after_checkpoint_http", ctx, transaction.tx_id)
            _verify_checkpoint_result(
                checkpoint,
                payload,
                checkpoint_id=checkpoint_id,
                label="recovery candidate",
            )
            transaction = registry.mark_recovery_stage(
                transaction.tx_id,
                claim_token=token,
                expected_stage=transaction.stage,
                new_stage=lineage_registry.TX_STAGE_CHECKPOINT_VERIFIED,
                checkpoint_memory_id=checkpoint_id,
                checkpoint_payload=payload,
            )
            _failure_point("after_checkpoint", ctx, transaction.tx_id)
        else:
            transaction = registry.mark_recovery_stage(
                transaction.tx_id,
                claim_token=token,
                expected_stage=transaction.stage,
                new_stage=lineage_registry.TX_STAGE_READY_FOR_CAS,
            )

    if transaction.stage == lineage_registry.TX_STAGE_CHECKPOINT_VERIFIED:
        if transaction.checkpoint_payload is None:
            raise RoleGovernanceError("verified checkpoint has no durable canonical payload")
        expected_payload = json.loads(transaction.checkpoint_payload.decode("utf-8"))
        checkpoint = _memory_get_checkpoint(ctx, transaction.checkpoint_memory_id)
        _verify_checkpoint_result(
            checkpoint,
            expected_payload,
            checkpoint_id=transaction.checkpoint_memory_id,
            label="recovery verified",
        )
        transaction = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=token,
            expected_stage=transaction.stage,
            new_stage=lineage_registry.TX_STAGE_READY_FOR_CAS,
        )

    if transaction.stage == lineage_registry.TX_STAGE_READY_FOR_CAS:
        if record.memory_mode == "required":
            if transaction.checkpoint_payload is None:
                raise RoleGovernanceError("ready transaction lost its checkpoint payload")
            expected_payload = json.loads(transaction.checkpoint_payload.decode("utf-8"))
            checkpoint = _memory_get_checkpoint(ctx, transaction.checkpoint_memory_id)
            _verify_checkpoint_result(
                checkpoint,
                expected_payload,
                checkpoint_id=transaction.checkpoint_memory_id,
                label="pre-CAS recovery",
            )
        committed = registry.compare_and_swap_head(
            transaction.tx_id,
            expected_stage=transaction.stage,
            recovery_token=token,
        )
        transaction = committed.transaction
        _failure_point("after_registry_cas", ctx, transaction.tx_id)

    receipt_path, receipt_body, receipt = _transaction_receipt(ctx, transaction)
    chain_body, chain_mode, _chain = _transaction_chain(
        ctx,
        transaction,
        receipt_path,
        receipt,
    )
    if transaction.stage == lineage_registry.TX_STAGE_REGISTRY_COMMITTED:
        _atomic_bytes(
            receipt_path,
            receipt_body,
            replace=False,
            mode=transaction.receipt_mode or 0o600,
        )
        _failure_point("after_receipt_write", ctx, transaction.tx_id)
        transaction = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=token,
            expected_stage=transaction.stage,
            new_stage=lineage_registry.TX_STAGE_RECEIPT_WRITTEN,
        )
    if transaction.stage == lineage_registry.TX_STAGE_RECEIPT_WRITTEN:
        _failure_point("before_chain_replace", ctx, transaction.tx_id)
        _atomic_bytes(_chain_path(ctx), chain_body, replace=True, mode=chain_mode)
        _failure_point("after_chain_replace", ctx, transaction.tx_id)
        transaction = registry.mark_recovery_stage(
            transaction.tx_id,
            claim_token=token,
            expected_stage=transaction.stage,
            new_stage=lineage_registry.TX_STAGE_CHAIN_REPLACED,
        )
    if transaction.stage != lineage_registry.TX_STAGE_CHAIN_REPLACED:
        raise RoleGovernanceError(
            f"unsupported lifecycle recovery stage: {transaction.stage}"
        )
    return receipt, transaction


def _release_recovery_claim(
    registry: lineage_registry.LineageRegistry,
    tx_id: str,
    claim_token: str,
    *,
    category: str,
    error: BaseException,
) -> None:
    """Best-effort release of exactly the still-owned recovery claim."""

    try:
        latest = registry.get_transaction(tx_id)
        if (
            latest is not None
            and latest.status == lineage_registry.TX_STATUS_RECOVERING
            and latest.recovery_token == claim_token
        ):
            registry.release_recovery(
                tx_id,
                claim_token=claim_token,
                error=_journal_error(category, error),
            )
    except Exception:
        # Preserve the original failure.  A claim that could not be released is
        # itself durable and remains visible as recovery_pending.
        pass


def _recovery_publication_base(
    ctx: GovernanceContext,
    record: lineage_registry.LineageRecord,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "event": RECOVERY_EVENT,
        "phase": _recovery_phase(record.lifecycle_state),
        "from_role": "agent",
        "to_role": "",
        "actor": {"role": "agent", "runtime": "unknown", "session_id": ""},
        "profile": profile_snapshot(ctx),
        "artifacts": [],
        "dispatch": {},
        "human_acceptance": {},
        "recovery": {
            "source": source,
            "recovered_head_sha256": record.head_sha256,
            "recovered_sequence": record.sequence,
            "preserved_lifecycle_state": record.lifecycle_state,
        },
    }


def _require_exact_local_lineage_head(
    ctx: GovernanceContext,
    record: lineage_registry.LineageRecord,
) -> None:
    receipts = verify_chain(ctx)
    chain = load_chain(ctx)
    if (
        len(receipts) != record.sequence
        or int(chain.get("sequence") or 0) != record.sequence
        or str(chain.get("head_sha256") or "") != record.head_sha256
        or str(chain.get("state") or "") != record.lifecycle_state
    ):
        raise RoleGovernanceError(
            "recovered local history does not match the exact registry head"
        )


def _finish_recovery_successor(
    ctx: GovernanceContext,
    registry: lineage_registry.LineageRegistry,
    key: lineage_registry.LineageKey,
    record: lineage_registry.LineageRecord,
    transaction: lineage_registry.TransactionRecord,
    *,
    claim: lineage_registry.RecoveryClaim | None = None,
) -> dict[str, Any]:
    """Claim, replay, and complete exactly one evidence-recovery successor."""

    if transaction.event != RECOVERY_EVENT:
        raise RoleGovernanceError(
            "recovery successor transaction has an unexpected event"
        )
    if claim is None:
        claim = registry.claim_recovery(
            transaction.tx_id,
            expected_stage=transaction.stage,
        )
    elif claim.transaction.tx_id != transaction.tx_id:
        raise RoleGovernanceError("recovery successor claim belongs to another transaction")
    try:
        receipt, terminal = _resume_lifecycle_transaction(
            ctx,
            registry,
            key,
            record,
            claim,
        )
        registry.complete(
            terminal.tx_id,
            expected_stage=lineage_registry.TX_STAGE_CHAIN_REPLACED,
            recovery_token=claim.claim_token,
        )
        return receipt
    except Exception as exc:
        _release_recovery_claim(
            registry,
            transaction.tx_id,
            claim.claim_token,
            category="recovery_successor_failed",
            error=exc,
        )
        raise


def _recovery_result(
    ctx: GovernanceContext,
    record: lineage_registry.LineageRecord,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    status = status_data(ctx.artifact_dir)
    expected = {
        "lineage_id": record.lineage_id,
        "lifecycle_state": record.lifecycle_state,
        "sequence": record.sequence,
        "head_sha256": record.head_sha256,
        "registry_sequence": record.sequence,
        "registry_head_sha256": record.head_sha256,
        "registry_revision": record.revision,
        "receipt_count": record.sequence,
    }
    if (
        status.get("ok") is not True
        or status.get("integrity_state") != "aligned"
        or status.get("active_transaction") is not None
        or any(status.get(field) != value for field, value in expected.items())
    ):
        detail = str(status.get("error") or "status/registry values diverged")
        raise RoleGovernanceError(
            f"recovery final lineage-integrity verification failed: {detail}"
        )
    return {
        "ok": True,
        "integrity_state": status["integrity_state"],
        "lifecycle_state": status["lifecycle_state"],
        "lineage_id": status["lineage_id"],
        "recovery_receipt": receipt,
    }


@_serialized_transition
def recover(
    artifact_dir: str | Path,
    *,
    lineage_id: str,
    expected_head: str,
    archive: str | Path | None = None,
) -> dict[str, Any]:
    """Restore an exact committed head, then append a state-preserving audit event."""

    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    key = _require_exact_lineage_id(ctx, lineage_id)
    integrity = _lineage_integrity(ctx)
    record = integrity.record
    registry = integrity.registry
    if record is None or registry is None:
        raise RoleGovernanceError("recover requires an existing lineage registry record")
    expected = "" if expected_head == "EMPTY" else expected_head.strip()
    if expected != record.head_sha256:
        raise RoleGovernanceError("recover expected head does not match registry head")
    if integrity.integrity_state not in {
        "history_missing",
        "history_diverged",
        "recovery_pending",
    }:
        raise RoleGovernanceError(
            "recover requires history_missing, history_diverged, or recovery_pending"
        )

    active = integrity.active_transaction
    active_pre_cas = active is not None and (
        lineage_registry.TX_STAGES.index(active.stage)
        < lineage_registry.TX_STAGES.index(
            lineage_registry.TX_STAGE_REGISTRY_COMMITTED
        )
    )
    exact_local_predecessor = (
        integrity.chain is not None
        and not integrity.local_error
        and int(integrity.chain.get("sequence") or 0) == record.sequence
        and str(integrity.chain.get("head_sha256") or "") == record.head_sha256
        and str(integrity.chain.get("state") or "") == record.lifecycle_state
    )
    if (
        archive is None
        and record.memory_mode == "best_effort"
        and active_pre_cas
        and exact_local_predecessor
    ):
        receipt_envelopes, chain_envelope, source = (
            _local_predecessor_recovery_history(
                ctx,
                integrity.receipts,
                integrity.chain,
            )
        )
    elif archive is not None:
        receipt_envelopes, chain_envelope, source = _archive_recovery_history(
            Path(archive), lineage_id=key.lineage_id, expected_head=record.head_sha256
        )
    else:
        receipt_envelopes, chain_envelope, source = _checkpoint_recovery_history(ctx, record)
    staged_receipts, staged_chain = _validate_recovery_history(
        ctx,
        receipt_envelopes,
        chain_envelope,
        expected_head=record.head_sha256,
        expected_sequence=record.sequence,
    )
    if archive is not None and record.memory_mode == "required":
        retained_receipts, retained_chain, _retained_source = (
            _checkpoint_recovery_history(ctx, record)
        )
        if (
            receipt_envelopes != retained_receipts
            or chain_envelope != retained_chain
        ):
            raise RoleGovernanceError(
                "operator recovery archive does not exactly match retained "
                "strict checkpoint history"
            )
    _preflight_recovery_writes(ctx, staged_receipts, staged_chain)

    if active is not None:
        source_transaction = active
        source_claim = registry.claim_recovery(
            active.tx_id,
            expected_stage=active.stage,
        )
        source_transaction = source_claim.transaction
    else:
        source_transaction = registry.begin_pending(
            key,
            event="recovery_restore",
            expected_head_sha256=record.head_sha256,
            expected_sequence=record.sequence,
            expected_revision=record.revision,
            expected_checkpoint_memory_id=record.checkpoint_memory_id,
            target_lifecycle_state=record.lifecycle_state,
            transition_payload={
                "schema": "bugate.role-recovery-journal/v1",
                "lineage_id": record.lineage_id,
                "expected_head_sha256": record.head_sha256,
                "expected_sequence": record.sequence,
                "source": source,
            },
        )
        source_claim = registry.claim_recovery(
            source_transaction.tx_id,
            expected_stage=lineage_registry.TX_STAGE_PENDING,
        )
        source_transaction = source_claim.transaction

    try:
        _apply_recovery_history(
            ctx,
            staged_receipts,
            staged_chain,
            tx_id=source_transaction.tx_id,
        )

        # A recovery successor is already the one durable audit event for this
        # operation.  Resume it verbatim; never synthesize a second successor.
        if source_transaction.event == RECOVERY_EVENT:
            receipt = _finish_recovery_successor(
                ctx,
                registry,
                key,
                record,
                source_transaction,
                claim=source_claim,
            )
            final_record = registry.require_lineage(key)
            _require_exact_local_lineage_head(ctx, final_record)
            return _recovery_result(ctx, final_record, receipt)

        if source_transaction.event == "recovery_restore":
            recovered_record = record
            if active is not None:
                source = f"{source}+resumed_restore"
        else:
            _receipt, source_transaction = _resume_lifecycle_transaction(
                ctx,
                registry,
                key,
                record,
                source_claim,
            )
            recovered_record = registry.require_lineage(key)
            source = f"{source}+resumed_lifecycle_tx"

        _require_exact_local_lineage_head(ctx, recovered_record)
        base = _recovery_publication_base(ctx, recovered_record, source=source)
        transition = _transition_for_publication(ctx, key, recovered_record, base)
        handoff = registry.handoff_recovery_successor(
            source_transaction.tx_id,
            claim_token=source_claim.claim_token,
            transition_payload=transition,
        )
        _failure_point(
            "after_recovery_successor_handoff",
            ctx,
            handoff.successor_transaction.tx_id,
        )
    except Exception as exc:
        _release_recovery_claim(
            registry,
            source_transaction.tx_id,
            source_claim.claim_token,
            category="recovery_failed",
            error=exc,
        )
        raise

    receipt = _finish_recovery_successor(
        ctx,
        registry,
        key,
        handoff.lineage,
        handoff.successor_transaction,
    )
    final_record = registry.require_lineage(key)
    _require_exact_local_lineage_head(ctx, final_record)
    return _recovery_result(ctx, final_record, receipt)


@_serialized_transition
def approve(
    artifact_dir: str | Path,
    *,
    approved_by: str,
    role: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    _require_aligned_lineage(ctx)
    actor = _actor(ctx, "pre_code", role=role, session_id=session_id)
    if not approved_by.strip():
        raise RoleGovernanceError("--approved-by is required and records an existing human decision")
    # Human acceptance is the last pre-code decision, not a shortcut around
    # Layer 1-3: all configured pre-code artifacts must already be passed.
    _precode_snapshot(ctx)
    artifacts = []
    for name in ctx.policy["human_acceptance_artifacts"]:
        item = _snapshot(ctx.artifact_dir / name, ctx, with_gate=True)
        if item["gate_status"] != "passed":
            raise RoleGovernanceError(
                f"cannot record human acceptance: {item['path']} is not gate_status: passed"
            )
        artifacts.append(item)
    base = {
        "event": "human_acceptance",
        "phase": "pre_code",
        "from_role": "human",
        "to_role": actor["role"],
        "actor": actor,
        "profile": profile_snapshot(ctx),
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
        "dispatch": {},
        "human_acceptance": {"required": True},
        "approved_by": approved_by.strip(),
        "decision": "accepted",
    }
    return _publish(ctx, base, EVENT_STATES["human_acceptance"])


def _phase_for_handoff(ctx: GovernanceContext, actor_role: str, to_role: str) -> tuple[str, str]:
    for target_index in (1, 2):
        target = PHASES[target_index]
        source = PHASES[target_index - 1]
        cfg = ctx.policy["phases"][target]
        if to_role in cfg["allowed_roles"] and actor_role in cfg["requires_handoff_from"]:
            return source, target
    raise RoleGovernanceError(
        f"profile defines no lifecycle handoff from {actor_role!r} to {to_role!r}"
    )


def _compiled_guarded(ctx: GovernanceContext) -> list[re.Pattern[str]]:
    raw = ctx.config.get("guarded_path_regex") or []
    values = [raw] if isinstance(raw, str) else raw
    flags = re.IGNORECASE if _filesystem_case_insensitive(ctx.root) else 0
    return [re.compile(str(value), flags) for value in values]


def _guard_match(ctx: GovernanceContext, path: Path) -> re.Match[str] | None:
    rel = workspace_rel(path, ctx.root)
    absolute = path.resolve().as_posix()
    for regex in _compiled_guarded(ctx):
        match = regex.search(rel) or regex.search(absolute)
        if match:
            return match
    return None


def _files_below(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []

    def fail_walk(exc: OSError) -> None:
        raise RoleGovernanceError(
            f"cannot enumerate phase-owned directory {directory}: {exc}"
        ) from exc

    files: list[Path] = []
    for parent, _subdirs, names in os.walk(
        directory,
        topdown=True,
        onerror=fail_walk,
        followlinks=False,
    ):
        for name in names:
            candidate = Path(parent) / name
            if candidate.is_file():
                files.append(candidate.resolve())
    return files


def _context_artifact_candidates(ctx: GovernanceContext) -> list[Path]:
    candidates = [ctx.artifact_dir]
    template = str(ctx.config.get("artifact_dir_template") or "")
    if template.count("{uc}") == 1:
        before, after = template.split("{uc}", 1)
        prefix = Path(before.rstrip("/")) if before.rstrip("/") else Path(".")
        parent = prefix if prefix.is_absolute() else ctx.root / prefix
        suffix = Path(after.strip("/")) if after.strip("/") else None
        parent = _canonical_existing_path(parent)
        if parent.is_dir() and _within(parent, ctx.root):
            for child in sorted(parent.iterdir()):
                if not child.is_dir():
                    continue
                candidate = child / suffix if suffix is not None else child
                if candidate.is_dir() and _within(candidate, ctx.root):
                    candidates.append(_canonical_existing_path(candidate))
    dedup = {path.as_posix(): path for path in candidates}
    return [dedup[key] for key in sorted(dedup)]


def _role_evidence_dirs(ctx: GovernanceContext) -> list[Path]:
    evidence = Path(ctx.policy["evidence_dir"])
    directories = [
        (artifact / evidence).resolve()
        for artifact in _context_artifact_candidates(ctx)
        if (artifact / evidence).is_dir()
    ]
    dedup = {path.as_posix(): path for path in directories}
    return [dedup[key] for key in sorted(dedup)]


def _same_file_as_descendant(path: Path, directories: Iterable[Path]) -> bool:
    if not path.exists() or not path.is_file():
        return False
    return any(
        _same_existing_path(path, candidate)
        for directory in directories
        for candidate in _files_below(directory)
    )


def _phase_owned_files(
    ctx: GovernanceContext,
    receipts: Iterable[dict[str, Any]],
) -> dict[str, tuple[Path, str]]:
    """Return existing pre-code/implementation/post-run file identities."""

    owned: dict[str, tuple[Path, str]] = {}

    def add(path: Path, phase: str) -> None:
        canonical = path.resolve()
        if canonical.is_file():
            owned.setdefault(canonical.as_posix(), (canonical, phase))

    for name in required_precode_artifacts(ctx.config):
        add(ctx.artifact_dir / name, "pre_code")
    if ctx.artifact_dir.is_dir():
        for path in ctx.artifact_dir.iterdir():
            if path.is_file() and PRECODE_PREFIX_RE.match(path.name):
                add(path, "pre_code")
    for name in POSTRUN_NAMES:
        add(ctx.artifact_dir / name, "post_run")
    for name, phase in {
        "00_multiview": "pre_code",
        "00_adversarial": "pre_code",
        "04_execution": "post_run",
        "05_knowledge": "post_run",
        "00_post_run": "post_run",
    }.items():
        for path in _files_below(ctx.artifact_dir / name):
            add(path, phase)

    receipt_phases = {
        "human_acceptance": "pre_code",
        "designer_handoff": "pre_code",
        "implementer_acceptance": "pre_code",
        "implementer_handoff": "implementation",
        "reviewer_acceptance": "implementation",
    }
    for receipt in receipts:
        phase = receipt_phases.get(str(receipt.get("event") or ""))
        if phase is not None:
            for item in receipt.get("artifacts", []):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    add(ctx.root / item["path"], phase)
        implementation_files = receipt.get("implementation_files", [])
        if isinstance(implementation_files, list):
            for value in implementation_files:
                if isinstance(value, str):
                    add(ctx.root / value, "implementation")
    return owned


def _workspace_identity_aliases(root: Path, path: Path) -> list[Path]:
    """Return existing workspace entries that name the same file object."""

    try:
        target_stat = path.stat()
    except OSError:
        return []
    if not path.is_file():
        return []
    aliases: dict[str, Path] = {}

    def add(candidate: Path) -> None:
        if _within(candidate, root) and _same_existing_path(path, candidate):
            aliases.setdefault(candidate.as_posix(), candidate)

    add(path)
    if target_stat.st_nlink < 2:
        return [aliases[key] for key in sorted(aliases)]

    def fail_walk(exc: OSError) -> None:
        raise RoleGovernanceError(
            f"cannot enumerate workspace file identities below {root}: {exc}"
        ) from exc

    for parent, subdirs, names in os.walk(
        root,
        topdown=True,
        onerror=fail_walk,
        followlinks=False,
    ):
        subdirs[:] = [
            name for name in subdirs if name not in {".git", ".bugate-update"}
        ]
        for name in names:
            add(Path(parent) / name)
    return [aliases[key] for key in sorted(aliases)]


def _context_structural_phase_owned_files(ctx: GovernanceContext) -> list[Path]:
    """Enumerate deterministic pre-code/post-run owners for every configured UC."""

    owned: dict[str, Path] = {}

    def add(path: Path) -> None:
        canonical = path.resolve()
        if canonical.is_file():
            owned.setdefault(canonical.as_posix(), canonical)

    for artifact in _context_artifact_candidates(ctx):
        for name in required_precode_artifacts(ctx.config):
            add(artifact / name)
        for path in artifact.iterdir():
            if path.is_file() and PRECODE_PREFIX_RE.match(path.name):
                add(path)
        for name in POSTRUN_NAMES:
            add(artifact / name)
        for name in (
            "00_multiview",
            "00_adversarial",
            "04_execution",
            "05_knowledge",
            "00_post_run",
        ):
            for path in _files_below(artifact / name):
                add(path)
    return [owned[key] for key in sorted(owned)]


def _receipt_store_mentions_alias(
    artifact: Path,
    evidence_dir: Path,
    root: Path,
    aliases: Iterable[Path],
) -> bool:
    """Cheaply bind a target identity to a receipt store before parsing it."""

    relpaths = {
        workspace_rel(alias, root)
        for alias in aliases
        if _within(alias, root)
    }
    if not relpaths:
        return False

    def path_values(value: object, *, allow_bare: bool) -> list[str]:
        if isinstance(value, str):
            return [value] if allow_bare else []
        if isinstance(value, list):
            values: list[str] = []
            for child in value:
                if isinstance(child, str):
                    if allow_bare:
                        values.append(child)
                else:
                    values.extend(path_values(child, allow_bare=False))
            return values
        if isinstance(value, dict):
            values = [
                child
                for key, child in value.items()
                if key == "path" and isinstance(child, str)
            ]
            values.extend(
                item
                for key, child in value.items()
                if key != "path" and isinstance(child, (dict, list))
                for item in path_values(child, allow_bare=False)
            )
            return values
        return []

    def owned_paths(payload: object) -> list[str]:
        if not isinstance(payload, dict):
            return []
        values: list[str] = []
        if "artifacts" in payload:
            values.extend(path_values(payload["artifacts"], allow_bare=True))
        if "implementation_files" in payload:
            values.extend(
                path_values(payload["implementation_files"], allow_bare=True)
            )
        return values

    def malformed_ownership_mentions(body: bytes) -> bool:
        text = body.decode("utf-8", errors="ignore")
        json_string = r'"(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|[^"\\])*"'

        def value_fragment(start: int) -> str:
            while start < len(text) and text[start].isspace():
                start += 1
            direct = re.match(json_string, text[start:])
            if direct:
                return direct.group(0)
            if start >= len(text) or text[start] not in "[{":
                end = start
                while end < len(text) and text[end] not in ",\n\r}]":
                    end += 1
                return text[start:end]
            stack: list[str] = []
            quoted = False
            escaped = False
            pairs = {"}": "{", "]": "["}
            for index in range(start, len(text)):
                char = text[index]
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                    continue
                if char == '"':
                    quoted = True
                elif char in "[{":
                    stack.append(char)
                elif char in "]}":
                    if not stack or stack[-1] != pairs[char]:
                        return text[start:index]
                    stack.pop()
                    if not stack:
                        return text[start : index + 1]
            return text[start:]

        def decoded_token(token: str) -> str | None:
            try:
                value = json.loads(token)
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, str) else None

        def keyed_string_tokens(fragment: str, key: str) -> list[str]:
            tokens: list[str] = []
            index = 0
            while index < len(fragment):
                match = re.match(json_string, fragment[index:])
                if match is None:
                    index += 1
                    continue
                token = match.group(0)
                end = index + len(token)
                cursor = end
                while cursor < len(fragment) and fragment[cursor].isspace():
                    cursor += 1
                if (
                    decoded_token(token) == key
                    and cursor < len(fragment)
                    and fragment[cursor] == ":"
                ):
                    cursor += 1
                    while cursor < len(fragment) and fragment[cursor].isspace():
                        cursor += 1
                    value_match = re.match(json_string, fragment[cursor:])
                    if value_match is not None:
                        tokens.append(value_match.group(0))
                index = end
            return tokens

        fragments: list[str] = []
        depth = 0
        index = 0
        while index < len(text):
            match = re.match(json_string, text[index:])
            if match is not None:
                token = match.group(0)
                end = index + len(token)
                cursor = end
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
                if (
                    depth == 1
                    and decoded_token(token)
                    in {"artifacts", "implementation_files"}
                    and cursor < len(text)
                    and text[cursor] == ":"
                ):
                    fragments.append(value_fragment(cursor + 1))
                index = end
                continue
            if text[index] in "[{":
                depth += 1
            elif text[index] in "]}":
                depth = max(0, depth - 1)
            index += 1

        for fragment in fragments:
            try:
                decoded = json.loads(fragment)
            except json.JSONDecodeError:
                tokens = keyed_string_tokens(fragment, "path")
                stripped = fragment.lstrip()
                direct = re.match(json_string, stripped)
                if direct:
                    tokens.append(direct.group(0))
                if stripped.startswith("[") and "{" not in stripped:
                    tokens.extend(re.findall(json_string, stripped))
                values = [
                    value
                    for token in tokens
                    if (value := decoded_token(token)) is not None
                ]
            else:
                values = path_values(decoded, allow_bare=True)
            if any(value in relpaths for value in values):
                return True
        return False

    receipts = artifact / evidence_dir / "receipts"
    if not receipts.is_dir():
        return False
    for receipt in sorted(receipts.glob("*.json")):
        try:
            body = receipt.read_bytes()
        except OSError as exc:
            raise RoleGovernanceError(
                f"cannot inspect role receipt ownership {receipt}: {exc}"
            ) from exc
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            if malformed_ownership_mentions(body):
                return True
            continue
        if any(value in relpaths for value in owned_paths(payload)):
            return True
        # A valid JSON parser discards all but the last duplicate object key.
        # The ownership-limited raw scan preserves visible earlier ownership
        # fragments without treating run/metadata strings as path authority.
        if malformed_ownership_mentions(body):
            return True
    return False


def _completion_evidence_reuses_phase_path(
    ctx: GovernanceContext,
    path: Path,
    receipts: Iterable[dict[str, Any]],
) -> bool:
    """Reject phase-owned evidence by path or filesystem-object identity."""

    current_owned = [
        item for item, _phase in _phase_owned_files(ctx, receipts).values()
    ]
    structural_owned = _context_structural_phase_owned_files(ctx)
    if any(
        _same_existing_path(path, reserved)
        for reserved in current_owned + structural_owned
    ):
        return True

    aliases = _workspace_identity_aliases(ctx.root, path)
    if any(_guard_match(ctx, alias) is not None for alias in aliases):
        return True

    evidence_dir = Path(ctx.policy["evidence_dir"])
    for artifact in _context_artifact_candidates(ctx):
        if _same_existing_path(artifact, ctx.artifact_dir):
            continue
        if not _receipt_store_mentions_alias(
            artifact,
            evidence_dir,
            ctx.root,
            aliases,
        ):
            continue
        other = load_context(artifact, root=ctx.root, config=ctx.config)
        other_receipts = verify_chain(other)
        if any(
            _same_existing_path(path, reserved)
            for reserved, _phase in _phase_owned_files(
                other, other_receipts
            ).values()
        ):
            return True
    return False


def role_phase_owned_paths(artifact_dir: str | Path) -> dict[str, str]:
    """Expose verified phase-owned paths for hook same-file identity checks."""

    ctx = load_context(artifact_dir)
    receipts = verify_chain(ctx) if ctx.mode != "off" else []
    return {
        workspace_rel(path, ctx.root): phase
        for path, phase in _phase_owned_files(ctx, receipts).values()
    }


def implementation_snapshot(
    ctx: GovernanceContext, paths: Iterable[str | Path]
) -> list[dict[str, Any]]:
    supplied = list(paths)
    if not supplied:
        raise RoleGovernanceError("implementer handoff requires at least one --implementation-file")
    out: list[dict[str, Any]] = []
    template = str(ctx.config.get("artifact_dir_template") or "")
    for raw in supplied:
        path = Path(raw)
        path = path if path.is_absolute() else ctx.root / path
        path = path.resolve()
        if not _within(path, ctx.root):
            raise RoleGovernanceError(f"implementation file is outside workspace: {path}")
        match = _guard_match(ctx, path)
        if match is None:
            raise RoleGovernanceError(
                f"implementation file does not match guarded_path_regex: {workspace_rel(path, ctx.root)}"
            )
        if template:
            uc = (match.groupdict() or {}).get("uc")
            if not uc:
                raise RoleGovernanceError(
                    "guarded implementation regex must capture (?P<uc>...) when artifact_dir_template is used"
                )
            if uc != ctx.uc:
                normalized = re.sub(r"[-_]", "", uc).lower()
                expected = re.sub(r"[-_]", "", ctx.uc).lower()
                if ctx.config.get("uc_dir_resolve") != "normalized-glob" or normalized != expected:
                    raise RoleGovernanceError(
                        f"implementation file belongs to UC {uc!r}, not {ctx.uc!r}"
                    )
        out.append(_snapshot(path, ctx))
    dedup = {item["path"]: item for item in out}
    return [dedup[key] for key in sorted(dedup)]


@_serialized_transition
def handoff(
    artifact_dir: str | Path,
    *,
    phase: str,
    to_role: str,
    implementation_files: Iterable[str | Path] = (),
    role: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    _require_aligned_lineage(ctx)
    actor_role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
    source_phase, target_phase = _phase_for_handoff(ctx, actor_role, to_role.lower())
    if phase not in {source_phase, target_phase}:
        raise RoleGovernanceError(
            f"handoff --phase must be {source_phase} (or target alias {target_phase})"
        )
    actor = _actor(ctx, source_phase, role=role, session_id=session_id)
    receipts = verify_chain(ctx)
    if source_phase == "pre_code":
        verify_precode_semantics(ctx)
        artifacts = _precode_snapshot(ctx)
        dispatch = dispatch_snapshot(ctx)
        human = _human_acceptance_ref(ctx, receipts)
        event = "designer_handoff"
        extra: dict[str, Any] = {}
    elif source_phase == "implementation":
        verify_evidence(ctx.artifact_dir, phase="implementation")
        accepted = _latest(receipts, "implementer_acceptance")
        if not accepted:
            raise RoleGovernanceError("implementer acceptance is required before handoff")
        _require_exact_acceptance_actor(
            accepted,
            actor,
            transition="implementer_handoff",
        )
        artifacts = _precode_snapshot(ctx)
        impl = implementation_snapshot(ctx, implementation_files)
        artifacts = sorted(artifacts + impl, key=lambda item: item["path"])
        dispatch = dispatch_snapshot(ctx)
        human = _human_acceptance_ref(ctx, receipts)
        event = "implementer_handoff"
        extra = {
            "accepted_handoff_receipt_sha256": accepted["handoff_receipt_sha256"],
            "implementation_files": impl,
        }
    else:
        raise RoleGovernanceError(f"handoff is not supported from phase {source_phase}")
    base = {
        "event": event,
        "phase": source_phase,
        "from_role": actor["role"],
        "to_role": to_role.lower(),
        "actor": actor,
        "profile": profile_snapshot(ctx),
        "artifacts": artifacts,
        "dispatch": dispatch,
        "human_acceptance": human,
        **extra,
    }
    return _publish(ctx, base, EVENT_STATES[event])


@_serialized_transition
def accept(
    artifact_dir: str | Path,
    *,
    phase: str,
    handoff_id: str,
    role: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    _require_aligned_lineage(ctx)
    if phase not in {"implementation", "post_run"}:
        raise RoleGovernanceError("accept --phase must be implementation or post_run")
    actor = _actor(ctx, phase, role=role, session_id=session_id)
    receipts = verify_chain(ctx)
    source_roles = ctx.policy["phases"][phase]["requires_handoff_from"]
    handoff_receipt = _find_handoff(receipts, handoff_id)
    expected_handoff_event = (
        "designer_handoff" if phase == "implementation" else "implementer_handoff"
    )
    if handoff_receipt.get("event") != expected_handoff_event:
        raise RoleGovernanceError(
            f"{phase} acceptance requires a {expected_handoff_event} receipt"
        )
    latest_handoff = _latest(receipts, expected_handoff_event)
    if not latest_handoff or latest_handoff.get("receipt_sha256") != handoff_receipt.get(
        "receipt_sha256"
    ):
        raise RoleGovernanceError(
            f"{phase} handoff is stale: accept the latest {expected_handoff_event}"
        )
    if handoff_receipt.get("from_role") not in source_roles:
        raise RoleGovernanceError("handoff source role does not satisfy phase configuration")
    if handoff_receipt.get("to_role") != actor["role"]:
        raise RoleGovernanceError("handoff was not addressed to the active role")
    _verify_snapshot(ctx, handoff_receipt)
    if handoff_receipt.get("event") == "designer_handoff":
        if handoff_receipt.get("human_acceptance") != _human_acceptance_ref(ctx, receipts):
            raise RoleGovernanceError("designer handoff human-acceptance anchor is stale")
    if ctx.policy["memory_mode"] == "required":
        exact_memory_id = str((handoff_receipt.get("memory") or {}).get("memory_id") or "")
        if not exact_memory_id or handoff_id != exact_memory_id:
            raise RoleGovernanceError(
                "required Memory mode accepts only the handoff's exact Memory ID"
            )
        _memory_verify(ctx, handoff_receipt)
    if handoff_receipt.get("actor", {}).get("role") == actor["role"]:
        raise RoleGovernanceError("a role cannot accept its own handoff")
    if (
        ctx.policy["require_distinct_sessions"]
        and handoff_receipt.get("actor", {}).get("session_id") == actor["session_id"]
    ):
        raise RoleGovernanceError("handoff and acceptance must use distinct session IDs")
    event = f"{actor['role']}_acceptance"
    existing = [
        item
        for item in receipts
        if item.get("event") == event
        and item.get("handoff_receipt_sha256") == handoff_receipt["receipt_sha256"]
    ]
    chain_state = str(load_chain(ctx).get("state") or INITIAL_STATE)
    expected_state = {
        "implementation": "awaiting_implementer_acceptance",
        "post_run": "awaiting_reviewer_acceptance",
    }[phase]
    retry_states = {
        "implementation": {
            "implementation_unlocked",
            "awaiting_reviewer_acceptance",
            "post_run_active",
            "closed",
        },
        "post_run": {"post_run_active", "closed"},
    }[phase]
    if existing:
        if chain_state not in retry_states:
            raise RoleGovernanceError(
                f"{phase} acceptance is stale for current chain state {chain_state!r}"
            )
        prior = existing[-1]
        if prior.get("actor") == actor:
            return prior
        raise RoleGovernanceError("this handoff was already accepted by a different session")
    if chain_state != expected_state:
        raise RoleGovernanceError(
            f"{phase} acceptance requires chain state {expected_state!r}, "
            f"not {chain_state!r}"
        )
    base = {
        "event": event,
        "phase": phase,
        "from_role": handoff_receipt["from_role"],
        "to_role": actor["role"],
        "actor": actor,
        "profile": handoff_receipt["profile"],
        "artifacts": handoff_receipt["artifacts"],
        "dispatch": handoff_receipt.get("dispatch", {}),
        "human_acceptance": handoff_receipt.get("human_acceptance", {}),
        "handoff_receipt_sha256": handoff_receipt["receipt_sha256"],
        "handoff_memory_id": (handoff_receipt.get("memory") or {}).get("memory_id", ""),
    }
    if event not in EVENT_STATES:
        raise RoleGovernanceError(f"unsupported lifecycle acceptance event: {event}")
    return _publish(ctx, base, EVENT_STATES[event])


def _verify_snapshot(ctx: GovernanceContext, receipt: dict[str, Any]) -> None:
    current_profile = profile_snapshot(ctx)
    if receipt.get("profile") != current_profile:
        raise RoleGovernanceError(
            "active profile hash/path drifted or effective config changed since role transition"
        )
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list):
        raise RoleGovernanceError("receipt artifacts snapshot is malformed")
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RoleGovernanceError("receipt artifact item is malformed")
        path = ctx.root / item["path"]
        current = _snapshot(path, ctx, with_gate="gate_status" in item)
        if current != item:
            raise RoleGovernanceError(f"artifact drift detected: {item['path']}")


def _accepted_latest_handoff(
    receipts: list[dict[str, Any]],
    acceptance: dict[str, Any],
    *,
    phase: str,
    event: str,
) -> dict[str, Any]:
    """Return the handoff accepted by the current generation, never an older one."""

    latest = _latest(receipts, event)
    if not latest:
        raise RoleGovernanceError(f"{phase} is locked: {event} missing")
    if acceptance.get("handoff_receipt_sha256") != latest.get("receipt_sha256"):
        raise RoleGovernanceError(
            f"{phase} acceptance is stale: latest {event} has not been accepted"
        )
    return _find_handoff(receipts, str(acceptance["handoff_receipt_sha256"]))


def _verify_closed_completion(
    ctx: GovernanceContext,
    receipts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Revalidate the terminal 04/05 and execution-evidence snapshot locally."""

    if load_chain(ctx)["state"] != "closed":
        return None
    completion = _latest(receipts, "reviewer_completion")
    if not completion or completion.get("resulting_state") != "closed":
        raise RoleGovernanceError(
            "closed role chain has no successful reviewer completion receipt"
        )
    _verify_snapshot(ctx, completion)
    return completion


def latest_completion_snapshot_paths(artifact_dir: str | Path) -> set[str]:
    """Return exact workspace paths captured by the latest completion receipt.

    Hooks use this read-only index so arbitrary execution-evidence names are
    governed by their receipt identity instead of a filename heuristic.
    """

    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        return set()
    receipts = verify_chain(ctx)
    completion = _latest(receipts, "reviewer_completion")
    if not completion:
        return set()
    artifacts = completion.get("artifacts")
    if not isinstance(artifacts, list):
        raise RoleGovernanceError("reviewer completion artifacts snapshot is malformed")
    paths: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RoleGovernanceError("reviewer completion artifact item is malformed")
        path = Path(item["path"])
        if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
            raise RoleGovernanceError("reviewer completion artifact path is unsafe")
        resolved = (ctx.root / path).resolve()
        if not _within(resolved, ctx.root):
            raise RoleGovernanceError("reviewer completion artifact path escapes workspace")
        paths.add(path.as_posix())
    return paths


def _verify_acceptance_session(
    ctx: GovernanceContext,
    acceptance: dict[str, Any],
    phase: str,
) -> None:
    """Bind an unlock to the role session that explicitly accepted it."""

    role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
    session = os.environ.get("BUGATE_SESSION_ID", "").strip()
    actor = acceptance.get("actor")
    if not isinstance(actor, dict):
        raise RoleGovernanceError(f"{phase} acceptance actor is malformed")
    if actor.get("role") != role:
        raise RoleGovernanceError(
            f"{phase} acceptance belongs to role {actor.get('role')!r}, not {role or '<unset>'!r}"
        )
    if ctx.policy["session_id_required"] and actor.get("session_id") != session:
        raise RoleGovernanceError(
            f"{phase} acceptance belongs to a different BUGATE_SESSION_ID"
        )


def verify_evidence(
    artifact_dir: str | Path,
    *,
    phase: str | None = None,
    strict_memory: bool = False,
) -> list[dict[str, Any]]:
    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        return []
    receipts = verify_chain(ctx)
    if phase is None:
        _verify_closed_completion(ctx, receipts)
        if strict_memory:
            for receipt in receipts:
                _memory_verify(ctx, receipt)
        return receipts
    if phase == "pre_code":
        if strict_memory:
            for receipt in receipts:
                _memory_verify(ctx, receipt)
        return receipts
    if phase == "implementation":
        acceptance = _latest(receipts, "implementer_acceptance")
        if not acceptance:
            raise RoleGovernanceError("implementation is locked: implementer acceptance missing")
        _verify_acceptance_session(ctx, acceptance, phase)
        handoff_receipt = _accepted_latest_handoff(
            receipts,
            acceptance,
            phase=phase,
            event="designer_handoff",
        )
        if handoff_receipt.get("event") != "designer_handoff":
            raise RoleGovernanceError("implementer acceptance does not reference designer handoff")
        _verify_snapshot(ctx, handoff_receipt)
        human = _human_acceptance_ref(ctx, receipts)
        if handoff_receipt.get("human_acceptance") != human:
            raise RoleGovernanceError("designer handoff human-acceptance anchor drifted")
        if strict_memory:
            _memory_verify(ctx, handoff_receipt)
            _memory_verify(ctx, acceptance)
        return receipts
    if phase == "post_run":
        acceptance = _latest(receipts, "reviewer_acceptance")
        if not acceptance:
            raise RoleGovernanceError("post-run is locked: reviewer acceptance missing")
        chain_state = str(load_chain(ctx).get("state") or INITIAL_STATE)
        if chain_state not in {"post_run_active", "closed"}:
            raise RoleGovernanceError(
                f"post-run is locked in chain state {chain_state!r}; "
                "a current reviewer acceptance is required"
            )
        _verify_closed_completion(ctx, receipts)
        _verify_acceptance_session(ctx, acceptance, phase)
        handoff_receipt = _accepted_latest_handoff(
            receipts,
            acceptance,
            phase=phase,
            event="implementer_handoff",
        )
        if handoff_receipt.get("event") != "implementer_handoff":
            raise RoleGovernanceError("reviewer acceptance does not reference implementer handoff")
        _verify_snapshot(ctx, handoff_receipt)
        if not handoff_receipt.get("implementation_files"):
            raise RoleGovernanceError("implementer handoff has no implementation snapshot")
        if strict_memory:
            _memory_verify(ctx, handoff_receipt)
            _memory_verify(ctx, acceptance)
        return receipts
    raise RoleGovernanceError(f"invalid phase: {phase}")


def preflight(
    artifact_dir: str | Path,
    phase: str,
    *,
    require_acceptance: bool = True,
) -> GovernanceResult:
    """Fast local phase/role/receipt validation; never accesses Memory Service."""

    if phase not in PHASES:
        raise RoleGovernanceError(f"invalid phase: {phase}")
    try:
        ctx = load_context(artifact_dir)
        if ctx.mode == "off":
            return GovernanceResult(True, "off", phase, INITIAL_STATE)
        integrity = _lineage_integrity(ctx)
        chain_state = integrity.lifecycle_state
        errors: list[str] = []
        allow_uninitialized_precode = (
            integrity.integrity_state == "uninitialized" and phase == "pre_code"
        )
        if integrity.integrity_state != "aligned" and not allow_uninitialized_precode:
            detail = integrity.local_error or integrity.registry_error
            errors.append(
                f"integrity_state={integrity.integrity_state}"
                + (f": {detail}" if detail else "")
            )
        role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
        session = os.environ.get("BUGATE_SESSION_ID", "").strip()
        if not role:
            errors.append("BUGATE_AGENT_ROLE is unset")
        elif role not in ctx.policy["phases"][phase]["allowed_roles"]:
            errors.append(
                f"role {role!r} is not allowed in {phase}; expected "
                f"{ctx.policy['phases'][phase]['allowed_roles']}"
            )
        if ctx.policy["session_id_required"] and not session:
            errors.append("BUGATE_SESSION_ID is unset")
        if phase == "post_run" and chain_state == "closed":
            errors.append(
                "post-run is closed by reviewer completion; start a new lifecycle "
                "generation before further post-run writes"
            )
        try:
            if integrity.integrity_state == "aligned":
                verify_chain(ctx)
                if require_acceptance and phase in {"implementation", "post_run"}:
                    verify_evidence(ctx.artifact_dir, phase=phase)
        except RoleGovernanceError as exc:
            errors.append(str(exc))
        if errors and ctx.mode == "required":
            return GovernanceResult(False, ctx.mode, phase, chain_state, errors=errors)
        return GovernanceResult(
            True,
            ctx.mode,
            phase,
            chain_state,
            warnings=errors if ctx.mode == "advisory" else [],
        )
    except (RoleGovernanceError, SystemExit) as exc:
        try:
            # Mode belongs to the governed workspace, not to an arbitrary
            # output/fixture directory supplied by a mutator.  In core/off
            # mode callers legitimately write temporary artifacts outside the
            # checkout; resolving from that path would lose the core config.
            root = find_root(Path.cwd())
            cfg = load_config(root, os.environ.get("BUGATE_PROFILE"))
            hint = governance_mode_hint(cfg)
        except SystemExit:
            hint = "off"  # no governed workspace exists for this operation
        except Exception:
            hint = "required"
        if hint == "off":
            return GovernanceResult(True, "off", phase, INITIAL_STATE)
        if hint == "advisory":
            return GovernanceResult(
                True,
                "advisory",
                phase,
                INITIAL_STATE,
                warnings=[f"malformed advisory role_governance config: {exc}"],
            )
        return GovernanceResult(False, "required", phase, INITIAL_STATE, errors=[str(exc)])


@_serialized_transition
def complete(
    artifact_dir: str | Path,
    *,
    phase: str,
    run_command: str,
    exit_code: int,
    evidence_files: Iterable[str | Path],
    final_gate_status: str,
    role: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if phase != "post_run":
        raise RoleGovernanceError("complete --phase must be post_run")
    ctx = load_context(artifact_dir)
    if ctx.mode == "off":
        raise RoleGovernanceError("role governance is off for this profile")
    _require_aligned_lineage(ctx)
    actor = _actor(ctx, phase, role=role, session_id=session_id)
    receipts = verify_evidence(ctx.artifact_dir, phase="post_run")
    acceptance = _latest(receipts, "reviewer_acceptance")
    if not acceptance:
        raise RoleGovernanceError(
            "reviewer acceptance is required before reviewer_completion"
        )
    _require_exact_acceptance_actor(
        acceptance,
        actor,
        transition="reviewer_completion",
    )
    if not run_command.strip():
        raise RoleGovernanceError("--run-command summary is required")
    if final_gate_status not in {"passed", "failed"}:
        raise RoleGovernanceError("--gate-status must be passed or failed")
    report_items = [_snapshot(ctx.artifact_dir / name, ctx, with_gate=True) for name in sorted(POSTRUN_NAMES)]
    config_owned = [
        path
        for path in (
            ctx.profile_path.resolve(),
            (ctx.root / "bugate.config.yaml").resolve(),
        )
        if path.is_file()
    ]
    role_evidence_dirs = _role_evidence_dirs(ctx)
    evidence = []
    evidence_identities: list[Path] = []
    for supplied in evidence_files:
        path = Path(supplied)
        path = _canonical_existing_path(
            path if path.is_absolute() else ctx.root / path
        )
        workspace_rel(path, ctx.root)
        if any(
            _same_existing_path(path, prior) for prior in evidence_identities
        ):
            raise RoleGovernanceError(
                "reviewer completion evidence contains a duplicate filesystem identity"
            )
        evidence_identities.append(path)
        if any(_within(path, directory) for directory in role_evidence_dirs) or (
            _same_file_as_descendant(path, role_evidence_dirs)
        ):
            raise RoleGovernanceError(
                "reviewer completion evidence cannot be inside the role evidence directory"
            )
        if (
            any(_same_existing_path(path, reserved) for reserved in config_owned)
            or _completion_evidence_reuses_phase_path(ctx, path, receipts)
        ):
            raise RoleGovernanceError(
                "reviewer completion evidence cannot reuse a profile, pre-code, "
                "implementation, or post-run phase-owned path"
            )
        evidence.append(_snapshot(path, ctx))
    if not evidence:
        raise RoleGovernanceError("reviewer completion requires at least one --evidence-file")
    if final_gate_status == "passed":
        if exit_code != 0:
            raise RoleGovernanceError("a passed reviewer completion requires --exit-code 0")
        pending = [item["path"] for item in report_items if item.get("gate_status") != "passed"]
        if pending:
            raise RoleGovernanceError(
                "a passed reviewer completion requires 04/05 gate_status: passed: "
                + ", ".join(pending)
            )
    artifacts = sorted(report_items + evidence, key=lambda item: item["path"])
    state = "closed" if final_gate_status == "passed" else "post_run_active"
    base = {
        "event": "reviewer_completion",
        "phase": "post_run",
        "from_role": actor["role"],
        "to_role": "",
        "actor": actor,
        "profile": profile_snapshot(ctx),
        "artifacts": artifacts,
        "dispatch": {},
        "human_acceptance": {},
        "run": {
            "command_summary": run_command.strip(),
            "exit_code": int(exit_code),
            "evidence": sorted(evidence, key=lambda item: item["path"]),
            "gate_status": final_gate_status,
        },
    }
    if load_chain(ctx)["state"] == "closed":
        prior = _latest(receipts, "reviewer_completion")
        if prior and prior.get("idempotency_sha256") == _idempotency_payload(base):
            return prior
        raise RoleGovernanceError(
            "post-run is already closed; start a new lifecycle generation before "
            "publishing a different reviewer completion"
        )
    return _publish(ctx, base, state)


def status_data(artifact_dir: str | Path) -> dict[str, Any]:
    try:
        ctx = load_context(artifact_dir)
        if ctx.mode == "off":
            chain = load_chain(ctx, allow_uninitialized=True)
            return {
                "ok": True,
                "mode": "off",
                "memory_mode": ctx.policy["memory_mode"],
                "integrity_state": "uninitialized",
                "lifecycle_state": str(chain.get("state") or INITIAL_STATE),
                "role": os.environ.get("BUGATE_AGENT_ROLE", ""),
                "session_id": os.environ.get("BUGATE_SESSION_ID", ""),
                "uc": ctx.uc,
                "artifact_dir": workspace_rel(ctx.artifact_dir, ctx.root),
                "state": str(chain.get("state") or INITIAL_STATE),
                "sequence": int(chain.get("sequence") or 0),
                "head_sha256": str(chain.get("head_sha256") or ""),
                "latest_receipts": chain.get("latest_receipts", {}),
                "receipt_count": 0,
                "error": "",
            }
        integrity = _lineage_integrity(ctx)
        chain = integrity.chain
        record = integrity.record
        error_parts: list[str] = []
        if integrity.integrity_state != "aligned":
            error_parts.append(f"integrity_state={integrity.integrity_state}")
        if integrity.local_error:
            error_parts.append(integrity.local_error)
        if integrity.registry_error:
            error_parts.append(integrity.registry_error)
        if integrity.integrity_state == "aligned":
            try:
                _verify_closed_completion(ctx, list(integrity.receipts))
            except RoleGovernanceError as exc:
                error_parts.append(str(exc))
        sequence = record.sequence if record is not None else int((chain or {}).get("sequence") or 0)
        head = record.head_sha256 if record is not None else str((chain or {}).get("head_sha256") or "")
        latest = (chain or {}).get("latest_receipts", {})
        active = integrity.active_transaction
        active_initialization = integrity.active_initialization
        return {
            "ok": not error_parts and integrity.integrity_state == "aligned",
            "mode": ctx.mode,
            "memory_mode": ctx.policy["memory_mode"],
            "integrity_state": integrity.integrity_state,
            "lifecycle_state": integrity.lifecycle_state,
            "lineage_id": integrity.lineage_id,
            "lineage_key": integrity.lineage_key.as_dict(),
            "role": os.environ.get("BUGATE_AGENT_ROLE", ""),
            "session_id": os.environ.get("BUGATE_SESSION_ID", ""),
            "uc": ctx.uc,
            "artifact_dir": workspace_rel(ctx.artifact_dir, ctx.root),
            "state": integrity.lifecycle_state,
            "sequence": sequence,
            "head_sha256": head,
            "registry_head_sha256": record.head_sha256 if record else "",
            "registry_sequence": record.sequence if record else 0,
            "registry_revision": record.revision if record else 0,
            "lineage_root_memory_id": record.root_memory_id if record else "",
            "checkpoint_memory_id": record.checkpoint_memory_id if record else "",
            "latest_receipts": latest,
            "receipt_count": len(integrity.receipts),
            "active_transaction": (
                {
                    "tx_id": active.tx_id,
                    "event": active.event,
                    "status": active.status,
                    "stage": active.stage,
                    "expected_head_sha256": active.expected_head_sha256,
                    "target_head_sha256": active.target_head_sha256,
                }
                if active is not None
                else None
            ),
            "active_initialization": (
                {
                    "init_id": active_initialization.init_id,
                    "status": active_initialization.status,
                    "stage": active_initialization.stage,
                    "lineage_id": active_initialization.lineage_id,
                    "memory_mode": active_initialization.memory_mode,
                }
                if active_initialization is not None
                else None
            ),
            "error": "; ".join(error_parts),
        }
    except (RoleGovernanceError, lineage_registry.RoleLineageError) as exc:
        return {
            "ok": False,
            "mode": "invalid",
            "integrity_state": "registry_unavailable",
            "error": str(exc),
        }


def lineage_status_data(artifact_dir: str | Path) -> dict[str, Any]:
    """Augment local status with an explicit strict-Memory root probe.

    Ordinary hooks and per-edit preflight remain registry/local-only.  This
    operator command is intentionally allowed to perform the exact remote GET
    needed to distinguish first use from a deleted machine registry while the
    deterministic strict-Memory root still exists.
    """

    data = status_data(artifact_dir)
    if (
        data.get("mode") == "off"
        or data.get("memory_mode") != "required"
        or data.get("integrity_state") != "uninitialized"
    ):
        return data
    try:
        ctx = load_context(artifact_dir)
        key = _lineage_key(ctx)
        root = _memory_probe_lineage_root(ctx, key)
    except RoleGovernanceError as exc:
        updated = dict(data)
        updated.update(
            {
                "ok": False,
                "integrity_state": "registry_unavailable",
                "error": f"strict Memory lineage-root probe failed: {exc}",
            }
        )
        return updated
    if root is None:
        return data
    updated = dict(data)
    updated.update(
        {
            "ok": False,
            "integrity_state": "migration_required",
            "lineage_root_memory_id": str(root.get("lineage_root_id") or ""),
            "error": (
                "integrity_state=migration_required; deterministic strict Memory "
                "root exists while the machine registry/local history is absent"
            ),
        }
    )
    return updated


def _print_receipt(receipt: dict[str, Any]) -> None:
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))


def _session_start(args: argparse.Namespace) -> int:
    try:
        root = find_root()
        config = load_config(root, os.environ.get("BUGATE_PROFILE"))
        policy = governance_policy(config)
        role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower() or "<unset>"
        session = os.environ.get("BUGATE_SESSION_ID", "").strip() or "<unset>"
        print(f"BUGate role governance: mode={policy['mode']} role={role} session_id={session}")
        available = [phase for phase in PHASES if role in policy["phases"][phase]["allowed_roles"]]
        print("available phases: " + (", ".join(available) if available else "none"))
        artifact = config.get("artifact_dir") or config.get("artifact_root")
        if artifact:
            data = status_data(artifact)
            print(
                f"UC={data.get('uc', '<unknown>')} "
                f"integrity={data.get('integrity_state', '<invalid>')} "
                f"lifecycle={data.get('lifecycle_state', '<invalid>')} "
                f"sequence={data.get('sequence', 0)}"
            )
        if policy["mode"] == "required" and role == "<unset>":
            print(
                "BLOCKED: required role governance is active but BUGATE_AGENT_ROLE is unset. "
                "Start a fresh role session with bin/bugate-role run.",
                file=sys.stderr,
            )
            return 2
        if policy["mode"] == "required" and policy["session_id_required"] and session == "<unset>":
            print("BLOCKED: BUGATE_SESSION_ID is required.", file=sys.stderr)
            return 2
        return 0
    except Exception as exc:
        print(f"BUGate role governance session-start BLOCKED: {exc}", file=sys.stderr)
        return 2


def _run_role(args: argparse.Namespace) -> int:
    role = args.role.strip().lower()
    if role not in BUGATE_ROLES:
        print(f"invalid --role: {role}", file=sys.stderr)
        return 2
    current_role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
    if current_role and current_role != role:
        print(
            f"refusing to replace active BUGATE_AGENT_ROLE={current_role!r} with {role!r}",
            file=sys.stderr,
        )
        return 2
    session = args.session_id or str(uuid.uuid4())
    current_session = os.environ.get("BUGATE_SESSION_ID", "").strip()
    if args.session_id and current_session and current_session != args.session_id:
        print("--session-id conflicts with BUGATE_SESSION_ID", file=sys.stderr)
        return 2
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("bugate-role run requires a command after --", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["BUGATE_AGENT_ROLE"] = role
    env["BUGATE_SESSION_ID"] = session
    runtime_name = Path(command[0]).name.lower()
    if runtime_name.startswith("codex"):
        env["BUGATE_AGENT_RUNTIME"] = "codex"
    elif runtime_name.startswith("claude"):
        env["BUGATE_AGENT_RUNTIME"] = "claude"
    else:
        env["BUGATE_AGENT_RUNTIME"] = "unknown"
    print(
        f"BUGate role session: role={role} session_id={session} command={Path(command[0]).name}",
        file=sys.stderr,
    )
    try:
        return subprocess.run(command, env=env, check=False).returncode
    except OSError as exc:
        print(f"failed to start role command: {exc}", file=sys.stderr)
        return 127


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command_name", required=True)
    p = sub.add_parser("status")
    p.add_argument("artifact_dir")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("lineage-status")
    p.add_argument("artifact_dir")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("lineage-init")
    p.add_argument("artifact_dir")
    p.add_argument("--lineage-id", required=True)
    p = sub.add_parser("lineage-adopt")
    p.add_argument("artifact_dir")
    p.add_argument("--lineage-id", required=True)
    p.add_argument("--expected-head", required=True)
    p = sub.add_parser("recover")
    p.add_argument("artifact_dir")
    p.add_argument("--lineage-id", required=True)
    p.add_argument("--expected-head", required=True)
    p.add_argument("--archive")
    p = sub.add_parser("verify")
    p.add_argument("artifact_dir")
    p.add_argument("--phase", choices=PHASES)
    p.add_argument("--strict-memory", action="store_true")
    p = sub.add_parser("approve")
    p.add_argument("artifact_dir")
    p.add_argument("--approved-by", required=True)
    p.add_argument("--role")
    p.add_argument("--session-id")
    p = sub.add_parser("handoff")
    p.add_argument("artifact_dir")
    p.add_argument("--phase", choices=PHASES, required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--implementation-file", action="append", default=[])
    p.add_argument("--role")
    p.add_argument("--session-id")
    p = sub.add_parser("accept")
    p.add_argument("artifact_dir")
    p.add_argument("--phase", choices=PHASES, required=True)
    p.add_argument("--handoff-id", required=True)
    p.add_argument("--role")
    p.add_argument("--session-id")
    p = sub.add_parser("complete")
    p.add_argument("artifact_dir")
    p.add_argument("--phase", choices=PHASES, required=True)
    p.add_argument("--run-command", required=True)
    p.add_argument("--exit-code", type=int, required=True)
    p.add_argument("--evidence-file", action="append", default=[])
    p.add_argument("--gate-status", choices=("passed", "failed"), required=True)
    p.add_argument("--role")
    p.add_argument("--session-id")
    sub.add_parser("session-start")
    p = sub.add_parser("run")
    p.add_argument("--role", required=True)
    p.add_argument("--session-id")
    p.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command_name == "session-start":
            return _session_start(args)
        if args.command_name == "run":
            return _run_role(args)
        if args.command_name in {"status", "lineage-status"}:
            data = (
                lineage_status_data(args.artifact_dir)
                if args.command_name == "lineage-status"
                else status_data(args.artifact_dir)
            )
            if args.json:
                print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
            else:
                print(
                    f"role governance mode={data.get('mode')} UC={data.get('uc', '<unknown>')} "
                    f"integrity={data.get('integrity_state', '<invalid>')} "
                    f"lifecycle={data.get('lifecycle_state', '<invalid>')} "
                    f"sequence={data.get('sequence', 0)}"
                )
                if data.get("error"):
                    print(f"ERROR: {data['error']}", file=sys.stderr)
            return 0 if data.get("ok") else 2
        if args.command_name == "lineage-init":
            _print_receipt(
                lineage_init(
                    args.artifact_dir,
                    lineage_id=args.lineage_id,
                )
            )
            return 0
        if args.command_name == "lineage-adopt":
            _print_receipt(
                lineage_adopt(
                    args.artifact_dir,
                    lineage_id=args.lineage_id,
                    expected_head=args.expected_head,
                )
            )
            return 0
        if args.command_name == "recover":
            _print_receipt(
                recover(
                    args.artifact_dir,
                    lineage_id=args.lineage_id,
                    expected_head=args.expected_head,
                    archive=args.archive,
                )
            )
            return 0
        if args.command_name == "verify":
            verify_evidence(
                args.artifact_dir, phase=args.phase, strict_memory=args.strict_memory
            )
            print("PASS: role evidence is valid")
            return 0
        if args.command_name == "approve":
            receipt = approve(
                args.artifact_dir,
                approved_by=args.approved_by,
                role=args.role,
                session_id=args.session_id,
            )
        elif args.command_name == "handoff":
            receipt = handoff(
                args.artifact_dir,
                phase=args.phase,
                to_role=args.to,
                implementation_files=args.implementation_file,
                role=args.role,
                session_id=args.session_id,
            )
        elif args.command_name == "accept":
            receipt = accept(
                args.artifact_dir,
                phase=args.phase,
                handoff_id=args.handoff_id,
                role=args.role,
                session_id=args.session_id,
            )
        elif args.command_name == "complete":
            receipt = complete(
                args.artifact_dir,
                phase=args.phase,
                run_command=args.run_command,
                exit_code=args.exit_code,
                evidence_files=args.evidence_file,
                final_gate_status=args.gate_status,
                role=args.role,
                session_id=args.session_id,
            )
        else:
            raise RoleGovernanceError(f"unsupported command: {args.command_name}")
        _print_receipt(receipt)
        return 0
    except (RoleGovernanceError, lineage_registry.RoleLineageError) as exc:
        print(f"BUGate role governance BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
