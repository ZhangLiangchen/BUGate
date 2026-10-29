# 发布包：已安装 DSH runtime 验证

[English](REPORT.md) | 简体中文

PASS：重新构建的发布包通过真实 DSH standalone 集成测试，内部包含两个确定性流程。
这是新包的重新执行证据，不是把此前实现包的通过结果改标为新包。

| 身份 / 结果 | 实际观察 |
|---|---|
| 构建输入提交 | `062b03f2895a7ddabe7ee848d5b8f047a1826da5` |
| 原生 bundle SHA-256 | `0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c` |
| 组件 | 协议/原生 `2.1.0-dev.1`；Python `2.1.0a1`；已发布 DSH 包 `0.2.2-alpha.2` |
| 测试命令 | `node --test adapters/deepseek-harness/tests/standalone-package.test.mjs` |
| 结果 | exit 0；1 项集成测试通过；0 失败/跳过；171.831 秒 |
| 运行环境 | macOS arm64，实际安装插件与隔离安装的 Python venv |
| 源码隔离 | OS sandbox 禁止读取源码仓库；加载插件前读取 `pyproject.toml` 的探针以 EPERM/EACCES 被拒绝 |
| 模型 | 确定性 fixture 驱动真实 DSH Agent/工具/provider/会话服务，未运行 live model |

本次使用新包的[干净安装](../package/clean-install/)。精确环境、驱动文件摘要和命令计时见
[command.json](command.json)。Profile resolver 为插件与宿主解析到同一个 DSH LLM
模块，Python 模块身份确认位于安装 venv 中。兼容元数据中的 DSH commit 是源码参考，
不表示本轮证明 npm 包与某个本地检出源码构建逐字节一致。

| 流程 | 必需用例实际结果 | 需求判定 | 执行完成 | 建议 | 诊断 |
|---|---|---|---|---|---|
| 预期行为 | 2 通过 | satisfied | satisfied | 4 条，不阻塞 | 无需诊断 |
| 故意引入产品缺陷 | 1 通过、1 失败 | unsatisfied | satisfied | 4 条，不阻塞 | 2 次已观察，第三次启动前拒绝 |

临时通用 SUT 是整数边界函数，有限请求明确包含正数和负数分支。两个流程均主动
尝试把无关安全场景加入有效计划并执行：计划更新被拒绝，描述性建议保留，现有 inventory
不变，禁止命令的执行标记文件不存在。已接受用例绑定精确范围/计划/环境执行，原始
证据在工具结果返回前持久化。

完成后又记录两条建议；同一声明字节和授权快照继续有效，独立不可变评估记录仍为
satisfied，未触发完成返工。故障 SUT 中失败 Oracle 仍为功能失败。两次允许的辅助诊断
不计入验收用例，下一次收到 `DIAGNOSTIC_LIMIT_REACHED`。

[结果 JSON](installed-runtime-result.json)分别列出覆盖、功能结果、阻塞代码和建议 ID。
原始输出包括 [Node 测试日志](installed-runtime.log)、[子 runtime 日志](installed-runtime-child.log)、
[会话 trace](session-traces/index.json)及复制的通用[通过](generated-project/) /
[失败](generated-failure-project/)任务工作区，字节摘要见 [evidence-sha256.json](evidence-sha256.json)。

本结论仅覆盖以上发布包 runtime 检查。既有完整核心/runtime 回归仍按冻结范围收敛报告
标注其原提交和包，不宣称本轮重跑全部。未变更驱动中的历史测试编号和映射字段仅用于
追踪，不新增强制场景矩阵。本结果不证明 live-model 效果、远程 provider 行为或未执行平台。
