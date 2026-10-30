# Protocol projections

English | [简体中文](README.zh-CN.md)

`skills/bugate/SKILL.md` is the DSH entry template. The native build appends the
canonical MethodSpec JSON resources and records protocol/Skill digests in
`resources/projection.json`. The bundle contains the same exact `protocol/v2/`
bytes as the shared TypeScript core; the Node build rejects stale projections.

The projection points to canonical Schema/MethodSpec and does not create a second
quality standard. Runtime context comes from the core PreparedProtocolContext,
validated then injected unchanged. There are no other host projections.

Use inline `bugate_publish_artifact` for observed native authorship and explicit
current review target digests. File imports do not create host provenance. The
entry points to canonical `team_review` for official Team and settlement rules;
it does not define a second review policy.
