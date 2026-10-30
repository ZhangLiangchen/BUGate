# BUGate native DSH plugin

English | [简体中文](README.zh-CN.md)

`@bugate/deepseek-harness` 2.2.0-dev.1 is the Cordis integration for the shared
TypeScript BUGate core and unified Node CLI. DSH is the only product host. The
plugin consumes `@bugate/core` canonical serialization directly and executes the
installed `@bugate/cli` in the configured DSH subprocess/filesystem world for
validation, hashing, storage and process collection. It has no Python fallback.

The package's ordered `dsh.bundle.patch` first consumes the exact official
`@deepseek-ai/dsh-experimental-agent-team-profile` patch, then registers BUGate.
The official layer mounts Team service/tools and disables direct delegation tools.
DSH owns members, tasks, messaging, execution and lifecycle; BUGate does not own a
Team scheduler. Supported interface pins are DSH/Team 0.2.2-alpha.2 and Cordis
4.0.6-alpha.1. Node requires >=24.9.0. Experimental Team APIs have no stability promise.

## Configuration

```yaml
- id: bugate
  config:
    projectRoot: /absolute/external-test-project
    mode: managed
    nodeExecutable: node
    maxRework: 2
    maxReworkMs: 120000
    executionCommands:
      required-tests:
        argv: [node, tests/runner.mjs, logs/results.xml]
        caseIds: [case-from-current-inventory]
        report: logs/results.xml
        purpose: test
```

Select `taskPath` when a project has multiple tasks. `coreEntrypoint` is an optional
host-owned absolute installed CLI path, not a model argument. Both the Node
executable and CLI installation must map into the filesystem/process world.
Opaque target keys are never interpreted as paths; file witnesses compare actual
provider-visible bytes and directories. Unsupported mappings fail explicitly.

`context.text` and `tools.guard` synchronously consume completed prepared state.
Preparation, request admission and final review settlement run at actual awaited
Agent lifecycle seams. Managed write/execution tickets recheck scope, plan and
current design acceptance. Ordinary reads and source analysis remain available.
Managed `bash` is rejected because arbitrary shell intent is not inferred; use
configured `bugate_run_test` commands with a declared required case or bounded
auxiliary activity. Advisory mode does not make a blocking tool policy.

## Native tools and review provenance

The tool set includes prepare/status/validate, scope authoring, artifact/evidence
imports, suggestions, assessment and configured execution. The unified CLI and
native plugin use the same core rules and JSON envelopes.

`bugate_publish_artifact({artifact: ...})` accepts inline output from the actual
model tool call. It captures exact live official Team membership, Agent/Session,
parent, fresh/fork and inherited prefix, original tool arguments and received peer
messages. `bugate_record_artifact({input: ...})` imports a file; importing a
Lead-written file does not establish the caller as its author. Labels, model names
and JSON identity fields never become host identity.

Publication is two-stage. The artifact and immutable `HostObservation` are durable
before tool return, with `status: published`. This does not authorize review. At
an awaited preparation/stop/resume seam, the plugin flushes the actual final DSH
`tool/result` and publishes immutable `HostSettlement`. Only a completed matching
settlement can support evaluation; cancellation after publication, timeout, failed
output and missing settlement remain incomplete. Cold recovery reads the durable
Session and core facts rather than trusting a Lead-local map.

A locked `Profile.team_review_policy` selects only necessary source-backed
perspectives, context policy, cross-check requirement and bounded review rounds.
It imposes no universal role/model/case matrix. Reviews bind exact artifact and
upstream dependency snapshots, scope/plan, Profile/protocol and evaluator bytes.
Changes invalidate the applicable review. `fresh_required` means no inherited
Lead event prefix; it is not a guarantee of cognitive independence or blindness
to the shared checkout. Same-model members can satisfy observable execution
separation; different models alone prove nothing.

Cross-checks reference exact prior opinions and actual received Team messages.
Raw findings, counterevidence, limitations and dispositions persist. An evidenced
independent disposition can resolve a finding without requiring all members to
agree; the original dissent is retained. Majority voting and Team task completion
do not replace business Oracles. Message acceptance is not recipient processing
or review persistence.

Team is process-local with shared cwd. It provides no worktree isolation;
writeScopes are advisory, ownership is not automatically released, and message
retry/deduplication is not guaranteed. Publication/CAS/locks protect cooperating
writers, not a same-permission filesystem/process attacker. No model-quality or
efficiency improvement is asserted from deterministic runtime tests.

See [adapter usage](../README.md), [protocol authority](../../../protocol/v2/README.md)
and [runtime verification](../tests/README.md).

The native observation channel is signed by an Ed25519 private key held only in the plugin process. The configured filesystem provider persists only its public-key pin. Signatures bind task/workspace, operation and the complete context digest; the CLI verifies an existing pin and exposes no authority-creation command. Ordinary model JSON and a self-supplied environment token cannot mint provenance. Historical public keys/signatures remain for recovery verification; same-permission direct key/file rewriting remains outside the assurance.

Fresh Team sessions still receive a Lead-authored delegation prompt. Its exact
bytes remain in the durable DSH session and the Lead's actual spawn tool call.
The native record proves which live member emitted an output and its observable
context policy; it does not prove textual originality. A Lead can place text in
a prompt that a member repeats, so identity alone cannot establish cognitive
independence or detect every form of ghostwriting.
