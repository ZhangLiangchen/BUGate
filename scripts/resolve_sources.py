#!/usr/bin/env python3
"""Resolve a SUT profile's evidence/skill source roots, SUT-neutral.

The profile binds `evidence_sources` (where the SUT's endpoint/interface contracts
live) and `skill_sources` (where SUT-specific skills are staged in the mount). This
CLI turns those bindings into concrete paths so a flow/agent can answer "where is
the contract for this SUT?" by reading the profile instead of guessing a path.

    python3 scripts/resolve_sources.py                 # both kinds, with existence
    python3 scripts/resolve_sources.py --kind evidence # only contract/evidence roots
    python3 scripts/resolve_sources.py --existing      # only roots that exist
    python3 scripts/resolve_sources.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bugate_core import evidence_roots, find_root, load_config, rel, skill_roots

_RESOLVERS = {"evidence": evidence_roots, "skill": skill_roots}


def collect(kind: str, config: dict, root: Path, existing_only: bool) -> list[dict]:
    roots = _RESOLVERS[kind](config, root, existing_only=existing_only)
    return [{"kind": kind, "path": rel(p, root), "exists": p.exists()} for p in roots]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", choices=["evidence", "skill", "both"], default="both")
    parser.add_argument("--existing", action="store_true",
                        help="only roots that exist on disk")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile", default=None,
                        help="explicit profile path (default: bugate.config.yaml pointer)")
    args = parser.parse_args()

    root = find_root()
    config = load_config(root=root, profile=args.profile)
    kinds = ["evidence", "skill"] if args.kind == "both" else [args.kind]
    rows = [row for kind in kinds for row in collect(kind, config, root, args.existing)]

    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("no evidence_sources / skill_sources bound in the active profile")
    else:
        for row in rows:
            mark = "OK  " if row["exists"] else "MISS"
            print(f"{mark} {row['kind']:8} {row['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
