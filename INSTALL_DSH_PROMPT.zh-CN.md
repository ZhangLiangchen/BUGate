# 安装本地 DSH 2.1.0-dev.1 构建

[English](INSTALL_DSH_PROMPT.md) | 简体中文

此 Prompt 使用本次本地交付，不指向新的公开 release。六个文件位于 `dist/scope-convergence/`，可整体复制到任何产物目录，产品运行不需要 BUGate 源码。Python ABI 与平台以 tarball 内 `python/inventory.json` 为准。

```text
请安装此产物目录中的 BUGate DeepSeek Harness 原生构建：
/Users/zhangliangchen/PycharmProjects/BUGate/dist/scope-convergence
若用户提供已复制的产物目录，使用该目录；不要下载 release 或替换版本。

必须使用同次构建的全部六个文件：
- bugate-deepseek-harness-2.1.0-dev.1.tgz
- bugate_core-2.1.0a1-py3-none-any.whl
- install_dsh.py
- build-manifest.json
- source-manifest.json
- SHA256SUMS

逐项验证 SHA256SUMS（macOS 使用 shasum -a 256 -c SHA256SUMS，Linux 使用
sha256sum -c SHA256SUMS）。预期 bundle SHA-256：
4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2
缺文件或摘要不符立即停止。ARTIFACT_DIR 设为已验证产物目录绝对路径。
检查包内 python/inventory.json，核对匹配的 Python、DSH 0.2.2-alpha.2
和 Node >=24.9.0。产品不需要源码 checkout 或 editable install。

使用用户明确选定的外部测试项目、DSH profile、DSH home，以及新建或 BUGate 已拥有
的 bootstrap venv。缺少这些目标身份时，只询问缺少的目标，不从历史任务猜测项目。
保留无关 profile、插件、配置和任务事实。

执行 python3 ARTIFACT_DIR/install_dsh.py --package
ARTIFACT_DIR/bugate-deepseek-harness-2.1.0-dev.1.tgz --venv BOOTSTRAP_VENV
--project TEST_PROJECT --dsh-profile PROFILE --dsh DSH_EXECUTABLE
--dsh-home DSH_HOME，替换为明确路径并安全引用。
然后用 BOOTSTRAP_VENV/bin/bugate doctor integrations 带相同目标参数和 --json。
分别报告包/配置安装与实际 runtime hydration；确定性 fixture 结果不能称为 live-model 验证。

仅针对本轮明确的当前 Profile 初始化任务。DSH 捕获用户直接请求收据，根据收据和
Profile 声明的领域证据形成有限验收范围。每个范围版本保持 Profile 需求/风险分母；
分母变化须显式新 Profile 版本并 init 新任务，原任务、证据与失败保留。
范围外建议不扩展执行计划或阻塞完成。分别报告实际范围、执行闭环、功能结果、阻塞和建议。

失败时保留精确错误和有所有权的 pending 操作状态，不删除用户文件或改目标重试。
仅对本次明确的自有集成恢复使用 remove deepseek-harness --rollback。
不发布或覆盖 tag/release，不恢复退役宿主适配器或旧任务格式。
```

Python wheelhouse 离线安装；DSH 解析宿主 peer dependencies 时可能需要 registry。参见[安装与恢复](docs/DEEPSEEK_HARNESS.zh-CN.md)和[范围示例](docs/ACCEPTANCE_SCOPE.zh-CN.md)。
