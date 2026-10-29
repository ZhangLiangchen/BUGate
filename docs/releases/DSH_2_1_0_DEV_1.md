# DSH 2.1.0-dev.1 — acceptance scope and convergence

English | [简体中文](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/releases/DSH_2_1_0_DEV_1.zh-CN.md)

A prerelease of BUGate's host-neutral Python v2 evaluator, unified CLI and native
DeepSeek Harness plugin. This release introduces a first-class acceptance scope
contract and keeps execution focused on sufficient evidence for the user's finite
goals. Earlier public tags and assets remain unchanged.

| Component | Version / target |
|---|---|
| Native package | `@bugate/deepseek-harness 2.1.0-dev.1` |
| Python distribution | `bugate-core 2.1.0a1` |
| Protocol | `2.1.0-dev.1`, with exact content-digest binding |
| DeepSeek Harness | `0.2.2-alpha.2`, source `f3803c96974ad4a94195fb97e5c637e9c2a3afe3` |
| Runtime | Node `24.9.0+`, Python `3.11+`, POSIX providers |
| Published offline wheelhouse | CPython 3.13 / macOS arm64; checked before installation |

## Behavior

`ScopeContract` links actual user-request receipts, goals, allowed environments,
main flows and necessary branches to existing requirements, business Oracles and
the current inventory plan. Required scenarios, bounded auxiliary activities and
non-executable suggestions have separate treatment. A default normal/boundary/
security/performance matrix does not expand the user's objective. Explicitly
requested rejection, boundary or permission behavior remains required.

DSH consumes exact scope/plan/snapshot evaluations before managed execution and
restores the current binding across supported recovery and child-agent paths.
Out-of-scope additions are rejected as executable inventory and recorded as
suggestions. Suggestions and their drafts do not invalidate required execution
inputs or force completion rework. Reads, preparation, cleanup and bounded
failure-linked diagnosis remain available within host permissions.

Execution completeness, functional acceptance, blockers, suggestions and
historical failures are reported separately. A real failed Oracle can complete
execution while requirement verification remains unsatisfied. A named R1 claim
does not require unrelated R2 execution or falsely mark the whole task complete.

The locked Profile fixes the requirement/risk denominator in every scope
revision. Explicit user changes within that denominator create a new scope
revision and invalidate prior plan/evidence applicability. Changing the denominator
requires an explicit new Profile version and a new task through `bugate init`;
the old task and failures remain. There is no same-task Profile migration or
selective evidence reuse. Request receipts establish user choice; Profile sources
remain the authority for domain facts.

## Download and install

Use the exact [dsh-v2.1.0-dev.1 prerelease](https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.1.0-dev.1)
assets together:

- [Native bundle](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/bugate-deepseek-harness-2.1.0-dev.1.tgz)
- [Python core wheel](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/bugate_core-2.1.0a1-py3-none-any.whl)
- [Bootstrap installer](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/install_dsh.py)
- [Build manifest](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/build-manifest.json)
- [Source-input digest manifest](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/source-manifest.json)
- [SHA256SUMS](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/SHA256SUMS)

Verify the complete checksum list before running the installer. Use the
[exact installation Prompt](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/INSTALL_DSH_PROMPT.md)
for download, digest and target-identity checks. No source checkout or editable
installation is needed.

Published bundle SHA-256:
`0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c`.

```sh
shasum -a 256 -c SHA256SUMS
python3 install_dsh.py \
  --package ./bugate-deepseek-harness-2.1.0-dev.1.tgz \
  --venv /absolute/path/bugate-bootstrap \
  --project /absolute/path/test-repo \
  --dsh-profile bugate \
  --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home
/absolute/path/bugate-bootstrap/bin/bugate doctor integrations \
  --project /absolute/path/test-repo --dsh-profile bugate \
  --dsh /absolute/path/to/dsh --dsh-home /absolute/path/dsh-home --json
```

The core wheel is pure Python, but this bundled offline dependency wheelhouse is
specific to CPython 3.13/macOS arm64. A different ABI needs a separately built and
verified bundle. DSH may contact its registry for host peer dependencies. Windows
is unsupported. Setup/doctor verifies installation; actual Agent hydration is a
separate observation. Upgrade/rollback preserves owned archives and task history;
changed protocol digests do not silently migrate existing tasks. Retired host
adapters and old formats are not restored.

Release preparation corrected version text in two bundled protocol README files;
it added no business rules. The previously unpublished component version numbers
remain `2.1.0-dev.1` / `2.1.0a1`, but protocol, evaluator and package digests differ
from the earlier local 2.1 delivery. Those local tasks cannot reuse their old
bindings unconditionally: use the existing explicit new-task initialization path
and preserve the previous tasks and history. See the
[digest transition](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/reports/release-2.1-dev.1/package/digest-transition.json).

The CLI adds `scope record/show/check/request`, `suggestion record/list` and
`execution authorize` alongside the existing artifact/evidence/claim workflow.
`scope request` is trusted operator/host intake, not a model-facing authorization
tool. See [usage](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/DEEPSEEK_HARNESS.md)
and [scope examples](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/ACCEPTANCE_SCOPE.md).

## Observed verification and limits

The **previous implementation-delivery archive** (`scope-convergence`, bundle
SHA-256 `4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`)
records 123 core tests, 4 distribution tests and 15 installation boundary tests.
Actual DSH regression passed 24 tests before the final named-claim fix, followed
by 2 affected-path tests on the fixed source. That earlier archive passed 20
source-denied lifecycle commands, 9 actual prior-v2 archive upgrade/rollback
commands, and an installed real DSH test covering passing and genuinely failing
workflows. These are historical observations, not new runs against the release
bytes.

Those implementation records are fixed at commit `bb6153efc7a334172c36f069c2c160504df8f375`:
[delivery and proof boundaries](https://github.com/ZhangLiangchen/BUGate/blob/bb6153efc7a334172c36f069c2c160504df8f375/docs/reports/scope-convergence/DELIVERY.md),
[formal acceptance](https://github.com/ZhangLiangchen/BUGate/blob/bb6153efc7a334172c36f069c2c160504df8f375/docs/reports/scope-convergence/acceptance.json),
and [real installed-runtime record](https://github.com/ZhangLiangchen/BUGate/blob/bb6153efc7a334172c36f069c2c160504df8f375/docs/reports/scope-convergence/dsh/installed-runtime-result.json).
Statements about “not published” in those sealed reports describe their earlier
local-delivery checkpoint.

The **current release bundle** (`0e9f87a6…4ea0c`) was built from
`062b03f2895a7ddabe7ee848d5b8f047a1826da5`. Its 57 build inputs match that commit;
only the two protocol README files differ from the validated implementation.
Source and bundle boundary checks passed. This exact new bundle passed 20
source-denied installation lifecycle commands, including clean/repeat installation,
doctor, controlled metadata-upgrade/rollback, removal and recovery. It also passed an installed
real DSH runtime test that drives both successful and genuine-failure workflows
with deterministic model fixtures. The prior-v2 archive's 9-command upgrade run
was not repeated for this documentation-only rebuild. See the
[package result](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/reports/release-2.1-dev.1/package/REPORT.md)
and [exact installed-runtime result](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/reports/release-2.1-dev.1/runtime/installed-runtime-result.json).
Test counts describe observations; assistant-authored candidate examples are not
a fixed acceptance threshold.

The verified runtime uses deterministic model fixtures and real DSH tools/processes;
live-model validation was not run. Managed admission does not infer all business
semantics from arbitrary shell commands or authenticate a hostile actor owning all
local files. Static validity and hashes do not prove business truth. This conclusion
covers only the agreed BUGate implementation and verification scope; it does not
claim whole-product safety or measured model productivity benefits. Release-commit
hosted CI and downloaded asset byte verification are recorded separately by the
publisher, not inferred from local test results.
