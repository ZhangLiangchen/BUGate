# 协议投影

[English](README.md) | 简体中文

`skills/bugate/SKILL.md` 是 DSH 入口模板。原生构建追加 canonical MethodSpec JSON，并将协议/Skill 摘要写入 `resources/projection.json`。Bundle 与 Python wheel 携带同一套 `protocol/v2/` 字节，打包拒绝过期投影。

投影指向 canonical Schema/MethodSpec，不建立第二套质量标准。运行时上下文来自核心 PreparedProtocolContext，校验后原样注入。不再存在其他宿主投影。
