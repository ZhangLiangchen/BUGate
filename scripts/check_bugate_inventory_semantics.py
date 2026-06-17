#!/usr/bin/env python3
"""BUGate Layer 3 inventory gate, SUT-neutral."""

from __future__ import annotations

import argparse
from pathlib import Path

from bugate_core import (
    GateReport,
    ensure_no_tbd,
    oracle_ids,
    parse_inventory_cases,
    proposition_ids,
    read_text,
)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def check(artifact_dir: Path, *, require_passed: bool = False) -> GateReport:
    path = artifact_dir / "03_inventory.yaml"
    report = GateReport("layer3_inventory", artifact_dir)
    if not report.require_file(path):
        return report
    body = read_text(path)
    if require_passed:
        report.require_status(path)
    cases = parse_inventory_cases(body)
    if not cases:
        report.fail("03_inventory.yaml: must define at least one case under cases:")
    seen_ids: set[str] = set()
    covered_p: set[str] = set()
    covered_o: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        loc = f"cases[{idx}]"
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            report.fail(f"{loc}: missing id")
        elif case_id in seen_ids:
            report.fail(f"{loc}.id: duplicate {case_id}")
        seen_ids.add(case_id)
        if not str(case.get("intent") or "").strip():
            report.fail(f"{loc}: missing intent")
        p_refs = _as_list(case.get("proposition_refs"))
        o_refs = _as_list(case.get("oracle_refs"))
        if not p_refs:
            report.fail(f"{loc}: missing proposition_refs")
        if not o_refs:
            report.fail(f"{loc}: missing oracle_refs")
        covered_p.update(ref for ref in p_refs if ref.startswith("P-"))
        covered_o.update(ref for ref in o_refs if ref.startswith("O-"))
        if not str(case.get("layer_decision") or "").strip():
            report.fail(f"{loc}: missing layer_decision")
        if not str(case.get("implementation_target") or "").strip():
            report.warn(f"{loc}: implementation_target is empty; SUT profile must provide it before code generation")
    brief = artifact_dir / "01_business_brief.md"
    if brief.exists():
        brief_body = read_text(brief)
        missing_p = sorted(proposition_ids(brief_body) - covered_p)
        missing_o = sorted(oracle_ids(brief_body) - covered_o)
        if missing_p:
            report.fail(f"03_inventory.yaml: missing reverse proposition coverage for {', '.join(missing_p)}")
        if missing_o:
            report.fail(f"03_inventory.yaml: missing oracle coverage for {', '.join(missing_o)}")
    else:
        report.warn("01_business_brief.md not found; reverse coverage was not checked")
    ensure_no_tbd(report, path, body, enabled=require_passed)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--require-passed", action="store_true")
    args = parser.parse_args()
    return check(args.artifact_dir, require_passed=args.require_passed).exit()


if __name__ == "__main__":
    raise SystemExit(main())
