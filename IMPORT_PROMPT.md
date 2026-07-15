# BUGate Import Prompt

[English](IMPORT_PROMPT.md) | [简体中文](IMPORT_PROMPT.zh-CN.md)

> Paste this prompt into Claude Code or Codex while the **SUT automation test
> repo** is open as the project root. The agent should import BUGate as a kit,
> wire Claude Code and Codex symmetrically, initialize the machine-level Memory
> Bus, activate the SUT profile when the test layout is clear, and report the
> remaining human actions.

## Agent Instructions

You are installing BUGate into a SUT automation test repo. BUGate is a
SUT-neutral Agentic QA Governance Kernel. Keep the SUT repo as the project root;
do not clone BUGate core inside the SUT repo, do not mount the SUT inside
BUGate core, and do not put product secrets or environment facts into BUGate
core files.

### Support envelope (read before aborting on a mismatch)

- Verified on macOS; other OSes are unvalidated and adapter-owned.
- Physical gate wiring targets Claude Code + Codex by design.
- `python3 >= 3.9` is the KIT's host runtime — the SUT's test framework can be
  any language (the guard/gates are language-agnostic); do not abort the import
  just because the SUT repo contains no Python.

### Inputs

- Target SUT repo: use the current working directory unless the user gives a
  different path. The target must be the **test-framework home directory**,
  and later agent sessions must open THAT directory as their project root —
  hooks load from the session's workspace, so a session rooted at a parent
  (monorepo) directory silently loads no guard. The importer warns when the
  target is not the git toplevel; relay that warning to the user.
- BUGate version: use `BUGATE_VERSION` if set, otherwise `0.3.4`.
- Vendor dir: use `BUGATE_VENDOR_DIR` if set, otherwise `.bugate`.
- If `BUGATE_ENGINE_DIR` points to an existing BUGate checkout or unpacked
  release, use it. Otherwise download the GitHub Release tarball outside the
  SUT repo.

### Required Flow

1. **Preflight the SUT repo**
   - Run `pwd`, `git status --short --branch`, and `python3 --version`.
   - Confirm Python is >= 3.9.
   - Inspect the repo's test layout with read-only commands such as
     `find . -maxdepth 3 -type d | sort` and targeted `rg --files`.
   - If the current directory is BUGate core itself, stop and ask for the SUT
     automation test repo path.

2. **Acquire the BUGate kit outside the SUT repo**
   - If `BUGATE_ENGINE_DIR` is usable, keep it.
   - Otherwise run the equivalent of:

     ```bash
     BUGATE_VERSION="${BUGATE_VERSION:-0.3.4}"
     BUGATE_TMP="$(mktemp -d)"
     if curl -fL -o "$BUGATE_TMP/bugate-${BUGATE_VERSION}.tar.gz" \
       "https://github.com/ZhangLiangchen/BUGate/releases/download/v${BUGATE_VERSION}/bugate-${BUGATE_VERSION}.tar.gz"; then
       tar -xzf "$BUGATE_TMP/bugate-${BUGATE_VERSION}.tar.gz" -C "$BUGATE_TMP"
     elif curl -fL -o "$BUGATE_TMP/bugate-${BUGATE_VERSION}.zip" \
       "https://github.com/ZhangLiangchen/BUGate/releases/download/v${BUGATE_VERSION}/bugate-${BUGATE_VERSION}.zip"; then
       unzip -q "$BUGATE_TMP/bugate-${BUGATE_VERSION}.zip" -d "$BUGATE_TMP"
     else
       echo "BUGate release v${BUGATE_VERSION} was not downloadable; ask the user for BUGATE_ENGINE_DIR or a valid version." >&2
       exit 2
     fi
     BUGATE_ENGINE_DIR="$BUGATE_TMP/bugate-${BUGATE_VERSION}"
     ```

   - Verify the engine exists:

     ```bash
     test -f "$BUGATE_ENGINE_DIR/scripts/bugate_init.py"
     test -f "$BUGATE_ENGINE_DIR/.shared/skills/bugate/SKILL.md"
     ```

3. **Verify the downloaded engine before installing**

   ```bash
   cd "$BUGATE_ENGINE_DIR"
   python3 -m py_compile scripts/*.py
   python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
   cd -
   ```

4. **Preview and run the importer**

   ```bash
   SUT_REPO="$(pwd)"
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   python3 "$BUGATE_ENGINE_DIR/scripts/bugate_init.py" "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR" --dry-run
   python3 "$BUGATE_ENGINE_DIR/scripts/bugate_init.py" "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR"
   ```

   The importer must vendor the kit, wire `.claude/skills/`, `.agents/skills/`,
   legacy `.codex/skills/`, `.codex/agents/`, `.claude/settings.json`,
   `.codex/hooks.json`, `bugate.config.yaml`, `bugate.profile.yaml`,
   `docs/usecases/`, `.gitignore`, and the machine-level Memory Bus.

5. **Activate the SUT profile only from evidence**
   - Open `bugate.profile.yaml`.
   - Preserve `memory.namespace`.
   - If the test layout is obvious, update `guarded_path_regex` with one or
     more regexes containing a named `(?P<uc>...)` capture.
   - If the layout does not match the scaffold's example (different language,
     naming convention, or per-UC unit), read the vendored adapter skill —
     `$BUGATE_VENDOR_DIR/.shared/skills/bugate-import/SKILL.md` — and follow
     its adaptation procedure (matching rules, worked bindings for four
     framework shapes, and the mandatory negative/positive verification).
   - If the layout is ambiguous, stop and ask the user which test paths BUGate
     should guard.
   - Do not invent product endpoints, credentials, accounts, environment names,
     fixtures, or business facts. Put only SUT test-repo wiring in the profile.

6. **Verify Claude Code and Codex wiring**

   Run from the SUT repo root:

   ```bash
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   python3 -m json.tool .claude/settings.json >/dev/null
   python3 -m json.tool .codex/hooks.json >/dev/null
   test -f "$BUGATE_VENDOR_DIR/scripts/check_bugate.py"
   test -f "$BUGATE_VENDOR_DIR/scripts/bugate_prompt_reminder.py"
   test -f "$BUGATE_VENDOR_DIR/scripts/memory_bus.py"
   test -f "$BUGATE_VENDOR_DIR/.shared/skills/bugate/SKILL.md"
   test -e .claude/skills/bugate/SKILL.md
   test -e .agents/skills/bugate/SKILL.md
   test -e .codex/skills/bugate/SKILL.md
   test -d .codex/agents
   ```

   Then verify the vendored gate scripts:

   ```bash
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   python3 "$BUGATE_VENDOR_DIR/scripts/check_bugate_v13_semantics.py" \
     "$BUGATE_VENDOR_DIR/.shared/skills/bugate/templates" --scope pre-code
   python3 - "$BUGATE_VENDOR_DIR" <<'PY'
   import sys
   from pathlib import Path
   vendor = sys.argv[1] if len(sys.argv) > 1 else ".bugate"
   sys.path.insert(0, f"{vendor}/scripts")
   import bugate_core
   cfg = bugate_core.load_config(root=Path.cwd())
   print("profile=", cfg.get("profile") or cfg.get("active_profile"))
   print("guarded_path_regex=", cfg.get("guarded_path_regex"))
   print("memory.namespace=", cfg.get("namespace") or cfg.get("memory.namespace"))
   PY
   ```

7. **Verify Memory Bus initialization**

   ```bash
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   "$BUGATE_VENDOR_DIR/bin/memory-bus-ensure" || true
   "$BUGATE_VENDOR_DIR/bin/memory-bus-status" --no-fail
   ```

   A slow first-time install is acceptable if the status says it is still
   starting. Report that BUGate is incomplete until the machine-level Memory Bus
   becomes healthy. Do not create a per-repo memory service directory.
   - Online `pip` install is the PREFERRED path. Only when the machine has no
     network: set `BUGATE_MEMORY_NO_INSTALL=1` to skip auto-install and follow
     the manual/offline steps in the vendored `$BUGATE_VENDOR_DIR/docs/SETUP-OPTIONAL.md` §2 —
     this is a fallback, not the recommended route; report the import as
     "governance active, memory pending" until the bus is installed.

8. **Verify the write guard negative control**
   - If `guarded_path_regex` is still empty, report that BUGate is installed but
     physically inert until the profile is activated.
   - If it is active, choose a guarded test path for a UC with no accepted
     pre-code artifacts and run:

     ```bash
     BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
     python3 "$BUGATE_VENDOR_DIR/scripts/check_bugate.py" <guarded-test-path> </dev/null
     ```

   - Expect exit code `2` with a missing-artifact list. If it exits `0`, explain
     why the guard did not apply and fix the profile or path selection.
   - Optional one-shot self-check (v0.3.2+, layout-aware, run from the SUT repo
     root):

     ```bash
     python3 "$BUGATE_VENDOR_DIR/.shared/skills/bugate-full-check/scripts/run_full_check.py" --mode smoke
     ```

     Smoke mode skips real dual-peer model dispatch; `--mode full` dispatches
     codex+claude for real. If this machine's direct egress to the model APIs
     is blocked, pass a proxy through the kit's own injection surface (reaches
     only the spawned peer CLIs, never gate scripts or git):
     `SDTD_CLI_HTTPS_PROXY` / `SDTD_CLI_HTTP_PROXY` / `SDTD_CLI_ALL_PROXY`.

9. **Report final status**
   - List every file or directory changed in the SUT repo.
   - List the exact files that should be committed:
     `bugate.config.yaml`, `bugate.profile.yaml`, `$BUGATE_VENDOR_DIR/`,
     `.claude/settings.json`, `.codex/hooks.json`, `.claude/skills/`,
     `.agents/skills/`, `.codex/skills/`, `.codex/agents/`, `docs/usecases/`,
     and the `.gitignore` BUGate block.
   - State whether `guarded_path_regex` is active.
   - State Memory Bus status.
   - State that Codex requires a one-time re-trust of the changed hook hash in
     Codex Desktop before Codex hooks become active. Claude Code may need a new
     session or plugin reload depending on how the repo is opened.
   - Point the user at the vendored usage guide for day-to-day work:
     `$BUGATE_VENDOR_DIR/.shared/skills/bugate-import/references/using-bugate.md`
     (中文: `using-bugate.zh-CN.md` beside it) — open this repo as the
     session's project root, then drive new requirements through the
     orchestrator working loop (`--auto` → human accepts 03b → guard admits
     test code → post-run closure). ALL post-import guidance is consolidated
     under that one skill.
   - Do not stage, commit, or push unless the user explicitly asks.

### Appendix: activating the optional waves (Wave 7 / Wave 8)

> The importer vendors the field-tested operator manual inside the
> bugate-import skill —
> `$BUGATE_VENDOR_DIR/.shared/skills/bugate-import/references/field-guide.md`
> — read it right after import: the dual-agent dispatch diagnosis ladder and proxy surface, the
> `--auto` 03b overwrite semantics, the post-run 04/05 clobber SOP, UC copy
> hygiene, and the full activation recipes for both waves below live there.

Both waves are dormant by default — configuration switches, not defects.
Enable them in the SUT profile once the evidence is ready (the profile
scaffold now carries commented example blocks).

- **Wave 7 role isolation**: add a top-level `agent_roles:` block to the
  profile (role names lowercase; a bare list forbids both read and write,
  `read:`/`write:` sub-lists scope each side), and set
  `BUGATE_AGENT_ROLE=<role>` at runtime. Example:

  ```yaml
  agent_roles:
    implementer:            # test writers must not see business source/API dumps
      - "^docs/raw/source_code/.*"
    designer:
      write:
        - "^tests/.*"       # designers must not write test code directly
  ```

  Read-isolation only covers tools the hook sees (importer v0.3.2+ wires the
  role guard on its own `Read|Edit|Write` matcher; the write-shaped
  `check_bugate` must NEVER be matched on Read — it does not inspect the
  action and would block reads of guarded tests). Shell-level reads
  (cat/grep) stay review discipline, not physical enforcement.
- **Wave 8 mutation / oracle falsification**: write a falsification spec for
  real captured evidence JSON (declarative oracles + per-field mutations;
  `evidence` paths resolve relative to the spec file's directory), then
  declare in the profile:

  ```yaml
  falsification_spec: <path/to/falsification_spec.yaml>
  falsification_threshold: 0.7
  wave8_evidence_glob: <workspace-relative glob>   # consumed by wave8-weekly
  wave8_reports_dir: <workspace-relative dir>      # prefer a gitignored home
  wave8_artifact_root: <inventory scan root, e.g. docs/usecases>
  ```

  Verify with `python3 $BUGATE_VENDOR_DIR/scripts/oracle_falsification.py
  --gate` (a real score, no longer `profile_required`); schedule with
  `$BUGATE_VENDOR_DIR/bin/wave8-weekly` (layout-aware in v0.3.2+, reports land
  in the workspace). Hold off on the coverage-matrix gate
  (`require_assertion_coverage`) until the spec covers most inventory-referenced
  oracles, or `missing_implementation` noise turns it red.
