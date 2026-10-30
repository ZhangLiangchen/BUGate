# Contributing

English | [简体中文](CONTRIBUTING.zh-CN.md)

Read [AGENTS](AGENTS.md), [METHOD](docs/qa-methodology/METHOD.md),
[SOP](docs/qa-methodology/SOP.md), the [protocol guide](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.md)
and the [TypeScript plan](docs/plans/TYPESCRIPT_CORDIS.md). Save the actual
requirements, necessary invariants, observable Oracles and finite acceptance map
before implementation. Historical test counts are not universal gates.

Node 24.9.0 or later is required. Install the checked-in lockfile, then compile:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm test
node scripts/check_distribution.mjs
node --test adapters/deepseek-harness/tests/core-service.test.mjs adapters/deepseek-harness/tests/runtime-node.test.mjs adapters/deepseek-harness/tests/team.test.mjs
node packaging/build.mjs --out-dir /absolute/path/bugate-package
node scripts/check_distribution.mjs --bundle /absolute/path/bugate-package/bugate-deepseek-harness-2.2.0-dev.1.tgz
```

The workspaces are shared core, CLI, installer and native plugin. The core and
plugin consume the same canonical protocol bytes. Build cleans only owned output
directories and emits a finite resource/dependency inventory and SHA256SUMS.
DSH/Cordis/official Team dependencies are pinned in the lockfile. The installation
manager calls DSH's actual plugin manager; it does not replace the host scheduler.

Repository tests use neutral temporary fixtures. Real SUT code, environments,
secrets and task evidence stay in external workspaces. Preserve real failures,
skips, xfails, unobserved outcomes and concurrent writers. Test subprocess/world
mapping and awaited persistence; mock contexts alone cannot establish host support.

Independent acceptance runs the installed artifact under OS-enforced source-read
and executable restrictions. See [installation](docs/DEEPSEEK_HARNESS.md) and
[CI](.github/workflows/ci.yml). Record actual platform, package digest, commands
and results. Deterministic runtime and live-model results are distinct. Keep
framework documentation bilingual. Do not publish a release without authorization.
