# Scope governance delivery

English | [简体中文](DELIVERY.zh-CN.md)

**Status: COMPLETE.** The source-verified formal items F-01–F-08 are satisfied,
with no unresolved in-scope blocker. The completion basis is the user objective
and applicable contracts, not all SC candidates. This conclusion covers only the
agreed implementation and validation scope.

## Acceptance basis correction

SC-01–SC-18 were assistant-authored candidates/examples, mistakenly promoted by
the earlier prompt and contract. The user's explicit correction removed that
promotion. The [revised contract](CONTRACT.md), [formal source matrix](ACCEPTANCE_MATRIX.md)
and [former R / SC disposition](SOURCE_CORRECTION.md) distinguish direct user
requirements, pre-change v2 authority, necessary engineering derivation and
examples. No order/tenant domain or generic full test matrix becomes a default
SUT requirement. Existing reasonable tests/results remain. No history was reset.

The starting commit `fe3e29b82059504cd47893b72005051489c0810f` was reverified from
Git objects, with [baseline file hashes](evidence/baseline-reverified.json).
Product implementation is committed as
`c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60` on local branch
`codex/scope-convergence`. Earlier reviewable commits are `000d3be` (historical
incorrect basis, retained) and `e66935f` (authorized source correction).

## Delivered behavior

Protocol `2.1.0-dev.1`, Python `bugate-core 2.1.0a1`, and native DSH package
`2.1.0-dev.1` implement ScopeContract, captured ScopeRequest and non-executable
Suggestion. The existing inventory remains the only plan. Scope/plan/environment
bindings are checked at admission, execution and claims; canonical context
recovery carries the same scope. Suggested extensions cannot enter a required
plan, invalidate execution inputs, or trigger completion rework merely because
they remain suggestions. Lawful auxiliary work and failure-linked bounded
diagnosis remain available.

Execution closure, functional results, blockers, suggestions and historical
failures are reported separately. A genuine failed Oracle can complete execution
without verifying the requirement. Named requirement verification is also separate
from whole-scope completeness: verifying R1 does not mark unobserved R2 complete.
Schemas, MethodSpec, rules/reasons, CLI, native tools/Skill, packaging/CI and
English/Chinese usage documentation are synchronized.

The Profile denominator remains locked at every scope revision. An explicitly
authorized branch change inside it creates a new scope version. A different
requirement/risk denominator requires a new Profile version and task; original
facts/failures remain. Scope changes conservatively invalidate old evidence and
require fresh execution; no selective reuse or same-task Profile migration is
claimed. See [architecture](ARCHITECTURE.md) and [review findings](REVIEW.md).

## Actual verification

| Check | Actual result | Evidence |
|---|---|---|
| Final core v2 regression | 123 tests PASS, exit 0 | [command/result](evidence/core-tests.result.json), [raw log](evidence/core-tests.stderr), [123-test mapping](evidence/core-mapping.json) |
| Actual DSH complete regression | 24 tests PASS, exit 0; exact run snapshot recorded | [result](dsh/runtime-result.json), [raw log](dsh/runtime-final.log) |
| Final named-claim fix: affected actual DSH paths | 2 tests PASS, exit 0; source authoring and normal/failing convergence workflows | [result](dsh/runtime-after-named-scope-fix-result.json), [raw log](dsh/runtime-after-named-scope-fix.log) |
| Distribution / installer boundaries | 4 + 15 tests PASS | [package index](packaging/verification-summary.json) |
| Final source-denied clean install/repeat/doctor/upgrade/rollback/remove/recovery | 20 commands PASS on final package | [raw results](packaging/clean-install/result.json), [executed commands](packaging/clean-install/commands.json), [denial attestation](packaging/source-denial-attestation.json) |
| Actual dev.2 archive upgrade / rejection of old binding / rollback | 9 commands PASS | [result](packaging/actual-upgrade/result.json), [commands](packaging/actual-upgrade/commands.json) |
| Exact installed package + OS source denial + actual DSH | 1 integration test with 2 complete workflows PASS, exit 0 | [exact result](dsh/installed-runtime-result.json), [raw log](dsh/installed-runtime-final.log), [actual session traces](dsh/installed-session-traces/index.json) |
| Source/package equality and checksums | 57 build inputs match product commit; finite 50 native / 46 wheel members and 28 protocol resources; all published local checksums match | [source match](packaging/source-commit-match.json), [boundary](packaging/bundle-boundaries.json), [independent checksum readback](evidence/final-checksums.json) |

The complete DSH run predates the final two-line named-claim fix; the subsequent
2-test affected-path run and final installed-package run identify the final source
separately. They are not described as a full 24-test rerun on later bytes. The
actual host is DSH `0.2.2-alpha.2`, Node `26.8.1`, Python `3.13.3` on macOS arm64.
Model behavior is deterministic fixture-driven; **live-model validation was not
run**. Hosted CI was updated, not triggered; the applicable commands ran locally.

Two actual defects were preserved and repaired without changing expected outcomes:
suggestion re-assessment conflicted at an immutable result key; named R1
verification incorrectly required R2 execution. [Runtime finding](dsh/RUNTIME_FINDINGS.md)
and [independent before/after evidence](evidence/independent-review.json) preserve
the failures. An installer-test metadata variable also shadowed the denied root;
its two-line name correction changes no installation behavior. The original raw
result is retained, and the [attestation](packaging/source-denial-attestation.json)
checks the saved sandbox command/probe evidence. No installer rerun is falsely
claimed for that metadata-only correction. Intermediate packages/results are
labelled intermediate and are not used as final artifact acceptance.

## Formal requirement to evidence

| Formal item | Evidence closure |
|---|---|
| F-01 first-class shared scope | Scope/core mapping, actual authoring, shared package resources, installed runtime. |
| F-02 no automatic expansion or advisory blockage | Core extension/draft/suggestion checks; actual rejected inventory/command attempts, unchanged completed claim and no rework. |
| F-03 authentic provenance / Profile / traceable changes | Core request/version/Profile checks; actual synthetic subagent/rework rejection; preserved history. |
| F-04 real evidence and separate outcomes | Core authenticity/skip/error/partial-claim regressions; actual passing and failing runs; immutable advisory assessments. |
| F-05 real native execution and bounded support | Actual DSH tool/guard/provider/barrier/cancel/timeout/probe/diagnosis runs. |
| F-06 binding / recovery / isolation | Core stale-plan/snapshot/path/concurrency checks; actual 50-turn compaction, cold restart, spawn/fork/preset and multi-task regressions. |
| F-07 independent native package | Exact final archive, source-denied installation, actual upgrade/rollback and installed runtime. |
| F-08 consistent bounded delivery | Source correction, bilingual docs, evidence mapping, checksums, matching install Prompt and reviewable commits. |

Detailed test names and commands are in [core mapping](evidence/core-mapping.json),
[DSH report](dsh/REPORT.md) and [package report](packaging/REPORT.md).

## Installable deliverables and boundaries

All six deliverables are in [dist/scope-convergence](../../../dist/scope-convergence).
Use the [exact one-step installation Prompt](../../../INSTALL_DSH_PROMPT.md), which
checks the five content hashes in [SHA256SUMS](../../../dist/scope-convergence/SHA256SUMS)
before installing into explicit external project/profile targets.

- [Native bundle](../../../dist/scope-convergence/bugate-deepseek-harness-2.1.0-dev.1.tgz):
  `4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`.
- [Python wheel](../../../dist/scope-convergence/bugate_core-2.1.0a1-py3-none-any.whl):
  `89061edf1f560f5ce7c0d79366faf329bf8186d73f5115959ae00ddebd06dafa`.
- Standalone installer, build/source manifests and checksum list accompany them.

The bundled offline dependencies target CPython 3.13/macOS arm64 as recorded in
[wheel-target.json](packaging/wheel-target.json); installation checks ABI. DSH peer
dependency resolution may use a registry. Scope checks enforce declared managed
activities and exact bindings, not arbitrary shell semantics or hostile same-user
authentication. Semantic source judgment is not a mathematical proof. No live-model
quality/efficiency benefit or whole-product safety claim is made.

No agreed implementation or verification item remains incomplete. Live-model
validation is not run and is not claimed. The [machine acceptance record](acceptance.json)
and [final reconciliation](evidence/final-reconciliation.json) index the completed
formal items. No public release/tag was published or overwritten, and no remote
branch was pushed.

The final whitespace check reports only blank terminal output in three retained
raw observation files; source and document changes pass the scoped check. Those
logs are preserved byte-for-byte in the [evidence manifest](evidence-index.json).
