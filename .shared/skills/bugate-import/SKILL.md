# BUGate Import Adapter

## Principle (read first)

**BUGate does not know what the imported test framework looks like — and must
never assume it.** The kit ships mechanisms (gates, guard, orchestrator,
memory) and THIS skill family; all adaptation and wiring decisions are made by
the importing agent (Claude Code / Codex) from two inputs only: the guidance
inside the kit, and the actual shape of the framework it finds in the repo.
When something doesn't fit, the answer is an adaptation decision recorded in
the SUT profile — never a kit patch, never an invented product fact.

## The adapter's map (single entry point — everything lives under this skill)

| You need | Read |
|---|---|
| Install a new import vs update an existing one; external legacy/pre-lock bootstrap; lock+launcher plan/apply/verify/rollback; offline/conflict/profile/session rules | `references/updating-bugate.md` · 中文 `references/updating-bugate.zh-CN.md` |
| Wire the write guard to THIS repo's layout (regex/binding/verification) | this file, below |
| Day-to-day usage after import (three role sessions, human checkpoint, handoff/acceptance, post-run) | `references/using-bugate.md` · 中文 `references/using-bugate.zh-CN.md` |
| Operations & diagnosis (peer dispatch, role drift/recovery, Memory boundaries, hooks/re-trust, copy hygiene, Wave 7/8, CI) | `references/field-guide.md` |
| Gate criteria and artifact contracts (what the gates actually judge) | sibling skill `../bugate/` (SKILL.md + references/) |
| One-shot capability self-check | sibling skill `../bugate-full-check/` |
| Machine runtime setup (peer CLIs, memory service, offline fallback) | `<vendor>/docs/SETUP-OPTIONAL.md` (vendored beside the kit) |

## Install/update routing boundary

- No existing imported installation: use `scripts/bugate_init.py` once.
- Existing exact v0.3.x or pre-lock v0.4.0/v0.4.1 installation: bootstrap
  with `scripts/bugate_update.py` from an unpacked v0.4.2-or-later release;
  retain that verified external release through the rollback window.
- Installation with both its authoritative installed lock and executable
  updater launcher: use the vendored
  `<vendor>/bin/bugate-update` `status` → `plan` → `apply` → `verify` flow;
  use `rollback --transaction <id>` only against its exact current post-image.
- After rollback, use vendored `verify` only if both lock and launcher remain.
  A first updater transaction rolled back to v0.3.x/pre-lock v0.4.0/v0.4.1
  removes them by design; verify that restored image with
  `python3 <unpacked-release>/scripts/bugate_update.py verify . --vendor-dir <vendor>`.
  The same external updater supplies `status`/`verify` if rollback is
  interrupted after the launcher changes.
- Unknown/mixed/local-modified managed state: stop at `NO-GO`. Never rerun the
  importer, patch the vendored kit, or force an overwrite to simulate an
  upgrade.

The engine transaction never edits the SUT profile or activates role
governance. Treat any profile migration as a separate explicit review and
commit. Follow the update report's hook flags: Codex re-trust is conditional on
an actual Codex hook byte change, while any hook change requires a new agent
session before claiming the new enforcement surface is active.

## Purpose

Use this skill when wiring BUGate into a SUT test repo whose framework or
layout does not match the scaffold's example — it turns "activate the profile
from evidence" (IMPORT_PROMPT step 5) into a deterministic adaptation
procedure. Validated end-to-end on four framework shapes: Python/pytest,
TypeScript spec files, Java CamelCase test classes, and Gherkin `.feature`
trees.

## What "wiring" means here

The physical write guard and auditable role guard need one common binding: a
way to map **a guarded test file path** to **one UC artifact directory** whose
pre-code artifacts and `00_role_evidence/` chain gate it. Two profile keys
express the path-to-UC binding:

```yaml
guarded_path_regex:
  - "<regex with a (?P<uc>...) named capture>"
artifact_dir_template: docs/usecases/{uc}/
uc_dir_resolve: normalized-glob     # optional; see matching rules below
```

Without `uc_dir_resolve`, the captured token is substituted literally into
the template and must equal the directory name exactly. With
`uc_dir_resolve: normalized-glob`, both the token and every candidate child
directory of the template's parent are **canonicalized — lowercased with all
`-` and `_` REMOVED** — and exactly one candidate must match.

## Matching rules you must know before writing the regex

- The fold is separator **removal**, not unification. Consequence:
  separator-less tokens bind to separated dir names —
  `UcOrder01Submit` ≡ `uc-order-01-submit` ≡ `UC_Order_01_Submit`
  (all canonicalize to `ucorder01submit`). CamelCase needs no workaround.
- Digit runs must match exactly: `...01...` never binds a dir named `...1...`.
  Pick one zero-padding convention and keep it on both sides.
- Fail-closed on zero AND on ambiguity: no matching dir → blocked; TWO dirs
  folding to the same canon → blocked even with passed artifacts. Never
  create sibling dirs that differ only by separators/case.
- Only immediate children of the template parent are scanned — nested UC
  dirs (`docs/usecases/group/<uc>/`) do not resolve; flatten or point the
  template deeper.
- The regex matches the path string exactly as the runtime payload delivers
  it (argv path or hook JSON). Anchor with `(^|/)` and keep `/` separators.
- Layouts whose captures genuinely cannot fold onto a dir name (dot-separated
  ids like `UC.RT.14`, bare numeric ids) have two honest options: adopt a
  foldable naming convention for either side, or drop the `{uc}` template and
  use the single `artifact_dir:` mode (one artifact set gates all guarded
  paths — coarser, still fail-closed).

## Worked bindings (all field-validated)

| Framework shape | Test path | Regex | Bound dir |
|---|---|---|---|
| pytest | `tests/flows/test_uc_pay_01_checkout.py` | `(^|/)tests/(?:[^/]+/)*test_(?P<uc>uc_[a-z0-9_]+)[.]py$` | `docs/usecases/uc_pay_01_checkout/` |
| TS spec | `tests/e2e/uc-pay-01-checkout.spec.ts` | `(^|/)tests/e2e/(?P<uc>uc-[^/]+)[.]spec[.]ts$` | `docs/usecases/uc-pay-01-checkout/` |
| Java CamelCase | `src/test/java/com/x/blackbox/UcOrder01SubmitTest.java` | `(^|/)src/test/java/com/x/blackbox/(?P<uc>Uc[A-Za-z0-9]+)Test[.]java$` | `docs/usecases/uc-order-01-submit/` |
| Gherkin | `features/uc_login_01_password.feature` | `(^|/)features/(?P<uc>uc_[a-z0-9_]+)[.]feature$` | `docs/usecases/uc_login_01_password/` |

All four need `uc_dir_resolve: normalized-glob` unless the capture equals the
dir name byte-for-byte.

## Adaptation procedure

1. **Inventory the test tree** (read-only): where do black-box tests live,
   what is the per-UC unit (one file? one dir? one feature?), what naming
   token identifies the UC inside the path.
2. **Draft the regex**: one pattern per distinct layout, each with a single
   `(?P<uc>...)` capture over that token. Prefer anchoring to the tree root
   (`(^|/)tests/...`) so unrelated files never match; over-matching is safe
   (fail-closed) but noisy.
3. **Choose the binding mode**: captures that equal dir names → literal
   template; anything else foldable → add `uc_dir_resolve: normalized-glob`;
   unfoldable → single `artifact_dir:` mode and say so in the report.
4. **Verify — non-negotiable, both invocation forms**:
   ```bash
   # negative control (no artifacts yet): expect exit 2
   python3 "$VENDOR/scripts/check_bugate.py" <a-guarded-test-path> </dev/null
   # hook-shaped payload: expect exit 2
   printf '{"tool_name":"Edit","tool_input":{"file_path":"<same-path>"}}' \
     | python3 "$VENDOR/scripts/check_bugate.py"
   ```
   Two distinct exit-2 messages mean different things: a *missing-artifact
   list* = binding worked, gate holds; *"cannot bind to a UC artifact dir"* =
   your regex/dir naming is wrong — fix the binding, do not "fix" the guard.
5. **Positive control**: create one UC dir from the kit templates, set all
   five pre-code artifacts to `gate_status: passed`, re-run both forms —
   expect exit 0 for that UC and exit 2 for every other UC (per-UC isolation).
6. **Ambiguity probe** (once): temporarily add a second dir folding to the
   same canon, confirm exit 2, remove it.
7. **Choose lifecycle mode explicitly.** Keep `role_governance.mode: off` for
   exact v0.3.x compatibility, or replace it with the canonical `required`
   block from `../bugate/references/profile-schema.md`. Do not keep duplicate
   active blocks. `agent_roles` remains a separate deny-path policy.
8. **If required, verify identity controls before claiming activation:** start
   fresh designer/implementer/reviewer processes with `bin/bugate-role run`,
   confirm unset/wrong role is rejected, and run `check_role_evidence.py` in
   both Claude/Codex payload shapes. For an existing import, update the engine
   only through `references/updating-bugate.md`; `bugate_init.py` remains
   first-install-only. An updater-reported hook change requires a new session,
   and an actual Codex hook hash change additionally requires operator
   re-trust before runtime activation may be claimed.

## Session/workspace alignment (do this check first)

The physical hooks load from the workspace where the agent session is rooted.
The import target must be the **test-framework home directory**, and later
agent sessions must open **that directory** as their project root. If the
import target is a subdirectory of a larger repo (monorepo), a session opened
at the repo root will NOT load the target's hook wiring and the guard is
silently absent. `bugate_init` warns when target ≠ git toplevel; take the
warning seriously — either open sessions at the target, or export
`BUGATE_PROJECT_ROOT=<target>` in the environment the agent runs under.

Role identity has the same process boundary. SessionStart can report role,
session, available phase, and chain state, but a hook child cannot export
`BUGATE_AGENT_ROLE` or `BUGATE_SESSION_ID` into the parent agent. Launch each
role through `bin/bugate-role run --role ... -- <command>` (or launch Desktop
from an equivalent environment) and open a new session. Shell redirection and
external editors are outside hook interception; managed filesystem isolation
is required for that stronger boundary.

## Report contract

After adaptation, report: the regex(es) shipped, the binding mode chosen and
why, the lifecycle mode, the exit codes observed for negative/positive/
ambiguity and unset/wrong-role probes, Codex re-trust state, and any layout
limitation accepted (single-dir mode, renaming convention adopted).
Then hand the operator `references/using-bugate.md` (中文:
`references/using-bugate.zh-CN.md`) — the day-to-day working loop starts
there. For any later engine change, hand them `references/updating-bugate.md`
(中文: `references/updating-bugate.zh-CN.md`) and keep profile activation out
of the engine-update transaction.
