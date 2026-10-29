# ADR: TypeScript implementation within DSH's Cordis architecture

English | [简体中文](TYPESCRIPT_CORDIS.zh-CN.md)

**Decision:** Accepted, 2026-10-29.  
**Implementation:** Planned; not started. All migration checks: **NOT_RUN**.  
**Authority:** The user's explicit decision in this task: DSH remains the only
host; move BUGate fully to TypeScript within Cordis and record the next development
plan. This supersedes the earlier suggestion to retain Python as the target
architecture, without weakening existing quality or evidence invariants.

This task changes explanatory documents only. It does not migrate code, change
versions or machine rules, alter published assets, or prohibit maintenance of the
current Python implementation. [Protocol v2](../../protocol/v2/manifest.yaml)
remains the sole machine authority; this plan is not a second specification or a
default acceptance matrix for other development tasks or SUTs.

## Context and baseline

The inspected baseline is `3a5fcc7ae2b35f8eb361896c932d688939364bae`.
The [current release](../releases/DSH_2_1_0_DEV_1.md) uses Python core `2.1.0a1`
and Protocol/native package `2.1.0-dev.1`. Its evidence applies to its recorded
source and artifact digests, not to the planned migration.

BUGate already has a native Cordis plugin:
[`cordis.patch.yml`](../../adapters/deepseek-harness/plugin/cordis.patch.yml)
registers `@bugate/deepseek-harness` with `pythonExecutable: python3`.
[`index.ts`](../../adapters/deepseek-harness/plugin/src/index.ts) handles native
integration; [`bridge.ts`](../../adapters/deepseek-harness/plugin/src/bridge.ts)
invokes Python through DSH providers. The target replaces that product bridge
and Python implementation; it does not introduce Cordis integration from scratch.

Implementation starting points:

- [`src/bugate/`](../../src/bugate/): `protocol.py`, `assessment.py`, `semantics.py`
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

Every row is **NOT_RUN**. Each phase records its actual source/command/artifact
identity and results; this documentation update satisfies none of these exits.
Phase 0 uses existing verified tools to establish a finite baseline; it does not
require the not-yet-built TS evaluator to authorize its own migration.

| Phase | Requirement / deliverable | Observable Oracle and minimum sufficient checks | Exit condition |
|---|---|---|---|
| 0. Requirements, Oracles, baseline | Freeze selected contracts/source and known defects; map changed boundaries, Python dependencies and actual DSH interfaces. Decide version/binding and historical inspection policy. | Source-backed requirement/Oracle map; canonical vectors; targeted baseline counterexamples; actual provider/interface probes. | Every planned change has a necessity source and discriminating Oracle; material gaps resolved or explicitly blocked, no placeholder implementation approval. |
| 1. Core and Node CLI | Port validation, semantics/scope/claims, authoring/context and unified CLI with explicit IO boundaries. | Relevant categories from [core tests](../../tests/v2/): real verdict/reason expectations, malformed JSON, canonical vectors, stale refs and advisory isolation; development differential comparison with documented identity differences. | Runtime checks and output semantics meet their Oracles; disagreements have evidence-backed dispositions. |
| 2. Execution, storage, collector | Port workspace identity, atomic publication/CAS/locks and process/report capture; design execution-world, CPU/IO and cleanup boundaries. | Neutral real commands for success/failure/skip/xfail, missing report, timeout/cancel; necessary concurrent-writer/path/world mismatch and durable-return checks. | No false completion on interruption; correct configured world and bounded cleanup, unsupported mappings explicitly rejected. |
| 3. Cordis native integration | Replace PythonBridge with shared TS services at verified native seams; preserve context/admission, provenance and bounded rework. | [Real DSH test categories](../../adapters/deepseek-harness/tests/README.md): deterministic fixtures drive actual tools/temporary SUT commands for scope, suggestions, failures, aux/diagnosis, recovery/children/isolation and cancellation. | Actual host calls demonstrate enforcement and evidence barriers. Mock contexts alone do not close the phase; live-model checks, if run, are separate. |
| 4. Node packaging and CI | Port first-party build/install/doctor/distribution/test entry points; produce finite npm assets, canonical projections and matching usage/CI. | Clean installation with source reads denied and Python unavailable; digest/resources; applicable repeat/doctor/upgrade/rollback/remove/ownership checks; installed real DSH workflows. Run declared CI checks and report actual platforms. | Exact assets work independently; checksums/docs agree. No platform claim from TS alone, including Windows. |
| 5. Python retirement and cutover | Retire product bridge/bootstrap/wheelhouse, first-party Python tooling and production fallback after replacements are verified. | Finite package/dependency inspection; product install/runtime/verification without Python; explicit old-binding rejection, retained history and owned uninstall/rollback checks. | One supported TS product path, no implicit Python requirement. Historical releases remain intact; later publication has its own authorized delivery. |

## Version, evidence and installation cutover

Choose protocol/evaluator/package versions during implementation from the actual
compatibility impact; assign no new version now and do not silently change an
existing version's meaning. Old Python assessments/receipts cannot authorize TS
execution because fields happen to match. The default cutover uses explicit new
tasks/bindings while preserving old scopes, evidence, failures and task history.
Phase 0 defines read-only historical inspection without adding automatic format
conversion, cross-version authorization or selective evidence reuse. Existing
within-task scope invalidation and Profile-denominator rules remain effective.

The final product has no Python bootstrap/wheelhouse requirement. Existing user
Python environments remain until an explicit owned-uninstall action, limited to
proven BUGate-owned state. Preserve SUT dependencies, task facts and unrelated
configuration; do not scan or clean global environments. Node/npm installation
must prove ownership, interrupted-operation recovery and rollback; choosing npm
alone establishes none of these properties.

## Options, consequences and limits

| Option | Decision / tradeoff |
|---|---|
| Keep Python core plus TS bridge as target | Delivered baseline, superseded as the target by the user; remains maintainable until cutover. |
| Shared TS core + Node CLI + native Cordis | **Accepted.** One first-party language/toolchain and eventual removal of Python bootstrap/bridge; requires preserving runtime/persistence semantics and effect boundaries. |
| Permanent dual runtime or generic multi-host layer | Not adopted; outside this decision. Temporary development comparison is sufficient. |

Risks are serialization/validator drift, local-world assumptions, event-loop
blocking, cancellation/drain loss, weaker persistence, synthetic user authority
and stale binding reuse. The phases above address their direct migration impact.
No measured speed/model-quality benefit, new platform, additional host or extra
SUT risk category is promised. Other opportunities remain suggestions.

**Next action:** begin phase 0 in the next implementation task. This task performs
only plan/navigation edits and Markdown link/whitespace checks. No port, product
test, package build, runtime validation, uninstall or release is claimed; all
migration evidence remains **NOT_RUN**.
