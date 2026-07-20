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
  3. **Wiring upgrade** — re-running init against a repo wired by an older
     engine must refresh OUR stale hook entries to the current template while
     never rewriting the repo's own hooks. Mixed entries stay theirs and gain
     a separate canonical BUGate entry; a second pass must be a no-op.

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
    print("S1 scaffold bodies: hygienic, backward-compatible, one active guard contract")
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
    check(
        "profile has exactly one active guarded_path_regex key",
        profile.count("\nguarded_path_regex:") == 1,
        str(profile.count("\nguarded_path_regex:")),
    )
    check(
        "profile explicitly defaults role governance off",
        profile.count("\nrole_governance:") == 1
        and "\nrole_governance:\n  mode: off\n" in profile,
        "expected exactly one active mode: off block",
    )
    check(
        "profile carries a complete commented required-mode migration example",
        all(token in profile for token in (
            "#   mode: required",
            "#   memory_mode: required",
            "#   evidence_dir: 00_role_evidence",
            "#   require_distinct_sessions: true",
            "#         - designer",
            "#         - implementer",
        )),
        "required-mode example is incomplete",
    )


def scenario_root_snippet_contract() -> None:
    print("S2 generated hooks: exact Claude/Codex gates and lifecycle order")
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
        contract = [
            (entry.get("matcher"), [
                next((name for name in (
                    "check_bugate.py",
                    "check_plan_lock.py",
                    "check_agent_role_paths.py",
                    "check_role_evidence.py",
                ) if name in hook["command"]), "<unknown>")
                for hook in entry["hooks"]
            ])
            for entry in blocks["PreToolUse"]
        ]
        expected = (
            [
                ("Edit|Write", [
                    "check_bugate.py",
                    "check_plan_lock.py",
                    "check_role_evidence.py",
                ]),
                ("Read|Edit|Write", ["check_agent_role_paths.py"]),
            ]
            if runtime == "claude"
            else [("apply_patch", [
                "check_bugate.py",
                "check_plan_lock.py",
                "check_agent_role_paths.py",
                "check_role_evidence.py",
            ])]
        )
        check(f"{runtime} exact matcher/gate contract", contract == expected, str(contract))
        start = [h["command"] for entry in blocks["SessionStart"] for h in entry["hooks"]]
        stop = [h["command"] for entry in blocks["Stop"] for h in entry["hooks"]]
        check(
            f"{runtime} SessionStart recalls Memory then reports role state",
            len(start) == 2
            and "memory_bus.py" in start[0]
            and "bugate-role" in start[1]
            and all("--core" not in command for command in start),
            str(start),
        )
        check(
            f"{runtime} Stop is one imported heartbeat using the active role",
            len(stop) == 1
            and "memory_bus.py" in stop[0]
            and " stop " in stop[0]
            and '"${BUGATE_AGENT_ROLE:-agent}"' in stop[0]
            and "--core" not in stop[0],
            str(stop),
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
        role_paths = [e for e in settings["hooks"]["PreToolUse"] if e.get("matcher") == "Read|Edit|Write"]
        check(
            "fresh Claude wiring has the two independent canonical matchers",
            len(pre) == 1
            and len(role_paths) == 1
            and [
                next((name for name in (
                    "check_bugate.py",
                    "check_plan_lock.py",
                    "check_role_evidence.py",
                ) if name in hook["command"]), "<unknown>")
                for hook in pre[0]["hooks"]
            ] == ["check_bugate.py", "check_plan_lock.py", "check_role_evidence.py"]
            and len(role_paths[0]["hooks"]) == 1
            and "check_agent_role_paths.py" in role_paths[0]["hooks"][0]["command"],
            str(settings["hooks"]["PreToolUse"]),
        )
        start = [
            hook["command"]
            for entry in settings["hooks"]["SessionStart"]
            for hook in entry["hooks"]
        ]
        check(
            "fresh SessionStart wires Memory recall before bugate-role",
            len(start) == 2 and "memory_bus.py" in start[0] and "bugate-role" in start[1],
            str(start),
        )
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


def scenario_merge_refreshes_stale_wiring() -> None:
    print("S4 upgrade: stale owned wiring refreshes; SUT and mixed entries are preserved")
    legacy_cmd = 'ROOT="$(legacy-resolver)"; /usr/bin/env python3 "$ROOT/.bugate/scripts/check_bugate.py"'
    own = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo repo-own >/dev/null"}]}
    stale = {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": legacy_cmd}]}
    blocks = bugate_init.hook_blocks(".bugate", "claude")
    merged, added = bugate_init.merge_hooks({"hooks": {"PreToolUse": [own, stale]}}, blocks, ".bugate")
    pre = merged["hooks"]["PreToolUse"]
    check("repo's own entry untouched", pre[0] == own, str(pre[0]))
    cmds = [h["command"] for e in pre for h in e["hooks"] if ".bugate/scripts/" in h["command"]]
    check(
        "stale command replaced by the current guarded template",
        cmds and all('[ -n "$ROOT" ] || exit 0;' in c for c in cmds) and legacy_cmd not in cmds,
        str(cmds)[:200],
    )
    check("refresh reported", any(a.startswith("PreToolUse") and "refreshed" in a for a in added), str(added))
    _, added2 = bugate_init.merge_hooks(merged, blocks, ".bugate")
    check("second pass is a no-op", not added2, str(added2))
    mixed = {"matcher": "Edit|Write", "hooks": [
        {"type": "command", "command": "echo their-wrapper >/dev/null"},
        {"type": "command", "command": legacy_cmd},
    ]}
    merged3, added3 = bugate_init.merge_hooks({"hooks": {"PreToolUse": [mixed]}}, blocks, ".bugate")
    pre3 = merged3["hooks"]["PreToolUse"]
    check(
        "mixed entry remains byte-for-byte first-party SUT wiring",
        pre3[0] == mixed,
        str(pre3[0]),
    )
    check(
        "mixed entry never substitutes for independent canonical wiring",
        pre3[1:] == blocks["PreToolUse"]
        and any(a == "PreToolUse" for a in added3),
        str(added3),
    )
    snapshot = json.dumps(merged3, sort_keys=True)
    merged4, added4 = bugate_init.merge_hooks(merged3, blocks, ".bugate")
    check(
        "mixed upgrade is idempotent on the second pass",
        not added4 and json.dumps(merged4, sort_keys=True) == snapshot,
        str(added4),
    )
    codex_blocks = bugate_init.hook_blocks(".bugate", "codex")
    codex_own = {
        "matcher": "apply_patch",
        "hooks": [{"type": "command", "command": "echo sut-codex-hook >/dev/null"}],
    }
    codex_stale = {
        "matcher": "apply_patch",
        "hooks": [{"type": "command", "command": legacy_cmd}],
    }
    codex_merged, codex_added = bugate_init.merge_hooks(
        {"hooks": {"PreToolUse": [codex_own, codex_stale]}},
        codex_blocks,
        ".bugate",
    )
    codex_pre = codex_merged["hooks"]["PreToolUse"]
    check("SUT Codex hook is preserved on upgrade", codex_pre[0] == codex_own, str(codex_pre))
    check(
        "stale Codex owned hook upgrades to all four guards",
        codex_pre[1:] == codex_blocks["PreToolUse"]
        and any(item.startswith("PreToolUse") for item in codex_added),
        str(codex_pre),
    )
    codex_snapshot = json.dumps(codex_merged, sort_keys=True)
    codex_again, codex_added_again = bugate_init.merge_hooks(
        codex_merged, codex_blocks, ".bugate"
    )
    check(
        "Codex hook upgrade is idempotent",
        not codex_added_again
        and json.dumps(codex_again, sort_keys=True) == codex_snapshot,
        str(codex_added_again),
    )


def scenario_gitignore_backstop() -> None:
    print("S5 .gitignore backstop: marked block written once; SUT's own lines preserved")
    begin = bugate_init.GITIGNORE_BEGIN
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        def run_init(sut: Path) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(REPO / "scripts" / "bugate_init.py"), str(sut)],
                capture_output=True, text=True,
            )

        # (a) fresh init creates .gitignore with the marked block + the scorer defaults.
        fresh = tmp / "fresh"
        fresh.mkdir()
        cp = run_init(fresh)
        check("fresh init succeeds", cp.returncode == 0, cp.stderr[-300:])
        gi = fresh / ".gitignore"
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        check("fresh .gitignore carries the BUGate marker", begin in text, text[:200])
        check(
            "fresh block ignores the scorer defaults",
            all(name in text for name in (
                "/oracle_falsification_result.json",
                "/prd_health_result.json",
                "/prd_health_report.md",
                "/assertion_coverage_matrix.md",
            )),
            text,
        )
        check(
            "committed governance contract is NOT ignored",
            "bugate.config.yaml" not in text and "bugate.profile.yaml" not in text,
            text,
        )

        # (b) re-running init does not duplicate the block.
        cp2 = run_init(fresh)
        check("re-run init succeeds", cp2.returncode == 0, cp2.stderr[-300:])
        text2 = gi.read_text(encoding="utf-8")
        check("re-run does not duplicate the marked block", text2.count(begin) == 1, str(text2.count(begin)))
        check("re-run leaves the .gitignore byte-identical", text2 == text)

        # (c) a pre-existing SUT .gitignore is preserved; our block is appended after it.
        seeded = tmp / "seeded"
        seeded.mkdir()
        own_lines = "node_modules/\n*.log\n/build/\n"
        (seeded / ".gitignore").write_text(own_lines, encoding="utf-8")
        cp3 = run_init(seeded)
        check("seeded init succeeds", cp3.returncode == 0, cp3.stderr[-300:])
        seeded_text = (seeded / ".gitignore").read_text(encoding="utf-8")
        check(
            "SUT's own .gitignore lines are intact",
            all(line in seeded_text for line in ("node_modules/", "*.log", "/build/")),
            seeded_text,
        )
        check("BUGate block is appended (marker present, once)", seeded_text.count(begin) == 1, seeded_text)
        check(
            "BUGate block comes after the SUT's own lines",
            seeded_text.index("node_modules/") < seeded_text.index(begin),
            seeded_text,
        )
        # re-running against the seeded repo is still a no-op.
        run_init(seeded)
        check(
            "seeded re-run does not duplicate the block",
            (seeded / ".gitignore").read_text(encoding="utf-8").count(begin) == 1,
        )


def scenario_codex_agents_installed() -> None:
    print("S6 vendor contents + Codex agents/skills: new role runtime, links, refresh-ours")
    names = {"brief-gate.toml", "testability-gate.toml", "inventory-gate.toml"}
    with tempfile.TemporaryDirectory() as td:
        sut = Path(td) / "sut"
        sut.mkdir()
        cp = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "bugate_init.py"), str(sut)],
            capture_output=True, text=True,
        )
        check("init succeeds", cp.returncode == 0, cp.stderr[-300:])
        agents_dir = sut / ".codex" / "agents"
        present = {p.name for p in agents_dir.glob("*.toml")} if agents_dir.is_dir() else set()
        check("all three gate agents installed", names <= present, str(present))
        brief = (agents_dir / "brief-gate.toml").read_text(encoding="utf-8") if (agents_dir / "brief-gate.toml").exists() else ""
        check(
            "agent references the skill vendor-agnostically via .agents/skills/bugate",
            ".agents/skills/bugate/SKILL.md" in brief and ".shared/skills/bugate/SKILL.md" not in brief,
            brief,
        )
        # The official Codex path resolves the reference, and the legacy bridge
        # stays in place for older clients during the migration window.
        check(
            "the referenced SKILL.md resolves through the installed .agents symlink",
            (sut / ".agents" / "skills" / "bugate" / "SKILL.md").exists(),
        )
        check(
            "legacy .codex skill bridge still resolves",
            (sut / ".codex" / "skills" / "bugate" / "SKILL.md").exists(),
        )
        check(
            "full-check skill resolves for Claude, Codex, and legacy Codex paths",
            all((sut / runtime / "skills" / "bugate-full-check" / "SKILL.md").exists()
                for runtime in (".claude", ".agents", ".codex")),
        )
        check(
            "role-governance scripts are vendored",
            all((sut / ".bugate" / "scripts" / name).is_file() for name in (
                "role_governance.py",
                "check_role_evidence.py",
            )),
        )
        role_bin = sut / ".bugate" / "bin" / "bugate-role"
        check(
            "bugate-role bin is vendored and executable",
            role_bin.is_file() and os.access(role_bin, os.X_OK),
            str(role_bin),
        )
        check(
            "all three shipped skills are vendored",
            all((sut / ".bugate" / ".shared" / "skills" / skill / "SKILL.md").is_file()
                for skill in ("bugate", "bugate-full-check", "bugate-import")),
        )
        # refresh-ours: a SUT-owned agent survives a re-run; ours are refreshed, not duplicated.
        (agents_dir / "sut-own.toml").write_text('name = "sut-own"\n', encoding="utf-8")
        cp2 = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "bugate_init.py"), str(sut)],
            capture_output=True, text=True,
        )
        check("re-run succeeds", cp2.returncode == 0, cp2.stderr[-300:])
        after = {p.name for p in agents_dir.glob("*.toml")}
        check("SUT's own agent is preserved on re-run", "sut-own.toml" in after, str(after))
        check("our agents are not duplicated", after == names | {"sut-own.toml"}, str(after))
        check(
            "re-run leaves one canonical SessionStart entry with both owned commands",
            len(json.loads((sut / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]) == 1,
        )
        # dry-run writes nothing.
        dry = Path(td) / "dry"
        dry.mkdir()
        subprocess.run(
            [sys.executable, str(REPO / "scripts" / "bugate_init.py"), str(dry), "--dry-run"],
            capture_output=True, text=True,
        )
        check("dry-run installs no codex agents", not (dry / ".codex" / "agents").exists())
        check("dry-run installs no skill links", not (dry / ".agents" / "skills").exists())


def main() -> int:
    for scenario in (
        scenario_scaffold_hygiene,
        scenario_root_snippet_contract,
        scenario_wired_hook_inert_without_config,
        scenario_merge_refreshes_stale_wiring,
        scenario_gitignore_backstop,
        scenario_codex_agents_installed,
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
