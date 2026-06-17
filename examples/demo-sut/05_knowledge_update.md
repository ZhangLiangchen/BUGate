---
gate: knowledge_update
gate_status: passed
sut_profile: demo-sut (Linkly URL shortener)
---

# Knowledge Update

## Reusable Findings

Asserting the redirect status and Location header directly (without following
redirects) is the reusable pattern for any redirect-based SUT.

## Regression Cases

> Record the named regression case created for each confirmed or escaped defect,
> so the escaped-defect-traceback metric has a mechanism behind it. Prioritize
> future test design toward areas where defects were historically found.

| defect / incident id | named regression case | proposition / oracle | tag |
|---|---|---|---|
| none | none | none | none |

## SUT Profile Updates

Keep the expired-link fixture in the profile so O-003 stays cheap to exercise.

## BUGate Core Updates

None; this slice used the core engine unchanged.

## Follow-ups

Resolve Q-001 (confirm 410 vs 404) with the service owner, then tighten O-003.
