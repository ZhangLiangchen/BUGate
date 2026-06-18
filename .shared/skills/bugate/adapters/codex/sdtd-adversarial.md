# Codex Adversarial Adapter

This is a Codex routing adapter only. It is not a second BUGate rule source.

Read `.shared/skills/bugate/SKILL.md`, then read:

- `references/sdtd-constitution.md`
- `references/test-design-gate.md`

## Use When

- The Layer 3 `03_inventory.yaml` and the readable `03a_test_cases.md` are ready.
- The case set needs an exploratory / adversarial red-team pass before any test
  implementation.
- CLI automation should run independent Codex and Claude attack views before
  producing `03b_adversarial_cases.yaml`.

## Codex Procedure

1. Generate the Stage 3B support layout and prompt card:

```bash
python3 scripts/sdtd_adversarial.py init <artifact_dir> --focus "<risk focus>"
```

2. Check CLI availability and the resolved reasoning budget:

```bash
python3 scripts/sdtd_adversarial_cli_bridge.py check-env
```

3. Run the dual-peer adversarial bridge over the active SUT profile's artifact
   directory:

```bash
python3 scripts/sdtd_adversarial_cli_bridge.py run-all <artifact_dir>
```

The bridge keeps this runtime as controller and dispatches `codex` and `claude`
as independent red-team reviewers. Each peer sees only the shared prompt card,
`03_inventory.yaml`, and `03a_test_cases.md`; neither sees the other's view.
Each attacks weak oracles, missing negative paths, ambiguous wording, and
fake-green risk. It writes `00_adversarial/codex_adversarial_view.md`,
`00_adversarial/claude_adversarial_view.md`, and synthesizes
`03b_adversarial_cases.yaml` as `gate_status: pending` for human review. If
either CLI is absent it falls back to deterministic placeholder views and
records that real dispatch was skipped.

Model, reasoning effort, timeout, and proxy injection are read from `SDTD_*`
environment variables with neutral defaults: `SDTD_CODEX_MODEL`,
`SDTD_CLAUDE_MODEL`, `SDTD_CODEX_REASONING_EFFORT`, `SDTD_CLAUDE_EFFORT`,
`SDTD_CODEX_BIN`, `SDTD_CLAUDE_BIN`, `SDTD_CLI_TIMEOUT_SECONDS`, and the
`SDTD_CLI_*_PROXY` set. Empty model/effort means the CLI uses its own default;
proxy injection stays off unless its env vars are set.

4. Check the Stage 3B gate:

```bash
python3 scripts/sdtd_adversarial.py check <artifact_dir>
```

The bridge writes `03b_adversarial_cases.yaml` as `gate_status: pending`, so this
`check` **fails until a human reviews the synthesized additions and sets
`gate_status: passed`**. That failure is the gate working — accept first, then
re-run `check`.

## Boundaries

- Do not pass one peer's chat analysis, interim conclusion, or saved view into
  the other peer's prompt.
- Do not read the other peer view before producing the current peer view.
- Do not mark `03b_adversarial_cases.yaml` as `passed` until selected additions
  are reviewed and absorbed.
- Do not generate test implementation before the configured pre-code artifacts
  are present and accepted for the active SUT profile.
- Do not infer product facts that are absent from the active SUT profile or its
  evidence; stop at the profile boundary and ask for the profile instead.
- If this adapter conflicts with `.shared/skills/bugate/SKILL.md`, the shared
  BUGate skill wins.
