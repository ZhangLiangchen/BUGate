# 绑定测试任务操作流程

[English](SOP.md) | 简体中文

1. 将原生包安装到明确的外部项目与 DSH profile，运行 doctor，见[安装指南](../DEEPSEEK_HARNESS.zh-CN.md)。
2. 读取需求，按 canonical Schema 建立有来源的 Profile，再用 `bugate init` 初始化任务；不发明资源标识或复制过期绑定。
3. 通过 `bugate protocol prepare` / `bugate_prepare` 取得当前精确绑定、MethodSpec 约束和可补足缺口。
4. 编写 JSON 草稿，再以 `artifact record` / `bugate_record_artifact` 登记。核心验证 Schema 并计算摘要；更新必须提供当前预期摘要，摘要不代表语义批准。
5. 建立业务理解、可测性、Oracle/用例映射和必要语义评审，在受管实现前评估当前 `test_design_complete` 声明。Profile 要求的可选建模使用原生工件。
6. 通过 `bugate_run_test` 执行配置命令，探索材料保留真实来源；等待证据持久化，保留产品失败并区分基础设施问题。
7. 按 Profile 生成诊断、执行和知识工件；提交限定范围声明，运行 assess 并报告真实结论。在宿主预算内修复可处理缺口，阻塞或取消不能静默标为完成。

不以任意 shell 写入修改已登记权威事实，不假设变更后历史评估仍适用。恢复校验精确 Protocol/Profile/评估器绑定，失配必须显式处理，不自动转换。受管实现被阻断时，正常读取和分析仍可继续。
