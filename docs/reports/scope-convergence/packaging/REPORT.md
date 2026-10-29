# Package and installation verification

English | [简体中文](REPORT.zh-CN.md)

This report supports formal requirements **F-07** (independent package and supported
installation lifecycle) and **F-08** (consistent artifacts, documentation and
honest delivery) in the [source-verified matrix](../ACCEPTANCE_MATRIX.md). Test
counts are observations of selected checks, not an acceptance threshold. Historical
SC labels do not authorize tests or create a fixed completion gate.

Current state: **package/build/installation and installed-runtime PASS** for source commit
`c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60` and bundle SHA-256
`4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`. All 57 build inputs match
that commit's Git blobs. Both earlier builds remain intermediate evidence only.
The [installed-runtime result](../dsh/installed-runtime-result.json) independently
pins the same bundle SHA and passed with source reads denied. Its real DSH plus
deterministic-model test executes both a passing flow and a genuine failing flow.
The failing flow permits execution closure while requirement verification stays
unsatisfied; suggestions do not trigger completion rework. Package installation
does not substitute for that separate runtime proof.

The final source-denied clean lifecycle completed 20 selected command checks,
and the actual `2.0.0-dev.2` → `2.1.0-dev.1` archive upgrade/rollback completed 9.
Both result files pin this exact final bundle digest. No fixed test count is an
acceptance requirement.

## Final artifacts and observed commands

| Artifact/check | Evidence |
|---|---|
| Native bundle and Python wheel | [build manifest](build-manifest.json), [SHA256SUMS](SHA256SUMS) |
| Source commit correspondence | [57 matching source digests](source-commit-match.json), [source manifest](source-manifest.json) |
| Build and finite wheel/native equality | `python3 packaging/build_dsh.py --out-dir dist/scope-convergence`, exit 0; [output](build.stdout), [boundary result](bundle-boundaries.json) |
| OS source-denied clean installation and lifecycle | `python3 tests/integration_install/accept_package.py ... --deny-source-root /Users/zhangliangchen/PycharmProjects/BUGate`, exit 0; [20 exact commands](clean-install/commands.json), [result](clean-install/result.json), [read-denial probe](clean-install/00-source-denial-probe.stdout) |
| Prior published v2 archive → current artifact, then rollback | `python3 tests/integration_install/accept_published_upgrade.py ...`, exit 0; [9 exact commands](actual-upgrade/commands.json), [result](actual-upgrade/result.json) |
| Current one-step installation prompt | [English](../../../../INSTALL_DSH_PROMPT.md), [Chinese](../../../../INSTALL_DSH_PROMPT.zh-CN.md); both pin the final bundle digest. |

Six standalone delivery files are under `dist/scope-convergence/`: the `.tgz`,
core `.whl`, `install_dsh.py`, `build-manifest.json`, `source-manifest.json` and
`SHA256SUMS`. Every checksum was checked again after copying the set outside the
checkout; see [checksum verification](checksums.json). The wheelhouse target is
Python `3.13.3`, macOS arm64 (`cpython-313-darwin`), recorded in
[wheel target](wheel-target.json). This is a local delivery, not a public release.

## Established packaging checks

| Check | Observed result | Evidence |
|---|---|---|
| Distribution regressions | 4 tests passed, including forbidden legacy/credential payloads and wheel metadata identity. | [output](distribution-tests.stdout) |
| Installation boundary regressions | 15 tests passed, covering ownership, paths, locks, timeout, ABI and exact bundle core/protocol pair validation. | [output](install-boundaries.stdout) |
| Finite resource/source inventory | 13 Python modules and 28 canonical protocol resources passed. | [result](source-boundaries.json) |
| Documentation source basis | Existing rules read from immutable pre-change commit; candidate examples are not original authority. | [review](basis-review.json) |

The standalone installer reads the current archive's pinned compatibility record.
It does not hardcode the prior Python distribution version. Native resources and
wheel resources are compared byte-for-byte by the build, and the current Python
module and package version declarations must agree. The built package contains no
source checkout path, retired runtime, test fixture, credential or editable install.

The clean installation test uses a new external project, DSH home/profile and
owned bootstrap venv. With `--deny-source-root`, macOS `sandbox-exec` denies
product-process reads of the BUGate checkout; an explicit read attempt must fail
before installation. The raw result has a test-runner metadata variable-shadowing
defect: its `source_denied` field contains the later rejection argv. The original
result is preserved. [Source-denial attestation](source-denial-attestation.json)
independently verifies the exact sandbox prefix on all 20 saved commands and the
negative read probe. Only the runner variable was renamed; no product or package
bytes changed and no installation rerun is claimed. The same deny policy covers install/repeat/doctor, upgrade,
rollback, removal and crash-recovery commands. A different execution environment
is not silently substituted for this OS-level proof.

## Retained intermediate evidence

| Artifact | Installation observations | Why superseded |
|---|---|---|
| `67884700a4f54e042a24958d4bd201509e24cb400e1b007ff3d6041a374e3946` | Source-denied lifecycle 20 command checks and actual prior-v2 archive upgrade/rollback 9 checks passed. | Functional summary had to include still-applicable failures omitted from claim-selected runs. [record](intermediate-1/status.json) |
| `06e79269e9ae5cc02655710584a9d1a13990dcf365979c61482d1fd1ae738b4d` | Source-denied lifecycle 20 command checks and actual archive upgrade/rollback 9 checks passed. | Real runtime found immutable assessment collision after adding a suggestion. [record](intermediate-2/status.json) |

Each intermediate directory retains the exact manifest, checksums, command logs,
raw stdout/stderr and result JSON. These are passing observations for those bytes,
not proof of the corrected final artifact. The real runtime failure remains in
the adapter evidence and is not relabeled as an optional issue.

## Boundaries

The target is DSH `0.2.2-alpha.2`, POSIX providers and the Python/platform ABI in
the bundle's `python/inventory.json`. Windows is unsupported; an added Linux CI
workflow is not a locally observed Linux result. Python dependencies install
offline; DSH peer dependency resolution may use the registry. No public tag or
release is published or overwritten.

Installation/doctor proves package configuration and integrity, not Agent
hydration or business acceptance. Only the actual installed-runtime record can
establish the latter invocation path. Deterministic model fixtures are separate
from live-model validation; live-model quality or productivity is not inferred.
