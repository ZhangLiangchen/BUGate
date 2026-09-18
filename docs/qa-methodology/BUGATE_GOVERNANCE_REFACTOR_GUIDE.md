# BUGate governance-kernel refactoring guide

English | [简体中文](BUGATE_GOVERNANCE_REFACTOR_GUIDE.zh-CN.md)

- Plan revision: `2026-09-18.1`.
- Status: implementation guidance for accepted ADR-BUGATE-006; the work packages below are **not implemented by this document**.
- Inspected baseline: [`e12d065`](https://github.com/ZhangLiangchen/BUGate/commit/e12d065615717c9c78f61be7e224787b00d9d604).
- Decision: [Governance/runtime boundary ADR](BUGATE_RUNTIME_BOUNDARY_ADR.md).
- Companion: [HyperTest implementation guide](https://github.com/ZhangLiangchen/hypertest/blob/main/docs/governance-runtime-refactor-guide.md).

## 1. Direction and scope

Make BUGate easier to invoke and harder to bypass, without turning it into an
agent or workflow framework. Retain methodology, evidence requirements, policy,
authorization, lineage, and audit. Move generic scheduling and execution loops
to hosts such as HyperTest. Do **not** introduce LangGraph into BUGate.

The first deliverable is a stable governance contract plus characterization
tests, not a rewrite of `role_governance.py`, a new database, or deletion of
`sdtd_orchestrator.py`. The existing skill/CLI/hook/CI use cases remain supported.

This guide operationalizes the ADR. It does not silently amend CHARTER, the
frozen role protocol, imported-updater ownership, or self-healing policy. Any
change to those contracts needs its own reviewed protocol amendment and
migration evidence. Proposed operation/schema names below are design vocabulary,
not commands available in the current release.

## 2. Current implementation versus target

| Current source | Verified current responsibility | Required direction |
|---|---|---|
| [`scripts/sdtd_orchestrator.py`](../../scripts/sdtd_orchestrator.py) | `init`, `status`, `auto_precode`, `auto_postrun`, and self-heal routing; includes role preflight and degraded-peer checks | Keep primitives; preserve `--auto` as compatibility composition; extract dispatch only after an equivalent host exists |
| [`scripts/role_governance.py`](../../scripts/role_governance.py) | Role policy, lifecycle validation, snapshots, receipt verification/publication, strict Memory integration and recovery | Separate pure policy from receipt/audit and CLI concerns incrementally; retain governance transaction recovery |
| [`scripts/role_lineage.py`](../../scripts/role_lineage.py) | SQLite registry, short transactions, head CAS, initialization/publication journals and checkpoint verification | Reuse and test these mechanisms; they are not generic workflow state to replace with LangGraph |
| `scripts/check_bugate*.py`, `check_role_evidence.py`, `check_agent_role_paths.py` | Existing semantic and write-admission enforcement | Preserve decisions, UC binding, reason semantics and fail-closed behavior across adapters |
| Multiview/adversarial bridges and report generators | Mixture of worker invocation, transformation and review acceptance | Host owns worker lifetime; BUGate retains schemas, acceptance and deterministic transforms |
| Skills, templates and `bin/bugate-role` | User-facing method and current governance publisher | Preserve entrypoints; make them clients of the same primitives, not a second policy implementation |

The current registry already uses CAS and journals. Do not list them as missing
features. What remains to design is **per-action authorization consumption and
effect reconciliation across the host boundary**, not the existing role-chain
publication mechanism.

## 3. Non-negotiable invariants

1. Core remains SUT-neutral and standard-library-only. Real SUT data stays in
   the imported test repository; tests use temporary neutral fixtures.
2. Preserve pre-code gates, proposition/oracle traceability, role separation,
   human acceptance, evidence freshness, and repair safety. A different runtime
   must not lower quality requirements.
3. Existing `00_role_evidence/` receipts and hashes remain byte-stable. Do not
   add action-consumption events to the frozen lifecycle vocabulary.
4. First-use, adoption and recovery remain explicit operator actions. An engine
   update must not initialize, adopt, reset or migrate governance history.
5. Keep required Memory and best-effort semantics distinct. Required-mode
   unavailability is not a reason to substitute local evidence or a graph
   checkpoint. Ordinary existing hooks/status retain local verification and do
   not acquire per-write Memory network calls.
6. A decision, a consumed authorization, and a completed external effect are
   different facts. Unknown external results must remain unknown.
7. A hash chain detects some tampering; it does not authenticate a human, make
   evidence factually true, or defeat an actor controlling all local anchors.

## 4. Target module boundaries

Use logical seams first; file moves are optional follow-up changes.

| Seam | Owns | Must not own |
|---|---|---|
| Policy evaluator | Evidence/role requirements, admissibility, typed obligations and reason codes | Agent planning, process lifetime or workflow retries |
| Governance audit service/library | Receipt validation, publication, idempotency, authoritative action state, lineage and recovery | Generic task queue or test scheduling |
| Host-facing operation API | Versioned requests/results and explicit capability negotiation | A hidden `--auto` workflow or host-specific policy fork |
| CLI/hook adapters | Input/output normalization and calls to shared primitives | Independent authorization shortcuts |
| Compatibility orchestrator | Existing deterministic convenience composition | New durable checkpoints, worker cluster or autonomous repair engine |

Start with extracted functions behind current imports, then small modules such
as policy/receipt/operation helpers. Check vendoring, archive manifests, the
stdlib scanner and CLI imports before changing package layout. Do not split
into separately distributed packages merely to match this table.

## 5. Shared integration contract (design baseline)

BUGate owns governance meanings and authoritative schemas. HyperTest owns its
client mapping and conformance tests. BG-1 publishes the future schema fixtures
and compatibility matrix in BUGate; until then this section is a planning
contract, not a new production wire protocol. Both repositories use the same
plan revision and link to this section instead of maintaining divergent schemas.

### 5.1 Operation families

| Proposed operation family | Meaning | Replay classification |
|---|---|---|
| Inspect/verify | Artifact, role and receipt checks | Read-only; no hidden initialization |
| Initialize/generate | Explicit creation or deterministic transformation | Mutating: require role/precondition checks; never overwrite accepted evidence |
| Decide | Evaluate a bound request and durably issue a decision where requested | Not purely read-only if issuing a receipt; deduplicate by stable request identity |
| Reserve action | Claim an eligible one-use action grant | Transactional CAS; stable operation identity |
| Record/reconcile outcome | Persist or resolve the result of the same operation | Idempotent; conflicting outcomes block rather than overwrite |

Use a process/JSON contract first, matching HyperTest's existing process bridge.
An authenticated service is an optional later host. Keep stdout machine-readable
and diagnostics on stderr. A valid `deny` or `needs_human` is a successful
decision response, not a transport exception to retry. Unknown schema or
unavailable authority blocks governed work. Preserve legacy CLI exit codes by
translation rather than changing them in place.

### 5.2 Binding and compatibility

The proposed action request binds stable `requestId` and `operationId`, action,
resource scope, source/workspace revision, patch or effect digest, evidence
references/hashes, policy/profile versions, role/session context and the
relevant governance anchor. Generate identities before dispatch and persist
them; retry must not generate a new identity for the same logical effect.

A decision adds verdict, receipt ID/hash, authority provenance, reason codes,
typed obligations, validity/revocation rules and (for a one-use action grant)
nonce. The outcome binds the grant and operation to result hashes, target IDs,
executor identity at the declared assurance tier, and an explicit result class.

Important distinctions:

- Lifecycle acceptance receipts remain durable evidence. They are **not all
  converted into consumable tokens**. A one-use action grant references the
  applicable lifecycle evidence and authorizes one scoped effect.
- Re-evaluate current evidence and policy immediately before dispatch. Do not
  reconstruct a new request with new UUIDs and then try to validate an old
  receipt against it.
- Use a versioned action sidecar anchored to the role lineage, analogous in
  compatibility strategy to the existing self-heal sidecar. The exact format
  requires review; do not modify frozen role receipts or reuse the self-heal
  namespace for unrelated actions.
- Define which role-head/revocation changes invalidate a grant. Appending its
  own reservation/outcome must not invalidate it through an accidental
  equality check against the action log's now-new head.
- Preserve the current HyperTest gate v1 contract through a legacy adapter.
  Enhanced enforcement requires explicit capability/version negotiation; never
  pretend a v1/static decision provides consumption, signing or strong identity.
- Specify cross-language canonical bytes and golden test vectors: Unicode,
  number limits, key ordering, missing versus null, array/set ordering and paths.
  Do not assume Python and TypeScript JSON serializers hash identically.
- Unknown obligations block. An `authority` string is a label, not issuer
  authentication. Local-process trust and managed authenticated trust must be
  declared separately; do not invent cryptography inside a stdlib-only core.

### 5.3 Effect recovery, not magical exactly-once

CAS guarantees one winning reservation in its transaction domain. It does not
make a filesystem patch, SCM call and SQLite/Memory update one atomic commit.
The target action journal distinguishes `authorized`, `reserved`, `dispatched`,
`succeeded`, `failed_no_effect` and `reconciliation_required` (proposed terms).

| Failure window | Required behavior |
|---|---|
| Before durable reservation | No protected effect; same request can be retried after revalidation |
| Reserved, before dispatch | Recover the same operation; verify lease/preconditions before dispatch |
| Target accepted effect, response or outcome missing | Reconcile using the stable target idempotency key or queryable operation/result ID; never blindly repeat |
| Completed effect and durable outcome, graph checkpoint missing | Return the existing verified outcome, not execute the effect again |
| Target cannot deduplicate or conclusively report result | Keep `reconciliation_required`; stop for operator review |

The PEP may have an execution journal/outbox for delivery, but BUGate remains
the authority for grant consumption. An outbox is not another independently
editable permission store. Persist intent before dispatch, fence stale workers,
and deliver result attestations idempotently. Lease expiry alone does not prove
that the old worker performed no effect. Compensation, if allowed, is another
authorized operation, not erasure of history.

## 6. Ordered BUGate work packages

Every row is planned. Use the IDs in commits/PRs and link evidence before marking
a row complete; they are work-package labels, not existing PR numbers.

| ID | Change | Entry dependency | Exit evidence / rollback |
|---|---|---|---|
| BG-0 | Characterize `--init`, status, `--auto`, peer degradation, role transitions, lineage failure/recovery and imported layouts | Current baseline | Golden outputs/receipts and failure matrix pass unchanged; no behavior change to roll back |
| BG-1 | Extract shared primitives and publish versioned process contract, schemas, canonicalization vectors and capability matrix | BG-0 | CLI and process API return equivalent policy results; unknown versions fail closed; legacy entrypoints remain |
| BG-2 | Separate pure role/policy checks from audit orchestration behind existing public APIs | BG-1 | Golden role bytes and all CAS/recovery tests unchanged; no mass rename or state migration; reversible implementation-only extraction |
| BG-3 | Add opt-in action-grant sidecar, reserve/outcome/reconcile API and compatibility checks | BG-1, BG-2; joint design with HT-2 | Duplicate, concurrent, drift, replay, crash-window and old-reader tests; disable new admission without discarding unresolved operations |
| BG-4 | Consolidate hook/CLI/HyperTest enforcement adapters over the same evaluator | BG-3 and HT-2 | Bypass-negative tests for the claimed assurance tier; local toolkit retained; disabling brokered mode must not reopen pending actions |
| BG-5 | Reduce `--auto` to the compatibility surface; retire only proven-replaced orchestration | BG-4, HT-4, published compatibility window | Command/options/parity inventory, migration guide, standalone acceptance, safe rollback; retain thin wrapper if users still need it |

BG-0 and HT-0 can proceed independently. BG-1 and HT-1 can then proceed in
parallel against agreed fixtures. Do not gate every BUGate cleanup on LangGraph
installation; ordinary primitive extraction is useful to all hosts.

## 7. Exact disposition of `sdtd_orchestrator.py`

| Current responsibility | Destination | Compatibility requirement |
|---|---|---|
| `init`, `status`, optional full-SDTD scaffolding | BUGate artifact primitives | Preserve required/advisory/off behavior and no-overwrite semantics |
| `_role_preflight`, acceptance freeze, UC/role checks | Shared BUGate policy called by every mutating entrypoint | Calling a primitive directly cannot bypass a check previously supplied by `--auto` |
| `readable_cases_stale`, semantic checks and generators | BUGate validation/transformation primitives | Preserve source-hash drift and corrupt-output checks |
| `peer_review_degraded`, review acceptance | BUGate review-result validation | A placeholder/partial dispatch must not become successful review merely because subprocess exit code is zero |
| `run_script`, peer worker launch, `auto_precode`/`auto_postrun` sequencing | HyperTest operations/workflow; old wrapper during migration | Preserve ordering where acceptance requires it; no accepted pre-code rewrite |
| Self-heal routing | BUGate sidecar commands and host-controlled loop | Explicit opt-in, independent review and existing lifecycle anchor remain |
| Human-readable lifecycle output | CLI adapter | Keep lifecycle status separate from workflow/self-heal/action status |

Do not move all `self_heal_gate.py` behavior to HyperTest: safety and acceptance
stay in BUGate. Do not treat a file as removable merely because its name contains
`orchestrator`, `journal`, `checkpoint`, or `recovery`.

## 8. Acceptance suite and performance evidence

For each changed enforcement surface require tests for allow, deny, unavailable,
malformed/unknown schema, stale evidence, wrong UC/profile/role/session, expired
grant, unhandled obligation, duplicate/concurrent reservation, missing/tampered
history, and all §5.3 crash windows. `needs_human` must never unlock an action by
itself. Include no-SUT/core, imported, vendored/plugin and CI invocation layouts.

Keep the existing lineage and orchestrator test suites, self-heal golden
fixtures and release/updater acceptance. The repository's current
`.github/workflows/ci.yml` is the full command authority. Focused examples:

```bash
python3 tests/test_role_governance.py
python3 tests/test_role_governance_lineage.py
python3 tests/test_role_lineage_registry.py
python3 tests/test_orchestrator_role_governance.py
python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
python3 scripts/check_no_sut_terms.py
```

Record p50/p95 decision/verification latency, bytes hashed, lock duration and
network calls on fixed fixtures before optimizing. Cache immutable evidence
verification only when the content/hash/anchor cannot change unnoticed; never
cache a mutable `allow` across source or policy drift. Compact verified
projections rather than deleting authoritative evidence to improve speed.

## 9. First increment, later work and completion checklist

**Next increment: BG-0 then BG-1.** Inventory public commands and guard sites,
capture fixtures, define the minimum request/result contract, and make a second
entrypoint call the exact same checks. Coordinate its vectors with HT-1.
Do not begin with signing, WORM, a remote policy service, or a new registry.

Managed credentials, cryptographic attestations and independent retention are
later options after a threat model establishes the need. Recovery of a local
controlled process is not proof of hostile-worker isolation.

- [ ] Existing CLI/skill/CI contract and golden receipts preserved.
- [ ] Policy, audit and compatibility composition have explicit owners.
- [ ] HyperTest can call primitives without `--auto`.
- [ ] Direct primitive calls enforce the same gates as the wrapper.
- [ ] New action grants distinguish authorization, reservation and outcome.
- [ ] Ambiguous effects reconcile or stop; no unsupported exactly-once claim.
- [ ] Upgrade, downgrade, active-operation handling and standalone support documented.
- [ ] Both repositories cite the same contract revision and compatibility results.

Completion means all relevant evidence is attached, not merely that a graph or
new directory exists. Each delivery records baseline/target SHAs, work-package
IDs, schema/capability versions, tests, unresolved risks and next dependency.
