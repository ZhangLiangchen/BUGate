# BUGate Protocol v2

[English](README.md) | 简体中文

Protocol `2.0.0`、Python `bugate-core 2.0.0a2` 和 DSH bundle `2.0.0-dev.2`
各自具有版本身份；每个任务锁定精确协议摘要。
本目录是 canonical 资源；wheel 打包包含相同字节。`manifest.yaml` 和
`reason_codes.yaml` 使用 JSON 语法，属于合法 YAML 1.2。

九类核心 Schema 加 MethodSpec、ExecutionRecord 和 CommandResult 使用 Draft
2020-12，由锁定的 `jsonschema==4.23.0` 校验。Artifact 按 artifact_type 严格约束内容。
MethodSpec 定义质量，不调度工作。Python 评估分别保留确定性、语义评审和运行维度；
适配器原样注入 `render.content`。

初始化接收 JSON Profile。Profile 来源路径相对于显式 project，工件/证据/声明路径
限定于任务。来源摘要必须取真实文件字节。对象摘要为 `kind + '/v1'`、NUL、按键排序
且无空白的 UTF-8 JSON 的 SHA-256；排除顶层 `digest` 和 `request_digest`。文件摘要
直接对原始字节计算 SHA-256。不接受浮点数和超出 JavaScript 安全范围的整数。
`protocol.lock.json` 统一使用 `digest`，不并存另一套 `sha256` 字段名。

快照排除 Claim、AssessmentRequest、AssessmentResult 和上下文派生输出；请求摘要
包含固定 Claim 正文。任务事实、来源字节、精确绑定与评估器源码字节纳入输入摘要。
status 重新评估声明，历史 satisfied 字段不能授权。原子写和非阻塞任务锁保护配合协议
的本机写入者；不受管外部并发写入可能冲突，不宣称全文件系统原子性。

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
纳入的对抗发现必须映射到实际用例，未决处置和残余风险阻止设计完成。
独立评审要求工件的具名 `producer` 与 `reviewer` 不同；这是来源记录，不是认证身份。
可选模型不隐式成为默认必需项，其正反例与普通 v2 测试一同验证。

`tests/v2/test_core.py` 运行时生成并校验示例，fixture 和执行日志位于外部临时 SUT
目录。普通 prepare/validate/assess 不依赖 Memory Bus。
