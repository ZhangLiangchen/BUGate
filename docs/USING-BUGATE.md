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
  v0.4.2-or-later release. Once installed, run the in-repo
  `.bugate/bin/bugate-update` interface.
- Routine online flow is `status` → `plan --to <version>` →
  `apply --to <version>` → `verify`; remote updates have no implicit `latest`.
  Offline `plan` and `apply` repeat both `--archive <release>` and
  `--checksums <SHA256SUMS>` (prefer an explicit `--to` as a version
  cross-check).
- `plan` is zero-write and must end in `GO`. Managed local modifications,
  mixed/unknown layouts, non-standard hooks, and type/mode conflicts remain
  `NO-GO`; there is no broad force or arbitrary-local-change adoption escape.
- Preserve the apply transaction ID. Roll back only its exact current
  post-image with `rollback --transaction <id>`, then run `verify`; stale or
  drifted state fails closed. Do not hand-delete journals/history. The v1
  updater also refuses a 129th transaction-history entry at the descriptor-safe
  128-entry cap.
- Engine update never edits profiles, role evidence, acceptance, or Memory.
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
