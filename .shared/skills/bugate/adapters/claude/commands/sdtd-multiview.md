# /sdtd-multiview

Run the Wave 1 multi-view input pack and independent-peer divergence check.

Use this when a use case needs an independent cross-read of the Layer 1 brief
before Layer 2, or when you want a machine-detected divergence report over the
two peer views' proposition-id sets.

Commands (run from the project root; `<artifact_dir>` is the per-use-case
artifact directory):

```bash
python3 scripts/sdtd_multiview.py init <artifact_dir> --focus "<Wave 1 focus>"
python3 scripts/sdtd_multiview.py status <artifact_dir>
python3 scripts/sdtd_multiview_cli_bridge.py check-env
python3 scripts/sdtd_multiview_cli_bridge.py run-all <artifact_dir>
python3 scripts/sdtd_multiview_cli_bridge.py run-divergence <artifact_dir> --force
python3 scripts/sdtd_multiview.py check <artifact_dir>
```

Runtime knobs (env, all optional; empty/off by default):
`SDTD_CODEX_MODEL`, `SDTD_CLAUDE_MODEL`, `SDTD_CODEX_REASONING_EFFORT`,
`SDTD_CLAUDE_EFFORT`, `SDTD_CODEX_BIN`, `SDTD_CLAUDE_BIN`,
`SDTD_CLI_HTTPS_PROXY` / `SDTD_CLI_HTTP_PROXY` / `SDTD_CLI_ALL_PROXY`
(`SDTD_CLI_PROXY=0` force-disables proxy injection), `SDTD_CLI_TIMEOUT_SECONDS`.

Graceful degradation: if both CLI runtimes are not on PATH, the bridge writes
deterministic placeholder views and a divergence report noting that real peer
dispatch was skipped. The gate flow stays green; replace the placeholders with
real peer output once both CLIs are available.

Defer to `.shared/skills/bugate/SKILL.md` on conflict.
