# BUGate

**BUGate** is a SUT-agnostic methodology and gate engine for AI-driven **black-box test development**. It forces an AI agent to build a *verifiable business understanding* of a system under test (SUT) — propositions, oracles, boundaries, states — and to pass review gates **before** any test code is written.

This repository is the reusable **core**. It contains no product's tests or business data; a **SUT profile** mounts a specific system onto the core.

## The three-layer model

| Layer | What it is | Where it lives |
|---|---|---|
| **Core** (this repo) | Methodology + gate engine + templates + agent adapters. Knows nothing about any specific SUT. | here |
| **Profile** (the bridge) | A small declarative file pointing the core at one SUT's paths, artifact dir, guarded test glob, markers, namespace. | with each SUT |
| **SUT** | The system under test + its automation framework, tests, docs, fixtures, secrets. | its own repo/workspace |

One core can mount **one** SUT or **many** (N=1 is just the degenerate case). The core knows nothing SUT-specific; everything SUT-aware lives in the profile. See [`docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md`](docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md).

## The gate flow

Test development is gated through layered artifacts; code is blocked until the pre-code artifacts reach `gate_status: passed`:

1. **Layer 1 — Business Brief** (`01_business_brief.md`) — SUT boundary, propositions (`P-xxx`), business oracles (`O-xxx`), boundaries, states, open questions.
2. **Layer 2 — Testability** (`02_testability.md`) — the cheapest valid test layer per proposition, resource strategy, side-effect classification, skip/xfail rules.
3. **Layer 3 — Inventory** (`03_inventory.yaml`) — concrete cases bound to propositions + oracles.
4. **Layer 3A / 3B** (`03a_test_cases.md`, `03b_adversarial_cases.yaml`) — human-readable review cases + adversarial/red-team cases.
5. **Layer 4 — Code** — written only after the above pass.

First principles live in [`.shared/skills/bugate/references/sdtd-constitution.md`](.shared/skills/bugate/references/sdtd-constitution.md); the full methodology in [`docs/qa-methodology/METHOD.md`](docs/qa-methodology/METHOD.md) and [`SOP.md`](docs/qa-methodology/SOP.md).

## Quickstart — mount a SUT

1. Point `bugate.config.yaml` at your SUT profile (or keep `mode: core` for the unmounted engine):

   ```yaml
   profile: path/to/my-sut.profile.yaml
   ```

2. In the profile, declare the SUT's surfaces:

   ```yaml
   artifact_dir: docs/usecases             # where UC artifacts live
   guarded_path_regex:                     # which test files the write-guard protects
     - "tests/.*/test_uc_.*\\.py$"
   required_precode_artifacts:             # override the default 01–05 set if needed
     - 01_business_brief.md
     - 02_testability.md
     - 03_inventory.yaml
   ```

3. Run a gate:

   ```bash
   python3 scripts/check_bugate.py <test-file-or-patch>      # physical write guard
   python3 scripts/check_bugate_inventory_semantics.py <uc-dir>
   ```

The core ships with `guarded_path_regex: []` (write-guard **disabled**) and an empty `artifact_dir`; a SUT profile turns these on.

## Agent runtimes

BUGate runs under **Claude Code** and **Codex** via the skill at `.shared/skills/bugate/` and the hooks in `.claude/` / `.codex/`. The gate engine is **stdlib-only** (no third-party deps) and resolves the repo root git-free via a sentinel (`AGENTS.md` + `.shared/`). Note: adding or changing a Codex hook requires re-trusting its hash.

## Layout

```
bugate.config.yaml          # core config; a SUT profile overrides its values
AGENTS.md                   # agent behavior protocol (SUT-neutral)
scripts/                    # gate engine + SDTD orchestration (stdlib-only)
.shared/skills/bugate/      # the BUGate skill: SKILL.md, references/, templates/, adapters/
docs/qa-methodology/        # METHOD.md, SOP.md, evolution timeline, decision records
```

## License

[MIT](LICENSE).
