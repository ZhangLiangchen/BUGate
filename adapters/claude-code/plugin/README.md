# Claude Code Plugin Projection

Planned BUGate 2.0 host-native integration.

This directory will contain Claude Code-specific lifecycle glue only. It will
consume `PreparedProtocolContext` and inject it without changing BUGate
methodology semantics.

Expected installed projections include:

- minimal BUGate bootstrap in `CLAUDE.md`;
- generated BUGate Skill;
- plugin/hook lifecycle glue;
- diagnostics metadata.

Implementation must satisfy [Host Adapter Conformance](../../CONFORMANCE.md).
