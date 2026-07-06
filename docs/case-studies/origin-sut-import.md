---
type: case-study
id: CASE-BUGATE-ORIGIN-001
title: The origin SUT — from embedded gate stack to imported governance layer
status: recorded
created_at: 2026-07-03
---

# Case study: importing BUGate back into the SUT it was born in

> This directory (`docs/case-studies/`) is the charter's narrative allowlist
> (CHARTER Amendment A1): real product names may appear here as **provenance**,
> because the de-SUT guard blocks *seepage into the reusable kit*, not *mention
> in the story*. General hygiene (credentials, machine-local paths) still
> applies. Nothing in this document is read by the engine.

## 1. Where BUGate comes from

BUGate was not designed in the abstract. It grew up **embedded** in the
automation test workspace of **hypervise**, a production multi-chain wallet
platform: one repo carrying the product's scenario tests *and* an in-repo gate
stack — layered pre-code artifacts (business brief → testability → inventory),
a physical write guard hooked into the agent runtime, role isolation, a
memory bus, and weekly oracle-falsification runs. "Prove your business
understanding before you write test code" was enforced against a live SUT
long before it had a neutral name.

That embedding was also the problem. The gate criteria, the artifact
templates, and the hook wiring all referenced one product's paths, entities,
and vocabulary. A second SUT could not adopt any of it without forking — and
the fork-per-product failure (rules drift, learning does not compound) is
exactly what ADR-BUGATE-001 later rejected in writing.

## 2. The extraction (2026-06)

The split was chartered in three moves, each with a decision record:

1. **The seam list (2026-06-11).** A survey of the embedded stack drew the
   target shape: an importable kit plus a single per-project configuration in
   the SUT repo — what the charter now calls imported mode.
2. **The four-part model (2026-06-16, ADR-BUGATE-001).** Core / SUT profile /
   imported SUT test repo / SUT runtime, with a one-way Promotion Rule: a lesson
   enters core only if it can be stated without referencing one SUT's
   entities, paths, environments, credentials, or fixtures.
3. **De-SUT-ing the core (from 2026-06-17).** Neutral skill tree, profile
   mechanism, artifact templates, ephemeral-fixture acceptances, and a
   forbidden-term CI guard. In that era the guard was a **total blockade**:
   a hard-coded list of legacy-SUT vocabulary (product name, internal system names, API
   identifiers, even industry words like chain names) that could appear
   nowhere in core — not even in this sentence's form. The blockade was the
   right tool while the embedded stack and the core shared one working tree;
   it was later recalibrated (§5).

## 3. The transition (2026-06-29 → 06-30)

TRANSITION_PROTOCOL (PROTO-BUGATE-TRANS-001) fixed the migration posture:
**asymmetric strangler-fig**. The embedded stack was frozen as a read-only
reference and fallback executor (a `frozen-reference` tag, a top-note, no
further behavioral development); the neutral core became the only actively
developed surface. Every capability gap was sorted into exactly three buckets
— (a) neutral capability → core, (b) SUT contract/data/skill → profile or
workspace, (c) mixed constraint → split — with the de-SUT guard itself as the
mechanized classifier.

Concrete moves out of the embedded stack:

- the embedded repo's 34KB agent protocol was **partitioned**: five neutral
  governance sections landed in core's `AGENTS.md`; 226 lines of SUT-specific
  operating rules moved to the workspace's own `docs/sut-operating-rules.md`,
  referenced from the profile — nothing rule-bearing stayed old-only;
- endpoint/contract knowledge was wired as profile keys (`evidence_sources`,
  `skill_sources`) instead of paths hard-coded anywhere in core;
- the Wave-8 falsification engine was backfilled into core as a declarative,
  spec-driven neutral capability (the origin run: 85.7% mutation kill rate);
- differences that were **correct handoffs** — e.g. a Layer-3 hardening loop
  converging on "this needs Layer 2 data / Layer 4 implementation" after four
  rounds — were recorded as *resolved*, deliberately **not** backfilled.

## 4. The import (2026-07-03, the full circle)

CHARTER-BUGATE-001 then flipped the host direction, and CHARTER A4 later retired
the extraction-era SUT-mount bridge entirely: the default and only usage mode is
**imported** — the agent opens the *SUT test repo* as project root and BUGate is
vendored in as its governance layer. Opening BUGate core is now pure engine
iteration only. The embedded era's exit state follows: the origin repo re-adopts
its own extracted kit **in imported mode**, replacing the frozen embedded stack.

What the origin SUT's committed configuration looks like under imported mode
(sanitized excerpt of the real profile; resource policies, environments, and
credentials stay in the SUT repo where they belong):

```yaml
# bugate.profile.yaml — committed in the SUT test repo
artifact_dir_template: docs/usecases/{uc}/

# Per-UC fail-closed binding: the (?P<uc>...) capture maps each guarded test
# file to its OWN use-case artifact dir.
guarded_path_regex:
  - "(^|/)python/tests/scenario_driven/[^/]+/test_(?P<uc>uc_[a-z]+_[0-9]+_[a-z0-9_]+)[.]py$"

# The v1.2 trio is the physical gate for this corpus's mixed-era artifacts.
required_precode_artifacts:
  - 01_business_brief.md
  - 02_testability.md
  - 03_inventory.yaml

# Historical artifacts were authored to the pre-canonical dialect; the
# schema-driven semantic gate validates the same universal contract through
# dialect section names (28/32 legacy use cases pass unmodified).
semantic_schema: original-gate

# This SUT's identity terms — the guard keeps them out of the vendored kit
# subtree; the workspace's own files may use them freely.
sut_identity_terms:
  - "\bhypervise\b"

agent_roles:
  implementer:
    - "(^|/)docs/raw/.*$"        # source/API mirror: implementer must not read
    - "(^|/)docs/usecases/.*$"   # pre-code artifacts: implementer must not rewrite
```

Two mechanisms in that excerpt were themselves *lessons the origin SUT taught
core*, promoted through the three-bucket classifier:

- **`semantic_schema` dialects.** The origin corpus predates the canonical
  v1.3 artifact schema. Instead of rewriting 30+ historical artifacts or
  forking the gate, core's semantic checkers became schema-driven: the same
  universal contract (a brief establishes propositions and oracles; a
  testability note declares strategy and evidence; an inventory lists
  intent-bearing cases), validated through per-dialect section names.
- **`sut_identity_terms`.** The de-SUT term list moved out of engine source
  into the profile (§5) — the origin SUT's vocabulary survives upstream only
  as a CI regression fixture (`tests/fixtures/legacy-sut-terms.txt`).

## 5. Recalibrating the guard: block seepage, not mention (CHARTER A1)

With the host direction flipped, the total blockade stopped matching the
threat model. In imported mode the thing to protect is the **kit's
reusability**: the core subtree vendored into a SUT repo must not carry facts
that are true for only one SUT — but a governance framework that cannot *name*
its own origin in a README, a tutorial, or this case study is defending
against the wrong thing.

Amendment A1 restated the intent as three layers: behavioral SUT facts
(defaults, endpoints, resources, credentials, environment names) stay banned
from core unconditionally; identity terms are banned by default with explicit,
per-site narrative exemptions (inline marker, provenance frontmatter, this
directory); industry vocabulary left the core list entirely — a chain name or
an API-doc tool name is defended only by a SUT profile that lists it itself.
The scan surface anchored on the engine's kit subtree, and a governed
workspace's own files left the surface: a SUT names itself freely on its own
territory.

This document is the first exercise of that dividend: the origin story,
told with its real name, in the repo that used to forbid the word.

## 6. What a new adopter should take from this

1. **Vendor the kit, commit the contract.** Config + profile live in the SUT
   repo, reviewed with the tests they guard (README Quickstart A; the layout
   is exercised on ephemeral fixtures by `tests/test_write_guard_layouts.py`
   and the CI `bugate init` e2e).
2. **Let the profile absorb your specificity.** Paths, dialects, identity
   terms, role fences — everything one-SUT-shaped has a profile key; the
   engine stays reusable.
3. **Freeze, classify, migrate, and know when not to.** If you are extracting
   your own embedded stack, the strangler-fig + three-bucket discipline in
   TRANSITION_PROTOCOL is the whole journey — including recording correct
   handoffs instead of backfilling them.
4. **Mention is not seepage.** Write your origin honestly; mark it. The guard
   exists so the *next* SUT inherits a clean kit, not so the story goes
   untold.
