---
name: bugate
description: Evidence-first business understanding, test design, execution, diagnosis and scoped completion assessment in a bound BUGate task.
---

# BUGate protocol entry

The exact protocol and Profile are fixed by the task's `.bugate/protocol.lock.json`.
Use `bugate_prepare` and the current dynamic protocol context. Canonical methods,
schemas and rules reside beside this projection under `protocol/`; this entry
never overrides the exact binding or the current workspace evidence.

Read requirement sources and record propositions, unknowns, states and boundaries.
Establish business Oracles and their sources before test design. Map risks,
requirements, Oracles, cases and implementation paths. Assess the design before
governed implementation. Run configured tests through `bugate_run_test` and keep
raw execution facts, including failures, skips and cancellation. Diagnose failures
with competing hypotheses and supporting evidence; do not weaken assertions to
hide product defects. Record scoped knowledge with its evidence and uncertainty.

Use `bugate_validate` for deterministic checks and `bugate_assess` for a specific
persisted claim. Structural validity does not prove semantic correctness or real
execution. Imported/model-reported logs are not host observations. Task completion
can include genuine product failures when the agreed evidence and report exist.
Write JSON drafts under `.bugate/drafts/` and call `bugate_record_artifact` to validate, compute digests and persist canonical artifacts. Updates supply `expected_digest` from the current artifact. Semantic review digest values may be null for the core to bind to current bytes; review conclusions still require evidence. Read and repair missing analysis using ordinary file tools. Arbitrary shell writes
are outside managed policy; an administrator can configure an exploration command.

## 中文

任务的精确协议与 Profile 以 `.bugate/protocol.lock.json` 为准。先读取需求来源，
记录业务命题、状态、边界和未知项，建立有来源的 Oracle，再映射风险、用例和实现。
设计评估满足后进行受管实现；使用 `bugate_run_test` 执行已配置命令并保存真实失败。
用 `bugate_validate` 检查结构与引用，用 `bugate_assess` 评估具体声明。结构通过、
日志哈希和模型自报不能证明真实执行。缺陷诊断保留假设与证据，不能为全绿放宽断言。
草稿写入 `.bugate/drafts/`，用 `bugate_record_artifact` 由核心计算摘要并原子登记；更新传入当前 `expected_digest`。评审结论仍需来源证据。知识沉淀注明来源和适用边界；完整测试交付可以包含真实产品失败。
