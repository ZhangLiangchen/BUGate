#!/usr/bin/env python3
"""BUGate Layer 1 semantic gate, SUT-neutral."""

from __future__ import annotations

import argparse
import os
import re
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
_EVIDENCE_LABELS = {"fact", "inferred", "unknown"}


def _check_evidence_label(report: GateReport, section: str, idx: int, row: dict) -> None:
    if "evidence_label" not in row:
        return
    value = (row.get("evidence_label") or "").strip().lower()
    if not value:
        report.fail(f"01_business_brief.md: {section}[{idx}] evidence_label must be set")
    elif value not in _EVIDENCE_LABELS:
        report.fail(
            f"01_business_brief.md: {section}[{idx}] evidence_label must be one of fact/inferred/unknown, "
            f"got {row.get('evidence_label')!r}"
        )


def _check_brief_fields(report: GateReport, body: str) -> None:
    """Accepted-brief field gates: source non-empty, evidence_label enum, unknown→Q."""
    for idx, row in enumerate(table_dicts(body, "Propositions"), start=1):
        if "source" in row and not (row.get("source") or "").strip():
            report.fail(f"01_business_brief.md: Propositions[{idx}] source must be non-empty when accepted")
        _check_evidence_label(report, "Propositions", idx, row)
    for idx, row in enumerate(table_dicts(body, "Business Oracles"), start=1):
        _check_evidence_label(report, "Business Oracles", idx, row)
    for idx, row in enumerate(table_dicts(body, "Clarification Gate"), start=1):
        status = (row.get("status") or "").strip().lower()
        if "unknown" in status and not re.search(r"\bQ-\d", row.get("open_question") or ""):
            report.fail(
                f"01_business_brief.md: Clarification Gate[{idx}] is 'unknown' but has no bound Q-xxx open question"
            )


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
    if require_passed:
        _check_brief_fields(report, body)
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
