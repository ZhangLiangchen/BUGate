# BUGate 2.0 — Executable Agent Testing Protocol 改造指导

- **状态：** Accepted Direction / Next-stage Implementation Guide
- **目标版本：** BUGate 2.0
- **日期：** 2026-09-18
- **适用范围：** BUGate Core、SUT Profile、Agent-facing Skill / Adapter
- **不适用范围：** Agent Loop、Workflow Runtime、任务调度、Subagent 编排、Retry、Checkpoint、Tool Enforcement
- **与现有 1.x 文档的关系：** 本文定义 BUGate 2.0 的目标态与迁移方向；现有 1.x / v0.4.x 文档继续描述当前兼容行为。发生冲突时，当前运行行为以已发布 1.x 契约为准，2.0 新实现与迁移决策以本文为准。

---

## 1. Executive Summary

BUGate 1.x 在演进过程中逐渐同时承担了多种职责：

- 测试开发方法论；
- Agent Skill；
- 分层测试 Artifact 定义；
- Semantic Gate；
- Pre-code Write Guard；
- Role Governance；
- Receipt / Lineage；
- Multi-Agent Dispatch；
- Orchestrator；
- Memory；
- Self-Healing Workflow。

这些能力在早期 Agent 自主性不足时具有合理性，但随着 Claude Code、Codex、Pi 等 Agent Harness 逐渐具备 autonomous planning、loop engineering、tool use、task decomposition、subagents、retry、reflection、context / session management，BUGate 再继续承担 Workflow / Agent orchestration 会与 Agent Harness 和 HyperTest Runtime 形成职责竞争。

BUGate 2.0 因此重新定义为：

> **BUGate is a SUT-neutral executable testing methodology protocol for autonomous agents.**

中文定义：

> **BUGate 是一套面向自主 Agent 的、与被测系统、模型、Harness 和 Runtime 解耦的可执行测试开发方法论协议。**

BUGate 负责回答：

> 一个优秀的测试开发 Agent 在当前测试阶段应该理解什么、考虑什么、产出什么，以及如何证明自己的结论具有足够质量？

BUGate 不再回答：

- Agent 下一步应该调用哪个工具；
- 应该调用几个 Subagent；
- 哪个 Worker 先执行；
- 失败后重试几次；
- 从哪个 checkpoint 恢复；
- 是否允许某个 Tool 执行；
- 当前任务应该进入哪个 workflow node。

这些职责交给 Agent Harness、HyperTest Runtime、LangGraph、Temporal、CI / Sandbox Runtime 等宿主系统。

---

## 2. BUGate 2.0 的根本定位

BUGate 2.0 的核心公式：

```text
BUGate
=
Testing Methodology
+
Machine-readable Protocol
+
Artifact Specification
+
Evidence Specification
+
Quality Assessment
```

明确不再包含：

```text
Workflow Orchestration
Agent Scheduling
Subagent Dispatch
Retry Loop
Checkpoint / Resume
Pause / Interrupt
Tool Blocking
Write Guard
Runtime Authorization
Agent Session Lifecycle
Process Recovery
```

因此：

```text
BUGate 1.x

Methodology
+ Skill
+ Gate
+ Enforcement
+ Workflow
+ Role Runtime
+ Evidence
+ Memory
+ Orchestration

             ↓

BUGate 2.0

Methodology
+ Protocol
+ Artifact Contract
+ Evidence Contract
+ Assessment
```

---

## 3. 四条系统边界

BUGate 2.0 与 HyperTest 的长期边界统一为：

```text
HyperTest = WHAT
Agent     = HOW
Runtime   = CONTINUE
BUGate    = GOOD
```

### HyperTest — WHAT

HyperTest 负责定义：

- TaskSpec；
- WorkPackage；
- Model Routing；
- Agent Role / Capability；
- SUT Adapter；
- 测试环境；
- 系统级任务状态；
- 任务预算；
- 任务结果聚合。

### Agent Harness — HOW

Pi / Codex / Claude 等 Harness 负责：

- reasoning；
- planning；
- tool calling；
- subagent；
- self-correction；
- replanning；
- context；
- local retry。

### Runtime — CONTINUE

LangGraph、Temporal 或 Harness-native runtime 负责：

- checkpoint；
- resume；
- retry；
- long-running execution；
- interrupt；
- concurrency；
- scheduling；
- process / worker recovery。

### BUGate — GOOD

BUGate 只负责：

- Testing Methodology；
- Quality Criteria；
- Artifact Contract；
- Evidence Contract；
- Assessment；
- Knowledge Promotion Methodology。

---

## 4. 核心原则

### Principle 1 — Method over Workflow

BUGate 定义方法，不定义流程。

### Principle 2 — Outcome over Procedure

BUGate 规定 Agent 应达到什么质量，不规定 Agent 如何达到。

### Principle 3 — Protocol over Prompt

Markdown Skill 不再是唯一事实源。机器可读 Protocol 才是规范本体。

### Principle 4 — Evidence over Assertion

Agent 的自然语言声明不是 Evidence。质量结论必须可以追溯到结构化 Evidence。

### Principle 5 — Assessment over Enforcement

BUGate 对质量做评估，不拥有 Tool / Runtime 的最终执行权。

### Principle 6 — Runtime Independence

BUGate Core 不依赖 LangGraph、Pi、Claude Code、Codex、Temporal 或任意单一 Runtime。

### Principle 7 — SUT Neutrality

Core 不拥有产品事实。具体 SUT 的领域、资源、测试层、Evidence Source 通过 Profile 扩展。

### Principle 8 — Layer is Quality, not Workflow

BUGate Layer 表示质量成熟度和声明边界，不表示必须顺序执行的 workflow node。

---

## 5. Layer 的重新定义

BUGate 1.x 中 Layer 容易被理解为：

```text
Layer 1
 ↓
Layer 2
 ↓
Layer 3
 ↓
Layer 4
```

BUGate 2.0 必须明确：

> **Layer ≠ Workflow Node**

Layer 应重新定义为 Quality Maturity Dimension / Quality State，例如：

```text
Business Understanding
Testability
Test Design
Implementation
Execution
Diagnosis
Knowledge
```

Agent 可以针对这些质量维度提交 Claim：

```text
business_understanding_complete
testability_strategy_defined
test_design_complete
implementation_complete
execution_analyzed
diagnosis_supported
knowledge_extracted
```

BUGate 只定义“宣称这些状态成立需要什么”，不规定“如何到达这些状态”。

因此：

> Agent 可以自由决定读 PRD、看代码、跑探针、开几个 Subagent、调用什么模型、如何重试。BUGate 不干预路径，只评估结果质量。

---

## 6. BUGate Protocol v2 核心对象

BUGate Protocol v2 第一阶段定义七个核心对象：

```text
MethodSpec
Artifact
Evidence
Claim
AssessmentRequest
AssessmentResult
Profile
```

关系如下：

```text
MethodSpec
    │
    ▼
Agent
    │
    ├── Artifact
    ├── Evidence
    └── Claim
          │
          ▼
 AssessmentRequest
          │
          ▼
      BUGate
          │
          ▼
 AssessmentResult
```

Protocol v2 的最小闭环：

```text
Method
  ↓
Artifact + Evidence
  ↓
Claim
  ↓
Assessment
  ↓
Structured Findings
```

---

## 7. MethodSpec

`MethodSpec` 是 BUGate 2.0 最重要的新对象。

它定义：

> 在一个测试开发质量维度上，Agent 应该达到怎样的方法论质量。

示例：

```yaml
apiVersion: bugate.io/v2
kind: MethodSpec

metadata:
  id: testability
  version: 2.0.0

objective:
  Decide how each business proposition can be verified
  with the cheapest sufficient testing strategy.

quality_dimensions:
  - proposition_coverage
  - oracle_binding
  - evidence_strategy
  - test_layer_selection
  - side_effect_analysis
  - environment_requirements

required_outputs:
  - type: bugate.testability/v2

recommended_considerations:
  - cheapest sufficient test layer
  - destructive side effects
  - test data lifecycle
  - environment constraints
  - observability
  - asynchronous behavior
```

MethodSpec 中禁止出现：

```yaml
steps:
  - read_prd
  - inspect_repo
  - call_worker
  - generate_cases
```

因为这属于 Agent / Runtime 的 HOW。

MethodSpec 必须描述：

> **WHAT GOOD LOOKS LIKE**

而不是：

> **HOW TO DO THE WORK**

---

## 8. Artifact Protocol

现有 Artifact Stack 继续保留其方法论价值：

- `01_business_brief.md`；
- `02_testability.md`；
- `03_inventory.yaml`；
- `03a_test_cases.md`；
- `03b_adversarial_cases.yaml`；
- `04_execution_report.md`；
- `05_knowledge_update.md`。

但文件名不再是 Protocol 本身。

例如：

```text
02_testability.md
```

只是：

```text
serialization of bugate.testability/v2
```

示例：

```yaml
apiVersion: bugate.io/v2
kind: TestabilityArtifact

metadata:
  id: testability-UC001

subject:
  id: UC001

propositions:
  - id: P-001

    selected_strategy:
      layer: api

    alternatives:
      - layer: static
        rejected_reason: cannot validate runtime state

    oracle:
      id: O-001

    evidence_strategy:
      type: runtime_response
```

未来 Markdown、YAML、JSON、Database、API 都可以作为 serialization。

Core Protocol 绑定 Artifact Type，不绑定具体文件格式。

---

## 9. Evidence Protocol

BUGate 2.0 中 Evidence 是一等对象。

核心原则：

> **Agent 的自然语言陈述不是 Evidence。**

“测试已经全部通过”不能直接作为质量依据。

示例：

```yaml
apiVersion: bugate.io/v2
kind: Evidence

metadata:
  id: EV-20260918-001

type: test_execution

subject:
  test_case: TC-042

source:
  revision: abc123

execution:
  command: pytest tests/test_consensus.py::test_view_change
  exit_code: 0

environment:
  id: hyperchain-test-01

artifacts:
  - uri: artifact://junit/001.xml
    sha256: xxx

producer:
  agent: worker-03
  model: deepseek
```

首批建议 Evidence Type：

```text
source_reference
requirement_reference
code_reference
runtime_probe
test_execution
log
metric
coverage
benchmark
review
human_input
external_system_result
```

Evidence 的重点是 provenance，而不是把所有原始数据复制进 BUGate。

---

## 10. Claim Protocol

Agent 不再只用自然语言说：

> 我完成了测试设计。

Agent 应形成结构化 Claim：

```yaml
apiVersion: bugate.io/v2
kind: Claim

metadata:
  id: CL-001

claim:
  type: test_design_complete

subject:
  id: UC001

artifacts:
  - ref: artifact://inventory/001
  - ref: artifact://cases/001

evidence:
  - ref: evidence://EV001
  - ref: evidence://EV002

open_questions: []
known_gaps: []
```

Claim 是 Agent 与 BUGate 之间最重要的协议边界之一。

它表达：

> “我宣称 X 已经达到某种质量状态，这是对应的 Artifact、Evidence、Open Question 与 Known Gap。”

---

## 11. AssessmentRequest

BUGate 不再主动“推进流程”或“卡住 Agent”。

宿主系统可以针对 Claim 发起 Assessment：

```yaml
apiVersion: bugate.io/v2
kind: AssessmentRequest

metadata:
  id: AR-001

method:
  id: test_design
  version: 2.0.0

claim:
  ref: CL-001

profile:
  ref: profile://hyperchain

mode: standard
```

AssessmentRequest 是纯评估调用。

它不包含：

- next node；
- worker；
- retry count；
- runtime session；
- checkpoint；
- process state。

---

## 12. AssessmentResult

BUGate 2.0 不返回 Runtime 意义上的 ALLOW / BLOCK。

推荐统一输出：

```yaml
apiVersion: bugate.io/v2
kind: AssessmentResult

metadata:
  id: AS-001

status:
  complete: false
  confidence: high

quality:
  level: needs_improvement

findings:
  - id: FINDING-001
    code: MISSING_NEGATIVE_COVERAGE
    dimension: negative_testing
    severity: major
    message: Proposition P-019 has no negative-path coverage.
    related:
      - P-019

recommendations:
  - Add at least one invalid-signature scenario for P-019.

missing_evidence:
  - proposition: P-027
    evidence_type: runtime_oracle

deviations: []

protocol:
  version: 2.0.0
```

BUGate 到 AssessmentResult 为止。

随后宿主可以自行决定：

```text
continue
rework
retry
escalate
block
request_human_review
```

这些都不是 BUGate 的职责。

---

## 13. “可执行 Protocol”的定义

BUGate 2.0 所谓“可执行”，不是：

> BUGate 自动执行 Agent。

而是：

> **方法论规则可以被机器解释、验证和评估。**

例如：

```text
“所有 HIGH risk proposition 必须具有测试覆盖”
```

不能只存在 Markdown 中。

应该成为机器可执行规则：

```yaml
id: high_risk_coverage

applies_to:
  proposition.risk: high

assertion:
  testcase_count:
    min: 1
```

因此：

```text
Methodology
      ↓
Machine-readable Rule
      ↓
Evaluator
      ↓
AssessmentResult
```

这就是 Executable Methodology。

---

## 14. 三层架构

BUGate 2.0 建议形成三层：

```text
Protocol
   │
   ├── Method
   ├── Schema
   ├── Artifact
   ├── Evidence
   ├── Claim
   └── Assessment Rules

Engine
   │
   ├── Parser
   ├── Validator
   └── Evaluator

Views / Adapters
   │
   ├── Agent Skill
   ├── Documentation
   ├── CLI
   ├── Claude
   ├── Codex
   ├── Pi
   └── CI
```

其中：

> **Protocol 是唯一规范事实源。**

Engine 解释 Protocol。

Skill / Docs / CLI / Adapter 是 Protocol 的不同 View。

---

## 15. SKILL.md 的重新定位

BUGate 1.x 中 `SKILL.md` 承担了大量 normative specification。

BUGate 2.0 后：

```text
Protocol
   ↓
Agent-facing View
   ↓
SKILL.md
```

Skill 应帮助 Agent理解：

- 当前质量目标是什么；
- 应考虑哪些维度；
- 需要产生什么 Artifact；
- 需要收集什么 Evidence；
- 常见遗漏是什么；
- 如何形成 Claim。

真正 normative 的规则位于 `protocol/`。

因此：

> **Skill 是 Protocol 的 Agent UX，而不是 Protocol 本体。**

---

## 16. 推荐目录结构

建议 BUGate 2.0 逐步重构为：

```text
BUGate/

  protocol/
    v2/
      manifest.yaml

      methods/
        requirement_readiness.yaml
        business_understanding.yaml
        testability.yaml
        test_design.yaml
        implementation.yaml
        execution.yaml
        diagnosis.yaml
        knowledge.yaml

      schemas/
        artifact.schema.json
        evidence.schema.json
        claim.schema.json
        assessment_request.schema.json
        assessment_result.schema.json
        profile.schema.json

      artifacts/
        business_brief.yaml
        testability.yaml
        inventory.yaml
        test_cases.yaml
        adversarial_cases.yaml
        execution_report.yaml
        knowledge_update.yaml

      rules/
        traceability.yaml
        oracle_coverage.yaml
        risk_coverage.yaml
        negative_testing.yaml
        evidence_quality.yaml

      reason_codes.yaml

  engine/
    parser/
    validators/
    evaluator/
    rules/

  profiles/
    schema/
    examples/

  views/
    skills/
      bugate/
        SKILL.md
        references/
    docs/

  adapters/
    claude/
    codex/
    pi/
    ci/

  compatibility/
    v1/
```

目录结构表达清晰的依赖方向：

```text
Protocol
   ↓
Engine
   ↓
Views / Adapters
```

反向依赖禁止。

---

## 17. Profile 的重新定义

`bugate.profile.yaml` 继续存在，但应大幅瘦身。

Profile 只描述：

> **SUT-specific methodology binding**

示例：

```yaml
apiVersion: bugate.io/v2
kind: Profile

sut:
  name: Hyperchain

domains:
  - consensus
  - transaction
  - storage

test_layers:
  unit:
    available: true
  api:
    available: true
  cluster:
    available: true

evidence_sources:
  logs:
    type: filesystem
  metrics:
    type: prometheus

risk_extensions:
  - consensus_safety
  - consensus_liveness
  - epoch_transition
```

Profile 不再承载：

- Agent Runtime；
- Claude / Codex session；
- role process；
- worker command；
- retry；
- workflow state；
- checkpoint；
- process recovery。

这些信息属于 HyperTest / Runtime Adapter。

---

## 18. 现有 Artifact 与 Evaluator 的迁移

### 18.1 Artifact Template

现有 01–05 Template 全部保留。

定位从：

> gate 文件本身

调整为：

> Protocol Artifact 的默认 human-readable serialization。

### 18.2 `check_bugate_brief_semantics.py`

保留能力，升级为：

```text
BusinessUnderstandingEvaluator
```

### 18.3 `check_bugate_layer2_semantics.py`

保留能力，升级为：

```text
TestabilityEvaluator
```

### 18.4 `check_bugate_inventory_semantics.py`

保留能力，升级为：

```text
TestDesignEvaluator
```

### 18.5 `check_bugate_v13_semantics.py`

保留其组合评估能力，重新定位为：

```text
CompositeAssessment
```

不再承担 runtime unlock 语义。

### 18.6 统一 Engine Contract

所有 evaluator 最终统一成：

```python
AssessmentResult evaluate(AssessmentRequest)
```

旧脚本 CLI 在迁移期作为兼容 Adapter。

---

## 19. sdtd_orchestrator.py 的终态

### `--init`

可以拆分为纯 Artifact Tooling，例如：

```text
bugate artifact init
```

该能力不涉及 Agent orchestration，可以保留。

### `--auto`

BUGate 2.0 Core 移除。

当前能力包括：

- peer dispatch；
- semantic checker 顺序调度；
- artifact generation sequencing；
- adversarial worker dispatch；
- post-run sequencing；
- self-heal loop。

这些职责迁移到：

- Agent Harness；
- HyperTest Runtime；
- LangGraph；
- 其他宿主 Runtime。

1.x 兼容期可冻结到：

```text
compatibility/v1/
```

但不得继续扩展为第二套 Workflow Runtime。

---

## 20. Multi-view / Adversarial 的拆分

BUGate 保留：

- 为什么需要 independent view；
- 什么风险等级建议 independent review；
- reviewer 应关注什么；
- review Artifact 应包含什么；
- 什么叫 divergence；
- 什么叫 evidence-backed review；
- adversarial quality dimensions。

BUGate 移出：

- 如何启动 Claude；
- 如何启动 Codex；
- 开几个 worker；
- worker 并行还是串行；
- dispatch failure 如何 retry；
- 如何等待 worker；
- 如何恢复 worker session。

因此：

```text
Independent Review Methodology → BUGate
Worker Dispatch                → Runtime
```

---

## 21. Physical Write Guard 的处理

现有 `check_bugate.py` 同时包含质量判断与 Runtime Enforcement。

BUGate 2.0 将两者拆开。

Core 保留：

```text
assess_precode_readiness()
```

返回 AssessmentResult。

是否根据该结果禁止 implementation，由宿主决定。

例如：

```text
BUGate:
precode quality = incomplete

HyperTest:
therefore I will not schedule implementation
```

而不是：

```text
BUGate:
BLOCK TOOL
```

Claude / Codex Hook 版本可以在 `adapters/` 或 `compatibility/v1/` 中继续存在，但不得被视为 BUGate Core 语义。

---

## 22. Role Governance 的处理

现有 designer / implementer / reviewer 体系包含两类能力，必须拆分。

### 保留：方法论语义

例如：

- 高风险实现应接受独立 Review；
- reviewer 应与 producer 保持适当独立性；
- Review 必须引用原始 Artifact / Evidence；
- reviewer 不应覆盖原始失败事实；
- human review 的适用条件。

这些属于 Testing Methodology。

### 移出：Runtime 语义

例如：

- session identity；
- environment variable；
- role process；
- session launcher；
- handoff authorization；
- file permission；
- Tool access；
- process lifecycle。

这些属于 HyperTest / Agent Runtime。

因此 `role_governance.py` 不再整体保留为 Core 模块，应逐步拆出：

```text
Independent Review Method
Review Artifact / Evidence Schema
Assessment Rules
```

Runtime / session 部分迁出。

---

## 23. Receipt / Lineage 的处理

BUGate 2.0 不再使用 Authorization Receipt 来决定 Agent 是否能够执行动作。

但仍应保留 Assessment Provenance，用于：

- audit；
- reproducibility；
- traceability；
- protocol regression；
- quality history。

建议重新定义为：

```yaml
apiVersion: bugate.io/v2
kind: AssessmentRecord

metadata:
  id: ASR-001

assessment:
  ref: AS-001

method:
  id: test_design
  version: 2.0.0

claim:
  ref: CL-001

artifact_hashes:
  - ...

evidence_hashes:
  - ...

result:
  complete: false

protocol:
  version: 2.0.0

timestamp: ...
```

其含义从：

> Why this action was allowed

改变为：

> Why this quality conclusion was produced

Lineage 如果保留，应成为 Assessment / Evidence Provenance，而不是 Workflow State Machine。

---

## 24. Memory Bus 的处理

BUGate 2.0 Core 不拥有 Agent Memory Implementation。

因此 `memory_bus.py` 不应作为核心 runtime dependency。

BUGate 可以继续定义：

- Knowledge Artifact；
- Finding；
- Lesson；
- Reusable Pattern；
- Known Risk；
- Test Heuristic；
- Experience Promotion Methodology。

至于保存到：

```text
SQLite
Vector DB
MCP Memory
HyperTest Memory
External Knowledge Store
```

由宿主决定。

---

## 25. Self-Healing 的处理

BUGate 保留 Self-Healing Methodology：

- 如何区分 SUT defect 与 test defect；
- attribution 必须需要什么 Evidence；
- 什么情况下 evidence insufficient；
- 什么情况下测试资产可以被认为过期；
- 修复后需要重新验证哪些质量维度；
- independent review 需要什么 Evidence。

BUGate 移出 Self-Healing Loop：

```text
triage
→ propose
→ edit
→ execute
→ retry
→ reviewer
→ repeat
```

循环属于 HyperTest / Agent Harness / Runtime。

---

## 26. BUGate 与 LangGraph 的最终关系

BUGate Core 完全不知道 LangGraph 是否存在。

LangGraph 可以：

```python
result = bugate.assess(request)

if not result.complete:
    route("rework")
```

也可以选择继续或升级。

这是 Runtime Decision。

未来将 LangGraph 替换成 Temporal、Pi Native Loop、Codex Runtime 或其他执行系统，BUGate Protocol 不应变化。

---

## 27. BUGate 与 Pi Agent 的关系

Pi 可以加载 BUGate 的 Agent-facing Skill / Protocol View。

然后 Planner Model（例如 Astra / Fable）依据 Protocol 自主完成任务拆分。

例如面对 Business Understanding，Agent 自己可以生成：

```text
WorkPackage A:
analyze PRD

WorkPackage B:
inspect implementation

WorkPackage C:
review historical defects
```

这些 WorkPackage 不属于 BUGate。

最终 Agent 提交：

```text
Artifact
Evidence
Claim
```

BUGate 只负责 Assessment。

---

## 28. HyperTest 推荐执行模型

```text
User Goal
   │
   ▼
HyperTest TaskSpec
   │
   ▼
Pi Agent
   │
   ├── load BUGate Protocol View
   │
   ▼
Astra / Fable
   │
   ▼
Autonomous Planning
   │
   ▼
WorkPackages
   │
 ┌─┴───────────────┐
 ▼                 ▼
DeepSeek        DeepSeek
Worker          Worker
 │                 │
 └───────┬─────────┘
         ▼
 Artifact + Evidence
         │
         ▼
       Claim
         │
         ▼
 BUGate Assessment
         │
         ▼
 AssessmentResult
         │
         ▼
HyperTest / Agent decides:
continue / rework / escalate / stop
```

最重要的是：

> **BUGate 不执行最后一行。**

---

## 29. Protocol Versioning

BUGate 2.0 正式进入 Protocol Versioning。

建议：

```text
bugate.io/v2
```

所有核心对象包含：

```yaml
apiVersion: bugate.io/v2
kind: ...
```

Protocol 版本使用 SemVer：

```text
2.0.0
2.1.0
2.1.1
3.0.0
```

规则：

- Patch：不改变质量语义；
- Minor：增加兼容字段、Rule 或 Method；
- Major：改变核心质量语义、Claim 语义或 Schema。

每次 Assessment 必须记录实际 Protocol Version。

---

## 30. Extension / Profile 机制

BUGate Core 不知道具体 SUT。

因此：

```text
BUGate Core
       +
SUT Profile
```

Profile 可以：

- 增加 quality dimensions；
- 增加 Artifact field；
- 增加 Evidence Source；
- 增加 domain heuristics；
- 收紧 Core requirement；
- 定义领域 risk taxonomy。

但不能：

- 修改 Core Claim 语义；
- 删除 Core quality invariant；
- 引入 Runtime / Harness 耦合；
- 把具体 Agent workflow 写回 Core。

例如 Hyperchain Profile 可以增加：

```text
consensus safety
consensus liveness
epoch transition
Byzantine behavior
```

数据库 Profile 可以增加：

```text
backup / restore
replication
failover
consistency
```

---

## 31. Protocol Conformance

BUGate 2.0 必须新增 Protocol Conformance 能力。

建议 CLI：

```text
bugate protocol validate
bugate artifact validate
bugate assess
bugate explain
bugate inspect
```

例如：

```bash
bugate protocol validate

bugate inspect method testability

bugate artifact validate testability.yaml

bugate assess   --method testability   --claim claim.yaml   --profile bugate.profile.yaml   --json

bugate explain MISSING_NEGATIVE_COVERAGE
```

Protocol Conformance 至少检查：

- Schema validity；
- Method reference validity；
- Rule reference validity；
- Artifact Type validity；
- Evidence Type validity；
- Reason Code validity；
- Protocol Version consistency；
- Profile extension legality。

不应出现：

```text
run-agent
run-worker
auto
retry
resume
schedule
```

这类 Runtime 命令。

---

## 32. 推荐 Reason Code 体系

AssessmentResult 应尽量避免把所有问题降级成自由文本。

建议定义稳定 Reason Code，例如：

```text
MISSING_PROPOSITION
MISSING_ORACLE
UNTRACEABLE_PROPOSITION
INSUFFICIENT_EVIDENCE
MISSING_NEGATIVE_COVERAGE
MISSING_BOUNDARY_COVERAGE
HIGH_RISK_UNCOVERED
AMBIGUOUS_EXPECTATION
TEST_LAYER_UNJUSTIFIED
UNRESOLVED_CRITICAL_GAP
STALE_ARTIFACT
UNSUPPORTED_CLAIM
PROFILE_CONFLICT
```

自由文本只作为 explanation。

这样 HyperTest、Dashboard、CI、Agent 都可以稳定消费 AssessmentResult。

---

## 33. Protocol 与 Skill 一致性

BUGate 2.0 必须防止：

```text
Protocol 说 A
SKILL.md 说 B
Docs 说 C
```

因此建立 conformance test：

```text
Protocol → Skill projection
Protocol → Docs projection
Protocol → CLI help projection
```

第一阶段不要求完全自动生成，但必须至少有测试验证：

- Method ID 一致；
- Required Artifact 一致；
- Quality Dimension 一致；
- Reason Code 一致；
- Version 一致。

长期目标是从 Protocol metadata 生成大部分 Agent-facing Reference。

---

## 34. Migration Strategy

BUGate 2.0 不推倒重写，按五阶段演进。

### Phase 0 — Freeze Orchestration Growth

立即停止给以下组件增加新的通用 Orchestration 能力：

- `sdtd_orchestrator.py --auto`；
- role runtime；
- CLI worker dispatch；
- memory orchestration；
- self-healing execution loop；
- process retry / recovery。

1.x 进入 maintenance / compatibility mode。

### Phase 1 — Protocol Extraction

创建：

```text
protocol/v2/
```

先定义：

- MethodSpec；
- Artifact；
- Evidence；
- Claim；
- AssessmentRequest；
- AssessmentResult；
- Profile。

此阶段现有 Script 行为不变。

### Phase 2 — Evaluator Unification

逐步把：

- `check_bugate_brief_semantics.py`；
- `check_bugate_layer2_semantics.py`；
- `check_bugate_inventory_semantics.py`；
- `check_bugate_v13_semantics.py`；

统一到 `engine/evaluator`。

所有 evaluator 输出统一 AssessmentResult。

旧 CLI 作为 Adapter。

### Phase 3 — Skill Projection

重构 `SKILL.md`：

从 normative specification 转为 Agent-facing Protocol View。

增加 Protocol ↔ Skill consistency tests。

### Phase 4 — Runtime Extraction

逐步移出 Core：

- `sdtd_orchestrator --auto`；
- peer process dispatch；
- role process management；
- physical write guard enforcement；
- retry / resume；
- self-healing loop；
- agent memory runtime。

HyperTest / Harness / Runtime 接管。

### Phase 5 — BUGate 2.0 Release

最终 BUGate Core 中不再存在：

- generic agent orchestration；
- generic workflow state；
- agent runtime management；
- generic scheduler；
- process checkpoint；
- tool enforcement。

1.x compatibility layer 在明确 release window 后再决定是否删除。

---

## 35. 现有代码去留总表

| 当前能力 | BUGate 2.0 |
|---|---|
| `SKILL.md` | 保留，改为 Protocol View |
| Business Brief Method | 保留 |
| Testability Method | 保留 |
| Inventory Method | 保留 |
| Adversarial Method | 保留 |
| Artifact templates | 保留，作为默认 serialization |
| Semantic validators | 保留并统一到 Evaluator |
| Evidence model | 强化为一等 Protocol Object |
| Claim | 新增 |
| AssessmentRequest / Result | 新增 |
| Profile | 保留并瘦身 |
| `sdtd_orchestrator --init` | 拆成 Artifact Tooling |
| `sdtd_orchestrator --auto` | 移出 Core |
| Claude / Codex worker dispatch | 移出 Core |
| Physical write guard | 移出 Core；可留 Adapter / compatibility |
| Role process / session | 移出 Core |
| Authorization Receipt | 退出 Core |
| Assessment provenance | 保留 |
| Memory Service | 移出 Core |
| Knowledge methodology | 保留 |
| Self-healing methodology | 保留 |
| Self-healing loop | 移出 Core |
| LangGraph integration | 禁止进入 Core |
| Pi integration | Adapter only |
| Claude / Codex integration | Adapter only |
| CI integration | Adapter only |

---

## 36. 首批工程 Work Packages

建议 BUGate 2.0 按以下工作包推进。

### BG2-0 — Protocol Skeleton

建立：

```text
protocol/v2/
engine/
views/
adapters/
compatibility/
```

不移动旧代码，只建立目标结构与 manifest。

### BG2-1 — Core Schemas

实现七个核心 Schema：

- MethodSpec；
- Artifact；
- Evidence；
- Claim；
- AssessmentRequest；
- AssessmentResult；
- Profile。

完成 Schema validation tests。

### BG2-2 — Business Understanding Migration

把现有 Layer 1 方法、Template、Semantic Gate 映射到：

```text
business_understanding MethodSpec
business_brief Artifact
BusinessUnderstandingEvaluator
```

证明新旧结果具有语义等价性。

### BG2-3 — Testability Migration

映射 Layer 2：

```text
testability MethodSpec
testability Artifact
TestabilityEvaluator
```

### BG2-4 — Test Design Migration

映射 Layer 3 / 3A / 3B：

```text
test_design MethodSpec
inventory Artifact
test_case Artifact
adversarial_review Artifact
TestDesignEvaluator
```

### BG2-5 — Evidence / Claim Loop

实现完整：

```text
Artifact + Evidence
       ↓
Claim
       ↓
AssessmentRequest
       ↓
AssessmentResult
```

这是 BUGate 2.0 第一个真正的 end-to-end milestone。

### BG2-6 — Skill Projection

改造 SKILL.md 与 references，使其明确：

> Agent 自主决定执行路径，BUGate 提供 Method / Artifact / Evidence / Assessment 规范。

建立 consistency tests。

### BG2-7 — Runtime Extraction

冻结并迁移：

- orchestrator auto；
- worker dispatch；
- role process；
- write enforcement；
- memory runtime；
- self-healing loop。

HyperTest 提供参考宿主。

### BG2-8 — Compatibility and Release

验证：

- Claude Code adapter；
- Codex adapter；
- Pi adapter；
- CI adapter；
- 1.x compatibility；
- cross-runtime protocol consistency。

随后发布 BUGate 2.0。

---

## 37. 非目标

BUGate 2.0 明确不建设：

- 自己的 Agent Harness；
- 自己的 LangGraph replacement；
- 自己的 durable workflow engine；
- 自己的 scheduler；
- 自己的 distributed queue；
- 自己的 context manager；
- 自己的 model router；
- 自己的 subagent protocol；
- 通用 Tool permission system；
- 通用 process supervisor。

如果某个需求本质上回答的是：

> “Agent / Worker 下一步应该怎么运行？”

默认不属于 BUGate。

如果某个需求回答的是：

> “一个测试质量结论需要满足什么？”

才属于 BUGate。

---

## 38. Architecture Decision Test

未来任何新功能进入 BUGate 前，使用以下判定。

### Question A

如果 GPT / Claude / DeepSeek 下一代模型聪明 10 倍，这段代码还需要吗？

如果它负责：

- task decomposition；
- next-step selection；
- tool choice；
- worker selection；
- local replanning；

答案通常是否，应放到 Agent / Harness。

### Question B

如果换掉 Pi、Claude Code、Codex 和 LangGraph，这条测试方法规则仍成立吗？

如果是，适合进入 BUGate。

### Question C

它是在描述“怎么运行”，还是“什么叫做好”？

- 怎么运行 → Runtime / Harness；
- 什么叫做好 → BUGate。

---

## 39. BUGate 2.0 完成标准

只有满足以下条件，才能称 BUGate 已真正 Protocol 化：

1. BUGate Core 不依赖 LangGraph、Pi、Claude Code、Codex；
2. Core 无 Agent Scheduling / Worker Dispatch / Retry Loop；
3. 核心 Method 均存在机器可读定义；
4. 核心 Artifact 均有 Schema；
5. Evidence 是独立 Protocol Object；
6. Agent 可以提交结构化 Claim；
7. BUGate 可以针对 Claim 返回结构化 AssessmentResult；
8. Evaluator 不调度 Agent、不修改 SUT、不控制 Runtime；
9. Skill 与 Protocol 存在自动一致性检查；
10. Claude Code、Codex、Pi 可以消费同一份 Protocol；
11. Runtime 可以根据 AssessmentResult 自行 continue / rework / block / escalate；
12. BUGate 不控制 Runtime 最终选择；
13. 完全陌生的 Agent 只要实现 Protocol Adapter 就能使用 BUGate；
14. Protocol Version 与 Assessment Provenance 可追溯；
15. SUT-specific 扩展只通过 Profile / Extension 进入。

---

## 40. 最终目标架构

```text
                     BUGate 2.0

              Executable Testing Protocol
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼

     Methods          Artifacts        Evidence

        │                │                │
        └────────────────┼────────────────┘
                         ▼

                      Claims
                         │
                         ▼

                    Assessment
                         │
                         ▼

                 AssessmentResult


                    NO AGENT LOOP
                    NO WORKFLOW
                    NO SCHEDULER
                    NO TOOL BLOCKING
                    NO RETRY
                    NO CHECKPOINT
```

外部系统：

```text
                       HyperTest

                          │
                          ▼

                       Pi Agent

                          │
                   BUGate Skill/View
                          │
                          ▼

                  Astra / Fable
                  autonomous plan

                          │
                          ▼

                  DeepSeek Workers

                          │
                          ▼

              Artifact + Evidence + Claim

                          │
                          ▼

                    BUGate 2.0

                          │
                          ▼

                 AssessmentResult

                          │
                          ▼

                HyperTest Runtime
             decides what happens next
```

---

## 41. 最终架构裁决

BUGate 1.x 的历史价值是：

> 在 Agent 自主能力还不充分的时期，用 Workflow、Hook、Gate 和 Governance 把测试方法论强制嵌入 Agent 行为。

BUGate 2.0 的价值将变成：

> 在 Agent 已经能够自主规划和执行的时代，把资深测试工程师的方法论编码成一种模型无关、运行时无关、可机器理解、可验证、可演进的 Testing Protocol。

因此 BUGate 不再尝试控制 Agent。

BUGate 应做到：

> **教育 Agent、定义测试开发方法、描述优秀测试的标准、规范 Artifact 与 Evidence，并提供机器可执行的质量评估。**

最终原则：

> **不要编排 Agent 的行为。**  
> **不要替 Agent 决定下一步。**  
> **不要通过 BUGate 控制 Tool。**  
> **把测试工程方法论变成 Protocol，让任何 Agent 都知道“什么才算做好了测试开发”。**

---

## 42. 一句话定义

> **BUGate is a SUT-neutral executable testing methodology protocol that defines what good autonomous test development looks like through machine-readable methods, artifacts, evidence and quality assessments, while leaving planning, orchestration and execution entirely to the host agent runtime.**

中文：

> **BUGate 是一套与被测系统、模型和运行时无关的可执行测试开发方法论协议，通过机器可读的方法、工件、证据和质量评估定义“优秀的自主测试开发应该是什么样”，而将规划、编排与执行完全交给宿主 Agent Runtime。**

BUGate 2.0 与 HyperTest 的最终边界：

```text
BUGate defines GOOD.
HyperTest defines WHAT.
Agent decides HOW.
Runtime ensures CONTINUITY.
```