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
from pathlib import Path
from typing import Any

from bugate_core import find_root, load_config, rel, read_text, strip_inline_comment


WRITE_ACTIONS = {"Edit", "Write", "apply_patch", "MultiEdit"}
READ_ACTIONS = {"Read"}

APPLY_PATCH_PATH_RE = re.compile(
    r"^\*\*\*\s+(?:(?:Update|Add|Delete)\s+File|Move\s+to):\s+(.+?)\s*$",
    re.MULTILINE,
)


def _config_text(root: Path, profile: str | None) -> str:
    """Concatenate the base config and the referenced profile file text.

    ``bugate_core.parse_simple_yaml`` only understands top-level scalars and
    lists, so it cannot represent the nested ``agent_roles:`` mapping. We read
    the raw text ourselves and parse just that block.
    """

    chunks: list[str] = []
    base = root / "bugate.config.yaml"
    if base.exists():
        chunks.append(read_text(base))
    # Resolve the configured profile path the same way load_config does.
    cfg = load_config(root, profile)
    profile_path = profile or cfg.get("profile") or cfg.get("active_profile")
    if profile_path:
        path = Path(str(profile_path))
        if not path.is_absolute():
            path = root / path
        if path.exists():
            chunks.append(read_text(path))
    return "\n".join(chunks)


def _parse_agent_roles(text: str) -> dict[str, dict[str, list[str]]]:
    """Parse the ``agent_roles:`` block into ``{role: {read: [...], write: [...]}}``.

    Supports both a bare list under a role (applies to read+write) and explicit
    ``read:`` / ``write:`` sub-lists. Stdlib-only, intentionally a small subset.
    """

    roles: dict[str, dict[str, list[str]]] = {}
    lines = text.splitlines()
    i = 0
    n = len(lines)

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def strip_quotes(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    # Find the top-level `agent_roles:` key.
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if indent_of(raw) == 0 and stripped.split("#", 1)[0].strip() == "agent_roles:":
            i += 1
            break
        i += 1
    else:
        return roles

    role_indent: int | None = None
    current_role: str | None = None
    current_bucket: str | None = None  # "read", "write", or None (bare list = both)
    bucket_indent: int | None = None

    while i < n:
        # Strip trailing inline `# ...` comments (quote-aware), matching the main
        # config parser, so a comment on a role/sub-list/pattern line can't be
        # baked into the regex.
        raw = strip_inline_comment(lines[i])
        stripped = raw.strip()
        # Blank or comment lines do not terminate the block.
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        ind = indent_of(raw)
        # A new top-level (indent 0) key ends the agent_roles block.
        if ind == 0:
            break

        if role_indent is None:
            role_indent = ind

        # Role header: `  implementer:` (possibly with an inline value, ignored).
        if ind == role_indent and stripped.endswith(":") and not stripped.startswith("- "):
            current_role = stripped[:-1].strip()
            roles.setdefault(current_role, {"read": [], "write": []})
            current_bucket = None
            bucket_indent = None
            i += 1
            continue

        if current_role is None:
            i += 1
            continue

        # read:/write: sub-headers under a role.
        if ind > role_indent and stripped in {"read:", "write:"}:
            current_bucket = stripped[:-1]
            bucket_indent = ind
            i += 1
            continue

        # List item.
        if stripped.startswith("- "):
            pattern = strip_quotes(stripped[2:])
            if not pattern:
                i += 1
                continue
            if current_bucket in {"read", "write"} and bucket_indent is not None and ind > bucket_indent:
                roles[current_role][current_bucket].append(pattern)
            else:
                # Bare list directly under the role -> applies to both.
                current_bucket = None
                roles[current_role]["read"].append(pattern)
                roles[current_role]["write"].append(pattern)
            i += 1
            continue

        i += 1

    # Drop roles that ended up empty.
    return {r: b for r, b in roles.items() if b["read"] or b["write"]}


def forbidden_patterns(text: str, role: str, action: str) -> list[str]:
    roles = _parse_agent_roles(text)
    bucket = roles.get(role)
    if not bucket:
        return []
    if action in READ_ACTIONS:
        return bucket["read"]
    if action in WRITE_ACTIONS:
        return bucket["write"]
    # Unknown action -> be conservative and check the union.
    return sorted(set(bucket["read"]) | set(bucket["write"]))


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
    config_text = _config_text(root, os.environ.get("BUGATE_PROFILE"))

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
    patterns = forbidden_patterns(config_text, role, action)
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
