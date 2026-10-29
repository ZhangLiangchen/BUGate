# Runtime integration findings

English | [简体中文](RUNTIME_FINDINGS.zh-CN.md)

The first full candidate run passed 23 of 24 actual DSH tests and exposed a
completion-persistence defect. The retained run is
[runtime-intermediate-1.log](runtime-intermediate-1.log); it is not final package
acceptance.

The deterministic model completed the required cases, obtained a satisfied
`execution_complete` assessment, then recorded two additional advisory
suggestions and reassessed the identical claim. The reassessment returned
`CONCURRENT_CONFLICT: Identity already has different immutable content`.
[suggestion-immutable-conflict.json](suggestion-immutable-conflict.json) records
an independent CLI reproduction against the retained temporary task.

The authorization snapshot correctly excluded suggestions. However, the
immutable assessment output included current suggestion ids while the assessment
request identity did not cover them. New suggestions therefore produced different
result bytes at the same immutable assessment key. The corrective design binds
advisory content in the assessment request identity without putting it in the
claim's authorization snapshot or turning it into a blocker. The existing test
assertions remain: the original completion claim stays byte-identical, its
verdict stays satisfied, and no rework is injected. Final verification is indexed
separately in the runtime report.

Actual DSH source inspection also found that an initial subagent delegation is
represented as a user-source message. The adapter checks persisted
`SessionHeader.origin === 'subagent'` and does not turn such a model-authored
prompt into a user authorization receipt. Fork-inherited event prefixes are not
recaptured. Plugin-generated completion rework uses `bugate-rework` provenance,
not `user`. The actual spawn test passed in the first full run.

Auxiliary commands may omit JUnit reports. A declared read-only source probe
runs before design acceptance and records a `runtime_probe`, zero acceptance
cases and no test report. It is not upgraded into business verification. The
actual provider test passed in the first full run.
