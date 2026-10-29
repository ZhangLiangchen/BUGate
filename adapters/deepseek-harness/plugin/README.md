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
JUnit path), `purpose: 'test' | 'exploration'` and optional `timeoutSeconds`
(default 20). Commands must run in the same configured execution world and use
its executable/argument paths. The adapter does not translate arbitrary opaque
argument strings or implicitly cross into a different world. Configure a
bridge timeout longer than the command timeout plus capture overhead.

Tools: `bugate_prepare`, `bugate_status`, `bugate_validate`, `bugate_assess`,
`bugate_record_artifact`, `bugate_record_evidence`, and `bugate_run_test`.
`bugate_assess({claim,submit:true})` enables bounded completion rework.
Write artifact drafts under `.bugate/drafts/`; `bugate_record_artifact({input,
expected_digest?})` delegates digest computation, validation and atomic CAS to
the core. Updates supply the previous artifact digest. Ordinary evidence
recording never accepts host-observed provenance.

Analysis writes under `artifacts/`, `claims/`, `evidence-input/`, and precisely
`.bugate/drafts/` remain available before design acceptance. Implementation and
`tests/` writes require a current satisfied design claim. Other `.bugate/`
control files and arbitrary shell writes are denied in managed mode; configured
exploration commands permit necessary probes. Reads remain available.

A protocol/schema/version/projection mismatch clears prior satisfactory state.
Reload disposes tools, prompt providers and live subprocesses. Sessions pin the
exact full binding and reject silent changes during cold resume. Install the
adapter globally for independent presets and children; parent-local guards do
not automatically transfer to them. No BUGate agent loop or mandatory memory
service is added.
