---
type: ADR
id: ADR-BUGATE-005
title: Failure triage and test-asset self-healing — an anchored sidecar, not a fourth phase
status: accepted
created_at: 2026-08-11
amended_at: 2026-08-18
authority: ADR-BUGATE-001
companions:
  - CHARTER.md
  - docs/qa-methodology/ROLE_GOVERNANCE_PROTOCOL.md
  - docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.md
---

# ADR-BUGATE-005: Failure Triage and Test-Asset Self-Healing

## Context

BUGate's post-run layer could classify a failure (`self_healing_mvp.py`) but not
*attribute* it, and it had no governed route from "this failure is a broken test
asset" to a reviewed repair. Two problems made the gap concrete.

**The classifier could not reach a verdict.** `PATTERNS` matched bare substrings
with no word boundaries, so `environment_or_resource` fired on any log
containing the letters "connection" — including a debug line about a *pooled*
connection — and `sut_behavior_failure` fired on any log containing "status
code". Both then co-fired, `detected_exclusions` was never empty, and
`sut_defect_admissible` was structurally pinned to `False`. The 2026-07-16 field
note ("the moment a polling word shows up in the log it mechanically refuses a
SUT-defect verdict") is that defect observed in practice.

**There was no place to put a repair.** The obvious design — new lifecycle
events on `00_role_evidence/chain.json` — is closed by two frozen invariants,
and neither may be relaxed:

1. `governance_policy` raises `RoleConfigError` whenever the configured `phases`
   differ from `DEFAULT_PHASES`, and `DEFAULT_PHASES["post_run"]["allowed_roles"]`
   is exactly `["reviewer"]`; `_actor` rejects any role outside that list. A
   healer acting as *implementer* during post-run can therefore never be a legal
   main-chain actor. Publishing self-heal events there would require unfreezing
   the v0.4.2 canonical phase-ownership hardening.
2. `_validate_event_receipt_contract` **raises** on any event outside
   `EVENT_STATES` — it does not degrade — and `preflight` re-verifies the whole
   chain on every governed write. One self-heal event written into a UC's main
   chain would make every v0.4.0–v0.4.4 engine fail that chain outright and block
   all later governed writes on that UC. That collides head-on with the updater's
   guaranteed rollback-to-prior-image contract (ADR anchor:
   `IMPORTED_UPDATER_CONTRACT.md`): rolling back to a prior engine image would
   land the operator in exactly that state.

## Decision

Self-healing evidence lives in an **append-only sidecar**,
`<artifact_dir>/00_self_healing/`, that is *anchored to* but never *written
into* the role chain.

1. **Sidecar, not a fourth phase.** `PHASES` stays a three-tuple, the six
   `EVENT_STATES` keep their keys and values, and `_phase_for_handoff` keeps its
   hardcoded `(1, 2)` edges. The sidecar carries its own schemas
   (`bugate.self-heal-chain/v1`, `bugate.self-heal-evidence/v1`), its own six
   events, and its own states. An older engine reading such a UC sees a
   directory it does not know about and behaves identically — proved field by
   field in `tests/test_self_healing_governance.py`.

2. **Read-only anchoring.** Every sidecar receipt records a
   `role_chain_anchor` — `chain_sha256`, `sequence`, `lifecycle_state` — taken
   from `00_role_evidence/chain.json` at publication time. After complete main-
   chain verification, `chain_sha256` carries the authenticated `head_sha256`;
   `sequence` and `lifecycle_state` bind the other two frozen anchor values.
   Semantic chain damage is rejected by that full verification, while a JSON-
   only reserialization does not create drift. If the role chain advances while
   an attempt is open, the attempt becomes
   `attempt_invalidated_by_lifecycle_drift` rather than continuing against stale
   evidence. The lifecycle must be exactly `post_run_active`; nothing may open
   after `closed`.

3. **Default off.** `self_healing.mode` defaults to `off`, which is not a soft
   disable: `--scope self-heal` returns `disabled`, creates nothing, and the
   `--scope post-run` output is byte-identical to v0.4.4 — pinned by golden
   fixtures captured from the v0.4.4 engine itself.

4. **A separate actor rule, and a stricter one.** `_self_heal_actor` validates
   against the sidecar's own role table instead of `ctx.policy["phases"]`,
   because post-run is frozen to `reviewer` and the healer leg needs an
   implementer. It replaces the phase check with *session distinctness*: the
   healer may not be the session that declared the failure healable, and the
   independent reviewer may be neither of them.

   The same conflict appears once more, and is resolved the same way.
   `verify_evidence(phase="post_run")` calls `_verify_acceptance_session`, which
   binds every post-run write to *the* reviewer session that published
   `reviewer_acceptance`. That rule and "the independent reviewer must be a
   fresh session" cannot both hold. `self_heal_preflight` therefore mirrors
   `verify_evidence` check for check — aligned lineage, valid chain, reviewer
   acceptance present, chain in `post_run_active`, no closed completion, accepted
   implementer handoff with an un-drifted profile/artifact snapshot — and omits
   exactly that one helper. Post-run *artifact* writes are unaffected: they still
   go through `governed_write_preflight` and still require the accepting reviewer
   session. Only sidecar receipts use the relaxed path.

5. **Two-part anti-fake-green control, with the deterministic half
   authoritative.** A candidate must pass both a structural analysis
   (AST-based: assertion count, removed tests, broad/any-of exception handlers,
   introduced skip/xfail, introduced mocks, inflated timeouts, narrowed
   collection, expected-rewritten-to-the-observed-actual) and an independent
   semantic review in a fresh session. A structural finding rejects the
   candidate outright — an approving reviewer can never overrule "this diff
   deleted an assertion", or the review becomes the rubber stamp it exists to
   prevent. A review whose `dispatch_mode` is degraded is not a review, matching
   how `peer_review_degraded` already treats Wave 1 / Stage 3B.

6. **Falsification is required, never degraded.** `healing_verified` is
   unreachable without a `self_healing.falsification_spec` that resolves and
   actually runs. A missing spec blocks (`falsification_spec_missing`, exit 2);
   it does not fall back to a structural-only check. A passing standalone
   oracle score is not sufficient: after the candidate passes, BUGate applies
   the same clean, exact `(evidence path, mutation, oracle)` counterexample in a
   fresh candidate sandbox and requires the repaired command to fail with an
   `AssertionError` at the changed governed assertion, followed by a restored
   fresh pass. A survivor is rejected as
   `repaired_test_survives_mutation`; a crash, ambiguous assertion, unsafe or
   absent runner-visible input, or unrelated oracle is blocked as
   `repaired_test_reverse_verification_unavailable`. A clean dynamic kill does
   not authorize arbitrary Python: the candidate must also belong to one of the
   closed proof languages described below. This is new wiring:
   `oracle_falsification.py` was never called by the orchestrator before.

7. **Sandbox first, apply last.** `verify` mode proves the repair in a copy of
   the workspace and never writes the real tree. Only `apply_with_approval`
   writes back, only after the full review flow and a human approval record,
   only to paths the profile's `allowed_write_regex` names, and only through a
   journal that records every target's exact prior bytes before the first write.

> **Amendment pointer (2026-08-12):** Items 5–7 are retained as the original
> accepted wording. The effective ordering, exact review binding, permission-bit
> restore boundary, and receipt/index crash-cut rules are specified by the dated
> amendment below.
>
> **Amendment pointer (2026-08-18):** Item 6 is likewise retained as the original
> accepted wording. The condition under which a survivor may be published as
> `repaired_test_survives_mutation` is narrowed by the dated amendment below: the
> assertion binding must be carried by a closed static proof, never by evidence
> the candidate under test produced.

## Consequences

**An applied repair re-locks the UC, by design.** Writing the repaired file
changes a file the implementer handoff snapshotted, so `_verify_snapshot`
reports artifact drift. That is the correct outcome, not a defect to suppress: a
changed test asset must be re-accepted and re-reviewed through the normal
lifecycle rather than waved through by the repair that changed it. The `close`
step says so in its `next_action`.

**Recovery must stay reachable.** Because the previous consequence is real, the
`status` and `resume` steps deliberately skip `self_heal_preflight` — otherwise
the one situation recovery exists for would be the one situation recovery could
not run in. Neither publishes a sidecar event; `status` is read-only and
`resume` can only restore bytes the engine itself journalled.

> **Amendment pointer (2026-08-12):** This paragraph is retained unchanged as
> the original accepted wording. Its statement that `status` is unconditionally
> read-only is not the effective contract; use the dated amendment below.

**Enabling the key re-locks an existing UC.** `self_healing` participates in
`profile.effective_config_sha256`, so adding it to a UC that already carries
published receipts drifts the profile snapshot. Set it before the lifecycle
runs, or start a new generation.

**The legacy classifier is frozen in place.** `PATTERNS`, `PRECEDENCE`, and
`EXCLUSION_CAUSES` are untouched and still run for `mode: off`, imprecision and
all. Fixing them in place would have changed the `off` output and broken the
zero-behavior-change guarantee, so the improved discriminators live in
`failure_triage.py` and only run when the capability is enabled. The cost is two
classifiers in the tree; the benefit is that no existing repository's output
moves by a byte.

**Rejected alternatives.** (a) *Publish sub-events on the main chain* — rejected:
it breaks every prior engine's read of that UC and collides with the rollback
guarantee. (b) *Accept the break and gate it in `bugate-update rollback`* —
rejected: it converts a data-compatibility problem into an operational
tripwire, and the tripwire is only consulted during rollback, which is exactly
when the operator is already in trouble. (c) *Make falsification optional,
degrading to a structural check* — rejected by the owner: a negative control
that can be skipped is not a control.

## Status of the frozen surface

Named in the stage-2 contract and not to be renamed without a further
amendment: the vocabulary (`self_heal` / `triage` / `healing`; `recovery`,
`evidence_recovery`, and "Layer 5" are forbidden — the first two are occupied by
`RECOVERY_EVENT` / `RECOVERY_ARCHIVE_SCHEMA` / `_recovery_phase`), the sidecar
layout, the three schema strings, the `role_chain_anchor` field names, the six
event names and their states, the `self_healing` key with its four modes, the
five-field JSON result, and the four exit codes (0 / 2 / 3 / 4).

## Amendment — status and resume/restore semantics (2026-08-12)

The effective contract is narrower and more precise than the retained paragraph
above. Both `status` and `resume` skip the main role-evidence snapshot preflight
so an applied test-asset change cannot make exact restore unreachable. They still
load and verify the sidecar through its structural and integrity boundary.

`status` normally reports the verified sidecar state. When it detects that the
recorded role-chain anchor has drifted, it persists invalidation metadata in
`00_self_healing/chain.json`; it therefore is not unconditionally read-only.
This metadata update does not append an event receipt. `resume` may restore only
the exact prior workspace bytes authenticated by the engine-created apply
journal and may update that journal; it also appends no event receipt. Neither
command is a general bypass for artifact drift or an unrestricted write path.

## Amendment — proposal, review, and restore hardening (2026-08-12)

This amendment preserves the accepted sidecar/event vocabulary and tightens
when an attempt exists, what an independent reviewer must have seen, and what
may be changed during exact restore.

**Only a repair-eligible triage opens an attempt.** `diagnose` mode and a
`healing_eligible: false` classification still write the ordinary governed
post-run reports (`self_healing.json`, `self_healing.md`, and
`self_healing_repair_plan.md`), but publish no `triage_recorded` sidecar receipt
and create no sidecar attempt. The six-event table applies only after an
eligible failure enters `verify` or `apply_with_approval`.

**The reviewer receives completed negative-control evidence.** During
`propose`, the engine performs the structural check, sandbox before/after
verification, required oracle falsification, and repaired-test reverse
verification before publishing
`healer_handoff`. A missing, inconclusive, or below-threshold falsification
cannot advance to independent review. A repaired test that remains green under
the exact clean counterexample is rejected without creating or changing an
attempt artifact. On success, the engine writes
`review_context.json` and binds its hash and full binding object into the
immutable handoff receipt. The binding names the UC and attempt; a canonical
manifest of the accepted business brief and inventory with their exact hashes;
the candidate manifest and human-readable patch; the captured original failure;
the before/after command logs; the verification and falsification results; and
the oracle references.

The ordinary reverse-verification lane is intentionally bounded to exact JSON
evidence files declared by the profile, located inside the governed workspace,
and authenticated as non-symlink regular files. The falsification artifact
embeds the spec/evidence path-and-SHA manifest; review and close both recompute
it. A candidate with no runner-visible clean counterexample is blocked, never
silently approved.

A dynamic counterexample is only a falsifying observation; because arbitrary
Python can recognize any finite candidate-visible mutant, it is not by itself a
proof of honest behavior. Authorization is therefore limited to two closed,
decidable candidate languages, each with exactly one changed test file:

- The evidence-free NameError/literal language requires the complete module AST
  to be identical except for one local immutable-scalar assignment and one
  replacement of the recorded unresolved name. The complete module contains
  only unique primitive bindings, unannotated zero-argument `test_*` functions,
  direct primitive equality assertions without message expressions, and one
  direct call of each test.
- The JSON-evidence language permits only the exact `json` and `Path` imports,
  unique bindings, one direct scalar load from the exact declared evidence
  path/key, an inherited immutable-scalar observation, a direct equality, and
  unchanged auxiliary tests in that same closed grammar. Removing the exact
  evidence delta and restoring the recorded unresolved name must reconstruct
  the complete original AST. Workspace and configured import shadows block;
  BUGate also replays pristine, mutant, and restored executions under
  `python -I -S` so the accepted imports have standard-library semantics.

Both languages require coherent UTF-8 source: Python's encoding detection and
the exact byte-compiled AST must agree with the decoded AST. Alternate source
encodings cannot make matcher-visible comments become runner-visible code.

Both languages perturb the observation source as well as applying any declared
JSON counterexample. Equality overloads, descriptors, helpers, decorators,
dynamic imports, rebinding, annotations, dispatch, control flow, and additional
candidate files have no slot in these languages; adding that next layer changes
the complete syntax and blocks with
`repaired_test_reverse_verification_unavailable`. This boundary deliberately
blocks more complex honest repairs until a separately governed observation
contract exists. It proves falsifiability and the stated scalar binding, not
full business provenance or correctness over every undeclared state.
Nondeterminism, host compromise, and operating-system-level sandbox escape
remain residual risks requiring independent review.

> **Amendment pointer (2026-08-18):** The observation-source perturbation
> described above is retained as accepted. What it is allowed to *conclude* is
> narrowed by the dated amendment below, which also records why an in-band
> execution witness cannot establish the binding it was introduced to prove.

The review document must reproduce that binding exactly. Only
`dispatch_mode: real_peer_dispatch` is trusted, and its declared `runtime` must
equal the fresh review session's actual normalized `BUGATE_AGENT_RUNTIME`
(`codex` or `claude`). `findings` is a non-empty list of itemized
`{claim, evidence}` objects; `residual_risks` is a list whose entries, when
present, are non-empty strings. The gate archives the supplied review document
byte-for-byte as `independent_review.json` and records its SHA-256 in the review
receipt; a review for another attempt, candidate, or evidence set cannot be
reused.

**Approval cannot be configured away.** Both
`independent_review_required` and `human_approval_required` are frozen `true`;
setting either to `false` is a configuration error. `apply_with_approval` still
requires a non-empty human-approval record at `close`.

**Sandbox and apply paths fail closed.** Before making the verification copy,
the engine rejects every symbolic link on the surface that would be copied.
Candidate and write targets are rechecked without following symbolic links.
The apply journal authenticates exact prior and candidate bytes plus POSIX
permission bits. `resume` restores both an interrupted `applying` window and a
durable `applied` window while the attempt is still independently verified,
restoring existing files' bytes and permission bits, removing journal-created
files, and removing only empty journal-created parent directories. New regular
files use mode `0600`. This is not a promise to preserve ownership, xattrs, or
ACLs.

**Receipt-first publication has two authenticated outcomes.** A sidecar event
publishes its immutable receipt before replacing the chain index. If a process
is interrupted between those two writes, the next read first constructs and
fully replays the sole possible next chain: schema, sequence, state edge,
parent/hash, actor/session, role-chain anchor, and Memory envelope must all be
valid. A strict-`required` receipt for one of the three Memory-anchored events
is indexed only after a read-only exact Memory lookup proves that event's
transition identity and receipt hash; the reconciliation creates no Memory
record and changes neither the main role-evidence tree nor any receipt.

A locally valid receipt without that authenticity proof is preserved but never
accepted. This covers every unanchored event and every `best_effort` orphan.
Its exact bytes move to the content-addressed attempt evidence path
`attempts/<attempt_id>/unindexed_<event>_<content-sha256>.json`; the root-level
orphan is then removed, the previously indexed state and chain bytes remain
unchanged, and the same event may retry through the ordinary actor, state,
anchor, session, and Memory controls. The preserved file remains evidence and
never becomes a chain entry. A malformed receipt, invalid state edge, stale
anchor, forged strict Memory id, or exact-Memory mismatch fails integrity and is
neither indexed nor treated as an authentic transition.

All existing project, artifact, sidecar, `attempts`, and attempt-directory path
components are inspected with `lstat`, constrained below the real project and
artifact roots, and required to be real directories. Chain, receipt, review,
candidate, and journal leaves are likewise non-symlink regular files. A
symbolic-link parent or storage escape fails before read or write.
The role-chain anchor reader enforces those checks independently of main
preflight, and each verified sidecar load inventories every direct root entry;
an unknown or symbolic-link leaf is an integrity failure, not invisible state.
Attempt evidence is recursively inspected under the same directory/regular-file
rule. Indexed receipt anchors always bind `post_run_active` and remain identical
across adjacent receipts unless an indexed drift record is explicitly
superseded; malformed live anchor values never become a durable drift record.

## Amendment — cooperative serialization and reviewed restore topology (2026-08-12)

The receipt-first design needs a concurrency distinction that the retained
crash-recovery wording did not state. A durable receipt without its chain entry
is an orphan only after its publisher has stopped; it is also the normal live
publication interval. BUGate therefore serializes every main role transition
and every complete sidecar verified read, publication, invalidation, and status
snapshot with an exclusive advisory `flock` on the same existing UC artifact
directory. No persistent lock file extends either evidence layout.

Each complete self-heal CLI step holds the same lock from sidecar-state
validation through all attempt-evidence or workspace writes and the final
receipt publication, passing one re-entrant store through the handler. A
concurrent losing proposal, review, close, or resume therefore cannot mutate
evidence after a cooperating winner commits the next state.

The sidecar publisher holds that lock across strict-Memory work, receipt
durability, and chain-index replacement. A cooperating reader cannot enter
reconciliation or quarantine during that live gap. If the publisher dies, the
kernel releases the descriptor and the next reader applies the authenticated
orphan rules above. Drift persistence uses the previously read sidecar-head
anchor as a compare-and-swap token, reloading both verified sidecar head and
current role-chain anchor under the lock. `status` likewise returns one locked
snapshot of state, attempt identity, review outcome, and anchor comparison,
persisting newly observed drift before returning it only when the live anchor
passes the full anchor schema. A missing main chain yields the read-only empty
sentinel: status repeatedly returns `invalidated` with `lifecycle_drift` but
never writes that empty hash into sidecar `chain.json`. A malformed or
unreadable anchor likewise does not create a drift record.

This lock is a linearization mechanism for cooperating BUGate processes, not an
OS security boundary. A same-account process can ignore an advisory lock or
mutate a path outside BUGate; the existing same-OS threat caveat remains.

The independently reviewed candidate now binds restore topology as well as the
proposed file contents. Its manifest digest covers the exact `baseline.json`
bytes, baseline-authenticated original before images, candidate after images,
the reconstructed patch, and the canonical set of parent directories absent
before apply. `close` recomputes this manifest and re-authenticates all live
review inputs, the archived review document, its receipt binding, reviewer
runtime/verdict, falsification result, and triage evidence before closing in
either mode.

The apply journal is an internal `bugate.self-heal-apply-journal/v2` envelope.
It must exactly bind that reviewed manifest, file set, before/after bytes,
ordinary POSIX permission bits, and the same pre-apply missing-directory set.
Directories are created with a no-pre-existing-entry check. Before any resume
write, the complete affected tree is checked: unexpected files, directories,
or symlinks block the restore. Resume removes only engine-created new files and
then only authenticated originally-missing directories that are empty; a
pre-existing parent cannot enter that set.

The restore guarantee is deliberately limited to exact bytes and ordinary
POSIX permission bits (`0o777`). Special mode bits fail closed before apply.
Ownership, xattrs, and ACLs are not captured and are not promised to be
restored.

## Amendment — replayable payloads and authenticated anchor reads (2026-08-12)

This amendment records the final integrity review without rewriting the
earlier decision or changing the six-event/state vocabulary.

**Every event payload is authenticated, replayable data.** The payload of every
sidecar receipt must be a JSON object whose complete value graph can be encoded
as BUGate canonical JSON and parsed back under the active interpreter: object
keys are strings, values use only JSON types, floating-point values are finite,
and cyclic or implementation-specific objects are rejected. The same validator
runs before publication and during full receipt replay. A non-object or
non-replayable value is `self_heal_event_payload_invalid`; it is never silently
normalized.

Three payload fields are part of the state edge rather than descriptive
metadata. `triage_recorded.payload.healing_eligible` must be exactly boolean
`true`. `independent_review.payload.outcome` must equal that receipt's
`resulting_state`. `attempt_closed.payload.final_state` must be either
`healing_verified` or `healing_rejected` and must equal the authenticated prior
state. These bindings are checked both prospectively and on replay, with the
stable reasons `triage_healing_eligible_not_true`,
`review_outcome_state_mismatch`, and `close_final_state_mismatch`.

**A losing triage is zero-write.** For an eligible repair flow, maximum-attempt
and complete sidecar publication preflight now run before the three ordinary
triage reports, attempt evidence, or a receipt can be written. If that preflight
rejects the state, payload, actor, session, attempt, or anchor, the requested
triage changes none of those bytes. This prevents a rejected retry from
rewriting evidence that belongs to the already-open attempt.

**A main-chain anchor is usable only after main-chain verification.** The
anchor reader first applies the role-evidence `lstat` boundary and requires any
present `00_role_evidence/chain.json` to be a non-symlink regular file. It then
checks
the exact minimal five-key v1 envelope, schema, non-negative sequence,
lifecycle state, head, and canonical latest-receipt index before running the
complete `verify_chain` check over receipt inventory, hashes, state edges, and
latest-receipt history. Only that fully verified chain may supply its
`head_sha256` as `chain_sha256`, plus `sequence` and `lifecycle_state`, or become
a persisted drift observation.

**The CLI boundary never leaks an integrity traceback.** Its result object has
exactly `status`, `exit_code`, `blocking_reasons`, `artifact_paths`, and
`next_action`. A malformed but safely readable main-chain envelope/replay maps
to `lifecycle_drift`, `invalidated`, exit 4. Sidecar and unsafe-path contract
violations retain their precise reason and map to `blocked`, exit 2; ordinary
evidence I/O/decoding failures also map to a five-field blocked result rather
than exit 1. `status` verifies the sidecar before reading the main anchor, so if
both are damaged the immutable sidecar corruption is reported instead of being
masked by lifecycle drift.

## Amendment — assertion-binding proof and the published fake-green verdict (2026-08-18)

This amendment records a correction to *what BUGate is entitled to publish* about
a surviving negative control. It preserves the accepted sidecar/event vocabulary,
the six-event table, the two closed proof languages, and every exit-code mapping
above; only the condition under which `repaired_test_survives_mutation` may be
returned is narrowed, and it is narrowed in the blocking direction.

**Two defects were found against a fully green suite.** Neither moved any test at
the time it existed, which is the reason this amendment exists at all.

*The observation control did not check what it claimed to perturb.* Decision
item 6 requires the repaired command to fail at the changed governed assertion
after the counterexample is applied, and the closed languages additionally
perturb the observation source. That perturbation targets a module-level
assignment, but Python resolves an assertion operand by scope, not by module
order. A candidate that rebinds the same name inside the assertion's scope — a
function-local assignment, a `global` write, a parameter, an import alias, an
`except .. as`, a same-named `def`/`class` — makes the perturbation invisible to
its own assertion while the assertion still executes. Such a candidate survived
and was published as a *proven* fake-green repair, even when it was in fact bound
to the declared external contract and would die if that contract were falsified.
The control now counts every binding site of the observation name in the
candidate module and refuses to derive a control unless there is exactly one:
the module assignment it perturbs. This is a positive requirement, not a
blacklist of rebinding tricks, and it fails closed to
`repaired_test_reverse_verification_unavailable`.

*The binding that carried the verdict rested on the candidate's own testimony.*
The engine established "the mapped assertion executed" by injecting a sentinel
`raise AssertionError(...)` immediately above that assertion, re-running the
candidate's own verification commands, and confirming when the sentinel string
appeared in the resulting output with an `AssertionError` attributed to that
file and line. Every input to that judgement is written by the process under
judgement. The sentinel and the candidate share one interpreter, and the
candidate can read the injected line out of its own `__file__` before deciding
what to print, so it can produce the whole confirmation without executing
anything. A nonce does not help — the nonce is in the source the candidate
reads. A decoy injection into a line that provably will not execute does not
help either: it removes candidates that forge whenever the sentinel appears
anywhere in their source, and it was measured to leave untouched a candidate
that forges only when the sentinel sits immediately above an `assert`, which is
a source-local test the candidate can run on itself. No in-band dynamic witness
is sound against a candidate that can read its own source.

A separate observation makes the point structural rather than merely practical:
even a *sound* execution witness would establish that the assertion line was
reached, which is not the proposition the basis name asserted — that the
perturbed observation source is the binding of that assertion's operand. Line
reachability and operand binding are different claims.

**The in-band witness is therefore retired.** An assertion binding may now be
confirmed only by a closed *static* proof: the engine-derived literal source, the
canonical external observation source, or the canonical declared-JSON source.
Every other shape records an unproven binding and the caller blocks at exit 2.
The negative control still runs and a survivor is still recorded in the
governance artifact; what is withdrawn is the promotion of that record to a
published verdict.

**This is a contraction, not a relaxation, and the code path makes that
checkable.** `repaired_test_survives_mutation` requires a confirmed binding
together with a surviving control; a binding that is not confirmed falls through
to `repaired_test_reverse_verification_unavailable`. Downgrading a binding can
therefore only move a result toward exit 2 and can never move one toward exit 0.
Measured over the complete review corpus, five attack candidates and one
adversarial probe case moved from exit 3 to exit 2; every honest control kept
exit 0; nothing moved to exit 0; and the affected candidates remained blocked
with the governed workspace and the complete `00_self_healing/` tree unchanged.

**The cost is stated rather than hidden.** Those candidates really are fake-green.
BUGate no longer claims to have proven it. They are blocked, never applied, and
not named. The gate's safety is unchanged; the precision of its public diagnosis
is narrower than the truth, and the operator-facing boundary in `CAPABILITIES.md`
says so.

**The governing principle, generalized.** A published attribution of
responsibility may not rest on evidence produced by the subject of that
attribution. `blocked` and `rejected` are different public governance outcomes
with different facts, different attribution, and different next actions; the
stricter of the two must be reachable only from evidence the subject cannot
author. Where BUGate cannot obtain such evidence, it says "cannot verify" rather
than saying something stronger from something weaker.

**Residual surfaces, explicitly not closed.** The binding-site count reads the
candidate module's own AST, so a rebinding performed from another file or through
a dynamic namespace write is outside it. After this amendment that no longer
produces a wrong `rejected` verdict, because the lane can no longer confirm a
binding at all — but it does mean a recorded `result: survived` is not by itself
proof of fake-greenness and must not be read as one. Separately,
`repaired_test_survives_mutation` was not observed in any corpus after this
change; it has not been proven unreachable and is not treated as dead.

**Verification discipline.** The reproductions for both defects, the honest
controls that guard against over-tightening, and an exact-one-anchor engine
mutation harness now live in the engine's own test tree rather than in an
external review workspace, so a future change that reverts either fix fails a
test in the same repository that carries the change.
