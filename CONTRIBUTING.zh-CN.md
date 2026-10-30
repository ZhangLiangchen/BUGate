# 参与开发

[English](CONTRIBUTING.md) | 简体中文

先读 [AGENTS](AGENTS.zh-CN.md)、[METHOD](docs/qa-methodology/METHOD.zh-CN.md)、
[SOP](docs/qa-methodology/SOP.zh-CN.md)、[协议指导](docs/qa-methodology/BUGATE_2_0_PROTOCOL_GUIDE.zh-CN.md)
和 [TypeScript 计划](docs/plans/TYPESCRIPT_CORDIS.zh-CN.md)。实施前保存实际需求、必要不变量、可观察 Oracle 和有限验收映射；历史测试数量不是通用门槛。

需要 Node 24.9.0 或更高版本。按仓库 lockfile 安装并编译：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm test
node scripts/check_distribution.mjs
node --test adapters/deepseek-harness/tests/core-service.test.mjs adapters/deepseek-harness/tests/runtime-node.test.mjs adapters/deepseek-harness/tests/team.test.mjs
node packaging/build.mjs --out-dir /absolute/path/bugate-package
node scripts/check_distribution.mjs --bundle /absolute/path/bugate-package/bugate-deepseek-harness-2.2.0-dev.1.tgz
```

workspace 包含共享核心、CLI、安装器和原生插件。核心和插件消费相同的规范字节。构建只清理自有输出目录，生成有限资源/依赖清单和 SHA256SUMS。lockfile 锁定 DSH/Cordis/官方 Team 依赖；安装器调用真实 DSH 插件管理器，不取代宿主调度。

仓库测试使用临时通用 fixture。真实 SUT 源码、环境、秘密及任务证据放在外部工作区。保留真实失败、skip、xfail、未观察结果和其他写者的改动。验证 subprocess/world 映射与可等待持久化；mock context 不能单独证明宿主支持。

独立验收以操作系统限制源码读取和可执行文件，运行已安装的精确产物。参见[安装说明](docs/DEEPSEEK_HARNESS.zh-CN.md)与 [CI](.github/workflows/ci.yml)。记录实际平台、包摘要、命令与结果，区分确定性 runtime 与 live-model 结果。框架文档保持中英文对应；未经授权不发布 release。
