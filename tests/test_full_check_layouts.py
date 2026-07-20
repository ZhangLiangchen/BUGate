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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bugate-full-check-layouts.") as td:
        base = Path(td)
        scenario_core_layout(base)
        scenario_imported_collision(base)
        scenario_custom_vendor_and_project_override(base)
        scenario_invalid_engine_fails_fast(base)
        scenario_negative_checks_require_markers(base)
    if FAILURES:
        print(f"\nfull-check layout acceptance: FAIL ({len(FAILURES)})")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nfull-check layout acceptance: PASS (all scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
