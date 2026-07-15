#!/usr/bin/env python3
"""Optional BUGate plan lock.

If `plan.lock` exists at the ENGINE root — the directory holding this script's
parent, i.e. the vendored kit dir in imported mode (any --vendor-dir name) or
the core checkout root — implementation writes are blocked until the lock is
removed by the workflow owner. Core BUGate does not create the lock itself.
Resolving via the script's own location (not a hardcoded `.bugate/`) keeps the
lock working under custom vendor dirs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from bugate_core import find_root


def main() -> int:
    find_root(Path.cwd().resolve())  # fail loud outside a governed workspace
    lock = Path(__file__).resolve().parent.parent / "plan.lock"
    if not lock.exists():
        return 0
    sys.stderr.write(f"BUGate plan lock is active: {lock}\n")
    sys.stderr.write("Remove the lock only after the active plan is accepted.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
