# Using BUGate After Import

[English](using-bugate.md) | [简体中文](using-bugate.zh-CN.md)

The operator's guide for day-to-day Claude Code / Codex work after BUGate is
imported into a SUT automation test repo. (`.bugate` below = the vendor dir.)

## 0. Open the right directory and role session

Open the **SUT test repo itself** (the directory holding `bugate.config.yaml`)
as the project root. Hooks load from that session workspace; opening a parent
directory carries no physical guard. For a fresh import, follow the install
acceptance output; for an existing installation, use
[`updating-bugate.md`](updating-bugate.md), never a re-import. Re-trust Codex
Desktop only when import/update actually changes its hook hash, and open a new
agent session after any hook change. Until those required process boundaries
are complete, file acceptance can pass but the new runtime enforcement is not
active.

With `role_governance.mode: required`, use three separate processes/sessions:

```bash
.bugate/bin/bugate-role run --role designer -- codex
.bugate/bin/bugate-role run --role implementer -- claude
.bugate/bin/bugate-role run --role reviewer -- codex
```

`run` generates a fresh `BUGATE_SESSION_ID` and sets identity only for its
child. A SessionStart hook reports identity and chain state but cannot export
variables into its parent. For Desktop, launch from an equivalent environment
and open a new session; do not assume a hook activated the parent process.

## 1. The working loop for a new requirement

Give the agent the requirement evidence and UC name, then follow this chain.

1. In the **designer session**, scaffold first, then author 01/02/03:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> --init
   ```

   `--init` and `--auto` are separate operations; `--init --auto` is rejected.
   Required mode initializes pre-code and selected optional modeling artifacts,
   not reviewer-owned 04/05.

2. Establish the UC's lineage before any normal lifecycle publication:

   ```bash
   .bugate/bin/bugate-role lineage-status docs/usecases/<UC> --json

   # Genuine first use only; copy the exact ID from the status output.
   .bugate/bin/bugate-role lineage-init docs/usecases/<UC> \
     --lineage-id <exact-lineage-id>
   ```

   Before the first init, exit 2 with `integrity_state: uninitialized` is the
   expected read-only result. Confirm that this is truly a new UC; a normal
   publisher never makes that decision. If a non-empty pre-v0.4.3 chain exists,
   use exact `lineage-adopt`, not init. If a registered chain is missing,
   diverged, or has an incomplete transaction, recover it first. The decision
   table and exact commands are in §4. Continue only at `aligned`.

   Initialization persists its intent before any Memory request and advances
   `pending` -> `root_absence_verified` -> `root_verified` ->
   `registry_initialized` -> `chain_written` -> `completed`. If JSON status
   shows `recovery_pending` with `active_initialization`, rerun the same exact
   `lineage-init`; it resumes that intent and normal publishers remain blocked.
   Do not send this state to `recover`. A strict root found during the initial
   pending probe closes the pre-root/pre-lineage intent and reports
   `migration_required` instead.

3. Run the full pre-code chain, still as designer:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> --auto
   ```

   This runs Wave 1 independent peers, Layer 1/2/3 gates, 03A generation, 03B
   adversarial peers, the full contract, and degraded-review checks. Peer
   subprocesses do not inherit the designer's lifecycle identity. Steps stop at
   the first failure; 03B remains `pending` for human review.

4. **Human checkpoint:** a real human reviews the divergence/adversarial
   evidence and explicitly sets 03B to `gate_status: passed`. The agent must not
   make or impersonate that decision. The designer records the already-made
   decision and creates the strict Memory handoff:

   ```bash
   .bugate/bin/bugate-role approve docs/usecases/<UC> --approved-by <human-id>
   .bugate/bin/bugate-role handoff docs/usecases/<UC> \
     --phase pre_code --to implementer
   ```

   `approve` never edits 03B; `approved_by` is declarative, not authentication.
   Do not rerun pre-code `--auto` after this receipt. Use the designer-handoff
   receipt's exact Memory `memory_id` next.

5. In a **new implementer session**, accept and implement Layer 4:

   ```bash
   .bugate/bin/bugate-role accept docs/usecases/<UC> \
     --phase implementation --handoff-id <exact-memory-id>
   ```

   `check_bugate.py` and `check_role_evidence.py` must both pass. After the
   implementation, hand off every concrete guarded file (repeat the flag when
   needed):

   ```bash
   .bugate/bin/bugate-role handoff docs/usecases/<UC> \
     --phase implementation --to reviewer \
     --implementation-file <guarded-test-file>
   ```

6. In a **new reviewer session**, accept the second exact Memory ID, run the
   tests, and generate 04/05:

   ```bash
   .bugate/bin/bugate-role accept docs/usecases/<UC> \
     --phase post_run --handoff-id <exact-memory-id>

   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> \
     --auto --scope post-run --pytest-log <run.log> \
     --command "<exact test command>" --env <env> --exit-code <rc>
   ```

   Post-run regenerates 04/05 drafts; back up and merge any curated history,
   adjudicate the result honestly, then finish with a receipt:

   ```bash
   .bugate/bin/bugate-role complete docs/usecases/<UC> \
     --phase post_run --run-command "<exact test command>" \
     --exit-code <rc> --evidence-file <run.log> \
     --gate-status <passed|failed>
   ```

   Passed completion requires exit code 0 and passed 04/05. Failed completion
   remains `post_run_active`; it does not manufacture a green close.

## 2. Command quick reference

| Intent | Command |
|---|---|
| UC artifact status | `python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC>` |
| Role-chain status | `.bugate/bin/bugate-role status docs/usecases/<UC> [--json]` |
| Lineage identity/integrity status | `.bugate/bin/bugate-role lineage-status docs/usecases/<UC> --json` |
| Confirmed first use | `.bugate/bin/bugate-role lineage-init docs/usecases/<UC> --lineage-id <exact-id>` |
| Adopt verified legacy chain | `.bugate/bin/bugate-role lineage-adopt docs/usecases/<UC> --lineage-id <exact-id> --expected-head <exact-head>` |
| Recover registered history | `.bugate/bin/bugate-role recover docs/usecases/<UC> --lineage-id <exact-id> --expected-head <head-or-EMPTY> [--archive <trusted-archive>]` |
| Local receipt verification | `.bugate/bin/bugate-role verify docs/usecases/<UC> --phase <phase>` |
| Local + strict Memory verification | `... verify ... --strict-memory` |
| One-shot capability self-check | `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke` |
| Recall / record ordinary memory | `.bugate/bin/memory-recent --agent <role>` / `python3 .bugate/scripts/memory_bus.py note ...` |

The orchestrator prints one lifecycle status: `BLOCKED`,
`READY_FOR_HUMAN_ACCEPTANCE`, `READY_FOR_DESIGNER_HANDOFF`,
`IMPLEMENTATION_UNLOCKED`, `READY_FOR_REVIEWER_HANDOFF`, `POST_RUN_ACTIVE`, or
`CLOSED`. Treat it as state, not permission to skip the next command.

Lineage integrity is a separate axis: `uninitialized`, `aligned`,
`migration_required`, `history_missing`, `history_diverged`,
`recovery_pending`, or `registry_unavailable`. Only `aligned` admits a normal
lifecycle publication; an integrity result never rewinds the lifecycle.

Peer-dispatch knobs (`SDTD_CLI_*`) remain profile/repo-owned. They are preserved
for peer subprocesses while lifecycle role/session/receipt identity is removed.

## 3. What stays human

- Accepting 03B; the CLI records only a decision that already happened.
- Adjudicating execution/self-healing results and signing 04/05.
- Deciding defect versus intended behavior and owning incident closure.
- Fixing evidence or environment when a gate refuses; never lowering a gate.

## 4. Migration, drift, and boundaries

- A v0.3.x profile without `role_governance` remains unchanged. Enabling
  `required` does not grandfather passed UCs. First establish/adopt each UC's
  lineage; then create current human acceptance, handoff, and acceptance
  receipts. A successful updater `apply`/`verify` installs engine files only;
  it never accepts lineage migration or edits the profile, namespace,
  `00_role_evidence/**`, machine registry, or Memory home.

| Integrity result | Required operator route |
|---|---|
| `uninitialized` | Confirm true first use, copy the exact ID, and run `lineage-init`. |
| `aligned` | Continue the current lifecycle. |
| `migration_required` with a verified non-empty legacy chain | Run `lineage-adopt` with the exact chain head; it rewrites zero receipts. |
| `migration_required` with only an existing strict root | Restore trusted pre-loss evidence before adoption/restoration; never initialize over the root. |
| `recovery_pending` with `active_initialization` | Rerun `lineage-init` with the same exact ID; it resumes the initialization journal. |
| `history_missing`, `history_diverged`, or `recovery_pending` with an active publication/recovery transaction | Run `recover` with the exact registry head. `EMPTY` means only the sequence-zero expected head. |
| `registry_unavailable` | Stop writes and repair the registry or explicit root-probe failure before reclassifying. |

```bash
.bugate/bin/bugate-role lineage-adopt docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-chain-head>

.bugate/bin/bugate-role recover docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-head-or-EMPTY> \
  [--archive <trusted-recovery-archive>]
```

- Under `memory_mode: required`, deterministic roots and immutable checkpoints
  are exact-verified and can reconstruct missing local evidence while registry
  and Memory history survive. An explicitly supplied trusted archive selects
  candidate bytes, but retained strict checkpoints remain mandatory and
  authoritative; every archive envelope must exactly match them before any
  write. It is not an offline fallback for unavailable or divergent strict
  Memory. Under `best_effort`, the registry still detects
  deletion and serializes publishers, but lost local history requires an
  independently retained trusted `bugate.role-recovery-archive/v1` passed with
  `--archive`. Best-effort lifecycle publication may still attempt transition
  Memory calls, but it tolerates their failure and creates no root/checkpoint.
  After validation/preflight, recovery claims the active source or creates and
  claims a pending `recovery_restore` before target writes. It restores the
  committed predecessor and resumes an original transaction through its durable
  stage—including a verified checkpoint ready for CAS. One SQLite transaction
  then terminalizes that restore/lifecycle source and installs the sole pending
  `evidence_recovery` successor. There is no aligned/no-audit crash gap; if the
  active source is already `evidence_recovery`, retry resumes it directly
  instead of installing another or manufacturing a duplicate receipt. A dead recovery
  claimant may be taken over only after a liveness check and exact-token CAS.
  A durable best-effort unanchored marker is preserved without retrying HTTP.
- Same-role/same-session acceptance, a non-exact Memory ID, missing receipt, or
  direct edits to `00_role_evidence/**` are blocked.
- Profile/pre-code drift restarts from designer acceptance/handoff;
  implementation drift restarts from implementer handoff/reviewer acceptance.
  Append a superseding generation; never delete evidence to reset.
- Ordinary `status`, hooks, and per-edit preflight verify the local registry,
  receipt chain, and hashes with zero Memory HTTP requests. `lineage-status` is
  an explicit operator diagnostic and probes the deterministic root only when
  required Memory plus local state appears `uninitialized`. Required-mode init,
  adoption, and recovery, plus lifecycle transitions, can also be explicit
  network boundaries; a strict Memory outage leaves them non-zero without a
  completed local unlock publication. Once an initialization intent exists,
  failure is instead visible as `recovery_pending` and exact `lineage-init`
  resumes it.
- These controls provide declarations, session separation, hash linkage,
  registry/Memory anchors, and tamper/drift detection, not non-repudiable
  identity. Hooks cannot intercept arbitrary shell redirection, recursive
  deletion, or external editors; stronger isolation requires OS accounts,
  containers, managed runners, protected backups, or role-scoped credentials.
  A same-OS-user actor who deletes workspace evidence, the machine registry,
  and the complete Memory home is outside the local boundary. History lost
  before deterministic anchoring cannot be inferred from an empty directory.

## 5. Where the deeper docs live

- Fresh-install versus update routing, transactional upgrade, verification,
  rollback, and profile/session boundaries: `updating-bugate.md`.
- Layout/profile adaptation: this skill's `SKILL.md` (one directory up).
- Operations, diagnosis, and recovery: `field-guide.md`.
- Gate criteria and schema: `.bugate/.shared/skills/bugate/`.
