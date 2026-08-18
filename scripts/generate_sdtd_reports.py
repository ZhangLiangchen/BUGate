#!/usr/bin/env python3
"""Generate BUGate post-run 04/05 report drafts.

The two reports are rendered from a *section table* rather than from a single
hardcoded string.  The v0.4.4 renderer had no slot for anything, so recording a
failure attribution or a repair attempt meant editing the literal.  A section
list can grow without the base report changing.

Zero behavior change is not an aspiration here, it is pinned: with
``self_healing`` absent or ``off`` the rendered bytes must equal
``tests/fixtures/golden/04_execution_report.v0.4.4.md`` and its 05 counterpart
exactly, and ``tests/test_self_healing_governance.py`` asserts that byte for
byte.  Any edit to the base sections below will fail that test.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from bugate_core import load_json, read_text, write_text
from role_governance import preflight


def _postrun_write_allowed(artifact_dir: Path) -> bool:
    result = preflight(artifact_dir, "post_run", require_acceptance=True)
    for warning in result.warnings:
        print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
    if result.allowed:
        return True
    print("BUGate role governance BLOCKED (post_run):", file=sys.stderr)
    for error in result.errors or ["role preflight failed"]:
        print(f"  - {error}", file=sys.stderr)
    return False


def summarize_log(path: Path) -> str:
    if not path.exists():
        return "log_not_found"
    text = read_text(path)
    if "FAILED" in text or "AssertionError" in text or "Traceback" in text:
        return "failed"
    if "passed" in text.lower() or "PASS" in text:
        return "passed"
    return "unknown"


# --------------------------------------------------------------------------
# Data-driven rendering
#
# A section is (heading, body_lines).  ``heading`` of ``None`` means the lines
# belong to the document preamble and are emitted without a "## " header.  The
# renderer joins with a trailing blank line after each block, which is what
# reproduces the v0.4.4 layout exactly.
# --------------------------------------------------------------------------

Section = tuple[str | None, list[str]]


def render_report(front_matter: list[str], title: str, sections: list[Section]) -> str:
    lines: list[str] = ["---", *front_matter, "---", "", f"# {title}", ""]
    for heading, body in sections:
        if heading is not None:
            lines.append(f"## {heading}")
            lines.append("")
        lines.extend(body)
        lines.append("")
    return "\n".join(lines)


def base_04_sections(
    *,
    command: str,
    env: str,
    exit_code: int,
    log_status: str,
    healing_overall: str,
    log_path: Path,
) -> list[Section]:
    return [
        (
            None,
            [
                f"- Command: `{command}`",
                f"- Environment: {env}",
                f"- Exit code: {exit_code}",
                f"- Log status: {log_status}",
                f"- Self-healing classification: {healing_overall}",
            ],
        ),
        (
            "Regression Cases",
            [
                "| defect / incident id | named regression case | proposition / oracle | status |",
                "|---|---|---|---|",
                "| none | none | none | n/a |",
            ],
        ),
        ("Evidence Links", [f"- Log: `{log_path}`"]),
    ]


def base_05_sections() -> list[Section]:
    return [
        ("Reusable Findings", ["- TBD after human review."]),
        (
            "SUT Profile Updates",
            ["- TBD if failure classification points to profile gaps."],
        ),
        (
            "Regression Cases",
            [
                "| defect / incident id | named regression case | proposition / oracle | tag |",
                "|---|---|---|---|",
                "| none | none | none | none |",
            ],
        ),
        (
            "BUGate Core Updates",
            [
                "- Keep SUT-specific learnings out of core unless they pass the promotion rule.",
            ],
        ),
    ]


# --------------------------------------------------------------------------
# Self-healing sections (rendered only when the schema is present)
# --------------------------------------------------------------------------


def _sidecar_entries(artifact_dir: Path) -> list[dict[str, Any]]:
    chain = artifact_dir / "00_self_healing" / "chain.json"
    if not chain.exists():
        return []
    try:
        data = load_json(chain)
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [item for item in entries or [] if isinstance(item, dict)]


def _receipt_payload(artifact_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path = artifact_dir / "00_self_healing" / str(entry.get("receipt") or "")
    if not path.exists():
        return {}
    try:
        receipt = load_json(path)
    except (OSError, ValueError):
        return {}
    payload = receipt.get("payload") if isinstance(receipt, dict) else None
    return payload if isinstance(payload, dict) else {}


def _bullets(pairs: list[tuple[str, Any]]) -> list[str]:
    return [f"- {label}: {value}" for label, value in pairs]


def self_healing_04_sections(healing: dict[str, Any], artifact_dir: Path) -> list[Section]:
    """The seven post-run sections a governed repair attempt has to show."""

    entries = _sidecar_entries(artifact_dir)
    reviews = [entry for entry in entries if entry.get("event") == "independent_review"]
    closes = [entry for entry in entries if entry.get("event") == "attempt_closed"]
    handoffs = [entry for entry in entries if entry.get("event") == "healer_handoff"]

    triage_rows = _bullets(
        [
            ("Schema", healing.get("schema_version")),
            ("Attempt", healing.get("attempt_id")),
            ("Original command", f"`{healing.get('original_command') or 'not_recorded'}`"),
            ("Original exit code", healing.get("original_exit_code")),
            ("Original log sha256", healing.get("original_log_sha256")),
            ("Failure owner", healing.get("failure_owner")),
            ("Failure subtype", healing.get("failure_subtype")),
            ("Confidence", healing.get("confidence")),
            ("Target layer reached", healing.get("target_layer_reached")),
            ("Oracle refs", ", ".join(healing.get("oracle_refs") or []) or "none"),
            ("Healing eligible", healing.get("healing_eligible")),
            (
                "Blocking reasons",
                ", ".join(healing.get("blocking_reasons") or []) or "none",
            ),
        ]
    )

    exclusions = ["| exclusion | state |", "|---|---|"]
    for name in healing.get("exclusions_checked") or []:
        detected = name in (healing.get("matched_rules") or {})
        exclusions.append(f"| {name} | {'detected' if detected else 'clear'} |")
    if len(exclusions) == 2:
        exclusions.append("| none | n/a |")

    attempts = ["| sequence | event | state | attempt |", "|---|---|---|---|"]
    for entry in entries:
        attempts.append(
            f"| {entry.get('sequence')} | {entry.get('event')} | "
            f"{entry.get('state')} | {entry.get('attempt_id')} |"
        )
    if len(attempts) == 2:
        attempts.append("| none | none | none | none |")

    if reviews:
        payload = _receipt_payload(artifact_dir, reviews[-1])
        findings = payload.get("structural_findings") or []
        review_lines = _bullets(
            [
                ("Outcome", payload.get("outcome")),
                ("Reviewer verdict", payload.get("reviewer_verdict")),
                ("Reviewer runtime", payload.get("reviewer_runtime") or "unrecorded"),
                ("Structural findings", len(findings)),
            ]
        )
        for finding in findings:
            review_lines.append(
                f"  - {finding.get('code')}: {finding.get('path')} — {finding.get('detail')}"
            )
    else:
        review_lines = ["- No independent review has been published for this UC."]

    if handoffs:
        payload = _receipt_payload(artifact_dir, handoffs[-1])
        verification = payload.get("verification") or {}
        verification_lines = _bullets(
            [
                (
                    "Failure reproduced before the candidate",
                    verification.get("before_reproduced_failure"),
                ),
                ("Verification passed after the candidate", verification.get("after_passed")),
                ("Commands", ", ".join(verification.get("commands") or []) or "none"),
                ("Files", ", ".join(payload.get("files") or []) or "none"),
            ]
        )
    else:
        verification_lines = ["- No verified repair candidate was published."]

    if reviews:
        payload = _receipt_payload(artifact_dir, reviews[-1])
        falsification = payload.get("falsification") or {}
        falsification_lines = _bullets(
            [
                ("Status", falsification.get("status") or "not_run"),
                ("Score", falsification.get("score")),
                ("Threshold", falsification.get("threshold")),
            ]
        )
    else:
        falsification_lines = [
            "- Not run: falsification is required before any healing_verified outcome."
        ]

    if closes:
        payload = _receipt_payload(artifact_dir, closes[-1])
        final_lines = _bullets(
            [
                ("Final state", payload.get("final_state")),
                ("Applied to the workspace", payload.get("applied")),
                ("Approved by", payload.get("approved_by") or "not_applicable"),
                ("Mode", payload.get("mode")),
            ]
        )
        rollback_lines = [
            "- The workspace was written only after an independent review verified the candidate."
            if payload.get("applied")
            else "- Nothing was written back; the original failure and workspace bytes stand."
        ]
    else:
        final_lines = [f"- Recommended action: {healing.get('recommended_action') or 'none'}"]
        rollback_lines = ["- Nothing was written back; the original failure stands."]

    return [
        ("Failure Triage", triage_rows),
        ("Exclusions", exclusions),
        ("Healing Attempts", attempts),
        ("Independent Review", review_lines),
        ("Verification Runs", verification_lines),
        ("Falsification", falsification_lines),
        ("Final Outcome", final_lines),
        ("Rollback Status", rollback_lines),
    ]


def self_healing_05_sections(healing: dict[str, Any]) -> list[Section]:
    owner = str(healing.get("failure_owner") or "")
    subtype = str(healing.get("failure_subtype") or "")
    findings = [
        f"- The {subtype} attribution came from anchored evidence, not a substring match.",
        "- Promote a reusable rule only after it holds across more than this one UC.",
    ]
    profile = ["- TBD if the attribution points at a profile gap."]
    if owner == "environment":
        profile = [
            "- The environment owned this failure; record the resource precondition in the profile.",
        ]
    elif owner == "test_asset":
        profile = [
            "- Confirm `self_healing.allowed_write_regex` still names exactly the assets this repair touched.",
        ]
    elif owner == "sut":
        profile = ["- No profile change: keep the failing assertion and file the defect."]
    return [
        ("Reusable Test-Asset Findings", findings),
        ("Profile Improvement Candidates", profile),
        (
            "Promotion Candidates",
            [
                "- A finding is promotable only once it is SUT-neutral and reproduced elsewhere.",
            ],
        ),
        (
            "Residual Risk",
            [
                f"- Attribution confidence: {healing.get('confidence') or 'unknown'}.",
                "- A medium- or low-confidence attribution must not be treated as settled.",
            ],
        ),
    ]


def build_reports(
    artifact_dir: Path,
    *,
    command: str,
    env: str,
    exit_code: int,
    log_path: Path,
    healing: dict[str, Any],
) -> tuple[str, str]:
    log_status = summarize_log(log_path)
    sections_04 = base_04_sections(
        command=command,
        env=env,
        exit_code=exit_code,
        log_status=log_status,
        healing_overall=str(healing.get("overall", "not_run")),
        log_path=log_path,
    )
    sections_05 = base_05_sections()
    # The appended sections exist only when the triage schema does, and the
    # triage schema exists only when self_healing.mode != off.  That is the whole
    # gate: an opted-out repository renders the v0.4.4 bytes.
    if healing.get("schema_version"):
        sections_04.extend(self_healing_04_sections(healing, artifact_dir))
        sections_05.extend(self_healing_05_sections(healing))
    front_04 = ["gate: execution_report", "gate_status: draft", "sut_profile: TBD"]
    front_05 = ["gate: knowledge_update", "gate_status: draft", "sut_profile: TBD"]
    return (
        render_report(front_04, "Execution Report", sections_04),
        render_report(front_05, "Knowledge Update", sections_05),
    )


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
    if args.write and not _postrun_write_allowed(args.artifact_dir):
        return 2
    log_path = Path(args.pytest_log)
    healing: dict[str, Any] = {}
    if args.self_healing_json and Path(args.self_healing_json).exists():
        loaded = load_json(Path(args.self_healing_json))
        if isinstance(loaded, dict):
            healing = loaded
    report04, report05 = build_reports(
        args.artifact_dir,
        command=args.command,
        env=args.env,
        exit_code=args.exit_code,
        log_path=log_path,
        healing=healing,
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
