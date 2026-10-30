# BUGate v2 协议架构

[English](BUGATE_2_0_PROTOCOL_GUIDE.md) | 简体中文

本文描述已实现的 v2-only 产品。此前迁移和多宿主计划由 [2026-10-29 决策](../../CHARTER.zh-CN.md)退役。

| 边界 | 权威职责 |
|---|---|
| `protocol/v2/` | canonical Schema、MethodSpec、质量规则和 reason codes。 |
| `packages/core/` | 确定性校验、语义评审要求、快照、证据与声明评估。 |
| 统一 CLI | 版本化 JSON CommandResult 与稳定退出语义。 |
| DSH 原生适配器 | 宿主服务、精确绑定、原样上下文、受管工具和有界返工。 |
| `packaging/` | 有限资源清单、有限 Node bundle 和有所有权的安装生命周期。 |

`ProtocolBinding` 锁定精确协议与 Profile；`TestTask` 标识规范化项目/任务范围；`Artifact`、`Evidence`、`Claim` 保存内容、来源与适用性。`AssessmentRequest` 绑定固定声明和输入快照；`AssessmentResult` 保存确定性/语义/执行维度与 findings；`PreparedProtocolContext` 为宿主渲染当前约束和缺口。字段与类型以 canonical Schema 为准。

授权快照排除派生评估、草稿、建议、未引用用户收据与事务记账；请求摘要额外覆盖声明、绑定、评估器与参数。请求中的 `advisory_digest` 使建议变化后可保存独立评估记录，但不使既有声明或执行失效。对象摘要排除自身摘要字段。内容变化或并发提交冲突使旧授权失效。核心不拥有 Agent loop、current-stage、调度器或必需长期记忆服务。

版本化范围契约引用用户请求证据、有限目标及已有要求/Oracle 事实，inventory 用例绑定其当前版本和摘要。建议与必需覆盖、完成阻塞隔离，辅助活动具有明确边界，见[范围语义](../ACCEPTANCE_SCOPE.zh-CN.md)。

原生 v2 语义覆盖有来源命题、可观测 Oracle、可测性、固定范围分母覆盖、明确要求的负例、实现引用、评审新鲜度及 Profile 要求的可选建模。旧产物格式明确拒绝，不运行转换器或降级 checker。

只有受管采集器创建宿主观测；导入/模型材料保留来源。原子写、锁和持久化屏障保障一致性，不认证控制同权限全部文件的恶意行为。真实产品失败可以完成测试交付，而失败需求仍未验证。

DSH `context.text` 与 `tools.guard` 保持同步；准备和证据持久化使用可等待生命周期/工具接缝。适配器校验并原样注入核心 `render.content`；子 Agent 和恢复会话独立验证精确绑定。文件与执行使用配置的 provider/execution-world 映射，opaque key 不当路径。实现前提缺失时仍允许读取、分析和合法探索。

文档和投影不复制机器权威。原生构建向 Skill 入口追加 canonical MethodSpec 并记录协议/内容摘要；打包校验共享核心与原生资源一致。参见[安装](../DEEPSEEK_HARNESS.zh-CN.md)、[方法](METHOD.zh-CN.md)及[当前文档与历史记录](../README.zh-CN.md)。

官方 DSH Agent Team 按 Profile 声明的必要视角执行互审。宿主捕获作者、评审者、Team 成员、执行及输入上下文；fresh/fork 和执行分离是可观察事实，不保证认知独立。评审须具备持久化宿主观察、成功的最终工具结算、当前精确工件/输入绑定和有来源的语义内容。Team 任务完成和消息接受不等于评审有效。异议及处置保留可追溯关系；导入 JSON 和 reviewer 名称不能制造宿主来源。
