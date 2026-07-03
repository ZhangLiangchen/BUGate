# Imported-mode layout demo — the governed repo is the project root

This directory is a miniature **SUT automation test repo with BUGate imported**
— the default usage mode (CHARTER §2.2). It is the first-class counterpart of
[`examples/mounted-demo/`](../mounted-demo/) (the maintainer-workbench demo of
the same guard).

What makes it "imported mode":

- **The workspace root is this directory**, not the engine repo. The engine
  finds it by walking up from CWD to the nearest committed
  `bugate.config.yaml` (or an explicit `BUGATE_PROJECT_ROOT`).
- **Config and profile are committed here**, beside the tests they guard —
  same repo, same review, same history (rule R2). Nothing about this layout
  relies on a local, uncommitted pointer.
- **Profile paths are repo-relative** (`usecases/{uc}/`, `tests/...`): the
  profile never reaches outside the governed repo.
- **Engine root ≠ workspace root.** Here the engine happens to live two levels
  up (this demo borrows the surrounding checkout); in a real adoption it is
  vendored into the SUT repo (README Quickstart A) or shipped as a plugin. The
  gate resolves templates and sibling scripts from the engine's own location,
  and config/artifacts/guarded paths from the workspace root.

```
examples/imported-demo/          <- governed workspace root (committed marker: bugate.config.yaml)
├── bugate.config.yaml           <- committed: profile pointer
├── bugate.profile.yaml          <- committed: repo-relative template + guarded regex
├── tests/
│   ├── link/test_redirect.py    <- guarded; UC "link"
│   └── new/test_new.py          <- guarded; UC "new"
└── usecases/
    ├── link/  01..03b  (gate_status: passed)   -> edits to tests/link/ ALLOWED
    └── new/   01 only  (gate_status: pending)  -> edits to tests/new/  BLOCKED
```

## Run it

```bash
cd examples/imported-demo   # the committed bugate.config.yaml marks the workspace root

# 1) ALLOWED — the "link" UC's pre-code artifacts are all gate_status: passed
python3 ../../scripts/check_bugate.py tests/link/test_redirect.py </dev/null
echo "exit=$?"   # -> 0

# 2) BLOCKED (negative control, rule R4) — the "new" UC is still pending
python3 ../../scripts/check_bugate.py tests/new/test_new.py </dev/null
echo "exit=$?"   # -> 2, listing the missing/pending artifacts

# 3) BLOCKED (fail-closed) — a UC with no artifact dir at all
python3 ../../scripts/check_bugate.py tests/other/test_x.py </dev/null
echo "exit=$?"   # -> 2
```

The artifacts here are deliberately minimal — enough to drive the guard. For a
filled, semantically complete stack that passes every gate, see
[`examples/demo-sut/`](../demo-sut/).

In a real SUT repo the hook wiring (`.claude/settings.json` / `.codex/hooks.json`
blocks, README Quickstart A) runs the same `check_bugate.py` on every write, so
step 2 is what an agent hits when it tries to jump to implementation before the
pre-code artifacts pass.
