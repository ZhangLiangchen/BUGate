# /integrate-qa-methodology

Claude adapter for the BUGate methodology onboarding workflow.

1. Read the canonical body:
   `.shared/skills/bugate/integration/commands/integrate-qa-methodology.md`.
2. Read `.shared/skills/bugate/SKILL.md` — it is the source of truth on any conflict.
3. Run the five phases exactly as written: read-only inventory of the existing
   QA setup, then read `docs/qa-methodology/METHOD.md` and `SOP.md`, then a
   gap/overlap matrix and gated plan, then approval-gated implementation, then a
   minimal Wave 0 dry run.
4. Write all onboarding artifacts under the workspace-local output directory
   `docs/qa-methodology-integration/`, resolved relative to the governed
   workspace root (the nearest `bugate.config.yaml`) — the SUT repo's own docs
   area in imported mode. Do not write them into
   `.shared/skills/bugate/integration/workbench/`; that path is an engine
   (self-)development convenience only, and the vendored kit carries no gitignore
   exemption for it.
5. Stop after the plan and wait for explicit approval before changing any file.
