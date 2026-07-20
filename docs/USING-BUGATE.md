# Using BUGate

[English](USING-BUGATE.md) | [简体中文](USING-BUGATE.zh-CN.md)

This document now lives inside the consolidated import-adapter skill:
[`.shared/skills/bugate-import/references/using-bugate.md`](../.shared/skills/bugate-import/references/using-bugate.md)
(vendored into governed repos at `<vendor>/.shared/skills/bugate-import/references/using-bugate.md`).

Use that vendored guide as the canonical post-import operating manual. In
v0.4.0 it includes the opt-in Wave 7 lifecycle (`designer` → `implementer` →
`reviewer`), strict Memory handoffs, local receipt verification, drift
recovery, separate CLI/Desktop role sessions, and the Codex hook re-trust
step. Do not combine `--init` and `--auto`, and after a human has passed 03B go
directly to `bugate-role approve` / `handoff` rather than regenerating 03B.
