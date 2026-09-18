# ADR-BUGATE-006 — Governance Kernel and Durable Runtime Boundary

- **Status:** accepted (2026-09-18)
- **Authority:** [`CHARTER.md`](../../CHARTER.md) and the Wave 7 governance invariants in [`ROLE_GOVERNANCE_PROTOCOL.md`](ROLE_GOVERNANCE_PROTOCOL.md)
- **Companion:** [HyperTest ADR-0005](https://github.com/ZhangLiangchen/hypertest/blob/main/docs/adr/0005-durable-workflow-runtime-and-bugate-boundary.md)
- **Language:** English | [简体中文](BUGATE_RUNTIME_BOUNDARY_ADR.zh-CN.md)

Implementation sequencing and acceptance: [BUGate refactoring guide](BUGATE_GOVERNANCE_REFACTOR_GUIDE.md).
The guide distinguishes the existing role-chain implementation from planned action-grant capabilities.

## 1. Context

BUGate began as a SUT-neutral method and toolkit that can be imported into
Claude Code, Codex, CI, and other hosts. Its value is the quality-governance
contract: evidence, policy, gates, authorization, receipts, audit, and
promotion. It deliberately does not own an agent loop or a product-specific
test runner.

Some BUGate scripts now also perform execution orchestration. In particular,
`scripts/sdtd_orchestrator.py --auto` sequences peer dispatch, semantic checks,
artifact generation, adversarial review, post-run reports, and the optional
self-healing path. `role_governance.py` and `role_lineage.py` additionally
contain crash-safety and recovery machinery because governance publication
must never fabricate or lose authorization history.

HyperTest is evolving into an autonomous test-development system and needs a
durable workflow runtime for checkpoint/resume, retry, interrupt, concurrency,
and long-running execution. LangGraph is the current preferred implementation.
The architectural question is whether BUGate should itself be rewritten around
LangGraph or should stop owning generic workflow orchestration.

## 2. Decision

BUGate remains a **runtime-agnostic governance kernel**. It will not take a
LangGraph dependency and will not encode its governance policy as LangGraph
nodes or edges. HyperTest owns durable execution and may use LangGraph behind a
replaceable workflow-runtime boundary.

The governing distinction is:

> **Execution state is not authorization state.**

- A workflow checkpoint answers: *where can execution resume?*
- A BUGate receipt answers: *why was this protected action authorized?*
- An enforcement attestation answers: *which authorized action actually ran,
  against which exact state, and what happened?*

LangGraph may route on a BUGate verdict, but it cannot create, reinterpret, or
replace that verdict. A graph state such as `current_node=implementation` is
never evidence that implementation is unlocked.

## 3. Receipt and evidence-chain execution model

The receipt chain becomes a governance transaction log rather than a shadow
workflow engine. The target contract has four parts.

### 3.1 Deterministic decision

The Policy Decision Point (PDP) evaluates a canonical request against immutable
evidence and versioned policy. An authorization receipt binds at least:

- receipt and request IDs plus an idempotency key;
- action and narrow resource/path scope;
- source revision and, where applicable, workspace or patch digest;
- the complete evidence-hash set;
- policy, profile, and schema versions;
- actor, role, and session claims available at the active assurance tier;
- verdict, reason codes, obligations, issue time, expiry, and one-use nonce;
- sequence number, previous receipt hash, and anchoring/signature metadata.

Canonical serialization and schema-versioned hashing are normative. A policy
change, source drift, evidence drift, expiry, or scope mismatch invalidates the
authorization and fails closed.

### 3.2 Append-only publication

Governance events are appended through one transactional publication API. The
append-only log is authoritative; lifecycle status is a derived projection
that can be rebuilt by replay. Publication uses compare-and-swap on the
expected lineage head and idempotency key so concurrent or repeated attempts
cannot create two valid successors.

Local hash linking provides tamper evidence, not actor identity or durable
survival by itself. Higher-assurance deployments anchor lineage heads outside
the governed workspace (the existing strict Memory checkpoint is one such
anchor). Managed deployments may add role-scoped signing keys, an append-only
database/WORM store, or a transparency-log service without changing policy
semantics.

### 3.3 Brokered enforcement

Every protected side effect goes through a Policy Enforcement Point (PEP).
The recommended HyperTest deployment does not expose raw workspace mutation or
publication credentials to the agent; the PEP is the only component able to
apply a governed patch or publish a change.

Immediately before the side effect, the PEP:

1. reconstructs the canonical gate request from current state;
2. verifies the receipt, evidence hashes, source/workspace preconditions,
   policy version, scope, obligations, expiry, lineage head, and nonce;
3. atomically reserves or consumes the one-use authorization using
   compare-and-swap;
4. executes the exact authorized effect; and
5. appends an enforcement outcome that binds the decision receipt to the
   resulting revision, artifact hashes, status, and failure details.

A decision receipt proves permission, not execution. The separate outcome
record prevents an `allow` decision from being mistaken for a completed
mutation. Crash recovery resumes the same governance transaction; it does not
mint a replacement authorization or silently repeat a non-idempotent effect.

### 3.4 Assurance tiers

| Tier | Enforcement | Guarantee |
|---|---|---|
| Local toolkit | hooks plus local receipt/registry verification | developer-friendly, auditable and fail-closed on observed paths, but bypassable by an actor with the same OS-user authority |
| Brokered autonomous runtime | agent has no direct protected write/publish capability; all effects cross the PEP | prevention of ordinary agent bypass, scoped one-use authorization, atomic consumption and outcome attestation |
| Managed high assurance | brokered PEP plus isolated runner, role-scoped credentials/signatures, and externally anchored append-only history | stronger identity, non-bypass, retention, and independent audit properties |

BUGate documents the active tier honestly. Hooks and hash chains must never be
described as non-repudiation.

## 4. Ownership boundary

| Concern | Owner |
|---|---|
| Methodology, schemas, evidence rules, policy evaluation, role/session rules, authorization decisions | BUGate policy kernel |
| Receipt/lineage validation, governance CAS, audit projection, recovery of an in-flight governance publication | BUGate audit kernel |
| Protected-action interception and receipt consumption | Host-specific BUGate PEP adapter |
| Planning, tool use, subagent choice, and local reasoning | Agent harness |
| Checkpoint/resume, scheduling, retry, interrupt, parallelism, and generic process recovery | Host workflow runtime |
| SUT execution, CI, SCM, sandbox, and test-framework behavior | Host adapters |

Governance crash consistency remains in BUGate because it protects the
authorization ledger. Generic task checkpointing and worker/process lifecycle
do not.

## 5. Disposition of `sdtd_orchestrator.py`

`sdtd_orchestrator.py` will **not** be rewritten with LangGraph inside BUGate.
It is split conceptually into three buckets:

1. **Keep as BUGate primitives:** artifact initialization/status, semantic gate
   commands, generators, policy evaluation, receipt verification, and
   machine-readable results. These remain usable from CI without an agent or
   workflow framework.
2. **Keep temporarily as a compatibility runtime:** the current `--auto`
   sequencing for standalone adopters. It is frozen to deterministic,
   single-process compatibility behavior; no new durable-runtime features are
   added to it.
3. **Move to the host runtime:** peer scheduling, long-running phase
   orchestration, retries, pause/resume, parallelism, post-run sequencing, and
   repair-loop control. HyperTest owns these through its workflow runtime and
   calls the BUGate primitives/PDP through stable process or service contracts.

The optional self-healing **policy** stays in BUGate; the repeated remediation
**loop** belongs to HyperTest. Likewise, multiview/adversarial acceptance rules
stay in BUGate while worker dispatch belongs to the host.

## 6. Migration plan and retirement criteria

### Phase A — contract extraction

- Preserve current behavior and tests.
- Give every orchestrated operation a versioned, machine-readable request and
  result contract with stable exit semantics and idempotency keys.
- Separate pure policy/validation functions from process dispatch and CLI
  presentation.
- Add decision, consumption, and outcome receipt schemas without weakening the
  existing lineage checks.

### Phase B — HyperTest adoption

- HyperTest invokes discrete BUGate operations rather than `--auto`.
- LangGraph checkpoints contain references and hashes, not duplicate mutable
  copies of governance receipts.
- Every protected edge calls the BUGate PDP and every side effect crosses the
  brokered PEP.
- Conformance tests prove that restart, retry, duplicate delivery, denial,
  expiry, drift, and partial failure cannot bypass or duplicate authorization.

### Phase C — compatibility reduction

- Mark `--auto` as compatibility-only and stop adding features to it.
- Keep `--init`, `status`, validators, generators, and gate commands as
  first-class BUGate CLI operations.
- Remove an orchestration path only after HyperTest (or another reference host)
  passes capability parity, failure-injection, recovery, and migration tests;
  the supported standalone release window has expired; and the removal has a
  documented replacement and rollback path.

Removal is therefore an evidence-based end state, not an immediate deletion.
If standalone users remain important, the compatibility runtime may remain a
small deterministic wrapper indefinitely, but it must not become a second
durable workflow platform.

## 7. Consequences

- BUGate remains importable, stdlib-friendly, CI-usable, SUT-neutral, and
  replaceable across Claude Code, Codex, HyperTest, and future hosts.
- HyperTest can adopt LangGraph without making BUGate depend on LangGraph.
- Receipt correctness is stronger because authorization, consumption, and
  execution outcome are explicit rather than inferred from call order.
- Some current modules need extraction: policy and audit semantics stay;
  generic process orchestration shrinks.
- Strong prevention requires credential/capability isolation. Local hooks alone
  remain a useful lower-assurance mode, not the final autonomous-runtime PEP.

## 8. Rejected alternatives

- **Rewrite BUGate around LangGraph.** Rejected because it couples governance
  semantics to one runtime, weakens zero-install/CI use, and merges execution
  with authorization state.
- **Treat LangGraph checkpoints as governance receipts.** Rejected because a
  checkpoint records execution progress, not policy authority or legal
  provenance.
- **Delete `sdtd_orchestrator.py` immediately.** Rejected because it breaks the
  standalone path before a replacement proves parity and recovery safety.
- **Keep expanding BUGate's home-grown orchestrator.** Rejected because generic
  durable execution is a host responsibility and would create two competing
  runtimes.
