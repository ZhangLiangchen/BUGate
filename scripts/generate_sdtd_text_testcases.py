#!/usr/bin/env python3
"""Generate human-readable BUGate test cases from 03_inventory.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path

from bugate_core import parse_inventory_cases, read_text, write_text


def render(artifact_dir: Path) -> str:
    inventory = artifact_dir / "03_inventory.yaml"
    cases = parse_inventory_cases(read_text(inventory)) if inventory.exists() else []
    lines = [
        "---",
        "gate: readable_test_cases",
        "gate_status: pending",
        "sut_profile: TBD",
        "---",
        "",
        "# Test Cases",
        "",
    ]
    if not cases:
        lines += ["No cases found in `03_inventory.yaml`.", ""]
        return "\n".join(lines)
    for case in cases:
        cid = case.get("id", "CASE-UNKNOWN")
        lines += [
            f"## {cid}",
            "",
            f"- Intent: {case.get('intent', 'TBD')}",
            f"- Layer: {case.get('layer_decision', 'TBD')}",
            f"- Preconditions: {', '.join(case.get('preconditions') or []) or 'TBD'}",
            f"- Action: execute the SUT-profile-owned implementation target `{case.get('implementation_target', 'TBD')}`",
            f"- Expected observations: {', '.join(case.get('expected_observations') or []) or 'TBD'}",
            f"- Proposition refs: {', '.join(case.get('proposition_refs') or []) or 'TBD'}",
            f"- Oracle refs: {', '.join(case.get('oracle_refs') or []) or 'TBD'}",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    output = render(args.artifact_dir)
    if args.write:
        path = args.artifact_dir / "03a_test_cases.md"
        write_text(path, output)
        print(f"written {path}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
