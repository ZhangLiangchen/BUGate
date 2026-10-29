# 发布文档审计

[English](DOCUMENTATION_AUDIT.md) | 简体中文

本审计修正现行说明，并保留历史证据；不新增产品行为、测试类别或验收分母。
用户已授权发布及清理会误导后续开发的过时文档。
起始文档提交为 `bb6153efc7a334172c36f069c2c160504df8f375`。

| 发现 / 范围 | 影响 | 处理 |
|---|---|---|
| `protocol/v2/README{,.zh-CN}.md` 仍声明 Protocol 2.0.0、Python 2.0.0a2 和原生 dev.2 | 与当前 manifest/包版本冲突，旧文字已进入 bundle | 修正为协议/原生 2.1.0-dev.1、Python 2.1.0a1；Schema 数量改引用 manifest 清单，不固定数量。 |
| 同一协议简介缺少范围实体和建议评估身份 | 可能误导为建议使声明失效、所有残余风险都阻塞完成 | 补充 ScopeRequest/ScopeContract/Suggestion、唯一 inventory 计划、Profile 分母、范围/计划/环境接入和 advisory_digest。范围内未决发现仍阻塞；类型明确的范围外残余项引用不可执行建议，不阻塞。未变更规则。 |
| CONTRIBUTING、CAPABILITIES、协议架构和 DSH 指南把范围收敛交付合同作为长期现行权威 | 一次变更的矩阵可能被套到未来无关任务 | 当前入口改指向 canonical 协议、方法和新双语文档索引，历史矩阵保留为特定版本证据。 |
| 开发构建示例写入 `dist/scope-convergence` | 后续本地构建可能覆盖已验证产物路径 | 通用开发示例改为 `dist/local-build`，发布产物使用独立 release 目录。 |
| DSH 适配器 README 声称 dev.1 仅本地交付且不发布 | 用户明确授权发布后已过时 | 改为与发布阶段无关的说明，链接当前发布/证据导航；本地 build 本身仍不发布。 |
| 适配器符合性开头描述多个一等宿主 | 暗示支持已退役适配器，历史 AC-33 缺乏上下文 | 开头明确仅 DSH，HC 行为编号保留追踪用途，退役决策通过历史索引访问。 |
| AGENTS、CHARTER、INIT、METHOD/SOP、范围示例、使用指南、adapter/plugin/test README、Skill/投影说明 | 核对权威、宿主边界、默认矩阵、来源、完成与恢复一致性 | 准确正文保持不变；Skill 与插件配置未修改；正常的显式发布授权要求继续适用。 |
| 历史 `docs/reports/scope-convergence/`、`docs/reports/v2-dsh-only/`、旧发布说明及摘要 | 改写会改变“当时实际执行了什么”的证据 | 字节保持不变；新 `docs/README` 区分现行规范、实现快照与历史版本。 |
| 本地忽略的 `prd_health_report.md`、`oracle_falsification_result.md` | 旧生成输出，不在版本控制和打包清单中 | 保留本地文件，不当作现行文档，也不打包；不恢复退役生成器说明。 |

根 README 双语、安装 Prompt 双语和发布说明由其他写作者负责，其变更另行审阅。
本审计未改产品源码、Schema、MethodSpec、规则、测试或 runtime Skill 输入。
两份协议 README 在 `packaging/resources.json` 中被明确打包；文字字节变化会改变协议摘要、
Python wheel、原生投影和 bundle，因此发布构建获得新校验和，并单独运行新安装包的真实
DSH 验证。此前 `4279c20c…` 包的结果不重新标记为新包结果。

核验限于文档链接、双语配对、已知过时指令扫描和跟踪文件/包输入差异，
[机器可读检查](documentation-checks.json)记录命令与结果。打包和实际已安装 runtime
结果分别保存在本次发布记录；文档检查不等于 runtime 验收或 live-model 证明。
