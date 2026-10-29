# BUGate v2 + DSH-only delivery

English | [简体中文](DELIVERY.zh-CN.md)

**Final status: COMPLETE.** All 48 applicable HC/AC entries pass. AC-33 is retired by the explicit scope change and is not counted as PASS. Three additional no-legacy checks pass. The new package installs and runs with the operating system denying access to the source checkout.

Date: 2026-10-29. Implementation commit: `e40ce1f739899acf1b88084c537ae62b871282b6`, branch `codex/v2-dsh-only`; baseline: `61e1327a7bf01505cc82760782ffa1272ccaa03f`. Reports and raw evidence are committed separately. No push, publication, release replacement or history rewrite occurred.

## Delivered architecture

The product now consists of the host-neutral Python v2 evaluator, canonical Protocol/Schema/MethodSpec/reason codes, unified CLI, native DSH adapter and necessary packaging/tests. The [removal and replacement inventory](CLEANUP.md) records 249 deleted tracked files: old host hooks/projections, installers/updaters, role/orchestration/memory runtimes and their exclusive tests.

`legacy.py`, the `legacy_precode` branch and old script imports, calls and digest dependencies are removed. Historical input is explicitly rejected. [Native semantics retention](CORE_SEMANTICS.md) maps business understanding, testability, Oracle coverage, source plans, review and optional models to typed v2 objects and negative tests. Common invariants and claim dimensions have one source in `quality.json`; DSH Skills are generated from canonical resources.

References, snapshots, provenance, claims, PreparedProtocolContext, atomic publication and conflicts remain. Real DSH interfaces provide synchronous context/guards and awaited preparation, evidence durability and lifecycle operations. BUGate adds no Agent loop or required memory service.

Finite resource/member checks validate the source and actual archives, including the sole `bugate` console entry. The core wheel contains 12 modules, 24 canonical resources and five metadata files; the bundle has 46 members with no old host/runtime payload. Bilingual architecture, capabilities, installation, usage and contributor documentation are current. See the [frozen contract](CONTRACT.md) and [integration review](REVIEW.md).

## Executed acceptance

| Scope | Result | Evidence |
| --- | --- | --- |
| Core, native semantics, negative/boundary tests | 96 PASS | [Core log](evidence/core-tests.log) |
| Distribution boundary tests and actual archive scan | 4 PASS; scan PASS | [Tests](evidence/distribution-tests-final.log), [scan](evidence/distribution-final.json) |
| Actual DSH services and lifecycle | 22 PASS, no FAIL/SKIP | [Runtime log](dsh/runtime-final.log) |
| Installer boundaries | 15 PASS | [Boundary log](dsh/install-boundaries.log) |
| Clean installation, repeat, upgrade, recovery, removal/rollback | 19 steps PASS | [Result](dsh/clean-install/result.json), [commands](dsh/clean-install/commands.json) |
| Published dev.1 → new dev.2 → original archive rollback | 9 steps PASS | [Result](dsh/published-upgrade/result.json), [commands](dsh/published-upgrade/commands.json) |
| Installation with OS-denied checkout reads | PASS | [Policy and command](dsh/source-denied-install/result.json) |
| Installed real runtime under the same restriction | PASS: 27 model requests, two actual tests, persisted evidence and current satisfied requirement claim | [Result](dsh/standalone-runtime/result.json), [log](dsh/standalone-runtime/runtime.log) |

Every original HC-01–15 / AC-01–34 and added NL-01–03 entry retains its requirement and maps to current evidence in the [table](ACCEPTANCE.md) and [machine matrix](acceptance.json). Existing non-compatibility core tests remain; retired tests are documented, not counted as passing.

Core command: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests/v2 -v`. Distribution command: `.venv/bin/python scripts/check_distribution.py --bundle dist/v2-dsh-only/bugate-deepseek-harness-2.0.0-dev.2.tgz`. The [DSH report](dsh/REPORT.md) and adjacent command records contain actual build, runtime, install and recovery invocations.

## Versions and installable artifacts

Core: `2.0.0a2`; bundle: `2.0.0-dev.2`; Protocol: `2.0.0`, digest `sha256:053717ac25e49aa4a03e2a11176c3e9263d1a5d89b0388f0eaaa3a7b568fd77e`.

Executed target: DSH `0.2.2-alpha.2`, source `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`, Node `26.8.1`, Python `3.13.3`, macOS `26.5.1` arm64. [Interface evidence](dsh/environment-and-interfaces.json) records actual source locations and hashes.

New local artifacts are in `dist/v2-dsh-only/`; these are not old release URLs:

| Artifact | SHA-256 |
| --- | --- |
| [DSH bundle](../../../dist/v2-dsh-only/bugate-deepseek-harness-2.0.0-dev.2.tgz) | `1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0` |
| [Core wheel](../../../dist/v2-dsh-only/bugate_core-2.0.0a2-py3-none-any.whl) | `56813e878d17defd62120e69b17c58095e598a9292c5668e1cee80de5eb3b5d9` |
| [Bootstrap installer](../../../dist/v2-dsh-only/install_dsh.py) | `85d354ebcb36bd901bafd49be1ae38fd6b0ac4dc775957cabec3b37c4494d67f` |

[Checksums](evidence/SHA256SUMS), [build manifest](evidence/build-manifest.json) and [52 build-input hashes](evidence/source-manifest.json) are retained. Use the [installation prompt](../../../INSTALL_DSH_PROMPT.md) or [guide](../../DEEPSEEK_HARNESS.md), copying the new artifacts together to an ABI-matching environment.

Installation upgrade is separate from task migration. Actual dev.1 installations upgrade to dev.2; old task locks produce `PROTOCOL_MISMATCH` without modification. Archive rollback restores the old package and its task preparation. Automatic v0.x/old-artifact migration is unsupported. Removal and recovery retain task facts, unrelated configuration and other plugins.

## Boundaries and recovery

No applicable engineering acceptance or external blocker remains. **Live-model testing is NOT_RUN**: these results use deterministic model fixtures with real DSH and real Python execution, and do not establish model effectiveness or token savings. Linux CI is updated but not executed locally; Windows, browser Python and remote providers are outside the verified target. Bundled wheels are platform/ABI-bound; DSH peer dependencies may require registry access.

Hashes establish consistency, not hostile same-user authentication. Guards cover declared managed tools, not arbitrary machine writes. Genuine product failures, zero cases, all skipped, cancellation and environment failures retain distinct outcomes.

The initial parallel resource edit correctly caused `PROJECTION_STALE`; [original output](dsh/runtime-initial.log) remains. After resource freeze and rebuild, all final tests passed without weakened assertions. The [integration audit](evidence/integration-audit.json) confirms unchanged tag references, the clean protected nested worktree and no build-input drift. Historical source remains recoverable from Git. Other projects and global host configurations were not cleaned.
