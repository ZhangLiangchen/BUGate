# Codex Plugin Projection

Planned BUGate 2.0 host-native integration.

This directory will contain Codex-specific lifecycle glue only. It will
consume `PreparedProtocolContext`, validate the exact ProtocolBinding, and
inject the final rendered content without reinterpreting MethodSpec or
Assessment semantics.

Expected installed projections include:

- minimal BUGate bootstrap in `AGENTS.md`;
- generated BUGate Skill;
- plugin/hook lifecycle glue;
- diagnostics metadata.

Implementation must satisfy [Host Adapter Conformance](../../CONFORMANCE.md).
