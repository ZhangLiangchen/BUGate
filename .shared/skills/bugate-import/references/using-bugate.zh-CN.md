# 导入之后：BUGate 使用指导

[English](using-bugate.md) | [简体中文](using-bugate.zh-CN.md)

BUGate 已导入 SUT 自动化测试仓后，Claude Code / Codex 的日常
操作手册。（下文 `.bugate` = vendor 目录。）

## 0. 打开正确目录和角色会话

将**包含 `bugate.config.yaml` 的 SUT 测试仓本身**作为项目根打开。
Hook 从当前会话工作区加载；打开父目录时没有物理守卫。首次导入按 installer
验收输出执行；已有安装必须使用 [`updating-bugate.zh-CN.md`](updating-bugate.zh-CN.md)，
禁止重新导入。只有 import/update 实际改变 Codex hook hash 时才 re-trust Codex
Desktop；任一 hook 变化后都必须新开 agent session。在这些 process boundary 完成前，
只能说文件验收通过，不能说新的 runtime 门禁已激活。

启用 `role_governance.mode: required` 时，用三个独立进程/会话：

```bash
.bugate/bin/bugate-role run --role designer -- codex
.bugate/bin/bugate-role run --role implementer -- claude
.bugate/bin/bugate-role run --role reviewer -- codex
```

`run` 会生成新 `BUGATE_SESSION_ID`，并只向子进程设置身份。
SessionStart hook 可报告身份与 chain 状态，但无法把变量 export 回父进程。
Desktop 需从等价角色环境启动并新建会话，不得假设 hook 已激活父进程。

## 1. 新需求的标准工作链

向 agent 给出需求证据和 UC 名，然后按以下顺序执行。

1. 在 **designer 会话**中先脚手架，再撰写 01/02/03：

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> --init
   ```

   `--init` 和 `--auto` 是两个独立操作；`--init --auto` 会被拒绝。
   Required 模式只初始化 pre-code 及已选可选建模工件，不初始化 reviewer
   所有的 04/05。

2. 在发布任何普通 lifecycle event 前先建立 UC lineage：

   ```bash
   .bugate/bin/bugate-role lineage-status docs/usecases/<UC> --json

   # 仅限真实首次使用；从 status 输出复制 exact ID。
   .bugate/bin/bugate-role lineage-init docs/usecases/<UC> \
     --lineage-id <exact-lineage-id>
   ```

   首次 init 前，命令以 exit 2 返回 `integrity_state: uninitialized` 是预期的只读
   结果。必须确认这确实是新 UC；普通 publisher 永远不会代做该决定。若存在非空
   pre-v0.4.3 chain，使用 exact `lineage-adopt`，禁止 init。若已注册 chain 缺失、
   分歧或有未完成 transaction，先恢复。§4 给出 decision table 与精确命令。只有
   `aligned` 后才能继续。

   Initialization 会在任何 Memory request 前持久化 intent，并按
   `pending` -> `root_absence_verified` -> `root_verified` ->
   `registry_initialized` -> `chain_written` -> `completed` 推进。若 JSON 状态显示
   `recovery_pending` + `active_initialization`，用相同 exact ID 重跑
   `lineage-init`；它会继续原 intent，且普通 publisher 仍保持阻断。不要把这种状态
   交给 `recover`。初始 pending probe 若发现 strict root，会关闭尚未创建
   root/lineage 的 intent，并改报 `migration_required`。

3. 仍以 designer 身份跑完整 pre-code 链：

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> --auto
   ```

   它依次跑 Wave 1 独立 peer、Layer 1/2/3、03A 生成、03B 对抗 peer、
   完整契约和降级评审检查。Peer 子进程不继承 designer 生命周期身份。
   步骤在第一个失败处终止；03B 保持 `pending` 等人工审阅。

4. **人工检查点：**真实人类阅读分歧/对抗证据，并显式将 03B 设为
   `gate_status: passed`。Agent 不得代替或冒充该决定。Designer 然后记录
   这个已经发生的决定，并创建 strict Memory handoff：

   ```bash
   .bugate/bin/bugate-role approve docs/usecases/<UC> --approved-by <human-id>
   .bugate/bin/bugate-role handoff docs/usecases/<UC> \
     --phase pre_code --to implementer
   ```

   `approve` 不修改 03B；`approved_by` 是声明性证据，不是身份认证。
   已有该 receipt 后不要重跑 pre-code `--auto`。下一步使用 designer handoff
   receipt 里的精确 Memory `memory_id`。

5. 在**新 implementer 会话**中接单并实现 Layer 4：

   ```bash
   .bugate/bin/bugate-role accept docs/usecases/<UC> \
     --phase implementation --handoff-id <exact-memory-id>
   ```

   `check_bugate.py` 与 `check_role_evidence.py` 必须同时通过。实现完成后，
   handoff 每个具体 guarded 文件（多文件可重复参数）：

   ```bash
   .bugate/bin/bugate-role handoff docs/usecases/<UC> \
     --phase implementation --to reviewer \
     --implementation-file <guarded-test-file>
   ```

6. 在**新 reviewer 会话**中用第二个 exact Memory ID 接单，执行测试并生成
   04/05：

   ```bash
   .bugate/bin/bugate-role accept docs/usecases/<UC> \
     --phase post_run --handoff-id <exact-memory-id>

   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> \
     --auto --scope post-run --pytest-log <run.log> \
     --command "<真实测试命令>" --env <环境> --exit-code <rc>
   ```

   Post-run 会重生成 04/05 草稿；先备份并合并人工历史，诚实裁定结果，
   再以 receipt 完成闭环：

   ```bash
   .bugate/bin/bugate-role complete docs/usecases/<UC> \
     --phase post_run --run-command "<真实测试命令>" \
     --exit-code <rc> --evidence-file <run.log> \
     --gate-status <passed|failed>
   ```

   Passed completion 要求 exit code 0 且 04/05 都是 passed。Failed completion 保持
   `post_run_active`，不会伪造绿色 closed。

## 2. 命令速查

| 意图 | 命令 |
|---|---|
| UC 工件状态 | `python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC>` |
| 角色证据链状态 | `.bugate/bin/bugate-role status docs/usecases/<UC> [--json]` |
| Lineage identity/integrity 状态 | `.bugate/bin/bugate-role lineage-status docs/usecases/<UC> --json` |
| 确认真实首次使用 | `.bugate/bin/bugate-role lineage-init docs/usecases/<UC> --lineage-id <exact-id>` |
| Adoption 已验证 legacy chain | `.bugate/bin/bugate-role lineage-adopt docs/usecases/<UC> --lineage-id <exact-id> --expected-head <exact-head>` |
| 恢复已注册历史 | `.bugate/bin/bugate-role recover docs/usecases/<UC> --lineage-id <exact-id> --expected-head <head-or-EMPTY> [--archive <trusted-archive>]` |
| 本地 receipt 验证 | `.bugate/bin/bugate-role verify docs/usecases/<UC> --phase <phase>` |
| 本地 + strict Memory 验证 | `... verify ... --strict-memory` |
| 一键能力自检 | `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke` |
| 普通 Memory 检回/记录 | `.bugate/bin/memory-recent --agent <role>` / `python3 .bugate/scripts/memory_bus.py note ...` |

Orchestrator 会输出一个生命周期状态：`BLOCKED`、
`READY_FOR_HUMAN_ACCEPTANCE`、`READY_FOR_DESIGNER_HANDOFF`、
`IMPLEMENTATION_UNLOCKED`、`READY_FOR_REVIEWER_HANDOFF`、`POST_RUN_ACTIVE`或
`CLOSED`。它是状态信号，不是跳过下一个命令的授权。

Lineage integrity 是独立维度：`uninitialized`、`aligned`、
`migration_required`、`history_missing`、`history_diverged`、
`recovery_pending` 或 `registry_unavailable`。只有 `aligned` 才能发布普通 lifecycle
event；integrity 结果永远不会倒退 lifecycle。

Peer dispatch 的 `SDTD_CLI_*` 由 profile/repo 持有。Peer 子进程保留 model、
effort、proxy、profile/root 配置，同时清除生命周期 role/session/receipt 身份。

## 3. 永远由人完成

- 接受 03B；CLI 只记录已经发生的决定。
- 裁定 execution/self-healing 结果并签署 04/05。
- 判定缺陷还是预期行为，并对事故闭环负责。
- 门禁拒绝时修证证据或环境，绝不降低门禁。

## 4. 迁移、drift 与边界

- 不含 `role_governance` 的 v0.3.x profile 行为不变。启用 `required` 不会自动
  承认历史 passed UC。先为每个 UC 建立/adopt lineage，再建立当前 human acceptance、
  handoff 与 acceptance receipt。Updater `apply`/`verify` 成功只安装 engine files；
  它永不接受 lineage migration，也不编辑 profile、namespace、
  `00_role_evidence/**`、machine registry 或 Memory home。

| Integrity 结果 | 必须采用的 operator 路径 |
|---|---|
| `uninitialized` | 确认真正首次使用，复制 exact ID，并运行 `lineage-init`。 |
| `aligned` | 继续当前 lifecycle。 |
| `migration_required` 且有 verified 非空 legacy chain | 用 exact chain head 运行 `lineage-adopt`；它重写 0 个 receipt。 |
| `migration_required` 且只剩 existing strict root | 先恢复可信 pre-loss evidence，再 adoption/restoration；禁止覆盖 root 做 init。 |
| `recovery_pending` + `active_initialization` | 用相同 exact ID 重跑 `lineage-init`，继续 initialization journal。 |
| `history_missing`、`history_diverged`，或带 active publication/recovery transaction 的 `recovery_pending` | 用 exact registry head 运行 `recover`；`EMPTY` 只表示 sequence-zero expected head。 |
| `registry_unavailable` | 停止写入，修复 registry 或显式 root probe 故障，再重新分类。 |

```bash
.bugate/bin/bugate-role lineage-adopt docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-chain-head>

.bugate/bin/bugate-role recover docs/usecases/<UC> \
  --lineage-id <exact-lineage-id> --expected-head <exact-head-or-EMPTY> \
  [--archive <trusted-recovery-archive>]
```

- `memory_mode: required` 会 exact-verify deterministic root 与 immutable checkpoint；
  registry 与 Memory history 尚存时可重建缺失的 local evidence。显式提供 trusted
  archive 只用于选择候选 bytes；retained strict checkpoint 仍是必须存在的权威，
  每个 archive envelope 都必须在任何写入前与其精确一致。它不是 strict Memory
  不可用或分歧时的离线回退。`best_effort` 仍用
  registry 检测删除并串行化 publisher，但本地历史丢失后必须通过 `--archive` 提供
  独立保留、可信的 `bugate.role-recovery-archive/v1`。Best-effort lifecycle
  publication 仍可能尝试 transition Memory call，但会容忍失败且不创建
  root/checkpoint。
  Validation/preflight 通过后，Recovery 会先 claim active source，或在 target write 前
  创建并 claim pending `recovery_restore`。随后恢复 committed predecessor，并从原
  transaction 的 durable stage 继续——包括 checkpoint 已验证、可进入 CAS 的阶段。
  Registry 在同一个 SQLite transaction 中 terminalize 该 restore/lifecycle source，
  并安装唯一 pending `evidence_recovery` successor。中间没有 aligned/no-audit crash
  gap；若 active source 已是 `evidence_recovery`，retry 会直接继续它，不会再安装
  successor 或制造重复 receipt。Dead recovery
  claimant 只有通过 liveness check 与 exact-token CAS 才可 takeover。Best-effort 的
  durable unanchored marker 会被保留，不会重试 HTTP。
- 同角色/同 session 自接单、非 exact Memory ID、缺 receipt 或直接编辑
  `00_role_evidence/**` 都会被拒绝。
- Profile/pre-code drift 从 designer acceptance/handoff 重建；implementation drift 从
  implementer handoff/reviewer acceptance 重建。追加 superseding generation，不得删除 evidence reset。
- 普通 `status`、hook 与 per-edit preflight 只查本地 registry、receipt chain 与 hash，
  Memory HTTP 请求数为 0。`lineage-status` 是显式 operator diagnostic，仅在 required
  Memory 且本地状态看似 `uninitialized` 时探测 deterministic root。Required-mode
  init、adoption、recovery 以及 lifecycle transition 也可能是显式 network boundary；
  strict Memory 故障时它们非零结束，且没有完成的本地 unlock publication。若
  initialization intent 已存在，失败会显示为 `recovery_pending`，并由 exact
  `lineage-init` 继续。
- 这些控制提供角色声明、session 区分、hash 链接、registry/Memory 锚定和篡改/drift
  检测，但不提供不可抵赖身份。Hook 不能拦截任意 shell 重定向、递归删除或外部
  editor；更强隔离需要 OS 账号、容器、managed runner、受保护备份或按角色凭据。
  同一 OS-user actor 若同时删除 workspace evidence、machine registry 与完整 Memory
  home，超出本地边界。在 deterministic anchor 建立前丢失的历史无法从空目录推断。

## 5. 更深的文档

- 首次安装/更新选路、transactional upgrade、验证、回滚及 profile/session 边界：
  `updating-bugate.zh-CN.md`。
- 布局/profile 适配：本技能上一级的 `SKILL.md`。
- 运维、诊断与恢复：`field-guide.md`。
- 门禁判据与 schema：`.bugate/.shared/skills/bugate/`。
