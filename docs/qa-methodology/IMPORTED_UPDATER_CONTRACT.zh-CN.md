---
title: "Imported-mode 更新器契约"
version: 1.0
target_release: BUGate v0.4.2
status: normative
language: zh-CN
companion: IMPORTED_UPDATER_CONTRACT.md
---

[English](IMPORTED_UPDATER_CONTRACT.md)

# Imported-mode 更新器契约

## 1. 目的与发布边界

本文是 imported SUT 仓内 BUGate 安装升级的 v0.4.2 规范性契约。v0.4.2 是首个带有一等增量更新器的 v0.4.x 版本；它不是 `bugate init` 的别名、重新导入或脚手架刷新。

一次更新必须可审计、fail-closed、具备事务与回滚能力，并且严格限制在 BUGate-owned surface 内。archive mode 获取并校验完整 Release archive；强制支持的 unpacked bootstrap mode 则校验 canonical manifest 与每个 mapped payload，并明确 raw archive provenance 不可获得。两种模式都只应用 manifest 推导的 `add`/`update`/安全 `delete` 差异；不得以删除再复制整个 vendor directory 作为更新算法。

在第 10 节的 bootstrap、增量更新、回滚和 imported-mode 验收门全部通过前，任何 v0.4.x release 都不得宣布 GO。

## 2. 职责分离与支持的入口

`scripts/bugate_init.py` 仅负责**首次 imported-mode 安装**：创建初始 SUT-owned config/profile skeleton、skill discovery link、初始 BUGate hook fragment 与第一份 installed lock。若发现已有 installed lock 或受支持的 legacy layout，必须非零退出并提示使用 `bugate-update`；显式 `--upgrade` 只能委托给同一个 updater engine，禁止第二套升级实现。

对尚无 authoritative lock/updater pair 的 v0.3.x 或 exact pre-lock v0.4.0/v0.4.1 安装，解压后的 v0.4.2 或更高 release 提供一次性 bootstrap 接口：

```sh
cd <imported-sut-repository>
python3 <unpacked-release>/scripts/bugate_update.py plan . --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py apply . --vendor-dir .bugate
```

已验证的解包 release 必须在 imported 仓外保留到预期 rollback 窗口结束。安装完成后，只有 `<vendor-dir>/bugate.lock.json` 与 executable `bin/bugate-update` launcher 同时存在，才可使用仓内 vendored 接口；版本文字不能作为选路证据：

```sh
.bugate/bin/bugate-update status
.bugate/bin/bugate-update plan --to 0.4.2
.bugate/bin/bugate-update apply --to 0.4.2
.bugate/bin/bugate-update verify
.bugate/bin/bugate-update rollback --transaction <transaction-id>
```

远程解析必须显式给出合法 semver 目标；不得隐式升级到 `latest`。确定性的离线操作必须同时接收 archive 与 checksum asset：

```sh
.bugate/bin/bugate-update plan --archive /path/to/bugate-0.4.2.tar.gz \
  --checksums /path/to/bugate-0.4.2.SHA256SUMS
.bugate/bin/bugate-update apply --archive /path/to/bugate-0.4.2.tar.gz \
  --checksums /path/to/bugate-0.4.2.SHA256SUMS
```

`status`、`plan`、`verify` 对 SUT-owned state 均为只读。`plan` 与 `--dry-run` 对目标仓保持**零持久写入**：不得写 lock、hook、profile、Memory、cache 或 report。为校验 archive 可在仓外创建并删除临时目录。

## 3. Ownership catalog：完整写入边界

Release manifest 是 engine-managed path 的唯一目录；它必须把 full archive inventory 与 installed projection 分层。允许管理的只有：

| 类别 | Managed surface |
|---|---|
| Vendored runtime | `<vendor-dir>/scripts/**`、`<vendor-dir>/bin/**`、`.shared/skills/bugate/**`、`.shared/skills/bugate-full-check/**`、`.shared/skills/bugate-import/**`，以及 release 明确声明的 BUGate runtime/setup 文档 |
| Discovery 与 agents | BUGate skill-discovery symlink 与 BUGate-owned Codex gate-agent TOML |
| Shared integration | `.claude/settings.json`、`.codex/hooks.json` 内的 BUGate-owned entry；根 `.gitignore` 内带标记的 BUGate block |
| State | `<vendor-dir>/bugate.lock.json` 以及 gitignored 的本地 transaction state |

更新器不得写入、删除、stage、commit 或格式化其他任何 surface。这包括 `bugate.config.yaml`、`bugate.profile.yaml`、`docs/usecases/**`、`00_role_evidence/**`、human acceptance artifact、SUT tests/evidence/wrappers/operating rules、`AGENTS.md`、`CLAUDE.md`、SUT-owned hooks/skills/agents、`.gitignore` 的非标记内容、Memory data/namespace，以及产品与环境材料。无关 dirty file 只能报告 warning，不能成为 update conflict。

managed directory 内的未知文件仍是未知文件：更新器不得递归删除。目录只可在它属于 manifest、已知安全删除后为空、且类型仍为 directory 时删除。

## 4. Release manifest、legacy manifest 与 installed lock

Release builder 必须从 release staging tree 自动生成 canonical JSON release manifest，禁止手工维护文件清单。canonical bytes 使用 UTF-8、sorted key、compact separator、结尾换行。`self_digest` 是移除 `self_digest` 字段后的 canonical bytes 的 SHA-256。

```json
{
  "schema_version": 1,
  "bugate_version": "0.4.2",
  "layout_version": 1,
  "hook_contract_version": 1,
  "profile_schema_compatibility": {"source": "bugate.config.yaml:bugate.version", "min": "0.1", "max_exclusive": "0.2", "missing_maps_to": "0.1"},
  "updater_minimum_version": "0.4.2",
  "archive_inventory": [{
    "path": "scripts/bugate_core.py",
    "type": "file",
    "sha256": "<64-lowercase-hex>",
    "mode": "0644",
    "roles": ["installable_payload"]
  }],
  "installed_projection": [{
    "id": "vendor:scripts/bugate_core.py",
    "scope": "vendor",
    "source_path": "scripts/bugate_core.py",
    "target_path": "scripts/bugate_core.py",
    "type": "file",
    "sha256": "<64-lowercase-hex>",
    "mode": "0644"
  }],
  "self_digest": "<64-lowercase-hex>"
}
```

`updater_minimum_version` 表示 manifest/layout 协议的兼容下限，不是每个目标
release version 的自动副本。schema/layout 1 的下限为 `0.4.2`；后续兼容的
v0.4.x release 保持该下限，使已安装的 v0.4.2 launcher 能先校验来源，再启动
verified target worker。只有不兼容地改变 updater protocol 的 release 才提高
下限，并由旧 launcher 在目标仓写入前 fail-closed。

`archive_inventory` 自动覆盖全部 archive entry，并以 machine-readable `roles` 数组赋予一个或多个值：`installable_payload`、`release_metadata`（release/legacy manifests、bootstrap updater、plugin manifests）或 `validated_extra`。metadata 与 payload 可重叠：`scripts/bugate_update.py` 同时属于两者；canonical release manifest 通过 `generated_metadata` projection 安装。`generated_metadata` 只有在 projection 指向 verified metadata derivation 时才可写，不需要伪造 payload role。manifest 自身以 reserved self-digest reference 表达，避免递归 file hash；所有 entry 仍必须做 path/type/mode/duplicate/link/escape 校验。

`installed_projection` 是完整写入目录。每个 item 有稳定 ID，并明确属于 `vendor`（vendor dir 下 file/directory/symlink）、`workspace`（repo-root 下 BUGate 独占 agent/link）、`shared_json_fragment`（hook event/matcher/value/semantic digest）、`marked_text_block`（`.gitignore` marker/body/digest）或 `generated_metadata`（安装到 `<vendor-dir>/bugate.release.json` 的 canonical manifest 与 generated lock contract）。installed manifest 的 full-file hash 写入 installed lock，避免在自身内部递归。

每个 archive `source_path` 相对 release root 规范化；每个 rendered `target_path` 相对声明 scope 规范化。两者均不得有 empty/absolute/`.`/`..` 段，并须在 symlink-aware resolve 后保持在各自 root 内。唯一参数是单独校验的 relative `vendor_dir`。file 声明 SHA-256/mode，directory 声明 type/mode，symlink 声明 relative safe target。duplicate ID/path、conflicting type、duplicate archive name、undeclared executable 一律无效。

builder 还必须从每一个受支持的正式 v0.3.x release/tag 自动生成 legacy manifest，其中包含相同的 owned-file/hook-shape evidence 及精确 legacy layout fingerprint。支持范围由随 release 交付的 legacy manifest 定义，而非 SUT 文档中的版本字符串。正式支持的 v0.3 tag 是 v0.3.0、v0.3.1、v0.3.2、v0.3.4 与 v0.3.5（不存在 v0.3.3 release）。还必须生成 v0.4.0 与 v0.4.1 的 pre-lock adoption manifest，避免现有 v0.4.x import 无法进入新升级链。禁止“差不多相同”的 best-effort 识别。

成功 apply 后，`<vendor-dir>/bugate.lock.json` 是确定性、可提交的 installed-state record：

```json
{
  "schema_version": 1,
  "installed_version": "0.4.2",
  "previous_version": "0.3.2",
  "verified_release_digest": "<64-lowercase-hex>",
  "archive_sha256": "<64-lowercase-hex-or-null>",
  "archive_verification": "sha256-or-unavailable-from-unpacked-source",
  "release_manifest_sha256": "<64-lowercase-hex>",
  "layout_version": 1,
  "hook_contract_version": 1,
  "profile_schema_compatibility": {"min": "0.1", "max_exclusive": "0.2"},
  "updater_version": "0.4.2",
  "installed_manifest": {"path": ".bugate/bugate.release.json", "sha256": "<64-lowercase-hex>"},
  "installed_projection": [
    {"id": "vendor:scripts/bugate_core.py", "scope": "vendor", "target_path": ".bugate/scripts/bugate_core.py", "type": "file", "sha256": "<...>", "mode": "0644"},
    {"id": "skill:codex:bugate", "scope": "workspace", "target_path": ".agents/skills/bugate", "type": "symlink", "target": "../../.bugate/.shared/skills/bugate"},
    {"id": "hooks:codex:pre-write", "scope": "shared_json_fragment", "target_path": ".codex/hooks.json", "semantic_digest": "<...>"}
  ]
}
```

其中不得含 absolute machine path、time、identity、credential、token 或 SUT fact。`verified_release_digest` 始终绑定 canonical manifest 与全部 source/projection hash；remote/offline archive 操作还记录 raw archive SHA-256。解压目录 bootstrap 无法从 extracted bytes 反推出或验证原 archive digest，因此记录 null 与 `unavailable-from-unpacked-source`；解压前 checksum 校验是 operator prerequisite，不能冒充 updater observation。更新器仍按 manifest 校验每个 mapped payload。same-version no-op 不得仅因换用 tar/zip 或 container digest 不同而重写 lock，该输入 digest 只进 read-only report。完整 rendered projection 与安装的 canonical manifest 共同构成 v0.4.x update 及 archive-free `verify` 的 authoritative baseline。

## 5. Hook ownership identity 与语义合并

Hook ownership 必须是稳定数据，而非 substring heuristic。canonical v0.4.2 hook command 必须以导出的 identity prefix 开头：

```sh
BUGATE_HOOK_ID='<id>'; export BUGATE_HOOK_ID; ROOT="$(...find bugate.config.yaml...)"; [ -n "$ROOT" ] || exit 0; <vendored command>
```

精确 ID 与 entry shape 如下：

| Runtime/event | ID | 必须 matcher | 有序 command suffix |
|---|---|---|---|
| Claude `PreToolUse` write gates | `bugate.claude.pre.write.v1` | `Edit|Write` | `check_bugate.py`、`check_plan_lock.py`、`check_role_evidence.py` |
| Claude `PreToolUse` role guard | `bugate.claude.pre.role.v1` | `Read|Edit|Write` | `check_agent_role_paths.py` |
| Codex `PreToolUse` | `bugate.codex.pre.write.v1` | `apply_patch` | `check_bugate.py`、`check_plan_lock.py`、`check_agent_role_paths.py`、`check_role_evidence.py` |
| Claude/Codex `UserPromptSubmit` | `bugate.<runtime>.prompt.v1` | event default | `bugate_prompt_reminder.py` |
| Claude/Codex `SessionStart` | `bugate.<runtime>.session-start.v1` | event default | `memory_bus.py session-start --agent agent`、`bin/bugate-role session-start` |
| Claude/Codex `Stop` | `bugate.<runtime>.stop.v1` | event default | `memory_bus.py stop --agent "${BUGATE_AGENT_ROLE:-agent}"` |

`<vendored command>` 使用配置的 vendor dir 与 rooted lazy resolver。统一 pre-lock recognizer 只能接受 v0.3.0/.1/.2/.4/.5 与 v0.4.0/.1 manifest 中逐字记录的 identity-free shape：相同 event/matcher、完整有序 command list、官方 resolver 与该 release 的 entrypoint。

ID 只是 routing label，绝不能单独证明 ownership。merger 只有在完整 event、matcher、ordered value 与 semantic digest 精确等于 prior installed lock 或 shipped pre-lock manifest 时才能替换。duplicate ID、ID+shape/digest mismatch、partial canonical entry 与疑似 spoof 均为 conflict/NO-GO。mixed entry 作为 SUT-owned 保留；pre-lock adoption 中如果独立 exact legacy entry 缺失或不标准，则 adoption NO-GO，禁止通过另加 canonical entry 掩盖坏 baseline。其余 JSON value/顺序保持，只改最小 owned entry且避免 whole-file reformat。只有 `.codex/hooks.json` bytes 实变才输出 re-trust；任何 hook change 都要求新 session。

## 6. Detection、adoption 与 plan contract

检测首先读取并校验 installed lock。无 lock 时，统一使用 release-generated pre-lock manifests 对 v0.3.0/.1/.2/.4/.5 与 v0.4.0/.1 的完整 rendered projection、layout fingerprint 与 exact hook shape 做识别。精确匹配后可在 `apply` 中建立第一份 lock；`plan` 不得建立。critical file 缺失、mixed fingerprint、non-standard hook wiring、unknown layout 或 local managed modification 都是 NO-GO，并必须逐路径给出 expected/actual type/hash/mode。

禁止宽泛 `--force`。若支持接受 legacy local change，必须是显式 per-path `adopt`，在任何 apply 前将 operator’s named override 与 observed hash 记录进 JSON report。它不得静默覆盖 conflict，也不得扩大 ownership。

`plan` 必须同时提供可读输出与 `--json` 自动化输出。每个 managed item 只能归为 `unchanged`、`add`、`update`、`delete`、`locally_modified`、`conflict`、`type_changed`、`permission_changed`；hook operation 为 `hook_refresh`；profile result 为 `migration_available` 或 `migration_required`。plan 包含 from/to version、archive/manifest digest、全部 change、stale known file、local modification、hook change、profile compatibility、Codex re-trust/new-session flag、rollback availability 和最终 `GO`/`NO-GO`。

JSON plan 必须对 canonical content 及 exact base observation 计算确定性 `plan_digest`。`apply --plan <file>` 必须重新 hash/check 每一个 base item，发现 drift 即拒绝，禁止使用 stale plan。直接 `apply` 也必须在写入前构建并验证等价的 in-memory plan。

current-item 规则严格如下：current hash 等于 old manifest 才可 update；等于 new manifest 视为 already-updated；两者皆不等即 locally modified 且 NO-GO。stale file 只有仍等于 old manifest hash 才能 delete。type 与 permission change 必须单独可见，并满足相同 ownership/baseline 检查。

Profile compatibility 从 `bugate.config.yaml` 的 config-schema 字段 `bugate.version` 读取，legacy 顶层 `version` 等价；缺失 legacy 值映射为 `0.1`。malformed/unknown 值产生 blocking `migration_required`，engine update NO-GO；兼容 legacy/off role-governance profile 可报告非阻断 `migration_available`，仍由独立人工动作处理。

## 7. 事务、回滚与 crash recovery

persistent journal、transaction download、backup、worker 与 failure report 只能置于已验证 Git-ignored 的 repo-local state，不得写入 release manifest 或 installed lock。已有 lock 的安装使用 `<repo-root>/.bugate-update/`。每个受支持 pre-lock manifest 都证明 exact historical BUGate marker block 已忽略 `/<vendor-dir>/plan.lock`，因此 bootstrap 先使用已被忽略的 `<repo-root>/<vendor-dir>/plan.lock/bugate-update/`；缺少该规则或 exact block 时 apply 零写入 NO-GO。

`plan.lock` 同时是合法 optional plan-lock file，因此预存 file 或 symlink 时 bootstrap 必须零写入 NO-GO，绝不覆盖、删除或 adopt。预存 directory 只有在包含属于该 canonical repo 的 exact updater ownership sentinel 与有效 bootstrap journal 时才可 recovery；其他 directory 一律视为 operator-owned 并 NO-GO。

路径不存在时，updater 先在 repo 外同一 filesystem 的 auto-cleaned staging directory 中构造并 fsync 完整 sentinel+journal directory，再把整个 directory 原子 rename 为 `<vendor-dir>/plan.lock`。因此 repo-visible ownership intent 与 recoverable journal 在一次 filesystem operation 中同时出现：rename 前 crash 对 repo 零变化，rename 后 crash 可明确恢复。发布必须使用内核提供的 no-replace 原语（Darwin `renameatx_np(RENAME_EXCL)` 或 Linux `renameat2(RENAME_NOREPLACE)`）；普通 `rename`/`os.replace` 可能在检查后覆盖并发创建的空目录，存在 TOCTOU 风险。当前平台无法保证 same-filesystem atomic no-replace rename 时，apply 必须对目标仓零写入并 NO-GO。该目录存在期间会激活 write gate，形成额外 fail-closed 信号。

任何 repo 写入前，updater 以 plan 允许的 auto-cleaned temporary 方式完成 archive/checksum/manifest/version/base 校验并获取 workspace lock，再把 verified transaction input、旧 `.gitignore` snapshot、backup 与 journal 复制进上述 ignored bootstrap state。随后把 exact BUGate block 作为第一项 transaction mutation，验证新的 root-state ignore，建立 `<repo-root>/.bugate-update/` 并在任何 vendor/hook mutation 前把 journal 迁移过去。transaction worker 从这个 vendor 外 root state 执行；仅最初 self-copy 可短暂使用 OS temp，且可由 ignored state 内 verified copy 重建。durable transition 完成后、vendor/hook mutation 前，只删除 updater 自己创建且已为空的 `<vendor-dir>/plan.lock` bootstrap directory，绝不删除 operator-created plan lock。失败恢复旧 block，crash 从 ignored journal 恢复；并发 updater 非零退出且不得触碰 managed/SUT-owned state。

一次 apply transaction 依次为：

1. 获取 workspace/update lock，恢复先前中断 journal；
2. 校验 root、vendor dir、lock/legacy state、archive checksum、archive safety、release manifest、version agreement 与 freshly observed plan base；
3. 将 transaction-scoped、self-contained updater worker 及全部 imports copy 到 vendor tree 外，在 journal 记录其 path/digest，再在该处 stage 并校验 new content/mode/symlink target；
4. snapshot 每个将变更的 managed path、shared hook file 与 installed lock；
5. 仅原子替换允许的 managed file，并做 safe known delete；
6. 对 owned hook entry 与标记 `.gitignore` block 做语义合并；
7. 对 new manifest 与 lock candidate 执行 post-update `verify`；
8. 原子写入 new installed lock，标记 journal committed，写 transaction report 并清理 staging。

在 commit 前任一失败点或受处理 interrupt，worker 必须恢复 managed path、shared hook file 与 old lock 的 snapshot，并写 failure report。crash 会留下 durable journal；只读 `status`、`plan`、`verify` 仅报告 `recovery_required`；下一次 `apply` 或显式 `rollback` 先恢复。显式 rollback 持同一 workspace lock，本身 journaled/atomic/interrupt-safe/crash-recoverable；在恢复旧 transaction 前，必须逐项确认当前 owned item、semantic fragment、installed manifest 与 lock 精确等于该 transaction 的 post-image，后续 update 或 local drift 会使其 stale 并 NO-GO。rollback path validation 与 apply 采用相同 no-escape 规则。

Rollback 恢复的是 exact recorded pre-image，而不是永久升级后的 control plane。因此，第一笔 v0.4.2 updater transaction 回滚到 v0.3.x 或 pre-lock v0.4.0/v0.4.1 时，会移除 installed lock 与 vendored launcher。rollback 后必须按 restored state 选 verify 入口：只有 lock 与 executable launcher 同时仍在才使用 vendored `verify`；否则使用保留的外部 updater：

```sh
python3 "$BOOTSTRAP" verify . --vendor-dir .bugate
```

`$BOOTSTRAP` 指向 imported 仓外已验证且已解包的 v0.4.2 或更高 release updater。这个只读验证必须能识别 exact supported legacy/pre-lock image，且不得为此安装 lock 或 launcher。若 rollback 在 launcher 变化后中断，同一外部 updater 必须承担只读 `status`/`verify` 以及 recovery 所需的 exact transaction-specific rollback retry。操作者不得重建 launcher，也不得手改 journal/sentinel。

v1 transaction image 是逻辑内容镜像，不是完整的 inode 元数据快照。journal 仅记录 file 的 type、bytes/SHA-256 与 mode，directory 的 type 与 mode，以及 symlink target。extended attributes、ACL、uid/gid、hardlink 关系和 timestamps 不在 v1 journal 与 rollback 保证范围内。因此 atomic replacement 可能分配新 inode，而不保留这些元数据。如果 operator 在 managed 或 shared file 上依赖这些元数据，必须在更新前独立备份，或先移除该依赖。

descriptor-pinned v1 validator 最多保留并校验 128 个 transaction journal。历史恰好为 128 时，既有历史仍有效，中断恢复仍可执行；但任何会创建第 129 个 journal 的 public apply、bootstrap reuse 或 rollback，必须在写入持久仓库状态或 transition intent 之前 fail-closed。BUGate 不会自动裁剪已提交的 rollback 历史。这是明确的运维上限，不代表无限 rollback retention。

当 rollback 恢复的 pre-lock installation 之 historical marked block 不忽略 `/.bugate-update/` 时，必须先复制并 fsync committed state 与 reports，再用同一 exclusive no-replace 规则把 exact `<vendor-dir>/plan.lock/bugate-update/` 形态原子发布，之后才 retire root state。后续 bootstrap 只有在 marker、sentinel、完整 journals、root identity 与 tree digest 全部通过时，才可复用 idle archived state。crash 临时留下双份 state 时，只读命令报告 recovery-required；mutating recovery 只协调完全相同的副本，副本不同时绝不猜测或覆盖。

## 8. Profile、role governance 与 Memory 隔离

engine update 与 governance activation 是两个动作。更新器可交付支持 role governance 的 script/hook、检查 profile schema compatibility、报告 `migration_available`/`migration_required`、生成 proposed profile patch；但不得在 `plan` 或 `apply` 中编辑 profile、将 `mode: off` 改为 `required`、伪造 human acceptance/handoff/receipt evidence、修改 `00_role_evidence/**` 或改变 `memory.namespace`。

可选 profile-migration command 默认仅 check/plan。如未来提供 write，必须是独立显式 action，产生独立可审 diff，且不属于 engine transaction。推荐 adoption 因而是两个可独立回滚的 commit：先更新 vendor engine 并保留 legacy profile；再审查并有意启用 strict role governance。

更新器不得重建、清空、迁移或写入 Memory Bus data，只可作 read-only health check。Memory downtime 必须单独报告：它不能把已落盘的 engine update 误称为已完全验收的 strict-role transition。required Memory transition 仅在操作者之后 activation/verification governance 时 fail-closed。

## 9. Archive safety 与威胁模型

stdlib-only reader 必须校验每个 tar/zip entry，并拒绝带 absolute path、`..`、empty/duplicate/conflicting name、path traversal、unsafe hardlink、unsafe symlink 或 symlink escape 的 payload。完整 BUGate release 可以包含不进入 vendor 的 Core docs/plugin files；bootstrap updater、release/legacy manifests 与 plugin manifests 作为 metadata 明确 inventoried，metadata 也可同时是 installable payload，validated extra 永不作为 update input。只有 mapped `installable_payload`（包括兼具 metadata role 的 entry）及显式 derived `generated_metadata` projection 可写目标。还必须在目标仓写入前拒绝 invalid semver、ambiguous/missing/duplicate checksum record、checksum mismatch、manifest/archive/plugin/CLI target-version disagreement以及逃出 declared vendor/workspace/shared scope root 的 manifest path。

这是 tamper-evident integrity check，不是 cryptographic publisher identity 或 signed supply chain。SHA-256 加上选定的 GitHub Release archive 能发现 supplied checksum 相对的意外损坏或变更，但不能证明发布者身份，也不能阻止恶意但 checksum 一致的 release。

## 10. Verification 与 GO gates

`verify` 必须检查 installed-lock 的确定性与一致性、release-manifest digest、每个 owned file 的 type/hash/mode/symlink target、exact canonical hook ownership 及 non-owned hook entry 的保留。它不得修复 drift。

Release acceptance 必须从 clean Core checkout 构建，并以真实 command、exit code 与 test count 证明：

1. plugin/version/release-manifest agreement、archive SHA-256、safe extraction，以及 archive 中没有 SUT fact、secret、cache 或 developer-machine path；
2. 用 archive 内 bootstrap updater 升级 fresh synthetic temporary v0.3.2 imported fixture，且所有 non-owned fixture 保持 byte-identical；
3. imported smoke/full-check 成功、same-version idempotence、explicit rollback 与 verify；
4. 每个 transaction stage 和 ownership boundary 都有 unit、integration、negative、concurrency、crash-recovery、failure-injection coverage。

对于 updater/archive release gate，"imported smoke/full-check" 明确指用
`--full-check-mode smoke --full-check-archive both` 运行 archive-native
acceptance。这不是浅层 binary check：它会对 tar 与 zip 同时执行 installed-state
verification、strict Memory 六次 transition 契约、bootstrap/apply、幂等、
rollback/reapply 与更新后的 ownership-preservation oracle。独立的
`--full-check-mode full` 用于审计操作者机器上的可选 Codex+Claude 异源 runtime；
其真实结果必须如实报告，但一个 provider 的账号或网络故障不会推翻已经验证通过的
updater archive。

语义 release review 保持 provider-neutral，并且至少需要两个真实、独立的 session。
优先使用跨 provider review；一个 provider 不可用时，仅当同源 reviewer 是新建
session、使用不同 review prompt，且在 synthesis 前无法看到第一个 reviewer 的输出，
才接受 same-provider fallback。fallback 与两个结论都必须记录。placeholder、延续
同一会话或 self-review 永远不能满足该门禁。

所有测试必须在运行时构造 SUT-neutral temporary repository。不得读取、复制、clone、worktree 或修改真实 imported SUT。GO 要求 release、archive、updater、compatibility 与既有 Wave 7 gate 全部通过；desktop hook hash change 仍需用户 re-trust，未 re-trust 前不得声称 hook 已激活。
