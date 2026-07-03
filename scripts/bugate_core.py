#!/usr/bin/env python3
"""Shared BUGate core helpers.

This module intentionally uses only the Python standard library. SUT profiles
may add richer tooling, but BUGate core should stay dependency-light.
"""

from __future__ import annotations

import hashlib
import json
import os
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


WORKSPACE_SENTINEL = "bugate.config.yaml"

# The fixed kit layout: the subtrees `bugate init` vendors into a governed repo
# and the de-SUT guard therefore scans as the reusable surface. Init may vendor
# a subset of an entry (e.g. one skill dir under .shared/skills); the guard
# scans the whole entry. tests/test_desut_guard.py asserts the two stay aligned.
KIT_LAYOUT = ("scripts", "bin", ".shared/skills")


def find_root(start: Path | None = None) -> Path:
    """Locate the governed WORKSPACE root (where config, profile, artifacts live).

    Resolution order:
    1. ``BUGATE_PROJECT_ROOT`` env var — explicit override;
    2. the nearest ancestor of ``start`` (default CWD) carrying
       ``bugate.config.yaml`` — the imported-mode contract: the governed repo
       commits its own config, so the config marks the workspace;
    3. legacy sentinel fallback (``AGENTS.md`` + ``.shared/``) for pre-split
       layouts where the engine repo is itself the workspace (BUGate self-development).

    The engine's own location is a separate concern — see ``find_engine_root``.
    """
    env = os.environ.get("BUGATE_PROJECT_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    start = (start or Path.cwd()).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / WORKSPACE_SENTINEL).exists():
            return candidate
    for candidate in candidates:
        if (candidate / "AGENTS.md").exists() and (candidate / ".shared").exists():
            return candidate
    raise SystemExit(
        "BUGate workspace root not found: expected bugate.config.yaml in an "
        "ancestor (imported mode), an AGENTS.md + .shared engine-development layout, "
        "or BUGATE_PROJECT_ROOT set"
    )


def find_engine_root() -> Path:
    """Locate the ENGINE root (where BUGate's own scripts/ and .shared/ live).

    Independent of the workspace root: in imported mode the engine is vendored
    into (or shipped as a plugin alongside) the governed repo, so it is resolved
    from this file's location, never from CWD. ``BUGATE_ENGINE_ROOT`` overrides
    for layouts that relocate the asset tree away from the scripts.
    """
    env = os.environ.get("BUGATE_ENGINE_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


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
        raw = strip_inline_comment(raw)  # drop trailing ' # ...' (quote-safe); keeps indent
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


def inventory_sha256(artifact_dir: Path) -> str:
    """sha256 hex of an artifact dir's 03_inventory.yaml ('' if absent).

    Detects when a generated 03a_test_cases.md has drifted from a newer
    inventory: the readable-cases generator records this in the 03a frontmatter
    (``source_inventory_sha256``) and the orchestrator compares it before
    deciding whether to regenerate, so a freshly-added inventory case flows
    through to the readable layer automatically.
    """
    path = artifact_dir / "03_inventory.yaml"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


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


def as_bool(value: Any) -> bool:
    """Interpret a config flag (which may arrive as a string) as a boolean."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "on", "1"}


def required_precode_artifacts(config: dict[str, Any] | None = None) -> list[str]:
    configured = (config or {}).get("required_precode_artifacts")
    if isinstance(configured, list) and configured:
        return [str(item) for item in configured]
    return PRECODE_ARTIFACTS[:]


def _source_roots(
    config: dict[str, Any] | None, key: str, root: Path | None = None
) -> list[Path]:
    """Resolve a profile path-or-list key into deduped resolved Paths.

    A SUT profile may bind `evidence_sources` / `skill_sources` as a single path or
    a list; resolve each against the BUGate root, preserving order, dropping blanks
    and duplicates. Returns [] when the key is absent — these are optional bindings,
    so the resolver never raises on a profile that omits them.
    """
    raw = (config or {}).get(key)
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    root = root or find_root()
    roots: list[Path] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        path = resolve_path(text, root)
        if path not in roots:
            roots.append(path)
    return roots


def evidence_roots(
    config: dict[str, Any] | None = None,
    root: Path | None = None,
    *,
    existing_only: bool = False,
) -> list[Path]:
    """Resolved SUT contract/evidence roots from the profile `evidence_sources` key.

    The first entry is the primary contract root. This lets a flow resolve where a
    SUT's endpoint/interface contracts live *via the profile* instead of guessing a
    path. ``existing_only`` filters to roots that exist on disk.
    """
    roots = _source_roots(config, "evidence_sources", root)
    return [p for p in roots if p.exists()] if existing_only else roots


def skill_roots(
    config: dict[str, Any] | None = None,
    root: Path | None = None,
    *,
    existing_only: bool = False,
) -> list[Path]:
    """Resolved SUT skill-source dirs from the profile `skill_sources` key.

    Lets a flow resolve SUT-specific skills staged in the mounted workspace through
    the profile, without those skills entering Core. ``existing_only`` filters to
    dirs that exist on disk.
    """
    roots = _source_roots(config, "skill_sources", root)
    return [p for p in roots if p.exists()] if existing_only else roots


def ids(pattern: str, text: str) -> set[str]:
    return set(re.findall(pattern, text))


PROPOSITION_PATTERN = r"\bP-\d{3,}\b"
ORACLE_PATTERN = r"\bO-\d{3,}\b"


def proposition_ids(text: str, pattern: str | None = PROPOSITION_PATTERN) -> set[str]:
    return ids(pattern, text) if pattern else set()


def oracle_ids(text: str, pattern: str | None = ORACLE_PATTERN) -> set[str]:
    return ids(pattern, text) if pattern else set()


def case_ids(text: str) -> set[str]:
    return ids(r"\b(?:CASE|ADV)-[A-Z0-9_-]+\b", text)


# ── Semantic-schema dialects ─────────────────────────────────────────────────
# Core enforces the universal artifact CONTRACT (a brief establishes propositions
# and oracles, a testability note declares strategy + evidence, an inventory
# lists intent-bearing cases). The SECTION NAMES and ID conventions that ENCODE
# that contract are a *dialect*. The default "v1.3" preset is the canonical
# dialect — its checks are byte-for-byte the previous hard-coded behaviour. A SUT
# profile whose accumulated artifacts predate / diverge from the canonical schema
# may select an alternate dialect with `semantic_schema: <name>`; the
# `--schema <name>` flag overrides per-invocation. Presets stay SUT-neutral: they
# carry only generic governance section names, never SUT product terms.
DEFAULT_SEMANTIC_SCHEMA = "v1.3"

SEMANTIC_SCHEMAS: dict[str, dict[str, Any]] = {
    "v1.3": {
        "proposition_pattern": PROPOSITION_PATTERN,
        "oracle_pattern": ORACLE_PATTERN,
        "modeling_stack": True,
        "brief": {
            "require_proposition_ids": True,
            "require_oracle_ids": True,
            "sections": ["Scope", "Canonical Business Flows", "Propositions",
                         "Business Oracles", "Boundaries", "Assumptions", "Open Questions"],
            "proposition_table": "Propositions",
            "oracle_table": "Business Oracles",
            "clarification_table": "Clarification Gate",
        },
        "layer2": {"sections": ["Layer Decision", "Evidence Plan"]},
        "inventory": {
            "required_case_keys": ["proposition_refs", "oracle_refs", "layer_decision"],
            "passed_case_keys": ["expected_observations", "preconditions"],
            "require_data_source_status": True,
        },
        "adversarial": {
            "cases_key": "adversarial_cases",
            "required_fields": ["risk", "scenario", "expected_oracle_pressure"],
            "disposition_field": "disposition",
            "residual_key": "residual_risks",
        },
    },
    # Pre-canonical dialect used by the original in-repo gate: prose assertions
    # instead of P-/O- ids, narrative section names, a free-form nested inventory.
    "original-gate": {
        "proposition_pattern": None,
        "oracle_pattern": None,
        # The optional Full-SDTD modeling stack (01a/01b/02a) has no canonical-id
        # equivalent in this dialect; don't validate it under v1.3 id rules.
        "modeling_stack": False,
        "brief": {
            "require_proposition_ids": False,
            "require_oracle_ids": False,
            # Each entry is any-of: the corpus drifted into synonym section names.
            "sections": [["SUT And Scope", "Scope"],
                         ["Canonical Business Flow", "Canonical Business Flows"],
                         ["Assertions That Follow From Business", "PRD-Derived Propositions"],
                         ["Unknowns And Questions", "Open Questions"]],
            "proposition_table": "Propositions",
            "oracle_table": "Business Oracles",
            "clarification_table": "Clarification Gate",
        },
        # Two universal requirements, each any-of across the testability sub-dialects:
        # (a) a layer/execution decision, (b) an assertion/evidence plan.
        "layer2": {"sections": [["Execution Boundary", "Test Layer Decision"],
                                ["Assertion Strategy", "Acceptance Criteria", "Evidence Plan"]]},
        "inventory": {
            "required_case_keys": [],
            "passed_case_keys": [],
            "require_data_source_status": False,
        },
        # 03b dialect: decision/threat_model/coverage_decisions instead of the
        # canonical disposition/scenario/residual_risks; richer per-case schema.
        "adversarial": {
            "cases_key": "adversarial_cases",
            "required_fields": ["risk", "threat_model", "expected_defense_or_oracle"],
            "disposition_field": "decision",
            "residual_key": "coverage_decisions",
        },
    },
}


def section_missing(body: str, requirement: Any, *, prefix: str = "## ") -> str | None:
    """Return a label for an unmet section requirement, or None if satisfied.

    ``requirement`` is a heading name, or a list of acceptable names (any-of):
    the requirement is met when at least one ``{prefix}{name}`` occurs in body.
    Any-of lets one dialect tolerate synonym section names across drifted artifacts.
    """
    names = requirement if isinstance(requirement, list) else [requirement]
    if any(f"{prefix}{name}" in body for name in names):
        return None
    return " / ".join(str(name) for name in names)


def semantic_schema(name: str | None = None, *, layer: str | None = None) -> dict[str, Any]:
    """Return a schema preset, or one layer block merged over its top-level keys.

    Unknown names fall back to the canonical default. When ``layer`` is given,
    scalar top-level keys (e.g. proposition_pattern) are visible alongside the
    layer's own keys so a checker can read both from one dict.
    """
    preset = SEMANTIC_SCHEMAS.get(str(name or DEFAULT_SEMANTIC_SCHEMA)) or SEMANTIC_SCHEMAS[DEFAULT_SEMANTIC_SCHEMA]
    if layer is None:
        return preset
    merged = {key: value for key, value in preset.items() if not isinstance(value, dict)}
    merged.update(preset.get(layer) or {})
    return merged


def artifact_in_profile_scope(artifact_dir: Path, config: dict[str, Any] | None, root: Path | None = None) -> bool:
    """True if ``artifact_dir`` lives under the profile's artifact_dir_template parent.

    Used to scope a profile-selected dialect to the governed SUT's own UC dirs, so
    core fixtures (templates, ephemeral test workspaces) keep the default dialect
    even while a SUT profile is active. Symlink-safe via resolve().
    """
    template = str((config or {}).get("artifact_dir_template") or "")
    if "{uc}" not in template:
        return False
    parent_rel = template.split("{uc}", 1)[0].rstrip("/")
    if not parent_rel:
        return False
    try:
        parent = (resolve_path(parent_rel, root)).resolve()
        target = artifact_dir.resolve()
    except OSError:
        return False
    return target == parent or parent in target.parents


def resolve_schema_name(
    artifact_dir: Path,
    config: dict[str, Any] | None = None,
    *,
    override: str | None = None,
    root: Path | None = None,
) -> str:
    """Pick the dialect for an artifact dir: --schema override > in-scope profile > default."""
    if override:
        return override
    configured = (config or {}).get("semantic_schema")
    if configured and artifact_in_profile_scope(artifact_dir, config, root=root):
        return str(configured)
    return DEFAULT_SEMANTIC_SCHEMA


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
        line = strip_inline_comment(raw)  # quote-safe trailing ' # ...' removal (consistent with the other parsers)
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
    if enabled and re.search(r"\bTBD\b|待定|\bTODO\b", body, re.I):
        report.fail(f"{path.name}: accepted artifact must not contain TBD/TODO placeholders")


def load_json(path: Path) -> Any:
    return json.loads(read_text(path))


def dump_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main_error(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return 2
