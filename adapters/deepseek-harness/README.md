[简体中文](README.zh-CN.md)

# DeepSeek Harness adapter

The native `@bugate/deepseek-harness` bundle targets DSH **0.2.2-alpha.2**, verified
against official source commit `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`.
It uses the shared Python core **2.1.0a1** and canonical protocol **2.1.0-dev.1**.
This is the sole current product adapter. The 2.1.0-dev.1 build is a local delivery;
this refactor does not publish or replace a public release.

See [installation and usage](../../docs/DEEPSEEK_HARNESS.md),
[plugin configuration](plugin/README.md), and [runtime tests](tests/README.md).

`plugin/src/index.ts` implements real Cordis `apply(ctx, config)`. Awaited
`agent/created` and `agent/pre-step` prepare core context; synchronous
`systemPrompt.context.text` returns the validated core render verbatim.
`tools/pre-execute` obtains a current assessment and task lease; synchronous
`tools.guard` can only add a denial. Task-wide epochs invalidate old tickets and
contexts after managed operations. `agent/turn-stopping` re-assesses explicitly
submitted claims with retry, elapsed-time and unchanged-gap limits.

All Python calls use configured filesystem and subprocess providers. Opaque
filesystem target keys are never paths. Bundle resources and absolute installed
Python executables require an explicit host-to-execution-world mapping; unmapped
browser or remote worlds fail with an actionable capability error. Workspace
paths are resolved and contained before mapping to subprocess paths. The adapter
validates the core's finite filesystem witness against provider-read raw bytes,
source files and the complete task file/directory set; a matching lock alone is
not enough to establish a shared world. Only the write lock and the core-declared `.bugate/runtime/` control
subtree are excluded. Session pins live in its `deepseek-harness/` directory. The tested
execution provider is the official local provider on macOS; a mapped opaque
provider also verifies that no target key is interpreted as a path. Unconfigured
remote/browser execution is not claimed.

The supported guard scope is registered write/edit/str_replace_editor/bash and
BUGate mutation/execution tools. Other host tools require host policy/classification.
External processes or a principal controlling the same filesystem can change
facts outside a managed lease; hashes and session pins do not create a security
boundary against that principal. Ordinary imported evidence cannot become
host-observed execution. `bugate_run_test` invokes the core's one-shot execution
capture and awaits durable evidence before returning its result.

Real-runtime tests use official DSH services and a deterministic LLM fixture.
They prove runtime integration, lifecycle behavior and real local command
execution. They do not claim live-model business analysis quality.

An awaited `agent/request` refresh runs again before admission. If a sibling
changes task facts during subsequent model preparation, the official
`llm/stream` seam rejects the request before the model receives missing or stale
protocol context. It does not rewrite the immutable DSH request.

Acceptance scope is a first-class `ScopeContract`, consumed unchanged through
core context and one-shot execution admission. `bugate_scope` exposes the current
contract and host-captured user request receipts; `bugate_record_scope` records
source-backed drafts with compare-and-swap revisions. Each configured execution
is checked against the current scope, inventory plan, allowed environment and
activity. Expected scope/plan digests are rechecked by the collector before
starting the process. Auxiliary commands require a declared activity id; a
diagnostic activity also requires a current related failure and finite budget.

`bugate_record_suggestion` stores advisory descriptions only. Suggestions do not
change the authorization snapshot, become executable plans or trigger completion
rework. Completion of acceptance execution and functional requirement verification
are separate claims; an observed product failure remains a failure. Reports list
the actual finite scope and cannot claim overall product safety.

User request capture is awaited at primary-session input seams. DSH tags initial
subagent prompts as user messages, so the adapter checks persisted subagent origin
and does not promote model-authored delegation into user authority. Fork-inherited
events are not recaptured; BUGate rework has its own message source. Children and
cold sessions independently read the current scope and plan. Host permissions
and existing tool denials remain authoritative.
