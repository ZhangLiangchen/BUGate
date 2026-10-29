# Protocol projections

English | [简体中文](README.zh-CN.md)

`skills/bugate/SKILL.md` is the DSH entry template. The native build appends the
canonical MethodSpec JSON resources and records protocol/Skill digests in
`resources/projection.json`. The bundle contains the same exact `protocol/v2/`
bytes as the Python wheel; packaging rejects stale projections.

The projection points to canonical Schema/MethodSpec and does not create a second
quality standard. Runtime context comes from the core PreparedProtocolContext,
validated then injected unchanged. There are no other host projections.
