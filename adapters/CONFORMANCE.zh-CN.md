# BUGate DSH 符合性契约

[English](CONFORMANCE.md) | 简体中文

DeepSeek Harness 适配器遵循以下行为契约。HC 编号用于证据追踪，不表示支持其他宿主；唯一机器规则仍位于 `protocol/v2/`。

| ID | 契约 |
|---|---|
| HC-01 | setup 幂等。 |
| HC-02 | canonical Protocol 可发现。 |
| HC-03 | 任务工作区身份一致解析。 |
| HC-04 | 精确版本/摘要绑定保持。 |
| HC-05 | PreparedContext 注入前通过 Schema。 |
| HC-06 | 相关 Agent 推理前已取得协议上下文。 |
| HC-07 | 长会话不依赖一次性 Skill 记忆。 |
| HC-08 | 压缩/重启在宿主提供的生命周期重新 hydrate。 |
| HC-09 | 受支持子 Agent 独立验证相同绑定。 |
| HC-10 | 版本/摘要失配可见失败。 |
| HC-11 | 可检测过期投影。 |
| HC-12 | 显式 assessment 可调用。 |
| HC-13 | Adapter 不改变核心方法/评估语义。 |
| HC-14 | Rollback 仅移除插件拥有的集成状态。 |
| HC-15 | 历史上下文不能覆盖协议和当前工作区事实。 |

HC-15 保留为权威边界，但不要求 Context Runtime 服务。多宿主 setup 和旧兼容回归已退役；[历史证据索引](../docs/README.zh-CN.md#验证记录)保留这些历史决策，与当前适配器行为分开。Manifest 声明支持能力及限制。Setup/doctor/remove 针对一个明确 DSH profile。Doctor 区分宿主存在、插件/资源、投影、绑定与 hydration 观察；安装成功不证明真实受管会话已运行。
