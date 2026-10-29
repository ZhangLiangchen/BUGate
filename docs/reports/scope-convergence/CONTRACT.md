# Acceptance scope implementation contract — revision 2

English | [简体中文](CONTRACT.zh-CN.md)

This revision applies the user's explicit 2026-10-29 scope correction. It
supersedes revision 1 in commit `000d3be46068cdc0e64f60f454342c78892361a8` without
rewriting that commit or discarding valid implementation or test evidence.

**The original acceptance basis was wrong:** SC-01–SC-18 were assistant-authored
candidate checks and illustrative examples. The earlier implementation prompt
mistakenly promoted them to mandatory acceptance. They are not a pre-existing
repository standard or eighteen individually requested user requirements. The
user has explicitly withdrawn that promotion. Neither their numbering nor their
presence in newly written files creates an execution obligation.

## Real objective and sources

The user requires a first-class v2 acceptance scope contract and main-flow
convergence: sufficient, real, traceable evidence for the agreed goal; additional
scenarios remain suggestions and do not automatically expand execution or block
completion. The supported product is the host-neutral Python v2 core/unified CLI
and native DSH plugin. The development tool is not another supported product host.

Source classes used by the formal matrix:

- **U1:** the original user implementation request, except its withdrawn SC
  checklist mandate and the corresponding completion condition. Its explicit
  product objective, business invariants, architecture, evidence and delivery
  constraints remain authorized where consistent with U2.
- **U2:** the user's formal correction in this chat, sections I–VIII. It determines
  the acceptance basis and takes precedence over U1 and assistant artifacts.
- **B0:** the actual pre-change commit
  `fe3e29b82059504cd47893b72005051489c0810f`, reverified as a Git commit and ancestor
  of the current HEAD. AGENTS, METHOD, SOP, protocol guide, quality rules, Profile
  and binding schemas, workspace identity and CI were read from that commit.
  [Blob hashes and verification](evidence/baseline-reverified.json) distinguish
  them from files changed during this task.
- **D:** indispensable engineering derivations. Each formal matrix row states
  the causal link to U1/U2/B0. A useful practice is not sufficient justification.
- **C:** assistant candidates, examples and implementation proposals. They may
  demonstrate an authorized requirement but are not its source.

The new Schemas, MethodSpec, this report, commit 000d3be and current documentation
are implementation outputs. They cannot prove that their content was an
already-existing obligation. See the [source correction and historical mapping](SOURCE_CORRECTION.md).

## Retained engineering boundaries

`protocol/v2/` remains the sole machine authority; `src/bugate/` evaluates facts;
DSH owns execution, lifecycle and bounded rework; views project the authority;
packaging uses a finite inventory; methodology docs explain rather than invent
rules. Preserve source-backed propositions, business Oracles, static/semantic/
execution distinctions, real failures, skips, xfails, unknowns, snapshots,
permissions, path/world boundaries and durable publication.

The locked Profile continues to fix requirement/risk denominators. Scope revision
cannot silently replace them. Explicit user changes within that denominator
require a new scope version and fresh assessment. Changing the denominator
requires an explicit new Profile version and task through existing initialization;
the old task and failures remain available. This delivery does not introduce a
same-task Profile migration engine. User-request receipts establish scope
provenance; they do not substitute for Profile-declared domain evidence.

No SUT must implement orders, tenants or a generic security/boundary/performance
matrix because this development task used an illustrative fixture. Tests exercise
BUGate's treatment of a SUT's own finite requirements. The development matrix is
not projected as a default SUT Profile or MethodSpec requirement.

## Completion and ownership

COMPLETE requires the real objective, all source-verified formal items F-01–F-08,
applicable v2 regressions, package boundaries and actual DSH acceptance to be
satisfied, with consistent code/docs/artifacts/evidence and no unresolved in-scope
blocker. **There is no all-SC-pass condition or fixed test-count target.** Test
presence is not execution evidence. Report deterministic real-runtime and
live-model results separately. No release/tag publication is authorized.

INCOMPLETE/BLOCKED must name actual remaining requirements or observed external
blockers. Do not relabel a genuine in-scope defect as optional, weaken assertions,
or delete failing evidence. Reasonable existing tests and historical outcomes
remain; unused candidate suggestions are not reported as passing.

Single writers remain: core_scope owns protocol/core/core tests; dsh_scope owns
adapter/Skill/runtime evidence; packaging_delivery owns packaging/install/CI and
product docs; main owns source correction, integration, review and final commits.
