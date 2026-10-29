[简体中文](README.zh-CN.md)

# DeepSeek Harness adapter

The native `@bugate/deepseek-harness` bundle targets DSH **0.2.2-alpha.2**, verified
against official source commit `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`.
It uses the shared Python core **2.0.0a2** and canonical protocol **2.0.0**.
This is the sole current product adapter. The dev.2 build is a local delivery;
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
