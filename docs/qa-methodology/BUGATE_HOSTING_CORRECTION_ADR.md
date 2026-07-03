# ADR-BUGATE-002 — Hosting-Direction Correction: Imported Mode Becomes Real

- **Status:** accepted (2026-07-03, human-ratified via CHARTER-BUGATE-001 and
  the signed K/W/C disposal review)
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
form**: BUGate opened as the project root, a SUT test workspace mounted beneath
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
  demos, graceful degradation; the dual-runtime gate-agent adapters; the
  memory-bus mechanism. **Notably kept:** the profile `evidence_sources` /
  `skill_sources` keys and their resolver — audited as mode-independent (in
  imported mode the same binding resolves plain repo-relative paths); only
  their documentation was reworded.
- **W — workbench-legitimate, kept but scoped to the maintainer context:** the
  symlink mount procedure, the local *uncommitted* profile-pointer convention
  (now explicitly labeled a workbench convention wherever it appears), the
  mounted-demo (relabeled maintainer demo), and local mount state, which never
  enters the committed tree.
- **C — Mode-B overbuild/residue, corrected:** the root-discovery sentinel
  coupling (§3); mounted-first wording in the skill, agent protocol, and
  profile schema (rewritten to the governed-workspace framing, imported mode
  first); the whitepaper/slides mounted-as-endstate narrative (dated errata
  pointing at the charter, no rewrite); and the shared memory-bus service-host
  residue (§5).

## 3. Decision 2 — the root-discovery contract (the S2 split)

One sentinel (`AGENTS.md` + `.shared/`) used to define *the* root, silently
equating "where the engine lives" with "where the governed work lives" — the
technical root of Mode-B. The two are now separate concepts with separate
resolution:

| Root | Meaning | Resolution order |
|---|---|---|
| **Workspace root** (`bugate_core.find_root`) | the governed repo: config, profile, artifacts, guarded paths, namespaces | `BUGATE_PROJECT_ROOT` env → nearest `bugate.config.yaml` walking up from CWD (the committed config **is** the workspace marker) → legacy `AGENTS.md`+`.shared/` sentinel as the workbench fallback |
| **Engine root** (`bugate_core.find_engine_root`) | the kit's own tree: templates, sibling gate scripts, bin wrappers, the de-SUT scan anchor | `BUGATE_ENGINE_ROOT` env → the engine tree's own location (resolved from the module file, never from CWD) |

Hook contract: runtime hooks **locate the engine** (walk up for
`scripts/bugate_core.py`, or `${CLAUDE_PLUGIN_ROOT}` in the plugin channel, or
the known vendor dir written by the installer) and the gate scripts resolve the
workspace themselves from CWD. A governed repo therefore needs no engine
sentinel files. Both layouts are CI-enforced by the dual-layout write-guard
acceptance (`examples/imported-demo/` as the first-class imported layout,
`examples/mounted-demo/` as the workbench layout).

> *2026-07-04 update:* the committed example trees were removed for
> imported-mode purity (the upstream repo carries no SUT-shaped directories at
> all). The same dual-layout acceptance now runs on ephemeral fixtures —
> `tests/test_write_guard_layouts.py` — plus a `bugate init` scratch-repo e2e
> with the R4 negative control in CI. The decisions above are unchanged.

## 4. Decision 3 — distribution channels

Shipped now (CHARTER §5.2's two-channel requirement):

1. **Installer, vendored-dir channel** — `scripts/bugate_init.py <sut-repo>`
   vendors the kit into `<sut-repo>/.bugate/`, links skill discovery, merges
   (never overwrites) the repo's own hook files, scaffolds a **committed**
   config + profile, and prints the acceptance checklist (Codex hook re-trust;
   R4 negative control). Idempotent, self-vendor-safe.
2. **Claude Code plugin** — `.claude-plugin/` manifest with skills, commands,
   gate agents, and hooks loading via `${CLAUDE_PLUGIN_ROOT}`; hooks are inert
   in any repo without a committed `bugate.config.yaml`. The other runtime has
   no plugin system; the installer covers it.

Deferred, direction accepted: the **pip/pipx console-script** (`bugate`) the
charter leans toward as the end-state — stdlib-only makes packaging near-free,
hooks would gain a layout-independent entry point, and upgrades become a
version bump. It is not shipped in this correction because the vendored channel
already satisfies the §5.2 acceptance and the console-script deserves its own
packaging/versioning pass. Trigger to revisit: the first adoption where
per-repo vendoring measurably drifts, or the first multi-repo fleet upgrade.

## 5. Decision 4 — shared memory-bus service hosting (client side)

The memory-bus **service** is machine-level: one instance, one live database,
every governed repo a namespace-isolated **client**. Where the service lives
and how it is supervised is owned by ADR-BUGATE-003 (machine-level home
directory, legacy in-repo state readable as a deprecated fallback, optional
launch-supervision hardening). What this correction fixes is the
hosting-direction residue on the client side: a client repo's start wrapper
must **delegate to the shared host or refuse** — never lazily create an empty
local database. The origin workspace's starter was fixed accordingly (its
ensure-fallback used to boot an empty duplicate DB on the shared port after a
reboot: a split brain). In generic imported use the same rule reads: vendored
kits are clients by default; self-hosting is an explicit operator decision.

## 6. Consequences

- Imported mode is now *practically* the default, not just narratively: a
  fresh SUT repo adopts BUGate by installer or plugin without cloning the core,
  and the R4 negative control is part of the acceptance path.
- The workbench remains fully supported for the four maintainer activities
  (CHARTER §2.3) — nothing was de-toothed; the K red-line list survived the
  sweep intact.
- Engine upgrades inside a governed repo are a re-run of the installer (or a
  plugin update); the committed config/profile are untouched by refreshes.
- Documentation now states the split root contract in one voice (README,
  CAPABILITIES, INIT ×2, CONTRIBUTING, METHOD, SOP, promotion protocol, this
  file's companions).

## 7. Rejected alternatives

- **Keep workbench-as-production** — rejected by the charter (§2.4): trust,
  context, CI, and review all bind to the wrong root, and N-SUT collapses into
  per-SUT framework clones.
- **Delete the workbench** — rejected as over-correction: it is the maintainer
  form (four legitimate activity classes) and the extraction-era mount tooling
  remains correct there.
- **Delete the profile evidence/skill source keys** (suspected Mode-B reach) —
  rejected on audit: the binding is declarative and mode-independent; only its
  wording was mounted-first.
