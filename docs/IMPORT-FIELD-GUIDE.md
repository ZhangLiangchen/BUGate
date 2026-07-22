# Moved

This document now lives inside the consolidated import-adapter skill:
[`.shared/skills/bugate-import/references/field-guide.md`](../.shared/skills/bugate-import/references/field-guide.md)
(vendored into governed repos at `<vendor>/.shared/skills/bugate-import/references/field-guide.md`).

The historical field-guide advice to rerun the importer for an upgrade is
retired. Current operations are indexed in the self-contained
[`updating-bugate.md`](../.shared/skills/bugate-import/references/updating-bugate.md)
([中文](../.shared/skills/bugate-import/references/updating-bugate.zh-CN.md)):

- init is fresh-only; exact v0.3.x/pre-lock v0.4.x bootstraps from an unpacked
  release, then the in-repo updater owns `status`/zero-write `plan`/transactional
  `apply`/read-only `verify`/exact-ID `rollback`;
- offline archive and checksum are mandatory as a pair, and SHA-256 detects
  supplied-input tampering/corruption but does not authenticate the publisher;
- managed drift, unknown/mixed layouts, non-standard hooks, stale rollback,
  and the 128-entry history cap fail closed—no broad force/adopt and no manual
  journal deletion;
- engine update preserves SUT-owned state, profile, Memory, and role evidence;
  profile migration is a separate explicit diff/commit;
- Codex re-trust is conditional on actual Codex hook-byte change, while every
  hook change requires a new agent session before active enforcement is claimed.
