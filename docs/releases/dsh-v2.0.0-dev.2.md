# DSH v2.0.0-dev.2

English | [简体中文](dsh-v2.0.0-dev.2.zh-CN.md)

Breaking v2-only prerelease. Core: `bugate-core 2.0.0a2`. DSH target:
`0.2.2-alpha.2`; Protocol: `2.0.0` with exact content binding.

The current product contains only the Python v2 quality evaluator, canonical
protocol resources, unified CLI and native DeepSeek Harness adapter. Historical
hooks, adapters, role/memory/orchestration runtimes and legacy installer/updater
are removed from the current source and distribution. History and public tags
remain unchanged. Native business/testability/Oracle/reference/modeling checks
replace legacy semantic runtime dependencies; old inputs are rejected.

The new bundle includes compiled native code, generated method projection,
canonical resources, the Python wheel and offline dependency wheelhouse. The
finite resource manifest, wheel entry point and nested bundle inventory are
validated during build. Checksums and a source digest manifest accompany it.

Download this version from the
[GitHub prerelease](https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.0.0-dev.2).
Keep all six assets together so the complete checksum list can be verified:

- [Native bundle](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/bugate-deepseek-harness-2.0.0-dev.2.tgz)
- [Python core wheel](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/bugate_core-2.0.0a2-py3-none-any.whl)
- [Bootstrap installer](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/install_dsh.py)
- [Build manifest](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/build-manifest.json)
- [Source-input digest manifest](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/source-manifest.json)
- [SHA256SUMS](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/SHA256SUMS)

Bundle SHA-256: `1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0`.
Use [installation](../DEEPSEEK_HARNESS.md) and the [release installation prompt](../../INSTALL_DSH_PROMPT.md).
V2 installation upgrade/rollback does not migrate task bindings. A changed
protocol/evaluator requires current inputs and a new explicit task; v0.x automatic
migration is unsupported. macOS arm64 is the local verification target; release
publication is gated on green GitHub CI for the release commit. Windows is
unsupported. Wheel ABI/platform compatibility is recorded in the package.

Local verification passed 96 core tests, four distribution tests, 15 installer
boundary tests, 22 real DSH runtime tests, 19 clean-install lifecycle steps and
nine actual published-v2 upgrade/rollback steps. Installation and the installed
runtime also passed with the OS denying reads of the source checkout: 27 model
requests, two actual tests and a current satisfied requirement claim. All 48
applicable HC/AC entries and three added no-legacy checks passed; AC-33 was
explicitly retired and is not counted as passing.

Actual results and proof boundaries are in the [delivery report](../reports/v2-dsh-only/DELIVERY.md)
and [acceptance matrix](../reports/v2-dsh-only/acceptance.json). Deterministic model
fixtures drive a real DSH runtime; live-model testing was not run. This local
evidence does not substitute for GitHub CI or verification of downloaded release
asset bytes, which are separate publication checks.
