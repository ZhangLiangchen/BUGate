# Capabilities and proof boundaries

English | [简体中文](CAPABILITIES.zh-CN.md)

| Capability | Current implementation |
|---|---|
| Acceptance scope | Versioned source-backed goals, finite flows/branches, required cases, auxiliary activities, bounded diagnosis and non-blocking suggestions. |
| Business understanding | Source-backed propositions, Oracles, states, boundaries, gaps and digest-bound semantic review. |
| Testability and design | Observation, preconditions, layer selection, requirement/risk coverage and scope-required case traceability. |
| Optional richer modeling | Profile-required domain/state/dimension/adversarial artifacts are evaluated natively. |
| References and freshness | Exact Protocol/Profile binding, content references, snapshots, evaluator digest and concurrent-write checks. |
| Evidence | Bounded managed execution, JUnit interpretation, provenance separation and atomic persistence. |
| Claims | Business understanding, design, implementation, execution, requirement verification and scoped task delivery. |
| DSH | Native apply(ctx), canonical context, tools, Skill projection, managed policy, child isolation, resume and bounded rework. |
| Installation | Offline Python wheelhouse, real DSH plugin installation, idempotence, upgrade, rollback, doctor and removal. |

The CLI is `bugate`; see the [guide](docs/DEEPSEEK_HARNESS.md) for exact commands
and exit semantics. Machine contracts are under `protocol/v2/`; they outrank this
summary. The adapter capability manifest declares its tested target and limits.

Tests distinguish model reports, imported logs and host observations. Content
hashes establish integrity, not authenticity of execution. Same-user arbitrary
file edits are outside hostile-worker authentication guarantees. Managed policy
is not a universal operating-system sandbox. A product failure can be a complete
test delivery while requirement verification remains unsatisfied.

No legacy format migration, alternative host adapter, role process manager,
self-healing loop, automatic memory lifecycle or global user configuration cleanup
is included. Current acceptance requirements are in the [scope matrix](docs/reports/scope-convergence/ACCEPTANCE_MATRIX.md); recorded results must be read separately from that frozen contract.

The guard uses declared case/activity IDs and current protocol, scope, plan and
snapshot bindings. It cannot determine every business meaning in arbitrary shell
commands. Semantic review of necessity is distinct from structural checks;
recording “approved” or a necessity sentence is not trusted user authorization.
See [scope and result examples](docs/ACCEPTANCE_SCOPE.md).
