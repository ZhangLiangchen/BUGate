# BUGate for DeepSeek Harness

[English](DEEPSEEK_HARNESS.md) | 简体中文

共享 TypeScript 核心评估有限、有来源的任务事实，统一 Node CLI 与 Cordis 插件消费同一实现。DSH 负责 Agent loop、Team 成员、消息、任务板、调度和生命周期。任务事实及 SUT 依赖保留在明确选择的外部项目。

`2.2.0-dev.1` 实现正在验证。候选安装结果不等于最终 Team 验收。公开 Python release 保留历史身份，本次不发布或覆盖 release。

| 组件 | 目标 |
|---|---|
| Core / CLI / 安装器 / 原生包 | `2.2.0-dev.1` |
| Protocol | 精确 `2.2.0-dev.1` 资源摘要 |
| DSH / 官方 experimental Agent Team 包 | `0.2.2-alpha.2` |
| Cordis | `4.0.6-alpha.1` |
| Node | `24.9.0+`，另记录实际测试版本 |

tarball 包含第一方编译后 JavaScript、运行依赖、canonical Schema/MethodSpec、生成的 Skill 投影和官方 Team profile 原始层，不包含 Python bootstrap、wheel 或产品 fallback。宿主依赖可能需要 registry，不声称内置完整离线 DSH。使用 POSIX 进程组；仅报告实测平台，不推导 Windows 支持。

## 安装和检查

使用同一次构建的全部资产，先验证 `SHA256SUMS` 再执行独立 Node 安装器。须已安装 DSH 与 pnpm，外部项目目录须存在。明确选择项目、DSH home 和 profile。

```sh
shasum -a 256 -c SHA256SUMS
node install.mjs \
  --package ./bugate-deepseek-harness-2.2.0-dev.1.tgz \
  --project /absolute/path/test-repo \
  --dsh-profile bugate \
  --dsh /absolute/path/dsh \
  --dsh-home /absolute/path/dsh-home
BUGATE=/absolute/path/dsh-home/profiles/bugate/node_modules/.bin/bugate
"$BUGATE" doctor integrations --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/dsh \
  --dsh-home /absolute/path/dsh-home --json
```

setup 调用真实 DSH 插件管理器，记录包身份及自有配置块，并核验合成配置。同字节重复安装幂等；拒绝接管无所有权包、覆盖已修改配置块或使用含糊目标。doctor 验证留存/已安装字节；配置健康不证明 Agent hydration 或 Team 评审通过。实际 DSH 会话须加载该 profile 的官方 Team 层与 BUGate。

原生包在 BUGate 前组合官方 Team profile 原始 patch，由其提供 Team service/tools，并按 DSH 规则禁用直接 subagent/control/fork 工具。不要在同一 profile 再重复添加 Team profile 层。experimental API 固定版本，不称为稳定接口。

## 绑定有限任务

依据包内 `profile.schema.json` 编写 Profile，提供真实领域来源路径/摘要、固定需求/风险 ID 和适用评审策略，然后：

```sh
"$BUGATE" init --project /absolute/path/test-repo --task TASK-1 \
  --sut-profile /absolute/path/profile.json --json
"$BUGATE" protocol prepare --task /absolute/path/test-repo/test-task/TASK-1 --json
"$BUGATE" status --task /absolute/path/test-repo/test-task/TASK-1 --json
"$BUGATE" artifact record --task /absolute/path/test-repo/test-task/TASK-1 \
  --input .bugate/drafts/brief.json --json
"$BUGATE" assess --task /absolute/path/test-repo/test-task/TASK-1 \
  --claim claims/design.json --json
```

任务选择含糊时，使用 `setup deepseek-harness ... --task-path /absolute/path/.../TASK-1`。已有安装重新绑定须先明确移除；不推断最新任务、不自动初始化。用户收据证明选择，领域事实仍来自 Profile 声明证据。覆盖分母内的授权分支/Oracle 变更新建范围版本；分母变更须新 Profile 版本与新任务，保留旧事实和失败。建议不可执行、不阻塞。

任务保留 artifacts、evidence、claims、assessments、scope 历史以及不可变宿主观察/结算。核心拒绝不匹配的协议、评估器或 Profile 绑定。旧 Python 任务仅作只读历史：检查留存 JSON/日志及其身份，或在独立历史环境中使用匹配 release。不提供转换器或跨版本授权桥；Node 新任务需要明确当前绑定。

## 原生工具与评审来源

使用 `bugate_prepare`、`bugate_status`、`bugate_validate`、`bugate_scope`、`bugate_check_scope`、`bugate_record_scope`、`bugate_record_suggestion`、`bugate_record_artifact`、`bugate_record_evidence`、`bugate_assess` 和 `bugate_run_test` 执行相应质量操作。导入材料保留 model/import 来源。

`bugate_publish_artifact` 捕获真实 live Team 成员的内联输出及宿主身份，绑定作者/评审执行、Team 成员、fresh/fork 上下文、工具输入、任务/协议/评估器/Profile、工件摘要及无循环输入快照。输出/HostObservation 已持久化仍不足以授权：最终宿主 tool-result 接缝须持久化成功的 HostSettlement。取消、超时、失败或缺失结算仍未完成。CLI JSON 导入不能产生这些宿主收据。

必要视角由有效 Profile 决定，具备来源、必要性和有界完成条件。Team 成员通过官方消息交换异议及反证，原始意见、讨论关联、修订意见和采纳/拒绝/未解决处置均可追踪。工件或相关输入变更使旧评审失效；跨任务、重放、改名自审以及作者/评审来源缺失不能授权当前声明。Team task complete 与 inbox 接受不等于评审有效。

分别报告执行独立性与语义质量。fresh/fork 和角色分离不保证认知独立或正确性；fork 不是盲审。策略允许时，同模型不同成员不自动无效。Team 当前共用进程/cwd，writeScopes 仅为提示，不能隔离工作树；BUGate 不承诺任务 owner 自动释放或消息自动重试/去重。

发送给 Node subprocess 的原生上下文由插件进程内持有的 Ed25519 私钥签名，配置 filesystem provider 持久化公钥 pin；公共 CLI 不提供 authority 注册。签名绑定操作、任务、工作区和精确上下文字节，恢复后重新验证持久化收据。同权限者替换 pin 或全部本地状态不在该信任边界内。

## Provider、受管工具与完成

在真实 filesystem/subprocess execution world 配置 `nodeExecutable`，必要时配置 `coreEntrypoint`；`projectRoot` 和 `taskPath` 绑定外部任务。opaque fs target key 不是宿主路径。Node 服务 IO 与采集走配置 subprocess provider；插件核验 provider 可见的 manifest，不支持的映射明确拒绝，不静默读取另一执行世界。持久化完成后工具才返回成功。

`executionCommands` 将稳定 `command_id` 映射到 argv、case IDs、report、可选 activity/environment 和 timeout。必需用例须通过当前范围/计划准入；辅助和诊断使用显式契约边界，建议不能成为可执行计划。`bugate_run_test` 接受配置命令 ID，不接受任意模型 argv。保留真实产品失败、基础设施错误、缺报告/零测试、skip、xfail、超时和取消。

异步 lifecycle 接缝完成准备后，同步 `context.text` 和 `tools.guard` 才消费状态。插件原样注入 canonical render，在模型调用前复核准入；恢复、子 Agent、Team 成员分别校验绑定。managed 模式约束声明的写入工具/命令触发，不能理解所有 shell 语义，也不替代 DSH 权限。advisory 省略受管写入准入，但核心评估及受管测试范围检查仍生效。

仅 `bugate_assess` 的 `submit: true` 启动完成返工；次数、时间和重复缺口有界，不无限循环追求一致。回合停止不等于声明满足。分别报告执行闭环、功能验收、阻塞和范围外建议。

CLI 输出单个 `bugate.io/v2` `CommandResult`。退出 0 表示调用完成，仍须查看 data：采集到真实产品失败也可以是已完成调用。退出 3 表示评估不满足或质量校验未通过，2 为输入无效，1 为运行时/基础设施错误，124 为超时，130 为取消。verdict、结构、语义评审和执行维度分别表达。

## 升级、中断恢复与卸载

```sh
"$BUGATE" setup deepseek-harness --package /absolute/path/new-bundle.tgz \
  --project /absolute/path/test-repo --dsh-profile bugate \
  --dsh /absolute/path/dsh --dsh-home /absolute/path/dsh-home --json
"$BUGATE" remove deepseek-harness --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/dsh \
  --dsh-home /absolute/path/dsh-home --rollback --json
```

`--rollback` 协调中断的自有操作或恢复留存的前一 Node 包。仅该显式路径恢复 dead-owner 锁，不抢占活跃进程。不加 `--rollback` 则只卸载自有配置/包状态，保留无关设置/插件、任务事实、失败历史、收据和恢复归档。不删除全局 Python、SUT 依赖或无关环境。失败时保留精确错误与 journal，不删除状态或盲目重试。

## 验证

参见[贡献指南](../CONTRIBUTING.zh-CN.md)与 [CI](../.github/workflows/ci.yml)。独立验收先将产物和 runner 复制出 checkout，再用操作系统策略禁止源码读取，并仅允许明确的 Node/宿主/必要工具执行。源码读取及绝对解释器执行拒绝探针，与安装、生命周期、已安装真实 DSH/Team 测试位于同一受限进程树。仅修改 PATH 不构成隔离。

记录精确包摘要、源码清单、Node/DSH/Cordis/Team/provider/平台版本、命令输出和实际结果。确定性 fixture 驱动真实成员、工具与临时命令，不证明 live-model 质量或效率；live-model 结果单独说明。本地宿主观察与摘要不抵抗同权限文件系统篡改。历史报告只适用于原始身份。
