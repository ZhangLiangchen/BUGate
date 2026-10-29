# DSH v2.0.0-dev.2

[English](dsh-v2.0.0-dev.2.md) | 简体中文

本次是破坏性 v2-only 源码与本地构建交付；本文不发布或替换 GitHub release。Core 为 `bugate-core 2.0.0a2`，DSH 目标为 `0.2.2-alpha.2`，Protocol 为 `2.0.0` 并绑定精确内容摘要。

当前产品只含 Python v2 质量核心、canonical 协议资源、统一 CLI 和 DeepSeek Harness 原生适配器。旧 hooks、适配器、角色/记忆/编排运行时和旧 installer/updater 从当前源码及分发移除，历史与公开 tag 不变。原生业务/可测性/Oracle/引用/建模检查替代旧语义运行依赖，旧输入明确拒绝。

新 bundle 包含编译后的原生代码、生成的方法投影、canonical 资源、Python wheel 和离线依赖 wheelhouse。构建校验有限资源清单、wheel 唯一入口和嵌套 bundle 成员，随包提供校验和与源码摘要清单。

使用[安装指南](../DEEPSEEK_HARNESS.zh-CN.md)及[安装 Prompt](../../INSTALL_DSH_PROMPT.zh-CN.md)。V2 安装升级/rollback 不迁移任务绑定；协议/评估器变更需要当前输入和新的显式任务，不支持 v0.x 自动迁移。本地验证目标为 macOS arm64；Linux 配置了 CI，但不冒称本次本地运行了 Linux。不支持 Windows。Wheel ABI/平台兼容信息随包记录。

实际结果和证明边界见[交付报告](../reports/v2-dsh-only/DELIVERY.zh-CN.md)及[验收矩阵](../reports/v2-dsh-only/acceptance.json)。确定性模型 fixture 驱动真实 DSH runtime，不是 live-model 效果证据。
