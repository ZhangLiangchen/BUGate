# Codex Multi-View Adapter

This is a Codex routing adapter only. It is not a second BUGate rule source.

Read `.shared/skills/bugate/SKILL.md`, then read:

- `references/sdtd-constitution.md`
- `references/business-understanding-gate.md`

## Use When

- A requirement needs two independent Wave 1 readings before the Layer 1 brief
  is accepted.
- The user wants independent Codex and Claude views plus a checkable divergence
  report over the `01_business_brief.md` draft.
- CLI automation should reuse the shared multi-view prompt card instead of new
  prompts.

## Codex Procedure

1. Generate the multi-view support layout and prompt card:

```bash
python3 scripts/sdtd_multiview.py init <artifact_dir> --focus "<Wave 1 focus>"
```

2. Check CLI availability and the resolved reasoning budget:

```bash
python3 scripts/sdtd_multiview_cli_bridge.py check-env
```

3. Run the dual-peer bridge over the active SUT profile's artifact directory:

```bash
python3 scripts/sdtd_multiview_cli_bridge.py run-all <artifact_dir>
```

The bridge keeps this runtime as controller and dispatches `codex` and `claude`
as independent peer workers. Each peer sees only the shared prompt card and the
Layer 1 brief draft; neither sees the other's view. It writes
`00_multiview/codex_view.md`, `00_multiview/claude_view.md`, and
`00_multiview/divergence_report.md`. If either CLI is absent it falls back to
deterministic placeholder views and records that real dispatch was skipped.

Model, reasoning effort, timeout, and proxy injection are read from `SDTD_*`
environment variables with neutral defaults: `SDTD_CODEX_MODEL`,
`SDTD_CLAUDE_MODEL`, `SDTD_CODEX_REASONING_EFFORT`, `SDTD_CLAUDE_EFFORT`,
`SDTD_CODEX_BIN`, `SDTD_CLAUDE_BIN`, `SDTD_CLI_TIMEOUT_SECONDS`, and the
`SDTD_CLI_*_PROXY` set. Empty model/effort means the CLI uses its own default;
proxy injection stays off unless its env vars are set.

4. If the divergence report records `layer1_update_required: yes`, absorb the
   missing propositions into `01_business_brief.md`, then re-synthesize:

```bash
python3 scripts/sdtd_multiview_cli_bridge.py run-divergence <artifact_dir> --force
```

5. Check the Wave 1 gate:

```bash
python3 scripts/sdtd_multiview.py check <artifact_dir>
```

## Boundaries

- Do not edit `01_business_brief.md` or downstream layer artifacts while
  generating the two views; brief absorption is a separate explicit edit.
- Do not pass one peer's chat analysis, interim conclusion, or saved view into
  the other peer's prompt.
- Use `divergence_report.md` only after both independent views are saved.
- Do not infer product facts that are absent from the active SUT profile or its
  evidence; stop at the profile boundary and ask for the profile instead.
- If this adapter conflicts with `.shared/skills/bugate/SKILL.md`, the shared
  BUGate skill wins.
