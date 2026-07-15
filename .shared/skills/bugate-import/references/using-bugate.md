# Using BUGate After Import

[English](using-bugate.md) | [简体中文](using-bugate.zh-CN.md)

The operator's guide: BUGate is imported into your SUT automation test repo —
how do you actually work with it day to day in Claude Code / Codex? Five
minutes to your first governed use case. (`.bugate` below = your vendor dir.)

## 0. Open the right directory, once per session

Open the **SUT test repo itself** (the directory holding `bugate.config.yaml`)
as the project root in Claude Code or Codex. Hooks load from the session's
workspace — a session opened at a parent directory carries **no physical
guard**. Codex only: after any hook change, re-trust the hook hash when Codex
Desktop prompts, or the hooks stay silently inactive.

## 1. The working loop for a NEW requirement

Drop the requirement material (PRD, dev handoff notes, interview answers)
somewhere in the repo, then tell the agent what you want in plain language:

> Use BUGate: start a new use case `UC-<AREA>-<NN>-<slug>` for <requirement>,
> fill the pre-code artifacts from the PRD at <path>, then run the
> orchestrator `--auto` and stop at the human checkpoints.

What the agent should do (and the gates verify):

1. **Author the pre-code artifacts** under `docs/usecases/UC-<...>/` —
   business brief (01), testability (02), case inventory (03). The orchestrator
   scaffolds any missing file from kit templates.
2. **Run the full pre-code chain**:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/UC-<...> --auto
   ```

   `--auto` = dual-agent multiview review (real Codex + Claude peers) →
   Layer 1/2/3 semantic gates → readable-case generation (03a) → dual-agent
   adversarial review (3B, writes `03b` as a `pending` skeleton) → full
   contract check → fail-closed degraded-review check. Steps short-circuit:
   the first failing gate stops the chain.
3. **Human checkpoint (yours, not the agent's)**: read
   `00_multiview/divergence_report.md` and the adversarial views, refine the
   artifacts if the reviews found real gaps, then accept `03b` by setting its
   `gate_status: passed`. Until every required artifact declares `passed`,
   the write guard physically blocks test code for this UC — that is the
   design, not an error.
4. **Let the agent implement Layer 4**: once the artifacts pass, the guard
   admits edits to that UC's test file (and only that UC's). The agent writes
   the tests against the inventory's cases and oracles.
5. **Run the tests, then close the loop**:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/UC-<...> \
     --auto --scope post-run --pytest-log <run.log> \
     --command "<exact test command>" --env <env> --exit-code <rc>
   ```

   This classifies the run (self-healing verdict) and regenerates the
   execution report (04) and knowledge update (05) as drafts. **Back up
   human-authored 04/05 first — post-run overwrites them**; merge history
   back in, adjudicate the self-healing verdict, and set the final
   `gate_status` honestly (an open SUT defect means `failed` stays).

## 2. Command quick reference

| Intent | Command |
|---|---|
| UC status at a glance | `python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC>` |
| Full pre-code chain | `... <UC> --auto` |
| Preserve a curated 03b (skip re-review, loudly logged) | `... <UC> --auto --skip-peer-review` |
| Check whether a test file is admitted | `python3 .bugate/scripts/check_bugate.py <test-path> </dev/null` (0 = admitted, 2 = blocked) |
| One-shot capability self-check | `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke` |
| Recall / record cross-session memory | `.bugate/bin/memory-recent --agent <role>` / `python3 .bugate/scripts/memory_bus.py note ...` |

Peer-dispatch environment knobs (when the machine needs them):
`SDTD_CLI_HTTPS_PROXY`/`SDTD_CLI_HTTP_PROXY`/`SDTD_CLI_ALL_PROXY` (proxy for
the spawned peer CLIs only), `SDTD_CLAUDE_MODEL`/`SDTD_CODEX_MODEL` +
`SDTD_CLAUDE_EFFORT`/`SDTD_CODEX_REASONING_EFFORT` (pin review quality),
`SDTD_CLI_TIMEOUT_SECONDS`. A committed repo-local wrapper that exports these
and execs the orchestrator is a good pattern.

## 3. What stays human

- Accepting `03b_adversarial_cases.yaml` (and re-accepting it after every
  `--auto`, which deliberately re-skeletons it to `pending`).
- Adjudicating the self-healing verdict when logs contain polling vocabulary,
  and signing off 04/05 content.
- Deciding defect vs. intended behavior; incidents and their closure.
- Anything the gates refuse: the answer is to fix artifacts or environment,
  never to lower a gate for green.

## 4. Anti-patterns the gates will catch (working as designed)

- Asking the agent to "just write the test" for a UC with no passed
  artifacts → blocked, exit 2, missing-artifact list.
- Editing a guarded test after `--auto` demoted its 03b → blocked until the
  human re-accepts.
- Treating a degraded peer review (exit 3) as green — classify the failing
  peer (environment/kit/SUT) instead; `--allow-degraded-peer-review` is for
  explicitly accepted placeholder reviews only.
- "Fixing" a `cannot bind to a UC artifact dir` block by weakening the regex —
  that message means the binding is wrong; see the vendored `bugate-import`
  skill for the adaptation rules.

## 5. Where the deeper docs live

- Layout adaptation (non-default frameworks/naming): this skill's
  `SKILL.md` (one directory up).
- Operations & diagnosis (peer dispatch failures, overwrite semantics, copy
  hygiene): `field-guide.md` (this directory).
- Gate criteria and artifact contracts: `.bugate/.shared/skills/bugate/`
  (SKILL.md + references/).
