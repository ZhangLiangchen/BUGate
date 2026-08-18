#!/usr/bin/env python3
"""Append-only self-healing evidence sidecar, anchored to the role chain.

Why a sidecar instead of new events on ``00_role_evidence/chain.json``
---------------------------------------------------------------------
Two frozen engine invariants make the main chain unusable for this flow, and
neither may be relaxed:

1. ``role_governance.governance_policy`` raises ``RoleConfigError`` whenever the
   configured ``phases`` differ from ``DEFAULT_PHASES``, and
   ``DEFAULT_PHASES["post_run"]["allowed_roles"]`` is exactly ``["reviewer"]``.
   ``_actor`` rejects any role outside that list.  A healer acting as
   *implementer* during post-run therefore can never be a legal main-chain
   actor; publishing self-heal events there would require unfreezing the v0.4.2
   canonical phase-ownership hardening.
2. ``_validate_event_receipt_contract`` raises on any event outside
   ``EVENT_STATES``, and ``preflight`` re-verifies the whole chain on every
   governed write.  A single self-heal event written into a UC's main chain
   would make every v0.4.0--v0.4.4 engine fail that chain outright and block all
   later governed writes -- which directly collides with the updater's
   guaranteed rollback-to-prior-image contract.

So this module keeps its own append-only chain under
``<artifact_dir>/00_self_healing/`` and *binds* to the main chain by a read-only
anchor.  It never writes a byte under ``00_role_evidence/``.  An older engine
reading such a UC sees a directory it does not know about and behaves exactly as
it did before -- proved by
``tests/test_self_healing_governance.py`` (contract section 2.4).

The anchor is what keeps the two honest: every sidecar receipt records the main
chain's hash, sequence and lifecycle state at publication time.  If the main
chain moves -- a new lifecycle event, a completion, a lineage restore -- the in-flight
attempt is invalidated rather than silently continuing against stale evidence.
"""

from __future__ import annotations

import copy
import fcntl
import functools
import json
import math
import os
import re
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from role_governance import (
    CHAIN_SCHEMA as ROLE_CHAIN_SCHEMA,
    EVENT_STATES as ROLE_EVENT_STATES,
    INITIAL_STATE as ROLE_INITIAL_STATE,
    LIFECYCLE_ROLES,
    RECOVERY_EVENT as ROLE_RECOVERY_EVENT,
    GovernanceContext,
    RoleGovernanceError,
    _accepted_latest_handoff,
    _atomic_bytes,
    _fsync_directory,
    _json_bytes,
    _latest,
    _lineage_integrity,
    _local_evidence_structure_error,
    _memory_finalize,
    _memory_namespace,
    _memory_prepare,
    _memory_verify,
    _verify_closed_completion,
    _verify_snapshot,
    canonical_json,
    lineage_identity,
    load_chain,
    sha256_bytes,
    utc_now,
    verify_chain,
    workspace_rel,
)
from self_heal_policy import SIDECAR_DIR


EVIDENCE_SCHEMA = "bugate.self-heal-evidence/v1"
CHAIN_SCHEMA = "bugate.self-heal-chain/v1"
TRANSITION_SCHEMA = "bugate.self-heal-transition/v1"
ROLE_TRANSITION_SCHEMA = "bugate.role-transition/v1"

#: The lifecycle state the main chain must be in for any attempt to be valid.
REQUIRED_LIFECYCLE_STATE = "post_run_active"

STATE_NONE = ""
STATE_TRIAGE_RECORDED = "triage_recorded"
STATE_AWAITING_HEALER = "awaiting_healer_acceptance"
STATE_HEALING_ACTIVE = "healing_active"
STATE_AWAITING_REVIEW = "awaiting_independent_review"
STATE_VERIFIED = "healing_verified"
STATE_REJECTED = "healing_rejected"
STATE_CLOSED = "attempt_closed"
STATE_INVALIDATED = "attempt_invalidated_by_lifecycle_drift"

#: States from which a fresh attempt may be opened.
TERMINAL_STATES = (STATE_CLOSED, STATE_INVALIDATED)

EVENT_TRIAGE = "triage_recorded"
EVENT_HANDOFF = "self_heal_handoff"
EVENT_HEALER_ACCEPT = "healer_acceptance"
EVENT_HEALER_HANDOFF = "healer_handoff"
EVENT_REVIEW = "independent_review"
EVENT_CLOSED = "attempt_closed"

#: event -> (actor role, allowed prior states, resulting states)
EVENT_CONTRACT: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    EVENT_TRIAGE: ("reviewer", (STATE_NONE, *TERMINAL_STATES), (STATE_TRIAGE_RECORDED,)),
    EVENT_HANDOFF: ("reviewer", (STATE_TRIAGE_RECORDED,), (STATE_AWAITING_HEALER,)),
    EVENT_HEALER_ACCEPT: ("implementer", (STATE_AWAITING_HEALER,), (STATE_HEALING_ACTIVE,)),
    EVENT_HEALER_HANDOFF: ("implementer", (STATE_HEALING_ACTIVE,), (STATE_AWAITING_REVIEW,)),
    EVENT_REVIEW: ("reviewer", (STATE_AWAITING_REVIEW,), (STATE_VERIFIED, STATE_REJECTED)),
    EVENT_CLOSED: ("reviewer", (STATE_VERIFIED, STATE_REJECTED), (STATE_CLOSED,)),
}

#: Events that must carry a strict Memory transition under ``memory_mode: required``.
MEMORY_ANCHORED_EVENTS = (EVENT_HANDOFF, EVENT_HEALER_ACCEPT, EVENT_REVIEW)

#: Memory's generic role-transition envelope keeps the frozen sidecar transition
#: nested while recording the route that each anchored event represents.  The
#: phase value is metadata only: it does not add a fourth lifecycle phase.
MEMORY_EVENT_ROUTES = {
    EVENT_HANDOFF: ("reviewer", "implementer"),
    EVENT_HEALER_ACCEPT: ("reviewer", "implementer"),
    EVENT_REVIEW: ("implementer", "reviewer"),
}

#: Events whose session must *differ* from every session named below it.
FRESH_SESSION_EVENTS = (EVENT_HEALER_ACCEPT, EVENT_REVIEW)
#: Events whose session must *match* the event that opened their leg.
PAIRED_SESSION_EVENTS = {
    EVENT_HANDOFF: EVENT_TRIAGE,
    EVENT_HEALER_HANDOFF: EVENT_HEALER_ACCEPT,
}

_QUARANTINE_PREFIX = "unindexed_"
_QUARANTINE_NAME_RE = re.compile(
    r"^unindexed_(?P<event>"
    + "|".join(re.escape(event) for event in EVENT_CONTRACT)
    + r")_(?P<content_sha256>[0-9a-f]{64})[.]json$"
)

REASON_ROLE = "self_heal_role_not_allowed"
REASON_SESSION_REQUIRED = "self_heal_session_id_required"
REASON_SAME_SESSION = "same_session_self_approval"
REASON_SESSION_MISMATCH = "handoff_session_mismatch"
REASON_TRANSITION = "self_heal_transition_not_allowed"
REASON_INTEGRITY = "sidecar_integrity_failed"
REASON_DRIFT = "lifecycle_drift"
REASON_LIFECYCLE_STATE = "lifecycle_state_not_post_run_active"
# Keep the historical prepare value because callers already expose it as a
# stable blocking reason.  Finalize and verify are intentionally distinct.
REASON_MEMORY_PREPARE = "memory_exact_id_verification_failed"
REASON_MEMORY_FINALIZE = "memory_receipt_binding_failed"
REASON_MEMORY_VERIFY = "memory_receipt_verification_failed"
REASON_MEMORY = REASON_MEMORY_PREPARE
REASON_ATTEMPT_MISMATCH = "attempt_id_mismatch"
REASON_EVENT_PAYLOAD_INVALID = "self_heal_event_payload_invalid"
REASON_REVIEW_OUTCOME_MISMATCH = "review_outcome_state_mismatch"
REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE = "triage_healing_eligible_not_true"
REASON_CLOSE_FINAL_STATE_MISMATCH = "close_final_state_mismatch"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PAYLOAD_NOT_PROVIDED = object()


class SelfHealSidecarError(RoleGovernanceError):
    """A sidecar contract violation, carrying a stable blocking reason code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _integrity_error(message: str) -> SelfHealSidecarError:
    return SelfHealSidecarError(REASON_INTEGRITY, message)


def _validate_json_payload(value: Any, *, label: str) -> None:
    """Require an object graph that this engine can serialize and replay."""

    active: set[int] = set()

    def walk(item: Any) -> None:
        if item is None or isinstance(item, (bool, str)):
            return
        if isinstance(item, int):
            return
        if isinstance(item, float):
            if math.isfinite(item):
                return
            raise ValueError("non-finite floating-point value")
        if isinstance(item, list):
            identity = id(item)
            if identity in active:
                raise ValueError("cyclic list")
            active.add(identity)
            try:
                for child in item:
                    walk(child)
            finally:
                active.remove(identity)
            return
        if isinstance(item, dict):
            identity = id(item)
            if identity in active:
                raise ValueError("cyclic object")
            active.add(identity)
            try:
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise TypeError("JSON object key is not a string")
                    walk(child)
            finally:
                active.remove(identity)
            return
        raise TypeError(f"unsupported JSON value type {type(item).__name__}")

    try:
        walk(value)
        # This also applies the interpreter's configured integer-string limit,
        # proving that bytes accepted now remain parseable during later replay.
        json.loads(canonical_json(value).decode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise SelfHealSidecarError(
            REASON_EVENT_PAYLOAD_INVALID,
            f"{label} payload is not replayable canonical JSON: {exc}",
        ) from exc


def _public_memory_binding(
    value: Any,
    *,
    reason: str,
    label: str,
) -> dict[str, Any]:
    """Validate the untrusted Memory-adapter mapping before publication."""

    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise SelfHealSidecarError(reason, f"{label} must be a string-keyed object")
    public = {key: item for key, item in value.items() if not key.startswith("_")}
    try:
        _validate_json_payload(public, label=label)
    except SelfHealSidecarError as exc:
        raise SelfHealSidecarError(reason, str(exc)) from exc
    return public


def _validate_review_outcome(
    event: str,
    payload: Any,
    resulting_state: str,
    *,
    label: str,
) -> None:
    """Bind the reviewer's public outcome to the state-machine transition."""

    if event != EVENT_REVIEW:
        return
    outcome = payload.get("outcome") if isinstance(payload, dict) else None
    if outcome != resulting_state:
        raise SelfHealSidecarError(
            REASON_REVIEW_OUTCOME_MISMATCH,
            f"{label} payload.outcome {outcome!r} does not match "
            f"resulting_state {resulting_state!r}",
        )


def _validate_triage_eligibility(event: str, payload: Any, *, label: str) -> None:
    """Require every attempt-opening triage to be explicitly repair-eligible."""

    if event != EVENT_TRIAGE:
        return
    healing_eligible = (
        payload.get("healing_eligible") if isinstance(payload, dict) else None
    )
    if healing_eligible is not True:
        raise SelfHealSidecarError(
            REASON_TRIAGE_HEALING_ELIGIBLE_NOT_TRUE,
            f"{label} payload.healing_eligible must be exactly True; "
            f"observed {healing_eligible!r}",
        )


def _validate_close_final_state(
    event: str,
    payload: Any,
    prior_state: str | None,
    *,
    label: str,
) -> None:
    """Bind the close evidence to the authenticated review outcome.

    ``attempt_closed`` deliberately collapses the state machine to one terminal
    state.  The payload is therefore the only retained declaration of whether
    the authenticated predecessor was verified or rejected; it must not be
    allowed to contradict that predecessor during publication or replay.
    """

    if event != EVENT_CLOSED:
        return
    final_state = payload.get("final_state") if isinstance(payload, dict) else None
    if (
        not isinstance(final_state, str)
        or final_state not in {STATE_VERIFIED, STATE_REJECTED}
        or (prior_state is not None and final_state != prior_state)
    ):
        raise SelfHealSidecarError(
            REASON_CLOSE_FINAL_STATE_MISMATCH,
            f"{label} payload.final_state {final_state!r} does not match "
            f"authenticated prior_state {prior_state!r}",
        )


def _validate_event_payload(
    event: str,
    payload: Any,
    resulting_state: str,
    *,
    prior_state: str | None = None,
    label: str,
) -> None:
    """Validate event-specific payload invariants shared by publish and replay."""

    if not isinstance(payload, dict):
        raise SelfHealSidecarError(
            REASON_EVENT_PAYLOAD_INVALID,
            f"{label} payload must be a JSON object",
        )
    _validate_json_payload(payload, label=label)
    _validate_triage_eligibility(event, payload, label=label)
    _validate_review_outcome(event, payload, resulting_state, label=label)
    _validate_close_final_state(event, payload, prior_state, label=label)


def _validate_directory_chain(
    ctx: GovernanceContext,
    target: Path,
    *,
    label: str,
    allow_missing: bool,
) -> Path:
    """Prove that a governance-owned directory path cannot traverse a link.

    ``Path.resolve`` alone is insufficient here: a symlink that happens to stay
    inside the project would pass containment while still making a later path
    swap invisible.  Walk every existing component from the configured project
    root with ``lstat`` first, require directories, and then use the resolved
    paths as a second containment check.  Missing suffixes are permitted only
    for paths the engine is about to create.
    """

    root = Path(os.path.abspath(ctx.root))
    candidate = Path(os.path.abspath(target))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise _integrity_error(
            f"{label} escapes the BUGate project root: {candidate}"
        ) from exc

    components = [root]
    current = root
    for part in relative.parts:
        current = current / part
        components.append(current)

    missing_seen = False
    for component in components:
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            missing_seen = True
            continue
        except OSError as exc:
            raise _integrity_error(f"cannot inspect {label} parent {component}: {exc}") from exc
        if missing_seen:
            raise _integrity_error(
                f"{label} has an existing child below a missing parent: {component}"
            )
        if stat.S_ISLNK(metadata.st_mode):
            raise _integrity_error(f"{label} must not traverse symlink parent {component}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise _integrity_error(f"{label} parent is not a directory: {component}")

    if missing_seen and not allow_missing:
        raise _integrity_error(f"{label} directory is missing: {candidate}")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise _integrity_error(
            f"{label} resolved path escapes the BUGate project root: {candidate}"
        ) from exc
    return candidate


def _validate_sidecar_directory(ctx: GovernanceContext, target: Path) -> Path:
    artifact = _validate_directory_chain(
        ctx,
        ctx.artifact_dir,
        label="self-heal artifact storage",
        allow_missing=False,
    )
    candidate = Path(os.path.abspath(target))
    try:
        candidate.relative_to(artifact)
    except ValueError as exc:
        raise _integrity_error(
            f"self-heal sidecar storage escapes its artifact directory: {candidate}"
        ) from exc
    validated = _validate_directory_chain(
        ctx,
        candidate,
        label="self-heal sidecar storage",
        allow_missing=True,
    )
    try:
        validated.resolve(strict=False).relative_to(artifact.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise _integrity_error(
            f"self-heal sidecar storage resolved outside its artifact directory: {candidate}"
        ) from exc
    return validated


def _self_heal_actor(ctx: GovernanceContext, expected_role: str) -> dict[str, str]:
    """Identity check for a sidecar event -- deliberately *not* phase-based.

    ``role_governance._actor`` resolves the allowed roles from
    ``ctx.policy["phases"][phase]["allowed_roles"]``, and post-run is frozen to
    ``["reviewer"]``.  The healer leg needs an *implementer* actor inside the
    same post-run window, so this checker validates against the sidecar's own
    role table instead.  Everything else -- the environment-variable contract,
    the session requirement, the runtime normalization -- matches ``_actor`` so
    the two identities are interchangeable everywhere else.
    """

    env_role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
    env_session = os.environ.get("BUGATE_SESSION_ID", "").strip()
    if not env_role:
        raise SelfHealSidecarError(
            REASON_ROLE, "BUGATE_AGENT_ROLE is required for a self-heal transition"
        )
    if env_role not in LIFECYCLE_ROLES:
        raise SelfHealSidecarError(
            REASON_ROLE,
            f"self-heal roles are limited to {sorted(LIFECYCLE_ROLES)}; got {env_role!r}",
        )
    if env_role != expected_role:
        raise SelfHealSidecarError(
            REASON_ROLE,
            f"role {env_role!r} cannot publish this self-heal event; expected {expected_role!r}",
        )
    if ctx.policy["session_id_required"] and not env_session:
        raise SelfHealSidecarError(
            REASON_SESSION_REQUIRED, "BUGATE_SESSION_ID is required for this profile"
        )
    runtime = os.environ.get("BUGATE_AGENT_RUNTIME", "").strip().lower()
    if runtime not in {"codex", "claude"}:
        runtime = "unknown"
    return {"role": env_role, "runtime": runtime, "session_id": env_session}


def self_heal_preflight(ctx: GovernanceContext) -> list[str]:
    """Evidence integrity for a sidecar step, minus the post-run session binding.

    This mirrors ``role_governance.verify_evidence(phase="post_run")`` check for
    check -- valid chain, aligned lineage, reviewer acceptance present, chain in
    ``post_run_active``, no closed completion, accepted implementer handoff with
    an un-drifted profile/artifact snapshot -- with exactly one omission:
    ``_verify_acceptance_session``.

    That helper binds every post-run write to *the* reviewer session that
    published ``reviewer_acceptance``.  The frozen sidecar contract requires the
    opposite for the independent review: a reviewer in a session that is
    provably **not** the one that opened the attempt.  Both rules cannot hold at
    once, so the sidecar keeps the evidence half and replaces the identity half
    with a stricter control of its own -- ``_self_heal_actor`` plus the
    session-*distinctness* rules in :meth:`SelfHealSidecar._check_sessions`.

    Nothing here relaxes the main chain: post-run *artifact* writes still go
    through ``governed_write_preflight`` and therefore still require the
    accepting reviewer session.  Only sidecar receipts use this path.
    """

    if ctx.mode == "off":
        return ["role_governance is off; self-healing requires the auditable lifecycle"]
    try:
        integrity = _lineage_integrity(ctx)
        if integrity.integrity_state != "aligned":
            detail = integrity.local_error or integrity.registry_error
            return [
                f"integrity_state={integrity.integrity_state}" + (f": {detail}" if detail else "")
            ]
        receipts = verify_chain(ctx)
        acceptance = _latest(receipts, "reviewer_acceptance")
        if not acceptance:
            return ["post-run is locked: reviewer acceptance missing"]
        chain_state = str(load_chain(ctx).get("state") or "")
        if chain_state != REQUIRED_LIFECYCLE_STATE:
            return [
                f"post-run is locked in chain state {chain_state!r}; "
                "a current reviewer acceptance is required"
            ]
        _verify_closed_completion(ctx, receipts)
        handoff = _accepted_latest_handoff(
            receipts, acceptance, phase="post_run", event="implementer_handoff"
        )
        _verify_snapshot(ctx, handoff)
        if not handoff.get("implementation_files"):
            return ["implementer handoff has no implementation snapshot"]
    except RoleGovernanceError as exc:
        return [str(exc)]
    return []


def sidecar_dir(ctx: GovernanceContext) -> Path:
    return ctx.artifact_dir / SIDECAR_DIR


def attempts_dir(ctx: GovernanceContext, attempt_id: str) -> Path:
    if not _SAFE_TOKEN_RE.fullmatch(attempt_id or ""):
        raise SelfHealSidecarError(
            REASON_ATTEMPT_MISMATCH,
            f"invalid self-heal attempt id: {attempt_id!r}",
        )
    return _validate_sidecar_directory(
        ctx,
        sidecar_dir(ctx) / "attempts" / attempt_id,
    )


def _validate_role_chain_envelope(
    ctx: GovernanceContext,
    chain: Any,
) -> dict[str, Any]:
    """Validate the frozen v1 main-chain envelope without following its paths.

    The sidecar anchor is intentionally read-only and does not replace the main
    governance verifier.  It nevertheless must reject an object that could not
    have been emitted by ``role_governance``.  These checks mirror that
    verifier's exact five-key/schema/state/index constraints and its canonical
    receipt filename convention, using the same exported lifecycle constants.
    The anchor reader runs the complete main-chain verifier after this envelope
    check.  Keeping the cheap shape check here gives malformed input the stable
    lifecycle-drift reason, while ``verify_chain`` proves receipt inventory,
    hashes, state transitions and the full ``latest_receipts`` history before
    any observed anchor may be persisted.
    """

    required_keys = {
        "schema",
        "state",
        "sequence",
        "head_sha256",
        "latest_receipts",
    }
    if not isinstance(chain, dict) or set(chain) != required_keys:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "role chain has missing or non-minimal v1 envelope fields",
        )
    if chain.get("schema") != ROLE_CHAIN_SCHEMA:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            f"role chain schema must be {ROLE_CHAIN_SCHEMA}",
        )

    sequence = chain.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "role chain sequence must be a non-negative integer",
        )
    state = chain.get("state")
    valid_states = {ROLE_INITIAL_STATE, *ROLE_EVENT_STATES.values()}
    if not isinstance(state, str) or state not in valid_states:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            f"role chain lifecycle state is invalid: {state!r}",
        )
    head = chain.get("head_sha256")
    latest = chain.get("latest_receipts")
    if not isinstance(head, str) or not isinstance(latest, dict):
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "role chain head/latest_receipts values are invalid",
        )
    if sequence == 0:
        if head != "" or latest != {} or state != ROLE_INITIAL_STATE:
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "empty role chain envelope is internally inconsistent",
            )
        return chain
    if not re.fullmatch(r"[0-9a-f]{64}", head) or not latest:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "non-empty role chain requires a 64-hex head and latest receipt index",
        )

    allowed_events = {*ROLE_EVENT_STATES, ROLE_RECOVERY_EVENT}
    expected_receipt_parent = Path(
        workspace_rel(ctx.evidence_dir / "receipts", ctx.root)
    )
    indexed: dict[str, tuple[int, str]] = {}
    seen_sequences: set[int] = set()
    for event, receipt_name in latest.items():
        if event not in allowed_events or not isinstance(receipt_name, str):
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "role chain latest_receipts contains an unknown event or invalid path",
            )
        receipt_path = Path(receipt_name)
        if (
            not receipt_name
            or "\\" in receipt_name
            or receipt_path.is_absolute()
            or receipt_path.parent != expected_receipt_parent
        ):
            raise SelfHealSidecarError(
                REASON_DRIFT,
                f"role chain latest receipt path is invalid for {event!r}",
            )
        match = re.fullmatch(
            rf"(?P<sequence>[0-9]{{6}})-{re.escape(event.replace('_', '-'))}-"
            r"(?P<receipt_sha256>[0-9a-f]{64})[.]json",
            receipt_path.name,
        )
        if match is None:
            raise SelfHealSidecarError(
                REASON_DRIFT,
                f"role chain latest receipt filename is invalid for {event!r}",
            )
        receipt_sequence = int(match.group("sequence"))
        if (
            receipt_sequence < 1
            or receipt_sequence > sequence
            or receipt_sequence in seen_sequences
        ):
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "role chain latest receipt sequences are invalid",
            )
        seen_sequences.add(receipt_sequence)
        indexed[str(event)] = (receipt_sequence, match.group("receipt_sha256"))

    last_event, (last_sequence, last_hash) = max(
        indexed.items(), key=lambda item: item[1][0]
    )
    if last_sequence != sequence or last_hash != head:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "role chain latest receipt index does not bind its sequence/head",
        )
    # A failed reviewer completion is a real, indexed main-chain event but
    # deliberately leaves the lifecycle ``post_run_active`` for another run.
    # All other ordinary events have one canonical resulting state.
    if (
        last_event == "reviewer_completion"
        and state not in {REQUIRED_LIFECYCLE_STATE, "closed"}
    ) or (
        last_event not in {ROLE_RECOVERY_EVENT, "reviewer_completion"}
        and ROLE_EVENT_STATES[last_event] != state
    ):
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "role chain state does not match its latest lifecycle event",
        )
    state_event = next(
        (event for event, resulting_state in ROLE_EVENT_STATES.items() if resulting_state == state),
        None,
    )
    if state_event is not None and state_event not in indexed:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            "role chain latest_receipts omits the event that established its state",
        )
    return chain


def read_role_chain_anchor(ctx: GovernanceContext) -> dict[str, Any]:
    """Read-only snapshot of the governed role chain; writes nothing.

    After complete main-chain verification, ``chain_sha256`` carries the
    authenticated ``head_sha256``.  The separately bound ``sequence`` and
    ``lifecycle_state`` complete the frozen lifecycle anchor without making
    semantically identical JSON serialization a drift event.
    """

    structure_error = _local_evidence_structure_error(ctx)
    if structure_error:
        raise _integrity_error(structure_error)
    evidence_dir = _validate_directory_chain(
        ctx,
        ctx.evidence_dir,
        label="role-evidence anchor storage",
        allow_missing=False,
    )
    chain_path = evidence_dir / "chain.json"
    try:
        metadata = chain_path.lstat()
    except FileNotFoundError:
        return {"chain_sha256": "", "sequence": 0, "lifecycle_state": ""}
    except OSError as exc:
        raise _integrity_error(
            f"cannot inspect role-evidence chain for anchoring: {chain_path}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _integrity_error(
            "role-evidence chain for anchoring must be a non-symlink regular file: "
            f"{chain_path}"
        )
    try:
        body = chain_path.read_bytes()
    except OSError as exc:
        raise _integrity_error(
            f"cannot read role-evidence chain for anchoring: {chain_path}: {exc}"
        ) from exc
    try:
        chain = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SelfHealSidecarError(
            REASON_DRIFT, f"role chain is unreadable for anchoring: {exc}"
        ) from exc
    chain = _validate_role_chain_envelope(ctx, chain)
    try:
        verify_chain(ctx)
    except (
        RoleGovernanceError,
        TypeError,
        ValueError,
        KeyError,
        IndexError,
        RecursionError,
        OverflowError,
    ) as exc:
        raise SelfHealSidecarError(
            REASON_DRIFT,
            f"role chain is invalid for anchoring: {exc}",
        ) from exc
    sequence = chain["sequence"]
    lifecycle_state = chain["state"]
    return {
        "chain_sha256": str(chain["head_sha256"]),
        "sequence": sequence,
        "lifecycle_state": lifecycle_state,
    }


def receipt_sha256(receipt: dict[str, Any]) -> str:
    """Hash a receipt excluding its own ``receipt_sha256`` field."""

    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    try:
        return sha256_bytes(canonical_json(payload))
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise _integrity_error(
            f"self-heal receipt is not replayable canonical JSON: {exc}"
        ) from exc


def _exclusive_sidecar_operation(method: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize one complete sidecar read/modify/write operation.

    Receipt publication and the chain-index replace are two durable writes.  A
    second process must not mistake the live publisher's receipt for a crashed
    orphan in the gap between them.  The lock is taken on the already-existing
    UC artifact directory, so this control adds no file to the frozen sidecar
    layout.  ``SelfHealSidecar`` operations call each other recursively; the
    per-instance lock below is deliberately re-entrant.
    """

    @functools.wraps(method)
    def locked(self: "SelfHealSidecar", *args: Any, **kwargs: Any) -> Any:
        with self._operation_lock():
            return method(self, *args, **kwargs)

    return locked


class SelfHealSidecar:
    """Append-only chain of self-heal evidence for one UC."""

    def __init__(self, ctx: GovernanceContext) -> None:
        self.ctx = ctx
        self.dir = sidecar_dir(ctx)
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self._lock_fd: int | None = None

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        """Hold the cross-process artifact-directory lock, re-entrantly.

        Cooperative sidecar readers and publishers all open the same real
        artifact directory and take an exclusive BSD/POSIX advisory lock.  A
        process death releases the descriptor automatically, after which the
        next reader may apply the orphan rules.  ``lstat`` plus inode/device
        comparison ensures we locked the directory named by the active
        context, rather than a pre-existing link or a raced replacement.

        This is an engine concurrency boundary, not an OS security sandbox: an
        unrelated same-account process can ignore advisory locks.  The normal
        same-OS threat-boundary caveat therefore still applies.
        """

        with self._thread_lock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return

            artifact = _validate_directory_chain(
                self.ctx,
                self.ctx.artifact_dir,
                label="self-heal artifact lock",
                allow_missing=False,
            )
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(artifact, flags)
                opened = os.fstat(fd)
                named = os.lstat(artifact)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or stat.S_ISLNK(named.st_mode)
                    or not stat.S_ISDIR(named.st_mode)
                    or opened.st_dev != named.st_dev
                    or opened.st_ino != named.st_ino
                ):
                    raise _integrity_error(
                        "self-heal artifact lock target changed during open"
                    )
                fcntl.flock(fd, fcntl.LOCK_EX)
            except SelfHealSidecarError:
                if "fd" in locals():
                    os.close(fd)
                raise
            except OSError as exc:
                if "fd" in locals():
                    os.close(fd)
                raise _integrity_error(
                    f"cannot lock self-heal artifact directory {artifact}: {exc}"
                ) from exc

            self._lock_fd = fd
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0
                self._lock_fd = None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    # ------------------------------------------------------------------ read

    @property
    def chain_path(self) -> Path:
        return self.dir / "chain.json"

    def _validate_storage_root(self) -> None:
        _validate_sidecar_directory(self.ctx, self.dir)
        attempts = self.dir / "attempts"
        try:
            attempts.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _integrity_error(
                f"cannot inspect self-heal attempts directory {attempts}: {exc}"
            ) from exc
        _validate_sidecar_directory(self.ctx, attempts)

    def _validate_root_inventory(self) -> None:
        """Reject every direct sidecar entry outside the frozen root layout."""

        try:
            paths = sorted(self.dir.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _integrity_error(
                f"cannot list self-heal sidecar storage {self.dir}: {exc}"
            ) from exc
        for path in paths:
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise _integrity_error(
                    f"cannot inspect self-heal root entry {path}: {exc}"
                ) from exc
            if path.name == "attempts":
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise _integrity_error(
                        f"self-heal attempts root must be a real directory: {path}"
                    )
                continue
            # chain.json and indexed/prospective receipt leaves are JSON regular
            # files.  Exact receipt names and chain membership are checked later;
            # rejecting every other leaf here prevents an unknown file, directory,
            # FIFO, device, or symlink from hiding outside the verified inventory.
            if (
                not path.name.endswith(".json")
                or stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                raise _integrity_error(
                    "unexpected or unsafe self-heal root entry: " + path.name
                )

    def _validate_storage_file(
        self,
        path: Path,
        *,
        label: str,
        allow_missing: bool,
    ) -> bool:
        """Validate a chain/receipt leaf without following it or its parents."""

        self._validate_storage_root()
        _validate_sidecar_directory(self.ctx, path.parent)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return False
            raise _integrity_error(f"{label} is missing: {path.name}")
        except OSError as exc:
            raise _integrity_error(f"cannot inspect {label} {path}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise _integrity_error(f"{label} must not be a symlink: {path.name}")
        if not stat.S_ISREG(metadata.st_mode):
            raise _integrity_error(f"{label} is not a regular file: {path.name}")
        try:
            path.resolve(strict=True).relative_to(self.dir.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise _integrity_error(f"{label} escapes self-heal sidecar storage: {path}") from exc
        return True

    @_exclusive_sidecar_operation
    def exists(self) -> bool:
        return self._validate_storage_file(
            self.chain_path,
            label="self-heal chain.json",
            allow_missing=True,
        )

    @staticmethod
    def _empty_chain() -> dict[str, Any]:
        return {
            "schema": CHAIN_SCHEMA,
            "lineage_ref": {},
            "head_sha256": "",
            "sequence": 0,
            "entries": [],
        }

    @staticmethod
    def _read_receipt(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise _integrity_error(
                f"self-heal receipt {path.name} is unreadable: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise _integrity_error(f"self-heal receipt {path.name} must be a JSON object")
        return value

    def _verify_memory_envelope(self, receipt: dict[str, Any], path: Path) -> None:
        event = str(receipt["event"])
        if event not in MEMORY_ANCHORED_EVENTS:
            unexpected = {
                "phase",
                "from_role",
                "to_role",
                "sidecar_transition",
                "transition_sha256",
            }.intersection(receipt)
            if unexpected:
                raise _integrity_error(
                    f"non-anchored receipt {path.name} has Memory envelope fields: "
                    + ", ".join(sorted(unexpected))
                )
            return

        from_role, to_role = MEMORY_EVENT_ROUTES[event]
        native = {
            "schema": TRANSITION_SCHEMA,
            "event": event,
            "attempt_id": receipt["attempt_id"],
            "uc": receipt["uc"],
            "artifact_dir": receipt["artifact_dir"],
            "actor": receipt["actor"],
            "prior_state": receipt["prior_state"],
            "resulting_state": receipt["resulting_state"],
            "role_chain_anchor": receipt["role_chain_anchor"],
            "recorded_at": receipt["recorded_at"],
        }
        if receipt.get("phase") != "post_run":
            raise _integrity_error(f"Memory phase mismatch in {path.name}")
        if receipt.get("from_role") != from_role or receipt.get("to_role") != to_role:
            raise _integrity_error(f"Memory role route mismatch in {path.name}")
        if receipt.get("sidecar_transition") != native:
            raise _integrity_error(f"native sidecar transition mismatch in {path.name}")
        envelope = {
            "schema": ROLE_TRANSITION_SCHEMA,
            "event": event,
            "uc": receipt["uc"],
            "artifact_dir": receipt["artifact_dir"],
            "phase": "post_run",
            "from_role": from_role,
            "to_role": to_role,
            "actor": receipt["actor"],
            "sidecar_transition": native,
        }
        expected_hash = sha256_bytes(canonical_json(envelope))
        if receipt.get("transition_sha256") != expected_hash:
            raise _integrity_error(f"Memory transition hash mismatch in {path.name}")
        memory = receipt["memory"]
        required_memory_keys = {"namespace", "memory_id", "verified_at"}
        if (
            not required_memory_keys.issubset(memory)
            or memory.get("namespace") != _memory_namespace(self.ctx)
            or not isinstance(memory.get("memory_id"), str)
            or not isinstance(memory.get("verified_at"), str)
        ):
            raise _integrity_error(f"Memory binding schema is invalid in {path.name}")
        if self.ctx.policy["memory_mode"] == "required" and (
            not re.fullmatch(r"[0-9a-f]{64}", str(memory["memory_id"] or ""))
            or not str(memory["verified_at"] or "").strip()
        ):
            raise _integrity_error(f"strict Memory binding is invalid in {path.name}")

    @staticmethod
    def _validate_anchor(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "chain_sha256",
            "sequence",
            "lifecycle_state",
        }:
            raise _integrity_error(f"{label} schema is invalid")
        if (
            not isinstance(value.get("chain_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("chain_sha256") or ""))
            or isinstance(value.get("sequence"), bool)
            or not isinstance(value.get("sequence"), int)
            or value["sequence"] < 0
            or not isinstance(value.get("lifecycle_state"), str)
            or not value["lifecycle_state"]
        ):
            raise _integrity_error(f"{label} values are invalid")
        return value

    @classmethod
    def _validate_drift_record(
        cls,
        value: Any,
        *,
        expected_anchor: dict[str, Any],
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "detected_at",
            "expected_role_chain_anchor",
            "observed_role_chain_anchor",
            "state",
        }:
            raise _integrity_error(f"{label} schema is invalid")
        if (
            value.get("state") != STATE_INVALIDATED
            or not isinstance(value.get("detected_at"), str)
            or not str(value.get("detected_at") or "").strip()
        ):
            raise _integrity_error(f"{label} values are invalid")
        expected = cls._validate_anchor(
            value.get("expected_role_chain_anchor"),
            f"{label} expected role-chain anchor",
        )
        observed = cls._validate_anchor(
            value.get("observed_role_chain_anchor"),
            f"{label} observed role-chain anchor",
        )
        if expected != expected_anchor:
            raise _integrity_error(f"{label} does not bind the latest receipt anchor")
        if observed == expected:
            raise _integrity_error(f"{label} does not record an actual anchor change")
        return value

    @staticmethod
    def _validate_session_edge(
        event: str,
        session_id: str,
        sessions: dict[str, str],
        receipt_name: str,
    ) -> None:
        paired = PAIRED_SESSION_EVENTS.get(event)
        if paired is not None:
            if paired not in sessions or session_id != sessions[paired]:
                raise _integrity_error(
                    f"self-heal session pairing is invalid in {receipt_name}"
                )
            return
        if event not in FRESH_SESSION_EVENTS:
            return
        forbidden = {
            EVENT_HEALER_ACCEPT: (EVENT_TRIAGE, EVENT_HANDOFF),
            EVENT_REVIEW: (
                EVENT_TRIAGE,
                EVENT_HANDOFF,
                EVENT_HEALER_ACCEPT,
                EVENT_HEALER_HANDOFF,
            ),
        }[event]
        if not session_id or any(
            name not in sessions or not sessions[name] for name in forbidden
        ):
            raise _integrity_error(
                f"self-heal fresh-session constraint is unprovable in {receipt_name}"
            )
        if any(
            sessions.get(name, "") and sessions[name] == session_id
            for name in forbidden
        ):
            raise _integrity_error(
                f"self-heal fresh-session constraint is invalid in {receipt_name}"
            )

    def _orphan_candidate(
        self,
        chain: dict[str, Any],
        receipt_name: str,
    ) -> dict[str, Any] | None:
        """Build, but do not publish, the only possible next chain snapshot."""

        next_sequence = int(chain["sequence"]) + 1
        event = next(
            (
                candidate
                for candidate in EVENT_CONTRACT
                if receipt_name == f"{next_sequence - 1:03d}-{candidate}.json"
            ),
            None,
        )
        if event is None:
            return None
        receipt_path = self.dir / receipt_name
        if not self._validate_storage_file(
            receipt_path,
            label="self-heal orphan receipt",
            allow_missing=True,
        ):
            return None
        receipt = self._read_receipt(receipt_path)
        actor = receipt.get("actor")
        session_id = actor.get("session_id") if isinstance(actor, dict) else None
        candidate = copy.deepcopy(chain)
        candidate["schema"] = CHAIN_SCHEMA
        candidate["lineage_ref"] = self._lineage_ref()
        candidate["head_sha256"] = receipt.get("receipt_sha256")
        candidate["sequence"] = next_sequence
        entries = list(candidate.get("entries") or [])
        entries.append(
            {
                "sequence": next_sequence,
                "event": event,
                "state": receipt.get("resulting_state"),
                "attempt_id": receipt.get("attempt_id"),
                "receipt": receipt_name,
                "receipt_sha256": receipt.get("receipt_sha256"),
                "recorded_at": receipt.get("recorded_at"),
                "session_id": session_id,
            }
        )
        candidate["entries"] = entries
        candidate.pop("drift", None)
        return candidate

    def _validated_quarantine_inventory(self) -> set[str]:
        """Verify immutable, content-addressed unindexed-receipt evidence.

        Quarantined receipts are evidence, never chain entries.  They therefore
        cannot authorize a transition, but their exact bytes must remain
        detectable after the original root-level orphan is removed.  Only the
        direct ``attempts/<attempt_id>/unindexed_<event>_<content-sha256>.json``
        shape is recognized; a malformed lookalike fails closed.
        """

        attempts = self.dir / "attempts"
        try:
            attempts_metadata = attempts.lstat()
        except FileNotFoundError:
            return set()
        except OSError as exc:
            raise _integrity_error(
                f"cannot inspect self-heal attempts directory {attempts}: {exc}"
            ) from exc
        if stat.S_ISLNK(attempts_metadata.st_mode) or not stat.S_ISDIR(
            attempts_metadata.st_mode
        ):
            raise _integrity_error(
                f"self-heal attempts directory must be a real directory: {attempts}"
            )
        _validate_sidecar_directory(self.ctx, attempts)

        def validate_attempt_tree(parent: Path) -> None:
            """Recursively enforce the non-symlink governed-leaf boundary."""

            try:
                children = sorted(parent.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                raise _integrity_error(
                    f"cannot list self-heal attempt evidence {parent}: {exc}"
                ) from exc
            for child in children:
                try:
                    metadata = child.lstat()
                except OSError as exc:
                    raise _integrity_error(
                        f"cannot inspect self-heal attempt evidence {child}: {exc}"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    raise _integrity_error(
                        f"self-heal attempt evidence must not be a symlink: {child}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    _validate_sidecar_directory(self.ctx, child)
                    validate_attempt_tree(child)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise _integrity_error(
                        f"self-heal attempt evidence is not a regular file: {child}"
                    )
                try:
                    child.resolve(strict=True).relative_to(self.dir.resolve(strict=True))
                except (OSError, ValueError) as exc:
                    raise _integrity_error(
                        f"self-heal attempt evidence escapes sidecar storage: {child}"
                    ) from exc

        verified: set[str] = set()
        try:
            attempt_paths = sorted(attempts.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise _integrity_error(
                f"cannot list self-heal attempts directory {attempts}: {exc}"
            ) from exc
        for attempt_path in attempt_paths:
            try:
                metadata = attempt_path.lstat()
            except OSError as exc:
                raise _integrity_error(
                    f"cannot inspect self-heal attempt storage {attempt_path}: {exc}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise _integrity_error(
                    f"self-heal attempt storage must not be a symlink: {attempt_path}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                # Ordinary attempt evidence is directory-owned.  A direct file
                # here cannot be a legitimate quarantine or candidate artifact.
                raise _integrity_error(
                    f"self-heal attempt storage is not a directory: {attempt_path}"
                )
            attempt_id = attempt_path.name
            if not _SAFE_TOKEN_RE.fullmatch(attempt_id):
                raise _integrity_error(
                    f"self-heal attempt storage has an invalid attempt id: {attempt_id!r}"
                )
            _validate_sidecar_directory(self.ctx, attempt_path)
            validate_attempt_tree(attempt_path)
            try:
                children = sorted(attempt_path.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                raise _integrity_error(
                    f"cannot list self-heal attempt storage {attempt_path}: {exc}"
                ) from exc
            for path in children:
                if not path.name.startswith(_QUARANTINE_PREFIX):
                    continue
                match = _QUARANTINE_NAME_RE.fullmatch(path.name)
                if match is None:
                    raise _integrity_error(
                        f"malformed self-heal unindexed receipt name: {path.name}"
                    )
                self._validate_storage_file(
                    path,
                    label="self-heal unindexed receipt",
                    allow_missing=False,
                )
                try:
                    body = path.read_bytes()
                except OSError as exc:
                    raise _integrity_error(
                        f"cannot read self-heal unindexed receipt {path.name}: {exc}"
                    ) from exc
                if sha256_bytes(body) != match.group("content_sha256"):
                    raise _integrity_error(
                        f"self-heal unindexed receipt content hash mismatch in {path.name}"
                    )
                receipt = self._read_receipt(path)
                if (
                    receipt.get("event") != match.group("event")
                    or receipt.get("attempt_id") != attempt_id
                    or receipt.get("receipt_sha256") != receipt_sha256(receipt)
                ):
                    raise _integrity_error(
                        f"self-heal unindexed receipt identity/hash mismatch in {path.name}"
                    )
                verified.add(path.relative_to(self.dir).as_posix())
        return verified

    def _quarantine_untrusted_orphan(
        self,
        chain: dict[str, Any],
        chain_body: bytes | None,
        verified_receipts: dict[int, dict[str, Any]],
        receipt_name: str,
    ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
        """Preserve an untrusted orphan verbatim, without accepting its event.

        Local receipt hashes are integrity checks, not authenticity.  When no
        strict Memory binding exists, the only safe continuation is to retain
        the exact orphan bytes as content-addressed evidence, remove the
        root-level publication candidate, and keep the previously indexed
        state.  A retry then creates a new receipt through the ordinary actor,
        state, anchor and (where applicable) Memory controls.
        """

        next_sequence = int(chain["sequence"]) + 1
        orphan = verified_receipts[next_sequence]
        receipt_path = self.dir / receipt_name
        self._validate_storage_file(
            receipt_path,
            label="self-heal orphan receipt",
            allow_missing=False,
        )
        try:
            body = receipt_path.read_bytes()
        except OSError as exc:
            raise _integrity_error(
                f"cannot read self-heal orphan before quarantine {receipt_name}: {exc}"
            ) from exc
        try:
            current_value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise _integrity_error(
                f"self-heal orphan changed before quarantine in {receipt_name}: {exc}"
            ) from exc
        if current_value != orphan:
            raise _integrity_error(
                f"self-heal orphan changed during validation in {receipt_name}"
            )

        attempt_id = str(orphan["attempt_id"])
        event = str(orphan["event"])
        content_sha256 = sha256_bytes(body)
        attempt_path = attempts_dir(self.ctx, attempt_id)
        quarantine_path = (
            attempt_path
            / f"{_QUARANTINE_PREFIX}{event}_{content_sha256}.json"
        )
        try:
            chain_exists = self._validate_storage_file(
                self.chain_path,
                label="self-heal chain.json",
                allow_missing=True,
            )
            current_chain_body = (
                self.chain_path.read_bytes() if chain_exists else None
            )
        except OSError as exc:
            raise _integrity_error(
                f"cannot recheck self-heal chain before orphan quarantine: {exc}"
            ) from exc
        if current_chain_body != chain_body:
            raise _integrity_error(
                "self-heal chain advanced during orphan quarantine"
            )
        try:
            _atomic_bytes(quarantine_path, body, replace=False)
        except (OSError, RoleGovernanceError) as exc:
            raise _integrity_error(
                f"failed to preserve unindexed self-heal receipt {receipt_name}: {exc}"
            ) from exc

        # Revalidate both endpoints before deleting the root-level orphan.  If a
        # process died after the no-replace write, the same bytes already at the
        # content-addressed destination make this path idempotent.
        self._validate_storage_file(
            quarantine_path,
            label="self-heal unindexed receipt",
            allow_missing=False,
        )
        try:
            chain_exists = self._validate_storage_file(
                self.chain_path,
                label="self-heal chain.json",
                allow_missing=True,
            )
            current_chain_body = (
                self.chain_path.read_bytes() if chain_exists else None
            )
            if current_chain_body != chain_body:
                raise _integrity_error(
                    "self-heal chain advanced during orphan quarantine"
                )
            self._validate_storage_file(
                receipt_path,
                label="self-heal orphan receipt",
                allow_missing=False,
            )
            if quarantine_path.read_bytes() != body or receipt_path.read_bytes() != body:
                raise _integrity_error(
                    f"self-heal orphan changed while being quarantined in {receipt_name}"
                )
            receipt_path.unlink()
            _fsync_directory(self.dir)
        except SelfHealSidecarError:
            raise
        except OSError as exc:
            raise _integrity_error(
                f"failed to remove quarantined self-heal orphan {receipt_name}: {exc}"
            ) from exc
        self._validated_quarantine_inventory()

        prior_receipts = {
            sequence: receipt
            for sequence, receipt in verified_receipts.items()
            if sequence < next_sequence
        }
        return copy.deepcopy(chain), prior_receipts

    def _reconcile_verified_orphan(
        self,
        chain: dict[str, Any],
        chain_body: bytes | None,
        receipt_name: str,
    ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]] | None:
        """Index one crash-orphaned next receipt only after full replay proof."""

        candidate = self._orphan_candidate(chain, receipt_name)
        if candidate is None:
            return None

        # Reuse the complete ordinary replay verifier against the prospective
        # snapshot.  Nothing is written unless schema, sequence, state edge,
        # hash/parent, session, anchor and Memory envelope all verify together.
        verified_chain, verified_receipts = self._load_verified(
            _chain_override=candidate,
            _allow_orphan_reconciliation=False,
        )
        next_sequence = int(chain["sequence"]) + 1
        orphan = verified_receipts[next_sequence]
        orphan_anchor = orphan["role_chain_anchor"]

        drift = chain.get("drift")
        if drift is not None:
            if (
                orphan["event"] != EVENT_TRIAGE
                or orphan["prior_state"] != STATE_INVALIDATED
                or orphan["payload"].get("superseded_lifecycle_drift") != drift
            ):
                raise _integrity_error(
                    f"self-heal orphan does not supersede the indexed drift in {receipt_name}"
                )
        elif orphan["prior_state"] == STATE_INVALIDATED:
            raise _integrity_error(
                f"self-heal orphan claims unindexed lifecycle drift in {receipt_name}"
            )

        if next_sequence > 1 and drift is None:
            previous_anchor = verified_receipts[next_sequence - 1]["role_chain_anchor"]
            if orphan_anchor != previous_anchor:
                raise _integrity_error(
                    f"self-heal orphan anchor changed without indexed drift in {receipt_name}"
                )
        if (
            orphan_anchor.get("lifecycle_state") != REQUIRED_LIFECYCLE_STATE
            or read_role_chain_anchor(self.ctx) != orphan_anchor
        ):
            raise _integrity_error(
                f"self-heal orphan role-chain anchor is stale in {receipt_name}"
            )

        # A self-hash is not an authenticity proof: an actor able to rewrite an
        # orphan can also recompute that hash and the prospective chain entry.
        # Automatic indexing is therefore limited to strict receipts whose exact
        # transition, identity and receipt hash are still bound by Memory.  The
        # verifier performs exact GETs only; reconciliation never prepares or
        # finalizes a new Memory record.  All unanchored events, plus every
        # best-effort orphan, are preserved but not accepted so the same event can
        # safely retry from the previously indexed state.
        strict_anchored = (
            self.ctx.policy["memory_mode"] == "required"
            and orphan["event"] in MEMORY_ANCHORED_EVENTS
        )
        if not strict_anchored:
            return self._quarantine_untrusted_orphan(
                chain,
                chain_body,
                verified_receipts,
                receipt_name,
            )
        try:
            _memory_verify(self.ctx, orphan)
        except RoleGovernanceError as exc:
            raise _integrity_error(
                f"self-heal orphan Memory verification failed in {receipt_name}: {exc}"
            ) from exc

        candidate_body = _json_bytes(verified_chain)
        self._validate_storage_file(
            self.chain_path,
            label="self-heal chain.json",
            allow_missing=True,
        )
        try:
            current_body = (
                self.chain_path.read_bytes() if self.chain_path.exists() else None
            )
        except OSError as exc:
            raise _integrity_error(
                f"cannot recheck self-heal chain before orphan reconciliation: {exc}"
            ) from exc

        # A peer may already have completed the same deterministic reconciliation.
        if current_body == candidate_body:
            return self._load_verified(_allow_orphan_reconciliation=False)
        if current_body != chain_body:
            raise _integrity_error(
                "self-heal chain advanced during orphan reconciliation"
            )
        try:
            _atomic_bytes(
                self.chain_path,
                candidate_body,
                replace=chain_body is not None,
            )
        except (OSError, RoleGovernanceError) as exc:
            raise _integrity_error(
                f"failed to index verified self-heal orphan {receipt_name}: {exc}"
            ) from exc
        return self._load_verified(_allow_orphan_reconciliation=False)

    @_exclusive_sidecar_operation
    def _load_verified(
        self,
        *,
        _chain_override: dict[str, Any] | None = None,
        _allow_orphan_reconciliation: bool = True,
    ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
        """Read and fully verify one immutable sidecar snapshot.

        No verified state is cached: every public read reopens the chain and all
        referenced receipts, so a mutation after an earlier successful read is
        detected by the next ``load``/``state``/receipt lookup.  The only write
        exceptions are deterministic indexing of a strict-Memory-verified
        receipt, or content-addressed quarantine of a locally valid receipt
        whose authenticity cannot be proved.
        """

        self._validate_storage_root()
        self._validate_root_inventory()
        chain_exists = self._validate_storage_file(
            self.chain_path,
            label="self-heal chain.json",
            allow_missing=True,
        )
        quarantined_files = self._validated_quarantine_inventory()
        chain_body: bytes | None = None
        if _chain_override is not None:
            chain = copy.deepcopy(_chain_override)
        elif not chain_exists:
            orphan_files = (
                sorted(
                    path.relative_to(self.dir).as_posix()
                    for path in self.dir.rglob("*")
                    if path.is_file()
                )
                if self.dir.exists()
                else []
            )
            non_quarantine_files = sorted(set(orphan_files) - quarantined_files)
            if not non_quarantine_files:
                return self._empty_chain(), {}
            if not (
                _allow_orphan_reconciliation
                and non_quarantine_files == [f"000-{EVENT_TRIAGE}.json"]
            ):
                raise _integrity_error(
                    "self-heal chain is missing while sidecar files exist: "
                    + ", ".join(non_quarantine_files)
                )
            chain = self._empty_chain()
        else:
            try:
                chain_body = self.chain_path.read_bytes()
                chain = json.loads(chain_body.decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise _integrity_error(f"invalid self-heal chain: {exc}") from exc
        if not isinstance(chain, dict):
            raise _integrity_error("self-heal chain must be a JSON object")
        allowed_chain_keys = {
            "schema",
            "lineage_ref",
            "head_sha256",
            "sequence",
            "entries",
            "drift",
        }
        required_chain_keys = allowed_chain_keys - {"drift"}
        if not required_chain_keys.issubset(chain) or not set(chain).issubset(
            allowed_chain_keys
        ):
            raise _integrity_error("self-heal chain has missing or unexpected fields")
        if chain.get("schema") != CHAIN_SCHEMA:
            raise _integrity_error(f"self-heal chain schema must be {CHAIN_SCHEMA}")
        if not isinstance(chain.get("lineage_ref"), dict):
            raise _integrity_error("self-heal chain lineage_ref must be an object")
        sequence = chain.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise _integrity_error("self-heal chain sequence must be a non-negative integer")
        entries = chain.get("entries")
        if not isinstance(entries, list):
            raise _integrity_error("self-heal chain entries must be a list")
        if len(entries) != sequence:
            raise _integrity_error(
                f"self-heal entry count {len(entries)} does not match chain sequence {sequence}"
            )
        if sequence:
            lineage_ref = chain["lineage_ref"]
            if set(lineage_ref) != {"uc", "artifact_dir", "lineage_id", "namespace"}:
                raise _integrity_error("self-heal chain lineage_ref schema is invalid")
            expected_lineage_ref = self._lineage_ref()
            if lineage_ref != expected_lineage_ref:
                raise _integrity_error(
                    "self-heal chain lineage_ref does not match the active lineage"
                )
        elif chain["lineage_ref"] != {}:
            raise _integrity_error("an empty self-heal chain must have an empty lineage_ref")

        receipts: dict[int, dict[str, Any]] = {}
        referenced_names: set[str] = set()
        previous_hash = ""
        previous_state = STATE_NONE
        active_attempt = ""
        seen_attempt_ids: set[str] = set()
        attempt_sessions: dict[str, str] = {}
        latest_anchor: dict[str, Any] = {}
        entry_keys = {
            "sequence",
            "event",
            "state",
            "attempt_id",
            "receipt",
            "receipt_sha256",
            "recorded_at",
            "session_id",
        }
        base_receipt_keys = {
            "schema",
            "event",
            "sequence",
            "attempt_id",
            "uc",
            "artifact_dir",
            "recorded_at",
            "actor",
            "prior_state",
            "resulting_state",
            "role_chain_anchor",
            "parent_receipt_sha256",
            "payload",
            "memory",
            "receipt_sha256",
        }
        memory_receipt_keys = {
            "phase",
            "from_role",
            "to_role",
            "sidecar_transition",
            "transition_sha256",
        }

        for expected_sequence, entry in enumerate(entries, 1):
            if not isinstance(entry, dict) or set(entry) != entry_keys:
                raise _integrity_error(
                    f"self-heal chain entry {expected_sequence} schema is invalid"
                )
            entry_sequence = entry.get("sequence")
            if isinstance(entry_sequence, bool) or entry_sequence != expected_sequence:
                raise _integrity_error(
                    f"self-heal chain entry sequence is not continuous at {expected_sequence}"
                )
            event = entry.get("event")
            if not isinstance(event, str) or event not in EVENT_CONTRACT:
                raise _integrity_error(
                    f"self-heal chain entry {expected_sequence} has unknown event {event!r}"
                )
            receipt_name = entry.get("receipt")
            if not isinstance(receipt_name, str) or not receipt_name:
                raise _integrity_error(
                    f"self-heal chain entry {expected_sequence} has no receipt filename"
                )
            if receipt_name in referenced_names:
                raise _integrity_error(f"duplicate self-heal receipt reference: {receipt_name}")
            referenced_names.add(receipt_name)
            if Path(receipt_name).name != receipt_name:
                raise _integrity_error(
                    f"self-heal receipt reference must be a leaf filename: {receipt_name}"
                )
            expected_name = f"{expected_sequence - 1:03d}-{event}.json"
            if receipt_name != expected_name:
                raise _integrity_error(
                    f"self-heal receipt filename mismatch at sequence {expected_sequence}: "
                    f"expected {expected_name}, got {receipt_name}"
                )
            receipt_path = self.dir / receipt_name
            self._validate_storage_file(
                receipt_path,
                label="self-heal receipt",
                allow_missing=False,
            )
            receipt = self._read_receipt(receipt_path)
            expected_receipt_keys = set(base_receipt_keys)
            if event in MEMORY_ANCHORED_EVENTS:
                expected_receipt_keys.update(memory_receipt_keys)
            if set(receipt) != expected_receipt_keys:
                raise _integrity_error(f"self-heal receipt schema is invalid in {receipt_name}")
            if receipt.get("schema") != EVIDENCE_SCHEMA:
                raise _integrity_error(
                    f"self-heal receipt schema must be {EVIDENCE_SCHEMA} in {receipt_name}"
                )
            receipt_sequence = receipt.get("sequence")
            if isinstance(receipt_sequence, bool) or receipt_sequence != expected_sequence:
                raise _integrity_error(f"self-heal receipt sequence mismatch in {receipt_name}")
            if receipt.get("event") != event:
                raise _integrity_error(f"self-heal receipt event mismatch in {receipt_name}")
            if receipt.get("uc") != self.ctx.uc:
                raise _integrity_error(f"self-heal receipt UC mismatch in {receipt_name}")
            if receipt.get("artifact_dir") != workspace_rel(
                self.ctx.artifact_dir, self.ctx.root
            ):
                raise _integrity_error(
                    f"self-heal receipt artifact_dir mismatch in {receipt_name}"
                )
            if not isinstance(receipt.get("payload"), dict):
                raise SelfHealSidecarError(
                    REASON_EVENT_PAYLOAD_INVALID,
                    f"self-heal receipt {receipt_name} payload must be a JSON object",
                )
            if not isinstance(receipt.get("memory"), dict):
                raise _integrity_error(
                    f"self-heal receipt Memory schema is invalid in {receipt_name}"
                )
            actor = receipt.get("actor")
            if not isinstance(actor, dict) or set(actor) != {"role", "runtime", "session_id"}:
                raise _integrity_error(
                    f"self-heal receipt actor schema is invalid in {receipt_name}"
                )
            actor_runtime = actor.get("runtime")
            if (
                not isinstance(actor_runtime, str)
                or actor_runtime not in {"codex", "claude", "unknown"}
                or not isinstance(actor.get("session_id"), str)
                or actor.get("session_id") != str(actor.get("session_id") or "").strip()
                or (
                    self.ctx.policy["session_id_required"]
                    and not str(actor.get("session_id") or "")
                )
                or not isinstance(receipt.get("recorded_at"), str)
                or not receipt.get("recorded_at")
            ):
                raise _integrity_error(
                    f"self-heal receipt actor/timestamp values are invalid in {receipt_name}"
                )
            expected_role, allowed_prior, allowed_results = EVENT_CONTRACT[event]
            if actor.get("role") != expected_role:
                raise _integrity_error(f"self-heal receipt actor role mismatch in {receipt_name}")
            receipt_prior_state = receipt.get("prior_state")
            receipt_resulting_state = receipt.get("resulting_state")
            if (
                not isinstance(receipt_prior_state, str)
                or not isinstance(receipt_resulting_state, str)
                or receipt_prior_state not in allowed_prior
                or receipt_resulting_state not in allowed_results
            ):
                raise _integrity_error(f"self-heal receipt state edge is invalid in {receipt_name}")
            _validate_event_payload(
                str(event),
                receipt["payload"],
                str(receipt["resulting_state"]),
                prior_state=str(receipt["prior_state"]),
                label=f"self-heal receipt {receipt_name}",
            )
            if receipt.get("prior_state") != previous_state and not (
                event == EVENT_TRIAGE and receipt.get("prior_state") == STATE_INVALIDATED
            ):
                raise _integrity_error(
                    f"self-heal receipt prior state is discontinuous in {receipt_name}"
                )
            attempt_id = receipt.get("attempt_id")
            if not isinstance(attempt_id, str) or not _SAFE_TOKEN_RE.fullmatch(attempt_id):
                raise _integrity_error(f"self-heal receipt attempt id is invalid in {receipt_name}")
            if event == EVENT_TRIAGE:
                if attempt_id in seen_attempt_ids:
                    raise _integrity_error(
                        f"self-heal attempt id is reused in {receipt_name}"
                    )
                if receipt.get("prior_state") == STATE_INVALIDATED:
                    if not previous_hash:
                        raise _integrity_error(
                            f"self-heal invalidated triage has no predecessor in {receipt_name}"
                        )
                    self._validate_drift_record(
                        receipt["payload"].get("superseded_lifecycle_drift"),
                        expected_anchor=latest_anchor,
                        label=f"superseded lifecycle drift in {receipt_name}",
                    )
                active_attempt = attempt_id
                seen_attempt_ids.add(attempt_id)
                attempt_sessions = {}
            elif attempt_id != active_attempt:
                raise _integrity_error(f"self-heal receipt attempt id changed in {receipt_name}")
            self._validate_session_edge(
                str(event), str(actor["session_id"]), attempt_sessions, receipt_name
            )
            attempt_sessions[str(event)] = str(actor["session_id"])
            anchor = self._validate_anchor(
                receipt.get("role_chain_anchor"),
                f"self-heal role-chain anchor in {receipt_name}",
            )
            if anchor["lifecycle_state"] != REQUIRED_LIFECYCLE_STATE:
                raise _integrity_error(
                    f"self-heal receipt anchor lifecycle state is invalid in {receipt_name}"
                )
            supersedes_indexed_drift = (
                event == EVENT_TRIAGE
                and receipt.get("prior_state") == STATE_INVALIDATED
            )
            if previous_hash and not supersedes_indexed_drift and anchor != latest_anchor:
                raise _integrity_error(
                    "self-heal receipt anchor changed without indexed lifecycle drift in "
                    + receipt_name
                )

            computed_hash = receipt_sha256(receipt)
            if receipt.get("receipt_sha256") != computed_hash:
                raise _integrity_error(f"self-heal receipt hash mismatch in {receipt_name}")
            if receipt.get("parent_receipt_sha256") != previous_hash:
                raise _integrity_error(f"self-heal parent receipt mismatch in {receipt_name}")
            entry_checks = {
                "sequence": receipt["sequence"],
                "event": receipt["event"],
                "state": receipt["resulting_state"],
                "attempt_id": receipt["attempt_id"],
                "receipt_sha256": computed_hash,
                "recorded_at": receipt["recorded_at"],
                "session_id": actor["session_id"],
            }
            for field, wanted in entry_checks.items():
                if entry.get(field) != wanted:
                    raise _integrity_error(
                        f"self-heal chain entry {field} mismatch in {receipt_name}"
                    )
            self._verify_memory_envelope(receipt, receipt_path)
            receipts[expected_sequence] = receipt
            previous_hash = computed_hash
            previous_state = str(receipt["resulting_state"])
            latest_anchor = anchor

        if chain.get("head_sha256") != previous_hash:
            raise _integrity_error("self-heal chain head does not match the latest receipt")
        if "drift" in chain:
            if not sequence:
                raise _integrity_error("self-heal drift record has no receipt predecessor")
            drift = self._validate_drift_record(
                chain["drift"],
                expected_anchor=latest_anchor,
                label="self-heal drift record",
            )
            if read_role_chain_anchor(self.ctx) == drift["expected_role_chain_anchor"]:
                raise _integrity_error(
                    "self-heal drift record is not supported by the current role-chain anchor"
                )

        actual_names = {
            path.name for path in self.dir.glob("*.json") if path.name != "chain.json"
        }
        if actual_names != referenced_names:
            missing = sorted(referenced_names - actual_names)
            extra = sorted(actual_names - referenced_names)
            if (
                _allow_orphan_reconciliation
                and not missing
                and len(extra) == 1
            ):
                reconciled = self._reconcile_verified_orphan(
                    chain,
                    chain_body,
                    extra[0],
                )
                if reconciled is not None:
                    return reconciled
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("unreferenced=" + ",".join(extra))
            raise _integrity_error("self-heal receipt inventory mismatch: " + "; ".join(detail))
        return chain, receipts

    def load(self) -> dict[str, Any]:
        chain, _receipts = self._load_verified()
        return chain

    def entries(self) -> list[dict[str, Any]]:
        chain, _receipts = self._load_verified()
        return list(chain["entries"])

    def state(self) -> str:
        chain, _receipts = self._load_verified()
        if chain.get("drift"):
            return STATE_INVALIDATED
        entries = chain["entries"]
        if not entries:
            return STATE_NONE
        return str(entries[-1].get("state") or STATE_NONE)

    def attempt_id(self) -> str:
        chain, _receipts = self._load_verified()
        entries = chain["entries"]
        return str(entries[-1].get("attempt_id") or "") if entries else ""

    def attempt_count(self) -> int:
        """Distinct attempts opened so far (one per ``triage_recorded``)."""

        chain, _receipts = self._load_verified()
        return sum(1 for entry in chain["entries"] if entry.get("event") == EVENT_TRIAGE)

    def session_for(self, event: str) -> str:
        """Session id of the most recent occurrence of ``event`` in this attempt."""

        chain, _receipts = self._load_verified()
        entries = chain["entries"]
        attempt = str(entries[-1].get("attempt_id") or "") if entries else ""
        for entry in reversed(entries):
            if entry.get("attempt_id") != attempt:
                break
            if entry.get("event") == event:
                return str(entry.get("session_id") or "")
        return ""

    def attempt_sessions(self) -> dict[str, str]:
        chain, _receipts = self._load_verified()
        entries = chain["entries"]
        attempt = str(entries[-1].get("attempt_id") or "") if entries else ""
        sessions: dict[str, str] = {}
        for entry in entries:
            if entry.get("attempt_id") != attempt:
                continue
            sessions[str(entry.get("event"))] = str(entry.get("session_id") or "")
        return sessions

    def receipt(self, sequence: int) -> dict[str, Any]:
        _chain, receipts = self._load_verified()
        if sequence in receipts:
            return receipts[sequence]
        raise SelfHealSidecarError(
            REASON_TRANSITION, f"self-heal receipt {sequence} is not in the chain"
        )

    def latest_payload(self, event: str) -> dict[str, Any]:
        chain, receipts = self._load_verified()
        entries = chain["entries"]
        attempt = str(entries[-1].get("attempt_id") or "") if entries else ""
        for entry in reversed(entries):
            if entry.get("attempt_id") != attempt:
                break
            if entry.get("event") == event:
                payload = receipts[int(entry["sequence"])].get("payload")
                return payload if isinstance(payload, dict) else {}
        return {}

    @_exclusive_sidecar_operation
    def status_snapshot(self) -> dict[str, Any]:
        """Return one linearized state/attempt/review/anchor snapshot.

        CLI status must not compose fields from several independently locked
        reads: a peer may legally close or retriage between them.  The main role
        publisher uses the same artifact-directory flock, so the observed main
        anchor and the verified sidecar chain below form one cooperative
        cross-process snapshot.  Newly detected drift is persisted before the
        snapshot is returned.
        """

        chain, receipts = self._load_verified()
        # Authenticate the sidecar before consulting the main lifecycle.  If
        # both surfaces are damaged, report the immutable attempt-evidence
        # corruption instead of letting a malformed anchor mask it as drift.
        observed = read_role_chain_anchor(self.ctx)
        entries = chain["entries"]
        if not entries:
            return {
                "state": STATE_NONE,
                "attempt_id": "",
                "review_outcome": "",
                "drifted": False,
            }

        attempt = str(entries[-1].get("attempt_id") or "")
        if chain.get("drift"):
            return {
                "state": STATE_INVALIDATED,
                "attempt_id": attempt,
                "review_outcome": "",
                "drifted": True,
            }

        recorded = receipts[int(entries[-1]["sequence"])].get("role_chain_anchor")
        recorded = recorded if isinstance(recorded, dict) else {}
        drifted = any(
            recorded.get(field) != observed.get(field)
            for field in ("chain_sha256", "sequence", "lifecycle_state")
        )
        if drifted:
            # A missing main chain is drift, but its empty sentinel is not a
            # schema-valid anchor and must never be persisted into chain.json.
            # Report a repeatable invalidated snapshot without changing bytes;
            # once a real chain exists again, the normal validated drift record
            # may be written against its exact anchor.
            if self._anchor_is_persistable(observed):
                self.invalidate(recorded, observed)
            return {
                "state": STATE_INVALIDATED,
                "attempt_id": attempt,
                "review_outcome": "",
                "drifted": True,
            }

        review_outcome = ""
        for entry in reversed(entries):
            if entry.get("attempt_id") != attempt:
                break
            if entry.get("event") == EVENT_REVIEW:
                payload = receipts[int(entry["sequence"])].get("payload")
                if isinstance(payload, dict):
                    review_outcome = str(payload.get("outcome") or "")
                break
        return {
            "state": str(entries[-1].get("state") or STATE_NONE),
            "attempt_id": attempt,
            "review_outcome": review_outcome,
            "drifted": False,
        }

    @_exclusive_sidecar_operation
    def invalidate_if_drifted(self) -> bool:
        """Atomically compare the live anchor and persist drift when required."""

        snapshot = self.status_snapshot()
        return bool(snapshot["drifted"])

    # ----------------------------------------------------------- drift check

    @_exclusive_sidecar_operation
    def anchor_drifted(self) -> tuple[bool, dict[str, Any], dict[str, Any]]:
        """Compare the recorded anchor against the main chain as it is now."""

        observed = read_role_chain_anchor(self.ctx)
        chain, receipts = self._load_verified()
        entries = chain["entries"]
        if not entries:
            return False, {}, observed
        recorded = receipts[int(entries[-1]["sequence"])].get("role_chain_anchor")
        recorded = recorded if isinstance(recorded, dict) else {}
        drifted = any(
            recorded.get(field) != observed.get(field)
            for field in ("chain_sha256", "sequence", "lifecycle_state")
        )
        return drifted, recorded, observed

    @staticmethod
    def _anchor_is_persistable(value: Any) -> bool:
        return bool(
            isinstance(value, dict)
            and set(value) == {"chain_sha256", "sequence", "lifecycle_state"}
            and isinstance(value.get("chain_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(value.get("chain_sha256") or ""))
            and not isinstance(value.get("sequence"), bool)
            and isinstance(value.get("sequence"), int)
            and value["sequence"] >= 0
            and isinstance(value.get("lifecycle_state"), str)
            and bool(str(value.get("lifecycle_state") or ""))
        )

    @_exclusive_sidecar_operation
    def invalidate(self, recorded: dict[str, Any], observed: dict[str, Any]) -> None:
        """Record lifecycle drift on the chain index without forging an event.

        No receipt is written: nothing an actor did produced this state.  The
        drift record is what makes the invalidation auditable and stops the
        attempt from continuing against evidence that no longer holds.
        """

        chain, receipts = self._load_verified()
        entries = chain["entries"]
        if not entries:
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "the self-heal chain changed before lifecycle drift could be recorded; "
                "retry against the current state",
            )

        # ``anchor_drifted`` is intentionally a separate public read, so another
        # cooperative process can advance the sidecar before this method obtains
        # the operation lock.  Treat the caller's recorded anchor as a CAS token:
        # never attach an old comparison to a newer receipt head.  An already
        # persisted drift is authoritative and idempotent; a retriage clears it
        # while changing the latest receipt anchor, which makes the stale writer
        # fail without touching chain.json.
        latest = receipts[int(entries[-1]["sequence"])].get("role_chain_anchor")
        latest = latest if isinstance(latest, dict) else {}
        if latest != recorded:
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "the self-heal chain advanced before lifecycle drift could be recorded; "
                "retry against the current state",
            )
        if chain.get("drift") is not None:
            return

        # Re-read the main role chain while holding the sidecar lock.  The
        # caller's observed value may itself be stale even when the sidecar head
        # is unchanged; persisting the live value keeps the drift record honest.
        current_observed = read_role_chain_anchor(self.ctx)
        if not self._anchor_is_persistable(current_observed):
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "the live role-chain anchor is missing or invalid; lifecycle drift "
                "cannot be persisted safely",
            )
        if current_observed == latest:
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "the role-chain anchor changed again before lifecycle drift could be "
                "recorded; retry against the current state",
            )
        chain["drift"] = {
            "detected_at": utc_now(),
            "expected_role_chain_anchor": latest,
            "observed_role_chain_anchor": current_observed,
            "state": STATE_INVALIDATED,
        }
        self._validate_storage_file(
            self.chain_path,
            label="self-heal chain.json",
            allow_missing=False,
        )
        _atomic_bytes(self.chain_path, _json_bytes(chain), replace=True)

    # --------------------------------------------------------------- publish

    def _validate_publish(
        self,
        event: str,
        attempt_id: str,
        resulting_state: str | None,
        *,
        payload: Any = _PAYLOAD_NOT_PROVIDED,
        invalidate_on_drift: bool,
    ) -> dict[str, Any]:
        """Shared, otherwise read-only validation for a prospective event."""

        if not isinstance(event, str) or event not in EVENT_CONTRACT:
            raise SelfHealSidecarError(REASON_TRANSITION, f"unknown self-heal event: {event}")
        expected_role, allowed_prior, allowed_results = EVENT_CONTRACT[event]
        if not isinstance(attempt_id, str) or not _SAFE_TOKEN_RE.fullmatch(attempt_id):
            raise SelfHealSidecarError(
                REASON_ATTEMPT_MISMATCH, f"invalid self-heal attempt id: {attempt_id!r}"
            )
        selected_state = resulting_state or allowed_results[0]
        if selected_state not in allowed_results:
            raise SelfHealSidecarError(
                REASON_TRANSITION,
                f"{event} cannot result in state {selected_state!r}; "
                f"expected one of {list(allowed_results)}",
            )
        if payload is not _PAYLOAD_NOT_PROVIDED:
            _validate_event_payload(
                event,
                payload,
                selected_state,
                label=f"prospective {event}",
            )

        chain, receipts = self._load_verified()
        drift = chain.get("drift")
        if drift and event != EVENT_TRIAGE:
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "the self-heal attempt is already invalidated by lifecycle drift; "
                "re-run triage against current lifecycle evidence",
            )
        entries = chain["entries"]
        if event == EVENT_TRIAGE and any(
            entry.get("event") == EVENT_TRIAGE and entry.get("attempt_id") == attempt_id
            for entry in entries
        ):
            raise SelfHealSidecarError(
                REASON_ATTEMPT_MISMATCH,
                f"self-heal attempt id {attempt_id!r} was already used",
            )
        current = (
            STATE_INVALIDATED
            if drift
            else (str(entries[-1].get("state") or STATE_NONE) if entries else STATE_NONE)
        )
        if current not in allowed_prior:
            raise SelfHealSidecarError(
                REASON_TRANSITION,
                f"{event} requires state {list(allowed_prior)}; the attempt is in {current!r}",
            )
        if payload is not _PAYLOAD_NOT_PROVIDED:
            _validate_event_payload(
                event,
                payload,
                selected_state,
                prior_state=current,
                label=f"prospective {event}",
            )
        actor = _self_heal_actor(self.ctx, expected_role)

        open_attempt = str(entries[-1].get("attempt_id") or "") if entries else ""
        if event != EVENT_TRIAGE:
            if open_attempt and open_attempt != attempt_id:
                raise SelfHealSidecarError(
                    REASON_ATTEMPT_MISMATCH,
                    f"attempt {attempt_id!r} does not match the open attempt {open_attempt!r}",
                )
        if event == EVENT_HANDOFF:
            triage_entry = next(
                (
                    entry
                    for entry in reversed(entries)
                    if entry.get("attempt_id") == open_attempt
                    and entry.get("event") == EVENT_TRIAGE
                ),
                None,
            )
            triage_payload = (
                receipts[int(triage_entry["sequence"])].get("payload")
                if triage_entry is not None
                else None
            )
            _validate_triage_eligibility(
                EVENT_TRIAGE,
                triage_payload,
                label=f"authenticated triage for prospective {event}",
            )
        attempt_sessions = {
            str(entry.get("event")): str(entry.get("session_id") or "")
            for entry in entries
            if entry.get("attempt_id") == open_attempt
        }
        self._check_sessions(
            event,
            actor["session_id"],
            sessions=attempt_sessions,
        )

        anchor = read_role_chain_anchor(self.ctx)
        if not drift:
            recorded = (
                receipts[int(entries[-1]["sequence"])].get("role_chain_anchor")
                if entries
                else {}
            )
            recorded = recorded if isinstance(recorded, dict) else {}
            observed = anchor
            drifted = bool(entries) and any(
                recorded.get(field) != observed.get(field)
                for field in ("chain_sha256", "sequence", "lifecycle_state")
            )
            if drifted:
                if invalidate_on_drift:
                    self.invalidate(recorded, observed)
                raise SelfHealSidecarError(
                    REASON_DRIFT,
                    "the role chain advanced while this attempt was open; re-run triage "
                    "against the current lifecycle evidence",
                )
        if anchor["lifecycle_state"] != REQUIRED_LIFECYCLE_STATE:
            raise SelfHealSidecarError(
                REASON_LIFECYCLE_STATE,
                "self-healing requires the role chain in state "
                f"{REQUIRED_LIFECYCLE_STATE!r}; observed {anchor['lifecycle_state']!r}",
            )
        validated = {
            "actor": actor,
            "prior_state": current,
            "resulting_state": selected_state,
            "role_chain_anchor": anchor,
        }
        if drift:
            validated["superseded_lifecycle_drift"] = copy.deepcopy(drift)
        return validated

    def preflight_publish(
        self,
        event: str,
        attempt_id: str,
        resulting_state: str | None = None,
        *,
        payload: Any = _PAYLOAD_NOT_PROVIDED,
    ) -> dict[str, Any]:
        """Validate a prospective publication without changing sidecar bytes.

        This is safe to call before candidate generation writes attempt evidence.
        In particular, lifecycle drift is reported but is not persisted here;
        only the actual publisher retains the historical invalidation behavior.
        """

        return self._validate_publish(
            event,
            attempt_id,
            resulting_state,
            payload=payload,
            invalidate_on_drift=False,
        )

    @_exclusive_sidecar_operation
    def publish(
        self,
        event: str,
        *,
        attempt_id: str,
        payload: dict[str, Any],
        resulting_state: str | None = None,
    ) -> dict[str, Any]:
        """Validate and append one sidecar event.  Fail-closed on every check."""

        validated = self._validate_publish(
            event,
            attempt_id,
            resulting_state,
            payload=payload,
            invalidate_on_drift=True,
        )
        actor = validated["actor"]
        current = str(validated["prior_state"])
        resulting_state = str(validated["resulting_state"])
        anchor = validated["role_chain_anchor"]

        chain = self.load()
        sequence = int(chain.get("sequence") or 0) + 1
        receipt_payload = copy.deepcopy(payload)
        superseded_drift = validated.get("superseded_lifecycle_drift")
        if superseded_drift is not None:
            receipt_payload["superseded_lifecycle_drift"] = superseded_drift
        receipt: dict[str, Any] = {
            "schema": EVIDENCE_SCHEMA,
            "event": event,
            "sequence": sequence,
            "attempt_id": attempt_id,
            "uc": self.ctx.uc,
            "artifact_dir": workspace_rel(self.ctx.artifact_dir, self.ctx.root),
            "recorded_at": utc_now(),
            "actor": actor,
            "prior_state": current,
            "resulting_state": resulting_state,
            "role_chain_anchor": anchor,
            "parent_receipt_sha256": str(chain.get("head_sha256") or ""),
            "payload": receipt_payload,
        }

        # Same ordering as role_governance._publish: prepare Memory, hash the
        # receipt that carries the prepared binding, finalize against that hash,
        # and re-hash only if finalization changed the public view.
        transition, prepared = self._prepare_memory(event, receipt)
        receipt["memory"] = _public_memory_binding(
            prepared,
            reason=REASON_MEMORY_PREPARE,
            label="prepared Memory binding",
        )
        receipt["receipt_sha256"] = receipt_sha256(receipt)
        if transition is not None:
            try:
                finalized = _memory_finalize(
                    self.ctx, prepared, receipt["receipt_sha256"], transition
                )
            except RoleGovernanceError as exc:
                raise SelfHealSidecarError(REASON_MEMORY_FINALIZE, str(exc)) from exc
            finalized_public = _public_memory_binding(
                finalized,
                reason=REASON_MEMORY_FINALIZE,
                label="finalized Memory binding",
            )
            if finalized_public != receipt["memory"]:
                receipt["memory"] = finalized_public
                receipt["receipt_sha256"] = receipt_sha256(receipt)

        if self.ctx.policy["memory_mode"] == "required" and event in MEMORY_ANCHORED_EVENTS:
            try:
                _memory_verify(self.ctx, receipt)
            except RoleGovernanceError as exc:
                raise SelfHealSidecarError(REASON_MEMORY_VERIFY, str(exc)) from exc

        # Memory calls may take long enough for the main lifecycle to move after
        # the initial validation.  Re-read immediately before the first local
        # sidecar write; a stale receipt must never be published successfully.
        observed_after_memory = read_role_chain_anchor(self.ctx)
        if observed_after_memory != anchor:
            if superseded_drift is None:
                self.invalidate(anchor, observed_after_memory)
            raise SelfHealSidecarError(
                REASON_DRIFT,
                "the role chain advanced during self-heal publication; re-run triage "
                "against the current lifecycle evidence",
            )

        # Exact Memory verification is intentionally complete before the first
        # local write.  Prepare/finalize/verify failure therefore leaves neither
        # an indexed event nor an orphan receipt that could block a safe retry.
        name = f"{sequence - 1:03d}-{event}.json"
        receipt_path = self.dir / name
        if self._validate_storage_file(
            receipt_path,
            label="self-heal receipt",
            allow_missing=True,
        ):
            raise _integrity_error(f"self-heal receipt already exists: {name}")
        self._validate_storage_file(
            self.chain_path,
            label="self-heal chain.json",
            allow_missing=True,
        )
        _atomic_bytes(receipt_path, _json_bytes(receipt), replace=False)

        chain["schema"] = CHAIN_SCHEMA
        chain["lineage_ref"] = self._lineage_ref()
        chain["head_sha256"] = receipt["receipt_sha256"]
        chain["sequence"] = sequence
        entries = list(chain.get("entries") or [])
        entries.append(
            {
                "sequence": sequence,
                "event": event,
                "state": resulting_state,
                "attempt_id": attempt_id,
                "receipt": name,
                "receipt_sha256": receipt["receipt_sha256"],
                "recorded_at": receipt["recorded_at"],
                "session_id": actor["session_id"],
            }
        )
        chain["entries"] = entries
        chain.pop("drift", None)
        self._validate_storage_file(
            self.chain_path,
            label="self-heal chain.json",
            allow_missing=True,
        )
        _atomic_bytes(self.chain_path, _json_bytes(chain), replace=True)
        return receipt

    # ---------------------------------------------------------------- checks

    def _check_sessions(
        self,
        event: str,
        session_id: str,
        *,
        sessions: dict[str, str] | None = None,
    ) -> None:
        sessions = self.attempt_sessions() if sessions is None else sessions
        paired = PAIRED_SESSION_EVENTS.get(event)
        if paired is not None:
            if paired not in sessions or session_id != sessions[paired]:
                raise SelfHealSidecarError(
                    REASON_SESSION_MISMATCH,
                    f"{event} must continue the {paired} session "
                    f"{sessions.get(paired, '')!r}",
                )
            return
        if event not in FRESH_SESSION_EVENTS:
            return
        # A fresh session is the whole anti-self-approval control: the healer may
        # not be the reviewer who declared the failure healable, and the
        # independent reviewer may be neither of them.
        forbidden = {
            EVENT_HEALER_ACCEPT: (EVENT_TRIAGE, EVENT_HANDOFF),
            EVENT_REVIEW: (EVENT_TRIAGE, EVENT_HANDOFF, EVENT_HEALER_ACCEPT, EVENT_HEALER_HANDOFF),
        }[event]
        unprovable = sorted(
            name for name in forbidden if name not in sessions or not sessions[name]
        )
        if not session_id or unprovable:
            missing = ["current"] if not session_id else []
            missing.extend(unprovable)
            raise SelfHealSidecarError(
                REASON_SAME_SESSION,
                f"{event} requires a provably fresh session; session identity is "
                "missing for " + ", ".join(missing),
            )
        clashes = sorted(
            name for name in forbidden if sessions.get(name, "") and sessions[name] == session_id
        )
        if clashes:
            raise SelfHealSidecarError(
                REASON_SAME_SESSION,
                f"{event} requires a fresh session; {session_id!r} already published "
                + ", ".join(clashes),
            )

    def _lineage_ref(self) -> dict[str, Any]:
        try:
            identity = lineage_identity(self.ctx.artifact_dir)
        except RoleGovernanceError:
            identity = {}
        return {
            "uc": self.ctx.uc,
            "artifact_dir": workspace_rel(self.ctx.artifact_dir, self.ctx.root),
            "lineage_id": str(identity.get("lineage_id") or ""),
            "namespace": _memory_namespace(self.ctx),
        }

    def _prepare_memory(
        self, event: str, receipt: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Strict Memory transition for the anchored events.

        Deliberately *not* ``role_governance._publish``: no registry
        transaction, no checkpoint, no lineage CAS.  A Memory failure stops the
        sidecar event and leaves the attempt where it was; it never touches the
        governed lifecycle.
        """

        if event not in MEMORY_ANCHORED_EVENTS:
            return None, {
                "namespace": _memory_namespace(self.ctx),
                "memory_id": "",
                "status": "not_required",
            }
        native_transition = {
            "schema": TRANSITION_SCHEMA,
            "event": event,
            "attempt_id": receipt["attempt_id"],
            "uc": receipt["uc"],
            "artifact_dir": receipt["artifact_dir"],
            "actor": receipt["actor"],
            "prior_state": receipt["prior_state"],
            "resulting_state": receipt["resulting_state"],
            "role_chain_anchor": receipt["role_chain_anchor"],
            "recorded_at": receipt["recorded_at"],
        }
        from_role, to_role = MEMORY_EVENT_ROUTES[event]
        transition = {
            "schema": ROLE_TRANSITION_SCHEMA,
            "event": event,
            "uc": receipt["uc"],
            "artifact_dir": receipt["artifact_dir"],
            "phase": "post_run",
            "from_role": from_role,
            "to_role": to_role,
            "actor": receipt["actor"],
            "sidecar_transition": copy.deepcopy(native_transition),
        }
        transition["transition_sha256"] = sha256_bytes(canonical_json(transition))
        # ``memory_bus.verify_role_transition`` compares every transition field
        # to the local receipt.  Add only the envelope fields: the receipt keeps
        # its frozen self-heal evidence schema, event and state vocabulary.
        for key, value in transition.items():
            if key not in {"schema", "event", "uc", "artifact_dir", "actor"}:
                receipt[key] = copy.deepcopy(value)
        try:
            prepared = _memory_prepare(self.ctx, transition)
        except RoleGovernanceError as exc:
            raise SelfHealSidecarError(REASON_MEMORY_PREPARE, str(exc)) from exc
        return transition, prepared
