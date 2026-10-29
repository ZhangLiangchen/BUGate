# Documentation

English | [简体中文](README.zh-CN.md)

BUGate is a host-neutral Python quality evaluator and v2 protocol with one native
product host, DeepSeek Harness. Start with the current guides below. The
canonical machine authority is [Protocol v2](../protocol/v2/manifest.yaml), not
a delivery report or a historical acceptance matrix.

## Current guides

| Purpose | Document |
|---|---|
| Product boundary and contributor rules | [Charter](../CHARTER.md), [AGENTS](../AGENTS.md), [contributing](../CONTRIBUTING.md) |
| Installation, configuration and recovery | [DeepSeek Harness guide](DEEPSEEK_HARNESS.md), [one-step installation prompt](../INSTALL_DSH_PROMPT.md) |
| Finite acceptance scope and result interpretation | [Scope contracts and examples](ACCEPTANCE_SCOPE.md) |
| Method and task workflow | [Method](qa-methodology/METHOD.md), [SOP](qa-methodology/SOP.md) |
| Core/host responsibilities | [Protocol architecture](qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.md), [adapter contract](../adapters/CONFORMANCE.md) |
| Native adapter configuration and testing | [Adapter](../adapters/deepseek-harness/README.md), [runtime test instructions](../adapters/deepseek-harness/tests/README.md) |

The architecture guide retains its `BUGATE_2_0_PROTOCOL_GUIDE` filename for link
continuity; its content describes the current v2 architecture. Current exact
component versions are in the protocol manifest and distribution metadata.

## Verification records

These are versioned observations and decisions, not additional standing rules.
Read each record's commit, artifact digest, command results and proof boundary.
A passing result from one bundle is not evidence for a different bundle.

- [2.1.0-dev.1 release notes](releases/DSH_2_1_0_DEV_1.md) and
  [documentation audit](reports/release-2.1-dev.1/DOCUMENTATION_AUDIT.md) describe
  the publication build and the corrections made before publication.
- [Scope-convergence delivery](reports/scope-convergence/DELIVERY.md),
  [adopted mapping](reports/scope-convergence/ACCEPTANCE_MATRIX.md) and
  [source correction](reports/scope-convergence/SOURCE_CORRECTION.md) preserve
  the earlier implementation/verification snapshot. Its SC examples are not a
  universal required scenario list; its original package hashes remain unchanged.
- [2.0.0-dev.2 release notes](releases/dsh-v2.0.0-dev.2.md) and
  [v2-only delivery](reports/v2-dsh-only/DELIVERY.md) document that historical
  release. Its component versions and retired acceptance IDs apply only there.

Historical migration, multi-host and legacy-runtime discussions do not authorize
restoring those product paths. New work follows the current protocol and the
user's actual goals. Keep acceptance scope finite and record actual execution,
functional results, blockers and optional suggestions separately.
