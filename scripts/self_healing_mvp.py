#!/usr/bin/env python3
"""Generic BUGate failure classifier and repair-plan writer."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from bugate_core import dump_json, read_text, write_text


PATTERNS = [
    ("assertion_failure", r"AssertionError|assert .*failed|E\s+assert"),
    ("test_infrastructure", r"fixture .*not found|ModuleNotFoundError|ImportError|SyntaxError"),
    ("environment_or_resource", r"timeout|connection|network|resource|credential|permission denied"),
    ("sut_behavior_failure", r"expected .* got|status code|business|oracle|mismatch"),
]


def classify(log: str, exit_code: int | None = None) -> dict:
    if not log.strip():
        return {
            "overall": "no_log",
            "exit_code": exit_code,
            "failures": [],
            "next_action": "provide pytest or runner log before repair planning",
        }
    if exit_code == 0 and not re.search(r"\bFAILED\b|AssertionError|Traceback", log):
        return {
            "overall": "passed",
            "exit_code": exit_code,
            "failures": [],
            "next_action": "record execution report and keep assertions unchanged",
        }
    failures = []
    for label, pattern in PATTERNS:
        if re.search(pattern, log, re.I):
            failures.append({"classification": label, "evidence_pattern": pattern})
    if not failures:
        failures.append({"classification": "unknown_failure", "evidence_pattern": "unclassified non-empty failure log"})
    return {
        "overall": "failed",
        "exit_code": exit_code,
        "failures": failures,
        "next_action": "review classification, rerun only if failure is transient, otherwise update SUT profile/artifacts or implementation deliberately",
    }


def render_md(result: dict) -> str:
    lines = ["# BUGate Self-Healing Classification", ""]
    lines.append(f"- Overall: {result.get('overall')}")
    lines.append(f"- Exit code: {result.get('exit_code')}")
    lines.append(f"- Next action: {result.get('next_action')}")
    lines.append("")
    lines.append("## Failures")
    failures = result.get("failures") or []
    if not failures:
        lines.append("- none")
    for item in failures:
        lines.append(f"- {item.get('classification')}: `{item.get('evidence_pattern')}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pytest-log", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--md-output", required=True)
    parser.add_argument("--repair-plan-output", required=True)
    parser.add_argument("--exit-code", type=int, default=None)
    args = parser.parse_args()
    log_path = Path(args.pytest_log)
    log = read_text(log_path) if log_path.exists() else ""
    result = classify(log, args.exit_code)
    dump_json(Path(args.json_output), result)
    write_text(Path(args.md_output), render_md(result))
    write_text(
        Path(args.repair_plan_output),
        "# BUGate Repair Plan\n\n"
        f"- Classification: {result['overall']}\n"
        f"- Next action: {result['next_action']}\n"
        "- Boundary: this planner does not edit tests automatically.\n",
    )
    print(f"written {args.json_output}")
    print(f"written {args.md_output}")
    print(f"written {args.repair_plan_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
