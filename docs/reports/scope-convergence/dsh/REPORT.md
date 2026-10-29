# DSH runtime verification

English | [简体中文](REPORT.zh-CN.md)

Runtime verification passed for the finite-scope integration and existing host
boundaries below. This conclusion covers only the agreed acceptance scope: the
new scope/plan/evidence/completion mechanisms and their actual DSH integration,
using temporary generic fixtures. It does not establish general product safety,
all risk categories, or live-model analysis quality.

The final installed package was built from source commit
`c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60`:

- Native package: `bugate-deepseek-harness-2.1.0-dev.1.tgz`.
- SHA256: `4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`.
- Python core: `2.1.0a1`; protocol: `2.1.0-dev.1`.
- Real DSH: `0.2.2-alpha.2`; inspected official source commit
  `f3803c96974ad4a94195fb97e5c637e9c2a3afe3` in the existing local checkout,
  [verified by local Git/interface probes](source-interface-probes.json). Actual
  execution loads the published npm packages; their source/type hashes are in
  [environment evidence](environment-and-interfaces.json).
- Model boundary: deterministic fixture; live model **not run**.

| Evidence set | Actual result | Source boundary |
|---|---|---|
| Full real-host regression | 24 passed, 0 failed/skipped; exit 0; 307.986 s. | [Source manifest](runtime-source-manifest.json), [result](runtime-result.json), [log](runtime-final.log). |
| After the final named-requirement review fix | 2 affected tests passed, 0 failed/skipped; exit 0; 204.564 s. | [Final source manifest](runtime-source-manifest-after-named-scope-fix.json), [result](runtime-after-named-scope-fix-result.json), [log](runtime-after-named-scope-fix.log). |
| Final installed package, checkout reads denied | 1 integration test containing both complete workflows passed; exit 0; 176.166 s. | [Exact package/runtime result](installed-runtime-result.json), [test log](installed-runtime-final.log), [child log](installed-runtime-child.log). |

These rows are separate evidence sets. The full 24-test run preceded the final
small named-requirement evaluator fix; affected authoring/convergence paths were
then rerun against the final source, followed by both workflows in the exact
final installed package. No old package result is relabeled as the final build.

The installed run used official DSH service registration, AgentLoop, model/tool
pipeline, configured local filesystem/subprocess providers, JSONL persistence
and official profile module resolution. It asserted that host and installed
plugin resolve the same DSH LLM module. The Python module loaded from an isolated
installed venv. Before loading the plugin, macOS `sandbox-exec` denied an actual
read of the source checkout's `pyproject.toml` with EPERM/EACCES. The same OS deny
rule covered the entire checkout for the complete run. Exact executable paths,
command arguments, environment variables and module identities are in the
installed result. Raw DSH session logs are retained in the finite
[session-trace manifest](installed-session-traces/index.json).

| Installed workflow | Model requests | Required case results | Functional verification | Execution completion | Suggestions | Diagnosis |
|---|---:|---|---|---|---:|---|
| Requested positive and negative integer branches | 47 | 2 passed | satisfied | satisfied | 4, nonblocking | Not needed. |
| Same branches, deliberately faulty generated SUT | 51 | 1 passed, 1 failed | unsatisfied | satisfied | 4, nonblocking | 2 bounded auxiliary captures; third denied before execution. |

In each workflow the model attempted to register an unrelated scenario. The
core preserved the accepted inventory and recorded only an advisory description.
A configured command for the extra case was denied before process start; its
marker file did not exist. The model then executed the actual required cases.
Evidence was read back from disk before accepting the tool result. More
suggestions were added after execution completion: the original claim bytes and
authorization snapshot stayed unchanged, both immutable assessments remained
satisfied, and no completion rework was injected. See the
[passing proof](installed-passing/suggestion-reassessment-proof.json) and
[failure proof](installed-failure/suggestion-reassessment-proof.json), with raw
JUnit/stdout/evidence and hashes alongside them.

Formal requirement coverage is mapped to F-01/F-02/F-04/F-05/F-07 by these installed
workflows. The full source runtime regression additionally covers F-03/F-06:
50 real turns and actual compaction, cold resume, spawn/fork/independent presets,
subagent authorization provenance, parallel task isolation, task leases,
admission races, PTC lineage, durable evidence, cancellation/disposal, timeout,
path/mapping boundaries and source-only reads before design. F-08 is supported by
this bilingual report and exact evidence/package reconciliation. Historical
SC labels are not an acceptance count gate.

The first runtime candidate exposed a real advisory/immutable-assessment conflict.
It was fixed and rerun without weakening the original completion assertions.
[Runtime findings](RUNTIME_FINDINGS.md) retain the failure and its independent CLI
reproduction. The final source and installed runs both prove the fix.

The enforcement boundary is known managed write tools and host-configured
commands with core-checked scope/plan/environment/activity identities. The
adapter does not infer all business semantics from arbitrary shell text. Other
host tools remain subject to host policy. Local hashes and receipts do not
authenticate against a principal controlling the same filesystem. Semantic
necessity reviews remain source-backed judgments, separate from deterministic
reference checks and real execution observations. No outstanding runtime blocker
was observed in this agreed verification scope.
