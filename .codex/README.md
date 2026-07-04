# Codex Bridge

This directory is the Codex adapter layer for BUGate.

| Path | Role |
|---|---|
| `.codex/config.toml` | Enables project-local hooks. |
| `.codex/hooks.json` | Runs SUT-neutral BUGate guard scripts. |
| `.codex/agents/*.toml` | Codex gate-review agents (brief / testability / inventory), in Codex's native TOML format. This is the single source for the Codex gate agents — the Claude equivalents live as Markdown under `.shared/skills/bugate/adapters/claude/agents/`. |
| `.codex/skills/bugate` | Symlink to `.shared/skills/bugate`. |
| `.codex/skills/bugate-full-check` | Symlink to `.shared/skills/bugate-full-check`. |

Codex reads gate agents from `.codex/agents/` and skills from `.codex/skills/`;
the shared skill tree also carries Codex command adapters (the multi-view and
adversarial dual-CLI procedures) under
`.shared/skills/bugate/adapters/codex/`.

Do not place SUT-specific rules, credentials, product memories, or API snapshots
here.
