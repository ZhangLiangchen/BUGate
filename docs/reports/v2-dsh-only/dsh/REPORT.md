[简体中文](REPORT.zh-CN.md)

# DSH delivery verification

**PASS for the DSH-owned scope.** The exact delivered bundle is
`bugate-deepseek-harness-2.0.0-dev.2.tgz`, SHA256
`1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0`.
The core is `2.0.0a2`; protocol digest is
`sha256:053717ac25e49aa4a03e2a11176c3e9263d1a5d89b0388f0eaaa3a7b568fd77e`.

| Verification | Actual result | Evidence |
|---|---|---|
| Real DSH services, bridge and lifecycle | 22 passed, zero failed/skipped; 50 model turns, actual compaction, resume, child/preset isolation, PTC, rework, provider boundaries and cancellation | [runtime-result.json](runtime-result.json), [raw log](runtime-final.log) |
| Installer boundaries | 15 passed, including exact v2 core pin and rejection of v0.x/version injection | [result](install-boundaries-result.json), [log](install-boundaries.log) |
| Clean install lifecycle | 19 successful expected outcomes; repeated setup, doctor, corruption rejection, controlled v2 upgrade, rollback, remove and two injected crash recoveries | [result](clean-install/result.json), [commands](clean-install/commands.json) |
| Real published v2 upgrade | 9 successful outcomes; published dev.1 to delivered dev.2, old binding rejected with exact `PROTOCOL_MISMATCH`, new task prepares, original archive rollback and removal preserve facts | [result](published-upgrade/result.json), [commands](published-upgrade/commands.json) |
| Installation without source access | New distribution copy and bootstrap in `/tmp`; OS sandbox denied the entire checkout during installation | [result](source-denied-install/result.json), [stdout](source-denied-install/install.stdout) |
| Installed package real runtime without source access | 27 deterministic model requests, 2 real Python test cases, durable host-observed evidence and satisfied current requirement claim | [result](standalone-runtime/result.json), [runtime log](standalone-runtime/runtime.log), [test log](standalone-source-denied.log) |

Installed module identity is checked against the official DSH profile runtime;
Python loads from its isolated venv. Before loading the plugin, the runtime
asserts that reading the checkout fails with an OS permission error. This is
stronger than merely unsetting `PYTHONPATH`.

The installed DSH is `0.2.2-alpha.2`, Node `v26.8.1`, Python `3.13.3`, macOS arm64.
The local official source checkout remains at
`f3803c96974ad4a94195fb97e5c637e9c2a3afe3`. Exact source/package interface lines
and hashes are retained in [environment-and-interfaces.json](environment-and-interfaces.json).
Synchronous context/guard providers and awaited creation, pre-execute and
turn-stopping seams were verified against the installed packages and exercised.

The adapter's `output.schema` is the host presentation envelope. Full results
are checked against canonical schemas before interpretation; it is not a second
quality specification. Generated Skill content now includes canonical common
quality rules, and runtime versions are read from generated compatibility data.
Installation records pin each archive's exact core, enabling real v2 rollback
without interpreting or migrating old task facts. Atomic installation writes
flush file bytes and the containing directory.

The initial runtime log records `PROJECTION_STALE` during concurrent canonical
resource editing. After the protocol freeze and rebuild, all 22 tests passed;
no assertions were relaxed to suppress the stale-projection failure.
The metadata-only transaction fixture and actual published-archive upgrade are
reported separately. The published archive is only an explicit test input;
this delivery neither reuses it as a new artifact nor republishes it.

Live-model behavior was not tested. Runtime integration uses actual DSH
services with a deterministic model fixture and actual local Python execution.
Remote/browser execution and unclassified third-party write tools are outside
the verified provider/guard scope. No unresolved DSH test failures remain.
