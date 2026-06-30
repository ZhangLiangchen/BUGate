# BUGate QA Methodology — Index

SUT-neutral navigation index for the methodology doc set. This directory holds
reusable method and operating guidance only; SUT-specific facts live in a
profile or mounted workspace. On any conflict,
`.shared/skills/bugate/SKILL.md` is the canonical source.

## Documents

| File | Purpose |
|---|---|
| [METHOD.md](METHOD.md) | The "why": full AI-assisted black-box test methodology, the nine-Wave flow, theory mapping, and rationale. |
| [SOP.md](SOP.md) | The "what to do next": step-by-step execution handbook for the Wave 0–3 minimum viable loop. |
| [EXPERIENCE_PROMOTION_PROTOCOL.md](EXPERIENCE_PROMOTION_PROTOCOL.md) | How a SUT-local lesson is decided to either stay local or be promoted into SUT-neutral BUGate Core. |
| [BUGATE_PLATFORM_DECOUPLING_ADR.md](BUGATE_PLATFORM_DECOUPLING_ADR.md) | ADR-BUGATE-001: the accepted BUGate Core / SUT Profile / Mounted Workspace / SUT / Product Runtime four-part architecture and its promotion rule. |
| [TRANSITION_PROTOCOL.md](TRANSITION_PROTOCOL.md) | PROTO-BUGATE-TRANS-001: the *journey* from an old embedded BUGate to the decoupled core — asymmetric strangler-fig, the 3-bucket capability-gap classifier, the transition-gap ledger, and retirement exit criteria. |
| [BUGATE_EVOLUTION_TIMELINE.md](BUGATE_EVOLUTION_TIMELINE.md) | SUT-neutral summary of how BUGate evolved from a method into a profile-driven pre-code governance framework. |

## Recommended reading order

1. `METHOD.md` — understand the method and its reasoning first.
2. `SOP.md` — then learn how to execute it day to day.
3. `BUGATE_PLATFORM_DECOUPLING_ADR.md` — the architecture that keeps Core reusable.
4. `EXPERIENCE_PROMOTION_PROTOCOL.md` — how learning compounds back into Core.
5. `TRANSITION_PROTOCOL.md` — how to migrate an old embedded BUGate to the decoupled core without losing capability.
6. `BUGATE_EVOLUTION_TIMELINE.md` — optional background on how it all came to be.

## Method summary (English)

`METHOD.md` and `SOP.md` are written in Chinese. This is a faithful English
digest of `METHOD.md` (§2–§3, §10) and the ADR — it is a summary, not a
replacement; the Chinese files remain canonical.

The method is a **dual-layer, nine-Wave flow**. **Wave 0** is the admission
gate: a 9-dimension PRD health check that decides whether the PRD is usable as a
business source-of-truth and emits a structured gap report. The first layer, the
**business-understanding audit (Waves 1–4)**, shifts the QA's job from judging
*correctness* to judging *evidence*: multiple AIs independently extract
propositions (Wave 1), the QA does citation traceback and routes the divergences
(Wave 2), a structured interview converts tacit dev knowledge into record (Wave
3), and optional behavioral-oracle replay calibrates the model against the live
system (Wave 4) — producing a high-confidence business model. The second layer,
**defect-discovery generation (Waves 5–8)**, forces the AI out of happy-path
thinking: structured test design over boundaries / illegal state transitions /
risk weighting (Wave 5), adversarial red-team augmentation (Wave 6), three-role
agent isolation so the implementer never reads business source (Wave 7), and a
mutation / oracle-falsification quality gate (Wave 8). **Wave 9** is the
change-driven regeneration mechanism: `source_hashes` detect staleness so only
the affected scope is re-run.

These Waves are *analysis-time middleware* (kept under a working dir such as
`.ai/`) that **converge onto the shipped 01–05 gate stack**: propositions and
oracles land in `01_business_brief.md`, the layer decision in `02_testability.md`,
the case inventory in `03_inventory.yaml`, and so on through
`04_execution_report.md` / `05_knowledge_update.md`. The gate stack — templates,
structural gates in `scripts/`, and the skill in `.shared/skills/bugate/` — is
the actual install contract; the Waves are how you fill it.

Architecturally, BUGate is a **four-part model** (per
`BUGATE_PLATFORM_DECOUPLING_ADR.md`): **BUGate Core** owns SUT-neutral method,
artifact templates, structural gate criteria, hook mechanism, and adapter
layout; the **SUT Profile** is the bridge contract for paths, commands, evidence
sources, guarded test patterns, resource policy, runtime kind, role policy, and
namespace; the **Mounted Workspace** is usually the SUT automation test
framework / test workspace, owning test code, BUGate artifacts, fixtures,
runners, generated cases, captured evidence, and local test rules; the
**SUT / Product Runtime** is product-owned, covering black-box API/UI/runtime
behavior, product docs/contracts/environments, and optional source/API
dump/secrets only as evidence sources. Core must never depend on any single
SUT's entities or paths; a lesson is promoted into Core only after it is
restated in product-neutral terms.

## Glossary (English)

**Two meanings of "layer" — disambiguated.** The word is overloaded; keep them
separate:

- **Architecture parts (four-part model):** **BUGate Core / SUT Profile /
  Mounted Workspace / SUT / Product Runtime** — the ownership/decoupling split
  from the ADR (see above). About *who owns what*.
- **Gate "Layers 1–4":** the pre-code artifact stages enforced by the gates.
  About *what artifact you produce next*. These are unrelated to the four-part
  model — a Layer-1 gate lives entirely inside Core.

**Gate Layers ↔ artifact numbering** (from `.shared/skills/bugate/SKILL.md`):

- **Layer 1** — `01_business_brief.md` (propositions, oracles, states,
  boundaries, gaps). Optional full-SDTD pre-Layer-3 models: `01a_domain_model.md`,
  `01b_state_flow.md`.
- **Layer 2** — `02_testability.md` (test-layer decision + evidence plan).
  Optional: `02a_test_dimension_matrix.yaml`.
- **Layer 3** — `03_inventory.yaml` (case + proposition + oracle coverage);
  `03a_test_cases.md` (human-readable cases); `03b_adversarial_cases.yaml`.
- **Layer 4** — implementation (SUT-profile-owned test code; the gate blocks
  this until the pre-code artifacts are accepted).
- Post-execution: `04_execution_report.md`, `05_knowledge_update.md`.

**The nine Waves, one line each:**

- **Wave 0** — PRD health check (9-dimension admission gate + gap report).
- **Wave 1** — multiple AIs independently extract propositions (multiview).
- **Wave 2** — QA citation-traceback audit + divergence routing.
- **Wave 3** — structured interview turns dev tacit knowledge into record.
- **Wave 4** — behavioral-oracle replay against the live system (optional).
- **Wave 5** — structured test design (boundaries, illegal transitions, risk).
- **Wave 6** — adversarial red-team scenario augmentation.
- **Wave 7** — three-role agent isolation (builder / designer / implementer).
- **Wave 8** — mutation / oracle-falsification quality gate.
- **Wave 9** — change-driven regeneration (re-run only the stale scope).

**Key terms:**

- **Proposition (`P-xxx`)** — a single-sentence business rule extracted from the
  PRD, carrying a verifiable citation (source + quote).
- **Oracle (`O-xxx`)** — an observable expected-truth statement used to decide
  pass/fail for a proposition.
- **Evidence label (`fact` / `inferred` / `unknown`)** — every proposition and
  oracle is classified by evidence strength: `fact` (directly supported),
  `inferred` (reasonable but not yet strict-assertion-grade), `unknown` (not yet
  evidenced). Never upgrade `inferred` / `unknown` to `fact` to pass a gate.
- **Falsification kill / survive** — in Wave 8, a (evidence, mutation) case is
  **killed** when at least one oracle fails on the mutated evidence, and
  **survives** when every oracle still passes (a surviving mutation flags a blind
  spot); score = killed / (killed + survived).
- **Divergence** — where the independent Wave-1 AIs disagree on a proposition;
  recorded in the Wave-1 `00_multiview/divergence_report.md` and routed by Wave 2
  to the interview pool, since disagreement marks where uncertainty lives.

See `.shared/skills/bugate/references/profile-schema.md` and the per-layer gate
references for the authoritative definitions.
