# BUGATE-CORE-2026-07-20-FULL-CHECK-FIXTURE-PROFILE-LEAK

- Classification: BUGate Core full-check fixture-isolation defect
- Status: fixed in v0.4.1
- Found: 2026-07-20 downstream imported-mode acceptance of v0.4.0
- Affected surface: `.shared/skills/bugate-full-check/scripts/run_full_check.py`
- Affected release: v0.4.0

## Symptom

The v0.4.0 Core smoke check passes, but the same released full-check run from an
imported workspace whose active profile sets `role_governance.mode: required`
reports one false failure:

```text
Hardening flags enforce (multiview) | FAIL
```

The semantic negative itself returns the expected exit code and
`divergence_report` marker. The hidden failed predicate is the preceding
fixture initialization (`init_ok == false`). A direct reproduction shows the
orchestrator returning exit 2 before creating the temporary UC:

```text
BUGate role governance BLOCKED (pre_code):
  - artifact_dir must be inside the governed workspace
```

All required-mode role-chain, strict-Memory, Wave 8, and activation checks in
the same imported smoke run pass. This is a BUGate Core test-infrastructure
defect, not a SUT defect and not evidence that the lifecycle gate should be
weakened.

## Evidenced root cause

The hardening probe creates its UC under a system temporary directory and runs
`sdtd_orchestrator.py --init` without a probe-owned `BUGATE_PROFILE`. In an
imported layout, `load_config()` therefore resolves the workspace's active
required profile. Required role preflight correctly rejects the out-of-workspace
synthetic artifact before initialization. Only the later semantic command is
given the temporary hardening profile, so the final report exposes the
semantic result but not the failed init result.

The full-mode real-peer fixture has the same isolation gap: its init and bridge
commands can inherit SUT-specific role/hardening configuration instead of a
SUT-neutral probe profile.

## Fix and regression control

Synthetic Core-template, guard, hardening, dialect, and real-peer probes now
receive probe-owned configuration instead of inheriting the caller's SUT
profile. Temporary peer dispatch also uses its temporary project root as CWD
and the dedicated `project:bugate-full-check` Memory namespace.

The hardening control creates its profile before init, sets
`role_governance.mode: off`, verifies all five pre-code files, requires a
baseline semantic PASS without multiview hardening, and only then requires the
precise missing-`divergence_report` failure. Its report exposes init, baseline,
and hardened-semantic outcomes independently.

Codex normally rejects a non-git CWD. Both peer bridges therefore expose a
default-off `SDTD_CODEX_SKIP_GIT_REPO_CHECK=1` automation opt-in. Only the
isolated full-check peer probe enables it; normal imported dispatch retains the
repository check, Codex remains in `read-only` sandbox mode, and hook trust is
not bypassed.

`tests/test_full_check_layouts.py` proves that an outer imported required
profile blocks the old unisolated init, while the new hardening probe passes,
leaves the outer workspace unchanged, and sends init/multiview/adversarial
through one temporary profile, root, CWD, and Memory namespace.
`tests/test_peer_role_env.py` pins the new Codex flag as explicit opt-in and
continues to prove lifecycle identity stripping.

After the repair, compileall, all 13 `tests/test_*.py` files, the de-SUT guard,
template pre-code semantics, and Core smoke passed. Core full passed without
degraded flags: both real Codex/Claude dispatch stages, the required-mode role
chain, and strict Memory exact-ID verification were green; only the expected
Core activation-boundary warning remained.

Release-download and downstream imported smoke/full acceptance remain release
gates, not evidence that may be inferred from the source-tree result.

The published v0.4.0 tag and assets are immutable. The fix must ship as a new
SemVer patch release; v0.4.0 must not be retagged or replaced.
