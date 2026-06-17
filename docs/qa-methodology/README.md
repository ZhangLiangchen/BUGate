# BUGate QA Methodology — Index

SUT-neutral navigation index for the methodology doc set. This directory holds
reusable method and operating guidance only; SUT-specific facts live in a
profile or mounted workspace. On any conflict,
`.shared/skills/bugate/SKILL.md` is the canonical source.

## Documents

| File | Purpose |
|---|---|
| [METHOD.md](METHOD.md) | The "why": full AI-assisted black-box test methodology, the nine-Wave flow, theory mapping, and rationale. |
| [SOP.md](SOP.md) | The "what to do next": step-by-step execution handbook for the Wave 0–3 minimum viable loop. |
| [EXPERIENCE_PROMOTION_PROTOCOL.md](EXPERIENCE_PROMOTION_PROTOCOL.md) | How a SUT-local lesson is decided to either stay local or be promoted into SUT-neutral BUGate Core. |
| [BUGATE_PLATFORM_DECOUPLING_ADR.md](BUGATE_PLATFORM_DECOUPLING_ADR.md) | ADR-BUGATE-001: the accepted Core / Profile / SUT three-layer architecture and its promotion rule. |
| [BUGATE_EVOLUTION_TIMELINE.md](BUGATE_EVOLUTION_TIMELINE.md) | SUT-neutral summary of how BUGate evolved from a method into a profile-driven pre-code governance framework. |

## Recommended reading order

1. `METHOD.md` — understand the method and its reasoning first.
2. `SOP.md` — then learn how to execute it day to day.
3. `BUGATE_PLATFORM_DECOUPLING_ADR.md` — the architecture that keeps Core reusable.
4. `EXPERIENCE_PROMOTION_PROTOCOL.md` — how learning compounds back into Core.
5. `BUGATE_EVOLUTION_TIMELINE.md` — optional background on how it all came to be.
