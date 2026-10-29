# Acceptance mapping / 验收映射

48 applicable HC/AC entries PASS; AC-33 retired by explicit scope change, never reported PASS. Three new no-legacy checks PASS.
48 项适用 HC/AC 通过；AC-33 因用户明确范围变更退役，不计为通过。新增 3 项无历史依赖验收通过。

Exact selectors and complete evidence links: [acceptance.json](acceptance.json). 原始要求与逐项测试名称完整保存在机器映射中。

| ID | Original requirement / 原要求 | Result | Current-run evidence / 本轮证据 |
| --- | --- | --- | --- |
| HC-01 | setup 幂等 | PASS | [dsh/clean-install/result.json](dsh/clean-install/result.json); [dsh/published-upgrade/result.json](dsh/published-upgrade/result.json) |
| HC-02 | canonical Protocol 可发现 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/clean-install/result.json](dsh/clean-install/result.json) |
| HC-03 | 工作区/任务身份一致解析 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-04 | 精确绑定版本与摘要保持一致 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/published-upgrade/result.json](dsh/published-upgrade/result.json) |
| HC-05 | PreparedContext 注入前通过 Schema 校验 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-06 | 相关 Agent 推理前已有适用协议上下文 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-07 | 长任务不依赖一次性 Skill 记忆 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-08 | 提供恢复生命周期时，压缩/重启后重新 hydrate | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-09 | 受支持子 Agent 独立取得同一 Binding | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-10 | 版本/摘要失配可见失败 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/published-upgrade/result.json](dsh/published-upgrade/result.json) |
| HC-11 | 过期投影可检测 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/clean-install/result.json](dsh/clean-install/result.json) |
| HC-12 | 显式 assessment 可调用 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-13 | Adapter 不改变核心方法与评估语义 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| HC-14 | rollback 只删除本插件拥有的集成状态 | PASS | [dsh/clean-install/result.json](dsh/clean-install/result.json); [dsh/published-upgrade/result.json](dsh/published-upgrade/result.json) |
| HC-15 | 历史 Context Runtime 不覆盖协议与当前工作区事实 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-01 | 通用临时 SUT 完整闭环 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
| AC-02 | 缺少 Oracle 或业务语义未定 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-03 | 只有代码，无运行 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-04 | 退出码零但零用例/全部跳过 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-05 | 真实产品失败 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-06 | 伪造 passed/verified/exit_code 字段 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-07 | 导入历史或他人日志 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-08 | 同路径内容修改/证据截断/删除 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-09 | 协议/Profile/规则/脚本变化 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/published-upgrade/result.json](dsh/published-upgrade/result.json) |
| AC-10 | 评估写入后的重复 prepare | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-11 | 并发修改发生在评估与提交之间 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-12 | 同 task id、不同工作区 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-13 | 路径逃逸、符号链接、越权写 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-14 | 工件含指令、模板变量或长文本 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-15 | Python 缺失、异常、无效 JSON、输出过大 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-16 | 超时或用户取消 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-17 | evidence 写盘失败、半写、重复事件 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
| AC-18 | 无记忆服务 | PASS | [evidence/core-tests.log](evidence/core-tests.log) |
| AC-19 | 实际 bundle 安装到新 profile | PASS | [dsh/clean-install/result.json](dsh/clean-install/result.json); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
| AC-20 | 脱离 BUGate checkout | PASS | [dsh/clean-install/result.json](dsh/clean-install/result.json); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
| AC-21 | 用锁定 DSH 类型编译 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
| AC-22 | 真正模型请求前的上下文 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-23 | 至少 50 次确定性 turn/等价刷新驱动及压缩 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-24 | restart/resume 与 preset 变化 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-25 | spawn/fork/独立 preset 子 Agent | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-26 | nested/PTC tool call | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-27 | async pre-execute + sync guard | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-28 | 缺业务理解时补足材料 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-29 | turn-stopping 缺口返工 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-30 | fs/subprocess execution world 不匹配 | PASS | [evidence/core-tests.log](evidence/core-tests.log); [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-31 | 插件重载/卸载/升级/重复安装 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/clean-install/result.json](dsh/clean-install/result.json); [dsh/published-upgrade/result.json](dsh/published-upgrade/result.json) |
| AC-32 | 非 BUGate 工作区 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log) |
| AC-33 | legacy Claude/Codex 与脚本回归 | RETIRED | 因本次范围变更退役：用户明确撤销旧 Claude/Codex、脚本、installer/updater 兼容与回归要求。 |
| AC-34 | CLI 与插件同一输入 | PASS | [dsh/runtime-final.log](dsh/runtime-final.log); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
| NL-01 | No legacy source runtime or old host entry | PASS | [evidence/distribution-final.json](evidence/distribution-final.json); [evidence/distribution-tests-final.log](evidence/distribution-tests-final.log) |
| NL-02 | No legacy in new wheel and bundle | PASS | [evidence/distribution-final.json](evidence/distribution-final.json); [evidence/distribution-tests-final.log](evidence/distribution-tests-final.log) |
| NL-03 | Native v2 runtime independent of deleted scripts; old inputs rejected | PASS | [evidence/distribution-final.json](evidence/distribution-final.json); [evidence/distribution-tests-final.log](evidence/distribution-tests-final.log); [evidence/core-tests.log](evidence/core-tests.log); [dsh/source-denied-install/result.json](dsh/source-denied-install/result.json); [dsh/standalone-runtime/result.json](dsh/standalone-runtime/result.json) |
