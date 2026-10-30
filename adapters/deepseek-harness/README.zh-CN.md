# DeepSeek Harness 接入

[English](README.md) | 简体中文

DSH 是 BUGate 唯一产品宿主。Cordis 插件和统一 Node CLI 消费相同 TypeScript 核心；
Protocol v2 继续是唯一机器权威，DSH 拥有 Agent 执行、官方 Team 协作及有界生命周期工作。

按照[安装说明](../../docs/DEEPSEEK_HARNESS.zh-CN.md)，将有限独立 Node 产物安装到
显式外部项目和隔离 DSH profile。bundle 先消费官方 Team profile patch，再注册 BUGate。
不要同时启用第二份独立 Team profile；安装应解析到恰好一个 Team service 和工具集。
Node installer 管理所有权、中断恢复、rollback 和移除，保留已有任务事实及无关 profile。

编写有来源的 Profile，并通过已安装 `bugate init` 新建任务。Profile 固定要求/风险分母，
按需声明有限评审视角及其必要性来源。依据实际用户请求收据确定有限范围、有来源命题和
可观察 Oracle，在受管实现之前发布设计工件。锁定分母内的范围修订需要当前用户选择；
改变分母需要显式新 Profile 版本及新任务。

用原生 `bugate_publish_artifact` 发布真实作者/评审者的内联输出。普通路径导入保留未知/
导入作者状态。由官方 Team 创建所需成员、协调任务、读取精确工件、交换意见与证据，
各成员发布自己的交叉核对处置。BUGate 校验真实宿主来源、上下文策略、作者/评审执行
分离、当前输入/输出绑定及最终执行结算。Team task complete 和消息 accepted 不等于质量通过。

原生插件负责精确绑定、prepared context、模型准入、受管工具 ticket、实际配置的进程
执行及证据发布。不把任意 shell 命令解释成安全测试意图，也不假设父 Agent 局部 guard
自动继承。成员、fork、preset 和恢复后的 Agent 各自独立检查绑定。合法辅助工作和有界
诊断继续与必需场景及不可执行建议分开。

Node 校验/采集通过实际配置的 subprocess world 运行，provider 文件系统见证必须一致。
停止/取消/dispose 排空受管工作；未结算或失败评审不授权。Team 共享 cwd 与建议性
writeScopes 不构成文件系统隔离。保证限于本地受管宿主观察，不保证同权限防篡改或认知独立。

另见[插件配置及边界](plugin/README.zh-CN.md)、[实际验证类别](tests/README.zh-CN.md)
和[规范协议](../../protocol/v2/README.zh-CN.md)。确定性模型驱动的真实 runtime 结果与
live-model 结果单独报告；实际命令及精确产物身份见本次迁移报告。
