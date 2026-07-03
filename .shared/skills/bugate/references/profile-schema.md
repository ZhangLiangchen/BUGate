# BUGate SUT Profile Schema

BUGate core reads a deliberately small profile surface. A profile is a YAML file
whose keys are merged on top of `bugate.config.yaml` by `load_config`. In
imported mode (the default, CHARTER §2.2) the profile lives **in the governed
SUT test repo and is committed there**, beside the tests it guards; in the
self-development setup (developing BUGate itself) it lives in or beside the
mounted SUT test workspace. It is
selected through `BUGATE_PROFILE=/path/profile.yaml`, the workspace
`bugate.config.yaml` `profile` field, or its `active_profile` alias. The profile
binds BUGate to a test automation surface; it does not copy product source, API
dumps, secrets, or environment facts into BUGate core.

> BUGate-self-development convention only (maintainers): when the ENGINE repo's own
> `bugate.config.yaml` `profile` field points at a mounted SUT, that line is a
> local, per-clone edit — do not commit it; the committed core stays
> SUT-neutral. In imported mode the opposite holds: the governed repo's config
> and profile are committed (they are that repo's own governance contract).

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
| `memory.namespace` | str | `project:bugate` (`DEFAULT_PROJECT_TAG`, after `MEMORY_BUS_PROJECT_TAG` env) | Project namespace/tag used for all Memory Service reads/writes. In imported mode this is the ONLY memory scaffolding a governed repo declares: all repos share the machine-level bus (one DB under `~/.bugate/memory-bus`), isolated by this tag — do not scaffold a per-repo service dir (ADR-BUGATE-003). |
| `namespace` | str | `project:bugate` | Flattened form of `memory.namespace` surfaced by `parse_simple_yaml` (nested `memory:` → `namespace:` collapses to a top-level `namespace` key); same project-tag fallback. |

> `memory.namespace` is read both as the nested key and, because the simple YAML
> parser collapses nested keys, as a flattened top-level `namespace` key. Either
> form sets the Memory Service project tag; both fall back to `project:bugate`.

## SUT-profile keys

These keys are normally supplied by the SUT profile and bind BUGate's gate to a
specific requirement and guarded implementation tree inside the governed test
automation workspace.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `artifact_dir` | path | none (falls back to `artifact_root`; if neither set, the guard reports it as unconfigured and blocks) | Directory holding the UC's pre-code artifacts whose `gate_status` is checked before allowing edits to guarded paths. |
| `artifact_root` | path | none | Alternate key for the artifact directory; used only if `artifact_dir` is absent. |
| `guarded_path_regex` | str or list[str] of regexes | `[]` (empty → guard is a no-op, returns `0`) | Regex patterns; any edited/patched path matching one is physically blocked until the pre-code BUGate artifacts pass. |
| `required_precode_artifacts` | list[str] | `['01_business_brief.md', '02_testability.md', '03_inventory.yaml', '03a_test_cases.md', '03b_adversarial_cases.yaml']` (`PRECODE_ARTIFACTS`) | Overrides the list of pre-code artifact filenames that must reach `gate_status: passed` before guarded files may be edited; used by `check_bugate.precode_passed`. The same list drives `check_bugate_v13_semantics.py --scope pre-code`: an artifact's presence is required and its layer gate chained only when it is in this set, so the write guard and the CI chain unlock/validate from one source of truth. `--scope all` always validates the full canonical set; out-of-set artifacts can still be gated by invoking their layer checker directly. |
| `agent_roles` | mapping: role → (bare list[str] \| `{read: list[str], write: list[str]}`) of regexes | none (no roles → agent-role guard is a no-op) | Per-role forbidden path regexes for Wave 7 role isolation; a bare list applies to both read and write, or use `read:` / `write:` sub-lists to scope each independently. |
| `sut_identity_terms` | str \| list[str] of regexes | none (no list → the de-SUT guard's identity scan is inert; its built-in general hygiene checks still run) | This SUT's identity terms — product, internal-system, account, or person names — that `check_no_sut_terms.py` keeps out of the reusable engine/kit subtree. Case-insensitive regexes, one per entry; the simple YAML parser does not unescape, so `\b` is written literally. See "De-SUT identity terms" below. |

> `guarded_path_regex` accepts either a single regex string or a list of regex
> strings. With no patterns the guard is a no-op.

> Matching is **textual, on the path string exactly as the runtime payload
> delivers it** (relative or absolute); the guard performs no
> workspace-membership check. A same-shaped absolute path *outside* the
> governed repo (e.g. `/other/repo/tests/<uc>/x.py` while this repo's pattern
> is `(^|/)tests/…`) is therefore also blocked — a deliberate fail-closed
> overreach, not an unlock risk. Write patterns as specifically as practical
> (anchor on distinctive directory names) if sibling repos share your test
> layout shape.

> `agent_roles` is consulted only when `BUGATE_AGENT_ROLE` is set to a role name
> defined in the mapping. A bare list under a role applies its regexes to both
> read and write actions; splitting into `read:` and `write:` sub-lists scopes
> each side independently.

### Evidence- and skill-source keys

These keys point the flow at where the governed SUT test workspace keeps its
black-box **evidence** (endpoint/interface contracts, captured API dumps, wiki,
recorded cases) and its SUT-specific **skills** (fetchers, environment adapters,
diagnostics). Core does not parse the documents themselves and imports nothing
from them; the keys exist so a worker — or an analysis prompt — can resolve the
contract and skill locations *from the profile* instead of guessing a path or
hardcoding a product path into Core. Each value is a path or a list of paths,
relative to the workspace root — in imported mode plain repo-relative paths, in
a self-development mount they may traverse the mount — or absolute. Globs are permitted in
list entries.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `evidence_sources` | path \| list[path \| glob] | none (unset → no profile-declared evidence root; the flow must be told a path explicitly) | One or more directories/files holding the SUT's black-box evidence the analysis reads to derive oracles — typically generated interface/endpoint contract docs, plus optional captured dumps, recorded cases, or wiki. The first entry is treated as the primary contract root. |
| `skill_sources` | path \| list[path] | none (unset → only the core BUGate skill is in scope) | One or more directories that contain SUT-specific skill folders (each a skill dir with its own `SKILL.md`) staged inside the governed workspace. Lets SUT skills be resolved declaratively through the profile without copying them into Core (in imported mode the runtime usually also discovers them natively as the repo's own skills). |

> `evidence_sources` and `skill_sources` are descriptive bindings, not gates:
> Core scripts ignore unknown fields, so an older gate simply skips them while a
> resolver or prompt that understands them can locate the SUT's contracts and
> skills. Keep the *documents and skill bodies* in the governed workspace, never
> in Core — these keys only record where they live.

### De-SUT identity terms

`sut_identity_terms` feeds the de-SUT guard (`scripts/check_no_sut_terms.py`),
whose purpose is the kit's **reusability**: the engine subtree vendored into a
governed repo must not carry facts true for only one SUT ("block seepage, not
mention" — CHARTER Amendment A1). The discipline is three-layered, and this key
covers exactly the middle layer:

1. **Behavioral SUT facts** (defaults, endpoints, resources, credentials,
   environment names) are barred from core unconditionally — review discipline
   plus the guard's built-in general hygiene patterns; no profile key and no
   exemption marker can allow them.
2. **Identity terms** — what this key lists. Forbidden in the kit tree by
   default; *narrative/provenance* mentions in upstream documentation are
   legitimate only through explicit, per-site exemption channels (inline
   `bugate: allow-sut-term`, file-level `desut: provenance-allowed`
   frontmatter on narrative docs, or the `docs/case-studies/` allowlist).
3. **Industry/domain vocabulary** is *not* defended by core. If this SUT wants
   a domain word (a chain name, an API-doc tool name, a trade term) defended,
   it lists that word here itself.

The scan surface is anchored on the **engine root's kit subtree** — the files
that will be reused for the next SUT. The governed workspace's own files
(artifacts, tests, its README, this profile) are never the scan surface, so a
SUT names itself freely on its own territory. Upstream CI additionally runs the
guard with a legacy fixture list (`tests/fixtures/legacy-sut-terms.txt`) so the
origin SUT's identity cannot seep back into core.

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
| `BUGATE_MEMORY_HOME` | path | unset (falls back to `~/.bugate/memory-bus`; the service's own `MCP_MEMORY_BASE_DIR` outranks it) | System-level bus data home where the service keeps `sqlite_vec.db`, `client.env`, `backups/`. Wrappers and clients resolve it identically; clients load `client.env` from here first, then fall back (deprecated, stderr hint) to the workspace `.memory_bus/client.env`. |
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
with the real ones for your SUT automation test workspace.

```yaml
# SUT profile merged on top of bugate.config.yaml by load_config.

# Where this requirement's pre-code BUGate artifacts live in the test workspace.
artifact_dir: sut/example/bugate/REQ-001

# Test implementation paths physically blocked until the pre-code artifacts pass.
guarded_path_regex:
  - "^sut/example/tests/.*[.]py$"

# Where the governed workspace keeps black-box evidence (contracts first, then
# any captured dumps / recorded cases / wiki) the analysis reads to derive
# oracles. Imported mode: plain repo-relative paths (e.g. docs/api). Self-development mounts:
# paths may traverse the mount, as below.
evidence_sources:
  - sut/example/workspace/docs/api          # primary: generated interface/endpoint contracts
  - sut/example/workspace/docs/raw          # secondary: captured dumps, recorded cases, wiki

# Directories of SUT-specific skill folders staged inside the governed workspace.
skill_sources:
  - sut/example/workspace/.shared/skills

# Artifact filenames that must reach gate_status: passed before guarded writes.
required_precode_artifacts:
  - 01_business_brief.md
  - 02_testability.md
  - 03_inventory.yaml
  - 03a_test_cases.md
  - 03b_adversarial_cases.yaml

# This SUT's identity terms the de-SUT guard keeps out of the engine/kit tree.
sut_identity_terms:
  - "\bexamplesut\b"

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
kill-rate gate (`--gate` exits non-zero below it). The spec shape is documented
in `scripts/oracle_falsification.py`'s module docstring.

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
  `profile_required`; the spec shape is documented in
  `scripts/check_prd_health.py`'s module docstring.
- `verifiability_min` — enables the Layer 1 **verifiability-ratio gate** in
  `check_bugate_brief_semantics.py`. A proposition counts as verifiable unless its
  `verifiability` cell reads unverifiable / deferred / unknown / tbd. The gate
  fails below this floor (e.g. `0.8`) and warns below the `0.80` advisory bar.
  When unset, the gate is off (default).
- `require_multiview` — when true, `check_bugate_v13_semantics.py` requires a
  Wave 1 `00_multiview/divergence_report.md` per UC (and, under `--require-passed`,
  `gate_status: passed`). Makes "every new UC runs Wave 1 + divergence archived" an
  enforced gate. Also available as the `--require-multiview` flag. Off by default.
- `require_adversarial_absorption` — when true, `check_bugate_inventory_semantics.py`
  requires the inventory to contain at least one case absorbed from Stage 3B
  (marked `origin: adversarial` or referencing an `ADV-xxx` id). Makes "every new
  UC absorbs >= 1 adversarial finding" an enforced gate. Off by default.
- `require_regression_traceability` — when true, `check_bugate_v13_semantics.py`
  (scope `all`) requires a `## Regression Cases` section in 04/05, and every
  non-baseline row must name a regression case + reference a `P-`/`O-` id.
- `layer2_strict` — when true, `check_bugate_layer2_semantics.py` additionally
  requires resolved Evidence Plan `status` (not pending), non-empty Layer Decision
  `reason`, and `## Dependencies` + `## Deferred Claims` sections.
- `require_assertion_coverage` — when true (or `generate_assertion_coverage_matrix.py
  --gate`), the matrix exits non-zero if `missing_implementation > --max-missing`
  (a case references an oracle the falsification spec never defines).
- `require_real_adversarial_dispatch` — when true, the 03b gate fails unless the
  artifact came from real peer dispatch (not a deterministic placeholder).
- `reject_on_bridge_failures` — when true, `sdtd_multiview.check` /
  `sdtd_adversarial.check` fail if any schema-rejected peer view sits in
  `00_*/cli_bridge_failures/`.
- `wave8_evidence_glob` — the captured-evidence glob `bin/wave8-weekly` feeds to
  the falsifier (SUT-specific, so not hardcoded). Also settable via the
  `WAVE8_EVIDENCE_GLOB` env var; `WAVE8_REPORTS_DIR` / `WAVE8_ARTIFACT_ROOT`
  override the weekly run's output dir and the inventory-scan root.
