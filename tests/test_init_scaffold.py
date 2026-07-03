#!/usr/bin/env python3
"""Installer scaffold + hook-wiring acceptance on ephemeral fixtures.

Regressions pinned by the 2026-07 import-readiness review:

  1. **Scaffold hygiene** — the config/profile bodies `bugate_init.py` writes
     must carry no control characters: the `sut_identity_terms` example must
     reach the file as a literal ``\\b`` (backslash + b), never as a 0x08
     backspace (a non-raw Python string once ate it), because the simple YAML
     parser does not unescape and users copy this line verbatim.
  2. **Hook inertness** — the vendored hook command must degrade exactly like
     the plugin channel: in a CWD with no ``bugate.config.yaml`` ancestor it
     exits 0 (inert) instead of hard-blocking every write with a resolver
     traceback (`next()` needs its default, plus the ``[ -n "$ROOT" ]`` guard).

Stdlib-only, self-contained: run ``python3 tests/test_init_scaffold.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import bugate_init  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def scenario_scaffold_hygiene() -> None:
    print("S1 scaffold bodies: no control characters, identity-term example survives literally")
    profile = bugate_init.PROFILE_SCAFFOLD.format(vendor_dir=".bugate", name="probe")
    config = bugate_init.CONFIG_SCAFFOLD.format(vendor_dir=".bugate")
    for label, body in (("profile", profile), ("config", config)):
        bad = sorted({c for c in body if ord(c) < 0x20 and c != "\n"})
        check(f"{label} scaffold is control-char free", not bad, f"found {bad!r}")
    check(
        "identity-term example is a literal backslash-b regex",
        "\\bmy-product-name\\b" in profile,
        "expected raw \\b...\\b in the sut_identity_terms comment",
    )


def scenario_root_snippet_contract() -> None:
    print("S2 hook ROOT snippet: next() default + lazy guard (plugin-channel parity)")
    snippet = bugate_init._ROOT_SNIPPET
    check("next() carries an empty-string default", ', ""))' in snippet, snippet)
    check('lazy guard [ -n "$ROOT" ] || exit 0 present', '[ -n "$ROOT" ] || exit 0;' in snippet, snippet)
    for runtime in ("claude", "codex"):
        blocks = bugate_init.hook_blocks(".bugate", runtime)
        cmds = [h["command"] for entries in blocks.values() for e in entries for h in e["hooks"]]
        check(
            f"every {runtime} hook command is lazy-guarded",
            cmds and all('[ -n "$ROOT" ] || exit 0;' in c for c in cmds),
            f"{len(cmds)} commands",
        )


def scenario_wired_hook_inert_without_config() -> None:
    print("S3 behavioral: an initialized repo's hook command exits 0 in a config-less CWD")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sut = tmp / "sut"
        sut.mkdir()
        cp = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "bugate_init.py"), str(sut)],
            capture_output=True, text=True,
        )
        check("bugate init succeeds", cp.returncode == 0, cp.stderr[-300:])
        settings = json.loads((sut / ".claude" / "settings.json").read_text(encoding="utf-8"))
        pre = [e for e in settings["hooks"]["PreToolUse"] if e.get("matcher") == "Edit|Write"]
        command = pre[0]["hooks"][0]["command"]
        scaffold = (sut / "bugate.profile.yaml").read_bytes()
        check("written profile carries no 0x08 byte", b"\x08" not in scaffold)
        nowhere = tmp / "nowhere"
        nowhere.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("BUGATE_")}
        hook = subprocess.run(
            ["sh", "-c", command],
            cwd=nowhere, env=env,
            input='{"tool_input":{"file_path":"x.py"}}',
            capture_output=True, text=True,
        )
        check(
            "hook is inert (rc 0, silent) outside any governed workspace",
            hook.returncode == 0 and not hook.stderr.strip(),
            f"rc={hook.returncode} stderr={hook.stderr[:200]!r}",
        )
        inside = subprocess.run(
            ["sh", "-c", command],
            cwd=sut, env=env,
            input='{"tool_input":{"file_path":"x.py"}}',
            capture_output=True, text=True,
        )
        check(
            "hook still runs the guard inside the workspace (rc 0: no guards configured yet)",
            inside.returncode == 0,
            f"rc={inside.returncode} stderr={inside.stderr[:200]!r}",
        )


def main() -> int:
    for scenario in (
        scenario_scaffold_hygiene,
        scenario_root_snippet_contract,
        scenario_wired_hook_inert_without_config,
    ):
        scenario()
    if FAILURES:
        print(f"\ninit scaffold acceptance: FAIL ({len(FAILURES)})")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\ninit scaffold acceptance: PASS (all scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
