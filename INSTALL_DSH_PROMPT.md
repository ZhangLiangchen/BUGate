# Install this v2 + DSH build

English | [简体中文](INSTALL_DSH_PROMPT.zh-CN.md)

This prompt uses newly built local assets, not a prior release download. The
artifact directory is `dist/v2-dsh-only/` in the BUGate checkout; it may be copied
as a unit to another machine with the recorded Python/platform ABI.

```text
Install BUGate v2 native DeepSeek Harness from these local build artifacts:
ARTIFACT_DIR=/Users/zhangliangchen/PycharmProjects/BUGate/dist/v2-dsh-only
Use bugate-deepseek-harness-2.0.0-dev.2.tgz, install_dsh.py, build-manifest.json,
source-manifest.json and SHA256SUMS from that directory. Do not substitute an older
published asset. First verify every SHA256SUMS entry and inspect the bundle's
python/inventory.json for target platform/ABI. Verify DSH 0.2.2-alpha.2, Node >=24.9.0
and a matching Python runtime.

Use the user's explicitly selected external test project, DSH profile, DSH home,
and new or BUGate-owned bootstrap venv. If these targets are absent from the chat,
ask for them before installation; do not infer them from past tasks or use BUGate
core as the SUT. Preserve unrelated profiles/plugins and task facts.

Run python3 ARTIFACT_DIR/install_dsh.py --package
ARTIFACT_DIR/bugate-deepseek-harness-2.0.0-dev.2.tgz --venv BOOTSTRAP_VENV
--project TEST_PROJECT --dsh-profile PROFILE --dsh DSH_EXECUTABLE
--dsh-home DSH_HOME, substituting each explicit absolute path and quoting safely.
Then use BOOTSTRAP_VENV/bin/bugate doctor integrations with those same target flags
and --json. Report installation and hydration separately. Initialize a task only
when its v2 Profile and scope are supplied; do not convert v0.x/old task formats.

On failure save the exact error and owned pending-operation state. Do not delete
user files or retry by inventing a new target. Explain remove deepseek-harness and
--rollback for owned integration recovery. Do not publish any release.
```

The bundle contains an offline Python wheelhouse. DSH peer dependency resolution
can require registry access. See [installation](docs/DEEPSEEK_HARNESS.md) for exact
commands and the tested execution boundary.
