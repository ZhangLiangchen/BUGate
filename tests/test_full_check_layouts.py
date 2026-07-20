#!/usr/bin/env python3
"""Full-check root-resolution and fail-closed assertion acceptances.

Every layout is fabricated under a temporary directory. In particular, the
imported collision fixture deliberately owns both ``AGENTS.md`` and ``.shared``
beside ``bugate.config.yaml``; those SUT-owned agent surfaces must never make
the full-check treat the workspace root as the BUGate engine.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / ".shared/skills/bugate-full-check/scripts/run_full_check.py"
FAILURES: list[str] = []
ROLE_FLOW_FILES = (
    "scripts/sdtd_orchestrator.py",
    "scripts/role_governance.py",
    "scripts/check_role_evidence.py",
    "scripts/check_bugate.py",
    "scripts/memory_bus.py",
    "scripts/check_bugate_v13_semantics.py",
)


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{': ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


@contextmanager
def bugate_env(**values: str):
    previous = os.environ.copy()
    try:
        for key in tuple(os.environ):
            if key.startswith("BUGATE_"):
                os.environ.pop(key)
        os.environ.update(values)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def seed_engine(engine: Path, module_name: str):
    script = engine / ".shared/skills/bugate-full-check/scripts/run_full_check.py"
    script.parent.mkdir(parents=True)
    (engine / ".shared/skills/bugate").mkdir(parents=True)
    (engine / "scripts").mkdir(parents=True)
    shutil.copy2(SOURCE, script)
    (engine / ".shared/skills/bugate-full-check/SKILL.md").write_text(
        "# full check fixture\n", encoding="utf-8"
    )
    (engine / ".shared/skills/bugate/SKILL.md").write_text(
        "# bugate fixture\n", encoding="utf-8"
    )
    (engine / "scripts/bugate_core.py").write_text("# engine sentinel\n", encoding="utf-8")
    for relative in ROLE_FLOW_FILES:
        path = engine / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# engine role-flow command: {relative}\n", encoding="utf-8")
    return load_module(script, module_name)


def load_module(script: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def mark_workspace(workspace: Path, *, collision: bool = False) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "bugate.config.yaml").write_text(
        "profile: bugate.profile.yaml\n", encoding="utf-8"
    )
    if collision:
        (workspace / ".shared").mkdir()
        (workspace / "AGENTS.md").write_text("# SUT-owned agent protocol\n", encoding="utf-8")


def seed_workspace_role_decoys(workspace: Path) -> None:
    for relative in ROLE_FLOW_FILES:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# workspace-owned decoy: {relative}\n", encoding="utf-8")


def check_role_flow_paths(module, engine: Path, workspace: Path, label: str) -> None:
    paths = module.role_flow_engine_paths(engine)
    check(f"{label} role command count", len(paths) == len(ROLE_FLOW_FILES), str(len(paths)))
    check(
        f"{label} role commands stay under engine",
        all(module._within(path, engine.resolve()) for path in paths.values()),
    )
    if workspace.resolve() != engine.resolve():
        check(
            f"{label} role commands ignore workspace collisions",
            all(not module._within(path, workspace.resolve() / "scripts") for path in paths.values()),
        )
        check(
            f"{label} role command provenance",
            all("engine role-flow command" in path.read_text(encoding="utf-8") for path in paths.values()),
        )


def scenario_core_layout(base: Path) -> None:
    print("S1 core checkout/release resolves workspace == engine")
    engine = base / "core"
    mark_workspace(engine, collision=True)
    module = seed_engine(engine, "full_check_core_fixture")
    with bugate_env(BUGATE_VENDOR_DIR=".bugate"):
        root, resolved_engine, layout = module.find_roots(engine / "scripts")
    check("core layout", layout == "core", layout)
    check("core workspace", root == engine.resolve(), str(root))
    check("core engine", resolved_engine == engine.resolve(), str(resolved_engine))
    check_role_flow_paths(module, resolved_engine, root, "core")


def scenario_imported_collision(base: Path) -> None:
    print("S2 imported repo keeps vendored engine despite AGENTS.md + .shared collision")
    workspace = base / "collision"
    mark_workspace(workspace, collision=True)
    seed_workspace_role_decoys(workspace)
    engine = workspace / ".bugate"
    module = seed_engine(engine, "full_check_collision_fixture")
    nested = workspace / "tests/e2e"
    nested.mkdir(parents=True)
    with bugate_env():
        root, resolved_engine, layout = module.find_roots(nested)
    check("collision stays imported", layout == "imported", layout)
    check("collision workspace", root == workspace.resolve(), str(root))
    check("collision engine", resolved_engine == engine.resolve(), str(resolved_engine))
    check_role_flow_paths(module, resolved_engine, root, "collision")


def scenario_custom_vendor_and_project_override(base: Path) -> None:
    print("S3 custom vendor path and BUGATE_PROJECT_ROOT remain supported")
    workspace = base / "custom"
    mark_workspace(workspace)
    seed_workspace_role_decoys(workspace)
    engine = workspace / "vendor/bugate-kit"
    module = seed_engine(engine, "full_check_custom_fixture")
    elsewhere = base / "elsewhere"
    elsewhere.mkdir()
    with bugate_env(
        BUGATE_PROJECT_ROOT=str(workspace),
        BUGATE_VENDOR_DIR="vendor/bugate-kit",
    ):
        root, resolved_engine, layout = module.find_roots(elsewhere)
    check("project override layout", layout == "imported", layout)
    check("project override workspace", root == workspace.resolve(), str(root))
    check("script-owned custom engine", resolved_engine == engine.resolve(), str(resolved_engine))
    check_role_flow_paths(module, resolved_engine, root, "custom vendor")


def scenario_invalid_engine_fails_fast(base: Path) -> None:
    print("S4 invalid explicit engine fails before capability checks")
    workspace = base / "invalid"
    mark_workspace(workspace, collision=True)
    real_engine = workspace / ".bugate"
    module = seed_engine(real_engine, "full_check_invalid_fixture")
    try:
        with bugate_env(BUGATE_ENGINE_ROOT=str(workspace)):
            module.find_roots(workspace)
    except SystemExit as exc:
        message = str(exc)
        check("invalid engine rejected", "scripts/bugate_core.py" in message, message)
    else:
        check("invalid engine rejected", False, "find_roots returned instead of failing")


def scenario_negative_checks_require_markers(base: Path) -> None:
    print("S5 expected exit 2 is not enough for a green negative control")
    engine = base / "predicate-engine"
    module = seed_engine(engine, "full_check_predicate_fixture")
    missing_script = subprocess.CompletedProcess(
        ["python3", "missing.py"], 2, stdout="python3: can't open file 'missing.py'"
    )
    real_guard = subprocess.CompletedProcess(
        ["python3", "check_bugate.py"],
        2,
        stdout="BUGate guard blocked edits to configured implementation paths",
    )
    check(
        "missing script is not an expected block",
        not module.outcome_matches(
            missing_script, 2, "BUGate guard blocked edits to configured implementation paths"
        ),
    )
    check(
        "semantic guard block is accepted",
        module.outcome_matches(
            real_guard, 2, "BUGate guard blocked edits to configured implementation paths"
        ),
    )


def scenario_hardening_probe_isolates_required_profile(base: Path) -> None:
    print("S6 hardening fixture ignores an imported required active profile")
    workspace = base / "required-imported"
    mark_workspace(workspace)
    active_profile = workspace / "bugate.profile.yaml"
    active_profile.write_text(
        "\n".join(
            [
                "artifact_dir: docs/usecases/ACTIVE",
                "role_governance:",
                "  mode: required",
                "  memory_mode: best_effort",
                "  evidence_dir: 00_role_evidence",
                "  session_id_required: true",
                "  require_distinct_sessions: true",
                "  human_acceptance_artifacts:",
                "    - 03b_adversarial_cases.yaml",
                "  phases:",
                "    pre_code:",
                "      allowed_roles: [designer]",
                "    implementation:",
                "      allowed_roles: [implementer]",
                "      requires_handoff_from: [designer]",
                "    post_run:",
                "      allowed_roles: [reviewer]",
                "      requires_handoff_from: [implementer]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    profile_before = active_profile.read_bytes()
    module = load_module(SOURCE, "full_check_required_profile_fixture")
    outside_uc = base / "unisolated-harden" / "uc"
    with bugate_env(
        BUGATE_PROJECT_ROOT=str(workspace),
        BUGATE_PROFILE=str(active_profile),
    ):
        inherited = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts/sdtd_orchestrator.py"),
                str(outside_uc),
                "--init",
            ],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
            timeout=60,
        )
        checks = []
        module.run_hardening_multiview_probe(checks, workspace, REPO)
    check(
        "required profile would block an unisolated temporary init",
        inherited.returncode == 2
        and "artifact_dir must be inside the governed workspace" in inherited.stdout,
        f"exit={inherited.returncode}",
    )
    probe = next(
        (item for item in checks if item.name == "Hardening flags enforce (multiview)"),
        None,
    )
    check("isolated hardening probe recorded", probe is not None)
    check(
        "isolated hardening probe passes",
        probe is not None
        and probe.status == "PASS"
        and "init_exit=0/0" in probe.detail
        and "all_precode_files=True" in probe.detail
        and "baseline_exit=0/0" in probe.detail
        and "semantic_exit=1/1" in probe.detail,
        probe.detail if probe is not None else "missing check",
    )
    check(
        "outer required profile and workspace remain untouched",
        active_profile.read_bytes() == profile_before
        and not outside_uc.exists()
        and not any(workspace.rglob("00_role_evidence")),
    )


def scenario_real_peer_probe_uses_one_isolated_environment(base: Path) -> None:
    print("S7 every real-peer fixture command uses one probe-owned environment")
    workspace = base / "peer-required-imported"
    mark_workspace(workspace)
    active_profile = workspace / "bugate.profile.yaml"
    active_profile.write_text(
        "role_governance:\n  mode: required\n",
        encoding="utf-8",
    )
    module = load_module(SOURCE, "full_check_peer_environment_fixture")
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str],
        root: Path,
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del root, input_text, timeout
        assert env is not None
        assert cwd is not None
        profile = Path(env["BUGATE_PROFILE"])
        project_root = Path(env["BUGATE_PROJECT_ROOT"])
        calls.append(
            {
                "cmd": tuple(cmd),
                "env": dict(env),
                "profile_text": profile.read_text(encoding="utf-8"),
                "project_root_exists": project_root.is_dir(),
                "cwd": str(cwd),
            }
        )
        target = Path(cmd[-2] if cmd[-1] == "--init" else cmd[-1])
        executable = Path(cmd[1]).name
        if executable == "sdtd_orchestrator.py":
            target.mkdir(parents=True)
            for name in module.PRECODE_NAMES:
                (target / name).write_text("fixture\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "created 01_business_brief.md\n")
        if executable == "sdtd_multiview_cli_bridge.py":
            output = target / "00_multiview"
            output.mkdir(parents=True)
            (output / "divergence_report.md").write_text(
                "dispatch_mode: real_peer_dispatch\n", encoding="utf-8"
            )
            (output / "codex_view.md").write_text("codex view\n", encoding="utf-8")
            (output / "claude_view.md").write_text("claude view\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "multiview ok\n")
        if executable == "sdtd_adversarial_cli_bridge.py":
            (target / "03b_adversarial_cases.yaml").write_text(
                "dispatch_mode: real_peer_dispatch\n", encoding="utf-8"
            )
            output = target / "00_adversarial"
            output.mkdir(parents=True)
            (output / "codex_adversarial_view.md").write_text(
                "codex view\n", encoding="utf-8"
            )
            (output / "claude_adversarial_view.md").write_text(
                "claude view\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0, "adversarial ok\n")
        raise AssertionError(f"unexpected peer-probe command: {cmd}")

    real_run = module.run
    module.run = fake_run
    try:
        checks = []
        with bugate_env(
            BUGATE_PROJECT_ROOT=str(workspace),
            BUGATE_PROFILE=str(active_profile),
        ):
            module.run_real_peer_dispatch_probe(checks, workspace, REPO, timeout=17)
    finally:
        module.run = real_run

    check("peer helper issued init + two bridge commands", len(calls) == 3, str(len(calls)))
    profiles = {str((item["env"])["BUGATE_PROFILE"]) for item in calls}
    project_roots = {str((item["env"])["BUGATE_PROJECT_ROOT"]) for item in calls}
    cwd_values = {str(item["cwd"]) for item in calls}
    check(
        "all peer commands share one non-SUT profile and root",
        len(profiles) == 1
        and len(project_roots) == 1
        and cwd_values == project_roots
        and str(active_profile) not in profiles
        and all("role_governance:\n  mode: off" in str(item["profile_text"]) for item in calls)
        and all(bool(item["project_root_exists"]) for item in calls),
    )
    check(
        "all peer commands isolate the Memory namespace",
        all(
            (item["env"])["MEMORY_BUS_PROJECT_TAG"] == module.ROLE_FLOW_NAMESPACE
            for item in calls
        ),
    )
    check(
        "all peer commands explicitly allow the non-git probe root",
        all(
            (item["env"])["SDTD_CODEX_SKIP_GIT_REPO_CHECK"] == "1"
            for item in calls
        ),
    )
    check(
        "peer init and both dispatch assertions pass",
        [item.status for item in checks] == ["PASS", "PASS", "PASS"],
        str([(item.name, item.status) for item in checks]),
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bugate-full-check-layouts.") as td:
        base = Path(td)
        scenario_core_layout(base)
        scenario_imported_collision(base)
        scenario_custom_vendor_and_project_override(base)
        scenario_invalid_engine_fails_fast(base)
        scenario_negative_checks_require_markers(base)
        scenario_hardening_probe_isolates_required_profile(base)
        scenario_real_peer_probe_uses_one_isolated_environment(base)
    if FAILURES:
        print(f"\nfull-check layout acceptance: FAIL ({len(FAILURES)})")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nfull-check layout acceptance: PASS (all scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
