# BUGate DSH v2.0.0-dev.1 prerelease

[简体中文](dsh-v2.0.0-dev.1.zh-CN.md)

This is the independent native DeepSeek Harness distribution. The stable legacy
Claude/Codex release remains v0.4.5; its assets and tags are unchanged.

- Native package: `@bugate/deepseek-harness@2.0.0-dev.1`.
- Shared Python core: `bugate-core==2.0.0a1`; Protocol: `2.0.0`.
- Host target: DSH `0.2.2-alpha.2`, Node `>=24.9.0`.
- Published offline wheelhouse: **macOS arm64, CPython 3.13 only**.
- Contents: compiled plugin, canonical schemas/methods/Skill, Python wheel,
  offline Python dependencies, standalone installer and checksums.

Implementation acceptance passed all 49 HC/AC items. Real DSH tests use a
deterministic model fixture, including independent installed-package execution;
this is not live-model effectiveness evidence. See the [delivery report](../reports/DEEPSEEK_HARNESS_DELIVERY_2026-10-29.md).

## Installation

Copy the [installation prompt](../../INSTALL_DSH_PROMPT.md) into an Agent chat
opened in your external test repository. It verifies release bytes, uses isolated
host/bootstrap/profile directories and preserves existing task facts. Installation
alone does not create a sourced business Profile or establish real task hydration.

## Release contract

The immutable annotated tag is `dsh-v2.0.0-dev.1`, matching the native package
version. Main and tag CI must both pass, including the unchanged legacy chain
and the new Linux DSH job. Linux CI builds/validates a Linux wheelhouse for audit;
this release publishes only the already validated macOS wheelhouse.

Public assets are `bugate-deepseek-harness-2.0.0-dev.1.tgz`,
`bugate_core-2.0.0a1-py3-none-any.whl`, `install_dsh.py`, `build-manifest.json`,
`SHA256SUMS`, and the English/Chinese installation prompts. The five package
assets come from one accepted build. Prompt files are versioned documentation.
After publication, download the public assets, verify all package checksums,
and perform a new standalone install and real-runtime acceptance.

Runtime and provenance limits remain those in the delivery report. Windows,
live-model behavior and arbitrary remote execution worlds are not claimed.
