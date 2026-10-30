# BUGate charter

English | [简体中文](CHARTER.zh-CN.md)

BUGate is a SUT-neutral testing quality protocol, not an execution orchestrator.
The shared TypeScript core assesses versioned facts; the only product adapter is native
DeepSeek Harness. Task work happens in an external test repository with an
explicit Profile and exact protocol binding. Core development never mounts a
real SUT into this repository.

The accepted architecture has a shared TypeScript evaluation core, unified Node
CLI and native Cordis service/plugin. Official DSH Agent Team owns reviewer
membership, task boards, messages and lifecycle; BUGate binds persisted host
observations to review outputs and evaluates their applicability. Implementation
is in progress under the 2026-10-30 request; final cutover remains conditional on
exact-artifact acceptance. See the [migration plan](docs/plans/TYPESCRIPT_CORDIS.md).
This does not transfer Agent-loop ownership or restrict external SUT test languages.

The 2026-10-29 scope decision retires all previous compatibility, multi-host,
role-daemon, memory-runtime and imported-installer requirements. Git history and
published tags/releases remain available as history. No old-format converter or
automatic migration is part of the current product.

Quality invariants continue: source-backed business understanding, testability,
Oracle/case traceability, snapshot freshness, evidence provenance, honest failure
classification and scoped completion. DSH owns scheduling and lifecycle. No
mutable current-stage authority or mandatory memory service is introduced.

Acceptance breadth follows the user’s goals and accepted requirements. Evidence
depth must be sufficient within that scope. A first-class contract pins those
goals; suggestions cannot become execution or completion obligations without
a source-backed user change. Execution completion and functional acceptance
remain separate conclusions.
