# BUGate Update Prompt

[English](UPDATE_PROMPT.md) | [简体中文](UPDATE_PROMPT.zh-CN.md)

> Paste this prompt into Codex or Claude Code with the existing imported SUT
> automation test repository open as the project root. It upgrades the
> BUGate-owned engine projection; it does not migrate or modify the SUT.

## Agent instructions

Use the `bugate-update` skill to update this existing imported BUGate
installation. Read the repository's `AGENTS.md`/`CLAUDE.md` and the vendored
`bugate-update` skill before acting.

### Inputs and authority

- `SUT_REPO`: the current imported automation test workspace containing
  `bugate.config.yaml`.
- `BUGATE_VENDOR_DIR`: use the installed vendor directory; default to
  `.bugate` only when that path is evidenced.
- `BUGATE_TARGET_VERSION`: require an exact semantic version from the user.
  There is no implicit `latest`.
- `BUGATE_SOURCE`: either a trusted remote release or an explicit archive plus
  its matching `SHA256SUMS`, both kept outside the repository.
- `AUTHORIZATION_SCOPE`: default to `PLAN_ONLY`.

`PLAN_ONLY` authorizes read-only discovery, updater `status`, updater `plan`,
and reporting. It does not authorize `apply`, rollback, profile migration,
lineage actions, commit, push, tag, Release publication, or edits to real SUT
surfaces.

Enter `APPLY_APPROVED` only after explicit approval for the exact target,
source, and reviewed `Decision: GO` plan. If the active request already gives
that exact authority, state how it does so before applying. Do not infer apply
authority from a request to inspect, assess, or plan an upgrade.

### Required workflow

1. **Inspect without mutation**

   ```sh
   cd "$SUT_REPO"
   pwd
   git status --short --branch
   ```

   Report every dirty item. Preserve it byte-for-byte. Do not reset, checkout,
   clean, revert, overwrite, stage, commit, or push.

2. **Classify the installation by lock and launcher**

   ```sh
   if test -f "$BUGATE_VENDOR_DIR/bugate.lock.json" \
     && test -x "$BUGATE_VENDOR_DIR/bin/bugate-update"; then
     BUGATE_ROUTE=locked-in-repo-update
   else
     BUGATE_ROUTE=external-bootstrap-candidate
   fi
   ```

   - For `locked-in-repo-update`, use the vendored launcher.
   - For `external-bootstrap-candidate`, an exact supported v0.3.x or pre-lock
     v0.4.0/v0.4.1 installation uses `scripts/bugate_update.py` from an
     independently checksum-verified and unpacked v0.4.2-or-later target
     release outside the SUT repository.
   - Unknown, mixed, partial, or locally modified managed state is `NO-GO`.
     Do not run `bugate_init.py`; it is fresh-install-only.

3. **Run status and plan**

   Locked route:

   ```sh
   "$BUGATE_VENDOR_DIR/bin/bugate-update" status
   "$BUGATE_VENDOR_DIR/bin/bugate-update" plan \
     --to "$BUGATE_TARGET_VERSION" --json
   ```

   External bootstrap route:

   ```sh
   python3 "$BOOTSTRAP" status "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR"
   python3 "$BOOTSTRAP" plan "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR" \
     --to "$BUGATE_TARGET_VERSION" --json
   ```

   In archive mode, pass the same `--archive` and `--checksums` pair to plan
   and apply. Save the JSON plan outside the repository. `plan` and
   `apply --dry-run` must make zero persistent target writes.

4. **Report and stop at the authority gate**

   Report:

   - from/to version, source kind, release/manifest/archive digests;
   - all managed additions, updates, safe deletes, conflicts, local
     modifications, type and permission changes;
   - recovery state and transaction-history capacity;
   - profile `migration_required`/`migration_available`;
   - hook hash, Codex re-trust, and new-session impact;
   - exact rollback path and every `NO-GO` reason.

   Stop unless the plan says `Decision: GO`. Under `PLAN_ONLY`, stop even when
   it is GO and request explicit approval.

5. **Apply only under `APPLY_APPROVED`**

   Recheck repository status and rebuild/validate the plan immediately before
   mutation. Use the same exact target, source, archive/checksum pair, and saved
   plan. There is no broad `--force`.

   ```sh
   "$BUGATE_VENDOR_DIR/bin/bugate-update" apply \
     --to "$BUGATE_TARGET_VERSION" --plan "$BUGATE_PLAN"
   "$BUGATE_VENDOR_DIR/bin/bugate-update" verify
   ```

   Use the corresponding external-bootstrap command form when the vendored
   launcher is not authoritative.

6. **Verify**

   Require updater `verify` to be GO, lock-based, drift-free, and not awaiting
   recovery. Then run:

   ```sh
   python3 "$BUGATE_VENDOR_DIR/.shared/skills/bugate-full-check/scripts/run_full_check.py" \
     --mode smoke
   git status --short
   git diff
   ```

   Prove profile/config, SUT tests, use-case artifacts,
   `00_role_evidence/**`, Memory namespace/data, machine lineage registry,
   SUT-owned hooks/skills, and unrelated dirty files were not modified.

7. **Keep later authorities separate**

   - Report profile compatibility, but do not edit the profile without separate
     approval.
   - Report each governed UC's lineage status after the engine update, but do
     not run `lineage-init`, `lineage-adopt`, or `recover` without separate
     approval.
   - Do not commit and do not push unless separately authorized.
   - A hook change requires a new agent session; Codex re-trust is required only
     when Codex hook bytes/hash changed.

8. **Rollback and interruption**

   Do not delete or hand-edit updater state. Rollback requires separate approval
   and the exact committed transaction ID. If rollback removes the vendored
   launcher and lock, verify with the retained external updater:

   ```sh
   python3 "$BOOTSTRAP" verify . --vendor-dir "$BUGATE_VENDOR_DIR"
   ```

### Completion report

Report the route, target/source, plan decision, authorization used, transaction
ID, verify and smoke results, exact managed diff, preserved SUT-owned surfaces,
hook/session actions, rollback route, and profile/lineage status as separate
outcomes. Never describe engine-update success as governance migration success.
