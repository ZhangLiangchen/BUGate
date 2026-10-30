# 文档索引

[English](README.md) | 简体中文

BUGate 使用共享 TypeScript 质量评估核心与版本化协议，DeepSeek Harness 是唯一产品宿主。
实施与精确产物验证进行中。请从下列现行指南开始；唯一机器权威是
[Protocol v2](../protocol/v2/manifest.yaml)，交付报告或历史验收矩阵不建立额外规则。

## 当前指南

| 用途 | 文档 |
|---|---|
| 产品边界与贡献规则 | [章程](../CHARTER.zh-CN.md)、[AGENTS](../AGENTS.zh-CN.md)、[贡献指南](../CONTRIBUTING.zh-CN.md) |
| 安装、配置与恢复 | [DSH 指南](DEEPSEEK_HARNESS.zh-CN.md)、[一键安装 Prompt](../INSTALL_DSH_PROMPT.zh-CN.md) |
| 有限验收范围与结果解释 | [范围契约与示例](ACCEPTANCE_SCOPE.zh-CN.md) |
| 方法与任务流程 | [METHOD](qa-methodology/METHOD.zh-CN.md)、[SOP](qa-methodology/SOP.zh-CN.md) |
| 核心与宿主职责 | [协议架构](qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)、[适配器契约](../adapters/CONFORMANCE.zh-CN.md) |
| 原生适配器配置与验证 | [适配器](../adapters/deepseek-harness/README.zh-CN.md)、[runtime 测试说明](../adapters/deepseek-harness/tests/README.zh-CN.md) |

架构指南保留 `BUGATE_2_0_PROTOCOL_GUIDE` 文件名以维持已有链接，正文描述当前
v2 架构；精确组件版本以协议 manifest 和分发元数据为准。

## 实施与证据

[TypeScript/Cordis 计划](plans/TYPESCRIPT_CORDIS.zh-CN.md)记录已授权实施和切换条件。Node 实现版本为 `2.2.0-dev.1`；候选包安装通过不能证明最终 Team runtime 验收。

## 验证记录

以下记录是特定版本的观察与决策，不是额外的长期规则。请同时核对提交、产物摘要、
命令结果与证明边界；某个包的通过结果不能当作另一包的验证证据。

- [2.1.0-dev.1 发布说明](releases/DSH_2_1_0_DEV_1.zh-CN.md)与
  [文档审计](reports/release-2.1-dev.1/DOCUMENTATION_AUDIT.zh-CN.md)说明公开发布构建及发布前修正。
- [范围收敛交付](reports/scope-convergence/DELIVERY.zh-CN.md)、
  [采纳验证映射](reports/scope-convergence/ACCEPTANCE_MATRIX.zh-CN.md)与
  [来源纠正](reports/scope-convergence/SOURCE_CORRECTION.zh-CN.md)保留此前实现及验证快照。
  其中 SC 示例不是通用必测清单，原始产物摘要保持不变。
- [2.0.0-dev.2 发布说明](releases/dsh-v2.0.0-dev.2.zh-CN.md)与
  [v2-only 交付](reports/v2-dsh-only/DELIVERY.zh-CN.md)记录该历史版本，旧组件版本和退役验收编号仅适用于当时。

历史迁移、多宿主或 legacy runtime 讨论不授权恢复这些产品路径。新工作遵循现行
协议及用户实际目标，保持有限验收范围，并分别报告真实执行、功能结果、阻塞与可选建议。
