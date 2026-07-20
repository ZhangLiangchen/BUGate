# Using BUGate After Import

[English](using-bugate.md) | [简体中文](using-bugate.zh-CN.md)

The operator's guide for day-to-day Claude Code / Codex work after BUGate is
imported into a SUT automation test repo. (`.bugate` below = the vendor dir.)

## 0. Open the right directory and role session

Open the **SUT test repo itself** (the directory holding `bugate.config.yaml`)
as the project root. Hooks load from that session workspace; opening a parent
directory carries no physical guard. After import or re-import, re-trust the
changed Codex hook hash in Desktop. Until re-trust, file acceptance can pass but
Codex runtime enforcement is not active.

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

2. Run the full pre-code chain, still as designer:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> --auto
   ```

   This runs Wave 1 independent peers, Layer 1/2/3 gates, 03A generation, 03B
   adversarial peers, the full contract, and degraded-review checks. Peer
   subprocesses do not inherit the designer's lifecycle identity. Steps stop at
   the first failure; 03B remains `pending` for human review.

3. **Human checkpoint:** a real human reviews the divergence/adversarial
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

4. In a **new implementer session**, accept and implement Layer 4:

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

5. In a **new reviewer session**, accept the second exact Memory ID, run the
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
| Local receipt verification | `.bugate/bin/bugate-role verify docs/usecases/<UC> --phase <phase>` |
| Local + strict Memory verification | `... verify ... --strict-memory` |
| One-shot capability self-check | `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke` |
| Recall / record ordinary memory | `.bugate/bin/memory-recent --agent <role>` / `python3 .bugate/scripts/memory_bus.py note ...` |

The orchestrator prints one lifecycle status: `BLOCKED`,
`READY_FOR_HUMAN_ACCEPTANCE`, `READY_FOR_DESIGNER_HANDOFF`,
`IMPLEMENTATION_UNLOCKED`, `READY_FOR_REVIEWER_HANDOFF`, `POST_RUN_ACTIVE`, or
`CLOSED`. Treat it as state, not permission to skip the next command.

Peer-dispatch knobs (`SDTD_CLI_*`) remain profile/repo-owned. They are preserved
for peer subprocesses while lifecycle role/session/receipt identity is removed.

## 3. What stays human

- Accepting 03B; the CLI records only a decision that already happened.
- Adjudicating execution/self-healing results and signing 04/05.
- Deciding defect versus intended behavior and owning incident closure.
- Fixing evidence or environment when a gate refuses; never lowering a gate.

## 4. Migration, drift, and boundaries

- A v0.3.x profile without `role_governance` remains unchanged. Enabling
  `required` does not grandfather passed UCs: create current human acceptance,
  handoff, and acceptance receipts.
- Same-role/same-session acceptance, a non-exact Memory ID, missing receipt, or
  direct edits to `00_role_evidence/**` are blocked.
- Profile/pre-code drift restarts from designer acceptance/handoff;
  implementation drift restarts from implementer handoff/reviewer acceptance.
  Append a superseding generation; never delete evidence to reset.
- Ordinary edits verify local hashes and do not call Memory. A Memory outage
  blocks the next transition before a local unlock receipt is published.
- These controls provide declarations, session separation, hash linkage,
  Memory anchors, and tamper/drift detection, not non-repudiable identity.
  Hooks cannot intercept arbitrary shell redirection or external editors;
  stronger isolation requires OS accounts, containers, managed runners, or
  role-scoped credentials.

## 5. Where the deeper docs live

- Layout/profile adaptation: this skill's `SKILL.md` (one directory up).
- Operations, diagnosis, and recovery: `field-guide.md`.
- Gate criteria and schema: `.bugate/.shared/skills/bugate/`.
