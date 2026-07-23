# BUGate 导入 Prompt

[English](IMPORT_PROMPT.md) | [简体中文](IMPORT_PROMPT.zh-CN.md)

> 在 **SUT 自动化测试仓**作为项目根打开时，把这份 prompt 粘给 Claude Code 或
> Codex。agent 必须先区分首次安装、外部 bootstrap/pre-lock 路径与
> lock+launcher 仓内更新，再使用
> 唯一适用的事务边界，保留 SUT-owned state，验证结果并报告剩余人工与 runtime
> reload 动作。

## Agent 指令

你正在把 BUGate 安装或更新到一个 SUT 自动化测试仓。BUGate 是 SUT-neutral 的
Agentic QA Governance Kernel。保持 SUT 仓作为项目根；不要在 SUT 仓里 clone
BUGate core，不要把 SUT 挂进 BUGate core，也不要把产品 secret 或环境事实写进
BUGate core 文件。

### 支持范围（遇到不匹配先读这里再决定是否中止）

- 已在 macOS 验证；其他操作系统未经验证，适配由使用方负责。
- 物理门接线按设计只面向 Claude Code + Codex。
- `python3 >= 3.9` 是 **kit 的宿主运行时**——SUT 的测试框架可以是任何语言
  （守卫/门与语言无关）；不要因为 SUT 仓里没有 Python 就中止导入。

### 输入

- 目标 SUT 仓：除非用户给出其他路径，否则使用当前工作目录。目标必须是
  **测试框架的家目录**,且之后的 agent 会话必须以**该目录**为项目根打开——
  hook 从会话工作区加载,开在父目录(monorepo 根)的会话不会加载任何守卫。
  importer 在目标不是 git 顶层时会发出警告;把该警告转达给用户。
- BUGate 目标 release 版本线：若设置了 `BUGATE_VERSION` 就使用它，否则使用
  `0.4.3`。只有该公开 tag/Release 及其资产通过 checksum 校验后，默认值才可用；
  在此之前必须显式选择已发布的 v0.4.2 回退版本，不能把 source branch 文案当作
  release 权威。
- Vendor 目录：若设置了 `BUGATE_VENDOR_DIR` 就使用它，否则使用 `.bugate`。
- 安装路径：只读检测。不能用用户记忆的版本替代 installed layout 证据；只要
  vendor path 以任何形态存在，就绝不能运行 `bugate_init.py`。
- 首次安装可有意使用 BUGate development checkout。legacy bootstrap 必须使用
  带 canonical/legacy manifests 的正式已解包 v0.4.2 或更高 release。若
  `BUGATE_ENGINE_DIR` 不是适用来源，就在 SUT 仓外下载 GitHub Release。
- 公开 v0.4.3 tag/Release 存在后，只有它恰好包含三项资产时才成为权威：
  `bugate-0.4.3.tar.gz`、`bugate-0.4.3.zip` 与
  `bugate-0.4.3.SHA256SUMS`。checksum asset 是必需项；必须在解压前校验所选
  archive。在此之前显式使用已发布的 v0.4.2 回退版本。

### 必须执行的流程

1. **预检并分类 SUT 仓**
   - 运行 `pwd`、`git status --short --branch`、`python3 --version`。
   - 确认 Python >= 3.9。
   - 用只读命令检查测试布局，例如 `find . -maxdepth 3 -type d | sort` 与
     定向 `rg --files`。
   - 如果当前目录就是 BUGate core 本身，停止并询问 SUT 自动化测试仓路径。
   - 设置 `BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"`，然后分类：

     ```bash
     if test -f "$BUGATE_VENDOR_DIR/bugate.lock.json" \
       && test -x "$BUGATE_VENDOR_DIR/bin/bugate-update"; then
       BUGATE_ROUTE=locked-in-repo-update
     elif test -e "$BUGATE_VENDOR_DIR" || test -L "$BUGATE_VENDOR_DIR"; then
       BUGATE_ROUTE=external-bootstrap-candidate
     else
       BUGATE_ROUTE=fresh-install
     fi
     printf 'BUGATE_ROUTE=%s\n' "$BUGATE_ROUTE"
     ```

   - `external-bootstrap-candidate` 此时还不能认定为 v0.3.x 或 pre-lock
     v0.4.x。必须由 v0.4.2 或更高的外部 updater 通过 exact supported
     legacy/pre-lock manifest 识别，或诊断不一致的 lock/launcher state；未知、
     混合、locally modified 或不完整 lock-based layout 一律 `NO-GO`。不能只凭
     版本字符串选择仓内路径。

2. **仅在需要时于 SUT 仓外获取 BUGate kit**
   - `locked-in-repo-update` 跳过第 2–3 步：vendored updater 会解析显式指定的
     target release，或接受离线 archive/checksum 对。
   - 首次安装与 legacy bootstrap 需要正式已解包 v0.4.2 或更高 release。
   - 如果 `BUGATE_ENGINE_DIR` 可用，继续使用。
   - 否则执行等价步骤：

     ```bash
     BUGATE_VERSION="${BUGATE_VERSION:-0.4.3}"
     BUGATE_TMP="$(mktemp -d)"
     BUGATE_RELEASE="https://github.com/ZhangLiangchen/BUGate/releases/download/v${BUGATE_VERSION}"
     BUGATE_SUMS="bugate-${BUGATE_VERSION}.SHA256SUMS"
     if curl -fL -o "$BUGATE_TMP/bugate-${BUGATE_VERSION}.tar.gz" \
       "$BUGATE_RELEASE/bugate-${BUGATE_VERSION}.tar.gz"; then
       BUGATE_ARCHIVE="bugate-${BUGATE_VERSION}.tar.gz"
     elif curl -fL -o "$BUGATE_TMP/bugate-${BUGATE_VERSION}.zip" \
       "$BUGATE_RELEASE/bugate-${BUGATE_VERSION}.zip"; then
       BUGATE_ARCHIVE="bugate-${BUGATE_VERSION}.zip"
     else
       echo "BUGate release v${BUGATE_VERSION} 无法下载；请向用户索取 BUGATE_ENGINE_DIR 或有效版本。" >&2
       exit 2
     fi
     curl -fL -o "$BUGATE_TMP/$BUGATE_SUMS" "$BUGATE_RELEASE/$BUGATE_SUMS"
     if ! grep "${BUGATE_ARCHIVE}$" "$BUGATE_TMP/$BUGATE_SUMS" \
       | sed 's#  dist/#  #' \
       | (cd "$BUGATE_TMP" && shasum -a 256 -c -); then
       echo "BUGate archive checksum 校验失败；不得解压或安装。" >&2
       exit 2
     fi
     case "$BUGATE_ARCHIVE" in
       *.tar.gz) tar -xzf "$BUGATE_TMP/$BUGATE_ARCHIVE" -C "$BUGATE_TMP" ;;
       *.zip) unzip -q "$BUGATE_TMP/$BUGATE_ARCHIVE" -d "$BUGATE_TMP" ;;
     esac
     BUGATE_ENGINE_DIR="$BUGATE_TMP/bugate-${BUGATE_VERSION}"
     ```

   - 验证 engine 存在：

     ```bash
     test -f "$BUGATE_ENGINE_DIR/scripts/bugate_init.py"
     test -f "$BUGATE_ENGINE_DIR/scripts/bugate_update.py"
     test -f "$BUGATE_ENGINE_DIR/scripts/role_governance.py"
     test -f "$BUGATE_ENGINE_DIR/scripts/check_role_evidence.py"
     test -x "$BUGATE_ENGINE_DIR/bin/bugate-role"
     test -x "$BUGATE_ENGINE_DIR/bin/bugate-update"
     test -f "$BUGATE_ENGINE_DIR/.shared/skills/bugate/SKILL.md"
     ```

   - legacy bootstrap 还必须存在
     `$BUGATE_ENGINE_DIR/.bugate-release/manifest.json`；缺失说明它不是正式
     bootstrap source。

3. **首次安装或 bootstrap 前验证下载的 engine**

   ```bash
   cd "$BUGATE_ENGINE_DIR"
   python3 -m py_compile scripts/*.py
   python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code
   cd -
   ```

4. **只执行一个 install/update 路径**

   **仅首次安装**（`BUGATE_ROUTE=fresh-install`）：

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

   若 vendor path 在 preview 与 apply 之间出现，立即停止。installer 只负责首次
   安装，必须在任何 target 或 machine-state 写入前 fail；它不是 update/repair
   命令。

   **受支持 v0.3.x/pre-lock bootstrap**
   （`BUGATE_ROUTE=external-bootstrap-candidate`）：

   ```bash
   SUT_REPO="$(pwd)"
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   BOOTSTRAP="$BUGATE_ENGINE_DIR/scripts/bugate_update.py"
   python3 "$BOOTSTRAP" status "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR"
   python3 "$BOOTSTRAP" plan "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR"
   # 完整复核，除非 plan 输出 Decision: GO，否则停止。
   python3 "$BOOTSTRAP" apply "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR"
   python3 "$BOOTSTRAP" verify "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR"
   ```

   这是一条用于 v0.4.2 或更高 release 持续携带的 exact supported v0.3.x 与 pre-lock layout
   的一次性桥接。`status`/`plan` 为 `NO-GO` 时，不得手工复制文件、运行 importer
   或猜测版本。

   **Lock+launcher 仓内更新**（`BUGATE_ROUTE=locked-in-repo-update`）：

   ```bash
   BUGATE_VERSION="${BUGATE_VERSION:-0.4.3}"
   BUGATE_VENDOR_DIR="${BUGATE_VENDOR_DIR:-.bugate}"
   UPDATER="$BUGATE_VENDOR_DIR/bin/bugate-update"
   "$UPDATER" status
   "$UPDATER" plan --to "$BUGATE_VERSION"
   # 完整复核，除非 plan 输出 Decision: GO，否则停止。
   "$UPDATER" apply --to "$BUGATE_VERSION"
   "$UPDATER" verify
   ```

   不存在隐式 `latest`。`status`、`plan`、`verify` 都只读；`plan` 与
   `apply --dry-run` 对目标零持久写入。若 `status` 报告 interrupted recovery，
   不得自行清理：先报告，再使用 updater 经复核的 mutating `apply` recovery
   path。

   两种 updater 入口的确定性离线操作都必须把 archive 与 checksum asset 同时
   传给 plan 和 apply：

   ```bash
   "$UPDATER" plan \
     --archive /outside/bugate-0.4.3.tar.gz \
     --checksums /outside/bugate-0.4.3.SHA256SUMS
   "$UPDATER" apply \
     --archive /outside/bugate-0.4.3.tar.gz \
     --checksums /outside/bugate-0.4.3.SHA256SUMS
   "$UPDATER" verify
   ```

   bootstrap 直接调用外部脚本并传相同资产，不要在 SUT 仓创建 wrapper：

   ```bash
   python3 "$BOOTSTRAP" plan "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR" \
     --archive /outside/bugate-0.4.3.tar.gz \
     --checksums /outside/bugate-0.4.3.SHA256SUMS
   python3 "$BOOTSTRAP" apply "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR" \
     --archive /outside/bugate-0.4.3.tar.gz \
     --checksums /outside/bugate-0.4.3.SHA256SUMS
   python3 "$BOOTSTRAP" verify "$SUT_REPO" --vendor-dir "$BUGATE_VENDOR_DIR"
   ```

   checksum 缺失、歧义或不匹配会在 target 写入前被拒绝。

   rollback 必须显式指定 transaction，不能把 warning 当作自动回滚理由。执行前，
   在 SUT 仓外保留或取得一份已验证且已解包的 v0.4.2 或更高 release，并让
   `BOOTSTRAP` 指向其中 updater；exact rollback 移除 vendored launcher 后仍需此
   路径：

   ```bash
   BOOTSTRAP=/outside/bugate-0.4.3/scripts/bugate_update.py
   "$BUGATE_VENDOR_DIR/bin/bugate-update" rollback \
     --transaction <32-hex-transaction-id>
   if test -f "$BUGATE_VENDOR_DIR/bugate.lock.json" \
     && test -x "$BUGATE_VENDOR_DIR/bin/bugate-update"; then
     "$BUGATE_VENDOR_DIR/bin/bugate-update" verify
   else
     python3 "$BOOTSTRAP" verify . --vendor-dir "$BUGATE_VENDOR_DIR"
   fi
   ```

   只能使用 committed `apply` 输出的 id。rollback 只恢复 engine transaction；
   不会撤销单独评审的 profile 变更。第一笔 v0.4.2 updater transaction 可能精确
   恢复 v0.3.x 或 pre-lock v0.4.0/v0.4.1，包括删除 lock 与 launcher；这不是
   rollback 失败。若 rollback 在 launcher 变化后中断，用 `python3 "$BOOTSTRAP"
   status . --vendor-dir "$BUGATE_VENDOR_DIR"` 诊断；需要 recovery 时通过
   `$BOOTSTRAP` 重试同一个 exact rollback，最后执行外部 `verify`。禁止复制
   launcher 回去或手改 transaction state。

   managed local change、未知/重复 hook identity 或 shape、type/permission drift、
   critical file 缺失与混合 legacy fingerprint 都是冲突，会令 plan `NO-GO`。
   不存在宽泛 `--force`。保留全部文件，报告 expected/actual 细节，并让用户处理
   指定 path；无关 dirty file 只报告 warning。

5. **把 SUT profile 作为独立动作处理**
   - 只有首次安装才按下述步骤基于证据激活新 profile。
   - bootstrap/update 的 engine transaction 不得编辑 `bugate.config.yaml` 或
     `bugate.profile.yaml`。保留当前 profile，并报告 updater 的
     `migration_available` 或会阻塞的 `migration_required`。proposed migration
     必须是独立的人审 diff 与独立可回滚 commit；绝不能隐式把
     `role_governance.mode: off` 改成 `required`。
   - 打开 `bugate.profile.yaml`。
   - 保留 `memory.namespace`。
   - 如果测试布局清晰，用一个或多个带命名捕获 `(?P<uc>...)` 的 regex 更新
     `guarded_path_regex`。
   - 如果布局与 scaffold 示例不符（不同语言、命名约定或 per-UC 单元），读
     vendored 适配技能——
     `$BUGATE_VENDOR_DIR/.shared/skills/bugate-import/SKILL.md`——并按其
     适配流程执行（匹配规则、四种框架形态的实证绑定样例、强制的负向/正向
     验证）。
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
   test -f "$BUGATE_VENDOR_DIR/scripts/role_governance.py"
   test -f "$BUGATE_VENDOR_DIR/scripts/check_role_evidence.py"
   test -x "$BUGATE_VENDOR_DIR/bin/bugate-role"
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
   memory = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
   print("memory.namespace=", memory.get("namespace") or cfg.get("namespace"))
   print("role_governance=", cfg.get("role_governance"))
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
     `BUGATE_MEMORY_NO_INSTALL=1` 跳过自动安装，按 vendored 的
     `$BUGATE_VENDOR_DIR/docs/SETUP-OPTIONAL.md` §2 手动/离线安装——这是兜底而非推荐路线；
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
   - 说明实际执行的 route：`fresh-install`、`legacy-bootstrap` 或
     `locked-in-repo-update`；给出 installed version、最终 `verify` decision，以及
     transaction id/rollback availability。
   - 列出 SUT 仓中所有变更文件/目录。
   - 首次安装时，列出应该提交的精确文件：
     `bugate.config.yaml`、`bugate.profile.yaml`、`$BUGATE_VENDOR_DIR/`、
     `.claude/settings.json`、`.codex/hooks.json`、`.claude/skills/`、
     `.agents/skills/`、`.codex/skills/`、`.codex/agents/`、`docs/usecases/`
     以及 `.gitignore` 中的 BUGate block。
   - 更新时，只列出 transaction 报告的 manifest-owned change 与 exact BUGate
     hook entries。确认 SUT-owned config、profile、tests、artifacts、evidence、
     hooks、Memory data 与无关 dirty files 均未变化。
   - 说明 `guarded_path_regex` 是否已激活。
   - 说明 active `role_governance.mode` 与 `memory_mode`。legacy/off profile
     保持兼容，但没有激活 Wave 7 生命周期门。
   - 说明 Memory Bus 状态。
   - 首次安装时，报告新加入的 Codex hook 文件需要 re-trust，且所有新 hook 都
     要求新 session。更新时，报告 updater 结果中的 `codex_hook_hash_changed` 与
     `new_session_required`。**只有** Codex hook bytes/hash 变化时才对 Codex
     Desktop re-trust；same-byte no-op 不得要求重复 re-trust。任何 hook 变化都
     必须以该 SUT 仓为根启动新 Claude/Codex session，之后新 runtime surface
     才生效；re-trust 本身不是 reload。plugin 通道可能还要 reload/update plugin。
   - 把 vendored 使用指导交给用户作为日常手册：
     `$BUGATE_VENDOR_DIR/.shared/skills/bugate-import/references/using-bugate.zh-CN.md`
     （English: 同目录 `using-bugate.md`）——以本仓为会话项目根打开，然后在
     独立 designer / implementer / reviewer session 中推进新需求（先 `--init`，
     再 pre-code `--auto`，之后是人工 03B 接受、显式角色 receipts、受治理实现与
     post-run 闭环）。导入后的全部指导都整合在这一个技能之下。
   - 除非用户明确要求，不要 stage、commit 或 push。

### 附录：按需激活可选波次（Wave 7 / Wave 8）

> importer 会把实战运维手册 vendor 到 bugate-import 技能内——
> `$BUGATE_VENDOR_DIR/.shared/skills/bugate-import/references/field-guide.md`
> —— 导入完成后先读它：
> 双端调度诊断阶梯与代理注入、`--auto` 的 03b 覆写语义、post-run 04/05
> 覆写 SOP、UC 复制卫生、以及下述两个波次的完整激活配方都在里面。

这两个波次默认休眠，是配置开关而非缺陷；证据就绪后在 SUT profile 里开启
（profile 脚手架里已含注释掉的示例块）。

- **Wave 7 生命周期治理**：`agent_roles` 与 `role_governance` 互补，不是别名。
  `agent_roles` 继续作为独立的路径读写策略（裸列表 = 读写皆禁，`read:` /
  `write:` 可分别限定）；新状态机负责 phase、handoff、acceptance 与 evidence。
  要启用完整 fail-closed 治理，加入：

  ```yaml
  agent_roles:
    implementer:            # 写测试的角色不许接触业务源码/接口 dump
      - "^docs/raw/source_code/.*"
    designer:
      write:
        - "^tests/.*"       # 设计角色不许直接写测试代码

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
        allowed_roles:
          - designer
      implementation:
        allowed_roles:
          - implementer
        requires_handoff_from:
          - designer
      post_run:
        allowed_roles:
          - reviewer
        requires_handoff_from:
          - implementer
  ```

  不新增配置块或设置 `role_governance.mode: off` 都保持 v0.3.x 行为；
  `agent_roles` 仍可单独工作。启用 `required` 后，角色未设置/错误或缺 required
  session ID 都会 fail-closed。历史 passed UC 不会获得伪造证据：Layer 4 前必须
  新建 human acceptance、designer handoff、implementer acceptance；post-run
  前还需要 implementer handoff 与 reviewer acceptance。

  升级已有导入仓只能使用 v0.4.2 或更高外部 bootstrap updater（v0.3.x 或 pre-lock
  v0.4.0/v0.4.1），或在 lock+launcher 同时存在时使用 vendored
  `bugate-update`，按第 4 步执行 status → plan → 人工复核后的 apply →
  verify。`bugate_init.py` 仅首次安装。engine transaction 保留 profile，因此
  启用 Wave 7 仍是独立的人审 profile 变更。完成该独立决定后，启动三个独立
  session；不要试图从 SessionStart hook 设置父进程角色：

  ```bash
  "$BUGATE_VENDOR_DIR/bin/bugate-role" run --role designer -- codex
  "$BUGATE_VENDOR_DIR/bin/bugate-role" run --role implementer -- claude
  "$BUGATE_VENDOR_DIR/bin/bugate-role" run --role reviewer -- codex
  ```

  Hook 进程不能向父进程 export，已运行的 Desktop 进程也不会继承后续 shell 环境
  变更；每个 Desktop/CLI 角色都要从目标环境重新启动。任何 hook 变化都要求新
  session；只有 `.codex/hooks.json` bytes 变化时，Codex Desktop 才额外需要
  re-trust（updater 会报告该条件）。

  日常转换顺序：designer 把 `--init` 与 pre-code `--auto` 作为**两个命令**运行；
  人类评审 03B 并显式设为 `gate_status: passed`；designer 用
  `bugate-role approve`（仅记录既有决定）并
  `handoff --phase pre_code --to implementer`；新 implementer 用 exact
  `memory.memory_id` accept，完成写入/测试后至少带一个 `--implementation-file`
  handoff；新 reviewer accept 后运行 post-run，再携带 04/05 与执行证据 complete。
  人工接受 03B 后不得再运行 `--auto`，应直接 `approve` / `handoff`。

  每次普通编辑只检查本地 receipt/profile/artifact/implementation hash。Memory
  故障只阻塞下一次 strict 角色转换，并且不会生成解锁 receipt；恢复服务后幂等
  重试。profile/pre-code drift 需要新 human/designer generation；implementation
  drift 需要新 implementer handoff/reviewer acceptance。直接修改
  `00_role_evidence/**` 会被拒绝；禁止删除 evidence 来 reset，receipt 被篡改时
  先从可信来源恢复，再追加 superseding transition。

  注意：读隔离只对 hook 能看到的 `Read` 工具生效（importer v0.3.2+ 已把角色
  守卫单独接到 `Read|Edit|Write` matcher；写形守卫 `check_bugate` 绝不能挂到
  `Read`，它不辨别 action、会把读也拦下）。shell 级读取（cat/grep）不在物理
  守卫范围内；任意重定向/外部编辑器写入也无法被 hook 拦截。受支持的 agent
  tool、orchestrator 与 Core mutator 会强制状态机；更强文件系统隔离属于 managed
  runner。`approved_by`、环境角色、本地 hash 与 Memory anchor 是可审计声明，
  不是不可抵赖的人类身份认证。强身份需要独立 OS 账号、容器、managed runner
  或按角色 credential。
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
