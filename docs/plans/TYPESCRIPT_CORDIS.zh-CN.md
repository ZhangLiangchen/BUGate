# ADR：在 DSH Cordis 架构内实现全面 TypeScript

[English](TYPESCRIPT_CORDIS.md) | 简体中文

**架构决策：** Accepted（已接受），2026-10-29。  
**实施状态：** Planned（计划中），尚未开始。迁移检查全部为 **NOT_RUN**。  
**权威来源：** 用户在本任务中的明确决策：DSH 保持唯一宿主，将 BUGate 全面转向 TypeScript 并纳入 Cordis 架构，写入下一步开发计划。它替代此前暂保留 Python 作为目标核心架构的建议，不削弱已有质量与证据不变量。

本次仅修改说明文档，不迁移代码、不修改版本或机器规则、不改已发布资产，也不禁止切换前维护现有 Python 实现。[Protocol v2](../../protocol/v2/manifest.yaml)仍是唯一机器权威；本计划不是第二套规范，也不是其他开发任务或 SUT 的默认验收矩阵。

## 背景与基线

现场核验基线为 `3a5fcc7ae2b35f8eb361896c932d688939364bae`。[当前发布](../releases/DSH_2_1_0_DEV_1.zh-CN.md)使用 Python core `2.1.0a1` 和 Protocol/原生包 `2.1.0-dev.1`，已有证据仅适用于其记录的源码及产物摘要，不证明计划中的迁移。

BUGate 已有原生 Cordis 插件：[`cordis.patch.yml`](../../adapters/deepseek-harness/plugin/cordis.patch.yml)注册 `@bugate/deepseek-harness`，配置包含 `pythonExecutable: python3`。[`index.ts`](../../adapters/deepseek-harness/plugin/src/index.ts)负责原生接入，[`bridge.ts`](../../adapters/deepseek-harness/plugin/src/bridge.ts)通过 DSH provider 调用 Python。目标是替换这条产品 Python 桥接及实现，不是首次引入 Cordis。

实施起点：

- [`src/bugate/`](../../src/bugate/)：`protocol.py`、`assessment.py`、`semantics.py`、`scope.py` 保存校验/评估；`workspace.py` 保存身份及持久化；`authoring.py`/`context.py` 负责创作/准备；`evidence.py`、`cli.py`、`integrations.py` 负责采集、CLI 和安装。
- [`adapters/deepseek-harness/`](../../adapters/deepseek-harness/)：原生插件、provider/生命周期接入及真实 runtime 测试。
- [`packaging/`](../../packaging/)、[`scripts/`](../../scripts/)、[`tests/`](../../tests/)及 [CI](../../.github/workflows/ci.yml)：第一方构建、安装、doctor、分发和验收工具链均纳入 TypeScript/Node 目标。

JSON、YAML 和文档保留各自格式。外部 SUT 测试可以使用所需语言，包括 Python；通用 fixture 和产品验收入口不能为 TS 产品留下隐含的 Python 前提。

## 决策与职责边界

目标为共享的 TypeScript 评估核心、统一 Node CLI，以及消费同一实现的 Cordis 原生服务/插件。业务语义继续 SUT-neutral，并与 DSH Agent loop 解耦。执行、调度、生命周期和有界返工仍由 DSH 负责；BUGate 不新增 Agent loop、永久双运行时或多宿主框架。

纯评估与工作区/进程/证据副作用分离。使用配置的 DSH filesystem/subprocess provider，CLI 也须明确执行上下文。DSH 唯一宿主不等于可以直接读取本地文件或共用一个进程命名空间，opaque target 不是路径。TypeScript 不代表全部逻辑必须运行在插件事件循环中：CPU 密集校验/哈希、其他 execution world 的 IO、取消与清理，都需要明确并验证执行位置和资源边界。worker/进程如何选择由实现阶段决定，本计划不虚构 SDK 或传输能力。

当前包锁定 DSH `0.2.2-alpha.2` 和 Cordis `4.0.6-alpha.1`。已有 `apply(ctx, config)`/注入、同步 `context.text`/`tools.guard`、可等待的 `agent/created`、`agent/pre-step`、`agent/request`、`tools/pre-execute`，以及模型准入检查、provider 调用和 teardown，是实施起点。开发前重新核验实际选用的 DSH 源码、类型与包，不虚构 hook、取消接口或子 Agent guard 继承。异步准备完成后才能供同步 guard 使用，证据持久化完成后才能返回工具结果。

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

所有阶段均为 **NOT_RUN**。执行时各自记录实际源码/命令/产物身份与结果；本次文档更新不满足任何实施退出条件。
阶段 0 使用现有已验证工具建立有限基线，不要求尚未实现的 TS 评估器授权自身迁移。

| 阶段 | 需求 / 交付 | 可观察 Oracle 与最小充分验证 | 退出条件 |
|---|---|---|---|
| 0. 需求、Oracle、基线 | 冻结契约/源码及已知缺陷，映射变更边界、Python 依赖和真实 DSH 接口；决定版本/绑定及历史查看策略。 | 有来源的需求/Oracle 映射、规范字节向量、针对性基线反例、真实 provider/接口核验。 | 每项变更有必要性来源和可区分的 Oracle；实质缺口解决或明确阻塞，不用占位分析放行实现。 |
| 1. 核心与 Node CLI | 移植校验、语义/范围/声明、创作/上下文与统一 CLI，明确 IO 边界。 | [核心测试](../../tests/v2/)中的适用类别：真实 verdict/reason 断言、无效 JSON、规范向量、旧引用和建议隔离；明确身份差异的开发期差分。 | 运行时检查及输出语义满足 Oracle，差异有证据处置。 |
| 2. 执行、存储、采集 | 移植工作区身份、原子发布/CAS/锁及进程/报告采集；设计 execution world、CPU/IO 和清理边界。 | 临时真实命令覆盖成功/失败/skip/xfail、缺报告、超时/取消；必要的并发写入、路径/world 不一致、返回前持久化检查。 | 中断不制造完成，使用正确配置 world，清理有界，不支持的映射明确拒绝。 |
| 3. Cordis 原生接入 | 在核验过的原生接缝以共享 TS 服务替代 PythonBridge，保持上下文/准入、来源和有界返工。 | [真实 DSH 测试类别](../../adapters/deepseek-harness/tests/README.zh-CN.md)：确定性 fixture 驱动实际工具/临时 SUT 命令，检查范围、建议、失败、辅助/诊断、恢复/子 Agent/隔离和取消。 | 真实宿主调用证明约束和证据屏障；mock context 不单独完成本阶段；若运行 live model，单独报告。 |
| 4. Node 打包与 CI | 移植第一方 build/install/doctor/distribution/test 入口，产出有限 npm 资产、规范投影及一致使用说明/CI。 | 源码禁读且 Python 不可用的干净安装；摘要/资源；适用的重复安装、doctor、升级/rollback/卸载/所有权检查；已安装包真实 DSH 流程。运行所声明的 CI 检查并报告实测平台。 | 精确产物独立运行，校验和与文档一致；不因 TS 推断 Windows 等平台支持。 |
| 5. Python 退役与切换 | 替代实现验证后退役产品桥接/bootstrap/wheelhouse、第一方 Python 工具链和生产回退。 | 有限包/依赖清单检查；无 Python 的产品安装/运行/验收；旧绑定明确拒绝、历史保留及自有卸载/rollback 验证。 | 只保留一个受支持的 TS 产品路径，无隐含 Python 前提；历史发布不变，后续发布独立授权交付。 |

## 版本、旧证据与安装切换

实现时按实际兼容性影响决定协议/评估器/打包版本，现在不分配新版本，也不静默改变现有版本含义。旧 Python 评估/收据不能因为字段相似就授权 TS 执行。默认切换方式为明确的新任务/绑定，保留旧范围、证据、失败和任务历史。阶段 0 决定历史只读查看说明，不新增自动格式转换、跨版本授权或选择性证据复用。现有任务内的范围失效及 Profile 分母规则继续有效。

最终产品不依赖 Python bootstrap/wheelhouse。已有用户 Python 环境保留，只有显式的 owned-uninstall 才能处理已证实属于 BUGate 的状态；保留 SUT 依赖、任务事实及无关配置，不扫描或清理用户全局环境。Node/npm 安装必须证明所有权、中断恢复和 rollback，选用 npm 本身并不证明这些性质。

## 选项、影响与边界

| 选项 | 决策 / 取舍 |
|---|---|
| 目标继续保留 Python core + TS bridge | 现有已交付基线；作为目标被用户新决策替代，切换前仍可维护。 |
| 共享 TS core + Node CLI + Cordis 原生实现 | **Accepted。** 统一第一方语言/工具链，最终移除 Python bootstrap/bridge；必须保持运行时、持久化语义和副作用边界。 |
| 永久双运行时或通用多宿主层 | 不采纳，超出本决策；临时开发期对照足够。 |

风险包括序列化/校验器差异、本地 world 假设、事件循环阻塞、取消/drain 丢失、持久化削弱、伪造用户来源和旧绑定复用；上述阶段覆盖其直接迁移影响。不承诺实测性能/模型质量收益、新平台、新宿主或更多 SUT 风险类别。其他机会保持建议。

**下一步：** 在下一次实施任务从阶段 0 开始。本次仅更新计划/导航并检查 Markdown 链接与空白，不宣称完成移植、产品测试、打包、runtime 验证、卸载或发布；迁移证据全部保持 **NOT_RUN**。
