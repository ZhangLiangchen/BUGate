# 安装本次 v2 + DSH 构建

[English](INSTALL_DSH_PROMPT.md) | 简体中文

此 Prompt 使用本次新构建的本地产物，不下载旧 release。产物位于 BUGate checkout 的 `dist/v2-dsh-only/`，可整体复制到匹配所记录 Python/平台 ABI 的机器。

```text
从以下本地构建产物安装 BUGate v2 DeepSeek Harness 原生插件：
ARTIFACT_DIR=/Users/zhangliangchen/PycharmProjects/BUGate/dist/v2-dsh-only
使用同目录 bugate-deepseek-harness-2.0.0-dev.2.tgz、install_dsh.py、build-manifest.json、
source-manifest.json 和 SHA256SUMS，不得替换成历史发布资产。
先核对 SHA256SUMS 每一项，读取 bundle 的 python/inventory.json，检查目标平台/ABI、
DSH 0.2.2-alpha.2、Node >=24.9.0 和匹配的 Python runtime。

使用用户明确选定的外部测试项目、DSH profile、DSH home，以及新建或 BUGate-owned
bootstrap venv。如果当前会话没有这些目标，先询问，不从旧任务推断，不把 BUGate core
当作 SUT。保留无关 profile/插件和任务事实。

安全引用实际绝对路径，执行：
python3 ARTIFACT_DIR/install_dsh.py --package
ARTIFACT_DIR/bugate-deepseek-harness-2.0.0-dev.2.tgz --venv BOOTSTRAP_VENV
--project TEST_PROJECT --dsh-profile PROFILE --dsh DSH_EXECUTABLE --dsh-home DSH_HOME
然后以 BOOTSTRAP_VENV/bin/bugate doctor integrations 加相同目标参数和 --json 核对。
分开报告安装成功与真实会话 hydration；只有用户给出 v2 Profile 和范围才初始化任务，
不转换 v0.x 或旧任务格式。

失败时保留具体错误和 owned pending-operation 状态，不删除用户文件，不更换目标盲重试。
说明 remove deepseek-harness 和 --rollback 的受管恢复用法，不发布 release。
```

Bundle 包含离线 Python wheelhouse；DSH 解析 peer dependencies 可能需要 registry 网络。精确命令和已测运行边界见[安装指南](docs/DEEPSEEK_HARNESS.zh-CN.md)。
