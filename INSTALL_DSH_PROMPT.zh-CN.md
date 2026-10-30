# 安装已验证的 Node DSH 产物

[English](INSTALL_DSH_PROMPT.md) | 简体中文

本 Prompt 安装本地交付的精确 `2.2.0-dev.1` Node 产物，不假设存在新公开 release/tag。使用对应完整交付报告中的产物目录及摘要；不能因为版本字符串相同就替换为开发候选包。

```text
从交付报告标识的精确产物目录安装 BUGate 原生 DeepSeek Harness 2.2.0-dev.1。
不用 latest、历史 Python release，不重建源码，也不推断其他安装目标。

要求以下文件来自同一次构建：
- bugate-deepseek-harness-2.2.0-dev.1.tgz
- install.mjs
- lifecycle.mjs
- archive.mjs
- package.json
- source-manifest.json
- build-manifest.json
- SHA256SUMS

验证全部 SHA256SUMS，并将 tarball 摘要与精确交付报告对照，不只看文件名。
缺文件、摘要不符，或交付为 INCOMPLETE/BLOCKED 且未通过已安装 runtime 验收时停止。
检查 build-manifest.json 中的源码、依赖和平台身份。
产品需要 Node >=24.9.0、DSH 0.2.2-alpha.2 与 pnpm，不需要 Python 或 BUGate checkout。
DSH 宿主依赖可能需要 registry。

仅使用用户明确选择的外部测试项目、DSH profile、DSH home 和 DSH 可执行文件。
只询问缺失的目标身份，不从历史聊天猜测。保留无关 profile、插件、凭据和任务事实。
验证安装使用隔离目标，不替换正在使用的真实 profile。

安全引用明确路径后执行：
node ARTIFACT_DIR/install.mjs --package
ARTIFACT_DIR/bugate-deepseek-harness-2.2.0-dev.1.tgz --project TEST_PROJECT
--dsh-profile PROFILE --dsh DSH_EXECUTABLE --dsh-home DSH_HOME。
然后使用 DSH_HOME/profiles/PROFILE/node_modules/.bin/bugate
执行 doctor integrations，传相同目标选项和 --json。

安装包组合官方 Agent Team 原始 profile 层与 BUGate，不重复添加 Team profile 层。
分别报告包/配置健康、真实 DSH Agent 会话和 Team 评审结果。
确定性 runtime fixture 不证明 live-model 效果或认知独立。

仅使用预期的当前有来源 Profile 初始化新任务。旧协议/评估器绑定不能授权新实现。
保留旧范围、失败、证据与只读历史，不转换，不安装 Python fallback。
必要视角由 Profile 决定，不固定角色/模型数量。必须具备真实 Team 宿主来源、
当前精确输入绑定、持久化输出和成功最终结算；名称、模型自报、消息接受或
Team task complete 均不证明评审通过。

任务内 Profile 覆盖分母固定；分母变化需要显式新 Profile 版本和 init 新任务。
ScopeRequest 证明用户选择，不替代领域证据。建议不能变成必需执行或完成阻塞。
分别报告实际范围、执行闭环、功能结果、阻塞和建议。

失败时保留精确错误及 pending-operation journal，不删除用户状态或盲目重试。
仅对明确的自有恢复目标使用统一 CLI remove deepseek-harness --rollback。
不发布/覆盖 release/tag，不清理无关 Python/SUT 环境。
```

参见[安装与恢复](docs/DEEPSEEK_HARNESS.zh-CN.md)和[范围示例](docs/ACCEPTANCE_SCOPE.zh-CN.md)。
