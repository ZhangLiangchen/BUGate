# ADR-BUGATE-007 — Stateless Protocol Core and TestTaskWorkspace

- **状态：** Accepted
- **日期：** 2026-09-18
- **目标版本：** BUGate 2.0
- **上位设计：** [BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md](BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)
- **Host 接入设计：** [BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md](BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md)

## 1. 背景

BUGate 2.0 已经明确从 Workflow / Enforcement / Agent Runtime 中退出，重新定位为：

> **SUT-neutral executable testing methodology protocol.**

在这一方向下，还需要进一步解决一个关键问题：

> 一个长时间运行的测试开发任务，Agent 如何知道自己已经完成了哪些质量工作、还缺什么，以及重新打开 Claude Code / Codex 或切换 Agent 后如何继续？

一种直觉方案是让 BUGate 或 Host Adapter 维护一个 `host-state.json`、`current_stage` 或类似状态文件。

本 ADR 明确拒绝这种设计。

如果 BUGate 自己维护“当前执行到哪”“下一阶段是什么”，BUGate 会重新承担 Workflow State Machine 的职责，与 2.0 的净化目标冲突。

因此 BUGate 2.0 进一步收缩为完全无状态的 Protocol / Evaluation Core。

---

## 2. 决策

### 2.1 BUGate Core 必须无状态

BUGate Core 不保存：

- current task；
- current stage；
- current method；
- next step；
- Agent session；
- worker state；
- retry state；
- workflow state；
- host state；
- conversation state。

BUGate 接收显式输入并产生确定性输出：

```text
MethodSpec
+ Profile
+ Artifact
+ Evidence
+ Claim
        |
        v
BUGate Evaluation
        |
        v
AssessmentResult
```

同一份 Protocol / Profile / Artifact / Evidence / Claim 在相同版本下应产生可重复的 Assessment 语义。

BUGate 本身不需要数据库、session store 或 workflow ledger 才能完成质量判断。

---

## 3. TestTaskWorkspace

一次测试开发任务的长期事实应存在于任务工作目录中。

本 ADR 将这一概念正式命名为：

> **TestTaskWorkspace**

推荐结构：

```text
test-task/
└── <task-id>/
    ├── task.yaml
    ├── sources/
    ├── evidence/
    │
    ├── 01-business-understanding/
    │   ├── artifact.*
    │   ├── claim.yaml
    │   └── assessments/
    │
    ├── 02-testability/
    │   ├── artifact.*
    │   ├── claim.yaml
    │   └── assessments/
    │
    ├── 03-test-design/
    │   ├── artifact.*
    │   ├── claim.yaml
    │   └── assessments/
    │
    ├── 04-implementation/
    ├── 05-execution/
    ├── 06-diagnosis/
    └── 07-knowledge/
```

TestTaskWorkspace 是：

- 一次测试任务的持久事实载体；
- Claude Code / Codex / HyperTest 之间可共享的交接边界；
- Artifact / Evidence / Claim / AssessmentResult 的存放位置；
- 多轮工作、跨 session、跨 Agent 恢复的依据。

它不是 Workflow Engine。

---

## 4. task.yaml 的职责

`task.yaml` 是 Task Manifest，而不是 Workflow State。

推荐只记录：

```yaml
apiVersion: bugate.io/v2
kind: TestTask

metadata:
  id: REQ-2026-0918-consensus-change
  title: Consensus epoch transition change

protocol:
  id: bugate
  version: 2.0.0
  digest: sha256:...

profile:
  id: hyperchain
  version: 1.0.0
  digest: sha256:...

sources:
  - type: requirement
    path: sources/requirement.md

  - type: repository
    ref: git:abc123

  - type: environment
    ref: test-env://cluster-a
```

禁止把以下字段作为 BUGate Protocol 的规范状态：

```yaml
current_step: testability
next_step: test_design
retry_count: 2
active_worker: worker-3
workflow_node: analyze
```

这些属于 Agent Harness / Runtime。

---

## 5. 三种状态必须严格分离

### 5.1 Execution State

回答：

> Agent 此刻正在做什么？

例如：

- 当前 WorkPackage；
- active agent；
- retry；
- pause / resume；
- current tool call；
- current runtime node。

归属：

> **Agent Harness / Runtime**

BUGate 不保存，也不定义。

---

### 5.2 Artifact Lifecycle State

回答：

> 当前工件处于什么生命周期？

建议只保留最小状态：

```text
draft
candidate
superseded
```

语义：

- `draft`：Agent 正在编辑；
- `candidate`：Agent 认为该 Artifact 已经可以接受评估；
- `superseded`：已有新版本替代。

Artifact Lifecycle State 存在于 TestTaskWorkspace。

它不是质量结论。

---

### 5.3 Quality State

回答：

> 根据当前 Artifact / Evidence / Claim，这个质量声明是否成立？

例如：

```text
satisfactory
needs_improvement
uncertain
incomplete
```

Quality State 由 BUGate Assessment 计算。

BUGate 不保存该状态；`AssessmentResult` 作为普通工件写回 TestTaskWorkspace。

因此：

```text
Execution State
    -> Harness-owned

Artifact Lifecycle State
    -> Workspace-owned

Quality State
    -> BUGate-derived, Workspace-persisted
```

---

## 6. Agent 不能 self-certify

Agent 可以声明：

```text
“我认为 Business Understanding 已完成。”
```

但必须表达为 Claim：

```yaml
apiVersion: bugate.io/v2
kind: Claim

claim:
  type: business_understanding_complete

subject:
  id: REQ-2026-0918-consensus-change

artifacts:
  - ./artifact.yaml

evidence:
  - ../../evidence/EV-001.yaml

known_gaps: []
```

Claim 只代表 Agent 的声明。

真正的质量结论来自 BUGate Assessment。

核心原则：

> **Agent may self-check, but Agent must not self-certify.**

---

## 7. QualityPosture 是派生视图，不是持久状态机

为了帮助 Agent 理解“现在做到什么程度”，BUGate 可以从 TestTaskWorkspace 派生：

> **QualityPosture**

例如：

```text
Business Understanding
  SATISFACTORY
  Assessment: AS-001

Testability
  NEEDS_IMPROVEMENT
  Assessment: AS-002
  Findings:
  - MISSING_ORACLE: P-021
  - INSUFFICIENT_EVIDENCE: P-018

Test Design
  UNCLAIMED
```

QualityPosture 不应有独立可编辑的状态文件作为 Source of Truth。

它应通过：

```text
Workspace
  -> Artifact
  -> Claim
  -> AssessmentResult
  -> derive QualityPosture
```

动态计算。

如果需要缓存，也必须被视为 disposable projection，而不是规范状态。

---

## 8. 不引入 BUGate-owned host-state

BUGate 2.0 明确不引入：

```text
.bugate/host-state.json
.bugate/current-stage.json
.bugate/workflow.json
```

作为 Protocol Core 所需状态。

Host 可以在自己的 Runtime 内存、checkpoint 或私有运行目录中维护 Execution State，但这些状态：

- 不属于 BUGate Protocol；
- 不应成为 BUGate Assessment 的隐式依赖；
- 不应改变 Artifact / Evidence / Claim 的规范语义。

---

## 9. ProtocolBinding 仍然保留

本 ADR 不否定 ProtocolBinding。

`ProtocolBinding` 仍然用于固定：

- BUGate Protocol exact version；
- Protocol digest；
- SUT Profile exact version；
- Profile digest。

它表达的是：

> **这次测试任务使用哪一套方法论。**

它不是：

> **这次测试任务执行到哪一步。**

推荐既可以出现在：

```text
.bugate/protocol.lock.json
```

也可以被引用进 `task.yaml`。

实现必须避免两个可编辑 Source of Truth；若两者并存，应定义 canonical owner 与只读引用关系。

---

## 10. Agent 如何恢复任务

Claude Code、Codex 或未来 HyperTest 在新 session 启动时，不需要依赖旧聊天记录。

恢复流程：

```text
open TestTaskWorkspace
        |
        v
read task.yaml / ProtocolBinding
        |
        v
scan Artifact / Evidence / Claim / AssessmentResult
        |
        v
derive QualityPosture
        |
        v
load relevant MethodSpec
        |
        v
render Protocol Context Capsule
        |
        v
Agent reasons what to do next
```

注意最后一步：

> Agent 自己决定下一步。

BUGate 不返回：

```text
NEXT_STEP = TESTABILITY
```

---

## 11. Context Compiler 输入模型修正

原先 Host Adapter 设计可能倾向：

```text
ProtocolBinding
+ Host State
+ MethodSpec
+ Assessment
```

本 ADR 之后，优先模型调整为：

```text
ProtocolBinding
+ TestTaskWorkspace
+ Profile
        |
        v
derive QualityPosture
        |
        v
resolve relevant MethodSpec
        |
        v
Protocol Context Capsule
```

这样 Protocol Context Compiler 不依赖可变的 Host Workflow State。

Host 仍可以把自己的当前任务上下文附加给 Agent，但 BUGate Capsule 的质量信息来自 Workspace 事实。

---

## 12. bugate status

建议 BUGate 2.0 提供一个纯投影命令：

```bash
bugate status --task test-task/REQ-001
```

它只读取 Workspace，并输出派生视图。

例如：

```text
BUGate Protocol 2.0.0
Task: REQ-001

Business Understanding
  SATISFACTORY
  Assessment: AS-001

Testability
  NEEDS_IMPROVEMENT
  Assessment: AS-002

Outstanding findings:
  - MISSING_ORACLE: P-021
  - INSUFFICIENT_EVIDENCE: P-018

Test Design
  UNCLAIMED
```

`bugate status` 不是 Workflow Orchestrator。

它不能：

- 自动推进阶段；
- 自动选择 Worker；
- 自动 retry；
- 修改 current stage；
- 调度 Agent。

它只是：

> **Workspace -> QualityPosture projection**

---

## 13. Claude Code / Codex 的使用方式

现阶段 Host Adapter 应围绕 TestTaskWorkspace 工作。

### Claude Code

```text
CLAUDE.md bootstrap
   |
   v
discover task workspace
   |
   v
read task.yaml + protocol binding
   |
   v
derive QualityPosture
   |
   v
render Protocol Capsule
   |
   v
Claude works
```

### Codex

```text
AGENTS.md bootstrap
   |
   v
discover task workspace
   |
   v
read task.yaml + protocol binding
   |
   v
derive QualityPosture
   |
   v
render Protocol Capsule
   |
   v
Codex works
```

SessionStart / Compact / Subagent lifecycle 只负责重新执行这一读取与 Hydration 流程。

它们不维护 BUGate 状态。

---

## 14. 跨 Agent / 跨 Harness 交接

TestTaskWorkspace 是跨 Agent 的交接协议。

例如：

```text
Claude Code
   |
   v
TestTaskWorkspace
   |
   v
Codex
   |
   v
TestTaskWorkspace
   |
   v
HyperTest
```

接收方不需要读取前一个 Agent 的聊天记录。

它只需要：

- 相同 ProtocolBinding；
- 相同 Profile；
- Workspace 中的 Artifact / Evidence / Claim / AssessmentResult。

这使 BUGate 2.0 真正做到 Harness-neutral。

---

## 15. Event-sourcing / Derived-state 原则

BUGate 2.0 的 Workspace 设计遵循类似 Event Sourcing 的思想，但不要求实现事件数据库。

持久事实是：

```text
Artifact
Evidence
Claim
AssessmentResult
```

派生视图是：

```text
QualityPosture
Progress summary
Current maturity
Open findings
```

派生视图可以随时重算。

因此：

> **Facts are persisted; status is derived.**

---

## 16. 对 Host Adapter Guide 的约束

Host Adapter 可以：

- discover Workspace；
-重新 Hydrate Protocol；
- attach current runtime task context；
- 启动 Subagent；
- 保持自己的 Execution State。

Host Adapter 不得：

- 创建 BUGate-owned workflow state machine；
- 把 `current_stage` 写成 Protocol 事实；
- 让 `host-state.json` 成为 Assessment 的 Source of Truth；
- 让 Compaction / Resume 依赖聊天记忆恢复 Protocol；
- 让一个 Host 的生命周期模型污染 Core Protocol。

---

## 17. 对 HyperTest 的长期意义

HyperTest 未来可以直接把 TestTaskWorkspace 作为 Protocol-facing task boundary。

HyperTest 可以有自己的：

- TaskSpec；
- WorkPackage；
- AgentRun；
- Runtime checkpoint；
- active method；
- scheduler state。

但当它调用 BUGate 时，仍然转换为同一套：

```text
ProtocolBinding
+ Artifact
+ Evidence
+ Claim
        |
        v
AssessmentResult
```

因此 Claude Code / Codex 时代形成的 Workspace 资产可以被 HyperTest 继续消费。

---

## 18. 完成标准

BUGate 2.0 的无状态模型只有满足以下条件才算成立：

1. BUGate Core 不需要 session state；
2. BUGate Core 不保存 current stage；
3. BUGate Core 不保存 current task；
4. BUGate Core 不需要 Host runtime state 才能 Assessment；
5. TestTaskWorkspace 可以独立恢复任务质量上下文；
6. Artifact Lifecycle 与 Quality State 明确分离；
7. Agent Claim 与 AssessmentResult 明确分离；
8. QualityPosture 可以从 Workspace 重新计算；
9. 新 Agent 不读取旧 conversation 也能继续工作；
10. Claude Code 与 Codex 可以围绕同一 Workspace 交接；
11. HyperTest 未来可以接管同一 Workspace；
12. Protocol version/digest 可复现；
13. Runtime 执行状态不进入 BUGate Protocol Core。

---

## 19. 最终裁决

BUGate 2.0 的状态模型最终确定为：

```text
Execution State
  -> Agent Harness / Runtime

Artifact Lifecycle State
  -> TestTaskWorkspace

Quality State
  -> BUGate-derived AssessmentResult
  -> persisted in TestTaskWorkspace
```

BUGate 本身：

```text
Input
  |
  v
Pure / deterministic evaluation
  |
  v
Output
```

因此：

> **BUGate 不拥有任务状态。BUGate 只解释任务工件。**

以及：

> **真正的长期状态存在于 TestTaskWorkspace 的事实工件中；所谓“执行到哪、质量做到哪”应由 Harness reasoning 与 BUGate-derived projection 分别得出，而不是由 BUGate 自己维护状态机。**
