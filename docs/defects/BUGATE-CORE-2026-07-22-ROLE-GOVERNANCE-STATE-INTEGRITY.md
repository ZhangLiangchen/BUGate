# BUGATE-CORE-2026-07-22: Role-governance state integrity gaps

- Component: Wave 7 role-governance policy and terminal evidence verification
- Classification: BUGate Core governance defect; not a SUT defect
- Severity: P1
- Status: fixed and closed on 2026-07-22; publication gates remain independent
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
- Clean candidate `8f7b6edad8d88b92502220809e932edd3882a8ff` (tree
  `e8f29c7e4906e78daadc4bfceae1d13947c23b86`) produced exactly the tar, zip,
  and checksum assets. The archive-native `smoke + both` report returned
  `decision: GO` for both workflows.
- Tar SHA-256:
  `00f7f5a941090c6e88836e10fd9806a5988989a0e16049760ec74fec3a25b8a4`;
  zip SHA-256:
  `dee3f7a72c2acd23bb25860a076ee4798a9725092977ffbfcbbc44a2b48badb8`;
  release-manifest digest:
  `0e879537c1437ebca70deff9e1d52693d09e3a9b0e471d955ff5e92e7bd1e570`.
- Each archive independently completed v0.3.2 bootstrap plan/apply, installed
  verify, same-version idempotence, rollback, legacy-preimage verification,
  reapply, final verify, the imported `smoke` full-check, and six strict Memory
  transitions. SUT-owned fixtures, hooks, profile, role evidence, Memory
  namespace, unrelated dirty state, and `.gitignore` content outside the
  BUGate marker remained unchanged.
- Complete discovery after adding the provider-neutral release-gate regression:
  378/378 PASS. Two newly spawned same-provider reviewer sessions independently
  confirmed the updater/archive evidence and rejected use of stale or dirty-tree
  assets; neither placeholder output nor the failed optional heterogeneous
  runtime diagnostic was relabeled as GO.

The implementation, source-tree regression, and clean-archive behavior closure
are complete, so this defect is closed. Publication remains separately NO-GO
until the final merged-main bytes pass the same archive-native `smoke + both`
gate, main and annotated-tag CI pass, and the three public release assets are
downloaded, checksum-verified, and reaccepted. A documentation-only candidate
change therefore requires a fresh final archive build, but does not reopen this
fixed state-integrity defect unless its governed behavior changes.
