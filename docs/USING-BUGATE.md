# Using BUGate

[English](USING-BUGATE.md) | [简体中文](USING-BUGATE.zh-CN.md)

This document now lives inside the consolidated import-adapter skill:
[`.shared/skills/bugate-import/references/using-bugate.md`](../.shared/skills/bugate-import/references/using-bugate.md)
(vendored into governed repos at `<vendor>/.shared/skills/bugate-import/references/using-bugate.md`).

## Existing installations: update, do not re-import

The complete bilingual updater runbook is
[`updating-bugate.md`](../.shared/skills/bugate-import/references/updating-bugate.md)
([中文](../.shared/skills/bugate-import/references/updating-bugate.zh-CN.md)).
Its current routing contract is:

- `bugate_init.py` is fresh-install-only. An exact v0.3.x or pre-lock v0.4.x
  installation bootstraps through `scripts/bugate_update.py` in an unpacked
  v0.4.2-or-later release. Retain that verified external source through the
  rollback window. Use the in-repo `.bugate/bin/bugate-update` interface only
  while both `.bugate/bugate.lock.json` and the executable launcher exist;
  version text alone does not select the route.
- Routine online flow is `status` → `plan --to <version>` →
  `apply --to <version>` → `verify`; remote updates have no implicit `latest`.
  Offline `plan` and `apply` repeat both `--archive <release>` and
  `--checksums <SHA256SUMS>` (prefer an explicit `--to` as a version
  cross-check).
- `plan` is zero-write and must end in `GO`. Managed local modifications,
  mixed/unknown layouts, non-standard hooks, and type/mode conflicts remain
  `NO-GO`; there is no broad force or arbitrary-local-change adoption escape.
- Preserve the apply transaction ID. Roll back only its exact current
  post-image with `rollback --transaction <id>`. Then run vendored `verify`
  only if lock+launcher remain. A first updater rollback to v0.3.x/pre-lock
  v0.4.0/v0.4.1 removes them by design; verify with
  `python3 <unpacked-release>/scripts/bugate_update.py verify . --vendor-dir .bugate`.
  Use the same external bootstrap for `status`/`verify` after an interrupted
  rollback. Stale or drifted state fails closed. Do not hand-delete
  journals/history. The v1
  updater also refuses a 129th transaction-history entry at the descriptor-safe
  128-entry cap.
- Engine update never edits profiles, role evidence, acceptance, the machine
  lineage registry, or Memory, and never runs lineage init/adopt/recover.
  Keep the complete BUGate-owned installed projection (including lock/hooks) in
  one commit and any profile/governance migration in a separate explicit action
  and commit.
- Re-trust Codex Desktop only when Codex hook bytes actually change. Any hook
  change requires a new affected-runtime session; until both required
  boundaries complete, do not claim the new enforcement surface is active.

Archive/checksum SHA-256 is tamper-evident integrity, not publisher identity:
a malicious but consistently replaced archive/checksum pair is outside the
guarantee. Obtain both through a trusted release channel.

Use that vendored guide as the canonical post-import operating manual. In
v0.4.x it includes the opt-in Wave 7 lifecycle (`designer` → `implementer` →
`reviewer`), strict Memory handoffs, local receipt verification, drift
recovery, separate CLI/Desktop role sessions, and the Codex hook re-trust
step. Do not combine `--init` and `--auto`, and after a human has passed 03B go
directly to `bugate-role approve` / `handoff` rather than regenerating 03B.

Starting with v0.4.3, every required-mode UC also has a
separate lineage-integrity gate. After scaffolding, run `lineage-status --json`;
explicitly `lineage-init` only a confirmed new UC, `lineage-adopt` a verified
non-empty legacy chain with its exact head, and `recover` registered missing,
diverged, or pending transaction history. An interrupted initialization is the
exception: JSON status exposes `active_initialization`, and the same exact
`lineage-init` resumes its durable
`pending` -> `root_absence_verified` -> `root_verified` ->
`registry_initialized` -> `chain_written` -> `completed` journal. Only
`aligned` admits normal lifecycle publication. Updater profile
`migration_required` and role-lineage integrity `migration_required` are
different states, and updater success proves neither per-UC adoption nor
recovery. After validation/preflight, transaction recovery claims its active
source or a pending `recovery_restore` before target writes, completes an
original lifecycle publication when needed, and atomically terminalizes that
source while installing the sole pending `evidence_recovery` successor. An
already-active successor resumes directly, so retry cannot expose an
aligned/no-audit gap or append another successor.
