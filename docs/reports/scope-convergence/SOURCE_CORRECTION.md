# Acceptance source correction and historical mapping

English | [简体中文](SOURCE_CORRECTION.zh-CN.md)

On 2026-10-29 the user explicitly corrected the acceptance basis. SC-01–SC-18
originated as assistant candidates/examples; the earlier implementation prompt
mistakenly made them mandatory. Revision 1 repeated that error. Revision 2 removes
both the all-SC obligation and the all-SC completion condition. This is an explicit
user-authorized correction, not an agent reduction caused by test failures.
Commit `000d3be46068cdc0e64f60f454342c78892361a8` preserves revision 1. No Git history
was reset or rewritten. Existing test results, including debugging failures, remain.

The starting commit `fe3e29b82059504cd47893b72005051489c0810f` was checked using
`git cat-file`, `git merge-base --is-ancestor` and `git show`; exact baseline blob
hashes are in [baseline-reverified.json](evidence/baseline-reverified.json).
The current uncommitted implementation and new MethodSpec are not baseline evidence.

## Reassessment of the former R labels

| Former label | Genuine source and disposition | Formal requirement |
|---|---|---|
| R1 | U1 III and U2 II/VII explicitly require bounded breadth with sufficient evidence. Retained, not derived from the order example. | F-02/F-04 |
| R2 | U1 IV/V and U2 II require honoring the SUT task's own requested branches. Retained as generic capability; orders/tenants/three fixed categories are examples, not product domains. | F-02 |
| R3 | U1 III.4/V and B0 METHOD require source-backed necessity and Oracles. Retained. | F-01/F-03 |
| R4 | U2's main objective explicitly makes suggestions non-executing and nonblocking. Retained. | F-02/F-04 |
| R5 | Retained U1 VI.3 and B0 METHOD preserve lawful exploration and bounded diagnosis. Retained for touched execution paths. | F-05 |
| R6 | U1 VII and U2 III/VI authorize traceable changes while preserving Profile denominators. Corrected: scope may not override the locked Profile. New denominator requires explicit new Profile/task. | F-03 |
| R7 | B0 AGENTS/ProtocolBinding/workspace and U2 III explicitly require exact snapshots/version bindings. Retained. | F-06 |
| R8 | B0 AGENTS/quality rules and U2 III/VI preserve real failures and separate delivery from passing requirements. Retained. | F-04 |
| R9 | B0 AGENTS/protocol guide and retained U1 VI require recovery/child/task isolation. Retained as applicable regression, not an additional platform matrix. | F-06 |
| R10 | B0 execution-world/synchronous-interface constraints and U1 III.10 retain host permissions. Retained at observable managed-tool boundaries. | F-05/F-06 |
| R11 | B0 source/semantic/execution distinctions and U2 III explicitly require separate proof levels. Retained. | F-03/F-04 |
| R12 | B0 AGENTS/CI and U2 III/VII require actual DSH and package-boundary validation. Retained; no fixed schema/check count. | F-07/F-08 |

## Candidate disposition (not a separate acceptance gate)

| Historical candidate | Disposition and reason |
|---|---|
| SC-01 | Replaceable example of F-02/F-04. Existing order fixture retained as useful evidence; no mandatory order domain. |
| SC-02 | Replaceable explicit-rejection example of F-02. Does not require every SUT to test permissions. |
| SC-03 | Merged with F-02's finite explicitly requested branches. No mandatory three-category fixture. |
| SC-04 | Merged with F-05: lawful support is necessary to obtain in-scope evidence. |
| SC-05 | Merged with F-02: refusing automatic extension directly tests the user objective. |
| SC-06 | Merged with F-02/F-04: suggestions must not indirectly stale evidence or block completion. |
| SC-07 | Merged with F-04 and baseline delivery semantics. Deliberate fixture failure is a negative verification test, not a product pass. |
| SC-08 | Merged with F-04 and baseline execution authenticity. Missing observations cannot close acceptance. |
| SC-09 | Merged with F-05's bounded diagnosis from retained U1 VI.3. No new specialist campaign required. |
| SC-10 | Merged with F-03/F-06 change/freshness checks. Explicitly preserve Profile denominator and supported new-task boundary. |
| SC-11 | Merged with F-03's real authorization and failure preservation. |
| SC-12 | Merged with F-06 applicable lifecycle regressions required independently by B0 and U2. |
| SC-13 | Merged with F-06 exact-binding regressions. |
| SC-14 | Merged with F-06 task isolation; no fixed concurrency matrix beyond touched state. |
| SC-15 | Merged with F-03 source/conflict checks. Semantic necessity remains distinct from structural references. |
| SC-16 | Merged with F-05/F-07: actual DSH runtime is independently required. Deterministic-model fixture is the chosen sufficient mechanism. |
| SC-17 | Merged with F-07's independently usable package. Source-denied installation is the selected observation, not authority derived from SC. |
| SC-18 | Merged with F-04/F-08 honest result expression. |

Candidate identifiers may remain in existing test names for traceability. They
have no independent execution or completion force. A retained test is reported
only from its actual run result; an unused candidate is never assigned PASS.
Nothing in this mapping adds the candidates to a SUT's Profile or default test
matrix. No development-task Profile containing the eighteen candidates was created.

## Implementation corrections and remaining boundaries

The initial implementation briefly allowed ScopeContract requirements/risks to
override Profile arrays. Baseline review found this incompatible with the locked
Profile invariant. It was corrected before final regression: every scope revision
must retain the Profile denominator; a different denominator returns
`SCOPE_PROFILE_CHANGE_REQUIRED`. New Profile/task initialization preserves the old
task and failures. User receipts provide scope provenance, not undeclared domain
facts. Scope changes conservatively invalidate old execution evidence; this
version does not provide selective reuse or same-task Profile rebinding.

The baseline unconditional negative-case rule is intentionally replaced by
source-backed, scope-derived necessity under the explicit user product decision.
Explicitly requested negative and boundary behavior remains covered. Other
non-conflicting v2 assertions remain. Reasonable tests already implemented are
retained; no failure was hidden or reclassified to satisfy this correction.

The final delivery report maps F-01–F-08 to command logs and the tested package.
Debug runs against changing protocol resources remain separately labelled; they
cannot substantiate a final pass. No public release/tag is published.
