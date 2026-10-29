# BUGate v2 method

English | [简体中文](METHOD.zh-CN.md)

The machine authority is [Protocol v2](../../protocol/v2/manifest.yaml), especially
its MethodSpec files and quality rules. This guide explains their application.

Build an auditable business model from evidence. Distinguish facts, inference and
unknowns; transform stories into propositions, states, boundaries and observable
Oracles. A source citation establishes traceability, not automatic truth. Resolve
contradictions with discriminating observations, and keep unresolved gaps visible.

First derive a finite acceptance scope from user-request sources: goals, allowed
objects/environments, flows and necessary branches, requirements, Oracles and
completion conditions. Distinguish explicit instructions, indispensable derived
conditions, unresolved assumptions and out-of-scope suggestions. General best
practice alone cannot make a scenario required.

Select the cheapest sufficient test layer. Record what can be observed, its
preconditions, observation mechanism, deadline, side effects and environment
requirements. A gap in implementation prerequisites must not prohibit reading
requirements or collecting the missing evidence through legitimate exploration.

The locked Profile fixes requirement/risk denominators in every task scope
revision. User-authorized branch/Oracle changes within those denominators create
a new scope revision; changing the denominators requires an explicit new Profile
version and a new task, preserving the old task and failures. User-request receipts
prove choices, while domain evidence remains Profile-declared. Trace those fixed
requirements and risks through propositions, Oracles,
cases, implementation and execution within the current acceptance scope contract.
Include positive, negative, permission and boundary cases when the user goal or
adopted requirements call for them; no generic test-category matrix adds goals. Profiles may require native domain/state/dimension/adversarial artifacts;
their exact contracts are defined in the Artifact Schema. Source-backed semantic
review remains separate from deterministic structural checks.

Execute through the configured host collector and preserve actual results.
Distinguish product failures from test-infrastructure/environment failures and
zero-test, skip, xfail, cancellation and unobserved conditions. Test assertions
are derived from contracts and observations; do not weaken them to fit a defect.
Diagnosis records hypotheses, support/counterevidence and minimal distinguishing
experiments tied to a current failure and bounded by the contract. Reaching the
bound records a conclusion or blocker; it does not create a new test campaign. Knowledge records source, scope and confirmation status.

Claims are scoped to exact snapshots. Business understanding does not require
future execution; requirement verification requires current Oracle-linked run
observations; complete delivery follows the locked Profile criteria and may
include genuine product failures. Derived QualityPosture is not a mutable stage
or a scheduling instruction. DSH owns execution and lifecycle.

The method draws on requirements traceability, equivalence partitioning,
boundary/state testing, risk-based testing, independent review and the test-oracle
problem. The implementation makes no empirical claim of improved defect yield or
model efficiency without a separate controlled experiment.

Suggestions remain non-blocking and non-executable. User-authorized scope changes
create immutable revisions, preserve failures and require fresh plan/claim
assessment. A changed claim never inherits old authorization. Reports state the
actual agreed scope and separate execution closure, functional results, blockers
and suggestions. See [scope examples](../ACCEPTANCE_SCOPE.md).
