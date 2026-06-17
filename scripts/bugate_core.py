#!/usr/bin/env python3
"""Shared BUGate core helpers.

This module intentionally uses only the Python standard library. SUT profiles
may add richer tooling, but BUGate core should stay dependency-light.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PRECODE_ARTIFACTS = [
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
]
POSTRUN_ARTIFACTS = ["04_execution_report.md", "05_knowledge_update.md"]
ALL_ARTIFACTS = PRECODE_ARTIFACTS + POSTRUN_ARTIFACTS


def find_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "AGENTS.md").exists() and (candidate / ".shared").exists():
            return candidate
    raise SystemExit("BUGate root not found: expected AGENTS.md and .shared")


def resolve_path(value: str | Path, root: Path | None = None) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root or find_root()) / path


def rel(path: Path, root: Path | None = None) -> str:
    root = root or find_root()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_scalar(value: str) -> Any:
    value = strip_quotes(value.strip())
    if value in {"", "null", "None"}:
        return None
    if value == "[]":
        return []
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        items = [strip_quotes(item.strip()) for item in value[1:-1].split(",") if item.strip()]
        return items
    return value


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the simple YAML subset used by BUGate templates/config.

    Supports top-level scalars and top-level lists. It is not a general YAML
    parser, by design.
    """

    data: dict[str, Any] = {}
    active_list: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if active_list and indent > 0 and line.startswith("- "):
            data.setdefault(active_list, []).append(parse_scalar(line[2:]))
            continue
        active_list = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "":
            data[key] = []
            active_list = key
        else:
            data[key] = parse_scalar(value)
    return data


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm = parse_simple_yaml(text[4:end])
    body = text[text.find("\n", end + 1) + 1 :]
    return fm, body


def frontmatter_for(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    fm, _ = split_frontmatter(read_text(path))
    return fm


def gate_status(path: Path) -> str:
    if not path.exists():
        return ""
    text = read_text(path)
    fm, _ = split_frontmatter(text)
    if "gate_status" in fm:
        return str(fm.get("gate_status") or "").strip().lower()
    top = parse_simple_yaml(text)
    return str(top.get("gate_status") or "").strip().lower()


def artifact_path(artifact_dir: Path, name: str) -> Path:
    return artifact_dir / name


def load_config(root: Path | None = None, profile: str | None = None) -> dict[str, Any]:
    root = root or find_root()
    data: dict[str, Any] = {}
    base = root / "bugate.config.yaml"
    if base.exists():
        data.update(parse_simple_yaml(read_text(base)))
    profile_path = profile or data.get("profile") or data.get("active_profile")
    if profile_path:
        path = resolve_path(str(profile_path), root)
        if path.exists():
            data.update(parse_simple_yaml(read_text(path)))
    return data


def required_precode_artifacts(config: dict[str, Any] | None = None) -> list[str]:
    configured = (config or {}).get("required_precode_artifacts")
    if isinstance(configured, list) and configured:
        return [str(item) for item in configured]
    return PRECODE_ARTIFACTS[:]


def ids(pattern: str, text: str) -> set[str]:
    return set(re.findall(pattern, text))


def proposition_ids(text: str) -> set[str]:
    return ids(r"\bP-\d{3,}\b", text)


def oracle_ids(text: str) -> set[str]:
    return ids(r"\bO-\d{3,}\b", text)


def case_ids(text: str) -> set[str]:
    return ids(r"\b(?:CASE|ADV)-[A-Z0-9_-]+\b", text)


def markdown_table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", ":"} for cell in cells):
            rows.append(cells)
    return rows


def parse_inventory_cases(text: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^-\s+id:\s*(.+)$", stripped)
        if match:
            if current:
                cases.append(current)
            current = {"id": strip_quotes(match.group(1))}
            current_key = None
            continue
        if current is None:
            continue
        kv = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", stripped)
        if kv:
            key, value = kv.group(1), kv.group(2)
            if value == "":
                current[key] = []
                current_key = key
            else:
                current[key] = parse_scalar(value)
                current_key = None
            continue
        if current_key and stripped.startswith("- "):
            current.setdefault(current_key, []).append(parse_scalar(stripped[2:]))
    if current:
        cases.append(current)
    return cases


@dataclass
class GateReport:
    name: str
    target: Path
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def require_file(self, path: Path) -> bool:
        if not path.exists():
            self.fail(f"missing required file: {path.name}")
            return False
        return True

    def require_status(self, path: Path, required: str = "passed") -> None:
        status = gate_status(path)
        if status != required:
            self.fail(f"{path.name}: gate_status must be {required!r}, got {status or '<missing>'!r}")

    def exit(self) -> int:
        root = find_root()
        print(f"[{self.name}] {rel(self.target, root)}")
        for item in self.warnings:
            print(f"WARNING: {item}")
        if self.failures:
            for item in self.failures:
                print(f"FAIL: {item}")
            return 1
        print("PASS")
        return 0


def ensure_no_tbd(report: GateReport, path: Path, body: str, *, enabled: bool) -> None:
    if enabled and re.search(r"\bTBD\b|待定|TODO", body, re.I):
        report.fail(f"{path.name}: accepted artifact must not contain TBD/TODO placeholders")


def load_json(path: Path) -> Any:
    return json.loads(read_text(path))


def dump_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main_error(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return 2
