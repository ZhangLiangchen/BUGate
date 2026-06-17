---
gate: layer1a_domain_model
gate_status: passed
sut_profile: demo-sut (Linkly URL shortener)
---

# Domain Model

> Optional Full-SDTD stage 1A, shown filled for the Linkly demo.

## Model Scope

- **SUT**: Linkly URL shortener (HTTP surface only).
- **Business slice**: create-link and redirect, including expiry.
- **Out of scope**: auth, analytics, custom domains.
- **Primary evidence**: the Linkly HTTP API responses.

## Business Objects

| object id | object name | business meaning | source / evidence | lifecycle owner | test relevance |
|---|---|---|---|---|---|
| OBJ-001 | ShortLink | A mapping from a short code to an original URL with a time-to-live. | POST /links response and GET /links/{short_code}. | SUT | P-001 / O-001 |
| OBJ-002 | RedirectRequest | An inbound GET on a short code that resolves to a ShortLink. | GET /{short_code} response. | external | P-002 / O-002 |

## Object Attributes

| object id | attribute | type / domain | required | source / evidence | assertion relevance |
|---|---|---|---|---|---|
| OBJ-001 | short_code | string (7 chars) | yes | POST /links response body | strict |
| OBJ-001 | original_url | URL string | yes | GET /links/{short_code} | strict |
| OBJ-001 | expires_at | timestamp | yes | GET /links/{short_code} | soft |

## Relationships

| relationship id | from | to | cardinality | business rule | evidence | related proposition |
|---|---|---|---|---|---|---|
| REL-001 | OBJ-002 | OBJ-001 | N:1 | A redirect request resolves exactly one short link. | GET /{short_code} Location header | P-002 |

## Invariants

| invariant id | statement | objects involved | evidence | oracle candidate |
|---|---|---|---|---|
| INV-001 | A short code maps to at most one active original URL. | OBJ-001 | GET /links/{short_code} | O-001 |
| INV-002 | An expired short link never produces a redirect. | OBJ-001 | GET /{short_code} status | O-003 |

## Risky Ambiguities

| ambiguity | why it matters | bound to | resolution plan |
|---|---|---|---|
| Expired status code (410 vs 404) | affects oracle O-003 strictness | Q-001 | dev answer |

## Readiness

| check | status | notes |
|---|---|---|
| Every object has named evidence | yes | Both objects cite an HTTP surface. |
| Every strict invariant maps to an oracle | yes | INV-001 to O-001, INV-002 to O-003. |
| No fabricated IDs, values, or enum members | yes | Values come from the demo spec. |
| Blocking ambiguities are bound to a question id | yes | Q-001 covers the expired status code. |
