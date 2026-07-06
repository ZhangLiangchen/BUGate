#!/usr/bin/env python3
"""Run a SUT-neutral BUGate capability self-check.

The script exercises BUGate core without adding SUT-specific facts. The repo
ships no committed example SUT trees (imported-mode purity), so every
governed-workspace probe fabricates its fixtures under /tmp at run time; only
a compact Markdown summary is printed.
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
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        cwd=cwd or root,
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


PRECODE_NAMES = [
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
]


def build_guard_workspace(base: Path) -> Path:
    """Fabricate a minimal governed workspace (imported layout) for guard probes."""
    ws = base / "ws"
    for uc, passed in (("ok", True), ("pending", False)):
        (ws / "tests" / uc).mkdir(parents=True)
        (ws / "tests" / uc / "test_x.py").write_text("# guarded placeholder\n", encoding="utf-8")
        uc_dir = ws / "usecases" / uc
        uc_dir.mkdir(parents=True)
        names = PRECODE_NAMES if passed else PRECODE_NAMES[:1]
        status = "passed" if passed else "pending"
        for name in names:
            (uc_dir / name).write_text(f"---\ngate_status: {status}\n---\n", encoding="utf-8")
    (ws / "bugate.config.yaml").write_text("profile: bugate.profile.yaml\n", encoding="utf-8")
    (ws / "bugate.profile.yaml").write_text(
        "artifact_dir_template: usecases/{uc}/\n"
        'guarded_path_regex:\n  - "(^|/)tests/(?P<uc>[^/]+)/[^/]+[.]py$"\n'
        + "required_precode_artifacts:\n"
        + "".join(f"  - {n}\n" for n in PRECODE_NAMES)
        + 'agent_roles:\n  implementer:\n    - "^mirror/.*$"\n',
        encoding="utf-8",
    )
    return ws


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
            ".shared/skills/bugate/templates",
            "--scope",
            "pre-code",
        ],
        root,
        timeout=60,
    )
    add(checks, "4-layer gate engine (templates, pre-code)", result.returncode == 0, result.stdout)

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
            uc_dir = tmp_root / "peer-uc"
            result = run(
                ["python3", "scripts/sdtd_orchestrator.py", str(uc_dir), "--init"],
                root,
                timeout=60,
            )
            add(checks, "Peer fixture init (templates)", result.returncode == 0, result.stdout)
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

    # System-level bus home, same resolution as bin/memory-bus-start:
    # MCP_MEMORY_BASE_DIR > BUGATE_MEMORY_HOME > ~/.bugate/memory-bus.
    bus_home = (
        os.environ.get("MCP_MEMORY_BASE_DIR")
        or os.environ.get("BUGATE_MEMORY_HOME")
        or str(Path.home() / ".bugate" / "memory-bus")
    )
    memory_env = {
        "MCP_MEMORY_BASE_DIR": bus_home,
        "MCP_MEMORY_STORAGE_BACKEND": "sqlite_vec",
        "MCP_MEMORY_USE_ONNX": "1",
        "PATH": f"{root / '.venv/bin'}:{os.environ.get('PATH', '')}",
    }
    result = run(["memory", "status"], root, env=memory_env, timeout=60)
    add(checks, "ONNX memory status", result.returncode == 0 and "healthy" in result.stdout.lower(), result.stdout)

    # Wave 0 / Wave 8 — no committed demo specs: the capability probe is the
    # graceful-degradation contract (engine wired, reports profile_required,
    # exit 0 until a SUT profile supplies a real spec).
    result = run(["python3", "scripts/check_prd_health.py", "--gate"], root, timeout=60)
    add(
        checks,
        "Wave 0 engine (profile_required degrade)",
        result.returncode == 0 and "profile_required" in result.stdout,
        result.stdout,
    )
    result = run(["python3", "scripts/oracle_falsification.py", "--gate"], root, timeout=60)
    add(
        checks,
        "Wave 8 engine (profile_required degrade)",
        result.returncode == 0 and "profile_required" in result.stdout,
        result.stdout,
    )
    result = run(["python3", "scripts/generate_assertion_coverage_matrix.py", "--help"], root, timeout=30)
    add(checks, "Wave 8 coverage-matrix CLI present", result.returncode == 0, "argparse help ok")

    # Write guard and role isolation — fabricated governed workspace.
    with tempfile.TemporaryDirectory(prefix="bugate-guard.") as tmp:
        ws = build_guard_workspace(Path(tmp))
        guard = str(root / "scripts" / "check_bugate.py")
        neutral_env = {"BUGATE_PROFILE": ""}
        result = run([sys.executable, guard, "tests/ok/test_x.py"],
                     root, cwd=ws, input_text="", env=neutral_env, timeout=30)
        add(checks, "Write guard allows passed UC", result.returncode == 0, result.stdout or "allowed")
        result = run([sys.executable, guard, "tests/pending/test_x.py"],
                     root, cwd=ws, input_text="", env=neutral_env, timeout=30)
        add(checks, "Write guard blocks pending UC", result.returncode == 2, result.stdout)

        role_env = {
            "BUGATE_PROFILE": str(ws / "bugate.profile.yaml"),
            "BUGATE_AGENT_ROLE": "implementer",
        }
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "mirror/spec.md"}})
        result = run(["python3", "scripts/check_agent_role_paths.py"],
                     root, input_text=payload, env=role_env, timeout=30)
        add(checks, "Role guard blocks forbidden path", result.returncode == 2, result.stdout)
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "tests/ok/test_x.py"}})
        result = run(["python3", "scripts/check_agent_role_paths.py"],
                     root, input_text=payload, env=role_env, timeout=30)
        add(checks, "Role guard allows permitted path", result.returncode == 0, result.stdout or "allowed")

    # Hardening flags enforce (fabricated): a template-initialized UC must be
    # REJECTED once the profile demands the Wave-1 multiview report.
    with tempfile.TemporaryDirectory(prefix="bugate-harden.") as tmp:
        uc = Path(tmp) / "uc"
        result = run(["python3", "scripts/sdtd_orchestrator.py", str(uc), "--init"], root, timeout=60)
        init_ok = result.returncode == 0
        prof = Path(tmp) / "harden.profile.yaml"
        prof.write_text(
            f"artifact_dir_template: {tmp}/{{uc}}/\nrequire_multiview: true\n",
            encoding="utf-8",
        )
        result = run(
            ["python3", "scripts/check_bugate_v13_semantics.py", str(uc), "--scope", "pre-code"],
            root,
            env={"BUGATE_PROFILE": str(prof)},
            timeout=60,
        )
        enforced = result.returncode != 0 and "divergence_report" in result.stdout
        add(checks, "Hardening flags enforce (multiview)", init_ok and enforced, result.stdout)

    # Alternate semantic-schema dialect: dialect selection must be a real fork —
    # a minimal original-gate Layer-1 brief passes under --schema original-gate
    # and is rejected by the canonical v1.3 schema (canonical ids + sections).
    with tempfile.TemporaryDirectory(prefix="bugate-dialect.") as tmp:
        uc = Path(tmp)
        (uc / "01_business_brief.md").write_text(
            "---\ngate: layer1_business_brief\ngate_status: passed\n---\n\n"
            "## SUT And Scope\nA neutral request flow under test.\n\n"
            "## Canonical Business Flow\nSubmit -> validate -> settle.\n\n"
            "## Assertions That Follow From Business\n- A settled request is queryable.\n\n"
            "## Unknowns And Questions\n- None open.\n",
            encoding="utf-8",
        )
        result = run(
            ["python3", "scripts/check_bugate_brief_semantics.py", str(uc),
             "--require-passed", "--schema", "original-gate"],
            root, timeout=60,
        )
        alt_ok = result.returncode == 0
        result = run(
            ["python3", "scripts/check_bugate_brief_semantics.py", str(uc),
             "--require-passed", "--schema", "v1.3"],
            root, timeout=60,
        )
        rejects_default = result.returncode != 0
        add(
            checks,
            "Alternate dialect (original-gate, Layer 1)",
            alt_ok and rejects_default,
            f"original-gate={'ok' if alt_ok else 'FAIL'} v1.3-rejects={'ok' if rejects_default else 'NO'}",
        )

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
            "Core mode with no guarded paths; real SUT gates require an imported SUT profile."
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
