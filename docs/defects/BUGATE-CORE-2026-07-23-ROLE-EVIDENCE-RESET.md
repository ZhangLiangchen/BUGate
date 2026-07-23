# BUGATE-CORE-2026-07-23: Deleted role evidence could reset lifecycle history

- Component: Wave 7 role-evidence continuity, publication, and recovery
- Classification: BUGate Core governance defect; not a SUT defect
- Severity: P1 release blocker (pre-fix)
- Status: fixed and source-verified for v0.4.3; release-operation acceptance remains pending
- Scope: SUT-neutral synthetic repositories and machine-level BUGate state only

## Confirmed pre-fix behavior

The v0.4.0-v0.4.2 role chain was append-only while present, but its local
directory was also the only durable local indication that a UC had ever entered
the lifecycle. When `00_role_evidence/` or `chain.json` disappeared, the loader
could synthesize an empty chain. A later lifecycle publisher could therefore
start again at sequence 1 instead of proving that the previous history had
never existed.

Strict Memory transition records did not close that gap. They were exact-ID
anchors for individual transitions, but there was no deterministic per-UC root
that an operator could probe after losing both the workspace evidence and the
local index. There was also no machine-level compare-and-swap head or durable
publication journal. As a result:

1. deleting the chain, one receipt, all receipts, or the whole evidence
   directory could be confused with first use;
2. a crash between Memory publication, receipt publication, and `chain.json`
   replacement had no single durable recovery decision;
3. two workspace copies representing the same governed UC could each begin
   from the same head; and
4. the existence check followed by receipt replacement did not provide a
   cross-process no-replace guarantee.

This is a Core governance defect. It is not incorrect SUT behavior and must not
be worked around by weakening a SUT profile.

## Impact

The defect could erase the audit meaning of an earlier human acceptance,
handoff, acceptance, or completion generation. A fresh-looking local directory
was not proof of a fresh UC, so a new green chain could conceal lost history.
The failure was especially serious for `role_governance.mode: required`, whose
contract says that deletion is never a reset.

The defect did not make local hooks an operating-system security boundary.
Hooks have never been able to intercept arbitrary shell redirection, recursive
deletion, or external-editor writes. The missing control was reliable detection
and fail-closed recovery after such a deletion, not a claim that deletion was
physically impossible.

## Root cause

The design conflated two independent states:

- **lifecycle state**: `awaiting_human_acceptance`,
  `implementation_unlocked`, `closed`, and the other phase states; and
- **history integrity**: whether the local evidence is the exact history that
  the machine had previously accepted for this UC.

Without a second authority for history existence and head position, “no local
files” meant both “genuine first use” and “previous evidence was deleted.” No
validator can safely distinguish those cases from the empty directory alone.

## Required closure

Closure requires all of the following as one contract:

- Derive a deterministic `lineage_id` from canonical JSON containing exactly
  the effective Memory namespace, resolved UC token, and canonical
  workspace-relative artifact directory, plus its schema identifier.
- Keep a machine-level, profile-independent SQLite registry under the effective
  Memory home. The registry records the accepted lifecycle state, sequence,
  head, revision, Memory mode, root ID, checkpoint ID, the sole active
  publication/recovery transaction, and the durable initialization intent.
  Registry schema v2 must enforce exact next-stage transitions and
  content-addressed root/checkpoint binding.
- Report history integrity separately as `uninitialized`, `aligned`,
  `migration_required`, `history_missing`, `history_diverged`,
  `recovery_pending`, or `registry_unavailable`. Only `aligned` may publish a
  normal lifecycle event.
- Make first use and legacy adoption explicit operator decisions:
  `lineage-init` accepts only a confirmed empty lineage, while `lineage-adopt`
  accepts only a fully verified non-empty legacy chain at an exact expected
  head and rewrites zero existing receipt bytes.
- Journal first use before its first Memory request and resume the same exact
  intent through `pending`, `root_absence_verified`, `root_verified`,
  `registry_initialized`, `chain_written`, and `completed`. Any interruption
  after intent creation must report `recovery_pending` and resume through exact
  `lineage-init`; only an existing root found at the initial probe may abort the
  pre-root/pre-lineage intent and return `migration_required`.
- In strict Memory mode, establish an immutable deterministic lineage root and
  content-addressed checkpoints containing the exact receipt and chain
  envelopes. In best-effort mode, state clearly that no remote recovery copy is
  created: lost or unverifiable committed local history requires a separately
  retained trusted archive, while an active pre-CAS publication may resume from
  its exact locally verified predecessor without pretending to reconstruct loss.
- Journal the transition precondition before Memory work; exact-verify the
  deterministic root and committed predecessor checkpoint; then prepare
  Memory, construct the receipt, and strict-finalize/exact-verify its binding
  before journaling the exact receipt bytes once. Only then publish/verify the
  checkpoint, advance the registry head with an exact compare-and-swap,
  publish the receipt with no-replace semantics, replace the chain atomically,
  and mark the transaction complete.
- Detect every interrupted publication after durable journal creation as
  `recovery_pending`; a pre-journal validation failure creates no new pending
  transaction. After complete validation and write preflight, `recover` must
  claim the active source or create and claim a pending `recovery_restore`
  before target writes, restore the exact committed predecessor, and resume
  the durable transaction. When the original publication is
  checkpoint-verified and ready for CAS, it must resume that same transaction
  through CAS and local publication. In the same SQLite transaction, the
  registry must terminalize the claimed source (`recovery_restore` as
  `aborted`, lifecycle as `completed`) and install one canonical-bound,
  pending, state-preserving `evidence_recovery` successor, so there is no
  aligned/no-audit crash gap. Retrying an already-active `evidence_recovery`
  must resume it directly and must not create another successor, a duplicate
  lifecycle/recovery receipt, or hide the incident.
- Keep hooks, ordinary `status`, and per-edit preflight local-only. The explicit
  operator command `lineage-status` may exact-GET the deterministic strict
  Memory root only when local state appears `uninitialized`; an existing root
  changes that result to non-zero `migration_required`. `lineage-init` must
  perform the same probe and refuse to create a second empty history.
- Preserve the updater boundary: engine installation or verification must not
  initialize, adopt, or recover a lineage and must never edit the profile,
  namespace, role evidence, machine registry, or Memory data.

## Implemented remediation behavior

v0.4.3 implements the deterministic lineage key as:

```json
{
  "schema": "bugate.role-lineage-key/v1",
  "namespace": "<effective-memory-namespace>",
  "uc": "<resolved-uc-token>",
  "artifact_dir": "<canonical-workspace-relative-posix-path>"
}
```

`lineage_id` is the lowercase SHA-256 of the UTF-8 canonical JSON bytes (sorted
keys, compact separators, no trailing newline). Absolute workspace paths, OS
identity, credentials, and Memory tokens are not identity inputs.

The machine registry is `role-lineage.sqlite3` under the effective Memory home
(`MCP_MEMORY_BASE_DIR`, then `BUGATE_MEMORY_HOME`, then
`~/.bugate/memory-bus`). Read paths do not create it. Explicit initialization
or adoption creates it with a validated schema and fail-closed SQLite
transactions.

For required Memory, each committed positive sequence has an immutable
checkpoint that binds the exact receipt bytes, exact minimal chain bytes,
file modes, previous checkpoint, receipt hashes, resulting lifecycle state,
and next registry revision. Recovery walks that exact checkpoint lineage. For
best-effort Memory, the registry still detects deletion, but it does not pretend
that an external recovery copy exists; the operator must supply a trusted
`bugate.role-recovery-archive/v1` file.

## Migration and recovery boundary

- A genuine new UC uses `lineage-status --json`, confirms the displayed
  deterministic ID, and then runs `lineage-init --lineage-id <exact-id>`.
- An interrupted first-use intent reports `recovery_pending` plus
  `active_initialization` and resumes by rerunning that same exact
  `lineage-init`; it does not use `recover`.
- A valid pre-v0.4.3 non-empty chain reports `migration_required` and uses
  `lineage-adopt --lineage-id <exact-id> --expected-head <exact-head>`.
- A registered lineage with missing, divergent, or interrupted local history
  uses `recover --lineage-id <exact-id> --expected-head <head-or-EMPTY>`.
  In required mode an explicit archive selects candidate bytes only; retained
  strict checkpoints remain mandatory and authoritative, and every archive
  envelope must exactly match them before any write. It is not an offline
  fallback for unavailable or divergent strict Memory.
  Best-effort additionally requires `--archive <trusted-archive>` whenever the
  exact committed local predecessor is not intact; an active pre-CAS
  publication with that predecessor still locally verified resumes its journal
  without an archive.
- An engine update that installs these commands has not accepted any of those
  operator decisions. Update success is not lineage migration success.

If evidence was lost **before** any deterministic registry/root/checkpoint was
established, an empty directory cannot reveal the lost history. BUGate must not
invent or infer it. Restore a trusted pre-loss copy or leave the migration
blocked and document the gap.

## Security and threat boundary

The registry plus strict Memory make supported deletion and drift detectable;
they do not authenticate the operator or provide non-repudiation. Role
environment variables, `approved_by`, local file ownership, registry rows, and
Memory records remain audit controls.

An actor with the same OS-user authority who can delete the workspace evidence,
the machine registry, **and** the entire Memory home can remove all local
anchors. That combined destruction is outside BUGate's local threat boundary.
Use separate OS accounts, containers, managed runners, protected backups, or
role-scoped remote credentials when the threat model includes that actor.

## Acceptance and publication gates

The remediation was accepted against these SUT-neutral source-level closure
requirements. Release publication additionally follows the operational gates
recorded below:

1. non-zero, zero-write failure for deletion of the chain, one/all receipts,
   the evidence directory, and a recreated empty directory;
2. exact lineage identity, registry schema/modes, one-winner CAS concurrency,
   immutable journal fields, and every integrity-state classification;
3. strict-root detection after workspace and registry loss;
4. exact-stage resume from every first-use initialization crash window, with
   one durable intent, no duplicate root, and no publisher bypass;
5. recovery from every pre/post-CAS publication crash window without changing
   earlier receipt bytes or lifecycle state, and without a duplicate original
   lifecycle receipt before the visible recovery record;
6. distinct required versus best-effort recovery behavior and invalid-input
   zero-write controls;
7. hooks that detect registered deletion without making a Memory HTTP request;
8. updater integration proving profile, role evidence, namespace, registry,
   and Memory home remain unchanged; and
9. synchronized bilingual protocol, schema, runbook, and final release
   notes with the threat boundary above.

## Source verification evidence

The 2026-07-23 SUT-neutral verification run used only this Core checkout and
fixtures under `/tmp` or `TemporaryDirectory`. It did not mount, inspect, or
modify a real imported SUT.

- `python3 -m unittest discover -s tests`: exit 0, 456/456 tests passed in
  158.398 seconds; SKIP 0, XFAIL 0, warnings 0.
- Direct `tests/test_*.py` loop: exit 0 across 27 files, with 456 summed
  unittest cases plus the direct meta-test programs passing in 171.37 seconds;
  SKIP 0, XFAIL 0, warnings 0.
- Focused role-governance, lineage-registry, strict-Memory, hook, updater, and
  release-contract suites all passed; deletion, one-winner concurrency, every
  journaled crash stage, exact recovery, legacy adoption, and updater
  non-mutation were exercised.
- The de-SUT term guard and the pre-code template semantic check both exited 0.
- A clean `/tmp` Core snapshot completed `run_full_check.py --mode smoke` with
  exit 0: the required role chain reached `closed`, all seven transition
  anchors passed strict exact-ID verification, and the four reported warnings
  were the expected smoke/profile-activation boundaries.
- Three standalone probes independent of test-module names exited 0: complete
  evidence deletion remained `history_missing` at sequence 2; a strict Memory
  outage advanced no local, registry, or Memory head; and interrupted recovery
  restored the exact original receipt bytes before adding one state-preserving
  `evidence_recovery` receipt without unlocking implementation.
- `compileall` and `git diff --check` both exited 0.

One exploratory invocation from the developer checkout encountered a
machine-local optional `.venv/bin/memory status` timeout and exited 1 before
rendering the full-check table. It used an isolated `/tmp` Memory home and made
no real SUT or Memory mutation. The release-like clean snapshot invocation
above passed; the formal archive-native release gate is still intentionally
outstanding.

The implementation satisfies the defect-specific source closure criteria and
is ready for v0.4.3 release operations. Clean-archive acceptance, independent
release review, merged-main CI, annotated-tag CI, and public-download
reacceptance remain mandatory operational gates and are not claimed complete by
this record. Any failure at those gates blocks publication and requires the
defect or release acceptance to be reopened as appropriate.
