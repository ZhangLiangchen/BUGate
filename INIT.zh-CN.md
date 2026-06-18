# BUGate 安装与上手（中文）

> 面向使用者的快速上手说明。**先说必须依赖**：BUGate 核心是**零三方依赖**的，唯一硬要求是 Python。
> 想直接粘给 Claude Code / Codex 当 init prompt 自动执行的英文版：见 [INIT.md](INIT.md)。

## 一句话

BUGate 是一个与被测系统（SUT）无关的、AI 驱动的黑盒测试「门」引擎。核心纯 Python 标准库实现，**clone 即用**。

## ✅ 必须依赖（只有一个）

| 依赖 | 版本 | 说明 |
|---|---|---|
| **Python** | **≥ 3.9**（推荐 3.10+） | 核心唯一硬依赖。门引擎只用标准库，**无需 `pip install` 任何三方包**，仓库也没有 `requirements.txt` 要装。 |

```bash
python3 --version    # 期望 3.9 以上
```

> 没有依赖要装——这是刻意设计：核心保持零依赖、开箱即用。

## 验证安装（零安装冒烟测试）

在仓库根目录执行，每行都应通过：

```bash
python3 -m py_compile scripts/*.py && echo "编译 OK"
python3 -c "import sys; sys.path.insert(0,'scripts'); import bugate_core; print('引擎导入 OK')"
python3 scripts/check_bugate_inventory_semantics.py .shared/skills/bugate/templates   # 期望 PASS
python3 scripts/check_bugate_brief_semantics.py     .shared/skills/bugate/templates   # 期望 PASS
```

全部通过 = 核心就绪（没有安装任何依赖）。

## 挂载你的被测系统（SUT）

核心默认「未挂载」（`bugate.config.yaml` 是 `mode: core`，守卫关闭）。要在真实系统上用：

1. 建一个 profile（如 `sut/<名字>.profile.yaml`），声明你的 SUT 路径：

   ```yaml
   artifact_dir: docs/usecases                 # 用例工件（01–03…）所在目录
   guarded_path_regex:                         # 物理写守卫保护哪些测试文件
     - "tests/.*/test_.*[.]py$"
   required_precode_artifacts:                 # 可覆盖默认的 01–05 工件集
     - 01_business_brief.md
     - 02_testability.md
     - 03_inventory.yaml
   ```

2. 在 `bugate.config.yaml` 指向它：

   ```yaml
   profile: sut/<名字>.profile.yaml
   ```

3. profile 全部字段见 [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md)；方法论与门流程见 [`README.md`](README.md) 与 [`docs/qa-methodology/METHOD.md`](docs/qa-methodology/METHOD.md)。

## Agent 运行时（可选，无需额外安装）

在 Claude Code / Codex 里跑：技能在 `.shared/skills/bugate/`，hooks 在 `.claude/`、`.codex/`。根定位是 **git-free**（靠 `AGENTS.md` + `.shared/` 哨兵）。**Codex** 改任何 hook 需在其 hook 管理界面**重新信任 hash**。这些都复用第 2 步验证过的标准库脚本，无需安装。

## 🔌 可选能力 —— 运行时你自己装，驱动脚本我们提供

零依赖核心覆盖**4 层门**。另外三套机制以**驱动脚本**形式随核心发布,它们调用你**自行安装**的运行时;运行时缺席时**优雅回退**。

### a) 双 agent 视角互审（Wave 1）

两个独立 AI agent 并行提取业务模型,在 Layer 1 通过前给出分歧报告。

- **你装:** `codex` 和 `claude` 两个 CLI(放到 `PATH`)。
- **我们提供:** `scripts/sdtd_multiview.py` + `scripts/sdtd_multiview_cli_bridge.py`。

```bash
python3 scripts/sdtd_multiview_cli_bridge.py check-env          # 显示 codex/claude 是否就位 + dispatch_mode
python3 scripts/sdtd_multiview_cli_bridge.py run-all <uc-dir>   # 两个 CLI 都在=真派发;否则占位回退
```

可用环境变量调:`SDTD_CODEX_MODEL` / `SDTD_CLAUDE_MODEL` / `SDTD_*_EFFORT`、代理 `SDTD_CLI_*_PROXY`。任一 CLI 缺失会**回退到确定性占位**,产物流照常跑。

### b) Agent 记忆 + 记忆晋级

跨会话记忆,以及「发现 → 确认/晋级」的经验沉淀闭环。

- **你装(MCP):** `pip install mcp-memory-service`,再把 ONNX 嵌入模型一次性预下载到 `~/.cache/mcp_memory/onnx_models`(服务内置下载器走不了 SOCKS 代理,需手动先下好)。
- **我们提供:** `scripts/memory_bus.py` + `bin/memory-bus-*` + `bin/memory-service-*` + `bin/promote-memory`。

```bash
bin/memory-bus-start                                    # 拉起服务(从 .venv 或 PATH 解析 `memory`)
bin/memory-bus-status
bin/memory-service-note --agent <a> --type finding --msg "..."
bin/promote-memory ...                                  # 把一条 finding 晋级为 status:confirmed
```

命名空间来自 SUT profile（`memory.namespace`）或 `MEMORY_BUS_PROJECT_TAG`(默认 `project:bugate`)。运行数据落在已忽略的 `.memory_bus/`。服务/CLI 缺席时,脚本打印安装提示并非致命退出。

### c) 三层 agent 角色隔离（Wave 7）

- **我们提供:** `scripts/check_agent_role_paths.py`(PreToolUse 路径守卫)。
- 用 `BUGATE_AGENT_ROLE=builder|designer|implementer` 按会话启用;禁止路径来自 SUT profile 的 `agent_roles:` 映射。未设角色 / profile 无规则 → 空操作(默认 OFF)。

## 一张表看清

| 目标 | 你装什么 | 我们提供 | 缺席会怎样 |
|---|---|---|---|
| 4 层门引擎(核心) | **无** | 门脚本 + 模板 | —(永远可用) |
| 在 agent 里跑 | 无 | `.claude` / `.codex` hooks | — |
| 挂载 SUT | 无 | `bugate.config.yaml` + profile schema | — |
| 双 agent 互审 | `codex` + `claude` CLI | `sdtd_multiview*` | 会 → 确定性占位 |
| Agent 记忆 + 晋级 | `mcp-memory-service` + ONNX 模型 | `memory_bus.py` + `bin/memory-*` | 会 → 安装提示,非致命 |
| Agent 角色隔离 | 无 | `check_agent_role_paths.py` | —(默认 OFF) |

**结论：** `git clone` → 确认 `python3` ≥ 3.9 → 跑冒烟测试 → **核心零安装即就绪**。双 agent 与记忆能力是可选项:装上对应运行时(CLI / `mcp-memory-service`),我们提供的驱动脚本就会用它们,缺席时干净回退。
