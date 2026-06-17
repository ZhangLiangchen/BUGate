---
gate: readable_test_cases
gate_status: passed
sut_profile: demo-sut (Linkly URL shortener)
---

# Test Cases

## CASE-001

- Intent: Create a short link for a valid URL and verify the stored mapping.
- Preconditions: A running Linkly instance.
- Steps: POST /links with a generated unique long URL; capture the short_code; GET /links/{short_code}.
- Expected result: 201 with a 7-character short_code, and the fetched original_url equals the posted URL.
- Proposition refs: P-001
- Oracle refs: O-001

## CASE-002

- Intent: An active short code redirects to its original URL.
- Preconditions: An active short link created in setup.
- Steps: GET /{short_code} without following redirects.
- Expected result: 302 with a Location header equal to the original_url.
- Proposition refs: P-002
- Oracle refs: O-002

## CASE-003

- Intent: An expired short code returns 410 and does not redirect.
- Preconditions: An expired short link seeded via fixture.
- Steps: GET /{short_code} without following redirects.
- Expected result: 410 with no Location header.
- Proposition refs: P-003
- Oracle refs: O-003
