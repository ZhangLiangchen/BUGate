# BUGate Host Adapters

English | [简体中文](README.zh-CN.md)

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

## Host status

- `claude-code/` — v2 migration target; existing legacy interfaces remain supported.
- `codex/` — v2 migration target; existing legacy interfaces remain supported.
- `pi/` — reserved for future HyperTest integration.
- `deepseek-harness/` — implemented native bundle for DSH `0.2.2-alpha.2`;
  see the [installation and usage guide](../docs/DEEPSEEK_HARNESS.md).
  Catalog availability remains `planned` until a public release; capability
  support is declared separately in its manifest.

All hosts should consume the same Protocol Bundle, ProtocolBinding semantics,
Artifact/Evidence/Claim contracts, and AssessmentResult.


## Repository contract

- `catalog.yaml` — first-class Host catalog.
- `schema/host-manifest.schema.json` — Host manifest schema.
- `<host>/manifest.yaml` — one Host's capability declaration.
- `CONFORMANCE.md` — shared behavioral contract.
- `../views/` — canonical Host Projection model.

Implemented DSH CLI:

```text
bugate setup deepseek-harness --project ... --package ... --dsh-profile ...
bugate doctor integrations --project ... --dsh-profile ...
bugate remove deepseek-harness --project ... --dsh-profile ... [--rollback]
bugate protocol prepare --task ... --json
```

The wider multi-host selector remains a separate migration target:

```text
bugate setup <host>
bugate setup select --host ...
bugate doctor <host>
bugate doctor integrations
bugate protocol prepare --task ... --json
```
