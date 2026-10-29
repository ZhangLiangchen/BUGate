# DSH v2.0.0-dev.2

[English](dsh-v2.0.0-dev.2.md) | 简体中文

本次是破坏性 v2-only 预发布。Core 为 `bugate-core 2.0.0a2`，DSH 目标为 `0.2.2-alpha.2`，Protocol 为 `2.0.0` 并绑定精确内容摘要。

当前产品只含 Python v2 质量核心、canonical 协议资源、统一 CLI 和 DeepSeek Harness 原生适配器。旧 hooks、适配器、角色/记忆/编排运行时和旧 installer/updater 从当前源码及分发移除，历史与公开 tag 不变。原生业务/可测性/Oracle/引用/建模检查替代旧语义运行依赖，旧输入明确拒绝。

新 bundle 包含编译后的原生代码、生成的方法投影、canonical 资源、Python wheel 和离线依赖 wheelhouse。构建校验有限资源清单、wheel 唯一入口和嵌套 bundle 成员，随包提供校验和与源码摘要清单。

从 [GitHub 预发布](https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.0.0-dev.2)下载本版本。六个文件保持同目录，以便完整核验校验和清单：

- [原生 bundle](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/bugate-deepseek-harness-2.0.0-dev.2.tgz)
- [Python core wheel](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/bugate_core-2.0.0a2-py3-none-any.whl)
- [Bootstrap 安装器](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/install_dsh.py)
- [构建清单](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/build-manifest.json)
- [源码输入摘要清单](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/source-manifest.json)
- [SHA256SUMS](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/SHA256SUMS)

Bundle SHA-256：`1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0`。
使用[安装指南](../DEEPSEEK_HARNESS.zh-CN.md)及[release 安装 Prompt](../../INSTALL_DSH_PROMPT.zh-CN.md)。V2 安装升级/rollback 不迁移任务绑定；协议/评估器变更需要当前输入和新的显式任务，不支持 v0.x 自动迁移。本地验证目标为 macOS arm64；正式创建预发布前须确认对应发布提交的 GitHub CI 通过。不支持 Windows。Wheel ABI/平台兼容信息随包记录。

本地实际通过：96 项核心测试、4 项分发测试、15 项安装边界测试、22 项真实 DSH runtime 测试、19 步干净安装生命周期及 9 步真实已发布 v2 升级/回滚。操作系统禁止读取源码仓库时，独立安装及安装包 runtime 也通过：27 次模型请求、2 条实际测试和当前 satisfied 需求声明。48 项适用 HC/AC 及新增 3 项无 legacy 检查通过；AC-33 明确退役，不计为通过。

实际结果和证明边界见[交付报告](../reports/v2-dsh-only/DELIVERY.zh-CN.md)及[验收矩阵](../reports/v2-dsh-only/acceptance.json)。确定性模型 fixture 驱动真实 DSH runtime，未运行 live-model 测试。以上本地证据不替代 GitHub CI 和公开下载产物字节核对；两者属于独立发布检查。
