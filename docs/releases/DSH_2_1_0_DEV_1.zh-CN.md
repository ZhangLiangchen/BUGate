# DSH 2.1.0-dev.1 — 验收范围与主流程收敛

[English](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/releases/DSH_2_1_0_DEV_1.md) | 简体中文

本预发布包含 BUGate 宿主无关 Python v2 核心、统一 CLI 和 DeepSeek Harness 原生插件。新增第一等验收范围契约，围绕用户有限目标形成充分证据闭环。既有公开 tag 和产物保持不变。

| 组件 | 版本 / 目标 |
|---|---|
| 原生包 | `@bugate/deepseek-harness 2.1.0-dev.1` |
| Python 分发 | `bugate-core 2.1.0a1` |
| Protocol | `2.1.0-dev.1`，精确内容摘要绑定 |
| DeepSeek Harness | `0.2.2-alpha.2`，源码 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3` |
| 运行环境 | Node `24.9.0+`、Python `3.11+`、POSIX provider |
| 本次离线 wheelhouse | CPython 3.13 / macOS arm64，安装前检查兼容性 |

## 实际行为

`ScopeContract` 将真实用户请求收据、目标、允许环境、主流程和必要分支关联到已有要求、业务 Oracle 与当前 inventory 计划。必需场景、有界辅助活动和不可执行建议分别处理，不默认以正常/边界/安全/性能完整矩阵扩大目标。用户明确要求的拒绝、边界或权限行为仍为必需项。

DSH 在受管执行前消费精确范围/计划/快照评估，在受支持恢复及子 Agent 路径中恢复当前绑定。范围外扩展不能进入可执行 inventory，而是记录为建议；建议及草稿不使必需执行输入失效，也不自动触发完成返工。读取、准备、清理和关联失败的有界诊断继续服从宿主权限并保持可用。

结果分别表达执行完整性、功能验收、阻塞、建议与历史失败。真实 Oracle 失败可以完成执行，但需求验证仍不满足。具名 R1 声明既不强制无关 R2 执行，也不能把整体任务误报完成。

锁定 Profile 在每个范围版本中固定需求/风险分母。同分母内用户明确变更产生新范围版本，使旧计划/证据适用性失效。分母变化必须显式建立新 Profile 版本，再通过 `bugate init` 新建任务；旧任务和失败保留。不提供同任务 Profile 迁移或选择性证据复用。请求收据证明用户选择，领域事实仍以 Profile 来源为依据。

## 下载与安装

完整使用固定 [dsh-v2.1.0-dev.1 预发布](https://github.com/ZhangLiangchen/BUGate/releases/tag/dsh-v2.1.0-dev.1)的六份产物：

- [原生 bundle](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/bugate-deepseek-harness-2.1.0-dev.1.tgz)
- [Python core wheel](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/bugate_core-2.1.0a1-py3-none-any.whl)
- [Bootstrap 安装器](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/install_dsh.py)
- [构建 manifest](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/build-manifest.json)
- [源码输入摘要 manifest](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/source-manifest.json)
- [SHA256SUMS](https://github.com/ZhangLiangchen/BUGate/releases/download/dsh-v2.1.0-dev.1/SHA256SUMS)

先验证完整校验和清单，再运行安装器。可直接使用[精确安装 Prompt](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/INSTALL_DSH_PROMPT.zh-CN.md)，完成下载、摘要及目标身份核对。产品无需源码 checkout 或 editable install。

公开 bundle SHA-256：`0e9f87a6e9f3b21744e4b9f865ac7e698110a3c556fe43a70b9f34c37f74ea0c`。

```sh
shasum -a 256 -c SHA256SUMS
python3 install_dsh.py \
  --package ./bugate-deepseek-harness-2.1.0-dev.1.tgz \
  --venv /absolute/path/bugate-bootstrap \
  --project /absolute/path/test-repo \
  --dsh-profile bugate \
  --dsh /absolute/path/to/dsh \
  --dsh-home /absolute/path/dsh-home
/absolute/path/bugate-bootstrap/bin/bugate doctor integrations \
  --project /absolute/path/test-repo --dsh-profile bugate \
  --dsh /absolute/path/to/dsh --dsh-home /absolute/path/dsh-home --json
```

core wheel 为纯 Python，但本次离线依赖 wheelhouse 特定于 CPython 3.13/macOS arm64；其他 ABI 需要另行构建并验证的 bundle。DSH 解析宿主 peer dependencies 可能访问 registry。不支持 Windows。Setup/doctor 证明安装，Agent hydration 另行观测。升级/rollback 保留自有归档和任务历史；协议摘要变化不静默迁移已有任务。不恢复退役宿主适配器或旧格式。

发布准备仅修正两份包内 protocol README 的版本说明，没有新增业务规则。此前尚未公开的组件版本仍为 `2.1.0-dev.1` / `2.1.0a1`，但 protocol、evaluator 与 package 摘要相对先前本地 2.1 交付发生变化。此前本地任务不能无条件复用旧绑定：请使用既有显式新任务初始化流程，保留原任务与历史。见[摘要变化记录](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/reports/release-2.1-dev.1/package/digest-transition.json)。

统一 CLI 新增 `scope record/show/check/request`、`suggestion record/list` 与 `execution authorize`，沿用工件、证据与声明流程。`scope request` 是可信操作者/宿主输入入口，不是模型可调用的授权工具。参见[使用指南](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/DEEPSEEK_HARNESS.zh-CN.md)及[范围示例](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/ACCEPTANCE_SCOPE.zh-CN.md)。

## 实测结果与边界

**前次实施交付归档**（`scope-convergence`，bundle SHA-256 `4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`）记录 123 项核心测试、4 项分发测试和 15 项安装边界测试。真实 DSH 完整回归 24 项通过发生在最后具名声明修复前，修复后又运行 2 项受影响路径并通过。该旧包通过 20 条源码禁读安装生命周期命令、9 条真实旧 v2 归档升级/rollback 命令，以及包含正常通过和真实失败流程的已安装真实 DSH 测试。这些是历史观测，不是针对新发布字节重新运行的结果。

这些实现记录固定在提交 `bb6153efc7a334172c36f069c2c160504df8f375`：[交付与证明边界](https://github.com/ZhangLiangchen/BUGate/blob/bb6153efc7a334172c36f069c2c160504df8f375/docs/reports/scope-convergence/DELIVERY.zh-CN.md)、[正式验收](https://github.com/ZhangLiangchen/BUGate/blob/bb6153efc7a334172c36f069c2c160504df8f375/docs/reports/scope-convergence/acceptance.json)、[真实已安装 runtime 记录](https://github.com/ZhangLiangchen/BUGate/blob/bb6153efc7a334172c36f069c2c160504df8f375/docs/reports/scope-convergence/dsh/installed-runtime-result.json)。封存报告中“尚未发布”的文字描述当时的本地交付节点。

**本次发布包**（`0e9f87a6…4ea0c`）来自提交 `062b03f2895a7ddabe7ee848d5b8f047a1826da5`，57 项构建输入匹配该提交；与已验证实现相比仅两份 protocol README 变化。源码与 bundle 边界检查通过。这个精确新包通过 20 条源码禁读安装生命周期命令，包含干净/重复安装、doctor、受控版本元数据升级/rollback、卸载及恢复；也通过使用确定性模型 fixture 驱动正常成功和真实失败两条工作流的已安装真实 DSH runtime 测试。此前旧 v2 归档的 9 条升级命令未针对本次仅文档修正的重建包重跑。见[包验证结果](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/reports/release-2.1-dev.1/package/REPORT.zh-CN.md)及[精确已安装 runtime 结果](https://github.com/ZhangLiangchen/BUGate/blob/dsh-v2.1.0-dev.1/docs/reports/release-2.1-dev.1/runtime/installed-runtime-result.json)。数量描述实际观测，助手候选例子不是固定验收门槛。

已验证 runtime 使用确定性模型 fixture 和真实 DSH 工具/进程；未运行 live-model 验证。受管准入不推断任意 shell 命令的全部业务语义，也不认证控制全部本地文件的同权限恶意操作者。结构与哈希不证明业务事实正确。本结论仅覆盖约定 BUGate 实现及验证范围，不宣称产品整体安全或模型效率收益。发布提交的托管 CI 和下载产物逐字节核对由发布者独立记录，不从本地测试推断。
