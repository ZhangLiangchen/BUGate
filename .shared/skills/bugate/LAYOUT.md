# BUGate Skill Layout

| Path | Purpose |
|---|---|
| `SKILL.md` | Main runtime instructions. |
| `references/` | Gate criteria and method invariants. |
| `templates/` | SUT-neutral artifact templates. |
| `adapters/codex/` | Codex command routing adapters (multi-view, adversarial dual-CLI procedures). |
| `adapters/codex/agents/` | Codex gate-review agents (native TOML). Single source: `.codex/agents` symlinks here in this repo; the installer copies them into an imported SUT repo's `.codex/agents/`. |
| `adapters/claude/agents/` | Claude agent routing cards. |
| `adapters/claude/commands/` | Claude command routing cards. |
