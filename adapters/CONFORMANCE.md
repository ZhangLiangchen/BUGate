# BUGate Host Adapter Conformance

Every first-class BUGate Host Adapter is evaluated against one shared behavioral
contract. Host-native APIs may differ; observable BUGate semantics must not.

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

## Capability states

A host manifest may declare a capability as:

- `required`
- `supported`
- `unsupported`
- `reserved`

`unsupported` requires a rationale. Missing declarations are invalid.

The suite tests semantics, not hook names. For example, one Host may hydrate
through `UserPromptSubmit`, another through `beforeAgentStart`, and another
through a model-step middleware. All satisfy the same
`automatic_hydration` contract if the prepared context is present before
relevant reasoning.

## Multi-host setup behavior

`bugate setup select --host ...` must isolate sibling failures. One selected
Host failing setup does not prevent another selected Host from being attempted.
The overall command fails when any selected Host fails.

Unselected Hosts are skipped even if their CLI is present.

## Doctor behavior

`bugate doctor integrations` treats a missing Host as non-fatal. A Host that
is present but has a broken BUGate integration is fatal.

A stable JSON report should distinguish:

- host presence;
- native CLI/package status;
- plugin/package status;
- projection status;
- ProtocolBinding status;
- hydration status.
