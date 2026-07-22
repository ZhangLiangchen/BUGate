# Imported-Mode Field Guide

Lessons from imported-mode field rounds on real SUT test repos (import → three
`--auto` rounds → live Layer-4 execution and closure; later: a full live-SUT
regression round with a verdict flip), distilled SUT-neutrally. Read this AFTER
`IMPORT_PROMPT.md`; it covers what the protocol itself cannot tell you. Known
v0.3.1 kit gaps found in the field are listed at the end.

## 1. Dual-agent CLI dispatch: diagnose before you degrade

- **`check-env` proves binaries, not reachability.** A peer that resolves on
  PATH can still fail dispatch. The bridges swallow peer stderr (the failure
  archive may be a 1-byte file), so reproduce the exact bridge command by hand
  to see the real error:
  `echo test | claude -p --permission-mode dontAsk --output-format text` /
  `echo test | codex exec --sandbox read-only -`.
- **Auth-shaped errors are often network-shaped.** `401 Invalid authentication
  credentials` / `403 Request not allowed` from a spawned `claude -p` can mean
  the machine's direct egress to the model API is blocked, not that login is
  broken. Diagnostic ladder: (1) strip harness session env vars (`CLAUDECODE`,
  `CLAUDE_CODE_ENTRYPOINT`, …) — spawning `claude -p` from inside a Claude Code
  session trips nested-session/credential paths; (2) retry under `env -i` with
  only HOME/PATH; (3) confirm keychain/credential readability; (4) try the
  local proxy. If the interactive terminal works but spawned automation
  doesn't, suspect egress first.
- **Persist proxy repo-locally, never in the kit.** The bridges' own injection
  surface is `SDTD_CLI_HTTPS_PROXY` / `SDTD_CLI_HTTP_PROXY` /
  `SDTD_CLI_ALL_PROXY` — it reaches only the spawned peer CLIs, leaving gate
  scripts and git traffic direct. Good imported-repo pattern: a committed
  wrapper script (exports proxy + timeout, execs `sdtd_orchestrator.py`) plus,
  for Claude Code, a `.claude/settings.json` `"env"` block with the three
  `SDTD_CLI_*` keys. Machine facts stay out of the vendored kit.
- **`dispatch_mode` is the only trustworthy review marker**:
  `real_peer_dispatch` / `partial_real_peer_dispatch` / `fallback_placeholder`
  in the divergence report and 03b frontmatter. Exit 3 with a degraded banner
  is fail-closed working as designed — classify the failing peer
  (environment / kit / SUT) instead of reaching for
  `--allow-degraded-peer-review`.

## 2. `--auto` semantics operators must know

- **Steps short-circuit** (`rc = rc or step()`): everything after the first
  failing gate is skipped. Absence of later output is not success.
- **Before a role receipt exists, peer review rewrites 03b** to
  `gate_status: pending` by design. In legacy/off mode, `--skip-peer-review`
  preserves a curated 03b with loudly logged review debt. In required mode,
  once `bugate-role approve` has recorded a real human acceptance, pre-code
  `--auto` is blocked so accepted evidence cannot be silently regenerated;
  proceed directly to designer handoff.
- **Dialect must live in the profile.** The orchestrator does not forward
  `--schema`; set `semantic_schema: original-gate` in the profile when
  importing a pre-canonical artifact corpus (v1.3 stays the target for new
  artifacts). Costly ordering note: Wave-1 dispatch runs BEFORE the Layer-1
  gate, so a dialect mismatch burns two real peer calls and rewrites
  00_multiview before failing — align the dialect first.
- **When copying an existing UC into an imported repo, exclude
  `00_multiview/cli_bridge_failures/`** — the degraded check counts ANY file
  there, including stale archives from another repo, forcing exit 3 forever.
  Also skip legacy `00_orchestration/` state.

## 3. Layer-4 post-run closure

- **Required mode needs reviewer acceptance before post-run.** An implementer
  handoff must name at least one guarded implementation file; a reviewer in a
  distinct session accepts its exact Memory ID before the orchestrator may
  write 04/05.
- **`--scope post-run` `--write` clobbers 04/05 without reading them.** The
  generated drafts are skeletons, not reports of record. SOP: back up 04/05 →
  run post-run → hand-merge the preserved history (incident narrative,
  evidence tables) with the new run-of-record. Treat this as part of closure,
  not an accident.
- **Human adjudication of `sut_defect_admissible` is part of the loop.** The
  self-healing MVP classifier refuses a SUT-defect verdict whenever polling
  vocabulary (timeout/connection) appears in the log. Override with evidence
  (sibling cases green in the same session, cross-date reproduction, the
  assertion's business nature) and record the adjudication in 04.
- **Closure ≠ green.** With an open SUT defect, `gate_status: failed` on 04/05
  is the honest terminal state; the structural closure gate is
  `check_bugate_v13_semantics <dir> --scope all` (add `--require-passed` only
  when you actually mean "all green").
- Post-run exit code is 0 even when the test run failed — the failure lives in
  file content. Don't gate CI on the orchestrator's post-run exit code alone.

## 4. Write-guard field notes

- **Import target = the test-framework home; sessions must open there.** Hook
  wiring loads from the agent session's workspace root. When BUGate is
  imported into a subdirectory of a larger repo, a session opened at the repo
  root loads none of the target's hooks and the guard is silently absent
  (verified live). `bugate_init` warns at install time when target ≠ git
  toplevel; mitigations: open sessions at the target, or export
  `BUGATE_PROJECT_ROOT=<target>` for the agent's environment.

- The normalized-glob resolver is fail-closed on zero AND ambiguous matches:
  test filenames must normalize (case, `-`/`_`) to exactly one UC directory.
  A mismatched slug blocks with "cannot bind to a UC artifact dir" — that's the
  guard working, not a bug.
- Legacy/off mode still admits a file from passed artifacts alone. Required
  mode additionally demands a current designer handoff and implementer
  acceptance; profile/pre-code drift re-locks that acceptance, and
  implementation drift re-locks reviewer acceptance.
- Verify both invocation forms during acceptance: direct path argument, and
  the hook-shaped stdin JSON payload — they exercise different parsing paths.

## 5. Memory bus field notes

- `memory_bus.py note --agent` accepts role names
  (builder/designer/implementer/reviewer/human/agent), **not** model names.
- `recent` shows only broadcast/addressed notes — a non-broadcast note that
  doesn't appear there is filtered, not lost; confirm by content-hash search.
- Namespace isolation is tag-based on one shared machine-level service:
  verify writability + isolation during acceptance by writing a probe note and
  searching it from a sibling namespace.
- Ordinary recall/note/search/Stop remains best-effort. Required role
  transitions are different: POST + exact GET + receipt binding + exact GET
  must all validate namespace, roles, UC, phase, transition, and receipt hash.
  Any failure returns non-zero and publishes no local unlock receipt.
- Every governed edit verifies the local chain only. Do not add live Memory
  calls to a per-edit hook; `bugate-role verify --strict-memory` is an explicit
  audit/recovery operation.

## 5A. Wave 7 lifecycle, migration, and recovery

- Wave 1 peers expose interpretation divergence inside the designer phase;
  Wave 7 actors are `designer`, `implementer`, and `reviewer` across distinct
  sessions. Peer child environments must not inherit lifecycle identity.
- A v0.3.x profile stays unchanged until `role_governance` is enabled. Existing
  passed UCs are not grandfathered into required mode: record current human
  acceptance, designer handoff, and implementer acceptance.
- Use `bin/bugate-role run --role ... -- <command>` to create each role process.
  SessionStart can report identity, but a hook child cannot export variables to
  its parent. Desktop needs a fresh launch/session with the intended env.
- **Updater correction (v0.4.2+):** historical rerun-importer upgrade advice is
  retired. `bugate_init.py` is fresh-install-only. Bootstrap an exact v0.3.x or
  pre-lock v0.4.x baseline from an unpacked release retained through the
  rollback window. Use the vendored `bugate-update`
  `status`/`plan`/`apply`/`verify`/`rollback` flow only while both its installed
  lock and launcher exist. A first updater rollback may restore the exact
  pre-updater image and remove that launcher; use the retained external
  `scripts/bugate_update.py` for `status`/`verify` after that rollback or an
  interruption, never recreate the launcher. Offline mode
  requires both archive and checksum; conflicts stay `NO-GO` without a broad
  force/adopt escape. Engine update preserves profiles, role evidence, Memory,
  and SUT-owned hooks; profile migration is a separate explicit commit. See
  `updating-bugate.md`, including its 128-entry history limit and SHA-256
  threat model. Re-trust Codex only on an actual Codex hook byte change, and
  start a new agent session after any hook change before claiming enforcement.
- Never edit `00_role_evidence/**` directly or delete it to reset. Profile or
  pre-code drift restarts from designer acceptance/handoff; implementation
  drift restarts from implementer handoff/reviewer acceptance. Append a
  superseding generation.
- Role env, session IDs, hashes, and Memory anchors are audit controls, not
  non-repudiable identity. Hooks do not cover arbitrary shell redirection or
  external editors; managed runners/OS isolation own that stronger boundary.

## 6. CI carrier pattern (GitLab)

- Guard gate jobs with `rules: exists: [".bugate/scripts/<gate>.py"]` so they
  activate exactly when the kit lands.
- A semantic-gates job should enforce the gate only on UCs whose required
  pre-code artifacts all DECLARE passed — reuse the kit's `precode_passed`
  parser as the single source of truth instead of grepping `gate_status`.
  Pending UCs are the write guard's job, not CI noise; declared-passed UCs
  that fail the contract are honest red (no gate-lowering for green).
- Append `</dev/null` to `check_bugate.py` invocations in CI to avoid stdin
  hangs.

## 7. Live-SUT regression & verdict-flip field notes

Distilled from a full regression round against a shared live SUT after a
dev-claimed fix, ending in a release-verdict flip (blocked → released).

- **Deployment fingerprint is the master gate — judge nothing before it.**
  "MR merged / issue closed ≠ deployed": observed a fix merged at T whose
  container only started at T+2h; the window in between still ran the old
  build. When the app exposes no build info (empty `/actuator/info`), derive
  artifact identity from tag encoding (here the image tag embedded the merge
  commit's short SHA) and cross-check it against the tracker's
  `merge_commit_sha` — two independent sources, one conclusion.
- **Dev-claimed fixes get three-level verification, in order**: (1) live
  probes recording BOTH transport status AND body error code — a correct code
  under the wrong HTTP status is still a fail; (2) the targeted suite for that
  contract; (3) the full regression, with EVERY result delta vs the previous
  round individually attributed (deploy regression / oracle drift / new
  defect). Only all three levels flip the verdict.
- **Collateral FAILs convert to XFAIL, not PASS.** A test that fails in its
  *precondition* because of an unrelated defect never reached its own
  assertion. Fix the root cause and the real assertion runs for the first
  time — often landing on its own issue-bound xfail. Label each FAIL
  root-vs-collateral at triage time and write the post-fix expectation
  accordingly; otherwise correct behavior reads as "fix didn't work", or the
  new XFAIL reads as a new bug.
- **Two directions, two meanings, in xfail-gated suites**: FAIL→PASS is a
  regression guard closing; XFAIL→XPASS is a follow-up capability shipping.
  Report per-test deltas with direction and attribution, never totals alone —
  identical totals can hide churn.
- **Xfail reasons must not assert deployment state.** A canned reason like
  "deployed build has no read-retry" rots silently the day read-retry ships —
  the test still xfails, nobody notices the lie. Bind reasons to tracker
  issues (ownership) instead of deployment claims (state), and re-grep reason
  strings at the end of every deployment-verification round.
- **Environment-restore probes can self-match.** `pgrep -f` / `ps | grep` run
  over ssh matches the remote shell carrying the pattern itself (a phantom
  count of 1). Use the `[b]racket` idiom. Corollary for the credential sweep:
  the handover document is an *expected* hit; your own outputs (logs, docs,
  draft comments) must be zero-hit.
- **Tracker-note discipline**: declare intended tracker writes in the
  deliverable before posting; comment, don't transition state (closure belongs
  to the owner); fix the evidence format (artifact tag + contract table with
  HTTP+code + suite tally deltas); on a failed POST, query for the note before
  retrying — a response parse error does not mean the write failed.
- **Verdict-flip checklist**: update the SSOT document first and fan out;
  recompute every aggregate it feeds (bucket totals, closed/open counts);
  keep history as "was X → now Y" evidence lines instead of narrative; append
  same-day reruns as new sections rather than overwriting the old record; and
  refuse fuzzy verdicts — if residual items don't block, the verdict is
  "released + tracked debt", not "conditionally released".

## 8. Known v0.3.1 kit gaps (found in the field, fix upstream — do not patch vendored copies)

> **Status update:** gaps 1–3 are FIXED in v0.3.2 (layout-aware
> run_full_check.py with graceful `memory`-console degrade; workspace-aware
> wave8-weekly; split Read|Edit|Write matcher for the Wave-7 role guard in
> bugate_init.py), and the doc gaps of item 5 are folded into IMPORT_PROMPT
> (full-check step + Wave 7/8 activation appendix). Item 4 (stale
> `cli_bridge_failures/` counting as degraded) remains open — keep the copy
> hygiene rule from §2. The list below is preserved as found for the record.

1. **`run_full_check.py` cannot run in imported mode**: root detection expects
   `AGENTS.md` + `.shared/` (core layout) and aborts in a governed SUT repo.
2. **`run_full_check.py` invokes a nonexistent `memory` CLI** (`["memory",
   "status"]`); neither the release tarball nor the core checkout ships a
   `bin/memory` — the official self-check crashes even from a core-layout
   root. Both gaps together mean v0.3.1's full-check is unusable; acceptance
   must rely on direct probes (R4 negative control, bridge check-env + real
   dispatch, memory probe write/search).
3. **`bin/wave8-weekly` references pre-split paths** (root `scripts/…` instead
   of the vendored kit layout) and hardcodes a developer-machine path in its
   crontab example — must be rebuilt for imported repos.
4. **Degraded-review check counts stale `cli_bridge_failures/` archives**
   (see §2). Consider scoping the check to files produced by the current run,
   or documenting the copy-hygiene exclusion in IMPORT_PROMPT.
5. Docs gap: IMPORT_PROMPT does not mention the `SDTD_CLI_*` proxy surface,
   the 03b overwrite semantics of `--auto`, or the post-run 04/05 clobber —
   this guide covers them; consider folding the warnings into the prompt.
