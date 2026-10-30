# Development prompt

English | [简体中文](INIT.zh-CN.md)

```text
Develop BUGate v2 with the shared TypeScript core, unified Node CLI and native
Cordis/official DSH Agent Team integration. Read AGENTS.md and the current protocol,
MethodSpec, Schema, source and CI. First save requirements, business invariants,
Oracles and acceptance mapping for the change. Preserve existing worktrees and
other writers' changes. Keep the core SUT-neutral; use only generated temporary
fixtures. Implement the change, run applicable core, distribution and real DSH
tests with Node, rebuild projections and installable assets, verify the exact
installed package without source access or Python, update both documentation
languages, then report actual results and remaining gaps. Do not restore retired
host integrations or publish a release without authorization.
```

To install the product into a test project, use [INSTALL_DSH_PROMPT](INSTALL_DSH_PROMPT.md).
