---
gate: layer2_testability
gate_status: passed
sut_profile: demo-sut (Linkly URL shortener)
---

# Testability

## Layer Decision Matrix

| proposition | chosen layer | cheaper layer considered | reason |
|---|---|---|---|
| P-001 | api | none | The create-and-fetch contract is fully observable at the API. |
| P-002 | e2e | api | Redirect status plus Location header is best asserted end to end. |
| P-003 | api | e2e | The expired status code is an API-level contract. |

## Evidence Plan

| oracle | evidence source | probe or fixture | status |
|---|---|---|---|
| O-001 | POST /links then GET /links/{short_code} | live API call | ready |
| O-002 | GET /{short_code} on an active link | seeded active link | ready |
| O-003 | GET /{short_code} on an expired link | fixture seeds an expired link | ready |

## Dependencies

A running Linkly instance and the ability to seed an already-expired link.

## Deferred Claims

None for this slice.
