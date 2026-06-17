---
name: bugate
description: Use for SUT-neutral BUGate work: AI black-box requirement analysis, pre-code test governance, testability decisions, oracle mapping, adversarial review, and profile-based test-case framework setup. Do not use it to assume product-specific APIs, resources, or environments.
---

# BUGate

BUGate is a generic AI black-box test analysis and test-case governance
framework. It separates reusable method from SUT-specific facts.

## Required Workflow

1. Identify the active SUT profile. If none exists, stay in core mode and do not
   invent product facts.
2. Build or review the business brief before test design.
3. Decide testability and the cheapest sufficient test layer.
4. Map inventory, propositions, oracles, evidence, and implementation targets.
5. Generate human-readable test cases when the workflow requires review.
6. Run adversarial or exploratory review for high-risk changes.
7. Only then implement or review concrete tests in the mounted SUT workspace.

## Artifact Stack

| Stage | Artifact | Purpose |
|---|---|---|
| 1 | `01_business_brief.md` | Business propositions, oracles, states, boundaries, and gaps. |
| 2 | `02_testability.md` | Test layer decision and evidence plan. |
| 3 | `03_inventory.yaml` | Case inventory, proposition coverage, oracle coverage, and data source plan. |
| 3A | `03a_test_cases.md` | Human-readable test cases for review. |
| 3B | `03b_adversarial_cases.yaml` | Adversarial additions and residual risks. |
| 4 | implementation | SUT-profile-owned test code. |
| 5 | `04_execution_report.md` | Execution result, failures, and classification. |
| 6 | `05_knowledge_update.md` | Reusable learnings and promotion candidates. |

## References

- Read `references/sdtd-constitution.md` for invariants.
- Read `references/business-understanding-gate.md` before accepting Layer 1.
- Read `references/testability-gate.md` before accepting Layer 2.
- Read `references/case-blueprint.md` before accepting Layer 3.
- Read `references/test-design-gate.md` before accepting readable/adversarial cases.
- Read `references/profile-schema.md` when mounting a SUT profile or changing guard behavior.

## Core Commands

- Initialize/status artifacts: `python3 scripts/sdtd_orchestrator.py <artifact_dir> --init`
- Layer gates: `python3 scripts/check_bugate_brief_semantics.py <artifact_dir>`, `check_bugate_layer2_semantics.py`, `check_bugate_inventory_semantics.py`
- Full pre-code check: `python3 scripts/check_bugate_v13_semantics.py <artifact_dir> --scope pre-code`
- Generate readable cases: `python3 scripts/generate_sdtd_text_testcases.py <artifact_dir> --write`
- Multi-view core bridge: `python3 scripts/sdtd_multiview_cli_bridge.py run-all <artifact_dir>`
- Adversarial core bridge: `python3 scripts/sdtd_adversarial_cli_bridge.py run-all <artifact_dir>`
- Post-run reports: `python3 scripts/self_healing_mvp.py ...` then `python3 scripts/generate_sdtd_reports.py <artifact_dir> ... --write`

SUT profiles may wrap these commands with product-specific paths, peer runtime
dispatch, evidence fetchers, and assertion runners. Core commands must remain
valid without any product repository mounted.

## Boundaries

- No SUT API paths, credentials, product resource IDs, service URLs, or
  environment names belong in this skill.
- SUT profiles may add stricter artifact names, guarded paths, commands, and
  evidence rules, but they must not weaken the core invariants.
- If source code is available, treat it as one possible evidence source, not as
  the only truth for black-box behavior.
