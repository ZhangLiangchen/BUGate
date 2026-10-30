# 开发 Prompt

[English](INIT.md) | 简体中文

```text
在本仓开发 BUGate v2 的共享 TypeScript 核心、统一 Node CLI、Cordis 原生插件和官方 DSH Agent Team 接入。
阅读 AGENTS.md、当前 Protocol、MethodSpec、Schema、源码与 CI。
先保存本次变更的需求、业务不变量、Oracle 和验收映射。保留已有 worktree 与其他写者改动。
Core 保持 SUT-neutral，仅用动态通用临时 fixture。完成实现及适用 Node 核心/分发/真实 DSH 测试，
在源码不可读且 Python 不可执行的独立环境验证精确安装包，
重建投影及安装产物，同步中英文文档，最后依据真实证据报告结果与缺口。
不恢复退役宿主集成，未经授权不发布 release。
```

将产品安装到测试项目使用 [INSTALL_DSH_PROMPT](INSTALL_DSH_PROMPT.zh-CN.md)。
