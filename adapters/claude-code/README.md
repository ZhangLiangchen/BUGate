# Claude Code Adapter

**Status:** planned for BUGate 2.0.

This adapter will connect the runtime-neutral BUGate Protocol to Claude Code.

Target responsibilities:

- install a minimal always-on `CLAUDE.md` bootstrap;
- expose the canonical BUGate Agent Skill view;
- resolve `.bugate/protocol.lock.json`;
- render and rehydrate the active Protocol Context Capsule;
- preserve binding across long sessions, compaction/resume, and delegated work
  where the host lifecycle permits it;
- submit Artifact + Evidence + Claim to BUGate Assessment.

It must not make Claude Code lifecycle semantics part of BUGate Core.

See:
[BUGate 2.0 Host Adapter Guide](../../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md).
