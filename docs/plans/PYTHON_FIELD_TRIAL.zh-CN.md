# 双分支独立开发与 Python 首轮实战

[English](PYTHON_FIELD_TRIAL.md) | 简体中文

**决策：** Accepted，2026-10-30。来源是用户明确要求：改进后的 Python 版与全面 TypeScript 版使用独立分支开发，先用改进后的 Python 版做一轮实战。这修正上一版 Prompt 中“不继续全面 TypeScript 化”的表述；现有 TypeScript 工作独立继续，不取消、不回滚。

**本次更新状态：** 仅文档。未创建或切换分支/worktree，未安装产品，Python 实战为 **NOT_RUN**。本计划记录交付顺序与隔离要求；各选定版本的 [Protocol v2](../../protocol/v2/manifest.yaml) 仍是机器规范权威，本文件不另建规则。

## 分支与交付边界

| 路线 | 实现与用途 | 2026-10-30 现场核验的分支/worktree 状态 |
|---|---|---|
| Python 改进版 | Python 评估核心与统一 CLI，加 TypeScript DSH/Cordis 原生插件。复用并补齐范围/主流程收敛，加入官方 Agent Team 互审，然后开展首轮实战。 | 建议分支 `codex/python-scope-team` **尚未创建**。实施前核验并记录 Python 基线，在独立分支/worktree 开发。 |
| 全面 TypeScript 版 | 共享 TypeScript 核心、Node CLI/工具链与 Cordis 原生插件，按 [TS 计划](TYPESCRIPT_CORDIS.zh-CN.md) 独立实施和验收。 | 已有分支 `codex/typescript-cordis-team`，本次核验 HEAD 为 `214bedfcb2129d3b77840cfe207e437d13fdec6a`，位于 `/Users/zhangliangchen/PycharmProjects/BUGate`；含其他执行者未提交工作，须保留。 |

执行前重新核验现场。历史 Python 提交 `3a5fcc7ae2b35f8eb361896c932d688939364bae`、`eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654` 是待核验的基线线索，不是 reset 当前 TS checkout 的指令。保留所有分支、未提交改动和历史。`main` 是集成目标，仅凭分支名称不能认定任一路线已验收或已安装交付。

两条源码分支不代表同一安装包永久保留双评估运行时，也不在 TS 产品内保留 Python fallback；每个产物只有一个权威评估实现。Python 改进和首轮实战不等待 TS 完成。TS 自身验收通过也不自动替换 Python 实战安装；后续采用/切换须另行明确具体目标及精确产物。

## 共同约束、分别验证

两线均只支持 DSH，并复用官方 Agent Team。不恢复 Claude/Codex 产品适配、legacy 格式、兼容桥或自有 Agent loop/团队调度器。使用其他工具开发 BUGate 不等于支持该工具作为产品宿主。

Python 线先盘点已有范围契约、必需用例计划、辅助/诊断通道、建议和声明，复用有效实现、修复真实缺口，不按名称重新搭建。范围内业务断言保持充分，用户明确的分支/权限/边界目标不能被删减；建议不可执行、不阻塞，执行完成与功能通过分别表达。Profile 分母、用户来源、快照绑定、真实失败和证据持久化屏障继续有效。

官方 Agent Team 负责名册、消息、任务协作和生命周期。BUGate 校验宿主观察到的作者/评审者身份、适用的独立性策略、当前工件/输入绑定、持久化结果和分歧处置。reviewer 名称不同、Team task complete 或消息被接受都不等于独立评审已成立。保留原始意见和反证，不默认固定视角、模型、轮数或新增 SUT 风险类别。已接受的 [Team 设计](TYPESCRIPT_CORDIS.zh-CN.md#dsh-官方-agent-team-多视角交叉互审用户追加决策) 是共同意图；Python 线须在自己的版本化机器契约中实现并验证，TS 的实施证据不能证明 Python PASS。

分别标识产物/评估器身份、协议/Profile 绑定、工件摘要、DSH 安装 profile/目录、任务、收据和证据。不得让同一版本号对应不同协议含义，也不得把另一分支的评估静默导入为授权。只有含义一致且绑定核验通过的协议资源才能共享；规则独立变化须显式版本化。修复按实际适用性选择移植，记录来源、影响和各分支验证，不为共享一个修复整分支合并。

## Python 就绪与首轮实战

1. 在独立 worktree 冻结核验后的 Python 基线，对真实改进范围建立有来源的需求/Oracle/检查映射。历史 SC 编号和测试数量不是交付门槛。
2. 完成必要的 Python/core、CLI、插件和打包修改，使用通用 fixture 运行适用回归与真实 DSH Agent Team 验收。使用配置的 filesystem/subprocess execution world，等待证据持久化；mock 或预写 review JSON 不能完成 runtime 验收。
3. 在源码 checkout 禁读的干净目录验证精确 Python 安装包。声明并核验所需 Python runtime/依赖，不套用 TS 线“Python 不可用”的条件。验证适用的安装/重复安装/doctor/升级/恢复/rollback/自有卸载行为及已安装范围/Team 流程，保留用户 Python 与 SUT 环境。
4. 使用该改进 Python 产物，在实际 DSH 中对用户指定的真实外部 SUT、环境与目标做一轮有限实战。执行前冻结包摘要、DSH/Team 版本、Profile、范围、来源与业务 Oracle，并记录实际模型与配置的 provider。本次决定尚未选定具体 SUT、环境和目标，不能虚构或宣称实战已启动；这些信息在实战前需要明确，但不阻塞独立实现或 fixture 验证。
5. 保存真实执行/Team 记录、范围约束观察、相关恢复行为、评审分歧及处置、产品/框架缺陷、阻塞和建议。按约定实战的实际需要检查恢复，不自动扩为通用测试矩阵。分别报告执行完整性、功能结果、证据缺口/阻塞及范围外建议，不为实战报告全绿抹除真实产品失败。

确定性模型驱动的真实 runtime、live-model 使用和真实 SUT 业务证明分别记录。临时 fixture 和确定性 runtime 检查不算本轮实战完成。只报告实际发生的验证；安装成功或 Team 完成标记不能替代其他证明。真实公司源码、凭据和 SUT 原始证据留在外部工作区，仓库 fixture 保持通用。本次决定不发布 release，也不授权替换无关的已安装 DSH profile。

## 反馈与后续采用

Python 实战发现按适用性成为两线有来源的输入，区分共同契约问题、Python 专属缺陷、TS 相关问题、环境/SUT 缺陷或额外建议。采纳依据实际影响与有限 Oracle，不自动把每条发现升级为两线共同需求或完成阻塞。

各线按自己的交付身份保存提交、包校验和、测试命令和 runtime 证据。Python 实战报告仅支持该轮范围内的结论，不取消 TS 路线，也不授权其切换；两线保持相同质量原则，分别证明实现正确性和独立安装能力。
