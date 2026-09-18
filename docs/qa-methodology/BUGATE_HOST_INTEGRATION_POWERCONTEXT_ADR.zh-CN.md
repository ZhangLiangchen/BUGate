# ADR-BUGATE-009 — PowerContext-style Host Integration Architecture

- **状态：** Accepted
- **日期：** 2026-09-18
- **目标版本：** BUGate 2.0
- **参考工程：** [OceanBase PowerContext](https://github.com/oceanbase/powercontext)
- **关联：**
  - [BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md](BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md)
  - [ADR-BUGATE-008 — Context Runtime](BUGATE_CONTEXT_RUNTIME_ADR.zh-CN.md)
  - [ADR-BUGATE-007 — Stateless Core / TestTaskWorkspace](BUGATE_STATELESS_WORKSPACE_ADR.zh-CN.md)

## 1. 决策

BUGate 2.0 的多 Agent / Harness 接入方式正式采用 PowerContext 已验证的工程模式：

```text
Canonical Core Contract
        |
        v
Prepared Output
        |
        v
Host-local Projection / Adapter
        |
        v
Host-native lifecycle
```

BUGate 不再为 Claude Code、Codex、Pi、DeepSeek Harness 分别维护方法论副本。

规范事实始终来自：

```text
protocol/
engine/
TestTaskWorkspace
```

Host Adapter 只负责：

- 宿主发现；
- 安装 / 升级；
- Workspace / ProtocolBinding 发现；
- 调用 BUGate prepare；
- 验证 PreparedProtocolContext；
- 注入宿主上下文；
- 暴露显式 BUGate 操作；
- diagnostics / rollback。

Host Adapter **不得重新解释、筛选、改写 BUGate 方法论语义**。

---

## 2. 直接吸收的 PowerContext 工程原则

BUGate 2.0 直接采用以下模式：

1. **First-class Host Catalog**
   - 用机器可读 catalog 维护正式支持的 Agent Host；
   - catalog 不依赖 PATH 自动发现来决定“支持谁”。

2. **Capability Manifest**
   - 不强迫所有 Host 使用同名 hook；
   - 规定语义能力，例如 protocol injection / compaction recovery / subagent inheritance；
   - 每个 Host 声明自己通过什么 native surface 实现。

3. **Prepared Output owned by Core**
   - BUGate Engine 生成最终 `PreparedProtocolContext`；
   - Adapter 只能 validate + inject；
   - Adapter 不得自行选择 MethodSpec、重算 QualityPosture 或裁剪 findings。

4. **Host-local Projection**
   - `CLAUDE.md`、`AGENTS.md`、`SKILL.md`、plugin manifest、hook config 都是生成/安装的 projection；
   - 不是 Protocol authority。

5. **Automatic Hydration + Explicit Operations**
   - lifecycle 自动注入保证 Agent 不遗忘 Protocol；
   - CLI / Skill / tool 显式操作负责 status / assess / explain 等交互。

6. **Self-contained Adapters**
   - Claude / Codex / Pi / DSH Adapter 可以各自包含宿主 glue；
   - 不为了 DRY 过早共享复杂 host runtime；
   - 统一行为由 Conformance Suite 保证。

7. **Unified Setup**
   - `bugate setup <host>`
   - `bugate setup select --host ...`

8. **Unified Doctor**
   - `bugate doctor <host>`
   - `bugate doctor integrations`

9. **Version / Projection Integrity**
   - CLI / Adapter / Projection / ProtocolBinding 必须可核对版本与 digest；
   - stale Skill / stale plugin 必须被 diagnostics 识别。

10. **Per-host Failure Isolation**
    - multi-host setup 中，一个 Host 安装失败不能阻断其它选中 Host；
    - 最终退出码反映是否存在 selected-host failure。

---

## 3. PreparedProtocolContext

BUGate 2.0 将 Protocol Context Capsule 正式提升为机器对象：

```yaml
apiVersion: bugate.io/v2
kind: PreparedProtocolContext

protocol:
  id: bugate
  version: 2.0.0
  digest: sha256:...

workspace:
  task_id: REQ-001
  workspace_digest: sha256:...

quality_posture:
  business_understanding: satisfactory
  testability: needs_improvement

active_concerns:
  - code: MISSING_ORACLE
    subject: P-021

render:
  media_type: text/markdown
  bytes: 3240
  content: |
    ...
```

它是：

> **Host-neutral, injection-ready, final methodology context.**

Adapter 对成功返回的对象：

- 校验 schema；
- 校验 protocol digest；
- 校验 workspace identity；
- 检查 size / media type；
- 原样注入 `render.content`。

Adapter 不得：

- 再次搜索 Protocol；
- 替换 MethodSpec；
- 删除 finding；
- 自行重排 quality priority；
- 改写 Assessment；
- 把历史 Context Runtime 内容混入 Protocol authority。

---

## 4. Protocol Prepare

建议正式 CLI：

```bash
bugate protocol prepare \
  --task test-task/REQ-001 \
  --max-bytes 8000 \
  --json
```

核心尽量 Host-neutral。

`--host` 如果未来存在，只允许影响：

- transport envelope；
- max size capability；
- rendering compatibility hint。

不得影响：

- Method semantics；
- QualityPosture；
- Assessment findings；
- Protocol rules。

---

## 5. Host Catalog

BUGate 维护：

```text
adapters/catalog.yaml
```

首批 first-class hosts：

```text
claude-code
codex
pi
deepseek-harness
```

状态：

- Claude Code：BUGate 2.0 first implementation target；
- Codex：BUGate 2.0 first implementation target；
- Pi：reserved / HyperTest target；
- DeepSeek Harness：reserved / HyperTest target。

Catalog 的作用：

- setup selector；
- doctor integrations；
- documentation generation；
- conformance matrix；
- release validation。

---

## 6. Capability，不绑定 Hook 名字

BUGate 定义语义能力，例如：

```text
protocol_discovery
workspace_discovery
protocol_binding
automatic_hydration
session_rehydration
compaction_rehydration
subagent_inheritance
skill_projection
explicit_assessment
diagnostics
rollback
context_runtime_hydration
```

Host Adapter 决定用：

- hook；
- plugin callback；
- before-agent-start；
- before-model-step；
- package middleware；
- MCP；
- native SDK extension。

因此：

> BUGate requires hydration semantics, not a specific hook API.

---

## 7. Projection Model

Host-local 文件必须视为 projection：

### Claude Code

```text
CLAUDE.md fragment
.claude/skills/bugate/SKILL.md
plugin / hooks
```

### Codex

```text
AGENTS.md fragment
.agents/skills/bugate/SKILL.md
plugin / hooks
```

### Pi

```text
package / extension
generated skill projection
lifecycle glue
```

### DeepSeek Harness

```text
plugin
generated skill projection
lifecycle glue
```

所有 projection 必须可从 canonical BUGate assets 重建。

禁止：

> 在 Host projection 中出现只有该 Host 才拥有的 Methodology rule。

---

## 8. Setup UX

采用：

```bash
bugate setup claude-code
bugate setup codex
bugate setup pi
bugate setup deepseek-harness

bugate setup select \
  --host claude-code \
  --host codex
```

setup 必须：

1. 检查 Host CLI / environment；
2. 读取 catalog / manifest；
3. 生成 installation plan；
4. 展示将 CREATE / PATCH / INSTALL / BIND 的内容；
5. 安装 projection；
6. verify；
7. 输出 rollback 信息。

重复 setup 必须幂等。

---

## 9. Doctor UX

采用：

```bash
bugate doctor
bugate doctor claude-code
bugate doctor codex
bugate doctor pi
bugate doctor deepseek-harness
bugate doctor integrations
```

`doctor integrations` 返回统一矩阵：

```text
claude-code: present  cli=ok plugin=ok projection=ok binding=ok hydration=ok
codex:       present  cli=ok plugin=ok projection=ok binding=ok hydration=ok
pi:          missing  cli=failed package=skipped
deepseek:    missing  cli=failed plugin=skipped
```

原则：

- 未安装 Host = skipped / missing，不使全局 doctor 失败；
- 已安装 Host 但 integration broken = failure；
- `--json` 输出稳定 schema。

---

## 10. Adapter Conformance Suite

所有 first-class Host 必须通过同一组行为契约：

```text
HC-01 install is idempotent
HC-02 canonical Protocol is discoverable
HC-03 TestTaskWorkspace identity is stable
HC-04 exact ProtocolBinding is preserved
HC-05 PreparedProtocolContext validates
HC-06 context is injected before relevant reasoning
HC-07 long session does not lose Protocol
HC-08 compaction/restart rehydrates when host supports lifecycle recovery
HC-09 child Agent inherits Binding when host supports child agents
HC-10 protocol mismatch fails visible
HC-11 stale projection is detected
HC-12 explicit bugate assess is reachable
HC-13 Adapter cannot change quality semantics
HC-14 rollback removes only BUGate-owned projection
HC-15 Context Runtime Context Pack does not override Protocol authority
```

Host capability manifest 可以把某些 lifecycle 特性标记为：

```text
required
supported
unsupported-with-rationale
reserved
```

但不能静默缺失。

---

## 11. 与 PowerContext 的关系

本 ADR “照搬”的是 PowerContext 的 **integration architecture pattern**：

- first-class host catalog；
- capability manifest；
- self-contained host integration；
- unified setup selector；
- integration doctor matrix；
- installation failure isolation；
- provider/core-owned prepared output；
- projection integrity；
- common conformance tests。

BUGate 不复制 PowerContext 的 Memory domain code，也不依赖其 CLI implementation。

PowerContext 使用 Apache-2.0，但 BUGate 仍优先独立实现自己的 Protocol-domain adapter code，避免把 Context-specific 逻辑带入 Testing Protocol。

---

## 12. 目标目录

```text
adapters/
  catalog.yaml
  CONFORMANCE.md
  schema/
    host-manifest.schema.json

  claude-code/
    manifest.yaml
    README.md
    plugin/
    tests/

  codex/
    manifest.yaml
    README.md
    plugin/
    tests/

  pi/
    manifest.yaml
    README.md
    package/
    tests/

  deepseek-harness/
    manifest.yaml
    README.md
    plugin/
    tests/

views/
  README.md
  skills/
    bugate/
      # generated / canonical projection assets

protocol/
engine/
```

---

## 13. 实施顺序

### HI-0 — Catalog / Manifest

建立 catalog、schema、四 Host manifest。

### HI-1 — PreparedProtocolContext

定义 schema 与 `bugate protocol prepare`。

### HI-2 — Projection Compiler

从 canonical Protocol / View 生成 Skill / Bootstrap projection。

### HI-3 — Claude Code Adapter

按 host-native lifecycle 安装、hydrate、doctor、conformance。

### HI-4 — Codex Adapter

同上。

### HI-5 — setup select / doctor integrations

实现 multi-host UX 与 failure isolation。

### HI-6 — Pi / DSH

等 HyperTest harness contract 稳定后实现。

---

## 14. 最终裁决

BUGate 2.0 不再问：

> “怎样给 Claude 写一套 BUGate？”

而是：

> “怎样让任何 Host 消费同一个 PreparedProtocolContext？”

最终模型：

```text
                    BUGate Protocol
                          |
                          v
                    Protocol Engine
                          |
                          v
               PreparedProtocolContext
                          |
            +-------------+-------------+
            |             |             |
            v             v             v
       Claude Code       Codex           Pi
            |                           /
            +---- DeepSeek Harness ----+
```

这套 Host Integration Architecture 作为 BUGate 2.0 的正式实现方向。
