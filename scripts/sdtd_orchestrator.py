#!/usr/bin/env python3
"""Small BUGate artifact orchestrator, SUT-neutral."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from bugate_core import (
    ALL_ARTIFACTS,
    OPTIONAL_PRECODE_ARTIFACTS,
    find_engine_root,
    find_root,
    inventory_sha256,
    read_text,
    write_text,
)


ROOT = find_root()  # workspace root: gate subprocesses run against it
ENGINE_ROOT = find_engine_root()  # engine root: templates + sibling gate scripts
TEMPLATE_DIR = ENGINE_ROOT / ".shared" / "skills" / "bugate" / "templates"


def _artifacts(full_sdtd: bool) -> list[str]:
    return [*ALL_ARTIFACTS, *OPTIONAL_PRECODE_ARTIFACTS] if full_sdtd else list(ALL_ARTIFACTS)


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
    artifact_dir.mkdir(parents=True, exist_ok=True)
    created = [name for name in _artifacts(full_sdtd) if copy_template(artifact_dir, name)]
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
    init(artifact_dir)
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
    rc = run_script(
        "self_healing_mvp.py",
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
    if args.init:
        return init(args.artifact_dir, args.full_sdtd)
    if args.auto:
        if args.scope == "pre-code":
            return auto_precode(
                args.artifact_dir,
                peer_review=not args.skip_peer_review,
                allow_degraded=args.allow_degraded_peer_review,
            )
        if not args.pytest_log or not args.command:
            print("--scope post-run requires --pytest-log and --command")
            return 2
        return auto_postrun(args.artifact_dir, args)
    return status(args.artifact_dir, args.full_sdtd)


if __name__ == "__main__":
    raise SystemExit(main())
