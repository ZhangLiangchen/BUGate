# Codex Bridge

This directory is the Codex adapter layer for BUGate.

| Path | Role |
|---|---|
| `.codex/config.toml` | Enables project-local hooks. |
| `.codex/hooks.json` | Runs SUT-neutral BUGate guard scripts. |
| `.codex/agents` | Codex gate-review agents (brief / testability / inventory). In **this** repo it is a symlink into the kit at `.shared/skills/bugate/adapters/codex/agents/` (the single source, mirroring how `.claude/agents` links into `adapters/claude/agents/`). In an **imported** SUT repo the installer copies the TOMLs here as committed files — Codex plugins package skills/hooks/MCP but not custom agents, so the installer is Codex's agent channel; the Claude equivalents load via the plugin. |
| `.codex/skills/bugate` | Symlink to `.shared/skills/bugate`. |
| `.codex/skills/bugate-full-check` | Symlink to `.shared/skills/bugate-full-check`. |

Codex reads gate agents from `.codex/agents/` and skills from `.codex/skills/`.
The agent TOMLs reference the skill through the `.codex/skills/bugate` symlink,
so one file resolves both here and in any imported SUT repo, whatever its vendor
dir. The shared skill tree also carries Codex **command** adapters (the
multi-view and adversarial dual-CLI procedures) under
`.shared/skills/bugate/adapters/codex/`.

Do not place SUT-specific rules, credentials, product memories, or API snapshots
here.
