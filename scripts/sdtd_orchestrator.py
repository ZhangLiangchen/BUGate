#!/usr/bin/env python3
"""Small BUGate artifact orchestrator, SUT-neutral."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bugate_core import ALL_ARTIFACTS, find_root, read_text, write_text


ROOT = find_root()
TEMPLATE_DIR = ROOT / ".shared" / "skills" / "bugate" / "templates"


def copy_template(artifact_dir: Path, name: str) -> bool:
    dst = artifact_dir / name
    if dst.exists():
        return False
    src = TEMPLATE_DIR / name
    if src.exists():
        write_text(dst, read_text(src))
        return True
    return False


def init(artifact_dir: Path) -> int:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    created = [name for name in ALL_ARTIFACTS if copy_template(artifact_dir, name)]
    for name in created:
        print(f"created {name}")
    if not created:
        print("no missing artifacts")
    return 0


def status(artifact_dir: Path) -> int:
    for name in ALL_ARTIFACTS:
        print(f"{name}: {'present' if (artifact_dir / name).exists() else 'missing'}")
    return 0


def run_script(name: str, *args: str) -> int:
    cmd = [sys.executable, str(ROOT / "scripts" / name), *args]
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def auto_precode(artifact_dir: Path, run_cli_workers: bool = False) -> int:
    init(artifact_dir)
    rc = 0
    if run_cli_workers:
        rc = rc or run_script("sdtd_multiview_cli_bridge.py", "run-all", str(artifact_dir))
    rc = rc or run_script("check_bugate_brief_semantics.py", str(artifact_dir))
    rc = rc or run_script("check_bugate_layer2_semantics.py", str(artifact_dir))
    rc = rc or run_script("check_bugate_inventory_semantics.py", str(artifact_dir))
    if not (artifact_dir / "03a_test_cases.md").exists() or "TBD" in read_text(artifact_dir / "03a_test_cases.md"):
        rc = rc or run_script("generate_sdtd_text_testcases.py", str(artifact_dir), "--write")
    if run_cli_workers:
        rc = rc or run_script("sdtd_adversarial_cli_bridge.py", "run-all", str(artifact_dir))
    rc = rc or run_script("check_bugate_v13_semantics.py", str(artifact_dir), "--scope", "pre-code")
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
    parser.add_argument("--run-cli-workers", action="store_true")
    parser.add_argument("--pytest-log", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--env", default="profile-owned")
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    if args.init:
        return init(args.artifact_dir)
    if args.auto:
        if args.scope == "pre-code":
            return auto_precode(args.artifact_dir, args.run_cli_workers)
        if not args.pytest_log or not args.command:
            print("--scope post-run requires --pytest-log and --command")
            return 2
        return auto_postrun(args.artifact_dir, args)
    return status(args.artifact_dir)


if __name__ == "__main__":
    raise SystemExit(main())
