# 范围治理改造交付

简体中文 | [English](DELIVERY.md)

**状态：COMPLETE。** 经来源核验的F-01～F-08均已满足，无未解决的本轮阻塞。
完成依据是用户目标和适用契约，不是SC候选全通过。本结论仅覆盖本次约定的实现与验证范围。

## 验收依据纠正

SC-01～SC-18原为助手候选/例子，被先前Prompt与合同错误升级；用户已明确撤销该升级。
[修订合同](CONTRACT.zh-CN.md)、[来源矩阵](ACCEPTANCE_MATRIX.zh-CN.md)及
[原R/SC处理映射](SOURCE_CORRECTION.zh-CN.md)区分用户需求、改造前v2规范、必要工程推导和示例。
订单/租户或通用全面矩阵不会成为SUT默认要求。保留合理测试、实际结果和Git历史，未reset。

起始提交 `fe3e29b82059504cd47893b72005051489c0810f` 已现场复核，见[基线文件摘要](evidence/baseline-reverified.json)。
产品实现提交为 `c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60`，本地分支 `codex/scope-convergence`。
此前 `000d3be` 保留原错误基准，`e66935f` 记录用户授权的来源纠正。

## 已实现行为

协议 `2.1.0-dev.1`、Python `bugate-core 2.1.0a1`、DSH包 `2.1.0-dev.1` 引入
ScopeContract、真实输入捕获的ScopeRequest及非执行Suggestion。既有inventory保持唯一计划。
准入、执行和声明校验scope/plan/environment绑定，恢复上下文使用同一范围。
额外建议不能进入必需计划、使执行输入失效，也不能仅因尚未采纳而触发完成返工。
合法辅助与关联失败的有界诊断继续可用。

执行闭环、功能结果、阻塞、建议和历史失败分开表达。真实Oracle失败可完成执行，但不验证失败需求。
具名需求声明与整体完整性同样分开：R1通过不代表未观察R2已完成。
Schema、MethodSpec、规则/reason codes、CLI、原生工具/Skill、打包/CI和中英文使用文档已同步。

Profile分母在每个scope版本中持续锁定。同分母内明确授权的分支变化创建新版本；
改变需求/风险分母须显式新Profile和新任务，保留原事实与失败。范围变化保守失效旧证据并重新执行，
不宣称支持选择性复用或同任务Profile迁移。见[架构](ARCHITECTURE.zh-CN.md)及[评审](REVIEW.zh-CN.md)。

## 实际验证

| 检查 | 实际结果 | 证据 |
|---|---|---|
| 最终core v2回归 | 123项通过，exit 0 | [命令结果](evidence/core-tests.result.json)、[原始日志](evidence/core-tests.stderr)、[逐测试映射](evidence/core-mapping.json) |
| 真实DSH完整回归 | 24项通过，exit 0，保存精确当时源码 | [结果](dsh/runtime-result.json)、[日志](dsh/runtime-final.log) |
| 最后具名声明修复的真实DSH受影响路径 | 2项通过，exit 0；authoring及正常/失败收敛流程 | [结果](dsh/runtime-after-named-scope-fix-result.json)、[日志](dsh/runtime-after-named-scope-fix.log) |
| 分发/安装边界 | 4+15项通过 | [打包索引](packaging/verification-summary.json) |
| 最终包源码禁读下安装/重复/doctor/升级/回滚/卸载/恢复 | 20条命令通过 | [原始结果](packaging/clean-install/result.json)、[实际命令](packaging/clean-install/commands.json)、[禁读核验](packaging/source-denial-attestation.json) |
| 真实dev.2归档升级、旧绑定拒绝及rollback | 9条命令通过 | [结果](packaging/actual-upgrade/result.json)、[命令](packaging/actual-upgrade/commands.json) |
| 精确已安装包+OS源码禁读+真实DSH | 1项集成测试、2条完整流程通过，exit 0 | [精确结果](dsh/installed-runtime-result.json)、[原始日志](dsh/installed-runtime-final.log)、[实际会话轨迹](dsh/installed-session-traces/index.json) |
| 源码/资源/校验和 | 57个构建输入匹配产品提交；有限50个native/46个wheel成员、28协议资源；本地校验和均一致 | [源码匹配](packaging/source-commit-match.json)、[资源边界](packaging/bundle-boundaries.json)、[独立校验](evidence/final-checksums.json) |

完整24项DSH运行早于最后具名声明两行修复；之后2项受影响路径及最终已安装包运行分别绑定最终源码，
不把它们表述成后续字节上重跑了完整24项。实际宿主为DSH `0.2.2-alpha.2`、Node `26.8.1`、
Python `3.13.3`、macOS arm64。模型由确定性fixture驱动，**未运行live model**。
远端CI已更新但未触发，适用命令实际在本地运行。

保留并修复了两项实际发现：建议后重评冲突于同一不可变结果key；具名R1验证错误要求R2执行。
见[运行发现](dsh/RUNTIME_FINDINGS.zh-CN.md)和[独立修前/修后证据](evidence/independent-review.json)。
另发现安装测试元数据变量遮蔽了拒读root，已用两行变量改名修正，不改变安装行为。
原始result保留，[补充核验](packaging/source-denial-attestation.json)逐条核对实际sandbox命令和探针；
未将元数据修正冒充重新执行安装。中间包/结果均标记intermediate，不计作最终产物验收。

## 正式需求到证据

| 正式项 | 证据闭环 |
|---|---|
| F-01统一范围实体 | core映射、真实authoring、共享包资源及已安装runtime。 |
| F-02不自动扩展/建议不阻塞 | core计划/草稿/建议检查；真实越界计划和命令拒绝；相同完成声明继续满足且无返工。 |
| F-03真实来源/Profile/变更 | core用户请求、版本、分母；真实子Agent/返工伪授权拒绝；历史保留。 |
| F-04真实证据与结果分离 | core真实性/skip/error/具名声明；真实成功和失败；建议前后不可变评估。 |
| F-05原生执行与有界辅助 | 真实DSH工具/guard/provider/屏障/取消/超时/探测/诊断。 |
| F-06绑定/恢复/隔离 | core旧计划/快照/路径/并发；实际50轮压缩、冷恢复、spawn/fork/preset、多任务。 |
| F-07独立原生包 | 最终归档、源码禁读安装、真实升级/rollback和已安装runtime。 |
| F-08一致且有限的交付 | 来源纠正、双语文档、证据、校验和、对应安装Prompt、可审查提交。 |

具名测试和命令见[core映射](evidence/core-mapping.json)、[DSH报告](dsh/REPORT.zh-CN.md)
及[打包报告](packaging/REPORT.zh-CN.md)。

## 产物与边界

六个产物位于[dist/scope-convergence](../../../dist/scope-convergence)。
[对应产物的一键安装Prompt](../../../INSTALL_DSH_PROMPT.zh-CN.md)先按
[SHA256SUMS](../../../dist/scope-convergence/SHA256SUMS)核验五个内容摘要，再安装到明确的外部项目/profile。

- [原生包](../../../dist/scope-convergence/bugate-deepseek-harness-2.1.0-dev.1.tgz)：
  `4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`。
- [Python wheel](../../../dist/scope-convergence/bugate_core-2.1.0a1-py3-none-any.whl)：
  `89061edf1f560f5ce7c0d79366faf329bf8186d73f5115959ae00ddebd06dafa`。
- 同目录附独立installer、build/source manifests及checksum清单。

离线依赖面向CPython 3.13/macOS arm64，见[wheel target](packaging/wheel-target.json)，安装时校验ABI。
DSH peer依赖解析可能访问registry。范围约束覆盖声明的受管活动和精确绑定，
不宣称理解任意shell业务语义或抵抗同权限所有者恶意伪造；语义评审仍是来源支撑的判断。
未宣称live model质量/效率收益或全产品安全。

本轮约定的实现与验证无剩余未完成项。live model未运行，未宣称相关效果。
[机器验收记录](acceptance.json)和[最终核对](evidence/final-reconciliation.json)索引正式项及证据。
未发布/覆盖公开release或tag，也未推送远端分支。

最终空白检查仅报告三份原始终端记录中的空行或尾随空白；源码和文档检查通过。
原始记录按字节保留，并列入[证据清单](evidence-index.json)。
