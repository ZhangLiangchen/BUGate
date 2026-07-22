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
    both Claude matchers plus the Codex ``apply_patch`` matcher; workspace-root dialect
    (``bugate.config.yaml`` + ``CLAUDE_PLUGIN_ROOT``).

For every hook command that invokes a BUGate python script, BOTH hardening
properties must be present so the resolver is inert outside a governed tree
instead of blocking every write with a traceback:

  (a) the root resolver uses ``next((...), "")`` — an empty-string default, and
      no bare ``next(`` without one;
  (b) the lazy guard ``[ -n "$ROOT" ] || exit 0`` short-circuits when the
      resolver found nothing.

Each surface must also resolve via its own sentinel dialect, never the other's.
Every command additionally carries one updater-compatible ``BUGATE_HOOK_ID``
prefix.  IDs are stable per runtime/event/matcher entry, shared by all commands
inside that entry, and never reused for another entry in the same JSON file.
The canonical imported fragments and installed projection are checked against
the same shape, and fresh init must delegate to that canonical contract.

Stdlib-only, self-contained: run ``python3 tests/test_hook_surface_parity.py``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_init  # noqa: E402
import bugate_install_contract as install_contract  # noqa: E402

# Empty-string default handed to next(); the lazy guard that consumes it.
EMPTY_DEFAULT = ', ""))'
LAZY_GUARD = '[ -n "$ROOT" ] || exit 0'

# Stable updater-compatible ownership prefix.  A command must carry exactly
# one prefix at byte zero; an ID-shaped token embedded later in a SUT command
# is deliberately not ownership metadata.
HOOK_ID_TOKEN = "BUGATE_HOOK_ID="
HOOK_ID_PREFIX = re.compile(
    r"^BUGATE_HOOK_ID='(bugate(?:\.[a-z][a-z0-9-]*)+\.v[1-9][0-9]*)'; "
    r"export BUGATE_HOOK_ID; "
)

# Exact entry-level ownership contract.  Core runtime surfaces and the plugin
# PreToolUse matchers share the runtime identity for the same logical entry;
# the plugin's single cross-runtime lifecycle entries use plugin-scoped IDs.
EXPECTED_IDENTITIES: dict[str, dict[tuple[str, str | None], str]] = {
    ".claude/settings.json": {
        ("PreToolUse", "Edit|Write"): "bugate.claude.pre.write.v1",
        ("PreToolUse", "Read|Edit|Write"): "bugate.claude.pre.role.v1",
        ("UserPromptSubmit", None): "bugate.claude.prompt.v1",
        ("SessionStart", None): "bugate.claude.session-start.v1",
        ("Stop", None): "bugate.claude.stop.v1",
    },
    ".codex/hooks.json": {
        ("PreToolUse", "apply_patch"): "bugate.codex.pre.write.v1",
        ("UserPromptSubmit", None): "bugate.codex.prompt.v1",
        ("SessionStart", None): "bugate.codex.session-start.v1",
        ("Stop", None): "bugate.codex.stop.v1",
    },
    "hooks/hooks.json": {
        ("PreToolUse", "Edit|Write"): "bugate.claude.pre.write.v1",
        ("PreToolUse", "Read|Edit|Write"): "bugate.claude.pre.role.v1",
        ("PreToolUse", "apply_patch"): "bugate.codex.pre.write.v1",
        ("UserPromptSubmit", None): "bugate.plugin.prompt.v1",
        ("SessionStart", None): "bugate.plugin.session-start.v1",
        ("Stop", None): "bugate.plugin.stop.v1",
    },
}
KNOWN_IDENTITIES = {
    identity
    for surface in EXPECTED_IDENTITIES.values()
    for identity in surface.values()
}

# Script/executable basenames expected somewhere across the surface.
EXPECTED_SCRIPTS = (
    "check_bugate.py",
    "check_plan_lock.py",
    "check_agent_role_paths.py",
    "check_role_evidence.py",
    "bugate_prompt_reminder.py",
    "memory_bus.py",
    "bugate-role",
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


def hook_commands(entry: dict) -> list[str]:
    """Return command strings only when the complete entry is command-shaped."""

    hooks = entry.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        return []
    result: list[str] = []
    for hook in hooks:
        if (
            not isinstance(hook, dict)
            or hook.get("type") != "command"
            or not isinstance(hook.get("command"), str)
        ):
            return []
        result.append(hook["command"])
    return result


def command_identity(command: str) -> str | None:
    """Parse one exact, non-duplicated identity prefix from a command."""

    if command.count(HOOK_ID_TOKEN) != 1:
        return None
    match = HOOK_ID_PREFIX.match(command)
    return match.group(1) if match else None


def recognized_entry_identity(entry: dict, expected_identity: str) -> str | None:
    """Recognize a complete entry only against its expected owned shape."""

    cmds = hook_commands(entry)
    identities = [command_identity(command) for command in cmds]
    if not cmds or any(identity is None for identity in identities):
        return None
    unique = set(identities)
    if len(unique) != 1:
        return None
    identity = unique.pop()
    return (
        identity
        if identity in KNOWN_IDENTITIES and identity == expected_identity
        else None
    )


def event_commands(surface: dict, event: str) -> list[str]:
    return [
        h["command"]
        for entry in surface.get("hooks", {}).get(event, [])
        for h in entry["hooks"]
    ]


def invoked_names(entry: dict) -> list[str]:
    """Return the expected BUGate target basename from each hook command."""

    names: list[str] = []
    for hook in entry.get("hooks", []):
        command = hook["command"]
        matches = [name for name in EXPECTED_SCRIPTS if name in command]
        names.append(matches[-1] if matches else "<unknown>")
    return names


def pretool_contract(surface: dict) -> list[tuple[str | None, list[str]]]:
    return [
        (entry.get("matcher"), invoked_names(entry))
        for entry in surface.get("hooks", {}).get("PreToolUse", [])
    ]


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
    print("S5 exact per-surface/matcher gate and lifecycle contracts")
    claude = parsed.get(".claude/settings.json")
    if claude is not None:
        check(
            ".claude/settings.json: exact Claude gate groups",
            pretool_contract(claude) == [
                ("Edit|Write", [
                    "check_bugate.py",
                    "check_plan_lock.py",
                    "check_role_evidence.py",
                ]),
                ("Read|Edit|Write", ["check_agent_role_paths.py"]),
            ],
            str(pretool_contract(claude)),
        )
        _check_lifecycle(".claude/settings.json", claude, core=True)
    codex = parsed.get(".codex/hooks.json")
    if codex is not None:
        check(
            ".codex/hooks.json: exact Codex apply_patch gates",
            pretool_contract(codex) == [("apply_patch", [
                "check_bugate.py",
                "check_plan_lock.py",
                "check_agent_role_paths.py",
                "check_role_evidence.py",
            ])],
            str(pretool_contract(codex)),
        )
        _check_lifecycle(".codex/hooks.json", codex, core=True)
    plugin = parsed.get("hooks/hooks.json")
    if plugin is not None:
        check(
            "hooks/hooks.json: exact Claude + Codex plugin gate groups",
            pretool_contract(plugin) == [
                ("Edit|Write", [
                    "check_bugate.py",
                    "check_plan_lock.py",
                    "check_role_evidence.py",
                ]),
                ("Read|Edit|Write", ["check_agent_role_paths.py"]),
                ("apply_patch", [
                    "check_bugate.py",
                    "check_plan_lock.py",
                    "check_agent_role_paths.py",
                    "check_role_evidence.py",
                ]),
            ],
            str(pretool_contract(plugin)),
        )
        _check_lifecycle("hooks/hooks.json", plugin, core=False)


def scenario_hook_identities(parsed: dict[str, dict]) -> None:
    print("S6 stable hook ownership identity prefixes and entry mappings")
    logical_identity_shapes: dict[str, set[tuple[str, str, str | None]]] = {}
    for rel, expected in EXPECTED_IDENTITIES.items():
        data = parsed.get(rel)
        if data is None:
            continue
        actual: dict[tuple[str, str | None], str | None] = {}
        entry_keys: list[tuple[str, str | None]] = []
        entry_identities: list[str | None] = []
        for event, entries in data.get("hooks", {}).items():
            for entry in entries:
                key = (event, entry.get("matcher"))
                entry_keys.append(key)
                cmds = hook_commands(entry)
                check(
                    f"{rel}/{event}/{key[1] or '<default>'}: every command has exactly one legal prefix",
                    bool(cmds)
                    and all(command_identity(command) is not None for command in cmds),
                    str(cmds),
                )
                expected_identity = expected.get(key)
                identity = (
                    recognized_entry_identity(entry, expected_identity)
                    if expected_identity is not None
                    else None
                )
                entry_identities.append(identity)
                check(
                    f"{rel}/{event}/{key[1] or '<default>'}: one known ID is shared by the complete entry",
                    identity is not None,
                    str([command_identity(command) for command in cmds]),
                )
                actual[key] = identity

                if rel == ".claude/settings.json":
                    runtime = "claude"
                elif rel == ".codex/hooks.json":
                    runtime = "codex"
                elif event != "PreToolUse":
                    runtime = "plugin"
                elif key[1] == "apply_patch":
                    runtime = "codex"
                else:
                    runtime = "claude"
                if identity is not None:
                    logical_identity_shapes.setdefault(identity, set()).add(
                        (runtime, event, key[1])
                    )

        check(
            f"{rel}: exact event/matcher/identity contract is stable",
            actual == expected and len(entry_keys) == len(expected),
            f"expected={expected!r}, actual={actual!r}, entry_keys={entry_keys!r}",
        )
        check(
            f"{rel}: no event/matcher shape is duplicated",
            len(entry_keys) == len(set(entry_keys)),
            str(entry_keys),
        )
        check(
            f"{rel}: no identity is reused by two entries",
            len(entry_identities) == len(set(entry_identities)),
            str(entry_identities),
        )

    check(
        "an identity maps to only one runtime/event/matcher shape",
        all(len(shapes) == 1 for shapes in logical_identity_shapes.values()),
        str(logical_identity_shapes),
    )


def scenario_identity_negative_controls() -> None:
    print("S7 mixed, embedded, duplicated, or unknown identities are not owned")
    canonical = (
        "BUGATE_HOOK_ID='bugate.claude.pre.write.v1'; "
        "export BUGATE_HOOK_ID; true"
    )
    role = (
        "BUGATE_HOOK_ID='bugate.claude.pre.role.v1'; "
        "export BUGATE_HOOK_ID; true"
    )
    fixtures = {
        "mixed BUGate and SUT commands": {
            "hooks": [
                {"type": "command", "command": canonical},
                {"type": "command", "command": "echo sut-owned"},
            ]
        },
        "different IDs inside one entry": {
            "hooks": [
                {"type": "command", "command": canonical},
                {"type": "command", "command": role},
            ]
        },
        "known ID used for the wrong entry shape": {
            "hooks": [{"type": "command", "command": role}]
        },
        "unknown well-shaped ID": {
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        "BUGATE_HOOK_ID='bugate.unknown.pre.write.v1'; "
                        "export BUGATE_HOOK_ID; true"
                    ),
                }
            ]
        },
        "identity token embedded after a SUT command": {
            "hooks": [
                {"type": "command", "command": "echo sut-owned; " + canonical}
            ]
        },
        "duplicated identity prefix": {
            "hooks": [
                {"type": "command", "command": canonical + "; " + canonical}
            ]
        },
    }
    for name, entry in fixtures.items():
        expected_identity = "bugate.claude.pre.write.v1"
        check(
            f"{name} is not recognized as BUGate-owned",
            recognized_entry_identity(entry, expected_identity) is None,
            str(entry),
        )


def scenario_installed_hook_contract() -> None:
    print("S8 fresh-init and installed-projection hook contract parity")
    expected_by_runtime = {
        "claude": EXPECTED_IDENTITIES[".claude/settings.json"],
        "codex": EXPECTED_IDENTITIES[".codex/hooks.json"],
    }
    target_by_runtime = {
        "claude": ".claude/settings.json",
        "codex": ".codex/hooks.json",
    }
    expected_projection: dict[str, tuple[str, str, dict]] = {}

    for runtime, expected in expected_by_runtime.items():
        fragments = install_contract.hook_fragments(".bugate", runtime)
        fragment_surface = {"hooks": fragments}
        check(
            f"fresh init delegates to the canonical {runtime} hook contract",
            bugate_init.hook_blocks(".bugate", runtime) == fragments,
            "bugate_init.hook_blocks drifted from install_contract.hook_fragments",
        )
        actual: dict[tuple[str, str | None], str | None] = {}
        entry_keys: list[tuple[str, str | None]] = []
        entry_ids: list[str | None] = []
        for event, entries in fragments.items():
            for entry in entries:
                key = (event, entry.get("matcher"))
                entry_keys.append(key)
                expected_identity = expected.get(key)
                identity = (
                    recognized_entry_identity(entry, expected_identity)
                    if expected_identity is not None
                    else None
                )
                actual[key] = identity
                entry_ids.append(identity)
                if identity is not None:
                    expected_projection[identity] = (runtime, event, entry)

        check(
            f"installed {runtime}: exact event/matcher/identity contract",
            actual == expected and len(entry_keys) == len(expected),
            f"expected={expected!r}, actual={actual!r}",
        )
        check(
            f"installed {runtime}: no entry shape or identity is duplicated",
            len(entry_keys) == len(set(entry_keys))
            and len(entry_ids) == len(set(entry_ids)),
            f"keys={entry_keys!r}, ids={entry_ids!r}",
        )

        cmds = commands(fragment_surface)
        check(
            f"installed {runtime}: every command keeps rooted lazy resolution",
            bool(cmds)
            and all(
                EMPTY_DEFAULT in command
                and LAZY_GUARD in command
                and "bugate.config.yaml" in command
                and "BUGATE_PROJECT_ROOT" in command
                and '"$ROOT/.bugate/' in command
                and "BUGATE_ENGINE_ROOT" not in command
                and "CLAUDE_PLUGIN_ROOT" not in command
                for command in cmds
            ),
            str(cmds),
        )

        if runtime == "claude":
            expected_pretool = [
                (
                    "Edit|Write",
                    [
                        "check_bugate.py",
                        "check_plan_lock.py",
                        "check_role_evidence.py",
                    ],
                ),
                ("Read|Edit|Write", ["check_agent_role_paths.py"]),
            ]
        else:
            expected_pretool = [
                (
                    "apply_patch",
                    [
                        "check_bugate.py",
                        "check_plan_lock.py",
                        "check_agent_role_paths.py",
                        "check_role_evidence.py",
                    ],
                )
            ]
        check(
            f"installed {runtime}: exact ordered PreToolUse commands",
            pretool_contract(fragment_surface) == expected_pretool,
            str(pretool_contract(fragment_surface)),
        )
        _check_lifecycle(f"installed {runtime}", fragment_surface, core=False)

    projection = install_contract._hook_projection(".bugate")
    try:
        install_contract.validate_installed_projection(projection)
    except install_contract.ContractError as exc:
        check("installed hook projection validates", False, str(exc))
    else:
        check("installed hook projection validates", True)
    by_identity = {
        item.get("hook_identity"): item
        for item in projection
        if item.get("scope") == "shared_json_fragment"
    }
    check(
        "installed projection contains exactly one item per canonical hook identity",
        set(by_identity) == set(expected_projection)
        and len(by_identity) == len(projection),
        f"expected={sorted(expected_projection)!r}, actual={sorted(by_identity)!r}",
    )
    for identity, (runtime, event, entry) in expected_projection.items():
        item = by_identity.get(identity, {})
        semantic_value = {"event": event, "value": entry}
        check(
            f"projection {identity}: target/event/value/digest are exact",
            item.get("id") == f"hook:{identity}"
            and item.get("runtime") == runtime
            and item.get("target_path") == target_by_runtime[runtime]
            and item.get("event") == event
            and item.get("value") == entry
            and item.get("semantic_digest")
            == install_contract.semantic_digest(semantic_value),
            str(item),
        )


def _check_lifecycle(rel: str, surface: dict, *, core: bool) -> None:
    start = event_commands(surface, "SessionStart")
    stop = event_commands(surface, "Stop")
    reminder = event_commands(surface, "UserPromptSubmit")
    check(
        f"{rel}: SessionStart orders best-effort recall before role status",
        len(start) == 2
        and "memory_bus.py" in start[0]
        and " session-start " in start[0]
        and "bugate-role" in start[1]
        and start[1].endswith(" session-start"),
        str(start),
    )
    check(
        f"{rel}: SessionStart memory namespace is {'core' if core else 'imported'}",
        len(start) == 2
        and (("--core" in start[0]) is core)
        and "--core" not in start[1],
        str(start),
    )
    check(
        f"{rel}: Stop is heartbeat-only and role-aware",
        len(stop) == 1
        and "memory_bus.py" in stop[0]
        and " stop " in stop[0]
        and '"${BUGATE_AGENT_ROLE:-agent}"' in stop[0]
        and (("--core" in stop[0]) is core)
        and all(token not in stop[0] for token in ("handoff", "accept", "complete")),
        str(stop),
    )
    check(
        f"{rel}: prompt reminder remains a single independent hook",
        len(reminder) == 1 and "bugate_prompt_reminder.py" in reminder[0],
        str(reminder),
    )


def scenario_expected_scripts(parsed: dict[str, dict]) -> None:
    print("S9 the expected guard scripts are wired somewhere across the surface")
    blob = " ".join(c for data in parsed.values() for c in commands(data))
    for script in EXPECTED_SCRIPTS:
        check(f"'{script}' is invoked by some surface", script in blob, "not found in any command")


def main() -> int:
    parsed = scenario_files_parse()
    scenario_hardening(parsed)
    scenario_engine_dialect(parsed)
    scenario_plugin_dialect(parsed)
    scenario_matchers_and_core(parsed)
    scenario_hook_identities(parsed)
    scenario_identity_negative_controls()
    scenario_installed_hook_contract()
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
