# BUGATE-CORE-2026-07-22: Rollback operator path loses the vendored launcher

- Component: imported-mode updater operator documentation and release contract
- Classification: BUGate Core documentation/operability defect; not a SUT defect
- Severity: P1 release blocker
- Status: fixed and verified on 2026-07-22
- Scope: SUT-neutral updater routes and synthetic acceptance only

## Confirmed behavior

The first v0.4.2 update of a supported v0.3.x or pre-lock v0.4.0/v0.4.1
installation adds the installed lock and `.bugate/bin/bugate-update` as managed
items. A transaction-specific rollback restores the exact recorded pre-image.
That correct restoration therefore removes both the lock and launcher.

The archive acceptance harness already exercises the safe path: after rollback
it invokes the retained release bootstrap as
`python3 <bootstrap>/scripts/bugate_update.py verify . --vendor-dir .bugate`
and confirms the restored legacy kind/version. The original operator documents
instead prescribed an unconditional vendored `verify` immediately after
rollback. That command cannot exist for the most important first-bootstrap
rollback path.

The same assumption made “v0.4+” a version-based routing shortcut even though
v0.4.0 and v0.4.1 are pre-lock installations. An interrupted rollback can also
change or remove the launcher before the operator performs read-only diagnosis.

## Impact and root cause

The updater transaction and rollback image are correct. The defect was a
documentation model that treated updater installation as permanent rather than
part of the reversible managed projection. Operators following it could see a
shell `not found` error after a successful rollback, misclassify that success
as corruption, or improvise by copying a launcher or editing journal state.
Those actions undermine the updater's fail-closed recovery boundary.

Because v0.4.2 introduces the first-class updater, an unusable documented
rollback verification path blocks release even when integration tests are
green.

## Required closure

- Route to the vendored updater only when the authoritative installed lock and
  executable launcher both exist. A remembered/written version is not routing
  evidence.
- Route exact v0.3.x and pre-lock v0.4.0/v0.4.1 installations through the
  updater in a verified unpacked v0.4.2-or-later release.
- Require the operator to retain that unpacked release outside the imported
  repo through the rollback window.
- After rollback, use vendored `verify` only if lock+launcher remain. Otherwise
  run `python3 "$BOOTSTRAP" verify . --vendor-dir .bugate`.
- If rollback is interrupted after the launcher changes, use the same external
  bootstrap for read-only `status`/`verify` and any exact reviewed rollback
  retry. Never reconstruct the launcher or hand-edit transaction state.
- Keep English/Chinese primary docs, prompts, vendored runbooks, normative
  contract, capability index, and v0.4.2 release notes synchronized.
- Add a SUT-neutral repository-contract test that proves the actual CLI accepts
  the external verify invocation and prevents version-only or unconditional
  post-rollback launcher guidance from returning.

No updater-engine change, broad force option, launcher reconstruction, journal
deletion, real imported SUT fixture, or weakened rollback gate is an acceptable
closure.

## Closure evidence

- Dedicated operator-doc contract: 5/5 PASS. It imports the actual updater
  parser, checks the executable `verify --help` surface, parses external
  status/verify/rollback forms, pins lock+launcher classification, requires the
  external fallback across primary bilingual runbooks, and rejects an
  unconditional vendored verify immediately after rollback.
- Repository release contract plus current/legacy release-manifest suites and
  the dedicated doc contract: 31/31 PASS.
- Synthetic v0.3.2 bootstrap/apply/idempotence/rollback integration: 1/1 PASS;
  its rollback removes the installed lock, restores the exact legacy
  projection, and verifies that image through the external updater.
- Markdown fences and relative links: PASS across all 29 changed/untracked
  Markdown files in the final dirty candidate snapshot.
- Both general and legacy-term de-SUT scans, plus `git diff --check`: PASS.
