# BUGate Protocol v2

English | [简体中文](README.zh-CN.md)

Protocol `2.2.0-dev.1`, TypeScript `@bugate/core 2.2.0-dev.1`, and DSH bundle `2.2.0-dev.1`
have separate version identities. Every task locks the exact protocol digest. This directory is the canonical package resource;
Node package construction includes these exact bytes. `manifest.yaml` and
`reason_codes.yaml` use JSON syntax, which is valid YAML 1.2.

The Schemas listed in `manifest.yaml` use Draft 2020-12, validated by pinned
`ajv@8.17.1`. Artifact content is
strictly typed by `artifact_type`. MethodSpec defines quality, not a workflow.
The shared TypeScript evaluator returns deterministic, semantic-review and runtime
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
requirement/risk references; it does not block completion. Independent review requires durable DSH Team host observations for the actual author and reviewer executions, distinct execution roles and an observable context policy. Names and imported model fields do not prove independence.
Optional models are not implicit default requirements. Their schema and negative
controls live alongside the ordinary v2 tests.

Node tests generate neutral temporary fixtures and execution logs. Ordinary operations have no Memory Bus dependency.

`Profile.team_review_policy` fixes finite source-backed perspectives, fresh/declared context, a bounded round limit and whether cross-checking is required. The native inline `bugate_publish_artifact` tool binds actual model tool output and exact live Team membership; path imports do not acquire authorship. `HostObservation` references the final output digest, precise binding and evaluator identity. It retains raw call and received-message facts plus canonical digests. Observed execution separation is distinct from semantic review quality and is not a guarantee of cognitive independence.

Review input snapshots use each review target’s upstream dependency closure: exact binding/evaluator, Profile sources, scope/plan, relevant design artifacts, implementation bytes and only explicitly referenced execution evidence/files. They exclude reviews, observations/settlements, claims, assessments, drafts, suggestions, history and unrelated downstream execution/report/knowledge/diagnosis. This prevents a review hashing itself. Changing relevant inputs invalidates old reviews. Cross-checks reference immutable prior opinions and actual received Team messages; unresolved blocking findings remain visible. A Team task marked complete or a message accepted never constitutes valid review. Ordinary CLI imports cannot mint host observations. Local managed observation does not protect against same-permission filesystem/process tampering.

Host provenance uses two-stage publication: `HostObservation.status=published` records durable output and observations but cannot authorize. At an awaited lifecycle seam, the adapter reads and flushes the final DSH `tool/result`, then publishes an immutable `HostSettlement`. Missing final output, cancellation, timeout and failure remain incomplete. Recovery settles from durable session facts, never Lead-local state.

The native observation channel is signed by an Ed25519 private key held only in the plugin process. The configured filesystem provider persists only its public-key pin. Signatures bind task/workspace, operation and the complete context digest; the CLI verifies an existing pin and exposes no authority-creation command. Ordinary model JSON and a self-supplied environment token cannot mint provenance. Historical public keys/signatures remain for recovery verification; same-permission direct key/file rewriting remains outside the assurance.

Native semantic-review tool arguments must carry explicit, current target digests;
the publisher cannot silently replace null or stale references with the latest
artifact. `AssessmentResult.review_assurance` separately exposes each opinion’s
host observation/settlement references, observed execution independence and
semantic conclusion with their respective limits. An accepted opinion is not a
claim of proven cognitive independence or correctness.

Target revisions do not erase genuine unresolved blocking findings. Within the
same scope and related target IDs, preserve historical independent opinions until
an exact original-review/finding disposition has actual peer-message provenance
and source-backed counterevidence. Historical opinions can support that disposition
but cannot authorize changed current inputs. Previously evidenced dispositions
remain closed without requiring consensus.
