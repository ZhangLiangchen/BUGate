[English](ROLE_GOVERNANCE_PROTOCOL.md) | [简体中文](ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)

# Wave 7 Auditable Role-Governance Protocol

Status: frozen for BUGate v0.4.0, with normative amendments through v0.4.3.
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
