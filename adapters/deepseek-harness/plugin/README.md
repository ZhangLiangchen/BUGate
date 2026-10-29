[简体中文](README.zh-CN.md)

# Native bundle configuration

Build with `node build.mjs`; it compiles TypeScript and generates protocol/Skill
resources from the repository's canonical protocol. Distribute the prebuilt
bundle produced by `packaging/build_dsh.py`, which includes Python wheels.
`dsh plugin add` uses `cordis.patch.yml`. The row id and plugin name are `bugate`
and `@bugate/deepseek-harness` respectively.

| Configuration | Default | Meaning |
|---|---|---|
| `pythonExecutable` | `python3` | Provider-resolved executable; absolute installed host paths require mapping. |
| `projectRoot` | session discovery | Optional filesystem-provider path restricting discovery. |
| `taskPath` | unique discovered task | Explicit filesystem-provider task path; ambiguity fails. |
| `mode` | `managed` | `advisory` omits write admission controls; assessments retain their meaning. |
| `maxContextBytes` | 8000 | Core rendering budget. |
| `timeoutMs` | 30000 | Per-core-call execution deadline. |
| `maxOutputBytes` | 1048576 | Combined process output limit. |
| `graceMs` | 1000 | Process termination grace period. |
| `maxRework` | 2 | Maximum completion repair requests. |
| `maxReworkMs` | 120000 | Completion repair time budget. |
| `executionCommands` | `{}` | Host-owned command registry; models supply only a command id. |

A command has `argv: string[]`, `caseIds: string[]`, `report` (a task-local new
JUnit path; required for acceptance tests and optional for auxiliary probes), `purpose: 'test' | 'auxiliary' | 'diagnostic' | 'exploration'`, optional
`environmentId`, and optional `timeoutSeconds`
(default 20). Commands must run in the same configured execution world and use
its executable/argument paths. The adapter does not translate arbitrary opaque
argument strings or implicitly cross into a different world. Configure a
bridge timeout longer than the command timeout plus capture overhead.

Tools: `bugate_prepare`, `bugate_status`, `bugate_validate`, `bugate_assess`,
`bugate_scope`, `bugate_check_scope`, `bugate_record_scope`,
`bugate_record_suggestion`, `bugate_record_artifact`, `bugate_record_evidence`,
and `bugate_run_test`.
`bugate_assess({claim,submit:true})` enables bounded completion rework.
Write artifact drafts under `.bugate/drafts/`; `bugate_record_artifact({input,
expected_digest?})` delegates digest computation, validation and atomic CAS to
the core. Updates supply the previous artifact digest. Ordinary evidence
recording never accepts host-observed provenance.

Analysis writes under `artifacts/`, `claims/`, `evidence-input/`, and precisely
`.bugate/drafts/` remain available before design acceptance. Implementation and
`tests/` writes require a current satisfied design claim. Other `.bugate/`
control files and arbitrary shell writes are denied in managed mode; configured
auxiliary/exploration commands require an `activityId` declared by the scope and
empty `caseIds`. A required test names only current inventory cases. Reads remain
available. The core admits one unambiguous allowed environment when
`environmentId` is omitted; multiple environments require an explicit selection.

A protocol/schema/version/projection mismatch clears prior satisfactory state.
Reload disposes tools, prompt providers and live subprocesses. Sessions pin the
exact full binding and reject silent changes during cold resume. Install the
adapter globally for independent presets and children; parent-local guards do
not automatically transfer to them. No BUGate agent loop or mandatory memory
service is added.

`bugate_record_scope({input, expected_digest?})` registers a draft referencing
host-captured user requests. `bugate_scope` supplies those request ids and exact
messages. The capture command is deliberately not a model-facing tool; writing
a claim of user consent is not authority. Changes need a new user request and
a new semantic necessity/change review. A changed requirement/risk denominator
requires an explicit new Profile version and new task; the old task and its
failures remain intact. Requests from subagent delegation and
plugin rework are not captured as human authorization.

For suggestions, write a `.bugate/drafts/` JSON object containing `id`,
`description`, `rationale` and `source_refs`, then call
`bugate_record_suggestion({input})`. Do not add commands or scenario steps. This
draft directory and canonical suggestions are excluded from authorization
snapshots, so an existing complete claim stays applicable.

Execution checks the current scope/plan twice: awaited admission prepares the
synchronous guard ticket, and the core verifies expected digests immediately
before starting the configured command. The registered auxiliary policy bounds
diagnosis by attempts and related failure evidence. Suggestions are never a
reason to steer more work; only in-scope evidence gaps can trigger bounded
completion repair. `execution_complete` may be satisfied for a genuine product
failure while `requirement_verified` is unsatisfied.
