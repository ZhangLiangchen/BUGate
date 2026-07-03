#!/usr/bin/env python3
"""Negative/positive controls for the de-SUT guard (CHARTER A1 / ADR-BUGATE-004).

Each scenario builds a throwaway mini-layout (engine kit + upstream sentinel +
governed workspace), plants content, and asserts the guard's verdict — proving
the acceptance criteria mechanically:

  1. reverse stays tight — an identity term planted in the kit subtree turns
     the guard red; templates accept no frontmatter exemption; the legacy
     fixture red line still bites;
  2. forward unlocks — case-studies dir, provenance frontmatter on narrative
     docs, and the inline marker (HTML-comment form) legitimize mention;
  3. second-SUT defense — a profile-declared term is caught in the kit tree
     but NOT reported in the governed workspace's own files;
  4. industry vocabulary is no longer defended by core;
  5. general hygiene runs everywhere, exempted by no directory or frontmatter.

Stdlib-only, self-contained: run `python3 tests/test_desut_guard.py`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEGACY_FIXTURE = REPO / "tests" / "fixtures" / "legacy-sut-terms.txt"

FAILURES: list[str] = []


def make_layout(tmp: Path, *, with_workspace_config: bool = True, terms: list[str] | None = None) -> None:
    """Build a mini upstream-shaped engine tree with an optional term profile."""
    (tmp / "scripts").mkdir(parents=True)
    for name in ("check_no_sut_terms.py", "bugate_core.py"):
        shutil.copy2(REPO / "scripts" / name, tmp / "scripts" / name)
    (tmp / "bin").mkdir()
    (tmp / ".shared/skills/bugate/templates").mkdir(parents=True)
    (tmp / "docs/case-studies").mkdir(parents=True)
    (tmp / "docs/qa-methodology").mkdir(parents=True)
    (tmp / "examples").mkdir()
    (tmp / "CHARTER.md").write_text("# charter (upstream sentinel)\n", encoding="utf-8")
    if with_workspace_config:
        body = "bugate:\n  version: 0.1\n"
        if terms:
            body += "profile: sut.profile.yaml\n"
            profile = "sut_identity_terms:\n" + "".join(f'  - "{t}"\n' for t in terms)
            (tmp / "sut.profile.yaml").write_text(profile, encoding="utf-8")
        (tmp / "bugate.config.yaml").write_text(body, encoding="utf-8")


def run_guard(tmp: Path, *, cwd: Path | None = None, args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["BUGATE_ENGINE_ROOT"] = str(tmp)
    env.pop("BUGATE_PROFILE", None)
    env.pop("BUGATE_PROJECT_ROOT", None)
    return subprocess.run(
        [sys.executable, str(tmp / "scripts/check_no_sut_terms.py"), *args],
        cwd=cwd or tmp,
        env=env,
        capture_output=True,
        text=True,
    )


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "ok" if ok else "FAIL"
    print(f"  [{status}] {name}")
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def first_active_legacy_term() -> str:
    """First uncommented regex from the shipped legacy fixture (no hardcoding here)."""
    for line in LEGACY_FIXTURE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise SystemExit("legacy fixture has no active terms")


def scenario_reverse_kit_seepage() -> None:
    print("S1 reverse: identity term planted in the kit subtree -> red")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, terms=[r"\bdemosut\b"])
        (tmp / "scripts/leak.py").write_text("BASE = 'https://api.demosut.example'\n", encoding="utf-8")
        cp = run_guard(tmp)
        check("kit-subtree seepage is red", cp.returncode != 0 and "demosut" in cp.stderr, cp.stderr)


def scenario_templates_reject_frontmatter() -> None:
    print("S2 reverse: templates/schema accept no frontmatter exemption")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, terms=[r"\bdemosut\b"])
        (tmp / ".shared/skills/bugate/templates/t.md").write_text(
            "---\ndesut: provenance-allowed\n---\n\ndemosut default endpoint\n", encoding="utf-8"
        )
        cp = run_guard(tmp)
        check("template frontmatter does not exempt", cp.returncode != 0 and "demosut" in cp.stderr, cp.stderr)


def scenario_forward_narrative_channels() -> None:
    print("S3 forward: case-studies dir, provenance frontmatter, inline marker -> green")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, terms=[r"\bdemosut\b"])
        (tmp / "docs/case-studies/story.md").write_text(
            "# Import case study\n\nThe origin SUT demosut migrated to imported mode.\n", encoding="utf-8"
        )
        (tmp / "docs/qa-methodology/history.md").write_text(
            "---\ndesut: provenance-allowed\n---\n\nBUGate was extracted from demosut.\n", encoding="utf-8"
        )
        (tmp / "README.md").write_text(
            "# Kit\n\nBorn inside demosut. <!-- bugate: allow-sut-term -->\n", encoding="utf-8"
        )
        cp = run_guard(tmp)
        check("all three exemption channels hold", cp.returncode == 0, cp.stderr)


def scenario_workspace_territory() -> None:
    print("S4 second-SUT defense: workspace's own files are not the surface; kit still is")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, with_workspace_config=False)
        ws = tmp / "examples/governed-ws"
        (ws / "usecases").mkdir(parents=True)
        (ws / "bugate.config.yaml").write_text("profile: bugate.profile.yaml\n", encoding="utf-8")
        (ws / "bugate.profile.yaml").write_text(
            'sut_identity_terms:\n  - "\\bdemosut\\b"\n', encoding="utf-8"
        )
        (ws / "usecases/notes.md").write_text("demosut owns this workspace.\n", encoding="utf-8")
        cp = run_guard(tmp, cwd=ws)
        check("workspace territory not reported", cp.returncode == 0, cp.stderr)
        (tmp / "scripts/leak.py").write_text("demosut\n", encoding="utf-8")
        cp = run_guard(tmp, cwd=ws)
        check("kit seepage still red from workspace cwd", cp.returncode != 0 and "scripts/leak.py" in cp.stderr, cp.stderr)


def scenario_industry_terms_delisted() -> None:
    print("S5 industry vocabulary: not defended unless a profile lists it")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, terms=[r"\bdemosut\b"])
        (tmp / "docs/qa-methodology/method.md").write_text(
            "Generic method text may mention swagger, tron, mnemonic, vault freely.\n", encoding="utf-8"
        )
        cp = run_guard(tmp)
        check("industry words are green", cp.returncode == 0, cp.stderr)


def scenario_hygiene_everywhere() -> None:
    print("S6 hygiene: machine paths/credential shapes red even with no term list, everywhere")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, with_workspace_config=False)  # no workspace, no terms -> hygiene-only
        (tmp / "scripts/cfg.py").write_text("CACHE = '/Users/somedev/work/cache/'\n", encoding="utf-8")
        cp = run_guard(tmp)
        check("machine-local user path is red", cp.returncode != 0 and "machine-local" in cp.stderr, cp.stderr)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, with_workspace_config=False)
        (tmp / "docs/case-studies/leaky.md").write_text(
            "key id AKIA" + "A" * 16 + " must never ship\n", encoding="utf-8"
        )
        cp = run_guard(tmp)
        check("allowlisted dir does NOT exempt hygiene", cp.returncode != 0 and "AWS access key" in cp.stderr, cp.stderr)


def scenario_kit_manifest_alignment() -> None:
    print("S8 alignment: init's vendor list is covered by the guard's kit scan surface")
    sys.path.insert(0, str(REPO / "scripts"))
    import bugate_core
    import bugate_init

    uncovered = [
        entry
        for entry in bugate_init.KIT_DIRS
        if not any(entry == k or entry.startswith(k + "/") for k in bugate_core.KIT_LAYOUT)
    ]
    check("every vendored dir is scanned", not uncovered, f"uncovered: {uncovered}")


def scenario_legacy_red_line() -> None:
    print("S7 legacy fixture red line: origin term (read from the fixture) planted in kit -> red")
    term = first_active_legacy_term()
    probe = term.replace(r"\b", "")  # \bword\b -> word
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layout(tmp, with_workspace_config=False)
        (tmp / "scripts/legacy_leak.py").write_text(f"NAME = '{probe}'\n", encoding="utf-8")
        cp = run_guard(tmp, args=("--terms-file", str(LEGACY_FIXTURE)))
        check("legacy origin term is red", cp.returncode != 0 and probe in cp.stderr, cp.stderr)


def main() -> int:
    for scenario in (
        scenario_reverse_kit_seepage,
        scenario_templates_reject_frontmatter,
        scenario_forward_narrative_channels,
        scenario_workspace_territory,
        scenario_industry_terms_delisted,
        scenario_hygiene_everywhere,
        scenario_kit_manifest_alignment,
        scenario_legacy_red_line,
    ):
        scenario()
    if FAILURES:
        print(f"\nde-SUT guard meta-test: {len(FAILURES)} FAILURE(S)")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nde-SUT guard meta-test: PASS (all scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
