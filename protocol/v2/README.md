# BUGate Protocol v2

English | [简体中文](README.zh-CN.md)

Protocol `2.1.0-dev.1`, Python `bugate-core 2.1.0a1`, and DSH bundle `2.1.0-dev.1`
have separate version identities. Every task locks the exact protocol digest. This directory is the canonical package resource;
wheel construction includes these exact bytes. `manifest.yaml` and
`reason_codes.yaml` use JSON syntax, which is valid YAML 1.2.

The Schemas listed in `manifest.yaml` use Draft 2020-12, validated by pinned
`jsonschema==4.23.0`. Artifact content is
strictly typed by `artifact_type`. MethodSpec defines quality, not a workflow.
The Python evaluator returns deterministic, semantic-review and runtime
dimensions separately. The adapter must inject `render.content` unchanged.

Task initialization accepts a JSON Profile. Paths in Profile sources are relative
to the explicit project; all artifact/evidence/claim paths stay inside the task.
Create source digests from actual bytes. Canonical object digests are SHA-256
of `kind + '/v1'`, a NUL separator, then UTF-8 JSON with sorted keys and no
whitespace; omit top-level `digest` and `request_digest`. File digests are plain
SHA-256 of bytes. Floats and integers outside JavaScript's safe range are rejected.
`protocol.lock.json` contains `digest` fields; there is no second `sha256` spelling.

The authorization snapshot includes current task facts, the active scope, referenced
source bytes, exact Binding and evaluator source bytes. It excludes claims, derived
assessments/context, drafts, suggestions, unreferenced user receipts, history and
transaction bookkeeping. Request digests include the fixed Claim body and an
`advisory_digest` of the current suggestions: recording a suggestion creates a
distinct assessment record without invalidating the existing claim or execution.
Read-time status recomputes claims; a stored `satisfied` result never authorizes
work. Atomic writes and non-blocking task locks cover cooperating local writers;
unmanaged external writes may cause conflicts and are not magically atomic.

`ScopeRequest` records user-origin choices, `ScopeContract` binds finite goals,
flows/branches, requirements, Oracles, necessary scenarios and auxiliary bounds,
and `Suggestion` records optional ideas without executable plans. The current
inventory is the sole plan; required cases reference the exact scope and scenario.
Scope changes preserve the locked Profile requirement/risk denominators. A change
to those denominators requires an explicit new Profile version and a new task.
User receipts do not replace Profile-declared business evidence or host permissions.
Current scope, plan, environment and snapshot checks govern execution and claims;
old evidence and failures are retained, but this version requires new execution
after a scope revision. Necessity still needs source-backed semantic review.

`evidence record` preserves imported/human/model provenance and rejects
`host_observed`. `execution run` executes real argv and records the process,
outputs, fresh JUnit report, requested cases, outcomes and input digests. Runtime
proof is local managed observation, not resistance to a same-permission actor
rewriting both evidence and collector receipts. A hash proves byte consistency,
not truth. A failed product test may satisfy execution/delivery scope while its
requirement remains unverified. Zero, entirely skipped and xfail-only runs do not
establish verification.

The evaluator consumes only native v2 objects. Historical file dialects and
`legacy_precode` are rejected by Schema; no conversion or fallback exists.
Public quality invariants and claim applicability live once in `rules/quality.json`;
MethodSpecs reference that constraint set. Context and Skill projections read it.

Business facts require declared sources and current semantic review. Every oracle
needs a testability strategy; cases use the inventory source plan or narrower
explicit `source_refs`. Required prose cannot be blank or unresolved placeholders.
Inventory cases carry readable preconditions, expected observations and layer reasons.

Profiles may tighten requirements with `quality_requirements` flags set to `true`:
`domain_model`, `state_model`, `dimension_matrix`, `adversarial_review`, and
`independent_review`. The four model artifacts have native typed schemas, explicit
cross-references and current semantic-review requirements whenever supplied.
Included adversarial findings must map to actual cases. Unresolved in-scope
findings and residual risks prevent design completion. An out-of-scope residual
references a recorded non-executable suggestion and carries no in-scope
requirement/risk references; it does not block completion. Independent review requires named artifact
`producer` values distinct from `reviewer`; this is provenance, not authenticated identity.
Optional models are not implicit default requirements. Their schema and negative
controls live alongside the ordinary v2 tests.

Examples are generated and schema-checked by `tests/v2/test_core.py`; fixtures and
execution logs live in temporary external SUT directories. Ordinary operations
have no Memory Bus dependency.
