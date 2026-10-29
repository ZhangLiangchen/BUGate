# Current-tree retirement and replacement inventory

User authorization: 2026-10-29 v2 + DeepSeek Harness-only destructive architecture upgrade. Git objects, existing tags and published releases are unchanged. Only tracked files listed below were removed; ignored local state and the nested `.claude/worktrees/bugate-stage-11-s10-d02-6c5b49` checkout were preserved.

| Retired surface | Current replacement / disposition |
|---|---|
| Codex / Claude hooks, plugins, skills, gate agents and placeholders | No replacement; these product hosts are out of scope. |
| Legacy installer, updater, aliases, release archives and their dedicated tests | Native DSH setup/doctor/remove and offline v2 bundle lifecycle tests. v0.x migration unsupported. |
| Role processes, orchestrator, self-heal loop, memory bus and daemon wrappers | DSH owns execution, scheduling and lifecycle. No BUGate loop or required memory service. |
| Legacy semantic scripts and YAML/Markdown artifact templates | Native v2 Artifact Schema, MethodSpec, reason codes and Python assessment; quality regressions remain mandatory. |
| Dual-route/planned architecture and historical operational docs | Current bilingual v2 guides; historical content remains in Git and release/tag history. |
| Broad scripts/ wheel payload | Finite canonical protocol resource inventory plus Python v2 package only. |

## Removed tracked paths

- `.agents/plugins/marketplace.json`
- `.agents/skills/bugate`
- `.agents/skills/bugate-full-check`
- `.agents/skills/bugate-import`
- `.agents/skills/bugate-update`
- `.claude-plugin/marketplace.json`
- `.claude-plugin/plugin.json`
- `.claude/agents`
- `.claude/commands`
- `.claude/launch.json`
- `.claude/settings.json`
- `.claude/skills/bugate`
- `.claude/skills/bugate-full-check`
- `.claude/skills/bugate-import`
- `.claude/skills/bugate-update`
- `.codex-plugin/plugin.json`
- `.codex/README.md`
- `.codex/agents`
- `.codex/config.toml`
- `.codex/hooks.json`
- `.codex/skills/bugate`
- `.codex/skills/bugate-full-check`
- `.codex/skills/bugate-import`
- `.codex/skills/bugate-update`
- `.shared/skills/bugate-full-check/SKILL.md`
- `.shared/skills/bugate-full-check/scripts/run_full_check.py`
- `.shared/skills/bugate-import/SKILL.md`
- `.shared/skills/bugate-import/references/field-guide.md`
- `.shared/skills/bugate-import/references/updating-bugate.md`
- `.shared/skills/bugate-import/references/updating-bugate.zh-CN.md`
- `.shared/skills/bugate-import/references/using-bugate.md`
- `.shared/skills/bugate-import/references/using-bugate.zh-CN.md`
- `.shared/skills/bugate-update/SKILL.md`
- `.shared/skills/bugate-update/agents/openai.yaml`
- `.shared/skills/bugate/LAYOUT.md`
- `.shared/skills/bugate/SKILL.md`
- `.shared/skills/bugate/adapters/claude/agents/brief-gate.md`
- `.shared/skills/bugate/adapters/claude/agents/inventory-gate.md`
- `.shared/skills/bugate/adapters/claude/agents/testability-gate.md`
- `.shared/skills/bugate/adapters/claude/commands/bugate.md`
- `.shared/skills/bugate/adapters/claude/commands/integrate-qa-methodology.md`
- `.shared/skills/bugate/adapters/claude/commands/sdtd-adversarial.md`
- `.shared/skills/bugate/adapters/claude/commands/sdtd-multiview.md`
- `.shared/skills/bugate/adapters/codex/agents/brief-gate.toml`
- `.shared/skills/bugate/adapters/codex/agents/inventory-gate.toml`
- `.shared/skills/bugate/adapters/codex/agents/testability-gate.toml`
- `.shared/skills/bugate/adapters/codex/sdtd-adversarial.md`
- `.shared/skills/bugate/adapters/codex/sdtd-multiview.md`
- `.shared/skills/bugate/integration/commands/integrate-qa-methodology.md`
- `.shared/skills/bugate/integration/scratch-output/README.md`
- `.shared/skills/bugate/references/business-understanding-gate.md`
- `.shared/skills/bugate/references/case-blueprint.md`
- `.shared/skills/bugate/references/profile-schema.md`
- `.shared/skills/bugate/references/sdtd-constitution.md`
- `.shared/skills/bugate/references/test-design-gate.md`
- `.shared/skills/bugate/references/testability-gate.md`
- `.shared/skills/bugate/templates/01_business_brief.md`
- `.shared/skills/bugate/templates/01a_domain_model.md`
- `.shared/skills/bugate/templates/01b_state_flow.md`
- `.shared/skills/bugate/templates/02_testability.md`
- `.shared/skills/bugate/templates/02a_test_dimension_matrix.yaml`
- `.shared/skills/bugate/templates/03_inventory.yaml`
- `.shared/skills/bugate/templates/03a_test_cases.md`
- `.shared/skills/bugate/templates/03b_adversarial_cases.yaml`
- `.shared/skills/bugate/templates/04_execution_report.md`
- `.shared/skills/bugate/templates/05_knowledge_update.md`
- `CLAUDE.md`
- `IMPORT_PROMPT.md`
- `IMPORT_PROMPT.zh-CN.md`
- `UPDATE_PROMPT.md`
- `UPDATE_PROMPT.zh-CN.md`
- `adapters/claude-code/README.md`
- `adapters/claude-code/manifest.yaml`
- `adapters/claude-code/plugin/README.md`
- `adapters/claude-code/tests/README.md`
- `adapters/codex/README.md`
- `adapters/codex/manifest.yaml`
- `adapters/codex/plugin/README.md`
- `adapters/codex/tests/README.md`
- `adapters/pi/README.md`
- `adapters/pi/manifest.yaml`
- `adapters/pi/package/README.md`
- `adapters/pi/tests/README.md`
- `agents`
- `bin/bugate-role`
- `bin/bugate-update`
- `bin/memory-bus-ensure`
- `bin/memory-bus-install-launchd`
- `bin/memory-bus-start`
- `bin/memory-bus-status`
- `bin/memory-bus-stop`
- `bin/memory-handoff`
- `bin/memory-model-fetch`
- `bin/memory-recent`
- `bin/memory-service-archive`
- `bin/memory-service-lint`
- `bin/memory-service-note`
- `bin/memory-service-search`
- `bin/promote-memory`
- `bin/wave8-weekly`
- `bugate.config.yaml`
- `context/README.md`
- `context/providers/powercontext/README.md`
- `docs/IMPORT-FIELD-GUIDE.md`
- `docs/SETUP-OPTIONAL.md`
- `docs/case-studies/origin-sut-import.md`
- `docs/defects/BUGATE-CORE-2026-07-20-CORE-CONFIG-ALIASES.md`
- `docs/defects/BUGATE-CORE-2026-07-20-FULL-CHECK-FIXTURE-PROFILE-LEAK.md`
- `docs/defects/BUGATE-CORE-2026-07-20-WAVE8-NESTED-CONFIG.md`
- `docs/defects/BUGATE-CORE-2026-07-21-UPDATER-TRANSACTION-INTEGRITY.md`
- `docs/defects/BUGATE-CORE-2026-07-22-RELEASE-ACCEPTANCE-INTEGRITY.md`
- `docs/defects/BUGATE-CORE-2026-07-22-ROLE-GOVERNANCE-STATE-INTEGRITY.md`
- `docs/defects/BUGATE-CORE-2026-07-22-UPDATER-ROLLBACK-OPERATOR-PATH.md`
- `docs/defects/BUGATE-CORE-2026-07-23-RELEASE-ACCEPTANCE-TRANSITION-COUNT.md`
- `docs/defects/BUGATE-CORE-2026-07-23-ROLE-EVIDENCE-RESET.md`
- `docs/defects/BUGATE-DSH-2026-10-29-IMPLEMENTATION-REVIEW.md`
- `docs/defects/BUGATE-DSH-RELEASE-CI.md`
- `docs/qa-methodology/AGENTIC_QA_PLATFORM_GUIDE.md`
- `docs/qa-methodology/BUGATE_2_0_CONTEXT_RUNTIME_GUIDE.zh-CN.md`
- `docs/qa-methodology/BUGATE_2_0_HOST_ADAPTER_GUIDE.zh-CN.md`
- `docs/qa-methodology/BUGATE_2_0_IMPLEMENTATION_ROADMAP.zh-CN.md`
- `docs/qa-methodology/BUGATE_CONTEXT_RUNTIME_ADR.zh-CN.md`
- `docs/qa-methodology/BUGATE_DESUT_CALIBRATION_ADR.md`
- `docs/qa-methodology/BUGATE_EVOLUTION_TIMELINE.md`
- `docs/qa-methodology/BUGATE_GOVERNANCE_REFACTOR_GUIDE.md`
- `docs/qa-methodology/BUGATE_GOVERNANCE_REFACTOR_GUIDE.zh-CN.md`
- `docs/qa-methodology/BUGATE_HOSTING_CORRECTION_ADR.md`
- `docs/qa-methodology/BUGATE_HOST_INTEGRATION_POWERCONTEXT_ADR.zh-CN.md`
- `docs/qa-methodology/BUGATE_MEMORY_BUS_SYSTEM_LEVEL_ADR.md`
- `docs/qa-methodology/BUGATE_PLATFORM_DECOUPLING_ADR.md`
- `docs/qa-methodology/BUGATE_RUNTIME_BOUNDARY_ADR.md`
- `docs/qa-methodology/BUGATE_RUNTIME_BOUNDARY_ADR.zh-CN.md`
- `docs/qa-methodology/BUGATE_SELF_HEALING_SIDECAR_ADR.md`
- `docs/qa-methodology/BUGATE_STATELESS_WORKSPACE_ADR.zh-CN.md`
- `docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md`
- `docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.md`
- `docs/qa-methodology/IMPORTED_UPDATER_CONTRACT.zh-CN.md`
- `docs/qa-methodology/ROLE_GOVERNANCE_PROTOCOL.md`
- `docs/qa-methodology/ROLE_GOVERNANCE_PROTOCOL.zh-CN.md`
- `docs/qa-methodology/TRANSITION_PROTOCOL.md`
- `docs/releases/dsh-v2.0.0-dev.1.md`
- `docs/releases/dsh-v2.0.0-dev.1.zh-CN.md`
- `docs/releases/v0.4.0.md`
- `docs/releases/v0.4.0.zh-CN.md`
- `docs/releases/v0.4.1.md`
- `docs/releases/v0.4.1.zh-CN.md`
- `docs/releases/v0.4.2.md`
- `docs/releases/v0.4.2.zh-CN.md`
- `docs/releases/v0.4.3.md`
- `docs/releases/v0.4.3.zh-CN.md`
- `docs/releases/v0.4.4.md`
- `docs/releases/v0.4.4.zh-CN.md`
- `docs/releases/v0.4.5.md`
- `docs/releases/v0.4.5.zh-CN.md`
- `docs/reports/DEEPSEEK_HARNESS_DELIVERY_2026-10-29.md`
- `docs/reports/DEEPSEEK_HARNESS_DELIVERY_2026-10-29.zh-CN.md`
- `hooks/hooks.json`
- `scripts/bugate_core.py`
- `scripts/bugate_init.py`
- `scripts/bugate_install_contract.py`
- `scripts/bugate_legacy_manifest.py`
- `scripts/bugate_prompt_reminder.py`
- `scripts/bugate_update.py`
- `scripts/bugate_update_engine.py`
- `scripts/bugate_update_source.py`
- `scripts/bugate_update_transaction.py`
- `scripts/build_release_archives.py`
- `scripts/check_agent_role_paths.py`
- `scripts/check_bugate.py`
- `scripts/check_bugate_brief_semantics.py`
- `scripts/check_bugate_inventory_semantics.py`
- `scripts/check_bugate_layer2_semantics.py`
- `scripts/check_bugate_v13_semantics.py`
- `scripts/check_no_sut_terms.py`
- `scripts/check_plan_lock.py`
- `scripts/check_prd_health.py`
- `scripts/check_role_evidence.py`
- `scripts/failure_triage.py`
- `scripts/generate_assertion_coverage_matrix.py`
- `scripts/generate_sdtd_reports.py`
- `scripts/generate_sdtd_text_testcases.py`
- `scripts/memory_bus.py`
- `scripts/oracle_falsification.py`
- `scripts/resolve_sources.py`
- `scripts/role_governance.py`
- `scripts/role_lineage.py`
- `scripts/sdtd_adversarial.py`
- `scripts/sdtd_adversarial_cli_bridge.py`
- `scripts/sdtd_multiview.py`
- `scripts/sdtd_multiview_cli_bridge.py`
- `scripts/sdtd_orchestrator.py`
- `scripts/self_heal_gate.py`
- `scripts/self_heal_policy.py`
- `scripts/self_heal_review.py`
- `scripts/self_heal_sidecar.py`
- `scripts/self_healing_mvp.py`
- `skills/bugate`
- `skills/bugate-full-check`
- `skills/bugate-import`
- `skills/bugate-update`
- `tests/accept_release_assets.py`
- `tests/bugate_selfheal_fixture.py`
- `tests/fixtures/golden/04_execution_report.v0.4.4.md`
- `tests/fixtures/golden/04_execution_report.v0.4.4.passed.md`
- `tests/fixtures/golden/05_knowledge_update.v0.4.4.md`
- `tests/fixtures/golden/05_knowledge_update.v0.4.4.passed.md`
- `tests/fixtures/golden/self_healing.v0.4.4.json`
- `tests/fixtures/golden/self_healing.v0.4.4.md`
- `tests/fixtures/golden/self_healing.v0.4.4.passed.json`
- `tests/fixtures/golden/self_healing.v0.4.4.passed.md`
- `tests/fixtures/golden/self_healing_repair_plan.v0.4.4.md`
- `tests/fixtures/golden/self_healing_repair_plan.v0.4.4.passed.md`
- `tests/fixtures/legacy-sut-terms.txt`
- `tests/fixtures/role-governance-required.yaml`
- `tests/test_bugate_update_acceptance.py`
- `tests/test_bugate_update_cli.py`
- `tests/test_bugate_update_engine.py`
- `tests/test_bugate_update_integration.py`
- `tests/test_bugate_update_skill_contract.py`
- `tests/test_bugate_update_transaction.py`
- `tests/test_config_nested_merge.py`
- `tests/test_desut_guard.py`
- `tests/test_engine_mutation_guards.py`
- `tests/test_failure_triage.py`
- `tests/test_full_check_layouts.py`
- `tests/test_governed_write_backstop.py`
- `tests/test_hook_surface_parity.py`
- `tests/test_init_scaffold.py`
- `tests/test_install_contract.py`
- `tests/test_memory_handoff_strict.py`
- `tests/test_orchestrator_role_governance.py`
- `tests/test_peer_role_env.py`
- `tests/test_release_archives.py`
- `tests/test_release_asset_acceptance.py`
- `tests/test_release_manifests.py`
- `tests/test_repository_release_contract.py`
- `tests/test_role_evidence_hook.py`
- `tests/test_role_governance.py`
- `tests/test_role_governance_lineage.py`
- `tests/test_role_lineage_registry.py`
- `tests/test_self_heal_fakegreen.py`
- `tests/test_self_heal_reverse_verification.py`
- `tests/test_self_heal_review_contract.py`
- `tests/test_self_heal_sidecar_integrity.py`
- `tests/test_self_healing_governance.py`
- `tests/test_skill_discovery.py`
- `tests/test_update_source.py`
- `tests/test_updater_operator_docs_contract.py`
- `tests/test_write_guard_layouts.py`

## Test disposition and current proof

The retired root `tests/test_*.py` suites exercised only the removed installer,
updater/archive, host hooks, role/lineage processes, memory service, self-healing
loop and legacy artifact layouts. They are not claimed as PASS. AC-33 is retired
by the user's explicit scope change. Their deletion is not the quality proof:
current `tests/v2/` separately verifies native business semantics, testability,
Oracle coverage, references, snapshots, provenance and claims; optional native
modeling replaces relevant former semantic rules. `tests/integration_install/`
and the real DSH tests retain installation, lifecycle and runtime requirements.

NL-01 checks current Python imports and script references, exact native module
inventory, retired auto-entry absence and the finite canonical resource mapping.
NL-02 inspects actual wheel/bundle members, including nested Python wheels and
the sole console entry `bugate = bugate.cli:main`. Negative controls reject old
aliases, undeclared files, retired runtime imports, unsafe paths and credential/
SUT-fixture paths. This is a concrete distribution-surface check, not proof that
an arbitrary secret could never be embedded in any text. Build also rejects
checkout/home paths and mismatched Python/native protocol projections.

The 249 removed paths were clean tracked files at baseline. The tracked root
`bugate.config.yaml` remains recoverable byte-for-byte from Git history; no
untracked user config was deleted. Four pre-existing ignored scorer outputs and
all ignored local state are preserved and excluded from packaging. Old ignored
`dist/` artifacts remain historical local outputs; new documentation uses only
`dist/v2-dsh-only/`. The separately managed nested worktree is untouched.

Final bundle: `bugate-deepseek-harness-2.0.0-dev.2.tgz`, SHA-256
`1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0`.
Build/distribution command logs are under `evidence/`; the final acceptance matrix
records standalone runtime NL-03 separately.
