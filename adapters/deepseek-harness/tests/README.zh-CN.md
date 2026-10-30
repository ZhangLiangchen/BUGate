# 原生 runtime 验证

[English](README.md) | 简体中文

测试使用真实锁定版本的 DSH/Cordis 服务、官方 Agent Team、实际工具、filesystem/
subprocess provider、持久化会话和临时中性命令；只有模型边界使用确定性 fixture。
代码树中存在测试不证明已经执行；迁移报告记录实际命令、版本、精确源码/包身份和输出。

```sh
npm run build
node --test adapters/deepseek-harness/tests/core-service.test.mjs \
  adapters/deepseek-harness/tests/runtime-node.test.mjs \
  adapters/deepseek-harness/tests/team.test.mjs
```

- `core-service.test.mjs`：实际 Node provider 调用、输出/envelope/退出错误、专用宿主
  观察通道、超时/取消/drain、opaque 文件系统 key 和不可用安装映射。
- `runtime-node.test.mjs`：保留适用基线断言，覆盖精确 context、压缩、恢复、任务隔离、
  guard、配置执行、PTC/子成员/preset 生命周期、并发、准入竞态和 provider 边界。
- `team.test.mjs`、`team-fixture.mjs`、`team-negative.mjs`：官方 Team 成员、任务 CAS、
  同伴消息及冷恢复、真实内联作者/评审来源、有来源必要视角、原始异议及有证据处置、
  当前工件绑定和两阶段发布/最终结算。反例包含 Lead 代写导入、换名自审、fork 策略、
  过时工件、跨任务重放和发布后取消。
- `workflow-node.mjs`：共用中性创作/执行 fixture。来源明确要求正数、零和负数结果，
  三者均有真实断言；范围外建议不可执行/不阻塞，真实产品失败与有界诊断保留。
- `standalone-node-runner.mjs` 和 `installed-profile.mjs`：精确独立安装包、官方 profile
  组合/包解析、实际后端 Team 和 BUGate 激活、成功/失败流程及 Team 控制。安装验收
  对完整 runner 子树施加 OS 源码禁读和进程 allowlist，并实际探测 Python 执行被拒。
  只改 PATH 不是隔离证据。

历史固定测试数量和角色矩阵不是发布门槛。fixture 证明 runtime 机制与确定性断言，
不证明 live-model 推理质量、缺陷发现率或效率。Live model 单独报告；未运行则明确
**NOT_RUN**。平台支持只根据实际测试报告。

历史 Python/runtime 测试及发布产物保留在 Git 历史；其原始源码/协议身份不是 TS/Team 证据。
