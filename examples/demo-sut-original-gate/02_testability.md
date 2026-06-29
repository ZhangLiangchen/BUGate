---
gate: layer2_testability
gate_status: passed
---

# Testability Gate: Coupon Redemption At Checkout

## Execution Boundary

Tested at the API layer against a stub checkout service in a local environment.
No external payment provider is touched; no real money moves.

## Resource Strategy

Coupons are seeded as fixtures: one valid, one expired, one already-used. Each
test run uses a fresh shopper id so the used-coupon state is deterministic and
independent across runs.

## Assertion Strategy

Assert on the returned order total and the rejection reason. Read the coupon's
used flag back from the store to confirm the side effect actually happened.

## Acceptance Criteria

- The valid-coupon path returns the reduced total and marks the coupon used.
- The expired and already-used paths return a rejection and leave the total
  unchanged.
