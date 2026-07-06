# integrate-qa-methodology

Your task is to integrate the BUGate "business-understanding constraint layer"
methodology (`docs/qa-methodology/METHOD.md` + `docs/qa-methodology/SOP.md`) into
a project's existing Claude Code / agent configuration.

This is the canonical body. Runtime adapter cards (e.g.
`.shared/skills/bugate/adapters/claude/commands/integrate-qa-methodology.md`)
point here. On any conflict, `.shared/skills/bugate/SKILL.md` is the source of
truth.

## Key premise

A target project usually already has *some* gate-like setup — skills, hooks,
possibly agents, slash commands, `.claude/rules/`, `.githooks/`, and local
integration output. The new methodology overlaps with that setup in intent,
but its implementation details, naming conventions, and file locations may be
entirely different.

**Your task is not to replace the old scheme with the new one. It is to:**

1. Fully map what the existing scheme covers.
2. Preserve working prior investment (do not rewrite for consistency's sake).
3. Fill in what the methodology requires but the existing scheme lacks.
4. In the overlap, make both cooperate with the smallest possible change.

All generated onboarding artifacts go under a **workspace-local output
directory**, `docs/qa-methodology-integration/`, resolved relative to the
active project root (the nearest `bugate.config.yaml` walking up from CWD).
In imported mode this is the SUT repo's own docs area, so the generated output
lives with the host project — never inside the vendored kit subtree. Do **not**
write generated output into `.shared/skills/bugate/integration/scratch-output/`;
that directory is a maintainer convenience for engine self-development only (see
its `README.md`) and, when the kit is vendored to `<sut>/.bugate/.shared/...`, it
carries no gitignore exemption, so committing `.bugate/` would sweep generated
output back into the reusable kit. Below, `<output-dir>` means this
workspace-local `docs/qa-methodology-integration/` path.

## Five phases, in strict order

### Phase 1: Inventory the existing scheme (read-only, no edits at any point)

Using Read / Glob / Grep only, fully explore the project's existing QA- and
test-related configuration, in this order:

1. The `.claude/` tree: skills/, agents/, rules/, hooks/, commands/.
2. Any prior integration output directory the project already carries (e.g. a
   previous `docs/qa-methodology-integration/`), paying special attention to any
   CONTEXT / PROGRESS / CONVENTIONS files.
3. Repo root: CLAUDE.md, AGENTS.md, PROGRESS.md, README.md.
4. `.githooks/`, `scripts/`, `hooks/` — every QA/test-related script.
5. Any `.md` mentioning: business brief, propositions, oracle, audit,
   interview, validated model, gherkin, BDD, Wave, quality gate, testability.

For each relevant file, emit a **factual summary** (describe, do not explain):

- File path.
- What mechanism it implements (one or two sentences).
- Its role in the workflow (skill / agent / hook / rule / command / other).
- Trigger conditions if any (glob pattern, PreToolUse intercept path, etc.).

**Forbidden throughout Phase 1:**

- Editing, creating, or deleting any file.
- Running any bash command with write side effects.
- Assuming a file's contents from its name — you must actually Read it.
- Offering any "recommendation" before all relevant files are read.

At the end of Phase 1, write
`<output-dir>/current-scheme-inventory.md`.

### Phase 2: Read the methodology

Read `docs/qa-methodology/METHOD.md` and `docs/qa-methodology/SOP.md` and build a
complete understanding. **Capture deliberately** (not just skim):

- For each of the nine Waves: inputs, outputs, pass criteria, forbidden actions.
- The three-layer agent isolation rules (`business-model-builder` /
  `test-case-designer` / `test-code-implementer`) and each layer's
  readable / forbidden paths.
- The PreToolUse hook intercept design.
- What the pre-commit / gate check scripts should validate.
- Which sub-directories and artifacts the workspace-local output directory
  should grow.
- The METHOD.md frontmatter changelog and its v1.0 -> v1.1 changes.

Also note how the nine Waves converge onto the published BUGate engine: the
01-05 gate artifact stack plus `scripts/` gates and orchestration, and the
`.shared/skills/bugate/` skill and adapters. Propositions/oracles land in
`01_business_brief.md`, the layer decision lands in `02_testability.md`, the case
inventory lands in `03_inventory.yaml`, and so on.

At the end of Phase 2, write
`<output-dir>/methodology-requirements.md`, listing every agent / hook / script
/ schema / artifact the methodology requires.

### Phase 3: Gap and overlap analysis (produce a reviewable, gated plan)

From the Phase 1 and Phase 2 outputs, write
`<output-dir>/integration-plan.md` with this structure:

```markdown
# Integration plan: existing gate x business-understanding constraint layer

## 1. Existing-scheme summary
(one line per skill/agent/hook, stating the problem it solves)

## 2. Methodology requirement list
(one group per Wave, listing every artifact that Wave needs)

## 3. Overlap and gap matrix

| Methodology requirement | Existing counterpart | Overlap | Recommendation |
|---|---|---|---|
| Wave 0 PRD health check | (if any) xxx | full / partial / none | keep / extend / build |
| Wave 2 reference-traceability audit | ... | ... | ... |
| Three-layer agent isolation hook | ... | ... | ... |
| Proposition schema | ... | ... | ... |
| ... | ... | ... | ... |

## 4. Implementation order

(list every change under one of these buckets)

### 4.1 Keep as is
(existing parts already covered; list explicitly so nothing is changed by mistake)

### 4.2 Minimal extension
(parts that need only a field or branch added to an existing skill/hook; give a concrete diff idea)

### 4.3 Build new
(parts the methodology requires and the existing scheme entirely lacks; give a new-file list)

### 4.4 Naming and location reconciliation
(if existing uses one name and the methodology uses another, decide which name stays)

## 5. Risks and decision points

(open questions that need your sign-off, each with 2-3 options)

For example:
- Existing skill X and methodology agent Y overlap ~70% — merge / coexist / replace?
- Existing hook intercepts one path set, methodology wants a wider set — widen it?
- Existing decision/output dir is close to the validated-model/unresolved
  concept — merge them?
```

**Stop after Phase 3 and wait for the user to review `integration-plan.md`.
Do not enter Phase 4 until they explicitly approve.**

### Phase 4: Implementation (only after the user approves `integration-plan.md`)

Implement the items in §4 order, one at a time. **For each completed item:**

1. Briefly report what was done (which files created / modified).
2. Show the change via git diff or a file list.
3. **Stop and wait for the user's confirmation** before the next item.

Implicit rules during implementation:

- Before editing an existing file, Read its current state (avoid editing from
  stale context).
- Any change to an existing skill/hook/rule must add a changelog note at the top
  of the file.
- New files must follow the project's observed naming conventions, directory
  structure, and comment style (you should have noted these in Phase 1).
- If implementation reveals a gap or error in `integration-plan.md`, stop and
  update the plan before continuing — do not drift while building.

### Phase 5: Minimal dry run

Once integration is complete, pick the **smallest verifiable scenario** and run
it end to end:

- Recommended scenario: run Wave 0 (PRD health check) over one PRD section of
  the project.
- Write `<output-dir>/dry-run-report.md` with:
  - The commands / agent calls executed.
  - The actual files produced.
  - A point-by-point comparison against the SOP "Wave 0 pass criteria".
  - Problems hit and any unmet criteria.

For anything the dry run fails, return to the matching Phase 4 item and fix it.

## Global prohibitions

- Do not skip Phase 1 and jump straight into METHOD.md / SOP.md — inventory the
  existing scheme first.
- Do not delete any existing skill/agent/hook the user has not explicitly
  approved.
- Do not modify any file before `integration-plan.md` is approved.
- Do not rewrite the existing scheme into the methodology's naming/structure
  "for consistency" — prefer respecting prior implementation.
- Do not pretend a phase ran — there must be real tool-call records.
- Do not treat the methodology document as gospel — if one of its requirements
  seriously conflicts with the project's reality, list it as a risk / decision
  point in `integration-plan.md` rather than forcing it in.

## Start with Phase 1
