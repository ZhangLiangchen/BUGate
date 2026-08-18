#!/usr/bin/env python3
"""``--scope self-heal`` driver: failure triage and governed test-asset repair.

This is not a "fix the system under test" agent.  It attributes a failure, and
*only* when the evidence says a test asset is at fault does it allow a repair
candidate -- generated in a sandbox copy, checked deterministically for
fake-green moves, verified before and after, falsified against the profile's
oracle spec, and approved by an independent reviewer in a different session.

Everything it can conclude is recorded; nothing it cannot prove is asserted.
The original failure is never overwritten, deleted, or disguised: the runner log
hash is bound into the first sidecar receipt and every later step re-anchors to
it.

Modes (``self_healing.mode``)
-----------------------------
``off``                 the capability does not exist; the entry returns
                        ``disabled`` and creates nothing.
``diagnose``            triage and schema output only, no candidate.
``verify``              candidate generated and verified in a sandbox copy; the
                        real workspace is never written.
``apply_with_approval`` after the full review flow *and* a human approval
                        record, the verified candidate is applied with a
                        journal so an interrupted apply can be resumed or rolled
                        back to exact bytes.
"""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import codecs
import copy
import difflib
import errno
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path, PurePosixPath
from typing import Any

from bugate_core import dump_json, find_engine_root, gate_status, read_text, write_text
from role_governance import (
    GovernanceContext,
    RoleConfigError,
    RoleGovernanceError,
    _atomic_bytes,
    _json_bytes,
    load_context,
    utc_now,
    workspace_rel,
)
from self_heal_policy import (
    APPLY_MODES,
    CANDIDATE_MODES,
    falsification_spec_path,
    self_healing_policy,
    write_allowed,
)
from self_heal_review import (
    CANDIDATE_SCHEMA,
    FileChange,
    ReviewExpectation,
    structural_review,
    validate_review_document,
)
from oracle_falsification import apply_mutation, load_spec, resolve_evidence
import self_heal_sidecar as sidecar


STATUS_DISABLED = "disabled"
STATUS_TRIAGED = "triaged"
STATUS_BLOCKED = "blocked"
STATUS_HEALING_ACTIVE = "healing_active"
STATUS_VERIFIED = "healing_verified"
STATUS_REJECTED = "healing_rejected"
STATUS_INVALIDATED = "invalidated"

#: Frozen exit codes (stage-2 contract section 7.4).  ``healing_active`` is a
#: healthy intermediate state, so it shares the success code: 2, 3 and 4 all
#: mean the flow stopped, and it did not.
EXIT_CODES = {
    STATUS_DISABLED: 0,
    STATUS_TRIAGED: 0,
    STATUS_HEALING_ACTIVE: 0,
    STATUS_VERIFIED: 0,
    STATUS_BLOCKED: 2,
    STATUS_REJECTED: 3,
    STATUS_INVALIDATED: 4,
}

STEPS = ("triage", "handoff", "accept", "propose", "review", "close", "resume", "status")

JOURNAL_SCHEMA = "bugate.self-heal-apply-journal/v2"
JOURNAL_MODE_INTEGRITY = "required"
NEW_FILE_MODE = 0o600
BASELINE_SCHEMA = "bugate.self-heal-baseline/v2"
VERIFICATION_SCHEMA = "bugate.self-heal-verification/v1"
REPAIRED_TEST_MUTATION_SCHEMA = "bugate.repaired-test-mutation/v1"

REASON_DISABLED = "self_healing_disabled"
REASON_PREFLIGHT = "role_preflight_blocked"
REASON_NOT_ELIGIBLE = "failure_not_healing_eligible"
REASON_LOG_MISSING = "runner_log_missing"
REASON_CANDIDATE_MISSING = "candidate_missing"
REASON_CANDIDATE_MODE = "candidate_not_allowed_in_this_mode"
REASON_VERIFY_COMMANDS = "verification_commands_missing"
REASON_NOT_REPRODUCIBLE = "failure_not_reproducible"
REASON_VERIFICATION_FAILED = "verification_failed"
REASON_FALSIFICATION_MISSING = "falsification_spec_missing"
REASON_FALSIFICATION_NOT_RUN = "falsification_not_executed"
REASON_FALSIFICATION_INCONCLUSIVE = "falsification_inconclusive"
REASON_FALSIFICATION_REJECTED = "falsification_score_below_threshold"
REASON_REPAIRED_TEST_SURVIVES_MUTATION = "repaired_test_survives_mutation"
REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE = (
    "repaired_test_reverse_verification_unavailable"
)
REASON_REPAIRED_ASSERTION_NOT_EXERCISED = "repaired_assertion_not_exercised"
REASON_REVIEW_MISSING = "independent_review_missing"
REASON_STRUCTURAL = "fake_green_structural_finding"
REASON_MAX_ATTEMPTS = "max_attempts_exhausted"
REASON_HUMAN_APPROVAL = "human_approval_missing"
REASON_WORKSPACE_DRIFT = "workspace_drift_since_baseline"
REASON_NO_JOURNAL = "no_interrupted_apply_to_resume"
REASON_JOURNAL_INVALID = "apply_journal_invalid"
REASON_CANDIDATE_DIVERGED_PROPOSAL = "candidate_diverged_from_proposal"
REASON_CANDIDATE_DIVERGED_REVIEW = "candidate_diverged_from_review"
REASON_CANDIDATE_PATH_UNSAFE = "candidate_path_unsafe"
REASON_EVIDENCE_INVALID = "self_heal_evidence_invalid"
REASON_VERIFICATION_EXECUTION_FAILED = "verification_execution_failed"
REASON_REVIEW_CONTEXT_INVALID = "independent_review_context_invalid"
REASON_REVIEW_EVIDENCE_DRIFT = "independent_review_evidence_drift"
REASON_REVIEW_PRECODE_DRIFT = "independent_review_precode_evidence_mismatch"
REASON_REVIEW_FALSIFICATION_INPUT_DRIFT = (
    "independent_review_falsification_input_drift"
)
REASON_REVIEW_FALSIFICATION_EVIDENCE_MISMATCH = (
    "independent_review_falsification_evidence_mismatch"
)
REASON_CONFIG = "self_healing_config_invalid"


# --------------------------------------------------------------------------
# Result plumbing
# --------------------------------------------------------------------------


def result(
    status: str,
    *,
    blocking_reasons: list[str] | None = None,
    artifact_paths: list[str] | None = None,
    next_action: str = "",
) -> dict[str, Any]:
    """Build the frozen five-field gate result (contract section 7.3)."""

    return {
        "status": status,
        "exit_code": EXIT_CODES[status],
        "blocking_reasons": list(dict.fromkeys(blocking_reasons or [])),
        "artifact_paths": list(dict.fromkeys(artifact_paths or [])),
        "next_action": next_action,
    }


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"BUGate self-heal status: {str(payload['status']).upper()}")
    return int(payload["exit_code"])


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Publish sidecar evidence through the private atomic writer.

    Deliberately not ``bugate_core.write_text``: that runs the governed-write
    preflight, which classifies ``00_self_healing/`` as a post-run surface and
    would reject the healer's own (implementer-role) evidence writes.  Role
    receipts use the same private writer for the same reason.
    """

    _atomic_bytes(path, _json_bytes(data), replace=True)


def _write_text(path: Path, body: str) -> None:
    _atomic_bytes(path, body.encode("utf-8"), replace=True)


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str | None:
    return _sha256_bytes(path.read_bytes()) if path.is_file() else None


def _required_sha256_file(path: Path, label: str) -> str:
    """Hash one regular evidence file without following symbolic links."""

    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise SelfHealEvidenceError(
                REASON_EVIDENCE_INVALID, f"{label} is not a regular file: {path}"
            )
        return _sha256_bytes(path.read_bytes())
    except SelfHealEvidenceError:
        raise
    except OSError as exc:
        raise SelfHealEvidenceError(
            REASON_EVIDENCE_INVALID, f"{label} cannot be read: {path}: {exc}"
        ) from exc


# --------------------------------------------------------------------------
# Candidate handling
# --------------------------------------------------------------------------


class SelfHealEvidenceError(ValueError):
    """External self-heal evidence is unsafe or cannot be authenticated."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def _safe_tree_target(
    root: Path,
    relative: Any,
    *,
    reason: str,
    label: str,
    leaf_kind: str = "file",
) -> tuple[str, Path]:
    """Resolve a relative target without following an existing symlink.

    The returned path is lexical beneath ``root``.  Every existing component is
    checked with ``lstat``; callers can therefore read or atomically replace the
    leaf without a pre-existing link redirecting the operation outside the
    tree.  This is a fail-closed path boundary, not a path-normalization helper.
    """

    try:
        relative_text = _safe_relative_path(relative)
    except ValueError as exc:
        raise SelfHealEvidenceError(reason, str(exc)) from exc

    try:
        resolved_root = root.resolve(strict=True)
        root_mode = os.lstat(root).st_mode
    except (OSError, RuntimeError) as exc:
        raise SelfHealEvidenceError(
            reason, f"{label} root cannot be inspected safely: {root}: {exc}"
        ) from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise SelfHealEvidenceError(reason, f"{label} root is not a real directory: {root}")

    target = resolved_root.joinpath(*PurePosixPath(relative_text).parts)
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise SelfHealEvidenceError(
            reason, f"{label} escapes its root: {relative_text}"
        ) from exc

    cursor = resolved_root
    parts = PurePosixPath(relative_text).parts
    for index, part in enumerate(parts):
        cursor = cursor / part
        try:
            mode = os.lstat(cursor).st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise SelfHealEvidenceError(
                reason, f"{label} cannot be inspected safely: {relative_text}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise SelfHealEvidenceError(
                reason, f"{label} traverses a symbolic link: {relative_text}"
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            raise SelfHealEvidenceError(
                reason, f"{label} parent is not a directory: {relative_text}"
            )
        if (
            index == len(parts) - 1
            and (
                (leaf_kind == "file" and not stat.S_ISREG(mode))
                or (leaf_kind == "directory" and not stat.S_ISDIR(mode))
            )
        ):
            raise SelfHealEvidenceError(
                reason, f"{label} has the wrong file type: {relative_text}"
            )
    return relative_text, target


def _attempt_evidence_path(
    attempt_dir: Path,
    relative: str,
    *,
    reason: str = REASON_EVIDENCE_INVALID,
    leaf_kind: str = "file",
) -> Path:
    return _safe_tree_target(
        attempt_dir,
        relative,
        reason=reason,
        label="self-heal attempt evidence",
        leaf_kind=leaf_kind,
    )[1]


def _write_attempt_json(
    attempt_dir: Path,
    relative: str,
    data: dict[str, Any],
    *,
    reason: str = REASON_EVIDENCE_INVALID,
) -> Path:
    path = _attempt_evidence_path(attempt_dir, relative, reason=reason)
    _write_json(path, data)
    return path


def _write_attempt_text(
    attempt_dir: Path,
    relative: str,
    body: str,
    *,
    reason: str = REASON_EVIDENCE_INVALID,
) -> Path:
    path = _attempt_evidence_path(attempt_dir, relative, reason=reason)
    _write_text(path, body)
    return path


def _ensure_attempt_directory(ctx: GovernanceContext, attempt_dir: Path) -> None:
    sidecar_root = sidecar.sidecar_dir(ctx)
    try:
        relative = attempt_dir.relative_to(sidecar_root).as_posix()
    except ValueError as exc:
        raise SelfHealEvidenceError(
            REASON_EVIDENCE_INVALID, "attempt directory escapes the self-heal sidecar"
        ) from exc
    _relative, target = _safe_tree_target(
        sidecar_root,
        relative,
        reason=REASON_EVIDENCE_INVALID,
        label="self-heal attempt directory",
        leaf_kind="directory",
    )
    try:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise SelfHealEvidenceError(
            REASON_EVIDENCE_INVALID,
            f"self-heal attempt directory cannot be created safely: {exc}",
        ) from exc
    _safe_tree_target(
        sidecar_root,
        relative,
        reason=REASON_EVIDENCE_INVALID,
        label="self-heal attempt directory",
        leaf_kind="directory",
    )


def collect_candidate(candidate_dir: Path) -> list[tuple[str, str]]:
    """Map a candidate tree to ``(workspace-relative path, proposed content)``."""

    files: list[tuple[str, str]] = []
    try:
        root_mode = os.lstat(candidate_dir).st_mode
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise SelfHealEvidenceError(
                REASON_CANDIDATE_PATH_UNSAFE,
                f"candidate root is not a real directory: {candidate_dir}",
            )
        for item in sorted(candidate_dir.rglob("*")):
            mode = os.lstat(item).st_mode
            relative = item.relative_to(candidate_dir).as_posix()
            _safe_relative_path(relative)
            if stat.S_ISLNK(mode):
                raise SelfHealEvidenceError(
                    REASON_CANDIDATE_PATH_UNSAFE,
                    f"candidate tree contains a symbolic link: {relative}",
                )
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise SelfHealEvidenceError(
                    REASON_CANDIDATE_PATH_UNSAFE,
                    f"candidate tree contains a non-regular file: {relative}",
                )
            files.append((relative, item.read_text(encoding="utf-8")))
    except SelfHealEvidenceError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise SelfHealEvidenceError(
            REASON_EVIDENCE_INVALID, f"candidate tree cannot be read safely: {exc}"
        ) from exc
    return files


def build_changes(
    proposed: list[tuple[str, str]],
    ctx: GovernanceContext,
    policy: dict[str, Any],
) -> list[FileChange]:
    changes: list[FileChange] = []
    for relative, after in proposed:
        allowed, denied_reason = write_allowed(policy, relative)
        if not allowed:
            raise SelfHealEvidenceError(
                REASON_CANDIDATE_PATH_UNSAFE,
                f"candidate target {relative!r} is not authorized: {denied_reason}",
            )
        _relative, target = _safe_tree_target(
            ctx.root,
            relative,
            reason=REASON_CANDIDATE_PATH_UNSAFE,
            label="candidate workspace target",
        )
        try:
            before = target.read_text(encoding="utf-8") if target.exists() else None
        except (OSError, UnicodeError) as exc:
            raise SelfHealEvidenceError(
                REASON_EVIDENCE_INVALID,
                f"candidate workspace target cannot be read: {relative}: {exc}",
            ) from exc
        changes.append(FileChange(path=relative, before=before, after=after))
    return changes


def unified_diff(changes: list[FileChange]) -> str:
    chunks: list[str] = []
    for change in changes:
        before = (change.before or "").splitlines(keepends=True)
        after = (change.after or "").splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
            )
        )
    body = "".join(chunks)
    return body if body.endswith("\n") or not body else body + "\n"


def baseline_record(
    ctx: GovernanceContext, changes: list[FileChange], attempt_id: str
) -> dict[str, Any]:
    pre_apply_missing_directories: set[str] = set()
    for change in changes:
        pre_apply_missing_directories.update(
            _missing_parent_directories(ctx, change.path)
        )
    return {
        "schema": BASELINE_SCHEMA,
        "attempt_id": attempt_id,
        "captured_at": utc_now(),
        # This is evidence, not an instruction supplied by the later apply
        # journal.  The candidate-manifest digest authenticates these exact
        # pre-apply directory observations before independent review.
        "pre_apply_missing_directories": sorted(pre_apply_missing_directories),
        "files": [
            {
                "path": change.path,
                "before_sha256": _sha256_bytes((change.before or "").encode("utf-8"))
                if change.before is not None
                else None,
                "after_sha256": _sha256_bytes((change.after or "").encode("utf-8")),
            }
            for change in changes
        ],
    }


def _safe_relative_path(value: Any) -> str:
    """Return one canonical workspace-relative POSIX path or fail closed."""

    text = str(value or "")
    pure = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or pure.is_absolute()
        or text != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"unsafe workspace-relative path: {text!r}")
    return text


def _candidate_state(
    ctx: GovernanceContext, attempt_dir: Path
) -> tuple[list[FileChange], str, list[str], list[str]]:
    """Load the stored candidate and bind every byte to ``baseline.json``.

    The returned digest covers the complete baseline bytes (including every
    ``before_sha256`` and pre-apply directory observation), the reconstructed
    candidate patch, and the sorted before/after file manifest. ``errors`` is
    non-empty for missing, extra, duplicated, symlinked, malformed, or
    hash-divergent content. Callers must never approve or apply such state.
    """

    errors: list[str] = []
    try:
        _baseline_relative, baseline_path = _safe_tree_target(
            attempt_dir,
            "baseline.json",
            reason=REASON_EVIDENCE_INVALID,
            label="candidate baseline",
        )
        _stored_relative, stored = _safe_tree_target(
            attempt_dir,
            "candidate",
            reason=REASON_EVIDENCE_INVALID,
            label="stored candidate",
            leaf_kind="directory",
        )
    except SelfHealEvidenceError:
        return [], "", ["candidate_evidence_unsafe"], []
    if not baseline_path.is_file() or not stored.is_dir() or stored.is_symlink():
        return [], "", ["candidate_evidence_missing"], []
    try:
        baseline_body = baseline_path.read_bytes()
        record = json.loads(baseline_body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], "", ["candidate_baseline_unreadable"], []
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schema",
            "attempt_id",
            "captured_at",
            "pre_apply_missing_directories",
            "files",
        }
        or record.get("schema") != BASELINE_SCHEMA
        or record.get("attempt_id") != attempt_dir.name
        or not isinstance(record.get("captured_at"), str)
        or not record.get("captured_at")
        or not isinstance(record.get("files"), list)
    ):
        return [], "", ["candidate_baseline_invalid"], []

    raw_missing_directories = record.get("pre_apply_missing_directories")
    if (
        not isinstance(raw_missing_directories, list)
        or any(not isinstance(item, str) for item in raw_missing_directories)
        or raw_missing_directories != sorted(set(raw_missing_directories))
    ):
        return [], "", ["candidate_baseline_invalid"], []
    pre_apply_missing_directories: list[str] = []
    for raw_directory in raw_missing_directories:
        try:
            pre_apply_missing_directories.append(_safe_relative_path(raw_directory))
        except ValueError:
            errors.append("candidate_baseline_invalid")

    expected: dict[str, dict[str, Any]] = {}
    for item in record["files"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "before_sha256", "after_sha256"}
        ):
            errors.append("candidate_baseline_invalid")
            continue
        try:
            relative = _safe_relative_path(item.get("path"))
        except ValueError:
            errors.append("candidate_path_invalid")
            continue
        if relative in expected:
            errors.append("candidate_path_duplicated")
            continue
        after_sha = str(item.get("after_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", after_sha):
            errors.append("candidate_after_hash_invalid")
            continue
        before_sha = item.get("before_sha256")
        if before_sha is not None and not re.fullmatch(r"[0-9a-f]{64}", str(before_sha)):
            errors.append("candidate_before_hash_invalid")
            continue
        expected[relative] = item

    actual: dict[str, Path] = {}
    try:
        for current, directories, files in os.walk(stored, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in sorted([*directories, *files]):
                item = current_path / name
                mode = os.lstat(item).st_mode
                if stat.S_ISLNK(mode):
                    errors.append("candidate_symlink_forbidden")
                    continue
                if stat.S_ISREG(mode):
                    relative = item.relative_to(stored).as_posix()
                    actual[relative] = item
                elif not stat.S_ISDIR(mode):
                    errors.append("candidate_non_regular_forbidden")
            directories[:] = [
                name
                for name in directories
                if not stat.S_ISLNK(os.lstat(current_path / name).st_mode)
            ]
    except OSError:
        errors.append("candidate_tree_unreadable")

    if set(actual) != set(expected):
        errors.append("candidate_file_set_diverged")

    changes: list[FileChange] = []
    manifest_files: list[dict[str, str | None]] = []
    for relative in sorted(expected):
        proposed = actual.get(relative)
        if proposed is None:
            continue
        try:
            after_bytes = proposed.read_bytes()
            after = after_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append("candidate_file_unreadable")
            continue
        actual_after_sha = _sha256_bytes(after_bytes)
        if actual_after_sha != expected[relative].get("after_sha256"):
            errors.append("candidate_after_hash_diverged")

        recorded_before = expected[relative].get("before_sha256")
        before: str | None = None
        if recorded_before is not None:
            try:
                _before_relative, original = _safe_tree_target(
                    attempt_dir,
                    f"before/{relative}",
                    reason=REASON_EVIDENCE_INVALID,
                    label="candidate before image",
                )
            except SelfHealEvidenceError:
                errors.append("candidate_before_image_unsafe")
                original = attempt_dir / "__unsafe_before_image__"
            if original.is_symlink() or not original.is_file():
                errors.append("candidate_before_image_missing")
            else:
                try:
                    before_bytes = original.read_bytes()
                    before = before_bytes.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    errors.append("candidate_before_image_unreadable")
                else:
                    if _sha256_bytes(before_bytes) != recorded_before:
                        errors.append("candidate_before_hash_diverged")
        changes.append(FileChange(path=relative, before=before, after=after))
        manifest_files.append(
            {
                "path": relative,
                "before_sha256": str(recorded_before)
                if recorded_before is not None
                else None,
                "after_sha256": actual_after_sha,
            }
        )

    new_targets = [
        PurePosixPath(relative)
        for relative, item in expected.items()
        if item.get("before_sha256") is None
    ]
    for relative in pre_apply_missing_directories:
        parts = PurePosixPath(relative).parts
        if not any(target.parts[: len(parts)] == parts for target in new_targets):
            errors.append("candidate_baseline_invalid")

    try:
        _patch_relative, patch_path = _safe_tree_target(
            attempt_dir,
            "candidate.patch",
            reason=REASON_EVIDENCE_INVALID,
            label="candidate patch",
        )
        patch_body = patch_path.read_bytes()
    except (SelfHealEvidenceError, OSError):
        patch_body = b""
        errors.append("candidate_patch_unreadable")
    expected_patch = unified_diff(changes).encode("utf-8")
    if patch_body != expected_patch:
        errors.append("candidate_patch_diverged")

    manifest_sha = _sha256_bytes(
        json.dumps(
            {
                "baseline_sha256": _sha256_bytes(baseline_body),
                "candidate_patch_sha256": _sha256_bytes(patch_body),
                "files": manifest_files,
                "pre_apply_missing_directories": pre_apply_missing_directories,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return (
        changes,
        manifest_sha,
        sorted(set(errors)),
        pre_apply_missing_directories,
    )


# --------------------------------------------------------------------------
# Sandbox verification
# --------------------------------------------------------------------------


_SANDBOX_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv", "node_modules")


def _reject_workspace_symlinks(root: Path) -> None:
    """Reject every symlink before copying or executing a sandbox command.

    Even a link whose current destination is inside ``root`` becomes unsafe
    when copied verbatim: an absolute link still points at the real workspace,
    not its sandbox clone.  The self-heal contract does not promise symlink
    support, so fail-closed rejection is the only unambiguous isolation rule.
    """

    try:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            names = [*directories, *files]
            ignored = set(_SANDBOX_IGNORE(current, names))
            directories[:] = [name for name in directories if name not in ignored]
            for name in sorted(name for name in names if name not in ignored):
                item = current_path / name
                if stat.S_ISLNK(os.lstat(item).st_mode):
                    raise SelfHealEvidenceError(
                        REASON_CANDIDATE_PATH_UNSAFE,
                        "sandbox verification refuses a workspace containing a symbolic "
                        f"link: {item.relative_to(root).as_posix()}",
                    )
    except SelfHealEvidenceError:
        raise
    except OSError as exc:
        raise SelfHealEvidenceError(
            REASON_VERIFICATION_EXECUTION_FAILED,
            f"workspace cannot be inspected before sandbox verification: {exc}",
        ) from exc


def run_verification(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    changes: list[FileChange],
) -> dict[str, Any]:
    """Reproduce the failure, apply the candidate, and re-run -- all in a copy.

    The real workspace is never touched here, in any mode.  ``verify`` mode
    stops after this; ``apply_with_approval`` still has to pass the independent
    review before anything is written back.
    """

    commands = policy["verification_commands"]
    outcome: dict[str, Any] = {
        "schema": VERIFICATION_SCHEMA,
        "recorded_at": utc_now(),
        "commands": commands,
        "before": [],
        "after": [],
        "before_reproduced_failure": False,
        "after_passed": False,
        "sandbox_only": True,
    }
    with tempfile.TemporaryDirectory(prefix="bugate-self-heal-sandbox-") as tmp:
        sandbox = Path(tmp) / "workspace"
        _copy_workspace_sandbox(ctx, sandbox)
        env = dict(os.environ)
        env["BUGATE_PROJECT_ROOT"] = str(sandbox)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        outcome["before"] = _run_commands(commands, sandbox, env)
        outcome["before_reproduced_failure"] = any(
            item["exit_code"] != 0 for item in outcome["before"]
        )
        _apply_candidate_sandbox(changes, sandbox)
        outcome["after"] = _run_commands(commands, sandbox, env)
        outcome["after_passed"] = all(item["exit_code"] == 0 for item in outcome["after"])
    return outcome


def _copy_workspace_sandbox(ctx: GovernanceContext, sandbox: Path) -> None:
    """Copy the governed workspace without following any symbolic link."""

    _reject_workspace_symlinks(ctx.root)
    try:
        shutil.copytree(ctx.root, sandbox, ignore=_SANDBOX_IGNORE, symlinks=False)
    except (OSError, shutil.Error) as exc:
        raise SelfHealEvidenceError(
            REASON_VERIFICATION_EXECUTION_FAILED,
            f"workspace cannot be copied for sandbox verification: {exc}",
        ) from exc


def _apply_candidate_sandbox(changes: list[FileChange], sandbox: Path) -> None:
    """Apply candidate bytes only inside an already isolated sandbox."""

    for change in changes:
        _relative, target = _safe_tree_target(
            sandbox,
            change.path,
            reason=REASON_CANDIDATE_PATH_UNSAFE,
            label="sandbox candidate target",
        )
        try:
            current_mode = (
                stat.S_IMODE(os.lstat(target).st_mode)
                if target.exists()
                else NEW_FILE_MODE
            )
        except OSError as exc:
            raise SelfHealEvidenceError(
                REASON_VERIFICATION_EXECUTION_FAILED,
                f"sandbox candidate target cannot be inspected: {change.path}: {exc}",
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_bytes(
            target,
            (change.after or "").encode("utf-8"),
            replace=True,
            mode=current_mode,
        )


def _copy_candidate_sandbox(
    ctx: GovernanceContext,
    changes: list[FileChange],
    sandbox: Path,
) -> None:
    """Copy the governed workspace and apply the candidate only inside it."""

    _copy_workspace_sandbox(ctx, sandbox)
    _apply_candidate_sandbox(changes, sandbox)


def _run_commands(commands: list[str], cwd: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for command in commands:
        try:
            proc = subprocess.run(
                shlex.split(command),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
            records.append(
                {
                    "command": command,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-8000:],
                    "stderr": proc.stderr[-8000:],
                }
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            records.append(
                {"command": command, "exit_code": 127, "stdout": "", "stderr": str(exc)}
            )
    return records


def _run_isolated_canonical_test(
    cwd: Path, relative: str, env: dict[str, str]
) -> list[dict[str, Any]]:
    """Execute a closed canonical test with authenticated stdlib provenance."""

    try:
        normalized, target = _safe_tree_target(
            cwd,
            relative,
            reason=REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            label="canonical isolated test",
        )
        trusted_env = dict(env)
        for variable in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            trusted_env.pop(variable, None)
        argv = [sys.executable, "-I", "-S", str(target)]
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=trusted_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        return [
            {
                "command": f"{sys.executable} -I -S {normalized}",
                "exit_code": proc.returncode,
                "stdout": proc.stdout[-8000:],
                "stderr": proc.stderr[-8000:],
            }
        ]
    except (OSError, ValueError, subprocess.SubprocessError, SelfHealEvidenceError) as exc:
        return [
            {
                "command": f"{sys.executable} -I -S {relative}",
                "exit_code": 127,
                "stdout": "",
                "stderr": str(exc),
            }
        ]


def verification_log(records: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in records:
        lines.append(f"$ {item['command']}")
        lines.append(item["stdout"])
        if item["stderr"]:
            lines.append(item["stderr"])
        lines.append(f"exit_code: {item['exit_code']}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Falsification (contract section 9 -- required, never degraded)
# --------------------------------------------------------------------------


def _workspace_input_identity(
    ctx: GovernanceContext, path: Path, *, label: str
) -> dict[str, str]:
    """Return a content identity for one non-symlink workspace input."""

    try:
        root = ctx.root.resolve(strict=True)
        lexical = Path(os.path.abspath(os.fspath(path)))
        relative = lexical.relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            f"{label} is outside the governed workspace: {path}: {exc}",
        ) from exc
    relative, target = _safe_tree_target(
        ctx.root,
        relative,
        reason=REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
        label=label,
    )
    try:
        mode = os.lstat(target).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise OSError("input is not a non-symlink regular file")
        body = target.read_bytes()
    except OSError as exc:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            f"{label} cannot be authenticated: {relative}: {exc}",
        ) from exc
    return {"path": relative, "sha256": _sha256_bytes(body)}


def _falsification_input_manifest(
    ctx: GovernanceContext, policy: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    """Authenticate the live spec and every exact evidence path it resolves."""

    spec_path = falsification_spec_path(policy, ctx.root)
    if spec_path is None or not spec_path.exists():
        raise SelfHealEvidenceError(
            REASON_FALSIFICATION_MISSING, "the profile falsification spec is missing"
        )
    spec_identity = _workspace_input_identity(
        ctx, spec_path, label="falsification spec"
    )
    try:
        spec = load_spec(spec_path)
        evidence_paths = resolve_evidence("", spec, spec_path)
    except (OSError, ValueError) as exc:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            f"falsification inputs cannot be resolved: {exc}",
        ) from exc
    if _workspace_input_identity(ctx, spec_path, label="falsification spec") != spec_identity:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            "the falsification spec changed while its inputs were being resolved",
        )
    if not evidence_paths:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            "the falsification spec resolves no evidence",
        )
    evidence = [
        _workspace_input_identity(ctx, path, label="falsification evidence")
        for path in evidence_paths
    ]
    paths = [item["path"] for item in evidence]
    if len(paths) != len(set(paths)):
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            "the falsification spec resolves a duplicate evidence path",
        )
    manifest = {
        "schema": "bugate.self-heal-falsification-inputs/v1",
        "spec": spec_identity,
        "evidence": evidence,
    }
    manifest["sha256"] = _sha256_bytes(_json_bytes(manifest))
    return spec_path, manifest


def run_falsification(
    ctx: GovernanceContext, policy: dict[str, Any]
) -> tuple[dict[str, Any], list[str], str, str]:
    """Compute falsification in scratch; callers persist only after all gates pass."""

    try:
        spec, input_manifest = _falsification_input_manifest(ctx, policy)
    except SelfHealEvidenceError as exc:
        return {}, [exc.reason], "", str(exc) + "\n"
    script = find_engine_root() / "scripts" / "oracle_falsification.py"
    markdown = ""
    log = ""
    # The scorer writes through the governed-write preflight, which correctly
    # refuses a direct write into the sidecar.  Compute in scratch, authenticate
    # exact input identities, and publish only after reverse verification.
    with tempfile.TemporaryDirectory(prefix="bugate-falsification-") as tmp:
        json_output = Path(tmp) / "falsification.json"
        md_output = Path(tmp) / "falsification.md"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--spec",
                    str(spec),
                    "--json-output",
                    str(json_output),
                    "--md-output",
                    str(md_output),
                ],
                cwd=ctx.root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return (
                {},
                [REASON_VERIFICATION_EXECUTION_FAILED],
                "",
                f"falsification execution failed: {exc}\n",
            )
        if not json_output.exists():
            log = f"exit_code: {proc.returncode}\n{proc.stdout}\n{proc.stderr}\n"
            return (
                {"stderr": proc.stderr[-4000:]},
                [REASON_FALSIFICATION_NOT_RUN],
                "",
                log,
            )
        try:
            payload = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = (
                md_output.read_text(encoding="utf-8") if md_output.exists() else ""
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {}, [REASON_FALSIFICATION_NOT_RUN], "", f"{exc}\n"

    try:
        _post_spec, post_manifest = _falsification_input_manifest(ctx, policy)
    except SelfHealEvidenceError as exc:
        return (
            payload,
            [REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE],
            markdown,
            f"falsification inputs changed during execution: {exc}\n",
        )
    if post_manifest != input_manifest:
        return (
            payload,
            [REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE],
            markdown,
            "falsification inputs changed during execution\n",
        )

    records = payload.get("records")
    evidence = input_manifest["evidence"]
    if payload.get("status") == "ran":
        if not isinstance(records, list) or len(records) != len(evidence):
            return (
                payload,
                [REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE],
                markdown,
                "falsification records do not bind every exact evidence path\n",
            )
        for record, identity in zip(records, evidence):
            if (
                not isinstance(record, dict)
                or record.get("evidence") != Path(identity["path"]).name
            ):
                return (
                    payload,
                    [REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE],
                    markdown,
                    "falsification record/evidence ordering is ambiguous\n",
                )
            record["evidence_path"] = identity["path"]
            record["evidence_sha256"] = identity["sha256"]
    payload["input_manifest"] = input_manifest
    if payload.get("status") != "ran":
        # profile_required is a graceful degrade elsewhere; here it is a block.
        return payload, [REASON_FALSIFICATION_NOT_RUN], markdown, log
    return payload, [], markdown, log


def _persist_falsification(
    attempt_dir: Path, payload: dict[str, Any], markdown: str, log: str
) -> None:
    """Publish already-computed evidence into only the frozen attempt leaves."""

    if payload:
        _write_attempt_json(attempt_dir, "falsification.json", payload)
    if markdown:
        _write_attempt_text(attempt_dir, "falsification.md", markdown)
    if log:
        _write_attempt_text(attempt_dir, "falsification.log", log)


def _persist_proposal_material(
    ctx: GovernanceContext,
    attempt_dir: Path,
    attempt_id: str,
    changes: list[FileChange],
    failure_log: str,
    findings: list[Any],
    verification: dict[str, Any],
    *,
    falsification: dict[str, Any] | None = None,
    falsification_markdown: str = "",
    falsification_log: str = "",
) -> tuple[str, list[str]]:
    """Publish a completed proposal proof after its write-free decision phase."""

    _ensure_attempt_directory(ctx, attempt_dir)
    _write_attempt_json(
        attempt_dir, "baseline.json", baseline_record(ctx, changes, attempt_id)
    )
    _write_attempt_text(attempt_dir, "candidate.patch", unified_diff(changes))
    _store_candidate(attempt_dir, changes)
    (
        _stored_changes,
        candidate_manifest_sha256,
        candidate_errors,
        _pre_apply_missing_directories,
    ) = _candidate_state(ctx, attempt_dir)
    if candidate_errors:
        raise SelfHealEvidenceError(
            REASON_CANDIDATE_DIVERGED_PROPOSAL,
            "the persisted candidate no longer matches its captured byte manifest",
        )
    _write_attempt_text(attempt_dir, "original_failure.log", failure_log)
    _write_attempt_json(
        attempt_dir,
        "structural_review.json",
        {
            "schema": "bugate.self-heal-structural/v1",
            "attempt_id": attempt_id,
            "recorded_at": utc_now(),
            "findings": [finding.as_dict() for finding in findings],
        },
    )
    _write_attempt_json(attempt_dir, "verification.json", verification)
    _write_attempt_text(
        attempt_dir, "before.log", verification_log(verification.get("before") or [])
    )
    _write_attempt_text(
        attempt_dir, "after.log", verification_log(verification.get("after") or [])
    )
    if falsification is not None or falsification_markdown or falsification_log:
        _persist_falsification(
            attempt_dir,
            falsification or {},
            falsification_markdown,
            falsification_log,
        )
    paths = [
        _rel(ctx, path)
        for path in (
            attempt_dir / "baseline.json",
            attempt_dir / "candidate.patch",
            attempt_dir / "structural_review.json",
            attempt_dir / "verification.json",
            attempt_dir / "before.log",
            attempt_dir / "after.log",
            attempt_dir / "falsification.json",
            attempt_dir / "falsification.md",
            attempt_dir / "falsification.log",
        )
        if path.exists()
    ]
    return candidate_manifest_sha256, paths


def falsification_rejected(payload: dict[str, Any]) -> bool:
    summary = payload.get("summary") or {}
    score = summary.get("score")
    threshold = payload.get("threshold")
    if score is None or threshold is None:
        return False
    try:
        return float(score) < float(threshold)
    except (TypeError, ValueError):
        return False


def falsification_inconclusive(payload: dict[str, Any]) -> bool:
    """True unless falsification produced at least one scored mutation result.

    ``status: ran`` only says the scorer process completed.  A baseline that no
    longer satisfies its oracle produces no cases and a null score; treating
    that empty set as success would silently turn configuration drift into a
    rubber stamp.
    """

    if payload.get("status") != "ran":
        return True
    summary = payload.get("summary")
    records = payload.get("records")
    if not isinstance(summary, dict) or not isinstance(records, list):
        return True
    if summary.get("score") is None:
        return True
    try:
        total = int(summary.get("killed") or 0) + int(summary.get("survived") or 0)
    except (TypeError, ValueError):
        return True
    if total <= 0 or summary.get("not_run_evidence"):
        return True
    return any(
        not isinstance(record, dict)
        or record.get("status") == "not_run"
        or not isinstance(record.get("cases"), list)
        or not record.get("cases")
        for record in records
    )


def _clean_killed_cases(
    payload: dict[str, Any], oracle_refs: list[str] | tuple[str, ...]
) -> tuple[list[dict[str, str]], list[str]]:
    """Preserve exact evidence/mutation/oracle identity for clean kills."""

    allowed_oracles = {str(item) for item in oracle_refs}
    exact: list[dict[str, str]] = []
    errors: list[str] = []
    for record in payload.get("records") or []:
        if not isinstance(record, dict) or record.get("status") != "ran":
            continue
        evidence_path = str(record.get("evidence_path") or "")
        for case in record.get("cases") or []:
            if not isinstance(case, dict) or case.get("result") != "killed":
                continue
            mutation_id = str(case.get("mutation") or "").strip()
            clean_oracles = sorted(
                {
                    str(item.get("oracle") or "")
                    for item in (case.get("killed_by") or [])
                    if isinstance(item, dict)
                    and item.get("outcome") == "assertion_fail"
                    and str(item.get("oracle") or "") in allowed_oracles
                }
            )
            if case.get("kill_kind") != "clean" or not clean_oracles:
                continue
            if not evidence_path or not mutation_id:
                errors.append("a clean falsification case lacks exact path or mutation identity")
                continue
            for oracle_id in clean_oracles:
                exact.append(
                    {
                        "evidence_path": evidence_path,
                        "mutation_id": mutation_id,
                        "oracle_id": oracle_id,
                    }
                )
    identities = {
        (item["evidence_path"], item["mutation_id"], item["oracle_id"])
        for item in exact
    }
    if len(identities) != len(exact):
        errors.append("a clean falsification case identity is duplicated")
    return exact, errors


def _immutable_scalar_value(node: ast.AST) -> tuple[bool, Any]:
    """Evaluate only syntax-level immutable scalar literals.

    Negative Python numbers are represented as ``UnaryOp`` over a constant;
    accepting only ``ast.Constant`` would make the documented scalar language
    accidentally exclude ordinary signed integers and floats.  No names,
    calls, containers, overloaded operators, or complex values are evaluated.
    """

    if isinstance(node, ast.Constant) and type(node.value) in {
        str,
        int,
        float,
        bool,
        type(None),
    }:
        return True, node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in {int, float}
    ):
        value = node.operand.value
        return True, value if isinstance(node.op, ast.UAdd) else -value
    return False, None


def _authenticated_python_ast(source: str) -> ast.Module | None:
    """Parse the exact UTF-8 bytes Python will execute, or fail closed.

    ``ast.parse(str)`` ignores PEP-263 byte decoding.  A candidate could use an
    alternate coding cookie (notably UTF-7) so text that looks like a comment to
    the matcher decodes into executable statements for the runner.  Candidate
    files enter this boundary as UTF-8 text, so only UTF-8 source is coherent;
    require its byte-compiled AST to equal the text AST exactly.
    """

    try:
        raw = source.encode("utf-8")
        encoding, _lines = tokenize.detect_encoding(io.BytesIO(raw).readline)
        canonical_encoding = codecs.lookup(encoding).name
        if canonical_encoding not in {"utf-8", "utf-8-sig"}:
            return None
        decoded = raw.decode(encoding)
        text_tree = ast.parse(decoded)
        byte_tree = compile(
            raw,
            "<bugate-candidate>",
            "exec",
            flags=ast.PyCF_ONLY_AST,
            dont_inherit=True,
        )
    except (SyntaxError, UnicodeError, LookupError, ValueError):
        return None
    if not isinstance(byte_tree, ast.Module) or ast.dump(
        byte_tree, include_attributes=False
    ) != ast.dump(text_tree, include_attributes=False):
        return None
    return text_tree


def _literal_negative_control(
    changes: list[FileChange], failure_log: str
) -> dict[str, Any] | None:
    """Derive the one safe literal-control transform from a complete AST diff.

    The before tree must fail with one recorded unresolved Name in an equality
    assertion.  The after tree may differ only by inserting a local immutable
    literal assignment immediately before that assertion and replacing exactly
    that unresolved Name load with the local name.  Every other AST node and
    attribute must be byte-independent structurally identical.
    """

    # The literal exception proves a complete one-file transform.  Ignoring an
    # accompanying helper, conftest, data file, or other candidate write would
    # let unrelated behavior ride on the proof for this test.
    if len(changes) != 1:
        return None
    python_changes = [
        change
        for change in changes
        if change.path.endswith(".py") and Path(change.path).name.startswith("test_")
    ]
    if len(python_changes) != 1:
        return None
    change = python_changes[0]
    if change.before is None or change.after is None or not change.path.endswith(".py"):
        return None
    missing_names = re.findall(r"NameError: name ['\"]([^'\"]+)['\"] is not defined", failure_log)
    if len(set(missing_names)) != 1:
        return None
    missing = missing_names[0]
    before = _authenticated_python_ast(change.before)
    after = _authenticated_python_ast(change.after)
    if before is None or after is None:
        return None
    before_by_name: dict[str, list[ast.FunctionDef]] = {}
    for node in before.body:
        if isinstance(node, ast.FunctionDef):
            before_by_name.setdefault(node.name, []).append(node)
    after_by_name: dict[str, list[ast.FunctionDef]] = {}
    for node in after.body:
        if isinstance(node, ast.FunctionDef):
            after_by_name.setdefault(node.name, []).append(node)

    plans: list[dict[str, Any]] = []
    for function_name, after_matches in after_by_name.items():
        before_matches = before_by_name.get(function_name) or []
        if len(before_matches) != 1 or len(after_matches) != 1:
            continue
        before_function, after_function = before_matches[0], after_matches[0]
        if (
            before_function.decorator_list
            or after_function.decorator_list
            or len(after_function.body) != len(before_function.body) + 1
        ):
            continue
        for inserted_index, candidate_assignment in enumerate(after_function.body):
            if (
                not isinstance(candidate_assignment, ast.Assign)
                or len(candidate_assignment.targets) != 1
                or not isinstance(candidate_assignment.targets[0], ast.Name)
                or inserted_index >= len(before_function.body)
            ):
                continue
            candidate_is_scalar, candidate_value = _immutable_scalar_value(
                candidate_assignment.value
            )
            if not candidate_is_scalar:
                continue
            expected_name = candidate_assignment.targets[0].id
            before_assert = before_function.body[inserted_index]
            after_assert = after_function.body[inserted_index + 1]
            if not isinstance(before_assert, ast.Assert) or not isinstance(
                after_assert, ast.Assert
            ):
                continue

            # Normalize only the two explicitly authorized edits, then require
            # complete module equality.  Unrelated functions/assertions are
            # allowed only when they are structurally identical before/after.
            normalized = copy.deepcopy(after)
            normalized_matches = [
                node
                for node in normalized.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            ]
            if len(normalized_matches) != 1:
                continue
            normalized_function = normalized_matches[0]
            normalized_function.body.pop(inserted_index)
            normalized_assert = normalized_function.body[inserted_index]
            replacements = 0
            for node in ast.walk(normalized_assert):
                if (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id == expected_name
                ):
                    node.id = missing
                    replacements += 1
            if replacements != 1 or ast.dump(
                normalized, include_attributes=False
            ) != ast.dump(before, include_attributes=False):
                continue

            comparison = after_assert.test
            if (
                not isinstance(comparison, ast.Compare)
                or len(comparison.ops) != 1
                or not isinstance(comparison.ops[0], ast.Eq)
                or len(comparison.comparators) != 1
            ):
                continue
            operands = [comparison.left, comparison.comparators[0]]
            expected_operands = [
                node
                for node in operands
                if isinstance(node, ast.Name) and node.id == expected_name
            ]
            observation_operands = [
                node for node in operands if node not in expected_operands
            ]
            if len(expected_operands) != 1 or len(observation_operands) != 1:
                continue
            observation = observation_operands[0]
            if not isinstance(observation, ast.Name):
                continue
            observation_assignments = [
                node
                for node in after.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == observation.id
                and _immutable_scalar_value(node.value)[0]
            ]
            if len(observation_assignments) != 1:
                continue
            observation_assignment = observation_assignments[0]
            _observation_is_scalar, observation_value = _immutable_scalar_value(
                observation_assignment.value
            )
            if (
                type(candidate_value) is not type(observation_value)
                or candidate_value != observation_value
            ):
                continue
            if not _closed_test_module(
                after,
                target_function=function_name,
                target_expected_name=expected_name,
                allow_imports=False,
            ):
                continue
            original = observation_value
            if isinstance(original, bool):
                mutant = not original
            elif isinstance(original, int):
                mutant = original + 1
            elif isinstance(original, float):
                mutant = 0.0 if original != 0.0 else 1.0
            elif isinstance(original, str):
                mutant = original + "__bugate_negative_control__"
            else:
                mutant = "__bugate_negative_control__"
            plans.append(
                {
                    "basis": "safe_literal_observation_falsification",
                    "path": change.path,
                    "assertion_line": after_assert.lineno,
                    "observation_line": observation_assignment.value.lineno,
                    "observation_col": observation_assignment.value.col_offset,
                    "observation_end_line": observation_assignment.value.end_lineno,
                    "observation_end_col": observation_assignment.value.end_col_offset,
                    "mutant_literal": repr(mutant),
                }
            )
    return plans[0] if len(plans) == 1 else None


def _observation_name_binding_sites(tree: ast.AST, name: str) -> int:
    """Count every binding site of ``name`` in the candidate module.

    The observation negative control perturbs a *module-level* assignment, but
    Python resolves the assertion operand by scope, not by module order.  A
    function-local rebinding, a ``global`` write, a parameter, an import alias,
    an ``except .. as``, or a same-named def/class all make that perturbation
    unobservable to the assertion while the assertion itself still executes --
    so a surviving mutant would prove nothing about fake greenness even though
    the assertion line is plainly reachable.  Counting binding sites is a
    positive requirement (exactly the one module assignment, nothing else), not
    a blacklist of rebinding tricks.
    """

    count = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.id == name
        ):
            count += 1
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            count += 1
        elif isinstance(node, ast.arg) and node.arg == name:
            count += 1
        elif isinstance(node, ast.alias) and (
            node.asname or node.name.split(".")[0]
        ) == name:
            count += 1
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            count += 1
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) and node.name == name:
            count += 1
    return count


def _unchanged_observation_negative_control(
    changes: list[FileChange],
) -> dict[str, Any] | None:
    """Derive one independent actual-observation control, or fail closed.

    A declared falsification mutation normally changes the expected evidence.
    That alone is forgeable: a custom comparator can reject exactly that
    expected-evidence value while ignoring the assertion's actual operand.  The
    general JSON lane therefore also perturbs a direct assertion operand whose
    primitive source assignment is inherited byte-semantically from the
    original failing test.  This is a positive provenance requirement, not a
    blacklist of Python indirection tricks.

    More complex observation sources require a profile-owned perturbation
    contract; until one exists, ambiguity deliberately returns ``None`` and the
    caller blocks instead of treating expected-only sensitivity as verified.
    """

    assertion_target = _candidate_assertion_target(changes)
    if assertion_target is None:
        return None
    relative, assertion_line = assertion_target
    change = next((item for item in changes if item.path == relative), None)
    if change is None or change.before is None or change.after is None:
        return None
    before = _authenticated_python_ast(change.before)
    after = _authenticated_python_ast(change.after)
    if before is None or after is None:
        return None
    assertions = [
        node
        for node in ast.walk(after)
        if isinstance(node, ast.Assert) and int(node.lineno) == assertion_line
    ]
    if len(assertions) != 1:
        return None
    assertion = assertions[0]
    comparison = assertion.test
    if (
        not isinstance(comparison, ast.Compare)
        or len(comparison.ops) != 1
        or not isinstance(comparison.ops[0], ast.Eq)
        or len(comparison.comparators) != 1
    ):
        return None

    def assignments(tree: ast.Module, name: str) -> list[ast.Assign]:
        return [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and _immutable_scalar_value(node.value)[0]
        ]

    controls: list[dict[str, Any]] = []
    for operand in (comparison.left, comparison.comparators[0]):
        if not isinstance(operand, ast.Name):
            continue
        # The control below perturbs one module-level assignment.  Unless the
        # operand provably resolves to exactly that binding, the perturbation is
        # invisible to the assertion, and a survivor is an unbound plan rather
        # than a proven fake-green repair.  Fail closed to exit 2 instead of
        # publishing a rejection the evidence does not support.
        if _observation_name_binding_sites(after, operand.id) != 1:
            continue
        before_assignments = assignments(before, operand.id)
        after_assignments = assignments(after, operand.id)
        if len(before_assignments) != 1 or len(after_assignments) != 1:
            continue
        before_assignment = before_assignments[0]
        after_assignment = after_assignments[0]
        if ast.dump(before_assignment, include_attributes=False) != ast.dump(
            after_assignment, include_attributes=False
        ):
            continue
        _original_is_scalar, original = _immutable_scalar_value(after_assignment.value)
        if isinstance(original, bool):
            mutant = not original
        elif isinstance(original, int):
            mutant = original + 1
        elif isinstance(original, float):
            mutant = 0.0 if original != 0.0 else 1.0
        elif isinstance(original, str):
            mutant = original + "__bugate_negative_control__"
        else:
            mutant = "__bugate_negative_control__"
        controls.append(
            {
                "basis": "unchanged_observation_source_perturbation",
                "path": relative,
                "assertion_line": assertion_line,
                "observation_name": operand.id,
                "observation_line": after_assignment.value.lineno,
                "observation_col": after_assignment.value.col_offset,
                "observation_end_line": after_assignment.value.end_lineno,
                "observation_end_col": after_assignment.value.end_col_offset,
                "mutant_literal": repr(mutant),
            }
        )
    return controls[0] if len(controls) == 1 else None


def _primitive_eq_assertion(
    node: ast.stmt, primitive_names: set[str]
) -> bool:
    """True for one side-effect-free equality over primitive names/literals."""

    if not isinstance(node, ast.Assert) or node.msg is not None:
        return False
    comparison = node.test
    if (
        not isinstance(comparison, ast.Compare)
        or len(comparison.ops) != 1
        or not isinstance(comparison.ops[0], ast.Eq)
        or len(comparison.comparators) != 1
    ):
        return False
    return all(
        (
            _immutable_scalar_value(operand)[0]
        )
        or (isinstance(operand, ast.Name) and operand.id in primitive_names)
        for operand in (comparison.left, comparison.comparators[0])
    )


def _closed_test_module(
    tree: ast.Module,
    *,
    external_bindings: set[str] | frozenset[str] = frozenset(),
    target_function: str,
    target_expected_name: str,
    allow_imports: bool,
) -> bool:
    """Validate the complete candidate module against the closed proof grammar."""

    primitive_names = {
        node.targets[0].id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and _immutable_scalar_value(node.value)[0]
    }
    if any(
        isinstance(node, ast.Assign) and node.type_comment is not None
        for node in ast.walk(tree)
    ):
        return False
    functions: dict[str, ast.FunctionDef] = {}
    calls: list[str] = []
    imports: list[str] = []
    assignment_names: list[str] = []
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            imports.append(ast.dump(statement, include_attributes=False))
            continue
        if isinstance(statement, ast.Assign):
            if (
                len(statement.targets) != 1
                or not isinstance(statement.targets[0], ast.Name)
                or statement.targets[0].id
                not in primitive_names | set(external_bindings)
            ):
                return False
            assignment_names.append(statement.targets[0].id)
            continue
        if isinstance(statement, ast.FunctionDef):
            if (
                statement.name in functions
                or statement.decorator_list
                or statement.returns is not None
                or statement.type_comment is not None
                or bool(getattr(statement, "type_params", []))
                or statement.args.args
                or statement.args.posonlyargs
                or statement.args.kwonlyargs
                or statement.args.vararg is not None
                or statement.args.kwarg is not None
                or any(
                    argument.annotation is not None
                    for argument in (
                        *statement.args.posonlyargs,
                        *statement.args.args,
                        *statement.args.kwonlyargs,
                    )
                )
            ):
                return False
            functions[statement.name] = statement
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (
                not isinstance(call.func, ast.Name)
                or call.args
                or call.keywords
            ):
                return False
            calls.append(call.func.id)
            continue
        return False
    expected_imports = {
        ast.dump(ast.parse("import json").body[0], include_attributes=False),
        ast.dump(
            ast.parse("from pathlib import Path").body[0], include_attributes=False
        ),
    }
    if (set(imports) if allow_imports else imports) != (
        expected_imports if allow_imports else []
    ) or (allow_imports and len(imports) != 2):
        return False
    if (
        not functions
        or len(assignment_names) != len(set(assignment_names))
        or any(not name.startswith("test_") for name in functions)
        or sorted(calls) != sorted(functions)
        or len(calls) != len(set(calls))
    ):
        return False
    for name, function in functions.items():
        if name == target_function:
            if allow_imports:
                local_names = {
                    node.targets[0].id
                    for node in function.body[:-1]
                    if isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                }
                if local_names not in (set(), {"evidence_path", target_expected_name}):
                    return False
                if len(function.body) not in {1, 3}:
                    return False
            else:
                if len(function.body) != 2:
                    return False
                assignment = function.body[0]
                if (
                    not isinstance(assignment, ast.Assign)
                    or len(assignment.targets) != 1
                    or not isinstance(assignment.targets[0], ast.Name)
                    or assignment.targets[0].id != target_expected_name
                    or not _immutable_scalar_value(assignment.value)[0]
                ):
                    return False
            allowed = primitive_names | {target_expected_name}
            if not _primitive_eq_assertion(function.body[-1], allowed):
                return False
        elif len(function.body) != 1 or not _primitive_eq_assertion(
            function.body[0], primitive_names
        ):
            return False
    return True


def _workspace_python_import_shadows(root: Path, test_relative: str) -> list[str]:
    """Name candidate-visible Python import shadows for the trusted template.

    The closed external template intentionally imports only ``json`` and
    ``pathlib``.  A workspace-local module with either name (or startup hook)
    would make that AST mean attacker-controlled code under an ordinary Python
    command.  Inspect the two locations an imported-repo runner normally puts
    ahead of the standard library: the governed root and the test script's
    directory plus configured ``PYTHONPATH`` entries.  Missing, non-directory,
    symlinked ``PYTHONPATH`` entries and any ``PYTHONHOME`` fail closed; the
    trusted isolated replay below ignores both as a second control.
    """

    findings: set[str] = set()
    if str(os.environ.get("PYTHONHOME") or "").strip():
        findings.add("environment:PYTHONHOME")
    directories = {root, root / Path(test_relative).parent}
    for raw in str(os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if not raw:
            continue
        entry = Path(raw)
        try:
            if not entry.is_dir() or entry.is_symlink():
                findings.add(f"pythonpath:{entry}")
                continue
        except OSError:
            findings.add(f"pythonpath:{entry}")
            continue
        directories.add(entry)
    file_names = {
        "json.py",
        "json.pyc",
        "pathlib.py",
        "pathlib.pyc",
        "sitecustomize.py",
        "sitecustomize.pyc",
        "usercustomize.py",
        "usercustomize.pyc",
    }
    for directory in directories:
        for name in sorted(file_names):
            path = directory / name
            try:
                if path.exists() or path.is_symlink():
                    findings.add(path.relative_to(root).as_posix())
            except (OSError, ValueError):
                findings.add(str(path))
        for package in ("json", "pathlib"):
            init = directory / package / "__init__.py"
            try:
                if init.exists() or init.is_symlink():
                    findings.add(init.relative_to(root).as_posix())
            except (OSError, ValueError):
                findings.add(str(init))
    return sorted(findings)


def _canonical_external_evidence_assertion(
    ctx: GovernanceContext,
    changes: list[FileChange],
    cases: list[dict[str, Any]],
    failure_log: str,
) -> dict[str, Any] | None:
    """Recognize the closed external-evidence assertion sublanguage.

    Dynamic mutants are visible to arbitrary Python and therefore cannot prove
    honesty on their own.  Acceptance in the general JSON lane is limited to a
    small complete AST language with no user-defined calls, dispatch, arbitrary
    imports, duplicate bindings, wrappers, rebinding, or control flow: exact
    stdlib ``json``/``Path`` imports;
    inherited primitive module observations; literal evidence-name/path
    construction; a direct ``json.loads(path.read_text())[single_key]`` load;
    equality of that built-in scalar with the inherited observation; and direct
    calls of test functions.  Unchanged auxiliary tests are allowed.
    """

    target = _candidate_assertion_target(changes)
    evidence_paths = {case["evidence_path"] for case in cases}
    mutation_paths = {
        str((case.get("mutation") or {}).get("path") or "") for case in cases
    }
    if (
        target is None
        or len(evidence_paths) != 1
        or len(mutation_paths) != 1
        or len(changes) != 1
    ):
        return None
    evidence_path = next(iter(evidence_paths))
    evidence_key = next(iter(mutation_paths))
    if not evidence_key or "." in evidence_key:
        return None
    relative, assertion_line = target
    change = next((item for item in changes if item.path == relative), None)
    if (
        change is None
        or change.before is None
        or change.after is None
    ):
        return None
    before = _authenticated_python_ast(change.before)
    after = _authenticated_python_ast(change.after)
    if before is None or after is None:
        return None

    def identity(node: ast.AST) -> str:
        return ast.dump(node, include_attributes=False)

    import_json = ast.parse("import json").body[0]
    import_path = ast.parse("from pathlib import Path").body[0]
    expected_imports = {identity(import_json), identity(import_path)}
    imports = [node for node in after.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    if {identity(node) for node in imports} != expected_imports or len(imports) != 2:
        return None

    def path_kind(node: ast.AST) -> str | None:
        with_name = (
            isinstance(node, ast.Call)
            and not node.keywords
            and len(node.args) == 1
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "with_name"
            and isinstance(node.func.value, ast.Call)
            and not node.func.value.keywords
            and len(node.func.value.args) == 1
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "Path"
            and isinstance(node.func.value.args[0], ast.Name)
            and node.func.value.args[0].id == "__file__"
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "EVIDENCE_NAME"
        )
        parents_probe = (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and node.right.value == "probe.json"
            and isinstance(node.left, ast.BinOp)
            and isinstance(node.left.op, ast.Div)
            and isinstance(node.left.right, ast.Constant)
            and node.left.right.value == "evidence"
            and isinstance(node.left.left, ast.Subscript)
            and isinstance(node.left.left.slice, ast.Constant)
            and node.left.left.slice.value == 1
            and isinstance(node.left.left.value, ast.Attribute)
            and node.left.left.value.attr == "parents"
            and isinstance(node.left.left.value.value, ast.Call)
            and not node.left.left.value.value.keywords
            and len(node.left.left.value.value.args) == 1
            and isinstance(node.left.left.value.value.func, ast.Name)
            and node.left.left.value.value.func.id == "Path"
            and isinstance(node.left.left.value.value.args[0], ast.Name)
            and node.left.left.value.value.args[0].id == "__file__"
        )
        return "with_name" if with_name else "parents_probe" if parents_probe else None

    def expected_load(node: ast.AST, path_name: str) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == evidence_key
            and isinstance(node.value, ast.Call)
            and not node.value.keywords
            and len(node.value.args) == 1
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "loads"
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "json"
            and isinstance(node.value.args[0], ast.Call)
            and not node.value.args[0].args
            and not node.value.args[0].keywords
            and isinstance(node.value.args[0].func, ast.Attribute)
            and node.value.args[0].func.attr == "read_text"
            and isinstance(node.value.args[0].func.value, ast.Name)
            and node.value.args[0].func.value.id == path_name
        )

    changed_asserts = [
        node
        for node in ast.walk(after)
        if isinstance(node, ast.Assert) and int(node.lineno) == assertion_line
    ]
    if len(changed_asserts) != 1:
        return None
    changed_assert = changed_asserts[0]
    comparison = changed_assert.test
    if (
        not isinstance(comparison, ast.Compare)
        or len(comparison.ops) != 1
        or not isinstance(comparison.ops[0], ast.Eq)
        or len(comparison.comparators) != 1
    ):
        return None

    normalized = copy.deepcopy(after)
    normalized.body = [
        node
        for node in normalized.body
        if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    removed: list[ast.stmt] = []
    evidence_name: str | None = None
    expected_name = ""
    path_name = ""
    path_shape = ""

    # Module-level form: remove exact EVIDENCE_NAME/PATH/EXPECTED bindings.
    module_assignments = [
        node
        for node in normalized.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    by_name = {node.targets[0].id: node for node in module_assignments}
    name_node = by_name.get("EVIDENCE_NAME")
    path_node = by_name.get("EVIDENCE_PATH")
    if (
        name_node is not None
        and isinstance(name_node.value, ast.Constant)
        and isinstance(name_node.value.value, str)
    ):
        evidence_name = name_node.value.value
    if (
        evidence_name is not None
        and path_node is not None
        and path_kind(path_node.value) == "with_name"
    ):
        evidence_name = name_node.value.value
        path_name = "EVIDENCE_PATH"
        path_shape = "with_name"
        expected_nodes = [
            node for node in module_assignments if expected_load(node.value, path_name)
        ]
        if len(expected_nodes) != 1:
            return None
        expected_node = expected_nodes[0]
        expected_name = expected_node.targets[0].id
        removed = [name_node, path_node, expected_node]
        normalized.body = [node for node in normalized.body if node not in removed]

    # The fixture-bound module form names evidence/probe.json directly without
    # an EVIDENCE_NAME helper.
    if not expected_name and path_node is not None and path_kind(path_node.value) == "parents_probe":
        path_name = "EVIDENCE_PATH"
        path_shape = "parents_probe"
        expected_nodes = [
            node for node in module_assignments if expected_load(node.value, path_name)
        ]
        if len(expected_nodes) != 1:
            return None
        expected_node = expected_nodes[0]
        expected_name = expected_node.targets[0].id
        normalized.body = [
            node for node in normalized.body if node is not path_node and node is not expected_node
        ]

    # Function-local form: remove exact evidence_path/expected assignments.
    if not expected_name:
        functions = [
            node
            for node in normalized.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(child, ast.Assert) and int(child.lineno) == assertion_line
                for child in ast.walk(node)
            )
        ]
        if len(functions) != 1:
            return None
        function = functions[0]
        path_nodes = [
            node
            for node in function.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "evidence_path"
            and path_kind(node.value) in {"parents_probe", "with_name"}
        ]
        if len(path_nodes) != 1:
            return None
        path_node = path_nodes[0]
        path_name = "evidence_path"
        path_shape = str(path_kind(path_node.value))
        if path_shape == "with_name":
            if name_node is None or evidence_name is None:
                return None
            normalized.body = [node for node in normalized.body if node is not name_node]
        expected_nodes = [
            node
            for node in function.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and expected_load(node.value, path_name)
        ]
        if len(expected_nodes) != 1:
            return None
        expected_node = expected_nodes[0]
        expected_name = expected_node.targets[0].id
        function.body = [
            node
            for node in function.body
            if node is not path_node and node is not expected_node
        ]

    normalized_asserts = [
        node
        for node in ast.walk(normalized)
        if isinstance(node, ast.Assert) and int(node.lineno) == assertion_line
    ]
    if len(normalized_asserts) != 1:
        return None
    missing_candidates = {
        node.id
        for node in ast.walk(before)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    log_names = set(
        re.findall(r"NameError: name ['\"]([^'\"]+)['\"] is not defined", failure_log)
    )
    if log_names:
        missing_candidates &= log_names
    matching_missing: list[str] = []
    for missing in sorted(missing_candidates):
        candidate_normalized = copy.deepcopy(normalized)
        candidate_asserts = [
            node
            for node in ast.walk(candidate_normalized)
            if isinstance(node, ast.Assert) and int(node.lineno) == assertion_line
        ]
        if len(candidate_asserts) != 1:
            continue
        replacements = 0
        for node in ast.walk(candidate_asserts[0]):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == expected_name
            ):
                node.id = missing
                replacements += 1
        if replacements == 1 and identity(candidate_normalized) == identity(before):
            matching_missing.append(missing)
    if len(matching_missing) != 1:
        return None

    operand_names = [
        node.id
        for node in (comparison.left, comparison.comparators[0])
        if isinstance(node, ast.Name)
    ]
    if (
        len(operand_names) != 2
        or len(set(operand_names)) != 2
        or expected_name not in operand_names
    ):
        return None
    observation_name = next(name for name in operand_names if name != expected_name)
    original_assignments = [
        node
        for node in before.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == observation_name
        and _immutable_scalar_value(node.value)[0]
    ]
    if len(original_assignments) != 1:
        return None

    target_functions = [
        node
        for node in after.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.Assert) and int(child.lineno) == assertion_line
            for child in ast.walk(node)
        )
    ]
    if path_shape == "with_name" and path_name == "EVIDENCE_PATH":
        external_bindings = {"EVIDENCE_NAME", "EVIDENCE_PATH", expected_name}
    elif path_shape == "with_name":
        external_bindings = {"EVIDENCE_NAME"}
    elif path_name == "EVIDENCE_PATH":
        external_bindings = {"EVIDENCE_PATH", expected_name}
    else:
        external_bindings = set()
    if len(target_functions) != 1 or not _closed_test_module(
        after,
        external_bindings=external_bindings,
        target_function=target_functions[0].name,
        target_expected_name=expected_name,
        allow_imports=True,
    ):
        return None

    candidate_path = (
        Path(relative).parent / str(evidence_name)
        if path_shape == "with_name"
        else Path(relative).parent / ".." / "evidence" / "probe.json"
    )
    normalized_path = PurePosixPath(os.path.normpath(candidate_path.as_posix())).as_posix()
    if normalized_path != evidence_path:
        return None
    try:
        live_payload = json.loads((ctx.root / normalized_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    _observed_is_scalar, observed_value = _immutable_scalar_value(
        original_assignments[0].value
    )
    evidence_value = live_payload.get(evidence_key) if isinstance(live_payload, dict) else None
    if type(evidence_value) is not type(observed_value) or evidence_value != observed_value:
        return None
    shadows = _workspace_python_import_shadows(ctx.root, relative)
    if shadows:
        return None
    return {
        "path": relative,
        "assertion_line": assertion_line,
        "evidence_path": normalized_path,
        "evidence_key": evidence_key,
        "observation_name": observation_name,
        "path_shape": path_shape,
        "trusted_import_provenance": "python_-I_-S",
    }


def _apply_literal_negative_control(
    sandbox: Path, plan: dict[str, Any]
) -> dict[str, str]:
    relative, target = _safe_tree_target(
        sandbox,
        plan["path"],
        reason=REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
        label="literal reverse-verification input",
    )
    body = target.read_bytes()
    # CPython records AST columns as UTF-8 byte offsets, not Python ``str``
    # indices.  Applying them to decoded text corrupts a line when the literal
    # itself (or an earlier token) contains non-ASCII characters.
    body.decode("utf-8")
    lines = body.splitlines(keepends=True)
    start_line = int(plan["observation_line"]) - 1
    end_line = int(plan["observation_end_line"]) - 1
    if start_line != end_line or start_line < 0 or start_line >= len(lines):
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            "literal observation source span is ambiguous",
        )
    line = lines[start_line]
    start = int(plan["observation_col"])
    end = int(plan["observation_end_col"])
    # ``compile(bytes, ...)`` strips a UTF-8 BOM before assigning first-line
    # AST column offsets.  The persisted candidate retains those three bytes,
    # so compensate only for a span on the physical first line.
    if start_line == 0 and body.startswith(codecs.BOM_UTF8):
        start += len(codecs.BOM_UTF8)
        end += len(codecs.BOM_UTF8)
    if start < 0 or end < start or end > len(line):
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            "literal observation source byte span is invalid",
        )
    mutant = str(plan["mutant_literal"]).encode("utf-8")
    mutated_line = line[:start] + mutant + line[end:]
    mutated = b"".join([*lines[:start_line], mutated_line, *lines[start_line + 1 :]])
    mode = stat.S_IMODE(os.lstat(target).st_mode)
    _atomic_bytes(target, mutated, replace=True, mode=mode)
    return {
        "path": relative,
        "before_sha256": _sha256_bytes(body),
        "after_sha256": _sha256_bytes(mutated),
    }


def _apply_json_negative_control(
    sandbox: Path, case: dict[str, Any]
) -> dict[str, str]:
    relative, target = _safe_tree_target(
        sandbox,
        case["evidence_path"],
        reason=REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
        label="reverse-verification evidence",
    )
    try:
        mode = os.lstat(target).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise OSError("evidence is not a non-symlink regular file")
        body = target.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            f"reverse-verification evidence is unreadable: {relative}: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            f"reverse-verification evidence is not a JSON object: {relative}",
        )
    applied, detail = apply_mutation(case["mutation"], payload)
    if not applied:
        raise SelfHealEvidenceError(
            REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE,
            f"reverse-verification mutation cannot be applied: {relative}: {detail}",
        )
    mutated = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    _atomic_bytes(target, mutated, replace=True, mode=stat.S_IMODE(mode))
    return {
        "path": relative,
        "before_sha256": _sha256_bytes(body),
        "after_sha256": _sha256_bytes(mutated),
        "detail": detail,
    }


def _clean_assertion_failure(
    records: list[dict[str, Any]], relative: str, assertion_line: int | None
) -> bool:
    """Reject crashes; accept only an AssertionError attributed to the changed test."""

    if not records or not any(item.get("exit_code") != 0 for item in records):
        return False
    combined = "\n".join(
        str(item.get("stdout") or "") + "\n" + str(item.get("stderr") or "")
        for item in records
    )
    if any(item.get("exit_code") in {124, 126, 127} for item in records):
        return False
    forbidden = (
        "SyntaxError",
        "ImportError",
        "ModuleNotFoundError",
        "JSONDecodeError",
        "TimeoutExpired",
    )
    if any(name in combined for name in forbidden) or "AssertionError" not in combined:
        return False
    names = {relative, Path(relative).name}
    if assertion_line is None:
        return any(name in combined for name in names)
    patterns = [rf"{re.escape(name)}[^\n]*line {assertion_line}\b" for name in names]
    patterns.extend(rf"{re.escape(name)}:{assertion_line}\b" for name in names)
    return any(re.search(pattern, combined) for pattern in patterns)


def _candidate_assertion_target(
    changes: list[FileChange],
) -> tuple[str, int] | None:
    """Return the only semantically changed Python assertion.

    Counting every assertion in the candidate is needlessly over-strict: an
    honest file may retain unrelated assertions while repairing exactly one.
    Subtract the before-tree assertion multiset from the after tree by complete
    AST identity, then require exactly one unmatched after assertion.  This
    remains fail closed for duplicate/ambiguous changed assertions.
    """

    targets: list[tuple[str, int]] = []
    for change in changes:
        if not change.path.endswith(".py") or change.after is None:
            continue
        after = _authenticated_python_ast(change.after)
        before = (
            _authenticated_python_ast(change.before)
            if change.before is not None
            else ast.Module(body=[])
        )
        if after is None or before is None:
            return None
        unchanged: dict[str, int] = {}
        for node in ast.walk(before):
            if isinstance(node, ast.Assert):
                identity = ast.dump(node, include_attributes=False)
                unchanged[identity] = unchanged.get(identity, 0) + 1
        for node in ast.walk(after):
            if not isinstance(node, ast.Assert):
                continue
            identity = ast.dump(node, include_attributes=False)
            if unchanged.get(identity, 0) > 0:
                unchanged[identity] -= 1
            else:
                targets.append((change.path, int(node.lineno)))
    return targets[0] if len(targets) == 1 else None


def run_repaired_test_mutation_gate(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    changes: list[FileChange],
    falsification: dict[str, Any],
    oracle_refs: list[str] | tuple[str, ...],
    failure_log: str,
) -> tuple[dict[str, Any], str | None]:
    """Require pristine PASS -> clean assertion failure -> restored PASS."""

    payload: dict[str, Any] = {
        "schema": REPAIRED_TEST_MUTATION_SCHEMA,
        "status": "blocked",
        "cases": [],
        "coverage_boundary": (
            "the strict literal-NameError subset is proven by a complete AST delta; "
            "the general JSON lane additionally requires a whole-module canonical "
            "external-evidence AST delta plus one unchanged primitive observation; "
            "both require coherent UTF-8 byte/text syntax; a surviving negative "
            "control is published as a proven fake-green repair only when its "
            "assertion binding is carried by one of those closed static proofs, "
            "never by candidate-produced execution evidence; all other or "
            "ambiguous shapes fail closed"
        ),
    }
    spec_path = falsification_spec_path(policy, ctx.root)
    if spec_path is None:
        payload["plan_errors"] = ["falsification spec is missing"]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    try:
        _manifest_spec, live_manifest = _falsification_input_manifest(ctx, policy)
    except SelfHealEvidenceError as exc:
        payload["plan_errors"] = [str(exc)]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    if falsification.get("input_manifest") != live_manifest:
        payload["plan_errors"] = [
            "falsification inputs changed before reverse verification"
        ]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    try:
        spec = load_spec(spec_path)
    except (OSError, ValueError) as exc:
        payload["plan_errors"] = [f"falsification spec cannot be parsed: {exc}"]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    raw_mutations = [item for item in (spec.get("mutations") or []) if isinstance(item, dict)]
    mutation_ids = [str(item.get("id") or "").strip() for item in raw_mutations]
    if not mutation_ids or any(not item for item in mutation_ids) or len(set(mutation_ids)) != len(
        mutation_ids
    ):
        payload["plan_errors"] = ["falsification mutation ids are missing or duplicated"]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    mutations = dict(zip(mutation_ids, raw_mutations))
    exact_cases, case_errors = _clean_killed_cases(falsification, oracle_refs)
    if case_errors:
        payload["plan_errors"] = case_errors
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    for case in exact_cases:
        mutation = mutations.get(case["mutation_id"])
        if mutation is None:
            payload.setdefault("plan_errors", []).append(
                f"live spec lacks exact mutation {case['mutation_id']!r}"
            )
        else:
            case["mutation"] = dict(mutation)
    literal_plan = _literal_negative_control(changes, failure_log)
    assertion_target = _candidate_assertion_target(changes)
    observation_plan = _unchanged_observation_negative_control(changes)
    canonical_external = _canonical_external_evidence_assertion(
        ctx, changes, exact_cases, failure_log
    )
    payload["literal_control"] = literal_plan is not None
    payload["canonical_external_control"] = canonical_external
    payload["observation_control"] = (
        {
            "path": observation_plan["path"],
            "assertion_line": observation_plan["assertion_line"],
            "observation_name": observation_plan["observation_name"],
        }
        if observation_plan is not None
        else None
    )
    payload["assertion_target"] = (
        {"path": assertion_target[0], "line": assertion_target[1]}
        if assertion_target is not None
        else None
    )
    if not exact_cases and literal_plan is None:
        payload["plan_errors"] = ["no exact clean case is bound to the triaged oracle refs"]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    if payload.get("plan_errors"):
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="bugate-reverse-pristine-") as tmp:
        pristine = Path(tmp) / "workspace"
        try:
            _copy_candidate_sandbox(ctx, changes, pristine)
        except SelfHealEvidenceError as exc:
            payload["plan_errors"] = [str(exc)]
            return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
        env["BUGATE_PROJECT_ROOT"] = str(pristine)
        baseline = _run_commands(policy["verification_commands"], pristine, env)
        trusted_plan = literal_plan or canonical_external
        trusted_baseline = (
            _run_isolated_canonical_test(pristine, str(trusted_plan["path"]), env)
            if trusted_plan is not None
            else []
        )
    payload["pristine"] = baseline
    if not baseline or any(item.get("exit_code") != 0 for item in baseline):
        payload["plan_errors"] = ["candidate is not green in a fresh pristine sandbox"]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    if trusted_plan is not None:
        payload["trusted_pristine"] = trusted_baseline
        if not trusted_baseline or any(
            item.get("exit_code") != 0 for item in trusted_baseline
        ):
            payload["plan_errors"] = [
                "candidate does not pass with trusted isolated Python semantics"
            ]
            return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE

    plans: list[dict[str, Any]] = [
        {**case, "basis": "declared_json_counterexample"} for case in exact_cases
    ]
    if literal_plan is not None:
        # The literal lane is engine-derived, never supplied as an arbitrary
        # profile source patch.  Prefer it for the required honest literal case.
        plans = [literal_plan]
    elif observation_plan is not None:
        # Expected-evidence sensitivity and actual-observation sensitivity are
        # independent obligations.  A candidate must kill both controls.
        plans.append(observation_plan)
    for plan in plans:
        source_control = plan["basis"] in {
            "safe_literal_observation_falsification",
            "unchanged_observation_source_perturbation",
        }
        outcome: dict[str, Any] = {
            "basis": plan["basis"],
            "path": plan["path"] if source_control else plan["evidence_path"],
            "result": "unavailable",
        }
        if plan["basis"] == "safe_literal_observation_falsification":
            binding: dict[str, Any] = {
                "status": "confirmed",
                "basis": "closed_literal_source_to_assertion",
            }
        elif plan["basis"] == "unchanged_observation_source_perturbation":
            if (
                canonical_external is not None
                and canonical_external.get("path") == plan.get("path")
                and canonical_external.get("assertion_line")
                == plan.get("assertion_line")
            ):
                binding = {
                    "status": "confirmed",
                    "basis": "closed_canonical_observation_source_to_assertion",
                }
            else:
                # The retired ``direct_observation_source_to_assertion`` basis
                # confirmed this binding from an in-band execution witness: the
                # engine injected a sentinel ``raise`` above the mapped assert,
                # re-ran the candidate's own verification commands, and accepted
                # the candidate's stdout/stderr as proof that the assertion had
                # executed.  The sentinel and the candidate share one
                # interpreter, and the candidate can read the injected line out
                # of its own ``__file__`` before deciding what to emit, so every
                # effect the sentinel can produce the candidate can produce
                # without executing the assertion -- a nonce does not help
                # because the nonce is in the source the candidate reads, and a
                # decoy injection does not help because the candidate can key on
                # the sentinel sitting immediately above an ``assert``.  No
                # in-band witness is sound here, and a governance record that
                # publishes "proven fake-green" must not rest on testimony from
                # the candidate it accuses.  Fail closed: the negative control
                # still runs and its survival is still recorded, but only the
                # closed literal and closed canonical bases may confirm a
                # binding, so the caller blocks at exit 2 instead of rejecting.
                binding = {
                    "status": "unavailable",
                    "basis": "unproven_observation_source_to_assertion",
                    "detail": (
                        "binding the perturbed observation source to this "
                        "assertion operand requires a closed whole-module "
                        "proof; an in-band execution witness is candidate "
                        "controlled evidence and cannot establish it"
                    ),
                }
        else:
            canonical_matches = (
                canonical_external is not None
                and canonical_external.get("evidence_path") == plan.get("evidence_path")
                and canonical_external.get("evidence_key")
                == str((plan.get("mutation") or {}).get("path") or "")
            )
            if canonical_matches:
                binding = {
                    "status": "confirmed",
                    "basis": "closed_canonical_json_source_to_assertion",
                }
            else:
                binding = {
                    "status": "unavailable",
                    "basis": "declared_json_counterexample",
                    "detail": "candidate consumption of the exact declared input is unbound or ambiguous",
                }
        outcome["binding"] = binding
        with tempfile.TemporaryDirectory(prefix="bugate-reverse-mutant-") as tmp:
            sandbox = Path(tmp) / "workspace"
            try:
                _copy_candidate_sandbox(ctx, changes, sandbox)
                if source_control:
                    outcome["mutation"] = _apply_literal_negative_control(sandbox, plan)
                    relative = str(plan["path"])
                    assertion_line = int(plan["assertion_line"])
                else:
                    outcome["mutation"] = _apply_json_negative_control(sandbox, plan)
                    relative, assertion_line = assertion_target or ("", None)
                    outcome.update(
                        {
                            "evidence_path": plan["evidence_path"],
                            "mutation_id": plan["mutation_id"],
                            "oracle_id": plan["oracle_id"],
                        }
                    )
                env["BUGATE_PROJECT_ROOT"] = str(sandbox)
                records = _run_commands(policy["verification_commands"], sandbox, env)
                outcome["commands"] = records
                if all(item.get("exit_code") == 0 for item in records):
                    outcome["result"] = "survived"
                elif assertion_target is None and not source_control:
                    outcome["detail"] = "the changed governed assertion is ambiguous"
                elif _clean_assertion_failure(records, relative, assertion_line):
                    outcome["result"] = "clean_assertion_failure"
                else:
                    outcome["detail"] = "mutant failed outside the mapped assertion"
                if trusted_plan is not None:
                    trusted_records = _run_isolated_canonical_test(
                        sandbox, str(trusted_plan["path"]), env
                    )
                    outcome["trusted_commands"] = trusted_records
                    if all(item.get("exit_code") == 0 for item in trusted_records):
                        outcome["trusted_result"] = "survived"
                    elif _clean_assertion_failure(
                        trusted_records, str(trusted_plan["path"]), assertion_line
                    ):
                        outcome["trusted_result"] = "clean_assertion_failure"
                    else:
                        outcome["trusted_result"] = "unavailable"
            except (OSError, UnicodeError, SelfHealEvidenceError) as exc:
                outcome["detail"] = str(exc)
        payload["cases"].append(outcome)

    if any(
        item.get("binding", {}).get("status") == "confirmed"
        and (
            item["result"] == "survived"
            or item.get("trusted_result") == "survived"
        )
        for item in payload["cases"]
    ):
        payload["status"] = "rejected"
        return payload, REASON_REPAIRED_TEST_SURVIVES_MUTATION
    if any(
        item.get("binding", {}).get("status") != "confirmed"
        for item in payload["cases"]
    ):
        payload["plan_errors"] = [
            "one or more required mutation plans lack an exact candidate-visible binding"
        ]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    if literal_plan is None and canonical_external is None:
        payload["plan_errors"] = [
            "candidate is outside the canonical external-evidence assertion language"
        ]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    if any(
        item["result"] != "clean_assertion_failure"
        or (
            trusted_plan is not None
            and item.get("trusted_result") != "clean_assertion_failure"
        )
        for item in payload["cases"]
    ):
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE

    with tempfile.TemporaryDirectory(prefix="bugate-reverse-restored-") as tmp:
        restored = Path(tmp) / "workspace"
        try:
            _copy_candidate_sandbox(ctx, changes, restored)
        except SelfHealEvidenceError as exc:
            payload["plan_errors"] = [str(exc)]
            return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
        env["BUGATE_PROJECT_ROOT"] = str(restored)
        restored_records = _run_commands(policy["verification_commands"], restored, env)
        trusted_restored = (
            _run_isolated_canonical_test(restored, str(trusted_plan["path"]), env)
            if trusted_plan is not None
            else []
        )
    payload["restored"] = restored_records
    if not restored_records or any(item.get("exit_code") != 0 for item in restored_records):
        payload["plan_errors"] = ["candidate did not return green after restoring inputs"]
        return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    if trusted_plan is not None:
        payload["trusted_restored"] = trusted_restored
        if not trusted_restored or any(
            item.get("exit_code") != 0 for item in trusted_restored
        ):
            payload["plan_errors"] = [
                "candidate did not restore under trusted isolated Python semantics"
            ]
            return payload, REASON_REPAIRED_TEST_MUTATION_UNAVAILABLE
    payload["status"] = "verified"
    return payload, None


def _accepted_precode_evidence(
    ctx: GovernanceContext, triage_payload: dict[str, Any]
) -> tuple[list[dict[str, str]], str]:
    """Re-read the accepted brief/inventory named by the triage receipt."""

    expected_paths = sorted(
        workspace_rel(ctx.artifact_dir / name, ctx.root)
        for name in ("01_business_brief.md", "03_inventory.yaml")
    )
    references = triage_payload.get("evidence_refs")
    if not isinstance(references, list):
        raise SelfHealEvidenceError(
            REASON_REVIEW_PRECODE_DRIFT,
            "triage evidence does not contain accepted pre-code references",
        )
    by_path: dict[str, dict[str, Any]] = {}
    for raw in references:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            continue
        path = str(raw["path"])
        if path in expected_paths:
            if path in by_path:
                raise SelfHealEvidenceError(
                    REASON_REVIEW_PRECODE_DRIFT,
                    f"triage pre-code evidence path is duplicated: {path}",
                )
            by_path[path] = raw
    if sorted(by_path) != expected_paths:
        raise SelfHealEvidenceError(
            REASON_REVIEW_PRECODE_DRIFT,
            "triage evidence omits the accepted business brief or inventory",
        )

    live: list[dict[str, str]] = []
    for relative in expected_paths:
        recorded_sha = by_path[relative].get("sha256")
        if not isinstance(recorded_sha, str) or not re.fullmatch(
            r"[0-9a-f]{64}", recorded_sha
        ):
            raise SelfHealEvidenceError(
                REASON_REVIEW_PRECODE_DRIFT,
                f"triage pre-code evidence hash is invalid: {relative}",
            )
        try:
            _relative, path = _safe_tree_target(
                ctx.root,
                relative,
                reason=REASON_REVIEW_PRECODE_DRIFT,
                label="accepted pre-code evidence",
            )
            live_sha = _required_sha256_file(path, "accepted pre-code evidence")
        except SelfHealEvidenceError as exc:
            raise SelfHealEvidenceError(REASON_REVIEW_PRECODE_DRIFT, str(exc)) from exc
        if live_sha != recorded_sha or gate_status(path) != "passed":
            raise SelfHealEvidenceError(
                REASON_REVIEW_PRECODE_DRIFT,
                f"accepted pre-code evidence changed after triage: {relative}",
            )
        live.append({"path": relative, "sha256": live_sha})
    return live, _sha256_bytes(_json_bytes(live))


def _validated_falsification_sha256(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    attempt_dir: Path,
) -> str:
    """Bind archived falsification proof to its still-live spec and evidence."""

    try:
        _relative, path = _safe_tree_target(
            attempt_dir,
            "falsification.json",
            reason=REASON_REVIEW_FALSIFICATION_EVIDENCE_MISMATCH,
            label="falsification result",
        )
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise OSError("falsification result is not a regular file")
        body = path.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (SelfHealEvidenceError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelfHealEvidenceError(
            REASON_REVIEW_FALSIFICATION_EVIDENCE_MISMATCH,
            f"falsification result cannot be authenticated: {exc}",
        ) from exc
    recorded = payload.get("input_manifest") if isinstance(payload, dict) else None
    if (
        not isinstance(recorded, dict)
        or set(recorded) != {"schema", "spec", "evidence", "sha256"}
        or recorded.get("schema") != "bugate.self-heal-falsification-inputs/v1"
        or not isinstance(recorded.get("spec"), dict)
        or not isinstance(recorded.get("evidence"), list)
        or not re.fullmatch(r"[0-9a-f]{64}", str(recorded.get("sha256") or ""))
    ):
        raise SelfHealEvidenceError(
            REASON_REVIEW_FALSIFICATION_EVIDENCE_MISMATCH,
            "the archived falsification input manifest is missing or malformed",
        )
    try:
        _spec, live = _falsification_input_manifest(ctx, policy)
    except SelfHealEvidenceError as exc:
        raise SelfHealEvidenceError(
            REASON_REVIEW_FALSIFICATION_INPUT_DRIFT,
            f"live falsification inputs cannot be authenticated: {exc}",
        ) from exc
    if recorded != live:
        raise SelfHealEvidenceError(
            REASON_REVIEW_FALSIFICATION_INPUT_DRIFT,
            "the falsification spec or exact evidence bytes changed after proposal",
        )
    return _sha256_bytes(body)


def _review_expectation(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    attempt_dir: Path,
    candidate_manifest_sha256: str,
    precode_evidence_sha256: str,
    oracle_refs: list[str] | tuple[str, ...],
    expected_failure_sha256: str,
) -> ReviewExpectation:
    """Bind an independent review to the exact live proposal evidence."""

    original_failure_sha256 = _required_sha256_file(
        attempt_dir / "original_failure.log", "captured original failure"
    )
    if original_failure_sha256 != expected_failure_sha256:
        raise SelfHealEvidenceError(
            REASON_REVIEW_EVIDENCE_DRIFT,
            "captured original failure differs from the triage log hash",
        )
    return ReviewExpectation(
        uc=ctx.uc,
        attempt_id=attempt_dir.name,
        candidate_manifest_sha256=candidate_manifest_sha256,
        candidate_patch_sha256=_required_sha256_file(
            attempt_dir / "candidate.patch", "candidate patch"
        ),
        precode_evidence_sha256=precode_evidence_sha256,
        original_failure_sha256=original_failure_sha256,
        verification_sha256=_required_sha256_file(
            attempt_dir / "verification.json", "sandbox verification"
        ),
        before_log_sha256=_required_sha256_file(
            attempt_dir / "before.log", "pre-repair verification log"
        ),
        after_log_sha256=_required_sha256_file(
            attempt_dir / "after.log", "post-repair verification log"
        ),
        falsification_sha256=_validated_falsification_sha256(
            ctx, policy, attempt_dir
        ),
        oracle_refs=tuple(sorted(set(str(ref) for ref in oracle_refs))),
    )


def _load_review_context(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    store: sidecar.SelfHealSidecar,
    attempt_dir: Path,
    candidate_manifest_sha256: str,
) -> tuple[ReviewExpectation | None, list[str]]:
    """Authenticate the immutable proposal context against every live byte."""

    try:
        _relative, context_path = _safe_tree_target(
            attempt_dir,
            "review_context.json",
            reason=REASON_REVIEW_CONTEXT_INVALID,
            label="review context",
        )
        body = context_path.read_bytes()
        context = json.loads(body.decode("utf-8"))
    except (SelfHealEvidenceError, OSError, UnicodeError, json.JSONDecodeError):
        return None, [REASON_REVIEW_CONTEXT_INVALID]
    triage_payload = store.latest_payload(sidecar.EVENT_TRIAGE)
    try:
        precode_evidence, precode_evidence_sha256 = _accepted_precode_evidence(
            ctx, triage_payload
        )
    except SelfHealEvidenceError as exc:
        return None, [exc.reason]
    expected_paths = [
        *(item["path"] for item in precode_evidence),
        "candidate.patch",
        "original_failure.log",
        "verification.json",
        "before.log",
        "after.log",
        "falsification.json",
    ]
    if (
        not isinstance(context, dict)
        or set(context) != {"schema", "binding", "evidence_paths"}
        or context.get("schema") != "bugate.self-heal-review-context/v1"
        or not isinstance(context.get("binding"), dict)
        or context.get("evidence_paths") != expected_paths
    ):
        return None, [REASON_REVIEW_CONTEXT_INVALID]
    handoff = store.latest_payload(sidecar.EVENT_HEALER_HANDOFF)
    if handoff.get("review_context_sha256") != _sha256_bytes(body):
        return None, [REASON_REVIEW_CONTEXT_INVALID]
    try:
        expected = _review_expectation(
            ctx,
            policy,
            attempt_dir,
            candidate_manifest_sha256,
            precode_evidence_sha256,
            list(triage_payload.get("oracle_refs") or []),
            str(triage_payload.get("original_log_sha256") or ""),
        )
    except SelfHealEvidenceError as exc:
        return None, [exc.reason]
    expected_binding = expected.as_binding()
    if context["binding"] != expected_binding:
        observed_binding = context["binding"]
        field_reasons = (
            (("uc",), "independent_review_uc_mismatch"),
            (("attempt_id",), "independent_review_attempt_mismatch"),
            (
                ("candidate_manifest_sha256", "candidate_patch_sha256"),
                "independent_review_candidate_mismatch",
            ),
            (("precode_evidence_sha256",), REASON_REVIEW_PRECODE_DRIFT),
            (("original_failure_sha256",), "independent_review_failure_evidence_mismatch"),
            (("verification_sha256",), "independent_review_verification_evidence_mismatch"),
            (("before_log_sha256",), "independent_review_before_log_mismatch"),
            (("after_log_sha256",), "independent_review_after_log_mismatch"),
            (("falsification_sha256",), "independent_review_falsification_evidence_mismatch"),
            (("oracle_refs",), "independent_review_oracle_mismatch"),
        )
        reasons = [
            reason
            for fields, reason in field_reasons
            if any(observed_binding.get(field) != expected_binding[field] for field in fields)
        ]
        return None, reasons or [REASON_REVIEW_EVIDENCE_DRIFT]
    if handoff.get("review_binding") != expected_binding:
        return None, [REASON_REVIEW_CONTEXT_INVALID]
    return expected, []


def _review_receipt(
    store: sidecar.SelfHealSidecar, attempt_id: str
) -> dict[str, Any] | None:
    """Return the authenticated review receipt for the current attempt."""

    for entry in reversed(store.entries()):
        if entry.get("attempt_id") != attempt_id:
            break
        if entry.get("event") == sidecar.EVENT_REVIEW:
            return store.receipt(int(entry["sequence"]))
    return None


def _validate_reviewed_evidence(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    store: sidecar.SelfHealSidecar,
    attempt_dir: Path,
    candidate_manifest_sha256: str,
) -> list[str]:
    """Re-authenticate the exact review inputs and archived verdict at close."""

    expected, context_reasons = _load_review_context(
        ctx, policy, store, attempt_dir, candidate_manifest_sha256
    )
    if context_reasons or expected is None:
        return context_reasons or [REASON_REVIEW_CONTEXT_INVALID]

    receipt = _review_receipt(store, attempt_dir.name)
    if receipt is None:
        return [REASON_REVIEW_EVIDENCE_DRIFT]
    payload = receipt.get("payload")
    actor = receipt.get("actor")
    if not isinstance(payload, dict) or not isinstance(actor, dict):
        return [REASON_REVIEW_EVIDENCE_DRIFT]

    try:
        review_path = _attempt_evidence_path(
            attempt_dir,
            "independent_review.json",
            reason=REASON_REVIEW_EVIDENCE_DRIFT,
        )
        review_body = review_path.read_bytes()
        document = json.loads(review_body.decode("utf-8"))
        context_path = _attempt_evidence_path(
            attempt_dir,
            "review_context.json",
            reason=REASON_REVIEW_CONTEXT_INVALID,
        )
        context_sha256 = _required_sha256_file(context_path, "review context")
    except (SelfHealEvidenceError, OSError, UnicodeError, json.JSONDecodeError):
        return [REASON_REVIEW_EVIDENCE_DRIFT]

    reviewer_runtime = str(actor.get("runtime") or "")
    verdict, review_reasons = validate_review_document(
        document,
        expected=expected,
        expected_reviewer_runtime=reviewer_runtime,
    )
    if review_reasons:
        return review_reasons

    binding = expected.as_binding()
    handoff = store.latest_payload(sidecar.EVENT_HEALER_HANDOFF)
    triage = store.latest_payload(sidecar.EVENT_TRIAGE)
    falsification = payload.get("falsification")
    if (
        _sha256_bytes(review_body) != payload.get("review_document_sha256")
        or payload.get("review_binding") != binding
        or payload.get("review_context_sha256") != context_sha256
        or handoff.get("review_context_sha256") != context_sha256
        or payload.get("candidate_manifest_sha256") != candidate_manifest_sha256
        or payload.get("reviewer_verdict") != verdict
        or payload.get("reviewer_runtime") != reviewer_runtime
        or payload.get("outcome") != sidecar.STATE_VERIFIED
        or verdict != "approved"
        or not isinstance(falsification, dict)
        or falsification.get("sha256") != expected.falsification_sha256
        or payload.get("triage_log_sha256") != triage.get("original_log_sha256")
    ):
        return [REASON_REVIEW_EVIDENCE_DRIFT]
    return []


# --------------------------------------------------------------------------
# Apply / rollback / resume
# --------------------------------------------------------------------------


class ApplyJournalError(ValueError):
    """An interrupted apply cannot be safely or authentically restored."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def _safe_workspace_target(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    relative: Any,
    *,
    failure_reason: str = REASON_JOURNAL_INVALID,
) -> tuple[str, Path]:
    try:
        relative_text = _safe_relative_path(relative)
    except ValueError as exc:
        raise ApplyJournalError(failure_reason, str(exc)) from exc
    allowed, reason = write_allowed(policy, relative_text)
    if not allowed:
        raise ApplyJournalError(
            failure_reason,
            f"journal target {relative_text!r} is not authorized: {reason}",
        )
    try:
        return _safe_tree_target(
            ctx.root,
            relative_text,
            reason=failure_reason,
            label="journal target",
        )
    except SelfHealEvidenceError as exc:
        raise ApplyJournalError(exc.reason, str(exc)) from exc


def _decode_journal_image(entry: dict[str, Any], prefix: str) -> bytes | None:
    encoded = entry.get(f"{prefix}_base64")
    recorded_sha = entry.get(f"{prefix}_sha256")
    if encoded is None:
        if prefix == "after" or recorded_sha is not None:
            raise ApplyJournalError(
                REASON_JOURNAL_INVALID, f"journal {prefix} image is incomplete"
            )
        return None
    if not isinstance(encoded, str):
        raise ApplyJournalError(REASON_JOURNAL_INVALID, f"journal {prefix} image is invalid")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID, f"journal {prefix} image is not valid base64"
        ) from exc
    if _sha256_bytes(body) != recorded_sha:
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID, f"journal {prefix} image hash does not match"
        )
    return body


def _journal_mode(value: Any, label: str, *, nullable: bool) -> int | None:
    """Validate one permission-only file mode from an apply journal."""

    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > 0o777
    ):
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID,
            f"journal {label} must be an integer permission mode",
        )
    return value


def _safe_journal_directory(ctx: GovernanceContext, relative: Any) -> tuple[str, Path]:
    """Validate one journal-owned directory without following any link."""

    try:
        relative_text = _safe_relative_path(relative)
        root = ctx.root.resolve(strict=True)
        target = root.joinpath(*PurePosixPath(relative_text).parts)
        cursor = root
        for part in PurePosixPath(relative_text).parts:
            cursor = cursor / part
            try:
                mode = os.lstat(cursor).st_mode
            except FileNotFoundError:
                break
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ApplyJournalError(
                    REASON_JOURNAL_INVALID,
                    f"journal-created directory is unsafe: {relative_text}",
                )
        return relative_text, target
    except ApplyJournalError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID,
            f"journal-created directory cannot be inspected: {relative}: {exc}",
        ) from exc


def _validated_apply_journal(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    store: sidecar.SelfHealSidecar,
    journal_path: Path,
) -> tuple[
    dict[str, Any],
    list[tuple[dict[str, Any], Path, bytes | None, bytes, int | None, int | None]],
    list[tuple[str, Path]],
]:
    """Authenticate a journal against the verified candidate before any write."""

    try:
        attempt_dir = journal_path.parent
        _relative, journal_path = _safe_tree_target(
            attempt_dir,
            "apply_journal.json",
            reason=REASON_JOURNAL_INVALID,
            label="apply journal",
        )
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (SelfHealEvidenceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplyJournalError(REASON_JOURNAL_INVALID, f"unreadable apply journal: {exc}") from exc
    if (
        not isinstance(journal, dict)
        or journal.get("schema") != JOURNAL_SCHEMA
        or journal.get("attempt_id") != attempt_dir.name
        or journal.get("state") not in {"applying", "applied"}
        or not isinstance(journal.get("started_at"), str)
        or not journal.get("started_at")
        or not isinstance(journal.get("files"), list)
        or not journal.get("files")
    ):
        raise ApplyJournalError(REASON_JOURNAL_INVALID, "apply journal schema/state is invalid")
    if store.state() != sidecar.STATE_VERIFIED or store.attempt_id() != attempt_dir.name:
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID,
            "the attempt never reached independently verified apply state",
        )

    (
        changes,
        manifest_sha,
        candidate_errors,
        pre_apply_missing_directories,
    ) = _candidate_state(ctx, attempt_dir)
    reviewed_manifest = str(
        store.latest_payload(sidecar.EVENT_REVIEW).get("candidate_manifest_sha256") or ""
    )
    if (
        candidate_errors
        or not reviewed_manifest
        or manifest_sha != reviewed_manifest
        or journal.get("candidate_manifest_sha256") != reviewed_manifest
    ):
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID, "apply journal is not bound to the reviewed candidate"
        )
    by_path = {change.path: change for change in changes}
    mode_integrity = journal.get("mode_integrity")
    if mode_integrity not in {None, JOURNAL_MODE_INTEGRITY}:
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID,
            "apply journal mode_integrity marker is invalid",
        )
    validated: list[
        tuple[dict[str, Any], Path, bytes | None, bytes, int | None, int | None]
    ] = []
    seen: set[str] = set()
    for raw in journal["files"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("applied"), bool):
            raise ApplyJournalError(REASON_JOURNAL_INVALID, "apply journal entry is invalid")
        relative, target = _safe_workspace_target(ctx, policy, raw.get("path"))
        if relative in seen or relative not in by_path:
            raise ApplyJournalError(
                REASON_JOURNAL_INVALID, "apply journal file set differs from the candidate"
            )
        seen.add(relative)
        before = _decode_journal_image(raw, "before")
        after = _decode_journal_image(raw, "after")
        assert after is not None
        expected_before = (
            by_path[relative].before.encode("utf-8")
            if by_path[relative].before is not None
            else None
        )
        expected_after = (by_path[relative].after or "").encode("utf-8")
        if before != expected_before or after != expected_after:
            raise ApplyJournalError(
                REASON_JOURNAL_INVALID, "apply journal images differ from candidate evidence"
            )
        if mode_integrity == JOURNAL_MODE_INTEGRITY:
            if "before_mode" not in raw or "after_mode" not in raw:
                raise ApplyJournalError(
                    REASON_JOURNAL_INVALID,
                    "mode-integrity journal entry is missing file modes",
                )
            before_mode = _journal_mode(
                raw.get("before_mode"), "before_mode", nullable=before is None
            )
            after_mode = _journal_mode(
                raw.get("after_mode"), "after_mode", nullable=False
            )
            if (before is None) != (before_mode is None):
                raise ApplyJournalError(
                    REASON_JOURNAL_INVALID,
                    "journal before image and before_mode disagree",
                )
            expected_after_mode = (
                before_mode if before_mode is not None else NEW_FILE_MODE
            )
            if after_mode != expected_after_mode:
                raise ApplyJournalError(
                    REASON_JOURNAL_INVALID,
                    "journal after_mode does not preserve the existing mode or "
                    "the secure new-file mode",
                )
        else:
            if "before_mode" in raw or "after_mode" in raw:
                raise ApplyJournalError(
                    REASON_JOURNAL_INVALID,
                    "legacy apply journal has partial file-mode metadata",
                )
            before_mode = None
            after_mode = None
        validated.append((raw, target, before, after, before_mode, after_mode))
    if seen != set(by_path):
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID, "apply journal omits a reviewed candidate file"
        )
    created_directories: list[tuple[str, Path]] = []
    raw_directories = journal.get("created_directories")
    if mode_integrity == JOURNAL_MODE_INTEGRITY:
        if not isinstance(raw_directories, list) or any(
            not isinstance(item, str) for item in raw_directories
        ):
            raise ApplyJournalError(
                REASON_JOURNAL_INVALID,
                "mode-integrity journal is missing created_directories",
            )
        if raw_directories != sorted(set(raw_directories)):
            raise ApplyJournalError(
                REASON_JOURNAL_INVALID,
                "journal-created directory list is duplicated or non-canonical",
            )
        if raw_directories != pre_apply_missing_directories:
            raise ApplyJournalError(
                REASON_JOURNAL_INVALID,
                "journal-created directories differ from the reviewed pre-apply evidence",
            )
        for raw_directory in raw_directories:
            relative, directory = _safe_journal_directory(ctx, raw_directory)
            created_directories.append((relative, directory))
    elif raw_directories is not None:
        raise ApplyJournalError(
            REASON_JOURNAL_INVALID,
            "legacy apply journal has partial created-directory metadata",
        )
    return journal, validated, created_directories


def _regular_file_image(path: Path) -> tuple[bytes | None, int | None]:
    """Return exact regular-file bytes and permission mode, or two ``None`` values."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise ApplyJournalError(
            REASON_WORKSPACE_DRIFT, f"workspace target cannot be inspected: {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise ApplyJournalError(
            REASON_WORKSPACE_DRIFT, f"workspace target is no longer a regular file: {path}"
        )
    try:
        return path.read_bytes(), stat.S_IMODE(info.st_mode)
    except OSError as exc:
        raise ApplyJournalError(
            REASON_WORKSPACE_DRIFT, f"workspace target cannot be read: {path}: {exc}"
        ) from exc


def _missing_parent_directories(ctx: GovernanceContext, relative: str) -> list[str]:
    """Directories an apply must create for one already-validated target."""

    root = ctx.root.resolve(strict=True)
    parts = PurePosixPath(relative).parts[:-1]
    cursor = root
    missing = False
    result: list[str] = []
    for index, part in enumerate(parts):
        cursor = cursor / part
        if not missing:
            try:
                mode = os.lstat(cursor).st_mode
            except FileNotFoundError:
                missing = True
            except OSError as exc:
                raise ApplyJournalError(
                    REASON_WORKSPACE_DRIFT,
                    f"workspace parent cannot be inspected: {relative}: {exc}",
                ) from exc
            else:
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ApplyJournalError(
                        REASON_WORKSPACE_DRIFT,
                        f"workspace parent is unsafe: {relative}",
                    )
        if missing:
            result.append(PurePosixPath(*parts[: index + 1]).as_posix())
    return result


def apply_candidate(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    store: sidecar.SelfHealSidecar,
    attempt_dir: Path,
    changes: list[FileChange],
    pre_apply_missing_directories: list[str],
) -> dict[str, Any]:
    """Write the verified candidate back, journalling every step first.

    The journal records each target's exact prior bytes *before* the first
    write, so an interruption anywhere leaves a state that ``resume`` can
    restore byte for byte.  There is no window in which a half-applied workspace
    lacks an authenticated restore path.
    """

    reviewed_manifest = str(
        store.latest_payload(sidecar.EVENT_REVIEW).get("candidate_manifest_sha256") or ""
    )
    targets: list[tuple[Path, bytes | None, int | None, bytes, int]] = []
    live_missing_directories: set[str] = set()
    for change in changes:
        target = _safe_workspace_target(ctx, policy, change.path)[1]
        current, current_mode = _regular_file_image(target)
        expected_before = (
            change.before.encode("utf-8") if change.before is not None else None
        )
        if current != expected_before:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"workspace changed immediately before apply: {change.path}",
            )
        if current_mode is not None and current_mode & ~0o777:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                "workspace target has special permission bits that the apply journal "
                f"cannot preserve: {change.path}",
            )
        after = (change.after or "").encode("utf-8")
        after_mode = current_mode if current_mode is not None else NEW_FILE_MODE
        targets.append((target, current, current_mode, after, after_mode))
        live_missing_directories.update(_missing_parent_directories(ctx, change.path))
    if sorted(live_missing_directories) != pre_apply_missing_directories:
        raise ApplyJournalError(
            REASON_WORKSPACE_DRIFT,
            "workspace parent-directory existence changed since the reviewed baseline",
        )
    journal = {
        "schema": JOURNAL_SCHEMA,
        "mode_integrity": JOURNAL_MODE_INTEGRITY,
        "attempt_id": attempt_dir.name,
        "candidate_manifest_sha256": reviewed_manifest,
        "started_at": utc_now(),
        "state": "applying",
        "created_directories": list(pre_apply_missing_directories),
        "files": [
            {
                "path": change.path,
                "before_sha256": _sha256_bytes(target[1])
                if target[1] is not None else None,
                "before_base64": base64.b64encode(target[1]).decode("ascii")
                if target[1] is not None else None,
                "before_mode": target[2],
                "after_sha256": _sha256_bytes(target[3]),
                "after_base64": base64.b64encode(target[3]).decode("ascii"),
                "after_mode": target[4],
                "applied": False,
            }
            for change, target in zip(changes, targets, strict=True)
        ],
    }
    journal_path = _attempt_evidence_path(
        attempt_dir, "apply_journal.json", reason=REASON_JOURNAL_INVALID
    )

    def write_journal() -> None:
        _attempt_evidence_path(
            attempt_dir, "apply_journal.json", reason=REASON_JOURNAL_INVALID
        )
        _write_json(journal_path, journal)

    write_journal()
    for relative in sorted(
        journal["created_directories"],
        key=lambda value: len(PurePosixPath(value).parts),
    ):
        _relative, directory = _safe_journal_directory(ctx, relative)
        try:
            os.mkdir(directory, mode=0o700)
        except FileExistsError as exc:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"workspace parent appeared after the apply journal was written: {relative}",
            ) from exc
        except OSError as exc:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"workspace parent could not be created safely: {relative}: {exc}",
            ) from exc
    for entry, target_record in zip(journal["files"], targets, strict=True):
        # Re-resolve immediately before the write so a newly introduced symlink
        # cannot turn the engine into a confused deputy.
        _relative, target = _safe_workspace_target(ctx, policy, entry["path"])
        _atomic_bytes(
            target,
            base64.b64decode(str(entry["after_base64"])),
            replace=True,
            mode=int(entry["after_mode"]),
        )
        applied_body, applied_mode = _regular_file_image(target)
        if applied_body != target_record[3] or applied_mode != target_record[4]:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"applied candidate image could not be verified: {entry['path']}",
            )
        entry["applied"] = True
        write_journal()
    journal["state"] = "applied"
    journal["finished_at"] = utc_now()
    write_journal()
    return journal


def rollback_from_journal(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    store: sidecar.SelfHealSidecar,
    journal_path: Path,
) -> dict[str, Any]:
    """Restore exact prior bytes for every entry the journal marked applied."""

    journal, validated, created_directories = _validated_apply_journal(
        ctx, policy, store, journal_path
    )
    actions: list[tuple[dict[str, Any], Path, bytes | None, int | None, bool]] = []
    mode_integrity = journal.get("mode_integrity") == JOURNAL_MODE_INTEGRITY
    for entry, target, before, after, before_mode, after_mode in validated:
        current, current_mode = _regular_file_image(target)
        if not mode_integrity:
            # A legacy v1 journal can be safely closed only if it requires no
            # actual restore. Once an old writer replaced a target, its prior
            # mode is unknowable and must never be guessed.
            if current != before:
                raise ApplyJournalError(
                    REASON_JOURNAL_INVALID,
                    "legacy apply journal cannot authenticate the prior file mode "
                    f"for an applied target: {entry['path']}",
                )
            actions.append((entry, target, before, current_mode, False))
            continue
        if current == before and current_mode == before_mode:
            actions.append((entry, target, before, before_mode, False))
        elif current == after and current_mode == after_mode:
            actions.append((entry, target, before, before_mode, True))
        else:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"workspace bytes or mode drifted during interrupted apply: {entry['path']}",
            )

    if mode_integrity:
        removable_files = {
            target.resolve(strict=False)
            for _entry, target, before, _before_mode, needs_restore in actions
            if before is None and needs_restore
        }
        created_paths = {path.resolve(strict=False) for _relative, path in created_directories}
        for _relative, directory in created_directories:
            try:
                if not directory.exists():
                    continue
                for current, dirs, files in os.walk(directory, followlinks=False):
                    current_path = Path(current)
                    for name in [*dirs, *files]:
                        child = current_path / name
                        if child.is_symlink():
                            raise ApplyJournalError(
                                REASON_WORKSPACE_DRIFT,
                                f"third-party symlink appeared in a journal-created directory: {child}",
                            )
                        resolved = child.resolve(strict=False)
                        if child.is_dir():
                            if resolved not in created_paths:
                                raise ApplyJournalError(
                                    REASON_WORKSPACE_DRIFT,
                                    f"third-party directory appeared during interrupted apply: {child}",
                                )
                        elif resolved not in removable_files:
                            raise ApplyJournalError(
                                REASON_WORKSPACE_DRIFT,
                                f"third-party file appeared during interrupted apply: {child}",
                            )
            except ApplyJournalError:
                raise
            except OSError as exc:
                raise ApplyJournalError(
                    REASON_WORKSPACE_DRIFT,
                    f"journal-created directory cannot be inspected: {directory}: {exc}",
                ) from exc

    # The scan above covers every target first: one divergent path means no
    # other path is restored, preserving all third-party edits atomically at the
    # decision boundary.
    for entry, target, before, before_mode, needs_restore in actions:
        if needs_restore:
            if before is None:
                target.unlink()
            else:
                assert before_mode is not None
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_bytes(target, before, replace=True, mode=before_mode)
        entry["applied"] = False
    for entry, target, before, before_mode, _needs_restore in actions:
        restored, restored_mode = _regular_file_image(target)
        if restored != before or (mode_integrity and restored_mode != before_mode):
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"restored workspace image could not be verified: {entry['path']}",
            )
    for _relative, directory in sorted(
        created_directories,
        key=lambda item: len(PurePosixPath(item[0]).parts),
        reverse=True,
    ):
        try:
            if directory.exists():
                directory.rmdir()
        except OSError as exc:
            raise ApplyJournalError(
                REASON_WORKSPACE_DRIFT,
                f"journal-created directory could not be removed exactly: {directory}: {exc}",
            ) from exc
    journal["state"] = "rolled_back"
    journal["finished_at"] = utc_now()
    _attempt_evidence_path(
        journal_path.parent, "apply_journal.json", reason=REASON_JOURNAL_INVALID
    )
    _write_json(journal_path, journal)
    return journal


def workspace_drifted(
    ctx: GovernanceContext, policy: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    """Paths or parent directories that differ from the captured baseline."""

    entries = baseline.get("files")
    recorded_missing = baseline.get("pre_apply_missing_directories")
    if (
        not isinstance(entries, list)
        or not isinstance(recorded_missing, list)
        or any(not isinstance(item, str) for item in recorded_missing)
        or recorded_missing != sorted(set(recorded_missing))
    ):
        raise ApplyJournalError(
            REASON_EVIDENCE_INVALID,
            "candidate baseline files or pre-apply directories field is invalid",
        )
    drifted: list[str] = []
    live_missing: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or "path" not in entry:
            raise ApplyJournalError(
                REASON_EVIDENCE_INVALID, "candidate baseline file entry is invalid"
            )
        relative, target = _safe_workspace_target(
            ctx, policy, entry["path"], failure_reason=REASON_EVIDENCE_INVALID
        )
        current_body, _current_mode = _regular_file_image(target)
        current = _sha256_bytes(current_body) if current_body is not None else None
        if current != entry.get("before_sha256"):
            drifted.append(relative)
        live_missing.update(_missing_parent_directories(ctx, relative))
    if sorted(live_missing) != recorded_missing:
        drifted.extend(sorted(set(live_missing).symmetric_difference(recorded_missing)))
    return drifted


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


def _status_for_state(chain_state: str, review_outcome: str = "") -> str:
    mapping = {
        sidecar.STATE_NONE: STATUS_TRIAGED,
        sidecar.STATE_TRIAGE_RECORDED: STATUS_TRIAGED,
        sidecar.STATE_AWAITING_HEALER: STATUS_HEALING_ACTIVE,
        sidecar.STATE_HEALING_ACTIVE: STATUS_HEALING_ACTIVE,
        sidecar.STATE_AWAITING_REVIEW: STATUS_HEALING_ACTIVE,
        sidecar.STATE_VERIFIED: STATUS_VERIFIED,
        sidecar.STATE_REJECTED: STATUS_REJECTED,
        sidecar.STATE_INVALIDATED: STATUS_INVALIDATED,
    }
    if chain_state == sidecar.STATE_CLOSED:
        return STATUS_VERIFIED if review_outcome == sidecar.STATE_VERIFIED else STATUS_REJECTED
    return mapping.get(chain_state, STATUS_BLOCKED)


def _rel(ctx: GovernanceContext, path: Path) -> str:
    """Workspace-relative path for reporting; an outside path reports as-is.

    ``artifact_paths`` is a report field, not a permission decision, so a path
    the caller supplied from outside the workspace must not turn into an
    exception that masks the real result.
    """

    try:
        return workspace_rel(path, ctx.root)
    except (RoleGovernanceError, ValueError):
        return path.as_posix()


def _triage_attempt_identity(
    store: sidecar.SelfHealSidecar, base_attempt_id: str
) -> tuple[str, int]:
    """Return a unique, retry-stable identity for the next real attempt.

    ``failure_triage`` deliberately fingerprints the failure, so its base value
    repeats for the same UC/log/command/exit code.  The sidecar deliberately
    forbids reusing an identity from a prior attempt.  Bind later attempts to
    the authenticated history count and head: a completed attempt gets a new
    identity, while an unindexed #0 receipt that is quarantined after a crash
    leaves both inputs unchanged and therefore retries with the same identity.
    """

    chain = store.load()
    entries = chain.get("entries")
    if not isinstance(entries, list):
        raise SelfHealEvidenceError(
            REASON_EVIDENCE_INVALID, "self-heal chain entries are invalid"
        )
    attempt_count = sum(
        entry.get("event") == sidecar.EVENT_TRIAGE
        for entry in entries
        if isinstance(entry, dict)
    )
    if attempt_count == 0:
        return base_attempt_id, 0
    seed = "\0".join(
        [base_attempt_id, str(attempt_count), str(chain.get("head_sha256") or "")]
    )
    return "att-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24], attempt_count


def _blocked(reasons: list[str], action: str, paths: list[str] | None = None) -> dict[str, Any]:
    return result(STATUS_BLOCKED, blocking_reasons=reasons, artifact_paths=paths, next_action=action)


def step_triage(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    from self_healing_mvp import classify_with_triage, render_md, repair_plan_text

    log_path = Path(args.pytest_log) if args.pytest_log else None
    if log_path is not None and not log_path.is_absolute():
        log_path = ctx.root / log_path
    if log_path is None or not log_path.exists():
        return _blocked(
            [REASON_LOG_MISSING],
            "supply --pytest-log pointing at the runner log for this UC",
        )
    try:
        log = read_text(log_path)
    except (OSError, UnicodeError) as exc:
        return _blocked(
            [REASON_EVIDENCE_INVALID], f"runner log cannot be read: {log_path}: {exc}"
        )
    payload = classify_with_triage(
        log,
        args.exit_code,
        artifact_dir=ctx.artifact_dir,
        root=ctx.root,
        uc=ctx.uc,
        command=args.command,
        log_path=log_path,
        mode=policy["mode"],
    )
    attempt_count = 0
    if payload["healing_eligible"] and policy["mode"] != "diagnose":
        store = store or sidecar.SelfHealSidecar(ctx)
        attempt_id, attempt_count = _triage_attempt_identity(
            store, str(payload["attempt_id"])
        )
        payload["attempt_id"] = attempt_id
        if attempt_count >= policy["max_attempts"]:
            return _blocked(
                [REASON_MAX_ATTEMPTS],
                "open a defect record instead of another self-heal attempt",
            )
        # A rejected transition must not rewrite ordinary attribution reports
        # before the sidecar state machine says that this attempt may open.
        # The whole-step lock serializes peers, while this read-only preflight
        # makes the losing sequential/concurrent triage exactly zero-write.
        store.preflight_publish(
            sidecar.EVENT_TRIAGE,
            attempt_id,
            payload={"healing_eligible": True},
        )
    # These three are ordinary governed post-run artifacts, so they go through
    # the same writer (and therefore the same governed-write preflight) that
    # --scope post-run uses.  ``dump_json`` also preserves insertion order, which
    # is what keeps the seven legacy keys at the front of the serialized file.
    json_path = ctx.artifact_dir / "self_healing.json"
    dump_json(json_path, payload)
    write_text(ctx.artifact_dir / "self_healing.md", render_md(payload))
    write_text(ctx.artifact_dir / "self_healing_repair_plan.md", repair_plan_text(payload))

    paths = [
        _rel(ctx, json_path),
        _rel(ctx, ctx.artifact_dir / "self_healing.md"),
        _rel(ctx, ctx.artifact_dir / "self_healing_repair_plan.md"),
    ]
    if not payload["healing_eligible"]:
        return _blocked(
            list(payload["blocking_reasons"]) or [REASON_NOT_ELIGIBLE],
            payload["recommended_action"],
            paths,
        )
    if policy["mode"] == "diagnose":
        return result(
            STATUS_TRIAGED,
            artifact_paths=paths,
            next_action=(
                "diagnose mode records the attribution only; raise self_healing.mode "
                "to verify to allow a repair candidate"
            ),
        )
    assert store is not None
    attempt_id = str(payload["attempt_id"])
    store.publish(
        sidecar.EVENT_TRIAGE,
        attempt_id=attempt_id,
        payload={
            "failure_owner": payload["failure_owner"],
            "failure_subtype": payload["failure_subtype"],
            "confidence": payload["confidence"],
            "target_layer_reached": payload["target_layer_reached"],
            "healing_eligible": payload["healing_eligible"],
            "blocking_reasons": payload["blocking_reasons"],
            "original_command": payload["original_command"],
            "original_exit_code": payload["original_exit_code"],
            "original_log_sha256": payload["original_log_sha256"],
            "evidence_refs": payload["evidence_refs"],
            "oracle_refs": payload["oracle_refs"],
        },
    )
    paths.append(_rel(ctx, sidecar.sidecar_dir(ctx)))
    return result(
        STATUS_TRIAGED,
        artifact_paths=paths,
        next_action="hand off to a healer with --self-heal-step handoff",
    )


def step_handoff(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    if policy["mode"] not in CANDIDATE_MODES:
        return _blocked(
            [REASON_CANDIDATE_MODE],
            f"self_healing.mode {policy['mode']!r} cannot produce a repair candidate",
        )
    store = store or sidecar.SelfHealSidecar(ctx)
    triage_payload = store.latest_payload(sidecar.EVENT_TRIAGE)
    if not triage_payload.get("healing_eligible"):
        return _blocked(
            list(triage_payload.get("blocking_reasons") or [REASON_NOT_ELIGIBLE]),
            "this failure is not a test-asset defect; do not hand it to a healer",
        )
    if store.attempt_count() > policy["max_attempts"]:
        return _blocked([REASON_MAX_ATTEMPTS], "open a defect record instead of another attempt")
    store.publish(
        sidecar.EVENT_HANDOFF,
        attempt_id=store.attempt_id(),
        payload={"healing_eligible": True, "mode": policy["mode"]},
    )
    return result(
        STATUS_HEALING_ACTIVE,
        artifact_paths=[_rel(ctx, sidecar.sidecar_dir(ctx))],
        next_action="a fresh implementer session must run --self-heal-step accept",
    )


def step_accept(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    store = store or sidecar.SelfHealSidecar(ctx)
    store.publish(
        sidecar.EVENT_HEALER_ACCEPT,
        attempt_id=store.attempt_id(),
        payload={"accepted_at": utc_now()},
    )
    return result(
        STATUS_HEALING_ACTIVE,
        artifact_paths=[_rel(ctx, sidecar.sidecar_dir(ctx))],
        next_action="propose a sandbox-verified candidate with --self-heal-step propose",
    )


def step_propose(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    store = store or sidecar.SelfHealSidecar(ctx)
    attempt_id = store.attempt_id()
    attempt_dir = sidecar.attempts_dir(ctx, attempt_id)
    if policy["mode"] not in CANDIDATE_MODES:
        return _blocked(
            [REASON_CANDIDATE_MODE],
            f"self_healing.mode {policy['mode']!r} cannot produce a repair candidate",
        )
    if not args.candidate_dir:
        return _blocked([REASON_CANDIDATE_MISSING], "supply --candidate-dir with the proposed files")
    candidate_dir = Path(args.candidate_dir)
    if not candidate_dir.is_absolute():
        candidate_dir = ctx.root / candidate_dir
    if not candidate_dir.is_dir():
        return _blocked([REASON_CANDIDATE_MISSING], f"candidate directory not found: {candidate_dir}")
    try:
        proposed = collect_candidate(candidate_dir)
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc))
    if not proposed:
        return _blocked([REASON_CANDIDATE_MISSING], "the candidate directory is empty")

    denied = []
    for relative, _content in proposed:
        allowed, reason = write_allowed(policy, relative)
        if not allowed:
            denied.append(f"{reason}:{relative}")
    if denied:
        return _blocked(
            sorted({item.split(":", 1)[0] for item in denied}),
            "the profile does not authorize writing these paths: " + ", ".join(sorted(denied)),
        )
    if not policy["verification_commands"]:
        return _blocked(
            [REASON_VERIFY_COMMANDS],
            "self_healing.verification_commands must name how this repair is proven",
        )

    # Decide the state-machine edge before creating or replacing any attempt
    # evidence.  ``publish`` repeats this validation at the final commit point;
    # this read-only preflight prevents a proposal made in the wrong state from
    # rewriting the candidate that an earlier review already approved.
    store.preflight_publish(sidecar.EVENT_HEALER_HANDOFF, attempt_id)

    try:
        _reject_workspace_symlinks(ctx.root)
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc))
    try:
        changes = build_changes(proposed, ctx, policy)
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc))
    triage_payload = store.latest_payload(sidecar.EVENT_TRIAGE)
    failure_log = _failure_log(ctx, store, args.pytest_log)
    if not failure_log:
        return _blocked(
            [REASON_EVIDENCE_INVALID],
            "the supplied or recorded failure log does not match the triage evidence hash",
        )
    findings = structural_review(changes, failure_log=failure_log)
    if findings:
        # A structurally rejected proposal is not evidence for this attempt.
        # Keep the previously recorded baseline/candidate/review byte-identical
        # so a rejected operation cannot replace what an independent reviewer
        # inspected.
        return result(
            STATUS_REJECTED,
            blocking_reasons=[REASON_STRUCTURAL]
            + sorted({finding.code for finding in findings}),
            next_action="the candidate weakens the test; keep the failure and revise",
        )

    # Verification, falsification, and the reverse negative-control gate are a
    # write-free decision phase.  In particular, a fake-green candidate is not
    # attempt evidence and must leave the complete sidecar byte-identical.
    try:
        verification = run_verification(ctx, policy, changes)
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc))
    if not verification["before_reproduced_failure"]:
        try:
            _candidate_manifest_sha256, paths = _persist_proposal_material(
                ctx,
                attempt_dir,
                attempt_id,
                changes,
                failure_log,
                findings,
                verification,
            )
        except SelfHealEvidenceError as exc:
            return _blocked([exc.reason], str(exc))
        return _blocked(
            [REASON_NOT_REPRODUCIBLE],
            "the failure does not reproduce in a clean sandbox; do not repair what is not proven broken",
            paths,
        )
    if not verification["after_passed"]:
        try:
            _candidate_manifest_sha256, paths = _persist_proposal_material(
                ctx,
                attempt_dir,
                attempt_id,
                changes,
                failure_log,
                findings,
                verification,
            )
        except SelfHealEvidenceError as exc:
            return _blocked([exc.reason], str(exc))
        return _blocked(
            [REASON_VERIFICATION_FAILED],
            "the candidate does not make the verification commands pass; the real "
            "workspace was not written",
            paths,
        )

    (
        falsification,
        falsification_reasons,
        falsification_markdown,
        falsification_log,
    ) = run_falsification(ctx, policy)
    if falsification_reasons:
        try:
            _candidate_manifest_sha256, paths = _persist_proposal_material(
                ctx,
                attempt_dir,
                attempt_id,
                changes,
                failure_log,
                findings,
                verification,
                falsification=falsification,
                falsification_markdown=falsification_markdown,
                falsification_log=falsification_log,
            )
        except SelfHealEvidenceError as exc:
            return _blocked([exc.reason], str(exc))
        return _blocked(
            falsification_reasons,
            "bind self_healing.falsification_spec to a real oracle/mutation spec",
            paths,
        )
    if falsification_inconclusive(falsification):
        try:
            _candidate_manifest_sha256, paths = _persist_proposal_material(
                ctx,
                attempt_dir,
                attempt_id,
                changes,
                failure_log,
                findings,
                verification,
                falsification=falsification,
                falsification_markdown=falsification_markdown,
                falsification_log=falsification_log,
            )
        except SelfHealEvidenceError as exc:
            return _blocked([exc.reason], str(exc))
        return _blocked(
            [REASON_FALSIFICATION_INCONCLUSIVE],
            "the falsification run produced no scored mutation result; repair the oracle/evidence binding",
            paths,
        )
    if falsification_rejected(falsification):
        try:
            _candidate_manifest_sha256, paths = _persist_proposal_material(
                ctx,
                attempt_dir,
                attempt_id,
                changes,
                failure_log,
                findings,
                verification,
                falsification=falsification,
                falsification_markdown=falsification_markdown,
                falsification_log=falsification_log,
            )
        except SelfHealEvidenceError as exc:
            return _blocked([exc.reason], str(exc))
        return result(
            STATUS_REJECTED,
            blocking_reasons=[REASON_FALSIFICATION_REJECTED],
            artifact_paths=paths,
            next_action="the oracle falsification score is below the required threshold; keep the original failure",
        )

    oracle_refs = sorted(
        set(str(ref) for ref in (triage_payload.get("oracle_refs") or []))
    )
    repaired_test_mutation, mutation_reason = run_repaired_test_mutation_gate(
        ctx, policy, changes, falsification, oracle_refs, failure_log
    )
    # Keep the dynamic proof inside the already-frozen falsification artifact;
    # adding a new attempt leaf here would itself expand the frozen section 2.1
    # path surface.  The independent-review binding hashes this final object.
    falsification["repaired_test_mutation"] = repaired_test_mutation
    if mutation_reason == REASON_REPAIRED_TEST_SURVIVES_MUTATION:
        return result(
            STATUS_REJECTED,
            blocking_reasons=[mutation_reason],
            artifact_paths=[],
            next_action=(
                "the repaired test stayed green after a declared oracle violation; "
                "keep the original failure and restore a real observation binding"
            ),
        )
    if mutation_reason:
        return _blocked(
            [mutation_reason],
            "the candidate cannot be reverse-verified against a safe, runner-visible oracle mutation",
        )

    try:
        candidate_manifest_sha256, paths = _persist_proposal_material(
            ctx,
            attempt_dir,
            attempt_id,
            changes,
            failure_log,
            findings,
            verification,
            falsification=falsification,
            falsification_markdown=falsification_markdown,
            falsification_log=falsification_log,
        )
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc))
    try:
        precode_evidence, precode_evidence_sha256 = _accepted_precode_evidence(
            ctx, triage_payload
        )
        expectation = _review_expectation(
            ctx,
            policy,
            attempt_dir,
            candidate_manifest_sha256,
            precode_evidence_sha256,
            oracle_refs,
            str(triage_payload.get("original_log_sha256") or ""),
        )
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc), paths)
    review_context_path = attempt_dir / "review_context.json"
    review_context = {
        "schema": "bugate.self-heal-review-context/v1",
        "binding": expectation.as_binding(),
        "evidence_paths": [
            *(item["path"] for item in precode_evidence),
            "candidate.patch",
            "original_failure.log",
            "verification.json",
            "before.log",
            "after.log",
            "falsification.json",
        ],
    }
    _write_attempt_json(attempt_dir, "review_context.json", review_context)
    review_context_sha256 = _required_sha256_file(
        review_context_path, "review context"
    )
    paths.append(_rel(ctx, review_context_path))

    store.publish(
        sidecar.EVENT_HEALER_HANDOFF,
        attempt_id=attempt_id,
        payload={
            "candidate_schema": CANDIDATE_SCHEMA,
            "files": [change.path for change in changes],
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "review_context_sha256": review_context_sha256,
            "review_binding": expectation.as_binding(),
            "structural_findings": [],
            "verification": {
                "before_reproduced_failure": True,
                "after_passed": True,
                "commands": policy["verification_commands"],
            },
            "triage_log_sha256": triage_payload.get("original_log_sha256", ""),
        },
    )
    return result(
        STATUS_HEALING_ACTIVE,
        artifact_paths=paths,
        next_action="a fresh reviewer session must run --self-heal-step review",
    )


def step_review(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    store = store or sidecar.SelfHealSidecar(ctx)
    attempt_id = store.attempt_id()
    attempt_dir = sidecar.attempts_dir(ctx, attempt_id)
    # Validate the transition before archiving any reviewer-supplied byte.  The
    # final publish repeats this check at its commit boundary.
    store.preflight_publish(sidecar.EVENT_REVIEW, attempt_id)
    if not args.review_file:
        return _blocked(
            [REASON_REVIEW_MISSING],
            "supply --review-file with the independent reviewer's verdict document",
        )
    review_path = Path(args.review_file)
    if not review_path.is_absolute():
        review_path = ctx.root / review_path
    if not review_path.exists():
        return _blocked([REASON_REVIEW_MISSING], f"review document not found: {review_path}")
    # The structural pass runs again here, independently of whatever the healer
    # reported.  A reviewer's "approved" can never overrule it.
    baseline_path = attempt_dir / "baseline.json"
    (
        changes,
        candidate_manifest_sha256,
        candidate_errors,
        _pre_apply_missing_directories,
    ) = _candidate_state(ctx, attempt_dir)
    triage_payload = store.latest_payload(sidecar.EVENT_TRIAGE)
    failure_log = _failure_log(ctx, store, "", attempt_dir=attempt_dir)
    findings = structural_review(changes, failure_log=failure_log) if changes else []
    paths = [_rel(ctx, attempt_dir), _rel(ctx, review_path)]
    if baseline_path.exists():
        paths.append(_rel(ctx, baseline_path))

    if candidate_errors:
        return _blocked(
            [REASON_CANDIDATE_DIVERGED_PROPOSAL],
            "the candidate bytes differ from the proposal manifest",
            paths,
        )
    expected, context_reasons = _load_review_context(
        ctx, policy, store, attempt_dir, candidate_manifest_sha256
    )
    if context_reasons or expected is None:
        return _blocked(
            context_reasons or [REASON_REVIEW_CONTEXT_INVALID],
            "the immutable proposal review context is missing or differs from live evidence",
            paths,
        )
    try:
        review_body = review_path.read_bytes()
        document = json.loads(review_body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _blocked(
            [REASON_EVIDENCE_INVALID], f"unreadable review document: {exc}", paths
        )

    reviewer_runtime = os.environ.get("BUGATE_AGENT_RUNTIME", "").strip().lower()
    if reviewer_runtime not in {"codex", "claude"}:
        reviewer_runtime = "unknown"
    verdict, review_reasons = validate_review_document(
        document,
        expected=expected,
        expected_reviewer_runtime=reviewer_runtime,
    )
    if findings:
        outcome = sidecar.STATE_REJECTED
        reasons = [REASON_STRUCTURAL] + sorted({finding.code for finding in findings})
    elif review_reasons:
        return _blocked(review_reasons, "re-run the independent review with a real, fresh reviewer session", paths)
    elif verdict != "approved":
        outcome = sidecar.STATE_REJECTED
        reasons = [f"independent_review_{verdict}"]
    else:
        outcome = sidecar.STATE_VERIFIED
        reasons = []

    archived_review = _attempt_evidence_path(
        attempt_dir, "independent_review.json", reason=REASON_REVIEW_CONTEXT_INVALID
    )
    archive_preexisted = archived_review.exists()
    _atomic_bytes(archived_review, review_body, replace=False)
    paths.append(_rel(ctx, archived_review))
    try:
        store.publish(
            sidecar.EVENT_REVIEW,
            attempt_id=attempt_id,
            payload={
                "outcome": outcome,
                "reviewer_verdict": verdict,
                "reviewer_runtime": str(document.get("runtime") or ""),
                "structural_findings": [finding.as_dict() for finding in findings],
                "candidate_manifest_sha256": candidate_manifest_sha256,
                "candidate_integrity_errors": candidate_errors,
                "review_document_sha256": _sha256_bytes(review_body),
                "review_binding": expected.as_binding(),
                "review_context_sha256": str(
                    store.latest_payload(sidecar.EVENT_HEALER_HANDOFF).get(
                        "review_context_sha256"
                    )
                    or ""
                ),
                "falsification": {
                    "sha256": expected.falsification_sha256,
                },
                "triage_log_sha256": triage_payload.get("original_log_sha256", ""),
            },
            resulting_state=outcome,
        )
    except (sidecar.SelfHealSidecarError, RoleGovernanceError):
        if not archive_preexisted:
            try:
                if archived_review.read_bytes() == review_body:
                    archived_review.unlink()
            except OSError:
                pass
        raise
    if outcome == sidecar.STATE_REJECTED:
        return result(
            STATUS_REJECTED,
            blocking_reasons=reasons,
            artifact_paths=paths,
            next_action="close the attempt; the original failure stands and must be reported",
        )
    return result(
        STATUS_VERIFIED,
        artifact_paths=paths,
        next_action="close the attempt with --self-heal-step close",
    )


def _changes_from_attempt(ctx: GovernanceContext, attempt_dir: Path) -> list[FileChange]:
    """Reload the exact candidate the healer proposed, for independent re-checking.

    The proposed content is stored verbatim under ``attempts/<id>/candidate/``
    rather than reconstructed from ``candidate.patch``.  Rebuilding a diff is
    lossy and depends on the workspace still holding the pre-repair bytes, which
    stops being true the moment an apply succeeds -- and a review control that
    silently degrades to "nothing to check" is worse than no control.
    ``candidate.patch`` remains the human-readable artifact, but is also
    reconstructed and byte-compared as part of the authenticated manifest.
    """

    changes, _manifest_sha256, errors, _pre_apply_missing_directories = (
        _candidate_state(ctx, attempt_dir)
    )
    return [] if errors else changes


def _failure_log(
    ctx: GovernanceContext,
    store: sidecar.SelfHealSidecar,
    supplied: str,
    *,
    attempt_dir: Path | None = None,
) -> str:
    """The original failing runner log, resolved without trusting the caller.

    The independent review must analyse the *same* failure the triage recorded,
    otherwise a reviewer could be handed a different log and the
    "expected rewritten to the observed actual" check would silently have
    nothing to compare against.  Resolution order: the copy captured beside the
    candidate, then the path the triage receipt recorded as evidence, then an
    explicitly supplied path.
    """

    triage_sha = str(
        store.latest_payload(sidecar.EVENT_TRIAGE).get("original_log_sha256") or ""
    )
    if attempt_dir is not None:
        captured = attempt_dir / "original_failure.log"
        try:
            if (
                captured.is_file()
                and not captured.is_symlink()
                and _sha256_file(captured) == triage_sha
            ):
                return read_text(captured)
        except (OSError, UnicodeError):
            return ""
    for reference in store.latest_payload(sidecar.EVENT_TRIAGE).get("evidence_refs") or []:
        if not isinstance(reference, dict):
            continue
        candidate = ctx.root / str(reference.get("path") or "")
        try:
            if (
                candidate.is_file()
                and not candidate.is_symlink()
                and _sha256_file(candidate) == reference.get("sha256") == triage_sha
            ):
                return read_text(candidate)
        except (OSError, UnicodeError):
            continue
    if supplied:
        path = Path(supplied)
        if not path.is_absolute():
            path = ctx.root / path
        try:
            if (
                path.is_file()
                and not path.is_symlink()
                and _sha256_file(path) == triage_sha
            ):
                return read_text(path)
        except (OSError, UnicodeError):
            return ""
    return ""


def _candidate_image_inventory(
    attempt_dir: Path, tree_name: str
) -> tuple[dict[str, Path], list[str]]:
    """Validate one engine-owned candidate image tree before replacement."""

    _relative, root = _safe_tree_target(
        attempt_dir,
        tree_name,
        reason=REASON_EVIDENCE_INVALID,
        label=f"stored {tree_name} image",
        leaf_kind="directory",
    )
    if not root.exists():
        return {}, []
    files: dict[str, Path] = {}
    directories: list[str] = []
    try:
        for current, child_dirs, child_files in os.walk(
            root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for name in sorted([*child_dirs, *child_files]):
                item = current_path / name
                mode = os.lstat(item).st_mode
                relative = item.relative_to(root).as_posix()
                if stat.S_ISLNK(mode) or not (
                    stat.S_ISDIR(mode) or stat.S_ISREG(mode)
                ):
                    raise SelfHealEvidenceError(
                        REASON_EVIDENCE_INVALID,
                        f"stored {tree_name} image contains an unsafe entry: {relative}",
                    )
                if stat.S_ISDIR(mode):
                    directories.append(relative)
                else:
                    files[relative] = item
            child_dirs[:] = sorted(child_dirs)
    except SelfHealEvidenceError:
        raise
    except OSError as exc:
        raise SelfHealEvidenceError(
            REASON_EVIDENCE_INVALID,
            f"stored {tree_name} image cannot be inspected safely: {exc}",
        ) from exc
    return files, sorted(
        directories, key=lambda value: len(PurePosixPath(value).parts), reverse=True
    )


def _store_candidate(attempt_dir: Path, changes: list[FileChange]) -> None:
    """Replace both engine-owned candidate images with the exact current set."""

    candidate_files, candidate_dirs = _candidate_image_inventory(
        attempt_dir, "candidate"
    )
    before_files, before_dirs = _candidate_image_inventory(attempt_dir, "before")
    desired_candidate = {change.path for change in changes}
    desired_before = {change.path for change in changes if change.before is not None}

    # Validate both trees completely before deleting any stale engine-owned
    # entry.  A structurally rejected or wrong-state proposal never reaches this
    # helper; a valid retry is allowed to replace evidence from its own earlier,
    # non-published proposal.
    for tree_name, inventory, desired in (
        ("candidate", candidate_files, desired_candidate),
        ("before", before_files, desired_before),
    ):
        for relative in sorted(set(inventory) - desired):
            path = _attempt_evidence_path(
                attempt_dir,
                f"{tree_name}/{relative}",
                reason=REASON_EVIDENCE_INVALID,
            )
            try:
                path.unlink()
            except OSError as exc:
                raise SelfHealEvidenceError(
                    REASON_EVIDENCE_INVALID,
                    f"stale {tree_name} image cannot be removed safely: {relative}: {exc}",
                ) from exc

    for tree_name, directories in (
        ("candidate", candidate_dirs),
        ("before", before_dirs),
    ):
        for relative in directories:
            _relative, directory = _safe_tree_target(
                attempt_dir,
                f"{tree_name}/{relative}",
                reason=REASON_EVIDENCE_INVALID,
                label=f"stored {tree_name} image directory",
                leaf_kind="directory",
            )
            try:
                directory.rmdir()
            except OSError as exc:
                # Non-empty directories are retained for the desired images;
                # every stale regular file was already removed above.
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                    raise SelfHealEvidenceError(
                        REASON_EVIDENCE_INVALID,
                        f"stored {tree_name} image directory cannot be pruned: "
                        f"{relative}: {exc}",
                    ) from exc

    for change in changes:
        _write_attempt_text(
            attempt_dir, f"candidate/{change.path}", change.after or ""
        )
        if change.before is not None:
            _write_attempt_text(
                attempt_dir, f"before/{change.path}", change.before
            )


def step_close(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    store = store or sidecar.SelfHealSidecar(ctx)
    attempt_id = store.attempt_id()
    attempt_dir = sidecar.attempts_dir(ctx, attempt_id)
    state = store.state()
    paths = [_rel(ctx, sidecar.sidecar_dir(ctx))]
    applied = False
    approval = str(args.human_approval or "").strip()

    # Closing can apply workspace bytes, so reject an invalid actor/session or
    # transition before any write.  ``publish`` repeats the same validation at
    # the final commit boundary.
    store.preflight_publish(sidecar.EVENT_CLOSED, attempt_id)

    if state == sidecar.STATE_VERIFIED:
        baseline_path = attempt_dir / "baseline.json"
        if not baseline_path.exists():
            return _blocked([REASON_CANDIDATE_MISSING], "no recorded candidate to apply", paths)
        (
            changes,
            candidate_manifest_sha256,
            candidate_errors,
            pre_apply_missing_directories,
        ) = _candidate_state(ctx, attempt_dir)
        reviewed_manifest = str(
            store.latest_payload(sidecar.EVENT_REVIEW).get("candidate_manifest_sha256") or ""
        )
        if candidate_errors:
            evidence_errors = {
                "candidate_baseline_unreadable",
                "candidate_baseline_invalid",
                "candidate_evidence_unsafe",
            }
            reason = (
                REASON_EVIDENCE_INVALID
                if evidence_errors.intersection(candidate_errors)
                else REASON_CANDIDATE_DIVERGED_REVIEW
            )
            return _blocked(
                [reason],
                "the stored candidate evidence is malformed or differs from the independent review",
                paths,
            )
        if not reviewed_manifest or candidate_manifest_sha256 != reviewed_manifest:
            return _blocked(
                [REASON_CANDIDATE_DIVERGED_REVIEW],
                "the candidate bytes differ from the manifest approved by the independent review",
                paths,
            )
        review_reasons = _validate_reviewed_evidence(
            ctx, policy, store, attempt_dir, candidate_manifest_sha256
        )
        if review_reasons:
            return _blocked(
                review_reasons,
                "the reviewed evidence changed after the independent verdict; start a fresh review",
                paths,
            )
        # Verify mode closes only after re-authenticating the exact candidate,
        # reconstructed patch, context, archived verdict, and every live review
        # input above.  Only the real-workspace apply branch is mode-specific.
        if policy["mode"] not in APPLY_MODES:
            changes = []
        elif not approval:
            return _blocked(
                [REASON_HUMAN_APPROVAL],
                "apply_with_approval requires --human-approval naming the approver",
                paths,
            )

    if state == sidecar.STATE_VERIFIED and policy["mode"] in APPLY_MODES:
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _blocked(
                [REASON_EVIDENCE_INVALID],
                f"candidate baseline cannot be read: {exc}",
                paths,
            )
        if not isinstance(baseline, dict):
            return _blocked(
                [REASON_EVIDENCE_INVALID],
                "candidate baseline schema is invalid",
                paths,
            )
        try:
            drifted = workspace_drifted(ctx, policy, baseline)
        except ApplyJournalError as exc:
            return _blocked([exc.reason], str(exc), paths)
        if drifted:
            return _blocked(
                [REASON_WORKSPACE_DRIFT],
                "the workspace changed since the candidate was verified; these paths "
                "would be overwritten: " + ", ".join(sorted(drifted)),
                paths,
            )
        try:
            journal = apply_candidate(
                ctx,
                policy,
                store,
                attempt_dir,
                changes,
                pre_apply_missing_directories,
            )
        except ApplyJournalError as exc:
            return _blocked([exc.reason], str(exc), paths)
        paths.append(_rel(ctx, attempt_dir / "apply_journal.json"))
        try:
            verification = run_verification(ctx, policy, [])
        except SelfHealEvidenceError as exc:
            try:
                rollback_from_journal(
                    ctx, policy, store, attempt_dir / "apply_journal.json"
                )
            except ApplyJournalError as rollback_exc:
                return _blocked(
                    [rollback_exc.reason],
                    "post-apply verification could not execute and automatic restore stopped: "
                    + str(rollback_exc),
                    paths,
                )
            return _blocked([exc.reason], str(exc), paths)
        if not verification["after_passed"]:
            try:
                rollback_from_journal(
                    ctx, policy, store, attempt_dir / "apply_journal.json"
                )
            except ApplyJournalError as exc:
                return _blocked(
                    [exc.reason],
                    "post-apply verification failed and automatic restore stopped: "
                    + str(exc),
                    paths,
                )
            _write_attempt_json(
                attempt_dir, "verification_after_apply.json", verification
            )
            return _blocked(
                [REASON_VERIFICATION_FAILED],
                "post-apply verification failed; the workspace was restored to its exact prior bytes",
                paths,
            )
        applied = bool(journal)

    store.publish(
        sidecar.EVENT_CLOSED,
        attempt_id=attempt_id,
        payload={
            "final_state": state,
            "applied": applied,
            "approved_by": approval,
            "mode": policy["mode"],
        },
    )
    status = STATUS_VERIFIED if state == sidecar.STATE_VERIFIED else STATUS_REJECTED
    return result(
        status,
        blocking_reasons=[] if status == STATUS_VERIFIED else ["attempt_closed_without_repair"],
        artifact_paths=paths,
        next_action=(
            # Applying the repair rewrote a file the implementer handoff
            # snapshotted, so the lifecycle chain is now legitimately re-locked:
            # a changed test asset has to be re-accepted and re-reviewed through
            # the normal lifecycle, not waved through by the repair that changed it.
            "record the repair in 04/05, then start a new lifecycle generation: "
            "the applied file is part of the implementer handoff snapshot, so this "
            "UC is re-locked until it is re-accepted and re-reviewed"
            if applied
            else "record the outcome in 04/05; the original failure is preserved"
        ),
    )


def step_resume(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    store = store or sidecar.SelfHealSidecar(ctx)
    attempt_dir = sidecar.attempts_dir(ctx, store.attempt_id())
    journal_path = attempt_dir / "apply_journal.json"
    if not journal_path.exists():
        return _blocked([REASON_NO_JOURNAL], "there is no interrupted apply for this attempt")
    paths = [_rel(ctx, journal_path)]
    try:
        journal, _validated, _created_directories = _validated_apply_journal(
            ctx, policy, store, journal_path
        )
    except ApplyJournalError as exc:
        return _blocked([exc.reason], str(exc), paths)
    try:
        rollback_from_journal(ctx, policy, store, journal_path)
    except ApplyJournalError as exc:
        return _blocked([exc.reason], str(exc), paths)
    return result(
        STATUS_HEALING_ACTIVE,
        artifact_paths=paths,
        next_action="the interrupted apply was rolled back to exact prior bytes; re-run close to retry",
    )


def step_status(
    ctx: GovernanceContext,
    policy: dict[str, Any],
    args: argparse.Namespace,
    store: sidecar.SelfHealSidecar | None = None,
) -> dict[str, Any]:
    store = store or sidecar.SelfHealSidecar(ctx)
    # One locked snapshot both applies authenticated orphan rules and prevents a
    # peer close/retriage from mixing state, attempt, review, and anchor fields
    # from different real sidecar states.
    snapshot = store.status_snapshot()
    if not snapshot["attempt_id"]:
        return result(
            STATUS_TRIAGED,
            next_action="no self-heal attempt has been opened for this UC",
        )
    if snapshot["drifted"]:
        return result(
            STATUS_INVALIDATED,
            blocking_reasons=[sidecar.REASON_DRIFT],
            artifact_paths=[_rel(ctx, sidecar.sidecar_dir(ctx))],
            next_action="the role chain advanced; re-run triage against current evidence",
        )
    state = str(snapshot["state"])
    attempt_id = str(snapshot["attempt_id"])
    return result(
        _status_for_state(state, str(snapshot["review_outcome"])),
        artifact_paths=[_rel(ctx, sidecar.sidecar_dir(ctx))],
        next_action=f"self-heal attempt {attempt_id!r} is in state {state!r}",
    )


STEP_HANDLERS = {
    "triage": step_triage,
    "handoff": step_handoff,
    "accept": step_accept,
    "propose": step_propose,
    "review": step_review,
    "close": step_close,
    "resume": step_resume,
    "status": step_status,
}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def run(artifact_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Execute one self-heal step and return the frozen five-field result."""

    try:
        ctx = load_context(artifact_dir)
    except RoleGovernanceError as exc:
        return _blocked([REASON_CONFIG], str(exc))

    try:
        policy = self_healing_policy(ctx.config)
    except RoleConfigError as exc:
        return _blocked([REASON_CONFIG], str(exc))

    if policy["mode"] == "off":
        # The capability does not exist here: create nothing, read nothing else.
        return result(
            STATUS_DISABLED,
            next_action="set self_healing.mode in the SUT profile to enable failure triage",
        )

    step = args.self_heal_step
    # ``status`` and ``resume`` deliberately skip the main role-evidence snapshot
    # preflight.  ``status`` must still be able to report integrity failure and may
    # persist lifecycle invalidation; ``resume`` may restore workspace bytes and
    # update its authenticated apply journal after an interrupted ``close``.
    # Applying a repair changes a file snapshotted by the implementer handoff, so
    # requiring that old snapshot to remain current would make exact restore
    # unreachable precisely after the write it is designed to undo.  The two
    # steps still run through the sidecar integrity boundary below, and ``resume``
    # can restore only bytes authenticated by the engine-created journal.
    if step not in {"status", "resume"}:
        errors = sidecar.self_heal_preflight(ctx)
        if errors:
            if ctx.mode == "advisory":
                for warning in errors:
                    print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
            else:
                for error in errors:
                    print(f"  - {error}", file=sys.stderr)
                return _blocked(
                    [REASON_PREFLIGHT, *errors],
                    "resolve the role-evidence integrity error before any self-heal step",
                )

    try:
        # Sidecar construction and the outer anchor check are part of the same
        # integrity boundary as the step handler.  A truncated or tampered chain
        # must produce the frozen five-field JSON contract, never an uncaught
        # traceback/exit 1 before the handler-level exception mapping can run.
        store = sidecar.SelfHealSidecar(ctx)
        # One cooperative CLI step is one artifact transaction: state preflight,
        # attempt-evidence/workspace changes, and the final receipt publication
        # must not be interleaved with another self-heal or main-chain publisher.
        # The sidecar methods below are re-entrant on this same store instance,
        # so they reuse the artifact-directory flock without adding a lock file
        # to the frozen evidence layout.  An uncooperative same-OS writer remains
        # outside this advisory-lock trust boundary and is handled by byte/hash
        # revalidation where the contract requires it.
        with store._operation_lock():
            if store.exists() and step not in {"status", "resume"}:
                if store.invalidate_if_drifted():
                    return result(
                        STATUS_INVALIDATED,
                        blocking_reasons=[sidecar.REASON_DRIFT],
                        artifact_paths=[_rel(ctx, sidecar.sidecar_dir(ctx))],
                        next_action="the role chain advanced; re-run triage against current evidence",
                    )

            handler = STEP_HANDLERS[step]
            return handler(ctx, policy, args, store)
    except sidecar.SelfHealSidecarError as exc:
        if exc.reason == sidecar.REASON_DRIFT:
            return result(
                STATUS_INVALIDATED,
                blocking_reasons=[sidecar.REASON_DRIFT],
                next_action=str(exc),
            )
        return _blocked([exc.reason], str(exc))
    except RoleGovernanceError as exc:
        return _blocked([REASON_PREFLIGHT], str(exc))
    except SelfHealEvidenceError as exc:
        return _blocked([exc.reason], str(exc))
    except (OSError, UnicodeError, json.JSONDecodeError, shutil.Error) as exc:
        return _blocked([REASON_EVIDENCE_INVALID], str(exc))
    except subprocess.SubprocessError as exc:
        return _blocked([REASON_VERIFICATION_EXECUTION_FAILED], str(exc))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--self-heal-step",
        choices=STEPS,
        default="triage",
        help="which governed self-heal step to run (default: triage)",
    )
    parser.add_argument("--candidate-dir", default="", help="tree of proposed file contents")
    parser.add_argument("--review-file", default="", help="independent reviewer verdict document")
    parser.add_argument("--human-approval", default="", help="approver identity for apply_with_approval")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--pytest-log", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--exit-code", type=int, default=0)
    add_arguments(parser)
    args = parser.parse_args()
    return emit(run(args.artifact_dir, args))


if __name__ == "__main__":
    raise SystemExit(main())
