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

# Causes that must be ruled OUT before a failure may be called a SUT defect.
EXCLUSION_CAUSES = ["test_infrastructure", "environment_or_resource"]
# Precedence for the single primary verdict (exclude-first ordering).
PRECEDENCE = ["test_infrastructure", "environment_or_resource", "assertion_failure", "sut_behavior_failure"]


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
    matched = {label for label, pattern in PATTERNS if re.search(pattern, log, re.I)}
    exclusions = {c: ("detected" if c in matched else "clear") for c in EXCLUSION_CAUSES}
    detected_exclusions = [c for c in EXCLUSION_CAUSES if c in matched]
    # A SUT defect is only admissible once infra/env causes are ruled out.
    sut_defect_admissible = (not detected_exclusions) and ("sut_behavior_failure" in matched)
    primary = next((c for c in PRECEDENCE if c in matched), "unknown_failure")

    failures = []
    for label, pattern in PATTERNS:
        if label not in matched:
            continue
        item = {"classification": label, "evidence_pattern": pattern}
        if label == "sut_behavior_failure" and detected_exclusions:
            # Do not let a SUT-defect verdict stand while infra/env are unexcluded.
            item["status"] = "blocked_by_exclusion"
            item["blocked_by"] = detected_exclusions
        failures.append(item)
    if not failures:
        failures.append({"classification": "unknown_failure", "evidence_pattern": "unclassified non-empty failure log"})

    if detected_exclusions and "sut_behavior_failure" in matched:
        next_action = (
            f"exclude {', '.join(detected_exclusions)} first; do NOT record a SUT defect until "
            "infra/environment causes are ruled out, then re-run and re-classify"
        )
    elif sut_defect_admissible:
        next_action = "infra/environment excluded; SUT-behavior defect admissible — confirm against the oracle, then add a named regression case before closure"
    else:
        next_action = "review classification, rerun only if failure is transient, otherwise update SUT profile/artifacts or implementation deliberately"

    return {
        "overall": "failed",
        "exit_code": exit_code,
        "primary_classification": primary,
        "exclusions": exclusions,
        "sut_defect_admissible": sut_defect_admissible,
        "failures": failures,
        "next_action": next_action,
    }


def render_md(result: dict) -> str:
    lines = ["# BUGate Self-Healing Classification", ""]
    lines.append(f"- Overall: {result.get('overall')}")
    lines.append(f"- Exit code: {result.get('exit_code')}")
    if result.get("primary_classification"):
        lines.append(f"- Primary classification: {result.get('primary_classification')}")
    if "sut_defect_admissible" in result:
        lines.append(f"- SUT defect admissible: {result.get('sut_defect_admissible')}")
    lines.append(f"- Next action: {result.get('next_action')}")
    exclusions = result.get("exclusions") or {}
    if exclusions:
        lines.append("")
        lines.append("## Exclusions (must be clear before a SUT-defect verdict)")
        for cause, state in exclusions.items():
            lines.append(f"- {cause}: {state}")
    lines.append("")
    lines.append("## Failures")
    failures = result.get("failures") or []
    if not failures:
        lines.append("- none")
    for item in failures:
        suffix = ""
        if item.get("status") == "blocked_by_exclusion":
            suffix = f" — blocked_by_exclusion: {', '.join(item.get('blocked_by') or [])}"
        lines.append(f"- {item.get('classification')}: `{item.get('evidence_pattern')}`{suffix}")
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
