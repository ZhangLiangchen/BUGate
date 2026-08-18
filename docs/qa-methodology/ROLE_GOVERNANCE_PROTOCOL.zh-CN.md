[English](ROLE_GOVERNANCE_PROTOCOL.md) | [简体中文](ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)

# Wave 7 可审计角色治理协议

状态：BUGate v0.4.0 冻结契约，并包含截至 v0.4.5 的规范性修正案。本协议保持
SUT-neutral，是实现、hooks、测试、导入器与 release 验收的规范性
依据。

## 1. 范围与角色词汇

Wave 1 与 Wave 7 解决不同的独立性问题：

- Wave 1 在同一设计阶段派发相互独立的 Codex/Claude peer，暴露理解分歧。
  peer 是只读分析 worker，不是生命周期角色。
- Wave 7 把生命周期职责隔离到 `designer`、`implementer`、`reviewer` 的独立
  会话，并记录每次状态转换。

`role_governance.phases` 只接受这三个生命周期 token。`codex`、`claude` 等
runtime 名只能进入 receipt runtime metadata，不能充当角色。现有 `agent_roles`
是独立的路径访问策略，继续兼容 legacy/SUT 自定义角色以及 bare-list、read、
write 三种形式。
冻结的 v0.4.x 状态机采用 canonical phase ownership，而不是可编程角色交换：
pre-code 固定为 `designer`，implementation 固定为 `implementer`，post-run 固定为
`reviewer`。在 `required` 模式中，交换或组合这些 owner 的 profile 属于非法配置，
必须 fail-closed；`advisory` 只报告该配置且不宣称解锁。

## 2. 配置契约

Core 默认保持 inert：

```yaml
role_governance:
  mode: off
```

imported SUT profile 可显式启用完整契约：

```yaml
role_governance:
  mode: required
  memory_mode: required
  evidence_dir: 00_role_evidence
  session_id_required: true
  require_distinct_sessions: true
  human_acceptance_artifacts:
    - 03b_adversarial_cases.yaml
  phases:
    pre_code:
      allowed_roles: [designer]
    implementation:
      allowed_roles: [implementer]
      requires_handoff_from: [designer]
    post_run:
      allowed_roles: [reviewer]
      requires_handoff_from: [implementer]
```

模式语义：

- `off`：保持 v0.3.x 行为，不执行角色状态门禁。
- `advisory`：评估并报告违规，但不阻塞普通写入，也不宣称解锁。为了避免 advisory
  证据链可被伪造，直接编辑 evidence chain 仍被禁止。
- `required`：配置非法、角色或 session 缺失/错误、receipt 缺失/无效以及任何
  drift 都 fail-closed。

`memory_mode` 只能是 `best_effort` 或 `required`。required 的角色转换使用 strict
Memory。Best-effort transition 仍可能尝试同一 transition anchor，但会容忍服务不可用
或 finalize 失败，并且不创建 lineage root/checkpoint；普通 recall、note 与 Stop
heartbeat 继续 best-effort。

配置文件按 nested mapping 解析。确定性合并规则为：mapping 递归合并，profile
scalar 覆盖 base scalar，profile list 整体替换 base list。`parse_simple_yaml()`
继续只服务 legacy frontmatter/简单工件。每份配置在合并前把旧顶层 `namespace`
规范化为 `memory.namespace`，因此旧 profile 能覆盖 base 的 nested 值；最终结果
同时暴露新旧访问形式。同一文档若同时声明冲突的新旧值，以新的 nested 形式为准，
再镜像到 legacy alias。

required 模式拒绝：超出支持子集的 YAML、非法类型/enum/boolean、绝对或逃逸的
evidence 目录、未知或缺失 phase、非法生命周期 token、空角色集、错误 handoff
关系、缺失显式 profile 以及所有非法受治理 regex；错误必须清晰。

## 3. 状态机

append-only 事件与状态如下：

| 顺序 | 事件 | 角色/session | 前置条件 | 结果状态 |
|---|---|---|---|---|
| 1 | `human_acceptance` | designer 会话仅记录已经发生的人工决定 | pre-code 全部 passed；配置要求的 03B 已是 `passed` | `ready_for_designer_handoff` |
| 2 | `designer_handoff` | designer | 人工接受有效，pre-code/provenance 当前有效，strict Memory 锚定 | `awaiting_implementer_acceptance` |
| 3 | `implementer_acceptance` | 不同 session 的 implementer | 精确验证 handoff ID/metadata；acceptance Memory 锚点已复核 | `implementation_unlocked` |
| 4 | `implementer_handoff` | implementer | 至少一个实现文件；均在 workspace 内、命中 guarded path 且绑定同一 UC | `awaiting_reviewer_acceptance` |
| 5 | `reviewer_acceptance` | 不同 session 的 reviewer | implementer handoff 精确验证；实现 snapshot 未漂移 | `post_run_active` |
| 6 | `reviewer_completion` | reviewer | 记录并验证 04/05、命令摘要、exit code、log/evidence hash 与最终 gate | `closed` |

approve 命令仅为已经由人类设成 `passed` 的 03B 记录声明性 `approved_by`；它不
修改 03B，也不是身份认证。同角色自接单被拒绝；启用配置后同 session 接单也被
拒绝。成功重试必须幂等。drift 恢复通过追加 superseding generation 完成，禁止
删除 evidence 来 reset。§9 的 v0.4.3 修正案让该不变量在删除和发布中断后可检测。

## 4. 本地证据与 hash

每个 UC 的 workspace-local evidence 只使用
`<artifact-dir>/00_role_evidence/`：

```text
00_role_evidence/
├── chain.json
└── receipts/000001-<event>-<hash>.json
```

Receipt append-only。`chain.json` 只保存 schema version、当前 state/sequence、
chain head hash，以及各逻辑事件最新 receipt 的路径。路径统一为 workspace-relative
POSIX，snapshot 按 path 排序。JSON hash 使用 UTF-8、sorted keys、紧凑 separators；
计算 receipt 时排除 `receipt_sha256`。每条 receipt 链接前一条 receipt 与稳定的
transition hash。

Designer handoff 捕获 active profile、全部 required pre-code 工件、存在时的正式
`00_multiview` 输出、03B dispatch provenance 与当前 human-acceptance receipt。
Implementer handoff 增加实现文件 hash；reviewer completion 增加 04/05、执行日志
与 evidence。成功 completion 是 terminal：status、verify 与 post-run preflight
必须持续本地复验其 profile、04/05 和执行 evidence snapshot。在 `required` 模式
进入 `closed` 后，受支持 tool 的写入必须阻塞；`advisory` 仍只警告。有意修改受治理
内容必须建立新的 handoff/acceptance lifecycle generation。

Receipt/chain 发布使用同目录临时文件、flush、`fsync` 与 `os.replace`。不得落盘
secret 或 Memory credential。每次受治理编辑只在本地复核 receipt 内容/hash、链
链接/head、profile hash、pre-code hash/gate status 与实现文件 hash；禁止每次编辑
访问 Memory Service。

新 receipt 同时绑定两类配置来源：`profile.path` 和 `profile.sha256` 标识被选中的
profile 文件，`profile.effective_config_sha256` 则对转换时真正生效的 canonical
base+profile 合并 mapping 做摘要。因此，即使 profile 文件字节未变，继承自 base
config 的策略发生变化也会重新上锁。Validator 仍能解析 v0.4.0/v0.4.1 的旧两字段
profile snapshot，避免 append-only chain 无法恢复；但旧 snapshot 不能继续解锁，
必须追加 superseding human-acceptance/handoff generation。治理为 `off` 时，
`approve`、`handoff`、`accept`、`complete` 等 lifecycle publisher 会拒绝，而不是
生成看似有效但实际 inert 的 receipt。

Reviewer completion 只接受专用 execution evidence；base config、selected profile、
任一已配置 role-evidence 目录，以及 pre-code、implementation、post-run phase-owned
路径都不能复用为 evidence。Hook 以 canonical resolved workspace path 绑定任意捕获
日志，因此 `..` 与 symlink alias 不能绕过 terminal snapshot。同一路径若被多个 UC
捕获，写入必须同时通过所有 owner 的 post-run preflight；不得按排序选择第一个 UC。

## 5. Strict Memory 转换协议

required 转换严格按以下顺序执行：

1. 构造稳定 transition payload 与 `transition_sha256`。
2. POST Memory transition，并要求有效 content hash。
3. exact GET 该 hash，验证 namespace、角色、UC、phase、transition 与被引用
   handoff metadata。
4. 带 Memory ID 构造完整本地 receipt，并计算 receipt hash。
5. PUT receipt hash 到 Memory metadata。
6. 再次 exact GET 并验证完整锚点。
7. 最后才原子发布本地 receipt 与 chain head。

Acceptance 必须先 exact GET 并验证传入的 handoff ID，再写入并 exact GET 自己的
acceptance。service unavailable、timeout、HTTP/write failure、exact ID 不存在或
任一字段不一致均返回非零，且不发布本地解锁 receipt、不推进 chain。稳定 transition
内容与本地 latest-event 检查保证 retry 幂等。高基数 ID/hash 放 metadata，不放 tags。

## 6. 强制执行面

所有 Core artifact mutator 在创建目录、复制模板、派发 peer 或写输出之前调用共享
Python preflight；通用 Core writer 再做一次目标路径分类 backstop。Role evidence
使用私有原子 writer，不提供可由环境变量打开的内部 bypass。

required 模式下，pre-code init 只创建 pre-code 与已选择的 optional modeling 工件；
legacy/off init 保持 v0.3.x 一次创建 01–05 的行为。04/05 归 reviewer 所有。03B 一旦
有 human-acceptance receipt，`--auto` 不得重新生成；handoff 只重跑 semantic 与
provenance 校验。

Hooks 保持两种独立职责：`check_bugate.py` 验证 pre-code passed，
`check_role_evidence.py` 验证角色与 receipt chain。Claude 对写门使用 `Edit|Write`，
对 `agent_roles` 使用 `Read|Edit|Write`；Codex 在 `apply_patch` 上执行四个 guard。
agent tool 直接编辑 `00_role_evidence/**` 一律拒绝。SessionStart 做 best-effort Memory
recall 并打印角色治理状态；Stop 继续按小时 best-effort heartbeat，agent 优先取当前
role，否则为 `agent`。

Peer bridge 子进程必须清除生命周期 role/session/receipt 身份，同时保留 profile/
project root、proxy、model 与 reasoning effort 配置。

## 7. 兼容、恢复与安全边界

以下为冻结的 v0.4.0 行为，自 v0.4.2 起已由 §8 取代。不含 `role_governance` 的
profile 与 v0.3.x 行为一致。启用 `required` 不会给历史
passed UC 自动补证据：必须创建当前 human acceptance、handoff、acceptance chain。
profile/pre-code drift 从 designer acceptance/handoff 重启；implementation drift 从
implementer handoff/reviewer acceptance 重启。rerun importer 会刷新 vendored scripts
与 BUGate-owned hook，同时保留 SUT-owned hook；Codex hook 变化后必须重新信任。

本协议提供角色声明、session 区分、hash 链接、外部 Memory 锚点、篡改/drift 检测
与可审计状态转换，但不提供不可抵赖的人类身份。环境变量、hook 与本地文件不能证明
真实操作者。强身份隔离需要独立 OS 账号、容器、managed runner 或按角色发放的
服务端凭据。Hook 也无法拦截任意 shell 重定向或外部编辑器；支持的 agent tool、
orchestrator 与 Core mutator 会被强制治理，更强的文件系统隔离属于 managed runner。

## 8. 修正案——imported updater 边界（2026-07-22）

§7 中“重跑 importer 刷新已有安装”的句子作为冻结的 v0.4.0 历史记录保留，但自
v0.4.2 及后续兼容 release 起已被取代。`bugate_init.py` 只用于首次安装。精确匹配的
v0.3.x 或 pre-lock v0.4.x 安装从解压 release bootstrap；已有 updater 的安装使用
vendored `status` → `plan` → `apply` → `verify`，并只按明确 transaction ID 回滚。
详见 [Imported-mode 更新器契约](IMPORTED_UPDATER_CONTRACT.zh-CN.md) 与 vendored
`bugate-import/references/updating-bugate.zh-CN.md` 操作手册。

更新器可以替换支持 role governance 的 engine/hook 文件，但绝不激活 governance、
编辑 profile/Memory/role evidence 或制造 lifecycle receipt。Engine update 与 profile
migration 必须分别审查、分别提交、分别可回滚。只有 Codex hook bytes 实际变化时才
要求 Codex Desktop re-trust；任一 hook 变化都要求新开 agent session，完成前不得声称
新的 enforcement surface 已激活。

## 9. 修正案——持久 role-evidence lineage（v0.4.3）

本节是 v0.4.3 lineage 的规范性契约。Tag、CI、asset 与 publication 状态属于 release
operation evidence，不由本源码文档提前声称。Durable defect 记录见
[`BUGATE-CORE-2026-07-23-ROLE-EVIDENCE-RESET`](../defects/BUGATE-CORE-2026-07-23-ROLE-EVIDENCE-RESET.md)。

### 9.1 确定性 identity 与独立 authority

Workspace evidence 缺失不能证明 UC 是新的。因此，每个受治理 UC 都有一份确定性
lineage key：

```json
{
  "schema": "bugate.role-lineage-key/v1",
  "namespace": "<effective-memory-namespace>",
  "uc": "<resolved-uc-token>",
  "artifact_dir": "<canonical-workspace-relative-posix-path>"
}
```

`lineage_id = sha256(canonical_json(lineage_key))`；canonical JSON 使用 UTF-8、
sorted key、紧凑 separator 且无结尾换行。输入按原值参与计算，不做大小写或空白折叠。
UC token 遵循已有 profile/template/artifact-directory 解析契约；`artifact_dir` 是
canonical workspace-relative POSIX path。Absolute workspace path、OS identity、
timestamp、credential 与 Memory token 都不属于 identity input。

Machine-level SQLite registry 固定名为 `role-lineage.sqlite3`，位于 effective Memory
home：优先 `MCP_MEMORY_BASE_DIR`，其次 `BUGATE_MEMORY_HOME`，最后
`~/.bugate/memory-bus`。它在 governed workspace 之外，也不属于 imported updater 的
installed projection。Read/status/hook path 不会创建它；只有显式 lineage init/adoption
可以创建并校验 registry。Registry 记录已接受的 lifecycle state、sequence、head、
revision、Memory mode、strict root/checkpoint ID 与唯一 active transaction。v0.4.3
registry schema version 为 2：它还持有 durable initialization journal，对
initialization/publication/recovery 强制 exact next-stage graph，并绑定 content-addressed
root/checkpoint ID，不能把 routing label 当成 proof。

### 9.2 Integrity states

History integrity 与 lifecycle state 分开报告：

| `integrity_state` | 含义与必须采用的路径 |
|---|---|
| `uninitialized` | 没有匹配 registry row，也没有本地历史。这只是“可能首次使用”；operator 必须确认后才能执行 `lineage-init`。 |
| `aligned` | Registry 与已验证本地 chain 在 identity、head、sequence、lifecycle state、Memory mode 上一致，且无 active transaction。普通 lifecycle publication 只允许该状态。 |
| `migration_required` | 存在本地历史但没有 registry row，或显式 strict-root probe 证明 registry/local history 缺失时仍存在旧 lineage。Adoption 会另外要求并验证非空有效 legacy chain；否则必须经审查 restore，禁止覆盖式 init。 |
| `history_missing` | Registry row 存在，但本地 chain 或一个/多个 receipts 缺失。 |
| `history_diverged` | 本地 evidence 非法或与 registered head/sequence/state 不一致，或配置的 Memory mode 不同于已 adoption lineage。 |
| `recovery_pending` | Registry 保留一笔未完成的 initialization、publication 或 recovery journal。`active_initialization` 必须通过 exact `lineage-init` 继续；active publication/recovery transaction 才使用 `recover`。 |
| `registry_unavailable` | 已存在 registry 不安全、locked/unreadable、schema 非法，lineage context 无法解析或验证失败；显式 strict-root probe 失败也使用该状态。单纯 registry 缺失会映射为 `uninitialized` 或 `migration_required`。 |

`implementation_unlocked`、`post_run_active`、`closed` 等 lifecycle state 保持原义。
Integrity failure 不会变成新的 lifecycle state，也不会授权 phase 回滚或 reset。

普通 `status`、hooks 与 per-edit preflight 有意保持 local-only，Memory HTTP 请求数为零；
它们通过 registry 与本地 chain 检测已注册历史被删除。显式 integrity field 应查看
`status --json` 或 `lineage-status --json`；默认 human status line 重点显示 lifecycle
state。`lineage-status` 是显式 operator
命令：只有 required Memory 且本地状态为 `uninitialized` 时，才 exact-GET 确定性
lineage root。若 registry/local history 缺失但 root 已存在，则非零返回
`migration_required`；probe 不可用或非法时非零返回 `registry_unavailable`。
`lineage-init` 在写入前执行同一 probe，并拒绝已存在 root。

### 9.3 显式 initialization 与 legacy adoption

`scripts/bugate_init.py` 继续只负责 fresh **engine installation**；
`bugate-role lineage-init` 是独立的 per-UC 首次使用决定。

对真实新 UC：

```sh
bin/bugate-role lineage-status <artifact-dir> --json
bin/bugate-role lineage-init <artifact-dir> --lineage-id <exact-lineage-id>
```

第一个命令只读；首次 init 前返回非零 `uninitialized` 属于预期。Operator 必须复制并
确认 exact computed ID。Initialization 要求本地无历史且 ID 精确。它本身也是
journaled、crash-recoverable saga：BUGate 在任何 Memory request 前先持久化 exact
initialization intent，再按
`pending` -> `root_absence_verified` -> `root_verified` ->
`registry_initialized` -> `chain_written` -> `completed` 推进。Required Memory 先证明
root 不存在，再创建并 exact-verify deterministic root，并在提交 sequence-zero 空
registry row 前绑定其 exact ID。Best-effort 走同一 journal，但只绑定明确的
no-remote-root 边界，不创建 root 或 checkpoint。Local empty `chain.json` 以 no-replace
语义、`0600` mode 发布，并在完成前 exact-verify bytes 与 mode。

用相同 exact ID 重跑 `lineage-init` 会从持久 stage 继续同一 intent，不会创建第二个
intent，也不会把已完成 stage 当成新首次使用再执行。Intent 建立后的任一中断都报告
`recovery_pending`；intent 完成前，普通 lifecycle publisher 一律阻断。唯一的 terminal
例外是在初始 `pending` probe 发现 strict root：BUGate 会 abort 这笔尚未创建
root/lineage 的 intent，并报告 `migration_required`，因为该 root 证明存在 prior
history。
`status --json` 与 `lineage-status --json` 会暴露 `active_initialization` 的 ID 与 stage，
供 operator 区分该路径和 publication recovery。

没有 registry row 的有效非空 v0.4.0-v0.4.2 chain 报告
`migration_required`，并使用：

```sh
bin/bugate-role lineage-adopt <artifact-dir> \
  --lineage-id <exact-lineage-id> --expected-head <exact-chain-head>
```

Adoption 重新校验完整 chain 与 exact expected head，不重写任何 receipt byte。
Required Memory 在 registry adoption 最终 head 前，为每个 retained sequence 创建并
exact-verify 确定性 root 和 immutable checkpoint；best-effort adoption 只记录已验证
local head，不声称存在 remote recovery copy。

如果 pre-v0.4.3 history 在 registry 或确定性 root 建立前就已遗失，空目录无法说明
历史是否曾存在。BUGate 不得推断或制造它；应恢复可信 pre-loss evidence，或保持
migration 阻断并披露缺口。

### 9.4 Transactional publication 与 recovery

每个普通 publisher（`approve`、`handoff`、`accept`、`complete`）都要求
`aligned`，并执行一套持久顺序：

1. 在完整 transition 期间获取 physical per-UC `flock`；
2. 按 exact current head、sequence、revision 与 prior checkpoint 创建唯一 active
   registry transaction，并在任何 Memory request 前绑定 canonical transition；
3. required 模式下，exact-GET deterministic root 与当前 committed predecessor
   checkpoint，再对照 registry 保留的 canonical payload、head、sequence、lifecycle
   state 与 revision 完整校验；
4. 按 `memory_mode` 准备 transition Memory record；
5. 根据 prepared public binding 构造 receipt，用 receipt hash finalize Memory
   transition；required 模式还必须 exact-verify 最终 transition/receipt binding；
6. 只有 Memory prepare 加 finalize/exact verification 成功后，才冻结 final receipt，
   并在 registry 中单次 journal 其 exact bytes/path/mode/hash，随后构造 exact minimal-chain
   bytes；
7. required 模式下，POST 并 exact-GET immutable checkpoint；checkpoint 携带 exact
   receipt/minimal-chain byte envelope、mode、hash、previous checkpoint、resulting state
   与 next registry revision；
8. 对 registry head 执行 compare-and-swap；
9. 以 no-replace 语义发布 append-only receipt，原子替换 `chain.json`，最后才把
   transaction 标为 completed。

Predecessor proof 位于 durable pending journal 之后、new transition prepare 之前；因此
验证失败可恢复，也不能在未验证 strict head 上写新 transition。New checkpoint 则必须
位于 strict transition finalize 与 exact registry receipt-byte bind 之后；它既不能
替代，也不能早于这两个步骤。

Sole-active-transaction constraint 会在 Memory 操作前串行化竞争者，registry CAS 则是
最终 cross-workspace head authority：同一 lineage 的两份 workspace copy 不能从同一个
head 同时发布。任一 journaled stage crash 或 handled failure 都保持
`recovery_pending`，绝不会被重新解释为空历史。这是跨 SQLite、Memory 与 workspace
files 的 journaled、crash-recoverable saga，不是一次 distributed atomic commit；后续
local abort 后 remote transition/checkpoint 仍可能存在，由记录的 transaction 协调。

已注册的 `history_missing`、`history_diverged`，以及带 active publication/recovery
transaction 的 `recovery_pending` 使用：

```sh
bin/bugate-role recover <artifact-dir> \
  --lineage-id <exact-lineage-id> --expected-head <exact-head-or-EMPTY> \
  [--archive <trusted-recovery-archive>]
```

`active_initialization` 不走本命令；必须重跑 exact `lineage-init`，由它自己的 journal
继续。对于 `recover`，exact ID 与 registry head 都是必填；`EMPTY` 表示 sequence-zero
的空 head。Recovery 在 target write 前校验完整 source、path、hash、mode、link、
receipt order、chain state 与所有现有 target；通过后才允许新建 journal 或写 target。
随后它选定 active source transaction，或针对未变化的 registered head 创建 pending
`recovery_restore` source，并在恢复 exact committed predecessor 与旧 receipt/chain
bytes 前 claim 这笔 exact source。一个 live claimant 独占 recovery；dead claimant
只有经过 process-liveness 校验与 exact-token registry CAS 才可 takeover。如果 source
是已经具备 exact receipt 与经过验证、可进入 CAS checkpoint 的原 lifecycle
publication，recovery 会让**同一笔** transaction 继续完成 registry CAS 与 exact local
receipt/chain publication，不会伪造重复的 next-sequence lifecycle receipt。

Local restoration 精确完成，且任何 resumed lifecycle source 已到
`chain_replaced` 后，registry 会在**同一个 SQLite transaction**中 terminalize 已 claim
的 source（`recovery_restore` 变为 `aborted`，lifecycle source 变为 `completed`），并按
resulting head、sequence、revision、checkpoint 与 lifecycle state 安装唯一
canonical-bound、pending 的 `evidence_recovery` successor；中间不存在没有 pending
audit record 的 `aligned` 状态。
随后 recovery 发布并完成这一条保持 state 的 receipt。若 active source 本身已经是
`evidence_recovery`（包括 handoff 后 crash），exact `recover` 会直接继续它，绝不再
安装 successor。旧 receipt bytes 永不重写。Best-effort journal 中 durable empty
transition ID 是明确的 unanchored marker，recovery 会保留它且不重试 Memory HTTP。

`memory_mode: required` 默认从 strict Memory 沿 exact immutable checkpoint chain
恢复。显式提供 trusted archive 只用于选择候选 bytes；retained strict checkpoint
仍是必须存在的权威，每个 archive envelope 都必须在任何写入前与其精确一致。
该 archive 不是 strict Memory 不可用或分歧时的离线回退。`best_effort` 没有
strict checkpoint；若 committed local predecessor 缺失、分歧或无法 exact-verify，
必须通过 `--archive` 提供独立保留、可信的
`bugate.role-recovery-archive/v1`。Best-effort 唯一无需 archive 的路径，是 active
pre-CAS publication 的 exact committed predecessor 仍能在本地完成验证；此时 recovery
继续原 durable transaction，而不是重建已删除历史。两种模式下 local registry 都能
检测删除；只有 required 提供 remote reconstruction source。

### 9.5 Updater 与威胁边界

Updater 可以安装具备 lineage 能力的 engine files，但绝不运行 `lineage-init`、
`lineage-adopt` 或 `recover`，不创建/编辑 machine registry，也不编辑 profile、
namespace、role evidence 或 Memory home。Updater transaction/verify 成功只证明
installed engine projection，不代表接受任何 per-UC lineage migration。已有 UC 必须
随后逐个分类，并显式 adoption 或 recovery。

Hook 无法拦截任意 shell redirection、递归删除或 external-editor write。Registry 加
strict Memory 让 deletion/drift 可检测，但不认证 actor，也不提供不可抵赖身份。
`approved_by`、role/session env、local file permission、registry row 与 Memory record
仍只是 audit control。强身份与 filesystem 隔离仍需要独立 OS 账号、容器、managed
runner、受保护备份或 role-scoped server credential。

拥有同一 OS-user 权限的 actor 如果同时删除 workspace evidence、machine registry 与
整个 Memory home，就能移除全部本地锚点。这种组合破坏超出 BUGate 的本地威胁边界，
不得声称本修正案能够检测或阻止。

## 10. 修正案——自愈 sidecar 契约（v0.4.5）

本修正案新增一条受治理的写入通道。第 1–9 节任何条款均未改动：`PHASES` 仍是三元组，
六个 `EVENT_STATES` 键值不变，`_phase_for_handoff` 仍只允许 `(1, 2)` 两条边，
post_run 的 phase ownership 仍规范地归 `reviewer`。宪章锚点：CHARTER.md §7 A6。
决策记录：`BUGATE_SELF_HEALING_SIDECAR_ADR.md`（ADR-BUGATE-005）。

### 10.1 为什么证据落在 sidecar

第 1–9 节的两条不变量使主链无法承载修复流程，本修正案一条都不放松。

`DEFAULT_PHASES["post_run"]["allowed_roles"]` 恰为 `["reviewer"]`，`_actor` 对不在表内
的角色直接拒绝，因此以 `implementer` 身份行动的 healer 在 post_run 永远不可能是合法
actor。`_validate_event_receipt_contract` 对 `EVENT_STATES` 之外的事件是**硬 raise 而非
降级**，且 `preflight` 在每次受治理写入时重校验整条链；一旦某 UC 的主链写入了自愈
事件，v0.4.0–v0.4.4 的任何引擎读该 UC 都会整链失败并阻断其后所有受治理写入，与 §8 的
rollback-to-prior-image 保证正面冲突。

因此自愈证据存放在 `<artifact_dir>/00_self_healing/`，**不向 `00_role_evidence/` 写入
任何字节**。早于本修正案的引擎只会看到一个它不认识的目录，行为完全不变；无论
sidecar 是否存在，`preflight` 与 `verify_evidence` 的返回逐字段相同。

### 10.2 布局与 schema

```
<artifact_dir>/00_self_healing/
├── chain.json                   # bugate.self-heal-chain/v1
├── 000-triage_recorded.json     # bugate.self-heal-evidence/v1
├── 001-self_heal_handoff.json
├── ...
└── attempts/<attempt_id>/       # baseline.json、candidate.patch、
                                 # before.log、after.log、verification.json、
                                 # structural_review.json、falsification.json、
                                 # review_context.json、independent_review.json、
                                 # apply_journal.json
```

Receipt 的 hash 采用与主链一致的 canonical 形式
（`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`），
落盘为 `indent=2` 加结尾换行，计算自身 hash 时排除 `receipt_sha256`，并通过同一套
no-replace 原子写入器发布。

### 10.3 锚定与生命周期漂移

每份 receipt 携带只读的 `role_chain_anchor`：

```json
"role_chain_anchor": {"chain_sha256": "...", "sequence": 3, "lifecycle_state": "post_run_active"}
```

完整校验主链后，`chain_sha256` 取该链已认证的 `head_sha256`；`sequence` 与
`lifecycle_state` 绑定另外两个 anchor 值。语义损坏由完整校验拒绝，只有 JSON
序列化格式变化时则不产生 drift。`lifecycle_state` 必须恰为 `post_run_active`；
`closed` 之后不得开新 attempt。发布前若 anchor 已不匹配，当前 attempt 立即置为
`attempt_invalidated_by_lifecycle_drift`（退出码 4），必须重新 triage。

### 10.4 事件、状态与 actor

| # | event | actor | 前置状态 | 结果状态 |
|---|---|---|---|---|
| 0 | `triage_recorded` | `reviewer` | *(无 / 终态)* | `triage_recorded` |
| 1 | `self_heal_handoff` | `reviewer`（#0 的会话） | `triage_recorded` 且 `healing_eligible` | `awaiting_healer_acceptance` |
| 2 | `healer_acceptance` | `implementer`（**新会话**） | `awaiting_healer_acceptance` | `healing_active` |
| 3 | `healer_handoff` | `implementer`（#2 的会话） | `healing_active` | `awaiting_independent_review` |
| 4 | `independent_review` | `reviewer`（**新会话**，≠ #0 且 ≠ #2） | `awaiting_independent_review` | `healing_verified` \| `healing_rejected` |
| 5 | `attempt_closed` | `reviewer` | `healing_verified` \| `healing_rejected` | `attempt_closed` |

这些状态 token 只存在于 sidecar，不写入主链，也不写入 lineage registry；
`_STATE_LABELS` 刻意不扩展——sidecar 用自己的状态行
`BUGate self-heal status: <STATUS>` 报告，与 `BUGate lifecycle status: <STATE>` 分离。

`_self_heal_actor` 按上表校验，而**不查** `ctx.policy["phases"]`，因为 post_run 已冻结
为 `reviewer` 而 healer 一侧需要 `implementer`。它保留 `_actor` 的环境变量契约、会话
要求与 runtime 归一化。同会话自批准将以 `same_session_self_approval` 拒绝。

### 10.5 唯一被省略的检查及其边界

`verify_evidence(phase="post_run")` 会调用 `_verify_acceptance_session`，把每次 post_run
写入绑定到发布 `reviewer_acceptance` 的**那一个**会话。该规则与「#4 必须是新的
reviewer 会话」不可兼得。因此 `self_heal_preflight` 逐项对齐 `verify_evidence`——
lineage 对齐、链有效、reviewer acceptance 存在、链处于 `post_run_active`、无 closed
completion、已受理的 implementer handoff 且 profile/artifact 快照未漂移——**仅**省略
`_verify_acceptance_session`，并以 §10.4 更强的**会话互异**约束替代。

该省略**只适用于 sidecar receipt**。后置产物写入（`self_healing.json`、04/05）仍走
`governed_write_preflight`，仍要求受理 reviewer 会话。

`status` 与 `resume` 另外整体跳过 `self_heal_preflight`。修复写回会改写 implementer
handoff 快照内的文件，那本就是真实的 artifact drift；若仍要求旧快照保持 current，
精确 restore 会在刚发生待撤销写入之后立即变得不可达。

`status` 通常只报告已校验的 sidecar 状态；若发现 role-chain anchor drift，它会把
invalidation metadata 持久化到 sidecar `chain.json`，但不追加 event receipt。
`resume` 可以 restore workspace 的精确旧字节并更新受认证的 apply journal，同样不追加
event receipt。两者仍执行 sidecar 结构与完整性校验，都不是不受约束的写入入口。

### 10.6 Strict Memory

在 `memory_mode: required` 下，事件 #1、#2、#4 各写一条 Memory transition 并做 exact-ID
回验，按与 `_publish` 相同的顺序复用 `_memory_prepare`、`_memory_finalize` 与
`_memory_verify`。它们**不**调用 `_publish`，不创建 registry transaction，也不发布
checkpoint。Memory 失败意味着该 sidecar 事件不发布、attempt 停在原状态，主链不受影响。

### 10.7 兼容性与「写回即重锁」

从未设置 `self_healing` 的仓库行为与 v0.4.4 完全一致：`--scope self-heal` 返回
`disabled` 且零创建，`--scope post-run` 产物逐字节相同（由 v0.4.4 引擎实测生成的
golden fixture 钉死）。

启用该键会改变 `profile.effective_config_sha256`，因此对已发布 receipt 的 UC 追加该键
会重锁其链。应在生命周期开始前设置，或另起一个 generation。

修复写回会改写快照内的实现文件，该 UC 按既有 drift 规则重锁。这是**期望结果**：
变更后的测试资产必须经正常生命周期重新受理与评审，而不是由「改它的那次修复」放行。

### 10.8 威胁边界

Sidecar 原样继承 §7 与 §9.5，不新增任何身份保证。`check_role_evidence.py` 把
`00_self_healing/**` 识别为独立的受保护 evidence 类，无条件拒绝直接的 agent 编辑，
与 `00_role_evidence/**` 同级；另一张结构归属表只记录其 post-run standing，不会给
reviewer 打开写入路径。但拥有同一 OS-user 权限的 actor 仍可绕过 hook 直接改动该目录。
Anchor 让主链漂移可检测，但不认证 healer 或 reviewer 的身份。

### 10.9 加固修正案（2026-08-12）

本日期修正案是 v0.4.5 源码候选的规范性条款，不改变 §10.4 的任何事件或状态 token。

1. **Attempt 边界。** 不可修复的归因与 `diagnose` 模式仍写普通受治理 triage 报告，
   但不发布 sidecar receipt，也不开 attempt。只有 `verify` 或
   `apply_with_approval` 中可修复的失败才以事件 #0 开始。
2. **Proposal 顺序。** 结构 gate、sandbox 修复前/后验证、强制 falsification 与
   repaired-test 反向验证均在 `propose` 内、事件 #3（`healer_handoff`）之前完成。
   Falsification 缺失或无结论时阻塞，分数低于阈值时拒绝。对每个绑定到 triage
   oracle 的精确 clean falsification case，修复后的命令必须从 pristine PASS 转为在
   映射的受治理断言处产生 `AssertionError`，恢复输入后再回到 PASS。测试仍然通过时
   以 `repaired_test_survives_mutation` 拒绝；崩溃、runner 可见输入不安全/缺失、
   oracle 无关或归因歧义时以 `repaired_test_reverse_verification_unavailable` 阻塞。
   这种拒绝不改变任何 attempt artifact；所有这些失败都不能进入独立评审。
   动态 clean failure 是必要条件，但不能授权任意 Python；候选还必须满足第 4 项的
   闭合证明语言边界。
3. **独立评审的精确绑定。** 成功的 proposal 写入 `review_context.json`；事件 #3
   同时绑定其 SHA-256 与完整 binding 对象。其精确字段为 `uc`、`attempt_id`、
   `candidate_manifest_sha256`、`candidate_patch_sha256`、
   `precode_evidence_sha256`（已验收 business brief 与 inventory 的 canonical
   manifest）、`original_failure_sha256`、`verification_sha256`、
   `before_log_sha256`、`after_log_sha256`、`falsification_sha256` 与
   `oracle_refs`。`falsification.json` 还嵌入 live spec 与每个精确 evidence 输入的
   workspace-relative path 和 SHA-256 身份；事件 #4 与 `close` 都重新计算该 input
   manifest，任一不符即拒绝。
   评审文档必须使用 `dispatch_mode: real_peer_dispatch`，声明与当前 reviewer 会话一致
   的 `codex`/`claude` runtime，逐字段复现 binding，包含非空的
   `{claim, evidence}` findings 列表，并包含仅由非空字符串组成的 residual-risk 列表
   （该列表本身可为空）。输入文档的精确字节归档为 `independent_review.json`；其
   SHA-256 与 binding 由事件 #4 携带。
4. **反向验证边界。** 普通路径只变异 profile 声明、位于 workspace 内且为非 symlink
   regular JSON file 的 evidence。没有精确、runner 可见的 clean counterexample 时，
   proposal fail closed。有限且候选可见的 mutant 不能证明任意 Python 诚实，因此自动
   授权只限两个“单一候选文件”的闭合语言。严格 NameError/literal 语言只允许在完整
   AST 中插入一个 local immutable scalar 并替换已记录的 unresolved name。JSON
   evidence 语言只允许精确的 `json`/`Path` import、唯一 binding、从精确声明的
   path/key 直接读取一个 built-in scalar、继承自修复前的 immutable scalar
   observation、直接等值比较、零参数 `test_*` 函数及其直接调用，以及保持不变的辅助
   primitive-equality assertion；函数不得带 annotation，assertion 不得带 message
   expression。移除精确修复 delta 后必须重建原始 AST。源码必须是 coherent UTF-8，
   encoding detection 后的精确 byte AST 必须与 decoded AST 一致。Workspace 或已配置
   import shadow 会阻塞，且 pristine/mutant/restored 都用 `python -I -S` 再执行一遍。
   两个语言都变异 observation scalar；custom comparator、helper、descriptor、
   decorator、dynamic import、rebinding、annotation、assertion message、alternate source
   encoding、dispatch、control flow 或额外候选
   文件都不在语法内并被阻塞。该边界只证明可证伪性与上述 scalar binding，不证明完整
   业务 provenance 或所有未声明状态。复杂但诚实的修复在另行治理 observation contract
   之前仍会被阻塞；非确定性、host compromise 与 OS 级 sandbox escape 是显式
   residual risk。
5. **冻结批准项。** `independent_review_required` 与
   `human_approval_required` 均冻结为 `true`；任一设为 `false` 都是配置错误。
   `apply_with_approval` 还要求在 `close` 提供非空的人工批准记录。
6. **Sandbox 与 apply 完整性。** 复制前，verification 拒绝被复制 surface 上的全部
   symbolic link；candidate 与写入 target 都以不跟随 link 的方式重校验。Apply journal
   记录精确的 before/after 字节与 POSIX permission bits。当 sidecar 仍处于
   `healing_verified` 时，`resume` 可从 `applying` 或 `applied` 两种 journal 状态恢复，
   还原旧字节与 permission bits，删除 journal 创建的文件，并且只删除为空的
   journal-created parent directory。新 regular file 以 `0600` 写入。Owner、xattr 与
   ACL 不在该还原保证内。
7. **Receipt/index crash cut 与存储边界。** Receipt 先于 chain-index replacement
   发布。下一次读取时，只有恰好一份 root-level 未引用 receipt、且它是唯一可能的 next
   receipt，才会进入候选；完整 prospective replay 必须验证 schema、sequence、状态边、
   parent/hash、actor/session、当前 role-chain anchor 与 Memory envelope。在
   `memory_mode: required` 下，冻结集合中的事件 #1、#2、#4 还必须经只读 exact Memory
   回验绑定 transition identity 与 receipt hash，之后才可入索引。该 reconciliation 不做
   Memory mutation，不写主 role-evidence tree，也不改 receipt。所有未锚定事件与全部
   `best_effort` orphan 的本地 hash 都不足以证明真实性；完整本地 replay 通过后，精确
   bytes 移至
   `attempts/<attempt_id>/unindexed_<event>_<content-sha256>.json`，root-level orphan
   被移除，chain 保持此前已索引状态，同一事件可以重新走普通发布控制。按内容寻址的
   文件作为 evidence 保留，永不成为 chain entry。Malformed、anchor stale、状态边
   非法、伪造 Memory id 或 exact-Memory 不匹配的 receipt 均报 integrity failure，不会
   被受理。每个已存在的 project、artifact、sidecar、`attempts` 与 attempt-directory
   component 都用 `lstat` 检查，必须是 project/artifact 边界内的真实目录且不得经过
   symbolic link；受治理 leaf 必须是非 symlink regular file。即使 `status` 跳过主链
   preflight，`00_role_evidence/chain.json` anchor reader 也执行相同的 parent/leaf 校验；
   每次 verified sidecar load 还会盘点全部直接 root entry，未知 file、directory、special
   node 或 symbolic link 一律 integrity failure，不会被忽略；attempt directory 以下每个
   directory 与 leaf 也递归执行相同 `lstat` 规则。每个 indexed receipt anchor 的 state
   必须是 `post_run_active`；相邻 anchor 必须完全相同，除非新 triage 明确 supersede 已索引
   drift record。Malformed main-anchor sequence/state 不得被归一化成可持久化 drift record。

### 10.10 并发与已评审写回绑定修正案（2026-08-12）

本日期修正案保留 §10.9，并补充最终并发评审要求的线性化与还原边界。

1. **每个 UC 共用一个协作临界区。** 每次主 role transition，以及完整的 sidecar
   verified read、publication、invalidation 与 status snapshot，都会在同一个既有 UC
   artifact-directory inode 上取得排他的 advisory `flock`。两套 evidence layout 都不
   新增 lock file。一个完整 self-heal CLI step 从 sidecar-state validation、全部 attempt
   evidence 或 workspace write，一直到最终 receipt publication 都持有同一把锁；所有嵌套
   操作复用同一个 re-entrant store。因此，并发 proposal、review、close 或 resume 的失败方
   不能在另一个合作步骤提交后再改动字节。Sidecar publication 从 Memory prepare/verify、receipt 持久化一直持锁
   到 chain-index replacement；verified read 在 orphan reconciliation 或 quarantine
   全程持锁。因此，另一个遵守 BUGate 协议的进程不会把 live publisher 的
   receipt/chain 间隙误判为 crash orphan。进程死亡会释放 descriptor，之后下一次读取
   才可应用 §10.9 的 authenticated crash-orphan 规则。
2. **Head-anchor compare-and-swap 与单次 status 快照。** Drift invalidation 把此前读取的
   latest receipt anchor 当作 compare-and-swap token：它在持有 artifact-directory lock
   时重新载入已校验的 sidecar head 与当前 main-chain anchor，拒绝把旧 drift observation
   附着到已经 retry 或 re-triage 的新 head。CLI `status` 通过一次加锁的
   `status_snapshot` 同时取得 state、attempt id、review outcome 与 main-chain 对比结果；
   只有 live main-chain anchor 通过 schema 校验时，新发现的 drift 才会在返回前持久化。
   主链缺失时，只读 empty sentinel 会让每次 status 都返回 `invalidated` 与
   `lifecycle_drift`，但绝不会写入 sidecar `chain.json`；malformed/unreadable anchor
   同样不会生成 drift record。Status 不会把多个独立时点的读取拼成一个结果。
3. **已评审 candidate 包含还原拓扑。** 已评审 candidate manifest 以密码学方式绑定精确
   `baseline.json` bytes、由 baseline 认证的原始 before image、candidate after image、
   重建出的 human-readable patch，以及 apply 前不存在的 parent directory canonical
   集合。无论 verify-only close 还是 workspace apply，`close` 都会先重算该 manifest，
   并重新校验完整 live review context、归档 review bytes、review receipt binding、
   runtime/verdict、falsification result 与 triage evidence。
4. **Apply journal v2 是内部且精确的。** 内部
   `bugate.self-heal-apply-journal/v2` 绑定已评审 candidate manifest、精确 file set、
   before/after bytes、POSIX permission bits，以及必须与已评审 pre-apply
   missing-directory 集合完全相等的 `created_directories` 集合。这些目录逐个创建，且
   要求当时不存在同名 entry。`resume` 时，任何意外 file、directory 或 symlink 都会在
   全部 restore write 前阻塞；只删除引擎创建的新文件，并且只删除完成该步骤后为空、
   且经认证为原先不存在的目录。预先存在的 parent 永远不进入可删除集合。
5. **Metadata 边界与 advisory-lock 限制。** 还原承诺只覆盖精确 file bytes 与普通
   POSIX permission bits（`0o777`）。Special mode bits 会在 apply 前 fail-closed；
   ownership、xattr 与 ACL 不会被捕获或还原。`flock` 是协作式并发控制，不是 OS 安全
   边界：同一 OS account 的 actor 可以忽略它，并在 BUGate 之外修改或替换路径。
   §10.8 的身份与同账户边界保持不变。

### 10.11 Replay 与 CLI 完整性修正案（2026-08-12）

本日期修正案保留 §§10.9–10.10，并补充最终完整性评审要求的 payload、主 anchor 与
错误边界规则。

1. **每个 receipt payload 都必须是可 replay 的 canonical JSON object。** Publication
   与整链 replay 都要求 `payload` 是 object，其完整 value graph 能由 BUGate canonical
   JSON 编码，并能在当前 interpreter 下重新解析。Key 必须是 string；value 只能使用
   JSON type；float 必须有限；cyclic 与 implementation-specific value 均非法。非 object
   或不可 replay 的 payload 以 `self_heal_event_payload_invalid` 失败，引擎不会强制转换。
2. **携带状态含义的 payload claim 必须绑定 authenticated edge。**
   `triage_recorded` payload 的 `healing_eligible` 必须精确为 boolean `true`，否则以
   `triage_healing_eligible_not_true` 失败。`independent_review` payload 的 `outcome`
   必须等于该 receipt 的 `resulting_state`，否则以
   `review_outcome_state_mismatch` 失败。`attempt_closed` payload 的 `final_state`
   必须是 `healing_verified` 或 `healing_rejected`，并且等于 authenticated prior
   state，否则以 `close_final_state_mismatch` 失败。这些规则同时适用于 prospective
   validation 与 replay。
3. **被拒绝的 triage 零写入。** 在 `verify` 与 `apply_with_approval` 中，maximum-attempt
   与完整 publication preflight 先于普通 triage report、attempt evidence 或 receipt 的
   任何写入。State、payload、actor、session、attempt 或 anchor 被拒时，不得改写已经拥有
   当前状态的 attempt evidence。
4. **主 anchor 不能只做 shape read 就被信任。** Anchor reader 先执行 role-evidence
   `lstat` 边界，要求任何已存在的主 `chain.json` 是非 symlink regular file，再校验精确的
   最小五字段
   v1 envelope 及其 schema、sequence、state、head、latest-receipt index，最后执行完整
   `verify_chain` receipt inventory、hash、state edge 与 history 校验。只有通过完整校验的
   值才能把已认证 `head_sha256` 作为 `chain_sha256`，并连同 `sequence`、
   `lifecycle_state` 用于 live/persisted `role_chain_anchor`。
5. **所有失败都留在冻结的 result 边界内。** 每个 result object 只有 `status`、
   `exit_code`、`blocking_reasons`、`artifact_paths` 与 `next_action`；任何 sidecar 或
   anchor exception 都不得逃逸成 traceback/exit 1。可安全读取但 malformed 的主 envelope
   或主链 replay 失败，映射为 exit 4、`lifecycle_drift` 的 `invalidated`；sidecar、unsafe
   path 与 payload contract violation 保留精确 blocking reason，并映射为 exit 2 的
   `blocked`；普通 evidence I/O/decoding failure 也映射成五字段 blocked result。
   `status` 先校验 sidecar，再读取主 anchor；两套 surface 同时损坏时，应报告 sidecar
   corruption，而不是让 lifecycle drift 把它遮蔽。
