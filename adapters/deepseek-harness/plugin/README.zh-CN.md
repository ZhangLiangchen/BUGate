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

命令字段为 `argv: string[]`、`caseIds: string[]`、`report`（任务内本次新 JUnit 路径，验收测试必填，辅助探测可省略）、
`purpose: 'test' | 'auxiliary' | 'diagnostic' | 'exploration'` 和可选 `timeoutSeconds`（默认 20）。命令及其参数路径
必须属于同一配置 execution world；适配器不猜测任意参数含义，也不跨运行环境隐式访问。
bridge timeout 应大于命令 timeout 与证据采集开销之和。

工具包括 `bugate_prepare`、`bugate_status`、`bugate_validate`、`bugate_assess`、
`bugate_scope`、`bugate_check_scope`、`bugate_record_scope`、`bugate_record_suggestion`、
`bugate_record_artifact`、`bugate_record_evidence`、`bugate_run_test`。
`bugate_assess({claim,submit:true})` 启用有界完成声明返工。草稿写入 `.bugate/drafts/`；
`bugate_record_artifact({input,expected_digest?})` 委托核心计算摘要、校验和原子 CAS。
更新需传旧工件摘要。普通 evidence 登记不接受宿主观测来源升级。

设计满足前允许 `artifacts/`、`claims/`、`evidence-input/`、精确 `.bugate/drafts/`
子树的分析写入。`implementation/`、`tests/` 写入需要新鲜且满足的设计声明。
受管模式拒绝其他 `.bugate/` 控制文件与任意 shell 写入；关联契约活动的配置 exploration 命令可做合法探测。
读取始终可用。

协议、Schema、版本或投影失配会清除旧满意状态。reload 释放工具、提示提供者及存活
子进程。冷恢复核对会话固定的完整绑定，不接受静默替换。需全局加载适配器才能覆盖
独立 preset 和子 Agent；不假定父 Agent 局部 guard 自动继承。没有 BUGate 自有 loop
或强制长期记忆服务。

范围契约与收敛工具：`bugate_scope` 查看当前契约、真实用户请求收据和建议；
`bugate_check_scope` 校验范围和计划；`bugate_record_scope({input, expected_digest?})`
登记契约草稿或有用户来源的新版本。请求捕获命令不向模型暴露为工具，Agent 自述
“用户同意”不构成授权；子 Agent 委派和插件返工不被捕获为真人请求。若改变锁定 Profile 的要求/风险分母，
需显式新 Profile 版本并初始化新任务；原任务及其失败保留，不自动重绑定。

命令配置支持 `purpose: test | auxiliary | diagnostic | exploration`，辅助活动
必须提供契约中声明的 `activityId` 且 `caseIds` 为空；验收测试只能列当前计划用例。
可设置 `environmentId`；省略时核心仅接受唯一允许环境，多环境需显式选择。
每次执行在 await 准备接缝获取同步 guard 凭据，核心启动命令前再次核对期望
契约/计划摘要。诊断按关联失败和有限次数受控，不无限扩展成专项。

建议草稿放 `.bugate/drafts/`，包含 `id`、`description`、`rationale`、
`source_refs`，通过 `bugate_record_suggestion({input})` 登记；不接受命令或执行
步骤。草稿目录和正式建议都不进入授权快照，因此不会使已经完整的声明失效。
范围外建议不触发返工；返工只修复范围内证据缺失或不一致。真实功能失败时，
`execution_complete` 可以通过，`requirement_verified` 必须保留不通过。
