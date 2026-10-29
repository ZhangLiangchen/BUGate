# 经来源核验的正式验收矩阵——修订 2

简体中文 | [English](ACCEPTANCE_MATRIX.md)

U1/U2/B0/D/C 来源类别见[合同](CONTRACT.zh-CN.md)。正式项按必要行为合并为 F-01～F-08，
不按候选数量决定范围。保留测试名中的 SC 仅是历史标识，示例 fixture 可替换。
[历史映射](SOURCE_CORRECTION.zh-CN.md)记录候选处理。以下结果对应实际命令日志和安装产物摘要；
PASS指正式需求满足，不把未采纳候选或未观察SUT结果标为通过。

| 编号 | 需求/不变量 | 真实来源 | 必要关系 | 可观测 Oracle | 最小充分验证及测试映射 | 实际证据 |
|---|---|---|---|---|---|---|
| F-01 | 范围是第一等版本化实体，核心/CLI/PreparedContext/DSH使用同一契约，表达有限目标分支、必需/辅助/建议、来源及完成规则。 | U2二/七；U1四/九；B0 AGENTS职责。 | 仅提示词不能把计划、执行和声明绑定同一范围。 | 各消费者返回同一当前身份/摘要，schema和持久化有效。 | scope schema/CLI/core；真实DSH工具authoring；包资源一致性。test_scope、workflow fixture、distribution tests。 | PASS — [123项core映射](evidence/core-mapping.json)、[真实authoring/runtime](dsh/runtime-after-named-scope-fix-result.json)、[已安装流程](dsh/installed-runtime-result.json)、[包资源一致性](packaging/bundle-boundaries.json)。 |
| F-02 | 用户目标决定广度，范围内断言充分；建议不进可执行计划也不阻塞。 | U2二/七；U1三/五/六；B0 METHOD来源与Oracle。 | 否则额外想法仍会直接或间接扩展任务。 | 无关场景被拒并作建议；建议及草稿不使完成失效；明确指定拒绝/边界被保留。 | 临时正常/预期拒绝业务断言；尝试新增case；完成后记录建议再评估。订单/整数只是可替换示例。 | PASS — [core映射](evidence/core-mapping.json)、[已安装越界尝试及结果](dsh/installed-runtime-result.json)、[同一声明建议证明](dsh/installed-passing/suggestion-reassessment-proof.json)。 |
| F-03 | 真实用户来源、显式变更历史、固定Profile分母及来源语义评审；不得伪造授权或因失败缩减。 | U2三/四/六；U1四/七；B0 AGENTS、Profile/Binding/workspace。 | 否则Agent可重定义目标或制造同意以通过。 | 无收据/篡改/模型委派授权拒绝；新范围使旧计划失效；分母冲突要求显式新Profile/task；必要性不明保存缺口。 | scope/request反例；同分母授权修订；来源评审；真实DSH子Agent/返工来源反例。 | PASS — [core映射](evidence/core-mapping.json)、[真实来源/生命周期回归](dsh/runtime-result.json)、[评审](REVIEW.zh-CN.md)。 |
| F-04 | 执行完整与功能通过分开，保留业务证据和真实失败/skip/xfail/未知。 | U2三/七/八；U1三/八；B0 quality及assessment/evidence。 | 测试完成可以发现缺陷，结构通过不能替代业务观察。 | 真失败可执行完成但不能验证需求；缺证据不闭环；报告字段与证据一致。 | 受管临时SUT运行、故意业务失败、缺失/零用例/跳过、报告一致性及遗漏失败证据反例；现有v2回归+scope tests。 | PASS — [最终core结果](evidence/core-tests.result.json)、[具名声明修前/后](evidence/independent-review.json)、[已安装正常/失败结果](dsh/installed-runtime-result.json)。 |
| F-05 | DSH用实际接口实施可观察执行准入，放行合法辅助并约束诊断。 | U2三/七；U1六/十一；B0 AGENTS/SOP/guide。 | 范围必须实际控制执行，且不能阻止补证或另造调度器。 | await准入先于同步guard；返回前证据持久化；辅助可用；诊断关联当前有效失败且有界。 | 真实DSH确定性模型驱动已安装工具和命令；guard/辅助/诊断/取消/超时/provider/屏障回归。 | PASS — [真实runtime回归](dsh/runtime-result.json)、[已安装工具/诊断/屏障](dsh/installed-runtime-result.json)、[实际接口探针](dsh/source-interface-probes.json)。live model未运行。 |
| F-06 | 快照/范围/计划/版本、恢复、子Agent/多任务隔离、路径及并发继续有效。 | U2三/七；U1六/七/九；B0 AGENTS/Binding/workspace及现有runtime tests。 | 新范围状态不能授权旧快照或其他任务。 | 旧引用被拒；冷启动/压缩/child保持当前范围；跨任务输入拒绝；原子并发冲突可见。 | 既有v2/runtime回归+变更scope/plan及跨任务针对性检查，不新增无关平台矩阵。 | PASS — [core映射](evidence/core-mapping.json)、[真实生命周期/隔离回归](dsh/runtime-result.json)、[精确快照边界](dsh/REPORT.zh-CN.md)。 |
| F-07 | 当前v2/DSH产物可独立安装，有限资源、支持的安装生命周期正确，无源码/legacy依赖。 | U2三/七/八；U1九/十一/十二；B0 AGENTS/CI/packaging。 | 仅源码可运行不满足原生产品交付。 | wheel/native资源摘要一致；干净安装/doctor/重复/升级/rollback/卸载通过；源码不可访问时已安装runtime可用。 | 现有distribution/install套件、拒读探针、真实归档生命周期和已安装runtime。 | PASS — [打包索引](packaging/verification-summary.json)、[源码禁读核验](packaging/source-denial-attestation.json)、[已安装runtime](dsh/installed-runtime-result.json)、[57个源码输入](packaging/source-commit-match.json)。 |
| F-08 | 双语文档、可审查代码、产物/校验和/安装Prompt和有限结论，来源已纠正。 | U2五/八；U1十二；B0 AGENTS双语交付。 | 用户须能安装被测字节，并理解证据实际证明范围。 | 文档/API/产物一致；报告分开范围/功能/阻塞/建议及fixture/live model；无SC数量门槛或全产品声明。 | 规范投影/CI文字审计、链接、证据核对、checksum和提交。 | PASS — [来源纠正](SOURCE_CORRECTION.zh-CN.md)、[校验和](evidence/final-checksums.json)、[最终核对](evidence/final-reconciliation.json)、[交付](DELIVERY.zh-CN.md)。 |

B0每命题无条件负向用例规则与 U2 明确有限目标及继续有效的 U1三/五存在冲突，
本次替换为契约必要性，并保留明确要求的负向/边界行为。
这是有授权的协议变更，不能据新规则声称它原本就存在。真实性、快照、安装等不冲突回归继续保留。
