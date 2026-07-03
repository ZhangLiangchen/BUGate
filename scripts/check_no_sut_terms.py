#!/usr/bin/env python3
"""BUGate de-SUT guard: fail if SUT-specific terms leak into the core tree.

BUGate's value proposition is that the engine, methodology, skill, and adapters
stay SUT-neutral. This guard greps the core tree for high-signal product/identity
terms that must never appear in BUGate core, and exits non-zero on any match. It
is safe to run as a pre-commit hook or in CI, and uses only the standard library.

Generic words (order, chain, wallet as English prose, the neutral `docs/usecases`
default artifact dir, e-commerce `订单` teaching examples) are intentionally NOT
forbidden — only unambiguous SUT tokens are. To allow a deliberate occurrence on
a single line, append a trailing ``# bugate: allow-sut-term`` marker.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bugate_core import find_engine_root, rel

# Unambiguous SUT-specific tokens lifted from the extraction's parent SUT. These
# must never appear in BUGate core. ASCII tokens use word boundaries so generic
# English (e.g. "strongest" contains "tron") does not trip the guard.
FORBIDDEN = [
    r"\bhypervise\b",
    r"\bxblock\b",
    r"\bmarlon\b",
    r"\bxyc2\b",
    r"\bscenario_driven\b",
    r"\bwalletId\b",
    r"\bcrossWithdraw\b",
    r"\bmnemonic\b",
    r"\beip1559\b",
    r"\bswagger\b",
    r"\btapd\b",
    r"\bvault\b",
    r"\btron\b",
    r"钱包",
    r"project:hypervise",
]

# Core tree to scan (paths relative to the repo root).
SCAN_ROOTS = [
    "scripts",
    "bin",
    ".shared/skills",
    "docs",
    "examples",
    ".github",
    "AGENTS.md",
    "CHARTER.md",
    "README.md",
    "INIT.md",
    "INIT.zh-CN.md",
    "CONTRIBUTING.md",
    "bugate.config.yaml",
]

# Never descend into these (state, caches, vcs, vendored memory).
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".memory_bus",
    "memory",
    "progress",
    "memory-service-archive",
}

TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".sh", ".txt", ".cfg", ".ini"}
ALLOW_MARKER = "bugate: allow-sut-term"


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    # Extensionless shell wrappers under bin/ are scanned too.
    return path.parent.name == "bin" and path.suffix == ""


def _iter_files(root: Path):
    for target in SCAN_ROOTS:
        base = root / target
        if base.is_symlink() or not base.exists():
            continue
        if base.is_file():
            yield base
            continue
        for path in base.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if _is_text_file(path):
                yield path


def scan(root: Path, self_path: Path) -> list[str]:
    patterns = [re.compile(term, re.IGNORECASE) for term in FORBIDDEN]
    hits: list[str] = []
    for path in _iter_files(root):
        if path.resolve() == self_path:
            continue  # the guard legitimately lists the forbidden terms
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            for regex in patterns:
                match = regex.search(line)
                if match:
                    hits.append(f"{rel(path, root)}:{lineno}: SUT term {match.group(0)!r}")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="Only print on failure")
    args = parser.parse_args()
    # The guard audits the CORE tree itself, so it anchors on the engine root —
    # never the governed workspace (whose files legitimately name its SUT).
    root = find_engine_root()
    hits = scan(root, Path(__file__).resolve())
    if hits:
        sys.stderr.write("BUGate de-SUT guard FAILED: SUT-specific terms found in core:\n")
        for hit in hits:
            sys.stderr.write(f"  - {hit}\n")
        sys.stderr.write(
            "Move SUT facts into a SUT profile or mounted workspace. To allow a "
            "deliberate occurrence, append '# bugate: allow-sut-term' on that line.\n"
        )
        return 1
    if not args.quiet:
        print("BUGate de-SUT guard: PASS (no SUT-specific terms in core)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
