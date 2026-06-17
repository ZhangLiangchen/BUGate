# Readable And Adversarial Test Design Gate

Readable test cases are acceptable only when a human reviewer can understand the
business scenario, preconditions, action, expected result, and coverage without
reading implementation code.

Adversarial review is acceptable only when it actively searches for:

- Boundary values and invalid states.
- Missing negative paths.
- Ambiguous business wording.
- Oracle weakness.
- Data or environment dependencies that could create fake green results.

Residual risks must be explicit. Do not silently convert them into passing
coverage.
