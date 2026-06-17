#!/usr/bin/env python3
"""Generate a generic BUGate assertion coverage matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bugate_core import parse_inventory_cases, read_text, write_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutation-result")
    parser.add_argument("--artifact-root", default=".")
    parser.add_argument("--output", default="assertion_coverage_matrix.md")
    args = parser.parse_args()
    root = Path(args.artifact_root)
    cases = []
    for inventory in root.glob("**/03_inventory.yaml"):
        cases.extend(parse_inventory_cases(read_text(inventory)))
    mutation_status = "not_provided"
    if args.mutation_result and Path(args.mutation_result).exists():
        if Path(args.mutation_result).suffix == ".json":
            mutation_status = json.loads(read_text(Path(args.mutation_result))).get("status", "unknown")
        else:
            mutation_status = "provided"
    lines = [
        "# Assertion Coverage Matrix",
        "",
        f"- Mutation/falsification status: {mutation_status}",
        "",
        "| Case | Propositions | Oracles | Implementation target |",
        "|---|---|---|---|",
    ]
    for case in cases:
        lines.append(
            f"| {case.get('id', '')} | {', '.join(case.get('proposition_refs') or [])} | "
            f"{', '.join(case.get('oracle_refs') or [])} | {case.get('implementation_target', '')} |"
        )
    if not cases:
        lines.append("| none | none | none | none |")
    write_text(Path(args.output), "\n".join(lines) + "\n")
    print(f"written {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
