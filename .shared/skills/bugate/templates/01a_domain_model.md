---
gate: layer1a_domain_model
gate_status: pending
sut_profile: TBD
---

# Domain Model

> Optional Full-SDTD stage 1A. Build this after `01_business_brief.md` and
> before state/flow modeling when a use case is complex enough that its business
> objects deserve an explicit, reviewable model. Skip it for simple flows; it is
> not part of the required pre-code set.

## Model Scope

- **SUT**: TBD
- **Business slice**: TBD
- **Out of scope**: TBD
- **Primary evidence**: TBD

## Business Objects

| object id | object name | business meaning | source / evidence | lifecycle owner | test relevance |
|---|---|---|---|---|---|
| OBJ-001 | TBD | TBD | TBD | SUT / upstream / downstream / external | P-001 / O-001 |

## Object Attributes

| object id | attribute | type / domain | required | source / evidence | assertion relevance |
|---|---|---|---|---|---|
| OBJ-001 | TBD | TBD | yes / no | TBD | strict / soft / probe_only |

## Relationships

| relationship id | from | to | cardinality | business rule | evidence | related proposition |
|---|---|---|---|---|---|---|
| REL-001 | OBJ-001 | OBJ-001 | 1:1 / 1:N / N:N | TBD | TBD | P-001 |

## Invariants

| invariant id | statement | objects involved | evidence | oracle candidate |
|---|---|---|---|---|
| INV-001 | TBD | OBJ-001 | TBD | O-001 |

## Risky Ambiguities

| ambiguity | why it matters | bound to | resolution plan |
|---|---|---|---|
| TBD | affects oracle / data / state / resource | Q-001 | dev answer / probe / out_of_scope |

## Readiness

| check | status | notes |
|---|---|---|
| Every object has named evidence | yes / no | TBD |
| Every strict invariant maps to an oracle | yes / no | TBD |
| No fabricated IDs, values, or enum members | yes / no | TBD |
| Blocking ambiguities are bound to a question id | yes / no | TBD |
