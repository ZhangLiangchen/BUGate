# BUGate DSH Conformance

English | [简体中文](CONFORMANCE.zh-CN.md)

The DeepSeek Harness adapter follows the behavioral contract below. The HC IDs
are retained for evidence traceability; they do not imply support for other hosts.
Canonical machine rules remain under `protocol/v2/`.

## Required contract

| ID | Contract |
|---|---|
| HC-01 | Setup is idempotent. |
| HC-02 | The canonical BUGate Protocol is discoverable. |
| HC-03 | TestTaskWorkspace identity resolves consistently. |
| HC-04 | Exact ProtocolBinding version/digest is preserved. |
| HC-05 | PreparedProtocolContext validates before injection. |
| HC-06 | Protocol context is present before relevant Agent reasoning. |
| HC-07 | Long-running sessions do not rely on one-time Skill memory. |
| HC-08 | Compaction/restart rehydrates when the Host exposes a recovery lifecycle. |
| HC-09 | Child Agents inherit the same Binding when the Host supports child agents. |
| HC-10 | Protocol version/digest mismatch fails visibly. |
| HC-11 | Stale Host Projection is detected. |
| HC-12 | Explicit BUGate assessment remains reachable. |
| HC-13 | Adapter code cannot change MethodSpec/Assessment semantics. |
| HC-14 | Rollback removes only BUGate-owned projection/integration state. |
| HC-15 | Context Runtime content never overrides Protocol or live Workspace authority. |

## Current scope

The only product host is DeepSeek Harness. HC-15 remains applicable as an authority
invariant: historical text never overrides current task facts, although there is
no Context Runtime service. Shared multi-host setup and prior compatibility tests
are retired. The [historical evidence index](../docs/README.md#verification-records)
retains those past decisions separately from current adapter behavior.

The manifest records supported capabilities and explicit limits. Setup/doctor/
remove target one explicit DSH profile. Doctor distinguishes host availability,
plugin/resources, projection, binding and observed hydration. A successful package
installation is not evidence of an active governed session.
