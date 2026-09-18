# BUGate 2.0 — Host Adapter / Protocol Persistence 实施指导

- **状态：** Accepted Direction / Next-stage Implementation Guide
- **目标版本：** BUGate 2.0
- **日期：** 2026-09-18
- **上位设计：** [BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md](BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)
- **首批宿主：** Claude Code、Codex
- **预留宿主：** Pi Agent、DeepSeek Harness
- **核心目标：** 让 Agent 在长会话、上下文压缩、Subagent、Resume 与模型切换中持续获得同一份 BUGate Protocol，而不是依赖模型“记住”某段 Skill / Prompt。

---

## 1. 问题定义

BUGate Protocol 化之后，仅仅把 MethodSpec 和规则写成机器可读文件还不够。

真正运行时还必须解决三个问题：

1. **Protocol Loading**：Agent 是否看到当前任务适用的 BUGate Protocol？
2. **Protocol Persistence**：多轮工作、Context Compaction、Subagent、Resume 后，Protocol 是否仍然存在？
3. **Protocol Conformance**：Agent 最终产出的 Artifact / Evidence / Claim 是否真的满足 Protocol？

BUGate 2.0 不应把可靠性建立在：

```text
触发 Skill
  -> Agent 阅读 SKILL.md
  -> 希望它在几十轮对话后仍然记得
```

而应建立在：

```text
Protocol Binding
  -> Host Lifecycle Hydration
  -> Agent-facing Protocol Capsule
  -> Artifact + Evidence + Claim
  -> BUGate Assessment
```

核心原则：

> **Protocol 不应只活在 Agent Memory 中。Protocol 应由 Host 持久绑定，并在需要时重新注入 Agent Context。**

---

## 2. 职责边界

### BUGate Core 负责

- Protocol Bundle；
- Protocol Version / Digest；
- MethodSpec；
- Artifact / Evidence / Claim Schema；
- Profile Extension；
- Protocol Context Compiler；
- Assessment。

### Host Adapter 负责

- 把 BUGate 接入具体 Agent Harness；
- 在宿主生命周期事件上重新加载 Protocol；
- 把 Protocol Capsule 注入 Agent；
- 让 Subagent 继承同一 Binding；
- 在 Compaction / Resume 后重新 Hydrate；
- 把 Agent 产出的 Claim 交给 BUGate Assessment。

### Host Runtime 负责

- 是否继续；
- 是否 rework；
- 是否 retry；
- 是否 block；
- 是否 escalate；
- Tool / File / Network permissions；
- Worker / Subagent lifecycle。

BUGate Adapter 不把这些 Runtime 决策重新收回 BUGate Core。

---

## 3. ProtocolBinding

所有 Host Adapter 共享同一个 repo-level Binding 概念。

推荐默认文件：

```text
.bugate/protocol.lock.json
```

示例：

```json
{
  "binding_id": "PB-20260918-001",
  "protocol": {
    "id": "bugate",
    "version": "2.0.0",
    "sha256": "..."
  },
  "profile": {
    "id": "hyperchain",
    "version": "1.0.0",
    "sha256": "..."
  },
  "methods": [
    "business_understanding",
    "testability",
    "test_design",
    "execution",
    "diagnosis",
    "knowledge"
  ]
}
```

ProtocolBinding 的语义类似 lockfile：

- 一个活动任务应绑定 exact Protocol Version；
- Profile 同样固定版本与 digest；
- 中途 BUGate 升级不能静默改变现有任务；
- Resume 时必须能够解析同一 bundle；
- hash 不一致时必须显式报告 protocol-integrity error；
- 不允许自动 fallback 到“最新版”。

---

## 4. Protocol Context Capsule

Agent 不应该在每一轮加载整个 BUGate Protocol。

BUGate 应提供一个纯函数式 Context Compiler：

```text
Protocol Bundle
+ active MethodSpec
+ Profile extensions
+ current subject
+ unresolved Assessment findings
        |
        v
Protocol Context Capsule
```

未来建议提供：

```bash
bugate protocol render \
  --binding .bugate/protocol.lock.json \
  --method testability \
  --format agent
```

典型输出：

```text
BUGate Protocol v2.0.0
Binding: PB-20260918-001
Active Method: testability

Objective:
For each proposition, determine a sufficient verification strategy.

Must account for:
- proposition coverage
- oracle binding
- evidence strategy
- test-layer choice
- environment/resource constraints
- side effects

Required Artifact:
bugate.testability/v2

Profile extensions:
- consensus_safety
- consensus_liveness
- epoch_transition

Unresolved findings:
- P-018 lacks runtime evidence
```

Capsule 应保持短小、确定性、可重新生成。

目标通常是数百到低千 token，而不是把整套 Methodology 文档塞入 Context。

---

## 5. Always-on Bootstrap

Skill 是按需加载机制，不能单独承担 Protocol Persistence。

每个 Host 需要一个极短的 always-on bootstrap，告诉 Agent：

1. 当前仓使用 BUGate Protocol；
2. ProtocolBinding 在哪里；
3. 测试开发任务必须解析 active MethodSpec；
4. 不得仅依赖聊天历史判断 Protocol State；
5. 声明阶段完成前必须产生 Artifact / Evidence / Claim，并接受 Assessment。

Bootstrap 只负责“让 Agent 永远知道 BUGate 存在”。

详细方法论仍然由 Protocol Capsule 按需提供。

---

## 6. Claude Code Adapter

建议目录：

```text
adapters/claude-code/
```

在受治理 SUT Repo 中，目标形态：

```text
CLAUDE.md
.bugate/
  protocol.lock.json
.claude/
  skills/
    bugate/
      SKILL.md
  hooks/
```

### 6.1 CLAUDE.md

Root `CLAUDE.md` 只承载最短的 Protocol Bootstrap，例如：

```text
This repository uses BUGate Protocol.

For test-development work:
- resolve .bugate/protocol.lock.json;
- load the active BUGate MethodSpec;
- treat Artifact, Evidence and Claim as the quality contract;
- do not rely on conversation memory for protocol state;
- assess the Claim before declaring a quality stage complete.
```

不得把整个 BUGate Protocol 放入 `CLAUDE.md`。

### 6.2 Skill

Claude Skill 的职责从“规范本体”调整为：

```text
Protocol Loader
+ Usage Guide
+ Agent-facing View
```

Skill 应指导 Agent：

1. 解析 Binding；
2. 确定 active Method；
3. 获取 / 生成 Protocol Capsule；
4. 自主执行任务；
5. 产出 Artifact + Evidence + Claim；
6. 请求 Assessment。

### 6.3 Lifecycle Hydration

Claude Code Adapter 应尽可能利用宿主提供的 session / hook 生命周期，在以下时机重新 Hydrate：

- session startup；
- context compaction 后；
- resume；
- subagent / delegated task 创建；
- active Method 切换；
- Assessment 返回新 findings 后。

如果某一宿主版本无法提供特定 lifecycle hook，则 root bootstrap + explicit Skill reload 是兼容降级路径，但不能改变 Protocol Core。

---

## 7. Codex Adapter

建议目录：

```text
adapters/codex/
```

在受治理 SUT Repo 中，目标形态：

```text
AGENTS.md
.bugate/
  protocol.lock.json
.agents/
  skills/
    bugate/
      SKILL.md
.codex/
  hooks.json
```

### 7.1 AGENTS.md

Root `AGENTS.md` 承担与 Claude `CLAUDE.md` 相同的 always-on bootstrap 角色。

它不承载 MethodSpec 全文，只声明：

- BUGate Protocol 已绑定；
- Binding 路径；
- 测试开发时必须 resolve active Method；
- 完成声明必须通过 Artifact / Evidence / Claim + Assessment。

### 7.2 Skill

Codex Skill 与 Claude Skill 应共享同一个 canonical BUGate Agent View。

Host-specific wrapper 可以不同，但 Methodology 内容不得 fork。

推荐：

```text
canonical Protocol View
       |
       +--> Claude Skill wrapper
       |
       +--> Codex Skill wrapper
```

### 7.3 Lifecycle Hydration

Codex Adapter 优先在以下 lifecycle 上重新注入 Protocol Capsule：

- SessionStart；
- resume；
- compact / post-compact；
- SubagentStart；
- WorkPackage / active Method 变化；
- Assessment findings 更新。

Subagent 不应依赖主 Agent 用自然语言转述 BUGate 规则。

Host Adapter 应独立为每个 Subagent resolve 同一个 ProtocolBinding。

---

## 8. 多轮工作与 Context Compaction

Protocol Persistence 的硬要求：

> **Conversation History 不是 Protocol 的 Source of Truth。**

长会话的正确模型：

```text
Persistent ProtocolBinding
        |
        v
Context Compiler
        |
        v
fresh Protocol Capsule
        |
        +--> current conversation summary
        +--> current task context
        |
        v
Agent invocation
```

发生 compaction 后：

```text
old conversation compressed
        |
        v
resolve ProtocolBinding again
        |
        v
render fresh Capsule
        |
        v
continue
```

因此压缩可以丢失普通对话细节，但不能丢失 Protocol，因为 Protocol 从外部状态重新注入。

---

## 9. Subagent Inheritance

ProtocolBinding 是任务属性，不是聊天属性。

正确模式：

```text
Main Agent
   |
   | spawn
   v
Subagent
   |
   +-- Host independently resolves same ProtocolBinding
   +-- Host independently renders active Method Capsule
```

错误模式：

```text
Main Agent knows BUGate
   |
   +-- writes prose instructions to Subagent
```

后者会产生：

- 信息丢失；
- 规则改写；
- version 漂移；
- reviewer 与 producer 使用不同规范；
- 无法复现。

---

## 10. Protocol Conformance

BUGate 2.0 不承诺“控制模型内部思考”。

能够可靠检查的是：

- Artifact 是否符合 Schema；
- Evidence 是否存在且 provenance 完整；
- Claim 是否与 Artifact / Evidence 对齐；
- 当前 MethodSpec 的 quality dimensions 是否得到满足；
- Profile extension 是否被覆盖。

因此 Host 使用 BUGate 的完整闭环是：

```text
Bootstrap
   |
   v
ProtocolBinding
   |
   v
Protocol Capsule
   |
   v
Agent autonomous work
   |
   v
Artifact + Evidence + Claim
   |
   v
BUGate Assessment
   |
   v
AssessmentResult
   |
   v
Host decides what happens next
```

BUGate 负责 **Conformance**，不重新承担 **Enforcement / Orchestration**。

---

## 11. Inner Loop / Outer Loop

推荐 Agent Host 使用两层质量闭环。

### Inner Loop — Agent Self-Conformance

Agent 在提交 Claim 前，根据当前 Capsule 自查明显缺口。

目标：

- 低成本；
- 快速修正；
- 减少无意义 Assessment 往返。

### Outer Loop — Independent BUGate Assessment

BUGate Evaluator 基于 immutable Artifact / Evidence refs 进行独立 Assessment。

如果返回 findings：

```text
BUGate AssessmentResult
        |
        v
Host Runtime
        |
        +-- continue
        +-- rework
        +-- escalate
        +-- stop
```

谁决定 rework，不属于 BUGate。

---

## 12. Host Adapter Contract

未来建议 BUGate 定义一个宿主无关的逻辑接口：

```text
resolve_binding()
verify_binding()
resolve_method()
compile_context()
hydrate_context()
submit_claim()
assess_claim()
```

其中真正属于 BUGate Core 的纯能力：

```text
verify_binding
resolve_method
compile_context
assess_claim
```

真正属于 Host Adapter 的能力：

```text
hydrate_context
lifecycle integration
subagent inheritance
resume integration
```

---

## 13. adapters/ 目录约束

目标目录：

```text
adapters/
  README.md

  claude-code/
    README.md

  codex/
    README.md

  pi/
    README.md

  deepseek-harness/
    README.md
```

### Claude Code / Codex

BUGate 2.0 首批实际实施目标。

### Pi

为 HyperTest 预留。

Pi 仍应被视作 generic Agent Harness。BUGate 不进入 Pi Core；未来由 HyperTest / Pi Adapter 完成 Binding 与 Hydration。

### DeepSeek Harness

为 HyperTest 可能采用的 DeepSeek Agent Harness 预留。

BUGate 不假定其最终 API、session model 或 lifecycle hook 设计。目录当前只定义宿主边界，待 HyperTest 选型稳定后再实现。

重要原则：

> **模型是资源，Harness 是 Host，BUGate Protocol 不应因为 Pi 或 DeepSeek Harness 的选择而改变。**

---

## 14. Host-neutral Test Matrix

未来 Adapter Conformance 至少需要验证：

| 场景 | Claude Code | Codex | Pi | DeepSeek Harness |
|---|---:|---:|---:|---:|
| startup 加载 Binding | required | required | reserved | reserved |
| active Method Hydration | required | required | reserved | reserved |
| 50+ turn 长任务不丢失 | required | required | reserved | reserved |
| context compaction 重注入 | required | required | reserved | reserved |
| subagent 继承 Binding | required | required | reserved | reserved |
| resume 仍使用 exact digest | required | required | reserved | reserved |
| protocol upgrade 不污染 in-flight task | required | required | reserved | reserved |
| Claim -> Assessment | required | required | reserved | reserved |

Protocol Core 对所有 Host 必须保持同一行为。

---

## 15. 第一阶段实施顺序

### HA-0 — Adapter Skeleton

建立 `adapters/` 与四个宿主目录。

### HA-1 — Protocol Lock

定义 `.bugate/protocol.lock.json` Schema。

### HA-2 — Context Compiler

实现：

```text
bugate protocol render
```

输入 Binding + Method + Profile，输出 deterministic Protocol Capsule。

### HA-3 — Claude Code Bootstrap

实现：

- root CLAUDE.md bootstrap template；
- canonical BUGate Skill wrapper；
- lifecycle hydration glue。

### HA-4 — Codex Bootstrap

实现：

- root AGENTS.md bootstrap template；
- canonical BUGate Skill wrapper；
- Session / Compact / Subagent hydration glue。

### HA-5 — Conformance Loop

实现：

```text
Artifact + Evidence + Claim
        -> bugate assess
        -> AssessmentResult
```

### HA-6 — Long-context Tests

重点验证：

- context compaction；
- resume；
- subagent；
- protocol version pinning；
- profile digest；
- assessment finding carry-over。

### HA-7 — Pi / DeepSeek Harness

只在 HyperTest 对具体 Harness 接口完成选型后推进。

不得为了占位目录提前把 Host-specific API 反向写进 BUGate Protocol。

---

## 16. 完成标准

Claude Code / Codex Adapter 只有同时满足以下条件才算完成：

1. 不依赖 Agent 初始读过一次 Skill 就长期记住；
2. ProtocolBinding 有 exact version 与 digest；
3. session startup 可恢复 Protocol；
4. compaction 后可重新 Hydrate；
5. Subagent 可独立继承 Binding；
6. active Method Capsule 可确定性重新生成；
7. Skill 与 Protocol 不产生双重事实源；
8. Agent 最终产出 Artifact + Evidence + Claim；
9. BUGate 可以独立 Assessment；
10. Host 决定 continue / rework / escalate，而不是 BUGate；
11. Claude Code 与 Codex 使用相同 Protocol Bundle；
12. Pi / DeepSeek Harness 可以未来接入而不修改 Core Protocol。

---

## 17. 最终原则

BUGate 2.0 的 Protocol Persistence 设计应长期遵守：

> **不要让 Agent 记住 BUGate；让 Host 每次都把正确版本的 BUGate 带给 Agent。**

以及：

```text
BUGate defines GOOD.
Host binds GOOD to the task.
Agent decides HOW.
Host keeps GOOD in context.
BUGate assesses the result.
```

这套 Host Adapter 机制是 BUGate 2.0 从“可执行方法论”真正走向“可被 Agent 长期稳定消费”的必要组成部分，但它仍然不改变 BUGate 的核心边界：BUGate 定义测试方法，不负责 Agent 编排。
