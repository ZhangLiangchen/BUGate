# BUGate 导入 Prompt

[English](IMPORT_PROMPT.md) | [简体中文](IMPORT_PROMPT.zh-CN.md)

> 在 **SUT 自动化测试仓**作为项目根打开时，把这份 prompt 粘给 Claude Code 或
> Codex。agent 应把 BUGate 作为 kit 导入，保持 Claude Code / Codex 双端对称
> 接线，初始化机器级 Memory Bus，在测试布局清晰时激活 SUT profile，并报告剩余
> 人工动作。

## Agent 指令

你正在把 BUGate 安装进一个 SUT 自动化测试仓。BUGate 是 SUT-neutral 的
Agentic QA Governance Kernel。保持 SUT 仓作为项目根；不要在 SUT 仓里 clone
BUGate core，不要把 SUT 挂进 BUGate core，也不要把产品 secret 或环境事实写进
BUGate core 文件。

### 支持范围（遇到不匹配先读这里再决定是否中止）

- 已在 macOS 验证；其他操作系统未经验证，适配由使用方负责。
- 物理门接线按设计只面向 Claude Code + Codex。
- `python3 >= 3.9` 是 **kit 的宿主运行时**——SUT 的测试框架可以是任何语言
  （守卫/门与语言无关）；不要因为 SUT 仓里没有 Python 就中止导入。

### 输入

- 目标 SUT 仓：除非用户给出其他路径，否则使用当前工作目录。
- BUGate 版本：若设置了 `BUGATE_VERSION` 就使用它，否则使用 `0.3.3`。
- Vendor 目录：若设置了 `BUGATE_VENDOR_DIR` 就使用它，否则使用 `.bugate`。
- 若 `BUGATE_ENGINE_DIR` 指向已有 BUGate checkout 或已解包 release，就使用它。
  否则在 SUT 仓外下载 GitHub Release tarball。

### 必须执行的流程

1. **预检 SUT 仓**
   - 运行 `pwd`、`git status --short --branch`、`python3 --version`。
   - 确认 Python >= 3.9。
   - 用只读命令检查测试布局，例如 `find . -maxdepth 3 -type d | sort` 与
     定向 `rg --files`。
   - 如果当前目录就是 BUGate core 本身，停止并询问 SUT 自动化测试仓路径。

2. **在 SUT 仓外获取 BUGate kit**
   - 如果 `BUGATE_ENGINE_DIR` 可用，继续使用。
   - 否则执行等价步骤：

     ```bash
     BUGATE_VERSION="${BUGATE_VERSION:-0.3.3}"
     BUGATE_TMP="$(mktemp -d)"
     if curl -fL -o "$BUGATE_TMP/bugate-${BUGATE_VERSION}.tar.gz" \
       "https://github.com/ZhangLiangchen/BUGate/releases/download/v${BUGATE_VERSION}/bugate-${BUGATE_VERSION}.tar.gz"; then
       tar -xzf "$BUGATE_TMP/bugate-${BUGATE_VERSION}.tar.gz" -C "$BUGATE_TMP"
     elif curl -fL -o "$BUGATE_TMP/bugate-${BUGATE_VERSION}.zip" \
       "https://github.com/ZhangLiangchen/BUGate/releases/download/v${BUGATE_VERSION}/bugate-${BUGATE_VERSION}.zip"; then
       unzip -q "$BUGATE_TMP/bugate-${BUGATE_VERSION}.zip" -d "$BUGATE_TMP"
     else
       echo "BUGate release v${BUGATE_VERSION} 无法下载；请向用户索取 BUGATE_ENGINE_DIR 或有效版本。" >&2
       exit 2
     fi
     BUGATE_ENGINE_DIR="$BUGATE_TMP/bugate-${BUGATE_VERSION}"
     ```

   - 验证 engine 存在：

     ```bash
     test -f "$BUGATE_ENGINE_DIR/scripts/bugate_init.py"
     test -f "$BUGATE_ENGINE_DIR/.shared/skills/bugate/SKILL.md"
     ```

3. **安装前验证下载的 engine**

   ```bash
   cd "$BUGATE_ENGINE_DIR"
   python3 -m py_compile scripts/*.py
   python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
   cd -
   ```

4. **预览并运行 importer**

   ```bash
   SUT_REPO="$(pwd)"
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   python3 "$BUGATE_ENGINE_DIR/scripts/bugate_init.py" "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR" --dry-run
   python3 "$BUGATE_ENGINE_DIR/scripts/bugate_init.py" "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR"
   ```

   importer 必须 vendor kit，并接线 `.claude/skills/`、`.agents/skills/`、
   legacy `.codex/skills/`、`.codex/agents/`、`.claude/settings.json`、
   `.codex/hooks.json`、`bugate.config.yaml`、`bugate.profile.yaml`、
   `docs/usecases/`、`.gitignore` 与机器级 Memory Bus。

5. **只基于证据激活 SUT profile**
   - 打开 `bugate.profile.yaml`。
   - 保留 `memory.namespace`。
   - 如果测试布局清晰，用一个或多个带命名捕获 `(?P<uc>...)` 的 regex 更新
     `guarded_path_regex`。
   - 如果布局有歧义，停止并询问用户 BUGate 应该守护哪些测试路径。
   - 不要编造产品 endpoint、credential、账号、环境名、fixture 或业务事实。profile
     只写 SUT 测试仓接线信息。

6. **验证 Claude Code 与 Codex 接线**

   从 SUT 仓根目录运行：

   ```bash
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   python3 -m json.tool .claude/settings.json >/dev/null
   python3 -m json.tool .codex/hooks.json >/dev/null
   test -f "$BUGATE_VENDOR_DIR/scripts/check_bugate.py"
   test -f "$BUGATE_VENDOR_DIR/scripts/bugate_prompt_reminder.py"
   test -f "$BUGATE_VENDOR_DIR/scripts/memory_bus.py"
   test -f "$BUGATE_VENDOR_DIR/.shared/skills/bugate/SKILL.md"
   test -e .claude/skills/bugate/SKILL.md
   test -e .agents/skills/bugate/SKILL.md
   test -e .codex/skills/bugate/SKILL.md
   test -d .codex/agents
   ```

   然后验证 vendored gate scripts：

   ```bash
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   python3 "$BUGATE_VENDOR_DIR/scripts/check_bugate_v13_semantics.py" \
     "$BUGATE_VENDOR_DIR/.shared/skills/bugate/templates" --scope pre-code
   python3 - "$BUGATE_VENDOR_DIR" <<'PY'
   import sys
   from pathlib import Path
   vendor = sys.argv[1] if len(sys.argv) > 1 else ".bugate"
   sys.path.insert(0, f"{vendor}/scripts")
   import bugate_core
   cfg = bugate_core.load_config(root=Path.cwd())
   print("profile=", cfg.get("profile") or cfg.get("active_profile"))
   print("guarded_path_regex=", cfg.get("guarded_path_regex"))
   print("memory.namespace=", cfg.get("namespace") or cfg.get("memory.namespace"))
   PY
   ```

7. **验证 Memory Bus 初始化**

   ```bash
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   "$BUGATE_VENDOR_DIR/bin/memory-bus-ensure" || true
   "$BUGATE_VENDOR_DIR/bin/memory-bus-status" --no-fail
   ```

   首次安装较慢是可以接受的，只要 status 说明仍在启动。报告时要说明：机器级
   Memory Bus 健康之前，BUGate 安装仍不完整。不要创建 per-repo memory service
   目录。
   - 在线 `pip` 安装是**优先路径**。仅当机器无网络时：设
     `BUGATE_MEMORY_NO_INSTALL=1` 跳过自动安装，按 engine 的
     `docs/SETUP-OPTIONAL.md` §2 手动/离线安装——这是兜底而非推荐路线；
     bus 装好前按「治理已激活、memory 待就绪」口径报告导入结果。

8. **验证写守卫 negative control**
   - 如果 `guarded_path_regex` 仍为空，报告 BUGate 已安装，但在 profile 激活前物理写守卫是 inert。
   - 如果它已激活，选择一个会命中守卫、且对应 UC 没有 accepted pre-code artifacts 的测试路径，运行：

     ```bash
     BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
     python3 "$BUGATE_VENDOR_DIR/scripts/check_bugate.py" <guarded-test-path> </dev/null
     ```

   - 期望退出码 `2`，并输出缺失 artifact 列表。如果退出 `0`，解释 guard 为什么没有命中，并修正 profile 或路径选择。
   - 可选的一键自检（v0.3.2+，布局自适应，从 SUT 仓根直接跑）：

     ```bash
     python3 "$BUGATE_VENDOR_DIR/.shared/skills/bugate-full-check/scripts/run_full_check.py" --mode smoke
     ```

     smoke 模式不做真实双端模型调度；`--mode full` 会真实调度 codex+claude。
     若本机直连模型 API 被网络阻断，用 kit 自带的注入面传代理（只作用于对端
     CLI 子进程，不影响 gate 脚本与 git）：`SDTD_CLI_HTTPS_PROXY` /
     `SDTD_CLI_HTTP_PROXY` / `SDTD_CLI_ALL_PROXY`。

9. **报告最终状态**
   - 列出 SUT 仓中所有变更文件/目录。
   - 列出应该提交的精确文件：
     `bugate.config.yaml`、`bugate.profile.yaml`、`$BUGATE_VENDOR_DIR/`、
     `.claude/settings.json`、`.codex/hooks.json`、`.claude/skills/`、
     `.agents/skills/`、`.codex/skills/`、`.codex/agents/`、`docs/usecases/`
     以及 `.gitignore` 中的 BUGate block。
   - 说明 `guarded_path_regex` 是否已激活。
   - 说明 Memory Bus 状态。
   - 说明 Codex 需要在 Codex Desktop 中对变更后的 hook hash 做一次 re-trust，
     Codex hooks 才会生效。Claude Code 是否需要新 session 或 plugin reload 取决于打开方式。
   - 除非用户明确要求，不要 stage、commit 或 push。

### 附录：按需激活可选波次（Wave 7 / Wave 8）

> importer（v0.3.2+）会把实战运维手册 vendor 到
> `$BUGATE_VENDOR_DIR/docs/IMPORT-FIELD-GUIDE.md` —— 导入完成后先读它：
> 双端调度诊断阶梯与代理注入、`--auto` 的 03b 覆写语义、post-run 04/05
> 覆写 SOP、UC 复制卫生、以及下述两个波次的完整激活配方都在里面。

这两个波次默认休眠，是配置开关而非缺陷；证据就绪后在 SUT profile 里开启
（profile 脚手架里已含注释掉的示例块）。

- **Wave 7 角色隔离**：在 profile 顶层加 `agent_roles:`（角色名小写；裸列表 =
  读写皆禁，`read:`/`write:` 子列表分别限定），并在运行时设
  `BUGATE_AGENT_ROLE=<role>`。示例：

  ```yaml
  agent_roles:
    implementer:            # 写测试的角色不许接触业务源码/接口 dump
      - "^docs/raw/source_code/.*"
    designer:
      write:
        - "^tests/.*"       # 设计角色不许直接写测试代码
  ```

  注意：读隔离只对 hook 能看到的 `Read` 工具生效（importer v0.3.2+ 已把角色
  守卫单独接到 `Read|Edit|Write` matcher；写形守卫 `check_bugate` 绝不能挂到
  `Read`，它不辨别 action、会把读也拦下）。shell 级读取（cat/grep）不在物理
  守卫范围内，属评审纪律。
- **Wave 8 突变/证伪**：为真实捕获的证据 JSON 写一份 falsification spec
  （声明式 oracle + 每字段突变；`evidence` 路径相对 spec 文件所在目录），然后
  在 profile 里声明：

  ```yaml
  falsification_spec: <path/to/falsification_spec.yaml>
  falsification_threshold: 0.7
  wave8_evidence_glob: <workspace-relative glob>   # 供 wave8-weekly 使用
  wave8_reports_dir: <workspace-relative dir>      # 建议放 gitignored 目录
  wave8_artifact_root: <inventory 扫描根，如 docs/usecases>
  ```

  验证：`python3 $BUGATE_VENDOR_DIR/scripts/oracle_falsification.py --gate`
  应真实评分（不再 `profile_required`）；周期化用
  `$BUGATE_VENDOR_DIR/bin/wave8-weekly`（v0.3.2+ 布局自适应，报告落
  workspace）。覆盖矩阵门（`require_assertion_coverage`）建议等 spec 覆盖到
  库存引用的多数 oracle 后再开，避免 `missing_implementation` 噪声红。
