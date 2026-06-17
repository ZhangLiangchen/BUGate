# BUGate Constitution

1. Business meaning comes before implementation details.
2. Every important claim must be testable, bounded, or explicitly deferred.
3. Every case must trace to a proposition and, where applicable, a business
   oracle.
4. Evidence must be named and classified before assertions are encoded.
5. A green run is not evidence of quality if it bypassed the intended behavior.

These invariants outrank checklist convenience. A SUT profile may make them
stricter, but not weaker.
