#!/usr/bin/env python3
"""Generic BUGate oracle-falsification harness.

Core mode records evidence files and reports that SUT-profile assertion runners
are required for a real mutation score.
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

from bugate_core import dump_json, write_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="")
    parser.add_argument("--json-output", default="oracle_falsification_result.json")
    parser.add_argument("--md-output", default="oracle_falsification_result.md")
    parser.add_argument("--profile")
    args = parser.parse_args()
    files = sorted(glob.glob(args.evidence)) if args.evidence else []
    result = {
        "status": "profile_required",
        "evidence_files": files,
        "mutation_score": None,
        "message": "BUGate core cannot falsify business oracles without a SUT profile assertion runner.",
    }
    dump_json(Path(args.json_output), result)
    write_text(
        Path(args.md_output),
        "# Oracle Falsification Result\n\n"
        "- Status: profile_required\n"
        f"- Evidence files: {len(files)}\n"
        "- Mutation score: not_applicable_without_profile\n",
    )
    print(f"written {args.json_output}")
    print(f"written {args.md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
