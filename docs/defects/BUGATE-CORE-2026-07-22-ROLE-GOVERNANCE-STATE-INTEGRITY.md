# BUGATE-CORE-2026-07-22: Role-governance state integrity gaps

- Component: Wave 7 role-governance policy and terminal evidence verification
- Classification: BUGate Core governance defect; not a SUT defect
- Severity: P1
- Status: code/test closure verified; clean-archive acceptance still blocks release
- Scope: SUT-neutral temporary fixtures only

## Confirmed behavior

Independent pre-release review found a family of false-green paths in the v0.4.2
candidate:

1. `reviewer_completion` recorded 04/05 and execution-evidence hashes, but
   `verify_evidence(post_run)`, `preflight(post_run)`, and `status_data()` did
   not revalidate that terminal snapshot. After changing
   `04_execution_report.md`, the chain still reported `ok: true`, `closed`, and
   allowed another post-run tool write.
2. The policy parser accepted non-canonical lifecycle ownership such as
   `reviewer` for implementation and `implementer` for post-run. The transition
   engine then emitted events that its own verification path could not consume,
   so a syntactically accepted required profile produced a contradictory state
   machine.
3. Acceptance verification did not require the latest handoff generation.
   A new designer handoff could leave an old reviewer acceptance apparently
   usable, and a same-byte completion in a later implementation generation
   could return an older closed receipt without advancing the chain.
4. Receipt profile snapshots bound only the selected profile file. Effective
   policy inherited from `bugate.config.yaml` could change from strict Memory
   to best-effort, or otherwise relax the merged policy, without invalidating
   the handoff. `accept` and `complete` also lacked complete `mode: off`
   publisher rejection.
5. Completion evidence accepted role-chain files and phase-owned/config paths,
   including files changed by receipt publication itself. Hook classification
   knew only canonical 04/05 names, so an arbitrary captured execution log,
   `..` spelling, symlink alias, or a path shared by multiple UCs could evade
   the terminal owner or select only the first owner.

All five behavior families violate the frozen Wave 7 contract: required mode must fail
closed on any drift or malformed phase policy, and reviewer completion evidence
must remain locally verifiable after closure.

## Required closure

- Freeze v0.4.x phase ownership to `pre_code=designer`,
  `implementation=implementer`, and `post_run=reviewer`.
- Require acceptance to reference the latest handoff generation.
- Bind every new receipt to the canonical effective merged config, retain
  structural parsing of legacy two-field snapshots, and require an append-only
  superseding generation before such a legacy snapshot can unlock.
- Reject every lifecycle publisher in off mode.
- Revalidate the successful completion receipt's profile, 04/05, and execution
  evidence from `verify`, `status`, and post-run preflight paths.
- Reject supported-tool post-run writes after `closed`; permit only an exact
  idempotent completion retry until a new lifecycle generation is established.
- Reject completion evidence that reuses config/profile, any role-evidence
  directory, or another phase-owned path. Canonicalize arbitrary captured paths
  and require post-run preflight from every UC owner of a shared path.
- Add direct state-machine, status, Claude payload, and Codex payload negative
  controls; re-run the complete suite and archive-native release acceptance.

No advisory downgrade, receipt deletion, real SUT fixture, or omission of the
execution-log drift control is an acceptable closure.

## Closure evidence

- Frozen role-governance state-machine suite: 24/24 PASS.
- Frozen Claude/Codex role-evidence hook suite: 16/16 PASS, including lexical
  versus resolved ownership, cross-UC aliases, duplicate ownership keys,
  inside/outside reverse-dangling targets, ordinary-link positive controls,
  and malformed-sibling isolation.
- Two independent frozen-byte reviews returned GO. Their additional SUT-neutral
  `/tmp` matrices passed 30/30 for managed-link path/target behavior and covered
  duplicate/escaped ownership plus `run.path`/`metadata.path` non-owners.
- Complete direct unittest discovery: 377/377 PASS; all 25
  `tests/test_*.py` entry points also PASS, including hook parity, imported
  full-check layouts, write-guard layouts, de-SUT, updater, and release
  contracts.
- Focused updater transaction suite: 93/93 PASS; compile and
  `git diff --check`: PASS.

The implementation and source-tree regression closure is complete. This
record remains a release blocker until the exact clean-built tar and zip also
pass archive-native imported full acceptance; that evidence must be appended
before changing the status to closed.
