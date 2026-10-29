# BUGate v2 protocol architecture

English | [简体中文](BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)

This guide describes the implemented v2-only product. Prior migration and
multi-host plans are retired by the [2026-10-29 decision](../../CHARTER.md).

| Boundary | Authority |
|---|---|
| `protocol/v2/` | Canonical Schemas, MethodSpec, quality rules and reason codes. |
| `src/bugate/` | Deterministic validation, semantic-review requirements, snapshots, evidence and claim assessment. |
| Unified CLI | Versioned JSON CommandResult and stable exit semantics. |
| DSH native adapter | Host services, exact binding, unchanged prepared context, managed tools and bounded rework. |
| `packaging/` | Finite resources, standalone wheels/bundle and owned installation lifecycle. |

The `ProtocolBinding` pins the exact protocol and Profile. `TestTask` identifies
canonical project/task scope; `Artifact`, `Evidence` and `Claim` preserve content,
provenance and applicability. `AssessmentRequest` binds a fixed claim and input
snapshot; `AssessmentResult` records deterministic/semantic/execution dimensions
and findings. `PreparedProtocolContext` renders current constraints and gaps for
the host. Consult the canonical Schemas for fields and allowed variants.

Authorization snapshots exclude derived assessments, drafts, suggestions,
unreferenced user receipts and transaction bookkeeping. Request digests also
cover the claim, binding, evaluator and parameters. The request `advisory_digest`
allows distinct assessment records after suggestions change without invalidating
the existing claim or execution. Object
digests exclude their own digest fields. Content changes or concurrent commit
conflicts invalidate authorization. The core does not own an Agent loop, current
stage, scheduler or required long-term memory service.

The versioned scope contract references user-request evidence, finite goals and
existing requirement/Oracle facts. Inventory cases bind its current version and
digest. Suggestions are isolated from required coverage and completion blockers;
auxiliary activities have explicit boundaries. See [scope semantics](../ACCEPTANCE_SCOPE.md).

Native v2 semantics include source-backed business propositions, observable
Oracles, testability, fixed-denominator scope coverage, explicitly required negative cases, implementation
references, semantic-review freshness and optional Profile-required modeling.
Legacy artifact formats are rejected; no converter or fallback checker runs.

Only the managed collector creates host observations. Imported/model material
retains its provenance. Atomic writes, locks and publication barriers guard
consistency, not hostile same-user forgery. A genuine product failure can complete
test delivery without satisfying the failed requirement.

DSH `context.text` and `tools.guard` remain synchronous. Preparation and evidence
publication use awaited lifecycle/tool seams. The adapter validates and injects
core `render.content` unchanged; each child and resumed session independently
checks exact binding. It uses configured filesystem/subprocess providers and
execution-world mappings; opaque keys are not paths. Reads, analysis and lawful
exploration remain available when implementation prerequisites are missing.

Machine authority is not duplicated in documentation or projections. The native
build appends canonical MethodSpec resources to the Skill entry and records
protocol/content digests; packaging verifies Python/native resource equality.
See [installation](../DEEPSEEK_HARNESS.md), [method](METHOD.md) and
[current and historical documentation](../README.md).
