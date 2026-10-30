# Native runtime verification

English | [简体中文](README.zh-CN.md)

Tests use real pinned DSH/Cordis services, official Agent Team, actual tools,
filesystem/subprocess providers, durable sessions and temporary neutral commands.
Only the model boundary is a deterministic fixture. Tests in the tree are not
proof they ran; the migration report records actual commands, versions, exact
source/package identities and output.

```sh
npm run build
node --test adapters/deepseek-harness/tests/core-service.test.mjs \
  adapters/deepseek-harness/tests/runtime-node.test.mjs \
  adapters/deepseek-harness/tests/team.test.mjs
```

- `core-service.test.mjs`: actual Node provider invocation, output/envelope/exit
  errors, private host observation channel, timeout/cancellation/drain, opaque
  filesystem keys and unavailable installation mappings.
- `runtime-node.test.mjs`: applicable preserved baseline assertions for exact
  context, compaction, resume, isolated tasks, guards, configured execution,
  PTC/child/preset lifecycle, concurrency, admission races and provider boundaries.
- `team.test.mjs`, `team-fixture.mjs`, `team-negative.mjs`: official Team members,
  task CAS, peer messaging and cold continuation, real inline author/reviewer
  provenance, source-required perspectives, raw dissent and evidenced disposition,
  current output binding and two-stage publication/final settlement. Negative
  controls cover imported Lead output, renamed self-review, fork context, stale
  artifacts, cross-task replay and cancellation after publication.
- `workflow-node.mjs`: shared neutral authoring/execution fixture. The contract
  explicitly requires positive, zero and negative results; all three have real
  assertions. Out-of-scope suggestions stay non-executable/non-blocking; actual
  product failure and bounded diagnostics are preserved.
- `standalone-node-runner.mjs` and `installed-profile.mjs`: the exact independently
  installed package, official profile composition/package resolution, actual
  backend Team and BUGate activation, success/failure workflows and Team controls.
  The owning installer acceptance applies OS source-read denial and process
  allowlisting for the complete runner subtree, then probes Python execution
  denial. Changing PATH alone is not isolation evidence.

No fixed historical test count or role matrix is a release gate. A fixture proves
runtime mechanisms and deterministic assertions, not live-model reasoning quality,
defect yield or efficiency. Live model execution is separately reported; absent
such a run it is **NOT_RUN**. Only actual tested platforms may be claimed.

Historical Python/runtime tests and released artifacts remain available in Git
history. Their original source and protocol identities are not TS/Team evidence.

`cordis-service.test.mjs` loads the actual registered `ctx.bugate` service and a
real `inject: ['bugate']` consumer. It calls the shared status/prepare/assessment
API, observes native tools using that same instance, and checks dependent unload,
old-reference rejection and reload. This focused test runs before the longer
Team and installed-package suites after a candidate build. The installed runner
also injects a consumer through the actually composed installed profile.
