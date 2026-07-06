---
type: charter
id: CHARTER-BUGATE-001
title: BUGate positioning, usage model, and evolution plan
status: accepted
created_at: 2026-07-03
amended_at: 2026-07-06 (A4; A3/A2 2026-07-04; A1 2026-07-03)
authority: ADR-BUGATE-001
companions:
  - docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md
  - docs/qa-methodology/TRANSITION_PROTOCOL.md
  - docs/qa-methodology/BUGATE_DESUT_CALIBRATION_ADR.md
provenance: >
  Produced by independent dual-agent analysis (Claude Code and Codex reached
  convergent conclusions from separate contexts, then were cross-checked),
  ratified by the human owner on 2026-07-03. The production of this document is
  itself an instance of the multi-view convergence discipline BUGate prescribes.
---

# BUGate Charter — 定位、使用规范与演进规划

> 本文是 BUGate 的**定性文档（charter）**：回答"BUGate 是什么、不是什么、默认怎么用、
> 例外怎么用、往哪里演进"。当 README / INIT 等入门文档与本文表述冲突时，以本文为准。
> 本文与 ADR-BUGATE-001（四部分解耦模型）和 TRANSITION_PROTOCOL（迁移协议）**正交互补**，
> 不推翻其中任何条款（兼容性声明见 §5.5）。规范性用语：**必须 / 应当 / 禁止**。

---

## 0. 摘要：三条裁决

1. **定位** — BUGate 是一个 SUT-neutral 的 **Agentic QA Governance Kit / SDTD Gate Engine**：
   一个"过程治理框架（process-governance framework）"，用 skills、hooks、profile、gate
   scripts 和 Memory Service，把 AI 从需求理解到测试生成的全过程变成**可审计、可阻断、
   可复用**的治理工作流。它**不是** harness engineering，**不是** skills 集合，**不是** MCP 项目。
2. **使用规范** — 唯一使用形态是**导入模式（Imported Governance Layer）**：agent runtime 打开
   **SUT 自动化测试仓**作为项目根，BUGate 以 plugin / skills / hooks / profile / scripts 的
   形式导入，作为 Agent 的约束层。打开 BUGate 仓本身只用于**纯净 core 迭代**：
   不挂载 SUT、不写本地 SUT profile、不把真实 SUT 工作区作为本仓子树。真实 SUT 验收
   必须在外部/临时 SUT 测试仓以导入形态完成。
   *（修订记录 A4：开发态也保持纯净；A3 的"工作台"挂载例外自此退役。）*
3. **演进方向** — 像 Spec Kit / Superpowers 一样**被导入** SUT 测试工作区，而不是把 SUT 作为
   BUGate core 仓库的内容来拥有。路线：叙事与文档翻转（P0）→ 安装器与双通道分发（P1）→
   根发现拆分（P1）→ MCP 附加通道与治理左移（P2）。

---

## 1. 定位：BUGate 是什么

### 1.1 规范定位表述（canonical statements）

**中文定位（内部与演讲通用）：**

> BUGate 是一个面向 AI 测试开发的**过程治理框架**：它把 SDTD 方法论编译成机器可强制执行的
> 门禁工作流——skills 分发方法论，stdlib 脚本做确定性语义门，runtime hook 做 fail-closed
> 物理拦截，治理型记忆总线做知识沉淀。需求分析、可测试性分析、用例设计、代码生成仍然由
> Agent 执行；BUGate 只裁决"凭什么证据、过了哪道门，才允许下一步"。
> 它之于 SDTD，如同 Spec Kit 之于 SDD。

**English（对外 canonical，演讲/README 使用）：**

> BUGate is a Spec-Driven Test Development (SDTD) governance kit for AI coding
> agents. It is installed into — or activated for — a SUT automation test
> workspace through skills, hooks, profiles, and gate scripts. The SUT keeps its
> own test harness; BUGate governs when and how agents may design and implement
> tests.

**一句话演讲版：**

> BUGate 不是替代 Claude/Codex 的测试生成器，而是把 Claude/Codex 放进一套可审计、可阻断、
> 可复用的 Agentic QA 治理流程里：先证明业务理解，再证明可测试性，再证明 oracle 与用例
> 映射，最后才允许写自动化测试代码。

### 1.2 职责三分：控制面 / 数据面 / 认知面

| 平面 | 归属 | 负责 |
|---|---|---|
| **治理控制面**（control plane） | **BUGate** | PRD 健康度、业务命题与 oracle、testability 决策、case inventory、可读用例、adversarial review、物理写门禁、角色隔离、Memory 治理与回写 |
| **执行数据面**（data plane） | **SUT 自动化测试框架** | test runner、clients、fixtures、assertions、测试账号与固定资源、API 调用、实际执行、环境适配 |
| **认知执行**（cognition） | **Agent（Claude Code / Codex）** | 真正的理解、分析、设计、生成与修改 |

BUGate"驾驭 agent"的方式**不是替 agent 执行**，而是通过上下文协议、强制产物、hook 拦截、
profile 边界和记忆治理，让 agent 不得不按高质量路径走。一行类比：方法论/宪法/模板是
**立法**，语义门脚本是**司法**，hook 物理拦截是借 runtime 之力的**执法**，Memory Bus 是
**档案**——而**行政（执行权）完整留给 Agent**。

### 1.3 机制分解：四个平面与生态坐标

BUGate 是四个平面的叠加。每个平面单独看都有业界对应物，但没有任何现有项目四面俱全——
这正是"难以用一个现成词定义它"的原因：

| BUGate 平面 | 内容物 | 对应业界概念 | 生态里谁只有这块 |
|---|---|---|---|
| **知识面** | SKILL.md / references / templates / commands / gate agents | Context engineering / Agent Skills | Superpowers ≈ 只有这面 |
| **流程面** | 10 阶段产物链 + `gate_status` 状态机 | Stage-Gate 阶段门 / Spec Kit 的 SDD 流 | Spec Kit ≈ 这面 + 知识面 |
| **强制面** | 物理写入门 + 语义门脚本 + 角色隔离 + de-SUT CI | Policy-as-Code / **准入控制（admission control）** | 无——BUGate 的差异化 |
| **记忆面** | Memory Bus（无证据 confirmed 拒写）+ 04/05 回写 + 经验晋升 | Institutional memory / 学习闭环 | 学习型系统有，但不与门禁耦合 |

强制面值得一个精确类比：挂在 PreToolUse 上的物理写门就是 **admission webhook 的 agent 版**
——拦截写操作、按策略（产物 `gate_status`）裁决放行或拒绝、fail-closed。

### 1.4 它不是什么（反定义）

**不是 harness engineering。** harness 是围绕模型的运行时外壳：主循环、工具面、权限与沙箱、
上下文管理、hook 机制、子 agent 调度（Claude Code 与 Codex 本身就是 harness）。BUGate 运行在
harness 的**扩展点**上，但不拥有循环、不管理上下文、不定义工具面——它与 harness 的关系是
**应用与操作系统的关系**。两个实证判别：

1. **换 harness 测试**：同一份规则源（`.shared/skills/bugate/` + `scripts/`）同时运行在两个
   runtime 上（一端拦 `Edit|Write`，一端拦 `apply_patch`，调用同一脚本），适配层只是薄路由卡。
   若 BUGate 是 harness engineering，换 runtime 它就应整体报废；实际只需换几行 hook 接线。
2. **代码占比测试**：23 个 stdlib 核心脚本中，被接线为 runtime hook 的 5 个，真正解析 hook
   载荷的只有 3 个（物理写门、角色隔离、提示提醒）；其余全部可在没有任何 agent 的 CI 中独立
   运行（本仓 CI 即如此运行它们）。

一句话分界：**harness engineering 抬升 agent 的能力上限（能做什么、看到什么、拿到什么反馈）；
BUGate 约束 agent 的行为下限（在什么证据条件下才被允许做下一步）。前者优化 capability，
后者优化 compliance。** 术语卫生：如必须借用该词，只可说 "agent governance harness /
control plane" 作为解释性短语；**禁止**以 "harness engineering" 作为产品定位——它容易被理解
为写 fixture / runner / mock / CI / 测试环境工具（传统 test harness），会系统性低估 BUGate 的价值。

**不是 skills 集合。** skills 是入口与分发形态（建议性上下文），覆盖不了 gate scripts、hooks、
profile、Memory 治理与 orchestrator。把 BUGate 降级为纯 skills 等于拆掉 fail-closed 的牙齿，
违背其存在理由——诚实不能靠提醒，只能靠机器可校验的产物与物理门禁来强制。

**不是 MCP 项目。** Memory Service MCP 只是记忆层，不是本体。门禁脚本刻意**不是** MCP：
CI 中没有 agent 也必须能跑；物理拦截也只有 hook 能做。

**不是传统 test harness，也不是全自动测试生成器。** SUT 保有自己的 test harness；BUGate 的
远景是"受治理的、可审计的自动化"（human-in-the-loop，六个人工审核点，编排器禁止自动晋升
审核产物、禁止代写测试代码、禁止绕门）。

### 1.5 为什么"感觉像在驾驭 Agent"

这个直觉是对的，且有三个结构性来源——但都不改变类属：

1. **Agent 世界的治理只能机械化落地。** 人类组织的治理是社会性的（评审会、签字、流程文档）；
   对 agent，"提醒"已被实证无效，唯一可靠的强制点是 runtime 的物理层。治理是目的，harness
   是支点——你感觉到的驾驭，是司法权借用物理强制力时的手感。
2. **BUGate 确有一张主动脸。** 门禁是被动否决权，但 orchestrator DAG 与双端 CLI 调度
   （并强制最高推理档）是在主动驱动 agent。注意它的权力边界被明文限定：不写测试代码、
   不晋升审核类产物、不绕门——**驾驶行为存在，但驾驶员和交规始终分开**。
3. **两者都"不改模型而改行为"，外观相似。** 区别看对象：harness 的对象是执行环境；
   BUGate 的对象是工作产物与过程合规。

---

## 2. 使用规范（normative）

### 2.1 形态：定名与状态

| 形态 | 含义 | 状态 |
|---|---|---|
| **内嵌模式**（Embedded） | BUGate 手工融合在旧 SUT 测试仓内 | **已冻结**（TRANSITION_PROTOCOL：frozen reference + fallback），按退出判据退役 |
| **导入模式**（Imported Governance Layer） | 打开 SUT 测试仓，BUGate 版本化导入 | **默认与终态**（本文裁决） |
| **BUGate core 迭代态** | 打开 BUGate 仓，只修改/验证 SUT-neutral core | **维护者开发活动；不挂载 SUT，不是使用形态** |

*（修订记录 A4 生效读法：**使用形态有且仅有导入模式**；BUGate core 迭代态
是改工具本身的活动，保持纯净，不再允许 symlink / 本地 profile 挂载 SUT。）*

内嵌模式的退出终态由此明确：**旧 SUT 测试仓以导入模式引入 kit，替换冻结的内嵌栈**。
TRANSITION_PROTOCOL 的三桶分类、strangler-fig 与退出判据全部不变，改变的只是终态的宿主方向。

### 2.2 默认：导入模式（Imported Governance Layer）

*（修订记录 A3 生效节题：「唯一使用形态：导入模式」。）*

```text
Agent runtime
  -> opens the SUT automation repo            # 项目根 = SUT 测试仓
  -> loads the BUGate plugin/skill            # 知识面
  -> BUGate hooks + gate scripts govern       # 强制面：PRD → test design → implementation
  -> SUT repo owns tests/fixtures/clients/assertions/env/resources   # 数据面
  -> Memory Service records findings/decisions # 记忆面（按项目 namespace 隔离）
```

规则：

- **R1（必须）** 日常测试开发会话的项目根是 SUT 自动化测试仓，不是 BUGate 仓。理由：runtime
  把 trust、permissions、hooks、skills 发现、项目指令装载、CI 与 git 语义全部绑定在打开的
  项目根上；日常工作对 SUT 领域上下文的需求远大于对 BUGate 自身文档的需求。
- **R2（必须）** 导入模式下，SUT profile 与 BUGate 配置**提交进 SUT 仓**，与被守护的测试代码
  同库、同评审、同历史。**治理配置必须与被治理物同库同审**。禁止在 BUGate core 仓用
  本地未提交 profile 指针承载真实 SUT。
- **R3（必须）** 语义门、覆盖矩阵、证伪器作为 **SUT 仓的 CI 检查**运行：门禁跑在变更发生地
  （UC 产物与被守护测试代码的 PR 所在地）。BUGate core 仓的 CI 只负责测引擎本身。
- **R4（必须）** 物理写门（PreToolUse hook）在导入后处于激活状态，且新 SUT 接入验收必须包含
  负向对照：一个没有已通过预编码产物的被守护测试文件，其编辑必须被物理阻断。
- **R5（应当）** BUGate 以**版本化方式**引入（plugin / vendored 目录 / git submodule /
  pip CLI，见 §5.2），升级走版本号。**禁止**手工散拷贝脚本片段——那是 ADR 否决过的
  fork-per-product 的碎片化变体，必然规则漂移。
- **R6（必须）** SUT 领域知识（endpoint 契约、资源策略、环境事实、领域 skills、凭证）留在
  SUT 仓或 profile，**永不进 core**（de-SUT guard 与 Promotion Rule 强制）。
  *（修订记录 A1 加注：「永不进 core」按三层判别式读，行为性事实零让步。）*

### 2.3 BUGate core 迭代态（pure core development）

*（修订记录 A4 生效节题：「BUGate core 迭代态」——开发这个工具时的调试与中立性纪律；
不挂载 SUT，不构成使用形态。）*

适用范围**有且仅有**开发 BUGate 自身，具体为以下活动：

1. debug core scripts / hooks / skill discovery；
2. 演进方法论、profile schema、semantic gates；
3. 运行模板门、临时构造 fixture 验收、core smoke；
4. 通过外部/临时 SUT 测试仓执行导入验收或跨 SUT 回归，验证 core 未被任何一个 SUT 污染。

规则：

- **R7（禁止）** 在 BUGate core 仓内挂载、软链接、嵌套、复制任何真实 SUT 测试工作区，
  也禁止把本仓 `bugate.config.yaml` 指向真实 SUT profile。真实 SUT 验收必须发生在
  BUGate core 之外的导入式 SUT 仓或 scratch 仓。
- **R8（禁止）** 把打开 BUGate core 仓描述为使用 BUGate 的路径。它只是维护者开发活动；
  使用者日常会话打开 SUT 测试仓。
- **R9（禁止）** 把任何被治理 SUT 的事实（名称、路径、实体、环境、凭证、固定资源）
  提交进 BUGate core——`check_no_sut_terms.py` 与 CI 兜底，但中立性首先是写作纪律。
  *（修订记录 A1 有本条的细化生效文本；原文按「不悄改」原则保留。）*

### 2.4 反模式（禁止清单）

- **✗ 宿主倒置**："SUT 挂载在 BUGate 之下"会导致治理契约（profile）
  脱离版本控制、SUT 领域上下文与 skills/hooks/权限全部失联、门禁 CI 无处安放、
  N-SUT 场景退化为"每 SUT 克隆一份框架"。
- **✗ 去牙化**：只发 skills、去掉物理门与语义门——治理退化为建议，假绿与臆造回归。
- **✗ fork-per-product**：每个 SUT 复制一份 core 各自改。规则漂移、学习不复利（ADR 原文
  否决理由）。
- **✗ core 沾染**：core 中出现任何单一 SUT 的业务实体、路径、环境、凭证或固定资源
  （Promotion Rule："当有疑问时，留在 SUT profile"）。

---

## 3. 生态坐标与归因

| | Spec Kit（github/spec-kit） | Superpowers（obra/superpowers） | **BUGate** |
|---|---|---|---|
| 类别 | SDD 工具包（per-project scaffold） | 技能库 plugin（per-runtime） | Agentic QA 治理 kit（两者叠加 + 强制面/记忆面） |
| 知识面（skills/commands） | ✓ 命令 + 模板 | ✓ 丰富 skills | ✓ SKILL.md / references / templates / adapters |
| 流程面（阶段产物） | ✓ specify → plan → tasks | 部分（brainstorm → plan → execute） | ✓ 10 阶段 + `gate_status` 状态机 |
| 强制面（fail-closed 门禁） | ✗ 靠 agent 自觉 | ✗ 靠 skills 说服 | ✓ 物理写门 + 语义门 + 角色隔离 + de-SUT CI |
| 双 runtime 互审 | ✗ | ✗ | ✓ Wave 1 / Stage 3B 跨厂商对审 |
| 记忆治理 | ✗ | ✗ | ✓ Memory Bus（无证据 confirmed 拒写）+ 经验晋升 |
| 质量证伪 | ✗ | ✗ | ✓ Wave 8 oracle falsification + 覆盖矩阵 |
| 垂直领域 | 通用软件开发 | 通用工程纪律 | **黑盒测试与自动化测试用例生成** |

归因口径（开源时保留）：BUGate "absorbs useful ideas … without
depending on their plugins at runtime"——借鉴 Spec Kit 的 specification flow 与 constitution
模式、Superpowers 的门前执行纪律；运行时零依赖、不复制分发其代码。对外统一表述为
**"original orchestration of established test methodology"**（对既有测试方法论的原创编排），
不使用"全新发明"。

---

## 4. 命名规范

对外命名优先级（从强到弱）：

1. **Agentic QA Governance Kit**（首选产品定位词）
2. **Spec-Driven Test Development (SDTD) Gate Engine**（机制副题；README 现行
   "SUT-agnostic methodology and gate engine" 与此一致）
3. **AI Black-box Test Governance Framework**
4. **Testing-focused Agent Workflow Plugin**
5. **"Superpowers / Spec Kit for Agentic QA"**（只作类比引子，不作正式名）

范式词（架构讨论用）：过程治理框架（process-governance framework）、方法论即代码
（Methodology-as-Code）、治理控制面（governance control plane）；物理门的机制类比是
**admission control**。

**不建议主打**：

- "MCP 项目" —— Memory Service 只是记忆层，不是本体；
- "skills 集合" —— 分发形态 ≠ 本体，覆盖不了强制面与记忆面；
- "harness engineering" —— 可作实现手段描述，不作产品定位（§1.4 术语卫生）。

---

## 5. 演进规划

### 5.1 P0 — 叙事与文档翻转（本文生效即启动）

| 事项 | 内容 |
|---|---|
| README | Quickstart 改写：导入模式为默认叙事；BUGate core 迭代态只保留模板门、临时 fixture、外部/scratch SUT 仓导入验收 |
| INIT | bootstrap 流程分成两条路径：使用者路径（在 SUT 仓导入并验证）与维护者路径（打开 BUGate 仓） |
| CAPABILITIES | 每类命令标注运行位置（SUT 仓 / core 仓） |
| 本文自治 | 把 `CHARTER.md` 纳入 `check_no_sut_terms.py` 的 SCAN_ROOTS 与 README 索引——宪章自身必须受 de-SUT guard 管辖 |

**验收**：新读者按 README 五分钟路径建立的心智模型 = 导入模式；维护者打开 BUGate core 只做纯净 core 迭代。

### 5.2 P1 — 安装器与双通道分发

- **`bugate init <sut-repo>`**：合并/写入双 runtime hook 接线、建立或复制 skills、脚手架
  `bugate.config.yaml` + profile（提交进 SUT 仓）、创建 usecases 骨架、打印 hook 重新信任
  （re-trust）提示。安装步骤基线：早期《拆分接缝清单》（2026-06-11）§5–6 已给出
  完整清单（kit + 单一项目配置 + re-trust 注意事项）。
- **双 plugin + installer**：Codex 与 Claude Code 均提供 plugin packaging。
  可复用面（skills / commands-or-skill-adapters / agents / hooks / scripts / bin）
  以 plugin-root 标准目录打包；SUT 仓必须版本化评审的治理合约（profile、project hooks、
  CI、Codex project-local gate agents）由 `bugate init` 写入并刷新。
- **引擎分发形态**（三选一，倾向 ③）：
  ① vendored `.bugate/` 目录——最简单，但会漂移；
  ② git submodule——版本精确，体验重；
  ③ **pip/pipx console-script `bugate`**——stdlib-only 使打包零成本；hook 获得与仓库布局
  无关的稳定入口（`bugate check` / `bugate gate …`）；升级 = 改版本号。
- **验收**：一个全新 SUT 测试仓，不 clone BUGate core，仅经安装器/插件接入后——R4 负向对照
  通过、语义门在该仓 CI 变绿、Memory namespace 按项目隔离。

### 5.3 P1 — 根发现拆分（接缝收尾，唯一的核心工程支点）

- **现状**：哨兵式根发现（`AGENTS.md` + `.shared/`）曾把"引擎根"与"工作区根"捆绑在一起，
  恰好匹配旧开发态挂载布局，但不匹配任意 SUT 仓。
- **目标**：**工作区根** = 自 CWD 向上查找 `bugate.config.yaml`（或显式 `BUGATE_PROJECT_ROOT`）；
  **引擎根** = 由安装方式决定（插件根 / console-script / vendored 目录）。
  `guarded_path_regex`、`artifact_dir`、per-UC fail-closed 绑定、profile 合并均已参数化，无需改动。
- **验收**：同一引擎版本在纯 core checkout、临时构造 fixture 与导入布局中保持根发现语义一致；
  该性质由临时构造 fixture 的验收测试与安装器 e2e 在 CI 持续强制。

### 5.4 P2 — 附加通道与治理左移

- **可选薄 `bugate` MCP server**：把门禁查询与编排暴露为工具，服务非 CLI runtime。定位是
  **新增分发通道**：物理拦截仍只由 hook 承担；脚本保持无 agent 的 CI 可独立运行。
- **长期路线**（左移 PRD / 设计 / TDD，BUGate Loop 元门禁）依赖导入形态横向铺开——
  一个母仓不可能承载全组织被治理的工作流。**本文的宿主翻转是这些路线的前置条件。**

### 5.5 兼容性声明

- **与 ADR-BUGATE-001**：四部分模型自 A4 起按 Core / Profile / Governed SUT Test Repo /
  Runtime 读取；真实 SUT 的 profile 与 workspace 均在导入后的 SUT 仓侧，BUGate core
  不再承载旧式挂载工作区。
- **与 TRANSITION_PROTOCOL**：三桶分类、asymmetric strangler-fig、transition-gap ledger、
  退出判据全部保留；本文仅把退出终态明确为"旧工作区以导入模式引入 kit，替换冻结内嵌栈"。
- **与 README**：README 的维护者路径自 A4 起只描述纯 core 迭代；真实 SUT 验收通过
  外部/scratch 仓的 imported-mode 接入完成。

---

## 6. 决策记录与演进历史锚点

**决策（2026-07-03）**：宿主方向翻转 + 定位定名。由两个 agent runtime 从独立上下文分析并
收敛到一致结论，经人工批准生效。该产生过程本身即 BUGate Wave 1 多视角收敛纪律的一次实例。

**历史锚点**（本决策不是转向，而是向原设计的收敛）：

| 日期 | 事件 |
|---|---|
| 2026-06-11 | 早期《拆分接缝清单》首次给出目标形态：**可导入 kit + SUT 仓内单一项目配置**（即本文的导入模式） |
| 2026-06-16 | ADR-BUGATE-001 确立四部分解耦模型与 Promotion Rule |
| 2026-06-17 起 | core 去 SUT 化：中性 skill、profile 机制、de-SUT guard、临时 fixture 验收 |
| 2026-06-29 | TRANSITION_PROTOCOL 确立 strangler-fig 迁移与退出判据 |
| 2026-06-30 | 过渡落地：AGENTS 拆分、profile 证据源接线、Wave-8 回迁 |
| 2026-07-03 | **本宪章**：宿主方向收敛回导入式；工作台模式限定为维护者例外 |
| 2026-07-06 | **A4**：维护者例外继续收紧为纯 core 迭代；BUGate core 不再挂载 SUT |

抽取期为自托管所需而形成的"SUT symlink 挂进 BUGate"用法已完成历史使命；自 A4 起不再作为
当前维护者路径保留。真实 SUT 验收转移到 BUGate core 之外的导入式 SUT 仓或 scratch 仓。

---

## 7. 修订记录（Amendments）

> 修订原则：R 条款原文**不悄改**——原文保留在原节并加指针标注，生效文本记于本节，
> 每条修正案带日期、批准人与决策记录锚点。

### A1 — de-SUT 校准：从「全面封锁」到「身份防渗」（2026-07-03）

- **批准**：human owner 拍板（2026-07-03）。决策记录：ADR-BUGATE-004
  （`docs/qa-methodology/BUGATE_DESUT_CALIBRATION_ADR.md`）。
- **立法本意重述**：de-SUT 防线的目的随宿主方向翻转（§0 裁决 2/3）而校准。
  旧目的（工作台/内嵌时代）：core 仓与 SUT 完全隔离，零 SUT 词汇。
  新目的（导入模式）：保护 kit 的**可复用性**——vendored 进 SUT 仓的 core 子树
  不得携带「只对某个 SUT 成立的行为性事实」。一句话：**防渗，不防提及**。
- **三层判别式**（guard 与写作者同一把尺）：
  1. **行为性事实**——影响引擎行为、或会被下一个 SUT 继承的默认值、端点、资源、
     凭证、环境名 → **永远禁止进 core，必须走 profile**。判据与 ADR-BUGATE-001
     Promotion Rule 不变，一寸不让。
  2. **身份词**——SUT/产品/内部系统/人名/账号名 → **默认禁止**；叙事/出处语境
     （case study、迁移史、真实导入教程）经**显式标记**豁免合法。
  3. **行业领域词**——API 文档工具名、公链名、密码学词汇、行业中文词等 → 移出
     core 全局词表，不再当 SUT 词防守；是否防由各 SUT profile 的
     `sut_identity_terms` 自行声明。
- **R9 细化生效文本**（原文保留于 §2.3）：

  > **R9（禁止）** 把任何挂载/被治理 SUT 的**行为性事实**（判别式第 ① 层：路径、
  > 实体、环境、凭证、固定资源、端点、默认值），以及**无豁免标记的身份词**
  > （判别式第 ② 层），提交进 BUGate core；经显式标记（行内
  > `bugate: allow-sut-term`——含 `<!-- bugate: allow-sut-term -->` 注释形式、
  > 文件级 frontmatter `desut: provenance-allowed`（仅叙事类文档）、白名单目录
  > `docs/case-studies/`）的**叙事性/出处性提及**除外。用豁免标记携带行为性事实
  > 仍是违规（在 code review 与语义门语境定性，不靠 grep 硬判）。
  > `check_no_sut_terms.py` 与 CI 兜底，但中立性首先是写作纪律。

- **R6 加注**（同义细化，原文保留于 §2.2）：R6 的「永不进 core」自本修正案起按
  三层判别式读——第 ① 层零让步；第 ② 层默认禁止、显式标记豁免；第 ③ 层由 SUT
  profile 自行声明。经验晋升协议「进 core 的规则必须可中立表述」判据不变
  （TRANSITION_PROTOCOL §2.2/2.3 加注同步）。
- **豁免红线**：豁免必须**显式、逐处、可审计**（行内标记 / 文件级 frontmatter /
  白名单目录）；禁止全局关闸或环境变量一键放水。引擎、模板、schema、任何默认值
  不接受任何豁免形式。白名单目录仍跑通用卫生检查（本机绝对用户路径、凭证/密钥
  形态 pattern）。
- **机制连带**：core 内置全局词表清空，词表 profile 化（`sut_identity_terms`）；
  legacy SUT 身份词从引擎源码迁出，只存于上游回归 fixture
  （`tests/fixtures/legacy-sut-terms.txt`）与其自身 profile；guard 扫描面锚定
  engine root 的 kit 子树，治理工作区自身文件不在扫描面。附录 A 术语表
  de-SUT guard 条目同步为身份防渗语义。

### A2 — 上游仓纯净化：零示例树，验收临时构造（2026-07-04）

- **批准**：human owner 直接指令（2026-07-04：移除 SUT 挂载物与 examples/，
  并为宪章走本修正案）。决策记录锚点：ADR-BUGATE-002
  （`docs/qa-methodology/BUGATE_HOSTING_CORRECTION_ADR.md`）§3 的
  2026-07-04 更新注记；落地提交 `7fba928`。
- **立法本意**：导入模式的引擎仓必须**在字面上**是纯 kit——上游提交树不携带
  任何 SUT 自动化测试框架，也不收藏任何 SUT 形状的示例树（示例即演示性
  config/profile/usecases/tests 目录组合）。演示与验收职能不因此消失，而是
  改由**临时构造 fixture**（运行时在临时目录搭建、用完即弃）承担——被治理
  布局只在运行时存在，不在上游提交树里存在。
- **§2.3 活动清单第 3 类生效文本**（原文保留于 §2.3）：

  > 3. 运行核心冒烟：模板门（`.shared/skills/bugate/templates` 过 pre-code
  >    语义门）与临时构造 fixture 验收（上游 `tests/` 的双布局写门验收、
  >    de-SUT 元测试、安装器 e2e），以及 full-check 自检。

- **§5.3 验收条生效文本**（原文保留于 §5.3）：

  > **验收**：同一引擎版本在纯 core checkout、临时构造 fixture 与导入布局中
  > 根发现语义一致，由临时构造 fixture 验收测试与 `bugate init` 临时仓 e2e
  > （含 R4 负向对照）在 CI 持续强制。（A4 起不再保留工作台挂载布局。）

- **机制连带**：examples/ 整树移除（含通过态门栈示例、方言 fixture、两种
  布局演示与样例 profile）；de-SUT guard 的上游扫描面去掉 examples 条目；
  full-check 的全部被治理布局探针改为临时构造；入门文档的五分钟路径改以
  模板门 + 临时 fixture 演示 + 安装器 dry-run 呈现。
- **边界（防矫枉过正）**：①模板（`.shared/skills/bugate/templates/`）是引擎
  资产而非示例 SUT 树，保留且随 kit 分发；②上游 `tests/` 的验收测试与
  fixture 词表为上游专用，不随 kit vendor；③本修正案当时未裁决维护者本地
  挂载机制，但该例外已被 A4 废止；④历史文本（§6 锚点、案例研究、既往 ADR
  正文）按最新修订注记读。

### A3 — 使用形态唯一化：「工作台」去形态化（2026-07-04）

- **批准**：human owner 直接指令（2026-07-04）。锚点：本修正案随派生文档
  同批提交（见 git 历史当日提交）。
- **立法本意**：BUGate 作为产品**有且仅有一种使用形态：导入模式**。此前定名
  的"工作台模式"不是 BUGate 的使用形态，而是**开发 BUGate 自身**这件普通的
  软件开发活动——把本仓作为项目在 Claude Code / Codex 中打开，对引擎做完善
  与修改。两者分属不同概念层：导入模式是"**用**这个工具"，开发态是"**改**这
  个工具"；"改工具"不构成工具的使用方式。
- **生效表述**：
  - §0 裁决 2 生效读法：使用规范 = 导入模式（唯一）。打开 BUGate 仓本身即
    进入 **BUGate 自身开发态（maintainer development）**；自 A4 起开发态
    也保持纯净，不得用 symlink / 本地 `profile:` 指针挂载真实 SUT 测试工作区。
    真实 SUT 验收只能在 BUGate core 外部的导入式 SUT 仓或 scratch 仓完成。
  - §2.1 生效读法：形态表退役为二元——使用形态唯一 = 导入；内嵌 = 历史
    形态；原"工作台模式"行读作"开发态"。
  - §2.2 生效节题：「唯一使用形态：导入模式（Imported Governance Layer）」。
  - §2.3 生效节题：「BUGate 自身开发态（maintainer development）」。四类
    活动清单与 **R7 / R8 / R9 保留全部原效力**，语义重述为开发态纪律；其中
    R8 的加强读法：对外叙事**不得把开发态表述为 BUGate 的一种使用方式**。
- **术语与派生连带**：附录 A"工作台模式"词条改注（本修正案标记）；README /
  INIT / CAPABILITIES / profile-schema / CONTRIBUTING / AGENTS / 测试与
  守卫脚本注释中的 "workbench (maintainer) mode / 工作台模式" 表述统一改为
  "developing BUGate itself / BUGate 自身开发（态）"；布局术语 "workbench
  layout" 改称 "engine-development layout"。历史文本（§5 演进计划原文、§6
  锚点、A1/A2 正文、既往 ADR、案例研究）按最新修订注记读。
- **边界（A4 修订）**：A3 当时只改变概念归类；自 A4 起，挂载手续（旧 R7）与
  本地 profile 指针例外同时废止。根发现的 legacy fallback 只服务 BUGate core
  自检与兼容，不再表达"挂载 SUT"能力。

### A4 — 纯 core 迭代：退役维护者挂载例外（2026-07-06）

- **批准**：human owner 直接指令（2026-07-06）：BUGate 将来的迭代也是完全
  纯净的迭代，不存在在 BUGate core 中挂载 SUT 工作区的情形。
- **立法本意**：BUGate core 仓只承载 SUT-neutral 引擎、方法论、双 runtime
  adapter、模板、临时 fixture 与自测。真实 SUT 的 profile、证据、测试代码、
  fixtures、环境事实和验收会话只存在于导入后的 SUT 测试仓，或 BUGate core
  之外的临时/scratch SUT 仓。
- **生效表述**：
  - §0 裁决 2：唯一使用形态仍是导入模式；打开 BUGate 仓只是纯 core 迭代。
  - §2.3 R7：禁止在 BUGate core 内挂载、软链接、嵌套、复制任何真实 SUT
    测试工作区；禁止把 core 的 `bugate.config.yaml` 指向真实 SUT profile。
  - §5.3：根发现验收覆盖纯 core、临时构造 fixture、导入式 SUT 仓；不再覆盖
    旧工作台挂载布局。
- **派生连带**：README / INIT / CAPABILITIES / AGENTS / SKILL / profile schema /
  ADR / transition protocol 中的旧工作台挂载、local profile pointer 例外统一
  改读为 Governed SUT Test Repo / imported SUT repo。历史案例可提旧桥接方式，
  但必须标注为 retired extraction-era practice。

---

## 附录 A — 术语表

| 术语 | 含义 |
|---|---|
| **导入模式** Imported Governance Layer | 默认使用形态：SUT 测试仓为项目根，BUGate 版本化导入其中作为约束层 |
| **BUGate 自身开发态** maintainer development（原"工作台模式" Core Workbench，A4 已退役挂载例外） | 开发 BUGate 本身时把本仓作为项目打开；**不是使用形态**。只能做 SUT-neutral core、模板、临时 fixture 与外部/scratch 导入验收，不得挂载真实 SUT。 |
| **内嵌模式** Embedded | 历史形态：BUGate 手工融合在旧 SUT 测试仓内；已冻结，按迁移协议退役 |
| **治理控制面 / 执行数据面** | BUGate 管"何时、凭何证据允许做"；SUT 框架管"怎么执行" |
| **admission control** | 物理写门的机制类比：拦截写操作、按产物 `gate_status` 裁决、fail-closed |
| **de-SUT guard** | `check_no_sut_terms.py`：身份防渗防线（修订记录 A1）——防止当前 SUT 的身份词与行为性事实渗入可复用的 core/kit 子树；词表由 SUT profile 声明，CI 以 fixture 词表回归，通用卫生检查内置 |
| **profile** | 把导入后的 BUGate kit 绑定到一个 SUT 测试仓内受治理范围的声明式桥接契约；提交进 SUT 仓 |
| **Governed SUT Test Repo** | 导入 BUGate kit 的 SUT 自动化测试仓；测试、产物、fixtures、证据与 profile 的所在地 |
| **Methodology-as-Code** | BUGate 的实现范式：把方法论编译成可版本化、可机器强制执行的门禁工作流 |
