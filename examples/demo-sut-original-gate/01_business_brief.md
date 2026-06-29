---
gate: layer1_business_brief
gate_status: passed
---

# Business Understanding Gate: Coupon Redemption At Checkout

> SUT-neutral demo fixture for the `original-gate` semantic-schema dialect
> (narrative sections, prose assertions, no P-/O- ids). It is the counterpart of
> `examples/demo-sut` (canonical v1.3 dialect) and exists so core CI can
> regression-test the alternate dialect without a mounted SUT. Validate with:
>
>     check_bugate_brief_semantics.py    examples/demo-sut-original-gate --schema original-gate
>     check_bugate_layer2_semantics.py   examples/demo-sut-original-gate --schema original-gate
>     check_bugate_inventory_semantics.py examples/demo-sut-original-gate --schema original-gate

## SUT And Scope

A checkout service applies a single discount coupon to an order total. In scope:
validating a coupon code, computing the discounted total, and rejecting expired
or already-used coupons. Out of scope: payment capture and stock reservation.

## Canonical Business Flow

1. A shopper enters a coupon code at checkout.
2. The service looks up the coupon and checks validity (exists, not expired,
   not already used by this shopper).
3. If valid, the order total is reduced by the coupon's fixed discount amount.
4. The coupon is recorded as used for this shopper.
5. The discounted order total is returned to the shopper.

## Assertions That Follow From Business

- A valid, unexpired, unused coupon reduces the order total by exactly its
  discount amount.
- An expired coupon is rejected and the order total is left unchanged.
- A coupon already used by the same shopper is rejected.
- The discounted total is never returned below zero.

## Unknowns And Questions

- Can two coupons stack on one order? Assumed no for this fixture.
- Is the discount a fixed amount or a percentage? Fixed amount for this fixture.
