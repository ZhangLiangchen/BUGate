# Native v2 semantics retention / 原生 v2 语义保留

Baseline: `61e1327a7bf01505cc82760782ffa1272ccaa03f`. This document records the
semantic mapping, not a compatibility promise. The 2026-10-29 request retires
historical serialized inputs, command wrappers and host governance runtimes.

基线如上。本表记录质量语义的原生实现去向，不承诺旧格式兼容；2026-10-29 用户授权
已撤销历史格式、命令包装和宿主治理运行时。

| Historical quality responsibility / 旧质量职责 | Native v2 representation and oracle / 原生表达与判据 | Validation / 验证 |
| --- | --- | --- |
| Layer 1 proposition/source/evidence classification | `business_brief.propositions`, exact Profile sources, fact/inference/unknown; unresolved facts prevent business completion | `CoreAcceptance.test_AC02_*`, `NativeSemantics.test_blank_business_statement_is_not_valid` |
| Observable business oracle, state/boundary descriptions | Typed oracle expected/observation/deadline/preconditions/source refs, states and boundaries | Schema + business assessment; missing O and stale review negative controls |
| Accepted prose cannot be TBD | Non-whitespace required strings + exact unresolved marker detection; quoted source text is still data | `test_placeholder_oracle_does_not_pass_even_with_current_review`, `test_quoted_placeholder_in_requirement_is_data` |
| Layer 2 evidence plan and all P/O mappings | Every proposition and Oracle has a testability strategy; exact `(P,O)` layer binding | `test_every_oracle_needs_testability_mapping`, `test_multiple_oracles_per_proposition_are_supported`, `test_oracle_specific_layer_cannot_borrow_another_oracle_layer` |
| Cheapest sufficient layer, dependencies, alternatives, side effects | Required `rationale`, `alternatives`, `preconditions`, `side_effects`, `environment`, explicit gaps | Strict Schema and testability assessment; semantics remain source-backed reviewer judgment |
| Layer 3 reverse coverage and frozen denominator | Inventory covers frozen requirement/risk scope and all P/O; each P needs negative/boundary coverage | Core scope, Oracle swap, multi-P and negative controls |
| Resolved case data-source plan | Inventory artifact source plan binds actual declared source bytes; per-case `source_refs` can narrow it | `test_inventory_requires_resolved_source_plan`, `test_case_source_must_be_declared` |
| Readable cases and implementation targets | Native inventory has preconditions, expected observations, layer reason, implementation path/digest and selector; no second Markdown truth source | Strict Schema; implementation digest and selector execution checks |
| Adversarial findings, dispositions, residual risks, absorption | Optional typed `adversarial_review`; included findings require actual cases covering named P/O/risks; unresolved findings/risks block design | Included-case, excluded-rationale, unresolved-risk tests |
| Optional full domain/state/dimension modeling | Typed `domain_model`, `state_model`, `dimension_matrix`, object/invariant/state/transition/P/O/case references | Full-model positive + missing-object/oracle/proposition/case negatives |
| Required independent review | Profile `quality_requirements.independent_review: true` requires each reviewed producer to differ from reviewer | `test_independence_requires_distinct_named_producer`; not an authentication claim |
| Optional model requirements | Only true-valued Profile flags may require richer models. Supplied models always need current review | Optional/default, required-absent, false-flag rejection tests |
| Failure report/knowledge/diagnosis | Native report evidence/case coverage, sourced knowledge, supporting/contradicting evidence and hypothesis statuses | AC-05, diagnosis/reference, unexecuted-report-case, knowledge-only tests |
| Runtime dispatch, lifecycle roles and memory | Retired per scope; no equivalent scheduler or long-term-memory dependency is introduced | Source/package audit and AC-18 |
| Legacy pre-code wrapper | Deleted; historical artifacts/Profile input fail v2 Schema with `STRUCTURE_INVALID` | `test_retired_artifact_format_is_explicitly_rejected`, `test_old_artifact_and_old_profile_are_rejected` |

`rules/quality.json` is now the sole public invariant text and claim-dimension
mapping. Every MethodSpec refers to `core-quality-invariants`; context and the DSH
Skill projection consume that source. `evaluator_identity()` hashes only current
Python modules and canonical Protocol content. It no longer imports or hashes a
historical script tree. Protocol remains version `2.0.0` with a new exact digest;
old task locks fail explicitly, without automatic migration.

公共不变量和声明维度映射唯一位于 `rules/quality.json`。MethodSpec 只引用约束集合，
上下文与 DSH Skill 投影读取同源。评估器身份仅包含当前 Python 模块与协议内容，
不再读取旧脚本。协议版本仍为 `2.0.0`，摘要已变化；旧锁明确失败，不自动迁移。

## Executed validation / 实际验证

Command: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests/v2 -v`.
Result: **96 tests PASS**. Full output: [evidence/core-tests.log](evidence/core-tests.log).
The historical-wrapper success test was replaced by explicit rejection tests;
all 71 other existing core tests remain and pass. 24 native-semantic tests were
added. These are local core/unit/real-process tests, not live-model evaluation.

命令如上：**96 项通过**。原包装器成功测试替换为明确拒绝旧格式的测试；其余原有
71 项核心测试保留并通过，新增 24 项原生语义测试。此处证据属于核心、单元及真实
本机进程验证，不是 live-model 效果验证。

## Review boundary / 自审边界

Business-understanding claims apply business/model checks without requiring future
execution. Design and implementation claims include testability, inventory and
optional design models; supplied models must also be named in the claim. Execution
and delivery retain actual failures, and no source/hash/reviewer field is promoted
to authenticated execution. Unknown classification and explicit gaps remain
blocking or inconclusive; `null`, absent required values, extra properties and
whitespace-only prose fail strict Schema where specified.

业务理解声明不被要求提供未来执行；设计与实现声明包含可测性、inventory 和提供的
设计模型。模型工件也必须列入声明。执行/交付保留真实失败，来源、哈希、评审字段
均不提升为认证执行。未知分类与显式缺口保持阻塞/不确定；Schema 按定义拒绝 null、
缺字段、额外字段和纯空白正文。

Limit: structural checks establish referential consistency and required fields;
semantic truth still requires a source-backed review. Same-user provenance is
not authenticated actor isolation. Profile-specific old dialect settings are
not migrated. No unresolved core test failures were observed in this run.

局限：结构检查证明引用一致和字段完整，业务事实仍需有来源的语义评审。同用户
来源记录不等于认证身份隔离。旧 Profile 方言配置不迁移。本轮无未解决核心测试失败。
