#!/usr/bin/env python3
"""Generate BUGate post-run 04/05 report drafts."""

from __future__ import annotations

import argparse
from pathlib import Path

from bugate_core import load_json, read_text, write_text


def summarize_log(path: Path) -> str:
    if not path.exists():
        return "log_not_found"
    text = read_text(path)
    if "FAILED" in text or "AssertionError" in text or "Traceback" in text:
        return "failed"
    if "passed" in text.lower() or "PASS" in text:
        return "passed"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--pytest-log", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--env", default="profile-owned")
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--self-healing-json")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    log_path = Path(args.pytest_log)
    status = summarize_log(log_path)
    healing = {}
    if args.self_healing_json and Path(args.self_healing_json).exists():
        healing = load_json(Path(args.self_healing_json))
    report04 = "\n".join(
        [
            "---",
            "gate: execution_report",
            "gate_status: draft",
            "sut_profile: TBD",
            "---",
            "",
            "# Execution Report",
            "",
            f"- Command: `{args.command}`",
            f"- Environment: {args.env}",
            f"- Exit code: {args.exit_code}",
            f"- Log status: {status}",
            f"- Self-healing classification: {healing.get('overall', 'not_run')}",
            "",
            "## Regression Cases",
            "",
            "| defect / incident id | named regression case | proposition / oracle | status |",
            "|---|---|---|---|",
            "| none | none | none | n/a |",
            "",
            "## Evidence Links",
            "",
            f"- Log: `{log_path}`",
            "",
        ]
    )
    report05 = "\n".join(
        [
            "---",
            "gate: knowledge_update",
            "gate_status: draft",
            "sut_profile: TBD",
            "---",
            "",
            "# Knowledge Update",
            "",
            "## Reusable Findings",
            "",
            "- TBD after human review.",
            "",
            "## SUT Profile Updates",
            "",
            "- TBD if failure classification points to profile gaps.",
            "",
            "## Regression Cases",
            "",
            "| defect / incident id | named regression case | proposition / oracle | tag |",
            "|---|---|---|---|",
            "| none | none | none | none |",
            "",
            "## BUGate Core Updates",
            "",
            "- Keep SUT-specific learnings out of core unless they pass the promotion rule.",
            "",
        ]
    )
    if args.write:
        write_text(args.artifact_dir / "04_execution_report.md", report04)
        write_text(args.artifact_dir / "05_knowledge_update.md", report05)
        print(f"written {args.artifact_dir / '04_execution_report.md'}")
        print(f"written {args.artifact_dir / '05_knowledge_update.md'}")
    else:
        print(report04)
        print(report05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
