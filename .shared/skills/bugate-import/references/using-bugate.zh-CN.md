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

2. 仍以 designer 身份跑完整 pre-code 链：

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC> --auto
   ```

   它依次跑 Wave 1 独立 peer、Layer 1/2/3、03A 生成、03B 对抗 peer、
   完整契约和降级评审检查。Peer 子进程不继承 designer 生命周期身份。
   步骤在第一个失败处终止；03B 保持 `pending` 等人工审阅。

3. **人工检查点：**真实人类阅读分歧/对抗证据，并显式将 03B 设为
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

4. 在**新 implementer 会话**中接单并实现 Layer 4：

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

5. 在**新 reviewer 会话**中用第二个 exact Memory ID 接单，执行测试并生成
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
| 本地 receipt 验证 | `.bugate/bin/bugate-role verify docs/usecases/<UC> --phase <phase>` |
| 本地 + strict Memory 验证 | `... verify ... --strict-memory` |
| 一键能力自检 | `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke` |
| 普通 Memory 检回/记录 | `.bugate/bin/memory-recent --agent <role>` / `python3 .bugate/scripts/memory_bus.py note ...` |

Orchestrator 会输出一个生命周期状态：`BLOCKED`、
`READY_FOR_HUMAN_ACCEPTANCE`、`READY_FOR_DESIGNER_HANDOFF`、
`IMPLEMENTATION_UNLOCKED`、`READY_FOR_REVIEWER_HANDOFF`、`POST_RUN_ACTIVE`或
`CLOSED`。它是状态信号，不是跳过下一个命令的授权。

Peer dispatch 的 `SDTD_CLI_*` 由 profile/repo 持有。Peer 子进程保留 model、
effort、proxy、profile/root 配置，同时清除生命周期 role/session/receipt 身份。

## 3. 永远由人完成

- 接受 03B；CLI 只记录已经发生的决定。
- 裁定 execution/self-healing 结果并签署 04/05。
- 判定缺陷还是预期行为，并对事故闭环负责。
- 门禁拒绝时修证证据或环境，绝不降低门禁。

## 4. 迁移、drift 与边界

- 不含 `role_governance` 的 v0.3.x profile 行为不变。启用 `required` 不会自动
  承认历史 passed UC；必须建立当前 human acceptance、handoff 与 acceptance receipt。
- 同角色/同 session 自接单、非 exact Memory ID、缺 receipt 或直接编辑
  `00_role_evidence/**` 都会被拒绝。
- Profile/pre-code drift 从 designer acceptance/handoff 重建；implementation drift 从
  implementer handoff/reviewer acceptance 重建。追加 superseding generation，不得删除 evidence reset。
- 普通编辑只查本地 hash，不访问 Memory。Memory 故障会在下一次转换时阻塞，
  且不发布本地解锁 receipt。
- 这些控制提供角色声明、session 区分、hash 链接、Memory 锚定和篡改/drift
  检测，但不提供不可抵赖身份。Hook 不能拦截任意 shell 重定向或外部编辑器；
  更强隔离需要 OS 账号、容器、managed runner 或按角色凭据。

## 5. 更深的文档

- 首次安装/更新选路、transactional upgrade、验证、回滚及 profile/session 边界：
  `updating-bugate.zh-CN.md`。
- 布局/profile 适配：本技能上一级的 `SKILL.md`。
- 运维、诊断与恢复：`field-guide.md`。
- 门禁判据与 schema：`.bugate/.shared/skills/bugate/`。
