# BUGate DeepSeek Harness implementation delivery report

[简体中文](DEEPSEEK_HARNESS_DELIVERY_2026-10-29.zh-CN.md)

**Overall status: COMPLETE.** All 49 contractual HC/AC items have passing evidence: core 72/72, real DSH native 22/22 with all 13 declared dependencies exact, installed runtime 1/1, installation boundaries 14/14, final package lifecycle 19/19, the complete 35-program legacy loop with 675 unittest cases, and both clean-tree archive steps. The installed real DSH runtime used a deterministic model fixture, made 27 actual requests, ran two real test cases and produced a current `requirement_verified: satisfied` assessment. Negative controls were rejected as required; infrastructure findings and original failures remain preserved.

Evidence is under the [delivery evidence directory](../../artifacts/deepseek-harness-20261029/), with the [final 49-item machine matrix](../../artifacts/deepseek-harness-20261029/acceptance-final.json). This establishes the contracted engineering delivery for the stated support target, not live-model effectiveness or untested-platform support.

## Implemented scope and decisions

- [src/bugate/](../../src/bugate/) contains a host-neutral Python core, source/reference hashes, locked Profile scope, claim assessment, stable errors, atomic persistence, conflict detection, actual execution capture and PreparedProtocolContext. Nine core Schema kinds plus MethodSpec, ExecutionRecord and CommandResult have positive/negative examples. Seven MethodSpecs cover understanding, testability, design, implementation, execution, diagnosis and knowledge.
- [protocol/v2/](../../protocol/v2/) is canonical for Schemas, rules, reason codes, methods and bilingual contracts. Mature jsonschema Draft202012 validation is used. Adapter and installer errors are registered without inventing a second quality evaluator.
- The CLI implements init, prepare, status, validate, assess, artifact/evidence record, execution run and DSH setup/doctor/remove. CommandResult separates invocation from quality; exits 0/3/2/1/124/130 identify command success, unmet quality, invalid input, runtime failure, timeout and cancellation. Console entry and `python -m bugate` are supported; no unusable v2 shim was added to the legacy vendor tree.
- [adapters/deepseek-harness/plugin/](../../adapters/deepseek-harness/plugin/) implements real apply(ctx), seven thin tools, canonical Skill/template projection, lifecycle recovery, managed writes/execution and bounded turn-stopping rework. DSH retains Agent scheduling; BUGate adds no loop, current-stage authority or mandatory memory service.
- Python uses configured DSH filesystem/subprocess providers; opaque targets are never interpreted as host paths. context.text and guards remain synchronous. Awaitable lifecycle seams perform preparation/assessment; tools/result is observation only and critical evidence publication is awaited before return.
- Identity separates identical task IDs across projects. Claims/results do not recursively change the quality snapshot, while request digests bind the entire current Claim. Profile/source/rule/script/Oracle drift invalidates old conclusions. Execution applicability compares the complete relevant input path set, including new configuration files.
- Imported/model/human facts never become host_observed through a field. A managed wrapper executes argv and captures process state, logs, JUnit and per-case observations; aggregates and input applicability are checked. Product failure can satisfy complete testing delivery without verifying the failed requirement. Zero/skip/xfail is not a passing requirement. Historical failures remain preserved without blocking unrelated/new scoped claims.
- A separate core-owned finite filesystem_manifest checks raw bytes and path sets across providers. It includes drafts, claims, assessments and Profile sources, bounded to 2,048 files, 2,048 directories and 32 MiB. Only write.lock and `.bugate/runtime` bookkeeping are excluded. This transport witness is not a quality snapshot or adapter-defined business rule.
- Packaging includes compiled JS, native patch, canonical resources, generated Skill, Python wheel and offline wheelhouse. Ownership, pending journals, upgrades/rollback and user configuration preservation have boundary and real-CLI acceptance tests.

The nine core Schema kinds are ProtocolBinding, TestTask, Artifact, Evidence, Claim, AssessmentRequest, AssessmentResult, Profile and PreparedProtocolContext. Supplementary kinds are MethodSpec, ExecutionRecord and CommandResult. All twelve have per-kind required-field, wrong-kind/version and unknown-property negative tests and pass Draft202012Validator.check_schema. The seven MethodSpecs do not encode scheduling.

## Versions and environment

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

Evidence: [environment-final.json](../../artifacts/deepseek-harness-20261029/environment-final.json), [core/dependencies.json](../../artifacts/deepseek-harness-20261029/core/dependencies.json), [dsh/dependency-probe.md](../../artifacts/deepseek-harness-20261029/dsh/dependency-probe.md). The user's global DSH 0.1.5-rc.1 was not upgraded; testing uses an isolated exact target. `CLAUDE.md -> AGENTS.md` remains a symbolic link.

All 13 final native-test dependencies have expected==actual, including Ajv8.17.1, TypeScript5.9.3 and Node types24.10.0: [dsh/exact-dependencies.json](../../artifacts/deepseek-harness-20261029/dsh/exact-dependencies.json), [dsh/typecheck-final.json](../../artifacts/deepseek-harness-20261029/dsh/typecheck-final.json). Earlier Ajv8.20 results remain in [dsh/all-tests-ajv-8.20.log](../../artifacts/deepseek-harness-20261029/dsh/all-tests-ajv-8.20.log) and do not substitute for exact-version evidence. The installed package also resolves Ajv8.17.1.

The main implementation commit is `eac4bd3780975f1b161928625285e6ac3bddc70b`; source end `c96622502475504d9670478ee9cd0d72f4787120` adds only the one-line portable-builder repair. The native tgz/wheel are the identical bytes actually installed and accepted, with hashes below; legacy tar/zip archives were built from source end.

## Executed commands and user entrypoints

[core/cli-commands-final.json](../../artifacts/deepseek-harness-20261029/core/cli-commands-final.json) and [core/cli-final/](../../artifacts/deepseek-harness-20261029/core/cli-final/) retain real argv and JSON for version, idempotent init, prepare, status, validate, requirement verification and task delivery. Artifact/evidence/execution entrypoints are also executed by core and real-runtime tests.

```sh
PYTHONPATH=src "$BUGATE_PYTHON" tests/v2/test_core.py
node adapters/deepseek-harness/plugin/node_modules/typescript/bin/tsc --noEmit -p adapters/deepseek-harness/plugin/tsconfig.json
BUGATE_TEST_PYTHON="$BUGATE_PYTHON" BUGATE_DSH_MODULES="$DSH_MODULES" node --test adapters/deepseek-harness/tests/bridge.test.mjs adapters/deepseek-harness/tests/runtime.test.mjs
python3 tests/integration_install/test_install_boundaries.py
```

The following commands normalize executed syntax. Variables explicitly select the Python, DSH, bootstrap, external project, Profile and task paths; exact recorded argv are in [package-final/commands.json](../../artifacts/deepseek-harness-20261029/package-final/commands.json).

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

## Installable artifacts and checksums

| Artifact | Version/contents | Final path and SHA-256 |
|---|---|---|
| Native tgz | @bugate/deepseek-harness 2.0.0-dev.1, compiled JS and resources | [dist/deepseek-harness/bugate-deepseek-harness-2.0.0-dev.1.tgz](../../dist/deepseek-harness/bugate-deepseek-harness-2.0.0-dev.1.tgz) / `0a4f7ddbcb0a035f603823f82da14e399ffa6bd236afeed3879625e8f626baab` |
| Python wheel | bugate-core 2.0.0a1, protocol and legacy resources | [dist/deepseek-harness/bugate_core-2.0.0a1-py3-none-any.whl](../../dist/deepseek-harness/bugate_core-2.0.0a1-py3-none-any.whl) / `ceffa043485f3383b8ecc03b71a273e5f646a7c7c2ad898bf965f18d10ec4e02` |
| Bootstrap installer | stdlib installer; offline Python bootstrap | [install_dsh.py](../../dist/deepseek-harness/install_dsh.py) / `5680d650f4e72e705e19f3e3618076999341e58f84eefe94a6aceff5b7f3bdd2` |
| Offline dependencies/bootstrap | wheelhouse, install_dsh.py, inventory, SHA256SUMS | [dist/deepseek-harness/build-manifest.json](../../dist/deepseek-harness/build-manifest.json), [dist/deepseek-harness/SHA256SUMS](../../dist/deepseek-harness/SHA256SUMS), tgz `package/python/inventory.json` |
| legacy tar | 0.4.5 | [bugate-0.4.5.tar.gz](../../dist/legacy-regression/bugate-0.4.5.tar.gz) / `8e610a15bbd9a34c2de6b854586baabc446b5fe934d5502ed43ee0b482ed8a9a` |
| legacy zip | 0.4.5 | [bugate-0.4.5.zip](../../dist/legacy-regression/bugate-0.4.5.zip) / `386bec9dff3d7e21eefa61affc6828135275edc47f7f2718022763f20047a3cf` |

Do not substitute `packages-iteration*` hashes for the final deliverables. Final clean installation removed PYTHONPATH/NODE_PATH, loaded installed Python/plugin/Schema/Skill, and completed real authoring, design assessment, implementation, captured execution and requirement verification. Doctor's hydration=unobserved is not proof of a real session.

## All HC/AC acceptance items

All 49 original contractual meanings are retained, with PASS for every item. Test aliases resolve in the following index. Exact assertions, commands and logs are mapped by [core/core-mapping.json](../../artifacts/deepseek-harness-20261029/core/core-mapping.json), [dsh/dsh-mapping.json](../../artifacts/deepseek-harness-20261029/dsh/dsh-mapping.json), [packaging-mapping.json](../../artifacts/deepseek-harness-20261029/packaging-mapping.json) and the final matrix.

| ID | Contract requirement | Executed test/selector references | Observed result |
|---|---|---|---|
| HC-01 | Idempotent setup | P02 | PASS |
| HC-02 | Canonical protocol discovery | C: `test_HC02_04_05_canonical_schema_binding_context`; D01; P03; S | PASS |
| HC-03 | Workspace and task identity | C: `test_HC03_task_ambiguity_is_explicit`, `test_AC12_same_task_id_different_workspace`; D02; D16; D18; W | PASS |
| HC-04 | Exact version/digest binding | C: `test_HC02_04_05_canonical_schema_binding_context`, `test_AC09_protocol_mismatch`, `test_AC09_profile_mismatch`; D01; D04 | PASS |
| HC-05 | Schema validation before context injection | C: `test_HC02_04_05_canonical_schema_binding_context`, `test_all_schemas_positive_and_negative_examples`; D01; D17 | PASS |
| HC-06 | Applicable context before relevant inference | D01; D13; R | PASS |
| HC-07 | Context maintained over long tasks | D01 | PASS |
| HC-08 | Rehydrate after compaction/restart | D01; D04 | PASS |
| HC-09 | Independent child binding | D09; D13 | PASS |
| HC-10 | Visible version/digest failures | C: `test_AC09_protocol_mismatch`, `test_AC09_profile_mismatch`; D07; D10; D16 | PASS |
| HC-11 | Detect stale projections | D07; D10; P05 | PASS |
| HC-12 | Explicit assessment | C: `test_CLI_envelope_operations_and_quality_exit`; D01 | PASS |
| HC-13 | Adapter preserves core semantics | C: `test_CLI_and_library_assessment_semantic_parity`; D01 | PASS |
| HC-14 | Ownership-limited rollback | P07; P09; P13; P18 | PASS |
| HC-15 | History cannot override current facts | C: `test_HC15_historical_memory_cannot_override_current_facts`, `test_AC18_no_memory_service_dependency`; D01 | PASS |
| AC-01 | Generic SUT traceable full workflow | C: `test_AC01_complete_traceable_real_execution`; D08; D12; S | PASS |
| AC-02 | Missing Oracle/business semantics | C: `test_AC02_missing_oracle_rejected`, `test_AC02_semantics_require_current_review`, `test_AC02_stale_review_rejected` | PASS |
| AC-03 | Code without execution | C: `test_AC03_code_without_execution` | PASS |
| AC-04 | Exit zero with zero/skipped tests | C: `test_AC04_zero_tests_exit_zero`, `test_AC04_all_skipped_exit_zero`, `test_AC04_all_xfail_not_verified` | PASS |
| AC-05 | Real product failure and complete delivery | C: `test_AC05_real_product_failure_completes_delivery_not_verification` | PASS |
| AC-06 | Forged passed/verified/exit fields | C: `test_AC06_import_cannot_upgrade_source`, `test_AC06_forged_host_record_without_receipt` | PASS |
| AC-07 | Imported historical/third-party logs | C: `test_AC07_external_history_retains_provenance` | PASS |
| AC-08 | Changed/truncated/deleted inputs | C: `test_AC08_evidence_truncated`, `test_AC08_evidence_deleted`, `test_AC08_same_path_implementation_changed`, `test_AC08_new_runtime_input_invalidates_prior_execution`; D10 | PASS |
| AC-09 | Protocol/Profile/rule/script drift | C: `test_AC09_protocol_mismatch`, `test_AC09_profile_mismatch`, `test_AC09_source_revision_mismatch`, `test_AC09_changed_oracle_cannot_reuse_prior_execution`; D07; D10 | PASS |
| AC-10 | Derived output does not self-invalidate | C: `test_AC10_assessment_outputs_do_not_self_invalidate`, `test_AC10_current_claim_mutation_cannot_reuse_old_satisfaction`, `test_AC10_forged_result_not_authority` | PASS |
| AC-11 | Concurrent evaluation/commit mutation | C: `test_AC11_expected_snapshot_conflict`, `test_AC11_binding_change_detected_in_existing_workspace`, `test_AC11_concurrent_lock_rejects_second_writer`, `test_AC11_change_between_evaluation_and_commit_is_conflict`; D11 | PASS |
| AC-12 | Same task ID in separate workspaces | C: `test_AC12_same_task_id_different_workspace`; D02 | PASS |
| AC-13 | Traversal/symlink/unauthorized writes | C: `test_AC13_traversal_and_symlink`, `test_AC13_report_symlink_created_during_execution_is_rejected`; I; D03 | PASS |
| AC-14 | Instructions/templates/long artifact text | C: `test_AC14_content_is_data_and_utf8_budget`; D17 | PASS |
| AC-15 | Missing Python/errors/invalid/large output | C: `test_AC15_missing_process`, `test_AC15_invalid_json_cli`, `test_AC15_output_limit`, `test_AC15_missing_python_package_has_structured_error`; B01; B02 | PASS |
| AC-16 | Timeout and cancellation | C: `test_AC16_timeout_terminates_process`, `test_AC16_cancel_cli_terminates_process`, `test_AC16_cancel_terminates_process_group_child`; B03; D15 | PASS |
| AC-17 | Failed/partial/repeated evidence writes | C: `test_AC17_run_idempotency`, `test_AC17_unknown_execution_is_not_replayed`, `test_AC17_atomic_failure_keeps_previous`, `test_AC17_evidence_publication_failure_does_not_authorize`; D08 | PASS |
| AC-18 | No memory service | C: `test_AC18_no_memory_service_dependency`; D01 | PASS |
| AC-19 | Real bundle installed in clean profile | P01; S | PASS |
| AC-20 | Independent of source checkout | P01; P03; S | PASS |
| AC-21 | Compile against exact DSH types | T; D12; S | PASS |
| AC-22 | Current context in actual model requests | D01; D11; D13; R | PASS |
| AC-23 | 50 turns and actual compaction | D01 | PASS |
| AC-24 | Cold resume and preset changes | D04; D09 | PASS |
| AC-25 | Spawn/fork/independent preset children | D02; D09; D13 | PASS |
| AC-26 | Nested/PTC attribution and deduplication | D08 | PASS |
| AC-27 | Awaited pre-execute and synchronous guard | D03; D11; D12 | PASS |
| AC-28 | Reads, analysis and legal exploration remain available | D03; D14 | PASS |
| AC-29 | Bounded completion rework | D05 | PASS |
| AC-30 | Filesystem/subprocess world mismatch | C: `test_AC30_filesystem_witness_covers_bytes_path_set_and_declared_sources`, `test_AC30_filesystem_witness_excludes_only_runtime_pins_and_lock`, `test_AC30_filesystem_witness_is_bounded`; B04; D07; D16; D18; W | PASS |
| AC-31 | Reload/unload/upgrade/reinstall | D04; D15; P02; P06; P07; P09; P14 | PASS |
| AC-32 | Non-BUGate workspace unaffected | D06 | PASS |
| AC-33 | Legacy Claude/Codex/scripts | L | PASS |
| AC-34 | CLI/plugin semantic parity | C: `test_CLI_and_library_assessment_semantic_parity`; D01 | PASS |

## Test selectors and verification levels

C = `tests/v2/test_core.py::CoreAcceptance`, 72/72 PASS, [core/core-tests.log](../../artifacts/deepseek-harness-20261029/core/core-tests.log), exact mapping [core/core-mapping.json](../../artifacts/deepseek-harness-20261029/core/core-mapping.json). D = runtime.test.mjs and B = bridge.test.mjs; final native 22/22 PASS is recorded in [dsh/all-tests-final.log](../../artifacts/deepseek-harness-20261029/dsh/all-tests-final.log) and [dsh/dsh-mapping.json](../../artifacts/deepseek-harness-20261029/dsh/dsh-mapping.json). The original failure remains in [dsh/pre-runtime-directory-failure.log](../../artifacts/deepseek-harness-20261029/dsh/pre-runtime-directory-failure.log).

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

- Pnn: corresponding numbered step of [tests/integration_install/accept_package.py](../../tests/integration_install/accept_package.py). The final identical package passed all 19 lifecycle steps: [package-final/result.json](../../artifacts/deepseek-harness-20261029/package-final/result.json) and [packaging-summary.json](../../artifacts/deepseek-harness-20261029/packaging-summary.json). Its commands.json contains exact argv.
- I: `test_install_boundaries.py`, 14/14 PASS, [install-boundaries-final.log](../../artifacts/deepseek-harness-20261029/install-boundaries-final.log).
- T: strict compilation against actual target types, exit0, [dsh/typecheck-final.json](../../artifacts/deepseek-harness-20261029/dsh/typecheck-final.json).
- S: `standalone-package.test.mjs`; final installed runtime 1/1 PASS: [dsh/installed-result.json](../../artifacts/deepseek-harness-20261029/dsh/installed-result.json) records 27 requests, two actually executed cases and a current satisfied requirement claim; [dsh/installed-runtime-final.log](../../artifacts/deepseek-harness-20261029/dsh/installed-runtime-final.log) records the runner result.
- R: independent real-DSH admission race, original [core/review-dsh-race.json](../../artifacts/deepseek-harness-20261029/core/review-dsh-race.json), corrected result [core/review-dsh-race-final.json](../../artifacts/deepseek-harness-20261029/core/review-dsh-race-final.json).
- W: independent real FsLocal/AgentLoop provider disagreements: different binding, or identical binding with differing brief bytes. Both final probes sent zero actual model requests. `core/review-dsh-{world,artifact-world}-final.json`; all three independent probes were rerun with exact exit-zero assertions after the generic runtime-directory change.
- L: full legacy loop, 35 programs / 675 unittest cases plus separately summarized custom programs, [legacy/all-tests-final.json](../../artifacts/deepseek-harness-20261029/legacy/all-tests-final.json), [legacy/test-counts-final.json](../../artifacts/deepseek-harness-20261029/legacy/test-counts-final.json), [legacy/targeted-final.json](../../artifacts/deepseek-harness-20261029/legacy/targeted-final.json). Both clean-tree archive steps exited zero, with archive-native GO: [legacy/repaired-final/archive-final-results.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/archive-final-results.json) and [legacy/repaired-final/bugate-release-acceptance.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/bugate-release-acceptance.json).

Unit/contracts, real runtime and installed-package proof are distinct. Only the LLM boundary uses a deterministic fixture; actual AgentLoop, tool pipeline, PTC, spawn/fork, compaction, session persistence, filesystem and subprocess services execute generic generated SUT commands. **Live model: NOT_RUN (optional under this contract); no live-model effectiveness claim.** No company SUT, credentials or secrets are committed.

## Review findings and closure

Original findings are preserved in [docs/defects/BUGATE-DSH-2026-10-29-IMPLEMENTATION-REVIEW.md](../../docs/defects/BUGATE-DSH-2026-10-29-IMPLEMENTATION-REVIEW.md). These are BUGate infrastructure defects, not product defects; failing evidence is retained.

| ID | Defect/fix target | Exact closure tests/evidence |
|---|---|---|
| DSH-R01 | Cached binding missed lock changes | test_AC11_binding_change_detected_in_existing_workspace / test_AC11_change_between_evaluation_and_commit_is_conflict |
| DSH-R02 | Repeated argv incorrectly rejected | test_repeated_argv_is_valid |
| DSH-R03 | Legacy Skill used as DSH projection | Canonical method-generated projection; D07/D10; P05 |
| DSH-R04 | First partial installation could not roll back | P11/P12/P13; I |
| DSH-R05 | Editable task scope reduced denominator | test_frozen_scope_cannot_be_shrunk / test_scope_task_risk_reduction_rejected |
| DSH-R06 | Implementation delivery skipped design dependencies | test_implementation_delivery_applies_design_dependencies / test_knowledge_only_delivery_does_not_require_execution_report |
| DSH-R07 | Legacy development fallback included node_modules symlink | L: full 35-program rerun; test_init_scaffold-final.log; 10-fixed.log |
| DSH-R08 | New SUT input escaped old execution digest map | test_AC08_new_runtime_input_invalidates_prior_execution / test_AC09_changed_oracle_cannot_reuse_prior_execution |
| DSH-R09 | A report named cases never executed | test_delivery_cannot_list_unexecuted_case_as_complete; independent-core-audit/verified_r09_r11.json |
| DSH-R10 | Dangling diagnosis/relation references accepted | test_diagnosis_and_relation_dangling_references_rejected; independent-core-audit/verified_r09_r11.json |
| DSH-R11 | JUnit aggregate contradicted case observations | test_junit_aggregate_mismatch_is_not_pass; independent-core-audit/verified_r09_r11.json |
| DSH-R12 | Historical failure blocked legitimate rerun/design | test_historical_failure_does_not_block_fixed_current_run; independent-core-audit/verified_r12.json |
| DSH-R13 | Oracle IDs existed but belonged to other propositions | test_testability_oracles_cannot_be_swapped_between_propositions / test_multi_proposition_case_requires_oracle_for_each_proposition; independent-core-audit/verified_r13.json |
| DSH-R14 | Child write invalidated parent inference context | R: review-dsh-race-final.json; D13 |
| DSH-R15 | Filesystem and Python observed different workspaces | W: review-dsh-world-final.json + review-dsh-artifact-world-final.json; D16/D18; generic runtime directory exclusion; final exact-dependency 22/22 and all three independent probes PASS |
| DSH-R16 | Filesystem witness included atomic bookkeeping directories and broke valid spawn | [dsh/pre-runtime-directory-failure.log](../../artifacts/deepseek-harness-20261029/dsh/pre-runtime-directory-failure.log) → [dsh/all-tests-final.log](../../artifacts/deepseek-harness-20261029/dsh/all-tests-final.log) 22/22; D09; core runtime exclusions test; independent probes |
| DSH-R17 | Archive pollution scan found a machine-home literal in the builder; use dynamic Path.home and retain original failure artifacts | `c96622502475504d9670478ee9cd0d72f4787120`; [legacy/repaired-final/archive-final-results.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/archive-final-results.json); [legacy/repaired-final/bugate-release-acceptance.json](../../artifacts/deepseek-harness-20261029/legacy/repaired-final/bugate-release-acceptance.json) GO |

DSH-R16 preserves the finite-witness concurrency failure: the previous 21/22 was not PASS. Host bookkeeping now lives in the dedicated runtime directory; final 22/22 and all three independent probes were rerun successfully. Drafts, claims and evidence remain compared.

## Compatibility and assurance limits

The main regression covers legacy Claude/Codex configuration, stdlib-only scripts, init/updater/transactions/release contracts. The development fallback now excludes node_modules dependencies while preserving other unsafe-link checks. CLAUDE's symlink remains intact; the new v2 entrypoint is not accidentally shipped in legacy bin. Clean-commit tar/zip archive-native acceptance is GO. It uses a synthetic CI runtime and does not establish live Claude/Codex-session behavior.

Executed platform: macOS arm64/Python3.13.3/Node26.8.1. Linux CI is configured but not locally executed. Windows is outside this distribution's supported target because process groups and fcntl are POSIX. Offline wheels are bound to their recorded ABI/platform/architecture; mismatch is rejected. Remote execution worlds and browser Python are not claimed; mismatching providers fail explicitly.

Hashes establish consistency, not the truth of a preexisting log. Semantic reviews are sourced judgments, not mathematical proofs. Local capture does not defend against an actor controlling both evidence and receipts with the same permissions. Managed guards cover the stated tool surface, not the entire machine. Arbitrary writers that ignore locks do not receive a global transaction guarantee.

## Remaining work and verification boundaries

No required implementation or applicable acceptance item remains incomplete, and no external blocker is outstanding. Public registry publication, main-branch merge and production deployment were not authorized or performed and are not completion prerequisites. Live-model testing is NOT_RUN and optional under this contract.

The delivered source end is `c96622502475504d9670478ee9cd0d72f4787120`. Later report/evidence commits may be recorded separately; they do not change the tested implementation or package digests in this report.
