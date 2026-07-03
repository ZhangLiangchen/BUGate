---
type: ADR
id: ADR-BUGATE-004
title: de-SUT calibration — from total blockade to identity-seepage defense
status: accepted
created_at: 2026-07-03
authority: CHARTER-BUGATE-001 Amendment A1 (human-approved 2026-07-03)
companions:
  - BUGATE_PLATFORM_DECOUPLING_ADR.md
  - TRANSITION_PROTOCOL.md
---

# ADR-BUGATE-004: de-SUT Calibration — Block Seepage, Not Mention

## Context

The de-SUT guard (`scripts/check_no_sut_terms.py`) was born in the extraction
era (ADR-BUGATE-001, 2026-06): the embedded gate stack and the neutral core
shared one working tree, and the guard's job was **total blockade** — a
hard-coded FORBIDDEN list of the origin SUT's vocabulary (product name,
internal system names, product API identifiers, person/account handles, and
industry words such as chain names, an API-doc tool name, cryptography terms,
a domain word in Chinese) that could appear nowhere in the core tree.

CHARTER-BUGATE-001 (2026-07-03) flipped the host direction: the default usage
mode is **imported** — the engine kit is vendored into a governed SUT repo and
later upgraded there. That flip changed the threat model, and the blockade
stopped matching it:

- **The protected asset is the kit's reusability**, not the repo's vocabulary.
  What must never happen is a fact that is true for only one SUT riding the
  vendored core subtree into the *next* SUT.
- **The blockade taxed the documentation dividend.** The origin story, real
  import tutorials, and migration case studies — the most persuasive material
  the project owns — could not be written, because the guard could not tell a
  narrative mention from an engine default.
- **Industry vocabulary was collateral damage.** Generic method text could not
  mention a public chain, a documentation tool, or a domain word that happened
  to sit on the origin SUT's list, though those words identify no SUT.
- **The list was baked into engine source**, so every new SUT inherited the
  origin SUT's vocabulary as its own forbidden list — itself a form of the
  coupling the guard exists to prevent.

## Decision

**Legislative intent, restated:** the de-SUT line protects the kit's
reusability. The core subtree that is vendored into (and upgraded inside) a
governed SUT repo must not carry behavioral facts that hold for only one SUT.
One line: **block seepage, not mention.**

### 1. Three-layer discriminator (one ruler for guard and writer)

1. **Behavioral SUT facts** — defaults, endpoints, resources, credentials,
   environment names: anything that would steer the engine or be inherited by
   the next SUT. **Never in core, no exemption.** Identical to the
   ADR-BUGATE-001 Promotion Rule; not an inch is yielded.
2. **Identity terms** — SUT/product/internal-system/person/account names.
   **Forbidden by default**; narrative/provenance contexts (README origin
   section, case studies, migration history, real import tutorials) are
   legitimate through **explicit, per-site markers**.
3. **Industry/domain vocabulary** — chain names, API-doc tool names,
   cryptography terms, trade words. **Removed from any core list**; defended
   only by a SUT profile that lists the word itself.

### 2. Term list is profile-supplied

The engine bakes in **no product vocabulary**. A SUT declares its own identity
terms in its profile (`sut_identity_terms`, schema-documented); the guard also
accepts `--terms-file` lists. Core retains only SUT-agnostic **general
hygiene** patterns (machine-local user paths, credential/key shapes), which no
directory or file-level exemption can lift.

### 3. Scan surface anchors on the engine root's kit subtree

The guard scans what gets reused: the fixed kit layout (`scripts/`, `bin/`,
`.shared/skills/`) in every layout, plus upstream-only assets (docs, examples,
root docs, CI, config) only when the engine root carries the upstream sentinel
(`CHARTER.md`, which never ships in the vendored kit). A governed workspace's
own files are **never** the scan surface — its subtree is excluded when nested
under the engine root, and in a vendored layout its files are simply not kit
members. Files that legitimately declare the terms (the active profile/config,
term-list fixtures) are excluded likewise.

### 4. Exemption channels — explicit, per-site, auditable

- inline `bugate: allow-sut-term` (HTML-comment form
  `<!-- bugate: allow-sut-term -->` keeps rendered Markdown clean) — waives
  both scans for that line;
- file-level frontmatter `desut: provenance-allowed` — narrative Markdown
  outside the kit subtree only; engine/templates/schema never qualify; hygiene
  still runs;
- allowlisted directory `docs/case-studies/` — real import/migration stories;
  hygiene still runs.

The marker legitimizes narrative/provenance **mention** only: using any
exemption to carry a behavioral fact is a violation, and that verdict belongs
to code review and the semantic-gate context, not to a grep. No global switch
and no environment-variable bypass exist.

### 5. Upstream regression fixtures

The old FORBIDDEN list migrated to `tests/fixtures/legacy-sut-terms.txt`
(identity terms active; industry words retired-but-archived per layer 3).
Upstream CI runs four readings: built-in hygiene; the legacy fixture (the
origin SUT's identity can never seep back); a second-SUT defense against the
`examples/imported-demo` profile; and a meta-test
(`tests/test_desut_guard.py`) proving every red/green verdict with negative
controls. The origin SUT's vocabulary now exists only in the fixture and in
that SUT's own profile — never in engine source.

## Consequences

- The origin story is writable: README provenance section, INIT tutorial
  pointer, and `docs/case-studies/origin-sut-import.md` name the origin SUT
  under explicit markers, and CI stays green — the documentation dividend this
  calibration was for.
- A new SUT's identity defense works day one: declare `sut_identity_terms`,
  and seepage into the kit subtree turns CI red while the SUT's own files stay
  free territory.
- Method texts may use industry vocabulary freely unless a profile defends a
  word.
- The TRANSITION_PROTOCOL three-bucket classifier is **unchanged**: candidate
  Core text must still be statable neutrally (run with the legacy fixture plus
  the active profile's terms); an allow marker on substantive content still
  means mis-bucketed (b)/(c). The Experience Promotion Rule is unchanged.
- The guard's verdicts are now layout-sensitive (engine root vs workspace
  root), covered by the meta-test's negative controls.

## Rejected alternatives

- **Keep the total blockade** — defends the wrong asset after the host flip;
  taxes documentation; bakes one SUT's vocabulary into every adopter's engine.
- **A global off-switch or env-var bypass for narrative work** — violates the
  charter's exemption red line (explicit, per-site, auditable); one flag would
  eventually launder behavioral facts.
- **Relax layer 1 (behavioral facts) alongside layer 2** — reusability is the
  whole point; the Promotion Rule stays absolute.
- **Per-SUT forks of the guard** — the ADR-BUGATE-001 rejected alternative in
  new clothing; rules drift, learning does not compound.

## Provenance

Direction ratified by the human owner on 2026-07-03; charter text landed as
CHARTER-BUGATE-001 Amendment A1 **before** the guard was re-implemented
(legislation precedes enforcement). Mechanism, fixtures, CI regression, and
the first case study landed together as one calibration.
