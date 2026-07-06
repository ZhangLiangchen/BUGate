# ADR-BUGATE-002 — Hosting-Direction Correction: Imported Mode Becomes Real

- **Status:** accepted (2026-07-03, human-ratified via CHARTER-BUGATE-001 and
  the signed K/W/C disposal review); amended by CHARTER A4 (2026-07-06) to
  retire the maintainer SUT-mount exception.
- **Authority:** [`CHARTER.md`](../../CHARTER.md) (CHARTER-BUGATE-001 §2, §5)
- **Companions:** ADR-BUGATE-001
  ([`BUGATE_PLATFORM_DECOUPLING_ADR.md`](BUGATE_PLATFORM_DECOUPLING_ADR.md)),
  [`TRANSITION_PROTOCOL.md`](TRANSITION_PROTOCOL.md), and — each owned by its
  own decision record, cross-referenced here only — ADR-BUGATE-003
  ([`BUGATE_MEMORY_BUS_SYSTEM_LEVEL_ADR.md`](BUGATE_MEMORY_BUS_SYSTEM_LEVEL_ADR.md),
  machine-level memory-bus hosting) and CHARTER Amendment A1 / ADR-BUGATE-004
  ([`BUGATE_DESUT_CALIBRATION_ADR.md`](BUGATE_DESUT_CALIBRATION_ADR.md), the
  de-SUT calibration).

## 1. Context — the error being corrected

Between the extraction (2026-06) and the charter (2026-07-03), this repository
drifted into building the **maintainer workbench as if it were the production
form**: BUGate opened as the project root, a SUT test workspace linked beneath
it by symlink + a local uncommitted profile pointer, and indirection layers
grown so that BUGate-as-root could reach out into external SUTs. The charter
ruled that hosting direction inverted (§2.4 anti-patterns): the runtime binds
trust, hooks, skills, and project context to the opened project root; the
governance contract must be reviewed and versioned with the code it governs;
gate CI must run where guarded changes happen; and per-SUT clones of the
framework are the fork-per-product variant ADR-BUGATE-001 already rejected.

The correction was executed audit-first: a full sweep of every surface assuming
"BUGate is the project root / the SUT is external", classified K/W/C and signed
off by the human owner before any change.

## 2. Decision 1 — K/W/C disposal (as signed)

- **K — mode-independent core assets, kept unchanged:** the profile mechanism
  (merge, guards, artifact templates, semantic-schema dialects, UC binding);
  the whole gate engine; ADR-BUGATE-001's four-part model and the transition
  protocol (orthogonal to hosting direction); the de-SUT guard + CI; templates,
  graceful degradation; the dual-runtime gate-agent adapters; the
  memory-bus mechanism. **Notably kept:** the profile `evidence_sources` /
  `skill_sources` keys and their resolver — audited as mode-independent (in
  imported mode the same binding resolves plain repo-relative paths); only
  their documentation was reworded.
- **W — formerly workbench-legitimate, now retired by CHARTER A4:** the symlink
  mount procedure and local uncommitted profile-pointer convention were kept on
  2026-07-03 only as a maintainer extraction bridge. They are no longer current:
  BUGate core iteration is pure, and real SUT validation happens in an external
  or scratch SUT repo that imports BUGate.
- **C — Mode-B overbuild/residue, corrected:** the root-discovery sentinel
  coupling (§3); host-inverted wording in the skill, agent protocol, and
  profile schema (rewritten to the governed-workspace framing, imported mode
  first); stale top-level release material (now removed rather than
  patched forward); and the shared memory-bus service-host residue (§5).

## 3. Decision 2 — the root-discovery contract (the S2 split)

One sentinel (`AGENTS.md` + `.shared/`) used to define *the* root, silently
equating "where the engine lives" with "where the governed work lives" — the
technical root of Mode-B. The two are now separate concepts with separate
resolution:

| Root | Meaning | Resolution order |
|---|---|---|
| **Workspace root** (`bugate_core.find_root`) | the governed repo: config, profile, artifacts, guarded paths, namespaces | `BUGATE_PROJECT_ROOT` env → nearest `bugate.config.yaml` walking up from CWD (the committed config **is** the workspace marker) → legacy `AGENTS.md`+`.shared/` sentinel only for pure-core self-check compatibility |
| **Engine root** (`bugate_core.find_engine_root`) | the kit's own tree: templates, sibling gate scripts, bin wrappers, the de-SUT scan anchor | `BUGATE_ENGINE_ROOT` env → the engine tree's own location (resolved from the module file, never from CWD) |

Hook contract: runtime hooks **locate the engine** (walk up for
`scripts/bugate_core.py`, or `${CLAUDE_PLUGIN_ROOT}` in the plugin channel, or
the known vendor dir written by the installer) and the gate scripts resolve the
workspace themselves from CWD. A governed repo therefore needs no engine
sentinel files. Both layouts are CI-enforced by ephemeral-fixture acceptance in
`tests/test_write_guard_layouts.py` plus a `bugate init` scratch-repo e2e with
the R4 negative control.

## 4. Decision 3 — distribution channels

Shipped now (CHARTER §5.2's distribution requirement):

1. **Installer, vendored-dir channel** — `scripts/bugate_init.py <sut-repo>`
   vendors the kit into `<sut-repo>/.bugate/`, links skill discovery, merges
   (never overwrites) the repo's own hook files, scaffolds a **committed**
   config + profile, and prints the acceptance checklist (Codex hook re-trust;
   R4 negative control). Idempotent, self-vendor-safe.
2. **Codex + Claude Code plugins** — `.codex-plugin/` and `.claude-plugin/`
   manifests identify the plugin; plugin-root `skills/`, `commands/`,
   `agents/`, `hooks/hooks.json`, `scripts/`, and `bin/` carry the reusable
   runtime surface. Hooks load through the plugin root variables and remain
   inert in any repo without a committed `bugate.config.yaml`.
3. **Project-local installer supplement** — even with plugins, `bugate init`
   remains the acceptance path for a SUT repo because it commits the profile,
   project hook wiring, CI-friendly vendored scripts, and Codex gate-agent
   cards that the SUT repo must review and version.

Deferred, direction accepted: the **pip/pipx console-script** (`bugate`) the
charter leans toward as the end-state — stdlib-only makes packaging near-free,
hooks would gain a layout-independent entry point, and upgrades become a
version bump. It is not shipped in this correction because the plugin +
vendored channels already satisfy the §5.2 acceptance and the console-script
deserves its own packaging/versioning pass. Trigger to revisit: the first
adoption where per-repo vendoring measurably drifts, or the first multi-repo
fleet upgrade.

## 5. Decision 4 — shared memory-bus service hosting (client side)

The memory-bus **service** is machine-level: one instance, one live database,
every governed repo a namespace-isolated **client**. Where the service lives
and how it is supervised is owned by ADR-BUGATE-003 (machine-level home
directory, legacy in-repo state readable as a deprecated fallback, optional
launch-supervision hardening). What this correction fixes is the
hosting-direction residue on the client side: a client repo's start wrapper
must **delegate to the shared host or refuse** — never lazily create an empty
local database. In generic imported use the same rule reads: vendored kits are
clients by default; self-hosting is an explicit operator decision.

## 6. Consequences

- Imported mode is now *practically* the default, not just narratively: a
  fresh SUT repo adopts BUGate by installer or plugin without cloning the core,
  and the R4 negative control is part of the acceptance path.
- BUGate core iteration remains fully supported for the maintainer activities in
  CHARTER §2.3, but it is pure-core only: no real SUT mount, symlink, nested
  checkout, copied workspace, or local profile pointer inside the engine repo.
- Engine upgrades inside a governed repo are a re-run of the installer (or a
  plugin update); the committed config/profile are untouched by refreshes.
- Documentation now states the split root contract in one voice (README,
  CAPABILITIES, INIT ×2, CONTRIBUTING, METHOD, SOP, promotion protocol, this
  file's companions).

## 7. Rejected alternatives

- **Keep workbench-as-production** — rejected by the charter (§2.4): trust,
  context, CI, and review all bind to the wrong root, and N-SUT collapses into
  per-SUT framework clones.
- **Delete core self-development support** — rejected as over-correction: core
  smoke, temporary fixture validation, and external/scratch imported-repo e2e
  remain necessary. CHARTER A4 deletes only the extraction-era SUT mount
  exception.
- **Delete the profile evidence/skill source keys** (suspected Mode-B reach) —
  rejected on audit: the binding is declarative and mode-independent; only its
  wording was host-inverted.
