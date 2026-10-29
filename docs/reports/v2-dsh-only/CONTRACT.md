# v2 + DSH only: frozen implementation contract

Date: 2026-10-29. Authority: the user's destructive architecture upgrade request,
which supersedes all earlier requirements to retain legacy hosts, formats,
installers, memory services and compatibility tests. This record precedes implementation.

Baseline: clean main and origin/main at 61e1327a7bf01505cc82760782ffa1272ccaa03f.
Existing secondary worktree under .claude/worktrees is protected. No user global
configuration, other project, Git history, published release or tag may change.
Delivery is local reviewable commits and newly built dev.2 artifacts, not publishing.

## Requirements / 需求

| ID | Requirement | Oracle / acceptance |
| --- | --- | --- |
| R1 | Only Python v2 core + canonical protocol + CLI + native DSH adapter | Source, wheel and bundle contain no legacy runtime or old host auto entry; old formats rejected |
| R2 | Retain business and test quality semantics | Native v2 positive and mutation fixtures cover source-backed propositions, oracle mapping, testability, risks, exact assertions, review and scope completeness |
| R3 | Preserve evidence truth and freshness | Model fields never become host observations; altered bytes, bindings or execution inputs invalidate claims; product FAIL remains FAIL while complete delivery may pass |
| R4 | Stable CLI and storage | init/prepare/status/validate/assess/evidence/setup/doctor/remove, JSON exits, atomic writes, conflict/isolation/path tests |
| R5 | Real DSH lifecycle integration | Actual pinned runtime loads package; synchronous text/guard, awaited preparation and persistence, child/preset/recovery and bounded completion rework verified |
| R6 | Correct execution world | Configured fs/subprocess provider; opaque target keys never treated as paths; timeout/cancel/output/resource errors fail visibly |
| R7 | Independent distribution lifecycle | New wheel and bundle; clean install without source access, idempotence, supported v2 upgrade, rollback and removal preserve owned boundary |
| R8 | Single authority and complete delivery | Explicit package manifest; generated or checked projections; bilingual current docs and install prompt; HC/AC mapping with actual evidence |

## Invariants / 业务不变量

1. BUGate evaluates quality; DSH owns execution, scheduling and lifecycle. No second Agent loop, current-stage authority or mandatory memory service.
2. A model assertion, content hash, successful CLI invocation or completed turn is not runtime proof or requirement verification.
3. Required business understanding precedes governed implementation, while reading, analysis and valid exploratory probes remain possible.
4. Frozen scope supplies the coverage denominator. Missing oracle, unresolved semantics, stale review or placeholder cannot authorize a positive claim.
5. Task/workspace/protocol/profile/input identity applies to every claim and receipt. Old satisfied assessments cannot authorize new inputs.
6. Evidence durability is awaited before dependent completion. Writes are atomic and conflicts explicit; same task IDs in different workspaces remain isolated.
7. Actual product failure may complete delivery; zero tests, all skipped, cancellation and infrastructure failures retain their distinct meanings.
8. Same-user writable files are not adversarial authentication. Do not overclaim hashes or local receipts.

## Decisions / 架构决策

- Retire AC-33 only: its entire subject is old Claude/Codex/script compatibility explicitly cancelled by the user. All HC-01..15 and other AC entries remain applicable.
- Replace valuable old text-checker semantics using native structured v2 objects and mutation tests, not wrappers, converters or relocated legacy scripts.
- Reject old artifact/config types; no automatic v0.x migration. Supported upgrades must be explicit v2 installation lifecycle operations with binding drift detected.
- Remove tracked legacy files only; do not recursively delete the .claude directory containing the protected worktree.
- Existing v2 tests are retained and strengthened; deleted historical tests are catalogued by retired behavior, never counted as PASS.
- Real DSH runtime with deterministic model fixture is required; live-model behavior is separately reported, never inferred.

## Ownership / 单写者

- core_v2: src/bugate except integrations.py; protocol/v2; tests/v2.
- dsh_runtime: adapters/deepseek-harness; src/bugate/integrations.py; tests/integration_install.
- cleanup_delivery: tracked legacy tree/tests; pyproject; packaging; CI; root/user documentation; views; adapter catalog/schema.
- root: this contract, HC/AC evidence mapping, integrated review and final commits.

## Completion oracle / 完成判据

COMPLETE requires R1–R8 and every applicable HC/AC to have current-run passing
evidence. An external blocker is recorded with exact error, attempts and minimum
remedy. Missing implementation or unrun tests means INCOMPLETE, not PASS.
