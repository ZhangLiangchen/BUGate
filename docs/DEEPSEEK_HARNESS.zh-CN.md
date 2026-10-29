# BUGate for DeepSeek Harness

[English](DEEPSEEK_HARNESS.md) | 简体中文

原生适配器调用 BUGate Python 核心评估任务当前事实。DeepSeek Harness（DSH）负责
Agent 循环、工具执行和有界返工。任务工件保存在外部测试仓库。此路线是唯一当前产品入口。

## 版本与分发

| 组件 | 兼容目标 |
|---|---|
| Python 分发 | `bugate-core==2.1.0a1`，Python 3.11+ |
| Protocol | 精确 `2.1.0-dev.1` bundle 摘要，不自动迁移 |
| 原生包 | `@bugate/deepseek-harness@2.1.0-dev.1` |
| DSH | `0.2.2-alpha.2`，源码 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3` |
| Node | DSH 要求 24.9.0+ |

预构建 tarball 包含编译后 JavaScript、原生 bundle patch、canonical Schema/方法、
生成的 Skill、Python wheel 和带校验和清单的 wheelhouse。平台 wheel 的 Python ABI
与平台记录在 `python/inventory.json`；其他 ABI 应在目标平台重新构建。运行不需要
全局 editable install，也不依赖 BUGate 源码 checkout。

当前采集器和安装锁使用 POSIX 进程组及文件锁。本次在 macOS arm64 实测，新增 CI
面向 Linux；本包不支持 Windows 执行环境。

## 安装到明确项目与 profile

使用同一构建的 tarball、`install_dsh.py` 和 `SHA256SUMS`，先校验再安装。
DSH 本身须已安装为精确目标版本。以下示例路径需替换为选定的实际目录。

```sh
shasum -a 256 -c SHA256SUMS
python3 install_dsh.py \
  --package ./bugate-deepseek-harness-2.1.0-dev.1.tgz \
  --venv /absolute/path/bugate-bootstrap \
  --project /absolute/path/test-repo \
  --dsh-profile bugate \
  --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home
```

Python 依赖从 bundle 离线安装；DSH 原生插件管理器可能需要访问包注册表解析宿主
peer dependencies。Setup 调用真实 `dsh plugin --profile ... add ...` 并验证
`--dump-config`，保留无关配置，拒绝接管无本插件所有权记录的已有包。相同字节重复
安装幂等；升级保留旧包用于显式 rollback。Pending 操作须核对恢复，不能静默覆盖。
安装成功证明包与配置就绪；上下文注入仍须真实 Agent 会话证明。

## 初始化与使用任务

按 `protocol/v2/schemas/profile.schema.json` 编写 JSON Profile，绑定来源路径与
SHA-256、需求/风险 ID、交付标准及资源边界。来源路径相对外部项目；Profile 不保存
秘密，也不从聊天推断。

```sh
BUGATE=/absolute/path/bugate-bootstrap/bin/bugate
"$BUGATE" init --project /absolute/path/test-repo --task REQ-001 \
  --sut-profile /absolute/path/profile.json --json
"$BUGATE" protocol prepare --task /absolute/path/test-repo/test-task/REQ-001 \
  --max-bytes 8000 --json
"$BUGATE" status --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" validate --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" artifact record --task /absolute/path/test-repo/test-task/REQ-001 \
  --input .bugate/drafts/brief.json --json
"$BUGATE" assess --task /absolute/path/test-repo/test-task/REQ-001 \
  --claim claims/design.json --json
"$BUGATE" evidence record --task /absolute/path/test-repo/test-task/REQ-001 \
  --input evidence-input/import.json --json
```

任务拥有 `task.json`、`profile.json`、`.bugate/protocol.lock.json`、`scope/`、
`suggestions/`、`artifacts/`、`evidence/`、`claims/`、`assessments/` 和 `logs/`，
不可变请求收据与范围历史保存在 `.bugate/` 下。身份包含规范化项目/任务路径及
精确 Protocol/Profile 绑定；不同项目中相同 task id 不共享身份。多任务须明确选择
（`setup --task-path`），适配器不会猜测“最近任务”。

先建立有来源的业务命题和 Oracle、可测性、用例清单及绑定版本的语义评审，再声明设计
完成。工件 payload 与 Claim 类型以 canonical Schema 为准。`protocol prepare`
给出当前硬约束与可补足缺口。DSH 工具包括 `bugate_prepare`、`bugate_status`、
`bugate_validate`、`bugate_assess`、`bugate_record_artifact`、`bugate_record_evidence`
和 `bugate_run_test`。未计算摘要的草稿写入 `.bugate/drafts/`，由登记入口计算
canonical digest；更新已有工件使用 `--expected-digest` 防止误覆盖。摘要只是内容
完整性元数据，不代表语义已接受。

`bugate_run_test` 接受宿主配置的 command id，不接受模型任意 argv。宿主插件配置的
`executionCommands` 指定 argv、case IDs、report 路径及下文的必需/辅助活动分类。测试执行要求当前设计已满足；探索命令可以收集缺失事实。显式 CLI
采集入口见 `bugate execution run --help`。

## 范围创建、查看与变更

DSH 将允许的用户直接消息登记为不可变 `ScopeRequest` 收据。模型通过 `bugate_scope` 读取，再用 `bugate_record_scope` 登记契约草稿；模型工具不提供伪造收据入口。人工直接操作 CLI 时可用 `scope request` 登记符合 `scope_request.schema.json` 的任务内 JSON。此 CLI 是可信本地操作者输入边界，不防御控制同用户全部文件的恶意操作者。

```sh
"$BUGATE" scope show --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" scope record --task /absolute/path/test-repo/test-task/REQ-001 \
  --input .bugate/drafts/scope.json --json
"$BUGATE" scope check --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" suggestion record --task /absolute/path/test-repo/test-task/REQ-001 \
  --input .bugate/drafts/suggestion.json --json
"$BUGATE" suggestion list --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" execution authorize --task /absolute/path/test-repo/test-task/REQ-001 \
  --case CASE-1 --environment-id local-fixture --json
```

输入文件位于选定任务内。`scope record` 补身份和摘要元数据；草稿提供目标、来源原文引用、要求、流程、场景必要性、辅助边界、假设、完成规则和变更影响，精确字段以 `scope_contract.schema.json` 为准。`scope show` 展示当前契约、请求收据和建议；历史契约保存在不可变 `.bugate/scope-history/` 记录中。变更需要新的真实用户请求、递增版本、前序摘要、影响说明，以及带当前契约摘要的 `--expected-digest`。每个版本须保持锁定 Profile 需求/风险分母；分母变化返回 `SCOPE_PROFILE_CHANGE_REQUIRED`，需显式新 Profile 版本及 `init` 新任务，可将旧契约快照列为新 Profile source 保留追溯。原任务、证据和失败保留，不提供同任务 Profile rebind。用户收据证明选择，不能替代 Profile 领域证据。登记后重新评估 inventory 和声明，旧评估不能授权新范围。

`bugate_check_scope` 使用同一核心检查；`bugate_record_suggestion` 保存可选想法，不创建必需用例或执行计划。`executionCommands` 中，必需用例使用 `purpose: test` 和声明的 `caseIds`；辅助命令使用契约中已有 `activityId`，不填写验收用例 ID，`purpose` 可为 `auxiliary`、`diagnostic` 或 `exploration`。`environmentId` 必须在允许环境内。诊断还需要当前关联失败及剩余运行预算。缺少设计前提时，读取和分析仍可进行。所有命令继续服从宿主权限与 provider。

参见[四类范围示例](ACCEPTANCE_SCOPE.zh-CN.md)。默认不生成完整风险矩阵；明确要求的正常、边界、拒绝或权限场景仍为必需项，范围外建议不触发完成返工。

## 质量与证据语义

命令成功、结构有效、执行结果、声明结论与质量状态分别表达。Stdout 仅输出一个版本化
`CommandResult`，诊断走 stderr。`status` 和 `protocol prepare` 可成功返回质量缺口。

| 退出码 | 含义 |
|---|---|
| 0 | 调用完成，满足该命令成功条件 |
| 3 | `validate`/`assess` 已完成，质量 invalid/unsatisfied/inconclusive |
| 2 | 输入、绑定或范围不合法 |
| 1 | 运行、依赖或存储故障 |
| 124 | 超时，已尝试并报告受管进程终止 |
| 130 | 取消 |

导入、人工提交和模型自报材料不能靠填写字段升级为 `host_observed`。受管采集记录真实
argv、身份、时间、结果、有界日志、JUnit 用例及相关输入摘要，并在工具返回前完成证据
持久化。哈希检测字节变化，不能证明已有日志属实，也不能防御控制全部同权限本地文件
的操作者。

声明具有范围：业务理解不要求未来执行；需求验证需要当前 Oracle 关联的实际用例观察。
零用例、全 skip/xfail、没有执行均不能证明需求通过。真实产品失败在证据、诊断与约定
报告完整时可以完成测试交付。评估只适用于固定声明与输入快照，事实变化使其失效。
普通评估不依赖 Memory 服务。

## 受管范围与恢复

默认受管工具为 DSH `write`、`edit`、`str_replace_editor`、`bash` 和
`bugate_run_test`。读取、任务内分析和声明材料允许继续；implementation/tests 路径
要求当前设计已接受。任意 shell 写入被拒绝，探索通过显式配置命令进行。其他外部工具
或 backend 不属于通用文件系统沙箱保证；访问权限仍由 DSH 权限系统及 runtime
provider 负责。

异步准备发生在可等待生命周期；动态 context 和最终 guard 为同步接口。适配器原样
注入 canonical `render.content`，关闭插值，失败时清除旧上下文。可等待的
`agent/request` hook 在请求组装后刷新，`llm/stream` 检查在模型执行前拒绝已失效的
上下文。子 Agent 独立解析
任务绑定；持久 session 绑定检查拒绝冷恢复时摘要漂移。只有显式提交完成声明后才发生
返工，并受次数、时间、无进展和取消约束。

文件目标为 opaque。适配器使用配置的 fs/subprocess provider 和 `processPath`；
安装的 Python/资源必须有 execution-world 映射。缺失映射返回 capability error，
不会在浏览器调用 Python，也不会退回另一台机器的磁盘。
核心提供有大小上限的文件系统清单，适配器通过 filesystem provider 核对任务文件、
目录成员和 Profile 来源字节，再接受 subprocess 结果。仅锁文件相同，不能证明两个
执行环境中的工件相同。
清单上限为 2,048 个文件、2,048 个目录和 32 MiB 文件内容，超限明确失败。排除
写入锁和无事实权威的 `.bugate/runtime/` 记账目录，草稿、声明与证据仍在核对范围内。
DSH session pin 及其原子临时文件位于该目录，受管模型写入不能修改它。

```sh
"$BUGATE" doctor integrations --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home --json
"$BUGATE" remove deepseek-harness --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home --rollback --json
```

移除 `--rollback` 即为卸载。保留任务事实、无关插件/配置与恢复包。Doctor 分别报告
宿主存在、插件/资源完整性、投影新鲜度、任务绑定及 hydration 观察；未选择且缺失的
宿主非致命，已选择且损坏的集成失败。

## 开发与验证证据

在独立 Python 环境安装本项目；进入 `adapters/deepseek-harness/plugin` 运行
`npm run build`，随后运行 `python packaging/build_dsh.py --out-dir dist/scope-convergence`。

验收区分 Python 契约、真实 DSH 加确定性模型 fixture，以及可选 live-model 测试。
确定性 fixture 驱动真实 DSH Agent loop 不等于 DeepSeek 模型效果评测。精确命令、
产物摘要、已采纳需求/Oracle 验证和适用回归结果及真实缺口以交付报告为准，不能由工具注册成功推断。


## 本次范围与升级边界

当前仅交付 v2 + DSH；旧宿主自动入口、安装器与格式已退役，不支持 v0.x 自动迁移。
当前验收以[实现合同](reports/scope-convergence/CONTRACT.zh-CN.md)与[采纳验证映射](reports/scope-convergence/ACCEPTANCE_MATRIX.zh-CN.md)为准。此前 v2-only 报告作为历史记录保留；CI 配置不证明未执行平台通过，也不证明 live-model 效果。
支持 v2 bundle 安装升级和按留存安装归档 rollback；core 或协议摘要变化不会静默迁移现有任务，应按当前 v2 输入建立新任务。

范围变更后，本版本要求针对新契约重新执行；旧证据和失败继续作为历史保留。不实现选择性复用，即使部分要求字段未变也不恢复旧授权。
