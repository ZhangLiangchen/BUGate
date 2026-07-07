# 为 BUGate 贡献

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

BUGate core 是一个 **SUT-neutral、零依赖**（纯 Python 标准库）的黑盒测试门引擎。所有贡献都必须保持它可移植到任何 System Under Test（SUT）。本指南说明 de-SUT 契约、PR 前应运行的本地检查、文件放置规则与 PR 约定。

先读：[`AGENTS.md`](AGENTS.md)（Core Rules）与 [`docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md`](docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md)（ADR-BUGATE-001，三层架构）。启动与验证命令见 [`INIT.md`](INIT.md) / [`INIT.zh-CN.md`](INIT.zh-CN.md)。

---

## 1. de-SUT 契约

Core 只保存**方法、artifact 模板、结构化 gate 标准、hook 机制与 adapter 布局**，不保存任何绑定单一产品的内容。按 ADR 的层次划分：

| 层 | 所在位置 | 保存内容 |
|---|---|---|
| **Core** | 本仓库 | 中立方法、模板、gates、hooks、adapters |
| **SUT Profile** | 导入后的 SUT 测试仓 | 路径、命令、证据来源、受保护路径、资源策略、runtime kind |
| **SUT Product Runtime** | 产品侧 runtime / repository | 源码、API 文档、fixtures、tests、secrets、live evidence |

具体规则（AGENTS.md Core Rules 1–2 与 5）：

- 永远不要把 SUT source code、产品 API snapshots、环境 secrets、credentials、generated caches 或 project-specific fixtures 加入 core。
- SUT paths、resource policies、environment names、auth rules 与 tool commands 放进 **SUT profile**，不要放进 core。
- 如果一个改动需要 SUT-specific facts，在 profile 边界停下；在导入后的 SUT 测试仓中增加 profile key，而不是在 core 中编造产品细节。

### de-SUT guard（identity-seepage 防御）

`scripts/check_no_sut_terms.py` 保护 kit 的**可复用性**：会被 vendor 到被治理 SUT 仓的 engine subtree 不能携带只对某一个 SUT 为真的事实 —— *阻断渗漏，而不是阻断所有提及*（CHARTER Amendment A1，[ADR-BUGATE-004](docs/qa-methodology/BUGATE_DESUT_CALIBRATION_ADR.md)）。纪律分三层：

1. **行为性 SUT 事实**（defaults、endpoints、resources、credentials、environment names）—— 永远不能进入 core，没有豁免。guard 的内置通用 hygiene patterns（本机用户路径、credential/key 形态）只覆盖机器可检测的一部分；其余由 review discipline 负责。
2. **身份词**（product/system/account/person names）—— 默认禁止进入 kit tree，但词表由 profile 提供（`sut_identity_terms`）或通过 `--terms-file` 给出；engine 不内置任何产品词汇。叙事/溯源提及可通过显式 marker 合法化（见下）。
3. **行业/领域词汇** —— core 不防御；如果某个 SUT profile 需要保护某个领域词，由 profile 自己列出。

扫描面锚定在 **engine root 的 kit subtree**（`scripts/`、`bin/`、`.shared/skills/`；当通过 `CHARTER.md` sentinel 识别为 upstream repo 时，也包括 docs/ 与 root 文件）。被治理 workspace 自己的文件永远不是扫描面。用于 upstream regression 的 legacy SUT 词汇只放在 `tests/fixtures/legacy-sut-terms.txt`，绝不进入 engine source。

> 开发 BUGate 本身（维护者）时，core 迭代保持纯净：用模板 gates 与临时 fixtures 验证，**不要把 SUT 挂进本仓**（CHARTER Amendment A4）。真实 SUT 验证通过把 BUGate 导入外部或 scratch SUT 测试仓完成。导入模式（默认，CHARTER §2.2）下，被治理 SUT 仓提交自己的 config + profile。

### 豁免通道（仅限叙事提及）

豁免必须显式、逐点、可审计；没有全局开关，也没有环境变量绕过：

- **行内 marker**：在行尾添加 `# bugate: allow-sut-term`；Markdown 使用注释形式 `<!-- bugate: allow-sut-term -->`，避免影响渲染。该行同时豁免两类扫描。
- **文件级 frontmatter**：在 kit subtree 之外的*叙事型* Markdown 文档中使用 `desut: provenance-allowed`。豁免 identity scan；general hygiene 仍运行。
- **Allowlisted directory**：`docs/case-studies/`（真实导入/迁移故事）。豁免 identity scan；general hygiene 仍运行。

所有通道只合法化叙事/溯源**提及**。如果用它携带行为事实（endpoint、engine 会读取的路径、默认值），仍然违规 —— 这个判断由 code review 与 semantic gates 负责，而不是 grep。

---

## 2. PR 前本地验证（镜像 CI）

在仓库根目录运行完整检查。它镜像 [`.github/workflows/ci.yml`](.github/workflows/ci.yml) 的 `gate` job；本地通过后，CI 应该也通过。

```bash
# 1. 全部脚本可编译（stdlib-only core）
python3 -m py_compile scripts/*.py

# 2. 随仓模板通过四个语义门
python3 scripts/check_bugate_brief_semantics.py     .shared/skills/bugate/templates
python3 scripts/check_bugate_layer2_semantics.py    .shared/skills/bugate/templates
python3 scripts/check_bugate_inventory_semantics.py .shared/skills/bugate/templates
python3 scripts/check_bugate_v13_semantics.py       .shared/skills/bugate/templates --scope pre-code

# 3. De-SUT guard：hygiene + legacy regression + meta-test
#    meta-test 也覆盖基于临时 fixture 的 second-SUT profile-declared defense
python3 scripts/check_no_sut_terms.py
python3 scripts/check_no_sut_terms.py --terms-file tests/fixtures/legacy-sut-terms.txt
python3 tests/test_desut_guard.py

# 4. Write-guard dual-layout acceptance（临时 fixtures；本仓不提交示例 SUT 树）
python3 tests/test_write_guard_layouts.py
```

CI 还会运行 `bugate init` scratch-repo e2e（R4 negative control）、Wave 0 / Wave 8 graceful-degradation checks、orchestrator init smoke 与 stdlib-only import check —— 精确命令见 `ci.yml`。如果你的改动触及这些子系统，请运行对应步骤。最稳妥的方式是在打开 PR 前本地跑完 `ci.yml` 中列出的每一步。

[`INIT.md`](INIT.md) / [`INIT.zh-CN.md`](INIT.zh-CN.md) 中的零安装 smoke test（Step 2 / Step 3）是确认 engine 仍能 import、config 仍能 load 的最快 sanity check。

---

## 3. 仓库布局与文件放置规则

| Path | 含义 |
|---|---|
| `scripts/` | gate engine 与 driver scripts —— **stdlib-only** |
| `bin/` | thin wrappers（如 memory-bus / promote helpers） |
| `.shared/skills/bugate/` | 共享 skill：`SKILL.md`、`references/`、`templates/`、`adapters/` |
| `docs/qa-methodology/` | SUT-neutral method、SOP、ADR、protocols |
| `tests/` | upstream-only 临时 fixture acceptances（dual-layout write guard、de-SUT meta-test）+ fixtures（`fixtures/legacy-sut-terms.txt` 是 regression term list）；不是 vendored kit 的一部分 |
| `docs/case-studies/` | narrative allowlist：真实导入/迁移故事（identity-scan exempt，hygiene enforced） |
| `bugate.config.yaml` | core 默认配置；随仓保持**不绑定 profile**（无 guarded paths） |

经验规则：

- **Core 保持 stdlib-only。** `scripts/` 中的脚本只能导入 Python 标准库和 `scripts/` 下的 sibling modules（CI 用 AST import check 强制）。不要给 core 增加第三方依赖。
- **SUT facts 放进 profile**，永远不要放进 core。完整 key contract 见 [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md)。Core scripts 忽略未知 profile keys，因此 profile 可以增加自己的 runtime commands、evidence fetchers、environments、resources 或 auth。

**新增脚本（`scripts/*.py`）：** 保持 stdlib-only imports；用 `bugate_core.find_root()` 解析 active project root（从 CWD 向上找最近的 `bugate.config.yaml`，自开发 sentinel fallback，不依赖 git），用 `bugate_core.find_engine_root()` 解析 engine assets（templates、sibling scripts）—— 不要假设 CWD，也不要使用 git metadata。如果脚本是 gate，把它加到对应 CI 步骤。让它通过 de-SUT guard。

**新增模板（`.shared/skills/bugate/templates/`）：** 所有字段与示例都保持 SUT-neutral。模板会被上面的 semantic gates 检查，编辑后运行四个 gates。

**新增 adapter：** adapters 放在 `.shared/skills/bugate/adapters/`（ADR Implementation Notes）。保持中立；SUT-specific wiring 属于导入后的 SUT 测试仓，不属于 adapter。

**Hooks**（`.claude/`、`.codex/`）只能调用 `scripts/` 中的 SUT-neutral scripts，且不能依赖 git metadata（AGENTS.md Hook Policy）。注意：修改 `.codex/hooks.json` 可能要求 Codex Desktop 重新信任 hook hash。

---

## 4. 把本地经验晋级到 core

测试某一个 SUT 时学到的经验**不能**直接进入 core。它必须遵循 **Experience Promotion Protocol**：[`docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md`](docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md)。

准入测试（ADR Promotion Rule）一句话：一条经验只有在**不引用任何单一 SUT 的业务实体、路径、环境、凭据或 fixture** 时，才可以进入 core。拿不准时，把它留在 SUT profile。当前没有自动 generalization-gate 脚本 —— 中立化是 human/agent 在 promotion 前必须履行的义务。protocol 中说明了 restatement test、推荐的 two-SUT corroboration bar，以及操作机制（`scripts/memory_bus.py` + `bin/promote-memory`）。

---

## 5. PR 约定

- **从 `main` 拉分支。** 不要直接提交到 `main`。
- **保持 core dependency-free。** `scripts/` 中不要引入第三方 import；stdlib-only invariant 由 CI 强制。
- **PR 前本地运行 §2 checks。** 你的 PR 应该在和 CI 相同的 gates 上是绿的。
- **遵守 de-SUT 契约。** core 中永远不要有 behavioral SUT facts；identity terms 只能通过显式 narrative exemption（§1）出现；运行 §2 中四类 guard 读数。exemption marker 合法化的是*提及*，不是事实。
- **新增 config/profile flag 时**，写入 canonical references，避免漂移：命令/能力索引 [`CAPABILITIES.md`](CAPABILITIES.md)，以及任何 profile-readable key 对应的 profile contract [`.shared/skills/bugate/references/profile-schema.md`](.shared/skills/bugate/references/profile-schema.md)。
- **文档指向 canonical sources**，不要重复定义：链接 profile schema、ADR 与 promotion protocol，而不是在多个地方重新叙述。
- **框架说明文档保持中英双版。** 更新主要用户或贡献者文档（如 `README.md`、`CONTRIBUTING.md`、`INIT.md`）时，必须在同一变更里更新对应 `*.zh-CN.md` 镜像。
