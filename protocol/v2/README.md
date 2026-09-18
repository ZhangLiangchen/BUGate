# BUGate Protocol v2

This directory is the canonical machine-readable home of BUGate 2.0.

The protocol is stateless and Host-neutral.

Current first schema:

- `schemas/prepared_protocol_context.schema.json`

The Host integration architecture is defined by
[ADR-BUGATE-009](../../docs/qa-methodology/BUGATE_HOST_INTEGRATION_POWERCONTEXT_ADR.zh-CN.md).

Target dependency direction:

```text
Protocol
   -> Engine
      -> PreparedProtocolContext
         -> Host Adapter Projection
```

Host-local Skills, bootstrap files, plugins, and hook configuration are not
normative Protocol sources.
