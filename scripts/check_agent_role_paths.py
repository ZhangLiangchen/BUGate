#!/usr/bin/env python3
"""SUT-neutral BUGate agent-role path isolation guard (default OFF).

This is the Wave 7 "agent-role path isolation" mechanism, de-SUT'd for the
BUGate core. The original methodology splits test production into distinct
roles (e.g. builder / designer / implementer) and stops one role from touching
files that belong to another — most importantly stopping the test implementer
from re-deriving expectations out of SUT source ("复刻实现" instead of
"验证契约").

This core version ships with NO hardcoded paths or role names. Everything is
supplied by the active SUT profile:

  - Role comes from the env var ``BUGATE_AGENT_ROLE``. If it is unset (or empty),
    the guard is a no-op (allow everything) — exactly like the upstream
    default-OFF behavior.
  - Forbidden path patterns come from the SUT profile via ``load_config()``,
    under an ``agent_roles:`` mapping of ``role -> [pattern, ...]``. Patterns
    are matched as Python regexes (a literal path substring is a valid regex).
    If the active profile defines no ``agent_roles``, or defines none for the
    active role, the guard is a no-op (allow).

Semantic (deny wins; everything else is allowed):

  - When a role is active AND an edited/read path matches one of that role's
    forbidden patterns -> deny (exit non-zero) with a clear message.
  - Otherwise -> allow (exit 0).

Wiring (profile-defined; the core only provides the mechanism):
  - Claude Code: PreToolUse hook with matcher ``Read|Edit|Write`` calling this
    script. Payload paths are read from ``tool_input.file_path`` / ``path`` /
    ``filePath`` and from an ``input`` ``*** Update File:`` patch string.
  - Codex Desktop: PreToolUse ``apply_patch`` can reuse this script; patch
    target paths are treated as writes.

Profile config shape (``bugate.config.yaml`` or a referenced profile)::

  agent_roles:
    implementer:
      - "src/.+_internal\\.py$"
    designer:
      - "tests/.+/test_.+\\.py$"

Optional read/write scoping: a role may instead define ``read`` and/or
``write`` sub-lists to restrict a pattern to only reads or only writes::

  agent_roles:
    implementer:
      read:
        - "src/vendor/.*"      # may not READ vendored SUT source
      write:
        - "specs/.*"           # may not WRITE spec artifacts

A bare list (no read/write keys) applies to both reads and writes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bugate_core import find_root, load_config, rel


WRITE_ACTIONS = {"Edit", "Write", "apply_patch", "MultiEdit"}
READ_ACTIONS = {"Read"}

APPLY_PATCH_PATH_RE = re.compile(
    r"^\*\*\*\s+(?:(?:Update|Add|Delete)\s+File|Move\s+to):\s+(.+?)\s*$",
    re.MULTILINE,
)


def _pattern_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of regex strings")
    patterns: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} entries must be non-empty regex strings")
        patterns.append(item)
    return patterns


def forbidden_patterns(config: Mapping[str, Any], role: str, action: str) -> list[str]:
    """Return active-role patterns from ``load_config``'s merged mapping.

    A bare role list applies to reads and writes. A mapping may scope patterns
    through ``read`` and ``write``. Role names remain profile-defined; lookup is
    case-insensitive because the environment role token is normalized.
    """

    roles = config.get("agent_roles")
    if roles is None:
        return []
    if not isinstance(roles, Mapping):
        raise ValueError("agent_roles must be a mapping of role names to path rules")

    normalized_role = role.strip().lower()
    matches = [value for key, value in roles.items() if str(key).strip().lower() == normalized_role]
    if not matches:
        return []
    if len(matches) > 1:
        raise ValueError(f"agent_roles contains duplicate normalized role {normalized_role!r}")

    rule = matches[0]
    if isinstance(rule, list):
        read_patterns = write_patterns = _pattern_list(
            rule, label=f"agent_roles[{normalized_role}]"
        )
    elif isinstance(rule, Mapping):
        read_patterns = _pattern_list(
            rule.get("read"), label=f"agent_roles[{normalized_role}].read"
        )
        write_patterns = _pattern_list(
            rule.get("write"), label=f"agent_roles[{normalized_role}].write"
        )
    else:
        raise ValueError(
            f"agent_roles[{normalized_role}] must be a bare list or read/write mapping"
        )

    if action in READ_ACTIONS:
        return read_patterns
    if action in WRITE_ACTIONS:
        return write_patterns
    # Unknown action -> be conservative and check the union.
    return sorted(set(read_patterns) | set(write_patterns))


def extract_targets(payload: Any) -> tuple[str, list[str]]:
    """Return (action, [paths]) from a Claude/Codex PreToolUse payload."""

    if not isinstance(payload, dict):
        return "Edit", []
    action = str(payload.get("tool_name") or payload.get("toolName") or "").strip()
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    targets: list[str] = []
    if isinstance(tool_input, dict):
        for key in ("file_path", "path", "filePath"):
            value = tool_input.get(key)
            if value:
                targets.append(str(value))
        patch_input = tool_input.get("input")
        if isinstance(patch_input, str) and "*** " in patch_input:
            action = action or "apply_patch"
            targets.extend(
                match.group(1).strip()
                for match in APPLY_PATCH_PATH_RE.finditer(patch_input)
            )
    return action or "Edit", targets


def _to_rel(root: Path, target: str) -> str:
    target = str(target).strip()
    try:
        return rel(Path(target), root)
    except Exception:
        # Best-effort: strip the root prefix if present.
        text = target
        prefix = str(root)
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip("/")
        return text


def main() -> int:
    role = os.environ.get("BUGATE_AGENT_ROLE", "").strip().lower()
    if not role:
        return 0  # agent-role isolation not enabled for this session

    root = find_root(Path.cwd().resolve())
    try:
        config = load_config(root, os.environ.get("BUGATE_PROFILE"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"BUGate agent-role guard: config error: {exc}\n")
        return 2

    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception:
        raw = ""
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    action, targets = extract_targets(payload)
    try:
        patterns = forbidden_patterns(config, role, action)
    except ValueError as exc:
        sys.stderr.write(f"BUGate agent-role guard: invalid agent_roles config: {exc}\n")
        return 2
    if not patterns:
        return 0  # active profile defines no rules for this role/action

    try:
        compiled = [re.compile(pattern) for pattern in patterns]
    except re.error as exc:
        sys.stderr.write(
            f"BUGate agent-role guard: invalid regex in agent_roles[{role}]: {exc}\n"
        )
        return 2

    failures: list[str] = []
    for target in targets:
        relpath = _to_rel(root, target)
        if any(regex.search(relpath) for regex in compiled):
            failures.append(f"{action} {relpath}")

    if not failures:
        return 0

    sys.stderr.write(
        f"BUGate agent-role path isolation (role={role}) blocked:\n"
    )
    for item in failures:
        sys.stderr.write(f"  - {item}\n")
    sys.stderr.write(
        "These paths are forbidden for the active role by the SUT profile's "
        "`agent_roles` config.\n"
        "Hand the change to the appropriate role, or unset BUGATE_AGENT_ROLE "
        "only if the human owner explicitly lifts the isolation for this session.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
