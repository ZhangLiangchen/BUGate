#!/usr/bin/env python3
"""Generic BUGate failure classifier and repair-plan writer.

Two classifiers live behind this entry point and they never mix:

``self_healing.mode: off`` (the default, and every pre-v0.4.5 repository)
    runs :func:`classify` exactly as v0.4.4 did.  The output is byte-identical,
    pinned by ``tests/fixtures/golden/self_healing.v0.4.4.json``.

``self_healing.mode != off``
    additionally appends the ``bugate.failure-triage/v1`` fields from
    :mod:`failure_triage`, which discriminates *who owns* the failure with
    anchored rules instead of the legacy substring patterns.

The legacy ``PATTERNS`` table below is deliberately left as it is.  Rewriting it
in place would change the ``off`` output and break the zero-behavior-change
contract, so the improved rules live in their own module and are gated on mode.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from bugate_core import dump_json, find_root, load_config, read_text, write_text
from role_governance import preflight


def _postrun_writes_allowed(artifact_dirs: list[Path]) -> bool:
    for artifact_dir in artifact_dirs:
        result = preflight(artifact_dir, "post_run", require_acceptance=True)
        for warning in result.warnings:
            print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
        if not result.allowed:
            print("BUGate role governance BLOCKED (post_run):", file=sys.stderr)
            for error in result.errors or ["role preflight failed"]:
                print(f"  - {error}", file=sys.stderr)
            return False
    return True


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


def resolve_triage_context(artifact_dir: Path) -> tuple[Path | None, str, str]:
    """Return ``(root, uc, self_healing mode)``, degrading to ``off`` on any error.

    A configuration problem must never change the *classification*: it can only
    keep the appended triage fields away.  That keeps a broken profile from
    silently altering the seven legacy keys downstream consumers read.
    """

    try:
        from role_governance import resolve_uc
        from self_heal_policy import self_healing_mode

        root = find_root(artifact_dir if artifact_dir.exists() else Path.cwd())
        config = load_config(root, os.environ.get("BUGATE_PROFILE"))
        return root, resolve_uc(root, artifact_dir, config), self_healing_mode(config)
    except Exception:
        return None, "", "off"


def classify_with_triage(
    log: str,
    exit_code: int | None,
    *,
    artifact_dir: Path,
    root: Path,
    uc: str,
    command: str = "",
    log_path: Path | None = None,
    mode: str = "off",
) -> dict:
    """Legacy classification, plus the triage schema when self-healing is enabled."""

    result = classify(log, exit_code)
    if mode == "off":
        return result
    from failure_triage import triage

    # ``update`` appends: the seven legacy keys keep their positions, so the
    # serialized prefix is unchanged for any consumer that reads them.
    result.update(
        triage(
            log,
            exit_code,
            artifact_dir=artifact_dir,
            root=root,
            uc=uc,
            command=command,
            log_path=log_path,
            overall=str(result.get("overall") or "failed"),
        )
    )
    return result


def repair_plan_text(result: dict) -> str:
    plan = (
        "# BUGate Repair Plan\n\n"
        f"- Classification: {result['overall']}\n"
        f"- Next action: {result['next_action']}\n"
        "- Boundary: this planner does not edit tests automatically.\n"
    )
    if not result.get("schema_version"):
        return plan
    return plan + (
        "\n## Failure Attribution\n\n"
        f"- Attempt: {result.get('attempt_id')}\n"
        f"- Owner: {result.get('failure_owner')}\n"
        f"- Subtype: {result.get('failure_subtype')}\n"
        f"- Confidence: {result.get('confidence')}\n"
        f"- Target layer reached: {result.get('target_layer_reached')}\n"
        f"- Healing eligible: {result.get('healing_eligible')}\n"
        f"- Blocking reasons: {', '.join(result.get('blocking_reasons') or []) or 'none'}\n"
        f"- Recommended action: {result.get('recommended_action')}\n"
    )


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
    if result.get("schema_version"):
        lines.append("## Failure Attribution")
        lines.append("")
        lines.append(f"- Schema: {result.get('schema_version')}")
        lines.append(f"- Attempt: {result.get('attempt_id')}")
        lines.append(f"- Owner: {result.get('failure_owner')}")
        lines.append(f"- Subtype: {result.get('failure_subtype')}")
        lines.append(f"- Confidence: {result.get('confidence')}")
        lines.append(f"- Target layer reached: {result.get('target_layer_reached')}")
        lines.append(f"- Oracle refs: {', '.join(result.get('oracle_refs') or []) or 'none'}")
        lines.append(f"- Healing eligible: {result.get('healing_eligible')}")
        blocking = ", ".join(result.get("blocking_reasons") or []) or "none"
        lines.append(f"- Blocking reasons: {blocking}")
        lines.append(f"- Recommended action: {result.get('recommended_action')}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="UC artifact directory; inferred from output parents when omitted.",
    )
    parser.add_argument("--pytest-log", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--md-output", required=True)
    parser.add_argument("--repair-plan-output", required=True)
    parser.add_argument("--exit-code", type=int, default=None)
    parser.add_argument(
        "--command",
        default="",
        help="runner command that produced the log; recorded in the triage schema "
        "when self_healing.mode != off, ignored otherwise",
    )
    args = parser.parse_args()
    outputs = [
        Path(args.json_output),
        Path(args.md_output),
        Path(args.repair_plan_output),
    ]
    artifact_dirs = sorted(
        {
            *(path.parent for path in outputs),
            *([args.artifact_dir] if args.artifact_dir is not None else []),
        },
        key=lambda path: path.as_posix(),
    )
    if not _postrun_writes_allowed(artifact_dirs):
        return 2
    log_path = Path(args.pytest_log)
    log = read_text(log_path) if log_path.exists() else ""
    artifact_dir = args.artifact_dir or artifact_dirs[0]
    root, uc, mode = resolve_triage_context(Path(artifact_dir))
    if mode == "off" or root is None:
        result = classify(log, args.exit_code)
    else:
        result = classify_with_triage(
            log,
            args.exit_code,
            artifact_dir=Path(artifact_dir),
            root=root,
            uc=uc,
            command=args.command,
            log_path=log_path if log_path.exists() else None,
            mode=mode,
        )
    dump_json(Path(args.json_output), result)
    write_text(Path(args.md_output), render_md(result))
    write_text(Path(args.repair_plan_output), repair_plan_text(result))
    print(f"written {args.json_output}")
    print(f"written {args.md_output}")
    print(f"written {args.repair_plan_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
