---
title: "新人 QA 执行手册（SOP）"
subtitle: "基于业务理解约束层方法论的工程化操作指南"
version: 1.0
date: 2026-05-11
companion: METHOD.md
scope: Wave 0 - Wave 3（最小可行闭环）
---

# 新人 QA 执行手册：Wave 0 - Wave 3

> *本文档是 METHOD.md 的配套执行手册。METHOD 解释"为什么"，本文档告诉"明天做什么"。*
> *本期覆盖 Wave 0-3（业务理解审计层最小闭环）。Wave 4-8 在后续版本提供。*

---

## 0. 阅读本文档前

### 0.1 心态校准

新人 QA 接手陌生项目时常被两种压力同时挤压：

- **来自外部**：业务知识不足被视为不专业
- **来自内部**：不懂业务但又必须签字放行测试，于是 rubber-stamp AI 输出

本 SOP 的设计前提是承认两件事：

1. **新人 QA 一开始不懂业务是正常的、合法的、不可避免的**
2. **新人 QA 的目标不是跳过业务理解，而是用结构化流程逐步构建最低可用业务模型**

不需要假装懂，不需要英雄主义地短时间内变成领域专家。按本 SOP 走，5 个工作日内能产出一份高置信业务模型，足以指导后续测试设计。

### 0.2 角色定义

| 角色 | 职责 | 本 SOP 中的称呼 |
|---|---|---|
| QA Owner | 流程执行与审计签字 | "你"（默认读者） |
| Domain Reviewer | 回答业务问题（PM/资深 QA/产品） | "业务方" |
| Dev Reviewer | 确认实现、防御、边界 | "开发" |
| Gatekeeper | 决定是否放行进入下一 Wave | 通常是 QA Owner 或 Tech Lead |

零领域知识的 QA 默认承担 QA Owner 角色。Domain Reviewer 和 Dev Reviewer 通常是开发或资深成员——你不会取代他们的领域知识，你的工作是把他们的知识结构化、档案化、可追溯化。

### 0.3 全局禁止行为（在所有 Wave 中都适用）

- 禁止人工直接修改 AI 输出的 statement 让它"看起来合理"
- 禁止 AI 工具写入需要人工签字的字段（approved_by / answered_by 等）
- 禁止跳过任何 Wave 的"通过标准"
- 禁止把"我觉得差不多了"作为打回或放行的依据——必须引用具体数字或具体引用位置

### 0.4 工具与环境前提

确认以下已就位（以**已发布的 BUGate 引擎**为准，参见 METHOD.md §10）：

- [ ] BUGate 引擎已接入（导入模式 vendor 进 SUT 测试仓 / 开发 BUGate 本身时直接用引擎仓），工作区根可定位（自 CWD 向上最近的 `bugate.config.yaml`；`AGENTS.md` + `.shared/` 哨兵为开发态 fallback），`python3` 可用（核心纯标准库）。
- [ ] 已写好一个 SUT profile（键契约见 `.shared/skills/bugate/references/profile-schema.md`；`scripts/bugate_init.py` 可脚手架同形状文件），设置 `artifact_dir` 等。
- [ ] 用 `python3 scripts/sdtd_orchestrator.py <artifact_dir> --init` 生成 01–05 gate 产物（无独立"初始化脚本"）。
- [ ] PRD / 需求文档可访问，作为 Layer 1 的证据来源。
- [ ] （可选）需要双 agent 互审或角色隔离时，准备好对应 CLI runtime；缺失则脚本确定性回退。
- [ ] 至少一位开发与一位业务方愿意配合（每周 1-2 小时即可）。

> **关于本手册的 9-Wave 产物。** 下文各 Wave 产出的 `.ai/` 工件（`prd-*`、`raw-propositions/`、`validated-model/` 等）是你在分析中**逐步产出的工作中间件**，不是仓库预置文件；同样，`prd-health-checker`、`prd-reader-a/b/c`、`business-model-builder` 是**方法论角色**，由你在所用 runtime 里以子 agent / prompt 扮演，BUGate 核心不预置这些 agent 文件。这些工作产物最终**收敛到** 01–05 gate 产物栈，由 `check_bugate_*_semantics.py` 把关。

### 0.5 Bootstrap：先用一个已通过的测试验证模板可用

把 BUGate gate 套到一个新 SUT profile 之前，先做一次半天级的「模板自校验」，避免在不靠谱的模板上迭代数周：

1. 在该 SUT 里挑**一个稳定、已经通过**的测试，反推它的 01–03 gate 产物。
2. 跑对应的 `check_bugate_*_semantics.py`。若一个**源自已通过测试**的产物却过不了门，说明这是**模板/门的缺陷**——先修模板，再推广。
3. 如果反推任何**单个**产物耗时超过约 30 分钟，说明模板过重——削减它。

把种子测试理解为「挂载的 SUT 工作区里任意一个稳定、已通过的测试」，本步即保持 SUT 无关。

---

## Wave 0：PRD 健康度体检

> *预计耗时：1-2 个工作日 | 关键产物：prd-gap-report.yaml*

### 目标

在投入 Wave 1-8 之前，确认 PRD 处于可作为业务真源使用的状态。低于阈值的 PRD 会导致整套方法论"对烂 PRD 做精确审计"，越严格越糟糕。

### 输入

- 当前项目 PRD 文档（任意格式：Markdown / Word / Confluence / PDF）
- 项目类型标签（如 `finance`, `enterprise`, `consumer`——影响某些维度的阈值）

### 输出

- `.ai/wave-0/prd-health-report.yaml` —— 9 维度评分 + 综合分 + 等级
- `.ai/wave-0/prd-gap-report.yaml` —— 结构化缺口清单，每条带 suggested_interview_question
- `.ai/wave-0/prd-routing-decision.md` —— 进入 Wave 1 / 先补洞 / 反向重建模式的明确决定

### 负责人

- 主：QA Owner
- 辅：Domain Reviewer（可选，对争议项给二意见）

### 操作步骤

**Day 1 上午（约 3 小时）**

1. 检查 PRD 是否可被 AI 读取：
   - Markdown / Word / PDF 可直接 attach
   - Confluence 需先导出
   - 总长度 > 50K 字时，分章节分别处理

2. 调用 `prd-health-checker` agent，对 PRD 执行 9 维度评估：
   ```
   9 维度健康度评估标准见 METHOD.md §3（PRD 健康度评估）
   ```

3. AI 产出 `prd-health-report.yaml` 草稿，结构如下：
   ```yaml
   overall_score: 0     # 0-100
   grade: ""            # A | B | C | D
   dimensions:
     - dim: completeness
       score: 0          # 1-5
       evidence: []      # 具体引用段落
       issues: []        # 发现的问题
     - dim: consistency
       ...
   ```

**Day 1 下午（约 3 小时）**

4. **执行人工 spot check**（这是 Wave 0 最关键的人工动作）：
   - 从 AI 评估结果中随机抽取 5 条**具体判定**
   - 例如："AI 判定第 3 维度（可证伪性）4 分，引用为 PRD §2.1-§4.3" → 你打开 PRD §2.1-§4.3，自己读一遍判断 4 分是否合理
   - 如果 ≥ 2 条判定与你读 PRD 的直观感受明显不符（不是细微差异，是方向性差异），spot check 失败

5. 若 spot check 失败：
   - 重新调用 prd-health-checker，调整 prompt（明确告诉它在哪些维度上偏差大）
   - 重新 spot check
   - 若连续 2 次 spot check 失败，停止 Wave 0，升级到 Tech Lead 决策

6. spot check 通过后，对每个低分维度（≤ 3 分）的 issues 字段，逐条核对 PRD 引用是否存在且合理。AI 提取的 issue 中无 section 引用、或引用不存在的，标记为 invalid 并要求 AI 重新生成

7. 产出最终 `prd-health-report.yaml` 与 `prd-gap-report.yaml`

**Day 2（约 0.5-2 个工作日，视等级）**

8. 根据综合分判定路由：

   | 综合分 | 等级 | 路由动作 |
   |---|---|---|
   | ≥ 85 | A | 写 `prd-routing-decision.md` 标注 "Enter Wave 1"，结束 Wave 0 |
   | 70-84 | B | 写决定文档标注 "Enter Wave 1, gap-report 直接进入访谈池"，结束 Wave 0 |
   | 60-69 | C | **不进入 Wave 1**。约一次 60 分钟会议，与业务方/开发过一遍 gap-report，决定哪些 PRD 现场补、哪些进入访谈池。补完 PRD 后**重新跑 Wave 0** |
   | < 60 | D | 进入 PRD 反向重建模式（v2 范围）。本版本暂不支持，升级到 Tech Lead 决策 |

### 通过标准

**全部满足**：

- [ ] 9 个维度评分完成，每个维度的 issues 字段都有具体 PRD 段落引用（不能空喊"完整性差"）
- [ ] spot check 通过：随机抽 5 条 AI 判定，与人工核对的方向性一致率 ≥ 80%
- [ ] gap-report 中所有 issue 条目都填写了 section、dimension、issue、severity 四个字段
- [ ] gap-report 中 ≥ 80% 的 issue 已生成 suggested_interview_question
- [ ] routing-decision.md 由 QA Owner 签字（写明日期与决定）

### 打回标准

**任一触发即打回**：

- AI 评估的 spot check 连续 2 次失败
- gap-report 中超过 30% 的 issue 没有具体 PRD 段落引用
- 综合分被人为调整以满足某个等级（例如硬把 68 分写成 70 分以避免 C 档处理）

### 禁止行为

- 跳过 spot check 直接信任 AI 评估
- 把"我们 PRD 不太完整"作为 D 档的判定依据——必须列出具体维度的具体缺口才能判 D
- 用单一维度（如"完整性"）的低分直接判定整体不可用——必须用综合分
- 把"PRD 没写测试验收标准"放进缺口报告（这不是 PRD 的职责）
- 把"PRD 与代码不一致"放进缺口报告（这是 Wave 2/4 的工作）

### 常见坏样例

**坏样例 1：评估结果含糊**

```yaml
dimensions:
  - dim: completeness
    score: 2
    evidence: []     # ❌ 没有具体引用
    issues:
      - "PRD 不完整"  # ❌ 含糊
```

正确做法：

```yaml
dimensions:
  - dim: completeness
    score: 2
    evidence: ["PRD §3.1", "PRD §3.4"]
    issues:
      - section: "PRD §3.1"
        problem: "支付流程章节只描述了成功路径，未覆盖支付失败、超时、退款"
        severity: high
```

**坏样例 2：综合分硬调整**

QA 判定综合分 65（C 档），但觉得"再补洞再跑 Wave 0 太费时间了"，于是把某维度从 3 分调整到 4 分，综合分变成 71（B 档）。

这是**最危险的坏样例**——它把方法论的安全网撕了一个洞，且无法被后续 Wave 捕获。等到 Wave 3 访谈时才发现 PRD 缺口太多、问卷做不出来，已经浪费 1 周时间。

**坏样例 3：缺口未引用具体段落**

```yaml
gaps:
  - dimension: error_handling
    issue: "错误处理写得不清楚"  # ❌ 没法行动
```

正确做法：

```yaml
gaps:
  - id: GAP-007
    section: "PRD §4.5 第 3 段"
    dimension: error_handling
    issue: "描述了支付成功后的订单状态变化，但未说明支付超时、银行拒绝、用户中途取消三种情况下订单状态如何流转"
    severity: critical
    suggested_interview_question: Q-007
```

**坏样例 4：把不属于 PRD 的内容放进缺口**

```yaml
gaps:
  - dimension: ???
    issue: "PRD 没有说哪些测试用例必须覆盖"  # ❌ 这是测试设计的工作
```

PRD 的职责是描述业务需求，不是规定测试覆盖。这条不应放入 gap-report。

### 完成后检查清单

- [ ] `.ai/wave-0/prd-health-report.yaml` 已生成，schema 校验通过
- [ ] `.ai/wave-0/prd-gap-report.yaml` 已生成，所有 gap 有 section + dimension + issue + severity 四个字段
- [ ] `.ai/wave-0/prd-routing-decision.md` 已签字
- [ ] spot check 记录已保存在 `.ai/wave-0/spot-check-log.md`
- [ ] 若等级为 B 或 C，访谈池草案（`.ai/wave-0/interview-pool-seed.yaml`）已生成
- [ ] 若等级为 C，PRD 补洞会议已安排
- [ ] 若等级为 D，已升级到 Tech Lead

---

## Wave 1：多 AI 命题独立提取

> *预计耗时：0.5-1 个工作日 | 前置：Wave 0 等级 A 或 B*

### 目标

让 2-3 个独立的 AI 实例各自从 PRD 中提取业务命题，使后续审计能够利用"AI 间分歧"作为不确定性信号。

### 输入

- `.ai/wave-0/prd-routing-decision.md` 标注为 "Enter Wave 1"
- PRD 文档
- （如 Wave 0 等级 B）`.ai/wave-0/prd-gap-report.yaml` 与 `interview-pool-seed.yaml`

### 输出

- `.ai/raw-propositions/ai-a.yaml`
- `.ai/raw-propositions/ai-b.yaml`
- `.ai/raw-propositions/ai-c.yaml`（可选）

每个文件结构遵循命题输出 Schema（见 METHOD.md 附录 A）。

### 负责人

- QA Owner（执行）
- 无需业务方/开发参与

### 操作步骤

**Day 1 上午（约 2 小时）**

1. 启动三个独立的 agent 会话——**必须是新建会话，不能在同一会话上下文中**。这是保证 AI 间独立性的关键。

2. 三个 agent 使用不同的提示策略：

   **AI-A（标准提取）**：
   ```
   你是 prd-reader-a。请阅读 PRD，提取所有业务规则、流程、边界、状态变化。
   按统一的命题字段输出（statement / type / confidence / source / source_quote）。
   每条命题必须有 source 与 source_quote。
   不要推断 PRD 中未明确陈述的内容——若不确定，confidence 标 low。
   ```

   **AI-B（批判性提取）**：
   ```
   你是 prd-reader-b。请阅读 PRD，重点找出可能存在歧义、矛盾、模糊的规则。
   对每条命题，额外标注是否存在替代解释（若存在，confidence 不能高于 medium）。
   ```

   **AI-C（用户流程提取）**：
   ```
   你是 prd-reader-c。请以一个真实用户的完整使用流程为骨架，提取业务规则。
   重点关注用户在每个步骤的输入、系统的响应、可能的异常路径。
   ```

3. 每个 agent 产出后，做命题字段校验。

   > **注（已发布 vs 方法论示意）。** `.ai/scripts/validate_propositions.py` 是本方法论的**示意工具**，BUGate 核心未发布同名脚本。你可以自备一个标准库校验器；或直接用已发布的「独立多视角提取 + 确定性合并」引擎来完成本步——它让 Codex/Claude 各自独立提取命题，再做确定性的命题集合 diff 产出分歧报告：

   ```bash
   # 示意（方法论工具，非核心发布）：
   #   .ai/scripts/validate_propositions.py .ai/raw-propositions/ai-a.yaml
   # 已发布等价路径（独立提取 + 确定性合并/分歧）：
   python3 scripts/sdtd_multiview_cli_bridge.py run-all <artifact_dir>
   ```

4. 字段校验失败的，要求 agent 修正后重新输出

**Day 1 下午（约 1-2 小时）**

5. 对三个 yaml 文件做基本健康检查：
   - 命题数量是否合理（PRD 每千字大约提取 5-15 条命题）
   - confidence 分布是否合理（high 占比 50-70% 为健康）
   - type 分布是否覆盖（invariant / flow / boundary / state_transition / error_handling / permission 至少出现 3 类）
   - source 字段是否都有，且格式一致

6. 任何明显异常（命题数量 < 10 / confidence 全是 high / type 单一）要求 agent 重新生成

### 通过标准

- [ ] 至少 2 个 agent 产出（AI-C 可选）
- [ ] 所有 yaml 文件 schema 校验通过
- [ ] 每个 agent 产出的命题数 ≥ 10（少于这个数通常说明 PRD 章节没被完整读到）
- [ ] 每个 agent 产出中 high confidence 占比在 30%-80% 之间（全高或全低都是异常信号）
- [ ] 命题 type 字段至少覆盖 3 种类别

### 打回标准

- 任何 agent 产出的命题中超过 10% 缺失 source 或 source_quote
- 命题数量异常（过少或过多——过多通常说明 AI 把单一规则拆得过细）
- AI-A 与 AI-B 的总命题数相差超过 3 倍（说明某个 agent 严重偏读）

### 禁止行为

- 在同一会话上下文中跑多个 agent（破坏独立性）
- 把三个 agent 的输出合并交给第四个 agent "总结"——必须用确定性脚本合并（Wave 2 的工作）
- 人工修改命题的 statement 字段——只能要求 AI 重新生成
- 跳过 schema 校验直接进入 Wave 2

### 常见坏样例

**坏样例 1：source_quote 是 AI 改写而非原文**

```yaml
- statement: "用户登录失败 5 次后账号锁定 30 分钟"
  source: "PRD §2.3"
  source_quote: "登录失败 5 次锁定 30 分钟"  # ❌ AI 改写过
```

正确做法：source_quote **必须是原文**，逐字摘录。Wave 2 审计时会核对原文是否真的存在这段文字。AI 改写会导致审计失败。

**坏样例 2：把实现细节当业务规则**

```yaml
- statement: "登录请求通过 POST /api/v1/auth/login 发送"
  type: flow
  source: "代码注释"  # ❌
```

PRD reader 不应读代码，更不应把接口路径当业务规则。这种命题必须打回。

**坏样例 3：confidence 全 high**

某个 agent 产出 50 条命题，全部 confidence: high。这是 AI 自信猜测的典型模式。要求重新生成时，明确指示："对任何在 PRD 中未明确陈述、需要推断的内容，confidence 必须为 medium 或 low"。

### 完成后检查清单

- [ ] `.ai/raw-propositions/` 下至少 2 个 yaml 文件
- [ ] 每个文件 schema 校验通过
- [ ] 命题数量、confidence 分布、type 分布通过健康检查
- [ ] 三个 agent 的会话保持独立，未发生上下文污染

---

## Wave 2：QA 引用审计 + 分歧识别

> *预计耗时：1-2 个工作日 | 核心人工动作：引用回溯审计*

### 目标

通过两个机制收敛 Wave 1 的原始命题：(1) 引用回溯审计去除"看似合理但无据"的命题；(2) 多 AI 分歧识别把不确定性显性化为待访谈问题。

### 输入

- `.ai/raw-propositions/ai-a.yaml`、`ai-b.yaml`、`ai-c.yaml`
- PRD 文档

### 输出

- `.ai/audit/audit-report.md` —— 审计过程与结论
- `.ai/audit/consensus.yaml` —— 通过审计的命题（候选高置信）
- `.ai/audit/unresolved.yaml` —— 进入 Wave 3 访谈池的命题
- `.ai/audit/rejected.yaml` —— 引用错误被打回的命题

### 负责人

- 主：QA Owner（执行所有引用审计——这是核心人工动作）
- 辅：无

### 操作步骤

**Day 1（约 4-6 小时）：引用回溯审计**

1. 跑命题聚类/合并步骤，按 statement 语义相似度聚类，输出四档：
   - `consensus`：多 AI 一致
   - `partial`：部分缺失
   - `conflict`：对立命题
   - `orphan`：孤证

   > **注（已发布 vs 方法论示意）。** `.ai/scripts/merge_propositions.py` 是本方法论的**示意工具**，BUGate 核心未发布同名脚本。已发布的等价能力是 `scripts/sdtd_multiview_cli_bridge.py`：它对独立提取的命题做**确定性**的集合 diff，产出分歧报告（一致 / 分歧 / 孤证）。需要自定义四档聚类时，可基于其输出再加一层标准库脚本。

   ```bash
   # 示意（方法论工具，非核心发布）：
   #   .ai/scripts/merge_propositions.py --input .ai/raw-propositions/ --output .ai/audit/clusters.yaml
   # 已发布等价路径（确定性合并/分歧）：
   python3 scripts/sdtd_multiview_cli_bridge.py run-divergence <artifact_dir>
   ```

2. **逐条做引用回溯审计**——这是零领域知识 QA 最关键的工作：

   对 `consensus` + `partial` 类别的每条命题：

   a. 打开 PRD 对应 section
   b. 找到 source_quote 标记的原文位置
   c. 核对三件事：
      - 原文是否真实存在（逐字）→ 不存在 → 标 `citation_missing`，打回
      - 原文是否能合理支持 statement → 不能 → 标 `citation_insufficient`，打回
      - statement 是否包含原文之外的推断 → 包含 → 标 `inference`，confidence 降为 medium

   d. 通过审计的命题标 `citation_verified`，进入 `consensus.yaml`

3. 对 `conflict` 与 `orphan` 类别的命题：

   - `conflict` 直接进入 `unresolved.yaml`，优先级 high
   - `orphan` 经引用审计后，若 citation_verified，进入 `unresolved.yaml`，优先级 medium；若引用失败，直接 rejected

**Day 2（约 2-4 小时）：产出审计报告**

4. 统计审计指标：
   - 总命题数
   - citation_verified 比例
   - citation_missing 比例
   - citation_insufficient 比例
   - inference 比例
   - 各类别（consensus/partial/conflict/orphan）分布

5. 编写 `audit-report.md`，结构：
   ```markdown
   # Wave 2 审计报告

   ## 统计概览
   - 总命题数：187
   - 通过引用审计：134（71.7%）
   - 引用缺失：18（9.6%）
   - 引用不足：21（11.2%）
   - 包含推断：14（7.5%）

   ## 类别分布
   - consensus: 78
   - partial: 56
   - conflict: 23
   - orphan: 30

   ## 关键发现
   1. PRD §3.2 的描述被三个 AI 都误解为...（说明 PRD 表述存在风险）
   2. ...
   ```

6. 提交审计签字。审计报告由 QA Owner 签字，记录日期。

### 通过标准

**全部满足**：

- [ ] 每条 `consensus` 或 `partial` 命题都经过人工引用核对
- [ ] citation_missing 比例 < 10%（若 ≥ 10%，触发 PRD 健康度回顾——可能 Wave 0 漏了问题）
- [ ] citation_insufficient 比例 < 15%
- [ ] consensus.yaml 中所有命题都标 `citation_verified`
- [ ] unresolved.yaml 中每条命题都标注了进入访谈池的原因
- [ ] audit-report.md 由 QA Owner 签字

### 打回标准

- 引用核对未真正逐条执行（抽查发现 ≥ 30% 命题的 source_quote 在 PRD 中找不到）
- citation_missing 比例 ≥ 25%（说明 AI 大量编造，可能需要换 AI 或调整 prompt）
- `conflict` 比例 ≥ 30%（说明 PRD 自身矛盾严重，可能需要回到 Wave 0 重新评估）

### 禁止行为

- **跳过单条引用核对**——这是 Wave 2 唯一不可省略的人工动作
- 把"看起来对"的命题直接标 citation_verified 而不打开 PRD 核对
- 人工修改命题的 statement 让它"匹配" PRD 原文
- 把 conflict 命题人工"裁决"为某一方对（必须进访谈池由开发裁决）
- 跨 Wave 操作：在 Wave 2 提前回答 unresolved 命题（这是 Wave 3 的工作）

### 常见坏样例

**坏样例 1：审计草率**

QA 看到 statement 是"用户登录失败 5 次后锁定 30 分钟"，觉得"嗯这个挺合理"，直接标 verified。但实际打开 PRD，那里写的是"用户连续登录失败 5 次后锁定 60 分钟"——锁定时长不一致。

这种错误一旦放过，后续测试就会按错误的 30 分钟生成，且很难在 Wave 4 行为 oracle 之前被发现。

**坏样例 2：把不一致归到"小差异"**

PRD 说"金额不超过 10000 元"，AI 命题写"金额不超过 1 万元"。QA 觉得"这是一回事"，标 verified。

这种"小差异"在测试生成时不是问题，但**它训练了一个坏习惯**——一旦容忍这种差异，后续 PRD 说"60 秒"AI 写"1 分钟"，再后续"95%"和"接近所有"也都会被放行。**审计纪律一旦松动就回不去**。

**坏样例 3：直接裁决 conflict**

AI-A 和 AI-B 对同一规则有不同提取，AI-A 说"未支付订单 30 分钟自动取消"，AI-B 说"未支付订单 24 小时自动取消"。

QA 觉得"30 分钟更合理"，把 AI-A 的版本标 verified，AI-B 的标 rejected。

**错误**：这恰恰是 Wave 3 访谈应该问开发的问题。QA 没有领域知识不应该裁决——必须把这种 conflict 放进 unresolved.yaml 让开发回答。

**坏样例 4：unresolved 没有标注原因**

```yaml
- id: P-042
  statement: "..."
  unresolved: true   # ❌ 没说为什么 unresolved
```

正确做法：

```yaml
- id: P-042
  statement: "..."
  unresolved:
    reason: conflict
    conflicting_versions:
      - source: ai-a
        statement: "30 分钟"
      - source: ai-b
        statement: "24 小时"
    priority: high
    suggested_question: Q-015
```

### 完成后检查清单

- [ ] consensus.yaml 已生成，所有命题 citation_verified
- [ ] unresolved.yaml 已生成，每条标注 reason、priority、suggested_question
- [ ] rejected.yaml 已生成，每条标注打回原因（citation_missing / citation_insufficient / inference 等）
- [ ] audit-report.md 包含统计概览、类别分布、关键发现
- [ ] audit-report.md 由 QA Owner 签字
- [ ] 各项统计指标在通过标准范围内

---

## Wave 3：结构化访谈 + 答案回灌

> *预计耗时：1-2 个工作日（含访谈安排时间） | 核心人工动作：现场访谈*

### 目标

通过结构化封闭性问卷访谈业务方/开发，把 Wave 2 的 unresolved 命题与 Wave 0 的 PRD 缺口转化为有据答案，形成最终的 validated-model。

### 输入

- `.ai/audit/unresolved.yaml`（Wave 2 产物）
- `.ai/wave-0/interview-pool-seed.yaml`（Wave 0 产物，如 Wave 0 等级为 B）
- PRD 文档（被访者可能需要查阅）

### 输出

- `.ai/interviews/Q-{batch}-questionnaire.yaml` —— 访谈问卷
- `.ai/interviews/Q-{batch}-record.yaml` —— 访谈记录（逐字）
- `.ai/validated-model/propositions.yaml` —— 最终命题库
- `.ai/validated-model/unresolved.yaml` —— 经访谈仍未决的"已知未知"

### 负责人

- 主：QA Owner（生成问卷、执行访谈、回灌答案）
- 辅：业务方/开发（被访者）

### 操作步骤

**Day 1 上午（约 2-3 小时）：生成问卷**

1. 调用 `business-model-builder` agent，输入 unresolved.yaml + interview-pool-seed.yaml，要求生成访谈问卷：

   ```
   你是 business-model-builder。请基于输入的 unresolved 命题与 PRD 缺口，
   生成结构化访谈问卷。严格遵守：

   1. 每题必须是 multiple_choice 或 yes_no——不允许开放式问题
   2. 每题必须有 "以上都不是，正确答案是：____" 的兜底选项
   3. 每题必须标注 triggered_by（哪些命题/缺口触发了它）
   4. 每题必须标注 context_ref（PRD 段落，方便被访者定位）
   5. 每题预计答题时间 ≤ 60 秒
   6. 单份问卷总时长 ≤ 45 分钟（建议 25-30 题以内）
   ```

2. 校验问卷：
   - 所有题目 schema 合规
   - 每题有 ≥ 2 个选项（含兜底）
   - 总时长估算 ≤ 45 分钟（若超出，分两批访谈）

3. **人工 review 问卷**——这是 Wave 3 第一个关键人工动作：
   - 题目是否清晰、被访者能否理解
   - 选项是否互斥（不能两个选项都"看起来对"）
   - 是否有遗漏（unresolved 中应该问的没问）

   review 不通过的退回 agent 修改，最多 2 轮迭代。

**Day 1 下午或 Day 2：安排访谈**

4. 联系被访者：
   - 提前 1 天发送问卷预读链接（让对方知道讨论范围）
   - 约 30-45 分钟会议
   - 准备好 PRD 文档（被访者可能需要现场查阅 context_ref）

**Day 2 或 Day 3：执行访谈（约 30-45 分钟）**

5. **执行访谈纪律**——这是 Wave 3 最关键的人工动作：

   - 逐题问，**不要跳题**
   - 被访者选 A/B/C/D 之一，记录到 chosen_option
   - 若被访者选"以上都不是"，记录 free_text（让对方亲自打字或写下，避免你转译失真）
   - 遇到被访者反问"这个为什么这么问"——回答"AI 在这里不确定，所以列入问卷"
   - 遇到被访者长篇展开——温和打断："我们后面有 20 多题，能否先记录您的选择，详细背景我会后再问？"
   - 录音（征得同意）或现场打字，**绝对不要凭印象事后重构**

6. 访谈结束前 5 分钟：
   - 快速过一遍未回答的题（"以上都不是"且 free_text 为空的）
   - 标注每题 answered_by 与 answered_at

**Day 3 上午（约 2 小时）：答案回灌**

7. 把 `Q-{batch}-record.yaml` 交给 business-model-builder，要求基于访谈答案修正命题库：

   - 对每条原 unresolved 命题，根据答案：
     - 选 A/B/C → 把对应版本的命题 promote 到 validated
     - 选 "以上都不是" + free_text → 创建新命题，引用源标为 `interview:Q-{batch}-{question_id}`
   - 修正后的命题必须**重新进入引用回溯审计**（Wave 2 简化版）：
     - 新引用源为 interview record
     - 核对 chosen_option 与 statement 是否对应一致
   - 输出更新后的 `validated-model/propositions.yaml`

8. 检查是否仍有 unresolved：
   - 访谈中被访者答"以上都不是"且 free_text 模糊的题
   - 访谈中被访者表示"不确定，需要进一步查"的题
   - 这些进入 `validated-model/unresolved.yaml`，标注为"已知未知"

9. 判断是否需要第二轮访谈：
   - 若 unresolved 比例 > 30%，安排第二轮访谈（通常找不同的被访者补充视角）
   - 若 unresolved 比例 ≤ 30%，结束 Wave 3，进入 Wave 4 或直接 Wave 5

### 通过标准

- [ ] 访谈问卷由 QA Owner 人工 review 通过
- [ ] 访谈录音或逐字记录已保存
- [ ] 每题都有 chosen_option 或 free_text（不允许空）
- [ ] 每题都有 answered_by 与 answered_at
- [ ] 答案回灌后，validated-model/propositions.yaml 中每条命题都有引用（PRD 或 interview record）
- [ ] unresolved 比例 ≤ 30%（否则触发第二轮访谈）
- [ ] unresolved.yaml 中每条都明确标注为"已知未知"，附带建议下一步动作

### 打回标准

- 访谈问卷包含开放式问题
- 访谈记录由 QA 事后凭印象重构（无录音/逐字记录）
- 答案回灌后存在命题 statement 与访谈答案不一致的情况
- unresolved.yaml 把仍未决的命题标为 verified（撒谎工程）

### 禁止行为

- **设计开放式问题**——"您觉得这个流程应该怎么处理？"必须改成 multiple choice
- **省略兜底选项**——每题必须有"以上都不是"
- **跳过问卷 review**——AI 生成的问卷有 5%-15% 概率包含含糊或诱导性问题
- **凭印象重构访谈答案**——必须有原始记录
- **代被访者改答案**——被访者选 A，QA 觉得 B 更合理，自行改成 B
- **隐藏 unresolved**——经过访谈仍未决的，必须诚实标注，不能为了"显得流程跑完了"硬塞到 validated 里

### 常见坏样例

**坏样例 1：开放式问题**

```yaml
- question_id: Q-005
  question_text: "您能描述一下用户下单后的完整流程吗？"  # ❌ 开放式
  question_type: open
```

被访者会讲 5 分钟，QA 没法捕获要点，回灌时还得靠 AI 再次"理解"——业务噪声被放大。

正确做法：拆成多个封闭题：

```yaml
- question_id: Q-005a
  question_text: "用户下单后，系统是否立即扣库存？"
  question_type: yes_no
- question_id: Q-005b
  question_text: "若 30 分钟内未支付，库存何时释放？"
  question_type: multiple_choice
  options:
    - id: A
      text: "立即释放"
    - id: B
      text: "30 分钟超时时释放"
    - id: C
      text: "用户主动取消时释放"
    - id: D
      text: "以上都不是，正确答案是：____"
      requires_free_text: true
```

**坏样例 2：QA 帮被访者"修正"答案**

被访者选 A（立即释放），但 QA 觉得"立即释放不合理，B 才对"，记录时改成 B。

**这是 Wave 3 最严重的违规**——你的工作是高保真记录，不是裁决。如果你认为答案不合理，正确做法是：

1. 记录被访者的真实答案 A
2. 在 free_text 字段标注 "QA 对此答案存疑，建议第二轮访谈交叉验证"
3. 触发第二轮访谈（找另一个被访者）或回查 PRD

**坏样例 3：访谈记录事后重构**

QA 访谈时只大致记了几个关键点，事后凭印象补全：

```yaml
- question_id: Q-008
  chosen_option: B
  free_text: "记得开发说大概是这个意思"  # ❌
  answered_by: "我（QA）回忆"            # ❌
```

这种记录在后续 Wave 5/6 测试设计时会成为麻烦——发现矛盾时无法回溯。正确做法是访谈时全程录音或逐字打字。

**坏样例 4：隐藏 unresolved**

经过访谈，10 条命题仍未决（被访者选"不确定"或"需要再查"）。QA 为了"显得流程跑完了"，把这 10 条按自己的判断填入 validated。

**这是欺骗性工程**。诚实做法：

```yaml
# validated-model/unresolved.yaml
known_unknowns:
  - proposition_id: P-073
    statement: "..."
    why_unresolved: "Wave 3 访谈中被访者表示需要确认历史决策记录，至今未补充"
    suggested_next_step: "等待开发补充资料，或在 Wave 4 行为 oracle 时观察实际系统行为"
    impact_if_wrong: medium
```

明确标注"已知未知"反而是工程严谨的体现。下游使用方知道这些区域需要额外关注。

### 完成后检查清单

- [ ] `.ai/interviews/Q-{batch}-questionnaire.yaml` 已生成并 review
- [ ] `.ai/interviews/Q-{batch}-record.yaml` 包含每题的 chosen_option/free_text + answered_by/answered_at
- [ ] `.ai/validated-model/propositions.yaml` 已生成
- [ ] 所有 validated 命题都能回溯到 PRD 或 interview record
- [ ] `.ai/validated-model/unresolved.yaml` 中每条标注 why_unresolved 与 suggested_next_step
- [ ] unresolved 比例 ≤ 30%
- [ ] 访谈录音/逐字记录已归档（路径写入 record.yaml）

---

## 完成 Wave 0-3 后

完成 Wave 0-3 意味着你已经产出了一份**高置信业务模型**——这是后续测试设计的真源。在这个时间节点上：

- **可以暂停**：把 validated-model 交给资深 QA 或 Tech Lead 做最后一次 sanity check，再决定是否进入 Wave 4-8
- **可以继续**：进入 Wave 4（行为 oracle 验证，如系统已部署）或直接 Wave 5（结构化测试设计）
- **可以并行**：Wave 4 与 Wave 5 之间没有强依赖，可以同时进行

后续 Wave 的 SOP 在下一个版本提供。

### 自查：你现在能回答这些问题吗？

如果完成 Wave 0-3 后，你能用以下问题校准自己的工作质量：

- [ ] 你能不能指出 PRD 中目前已知最薄弱的 3 个章节（来自 Wave 0 gap-report）？
- [ ] 你能不能指出哪些业务命题来自 PRD、哪些来自访谈？
- [ ] 你能不能列出本项目当前已知的 3-5 个"已知未知"？
- [ ] 你能不能解释为什么某条命题的 confidence 是 medium 而不是 high？

能干净利落回答这四个问题——即使你对业务本身仍然只有粗浅理解——说明你已经掌握了零领域知识 QA 应有的工程姿态。

---

## 附录：常见问题

**Q1：Wave 0 的 spot check 我都不懂业务，怎么核对 AI 评估是否合理？**

A：Spot check 不是核对"业务对不对"，而是核对"AI 评估的逻辑链是否合理"。例如 AI 说"第 4 维度（边界明确性）评 2 分，因为 PRD §3.2 涉及金额但未给上限"——你打开 PRD §3.2，看是否真的涉及金额、是否真的没给上限。这是阅读理解，不是业务判断。

**Q2：开发太忙，不愿意配合访谈怎么办？**

A：这是组织问题，本 SOP 无法单独解决。可尝试：(1) 把问卷设计得更短（< 20 题），降低开发心理成本；(2) 拿着 unresolved.yaml 上升到 Tech Lead——这份文档明确显示"如果不访谈，下游测试质量无法保证"，是有说服力的；(3) 若仍然无果，把 unresolved 全部标为"已知未知"，明确写入风险记录。

**Q3：被访者说"我也不确定"怎么办？**

A：这是有价值的发现，不是失败。记录"被访者不确定"本身就是数据。可以：(1) 询问被访者是否知道谁可能知道，发起第二轮访谈；(2) 标记为"已知未知"，等 Wave 4 行为 oracle 验证；(3) 写入风险记录，告诉团队"这个区域目前没有人能给出确定答案"。

**Q4：PRD 太长（> 100 页）一次跑 Wave 1 不现实怎么办？**

A：按业务模块分批：每个模块单独跑 Wave 0-3，最后汇总成 validated-model。注意模块间可能有交叉规则，汇总时要做一次跨模块的一致性检查。

**Q5：我执行 Wave 2 引用回溯审计时，发现某条命题的 source_quote 在 PRD 中找不到，是 AI 错了还是 PRD 改过了？**

A：先检查 PRD 是否有版本变更。若 PRD 改过、AI 用的是旧版——重跑 Wave 1 用最新 PRD。若 PRD 没改、AI 编造引用——把这条命题标 rejected，并要求重新生成（多次重复出现的，说明这个 agent 配置有问题，需要调整 prompt）。

---

*文档版本：v1.0 | 覆盖范围：Wave 0-3 | 配套：METHOD.md v1.1*
