#!/usr/bin/env python3
"""Validated ``self_healing`` profile policy for test-asset self-healing.

The key is top level, a sibling of ``role_governance``, and every value is
validated the same fail-closed way: a malformed contract raises
:class:`role_governance.RoleConfigError` instead of silently degrading.

The default is ``mode: off``.  A repository that never opts in therefore has
exactly the v0.4.4 behavior and exposure surface -- ``off`` is not a soft
disable, it is the absence of the whole capability.

Relative paths in this block resolve against the directory that owns
``bugate.config.yaml`` (the imported SUT test repository's project root), never
against the caller's working directory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from role_governance import RoleConfigError


SELF_HEAL_MODES = ("off", "diagnose", "verify", "apply_with_approval")
SELF_HEAL_KEY = "self_healing"
SIDECAR_DIR = "00_self_healing"

#: Modes that may generate a candidate patch at all.
CANDIDATE_MODES = ("verify", "apply_with_approval")
#: Modes that may write a verified candidate back into the real workspace.
APPLY_MODES = ("apply_with_approval",)

_KNOWN_KEYS = (
    "mode",
    "allowed_write_regex",
    "denied_write_regex",
    "verification_commands",
    "falsification_spec",
    "max_attempts",
    "independent_review_required",
    "human_approval_required",
)

DEFAULT_POLICY: dict[str, Any] = {
    "mode": "off",
    "allowed_write_regex": [],
    "denied_write_regex": [],
    "verification_commands": [],
    "falsification_spec": "",
    "max_attempts": 3,
    "independent_review_required": True,
    "human_approval_required": True,
}

# Paths self-healing may never write, whatever the profile allows.  These are
# contract surfaces, not preferences: accepted pre-code evidence, the role
# evidence chain, the self-healing sidecar itself, and the profile that grants
# the permission in the first place.
FORBIDDEN_WRITE_REGEX = (
    r"(?:^|/)00_role_evidence(?:/|$)",
    r"(?:^|/)00_self_healing(?:/|$)",
    r"(?:^|/)0(?:1|2|3)[ab]?[_-][^/]*$",
    r"(?:^|/)0(?:4|5)[_-][^/]*$",
    r"(?:^|/)bugate\.config\.yaml$",
    r"(?:^|/)bugate\.profile[^/]*\.ya?ml$",
    r"(?:^|/)\.git(?:/|$)",
    r"(?:^|/)\.env[^/]*$",
    r"(?:^|/)[^/]*(?:secret|credential|token)[^/]*$",
)


def _strict_bool(value: Any, where: str) -> bool:
    """Accept only a real boolean; a truthy string is a contract error."""

    if isinstance(value, bool):
        return value
    raise RoleConfigError(f"{where} must be a boolean")


def _regex_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise RoleConfigError(f"{where} must be a string or list")
    patterns: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RoleConfigError(f"{where} entries must be non-empty strings")
        try:
            re.compile(item)
        except re.error as exc:
            raise RoleConfigError(f"invalid {where} {item!r}: {exc}") from exc
        patterns.append(item)
    return patterns


def _command_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise RoleConfigError(f"{where} must be a string or list")
    commands: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RoleConfigError(f"{where} entries must be non-empty strings")
        commands.append(item.strip())
    return commands


def self_healing_policy(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return a validated ``self_healing`` policy with canonical defaults."""

    raw = (config or {}).get(SELF_HEAL_KEY)
    if raw is None:
        return dict(DEFAULT_POLICY)
    if not isinstance(raw, dict):
        raise RoleConfigError("self_healing must be a mapping")
    unknown = sorted(set(raw) - set(_KNOWN_KEYS))
    if unknown:
        raise RoleConfigError("self_healing has unknown key(s): " + ", ".join(unknown))

    mode = raw.get("mode", DEFAULT_POLICY["mode"])
    if not isinstance(mode, str) or mode not in SELF_HEAL_MODES:
        raise RoleConfigError(
            "self_healing.mode must be off, diagnose, verify, or apply_with_approval"
        )

    # The config parser keeps scalars as written, so a YAML integer arrives as a
    # string.  Coerce here rather than at every use site, and still reject a
    # boolean (``true`` is not an attempt count) and any non-numeric text.
    raw_attempts = raw.get("max_attempts", DEFAULT_POLICY["max_attempts"])
    if isinstance(raw_attempts, bool):
        raise RoleConfigError("self_healing.max_attempts must be an integer")
    try:
        max_attempts = int(str(raw_attempts).strip())
    except (TypeError, ValueError) as exc:
        raise RoleConfigError("self_healing.max_attempts must be an integer") from exc
    if max_attempts < 1:
        raise RoleConfigError("self_healing.max_attempts must be >= 1")

    spec = raw.get("falsification_spec", DEFAULT_POLICY["falsification_spec"])
    if spec is None:
        spec = ""
    if not isinstance(spec, str):
        raise RoleConfigError("self_healing.falsification_spec must be a string path")

    independent = raw.get(
        "independent_review_required", DEFAULT_POLICY["independent_review_required"]
    )
    independent = _strict_bool(independent, "self_healing.independent_review_required")
    if not independent:
        # Frozen by the stage-2 contract: the independent semantic review is the
        # only control that separates a real repair from a fake-green one, so it
        # cannot be configured away.
        raise RoleConfigError(
            "self_healing.independent_review_required cannot be false: the "
            "independent anti-fake-green review is a frozen control"
        )

    human_approval = raw.get(
        "human_approval_required", DEFAULT_POLICY["human_approval_required"]
    )
    human_approval = _strict_bool(
        human_approval, "self_healing.human_approval_required"
    )
    if not human_approval:
        # ``apply_with_approval`` is the only mode that may write the real
        # workspace.  Its name and the frozen mode contract both require an
        # approval record, so a profile must not be able to configure away the
        # final write boundary.
        raise RoleConfigError(
            "self_healing.human_approval_required cannot be false: "
            "apply_with_approval requires a human approval record"
        )

    return {
        "mode": mode,
        "allowed_write_regex": _regex_list(
            raw.get("allowed_write_regex"), "self_healing.allowed_write_regex"
        ),
        "denied_write_regex": _regex_list(
            raw.get("denied_write_regex"), "self_healing.denied_write_regex"
        ),
        "verification_commands": _command_list(
            raw.get("verification_commands"), "self_healing.verification_commands"
        ),
        "falsification_spec": spec.strip(),
        "max_attempts": max_attempts,
        "independent_review_required": True,
        "human_approval_required": True,
    }


def self_healing_mode(config: dict[str, Any] | None) -> str:
    """Mode only, tolerating a malformed block as ``off`` for read-only callers.

    Mutating entry points must call :func:`self_healing_policy` so a malformed
    contract fails closed.  This helper exists for the classifier, which must
    never turn a configuration error into a *changed* classification.
    """

    try:
        return self_healing_policy(config)["mode"]
    except RoleConfigError:
        return "off"


def write_allowed(policy: dict[str, Any], relative_posix: str) -> tuple[bool, str]:
    """Decide whether self-healing may write one workspace-relative path.

    Order is deny-first: the hard-coded contract surfaces, then the profile's
    ``denied_write_regex``, then the profile's ``allowed_write_regex``.  An empty
    allow list permits nothing -- self-healing is opt-in per path, not per repo.
    """

    for pattern in FORBIDDEN_WRITE_REGEX:
        if re.search(pattern, relative_posix):
            return False, "write_target_is_a_governed_surface"
    for pattern in policy["denied_write_regex"]:
        if re.search(pattern, relative_posix):
            return False, "write_target_denied_by_profile"
    for pattern in policy["allowed_write_regex"]:
        if re.search(pattern, relative_posix):
            return True, ""
    return False, "write_target_not_allowed_by_profile"


def falsification_spec_path(policy: dict[str, Any], root: Path) -> Path | None:
    """Resolve ``falsification_spec`` against the project root (never CWD)."""

    value = str(policy.get("falsification_spec") or "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path
