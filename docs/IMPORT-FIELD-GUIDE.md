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
- **Peer review overwrites 03b unconditionally** — a curated, human-accepted
  `03b_adversarial_cases.yaml` is re-skeletoned to `gate_status: pending` by
  design ("this repo re-reviews"). The UC drops out of declared-passed and the
  write guard re-locks its tests until a human re-accepts. Only
  `--skip-peer-review` preserves an imported 03b (loudly logged review debt).
  Decide which you want BEFORE running.
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

- The normalized-glob resolver is fail-closed on zero AND ambiguous matches:
  test filenames must normalize (case, `-`/`_`) to exactly one UC directory.
  A mismatched slug blocks with "cannot bind to a UC artifact dir" — that's the
  guard working, not a bug.
- The full lifecycle observed in the field: passed artifacts admit a file →
  `--auto` re-review demotes 03b to pending → guard re-locks the same path →
  human re-accepts → guard re-admits. Expect and plan for this cycle when
  importing UCs plus their tests.
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
