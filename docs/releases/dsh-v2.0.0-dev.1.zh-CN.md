# BUGate DSH v2.0.0-dev.1 预发布

[English](dsh-v2.0.0-dev.1.md)

这是独立的 DeepSeek Harness 原生分发线。既有 Claude/Codex 稳定版仍为 v0.4.5，
不修改其 tag 或资产。

- 原生包：`@bugate/deepseek-harness@2.0.0-dev.1`。
- 共享核心：`bugate-core==2.0.0a1`；Protocol：`2.0.0`。
- 宿主：DSH `0.2.2-alpha.2`，Node `>=24.9.0`。
- 公开离线 wheelhouse：**仅 macOS arm64、CPython 3.13**。
- 包含编译插件、规范 Schema/方法/Skill、Python wheel、离线 Python 依赖、独立安装器和校验和。

实现已通过全部49项HC/AC。真实DSH测试由确定性模型fixture驱动，包含独立安装包运行；
不代表live-model效果。详见[交付报告](../reports/DEEPSEEK_HARNESS_DELIVERY_2026-10-29.zh-CN.md)。

## 安装

把[安装Prompt](../../INSTALL_DSH_PROMPT.zh-CN.md)复制到外部测试仓库中的Agent会话。
它校验release字节，使用隔离的宿主、bootstrap与profile目录，保留已有任务事实。
安装成功不等于创建了有来源的业务Profile，也不证明真实任务已hydrate。

## 发布契约

不可变annotated tag为`dsh-v2.0.0-dev.1`，与原生包版本一致。Main与tag CI均须通过，
包含原legacy链和新增Linux DSH job。Linux CI构建并验证Linux wheelhouse用于审计；
本次公开发布已完成验收的macOS wheelhouse。

公开资产为`bugate-deepseek-harness-2.0.0-dev.1.tgz`、
`bugate_core-2.0.0a1-py3-none-any.whl`、`install_dsh.py`、`build-manifest.json`、
`SHA256SUMS`及中英文安装Prompt。前五个安装资产来自同一次已验收构建；Prompt为版本化文档。
发布后重新下载公开资产，校验全部安装文件，并执行新的独立安装和真实runtime验收。

运行与来源保证边界仍以交付报告为准，不声称Windows、live-model或任意远程执行环境可用。
