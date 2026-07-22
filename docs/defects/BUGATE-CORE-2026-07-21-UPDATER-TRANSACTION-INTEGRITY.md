# BUGATE-CORE-2026-07-21: Imported updater transaction integrity gaps

- Component: imported-mode updater transaction runtime and acceptance oracle
- Classification: BUGate Core infrastructure defect; not a SUT defect
- Severity: P1
- Status: fixed and independently re-reviewed on 2026-07-22
- Scope: SUT-neutral synthetic repositories only

## Confirmed behavior

Independent adversarial review found several transaction-state windows that
the original green suite did not cover:

1. A durable `prepared` transaction published immediately before
   `current.json` could be treated as idle.
2. A same-named transaction directory could replace a validated child after
   its descriptor was closed.
3. Root-state terminal reports did not enforce their schema and complete
   journal-aligned presence contract.
4. Later mutating journal/report publication windows did not keep the
   transaction, transaction-store, updater-state, and pending-report bindings
   intact through the write and target-mutation boundary.
5. The zero-write test snapshot originally ignored persistent metadata, so a
   metadata-only mutation could produce a false green.
6. The descriptor-safe history cap was checked only after state already
   exceeded 128 entries. A valid 128-entry store could therefore accept and
   commit transaction 129, after which every recovery/status validation failed
   closed on the updater's own newly-created history.
7. The first capacity fix guarded both prepare implementations, but the public
   archive-rollback and archived-bootstrap-reuse paths published persistent
   transition intent before reaching that guard. A full history was rejected,
   yet the rejected command had already changed transaction-state metadata.

The fourth item can separate a managed target mutation from its canonical
journal/rollback state. It is therefore a release blocker even when a later
verification detects the damaged state.

## Required closure

- Detect and recover the unique publish-before-current prepared transaction.
- Keep descriptor/inode bindings live across validation and mutating writes.
- Restore target pre-images through pinned backups whenever a binding drifts.
- Bind and revalidate the pending success report across journal commit and
  final publication.
- Enforce `STATE_SCHEMA` and journal identity for current pointers and every
  terminal report.
- Reserve transaction-history capacity before direct and atomic/private
  prepare paths and before any archive/reuse transition intent; at the exact
  cap, every public apply/rollback/bootstrap path rejects before persistent
  target-repository writes and keeps the existing store valid and recoverable.
- Prove no-op and rejected paths preserve content, type, mode, inode, size,
  and modification/change metadata.
- Re-run independent race probes and the complete direct test suite before
  changing the v0.4.2 release decision from NO-GO.

No broad force option, conflict deletion, SUT fixture, or weakened recovery
gate is an acceptable closure.

## Closure evidence

- Transaction source SHA-256:
  `6bc94a4c24a60d0492dd9cd8f88aae12cc799e4b536e89c8f6190a764fce6179`.
- Transaction-test SHA-256:
  `9d4732282913e3773f102dafff5ca1f1ed4a5a132308767b20f559fb608b62b6`.
- Focused transaction suite: 93/93 PASS.
- Complete direct unittest discovery: 332/332 PASS.
- Compileall, all four template semantics gates, both de-SUT readings, the
  de-SUT meta-test, full-check layout acceptance, and `git diff --check`: PASS.
- Independent exact-128 probes confirmed zero byte/metadata change, no archive
  or reuse intent, no private staging, and recovery `GO` for normal apply,
  atomic prepare, archiving rollback, and archived bootstrap reuse rejection.
- An interrupted transaction that brings history to exactly 128 remains
  recoverable; recovery restores the pre-image and leaves a valid capped
  history.

The 128-entry limit remains an intentional fail-closed operational bound. The
updater does not prune committed rollback history automatically; operators must
not claim unlimited retained transaction history.
