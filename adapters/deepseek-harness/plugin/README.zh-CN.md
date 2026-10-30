# BUGate 原生 DSH 插件

[English](README.md) | 简体中文

`@bugate/deepseek-harness` 2.2.0-dev.1 是共享 TypeScript 核心及统一 Node CLI 的
Cordis 接入。DSH 是唯一产品宿主。插件直接消费 `@bugate/core` 规范序列化，在配置的
DSH subprocess/filesystem world 中运行已安装 `@bugate/cli`，执行校验、哈希、存储和
进程采集；没有 Python fallback。

包的有序 `dsh.bundle.patch` 先消费原样的官方
`@deepseek-ai/dsh-experimental-agent-team-profile` patch，再注册 BUGate。
官方层挂载 Team service/tools 并禁用直接委派工具。成员、任务板、消息、执行及生命周期
由 DSH 拥有，BUGate 不建立团队调度器。接口锁定 DSH/Team 0.2.2-alpha.2、Cordis
4.0.6-alpha.1；Node 要求 >=24.9.0。experimental Team API 不保证稳定。

## 配置

```yaml
- id: bugate
  config:
    projectRoot: /absolute/external-test-project
    mode: managed
    nodeExecutable: node
    maxRework: 2
    maxReworkMs: 120000
    executionCommands:
      required-tests:
        argv: [node, tests/runner.mjs, logs/results.xml]
        caseIds: [case-from-current-inventory]
        report: logs/results.xml
        purpose: test
```

项目有多个任务时显式选择 `taskPath`。`coreEntrypoint` 可指定宿主拥有的已安装 CLI
绝对路径，不是模型参数。Node 可执行文件及 CLI 安装都必须映射进配置的 filesystem/
process world。opaque target key 不作路径；文件见证比较 provider 可见的实际文件和目录。
不支持的映射明确拒绝。

`context.text` 和 `tools.guard` 同步消费已完成的 prepared 状态。准备、模型准入和评审
最终结算使用真实可等待的 Agent 生命周期接缝。受管写入/执行 ticket 重新检查范围、计划
和当前设计验收。普通读取及来源分析保持可用。managed 模式拒绝任意 `bash`，因为插件不
推断任意 shell 意图；使用绑定必需用例或有界辅助活动的 `bugate_run_test` 配置命令。
advisory 模式不实施阻断工具策略。

## 原生工具与评审来源

工具包括 prepare/status/validate、范围创作、工件/证据导入、建议、评估和配置执行。
统一 CLI 与原生插件使用相同核心规则及 JSON envelope。

`bugate_publish_artifact({artifact: ...})` 接收真实模型工具调用的内联输出，捕获精确
live 官方 Team 成员、Agent/Session、parent、fresh/fork 及继承事件前缀、原始工具参数和
实际收到的同伴消息。`bugate_record_artifact({input: ...})` 只是文件导入；成员导入
Lead 代写文件不建立作者身份。标签、模型名称和 JSON 身份字段不提升为宿主身份。

采用两阶段发布：工具返回前，工件及不可变 `HostObservation` 已持久化，状态为
`published`，还不能授权评审。在可等待的准备/停止/恢复接缝，插件 flush 真实最终 DSH
`tool/result` 并发布不可变 `HostSettlement`。仅完成且匹配的结算可参与评估；发布后
取消、超时、失败和缺少结算保持未完成。冷恢复读取持久化 Session 与核心事实，不相信
Lead 局部缓存。

锁定的 `Profile.team_review_policy` 只选择必要且有来源的视角、上下文策略、交叉核对
要求和有界轮数，不施加通用角色/模型/用例矩阵。评审绑定精确工件及上游依赖快照、范围/
计划、Profile/协议和评估器字节，相关变化使评审失效。`fresh_required` 表示不继承 Lead
事件前缀，不保证认知独立或无法看到共享工作区。同模型成员可以满足可观察执行分离；
不同模型本身不证明独立性。

交叉核对引用精确历史意见和实际收到的 Team 消息；原始发现、反证、限制及处置持久化。
有证据的独立处置可解决发现，不要求全员同意，原始异议继续保留。多数票及 Team task
complete 不替代业务 Oracle；消息 accepted 不代表接收者处理或评审持久化完成。

Team 是进程内共享 cwd，不提供 worktree 隔离；writeScopes 仅建议、owner 不自动释放，
消息没有重试/去重保证。发布/CAS/锁保护配合协议的写入者，不抵御同权限文件系统/进程
篡改。不从确定性 runtime 测试推断模型质量或效率提升。

另见[适配器用法](../README.zh-CN.md)、[协议权威](../../../protocol/v2/README.zh-CN.md)
和[运行验证](../tests/README.zh-CN.md)。

原生观察通道由插件进程内 Ed25519 私钥签名，配置 filesystem provider 仅持久化公钥 pin。签名绑定 task/workspace、操作及完整上下文摘要；CLI 验证已有 pin，不提供创建宿主权威的命令。普通模型 JSON 或自设环境 token 不能制造来源。历史公钥及签名保留以验证恢复记录；同权限直接改写公钥/文件仍在保证边界之外。

fresh Team 会话仍接收 Lead 编写的委派 prompt，其精确字节保存在持久 DSH 会话和
Lead 的真实 spawn 工具调用中。原生记录证明哪个真实成员输出了内容及可观察上下文
策略，不证明文本原创。Lead 可以在 prompt 中放入文本后由成员复述，身份本身不能
证明认知独立或通用识别所有代写形式。

插件通过真正 Cordis 服务注册接缝提供已导出、带类型的 `BugateService`，即
`ctx.bugate`。消费插件声明 `inject: ['bugate']`，可 await
`status(task, signal?)`、`prepare(task, signal?)` 和
`assess(task, claimPath, signal?)`。任务及声明路径属于配置的执行 world。
这些方法与原生质量工具消费同一核心和传输，不开放任意命令或宿主来源签名入口。
Cordis 随拥有者插件 fiber 卸载服务及其依赖消费者；拥有者 dispose 会取消并排空
自身传输，旧引用以 `CORE_CANCELLED` 拒绝，重载提供新实例。质量结果本身不替代
宿主权限和工具准入控制。
