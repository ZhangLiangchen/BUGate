[English](README.md)

# DSH 自动化验收

先构建 `../plugin`。在临时 Node 环境安装官方 DSH 0.2.2-alpha.2、TypeScript 5.9.3
和 Ajv 8.17.1，通过 `plugin/node_modules` 解析；测试宿主也支持
`BUGATE_DSH_MODULES` 指向模块目录。`BUGATE_TEST_PYTHON` 指定已安装核心依赖的 Python。

```sh
node adapters/deepseek-harness/plugin/build.mjs
BUGATE_TEST_PYTHON=/absolute/venv/bin/python node --test adapters/deepseek-harness/tests/bridge.test.mjs adapters/deepseek-harness/tests/runtime.test.mjs
```

`host.mjs` 加载官方 Cordis/AgentLoop、filesystem、subprocess、提示、工具流水线、
会话、Skill 和 LLM 服务，只有模型适配器使用确定性 fixture。其他用例加载真实 JSONL
持久化、compaction-basic/token-meter、preset/loader、spawn/fork 和 Node PTC 服务。

覆盖 50 次真实模型请求的规范上下文、真实 compactNow 及压缩后恢复、事实变化后的
旧满意状态失效、preset 变更后的冷恢复精确绑定、多子 Agent 与多工作区、PTC 嵌套
执行谱系、完成返工边界、reload、非 BUGate 工作区、失配资源和丢失绑定。确定性 race
暂停真实模型准备阶段，在兄弟 Agent 写入后验证模型请求数为零；下一轮重新准备当前事实。

authoring 工作流从无工件、无实现开始，通过真实模型工具调用读取需求、写草稿、登记
由核心计算的摘要、建立有来源的业务 Oracle 和评审，再评估设计、生成实现。更新引用与
评审并重新评估后执行真实通用 Python 测试。工具结果返回前必须已有持久化执行证据。

Bridge 测试执行真实 local-provider 子进程，覆盖错误响应、退出码/operation 失配、
缺少 Python、输出上限、超时、取消，以及具有 opaque key 的映射 provider。同绑定但不同工件/源字节以及新增/缺失路径的真实
provider 子类必须在模型请求前被拒绝。
仓库不提交秘密或真实公司 SUT fixture。

干净包安装完成后，单独运行必需的安装闭环：

```sh
BUGATE_INSTALLED_PLUGIN=/absolute/profile/node_modules/@bugate/deepseek-harness \
BUGATE_INSTALLED_PYTHON=/absolute/installed/runtime/venv/bin/python \
BUGATE_DSH_MODULES=/absolute/runtime/node_modules \
node --test adapters/deepseek-harness/tests/standalone-package.test.mjs
```

runner 使用官方 profile runtime resolution，断言插件与宿主共享同一个 DSH LLM 模块。
该测试只复制通用测试驱动到新临时目录，清除 PYTHONPATH/NODE_PATH，加载实际安装包
lib/resources 与独立核心，并执行真实 authoring 和命令采集。缺失安装参数明确失败，
不会跳过。输出保留的 result JSON 和日志路径，包含安装路径、兼容元数据、请求次数和
执行证据。这里证明确定性运行时集成，不冒充 live-model 分析质量。

macOS 的独立包测试可设置 `BUGATE_DENY_SOURCE_ROOT=/absolute/BUGate`。
测试子进程在操作系统 sandbox 内运行，禁止读取源码仓库；加载安装插件前，
先断言读取源码确实返回权限错误，再验证真实 runtime 可独立运行。

`tests/integration_install/accept_published_upgrade.py` 显式接收旧版与候选归档，
并核对旧版公开 SHA256。它实际安装 dev.1、升级 dev.2、拒绝旧任务绑定且保持字节不变、
准备新 v2 任务，再回滚到保留的 dev.1 原始归档。该测试与 `accept_package.py` 中
只修改版本元数据的事务/崩溃恢复 fixture 分开报告。不支持 v0.x 导入或自动任务迁移。
