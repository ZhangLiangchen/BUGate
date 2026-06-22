# BUGate Agent Entry

This repository is the SUT-neutral home of BUGate: an AI-assisted black-box
test analysis and test-case governance framework.

- Keep agent behavior rules here.
- Keep reusable methodology in `docs/qa-methodology/`.
- Keep shared runtime instructions in `.shared/skills/bugate/`.
- Keep SUT-specific source, API dumps, secrets, environments, fixtures, live
  evidence, and project memories outside BUGate core.
- Treat a mounted SUT as the SUT automation test framework / test workspace
  unless a profile explicitly defines a narrower evidence boundary.
- `CLAUDE.md` must remain a symlink to this file.

## Roles

| Path | Role |
|---|---|
| `AGENTS.md` | Shared agent protocol for Codex and Claude. |
| `CLAUDE.md` | Symlink to `AGENTS.md`. |
| `bugate.config.yaml` | Core default config. SUT profiles override it. |
| `docs/qa-methodology/` | SUT-neutral method and operating guidance. |
| `.shared/skills/bugate/` | Shared BUGate skill, references, templates, and adapters. |
| `.codex/` | Codex-only hook and skill discovery bridge. |
| `.claude/` | Claude-only hook and skill discovery bridge. |

## Core Rules

1. Do not add SUT source code, product API snapshots, environment secrets,
   credentials, generated caches, or project-specific fixtures to BUGate core.
2. Put SUT paths, resource policies, environment names, auth rules, and tool
   commands in a SUT profile or mounted test workspace.
3. Treat BUGate artifacts as pre-code governance: business brief, testability
   decision, inventory/oracle mapping, readable test cases, adversarial review,
   execution report, and knowledge update.
4. A test implementation must not be generated before the configured pre-code
   artifacts are present and accepted for that SUT profile.
5. If a rule requires SUT-specific facts, stop at the profile boundary and ask
   for a profile or mounted SUT test workspace instead of inventing details.

## Startup

For non-trivial BUGate work:

1. Read `.shared/skills/bugate/SKILL.md`.
2. Read `docs/qa-methodology/METHOD.md` when method rationale is needed.
3. Read `docs/qa-methodology/SOP.md` when execution procedure is needed.
4. Inspect the active SUT profile only if the task explicitly involves a
   mounted SUT test workspace.

## Hook Policy

Codex and Claude hooks call only SUT-neutral scripts from `scripts/`. Hook
commands must not depend on git metadata; scripts resolve the repository root by
walking up to `AGENTS.md` and `.shared/`.

Changing `.codex/hooks.json` can require Codex Desktop to re-trust the project
hook hash before hooks become active.
