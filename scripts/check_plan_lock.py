#!/usr/bin/env python3
"""Optional BUGate plan lock.

If `.bugate/plan.lock` exists, implementation writes are blocked until the lock
is removed by the workflow owner. Core BUGate does not create the lock itself.
"""

from __future__ import annotations

import sys
from pathlib import Path


def find_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "AGENTS.md").exists() and (candidate / ".shared").exists():
            return candidate
    raise SystemExit("BUGate root not found: expected AGENTS.md and .shared")


def main() -> int:
    root = find_root(Path.cwd().resolve())
    lock = root / ".bugate" / "plan.lock"
    if not lock.exists():
        return 0
    sys.stderr.write(f"BUGate plan lock is active: {lock}\n")
    sys.stderr.write("Remove the lock only after the active plan is accepted.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
