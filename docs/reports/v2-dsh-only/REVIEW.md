# Integration review / 集成复核

These findings concern BUGate or its acceptance tooling, not a real SUT.
本轮问题属于 BUGate 及其验收工具，不是实际被测产品缺陷。

| ID | Evidence / 已确认事实 | Resolution and verification / 修复与验证 |
| --- | --- | --- |
| V2-R1 | Grouping testability layers by proposition alone allows a case for Oracle A to borrow the layer selected for Oracle B under the same proposition. / 仅按命题聚合测试层会掩盖不同 Oracle 的层错配。 | Bind the exact proposition/Oracle/layer triple; `test_oracle_specific_layer_cannot_borrow_another_oracle_layer` passes in the 96-test core run. |
| V2-R2 | The new published-upgrade test expected `BINDING_DIGEST_MISMATCH`; the actual stable Workspace contract raises `PROTOCOL_MISMATCH`. Detected by source review before running that test. / 新升级测试期待的错误码与实际契约不符。 | Correct the exact expected code, retaining exit 2 and byte-identical old lock assertions; actual upgrade evidence linked in final report. |
| V2-R3 | Distribution validation skipped every `.dist-info/` member. An in-memory wheel with old console aliases in `entry_points.txt` incorrectly passed. / 分发检查跳过 metadata，旧命令别名能漏过检查。 | Require the sole `bugate = bugate.cli:main` console entry, validate metadata namespace, and add negative controls. Final distribution test log is authoritative. |

No assertions are weakened to match incorrect product behavior. V2-R2 corrects
a test-infrastructure expectation against the existing explicit error contract.
Hash/source records establish consistency, not hostile-worker authentication.

未通过放宽断言适配错误行为；V2-R2 根据现有明确错误契约修正测试工具自身的预期。
哈希与来源记录用于一致性追踪，不等于对恶意同权限进程的认证。

## Final independent read-only audit / 最终独立只读复核

The independent reviewer compared all 49 IDs and requirement/expected texts against
the original external contract: no missing ID or requirement drift; only AC-33
is retired. Every evidence path exists. Final installation and runtime results
refer to the same new bundle SHA; all five checksum entries and 52 build-input
hashes match. The exact old entry paths are absent. The corrected metadata
negative control passes. COMPLETE is supported within the recorded macOS/local
provider engineering boundary; live-model remains NOT_RUN.

独立评审逐条比对原合同：49 个 ID 与要求/预期文本无漂移，仅 AC-33 退役。证据路径
全部存在，安装与 runtime 均绑定同一个新包 SHA，5 项校验和和52项源码输入均一致。
旧入口不存在，metadata 漏检已修。记录范围内的 COMPLETE 结论有证据支持。
