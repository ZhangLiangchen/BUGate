# 在 Imported 仓中更新 BUGate

[English](updating-bugate.md) | [简体中文](updating-bugate.zh-CN.md)

本文是已有 imported BUGate 安装的操作者手册，适用于 BUGate v0.4.2 首次引入的
一等更新器及后续兼容版本。它与首次安装严格分开：`scripts/bugate_init.py` 只创建
新的 imported 安装，不是升级、重新导入或 vendor 刷新命令。

规范性的 ownership 与 transaction 规则位于 release 源码中的
`docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.zh-CN.md`；本 vendored 指南给出
自包含的实际操作路径。

## 1. 先按真实状态选路

所有命令都从 imported SUT 测试仓根执行，也就是包含 `bugate.config.yaml` 的目录。

| 观察到的状态 | 正确路径 |
|---|---|
| 尚未安装 BUGate | 从可信 release 运行一次 `scripts/bugate_init.py <repo>`。 |
| 精确匹配受支持 v0.3.x，且无 updater/installed lock | 从解压后的 v0.4.2 或更高版本 release 运行更新器；见 §2。 |
| 精确匹配 pre-lock v0.4.0 或 v0.4.1 | 同样走解压 bootstrap；见 §2。 |
| 已有 installed lock 和 `.bugate/bin/bugate-update` | 走 vendored `status` / `plan` / `apply` / `verify` / `rollback`；见 §3。 |
| 未知/混合布局、非标准 hook、或 managed 文件存在本地修改 | 停在 `NO-GO` 并处理明确指出的冲突；禁止重跑 init 或强制覆盖。 |

正式支持的 v0.3 tag 是 v0.3.0、v0.3.1、v0.3.2、v0.3.4、v0.3.5；不存在
v0.3.3 release。识别依据是 release 自动生成的文件、mode、布局与 hook 证据，
而不是某个版本字符串或“差不多相同”。

规划前先完成：

1. 结束或停止正在进行的 agent 工作，并保存 SUT-owned 改动。建议先做干净 commit
   或独立备份。无关 dirty 文件只产生 warning，但 updater-managed path 漂移是阻断冲突。
2. 选择明确 target version；remote mode 永不隐式解析 `latest`。
3. archive/offline mode 下，从可信通道取得 release archive 与 `SHA256SUMS` asset，
   并放在 imported 仓之外。
4. 在 update 及有意保留的 rollback 窗口结束前，持续保留一份位于 imported 仓外、
   已验证且已解包的 v0.4.2 或更高 release。定义其 updater 路径，例如
   `BOOTSTRAP=<unpacked-release>/scripts/bugate_update.py`。第一笔 updater
   transaction 可能恢复尚无 updater 的 projection，因此 vendored launcher
   不保证在 rollback 后仍存在。
5. 如果 managed/shared 文件依赖 ACL、extended attributes、ownership、hardlink
   identity 或 timestamp，须另行备份。v1 journal 只恢复逻辑 bytes/type/mode/
   symlink target，不保证这些 inode metadata。

## 2. 从 v0.3.x 或 pre-lock v0.4.x 做一次性 bootstrap

旧安装没有可被信任的 updater launcher，因此从 imported 仓**外部**解压的目标 release
执行更新器：

```sh
cd <imported-sut-test-repo>
python3 <unpacked-release>/scripts/bugate_update.py status . \
  --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py plan . \
  --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py apply . \
  --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py verify . \
  --vendor-dir .bugate
```

`plan` 零写入。baseline 精确匹配时，`apply` 可 adoption 该官方 pre-lock 布局并
创建第一份 installed lock。识别失败、critical file 缺失、fingerprint 混合、hook
wiring 非标准或 managed 文件本地修改均为 `NO-GO`，在 baseline 完成校准前必须保持
阻断。

仅解压目录模式会校验 canonical release manifest 与每个 mapped payload，但无法从
解压后的 bytes 证明原始 archive digest。因此 installed lock 会记录
`archive_sha256: null` 与 `unavailable-from-unpacked-source`。必须在解压**之前**
验证 archive checksum，并单独保留 provenance。

如果原始 archive 与 checksum asset 仍在，推荐使用 archive mode，即使 bootstrap
程序本身来自解压目录。plan 与 apply 必须重复同一组 source 参数：

```sh
python3 <unpacked-release>/scripts/bugate_update.py plan . \
  --vendor-dir .bugate --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
python3 <unpacked-release>/scripts/bugate_update.py apply . \
  --vendor-dir .bugate --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
```

这样会在任何 target write 前校验 raw archive，并在仓外临时目录解压 update input。

## 3. updater 已安装后的日常更新

只有 authoritative installed lock 与 executable launcher 同时存在时（默认分别是
`.bugate/bugate.lock.json` 和 `.bugate/bin/bugate-update`），才走本节路径。
不能只凭版本标签选路。精确 pre-lock v0.4.0/v0.4.1 layout，或 rollback 恢复出的
legacy/pre-lock image，仍走 §2 的外部 bootstrap。

### Remote release

```sh
cd <imported-sut-test-repo>
.bugate/bin/bugate-update status
.bugate/bin/bugate-update plan --to <version>
.bugate/bin/bugate-update plan --to <version> --json \
  > /path/outside/repo/bugate-update-plan.json
.bugate/bin/bugate-update apply --to <version> \
  --plan /path/outside/repo/bugate-update-plan.json
.bugate/bin/bugate-update verify
```

保存 plan 非强制但推荐。`apply --plan` 会重建 plan、重新 hash 每个 base item，
并拒绝 drift 或不同 input；直接 `apply` 也会在写入前构建同等的新鲜 in-memory plan。
`apply --dry-run` 与 `plan` 一样，对 target repo 零持久写入。

### 确定性的 offline archive

```sh
.bugate/bin/bugate-update plan --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
.bugate/bin/bugate-update apply --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
.bugate/bin/bugate-update verify
```

`--archive` 与 `--checksums` 必须成对出现。推荐同时指定 `--to`，因为 CLI target、
archive/checksum 文件名、release manifest 与 plugin metadata 必须一致。只要 release
发布了匹配 checksum record，tar 与 zip 都受支持。

## 4. 授权写入前先读懂 plan

最终 decision 不是 `GO` 时禁止执行 `apply`。至少审查：

- `from_version`、`to_version`、release/manifest digest 与 source kind；
- 每个 managed item 的分类：`unchanged`、`add`、`update`、安全 `delete`、
  `locally_modified`、`conflict`、`type_changed` 或 `permission_changed`；
- 每个 `hook_refresh`，以及 `codex_hook_hash_changed`、`new_session_required`；
- profile 状态：`migration_available` 非阻断且是独立动作；`migration_required` 阻断；
- rollback availability、warnings 与每个明确的 `NO-GO` reason。

更新器只拥有 release manifest 的 installed projection。它不会写、删、stage、commit
或格式化 SUT tests、use-case artifacts、`00_role_evidence/**`、profile/config、
Memory data、SUT-owned hooks/skills、operating rules 或产品/环境材料。managed 目录内的
未知文件也不会被递归删除。它也永不创建或编辑 effective Memory home
（`MCP_MEMORY_BASE_DIR` → `BUGATE_MEMORY_HOME` → `~/.bugate/memory-bus`）下的
machine `role-lineage.sqlite3` registry、deterministic Memory root/checkpoint 或 per-UC
lineage transaction。

### Conflict 与 adoption 行为

- 当前 managed item 与 old manifest 相等才可 update；已等于 target 则 unchanged；
  第三个 hash 是 `locally_modified` 且 `NO-GO`。stale known file 只有仍等于 old
  manifest image 时才可删除。
- Hook ID 本身不能证明 ownership；完整 event、matcher、有序 commands 与 semantic
  digest 必须匹配 installed 或随 release 交付的 historical contract。mixed、partial、
  duplicate 或 spoof-shaped entry 都是 conflict。
- 禁止宽泛 `--force`；当前 CLI 也没有任意本地改动的通用 adoption 命令。只有精确
  official pre-lock baseline 可在已审查 `apply` 中自动 adoption。把有意定制移到
  SUT-owned wrapper/profile，或把明确指出的 path 校准回 official baseline，再重跑
  `plan`。
- 如果未来提供 local-change adoption surface，必须逐 path 显式执行，记录 observed
  hash 与 operator decision，且仍不得扩大 BUGate ownership 或掩盖 conflict。

## 5. Apply、verify、review、commit

`apply` 获取 workspace lock，在 vendor tree 外 stage 并校验 target，snapshot 将变化
的 managed/shared item，只原子安装计划内 projection，校验 post-image，写 installed
lock，并报告 transaction ID。保留该 ID 以备 rollback。

apply 成功后：

1. 运行 `.bugate/bin/bugate-update verify`；它只报告 drift，绝不修复。
2. 运行 imported smoke：
   `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke`。
3. 在宣布更新后的 gate 已激活前，重跑 profile-specific write-guard negative control 与
   相关 role-governance check。
4. 检查 `git diff`/status，确认 SUT-owned hook 与无关 dirty file 保持不变；更新器不会
   stage 或 commit。
5. 将完整 BUGate-owned installed projection（含 lock 与 hook 变化）作为一个可审查
   commit 提交，并保持既有 profile 行为不变。

Hook 激活存在 process boundary：

- 只有 plan/apply/rollback 明确报告 `.codex/hooks.json` hash 实际变化时，才要求
  Codex Desktop re-trust；byte-identical hook 不应要求 re-trust。
- 任一 hook 变化都要求**新 agent session**，对应 runtime 才会加载新 hook surface。
  apply 或 rollback 后关闭并重新打开受影响的 Claude/Codex session。在需要的 re-trust
  与新 session 完成前，只能报告 file/update verification，不能声称 runtime enforcement
  已激活。

## 6. Rollback、recovery 与 history 上限

使用 apply 报告的 32-hex ID 回滚一笔 committed transaction：

```sh
.bugate/bin/bugate-update rollback --transaction <transaction-id>
```

Rollback 本身同样持锁、journaled、atomic 且 crash-recoverable。它先要求当前 installed
projection、hooks、manifest 与 lock 精确等于该 transaction 记录的 post-image；后续
update 或 local drift 会让旧 transaction stale 并 `NO-GO`，不会覆盖较新的状态。
检查 rollback 的 hook flags，并重复 conditional re-trust/new-session 处理。

rollback 完成后，必须根据**当时仍存在的入口**选择 verify。第一笔 v0.4.2 updater
transaction 若回滚到 v0.3.x 或 pre-lock v0.4.0/v0.4.1，会精确恢复 pre-updater
projection，因此 installed lock 与 `.bugate/bin/bugate-update` 都会被移除。禁止重建或
手工复制 launcher：

```sh
if test -f .bugate/bugate.lock.json && test -x .bugate/bin/bugate-update; then
  .bugate/bin/bugate-update verify
else
  python3 "$BOOTSTRAP" verify . --vendor-dir .bugate
fi
```

`$BOOTSTRAP` 必须指向 imported 仓外保留的、已经验证并解包的 v0.4.2 或更高
release updater。外部 `verify` 能识别并验证 exact supported legacy/pre-lock
image，不会为此写新 lock 或重装 launcher。

写事务中断后，只读 `status`、`plan`、`verify` 只报告 `recovery_required`，不会修改仓。
下一次真实 `apply` 或显式 `rollback` 会在 workspace lock 下执行 journal-driven
recovery。禁止为了“解卡”而删除、改名或手改 `.bugate-update/`、
`.bugate/plan.lock/bugate-update/`、journal、sentinel 或 installed lock。

中断的 rollback 可能已经移除或替换 vendored launcher。此时使用保留的外部
bootstrap 做只读诊断与验证；若需要 recovery，也用它重试同一个已审查 rollback：

```sh
python3 "$BOOTSTRAP" status . --vendor-dir .bugate
python3 "$BOOTSTRAP" rollback . --vendor-dir .bugate \
  --transaction <transaction-id>
python3 "$BOOTSTRAP" verify . --vendor-dir .bugate
```

v1 实现为了用 pinned descriptor 防御 path-exchange attack，最多校验 **128 条
transaction-history entry**。已有 128 条时，任何会创建新 transaction 的操作都会在
target write 前被拒绝；超过 128 条的 store 非法并 fail-closed。当前 updater 没有公开
prune 命令。禁止手删 history：保留 state 与 report，停止更新，使用后续兼容 release
明确评审过的 archive/migration 流程，或升级给 BUGate maintainer 处理。

## 7. Profile 与 role-lineage migration 都是独立 action

Engine update 与 governance activation 不是同一 transaction。更新器可以报告 profile
compatibility，但绝不会编辑 `bugate.config.yaml`、profile、`memory.namespace`、human
acceptance、role receipt 或 `00_role_evidence/**`。它也绝不运行 `lineage-init`、
`lineage-adopt` 或 `recover`，不写 machine lineage registry 或 Memory home。

- plan 报告阻断的 `migration_required` 时，先把最小 profile-schema compatibility 修正
  作为独立可审变更完成并验证，再重跑 `plan`；禁止把它藏进 engine apply。
- plan 报告非阻断的 `migration_available` 时，先在 legacy/off profile 不变的前提下
  apply/verify/commit engine；随后再审查独立 profile diff（例如有意启用
  `role_governance.mode: required`），验证其 negative controls 与 Memory boundary，
  并单独 commit。
- 不得假设当前 updater 版本存在自动 profile migration 命令。按 profile schema reference
  操作并要求明确 human decision，绝不伪造 acceptance、handoff 或 receipt evidence。

两个 commit 的边界让 engine update 与 governance adoption 可以分别审计、分别回滚。
Engine rollback 不会回滚独立 profile commit。

### 区分两种 `migration_required`

- Updater **profile** `migration_required` 出现在 `bugate-update plan`，表示 profile
  schema 与 target engine 不兼容；独立审查的 profile 修正完成前，它会阻断 engine
  apply。
- Role-lineage **integrity** `migration_required` 出现在 lineage-capable engine/profile
  已进入范围后的 `bugate-role lineage-status`；它表示存在本地历史但没有 registry
  row，或 strict deterministic root 证明曾存在 lineage。Adoption 会另外执行完整的
  non-empty-chain verification。它不是 updater conflict，updater 成功也不能清除或
  接受它。

Engine projection 完成 apply、verify、审查并提交后，并且任何独立批准的 profile action
已经让 required role governance 生效时，对每个 governed UC 单独分类：

```sh
.bugate/bin/bugate-role lineage-status docs/usecases/<UC> --json

# 仅限真实首次使用；从 lineage-status 复制 exact ID。
.bugate/bin/bugate-role lineage-init docs/usecases/<UC> \
  --lineage-id <exact-lineage-id>

# Verified 非空 pre-v0.4.3 chain；重写 0 个 receipt。
.bugate/bin/bugate-role lineage-adopt docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-chain-head>

# 已注册但缺失/分歧的历史，或 active publication/recovery transaction。
.bugate/bin/bugate-role recover docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-head-or-EMPTY> \
  [--archive <trusted-recovery-archive>]
```

初始非零 `uninitialized` 是预期的只读结果；init 前必须确认真实首次使用。
`aligned` 无需迁移；verified 非空 legacy chain 走 adoption。只有 strict root 而没有
可信本地历史时，必须保持阻断，直到恢复 pre-loss evidence，禁止覆盖 root 做 init。
Initialization 会在任何 Memory request 前写入 durable intent，并按
`pending` -> `root_absence_verified` -> `root_verified` ->
`registry_initialized` -> `chain_written` -> `completed` 推进。若 JSON 状态显示
`recovery_pending` + `active_initialization`，用 exact `lineage-init` 继续原 intent；不要
运行 `recover`。已注册的 `history_missing`、`history_diverged`，或带 active
publication/recovery transaction 的 `recovery_pending` 才走 exact recovery。Recovery
会让原 checkpoint-verified/ready-for-CAS publication 继续完成同一笔 CAS 与 local
publish。Validation/preflight 通过后，它会在 target write 前 claim active source，或
创建并 claim pending `recovery_restore`。Registry 在同一个 SQLite transaction 中
terminalize 该 restore/lifecycle source，并安装唯一 pending `evidence_recovery`
successor；中间没有 aligned/no-audit crash gap。若 active source 已是
`evidence_recovery`，retry 会直接继续它，不会再安装 successor 或生成重复的
lifecycle/recovery sequence。
Best-effort Memory 下，本地历史丢失还必须提供独立保留的可信 archive；required Memory
在 registry 与 Memory history 尚存时从 exact immutable checkpoint 重建。显式提供
archive 只选择候选 bytes；retained strict checkpoint 仍必须存在并保持权威，每个
archive envelope 都必须在任何写入前与其精确一致。它不是 strict Memory 不可用或
分歧时的离线回退。

因此 updater `apply`/`verify` 可以成功，而一个或多个 UC 尚未 `aligned`。报告时必须把
两种结果分开；绝不能把 engine installation 成功写成 lineage migration 已接受或
Wave 7 runtime 已激活。

## 8. SHA-256 威胁模型

Archive 与 manifest 检查是 tamper-evident integrity control。它会拒绝 corruption、
ambiguous/missing/duplicate checksum record、archive traversal/unsafe link、版本不一致、
未声明 executable payload，以及 SHA-256 不等于 canonical manifest 的 mapped payload。

它**不是 publisher authentication 或 signed supply chain**。攻击者如果能同时替换 archive
与 checksum，就能提供 self-consistent malicious pair。必须从可信 release channel 获取
target version、archive 与 checksum；风险模型要求 publisher identity 时，还要独立验证
release provenance。

## 9. 完成报告

记录：

- from/to version、source kind、release/manifest digest，以及 archive SHA-256 或明确的
  unpacked-source limitation；
- plan decision 与所有 conflict/warning；
- apply transaction ID、`verify` 与 smoke exit code；
- hook changes、Codex re-trust 是否需要/已完成，以及打开了哪些新 session；
- profile migration 状态及其独立 commit/action（若有）；
- 每个 UC 的 role-lineage `integrity_state` 与独立审查的 init/adopt/recover action
  （若有）；
- 执行过 rollback 时的结果，以及任何残留 recovery/history limit。

`verify` 仍为 `NO-GO`、recovery 未完成、必须的 Codex re-trust/new session 未完成，或
必需 profile migration 尚未独立解决时，更新均未完成。
Engine update 成功仍不代表 per-UC lineage acceptance；任何声称 required Wave 7
governance 已激活的范围，在其 governed UC 全部 `aligned` 前都未完成。
