# BUGate Host Adapters

This directory contains **host-specific integration only**.

BUGate Core remains SUT-, model-, harness-, and runtime-neutral. An adapter may
bind BUGate Protocol to a particular Agent Harness lifecycle, but it must not
move host-specific session, orchestration, retry, checkpoint, or tool-policy
semantics into the Protocol.

Accepted direction:
- [BUGate 2.0 Host Adapter Guide](../docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md)
- [ADR-BUGATE-009 — PowerContext-style Host Integration](../docs/qa-methodology/BUGATE_HOST_INTEGRATION_POWERCONTEXT_ADR.zh-CN.md)

The engineering model intentionally mirrors PowerContext's multi-host integration architecture:

```text
catalog + capabilities + self-contained host adapters
+ setup selector + doctor matrix + shared conformance
```

BUGate-specific implementation is independent; adapters consume BUGate's own
`PreparedProtocolContext` rather than PowerContext memory APIs.

## Planned hosts

- `claude-code/` — first implementation target.
- `codex/` — first implementation target.
- `pi/` — reserved for future HyperTest integration.
- `deepseek-harness/` — reserved for future HyperTest integration.

All hosts should consume the same Protocol Bundle, ProtocolBinding semantics,
Artifact/Evidence/Claim contracts, and AssessmentResult.


## Repository contract

- `catalog.yaml` — first-class Host catalog.
- `schema/host-manifest.schema.json` — Host manifest schema.
- `<host>/manifest.yaml` — one Host's capability declaration.
- `CONFORMANCE.md` — shared behavioral contract.
- `../views/` — canonical Host Projection model.

Target CLI:

```text
bugate setup <host>
bugate setup select --host ...
bugate doctor <host>
bugate doctor integrations
bugate protocol prepare --task ... --json
```
