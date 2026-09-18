# BUGate 2.0 — Implementation Roadmap / Continuation Guide

- **状态：** Accepted / Working Plan
- **日期：** 2026-09-18
- **用途：** BUGate 2.0 后续实现的统一续接入口；新会话、新 Agent 或新开发阶段优先从本文继续
- **上位架构：**
  - [BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md](BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)
  - [ADR-BUGATE-007 — Stateless Protocol Core and TestTaskWorkspace](BUGATE_STATELESS_WORKSPACE_ADR.zh-CN.md)
  - [ADR-BUGATE-008 — Memory Bus → Context Runtime](BUGATE_CONTEXT_RUNTIME_ADR.zh-CN.md)
  - [ADR-BUGATE-009 — PowerContext-style Host Integration Architecture](BUGATE_HOST_INTEGRATION_POWERCONTEXT_ADR.zh-CN.md)

---

## 1. 当前最终架构结论

BUGate 2.0 已经完成最重要的边界收敛：

```text
BUGate Protocol
    = defines GOOD

TestTaskWorkspace
    = preserves NOW

Context Runtime
    = remembers BEFORE

Agent Harness / Runtime
    = executes HOW
```

完整模型：

```text
                         BUGate Protocol
                               |
                               v
                         Protocol Engine
                               |
                               v
                    PreparedProtocolContext
                               |
               +---------------+---------------+
               |                               |
               v                               v
       TestTaskWorkspace                 Context Runtime
       current task truth                historical context
               |                               |
               +---------------+---------------+
                               |
                               v
                         Agent Harness
               Claude / Codex / Pi / DSH
                               |
                               v
                             Agent
```

### 1.1 BUGate Core

必须保持：

- SUT-neutral；
- model-neutral；
- harness-neutral；
- runtime-neutral；
- stateless；
- deterministic / reproducible where inputs are identical。

BUGate Core 不保存：

- current stage；
- active worker；
- retry count；
- session；
- checkpoint；
- conversation；
- host state；
- long-term memory。

### 1.2 TestTaskWorkspace

一次测试任务的长期权威事实：

```text
task.yaml
sources/
evidence/
Artifact
Claim
AssessmentResult
```

原则：

> **Facts are persisted; status is derived.**

### 1.3 Context Runtime

原 Memory Bus 升级为独立 Context Runtime。

负责：

- Memory；
- Scope；
- Context Pack；
- Handoff；
- Source / Artifact / Revision；
- Experience；
- Skill Candidate；
- cross-session continuity；
- cross-agent continuity。

OceanBase PowerContext 是：

> **preferred initial provider / reference architecture**

但不是 BUGate Protocol Core dependency。

### 1.4 Agent Harness

负责：

- reasoning；
- planning；
- execution；
- tools；
- subagents；
- retry；
- resume；
- runtime state；
- context injection timing。

---

## 2. BUGate 2.0 的核心对象

已经确定的核心对象：

```text
MethodSpec
Artifact
Evidence
Claim
AssessmentRequest
AssessmentResult
Profile
ProtocolBinding
TestTask
PreparedProtocolContext
QualityPosture (derived view)
```

其中：

### ProtocolBinding

回答：

> 这次任务使用哪一版 BUGate / Profile？

不是：

> 当前执行到了哪一步？

### QualityPosture

回答：

> 根据 Workspace 当前事实，各质量维度成熟到了什么程度？

它是：

```text
Artifact + Evidence + Claim + AssessmentResult
        ->
derived projection
        ->
QualityPosture
```

不能成为第二个可编辑状态源。

### PreparedProtocolContext

所有 Agent Host 唯一的规范化方法论注入对象：

```text
ProtocolBinding
+ TestTaskWorkspace
+ Profile
        |
        v
derive QualityPosture
        |
        v
resolve relevant MethodSpec / findings
        |
        v
PreparedProtocolContext
```

Host Adapter 只能：

```text
validate
-> verify
-> inject
```

不能重新解释 BUGate。

---

## 3. 当前仓库已经建立的 2.0 骨架

### 3.1 Protocol

```text
protocol/
└── v2/
    ├── README.md
    ├── manifest.yaml
    └── schemas/
        └── prepared_protocol_context.schema.json
```

### 3.2 Host Adapters

```text
adapters/
├── catalog.yaml
├── CONFORMANCE.md
├── schema/
│   └── host-manifest.schema.json
│
├── claude-code/
│   ├── manifest.yaml
│   ├── plugin/
│   └── tests/
│
├── codex/
│   ├── manifest.yaml
│   ├── plugin/
│   └── tests/
│
├── pi/
│   ├── manifest.yaml
│   ├── package/
│   └── tests/
│
└── deepseek-harness/
    ├── manifest.yaml
    ├── plugin/
    └── tests/
```

### 3.3 Views / Projections

```text
views/
├── README.md
└── skills/
    └── bugate/
        └── README.md
```

### 3.4 Context Runtime Boundary

```text
context/
├── README.md
└── providers/
    └── powercontext/
        └── README.md
```

---

## 4. PowerContext-style Host Integration 已正式采用

BUGate 2.0 不再为每个 Host 分别设计一套接入方式。

正式照搬 PowerContext 已验证的工程模式：

```text
First-class Host Catalog
+ Capability Manifest
+ Core-owned Prepared Output
+ Host-local Projection
+ self-contained adapters
+ shared conformance suite
+ setup select
+ doctor integrations
+ per-host failure isolation
```

### 4.1 Host Catalog

当前 first-class hosts：

```text
claude-code        planned
codex              planned
pi                 reserved
deepseek-harness   reserved
```

### 4.2 Capability，而不是固定 Hook

BUGate 定义：

```text
protocol_discovery
workspace_discovery
protocol_binding
prepared_protocol_context
automatic_hydration
session_rehydration
compaction_rehydration
subagent_inheritance
skill_projection
explicit_assessment
diagnostics
rollback
context_runtime_hydration
```

各 Host 自己选择 native API 实现。

### 4.3 Projection

以下全部只是 projection：

```text
CLAUDE.md fragment
AGENTS.md fragment
SKILL.md
plugin metadata
hook config
package assets
```

Protocol 才是 authority。

---

## 5. 目标 CLI

BUGate 2.0 最终 CLI 面向以下稳定表面：

### Protocol

```bash
bugate protocol validate
bugate protocol inspect <method>
bugate protocol prepare --task <workspace> --max-bytes 8000 --json
```

### Artifact / Assessment

```bash
bugate artifact validate <artifact>
bugate assess --task <workspace>
bugate status --task <workspace>
bugate explain <reason-code>
```

### Host Integration

```bash
bugate setup claude-code
bugate setup codex
bugate setup pi
bugate setup deepseek-harness

bugate setup select --host claude-code --host codex

bugate doctor claude-code
bugate doctor codex
bugate doctor pi
bugate doctor deepseek-harness
bugate doctor integrations
```

---

## 6. 目标 Agent Context

一次 Agent invocation 的上下文应明确分成三种 authority：

```text
Current user / task instruction

        +

PreparedProtocolContext
= What does GOOD mean?

        +

Context Runtime Context Pack
= What happened BEFORE?

        +

TestTaskWorkspace / live repo / environment evidence
= What is true NOW?

        |
        v
      Agent
```

推荐信任顺序：

```text
current system/user instruction
    >
ProtocolBinding / BUGate Protocol
    >
TestTaskWorkspace / live evidence
    >
historical Context Runtime content
```

Memory 永远不能覆盖 live evidence。

---

# 7. 实施路线

后续实现按以下顺序推进。

---

## Phase A — Protocol Foundation

### BG2-0 / BG2-1 — Core Schemas

补齐：

```text
protocol/v2/schemas/
  protocol_binding.schema.json
  test_task.schema.json
  artifact.schema.json
  evidence.schema.json
  claim.schema.json
  assessment_request.schema.json
  assessment_result.schema.json
  profile.schema.json
  prepared_protocol_context.schema.json  # 已有初版
```

同时建立：

```text
protocol/v2/methods/
protocol/v2/artifacts/
protocol/v2/rules/
protocol/v2/reason_codes.yaml
```

### 验收

- 所有对象都有 `apiVersion: bugate.io/v2`；
- Schema 可独立 validate；
- 不出现 Host / session / worker / retry / workflow state；
- Protocol manifest 能枚举所有 canonical assets。

---

## Phase B — TestTaskWorkspace

定义 canonical Workspace：

```text
test-task/<task-id>/
  task.yaml
  sources/
  evidence/
  01-business-understanding/
  02-testability/
  03-test-design/
  04-implementation/
  05-execution/
  06-diagnosis/
  07-knowledge/
```

实现：

```text
workspace discover
workspace validate
derive QualityPosture
bugate status
```

### 验收

- 不读取 conversation 也能恢复质量上下文；
- 不存在 BUGate-owned current_stage；
- QualityPosture 可完全重算；
- Claude / Codex 对同一 Workspace 得出同一 identity。

---

## Phase C — Assessment Engine

把现有 1.x semantic checker 中有价值的规则迁移为：

```text
Protocol rule
+
Evaluator
+
ReasonCode
```

优先：

- traceability；
- oracle coverage；
- evidence quality；
- risk coverage；
- negative / boundary coverage；
- unresolved critical gap。

### 验收

```text
same Protocol
+ same Profile
+ same Artifact
+ same Evidence
+ same Claim
=
same Assessment semantics
```

Assessment 不执行 Agent、不修改 SUT、不调度 retry。

---

## Phase D — PreparedProtocolContext

实现：

```bash
bugate protocol prepare
```

输入：

```text
ProtocolBinding
+ TestTaskWorkspace
+ Profile
+ budget
```

输出：

```text
PreparedProtocolContext
```

### 验收

- Host-neutral；
- deterministic；
- schema-valid；
- bounded；
- exact protocol digest；
- Adapter 无需理解 MethodSpec 内部结构。

这是 Host Integration 的关键分界点。

---

## Phase E — Projection Compiler

实现：

```text
Canonical Protocol / View
        |
        v
Projection Compiler
        |
        +--> Claude Code
        +--> Codex
        +--> Pi
        +--> DeepSeek Harness
```

首批生成：

- bootstrap fragment；
- BUGate Skill；
- plugin/hook metadata template；
- projection version/digest metadata。

### 验收

- Skill 不再手工 fork；
- projection 可重复生成；
- stale projection 可检测；
- Host-specific projection 不包含新的 Methodology rule。

---

## Phase F — Claude Code Adapter

实现：

```text
bugate setup claude-code
bugate doctor claude-code
```

Adapter：

- discover Workspace；
- resolve ProtocolBinding；
- call `bugate protocol prepare`；
- validate；
- inject；
- expose explicit assessment；
- support lifecycle rehydration；
- optionally attach Context Runtime / PowerContext。

### 验收

通过 HC-01 ~ HC-15 中适用于 Claude Code 的所有项。

---

## Phase G — Codex Adapter

实现：

```text
bugate setup codex
bugate doctor codex
```

重点覆盖：

- Session lifecycle；
- context compaction；
- resume；
- Subagent；
- explicit assessment。

### 验收

通过适用的完整 Host Conformance Suite。

---

## Phase H — Unified Setup / Doctor

实现：

```text
bugate setup select
bugate doctor integrations
```

语义直接采用 PowerContext 模式：

- 只处理用户选中的 Host；
- selected Host failure 不阻断 sibling setup；
- missing Host 对 doctor integrations 非致命；
- present-but-broken integration 为失败；
- 支持 stable JSON output。

---

## Phase I — Context Runtime / PowerContext

完成：

```text
PreparedProtocolContext
        +
PowerContext PreparedContext / Context Pack
```

双路 Hydration。

Context Runtime 重点接入：

- Scope；
- Memory；
- Handoff；
- Experience；
- Revision / provenance。

### 验收

- PowerContext unavailable 不阻塞 BUGate Assessment；
- historical Memory 不覆盖 Workspace/live evidence；
- Context Pack 有 budget；
- Context Provider 可以被替换。

---

## Phase J — Knowledge Promotion

实现：

```text
TestTaskWorkspace
    |
    v
Knowledge Artifact
    |
    v
Memory Candidate
    |
    v
Experience Candidate
    |
    v
review
    |
    v
Profile heuristic / Method candidate / Skill candidate
```

BUGate 定义 promotion quality。

Context Runtime 管 candidate/revision/storage。

---

## Phase K — Legacy Extraction

冻结：

```text
scripts/memory_bus.py
sdtd_orchestrator.py --auto
role runtime/session state
physical write guard as Core authority
```

迁入：

```text
compatibility/v1/
```

只维护兼容性，不增加 2.0 语义。

---

## Phase L — HyperTest / Pi / DeepSeek Harness

只有在 BUGate 2.0 Protocol 和 Claude/Codex Adapter 稳定后推进。

HyperTest 直接消费：

```text
PreparedProtocolContext
+
TestTaskWorkspace
+
Context Runtime
```

Pi / DeepSeek Harness Adapter 只做 Host-native lifecycle glue。

BUGate Core 无需变化。

---

# 8. 下一次继续时从哪里开始

如果没有新的架构决策，**下一步不要继续写 ADR**。

直接进入：

> **Phase A — BUGate 2.0 Core Schemas**

优先顺序：

```text
1. ProtocolBinding schema
2. TestTask schema
3. Artifact schema
4. Evidence schema
5. Claim schema
6. AssessmentRequest schema
7. AssessmentResult schema
8. Profile schema
9. reason_codes.yaml
10. first MethodSpec: business_understanding
```

然后进入：

> **Phase B — TestTaskWorkspace**

再进入：

> **Phase C/D — Assessment Engine + protocol prepare**

只有完成 `PreparedProtocolContext` 后才开始真正写 Claude / Codex plugin。

---

# 9. 禁止回退的架构红线

后续实现不能重新引入：

### 不允许 BUGate Core 持有

```text
current_stage
next_step
worker
session
retry
checkpoint
conversation state
long-term memory state
```

### 不允许 Adapter 自己拥有方法论

```text
Claude-specific MethodSpec
Codex-specific quality rules
Pi-specific BUGate prompt semantics
DSH-specific assessment logic
```

### 不允许 Skill 成为 authority

```text
Protocol -> Skill projection
```

而不是：

```text
Skill -> Protocol
```

### 不允许 Memory 成为 current truth

```text
Workspace / live evidence > Memory
```

### 不允许 BUGate Assessment 做 orchestration

BUGate 只能：

```text
evaluate -> AssessmentResult
```

不能：

```text
retry / route / schedule / block tools / spawn worker
```

---

# 10. 2.0 完成定义

BUGate 2.0 可以宣布架构完成，当：

1. Protocol 全部 machine-readable；
2. BUGate Core stateless；
3. TestTaskWorkspace 可以跨 session / Host 恢复；
4. Artifact / Evidence / Claim / AssessmentResult schema 稳定；
5. QualityPosture 完全 derived；
6. `bugate assess` 可独立运行；
7. `bugate protocol prepare` 产生规范 `PreparedProtocolContext`；
8. Claude Code / Codex 使用同一个 Protocol；
9. Host projection 可生成和诊断；
10. `setup select / doctor integrations` 可用；
11. Host Conformance Suite 可执行；
12. Context Runtime 与 BUGate Core 解耦；
13. PowerContext 可以作为 Provider 接入；
14. Pi / DSH 可以未来接入而不改 Protocol；
15. legacy v1 runtime 被明确隔离到 compatibility boundary。

---

# 11. 一句话继续原则

后续所有实现决策都可以用以下五句话检查：

```text
BUGate defines GOOD.
Workspace preserves NOW.
Context Runtime remembers BEFORE.
Agent decides HOW.
Harness keeps execution alive.
```

以及 Host Integration 的核心规则：

> **BUGate Core 只产生一个 PreparedProtocolContext；所有 Agent Host 都只是把它投影并注入自己的原生生命周期。**
