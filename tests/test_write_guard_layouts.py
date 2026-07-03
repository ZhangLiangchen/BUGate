#!/usr/bin/env python3
"""Write-guard dual-layout acceptance on ephemeral fixtures.

The upstream repo ships no committed example SUT trees (imported-mode purity,
2026-07-04): this test fabricates BOTH governed layouts in a temp dir and
asserts the physical write guard (`scripts/check_bugate.py`) behaves the same
in each:

  - **imported layout** — the governed repo is the workspace root, marked by
    its committed ``bugate.config.yaml``; profile paths are repo-relative.
  - **engine-development layout** — the engine-repo shape found via the legacy
    ``AGENTS.md`` + ``.shared/`` sentinel fallback (no config file), with the
    SUT tree beneath it and the profile bound via ``BUGATE_PROFILE``.

Scenarios per layout: pending UC blocked (rc 2), passed UC allowed (rc 0),
UC without an artifact dir fail-closed (rc 2); plus a no-profile workspace
where the guard is inert (rc 0).

Run: ``python3 tests/test_write_guard_layouts.py``
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
GUARD = ENGINE / "scripts" / "check_bugate.py"

PRECODE = [
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
]

FAILURES: list[str] = []


def make_uc(uc_dir: Path, *, passed: bool, only_first: bool = False) -> None:
    uc_dir.mkdir(parents=True, exist_ok=True)
    names = PRECODE[:1] if only_first else PRECODE
    status = "passed" if passed else "pending"
    for name in names:
        (uc_dir / name).write_text(f"---\ngate_status: {status}\n---\n", encoding="utf-8")


def run_guard(cwd: Path, path_arg: str, *, profile_env: str | None = None) -> int:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BUGATE_")}
    if profile_env:
        env["BUGATE_PROFILE"] = profile_env
    proc = subprocess.run(
        [sys.executable, str(GUARD), path_arg],
        cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
    )
    return proc.returncode


def check(label: str, got: int, want: int) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: rc={got} (want {want})")
    if not ok:
        FAILURES.append(label)


def build_imported(tmp: Path) -> Path:
    ws = tmp / "imported-ws"
    ws.mkdir()
    (ws / "bugate.config.yaml").write_text("profile: bugate.profile.yaml\n", encoding="utf-8")
    (ws / "bugate.profile.yaml").write_text(
        "artifact_dir_template: usecases/{uc}/\n"
        "guarded_path_regex:\n"
        '  - "(^|/)tests/(?P<uc>[^/]+)/[^/]+[.]py$"\n'
        + "required_precode_artifacts:\n"
        + "".join(f"  - {n}\n" for n in PRECODE),
        encoding="utf-8",
    )
    make_uc(ws / "usecases" / "ok", passed=True)
    make_uc(ws / "usecases" / "pending", passed=False, only_first=True)
    for uc in ("ok", "pending", "other"):
        t = ws / "tests" / uc
        t.mkdir(parents=True, exist_ok=True)
        (t / "test_x.py").write_text("# guarded placeholder\n", encoding="utf-8")
    return ws


def build_engine_dev(tmp: Path) -> Path:
    eng = tmp / "engine-dev"
    (eng / ".shared").mkdir(parents=True)
    (eng / "AGENTS.md").write_text("# sentinel\n", encoding="utf-8")
    # Deliberately NO bugate.config.yaml here: this exercises the legacy
    # sentinel fallback, which is exactly the self-development regression to keep.
    (eng / "sutws" / "usecases").mkdir(parents=True)
    (eng / "sutws" / "demo.profile.yaml").write_text(
        "artifact_dir_template: sutws/usecases/{uc}/\n"
        "guarded_path_regex:\n"
        '  - "(^|/)sutws/tests/(?P<uc>[^/]+)/[^/]+[.]py$"\n'
        + "required_precode_artifacts:\n"
        + "".join(f"  - {n}\n" for n in PRECODE),
        encoding="utf-8",
    )
    make_uc(eng / "sutws" / "usecases" / "ok", passed=True)
    make_uc(eng / "sutws" / "usecases" / "pending", passed=False, only_first=True)
    for uc in ("ok", "pending", "other"):
        t = eng / "sutws" / "tests" / uc
        t.mkdir(parents=True, exist_ok=True)
        (t / "test_x.py").write_text("# guarded placeholder\n", encoding="utf-8")
    return eng


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("imported layout (workspace root = committed bugate.config.yaml):")
        ws = build_imported(tmp)
        check("passed UC allowed", run_guard(ws, "tests/ok/test_x.py"), 0)
        check("pending UC blocked", run_guard(ws, "tests/pending/test_x.py"), 2)
        check("unbound UC fail-closed", run_guard(ws, "tests/other/test_x.py"), 2)

        print("engine-development layout (sentinel fallback, profile via BUGATE_PROFILE):")
        eng = build_engine_dev(tmp)
        profile = "sutws/demo.profile.yaml"
        check("passed UC allowed", run_guard(eng, "sutws/tests/ok/test_x.py", profile_env=profile), 0)
        check("pending UC blocked", run_guard(eng, "sutws/tests/pending/test_x.py", profile_env=profile), 2)
        check("unbound UC fail-closed", run_guard(eng, "sutws/tests/other/test_x.py", profile_env=profile), 2)

        print("no-profile workspace (guard inert):")
        inert = tmp / "inert-ws"
        (inert / "tests" / "any").mkdir(parents=True)
        (inert / "bugate.config.yaml").write_text("bugate:\n  mode: core\n", encoding="utf-8")
        (inert / "tests" / "any" / "test_x.py").write_text("# unguarded\n", encoding="utf-8")
        check("no guards configured -> allowed", run_guard(inert, "tests/any/test_x.py"), 0)

    if FAILURES:
        print(f"\nwrite-guard dual-layout acceptance: FAIL ({len(FAILURES)}: {', '.join(FAILURES)})")
        return 1
    print("\nwrite-guard dual-layout acceptance: PASS (both layouts, all scenarios)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
