# 使用 BUGate v2

[English](USING-BUGATE.md) | 简体中文

安装、命令、配置、诊断、升级/rollback 与卸载见 [DSH 原生指南](DEEPSEEK_HARNESS.zh-CN.md)。证据优先测试按[任务流程](qa-methodology/SOP.zh-CN.md)执行。当前产品只接收 v2 输入，历史导入说明不是本版本安装路径。共享 TypeScript 核心和统一 Node CLI 是产品运行时；DSH 及其官方 Agent Team 负责 Agent 执行与协作。本版本使用新的当前任务绑定；`bugate history inspect --task PATH` 只读旧身份，不转换或延续授权。

根据用户请求明确有限[验收范围](ACCEPTANCE_SCOPE.zh-CN.md)，列明必需场景和辅助活动边界。建议不自动扩展计划或阻塞完成，完整执行报告可以保留真实功能失败。
