# BUGate v2

English | [简体中文](README.zh-CN.md)

BUGate is an evidence-first testing quality protocol and Python evaluator.
**DeepSeek Harness is its only product host.** BUGate evaluates business
understanding, testability, Oracle/case traceability and scoped completion;
DSH executes tools and owns agent lifecycle.

The current source delivers `bugate-core 2.1.0a1` and
`@bugate/deepseek-harness 2.1.0-dev.1`, with exact Protocol `2.1.0-dev.1` digest binding.
This is a breaking prerelease. Old hooks, installers, adapters and artifact
formats are retired. Automatic migration from v0.x or pre-refactor tasks is not
supported. Existing releases/tags are preserved; this build does not replace them.

The acceptance scope contract fixes the user’s finite goals and required branches.
BUGate requires sufficient business evidence within that scope. Extra ideas stay
non-blocking suggestions until an actual user request changes the contract.
Execution completion and functional acceptance are reported separately. See
[acceptance scope and examples](docs/ACCEPTANCE_SCOPE.md).

## Install and use

Use the newly built bundle, bootstrap installer and checksums together. Follow
the [installation and usage guide](docs/DEEPSEEK_HARNESS.md) or paste the
[one-step installation prompt](INSTALL_DSH_PROMPT.md) into your agent.
Installation targets an explicit external test repository and DSH profile.
It never requires copying files from a BUGate checkout.

The compatibility target is DSH `0.2.2-alpha.2`, Node `24.9.0+`, Python `3.11+`
and a local POSIX execution provider. The current platform result and wheel ABI
are recorded with the build; do not infer untested platform support. Windows is
not supported. Bundled Python dependencies install offline; the DSH plugin manager
may need registry access for host peer dependencies.

```sh
bugate init --project /absolute/test-repo --task TASK-1 --sut-profile /absolute/profile.json --json
bugate protocol prepare --task /absolute/test-repo/test-task/TASK-1 --json
bugate validate --task /absolute/test-repo/test-task/TASK-1 --json
bugate assess --task /absolute/test-repo/test-task/TASK-1 --claim claims/design.json --json
```

A zero exit code, model-reported pass or content hash does not prove a requirement.
A complete test delivery may contain genuine product failures. Claims are assessed
against exact current input snapshots and source-backed Oracles. No memory service
is required.

## Documentation

- [Capabilities and boundaries](CAPABILITIES.md)
- [Method](docs/qa-methodology/METHOD.md) and [workflow](docs/qa-methodology/SOP.md)
- [Protocol architecture](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.md)
- [Contributor setup](CONTRIBUTING.md) and [development prompt](INIT.md)
- [Current implementation contract](docs/reports/scope-convergence/CONTRACT.md) and [acceptance matrix](docs/reports/scope-convergence/ACCEPTANCE_MATRIX.md)
- [Scope architecture decisions](docs/reports/scope-convergence/ARCHITECTURE.md)
- [Prior v2-only delivery](docs/reports/v2-dsh-only/DELIVERY.md)

`protocol/v2/` is the sole specification source; `src/bugate/` evaluates it;
`adapters/deepseek-harness/` supplies the native plugin; `packaging/` builds the
finite release payload. Real SUT data and secrets stay outside this repository.

[MIT license](LICENSE).
