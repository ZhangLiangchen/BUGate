# BUGate v2 + DSH 单一路线交付报告

[English](DELIVERY.md) | 简体中文

**最终状态：COMPLETE。** 48 项适用 HC/AC 全部通过；AC-33 因本次明确范围变更退役，不计 PASS。新增 3 项无 legacy 依赖验收通过。新产物已在操作系统禁止读取源码仓库的条件下独立安装，并通过真实 DSH runtime 联调。

日期：2026-10-29。实现提交：`e40ce1f739899acf1b88084c537ae62b871282b6`，分支 `codex/v2-dsh-only`；起点为 `61e1327a7bf01505cc82760782ffa1272ccaa03f`。报告与原始证据另作提交。本次未推送、发布、覆盖 release 或改写历史。

## 实际交付

- 当前产品只保留宿主无关 Python v2 核心、唯一 Protocol/Schema/MethodSpec/reason codes、统一 CLI、DSH 原生插件以及必要构建和测试。删除 249 个旧受控文件，包含旧 hooks、宿主投影、installer/updater、角色/编排/记忆运行时与专属旧测试。详见[删除与替代清单](CLEANUP.md)。
- 删除 `legacy.py`、`legacy_precode` 运行分支以及对旧脚本的导入、调用和摘要依赖；旧格式明确拒绝。质量规则以原生 v2 对象保留并补强，详见[语义映射](CORE_SEMANTICS.md)。公共不变量与声明维度来自唯一 `quality.json`，DSH Skill 由规范生成。
- 保留引用、快照、证据来源、声明评估、PreparedProtocolContext、原子写入及并发冲突处理；同步 context/guard 与可等待事件按真实宿主接口接入，证据落盘先于依赖它的结果返回。
- 新分发使用明确成员清单，验证源码、实际 wheel 与 bundle；唯一 CLI 入口为 `bugate`。轮子包含 12 个核心模块、24 个协议资源及 5 个 metadata 文件，bundle 共 46 个成员，无旧宿主入口或旧运行时。
- 中英文安装、使用、架构、能力、贡献指南和安装 Prompt 均已更新。[冻结需求与决策](CONTRACT.md)、[集成复核问题](REVIEW.md)保留本次判断依据。

## 本轮实际验证

| 验证对象 | 结果 | 原始证据 |
| --- | --- | --- |
| 核心、语义、负例、边界 | 96 PASS | [core-tests.log](evidence/core-tests.log) |
| 源码与分发防线，包括旧别名注入负例 | 4 PASS；实际包扫描 PASS | [测试](evidence/distribution-tests-final.log)、[成员扫描](evidence/distribution-final.json) |
| 实际 DSH 服务及生命周期 | 22 PASS，0 FAIL/SKIP | [runtime-final.log](dsh/runtime-final.log) |
| 安装器边界 | 15 PASS | [install-boundaries.log](dsh/install-boundaries.log) |
| 干净安装、重复安装、恢复、升级、卸载及 rollback | 19 步通过 | [结果与映射](dsh/clean-install/result.json)、[全部命令](dsh/clean-install/commands.json) |
| 公开 dev.1 → 新 dev.2 → 原归档回滚 | 9 步通过 | [结果](dsh/published-upgrade/result.json)、[命令](dsh/published-upgrade/commands.json) |
| OS 拒读源码的独立安装 | PASS | [sandbox 与命令](dsh/source-denied-install/result.json) |
| 同条件下的安装包真实 DSH 完整流程 | PASS：27 次模型请求、2 条实际测试、持久化证据及当前 requirement 声明 satisfied | [完整结果](dsh/standalone-runtime/result.json)、[运行日志](dsh/standalone-runtime/runtime.log) |

逐项原 HC-01～HC-15、AC-01～AC-34 及新增 NL-01～03 的要求、结果、测试选择器见[验收表](ACCEPTANCE.md)和[机器映射](acceptance.json)。原有非兼容核心测试保留；被删除的历史测试明确按已退役行为归类，没有作为通过数。

核心实际命令为 `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests/v2 -v`；分发检查为 `.venv/bin/python scripts/check_distribution.py --bundle dist/v2-dsh-only/bugate-deepseek-harness-2.0.0-dev.2.tgz`。完整 DSH 编译、真实 runtime、安装和恢复命令见 [DSH 报告](dsh/REPORT.zh-CN.md)及相邻命令记录。

## 版本、产物与安装

核心 `2.0.0a2`；DSH bundle `2.0.0-dev.2`；Protocol `2.0.0`，精确摘要 `sha256:053717ac25e49aa4a03e2a11176c3e9263d1a5d89b0388f0eaaa3a7b568fd77e`。

现场目标为 DSH `0.2.2-alpha.2`，源码 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`，Node `26.8.1`、Python `3.13.3`、macOS `26.5.1` arm64；[接口证据](dsh/environment-and-interfaces.json)记录实际接口位置和摘要。

产物目录为仓库的 `dist/v2-dsh-only/`，不是旧 release 下载链接：

| 产物 | SHA-256 |
| --- | --- |
| [DSH bundle](../../../dist/v2-dsh-only/bugate-deepseek-harness-2.0.0-dev.2.tgz) | `1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0` |
| [Python wheel](../../../dist/v2-dsh-only/bugate_core-2.0.0a2-py3-none-any.whl) | `56813e878d17defd62120e69b17c58095e598a9292c5668e1cee80de5eb3b5d9` |
| [bootstrap installer](../../../dist/v2-dsh-only/install_dsh.py) | `85d354ebcb36bd901bafd49be1ae38fd6b0ac4dc775957cabec3b37c4494d67f` |

完整[校验和](evidence/SHA256SUMS)、[构建清单](evidence/build-manifest.json)及 [52 项源码输入摘要](evidence/source-manifest.json)已保存。使用[一键安装 Prompt](../../../INSTALL_DSH_PROMPT.zh-CN.md)或[安装指南](../../DEEPSEEK_HARNESS.zh-CN.md)，将这些新产物一起复制到 ABI 匹配的目标环境。

升级与任务迁移分开：实际 dev.1 安装可以升级到 dev.2；旧任务锁在新协议下明确返回 `PROTOCOL_MISMATCH`，不改写旧锁。回滚恢复原归档后旧任务可再次 prepare。v0.x 和旧工件自动迁移不受支持。卸载和恢复保留任务事实、无关配置及插件。

## 边界与恢复

无未完成的适用工程验收项或外部阻塞。真实 runtime 使用确定性模型 fixture；**live-model 测试 NOT_RUN**，不宣称模型效果、缺陷发现率或 Token 收益。Linux CI 已更新但本地没有运行 Linux；Windows、浏览器 Python 和远程 provider 不在已验证支持范围。离线 wheelhouse 受记录的平台/ABI 限制，DSH peer dependency 安装可能需要网络。

哈希只证明一致性；同权限文件所有者可篡改本地证据。门禁只覆盖明确受管工具面，不是整台机器的安全隔离。真实产品失败、零用例、全跳过、取消与环境失败保留区别。

本轮初始并行修改规范时触发的 `PROJECTION_STALE` 保留在 [runtime-initial.log](dsh/runtime-initial.log)，冻结规范重建后完整测试通过；没有通过放宽断言制造全绿。[集成审计](evidence/integration-audit.json)确认历史 tag 未变、既有嵌套工作树仍干净、构建输入无漂移。回溯旧实现使用原 Git 历史；本次不清理其他项目或全局宿主配置。
