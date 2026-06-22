#!/usr/bin/env python3
"""Run a SUT-neutral BUGate capability self-check.

The script intentionally exercises BUGate core and demo/profile fixtures without
adding SUT-specific facts. It writes transient artifacts only under /tmp and
prints a compact Markdown summary.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class Check:
    name: str
    status: str
    detail: str


def find_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").exists() and (candidate / ".shared").exists():
            return candidate
    raise SystemExit("BUGate root not found (expected AGENTS.md and .shared).")


def run(
    cmd: list[str],
    root: Path,
    *,
    input_text: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        cwd=root,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        env=merged_env,
    )


def add(checks: list[Check], name: str, ok: bool, detail: str, warn: bool = False) -> None:
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    checks.append(Check(name, status, compact(detail)))


def compact(text: str, limit: int = 280) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def require_tmp_path(prefix: str, suffix: str = "") -> str:
    handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=False)
    path = handle.name
    handle.close()
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("smoke", "full"),
        default="smoke",
        help="smoke skips real peer model dispatch; full runs Codex+Claude peers.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=240)
    args = parser.parse_args()

    root = find_root(Path.cwd().resolve())
    checks: list[Check] = []

    # Core gate.
    py_files = sorted(str(p.relative_to(root)) for p in (root / "scripts").glob("*.py"))
    result = run(["python3", "-m", "py_compile", *py_files], root, timeout=60)
    add(checks, "Python compile", result.returncode == 0, result.stdout or "scripts compiled")

    result = run(
        [
            "python3",
            "scripts/check_bugate_v13_semantics.py",
            "examples/demo-sut",
            "--scope",
            "all",
            "--require-passed",
        ],
        root,
        timeout=60,
    )
    add(checks, "4-layer demo gate", result.returncode == 0, result.stdout)

    # Runtime binaries and auth.
    codex_path = shutil.which("codex") or "not_found"
    claude_path = shutil.which("claude") or "not_found"
    add(checks, "Codex binary", codex_path != "not_found", codex_path)
    add(checks, "Claude binary", claude_path != "not_found", claude_path)

    if codex_path != "not_found":
        result = run(["codex", "--version"], root, timeout=20)
        add(checks, "Codex version", result.returncode == 0, result.stdout)
        result = run(
            ["codex", "exec", "--sandbox", "read-only", "-"],
            root,
            input_text="Reply exactly: ok\n",
            timeout=args.timeout_seconds,
        )
        add(checks, "Codex auth/model call", result.returncode == 0 and "ok" in result.stdout.lower(), result.stdout)

    if claude_path != "not_found":
        result = run(["claude", "--version"], root, timeout=20)
        add(checks, "Claude version", result.returncode == 0, result.stdout)
        result = run(
            ["claude", "-p", "--permission-mode", "dontAsk", "--output-format", "text", "Reply exactly: ok"],
            root,
            timeout=args.timeout_seconds,
        )
        add(checks, "Claude auth/model call", result.returncode == 0 and "ok" in result.stdout.lower(), result.stdout)

    # Bridge environment.
    result = run(["python3", "scripts/sdtd_multiview_cli_bridge.py", "check-env"], root, timeout=60)
    add(checks, "Multi-view check-env", result.returncode == 0 and "real_peer_dispatch" in result.stdout, result.stdout)
    result = run(["python3", "scripts/sdtd_adversarial_cli_bridge.py", "check-env"], root, timeout=60)
    add(checks, "Adversarial check-env", result.returncode == 0 and "real_peer_dispatch" in result.stdout, result.stdout)

    if args.mode == "full":
        with tempfile.TemporaryDirectory(prefix="bugate-full-check.") as tmp:
            tmp_root = Path(tmp)
            uc_dir = tmp_root / "demo-sut"
            shutil.copytree(root / "examples/demo-sut", uc_dir)
            peer_env = {"SDTD_CLI_TIMEOUT_SECONDS": str(args.timeout_seconds)}

            result = run(
                ["python3", "scripts/sdtd_multiview_cli_bridge.py", "run-all", str(uc_dir)],
                root,
                env=peer_env,
                timeout=args.timeout_seconds * 2,
            )
            mv_report = (uc_dir / "00_multiview/divergence_report.md").read_text(encoding="utf-8", errors="ignore")
            mv_codex = (uc_dir / "00_multiview/codex_view.md").read_text(encoding="utf-8", errors="ignore")
            mv_claude = (uc_dir / "00_multiview/claude_view.md").read_text(encoding="utf-8", errors="ignore")
            mv_ok = result.returncode == 0 and "dispatch_mode: real_peer_dispatch" in mv_report and "fallback_placeholder" not in (mv_codex + mv_claude)
            add(checks, "Real multi-view dispatch", mv_ok, result.stdout)

            result = run(
                ["python3", "scripts/sdtd_adversarial_cli_bridge.py", "run-all", str(uc_dir)],
                root,
                env=peer_env,
                timeout=args.timeout_seconds * 2,
            )
            adv_yaml = (uc_dir / "03b_adversarial_cases.yaml").read_text(encoding="utf-8", errors="ignore")
            adv_codex = (uc_dir / "00_adversarial/codex_adversarial_view.md").read_text(encoding="utf-8", errors="ignore")
            adv_claude = (uc_dir / "00_adversarial/claude_adversarial_view.md").read_text(encoding="utf-8", errors="ignore")
            adv_ok = result.returncode == 0 and "dispatch_mode: real_peer_dispatch" in adv_yaml and "fallback_placeholder" not in (adv_codex + adv_claude)
            add(checks, "Real adversarial dispatch", adv_ok, result.stdout)
    else:
        checks.append(Check("Real peer dispatch", "WARN", "Skipped in smoke mode; rerun with --mode full."))

    # Memory bus and ONNX.
    result = run(["bash", "bin/memory-bus-status"], root, timeout=30)
    add(checks, "Memory-bus status", result.returncode == 0 and "OK" in result.stdout, result.stdout)

    smoke = f"memory smoke {os.getpid()}"
    result = run(
        ["bash", "bin/memory-service-note", "--agent", "agent", "--type", "finding", "--msg", smoke, "--tag", "full-check-smoke"],
        root,
        timeout=30,
    )
    add(checks, "Memory-bus note", result.returncode == 0, result.stdout)
    result = run(
        ["bash", "bin/memory-service-search", "--query", smoke, "--tag", "full-check-smoke", "--limit", "1"],
        root,
        timeout=30,
    )
    add(checks, "Memory-bus search", result.returncode == 0 and smoke in result.stdout, result.stdout)

    onnx_root = Path.home() / ".cache/mcp_memory/onnx_models"
    onnx_files = list(onnx_root.rglob("*.onnx")) if onnx_root.exists() else []
    add(checks, "ONNX model files", bool(onnx_files), f"{len(onnx_files)} .onnx file(s) under {onnx_root}")

    memory_env = {
        "MCP_MEMORY_BASE_DIR": str(root / ".memory_bus"),
        "MCP_MEMORY_STORAGE_BACKEND": "sqlite_vec",
        "MCP_MEMORY_USE_ONNX": "1",
        "PATH": f"{root / '.venv/bin'}:{os.environ.get('PATH', '')}",
    }
    result = run(["memory", "status"], root, env=memory_env, timeout=60)
    add(checks, "ONNX memory status", result.returncode == 0 and "healthy" in result.stdout.lower(), result.stdout)

    # Wave 0 / Wave 8.
    prd_json = require_tmp_path("bugate-prd.", ".json")
    prd_md = require_tmp_path("bugate-prd.", ".md")
    try:
        result = run(
            [
                "python3",
                "scripts/check_prd_health.py",
                "--input",
                "examples/demo-sut/prd_health.yaml",
                "--gate",
                "--json-output",
                prd_json,
                "--md-output",
                prd_md,
            ],
            root,
            timeout=60,
        )
        add(checks, "Wave 0 PRD health", result.returncode == 0, result.stdout)
    finally:
        Path(prd_json).unlink(missing_ok=True)
        Path(prd_md).unlink(missing_ok=True)

    of_json = require_tmp_path("bugate-of.", ".json")
    of_md = require_tmp_path("bugate-of.", ".md")
    matrix_md = require_tmp_path("bugate-matrix.", ".md")
    try:
        result = run(
            [
                "python3",
                "scripts/oracle_falsification.py",
                "--spec",
                "examples/demo-sut/falsification_spec.yaml",
                "--gate",
                "--json-output",
                of_json,
                "--md-output",
                of_md,
            ],
            root,
            timeout=60,
        )
        add(checks, "Wave 8 falsification", result.returncode == 0, result.stdout)
        result = run(
            [
                "python3",
                "scripts/generate_assertion_coverage_matrix.py",
                "--artifact-root",
                "examples/demo-sut",
                "--spec",
                "examples/demo-sut/falsification_spec.yaml",
                "--mutation-result",
                of_json,
                "--output",
                matrix_md,
            ],
            root,
            timeout=60,
        )
        add(checks, "Wave 8 coverage matrix", result.returncode == 0, result.stdout)
    finally:
        Path(of_json).unlink(missing_ok=True)
        Path(of_md).unlink(missing_ok=True)
        Path(matrix_md).unlink(missing_ok=True)

    # Write guard and role isolation.
    guard_env = {"BUGATE_PROFILE": "examples/mounted-demo/demo.profile.yaml"}
    result = run(
        ["python3", "scripts/check_bugate.py", "examples/mounted-demo/tests/link/test_redirect.py"],
        root,
        input_text="",
        env=guard_env,
        timeout=30,
    )
    add(checks, "Write guard allows passed UC", result.returncode == 0, result.stdout)
    result = run(
        ["python3", "scripts/check_bugate.py", "examples/mounted-demo/tests/new/test_new.py"],
        root,
        input_text="",
        env=guard_env,
        timeout=30,
    )
    add(checks, "Write guard blocks pending UC", result.returncode == 2, result.stdout)

    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "sut/example/docs/source_mirror/spec.md"}})
    result = run(
        ["python3", "scripts/check_agent_role_paths.py"],
        root,
        input_text=payload,
        env={"BUGATE_PROFILE": "examples/sample-sut.profile.yaml", "BUGATE_AGENT_ROLE": "implementer"},
        timeout=30,
    )
    add(checks, "Role guard blocks forbidden path", result.returncode == 2, result.stdout)

    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "sut/example/tests/test_x.py"}})
    result = run(
        ["python3", "scripts/check_agent_role_paths.py"],
        root,
        input_text=payload,
        env={"BUGATE_PROFILE": "examples/sample-sut.profile.yaml", "BUGATE_AGENT_ROLE": "implementer"},
        timeout=30,
    )
    add(checks, "Role guard allows permitted path", result.returncode == 0, result.stdout or "allowed")

    result = run(
        [
            "python3",
            "scripts/check_bugate_v13_semantics.py",
            "examples/demo-sut",
            "--scope",
            "all",
            "--require-passed",
        ],
        root,
        env={"BUGATE_PROFILE": "examples/sample-sut.profile.yaml"},
        timeout=60,
    )
    add(checks, "Profile hardening fixture", result.returncode == 0, result.stdout)

    # Config boundary.
    result = run(
        [
            "python3",
            "-c",
            "import sys; sys.path.insert(0,'scripts'); import bugate_core; print(bugate_core.load_config())",
        ],
        root,
        timeout=30,
    )
    core_mode = "'mode': 'core'" in result.stdout and "'guarded_path_regex': []" in result.stdout
    checks.append(
        Check(
            "Activation boundary",
            "WARN" if core_mode else "PASS",
            "Core mode with no guarded paths; real SUT gates require a mounted profile."
            if core_mode
            else compact(result.stdout),
        )
    )

    print("# BUGate Full Check")
    print()
    print(f"- Mode: `{args.mode}`")
    print(f"- Repo: `{root}`")
    print()
    print("| Check | Status | Detail |")
    print("|---|---|---|")
    for check in checks:
        print(f"| {check.name} | {check.status} | {check.detail.replace('|', '/')} |")

    failures = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    print()
    if failures:
        print(f"Result: FAIL ({len(failures)} failed, {len(warnings)} warning).")
        return 1
    print(f"Result: PASS ({len(warnings)} warning).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
