# BUGate for DeepSeek Harness

English | [简体中文](DEEPSEEK_HARNESS.zh-CN.md)

BUGate's shared TypeScript core evaluates finite, source-backed task facts. The
unified Node CLI and Cordis plugin consume that same core. DSH owns Agent loops,
Team membership, messages, task boards, scheduling and lifecycle. Task facts and
SUT dependencies remain in an explicitly selected external project.

The `2.2.0-dev.1` implementation is under verification. Candidate installation
results are not final Team acceptance. Public Python releases remain historical
records; this work does not publish or overwrite a release.

| Component | Target |
|---|---|
| Core / CLI / installer / native package | `2.2.0-dev.1` |
| Protocol | Exact `2.2.0-dev.1` resource digest |
| DSH / official experimental Agent Team packages | `0.2.2-alpha.2` |
| Cordis | `4.0.6-alpha.1` |
| Node | `24.9.0+`; record the actual tested version |

The tarball contains compiled first-party JavaScript, runtime dependencies,
canonical Schemas/MethodSpec, the generated Skill projection and the original
official Team profile layer. It contains no Python bootstrap, wheels or product
fallback. Host dependencies may require registry access; the bundle does not
claim to include an offline DSH installation. POSIX process groups are used;
only actually verified platforms are reported, with no inferred Windows support.

## Install and inspect

Use all assets from the same build. Verify `SHA256SUMS` before executing the
standalone Node installer. DSH and pnpm must be installed, and the external project
must already exist. Select the project, DSH home and profile explicitly.

```sh
shasum -a 256 -c SHA256SUMS
node install.mjs \
  --package ./bugate-deepseek-harness-2.2.0-dev.1.tgz \
  --project /absolute/path/test-repo \
  --dsh-profile bugate \
  --dsh /absolute/path/dsh \
  --dsh-home /absolute/path/dsh-home
BUGATE=/absolute/path/dsh-home/profiles/bugate/node_modules/.bin/bugate
"$BUGATE" doctor integrations --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/dsh \
  --dsh-home /absolute/path/dsh-home --json
```

Setup calls the real DSH plugin manager, records archive identity and an owned
configuration block, and verifies composed configuration. Identical bytes are
idempotent. Unowned packages, changed owned blocks and ambiguous identities are
rejected. Doctor verifies retained and installed bytes; configuration health is
not Agent hydration or Team review acceptance. The installed profile must actually
load its official Team layer and BUGate plugin in a DSH session.

The native bundle composes the original official Team profile before BUGate. This
provides Team service/tools and disables direct subagent/control/fork tools as
specified by DSH. Do not independently add another Team profile layer to the same
profile. Experimental APIs are pinned, not claimed stable.

## Bind a finite task

Author a Profile using the shipped `profile.schema.json`, with real domain source
paths/digests, fixed requirement/risk IDs and applicable review policy. Then:

```sh
"$BUGATE" init --project /absolute/path/test-repo --task TASK-1 \
  --sut-profile /absolute/path/profile.json --json
"$BUGATE" protocol prepare --task /absolute/path/test-repo/test-task/TASK-1 --json
"$BUGATE" status --task /absolute/path/test-repo/test-task/TASK-1 --json
"$BUGATE" artifact record --task /absolute/path/test-repo/test-task/TASK-1 \
  --input .bugate/drafts/brief.json --json
"$BUGATE" assess --task /absolute/path/test-repo/test-task/TASK-1 \
  --claim claims/design.json --json
```

Use `setup deepseek-harness ... --task-path /absolute/path/.../TASK-1` when task
selection is ambiguous. Rebinding an existing installation requires explicit
removal first; no latest-task inference or automatic initialization occurs.
User receipts establish choices, while domain facts remain Profile-declared.
Authorized branch/Oracle changes within the denominator create a new scope
revision; denominator changes require a new Profile version and new task. Preserve
old facts and failures. Suggestions are non-executable and non-blocking.

Task-local paths include artifacts, evidence, claims, assessments, scope history
and immutable host observations/settlements. The core rejects mismatched protocol,
evaluator and Profile bindings. Old Python tasks remain read-only history: inspect
retained JSON/logs and their recorded identity, or use the matching historical
release in its separate environment. No converter or cross-version authorization
bridge exists; a new Node task requires an explicit current binding.

## Native tools and review provenance

Use `bugate_prepare`, `bugate_status`, `bugate_validate`, `bugate_scope`,
`bugate_check_scope`, `bugate_record_scope`, `bugate_record_suggestion`,
`bugate_record_artifact`, `bugate_record_evidence`, `bugate_assess` and
`bugate_run_test` for the corresponding quality operations. Imported material
retains model/import provenance.

`bugate_publish_artifact` captures the actual live Team member's inline output
and host identity. It binds author/reviewer execution, Team membership, fresh/fork
context, tool input, task/protocol/evaluator/Profile, artifact digest and the
acyclic input snapshot. A persisted output/HostObservation is not sufficient by
itself: the final host tool-result seam must durably publish successful
HostSettlement before the output can authorize claims. Cancellation, timeout,
failed or missing settlement remains incomplete. CLI JSON imports cannot create
these host receipts.

Required perspectives follow the effective Profile, with source-backed necessity
and bounded completion conditions. Team members exchange dissent and counterevidence
through official Team messages; raw opinions, discussion links, revised opinions
and adoption/rejection/unresolved dispositions remain traceable. Artifact or
relevant input revisions invalidate previous reviews. Cross-task records, replay,
renamed self-review and missing author/reviewer provenance cannot authorize the
current claim. Team task complete and inbox acceptance are not review acceptance.

Execution independence and semantic quality are reported separately. Fresh/fork
and role separation do not prove cognitive independence or correctness. Fork is
not blind review; the same model may validly serve different members when the
declared observable policy permits it. Team currently shares one process/cwd;
writeScopes is advisory, not worktree isolation. Task owner release and message
retry/deduplication are not guarantees supplied by BUGate.

Native context sent to the Node subprocess is signed by an Ed25519 key held in
the plugin process. The configured filesystem provider persists the public-key
pin; the public CLI cannot register an authority. Signatures bind operation, task,
workspace and exact context bytes. Stored receipts are verified again after
recovery. A person able to replace the pin or all local state remains outside
this same-permission trust boundary.

## Providers, managed tools and completion

Configure `nodeExecutable` and, when required, `coreEntrypoint` in the actual
filesystem/subprocess execution world. `projectRoot` and `taskPath` bind the
external task. Opaque fs target keys are not host paths. Node service IO and
collection run through the configured subprocess provider; the plugin validates
provider-visible manifests and rejects unsupported mappings instead of silently
reading another world. Publication is awaited before tool success.

`executionCommands` maps stable `command_id` values to argv, case IDs, report,
optional activity/environment identity and timeout. Required cases require current
scope/plan admission. Auxiliary and diagnostic activities use explicit contract
bounds; suggestions cannot become executable plans. `bugate_run_test` accepts a
configured command ID, not arbitrary model argv. Preserve actual product failures,
infrastructure errors, missing/zero reports, skip, xfail, timeout and cancellation.

Awaited lifecycle seams prepare state before synchronous `context.text` and
`tools.guard` consume it. The plugin injects canonical render content unchanged,
checks admission before model execution, and resolves each restored/child/Team
member binding independently. Managed mode governs declared write tools and
command triggers; it cannot infer all arbitrary shell semantics and does not
replace DSH permissions. Advisory mode omits managed write admission, but core
assessment and managed test scope checks remain active.

Completion rework starts only after `bugate_assess` with `submit: true`. Attempts,
time and repeated-gap bounds prevent an endless consensus loop. A stopped turn
is not a satisfied claim. Report execution closure, functional acceptance,
blockers and out-of-scope suggestions separately.

The CLI emits one `bugate.io/v2` `CommandResult`. Exit 0 means the invocation
completed; inspect its data, because a collected product failure is still a real
completed invocation. Exit 3 reports an unsatisfied assessment or quality
validation result, 2 invalid input, 1 runtime/infrastructure failure, 124 timeout
and 130 cancellation. Verdict, structure, semantic review and execution dimensions
remain separate.

## Upgrade, interrupted operations and removal

```sh
"$BUGATE" setup deepseek-harness --package /absolute/path/new-bundle.tgz \
  --project /absolute/path/test-repo --dsh-profile bugate \
  --dsh /absolute/path/dsh --dsh-home /absolute/path/dsh-home --json
"$BUGATE" remove deepseek-harness --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/dsh \
  --dsh-home /absolute/path/dsh-home --rollback --json
```

`--rollback` reconciles an interrupted owned operation or restores the retained
previous Node archive. A dead-owner lock is recovered only by this explicit path;
a live owner is not stolen. Without `--rollback`, remove uninstalls only owned
configuration/package state. Unrelated settings/plugins, task facts, historical
failures, receipts and recovery archives remain. It never removes global Python,
SUT dependencies or unrelated environments. Retain exact errors and journals
instead of deleting state or blindly repeating an interrupted operation.

## Verification

See [contributing](../CONTRIBUTING.md) and [CI](../.github/workflows/ci.yml).
Independent acceptance copies the artifact and test runner outside the checkout,
then uses an OS policy denying checkout reads and all executions except an explicit
Node/host/tool allowlist. Source-read and absolute-interpreter denial probes run
in the same restricted process tree as installation, lifecycle and installed real
DSH/Team tests. PATH changes alone do not establish isolation.

Record exact package checksum, source inventory, Node/DSH/Cordis/Team/provider and
platform versions, command output and actual results. Deterministic fixtures drive
real host members/tools and temporary commands; they do not measure live-model
quality or efficiency. Live-model results remain separately identified. Local
host observations and hashes do not authenticate against same-permission filesystem
tampering. Historical reports apply only to their original identities.
