# BUGate — Setting up the external runtimes

This file keeps its historical name, but it covers both the **required** machine
memory bus and optional external runtimes. The memory bus section is the
manual/offline reference for the component that `bugate_init.py` and
`bin/memory-bus-*` normally install or heal automatically. Until BUGate ships a
packaged console-script, prose shorthand `bugate init` means
`python3 scripts/bugate_init.py`.

The BUGate **gate engine** (the 4-layer gate) is zero-dependency stdlib Python —
see [`INIT.md`](../INIT.md). This document covers the runtimes that call out
beyond stdlib. **One is required, two are optional:**

- **Required — the memory bus** (§2). A core BUGate component (long-term memory,
  dual-agent progress sync + relay, memory promotion). `bugate init` /
  `bin/memory-bus-*` **auto-install** it once (machine-level) when absent and
  **self-heal** on an anomaly, so you normally don't run §2 by hand — it is here
  as the manual/offline reference and as the diagnosis path (set
  `BUGATE_MEMORY_NO_INSTALL=1` to install manually). Runtime is non-blocking (a
  transient outage restarts rather than fail-closing edits), but a BUGate setup
  is incomplete without it.
- **Optional — the dual-agent CLIs** (§1) and **agent role isolation** (§3):
  each degrades cleanly when absent (placeholder peer views / role guard off).

Each section follows the same shape: **Install → Wire → Verify → Fallback**.

| Capability | You install | Status / default if absent |
|---|---|---|
| 1. Dual-agent CLIs (multi-view + adversarial) | `codex` + `claude` CLIs | optional — deterministic placeholder views, gate stays green |
| 2. MCP memory service + ONNX | auto-installed by init (`mcp-memory-service` + ONNX model) | **required core** — init installs/self-heals; §2 is the manual/offline reference |
| 3. Agent role isolation | nothing (env + profile) | optional — default OFF (no-op when role unset) |

---

## 1. Dual-agent CLIs — multi-view cross-audit & adversarial review

Two independent AI agents work as *peer* reviewers. The Wave 1 bridge
(`scripts/sdtd_multiview_cli_bridge.py`) extracts the business model from two
sides and reports divergence before Layer 1 is accepted; the Stage 3B bridge
(`scripts/sdtd_adversarial_cli_bridge.py`) runs two independent red-team
reviewers and synthesizes `03b_adversarial_cases.yaml`. Both bridges share the
exact same dispatch shape and `SDTD_*` env contract.

### Install

Install both CLIs with the vendor native installers and put them on `PATH`:

- Codex CLI, using the standalone macOS/Linux installer:
  `curl -fsSL https://chatgpt.com/codex/install.sh | sh`
- Claude Code, using Native Install (Recommended):
  `curl -fsSL https://claude.ai/install.sh | bash`
- the `codex` CLI must support the non-interactive `codex exec` subcommand with
  `--sandbox` and stdin via a trailing `-`;
- the `claude` CLI (must support `claude -p` with `--permission-mode` and
  `--output-format`).

Minimum-version note: install a build recent enough that `codex exec --sandbox
read-only -` and `claude -p --permission-mode dontAsk --output-format text` are
both accepted. Older Codex builds that still accept `--ask-for-approval never`
are handled automatically by the bridge. If your build does not support a
model/effort flag, leave the corresponding env var unset (see below) to fall back
to the CLI's own defaults, or override the binary name.

### Wire

The bridges invoke these **exact** commands (read from `build_command()` in both
scripts), piping the prompt on **stdin**:

- **claude:** `claude -p [--model M] [--effort E] --permission-mode dontAsk --output-format text`
- **codex:** `codex exec [--ask-for-approval never] --sandbox read-only [--model M] [-c model_reasoning_effort="E"] -`

The `--model` / `--effort` (claude) and `--model` / `-c model_reasoning_effort`
(codex) flags are appended **only when** the corresponding env var is set —
nothing vendor-specific is hardcoded. Tune via these `SDTD_*` env knobs (neutral
defaults, all overridable):

| Env var | Effect | Default |
|---|---|---|
| `SDTD_CODEX_BIN` / `SDTD_CLAUDE_BIN` | CLI binary name | `codex` / `claude` |
| `SDTD_CODEX_MODEL` / `SDTD_CLAUDE_MODEL` | model override (empty → CLI default) | unset |
| `SDTD_CODEX_REASONING_EFFORT` / `SDTD_CLAUDE_EFFORT` | reasoning effort | unset |
| `SDTD_CLI_TIMEOUT_SECONDS` | per-peer subprocess timeout | `1800` |
| `SDTD_CLI_HTTPS_PROXY` / `SDTD_CLI_HTTP_PROXY` / `SDTD_CLI_ALL_PROXY` | optional proxy values (injected into the child env only if set) | unset |
| `SDTD_CLI_PROXY=0` | force-disable proxy injection even when proxy vars are set | injection on |

Proxy injection is **OFF unless** one of the proxy vars above is explicitly set;
no host:port is ever hardcoded. Run the bridges per use-case artifact dir:

```bash
python3 scripts/sdtd_multiview_cli_bridge.py    run-all <uc-dir>   # Wave 1 multi-view
python3 scripts/sdtd_adversarial_cli_bridge.py  run-all <uc-dir>   # Stage 3B adversarial
```

### Verify

```bash
python3 scripts/sdtd_multiview_cli_bridge.py    check-env
python3 scripts/sdtd_adversarial_cli_bridge.py  check-env
```

`check-env` prints each CLI's resolved path (or `not_found`), the resulting
`dispatch_mode` (`real_peer_dispatch` when both are present, otherwise
`fallback (missing: <name>)`), the model/effort defaults, the proxy-env summary,
and the timeout. When both CLIs resolve you are ready for real peer dispatch.
This is a binary-resolution check only: a CLI can still fail at dispatch time if
it is not logged in or lacks an API key. In that case, the failed peer is
degraded to `fallback_placeholder` while the other peer can still produce a real
view.

### Fallback

If **either** CLI is missing on `PATH`, the bridge writes **deterministic
placeholder views** (tagged `dispatch_mode: fallback_placeholder`) plus a note
that real dispatch was skipped, and still emits a divergence report /
`03b_adversarial_cases.yaml`. Per-peer failures during real dispatch (timeout,
non-zero exit, empty or schema-invalid output) also degrade that one peer to a
placeholder rather than failing. The artifact flow and the gate stay green.

---

## 2. MCP memory service + ONNX — cross-session memory

A locally running `mcp-memory-service` HTTP API gives BUGate cross-session
memory and an experience-promotion loop. The service + its ONNX embedding model
are **user-provided**; `scripts/memory_bus.py` is a thin stdlib-only driver that
only talks to the running service.

### Install

**Check first — reuse before installing.** The bus is machine-level: another
BUGate-governed repo on this machine may already run the shared instance.

```bash
bin/memory-bus-status   # "Memory service OK" → NOTHING to install; just
                        # declare memory.namespace in your profile and stop here
```

Only when no service exists machine-wide, install the runtime **once per
machine** (in the checkout that will host it):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install mcp-memory-service huggingface_hub numpy onnxruntime tokenizers
```

Then **pre-download the ONNX embedding model once** so the service can run
offline / behind a proxy:

```bash
bin/memory-model-fetch
```

This fetches the embedding model (default `sentence-transformers/all-MiniLM-L6-v2`;
override via `BUGATE_ONNX_MODEL`) into `~/.cache/mcp_memory/onnx_models`
(override via `MCP_MEMORY_ONNX_DIR`). **SOCKS-proxy caveat:** the in-service
downloader **cannot traverse a SOCKS `all_proxy`**, which is exactly why you
pre-fetch with this driver — it unsets `all_proxy`/`ALL_PROXY` for the fetch.
`bin/memory-model-fetch` needs a Hugging Face CLI (`hf` or `huggingface-cli`) on
`PATH`; installing `huggingface_hub` into `.venv` provides `hf`. If neither is
present it prints install hints and exits non-zero.
**Fallback:** set `MCP_MEMORY_USE_ONNX=0` to skip ONNX embeddings entirely.

### Wire

```bash
bin/memory-bus-start
```

The wrapper resolves the `memory` binary from `<root>/.venv/bin/memory` or
`PATH`, configures the **system-level data home** `MCP_MEMORY_BASE_DIR`
(resolution: `MCP_MEMORY_BASE_DIR` > `BUGATE_MEMORY_HOME` > `~/.bugate/memory-bus`),
`MCP_MEMORY_STORAGE_BACKEND` (`sqlite_vec`), and `MCP_MEMORY_USE_ONNX` (`1`),
generates client API keys once into `<bus-home>/client.env` (0600, never in
git), then launches the service on `127.0.0.1:${MCP_HTTP_PORT:-8000}`. If the
`memory` binary is not found it prints the `pip install` +
`bin/memory-model-fetch` hint and exits 1.

The bus is **machine-level by design** (ADR-BUGATE-003): ONE service instance
per machine, shared by every BUGate-enabled repo; projects are isolated
by namespace tag (`project:<name>` from each workspace's profile), never by
per-repo databases — so a restart triggered from any repo resolves to the same
database by construction (no split brain). Clients load `client.env` from the
system home first; a legacy in-repo `.memory_bus/client.env` still works but
prints a deprecation hint. The service itself takes daily `.db` backups into
`<bus-home>/backups/` (tune with `MCP_BACKUP_*` env vars).

**Optional hardening (macOS):** register the bus as a user-level LaunchAgent
so it starts at login and is restarted if it dies. Without the agent nothing
changes — `bin/memory-bus-ensure` still starts the bus on demand:

```bash
bin/memory-bus-install-launchd              # RunAtLoad + KeepAlive
bin/memory-bus-install-launchd --uninstall  # stop + remove
```
The driver's project namespace comes from the SUT profile (`memory.namespace`)
or `MEMORY_BUS_PROJECT_TAG`, defaulting to `project:bugate`. Once running, use
the `bin/memory-service-*` and `bin/promote-memory` wrappers (e.g.
`bin/memory-service-note`, `bin/memory-service-search`, `bin/memory-service-lint`,
`bin/promote-memory`).

### Verify

```bash
bin/memory-bus-start    # launch
bin/memory-bus-status   # confirm reachable
```

For a full smoke test, record and search one project-scoped note:

```bash
bin/memory-service-note --agent agent --type finding --msg "memory smoke"
bin/memory-service-search --query "memory smoke" --limit 1
```

Use the BUGate wrappers for verification; a raw `memory status` command may use
the service's default environment instead of the shared bus home database
(`~/.bugate/memory-bus/` by default).

### Fallback

When the service is unreachable, `memory_bus.py` prints a clear hint ("Start it
with `bin/memory-bus-start`") and **exits non-fatally (0)** — its note, search,
and lint subcommands all return 0 when `service_available()` is false, so hooks
and the gate are never blocked by a down memory service.

---

## 3. Agent role isolation — three-layer path guard (Wave 7)

`scripts/check_agent_role_paths.py` is a PreToolUse path guard that stops one
agent role from touching files that belong to another (e.g. keeping a test
implementer from re-deriving expectations out of SUT source). It ships with **no
hardcoded paths or role names** — everything comes from the env and the active
SUT profile, and it is **default OFF**.

### Install

Nothing to install — it is a stdlib-only core script.

### Wire

Enable **per session** by exporting the role and supplying forbidden patterns in
the profile:

```bash
export BUGATE_AGENT_ROLE=builder      # or designer | implementer | <your role>
```

Declare the role's forbidden path patterns under an `agent_roles:` map in the
active profile (a bare list applies to both reads and writes; or use `read:` /
`write:` sub-lists to scope). See the canonical
[`profile-schema.md`](../.shared/skills/bugate/references/profile-schema.md) for
the full shape. Patterns are Python regexes; deny wins, everything else is
allowed.

The PreToolUse wiring is **already shipped** in `.codex/hooks.json` and
`.claude/settings.json` (matcher `Edit|Write`). Hooks locate the engine by
walking up for `scripts/bugate_core.py` or through the plugin/vendor root, then
the guard resolves the active project by walking up from CWD to the nearest
`bugate.config.yaml`. In BUGate core, role isolation is verified through
temporary fixture profiles, not by mounting a SUT. Codex Desktop requires
re-trusting the hook hash after any hook change.

### Verify

With a role exported and a matching forbidden pattern in the profile, attempt an
edit/write to a forbidden path: the guard exits non-zero and prints
`BUGate agent-role path isolation (role=<role>) blocked:` with the offending
path. An allowed path returns 0.

### Fallback

Default **OFF**: if `BUGATE_AGENT_ROLE` is unset/empty, or the active profile
defines no `agent_roles` rules for the role/action, the guard is a **no-op**
(exit 0, allow everything).

---

## Field-tested gotchas

A few setup traps that are cheap to hit and cheap to avoid. The CLI-resolution
and memory-bus notes above cover the rest; these are the ones with no obvious
home elsewhere.

- **Stale npm CLI wrappers shadow the native install.** If a global
  `@anthropic-ai/claude-code` (or a Homebrew/app `codex`) resolves ahead of the
  native binary, `check-env` may pass while dispatch behaves oddly. Remove the
  wrapper (`npm uninstall -g @anthropic-ai/claude-code`) and keep `~/.local/bin`
  ahead of older app/Homebrew paths on `PATH`; confirm with `type -a codex` /
  `type -a claude`.
- **Codex skill discovery needs valid YAML frontmatter.** If Codex logs
  `failed to load skill … invalid YAML`, check the frontmatter of
  `.shared/skills/bugate/SKILL.md` first — a `description:` that contains a colon
  must be quoted.
- **Confirm the ONNX model actually landed.** After `bin/memory-model-fetch`,
  verify a usable model exists rather than assuming the download finished:
  `find ~/.cache/mcp_memory/onnx_models -name '*.onnx' -print`. With a usable
  `onnx/model.onnx` present the service starts; to defer the model entirely,
  `MCP_MEMORY_USE_ONNX=0 bin/memory-bus-start`.
