# BUGate 2.0 — Context Runtime Integration Guide

- **状态：** Accepted Direction
- **日期：** 2026-09-18
- **Normative ADR：** [ADR-BUGATE-008](BUGATE_CONTEXT_RUNTIME_ADR.zh-CN.md)
- **Reference provider：** [OceanBase PowerContext](https://github.com/oceanbase/powercontext)

## 1. Context Runtime 的目标

BUGate 1.x 的 Memory Bus 解决了“Agent 如何跨会话记住事情”。

BUGate 2.0 的 Context Runtime 解决更完整的问题：

```text
What historical context should this Agent receive,
from which scope,
with which provenance,
under which budget,
and how should ongoing work be handed off?
```

因此：

```text
Memory Store
     !=
Context Runtime
```

Context Runtime 至少覆盖：

- Memory；
- Scope；
- Context Pack；
- Handoff；
- Source / Artifact / Revision；
- Experience；
- Skill Candidate；
- Context provenance；
- cross-session continuity；
- cross-agent continuity。

## 2. 双上下文模型

每次 Agent invocation 推荐形成：

```text
Current user/task instruction
        +
BUGate Protocol Capsule
        +
Context Runtime Context Pack
        +
Current Workspace / repository / environment facts
        |
        v
      Agent
```

其中：

### Protocol Capsule

回答：

> 什么叫做得好？

### Context Pack

回答：

> 以前发生过什么、有什么值得参考？

### Workspace / live evidence

回答：

> 现在实际上是什么情况？

三者不能互相替代。

## 3. Context Pack 最小 Contract

建议：

```yaml
apiVersion: bugate.io/context/v1
kind: ContextPack

scope:
  id: project:hyperchain

subject:
  task: REQ-001

budget:
  max_bytes: 8000

items:
  - type: memory
    citation: memory/M-001@3
    trust: historical
    summary: ...
  - type: experience
    citation: experience/E-004@2
    trust: reviewed
    summary: ...
  - type: handoff
    citation: handoff/H-002@1
    trust: historical
    summary: ...
```

注意：这个 schema 属于 Context integration contract，不进入 BUGate Protocol v2 的核心 MethodSpec / Assessment 语义。

## 4. PowerContext Mapping

BUGate Context Runtime 与 PowerContext 的概念映射：

| BUGate 2.0 Context Runtime | PowerContext |
|---|---|
| Scope | Scope |
| Context source | Source |
| Versioned context object | Artifact / Revision |
| Historical memory | Memory |
| Prepared Context Pack | PreparedContext |
| Work transfer | Handoff |
| Reusable reviewed lesson | Experience |
| Reusable method package | managed Skill |
| Promotion proposal | Candidate |
| Review / approval | Candidate Review |

BUGate 不 fork 这些概念的实现。

## 5. Context selection

Context Runtime 应优先选择：

1. 与 current task / subject 直接相关；
2. 与当前 Scope 相符；
3. 未 retire / obsolete；
4. 有 exact citation；
5. 经 review 的 Experience 优先于普通 Memory；
6. 最近但不重复；
7. 总量满足 Context budget。

不得因为 embedding 相似度高就无界塞入上下文。

## 6. Handoff

Handoff 是工作连续性协议，不是长期 Memory。

推荐内容：

```yaml
objective:
verified_progress:
evidence_refs:
changed_artifacts:
known_omissions:
blockers:
next_action:
workspace_ref:
protocol_binding:
```

其中 `workspace_ref` 指向 TestTaskWorkspace。

真正的当前任务事实仍在 Workspace。

## 7. Workspace → Context promotion

任务过程中：

```text
TestTaskWorkspace
   |
   +-- artifacts
   +-- evidence
   +-- assessments
   +-- knowledge artifact
   |
   v
promotion candidate
   |
   v
Context Runtime Memory / Experience
```

禁止默认把完整 Workspace 自动写入长期 Memory。

## 8. Experience → BUGate evolution

```text
approved Experience
      |
      v
BUGate promotion review
      |
      +-- remain SUT-local Experience
      +-- become Profile heuristic
      +-- become Method consideration
      +-- become Protocol rule
```

最后一步必须有独立 BUGate protocol review。

PowerContext managed Skill 不自动等价于 BUGate normative Skill / MethodSpec。

## 9. Host Integration

### Claude Code

```text
CLAUDE.md bootstrap
+ BUGate Skill
+ Protocol Capsule
+ PowerContext / ContextProvider Pack
```

### Codex

```text
AGENTS.md bootstrap
+ BUGate Skill
+ Protocol Capsule
+ ContextProvider prompt/session integration
```

### Pi / DeepSeek Harness

预留给 HyperTest。

两者都使用：

```text
Protocol Capsule + Context Pack
```

而不是分别发明记忆格式。

## 10. Context Runtime failure

自动 recall：

```text
fail-open
```

显式 durable write：

```text
fail-visible
```

Assessment：

```text
independent of Context Runtime,
unless the Claim explicitly references Context Artifact evidence
```

## 11. Legacy Memory Bus

BUGate 1.x：

```text
mcp-memory-service
+ scripts/memory_bus.py
+ bin/memory-bus-*
```

保持兼容。

BUGate 2.0：

```text
no new semantics added to legacy Memory Bus
```

未来迁移时建立：

```text
legacy namespace/tag
    ->
Context Scope

memory entry
    ->
Memory Artifact Revision

handoff note
    ->
Handoff

confirmed finding/decision
    ->
Memory / Experience Candidate
```

## 12. 首批实施任务

```text
CR-0 freeze legacy Memory Bus growth
CR-1 define provider-neutral Context contracts
CR-2 add PowerContext provider docs/integration
CR-3 Claude Code + Codex dual hydration
CR-4 Knowledge Artifact -> Experience candidate
CR-5 legacy migration tooling
CR-6 HyperTest Pi / DSH integration
```

## 13. 核心不变量

1. BUGate Core 始终无状态；
2. Workspace 是 current task truth；
3. Memory 是 historical context；
4. Protocol Capsule 与 Context Pack 分离；
5. Context Runtime 可替换；
6. Context outage 不阻塞 Protocol；
7. historical context 永远不能覆盖 live evidence；
8. Promotion 必须保留证据；
9. Context Pack 必须有预算；
10. PowerContext 是首选 Provider，而不是 BUGate Protocol 的硬依赖。
