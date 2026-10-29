# 发布包验证

[English](REPORT.md) | 简体中文

下列构建与安装范围 PASS。实际已安装 DSH hydration 另见 [runtime 证据](../runtime/REPORT.zh-CN.md)。本文不声称托管 CI、公开 release 可用性或 live-model 验证已完成。

发布 bundle 来自提交 `062b03f2895a7ddabe7ee848d5b8f047a1826da5`，57 项声明的构建输入同时匹配该提交与工作区文件。相比实现提交 `c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60`，仅 `protocol/v2/README` 中英文两份文件变化：修正旧版本文字，没有新增业务规则。见[源码匹配](source-commit-match.json)。

六份产物位于 `dist/release-2.1-dev.1`；[校验清单](checksums.json)记录逐文件字节数与 SHA-256。

| 产物 | SHA-256 |
|---|---|
| Native bundle | `0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c` |
| Python wheel | `e932749f78cc708608d3368cfe75957d9610e1726244862e04ea627d68a0db8c` |
| 安装器 | `2ea459ed326ddde989a16bb9153fd5536a117838dd5d6f5f3a8a3c7bfca2604a` |

原生构建、独立构建、源码边界及 bundle 边界检查均通过。包内包含当前 Python/protocol 资源与必需 wheelhouse；源码边界检查报告 13 个 Python 模块、28 份协议文件。离线 wheelhouse 目标为 CPython 3.13/macOS arm64，本轮未验证其他 ABI。

安装前将 bundle 与五份配套文件复制到新的外部临时目录。20 条生命周期命令全部在操作系统拒读源码仓库的策略下执行；首条探针实际证明读取 `pyproject.toml` 触发 `PermissionError`。见[拒读证明](source-denial-attestation.json)、[精确命令](clean-install/commands.json)、[结果](clean-install/result.json)及[目录定位](locations.json)。

生命周期覆盖干净 bootstrap、重复安装、四次健康 doctor、拒绝任务重绑定、检测过期投影、仅修改版本元数据的受控升级 fixture 与 rollback、卸载/重复卸载、宿主变更失败恢复、拒绝非自有 bootstrap 以及写记录后恢复。原始记录保留预期负向退出码；PASS 表示既有断言成立。Doctor 明确报告 `configuration_verified`、hydration `unobserved`，不作为 Agent 执行证明。

旧 `scope-convergence` bundle 保持不变。其中九条真实旧 v2 归档升级命令本轮未重跑，也不归因给新发布包。此次仅说明修正的重建改变了内容摘要（包括 protocol/evaluator 绑定），组件版本仍使用此前未公开的版本。之前本地 2.1 任务需使用既有显式新任务流程，保留旧历史，不静默迁移。

复现命令（生命周期命令的外部路径取自 `locations.json`）：

```sh
npm run build --prefix adapters/deepseek-harness/plugin
python3 scripts/check_distribution.py
python3 packaging/build_dsh.py --out-dir dist/release-2.1-dev.1
python3 scripts/check_distribution.py \
  --bundle dist/release-2.1-dev.1/bugate-deepseek-harness-2.1.0-dev.1.tgz
python3 tests/integration_install/accept_package.py \
  --package EXTERNAL_ASSETS/bugate-deepseek-harness-2.1.0-dev.1.tgz \
  --dsh /Users/zhangliangchen/.local/bin/dsh \
  --out-dir EXTERNAL_CLEAN_INSTALL \
  --deny-source-root /Users/zhangliangchen/PycharmProjects/BUGate
```

构建原始日志见 [native-build.stdout](native-build.stdout) 与 [build.stdout](build.stdout)，机器结论见 [result.json](result.json)。
