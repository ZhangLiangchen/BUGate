# BUGate 升级 Prompt

[English](UPDATE_PROMPT.md) | [简体中文](UPDATE_PROMPT.zh-CN.md)

> 在已经导入 BUGate 的 SUT 自动化测试仓作为项目根打开时，把这份 prompt 粘给
> Codex 或 Claude Code。它只升级 BUGate-owned engine projection，不迁移或修改
> 真实 SUT。

## Agent 指令

使用 `bugate-update` skill 升级当前已有的 imported BUGate 安装。执行前读取仓内
`AGENTS.md`/`CLAUDE.md` 和 vendored `bugate-update` skill。

### 输入与授权

- `SUT_REPO`：当前包含 `bugate.config.yaml` 的 imported 自动化测试工作区。
- `BUGATE_VENDOR_DIR`：使用已有安装证据确定的 vendor 路径；只有标准布局有证据时
  才默认 `.bugate`。
- `BUGATE_TARGET_VERSION`：要求用户给出精确语义版本。不得隐式解析 `latest`。
- `BUGATE_SOURCE`：可信 remote release，或仓外保存的 archive 与匹配
  `SHA256SUMS`。
- `AUTHORIZATION_SCOPE`：默认为 `PLAN_ONLY`（只读规划）。

`PLAN_ONLY` 只授权只读发现、updater `status`、updater `plan` 和报告。它不授权
`apply`、rollback、profile migration、lineage action、commit、push、tag、Release
发布或真实 SUT surface 修改。

只有用户对 exact target、source 和已审查且为 `Decision: GO` 的 plan 作出明确批准
后，才进入 `APPLY_APPROVED`。若当前请求已经提供同等精确的 apply 授权，apply 前必须
说明授权依据。不得从“检查、评估或规划升级”的请求推导 apply 权限。

### 必须执行的流程

1. **只读检查**

   ```sh
   cd "$SUT_REPO"
   pwd
   git status --short --branch
   ```

   报告全部 dirty 项并保持其 bytes 不变。不得 reset、checkout、clean、revert、
   overwrite、stage、commit 或 push。

2. **同时依据 installed lock 与 launcher 分类**

   ```sh
   if test -f "$BUGATE_VENDOR_DIR/bugate.lock.json" \
     && test -x "$BUGATE_VENDOR_DIR/bin/bugate-update"; then
     BUGATE_ROUTE=locked-in-repo-update
   else
     BUGATE_ROUTE=external-bootstrap-candidate
   fi
   ```

   - `locked-in-repo-update`：使用 vendored launcher。
   - `external-bootstrap-candidate`：精确匹配受支持 v0.3.x 或 pre-lock
     v0.4.0/v0.4.1 的安装，使用 SUT 仓外、已独立校验 checksum 并解压的 v0.4.2
     或更高 target release 中的 `scripts/bugate_update.py`。
   - 未知、混合、残缺或 managed path 本地修改状态一律 `NO-GO`。
     不得运行 `bugate_init.py`；它只用于 fresh install。

3. **执行 status 与 plan**

   Locked 路径：

   ```sh
   "$BUGATE_VENDOR_DIR/bin/bugate-update" status
   "$BUGATE_VENDOR_DIR/bin/bugate-update" plan \
     --to "$BUGATE_TARGET_VERSION" --json
   ```

   External bootstrap 路径：

   ```sh
   python3 "$BOOTSTRAP" status "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR"
   python3 "$BOOTSTRAP" plan "$SUT_REPO" \
     --vendor-dir "$BUGATE_VENDOR_DIR" \
     --to "$BUGATE_TARGET_VERSION" --json
   ```

   Archive mode 下，plan 与 apply 必须传入同一组 `--archive` 和 `--checksums`。
   JSON plan 必须保存在仓外。`plan` 与 `apply --dry-run` 对 target 必须零持久写入。

4. **报告并停在授权门**

   报告：

   - from/to version、source kind、release/manifest/archive digest；
   - 所有 managed add、update、安全 delete、conflict、local modification、
     type 与 permission change；
   - recovery state 与 transaction-history capacity；
   - profile `migration_required`/`migration_available`；
   - hook hash、Codex re-trust 与 new-session 影响；
   - 精确 rollback 路径和全部 `NO-GO` reason。

   Plan 不是 `Decision: GO` 必须停止。处于 `PLAN_ONLY` 时，即使 GO 也必须停止并请求
   明确批准。

5. **只在 `APPLY_APPROVED` 下 apply**

   Mutation 前立即重新检查 repo status 并重建/校验 plan。使用用户批准的同一 exact
   target、source、archive/checksum pair 和 saved plan。不得使用宽泛 `--force`。

   ```sh
   "$BUGATE_VENDOR_DIR/bin/bugate-update" apply \
     --to "$BUGATE_TARGET_VERSION" --plan "$BUGATE_PLAN"
   "$BUGATE_VENDOR_DIR/bin/bugate-update" verify
   ```

   Vendored launcher 不具权威性时，使用对应的 external-bootstrap 命令形式。

6. **验证**

   Updater `verify` 必须为 GO、lock-based、无 drift 且不等待 recovery。随后执行：

   ```sh
   python3 "$BUGATE_VENDOR_DIR/.shared/skills/bugate-full-check/scripts/run_full_check.py" \
     --mode smoke
   git status --short
   git diff
   ```

   证明 profile/config、SUT tests、use-case artifacts、`00_role_evidence/**`、
   Memory namespace/data、machine lineage registry、SUT-owned hooks/skills 和无关 dirty
   文件均未被修改。

7. **保持后续权限独立**

   - 报告 profile compatibility；未经独立授权不得编辑 profile。
   - Engine update 后报告每个 governed UC 的 lineage status；未经独立授权不得运行
     `lineage-init`、`lineage-adopt` 或 `recover`。
   - 未经独立授权不得 commit，且不得 push。
   - Hook 发生变化必须新建 agent session；只有 Codex hook bytes/hash 变化时才需要
     Codex re-trust。

8. **Rollback 与中断**

   不得删除或手改 updater state。Rollback 需要独立授权和 exact committed
   transaction ID。若 rollback 移除了 vendored launcher 与 lock，使用保留的外部
   updater 验证：

   ```sh
   python3 "$BOOTSTRAP" verify . --vendor-dir "$BUGATE_VENDOR_DIR"
   ```

### 完成报告

分别报告 route、target/source、plan decision、实际使用的授权、transaction ID、
verify/smoke 结果、精确 managed diff、保持不变的 SUT-owned surfaces、hook/session
动作、rollback 路径，以及 profile/lineage 状态。不得把 engine update 成功描述为
governance migration 成功。
