---
gate: layer1_business_brief
gate_status: passed
sut_profile: demo-sut (Linkly URL shortener)
---

# Business Brief

## Scope

Linkly is a fictional URL-shortener service used here as a neutral BUGate demo.
This brief covers the create-link and redirect flows and the expiry behavior.
Out of scope: authentication, rate limiting, and analytics.

## Canonical Business Flows

1. A client posts a long URL; Linkly stores it and returns a short code.
2. A client visits the short code; Linkly redirects to the original URL.
3. After a link's time-to-live elapses, the short code stops redirecting.

## Clarification Gate

| dimension | status | open question |
|---|---|---|
| objective | clear | none |
| done criteria | clear | none |
| scope | clear | none |
| constraints | clear | none |
| environment | clear | none |
| safety | clear | Q-001 below records the expired-link status-code confirmation. |

## Propositions

| id | proposition | priority | verifiability | evidence_label |
|---|---|---|---|---|
| P-001 | Creating a short link for a valid URL returns a unique short code that maps to that URL. | P0 | api | fact |
| P-002 | Visiting an active short code redirects the client to the original URL. | P0 | e2e | fact |
| P-003 | Visiting an expired short code returns HTTP 410 and does not redirect. | P1 | api | fact |

## Business Oracles

| id | oracle | observable evidence | evidence_label |
|---|---|---|---|
| O-001 | A created link stores the original URL under its short code. | POST /links returns 201 with short_code; GET /links/{short_code} returns the original_url. | fact |
| O-002 | An active short code redirects to its original URL. | GET /{short_code} responds 302 with Location equal to original_url. | fact |
| O-003 | An expired short code does not redirect. | GET /{short_code} for an expired link responds 410, with no Location header. | fact |

## Boundaries

Only the public HTTP surface is exercised; storage internals are treated as a
black box and asserted only through the API.

## Assumptions

The Linkly spec states that expired links respond 410 (not 404).

## Open Questions

- Q-001: Confirm with the service owner that 410 (not 404) is the contractual
  status for an expired link before tightening O-003 into a strict assertion.
