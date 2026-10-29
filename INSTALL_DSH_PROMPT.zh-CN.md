# BUGate DSH 一键安装 Prompt

[English](INSTALL_DSH_PROMPT.md)

复制下面全文到外部测试仓库的 Agent 会话。

```text
请在当前外部测试仓库安装 BUGate DSH v2.0.0-dev.1，并执行安装验证。这是执行授权，不是只给建议。

固定 release：https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.0.0-dev.1
资产根：https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.0.0-dev.1

1. 先确认当前目录是我要治理的外部测试仓库，不是 BUGate core；保护未提交改动和现有 Claude/Codex、DSH 配置。检测 macOS arm64、CPython 3.13、Node >=24.9.0 和 npm。此包不适用于其他平台/ABI；缺少必要运行时则准确说明，不偷换包。
2. 在当前项目新建或复用已确认属于本次安装的 `.bugate-dsh-2.0.0-dev.1/` 状态目录。所有运行时、下载文件、venv 和 DSH home 都隔离于此，记录该目录所有权；遇到无所有权的既有目录则停下说明，不覆盖。不提交这些生成文件。
3. 从资产根下载以下五文件：bugate-deepseek-harness-2.0.0-dev.1.tgz、bugate_core-2.0.0a1-py3-none-any.whl、install_dsh.py、build-manifest.json、SHA256SUMS。校验 SHA256SUMS 全部四条；另外确认 tgz SHA-256 为 `0a4f7ddbcb0a035f603823f82da14e399ffa6bd236afeed3879625e8f626baab`，安装器为 `5680d650f4e72e705e19f3e3618076999341e58f84eefe94a6aceff5b7f3bdd2`。校验前不得执行下载代码。
4. 用 npm --prefix 在状态目录的 host/ 下安装精确版本 @deepseek-ai/dsh@0.2.2-alpha.2 和 pnpm@11.28.5；将其 node_modules/.bin 临时加入本次进程 PATH，不升级全局 DSH。
5. 使用 python3.13 执行已校验的 install_dsh.py，参数 --package 为下载的tgz，--python 为已检测Python3.13绝对路径，--venv 为状态目录/bootstrap，--project 为当前项目绝对路径，--dsh-profile bugate，--dsh 为隔离host的dsh绝对路径，--dsh-home 为状态目录/dsh-home。
6. 使用 bootstrap/bin/bugate 执行 doctor integrations --project <当前项目> --dsh-profile bugate --dsh <同一dsh> --dsh-home <同一home> --json。重复执行相同安装验证幂等。保存安装/doctor JSON、版本、路径和校验结果。
7. 不自动编造SUT Profile、需求、Oracle或测试结果，不连接旧HyperTest服务。普通v2不依赖Memory服务。没有业务Profile时安装可以完成，真实任务初始化另需有来源的Profile；doctor的hydration=unobserved必须原样报告，不能声称E2E已验证。
8. 最后报告安装完成与否、精确CLI/DSH/profile路径、doctor结果和后续在同一DSH home/profile启动的命令。任务已有多个BUGate目录时必须显式选择task-path，不猜最近任务。若失败，保留journal和产物，给出具体错误与恢复入口，不盲目重装或删除事实。
```
