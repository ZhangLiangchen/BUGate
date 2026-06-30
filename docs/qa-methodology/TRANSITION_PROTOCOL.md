---
type: protocol
id: PROTO-BUGATE-TRANS-001
title: Transition Protocol — from an embedded SUT-coupled gate stack to SUT-neutral BUGate Core
status: accepted
created_at: 2026-06-29
authority: ADR-BUGATE-001
companion: BUGATE_PLATFORM_DECOUPLING_ADR.md
---

# Transition Protocol

> *How a BUGate gate stack that grew up embedded inside one system under test
> (SUT) is migrated to the SUT-neutral Core — without symmetric dual-maintenance,
> without dragging product detail into Core, and without backfilling behavior that
> was already correct.*

---

## 0. Purpose & Scope

`BUGATE_PLATFORM_DECOUPLING_ADR.md` (ADR-BUGATE-001, `status: accepted`,
2026-06-16) describes the **end state**: a four-part split (Core / Profile /
Mounted Workspace / SUT Runtime) with a one-way Promotion Rule.
`EXPERIENCE_PROMOTION_PROTOCOL.md` (PROTO-BUGATE-EXP-001) describes how a single
**memory** is promoted upward and inward. Neither describes the **journey** — the
finite, auditable process of moving a gate stack that originally lived embedded
inside one SUT into the neutral Core, and of deciding when the old embedded stack
may finally be retired.

This protocol defines that journey. It fixes one decision and reuses one
mechanism:

- **The decision** — for each capability the embedded stack has that Core does
  not yet match, into which of exactly three buckets does it fall, and therefore
  where does it land?
- **The mechanism** — the **same** memory-bus promotion loop from
  PROTO-BUGATE-EXP-001. This protocol does **not** introduce a new tool; it adds a
  ledger discipline and an exit gate on top of the existing one.

The protocol is the **operational expansion** of the ADR's Rejected Alternatives
and Promotion Rule, applied across time rather than at a single moment. Those
sections are the governing text; this document does not override them, it makes
the migration executable. Core is SUT-neutral by construction (ADR Decision:
Core "must not depend on any single SUT kind, path, business entity, environment,
or resource naming scheme"); everything below is shaped to keep that property
intact while the embedded stack is drained into it.

---

## 1. The Asymmetric Strangler-Fig — why migration is one-directional

A capability that exists in two places must be governed in one of two ways. The
choice is the whole architecture of the transition.

### 1.1 The two postures

| Posture | What it means | Verdict |
|---|---|---|
| **Symmetric dual-maintenance** | Old embedded stack and new Core are both actively developed; a fix is written twice, once on each side, kept in sync by hand. | **Rejected.** |
| **Asymmetric strangler-fig** | Old embedded stack is **frozen** as a read-only reference and a fallback executor; new Core is the **only** surface that receives active development. Capability flows one way: old → Core, never back. | **Adopted.** |

### 1.2 Why symmetric dual-maintenance is rejected

It is the ADR's already-rejected alternative **"fork the whole gate stack per
product"** wearing a temporal costume. The ADR rejects that fork "because rules
drift and learning does not compound." Two actively-developed gate stacks are two
forks:

- **Rules drift.** A gate criterion fixed on one side and forgotten on the other
  diverges silently; the two stacks slowly disagree about what a gate even
  checks.
- **Learning does not compound.** Every lesson must be relearned and re-encoded
  on both sides. The promotion loop of PROTO-BUGATE-EXP-001 — whose entire reason
  to exist is that "learning can compound into Core" — is defeated the moment Core
  is not the single sink.

So dual-maintenance does not merely cost twice as much; it actively destroys the
property the ADR was written to protect. The transition therefore must be
asymmetric.

### 1.3 What "frozen reference + fallback" means concretely

The old embedded stack is not deleted at the start of the transition. It keeps
two passive roles, both read-only with respect to development:

1. **Read-only reference.** When a Core layer is a stub and the embedded stack
   had working logic, the embedded logic is the *source* a migration reads from.
   It is never edited to "improve" it — improvements land in Core.
2. **Fallback executor.** Until Core matches a capability, work that needs that
   capability may still be run against the embedded stack. Each such fallback is a
   **recorded event** (§4), not a quiet convenience.

The embedded stack is allowed exactly one kind of write during the transition: a
**freeze marker** (e.g. a freeze tag or a top-of-file "frozen reference — develop
in BUGate Core" note), and only when the task that owns the embedded repo
authorizes it. No behavioral development happens there. The strangler-fig grows
on the Core side; the old trunk is left standing only until it is fully shaded
out (§6).

---

## 2. The Three-Bucket Gap Classifier — where a gap lands

Every capability gap between the embedded stack and Core is sorted into **exactly
one** of three buckets. A gap that seems to belong in two is mis-described and
must be split until each piece belongs to one.

### 2.1 The buckets

| Bucket | Gap kind | Lands in | One-line test |
|---|---|---|---|
| **(a) Neutral capability** | A Core layer/Wave is a stub or regression versus the embedded stack's working logic, and the logic is method-level. | **BUGate Core**, restated SUT-neutral. | The logic survives the §2.3 de-SUT guard with no product token. |
| **(b) SUT contract / data / skill** | An endpoint contract, fixture, resource policy, environment name, or a SUT-specific skill the embedded stack carried. | **SUT Profile** or **Mounted Workspace** — **never Core**. | The capability *requires* a product name/path/entity to state. |
| **(c) Constraint / methodology** | An operating rule from the embedded stack's own `AGENTS.md` (or equivalent). | **Split:** the neutral half → Core docs / `.shared`; the SUT-specific half → Profile. | Part of the rule is statable neutrally; part trips the de-SUT guard. |

### 2.2 The classifier is the de-SUT guard

The classifier is **not** a new judgment call invented for the transition. It is
the **same** admission test PROTO-BUGATE-EXP-001 §4 uses for memory promotion,
mechanized:

> **If a candidate would trip `scripts/check_no_sut_terms.py`, it is (b)/(c)-
> specific and must NOT enter Core.**

This is load-bearing and it is the bridge to the existing protocol. The de-SUT
guard greps the core tree for unambiguous product/identity tokens and exits
non-zero on any match. Running a candidate's *intended Core text* through that
guard is therefore the migration-time form of the ADR Promotion Rule ("a lesson
can enter Core only if it can be stated without referencing a single SUT's
business entities, paths, environments, credentials, or fixtures"). The guard
does not check *meaning* — neutrality of intent is still a human/agent obligation
(PROTO-BUGATE-EXP-001 §4, §7.2) — but a candidate that fails the grep is
**objectively** not bucket (a).

### 2.3 The de-SUT guard as a classification step

The procedure for an ambiguous item:

1. Draft the item **as it would read in Core** (SUT-neutral wording, generic
   "a SUT" / "the product", no product names).
2. Run `python3 scripts/check_no_sut_terms.py`.
3. **Clean** → the item is admissible as bucket (a) on the *neutrality* axis;
   confirm it is genuinely method-level (not a product detail in neutral costume —
   the two-SUT signal of PROTO-BUGATE-EXP-001 §3.1 applies) and land it in Core.
4. **Flags** → at least part of the item is SUT-specific. If the *whole* item
   flags, it is bucket (b): route it to the Profile / Mounted Workspace
   unchanged. If only *part* flags, it is bucket (c): split off the flagging part
   to the Profile and re-run step 2 on the neutral remainder.

The `# bugate: allow-sut-term` marker that the guard honors is **not** a way to
smuggle a product token into Core for the transition — in Core the only
legitimate allowance is the mounted-profile pointer line. A migration that needs
that marker on substantive content has mis-bucketed: the content is (b)/(c).

---

## 3. Stub/Regression vs Correct Handoff — what NOT to migrate

A gap classifier answers *where* a capability lands. It does **not** answer
*whether the embedded behavior is worth migrating at all*. The most common
mistake in a strangler-fig transition is to read every difference between old and
new as a regression and to backfill it. Some differences are the embedded stack
being **wrong by today's standards**, and some are the new stack being **right**.

### 3.1 The distinction

| Embedded behavior | New (Core) behavior | This is a… | Action |
|---|---|---|---|
| Implements working logic for a layer/Wave. | The same layer/Wave is a stub or silently weaker. | **stub / regression** | **Migrate** the neutral logic (bucket (a)), or record a (b)/(c) gap. |
| Produces a result by an obsolete or product-coupled path. | Produces the *correct* result by handing off / deferring. | **correct handoff** | **Do NOT migrate.** Record it as a *resolved* understanding, not a gap. |

### 3.2 The load-bearing example

A Layer-3 specification process that **converges to "this needs Layer 2 / Layer
4"** and stops is exhibiting **correct handoff**, not a capability gap. A
read-only historical spec hardening loop tends to converge on exactly that signal
after a few rounds: the right move is to stop, record the deferral, and accept the
work-in-progress spec — *not* to keep grinding the Layer-3 loop trying to
manufacture an answer Layer 3 cannot own.

If the embedded stack "answered" such a case by reaching past its layer into
product-coupled detail, that is not a capability Core is missing — it is a
boundary the embedded stack violated. Backfilling it would **re-import** exactly
the coupling the ADR rejects. The correct ledger entry is therefore "resolved:
correct handoff to Layer 2/4," which **closes** the question rather than opening a
migration backlog item.

### 3.3 The test

> Before recording a difference as a transition-gap, ask: *is the embedded
> behavior something Core should reproduce, or something Core deliberately
> declines to reproduce?* Only the former is a gap. A correct handoff, a
> deliberate non-feature, or a product-coupled shortcut is **not** a gap and must
> not enter the migration backlog.

This mirrors PROTO-BUGATE-EXP-001's tie-breaker ("when in doubt, keep it in the
SUT profile") one level up: **when in doubt whether a difference is a gap at all,
it is not** — record it as resolved/deferred and move on, rather than minting
backlog that pulls coupling toward Core.

---

## 4. The Transition-Gap Ledger — backlog and retirement gauge in one

The transition needs a single, queryable record of every place the embedded stack
is still load-bearing. That record is **not** a checked-in file — for the same
reason PROTO-BUGATE-EXP-001 §2.1 gives (the memory service is the truth layer, not
a product-specific progress log). It is a set of memory-bus findings.

### 4.1 What gets recorded, and when

Record a ledger entry at the two moments the embedded stack proves it is still
needed:

- **A fallback fired** — work had to run against the embedded executor because
  Core could not yet do it.
- **A migration completed** — a capability was moved into Core (bucket (a)), or a
  gap was routed to Profile/Workspace (bucket (b)/(c)), or a difference was
  resolved as a correct handoff (§3).

### 4.2 How it is recorded — reuse, do not reinvent

Each entry is a normal `memory_bus.py note`, using the **existing** vocabulary and
tag machinery (PROTO-BUGATE-EXP-001 §2). The transition adds only conventions on
top:

```
python3 scripts/memory_bus.py note \
  --agent <a> --type finding --status draft \
  --msg "<what was missing / what was migrated, stated neutrally>" \
  --tag transition-gap --tag bucket:<a|b|c> \
  [--tag resolution:<migrated|routed|correct-handoff|fallback-open>] \
  [--scope ...] [--task ...] \
  [--metadata layer=<n>] [--metadata deferred_to_layer=<n>] \
  [--metadata fallback_count=<n>] [--artifact <evidence>]
```

- `--tag transition-gap` is the ledger selector. `build_tags()` appends raw
  `--tag` values unchanged, so this is a first-class, queryable marker; the
  memory lint treats an off-vocabulary prefix as a *warning* only
  (PROTO-BUGATE-EXP-001 lint: `unknown_tag_prefix`), so it never breaks the write.
- `--tag bucket:<a|b|c>` is the classifier result from §2.
- Bucket/fallback detail that is high-cardinality (counts, layer numbers, IDs)
  goes in `--metadata`, **not** more tags — this respects the lint's
  high-cardinality rule (PROTO-BUGATE-EXP-001: keys with >8 distinct values belong
  in metadata).
- The `--msg` is written **neutrally even for bucket (b)/(c) entries**: the
  ledger lives under the active namespace and must not itself become a leak. State
  "a SUT endpoint contract is unresolved in Core," not the endpoint.

### 4.3 The ledger has two readings

The same query, `memory_bus.py search` (or `recent`) filtered on
`tag:transition-gap`, answers two different questions depending on what you keep:

| Read it as… | You look at… | It tells you… |
|---|---|---|
| **Migration backlog** | open entries: `bucket:a` not yet migrated, `bucket:b/c` not yet routed, `resolution:fallback-open`. | what still has to be moved out of the embedded stack. |
| **Retirement gauge** | the *trend*: are new `bucket:a` fallbacks still appearing, or has the stream gone quiet? | whether the embedded stack is close to retirement (§6). |

This dual use is why the ledger is a stream of findings rather than a static list:
the **rate** at which `transition-gap` findings appear is itself the signal. A
backlog that has stopped growing — specifically, no new bucket-(a) gap — is the
precondition for retirement.

### 4.4 When a ledger finding is promotable

A `transition-gap` finding starts `status:draft`. When a migrated bucket-(a)
capability has settled into Core as a durable, neutral rule, it is eligible for
**promotion** exactly as PROTO-BUGATE-EXP-001 §3 prescribes: run the §4
generalization gate (here, the de-SUT guard of §2 is the mechanized half), then
`bin/promote-memory --from-id <draft-id>` to re-record it `status:confirmed` with
`promoted_from` provenance. The transition does not get its own promotion path —
it feeds the existing one. Bucket (b)/(c) entries are **not** promotable to Core
by construction (they failed §2.2); they are tracked to closure in the Profile,
not promoted.

---

## 5. Which command implements each step

Every command below is from PROTO-BUGATE-EXP-001 / the existing toolchain. The
transition contributes the `transition-gap` convention, not new tooling.

| Step | Command |
|---|---|
| **Classify a gap** | `python3 scripts/check_no_sut_terms.py` on the candidate's intended-Core text (clean → (a)-eligible; flags → (b)/(c)). |
| **Record a fallback / completion (ledger)** | `memory_bus.py note --agent <a> --type finding --status draft --msg "<neutral>" --tag transition-gap --tag bucket:<a\|b\|c> [--metadata ...]` |
| **Read the ledger (backlog / gauge)** | `memory_bus.py search --query "transition gap"` or filter `recent` on `tag:transition-gap`; inspect open entries and the appearance trend. |
| **Promote a settled (a)-migration into Core** | `bin/promote-memory --agent <a> --type finding --msg "<neutral rule>" --from-id <draft-id>` → re-records `status:confirmed` with `promoted_from` (PROTO-BUGATE-EXP-001 §3). |
| **Re-enter the rule in future sessions** | `memory_bus.py session-start --agent <a>` / `memory_bus.py search` (PROTO-BUGATE-EXP-001 §5.6). |
| **Freeze the embedded stack** | A freeze tag / top-of-file note in the *old* repo — only when that repo's owning task authorizes it; never a behavioral change. |

The failure modes of these commands are already documented in
PROTO-BUGATE-EXP-001 §7 and apply unchanged here — most importantly, a downed
memory service makes a write a **silent no-op success** (§7.1), so a ledger entry
must be confirmed with a follow-up `search`, never trusted on exit code alone.

---

## 6. Exit Criteria — when the embedded stack may be retired

The embedded stack stays a frozen reference **until all** of the following hold
simultaneously. Retirement is a gate, not a date.

| # | Criterion | Evidence |
|---|---|---|
| 1 | **No bucket-(a) gap in N sessions.** No new `transition-gap` / `bucket:a` fallback or stub-regression finding has appeared for N consecutive sessions (N fixed by the owning profile/task). | The ledger's retirement-gauge read (§4.3) shows a quiet bucket-(a) stream. |
| 2 | **All bucket-(b) contracts resolve via Profile.** Every SUT contract/data/skill gap the embedded stack carried is satisfied by the SUT Profile or Mounted Workspace — none still requires the embedded executor. | Each `bucket:b` ledger entry is closed against a profile/workspace artifact. |
| 3 | **The embedded `AGENTS.md` is fully partitioned.** Every operating rule from the embedded stack's `AGENTS.md` (bucket (c)) has been split: neutral half landed in Core docs/`.shared`, SUT-specific half landed in the Profile. Nothing rule-bearing remains only in the old repo. | Each `bucket:c` ledger entry is closed; no orphan rule remains old-only. |

### 6.1 Until then

While any criterion is unmet, the embedded stack remains a **frozen reference +
fallback** (§1.3): read-only for development, available as a fallback executor,
and every fallback continues to mint a `transition-gap` finding. Retirement is not
a unilateral cleanup — it is the point at which the ledger *proves* the embedded
stack is no longer load-bearing in any bucket.

### 6.2 What retirement is, and is not

Retirement removes the embedded stack's role as a **fallback executor**. It does
**not** require deleting the old repository — a frozen, tagged read-only
reference may persist indefinitely as provenance, exactly as a promoted Core rule
keeps its `promoted_from` link to the draft it came from (PROTO-BUGATE-EXP-001
§3.3). The transition is complete when Core is the sole executor and the ledger
shows nothing still depends on the old trunk.

---

## 7. The transition in one paragraph

A BUGate gate stack that grew up embedded in one SUT is migrated to Core by an
**asymmetric strangler-fig**: the embedded stack is frozen as a read-only
reference and fallback executor, and Core becomes the only actively-developed
surface — symmetric dual-maintenance is rejected because it is the ADR's "fork per
product" failure (rules drift, learning does not compound) in temporal form.
Every capability gap is sorted into exactly one bucket — **(a)** neutral
capability → Core, **(b)** SUT contract/data/skill → Profile, **(c)** constraint
→ split — and the classifier is the de-SUT guard `scripts/check_no_sut_terms.py`:
if the candidate's intended-Core text trips the guard, it is (b)/(c), not Core. A
difference that is a stub or regression is migrated; a difference that is a
**correct handoff** (e.g. a Layer-3 spec correctly converging to "needs Layer
2/4") is recorded as resolved and **not** backfilled, because backfilling it would
re-import the coupling the ADR forbids. Every fallback and every completion is
recorded to the memory bus as a `status:draft` finding tagged `transition-gap`
with its `bucket:`; that stream is simultaneously the **migration backlog** (open
entries) and the **retirement gauge** (the appearance trend). The embedded stack
is retired only when the ledger proves it: no bucket-(a) gap in N sessions, all
bucket-(b) contracts resolved via Profile, and the embedded `AGENTS.md` fully
partitioned — until then it stays a frozen reference. Throughout, the protocol
reuses the existing promotion loop (`scripts/memory_bus.py note`/`search`,
`bin/promote-memory`) rather than inventing a new one.

---

*Protocol authority: ADR-BUGATE-001 (Rejected Alternatives + Promotion Rule).
Classifier: `scripts/check_no_sut_terms.py`. Mechanism: `scripts/memory_bus.py`
(`note`/`search`/`session-start`) and `bin/promote-memory`, reused from
PROTO-BUGATE-EXP-001 — the transition adds the `transition-gap` ledger convention
and the §6 exit gate, not new tooling.*
