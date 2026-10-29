[English](README.md)

# 共享 Skill 投影入口

`SKILL.md` 是简短的中英文入口，不是第二套方法论真相源。DSH build 将规范 MethodSpec
JSON 附加到生成资源，并在 `resources/projection.json` 记录协议与 Skill 摘要。
运行时验证投影、bundle 协议和已安装核心的一致性。规范协议或此入口变化后执行
`node adapters/deepseek-harness/plugin/build.mjs` 重新生成；不要直接编辑生成资源。
