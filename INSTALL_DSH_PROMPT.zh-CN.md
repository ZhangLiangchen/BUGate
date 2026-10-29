# 安装 DSH v2.0.0-dev.2

[English](INSTALL_DSH_PROMPT.md) | 简体中文

此 Prompt 安装 [dsh-v2.0.0-dev.2 预发布](https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.0.0-dev.2)附带的精确产物，无需 BUGate 源码仓库。内置 wheelhouse 必须与所记录的 Python/平台 ABI 匹配。

```text
从这个精确 GitHub release 安装 BUGate v2 DeepSeek Harness 原生插件：
https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.0.0-dev.2

新建本地下载目录，从以下地址前缀下载全部六个文件：
https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.2/
使用 HTTPS、跟随重定向并检查 HTTP 错误：
- bugate-deepseek-harness-2.0.0-dev.2.tgz
- bugate_core-2.0.0a2-py3-none-any.whl
- install_dsh.py
- build-manifest.json
- source-manifest.json
- SHA256SUMS

六个文件保持同目录，在该目录校验 SHA256SUMS 的每一项
（macOS 用 shasum -a 256 -c SHA256SUMS，Linux 用 sha256sum -c SHA256SUMS）。
预期 bundle SHA-256 为：
1d51534de8c059a5828b9a551ec94f41f24df97069b65994dcc026d672ac71d0。
文件缺失、下载失败或摘要不符时停止，不替换其他 release。将 ARTIFACT_DIR 设为
这个已校验目录的绝对路径。读取 bundle 的 python/inventory.json，检查目标平台/ABI、
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
