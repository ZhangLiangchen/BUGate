# Contributing

English | [简体中文](CONTRIBUTING.zh-CN.md)

Read [AGENTS.md](AGENTS.md), the [method and SOP](docs/qa-methodology/README.md),
and the [current documentation index](docs/README.md). Record a change-specific
contract and evidence mapping; a historical delivery contract is not a standing
requirement for unrelated changes.
Use temporary generic fixtures; do not commit SUT sources or secrets. The Protocol
is canonical. Changes to Schema, MethodSpec, reasons or snapshots require positive,
negative and stale-input tests plus regenerated DSH projections.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests/v2 -v
.venv/bin/python -m unittest discover -s tests/distribution -v
.venv/bin/python tests/integration_install/test_install_boundaries.py
```

For the native adapter, install exactly `@deepseek-ai/dsh@0.2.2-alpha.2`,
`typescript@5.9.3`, `ajv@8.17.1` and `@types/node@24.10.0` into an isolated npm
prefix, then link its `node_modules` into `adapters/deepseek-harness/plugin/`.
Use Node 24.9.0+ and pnpm 11.28.5. The CI file records the reproducible commands.

```sh
npm run build --prefix adapters/deepseek-harness/plugin
node --test adapters/deepseek-harness/tests/bridge.test.mjs adapters/deepseek-harness/tests/runtime.test.mjs
.venv/bin/python packaging/build_dsh.py --out-dir dist/local-build
.venv/bin/python scripts/check_distribution.py --bundle dist/local-build/bugate-deepseek-harness-2.1.0-dev.1.tgz
```

Build on the target Python/platform ABI. Run standalone package installation and
real DSH runtime acceptance with the resulting bundle; a source-tree unit test is
not standalone evidence. Preserve unrelated user configuration. Record actual
commands and their results, including failures. Keep documentation bilingual.
No release publication occurs during normal build/test work.
