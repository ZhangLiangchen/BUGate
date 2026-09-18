# Pi Agent Adapter

**Status:** reserved; no implementation contract yet.

This directory is reserved for possible HyperTest integration using Pi Agent as
the Agent Harness.

Boundary:

- Pi remains a generic Agent Harness.
- BUGate must not be hard-coded into Pi Core.
- A future HyperTest/Pi adapter may resolve ProtocolBinding, hydrate a BUGate
  Protocol Context Capsule, collect Artifact/Evidence/Claim, and invoke BUGate
  Assessment.
- Pi-specific context/session/tool APIs must not leak into BUGate Protocol.

Implementation starts only after HyperTest's Pi integration contract is stable.

See:
[BUGate 2.0 Host Adapter Guide](../../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md).
