[简体中文](README.zh-CN.md)

# DSH acceptance tests

Build `../plugin` first. Install the exact official DSH 0.2.2-alpha.2 package,
TypeScript 5.9.3 and Ajv 8.17.1 in a disposable Node environment. Resolve these
packages through `plugin/node_modules` or set `BUGATE_DSH_MODULES` to the directory
containing them for test-host loading. `BUGATE_TEST_PYTHON` selects a Python
interpreter with the core's dependencies installed.

```sh
node adapters/deepseek-harness/plugin/build.mjs
BUGATE_TEST_PYTHON=/absolute/venv/bin/python node --test adapters/deepseek-harness/tests/bridge.test.mjs adapters/deepseek-harness/tests/runtime.test.mjs
```

`host.mjs` mounts official Cordis/AgentLoop, filesystem, subprocess, prompt,
ToolExecutionPipeline, session, Skill and LLM services. Only the LLM adapter is a
deterministic fixture. Runtime tests additionally mount real JSONL persistence,
compaction-basic/token-meter, preset/loader, spawn/fork and Node PTC services.

The suite covers canonical context in 50 real model requests, actual compactNow
and subsequent rehydration, satisfaction becoming stale after a fact change,
exact cold bindings after a preset changes, parallel children and workspaces,
PTC nested execution with stable lineage, completion rework limits, reload,
non-BUGate workspaces, invalid resources and missing bindings. A deterministic
race suspends actual model preparation while a sibling writes, then proves that
no invalidated request reaches the model. The next turn prepares current facts.

The authoring workflow starts with no artifacts or implementation. Real model
tool calls read the requirement, write drafts, register core-computed digests,
record source-backed business Oracles and review, and assess design before
writing implementation. It then updates references/review, reassesses the
current design and executes real generated Python tests. Durable evidence must
exist before the tool result is observed.

Bridge tests execute actual local-provider subprocesses, including malformed
responses, exit/operation mismatches, missing Python, bounded output, timeout,
cancellation and opaque mapped target identifiers. Real provider subclasses also
expose same-binding copies with changed artifact/source bytes and extra/missing
paths; each must be refused before a model request. No secrets or company SUT
fixtures are committed.

After clean-package installation, run the separate mandatory test:

```sh
BUGATE_INSTALLED_PLUGIN=/absolute/profile/node_modules/@bugate/deepseek-harness \
BUGATE_INSTALLED_PYTHON=/absolute/installed/runtime/venv/bin/python \
BUGATE_DSH_MODULES=/absolute/runtime/node_modules \
node --test adapters/deepseek-harness/tests/standalone-package.test.mjs
```

The runner uses the official profile runtime resolution and asserts identical
DSH LLM module identity between the installed plugin and host.
It copies only generic test drivers into a fresh temporary directory, removes
PYTHONPATH/NODE_PATH, loads the installed bundle's lib/resources and installed
core, and executes the real authoring and command-capture workflow. Missing
installation variables fail explicitly; they never turn into skips. The
printed retained result JSON and runtime log identify exact installed paths,
compatibility metadata, request count and execution evidence.

These tests establish deterministic runtime integration, not live-model quality.
