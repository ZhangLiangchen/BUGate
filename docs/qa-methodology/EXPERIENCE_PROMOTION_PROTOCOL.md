---
type: protocol
id: PROTO-BUGATE-EXP-001
title: Experience Promotion Protocol — from SUT-local memory to BUGate Core
status: accepted
created_at: 2026-06-17
authority: ADR-BUGATE-001
companion: BUGATE_PLATFORM_DECOUPLING_ADR.md
---

# Experience Promotion Protocol

> *How a lesson learned while testing one system under test (SUT) becomes either
> SUT-local memory that stays put, or a product-neutral rule promoted into BUGate
> Core.*

---

## 0. Purpose & Scope

This protocol defines a single decision and a single mechanism:

- **The decision** — when an agent learns something while testing a specific SUT,
  does that lesson stay as SUT-local memory, or does it become a product-neutral
  rule promoted into BUGate Core?
- **The mechanism** — how that promotion is physically recorded, where it lands,
  and how it is later retrieved.

The protocol is the **operational expansion** of two sections of
`BUGATE_PLATFORM_DECOUPLING_ADR.md` (ADR-BUGATE-001, `status: accepted`,
2026-06-16): the **Promotion Rule** and the **Consequences**. Those sections are
the governing text; this document does not override them, it makes them
executable.

Core is SUT-neutral by construction. The ADR Decision states Core "must not
depend on any single SUT kind, path, business entity, environment, or resource
naming scheme." Everything below is shaped to keep that property intact: the
promotion loop exists precisely so that learning can compound into Core *without*
dragging any one product's specifics in with it.

---

## 1. The Two-Tier Layout — why a promotion loop exists at all

The ADR splits BUGate into three layers with distinct ownership. The promotion
loop runs **between** them:

| Layer | Ownership | Content (ADR Decision table) |
|---|---|---|
| **BUGate Core** | This repository | Method, artifact templates, structural gate criteria, hook mechanism, adapter layout. |
| **SUT Profile** | Mounted SUT workspace or profile package | Paths, commands, evidence sources, guarded implementation patterns, resource policy, runtime kind. |
| **SUT** | Product repository | Source, API docs, fixtures, tests, secrets, live evidence, incidents, local agent rules. |

Promotion is **strictly upward and inward**: a lesson recorded against a SUT (or
its profile) may only move *into* Core, never sideways into another SUT and never
back out of Core as a product detail. The ADR Promotion Rule's tie-breaker is the
default posture: **"When in doubt, keep it in the SUT profile."**

The loop is not decoration — it is the mechanism that defeats three failure modes
the ADR explicitly rejected (Rejected Alternatives):

- A **single product-coupled framework** — rejected because it prevents reuse.
  Without a promotion path, every lesson would be welded to one product.
- **Forking the whole gate stack per product** — rejected because rules drift and
  learning does not compound. Without an *inward* path, each fork relearns the
  same lessons in isolation.
- **Putting all product profiles inside Core** — rejected because Core would
  become a product registry instead of a framework. The promotion *gate* (§4) is
  what stops the loop from re-importing product detail under the banner of
  "experience."

So the loop has exactly two tiers of activity: **record everything locally**
(§2), then **promote only the neutral subset** (§3) through a generalization gate
(§4).

---

## 2. Tier 1 — SUT-local experience capture

Recording happens through the **memory bus**, not through checked-in files. The
`bin/promote-memory` header is explicit about why: "rather than exporting into a
product-specific progress log, the publishable BUGate version simply re-records
the content as a `status:confirmed` memory that references the source. This keeps
the long-term truth layer in the memory service itself." There is no
project-specific progress log in the neutral version — the memory service is the
truth layer.

### 2.1 Mechanism

The entrypoint is `scripts/memory_bus.py note`. Its module docstring lists the
full subcommand set: `session-start, stop, status, note, search, recent,
handoff`. A capture is:

```
python3 scripts/memory_bus.py note \
  --agent <a> --type <finding|...> --status draft \
  --msg "<observation>" \
  [--scope ...] [--task ...] [--to <agent> | --broadcast] \
  [--tag ...] [--artifact ...] [--metadata key=value]
```

### 2.2 Record vocabulary is constrained, not freeform

`cmd_note` validates three axes against the script's own constants. Treat these
as the protocol's **allowed values**, not as suggestions — anything outside them
is rejected with exit code `2`:

- `VALID_TYPES = ("progress", "finding", "blocker", "decision", "handoff")`
- `VALID_STATUS = ("draft", "confirmed", "obsolete")`
- `VALID_AGENTS = ("builder", "designer", "implementer", "reviewer", "human", "agent")`

### 2.3 Tagging is generated, not hand-written

`build_tags()` always emits `<project_tag>`, `agent:<agent>`, `type:<type>`, and
`status:<status>`; it optionally adds `scope:<scope>`, `task:<task>`,
`msg:to-<agent>` (when `--to` is set) **or** `msg:broadcast` (when `--broadcast`
is set), plus any raw `--tag` extras. Duplicate tags are de-duplicated in order.

### 2.4 The namespace boundary keeps SUTs apart

The project namespace is **resolved, never hardcoded** (`project_tag()`), in this
order:

1. env `MEMORY_BUS_PROJECT_TAG`
2. config `memory.namespace` (from `bugate.config.yaml`)
3. default `project:bugate`

This resolved namespace is the boundary that keeps one SUT's recorded experience
out of another's. A capture lands under whatever namespace the active profile
resolves — so two SUTs mounted with two namespaces do not bleed into each other,
and a Core-bound promotion lands under the Core namespace rather than a product's.

### 2.5 Default recording posture

- Working notes and hypotheses are written `status:draft`. A draft is *not* yet a
  candidate for Core — it is just captured experience.
- Cross-agent context is addressed with `--to <agent>` (or the dedicated
  `handoff` subcommand, which forces `type:handoff` and requires a `--to`
  target), or fanned out with `--broadcast`.

### 2.6 Heartbeats are bookkeeping, not experience

The `stop` subcommand writes an **hourly heartbeat** (`scope:heartbeat`,
`status:draft`, broadcast) so dashboards reflect recency; it is disabled with
`MEMORY_BUS_STOP_WRITE=0`. Call this out plainly: **a heartbeat is bookkeeping,
never a promotable finding.** Consumers that scan drafts for promotable
experience must filter `scope:heartbeat` out first (see §7).

---

## 3. Tier 2 — what is eligible for Core, and what promotion actually does

### 3.1 Eligibility is a single criterion

Per the ADR Promotion Rule, a lesson can enter Core **only if it can be stated
without referencing a single SUT's business entities, paths, environments,
credentials, or fixtures.** That is the whole admission test. Everything in §4 is
the operationalization of this one sentence.

A stronger, recommended bar: a candidate is most trustworthy when the same lesson
has been **observed on at least two different SUTs**. A rule that has only ever
held for one product is more likely to be a product detail wearing a neutral
costume. Two-SUT corroboration is not enforced by code, but it is the surest
signal that a lesson is genuinely method-level rather than SUT-level.

### 3.2 Promotion is two obligations, not one

Promotion is simultaneously a **state transition** and a **content rewrite**.
They are separate; satisfying one does not satisfy the other.

1. **State: `draft` → `confirmed`.** `bin/promote-memory` hardcodes
   `--status confirmed` in its final `exec`, and its usage line restricts
   `--type` to `finding|decision`. This matches the ADR's "durable conclusions"
   intent: only confirmed findings and decisions belong in Core.
2. **Content: it must already be the neutral form.** The wrapper does **not**
   sanitize content. It changes state and rewrites provenance; it does not read
   the message for product names, paths, or entities. Generalization is therefore
   an **author/reviewer obligation discharged before the command is run** (§4),
   not a guarantee the tooling provides.

### 3.3 Provenance is preserved, not discarded

`bin/promote-memory` rewrites `--from-id <source-memory-id>` into
`--metadata promoted_from=<id>` before calling `note`. The confirmed Core rule
therefore links back to the SUT-local draft it came from, keeping the promotion
auditable to its originating evidence.

### 3.4 Net effect

A promotion produces a **new** memory that is `status:confirmed`,
`type:finding|decision`, whose content is product-neutral, and whose metadata
cites its origin via `promoted_from`. The original SUT-local draft is **not
deleted** by the script — the local lesson and its generalized Core form coexist,
linked by provenance.

---

## 4. The Generalization Gate — admission criteria

There is **no standalone generalization-gate script in the repository.** This is
load-bearing: do not look for a checker that does not exist. The gate is *policy*
— the ADR Promotion Rule — enforced by the author or reviewer **before** invoking
`bin/promote-memory`. The tooling enforces *state* (`draft → confirmed`) and
*provenance*; it does **not** enforce *neutrality*. Neutrality is enforced here,
by a human or agent applying the checklist below.

### 4.1 Hard reject list (ADR-derived)

A candidate fails the gate — and stays in the SUT/profile — if its content
references any of:

- a single SUT's **business entities, paths, environments, credentials, or
  fixtures** (ADR Promotion Rule);
- a single SUT's **kind, business entity, or resource naming scheme** (ADR
  Decision).

### 4.2 Tie-breaker

"When in doubt, keep it in the SUT profile" (ADR). The **default is DON'T
promote.** A borderline candidate is a non-candidate.

### 4.3 Type gate

Only `finding` or `decision` are promotable (the `promote-memory` usage line).
`progress`, `blocker`, and `handoff` are operational or transient and are **not**
Core-promotion candidates. Note that this is convention/usage, not a wrapper
check — see §7.

### 4.4 Restatement test (operationalizing the rule)

The ADR Decision table names exactly five Core content categories: **method,
artifact templates, structural gate criteria, hook mechanism, adapter layout.**
Apply this test to a candidate:

> Can the lesson be re-expressed in terms of method, gate criteria, a template, a
> hook mechanism, or adapter layout **without naming a product detail**?

If yes → it lands in one of those categories (see §5) and is eligible. If the
restatement cannot survive without naming a SUT entity, path, environment,
credential, or fixture → it **fails** and stays in the profile/SUT.

### 4.5 Two-SUT validation (recommended, see §3.1)

Where feasible, confirm the lesson holds on at least two distinct SUTs before
promoting. A lesson validated on one SUT only is a candidate for the **profile**,
not Core, until a second SUT corroborates it.

### 4.6 Provenance requirement

Promotion should carry `--from-id` so the confirmed Core rule remains auditable
back to its originating draft and evidence. Treat a promotion without
`promoted_from` metadata as incomplete (§7).

---

## 5. Where a promotion physically lands

A promotion does **not** land in a checked-in file. `bin/promote-memory` ends in
`exec python3 "$ROOT/scripts/memory_bus.py" note --status confirmed ...`, so the
record lands in the **running memory service**.

### 5.1 Service address and namespace

- The record lands wherever `base_url()` points: env `MEMORY_BUS_URL`, defaulting
  to `http://localhost:8000`.
- It lands under the resolved `project_tag()` namespace (§2.4). For a Core-bound
  rule this should be the Core namespace, not a product's — getting this wrong is
  a failure mode (§7).

### 5.2 Which Core category a promotion maps to (conceptual landing)

Mechanically, every promotion is a confirmed `finding|decision` memory. But each
promotion should be *authored against* one of the ADR's Core content categories,
because that is what makes it a Core rule rather than free-floating text. When you
promote, state which category the rule belongs to:

| Core content category (ADR Decision) | A promoted rule of this kind reads like… |
|---|---|
| **Method** | a generalized step, ordering, or principle in the Wave flow. |
| **Structural gate criteria** | a neutral condition a gate should check. |
| **Artifact template** | a neutral field, schema constraint, or template section. |
| **Hook mechanism** | a neutral guard/trigger behavior, independent of any SUT path. |
| **Adapter layout** | a neutral runtime-adapter or discovery convention. |

The category a promotion claims is also its **restatement test target** (§4.4): if
the lesson cannot be phrased as one of these without a product name, it does not
promote.

### 5.3 Service-side type mapping

`post_memory()` translates the BUGate logical type into the memory service's own
`memory_type` via `type_map`:

| BUGate type | Stored `memory_type` |
|---|---|
| `finding` | `observation` |
| `decision` | `decision` |
| `blocker` | `error` |
| `handoff` | `communication` |
| `progress` | `milestone` |

So a promoted `finding` is stored as an `observation`; a promoted `decision`
stays a `decision`. Retrieval by tag (`type:finding` / `type:decision`) is
unaffected because the BUGate tags are written regardless.

### 5.4 Dedup avoidance

Each write sets a unique `conversation_id`
(`bugate-memory-bus-<agent>-<timestamp>`) so the service does **not** semantically
collapse a generalized promotion into the SUT-local draft it came from. This is
intentional and load-bearing: without it, a neutral Core rule could be silently
merged back into its product-coupled source. Any future change that drops the
unique `conversation_id` is a regression (§7).

### 5.5 Root resolution is git-independent

Both `promote-memory`'s `find_root()` and `bugate_core.find_root()` resolve the
repository root by walking up to a directory containing `AGENTS.md` **and**
`.shared/`. They do not require git metadata (ADR Implementation Notes: hooks
"resolve the repository root by walking to `AGENTS.md` and `.shared/`; they do not
require git metadata"). A promotion therefore lands correctly whether or not the
checkout is a git repo.

### 5.6 How a Core rule re-enters future sessions

After landing, promoted rules surface through reads:

- `search` — semantic `/api/search`, with a tag-filter fallback to
  `/api/search/by-tag`, then a final tag-listing fallback. This is how an agent
  pulls a Core rule by topic.
- `session-start` — explicitly pulls `status:confirmed` matches for "current
  progress blockers active decisions handoff", merged with agent-addressed recent
  notes. This is the path by which a Core-promoted rule **re-enters every future
  session** without anyone re-finding it manually.

---

## 6. Which command implements each step

| Step | Command |
|---|---|
| **Record (Tier 1)** | `memory_bus.py note --agent <a> --type <finding\|...> --status draft --msg "..."` (+ `--scope/--task/--to/--broadcast/--tag/--artifact/--metadata`) |
| **Hand off between agents** | `memory_bus.py handoff --from <a> --to <b> --msg "..."` (forces `type:handoff`, requires a `--to` target) |
| **Recall before/within a session** | `memory_bus.py session-start --agent <a>` (merges agent-addressed recent + confirmed); `memory_bus.py recent --agent <a>`; `memory_bus.py search --query "..."` |
| **Promote (Tier 2)** | `bin/promote-memory --agent <a> --type <finding\|decision> --msg "<neutral conclusion>" [--from-id <id>] [--task ...] [--artifact ...]` → ensures the service is up via `bin/memory-bus-ensure`, then runs `note --status confirmed` |
| **Health / diagnostics** | `memory_bus.py status [--json] [--no-fail]`; `service_available()` gates every write |
| **Heartbeat backstop (not promotion)** | `memory_bus.py stop --agent <a>` |

---

## 7. Failure modes — what actually breaks, from the scripts

These are not hypotheticals; each is a behavior in the current code that an
operator must guard against.

### 7.1 Nothing promotes — service down is a silent no-op success

Every write command checks `service_available()` and, if it is false, calls
`warn_unavailable()` and `return 0` (`cmd_note`, `cmd_handoff`).
`bin/promote-memory` likewise swallows ensure failures
(`bin/memory-bus-ensure ... || true`). A promotion can therefore **"succeed"
(exit 0) while nothing was recorded** — the failure goes to **stderr, not the exit
code.**

> **Guard:** read stderr, and re-run `search` to confirm the promotion actually
> landed. Never treat exit 0 alone as proof a Core rule exists.

### 7.2 SUT-specific cruft leaks into Core — neutrality is not enforced by code

Neither `bin/promote-memory` nor `memory_bus.py` inspects content for
SUT-specific entities, paths, or credentials. A leaky, product-coupled "rule" can
be promoted to `confirmed` if the author skips the §4 gate. This is the ADR's
"rules drift / learning does not compound" failure surfacing concretely — as
polluted Core memory.

> **Guard:** the §4 generalization gate is the only thing standing between a
> product detail and Core. It is a human/agent obligation. There is no checker.

### 7.3 Off-policy type slips through the wrapper

`cmd_note` rejects `--type` outside `VALID_TYPES` (exit 2), but `VALID_TYPES`
includes `progress`, `blocker`, and `handoff`. `bin/promote-memory` passes
`--type` through unchanged, so the `finding|decision` restriction is only its
**usage line** — convention, not a wrapper check. Promoting with, e.g.,
`--type progress` would still pass `cmd_note` validation and land an off-policy
"promotion."

> **Guard:** enforce the `finding|decision` type at the §4 gate, not in code.

### 7.4 Namespace misconfig — cross-SUT bleed or orphaned promotion

If `MEMORY_BUS_PROJECT_TAG` / `memory.namespace` is wrong or unset, the record
lands under the wrong (or default `project:bugate`) tag. The promotion then is not
retrievable under the intended namespace — or worse, it mixes SUTs.

> **Guard:** confirm `project_tag()` resolves to the intended namespace
> (`memory_bus.py status` prints it) before promoting.

### 7.5 Lost provenance

Omitting `--from-id` produces a confirmed Core rule with **no** `promoted_from`
metadata, defeating auditability back to the originating evidence (§4.6).

> **Guard:** always pass `--from-id` when promoting from an existing draft.

### 7.6 Dedup collapse if `conversation_id` were not unique

The unique `conversation_id` (§5.4) is what stops the service from merging a
generalized rule into its SUT-local source. The current code is correct, but any
future change that drops or reuses the `conversation_id` would silently re-couple
a Core rule to its product origin.

> **Guard:** treat the unique `conversation_id` as a regression-sensitive
> invariant; flag any change to it.

### 7.7 Heartbeat noise mistaken for findings

`scope:heartbeat` / `status:draft` stop-writes share the broadcast channel with
real drafts (§2.6). A consumer that scans broadcast drafts for promotable
experience will pick up heartbeats unless it filters them.

> **Guard:** exclude `scope:heartbeat` before treating any draft as promotable.

---

## 8. The loop in one paragraph

While testing a SUT, an agent records experience as a `status:draft`
`finding`/`decision` through `memory_bus.py note`, tagged under the SUT's resolved
namespace. Most of it stays there — that is the default. When a lesson can be
restated in the ADR's Core terms (method, gate criteria, template, hook mechanism,
adapter layout) **without** naming any SUT entity, path, environment, credential,
or fixture — and, ideally, after a second SUT corroborates it — the author runs
the §4 generalization gate by hand (there is no script), then `bin/promote-memory`
re-records it as a `status:confirmed` rule linked by `promoted_from` provenance.
That confirmed rule lands in the running memory service under the Core namespace
and re-enters every future session through `session-start` and `search`. The two
things that break the loop are equally important to guard: **nothing promotes**
(silent no-op on a downed service) and **SUT-specific cruft leaks into Core**
(neutrality is policy, not code).

---

*Protocol authority: ADR-BUGATE-001 (Promotion Rule + Consequences). Mechanism:
`scripts/memory_bus.py` (`note`/`search`/`handoff`/`session-start`) and
`bin/promote-memory`. The generalization gate is policy, not a script.*
