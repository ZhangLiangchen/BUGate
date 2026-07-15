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
| Wire the write guard to THIS repo's layout (regex/binding/verification) | this file, below |
| Day-to-day usage after import (working loop, human checkpoints, commands) | `references/using-bugate.md` · 中文 `references/using-bugate.zh-CN.md` |
| Operations & diagnosis (peer-dispatch failures, `--auto` overwrite semantics, post-run SOP, copy hygiene, Wave 7/8 recipes, CI carrier pattern) | `references/field-guide.md` |
| Gate criteria and artifact contracts (what the gates actually judge) | sibling skill `../bugate/` (SKILL.md + references/) |
| One-shot capability self-check | sibling skill `../bugate-full-check/` |
| Machine runtime setup (peer CLIs, memory service, offline fallback) | `<vendor>/docs/SETUP-OPTIONAL.md` (vendored beside the kit) |

## Purpose

Use this skill when wiring BUGate into a SUT test repo whose framework or
layout does not match the scaffold's example — it turns "activate the profile
from evidence" (IMPORT_PROMPT step 5) into a deterministic adaptation
procedure. Validated end-to-end on four framework shapes: Python/pytest,
TypeScript spec files, Java CamelCase test classes, and Gherkin `.feature`
trees.

## What "wiring" means here

The write guard needs one thing from the profile: a way to map **a guarded
test file path** to **one UC artifact directory** whose pre-code artifacts
gate it. Two profile keys express it:

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

## Session/workspace alignment (do this check first)

The physical hooks load from the workspace where the agent session is rooted.
The import target must be the **test-framework home directory**, and later
agent sessions must open **that directory** as their project root. If the
import target is a subdirectory of a larger repo (monorepo), a session opened
at the repo root will NOT load the target's hook wiring and the guard is
silently absent. `bugate_init` warns when target ≠ git toplevel; take the
warning seriously — either open sessions at the target, or export
`BUGATE_PROJECT_ROOT=<target>` in the environment the agent runs under.

## Report contract

After adaptation, report: the regex(es) shipped, the binding mode chosen and
why, the exit codes observed for negative/positive/ambiguity probes, and any
layout limitation accepted (single-dir mode, renaming convention adopted).
Then hand the operator `references/using-bugate.md` (中文:
`references/using-bugate.zh-CN.md`) — the day-to-day working loop starts
there.
