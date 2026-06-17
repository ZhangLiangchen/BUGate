#!/usr/bin/env python3
"""BUGate v1.3 artifact stack checker, SUT-neutral."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from bugate_core import (
    ALL_ARTIFACTS,
    GateReport,
    load_config,
    parse_inventory_cases,
    read_text,
    required_precode_artifacts,
)
from check_bugate_brief_semantics import check as check_brief
from check_bugate_inventory_semantics import check as check_inventory
from check_bugate_layer2_semantics import check as check_layer2


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


def check(artifact_dir: Path, *, scope: str, require_passed: bool, profile: str | None = None) -> GateReport:
    config = load_config(profile=profile)
    report = GateReport("bugate_v13", artifact_dir)
    needed = required_precode_artifacts(config) if scope == "pre-code" else ALL_ARTIFACTS
    for name in needed:
        report.require_file(artifact_dir / name)
    _merge(report, check_brief(artifact_dir, require_passed=require_passed))
    _merge(report, check_layer2(artifact_dir, require_passed=require_passed))
    _merge(report, check_inventory(artifact_dir, require_passed=require_passed))
    if "03a_test_cases.md" in needed:
        _check_readable_cases(report, artifact_dir, require_passed)
    if "03b_adversarial_cases.yaml" in needed:
        _check_adversarial(report, artifact_dir, require_passed)
    if scope == "all":
        _check_report(report, artifact_dir, "04_execution_report.md", require_passed)
        _check_report(report, artifact_dir, "05_knowledge_update.md", require_passed)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--scope", choices=["pre-code", "all"], default="pre-code")
    parser.add_argument("--require-passed", action="store_true")
    parser.add_argument("--profile", help="Optional SUT profile config path")
    args = parser.parse_args()
    return check(args.artifact_dir, scope=args.scope, require_passed=args.require_passed, profile=args.profile).exit()


if __name__ == "__main__":
    raise SystemExit(main())
