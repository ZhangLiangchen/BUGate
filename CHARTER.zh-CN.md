# BUGate 章程

[English](CHARTER.md) | 简体中文

BUGate 是 SUT-neutral 的测试质量协议，不是执行编排器。共享 TypeScript 核心评估版本化事实，唯一产品宿主是原生 DeepSeek Harness。

已接受的架构为共享 TypeScript 评估核心、统一 Node CLI 和 Cordis 原生服务/插件。官方 DSH Agent Team 拥有评审成员、任务板、消息和生命周期；BUGate 将持久化宿主观察绑定到评审输出并评估适用性。2026-10-30 请求已授权实施，精确产物验收完成后才切换，见 [TypeScript/Cordis 计划](docs/plans/TYPESCRIPT_CORDIS.zh-CN.md)。这不转移 Agent loop 职责，也不限制外部 SUT 测试语言。

2026-10-29 范围决策退役此前兼容、多宿主、角色守护进程、记忆运行时和旧导入安装器要求。Git 历史和已发布 tag/release 保留为历史资料。当前产品不含旧格式转换器或自动迁移。

质量不变量继续有效：有来源的业务理解、可测性、Oracle/用例追踪、快照时效、证据来源、真实失败分类和限定范围的完成声明。DSH 负责调度与生命周期，不引入可编辑 current-stage 真相源或强制记忆服务。

验收广度由用户目标和采纳要求决定，范围内证据深度必须充分。第一等范围契约固定目标；范围外建议未经有来源的用户变更，不能成为执行或完成义务。执行完成与功能验收保持为两个独立结论。
