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

Only an actual byte/hash change to `.codex/hooks.json` requires Codex Desktop
to re-trust the new hook hash; a same-byte install/update no-op does not. Any
hook change also requires a **new Codex session** rooted at the governed repo
before the new runtime surface is active. Re-trust does not reload an existing
session. Fresh imports and transactional updates report these two conditions
separately as `codex_hook_hash_changed` and `new_session_required`.

In an imported repo, `bugate_init.py` is fresh-install-only. Existing exact
v0.3.x or pre-lock v0.4.0/v0.4.1 imports bootstrap once with
`scripts/bugate_update.py` from a verified unpacked v0.4.2-or-later release;
keep that external updater through the rollback window. Use the vendored
`.bugate/bin/bugate-update` `status` → `plan` → reviewed `apply` → `verify`
flow (and exact-transaction `rollback` when needed) only when both the
authoritative installed lock (`.bugate/bugate.lock.json`) and executable
launcher exist. The updater refreshes only manifest-owned runtime surfaces;
profile migration remains a separate reviewed change.

The shared skill tree also carries Codex **command-equivalent** adapters (the
multi-view and adversarial dual-CLI procedures) under
`.shared/skills/bugate/adapters/codex/`.

Do not place SUT-specific rules, credentials, product memories, or API snapshots
here.
