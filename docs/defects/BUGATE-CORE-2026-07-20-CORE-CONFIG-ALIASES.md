# BUGATE-CORE-2026-07-20-CORE-CONFIG-ALIASES

- Classification: BUGate Core compatibility defect
- Status: fixed before v0.4.0 release
- Found: 2026-07-20 pre-release legacy-consumer audit
- Affected surface: `bugate_core.load_config()` and the documented startup probe

## Symptom

The committed config has always declared `bugate.mode` and `bugate.version` as
nested keys. The v0.3.x simple parser exposed them through the observable
top-level aliases `mode` and `version`, and the bilingual startup contract
continued to read `config.get("mode")`. After nested parsing was introduced,
the startup probe returned `mode=None` even though `bugate.mode` remained
present.

## Evidenced root cause

The nested/deep-merge migration intentionally removed generic flattening but
did not preserve the two legacy aliases that were already part of BUGate's
public startup behavior. This was a Core compatibility defect, not a SUT
configuration error. It is distinct from `role_governance.mode`; the two modes
must never overwrite each other.

## Fix and regression control

Config loading now canonicalizes only `bugate.mode` and `bugate.version` to
their legacy top-level aliases, per document and before deep merge. A nested
value wins a same-document conflict; a legacy profile alias can still override
the base document. Generic nested keys remain nested.

`tests/test_config_nested_merge.py` verifies both alias forms, same-document
precedence, base/profile merge behavior, and non-collision with
`role_governance.mode`. The startup probe again reports `mode=core` and
`version=0.1`.
