# BUGate Views / Host Projections

BUGate Protocol is the normative source of truth. Files installed into an Agent
Host are projections.

Examples:

- `CLAUDE.md` BUGate bootstrap fragment;
- `AGENTS.md` BUGate bootstrap fragment;
- Claude/Codex `SKILL.md`;
- Host plugin metadata;
- hook/lifecycle configuration;
- Pi package-facing instruction assets.

A projection must be reproducible from canonical BUGate inputs and carry enough
version/digest metadata for `bugate doctor <host>` to detect staleness.

Host projections must never introduce MethodSpec or Assessment semantics that
do not exist upstream.

Target flow:

```text
Protocol + View Template
        |
        v
Projection Compiler
        |
        +--> Claude Code projection
        +--> Codex projection
        +--> Pi projection
        +--> DeepSeek Harness projection
```

The intended CLI surface is:

```text
bugate setup <host>
bugate setup select --host ...
bugate doctor <host>
bugate doctor integrations
bugate protocol prepare --task ... --json
```
