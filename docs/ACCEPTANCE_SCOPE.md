# Acceptance scope and sufficient evidence

English | [简体中文](ACCEPTANCE_SCOPE.zh-CN.md)

An acceptance scope contract records the finite goals accepted for this run and
how they will be demonstrated. It is a versioned protocol entity, not an advisory
prompt. The canonical [ScopeContract Schema](../protocol/v2/schemas/scope_contract.schema.json)
and [acceptance MethodSpec](../protocol/v2/methods/acceptance_scope.json) define its
fields and rules; the current inventory remains the executable plan authority.

The default is the smallest sufficient evidence closure for the requested goals.
Depth and breadth are independent: a successful API response is insufficient when
the goal includes persistent business state, ownership or downstream effects.
Conversely, adding tenants, actors, environments, flows or risk categories needs
an explicit goal or an indispensable requirement-to-goal relationship. “Best
practice” alone is not that relationship.

The contract references request sources and existing business requirements and
Oracles instead of copying their truth into a new plan. It covers the object and
environment boundary, main-flow entry/nodes/termination, necessary branches,
required scenario IDs and necessity, exclusions, auxiliary activity boundaries,
completion criteria and change history. It can represent one flow, several
required branches, a finite set of goals or expected rejection/failure behavior.
It does not prescribe a single success path.

Explicit user requirements, indispensable derived conditions, unresolved
assumptions and optional suggestions are distinct. If an assumption changes the
acceptance direction, keep the gap and ask the smallest necessary clarification.
Clear intent does not need repeated approval of every field. Excluding an
explicitly requested behavior is a conflict, not a way to reduce coverage.

## Four examples

| User request | Required scope and observation | Outside this run unless requested |
|---|---|---|
| “An ordinary user can create an order.” | Authenticate as that user, submit a valid order, observe the created order's persisted owner and agreed business fields through the declared Oracle. | Other-tenant attacks, fuzzing, concurrency and performance campaigns. |
| “An ordinary user cannot create an order for another tenant.” | The cross-tenant attempt is a required rejection branch. Observe the expected refusal and absence of an unauthorized order. | Unrelated vulnerability categories. |
| “Verify normal, boundary and failure behavior.” | Preserve all three explicitly requested variants and their different business Oracles. Expected refusal may itself be the passing result. | Additional variants without a source-backed necessity. |
| “Create an order,” but the real command returns the wrong owner. | Preserve the real failing observation, complete bounded diagnosis and report execution complete with functional acceptance failed when all agreed evidence is present. | Extra tests intended to compensate for the failure or make the report green. |

For the first example, several recorded suggestions such as load testing and
another browser do not block completion. The report can state “execution
complete; accepted requirements satisfied; no execution blocker; two suggestions
outside this run.” It must still say “This conclusion covers only the agreed
scope,” list that scope, and avoid a whole-product safety or correctness claim.

Login, setup, source/evidence reads, analysis and cleanup are auxiliary activities.
They are not silently promoted to acceptance goals. Diagnostic execution must
refer to a current failure and a declared bounded activity; exhausting its budget
records what was learned or what remains blocked. An in-scope failure discovered
by diagnosis remains a failure in the acceptance result.

## Plans, changes and proof

Each required case binds the current contract version/digest, a requirement,
flow/branch, business Oracle, expected evidence and necessity. Static checks verify
references, revision consistency and scope membership. The semantic review must
separately judge necessity against the source; complete fields or model-reported
necessity do not prove business correctness.

Record unrelated findings as suggestions. A suggestion is neither an executable
plan nor a pending required test. It cannot indirectly become a completion
blocker. To expand testing, state the additional goals or required categories in
a new user request, for example “Also verify that users cannot create orders for
another tenant, using the same environment.” Within the locked Profile
requirement/risk denominator, authorized branch/Oracle changes create a new
contract revision with source, impact and fresh plan/claim assessment. If the new
goal changes that denominator, `SCOPE_PROFILE_CHANGE_REQUIRED` directs an explicit
new Profile version and `init` of a new task. A new Profile source may reference
the prior contract snapshot; preserve the prior task and failures. There is no
same-task Profile rebind or automatic migration. ScopeRequest proves user choice;
business-domain evidence still comes from Profile-declared sources. Failed tests, time pressure or execution difficulty do
not authorize an Agent to shrink the agreed goals.

A scope contract does not override host permissions or tool safety. Managed
execution uses observable case/activity identifiers and exact current bindings;
BUGate cannot infer all business semantics from arbitrary shell text. The host
must collect user-origin requests at its trusted intake seam. A model-written
approval string is not such a request. Same-user control of all local files is
outside the local evidence authenticity guarantee.

Old scope, plan or snapshot assessments cannot authorize new execution or claims.
This release conservatively invalidates all previous scope/plan execution
authorization and evidence applicability after a scope revision. The old evidence
and failures remain inspectable history, but required cases must run again.
Selective evidence reuse is not implemented; no unchanged-field shortcut restores
a previous assessment. Cold recovery, compaction and children independently
prepare the current binding. See [DSH usage and recovery](DEEPSEEK_HARNESS.md).

## Reporting results

| Execution/evidence closure | Functional acceptance | Required reporting |
|---|---|---|
| Complete | Satisfied | Exact covered requirements, observed business results and remaining optional suggestions. |
| Complete | Failed | Genuine failures and their evidence; execution completion must not turn failure into pass. |
| Incomplete | Not established | Missing required execution/evidence and actual blockers, without treating model flags or structure as proof. |
| Complete with suggestions | Satisfied or failed | The same in-scope verdict; suggestions remain separate and do not trigger automatic rework. |

The structured `AssessmentResult.acceptance` summary separates
`execution_complete`, `functional_result`, `requirement_ids`, `blocking_codes`,
`suggestion_ids`, `historical_failure_ids` and `coverage_statement`. Execution
completion and `requirement_verified` are still evaluated independently.
A `requirement_verified` claim may name only R1. `AssessmentResult.scope` then
lists the requirements actually covered by that claim, while
`acceptance.requirement_ids` retains the entire agreed Profile requirement set.
Verifying R1 does not make overall `execution_complete` or `functional_result`
pass when required R2 has not been observed.

Reports explicitly distinguish coverage, functional verdict, execution blockers
and out-of-scope suggestions. No live-model quality improvement is implied by a
passing deterministic runtime test.
