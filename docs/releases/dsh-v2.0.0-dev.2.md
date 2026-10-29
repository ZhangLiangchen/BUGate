# DSH v2.0.0-dev.2

English | [简体中文](dsh-v2.0.0-dev.2.zh-CN.md)

Breaking v2-only source and local-build delivery; this document does not publish
or replace a GitHub release. Core: `bugate-core 2.0.0a2`. DSH target:
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

Use [installation](../DEEPSEEK_HARNESS.md) and the [installation prompt](../../INSTALL_DSH_PROMPT.md).
V2 installation upgrade/rollback does not migrate task bindings. A changed
protocol/evaluator requires current inputs and a new explicit task; v0.x automatic
migration is unsupported. macOS arm64 is the local verification target; Linux is
covered by configured CI, not asserted as run by this local delivery. Windows is
unsupported. Wheel ABI/platform compatibility is recorded in the package.

Actual results and proof boundaries are in the [delivery report](../reports/v2-dsh-only/DELIVERY.md)
and [acceptance matrix](../reports/v2-dsh-only/acceptance.json). Deterministic model
fixtures drive a real DSH runtime; they are not live-model effectiveness evidence.
