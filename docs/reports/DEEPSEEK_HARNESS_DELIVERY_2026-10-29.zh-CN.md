# BUGate DeepSeek Harness 完整改造交付报告

[English](DEEPSEEK_HARNESS_DELIVERY_2026-10-29.md)

**总体状态：COMPLETE。** 合同的49项HC/AC均有通过证据：核心72/72、精确13项依赖下真实DSH native 22/22、独立安装runtime 1/1、安装边界14/14、最终包生命周期19/19、legacy完整35个程序及675个unittest、clean-tree归档两步全部通过。安装后的真实DSH由确定性模型fixture驱动，实际27次模型请求、2个真实执行用例，当前`requirement_verified`为`satisfied`。失败反例按预期拒绝；发现的基础设施缺陷及原失败证据保留。

完整证据位于[交付证据目录](../../artifacts/deepseek-harness-20261029/)，逐项机器记录见[最终49项矩阵](../../artifacts/deepseek-harness-20261029/acceptance-final.json)。本报告证明约定支持目标的完整工程交付，不声称live-model效果或未测试平台可用。

## 实现范围与关键决策

- [src/bugate/](../../src/bugate/) 实现宿主无关Python核心、引用与内容摘要、锁定Profile范围、声明评估、稳定错误、原子持久化、并发检测、执行采集和PreparedProtocolContext。九个核心Schema加MethodSpec、ExecutionRecord、CommandResult三种辅助Schema全部具正反例；七个MethodSpec覆盖业务理解、可测性、测试设计、实现、执行、诊断、知识。
- [protocol/v2/](../../protocol/v2/) 是规范来源，包含Schema、规则、reason codes、方法和双语契约。校验使用成熟jsonschema Draft202012；工具和安装错误也纳入目录，不另造宿主质量语义。
- CLI提供init、prepare、status、validate、assess、artifact/evidence record、execution run及DSH setup/doctor/remove。统一CommandResult区分调用完成与质量通过；退出0/3/2/1/124/130分别表示命令成功、质量未满足、输入错误、运行失败、超时、取消。console entry和`python -m bugate`为入口，未向legacy vendor加入无法运行的v2 shim。
- [adapters/deepseek-harness/plugin/](../../adapters/deepseek-harness/plugin/) 提供真实apply(ctx)、七个薄工具、canonical Skill/模板投影、会话恢复、受管写入/执行门禁、有界turn-stopping返工。DSH负责循环/调度；BUGate不新增Agent loop、current-stage真相源或强依赖记忆服务。
- Python调用通过DSH配置的filesystem/subprocess provider；opaque target不作宿主路径。`context.text`和guard保持同步；created/pre-step/request/pre-execute/stopping等可await接缝执行核心调用。`tools/result`只观察，证据持久化在返回前明确await。
- 同task ID不同项目隔离；claims/results不递归污染质量快照，但AssessmentRequest绑定当前Claim完整内容。Profile范围、来源、规则、脚本与Oracle变化会失效旧评估。执行输入比对包含路径集合，新增配置也会失效旧运行。
- 外部/model/human材料不升级host_observed。受管wrapper实际运行固定argv、捕获进程/日志/JUnit/逐case观察，检查聚合统计和输入适用性。真实产品失败可以满足完整测试交付，不能验证失败需求；零测试/全skip/xfail不证明通过；旧失败保留但不污染未选中的当前证据范围。
- 额外的core-owned finite filesystem_manifest验证两provider读取同一字节和路径集合；它不是quality snapshot。包含draft、claims、assessment和Profile来源，界限为2048文件/2048目录/32MiB。仅排除write.lock和专用`.bugate/runtime`记账子树，adapter不重写业务规则。
- 打包包含编译JS、bundle patch、规范资源、生成Skill、Python wheel和离线wheelhouse。安装所有权、pending journal、升级/rollback与无关用户配置保留由独立边界和真实DSH CLI验收。

九个核心Schema为ProtocolBinding、TestTask、Artifact、Evidence、Claim、AssessmentRequest、AssessmentResult、Profile、PreparedProtocolContext；辅助Schema为MethodSpec、ExecutionRecord、CommandResult。全部12种均逐类校验缺required、错误kind/version和未知字段，并运行Draft202012Validator.check_schema。七个MethodSpec不编码执行调度。

## 版本与环境

| Item | Verified value |
|---|---|
| Contract SHA-256 | `96375e7c7357e01aaea9197539bc2096a94833d1130e25c3a655faa261ec00e2` |
| Initial workspace HEAD | `cac1be96ba38f03c40ab9ab312a270b01930e467` |
| Implementation reference | `76fd4a12ad62f4071952fe7b1750560cde4c7505` |
| Source end | `c96622502475504d9670478ee9cd0d72f4787120` |
| Branch | `codex/deepseek-harness-complete` |
| Core / protocol / native bundle | `2.0.0a1` / `2.0.0` / `2.0.0-dev.1` |
| Canonical protocol digest | `sha256:2a29de5e50dc81a832bc3ed9c1ae940386a2232db2170d649089592e80122e3f` |
| Actual DSH source / runtime | `f3803c96974ad4a94195fb97e5c637e9c2a3afe3` / `0.2.2-alpha.2` |
| Executed platform | macOS 26.5.1 arm64; Python 3.13.3; Node 26.8.1; pnpm 11.25.0 |
| Key dependencies | jsonschema 4.23.0; hatchling 1.27.0; Ajv 8.17.1 (final exact native and installed runtime); TypeScript 5.9.3; Cordis 4.0.6-alpha.1 |

环境证据：[environment-final.json](../../artifacts/deepseek-harness-20261029/environment-final.json)、[core/dependencies.json](../../artifacts/deepseek-harness-20261029/core/dependencies.json)、[dsh/dependency-probe.md](../../artifacts/deepseek-harness-20261029/dsh/dependency-probe.md)。用户全局DSH `0.1.5-rc.1`未被升级；测试使用独立安装的精确目标版。`CLAUDE.md -> AGENTS.md`仍为符号链接。

最后一轮native验证的13项依赖逐项expected==actual，包含Ajv8.17.1、TypeScript5.9.3、Node类型24.10.0；见[dsh/exact-dependencies.json](../../artifacts/deepseek-harness-20261029/dsh/exact-dependencies.json)和[dsh/typecheck-final.json](../../artifacts/deepseek-harness-20261029/dsh/typecheck-final.json)。早期Ajv8.20运行日志保留为[dsh/all-tests-ajv-8.20.log](../../artifacts/deepseek-harness-20261029/dsh/all-tests-ajv-8.20.log)，不替代最终锁定版本证据。最终安装版也解析Ajv8.17.1。

实施主体commit为`eac4bd3780975f1b161928625285e6ac3bddc70b`；source end `c96622502475504d9670478ee9cd0d72f4787120`仅追加portable builder的单行修正。native tgz/wheel是已实际安装验收的同一内容，摘要见下表；legacy tar/zip由source end构建。

## 实际命令与使用入口

核心验证实际命令与逐个JSON输出在[core/cli-commands-final.json](../../artifacts/deepseek-harness-20261029/core/cli-commands-final.json)、[core/cli-final/](../../artifacts/deepseek-harness-20261029/core/cli-final/)；包含version、幂等init、prepare、status、validate以及需求验证/任务交付assess。artifact/evidence/执行命令另由核心与真实runtime测试执行。

```sh
PYTHONPATH=src "$BUGATE_PYTHON" tests/v2/test_core.py
node adapters/deepseek-harness/plugin/node_modules/typescript/bin/tsc --noEmit -p adapters/deepseek-harness/plugin/tsconfig.json
BUGATE_TEST_PYTHON="$BUGATE_PYTHON" BUGATE_DSH_MODULES="$DSH_MODULES" node --test adapters/deepseek-harness/tests/bridge.test.mjs adapters/deepseek-harness/tests/runtime.test.mjs
python3 tests/integration_install/test_install_boundaries.py
```

以下命令由已执行命令规范化而来；环境变量应显式指向所选Python、DSH、bootstrap、外部项目、Profile和任务，实际路径与逐项运行记录见[package-final/commands.json](../../artifacts/deepseek-harness-20261029/package-final/commands.json)：

```sh
python3 dist/deepseek-harness/install_dsh.py --package dist/deepseek-harness/bugate-deepseek-harness-2.0.0-dev.1.tgz --venv "$BOOTSTRAP" --project "$SUT_PROJECT" --dsh-profile bugate --dsh "$DSH_CLI" --dsh-home "$DSH_HOME"
"$BOOTSTRAP/bin/bugate" setup deepseek-harness --project "$SUT_PROJECT" --dsh-profile bugate --dsh "$DSH_CLI" --dsh-home "$DSH_HOME" --package dist/deepseek-harness/bugate-deepseek-harness-2.0.0-dev.1.tgz --json
"$BOOTSTRAP/bin/bugate" init --project "$SUT_PROJECT" --task REQ-001 --sut-profile "$SUT_PROFILE" --json
"$BOOTSTRAP/bin/bugate" protocol prepare --task "$TASK" --max-bytes 8000 --json
"$BOOTSTRAP/bin/bugate" status --task "$TASK" --json
"$BOOTSTRAP/bin/bugate" validate --task "$TASK" --json
"$BOOTSTRAP/bin/bugate" artifact record --task "$TASK" --input .bugate/drafts/brief.json --json
"$BOOTSTRAP/bin/bugate" assess --task "$TASK" --claim claims/design.json --json
"$BOOTSTRAP/bin/bugate" evidence record --task "$TASK" --input evidence-input/import.json --json
"$BOOTSTRAP/bin/bugate" execution run --task "$TASK" --run-id explicit-run --case C1 --case C2 --report logs/runner.xml --json -- "$BUGATE_PYTHON" tests/runner.py logs/runner.xml
"$BOOTSTRAP/bin/bugate" doctor integrations --project "$SUT_PROJECT" --dsh-profile bugate --dsh "$DSH_CLI" --dsh-home "$DSH_HOME" --json
"$BOOTSTRAP/bin/bugate" remove deepseek-harness --project "$SUT_PROJECT" --dsh-profile bugate --dsh "$DSH_CLI" --dsh-home "$DSH_HOME" --rollback --json
"$BOOTSTRAP/bin/bugate" remove deepseek-harness --project "$SUT_PROJECT" --dsh-profile bugate --dsh "$DSH_CLI" --dsh-home "$DSH_HOME" --json
```

## 安装产物与校验和

| 产物 | 版本/内容 | 最终路径与SHA-256 |
|---|---|---|
| 原生tgz | @bugate/deepseek-harness 2.0.0-dev.1，编译JS和全部资源 | [dist/deepseek-harness/bugate-deepseek-harness-2.0.0-dev.1.tgz](../../dist/deepseek-harness/bugate-deepseek-harness-2.0.0-dev.1.tgz) / `0a4f7ddbcb0a035f603823f82da14e399ffa6bd236afeed3879625e8f626baab` |
| Python wheel | bugate-core 2.0.0a1，规范与legacy复用资源 | [dist/deepseek-harness/bugate_core-2.0.0a1-py3-none-any.whl](../../dist/deepseek-harness/bugate_core-2.0.0a1-py3-none-any.whl) / `ceffa043485f3383b8ecc03b71a273e5f646a7c7c2ad898bf965f18d10ec4e02` |
| Bootstrap安装器 | stdlib安装器；离线Python bootstrap | [install_dsh.py](../../dist/deepseek-harness/install_dsh.py) / `5680d650f4e72e705e19f3e3618076999341e58f84eefe94a6aceff5b7f3bdd2` |
| 离线依赖与bootstrap | wheelhouse、install_dsh.py、inventory、SHA256SUMS | [dist/deepseek-harness/build-manifest.json](../../dist/deepseek-harness/build-manifest.json), [dist/deepseek-harness/SHA256SUMS](../../dist/deepseek-harness/SHA256SUMS), tgz `package/python/inventory.json` |
| legacy tar | 0.4.5 | [bugate-0.4.5.tar.gz](../../dist/legacy-regression/bugate-0.4.5.tar.gz) / `8e610a15bbd9a34c2de6b854586baabc446b5fe934d5502ed43ee0b482ed8a9a` |
| legacy zip | 0.4.5 | [bugate-0.4.5.zip](../../dist/legacy-regression/bugate-0.4.5.zip) / `386bec9dff3d7e21eefa61affc6828135275edc47f7f2718022763f20047a3cf` |

不得沿用`packages-iteration*`的摘要作为最终交付摘要。最终安装验收已在干净目录清除PYTHONPATH/NODE_PATH，加载安装位置的Python/插件/Schema/Skill，并执行真实authoring→设计assess→实现→命令采集→需求验证声明。安装doctor的hydration=unobserved本身不证明真实会话。

## 逐项HC/AC映射

原49项语义取自冻结合同，未删减或改名。每项均PASS；下表测试别名解析见后续索引。精确断言、命令、日志由[core/core-mapping.json](../../artifacts/deepseek-harness-20261029/core/core-mapping.json)、[dsh/dsh-mapping.json](../../artifacts/deepseek-harness-20261029/dsh/dsh-mapping.json)、[packaging-mapping.json](../../artifacts/deepseek-harness-20261029/packaging-mapping.json)及最终矩阵共同提供。

| ID | 合同要求 | 已执行测试/选择器 | 实际结果 |
|---|---|---|---|
| HC-01 | setup 幂等 | P02 | PASS |
| HC-02 | canonical Protocol 可发现 | C: `test_HC02_04_05_canonical_schema_binding_context`; D01; P03; S | PASS |
| HC-03 | 工作区/任务身份一致解析 | C: `test_HC03_task_ambiguity_is_explicit`, `test_AC12_same_task_id_different_workspace`; D02; D16; D18; W | PASS |
| HC-04 | 精确绑定版本与摘要保持一致 | C: `test_HC02_04_05_canonical_schema_binding_context`, `test_AC09_protocol_mismatch`, `test_AC09_profile_mismatch`; D01; D04 | PASS |
| HC-05 | PreparedContext 注入前通过 Schema 校验 | C: `test_HC02_04_05_canonical_schema_binding_context`, `test_all_schemas_positive_and_negative_examples`; D01; D17 | PASS |
| HC-06 | 相关 Agent 推理前已有适用协议上下文 | D01; D13; R | PASS |
| HC-07 | 长任务不依赖一次性 Skill 记忆 | D01 | PASS |
| HC-08 | 提供恢复生命周期时，压缩/重启后重新 hydrate | D01; D04 | PASS |
| HC-09 | 受支持子 Agent 独立取得同一 Binding | D09; D13 | PASS |
| HC-10 | 版本/摘要失配可见失败 | C: `test_AC09_protocol_mismatch`, `test_AC09_profile_mismatch`; D07; D10; D16 | PASS |
| HC-11 | 过期投影可检测 | D07; D10; P05 | PASS |
| HC-12 | 显式 assessment 可调用 | C: `test_CLI_envelope_operations_and_quality_exit`; D01 | PASS |
| HC-13 | Adapter 不改变核心方法与评估语义 | C: `test_CLI_and_library_assessment_semantic_parity`; D01 | PASS |
| HC-14 | rollback 只删除本插件拥有的集成状态 | P07; P09; P13; P18 | PASS |
| HC-15 | 历史 Context Runtime 不覆盖协议与当前工作区事实 | C: `test_HC15_historical_memory_cannot_override_current_facts`, `test_AC18_no_memory_service_dependency`; D01 | PASS |
| AC-01 | 通用临时 SUT 完整闭环 | C: `test_AC01_complete_traceable_real_execution`; D08; D12; S | PASS |
| AC-02 | 缺少 Oracle 或业务语义未定 | C: `test_AC02_missing_oracle_rejected`, `test_AC02_semantics_require_current_review`, `test_AC02_stale_review_rejected` | PASS |
| AC-03 | 只有代码，无运行 | C: `test_AC03_code_without_execution` | PASS |
| AC-04 | 退出码零但零用例/全部跳过 | C: `test_AC04_zero_tests_exit_zero`, `test_AC04_all_skipped_exit_zero`, `test_AC04_all_xfail_not_verified` | PASS |
| AC-05 | 真实产品失败 | C: `test_AC05_real_product_failure_completes_delivery_not_verification` | PASS |
| AC-06 | 伪造 passed/verified/exit_code 字段 | C: `test_AC06_import_cannot_upgrade_source`, `test_AC06_forged_host_record_without_receipt` | PASS |
| AC-07 | 导入历史或他人日志 | C: `test_AC07_external_history_retains_provenance` | PASS |
| AC-08 | 同路径内容修改/证据截断/删除 | C: `test_AC08_evidence_truncated`, `test_AC08_evidence_deleted`, `test_AC08_same_path_implementation_changed`, `test_AC08_new_runtime_input_invalidates_prior_execution`; D10 | PASS |
| AC-09 | 协议/Profile/规则/脚本变化 | C: `test_AC09_protocol_mismatch`, `test_AC09_profile_mismatch`, `test_AC09_source_revision_mismatch`, `test_AC09_changed_oracle_cannot_reuse_prior_execution`; D07; D10 | PASS |
| AC-10 | 评估写入后的重复 prepare | C: `test_AC10_assessment_outputs_do_not_self_invalidate`, `test_AC10_current_claim_mutation_cannot_reuse_old_satisfaction`, `test_AC10_forged_result_not_authority` | PASS |
| AC-11 | 并发修改发生在评估与提交之间 | C: `test_AC11_expected_snapshot_conflict`, `test_AC11_binding_change_detected_in_existing_workspace`, `test_AC11_concurrent_lock_rejects_second_writer`, `test_AC11_change_between_evaluation_and_commit_is_conflict`; D11 | PASS |
| AC-12 | 同 task id、不同工作区 | C: `test_AC12_same_task_id_different_workspace`; D02 | PASS |
| AC-13 | 路径逃逸、符号链接、越权写 | C: `test_AC13_traversal_and_symlink`, `test_AC13_report_symlink_created_during_execution_is_rejected`; I; D03 | PASS |
| AC-14 | 工件含指令、模板变量或长文本 | C: `test_AC14_content_is_data_and_utf8_budget`; D17 | PASS |
| AC-15 | Python 缺失、异常、无效 JSON、输出过大 | C: `test_AC15_missing_process`, `test_AC15_invalid_json_cli`, `test_AC15_output_limit`, `test_AC15_missing_python_package_has_structured_error`; B01; B02 | PASS |
| AC-16 | 超时或用户取消 | C: `test_AC16_timeout_terminates_process`, `test_AC16_cancel_cli_terminates_process`, `test_AC16_cancel_terminates_process_group_child`; B03; D15 | PASS |
| AC-17 | evidence 写盘失败、半写、重复事件 | C: `test_AC17_run_idempotency`, `test_AC17_unknown_execution_is_not_replayed`, `test_AC17_atomic_failure_keeps_previous`, `test_AC17_evidence_publication_failure_does_not_authorize`; D08 | PASS |
| AC-18 | 无记忆服务 | C: `test_AC18_no_memory_service_dependency`; D01 | PASS |
| AC-19 | 实际 bundle 安装到新 profile | P01; S | PASS |
| AC-20 | 脱离 BUGate checkout | P01; P03; S | PASS |
| AC-21 | 用锁定 DSH 类型编译 | T; D12; S | PASS |
| AC-22 | 真正模型请求前的上下文 | D01; D11; D13; R | PASS |
| AC-23 | 至少 50 次确定性 turn/等价刷新驱动及压缩 | D01 | PASS |
| AC-24 | restart/resume 与 preset 变化 | D04; D09 | PASS |
| AC-25 | spawn/fork/独立 preset 子 Agent | D02; D09; D13 | PASS |
| AC-26 | nested/PTC tool call | D08 | PASS |
| AC-27 | async pre-execute + sync guard | D03; D11; D12 | PASS |
| AC-28 | 缺业务理解时补足材料 | D03; D14 | PASS |
| AC-29 | turn-stopping 缺口返工 | D05 | PASS |
| AC-30 | fs/subprocess execution world 不匹配 | C: `test_AC30_filesystem_witness_covers_bytes_path_set_and_declared_sources`, `test_AC30_filesystem_witness_excludes_only_runtime_pins_and_lock`, `test_AC30_filesystem_witness_is_bounded`; B04; D07; D16; D18; W | PASS |
| AC-31 | 插件重载/卸载/升级/重复安装 | D04; D15; P02; P06; P07; P09; P14 | PASS |
| AC-32 | 非 BUGate 工作区 | D06 | PASS |
| AC-33 | legacy Claude/Codex 与脚本回归 | L | PASS |
| AC-34 | CLI 与插件同一输入 | C: `test_CLI_and_library_assessment_semantic_parity`; D01 | PASS |

## 测试索引与验证级别

C = `tests/v2/test_core.py::CoreAcceptance`，72/72 PASS，日志[core/core-tests.log](../../artifacts/deepseek-harness-20261029/core/core-tests.log)，精确映射[core/core-mapping.json](../../artifacts/deepseek-harness-20261029/core/core-mapping.json)。D = `runtime.test.mjs`，B = `bridge.test.mjs`；最终全量22/22 PASS，日志[dsh/all-tests-final.log](../../artifacts/deepseek-harness-20261029/dsh/all-tests-final.log)，映射[dsh/dsh-mapping.json](../../artifacts/deepseek-harness-20261029/dsh/dsh-mapping.json)。原21/22失败保留在[dsh/pre-runtime-directory-failure.log](../../artifacts/deepseek-harness-20261029/dsh/pre-runtime-directory-failure.log)。

- **D01**: `HC-02/04/05/06/07/08/12/13/15 AC-22/23/34 real requests retain canonical context across 50 turns and compaction`
- **D02**: `AC-12/25 multi-workspace same task id has isolated contexts and evidence`
- **D03**: `AC-27/28 managed pre-execute ticket and sync guard preserve denial but permit analysis repair and reads`
- **D04**: `AC-24/31 restart resumes exact binding, reload avoids duplicate tools, binding drift fails visibly`
- **D05**: `AC-29 completion submission rework is bounded and unchanged gaps stop`
- **D06**: `AC-32 non-BUGate workspace does not activate context or guard`
- **D07**: `HC-10/11 AC-09/30 invalid projection/world mapping fails before registration`
- **D08**: `AC-01/17/26 real PTC nested execution persists evidence before result with stable lineage and deduplication`
- **D09**: `HC-09 AC-24/25 actual spawn, fork and independent preset retain global binding and guard`
- **D10**: `HC-10/11 AC-08/09 stale projections and deleted bindings invalidate prior context`
- **D11**: `AC-11/22/27 one task lease rejects competing child implementation and refreshes current model gaps`
- **D12**: `AC-21/27 source-backed authoring starts empty and records digests through the shared core`
- **D13**: `HC-09 AC-22/25 admission race rejects a changed child snapshot before the real model receives it`
- **D14**: `AC-28 configured source probe runs before design without turning zero cases into verification`
- **D15**: `AC-16/31 plugin disposal cancels a real in-flight command and preserves task facts`
- **D16**: `HC-03/10 AC-30 valid and malformed filesystem bindings cannot disagree with the subprocess world`
- **D17**: `HC-05 AC-14 artifact instructions and template expressions remain data and context budgets fail visibly`
- **D18**: `HC-03 AC-30 same-binding filesystem copies with different artifacts, sources or path sets never reach the model`
- **B01**: `AC-15 actual provider rejects missing Python, malformed JSON and oversized output`
- **B02**: `AC-15 envelope operation and exit semantics cannot be forged`
- **B03**: `AC-16 timeout and cancellation terminate real managed processes`
- **B04**: `AC-30 provider has opaque targets and rejects unmapped installation`

- Pnn = [tests/integration_install/accept_package.py](../../tests/integration_install/accept_package.py)中同编号步骤；最终同一包19步生命周期全部PASS，详见[package-final/result.json](../../artifacts/deepseek-harness-20261029/package-final/result.json)与[packaging-summary.json](../../artifacts/deepseek-harness-20261029/packaging-summary.json)。精确步骤和argv由最终commands.json记录。
- I = [tests/integration_install/test_install_boundaries.py](../../tests/integration_install/test_install_boundaries.py)：14/14 PASS，[install-boundaries-final.log](../../artifacts/deepseek-harness-20261029/install-boundaries-final.log)。
- T = 真实目标类型strict编译：exit0，[dsh/typecheck-final.json](../../artifacts/deepseek-harness-20261029/dsh/typecheck-final.json)。
- S = `standalone-package.test.mjs`：最终安装包真实runtime，1/1 PASS：[dsh/installed-result.json](../../artifacts/deepseek-harness-20261029/dsh/installed-result.json)记录27次请求、两个实际执行用例和当前需求验证`satisfied`；[dsh/installed-runtime-final.log](../../artifacts/deepseek-harness-20261029/dsh/installed-runtime-final.log)保存runner结果。
- R = 核心代理独立真实DSH admission race probe，原缺陷与最终结果分别[core/review-dsh-race.json](../../artifacts/deepseek-harness-20261029/core/review-dsh-race.json)、[core/review-dsh-race-final.json](../../artifacts/deepseek-harness-20261029/core/review-dsh-race-final.json)。
- W = 两个独立真实FsLocal/AgentLoop反例：binding不一致、binding相同但brief字节不一致；最终均实际模型请求0，`core/review-dsh-{world,artifact-world}-final.json`。generic runtime子树修改后三个独立probe已再次运行并精确断言exit0。
- L = legacy完整35程序/675 unittest通过，加独立定制验收日志；[legacy/all-tests-final.json](../../artifacts/deepseek-harness-20261029/legacy/all-tests-final.json)、[legacy/test-counts-final.json](../../artifacts/deepseek-harness-20261029/legacy/test-counts-final.json)、[legacy/targeted-final.json](../../artifacts/deepseek-harness-20261029/legacy/targeted-final.json)。clean-tree release archives两步exit0，archive-native为GO；[legacy/repaired-final/archive-final-results.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/archive-final-results.json)及[legacy/repaired-final/bugate-release-acceptance.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/bugate-release-acceptance.json)。

单元/契约、真实DSH runtime及最终安装包是不同证明层级。DSH测试仅模型边界使用确定性fixture；AgentLoop、工具管线、PTC、spawn/fork、压缩、session持久化、filesystem和subprocess均使用实际目标实现，生成的通用SUT执行真实命令。**Live-model：NOT_RUN（非本合同必需条件），不声称生产模型效果。** 未提交真实公司SUT、秘密或凭据。

## 实施审查发现与闭合

缺陷原始记录：[docs/defects/BUGATE-DSH-2026-10-29-IMPLEMENTATION-REVIEW.md](../../docs/defects/BUGATE-DSH-2026-10-29-IMPLEMENTATION-REVIEW.md)。这些是BUGate基础设施缺陷，不冒称产品SUT缺陷；原失败证据保留。

| ID | 缺陷及修正对象 | 精确闭合测试/证据 |
|---|---|---|
| DSH-R01 | 缓存绑定漏检lock变化 | test_AC11_binding_change_detected_in_existing_workspace / test_AC11_change_between_evaluation_and_commit_is_conflict |
| DSH-R02 | 合法重复argv被拒绝 | test_repeated_argv_is_valid |
| DSH-R03 | legacy Skill误作DSH投影 | Canonical method-generated projection; D07/D10; P05 |
| DSH-R04 | 首次半安装无法回滚 | P11/P12/P13; I |
| DSH-R05 | 缩小task范围可规避分母 | test_frozen_scope_cannot_be_shrunk / test_scope_task_risk_reduction_rejected |
| DSH-R06 | implementation交付漏设计依赖 | test_implementation_delivery_applies_design_dependencies / test_knowledge_only_delivery_does_not_require_execution_report |
| DSH-R07 | legacy开发fallback纳入node_modules符号链接 | L: full 35-program rerun; test_init_scaffold-final.log; 10-fixed.log |
| DSH-R08 | 新增SUT输入未失效旧执行 | test_AC08_new_runtime_input_invalidates_prior_execution / test_AC09_changed_oracle_cannot_reuse_prior_execution |
| DSH-R09 | 报告自报名单冒充完整执行 | test_delivery_cannot_list_unexecuted_case_as_complete; independent-core-audit/verified_r09_r11.json |
| DSH-R10 | 诊断和关系悬空引用被接受 | test_diagnosis_and_relation_dangling_references_rejected; independent-core-audit/verified_r09_r11.json |
| DSH-R11 | JUnit聚合与用例观察冲突 | test_junit_aggregate_mismatch_is_not_pass; independent-core-audit/verified_r09_r11.json |
| DSH-R12 | 历史失败误阻塞合法重跑和设计 | test_historical_failure_does_not_block_fixed_current_run; independent-core-audit/verified_r12.json |
| DSH-R13 | Oracle存在但所属命题错误 | test_testability_oracles_cannot_be_swapped_between_propositions / test_multi_proposition_case_requires_oracle_for_each_proposition; independent-core-audit/verified_r13.json |
| DSH-R14 | 子Agent写入使父Agent推理上下文失效 | R: review-dsh-race-final.json; D13 |
| DSH-R15 | filesystem与Python读取不同工作区 | W: review-dsh-world-final.json + review-dsh-artifact-world-final.json; D16/D18; generic runtime directory exclusion; final exact-dependency 22/22 and all three independent probes PASS |
| DSH-R16 | 文件witness误纳入DSH原子临时目录，合法spawn失败 | [dsh/pre-runtime-directory-failure.log](../../artifacts/deepseek-harness-20261029/dsh/pre-runtime-directory-failure.log) → [dsh/all-tests-final.log](../../artifacts/deepseek-harness-20261029/dsh/all-tests-final.log) 22/22; D09; core runtime exclusions test; independent probes |
| DSH-R17 | 归档污染检查发现builder包含本机home常量；改为Path.home动态检查并保留原失败产物 | `c96622502475504d9670478ee9cd0d72f4787120`; [legacy/repaired-final/archive-final-results.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/archive-final-results.json); [legacy/repaired-final/bugate-release-acceptance.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/bugate-release-acceptance.json) GO |

DSH-R16保留有限manifest引入的并发临时目录失败：先前21/22不是通过。宿主记账已移入专用runtime目录，最终22/22及三个独立probe均重跑通过；draft、claim与evidence仍参与文件一致性检查。

## 兼容性与真实边界

legacy Claude/Codex配置、原脚本的stdlib-only依赖、init/updater/transaction/release契约通过主回归；修正仅从开发fallback排除node_modules依赖树，其他危险symlink检查保留。CLAUDE符号链接保持；新v2 console entry没有混入legacy vendor bin。最终archive原生验收基于干净source-end commit构建，tar/zip均GO；使用synthetic CI runtime，不等于live Claude/Codex会话。

本次实际验证macOS arm64/Python3.13.3/Node26.8.1；Linux CI已配置但本地未执行；Windows因POSIX process groups/fcntl不在此分发支持范围。离线wheelhouse绑定记录的ABI/平台/架构，错配明确拒绝。远程执行世界和浏览器Python未声称支持；provider不一致明确失败。

哈希证明一致性，不证明日志执行真实；语义review是有来源的判断，不是数学证明。本地受管执行也不防所有同权限文件owner同时改证据与收据；受管门禁覆盖声明的工具面，不是全机器沙盒。不遵守锁的外部写入不受这些事务保护；发现漂移时拒绝，不承诺与任意外部写入原子串行。

## 未完成项与验证边界

合同必需实现与适用验收无未完成项，无已证明的外部阻塞。公开注册表发布、合并主分支和生产部署未授权且未执行，不属于本次完成条件。Live-model为NOT_RUN，不是本合同的必需项。

代码交付的source end为`c96622502475504d9670478ee9cd0d72f4787120`；此后报告与证据整理可有独立文档commit，不改变本报告记录的受测实现与安装产物摘要。
