#!/usr/bin/env python3
"""BUGate v1.3 artifact stack checker, SUT-neutral."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from bugate_core import (
    ALL_ARTIFACTS,
    GateReport,
    as_bool,
    ensure_no_tbd,
    load_config,
    parse_inventory_cases,
    parse_nested_yaml,
    read_text,
    required_precode_artifacts,
    split_frontmatter,
    table_dicts,
)
from check_bugate_brief_semantics import check as check_brief
from check_bugate_inventory_semantics import check as check_inventory
from check_bugate_layer2_semantics import check as check_layer2


OBJECT_RE = re.compile(r"^OBJ-\d{3,}$")
RELATION_RE = re.compile(r"^REL-\d{3,}$")
INVARIANT_RE = re.compile(r"^INV-\d{3,}$")
FLOW_RE = re.compile(r"^FLOW-\d{3,}$")
STATE_RE = re.compile(r"^STATE-\d{3,}$")
TRANSITION_RE = re.compile(r"^TR-\d{3,}$")
DIMENSION_RE = re.compile(r"^DIM-\d{3,}$")
PROP_RE = re.compile(r"^P-\d{3,}$")
ORACLE_RE = re.compile(r"^O-\d{3,}$")
CASE_HINT_RE = re.compile(r"^(?:CASE|ADV)-[A-Z0-9_-]+$")

VALID_DIMENSION_CATEGORY = {
    "flow",
    "state",
    "boundary",
    "permission",
    "error_handling",
    "async",
    "data_integrity",
    "safety",
    "contract",
    "regression",
}
VALID_LAYER = {"api", "contract", "integration", "e2e", "manual", "deferred"}
VALID_PRIORITY = {"P0", "P1", "P2", "P3"}
VALID_SIDE_EFFECT = {"read_only", "creates_resources", "irreversible"}
VALID_DATA_STRATEGY = {"fixed", "query_first", "generated", "create", "unavailable"}


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _merge(target: GateReport, source: GateReport) -> None:
    target.failures.extend(f"{source.name}: {item}" for item in source.failures)
    target.warnings.extend(f"{source.name}: {item}" for item in source.warnings)


def _check_readable_cases(report: GateReport, artifact_dir: Path, require_passed: bool) -> None:
    path = artifact_dir / "03a_test_cases.md"
    if not report.require_file(path):
        return
    if require_passed:
        report.require_status(path)
    body = read_text(path)
    inventory = artifact_dir / "03_inventory.yaml"
    if inventory.exists():
        for case in parse_inventory_cases(read_text(inventory)):
            cid = str(case.get("id") or "")
            if cid and cid not in body:
                report.fail(f"03a_test_cases.md: missing readable case for {cid}")
    if require_passed and re.search(r"\bTBD\b|待定|TODO", body, re.I):
        report.fail("03a_test_cases.md: accepted artifact must not contain placeholders")


def _check_adversarial(report: GateReport, artifact_dir: Path, require_passed: bool) -> None:
    path = artifact_dir / "03b_adversarial_cases.yaml"
    if not report.require_file(path):
        return
    if require_passed:
        report.require_status(path)
    body = read_text(path)
    if "adversarial_cases:" not in body:
        report.fail("03b_adversarial_cases.yaml: missing adversarial_cases")
    if require_passed and re.search(r"\bTBD\b|待定|TODO", body, re.I):
        report.fail("03b_adversarial_cases.yaml: accepted artifact must not contain placeholders")


def _check_report(report: GateReport, artifact_dir: Path, name: str, require_passed: bool) -> None:
    path = artifact_dir / name
    if not report.require_file(path):
        return
    if require_passed:
        status = {"passed", "draft"} if name.startswith(("04_", "05_")) else {"passed"}
        actual = report_status(path)
        if actual not in status:
            report.fail(f"{name}: gate_status must be one of {sorted(status)}, got {actual or '<missing>'}")


def report_status(path: Path) -> str:
    from bugate_core import gate_status

    return gate_status(path)


def _check_domain_model(report: GateReport, artifact_dir: Path, require_passed: bool) -> set[str]:
    """Optional Stage 1A. Returns declared OBJ-xxx ids for downstream cross-refs."""
    path = artifact_dir / "01a_domain_model.md"
    if not path.exists():
        return set()
    frontmatter, body = split_frontmatter(read_text(path))
    if frontmatter.get("gate") not in {None, "layer1a_domain_model"}:
        report.fail("01a_domain_model.md: frontmatter.gate must be layer1a_domain_model")
    if require_passed:
        report.require_status(path)
    for heading in ("Business Objects", "Invariants"):
        if f"## {heading}" not in body:
            report.fail(f"01a_domain_model.md: missing section ## {heading}")

    object_ids: set[str] = set()
    object_rows = table_dicts(body, "Business Objects")
    if not object_rows:
        report.fail("01a_domain_model.md: Business Objects needs at least one OBJ-xxx row")
    for idx, row in enumerate(object_rows, start=1):
        oid = row.get("object_id", "")
        if OBJECT_RE.fullmatch(oid):
            object_ids.add(oid)
        else:
            report.fail(f"01a_domain_model.md: Business Objects[{idx}] object id must be OBJ-xxx, got {oid or '<empty>'}")

    invariant_rows = table_dicts(body, "Invariants")
    if not invariant_rows:
        report.fail("01a_domain_model.md: Invariants needs at least one INV-xxx row")
    for idx, row in enumerate(invariant_rows, start=1):
        iid = row.get("invariant_id", "")
        if not INVARIANT_RE.fullmatch(iid):
            report.fail(f"01a_domain_model.md: Invariants[{idx}] invariant id must be INV-xxx, got {iid or '<empty>'}")

    if require_passed:
        for idx, row in enumerate(table_dicts(body, "Object Attributes"), start=1):
            ref = row.get("object_id", "")
            if ref and ref not in object_ids:
                report.fail(f"01a_domain_model.md: Object Attributes[{idx}] object id {ref} not declared in Business Objects")
        for idx, row in enumerate(table_dicts(body, "Relationships"), start=1):
            for key in ("from", "to"):
                ref = row.get(key, "")
                if ref.startswith("OBJ-") and ref not in object_ids:
                    report.fail(f"01a_domain_model.md: Relationships[{idx}].{key} {ref} not declared in Business Objects")
        ensure_no_tbd(report, path, body, enabled=True)
    return object_ids


def _check_state_flow(report: GateReport, artifact_dir: Path, object_ids: set[str], require_passed: bool) -> set[str]:
    """Optional Stage 1B. Returns declared TR-xxx ids for downstream cross-refs."""
    path = artifact_dir / "01b_state_flow.md"
    if not path.exists():
        return set()
    frontmatter, body = split_frontmatter(read_text(path))
    if frontmatter.get("gate") not in {None, "layer1b_state_flow"}:
        report.fail("01b_state_flow.md: frontmatter.gate must be layer1b_state_flow")
    if require_passed:
        report.require_status(path)
    for heading in ("Business Flow", "State Catalog", "Transition Table"):
        if f"## {heading}" not in body:
            report.fail(f"01b_state_flow.md: missing section ## {heading}")

    flow_rows = table_dicts(body, "Business Flow")
    if not flow_rows:
        report.fail("01b_state_flow.md: Business Flow needs at least one FLOW-xxx row")
    for idx, row in enumerate(flow_rows, start=1):
        if not FLOW_RE.fullmatch(row.get("step_id", "")):
            report.fail(f"01b_state_flow.md: Business Flow[{idx}] step id must be FLOW-xxx")

    state_ids: set[str] = set()
    state_rows = table_dicts(body, "State Catalog")
    if not state_rows:
        report.fail("01b_state_flow.md: State Catalog needs at least one STATE-xxx row")
    for idx, row in enumerate(state_rows, start=1):
        sid = row.get("state_id", "")
        if STATE_RE.fullmatch(sid):
            state_ids.add(sid)
        else:
            report.fail(f"01b_state_flow.md: State Catalog[{idx}] state id must be STATE-xxx")
        if object_ids and require_passed:
            obj = row.get("object", "")
            if obj.startswith("OBJ-") and obj not in object_ids:
                report.fail(f"01b_state_flow.md: State Catalog[{idx}] object {obj} not declared in 01a")

    transition_ids: set[str] = set()
    transition_rows = table_dicts(body, "Transition Table")
    if not transition_rows:
        report.fail("01b_state_flow.md: Transition Table needs at least one TR-xxx row")
    for idx, row in enumerate(transition_rows, start=1):
        tid = row.get("transition_id", "")
        if TRANSITION_RE.fullmatch(tid):
            transition_ids.add(tid)
        else:
            report.fail(f"01b_state_flow.md: Transition Table[{idx}] transition id must be TR-xxx")
        if require_passed:
            for key in ("from_state", "to_state"):
                ref = row.get(key, "")
                if ref.startswith("STATE-") and ref not in state_ids:
                    report.fail(f"01b_state_flow.md: Transition Table[{idx}].{key} {ref} not declared in State Catalog")
            oracle = row.get("oracle", "")
            if oracle and not ORACLE_RE.fullmatch(oracle):
                report.fail(f"01b_state_flow.md: Transition Table[{idx}] oracle must be O-xxx, got {oracle}")

    if require_passed:
        ensure_no_tbd(report, path, body, enabled=True)
    return transition_ids


def _check_dimension_matrix(
    report: GateReport,
    artifact_dir: Path,
    object_ids: set[str],
    transition_ids: set[str],
    require_passed: bool,
) -> None:
    """Optional Stage 2A test-dimension matrix (nested YAML)."""
    path = artifact_dir / "02a_test_dimension_matrix.yaml"
    if not path.exists():
        return
    text = read_text(path)
    data = parse_nested_yaml(text)
    if not isinstance(data, dict):
        report.fail("02a_test_dimension_matrix.yaml: must parse to a YAML mapping")
        return
    if data.get("gate") not in {None, "layer2a_test_dimension_matrix"}:
        report.fail("02a_test_dimension_matrix.yaml: gate must be layer2a_test_dimension_matrix")
    if require_passed:
        report.require_status(path)

    dimensions = data.get("dimension_matrix")
    if not isinstance(dimensions, list) or not dimensions:
        report.fail("02a_test_dimension_matrix.yaml: dimension_matrix must be a non-empty list")
        return

    candidate_ids: set[str] = set()
    for idx, dim in enumerate(dimensions, start=1):
        loc = f"02a_test_dimension_matrix.yaml: dimension_matrix[{idx}]"
        if not isinstance(dim, dict):
            report.fail(f"{loc} must be a mapping")
            continue
        if not DIMENSION_RE.fullmatch(str(dim.get("id") or "")):
            report.fail(f"{loc}.id must be DIM-xxx")
        if dim.get("category") not in VALID_DIMENSION_CATEGORY:
            report.fail(f"{loc}.category invalid: {dim.get('category')}")
        if dim.get("selected_layer") not in VALID_LAYER:
            report.fail(f"{loc}.selected_layer invalid: {dim.get('selected_layer')}")
        priority = dim.get("priority")
        if priority not in VALID_PRIORITY:
            report.fail(f"{loc}.priority invalid: {priority}")
        prop_refs = _as_str_list(dim.get("proposition_refs"))
        if not prop_refs or not all(PROP_RE.fullmatch(ref) for ref in prop_refs):
            report.fail(f"{loc}.proposition_refs must list P-xxx ids")
        oracle_refs = _as_str_list(dim.get("oracle_refs"))
        if priority in {"P0", "P1"} and (not oracle_refs or not all(ORACLE_RE.fullmatch(ref) for ref in oracle_refs)):
            report.fail(f"{loc}.oracle_refs: P0/P1 dimensions must list O-xxx ids")
        if require_passed:
            for ref in _as_str_list(dim.get("object_refs")):
                if object_ids and ref not in object_ids:
                    report.fail(f"{loc}.object_refs: {ref} not declared in 01a_domain_model.md")
            for ref in _as_str_list(dim.get("transition_refs")):
                if transition_ids and ref not in transition_ids:
                    report.fail(f"{loc}.transition_refs: {ref} not declared in 01b_state_flow.md")
        for case_idx, case in enumerate(dim.get("candidate_cases") or [], start=1):
            if not isinstance(case, dict):
                report.fail(f"{loc}.candidate_cases[{case_idx}] must be a mapping")
                continue
            hint = str(case.get("id_hint") or "")
            if CASE_HINT_RE.fullmatch(hint):
                candidate_ids.add(hint)
            else:
                report.fail(f"{loc}.candidate_cases[{case_idx}].id_hint must be CASE-xxx/ADV-xxx, got {hint or '<empty>'}")
            if require_passed:
                if case.get("side_effect") not in VALID_SIDE_EFFECT:
                    report.fail(f"{loc}.candidate_cases[{case_idx}].side_effect invalid: {case.get('side_effect')}")
                if case.get("data_strategy") not in VALID_DATA_STRATEGY:
                    report.fail(f"{loc}.candidate_cases[{case_idx}].data_strategy invalid: {case.get('data_strategy')}")

    for q_idx, question in enumerate(data.get("open_questions") or [], start=1):
        if isinstance(question, dict) and str(question.get("resolution_needed_before")) in {"layer3", "code"}:
            report.fail(
                f"02a_test_dimension_matrix.yaml: open_questions[{q_idx}] is still blocking; resolve it before Layer 3 / code"
            )

    inventory = artifact_dir / "03_inventory.yaml"
    if candidate_ids and inventory.exists():
        from bugate_core import gate_status

        inventory_ids = {str(case.get("id") or "") for case in parse_inventory_cases(read_text(inventory))}
        if gate_status(inventory) == "passed":
            for missing in sorted(candidate_ids - inventory_ids):
                report.fail(f"02a_test_dimension_matrix.yaml: {missing} is included but missing from passed 03_inventory.yaml")
        elif require_passed:
            report.warn("02a_test_dimension_matrix.yaml: 03_inventory.yaml not passed yet; candidate cases unverified")

    if require_passed:
        ensure_no_tbd(report, path, text, enabled=True)


def _check_multiview(report: GateReport, artifact_dir: Path, require_passed: bool) -> None:
    """Wave 1 multiview is a required per-UC gate (profile-gated via require_multiview)."""
    path = artifact_dir / "00_multiview" / "divergence_report.md"
    if not report.require_file(path):
        return
    if require_passed:
        report.require_status(path)


def check(
    artifact_dir: Path,
    *,
    scope: str,
    require_passed: bool,
    profile: str | None = None,
    require_multiview: bool = False,
) -> GateReport:
    config = load_config(profile=profile or os.environ.get("BUGATE_PROFILE"))
    report = GateReport("bugate_v13", artifact_dir)
    needed = required_precode_artifacts(config) if scope == "pre-code" else ALL_ARTIFACTS
    for name in needed:
        report.require_file(artifact_dir / name)
    _merge(report, check_brief(artifact_dir, require_passed=require_passed))
    _merge(report, check_layer2(artifact_dir, require_passed=require_passed))
    _merge(report, check_inventory(artifact_dir, require_passed=require_passed))
    # Optional Full-SDTD modeling stack (validated only when the files exist).
    object_ids = _check_domain_model(report, artifact_dir, require_passed)
    transition_ids = _check_state_flow(report, artifact_dir, object_ids, require_passed)
    _check_dimension_matrix(report, artifact_dir, object_ids, transition_ids, require_passed)
    if "03a_test_cases.md" in needed:
        _check_readable_cases(report, artifact_dir, require_passed)
    if "03b_adversarial_cases.yaml" in needed:
        _check_adversarial(report, artifact_dir, require_passed)
    # Wave 1 multiview as a per-UC gate, when the profile opts in.
    if require_multiview or as_bool(config.get("require_multiview")):
        _check_multiview(report, artifact_dir, require_passed)
    if scope == "all":
        _check_report(report, artifact_dir, "04_execution_report.md", require_passed)
        _check_report(report, artifact_dir, "05_knowledge_update.md", require_passed)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--scope", choices=["pre-code", "all"], default="pre-code")
    parser.add_argument("--require-passed", action="store_true")
    parser.add_argument("--require-multiview", action="store_true", help="Require a Wave 1 00_multiview/divergence_report.md per UC")
    parser.add_argument("--profile", help="Optional SUT profile config path")
    args = parser.parse_args()
    return check(
        args.artifact_dir,
        scope=args.scope,
        require_passed=args.require_passed,
        profile=args.profile,
        require_multiview=args.require_multiview,
    ).exit()


if __name__ == "__main__":
    raise SystemExit(main())
