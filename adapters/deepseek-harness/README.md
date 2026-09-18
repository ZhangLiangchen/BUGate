# DeepSeek Harness Adapter

**Status:** reserved; no implementation contract yet.

This directory is reserved for a possible DeepSeek Agent Harness used by
HyperTest.

BUGate intentionally makes no assumption today about the final DeepSeek
Harness API, session model, subagent model, lifecycle hooks, or tool interface.

A future adapter may:

- resolve the same BUGate ProtocolBinding used by other hosts;
- compile/hydrate the same MethodSpec-driven Protocol Context Capsule;
- preserve binding across the harness lifecycle;
- submit the same Artifact/Evidence/Claim contracts to BUGate Assessment.

DeepSeek-specific runtime semantics must remain in this adapter or HyperTest,
not in BUGate Core Protocol.

See:
[BUGate 2.0 Host Adapter Guide](../../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md).
