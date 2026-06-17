---
gate: layer1b_state_flow
gate_status: passed
sut_profile: demo-sut (Linkly URL shortener)
---

# State / Flow Model

> Optional Full-SDTD stage 1B, shown filled for the Linkly demo.

## Flow Boundary

- **Start trigger**: a client POSTs a URL or GETs a short code.
- **End condition**: a short link is created, or a redirect/410 is returned.
- **Async / polling involved**: no
- **Irreversible side effects**: creating a short link is persistent.
- **Evidence inputs**: HTTP status, headers, and JSON bodies.

## Business Flow

| step id | actor | action / event | input object | output object / state | evidence | related proposition |
|---|---|---|---|---|---|---|
| FLOW-001 | client | POST a long URL | OBJ-002 | STATE-001 | POST /links 201 + short_code | P-001 |
| FLOW-002 | client | GET an active short code | OBJ-002 | STATE-001 | GET /{short_code} 302 | P-002 |
| FLOW-003 | client | GET an expired short code | OBJ-002 | STATE-002 | GET /{short_code} 410 | P-003 |

## State Catalog

| state id | object | state name / value | meaning | source / evidence | terminal |
|---|---|---|---|---|---|
| STATE-001 | OBJ-001 | active | The short link redirects. | GET /{short_code} 302 | no |
| STATE-002 | OBJ-001 | expired | The short link no longer redirects. | GET /{short_code} 410 | yes |

## Transition Table

| transition id | from state | event / condition | to state | expected observable surface | oracle | forbidden transition |
|---|---|---|---|---|---|---|
| TR-001 | STATE-001 | time-to-live elapses | STATE-002 | GET /{short_code} status flips 302 to 410 | O-003 | expired back to active |

## Async And Timing

| async point | poll / wait strategy | timeout | evidence | failure signal |
|---|---|---|---|---|
| expiry | seed an already-expired link via fixture | none | GET /{short_code} 410 | a 302 after expiry |

## Flow / State Coverage Notes

- **Must cover transitions**: TR-001 (active to expired).
- **Can defer transitions**: none.
- **Out-of-scope states**: deleted links.
- **State assertions that must not be strict yet**: none.

## Readiness

| check | status | notes |
|---|---|---|
| Every P0/P1 flow step has observable evidence | yes | Each step cites an HTTP response. |
| Every strict terminal state maps to an oracle | yes | STATE-002 maps to O-003. |
| Async behavior has a polling or probe plan | not_applicable | Expiry is seeded via fixture, not awaited. |
| Forbidden transitions are tested or explicitly deferred | yes | The expired-to-active case is forbidden. |
