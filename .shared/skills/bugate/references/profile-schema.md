# BUGate SUT Profile Schema

BUGate core reads a deliberately small profile surface. A profile is a YAML file
whose keys are merged on top of `bugate.config.yaml` by `load_config`. Profiles
can live in a mounted SUT workspace and be selected through
`BUGATE_PROFILE=/path/profile.yaml`, the core `bugate.config.yaml` `profile` field,
or its `active_profile` alias.

This document is the full key contract. Keys are grouped into core
`bugate.config.yaml` fields, SUT-profile fields, and environment variables. Core
scripts ignore unknown fields, so profiles may add their own keys for runtime
commands, evidence fetchers, assertion runners, environments, resources, or
authentication.

## Core `bugate.config.yaml` keys

These keys are read from the core config and may be overridden by a profile.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `profile` | path | none (falls back to `active_profile`, then no profile loaded) | Relative or absolute path to a SUT profile YAML whose keys are merged on top of `bugate.config.yaml` by `load_config`; also re-resolved in `check_agent_role_paths.py`. |
| `active_profile` | path | none | Alternate key for the profile path; used only if `profile` is absent (`load_config` tries `profile` then `active_profile`). |
| `memory.namespace` | str | `project:bugate` (`DEFAULT_PROJECT_TAG`, after `MEMORY_BUS_PROJECT_TAG` env) | Project namespace/tag used for all Memory Service reads/writes. |
| `namespace` | str | `project:bugate` | Flattened form of `memory.namespace` surfaced by `parse_simple_yaml` (nested `memory:` → `namespace:` collapses to a top-level `namespace` key); same project-tag fallback. |

> `memory.namespace` is read both as the nested key and, because the simple YAML
> parser collapses nested keys, as a flattened top-level `namespace` key. Either
> form sets the Memory Service project tag; both fall back to `project:bugate`.

## SUT-profile keys

These keys are normally supplied by the SUT profile and bind BUGate's gate to a
specific requirement and implementation tree.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `artifact_dir` | path | none (falls back to `artifact_root`; if neither set, the guard reports it as unconfigured and blocks) | Directory holding the UC's pre-code artifacts whose `gate_status` is checked before allowing edits to guarded paths. |
| `artifact_root` | path | none | Alternate key for the artifact directory; used only if `artifact_dir` is absent. |
| `guarded_path_regex` | str or list[str] of regexes | `[]` (empty → guard is a no-op, returns `0`) | Regex patterns; any edited/patched path matching one is physically blocked until the pre-code BUGate artifacts pass. |
| `required_precode_artifacts` | list[str] | `['01_business_brief.md', '02_testability.md', '03_inventory.yaml', '03a_test_cases.md', '03b_adversarial_cases.yaml']` (`PRECODE_ARTIFACTS`) | Overrides the list of pre-code artifact filenames that must reach `gate_status: passed` before guarded files may be edited; used by `check_bugate.precode_passed`. |
| `agent_roles` | mapping: role → (bare list[str] \| `{read: list[str], write: list[str]}`) of regexes | none (no roles → agent-role guard is a no-op) | Per-role forbidden path regexes for Wave 7 role isolation; a bare list applies to both read and write, or use `read:` / `write:` sub-lists to scope each independently. |

> `guarded_path_regex` accepts either a single regex string or a list of regex
> strings. With no patterns the guard is a no-op.

> `agent_roles` is consulted only when `BUGATE_AGENT_ROLE` is set to a role name
> defined in the mapping. A bare list under a role applies its regexes to both
> read and write actions; splitting into `read:` and `write:` sub-lists scopes
> each side independently.

## Environment variables

These environment variables override or supplement config/profile values at run
time. They are read directly by the BUGate scripts and never need to live in a
profile.

### Gate and role isolation

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `BUGATE_PROFILE` | path | unset (`None` → `load_config` uses the config-declared profile) | Env override for which SUT profile `load_config` merges; passed in `check_bugate.py` and `check_agent_role_paths.py`. |
| `BUGATE_AGENT_ROLE` | role name (lowercased) | `''` (empty/unset → agent-role isolation guard returns `0`, allow all) | Active agent role; when set and the profile defines `agent_roles` for it, matching paths are denied for the current Read/Edit/Write/patch action. |

### Memory Service

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `MEMORY_BUS_PROJECT_TAG` | str | unset (falls back to config `memory.namespace`, then `project:bugate`) | Highest-priority override for the Memory Service project namespace/tag. |
| `MEMORY_BUS_URL` | URL | `http://localhost:8000` (`DEFAULT_URL`) | Base URL of the local `mcp-memory-service` HTTP API; trailing slash stripped. Also set programmatically from the `--url` CLI flag. |
| `MCP_API_KEY_AGENT` | token | unset (then tries `MCP_API_KEY_HUMAN`, then `MCP_API_KEY`; if all unset, no auth header) | First-choice bearer/API-key token for Memory Service auth (sent as `Authorization: Bearer` and `X-API-Key`). |
| `MCP_API_KEY_HUMAN` | token | unset | Second-choice Memory Service auth token, used if `MCP_API_KEY_AGENT` is unset. |
| `MCP_API_KEY` | token | unset | Last-resort Memory Service auth token, used if both `MCP_API_KEY_AGENT` and `MCP_API_KEY_HUMAN` are unset. |
| `MEMORY_BUS_STOP_WRITE` | `'0'` to disable | unset (enabled; heartbeat written) | Set to `'0'` to skip the stop-hook hourly heartbeat memory write in `cmd_stop`. |

### SDTD multi-view CLI bridge

These drive the Codex/Claude peer CLI dispatch in `sdtd_multiview_cli_bridge.py`.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `SDTD_CODEX_BIN` | executable name/path | `codex` | Name/path of the Codex CLI binary the bridge dispatches and probes via `shutil.which`. |
| `SDTD_CLAUDE_BIN` | executable name/path | `claude` | Name/path of the Claude CLI binary the bridge dispatches and probes via `shutil.which`. |
| `SDTD_CODEX_MODEL` | str (stripped) | `''` (empty → CLI default; appended as `--model` only if set) | Forces a specific Codex model via `codex exec --model M`; empty means let the CLI choose. |
| `SDTD_CLAUDE_MODEL` | str (stripped) | `''` (empty → CLI default) | Forces a specific Claude model via `claude -p --model M`; empty means let the CLI choose. |
| `SDTD_CODEX_REASONING_EFFORT` | str (stripped) | `''` (empty → CLI default) | Codex reasoning effort, appended as `-c model_reasoning_effort="E"` only when set. |
| `SDTD_CLAUDE_EFFORT` | str (stripped) | `''` (empty → CLI default) | Claude reasoning effort, appended as `--effort E` only when set. |
| `SDTD_CLI_TIMEOUT_SECONDS` | int (parsed with `int()`) | `1800` | Per-peer subprocess timeout in seconds for each Codex/Claude CLI dispatch. |
| `SDTD_CLI_HTTPS_PROXY` | proxy URL | `''` (empty → not injected) | If set (and `SDTD_CLI_PROXY != '0'`), injected into child env as `https_proxy`/`HTTPS_PROXY` for peer CLI calls. |
| `SDTD_CLI_HTTP_PROXY` | proxy URL | `''` (empty → not injected) | If set (and `SDTD_CLI_PROXY != '0'`), injected as `http_proxy`/`HTTP_PROXY` for peer CLI calls. |
| `SDTD_CLI_ALL_PROXY` | proxy URL | `''` (empty → not injected) | If set (and `SDTD_CLI_PROXY != '0'`), injected as `all_proxy`/`ALL_PROXY` for peer CLI calls. |
| `SDTD_CLI_PROXY` | `'0'` to disable | `'1'` (injection enabled when the proxy vars are set); `'0'` force-disables all proxy injection | Master switch for proxy injection into peer CLI subprocess env; also drives `proxy_summary()`. |

## Example profile

A complete, copy-paste SUT profile. Replace the placeholder paths and regexes
with the real ones for your system under test.

```yaml
# SUT profile merged on top of bugate.config.yaml by load_config.

# Where this requirement's pre-code BUGate artifacts live.
artifact_dir: sut/example/bugate/REQ-001

# Implementation paths physically blocked until the pre-code artifacts pass.
guarded_path_regex:
  - "^sut/example/tests/.*[.]py$"

# Artifact filenames that must reach gate_status: passed before guarded writes.
required_precode_artifacts:
  - 01_business_brief.md
  - 02_testability.md
  - 03_inventory.yaml
  - 03a_test_cases.md
  - 03b_adversarial_cases.yaml

# Wave 7 agent-role path isolation (consulted when BUGATE_AGENT_ROLE is set).
agent_roles:
  # Bare list: forbidden for both read and write under this role.
  implementer:
    - "^sut/example/docs/source_mirror/.*$"
  # Split form: scope read and write independently.
  designer:
    read:
      - "^sut/example/internal/.*$"
    write:
      - "^sut/example/tests/.*[.]py$"

# Wave 8 oracle falsification: declarative oracle/mutation spec + score gate (0-1).
falsification_spec: sut/example/falsification_spec.yaml
falsification_threshold: 0.7

# Memory Service project namespace/tag for all reads/writes.
memory:
  namespace: project:example-sut
```

`falsification_spec` points `scripts/oracle_falsification.py` at a declarative
spec (`oracles:` as field assertions, `mutations:` as field-path ops, plus an
`evidence`/`evidence_glob`). The engine is SUT-neutral and imports no SUT code;
without a spec it reports `profile_required`. `falsification_threshold` is the
kill-rate gate (`--gate` exits non-zero below it). See the worked example at
`examples/demo-sut/falsification_spec.yaml`.

### Optional hardening keys

- `artifact_dir_template` — enables **per-UC fail-closed** write-guarding in
  `scripts/check_bugate.py`. Set a template with a `{uc}` placeholder (e.g.
  `docs/usecases/{uc}/`) and give each `guarded_path_regex` a named `(?P<uc>...)`
  capture. A blocked path is then checked against *its own* UC artifact dir, so
  one requirement's passed artifacts can never unlock another's tests. A guarded
  path that matches a pattern with no `uc` capture is blocked (fail-closed). When
  unset, the single `artifact_dir` is used (default).
- `prd_health_spec` / `prd_health_min` — enable the **Wave 0 PRD health check**
  (`scripts/check_prd_health.py`, METHOD §3). Point at a declarative 8-dimension
  self-assessment (scores 1-5); the engine computes a 0-100 composite + grade
  A–D + routing and passes through a structured gap report. `--gate` exits
  non-zero below `prd_health_min` (default 60). Without a spec it reports
  `profile_required`. See `examples/demo-sut/prd_health.yaml`.
- `verifiability_min` — enables the Layer 1 **verifiability-ratio gate** in
  `check_bugate_brief_semantics.py`. A proposition counts as verifiable unless its
  `verifiability` cell reads unverifiable / deferred / unknown / tbd. The gate
  fails below this floor (e.g. `0.8`) and warns below the `0.80` advisory bar.
  When unset, the gate is off (default).
