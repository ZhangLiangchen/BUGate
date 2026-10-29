# BUGate DSH one-prompt installation

[简体中文](INSTALL_DSH_PROMPT.zh-CN.md)

Copy the following into an Agent chat in the external test repository.

```text
Install BUGate DSH v2.0.0-dev.1 in the current external test repository and verify installation. This authorizes execution, not advice only.

Release: https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.0.0-dev.1
Asset base: https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.1

1. Confirm this is the intended external test repository, not BUGate core. Preserve dirty work and existing Claude/Codex/DSH configuration. Require macOS arm64, CPython 3.13, Node >=24.9.0 and npm. Report missing prerequisites or ABI/platform mismatch instead of substituting another package.
2. Create or reuse an explicitly owned project-local .bugate-dsh-2.0.0-dev.1/ state directory. Keep downloads, host, venv and DSH home there, record ownership and never overwrite an unowned directory. Do not commit generated files.
3. Download bugate-deepseek-harness-2.0.0-dev.1.tgz, bugate_core-2.0.0a1-py3-none-any.whl, install_dsh.py, build-manifest.json and SHA256SUMS from the asset base. Verify all four checksum entries. Also require tgz SHA-256 0a4f7ddbcb0a035f603823f82da14e399ffa6bd236afeed3879625e8f626baab and installer SHA-256 5680d650f4e72e705e19f3e3618076999341e58f84eefe94a6aceff5b7f3bdd2. Execute no downloaded code before verification.
4. npm install --prefix into state/host using exact @deepseek-ai/dsh@0.2.2-alpha.2 and pnpm@11.28.5. Temporarily prepend host/node_modules/.bin to this process PATH; do not upgrade global DSH.
5. Run the verified install_dsh.py with python3.13: --package <downloaded tgz>, --python <absolute Python3.13>, --venv <state/bootstrap>, --project <current repository>, --dsh-profile bugate, --dsh <isolated host dsh>, --dsh-home <state/dsh-home>.
6. Run bootstrap/bin/bugate doctor integrations with the identical project/profile/dsh/dsh-home and --json. Repeat identical installation to verify idempotence. Retain installation/doctor JSON, versions, paths and checksum results.
7. Do not invent a SUT Profile, requirements, Oracles or test results; do not connect retired HyperTest services. Ordinary v2 operations need no Memory service. Installation can complete without a business Profile; real task initialization requires sourced inputs. Preserve hydration=unobserved instead of claiming E2E acceptance.
8. Report installation status, exact CLI/DSH/profile paths, doctor result and launch command for the same DSH home/profile. Multiple tasks require explicit task-path selection. On failure retain journals and facts, report the concrete error and recovery entrypoint; do not blindly reinstall or delete evidence.
```
