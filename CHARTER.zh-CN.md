# BUGate 章程

[English](CHARTER.md) | 简体中文

BUGate 是 SUT-neutral 的测试质量协议，不是执行编排器。当前已发布的 Python 核心评估版本化事实；唯一产品适配器是 DeepSeek Harness 原生插件。实际测试任务位于外部测试仓库，具有显式 Profile 与精确协议绑定。Core 开发不在本仓挂载真实 SUT。

已接受的下一步架构为全面 TypeScript：共享的 SUT-neutral 评估核心、统一 Node CLI，以及 DSH 内的 Cordis 原生服务/插件。现有原生插件将退出 Python 桥接。这是用户已接受的决策，实施处于**计划中、尚未开始**；现有发布及 Python 安装保持不变。阶段、Oracle 和切换边界见 [TypeScript/Cordis 开发计划](docs/plans/TYPESCRIPT_CORDIS.zh-CN.md)。目标不改变 DSH execution-world provider 边界，不转移 Agent loop 职责，也不限制外部 SUT 测试语言。

2026-10-29 范围决策退役此前兼容、多宿主、角色守护进程、记忆运行时和旧导入安装器要求。Git 历史和已发布 tag/release 保留为历史资料。当前产品不含旧格式转换器或自动迁移。

质量不变量继续有效：有来源的业务理解、可测性、Oracle/用例追踪、快照时效、证据来源、真实失败分类和限定范围的完成声明。DSH 负责调度与生命周期，不引入可编辑 current-stage 真相源或强制记忆服务。

验收广度由用户目标和采纳要求决定，范围内证据深度必须充分。第一等范围契约固定目标；范围外建议未经有来源的用户变更，不能成为执行或完成义务。执行完成与功能验收保持为两个独立结论。
