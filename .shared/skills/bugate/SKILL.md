---
name: bugate
description: "Use for SUT-neutral BUGate work: AI black-box requirement analysis, pre-code test governance, testability decisions, oracle mapping, adversarial review, and profile-based test-case framework setup. Do not use it to assume product-specific APIs, resources, or environments."
---

# BUGate

BUGate is a generic AI black-box test analysis and test-case governance
framework. It separates reusable method from SUT-specific facts.

## Required Workflow

1. Identify the active SUT profile and the governed automation test
   workspace — the repo you are working in (imported mode, the default) or a
   mounted workspace (while developing BUGate itself). If none exists, stay in core mode
   and do not invent product facts.
2. Build or review the business brief before test design.
3. Decide testability and the cheapest sufficient test layer.
4. Map inventory, propositions, oracles, evidence, and implementation targets.
5. Generate human-readable test cases when the workflow requires review.
6. Run adversarial or exploratory review for high-risk changes.
7. Only then implement or review concrete tests in the governed SUT automation
   test workspace.

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

### Optional Full-SDTD modeling stages

For complex use cases you may opt into three extra pre-Layer-3 modeling artifacts.
They are not part of the required pre-code set; skip them for simple flows.

| Stage | Artifact | Purpose |
|---|---|---|
| 1A | `01a_domain_model.md` | Business objects, attributes, relationships, and invariants. |
| 1B | `01b_state_flow.md` | State catalog, flow steps, and transition table. |
| 2A | `02a_test_dimension_matrix.yaml` | Explicit test-dimension selection before inventory. |

Create them with `python3 scripts/sdtd_orchestrator.py <artifact_dir> --init --full-sdtd`.
`check_bugate_v13_semantics.py` validates them only when present, chaining
`OBJ-`/`STATE-`/`TR-` ids across stages.

The shipped templates pass the pre-code gates as-is
(`python3 scripts/check_bugate_v13_semantics.py .shared/skills/bugate/templates --scope pre-code`);
the write-guard acceptance fabricates governed workspaces at run time
(upstream `tests/test_write_guard_layouts.py` — no example SUT trees ship in
the kit or the repo).

## Gate Enforcement

The pre-code gates are not advisory. A PreToolUse hook runs
`scripts/check_bugate.py` before a file write: when the write targets a
profile-guarded implementation path and the configured pre-code artifacts are
not present and accepted, the hook returns a non-zero decision and the write is
blocked. Claude triggers it through the matched `file_path`; Codex triggers it
through the `apply_patch` header. With no SUT profile active
(`guarded_path_regex: []`) nothing is guarded, so the core stays usable.

**Code-first requests.** If the user asks to skip straight to test
implementation (Stage 4 / Layer 4), do not produce code. First build or confirm
the configured pre-code artifacts (business brief, testability, inventory, and
any profile-required readable/adversarial cases) and explain that implementation
follows once those gates are accepted. Because the hook physically blocks
guarded writes until then, jumping to code is rejected rather than helpful.

## References

- Read `references/sdtd-constitution.md` for invariants.
- Read `references/business-understanding-gate.md` before accepting Layer 1.
- Read `references/testability-gate.md` before accepting Layer 2.
- Read `references/case-blueprint.md` before accepting Layer 3.
- Read `references/test-design-gate.md` before accepting readable/adversarial cases.
- Read `references/profile-schema.md` when binding a SUT profile or changing guard behavior.

## Core Commands

- Wave 0 PRD health: `python3 scripts/check_prd_health.py --input <prd_health.yaml> --gate` — 8-dimension PRD score (0-100) + grade A–D + routing; `profile_required` without a spec.
- Initialize/status artifacts: `python3 scripts/sdtd_orchestrator.py <artifact_dir> --init`
- Layer gates: `python3 scripts/check_bugate_brief_semantics.py <artifact_dir>`, `check_bugate_layer2_semantics.py`, `check_bugate_inventory_semantics.py`
- Full pre-code check: `python3 scripts/check_bugate_v13_semantics.py <artifact_dir> --scope pre-code`
- Generate readable cases: `python3 scripts/generate_sdtd_text_testcases.py <artifact_dir> --write`
- Multi-view core bridge: `python3 scripts/sdtd_multiview_cli_bridge.py run-all <artifact_dir>`
- Adversarial core bridge: `python3 scripts/sdtd_adversarial_cli_bridge.py run-all <artifact_dir>`
- Post-run reports: `python3 scripts/self_healing_mvp.py ...` then `python3 scripts/generate_sdtd_reports.py <artifact_dir> ... --write`
- Wave 8 quality: `python3 scripts/oracle_falsification.py --spec <spec.yaml> --gate` then `python3 scripts/generate_assertion_coverage_matrix.py --artifact-root <dir> --spec <spec.yaml> --mutation-result <result.json>` — declarative oracle falsification scoring + 5-state coverage matrix; reports `profile_required` without a spec.

SUT profiles may wrap these commands with product-specific paths, peer runtime
dispatch, evidence fetchers, and assertion runners. Core commands must remain
valid without any product repository attached.

## Memory (cross-session, cross-agent)

BUGate keeps memory in the local memory-bus, not in checked-in files, and it is a
generic BUGate component: it serves both the SUT and BUGate itself. The bus is
dual-namespace — SUT work records under the active profile's namespace; BUGate's
OWN governance memory records under the core namespace via `--core`.

- **Recall first.** At the start of a session pull prior context:
  `python3 scripts/memory_bus.py session-start --agent <role>` (add `--core`
  for BUGate-core context, ignoring the active SUT profile's namespace). A
  SessionStart hook runs this automatically.
- **Record progress at milestones — this is agent-driven, not automatic.** After
  a real decision, finding, or completed step, write it:
  `python3 scripts/memory_bus.py note --agent <role> --type <progress|finding|decision> --status confirmed --broadcast --msg "<neutral conclusion>"`.
  Use `--core` when the progress is about BUGate itself (engine/gates/governance);
  omit it for SUT-specific findings. Attach evidence with `--artifact <path>` or
  `--metadata key=value`. `<role>` ∈ builder/designer/implementer/reviewer/human/agent.
- **Hand off** to another agent: `memory_bus.py handoff --from <a> --to <b> --msg "..."`.
- The Stop hook writes only an hourly liveness heartbeat — bookkeeping, **not** a
  substitute for recording real progress with `note`.

See `docs/qa-methodology/EXPERIENCE_PROMOTION_PROTOCOL.md` for the full
record / recall / promote protocol.

## Boundaries

- No SUT API paths, credentials, product resource IDs, service URLs, or
  environment names belong in this skill.
- The governed SUT workspace — the host test repo in imported mode, a mounted
  workspace mounted while developing BUGate itself — means the SUT's automation test
  framework / test workspace: tests, BUGate artifacts, fixtures, runners,
  captured evidence, and local test rules. Product source, API dumps, secrets,
  and live environment details stay outside BUGate core and enter only through
  profile-controlled evidence/config boundaries.
- SUT profiles may add stricter artifact names, guarded paths, commands, and
  evidence rules, but they must not weaken the core invariants.
- If source code is available, treat it as one possible evidence source, not as
  the only truth for black-box behavior.
