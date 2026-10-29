# BUGate Protocol v2

English | [简体中文](README.zh-CN.md)

Protocol `2.0.0`, Python `bugate-core 2.0.0a1`, and the legacy repository release
are independent versions. This directory is the canonical package resource;
wheel construction includes these exact bytes. `manifest.yaml` and
`reason_codes.yaml` use JSON syntax, which is valid YAML 1.2.

The nine core schemas plus MethodSpec, ExecutionRecord and CommandResult use
Draft 2020-12, validated by pinned `jsonschema==4.23.0`. Artifact content is
strictly typed by `artifact_type`. MethodSpec defines quality, not a workflow.
The Python evaluator returns deterministic, semantic-review and runtime
dimensions separately. The adapter must inject `render.content` unchanged.

Task initialization accepts a JSON Profile. Paths in Profile sources are relative
to the explicit project; all artifact/evidence/claim paths stay inside the task.
Create source digests from actual bytes. Canonical object digests are SHA-256
of `kind + '/v1'`, a NUL separator, then UTF-8 JSON with sorted keys and no
whitespace; omit top-level `digest` and `request_digest`. File digests are plain
SHA-256 of bytes. Floats and integers outside JavaScript's safe range are rejected.
`protocol.lock.json` contains `digest` fields; there is no second `sha256` spelling.

The snapshot excludes Claim, AssessmentRequest, AssessmentResult and prepared
context outputs, but request digests include the fixed Claim body. Task facts,
referenced source bytes, exact Binding and evaluator source bytes are included.
Read-time status recomputes claims; a stored `satisfied` result never authorizes
work. Atomic writes and non-blocking task locks cover cooperating local writers;
unmanaged external writes may cause conflicts and are not magically atomic.

`evidence record` preserves imported/human/model provenance and rejects
`host_observed`. `execution run` executes real argv and records the process,
outputs, fresh JUnit report, requested cases, outcomes and input digests. Runtime
proof is local managed observation, not resistance to a same-permission actor
rewriting both evidence and collector receipts. A hash proves byte consistency,
not truth. A failed product test may satisfy execution/delivery scope while its
requirement remains unverified. Zero, entirely skipped and xfail-only runs do not
establish verification.

Existing legacy `check(...) -> GateReport` logic is reused for a
`legacy_precode` artifact through a subprocess with explicit neutral configuration;
stdout returns its structured failures/warnings. Legacy scripts keep their
original CLI and stdlib-only dependency contract.

Examples are generated and schema-checked by `tests/v2/test_core.py`; fixtures and
execution logs live in temporary external SUT directories. Ordinary operations
have no Memory Bus dependency.
