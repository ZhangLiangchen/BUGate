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

On macOS, set `BUGATE_DENY_SOURCE_ROOT=/absolute/BUGate` for the standalone test.
The runner is then launched under the OS sandbox with checkout reads denied;
it asserts an actual permission error before loading the installed plugin.
This proves independent runtime operation even when the source cannot be read.

`tests/integration_install/accept_published_upgrade.py` takes explicit previous
and candidate archives plus the published previous SHA256. It installs the selected prior archive,
upgrades to the current candidate, rejects the old task binding without modifying it, prepares a
new current-protocol task, and rolls back to the exact retained prior archive. This is distinct
from the metadata-only upgrade fixture in `accept_package.py`, which tests
transaction/crash recovery. No v0.x import or automatic task migration is supported.

Scope-convergence invariant checks run real model tool
calls against current core admission. The deterministic model attempts an
unrelated configured test, observes its denial before process start, records a
non-executable suggestion, executes the required cases and submits completion.
The same workflow runs with an intentional product defect: functional verification
fails while execution completion succeeds. Additional suggestions are recorded
after completion; the identical claim remains satisfied with no rework.

The installed runner executes both passing and failing workflows under source
read denial when configured. It verifies durable evidence before tool results,
exact scope/plan references, required scenario counts and independent functional
versus execution verdicts. A separate real subagent test proves that DSH's
user-tagged delegation prompt cannot create a human authorization receipt.
Uncited continuation messages do not alter the authorization snapshot.
