# BUGate Protocol v2

[English](README.md) | 简体中文

Protocol `2.2.0-dev.1`、TypeScript `@bugate/core 2.2.0-dev.1` 和 DSH bundle `2.2.0-dev.1`
各自具有版本身份；每个任务锁定精确协议摘要。
本目录是 canonical 资源；Node 打包包含相同字节。`manifest.yaml` 和
`reason_codes.yaml` 使用 JSON 语法，属于合法 YAML 1.2。

`manifest.yaml` 所列 Schema 使用 Draft 2020-12，由锁定的
`ajv@8.17.1` 校验。Artifact 按 artifact_type 严格约束内容。
MethodSpec 定义质量，不调度工作。共享 TypeScript 评估分别保留确定性、语义评审和运行维度；
适配器原样注入 `render.content`。

初始化接收 JSON Profile。Profile 来源路径相对于显式 project，工件/证据/声明路径
限定于任务。来源摘要必须取真实文件字节。对象摘要为 `kind + '/v1'`、NUL、按键排序
且无空白的 UTF-8 JSON 的 SHA-256；排除顶层 `digest` 和 `request_digest`。文件摘要
直接对原始字节计算 SHA-256。不接受浮点数和超出 JavaScript 安全范围的整数。
`protocol.lock.json` 统一使用 `digest`，不并存另一套 `sha256` 字段名。

授权快照包含当前任务事实、有效范围、引用来源字节、精确绑定与评估器源码字节；
排除声明、派生评估/上下文、草稿、建议、未引用用户收据、历史与事务记账。请求摘要
包含固定 Claim 正文和当前建议的 `advisory_digest`：新增建议产生独立评估记录，
但不使既有声明或执行失效。
status 重新评估声明，历史 satisfied 字段不能授权。原子写和非阻塞任务锁保护配合协议
的本机写入者；不受管外部并发写入可能冲突，不宣称全文件系统原子性。

`ScopeRequest` 记录用户来源选择，`ScopeContract` 绑定有限目标、流程/分支、要求、
Oracle、必要场景和辅助边界，`Suggestion` 记录不含可执行计划的可选想法。当前 inventory
是唯一计划，必需用例引用精确范围及场景。范围变更保持锁定 Profile 的要求/风险分母；
变更分母需要明确的新 Profile 版本与新任务。用户收据不替代 Profile 声明的业务证据，
也不越过宿主权限。执行和声明检查当前范围、计划、环境与快照；旧证据和失败继续保留，
但本版本要求范围变更后重新执行。场景必要性仍需有来源语义评审。

`evidence record` 保留导入/人工/模型来源并拒绝 host_observed。`execution run` 实际
执行 argv，记录进程、输出、新生成的 JUnit 报告、请求用例、结果与输入摘要。可信度
限于本机受管观测，不能防住同权限修改证据与采集收据的操作者。哈希只证明字节一致，
不证明事实为真。产品失败可满足执行/交付声明，失败需求仍不可标已验证；零用例、
全跳过和仅 xfail 的运行不能建立验证结论。

评估器只接受原生 v2 对象。历史文件格式与 `legacy_precode` 由 Schema 明确拒绝，
没有转换器或降级路径。公共质量不变量与声明适用维度唯一保存在 `rules/quality.json`；
MethodSpec 引用该约束集合，上下文与 Skill 投影读取相同来源。

业务事实必须有声明来源和当前语义评审。每个 Oracle 必须有可测性策略；用例继承
inventory 的来源计划，也可用显式 `source_refs` 收窄。必需正文不能为空白或未决占位词。
Inventory 用例本身包含可读前置条件、预期观测和测试层理由。

Profile 可通过只接受 `true` 的 `quality_requirements` 收紧要求：`domain_model`、
`state_model`、`dimension_matrix`、`adversarial_review`、`independent_review`。
四种模型工件具有原生类型 Schema、显式交叉引用；只要提供就必须绑定当前语义评审。
纳入的对抗发现必须映射到实际用例；范围内未决发现和残余风险阻止设计完成。
范围外残余项引用已记录的不可执行建议，不携带范围内要求/风险引用，也不阻塞完成。
独立评审需要实际作者与评审执行的持久化 DSH Team 宿主观察、执行角色分离以及可观察的上下文策略；名称和导入的模型字段不能证明独立性。
可选模型不隐式成为默认必需项，其正反例与普通 v2 测试一同验证。

Node 测试运行时生成中性临时 fixture 和执行日志。普通 prepare/validate/assess 不依赖 Memory Bus。

`Profile.team_review_policy` 固定有限且有来源的视角、fresh/declared 上下文、有界轮数与是否必需交叉核对。原生内联 `bugate_publish_artifact` 工具绑定真实模型工具输出与精确 live Team 成员；路径导入不获得作者身份。`HostObservation` 单向引用最终输出摘要、精确绑定与评估器身份，并保存原始调用、收到的消息及规范摘要。可观察执行分离与语义评审质量分开，不保证认知独立。

评审输入快照按被审目标的上游依赖闭包计算：精确绑定/评估器、Profile 来源、范围/计划、相关设计工件、实现文件字节，以及明确引用的执行证据与文件；排除评审、宿主观察/结算、声明、评估、草稿、建议、历史和无关的下游执行/报告/知识/诊断，避免评审哈希自身及未来执行造成设计评审循环失效。相关输入变更使旧评审失效。交叉核对引用不可变历史意见与实际收到的 Team 消息；范围内未解决阻塞发现保留。Team task complete 或消息 accepted 均不等于有效评审。普通 CLI 导入不能制造宿主观察；本地受管观察不防同权限文件系统/进程篡改。

宿主来源采用两阶段发布：`HostObservation.status=published` 只证明输出与观察已落盘，不能授权；在实际可等待生命周期接缝读取并 flush 最终 DSH `tool/result` 后，发布不可变 `HostSettlement`。缺少最终结果、取消、超时或失败保持未完成；恢复读取持久化会话事实重新结算，不依赖 Lead 局部状态。

原生观察通道由插件进程内 Ed25519 私钥签名，配置 filesystem provider 仅持久化公钥 pin。签名绑定 task/workspace、操作及完整上下文摘要；CLI 验证已有 pin，不提供创建宿主权威的命令。普通模型 JSON 或自设环境 token 不能制造来源。历史公钥及签名保留以验证恢复记录；同权限直接改写公钥/文件仍在保证边界之外。

原生语义评审工具参数必须携带明确且当前的目标摘要；发布器不能把 null 或过时
引用静默替换为最新工件。`AssessmentResult.review_assurance` 分别展示每份意见的
宿主观察/结算引用、可观察执行独立性、语义结论及各自限制。接受意见不等于证明
认知独立或结论正确。

目标修订不能抹去真实且未处置的阻塞发现。在相同范围和相关目标 ID 内，历史独立
意见持续保留，直到有精确原评审/发现引用、真实成员消息来源及有据反证的处置。
历史意见可用于该处置，不能授权变更后的当前输入。已得到有效证据处置的发现不因
后续修订重新打开，也不要求全员同意。
