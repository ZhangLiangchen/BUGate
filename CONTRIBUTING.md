# Contributing to BUGate

BUGate core is a **SUT-neutral, zero-dependency** (pure Python standard library)
black-box test gate engine. Contributions must keep it portable across any
System Under Test (SUT). This guide covers the de-SUT contract, the exact local
checks to run before a PR, where things go, and PR conventions.

Read first: [`AGENTS.md`](AGENTS.md) (Core Rules) and
[`docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md`](docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md)
(ADR-BUGATE-001, the three-layer architecture). The bootstrap and verification
commands live in [`INIT.md`](INIT.md).

---

## 1. The de-SUT contract

Core holds **method, artifact templates, structural gate criteria, hook
mechanism, and adapter layout** — and nothing tied to a single product. Per the
ADR layer split:

| Layer | Where it lives | What it holds |
|---|---|---|
| **Core** | this repository | neutral method, templates, gates, hooks, adapters |
| **SUT Profile** | the governed SUT workspace / profile package | paths, commands, evidence sources, guarded patterns, resource policy, runtime kind |
| **SUT** | the product repository | source, API docs, fixtures, tests, secrets, live evidence |

Concretely (AGENTS.md Core Rules 1–2 and 5):

- Never add SUT source code, product API snapshots, environment secrets,
  credentials, generated caches, or project-specific fixtures to core.
- Put SUT paths, resource policies, environment names, auth rules, and tool
  commands in a **SUT profile**, not in core.
- If a change needs SUT-specific facts, stop at the profile boundary and add a
  profile key (or ask for a governed SUT workspace) instead of inventing product details.

### The forbidden-term guard

`scripts/check_no_sut_terms.py` greps the core tree
(`scripts`, `bin`, `.shared/skills`, `docs`, `examples`, `.github`, plus the
root `AGENTS.md` / `README.md` / `INIT*.md` / `CONTRIBUTING.md` /
`bugate.config.yaml`) for unambiguous product/identity tokens and exits non-zero
on any match. Run it before every PR:

```bash
python3 scripts/check_no_sut_terms.py
```

Generic English prose (the words order, chain, wallet) and the neutral
`docs/usecases` default artifact dir are intentionally **not** forbidden — only
unambiguous SUT tokens are.

> Mounting a SUT locally (core-workbench mode, maintainers)? Keep your
> `profile:` pointer in this repo's `bugate.config.yaml` uncommitted — it's a
> per-clone local edit, so the committed core (which this guard scans) stays
> SUT-neutral. In imported mode (the default, CHARTER §2.2) the reverse holds:
> the governed SUT repo commits its own config + profile.

### The allow-marker (for deliberate occurrences)

If a forbidden token must appear on a line for a legitimate reason (for example,
the guard's own term list, or documentation *about* the guard), append the
trailing marker to that line:

```text
# bugate: allow-sut-term
```

The guard skips any line containing `bugate: allow-sut-term`. Use it sparingly
and only for genuinely necessary occurrences — it is an escape hatch, not a way
to smuggle product facts into core.

---

## 2. Local verification before a PR (mirror CI)

Run the full set from the repo root. This mirrors the
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) `gate` job; if these pass
locally, CI should pass.

```bash
# 1. Everything compiles (stdlib-only core)
python3 -m py_compile scripts/*.py

# 2. The four semantics gates over the shipped templates
python3 scripts/check_bugate_brief_semantics.py     .shared/skills/bugate/templates
python3 scripts/check_bugate_layer2_semantics.py    .shared/skills/bugate/templates
python3 scripts/check_bugate_inventory_semantics.py .shared/skills/bugate/templates
python3 scripts/check_bugate_v13_semantics.py       .shared/skills/bugate/templates --scope pre-code

# 3. De-SUT forbidden-term guard
python3 scripts/check_no_sut_terms.py

# 4. The worked demo passes the strict gate
python3 scripts/check_bugate_v13_semantics.py examples/demo-sut --scope all --require-passed
```

CI also runs the per-UC hardening gates, the Wave 0 PRD-health gate, the Wave 8
falsification + coverage-matrix gates, an orchestrator init smoke, and a
stdlib-only import check — see `ci.yml` for the exact invocations. If your change
touches any of those subsystems, run the matching steps too. The simplest way to
catch everything is to run each step listed in `ci.yml` locally before opening
the PR.

The zero-install smoke test in [`INIT.md`](INIT.md) (Step 2 / Step 3) is the
fastest sanity check that the engine still imports and config still loads.

---

## 3. Repo layout & where things go

| Path | What it is |
|---|---|
| `scripts/` | the gate engine and driver scripts — **stdlib-only** |
| `bin/` | thin wrappers (e.g. memory-bus / promote helpers) |
| `.shared/skills/bugate/` | the shared skill: `SKILL.md`, `references/`, `templates/`, `adapters/` |
| `docs/qa-methodology/` | SUT-neutral method, SOP, ADR, protocols |
| `examples/` | `demo-sut/` (worked, passing gate stack), `sample-sut.profile.yaml` |
| `bugate.config.yaml` | core default config; ships with **no profile bound** (no guarded paths) |

Rules of thumb:

- **Core stays stdlib-only.** Scripts may import only the Python standard
  library and sibling modules under `scripts/` (CI enforces this with an AST
  import check). Do not add a third-party dependency to core.
- **SUT facts go in a profile**, never in core. The full key contract is
  [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md).
  Core scripts ignore unknown profile keys, so a profile may add its own runtime
  commands, evidence fetchers, environments, resources, or auth.

**Adding a script (`scripts/*.py`):** keep imports stdlib-only; resolve the
governed workspace root via `bugate_core.find_root()` (nearest
`bugate.config.yaml` up from CWD, workbench-sentinel fallback, no git
dependency) and engine assets (templates, sibling scripts) via
`bugate_core.find_engine_root()` — never by assuming a CWD or using git
metadata. Add it to the relevant CI step if it is a gate. Run it through the
de-SUT guard.

**Adding a template (`.shared/skills/bugate/templates/`):** keep every field and
example SUT-neutral. Templates are checked by the semantics gates above, so run
the four gates against the templates dir after editing.

**Adding an adapter:** adapters live under
`.shared/skills/bugate/adapters/` (ADR Implementation Notes). Keep them neutral;
SUT-specific wiring belongs in the governed workspace, not the adapter.

**Hooks** (`.claude/`, `.codex/`) must call only SUT-neutral scripts from
`scripts/` and must not depend on git metadata (AGENTS.md Hook Policy). Note:
changing `.codex/hooks.json` may require Codex Desktop to re-trust the hook hash.

---

## 4. Promoting a local lesson into core

A lesson learned while testing one SUT does **not** go straight into core. It
follows the **Experience Promotion Protocol**:
[`docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md`](docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md).

The admission test (ADR Promotion Rule) is one sentence: a lesson may enter core
**only if it can be stated without referencing a single SUT's business entities,
paths, environments, credentials, or fixtures.** When in doubt, keep it in the
SUT profile. There is no automated generalization-gate script — neutrality is a
human/agent obligation discharged *before* promotion. See the protocol for the
restatement test, the (recommended) two-SUT corroboration bar, and the mechanics
(`scripts/memory_bus.py` + `bin/promote-memory`).

---

## 5. PR conventions

- **Branch off `main`.** Do not commit directly to `main`.
- **Keep core dependency-free.** No third-party imports in `scripts/`; the
  stdlib-only invariant is enforced in CI.
- **Run the §2 checks locally** before opening the PR — your PR should be green
  on the same gates CI runs.
- **Honor the de-SUT contract.** No SUT source/secrets/product facts; run
  `scripts/check_no_sut_terms.py`; use `# bugate: allow-sut-term` only for
  genuinely necessary occurrences.
- **When you add a config/profile flag**, document it in the canonical
  references so it does not drift: the command/capability index
  [`CAPABILITIES.md`](CAPABILITIES.md) and, for any profile-readable key, the
  profile contract
  [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md).
- **Keep docs pointing at canonical sources** instead of duplicating them (link
  to the profile schema, the ADR, and the promotion protocol rather than
  restating them).
