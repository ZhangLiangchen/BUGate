# Install the DSH 2.1.0-dev.1 prerelease

English | [简体中文](INSTALL_DSH_PROMPT.zh-CN.md)

This prompt installs the six exact assets from the fixed
[dsh-v2.1.0-dev.1 prerelease](https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.1.0-dev.1).
No BUGate checkout is required. The Python ABI and platform are recorded in the
tarball's `python/inventory.json`.

```text
Install BUGate native DeepSeek Harness from this exact GitHub prerelease:
https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.1.0-dev.1

Create a new local download directory and download all six files below using
HTTPS, redirects and HTTP-error checking from this fixed prefix:
https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/
Do not substitute another version, use latest, or rebuild from source:
- bugate-deepseek-harness-2.1.0-dev.1.tgz
- bugate_core-2.1.0a1-py3-none-any.whl
- install_dsh.py
- build-manifest.json
- source-manifest.json
- SHA256SUMS

Verify every SHA256SUMS entry (shasum -a 256 -c SHA256SUMS on macOS,
or sha256sum -c SHA256SUMS on Linux). The expected bundle SHA-256 is:
0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c
Stop if a file is absent or a digest differs. Set ARTIFACT_DIR to the verified
absolute artifact directory. Inspect python/inventory.json in the bundle and
verify a matching Python runtime, DSH 0.2.2-alpha.2 and Node >=24.9.0.
No source checkout or editable installation is needed by the product.

Use the user's explicitly selected external test project, DSH profile, DSH home,
and new or BUGate-owned bootstrap venv. If these target identities have not been
provided, ask only for the missing targets before installation; do not guess a
project from prior tasks. Preserve unrelated profiles/plugins and task facts.

Run python3 ARTIFACT_DIR/install_dsh.py --package
ARTIFACT_DIR/bugate-deepseek-harness-2.1.0-dev.1.tgz --venv BOOTSTRAP_VENV
--project TEST_PROJECT --dsh-profile PROFILE --dsh DSH_EXECUTABLE
--dsh-home DSH_HOME, substituting the explicit paths and quoting safely.
Then run BOOTSTRAP_VENV/bin/bugate doctor integrations with the same target flags
and --json. Report package/configuration installation separately from actual
runtime hydration. Do not claim live-model validation from a fixture result.

Initialize a task only with the intended current Profile. DSH captures direct
user-request receipts; author a finite acceptance scope from those receipts and
Profile-declared domain evidence. Keep the Profile requirement/risk denominator
fixed for every revision. A changed denominator requires an explicit new Profile
version and init of a new task; preserve the old task, evidence and failures.
This release corrects packaged version documentation and has different protocol
and evaluator digests from the earlier local 2.1 delivery. Preserve those earlier
tasks and initialize a new task explicitly; do not silently reuse their bindings.
Suggestions do not expand the executable plan or block completion. Report actual
scope, execution closure, functional results, blockers and suggestions separately.

On failure retain the exact error and owned pending-operation state. Do not delete
user files or invent a new target to retry. Use remove deepseek-harness with
--rollback only for the intended owned recovery. Do not publish or overwrite tags
or releases, and do not restore retired host adapters or old task formats.
```

The Python wheelhouse installs offline; DSH host peer dependency resolution may
need registry access. See [installation and recovery](docs/DEEPSEEK_HARNESS.md)
and [scope examples](docs/ACCEPTANCE_SCOPE.md).
