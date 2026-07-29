---
name: bugate-update
description: "Safely plan, apply, verify, recover, or roll back an existing imported BUGate engine update. Use when a user asks to update BUGate, upgrade BUGate, change the BUGate version, refresh an existing imported installation, run bugate-update, or says 升级 BUGate 版本 or 升级BUGate版本. Do not use for a fresh import."
---

# BUGate Update

Upgrade only the BUGate-owned engine projection of an existing imported SUT
test workspace. Keep the SUT profile, tests, use-case artifacts, role evidence,
Memory namespace/data, machine lineage registry, and unrelated dirty files
outside the engine transaction.

## Required context

1. Treat the directory containing `bugate.config.yaml` as the imported
   workspace root. Read its `AGENTS.md`/`CLAUDE.md` before acting.
2. Read exactly one operator guide completely:
   - English: `../bugate-import/references/updating-bugate.md`
   - 中文: `../bugate-import/references/updating-bugate.zh-CN.md`
3. Use `../../../UPDATE_PROMPT.md` or
   `../../../UPDATE_PROMPT.zh-CN.md` when the operator needs a portable prompt.
4. Keep downloaded releases, checksum assets, saved plans, backups, and probe
   output outside the imported repository, preferably under `/tmp`.
5. Never open, clone, copy, mount, or infer another SUT while updating the
   active imported workspace.

## Authority boundary

Default to `PLAN_ONLY`: run read-only discovery, `status`, and `plan`, report
the exact result, and stop before mutation.

Enter `APPLY_APPROVED` only when the user explicitly authorizes `apply` for the
exact target version and source, or the active request already provides equally
specific authority. A generic request to inspect, assess, check, or plan an
upgrade is not apply authority.

Profile migration, per-UC lineage `init`/`adopt`/`recover`, rollback,
commit, push, tag, Release publication, and real SUT changes are separate
authorities. Never infer them from engine-update approval.

## Workflow

### 1. Inspect without writes

- Run `pwd`, `git status --short --branch`, and inspect the current repository
  rules.
- Resolve the vendor directory from the installed layout; default to
  `.bugate` only when the workspace uses that standard path.
- Require an explicit target version. Never resolve an implicit `latest`.
- Preserve all dirty files. Do not reset, checkout, clean, revert, or overwrite
  them.

Classify by physical evidence, not a version label:

```sh
if test -f "$BUGATE_VENDOR_DIR/bugate.lock.json" \
  && test -x "$BUGATE_VENDOR_DIR/bin/bugate-update"; then
  BUGATE_ROUTE=locked-in-repo-update
else
  BUGATE_ROUTE=external-bootstrap-candidate
fi
```

- `locked-in-repo-update`: use the vendored launcher.
- `external-bootstrap-candidate`: an exact supported v0.3.x or pre-lock
  v0.4.0/v0.4.1 installation uses `scripts/bugate_update.py` from an
  independently verified, unpacked v0.4.2-or-later target release.
- Unknown, mixed, partially installed, or locally modified managed state:
  report `NO-GO`. Do not rerun `bugate_init.py` or invent a repair.

### 2. Build the exact plan

For a locked installation:

```sh
"$BUGATE_VENDOR_DIR/bin/bugate-update" status
"$BUGATE_VENDOR_DIR/bin/bugate-update" plan \
  --to "$BUGATE_TARGET_VERSION" --json
```

For an external bootstrap:

```sh
python3 "$BOOTSTRAP" status "$SUT_REPO" \
  --vendor-dir "$BUGATE_VENDOR_DIR"
python3 "$BOOTSTRAP" plan "$SUT_REPO" \
  --vendor-dir "$BUGATE_VENDOR_DIR" \
  --to "$BUGATE_TARGET_VERSION" --json
```

Prefer deterministic archive mode by passing the same `--archive` and
`--checksums` pair to both plan and apply. Keep a saved JSON plan outside the
repository. `plan` and `apply --dry-run` must make zero persistent target
writes.

Review and report:

- from/to version, source kind, release/manifest/archive digest;
- every add/update/delete/conflict/local modification and mode change;
- updater recovery state and transaction-history capacity;
- profile `migration_required` versus non-blocking `migration_available`;
- Codex hook hash change, re-trust, and new-session requirements;
- rollback availability and exact `NO-GO` reasons.

Stop unless the complete plan says `Decision: GO`.

### 3. Apply only the approved plan

Reconfirm the worktree and source inputs immediately before applying. Use the
same exact target, archive/checksum pair, and saved plan that the user approved.
Do not use a broad `--force`; BUGate intentionally provides none.

For a locked installation:

```sh
"$BUGATE_VENDOR_DIR/bin/bugate-update" apply \
  --to "$BUGATE_TARGET_VERSION" --plan "$BUGATE_PLAN"
"$BUGATE_VENDOR_DIR/bin/bugate-update" verify
```

For an external bootstrap, pass the workspace and vendor directory to both
commands. Retain the verified external updater through the rollback window.

### 4. Verify before claiming completion

1. Require updater `verify` to report `decision: GO`, a lock-based installation,
   no drift, and `recovery_required: false`.
2. Run the vendored imported smoke check:

   ```sh
   python3 "$BUGATE_VENDOR_DIR/.shared/skills/bugate-full-check/scripts/run_full_check.py" \
     --mode smoke
   ```

3. Inspect `git status` and `git diff`; prove SUT-owned and unrelated dirty
   files stayed byte-identical.
4. If hooks changed, require a new agent session. Require Codex re-trust only
   when the Codex hook bytes/hash changed.
5. Report engine verification separately from profile and role-lineage state.
   Updater success never means lineage migration was accepted.
6. Do not commit or push unless the user separately authorized those actions.

### 5. Handle interruption or rollback

Do not delete or edit updater journals, sentinels, plan locks, history, or the
installed lock. A mutating retry or exact rollback performs journal-driven
recovery under the workspace lock.

Rollback requires separately authorized use of the exact committed transaction
ID. If rollback removes the installed lock and launcher, verify through the
retained external updater:

```sh
python3 "$BOOTSTRAP" verify . --vendor-dir "$BUGATE_VENDOR_DIR"
```

Never recreate the launcher manually.

## Completion report

Report the exact target/source, plan and apply decisions, transaction ID,
verification and smoke exit codes, managed diff, preserved SUT-owned surfaces,
hook/session actions, rollback route, and separate profile/lineage status.
Call the update complete only when verification is green and every authorized
runtime activation step is finished.
