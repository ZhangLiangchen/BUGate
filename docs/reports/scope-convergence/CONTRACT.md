# Acceptance scope and convergence implementation contract

English | [简体中文](CONTRACT.zh-CN.md)

Frozen before implementation on 2026-10-29 against BUGate
`fe3e29b82059504cd47893b72005051489c0810f`. Source: the user's explicit
implementation request in this chat, sections I–XIII and SC-01–SC-18.
The repository was clean; HEAD and origin/main were equal. Existing sibling
worktrees remain untouched. DSH reports `0.2.2-alpha.2`; its installed package
types and lifecycle implementations must be verified in this run.

## Goal and authority

Implement a first-class versioned acceptance scope contract and convergence in
the host-neutral v2 evaluator, unified CLI and native DSH plugin. Preserve actual
business assertions and execution evidence. The existing inventory remains the
plan authority; do not introduce a competing scheduler or Agent loop.

Required delivery is the current code, reviewable commits, bilingual methods and
usage, independent installation bundle/checksums, actual runtime evidence and a
case-by-case acceptance report. No public release/tag publication is authorized.
The development acceptance is this contract and the matrix below; a new product
mechanism must not circularly require itself before it can be implemented.

## Invariants and observable oracles

| ID | Requirement | Observable oracle |
|---|---|---|
| R1 | User goals fix breadth; sufficient business depth remains mandatory. | Only source-backed requirements/branches become required cases; missing business observation rejects completion/verification. |
| R2 | Boundary, rejection, failure, permissions and multiple goals are included when requested. | Explicit finite variants survive plan evaluation and execution without default matrix expansion. |
| R3 | Each required case proves a goal or an indispensable prerequisite. | Exact contract, requirement, flow/branch, Oracle, expected evidence and necessity links validate; semantic review separately judges necessity against source. |
| R4 | Suggestions do not become executable or blocking. | Recording suggestions leaves required coverage and completion authorization unchanged; trying to execute/promote one without scope change is rejected. |
| R5 | Legitimate support and bounded diagnosis remain available. | Requirements/data/evidence/cleanup operations execute via explicit auxiliary channels; diagnostic budget and current-failure association are enforced. |
| R6 | Scope changes require traceable actual user authority. | New version and impact history; unsupported or model-invented authorization cannot authorize changed execution/completion. |
| R7 | Assessments and execution bind exact scope, plan and snapshot. | Old scope/plan/snapshot tokens reject; evidence reuse checks requirements, Oracle, target and observation applicability. |
| R8 | Genuine failures and all history remain visible. | Completed failing execution satisfies delivery only; requirement verification stays failed. Shrinking scope to hide failure rejects. |
| R9 | Recovery and task isolation preserve the same effective scope. | Cold/compact/child/preset contexts independently hydrate matching bindings; two tasks cannot exchange authorization/evidence. |
| R10 | Scope does not override host security or permissions. | Managed tools continue to respect actual host guard/provider/cancellation boundaries; arbitrary command semantics are not claimed to be classified. |
| R11 | Static validity, semantic judgment and execution proof are different. | Field completion and model pass flags alone cannot grant business verification; source and managed observations are required. |
| R12 | Delivery is independently reproducible. | New package operates without source checkout, doctor/lifecycle pass, installed plugin drives actual DSH runtime and temporary SUT. |

## Ownership and evidence policy

- `core_scope`: protocol/v2, src/bugate and tests/v2.
- `dsh_scope`: adapter, adapter tests, Skill entry and runtime evidence.
- `packaging_delivery`: packaging, distribution/install tests, CI, package version,
  bilingual product documentation and packaging evidence.
- Main agent: integration decisions, frozen contract/matrix, independent review,
  evidence reconciliation and final commits.

Use neutral temporary fixtures, no company SUT or secrets. Keep prior assertions
unless they conflict with this explicit decision; record the conflict and its
replacement test. Save exact commands, exit codes, runtime versions and artifact
digests. Test presence is not a passing result. Deterministic real-host validation
and live-model validation must be reported separately.

Final verdict is COMPLETE only if every SC case, applicable regression, actual
installed runtime and standalone installation pass. INCOMPLETE/BLOCKED must name
concrete remaining work or an observed external blocker without reducing scope.
