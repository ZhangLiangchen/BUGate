#!/usr/bin/env python3
"""Initialize and check BUGate Wave 1 multi-view artifacts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bugate_core import as_bool, load_config, read_text, write_text
from role_governance import GovernanceResult, preflight


def precode_preflight(artifact_dir: Path) -> GovernanceResult:
    """Run the shared local role check before this mutator creates anything."""

    result = preflight(artifact_dir, "pre_code", require_acceptance=False)
    for warning in result.warnings:
        print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
    if not result.allowed:
        print("BUGate role governance BLOCKED (pre_code):", file=sys.stderr)
        for error in result.errors or ["role preflight failed"]:
            print(f"  - {error}", file=sys.stderr)
    return result


def multiview_dir(artifact_dir: Path) -> Path:
    return artifact_dir / "00_multiview"


def bridge_failures(out: Path, label: str) -> int:
    """When the profile opts in, archived schema-rejected peer views block the gate."""
    if not as_bool(load_config(profile=os.environ.get("BUGATE_PROFILE")).get("reject_on_bridge_failures")):
        return 0
    fdir = out / "cli_bridge_failures"
    failures = sorted(fdir.glob("*")) if fdir.exists() else []
    for f in failures:
        print(f"FAIL: archived bridge failure {label}/cli_bridge_failures/{f.name}")
    return 1 if failures else 0


def init(artifact_dir: Path, focus: str) -> int:
    if not precode_preflight(artifact_dir).allowed:
        return 2
    out = multiview_dir(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    prompt = "\n".join(
        [
            "# BUGate Multi-View Prompt Card",
            "",
            f"- Focus: {focus}",
            "- Read the active SUT profile and Layer 1 draft.",
            "- Extract business propositions, oracles, gaps, and risks independently.",
            "- Do not modify implementation code.",
            "",
        ]
    )
    write_text(out / "prompt_card.md", prompt)
    for name in ("codex_view.md", "claude_view.md"):
        path = out / name
        if not path.exists():
            write_text(
                path,
                "---\n"
                f"gate: multiview_{name.removesuffix('_view.md')}\n"
                "gate_status: pending\n"
                "requested_model_class: strongest_available\n"
                "requested_reasoning_effort: maximum\n"
                "---\n\n"
                f"# {name}\n\nPending independent review.\n",
            )
    div = out / "divergence_report.md"
    if not div.exists():
        write_text(
            div,
            "---\n"
            "gate: multiview_divergence\n"
            "gate_status: pending\n"
            "layer1_update_required: unknown\n"
            "layer1_updated: unknown\n"
            "---\n\n# Divergence Report\n\nPending synthesis.\n",
        )
    print(f"initialized {out}")
    return 0


def status(artifact_dir: Path) -> int:
    out = multiview_dir(artifact_dir)
    names = ["prompt_card.md", "codex_view.md", "claude_view.md", "divergence_report.md"]
    for name in names:
        print(f"{name}: {'present' if (out / name).exists() else 'missing'}")
    return 0


def check(artifact_dir: Path) -> int:
    out = multiview_dir(artifact_dir)
    failures = [name for name in ["codex_view.md", "claude_view.md", "divergence_report.md"] if not (out / name).exists()]
    if failures:
        for name in failures:
            print(f"FAIL: missing 00_multiview/{name}")
        return 1
    report = read_text(out / "divergence_report.md")
    if "gate_status: passed" not in report:
        print("FAIL: divergence_report.md gate_status must be passed")
        return 1
    if bridge_failures(out, "00_multiview"):
        return 1
    print("PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("artifact_dir", type=Path)
    p_init.add_argument("--focus", default="BUGate requirement understanding")
    p_status = sub.add_parser("status")
    p_status.add_argument("artifact_dir", type=Path)
    p_check = sub.add_parser("check")
    p_check.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    if args.cmd == "init":
        return init(args.artifact_dir, args.focus)
    if args.cmd == "status":
        return status(args.artifact_dir)
    return check(args.artifact_dir)


if __name__ == "__main__":
    raise SystemExit(main())
