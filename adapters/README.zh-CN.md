# BUGate 宿主适配器

[English](README.md) | 简体中文

本目录仅包含宿主特定的集成。BUGate Core 保持 SUT、模型、Harness 和运行时无关。
适配器将 BUGate Protocol 绑定到宿主生命周期，不能把宿主会话、调度、重试、检查点
或工具策略语义搬入 Protocol。

架构依据：

- [BUGate 2.0 Host Adapter Guide](../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md)
- [ADR-BUGATE-009 — PowerContext 风格宿主集成](../docs/qa-methodology/BUGATE_HOST_INTEGRATION_POWERCONTEXT_ADR.zh-CN.md)

工程组织采用 catalog、capability manifest、自包含适配器、setup、doctor 与共享
conformance。实现独立于 PowerContext；适配器消费 BUGate 自有的
`PreparedProtocolContext`。

## 宿主状态

- `claude-code/`：v2 迁移目标，已有 legacy 入口继续兼容。
- `codex/`：v2 迁移目标，已有 legacy 入口继续兼容。
- `pi/`：为后续 HyperTest 接入预留。
- `deepseek-harness/`：已实现面向 DSH `0.2.2-alpha.2` 的原生 bundle，见
  [安装使用指南](../docs/DEEPSEEK_HARNESS.zh-CN.md)。公开发布前 catalog availability
  保持 `planned`，具体能力支持另由 manifest 声明。

所有宿主应消费相同的 Protocol Bundle、ProtocolBinding、Artifact、Evidence、Claim
和 AssessmentResult 语义。

## 仓库契约

- `catalog.yaml`：宿主目录。
- `schema/host-manifest.schema.json`：宿主 manifest Schema。
- `<host>/manifest.yaml`：对应宿主的能力声明。
- `CONFORMANCE.md`：共享行为契约。
- `../views/`：canonical 宿主投影模型。

已实现的 DSH CLI：

```text
bugate setup deepseek-harness --project ... --package ... --dsh-profile ...
bugate doctor integrations --project ... --dsh-profile ...
bugate remove deepseek-harness --project ... --dsh-profile ... [--rollback]
bugate protocol prepare --task ... --json
```

通用多宿主 selector 属于后续迁移目标：

```text
bugate setup <host>
bugate setup select --host ...
bugate doctor <host>
bugate doctor integrations
bugate protocol prepare --task ... --json
```
