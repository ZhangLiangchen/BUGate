---
title: "Imported-mode updater contract"
version: 1.0
target_release: BUGate v0.4.2
status: normative
language: en
companion: IMPORTED_UPDATER_CONTRACT.zh-CN.md
---

[简体中文](IMPORTED_UPDATER_CONTRACT.zh-CN.md)

# Imported-mode updater contract

## 1. Purpose and release boundary

This is the normative v0.4.2 contract for upgrading a BUGate installation
inside an imported SUT repository. v0.4.2 is the first v0.4.x release that
ships a first-class incremental updater. It is not an alternate spelling of
`bugate init`, a re-import, or a scaffold refresh.

An update is auditable, fail-closed, transactional, reversible, and limited to
BUGate-owned surfaces. In archive mode the updater obtains and verifies the
complete release archive; the required unpacked bootstrap mode instead verifies
the canonical manifest and every mapped payload while reporting that raw archive
provenance is unavailable. Both apply only manifest-derived
`add`/`update`/safe-`delete` differences. Neither deletes and recopies the vendor
directory as its update algorithm.

No v0.4.x release is GO until the release archive itself passes the bootstrap,
incremental-update, rollback, and imported-mode acceptance gates in section 10.

## 2. Separate commands and supported entry points

`scripts/bugate_init.py` owns **fresh imported-mode installation** only. It
creates the initial SUT-owned config/profile skeleton, skill discovery links,
initial BUGate hook fragments, and the first installed lock. If it detects an
existing installed lock or a supported legacy layout, it exits non-zero and
directs the operator to `bugate-update`; an explicit `--upgrade` may only
delegate to this same updater engine, never a second implementation.

For a v0.3.x or exact pre-lock v0.4.0/v0.4.1 installation without an
authoritative lock/updater pair, an unpacked v0.4.2-or-later release provides
the one-time bootstrap interface:

```sh
cd <imported-sut-repository>
python3 <unpacked-release>/scripts/bugate_update.py plan . --vendor-dir .bugate
python3 <unpacked-release>/scripts/bugate_update.py apply . --vendor-dir .bugate
```

The verified unpacked release must remain available outside the imported repo
through the intended rollback window. After installation, the vendored
interfaces may be used only while both `<vendor-dir>/bugate.lock.json` and its
executable `bin/bugate-update` launcher exist; version text is not routing
evidence:

```sh
.bugate/bin/bugate-update status
.bugate/bin/bugate-update plan --to 0.4.2
.bugate/bin/bugate-update apply --to 0.4.2
.bugate/bin/bugate-update verify
.bugate/bin/bugate-update rollback --transaction <transaction-id>
```

Remote resolution requires an explicit, valid semantic-version target; there
is no implicit `latest`. Deterministic offline operation accepts both an archive
and its checksum asset:

```sh
.bugate/bin/bugate-update plan --archive /path/to/bugate-0.4.2.tar.gz \
  --checksums /path/to/bugate-0.4.2.SHA256SUMS
.bugate/bin/bugate-update apply --archive /path/to/bugate-0.4.2.tar.gz \
  --checksums /path/to/bugate-0.4.2.SHA256SUMS
```

`status`, `plan`, and `verify` are read-only with respect to SUT-owned state.
`plan` and `--dry-run` make **zero persistent writes** to the target repository:
no lock, hook, profile, Memory, cache, or report write. A temporary directory
outside the repository may be created and removed for archive verification.

## 3. Ownership catalog: the entire write boundary

The release manifest is the sole catalog of engine-managed paths. It separates
the full archive inventory from the installed projection and may manage only
these categories:

| Category | Managed surface |
|---|---|
| Vendored runtime | `<vendor-dir>/scripts/**`, `<vendor-dir>/bin/**`, `.shared/skills/bugate/**`, `.shared/skills/bugate-full-check/**`, `.shared/skills/bugate-import/**`, and release-declared BUGate runtime/setup documents |
| Discovery and agents | BUGate skill-discovery symlinks and BUGate-owned Codex gate-agent TOML files |
| Shared integration | BUGate-owned entries in `.claude/settings.json` and `.codex/hooks.json`; the marked BUGate block in root `.gitignore` |
| State | `<vendor-dir>/bugate.lock.json` and gitignored local transaction state only |

The updater must never write, delete, stage, commit, or normalize any other
surface. This includes `bugate.config.yaml`, `bugate.profile.yaml`,
`docs/usecases/**`, `00_role_evidence/**`, human acceptance artifacts, SUT
tests/evidence/wrappers/operating rules, `AGENTS.md`, `CLAUDE.md`, SUT-owned
hooks/skills/agents, non-marked `.gitignore` content, Memory data/namespace,
the machine-level `role-lineage.sqlite3` registry, or product and environment
material. Unrelated dirty files are reported as a warning only and are not an
update conflict.

Unknown files under a managed directory remain unknown: the updater must not
recursively remove them. A directory is removed only when it is manifest-owned,
empty after known safe deletions, and its type remains a directory.

## 4. Release manifest, legacy manifests, and installed lock

The release builder, not a hand-maintained list, generates a canonical JSON
release manifest from the release staging tree. Its canonical bytes are UTF-8,
sorted keys, compact separators, and a trailing newline. `self_digest` is the
SHA-256 of those canonical bytes with the `self_digest` member omitted.

```json
{
  "schema_version": 1,
  "bugate_version": "0.4.2",
  "layout_version": 1,
  "hook_contract_version": 1,
  "profile_schema_compatibility": {"source": "bugate.config.yaml:bugate.version", "min": "0.1", "max_exclusive": "0.2", "missing_maps_to": "0.1"},
  "updater_minimum_version": "0.4.2",
  "archive_inventory": [{
    "path": "scripts/bugate_core.py",
    "type": "file",
    "sha256": "<64-lowercase-hex>",
    "mode": "0644",
    "roles": ["installable_payload"]
  }],
  "installed_projection": [{
    "id": "vendor:scripts/bugate_core.py",
    "scope": "vendor",
    "source_path": "scripts/bugate_core.py",
    "target_path": "scripts/bugate_core.py",
    "type": "file",
    "sha256": "<64-lowercase-hex>",
    "mode": "0644"
  }],
  "self_digest": "<64-lowercase-hex>"
}
```

`updater_minimum_version` is the compatibility floor for the manifest/layout
protocol, not an automatic copy of every target release version. Schema/layout
1 uses `0.4.2`; a compatible later v0.4.x release keeps that floor so the
installed v0.4.2 launcher can validate the source and start the verified target
worker. A release that changes the updater protocol incompatibly must raise the
floor and is therefore rejected by older launchers before target writes.

`archive_inventory` covers every archive entry and assigns the machine-readable
array `roles` with one or more values:
`installable_payload`, `release_metadata` (release/legacy manifests, bootstrap
updater, and plugin manifests), or `validated_extra` (Core/plugin material that
is shipped but never installed). Metadata and payload are not mutually
exclusive: `scripts/bugate_update.py` is both; the canonical release manifest is
metadata whose content is installed through a `generated_metadata` projection.
The manifest entry itself uses
a reserved self-digest reference rather than a recursive file hash. Every entry
is still checked for path, type, mode, duplicate, link, and escape safety.

`installed_projection` is the complete write catalog. Each item has a stable ID
and one scope: `vendor` for a file/directory/symlink under `<vendor-dir>`;
`workspace` for an exclusive repo-root BUGate file or symlink;
`shared_json_fragment` for an exact hook event/matcher/value and semantic
digest; `marked_text_block` for the exact `.gitignore` marker/body/digest; or
`generated_metadata` for `<vendor-dir>/bugate.release.json` and the installed
lock contract. A `generated_metadata` item is writable only when its projection
names a verified `release_metadata` derivation; it needs no pretend payload
role. The installed manifest's content is the canonical source
manifest, so its full-file hash is recorded in the installed lock rather than
recursively inside itself.

Every archive `source_path` is normalized relative to the release root; every
rendered `target_path` is normalized relative to its declared target scope.
Neither may contain an empty, absolute, `.` or `..` component, and both remain
inside their roots after symlink-aware resolution. A separately validated
relative `vendor_dir` is the only projection parameter. Files declare SHA-256
and normalized executable mode; directories declare type and mode; symlinks
declare a safe relative target. Duplicate IDs/paths, conflicting types,
duplicate archive names, and undeclared executable payloads are invalid.

The builder also generates a legacy manifest from each supported formal v0.3.x
release/tag. It has the same owned-file and hook-shape evidence, plus its exact
legacy layout fingerprint. Support is defined by shipped legacy manifests, not
by a string in an SUT document. v0.3.2 is mandatory; v0.3.0, v0.3.1, v0.3.2,
v0.3.4, and v0.3.5 are the supported formal v0.3 tags (there is no v0.3.3
release). Pre-lock adoption manifests for v0.4.0 and v0.4.1 are also mandatory
so an existing v0.4.x import is not stranded. No best-effort “close enough”
recognition is permitted.

After a successful apply, `<vendor-dir>/bugate.lock.json` is a deterministic,
committable installed-state record:

```json
{
  "schema_version": 1,
  "installed_version": "0.4.2",
  "previous_version": "0.3.2",
  "verified_release_digest": "<64-lowercase-hex>",
  "archive_sha256": "<64-lowercase-hex-or-null>",
  "archive_verification": "sha256-or-unavailable-from-unpacked-source",
  "release_manifest_sha256": "<64-lowercase-hex>",
  "layout_version": 1,
  "hook_contract_version": 1,
  "profile_schema_compatibility": {"min": "0.1", "max_exclusive": "0.2"},
  "updater_version": "0.4.2",
  "installed_manifest": {"path": ".bugate/bugate.release.json", "sha256": "<64-lowercase-hex>"},
  "installed_projection": [
    {"id": "vendor:scripts/bugate_core.py", "scope": "vendor", "target_path": ".bugate/scripts/bugate_core.py", "type": "file", "sha256": "<...>", "mode": "0644"},
    {"id": "skill:codex:bugate", "scope": "workspace", "target_path": ".agents/skills/bugate", "type": "symlink", "target": "../../.bugate/.shared/skills/bugate"},
    {"id": "hooks:codex:pre-write", "scope": "shared_json_fragment", "target_path": ".codex/hooks.json", "semantic_digest": "<...>"}
  ]
}
```

It contains no absolute machine path, time, identity, credential, token, or
SUT fact. `verified_release_digest` binds the canonical manifest and every
declared source/projection hash. Remote/offline archive operation also records
the raw archive SHA-256. The unpacked bootstrap form cannot recover or verify a
raw archive digest from extracted bytes, so it records null and
`unavailable-from-unpacked-source`; checksum verification before extraction is
an operator prerequisite, not an updater observation. Every mapped payload is
still verified against the manifest. A same-version no-op never rewrites the
existing lock merely because the other archive format or a different container
digest was supplied; that input digest belongs in the read-only report.
Otherwise, reapplying the same version produces byte-identical lock content.
The complete rendered projection and installed canonical manifest are the
authoritative old/post-image baseline for v0.4.x updates and archive-free
`verify`.

## 5. Hook ownership identity and semantic merge

Hook ownership is stable data, not a substring heuristic. Canonical v0.4.2
hook commands start with an exported identity prefix:

```sh
BUGATE_HOOK_ID='<id>'; export BUGATE_HOOK_ID; ROOT="$(...find bugate.config.yaml...)"; [ -n "$ROOT" ] || exit 0; <vendored command>
```

The exact IDs and entry shapes are:

| Runtime/event | ID | Required matcher | Ordered command suffixes |
|---|---|---|---|
| Claude `PreToolUse` write gates | `bugate.claude.pre.write.v1` | `Edit|Write` | `check_bugate.py`, `check_plan_lock.py`, `check_role_evidence.py` |
| Claude `PreToolUse` role guard | `bugate.claude.pre.role.v1` | `Read|Edit|Write` | `check_agent_role_paths.py` |
| Codex `PreToolUse` | `bugate.codex.pre.write.v1` | `apply_patch` | `check_bugate.py`, `check_plan_lock.py`, `check_agent_role_paths.py`, `check_role_evidence.py` |
| Claude/Codex `UserPromptSubmit` | `bugate.<runtime>.prompt.v1` | event default | `bugate_prompt_reminder.py` |
| Claude/Codex `SessionStart` | `bugate.<runtime>.session-start.v1` | event default | `memory_bus.py session-start --agent agent`, `bin/bugate-role session-start` |
| Claude/Codex `Stop` | `bugate.<runtime>.stop.v1` | event default | `memory_bus.py stop --agent "${BUGATE_AGENT_ROLE:-agent}"` |

The `<vendored command>` uses the configured vendor dir and the rooted lazy
resolver shown above. Its event, matcher, identity, command count, order, and
entrypoint/arguments must all match. The unified pre-lock recognizer accepts
only the identity-free shapes recorded verbatim for v0.3.0, v0.3.1, v0.3.2,
v0.3.4, v0.3.5, v0.4.0, or v0.4.1: same event/matcher and complete ordered
command list with that release's official resolver and entrypoints.

An ID is only a routing label and never proves ownership. The merger may replace
an entry only when its complete event, matcher, ordered value, and semantic
digest exactly match the prior installed lock or a shipped pre-lock manifest.
Duplicate IDs, an ID with a different shape/digest, partial canonical entries,
or apparent ID spoofing are conflicts and NO-GO. A mixed entry is preserved as
SUT-owned; during pre-lock adoption, if the independent exact legacy entry is
missing or non-standard, adoption is NO-GO. Adding a new canonical entry must
not hide the broken baseline. The merger preserves all other JSON values and
ordering, changes only the minimal owned entries, retains valid JSON, and avoids
whole-file reformatting. The plan reports whether `.codex/hooks.json` bytes will change;
only an actual byte change reports `Codex hook hash changed: re-trust required`.
Any changed hook also requires a new Claude/Codex session before its new runtime
surface is active.

## 6. Detection, adoption, and plan contract

Detection first reads and verifies an installed lock. Without a lock it attempts
exact pre-lock detection for v0.3.0, v0.3.1, v0.3.2, v0.3.4, v0.3.5, v0.4.0,
and v0.4.1 from release-generated manifests, using the complete rendered
projection, layout fingerprint, and exact hook shapes. A precise match
may create the first lock during `apply`; `plan` creates nothing. Missing
critical files, mixed fingerprints, non-standard hook wiring, an unknown
layout, or a local managed modification is NO-GO and identifies each path with
expected and actual type/hash/mode.

There is no broad `--force`. If adoption of a legacy local change is supported,
it is an explicit per-path `adopt` operation that records the operator’s named
override and observed hash in the JSON report before any apply. It does not
silently overwrite a conflict and does not broaden ownership.

`plan` is reproducible human-readable output, with `--json` for automation.
Every managed item is one of `unchanged`, `add`, `update`, `delete`,
`locally_modified`, `conflict`, `type_changed`, or `permission_changed`; hook
operations are `hook_refresh`; profile results are `migration_available` or
`migration_required`. The plan includes from/to version, archive and manifest
digests, full changes, stale known files, local modifications, hook changes,
profile compatibility, Codex re-trust/new-session flags, rollback availability,
and a final `GO` or `NO-GO` decision.

The JSON plan has a deterministic `plan_digest` over its canonical content and
the exact base observations. `apply --plan <file>` rehashes/rechecks every base
item and rejects any drift; it does not use a stale plan. Direct `apply` builds
and validates an equivalent in-memory plan before writing.

The current-item rules are strict: a current hash equal to the old manifest can
be updated; equal to the new manifest is already-updated; equal to neither is
locally modified and NO-GO. A stale file can be deleted only if its hash still
equals the old manifest. Type and permission changes are separately visible and
must satisfy the same ownership/baseline checks.

Profile compatibility observes the config-schema field `bugate.version` in
`bugate.config.yaml`; the legacy top-level `version` alias is equivalent.
Missing legacy values map to `0.1`. Malformed or unknown values produce blocking
`migration_required` and make the engine-update plan NO-GO. A compatible
legacy/off role-governance profile may report non-blocking
`migration_available`, which remains a separate human-reviewed action.

## 7. Transaction, rollback, and crash recovery

Persistent journals, transaction downloads, backups, workers, and failure
reports live only in a verified Git-ignored repository-local state directory;
they are never part of the release manifest or installed lock. A lock-based
installation uses `<repo-root>/.bugate-update/`. Every supported pre-lock
manifest proves that its exact historical BUGate marker block ignores
`/<vendor-dir>/plan.lock`; bootstrap therefore first uses the already-ignored
`<repo-root>/<vendor-dir>/plan.lock/bugate-update/`. If that ignore rule or
exact block is absent, apply is NO-GO with zero writes. Because `plan.lock` is
also the legitimate optional plan-lock file, any pre-existing path of that name
(file or symlink) makes bootstrap NO-GO and is never overwritten, removed, or
adopted. A pre-existing directory is recoverable only when it contains the exact
updater ownership sentinel and valid bootstrap journal for this canonical repo;
any other directory is operator-owned and NO-GO.

When the path is absent, the updater prepares the complete sentinel+journal
directory in an auto-cleaned staging directory on the same filesystem outside
the repo, fsyncs it, then atomically renames that whole directory to
`<vendor-dir>/plan.lock`. Thus repo-visible bootstrap ownership intent and the
recoverable journal appear in one filesystem operation: a crash before rename
leaves no repo change, and a crash after rename is unambiguously recoverable.
The publication must use a kernel no-replace primitive (Darwin
`renameatx_np(RENAME_EXCL)` or Linux `renameat2(RENAME_NOREPLACE)`), because a
plain `rename`/`os.replace` can overwrite a concurrently created empty
directory after a time-of-check/time-of-use race. If same-filesystem atomic
no-replace rename cannot be guaranteed on the host platform, apply is
zero-target-write NO-GO. While present, the resulting write-gate block is an
additional fail-closed signal.

Before any repository write, the updater completes archive/checksum/manifest/
version/base validation in the auto-cleaned temporary manner permitted for
plan, acquires the workspace lock, then copies the verified transaction input,
old `.gitignore` snapshot, backups, and journal into that verified ignored
bootstrap state. It updates only the exact BUGate block as the first transaction
mutation, verifies the new root-state ignore, establishes
`<repo-root>/.bugate-update/`, and transitions the journal there before any
vendor or hook mutation. The transaction-scoped worker executes from that root
state outside the vendor tree; an OS-temporary worker is allowed only for the
brief self-copy bootstrap and is reproducible from the verified copy in ignored
state. After that durable transition and before vendor/hook mutation, it removes
only its own empty `<vendor-dir>/plan.lock` bootstrap directory; it never
removes an operator-created plan lock. Any failure restores the old block and a
crash is recoverable from the ignored journal. Reports are machine-readable,
diagnostic, and secret-free. A
concurrent updater exits non-zero without touching managed or SUT-owned state.

An apply transaction is:

1. acquire the workspace/update lock and recover an interrupted prior journal;
2. validate root, vendor dir, lock/legacy state, archive checksum, archive
   safety, release manifest, version agreement, and freshly observed plan base;
3. copy a transaction-scoped, self-contained updater worker and all imports
   outside the vendor tree, record its path and digest in the journal, then
   stage and verify new content, modes, and symlink targets there;
4. snapshot every changing managed path, shared hook file, and installed lock;
5. atomically replace only allowed managed files, performing safe known deletes;
6. semantically merge owned hook entries and the marked `.gitignore` block;
7. run post-update `verify` against the new manifest and lock candidate;
8. atomically write the new installed lock, mark the journal committed, write
   the transaction report, and clean staging.

At every failure point or handled interrupt before commit, the worker restores
the snapshots of managed paths, shared hook files, and old lock, then writes a
failure report. A crash leaves a durable journal. `rollback --transaction <id>`
requires a complete committed transaction snapshot, restores only that
transaction’s owned changes, verifies the restored state, and writes its own
report. Read-only `status`, `plan`, and `verify` report `recovery_required`
without mutating persistent state; the next `apply` or explicit `rollback`
performs the journal-driven recovery before proceeding. Rollback path validation
uses the same no-escape rules as apply.

The v1 transaction image is a logical content image, not a complete inode
metadata snapshot. It journals file type, bytes/SHA-256, and mode; directory
type and mode; and symlink target. Extended attributes, ACLs, uid/gid,
hardlink relationships, and timestamps are outside the v1 journal and rollback
guarantee. An atomic replacement can therefore allocate a new inode without
preserving that metadata. Operators that attach such metadata to a managed or
shared file must back it up independently or remove that dependency before an
update.

The descriptor-pinned v1 validator retains at most 128 transaction journals.
At exactly 128, the existing history remains valid and interrupted recovery is
still available, but every public apply, bootstrap reuse, or rollback that
would create journal 129 fails before persistent repository state or transition
intent is written. BUGate does not automatically prune committed rollback
history. This is an explicit fail-closed operational bound, not unlimited
rollback retention.

Explicit rollback acquires the same workspace lock and is itself journaled,
atomic, interrupt-safe, and crash-recoverable. Before restoring an older
transaction it verifies every current owned item, semantic fragment, installed
manifest, and lock against that transaction's recorded post-image. A later
update or local drift makes the transaction stale and rollback NO-GO rather
than overwriting newer state.

Rollback restores the exact recorded pre-image, not a permanently upgraded
control plane. Consequently, rollback of the first v0.4.2 updater transaction
to v0.3.x or pre-lock v0.4.0/v0.4.1 removes the installed lock and vendored
launcher. Post-rollback verification must select the entry point from the
restored state: use vendored `verify` only when both lock and executable
launcher remain; otherwise use the retained external updater:

```sh
python3 "$BOOTSTRAP" verify . --vendor-dir .bugate
```

`$BOOTSTRAP` names the verified updater in an unpacked v0.4.2-or-later release
outside the imported repo. This read-only verification must recognize an exact
supported legacy/pre-lock image without installing a lock or launcher. If a
rollback is interrupted after the launcher changes, the same external updater
must provide read-only `status`/`verify` and the exact transaction-specific
rollback retry needed for recovery. Operators must not reconstruct the
launcher or edit journals/sentinels manually.

When rollback restores a pre-lock installation whose historical marked block
does not ignore `/.bugate-update/`, the committed state and reports are copied,
fsynced, and published with the same exclusive no-replace rule as the exact
`<vendor-dir>/plan.lock/bugate-update/` shape before root state is retired. A
marker-bound, idle archived state may be reused by a later bootstrap only after
its sentinel, complete journals, root identity, and tree digest validate. A
crash that temporarily leaves both copies is reported as recovery-required;
the mutating recovery path reconciles equal copies and never guesses between
different ones.

## 8. Profile, role governance, and Memory isolation

Engine update and governance activation are different actions. The updater may
ship role-governance-capable scripts/hooks, inspect profile schema compatibility,
report `migration_available`/`migration_required`, and generate a proposed
profile patch. It must not edit a profile during `plan` or `apply`, turn
`mode: off` into `required`, manufacture human acceptance/handoff/receipt
evidence, modify `00_role_evidence/**`, or change `memory.namespace`.

The v0.4.3 lineage amendment adds another independent operator
boundary. The updater may install lineage-capable engine files, but it never
creates or edits the machine-level lineage registry, never publishes a
deterministic lineage root/checkpoint, and never runs `bugate-role
lineage-init`, `lineage-adopt`, or `recover`. A successful engine
`apply`/`verify` is therefore **not** acceptance of per-UC lineage migration.
After the engine update is reviewed and committed, the operator must run
`bugate-role lineage-status <artifact-dir> --json` for each governed UC and
separately confirm the exact initialization, adoption, or recovery route. The
updater's profile-compatibility `migration_available`/`migration_required`
result and `bugate-role`'s history-integrity `migration_required` state are
different signals and must not be conflated.

An optional profile-migration command defaults to check/plan. A write, if ever
provided, is a separately named explicit action with its own reviewable diff
and is not inside an engine transaction. Recommended adoption is therefore two
separately reversible commits: update the vendor engine while preserving the
legacy profile, then review and deliberately enable strict role governance.

The updater neither reconstructs, clears, migrates, nor writes Memory Bus data
or lineage registry data.
It may perform a read-only health check. Memory downtime is reported separately:
it cannot relabel a successfully persisted engine update as a fully accepted
strict-role transition. Required Memory transitions fail closed only when the
operator later activates and verifies that governance.

Updater rollback restores only its manifest-owned engine transaction. It does
not undo a separately accepted profile change, lineage adoption, registry
record, Memory root/checkpoint, or lifecycle receipt. Those operations need
their own compatibility review and recovery record; they must never be hidden
inside an updater transaction.

## 9. Archive safety and threat model

The stdlib-only reader validates every tar/zip entry and rejects payloads with
absolute paths, `..`, empty or duplicate/conflicting names, path traversal,
unsafe hardlinks, unsafe symlinks, or symlink escapes. A full BUGate release may
contain non-vendored Core documentation and plugin files. Read-only release
metadata (bootstrap updater, release/legacy manifests, and plugin manifests) is
explicitly inventoried and readable but is not installed unless a projection
item names a target; validated extras are never update input. Only mapped
`installable_payload` paths (including entries that are also metadata), plus
explicitly derived `generated_metadata` projection items, can write targets.
The reader rejects
invalid semver, ambiguous/missing/duplicate checksum records, checksum mismatch,
manifest/archive/plugin/CLI target-version disagreement, or manifest paths that
escape their declared vendor/workspace/shared scope root. All such failures
occur before target-repository writes.

This is tamper-evident integrity checking, not cryptographic publisher identity
or a signed supply chain. SHA-256 plus a chosen GitHub Release archive detects
accidental corruption and changes relative to the supplied checksum; it does
not prove who published a malicious but consistently checksummed release.

## 10. Verification and GO gates

`verify` checks installed-lock determinism and agreement, release-manifest
digest, every owned file’s type/hash/mode/symlink target, exact canonical hook
ownership, and preservation of non-owned hook entries. It never repairs drift.

Release acceptance must build from a clean Core checkout and demonstrate, with
recorded commands, exit codes, and test counts:

1. plugin/version/release-manifest agreement, archive SHA-256, safe extraction,
   and absence of SUT facts, secrets, caches, or developer-machine paths;
2. a fresh synthetic temporary v0.3.2 imported fixture upgraded by the archive’s
   bootstrap updater, with all non-owned fixtures byte-identical;
3. imported smoke/full-check success, same-version idempotence, and explicit
   rollback plus verify; and
4. unit, integration, negative, concurrency, crash-recovery, and failure-
   injection coverage for every transaction stage and ownership boundary.

For a lineage-capable release, ownership-preservation acceptance additionally
snapshots the effective Memory home and proves that updater plan/apply/verify/
rollback do not create or change `role-lineage.sqlite3`, lineage roots,
checkpoints, profiles, namespaces, or role evidence. Installing the command
surface is not a substitute for separately exercising its SUT-neutral lineage
init/adopt/recovery contract.

For the updater/archive release gate, "imported smoke/full-check" means running
the archive-native acceptance with
`--full-check-mode smoke --full-check-archive both`. This mode is not a shallow
binary check: it exercises installed-state verification, the strict
six-transition Memory contract, bootstrap/apply, idempotence, rollback/reapply,
and the post-check ownership-preservation oracles on both tar and zip. The
separate `--full-check-mode full` audits an operator machine's optional
heterogeneous Codex+Claude runtime. Its real result must be reported, but one
provider's account or network outage does not invalidate an otherwise verified
updater archive.

Semantic release review remains provider-neutral and requires at least two real
independent sessions. Cross-provider review is preferred; when one provider is
unavailable, a newly spawned same-provider session is acceptable only if it
uses a different review prompt and cannot see the first reviewer's output before
synthesis. The fallback and both conclusions are recorded. Placeholder output,
a continued conversation, or self-review never satisfies this gate.

Tests construct SUT-neutral temporary repositories at runtime. They do not read,
copy, clone, worktree, or modify a real imported SUT. GO requires all release,
archive, updater, compatibility, and pre-existing Wave 7 gates to pass; a
desktop hook hash change remains a user re-trust prerequisite and must not be
claimed active until re-trusted.
