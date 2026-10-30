# Independent development tracks and the first Python field trial

English | [简体中文](PYTHON_FIELD_TRIAL.zh-CN.md)

**Decision:** Accepted, 2026-10-30, from the user's explicit request: develop the
improved Python version and the full TypeScript version on independent branches,
and use the improved Python version for the first field trial. This supersedes
the previous prompt's suggestion to stop full TypeScript development. Existing
TypeScript work continues independently; it is neither cancelled nor rolled back.

**Status of this update:** documentation only. No branch/worktree was created or
switched, no product was installed, and the Python field trial is **NOT_RUN**.
This plan records delivery order and isolation; [Protocol v2](../../protocol/v2/manifest.yaml)
in each selected version remains the machine authority, not this document.

## Branches and delivery boundaries

| Track | Implementation and purpose | Branch/worktree state inspected on 2026-10-30 |
|---|---|---|
| Improved Python | Python evaluator and unified CLI plus the TypeScript DSH/Cordis native plugin. Reuse and complete scope/main-flow convergence, add official Agent Team cross-review, then conduct the first field trial. | Proposed branch `codex/python-scope-team` is **not created**. Before implementation, select and record a verified Python baseline and create a separate branch/worktree. |
| Full TypeScript | Shared TypeScript core, Node CLI/toolchain and native Cordis plugin, following the [TypeScript plan](TYPESCRIPT_CORDIS.md) and its own acceptance. | Existing branch `codex/typescript-cordis-team`, inspected at `214bedfcb2129d3b77840cfe207e437d13fdec6a`, in `/Users/zhangliangchen/PycharmProjects/BUGate`; it contains other executors' uncommitted work. Preserve it. |

Recheck these observations before work. Historical Python commits such as
`3a5fcc7ae2b35f8eb361896c932d688939364bae` and
`eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654` are baseline candidates to inspect,
not instructions to reset the TypeScript checkout. Preserve all branches,
uncommitted changes and history. `main` is an integration target; its name alone
does not identify an accepted or installed delivery of either track.

Two source branches do not mean one package with permanent dual evaluators or a
Python fallback inside the TypeScript product. Each package has one authoritative
evaluator. Python development and its field trial do not wait for TS completion.
TS acceptance does not automatically replace the Python field-trial installation;
a later adoption/cutover decision identifies the exact target and package.

## Shared constraints, separately verified implementations

Both tracks support DSH only and reuse its official Agent Team. Do not restore
Claude/Codex product adapters, legacy formats, compatibility bridges or an owned
Agent loop/team scheduler. Developing BUGate with another tool does not add that
tool as a product host.

The Python track first inventories its existing scope contract, required-case
plan, auxiliary/diagnostic channel, suggestions and claims. Reuse effective
implementation; fix demonstrated gaps rather than rebuilding by label. Required
business assertions stay sufficient, explicit branches/permission/boundary goals
remain in scope, suggestions remain non-executable and nonblocking, and execution
closure remains separate from functional acceptance. Profile denominators, user
provenance, snapshot binding, actual failures and evidence publication barriers
remain effective.

Official Agent Team owns roster, messages, task coordination and lifecycle.
BUGate checks host-observed author/reviewer identities, applicable independence
policy, current artifact/input bindings, durable results and dispositions of
disagreement. Different reviewer names, Team task completion or message acceptance
are not proof of independent review. Preserve actual opinions and counterevidence;
do not impose fixed perspectives, models, rounds or extra SUT risk categories.
The accepted [Team design](TYPESCRIPT_CORDIS.md#dsh-official-agent-team-multi-perspective-cross-review-user-added-decision)
is shared intent; Python implements and verifies it against its own versioned
machine contract. TypeScript implementation evidence cannot establish Python PASS.

Keep package/evaluator identities, protocol/Profile bindings, artifact digests,
DSH installation profiles/directories, tasks, receipts and evidence clearly
separated by delivery. Never reuse one version identifier for changed protocol
meaning or silently import the other branch's assessments as authorization.
Equivalent protocol resources may be shared only with identical meaning and
verified binding; independently changing rules require explicit versioning.
Selectively port applicable fixes with their source, impact and branch-specific
verification. Do not merge either branch wholesale merely to share a fix.

## Python readiness and the first field trial

1. Freeze a verified Python baseline in its separate worktree. Establish a
   source-backed requirement/Oracle/check map for the actual improvement scope.
   Historical SC labels and test counts are not delivery gates.
2. Complete the necessary Python/core, CLI, plugin and packaging changes; run
   applicable regression and real DSH Agent Team acceptance with neutral fixtures.
   Use the configured filesystem/subprocess execution world and await durable
   evidence; mocks and prewritten review JSON do not close runtime acceptance.
3. Verify the exact Python package in a clean directory with source checkout
   access denied. Declare and verify its Python runtime/dependencies rather than
   applying the TS track's “Python unavailable” condition. Verify applicable
   install/repeat/doctor/upgrade/recovery/rollback/owned-uninstall behavior and
   the installed scope/Team workflow. Preserve user Python and SUT environments.
4. Use that improved Python package in actual DSH for one bounded field trial on
   the real external SUT, environment and goals specified by the user. Freeze the
   package digest, DSH/Team versions, Profile, scope, sources and business Oracles
   before execution; record the actual model and configured providers.
   No concrete SUT, environment or goals have been selected in this decision;
   do not invent them or claim a trial has begun. Their selection is needed for
   the field trial, not a blocker to independent implementation or fixture checks.
5. Preserve real execution/Team records, scope enforcement observations, relevant
   recovery behavior, review disagreements and dispositions, product/framework
   defects, blockers and suggestions. Check recovery when relevant to the agreed
   trial, without automatically expanding into a generic test matrix. Report
   execution completeness, functional results, evidence gaps/blockers and
   out-of-scope suggestions separately; a genuine product failure is not erased
   to obtain a green field-trial report.

Deterministic-model real-runtime verification, live-model use, and actual SUT
business proof are separate observations. Temporary fixtures and deterministic
runtime checks do not count as completion of this field trial. Report what ran;
package installation
or a Team completion marker alone proves none of the others. Real company
sources, credentials and raw SUT evidence stay in the external workspace;
repository fixtures remain neutral. This decision does not publish a release or
authorize replacing an unrelated installed DSH profile.

## Feedback and later adoption

Python field-trial findings become source-backed inputs for both tracks where
applicable. Record whether each finding is a shared contract issue, a Python-only
defect, a TS-relevant issue, an environment/SUT defect or an extra suggestion.
Adopting a finding requires its actual impact and a scoped Oracle; it does not
automatically add requirements or completion blockers to both branches.

Keep each track's commits, package checksums, test commands and runtime evidence
under its own delivery identity. The Python field-trial report determines only
the conclusions supported within that trial's scope. It neither cancels the TS
track nor authorizes its cutover. Both implementations retain the same quality
principles while demonstrating their own correctness and installability.
