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

# Optional Full-SDTD modeling artifacts. They are NOT part of the required
# pre-code set: a profile or use case opts into them "when needed" for complex
# flows. The v1.3 semantic checker validates them only when present.
OPTIONAL_PRECODE_ARTIFACTS = [
    "01a_domain_model.md",
    "01b_state_flow.md",
    "02a_test_dimension_matrix.yaml",
]


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


def strip_inline_comment(line: str) -> str:
    """Drop a trailing ``  # ...`` comment, ignoring ``#`` inside quotes."""

    quote: str | None = None
    out: list[str] = []
    for idx, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            out.append(char)
            continue
        if char == "#" and (idx == 0 or line[idx - 1] in {" ", "\t"}):
            break
        out.append(char)
    return "".join(out).rstrip()


def parse_nested_yaml(text: str) -> Any:
    """Parse the indented YAML subset used by nested BUGate artifacts.

    Supports nested mappings, lists of scalars, lists of mappings (including a
    first key on the dash line), inline ``[a, b]`` lists, and scalars. It is not
    a general YAML parser, by design; BUGate core stays standard-library only.
    """

    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        content = strip_inline_comment(raw)
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        rows.append((indent, content.strip()))
    idx = [0]

    def parse_map(indent: int) -> dict[str, Any]:
        node: dict[str, Any] = {}
        while idx[0] < len(rows):
            cur_indent, line = rows[idx[0]]
            if cur_indent != indent or line.startswith("- "):
                break
            if ":" not in line:
                idx[0] += 1
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            idx[0] += 1
            node[key] = parse_scalar(value) if value else parse_child(indent)
        return node

    def parse_child(parent_indent: int) -> Any:
        if idx[0] >= len(rows):
            return None
        cur_indent, line = rows[idx[0]]
        if cur_indent <= parent_indent:
            return None
        return parse_list(cur_indent) if line.startswith("- ") else parse_map(cur_indent)

    def parse_list(indent: int) -> list[Any]:
        items: list[Any] = []
        while idx[0] < len(rows):
            cur_indent, line = rows[idx[0]]
            if cur_indent != indent or not line.startswith("- "):
                break
            rest = line[2:].strip()
            idx[0] += 1
            if not rest:
                items.append(parse_child(indent))
            elif ":" in rest and rest[0] not in {"[", '"', "'"}:
                key, _, value = rest.partition(":")
                item: dict[str, Any] = {key.strip(): parse_scalar(value.strip()) if value.strip() else None}
                while idx[0] < len(rows):
                    sub_indent, sub_line = rows[idx[0]]
                    if sub_indent <= indent or sub_line.startswith("- "):
                        break
                    item.update(parse_map(sub_indent))
                    break
                items.append(item)
            else:
                items.append(parse_scalar(rest))
        return items

    if idx[0] < len(rows) and rows[0][1].startswith("- "):
        return parse_list(rows[0][0])
    return parse_map(0)


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


def section_between(text: str, heading: str, level: str = "## ") -> str:
    """Return the body of a ``## heading`` section up to the next same-level heading."""

    marker = f"{level}{heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    nxt = text.find(f"\n{level}", start + len(marker))
    return text[start:] if nxt < 0 else text[start:nxt]


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def table_dicts(text: str, heading: str) -> list[dict[str, str]]:
    """Parse the markdown table under ``## heading`` into a list of row dicts.

    Keys are the normalized header cells; empty rows are dropped.
    """

    rows = markdown_table_rows(section_between(text, heading))
    if len(rows) < 2:
        return []
    headers = [normalize_header(cell) for cell in rows[0]]
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        result.append({headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))})
    return result


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
