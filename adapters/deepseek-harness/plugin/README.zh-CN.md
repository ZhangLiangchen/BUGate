[English](README.md)

# 原生 bundle 配置

执行 `node build.mjs` 编译 TypeScript，并从仓库规范协议生成协议与 Skill 资源。
分发使用 `packaging/build_dsh.py` 生成的预构建 bundle，内含 Python wheels。
`dsh plugin add` 读取 `cordis.patch.yml`，配置行 id 为 `bugate`，插件名称为
`@bugate/deepseek-harness`。

| 配置 | 默认值 | 含义 |
|---|---|---|
| `pythonExecutable` | `python3` | 由 provider 解析；绝对宿主安装路径须映射。 |
| `projectRoot` | 会话发现 | 可选的 filesystem provider 路径，限制发现范围。 |
| `taskPath` | 唯一发现任务 | 明确的 provider 任务路径；歧义报错。 |
| `mode` | `managed` | `advisory` 不拦截写入，评估含义不变。 |
| `maxContextBytes` | 8000 | 核心渲染预算。 |
| `timeoutMs` | 30000 | 每次核心调用期限。 |
| `maxOutputBytes` | 1048576 | stdout/stderr 合计字节上限。 |
| `graceMs` | 1000 | 终止进程宽限期。 |
| `maxRework` | 2 | 完成声明返工次数上限。 |
| `maxReworkMs` | 120000 | 完成声明返工总时限。 |
| `executionCommands` | `{}` | 宿主配置的命令注册表，模型只提交命令 id。 |

命令字段为 `argv: string[]`、`caseIds: string[]`、`report`（任务内本次新 JUnit 路径）、
`purpose: 'test' | 'exploration'` 和可选 `timeoutSeconds`（默认 20）。命令及其参数路径
必须属于同一配置 execution world；适配器不猜测任意参数含义，也不跨运行环境隐式访问。
bridge timeout 应大于命令 timeout 与证据采集开销之和。

工具包括 `bugate_prepare`、`bugate_status`、`bugate_validate`、`bugate_assess`、
`bugate_record_artifact`、`bugate_record_evidence`、`bugate_run_test`。
`bugate_assess({claim,submit:true})` 启用有界完成声明返工。草稿写入 `.bugate/drafts/`；
`bugate_record_artifact({input,expected_digest?})` 委托核心计算摘要、校验和原子 CAS。
更新需传旧工件摘要。普通 evidence 登记不接受宿主观测来源升级。

设计满足前允许 `artifacts/`、`claims/`、`evidence-input/`、精确 `.bugate/drafts/`
子树的分析写入。`implementation/`、`tests/` 写入需要新鲜且满足的设计声明。
受管模式拒绝其他 `.bugate/` 控制文件与任意 shell 写入；配置 exploration 命令可做合法探测。
读取始终可用。

协议、Schema、版本或投影失配会清除旧满意状态。reload 释放工具、提示提供者及存活
子进程。冷恢复核对会话固定的完整绑定，不接受静默替换。需全局加载适配器才能覆盖
独立 preset 和子 Agent；不假定父 Agent 局部 guard 自动继承。没有 BUGate 自有 loop
或强制长期记忆服务。
