# BUGATE-CORE-2026-07-23: Release acceptance rejected the recovery anchor

- Component: archive-native release acceptance gate
- Classification: BUGate Core test/release infrastructure defect; not a SUT defect
- Severity: P1 release blocker
- Status: fixed and source-verified for v0.4.3; clean-archive acceptance remains pending
- Scope: SUT-neutral temporary release fixtures only

## Confirmed behavior

The v0.4.3 imported smoke flow deliberately interrupts the first strict Memory
publication and resumes it through `recover`. The successful route contains the
six normal lifecycle events plus one state-preserving `evidence_recovery`
receipt:

1. `human_acceptance`;
2. `evidence_recovery`;
3. `designer_handoff`;
4. `implementer_acceptance`;
5. `implementer_handoff`;
6. `reviewer_acceptance`; and
7. `reviewer_completion`.

The vendored full-check exact-verified all seven receipts and reached `closed`,
but `tests/accept_release_assets.py` still required exactly six transition
Memory records and fabricated a six-item count for real-runtime evidence. The
first clean-candidate tar workflow was therefore correctly reported as
`NO-GO`; zip acceptance did not run. The archive runtime was not the cause.

## Root cause and impact

The release harness duplicated an earlier fixed lifecycle count instead of
binding its oracle to the recovery-augmented formal smoke route. Recovery added
one audit receipt without unlocking or changing lifecycle state, but the
acceptance count was not updated with the full-check contract. This was a
false-negative release-infrastructure defect: it could block a correct release
and, if repaired by changing only a number, could later accept a wrong
same-count route.

## Required closure

- Validate the exact ordered seven-event route, not only its cardinality.
- Bind every transition to one deterministic lineage ID, the exact expected
  sequence/revision `0..6`, its predecessor head, and a unique transition hash.
- Require the full-check evidence marker `receipt_count=7; exact_anchors=7`.
- Preserve the real-runtime privacy boundary: do not enumerate or copy records
  from the operator's Memory service; use the exact full-check PASS evidence.
- Run tar and zip archive-native `smoke + both` from a clean final commit before
  publication.

Historical v0.4.2 references to six lifecycle transitions remain correct and
must not be rewritten as v0.4.3 evidence.

## Source-level closure evidence

- A focused regression first failed because no exact seven-event validator
  existed, then passed after the validator was added.
- Negative controls reject a missing event, reordered events, revision drift,
  predecessor-head drift, and lineage-ID divergence.
- The updater acceptance now asserts the ordered event and phase route plus the
  shared lineage sequence/revision/head preconditions instead of a multiset.
- Final clean-archive, CI, tag, and public-download evidence is a separate
  release-operation gate and is not claimed by this source record.
