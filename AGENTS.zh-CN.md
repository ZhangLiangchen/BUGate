# BUGate 贡献者协议

[English](AGENTS.md) | 简体中文

BUGate 定义测试质量并评估事实。DeepSeek Harness（DSH）是唯一产品宿主，负责执行、调度、Agent 生命周期和有界返工。使用其他开发工具不代表产品支持该工具。

## 权威与目录

- `protocol/v2/` 是唯一机器规范：Schema、MethodSpec、规则和 reason codes。
- `src/bugate/` 是宿主无关 Python 核心与统一 CLI。
- `adapters/deepseek-harness/` 保存原生适配器与真实宿主验收。
- `views/skills/bugate/` 是精简入口；构建追加 canonical MethodSpec 并校验摘要。
- `packaging/` 保存明确资源清单与独立安装器。
- `docs/qa-methodology/` 解释协议，不建立并行规则。

非平凡修改前阅读 [METHOD](docs/qa-methodology/METHOD.zh-CN.md)、[SOP](docs/qa-methodology/SOP.zh-CN.md) 和[协议指南](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)。保留未提交工作与其他写者修改；委派时明确文件所有权。把决策、发现和命令存为可审查任务产物，不依赖记忆服务或 hook。

## 工程不变量

Core 保持 SUT-neutral。真实产品源码、API、秘密、环境、fixture 与现场证据留在外部测试工作区；本仓测试动态生成通用临时 fixture。领域事实必须绑定 Profile 声明的证据来源。

受管测试实现前建立有来源的业务命题与可观测 Oracle，同时验证结构和业务语义。占位内容、未解决的阻断缺口和延后探针不能放行实现。需求与风险覆盖分母由 Profile 固定。

模型填写通过字段与内容哈希不证明真实执行。旧评估不能授权变更后的声明或快照。保留真实产品失败、skip、xfail 和未观测状态，不为全绿放宽断言。区分基础设施缺陷和产品缺陷、假设和确认发现。断言精确错误并提供来源，文件路径通过项目 helper 解析。

DSH 同步 context/guard 只使用已完成快照；异步准备和证据持久化在实际可等待生命周期接缝完成。不假设子 Agent 继承 guard，不把 opaque filesystem key 当宿主路径；使用配置的 filesystem/subprocess execution world。

不恢复退役 hooks、其他宿主适配器、旧格式转换、安装兼容、角色守护进程或调度循环。Git 历史与公开 release 保留为历史记录。v0.x 与重构前任务格式明确拒绝。

框架文档保持英中镜像和语言链接。完成交付前执行适用测试、源码/产物边界检查和真实 DSH 验收。测试存在不代表已运行；确定性模型驱动的真实 runtime 与 live-model 结果分开报告。未经显式授权不发布或覆盖 release。
