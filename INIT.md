# BUGate — Init Prompt

> **Paste this whole file to your AI coding agent (Claude Code / Codex) right after cloning BUGate**, and it will verify your environment, confirm the gate engine works, and route you to the right usage path — **imported mode** (default: BUGate goes into your SUT test repo) or the **maintainer workbench** (mount a SUT into this repo). A human can follow the same steps manually.
>
> **Good news first:** the BUGate *core* is **zero-dependency** — pure Python standard library. There is **nothing to `pip install`** to use the gate engine. "Installing dependencies" here means *verifying Python* and *optionally* adding the agent-memory subsystem.

---

## Agent instructions

You are bootstrapping a freshly cloned **BUGate** repository — a SUT-agnostic, AI-driven black-box test gate engine. Do the following in order, report the result of each step, and stop to ask the user only if a step fails.

### Step 0 — Choose the path (imported vs workbench)

BUGate has two usage modes (normative rules: `CHARTER.md` §2). Ask the user which applies:

- **User path — imported mode (default).** They are adopting BUGate to govern a
  SUT automation test repo. Run Steps 1–3 below to verify the core, then run
  the installer — `python3 scripts/bugate_init.py <sut-repo>` — or follow
  README **"Quickstart A) Imported mode"** manually: vendor the engine + skill
  into the SUT repo, wire the hooks there, and **commit** `bugate.config.yaml`
  + profile in that repo. Daily agent sessions then open the **SUT repo**, not
  this one.
  For a real end-to-end import — the SUT BUGate was extracted from, re-adopting
  its own kit — read
  [`docs/case-studies/origin-sut-import.md`](docs/case-studies/origin-sut-import.md).
- **Maintainer path — core workbench.** They are developing BUGate itself
  (core scripts/hooks, methodology, profile schema, gates, demos, cross-SUT
  regression). Continue with **all** steps below, including "Mount a SUT" via
  symlink + local uncommitted profile pointer.

### Step 1 — Check the one hard requirement: Python

```bash
python3 --version    # require Python >= 3.9 (3.10+ recommended)
```

The gate engine imports only the standard library (`argparse json os pathlib re dataclasses typing …`). If `python3` is 3.9+, **no dependency install is needed for the core**.

### Step 2 — Verify the core works (zero-install smoke test)

Run from the repo root and confirm each line:

```bash
python3 -m py_compile scripts/*.py && echo "compile: OK"
python3 -c "import sys; sys.path.insert(0,'scripts'); import bugate_core; print('engine import: OK')"
python3 scripts/check_bugate_inventory_semantics.py .shared/skills/bugate/templates   # expect: PASS
python3 scripts/check_bugate_brief_semantics.py     .shared/skills/bugate/templates   # expect: PASS
```

Expected: every script compiles, `bugate_core` imports, and both gates print `PASS`. If so, **the core is ready — no dependencies were installed.**

### Step 3 — Confirm config loads

```bash
cd scripts && python3 -c "import bugate_core as c; cfg=c.load_config(); print('mode=', cfg.get('mode'), '| guard=', cfg.get('guarded_path_regex'), '| precode=', len(c.required_precode_artifacts(cfg)))" ; cd ..
```

Expect `mode= core | guard= [] | precode= 5`. The core ships **unmounted**: the write-guard is disabled and `artifact_dir` is empty until a SUT profile sets them.

### Step 4 — (Optional) wire your agent runtime

BUGate runs as a skill under Claude Code and Codex:

- Skill: `.shared/skills/bugate/` (discovered via the symlinks in `.claude/skills/` and `.codex/skills/`).
- Hooks: `.claude/settings.json` and `.codex/hooks.json`. Root resolution is **git-free** and split: hooks find the engine by walking up for `scripts/bugate_core.py`; gate scripts find the governed workspace via the nearest `bugate.config.yaml` (sentinel fallback for the workbench).
- **Codex only:** changing any hook requires re-trusting its hash in the Codex hook-management UI.

No install is needed for this — the hooks invoke the same stdlib-only scripts you verified in Step 2.

---

## Mount a SUT (maintainer workbench path)

> For governing a SUT day-to-day, use **imported mode** instead (Step 0; README
> Quickstart A) — BUGate goes into the SUT repo, and the profile is committed
> there. The mount below is the **workbench** setup: this repo stays the
> project root, and the profile pointer stays local and uncommitted.

The core does nothing on its own; you mount a system under test via a **profile**.

1. Create a profile by copying the sample, then declare your SUT's surfaces:

   ```bash
   mkdir -p sut && cp examples/sample-sut.profile.yaml sut/<name>.profile.yaml
   ```

   ```yaml
   artifact_dir: docs/usecases                 # where UC artifacts (01–03…) live
   guarded_path_regex:                          # which test files the write-guard protects
     - "tests/.*/test_.*[.]py$"
   required_precode_artifacts:                  # override the default 01–05 set if you want
     - 01_business_brief.md
     - 02_testability.md
     - 03_inventory.yaml
   ```

   A filled, passing example gate stack lives in [`examples/demo-sut/`](examples/demo-sut/).

2. Point the core at it in `bugate.config.yaml`:

   ```yaml
   profile: sut/<name>.profile.yaml
   ```

   > Local, per-clone edit — **don't commit** this `profile:` line; each clone mounts its own SUT.

   > **Separate repo? Symlink it, don't nest it.** If the SUT test workspace is
   > its own git repo, keep it in its own directory and symlink it in
   > (`ln -s ../my-sut my-sut`), then ignore the symlink **locally**
   > (`printf '/my-sut\n' >> .git/info/exclude` — no trailing slash; a symlink
   > isn't a directory to git). Never nest the SUT repo inside BUGate's tree:
   > the symlink keeps the gate working on the same relative paths while the two
   > repos stay fully independent (separate histories, remotes, lifecycles).

3. Full profile reference: [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md).
   The methodology and gate flow: [`README.md`](README.md) and [`docs/qa-methodology/METHOD.md`](docs/qa-methodology/METHOD.md).

---

## Optional capabilities — you install the runtime, we ship the driver scripts

The zero-dependency core covers the **4-layer gate**. Three further mechanisms ship as **driver scripts** that call out to runtimes **you install yourself**; each **degrades gracefully** when its runtime is absent.

### a) Dual-agent multi-view cross-audit (Wave 1)

Two independent AI agents extract the business model in parallel; their divergence is reported before Layer 1 is accepted.

- **You install:** the `codex` and `claude` CLIs (on `PATH`).
- **We ship:** `scripts/sdtd_multiview.py` + `scripts/sdtd_multiview_cli_bridge.py`.

```bash
python3 scripts/sdtd_multiview_cli_bridge.py check-env          # shows codex/claude presence + dispatch_mode
python3 scripts/sdtd_multiview_cli_bridge.py run-all <uc-dir>   # real peer dispatch if both CLIs present; else placeholder
```

Tune via env: `SDTD_CODEX_MODEL` / `SDTD_CLAUDE_MODEL` / `SDTD_*_EFFORT`, proxy `SDTD_CLI_*_PROXY`. If either CLI is missing it **falls back to a deterministic placeholder** so the artifact flow still runs.

### b) Agent memory + experience promotion

Cross-session memory and a confirm/promote loop for learned findings.

- **You install (MCP):** `pip install mcp-memory-service`, then pre-download the ONNX embedding model into `~/.cache/mcp_memory/onnx_models` (one-time; its in-service downloader cannot traverse a SOCKS proxy).
- **We ship:** `scripts/memory_bus.py` + `bin/memory-bus-*` + `bin/memory-service-*` + `bin/promote-memory`.

```bash
bin/memory-bus-start                                    # launches the service (resolves `memory` from .venv or PATH)
bin/memory-bus-status
bin/memory-service-note --agent <a> --type finding --msg "..."
bin/promote-memory ...                                  # promote a finding to status:confirmed
```

Namespace comes from the SUT profile (`memory.namespace`) or `MEMORY_BUS_PROJECT_TAG` (default `project:bugate`). The service is **machine-level** (ADR-BUGATE-003): one instance per machine with its data home at `~/.bugate/memory-bus/` (override `BUGATE_MEMORY_HOME`; the service's own `MCP_MEMORY_BASE_DIR` wins), shared by every governed repo and isolated per project by the namespace tag — a governed repo only declares its namespace in its profile and does NOT scaffold a local service dir. A legacy in-repo `.memory_bus/` is still read as a deprecated fallback. Optional macOS hardening: `bin/memory-bus-install-launchd` (RunAtLoad + KeepAlive; `--uninstall` to remove). If the service/CLI is absent, the scripts print an install hint and exit non-fatally.

### c) Three-layer agent-role isolation (Wave 7)

- **We ship:** `scripts/check_agent_role_paths.py` (a PreToolUse path guard).
- Enable per session with `BUGATE_AGENT_ROLE=builder|designer|implementer`; forbidden path patterns come from your SUT profile's `agent_roles:` map. Unset role / no profile rules → no-op (default-OFF).

---

## Summary

| Goal | You install | We ship | Degrades if absent? |
|---|---|---|---|
| 4-layer gate engine (core) | **nothing** | gate scripts + templates | — (always works) |
| Run under an agent | nothing | `.claude` / `.codex` hooks | — |
| Mount a SUT (workbench) / import into a SUT repo | nothing | `bugate.config.yaml` + profile schema | — |
| Dual-agent cross-audit | `codex` + `claude` CLIs | `sdtd_multiview*` | yes → deterministic placeholder |
| Agent memory + promotion | `mcp-memory-service` + ONNX model | `memory_bus.py` + `bin/memory-*` | yes → install hint, non-fatal |
| Agent-role isolation | nothing | `check_agent_role_paths.py` | — (default-OFF) |

**Bottom line:** `git clone` → `python3 --version` (3.9+) → run the Step 2 smoke test → the **core is ready with zero installs**. The dual-agent and memory capabilities are opt-in: install their runtime (the CLIs / `mcp-memory-service`) and the driver scripts we ship will use them, falling back cleanly when they're not present.
