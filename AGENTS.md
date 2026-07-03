# BUGate Agent Entry

This repository is the SUT-neutral home of BUGate: an AI-assisted black-box
test analysis and test-case governance framework.

- Keep agent behavior rules here.
- Keep reusable methodology in `docs/qa-methodology/`.
- Keep shared runtime instructions in `.shared/skills/bugate/`.
- Keep SUT-specific source, API dumps, secrets, environments, fixtures, live
  evidence, and project memories outside BUGate core.
- Treat the governed SUT workspace — the host test repo in imported mode, a
  mounted workspace in the maintainer workbench — as the SUT automation test
  framework / test workspace unless a profile explicitly defines a narrower
  evidence boundary.
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
   commands in a SUT profile or the governed test workspace.
3. Treat BUGate artifacts as pre-code governance: business brief, testability
   decision, inventory/oracle mapping, readable test cases, adversarial review,
   execution report, and knowledge update.
4. A test implementation must not be generated before the configured pre-code
   artifacts are present and accepted for that SUT profile.
5. If a rule requires SUT-specific facts, stop at the profile boundary and ask
   for a profile or governed SUT test workspace instead of inventing details.

## Startup

For non-trivial BUGate work:

1. Read `.shared/skills/bugate/SKILL.md`.
2. Read `docs/qa-methodology/METHOD.md` when method rationale is needed.
3. Read `docs/qa-methodology/SOP.md` when execution procedure is needed.
4. Inspect the active SUT profile only if the task explicitly involves a
   governed (imported or mounted) SUT test workspace.

## Hook Policy

Codex and Claude hooks call only SUT-neutral scripts from `scripts/`. Hook
commands must not depend on git metadata: hooks locate the engine by walking up
for `scripts/bugate_core.py`, and gate scripts resolve the governed workspace
root by walking up from CWD to the nearest `bugate.config.yaml`
(`BUGATE_PROJECT_ROOT` overrides; the `AGENTS.md` + `.shared/` sentinel remains
as a workbench fallback).

Changing `.codex/hooks.json` can require Codex Desktop to re-trust the project
hook hash before hooks become active.

## State Discipline

Memory is agent-driven, not automatic. The Stop hook only writes a liveness
heartbeat; a heartbeat is bookkeeping, never a recorded finding.

1. Record progress at milestones, not only at session start or stop. After a
   real decision, finding, completed step, blocker, or handoff, write it through
   the memory bus (`scripts/memory_bus.py note`).
2. Write durable conclusions as `type:decision` or `type:finding` with
   `status:confirmed`; keep working notes, hypotheses, and handoffs as
   `status:draft` until confirmed.
3. If a task changes collaboration state, gate state, current blockers, or the
   next action, persist it immediately rather than waiting for session end.
4. Recall before acting: pull prior context at session start and search the bus
   by topic before re-deriving state from files.

See `docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md` for the full
record / recall / promote loop and the dual (SUT / core) namespace boundary.

## Incident Discipline

1. When a defect or material risk is found, do not only record progress; create
   or update the durable defect record and write a `type:finding` or
   `type:blocker` memory that references it.
2. Distinguish a test-infrastructure defect from a defect in the system under
   test before asking developers to act on it.
3. If a root cause is not evidenced, label it a hypothesis. Do not record a
   speculative root cause as confirmed fact.

## Testing Discipline

The purpose of a test is to find defects, not to manufacture green runs.

1. Do not adapt an assertion to incorrect system-under-test behavior just to
   make it pass.
2. PASS, FAIL, XFAIL, SKIP, and a fake-green run are different signals; preserve
   the distinction between them.
3. For a read-only known defect, prefer `xfail` so a later fix surfaces as an
   unexpected pass. For a known write-side defect that would pollute an
   environment, prefer `skip` with a defect id and an explicit restore
   condition.
4. New business assertions must be evidence-first: read source, contracts, live
   responses, or probe output before encoding an expectation. Do not let an
   agent invent identifiers, addresses, secrets, account handles, or record ids;
   bind them to a profile-declared evidence source instead.
5. Resolve file paths through a project/test helper; do not hard-code fragile
   relative-path math.

## Assertion Precision

1. Avoid broad "any-of-these-exceptions" assertions that hide which layer
   actually failed; assert the exact expected outcome with evidence.
2. Avoid asserting only that an outcome is "not the success code"; assert the
   exact expected status or error with evidence.
3. Distinguish a validation-layer rejection from a business-layer rejection. If
   a request is stopped by an auth, signature, or precondition check before it
   reaches the target layer, the test has not exercised the intended behavior.

## Gate Boundaries

These are SUT-neutral invariants over the pre-code gate flow. A SUT profile may
make them stricter but must not weaken them.

1. `gate_status: passed` must mean implementation (Layer 4) is unblocked. Do not
   hide remaining blockers behind fields such as a "prerequisites" list.
2. Placeholders, deferred probes, and assertion stubs are not valid pass
   conditions for a layer unless the gate explicitly treats them as non-blocking
   and the user accepts that boundary.
3. The structural write-guard check is necessary but not sufficient; a semantic
   review must still confirm evidence, traceability, marker correctness, and
   absence of stale wording before a layer is accepted.
4. User stories, acceptance criteria, personas, assumptions, and risks are
   Layer 1 inputs only; convert them into propositions, business oracles,
   boundaries, states, gaps, and traceability before moving to Layer 2.
5. Do not reuse one use-case identifier for a different requirement. When several
   artifact directories share an identifier, bind a test to the directory whose
   slug exactly matches the test filename; with no exact match, fail closed and
   list the candidates rather than guessing.
6. Treat the recorded design invariants as outranking any individual checklist
   item; a reviewer who wants to relax a check must reconcile it against those
   invariants first.
