[English](ROLE_GOVERNANCE_PROTOCOL.md) | [简体中文](ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)

# Wave 7 Auditable Role-Governance Protocol

Status: frozen for BUGate v0.4.0, with normative amendments through v0.4.5.
This protocol is SUT-neutral and is the
normative contract for the implementation, hooks, tests, importer, and release
acceptance.

## 1. Scope and role vocabulary

Wave 1 and Wave 7 solve different independence problems:

- Wave 1 dispatches independent Codex/Claude peers inside one design phase to
  expose interpretation divergence. A peer is a read-only analysis worker, not
  a lifecycle actor.
- Wave 7 separates lifecycle responsibility across `designer`, `implementer`,
  and `reviewer` sessions and records every transition.

`role_governance.phases` accepts only those three lifecycle tokens. Runtime
names such as `codex` and `claude` belong in receipt runtime metadata, never in
role fields. The existing `agent_roles` mapping remains a separate path-access
policy and retains legacy/custom role tokens and its bare-list/read/write forms.
For the frozen v0.4.x state machine, phase ownership is canonical rather than
programmable: pre-code is `designer`, implementation is `implementer`, and
post-run is `reviewer`. In `required` mode a profile that swaps or combines
those owners is malformed and fails closed; `advisory` reports it without
claiming an unlock.

## 2. Configuration contract

Core remains inert:

```yaml
role_governance:
  mode: off
```

An imported SUT profile may explicitly enable the complete contract:

```yaml
role_governance:
  mode: required
  memory_mode: required
  evidence_dir: 00_role_evidence
  session_id_required: true
  require_distinct_sessions: true
  human_acceptance_artifacts:
    - 03b_adversarial_cases.yaml
  phases:
    pre_code:
      allowed_roles: [designer]
    implementation:
      allowed_roles: [implementer]
      requires_handoff_from: [designer]
    post_run:
      allowed_roles: [reviewer]
      requires_handoff_from: [implementer]
```

Mode semantics:

- `off`: v0.3.x behavior; no role-state enforcement.
- `advisory`: evaluate and report violations without unlocking claims or
  blocking normal writes. Evidence-chain files remain protected from direct
  tool edits because an advisory chain must not become forgeable.
- `required`: invalid configuration, missing/wrong role or session, missing or
  invalid receipt, and any drift fail closed.

`memory_mode` is `best_effort` or `required`. Required role transitions use
strict Memory operations. Best-effort transitions may attempt the same
transition anchoring but tolerate unavailability or finalization failure and
create no lineage root/checkpoint. Ordinary recall, notes, and Stop heartbeats
remain best-effort.

Configuration files are parsed as nested mappings. Merge rules are
deterministic: mappings merge recursively, profile scalars replace base
scalars, and profile lists replace base lists. `parse_simple_yaml()` remains the
legacy frontmatter/simple-artifact parser. Each document canonicalizes legacy
top-level `namespace` into `memory.namespace` before merge, so a legacy profile
can override a nested base value; the merged result exposes both access forms.
If one document declares conflicting old and new forms, the new nested form
wins and is mirrored to the legacy alias.

Required mode rejects malformed YAML subset input, invalid types/enums/booleans,
absolute or escaping evidence directories, unknown/missing phases, invalid
lifecycle tokens, empty role sets, bad handoff relations, missing explicit
profiles, and every invalid governed regex with a clear error.

## 3. State machine

The append-only events and resulting states are:

| Sequence | Event | Required actor/session | Preconditions | Resulting state |
|---|---|---|---|---|
| 1 | `human_acceptance` | designer session records an already-made human decision | required pre-code gates passed; configured 03B is already `passed` | `ready_for_designer_handoff` |
| 2 | `designer_handoff` | designer | valid human acceptance and current pre-code/provenance snapshot; strict Memory anchor | `awaiting_implementer_acceptance` |
| 3 | `implementer_acceptance` | implementer in a distinct session | exact handoff ID and metadata verified; acceptance Memory anchor verified | `implementation_unlocked` |
| 4 | `implementer_handoff` | implementer | one or more workspace-contained implementation files, each guarded and bound to the same UC | `awaiting_reviewer_acceptance` |
| 5 | `reviewer_acceptance` | reviewer in a distinct session | exact implementer handoff verified; implementation snapshot current | `post_run_active` |
| 6 | `reviewer_completion` | reviewer | 04/05, command summary, exit code, log/evidence hashes, and final gates recorded and valid | `closed` |

The approval command records a declared `approved_by` for a 03B that a human
has already set to `passed`; it never modifies 03B and is not identity
authentication. Same-role acceptance and, when configured, same-session
acceptance are rejected. A successful retry is idempotent. Drift recovery
appends a superseding generation; deleting evidence is never a reset. The
v0.4.3 amendment in §9 makes that last invariant detectable across deletion and
interrupted publication.

## 4. Local evidence and hashing

Each UC's workspace-local evidence uses only
`<artifact-dir>/00_role_evidence/`:

```text
00_role_evidence/
├── chain.json
└── receipts/000001-<event>-<hash>.json
```

Receipts are append-only. `chain.json` contains only schema version, current
state and sequence, chain-head hash, and the latest path for each logical
event. Paths are workspace-relative POSIX paths and snapshots are sorted by
path. JSON hashes use UTF-8, sorted keys, and compact separators; receipt
hashing excludes `receipt_sha256` itself. Each receipt links the previous
receipt and a stable transition hash.

Designer handoff captures the active profile, every required pre-code artifact,
formal `00_multiview` outputs when present, 03B dispatch provenance, and the
current human-acceptance receipt. Implementer handoff adds guarded
implementation hashes. Reviewer completion adds 04/05 plus execution logs and
evidence. A successful completion is terminal: its profile, 04/05, and
execution-evidence snapshot remains locally verified by status, verify, and
post-run preflight. In `required` mode supported tool writes are blocked after
`closed`; in `advisory` they remain warning-only. An intentional governed
change requires a new handoff/acceptance lifecycle generation.

Receipt and chain publication uses same-directory temporary files, flush,
`fsync`, and `os.replace`. No secret or Memory credential is persisted. Receipt
content/hash, chain linkage/head, profile hash, pre-code hash/gate status, and
implementation hashes are locally revalidated on every governed edit. No
per-edit Memory request is permitted.

New receipts bind both configuration sources: `profile.path` and
`profile.sha256` identify the selected profile file, while
`profile.effective_config_sha256` hashes the canonical merged base+profile
mapping that actually enforced the transition. Changing inherited base policy
therefore re-locks the chain even when selected profile bytes are unchanged.
The validator still parses the legacy v0.4.0/v0.4.1 two-field profile snapshot
so its append-only chain can append recovery events, but that snapshot is stale
for a current unlock and requires a superseding human-acceptance/handoff
generation. When governance is `off`, lifecycle publisher commands (`approve`,
`handoff`, `accept`, and `complete`) reject rather than creating inert-looking
receipts.

Reviewer completion accepts only dedicated execution evidence. It rejects the
base config, selected profile, every configured role-evidence directory, and
pre-code, implementation, or post-run phase-owned paths. Hook ownership for an
arbitrary captured log is its canonical resolved workspace path, not its
spelling: `..` and symlink aliases cannot bypass the terminal snapshot. If more
than one UC captures the same path, the write must pass post-run preflight for
every owner; ambiguity never selects the first matching UC.

## 5. Strict Memory transition protocol

At a required transition BUGate performs, in order:

1. Build a stable transition payload and `transition_sha256`.
2. POST the Memory transition and require a valid content hash.
3. Exact GET that hash and verify namespace, roles, UC, phase, transition and
   referenced handoff metadata.
4. Construct the complete local receipt with the Memory ID and compute its
   receipt hash.
5. PUT the receipt hash into Memory metadata.
6. Exact GET again and verify the complete anchor.
7. Only then atomically publish the local receipt and chain head.

Acceptance first exact-GETs and validates the supplied handoff ID, then writes
and exact-GETs its acceptance. Unavailability, timeout, HTTP/write failure,
missing exact ID, or any field mismatch returns non-zero and publishes no local
unlock receipt or chain advancement. Stable transition content and local
latest-event checks make retries idempotent. High-cardinality identifiers and
hashes remain metadata rather than tags.

## 6. Enforcement surfaces

All Core artifact mutators call the shared Python preflight before directories,
templates, peer dispatch, or output writes. The common Core writer is a second
path-classification backstop. Role evidence uses a private atomic writer that
is not an environment-selectable bypass.

In required mode, pre-code init creates only pre-code and selected optional
modeling artifacts. The legacy/off init still creates 01–05 as v0.3.x did.
04/05 are reviewer-owned. Once a 03B has a human-acceptance receipt, `--auto`
must not regenerate it; handoff reruns semantic/provenance validation only.

Hooks keep two independent guards: `check_bugate.py` validates passed pre-code
gates, while `check_role_evidence.py` validates roles and the receipt chain.
Claude keeps `Edit|Write` for write gates and `Read|Edit|Write` for
`agent_roles`; Codex runs all four guards on `apply_patch`. Direct agent-tool
edits to `00_role_evidence/**` are denied. SessionStart performs best-effort
Memory recall and prints role-governance status; Stop remains a best-effort,
hourly heartbeat using the active role or `agent`.

Peer bridge child environments remove lifecycle role/session/receipt identity
without removing profile/project roots, proxy settings, model selection, or
reasoning effort.

## 7. Compatibility, recovery, and security boundary

Historical v0.4.0 behavior—superseded by §8 for v0.4.2 and later—follows.
Profiles without `role_governance` behave as v0.3.x. Enabling `required` does
not grandfather historical passed UCs: they need a current human acceptance,
handoff, and acceptance chain. Profile/artifact drift restarts from designer
acceptance/handoff; implementation drift restarts from implementer handoff and
reviewer acceptance. Re-running the importer refreshes vendored scripts and
BUGate-owned hook entries while preserving SUT-owned hooks; changed Codex hooks
must be re-trusted.

This protocol provides role declarations, session separation, hash linkage,
external Memory anchors, drift/tamper detection, and auditable transitions. It
does not provide non-repudiable human identity. Environment variables, hooks,
and local files cannot prove who operated them. Strong identity isolation needs
separate OS accounts, containers, managed runners, or role-scoped server
credentials. Hooks also cannot intercept arbitrary shell redirection or an
external editor; supported agent tools, orchestrators, and Core mutators are
enforced, while stronger filesystem isolation belongs to a managed runner.

## 8. Amendment — imported updater boundary (2026-07-22)

Section 7's sentence that re-running the importer refreshes an existing
installation is preserved as the frozen v0.4.0 record, but is superseded for
v0.4.2 and later compatible releases. `bugate_init.py` is fresh-install-only.
An exact v0.3.x or pre-lock v0.4.x installation bootstraps from an unpacked
release; an installation with the updater uses vendored `status` → `plan` →
`apply` → `verify`, with rollback by explicit transaction ID. See the
[Imported-mode updater contract](IMPORTED_UPDATER_CONTRACT.md) and the vendored
`bugate-import/references/updating-bugate.md` runbook.

The updater may replace role-governance-capable engine/hook files, but it never
activates governance, edits a profile/Memory/role evidence, or manufactures a
lifecycle receipt. Engine update and profile migration remain separately
reviewed, separately reversible commits. Codex Desktop re-trust is required
only when its hook bytes actually change; any hook change requires a new agent
session before the new enforcement surface may be claimed active.

## 9. Amendment — durable role-evidence lineage (v0.4.3)

This section is the normative v0.4.3 lineage contract. Tag, CI, asset, and
publication status are release-operation evidence rather than claims made by
this source document. The durable defect record is
[`BUGATE-CORE-2026-07-23-ROLE-EVIDENCE-RESET`](../defects/BUGATE-CORE-2026-07-23-ROLE-EVIDENCE-RESET.md).

### 9.1 Deterministic identity and independent authority

The absence of workspace evidence is not proof that a UC is new. Each governed
UC therefore has a deterministic lineage key:

```json
{
  "schema": "bugate.role-lineage-key/v1",
  "namespace": "<effective-memory-namespace>",
  "uc": "<resolved-uc-token>",
  "artifact_dir": "<canonical-workspace-relative-posix-path>"
}
```

`lineage_id = sha256(canonical_json(lineage_key))`, where canonical JSON is
UTF-8 with sorted keys, compact separators, and no trailing newline. Inputs are
exact: there is no case or whitespace folding. The UC token follows the normal
profile/template/artifact-directory resolution contract, and `artifact_dir`
is the canonical workspace-relative POSIX path. Absolute workspace paths, OS
identity, timestamps, credentials, and Memory tokens are not identity inputs.

A machine-level SQLite registry named `role-lineage.sqlite3` lives under the
effective Memory home: `MCP_MEMORY_BASE_DIR`, then `BUGATE_MEMORY_HOME`, then
`~/.bugate/memory-bus`. It is outside the governed workspace and independent
of the imported updater's installed projection. Read/status/hook paths never
create it. Only explicit lineage initialization or adoption may create the
validated registry. It records the accepted lifecycle state, sequence, head,
revision, configured Memory mode, strict root/checkpoint IDs, and the sole
active transaction. The v0.4.3 registry schema is version 2: it also owns
the durable initialization journal, enforces the exact next-stage graph for
initialization/publication/recovery, and binds content-addressed root and
checkpoint identifiers rather than accepting routing labels as proof.

### 9.2 Integrity states

History integrity is reported separately from lifecycle state:

| `integrity_state` | Meaning and required route |
|---|---|
| `uninitialized` | No matching registry row and no local history. This is only a possible first-use state; the operator must confirm it before `lineage-init`. |
| `aligned` | The registry and verified local chain agree on identity, head, sequence, lifecycle state, and Memory mode, with no active transaction. Normal lifecycle publication requires this state. |
| `migration_required` | Local history exists without a registry row, or an explicit strict-root probe proves prior lineage existence while registry/local history is absent. Adoption then independently requires and verifies a non-empty valid legacy chain; otherwise use reviewed restoration and never initialize over it. |
| `history_missing` | A registry row exists but the local chain or one or more receipts are absent. |
| `history_diverged` | Local evidence is malformed or disagrees with the registered head/sequence/state, or the configured Memory mode differs from the adopted lineage. |
| `recovery_pending` | The registry retains one incomplete initialization, publication, or recovery journal. `active_initialization` resumes through exact `lineage-init`; an active publication/recovery transaction uses `recover`. |
| `registry_unavailable` | A present registry is unsafe, locked/unreadable, schema-invalid, the lineage context cannot be resolved, or validation otherwise fails; explicit strict-root probe failure is reported the same way. A simply absent registry maps to `uninitialized` or `migration_required`. |

`implementation_unlocked`, `post_run_active`, `closed`, and the other lifecycle
states retain their previous meaning. An integrity failure never becomes a new
lifecycle state and never authorizes a phase rollback or reset.

Ordinary `status`, hooks, and per-edit preflight are intentionally local-only
and make zero Memory HTTP requests. They use the registry and local chain to
detect registered deletion. Inspect `status --json` or `lineage-status --json`
for the explicit integrity field; the default human status line emphasizes the
lifecycle state. `lineage-status` is an explicit operator command:
only when required Memory plus local state yields `uninitialized` does it
exact-GET the deterministic lineage root. If that root exists while the
registry/local history is absent, it returns non-zero `migration_required`.
If the probe is unavailable or invalid it returns non-zero
`registry_unavailable`. `lineage-init` performs the same probe before mutation
and refuses an existing root.

### 9.3 Explicit initialization and legacy adoption

`scripts/bugate_init.py` remains the fresh **engine installation** command.
`bugate-role lineage-init` is a separate, per-UC first-use decision.

For a genuinely new UC:

```sh
bin/bugate-role lineage-status <artifact-dir> --json
bin/bugate-role lineage-init <artifact-dir> --lineage-id <exact-lineage-id>
```

The first command is read-only; its non-zero `uninitialized` result before the
first init is expected. The operator copies and confirms the exact computed ID.
Initialization requires no local history and an exact ID. It is itself a
journaled, crash-recoverable saga: before any Memory request BUGate persists an
exact initialization intent, then advances it through
`pending` -> `root_absence_verified` -> `root_verified` ->
`registry_initialized` -> `chain_written` -> `completed`. Required Memory
proves root absence, creates/exact-verifies the deterministic root, and binds
its exact ID before the empty sequence-zero registry row is committed.
Best-effort follows the same journal but binds the explicit no-remote-root
boundary and creates no root or checkpoint. The local empty `chain.json` is
published with no-replace semantics, mode `0600`, and exact byte/mode
verification before completion.

An exact `lineage-init` retry resumes the same intent from its durable stage;
it never creates a second intent or repeats a completed stage as a new first
use. Every interruption after intent creation is reported as
`recovery_pending`, and normal lifecycle publishers remain blocked until the
intent completes. The one terminal exception is a strict root found during the
initial `pending` probe: BUGate aborts that still-pre-root/pre-lineage intent and
reports `migration_required`, because the root is evidence of prior history.
`status --json` and `lineage-status --json` expose the
`active_initialization` ID and stage so the operator can distinguish this route
from publication recovery.

A valid non-empty v0.4.0-v0.4.2 chain without a registry row reports
`migration_required` and uses:

```sh
bin/bugate-role lineage-adopt <artifact-dir> \
  --lineage-id <exact-lineage-id> --expected-head <exact-chain-head>
```

Adoption revalidates the complete chain and exact expected head. It rewrites
zero receipt bytes. Required Memory creates/exact-verifies the deterministic
root and an immutable checkpoint for every retained sequence before the
registry adopts the final head; best-effort adoption records the verified local
head without claiming a remote recovery copy.

If pre-v0.4.3 history was already lost before a registry or deterministic root
was established, an empty directory cannot reveal whether that history ever
existed. BUGate must not infer or manufacture it. Restore trusted pre-loss
evidence or keep the migration blocked and disclose the gap.

### 9.4 Transactional publication and recovery

Every normal publisher (`approve`, `handoff`, `accept`, `complete`) requires
`aligned` and executes one durable sequence:

1. acquire a physical per-UC `flock` for the complete transition;
2. create the sole active registry transaction against the exact current
   head, sequence, revision, and prior checkpoint, binding the canonical
   transition before any Memory request;
3. in required mode, exact-GET and validate the deterministic root and the
   current committed predecessor checkpoint against the registry-retained
   canonical payload, head, sequence, lifecycle state, and revision;
4. prepare the transition Memory record according to
   `memory_mode`;
5. construct the receipt from that prepared public binding, finalize the Memory
   transition against its receipt hash, and, in required mode, exact-verify the
   final transition/receipt binding;
6. only after Memory prepare plus finalization/exact verification succeeds,
   freeze and journal the final receipt bytes/path/mode/hash exactly once in the
   registry, then construct the exact minimal-chain bytes;
7. in required mode, POST and exact-GET an immutable checkpoint containing the
   exact receipt and minimal-chain byte envelopes, modes, hashes, previous
   checkpoint, resulting state, and next registry revision;
8. compare-and-swap the registry head;
9. publish the append-only receipt with no-replace semantics, atomically
   replace `chain.json`, and only then mark the transaction complete.

The predecessor proof occurs after the durable pending journal but before the
new transition is prepared. Its failure is therefore recoverable and cannot
write a new transition over an unverified strict head. The new checkpoint then
follows strict transition finalization and the exact registry receipt-byte
bind; it never substitutes for or precedes either one.

The sole-active-transaction constraint serializes contenders before Memory
work, and the registry CAS is the final cross-workspace head authority: two
workspace copies with the same lineage cannot both publish from one head. A
crash or handled failure at any journaled stage remains visible as
`recovery_pending`; it is never reinterpreted as empty history. This is a
journaled, crash-recoverable saga across SQLite, Memory, and workspace files,
not one distributed atomic commit; a remote transition/checkpoint may remain
after a later local abort and is reconciled by the recorded transaction.

Registered `history_missing` or `history_diverged`, and `recovery_pending`
with an active publication/recovery transaction, use:

```sh
bin/bugate-role recover <artifact-dir> \
  --lineage-id <exact-lineage-id> --expected-head <exact-head-or-EMPTY> \
  [--archive <trusted-recovery-archive>]
```

An `active_initialization` is not handled by this command: rerun exact
`lineage-init` so its own journal resumes. For `recover`, the exact ID and
registry head are mandatory; `EMPTY` denotes sequence-zero's empty head.
Recovery validates the complete source, paths, hashes, modes, links, receipt
order, chain state, and all existing targets before creating a new journal or
writing a target. It then selects the active source transaction, or creates a
pending `recovery_restore` source against the unchanged registered head, and
claims that exact source before restoring the committed predecessor and prior
receipt/chain bytes. One live claimant is exclusive; a dead claimant can be
taken over only after process-liveness validation and an exact-token registry
CAS. If the source is an original lifecycle publication whose exact receipt and
verified checkpoint are ready for CAS, recovery resumes that same transaction
through registry CAS and exact local receipt/chain publication; it does not
synthesize a duplicate next-sequence lifecycle receipt.

Once local restoration is exact and any resumed lifecycle source has reached
`chain_replaced`, the registry uses one SQLite transaction to terminalize the
claimed source (`recovery_restore` becomes `aborted`; a lifecycle source becomes
`completed`) and install the sole canonical-bound, pending
`evidence_recovery` successor against the resulting head, sequence, revision,
checkpoint, and lifecycle state. There is no intermediate `aligned` state
without a pending audit record. Recovery then publishes and completes that one
state-preserving receipt. If the active source is already
`evidence_recovery`—including after a crash following the handoff—exact
`recover` resumes it directly and never installs another successor. Previous
receipt bytes are never rewritten. A durable best-effort empty transition ID
is an explicit unanchored marker and is preserved during recovery without
retrying Memory HTTP.

In `memory_mode: required`, recovery walks the exact immutable checkpoint chain
from strict Memory by default. An explicitly supplied trusted archive selects
candidate bytes only; retained strict checkpoints remain mandatory and
authoritative, and every archive envelope must exactly match them before any
write. The archive is not an offline fallback for unavailable or divergent
strict Memory. In `best_effort`, no strict
checkpoint exists. If the committed local predecessor is missing, divergent,
or cannot be exact-verified, recovery therefore requires an independently
retained, trusted `bugate.role-recovery-archive/v1` through `--archive`. The
only archive-free best-effort route is an active pre-CAS publication whose exact
committed predecessor remains locally verified; recovery resumes that durable
transaction rather than reconstructing deleted history. The local registry
makes deletion detectable in both modes; only required mode supplies a remote
reconstruction source.

### 9.5 Updater and threat boundaries

The updater may install lineage-capable engine files, but it never runs
`lineage-init`, `lineage-adopt`, or `recover`; never creates/edits the machine
registry; and never edits a profile, namespace, role evidence, or Memory home.
A successful updater transaction/verify proves only the installed engine
projection. It is not acceptance of any per-UC lineage migration. Existing UCs
must be classified and explicitly adopted or recovered afterward.

Hooks cannot intercept arbitrary shell redirection, recursive deletion, or
external-editor writes. The registry plus strict Memory make deletion/drift
detectable; they do not authenticate the actor or provide non-repudiation.
`approved_by`, role/session environment, local file permissions, registry rows,
and Memory records remain audit controls. Strong identity and filesystem
isolation still require separate OS accounts, containers, managed runners,
protected backups, or role-scoped server credentials.

An actor with the same OS-user authority who deletes the workspace evidence,
the machine registry, and the entire Memory home can remove all local anchors.
That combined destruction is outside BUGate's local threat boundary and must
not be described as detected or prevented by this amendment.

## 10. Amendment — self-healing sidecar contract (v0.4.5)

This amendment adds a governed write channel. It changes no clause numbered 1
through 9: `PHASES` remains a three-tuple, the six `EVENT_STATES` keep their
keys and values, `_phase_for_handoff` keeps its `(1, 2)` edges, and post-run
phase ownership remains canonically `reviewer`. Charter anchor: CHARTER.md §7
A6. Decision record: `BUGATE_SELF_HEALING_SIDECAR_ADR.md` (ADR-BUGATE-005).

### 10.1 Why the evidence is a sidecar

Two clause-1-through-9 invariants make the main chain unusable for a repair
flow, and neither is relaxed here.

`DEFAULT_PHASES["post_run"]["allowed_roles"]` is exactly `["reviewer"]` and
`_actor` rejects any role outside it, so a healer acting as `implementer`
during post-run can never be a legal main-chain actor.
`_validate_event_receipt_contract` **raises** on any event outside
`EVENT_STATES` rather than degrading, and `preflight` re-verifies the whole
chain on every governed write, so one self-heal event written into a UC's chain
would make every v0.4.0–v0.4.4 engine fail that chain and block all later
governed writes — colliding with the §8 rollback-to-prior-image guarantee.

Self-healing evidence therefore lives in `<artifact_dir>/00_self_healing/` and
never writes a byte under `00_role_evidence/`. An engine that predates this
amendment sees an unknown directory and behaves identically; `preflight` and
`verify_evidence` return field-identical results whether or not the sidecar
exists.

### 10.2 Layout and schemas

```
<artifact_dir>/00_self_healing/
├── chain.json                   # bugate.self-heal-chain/v1
├── 000-triage_recorded.json     # bugate.self-heal-evidence/v1
├── 001-self_heal_handoff.json
├── ...
└── attempts/<attempt_id>/       # baseline.json, candidate.patch,
                                 # before.log, after.log, verification.json,
                                 # structural_review.json, falsification.json,
                                 # review_context.json, independent_review.json,
                                 # apply_journal.json
```

Receipts hash with the same canonical form as the main chain
(`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`),
serialize with `indent=2` plus a trailing newline, exclude `receipt_sha256` from
their own hash, and publish through the same no-replace atomic writer.

### 10.3 Anchoring and lifecycle drift

Every receipt carries a read-only `role_chain_anchor`:

```json
"role_chain_anchor": {"chain_sha256": "...", "sequence": 3, "lifecycle_state": "post_run_active"}
```

After complete main-chain verification, `chain_sha256` carries the chain's
authenticated `head_sha256`; `sequence` and `lifecycle_state` bind the other two
anchor values. Semantic chain damage is rejected by the full verification,
while a JSON-only reserialization does not create drift. `lifecycle_state` must
be exactly `post_run_active`; nothing may open after `closed`. If the anchor no
longer matches before a publication, the attempt becomes
`attempt_invalidated_by_lifecycle_drift` (exit code 4) and must be re-triaged.

### 10.4 Events, states, and actors

| # | event | actor | prior state | resulting state |
|---|---|---|---|---|
| 0 | `triage_recorded` | `reviewer` | *(none / terminal)* | `triage_recorded` |
| 1 | `self_heal_handoff` | `reviewer` (session of #0) | `triage_recorded` + `healing_eligible` | `awaiting_healer_acceptance` |
| 2 | `healer_acceptance` | `implementer` (**fresh session**) | `awaiting_healer_acceptance` | `healing_active` |
| 3 | `healer_handoff` | `implementer` (session of #2) | `healing_active` | `awaiting_independent_review` |
| 4 | `independent_review` | `reviewer` (**fresh session**, ≠ #0 and ≠ #2) | `awaiting_independent_review` | `healing_verified` \| `healing_rejected` |
| 5 | `attempt_closed` | `reviewer` | `healing_verified` \| `healing_rejected` | `attempt_closed` |

These state tokens exist only in the sidecar. They are not written to the main
chain or the lineage registry, and `_STATE_LABELS` is deliberately not extended
— the sidecar reports its own status line, `BUGate self-heal status: <STATUS>`,
separate from `BUGate lifecycle status: <STATE>`.

`_self_heal_actor` validates against the table above instead of
`ctx.policy["phases"]`, because post-run is frozen to `reviewer` and the healer
leg needs an `implementer`. It keeps `_actor`'s environment-variable contract,
session requirement, and runtime normalization. A same-session self-approval
attempt is rejected with `same_session_self_approval`.

### 10.5 The one omitted check, and its boundary

`verify_evidence(phase="post_run")` calls `_verify_acceptance_session`, binding
every post-run write to *the* reviewer session that published
`reviewer_acceptance`. That rule and "#4 must be a fresh reviewer session"
cannot both hold. `self_heal_preflight` therefore mirrors `verify_evidence`
check for check — aligned lineage, valid chain, reviewer acceptance present,
chain in `post_run_active`, no closed completion, accepted implementer handoff
with an un-drifted profile/artifact snapshot — and omits exactly
`_verify_acceptance_session`, replacing it with the stricter session-distinctness
rules of §10.4.

This omission applies **only to sidecar receipts**. Post-run artifact writes
(`self_healing.json`, 04/05) still go through `governed_write_preflight` and
still require the accepting reviewer session.

`status` and `resume` additionally skip `self_heal_preflight` entirely. Applying
a repair rewrites a file the implementer handoff snapshotted, which is genuine
artifact drift; requiring that old snapshot to remain current would make exact
restore unreachable immediately after the write it exists to undo.

`status` normally reports verified sidecar state. If it detects role-chain
anchor drift, it persists invalidation metadata in the sidecar `chain.json`
without appending an event receipt. `resume` may restore exact prior workspace
bytes and update the authenticated apply journal, also without appending an
event receipt. Both operations still enforce sidecar structure and integrity;
neither is an unrestricted write path.

### 10.6 Strict Memory

Under `memory_mode: required`, events #1, #2 and #4 each write one Memory
transition and verify it by exact id, reusing `_memory_prepare`,
`_memory_finalize` and `_memory_verify` in the same order as `_publish`. They do
**not** call `_publish`, create a registry transaction, or publish a checkpoint.
A Memory failure means the sidecar event is not published and the attempt stays
where it was; the main chain is untouched.

### 10.7 Compatibility and the applied-repair consequence

A repository that never sets `self_healing` has exactly the v0.4.4 behavior:
`--scope self-heal` returns `disabled` and creates nothing, and `--scope
post-run` output is byte-identical (pinned by golden fixtures captured from the
v0.4.4 engine).

Enabling the key changes `profile.effective_config_sha256`, so adding it to a UC
that already carries published receipts re-locks that chain. Set it before the
lifecycle runs, or start a new generation.

Applying a repair rewrites a snapshotted implementation file, so the UC is
re-locked by the existing drift rules. That is the intended outcome: a changed
test asset must be re-accepted and re-reviewed through the normal lifecycle
rather than admitted by the repair that changed it.

### 10.8 Threat boundary

The sidecar inherits §7 and §9.5 unchanged and adds no new identity guarantee.
`check_role_evidence.py` recognizes `00_self_healing/**` as its own protected
evidence class and denies a direct agent edit unconditionally, exactly as it
does for `00_role_evidence/**`; the separate structural ownership table records
its post-run standing but does not grant a reviewer write path. An actor with
the same OS-user authority can still edit the tree outside the hooks. The anchor
makes main-chain drift detectable; it does not authenticate the healer or the
reviewer.

### 10.9 Hardening amendment (2026-08-12)

This dated amendment is normative for the v0.4.5 source candidate. It changes
none of §10.4's event or state tokens.

1. **Attempt boundary.** A non-eligible classification and `diagnose` mode
   write the ordinary governed triage reports but publish no sidecar receipt and
   open no attempt. Event #0 begins only for a repair-eligible failure in
   `verify` or `apply_with_approval`.
2. **Proposal ordering.** The structural gate, sandbox before/after
   verification, required falsification, and repaired-test reverse verification
   all complete during `propose`, before event #3 (`healer_handoff`). Missing or
   inconclusive falsification blocks; a below-threshold score rejects. For every
   exact clean falsification case bound to the triaged oracle, the repaired
   command must change from a pristine pass to an `AssertionError` at the mapped
   governed assertion and return to pass after restore. A surviving test rejects
   with `repaired_test_survives_mutation`; a crash, unsafe/missing runner-visible
   input, unrelated oracle, or ambiguous attribution blocks with
   `repaired_test_reverse_verification_unavailable`. Such a rejection changes no
   attempt artifact. In all cases the failure cannot advance to independent
   review. A clean dynamic failure is necessary but does not authorize arbitrary
   Python; the candidate must also satisfy the closed proof-language boundary
   in item 4.
3. **Exact independent-review binding.** A successful proposal writes
   `review_context.json`; event #3 binds both its SHA-256 and the exact binding
   object. Its exact fields are `uc`, `attempt_id`,
   `candidate_manifest_sha256`, `candidate_patch_sha256`,
   `precode_evidence_sha256` (the canonical manifest of the accepted business
   brief and inventory), `original_failure_sha256`, `verification_sha256`,
   `before_log_sha256`, `after_log_sha256`, `falsification_sha256`, and
   `oracle_refs`. `falsification.json` also embeds the workspace-relative path
   and SHA-256 identity of the live spec and every exact evidence input. Event
   #4 and `close` recompute that input manifest and reject any mismatch. The
   review document must use `dispatch_mode: real_peer_dispatch`, declare
   the same `codex`/`claude` runtime as the active reviewer session, reproduce
   the binding exactly, contain a non-empty list of `{claim, evidence}`
   findings, and contain a list of non-empty residual-risk strings (the list may
   be empty). The exact supplied bytes are archived as
   `independent_review.json`; their SHA-256 and the binding are carried by event
   #4.
4. **Reverse-verification boundary.** The ordinary lane mutates only
   profile-declared, workspace-local, non-symlink regular JSON evidence. With no
   exact runner-visible clean counterexample, the proposal fails closed. Finite
   candidate-visible mutants cannot prove arbitrary Python honest, so automatic
   authorization is limited to two one-file closed languages. The strict
   NameError/literal language permits only the complete-AST insertion of one
   local immutable scalar and replacement of the recorded unresolved name. The
   JSON-evidence language permits only exact `json`/`Path` imports, unique
   bindings, one direct built-in scalar load from the exact declared path/key,
   an inherited immutable-scalar observation, direct equality, zero-argument
   unannotated `test_*` functions/direct calls, and unchanged auxiliary primitive
   equality assertions without message expressions. Removing the exact repair
   delta must reconstruct the original AST. Source must be coherent UTF-8 whose
   encoding-detected byte AST equals its decoded AST. Workspace/configured
   import shadows block, and pristine/mutant/restored runs are repeated with
   `python -I -S`. Both languages perturb the observation scalar; a custom
   comparator, helper, descriptor, decorator, dynamic import, rebinding,
   annotation, assertion message, alternate source encoding, dispatch, control
   flow, or additional candidate file is outside the grammar and blocks. This proves falsifiability and the stated
   scalar binding, not full business provenance or all undeclared states.
   Complex honest repairs remain blocked pending a separately governed
   observation contract; nondeterminism, host compromise, and OS-level sandbox
   escape remain explicit residual risks.
5. **Frozen approvals.** `independent_review_required` and
   `human_approval_required` are both frozen `true`; either `false` value is a
   configuration error. `apply_with_approval` additionally requires a
   non-empty human approval record at `close`.
6. **Sandbox and apply integrity.** Before copying, verification rejects every
   symbolic link on the copied surface; candidate and write targets are
   rechecked without following links. The apply journal records exact before
   and after bytes and POSIX permission bits. While the sidecar is still
   `healing_verified`, `resume` restores either journal state `applying` or
   `applied`, restores prior bytes and permission bits, removes journal-created
   files, and removes only empty journal-created parent directories. A new
   regular file is written with mode `0600`. Ownership, xattrs, and ACLs are
   outside this restore guarantee.
7. **Receipt/index crash cut and storage boundary.** Receipt publication
   precedes chain-index replacement. On the next read, exactly one root-level
   unreferenced receipt is considered only if it is the sole possible next
   receipt and a complete prospective replay validates its schema, sequence,
   state edge, parent/hash, actor/session, current role-chain anchor, and Memory
   envelope. Under `memory_mode: required`, an event in the frozen set #1, #2,
   or #4 is indexed only after read-only exact Memory verification binds the
   transition identity and receipt hash. Reconciliation performs no Memory
   mutation and writes neither the main role-evidence tree nor a receipt. For
   every unanchored event and every `best_effort` orphan, local hashes cannot
   prove authenticity: after the complete local replay succeeds, the exact
   bytes are moved to
   `attempts/<attempt_id>/unindexed_<event>_<content-sha256>.json`, the
   root-level orphan is removed, the chain stays at its prior indexed state,
   and the same event may retry through ordinary publication controls. The
   content-addressed file is retained as evidence and is never a chain entry.
   Malformed, stale-anchor, state-invalid, forged-id, or exact-Memory-mismatched
   receipts fail integrity instead of being accepted. Every existing project,
   artifact, sidecar, `attempts`, and attempt-directory component is checked
   with `lstat`, must be a real directory within the project/artifact boundary,
   and may not traverse a symbolic link; governed leaves must be non-symlink
   regular files. The main `00_role_evidence/chain.json` anchor reader applies
   the same parent and leaf checks even on `status`, and every verified sidecar
   load inventories all direct root entries; unknown files, directories,
   special nodes, or symbolic links fail integrity rather than being ignored.
   The same recursive `lstat` rule covers every directory and leaf below an
   attempt directory. Every indexed receipt anchor must name
   `post_run_active`; adjacent anchors must be identical unless a new triage
   explicitly supersedes an indexed drift record. Malformed main-anchor
   sequence/state values are never normalized into a persistable drift record.

### 10.10 Concurrency and reviewed-apply binding amendment (2026-08-12)

This dated amendment preserves §10.9 and adds the linearization and restore
boundary required by the final concurrency review.

1. **One cooperative per-UC critical section.** Every main role transition and
   every complete sidecar verified read, publication, invalidation, and status
   snapshot takes an exclusive advisory `flock` on the same existing UC
   artifact-directory inode. No lock file is added to either evidence layout.
   A complete self-heal CLI step holds that same lock from its sidecar-state
   validation through all attempt-evidence or workspace writes and its final
   receipt publication; every nested operation reuses the same re-entrant
   store. Thus a losing concurrent proposal, review, close, or resume cannot
   mutate bytes after another cooperating step has committed a successor state.
   A sidecar publication holds the lock across Memory preparation/verification,
   receipt durability, and chain-index replacement; a verified read holds it
   across orphan reconciliation or quarantine. Therefore another cooperating
   BUGate process cannot mistake the receipt/chain gap of a live publisher for
   a crash orphan. Process death releases the descriptor, after which the next
   reader may apply the authenticated crash-orphan rules of §10.9.
2. **Head-anchor compare-and-swap and one status snapshot.** Drift invalidation
   treats the previously read latest receipt anchor as a compare-and-swap token:
   while holding the artifact-directory lock it reloads the verified sidecar
   head and the current main-chain anchor, and refuses to attach an old drift
   observation to a retried or re-triaged head. CLI `status` obtains state,
   attempt id, review outcome, and the main-chain comparison from one locked
   `status_snapshot`. Newly detected drift is persisted before return only when
   the live main-chain anchor is schema-valid. If the main chain is missing,
   the read-only empty sentinel makes each status return `invalidated` with
   `lifecycle_drift` but is never persisted into sidecar `chain.json`;
   malformed/unreadable anchors likewise do not create a drift record. Status
   never assembles one result from independently timed reads.
3. **The reviewed candidate includes the restore topology.** The reviewed
   candidate manifest cryptographically binds the exact `baseline.json` bytes,
   the baseline-authenticated original before images, the candidate after
   images, the reconstructed human-readable patch, and the canonical set of
   parent directories that were absent before apply. `close` recomputes that
   manifest and also revalidates the complete live review context, archived
   review bytes, review receipt binding, runtime/verdict, falsification result,
   and triage evidence before either verify-only closure or workspace apply.
4. **Apply journal v2 is internal and exact.** The internal
   `bugate.self-heal-apply-journal/v2` binds the reviewed candidate manifest,
   exact file set, before/after bytes, POSIX permission bits, and a
   `created_directories` set that must exactly equal the reviewed pre-apply
   missing-directory set. Those directories are created one at a time with a
   no-pre-existing-entry check. On `resume`, any unexpected file, directory, or
   symlink blocks all restore writes; only engine-created new files are removed,
   and only authenticated originally-missing directories that are empty after
   that removal are removed. A pre-existing parent is never in the removable
   set.
5. **Metadata boundary and advisory-lock limit.** The restore promise covers
   exact file bytes and ordinary POSIX permission bits (`0o777`). Special mode
   bits fail closed before apply; ownership, xattrs, and ACLs are not captured
   or restored. `flock` is a cooperative concurrency control, not an OS
   security boundary: a same-OS-account actor can ignore it and mutate or
   replace paths outside BUGate. The §10.8 identity and same-account caveats
   remain unchanged.

### 10.11 Replay and CLI-integrity amendment (2026-08-12)

This dated amendment preserves §§10.9–10.10 and adds the payload, main-anchor,
and error-boundary rules required by the final integrity review.

1. **Every receipt payload is a replayable canonical JSON object.** Publication
   and full-chain replay both require `payload` to be an object whose entire
   graph can be encoded by BUGate canonical JSON and parsed back under the
   active interpreter. Keys are strings; values use only JSON types; floats are
   finite; cyclic and implementation-specific values are invalid. A non-object
   or non-replayable payload fails with
   `self_heal_event_payload_invalid`; the engine never coerces it.
2. **State-bearing payload claims are bound to the authenticated edge.** A
   `triage_recorded` payload must set `healing_eligible` to exactly boolean
   `true`, or fail with `triage_healing_eligible_not_true`. An
   `independent_review` payload's `outcome` must equal the receipt's
   `resulting_state`, or fail with `review_outcome_state_mismatch`. An
   `attempt_closed` payload's `final_state` must be `healing_verified` or
   `healing_rejected` and equal its authenticated prior state, or fail with
   `close_final_state_mismatch`. These rules apply prospectively and on replay.
3. **Rejected triage is zero-write.** In `verify` and
   `apply_with_approval`, maximum-attempt and full publication preflight occur
   before ordinary triage reports, attempt evidence, or a receipt are written.
   A rejected state, payload, actor, session, attempt, or anchor therefore
   cannot rewrite the evidence of the attempt that already owns the state.
4. **The main anchor is not trusted after a shape-only read.** The anchor reader
   applies the role-evidence `lstat` boundary, requires any present main
   `chain.json` to be a non-symlink regular file, validates the exact minimal
   five-key v1 envelope
   and its schema/sequence/state/head/latest-receipt index, and then runs the
   complete `verify_chain` receipt-inventory, hash, state-edge, and history
   verification. Only those fully verified values may supply the authenticated
   `head_sha256` as `chain_sha256`, plus `sequence` and `lifecycle_state`, for a
   live/persisted `role_chain_anchor`.
5. **All failures stay inside the frozen result boundary.** Every result object
   has exactly `status`, `exit_code`, `blocking_reasons`, `artifact_paths`, and
   `next_action`; no sidecar or anchor exception may escape as traceback/exit 1.
   A safely read but malformed main envelope or failed main-chain replay maps to
   `invalidated` with `lifecycle_drift` and exit 4. Sidecar, unsafe-path, and
   payload contract violations retain their precise blocking reason and map to
   `blocked`, exit 2; ordinary evidence I/O/decoding failures also map to a
   five-field blocked result. `status` verifies the sidecar first and only then
   reads the main anchor, so concurrent damage to both surfaces reports the
   sidecar corruption instead of masking it as lifecycle drift.
