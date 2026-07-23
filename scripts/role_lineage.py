#!/usr/bin/env python3
"""Machine-level role-governance lineage registry.

The workspace role-evidence chain is intentionally not the sole authority for
whether a use case has history.  This module provides the second, machine-level
anchor: a SUT-neutral SQLite registry stored beside the Memory service data.

The registry is deliberately a small synchronous component.  Every mutating
method opens a short ``BEGIN IMMEDIATE`` transaction, performs only local
SQLite work, and commits before returning.  Callers must never place Memory
HTTP work inside one of these transactions; instead they journal each external
step with :meth:`LineageRegistry.update_stage`.

No workspace absolute path, OS identity, credential, or Memory token is part
of the lineage identity or has a dedicated database field.  Workspace paths
accepted here must be canonical workspace-relative POSIX paths.
"""

from __future__ import annotations

import hashlib
import base64
import binascii
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, ClassVar, Iterator, Mapping, Sequence


LINEAGE_KEY_SCHEMA = "bugate.role-lineage-key/v1"
ROLE_LINEAGE_ROOT_SCHEMA = "bugate.role-lineage-root/v1"
ROLE_LINEAGE_CHECKPOINT_SCHEMA = "bugate.role-lineage-checkpoint/v1"
ROLE_EVIDENCE_SCHEMA = "bugate.role-evidence/v1"
ROLE_CHAIN_SCHEMA = "bugate.role-chain/v1"
REGISTRY_FILENAME = "role-lineage.sqlite3"
REGISTRY_SCHEMA_VERSION = 2
DEFAULT_MEMORY_HOME = Path.home() / ".bugate" / "memory-bus"
MEMORY_MODES = frozenset({"best_effort", "required"})
INTEGRITY_STATES = frozenset(
    {
        "uninitialized",
        "aligned",
        "migration_required",
        "history_missing",
        "history_diverged",
        "recovery_pending",
        "registry_unavailable",
    }
)

TX_STATUS_PENDING = "pending"
TX_STATUS_RECOVERING = "recovering"
TX_STATUS_COMPLETED = "completed"
TX_STATUS_ABORTED = "aborted"
ACTIVE_TX_STATUSES = frozenset({TX_STATUS_PENDING, TX_STATUS_RECOVERING})

TX_STAGE_PENDING = "pending"
TX_STAGE_MEMORY_PREPARED = "memory_prepared"
TX_STAGE_RECEIPT_BOUND = "receipt_bound"
TX_STAGE_MEMORY_FINALIZED = "memory_finalized"
TX_STAGE_CHECKPOINT_VERIFIED = "checkpoint_verified"
TX_STAGE_READY_FOR_CAS = "ready_for_cas"
TX_STAGE_REGISTRY_COMMITTED = "registry_committed"
TX_STAGE_RECEIPT_WRITTEN = "receipt_written"
TX_STAGE_CHAIN_REPLACED = "chain_replaced"
TX_STAGE_COMPLETED = "completed"
TX_STAGE_ABORTED = "aborted"
TX_STAGES: tuple[str, ...] = (
    TX_STAGE_PENDING,
    TX_STAGE_MEMORY_PREPARED,
    TX_STAGE_RECEIPT_BOUND,
    TX_STAGE_MEMORY_FINALIZED,
    TX_STAGE_CHECKPOINT_VERIFIED,
    TX_STAGE_READY_FOR_CAS,
    TX_STAGE_REGISTRY_COMMITTED,
    TX_STAGE_RECEIPT_WRITTEN,
    TX_STAGE_CHAIN_REPLACED,
    TX_STAGE_COMPLETED,
    TX_STAGE_ABORTED,
)
_ACTIVE_STAGE_ORDER = {
    stage: index
    for index, stage in enumerate(
        (
            TX_STAGE_PENDING,
            TX_STAGE_MEMORY_PREPARED,
            TX_STAGE_RECEIPT_BOUND,
            TX_STAGE_MEMORY_FINALIZED,
            TX_STAGE_CHECKPOINT_VERIFIED,
            TX_STAGE_READY_FOR_CAS,
            TX_STAGE_REGISTRY_COMMITTED,
            TX_STAGE_RECEIPT_WRITTEN,
            TX_STAGE_CHAIN_REPLACED,
        )
    )
}
_REQUIRED_STAGE_NEXT = {
    TX_STAGE_PENDING: TX_STAGE_MEMORY_PREPARED,
    TX_STAGE_MEMORY_PREPARED: TX_STAGE_RECEIPT_BOUND,
    TX_STAGE_RECEIPT_BOUND: TX_STAGE_MEMORY_FINALIZED,
    TX_STAGE_MEMORY_FINALIZED: TX_STAGE_CHECKPOINT_VERIFIED,
    TX_STAGE_CHECKPOINT_VERIFIED: TX_STAGE_READY_FOR_CAS,
    TX_STAGE_REGISTRY_COMMITTED: TX_STAGE_RECEIPT_WRITTEN,
    TX_STAGE_RECEIPT_WRITTEN: TX_STAGE_CHAIN_REPLACED,
}
_BEST_EFFORT_STAGE_NEXT = {
    TX_STAGE_PENDING: TX_STAGE_MEMORY_PREPARED,
    TX_STAGE_MEMORY_PREPARED: TX_STAGE_RECEIPT_BOUND,
    TX_STAGE_RECEIPT_BOUND: TX_STAGE_MEMORY_FINALIZED,
    TX_STAGE_MEMORY_FINALIZED: TX_STAGE_READY_FOR_CAS,
    TX_STAGE_REGISTRY_COMMITTED: TX_STAGE_RECEIPT_WRITTEN,
    TX_STAGE_RECEIPT_WRITTEN: TX_STAGE_CHAIN_REPLACED,
}

INIT_STATUS_PENDING = "pending"
INIT_STATUS_COMPLETED = "completed"
INIT_STATUS_ABORTED = "aborted"
ACTIVE_INIT_STATUSES = frozenset({INIT_STATUS_PENDING})

INIT_STAGE_PENDING = "pending"
INIT_STAGE_ROOT_ABSENCE_VERIFIED = "root_absence_verified"
INIT_STAGE_ROOT_VERIFIED = "root_verified"
INIT_STAGE_REGISTRY_INITIALIZED = "registry_initialized"
INIT_STAGE_CHAIN_WRITTEN = "chain_written"
INIT_STAGE_COMPLETED = "completed"
INIT_STAGE_ABORTED = "aborted"
INIT_STAGES: tuple[str, ...] = (
    INIT_STAGE_PENDING,
    INIT_STAGE_ROOT_ABSENCE_VERIFIED,
    INIT_STAGE_ROOT_VERIFIED,
    INIT_STAGE_REGISTRY_INITIALIZED,
    INIT_STAGE_CHAIN_WRITTEN,
    INIT_STAGE_COMPLETED,
    INIT_STAGE_ABORTED,
)
_ACTIVE_INIT_STAGE_ORDER = {
    stage: index
    for index, stage in enumerate(
        (
            INIT_STAGE_PENDING,
            INIT_STAGE_ROOT_ABSENCE_VERIFIED,
            INIT_STAGE_ROOT_VERIFIED,
            INIT_STAGE_REGISTRY_INITIALIZED,
            INIT_STAGE_CHAIN_WRITTEN,
        )
    )
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ABSOLUTE_TEXT_RE = re.compile(r"(?:^|[\s='\"])/(?:[^\s'\"]+)")
_FILE_URI_RE = re.compile(r"(?:^|:)file:", re.IGNORECASE)
_RECOVERY_TOKEN_RE = re.compile(r"^r1:([1-9][0-9]{0,19}):([0-9a-f]{32})$")
_ERROR_TYPE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,127})(?::|$)")
_MAX_JOURNAL_BYTES = 16 * 1024 * 1024
_UNSET = object()


class RoleLineageError(RuntimeError):
    """Base class for fail-closed lineage-registry errors."""


class LineageValidationError(RoleLineageError):
    """A caller supplied a non-canonical or internally inconsistent value."""


class RegistryUnavailableError(RoleLineageError):
    """The machine registry cannot be safely opened or locked."""


class RegistryNotFoundError(RegistryUnavailableError):
    """The registry does not exist and implicit creation was not authorized."""


class RegistryIntegrityError(RoleLineageError):
    """The on-disk registry schema or retained data is inconsistent."""


class LineageNotFoundError(RoleLineageError):
    """No registry row exists for the deterministic lineage ID."""


class LineageAlreadyExistsError(RoleLineageError):
    """Initialization or adoption attempted to replace an existing lineage."""


class LineageConflictError(RoleLineageError):
    """An expected head, sequence, revision, or checkpoint did not match."""


class PendingTransactionError(RoleLineageError):
    """A lineage already has an active pending/recovery transaction."""


class TransactionNotFoundError(RoleLineageError):
    """No durable transaction exists for the requested transaction ID."""


class TransactionStateError(RoleLineageError):
    """A transaction status or stage did not permit the requested mutation."""


class RecoveryClaimError(TransactionStateError):
    """A recovery transaction was not held by the supplied claim token."""


class InitializationConflictError(RoleLineageError):
    """An initialization intent conflicts with durable registry state."""


class InitializationStateError(RoleLineageError):
    """An initialization intent is not at the required exact stage."""


@dataclass(frozen=True)
class LineageKey:
    """Canonical, SUT-neutral identity inputs for one governed use case."""

    namespace: str
    uc: str
    artifact_dir: str

    schema: ClassVar[str] = LINEAGE_KEY_SCHEMA

    def __post_init__(self) -> None:
        _validate_identity_text(self.namespace, "namespace")
        _validate_identity_text(self.uc, "uc")
        _validate_relative_posix_path(self.artifact_dir, "artifact_dir")

    def as_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "namespace": self.namespace,
            "uc": self.uc,
            "artifact_dir": self.artifact_dir,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json(self.as_dict())

    @property
    def lineage_id(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @classmethod
    def from_json(cls, value: str | bytes) -> "LineageKey":
        try:
            parsed = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise RegistryIntegrityError(f"invalid lineage key JSON: {exc}") from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "schema",
            "namespace",
            "uc",
            "artifact_dir",
        }:
            raise RegistryIntegrityError("lineage key JSON has a non-exact schema")
        if parsed.get("schema") != LINEAGE_KEY_SCHEMA:
            raise RegistryIntegrityError("unsupported lineage key schema")
        try:
            key = cls(
                namespace=parsed["namespace"],
                uc=parsed["uc"],
                artifact_dir=parsed["artifact_dir"],
            )
        except LineageValidationError as exc:
            raise RegistryIntegrityError(f"invalid stored lineage key: {exc}") from exc
        if canonical_json(parsed) != key.canonical_bytes:
            raise RegistryIntegrityError("stored lineage key is not canonical JSON")
        return key


@dataclass(frozen=True)
class LineageRecord:
    lineage_id: str
    key: LineageKey
    lifecycle_state: str
    sequence: int
    head_sha256: str
    revision: int
    memory_mode: str
    root_memory_id: str
    checkpoint_memory_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class InitializationRecord:
    init_id: str
    lineage_id: str
    key: LineageKey
    status: str
    stage: str
    lifecycle_state: str
    memory_mode: str
    root_memory_id: str
    error: str
    created_at: str
    updated_at: str
    completed_at: str


@dataclass(frozen=True)
class TransactionRecord:
    tx_id: str
    lineage_id: str
    event: str
    status: str
    stage: str
    expected_head_sha256: str
    expected_sequence: int
    expected_revision: int
    expected_checkpoint_memory_id: str
    target_head_sha256: str
    target_sequence: int
    target_lifecycle_state: str
    transition_payload: bytes | None
    receipt_path: str
    receipt_bytes: bytes | None
    receipt_mode: int | None
    receipt_sha256: str
    transition_memory_id: str
    checkpoint_memory_id: str
    checkpoint_payload: bytes | None
    recovery_token: str
    error: str
    created_at: str
    updated_at: str
    completed_at: str
    recovery_started_at: str


@dataclass(frozen=True)
class CheckpointRecord:
    checkpoint_memory_id: str
    lineage_id: str
    head_sha256: str
    sequence: int
    payload: bytes
    created_at: str


@dataclass(frozen=True)
class HeadCommit:
    lineage: LineageRecord
    transaction: TransactionRecord


@dataclass(frozen=True)
class RecoveryClaim:
    transaction: TransactionRecord
    claim_token: str


@dataclass(frozen=True)
class RecoverySuccessor:
    terminal_transaction: TransactionRecord
    successor_transaction: TransactionRecord
    lineage: LineageRecord


@dataclass(frozen=True)
class LineageSnapshot:
    """One SQLite read-transaction view used by local integrity gates."""

    record: LineageRecord | None
    active_transaction: TransactionRecord | None
    active_initialization: InitializationRecord | None
    checkpoint_payload: bytes | None
    checkpoint_payloads: tuple[bytes, ...]


def utc_now() -> str:
    """Return a stable, UTC RFC-3339 timestamp."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    """Encode a value using BUGate's canonical JSON representation."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_lineage_key(namespace: str, uc: str, artifact_dir: str) -> LineageKey:
    """Validate and retain the exact deterministic lineage inputs."""

    return LineageKey(namespace=namespace, uc=uc, artifact_dir=artifact_dir)


def lineage_id(
    key: LineageKey | None = None,
    *,
    namespace: str | None = None,
    uc: str | None = None,
    artifact_dir: str | None = None,
) -> str:
    """Compute a lineage ID without path, case, or whitespace normalization."""

    if key is not None:
        if any(value is not None for value in (namespace, uc, artifact_dir)):
            raise LineageValidationError(
                "provide either a LineageKey or identity keyword fields, not both"
            )
        if not isinstance(key, LineageKey):
            raise LineageValidationError("key must be a LineageKey")
        return key.lineage_id
    if namespace is None or uc is None or artifact_dir is None:
        raise LineageValidationError(
            "namespace, uc, and artifact_dir are all required"
        )
    return build_lineage_key(namespace, uc, artifact_dir).lineage_id


def lineage_root_payload(key: LineageKey) -> dict[str, Any]:
    """Return the exact deterministic strict-Memory root payload."""

    if not isinstance(key, LineageKey):
        raise LineageValidationError("key must be a LineageKey")
    return {
        "schema": ROLE_LINEAGE_ROOT_SCHEMA,
        "lineage_key": key.as_dict(),
        "lineage_id": key.lineage_id,
    }


def lineage_root_id(key: LineageKey) -> str:
    """Return the SHA-256 content address for a lineage root."""

    return hashlib.sha256(canonical_json(lineage_root_payload(key))).hexdigest()


_CHECKPOINT_FIELDS = frozenset(
    {
        "schema",
        "lineage_key",
        "lineage_id",
        "lineage_root_id",
        "sequence",
        "previous_checkpoint_id",
        "previous_receipt_sha256",
        "receipt_sha256",
        "resulting_state",
        "registry_revision",
        "receipt_envelope",
        "chain_envelope",
    }
)
_EVIDENCE_ENVELOPE_FIELDS = frozenset(
    {"path", "mode", "bytes_sha256", "bytes_base64", "parsed"}
)


def _checkpoint_object(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LineageValidationError(
            f"checkpoint payload is not canonical UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or set(parsed) != _CHECKPOINT_FIELDS:
        raise LineageValidationError(
            "checkpoint payload has a non-exact role-lineage schema"
        )
    if parsed.get("schema") != ROLE_LINEAGE_CHECKPOINT_SCHEMA:
        raise LineageValidationError(
            "checkpoint payload has an unsupported schema"
        )
    return parsed


def _validate_checkpoint_envelope(
    envelope: Any, *, label: str
) -> tuple[str, dict[str, Any]]:
    if not isinstance(envelope, dict) or set(envelope) != _EVIDENCE_ENVELOPE_FIELDS:
        raise LineageValidationError(
            f"checkpoint {label} envelope has a non-exact schema"
        )
    path = _validate_relative_posix_path(
        envelope.get("path"), f"checkpoint {label} envelope path"
    )
    mode = envelope.get("mode")
    if (
        isinstance(mode, bool)
        or not isinstance(mode, int)
        or not 0 <= mode <= 0o7777
    ):
        raise LineageValidationError(
            f"checkpoint {label} envelope mode must be permission bits"
        )
    if mode != 0o600:
        raise LineageValidationError(
            f"checkpoint {label} envelope mode must be exactly 0600"
        )
    expected_hash = _validate_hash(
        envelope.get("bytes_sha256"),
        f"checkpoint {label} envelope bytes_sha256",
    )
    encoded = envelope.get("bytes_base64")
    if not isinstance(encoded, str) or not encoded:
        raise LineageValidationError(
            f"checkpoint {label} envelope bytes_base64 must be non-empty"
        )
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise LineageValidationError(
            f"checkpoint {label} envelope base64 is invalid"
        ) from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise LineageValidationError(
            f"checkpoint {label} envelope base64 is non-canonical"
        )
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise LineageValidationError(
            f"checkpoint {label} envelope byte hash mismatch"
        )
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LineageValidationError(
            f"checkpoint {label} envelope bytes are not UTF-8 JSON"
        ) from exc
    parsed = envelope.get("parsed")
    if not isinstance(decoded, dict) or not isinstance(parsed, dict) or decoded != parsed:
        raise LineageValidationError(
            f"checkpoint {label} envelope parsed value mismatches bytes"
        )
    return path, parsed


def _validate_checkpoint_binding(
    payload: bytes,
    checkpoint_memory_id: str,
    *,
    key: LineageKey,
    root_memory_id: str,
    sequence: int,
    head_sha256: str,
    lifecycle_state: str,
    registry_revision: int,
    previous_checkpoint_memory_id: str,
    previous_receipt_sha256: str,
) -> dict[str, Any]:
    """Validate a checkpoint's content address and exact lineage semantics."""

    _validate_sequence_head(sequence, head_sha256)
    if sequence < 1:
        raise LineageValidationError("checkpoint sequence must be positive")
    _validate_state(lifecycle_state)
    _validate_revision(registry_revision)
    _validate_hash(checkpoint_memory_id, "checkpoint_memory_id")
    actual_id = hashlib.sha256(payload).hexdigest()
    if checkpoint_memory_id != actual_id:
        raise LineageValidationError(
            "checkpoint Memory ID does not match its canonical content SHA-256"
        )
    parsed = _checkpoint_object(payload)
    try:
        checkpoint_key = LineageKey.from_json(canonical_json(parsed["lineage_key"]))
    except (RegistryIntegrityError, TypeError) as exc:
        raise LineageValidationError(
            f"checkpoint lineage key is invalid: {exc}"
        ) from exc
    if checkpoint_key != key:
        raise LineageValidationError("checkpoint lineage key mismatch")
    checks = {
        "lineage_id": key.lineage_id,
        "lineage_root_id": root_memory_id,
        "sequence": sequence,
        "receipt_sha256": head_sha256,
        "resulting_state": lifecycle_state,
        "registry_revision": registry_revision,
    }
    for field, expected in checks.items():
        if parsed.get(field) != expected:
            label = "state" if field == "resulting_state" else field
            raise LineageValidationError(
                f"checkpoint {label} mismatch: expected {expected!r}"
            )
    if parsed.get("lineage_id") != checkpoint_key.lineage_id:
        raise LineageValidationError("checkpoint lineage ID does not match its key")
    expected_root = lineage_root_id(key)
    if root_memory_id != expected_root:
        raise LineageValidationError(
            "checkpoint lineage root is not the deterministic content address"
        )
    previous_checkpoint = parsed.get("previous_checkpoint_id")
    previous_receipt = parsed.get("previous_receipt_sha256")
    _validate_hash(
        previous_checkpoint,
        "previous_checkpoint_id",
        allow_empty=True,
    )
    _validate_hash(
        previous_receipt,
        "previous_receipt_sha256",
        allow_empty=True,
    )
    if sequence == 1 and (previous_checkpoint or previous_receipt):
        raise LineageValidationError(
            "first checkpoint must have empty predecessor IDs"
        )
    if sequence > 1 and (not previous_checkpoint or not previous_receipt):
        raise LineageValidationError(
            "non-root checkpoint must bind both predecessor IDs"
        )
    if previous_checkpoint != previous_checkpoint_memory_id:
        raise LineageValidationError(
            "checkpoint previous checkpoint ID mismatch"
        )
    if previous_receipt != previous_receipt_sha256:
        raise LineageValidationError(
            "checkpoint previous receipt SHA-256 mismatch"
        )
    receipt_path, receipt = _validate_checkpoint_envelope(
        parsed.get("receipt_envelope"), label="receipt"
    )
    chain_path, chain = _validate_checkpoint_envelope(
        parsed.get("chain_envelope"), label="chain"
    )
    if receipt_path == chain_path:
        raise LineageValidationError(
            "checkpoint receipt and chain envelope paths must differ"
        )
    if receipt.get("schema") != ROLE_EVIDENCE_SCHEMA:
        raise LineageValidationError(
            "checkpoint receipt envelope has an unsupported role-evidence schema"
        )
    if chain.get("schema") != ROLE_CHAIN_SCHEMA:
        raise LineageValidationError(
            "checkpoint chain envelope has an unsupported role-chain schema"
        )
    artifact_path = PurePosixPath(key.artifact_dir)
    receipt_posix = PurePosixPath(receipt_path)
    chain_posix = PurePosixPath(chain_path)
    try:
        receipt_relative = receipt_posix.relative_to(artifact_path)
        chain_relative = chain_posix.relative_to(artifact_path)
    except ValueError as exc:
        raise LineageValidationError(
            "checkpoint receipt and chain paths must be under lineage artifact_dir"
        ) from exc
    if not receipt_relative.parts or not chain_relative.parts:
        raise LineageValidationError(
            "checkpoint evidence paths must be descendants of lineage artifact_dir"
        )
    if chain_posix.name != "chain.json":
        raise LineageValidationError(
            "checkpoint chain envelope basename must be chain.json"
        )
    if receipt_posix.parent.name != "receipts":
        raise LineageValidationError(
            "checkpoint receipt envelope parent must be receipts"
        )
    evidence_dir = chain_posix.parent
    if evidence_dir == artifact_path or receipt_posix.parent.parent != evidence_dir:
        raise LineageValidationError(
            "checkpoint receipt and chain must share one evidence directory"
        )
    receipt_checks = {
        "sequence": sequence,
        "previous_receipt_sha256": previous_receipt_sha256,
        "receipt_sha256": head_sha256,
        "resulting_state": lifecycle_state,
        "uc": key.uc,
        "artifact_dir": key.artifact_dir,
    }
    for field, expected in receipt_checks.items():
        if receipt.get(field) != expected:
            raise LineageValidationError(
                f"checkpoint receipt envelope {field} mismatch"
            )
    event = _validate_state(receipt.get("event"), "checkpoint receipt event")
    expected_receipt_name = (
        f"{sequence:06d}-{event.replace('_', '-')}-{head_sha256}.json"
    )
    if receipt_posix.name != expected_receipt_name:
        raise LineageValidationError(
            "checkpoint receipt envelope filename does not bind event/sequence/hash"
        )
    chain_checks = {
        "sequence": sequence,
        "head_sha256": head_sha256,
        "state": lifecycle_state,
    }
    for field, expected in chain_checks.items():
        if chain.get(field) != expected:
            raise LineageValidationError(
                f"checkpoint chain envelope {field} mismatch"
            )
    latest_receipts = chain.get("latest_receipts")
    if not isinstance(latest_receipts, dict) or latest_receipts.get(event) != receipt_path:
        raise LineageValidationError(
            "checkpoint chain latest receipt does not bind its receipt envelope path"
        )
    return parsed


def memory_home(
    environ: Mapping[str, str] | None = None,
    *,
    explicit: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the absolute machine-level Memory home without creating it.

    Precedence is ``MCP_MEMORY_BASE_DIR`` > ``BUGATE_MEMORY_HOME`` > the
    per-user default.  An explicit value exists for isolated tests/operators
    and is subject to the same absolute-path requirement.
    """

    if explicit is not None:
        raw = os.fspath(explicit)
    else:
        source = os.environ if environ is None else environ
        raw = ""
        for name in ("MCP_MEMORY_BASE_DIR", "BUGATE_MEMORY_HOME"):
            candidate = source.get(name, "")
            if isinstance(candidate, str) and candidate.strip():
                raw = candidate.strip()
                break
        if not raw:
            raw = os.fspath(DEFAULT_MEMORY_HOME)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise LineageValidationError("effective Memory home must be a path string")
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise LineageValidationError(
            "effective Memory home must be absolute; relative registry homes fail closed"
        )
    # Normalize lexical ``..`` without resolving or changing permitted parent
    # symlink semantics.  The database leaf itself is checked separately.
    return Path(os.path.normpath(os.fspath(expanded)))


def registry_path(
    home: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the fixed registry leaf under the effective Memory home."""

    base = memory_home(environ, explicit=home) if home is not None else memory_home(environ)
    return base / REGISTRY_FILENAME


def _canonical_object_payload(
    value: Mapping[str, Any] | bytes, *, field: str
) -> bytes:

    if isinstance(value, bytes):
        if not value or len(value) > _MAX_JOURNAL_BYTES:
            raise LineageValidationError(f"{field} has an invalid size")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LineageValidationError(
                f"{field} must be canonical UTF-8 JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict) or canonical_json(parsed) != value:
            raise LineageValidationError(
                f"{field} must be a canonical JSON object"
            )
        return value
    if not isinstance(value, Mapping):
        raise LineageValidationError(f"{field} must be an object or bytes")
    encoded = canonical_json(dict(value))
    if not encoded or len(encoded) > _MAX_JOURNAL_BYTES:
        raise LineageValidationError(f"{field} has an invalid size")
    return encoded


def canonical_checkpoint_payload(value: Mapping[str, Any] | bytes) -> bytes:
    """Validate or create an exact canonical JSON checkpoint envelope."""

    return _canonical_object_payload(value, field="checkpoint payload")


def canonical_transition_payload(value: Mapping[str, Any] | bytes) -> bytes:
    """Validate or create exact canonical transition JSON for crash replay."""

    return _canonical_object_payload(value, field="transition payload")


def classify_integrity(
    lineage: LineageRecord | None,
    *,
    local_sequence: int | None,
    local_head_sha256: str | None,
    has_local_history: bool,
    active_transaction: TransactionRecord | None = None,
    active_initialization: InitializationRecord | None = None,
    registry_available: bool = True,
) -> str:
    """Classify registry/local alignment independently from lifecycle state.

    ``None`` for either local head field means the local chain is absent or
    unreadable.  Callers decide whether an unregistered local workspace has
    legacy history; this function never guesses from an empty directory.
    """

    if not registry_available:
        return "registry_unavailable"
    if active_initialization is not None:
        if active_initialization.status not in ACTIVE_INIT_STATUSES:
            raise LineageValidationError(
                "active initialization has a terminal status"
            )
        if (
            lineage is not None
            and active_initialization.lineage_id != lineage.lineage_id
        ):
            raise LineageValidationError(
                "active initialization belongs to a different lineage"
            )
        return "recovery_pending"
    if lineage is None:
        return "migration_required" if has_local_history else "uninitialized"
    if active_transaction is not None:
        if active_transaction.lineage_id != lineage.lineage_id:
            raise LineageValidationError(
                "active transaction belongs to a different lineage"
            )
        if active_transaction.status not in ACTIVE_TX_STATUSES:
            raise LineageValidationError("active transaction has a terminal status")
        return "recovery_pending"
    if local_sequence is None or local_head_sha256 is None:
        return "history_missing"
    try:
        _validate_sequence_head(local_sequence, local_head_sha256)
    except LineageValidationError:
        return "history_diverged"
    if (
        local_sequence != lineage.sequence
        or local_head_sha256 != lineage.head_sha256
    ):
        return "history_diverged"
    return "aligned"


def _validate_identity_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise LineageValidationError(f"{field} must be a non-empty exact string")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise LineageValidationError(f"{field} must not contain control separators")
    if (
        value.startswith(("/", "\\"))
        or _FILE_URI_RE.search(value)
        or _ABSOLUTE_TEXT_RE.search(value)
        or re.search(r"(?:^|:)\/(?:[^/]|$)", value)
        or re.search(r"(?:^|:)[A-Za-z]:[\\/]", value)
        or re.search(r"(?:^|:)[\\/]{2}[^\\/]", value)
        or PureWindowsPath(value).is_absolute()
    ):
        raise LineageValidationError(
            f"{field} must not contain an absolute path"
        )
    return value


def _validate_relative_posix_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise LineageValidationError(f"{field} must be a non-empty POSIX path")
    if "\x00" in value or "\\" in value:
        raise LineageValidationError(f"{field} must be a POSIX path")
    if _FILE_URI_RE.search(value):
        raise LineageValidationError(f"{field} must not use a file URI")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise LineageValidationError(f"{field} must be workspace-relative")
    if value in {".", ".."} or ".." in posix.parts:
        raise LineageValidationError(f"{field} must not escape the workspace")
    if posix.as_posix() != value:
        raise LineageValidationError(f"{field} must be a canonical POSIX path")
    return value


def _validate_state(value: Any, field: str = "lifecycle_state") -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise LineageValidationError(f"{field} is not a safe non-empty state token")
    return value


def _validate_tx_id(value: Any, field: str = "tx_id") -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise LineageValidationError(f"{field} is not a safe transaction token")
    return value


def _validate_hash(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise LineageValidationError(
            f"{field} must be a lowercase 64-character SHA-256"
        )
    return value


def _validate_sequence_head(sequence: Any, head_sha256: Any) -> None:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise LineageValidationError("sequence must be a non-negative integer")
    if sequence == 0:
        if head_sha256 != "":
            raise LineageValidationError("sequence zero requires an empty head")
    else:
        _validate_hash(head_sha256, "head_sha256")


def _validate_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LineageValidationError("revision must be a non-negative integer")
    return value


def _validate_memory_mode(value: Any) -> str:
    if value not in MEMORY_MODES:
        raise LineageValidationError(
            "memory_mode must be exactly 'best_effort' or 'required'"
        )
    return value


def _validate_opaque_id(value: Any, field: str, *, allow_empty: bool = True) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise LineageValidationError(f"{field} must be a bounded non-empty string")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise LineageValidationError(f"{field} contains a forbidden separator")
    return value


def _validate_error(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise LineageValidationError("transaction error must be a bounded string")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise LineageValidationError("transaction error must be one line")
    if _ABSOLUTE_TEXT_RE.search(value):
        raise LineageValidationError(
            "transaction errors must not persist absolute filesystem paths"
        )
    return value


def _stable_recovery_error(value: str | BaseException) -> str:
    """Reduce recovery failures to a path-free, bounded machine category.

    Recovery cleanup must not fail merely because an exception message embeds
    a workspace path.  Only the exception type (or a caller-supplied safe
    category token) survives; free-form message text is deliberately dropped.
    """

    if isinstance(value, BaseException):
        category = type(value).__name__
    elif isinstance(value, str):
        if value.startswith("recovery_error:"):
            candidate = value.removeprefix("recovery_error:")
            category = (
                candidate
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", candidate)
                else "unspecified"
            )
        else:
            match = _ERROR_TYPE_RE.match(value)
            category = match.group(1) if match is not None else "unspecified"
    else:
        category = "unspecified"
    diagnostic = f"recovery_error:{category}"
    _validate_error(diagnostic)
    return diagnostic


def _new_recovery_token() -> str:
    return f"r1:{os.getpid()}:{uuid.uuid4().hex}"


def _recovery_owner_pid(token: str) -> int:
    match = _RECOVERY_TOKEN_RE.fullmatch(token)
    if match is None:
        raise RegistryIntegrityError("stored recovery claim token is malformed")
    return int(match.group(1))


def _pid_is_alive(pid: int) -> bool:
    """Conservatively test claimant liveness without taking ownership.

    PID reuse can only create a false-positive live result, which blocks
    takeover rather than allowing two live claimants.  That is the required
    fail-closed direction for this local recovery lease.
    """

    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _validate_mode(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LineageValidationError("receipt_mode must be an integer")
    if value < 0 or value > 0o777:
        raise LineageValidationError("receipt_mode must contain permission bits only")
    return value


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lineages (
    lineage_id TEXT PRIMARY KEY,
    key_schema TEXT NOT NULL,
    key_json TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL,
    uc TEXT NOT NULL,
    artifact_dir TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    head_sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    memory_mode TEXT NOT NULL CHECK (memory_mode IN ('best_effort', 'required')),
    root_memory_id TEXT NOT NULL,
    checkpoint_memory_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (sequence = 0 AND head_sha256 = '') OR
        (sequence > 0 AND length(head_sha256) = 64)
    ),
    CHECK (
        memory_mode = 'best_effort' OR
        (
            root_memory_id <> '' AND
            (sequence = 0 OR checkpoint_memory_id <> '')
        )
    )
);

CREATE TABLE IF NOT EXISTS lineage_initializations (
    init_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    key_schema TEXT NOT NULL,
    key_json TEXT NOT NULL,
    namespace TEXT NOT NULL,
    uc TEXT NOT NULL,
    artifact_dir TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','completed','aborted')),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'pending','root_absence_verified','root_verified',
            'registry_initialized','chain_written','completed','aborted'
        )
    ),
    lifecycle_state TEXT NOT NULL,
    memory_mode TEXT NOT NULL CHECK (memory_mode IN ('best_effort', 'required')),
    root_memory_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    UNIQUE(lineage_id),
    UNIQUE(key_json)
);

CREATE UNIQUE INDEX IF NOT EXISTS lineage_one_active_initialization
ON lineage_initializations(lineage_id)
WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS lineage_transactions (
    tx_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id) ON DELETE RESTRICT,
    event TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','recovering','completed','aborted')),
    stage TEXT NOT NULL,
    expected_head_sha256 TEXT NOT NULL,
    expected_sequence INTEGER NOT NULL CHECK (expected_sequence >= 0),
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    expected_checkpoint_memory_id TEXT NOT NULL,
    target_head_sha256 TEXT NOT NULL DEFAULT '',
    target_sequence INTEGER NOT NULL CHECK (target_sequence > 0),
    target_lifecycle_state TEXT NOT NULL,
    transition_payload BLOB,
    receipt_path TEXT NOT NULL DEFAULT '',
    receipt_bytes BLOB,
    receipt_mode INTEGER,
    receipt_sha256 TEXT NOT NULL DEFAULT '',
    transition_memory_id TEXT NOT NULL DEFAULT '',
    checkpoint_memory_id TEXT NOT NULL DEFAULT '',
    checkpoint_payload BLOB,
    recovery_token TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    recovery_started_at TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS lineage_one_active_transaction
ON lineage_transactions(lineage_id)
WHERE status IN ('pending','recovering');

CREATE INDEX IF NOT EXISTS lineage_transactions_checkpoint
ON lineage_transactions(checkpoint_memory_id);

CREATE TABLE IF NOT EXISTS lineage_checkpoints (
    checkpoint_memory_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id) ON DELETE RESTRICT,
    head_sha256 TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    payload BLOB NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(lineage_id, head_sha256, sequence)
);

CREATE INDEX IF NOT EXISTS lineage_checkpoints_head
ON lineage_checkpoints(lineage_id, head_sha256, sequence);
"""

_REQUIRED_TABLES = frozenset(
    {
        "lineages",
        "lineage_initializations",
        "lineage_transactions",
        "lineage_checkpoints",
    }
)


class LineageRegistry:
    """SQLite-backed compare-and-swap registry for role-evidence lineages.

    ``create`` defaults to ``False`` so status/read paths never manufacture an
    empty authority.  Use ``create=True`` only for an explicit lineage
    initialization/adoption workflow in an isolated, already-selected Memory
    home.
    """

    def __init__(
        self,
        home: str | os.PathLike[str] | None = None,
        *,
        create: bool = False,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms < 1
            or busy_timeout_ms > 120_000
        ):
            raise LineageValidationError("busy_timeout_ms must be between 1 and 120000")
        self.home = memory_home(explicit=home) if home is not None else memory_home()
        self.path = self.home / REGISTRY_FILENAME
        self.busy_timeout_ms = busy_timeout_ms
        if create:
            self._precreate()
            self._initialize_schema()
        else:
            self._require_existing_leaf()
            self._validate_schema()

    def _require_existing_leaf(self) -> None:
        try:
            info = self.path.lstat()
        except FileNotFoundError as exc:
            raise RegistryNotFoundError(
                "role lineage registry is not initialized"
            ) from exc
        except OSError as exc:
            raise RegistryUnavailableError(
                f"cannot inspect role lineage registry: {exc.strerror or exc}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise RegistryIntegrityError("role lineage registry leaf must not be a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise RegistryIntegrityError("role lineage registry leaf must be a regular file")

    def _precreate(self) -> None:
        try:
            self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise RegistryUnavailableError(
                f"cannot create machine Memory home: {exc.strerror or exc}"
            ) from exc
        if not self.home.is_dir():
            raise RegistryUnavailableError("effective Memory home is not a directory")
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise RegistryUnavailableError(
                f"cannot inspect role lineage registry: {exc.strerror or exc}"
            ) from exc
        if info is not None and stat.S_ISLNK(info.st_mode):
            raise RegistryIntegrityError("role lineage registry leaf must not be a symlink")
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise RegistryIntegrityError("role lineage registry leaf must be a regular file")
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise RegistryUnavailableError(
                f"cannot precreate role lineage registry: {exc.strerror or exc}"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise RegistryIntegrityError(
                    "opened role lineage registry leaf is not a regular file"
                )
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._require_existing_leaf()

    def _connect(self, *, readonly: bool) -> sqlite3.Connection:
        self._require_existing_leaf()
        connection: sqlite3.Connection | None = None
        try:
            mode = "ro" if readonly else "rw"
            connection = sqlite3.connect(
                f"{self.path.as_uri()}?mode={mode}&nofollow=1",
                uri=True,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            if readonly:
                connection.execute("PRAGMA query_only=ON")
            else:
                mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
                if mode.lower() != "wal":
                    raise RegistryIntegrityError("SQLite registry did not enter WAL mode")
                connection.execute("PRAGMA synchronous=FULL")
            return connection
        except RegistryIntegrityError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise self._sqlite_error("cannot open role lineage registry", exc) from exc

    @staticmethod
    def _sqlite_error(prefix: str, exc: sqlite3.Error) -> RoleLineageError:
        message = str(exc).lower()
        if "locked" in message or "busy" in message or "unable to open" in message:
            return RegistryUnavailableError(f"{prefix}: {exc}")
        return RegistryIntegrityError(f"{prefix}: {exc}")

    @contextmanager
    def _writer(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect(readonly=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except RoleLineageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise self._sqlite_error("role lineage registry write failed", exc) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        connection = self._connect(readonly=False)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, REGISTRY_SCHEMA_VERSION):
                raise RegistryIntegrityError(
                    f"unsupported role lineage registry schema version {version}"
                )
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + _SCHEMA_SQL
                + f"\nPRAGMA user_version={REGISTRY_SCHEMA_VERSION};\nCOMMIT;"
            )
        except RoleLineageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise self._sqlite_error("cannot initialize role lineage registry", exc) from exc
        finally:
            connection.close()
        try:
            os.chmod(self.path, 0o600, follow_symlinks=False)
        except OSError as exc:
            raise RegistryUnavailableError(
                f"cannot secure role lineage registry mode: {exc.strerror or exc}"
            ) from exc
        self._validate_schema()

    def _validate_schema(self) -> None:
        connection = self._connect(readonly=True)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != REGISTRY_SCHEMA_VERSION:
                raise RegistryIntegrityError(
                    f"unsupported role lineage registry schema version {version}"
                )
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            names = {str(row["name"]) for row in rows}
            missing = sorted(_REQUIRED_TABLES - names)
            if missing:
                raise RegistryIntegrityError(
                    "role lineage registry tables missing: " + ", ".join(missing)
                )
            fk = connection.execute("PRAGMA foreign_key_check").fetchone()
            if fk is not None:
                raise RegistryIntegrityError("role lineage registry foreign-key violation")
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                raise RegistryIntegrityError(
                    f"role lineage registry quick_check failed: {integrity}"
                )
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot validate role lineage registry", exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _lineage_from_row(row: sqlite3.Row) -> LineageRecord:
        key = LineageKey.from_json(str(row["key_json"]))
        record = LineageRecord(
            lineage_id=str(row["lineage_id"]),
            key=key,
            lifecycle_state=str(row["lifecycle_state"]),
            sequence=int(row["sequence"]),
            head_sha256=str(row["head_sha256"]),
            revision=int(row["revision"]),
            memory_mode=str(row["memory_mode"]),
            root_memory_id=str(row["root_memory_id"]),
            checkpoint_memory_id=str(row["checkpoint_memory_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        if record.lineage_id != key.lineage_id:
            raise RegistryIntegrityError("stored lineage ID does not match its key")
        if str(row["key_schema"]) != LINEAGE_KEY_SCHEMA:
            raise RegistryIntegrityError("stored lineage key schema is unsupported")
        if (
            str(row["namespace"]) != key.namespace
            or str(row["uc"]) != key.uc
            or str(row["artifact_dir"]) != key.artifact_dir
        ):
            raise RegistryIntegrityError("stored lineage key columns diverge from key JSON")
        try:
            _validate_state(record.lifecycle_state)
            _validate_sequence_head(record.sequence, record.head_sha256)
            _validate_revision(record.revision)
            _validate_memory_mode(record.memory_mode)
            _validate_opaque_id(record.root_memory_id, "root_memory_id")
            _validate_opaque_id(record.checkpoint_memory_id, "checkpoint_memory_id")
            if record.root_memory_id:
                _validate_hash(record.root_memory_id, "root_memory_id")
            if record.checkpoint_memory_id:
                _validate_hash(
                    record.checkpoint_memory_id, "checkpoint_memory_id"
                )
        except LineageValidationError as exc:
            raise RegistryIntegrityError(f"invalid stored lineage row: {exc}") from exc
        if record.memory_mode == "required" and not record.root_memory_id:
            raise RegistryIntegrityError("required lineage is missing its Memory root")
        if (
            record.memory_mode == "required"
            and record.root_memory_id != lineage_root_id(record.key)
        ):
            raise RegistryIntegrityError(
                "required lineage root is not its deterministic content address"
            )
        if (
            record.memory_mode == "required"
            and record.sequence > 0
            and not record.checkpoint_memory_id
        ):
            raise RegistryIntegrityError(
                "required positive-sequence lineage is missing its checkpoint"
            )
        if (
            record.memory_mode == "required"
            and record.sequence == 0
            and record.checkpoint_memory_id
        ):
            raise RegistryIntegrityError(
                "required sequence-zero lineage must not retain a checkpoint"
            )
        if record.memory_mode == "best_effort" and (
            record.root_memory_id or record.checkpoint_memory_id
        ):
            raise RegistryIntegrityError(
                "best_effort lineage must not retain Memory root/checkpoint IDs"
            )
        return record

    @staticmethod
    def _initialization_from_row(row: sqlite3.Row) -> InitializationRecord:
        key = LineageKey.from_json(str(row["key_json"]))
        record = InitializationRecord(
            init_id=str(row["init_id"]),
            lineage_id=str(row["lineage_id"]),
            key=key,
            status=str(row["status"]),
            stage=str(row["stage"]),
            lifecycle_state=str(row["lifecycle_state"]),
            memory_mode=str(row["memory_mode"]),
            root_memory_id=str(row["root_memory_id"]),
            error=str(row["error"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=str(row["completed_at"]),
        )
        try:
            _validate_tx_id(record.init_id, "init_id")
            _validate_hash(record.lineage_id, "lineage_id")
            _validate_state(record.lifecycle_state)
            _validate_memory_mode(record.memory_mode)
            _validate_opaque_id(record.root_memory_id, "root_memory_id")
            _validate_error(record.error)
        except LineageValidationError as exc:
            raise RegistryIntegrityError(
                f"invalid stored initialization row: {exc}"
            ) from exc
        if record.lineage_id != key.lineage_id:
            raise RegistryIntegrityError(
                "stored initialization lineage ID does not match its key"
            )
        if str(row["key_schema"]) != LINEAGE_KEY_SCHEMA:
            raise RegistryIntegrityError(
                "stored initialization key schema is unsupported"
            )
        if (
            str(row["namespace"]) != key.namespace
            or str(row["uc"]) != key.uc
            or str(row["artifact_dir"]) != key.artifact_dir
        ):
            raise RegistryIntegrityError(
                "stored initialization key columns diverge from key JSON"
            )
        if record.status not in {
            INIT_STATUS_PENDING,
            INIT_STATUS_COMPLETED,
            INIT_STATUS_ABORTED,
        }:
            raise RegistryIntegrityError(
                "stored initialization has an unknown status"
            )
        if record.stage not in INIT_STAGES:
            raise RegistryIntegrityError(
                "stored initialization has an unknown stage"
            )
        if record.status == INIT_STATUS_PENDING and record.stage not in _ACTIVE_INIT_STAGE_ORDER:
            raise RegistryIntegrityError(
                "pending initialization has a terminal stage"
            )
        if record.status == INIT_STATUS_COMPLETED and record.stage != INIT_STAGE_COMPLETED:
            raise RegistryIntegrityError(
                "completed initialization has a non-completed stage"
            )
        if record.status == INIT_STATUS_ABORTED and record.stage != INIT_STAGE_ABORTED:
            raise RegistryIntegrityError(
                "aborted initialization has a non-aborted stage"
            )
        root_is_bound = (
            record.stage
            in {
                INIT_STAGE_ROOT_VERIFIED,
                INIT_STAGE_REGISTRY_INITIALIZED,
                INIT_STAGE_CHAIN_WRITTEN,
                INIT_STAGE_COMPLETED,
            }
        )
        if record.memory_mode == "required" and root_is_bound and not record.root_memory_id:
            raise RegistryIntegrityError(
                "required initialization lost its verified Memory root"
            )
        if (
            record.memory_mode == "required"
            and root_is_bound
            and record.root_memory_id != lineage_root_id(record.key)
        ):
            raise RegistryIntegrityError(
                "required initialization root is not its deterministic content address"
            )
        if record.memory_mode == "best_effort" and record.root_memory_id:
            raise RegistryIntegrityError(
                "best-effort initialization unexpectedly stores a Memory root"
            )
        if not root_is_bound and record.root_memory_id:
            raise RegistryIntegrityError(
                "initialization stores a root before root verification"
            )
        return record

    @staticmethod
    def _transaction_from_row(row: sqlite3.Row) -> TransactionRecord:
        receipt = row["receipt_bytes"]
        checkpoint = row["checkpoint_payload"]
        record = TransactionRecord(
            tx_id=str(row["tx_id"]),
            lineage_id=str(row["lineage_id"]),
            event=str(row["event"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            expected_head_sha256=str(row["expected_head_sha256"]),
            expected_sequence=int(row["expected_sequence"]),
            expected_revision=int(row["expected_revision"]),
            expected_checkpoint_memory_id=str(row["expected_checkpoint_memory_id"]),
            target_head_sha256=str(row["target_head_sha256"]),
            target_sequence=int(row["target_sequence"]),
            target_lifecycle_state=str(row["target_lifecycle_state"]),
            transition_payload=(
                bytes(row["transition_payload"])
                if row["transition_payload"] is not None
                else None
            ),
            receipt_path=str(row["receipt_path"]),
            receipt_bytes=bytes(receipt) if receipt is not None else None,
            receipt_mode=int(row["receipt_mode"]) if row["receipt_mode"] is not None else None,
            receipt_sha256=str(row["receipt_sha256"]),
            transition_memory_id=str(row["transition_memory_id"]),
            checkpoint_memory_id=str(row["checkpoint_memory_id"]),
            checkpoint_payload=bytes(checkpoint) if checkpoint is not None else None,
            recovery_token=str(row["recovery_token"]),
            error=str(row["error"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=str(row["completed_at"]),
            recovery_started_at=str(row["recovery_started_at"]),
        )
        try:
            _validate_tx_id(record.tx_id)
            _validate_hash(record.lineage_id, "lineage_id")
            _validate_state(record.event, "event")
        except LineageValidationError as exc:
            raise RegistryIntegrityError(f"invalid stored transaction row: {exc}") from exc
        if record.status not in {
            TX_STATUS_PENDING,
            TX_STATUS_RECOVERING,
            TX_STATUS_COMPLETED,
            TX_STATUS_ABORTED,
        }:
            raise RegistryIntegrityError("stored transaction has an unknown status")
        if record.stage not in TX_STAGES:
            raise RegistryIntegrityError("stored transaction has an unknown stage")
        if record.status == TX_STATUS_RECOVERING:
            _recovery_owner_pid(record.recovery_token)
            if not record.recovery_started_at:
                raise RegistryIntegrityError(
                    "recovering transaction is missing its claim timestamp"
                )
        elif record.recovery_token:
            raise RegistryIntegrityError(
                "non-recovering transaction retains a recovery claim token"
            )
        try:
            _validate_sequence_head(
                record.expected_sequence, record.expected_head_sha256
            )
            _validate_revision(record.expected_revision)
            _validate_opaque_id(
                record.expected_checkpoint_memory_id,
                "expected_checkpoint_memory_id",
            )
            if record.expected_checkpoint_memory_id:
                _validate_hash(
                    record.expected_checkpoint_memory_id,
                    "expected_checkpoint_memory_id",
                )
        except LineageValidationError as exc:
            raise RegistryIntegrityError(f"invalid stored transaction row: {exc}") from exc
        if record.target_sequence != record.expected_sequence + 1:
            raise RegistryIntegrityError("stored transaction target sequence is not exact")
        try:
            _validate_state(record.target_lifecycle_state)
            if record.target_head_sha256:
                _validate_hash(record.target_head_sha256, "target_head_sha256")
            if record.transition_payload is not None:
                canonical_transition_payload(record.transition_payload)
            if record.transition_memory_id:
                _validate_opaque_id(
                    record.transition_memory_id,
                    "transition_memory_id",
                    allow_empty=False,
                )
            if record.checkpoint_memory_id:
                _validate_hash(
                    record.checkpoint_memory_id, "checkpoint_memory_id"
                )
            if record.checkpoint_payload is not None:
                canonical_checkpoint_payload(record.checkpoint_payload)
        except LineageValidationError as exc:
            raise RegistryIntegrityError(f"invalid stored transaction row: {exc}") from exc
        return record

    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row) -> CheckpointRecord:
        payload = bytes(row["payload"])
        try:
            canonical_checkpoint_payload(payload)
        except LineageValidationError as exc:
            raise RegistryIntegrityError(
                f"invalid retained checkpoint payload: {exc}"
            ) from exc
        record = CheckpointRecord(
            checkpoint_memory_id=str(row["checkpoint_memory_id"]),
            lineage_id=str(row["lineage_id"]),
            head_sha256=str(row["head_sha256"]),
            sequence=int(row["sequence"]),
            payload=payload,
            created_at=str(row["created_at"]),
        )
        try:
            _validate_opaque_id(
                record.checkpoint_memory_id, "checkpoint_memory_id", allow_empty=False
            )
            _validate_hash(record.lineage_id, "lineage_id")
            _validate_sequence_head(record.sequence, record.head_sha256)
        except LineageValidationError as exc:
            raise RegistryIntegrityError(
                f"invalid retained checkpoint row: {exc}"
            ) from exc
        try:
            parsed = _checkpoint_object(payload)
            key = LineageKey.from_json(canonical_json(parsed["lineage_key"]))
            _validate_checkpoint_binding(
                payload,
                record.checkpoint_memory_id,
                key=key,
                root_memory_id=str(parsed.get("lineage_root_id") or ""),
                sequence=record.sequence,
                head_sha256=record.head_sha256,
                lifecycle_state=str(parsed.get("resulting_state") or ""),
                registry_revision=parsed.get("registry_revision"),
                previous_checkpoint_memory_id=str(
                    parsed.get("previous_checkpoint_id") or ""
                ),
                previous_receipt_sha256=str(
                    parsed.get("previous_receipt_sha256") or ""
                ),
            )
            if record.lineage_id != key.lineage_id:
                raise LineageValidationError(
                    "retained checkpoint lineage ID mismatch"
                )
        except (LineageValidationError, RegistryIntegrityError) as exc:
            raise RegistryIntegrityError(
                f"invalid retained checkpoint binding: {exc}"
            ) from exc
        return record

    @staticmethod
    def _select_lineage(
        connection: sqlite3.Connection, lineage_id_value: str
    ) -> LineageRecord | None:
        row = connection.execute(
            "SELECT * FROM lineages WHERE lineage_id=?", (lineage_id_value,)
        ).fetchone()
        return LineageRegistry._lineage_from_row(row) if row is not None else None

    @staticmethod
    def _select_initialization(
        connection: sqlite3.Connection, init_id: str
    ) -> InitializationRecord | None:
        row = connection.execute(
            "SELECT * FROM lineage_initializations WHERE init_id=?", (init_id,)
        ).fetchone()
        return (
            LineageRegistry._initialization_from_row(row)
            if row is not None
            else None
        )

    @staticmethod
    def _require_initialized_empty_lineage(
        connection: sqlite3.Connection, intent: InitializationRecord
    ) -> LineageRecord:
        lineage = LineageRegistry._select_lineage(connection, intent.lineage_id)
        if lineage is None:
            raise RegistryIntegrityError(
                "initialized journal lost its registry lineage"
            )
        if (
            lineage.key != intent.key
            or lineage.lifecycle_state != intent.lifecycle_state
            or lineage.memory_mode != intent.memory_mode
            or lineage.root_memory_id != intent.root_memory_id
            or lineage.sequence != 0
            or lineage.head_sha256 != ""
            or lineage.revision != 0
            or lineage.checkpoint_memory_id != ""
        ):
            raise RegistryIntegrityError(
                "initialized journal diverges from its registry lineage"
            )
        return lineage

    @staticmethod
    def _select_transaction(
        connection: sqlite3.Connection, tx_id: str
    ) -> TransactionRecord | None:
        row = connection.execute(
            "SELECT * FROM lineage_transactions WHERE tx_id=?", (tx_id,)
        ).fetchone()
        return LineageRegistry._transaction_from_row(row) if row is not None else None

    @staticmethod
    def _coerce_lineage_id(value: str | LineageKey) -> str:
        if isinstance(value, LineageKey):
            return value.lineage_id
        return _validate_hash(value, "lineage_id")

    @staticmethod
    def _validate_current_checkpoint(
        connection: sqlite3.Connection, lineage: LineageRecord
    ) -> CheckpointRecord | None:
        if not lineage.checkpoint_memory_id:
            return None
        row = connection.execute(
            "SELECT * FROM lineage_checkpoints WHERE checkpoint_memory_id=?",
            (lineage.checkpoint_memory_id,),
        ).fetchone()
        if row is None:
            raise RegistryIntegrityError(
                "lineage current checkpoint payload is not retained"
            )
        checkpoint = LineageRegistry._checkpoint_from_row(row)
        if (
            checkpoint.lineage_id != lineage.lineage_id
            or checkpoint.head_sha256 != lineage.head_sha256
            or checkpoint.sequence != lineage.sequence
        ):
            raise RegistryIntegrityError(
                "lineage current checkpoint does not bind its exact head"
            )
        try:
            _validate_checkpoint_binding(
                checkpoint.payload,
                checkpoint.checkpoint_memory_id,
                key=lineage.key,
                root_memory_id=lineage.root_memory_id,
                sequence=lineage.sequence,
                head_sha256=lineage.head_sha256,
                lifecycle_state=lineage.lifecycle_state,
                registry_revision=lineage.revision,
                previous_checkpoint_memory_id=str(
                    _checkpoint_object(checkpoint.payload).get(
                        "previous_checkpoint_id"
                    )
                    or ""
                ),
                previous_receipt_sha256=str(
                    _checkpoint_object(checkpoint.payload).get(
                        "previous_receipt_sha256"
                    )
                    or ""
                ),
            )
        except LineageValidationError as exc:
            raise RegistryIntegrityError(
                f"lineage current checkpoint semantics diverge: {exc}"
            ) from exc
        return checkpoint

    def get_lineage(self, value: str | LineageKey) -> LineageRecord | None:
        """Read one lineage without creating or updating registry state."""

        wanted = self._coerce_lineage_id(value)
        connection = self._connect(readonly=True)
        try:
            record = self._select_lineage(connection, wanted)
            if record is not None:
                if isinstance(value, LineageKey) and record.key != value:
                    raise RegistryIntegrityError("lineage ID collision with a different key")
                self._validate_current_checkpoint(connection, record)
            return record
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot read role lineage", exc) from exc
        finally:
            connection.close()

    def get_integrity_snapshot(self, value: str | LineageKey) -> LineageSnapshot:
        """Read lineage, active journals, and checkpoint from one DB snapshot.

        The explicit deferred transaction is required even in WAL mode: without
        it, three independent reads can observe an old head followed by a newly
        completed transaction and briefly misclassify stale workspace evidence
        as aligned.
        """

        wanted = self._coerce_lineage_id(value)
        connection = self._connect(readonly=True)
        try:
            connection.execute("BEGIN")
            record = self._select_lineage(connection, wanted)
            checkpoint: CheckpointRecord | None = None
            if record is not None:
                if isinstance(value, LineageKey) and record.key != value:
                    raise RegistryIntegrityError(
                        "lineage ID collision with a different key"
                    )
                checkpoint = self._validate_current_checkpoint(connection, record)

            transaction_rows = connection.execute(
                """
                SELECT * FROM lineage_transactions
                WHERE lineage_id=? AND status IN ('pending','recovering')
                ORDER BY created_at, tx_id
                """,
                (wanted,),
            ).fetchall()
            initialization_rows = connection.execute(
                """
                SELECT * FROM lineage_initializations
                WHERE lineage_id=? AND status='pending'
                ORDER BY created_at, init_id
                """,
                (wanted,),
            ).fetchall()
            if len(transaction_rows) > 1:
                raise RegistryIntegrityError(
                    "multiple active transactions exist for one lineage"
                )
            if len(initialization_rows) > 1:
                raise RegistryIntegrityError(
                    "multiple active initializations exist for one lineage"
                )
            active_transaction = (
                self._transaction_from_row(transaction_rows[0])
                if transaction_rows
                else None
            )
            active_initialization = (
                self._initialization_from_row(initialization_rows[0])
                if initialization_rows
                else None
            )
            if (
                isinstance(value, LineageKey)
                and active_initialization is not None
                and active_initialization.key != value
            ):
                raise RegistryIntegrityError(
                    "active initialization key differs from requested lineage key"
                )
            checkpoint_rows = connection.execute(
                """
                SELECT * FROM lineage_checkpoints
                WHERE lineage_id=?
                ORDER BY sequence, checkpoint_memory_id
                """,
                (wanted,),
            ).fetchall()
            checkpoints = tuple(
                self._checkpoint_from_row(row) for row in checkpoint_rows
            )
            if record is None and checkpoints:
                raise RegistryIntegrityError(
                    "checkpoint history exists without its lineage"
                )
            if record is not None and record.memory_mode == "best_effort" and checkpoints:
                raise RegistryIntegrityError(
                    "best_effort lineage unexpectedly retains checkpoint history"
                )
            if record is not None and record.memory_mode == "required":
                if len(checkpoints) != record.sequence:
                    raise RegistryIntegrityError(
                        "required lineage does not retain one checkpoint per receipt"
                    )
                prior_checkpoint_id = ""
                prior_receipt_sha256 = ""
                for expected_sequence, retained in enumerate(checkpoints, 1):
                    parsed = _checkpoint_object(retained.payload)
                    if (
                        retained.lineage_id != record.lineage_id
                        or retained.sequence != expected_sequence
                        or parsed.get("sequence") != expected_sequence
                        or parsed.get("previous_checkpoint_id") != prior_checkpoint_id
                        or parsed.get("previous_receipt_sha256")
                        != prior_receipt_sha256
                    ):
                        raise RegistryIntegrityError(
                            "retained checkpoint history is incomplete or forked"
                        )
                    prior_checkpoint_id = retained.checkpoint_memory_id
                    prior_receipt_sha256 = retained.head_sha256
                if checkpoints and (
                    checkpoints[-1].checkpoint_memory_id
                    != record.checkpoint_memory_id
                    or checkpoints[-1].head_sha256 != record.head_sha256
                ):
                    raise RegistryIntegrityError(
                        "retained checkpoint history does not end at current lineage head"
                    )
            connection.commit()
            return LineageSnapshot(
                record=record,
                active_transaction=active_transaction,
                active_initialization=active_initialization,
                checkpoint_payload=(checkpoint.payload if checkpoint is not None else None),
                checkpoint_payloads=tuple(item.payload for item in checkpoints),
            )
        except RoleLineageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise self._sqlite_error(
                "cannot read role lineage integrity snapshot", exc
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def require_lineage(self, value: str | LineageKey) -> LineageRecord:
        record = self.get_lineage(value)
        if record is None:
            raise LineageNotFoundError("role lineage is not initialized")
        return record

    def get_initialization(self, init_id: str) -> InitializationRecord | None:
        """Read one initialization journal without creating registry state."""

        _validate_tx_id(init_id, "init_id")
        connection = self._connect(readonly=True)
        try:
            return self._select_initialization(connection, init_id)
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error(
                "cannot read lineage initialization", exc
            ) from exc
        finally:
            connection.close()

    def list_active_initializations(
        self, lineage: str | LineageKey | None = None
    ) -> list[InitializationRecord]:
        """Return durable incomplete first-use intents in deterministic order."""

        connection = self._connect(readonly=True)
        try:
            if lineage is None:
                rows = connection.execute(
                    """
                    SELECT * FROM lineage_initializations
                    WHERE status='pending'
                    ORDER BY created_at, init_id
                    """
                ).fetchall()
            else:
                wanted = self._coerce_lineage_id(lineage)
                rows = connection.execute(
                    """
                    SELECT * FROM lineage_initializations
                    WHERE lineage_id=? AND status='pending'
                    ORDER BY created_at, init_id
                    """,
                    (wanted,),
                ).fetchall()
            records = [self._initialization_from_row(row) for row in rows]
            seen: set[str] = set()
            for record in records:
                if record.lineage_id in seen:
                    raise RegistryIntegrityError(
                        "multiple active initializations exist for one lineage"
                    )
                seen.add(record.lineage_id)
            return records
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error(
                "cannot list active lineage initializations", exc
            ) from exc
        finally:
            connection.close()

    def get_transaction(self, tx_id: str) -> TransactionRecord | None:
        _validate_tx_id(tx_id)
        connection = self._connect(readonly=True)
        try:
            return self._select_transaction(connection, tx_id)
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot read lineage transaction", exc) from exc
        finally:
            connection.close()

    def list_active_transactions(
        self, lineage: str | LineageKey | None = None
    ) -> list[TransactionRecord]:
        connection = self._connect(readonly=True)
        try:
            if lineage is None:
                rows = connection.execute(
                    """
                    SELECT * FROM lineage_transactions
                    WHERE status IN ('pending','recovering')
                    ORDER BY created_at, tx_id
                    """
                ).fetchall()
            else:
                wanted = self._coerce_lineage_id(lineage)
                rows = connection.execute(
                    """
                    SELECT * FROM lineage_transactions
                    WHERE lineage_id=? AND status IN ('pending','recovering')
                    ORDER BY created_at, tx_id
                    """,
                    (wanted,),
                ).fetchall()
            records = [self._transaction_from_row(row) for row in rows]
            seen: set[str] = set()
            for record in records:
                if record.lineage_id in seen:
                    raise RegistryIntegrityError(
                        "multiple active transactions exist for one lineage"
                    )
                seen.add(record.lineage_id)
            return records
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot list active lineage transactions", exc) from exc
        finally:
            connection.close()

    def list_lineages(self) -> list[LineageRecord]:
        """Return every registered lineage in deterministic key order."""

        connection = self._connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT * FROM lineages ORDER BY namespace, uc, artifact_dir"
            ).fetchall()
            records = [self._lineage_from_row(row) for row in rows]
            for record in records:
                self._validate_current_checkpoint(connection, record)
            return records
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot list role lineages", exc) from exc
        finally:
            connection.close()

    def _insert_checkpoint(
        self,
        connection: sqlite3.Connection,
        *,
        checkpoint_memory_id: str,
        lineage_id_value: str,
        head_sha256: str,
        sequence: int,
        payload: bytes,
        created_at: str,
    ) -> None:
        existing = connection.execute(
            "SELECT * FROM lineage_checkpoints WHERE checkpoint_memory_id=?",
            (checkpoint_memory_id,),
        ).fetchone()
        if existing is not None:
            record = self._checkpoint_from_row(existing)
            if (
                record.lineage_id != lineage_id_value
                or record.head_sha256 != head_sha256
                or record.sequence != sequence
                or record.payload != payload
            ):
                raise RegistryIntegrityError(
                    "checkpoint Memory ID was reused with different canonical bytes"
                )
            return
        collision = connection.execute(
            """
            SELECT * FROM lineage_checkpoints
            WHERE lineage_id=? AND head_sha256=? AND sequence=?
            """,
            (lineage_id_value, head_sha256, sequence),
        ).fetchone()
        if collision is not None:
            raise RegistryIntegrityError(
                "lineage head already has a different retained checkpoint"
            )
        connection.execute(
            """
            INSERT INTO lineage_checkpoints (
                checkpoint_memory_id, lineage_id, head_sha256, sequence,
                payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint_memory_id,
                lineage_id_value,
                head_sha256,
                sequence,
                payload,
                created_at,
            ),
        )

    @staticmethod
    def _require_pending_initialization(
        record: InitializationRecord, *, expected_stage: str
    ) -> None:
        if expected_stage not in _ACTIVE_INIT_STAGE_ORDER:
            raise LineageValidationError(
                "expected initialization stage is not active"
            )
        if record.status != INIT_STATUS_PENDING or record.stage != expected_stage:
            raise InitializationStateError(
                f"initialization requires exact stage {expected_stage}; "
                f"found {record.status}/{record.stage}"
            )

    def begin_initialization(
        self,
        key: LineageKey,
        *,
        lifecycle_state: str,
        memory_mode: str,
        init_id: str | None = None,
    ) -> InitializationRecord:
        """Persist a first-use intent before any strict Memory root probe.

        The same exact pending intent is returned on retry.  A different
        lifecycle contract, identity, or explicit intent ID fails closed.
        """

        if not isinstance(key, LineageKey):
            raise LineageValidationError("key must be a LineageKey")
        _validate_state(lifecycle_state)
        _validate_memory_mode(memory_mode)
        identifier = (
            uuid.uuid4().hex
            if init_id is None
            else _validate_tx_id(init_id, "init_id")
        )
        now = utc_now()
        with self._writer() as connection:
            lineage = self._select_lineage(connection, key.lineage_id)
            if lineage is not None:
                if lineage.key != key:
                    raise RegistryIntegrityError(
                        "deterministic lineage ID collides with another key"
                    )
                raise LineageAlreadyExistsError("role lineage already exists")
            row = connection.execute(
                "SELECT * FROM lineage_initializations WHERE lineage_id=?",
                (key.lineage_id,),
            ).fetchone()
            if row is not None:
                existing = self._initialization_from_row(row)
                if existing.key != key:
                    raise RegistryIntegrityError(
                        "initialization lineage ID collides with another key"
                    )
                exact_contract = (
                    existing.lifecycle_state == lifecycle_state
                    and existing.memory_mode == memory_mode
                    and (init_id is None or existing.init_id == identifier)
                )
                if existing.status == INIT_STATUS_PENDING and exact_contract:
                    return existing
                raise InitializationConflictError(
                    "a different or terminal initialization intent already exists"
                )
            key_json = key.canonical_bytes.decode("utf-8")
            connection.execute(
                """
                INSERT INTO lineage_initializations (
                    init_id, lineage_id, key_schema, key_json,
                    namespace, uc, artifact_dir, status, stage,
                    lifecycle_state, memory_mode, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', ?, ?, ?, ?)
                """,
                (
                    identifier,
                    key.lineage_id,
                    LINEAGE_KEY_SCHEMA,
                    key_json,
                    key.namespace,
                    key.uc,
                    key.artifact_dir,
                    lifecycle_state,
                    memory_mode,
                    now,
                    now,
                ),
            )
            created = self._select_initialization(connection, identifier)
            if created is None:  # pragma: no cover - shared transaction.
                raise RegistryIntegrityError(
                    "inserted initialization intent disappeared"
                )
            return created

    def mark_initialization_root_absence_verified(
        self, init_id: str
    ) -> InitializationRecord:
        """Journal the exact 404 decision before root creation can begin."""

        _validate_tx_id(init_id, "init_id")
        now = utc_now()
        with self._writer() as connection:
            record = self._select_initialization(connection, init_id)
            if record is None:
                raise InitializationStateError(
                    "lineage initialization intent does not exist"
                )
            if (
                record.status == INIT_STATUS_PENDING
                and record.stage == INIT_STAGE_ROOT_ABSENCE_VERIFIED
            ):
                return record
            self._require_pending_initialization(
                record, expected_stage=INIT_STAGE_PENDING
            )
            cursor = connection.execute(
                """
                UPDATE lineage_initializations
                SET stage='root_absence_verified', updated_at=?
                WHERE init_id=? AND status='pending' AND stage='pending'
                """,
                (now, init_id),
            )
            if cursor.rowcount != 1:
                raise InitializationStateError(
                    "initialization root-absence journal CAS lost"
                )
            updated = self._select_initialization(connection, init_id)
            if updated is None:  # pragma: no cover
                raise RegistryIntegrityError("initialization intent disappeared")
            return updated

    def bind_initialization_root(
        self, init_id: str, *, root_memory_id: str
    ) -> InitializationRecord:
        """Bind an exact verified root only after absence was durably recorded."""

        _validate_tx_id(init_id, "init_id")
        _validate_opaque_id(root_memory_id, "root_memory_id")
        now = utc_now()
        with self._writer() as connection:
            record = self._select_initialization(connection, init_id)
            if record is None:
                raise InitializationStateError(
                    "lineage initialization intent does not exist"
                )
            if record.memory_mode == "required" and not root_memory_id:
                raise LineageValidationError(
                    "required initialization needs an exact Memory root"
                )
            if (
                record.memory_mode == "required"
                and root_memory_id != lineage_root_id(record.key)
            ):
                raise LineageValidationError(
                    "required root Memory ID does not match its deterministic "
                    "content SHA-256"
                )
            if record.memory_mode == "best_effort" and root_memory_id:
                raise LineageValidationError(
                    "best-effort initialization must not bind a Memory root"
                )
            if record.stage in {
                INIT_STAGE_ROOT_VERIFIED,
                INIT_STAGE_REGISTRY_INITIALIZED,
                INIT_STAGE_CHAIN_WRITTEN,
                INIT_STAGE_COMPLETED,
            }:
                if record.root_memory_id != root_memory_id:
                    raise InitializationConflictError(
                        "initialization root is immutable"
                    )
                return record
            self._require_pending_initialization(
                record, expected_stage=INIT_STAGE_ROOT_ABSENCE_VERIFIED
            )
            cursor = connection.execute(
                """
                UPDATE lineage_initializations
                SET stage='root_verified', root_memory_id=?, updated_at=?
                WHERE init_id=? AND status='pending'
                  AND stage='root_absence_verified' AND root_memory_id=''
                """,
                (root_memory_id, now, init_id),
            )
            if cursor.rowcount != 1:
                raise InitializationStateError(
                    "initialization root binding CAS lost"
                )
            updated = self._select_initialization(connection, init_id)
            if updated is None:  # pragma: no cover
                raise RegistryIntegrityError("initialization intent disappeared")
            return updated

    def commit_initialization(self, init_id: str) -> LineageRecord:
        """Atomically create the empty registry head and advance its intent."""

        _validate_tx_id(init_id, "init_id")
        now = utc_now()
        with self._writer() as connection:
            intent = self._select_initialization(connection, init_id)
            if intent is None:
                raise InitializationStateError(
                    "lineage initialization intent does not exist"
                )
            existing = self._select_lineage(connection, intent.lineage_id)
            if intent.stage in {
                INIT_STAGE_REGISTRY_INITIALIZED,
                INIT_STAGE_CHAIN_WRITTEN,
                INIT_STAGE_COMPLETED,
            }:
                return self._require_initialized_empty_lineage(connection, intent)
            self._require_pending_initialization(
                intent, expected_stage=INIT_STAGE_ROOT_VERIFIED
            )
            if existing is not None:
                raise InitializationConflictError(
                    "lineage appeared before initialization commit"
                )
            key_json = intent.key.canonical_bytes.decode("utf-8")
            connection.execute(
                """
                INSERT INTO lineages (
                    lineage_id, key_schema, key_json, namespace, uc, artifact_dir,
                    lifecycle_state, sequence, head_sha256, revision, memory_mode,
                    root_memory_id, checkpoint_memory_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', 0, ?, ?, '', ?, ?)
                """,
                (
                    intent.lineage_id,
                    LINEAGE_KEY_SCHEMA,
                    key_json,
                    intent.key.namespace,
                    intent.key.uc,
                    intent.key.artifact_dir,
                    intent.lifecycle_state,
                    intent.memory_mode,
                    intent.root_memory_id,
                    now,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE lineage_initializations
                SET stage='registry_initialized', updated_at=?
                WHERE init_id=? AND status='pending' AND stage='root_verified'
                """,
                (now, init_id),
            )
            if cursor.rowcount != 1:
                raise InitializationStateError(
                    "initialization registry commit CAS lost"
                )
            lineage = self._select_lineage(connection, intent.lineage_id)
            if lineage is None:  # pragma: no cover
                raise RegistryIntegrityError("initialized lineage disappeared")
            return lineage

    def mark_initialization_chain_written(
        self, init_id: str
    ) -> InitializationRecord:
        """Record that the caller exact-verified the published empty chain."""

        _validate_tx_id(init_id, "init_id")
        now = utc_now()
        with self._writer() as connection:
            record = self._select_initialization(connection, init_id)
            if record is None:
                raise InitializationStateError(
                    "lineage initialization intent does not exist"
                )
            if record.stage in {INIT_STAGE_CHAIN_WRITTEN, INIT_STAGE_COMPLETED}:
                return record
            self._require_pending_initialization(
                record, expected_stage=INIT_STAGE_REGISTRY_INITIALIZED
            )
            self._require_initialized_empty_lineage(connection, record)
            cursor = connection.execute(
                """
                UPDATE lineage_initializations
                SET stage='chain_written', updated_at=?
                WHERE init_id=? AND status='pending'
                  AND stage='registry_initialized'
                """,
                (now, init_id),
            )
            if cursor.rowcount != 1:
                raise InitializationStateError(
                    "initialization chain journal CAS lost"
                )
            updated = self._select_initialization(connection, init_id)
            if updated is None:  # pragma: no cover
                raise RegistryIntegrityError("initialization intent disappeared")
            return updated

    def complete_initialization(self, init_id: str) -> InitializationRecord:
        """Close an initialization only after the local chain is exact-verified."""

        _validate_tx_id(init_id, "init_id")
        now = utc_now()
        with self._writer() as connection:
            record = self._select_initialization(connection, init_id)
            if record is None:
                raise InitializationStateError(
                    "lineage initialization intent does not exist"
                )
            if record.status == INIT_STATUS_COMPLETED:
                if record.stage != INIT_STAGE_COMPLETED:
                    raise RegistryIntegrityError(
                        "completed initialization has an invalid stage"
                    )
                return record
            self._require_pending_initialization(
                record, expected_stage=INIT_STAGE_CHAIN_WRITTEN
            )
            self._require_initialized_empty_lineage(connection, record)
            cursor = connection.execute(
                """
                UPDATE lineage_initializations
                SET status='completed', stage='completed', completed_at=?, updated_at=?
                WHERE init_id=? AND status='pending' AND stage='chain_written'
                """,
                (now, now, init_id),
            )
            if cursor.rowcount != 1:
                raise InitializationStateError(
                    "initialization completion CAS lost"
                )
            completed = self._select_initialization(connection, init_id)
            if completed is None:  # pragma: no cover
                raise RegistryIntegrityError("initialization intent disappeared")
            return completed

    def abort_initialization(
        self,
        init_id: str,
        *,
        error: str | BaseException = "root_preexisting",
    ) -> InitializationRecord:
        """Close only a pre-probe intent that found pre-existing Memory history."""

        _validate_tx_id(init_id, "init_id")
        diagnostic = _stable_recovery_error(error)
        now = utc_now()
        with self._writer() as connection:
            record = self._select_initialization(connection, init_id)
            if record is None:
                raise InitializationStateError(
                    "lineage initialization intent does not exist"
                )
            self._require_pending_initialization(
                record, expected_stage=INIT_STAGE_PENDING
            )
            cursor = connection.execute(
                """
                UPDATE lineage_initializations
                SET status='aborted', stage='aborted', error=?,
                    completed_at=?, updated_at=?
                WHERE init_id=? AND status='pending' AND stage='pending'
                """,
                (diagnostic, now, now, init_id),
            )
            if cursor.rowcount != 1:
                raise InitializationStateError(
                    "initialization abort CAS lost"
                )
            aborted = self._select_initialization(connection, init_id)
            if aborted is None:  # pragma: no cover
                raise RegistryIntegrityError("initialization intent disappeared")
            return aborted

    def _register(
        self,
        key: LineageKey,
        *,
        lifecycle_state: str,
        sequence: int,
        head_sha256: str,
        memory_mode: str,
        root_memory_id: str,
        checkpoint_memory_id: str,
        checkpoint_payload: Mapping[str, Any] | bytes | None,
        checkpoint_history: Sequence[Mapping[str, Any] | bytes] | None = None,
    ) -> LineageRecord:
        if not isinstance(key, LineageKey):
            raise LineageValidationError("key must be a LineageKey")
        _validate_state(lifecycle_state)
        _validate_sequence_head(sequence, head_sha256)
        _validate_memory_mode(memory_mode)
        _validate_opaque_id(root_memory_id, "root_memory_id")
        _validate_opaque_id(checkpoint_memory_id, "checkpoint_memory_id")
        payload: bytes | None = None
        if checkpoint_payload is not None:
            payload = canonical_checkpoint_payload(checkpoint_payload)
        if bool(checkpoint_memory_id) != bool(payload):
            raise LineageValidationError(
                "checkpoint_memory_id and checkpoint_payload must be supplied together"
            )
        if memory_mode == "required" and not root_memory_id:
            raise LineageValidationError(
                "required lineage needs an exact deterministic Memory root"
            )
        if memory_mode == "required":
            _validate_hash(root_memory_id, "root_memory_id")
            if root_memory_id != lineage_root_id(key):
                raise LineageValidationError(
                    "required root Memory ID is not the deterministic content address"
                )
        if memory_mode == "best_effort" and (
            root_memory_id or checkpoint_memory_id or payload is not None
        ):
            raise LineageValidationError(
                "best_effort lineage must not bind Memory root/checkpoint evidence"
            )
        if sequence == 0 and (checkpoint_memory_id or payload is not None):
            raise LineageValidationError(
                "required sequence-zero lineage must not bind a checkpoint"
            )
        if memory_mode == "required" and sequence > 0 and (
            not checkpoint_memory_id or payload is None
        ):
            raise LineageValidationError(
                "required adopted history needs an exact retained checkpoint"
            )
        retained_history: list[tuple[str, bytes, int, str]] = []
        if memory_mode == "required" and sequence > 0:
            assert payload is not None
            parsed_final = _checkpoint_object(payload)
            _validate_checkpoint_binding(
                payload,
                checkpoint_memory_id,
                key=key,
                root_memory_id=root_memory_id,
                sequence=sequence,
                head_sha256=head_sha256,
                lifecycle_state=lifecycle_state,
                registry_revision=0,
                previous_checkpoint_memory_id=str(
                    parsed_final.get("previous_checkpoint_id") or ""
                ),
                previous_receipt_sha256=str(
                    parsed_final.get("previous_receipt_sha256") or ""
                ),
            )
            supplied_history = list(checkpoint_history or (payload,))
            if len(supplied_history) != sequence:
                raise LineageValidationError(
                    "required adoption needs one retained checkpoint per receipt"
                )
            prior_checkpoint_id = ""
            prior_receipt_sha256 = ""
            for expected_sequence, supplied in enumerate(supplied_history, 1):
                retained_payload = canonical_checkpoint_payload(supplied)
                retained_id = hashlib.sha256(retained_payload).hexdigest()
                parsed_checkpoint = _checkpoint_object(retained_payload)
                retained_head = str(parsed_checkpoint.get("receipt_sha256") or "")
                retained_state = str(parsed_checkpoint.get("resulting_state") or "")
                if parsed_checkpoint.get("registry_revision") != 0:
                    raise LineageValidationError(
                        "legacy adoption checkpoints must bind registry revision zero"
                    )
                _validate_checkpoint_binding(
                    retained_payload,
                    retained_id,
                    key=key,
                    root_memory_id=root_memory_id,
                    sequence=expected_sequence,
                    head_sha256=retained_head,
                    lifecycle_state=retained_state,
                    registry_revision=0,
                    previous_checkpoint_memory_id=prior_checkpoint_id,
                    previous_receipt_sha256=prior_receipt_sha256,
                )
                retained_history.append(
                    (retained_id, retained_payload, expected_sequence, retained_head)
                )
                prior_checkpoint_id = retained_id
                prior_receipt_sha256 = retained_head
            final_id, final_payload, final_sequence, final_head = retained_history[-1]
            final_state = str(_checkpoint_object(final_payload).get("resulting_state") or "")
            if (
                final_id != checkpoint_memory_id
                or final_payload != payload
                or final_sequence != sequence
                or final_head != head_sha256
                or final_state != lifecycle_state
            ):
                raise LineageValidationError(
                    "required adoption checkpoint history does not end at exact lineage head"
                )
        elif checkpoint_history:
            raise LineageValidationError(
                "checkpoint_history is valid only for required non-empty adoption"
            )
        now = utc_now()
        with self._writer() as connection:
            existing = self._select_lineage(connection, key.lineage_id)
            if existing is not None:
                if existing.key != key:
                    raise RegistryIntegrityError(
                        "deterministic lineage ID collides with another key"
                    )
                raise LineageAlreadyExistsError("role lineage already exists")
            initialization_row = connection.execute(
                "SELECT * FROM lineage_initializations WHERE lineage_id=?",
                (key.lineage_id,),
            ).fetchone()
            if initialization_row is not None:
                initialization = self._initialization_from_row(initialization_row)
                if initialization.status == INIT_STATUS_PENDING or sequence == 0:
                    raise InitializationConflictError(
                        "direct registration cannot bypass or restart an "
                        "initialization journal"
                    )
            key_json = key.canonical_bytes.decode("utf-8")
            connection.execute(
                """
                INSERT INTO lineages (
                    lineage_id, key_schema, key_json, namespace, uc, artifact_dir,
                    lifecycle_state, sequence, head_sha256, revision, memory_mode,
                    root_memory_id, checkpoint_memory_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    key.lineage_id,
                    LINEAGE_KEY_SCHEMA,
                    key_json,
                    key.namespace,
                    key.uc,
                    key.artifact_dir,
                    lifecycle_state,
                    sequence,
                    head_sha256,
                    memory_mode,
                    root_memory_id,
                    checkpoint_memory_id,
                    now,
                    now,
                ),
            )
            for retained_id, retained_payload, retained_sequence, retained_head in retained_history:
                self._insert_checkpoint(
                    connection,
                    checkpoint_memory_id=retained_id,
                    lineage_id_value=key.lineage_id,
                    head_sha256=retained_head,
                    sequence=retained_sequence,
                    payload=retained_payload,
                    created_at=now,
                )
            row = self._select_lineage(connection, key.lineage_id)
            if row is None:  # pragma: no cover - INSERT and SELECT share a transaction.
                raise RegistryIntegrityError("inserted lineage disappeared")
            return row

    def initialize(
        self,
        key: LineageKey,
        *,
        lifecycle_state: str,
        memory_mode: str,
        root_memory_id: str = "",
        checkpoint_memory_id: str = "",
        checkpoint_payload: Mapping[str, Any] | bytes | None = None,
    ) -> LineageRecord:
        """Reject the legacy non-journaled first-use registration surface."""

        raise InitializationStateError(
            "direct initialize is disabled; use the crash-safe initialization "
            "journal APIs"
        )

    def adopt(
        self,
        key: LineageKey,
        *,
        lifecycle_state: str,
        sequence: int,
        head_sha256: str,
        memory_mode: str,
        root_memory_id: str = "",
        checkpoint_memory_id: str = "",
        checkpoint_payload: Mapping[str, Any] | bytes | None = None,
        checkpoint_history: Sequence[Mapping[str, Any] | bytes] | None = None,
    ) -> LineageRecord:
        """Register a verified legacy chain without rewriting its receipts."""

        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise LineageValidationError("lineage adoption requires non-empty history")
        return self._register(
            key,
            lifecycle_state=lifecycle_state,
            sequence=sequence,
            head_sha256=head_sha256,
            memory_mode=memory_mode,
            root_memory_id=root_memory_id,
            checkpoint_memory_id=checkpoint_memory_id,
            checkpoint_payload=checkpoint_payload,
            checkpoint_history=checkpoint_history,
        )

    def begin_pending(
        self,
        lineage: str | LineageKey,
        *,
        event: str,
        expected_head_sha256: str,
        expected_sequence: int,
        expected_revision: int,
        expected_checkpoint_memory_id: str,
        target_lifecycle_state: str,
        target_sequence: int | None = None,
        tx_id: str | None = None,
        transition_payload: Mapping[str, Any] | bytes | None = None,
    ) -> TransactionRecord:
        """Create the sole active transaction against an exact expected head."""

        wanted = self._coerce_lineage_id(lineage)
        _validate_state(event, "event")
        _validate_sequence_head(expected_sequence, expected_head_sha256)
        _validate_revision(expected_revision)
        _validate_opaque_id(
            expected_checkpoint_memory_id, "expected_checkpoint_memory_id"
        )
        _validate_state(target_lifecycle_state)
        next_sequence = expected_sequence + 1
        if target_sequence is not None and target_sequence != next_sequence:
            raise LineageValidationError(
                "target_sequence must be exactly expected_sequence + 1"
            )
        identifier = uuid.uuid4().hex if tx_id is None else _validate_tx_id(tx_id)
        durable_transition = (
            canonical_transition_payload(transition_payload)
            if transition_payload is not None
            else None
        )
        now = utc_now()
        with self._writer() as connection:
            record = self._select_lineage(connection, wanted)
            if record is None:
                raise LineageNotFoundError("role lineage is not initialized")
            if isinstance(lineage, LineageKey) and record.key != lineage:
                raise RegistryIntegrityError("lineage ID collision with a different key")
            initialization = connection.execute(
                """
                SELECT init_id FROM lineage_initializations
                WHERE lineage_id=? AND status='pending'
                """,
                (wanted,),
            ).fetchone()
            if initialization is not None:
                raise InitializationStateError(
                    "lineage initialization is still recovery_pending"
                )
            if (
                record.head_sha256 != expected_head_sha256
                or record.sequence != expected_sequence
                or record.revision != expected_revision
                or record.checkpoint_memory_id != expected_checkpoint_memory_id
            ):
                raise LineageConflictError(
                    "lineage head/sequence/revision/checkpoint no longer matches"
                )
            active = connection.execute(
                """
                SELECT tx_id FROM lineage_transactions
                WHERE lineage_id=? AND status IN ('pending','recovering')
                """,
                (wanted,),
            ).fetchone()
            if active is not None:
                raise PendingTransactionError(
                    "lineage already has an active transaction"
                )
            connection.execute(
                """
                INSERT INTO lineage_transactions (
                    tx_id, lineage_id, event, status, stage,
                    expected_head_sha256, expected_sequence, expected_revision,
                    expected_checkpoint_memory_id, target_sequence,
                    target_lifecycle_state, transition_payload, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    wanted,
                    event,
                    expected_head_sha256,
                    expected_sequence,
                    expected_revision,
                    expected_checkpoint_memory_id,
                    next_sequence,
                    target_lifecycle_state,
                    durable_transition,
                    now,
                    now,
                ),
            )
            transaction = self._select_transaction(connection, identifier)
            if transaction is None:  # pragma: no cover
                raise RegistryIntegrityError("inserted transaction disappeared")
            return transaction

    def bind_transition_payload(
        self,
        tx_id: str,
        *,
        transition_payload: Mapping[str, Any] | bytes,
        recovery_token: str | None = None,
    ) -> TransactionRecord:
        """Bind exact transition bytes at ``pending`` before any Memory call.

        Supplying the payload directly to :meth:`begin_pending` is preferred.
        This separate expected-state CAS supports callers that must construct
        the transition immediately after acquiring the durable transaction.
        """

        _validate_tx_id(tx_id)
        payload = canonical_transition_payload(transition_payload)
        now = utc_now()
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            self._require_active_transaction(
                transaction,
                expected_stage=TX_STAGE_PENDING,
                recovery_token=recovery_token,
            )
            if (
                transaction.transition_payload is not None
                and transaction.transition_payload != payload
            ):
                raise TransactionStateError(
                    "transition payload is already bound and immutable"
                )
            cursor = connection.execute(
                """
                UPDATE lineage_transactions
                SET transition_payload=?, updated_at=?
                WHERE tx_id=? AND status=? AND stage='pending'
                  AND recovery_token=?
                  AND (transition_payload IS NULL OR transition_payload=?)
                """,
                (
                    payload,
                    now,
                    tx_id,
                    transaction.status,
                    transaction.recovery_token,
                    payload,
                ),
            )
            if cursor.rowcount != 1:
                raise TransactionStateError("transition payload bind CAS lost")
            bound = self._select_transaction(connection, tx_id)
            if bound is None:  # pragma: no cover
                raise RegistryIntegrityError("transition-bound transaction disappeared")
            return bound

    @staticmethod
    def _require_active_transaction(
        transaction: TransactionRecord,
        *,
        expected_stage: str,
        recovery_token: str | None,
    ) -> None:
        if expected_stage not in _ACTIVE_STAGE_ORDER:
            raise LineageValidationError("expected_stage is not an active stage")
        if transaction.status not in ACTIVE_TX_STATUSES:
            raise TransactionStateError("transaction is no longer active")
        if transaction.stage != expected_stage:
            raise TransactionStateError(
                f"transaction stage changed from expected {expected_stage!r}"
            )
        if transaction.status == TX_STATUS_RECOVERING:
            if not recovery_token or transaction.recovery_token != recovery_token:
                raise RecoveryClaimError("recovery claim token does not match")
        elif recovery_token is not None:
            raise RecoveryClaimError("pending transaction is not recovery-claimed")

    @staticmethod
    def _merge_immutable(
        current: Any,
        supplied: Any,
        *,
        empty: Any,
        field: str,
    ) -> Any:
        if supplied is _UNSET:
            return current
        if current != empty and supplied != current:
            raise TransactionStateError(f"{field} is already bound and immutable")
        return supplied

    def update_stage(
        self,
        tx_id: str,
        *,
        expected_stage: str,
        new_stage: str,
        target_head_sha256: str | object = _UNSET,
        receipt_path: str | object = _UNSET,
        receipt_bytes: bytes | object = _UNSET,
        receipt_mode: int | object = _UNSET,
        receipt_sha256: str | object = _UNSET,
        transition_memory_id: str | object = _UNSET,
        checkpoint_memory_id: str | object = _UNSET,
        checkpoint_payload: Mapping[str, Any] | bytes | object = _UNSET,
        error: str | object = _UNSET,
        recovery_token: str | None = None,
    ) -> TransactionRecord:
        """Advance one durable stage using expected-stage compare-and-swap.

        Journal fields are bind-once.  After ``registry_committed`` only stage
        and diagnostic-error updates are permitted, so recovery can trust the
        exact receipt and checkpoint bytes that were used for the head CAS.
        """

        _validate_tx_id(tx_id)
        if expected_stage not in _ACTIVE_STAGE_ORDER or new_stage not in _ACTIVE_STAGE_ORDER:
            raise LineageValidationError("expected/new stage must be an active stage")
        if new_stage == TX_STAGE_REGISTRY_COMMITTED:
            raise TransactionStateError(
                "registry_committed can only be entered by compare_and_swap_head"
            )
        if (
            _ACTIVE_STAGE_ORDER[expected_stage]
            < _ACTIVE_STAGE_ORDER[TX_STAGE_REGISTRY_COMMITTED]
            < _ACTIVE_STAGE_ORDER[new_stage]
        ):
            raise TransactionStateError(
                "post-CAS stages cannot be reached without the registry head CAS"
            )
        journal_supplied = any(
            value is not _UNSET
            for value in (
                target_head_sha256,
                receipt_path,
                receipt_bytes,
                receipt_mode,
                receipt_sha256,
                transition_memory_id,
                checkpoint_memory_id,
                checkpoint_payload,
            )
        )
        if (
            _ACTIVE_STAGE_ORDER[expected_stage]
            >= _ACTIVE_STAGE_ORDER[TX_STAGE_REGISTRY_COMMITTED]
            and journal_supplied
        ):
            raise TransactionStateError("CAS-bound journal fields are immutable post-CAS")
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            self._require_active_transaction(
                transaction,
                expected_stage=expected_stage,
                recovery_token=recovery_token,
            )
            lineage = self._select_lineage(connection, transaction.lineage_id)
            if lineage is None:
                raise RegistryIntegrityError("transaction lineage disappeared")
            stage_graph = (
                _REQUIRED_STAGE_NEXT
                if lineage.memory_mode == "required"
                else _BEST_EFFORT_STAGE_NEXT
            )
            if stage_graph.get(expected_stage) != new_stage:
                raise TransactionStateError(
                    f"{lineage.memory_mode} transaction next stage after "
                    f"{expected_stage!r} is {stage_graph.get(expected_stage)!r}, "
                    f"not {new_stage!r}"
                )

            transition_binding_edge = (
                expected_stage == TX_STAGE_PENDING
                and new_stage == TX_STAGE_MEMORY_PREPARED
            )
            receipt_binding_edge = (
                new_stage == TX_STAGE_RECEIPT_BOUND
                and expected_stage
                in {TX_STAGE_PENDING, TX_STAGE_MEMORY_PREPARED}
            )
            checkpoint_binding_edge = (
                expected_stage == TX_STAGE_MEMORY_FINALIZED
                and new_stage == TX_STAGE_CHECKPOINT_VERIFIED
            )
            if transition_memory_id is not _UNSET and not transition_binding_edge:
                raise TransactionStateError(
                    "transition Memory ID can bind only at memory_prepared"
                )
            if any(
                value is not _UNSET
                for value in (
                    target_head_sha256,
                    receipt_path,
                    receipt_bytes,
                    receipt_mode,
                    receipt_sha256,
                )
            ) and not receipt_binding_edge:
                raise TransactionStateError(
                    "receipt/head fields can bind only at receipt_bound"
                )
            if any(
                value is not _UNSET
                for value in (checkpoint_memory_id, checkpoint_payload)
            ) and not checkpoint_binding_edge:
                raise TransactionStateError(
                    "checkpoint fields can bind only at checkpoint_verified"
                )

            if target_head_sha256 is not _UNSET:
                target_head_sha256 = _validate_hash(
                    target_head_sha256, "target_head_sha256"
                )
            if receipt_path is not _UNSET:
                receipt_path = _validate_relative_posix_path(
                    receipt_path, "receipt_path"
                )
            if receipt_bytes is not _UNSET:
                if not isinstance(receipt_bytes, bytes) or not receipt_bytes:
                    raise LineageValidationError("receipt_bytes must be non-empty bytes")
                if len(receipt_bytes) > _MAX_JOURNAL_BYTES:
                    raise LineageValidationError("receipt_bytes exceed the journal limit")
            if receipt_mode is not _UNSET:
                receipt_mode = _validate_mode(receipt_mode)
            if receipt_sha256 is not _UNSET:
                receipt_sha256 = _validate_hash(receipt_sha256, "receipt_sha256")
            if transition_memory_id is not _UNSET:
                transition_memory_id = _validate_opaque_id(
                    transition_memory_id,
                    "transition_memory_id",
                    allow_empty=False,
                )
            if checkpoint_memory_id is not _UNSET:
                checkpoint_memory_id = _validate_hash(
                    checkpoint_memory_id, "checkpoint_memory_id"
                )
            if checkpoint_payload is not _UNSET:
                checkpoint_payload = canonical_checkpoint_payload(checkpoint_payload)
            if error is not _UNSET:
                error = _validate_error(error)

            values: dict[str, Any] = {
                "target_head_sha256": self._merge_immutable(
                    transaction.target_head_sha256,
                    target_head_sha256,
                    empty="",
                    field="target_head_sha256",
                ),
                "receipt_path": self._merge_immutable(
                    transaction.receipt_path,
                    receipt_path,
                    empty="",
                    field="receipt_path",
                ),
                "receipt_bytes": self._merge_immutable(
                    transaction.receipt_bytes,
                    receipt_bytes,
                    empty=None,
                    field="receipt_bytes",
                ),
                "receipt_mode": self._merge_immutable(
                    transaction.receipt_mode,
                    receipt_mode,
                    empty=None,
                    field="receipt_mode",
                ),
                "receipt_sha256": self._merge_immutable(
                    transaction.receipt_sha256,
                    receipt_sha256,
                    empty="",
                    field="receipt_sha256",
                ),
                "transition_memory_id": self._merge_immutable(
                    transaction.transition_memory_id,
                    transition_memory_id,
                    empty="",
                    field="transition_memory_id",
                ),
                "checkpoint_memory_id": self._merge_immutable(
                    transaction.checkpoint_memory_id,
                    checkpoint_memory_id,
                    empty="",
                    field="checkpoint_memory_id",
                ),
                "checkpoint_payload": self._merge_immutable(
                    transaction.checkpoint_payload,
                    checkpoint_payload,
                    empty=None,
                    field="checkpoint_payload",
                ),
                "error": transaction.error if error is _UNSET else error,
            }
            receipt_bundle = (
                values["receipt_path"],
                values["receipt_bytes"],
                values["receipt_mode"],
                values["receipt_sha256"],
            )
            if any(value not in ("", None) for value in receipt_bundle) and any(
                value in ("", None) for value in receipt_bundle
            ):
                raise LineageValidationError(
                    "receipt path/bytes/mode/hash must be bound atomically"
                )
            checkpoint_bundle = (
                values["checkpoint_memory_id"],
                values["checkpoint_payload"],
            )
            if bool(checkpoint_bundle[0]) != bool(checkpoint_bundle[1]):
                raise LineageValidationError(
                    "checkpoint ID and canonical payload must be bound together"
                )
            if values["target_head_sha256"] and values["receipt_sha256"]:
                if values["target_head_sha256"] != values["receipt_sha256"]:
                    raise LineageValidationError(
                        "target head must equal the bound receipt SHA-256"
                    )
            if _ACTIVE_STAGE_ORDER[new_stage] >= _ACTIVE_STAGE_ORDER[TX_STAGE_RECEIPT_BOUND]:
                if transaction.transition_payload is None:
                    raise TransactionStateError(
                        "canonical transition must be durable before receipt binding"
                    )
                if any(value in ("", None) for value in receipt_bundle):
                    raise TransactionStateError(
                        "receipt bytes must be durable before receipt_bound"
                    )
                if not values["target_head_sha256"]:
                    raise TransactionStateError("target head is not bound")
            if lineage.memory_mode == "required":
                if (
                    _ACTIVE_STAGE_ORDER[new_stage]
                    >= _ACTIVE_STAGE_ORDER[TX_STAGE_MEMORY_PREPARED]
                    and not values["transition_memory_id"]
                ):
                    raise TransactionStateError(
                        "required Memory transition ID is not durable"
                    )
                if (
                    _ACTIVE_STAGE_ORDER[new_stage]
                    >= _ACTIVE_STAGE_ORDER[TX_STAGE_CHECKPOINT_VERIFIED]
                    and not values["checkpoint_memory_id"]
                ):
                    raise TransactionStateError(
                        "required immutable checkpoint is not durable"
                    )
                if (
                    _ACTIVE_STAGE_ORDER[new_stage]
                    >= _ACTIVE_STAGE_ORDER[TX_STAGE_CHECKPOINT_VERIFIED]
                ):
                    assert isinstance(values["checkpoint_payload"], bytes)
                    _validate_checkpoint_binding(
                        values["checkpoint_payload"],
                        values["checkpoint_memory_id"],
                        key=lineage.key,
                        root_memory_id=lineage.root_memory_id,
                        sequence=transaction.target_sequence,
                        head_sha256=values["target_head_sha256"],
                        lifecycle_state=transaction.target_lifecycle_state,
                        registry_revision=transaction.expected_revision + 1,
                        previous_checkpoint_memory_id=(
                            transaction.expected_checkpoint_memory_id
                        ),
                        previous_receipt_sha256=transaction.expected_head_sha256,
                    )
            elif (
                values["checkpoint_memory_id"]
                or values["checkpoint_payload"] is not None
            ):
                raise TransactionStateError(
                    "best_effort transaction must not bind a lineage checkpoint"
                )
            if (
                _ACTIVE_STAGE_ORDER[new_stage]
                >= _ACTIVE_STAGE_ORDER[TX_STAGE_MEMORY_PREPARED]
                and values["transition_memory_id"]
                and transaction.transition_payload is None
            ):
                raise TransactionStateError(
                    "Memory work cannot precede a durable canonical transition"
                )
            now = utc_now()
            cursor = connection.execute(
                """
                UPDATE lineage_transactions SET
                    stage=?, target_head_sha256=?, receipt_path=?, receipt_bytes=?,
                    receipt_mode=?, receipt_sha256=?, transition_memory_id=?,
                    checkpoint_memory_id=?, checkpoint_payload=?, error=?, updated_at=?
                WHERE tx_id=? AND stage=? AND status=? AND recovery_token=?
                """,
                (
                    new_stage,
                    values["target_head_sha256"],
                    values["receipt_path"],
                    values["receipt_bytes"],
                    values["receipt_mode"],
                    values["receipt_sha256"],
                    values["transition_memory_id"],
                    values["checkpoint_memory_id"],
                    values["checkpoint_payload"],
                    values["error"],
                    now,
                    tx_id,
                    expected_stage,
                    transaction.status,
                    transaction.recovery_token,
                ),
            )
            if cursor.rowcount != 1:
                raise TransactionStateError("expected-stage CAS lost")
            updated = self._select_transaction(connection, tx_id)
            if updated is None:  # pragma: no cover
                raise RegistryIntegrityError("updated transaction disappeared")
            return updated

    def compare_and_swap_head(
        self,
        tx_id: str,
        *,
        expected_stage: str,
        recovery_token: str | None = None,
    ) -> HeadCommit:
        """Commit one exact lineage head and journal stage in one SQLite CAS."""

        _validate_tx_id(tx_id)
        if expected_stage not in _ACTIVE_STAGE_ORDER:
            raise LineageValidationError("expected_stage is not an active stage")
        if _ACTIVE_STAGE_ORDER[expected_stage] >= _ACTIVE_STAGE_ORDER[TX_STAGE_REGISTRY_COMMITTED]:
            raise TransactionStateError("lineage head has already reached the CAS stage")
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            self._require_active_transaction(
                transaction,
                expected_stage=expected_stage,
                recovery_token=recovery_token,
            )
            lineage = self._select_lineage(connection, transaction.lineage_id)
            if lineage is None:
                raise RegistryIntegrityError("transaction lineage disappeared")
            required_cas_stage = TX_STAGE_READY_FOR_CAS
            if expected_stage != required_cas_stage:
                raise TransactionStateError(
                    f"{lineage.memory_mode} lineage CAS requires exact stage "
                    f"{required_cas_stage}"
                )
            if not transaction.target_head_sha256 or not transaction.receipt_sha256:
                raise TransactionStateError("receipt/head is not bound for CAS")
            if transaction.transition_payload is None:
                raise TransactionStateError("canonical transition is not durable for CAS")
            if _ACTIVE_STAGE_ORDER[expected_stage] < _ACTIVE_STAGE_ORDER[TX_STAGE_RECEIPT_BOUND]:
                raise TransactionStateError("lineage cannot CAS before receipt binding")
            if transaction.target_head_sha256 != transaction.receipt_sha256:
                raise RegistryIntegrityError("transaction receipt and target head diverge")
            if (
                not transaction.receipt_path
                or transaction.receipt_bytes is None
                or transaction.receipt_mode is None
            ):
                raise TransactionStateError("exact receipt envelope is not durable")
            next_checkpoint = transaction.checkpoint_memory_id
            if lineage.memory_mode == "required":
                if (
                    not transaction.transition_memory_id
                    or not next_checkpoint
                    or transaction.checkpoint_payload is None
                ):
                    raise TransactionStateError(
                        "required Memory transition/checkpoint is not exact-verified"
                    )
                if (
                    _ACTIVE_STAGE_ORDER[expected_stage]
                    < _ACTIVE_STAGE_ORDER[TX_STAGE_CHECKPOINT_VERIFIED]
                ):
                    raise TransactionStateError(
                        "required lineage cannot CAS before checkpoint verification"
                    )
                assert transaction.checkpoint_payload is not None
                _validate_checkpoint_binding(
                    transaction.checkpoint_payload,
                    next_checkpoint,
                    key=lineage.key,
                    root_memory_id=lineage.root_memory_id,
                    sequence=transaction.target_sequence,
                    head_sha256=transaction.target_head_sha256,
                    lifecycle_state=transaction.target_lifecycle_state,
                    registry_revision=transaction.expected_revision + 1,
                    previous_checkpoint_memory_id=(
                        transaction.expected_checkpoint_memory_id
                    ),
                    previous_receipt_sha256=transaction.expected_head_sha256,
                )
            elif next_checkpoint or transaction.checkpoint_payload is not None:
                raise TransactionStateError(
                    "best_effort lineage cannot CAS a lineage checkpoint"
                )
            if next_checkpoint and transaction.checkpoint_payload is None:
                if next_checkpoint != transaction.expected_checkpoint_memory_id:
                    raise TransactionStateError("new checkpoint ID lacks retained payload")
            now = utc_now()
            cursor = connection.execute(
                """
                UPDATE lineages SET
                    lifecycle_state=?, sequence=?, head_sha256=?,
                    revision=revision + 1, checkpoint_memory_id=?, updated_at=?
                WHERE lineage_id=?
                  AND head_sha256=? AND sequence=? AND revision=?
                  AND checkpoint_memory_id=?
                  AND EXISTS (
                      SELECT 1 FROM lineage_transactions AS tx
                      WHERE tx.tx_id=? AND tx.lineage_id=lineages.lineage_id
                        AND tx.status IN ('pending','recovering')
                        AND tx.stage=?
                        AND tx.expected_head_sha256=lineages.head_sha256
                        AND tx.expected_sequence=lineages.sequence
                        AND tx.expected_revision=lineages.revision
                        AND tx.expected_checkpoint_memory_id=lineages.checkpoint_memory_id
                  )
                """,
                (
                    transaction.target_lifecycle_state,
                    transaction.target_sequence,
                    transaction.target_head_sha256,
                    next_checkpoint,
                    now,
                    transaction.lineage_id,
                    transaction.expected_head_sha256,
                    transaction.expected_sequence,
                    transaction.expected_revision,
                    transaction.expected_checkpoint_memory_id,
                    tx_id,
                    expected_stage,
                ),
            )
            if cursor.rowcount != 1:
                raise LineageConflictError(
                    "lineage head CAS lost; no registry head was advanced"
                )
            if transaction.checkpoint_memory_id:
                if transaction.checkpoint_payload is None:
                    raise RegistryIntegrityError("checkpoint payload disappeared before CAS")
                self._insert_checkpoint(
                    connection,
                    checkpoint_memory_id=transaction.checkpoint_memory_id,
                    lineage_id_value=transaction.lineage_id,
                    head_sha256=transaction.target_head_sha256,
                    sequence=transaction.target_sequence,
                    payload=transaction.checkpoint_payload,
                    created_at=now,
                )
            tx_cursor = connection.execute(
                """
                UPDATE lineage_transactions
                SET stage='registry_committed', updated_at=?
                WHERE tx_id=? AND status=? AND stage=? AND recovery_token=?
                """,
                (
                    now,
                    tx_id,
                    transaction.status,
                    expected_stage,
                    transaction.recovery_token,
                ),
            )
            if tx_cursor.rowcount != 1:
                raise TransactionStateError("transaction CAS journal update lost")
            committed_lineage = self._select_lineage(connection, transaction.lineage_id)
            committed_transaction = self._select_transaction(connection, tx_id)
            if committed_lineage is None or committed_transaction is None:  # pragma: no cover
                raise RegistryIntegrityError("CAS result disappeared")
            return HeadCommit(committed_lineage, committed_transaction)

    def complete(
        self,
        tx_id: str,
        *,
        expected_stage: str = TX_STAGE_CHAIN_REPLACED,
        recovery_token: str | None = None,
    ) -> TransactionRecord:
        """Mark a transaction complete only after its exact local chain exists."""

        _validate_tx_id(tx_id)
        if expected_stage != TX_STAGE_CHAIN_REPLACED:
            raise TransactionStateError("completion requires chain_replaced")
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            self._require_active_transaction(
                transaction,
                expected_stage=expected_stage,
                recovery_token=recovery_token,
            )
            lineage = self._select_lineage(connection, transaction.lineage_id)
            if lineage is None:
                raise RegistryIntegrityError("transaction lineage disappeared")
            expected_checkpoint = transaction.checkpoint_memory_id
            if (
                lineage.head_sha256 != transaction.target_head_sha256
                or lineage.sequence != transaction.target_sequence
                or lineage.revision != transaction.expected_revision + 1
                or lineage.lifecycle_state != transaction.target_lifecycle_state
                or lineage.checkpoint_memory_id != expected_checkpoint
            ):
                raise LineageConflictError(
                    "cannot complete transaction against a different registry head"
                )
            now = utc_now()
            cursor = connection.execute(
                """
                UPDATE lineage_transactions
                SET status='completed', stage='completed', recovery_token='',
                    completed_at=?, updated_at=?
                WHERE tx_id=? AND status=? AND stage=? AND recovery_token=?
                """,
                (
                    now,
                    now,
                    tx_id,
                    transaction.status,
                    expected_stage,
                    transaction.recovery_token,
                ),
            )
            if cursor.rowcount != 1:
                raise TransactionStateError("transaction completion CAS lost")
            completed = self._select_transaction(connection, tx_id)
            if completed is None:  # pragma: no cover
                raise RegistryIntegrityError("completed transaction disappeared")
            return completed

    def handoff_recovery_successor(
        self,
        current_tx_id: str,
        *,
        claim_token: str,
        transition_payload: Mapping[str, Any] | bytes,
        successor_tx_id: str | None = None,
    ) -> RecoverySuccessor:
        """Atomically terminalize recovery work and create its audit successor.

        The claimed source must be either an exact pending ``recovery_restore``
        against the unchanged head, or a recovered lifecycle transaction whose
        target receipt and chain have reached ``chain_replaced``.  The same
        SQLite transaction terminalizes that source and inserts the sole
        ``evidence_recovery`` successor with its canonical transition already
        bound, eliminating an observable aligned-without-audit window.
        """

        _validate_tx_id(current_tx_id)
        _validate_tx_id(claim_token, "claim_token")
        payload = canonical_transition_payload(transition_payload)
        parsed_payload = json.loads(payload.decode("utf-8"))
        if parsed_payload.get("event") != "evidence_recovery":
            raise LineageValidationError(
                "recovery successor transition event must be evidence_recovery"
            )
        identifier = (
            uuid.uuid4().hex
            if successor_tx_id is None
            else _validate_tx_id(successor_tx_id, "successor_tx_id")
        )
        now = utc_now()
        with self._writer() as connection:
            current = self._select_transaction(connection, current_tx_id)
            if current is None:
                raise TransactionNotFoundError(
                    "recovery source transaction does not exist"
                )
            self._require_active_transaction(
                current,
                expected_stage=current.stage,
                recovery_token=claim_token,
            )
            if current.status != TX_STATUS_RECOVERING:
                raise RecoveryClaimError(
                    "recovery successor requires a claimed source transaction"
                )
            lineage = self._select_lineage(connection, current.lineage_id)
            if lineage is None:
                raise RegistryIntegrityError(
                    "recovery source lineage disappeared"
                )
            if current.event == "recovery_restore":
                if current.stage != TX_STAGE_PENDING:
                    raise TransactionStateError(
                        "recovery_restore handoff requires exact pending stage"
                    )
                if (
                    lineage.head_sha256 != current.expected_head_sha256
                    or lineage.sequence != current.expected_sequence
                    or lineage.revision != current.expected_revision
                    or lineage.checkpoint_memory_id
                    != current.expected_checkpoint_memory_id
                    or lineage.lifecycle_state != current.target_lifecycle_state
                ):
                    raise LineageConflictError(
                        "recovery_restore source no longer matches current lineage"
                    )
                terminal_status = TX_STATUS_ABORTED
                terminal_stage = TX_STAGE_ABORTED
                terminal_error = "recovery_restore_completed"
            else:
                if current.stage != TX_STAGE_CHAIN_REPLACED:
                    raise TransactionStateError(
                        "lifecycle recovery handoff requires chain_replaced"
                    )
                if (
                    lineage.head_sha256 != current.target_head_sha256
                    or lineage.sequence != current.target_sequence
                    or lineage.revision != current.expected_revision + 1
                    or lineage.lifecycle_state != current.target_lifecycle_state
                    or lineage.checkpoint_memory_id
                    != current.checkpoint_memory_id
                ):
                    raise LineageConflictError(
                        "recovered lifecycle source no longer matches its target head"
                    )
                terminal_status = TX_STATUS_COMPLETED
                terminal_stage = TX_STAGE_COMPLETED
                terminal_error = current.error

            transition_lineage = parsed_payload.get("lineage")
            if not isinstance(transition_lineage, dict):
                raise LineageValidationError(
                    "recovery successor transition requires a lineage precondition"
                )
            transition_checks = {
                "schema": "bugate.role-lineage-precondition/v1",
                "lineage_id": lineage.lineage_id,
                "expected_head_sha256": lineage.head_sha256,
                "expected_sequence": lineage.sequence,
                "expected_revision": lineage.revision,
                "previous_checkpoint_memory_id": lineage.checkpoint_memory_id,
            }
            if any(
                transition_lineage.get(field) != expected
                for field, expected in transition_checks.items()
            ):
                raise LineageConflictError(
                    "recovery successor transition lineage precondition mismatch"
                )
            recovery = parsed_payload.get("recovery")
            recovery_checks = {
                "recovered_head_sha256": lineage.head_sha256,
                "recovered_sequence": lineage.sequence,
                "preserved_lifecycle_state": lineage.lifecycle_state,
            }
            if not isinstance(recovery, dict) or any(
                recovery.get(field) != expected
                for field, expected in recovery_checks.items()
            ):
                raise LineageConflictError(
                    "recovery successor transition recovery state mismatch"
                )
            top_level_checks = {
                "schema": "bugate.role-transition/v1",
                "event": "evidence_recovery",
                "uc": lineage.key.uc,
                "artifact_dir": lineage.key.artifact_dir,
                "previous_receipt_sha256": lineage.head_sha256,
            }
            if any(
                parsed_payload.get(field) != expected
                for field, expected in top_level_checks.items()
            ):
                raise LineageConflictError(
                    "recovery successor transition identity/head mismatch"
                )
            transition_hash = parsed_payload.get("transition_sha256")
            unhashed_transition = dict(parsed_payload)
            unhashed_transition.pop("transition_sha256", None)
            if (
                not isinstance(transition_hash, str)
                or transition_hash
                != hashlib.sha256(canonical_json(unhashed_transition)).hexdigest()
            ):
                raise LineageValidationError(
                    "recovery successor transition semantic hash mismatch"
                )

            terminal_cursor = connection.execute(
                """
                UPDATE lineage_transactions
                SET status=?, stage=?, recovery_token='', recovery_started_at='',
                    error=?, completed_at=?, updated_at=?
                WHERE tx_id=? AND status='recovering' AND stage=?
                  AND recovery_token=?
                """,
                (
                    terminal_status,
                    terminal_stage,
                    terminal_error,
                    now,
                    now,
                    current_tx_id,
                    current.stage,
                    claim_token,
                ),
            )
            if terminal_cursor.rowcount != 1:
                raise RecoveryClaimError(
                    "recovery source terminalization CAS lost"
                )
            connection.execute(
                """
                INSERT INTO lineage_transactions (
                    tx_id, lineage_id, event, status, stage,
                    expected_head_sha256, expected_sequence, expected_revision,
                    expected_checkpoint_memory_id, target_sequence,
                    target_lifecycle_state, transition_payload, created_at, updated_at
                ) VALUES (?, ?, 'evidence_recovery', 'pending', 'pending',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    lineage.lineage_id,
                    lineage.head_sha256,
                    lineage.sequence,
                    lineage.revision,
                    lineage.checkpoint_memory_id,
                    lineage.sequence + 1,
                    lineage.lifecycle_state,
                    payload,
                    now,
                    now,
                ),
            )
            terminal = self._select_transaction(connection, current_tx_id)
            successor = self._select_transaction(connection, identifier)
            if terminal is None or successor is None:  # pragma: no cover
                raise RegistryIntegrityError(
                    "atomic recovery successor handoff disappeared"
                )
            return RecoverySuccessor(
                terminal_transaction=terminal,
                successor_transaction=successor,
                lineage=lineage,
            )

    def abort_pre_cas(
        self,
        tx_id: str,
        *,
        expected_stage: str,
        error: str,
        recovery_token: str | None = None,
    ) -> TransactionRecord:
        """Abort an explicitly failed transaction only while its head is unchanged."""

        _validate_tx_id(tx_id)
        _validate_error(error)
        if expected_stage not in _ACTIVE_STAGE_ORDER:
            raise LineageValidationError("expected_stage is not active")
        if _ACTIVE_STAGE_ORDER[expected_stage] >= _ACTIVE_STAGE_ORDER[TX_STAGE_REGISTRY_COMMITTED]:
            raise TransactionStateError("post-CAS transactions cannot be aborted")
        if _ACTIVE_STAGE_ORDER[expected_stage] >= _ACTIVE_STAGE_ORDER[TX_STAGE_CHECKPOINT_VERIFIED]:
            raise TransactionStateError(
                "checkpoint_verified/ready_for_cas transactions cannot be aborted"
            )
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            self._require_active_transaction(
                transaction,
                expected_stage=expected_stage,
                recovery_token=recovery_token,
            )
            lineage = self._select_lineage(connection, transaction.lineage_id)
            if lineage is None:
                raise RegistryIntegrityError("transaction lineage disappeared")
            if (
                lineage.memory_mode == "required"
                and _ACTIVE_STAGE_ORDER[expected_stage]
                >= _ACTIVE_STAGE_ORDER[TX_STAGE_MEMORY_FINALIZED]
            ):
                raise TransactionStateError(
                    "required memory_finalized/checkpoint-ready transactions "
                    "must be resumed, not aborted"
                )
            if (
                lineage.head_sha256 != transaction.expected_head_sha256
                or lineage.sequence != transaction.expected_sequence
                or lineage.revision != transaction.expected_revision
                or lineage.checkpoint_memory_id
                != transaction.expected_checkpoint_memory_id
            ):
                raise LineageConflictError("pre-CAS abort found an advanced lineage")
            now = utc_now()
            cursor = connection.execute(
                """
                UPDATE lineage_transactions
                SET status='aborted', stage='aborted', recovery_token='',
                    error=?, completed_at=?, updated_at=?
                WHERE tx_id=? AND status=? AND stage=? AND recovery_token=?
                """,
                (
                    error,
                    now,
                    now,
                    tx_id,
                    transaction.status,
                    expected_stage,
                    transaction.recovery_token,
                ),
            )
            if cursor.rowcount != 1:
                raise TransactionStateError("transaction abort CAS lost")
            aborted = self._select_transaction(connection, tx_id)
            if aborted is None:  # pragma: no cover
                raise RegistryIntegrityError("aborted transaction disappeared")
            return aborted

    def mark_incomplete(
        self, tx_id: str, *, expected_stage: str, error: str
    ) -> TransactionRecord:
        """Persist a semantic failure while deliberately leaving recovery pending."""

        _validate_tx_id(tx_id)
        _validate_error(error)
        if expected_stage not in _ACTIVE_STAGE_ORDER:
            raise LineageValidationError("expected_stage is not active")
        now = utc_now()
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            if transaction.status != TX_STATUS_PENDING or transaction.stage != expected_stage:
                raise TransactionStateError("only an exact pending stage can be marked")
            cursor = connection.execute(
                """
                UPDATE lineage_transactions SET error=?, updated_at=?
                WHERE tx_id=? AND status='pending' AND stage=?
                """,
                (error, now, tx_id, expected_stage),
            )
            if cursor.rowcount != 1:
                raise TransactionStateError("incomplete marker CAS lost")
            marked = self._select_transaction(connection, tx_id)
            if marked is None:  # pragma: no cover
                raise RegistryIntegrityError("marked transaction disappeared")
            return marked

    def claim_recovery(
        self,
        tx_id: str,
        *,
        expected_stage: str | None = None,
        claim_token: str | None = None,
    ) -> RecoveryClaim:
        """Claim or safely take over one incomplete transaction.

        The token carries the claimant PID.  A live owner is never displaced;
        a dead owner's exact token can be replaced by CAS.  PID reuse fails
        closed as live and may delay recovery, but cannot create two claimants.
        """

        _validate_tx_id(tx_id)
        token = _new_recovery_token() if claim_token is None else _validate_tx_id(
            claim_token, "claim_token"
        )
        token_match = _RECOVERY_TOKEN_RE.fullmatch(token)
        if token_match is None or int(token_match.group(1)) != os.getpid():
            raise LineageValidationError(
                "claim_token must be a current-process PID-bearing recovery token"
            )
        if expected_stage is not None and expected_stage not in _ACTIVE_STAGE_ORDER:
            raise LineageValidationError("expected_stage is not active")
        now = utc_now()
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            if expected_stage is not None and transaction.stage != expected_stage:
                raise RecoveryClaimError("transaction recovery stage changed")
            if transaction.status == TX_STATUS_PENDING:
                cursor = connection.execute(
                    """
                    UPDATE lineage_transactions
                    SET status='recovering', recovery_token=?,
                        recovery_started_at=?, updated_at=?
                    WHERE tx_id=? AND status='pending' AND stage=?
                    """,
                    (token, now, now, tx_id, transaction.stage),
                )
            elif transaction.status == TX_STATUS_RECOVERING:
                owner_pid = _recovery_owner_pid(transaction.recovery_token)
                if _pid_is_alive(owner_pid):
                    raise RecoveryClaimError(
                        "transaction has an active recovery claimant"
                    )
                cursor = connection.execute(
                    """
                    UPDATE lineage_transactions
                    SET recovery_token=?, recovery_started_at=?, updated_at=?
                    WHERE tx_id=? AND status='recovering' AND stage=?
                      AND recovery_token=?
                    """,
                    (
                        token,
                        now,
                        now,
                        tx_id,
                        transaction.stage,
                        transaction.recovery_token,
                    ),
                )
            else:
                raise RecoveryClaimError(
                    "transaction is not available for recovery"
                )
            if cursor.rowcount != 1:
                raise RecoveryClaimError("transaction recovery claim lost")
            claimed = self._select_transaction(connection, tx_id)
            if claimed is None:  # pragma: no cover
                raise RegistryIntegrityError("claimed transaction disappeared")
            return RecoveryClaim(transaction=claimed, claim_token=token)

    def mark_recovery_stage(
        self,
        tx_id: str,
        *,
        claim_token: str,
        expected_stage: str,
        new_stage: str,
        **journal: Any,
    ) -> TransactionRecord:
        """Recovery-token-guarded alias for expected-stage journal advancement."""

        return self.update_stage(
            tx_id,
            expected_stage=expected_stage,
            new_stage=new_stage,
            recovery_token=claim_token,
            **journal,
        )

    def release_recovery(
        self, tx_id: str, *, claim_token: str, error: str | BaseException
    ) -> TransactionRecord:
        """Release recovery with a stable, path-free machine diagnostic."""

        _validate_tx_id(tx_id)
        _validate_tx_id(claim_token, "claim_token")
        diagnostic = _stable_recovery_error(error)
        now = utc_now()
        with self._writer() as connection:
            transaction = self._select_transaction(connection, tx_id)
            if transaction is None:
                raise TransactionNotFoundError("lineage transaction does not exist")
            if (
                transaction.status != TX_STATUS_RECOVERING
                or transaction.recovery_token != claim_token
            ):
                raise RecoveryClaimError("recovery claim token does not match")
            cursor = connection.execute(
                """
                UPDATE lineage_transactions
                SET status='pending', recovery_token='', recovery_started_at='',
                    error=?, updated_at=?
                WHERE tx_id=? AND status='recovering' AND recovery_token=?
                """,
                (diagnostic, now, tx_id, claim_token),
            )
            if cursor.rowcount != 1:
                raise RecoveryClaimError("recovery release CAS lost")
            released = self._select_transaction(connection, tx_id)
            if released is None:  # pragma: no cover
                raise RegistryIntegrityError("released transaction disappeared")
            return released

    def get_checkpoint_payload(
        self,
        checkpoint_memory_id: str,
        *,
        lineage: str | LineageKey | None = None,
    ) -> bytes | None:
        """Return exact retained canonical bytes for an immutable checkpoint."""

        _validate_opaque_id(
            checkpoint_memory_id, "checkpoint_memory_id", allow_empty=False
        )
        wanted = self._coerce_lineage_id(lineage) if lineage is not None else None
        connection = self._connect(readonly=True)
        try:
            if wanted is None:
                row = connection.execute(
                    "SELECT * FROM lineage_checkpoints WHERE checkpoint_memory_id=?",
                    (checkpoint_memory_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM lineage_checkpoints
                    WHERE checkpoint_memory_id=? AND lineage_id=?
                    """,
                    (checkpoint_memory_id, wanted),
                ).fetchone()
            if row is None:
                return None
            return self._checkpoint_from_row(row).payload
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot read retained checkpoint", exc) from exc
        finally:
            connection.close()

    def get_checkpoint_for_head(
        self,
        lineage: str | LineageKey,
        head_sha256: str,
    ) -> CheckpointRecord | None:
        """Look up the exact checkpoint bound to a committed lineage head."""

        wanted = self._coerce_lineage_id(lineage)
        _validate_hash(head_sha256, "head_sha256", allow_empty=True)
        connection = self._connect(readonly=True)
        try:
            rows = connection.execute(
                """
                SELECT * FROM lineage_checkpoints
                WHERE lineage_id=? AND head_sha256=?
                ORDER BY sequence DESC
                """,
                (wanted, head_sha256),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise RegistryIntegrityError(
                    "multiple checkpoints are bound to one lineage head"
                )
            return self._checkpoint_from_row(rows[0])
        except RoleLineageError:
            raise
        except sqlite3.Error as exc:
            raise self._sqlite_error("cannot read lineage head checkpoint", exc) from exc
        finally:
            connection.close()


__all__: Sequence[str] = (
    "ACTIVE_INIT_STATUSES",
    "ACTIVE_TX_STATUSES",
    "CheckpointRecord",
    "HeadCommit",
    "INTEGRITY_STATES",
    "INIT_STAGES",
    "INIT_STAGE_ABORTED",
    "INIT_STAGE_CHAIN_WRITTEN",
    "INIT_STAGE_COMPLETED",
    "INIT_STAGE_PENDING",
    "INIT_STAGE_REGISTRY_INITIALIZED",
    "INIT_STAGE_ROOT_ABSENCE_VERIFIED",
    "INIT_STAGE_ROOT_VERIFIED",
    "InitializationConflictError",
    "InitializationRecord",
    "InitializationStateError",
    "LINEAGE_KEY_SCHEMA",
    "LineageAlreadyExistsError",
    "LineageConflictError",
    "LineageKey",
    "LineageNotFoundError",
    "LineageRecord",
    "LineageRegistry",
    "LineageSnapshot",
    "LineageValidationError",
    "PendingTransactionError",
    "RecoveryClaim",
    "RecoveryClaimError",
    "RecoverySuccessor",
    "REGISTRY_FILENAME",
    "REGISTRY_SCHEMA_VERSION",
    "RegistryIntegrityError",
    "RegistryNotFoundError",
    "RegistryUnavailableError",
    "RoleLineageError",
    "TransactionNotFoundError",
    "TransactionRecord",
    "TransactionStateError",
    "TX_STAGES",
    "TX_STAGE_CHAIN_REPLACED",
    "TX_STAGE_CHECKPOINT_VERIFIED",
    "TX_STAGE_MEMORY_FINALIZED",
    "TX_STAGE_MEMORY_PREPARED",
    "TX_STAGE_PENDING",
    "TX_STAGE_READY_FOR_CAS",
    "TX_STAGE_RECEIPT_BOUND",
    "TX_STAGE_RECEIPT_WRITTEN",
    "TX_STAGE_REGISTRY_COMMITTED",
    "build_lineage_key",
    "canonical_checkpoint_payload",
    "canonical_transition_payload",
    "canonical_json",
    "classify_integrity",
    "lineage_id",
    "lineage_root_id",
    "lineage_root_payload",
    "memory_home",
    "registry_path",
    "utc_now",
)
