# 打包与安装验证

[English](REPORT.md) | 简体中文

本文支撑[来源核验矩阵](../ACCEPTANCE_MATRIX.zh-CN.md)中的 **F-07**（独立安装包与受支持安装生命周期）和 **F-08**（产物、文档一致及如实交付）。测试数量是选定检查的实际观测，不是验收门槛。历史 SC 标签不能授权执行或形成固定完成条件。

当前状态：**打包/构建/安装及已安装 runtime 通过**，对应源码提交 `c9d2b8213f1ce9e0c3ea462ddb7d621ea12bbf60` 和 bundle SHA-256 `4279c20cfb55a1f9f6f75f26a65b5bca02d826327cd8179b9f04d35bc47143c2`。57 份构建输入逐项匹配该提交的 Git blob。前两轮包继续仅作为中间证据保留。[已安装 runtime 结果](../dsh/installed-runtime-result.json)独立锁定同一 bundle SHA，并在源码禁读条件下通过。真实 DSH 加确定性模型测试执行了正常通过和真实失败两条流程；失败流程允许执行闭环，但需求验证保持不满足，建议不触发完成返工。安装不能替代这份独立 runtime 证明。

最终源码禁读干净生命周期完成 20 条选定命令检查，真实 `2.0.0-dev.2` → `2.1.0-dev.1` 归档升级/rollback 完成 9 条；两个结果文件均锁定上述最终 bundle 摘要。测试数量不是验收门槛。

## 最终产物与实际命令

| 产物/检查 | 证据 |
|---|---|
| 原生 bundle 与 Python wheel | [构建 manifest](build-manifest.json)、[SHA256SUMS](SHA256SUMS) |
| 源码提交对应 | [57 项源码摘要匹配](source-commit-match.json)、[源码 manifest](source-manifest.json) |
| 构建及有限 wheel/native 资源一致性 | `python3 packaging/build_dsh.py --out-dir dist/scope-convergence`，exit 0；[输出](build.stdout)、[边界结果](bundle-boundaries.json) |
| OS 源码禁读干净安装与生命周期 | `python3 tests/integration_install/accept_package.py ... --deny-source-root /Users/zhangliangchen/PycharmProjects/BUGate`，exit 0；[20 条精确命令](clean-install/commands.json)、[结果](clean-install/result.json)、[拒读探针](clean-install/00-source-denial-probe.stdout) |
| 旧公开 v2 包 → 当前产物再 rollback | `python3 tests/integration_install/accept_published_upgrade.py ...`，exit 0；[9 条精确命令](actual-upgrade/commands.json)、[结果](actual-upgrade/result.json) |
| 当前一键安装 Prompt | [English](../../../../INSTALL_DSH_PROMPT.md)、[中文](../../../../INSTALL_DSH_PROMPT.zh-CN.md)，均锁定最终 bundle 摘要。 |

六份独立交付文件位于 `dist/scope-convergence/`：`.tgz`、core `.whl`、`install_dsh.py`、`build-manifest.json`、`source-manifest.json` 和 `SHA256SUMS`。整组复制到 checkout 外后再次逐项验证摘要，见[校验记录](checksums.json)。wheelhouse 目标为 Python `3.13.3`、macOS arm64（`cpython-313-darwin`），见[平台记录](wheel-target.json)。本次交付本地产物，未发布公开 release。

## 已建立的打包检查

| 检查 | 实际结果 | 证据 |
|---|---|---|
| 分发回归 | 4 项通过，包含拒绝退役/凭据载荷及 wheel 元数据身份。 | [输出](distribution-tests.stdout) |
| 安装边界回归 | 15 项通过，覆盖所有权、路径、锁、超时、ABI 和包内 core/protocol 精确配对。 | [输出](install-boundaries.stdout) |
| 有限源码/资源清单 | 13 个 Python 模块、28 个 canonical 协议资源通过。 | [结果](source-boundaries.json) |
| 文档原始依据 | 从不可变变更前提交重读规则；候选例子不是原始授权。 | [复核](basis-review.json) |

独立安装器从当前归档读取锁定兼容记录，不再硬编码旧 Python 分发版本。构建逐字节比较 native 与 wheel 资源，检查当前模块版本和分发版本一致。安装包不包含源码 checkout 路径、退役运行时、测试 fixture、凭据或 editable install。

干净安装使用新的外部项目、DSH home/profile 和有所有权的 bootstrap venv。`--deny-source-root` 通过 macOS `sandbox-exec` 禁止产品进程读取 BUGate checkout，并先验证一次读取确实失败。原始结果存在测试 runner 局部变量遮蔽的元数据错误：`source_denied` 字段打印了后续拒绝命令的 argv。原始结果未改写；[源码拒读核对](source-denial-attestation.json)独立核验已执行的全部 20 条命令均含正确 sandbox 前缀，且拒读探针通过。修正只重命名 runner 变量，未改变产品或包字节，也未宣称重新执行安装。同一拒读策略覆盖安装、重复安装、doctor、升级、rollback、卸载及崩溃恢复，不会用其他执行环境模拟这份操作系统隔离证明。

## 保留的中间证据

| 产物 | 安装观测 | 被后续构建替代的原因 |
|---|---|---|
| `67884700a4f54e042a24958d4bd201509e24cb400e1b007ff3d6041a374e3946` | 源码禁读生命周期 20 条命令检查、真实旧 v2 包升级/rollback 9 条检查通过。 | 功能摘要需要纳入声明未选择但仍适用的失败。见[记录](intermediate-1/status.json)。 |
| `06e79269e9ae5cc02655710584a9d1a13990dcf365979c61482d1fd1ae738b4d` | 源码禁读生命周期 20 条命令检查、真实归档升级/rollback 9 条检查通过。 | 真实 runtime 发现新增建议后产生不可变评估身份冲突。见[记录](intermediate-2/status.json)。 |

各中间目录保留精确 manifest、校验和、命令日志、原始 stdout/stderr 和结果 JSON。这些是对应字节的真实通过记录，不证明修复后的最终包。真实 runtime 失败继续保存在适配器证据中，不重新归类为可选问题。

## 验证边界

目标为 DSH `0.2.2-alpha.2`、POSIX provider 及包内 `python/inventory.json` 所记 Python/平台 ABI。不支持 Windows；新增 Linux CI 配置不等于本机已观察 Linux 通过。Python 依赖离线安装，DSH peer dependency 解析可能访问 registry。未发布或覆盖任何公开 tag/release。

安装/doctor 证明配置与完整性，不证明 Agent hydration 或业务验收。只有实际已安装 runtime 记录能证明对应调用链。确定性模型 fixture 与 live-model 分开，不据此推断模型质量或效率收益。
