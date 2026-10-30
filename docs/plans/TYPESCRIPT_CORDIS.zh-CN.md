# ADR：在 DSH Cordis 架构内实现全面 TypeScript

[English](TYPESCRIPT_CORDIS.md) | 简体中文

**架构决策：** Accepted（已接受），2026-10-29。
**实施状态：** IN_PROGRESS。2026-10-30 已授权从 `eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654` 实施阶段 0～5；尚不宣称最终已安装 Team 验收或切换完成。
**权威来源：** 用户在本任务中的明确决策：DSH 保持唯一宿主，将 BUGate 全面转向 TypeScript 并纳入 Cordis 架构，写入下一步开发计划。此前单一目标决定现由 2026-10-30 [双分支决策](PYTHON_FIELD_TRIAL.zh-CN.md)限定：Python 在独立分支改进并优先用于首轮实战，全面 TS 路线独立继续；两线均不取消、不回滚，质量与证据不变量保持。

2026-10-30 执行请求已授权阶段 0～5。此前“仅文档/NOT_RUN”描述上一轮规划状态，本计划现记录实施。[Protocol v2](../../protocol/v2/manifest.yaml)继续是唯一机器权威；`2.2.0-dev.1` 对本轮明确授权的 Team 来源增强进行版本化，本计划不创建其他 SUT 的默认矩阵。

**路线边界：** 本计划适用于 `codex/typescript-cordis-team`。下文 Python 退役、无 Python 打包及切换仅针对 TS 产物与分支，不退役独立 Python 改进线。TS 验收通过不自动替换 Python 实战安装；Python 实战无需等待这些迁移阶段全部完成。

## 背景与基线

本轮实施基线为 main `eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654`。`3a5fcc7ae2b35f8eb361896c932d688939364bae` 标识历史已发布 Python 基线。[历史 Python 发布](../releases/DSH_2_1_0_DEV_1.zh-CN.md)使用 Python core `2.1.0a1` 和 Protocol/原生包 `2.1.0-dev.1`，已有证据仅适用于其记录的源码及产物摘要，不证明计划中的迁移。

历史基线已有原生 Cordis 插件：[`cordis.patch.yml`](https://github.com/ZhangLiangchen/BUGate/blob/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/adapters/deepseek-harness/plugin/cordis.patch.yml)注册 `@bugate/deepseek-harness`，当时配置包含 `pythonExecutable: python3`。基线 `index.ts` 负责原生接入，[`bridge.ts`](https://github.com/ZhangLiangchen/BUGate/blob/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/adapters/deepseek-harness/plugin/src/bridge.ts)通过 DSH provider 调用 Python。目标是替换这条产品 Python 桥接及实现，不是首次引入 Cordis。

历史实施起点（新实现位于 `packages/core/`、`packages/cli/` 和 Node `packaging/`）：

- [`src/bugate/`](https://github.com/ZhangLiangchen/BUGate/tree/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/src/bugate/)：`protocol.py`、`assessment.py`、`semantics.py`、`scope.py` 保存校验/评估；`workspace.py` 保存身份及持久化；`authoring.py`/`context.py` 负责创作/准备；`evidence.py`、`cli.py`、`integrations.py` 负责采集、CLI 和安装。
- [`adapters/deepseek-harness/`](../../adapters/deepseek-harness/)：原生插件、provider/生命周期接入及真实 runtime 测试。
- [`packaging/`](../../packaging/)、[`scripts/`](../../scripts/)、[`tests/`](../../tests/)及 [CI](../../.github/workflows/ci.yml)：第一方构建、安装、doctor、分发和验收工具链均纳入 TypeScript/Node 目标。

JSON、YAML 和文档保留各自格式。外部 SUT 测试可以使用所需语言，包括 Python；通用 fixture 和产品验收入口不能为 TS 产品留下隐含的 Python 前提。

## 决策与职责边界

目标为共享的 TypeScript 评估核心、统一 Node CLI，以及消费同一实现的 Cordis 原生服务/插件。业务语义继续 SUT-neutral，并与 DSH Agent loop 解耦。执行、调度、生命周期和有界返工仍由 DSH 负责；BUGate 不新增 Agent loop、永久双运行时或多宿主框架。

纯评估与工作区/进程/证据副作用分离。使用配置的 DSH filesystem/subprocess provider，CLI 也须明确执行上下文。DSH 唯一宿主不等于可以直接读取本地文件或共用一个进程命名空间，opaque target 不是路径。TypeScript 不代表全部逻辑必须运行在插件事件循环中：CPU 密集校验/哈希、其他 execution world 的 IO、取消与清理，都需要明确并验证执行位置和资源边界。worker/进程如何选择由实现阶段决定，本计划不虚构 SDK 或传输能力。

当前包锁定 DSH `0.2.2-alpha.2` 和 Cordis `4.0.6-alpha.1`。已有 `apply(ctx, config)`/注入、同步 `context.text`/`tools.guard`、可等待的 `agent/created`、`agent/pre-step`、`agent/request`、`tools/pre-execute`，以及模型准入检查、provider 调用和 teardown，是实施起点。开发前重新核验实际选用的 DSH 源码、类型与包，不虚构 hook、取消接口或子 Agent guard 继承。异步准备完成后才能供同步 guard 使用，证据持久化完成后才能返回工具结果。

## DSH 官方 Agent Team 多视角交叉互审（用户追加决策）

**来源与状态：** 用户先要求原生 subagent 评审及真实来源、独立性、工件绑定，随后明确改为优先复用 DSH 官方 Agent Team 插件。采用最新决定：由 Team 成员执行多视角交叉互审，BUGate 校验真实评审来源、独立性和工件绑定，不能仅靠 reviewer 名称不同就认定独立，不恢复 Claude/Codex 依赖。本项已接受，实施及验收为 **IN_PROGRESS**；效率提升是用户期望，尚未实测。历史 [`assessment.py`](https://github.com/ZhangLiangchen/BUGate/tree/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/src/bugate/assessment.py) 的独立性检查主要比较 producer/reviewer 名称；历史 [DSH 子 Agent 测试](https://github.com/ZhangLiangchen/BUGate/blob/eb2ae6769f16a4c0b7d542795bcc95cbbcbfd654/adapters/deepseek-harness/tests/runtime.test.mjs)证明绑定和授权来源的部分行为，不证明完整互审链路。本项是有用户来源、需版本化的能力增强，不回写成旧协议已经保证的能力。

DSH 单宿主允许多个 Agent。优先使用官方 Agent Team 承载有限的必要视角评审；宿主拥有成员名册、任务板、消息、委派、执行、取消和生命周期。BUGate 只保存评审工件、必要宿主引用和评估，不复制 Team 状态机，不另建 Agent loop 或调度器，不依赖 Claude Code/Codex。subagent 可以作为官方 Team 的底层实现，或在明确有依据的降级设计中使用；不能自行用直接 subagent 调用替代 Team 来宣称本项验收完成。

**现场接口核验（不是运行验收）：** 本地 `adapters/deepseek-harness/plugin/node_modules/@deepseek-ai/dsh-experimental-agent-team` 的 `package.json`、README 和类型声明对应官方 [`packages/experimental/agent-team`](https://github.com/deepseek-ai/deepseek-harness/tree/main/packages/experimental/agent-team)，版本为 `0.2.2-alpha.2`，另有 `dsh-experimental-tool-agent-team` 和 `dsh-experimental-agent-team-profile`。`ctx.agentTeams` 提供以精确 live Agent 为调用身份的 membership、成员查询、spawnTeammate、sendMessage、任务查询/创建/expectedRevision CAS 更新、waitForChange 和 interrupt。TeamId 关联 Lead SessionId，成员支持 fresh/fork；官方 Team profile 挂载 Team 并禁用直接 subagent/control/fork 工具，底层仍依赖 dsh-subagent。实施前复核实际选用版本和配置，不把 experimental 能力描述成稳定接口。

Team 持久化名册及任务板；消息发送成功只证明目标 inbox 接受，不证明模型处理或单独同步 flush。当前为进程内共享 cwd，不提供 worktree 隔离；writeScopes 是提示，不能替代写入保护。任务 owner 不自动释放，消息无自动重试/重发去重保证。复用这些真实语义设计恢复、取消、并发及评审发布屏障；Team task complete 不等于评审有效，更不等于业务通过。此前规划轮未运行 Team；本轮实施的真实调用单独保存在验收记录。

- **适用范围：** 独立评审是否必需及所需视角由有效 Profile 和本轮采纳要求决定。视角说明它验证什么命题及必要性，不默认固定角色数、模型数、轮数或全员两两互审。评审发现范围外机会仍只记建议，不扩展 SUT 目标；评审本身也有可观察的完成条件和有界返工。
- **真实来源：** 从实际 DSH 执行接缝捕获被评审工件作者和评审者的 Agent/session 身份、Team/成员身份、委派/父子关系及实际执行结果，绑定此次评审输入与持久化输出。模型填写的名称、session ID、模型名或“已经独立审查”不能提升为宿主观察。主 Agent 代写评审文本可以保留为模型材料，不能冒充 Team 成员的实际输出；作者来源缺失时也不能判定独立性已证明。
- **独立性边界：** 依据宿主可核验的作者/评审执行关系和输入上下文策略校验独立性，拒绝同一 Agent 换名自审。区分可观察的运行独立性与业务评审质量；新 session、不同 Agent 或不同模型均不单独证明认知独立或结论正确。记录 Team 成员 fresh/fork 创建方式和上下文继承，fork 不得冒称盲审。模型多样性不是默认要求，也不是独立性的替代证据。必要信息不可观察或无法满足既定独立性策略时报告明确缺口，不伪造通过。
- **精确绑定与失效：** 评审关联任务、协议/评估器/Profile、当前范围/计划、被评审工件 ID/摘要、被审输入快照及评审执行身份。实现时在唯一机器规范中定义适用字段和无循环的输入快照边界。工件或相关输入变化使旧评审失效；跨任务、过时或重放的记录不能授权当前声明。恢复后的有效性依据持久化事实重算，不能依赖父 Agent 局部状态。
- **交叉核对与结论：** 保存各视角的原始意见、来源、发现、反证及限制，关联后续 Team 讨论/反驳的真实来源、修订及对相同命题分歧的有依据采纳、拒绝、未解决处置。交叉互审不止并行提交几份同意；Lead 不能抹平异议。不得用多数票、名称不同或一致的 pass 替代业务 Oracle，不隐藏范围内未解决问题。结论修订保留历史；修订被评审工件后重新评审当前版本，不无限循环追求全员同意。评审失败、取消、超时或缺失输出不能算评审完成；必要评审缺口只影响相关声明，不能阻止合法辅助工作。
- **持久化与信任边界：** 宿主观察和模型语义意见分开存储/标识，复用现有工件、证据及评估体系并合理规范化；先完成评审输出与来源记录的持久化，再允许据此授权。CLI、共享核心和插件消费同一规范，不能靠 CLI 导入模型 JSON 伪造宿主评审收据。保证属于本地受管宿主可观察的真实性，不宣称能对抗拥有同等文件系统权限的篡改者，也不承诺评审必然发现所有缺陷。

阶段 0 将上述用户要求映射到规范/Oracle；阶段 1 落实版本化 Schema、MethodSpec、reason codes 和评估；阶段 3 实现官方 Team 原生链路；阶段 4 验证已安装包。最小充分验证至少区分：真实 Team 成员对精确工件的多视角评审及分歧处置；换名自审、伪填身份/主 Agent 代写与来源缺失；工件变更/跨任务/重放失效；恢复、取消及返回前持久化。按这些需求及实际接口选取、合并检查，不固定用例数量。确定性 fixture 必须驱动真实 DSH Agent Team、成员与实际工具；mock、预写 review JSON 或历史子 Agent 测试不能单独证明本项通过。精确安装包在源码禁读、Python 不可用环境中也要运行互审路径；live-model 质量验证另行报告。

## 必须保持的不变量与差分策略

来源为用户本次决策、[AGENTS](../../AGENTS.zh-CN.md)、[METHOD](../qa-methodology/METHOD.zh-CN.md)、[SOP](../qa-methodology/SOP.zh-CN.md)和当前 v2 机器契约。迁移所需检查覆盖：

- **运行时含义：** TS 类型不替代运行时 JSON Schema 校验；规范对象/文件字节遵循已声明的域分隔、UTF-8/键排序和安全整数规则。verdict、reason codes、评估维度、JSON envelope 和退出语义保持来源支撑。
- **范围与授权：** Profile 分母固定，用户来源真实，显式分支/Oracle 必要性和语义评审保留；建议不可执行、不阻塞，建议评估身份与授权快照分离。合法辅助活动与有界诊断保持可用。
- **一致性：** 声明/评估器/协议/Profile/范围/计划/证据精确绑定；原子持久化、不可变记录、expected-digest CAS、锁、并发写者、任务隔离及路径/符号链接边界不削弱。
- **真实观察：** 真实采集/报告证据、来源区分、超时/取消/输出边界和持久化屏障。保留失败、基础设施错误、零测试、skip、xfail 和未观察；执行完成与具名需求验证分别评估。
- **生命周期：** 配置的 execution world、dispose/清理、冷恢复、压缩、子 Agent/fork/独立 preset 和并行任务隔离，需要真实宿主证据，不假设本地状态自动继承。

冻结的 Python 基线仅作为**开发期差分对照**，不作为产品回退运行时。规范要求一致的对象/文件摘要向量应一致；Python→TS 的评估器实现身份本应变化。版本/身份差异须明确记录，不能抹平差异以继承旧授权；业务 verdict/reason 与证据适用性仍须符合契约 Oracle。将差异归为移植缺陷、基线缺陷或有意的版本化变更，保存反例，不盲目复制已知 bug，也不降低断言追求一致。

根据需求和变更影响复用有效测试。SC 编号以及历史 123/24 项结果只是观察，不是固定迁移门槛。

## 有限实施阶段

各阶段按实际源码/命令/产物身份和结果记录，不继承历史测试结论。
阶段 0 使用现有已验证工具建立有限基线，不要求尚未实现的 TS 评估器授权自身迁移。

| 阶段 | 需求 / 交付 | 可观察 Oracle 与最小充分验证 | 退出条件 |
|---|---|---|---|
| 0. 需求、Oracle、基线 | 冻结契约/源码及已知缺陷，映射变更边界、Python 依赖和真实 DSH 接口；决定版本/绑定及历史查看策略，核验作者/评审来源和 Team 身份及成员独立性可观察边界。 | 有来源的需求/Oracle 映射、规范字节向量、针对性基线反例、真实 provider/接口核验。 | 每项变更有必要性来源和可区分的 Oracle；实质缺口解决或明确阻塞，不用占位分析放行实现；互审来源/上下文策略有可验证设计。 |
| 1. 核心与 Node CLI | 移植校验、语义/范围/声明、创作/上下文与统一 CLI，明确 IO 边界；版本化原生互审来源、独立性及绑定规则。 | [核心测试](../../tests/typescript/)中的适用类别：真实 verdict/reason 断言、无效 JSON、规范向量、旧引用和建议隔离；明确身份差异的开发期差分。 | 运行时检查及输出语义满足 Oracle，差异有证据处置；名称或自报身份不能满足宿主独立评审要求。 |
| 2. 执行、存储、采集 | 移植工作区身份、原子发布/CAS/锁及进程/报告采集；设计 execution world、CPU/IO 和清理边界。 | 临时真实命令覆盖成功/失败/skip/xfail、缺报告、超时/取消；必要的并发写入、路径/world 不一致、返回前持久化检查。 | 中断不制造完成，使用正确配置 world，清理有界，不支持的映射明确拒绝。 |
| 3. Cordis 原生接入 | 在核验过的原生接缝以共享 TS 服务替代 PythonBridge，保持上下文/准入、来源和有界返工；复用 DSH 官方 Agent Team 实现多视角互审和持久化来源。 | [真实 DSH 测试类别](../../adapters/deepseek-harness/tests/README.zh-CN.md)：确定性 fixture 驱动实际工具/临时 SUT 命令，检查范围、建议、失败、辅助/诊断、恢复/子 Agent/隔离和取消，以及上节互审正反例。 | 真实宿主调用证明约束、互审来源/独立性/绑定及证据屏障；mock context 不单独完成本阶段；若运行 live model，单独报告。 |
| 4. Node 打包与 CI | 移植第一方 build/install/doctor/distribution/test 入口，产出有限 npm 资产、规范投影及一致使用说明/CI。 | 源码禁读且 Python 不可用的干净安装；摘要/资源；适用的重复安装、doctor、升级/rollback/卸载/所有权检查；已安装包真实 DSH 流程及多视角互审链路。运行所声明的 CI 检查并报告实测平台。 | 精确产物独立运行，校验和与文档一致；不因 TS 推断 Windows 等平台支持。 |
| 5. TS 分支内 Python 退役与切换 | 替代实现验证后退役本分支桥接/bootstrap/wheelhouse、第一方 Python 工具链和生产回退；保留独立 Python 路线。 | 有限包/依赖清单检查；无 Python 的产品安装/运行/验收；旧绑定明确拒绝、历史保留及自有卸载/rollback 验证。 | 只保留一个受支持的 TS 产品路径，无隐含 Python 前提；历史发布不变，后续发布独立授权交付。 |

## 版本、旧证据与安装切换

本轮为明确授权的宿主来源和独立性增强，将协议/评估器/打包版本设为 `2.2.0-dev.1`，不静默改变已有版本含义。旧 Python 评估/收据不能因为字段相似就授权 TS 执行。默认切换方式为明确的新任务/绑定，保留旧范围、证据、失败和任务历史。阶段 0 决定历史只读查看说明，不新增自动格式转换、跨版本授权或选择性证据复用。现有任务内的范围失效及 Profile 分母规则继续有效。

最终 TS 产品不依赖 Python bootstrap/wheelhouse。已有用户 Python 环境保留，只有显式的 owned-uninstall 才能处理已证实属于 BUGate 的状态；保留 SUT 依赖、任务事实及无关配置，不扫描或清理用户全局环境。Node/npm 安装必须证明所有权、中断恢复和 rollback，选用 npm 本身并不证明这些性质。

## 选项、影响与边界

| 选项 | 决策 / 取舍 |
|---|---|
| Python core + TS 原生插件 | 2026-10-30 决策明确为独立改进路线；其验证后的交付优先用于首轮实战。 |
| 共享 TS core + Node CLI + Cordis 原生实现 | **Accepted。** 统一第一方语言/工具链，最终移除 Python bootstrap/bridge；必须保持运行时、持久化语义和副作用边界。 |
| 同一包永久双运行时或通用多宿主层 | 不采纳；独立源码分支及分别验证的产物不等于双评估运行时。 |

风险包括序列化/校验器差异、本地 world 假设、事件循环阻塞、取消/drain 丢失、持久化削弱、伪造用户来源和旧绑定复用；上述阶段覆盖其直接迁移影响。不承诺实测性能/模型质量收益、新平台、新宿主或更多 SUT 风险类别。其他机会保持建议。

**当前执行：** 共享核心/CLI、Node 生命周期及原生 Team 接入已实施并持续联调。候选 Node 包已在 macOS arm64 操作系统隔离下通过安装、重复、doctor、升级、rollback、卸载和中断恢复；该候选不是最终 Team 交付包。最终精确包验收与 Python 退役仍须完成。Live-model 验证为 NOT_RUN；未发布新公开 release/tag。
