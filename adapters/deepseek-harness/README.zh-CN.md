[English](README.md)

# DeepSeek Harness 适配器

原生 `@bugate/deepseek-harness` bundle 面向 DSH **0.2.2-alpha.2**，已核对官方源码
`f3803c96974ad4a94195fb97e5c637e9c2a3afe3`，使用共享 Python 核心 **2.0.0a1**
和规范协议 **2.0.0**。尚未公开发布，因此 manifest 的发行状态保留 `planned`。

参见[安装使用](../../docs/DEEPSEEK_HARNESS.zh-CN.md)、
[插件配置](plugin/README.zh-CN.md)和[运行时测试](tests/README.zh-CN.md)。

`plugin/src/index.ts` 实现真实 Cordis `apply(ctx, config)`。可等待的
`agent/created`、`agent/pre-step` 准备核心上下文；同步 `systemPrompt.context.text`
原样返回经校验的核心 render。`tools/pre-execute` 取得当前评估和任务租约，
同步 `tools.guard` 只能进一步拒绝。任务级 epoch 使其他 Agent 的旧 ticket 和上下文失效。
`agent/turn-stopping` 对显式提交的声明进行次数、耗时和无进展边界内的返工。

Python 始终通过配置的 filesystem/subprocess provider 执行，不将 opaque targetKey
当成路径。bundle 资源和绝对 Python 安装路径须有明确的宿主到 execution world 映射；
未映射的浏览器或远端环境明确报能力错误。工作区路径先解析、检查边界，再映射为
进程路径。核心返回有限文件见证，适配器用 provider 读取的原始字节、需求源和完整
文件/目录集合逐项核对；仅锁文件一致不足以证明同一运行环境。只豁免 write.lock
和核心声明的 `.bugate/runtime/` 控制子树，会话 pin 存在其 `deepseek-harness/` 目录。真实执行验证使用 macOS 官方 local provider；另用具有 opaque key 的映射
provider 验证路径处理。不宣称未配置的远端或浏览器环境可执行 Python。

受管范围包括已注册的 write/edit/str_replace_editor/bash 和 BUGate 变更、执行工具。
其他宿主工具需要宿主分类策略。外部进程或完全控制同一文件系统的主体可在租约外改写
事实；哈希和会话绑定不是对该主体的防伪边界。普通导入证据不能升级为宿主观测。
`bugate_run_test` 调用核心单次执行采集，等待证据持久化完成才返回结果。

真实联调使用官方 DSH 服务和确定性模型 fixture，证明生命周期、集成与本地真实命令
执行；不冒充 live-model 的业务分析效果。

模型请求前还通过 awaited `agent/request` 刷新；若模型准备期间发生并发变化，官方
`llm/stream` 接缝拒绝该请求，模型不会收到缺失或陈旧的协议上下文。
