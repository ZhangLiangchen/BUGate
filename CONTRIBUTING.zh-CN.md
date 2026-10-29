# 贡献指南

[English](CONTRIBUTING.md) | 简体中文

阅读 [AGENTS](AGENTS.zh-CN.md) 和当前[合同](docs/reports/v2-dsh-only/CONTRACT.md)。使用动态通用临时 fixture，不提交真实 SUT 源码或秘密。Protocol 是唯一规范；Schema、MethodSpec、reason codes 或快照变更必须增加正例、负例、过期输入测试并重新生成 DSH 投影。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests/v2 -v
.venv/bin/python -m unittest discover -s tests/distribution -v
.venv/bin/python tests/integration_install/test_install_boundaries.py
```

原生适配器依赖精确版本 `@deepseek-ai/dsh@0.2.2-alpha.2`、`typescript@5.9.3`、`ajv@8.17.1`、`@types/node@24.10.0`，安装到隔离 npm prefix，再将其 `node_modules` 链接到 `adapters/deepseek-harness/plugin/`。使用 Node 24.9.0+ 和 pnpm 11.28.5；CI 保存可复现命令。

```sh
npm run build --prefix adapters/deepseek-harness/plugin
node --test adapters/deepseek-harness/tests/bridge.test.mjs adapters/deepseek-harness/tests/runtime.test.mjs
.venv/bin/python packaging/build_dsh.py --out-dir dist/v2-dsh-only
.venv/bin/python scripts/check_distribution.py --bundle dist/v2-dsh-only/bugate-deepseek-harness-2.0.0-dev.2.tgz
```

按目标 Python/平台 ABI 构建。使用实际 bundle 执行独立安装和真实 DSH runtime 验收；源码树单测不等于独立运行证据。保留无关用户配置，记录真实命令与结果（含失败），同步中英文文档。普通构建测试不发布 release。
