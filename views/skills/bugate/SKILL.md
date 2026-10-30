---
name: bugate
description: Evidence-first business understanding, test design, execution, diagnosis and scoped completion assessment in a bound BUGate task.
---

# BUGate protocol entry

The exact protocol and Profile are fixed by the task's `.bugate/protocol.lock.json`.
Use `bugate_prepare` and the current dynamic protocol context. Canonical methods,
schemas and rules reside beside this projection under `protocol/`; this entry
never overrides the exact binding or the current workspace evidence.

Read the current `ScopeContract` with `bugate_scope`. Start from the user's
finite objectives and source requests. When intent is clear, draft and register
the smallest sufficient scope using `bugate_record_scope`; obtain clarification
only for a material unresolved assumption. A host-captured user request is the
authority, not an agent-written statement that the user agreed. Never expand
scope merely because security, boundaries, concurrency or performance are common
test categories. They are required when the user goal or adopted requirements
include them. A main flow can have several explicitly required branches,
including expected rejection, failure and permission behavior.

Read requirement sources and record propositions, unknowns, states and boundaries.
Establish business Oracles and their sources before test design. Map risks,
requirements, Oracles, cases and implementation paths. Assess the design before
governed implementation. Run configured tests through `bugate_run_test` and keep
raw execution facts, including failures, skips and cancellation. Diagnose failures
with competing hypotheses and supporting evidence; do not weaken assertions to
hide product defects. Record scoped knowledge with its evidence and uncertainty.

Each required inventory case references the current scope revision/digest, one
scope scenario, the relevant flow, requirements, business Oracle and expected
evidence. Keep business depth sufficient inside that scope. Record discoveries
outside it with `bugate_record_suggestion` as descriptions and rationale only;
do not generate executable plans for those suggestions, execute them, promote
them into required cases, or ask repeatedly for decisions on them. Suggestions
cannot block completion. User changes create a new contract revision and need a
new plan/claim assessment. Changing the locked Profile requirement/risk denominator
requires an explicit new Profile version and new task, preserving the original.
Failures, difficulty and time limits do not authorize
scope reduction or weaker assertions.

Use `bugate_validate` for deterministic checks and `bugate_assess` for a specific
persisted claim. Structural validity does not prove semantic correctness or real
execution. Imported/model-reported logs are not host observations. Task completion
can include genuine product failures when the agreed evidence and report exist.
Publish authored quality artifacts inline with `bugate_publish_artifact`. Its
actual DSH invocation supplies the author/member observation. Updates supply the
current `expected_digest`. Before reviewing, read the exact target artifacts
and provide their explicit, non-null digests in `content.artifact_digests`;
never let publication silently select a newer target. Use official Agent Team
members, tasks and messages for the Profile-required perspectives and necessary
cross-checks. Preserve original disagreements, evidence and their dispositions.
Fresh/fork context and local execution separation do not prove cognitive
independence or semantic correctness.

Follow canonical `protocol/methods/team_review.json` and its bound Schemas/rules.
The native plugin signs its observations against a locally pinned public key;
assessment requires durable output and a successful final host tool-result
settlement. Inbox acceptance or Team task completion is not review validity.
Missing author/reviewer source cannot prove independence. These are local host
observations, not protection against same-permission filesystem tampering.

For file imports, write drafts under `.bugate/drafts/` and use
`bugate_record_artifact`. Import validation/digest calculation creates no native
authorship receipt and cannot satisfy a required independent Team review.
Read and repair missing analysis using ordinary file tools. Arbitrary shell writes
are outside managed policy. Host-configured auxiliary commands reference a
declared scope activity for requirement reading, login, data preparation,
evidence inspection or cleanup. Diagnosis references the current failure and
has a finite attempt/time budget; at its boundary preserve the conclusion or
blocker. An auxiliary activity is not a new acceptance objective. A scope
contract never bypasses host permissions, approval or tool restrictions.

Report execution completeness, functional acceptance, blockers/evidence gaps
and out-of-scope suggestions separately. Say “This conclusion covers only the
agreed scope” and list that scope. A fully evidenced product failure can finish
acceptance execution while `requirement_verified` remains unsatisfied. Do not
claim product-wide safety or correctness. Resume and child agents read the
same current contract; a stale contract, plan or assessment is not authority.

## 中文

任务的精确协议与 Profile 以 `.bugate/protocol.lock.json` 为准。用 `bugate_scope` 读取当前范围契约；用户意图清楚时，依据宿主捕获的真实用户请求生成契约并用 `bugate_record_scope` 登记，无需逐项确认。Agent 自述“用户已同意”不能作为授权。目标或已采纳要求包含权限、边界、拒绝或失败时必须保留；“最佳实践”不能自行增加安全、并发、性能等类别。主流程可以有多个必需分支。

先读取需求来源，
记录业务命题、状态、边界和未知项，建立有来源的 Oracle，再映射风险、用例和实现。
设计评估满足后进行受管实现；使用 `bugate_run_test` 执行已配置命令并保存真实失败。
用 `bugate_validate` 检查结构与引用，用 `bugate_assess` 评估具体声明。结构通过、
日志哈希和模型自报不能证明真实执行。缺陷诊断保留假设与证据，不能为全绿放宽断言。
作者产出的质量工件优先通过 `bugate_publish_artifact` 内联提交，由实际 DSH 调用捕获作者/成员来源；更新传入当前 `expected_digest`。评审前读取精确目标工件，并在 `content.artifact_digests` 明确填写非 null 摘要，不让发布时自动改绑新目标。按 Profile 必要视角使用官方 Agent Team 成员、任务及消息核对，保留原始异议、反证和处置；fresh/fork 与可观察执行分离不证明认知独立或语义正确。

具体契约以 `protocol/methods/team_review.json` 及绑定的 Schema/规则为准。原生插件以本地公钥 pin 对应的签名保存观察；评估需等待输出持久化及真实最终工具结果成功结算。消息接收、Team task complete 都不是评审有效性。缺少作者/评审来源时不能证明独立性；同权限文件系统篡改超出本地观察的信任边界。

路径导入仍可将草稿写入 `.bugate/drafts/` 后用 `bugate_record_artifact` 校验、计算摘要并登记，但不会建立原生作者来源，不能满足必需的独立 Team 评审。知识沉淀注明来源和适用边界；完整测试交付可以包含真实产品失败。

每个必需用例绑定当前契约版本/摘要、范围内场景、流程、要求、Oracle 与预期证据。范围内保持充分业务断言；范围外发现用 `bugate_record_suggestion` 只记录描述和理由，不展开详细可执行计划、不自动执行、不变成必测或完成阻塞，也不反复要求用户处理。只有可追溯用户变更才能创建新契约版本，之后重新评估计划与声明；改变锁定 Profile 的要求/风险分母需显式新 Profile 版本和新任务，保留原任务；失败、困难或时间不足都不能授权缩减范围。

读取需求、登录、准备数据、检查证据、清理现场通过已配置且关联契约活动的辅助命令执行，不增加验收目标。诊断必须关联当前失败并遵守有限次数/时间边界，到达边界后保存结论或阻塞；诊断发现的范围内失败仍影响功能结论。范围契约不越过宿主权限、审批或工具安全边界。

报告分别写明执行完整性、功能验收结果、阻塞/证据缺口和范围外建议，注明“本结论仅覆盖本次约定范围”并列出实际范围。证据齐备的真实失败可完成验收执行，但 `requirement_verified` 不通过；不得宣称产品整体安全或全面正确。恢复和子 Agent 使用同一有效契约，旧契约、旧计划和旧评估不能授权新执行。
