# BUGATE-CORE-2026-07-22: Release acceptance integrity gaps

- Component: archive-native release acceptance gate
- Classification: BUGate Core test/release infrastructure defect; not a SUT defect
- Severity: P1
- Status: fixed and independently re-reviewed on 2026-07-22
- Scope: SUT-neutral synthetic repositories only

## Confirmed behavior

Independent adversarial review found three false-green or zero-write gaps in
the first archive-native acceptance harness:

1. The harness verified the SUT-owned snapshot before imported full-check, but
   did not verify it again afterwards. A mutating full-check could therefore
   change config, profile, hooks, use cases, role evidence, Memory namespace,
   or an unrelated dirty file while the overall report still returned `GO`.
2. Hook preservation checked only that each expected SUT entry remained a
   member of the final list. Duplicating an existing SUT entry changed hook
   semantics and count but still passed.
3. An explicitly requested report path inside BUGate Core was rejected only
   after its missing parent directory had been created, violating the gate's
   fail-closed zero-Core-write boundary.

The first two items can let a release acceptance report overstate the updater's
ownership preservation. They are release blockers even though updater Core and
its transaction engine are unchanged.

## Required closure

- Revalidate the complete SUT-owned snapshot and shared-file ownership
  projection after full-check returns.
- Compare the exact SUT-owned hook semantic projection and multiplicity after
  removing only stable BUGate-owned entries; reject additions, deletions,
  duplicates, or rewrites.
- Validate report-path containment before creating any directory or temporary
  file; a rejected path must leave Core byte- and layout-identical.
- Add negative controls that inject each mutation and prove `NO-GO` with zero
  unintended persistent writes.
- Re-run focused tests, dirty-preview tar/zip smoke, complete unittest
  discovery, and independent re-review before the clean release build.

No reduced snapshot, permissive membership check, report-path exception, or
real SUT fixture is an acceptable closure.

## Closure evidence

- Acceptance source SHA-256:
  `0eb45e5541bb5822adde950c903707852702691141ed0bfd8b3abfb5ef7d9d1d`.
- Acceptance-test SHA-256:
  `3466e59cdf29bd7dbfc9b682a68fe396537577e44601965181abb7839e1f9526`.
- Targeted acceptance gate: 12/12 PASS; release archives, release manifests,
  and repository release contract focused total: 55/55 PASS.
- Complete direct unittest discovery: 349/349 PASS.
- Rebuilt dirty-preview tar and zip both completed the v0.3.2 bootstrap,
  verify, idempotence, rollback, reapply, and imported smoke flow with the
  post-full-check preservation assertions active; archive inventory 240,
  scanned files 180, pollution findings 0.
- The original duplicate-SUT-hook and duplicate-BUGate-hook counterexamples
  now fail closed. Core-nested and outside-symlink-to-Core report paths are
  rejected without creating a directory; a safe outside nested report is
  published only after both containment checks.
- Independent re-review confirmed all three defects closed and found no
  residual hard defect. Formal publication remains separately gated on the
  exact clean-commit assets and public-download acceptance.
