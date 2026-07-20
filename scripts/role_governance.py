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
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

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


ROLE_SCHEMA = "bugate.role-evidence/v1"
CHAIN_SCHEMA = "bugate.role-chain/v1"
TRANSITION_SCHEMA = "bugate.role-transition/v1"
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
INITIAL_STATE = "awaiting_human_acceptance"
POSTRUN_NAMES = {"04_execution_report.md", "05_knowledge_update.md"}
PRECODE_PREFIX_RE = re.compile(r"^(?:01|02|03)(?:[ab])?[_-]", re.I)


class RoleGovernanceError(RuntimeError):
    """A fail-closed configuration, identity, transition, or evidence error."""


class RoleConfigError(RoleGovernanceError):
    """The role_governance contract is malformed."""


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def receipt_sha256(receipt: dict[str, Any]) -> str:
    payload = copy.deepcopy(receipt)
    payload.pop("receipt_sha256", None)
    return sha256_bytes(canonical_json(payload))


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
        if path.resolve() != artifact_dir.resolve():
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
    root = root.resolve()
    artifact = artifact if artifact.is_absolute() else root / artifact
    artifact = artifact.resolve()
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
    return {"path": snap["path"], "sha256": snap["sha256"]}


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


def _atomic_json(path: Path, data: dict[str, Any], *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.exists() and not replace:
        if path.read_bytes() == body:
            return
        raise RoleGovernanceError(f"append-only role receipt already exists: {path}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()


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
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleGovernanceError(f"invalid JSON evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RoleGovernanceError(f"JSON evidence must be an object: {path}")
    return value


def load_chain(ctx: GovernanceContext) -> dict[str, Any]:
    path = _chain_path(ctx)
    if not path.exists():
        if _receipt_dir(ctx).exists() and any(_receipt_dir(ctx).glob("*.json")):
            raise RoleGovernanceError("role receipts exist without chain.json")
        return _empty_chain()
    return _read_json(path)


_TRANSITION_OPTIONAL_FIELDS = (
    "approved_by",
    "decision",
    "handoff_receipt_sha256",
    "handoff_memory_id",
    "accepted_handoff_receipt_sha256",
    "implementation_files",
    "run",
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
    event = receipt.get("event")
    if event not in EVENT_STATES:
        raise RoleGovernanceError(f"unknown role receipt event in {path.name}: {event!r}")
    if receipt.get("phase") not in PHASES:
        raise RoleGovernanceError(f"invalid receipt phase in {path.name}")
    actor = receipt.get("actor")
    if not isinstance(actor, dict) or set(actor) != {"role", "runtime", "session_id"}:
        raise RoleGovernanceError(f"invalid receipt actor schema in {path.name}")
    if actor.get("role") not in LIFECYCLE_ROLES:
        raise RoleGovernanceError(f"invalid lifecycle actor in {path.name}")
    profile = receipt.get("profile")
    if not isinstance(profile, dict) or set(profile) != {"path", "sha256"}:
        raise RoleGovernanceError(f"invalid receipt profile schema in {path.name}")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or artifacts != sorted(
        artifacts, key=lambda item: str(item.get("path") or "") if isinstance(item, dict) else ""
    ):
        raise RoleGovernanceError(f"receipt artifacts must be a path-sorted list: {path.name}")
    transition = _transition_from_receipt(receipt)
    transition_hash = sha256_bytes(canonical_json(transition))
    if receipt.get("transition_sha256") != transition_hash:
        raise RoleGovernanceError(f"transition hash mismatch: {path.name}")
    state = receipt.get("resulting_state")
    allowed_states = {EVENT_STATES[event]}
    if event == "reviewer_completion":
        allowed_states.add("post_run_active")
    if state not in allowed_states:
        raise RoleGovernanceError(f"invalid resulting state for {event}: {state!r}")


def verify_chain(ctx: GovernanceContext) -> list[dict[str, Any]]:
    chain = load_chain(ctx)
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
    for expected, path in enumerate(paths, 1):
        receipt = _read_json(path)
        _validate_receipt_contract(receipt, path)
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
        latest[event] = workspace_rel(path, ctx.root)
        previous = actual
        receipts.append(receipt)
    if chain["head_sha256"] != previous:
        raise RoleGovernanceError("chain head hash does not match the latest receipt")
    if chain["latest_receipts"] != latest:
        raise RoleGovernanceError("chain latest_receipts does not match receipt history")
    expected_state = (
        str(receipts[-1].get("resulting_state") or EVENT_STATES.get(receipts[-1]["event"], INITIAL_STATE))
        if receipts
        else INITIAL_STATE
    )
    if chain["state"] != expected_state:
        raise RoleGovernanceError("chain state does not match the latest transition")
    return receipts


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


def _publish(ctx: GovernanceContext, base: dict[str, Any], state: str) -> dict[str, Any]:
    receipts = verify_chain(ctx)
    event = str(base["event"])
    idem = _idempotency_payload(base)
    prior = _latest(receipts, event)
    if prior and prior.get("idempotency_sha256") == idem:
        return prior
    chain = load_chain(ctx)
    transition = {
        "schema": TRANSITION_SCHEMA,
        "event": event,
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
        "previous_receipt_sha256": chain["head_sha256"],
        "idempotency_sha256": idem,
    }
    for key in (
        "approved_by",
        "decision",
        "handoff_receipt_sha256",
        "handoff_memory_id",
        "accepted_handoff_receipt_sha256",
        "implementation_files",
        "run",
    ):
        if key in base:
            transition[key] = base[key]
    transition_hash = sha256_bytes(canonical_json(transition))
    prepared = _memory_prepare(ctx, {**transition, "transition_sha256": transition_hash})
    memory_public = {k: v for k, v in prepared.items() if not k.startswith("_")}
    receipt = {
        "schema": ROLE_SCHEMA,
        "event": event,
        "sequence": chain["sequence"] + 1,
        "uc": ctx.uc,
        "artifact_dir": workspace_rel(ctx.artifact_dir, ctx.root),
        "phase": base["phase"],
        "from_role": base.get("from_role", ""),
        "to_role": base.get("to_role", ""),
        "actor": base["actor"],
        "created_at": utc_now(),
        "profile": base["profile"],
        "artifacts": base.get("artifacts", []),
        "dispatch": base.get("dispatch", {}),
        "human_acceptance": base.get("human_acceptance", {}),
        "previous_receipt_sha256": chain["head_sha256"],
        "transition_sha256": transition_hash,
        "idempotency_sha256": idem,
        "memory": memory_public,
        "resulting_state": state,
    }
    for key in (
        "approved_by",
        "decision",
        "handoff_receipt_sha256",
        "handoff_memory_id",
        "accepted_handoff_receipt_sha256",
        "implementation_files",
        "run",
    ):
        if key in base:
            receipt[key] = base[key]
    receipt["receipt_sha256"] = receipt_sha256(receipt)
    _memory_finalize(
        ctx,
        prepared,
        receipt["receipt_sha256"],
        {**transition, "transition_sha256": transition_hash},
    )
    filename = (
        f"{receipt['sequence']:06d}-{event.replace('_', '-')}-"
        f"{receipt['receipt_sha256']}.json"
    )
    receipt_path = _receipt_dir(ctx) / filename
    _atomic_json(receipt_path, receipt, replace=False)
    latest = dict(chain["latest_receipts"])
    latest[event] = workspace_rel(receipt_path, ctx.root)
    new_chain = {
        "schema": CHAIN_SCHEMA,
        "state": state,
        "sequence": receipt["sequence"],
        "head_sha256": receipt["receipt_sha256"],
        "latest_receipts": latest,
    }
    _atomic_json(_chain_path(ctx), new_chain, replace=True)
    return receipt


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
    return [re.compile(str(value)) for value in values]


def _guard_match(ctx: GovernanceContext, path: Path) -> re.Match[str] | None:
    rel = workspace_rel(path, ctx.root)
    absolute = path.resolve().as_posix()
    for regex in _compiled_guarded(ctx):
        match = regex.search(rel) or regex.search(absolute)
        if match:
            return match
    return None


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


def accept(
    artifact_dir: str | Path,
    *,
    phase: str,
    handoff_id: str,
    role: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    ctx = load_context(artifact_dir)
    if phase not in {"implementation", "post_run"}:
        raise RoleGovernanceError("accept --phase must be implementation or post_run")
    actor = _actor(ctx, phase, role=role, session_id=session_id)
    receipts = verify_chain(ctx)
    source_roles = ctx.policy["phases"][phase]["requires_handoff_from"]
    handoff_receipt = _find_handoff(receipts, handoff_id)
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
    if existing:
        prior = existing[-1]
        if prior.get("actor") == actor:
            return prior
        raise RoleGovernanceError("this handoff was already accepted by a different session")
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
        raise RoleGovernanceError("active profile hash/path drifted since role transition")
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
    if phase in (None, "pre_code"):
        if strict_memory:
            for receipt in receipts:
                _memory_verify(ctx, receipt)
        return receipts
    if phase == "implementation":
        acceptance = _latest(receipts, "implementer_acceptance")
        if not acceptance:
            raise RoleGovernanceError("implementation is locked: implementer acceptance missing")
        _verify_acceptance_session(ctx, acceptance, phase)
        handoff_receipt = _find_handoff(receipts, acceptance["handoff_receipt_sha256"])
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
        _verify_acceptance_session(ctx, acceptance, phase)
        handoff_receipt = _find_handoff(receipts, acceptance["handoff_receipt_sha256"])
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
        chain = load_chain(ctx)
        errors: list[str] = []
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
        try:
            verify_chain(ctx)
            if require_acceptance and phase in {"implementation", "post_run"}:
                verify_evidence(ctx.artifact_dir, phase=phase)
        except RoleGovernanceError as exc:
            errors.append(str(exc))
        if errors and ctx.mode == "required":
            return GovernanceResult(False, ctx.mode, phase, chain["state"], errors=errors)
        return GovernanceResult(
            True,
            ctx.mode,
            phase,
            chain["state"],
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
    actor = _actor(ctx, phase, role=role, session_id=session_id)
    verify_evidence(ctx.artifact_dir, phase="post_run")
    if not run_command.strip():
        raise RoleGovernanceError("--run-command summary is required")
    if final_gate_status not in {"passed", "failed"}:
        raise RoleGovernanceError("--gate-status must be passed or failed")
    report_items = [_snapshot(ctx.artifact_dir / name, ctx, with_gate=True) for name in sorted(POSTRUN_NAMES)]
    evidence = [_snapshot((Path(p) if Path(p).is_absolute() else ctx.root / Path(p)), ctx) for p in evidence_files]
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
    return _publish(ctx, base, state)


def status_data(artifact_dir: str | Path) -> dict[str, Any]:
    try:
        ctx = load_context(artifact_dir)
        chain = load_chain(ctx)
        error = ""
        try:
            receipts = verify_chain(ctx) if ctx.mode != "off" else []
        except RoleGovernanceError as exc:
            receipts = []
            error = str(exc)
        return {
            "ok": not error,
            "mode": ctx.mode,
            "memory_mode": ctx.policy["memory_mode"],
            "role": os.environ.get("BUGATE_AGENT_ROLE", ""),
            "session_id": os.environ.get("BUGATE_SESSION_ID", ""),
            "uc": ctx.uc,
            "artifact_dir": workspace_rel(ctx.artifact_dir, ctx.root),
            "state": chain["state"],
            "sequence": chain["sequence"],
            "head_sha256": chain["head_sha256"],
            "latest_receipts": chain["latest_receipts"],
            "receipt_count": len(receipts),
            "error": error,
        }
    except RoleGovernanceError as exc:
        return {"ok": False, "mode": "invalid", "error": str(exc)}


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
                f"UC={data.get('uc', '<unknown>')} state={data.get('state', '<invalid>')} "
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
        if args.command_name == "status":
            data = status_data(args.artifact_dir)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
            else:
                print(
                    f"role governance mode={data.get('mode')} UC={data.get('uc', '<unknown>')} "
                    f"state={data.get('state', '<invalid>')} sequence={data.get('sequence', 0)}"
                )
                if data.get("error"):
                    print(f"ERROR: {data['error']}", file=sys.stderr)
            return 0 if data.get("ok") else 2
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
    except RoleGovernanceError as exc:
        print(f"BUGate role governance BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
