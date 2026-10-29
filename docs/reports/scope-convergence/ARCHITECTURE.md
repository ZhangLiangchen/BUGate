# Acceptance scope architecture decisions

English | [简体中文](ARCHITECTURE.zh-CN.md)

The authority for this change is the user's product request and business
invariants, as corrected by the later instruction that SC-01–SC-18 are candidate
examples, not a mandatory checklist or a fixed completion threshold. The
[implementation contract](CONTRACT.md) and [adopted verification mapping](ACCEPTANCE_MATRIX.md)
record that derivation; they are not independent authority for their own rules.

The existing engineering constraints were re-read from immutable baseline
`fe3e29b82059504cd47893b72005051489c0810f`, including AGENTS, METHOD, SOP, protocol
guide, `protocol/v2/rules/quality.json`, the test-design MethodSpec and evaluator.
That baseline requires a Profile-fixed denominator, source-backed Oracles, exact
snapshots, actual execution evidence and preservation of product failures. Its
unconditional negative-case rule is visible in `quality.json.negative_coverage`
and `assessment.py`'s `NEGATIVE_COVERAGE_MISSING`; the current files must not be
used to retroactively justify changing these baseline rules. See the recorded
[baseline review](packaging/basis-review.json).

1. **A first-class scope with the existing plan.** Scope contracts have immutable
   revisions and content digests. The existing inventory expresses required
   executable cases; referenced requirements/Oracles remain their original truth
   source. Suggestions are stored separately and do not enter the acceptance
   denominator. Every revision preserves the locked Profile denominator; the
   contract cannot silently override it. This avoids parallel plan/state-machine
   authority. Advisory content has a separate assessment-request digest so a
   changed suggestion list can produce a consistent immutable result without
   changing the governed input snapshot or creating an execution obligation.
2. **Finite breadth, sufficient depth.** Required cases need a goal relationship
   and business evidence. There is no unconditional positive/negative/boundary
   matrix. Explicit rejection, boundary, permission and multiple-goal requests
   remain required. The user's breadth/depth decision, rather than candidate case
   IDs, authorizes replacing the old unconditional negative-case expansion.
   Adopted tests prove this behavior and retain explicitly required negative
   assertions. Candidate IDs remain historical traceability labels only.
3. **User intent provenance is a host boundary.** Contract text cannot authenticate
   its own authorization. DSH intake must distinguish direct human messages from
   delegated prompts, inherited fork messages and internal rework. In the tested
   host, `dsh-subagent/lib/types/manager.js` labels delegated prompts as user
   messages; checking role/source alone is therefore insufficient. The adapter
   also checks session origin and records only admissible user-origin input.
   Children hydrate the same contract without creating new user authority.
4. **Evaluate before executing.** The Python evaluator authorizes declared cases
   and bounded auxiliary activities against the effective scope, inventory and
   current snapshot. DSH consumes that result through awaited preparation/tool
   seams; synchronous context/guards only inspect completed prepared state.
   Arbitrary shell semantics are not inferred. Host permissions and execution
   providers remain authoritative.
5. **Complete execution can fail functionally.** Delivery closure requires all
   agreed observations and a consistent report. Requirement verification requires
   the corresponding observed Oracle to hold. Suggestions never require rework;
   actual missing/invalid in-scope evidence does. Product failures are retained.
6. **Changes invalidate authorization.** A user-authorized branch/Oracle change
   within the locked Profile denominator creates a scope revision, preserving
   history and impact. `SCOPE_PROFILE_CHANGE_REQUIRED` rejects denominator changes
   and directs the operator to an explicit new Profile version plus `init` for a
   new task. No in-task Profile rebind or migration engine is introduced. A new
   Profile source may reference the prior contract snapshot for cross-task
   traceability; old tasks and failures remain. ScopeRequest proves user choice
   and does not replace Profile-declared domain evidence. Plan and claim
   evaluations must match the exact
   digest/version. This release conservatively makes all previous-scope evidence inapplicable
   and requires new runs, while retaining it as history. Selective evidence reuse
   is not implemented. No Agent-generated approval or failed-test
   driven shrink can authorize a new completion claim.
7. **Distribution is finite and independently testable.** All three new scope
   Schemas and acceptance MethodSpec ship in the Python wheel and native package
   from the same explicit resource inventory. Version `2.1.0-dev.1` distinguishes
   changed protocol meaning; `bugate-core 2.1.0a1` is the Python distribution.
   The installer reads the package's pinned compatibility record, supports exact
   prior v2 archive rollback, and does not migrate task bindings. Current assets
   are local build deliverables, not a replacement public release.

The local authenticity boundary remains managed observation with filesystem
integrity and durable publication, not defense against an actor with arbitrary
same-user access. Static field/reference checks are not semantic correctness or
execution proof. Real DSH deterministic-model acceptance and live-model results
must be stated separately in the final evidence report.
