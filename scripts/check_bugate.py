#!/usr/bin/env python3
"""SUT-neutral BUGate physical write guard.

The core repository ships with no guarded paths. A SUT profile can enable this
guard by adding regexes under `guarded_path_regex` in `bugate.config.yaml`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from bugate_core import gate_status, load_config, required_precode_artifacts


PATCH_HEADER_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.MULTILINE)


def find_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "AGENTS.md").exists() and (candidate / ".shared").exists():
            return candidate
    raise SystemExit("BUGate root not found: expected AGENTS.md and .shared")


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


def main() -> int:
    root = find_root(Path.cwd().resolve())
    config = load_config(root, os.environ.get("BUGATE_PROFILE"))
    patterns = guarded_patterns(config)
    if not patterns:
        return 0

    stdin_text = sys.stdin.read()
    paths = collect_paths(stdin_text)
    if not paths:
        return 0

    compiled = [re.compile(pattern) for pattern in patterns]
    blocked = sorted(path for path in paths if any(regex.search(path) for regex in compiled))
    if not blocked:
        return 0

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
