# BUGate —— Init Prompt（中文）

[English](INIT.md) | [简体中文](INIT.zh-CN.md)

> **克隆 BUGate 后，把整份文件粘给你的 AI 编码 agent（Claude Code / Codex）**，它就会验证环境、确认门引擎可用，并把你路由到正确的路径 —— **导入模式**（唯一使用形态：BUGate 装进你的 SUT 测试仓）或**开发 BUGate 本身**（维护者；纯 core 迭代）。人工按同样的步骤手动执行亦可。本文件是英文 [INIT.md](INIT.md) 的对齐翻译，结构逐节一致。
>
> **先说好消息：** BUGate *门引擎*是**零依赖**的 —— 纯 Python 标准库,**跑门无需 `pip install` 任何东西**。**记忆总线**(长期记忆 + 晋级)是**必要核心组件**,但无需手工装:`bugate init` / `bin/memory-bus-*` 自动安装机器级服务并在异常时自愈(`BUGATE_MEMORY_NO_INSTALL=1` 可在离线/受限机器跳过)。**双 agent CLI** 仍为可选。
>
> 在 BUGate 发布 packaged console-script 前，文档里的 `bugate init` shorthand
> 指 `python3 scripts/bugate_init.py`。
>
> 当前正式版本是 **v0.4.2**。tar/zip archive 随附
> `bugate-0.4.2.SHA256SUMS`；导入模式必须在解压前校验所选 archive（见
> `IMPORT_PROMPT.zh-CN.md`）。

---

## Agent 指令

你正在引导一个刚克隆的 **BUGate** 仓库 —— 一个与 SUT 无关、AI 驱动的黑盒测试门引擎。按顺序完成下面的步骤，逐步汇报结果，只有某步失败时才停下来问用户。

### 第 0 步 —— 选择路径（用它 vs 开发它）

BUGate 只有一种使用形态 —— 导入（规范规则：`CHARTER.md` §2 + 修正案 A4）。先问清楚适用哪条路径：

- **使用者路径 —— 导入模式（默认）。** 他们在采用 BUGate 来治理某个 SUT
  自动化测试仓。先跑下面第 1–3 步验证核心，然后用
  [`IMPORT_PROMPT.zh-CN.md`](IMPORT_PROMPT.zh-CN.md) 走 release-tarball 接入路径；
  如果你明确要从当前源码 checkout 接入，也可以运行安装器 ——
  `python3 scripts/bugate_init.py <sut-repo>`。无论哪条路径，BUGate 都会把
  engine + skill vendor 进 SUT 仓、在那边接线 hooks，并创建需要**提交进那个仓**
  的 `bugate.config.yaml` + profile。日常 agent 会话随后打开的是 **SUT 仓**，
  不是本仓。
- **维护者路径 —— 开发 BUGate 本身（非使用形态）。** 他们在完善这个工具（core
  脚本/hooks、方法论、profile schema、语义门、跨 SUT 回归）。继续走完下面
  core 验证步骤。真实 SUT 验收应通过把 BUGate 导入外部 SUT 测试仓或 BUGate
  core 之外的 scratch 仓完成；不要把 SUT 挂进本仓。

任何导入模式写入前，先分类目标并且只选一个入口：

- **全新目标（无 vendor path）：** 使用已解包 release 的
  `scripts/bugate_init.py`，先 `--dry-run`，再只执行一次正式安装。
- **受支持 v0.3.x import（无 vendored updater）：** 禁止 rerun installer。
  从 SUT 仓根目录使用已解包 v0.4.2 的 bootstrap：

  ```bash
  python3 /outside/bugate-0.4.2/scripts/bugate_update.py status . --vendor-dir .bugate
  python3 /outside/bugate-0.4.2/scripts/bugate_update.py plan . --vendor-dir .bugate
  # 完整复核，并要求 Decision: GO。
  python3 /outside/bugate-0.4.2/scripts/bugate_update.py apply . --vendor-dir .bugate
  python3 /outside/bugate-0.4.2/scripts/bugate_update.py verify . --vendor-dir .bugate
  ```

- **v0.4+ import：** 使用已安装入口。没有隐式 `latest`；必须显式选择目标版本：

  ```bash
  .bugate/bin/bugate-update status
  .bugate/bin/bugate-update plan --to 0.4.2
  # 完整复核，并要求 Decision: GO。
  .bugate/bin/bugate-update apply --to 0.4.2
  .bugate/bin/bugate-update verify
  # 仅用于有意撤销一个已提交 transaction：
  .bugate/bin/bugate-update rollback --transaction <transaction-id>
  .bugate/bin/bugate-update verify
  ```

离线更新时，`plan` 与 `apply` 都必须同时传匹配的 archive 和 checksum：
`--archive /outside/bugate-0.4.2.tar.gz --checksums
/outside/bugate-0.4.2.SHA256SUMS`。`status`、`plan`、`verify` 都是只读的；
`plan` 与 `apply --dry-run` 对目标零持久写入。managed local change、未知 hook
shape、type/mode drift 或混合 legacy layout 会令 plan `NO-GO`；没有宽泛
`--force`，也不能退回 installer。无关 dirty files 保持不动，只报告 warning。

engine update 与 profile migration 是两个动作。updater 可报告
`migration_available` 或会阻塞的 `migration_required`，但绝不编辑
`bugate.config.yaml`、`bugate.profile.yaml`、测试、artifacts、evidence、Memory
或 SUT-owned hooks。任何 profile migration 都要单独评审，并作为独立可回滚变更
提交。

首次安装后，或 apply 改变 hook 后，必须以导入仓为根启动**新** session。只有
installer/updater 报告 `.codex/hooks.json` 已变化时才在 Codex Desktop
re-trust；re-trust 不能代替新 session。

### 第 1 步 —— 检查唯一的硬要求：Python

```bash
python3 --version    # 要求 Python >= 3.9（推荐 3.10+）
```

门引擎只导入标准库（`argparse json os pathlib re dataclasses typing …`）。若 `python3` 是 3.9+，**核心无需安装任何依赖**。

### 第 2 步 —— 验证核心可用（零安装冒烟测试）

在仓库根目录执行，逐行确认：

```bash
python3 -m py_compile scripts/*.py && echo "compile: OK"
python3 -c "import sys; sys.path.insert(0,'scripts'); import bugate_core; print('engine import: OK')"
python3 scripts/check_bugate_inventory_semantics.py .shared/skills/bugate/templates   # 期望 PASS
python3 scripts/check_bugate_brief_semantics.py     .shared/skills/bugate/templates   # 期望 PASS
```

期望：每个脚本都编译通过、`bugate_core` 可导入、两个门都打印 `PASS`。若如此，**核心就绪 —— 没有安装任何依赖。**

### 第 3 步 —— 确认配置可加载

```bash
cd scripts && python3 -c "import bugate_core as c; cfg=c.load_config(); print('mode=', cfg.get('mode'), '| guard=', cfg.get('guarded_path_regex'), '| precode=', len(c.required_precode_artifacts(cfg)))" ; cd ..
```

期望 `mode= core | guard= [] | precode= 5`。核心默认保持**纯净**：写守卫关闭，`artifact_dir` 为空，直到导入后的 SUT profile 在被治理 SUT 测试仓内设置它们。

### 第 4 步 —— （可选）接线你的 agent 运行时

BUGate 作为 skill 跑在 Claude Code 与 Codex 之下：

- 技能：`.shared/skills/bugate/`（Claude Code 通过 `.claude/skills/` 发现，Codex 通过 `.agents/skills/` 发现；`.codex/skills/` 仅作为旧 Codex 兼容桥保留）。
- Hooks：项目开发态用 `.claude/settings.json` 与 `.codex/hooks.json`；plugin 安装态用 plugin-root 的 `hooks/hooks.json`。根定位**无需 git** 且已拆分：hook 向上找 `scripts/bugate_core.py` 或使用 plugin/vendor root 定位引擎；门脚本自 CWD 向上找最近的 `bugate.config.yaml` 定位被治理工作区（开发态用哨兵 fallback）。v0.4.0 保持 pre-code guard 与 role-evidence guard 职责独立；orchestrator/Core mutator 也执行同一 role preflight，因为 Python 直接写文件不会触发 agent PreToolUse hook。
- Plugins：`.claude-plugin/plugin.json` 与 `.codex-plugin/plugin.json` 只放 manifest；共享的 `skills/`、`commands/`、`agents/`、`hooks/`、`scripts/`、`bin/` 都在 plugin root。
- **Runtime reload：** 任何 hook 变化都要求新 Claude/Codex session。
  **仅 Codex：** 只有 Codex hook 文件 hash 确实变化时才在 hook 管理界面
  re-trust。**Claude plugin 改动：**还要运行 `/reload-plugins` 或重新安装/更新
  plugin。

这一步无需安装 —— hooks 调用的正是你在第 2 步验证过的、只用标准库的脚本。

---

## 开发 BUGate 本身（纯 core 迭代）

> 想日常治理 SUT，请改用**导入模式**（第 0 步；README Quickstart A）—— BUGate
> 装进 SUT 仓、profile 提交在那边。BUGate core 迭代本身保持 SUT-neutral，
> 不在本仓挂载 SUT 工作区。

核心自身不携带任何 SUT 事实。维护者用模板和临时构造 fixture 验证可复用引擎：

```bash
python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
python3 tests/test_write_guard_layouts.py
python3 tests/test_init_scaffold.py
python3 tests/test_hook_surface_parity.py
python3 scripts/check_no_sut_terms.py --terms-file tests/fixtures/legacy-sut-terms.txt
```

真实接入验收请对 BUGate core 之外的外部 SUT 测试仓或 scratch 仓执行
`python3 scripts/bugate_init.py <sut-repo>`，然后把那个 SUT 仓作为项目根打开。
profile 完整参考：[`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md)。
方法论与门流程：[`README.md`](README.md) 与 [`docs/qa-methodology/METHOD.md`](docs/qa-methodology/METHOD.md)。

---

## 核心之外的运行时

零依赖核心覆盖 **4 层门**，另外三套机制继续扩展它。**记忆总线（b）是必要的**
—— `bugate init` / `bin/memory-bus-*` 自动安装并自愈，无需手工操作。
**双 agent CLI（a）** 仍是可选项，缺席时优雅回退。Wave 7 生命周期治理（c）
只用标准库、由 imported profile opt-in；一旦设为 `required` 就有意 fail-closed，
不会降级放行。

### a) 双 agent 视角互审（Wave 1）

两个独立 AI agent 并行提取业务模型，在 Layer 1 通过前给出分歧报告。

- **你装：** `codex` 与 `claude` 两个 CLI（放到 `PATH`）。
- **我们提供：** `scripts/sdtd_multiview.py` + `scripts/sdtd_multiview_cli_bridge.py`。

```bash
python3 scripts/sdtd_multiview_cli_bridge.py check-env          # 显示 codex/claude 是否就位 + dispatch_mode
python3 scripts/sdtd_multiview_cli_bridge.py run-all <uc-dir>   # 两个 CLI 都在=真派发；否则占位回退
```

用环境变量调：`SDTD_CODEX_MODEL` / `SDTD_CLAUDE_MODEL` / `SDTD_*_EFFORT`、代理 `SDTD_CLI_*_PROXY`。任一 CLI 缺失会**回退到确定性占位**，产物流照常跑。

### b) Agent 记忆 + 经验晋级（必要核心）

跨会话长期记忆、双 agent 进展同步/接力,以及一个把学到的发现「确认/晋级」的闭环 —— 缺了它,BUGate 装配就是不完整的。

- **自动安装 + 自愈：** `bugate init` / `bin/memory-bus-*` 复用运行中的机器级服务、重启崩溃的服务,或在缺席时一次性安装(`~/.bugate/venv` + `mcp-memory-service` + ONNX 模型),异常时自愈拉起。你无需手工操作,在 profile 里声明 `memory.namespace` 即可。
- **手工/离线路径**（或 `BUGATE_MEMORY_NO_INSTALL=1` 时）：`pip install mcp-memory-service`,再把 ONNX 模型预下载到 `~/.cache/mcp_memory/onnx_models`（服务内置下载器走不了 SOCKS 代理）。
- **我们提供：** `scripts/memory_bus.py` + `bin/memory-bus-*` + `bin/memory-service-*` + `bin/promote-memory`。

```bash
bin/memory-bus-start                                    # 复用运行中 / 重启崩溃 / 缺席则一次性安装
bin/memory-bus-status
bin/memory-service-note --agent <a> --type finding --msg "..."
bin/promote-memory ...                                  # 把一条 finding 晋级为 status:confirmed
```

命名空间来自 SUT profile（`memory.namespace`）或 `MEMORY_BUS_PROJECT_TAG`（默认 `project:bugate`）。服务是**机器级**的（ADR-BUGATE-003）：全机一个实例，数据家目录 `~/.bugate/memory-bus/`（用 `BUGATE_MEMORY_HOME` 覆盖；服务自身的 `MCP_MEMORY_BASE_DIR` 优先级最高），被本机所有被治理仓共享、项目间靠 namespace tag 隔离 —— 被治理仓只在 profile 里声明其 namespace，**不**脚手架本地服务目录。仓内遗留 `.memory_bus/` 仍作为弃用回退被读取。可选 macOS 加固：`bin/memory-bus-install-launchd`（RunAtLoad + KeepAlive；`--uninstall` 卸载）。记忆总线是**必要核心组件**：`bugate init` / `bin/memory-bus-*` 缺席则**自动安装**机器级服务、异常则**自愈拉起**。普通 recall/note/Stop 与每次编辑继续 best-effort/本地校验；Wave 7 `memory_mode: required` 下，临时故障会有意只阻塞下一次 handoff/acceptance/completion，并且不生成解锁 receipt。`BUGATE_MEMORY_NO_INSTALL=1` 可在离线/受限机器跳过自动安装。

### c) 可审计生命周期角色治理（Wave 7）

- **我们提供：** `bin/bugate-role`、`scripts/role_governance.py`、
  `scripts/check_role_evidence.py`，以及独立且兼容 legacy 的路径守卫
  `scripts/check_agent_role_paths.py`。
- **默认值：** `role_governance.mode: off`，v0.3.x profile 行为不变；
  `agent_roles` 仍可单独使用，它不是生命周期状态机。
- **required 模式：** 未设置/错误角色和缺 required session ID 都会阻塞；历史
  passed UC 也必须新建 human/designer/implementer receipts。用三个独立会话：

  ```bash
  bin/bugate-role run --role designer -- codex
  bin/bugate-role run --role implementer -- claude
  bin/bugate-role run --role reviewer -- codex
  ```

  Pre-code `--init` 与 `--auto` 必须分开运行。人类把 03B 改为
  `gate_status: passed` 后，不得再次跑 `--auto`：designer 用
  `bugate-role approve` 记录既有决定，再 handoff；新 implementer session
  使用 receipt 的 exact `memory.memory_id` 接单。进入 post-run 前还需要
  implementer handoff 与新的 reviewer acceptance。完整命令见
  [README 操作顺序](README.zh-CN.md#wave-7-可审计生命周期角色v040) 与
  [规范协议](docs/qa-methodology/ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)。

普通编辑只验证本地 hash chain；strict Memory 故障会阻塞下一次转换，恢复后可
幂等重试。profile/pre-code drift 从 human/designer evidence 重新开始；
implementation drift 从 implementer handoff/reviewer acceptance 重新开始。
Evidence 只追加，禁止删除或手改来 reset。

`bugate-role run` 只给子进程 export role/session。Hook 不能 export 到父进程，
已经运行的 Desktop app 必须从目标环境重新启动；Codex hook hash 确实变化时
Codex Desktop 还必须 re-trust（v0.4.0 转换就是这种情况）。证据链不是强身份认证：`approved_by` 只是声明，本地 hook
拦不住任意 shell/外部编辑器写入；不可抵赖需要 OS/container/managed-runner
或按角色 credential 隔离。

---

## 全功能自检（安装完成后）

当核心、agent 运行时以及任何可选运行时都装好并登录后，跑一次**端到端**的能力体检。优先用内置 skill —— 它通过 `.claude/skills/bugate-full-check` 与 `.agents/skills/bugate-full-check` 被发现（`.codex/skills/bugate-full-check` 保留为旧 Codex 兼容桥）：

```text
Use $bugate-full-check to verify this BUGate checkout end to end.
```

这个 skill 位于 `.shared/skills/bugate-full-check/`，并随附一个可运行的驱动脚本：

```bash
python3 .shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke
python3 .shared/skills/bugate-full-check/scripts/run_full_check.py --mode full
```

若当前运行时还不能自动发现该 skill，就把下面的 **fallback prompt** 原样交给 agent。目标不是停在 `check-env`，而是区分「已安装」「core 可用」「可选运行时可用」「真实 SUT 测试工作区已通过 profile 激活」。（现场安装踩坑 —— 原生安装器、`PATH` 顺序、额外的 ONNX 运行时包 —— 见 [`docs/SETUP-OPTIONAL.md`](docs/SETUP-OPTIONAL.md)。）

```text
请在当前 BUGate 仓库做一次全功能自检，并严格遵守 AGENTS.md 与
.shared/skills/bugate/SKILL.md。

要求:
1. 先读取 .shared/skills/bugate/SKILL.md，确认当前是 BUGate core mode，
   还是一个带已提交 profile 的导入式 SUT 测试仓。不要把 SUT 挂进 BUGate core，
   也不要编造任何 SUT 事实。
2. 验证 core 4-layer gate（仓内无示例 SUT 树，一律模板 + 临时构造）:
   - python3 -m py_compile scripts/*.py
   - python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
   - 如果这是带 `.bugate/bin/bugate-update` 的 imported installation，运行其
     只读 `status` 与 `verify`；任何 conflict 或 recovery-required 都是安装检查
     失败，不能拿 core 绿灯掩盖。
3. 验证 Codex / Claude Code:
   - type -a codex; type -a claude
   - codex --version; claude --version
   - 用 codex exec 和 claude -p 各自执行一次 "Reply exactly: ok"，确认不是
     只通过 check-env，而是真能调用模型。
4. 验证双端 bridge:
   - python3 scripts/sdtd_multiview_cli_bridge.py check-env
   - python3 scripts/sdtd_adversarial_cli_bridge.py check-env
   - 用 python3 scripts/sdtd_orchestrator.py <tmp>/peer-uc --init 在 /tmp 生成
     模板 UC，在其上分别 run-all multi-view 与 adversarial，确认 Codex 和
     Claude 都写出 real peer view，而不是 fallback_placeholder。
5. 验证 memory-bus:
   - bin/memory-bus-status
   - bin/memory-service-note --agent agent --type finding --msg "memory smoke"
   - bin/memory-service-search --query "memory smoke" --limit 1
   - find ~/.cache/mcp_memory/onnx_models -name '*.onnx' -print
   - MCP_MEMORY_BASE_DIR="${BUGATE_MEMORY_HOME:-$HOME/.bugate/memory-bus}" MCP_MEMORY_STORAGE_BACKEND=sqlite_vec
     MCP_MEMORY_USE_ONNX=1 PATH="$PWD/.venv/bin:$PATH" memory status
     需要看到服务 healthy，并尽量确认 onnxruntime/ONNX 路径被触发。
6. 验证 Wave 0 / Wave 8（优雅降级契约，无需 spec fixture）:
   - python3 scripts/check_prd_health.py --gate 应输出 profile_required 且退出码 0
   - python3 scripts/oracle_falsification.py --gate 同上
   - python3 scripts/generate_assertion_coverage_matrix.py --help 应正常退出
7. 验证物理写守卫（双布局，临时构造 fixture）:
   - python3 tests/test_write_guard_layouts.py 应输出 PASS(both layouts)——
     imported(config 标记根)与 engine-development(哨兵 fallback)各自 放行/阻断/fail-closed。
8. 验证 Wave 7 角色治理（临时 profile，见 full-check 的构造方式）:
   - 先确认 legacy `agent_roles` 路径 allow/deny 仍可独立工作。
   - 在 `role_governance.mode: required` 下证明 unset/wrong role 阻断；再用
     scratch fixture 跑完整 designer → human acceptance → handoff → 新
     implementer acceptance → guarded-write allow → implementer handoff → 新
     reviewer acceptance → post-run → completion。加入 strict-Memory failure
     和 profile/artifact/implementation drift 负控；不得使用真实 SUT fixture。
9. 验证 profile hardening gates（强制生效探针）:
   - 用 orchestrator --init 的模板 UC + 一个 require_multiview: true 的临时
     profile 跑 v13 pre-code，应因缺 divergence_report.md 而拒绝（非 0 退出）。
10. 清理所有 /tmp 自检产物，不要改动 SUT 事实或模板源文件。

最后输出一个表格，分为:
- 已安装并验证可用
- 已具备但需要真实 SUT profile / 测试工作区才能激活
- 设计上需要人工接受的 gate
- 当前仓库未脚本化或仅方法论定义的部分

结论必须明确区分:
- "BUGate core + optional runtimes 已可用"
- "真实 SUT 测试工作区的全部门禁已激活"

如果 bugate.config.yaml 仍是 mode: core 且 guarded_path_regex: []，不能宣称
真实 SUT 测试工作区全部门禁已激活，只能说 core、临时构造 fixture 与 optional runtime 已验证。
```

---

## 一览表

| 目标 | 你装什么 | 我们提供 | 缺席会怎样？ |
|---|---|---|---|
| 4 层门引擎（核心） | **无** | 门脚本 + 模板 | —（永远可用） |
| 在 agent 里跑 | 无 | `.claude` / `.codex` hooks | — |
| 导入进 SUT 仓 | 无 | `bugate.config.yaml` + profile schema | — |
| 更新 imported install | 无 | `bugate-update` status/plan/apply/verify/rollback + installed lock/transaction journal | drift/conflict 时 fail-closed；profile migration 独立处理 |
| 双 agent 互审 | `codex` + `claude` CLI | `sdtd_multiview*` | 会 → 确定性占位 |
| Agent 记忆 + 晋级（**必要核心**） | 无 —— 由 `bugate init` 自动安装 | `memory_bus.py` + `bin/memory-*` | 必要；自动安装 + 自愈；普通编辑不阻断，strict 生命周期转换 fail-closed |
| 路径角色隔离 | 无 | `check_agent_role_paths.py` | —（独立、默认 OFF） |
| 可审计生命周期角色 | 无 | `bugate-role` + role-evidence hook/state machine | opt-in；`required` fail-closed |

**结论：** `git clone` → `python3 --version`（3.9+）→ 跑第 2 步冒烟测试 → **门禁引擎零安装即就绪**（stdlib-only）。**记忆服务是必要核心组件**（长期记忆、双 agent 进展同步/接力、记忆升级）：`bugate init` / `bin/memory-bus-*` 检测到未装则**自动安装**机器级 `mcp-memory-service`、异常则**自愈拉起**。普通编辑只做本地角色证据检查，不因 Memory 短暂抖动而阻断；`memory_mode: required` 的 handoff/acceptance/completion 转换则必须 fail-closed。`BUGATE_MEMORY_NO_INSTALL=1` 可在离线/受限机器跳过自动安装。**双 agent CLI（codex/claude）仍为可选**——缺席时干净回退到确定性占位。
