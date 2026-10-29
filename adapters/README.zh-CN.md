# 原生适配器

[English](README.md) | 简体中文

DeepSeek Harness 是唯一产品宿主。[Manifest](deepseek-harness/manifest.yaml) 声明当前能力，[符合性契约](CONFORMANCE.md) 保留 HC 编号。[原生包](deepseek-harness/plugin/) 调用 Python 核心，不重新实现质量规则；执行/provider 和生命周期由宿主管理，不提供 BUGate Agent loop 或通用调度器。

安装及支持边界见[指南](../docs/DEEPSEEK_HARNESS.zh-CN.md)。其他宿主占位目录与 planned 能力已删除。
