# BUGate — Init Prompt

[English](INIT.md) | [简体中文](INIT.zh-CN.md)

> **Paste this whole file to your AI coding agent (Claude Code / Codex) right after cloning BUGate**, and it will verify your environment, confirm the gate engine works, and route you to the right path — **imported mode** (the only usage mode: BUGate goes into your SUT test repo) or **developing BUGate itself** (maintainers; pure core iteration only). A human can follow the same steps manually.
>
> **Good news first:** the BUGate *gate engine* is **zero-dependency** — pure Python standard library, **nothing to `pip install`** to run the gates. The **memory bus** (long-term memory + promotion) is a **required core component**, but you don't install it by hand: `bugate init` / `bin/memory-bus-*` auto-install the machine-level service once and self-heal on an anomaly (`BUGATE_MEMORY_NO_INSTALL=1` for offline/locked-down machines). The **dual-agent CLIs** remain optional.
>
> Until BUGate ships a packaged console-script, prose shorthand `bugate init`
> means `python3 scripts/bugate_init.py`.
>
> The current release is **v0.4.0**. Its tar and zip archives are accompanied
> by `bugate-0.4.0.SHA256SUMS`; imported-mode adoption must verify the selected
> archive before extraction (see `IMPORT_PROMPT.md`).

---

## Agent instructions

You are bootstrapping a freshly cloned **BUGate** repository — a SUT-agnostic, AI-driven black-box test gate engine. Do the following in order, report the result of each step, and stop to ask the user only if a step fails.

### Step 0 — Choose the path (use it vs develop it)

BUGate has one usage mode — imported (normative rules: `CHARTER.md` §2 + Amendment A4). Ask which path applies:

- **User path — imported mode (default).** They are adopting BUGate to govern a
  SUT automation test repo. Run Steps 1–3 below to verify the core, then use
  [`IMPORT_PROMPT.md`](IMPORT_PROMPT.md) for the release-tarball adoption path,
  or run the installer from this checkout —
  `python3 scripts/bugate_init.py <sut-repo>` — if you are intentionally using
  a source checkout. In either path, BUGate vendors the engine + skill into the
  SUT repo, wires the hooks there, and creates `bugate.config.yaml` + profile
  to be **committed in that repo**. Daily agent sessions then open the **SUT
  repo**, not this one.
- **Maintainer path — developing BUGate itself (not a usage mode).** They are working on the tool
  (core scripts/hooks, methodology, profile schema, gates, cross-SUT
  regression). Continue with the core verification steps below. Real-SUT
  validation happens by importing BUGate into an external SUT test repo or a
  scratch repo outside BUGate core; do not mount a SUT into this repository.

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

Expect `mode= core | guard= [] | precode= 5`. The core ships **pure**: the write-guard is disabled and `artifact_dir` is empty until an imported SUT profile sets them in the governed SUT test repo.

### Step 4 — (Optional) wire your agent runtime

BUGate runs as a skill under Claude Code and Codex:

- Skill: `.shared/skills/bugate/` (discovered via `.claude/skills/` for Claude Code and `.agents/skills/` for Codex; `.codex/skills/` remains a legacy Codex compatibility bridge).
- Hooks: `.claude/settings.json` and `.codex/hooks.json` for project-local development; plugin installs use plugin-root `hooks/hooks.json`. Root resolution is **git-free** and split: hooks find the engine by walking up for `scripts/bugate_core.py` or via the plugin/vendor root; gate scripts find the active project via the nearest `bugate.config.yaml` (sentinel fallback for self-development). In v0.4.0, the hook set keeps the pre-code guard and role-evidence guard independent; the orchestrator/Core mutators also run the same role preflight because Python writes do not trigger an agent PreToolUse hook.
- Plugins: `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` are manifests only; shared `skills/`, `commands/`, `agents/`, `hooks/`, `scripts/`, and `bin/` stay at the plugin root.
- **Codex only:** changing any hook requires re-trusting its hash in the Codex hook-management UI. **Claude plugin changes:** run `/reload-plugins` or reinstall/update the plugin.

No install is needed for this — the hooks invoke the same stdlib-only scripts you verified in Step 2.

---

## Developing BUGate itself (pure core iteration)

> For governing a SUT day-to-day, use **imported mode** instead (Step 0; README
> Quickstart A) — BUGate goes into the SUT repo, and the profile is committed
> there. BUGate core iteration itself remains SUT-neutral and does not mount a
> SUT workspace.

The core does nothing SUT-specific on its own. Maintainers validate the reusable
engine with templates and temporary fixtures:

```bash
python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
python3 tests/test_write_guard_layouts.py
python3 tests/test_init_scaffold.py
python3 tests/test_hook_surface_parity.py
python3 scripts/check_no_sut_terms.py --terms-file tests/fixtures/legacy-sut-terms.txt
```

For real adoption validation, run `python3 scripts/bugate_init.py <sut-repo>`
against an external SUT test repo or a scratch repo outside BUGate core, then
open that SUT repo as the project root. Full profile reference:
[`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md).
The methodology and gate flow: [`README.md`](README.md) and
[`docs/qa-methodology/METHOD.md`](docs/qa-methodology/METHOD.md).

---

## Runtimes beyond the stdlib core

The zero-dependency core covers the **4-layer gate**. Three further mechanisms
extend it. **The memory bus (b) is REQUIRED** — `bugate init` /
`bin/memory-bus-*` auto-install and self-heal it, you run nothing by hand. The
**dual-agent CLIs (a)** remain optional and degrade gracefully when absent.
Wave 7 lifecycle governance (c) is stdlib-only and opt-in per imported profile;
once set to `required`, it deliberately fails closed rather than degrading.

### a) Dual-agent multi-view cross-audit (Wave 1)

Two independent AI agents extract the business model in parallel; their divergence is reported before Layer 1 is accepted.

- **You install:** the `codex` and `claude` CLIs (on `PATH`).
- **We ship:** `scripts/sdtd_multiview.py` + `scripts/sdtd_multiview_cli_bridge.py`.

```bash
python3 scripts/sdtd_multiview_cli_bridge.py check-env          # shows codex/claude presence + dispatch_mode
python3 scripts/sdtd_multiview_cli_bridge.py run-all <uc-dir>   # real peer dispatch if both CLIs present; else placeholder
```

Tune via env: `SDTD_CODEX_MODEL` / `SDTD_CLAUDE_MODEL` / `SDTD_*_EFFORT`, proxy `SDTD_CLI_*_PROXY`. If either CLI is missing it **falls back to a deterministic placeholder** so the artifact flow still runs.

### b) Agent memory + experience promotion (REQUIRED core)

Cross-session long-term memory, dual-agent progress sync + relay, and a
confirm/promote loop — a BUGate setup is incomplete without it.

- **Auto-installed + self-healing:** `bugate init` / `bin/memory-bus-*` reuse a
  running machine-level service, restart a crashed one, or — when absent —
  install it once (`~/.bugate/venv` + `mcp-memory-service` + ONNX model). You run
  nothing by hand; declare `memory.namespace` in the profile and that's it.
- **Manual/offline path** (or when `BUGATE_MEMORY_NO_INSTALL=1`): `pip install mcp-memory-service`, then pre-download the ONNX model into `~/.cache/mcp_memory/onnx_models` (its in-service downloader cannot traverse a SOCKS proxy).
- **We ship:** `scripts/memory_bus.py` + `bin/memory-bus-*` + `bin/memory-service-*` + `bin/promote-memory`.

```bash
bin/memory-bus-start                                    # reuse running / restart crashed / install once if absent
bin/memory-bus-status
bin/memory-service-note --agent <a> --type finding --msg "..."
bin/promote-memory ...                                  # promote a finding to status:confirmed
```

Namespace comes from the SUT profile (`memory.namespace`) or `MEMORY_BUS_PROJECT_TAG` (default `project:bugate`). The service is **machine-level** (ADR-BUGATE-003): one instance per machine with its data home at `~/.bugate/memory-bus/` (override `BUGATE_MEMORY_HOME`; the service's own `MCP_MEMORY_BASE_DIR` wins), shared by every governed repo and isolated per project by the namespace tag — a governed repo only declares its namespace in its profile and does NOT scaffold a local service dir. A legacy in-repo `.memory_bus/` is still read as a deprecated fallback. Optional macOS hardening: `bin/memory-bus-install-launchd` (RunAtLoad + KeepAlive; `--uninstall` to remove). The memory bus is a **required core component**: `bugate init` / `bin/memory-bus-*` **auto-install** the machine-level service once when absent and **self-heal** (restart) on an anomaly. Ordinary recall/notes/Stop and every edit remain best-effort/local; with Wave 7 `memory_mode: required`, a transient outage intentionally blocks only the next handoff/acceptance/completion transition and publishes no unlocking receipt. Set `BUGATE_MEMORY_NO_INSTALL=1` to skip auto-install on locked-down/offline machines.

### c) Auditable lifecycle-role governance (Wave 7)

- **We ship:** `bin/bugate-role`, `scripts/role_governance.py`,
  `scripts/check_role_evidence.py`, and the independent legacy-compatible
  `scripts/check_agent_role_paths.py` path guard.
- **Default:** `role_governance.mode: off`; v0.3.x profiles behave unchanged.
  `agent_roles` can still be used alone and is not the lifecycle state machine.
- **Required mode:** unset/wrong roles and missing required session IDs block;
  historical passed UCs need new human/designer/implementer receipts. Start
  three separate sessions with:

  ```bash
  bin/bugate-role run --role designer -- codex
  bin/bugate-role run --role implementer -- claude
  bin/bugate-role run --role reviewer -- codex
  ```

  Run pre-code `--init` and `--auto` as separate commands. Once a human has
  changed 03B to `gate_status: passed`, do not run `--auto` again: the designer
  records the existing decision with `bugate-role approve`, hands off, and the
  new implementer session accepts the receipt's exact `memory.memory_id`.
  Implementation handoff and a fresh reviewer acceptance are required before
  post-run. See [the operating sequence in README](README.md#wave-7-auditable-lifecycle-roles-v040)
  and the [normative protocol](docs/qa-methodology/ROLE_GOVERNANCE_PROTOCOL.md).

Normal edits verify only the local hash chain; strict Memory failures block the
next transition and can be retried idempotently after recovery. Profile or
pre-code drift restarts from human/designer evidence; implementation drift
restarts from implementer handoff/reviewer acceptance. Evidence is append-only:
never delete or hand-edit it as a reset.

`bugate-role run` exports role/session values only to its child. Hooks cannot
export into a parent, and an already-running Desktop app must be relaunched
from the intended environment. Codex Desktop must also re-trust the changed
v0.4.0 hook hash. The chain is audit evidence, not strong identity:
`approved_by` is declarative, local hooks cannot catch arbitrary shell or
external-editor writes, and non-repudiation requires OS/container/managed-runner
or role-scoped credential isolation.

---

## Full-capability self-check (after setup)

Once the core, the agent runtime, and any optional runtimes are installed and
logged in, run one **end-to-end** capability audit. Prefer the built-in skill —
it is discovered via `.claude/skills/bugate-full-check` and
`.agents/skills/bugate-full-check` (`.codex/skills/bugate-full-check` remains
the legacy Codex bridge):

```text
Use $bugate-full-check to verify this BUGate checkout end to end.
```

The skill lives at `.shared/skills/bugate-full-check/` and ships a runnable
driver:

```bash
python3 .shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke
python3 .shared/skills/bugate-full-check/scripts/run_full_check.py --mode full
```

If the runtime cannot auto-discover the skill yet, hand the agent the **fallback
prompt** below verbatim. The goal is not to stop at `check-env`, but to
distinguish "installed" / "core works" / "optional runtimes work" / "a real SUT
test workspace is activated through a profile". (Field setup gotchas — native
installers, `PATH` ordering, the extra ONNX runtime packages — live in
[`docs/SETUP-OPTIONAL.md`](docs/SETUP-OPTIONAL.md).)

```text
Run a full-capability self-check on this BUGate repo, strictly following
AGENTS.md and .shared/skills/bugate/SKILL.md.

Requirements:
1. First read .shared/skills/bugate/SKILL.md and confirm whether this is BUGate
   core mode or an imported SUT test repo with a committed profile. Do not mount
   a SUT into BUGate core and do not invent any SUT fact.
2. Verify the core 4-layer gate (no example SUT tree in-repo; templates +
   ephemeral fixtures only):
   - python3 -m py_compile scripts/*.py
   - python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
3. Verify Codex / Claude Code:
   - type -a codex; type -a claude
   - codex --version; claude --version
   - Run "Reply exactly: ok" through both codex exec and claude -p to confirm
     real model calls, not just check-env.
4. Verify both peer bridges:
   - python3 scripts/sdtd_multiview_cli_bridge.py check-env
   - python3 scripts/sdtd_adversarial_cli_bridge.py check-env
   - Use python3 scripts/sdtd_orchestrator.py <tmp>/peer-uc --init to scaffold a
     template UC under /tmp, then run-all multi-view and adversarial on it, and
     confirm both Codex and Claude write a real peer view, not
     fallback_placeholder.
5. Verify the memory bus:
   - bin/memory-bus-status
   - bin/memory-service-note --agent agent --type finding --msg "memory smoke"
   - bin/memory-service-search --query "memory smoke" --limit 1
   - find ~/.cache/mcp_memory/onnx_models -name '*.onnx' -print
   - MCP_MEMORY_BASE_DIR="${BUGATE_MEMORY_HOME:-$HOME/.bugate/memory-bus}" MCP_MEMORY_STORAGE_BACKEND=sqlite_vec
     MCP_MEMORY_USE_ONNX=1 PATH="$PWD/.venv/bin:$PATH" memory status
     Expect a healthy service and, where possible, confirm the onnxruntime/ONNX
     path is exercised.
6. Verify Wave 0 / Wave 8 (graceful-degradation contract, no spec fixture):
   - python3 scripts/check_prd_health.py --gate must print profile_required and exit 0
   - python3 scripts/oracle_falsification.py --gate likewise
   - python3 scripts/generate_assertion_coverage_matrix.py --help must exit cleanly
7. Verify the physical write guard (dual layout, ephemeral fixtures):
   - python3 tests/test_write_guard_layouts.py must print PASS (both layouts) —
     imported (config-marked root) and engine-development (sentinel fallback)
     each allow / block / fail-closed.
8. Verify Wave 7 role governance (temp profile, as full-check constructs it):
   - Confirm `agent_roles` legacy path allow/deny still works independently.
   - With `role_governance.mode: required`, prove unset/wrong role blocks, then
     run the scratch designer → human acceptance → handoff → fresh implementer
     acceptance → guarded-write allow → implementer handoff → fresh reviewer
     acceptance → post-run → completion chain. Include strict-Memory failure
     and profile/artifact/implementation drift negative controls; never use a
     real SUT fixture.
9. Verify profile hardening gates (enforced-effect probe):
   - With the orchestrator --init template UC plus a temp profile carrying
     require_multiview: true, run v13 pre-code and confirm it is rejected
     (non-zero exit) for the missing divergence_report.md.
10. Clean up every /tmp self-check artifact; do not touch SUT facts or template
    source files.

Finally output a table split into:
- installed and verified working
- present but needs a real SUT profile / test workspace to activate
- gates that by design need human acceptance
- parts not yet scripted or only defined in the methodology

The conclusion must clearly separate:
- "BUGate core + optional runtimes are working"
- "every gate for a real SUT test workspace is activated"

If bugate.config.yaml is still mode: core with guarded_path_regex: [], you may
not claim a real SUT test workspace is fully gated — only that the core,
ephemeral fixtures, and optional runtimes are verified.
```

---

## Summary

| Goal | You install | We ship | Degrades if absent? |
|---|---|---|---|
| 4-layer gate engine (core) | **nothing** | gate scripts + templates | — (always works) |
| Run under an agent | nothing | `.claude` / `.codex` hooks | — |
| Import into a SUT repo | nothing | `bugate.config.yaml` + profile schema | — |
| Dual-agent cross-audit | `codex` + `claude` CLIs | `sdtd_multiview*` | yes → deterministic placeholder |
| Agent memory + promotion (**required core**) | nothing — auto-installed by `bugate init` | `memory_bus.py` + `bin/memory-*` | required; auto-installs + self-heals; ordinary edits stay non-blocking, strict lifecycle transitions fail closed |
| Path-role isolation | nothing | `check_agent_role_paths.py` | — (independent, default-OFF) |
| Auditable lifecycle roles | nothing | `bugate-role` + role-evidence hook/state machine | opt-in; `required` fails closed |

**Bottom line:** `git clone` → `python3 --version` (3.9+) → run the Step 2 smoke test → the **gate engine is ready with zero installs**. The **memory bus is required** and auto-installs / self-heals via `bugate init` / `bin/memory-bus-*` (`BUGATE_MEMORY_NO_INSTALL=1` to opt out on offline hosts). The **dual-agent CLIs** stay opt-in — install them and the driver scripts we ship will use them, falling back cleanly when absent.
