# BUGate contributor protocol

English | [简体中文](AGENTS.zh-CN.md)

BUGate defines testing quality and evaluates facts. DeepSeek Harness (DSH) is the
only product host and owns execution, scheduling, agent lifecycle and bounded
rework. Developing this repository with another tool does not add product support
for that tool.

## Authority and layout

- `protocol/v2/` is the sole machine authority: Schemas, MethodSpec, rules and reason codes.
- `src/bugate/` is the host-neutral Python evaluator and unified CLI.
- `adapters/deepseek-harness/` is the native adapter and real-runtime acceptance.
- `views/skills/bugate/` is the small entry projection; the build appends canonical MethodSpec resources and seals their digest.
- `packaging/` owns the explicit resource inventory and standalone installer.
- `docs/qa-methodology/` explains the protocol; it does not create parallel rules.

Read [METHOD](docs/qa-methodology/METHOD.md), [SOP](docs/qa-methodology/SOP.md)
and the [protocol guide](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.md) before
non-trivial changes. Preserve dirty worktrees and other writers' changes. Use
explicit file ownership when delegating. Save decisions, findings and commands
as reviewable task artifacts; no memory daemon or hook is required.

## Engineering invariants

Keep the core SUT-neutral. Real product sources, APIs, secrets, environments,
fixtures and evidence belong in external test workspaces; tests here create
neutral temporary fixtures. Bind domain facts to Profile-declared evidence.

Build source-backed propositions and observable Oracles before governed test
implementation. Check business semantics as well as structure. Placeholders,
unresolved blocking gaps and deferred probes cannot authorize implementation.
Keep requirement/risk coverage denominators fixed by the Profile.

Model-reported pass flags and hashes are not execution proof. A previous
assessment cannot authorize a changed claim or snapshot. Preserve actual product
failures, skips, xfails and unobserved outcomes; do not weaken assertions to make
a test green. Separate infrastructure defects from product defects and hypotheses
from confirmed findings. Use exact expected errors, source-backed assertions and
project path helpers.

Use synchronous DSH context/guard interfaces only for completed snapshots; await
preparation and durable evidence publication at supported lifecycle seams. Never
infer child guard inheritance or treat an opaque filesystem key as a host path.
Use the configured filesystem/subprocess execution world.

Do not restore retired hooks, host adapters, old-format conversion, installer
compatibility, role daemons or scheduling loops. Existing Git history and public
releases remain historical records. v0.x and pre-refactor task formats are rejected.

Framework-facing documentation must have matching English/Chinese files with
language links. Run applicable tests, source/package boundary checks and actual
DSH acceptance before claiming complete delivery. Tests existing in the tree are
not evidence that they ran. Report deterministic-runtime and live-model results
separately. Do not publish or overwrite a release without explicit authorization.
