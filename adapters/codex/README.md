# Codex Adapter

**Status:** planned for BUGate 2.0.

This adapter will connect the runtime-neutral BUGate Protocol to Codex.

Target responsibilities:

- install a minimal always-on `AGENTS.md` bootstrap;
- expose the canonical BUGate Agent Skill view;
- resolve `.bugate/protocol.lock.json`;
- use host lifecycle integration for startup, resume/compact, and subagent
  protocol hydration;
- render the active Protocol Context Capsule independently for each Agent;
- submit Artifact + Evidence + Claim to BUGate Assessment.

It must not make Codex hook or session semantics part of BUGate Core.

See:
[BUGate 2.0 Host Adapter Guide](../../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md).
