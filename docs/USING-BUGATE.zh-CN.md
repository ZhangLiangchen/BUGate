# 使用 BUGate

[English](USING-BUGATE.md) | [简体中文](USING-BUGATE.zh-CN.md)

本文档已迁移到统一的 import-adapter skill：
[`.shared/skills/bugate-import/references/using-bugate.zh-CN.md`](../.shared/skills/bugate-import/references/using-bugate.zh-CN.md)
（导入到受治理仓后的路径是
`<vendor>/.shared/skills/bugate-import/references/using-bugate.zh-CN.md`）。

请把该 vendored 指南作为 post-import 日常操作的唯一规范手册。v0.4.0
已在其中覆盖 opt-in Wave 7 生命周期（`designer` → `implementer` →
`reviewer`）、strict Memory 交接、本地 receipt 校验、drift 恢复、独立的
CLI/Desktop 角色会话，以及 Codex hook re-trust。不要组合 `--init` 与
`--auto`；03B 由人类设为 passed 后，应直接执行 `bugate-role approve` /
`handoff`，不能再次生成 03B。
