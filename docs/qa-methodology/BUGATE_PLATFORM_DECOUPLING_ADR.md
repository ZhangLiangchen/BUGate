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
- mounted automation test workspaces,
- product runtime/source/evidence that remains outside BUGate core.

## Decision

BUGate is split into four conceptual parts:

```mermaid
flowchart LR
  Core["BUGate Core\nSUT-neutral method and gates"]
  Profile["SUT Profile\nbridge contract"]
  Workspace["Mounted Workspace\nSUT automation test framework"]
  Runtime["SUT / Product Runtime\nblack-box target and evidence sources"]

  Core --> Profile
  Profile --> Workspace
  Workspace --> Runtime
  Runtime -. "observed behavior / evidence" .-> Workspace
```

| Part | Ownership | Content |
|---|---|---|
| BUGate Core | This repository | Method, artifact templates, structural gate criteria, hook mechanism, adapter layout. |
| SUT Profile | Profile package or mounted test workspace | Paths, commands, evidence sources, guarded test patterns, resource policy, runtime kind, role policy, namespace. |
| Mounted Workspace | Usually the SUT automation test repository/workspace | Test code, BUGate artifacts, fixtures, runners, generated cases, captured evidence, local test rules. |
| SUT / Product Runtime | Product-owned systems and repositories | Black-box API/UI/runtime behavior, product docs/contracts/environments, optional source/API dumps/secrets, live incidents, and operational evidence. |

Core must not depend on any single SUT kind, path, business entity, environment,
or resource naming scheme.

"Mounting a SUT" therefore means mounting the SUT's test automation surface, not
copying or vendoring the product repository into BUGate core. Product source can
be used as an evidence source when a profile allows it, but it is never the
default truth for black-box behavior.

## Consequences

- Core becomes portable across different systems under test.
- SUT profiles provide the concrete "teeth" for physical write guards and
  evidence checks inside the mounted test workspace.
- Core can still define strict invariants, but it must express them in
  SUT-neutral terms.
- SUT-specific learning can be promoted into Core only after it is rewritten as
  a product-neutral rule.
- Product source, API dumps, secrets, and live environment details remain
  outside Core. They belong to the product side, the test workspace, or a
  profile-controlled evidence/config boundary.

## Promotion Rule

A lesson can enter BUGate Core only if it can be stated without referencing a
single SUT's business entities, paths, environments, credentials, or fixtures.
When in doubt, keep it in the SUT profile.

## Implementation Notes

- Runtime adapters live under `.shared/skills/bugate/adapters/`.
- Codex and Claude discovery paths are symlinks to the shared BUGate skill.
- Hook commands locate the engine by walking up for `scripts/bugate_core.py`;
  gate scripts resolve the governed workspace root via the nearest
  `bugate.config.yaml` (legacy `AGENTS.md` + `.shared/` sentinel as workbench
  fallback). No git metadata required.
- `bugate.config.yaml` ships with no guarded paths. Profiles opt into guarded
  implementation paths.
- Workbench (maintainer) convention: a SUT is mounted by a local, uncommitted
  `profile:` pointer in the engine repo's `bugate.config.yaml` (or
  `BUGATE_PROFILE`); the committed core stays SUT-neutral. In imported mode
  (CHARTER §2.2, default) the governed repo commits its own config + profile.

## Rejected Alternatives

- Keep a single product-coupled framework: rejected because it prevents reuse.
- Fork the whole gate stack per product: rejected because rules drift and
  learning does not compound.
- Put all product profiles inside Core: rejected because Core would become a
  product registry instead of a framework.
