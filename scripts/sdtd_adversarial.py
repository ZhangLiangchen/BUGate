#!/usr/bin/env python3
"""Initialize and check BUGate adversarial review artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from bugate_core import as_bool, load_config, read_text, write_text


def out_dir(artifact_dir: Path) -> Path:
    return artifact_dir / "00_adversarial"


def bridge_failures(out: Path) -> int:
    """When the profile opts in, archived schema-rejected peer views block the gate."""
    if not as_bool(load_config(profile=os.environ.get("BUGATE_PROFILE")).get("reject_on_bridge_failures")):
        return 0
    fdir = out / "cli_bridge_failures"
    failures = sorted(fdir.glob("*")) if fdir.exists() else []
    for f in failures:
        print(f"FAIL: archived bridge failure 00_adversarial/cli_bridge_failures/{f.name}")
    return 1 if failures else 0


def init(artifact_dir: Path, focus: str) -> None:
    out = out_dir(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_text(
        out / "prompt_card.md",
        "# BUGate Adversarial Prompt Card\n\n"
        f"- Focus: {focus}\n"
        "- Attack weak oracles, missing negative paths, ambiguous wording, and fake-green risk.\n",
    )
    print(f"initialized {out}")


def check(artifact_dir: Path) -> int:
    path = artifact_dir / "03b_adversarial_cases.yaml"
    if not path.exists():
        print("FAIL: missing 03b_adversarial_cases.yaml")
        return 1
    body = read_text(path)
    if "gate_status: passed" not in body:
        print("FAIL: 03b_adversarial_cases.yaml gate_status must be passed")
        return 1
    if "adversarial_cases:" not in body:
        print("FAIL: missing adversarial_cases")
        return 1
    if bridge_failures(out_dir(artifact_dir)):
        return 1
    print("PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("artifact_dir", type=Path)
    p_init.add_argument("--focus", default="BUGate adversarial review")
    p_check = sub.add_parser("check")
    p_check.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    if args.cmd == "init":
        init(args.artifact_dir, args.focus)
        return 0
    return check(args.artifact_dir)


if __name__ == "__main__":
    raise SystemExit(main())
