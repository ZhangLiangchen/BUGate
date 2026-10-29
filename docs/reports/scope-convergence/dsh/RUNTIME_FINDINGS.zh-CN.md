# 真实运行时集成发现

[English](RUNTIME_FINDINGS.md) | 简体中文

第一轮完整候选运行的 24 个真实 DSH 测试中有 23 个通过，并发现完成评估的持久化缺陷。
原始日志保留在 [runtime-intermediate-1.log](runtime-intermediate-1.log)，不作为最终安装包验收。

确定性模型完成必需场景，获得满足的 `execution_complete` 评估，再记录两条额外建议，
重评字节完全相同的声明。重评返回
`CONCURRENT_CONFLICT: Identity already has different immutable content`。
[suggestion-immutable-conflict.json](suggestion-immutable-conflict.json) 保存对保留临时任务的独立 CLI 复现。

授权快照正确排除了建议，但不可变评估输出包含动态建议 id，而评估请求身份未覆盖建议。
新增建议因此在同一不可变键产生不同结果字节。修复设计把建议摘要纳入评估请求身份，
但不纳入声明授权快照、不变成阻塞项。测试判据保留：原完成声明字节不变、结论仍满足、
不注入返工。最终验证另外记录在运行时报告中。

实际 DSH 源码还表明，初始子 Agent 委派使用 user 来源消息。适配器检查持久化
`SessionHeader.origin === 'subagent'`，不把模型委派变成真人授权收据；fork 继承的历史
不重复捕获，插件完成返工使用 `bugate-rework` 来源。真实 spawn 反例在第一轮完整运行通过。

辅助命令允许省略 JUnit 报告。声明过的只读需求探测可在设计尚未完成时执行，记录
`runtime_probe`、零验收用例、无测试报告，不被升级为业务验证。实际 provider 回归已通过。
