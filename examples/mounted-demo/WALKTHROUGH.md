# Mounted-SUT walkthrough (maintainer workbench): watch the write-guard block, then allow

> This demo exercises the **core-workbench layout** — the engine repo is the
> workspace root and the SUT tree hangs beneath it (maintainer mode, CHARTER
> §2.3). The first-class counterpart for the default **imported mode** is
> [`examples/imported-demo/`](../imported-demo/), where the governed repo is
> the workspace root and config + profile are committed.

This is the piece `examples/demo-sut/` can't show on its own: a **runnable** mount
where you see the physical write-guard (`scripts/check_bugate.py`) **intercept** an
edit to a use case's tests until that use case's pre-code artifacts are accepted.

It is fully self-contained (no real SUT needed). `demo.profile.yaml` binds a tiny
fake test tree to per-UC artifact dirs:

```
examples/mounted-demo/
├── demo.profile.yaml          # artifact_dir_template + (?P<uc>...) guarded regex
├── tests/
│   ├── link/test_redirect.py  # guarded; UC "link"
│   └── new/test_new.py         # guarded; UC "new"
└── usecases/
    ├── link/  01..03b  (gate_status: passed)   → edits to tests/link/ ALLOWED
    └── new/   01 only  (gate_status: pending)  → edits to tests/new/  BLOCKED
```

## Run it

```bash
# from the repo root
export BUGATE_PROFILE=examples/mounted-demo/demo.profile.yaml

# 1) ALLOWED — the "link" UC's pre-code artifacts are all gate_status: passed
python3 scripts/check_bugate.py examples/mounted-demo/tests/link/test_redirect.py </dev/null
echo "exit=$?"   # -> 0  (edit permitted)

# 2) BLOCKED — the "new" UC is still pending (02/03/03a/03b absent)
python3 scripts/check_bugate.py examples/mounted-demo/tests/new/test_new.py </dev/null
echo "exit=$?"   # -> 2  (edit refused, with the exact missing/pending artifacts listed)

# 3) BLOCKED (fail-closed) — a UC with no artifact dir at all
python3 scripts/check_bugate.py examples/mounted-demo/tests/other/test_x.py </dev/null
echo "exit=$?"   # -> 2

# 4) Core mode (no profile) disables the guard entirely
unset BUGATE_PROFILE
python3 scripts/check_bugate.py examples/mounted-demo/tests/new/test_new.py </dev/null
echo "exit=$?"   # -> 0
```

To flip `new` from BLOCKED to ALLOWED, give it the same five `gate_status: passed`
pre-code artifacts that `usecases/link/` has (in real use you'd fill and accept
them — see `examples/demo-sut/` for what a real, semantically-validated stack
looks like).

## Two things to know

- **`check_bugate.py` reads its payload from stdin** (it's a PreToolUse hook). When
  you run it by hand, pass the path as an argument and redirect stdin with
  `</dev/null` (as above) so it does not block waiting for input. Under a real
  runtime, the hook feeds the tool-call JSON on stdin and you pass no argument.
- **Per-UC fail-closed binding**: each guarded test path resolves to its *own*
  artifact dir via the `{uc}` template + `(?P<uc>...)` capture, so one use case's
  passed artifacts can never unlock a different use case's tests. A guarded path
  that captures no `uc` (a pattern without the group) is blocked, not allowed.

## How to point this at YOUR SUT

Copy `demo.profile.yaml`, then change `artifact_dir_template` and
`guarded_path_regex` to your repo's real test layout (keep the `(?P<uc>...)`
capture), and put each use case's accepted 01–05 artifacts under your artifact
dir. Wire `scripts/check_bugate.py` into your runtime's PreToolUse hook (see
`.codex/hooks.json` / `.claude/settings.json` for the shipped wiring).
