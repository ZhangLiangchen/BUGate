# BUGate 贡献者协议

[English](AGENTS.md) | 简体中文

BUGate 定义测试质量并评估事实。DeepSeek Harness（DSH）是唯一产品宿主，负责执行、调度、Agent 生命周期和有界返工。使用其他开发工具不代表产品支持该工具。

## 权威与目录

- `protocol/v2/` 是唯一机器规范：Schema、MethodSpec、规则和 reason codes。
- 本 TypeScript 分支的 `packages/core/` 是共享评估核心，`packages/cli/` 是统一 Node CLI；独立 Python 路线保留自己的 Python 核心/CLI 目录。
- `adapters/deepseek-harness/` 保存原生适配器与真实宿主验收。
- `views/skills/bugate/` 是精简入口；构建追加 canonical MethodSpec 并校验摘要。
- `packaging/` 保存明确资源清单与独立安装器。
- `docs/qa-methodology/` 解释协议，不建立并行规则。

非平凡修改前阅读 [METHOD](docs/qa-methodology/METHOD.zh-CN.md)、[SOP](docs/qa-methodology/SOP.zh-CN.md) 和[协议指南](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)。保留未提交工作与其他写者修改；委派时明确文件所有权。把决策、发现和命令存为可审查任务产物，不依赖记忆服务或 hook。

## 工程不变量

2026-10-30 的[分支决策](docs/plans/PYTHON_FIELD_TRIAL.zh-CN.md)明确 Python 改进版与全面 TypeScript 版在独立分支/worktree 开发。先用经过验证的 Python 改进版做一轮实战，TS 独立继续；保留两线历史和未提交工作，分开产物、安装目标、任务与证据。TS 验收通过不授权替换 Python 实战安装。

本分支遵循已接受的 [TypeScript/Cordis 路线](docs/plans/TYPESCRIPT_CORDIS.zh-CN.md)：共享 TypeScript 核心、统一 Node CLI 与 Cordis 原生服务/插件，DSH 保持唯一宿主。2026-10-30 执行请求已授权实施，协议/评估器版本为 `2.2.0-dev.1`；精确产物完成 runtime 和独立安装验收后才切换。迁移前阅读计划；历史 Python 发布和测试数量不构成当前授权或通用门槛。实现语言变化须保持下述不变量，并使用实际配置的 execution world。

Core 保持 SUT-neutral。真实产品源码、API、秘密、环境、fixture 与现场证据留在外部测试工作区；本仓测试动态生成通用临时 fixture。领域事实必须绑定 Profile 声明的证据来源。

受管测试实现前建立有来源的业务命题与可观测 Oracle，同时验证结构和业务语义。占位内容、未解决的阻断缺口和延后探针不能放行实现。锁定 Profile 固定任务内每个范围版本的需求与风险覆盖分母。用户明确授权在同分母内调整必要分支或 Oracle 时，新建 ScopeContract 版本；改变分母须显式建立新 Profile 版本并通过 init 新建任务，保留原任务、失败和历史。ScopeRequest 证明用户选择，不替代 Profile 声明的领域证据。建议不进入必需覆盖或完成阻塞。

模型填写通过字段与内容哈希不证明真实执行。旧评估不能授权变更后的声明或快照。保留真实产品失败、skip、xfail 和未观测状态，不为全绿放宽断言。区分基础设施缺陷和产品缺陷、假设和确认发现。断言精确错误并提供来源，文件路径通过项目 helper 解析。

DSH 同步 context/guard 只使用已完成快照；异步准备和证据持久化在实际可等待生命周期接缝完成。不假设子 Agent 继承 guard，不把 opaque filesystem key 当宿主路径；使用配置的 filesystem/subprocess execution world。

不恢复退役 hooks、其他宿主适配器、旧格式转换、安装兼容、角色守护进程或调度循环。Git 历史与公开 release 保留为历史记录。v0.x 与重构前任务格式明确拒绝。

框架文档保持英中镜像和语言链接。完成交付前执行适用测试、源码/产物边界检查和真实 DSH 验收。测试存在不代表已运行；确定性模型驱动的真实 runtime 与 live-model 结果分开报告。未经显式授权不发布或覆盖 release。
