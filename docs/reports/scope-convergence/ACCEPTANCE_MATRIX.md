# Frozen acceptance matrix

English | [简体中文](ACCEPTANCE_MATRIX.zh-CN.md)

Source: user request SC-01–SC-18, 2026-10-29. This matrix is the development
acceptance oracle. Results and concrete test names are recorded separately after
execution; no case is presumed passed here.

| Case | Requirement | Required observation | Layer |
|---|---|---|---|
| SC-01 | R1/R3 | Ordinary-user order creation has only necessary flow cases and real business-result observations; no automatic security/fuzz/concurrency/performance matrix. | core + fixture |
| SC-02 | R2 | User-requested cross-tenant denial is required and verified as expected rejection. | core + fixture |
| SC-03 | R2 | Explicit normal/boundary/failure variants all remain required. | core |
| SC-04 | R5/R10 | Login/setup/read evidence/cleanup are lawful auxiliary activities, not goals. | core + DSH |
| SC-05 | R3/R4 | Unrelated proposal remains a suggestion; promotion/execution is refused without valid scope change. | core + DSH |
| SC-06 | R4 | Fully observed passing main flow completes despite multiple suggestions and no extra rework. | core + DSH |
| SC-07 | R8/R11 | Fully observed genuine failure permits execution completion, fails business verification, retains assertions/failure. | core + DSH |
| SC-08 | R1/R11 | Missing key business evidence blocks closure despite structure/model pass. | core + DSH |
| SC-09 | R5 | Diagnosis ties to current failure, stops at declared bound, preserves results/blocking reason. | core + DSH |
| SC-10 | R6/R7 | Explicit user expansion creates version/history, invalidates old plan and adds required cases. | core + DSH |
| SC-11 | R6/R8 | Failure-driven agent scope shrink or invented approval cannot authorize completion. | core + DSH |
| SC-12 | R9 | Restart/cold/compact/child/preset recover same effective contract. | real DSH |
| SC-13 | R7 | Old scope/plan/snapshot assessment cannot authorize new claim or execution. | core + DSH |
| SC-14 | R9 | Parallel tasks keep plans/suggestions/evidence/assessments isolated. | core + DSH |
| SC-15 | R1/R3/R6 | Exclusion conflicts and unsourced necessity yield explicit gaps without guessing. | core |
| SC-16 | R4/R7/R12 | Actual installed DSH runtime model fixture tries expansion; guard rejects, suggestion persists, evidence persists, completion converges. | installed real DSH |
| SC-17 | R12 | Source-denied clean installation runs core scope flow, repeat/doctor/upgrade/rollback/remove validated. | package + OS boundary |
| SC-18 | R4/R8/R11 | Report states exact scope, execution completion, functional verdict, blockers, suggestions and limited conclusion separately. | core + report |

Applicable regression: actual execution provenance; no execution/model pass/hash
shortcut; snapshots and claim digests; durable evidence barriers; cancellation and
timeout; paths/symlinks; concurrency/conflicts; execution-world mapping; installation
ownership/lifecycle; recovery and child isolation. Existing runtime and core suites
remain acceptance inputs. Required replacement of unconditional negative-case
matrix rules must retain explicit negative/permission/boundary cases (SC-02/03).
