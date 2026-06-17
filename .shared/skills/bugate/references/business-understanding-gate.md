# Layer 1 Business Understanding Gate

Layer 1 is acceptable only when it contains:

- Canonical business flows and scope boundaries.
- Business propositions with stable identifiers.
- Business oracles that describe observable truth.
- Assumptions, exclusions, and unresolved questions.
- Verifiability status for claims that cannot yet be tested.

Reject Layer 1 when it only restates user stories, lists APIs without business
meaning, or hides unknowns as implementation details.

## Minimal Clarification Gate

Before drafting Layer 1, check six dimensions for underspecification. Mark each
**clear**, **discoverable** (answerable from available evidence), or **unknown**.
Any high-impact dimension that stays unknown becomes a blocking Open Question.

| Dimension | What to pin down |
|---|---|
| Objective | What outcome is under test and why it matters. |
| Done criteria | What "correct" looks like as an observable result. |
| Scope | Which flows, surfaces, and data are in and out of scope. |
| Constraints | Rules, limits, and ordering the SUT must respect. |
| Environment | Where it runs and what state it depends on. |
| Safety | Irreversible or sensitive effects that must be guarded. |

## Evidence Labels

Classify every proposition and oracle by the strength of its evidence:

| Label | Meaning |
|---|---|
| fact | Directly supported by current docs, a source scan, probe output, or a developer's confirmation. |
| inferred | A reasonable interpretation, not yet strong enough for a strict assertion. |
| unknown | Not evidenced yet — ask, probe, or defer. |

**Never upgrade `inferred` or `unknown` to `fact` to make a test or gate pass.**
This is the rule that keeps the gate from being optimized into a rubber stamp.
