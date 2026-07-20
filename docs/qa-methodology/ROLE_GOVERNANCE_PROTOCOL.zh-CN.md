[English](ROLE_GOVERNANCE_PROTOCOL.md) | [简体中文](ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)

# Wave 7 可审计角色治理协议

状态：BUGate v0.4.0 冻结契约。本协议保持 SUT-neutral，是实现、hooks、测试、
导入器与 release 验收的规范性依据。

## 1. 范围与角色词汇

Wave 1 与 Wave 7 解决不同的独立性问题：

- Wave 1 在同一设计阶段派发相互独立的 Codex/Claude peer，暴露理解分歧。
  peer 是只读分析 worker，不是生命周期角色。
- Wave 7 把生命周期职责隔离到 `designer`、`implementer`、`reviewer` 的独立
  会话，并记录每次状态转换。

`role_governance.phases` 只接受这三个生命周期 token。`codex`、`claude` 等
runtime 名只能进入 receipt runtime metadata，不能充当角色。现有 `agent_roles`
是独立的路径访问策略，继续兼容 legacy/SUT 自定义角色以及 bare-list、read、
write 三种形式。

## 2. 配置契约

Core 默认保持 inert：

```yaml
role_governance:
  mode: off
```

imported SUT profile 可显式启用完整契约：

```yaml
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
      allowed_roles: [designer]
    implementation:
      allowed_roles: [implementer]
      requires_handoff_from: [designer]
    post_run:
      allowed_roles: [reviewer]
      requires_handoff_from: [implementer]
```

模式语义：

- `off`：保持 v0.3.x 行为，不执行角色状态门禁。
- `advisory`：评估并报告违规，但不阻塞普通写入，也不宣称解锁。为了避免 advisory
  证据链可被伪造，直接编辑 evidence chain 仍被禁止。
- `required`：配置非法、角色或 session 缺失/错误、receipt 缺失/无效以及任何
  drift 都 fail-closed。

`memory_mode` 只能是 `best_effort` 或 `required`。required 的角色转换使用 strict
Memory；普通 recall、note 与 Stop heartbeat 继续 best-effort。

配置文件按 nested mapping 解析。确定性合并规则为：mapping 递归合并，profile
scalar 覆盖 base scalar，profile list 整体替换 base list。`parse_simple_yaml()`
继续只服务 legacy frontmatter/简单工件。每份配置在合并前把旧顶层 `namespace`
规范化为 `memory.namespace`，因此旧 profile 能覆盖 base 的 nested 值；最终结果
同时暴露新旧访问形式。同一文档若同时声明冲突的新旧值，以新的 nested 形式为准，
再镜像到 legacy alias。

required 模式拒绝：超出支持子集的 YAML、非法类型/enum/boolean、绝对或逃逸的
evidence 目录、未知或缺失 phase、非法生命周期 token、空角色集、错误 handoff
关系、缺失显式 profile 以及所有非法受治理 regex；错误必须清晰。

## 3. 状态机

append-only 事件与状态如下：

| 顺序 | 事件 | 角色/session | 前置条件 | 结果状态 |
|---|---|---|---|---|
| 1 | `human_acceptance` | designer 会话仅记录已经发生的人工决定 | pre-code 全部 passed；配置要求的 03B 已是 `passed` | `ready_for_designer_handoff` |
| 2 | `designer_handoff` | designer | 人工接受有效，pre-code/provenance 当前有效，strict Memory 锚定 | `awaiting_implementer_acceptance` |
| 3 | `implementer_acceptance` | 不同 session 的 implementer | 精确验证 handoff ID/metadata；acceptance Memory 锚点已复核 | `implementation_unlocked` |
| 4 | `implementer_handoff` | implementer | 至少一个实现文件；均在 workspace 内、命中 guarded path 且绑定同一 UC | `awaiting_reviewer_acceptance` |
| 5 | `reviewer_acceptance` | 不同 session 的 reviewer | implementer handoff 精确验证；实现 snapshot 未漂移 | `post_run_active` |
| 6 | `reviewer_completion` | reviewer | 记录并验证 04/05、命令摘要、exit code、log/evidence hash 与最终 gate | `closed` |

approve 命令仅为已经由人类设成 `passed` 的 03B 记录声明性 `approved_by`；它不
修改 03B，也不是身份认证。同角色自接单被拒绝；启用配置后同 session 接单也被
拒绝。成功重试必须幂等。drift 恢复通过追加 superseding generation 完成，禁止
删除 evidence 来 reset。

## 4. 本地证据与 hash

每个 UC 只使用 `<artifact-dir>/00_role_evidence/`：

```text
00_role_evidence/
├── chain.json
└── receipts/000001-<event>-<hash>.json
```

Receipt append-only。`chain.json` 只保存 schema version、当前 state/sequence、
chain head hash，以及各逻辑事件最新 receipt 的路径。路径统一为 workspace-relative
POSIX，snapshot 按 path 排序。JSON hash 使用 UTF-8、sorted keys、紧凑 separators；
计算 receipt 时排除 `receipt_sha256`。每条 receipt 链接前一条 receipt 与稳定的
transition hash。

Designer handoff 捕获 active profile、全部 required pre-code 工件、存在时的正式
`00_multiview` 输出、03B dispatch provenance 与当前 human-acceptance receipt。
Implementer handoff 增加实现文件 hash；reviewer completion 增加 04/05、执行日志
与 evidence。

Receipt/chain 发布使用同目录临时文件、flush、`fsync` 与 `os.replace`。不得落盘
secret 或 Memory credential。每次受治理编辑只在本地复核 receipt 内容/hash、链
链接/head、profile hash、pre-code hash/gate status 与实现文件 hash；禁止每次编辑
访问 Memory Service。

## 5. Strict Memory 转换协议

required 转换严格按以下顺序执行：

1. 构造稳定 transition payload 与 `transition_sha256`。
2. POST Memory transition，并要求有效 content hash。
3. exact GET 该 hash，验证 namespace、角色、UC、phase、transition 与被引用
   handoff metadata。
4. 带 Memory ID 构造完整本地 receipt，并计算 receipt hash。
5. PUT receipt hash 到 Memory metadata。
6. 再次 exact GET 并验证完整锚点。
7. 最后才原子发布本地 receipt 与 chain head。

Acceptance 必须先 exact GET 并验证传入的 handoff ID，再写入并 exact GET 自己的
acceptance。service unavailable、timeout、HTTP/write failure、exact ID 不存在或
任一字段不一致均返回非零，且不发布本地解锁 receipt、不推进 chain。稳定 transition
内容与本地 latest-event 检查保证 retry 幂等。高基数 ID/hash 放 metadata，不放 tags。

## 6. 强制执行面

所有 Core artifact mutator 在创建目录、复制模板、派发 peer 或写输出之前调用共享
Python preflight；通用 Core writer 再做一次目标路径分类 backstop。Role evidence
使用私有原子 writer，不提供可由环境变量打开的内部 bypass。

required 模式下，pre-code init 只创建 pre-code 与已选择的 optional modeling 工件；
legacy/off init 保持 v0.3.x 一次创建 01–05 的行为。04/05 归 reviewer 所有。03B 一旦
有 human-acceptance receipt，`--auto` 不得重新生成；handoff 只重跑 semantic 与
provenance 校验。

Hooks 保持两种独立职责：`check_bugate.py` 验证 pre-code passed，
`check_role_evidence.py` 验证角色与 receipt chain。Claude 对写门使用 `Edit|Write`，
对 `agent_roles` 使用 `Read|Edit|Write`；Codex 在 `apply_patch` 上执行四个 guard。
agent tool 直接编辑 `00_role_evidence/**` 一律拒绝。SessionStart 做 best-effort Memory
recall 并打印角色治理状态；Stop 继续按小时 best-effort heartbeat，agent 优先取当前
role，否则为 `agent`。

Peer bridge 子进程必须清除生命周期 role/session/receipt 身份，同时保留 profile/
project root、proxy、model 与 reasoning effort 配置。

## 7. 兼容、恢复与安全边界

不含 `role_governance` 的 profile 与 v0.3.x 行为一致。启用 `required` 不会给历史
passed UC 自动补证据：必须创建当前 human acceptance、handoff、acceptance chain。
profile/pre-code drift 从 designer acceptance/handoff 重启；implementation drift 从
implementer handoff/reviewer acceptance 重启。rerun importer 会刷新 vendored scripts
与 BUGate-owned hook，同时保留 SUT-owned hook；Codex hook 变化后必须重新信任。

本协议提供角色声明、session 区分、hash 链接、外部 Memory 锚点、篡改/drift 检测
与可审计状态转换，但不提供不可抵赖的人类身份。环境变量、hook 与本地文件不能证明
真实操作者。强身份隔离需要独立 OS 账号、容器、managed runner 或按角色发放的
服务端凭据。Hook 也无法拦截任意 shell 重定向或外部编辑器；支持的 agent tool、
orchestrator 与 Core mutator 会被强制治理，更强的文件系统隔离属于 managed runner。
