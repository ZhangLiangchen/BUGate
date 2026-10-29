[English](REPORT.md)

# DSH 交付验证

**DSH 负责范围 PASS。** 本次新包为 `bugate-deepseek-harness-2.0.0-dev.2.tgz`，
SHA256 `1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0`。
核心版本 `2.0.0a2`，协议摘要
`sha256:053717ac25e49aa4a03e2a11176c3e9263d1a5d89b0388f0eaaa3a7b568fd77e`。

| 验证 | 实际结果 | 证据 |
|---|---|---|
| 真实 DSH 服务、桥接、生命周期 | 22 PASS，0 FAIL/SKIP；50 次模型 turn、真实压缩、恢复、子 Agent/preset 隔离、PTC、返工、provider 边界与取消 | [逐项映射](runtime-result.json)、[原始日志](runtime-final.log) |
| 安装器边界 | 15 PASS，含 v2 精确核心版本、v0.x 与版本注入拒绝 | [结果](install-boundaries-result.json)、[日志](install-boundaries.log) |
| 干净安装生命周期 | 19 步预期结果全部通过；重复安装、doctor、损坏拒绝、受控 v2 升级、rollback、卸载及两类故障注入恢复 | [结果](clean-install/result.json)、[命令](clean-install/commands.json) |
| 实际已发布 v2 升级 | 9 步通过；公开 dev.1 升级本次 dev.2，旧绑定精确报 `PROTOCOL_MISMATCH`，新任务 prepare，原始归档回滚及保留任务卸载 | [结果](published-upgrade/result.json)、[命令](published-upgrade/commands.json) |
| 无源码访问权限的安装 | 产物复制到 `/tmp`，OS sandbox 禁止读取整个仓库后，全新 bootstrap 安装成功 | [结果](source-denied-install/result.json)、[输出](source-denied-install/install.stdout) |
| 无源码访问权限的安装包真实 runtime | 27 次确定性模型请求、2 条真实 Python 测试、已持久化宿主观测证据，当前 requirement 声明 satisfied | [结果](standalone-runtime/result.json)、[runtime 日志](standalone-runtime/runtime.log)、[测试日志](standalone-source-denied.log) |

真实安装模块与官方 DSH profile runtime 的模块身份一致；Python 从独立 venv 加载。
加载插件前先断言读取源码仓库返回 OS 权限错误，因此不只是清空 `PYTHONPATH`。

现场 DSH `0.2.2-alpha.2`、Node `v26.8.1`、Python `3.13.3`、macOS arm64。
官方源码 checkout 为 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`。
[环境与接口记录](environment-and-interfaces.json) 保存精确文件、行号、摘要；
同步 context/guard 与可等待 created/pre-execute/turn-stopping 均按已安装包复核并实际执行。

适配器 `output.schema` 是宿主工具展示 envelope，完整结果解释前仍强制 canonical Schema
验证，不构成第二份质量规范。Skill 投影包含 canonical 公共质量规则，握手版本读取生成的
compatibility 数据。安装收据按归档锁定精确核心版本，支持真实 v2 回滚，不解释或迁移旧任务。
安装原子写同时 fsync 文件与父目录。

初次 runtime 日志保留了并行修改 canonical 资源时的 `PROJECTION_STALE`；协议冻结重建后
22 项全部通过，未放宽断言消除失败。只改版本元数据的事务 fixture 与实际发布归档升级分开报告。
旧发布包只作为显式升级输入；没有冒充新产物，也没有重新发布。

未做 live-model 验证。以上为真实 DSH runtime + 确定性模型 fixture + 真实本地 Python 执行。
远程/浏览器执行及未分类第三方写工具不在本次已验证 provider/guard 范围。
DSH 范围没有未解决的测试失败。
