#!/usr/bin/env python3
"""Small BUGate artifact orchestrator, SUT-neutral."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from bugate_core import (
    ALL_ARTIFACTS,
    OPTIONAL_PRECODE_ARTIFACTS,
    PRECODE_ARTIFACTS,
    find_engine_root,
    find_root,
    gate_status,
    inventory_sha256,
    load_config,
    read_text,
    required_precode_artifacts,
    write_text,
)
from role_governance import (
    GovernanceResult,
    RoleGovernanceError,
    load_context,
    preflight,
    status_data,
    verify_chain,
)


ROOT = find_root()  # workspace root: gate subprocesses run against it
ENGINE_ROOT = find_engine_root()  # engine root: templates + sibling gate scripts
TEMPLATE_DIR = ENGINE_ROOT / ".shared" / "skills" / "bugate" / "templates"


def _artifacts(full_sdtd: bool) -> list[str]:
    return [*ALL_ARTIFACTS, *OPTIONAL_PRECODE_ARTIFACTS] if full_sdtd else list(ALL_ARTIFACTS)


def _init_artifacts(full_sdtd: bool, governance_mode: str) -> list[str]:
    """Required governance initializes only the active pre-code phase.

    Legacy/off and advisory profiles retain the v0.3.x 01--05 scaffold.  The
    advisory path warns through preflight but deliberately remains compatible.
    """

    names = list(PRECODE_ARTIFACTS) if governance_mode == "required" else list(ALL_ARTIFACTS)
    if full_sdtd:
        names.extend(OPTIONAL_PRECODE_ARTIFACTS)
    return names


def _role_preflight(
    artifact_dir: Path,
    phase: str,
    *,
    require_acceptance: bool,
) -> GovernanceResult:
    result = preflight(
        artifact_dir,
        phase,
        require_acceptance=require_acceptance,
    )
    for warning in result.warnings:
        print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
    if not result.allowed:
        print(f"BUGate role governance BLOCKED ({phase}):", file=sys.stderr)
        for error in result.errors or ["role preflight failed"]:
            print(f"  - {error}", file=sys.stderr)
    return result


def _has_human_acceptance(artifact_dir: Path) -> bool:
    """Return true only for a locally valid human-acceptance receipt chain."""

    try:
        ctx = load_context(artifact_dir)
        if ctx.mode == "off":
            return False
        return any(item.get("event") == "human_acceptance" for item in verify_chain(ctx))
    except (RoleGovernanceError, OSError, ValueError, SystemExit):
        # Required mode was already rejected by the entry preflight.  Advisory
        # mode reports the malformed chain as a warning and keeps legacy flow.
        return False


_STATE_LABELS = {
    "awaiting_human_acceptance": "READY_FOR_HUMAN_ACCEPTANCE",
    "ready_for_designer_handoff": "READY_FOR_DESIGNER_HANDOFF",
    "awaiting_implementer_acceptance": "BLOCKED",
    "implementation_unlocked": "IMPLEMENTATION_UNLOCKED",
    "awaiting_reviewer_acceptance": "READY_FOR_REVIEWER_HANDOFF",
    "post_run_active": "POST_RUN_ACTIVE",
    "closed": "CLOSED",
}


def _legacy_auto_state(artifact_dir: Path, scope: str) -> str:
    if scope == "post-run":
        return "POST_RUN_ACTIVE"
    try:
        config = load_config(ROOT, os.environ.get("BUGATE_PROFILE"))
        names = required_precode_artifacts(config)
        if names and all(gate_status(artifact_dir / name) == "passed" for name in names):
            return "IMPLEMENTATION_UNLOCKED"
    except Exception:
        return "BLOCKED"
    return "READY_FOR_HUMAN_ACCEPTANCE"


def print_auto_state(artifact_dir: Path, scope: str, rc: int) -> None:
    label = "BLOCKED"
    if rc == 0:
        try:
            data = status_data(artifact_dir)
            if data.get("mode") == "off":
                label = _legacy_auto_state(artifact_dir, scope)
            elif data.get("ok"):
                label = _STATE_LABELS.get(str(data.get("state") or ""), "BLOCKED")
        except Exception:
            label = "BLOCKED"
    print(f"BUGate lifecycle status: {label}")


def copy_template(artifact_dir: Path, name: str) -> bool:
    dst = artifact_dir / name
    if dst.exists():
        return False
    src = TEMPLATE_DIR / name
    if src.exists():
        write_text(dst, read_text(src))
        return True
    return False


def init(artifact_dir: Path, full_sdtd: bool = False) -> int:
    role = _role_preflight(
        artifact_dir,
        "pre_code",
        require_acceptance=False,
    )
    if not role.allowed:
        return 2
    artifact_dir.mkdir(parents=True, exist_ok=True)
    created = [
        name
        for name in _init_artifacts(full_sdtd, role.mode)
        if copy_template(artifact_dir, name)
    ]
    for name in created:
        print(f"created {name}")
    if not created:
        print("no missing artifacts")
    return 0


def status(artifact_dir: Path, full_sdtd: bool = False) -> int:
    for name in _artifacts(full_sdtd):
        print(f"{name}: {'present' if (artifact_dir / name).exists() else 'missing'}")
    return 0


def run_script(name: str, *args: str) -> int:
    cmd = [sys.executable, str(ENGINE_ROOT / "scripts" / name), *args]
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def readable_cases_stale(artifact_dir: Path) -> bool:
    """True when 03a_test_cases.md should be (re)generated from the inventory.

    Stale = missing, carries a TBD placeholder, OR was generated from an older
    inventory — detected by comparing the inventory's current sha256 against the
    ``source_inventory_sha256`` recorded in the 03a frontmatter. The sha-drift
    check is what makes a freshly-added inventory case flow to the readable layer
    without a manual regenerate.
    """
    cases_md = artifact_dir / "03a_test_cases.md"
    if not cases_md.exists():
        return True
    text = read_text(cases_md)
    if "TBD" in text:
        return True
    # Corruption guard (Stage 3B adversarial finding): a list field mangled into
    # a comma-separated char stream (e.g. "[, P, -, 0, 0, 1, ]") means the 03a is
    # a damaged generator product — regenerate even though sha may still match.
    if re.search(r"(?:\S, ){4,}", text):
        return True
    recorded = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("source_inventory_sha256:"):
            recorded = stripped.split(":", 1)[1].strip()
            break
    current = inventory_sha256(artifact_dir)
    return bool(recorded) and bool(current) and recorded != current


def _warn_peer_review_skipped(stage: str) -> None:
    bar = "=" * 68
    print(bar)
    print(f"!! PEER REVIEW SKIPPED by --skip-peer-review: {stage} NOT run.")
    print("   This UC has NOT been adversarially cross-reviewed by two agents.")
    print(bar)


def peer_review_degraded(artifact_dir: Path) -> list[str]:
    """Reasons the dual-agent review did not happen *for real* (or []).

    The bridges degrade gracefully (placeholder / schema-rejected) and still
    exit 0, so the orchestrator inspects the produced artifacts to make any
    degradation loud and fail-worthy — a degraded review is a *simplification*,
    not a real review.
    """
    reasons: list[str] = []
    mv = artifact_dir / "00_multiview"
    div = mv / "divergence_report.md"
    if div.exists() and "fallback_placeholder" in read_text(div):
        reasons.append("Wave 1 multiview: fallback placeholder (real peer dispatch did not run)")
    failures = mv / "cli_bridge_failures"
    if failures.exists() and any(failures.iterdir()):
        reasons.append("Wave 1 multiview: a peer view was schema-rejected and replaced by placeholder")
    for peer in ("codex", "claude"):
        view = mv / f"{peer}_view.md"
        if view.exists() and "dispatch_mode: fallback_placeholder" in read_text(view):
            reasons.append(f"Wave 1 multiview: {peer} view degraded to placeholder")
    adv = artifact_dir / "03b_adversarial_cases.yaml"
    if adv.exists():
        text = read_text(adv)
        if "fallback_placeholder" in text or "partial_real_peer_dispatch" in text:
            reasons.append("Stage 3B adversarial: dispatch degraded (placeholder / partial)")
    return reasons


def auto_precode(artifact_dir: Path, peer_review: bool = True, allow_degraded: bool = False) -> int:
    """Full pre-code orchestration.

    Dual-agent peer review (Wave 1 multiview + Stage 3B adversarial) runs BY
    DEFAULT — it can only be skipped with an explicit, loudly-logged
    --skip-peer-review, and a degraded (placeholder) review fails unless
    --allow-degraded-peer-review is given. This makes the review impossible to
    skip or simplify by omission.
    """
    role = _role_preflight(
        artifact_dir,
        "pre_code",
        require_acceptance=False,
    )
    if not role.allowed:
        return 2
    if _has_human_acceptance(artifact_dir):
        message = (
            "03B already has a human-acceptance receipt; --auto will not rewrite "
            "accepted pre-code evidence. Continue with `bin/bugate-role handoff "
            f"{artifact_dir} --phase pre_code --to implementer`."
        )
        if role.mode == "required":
            print(f"BUGate role governance BLOCKED: {message}", file=sys.stderr)
            return 2
        print(f"BUGate role-governance WARNING: {message}", file=sys.stderr)
        return 0
    init_rc = init(artifact_dir)
    if init_rc:
        return init_rc
    rc = 0
    if peer_review:
        rc = rc or run_script("sdtd_multiview_cli_bridge.py", "run-all", str(artifact_dir))
    else:
        _warn_peer_review_skipped("Wave 1 multi-view")
    rc = rc or run_script("check_bugate_brief_semantics.py", str(artifact_dir))
    rc = rc or run_script("check_bugate_layer2_semantics.py", str(artifact_dir))
    rc = rc or run_script("check_bugate_inventory_semantics.py", str(artifact_dir))
    if readable_cases_stale(artifact_dir):
        rc = rc or run_script("generate_sdtd_text_testcases.py", str(artifact_dir), "--write")
    if peer_review:
        rc = rc or run_script("sdtd_adversarial_cli_bridge.py", "run-all", str(artifact_dir))
    else:
        _warn_peer_review_skipped("Stage 3B adversarial")
    rc = rc or run_script("check_bugate_v13_semantics.py", str(artifact_dir), "--scope", "pre-code")
    if peer_review:
        degraded = peer_review_degraded(artifact_dir)
        if degraded:
            bar = "=" * 68
            print(bar)
            print("!! PEER REVIEW DEGRADED — dual-agent review did not run for real:")
            for reason in degraded:
                print(f"   - {reason}")
            print("   Real Codex+Claude dispatch is required; do NOT treat this as reviewed.")
            print("   Re-run with both CLIs available, or pass --allow-degraded-peer-review.")
            print(bar)
            if not allow_degraded:
                rc = rc or 3
    return rc


def auto_postrun(artifact_dir: Path, args: argparse.Namespace) -> int:
    role = _role_preflight(
        artifact_dir,
        "post_run",
        require_acceptance=True,
    )
    if not role.allowed:
        return 2
    rc = run_script(
        "self_healing_mvp.py",
        "--artifact-dir",
        str(artifact_dir),
        "--pytest-log",
        args.pytest_log,
        "--json-output",
        str(artifact_dir / "self_healing.json"),
        "--md-output",
        str(artifact_dir / "self_healing.md"),
        "--repair-plan-output",
        str(artifact_dir / "self_healing_repair_plan.md"),
        "--exit-code",
        str(args.exit_code),
    )
    rc = rc or run_script(
        "generate_sdtd_reports.py",
        str(artifact_dir),
        "--pytest-log",
        args.pytest_log,
        "--command",
        args.command,
        "--env",
        args.env,
        "--exit-code",
        str(args.exit_code),
        "--self-healing-json",
        str(artifact_dir / "self_healing.json"),
        "--write",
    )
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--scope", choices=["pre-code", "post-run"], default="pre-code")
    parser.add_argument(
        "--full-sdtd",
        action="store_true",
        help="Also create/list the optional modeling artifacts (01a/01b/02a).",
    )
    parser.add_argument("--run-cli-workers", action="store_true",
                        help="deprecated: dual-agent peer review now runs by default in --auto")
    parser.add_argument("--skip-peer-review", action="store_true",
                        help="explicitly skip Wave 1 / Stage 3B dual-agent review (logged loudly)")
    parser.add_argument("--allow-degraded-peer-review", action="store_true",
                        help="do not fail when peer review degrades to placeholder/schema-rejected")
    parser.add_argument("--pytest-log", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--env", default="profile-owned")
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    if args.init and args.auto:
        print("--init and --auto are separate operations; run them as separate commands")
        print_auto_state(args.artifact_dir, args.scope, 2)
        return 2
    if args.init:
        return init(args.artifact_dir, args.full_sdtd)
    if args.auto:
        if args.scope == "pre-code":
            rc = auto_precode(
                args.artifact_dir,
                peer_review=not args.skip_peer_review,
                allow_degraded=args.allow_degraded_peer_review,
            )
            print_auto_state(args.artifact_dir, args.scope, rc)
            return rc
        if not args.pytest_log or not args.command:
            print("--scope post-run requires --pytest-log and --command")
            print_auto_state(args.artifact_dir, args.scope, 2)
            return 2
        rc = auto_postrun(args.artifact_dir, args)
        print_auto_state(args.artifact_dir, args.scope, rc)
        return rc
    return status(args.artifact_dir, args.full_sdtd)


if __name__ == "__main__":
    raise SystemExit(main())
