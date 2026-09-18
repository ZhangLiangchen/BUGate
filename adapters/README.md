# BUGate Host Adapters

This directory contains **host-specific integration only**.

BUGate Core remains SUT-, model-, harness-, and runtime-neutral. An adapter may
bind BUGate Protocol to a particular Agent Harness lifecycle, but it must not
move host-specific session, orchestration, retry, checkpoint, or tool-policy
semantics into the Protocol.

Accepted direction:
[BUGate 2.0 Host Adapter Guide](../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md).

## Planned hosts

- `claude-code/` — first implementation target.
- `codex/` — first implementation target.
- `pi/` — reserved for future HyperTest integration.
- `deepseek-harness/` — reserved for future HyperTest integration.

All hosts should consume the same Protocol Bundle, ProtocolBinding semantics,
Artifact/Evidence/Claim contracts, and AssessmentResult.
