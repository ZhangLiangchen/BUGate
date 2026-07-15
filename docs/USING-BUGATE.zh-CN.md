# 导入之后:BUGate 使用指导

[English](USING-BUGATE.md) | [简体中文](USING-BUGATE.zh-CN.md)

操作者手册:BUGate 已导入你的 SUT 自动化测试仓——日常在 Claude Code / Codex
里怎么用?五分钟跑起第一个受治理的用例。(下文 `.bugate` = 你的 vendor 目录。)

## 0. 每个会话先把目录开对

在 Claude Code / Codex 中,把 **SUT 测试仓本身**(含 `bugate.config.yaml` 的
目录)作为项目根打开。hook 从会话工作区加载——开在父目录的会话**没有任何
物理守卫**。Codex 专属:hook 变更后,按 Codex Desktop 提示 re-trust hash,
否则 hooks 静默不生效。

## 1. 新需求的标准工作循环

把需求材料(PRD、提测说明、访谈记录)放进仓内,然后用自然语言告诉 agent:

> 使用 BUGate:为 <需求> 开一个新用例 `UC-<域>-<NN>-<slug>`,根据 <路径> 的
> PRD 填写 pre-code 工件,然后跑 orchestrator `--auto`,在人工检查点停下等我。

agent 应做的事(门禁会逐项验证):

1. **撰写 pre-code 工件**,位于 `docs/usecases/UC-<...>/`——业务理解(01)、
   可测性(02)、用例清单(03)。orchestrator 会用 kit 模板补齐缺失文件。
2. **跑完整 pre-code 链**:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/UC-<...> --auto
   ```

   `--auto` = 双 agent 多视角评审(真实 Codex + Claude 对端)→ Layer 1/2/3
   语义门 → 可读用例生成(03a)→ 双 agent 对抗评审(3B,将 `03b` 重写为
   `pending` 骨架)→ 全契约校验 → fail-closed 降级检查。步骤短路:第一个
   失败的门终止链条。
3. **人工检查点(你的活,不是 agent 的)**:读
   `00_multiview/divergence_report.md` 与对抗视图,评审发现真缺口就完善工件,
   然后把 `03b` 的 `gate_status` 置为 `passed` 完成放行。在全部必需工件声明
   `passed` 之前,写守卫会物理拦截该 UC 的测试代码——这是设计,不是故障。
4. **让 agent 实现 Layer 4**:工件过门后,守卫放行该 UC(且仅该 UC)的测试
   文件编辑。agent 按清单的 case 与 oracle 写测试。
5. **跑测试,闭环收尾**:

   ```bash
   python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/UC-<...> \
     --auto --scope post-run --pytest-log <run.log> \
     --command "<真实测试命令>" --env <环境> --exit-code <rc>
   ```

   post-run 做运行分类(self-healing 判定)并重生成执行报告(04)与知识
   更新(05)草稿。**先备份人工撰写的 04/05——post-run 会直接覆写**;把
   历史合并回去,人工裁定 self-healing 判定,按真实结果定 `gate_status`
   (SUT 缺陷未修就诚实保持 `failed`)。

## 2. 命令速查

| 意图 | 命令 |
|---|---|
| 看 UC 状态 | `python3 .bugate/scripts/sdtd_orchestrator.py docs/usecases/<UC>` |
| 全 pre-code 链 | `... <UC> --auto` |
| 保留人工验收过的 03b(跳过重审,响亮记录) | `... <UC> --auto --skip-peer-review` |
| 查某测试文件是否放行 | `python3 .bugate/scripts/check_bugate.py <test-path> </dev/null>`(0 = 放行,2 = 拦截) |
| 一键能力自检 | `python3 .bugate/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke` |
| 跨会话记忆检回/记录 | `.bugate/bin/memory-recent --agent <role>` / `python3 .bugate/scripts/memory_bus.py note ...` |

对端调度环境旋钮(机器需要时):`SDTD_CLI_HTTPS_PROXY`/`SDTD_CLI_HTTP_PROXY`/
`SDTD_CLI_ALL_PROXY`(只注入对端 CLI 子进程的代理)、`SDTD_CLAUDE_MODEL`/
`SDTD_CODEX_MODEL` + `SDTD_CLAUDE_EFFORT`/`SDTD_CODEX_REASONING_EFFORT`
(钉住评审质量)、`SDTD_CLI_TIMEOUT_SECONDS`。推荐模式:在仓内提交一个本地
wrapper,export 这些变量后 exec orchestrator。

## 3. 永远由人做的事

- 接受 `03b_adversarial_cases.yaml`(以及每次 `--auto` 之后的重新接受——
  它会被有意重置为 `pending`)。
- 日志含轮询词汇时裁定 self-healing 判定;签署 04/05 内容。
- 判定缺陷 vs 预期行为;事故与其闭环。
- 门禁拒绝的一切:答案永远是修工件或修环境,绝不为绿降门。

## 4. 门禁会拦下的反模式(按设计工作)

- 让 agent 对没有 passed 工件的 UC "直接写测试" → 拦截,exit 2,列缺失工件。
- `--auto` 把 03b 降为 pending 后继续改被守卫的测试 → 拦到人工重新放行为止。
- 把降级评审(exit 3)当绿——应按 环境/kit/SUT 三分法定位失败对端;
  `--allow-degraded-peer-review` 仅用于明确接受占位评审的场合。
- 用放宽正则来"修" `cannot bind to a UC artifact dir` ——这个消息意味着
  绑定错了;适配规则见 vendored `bugate-import` 技能。

## 5. 更深的文档在哪

- 布局适配(非默认框架/命名):vendored 技能
  `.bugate/.shared/skills/bugate-import/SKILL.md`。
- 运维与诊断(对端调度失败、覆写语义、复制卫生):
  `.bugate/docs/IMPORT-FIELD-GUIDE.md`。
- 门禁判据与工件契约:`.bugate/.shared/skills/bugate/`(SKILL.md + references/)。
