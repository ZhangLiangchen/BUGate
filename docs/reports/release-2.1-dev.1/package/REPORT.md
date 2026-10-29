# Release package verification

English | [简体中文](REPORT.zh-CN.md)

PASS for the package build and installation scope below. Actual installed DSH
hydration is recorded separately in [runtime evidence](../runtime/REPORT.md).
This report does not assert hosted CI, public release availability or live-model
validation.

The release bundle was built from commit
`062b03f2895a7ddabe7ee848d5b8f047a1826da5`. All 57 declared source inputs match that
commit and the working files. Compared with implementation commit
`c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60`, only the English and Chinese
`protocol/v2/README` files changed: stale versions were corrected without adding
business rules. See [source matching](source-commit-match.json).

The six assets are in `dist/release-2.1-dev.1`; the
[checksum inventory](checksums.json) records every byte size and SHA-256.

| Asset | SHA-256 |
|---|---|
| Native bundle | `0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c` |
| Python wheel | `e932749f78cc708608d3368cfe75957d9610e1726244862e04ea627d68a0db8c` |
| Installer | `2ea459ed326ddde989a16bb9153fd5536a117838dd5d6f5f3a8a3c7bfca2604a` |

Native build, standalone build, source checks and bundle checks passed. The
bundle contains the current Python/protocol resources and required wheelhouse;
the source boundary check reports 13 Python modules and 28 protocol files.
The offline wheelhouse targets CPython 3.13/macOS arm64; other ABIs were not
validated by this package run.

The bundle and five companion assets were copied to a new external temporary
directory before installation. Every one of the 20 lifecycle commands ran under
an OS policy denying reads from the source repository, and the first command
proved that reading `pyproject.toml` raises `PermissionError`. See
[denial attestation](source-denial-attestation.json), [exact commands](clean-install/commands.json),
[result](clean-install/result.json), and [locations](locations.json).

The lifecycle covered clean bootstrap, repeat installation, four healthy doctor
observations, task-rebind refusal, stale projection detection, a controlled
metadata-only upgrade fixture and rollback, removal/repeat removal, host mutation failure recovery,
unowned-bootstrap refusal and post-record recovery. Expected negative-path exit
codes remain in the raw records; PASS means their original assertions held.
Doctor reports `configuration_verified` and hydration `unobserved`, not Agent
execution proof.

The old `scope-convergence` bundle remains unchanged. Its nine actual prior-v2
archive upgrade commands were not rerun or attributed to this release bundle.
The documentation-only rebuild changes content digests, including the protocol
and evaluator bindings, while retaining the previously unpublished component
versions. Earlier local 2.1 tasks require the existing explicit new-task workflow;
their old history is preserved rather than silently migrated.

Reproduction commands (the lifecycle command uses the external paths in
`locations.json`):

```sh
npm run build --prefix adapters/deepseek-harness/plugin
python3 scripts/check_distribution.py
python3 packaging/build_dsh.py --out-dir dist/release-2.1-dev.1
python3 scripts/check_distribution.py \
  --bundle dist/release-2.1-dev.1/bugate-deepseek-harness-2.1.0-dev.1.tgz
python3 tests/integration_install/accept_package.py \
  --package EXTERNAL_ASSETS/bugate-deepseek-harness-2.1.0-dev.1.tgz \
  --dsh /Users/zhangliangchen/.local/bin/dsh \
  --out-dir EXTERNAL_CLEAN_INSTALL \
  --deny-source-root /Users/zhangliangchen/PycharmProjects/BUGate
```

Build logs are [native-build.stdout](native-build.stdout) and
[build.stdout](build.stdout). Machine conclusions are in [result.json](result.json).
