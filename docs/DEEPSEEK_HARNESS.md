# BUGate for DeepSeek Harness

English | [简体中文](DEEPSEEK_HARNESS.zh-CN.md)

The native adapter uses BUGate's Python core to assess a task's current facts.
DeepSeek Harness (DSH) owns the Agent loop, tool execution and bounded rework.
Task artifacts stay in an external test repository. The existing Claude/Codex
0.4.5 installation and governance interfaces remain separate.

## Versions and distribution

| Component | Compatibility target |
|---|---|
| Python distribution | `bugate-core==2.0.0a1`, Python 3.11+ |
| Protocol | Exact `2.0.0` bundle digest; no automatic migration |
| Native package | `@bugate/deepseek-harness@2.0.0-dev.1` |
| DSH | `0.2.2-alpha.2`, source `f3803c96974ad4a94195fb97e5c637e9c2a3afe3` |
| Node | DSH requires 24.9.0+ |

The prebuilt tarball includes compiled JavaScript, native bundle patch, canonical
Schemas and methods, generated Skill, Python wheel and a checksum-inventoried
wheelhouse. Platform wheels are specific to the Python ABI and platform recorded
in `python/inventory.json`. Build on the target platform for a different ABI.
No global editable installation or source checkout is required at runtime.
The current collector and installation lock use POSIX process groups and file
locking. This delivery is tested on macOS arm64; the added CI job targets Linux.
Windows is not a supported execution target for this package.

## Install into an explicit project and profile

Use the tarball, `install_dsh.py` and `SHA256SUMS` from the same build. Verify
checksums before installation. DSH itself must already be installed at the exact
target version. Replace the example paths below with your chosen directories.

```sh
shasum -a 256 -c SHA256SUMS
python3 install_dsh.py \
  --package ./bugate-deepseek-harness-2.0.0-dev.1.tgz \
  --venv /absolute/path/bugate-bootstrap \
  --project /absolute/path/test-repo \
  --dsh-profile bugate \
  --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home
```

Python dependencies install offline from the bundle. DSH's native plugin manager
may need its package registry to resolve host peer dependencies. Setup calls the
real `dsh plugin --profile ... add ...` and verifies `--dump-config`. It preserves
unrelated configuration and rejects adoption of an unowned existing package.
Reinstalling identical bytes is idempotent; upgrading retains the previous bundle
for explicit rollback. A pending operation must be reconciled rather than silently
replaced. Setup success proves package/configuration installation; hydration is
only proven by an actual Agent session.

## Initialize and use a task

Author a JSON Profile against `protocol/v2/schemas/profile.schema.json`. It binds
source paths and SHA-256 digests, requirement/risk IDs, delivery criteria and
resource boundaries. Sources are relative to the external project; no secrets
belong in the Profile. The Profile is an explicit input, not inferred from chat.

```sh
BUGATE=/absolute/path/bugate-bootstrap/bin/bugate
"$BUGATE" init --project /absolute/path/test-repo --task REQ-001 \
  --sut-profile /absolute/path/profile.json --json
"$BUGATE" protocol prepare --task /absolute/path/test-repo/test-task/REQ-001 \
  --max-bytes 8000 --json
"$BUGATE" status --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" validate --task /absolute/path/test-repo/test-task/REQ-001 --json
"$BUGATE" artifact record --task /absolute/path/test-repo/test-task/REQ-001 \
  --input .bugate/drafts/brief.json --json
"$BUGATE" assess --task /absolute/path/test-repo/test-task/REQ-001 \
  --claim claims/design.json --json
"$BUGATE" evidence record --task /absolute/path/test-repo/test-task/REQ-001 \
  --input evidence-input/import.json --json
```

Each task owns `task.json`, `profile.json`, `.bugate/protocol.lock.json`,
`artifacts/`, `evidence/`, `claims/`, `assessments/` and `logs/`. Identity includes
the canonical project/task locations and exact Protocol/Profile bindings. The
same task ID in two projects has distinct identity. Multiple tasks require an
explicit selection (`setup --task-path`); the adapter does not guess the latest.

Build source-backed business propositions and Oracles, testability decisions,
inventory and a version-bound semantic review before claiming design completion.
Artifact payload variants and Claim types are defined by the canonical Schemas.
Use `protocol prepare` for current hard constraints and actionable gaps. The
adapter registers `bugate_prepare`, `bugate_status`, `bugate_validate`,
`bugate_assess`, `bugate_record_artifact`, `bugate_record_evidence` and
`bugate_run_test` in DSH. Write unsealed drafts under `.bugate/drafts/`; artifact
registration computes the canonical digest. Updating an existing artifact uses
`--expected-digest` to prevent accidental replacement. A calculated digest is
content integrity metadata, not semantic approval.

`bugate_run_test` accepts a host-configured command ID, not model-supplied argv.
Configure `executionCommands` in the owned plugin configuration with argv,
case IDs, report path and `purpose: test` or `exploration`. Test runs require
current accepted design; exploration can collect missing facts. The explicit CLI
collector `bugate execution run --help` exposes the same capture primitive.

## Quality and evidence semantics

Command success, structural validity, execution result, claim verdict and quality
posture are separate. CLI stdout is one versioned `CommandResult`; diagnostics
use stderr. `status` and `protocol prepare` can successfully report quality gaps.

| Exit | Meaning |
|---|---|
| 0 | Invocation completed and this command's success condition holds |
| 3 | `validate` or `assess` completed; quality is invalid/unsatisfied/inconclusive |
| 2 | Invalid input, binding or scope |
| 1 | Runtime/dependency/storage failure |
| 124 | Timeout; managed process termination attempted and reported |
| 130 | Cancellation |

Imported, human-supplied and model-reported material never becomes
`host_observed` merely by setting a field. Managed execution records actual argv,
identity, timestamps, outcome, bounded logs, JUnit cases and relevant input
digests, then durably publishes evidence before returning. Hashes detect changed
bytes; they neither establish the truth of preexisting logs nor protect against
an actor controlling all same-permission local files.

Claims are scoped: business understanding does not require future execution;
requirement verification needs current Oracle-linked case observations. Zero
tests, all skips/xfails and absent execution cannot establish verification. A
real product failure can still satisfy test delivery when its evidence, diagnosis
and agreed report are complete. An assessment applies to its fixed claim and
input snapshot; changed facts invalidate it. No Memory service is required.

## Managed policy and recovery

The default managed surface covers DSH `write`, `edit`, `str_replace_editor`,
`bash` and `bugate_run_test`. Reads and task-local analysis/claim material remain
available. Implementation/test paths require current accepted design. Arbitrary
shell writes are rejected; use explicitly configured exploration commands.
Other external tools/backends are not a universal filesystem sandbox. DSH's own
permission system and runtime providers remain responsible for execution access.

Asynchronous preparation runs at awaited lifecycle seams. Dynamic context and
the final guard are synchronous. The adapter injects canonical `render.content`
unchanged, with interpolation disabled, and invalidates old context on failure.
The awaited `agent/request` hook refreshes after request composition; the
`llm/stream` admission check rejects a context invalidated before model execution.
Child Agents resolve their own task binding. Persistent session binding checks
reject digest changes on cold recovery. Completion rework occurs only after an
explicit submitted claim, with attempt/time/no-progress bounds and cancellation.

Filesystem targets are opaque. The adapter uses the configured fs/subprocess
providers and `processPath`, requiring an execution-world mapping for installed
Python/resources. Missing mappings return capability errors; there is no browser
Python or fallback access to another host's disk.
The core supplies a bounded filesystem manifest. The adapter checks task files,
directory membership and Profile source bytes through the filesystem provider
before accepting the subprocess result. Matching lock files alone do not prove
that two execution worlds contain the same artifacts.
The witness is bounded to 2,048 files, 2,048 directories and 32 MiB of file
content; exceeding a bound fails explicitly. The write lock and non-authoritative
`.bugate/runtime/` bookkeeping are excluded; drafts, claims and evidence remain
covered. DSH session pins and their atomic temporary files live in that runtime
namespace, which managed model writes cannot modify.

```sh
"$BUGATE" doctor integrations --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home --json
"$BUGATE" remove deepseek-harness --project /absolute/path/test-repo \
  --dsh-profile bugate --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home --rollback --json
```

Omit `--rollback` for removal. Task facts, unrelated plugins/configuration and
recovery archives remain. Doctor separates host presence, plugin/resource
integrity, projection freshness, task bindings and hydration observation. A
missing unselected host is nonfatal; a broken selected integration fails.

## Development and evidence

Install the Python project in a dedicated environment; build the adapter with
`npm run build` in `adapters/deepseek-harness/plugin`, then run
`python packaging/build_dsh.py --out-dir dist/deepseek-harness`. Development
dependencies are not shipped. CI retains the entire legacy chain and adds an
independent Protocol/DSH job.

Acceptance distinguishes Python contracts, real DSH plus a deterministic model
fixture, and optional live-model testing. A deterministic fixture driving the
real DSH Agent loop is not a measurement of DeepSeek model effectiveness. Exact
commands, artifact hashes, 49-item HC/AC mapping and remaining gaps belong in the
delivery report, not in capability claims inferred from tool registration.

The [2026-10-29 delivery report](reports/DEEPSEEK_HARNESS_DELIVERY_2026-10-29.md)
records the executed 49-item acceptance matrix, package checksums and proof boundaries.
