# BUGate Capabilities

A single index of what BUGate does and every command that drives it. BUGate is a
SUT-neutral, AI-assisted black-box test analysis and **pre-code test-case
governance** framework: it forces a chain of accepted artifacts (business brief →
testability → inventory/oracle map → readable cases → adversarial review →
execution report → knowledge update) *before* any test implementation is
generated.

**Runtime contract**

- **Core is standard-library only.** Every script under `scripts/` and `bin/`
  runs on a bare Python 3 / bash install — no third-party deps. The BUGate YAML
  used by config and artifacts is a deliberately small subset parsed in
  `scripts/bugate_core.py` (`parse_simple_yaml` / `parse_nested_yaml`), not a
  general YAML library.
- **CLI / MCP runtimes are optional and degrade gracefully.** The dual-agent peer
  bridges need the `codex` / `claude` CLIs on `PATH`; if either is missing they
  write deterministic placeholder views instead of failing. The Wave 8 engines
  and Wave 0 scorer report `status: profile_required` (exit 0) when no SUT spec
  is supplied. The memory bus needs a user-installed `mcp-memory-service`; its
  wrappers no-op or print an install hint when it is absent. Nothing in core
  mounts or depends on a SUT repository.
- **Root discovery is git-free and split.** Gate scripts resolve the governed
  **workspace** root by walking up from CWD to the nearest `bugate.config.yaml`
  (`BUGATE_PROJECT_ROOT` overrides; the `AGENTS.md` + `.shared/` sentinel stays
  as the self-development fallback), while templates, sibling scripts, and `bin/`
  wrappers resolve from the **engine** tree's own location
  (`BUGATE_ENGINE_ROOT` overrides). Works in non-git checkouts.

**Where commands run.** The one usage mode is **imported**: the engine, skill,
hooks, and a **committed** profile live inside the SUT automation test repo,
and the SUT-facing commands below run from that repo's root against its
profile. When **developing BUGate itself**, this repo stays pure: only core
template checks, generated scratch fixtures, installer e2e runs against an
external/scratch SUT repo, and SUT-neutral CI checks run here.
Normative rules: [`CHARTER.md`](CHARTER.md) §2 + Amendment A4. Per command class:

| Command class | Runs in |
|---|---|
| Pre-code gate engine, physical write guard, orchestrator, 3A/04/05 generators, Wave 0 / Wave 8 engines, Wave 1 / 3B peer bridges, role isolation, plan lock, prompt reminder, `wave8-weekly` | The **imported SUT test repo**; in BUGate core only template checks and ephemeral fixture acceptances run |
| Importer (`bugate_init.py`) — vendors the kit, links Claude skills plus official Codex `.agents/skills` (with `.codex/skills` kept as a legacy bridge), copies the Codex gate agents into `.codex/agents/`, merges + refreshes the BUGate hook wiring (never the repo's own hooks), scaffolds committed config + profile, and appends a marked, idempotent ignore block to the SUT repo's root `.gitignore` (default scorer outputs + local agent/memory state; the SUT's own lines and the committed contract stay intact) | The **engine checkout** (this repo, or an already-vendored kit), pointed at a target SUT repo |
| De-SUT guard (`check_no_sut_terms.py`) | The **engine tree** it is part of — scans the kit subtree anywhere; the full upstream surface only in this repo (CI-enforced here) |
| Memory bus (`memory_bus.py`, `bin/memory-*`) | Either — namespace isolated per project via `memory.namespace` / `MEMORY_BUS_PROJECT_TAG` |

Script invocations below are from the **imported SUT test repo root** when a
real SUT is involved. In BUGate core, run only SUT-neutral
template checks, ephemeral fixture tests, and installer dry-runs/e2e against an
external or scratch repo. Bash wrappers live in `bin/`.

---

## Capability map

### Pre-code gate engine (the 4-layer gate + physical write guard)

| Capability | Wave / Stage | Script | Key flags / subcommands | Profile / env key | Graceful fallback | Example |
|---|---|---|---|---|---|---|
| Layer 1 — business-brief gate (P-/O- ids, required sections, accepted-field checks, optional verifiability ratio) | Stage 1 | `check_bugate_brief_semantics.py` | `<artifact_dir>`, `--require-passed` | `verifiability_min` (opt-in ratio gate); `BUGATE_PROFILE` | Verifiability gate off when `verifiability_min` unset; `--require-passed` adds the no-TBD/field checks | `python3 scripts/check_bugate_brief_semantics.py docs/usecases/UC --require-passed` |
| Layer 2 — testability gate (layer decision + evidence plan, cross-layer P-/O- coverage) | Stage 2 | `check_bugate_layer2_semantics.py` | `<artifact_dir>`, `--require-passed` | `layer2_strict` (deeper checks); `BUGATE_PROFILE` | Strict section/reason/status checks only when `layer2_strict` true | `python3 scripts/check_bugate_layer2_semantics.py docs/usecases/UC` |
| Layer 3 — inventory gate (case ids, intent, proposition/oracle refs, reverse coverage, data-source status) | Stage 3 | `check_bugate_inventory_semantics.py` | `<artifact_dir>`, `--require-passed` | `require_adversarial_absorption` (≥1 absorbed ADV case); `BUGATE_PROFILE` | Absorption gate off unless flag set; reverse coverage skipped if no brief present | `python3 scripts/check_bugate_inventory_semantics.py docs/usecases/UC --require-passed` |
| Full pre-code stack check (chains L1/L2/L3 + readable + adversarial + optional modeling + per-UC multiview) | Stages 1–3B | `check_bugate_v13_semantics.py` | `<artifact_dir>`, `--scope {pre-code,all}`, `--require-passed`, `--require-multiview`, `--profile` | `required_precode_artifacts`, `require_multiview`, `require_real_adversarial_dispatch`, `require_regression_traceability` | Pre-code scope requires + chains a layer gate only for artifacts in `required_precode_artifacts` (same unlock list as the write guard); `--scope all` validates the full canonical set and adds 04/05 report checks; modeling artifacts validated only when present | `python3 scripts/check_bugate_v13_semantics.py docs/usecases/UC --scope pre-code` |
| Physical write guard — blocks edits to guarded implementation paths until pre-code artifacts pass | gate enforcement (PreToolUse hook) | `check_bugate.py` | positional `paths…` (else reads hook payload on stdin) | `guarded_path_regex`, `artifact_dir`/`artifact_root`, `artifact_dir_template` (per-UC fail-closed `{uc}` binding), `required_precode_artifacts` | No `guarded_path_regex` → returns 0 (nothing guarded); never blocks a TTY | `echo '{"tool_input":{"file_path":"tests/x.py"}}' \| python3 scripts/check_bugate.py` |

### Optional modeling stages (Full-SDTD)

| Capability | Stage | Script | Subcommand | Notes |
|---|---|---|---|---|
| Domain model (OBJ-/INV- ids), state/flow (STATE-/TR-), test-dimension matrix (DIM-) | 1A / 1B / 2A | `sdtd_orchestrator.py … --init --full-sdtd`; validated by `check_bugate_v13_semantics.py` | n/a | Not part of the required pre-code set; `check_bugate_v13_semantics.py` validates `01a_domain_model.md` / `01b_state_flow.md` / `02a_test_dimension_matrix.yaml` only when the files exist, chaining ids across stages |

### Wave 0 — PRD health

| Capability | Wave | Script | Key flags | Profile / env key | Graceful fallback | Example |
|---|---|---|---|---|---|---|
| Score 8 PRD-quality dimensions (1–5) → 0–100 composite + grade A–D + routing + gap report | 0 | `check_prd_health.py` | `--input <spec.yaml>`, `--json-output`, `--md-output`, `--min-score`, `--gate`, `--profile` | `prd_health_spec`, `prd_health_min` (default floor 60) | No spec → `status: profile_required` (exit 0); `--gate` exits non-zero below floor | `python3 scripts/check_prd_health.py --input prd_health.yaml --gate` |

### Wave 8 — oracle falsification + assertion coverage

| Capability | Wave | Script | Key flags | Profile / env key | Graceful fallback | Example |
|---|---|---|---|---|---|---|
| Mutate pristine evidence JSON, run declarative oracles offline, score killed/survived | 8 | `oracle_falsification.py` | `--spec <spec.yaml>`, `--evidence <glob>`, `--json-output`, `--md-output`, `--min-score`, `--gate`, `--profile` | `falsification_spec`, `falsification_threshold` (default 0.7) | No spec / no oracles+mutations / no evidence → `status: profile_required` (exit 0); offline only, never calls live APIs | `python3 scripts/oracle_falsification.py --spec spec.yaml --gate` |
| 5-state assertion coverage matrix (referenced / defined / exercised → covered / missing_implementation / defined_unused) | 8 | `generate_assertion_coverage_matrix.py` | `--spec`, `--mutation-result <result.json>`, `--artifact-root`, `--output`, `--gate`, `--max-missing`, `--profile` | `require_assertion_coverage` | Without `--spec` → references-only listing + score line; `--gate`/config fails if `missing_implementation > --max-missing` | `python3 scripts/generate_assertion_coverage_matrix.py --artifact-root . --spec spec.yaml --mutation-result oracle_falsification_result.json` |

### Wave 1 / 3B — dual-agent peer bridges + dispatchers

| Capability | Wave / Stage | Script | Subcommands | Profile / env key | Graceful fallback | Example |
|---|---|---|---|---|---|---|
| Multi-view: dispatch two independent peer reviewers, diff their proposition sets into a divergence report | 1 | `sdtd_multiview_cli_bridge.py` | `check-env`, `run-all <artifact_dir>`, `run-divergence <artifact_dir> [--force]` | `SDTD_CODEX_BIN`/`SDTD_CLAUDE_BIN`, `SDTD_*_MODEL`, `SDTD_CODEX_REASONING_EFFORT`/`SDTD_CLAUDE_EFFORT`, `SDTD_CLI_TIMEOUT_SECONDS`, `SDTD_CLI_*_PROXY`, `SDTD_CLI_PROXY` | Missing `codex`/`claude` on PATH, per-peer timeout, non-zero exit, empty/schema-invalid output → deterministic placeholder view; schema-rejected views archived under `cli_bridge_failures/` | `python3 scripts/sdtd_multiview_cli_bridge.py run-all docs/usecases/UC` |
| Multi-view init/status/check (`00_multiview/` layout + per-UC divergence gate) | 1 | `sdtd_multiview.py` | `init <dir> [--focus]`, `status <dir>`, `check <dir>` | `reject_on_bridge_failures` (archived rejects block check) | `check` fails unless `divergence_report.md` is `gate_status: passed` | `python3 scripts/sdtd_multiview.py check docs/usecases/UC` |
| Adversarial: two independent red-team peers attack the plan → synthesize `03b_adversarial_cases.yaml` | 3B | `sdtd_adversarial_cli_bridge.py` | `check-env`, `run-all <artifact_dir>` | same `SDTD_*` env contract as multiview bridge | Same per-peer fallbacks; synthesized 03b left `gate_status: pending` for human review; tags `partial_real_peer_dispatch` if a peer degrades | `python3 scripts/sdtd_adversarial_cli_bridge.py run-all docs/usecases/UC` |
| Adversarial init/check (`00_adversarial/` layout + 03b gate) | 3B | `sdtd_adversarial.py` | `init <dir> [--focus]`, `check <dir>` | `reject_on_bridge_failures` | `check` fails unless `03b_adversarial_cases.yaml` is `gate_status: passed` with `adversarial_cases:` | `python3 scripts/sdtd_adversarial.py check docs/usecases/UC` |

### Orchestration, readable cases, reports, self-healing

| Capability | Stage | Script | Key flags | Graceful fallback | Example |
|---|---|---|---|---|---|
| Orchestrator: scaffold artifacts, run the pre-code chain, or the post-run chain | all | `sdtd_orchestrator.py` | `<artifact_dir>`, `--init`, `--auto`, `--scope {pre-code,post-run}`, `--full-sdtd`, `--run-cli-workers`, `--pytest-log`, `--command`, `--env`, `--exit-code` | No args (no `--init`/`--auto`) → status listing; `--run-cli-workers` only then invokes the peer bridges; post-run requires `--pytest-log` + `--command` | `python3 scripts/sdtd_orchestrator.py docs/usecases/UC --init` |
| Readable test cases from `03_inventory.yaml` → `03a_test_cases.md` | 3A | `generate_sdtd_text_testcases.py` | `<artifact_dir>`, `--write` | No inventory → emits an empty-cases stub | `python3 scripts/generate_sdtd_text_testcases.py docs/usecases/UC --write` |
| Post-run 04/05 report drafts (execution report + knowledge update) | 5 / 6 | `generate_sdtd_reports.py` | `<artifact_dir>`, `--pytest-log`, `--command`, `--env`, `--exit-code`, `--self-healing-json`, `--write` | Missing log → status `log_not_found`; without `--write` prints to stdout | `python3 scripts/generate_sdtd_reports.py docs/usecases/UC --pytest-log run.log --command "pytest" --exit-code 0 --write` |
| Failure classifier + repair plan (exclude infra/env before any SUT-defect verdict) | 5 | `self_healing_mvp.py` | `--pytest-log`, `--json-output`, `--md-output`, `--repair-plan-output`, `--exit-code` | Empty log → `overall: no_log`; never edits tests automatically | `python3 scripts/self_healing_mvp.py --pytest-log run.log --json-output sh.json --md-output sh.md --repair-plan-output plan.md --exit-code 1` |

### Role isolation, de-SUT guard, plan lock, prompt reminder

| Capability | Wave | Script | Key flags / env | Profile / env key | Graceful fallback | Example |
|---|---|---|---|---|---|---|
| Agent-role path isolation — deny a role's edits/reads to forbidden paths | 7 | `check_agent_role_paths.py` | reads PreToolUse payload on stdin | `BUGATE_AGENT_ROLE` (active role), `agent_roles` (per-role regexes, bare list or `read:`/`write:` sub-lists) | `BUGATE_AGENT_ROLE` unset/empty → returns 0 (no-op); no rules for the role/action → allow | `BUGATE_AGENT_ROLE=implementer python3 scripts/check_agent_role_paths.py` |
| De-SUT guard — keep a SUT's identity terms + behavioral leakage out of the reusable kit subtree (CHARTER A1: block seepage, not mention) | core hygiene | `check_no_sut_terms.py` | `--terms-file <list>` (repeatable; upstream CI uses `tests/fixtures/legacy-sut-terms.txt`), `--profile`, `--quiet` | `sut_identity_terms` (profile), `BUGATE_PROFILE` | No term list → identity scan inert, built-in general hygiene still runs; narrative exemptions: inline `bugate: allow-sut-term` (HTML-comment form for Markdown), file-level `desut: provenance-allowed`, `docs/case-studies/`; exits 1 on any hit | `python3 scripts/check_no_sut_terms.py --terms-file tests/fixtures/legacy-sut-terms.txt` |
| Plan lock — block implementation while `.bugate/plan.lock` exists | gate enforcement | `check_plan_lock.py` | none | n/a | No lock file → returns 0; core never creates the lock itself | `python3 scripts/check_plan_lock.py` |
| Prompt reminder — nudge toward pre-code gates when a prompt looks like test-implementation work | gate enforcement | `bugate_prompt_reminder.py` | reads prompt payload on stdin | n/a | Emits the reminder only on keyword match; always exits 0 | `echo '{"prompt":"write the e2e test"}' \| python3 scripts/bugate_prompt_reminder.py` |

### Memory bus (optional `mcp-memory-service`) + `bin/` set

The memory bus is an **optional** long-term truth layer backed by a user-installed
`mcp-memory-service`. `scripts/memory_bus.py` is the stdlib HTTP client;
`bin/memory-*` are thin wrappers that resolve the root, ensure the service is up,
and delegate to the matching `memory_bus.py` subcommand. Auth/namespace come from
env (`MEMORY_BUS_URL`, `MEMORY_BUS_PROJECT_TAG`, `MCP_API_KEY*`); see profile-schema.

The bus is **machine-level** (ADR-BUGATE-003): one local service instance whose
data home resolves system-wide (`MCP_MEMORY_BASE_DIR` > `BUGATE_MEMORY_HOME` >
`~/.bugate/memory-bus`, keys in `<bus-home>/client.env`), shared by every
BUGate-enabled repo on the machine. Projects are isolated by namespace tag
(`project:<name>`), never by per-repo databases; a legacy in-repo
`.memory_bus/client.env` still works as a deprecated fallback. Initialization
is **reuse-first**: `memory-bus-ensure`/`memory-bus-start` probe for a healthy
running instance before launching and never spawn a duplicate, and
`bugate init` only reports the probe result — it scaffolds no per-repo service.

| Command (`bin/…` → `memory_bus.py …`) | Purpose | Key flags |
|---|---|---|
| `memory-bus-ensure` | Health-check the service; start it in the background if down | env `MCP_HTTP_PORT`, `MEMORY_BUS_ENSURE_WAIT_SECONDS`; exits 0 even if it can't start |
| `memory-bus-start` | Reuse a healthy running `mcp-memory-service`, else launch it (resolves `memory` from `.venv/bin` or PATH; configures ONNX storage) | data home env `MCP_MEMORY_BASE_DIR` > `BUGATE_MEMORY_HOME` (default `~/.bugate/memory-bus`); `MCP_MEMORY_STORAGE_BACKEND`, `MCP_MEMORY_USE_ONNX`, `MCP_HTTP_PORT`; prints install hint if `memory` binary absent |
| `memory-bus-status` → `status` | Health-check / status | `--json`, `--timeout`, `--no-fail` |
| `memory-bus-stop` → `stop` | Stop the service | no-op if `memory` binary absent |
| `memory-bus-install-launchd` | OPTIONAL macOS hardening: user LaunchAgent for the bus (RunAtLoad + KeepAlive) | `--uninstall` removes it; absence changes nothing (`memory-bus-ensure` still starts on demand) |
| `memory-recent` → `recent` | Newest agent-visible memories | `--agent` (required), `--limit`, `--json`, `--no-header` |
| `memory-handoff` → `handoff` | Record a handoff between agents | `--from`, `--to`, `--msg` (required); `--status`, `--scope`, `--task`, `--tag`, `--artifact` |
| `memory-service-note` → `note` | Write a memory entry | `--agent`, `--type`, `--msg` (required); `--status`, `--scope`, `--task`, `--to`, `--broadcast`, `--tag`, `--artifact`, `--metadata` |
| `memory-service-search` → `search` | Query memories (semantic + tag fallback) | `--query` (required), `--tag`, `--limit`, `--json` |
| `memory-service-archive` → `archive` | Back up namespace memories to local JSON | `--out` (default `<bus-home>/backups/`), `--limit` |
| `memory-service-lint` → `lint` | Validate memories against governance rules | `--include-warnings`, `--limit`, `--show`, `--json` |
| `promote-memory` → `note --status confirmed` | Promote a working note to a confirmed memory | `--agent`, `--type`, `--msg` (required); `--from-id` (→ `promoted_from` metadata), `--task`, `--artifact` |
| `memory-model-fetch` | Pre-download the ONNX embedding model offline/behind proxy | env `BUGATE_ONNX_MODEL`, `MCP_MEMORY_ONNX_DIR`; needs an HF CLI |

### Wave 8 weekly automation

| Command | Purpose | Key env | Graceful fallback |
|---|---|---|---|
| `wave8-weekly` | Re-run `oracle_falsification.py` on the current evidence pool, then regenerate the assertion coverage matrix | `WAVE8_EVIDENCE_GLOB` (else config `wave8_evidence_glob`), `WAVE8_REPORTS_DIR` (default `<root>/reports`), `WAVE8_ARTIFACT_ROOT` (default `<root>`), `BUGATE_PROFILE` | No evidence glob configured → prints a hint and exits non-zero rather than guessing a SUT path; offline run |

Example:
`WAVE8_EVIDENCE_GLOB='reports/evidence/*.json' bin/wave8-weekly`

---

## Profile and config keys

Every `bugate.config.yaml` / SUT-profile key and environment variable referenced
above (artifact paths, `guarded_path_regex`, `required_precode_artifacts`,
`agent_roles`, the `falsification_*` / `prd_health_*` / `require_*` /
`layer2_strict` / `verifiability_min` / `wave8_evidence_glob` hardening keys, the
`SDTD_*` bridge env contract, the Memory Service env, and `BUGATE_PROFILE` /
`BUGATE_AGENT_ROLE`) is documented canonically — with types, defaults, and a
copy-paste example profile — in:

- `.shared/skills/bugate/references/profile-schema.md`

Profiles are merged on top of `bugate.config.yaml` by `load_config` and selected
via `BUGATE_PROFILE`, the config `profile` field, or its `active_profile` alias.
In imported mode the governed repo commits its own config + profile; selecting
via the ENGINE repo's config `profile` field is the self-development convention — a
local, per-clone edit, never committed. The shipped templates pass the pre-code
gates as-is; governed-layout acceptances fabricate fixtures at run time
(upstream `tests/`).
