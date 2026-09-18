# PowerContext Provider

**Status:** preferred initial Context Runtime provider / reference architecture.

BUGate 2.0 adopts the architectural concepts demonstrated by
[OceanBase PowerContext](https://github.com/oceanbase/powercontext), including:

- Scope
- Source → Artifact → Revision
- bounded PreparedContext
- Memory
- Handoff
- reviewed Experience
- managed Skill candidates
- evidence/provenance-preserving promotion
- integrations for multiple Agent hosts

Boundary:

```text
BUGate Protocol Core
        X
        | no SDK dependency
        X
ContextProvider boundary
        |
        v
PowerContext
```

PowerContext is not a normative dependency of BUGate Protocol. A host may use
another ContextProvider or no Context Runtime at all.

The provider integration should eventually map:

```text
BUGate Protocol Capsule     -> methodology context
PowerContext PreparedContext -> historical/reusable context
TestTaskWorkspace           -> current task facts
```

into one host invocation without merging their authority levels.

See:
[BUGate 2.0 Context Runtime Guide](../../../docs/qa-methodology/BUGATE_2_0_CONTEXT_RUNTIME_GUIDE.zh-CN.md).
