#!/usr/bin/env python3
"""BUGate Layer 2 testability gate, SUT-neutral."""

from __future__ import annotations

import argparse
from pathlib import Path

from bugate_core import (
    GateReport,
    ensure_no_tbd,
    oracle_ids,
    proposition_ids,
    read_text,
    split_frontmatter,
)


def check(artifact_dir: Path, *, require_passed: bool = False) -> GateReport:
    path = artifact_dir / "02_testability.md"
    brief = artifact_dir / "01_business_brief.md"
    report = GateReport("layer2_testability", artifact_dir)
    if not report.require_file(path):
        return report
    frontmatter, body = split_frontmatter(read_text(path))
    if require_passed:
        report.require_status(path)
    if frontmatter.get("gate") not in {None, "layer2_testability"}:
        report.fail("02_testability.md: frontmatter.gate must be layer2_testability")
    for heading in ("Layer Decision", "Evidence Plan"):
        if heading not in body:
            report.fail(f"02_testability.md: missing {heading} section")
    if brief.exists():
        brief_body = read_text(brief)
        missing_p = sorted(proposition_ids(brief_body) - proposition_ids(body))
        missing_o = sorted(oracle_ids(brief_body) - oracle_ids(body))
        if missing_p:
            report.fail(f"02_testability.md: missing proposition coverage for {', '.join(missing_p)}")
        if missing_o:
            report.fail(f"02_testability.md: missing oracle evidence mapping for {', '.join(missing_o)}")
    else:
        report.warn("01_business_brief.md not found; cross-layer coverage was not checked")
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
