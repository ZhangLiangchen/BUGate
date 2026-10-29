# Publication bundle: installed DSH runtime

English | [简体中文](REPORT.zh-CN.md)

PASS: the rebuilt publication bundle completed the real DSH standalone integration
test with two deterministic workflows. This is fresh evidence for the new bundle,
not a relabeling of the previously tested implementation bundle.

| Identity / result | Observed value |
|---|---|
| Source inputs | `062b03f2895a7ddabe7ee848d5b8f047a1826da5` |
| Native bundle SHA-256 | `0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c` |
| Components | Protocol/native `2.1.0-dev.1`; Python `2.1.0a1`; published DSH packages `0.2.2-alpha.2` |
| Test command | `node --test adapters/deepseek-harness/tests/standalone-package.test.mjs` |
| Result | Exit 0; 1 integration test passed; 0 failed/skipped; 171.831 seconds |
| Runtime | macOS arm64, actual installed plugin and isolated installed Python venv |
| Source isolation | OS sandbox denied checkout reads; the `pyproject.toml` read probe rejected with EPERM/EACCES before plugin load |
| Model | Deterministic fixture driving real DSH Agent/tool/provider/session services; no live model |

The package came from the fresh [clean installation](../package/clean-install/).
The exact environment, driver hashes and command timing are in [command.json](command.json).
The profile resolver loaded the same DSH LLM module for the plugin and test host;
Python module identity was verified inside its installed venv. The compatibility
metadata's DSH commit is a source reference, not a claim that the npm packages
were built byte-for-byte from a locally inspected checkout.

| Workflow | Required observed cases | Requirement verdict | Execution completion | Suggestions | Diagnosis |
|---|---|---|---|---|---|
| Expected behavior | 2 passed | satisfied | satisfied | 4, non-blocking | Not needed |
| Intentional product defect | 1 passed, 1 failed | unsatisfied | satisfied | 4, non-blocking | 2 observed; third denied before process start |

The generated temporary SUT implements a bounded integer function. The finite
request explicitly includes positive and negative branches. Each workflow
attempts to append an unrelated security scenario to the active plan and execute
it. The plan update is rejected, an advisory description is retained, the active
inventory stays unchanged, and the forbidden command's marker file is absent.
The accepted cases execute with exact scope/plan/environment bindings and durable
raw evidence before their tool result returns.

After completion, two more suggestions are recorded. The same claim bytes and
authorization snapshot remain applicable; distinct immutable assessment records
remain satisfied without completion rework. In the defective SUT, the failed
Oracle remains a functional failure. Two permitted auxiliary diagnostics do not
become acceptance cases; the next attempt receives `DIAGNOSTIC_LIMIT_REACHED`.

The [result](installed-runtime-result.json) includes separate coverage, functional
result, blocking codes and suggestion IDs. Raw outputs are in
[the Node test log](installed-runtime.log), [the child runtime log](installed-runtime-child.log),
[session traces](session-traces/index.json), and the copied generic
[passing](generated-project/) / [failure](generated-failure-project/) task workspaces.
[evidence-sha256.json](evidence-sha256.json) records their byte hashes.

This conclusion covers only the described publication-bundle runtime checks.
Existing full core/runtime regression results remain attributed to their original
commits and bundles in the frozen scope-convergence report; this run does not
claim they were all repeated. Existing historical test IDs and mapping fields in
the unchanged driver are traceability labels, not a new mandatory scenario matrix.
No live-model effectiveness, remote-provider behavior or unexecuted platform is
established by these results.
