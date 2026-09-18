# ADR-BUGATE-008 — Memory Bus → Context Runtime

- **状态：** Accepted
- **日期：** 2026-09-18
- **目标版本：** BUGate 2.0
- **取代范围：** 对 BUGate 2.0 而言，取代 [ADR-BUGATE-003](BUGATE_MEMORY_BUS_SYSTEM_LEVEL_ADR.md) 中“Memory Bus 是 BUGate required core component”的结论；ADR-BUGATE-003 继续描述 v0.x / 1.x 兼容行为
- **关联：** [ADR-BUGATE-007 — Stateless Protocol Core and TestTaskWorkspace](BUGATE_STATELESS_WORKSPACE_ADR.zh-CN.md)
- **参考实现 / 架构来源：** [OceanBase PowerContext](https://github.com/oceanbase/powercontext)

## 1. 背景

BUGate 1.x 的 Memory Bus 基于 `mcp-memory-service`，逐渐承担了：

- long-term memory；
- progress / finding / decision；
- cross-session recall；
- cross-agent messaging；
- handoff；
- role transition / lineage checkpoint；
- session-start context injection；
- memory promotion。

这些能力解决了早期 Claude Code / Codex 多会话、多 Agent 工作中的真实问题，但它们已经超出“测试方法论”的边界。

BUGate 2.0 已经通过 ADR-BUGATE-007 确定：

> **BUGate Core 是无状态的 Executable Testing Protocol。**

因此，如果继续把长期记忆、上下文召回、跨会话接力和 Agent 生命周期写在 BUGate Core 中，就会重新把 Runtime Infrastructure 混回 Protocol。

另一方面，这些能力本身不能简单删除。自主测试开发天然需要：

- 长期知识；
- 跨会话上下文恢复；
- Agent / Subagent 之间的任务接力；
- 历史经验召回；
- 对 Context Window 的预算管理；
- 从一次任务结果中沉淀 Experience；
- 可追溯的 Memory / Experience / Skill 演进。

因此本 ADR 将旧 Memory Bus 的长期方向重新定义为：

> **Context Runtime**

---

## 2. 决策摘要

BUGate 2.0 采用以下四层分离：

```text
BUGate Protocol
    |
    | defines methodology / quality / promotion rules
    v
TestTaskWorkspace
    |
    | authoritative current-task facts
    v
Context Runtime
    |
    | historical context / continuity / reusable experience
    v
Agent Harness
    |
    | reasoning / planning / tools / execution
    v
Agent
```

其中：

### BUGate Protocol

负责：

- MethodSpec；
- Artifact / Evidence / Claim schema；
- Assessment；
- Knowledge / Experience Promotion Methodology；
- 什么知识值得沉淀；
- 什么证据足以支持经验晋升。

不负责：

- Memory storage；
- Vector search；
- Context injection；
- Session recall；
- Handoff transport；
- Scope storage；
- Agent transcript；
- Long-term context lifecycle。

### TestTaskWorkspace

负责当前任务的权威事实：

- Task Manifest；
- Artifact；
- Evidence；
- Claim；
- AssessmentResult。

### Context Runtime

负责：

- long-term Memory；
- Context preparation；
- cross-session continuity；
- cross-agent Handoff；
- Scope；
- immutable / versioned context artifacts；
- Experience lifecycle；
- reusable Skill candidate lifecycle；
- historical context provenance。

### Agent Harness

负责：

- planning；
- tool calling；
- current execution state；
- subagents；
- retry / resume；
- context injection timing；
- execution。

---

## 3. BUGate Core 不依赖 Context Runtime

Context Runtime 对 BUGate Protocol 的正确性不是必需条件。

以下操作必须在 Context Runtime 完全不存在时仍然成立：

```text
validate Protocol
validate Artifact
validate Evidence
validate Claim
derive QualityPosture from TestTaskWorkspace
assess Claim
```

因此 BUGate 2.0 Core：

- 不 import PowerContext SDK；
- 不 import mcp-memory-service；
- 不要求 Context Server 存活；
- 不通过 Context Runtime 保存 current stage；
- 不以 Memory 作为 Assessment 的隐式 Source of Truth。

Context Runtime 是独立 companion infrastructure。

---

## 4. PowerContext 的吸收原则

BUGate 2.0 不复制 PowerContext 的实现，而吸收其已经成熟的 Context Runtime 抽象。

第一阶段正式吸收以下概念：

### 4.1 Scope

Context 不再主要依赖自由格式 tag 模拟隔离。

Context Runtime 应具备显式 Scope 概念，用于表达：

```text
user / organization
  -> project
      -> domain / workstream
          -> task
```

Scope 负责：

- context isolation；
- cross-session identity；
- controlled sharing；
- context query boundary。

BUGate Profile 可以提供 Scope hint，但不拥有 Scope lifecycle。

### 4.2 Source → Artifact → Revision

历史上下文不能只是一条可原地覆盖的 embedding record。

推荐采用：

```text
Source
   |
   v
Artifact
   |
   v
Revision 1 -> Revision 2 -> Revision 3
```

原则：

- Source 表示原始输入 / 观察；
- Artifact 表示可复用 Context 对象；
- Revision 表示 immutable history；
- revision / supersession 保留 provenance。

BUGate Evidence 可以引用 Context Artifact Revision，但不会把它变成更高权威的事实。

### 4.3 Prepared Context / Context Pack

自动召回不能等价于：

```text
vector search -> top K -> paste into prompt
```

Context Runtime 应对最终注入 Agent 的内容负责：

- retrieval；
- ranking / selection；
- deduplication；
- total byte/token budget；
- section ordering；
- citations；
- truncation；
- rendering；
- trust labeling。

本 ADR 将这一最终注入对象统一称为：

> **Context Pack**

PowerContext 中对应的公开对象是 `PreparedContext`。

### 4.4 Memory

Memory 用于：

- durable decisions；
- constraints；
- reusable facts；
- stable lessons；
- historical state worth recalling。

Memory 不是当前 Task Workspace 的替代品。

### 4.5 Handoff

Handoff 与 Memory 必须分离。

Memory 回答：

> 什么长期知识值得未来继续使用？

Handoff 回答：

> 另一个 session / Agent 接手这项正在进行的工作时，需要知道什么？

Handoff 可以包含：

- objective；
- verified progress；
- exact evidence；
- known omissions；
- blockers；
- next action；
- source / artifact references。

但 TestTaskWorkspace 仍然是当前测试任务的权威事实。

### 4.6 Experience

一次 Memory 或 Task Outcome 不能自动成为“经验规则”。

Experience 应经过：

```text
evidence selection
    -> candidate
    -> review
    -> approved immutable revision
```

BUGate 定义 Experience Promotion Methodology；
Context Runtime 负责 candidate / review / revision lifecycle。

### 4.7 Skill Promotion

采用：

```text
Memory
  -> Experience Candidate
  -> reviewed Experience Revision
  -> Skill Candidate
  -> reviewed Skill Revision
  -> explicit host projection / install
```

Skill 不能因为模型生成就直接成为新的 normative BUGate Protocol。

是否将某个 Experience 最终晋升为 BUGate Core Method / Rule，是独立的 BUGate protocol-evolution review。

---

## 5. Trust Model

Context Runtime 中的历史内容默认是：

> **historical supporting context, not current truth**

推荐信任优先级：

```text
System / user instructions
        >
Current BUGate ProtocolBinding
        >
Current TestTaskWorkspace
        >
Live repository / test environment evidence
        >
Historical Context Runtime content
```

因此 Context Pack 必须保留 citation / provenance。

Agent 看到：

```text
“过去一次任务发现 NVP 路由会导致 VP 操作失败”
```

只能把它当作：

> 值得验证的历史经验

不能直接替代：

> 当前代码 / 当前环境的证据。

---

## 6. Protocol Capsule 与 Context Pack

BUGate 2.0 Agent Context 由两类完全不同的内容组成：

```text
BUGate Protocol Capsule
        |
        | What does GOOD mean?
        |
        +--------------------+
                             |
                             v
                           Agent
                             ^
                             |
        +--------------------+
        |
Context Pack
        |
        | What happened before?
        |
Historical / reusable context
```

### Protocol Capsule

来源：

- ProtocolBinding；
- MethodSpec；
- Profile；
- TestTaskWorkspace；
- derived QualityPosture。

权威：

> Methodology / current quality requirements.

### Context Pack

来源：

- Context Runtime；
- current Scope；
- historical Memory；
- approved Experience；
- Handoff；
- optional Profile / Topic context。

权威：

> Historical supporting context.

两者不能合并为同一个数据库或事实层。

---

## 7. Context Hydration

Host Adapter 在一次模型调用前可以执行：

```text
resolve TestTaskWorkspace
        |
        +--> BUGate Context Compiler
        |       |
        |       v
        |   Protocol Capsule
        |
        +--> Context Runtime
                |
                v
            Context Pack
                |
                +-------------------+
                                    v
Current task + Protocol Capsule + Context Pack
                                    |
                                    v
                                  Agent
```

Host 决定何时 hydrate。

典型 lifecycle：

- session startup；
- user prompt submit；
- context compaction 后；
- resume；
- subagent creation；
- model switch；
- explicit handoff continuation。

BUGate Core 不感知这些 lifecycle event。

---

## 8. ContextProvider Contract

BUGate 2.0 的 Host / HyperTest 集成只依赖一个 provider-neutral 概念边界，不直接依赖 PowerContext API。

建议逻辑能力：

```text
prepare_context(scope, subject, budget)
remember(...)
revise(...)
retire(...)
search(...)
create_handoff(...)
continue_handoff(...)
record_outcome(...)
propose_experience(...)
review_candidate(...)
```

其中 BUGate Core 不需要调用这些方法。

调用者是：

- Claude Code Adapter；
- Codex Adapter；
- Pi Adapter；
- DeepSeek Harness Adapter；
- HyperTest。

---

## 9. PowerContext 的定位

PowerContext 被 BUGate 2.0 接受为：

> **Context Runtime 的参考架构与首选初始 Provider。**

原因：

- 已提供 Scope；
- 已提供持久 Context / Memory；
- 已提供 bounded PreparedContext；
- 已提供 Memory 与 Handoff 的分离；
- 已提供 Source / Artifact / Revision；
- 已提供 Experience / Skill candidate + review；
- 已提供 Codex / Claude Code / Pi / DeepSeek Harness 等宿主集成；
- 与 BUGate / HyperTest 的目标 Harness 集合高度重合。

但这不是：

```text
BUGate Core -> hard dependency -> PowerContext
```

而是：

```text
Host / HyperTest
      |
      v
ContextProvider
      |
      +--> PowerContext
      +--> future alternative
      +--> none
```

Protocol 不因 Context Provider 变化而变化。

---

## 10. 当前 mcp-memory-service 的迁移

现有：

```text
scripts/memory_bus.py
bin/memory-bus-*
mcp-memory-service
role-transition memory
lineage checkpoint
session-start recall
progress / finding / decision notes
```

继续服务 v0.x / 1.x 兼容行为。

BUGate 2.0 不继续扩展这些接口。

迁移方向：

```text
Current Memory Bus
      |
      +--> v1 compatibility
      |
      +--> extract useful data
              |
              v
        Context Runtime migration
```

最终建议归档至：

```text
compatibility/v1/memory-bus/
```

或等价 compatibility boundary。

迁移必须保留：

- historical Memory provenance；
- namespace / scope identity；
- original timestamps；
- content hashes where possible；
- promotion lineage。

---

## 11. ADR-BUGATE-003 的新地位

ADR-BUGATE-003 继续是 BUGate 1.x 的有效历史与兼容文档。

其中以下结论不再适用于 BUGate 2.0：

> Memory Bus is a REQUIRED core BUGate component.

BUGate 2.0 改为：

> Context Runtime is independent companion infrastructure; BUGate Protocol correctness does not depend on it.

---

## 12. Knowledge Promotion 的新边界

BUGate 保留：

- 什么是 reusable knowledge；
- 何时可以由 finding 形成 lesson；
- 何时需要更多 evidence；
- SUT-local 与 SUT-neutral 的区分；
- Experience 的质量要求；
- Method / Rule 晋升条件。

Context Runtime 负责：

- candidate persistence；
- review workflow storage；
- revision history；
- provenance；
- retrieval；
- Context Pack inclusion；
- Skill artifact lifecycle。

因此：

```text
BUGate
  = promotion methodology

Context Runtime
  = promotion infrastructure
```

---

## 13. TestTaskWorkspace 与 Context Runtime

必须明确：

> **TestTaskWorkspace != Memory**

TestTaskWorkspace：

- 当前任务；
- authoritative task facts；
- current Artifact；
- current Evidence；
- current Claim；
- current AssessmentResult。

Context Runtime：

- 历史任务；
- long-term decision；
- reusable lesson；
- prior Handoff；
- approved Experience；
- context prepared for recall。

一次任务完成后，可以选择性地从 Workspace 提炼 Context Runtime 内容。

不能默认把整个 Workspace 全量向量化为“Memory”。

---

## 14. 推荐目录演进

BUGate 2.0 目标结构增加 Context integration boundary：

```text
BUGate/
  protocol/
  engine/
  profiles/
  views/
  adapters/
    claude-code/
    codex/
    pi/
    deepseek-harness/

  context/
    README.md
    providers/
      powercontext/
        README.md

  compatibility/
    v1/
      memory-bus/
```

`context/` 不是 Protocol Core。

它只承载：

- provider-neutral Context Runtime contract docs；
- provider adapter / setup projection；
- PowerContext integration glue。

---

## 15. Claude Code / Codex / Pi / DeepSeek Harness

四种 Host 使用同一分层：

```text
Host lifecycle
    |
    +--> BUGate Protocol Capsule
    |
    +--> Context Runtime Context Pack
    |
    v
Agent invocation
```

Host-specific hook / plugin 只负责注入时机。

### Claude Code

优先使用 Host lifecycle + Context Runtime integration 进行 bounded recall。

### Codex

使用 prompt/session hooks + Context Runtime integration，并保持显式 Handoff 能力。

### Pi

未来 HyperTest / Pi Adapter 负责同时 hydrate Protocol Capsule 与 Context Pack。

### DeepSeek Harness

未来 HyperTest / DSH Adapter 采用相同双上下文模型。

---

## 16. Availability / Failure Semantics

Context Runtime 故障默认：

> **fail-open for autonomous execution, fail-visible for memory operations**

即：

- Agent 仍可依赖 Protocol + Workspace 工作；
- 不得因为 Context Runtime unavailable 就声称历史 Context 已加载；
- explicit durable memory / handoff / promotion 写入失败必须可见；
- BUGate Assessment 不因 Context Runtime outage 自动失败，除非某个 Claim 显式引用了一个不可取得的 Context Artifact 作为 Evidence。

这与 BUGate Protocol 的无状态、可离线评估目标一致。

---

## 17. 安全与隐私边界

Context Runtime 可能跨 session 长期保存内容，因此 Host 必须：

- 不默认存储 secret；
- 不默认捕获 credential；
- 对 prompt capture 提供显式控制；
- 保留 scope / access boundary；
- 对 remote Context Server 使用独立认证；
- 在 Context Pack 中保留来源引用；
- 支持 retire / supersede；
- 不把 Memory 内容提升为 system instruction。

---

## 18. 实施顺序

### CR-0 — Freeze legacy Memory Bus growth

除 v1 bugfix / compatibility 外，不再向 `memory_bus.py` 添加新的 2.0 语义。

### CR-1 — Context Contract

定义：

- ScopeRef；
- ContextCitation；
- ContextPack；
- HandoffRef；
- ExperienceRef；
- ContextProvider capabilities。

### CR-2 — PowerContext Provider

增加 PowerContext provider integration / documentation。

### CR-3 — Host dual hydration

Claude Code / Codex：

```text
Protocol Capsule + Context Pack
```

双路注入。

### CR-4 — Workspace → Memory promotion

从 TestTaskWorkspace 的 Knowledge Artifact 中显式生成 Memory / Experience candidate。

### CR-5 — Legacy migration

为现有 mcp-memory-service 数据提供迁移 / export / mapping 指导。

### CR-6 — HyperTest integration

HyperTest 成熟后，由 HyperTest Runtime 管理：

- Scope selection；
- Context prepare timing；
- Handoff；
- WorkPackage / AgentRun correlation。

BUGate Core 不改变。

---

## 19. 完成标准

Context Runtime 架构只有满足以下条件才算完成：

1. BUGate Protocol 在 Context Runtime unavailable 时仍可工作；
2. BUGate Core 不 import provider SDK；
3. TestTaskWorkspace 仍是当前任务权威事实；
4. Context Pack 有严格预算；
5. Context 项目保留 citation / provenance；
6. Memory 与 Handoff 分离；
7. Context Artifact 有 revision / supersession；
8. Experience / Skill 需要 review 才能晋升；
9. historical context 不覆盖 live evidence；
10. Claude Code / Codex 可消费同一 Context Provider；
11. Pi / DeepSeek Harness 可在未来复用同一边界；
12. legacy mcp-memory-service 不再承载新的 BUGate 2.0 语义；
13. Context Provider 可替换；
14. PowerContext 是首选 Provider，但不是 Protocol dependency。

---

## 20. 最终裁决

BUGate 2.0 对 Memory 的最终关系为：

```text
BUGate does not own Memory.

BUGate defines
what knowledge is worth preserving
and what evidence makes it trustworthy.

Context Runtime preserves,
retrieves, transfers and composes that context.
```

中文：

> **BUGate 不拥有记忆；BUGate 定义什么知识值得记住，以及什么证据足以让它可信。**

> **Context Runtime 负责保存、检索、接力、版本化与组装这些上下文。**

原 Memory Bus 因而不被删除，而是完成一次架构升级：

```text
Memory Bus
    |
    v
Context Runtime
    |
    +-- Memory
    +-- Scope
    +-- Context Pack
    +-- Handoff
    +-- Artifact Revision
    +-- Experience
    +-- Skill Candidate
    +-- Cross-session / Cross-agent continuity
```
