# Demo SUT: Linkly (worked BUGate gate stack)

This directory is a **filled, passing** BUGate artifact stack for a neutral
fictional SUT — *Linkly*, a URL shortener. It shows what a `gate_status: passed`
pre-code stack looks like, instead of the empty `TBD` templates. It also doubles
as a smoke fixture: the repo's own gates run against it green.

It includes the full optional Full-SDTD modeling stack (`01a`/`01b`/`02a`) so the
modeling validators are exercised too.

## Verify it passes

```bash
# from the repo root
python3 scripts/check_bugate_v13_semantics.py examples/demo-sut --scope pre-code --require-passed
python3 scripts/check_bugate_v13_semantics.py examples/demo-sut --scope all --require-passed
```

## What is here

| File | Stage |
|---|---|
| `01_business_brief.md` | Layer 1 — propositions, oracles, clarification gate, evidence labels. |
| `01a_domain_model.md` | Optional 1A — business objects and invariants. |
| `01b_state_flow.md` | Optional 1B — states and transitions. |
| `02_testability.md` | Layer 2 — layer decision and evidence plan. |
| `02a_test_dimension_matrix.yaml` | Optional 2A — test-dimension selection. |
| `03_inventory.yaml` | Layer 3 — case inventory with proposition/oracle coverage. |
| `03a_test_cases.md` | Readable cases for review. |
| `03b_adversarial_cases.yaml` | Adversarial additions and residual risks. |
| `04_execution_report.md` | Execution result and regression cases. |
| `05_knowledge_update.md` | Reusable findings and regression cases. |

The Layer 4 implementation (concrete test code) is intentionally absent: it is
SUT-profile-owned and lives in the mounted SUT workspace, not in BUGate core.
