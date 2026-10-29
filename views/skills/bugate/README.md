[简体中文](README.zh-CN.md)

# Shared Skill projection source

`SKILL.md` is a concise bilingual entry, not a competing methodology source.
The DSH build appends the canonical MethodSpec JSON resources and records both
protocol and generated Skill digests in `resources/projection.json`. Runtime
loading verifies these digests against the bundled protocol and installed core.
Regenerate with `node adapters/deepseek-harness/plugin/build.mjs` whenever the
canonical protocol or this entry changes. Do not edit generated resources.
