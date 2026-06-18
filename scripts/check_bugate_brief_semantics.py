#!/usr/bin/env python3
"""BUGate Layer 1 semantic gate, SUT-neutral."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from bugate_core import (
    GateReport,
    ensure_no_tbd,
    load_config,
    oracle_ids,
    proposition_ids,
    read_text,
    split_frontmatter,
    table_dicts,
)

_NOT_VERIFIABLE = ("unverif", "deferred", "unknown", "tbd", "not yet", "no ")


def _verifiability_ratio(body: str) -> tuple[int, int]:
    """Return (verifiable, total) propositions from the Layer 1 table."""
    rows = table_dicts(body, "Propositions")
    total = len(rows)
    verifiable = 0
    for row in rows:
        value = (row.get("verifiability") or "").strip().lower()
        if value and not any(token in value for token in _NOT_VERIFIABLE):
            verifiable += 1
    return verifiable, total


def check(artifact_dir: Path, *, require_passed: bool = False) -> GateReport:
    path = artifact_dir / "01_business_brief.md"
    report = GateReport("layer1_business_brief", artifact_dir)
    if not report.require_file(path):
        return report
    frontmatter, body = split_frontmatter(read_text(path))
    if require_passed:
        report.require_status(path)
    if frontmatter.get("gate") not in {None, "layer1_business_brief"}:
        report.fail("01_business_brief.md: frontmatter.gate must be layer1_business_brief")
    p_ids = proposition_ids(body)
    o_ids = oracle_ids(body)
    if not p_ids:
        report.fail("01_business_brief.md: must define at least one P-xxx proposition")
    if not o_ids:
        report.fail("01_business_brief.md: must define at least one O-xxx business oracle")
    for heading in ("Scope", "Propositions", "Business Oracles"):
        if f"## {heading}" not in body:
            report.fail(f"01_business_brief.md: missing section ## {heading}")
    # Optional verifiability-ratio gate (opt-in via profile config verifiability_min).
    config = load_config(profile=os.environ.get("BUGATE_PROFILE"))
    vmin_raw = config.get("verifiability_min")
    if vmin_raw not in (None, "", []):
        try:
            vmin = float(vmin_raw)
        except (TypeError, ValueError):
            vmin = None
        verifiable, total = _verifiability_ratio(body)
        if vmin is not None and total:
            ratio = verifiable / total
            if ratio < vmin:
                report.fail(f"01_business_brief.md: verifiability ratio {ratio:.2f} < required {vmin:.2f} ({verifiable}/{total} verifiable)")
            elif ratio < 0.80:
                report.warn(f"01_business_brief.md: verifiability ratio {ratio:.2f} below the 0.80 advisory bar ({verifiable}/{total})")
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
