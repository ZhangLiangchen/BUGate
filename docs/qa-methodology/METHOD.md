---
title: "零领域知识 QA 的 AI 辅助自动化测试方法论"
subtitle: "业务理解审计与缺陷发现的双层流程"
version: 1.1
date: 2026-05-11
last_updated: 2026-07-20
applicability: SUT-无关（适用于满足最小输入条件的任意被测系统）
companion: SOP.md
changelog:
  v1.0 → v1.1:
    - 新增 Wave 0：PRD 健康度体检（含 8 个计分维度、可追踪性加分项与缺口报告输出）
    - 修正"多 AI 一致 = 高置信"为"候选高置信，仍需引用审计"
    - Agent 隔离从两层细化为三层（business-model-builder / test-case-designer / test-code-implementer）
    - 新增"测试分层策略"章节，避免缺陷发现机制全部压到 E2E
    - 新增"度量指标"章节
    - 新增"需求变更后的再生成机制"章节
    - 收敛过度承诺表述（"任意 SUT 直接可套用" → "满足最小输入条件时可套用"）
    - 明确"棕地系统适配"为 v2 范围，本版本不解决
  2026-06-15 (additive, 非版本号变更):
    - 新增 §2.4 理论基础与相关工作：把 9 个 Wave 逐条映射到所基于的经典测试方法论与奠基文献/标准（oracle 问题、边界值/状态迁移/风险测试、变异测试、可追溯性、IV&V 等），明确"原创=对既有方法论的编排与综合"而非全新发明。开源诚信归因用。
  2026-07-20 (additive, 对齐 BUGate v0.4.0):
    - Wave 7 从可选路径隔离升级为 designer / implementer / reviewer 可审计生命周期，并明确区分 Wave 1 peer review
    - 新增 strict Memory 锚定、hash-linked receipt、独立 session、drift 重新上锁与安全边界
---

# 零领域知识 QA 的 AI 辅助自动化测试方法论

> *业务理解审计与缺陷发现的双层流程 · v1.1*
> *配套文件：SOP.md（新人执行手册）、`.shared/skills/bugate/templates/`（01–05 gate 产物模板，复制即填）*

---

## 摘要

本文针对一个普遍但被低估的工程现实：**测试工程师在新项目中往往不具备业务背景**——临时调配、跨项目支援、组织重组、新人接入都让"零领域知识"成为 QA 的常态。传统 BDD/SDD 方法论默认存在懂业务的人在场，这一前提在零领域知识场景下不成立。

本方法论提出一套**项目无关、SUT 无关**的工程化流程，让零领域知识的 QA 能够：(1) **审计 AI 的业务理解**，验证其是否真正以 PRD 为依据；(2) **生成高质量、可发现 SUT 缺陷的自动化测试用例**，而不是停留在 happy path 表层覆盖。

完整流程由九个 Wave 构成，分布在三层：

- **Wave 0：PRD 健康度体检** —— 准入门禁，确认 PRD 可作为业务真源使用
- **Wave 1-4（业务理解审计层）** —— 引用回溯审计、多 AI 分歧路由、结构化访谈、行为 oracle
- **Wave 5-8（缺陷发现生成层）** —— 边界提取、状态机非法转换、对抗式红队、变异测试质量门

本版本（v1.1）补齐了 v1.0 的几个关键缺口：Wave 0 防止"对烂 PRD 做精确审计"、Wave 7 职责隔离平衡业务独立性与工程可执行、测试分层策略避免 E2E 成本爆炸、度量指标让过程可观测、变更机制处理 PRD 演进。BUGate v0.4.0 进一步把 Wave 7 实现为可审计生命周期，而不再只是路径黑名单。

本方法论**不解决**棕地系统（已有数千条存量测试用例的 5 年以上代码库）的迁移问题——该场景列为 v2 范围。

---

## 1. 引言

### 1.1 问题陈述

测试工程师在新项目中往往不具备业务背景。在跨项目调配、组织重组、新员工接入、外包团队介入、微服务跨服务测试等场景下，QA 面临两难：

**两难一**：QA 不懂业务，因此无法独立判断 AI 生成的业务理解是否正确。如果直接信任 AI 的产物，AI 的"跑偏"会被原样带进测试用例，QA 沦为放大错误的中转站。

**两难二**：QA 不懂业务，因此也无法独立设计能发现深层缺陷的测试场景。即使 AI 业务理解是正确的，零领域知识的 QA 也很难评估"这套测试用例能不能真的发现 bug"——结果往往是一堆覆盖率漂亮但 mutation score 极低的 happy path 用例。

### 1.2 核心命题

**命题一**：QA 不需要先成为领域专家，也能审计 AI 的业务理解——只要把验证目标从"正确性"改为"**有据性**"。"AI 说的对不对"需要领域知识，"AI 说的有没有依据"只需要阅读理解。

**命题二**：QA 不需要业务直觉，也能生成高质量缺陷发现测试——只要把测试设计从"基于领域直觉"改为"**基于结构化规则**"。边界、状态机非法转换、负面路径、对抗场景这些机制完全机械，与领域无关。

**关于表述强度的说明**：本文（METHOD）使用上述强表述以确立方法论的核心立场。在配套的 SOP.md（新人执行手册）中，相同观点会以更稳健的措辞呈现："新人 QA 的目标不是跳过业务理解，而是通过引用审计、分歧路由、访谈和行为验证，逐步构建最低可用业务模型"。两种表述并不矛盾：前者是方法论立场，后者是执行心态。

### 1.3 方法论的 SUT 无关性与适用前提

本方法论对 SUT 的最小输入要求：

1. **存在 PRD 或等效需求文档**，且通过 Wave 0 健康度体检（综合分 ≥ 60）
2. **存在源代码或可观测的运行系统**
3. **存在至少一位熟悉业务的开发或业务方**（可被访谈）

不满足任一条件的项目，方法论需要降级使用或不适用：

- PRD 不存在或健康度 < 60 → 进入"PRD 反向重建模式"（Wave 0 输出）
- 系统完全不可观测 → 跳过 Wave 4（行为 oracle 验证），其他流程不变
- 无人可访谈 → 流程基本失效，建议先解决组织问题

满足三条件的项目——无论是 Web 应用、移动 App、企业后端、嵌入式系统、区块链系统、AI 模型服务——都可以套用。**已有数千条存量测试的棕地系统不在本版本支持范围**，详见第 12 章。

### 1.4 本文贡献

1. 提出 **Wave 0 PRD 健康度体检** 作为方法论准入门禁，避免"对烂 PRD 做精确审计"
2. 提出"**验证有据性 ≠ 验证正确性**"的认知反转，以引用回溯审计为核心机制
3. 提出"**多 AI 分歧 = 不确定性所在**"的分歧路由模式，将 QA 工作从全局判断者转为分歧识别者
4. 提出"**AI 出题、QA 传话、开发答题**"的结构化访谈协议，将隐性知识低成本档案化
5. 提出五项缺陷发现机制 + 测试分层策略，强制 AI 跳出 happy path 思维定势同时控制 E2E 成本
6. 提出**可审计生命周期职责隔离**架构，用独立会话、交接、receipt 与路径策略平衡业务独立性与工程可执行性
7. 给出项目无关、SUT 无关的落地模板与度量指标体系

---

## 2. 理论基础

### 2.1 AI 在测试生成中的两类失败模式

为了精确设计应对机制，必须把 AI 在测试生成中的失败拆开看。这两类失败成因不同、防御策略不同：

**失败类型一：业务理解偏差**。AI 在 PRD 的某些细节上脑补、误读、或选择性忽略。表现为"AI 生成的用例看起来在测，但测的不是业务真正关心的事"。成因是 LLM 被奖励"自信猜测"超过"承认不知"。应对：第 3-4 章的 **Wave 0 + 业务理解审计层**。

**失败类型二：缺陷发现能力弱**。即使 AI 业务理解完全正确，它生成的测试也倾向于覆盖正向场景。表现为"测试都通过了，但线上还是出 bug"。成因是 LLM 训练数据中"能跑通"和"能发现缺陷"是两个不平衡的目标。应对：第 5 章的 **缺陷发现生成层**。

两类失败叠加，使得 AI 生成的测试看起来覆盖率高、通过率好，但**对真实缺陷的发现能力可能极低**。两层防御必须同时存在。

### 2.2 QA 的角色重定位

| 传统 QA 假设 | 本方法论下的 QA 实际工作 |
|---|---|
| 理解业务，判断 AI 对错 | 审计 AI 引用，判断有据无据 |
| 主导规格生成 | 路由分歧，决定该问谁 |
| 向开发"传授"测试关注点 | 用 AI 生成的问卷"采访"开发 |
| 主观判断测试覆盖是否充分 | 用机械规则与系统行为比对预测覆盖 |

这四个角色都不要求领域知识，但都要求**严格的流程纪律**——后者是 QA 的核心专业能力，与领域无关。

### 2.3 方法论的整体框架

完整流程：

```
PRD                                  源代码                            开发/业务方
  │                                    │                                  │
  ▼                                    │                                  │
Wave 0: PRD 健康度体检                  │                                  │
  ├─ A/B 档 → 进入 Wave 1                                                 │
  ├─ C 档 → 先补洞访谈 ───────────────────────────────────────────────────┤
  └─ D 档 → PRD 反向重建模式（v2）                                         │
  │                                    │                                  │
  ▼                                    │                                  │
Wave 1: 多 AI 独立提取命题              │                                  │
  ├──> AI-A 命题清单 ─┐                 │                                  │
  ├──> AI-B 命题清单 ─┼─> 分歧 diff     │                                  │
  └──> AI-C 命题清单 ─┘    │            │                                  │
                          │            │                                  │
Wave 2: QA 审计           │            │                                  │
  引用回溯 + 分歧路由 ─────┘            │                                  │
              │                        │                                  │
              ▼                        │                                  │
Wave 3: 结构化访谈                      │                                  │
  AI 生成问卷 ───────────────────────────────────────────> QA 采访 ───────┘
              │                                                          │
              ◀──────────────────── 答案回灌 ────────────────────────────┘
              ▼
    已验证业务模型 (Validated Business Model)
              │
              ▼
Wave 4: 行为 oracle 验证（可选）
              │
              ▼
    高置信业务模型 (High-Confidence Business Model)
              │
─────────────────────────────────────────────────────────────────────────
       【业务理解审计层】 ↑   ↓ 【缺陷发现生成层】
─────────────────────────────────────────────────────────────────────────
              │
              ▼
Wave 5: 结构化测试设计（边界、状态机、风险加权）
              │
              ▼
Wave 6: 对抗式红队增强
              │
              ▼
Wave 7: 可审计职责隔离 + 测试代码生成
              │
              ▼
Wave 8: 变异测试质量门
              │
              ▼
        测试套件入库
```

### 2.4 理论基础与相关工作

本方法论的 9 个 Wave **不是凭空发明**——其中多数直接坐落在已有标准、有同行评议论文的经典软件测试方法论之上。本方法论的原创贡献(见 §1.4)在于**面向 LLM 失败模式对这些既有方法论的重新编排与综合**,而非重新发明各个技术本身。明确这一谱系是为了诚实归因,也是为了让读者知道每个 Wave 背后有数十年的工程与学术积累作支撑。

| Wave | 本方法论中的形态 | 所基于的经典测试方法论 | 奠基/标准出处 |
|---|---|---|---|
| Wave 0 PRD 健康度体检 | 8 个计分维度 + 可追踪性加分项 + 缺口报告 | 需求可测性 / 需求的静态测试("testable requirements") | ISO/IEC/IEEE 29148(系统与软件需求工程);ISTQB 基础级"静态测试" |
| Wave 1 多 AI 独立提取命题 | 多源命题 + 分歧识别 | 独立测试设计与评审多样性;规格导出测试(specification-based testing) | IEEE 1028(软件评审与审计);spec-based testing 通用实践 |
| Wave 2 引用回溯审计 | "有据性 ≠ 正确性",逐命题回溯原文 | 需求可追溯性矩阵(requirements traceability) | ISO/IEC/IEEE 29148;DO-178C(航空软件)双向可追溯性原则 |
| Wave 3 结构化访谈 | AI 出题 / QA 传话 / 开发答题 | 需求获取(elicitation)中的结构化访谈 | 需求工程 elicitation 经典技术(structured interviews) |
| Wave 4 行为 oracle 验证 | 用系统行为校准业务模型 | **测试预言问题(the test oracle problem)** | Weyuker, E.J.《On Testing Non-testable Programs》(The Computer Journal, 1982);Barr 等《The Oracle Problem in Software Testing: A Survey》(IEEE TSE, 2015) |
| Wave 5 结构化测试设计 | 边界 + 状态机非法转换 + 风险加权 | **边界值分析、状态迁移测试、风险驱动测试** | Myers, G.J.《The Art of Software Testing》(1979);Beizer, B.《Software Testing Techniques》(2nd ed., 1990);ISO/IEC/IEEE 29119 风险驱动测试 |
| Wave 6 对抗式红队 | 主动构造异常 / 边角输入找 bug | 负向测试、模糊测试(fuzzing)、基于性质的测试(property-based testing) | Miller 等《An Empirical Study of the Reliability of UNIX Utilities》(CACM, 1990,fuzzing 起源);Claessen & Hughes《QuickCheck》(ICFP, 2000) |
| Wave 7 可审计职责隔离 | designer / implementer / reviewer 独立 session + 交接证据链；`agent_roles` 补充路径隔离 | **独立验证与确认(IV&V)** 的独立性原则 | IEEE 1012(系统、软件、硬件验证与确认标准) |
| Wave 8 质量门 | 变异 / oracle falsification | **变异测试(mutation testing)** | DeMillo, Lipton & Sayward《Hints on Test Data Selection: Help for the Practicing Programmer》(IEEE Computer, 1978) |

**如何读这张表**:7/9 个 Wave(2、4、5、6、7、8 以及 0)有明确的标准或奠基论文支撑;Wave 1、Wave 3 是把经典评审与需求获取实践编排进 AI 流水线的新组合。换言之,本方法论的"地基"是公认的,"楼"是原创的。

**原创贡献的边界(与上表互补)**:经典方法论解决的是"如何设计有效测试";本方法论新增解决的是"**当生成测试的是 LLM 时,如何防止它在业务理解和缺陷发现两个环节系统性失败**"。这部分(§2.1 两类失败模式、§2.2 QA 角色重定位、§1.4 七项贡献)在既有测试文献中没有现成答案,是本方法论的真正增量。

> 注:上表只列可确证的奠基文献与国际标准,不含臆造引用。年份/卷期以领域公认信息为准;若用于正式发表,请按目标期刊格式核对并补全页码与 DOI。BUGate 可在黑盒场景中以 oracle falsification / evidence replay 替代传统白盒 SUT 变异,但其思想根仍是 1978 年的变异测试。

---

## 3. Wave 0：PRD 健康度体检

本章定义方法论的**准入门禁**。它独立于审计层之外，因为它的输出对象是 PRD 本身的可用性判定，而不是业务命题。

### 3.1 为什么需要 Wave 0

整套审计层的有效性依赖一个假设：**PRD 是相对可靠的业务真源**。如果 PRD 自身模糊、过期、自相矛盾，"引用回溯审计"会变成"对烂引用做精确引用"——越严格越糟糕，因为审计通过的命题里仍然带着 PRD 本身的错误。

但现实中"PRD 质量差"是连续光谱而不是二元状态。绝大多数项目的 PRD 都在中间地带：核心流程清楚、异常路径模糊、边界值缺失、新旧规则混杂。Wave 0 的任务是**用 8 个机械可执行的计分维度量化 PRD 的可用性，并用可追踪性作为加分项**，据此分流。

### 3.2 8 个计分维度 + 可追踪性加分项

| # | 维度 | 检查问题 | 评估方法 |
|---|---|---|---|
| 1 | 完整性 | 是否覆盖核心流程、异常流程、权限、边界、状态变化 | 对每个核心业务实体，检查上述五类内容是否存在 |
| 2 | 一致性 | 同一规则在不同段落是否冲突 | AI 扫描 PRD 找出潜在矛盾对（同名概念不同描述、同一边界不同值等） |
| 3 | 可证伪性 | 是否能转成明确的 Given/When/Then | 随机抽 10 条 PRD 陈述，AI 尝试转成 G/W/T，成功率作为评分 |
| 4 | 边界明确性 | 数量、时间、金额、次数、状态是否有明确边界 | 所有"涉及量"的描述应有明确数值，统计模糊边界占比 |
| 5 | 角色明确性 | 谁能做、谁不能做是否清楚 | 每个动作都应有明确的 actor，统计无主语动作占比 |
| 6 | 错误处理 | 失败、拒绝、回滚、补偿是否写清楚 | 每个成功路径应有对应的失败路径描述，计算错误路径覆盖率 |
| 7 | 时效性 | PRD 是否仍然代表当前业务 | 与开发确认 PRD 最后更新时间 + 关键变更是否同步 |
| 8 | **可验证性来源** | PRD 中可被外部观测验证的命题占比 | AI 分类每个声明为"可外部观测"vs"主观/无法证伪"，计算前者占比 |
| 9 | 可追踪性（加分项） | 是否能关联需求、接口、页面、埋点或代码模块 | 抽样检查；**不计入综合分**，作为加分项使用 |

第 8 个维度（可验证性来源）是 v1.1 新增——它捕获 v1.0 漏掉的一种 PRD 失败模式：**PRD 充斥"用户体验应当流畅"、"系统应当稳定"这类不可观测的主观描述**。这类 PRD 即使其他八个维度评分都高，下游的测试生成也会失败，因为提取不出可被测试触发的预测。

第 9 个维度（可追踪性）是 v1.0 设计中 GPT-5.5 review 提出、v1.1 主动降级的——大多数项目的 PRD 本身**不包含**与代码/接口/埋点的显式追踪关系，这是方法论应该**输出**的产物（traceability-matrix），而不是 PRD 必须满足的**输入**要求。强制将其作为入门门槛会导致绝大多数项目被判 C/D 档，丧失方法论的可推广性。

### 3.3 评分方式与等级判定

每个维度 1-5 分，前 8 个维度计入综合分（满分 40，换算到 100）。第 9 维度作为 +/-5 的加权调整。

| 综合分 | 等级 | 处理方式 |
|---|---|---|
| ≥ 85 | A：健康 | 直接进入 Wave 1，标准流程 |
| 70-85 | B：可用但有缺口 | Wave 1 正常跑，**缺口直接进入 Wave 3 访谈池** |
| 60-70 | C：高风险 | 暂停 Wave 1，**先做 PRD 补洞访谈**，补洞后重新跑 Wave 0 |
| < 60 | D：不可用 | 进入"PRD 反向重建模式"（v2 范围，本版本暂不支持） |

注意：**等级判定是辅助信号，不是 Wave 0 的核心产物**。

### 3.4 Wave 0 的核心产物：结构化缺口报告

Wave 0 真正有用的输出**不是等级**，而是一份具体到 PRD 段落的缺口清单。结构如下：

```yaml
# .ai/wave-0/prd-gap-report.yaml
gaps:
  - id: GAP-001
    section: "PRD §3.2"
    dimension: error_handling
    issue: "描述了成功路径但未说明失败/超时行为"
    severity: high
    suggested_interview_question: Q-001
  - id: GAP-002
    section: "PRD §4.5"
    dimension: boundary
    issue: "涉及金额操作但未明确上下限"
    severity: critical
    suggested_interview_question: Q-002
  - id: GAP-003
    section: "PRD §6.1"
    dimension: verifiability
    issue: "'响应迅速'未给出可观测的延迟阈值"
    severity: medium
    suggested_interview_question: Q-003
```

这份报告**直接成为 Wave 3 访谈池的种子**——Wave 0 与 Wave 3 在此自然衔接：Wave 0 发现缺口，Wave 3 通过访谈补上。等级判定只用来决定路由（直接进 Wave 1 vs. 先补洞）。

### 3.5 Wave 0 自身的局限

Wave 0 是**机械检查**而非**语义判断**。它能发现"PRD 没写错误处理"、"PRD 没给金额边界"、"PRD 充斥主观描述"，但**无法发现"PRD 写的错误处理是错的"**——后者本身就需要业务知识。

这一限制是设计有意的：Wave 0 的设计目标就是用机械检查筛掉粗糙错误，把语义层面的判断留给后续 Wave。零领域知识的 QA 完全可以执行 Wave 0；语义判断由 Wave 1-4 通过引用审计与访谈协议处理。

---

## 4. 业务理解审计层（Wave 1-4）

本章解决"AI 是否真正理解了 PRD"。核心思路：把 QA 的判断责任从"业务正确性"降级为"产物有据性"。

### 4.1 Wave 1：多 AI 命题独立提取

**机制设计**：让两到三个**初始 prompt 不同、上下文不共享**的 AI 实例独立阅读 PRD，各自产出命题清单。常见配置：

- **AI-A**：标准提示，要求"全面提取业务规则"
- **AI-B**：批判提示，要求"挑出 PRD 中可能存在歧义或不一致的地方"
- **AI-C**（可选）：用户视角提示，要求"以一个真实用户的使用流程为骨架，提取业务规则"

强制每条命题携带可机械校验的引用结构（详见附录 A）：

```yaml
- id: P-001
  statement: "..."              # 业务命题，单句陈述
  source: "PRD §X.Y 段落 Z"
  source_quote: "..."           # ≤200 字符
  confidence: high|medium|low
  type: invariant|flow|boundary|state_transition|error_handling|permission
```

### 4.2 Wave 2：QA 审计——引用回溯 + 分歧路由

**QA 的审计动作**（不需要业务背景）：

1. 抽取所有命题，逐条核对 `source_quote` 是否在 PRD 中真实存在
2. 核对引用段落是否合理支持 statement——不需要懂业务，只需要判断"原文这么写，能不能推出 AI 这个结论"
3. 引用缺失、引用错误、原文与 statement 明显不符的命题——直接打回

确定性脚本对三方命题做语义聚类，分类：

| 类别 | 含义 | 处理 |
|---|---|---|
| 多 AI 一致 | 三个 AI 都提出语义等价命题 | **候选高置信，仍需引用审计通过** |
| 部分缺失 | 部分 AI 提出，部分未提 | 标注，进入访谈池 |
| 对立命题 | AI 间命题语义对立 | 必然进入访谈池，且优先级最高 |
| 孤证 | 仅单个 AI 提出 | 默认不进入，作为补充提议留档 |

**v1.0 → v1.1 的关键修正**：v1.0 中"多 AI 一致"直接标为"高置信"是过度自信。多 AI 一致只能说明"这条命题在文本上显眼 + 多个模型都倾向于这样解释"，不能跳过引用审计。以下场景多 AI 极易**一起错**：

- PRD 表述模糊但业内惯例明显，AI 按惯例脑补
- PRD 使用项目内部黑话，多个 AI 都误解
- PRD 缺少异常路径，多个 AI 都默认合理行为
- 需求里有新旧规则冲突，多个 AI 都选了更顺的那一条

因此修正后的规则：**多 AI 一致是"候选高置信"信号——它降低了访谈优先级，但不能跳过引用回溯审计**。

### 4.3 Wave 3：结构化访谈协议

零领域知识的 QA 最大的工程价值，是把开发或业务方的隐性知识低成本档案化。

**第一步：AI 生成访谈问卷**

输入：Wave 2 产生的低置信命题 + 分歧命题 + **Wave 0 缺口报告中的 suggested_interview_question**
输出：结构化问卷（详见附录 B），每题严格遵守：

- **每题必须是封闭性问题**（multiple choice 或 yes_no）——避免开放式问题让被访者长篇大论
- **必须有"以上都不是"的兜底选项**——防止 AI 选项设计漏了真相
- **每题标注触发原因**（哪条命题/缺口触发了它）
- **问卷总时长 ≤ 45 分钟**，否则被访者疲劳

**第二步：QA 执行访谈**

QA 拿问卷约开发 30-45 分钟，逐题问、逐字记录。**QA 不需要理解答案**——只是高保真的传递通道。

**第三步：答案回灌**

把访谈记录交给 AI，让它据此修正业务理解，生成更新版命题清单。然后**重新执行 Wave 2 引用回溯审计**——这次引用源除 PRD 之外，还加上 `interview-record-DATE.yaml`，每条新增/修改的命题必须能回溯到某条访谈答案。

经过 1-2 轮访谈循环后，命题库整体置信度通常达到可下游水平。

### 4.4 Wave 4：行为 oracle 验证（可选）

对于已部署、可观测的 SUT，可以用**系统自身**作为业务真源的额外验证。

**机制**：对每条命题，要求 AI 给出可执行预测——"在场景 X 下，系统应该产生输出 Y"。QA 触发场景 X，观察实际输出，比对预测。

- 实际 == 预测 → 命题与系统行为一致
- 实际 != 预测 → 三种可能：AI 理解错、系统有 bug、PRD 与系统行为不一致

第三种情况本身就是有价值的发现，可能意味着 PRD 落后于系统、或系统违反 PRD。

### 4.5 审计层的产物

完成 Wave 1-4 后，应产出一个**高置信业务模型**（`.ai/validated-model/`），包含：

- `propositions.yaml`：所有已验证命题
- `interview-records/`：所有访谈记录，逐字
- `behavioral-oracle-results/`：行为验证记录（如适用）
- `unresolved.yaml`：**经过所有审计步骤仍未确定的命题，明确标注为"已知未知"**

最后一项极重要：**显式列出"已知未知"，而不是把所有命题都标为已验证**。这是诚实工程的体现。

---

## 5. 缺陷发现生成层（Wave 5-8）

业务理解审计层产出"系统应该做什么"，但仅靠它生成测试，AI 仍偏向 happy path。本章的任务是：**在已验证业务模型上，强制 AI 系统化探索缺陷可能藏匿的区域**。

### 5.1 Wave 5：结构化测试设计

**5.1.1 边界提取与覆盖**

PRD 中包含大量显式与隐式边界。提取规则（与领域无关）：

| 关键词类别 | 示例 |
|---|---|
| 数值边界 | "最多 / 最少 / 不超过 / 至少 / 恰好" |
| 时间边界 | "在 X 内 / 超过 X 后 / X 之前 / 每 X" |
| 基数边界 | "唯一 / 至多 X 次 / 仅一个" |
| 状态边界 | "仅在 X 状态下 / 只有当 X 时" |
| 权限边界 | "仅 X 角色 / 必须已 Y / 禁止 Z" |

对每个边界**强制要求覆盖四组测试**：远低于、刚低于、恰在、刚高于、远高于（合并为四组实际为五点）。例如"最多 1000 个并发连接"必须覆盖 0 / 999 / 1000 / 1001 / 5000。

**5.1.2 状态机覆盖**

对 PRD 描述的所有状态机，强制覆盖：

1. **每个状态可达性**：至少有一条路径到达
2. **每个合法转换**：每条 PRD 描述的合法状态转换都有测试
3. **关键：每个非法转换被拒绝**——这是 AI 最容易遗漏、bug 最容易藏匿的部分

**5.1.3 风险加权优先级**

不是所有业务命题同等重要。风险维度（与领域无关）：

| 维度 | 评估问题 |
|---|---|
| 影响范围 | 出错时影响多少用户/订单/交易？ |
| 经济影响 | 涉及资金、计费、合规？ |
| 安全影响 | 涉及认证、授权、数据隐私？ |
| 可逆性 | 错误能否回滚？ |
| 检测延迟 | 错误能立即发现，还是潜伏期长？ |

综合得分高的命题获得**更多测试用例配额**，低风险命题保留基本覆盖。

### 5.2 Wave 6：对抗式红队增强

引入一个独立 AI agent，prompt 明确定位为"红队/攻击者"：

> 你是渗透测试与故障注入专家。给定以下业务规格，你的任务是设计场景，目的是：(1) 让系统进入未定义行为；(2) 违反业务不变量；(3) 绕过权限或边界检查；(4) 利用并发或时序问题；(5) 利用资源耗尽。

产物是**攻击场景清单**（详见附录 D），每条标注：攻击路径、涉及的业务不变量、预期防御行为。

QA 拿清单访谈开发：

- "已处理" → 生成测试验证防御真实存在
- "未处理" → 这就是 bug，进入缺陷登记
- "不在范围" → 记录决策原因，进入未来风险档案

### 5.3 Wave 7：测试生命周期的可审计职责隔离

Wave 7 与 Wave 1 不是同一种"多 agent"：

- **Wave 1** 是同一 pre-code 设计阶段内的独立 Codex/Claude peer review，目标是暴露理解分歧；peer 是只读分析 worker，不是生命周期 actor。
- **Wave 7** 是 `designer` → `implementer` → `reviewer` 的跨阶段职责隔离，目标是证明谁在哪个 session 接受了哪份当时的证据快照。

BUGate v0.4.0 把 Wave 7 实现为以下状态链：

```text
designer pre-code --auto
  → 01/02/03/03A/03B 过门
  → 人类显式接受 03B
  → designer 记录人工决定并 strict-Memory handoff
  → 不同 session 的 implementer exact-ID accept
  → Layer 4 解锁
  → implementer 携实现文件 hash handoff
  → 不同 session 的 reviewer exact-ID accept
  → post-run / 04 / 05
  → reviewer completion
```

每个 UC 的 `00_role_evidence/` 保存 append-only receipt 与最小 `chain.json`。Receipt 链接前序 hash，并快照 profile、pre-code 工件、实现文件与 post-run 证据。角色转换边界通过 Memory Service 的 exact content hash 严格验证；每次普通编辑只在本地验 receipt/hash，不请求 Memory Service。Profile、pre-code 工件或实现文件发生 drift 后会自动重新上锁，必须追加新 generation，禁止删除 evidence 来 reset。

Wave 7 还保留一个独立的路径策略 `agent_roles`：

| 机制 | 解决什么 | 配置 |
|---|---|---|
| `role_governance` | phase、session、handoff/acceptance、receipt 与 drift | 只接受 `designer` / `implementer` / `reviewer` 生命周期 token |
| `agent_roles` | 某角色的禁读/禁写路径 | profile 可定义业务所需的角色名与 bare-list / `read` / `write` 正则 |

两者互补，不能合并成一个含义模糊的配置块。例如 implementer 可读 Page Object、fixture 与测试 helper，而 profile 可通过 `agent_roles.implementer.read` 禁止它从业务实现重新派生 oracle。SUT 契约与允许读取的适配层必须由 imported repo 自己的 profile 声明，Core 不猜测路径。

03B 是不可代理的人工门：peer bridge 可以生成 `gate_status: pending`，但 agent 不得自行改为 passed、不得冒充 `approved_by`。`bugate-role approve` 只记录**已经发生**的人工接受，不修改 03B，也不构成密码学身份认证。

### 5.4 Wave 8：变异测试质量门

**变异测试核心思想**：在源代码中人为植入小改动（变异），看测试套件能否检测出。

- 能检测 → 测试对该代码路径敏感
- 不能检测 → 测试在该路径上是盲区

**QA 不需要懂代码或业务即可执行**：现成工具（mutmut、pitest、stryker 等）自动产出变异，QA 只需保证 mutation score 达到团队约定阈值（通常 ≥ 75%-85%）。

**机制集成**：

- 测试生成完成后，CI 自动跑变异测试
- mutation score < 阈值 → 自动定位"哪些变异未被任何测试发现"
- 该变异对应的代码区域 → 反向触发 Wave 5/6 补强

变异测试是审计层之外**另一道独立的客观质量门**：它不依赖任何文档，只依赖代码与测试本身。

---

## 6. 测试分层策略

**v1.1 新增章节**：v1.0 默认所有缺陷发现机制都落到 E2E 层，工程上不可行。E2E 慢、脆、CI 成本高、flakiness 严重。本章给出分层映射，避免成本爆炸。

### 6.1 测试金字塔下的机制分配

| 测试类型 | 应优先落在哪一层 | 备注 |
|---|---|---|
| 纯业务规则边界 | API / service / contract test | 单元测试若可达成即更佳 |
| 核心用户路径 | E2E | 但只挑最关键的 3-5 条 |
| 状态机合法转换 | API / domain-level 优先 | 少量 E2E 兜底 |
| 状态机**非法转换**被拒绝 | API / service 层 | 非法转换数量多，不适合 E2E |
| 权限绕过 | API + E2E 混合 | API 验证完整规则、E2E 验证 UI 入口 |
| 并发 / 时序 | integration / fault-injection | E2E 难以稳定复现并发 |
| UI 展示和交互 | E2E | 这是 E2E 的核心价值 |
| 资金 / 订单 / 不可逆操作 | E2E + API 双保险 | 高风险场景双层验证 |
| 对抗式红队场景 | 视攻击类型选择 | 资源耗尽 → 性能测试层；权限绕过 → API 层 |

### 6.2 分层判定原则

对每条业务命题、每个边界、每个状态转换、每个对抗场景，按以下顺序判定测试层：

1. 如果**不需要 UI 交互**且**不涉及多服务集成** → API/单元层
2. 如果**涉及多服务集成但不需要 UI** → integration 层
3. 如果**必须验证 UI 行为或完整用户旅程** → E2E
4. 如果**涉及并发/时序/故障** → 专项测试层（性能、混沌工程）

E2E 测试用例总数应当控制在团队可维护的范围内（经验值：< 200 条核心场景），其余通过下层测试承担。

### 6.3 不在本版本范围

性能测试设计、安全渗透测试设计、可访问性（a11y）测试设计虽然也是测试体系的重要组成，但与"业务理解审计"关系较弱，本方法论暂不涵盖。建议作为独立体系建设。

---

## 7. 集成流程：完整 Wave 链路

```
Wave 0   PRD 健康度体检                   → prd-gap-report.yaml + 路由决定
                                             │
                  ┌─ A/B 档 ────────────────┤
                  │                          │
Wave 1   多 AI 命题独立提取                  → .ai/raw-propositions/
                  │
Wave 2   QA 引用审计 + 分歧识别              → audit-report + unresolved.yaml
                  │
Wave 3   AI 生成访谈问卷 → QA 执行 → 回灌    → .ai/validated-model/
                  │
Wave 4   行为 oracle 验证（可选）            → behavioral-oracle-results/
                  │
        【高置信业务模型形成】
                  │
Wave 5   结构化测试设计                      → boundary-catalog, state-machines,
                                                risk-priorities, test-design/
                  │
Wave 6   对抗式红队增强                      → adversarial-scenarios/
                  │
Wave 7   可审计职责隔离 + 测试实现       → 00_role_evidence/ + tests/...
                  │
Wave 8   变异测试质量门                      → mutation-test-reports/
                  │
                  ▼
            测试套件入库
```

**关键检查点（必须人工介入）**：

- Wave 0 结束：QA Owner 签字确认路由决定
- Wave 2 结束：QA Owner 审计签字
- Wave 3 访谈：开发或业务方独立填答（不与 AI 提议讨论后再答）
- Wave 6 红队场景：开发确认是否已防御
- Wave 8 变异测试：未达标拒绝发布

所有检查点遵循**强制选择而非橡皮章**的设计——人审字段必须显式填写，AI 工具不能写入。

---

## 8. 度量指标

**v1.1 新增章节**：方法论没有度量就没有改进信号。本章定义可观测的过程指标与结果指标。

### 8.1 过程指标（每个 Wave 结束时统计）

| 指标 | 含义 | 目标值 |
|---|---|---|
| **AI 命题引用错误率** | Wave 2 中引用缺失或不符的命题占比 | < 10% |
| **unresolved 收敛率** | 每轮 Wave 3 后未决命题减少比例 | > 60%/轮 |
| **访谈问题命中率** | 访谈问题中开发选择"以上都不是"的占比 | < 20% |
| **每条命题平均测试数** | 总测试数 / validated 命题数 | 视风险等级，3-10 |
| **高风险命题覆盖率** | 高风险命题中已生成测试的占比 | 100% |
| **边界覆盖率** | boundary-catalog 中已覆盖的边界占比 | 100% |
| **非法状态转换覆盖率** | 非法转换中已生成拒绝测试的占比 | 100% |

### 8.2 结果指标（CI 与上线后统计）

| 指标 | 含义 | 目标值 |
|---|---|---|
| **mutation score** | 测试套件能检测出的代码变异占比 | ≥ 75% |
| **flaky rate** | 同一测试在重复运行中通过率波动 | < 2% |
| **缺陷发现率** | 测试发现的缺陷数 / 单位时间 | 趋势上升 |
| **线上逃逸缺陷回溯命中率** | 线上 bug 中"对应业务命题已 validated"的占比 | < 30% |

最后一项是元指标：**它衡量方法论本身是否有效**——如果线上逃逸 bug 大量发生在已 validated 的命题区域，说明审计层有系统性盲区，需要回顾。

### 8.3 度量的合理使用

度量是诊断信号，不是绩效指标。**禁止将上述指标作为个人 KPI**，否则会激励数字游戏（如人为放宽 unresolved 标准以提高收敛率）。

### 8.4 事故驱动回归（让 §8.2 元指标有机制可依）

§8.2 的「线上逃逸缺陷回溯命中率」只是一个**待测量的数字**，需要一个机制让它闭环：

- **每个确认缺陷都要生成一条具名回归用例**——无论它来自 Wave 6 对抗审查、Wave 8 变异分数缺口，还是 §8.2 统计到的线上逃逸 bug——在缺陷关闭前，必须产出一条挂到该事故的命题/用例（在 `04_execution_report.md` 与 `05_knowledge_update.md` 的「Regression Cases」表中登记）。
- **测试设计优先投向「曾经出过 bug 的区域」**：历史缺陷密度高的地方，下一轮用例覆盖优先级更高。
- SUT 特有的事故库路径、断言规则号、环境名留在 **SUT profile**，不进 BUGate 核心。

---

## 9. 需求变更后的再生成机制

**v1.1 新增章节**：PRD、代码、自测文档都会演进。本章定义当源发生变化时，validated-model 如何级联更新。

### 9.1 哪些变化触发再生成

每个 validated-model 产物（propositions.yaml、state-machines/*.yaml、boundary-catalog.yaml）的 frontmatter 携带 `source_hashes`：

```yaml
source_hashes:
  prd_sections:
    - path: "PRD/checkout-flow.md"
      hash: "sha256:abc..."
  source_files: []  # 审计层不引用源码
  interview_records:
    - id: "IR-042"
      hash: "sha256:def..."
```

CI 定期跑 `detect_stale.py`：

- 对比 frontmatter 中 hash 与当前文件实际 hash
- 不一致 → 标记该产物为 `stale`
- 任何 stale 产物阻止其依赖的测试用例进入新一轮 CI 通过

### 9.2 stale 状态的处理流程

| stale 来源 | 处理 |
|---|---|
| PRD 段落变更 | 重跑 Wave 1（该段落对应区域） + Wave 2 + 视情况 Wave 3 |
| 访谈记录被修订 | 重跑 Wave 2 的引用审计（增加访谈记录为引用源） |
| 状态机定义变更 | 重跑 Wave 5 的状态机覆盖 + Wave 7 测试代码生成 |

### 9.3 关于"PRD 改了不要重跑全套"

完整 6-8 周流程不可能每次 PRD 改动都重跑。原则是：

- **只重跑受影响的范围**（用 source_hashes 精确定位）
- **小改动走轻量档**（仅 Wave 2 引用审计 + Wave 5 局部更新）
- **大改动（核心流程、状态机、风险模块）走完整档**

---

## 10. 落地实施模板

> **方法论与已发布引擎的关系。** 本章讲「如何在一个项目里落地 BUGate」。先区分两类东西：
>
> - **方法论工作产物**（§1–§9 描述的 `.ai/` 下 Wave 产物、命题库、访谈记录等）：它们是你在分析过程中**逐步产出**的中间件，不是仓库预置文件，也不是安装前提。把它们放在一个工作目录（例如 `.ai/`）即可。
> - **已发布的 BUGate 引擎**（本仓库 clone 即得）：pre-code 治理被实现为 **01–05 gate 产物栈** + `scripts/` 门与编排 + `.shared/skills/bugate/` 技能与 adapters。**这才是安装契约。** 下面的目录结构、hook 与初始化清单都以已发布引擎为准。
>
> 9-Wave 方法论的产物会**收敛**到 gate 产物栈：命题/oracle 落到 `01_business_brief.md`，层级决策落到 `02_testability.md`，用例清单落到 `03_inventory.yaml`，以此类推。

### 10.1 已发布引擎的目录结构（项目无关）

```
BUGate-core/
├── AGENTS.md                          # agent 协议；CLAUDE.md 指向它
├── bugate.config.yaml                 # core 默认配置；不指向任何 SUT profile
├── .agents/skills/                    # Codex 官方 repo-skill 发现桥
├── .codex-plugin/plugin.json          # Codex plugin manifest
├── .claude-plugin/plugin.json         # Claude Code plugin manifest
├── skills/                            # plugin-root shared skill symlinks
├── commands/                          # plugin-root Claude command adapters
├── agents/                            # plugin-root Claude gate agents
├── hooks/hooks.json                   # plugin-root lifecycle hooks
├── scripts/                           # 纯标准库的门 + 编排引擎
├── bin/                               # 纯脚本包装器
├── .shared/skills/bugate/
│   ├── SKILL.md
│   ├── references/                    # 各层 gate 判据 + profile-schema
│   ├── templates/                     # 01–05 gate 产物模板（+ 可选 01a/01b/02a）
│   └── adapters/{claude,codex}/       # 各 runtime 的 agent / 命令路由卡
├── docs/qa-methodology/               # 本方法论文档集（METHOD/SOP/...）
└── tests/                             # 上游专用：临时构造 fixture 验收 + de-SUT 元测试（不随 kit 分发）

imported-sut-test-repo/
├── bugate.config.yaml                 # 提交在 SUT 仓；标记 imported project root
├── bugate.profile.yaml                # 提交在 SUT 仓；声明 artifact_dir / guards / memory namespace
├── .bugate/                           # vendored BUGate kit（默认 installer 形态）
├── docs/usecases/<UC>/
│   ├── 00_role_evidence/              # 启用 Wave 7 后的 append-only 证据链
│   ├── 01_business_brief.md           # 每个用例一套 gate 产物
│   ├── 02_testability.md
│   ├── 03_inventory.yaml
│   ├── 03a_test_cases.md
│   ├── 03b_adversarial_cases.yaml
│   ├── 04_execution_report.md
│   └── 05_knowledge_update.md
└── .ai/                               # （可选）§1–§9 方法论工作产物，分析中间件，非安装前提
    ├── wave-0/  raw-propositions/  audit/  interviews/  validated-model/  ...
    └── scripts/                       # 方法论示意工具（如确定性合并）；核心未发布同名脚本
```

### 10.2 Wave 7 角色治理与路径隔离（已发布机制）

Core 默认 `role_governance.mode: off`，因此不挂 SUT profile 的自开发不会被锁死。Imported repo 显式选择 `required` 后，由两类 hook 共同执行：

- `check_bugate.py` 证明 pre-code 工件已过门；
- `check_role_evidence.py` 证明当前 phase 角色/session 和本地 receipt 链有效。

Layer 4 只在 designer handoff 已被不同 session 的 implementer 接受后解锁；04/05 只在 implementer handoff 已被 reviewer 接受后开放。`check_role_evidence.py` 对每次受治理编辑只做本地 hash/chain/drift 验证，strict Memory 请求只发生在 `approve` / `handoff` / `accept` / `complete` 转换边界。对 `00_role_evidence/**` 的直接 agent-tool 编辑一律拒绝。

`scripts/check_agent_role_paths.py` 仍是独立的**路径访问策略**：当前角色来自 `BUGATE_AGENT_ROLE`，禁读/禁写正则由 active profile 的 `agent_roles` 提供，Core 不内置 SUT 路径。Claude 保持两组 matcher：`Edit|Write` 调用写门，`Read|Edit|Write` 单独调用路径隔离；Codex `apply_patch` 上的四个 guard 都必须通过。

profile 里的路径隔离示例：

```yaml
# bugate.config.yaml 或被引用的 profile
agent_roles:
  builder:                      # 方法论工作角色：不读实现/测试码
    - "^src/.*"
    - "^tests/.*"
  designer:                     # 用例设计者：不读实现与 API spec
    - "^src/.*"
    - "^api-spec/.*"
  implementer:                  # 测试实现者：不读业务实现，避免照搬实现当预期
    read:
      - "^src/.*"
    write:
      - "^docs/business/.*"
```

`agent_roles` 的自定义 token 不会自动成为生命周期 actor；`role_governance.phases` 仅接受 `designer` / `implementer` / `reviewer`。完整配置、状态与恢复契约见 [`ROLE_GOVERNANCE_PROTOCOL.zh-CN.md`](ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)。

### 10.3 项目初始化清单（已发布引擎）

1. 接入 BUGate 引擎（导入模式：通过 `scripts/bugate_init.py <sut-repo>`、plugin 或 vendor / 子模块进入 SUT 测试仓）。真实 SUT 的工作区根 = 自 CWD 向上最近的 `bugate.config.yaml`；BUGate core 本身保持纯净，只运行模板门、临时构造 fixture 与 installer 对外部/scratch 仓的验收。
2. 写一个 SUT profile（键契约见 `references/profile-schema.md`；`scripts/bugate_init.py` 会为导入仓脚手架同形状文件），设置 `artifact_dir`/`artifact_dir_template`、`guarded_path_regex`、Memory namespace，并显式选择 `role_governance.mode`。`agent_roles` 只在需要路径隔离时配置。
3. 启用 required 治理时，分别通过 `bin/bugate-role run --role designer|implementer|reviewer -- <agent-command>` 启动三个独立会话。Hook 子进程无法把环境变量 export 回父进程；Desktop 必须从带角色环境的进程启动或重开会话。
4. 先单独运行 `python3 scripts/sdtd_orchestrator.py <artifact_dir> --init`（复杂用例加 `--full-sdtd` 一并生成 01a/01b/02a），再单独运行 `... <artifact_dir> --auto`。`--init --auto` 不是合法的日常入口。
5. 逐层跑门：`check_bugate_brief_semantics.py` / `check_bugate_layer2_semantics.py` / `check_bugate_inventory_semantics.py`，再 `check_bugate_v13_semantics.py <artifact_dir> --scope pre-code`。
6. 需要双 agent 互审时跑 `sdtd_multiview_cli_bridge.py` / `sdtd_adversarial_cli_bridge.py`。Peer 子进程会清除父会话的角色/session/receipt 身份，不会被当成 Wave 7 actor。
7. 03B 经真实人工接受后，designer 使用 `bugate-role approve` 记录决定，再 handoff；implementer 和 reviewer 各自在新 session 用 exact Memory ID accept。已记录 human-acceptance receipt 后不要重跑会重生成 03B 的 `--auto`。
8. reviewer acceptance 后跑 `self_healing_mvp.py` + `generate_sdtd_reports.py` 或 orchestrator post-run 产出 04/05，最后用 `bugate-role complete` 记录命令、exit code 与 evidence hash。
9. 保持 runtime hook 表面一致：`check_bugate.py` 和 `check_role_evidence.py` 共同保护写入，`check_agent_role_paths.py` 单独保护路径读写。Codex hook hash 变化后必须 re-trust，未 re-trust 时不得声称 Wave 7 已激活。
10. （可选）把 §1–§9 的 9-Wave 方法论工作产物放在 `.ai/` 下作为分析中间件，最终收敛到 01–05 gate 产物栈。

---

## 11. 适用性与边界

### 11.1 适用场景

- 新人/外援 QA 接手陌生项目
- 跨多个 SUT 的测试团队
- PRD 质量参差但仍可用（B 档及以上）的项目
- AI 辅助测试已在用、但效果不稳定的团队

### 11.2 不适用或需要降级的场景

| 场景 | 处理 |
|---|---|
| 完全没有 PRD 或 PRD < 60 分 | Wave 0 输出 D 档，本版本不支持 |
| 探索性测试 | 方法论以结构化为主，不适合纯探索 |
| 极小规模项目（< 2 周测试工作量） | 流程开销超过收益，不建议 |
| 强保密项目（不能让 AI 接触 PRD/代码） | 合规先于方法论 |
| **棕地系统（已有数千条存量测试）** | **本版本不解决，列为 v2 范围** |

### 11.3 v2 范围声明：棕地适配

本版本（v1.1）默认 greenfield 场景。棕地适配涉及：

- 存量测试的反向标注（哪些测试对应哪些业务命题）
- 存量与新生成测试在同一 CI 的共存策略
- 高风险模块优先迁移 vs. 全量迁移的决策
- 存量测试发现的 bug 不在 validated-model 中时的处理

这是一个独立的难题，需要单独的方法论延展。本版本明确不解决，避免在主流程中稀释边界。

### 11.4 已知局限

- 不解决"完全未文档化"的隐性知识
- 不替代领域专家的最终判断
- 变异测试有覆盖盲区（架构、性能、安全缺陷）
- 方法论疲劳（6-12 个月后需要重新校准）
- Wave 0 是机械检查，不识别"PRD 写的错误处理本身是错的"——这种语义错误由 Wave 1-3 处理

---

## 12. 推进路线

**阶段一（1 周）：Wave 0 + 引用审计最小可行版本**

只跑 Wave 0 + 单 AI 命题提取 + QA 引用审计。立即开始捕获 PRD 缺口与 AI 引用错误。这一步成本最低、收益立竿见影。

**阶段二（2 周）：多 AI 分歧路由 + 结构化访谈**

加入多 AI 并行、确定性合并、访谈协议。审计层完整跑通。

**阶段三（2 周）：缺陷发现层基础机制 + 测试分层**

加入边界提取、状态机覆盖、风险加权、分层映射。

**阶段四（1-2 周）：红队 + 变异测试 + 完整 CI 集成**

加入对抗式 agent 与变异测试质量门，接入 CI/CD。

整体周期约 6-7 周。每阶段结束做团队回顾，不达预期的阶段优先调优。

---

## 13. 结论

本文针对零领域知识 QA 在 AI 辅助测试生成中面临的两难，提出一套项目无关、SUT 无关的双层方法论。v1.1 在 v1.0 基础上补齐了 Wave 0（PRD 健康度体检）、三层 agent 隔离、测试分层策略、度量指标、变更机制五个关键缺口，并明确将棕地系统适配列为 v2 范围。

**核心信念**：QA 的专业价值不在于"懂某个具体业务"，而在于严格的流程纪律与系统化的质量审计能力。这两项能力是项目无关、SUT 无关、可复用的——一名掌握本方法论的 QA，可以在任意陌生项目中快速产生价值，而不必先用数周时间补齐领域背景。

本方法论不是银弹。它无法解决组织文化层面的问题、完全未文档化的隐性知识、棕地系统的存量测试迁移、或方法论疲劳风险。但它给出一条**结构化、可落地、不依赖个人英雄主义**的工程路径——让"AI 辅助测试"从依赖资深 QA 经验的不稳定实践，变成任何 QA 都能可靠执行的标准化流程。

具体的"明天如何执行"，由配套文件 **SOP.md** 提供。本方法论文档（METHOD.md）回答"为什么"；SOP.md 回答"怎么做"；`.shared/skills/bugate/templates/` 提供"复制即可填写"的 01–05 gate 产物模板（用 `python3 scripts/sdtd_orchestrator.py <artifact_dir> --init` 落地，没有单独的初始化脚本）。

---

## 附录 A：命题输出 Schema

核心字段：`id`、`statement`、`source`、`source_quote`（≤200 字符）、`confidence`、`type`、`risk_scores`、`extracted_by`、`extracted_at`。

## 附录 B：访谈问卷 Schema

核心约束：question_type 限定为 multiple_choice 或 yes_no；options 必须包含"以上都不是"兜底；answer 字段由人工填写，AI 不得写入。

## 附录 C：PRD 缺口报告 Schema

核心字段：`section`、`dimension`、`issue`、`severity`、`suggested_interview_question`。

## 附录 D：对抗场景 Schema

核心字段：`attack_path`、`related_invariants`、`attack_category`、`plausibility`、`dev_review`（人工填写）。

---

*文档版本：v1.1 | 性质：通用方法论 | 配套：SOP.md, .shared/skills/bugate/templates/ | v2 路线：棕地系统适配*
