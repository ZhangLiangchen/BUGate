# Updating BUGate in an Imported Repository

[English](updating-bugate.md) | [简体中文](updating-bugate.zh-CN.md)

This is the operator runbook for an existing imported BUGate installation.
It applies to the first-class updater introduced in BUGate v0.4.2 and to later
compatible releases. It is intentionally separate from first installation:
`scripts/bugate_init.py` creates a new imported installation; it is not an
upgrade, re-import, or vendor-refresh command.

The normative ownership and transaction rules live in
`docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.md` in the release source. This
vendored guide is the self-contained operating path.

## 1. Route by the state you actually have

Run commands from the imported SUT test repository root: the directory that
contains `bugate.config.yaml`.

| Observed state | Correct route |
|---|---|
| No BUGate installation | Run `scripts/bugate_init.py <repo>` once from a trusted release. |
| Exact supported v0.3.x layout, no updater/installed lock | Run the updater from an unpacked v0.4.2-or-later release; see §2. |
| Exact pre-lock v0.4.0 or v0.4.1 layout | Use the same unpacked bootstrap route; see §2. |
| Installed lock and `.bugate/bin/bugate-update` present | Use the vendored `status` / `plan` / `apply` / `verify` / `rollback` flow; see §3. |
| Unknown/mixed layout, non-standard hook, or locally modified managed file | Stop at `NO-GO`; reconcile the named conflict. Do not rerun init or force an overwrite. |

Supported formal v0.3 tags are v0.3.0, v0.3.1, v0.3.2, v0.3.4, and v0.3.5.
There was no v0.3.3 release. Recognition is based on release-generated file,
mode, layout, and hook evidence—not a version string or a “close enough” match.

Before planning:

1. Finish or stop active agent work and preserve SUT-owned changes. A clean
   commit or independent backup is recommended. Unrelated dirty files are only
   warnings, but drift on an updater-managed path is a blocking conflict.
2. Choose an explicit target version. Remote mode never resolves `latest`.
3. Obtain the release archive and `SHA256SUMS` asset from a trusted channel if
   using archive/offline mode. Keep them outside the imported repository.
4. Keep a verified unpacked v0.4.2-or-later release outside the imported repo
   until the update and any intended rollback window are closed. Define its
   updater path, for example
   `BOOTSTRAP=<unpacked-release>/scripts/bugate_update.py`. A first updater
   transaction can restore a pre-updater projection, so the vendored launcher
   is not guaranteed to survive rollback.
5. If managed/shared files rely on ACLs, extended attributes, ownership,
   hardlink identity, or timestamps, back those up separately. The v1 journal
   restores logical bytes/type/mode/symlink targets, not that inode metadata.

## 2. One-time bootstrap from v0.3.x or a pre-lock v0.4.x install

The old installation has no trustworthy updater launcher, so execute the
updater from an unpacked target release kept **outside** the imported repo:

```sh
cd <imported-sut-test-repo>
python3 <unpacked-release>/scripts/bugate_update.py status . \
  --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py plan . \
  --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py apply . \
  --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py verify . \
  --vendor-dir .bugate
```

`plan` is zero-write. On an exact recognized baseline, `apply` may adopt that
official pre-lock layout and create its first installed lock. Detection failure,
a missing critical file, a mixed fingerprint, non-standard hook wiring, or a
local managed modification is `NO-GO` and must remain so until the baseline is
reconciled.

The unpacked-only form verifies the canonical release manifest and every mapped
payload, but extracted bytes cannot prove the digest of the original archive.
The installed lock therefore records `archive_sha256: null` and
`unavailable-from-unpacked-source`. Verify the archive checksum **before**
extracting it and retain that provenance separately.

When the original archive and checksum asset are available, prefer archive
mode even though the bootstrap program itself is launched from the unpacked
release. Repeat the same source arguments for plan and apply:

```sh
python3 <unpacked-release>/scripts/bugate_update.py plan . \
  --vendor-dir .bugate --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
python3 <unpacked-release>/scripts/bugate_update.py apply . \
  --vendor-dir .bugate --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
```

This verifies the raw archive and extracts the update input into temporary
storage outside the repository before any target write.

## 3. Routine updates after the updater is installed

Use this route only when both the authoritative installed lock and executable
launcher exist (normally `.bugate/bugate.lock.json` and
`.bugate/bin/bugate-update`). A version label alone does not select this route.
An exact pre-lock v0.4.0/v0.4.1 layout, or a legacy/pre-lock image restored by
rollback, still uses the external bootstrap in §2.

### Remote release

```sh
cd <imported-sut-test-repo>
.bugate/bin/bugate-update status
.bugate/bin/bugate-update plan --to <version>
.bugate/bin/bugate-update plan --to <version> --json \
  > /path/outside/repo/bugate-update-plan.json
.bugate/bin/bugate-update apply --to <version> \
  --plan /path/outside/repo/bugate-update-plan.json
.bugate/bin/bugate-update verify
```

The saved plan is optional but recommended. `apply --plan` rebuilds the plan,
rehashes every base item, and rejects drift or different inputs. A direct
`apply` performs the same fresh in-memory planning. `apply --dry-run` has the
same zero-persistent-write boundary as `plan`.

### Deterministic offline archive

```sh
.bugate/bin/bugate-update plan --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
.bugate/bin/bugate-update apply --to <version> \
  --archive /path/to/bugate-<version>.tar.gz \
  --checksums /path/to/bugate-<version>.SHA256SUMS
.bugate/bin/bugate-update verify
```

`--archive` and `--checksums` are an inseparable pair. Supplying `--to` is
recommended because the CLI version, archive/checksum names, release manifest,
and plugin metadata must agree. Tar and zip are both supported when the release
publishes the matching checksum record.

## 4. Read the plan before authorizing a write

Do not run `apply` unless the final decision is `GO`. Review at least:

- `from_version`, `to_version`, release/manifest digest, and source kind;
- every managed item classification: `unchanged`, `add`, `update`, safe
  `delete`, `locally_modified`, `conflict`, `type_changed`, or
  `permission_changed`;
- every `hook_refresh`, plus `codex_hook_hash_changed` and
  `new_session_required`;
- profile status: `migration_available` is non-blocking and separate;
  `migration_required` is blocking;
- rollback availability, warnings, and every explicit `NO-GO` reason.

The updater owns only the release-manifest projection. It does not write,
delete, stage, commit, or format SUT tests, use-case artifacts,
`00_role_evidence/**`, profiles/config, Memory data, SUT-owned hooks/skills,
operating rules, or product/environment material. It also never creates or
edits the machine `role-lineage.sqlite3` registry under the effective Memory
home (`MCP_MEMORY_BASE_DIR` → `BUGATE_MEMORY_HOME` →
`~/.bugate/memory-bus`), deterministic Memory roots/checkpoints, or per-UC
lineage transactions. Unknown files inside a managed directory are not
recursively deleted.

### Conflict and adoption behavior

- A current managed item matching the old manifest may update; one already
  matching the target is unchanged; a third hash is `locally_modified` and
  `NO-GO`. A stale known file is deleted only while it still matches its old
  manifest image.
- Hook IDs alone never prove ownership. The complete event, matcher, ordered
  commands, and semantic digest must match an installed or shipped historical
  contract. Mixed, partial, duplicated, or spoof-shaped entries are conflicts.
- There is no broad `--force`, and the current CLI does not offer a general
  arbitrary-local-change adoption command. Exact official pre-lock adoption is
  automatic only during a reviewed `apply`. Move intentional customization to
  a SUT-owned wrapper/profile or reconcile the named path to an official
  baseline, then rerun `plan`.
- A future local-change adoption surface, if introduced, must be explicit and
  per path, record the observed hash/operator decision, and still must not
  widen BUGate ownership or conceal a conflict.

## 5. Apply, verify, review, and commit

`apply` acquires the workspace lock, stages and verifies the target outside the
vendor tree, snapshots changing managed/shared items, atomically installs only
the planned projection, verifies the post-image, writes the installed lock,
and reports a transaction ID. Preserve that ID for rollback.

After a successful apply:

1. Run `.bugate/bin/bugate-update verify`; it reports drift and never repairs it.
2. Run the imported smoke check:
   `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke`.
3. Re-run the profile-specific write-guard negative control and the relevant
   role-governance checks before claiming the updated gate is active.
4. Review `git diff`/status. Confirm SUT-owned hooks and unrelated dirty files
   were preserved; the updater never stages or commits.
5. Commit the complete BUGate-owned installed projection (including lock and
   hook changes) as one reviewable change while leaving the existing profile
   behavior unchanged.

Hook activation has a process boundary:

- Re-trust Codex Desktop only when the plan/apply/rollback report says the
  `.codex/hooks.json` hash actually changed. Do not demand re-trust for a
  byte-identical hook file.
- Any hook change requires a **new agent session** before that runtime can use
  the new hook surface. Close/reopen the affected Claude/Codex session after
  apply or rollback. Until required re-trust and the new-session boundary are
  complete, report file/update verification only—not active runtime
  enforcement.

## 6. Rollback, recovery, and the history limit

Rollback one committed transaction by its reported 32-hex ID:

```sh
.bugate/bin/bugate-update rollback --transaction <transaction-id>
```

Rollback is itself locked, journaled, atomic, and crash-recoverable. It first
requires the current installed projection, hooks, manifest, and lock to equal
that transaction's recorded post-image. A later update or local drift makes an
old transaction stale and rollback `NO-GO`; it will not overwrite newer state.
Review its hook flags and repeat conditional re-trust/new-session handling.

Verify through whichever entry point exists **after** rollback. A rollback of
the first v0.4.2 updater transaction to v0.3.x or pre-lock v0.4.0/v0.4.1
restores that exact pre-updater projection, including removal of the installed
lock and `.bugate/bin/bugate-update`. Do not recreate or copy the launcher:

```sh
if test -f .bugate/bugate.lock.json && test -x .bugate/bin/bugate-update; then
  .bugate/bin/bugate-update verify
else
  python3 "$BOOTSTRAP" verify . --vendor-dir .bugate
fi
```

`$BOOTSTRAP` must name the retained, verified updater from an unpacked
v0.4.2-or-later release outside the imported repo. The external `verify` can
recognize and verify an exact supported legacy/pre-lock image without writing
a new lock or reinstalling the launcher.

After an interrupted write, read-only `status`, `plan`, and `verify` report
`recovery_required` without changing the repository. The next real `apply`, or
an explicit `rollback`, performs journal-driven recovery under the workspace
lock. Never delete, rename, or hand-edit `.bugate-update/`,
`.bugate/plan.lock/bugate-update/`, journals, sentinels, or installed locks to
“unstick” an update.

An interrupted rollback may already have removed or replaced the vendored
launcher. In that case use the retained external bootstrap for read-only
diagnosis and verification, and use it to retry the exact reviewed rollback if
recovery is required:

```sh
python3 "$BOOTSTRAP" status . --vendor-dir .bugate
python3 "$BOOTSTRAP" rollback . --vendor-dir .bugate \
  --transaction <transaction-id>
python3 "$BOOTSTRAP" verify . --vendor-dir .bugate
```

The v1 implementation deliberately validates at most **128 transaction-history
entries** while pinning descriptors against path-exchange attacks. At 128 it
refuses any operation that would create another transaction before target
writes; a store with more than 128 entries is invalid and fail-closed. There is
no public prune command in this updater version. Do not delete history by hand:
preserve the state and reports, stop, and use an explicitly reviewed archival/
migration procedure from a later compatible release or escalate to BUGate
maintainers.

## 7. Profile and role-lineage migration are separate actions

Engine update and governance activation are not one transaction. The updater
may report profile compatibility, but it never edits `bugate.config.yaml`, a
profile, `memory.namespace`, human acceptance, role receipts, or
`00_role_evidence/**`. It also never runs `lineage-init`, `lineage-adopt`, or
`recover`, and never writes the machine lineage registry or Memory home.

- If the plan reports blocking `migration_required`, make and validate the
  minimum profile-schema compatibility correction as its own reviewed change,
  then rerun `plan`. Do not hide it inside an engine apply.
- If it reports non-blocking `migration_available`, first apply/verify/commit
  the engine with the legacy/off profile unchanged. Only then review a separate
  profile diff—such as deliberately enabling `role_governance.mode: required`—
  validate its negative controls and Memory boundary, and commit it separately.
- No command in this updater version should be assumed to perform profile
  migration. Follow the profile schema reference and require an explicit human
  decision. Never fabricate acceptance, handoff, or receipt evidence.

The two-commit boundary makes the engine update and governance adoption
independently auditable and reversible. Engine rollback does not roll back a
separate profile commit.

### Distinguish the two `migration_required` states

- Updater **profile** `migration_required` appears in `bugate-update plan` and
  means the profile schema is incompatible with the target engine. It blocks
  engine apply until the separately reviewed profile correction is complete.
- Role-lineage **integrity** `migration_required` appears in
  `bugate-role lineage-status` after a lineage-capable engine/profile is in
  scope. It means local history exists without a registry row, or a strict
  deterministic root proves prior lineage existence. Adoption then performs
  the separate full non-empty-chain verification. It is not an updater conflict
  and updater success cannot clear or accept it.

After the engine projection is applied, verified, reviewed, and committed—and
after any separately approved profile action makes required role governance
applicable—classify every governed UC independently:

```sh
.bugate/bin/bugate-role lineage-status docs/usecases/<UC> --json

# Genuine first use only; copy the exact ID from lineage-status.
.bugate/bin/bugate-role lineage-init docs/usecases/<UC> \
  --lineage-id <exact-lineage-id>

# Verified non-empty pre-v0.4.3 chain; zero receipt rewrites.
.bugate/bin/bugate-role lineage-adopt docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-chain-head>

# Registered missing/diverged history or an active publication/recovery transaction.
.bugate/bin/bugate-role recover docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-head-or-EMPTY> \
  [--archive <trusted-recovery-archive>]
```

An initial non-zero `uninitialized` result is expected and read-only; confirm
true first use before init. `aligned` needs no migration. A non-empty verified
legacy chain uses adoption. A strict root with no trustworthy local history
must remain blocked until pre-loss evidence is restored; do not initialize over
it. Initialization writes its durable intent before any Memory request and
advances `pending` -> `root_absence_verified` -> `root_verified` ->
`registry_initialized` -> `chain_written` -> `completed`. If JSON status shows
`recovery_pending` with `active_initialization`, rerun exact `lineage-init` to
resume that intent; do not run `recover`. Registered `history_missing`,
`history_diverged`, or `recovery_pending` with an active publication/recovery
transaction uses exact recovery. Recovery resumes an original
checkpoint-verified/ready-for-CAS publication through the same CAS and local
publish. After validation/preflight it claims the active source, or creates and
claims a pending `recovery_restore`, before target writes. One SQLite transaction
then terminalizes that restore/lifecycle source and installs the sole pending
`evidence_recovery` successor. There is no aligned/no-audit crash gap; an
already-active `evidence_recovery` resumes directly, and retry creates no second
successor or duplicate lifecycle/recovery sequence. Under best-effort Memory, local-history loss
additionally requires an independently retained trusted archive; required
Memory reconstructs from exact immutable checkpoints while registry and Memory
history survive. An explicitly supplied archive selects candidate bytes only;
retained strict checkpoints remain mandatory and authoritative, and every
archive envelope must exactly match them before any write. It is not an offline
fallback for unavailable or divergent strict Memory.

Updater `apply`/`verify` may therefore be successful while one or more UCs are
not yet `aligned`. Report those as two distinct outcomes; never label engine
installation success as accepted lineage migration or active Wave 7 runtime.

## 8. SHA-256 threat model

Archive and manifest checks are tamper-evident integrity controls. They reject
corruption, ambiguous/missing/duplicate checksum records, archive traversal or
unsafe links, version disagreement, undeclared executable payloads, and any
mapped payload whose SHA-256 differs from the canonical manifest.

They are **not publisher authentication or a signed supply chain**. An attacker
who can replace both an archive and its checksum can provide a self-consistent
malicious pair. Obtain the target version, archive, and checksum through a
trusted release channel and independently verify release provenance when your
risk model requires publisher identity.

## 9. Completion report

Record:

- from/to version, source kind, release/manifest digest, and archive SHA-256 or
  the explicit unpacked-source limitation;
- plan decision and all conflicts/warnings;
- apply transaction ID, `verify` and smoke exit codes;
- hook changes, whether Codex re-trust was required/completed, and which new
  sessions were opened;
- profile migration status and the separate commit/action, if any;
- per-UC role-lineage `integrity_state` and the separately reviewed
  init/adopt/recover action, if any;
- rollback result when exercised and any remaining recovery/history limit.

An update is not complete while `verify` is `NO-GO`, recovery is pending,
required Codex re-trust/new sessions are outstanding, or a required profile
migration has not been separately resolved. Engine update success still does
not assert per-UC lineage acceptance; any scope promising active required Wave
7 governance remains incomplete until its governed UCs are `aligned`.
