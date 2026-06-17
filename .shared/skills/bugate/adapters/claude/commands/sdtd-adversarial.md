# /sdtd-adversarial

Run the Stage 3B adversarial / exploratory peer review over the reviewed
pre-code artifacts, producing `03b_adversarial_cases.yaml`.

Use this after the inventory and readable test cases exist, to red-team weak
oracles, missing negative paths, and fake-green risk before the pre-code gate.

Commands (run from the project root; `<artifact_dir>` is the per-use-case
artifact directory):

```bash
python3 scripts/sdtd_adversarial.py init <artifact_dir> --focus "<risk focus>"
python3 scripts/sdtd_adversarial_cli_bridge.py check-env
python3 scripts/sdtd_adversarial_cli_bridge.py run-all <artifact_dir>
python3 scripts/sdtd_adversarial.py check <artifact_dir>
```

Runtime knobs (env, all optional; empty/off by default):
`SDTD_CODEX_MODEL`, `SDTD_CLAUDE_MODEL`, `SDTD_CODEX_REASONING_EFFORT`,
`SDTD_CLAUDE_EFFORT`, `SDTD_CODEX_BIN`, `SDTD_CLAUDE_BIN`,
`SDTD_CLI_HTTPS_PROXY` / `SDTD_CLI_HTTP_PROXY` / `SDTD_CLI_ALL_PROXY`
(`SDTD_CLI_PROXY=0` force-disables proxy injection), `SDTD_CLI_TIMEOUT_SECONDS`.

Graceful degradation: if both CLI runtimes are not on PATH, the bridge writes
deterministic placeholder adversarial views and a skeleton
`03b_adversarial_cases.yaml` (`gate_status: pending`) noting that real peer
dispatch was skipped. The synthesized cases stay pending human review either
way; replace the placeholders with real peer output once both CLIs are
available.

Defer to `.shared/skills/bugate/SKILL.md` on conflict.
