# BUGate v2

[English](README.md) | 简体中文

BUGate 是证据优先的测试质量协议与 Python 评估核心。**DeepSeek Harness 是唯一产品宿主。** BUGate 评估业务理解、可测性、Oracle/用例追踪及限定范围的完成声明；DSH 执行工具并管理 Agent 生命周期。

当前源码交付 `bugate-core 2.1.0a1` 和 `@bugate/deepseek-harness 2.1.0-dev.1`，绑定 Protocol `2.1.0-dev.1` 的精确摘要。本次是破坏性预发布：旧 hooks、安装器、适配器和产物格式已退役。不支持从 v0.x 或重构前任务自动迁移。已有 release/tag 保留，本次构建不覆盖它们。

验收范围契约固定用户的有限目标和必需分支，范围内必须提供充分业务证据。额外想法保持为不阻塞完成的建议，只有真实用户请求改变契约后才能纳入。验收执行完成与功能验收通过分别报告，见[范围说明与示例](docs/ACCEPTANCE_SCOPE.zh-CN.md)。

## 安装与使用

使用同一次新构建的 bundle、bootstrap 安装器和校验和。按[安装使用指南](docs/DEEPSEEK_HARNESS.zh-CN.md)操作，或把[一键安装 Prompt](INSTALL_DSH_PROMPT.zh-CN.md)交给 Agent。安装目标必须是明确的外部测试仓库与 DSH profile，无需从 BUGate checkout 复制运行文件。

兼容目标为 DSH `0.2.2-alpha.2`、Node `24.9.0+`、Python `3.11+` 和本地 POSIX 执行 provider。实际平台验证和 wheel ABI 随构建记录，不推断未测平台支持。不支持 Windows。Python 依赖从 bundle 离线安装；DSH 插件管理器解析宿主 peer dependencies 时可能需要访问 registry。

```sh
bugate init --project /absolute/test-repo --task TASK-1 --sut-profile /absolute/profile.json --json
bugate protocol prepare --task /absolute/test-repo/test-task/TASK-1 --json
bugate validate --task /absolute/test-repo/test-task/TASK-1 --json
bugate assess --task /absolute/test-repo/test-task/TASK-1 --claim claims/design.json --json
```

退出码零、模型自报通过或内容哈希不能证明需求已验证。完整测试交付可以包含真实产品失败。声明必须针对当前精确输入快照和有来源的 Oracle 评估。不依赖记忆服务。

## 文档

- [能力与边界](CAPABILITIES.zh-CN.md)
- [方法](docs/qa-methodology/METHOD.zh-CN.md)与[执行流程](docs/qa-methodology/SOP.zh-CN.md)
- [协议架构](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)
- [贡献者设置](CONTRIBUTING.zh-CN.md)与[开发 Prompt](INIT.zh-CN.md)
- [本次实现合同](docs/reports/scope-convergence/CONTRACT.zh-CN.md)与[验收矩阵](docs/reports/scope-convergence/ACCEPTANCE_MATRIX.zh-CN.md)
- [范围架构决策](docs/reports/scope-convergence/ARCHITECTURE.zh-CN.md)
- [此前 v2-only 交付](docs/reports/v2-dsh-only/DELIVERY.zh-CN.md)

`protocol/v2/` 是唯一规范来源，`src/bugate/` 负责评估，`adapters/deepseek-harness/` 提供原生插件，`packaging/` 构建有限清单的安装包。真实 SUT 数据与秘密留在仓库外。

[MIT 许可](LICENSE)。
