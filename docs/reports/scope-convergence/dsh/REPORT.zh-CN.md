# DSH 真实运行时验证

[English](REPORT.md) | 简体中文

下列有限范围集成及既有宿主边界验证通过。本结论仅覆盖本次约定范围：新的范围/计划/
证据/完成机制及其真实 DSH 接入，使用临时通用 fixture；不代表产品整体安全、所有风险
覆盖或 live model 的业务分析质量。

最终已安装包来自源码提交 `c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60`：

- 原生包：`bugate-deepseek-harness-2.1.0-dev.1.tgz`。
- SHA256：`4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`。
- Python 核心 `2.1.0a1`，协议 `2.1.0-dev.1`。
- 真实 DSH `0.2.2-alpha.2`，已核验官方源码提交
  `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`，通过已有本地 checkout 的
  [Git 与接口探针](source-interface-probes.json) 实测确认。实际执行加载发布的 npm 包，
  其源码/类型摘要另存于[环境证据](environment-and-interfaces.json)。
- 模型边界为确定性 fixture，**未运行 live model**。

| 证据组 | 实际结果 | 精确来源边界 |
|---|---|---|
| 完整真实宿主回归 | 24 通过，0 失败/跳过；exit 0；307.986 秒。 | [源码摘要](runtime-source-manifest.json)、[结果](runtime-result.json)、[日志](runtime-final.log)。 |
| 最终具名需求评审修复后 | 2 个受影响测试通过，0 失败/跳过；exit 0；204.564 秒。 | [最终源码摘要](runtime-source-manifest-after-named-scope-fix.json)、[结果](runtime-after-named-scope-fix-result.json)、[日志](runtime-after-named-scope-fix.log)。 |
| 最终已安装包、拒绝读取源码仓库 | 1 个集成测试包含两条完整流程，通过；exit 0；176.166 秒。 | [精确包与运行结果](installed-runtime-result.json)、[测试日志](installed-runtime-final.log)、[子进程日志](installed-runtime-child.log)。 |

这些证据组分别记录。完整 24 项运行发生在最终具名需求评估的小修之前；之后针对最终
源码重跑受影响的 authoring/convergence 路径，再在精确最终安装包中运行两条流程。
没有把旧包结果当作最终产物验证。

已安装运行使用官方 DSH 服务注册、AgentLoop、模型/工具流水线、配置的本地 filesystem/
subprocess provider、JSONL 持久化和官方 profile 模块解析，并断言宿主与已安装插件解析到
同一个 DSH LLM 模块。Python 从独立已安装 venv 加载。加载插件前，macOS `sandbox-exec`
对源码仓库 `pyproject.toml` 的实际读取返回 EPERM/EACCES；同一系统规则在整个运行期间
拒绝读取整个 checkout。实际解释器路径、命令参数、环境变量和模块身份保存在安装结果中。
原始 DSH 会话日志保留在有限的[会话记录清单](installed-session-traces/index.json)。

| 已安装流程 | 模型请求 | 必需用例结果 | 功能验证 | 验收执行完成 | 建议 | 诊断 |
|---|---:|---|---|---|---:|---|
| 明确要求的正整数与负整数分支 | 47 | 2 通过 | satisfied | satisfied | 4 条，非阻塞 | 不需要。 |
| 相同分支，临时 SUT 故意加入缺陷 | 51 | 1 通过、1 失败 | unsatisfied | satisfied | 4 条，非阻塞 | 2 次有界辅助观测，第 3 次在执行前拒绝。 |

两条流程中，模型均主动尝试登记与目标无关的新增场景。核心保留已采纳 inventory，只记录
建议描述；该新增用例的配置命令在进程启动前被拒绝，其 marker 文件不存在。之后模型执行
真实范围内用例，工具返回后立即读回已持久化证据。验收执行完成之后继续增加建议，原声明
字节和授权快照不变，两份不可变评估均满足，没有触发完成返工。参见[通过流程证明](installed-passing/suggestion-reassessment-proof.json)
与[失败流程证明](installed-failure/suggestion-reassessment-proof.json)，同目录保留原始 JUnit、stdout、
evidence 及校验和。

已安装流程映射正式要求 F-01/F-02/F-04/F-05/F-07；完整源码 runtime 回归还覆盖 F-03/F-06：
50 次真实 turn 与实际压缩、冷恢复、spawn/fork/独立 preset、子 Agent 授权来源、并行任务
隔离、任务租约、模型准入竞态、PTC 调用链、证据屏障、取消/卸载、超时、路径/运行环境边界，
以及设计尚未完整时只读需求来源。F-08 由本双语报告和精确证据/产物核对支持。历史 SC 标签
不作为数量门槛。

第一轮 runtime 候选运行暴露了建议与不可变评估存储的真实冲突；修复后重跑，未放宽原有
完成断言。[运行时发现](RUNTIME_FINDINGS.zh-CN.md) 保留该失败与独立 CLI 复现。最终源码和
已安装包运行均验证了修复。

实际强制边界是已识别的受管写工具和宿主配置命令，由核心检查范围/计划/环境/活动身份；
适配器不会声称能从任意 shell 文本识别全部业务语义。其他宿主工具仍由宿主策略约束。
本地哈希和收据不抵抗同权限文件系统控制者。场景必要性评审是有来源的判断，与确定性引用
检查和真实执行观测分开。本次约定验证范围内未发现剩余 runtime 阻塞项。
