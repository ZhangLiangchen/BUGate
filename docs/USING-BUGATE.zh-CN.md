# 使用 BUGate

[English](USING-BUGATE.md) | [简体中文](USING-BUGATE.zh-CN.md)

本文档已迁移到统一的 import-adapter skill：
[`.shared/skills/bugate-import/references/using-bugate.zh-CN.md`](../.shared/skills/bugate-import/references/using-bugate.zh-CN.md)
（导入到受治理仓后的路径是
`<vendor>/.shared/skills/bugate-import/references/using-bugate.zh-CN.md`）。

## 已有安装：更新，禁止重新导入

完整双语 updater 手册见
[`updating-bugate.zh-CN.md`](../.shared/skills/bugate-import/references/updating-bugate.zh-CN.md)
（[English](../.shared/skills/bugate-import/references/updating-bugate.md)）。当前选路契约：

- `bugate_init.py` 只负责 fresh install。精确匹配的 v0.3.x 或 pre-lock v0.4.x
  安装从解压的 v0.4.2 或更高 release 运行 `scripts/bugate_update.py` bootstrap；安装
  后仍把这份已验证的外部来源保留到 rollback 窗口结束。只有
  `.bugate/bugate.lock.json` 与 executable `.bugate/bin/bugate-update` 同时存在
  才走仓内入口；不能只凭版本文字选路。
- 日常 online 流程为 `status` → `plan --to <version>` →
  `apply --to <version>` → `verify`；remote update 没有隐式 `latest`。Offline
  `plan`/`apply` 必须重复提供 `--archive <release>` 与
  `--checksums <SHA256SUMS>`，并推荐用 `--to` 交叉校验版本。
- `plan` 对 target 零写入，且必须得到 `GO`。Managed 本地修改、mixed/unknown
  layout、非标准 hook、type/mode conflict 都保持 `NO-GO`；没有宽泛 force 或任意
  local-change adoption 逃生口。
- 保存 apply transaction ID。只可用 `rollback --transaction <id>` 回滚它当前仍
  精确一致的 post-image。rollback 后只有 lock+launcher 仍在才走 vendored
  `verify`；第一笔 updater rollback 到 v0.3.x/pre-lock v0.4.0/v0.4.1 会按设计
  移除二者，此时运行
  `python3 <unpacked-release>/scripts/bugate_update.py verify . --vendor-dir .bugate`。
  rollback 中断后也用同一外部 bootstrap 做 `status`/`verify`。
  stale/drifted state fail-closed。禁止手删 journal/history。v1 updater 在
  descriptor-safe 的 128 条 history 上限处也会拒绝
  创建第 129 条 transaction。
- Engine update 永不改 profile、role evidence、acceptance、machine lineage registry
  或 Memory，也绝不执行 lineage init/adopt/recover。完整
  BUGate-owned installed projection（含 lock/hooks）作为一个 commit，profile/
  governance migration 必须是独立显式 action 与 commit。
- 只有 Codex hook bytes 实际变化时才 re-trust Codex Desktop。任一 hook 变化都要求
  对应 runtime 新开 session；所需边界完成前不得声称新 enforcement surface 已激活。

Archive/checksum SHA-256 是 tamper-evident integrity，不是 publisher identity；恶意但
同时被替换且自洽的 archive/checksum pair 不在保证内。两者都必须来自可信 release channel。

请把该 vendored 指南作为 post-import 日常操作的唯一规范手册。v0.4.x
已在其中覆盖 opt-in Wave 7 生命周期（`designer` → `implementer` →
`reviewer`）、strict Memory 交接、本地 receipt 校验、drift 恢复、独立的
CLI/Desktop 角色会话，以及 Codex hook re-trust。不要组合 `--init` 与
`--auto`；03B 由人类设为 passed 后，应直接执行 `bugate-role approve` /
`handoff`，不能再次生成 03B。

从 v0.4.3 起，每个 required-mode UC 还拥有独立的 lineage-integrity
门。脚手架后先运行 `lineage-status --json`；只有确认是新 UC 才显式
`lineage-init`，verified 非空 legacy chain 用 exact head 执行 `lineage-adopt`，已注册
但缺失、分歧或 transaction pending 的历史走 `recover`。Interrupted initialization
是例外：JSON 状态会暴露 `active_initialization`，用相同 exact `lineage-init` 继续
`pending` -> `root_absence_verified` -> `root_verified` ->
`registry_initialized` -> `chain_written` -> `completed` 的 durable journal。只有
`aligned` 才允许普通 lifecycle publication。Updater profile `migration_required` 与
role-lineage integrity `migration_required` 是两个不同状态；updater 成功不证明 per-UC
adoption 或 recovery。Validation/preflight 通过后，transaction recovery 会在 target
write 前 claim active source 或 pending `recovery_restore`，按需完成原 lifecycle
publication，并在原子 terminalize source 的同时安装唯一 pending
`evidence_recovery` successor。已 active 的 successor 会直接继续，因此 retry 不会
暴露 aligned/no-audit gap，也不会再安装 successor。
