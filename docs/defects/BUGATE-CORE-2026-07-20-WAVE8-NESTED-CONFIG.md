# BUGATE-CORE-2026-07-20-WAVE8-NESTED-CONFIG

- Classification: BUGate Core compatibility defect
- Status: fixed before v0.4.0 release
- Found: 2026-07-20 pre-release compatibility audit
- Affected surface: `bin/wave8-weekly`

## Symptom

The documented nested configuration form
`wave8.evidence_glob` stopped reaching `bin/wave8-weekly` after
`load_config()` moved from the flattening simple parser to the nested parser.
The top-level `wave8_evidence_glob` form continued to work.

## Evidenced root cause

The old loader surfaced an indented `evidence_glob` child as a top-level key.
The new loader correctly preserves `wave8` as a mapping, while the shell
wrapper still queried only top-level keys. This was a Core configuration
consumer mismatch, not a SUT defect.

## Fix and regression control

`bin/wave8-weekly` now supports dotted nested lookup and checks
`wave8.evidence_glob` while retaining both top-level compatibility forms.
`tests/test_config_nested_merge.py` executes the wrapper against a temporary
workspace using the documented nested form and requires the resolved evidence
glob to reach the run.

The full deterministic suite, de-SUT guard, and template pre-code semantics
passed after the fix.
