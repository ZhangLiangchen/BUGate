# ADR: TypeScript implementation within DSH's Cordis architecture

English | [简体中文](TYPESCRIPT_CORDIS.zh-CN.md)

**Decision:** Accepted, 2026-10-29.
**Implementation:** IN_PROGRESS, authorized 2026-10-30 from `eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654`. Final installed Team acceptance and cutover are not yet claimed.
**Authority:** The user's explicit decision in this task: DSH remains the only
host; move BUGate fully to TypeScript within Cordis and record the next development
plan. That earlier single-target decision is now scoped by the 2026-10-30
[dual-branch decision](PYTHON_FIELD_TRIAL.md): improve Python on its own branch
for the first field trial, while this full TypeScript track continues independently.
Neither track is cancelled or rolled back; quality and evidence invariants remain.

The 2026-10-30 execution request authorizes phases 0–5. This working plan now
records implementation; earlier documentation-only/NOT_RUN wording described
the prior planning task. [Protocol v2](../../protocol/v2/manifest.yaml) remains
the sole machine authority. The new `2.2.0-dev.1` protocol versions the explicitly
authorized Team provenance enhancement; this plan is not a default SUT matrix.

**Track boundary:** this plan governs `codex/typescript-cordis-team`. Python
retirement, no-Python packaging and cutover below apply only to the TS package
and branch, not to the independent Python improvement. TS acceptance does not
automatically replace the Python field-trial installation. The Python trial
does not wait for completion of these migration phases.

## Context and historical baseline

The implementation baseline is main `eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654`.
`3a5fcc7ae2b35f8eb361896c932d688939364bae` identifies the historical published Python baseline.
The [historical Python release](../releases/DSH_2_1_0_DEV_1.md) uses Python core `2.1.0a1`
and Protocol/native package `2.1.0-dev.1`. Its evidence applies to its recorded
source and artifact digests, not to the planned migration.

The historical baseline already had a native Cordis plugin:
[`cordis.patch.yml`](https://github.com/ZhangLiangchen/BUGate/blob/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/adapters/deepseek-harness/plugin/cordis.patch.yml)
registered `@bugate/deepseek-harness` with `pythonExecutable: python3`.
The baseline `index.ts` handled native integration; [`bridge.ts`](https://github.com/ZhangLiangchen/BUGate/blob/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/adapters/deepseek-harness/plugin/src/bridge.ts)
invokes Python through DSH providers. The target replaces that product bridge
and Python implementation; it does not introduce Cordis integration from scratch.

Historical starting points (implementation now lives in `packages/core/`, `packages/cli/` and Node `packaging/`):

- [`src/bugate/`](https://github.com/ZhangLiangchen/BUGate/tree/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/src/bugate/): `protocol.py`, `assessment.py`, `semantics.py`
  and `scope.py` hold validation/evaluation; `workspace.py` holds identity and
  persistence; `authoring.py`/`context.py` handle authoring/preparation;
  `evidence.py`, `cli.py`, `integrations.py` handle collection, CLI and installation.
- [`adapters/deepseek-harness/`](../../adapters/deepseek-harness/): native plugin,
  provider/lifecycle integration and actual-runtime tests.
- [`packaging/`](../../packaging/), [`scripts/`](../../scripts/), [`tests/`](../../tests/)
  and [CI](../../.github/workflows/ci.yml): first-party build/install/doctor/
  distribution/verification tooling is included in the TypeScript/Node target.

JSON, YAML and documentation keep their formats. External SUT tests may use any
required language, including Python. Neutral fixtures and product verification
entry points must not create an implicit Python prerequisite for the TS product.

## Decision and boundaries

The target has a shared TypeScript evaluation core, a unified Node CLI and a
Cordis native service/plugin consuming that same implementation. Business
semantics remain SUT-neutral and independent of the DSH Agent loop. DSH owns
execution, scheduling, lifecycle and bounded rework; BUGate adds no Agent loop,
permanent dual runtime or alternative-host framework.

Separate pure evaluation from workspace/process/evidence effects. Use configured
DSH filesystem/subprocess providers and an explicit CLI execution context. DSH
being the only host does not imply local filesystem access or one process
namespace; opaque targets are not paths. TypeScript does not mean everything
runs in the plugin event loop: CPU-heavy validation/hashing, IO in another
execution world, cancellation and cleanup need an explicit, tested placement
and resource-boundary design. Worker/process selection remains an implementation
choice; this plan does not assert a new SDK or transport capability.

The current package pins DSH `0.2.2-alpha.2` and Cordis `4.0.6-alpha.1`. Existing
`apply(ctx, config)`/injection, synchronous `context.text` and `tools.guard`,
awaited `agent/created`, `agent/pre-step`, `agent/request` and `tools/pre-execute`,
model-admission checks, provider calls and teardown are the starting seams.
Reinspect actual selected DSH source/types/packages before implementation; do not
invent hooks, cancellation APIs or child-guard inheritance. Await preparation
before synchronous guards consume it and durable evidence before tool return.

## DSH official Agent Team multi-perspective cross-review (user-added decision)

**Source and status:** The user first requested native subagent review with
actual provenance, independence and artifact binding, then explicitly selected
reuse of DSH's official Agent Team plugin. The latest decision takes precedence:
Team members perform cross-review; BUGate validates actual provenance,
independence and artifact binding rather than different reviewer names, without
restoring Claude/Codex dependencies. Accepted; implementation and verification are **IN_PROGRESS**. Improved efficiency is the user's expectation, not a measured
result. The
current independence check in [`assessment.py`](https://github.com/ZhangLiangchen/BUGate/tree/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/src/bugate/assessment.py)
primarily compares producer/reviewer names. Existing [DSH subagent tests](https://github.com/ZhangLiangchen/BUGate/blob/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/adapters/deepseek-harness/tests/runtime.test.mjs)
prove some binding and authorization-provenance behavior, not a complete
cross-review workflow. This is a user-sourced, versioned enhancement, not a claim
that the previous protocol already guaranteed it.

One DSH host can run multiple Agents. Prefer the official Agent Team for a finite
set of necessary review perspectives. DSH owns roster, task board, messages,
delegation, execution, cancellation and lifecycle. BUGate keeps review artifacts,
necessary host references and assessments; it does not duplicate Team's state
machine or add an Agent loop, scheduler or Claude Code/Codex dependency. Subagents
may be the official Team implementation detail or part of an explicitly justified
fallback design. Direct subagent calls cannot silently replace Team and count as
completion of this requirement.

**Inspected interfaces, not runtime acceptance:** Local `package.json`, README
and type declarations under
`adapters/deepseek-harness/plugin/node_modules/@deepseek-ai/dsh-experimental-agent-team`
identify official [`packages/experimental/agent-team`](https://github.com/deepseek-ai/deepseek-harness/tree/main/packages/experimental/agent-team)
version `0.2.2-alpha.2`, with companion `dsh-experimental-tool-agent-team` and
`dsh-experimental-agent-team-profile` packages. `ctx.agentTeams` exposes membership
using the exact live calling Agent, member lookup, spawnTeammate, sendMessage,
task lookup/creation/expectedRevision CAS updates, waitForChange and interrupt.
TeamId relates to the Lead SessionId; members may start fresh or fork. The official
Team profile mounts Team and disables direct subagent/control/fork tools while
Team itself still depends on dsh-subagent. Recheck the selected version and
configuration before implementation; experimental does not mean a stable API.

Team persists roster and task state. A successful send proves inbox acceptance,
not model processing or a separate synchronous flush. The current implementation
shares a cwd within one process, offers no worktree isolation, and treats
writeScopes as hints rather than write protection. Task ownership is not
automatically released, and sends have no automatic retry/resend deduplication
guarantee. Design recovery, cancellation, concurrency and review publication
barriers around these actual semantics. Team task completion proves neither review
validity nor business acceptance. No actual Team integration run occurred in this
documentation update.

- **Applicability:** The effective Profile and adopted requirements determine
  whether independent review is required and which perspectives are needed.
  Each perspective explains its proposition and necessity. Do not impose fixed
  role/model/round counts or all-pairs review. Out-of-scope opportunities remain
  suggestions, not new SUT goals. Review has observable completion conditions and
  bounded rework of its own.
- **Actual provenance:** Capture both the artifact author's and reviewer's
  Agent/session and Team/member identities, delegation/parent-child relations and actual outcomes
  at real DSH execution seams, binding review inputs and persisted outputs.
  Model-supplied names, session IDs, model names or independence claims are not
  host observations. Parent-authored review text may remain model material but
  cannot masquerade as a Team member's actual output. Missing author provenance also
  prevents independence from being established.
- **Independence boundary:** Check host-verifiable author/reviewer execution
  relations and input-context policy, rejecting renamed self-review. Separate
  observable execution independence from business review quality: a new session,
  another Agent or another model alone proves neither cognitive independence nor
  correctness. Record fresh/fork Team member creation and inherited context; a fork is not a blind
  review. Model diversity is neither a default requirement nor substitute evidence.
  Unobservable required facts or an unmet independence policy produce explicit
  gaps, never invented success.
- **Exact binding and invalidation:** Bind reviews to task, protocol/evaluator/
  Profile, current scope/plan, artifact IDs/digests, reviewed input snapshot and
  review execution identity. Define applicable fields and non-circular input
  snapshot boundaries in the sole machine specification during implementation.
  Changed artifacts or relevant inputs stale the review. Cross-task, stale or
  replayed records cannot authorize a current claim. Resume recomputes validity
  from persisted facts, not inherited parent-local state.
- **Cross-checking and conclusions:** Preserve each perspective's original
  opinion, sources, findings, counterevidence and limits. Link subsequent Team
  discussion/rebuttal provenance and revisions to evidence-backed adoption,
  rejection or unresolved disposition of disagreements about the same proposition.
  Parallel agreement alone is not cross-review; the Lead cannot erase dissent.
  Majority votes, different names and matching pass flags do not replace business
  Oracles or hide unresolved in-scope issues. Preserve conclusion history; changed
  artifacts require review of the current version, without unbounded attempts to
  obtain unanimity. Failed, cancelled, timed-out or missing-output reviews are
  not complete. Required review gaps affect relevant claims without blocking
  lawful auxiliary work.
- **Persistence and trust:** Distinguish host observations from model semantic
  opinions, normalizing within the existing artifact/evidence/assessment system.
  Durably publish outputs and provenance before they can authorize a claim.
  CLI, shared core and plugin consume one specification; importing model JSON
  through the CLI cannot manufacture host review receipts. Assurance covers
  observations in the managed local host, not tamper resistance against an actor
  with equal filesystem privileges or a promise to find every defect.

Phase 0 maps these user requirements to specifications/Oracles; phase 1 adds
versioned Schemas, MethodSpec, reason codes and evaluation; phase 3 implements the
official Team workflow; phase 4 verifies the installed package. Minimum sufficient checks
distinguish actual multi-perspective Team member review and disagreement handling on
exact artifacts; renamed self-review, fabricated identities/parent-authored results
and absent provenance; changed artifacts/cross-task/replayed records; and resume,
cancellation and durable return. Select and combine checks by these requirements
and actual interfaces, without a fixed count. Deterministic fixtures must drive
real DSH Agent Team, members and actual tools. Mocks, prewritten review JSON and historical
subagent tests alone do not prove this enhancement. The exact installed package
must also run the review path with source access denied and Python unavailable;
live-model quality checks are reported separately.

## Preserved invariants and comparison policy

Sources are the user's decision, [AGENTS](../../AGENTS.md),
[METHOD](../qa-methodology/METHOD.md), [SOP](../qa-methodology/SOP.md) and current
canonical v2 contracts. Necessary migration checks cover:

- **Runtime meaning:** runtime JSON Schema validation remains required despite
  TS types; canonical object/file bytes follow declared domain separation,
  UTF-8/key ordering and safe-integer rules. Keep verdicts, reason codes,
  assessment dimensions, JSON envelopes and exit semantics source-backed.
- **Scope and authority:** fixed Profile denominators, actual user provenance,
  explicit branch/Oracle necessity and semantic review; suggestions remain
  non-executable/nonblocking, with advisory assessment identity separate from
  authorization snapshots. Lawful auxiliary work and bounded diagnosis remain.
- **Consistency:** exact claim/evaluator/protocol/Profile/scope/plan/evidence
  binding; atomic persistence, immutable records, expected-digest CAS, locks,
  concurrent writers, task isolation and path/symlink boundaries.
- **Observation:** real collector/report evidence, provenance distinctions,
  timeout/cancellation/output bounds and durable publication. Preserve failures,
  infrastructure errors, zero tests, skip, xfail and unobserved results; execution
  closure and named requirement verification remain separate.
- **Lifecycle:** configured execution worlds, disposal/cleanup, cold resume,
  compaction, child/fork/independent presets and parallel-task isolation require
  actual host evidence, not assumed local-state inheritance.

A frozen Python baseline is a **development-only differential reference**, never
a shipped fallback. Canonical object/file digest vectors must agree where the
contract requires; evaluator implementation identity is expected to change from
Python to TS. Version/identity differences must be explicit, not normalized away
to inherit an old authorization. Business verdicts/reasons and evidence
applicability still need their contract Oracles. Classify mismatches as port
bugs, baseline bugs or intentional versioned changes; preserve counterexamples
and do not reproduce known bugs or weaken assertions for equality.

Existing valid tests are reused by requirement and change impact. SC labels and
historical 123/24 totals are observations, not fixed migration gates.

## Finite implementation phases

Phase progress is recorded against actual evidence below; historical counts are
not gates. Phase 0 uses existing verified tools to establish a finite baseline; it does not
require the not-yet-built TS evaluator to authorize its own migration.

| Phase | Requirement / deliverable | Observable Oracle and minimum sufficient checks | Exit condition |
|---|---|---|---|
| 0. Requirements, Oracles, baseline | Freeze selected contracts/source and known defects; map changed boundaries, Python dependencies and actual DSH interfaces. Decide version/binding and historical inspection policy; verify observable author/reviewer provenance and Team identity and member independence boundaries. | Source-backed requirement/Oracle map; canonical vectors; targeted baseline counterexamples; actual provider/interface probes. | Every planned change has a necessity source and discriminating Oracle; material gaps resolved or explicitly blocked, no placeholder implementation approval; review provenance/context policy has a verifiable design. |
| 1. Core and Node CLI | Port validation, semantics/scope/claims, authoring/context and unified CLI with explicit IO boundaries; version native review provenance, independence and binding rules. | Relevant categories from [core tests](../../tests/typescript/): real verdict/reason expectations, malformed JSON, canonical vectors, stale refs and advisory isolation; development differential comparison with documented identity differences. | Runtime checks and output semantics meet their Oracles; disagreements have evidence-backed dispositions; names or self-reported identities cannot satisfy host independent-review requirements. |
| 2. Execution, storage, collector | Port workspace identity, atomic publication/CAS/locks and process/report capture; design execution-world, CPU/IO and cleanup boundaries. | Neutral real commands for success/failure/skip/xfail, missing report, timeout/cancel; necessary concurrent-writer/path/world mismatch and durable-return checks. | No false completion on interruption; correct configured world and bounded cleanup, unsupported mappings explicitly rejected. |
| 3. Cordis native integration | Replace PythonBridge with shared TS services at verified native seams; preserve context/admission, provenance and bounded rework; reuse DSH official Agent Team for multi-perspective review with persisted provenance. | [Real DSH test categories](../../adapters/deepseek-harness/tests/README.md): deterministic fixtures drive actual tools/temporary SUT commands for scope, suggestions, failures, aux/diagnosis, recovery/children/isolation and cancellation, plus the review success/counterexamples above. | Actual host calls demonstrate enforcement, review provenance/independence/binding and evidence barriers. Mock contexts alone do not close the phase; live-model checks, if run, are separate. |
| 4. Node packaging and CI | Port first-party build/install/doctor/distribution/test entry points; produce finite npm assets, canonical projections and matching usage/CI. | Clean installation with source reads denied and Python unavailable; digest/resources; applicable repeat/doctor/upgrade/rollback/remove/ownership checks; installed real DSH workflows including multi-perspective cross-review. Run declared CI checks and report actual platforms. | Exact assets work independently; checksums/docs agree. No platform claim from TS alone, including Windows. |
| 5. TS-branch Python retirement and cutover | Retire this branch's bridge/bootstrap/wheelhouse, first-party Python tooling and production fallback after replacements are verified; retain the independent Python track. | Finite package/dependency inspection; product install/runtime/verification without Python; explicit old-binding rejection, retained history and owned uninstall/rollback checks. | One supported TS product path, no implicit Python requirement. Historical releases remain intact; later publication has its own authorized delivery. |

## Version, evidence and installation cutover

The protocol/evaluator/package version is `2.2.0-dev.1` for the explicitly
authorized host provenance and independence enhancement. Existing versions are
not silently reinterpreted. Old Python assessments/receipts cannot authorize TS
execution because fields happen to match. The default cutover uses explicit new
tasks/bindings while preserving old scopes, evidence, failures and task history.
Phase 0 defines read-only historical inspection without adding automatic format
conversion, cross-version authorization or selective evidence reuse. Existing
within-task scope invalidation and Profile-denominator rules remain effective.

The final TS product has no Python bootstrap/wheelhouse requirement. Existing user
Python environments remain until an explicit owned-uninstall action, limited to
proven BUGate-owned state. Preserve SUT dependencies, task facts and unrelated
configuration; do not scan or clean global environments. Node/npm installation
must prove ownership, interrupted-operation recovery and rollback; choosing npm
alone establishes none of these properties.

## Options, consequences and limits

| Option | Decision / tradeoff |
|---|---|
| Python core plus TS native plugin | Accepted as an independent improvement track under the 2026-10-30 decision; its verified delivery serves the first field trial. |
| Shared TS core + Node CLI + native Cordis | **Accepted.** One first-party language/toolchain and eventual removal of Python bootstrap/bridge; requires preserving runtime/persistence semantics and effect boundaries. |
| Permanent dual runtime in one package or generic multi-host layer | Not adopted. Independent source branches and separately verified packages are not a dual-evaluator runtime. |

Risks are serialization/validator drift, local-world assumptions, event-loop
blocking, cancellation/drain loss, weaker persistence, synthetic user authority
and stale binding reuse. The phases above address their direct migration impact.
No measured speed/model-quality benefit, new platform, additional host or extra
SUT risk category is promised. Other opportunities remain suggestions.

**Current execution:** shared core/CLI, Node lifecycle and native Team integration
are implemented and undergoing integration. The candidate Node lifecycle passed
OS-isolated install/repeat/doctor/upgrade/rollback/remove/interruption recovery on
macOS arm64, but that candidate is not the final Team-enabled delivery. Final
exact-package acceptance and Python retirement remain required. Live-model
validation is NOT_RUN. No public release/tag has been published.
