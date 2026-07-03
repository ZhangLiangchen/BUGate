#!/usr/bin/env python3
"""SUT-neutral BUGate physical write guard.

The core repository ships with no guarded paths. A SUT profile can enable this
guard by adding regexes under `guarded_path_regex` in `bugate.config.yaml`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from bugate_core import find_root, gate_status, load_config, required_precode_artifacts


PATCH_HEADER_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.MULTILINE)


def guarded_patterns(config: dict[str, Any]) -> list[str]:
    values = config.get("guarded_path_regex") or []
    if isinstance(values, str):
        return [values]
    return [str(item) for item in values if str(item).strip()]


def resolve_artifact_dir(root: Path, config: dict[str, Any]) -> Path | None:
    value = config.get("artifact_dir") or config.get("artifact_root")
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def precode_passed(artifact_dir: Path, config: dict[str, Any]) -> tuple[bool, list[str]]:
    missing_or_pending: list[str] = []
    for name in required_precode_artifacts(config):
        path = artifact_dir / name
        if not path.exists():
            missing_or_pending.append(f"missing {name}")
        elif gate_status(path) != "passed":
            missing_or_pending.append(f"{name} gate_status={gate_status(path) or '<missing>'}")
    return not missing_or_pending, missing_or_pending


def artifact_dir_template(config: dict[str, Any]) -> str | None:
    return config.get("artifact_dir_template")


def _canon_uc(name: str) -> str:
    """Fold a UC token for comparison: lowercase, ``-``/``_`` removed."""
    return re.sub(r"[-_]", "", name).lower()


def _resolve_uc_normalized(uc: str, template: str, root: Path) -> Path | None:
    """Resolve ``{uc}`` to a real artifact dir by normalized match.

    For SUTs whose test filenames differ from their UC dir names only by case
    and ``-`` vs ``_`` (e.g. ``test_uc_rt_14_foo`` <-> ``UC-RT-14-foo``), match
    the captured token against the immediate subdirectories of the template's
    parent, case-insensitively with ``-``/``_`` folded. Fail-closed (None) on a
    zero or ambiguous match, so a token can never silently unlock the wrong UC.
    """
    prefix = template.split("{uc}", 1)[0]
    parent = Path(prefix)
    parent = parent if parent.is_absolute() else root / parent
    if not parent.is_dir():
        return None
    target = _canon_uc(uc)
    matches = sorted(d.name for d in parent.iterdir() if d.is_dir() and _canon_uc(d.name) == target)
    if len(matches) != 1:
        return None
    resolved = Path(template.replace("{uc}", matches[0]))
    return resolved if resolved.is_absolute() else root / resolved


def uc_dir_for(
    path: str,
    compiled: list[re.Pattern[str]],
    template: str,
    root: Path,
    resolve_mode: str | None = None,
) -> Path | None:
    """Bind a guarded path to its per-UC artifact dir via a ``(?P<uc>...)`` capture.

    Returns None (fail-closed) when the path matches a guarded pattern that
    carries no ``uc`` capture, so one UC's passed artifacts can never unlock a
    different UC's implementation files.

    Default binding substitutes ``{uc}`` literally. When a profile sets
    ``uc_dir_resolve: normalized-glob`` (passed here as ``resolve_mode``), the
    captured token is instead matched against real artifact dirs with case and
    ``-``/``_`` folded — for SUTs whose test filenames and UC dir names differ
    only by those (``test_uc_rt_14_foo`` <-> ``UC-RT-14-foo``).
    """
    for regex in compiled:
        match = regex.search(path)
        if not match:
            continue
        uc = (match.groupdict() or {}).get("uc")
        if not uc:
            return None
        if resolve_mode == "normalized-glob":
            return _resolve_uc_normalized(uc, template, root)
        resolved = Path(template.replace("{uc}", uc))
        return resolved if resolved.is_absolute() else root / resolved
    return None


def collect_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_path", "path", "filePath", "input"} and isinstance(item, str):
                found.append(item)
            found.extend(collect_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_strings(item))
    return found


def collect_paths(stdin_text: str) -> set[str]:
    values: list[str] = []
    if stdin_text.strip():
        try:
            values.extend(collect_strings(json.loads(stdin_text)))
        except json.JSONDecodeError:
            values.append(stdin_text)
    paths: set[str] = set()
    for value in values:
        for match in PATCH_HEADER_RE.finditer(value):
            path = match.group(1) or match.group(2)
            if path:
                paths.add(path.strip())
        if "\n" not in value and len(value) < 500:
            paths.add(value.strip())
    return {p for p in paths if p}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="File paths to check (manual use). When omitted, paths are read "
        "from the runtime's PreToolUse hook payload on stdin.",
    )
    args = parser.parse_args(argv)

    root = find_root(Path.cwd().resolve())
    config = load_config(root, os.environ.get("BUGATE_PROFILE"))
    patterns = guarded_patterns(config)
    if not patterns:
        return 0

    paths = {p.strip() for p in args.paths if p.strip()}
    # Read the hook payload from stdin only when it is piped (never block a TTY).
    if not sys.stdin.isatty():
        paths |= collect_paths(sys.stdin.read())
    if not paths:
        return 0

    compiled = [re.compile(pattern) for pattern in patterns]
    blocked = sorted(path for path in paths if any(regex.search(path) for regex in compiled))
    if not blocked:
        return 0

    template = artifact_dir_template(config)
    if template:
        # Per-UC fail-closed binding: each blocked path must map to its own UC
        # artifact dir (via the pattern's `uc` capture) and that dir must pass.
        reasons = []
        for path in blocked:
            uc_dir = uc_dir_for(path, compiled, template, root, config.get("uc_dir_resolve"))
            if uc_dir is None:
                reasons.append(f"{path}: cannot bind to a UC artifact dir (no 'uc' capture, or no unique normalized match) — fail-closed")
                continue
            passed, why = precode_passed(uc_dir, config)
            if not passed:
                reasons.append(f"{path}: {uc_dir} not ready: " + "; ".join(why))
        if not reasons:
            return 0
    else:
        artifact_dir = resolve_artifact_dir(root, config)
        if artifact_dir:
            passed, reasons = precode_passed(artifact_dir, config)
            if passed:
                return 0
        else:
            reasons = ["artifact_dir/artifact_root is not configured in the active BUGate profile"]

    sys.stderr.write("BUGate guard blocked edits to configured implementation paths:\n")
    for path in blocked:
        sys.stderr.write(f"  - {path}\n")
    sys.stderr.write("Complete and accept the configured pre-code BUGate artifacts before editing implementation files.\n")
    for reason in reasons:
        sys.stderr.write(f"  - {reason}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
