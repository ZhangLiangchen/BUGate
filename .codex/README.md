# Codex Bridge

This directory is the Codex adapter layer for BUGate.

| Path | Role |
|---|---|
| `.codex/config.toml` | Enables project-local hooks. |
| `.codex/hooks.json` | Runs SUT-neutral BUGate guard scripts. |
| `.codex/agents` | Codex gate-review agents (brief / testability / inventory). In **this** repo it is a symlink into the kit at `.shared/skills/bugate/adapters/codex/agents/`. In an **imported** SUT repo the installer copies the TOMLs here as committed files so each SUT repo reviews and versions the exact gate-agent cards it uses. |
| `.agents/skills/bugate` | Official Codex repo-skill symlink to `.shared/skills/bugate`. |
| `.agents/skills/bugate-full-check` | Official Codex repo-skill symlink to `.shared/skills/bugate-full-check`. |
| `.codex/skills/*` | Legacy Codex compatibility symlinks. Keep them while older clients still read this path, but do not treat it as the canonical path. |
| `.codex-plugin/plugin.json` | Codex plugin manifest; plugin-root `skills/` and `hooks/hooks.json` carry the shared skill and lifecycle hooks. |

Codex reads gate agents from `.codex/agents/` and skills from `.agents/skills/`.
The agent TOMLs reference the skill through the `.agents/skills/bugate` symlink,
so one file resolves both here and in any imported SUT repo, whatever its vendor
dir. The `.codex/skills` symlinks are kept only as a migration bridge.

Changing `.codex/hooks.json` requires a one-time re-trust of the changed hook
hash in Codex Desktop before Codex hooks become active.

The shared skill tree also carries Codex **command-equivalent** adapters (the
multi-view and adversarial dual-CLI procedures) under
`.shared/skills/bugate/adapters/codex/`.

Do not place SUT-specific rules, credentials, product memories, or API snapshots
here.
