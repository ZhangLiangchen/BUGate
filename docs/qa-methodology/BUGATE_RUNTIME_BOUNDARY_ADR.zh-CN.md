# ADR-BUGATE-006 — 治理内核与持久化运行时边界

- **状态：** 已接受（2026-09-18）
- **权威来源：** [`CHARTER.md`](../../CHARTER.md) 与 [`ROLE_GOVERNANCE_PROTOCOL.md`](ROLE_GOVERNANCE_PROTOCOL.md) 中的 Wave 7 治理不变量
- **配套决策：** [HyperTest ADR-0005](https://github.com/ZhangLiangchen/hypertest/blob/main/docs/adr/0005-durable-workflow-runtime-and-bugate-boundary.md)
- **语言：** [English](BUGATE_RUNTIME_BOUNDARY_ADR.md) | 简体中文

## 1. 背景

BUGate 的核心价值是与 SUT 无关的质量治理契约：Evidence、Policy、Gate、Authorization、Receipt、Audit 与 Promotion。它可以作为 toolkit 导入 Claude Code、Codex、CI 或其他宿主，原则上不拥有 Agent loop，也不应绑定某一种工作流运行时。

目前部分脚本同时承担了执行编排。尤其是 `scripts/sdtd_orchestrator.py --auto`，会依次调度多视角分析、语义检查、产物生成、对抗评审、运行后报告与可选自愈流程。`role_governance.py`、`role_lineage.py` 也包含崩溃恢复能力，但其中一部分是为了保证治理发布不会伪造或丢失授权历史，不能与普通工作流恢复混为一谈。

HyperTest 正在演进为自主测试开发系统，需要 checkpoint/resume、retry、interrupt、concurrency 与长任务恢复；当前首选实现是 LangGraph。因此必须明确：是让 BUGate 自身 LangGraph 化，还是让 BUGate 退出通用流程编排。

## 2. 决策

BUGate 保持为**与运行时无关的治理内核**。BUGate 不引入 LangGraph 依赖，也不把治理规则改写成 LangGraph 的 nodes/edges。HyperTest 拥有持久化执行，并可在可替换的 `WorkflowRuntime` 边界之后采用 LangGraph。

核心原则是：

> **执行状态不等于授权状态。**

- Workflow checkpoint 回答“执行应从哪里恢复”；
- BUGate receipt 回答“为什么允许执行这个受保护动作”；
- Enforcement attestation 回答“哪个已授权动作针对哪一个精确状态实际执行了什么结果”。

LangGraph 可以依据 BUGate verdict 路由，但不能生成、解释或替代该 verdict。`current_node=implementation` 永远不能证明 `implementation_unlocked=true`。

## 3. Receipt / Evidence Chain 的执行保障

Receipt Chain 应成为治理事务日志，而不是影子工作流引擎。

### 3.1 确定性决策

PDP 使用规范化请求、不可变 Evidence 与版本化 Policy 做确定性判断。Authorization Receipt 至少绑定：receipt/request/idempotency ID、动作及最小资源范围、source revision 与 workspace/patch digest、完整 evidence hashes、policy/profile/schema 版本、当前保障等级可提供的 actor/role/session 声明、verdict/reason/obligations、签发与过期时间、一次性 nonce、序号、前序 receipt hash，以及锚定或签名元数据。

规范序列化和带版本的哈希规则属于强制契约。策略、源码、证据发生漂移，receipt 过期或作用域不匹配时必须 fail closed。

### 3.2 只追加发布

所有治理事件通过唯一的事务发布 API 追加。Append-only log 是事实源，生命周期状态只是可由 replay 重建的 projection。追加时对预期 lineage head 与 idempotency key 做 compare-and-swap，避免并发或重复请求产生两个合法后继。

本地 hash chain 只能提供篡改可见性，不能单独提供身份真实性或持久保存。高保障部署应把 lineage head 锚定到受控工作区之外；现有 strict Memory checkpoint 可作为一种锚点。托管模式还可以增加角色级签名密钥、append-only/WORM 存储或透明日志服务，而无需改变 Policy 语义。

### 3.3 经纪式强制执行

所有受保护副作用必须经过 PEP。推荐的 HyperTest 模式不向 Agent 暴露原始工作区写入或发布凭证；只有 PEP 能应用受治理的 patch 或发布变更。

在副作用发生前，PEP 必须：

1. 从当前状态重建规范化 GateRequest；
2. 校验 receipt、evidence hashes、source/workspace 前置条件、policy version、scope、obligations、expiry、lineage head 与 nonce；
3. 使用 compare-and-swap 原子地预留或消费一次性授权；
4. 执行被精确授权的动作；
5. 追加 enforcement outcome，将 decision receipt 与结果 revision、artifact hashes、状态和失败信息绑定。

Decision receipt 只证明“允许”，不证明“已经执行”。独立的 outcome record 防止系统把 allow 误当成完成。崩溃恢复必须恢复同一笔治理事务，不能重发授权，也不能静默重复非幂等副作用。

### 3.4 保障等级

| 等级 | 强制执行方式 | 能力边界 |
|---|---|---|
| 本地 toolkit | hooks + 本地 receipt/registry 校验 | 便于开发、可审计、在已观测入口 fail closed，但同一 OS 用户仍可绕过 |
| 经纪式自主运行时 | Agent 没有直接的受保护写入/发布能力，所有副作用经过 PEP | 防止普通 Agent 绕过；支持最小权限、一次性授权、原子消费与结果证明 |
| 托管高保障 | 经纪式 PEP + 隔离 runner + 角色级凭证/签名 + 外部锚定的 append-only 历史 | 提供更强身份、不可绕过性、留存与独立审计能力 |

BUGate 必须如实声明当前等级，不能把 hooks 或 hash chain 描述成不可抵赖机制。

## 4. 职责边界

| 关注点 | 归属 |
|---|---|
| 方法、Schema、Evidence 规则、Policy、角色/Session 规则、授权决定 | BUGate Policy Kernel |
| Receipt/Lineage 校验、治理 CAS、审计 projection、治理发布事务恢复 | BUGate Audit Kernel |
| 拦截受保护动作并消费授权 | 宿主专用 BUGate PEP Adapter |
| 规划、工具使用、子 Agent 选择与局部推理 | Agent Harness |
| Checkpoint/resume、调度、重试、中断、并行与通用进程恢复 | 宿主 Workflow Runtime |
| SUT 执行、CI、SCM、Sandbox 与测试框架行为 | 宿主 Adapter |

治理事务的 crash consistency 留在 BUGate，因为它保障授权账本；通用任务 checkpoint 与 worker/process 生命周期不属于 BUGate。

## 5. `sdtd_orchestrator.py` 的归宿

**不在 BUGate 内用 LangGraph 重写 `sdtd_orchestrator.py`。** 现有能力分成三类：

1. **保留为 BUGate primitives：** artifact init/status、语义 Gate、生成器、Policy 判断、Receipt 校验和机器可读结果。这些能力应在没有 Agent 或 Workflow Framework 的 CI 中继续可用。
2. **暂时保留为 compatibility runtime：** 当前 `--auto` 顺序执行路径服务独立使用者；它被冻结为确定性、单进程兼容实现，不再增加持久化运行时能力。
3. **迁移到宿主 Runtime：** peer scheduling、长阶段编排、retry、pause/resume、parallel、post-run sequencing 与 repair-loop control。HyperTest 通过 Workflow Runtime 承担这些职责，并用稳定的进程或服务契约调用 BUGate primitives/PDP。

Self-healing 的**准入 Policy** 留在 BUGate，反复 remediation 的**循环**归 HyperTest。Multiview/Adversarial 的验收规则留在 BUGate，worker dispatch 归宿主。

## 6. 迁移与退出条件

### Phase A — 契约抽取

- 保持当前行为与测试不变；
- 为每个被编排操作提供版本化、机器可读的 request/result、稳定退出语义与 idempotency key；
- 把纯 Policy/Validation 与进程调度、CLI 展示分离；
- 在不削弱现有 Lineage 检查的前提下，引入 decision、consumption 与 outcome receipt schema。

### Phase B — HyperTest 接管

- HyperTest 调用离散 BUGate operation，不调用 `--auto`；
- LangGraph checkpoint 只保存 Receipt 的引用与哈希，不复制可变治理状态；
- 每条受保护 Edge 调用 BUGate PDP，每个受保护副作用经过经纪式 PEP；
- 用 conformance test 证明 restart、retry、duplicate delivery、denial、expiry、drift 与 partial failure 无法绕过或重复授权。

### Phase C — 收缩兼容层

- 将 `--auto` 标为 compatibility-only 并停止扩展；
- `--init`、`status`、validators、generators 与 gate commands 继续是一等 BUGate CLI；
- 只有在参考宿主通过能力等价、故障注入、恢复与迁移测试，独立兼容版本窗口结束，并提供替代与回滚路径之后，才能移除相应编排入口。

因此，移除是有证据支撑的终态，不是立即删除。若独立使用场景长期存在，可以永久保留小型确定性 wrapper，但不能把它继续扩展成第二套持久化 Workflow Platform。

## 7. 影响

- BUGate 继续保持可导入、stdlib-friendly、CI-usable、SUT-neutral 与 runtime-agnostic；
- HyperTest 可以采用 LangGraph，而 BUGate 不依赖 LangGraph；
- 授权、消费与实际执行结果被显式区分，Receipt 的执行保障更强；
- 现有模块需要逐步抽取：Policy/Audit 语义保留，通用进程编排收缩；
- 真正防绕过需要凭证与能力隔离，本地 hooks 只是较低保障模式。

## 8. 被否决的方案

- **用 LangGraph 重写 BUGate：** 会把治理语义绑定到单一 Runtime，并混淆执行与授权状态。
- **用 LangGraph checkpoint 替代 Governance Receipt：** checkpoint 不证明 Policy Authority 或合法来源。
- **立即删除 `sdtd_orchestrator.py`：** 在替代路径证明等价与恢复安全前会破坏独立使用者。
- **继续扩展 BUGate 自研 Orchestrator：** 通用持久化执行属于宿主，会形成两套竞争 Runtime。
