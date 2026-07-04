# BUGate —— Init Prompt（中文）

> **克隆 BUGate 后，把整份文件粘给你的 AI 编码 agent（Claude Code / Codex）**，它就会验证环境、确认门引擎可用，并把你路由到正确的路径 —— **导入模式**（唯一使用形态：BUGate 装进你的 SUT 测试仓）或**开发 BUGate 本身**（维护者；调试时可选地把 SUT 挂进本仓）。人工按同样的步骤手动执行亦可。本文件是英文 [INIT.md](INIT.md) 的对齐翻译，结构逐节一致。
>
> **先说好消息：** BUGate *核心*是**零依赖**的 —— 纯 Python 标准库。用门引擎**无需 `pip install` 任何东西**。这里说的「装依赖」，指的是*验证 Python* 以及*可选地*加上 agent 记忆子系统。

---

## Agent 指令

你正在引导一个刚克隆的 **BUGate** 仓库 —— 一个与 SUT 无关、AI 驱动的黑盒测试门引擎。按顺序完成下面的步骤，逐步汇报结果，只有某步失败时才停下来问用户。

### 第 0 步 —— 选择路径（用它 vs 开发它）

BUGate 只有一种使用形态 —— 导入（规范规则：`CHARTER.md` §2 + 修正案 A3）。先问清楚适用哪条路径：

- **使用者路径 —— 导入模式（默认）。** 他们在采用 BUGate 来治理某个 SUT
  自动化测试仓。先跑下面第 1–3 步验证核心，然后运行安装器 ——
  `python3 scripts/bugate_init.py <sut-repo>` —— 或按 README **「Quickstart
  A) Imported mode」**手工操作：把引擎 + skill 装进 SUT 仓、在那边接线
  hooks、并把 `bugate.config.yaml` + profile **提交进那个仓**。日常 agent
  会话随后打开的是 **SUT 仓**，不是本仓。
  想看一次真实的端到端导入 —— BUGate 从中提取出来的那个 SUT，重新采用它自己的
  kit —— 读
  [`docs/case-studies/origin-sut-import.md`](docs/case-studies/origin-sut-import.md)。
- **维护者路径 —— 开发 BUGate 本身（非使用形态）。** 他们在完善这个工具（core
  脚本/hooks、方法论、profile schema、语义门、demo、跨 SUT 回归）。继续走完下面
  **全部**步骤，包括用软链接 + 本地不提交的 profile 指针「挂载 SUT」。

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

期望 `mode= core | guard= [] | precode= 5`。核心默认**未挂载**：写守卫关闭，`artifact_dir` 为空，直到某个 SUT profile 设置它们。

### 第 4 步 —— （可选）接线你的 agent 运行时

BUGate 作为 skill 跑在 Claude Code 与 Codex 之下：

- 技能：`.shared/skills/bugate/`（通过 `.claude/skills/` 与 `.codex/skills/` 里的软链接被发现）。
- Hooks：`.claude/settings.json` 与 `.codex/hooks.json`。根定位**无需 git** 且已拆分：hook 向上找 `scripts/bugate_core.py` 定位引擎；门脚本自 CWD 向上找最近的 `bugate.config.yaml` 定位被治理工作区（开发态用哨兵 fallback）。
- **仅 Codex：** 改任何 hook 都要在 Codex 的 hook 管理界面重新信任其 hash。

这一步无需安装 —— hooks 调用的正是你在第 2 步验证过的、只用标准库的脚本。

---

## 挂载 SUT（开发 BUGate 时的调试辅助）

> 想日常治理 SUT，请改用**导入模式**（第 0 步；README Quickstart A）—— BUGate
> 装进 SUT 仓、profile 提交在那边。下面的挂载是**开发态调试**设置：本仓保持项目
> 根，profile 指针保持本地、不提交。

核心自身什么都不做；你通过一个 **profile** 挂载一个被测系统。

1. 在本地 `sut/` 目录下建一个 profile（完整 key 契约：
   [`profile-schema.md`](.shared/skills/bugate/references/profile-schema.md)；
   `scripts/bugate_init.py` 为导入仓脚手架出同样的文件形状），声明你的 SUT
   的接触面：

   ```yaml
   artifact_dir: docs/usecases                 # UC 工件（01–03…）所在目录
   guarded_path_regex:                          # 写守卫保护哪些测试文件
     - "tests/.*/test_.*[.]py$"
   required_precode_artifacts:                  # 想改就覆盖默认的 01–05 集
     - 01_business_brief.md
     - 02_testability.md
     - 03_inventory.yaml
   ```

   `.shared/skills/bugate/templates/` 下随附的模板开箱即过 pre-code 门（第 2
   步），写守卫的验收在运行时临时构造其工作区（`tests/test_write_guard_layouts.py`）。

2. 在 `bugate.config.yaml` 里指向它：

   ```yaml
   profile: sut/<name>.profile.yaml
   ```

   > 本地、逐 clone 的编辑 —— **不要提交**这行 `profile:`；每个 clone 各自挂载自己的 SUT。

   > **独立仓库？软链接挂载，不要嵌套。** 若 SUT 测试工作区是它自己的 git 仓库，
   > 把它放在独立目录里再软链接挂载（`ln -s ../my-sut my-sut`），然后**本地**忽略
   > 该软链接（`printf '/my-sut\n' >> .git/info/exclude` —— 不带尾斜杠，软链接对
   > git 不是目录）。切勿把 SUT 仓库嵌套进 BUGate 工作树：软链接让门引擎在同样的
   > 相对路径上照常工作，而两个仓库保持完全独立的历史、远程与生命周期。

3. profile 完整参考：[`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md)。
   方法论与门流程：[`README.md`](README.md) 与 [`docs/qa-methodology/METHOD.md`](docs/qa-methodology/METHOD.md)。

---

## 可选能力 —— 运行时你自己装，驱动脚本我们提供

零依赖核心覆盖 **4 层门**。另外三套机制以**驱动脚本**形式随核心发布，它们调用你**自行安装**的运行时；运行时缺席时**优雅回退**。

### a) 双 agent 视角互审（Wave 1）

两个独立 AI agent 并行提取业务模型，在 Layer 1 通过前给出分歧报告。

- **你装：** `codex` 与 `claude` 两个 CLI（放到 `PATH`）。
- **我们提供：** `scripts/sdtd_multiview.py` + `scripts/sdtd_multiview_cli_bridge.py`。

```bash
python3 scripts/sdtd_multiview_cli_bridge.py check-env          # 显示 codex/claude 是否就位 + dispatch_mode
python3 scripts/sdtd_multiview_cli_bridge.py run-all <uc-dir>   # 两个 CLI 都在=真派发；否则占位回退
```

用环境变量调：`SDTD_CODEX_MODEL` / `SDTD_CLAUDE_MODEL` / `SDTD_*_EFFORT`、代理 `SDTD_CLI_*_PROXY`。任一 CLI 缺失会**回退到确定性占位**，产物流照常跑。

### b) Agent 记忆 + 经验晋级

跨会话记忆，以及一个把学到的发现「确认/晋级」的闭环。

- **先探测（复用优先）：** `bin/memory-bus-status` —— 总线是机器级的，本机任一仓已在托管时**无需任何安装**：只要在 profile 里声明 `memory.namespace` 即可。（`bugate init` 会替你跑这个探测并报告结果。）
- **你装（MCP，仅当全机没有运行中的服务 —— 每台机器装一次）：** `pip install mcp-memory-service`，再把 ONNX 嵌入模型一次性预下载到 `~/.cache/mcp_memory/onnx_models`（一次性；服务内置下载器走不了 SOCKS 代理）。
- **我们提供：** `scripts/memory_bus.py` + `bin/memory-bus-*` + `bin/memory-service-*` + `bin/promote-memory`。

```bash
bin/memory-bus-start                                    # 已有健康服务则复用，否则拉起（从 .venv 或 PATH 解析 `memory`）
bin/memory-bus-status
bin/memory-service-note --agent <a> --type finding --msg "..."
bin/promote-memory ...                                  # 把一条 finding 晋级为 status:confirmed
```

命名空间来自 SUT profile（`memory.namespace`）或 `MEMORY_BUS_PROJECT_TAG`（默认 `project:bugate`）。服务是**机器级**的（ADR-BUGATE-003）：全机一个实例，数据家目录 `~/.bugate/memory-bus/`（用 `BUGATE_MEMORY_HOME` 覆盖；服务自身的 `MCP_MEMORY_BASE_DIR` 优先级最高），被本机所有被治理仓共享、项目间靠 namespace tag 隔离 —— 被治理仓只在 profile 里声明其 namespace，**不**脚手架本地服务目录。仓内遗留 `.memory_bus/` 仍作为弃用回退被读取。可选 macOS 加固：`bin/memory-bus-install-launchd`（RunAtLoad + KeepAlive；`--uninstall` 卸载）。服务/CLI 缺席时，脚本打印安装提示并非致命退出。

### c) 三层 agent 角色隔离（Wave 7）

- **我们提供：** `scripts/check_agent_role_paths.py`（一个 PreToolUse 路径守卫）。
- 用 `BUGATE_AGENT_ROLE=builder|designer|implementer` 按会话启用；禁止的路径 pattern 来自 SUT profile 的 `agent_roles:` 映射。未设角色 / profile 无规则 → 空操作（默认 OFF）。

---

## 全功能自检（安装完成后）

当核心、agent 运行时以及任何可选运行时都装好并登录后，跑一次**端到端**的能力体检。优先用内置 skill —— 它通过 `.claude/skills/bugate-full-check` 与 `.codex/skills/bugate-full-check` 被发现：

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
1. 先读取 .shared/skills/bugate/SKILL.md，确认当前是 core mode 还是挂载了
   SUT profile 及其自动化测试工作区。不要编造任何 SUT 事实。
2. 验证 core 4-layer gate（仓内无示例 SUT 树，一律模板 + 临时构造）:
   - python3 -m py_compile scripts/*.py
   - python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
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
8. 验证 Wave 7 角色隔离（临时 profile，见 full-check 的构造方式）:
   - 在 /tmp 造一个含 agent_roles 的 profile，用 BUGATE_PROFILE=<该文件> 加
     BUGATE_AGENT_ROLE=implementer 测被禁路径应返回 2，允许路径应返回 0。
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
真实 SUT 测试工作区全部门禁已激活，只能说 core/demo/optional runtime 已验证。
```

---

## 一览表

| 目标 | 你装什么 | 我们提供 | 缺席会怎样？ |
|---|---|---|---|
| 4 层门引擎（核心） | **无** | 门脚本 + 模板 | —（永远可用） |
| 在 agent 里跑 | 无 | `.claude` / `.codex` hooks | — |
| 挂载 SUT（开发态调试）/ 导入进 SUT 仓 | 无 | `bugate.config.yaml` + profile schema | — |
| 双 agent 互审 | `codex` + `claude` CLI | `sdtd_multiview*` | 会 → 确定性占位 |
| Agent 记忆 + 晋级 | `mcp-memory-service` + ONNX 模型 | `memory_bus.py` + `bin/memory-*` | 会 → 安装提示，非致命 |
| Agent 角色隔离 | 无 | `check_agent_role_paths.py` | —（默认 OFF） |

**结论：** `git clone` → `python3 --version`（3.9+）→ 跑第 2 步冒烟测试 → **核心零安装即就绪**。双 agent 与记忆能力是可选项：装上对应运行时（CLI / `mcp-memory-service`），我们提供的驱动脚本就会用它们，缺席时干净回退。
