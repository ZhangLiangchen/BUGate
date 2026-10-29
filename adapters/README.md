# Native adapter

English | [简体中文](README.zh-CN.md)

DeepSeek Harness is the sole product host. Its [manifest](deepseek-harness/manifest.yaml)
declares current capabilities; [conformance](CONFORMANCE.md) preserves the HC IDs.
The [native package](deepseek-harness/plugin/) calls the Python core without
reimplementing quality rules. It uses host execution/providers and lifecycle;
no BUGate agent loop or generic dispatcher exists.

Installation and supported boundaries: [guide](../docs/DEEPSEEK_HARNESS.md).
Other adapter directories and planned capabilities have been removed.
