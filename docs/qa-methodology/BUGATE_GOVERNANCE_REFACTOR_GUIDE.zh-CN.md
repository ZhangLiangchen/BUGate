# BUGate 治理内核改造指导

[English](BUGATE_GOVERNANCE_REFACTOR_GUIDE.md) | 简体中文

- 计划修订号：`2026-09-18.1`。
- 状态：已接受的 ADR-BUGATE-006 的实施指导；**本文中的工作包尚不因文档落库而完成**。
- 核对基线：[`e12d065`](https://github.com/ZhangLiangchen/BUGate/commit/e12d065615717c9c78f61be7e224787b00d9d604)。
- 架构依据：[治理与运行时边界 ADR](BUGATE_RUNTIME_BOUNDARY_ADR.zh-CN.md)。
- 配套指南：[HyperTest 改造指导](https://github.com/ZhangLiangchen/hypertest/blob/main/docs/governance-runtime-refactor-guide.md)。

## 1. 方向与范围

让 BUGate 更容易被调用、更难被绕过，但不把它改成 Agent 或 Workflow Framework。
保留方法论、证据要求、Policy、授权、Lineage 与审计；把通用调度和执行循环交给
HyperTest 等宿主。**BUGate 不引入 LangGraph。**

第一项交付应是稳定治理契约与行为基线测试，而不是重写 `role_governance.py`、
另建数据库或删除 `sdtd_orchestrator.py`。Skills、CLI、Hooks 和无 Agent 的 CI
继续是受支持的使用方式。

本文把 ADR 转成实施步骤，不默默改写 CHARTER、冻结的角色协议、Imported Updater
所有权或自愈策略。这些契约如需改变，必须有单独评审的协议修订与迁移证据。
下文提出的 operation/schema 名称是设计术语，**不是当前版本已经存在的命令**。

## 2. 当前实现与改造目标

| 当前源码 | 已核实职责 | 改造方向 |
|---|---|---|
| [`scripts/sdtd_orchestrator.py`](../../scripts/sdtd_orchestrator.py) | `init`、`status`、`auto_precode`、`auto_postrun`、self-heal 路由；含角色预检和 peer 降级检测 | 保留原语；`--auto` 暂留为兼容组合；有等价宿主后再抽离调度 |
| [`scripts/role_governance.py`](../../scripts/role_governance.py) | 角色策略、生命周期校验、快照、Receipt 验证/发布、strict Memory 和恢复 | 渐进拆开纯 Policy、Receipt/Audit、CLI；治理发布事务恢复继续保留 |
| [`scripts/role_lineage.py`](../../scripts/role_lineage.py) | SQLite registry、短事务、head CAS、初始化/发布 journal、checkpoint 验证 | 复用并补测试；这些不是可被 LangGraph 替换的通用工作流状态 |
| `scripts/check_bugate*.py`、`check_role_evidence.py`、`check_agent_role_paths.py` | 语义和写入准入守卫 | 不改变判断、UC 绑定、原因语义和 fail-closed 行为 |
| Multiview/Adversarial bridges、报告生成器 | 混合了 worker 调用、转换与验收 | 宿主管 worker 生命周期；BUGate 保留 schema、验收和确定性转换 |
| Skills、templates、`bin/bugate-role` | 方法入口和当前治理发布入口 | 保留入口；共享同一套原语，不复制策略实现 |

现有 registry **已经有 CAS 和 journal**，不能把它们写成缺失能力。
真正需要补的是跨宿主边界的“每动作授权消费与执行结果核对”，而不是重建已有的
角色收据发布机制。

## 3. 不可破坏的不变量

1. Core 保持 SUT-neutral、stdlib-only。真实 SUT 数据留在导入方测试仓；测试只用临时、中性的 fixture。
2. 保留 pre-code gate、P/O 追踪、角色隔离、人工验收、证据时效与修复安全。换 Runtime 不能降低质量门槛。
3. 现有 `00_role_evidence/` 收据及其哈希保持字节稳定，不能向冻结的生命周期事件集中直接塞入 action-consumption 事件。
4. 首次初始化、adoption、recovery 保持显式操作；Engine 更新不得顺手初始化、迁移或重置治理历史。
5. 严格区分 required Memory 与 best-effort。Required 模式不可用时不能拿本地记录或 Graph checkpoint 替代。现有普通 hook/status 继续本地验证，不增加逐次写入的 Memory 网络依赖。
6. “获准”“授权已占用”“外部动作完成”是不同事实；外部结果未知时必须保持未知。
7. Hash chain 不能认证一个人、证明证据内容属实，也不能防住控制全部本地锚点的同权限操作者。

## 4. 目标模块边界

先建立逻辑接口，再考虑文件搬迁；不为目录整齐而重写。

| 边界 | 负责 | 不负责 |
|---|---|---|
| Policy evaluator | Evidence/role 要求、准入、类型化 obligations、reason codes | Agent 规划、进程生命周期、Workflow retry |
| Governance audit service/library | Receipt 验证、发布、幂等、权威动作状态、Lineage、恢复 | 通用任务队列、测试调度 |
| Host operation API | 版本化输入输出、显式 capability 协商 | 隐藏的 `--auto`、宿主私有策略分叉 |
| CLI/hook adapters | 规范化输入输出并调用共享原语 | 独立授权捷径 |
| Compatibility orchestrator | 已有确定性便捷组合 | 新 durable checkpoint、worker 集群、自主修复引擎 |

先在现有 import 后抽函数，再按需要抽小型 policy/receipt/operation helper。
调整 package layout 前检查 vendoring、archive manifest、stdlib 扫描器与 CLI imports。
不要求为了这张表立即拆成多个独立发布包。

## 5. 两仓共享接缝契约（设计基线）

BUGate 拥有治理语义及权威 schema；HyperTest 拥有客户端映射和 conformance tests。
BG-1 将在 BUGate 发布正式 schema fixture 与兼容矩阵；在此之前，本节是计划契约，
不是生产 wire protocol。两仓引用同一个修订号与本节，不各自维护一份分叉的定义。

### 5.1 操作族

| 拟议操作族 | 含义 | 重放分类 |
|---|---|---|
| Inspect/verify | 检查 artifact、role、receipt | 只读，不暗中初始化 |
| Initialize/generate | 显式创建或确定性转换 | 有写入，必须做 role/precondition 检查，不能覆盖已验收证据 |
| Decide | 评估绑定请求，按需持久化签发 decision | 签发收据就不再是纯只读；按稳定请求身份去重 |
| Reserve action | 占用符合条件的一次性动作授权 | 事务 CAS，使用稳定 operation 身份 |
| Record/reconcile outcome | 保存或核对同一 operation 的执行结果 | 幂等；冲突结果阻塞，不能相互覆盖 |

首先采用 process/JSON，接续 HyperTest 已有 process bridge；认证服务作为后续可选宿主。
Stdout 只放机器结果，stderr 放诊断。合法 `deny`/`needs_human` 是成功的决策响应，
不是可以自动重试的 transport error。未知 schema、authority 不可用必须阻断受保护动作。
旧 CLI 通过适配保留退出码，不能原地改语义。

### 5.2 绑定与兼容

Action request 绑定稳定的 `requestId`、`operationId`、动作、资源范围、source/workspace
revision、patch/effect digest、evidence refs/hashes、policy/profile 版本、role/session
上下文和相关 governance anchor。**身份在 dispatch 前生成并持久化，同一逻辑动作
重试不能重新生成身份。**

Decision 增加 verdict、receipt ID/hash、authority provenance、reason codes、类型化
obligations、有效/撤销规则，以及一次性动作授权的 nonce。Outcome 绑定 grant、operation、
结果 hash、目标 ID、当前保障等级可验证的执行者身份和明确结果分类。

必须区分：

- 生命周期验收 Receipt 是可重复引用的历史证据，**不是全部改成一次性 token**。Action grant 引用这些证据，只授权一个明确副作用。
- Dispatch 前重新确认当前 Evidence 和 Policy，但不能换 UUID 构造新请求后拿旧 Receipt 验证。
- 新动作账本采用锚定主 role lineage 的版本化 sidecar，沿用已有 self-heal sidecar 的兼容思路；具体格式单独评审，不能改冻结主链或占用 self-heal 命名空间。
- 明确哪些 role-head/revocation 变化会使 grant 失效；不能因为其自身 reservation/outcome 追加导致 action log head 变更，就误判该 grant 失效。
- 保留 HyperTest gate v1 legacy adapter。增强模式显式协商版本/能力，不假装 v1 或 static decision 已有消费、签名或强身份保障。
- 定义 Python/TypeScript 跨语言规范字节与 golden vectors：Unicode、数字范围、key 排序、missing/null、数组/集合排序、路径。不能默认两种 JSON serializer 的 hash 一致。
- 未识别 obligation 必须阻断。`authority` 字符串不等于签发者认证；本地进程信任与托管认证信任分开声明，不在 stdlib Core 自研密码学。

### 5.3 副作用恢复，不承诺凭空的 exactly-once

CAS 只保证其事务域里一个占用者获胜，不会让文件 patch、SCM API、SQLite/Memory
变成同一笔原子提交。拟议动作状态区分 `authorized`、`reserved`、`dispatched`、
`succeeded`、`failed_no_effect`、`reconciliation_required`。

| 故障窗口 | 必须行为 |
|---|---|
| 持久化 reservation 前 | 不发生受保护动作；重新验证后可用同一请求重试 |
| Reserved 后、dispatch 前 | 恢复同一 operation，校验 lease 与前置条件后再 dispatch |
| 目标已接受动作，但响应或 outcome 丢失 | 用稳定目标幂等键或可查询 operation/result ID 核对；不盲目重发 |
| 动作完成且 outcome 已持久化，但 graph checkpoint 丢失 | 返回既有已验证结果，不重复副作用 |
| 目标不支持去重，也无法确认结果 | 保持 `reconciliation_required`，交人工处理 |

PEP 可以有执行 journal/outbox 保障投递，但 grant 消费真源仍是 BUGate；outbox 不能
成为另一份可独立修改的授权库。先记 intent 再 dispatch，用 fencing 拒绝旧 worker，
幂等投递结果。Lease 过期不证明旧 worker 没执行过。若允许 compensation，它也是
另一笔受授权操作，不是删除历史。

## 6. BUGate 工作包与先后顺序

以下均为待实施；ID 用于 commit/PR 和验收追踪，不是已有 PR 编号。附上证据后才能完成。

| ID | 改动 | 前置依赖 | 完成证据与回退 |
|---|---|---|---|
| BG-0 | 建立 `--init`、status、`--auto`、peer 降级、角色转换、Lineage 故障恢复、imported 布局的行为基线 | 当前代码 | Golden output/receipt 与失败矩阵通过；不改行为 |
| BG-1 | 抽共享原语，发布版本化 process contract、schema、规范化 vectors、capability 矩阵 | BG-0 | CLI/API 策略结果等价；未知版本阻断；旧入口保留 |
| BG-2 | 在现有公共 API 后分离纯 role/policy 和审计编排 | BG-1 | Role bytes、CAS/recovery 回归不变；不大规模重命名、不迁移状态；实现级抽取可回退 |
| BG-3 | 新增显式 opt-in action-grant sidecar、reserve/outcome/reconcile API 和兼容检查 | BG-1、BG-2，与 HT-2 联合设计 | 重复、并发、漂移、重放、崩溃窗口、旧 reader 测试；停用新准入时保留未决操作 |
| BG-4 | Hooks/CLI/HyperTest adapter 统一调用同一 evaluator | BG-3、HT-2 | 对声明的保障等级做绕过负向测试；保留本地模式；关闭 brokered 模式不能重新放行 pending action |
| BG-5 | 收缩 `--auto` 兼容层，只淘汰已被证明替代的编排 | BG-4、HT-4、已公布兼容窗口 | 命令/选项/能力等价清单、迁移指南、standalone 验收、可安全回退；需要时保留薄 wrapper |

BG-0 与 HT-0 可独立推进；之后 BG-1 与 HT-1 可围绕共同 fixture 并行。
无需等 LangGraph 安装后才开始 BUGate 清理；原语抽取本来就服务所有宿主。

## 7. `sdtd_orchestrator.py` 的逐项去向

| 当前职责 | 去向 | 兼容要求 |
|---|---|---|
| `init`、`status`、Full-SDTD scaffold | BUGate artifact 原语 | 保持 required/advisory/off 差异和 no-overwrite |
| `_role_preflight`、acceptance freeze、UC/role 检查 | 每个有写入入口都调用的共享 Policy | 单独调用原语不能跳过原来 `--auto` 提供的守卫 |
| `readable_cases_stale`、语义检查、生成器 | BUGate validation/transformation | 保留 source-hash 漂移与损坏产物检查 |
| `peer_review_degraded`、review acceptance | BUGate review-result validation | Placeholder/partial dispatch 不能因 exit code 为 0 被当成评审成功 |
| `run_script`、peer launch、`auto_precode`/`auto_postrun` 顺序执行 | HyperTest operations/workflow；旧 wrapper 暂留 | 验收需要的偏序不变；不重写已接受的 pre-code Evidence |
| Self-heal 路由 | BUGate sidecar commands + 宿主循环 | 显式启用、独立 reviewer、原 lifecycle anchor 不变 |
| 面向人的 lifecycle 输出 | CLI adapter | Lifecycle、workflow、self-heal、action 状态不能混用 |

不能把 `self_heal_gate.py` 整体搬走：安全与验收仍属 BUGate。不能仅凭文件名含
`orchestrator`、`journal`、`checkpoint` 或 `recovery` 就判定可删除。

## 8. 验收测试与性能证据

每个变更的 enforcement surface 至少覆盖：allow、deny、unavailable、malformed/unknown
schema、过期 Evidence、错误 UC/profile/role/session、过期 grant、未知 obligation、
重复/并发 reservation、缺失/篡改历史，以及 §5.3 全部崩溃窗口。`needs_human` 本身
不得解锁。覆盖无 SUT/core、imported、vendored/plugin、CI 布局。

保留现有 Lineage/Orchestrator 测试、自愈 golden fixtures 和 release/updater 验收。
完整命令以当前 `.github/workflows/ci.yml` 为准，聚焦回归示例：

```bash
python3 tests/test_role_governance.py
python3 tests/test_role_governance_lineage.py
python3 tests/test_role_lineage_registry.py
python3 tests/test_orchestrator_role_governance.py
python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
python3 scripts/check_no_sut_terms.py
```

优化前在固定 fixture 记录 decision/verification 的 p50/p95、hash 字节数、锁持有时长、
网络调用数。只缓存不可变且可验证的 Evidence，不跨 source/policy drift 缓存可变 allow。
可以优化已验证的 projection，不可为提速删除审计真源。

## 9. 第一批工作、后续事项与完成清单

**下一批：BG-0 → BG-1。** 盘点公共命令和守卫位置、固化 fixture、定义最小输入输出，
再让第二种入口调用完全相同的校验；与 HT-1 对齐 vectors。不要先做签名、WORM、
远程 Policy Service 或另一套 registry。

托管凭证、密码学 attestation、独立留存，在威胁模型证明必要后再做。本地受控进程
恢复成功，不等于已经实现恶意 worker 隔离。

- [ ] CLI/skill/CI 现有契约与 golden receipt 不变。
- [ ] Policy、Audit、Compatibility composition 有明确所有者。
- [ ] HyperTest 不经 `--auto` 就能调用离散原语。
- [ ] 直接调用原语也会经过原本的守卫。
- [ ] 新 action grant 明确区分授权、占用、结果。
- [ ] 模糊外部结果核对或停止，不做无根据的 exactly-once 承诺。
- [ ] 说明升级、降级、active operation 处理和 standalone 支持。
- [ ] 两仓指向同一契约版本并提供兼容性证据。

完成标准是证据齐全，不是出现新目录或 Graph。每次交付记录 baseline/target SHA、
工作包 ID、schema/capability 版本、测试、未决风险和下一个依赖。
