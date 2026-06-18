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

## Wave 0 PRD health

`prd_health.yaml` is a declarative 8-dimension PRD self-assessment scored by the
SUT-neutral engine (METHOD §3):

```bash
python3 scripts/check_prd_health.py --input examples/demo-sut/prd_health.yaml --gate   # 87.5/100, grade A
```

## Wave 8 quality (oracle falsification + coverage matrix)

`falsification_spec.yaml` + `evidence/` drive the SUT-neutral falsification
engine: it mutates the captured evidence field by field and scores how many wrong
states the declarative oracles catch.

```bash
# from the repo root
python3 scripts/oracle_falsification.py --spec examples/demo-sut/falsification_spec.yaml \
  --json-output /tmp/of.json --md-output /tmp/of.md --gate          # ~85.7% (>= 0.70 gate)
python3 scripts/generate_assertion_coverage_matrix.py --artifact-root examples/demo-sut \
  --spec examples/demo-sut/falsification_spec.yaml --mutation-result /tmp/of.json --output /tmp/matrix.md
```

Expected: 6 mutations killed, 1 survived (`expiry_drift` — no oracle covers
`expires_at`, so the engine flags the gap). The coverage matrix marks O-001/O-002
`covered` and **O-003 `missing_implementation`** on purpose: CASE-003 references the
expiry oracle but the active-link spec does not define it — exactly the bug-catch
the matrix exists for. Extend the spec with an expired-link oracle to close it.

## Per-UC hardening gates (opt-in)

With a profile that sets the opt-in hardening flags (`require_multiview`,
`require_adversarial_absorption`, `require_regression_traceability`,
`layer2_strict`, `reject_on_bridge_failures` — see
`examples/sample-sut.profile.yaml`), extra per-UC gates turn on:

```bash
BUGATE_PROFILE=examples/sample-sut.profile.yaml \
  python3 scripts/check_bugate_v13_semantics.py examples/demo-sut --scope all --require-passed
```

This demo satisfies them: `00_multiview/divergence_report.md` (Wave 1 ran) is
present, and `CASE-004` is an inventory case absorbed from Stage 3B
(`origin: adversarial`). Both gates are off in the lean core default.

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
