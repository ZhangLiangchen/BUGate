---
gate: layer3a_readable_cases
gate_status: passed
---

# Readable Test Cases: Coupon Redemption At Checkout

## COUPON-01 — valid coupon reduces the total

Given a valid, unexpired, unused coupon, when it is applied at checkout, the
order total is reduced by the discount amount and the coupon is marked used.

## COUPON-02 — expired coupon is rejected

Given an expired coupon, when it is applied, checkout rejects it and the order
total is left unchanged.

## COUPON-03 — already-used coupon is rejected

Given a coupon already used by this shopper, when it is applied, checkout
rejects it and the order total is left unchanged.
