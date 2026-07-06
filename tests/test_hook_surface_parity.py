#!/usr/bin/env python3
"""Hook-surface parity: the hand-maintained hook-JSON files agree.

BUGate wires the same guards through separately edited surfaces, in two
root-resolution dialects. This meta-test pins the cross-surface contract that
already drifted once (the F2 regression, where a hardening fix landed on one
surface but not the others):

  * ``.claude/settings.json``      — engine repo; engine-root dialect;
    ``SessionStart``/``Stop`` carry the ``--core`` memory flag.
  * ``.codex/hooks.json``          — engine repo; ``apply_patch`` matcher;
    engine-root dialect; ``SessionStart``/``Stop`` carry the ``--core``
    memory flag.
  * ``hooks/hooks.json``           — plugin channel for both runtimes;
    ``Edit|Write`` and ``apply_patch`` matchers; workspace-root dialect
    (``bugate.config.yaml`` + ``CLAUDE_PLUGIN_ROOT``).

For every hook command that invokes a BUGate python script, BOTH hardening
properties must be present so the resolver is inert outside a governed tree
instead of blocking every write with a traceback:

  (a) the root resolver uses ``next((...), "")`` — an empty-string default, and
      no bare ``next(`` without one;
  (b) the lazy guard ``[ -n "$ROOT" ] || exit 0`` short-circuits when the
      resolver found nothing.

Each surface must also resolve via its own sentinel dialect, never the other's.

Stdlib-only, self-contained: run ``python3 tests/test_hook_surface_parity.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Empty-string default handed to next(); the lazy guard that consumes it.
EMPTY_DEFAULT = ', ""))'
LAZY_GUARD = '[ -n "$ROOT" ] || exit 0'

# Script basenames the wiring is expected to invoke somewhere across the surface.
EXPECTED_SCRIPTS = (
    "check_bugate.py",
    "check_plan_lock.py",
    "check_agent_role_paths.py",
    "bugate_prompt_reminder.py",
    "memory_bus.py",
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def commands(surface: dict) -> list[str]:
    """Flatten every hook command string out of a parsed hook-JSON surface."""
    return [
        h["command"]
        for entries in surface.get("hooks", {}).values()
        for entry in entries
        for h in entry["hooks"]
    ]


def matchers(surface: dict) -> list[str]:
    return [e.get("matcher") for e in surface.get("hooks", {}).get("PreToolUse", [])]


def scenario_files_parse() -> dict[str, dict]:
    print("S1 the hook surfaces parse as JSON and carry hook commands")
    parsed: dict[str, dict] = {}
    for rel in (".claude/settings.json", ".codex/hooks.json", "hooks/hooks.json"):
        path = REPO / rel
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - fail-closed
            check(f"{rel} parses as JSON", False, str(exc))
            continue
        cmds = commands(data)
        check(f"{rel} parses and has hook commands", bool(cmds), f"{len(cmds)} commands")
        parsed[rel] = data
    return parsed


def scenario_hardening(parsed: dict[str, dict]) -> None:
    print("S2 every hook command is hardened: next() empty default + lazy guard")
    for rel, data in parsed.items():
        cmds = commands(data)
        check(
            f"{rel}: every command carries the empty-string next() default",
            cmds and all(EMPTY_DEFAULT in c for c in cmds),
            f"missing in {[c[:60] for c in cmds if EMPTY_DEFAULT not in c]}",
        )
        # No bare next( that lacks a default: every next( is matched by ", "")).
        check(
            f"{rel}: no bare next( without a default",
            all(c.count("next(") == c.count(EMPTY_DEFAULT) for c in cmds),
            "a next( has no matching empty-string default",
        )
        check(
            f'{rel}: every command carries the lazy guard [ -n "$ROOT" ] || exit 0',
            cmds and all(LAZY_GUARD in c for c in cmds),
            f"missing in {[c[:60] for c in cmds if LAZY_GUARD not in c]}",
        )


def scenario_engine_dialect(parsed: dict[str, dict]) -> None:
    print("S3 engine surfaces resolve via scripts/bugate_core.py + BUGATE_ENGINE_ROOT")
    for rel in (".claude/settings.json", ".codex/hooks.json"):
        data = parsed.get(rel)
        if data is None:
            continue
        cmds = commands(data)
        # The resolver walks up for the scripts/bugate_core.py sentinel via the
        # Path-division idiom (c/"scripts"/"bugate_core.py"), so the segments
        # appear quoted rather than as one contiguous "scripts/bugate_core.py".
        check(
            f"{rel}: resolves via the scripts/bugate_core.py sentinel",
            all('"scripts"/"bugate_core.py"' in c for c in cmds),
            "an engine command does not reference the bugate_core.py sentinel",
        )
        check(
            f"{rel}: overrides via BUGATE_ENGINE_ROOT",
            all("BUGATE_ENGINE_ROOT" in c for c in cmds),
            "an engine command does not reference BUGATE_ENGINE_ROOT",
        )
        blob = " ".join(cmds)
        check(
            f"{rel}: uses the engine dialect only (no plugin sentinel)",
            "bugate.config.yaml" not in blob and "CLAUDE_PLUGIN_ROOT" not in blob,
            "engine surface leaked a workspace/plugin sentinel",
        )


def scenario_plugin_dialect(parsed: dict[str, dict]) -> None:
    print("S4 plugin surface resolves via bugate.config.yaml + BUGATE_PROJECT_ROOT + CLAUDE_PLUGIN_ROOT")
    data = parsed.get("hooks/hooks.json")
    if data is not None:
        cmds = commands(data)
        check(
            "plugin: resolves via bugate.config.yaml",
            all("bugate.config.yaml" in c for c in cmds),
            "a plugin command does not reference bugate.config.yaml",
        )
        check(
            "plugin: overrides via BUGATE_PROJECT_ROOT",
            all("BUGATE_PROJECT_ROOT" in c for c in cmds),
            "a plugin command does not reference BUGATE_PROJECT_ROOT",
        )
        check(
            "plugin: invokes the engine via the CLAUDE_PLUGIN_ROOT variable",
            all("${CLAUDE_PLUGIN_ROOT}" in c for c in cmds),
            "a plugin command does not call ${CLAUDE_PLUGIN_ROOT}",
        )
        blob = " ".join(cmds)
        check(
            "plugin: uses the workspace dialect only (no engine sentinel)",
            "bugate_core.py" not in blob and "BUGATE_ENGINE_ROOT" not in blob,
            "plugin surface leaked an engine sentinel",
        )


def scenario_matchers_and_core(parsed: dict[str, dict]) -> None:
    print("S5 per-surface specifics: matchers and the --core memory flag")
    claude = parsed.get(".claude/settings.json")
    if claude is not None:
        check(
            ".claude/settings.json: PreToolUse matcher is Edit|Write",
            matchers(claude) == ["Edit|Write"],
            str(matchers(claude)),
        )
        # SessionStart/Stop carry the --core flag (engine repo governs core memory).
        for event in ("SessionStart", "Stop"):
            cmds = [
                h["command"]
                for entry in claude["hooks"].get(event, [])
                for h in entry["hooks"]
            ]
            check(
                f".claude/settings.json: {event} carries the --core flag",
                cmds and all("--core" in c for c in cmds),
                f"{event} commands: {cmds}",
            )
    codex = parsed.get(".codex/hooks.json")
    if codex is not None:
        check(
            ".codex/hooks.json: PreToolUse matcher is apply_patch",
            matchers(codex) == ["apply_patch"],
            str(matchers(codex)),
        )
        for event in ("SessionStart", "Stop"):
            cmds = [
                h["command"]
                for entry in codex["hooks"].get(event, [])
                for h in entry["hooks"]
            ]
            check(
                f".codex/hooks.json: {event} carries the --core flag",
                cmds and all("--core" in c for c in cmds),
                f"{event} commands: {cmds}",
            )
    plugin = parsed.get("hooks/hooks.json")
    if plugin is not None:
        check(
            "hooks/hooks.json: PreToolUse matchers cover Claude and Codex",
            matchers(plugin) == ["Edit|Write", "apply_patch"],
            str(matchers(plugin)),
        )


def scenario_expected_scripts(parsed: dict[str, dict]) -> None:
    print("S6 the expected guard scripts are wired somewhere across the surface")
    blob = " ".join(c for data in parsed.values() for c in commands(data))
    for script in EXPECTED_SCRIPTS:
        check(f"'{script}' is invoked by some surface", script in blob, "not found in any command")


def main() -> int:
    parsed = scenario_files_parse()
    scenario_hardening(parsed)
    scenario_engine_dialect(parsed)
    scenario_plugin_dialect(parsed)
    scenario_matchers_and_core(parsed)
    scenario_expected_scripts(parsed)
    if FAILURES:
        print(f"\nhook surface parity: FAIL ({len(FAILURES)})")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nhook surface parity: PASS (all scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
