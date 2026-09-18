# BUGate Context Runtime

BUGate 2.0 Core is stateless and does not own long-term memory.

This directory is the **integration boundary** for optional Context Runtime
providers used by Agent hosts and, later, HyperTest.

Normative decisions:

- [ADR-BUGATE-008 — Memory Bus → Context Runtime](../docs/qa-methodology/BUGATE_CONTEXT_RUNTIME_ADR.zh-CN.md)
- [BUGate 2.0 Context Runtime Guide](../docs/qa-methodology/BUGATE_2_0_CONTEXT_RUNTIME_GUIDE.zh-CN.md)

The Context Runtime is responsible for historical and reusable context:

- Memory
- Scope
- Context Pack / Prepared Context
- Handoff
- versioned context Artifacts
- Experience / Skill candidate lifecycle
- cross-session and cross-agent continuity

It is **not** the source of truth for the current test task.
Current-task facts remain in TestTaskWorkspace.

Provider-specific SDKs and runtime semantics must stay below this boundary and
must never become dependencies of `protocol/` or the BUGate assessment engine.
