# Install the verified Node DSH artifact

English | [简体中文](INSTALL_DSH_PROMPT.zh-CN.md)

This prompt installs the exact locally delivered `2.2.0-dev.1` Node artifact.
No new public release/tag is assumed. Use the artifact directory and checksum in
the corresponding completed delivery report; do not substitute a development
candidate merely because its version string matches.

```text
Install BUGate native DeepSeek Harness from the exact 2.2.0-dev.1 artifact
identified by the delivery report and supplied artifact directory. Do not use
latest, historical Python releases, rebuild from source or infer another target.

Require all files from the same build:
- bugate-deepseek-harness-2.2.0-dev.1.tgz
- install.mjs
- lifecycle.mjs
- archive.mjs
- package.json
- source-manifest.json
- build-manifest.json
- SHA256SUMS

Verify every SHA256SUMS entry and compare the tarball digest to the exact delivery
report, not only the filename. Stop on missing files, digest mismatch or an
INCOMPLETE/BLOCKED delivery that has not passed installed runtime acceptance.
Inspect build-manifest.json for exact source/dependency and platform identity.
The product requires Node >=24.9.0, DSH 0.2.2-alpha.2 and pnpm. It requires no
Python or BUGate source checkout. DSH host dependencies may need registry access.

Use only the user's explicitly selected external test project, DSH profile,
DSH home and DSH executable. Ask only for missing target identities; never guess
from a prior chat. Preserve unrelated profiles, plugins, credentials and task
facts. Do not replace an existing user profile during a validation exercise.

Run node ARTIFACT_DIR/install.mjs --package
ARTIFACT_DIR/bugate-deepseek-harness-2.2.0-dev.1.tgz --project TEST_PROJECT
--dsh-profile PROFILE --dsh DSH_EXECUTABLE --dsh-home DSH_HOME with the explicit
paths safely quoted. Then run DSH_HOME/profiles/PROFILE/node_modules/.bin/bugate
doctor integrations with the same target options and --json.

The bundle composes the original official Agent Team profile and BUGate. Do not
add a duplicate Team profile layer. Report package/configuration health separately
from a real DSH Agent session and Team review result. Deterministic runtime
fixtures do not prove live-model effectiveness or cognitive independence.

Initialize a new task only with the intended current source-backed Profile.
Prior protocol/evaluator bindings do not authorize the new implementation. Keep
old scope, failures, evidence and history read-only; do not convert them or
install a Python fallback. Required review perspectives follow the Profile,
not a fixed role/model count. Actual Team host provenance, exact current input
bindings, durable output and successful final settlement are required; names,
model reports, inbox acceptance and Team task complete do not prove review pass.

Keep the Profile coverage denominators fixed within a task. Changed denominator
requires an explicit new Profile version and init of a new task. ScopeRequest
proves user choice, not domain evidence. Suggestions cannot become required
execution or completion blockers. Report actual scope, execution closure,
functional results, blockers and suggestions separately.

On failure preserve exact errors and pending-operation journals. Do not delete
user state or blindly repeat an interrupted operation. Use the unified CLI
remove deepseek-harness --rollback only for the intended owned recovery. Do not
publish or overwrite releases/tags or remove unrelated Python/SUT environments.
```

See [installation and recovery](docs/DEEPSEEK_HARNESS.md) and
[scope examples](docs/ACCEPTANCE_SCOPE.md).
