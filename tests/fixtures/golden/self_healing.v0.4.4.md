# BUGate Self-Healing Classification

- Overall: failed
- Exit code: 1
- Primary classification: environment_or_resource
- SUT defect admissible: False
- Next action: exclude environment_or_resource first; do NOT record a SUT defect until infra/environment causes are ruled out, then re-run and re-classify

## Exclusions (must be clear before a SUT-defect verdict)
- test_infrastructure: clear
- environment_or_resource: detected

## Failures
- assertion_failure: `AssertionError|assert .*failed|E\s+assert`
- environment_or_resource: `timeout|connection|network|resource|credential|permission denied`
- sut_behavior_failure: `expected .* got|status code|business|oracle|mismatch` — blocked_by_exclusion: environment_or_resource
