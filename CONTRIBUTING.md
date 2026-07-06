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
| **SUT Profile** | the imported SUT test repo | paths, commands, evidence sources, guarded patterns, resource policy, runtime kind |
| **SUT Product Runtime** | the product-owned runtime/repository | source, API docs, fixtures, tests, secrets, live evidence |

Concretely (AGENTS.md Core Rules 1–2 and 5):

- Never add SUT source code, product API snapshots, environment secrets,
  credentials, generated caches, or project-specific fixtures to core.
- Put SUT paths, resource policies, environment names, auth rules, and tool
  commands in a **SUT profile**, not in core.
- If a change needs SUT-specific facts, stop at the profile boundary and add a
  profile key in the imported SUT test repo instead of inventing product details.

### The de-SUT guard (identity-seepage defense)

`scripts/check_no_sut_terms.py` defends the kit's **reusability**: the engine
subtree that gets vendored into a governed SUT repo must not carry facts true
for only one SUT — *block seepage, not mention* (CHARTER Amendment A1,
[ADR-BUGATE-004](docs/qa-methodology/BUGATE_DESUT_CALIBRATION_ADR.md)). The
discipline is three-layered:

1. **Behavioral SUT facts** (defaults, endpoints, resources, credentials,
   environment names) — never in core, no exemption. The guard's built-in
   general hygiene patterns (machine-local user paths, credential/key shapes)
   catch the machine-detectable slice; the rest is review discipline.
2. **Identity terms** (product/system/account/person names) — forbidden in the
   kit tree by default, but the term list is **profile-supplied**
   (`sut_identity_terms`) or given via `--terms-file`; the engine bakes in no
   product vocabulary. Narrative/provenance mentions are legitimate through
   explicit markers (below).
3. **Industry/domain vocabulary** — not defended by core; a SUT profile that
   wants a domain word defended lists it itself.

The scan surface anchors on the **engine root's kit subtree** (`scripts/`,
`bin/`, `.shared/skills/`; plus docs/ and root files when the engine root
is this upstream repo, detected by the `CHARTER.md` sentinel). A governed
workspace's own files are never the surface. The legacy SUT vocabulary used for
upstream regression lives only in `tests/fixtures/legacy-sut-terms.txt` — never
in engine source.

> Mounting a SUT locally while developing BUGate itself (maintainers)? Keep your
> `profile:` pointer in this repo's `bugate.config.yaml` uncommitted — it's a
> per-clone local edit — and append the inline marker to that one line so
> local fixture runs stay green. In imported mode (the default, CHARTER §2.2)
> the reverse holds: the governed SUT repo commits its own config + profile.

### Exemption channels (narrative mention only)

Explicit, per-site, auditable — there is no global switch and no
environment-variable bypass:

- **Inline marker** — append `# bugate: allow-sut-term` to the line; in
  Markdown use the comment form `<!-- bugate: allow-sut-term -->` so rendering
  stays clean. Waives both scans for that line.
- **File-level frontmatter** — `desut: provenance-allowed` on a *narrative*
  Markdown doc outside the kit subtree (engine/templates/schema files never
  qualify). Waives the identity scan; general hygiene still runs.
- **Allowlisted directory** — `docs/case-studies/` (real import/migration
  stories). Waives the identity scan; general hygiene still runs.

Every channel legitimizes narrative/provenance **mention** only. Using one to
carry a behavioral fact (an endpoint, a path the engine reads, a default) is a
violation — that verdict is owned by code review and the semantic gates, not
by the grep.

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

# 3. De-SUT guard: hygiene + legacy regression + meta-test (the meta-test also
#    covers the second-SUT profile-declared defense on fabricated fixtures)
python3 scripts/check_no_sut_terms.py
python3 scripts/check_no_sut_terms.py --terms-file tests/fixtures/legacy-sut-terms.txt
python3 tests/test_desut_guard.py

# 4. Write-guard dual-layout acceptance (ephemeral fixtures — the repo ships
#    no committed example SUT trees)
python3 tests/test_write_guard_layouts.py
```

CI also runs the `bugate init` scratch-repo e2e (R4 negative control), the
Wave 0 / Wave 8 graceful-degradation checks, an orchestrator init smoke, and a
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
| `tests/` | upstream-only ephemeral-fixture acceptances (dual-layout write guard, de-SUT meta-test) + fixtures (`fixtures/legacy-sut-terms.txt` is the regression term list); not part of the vendored kit |
| `docs/case-studies/` | narrative allowlist: real import/migration stories (identity-scan exempt, hygiene enforced) |
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
active project root via `bugate_core.find_root()` (nearest
`bugate.config.yaml` up from CWD, self-development sentinel fallback, no git
dependency) and engine assets (templates, sibling scripts) via
`bugate_core.find_engine_root()` — never by assuming a CWD or using git
metadata. Add it to the relevant CI step if it is a gate. Run it through the
de-SUT guard.

**Adding a template (`.shared/skills/bugate/templates/`):** keep every field and
example SUT-neutral. Templates are checked by the semantics gates above, so run
the four gates against the templates dir after editing.

**Adding an adapter:** adapters live under
`.shared/skills/bugate/adapters/` (ADR Implementation Notes). Keep them neutral;
SUT-specific wiring belongs in the imported SUT test repo, not the adapter.

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
- **Honor the de-SUT contract.** No behavioral SUT facts in core, ever;
  identity terms only under an explicit narrative exemption (§1); run the four
  §2 guard readings. Exemption markers legitimize *mention*, never facts.
- **When you add a config/profile flag**, document it in the canonical
  references so it does not drift: the command/capability index
  [`CAPABILITIES.md`](CAPABILITIES.md) and, for any profile-readable key, the
  profile contract
  [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md).
- **Keep docs pointing at canonical sources** instead of duplicating them (link
  to the profile schema, the ADR, and the promotion protocol rather than
  restating them).
