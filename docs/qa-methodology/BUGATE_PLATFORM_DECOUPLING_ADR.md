---
type: ADR
id: ADR-BUGATE-001
title: BUGate SUT-neutral core and profile architecture
status: accepted
created_at: 2026-06-16
---

# ADR-BUGATE-001: SUT-Neutral BUGate Core

## Context

BUGate is intended to be a reusable AI black-box test analysis and test-case
governance framework. A reusable framework cannot contain product source code,
product API dumps, environment names, fixed resources, credentials, or
project-specific operating rules.

The coupling boundary is clean enough to separate:

- reusable method and gates,
- SUT profile contracts,
- product-owned implementation and evidence.

## Decision

BUGate is split into three layers:

| Layer | Ownership | Content |
|---|---|---|
| BUGate Core | This repository | Method, artifact templates, structural gate criteria, hook mechanism, adapter layout. |
| SUT Profile | Mounted SUT workspace or profile package | Paths, commands, evidence sources, guarded implementation patterns, resource policy, runtime kind. |
| SUT | Product repository | Source code, API docs, fixtures, tests, secrets, live evidence, incidents, and local agent rules. |

Core must not depend on any single SUT kind, path, business entity, environment,
or resource naming scheme.

## Consequences

- Core becomes portable across different systems under test.
- SUT profiles provide the concrete "teeth" for physical write guards and
  evidence checks.
- Core can still define strict invariants, but it must express them in
  SUT-neutral terms.
- SUT-specific learning can be promoted into Core only after it is rewritten as
  a product-neutral rule.

## Promotion Rule

A lesson can enter BUGate Core only if it can be stated without referencing a
single SUT's business entities, paths, environments, credentials, or fixtures.
When in doubt, keep it in the SUT profile.

## Implementation Notes

- Runtime adapters live under `.shared/skills/bugate/adapters/`.
- Codex and Claude discovery paths are symlinks to the shared BUGate skill.
- Hook commands resolve the repository root by walking to `AGENTS.md` and
  `.shared/`; they do not require git metadata.
- `bugate.config.yaml` ships with no guarded paths. Profiles opt into guarded
  implementation paths.

## Rejected Alternatives

- Keep a single product-coupled framework: rejected because it prevents reuse.
- Fork the whole gate stack per product: rejected because rules drift and
  learning does not compound.
- Put all product profiles inside Core: rejected because Core would become a
  product registry instead of a framework.
