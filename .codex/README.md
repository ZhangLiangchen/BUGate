# Codex Bridge

This directory is the Codex adapter layer for BUGate.

| Path | Role |
|---|---|
| `.codex/config.toml` | Enables project-local hooks. |
| `.codex/hooks.json` | Runs SUT-neutral BUGate guard scripts. |
| `.codex/skills/bugate` | Symlink to `.shared/skills/bugate`. |
| `.codex/skills/bugate-full-check` | Symlink to `.shared/skills/bugate-full-check`. |

Do not place SUT-specific rules, credentials, product memories, or API snapshots
here.
