[English](ROLE_GOVERNANCE_PROTOCOL.md) | [简体中文](ROLE_GOVERNANCE_PROTOCOL.zh-CN.md)

# Wave 7 Auditable Role-Governance Protocol

Status: frozen for BUGate v0.4.0. This protocol is SUT-neutral and is the
normative contract for the implementation, hooks, tests, importer, and release
acceptance.

## 1. Scope and role vocabulary

Wave 1 and Wave 7 solve different independence problems:

- Wave 1 dispatches independent Codex/Claude peers inside one design phase to
  expose interpretation divergence. A peer is a read-only analysis worker, not
  a lifecycle actor.
- Wave 7 separates lifecycle responsibility across `designer`, `implementer`,
  and `reviewer` sessions and records every transition.

`role_governance.phases` accepts only those three lifecycle tokens. Runtime
names such as `codex` and `claude` belong in receipt runtime metadata, never in
role fields. The existing `agent_roles` mapping remains a separate path-access
policy and retains legacy/custom role tokens and its bare-list/read/write forms.

## 2. Configuration contract

Core remains inert:

```yaml
role_governance:
  mode: off
```

An imported SUT profile may explicitly enable the complete contract:

```yaml
role_governance:
  mode: required
  memory_mode: required
  evidence_dir: 00_role_evidence
  session_id_required: true
  require_distinct_sessions: true
  human_acceptance_artifacts:
    - 03b_adversarial_cases.yaml
  phases:
    pre_code:
      allowed_roles: [designer]
    implementation:
      allowed_roles: [implementer]
      requires_handoff_from: [designer]
    post_run:
      allowed_roles: [reviewer]
      requires_handoff_from: [implementer]
```

Mode semantics:

- `off`: v0.3.x behavior; no role-state enforcement.
- `advisory`: evaluate and report violations without unlocking claims or
  blocking normal writes. Evidence-chain files remain protected from direct
  tool edits because an advisory chain must not become forgeable.
- `required`: invalid configuration, missing/wrong role or session, missing or
  invalid receipt, and any drift fail closed.

`memory_mode` is `best_effort` or `required`. Required role transitions use
strict Memory operations; ordinary recall, notes, and Stop heartbeats remain
best-effort.

Configuration files are parsed as nested mappings. Merge rules are
deterministic: mappings merge recursively, profile scalars replace base
scalars, and profile lists replace base lists. `parse_simple_yaml()` remains the
legacy frontmatter/simple-artifact parser. Each document canonicalizes legacy
top-level `namespace` into `memory.namespace` before merge, so a legacy profile
can override a nested base value; the merged result exposes both access forms.
If one document declares conflicting old and new forms, the new nested form
wins and is mirrored to the legacy alias.

Required mode rejects malformed YAML subset input, invalid types/enums/booleans,
absolute or escaping evidence directories, unknown/missing phases, invalid
lifecycle tokens, empty role sets, bad handoff relations, missing explicit
profiles, and every invalid governed regex with a clear error.

## 3. State machine

The append-only events and resulting states are:

| Sequence | Event | Required actor/session | Preconditions | Resulting state |
|---|---|---|---|---|
| 1 | `human_acceptance` | designer session records an already-made human decision | required pre-code gates passed; configured 03B is already `passed` | `ready_for_designer_handoff` |
| 2 | `designer_handoff` | designer | valid human acceptance and current pre-code/provenance snapshot; strict Memory anchor | `awaiting_implementer_acceptance` |
| 3 | `implementer_acceptance` | implementer in a distinct session | exact handoff ID and metadata verified; acceptance Memory anchor verified | `implementation_unlocked` |
| 4 | `implementer_handoff` | implementer | one or more workspace-contained implementation files, each guarded and bound to the same UC | `awaiting_reviewer_acceptance` |
| 5 | `reviewer_acceptance` | reviewer in a distinct session | exact implementer handoff verified; implementation snapshot current | `post_run_active` |
| 6 | `reviewer_completion` | reviewer | 04/05, command summary, exit code, log/evidence hashes, and final gates recorded and valid | `closed` |

The approval command records a declared `approved_by` for a 03B that a human
has already set to `passed`; it never modifies 03B and is not identity
authentication. Same-role acceptance and, when configured, same-session
acceptance are rejected. A successful retry is idempotent. Drift recovery
appends a superseding generation; deleting evidence is never a reset.

## 4. Local evidence and hashing

Each UC uses only `<artifact-dir>/00_role_evidence/`:

```text
00_role_evidence/
├── chain.json
└── receipts/000001-<event>-<hash>.json
```

Receipts are append-only. `chain.json` contains only schema version, current
state and sequence, chain-head hash, and the latest path for each logical
event. Paths are workspace-relative POSIX paths and snapshots are sorted by
path. JSON hashes use UTF-8, sorted keys, and compact separators; receipt
hashing excludes `receipt_sha256` itself. Each receipt links the previous
receipt and a stable transition hash.

Designer handoff captures the active profile, every required pre-code artifact,
formal `00_multiview` outputs when present, 03B dispatch provenance, and the
current human-acceptance receipt. Implementer handoff adds guarded
implementation hashes. Reviewer completion adds 04/05 plus execution logs and
evidence.

Receipt and chain publication uses same-directory temporary files, flush,
`fsync`, and `os.replace`. No secret or Memory credential is persisted. Receipt
content/hash, chain linkage/head, profile hash, pre-code hash/gate status, and
implementation hashes are locally revalidated on every governed edit. No
per-edit Memory request is permitted.

## 5. Strict Memory transition protocol

At a required transition BUGate performs, in order:

1. Build a stable transition payload and `transition_sha256`.
2. POST the Memory transition and require a valid content hash.
3. Exact GET that hash and verify namespace, roles, UC, phase, transition and
   referenced handoff metadata.
4. Construct the complete local receipt with the Memory ID and compute its
   receipt hash.
5. PUT the receipt hash into Memory metadata.
6. Exact GET again and verify the complete anchor.
7. Only then atomically publish the local receipt and chain head.

Acceptance first exact-GETs and validates the supplied handoff ID, then writes
and exact-GETs its acceptance. Unavailability, timeout, HTTP/write failure,
missing exact ID, or any field mismatch returns non-zero and publishes no local
unlock receipt or chain advancement. Stable transition content and local
latest-event checks make retries idempotent. High-cardinality identifiers and
hashes remain metadata rather than tags.

## 6. Enforcement surfaces

All Core artifact mutators call the shared Python preflight before directories,
templates, peer dispatch, or output writes. The common Core writer is a second
path-classification backstop. Role evidence uses a private atomic writer that
is not an environment-selectable bypass.

In required mode, pre-code init creates only pre-code and selected optional
modeling artifacts. The legacy/off init still creates 01–05 as v0.3.x did.
04/05 are reviewer-owned. Once a 03B has a human-acceptance receipt, `--auto`
must not regenerate it; handoff reruns semantic/provenance validation only.

Hooks keep two independent guards: `check_bugate.py` validates passed pre-code
gates, while `check_role_evidence.py` validates roles and the receipt chain.
Claude keeps `Edit|Write` for write gates and `Read|Edit|Write` for
`agent_roles`; Codex runs all four guards on `apply_patch`. Direct agent-tool
edits to `00_role_evidence/**` are denied. SessionStart performs best-effort
Memory recall and prints role-governance status; Stop remains a best-effort,
hourly heartbeat using the active role or `agent`.

Peer bridge child environments remove lifecycle role/session/receipt identity
without removing profile/project roots, proxy settings, model selection, or
reasoning effort.

## 7. Compatibility, recovery, and security boundary

Profiles without `role_governance` behave as v0.3.x. Enabling `required` does
not grandfather historical passed UCs: they need a current human acceptance,
handoff, and acceptance chain. Profile/artifact drift restarts from designer
acceptance/handoff; implementation drift restarts from implementer handoff and
reviewer acceptance. Re-running the importer refreshes vendored scripts and
BUGate-owned hook entries while preserving SUT-owned hooks; changed Codex hooks
must be re-trusted.

This protocol provides role declarations, session separation, hash linkage,
external Memory anchors, drift/tamper detection, and auditable transitions. It
does not provide non-repudiable human identity. Environment variables, hooks,
and local files cannot prove who operated them. Strong identity isolation needs
separate OS accounts, containers, managed runners, or role-scoped server
credentials. Hooks also cannot intercept arbitrary shell redirection or an
external editor; supported agent tools, orchestrators, and Core mutators are
enforced, while stronger filesystem isolation belongs to a managed runner.

## 8. Amendment — imported updater boundary (2026-07-22)

Section 7's sentence that re-running the importer refreshes an existing
installation is preserved as the frozen v0.4.0 record, but is superseded for
v0.4.2 and later compatible releases. `bugate_init.py` is fresh-install-only.
An exact v0.3.x or pre-lock v0.4.x installation bootstraps from an unpacked
release; an installation with the updater uses vendored `status` → `plan` →
`apply` → `verify`, with rollback by explicit transaction ID. See the
[Imported-mode updater contract](IMPORTED_UPDATER_CONTRACT.md) and the vendored
`bugate-import/references/updating-bugate.md` runbook.

The updater may replace role-governance-capable engine/hook files, but it never
activates governance, edits a profile/Memory/role evidence, or manufactures a
lifecycle receipt. Engine update and profile migration remain separately
reviewed, separately reversible commits. Codex Desktop re-trust is required
only when its hook bytes actually change; any hook change requires a new agent
session before the new enforcement surface may be claimed active.
