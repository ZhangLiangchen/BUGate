# DSH implementation review / DSH 实施审查

Date / 日期: 2026-10-29. Scope / 范围: BUGate framework infrastructure,
not a product SUT / 框架基础设施，并非产品 SUT 缺陷。

This record tracks concrete findings during implementation. Static findings are
not runtime acceptance results. Closure requires the final implementation and
executed regression evidence in the delivery report.

本文记录实现过程中的具体审查发现。静态发现不等于运行验收结果；关闭条件是最终代码
及交付报告中实际运行的回归证据。本文保留发现时的行为，不代表最终代码仍然存在问题。

| ID | Observed implementation / 观察到的实现 | Required correction / 修正条件 |
|---|---|---|
| DSH-R01 | The snapshot excluded `.bugate` while using the constructor's cached Binding. / 快照排除 `.bugate`，同时使用构造时缓存的 Binding。 | Re-read and verify lock bytes at commit; reject concurrent drift. / 提交时复核锁文件字节，拒绝并发漂移。 |
| DSH-R02 | Array Schema applied `uniqueItems` to argv. / argv Schema 使用了 `uniqueItems`。 | Preserve valid repeated arguments and test them. / 允许合法重复参数并测试。 |
| DSH-R03 | Packaging initially copied the legacy Skill as the DSH projection. / 打包初版把 legacy Skill 当作 DSH 投影。 | Generate the DSH Skill from canonical v2 methods and check its digest. / 从 canonical v2 方法生成投影并校验摘要。 |
| DSH-R04 | An initial partial install could leave pending state without an installed record; remove returned absent. / 首次半安装可只留下 pending，无 installed 记录时 remove 返回 absent。 | Reconcile and roll back the original operation while preserving unrelated state. / 核对并回滚原操作，保留无关状态。 |
| DSH-R05 | Task scope was read from editable task fields without comparing the locked Profile. / 可编辑 task 字段作为覆盖分母，未与锁定 Profile 对照。 | Detect requirement/risk/delivery-scope drift and test attempted scope reduction. / 检测需求、风险和交付范围漂移，并测试缩小分母反例。 |
| DSH-R06 | Implementation-only task delivery did not transitively apply design checks. / 仅 implementation 的交付范围未递归应用设计检查。 | Apply the full pre-code dependency closure; keep genuinely inapplicable dimensions separate. / 应用完整 pre-code 依赖，区分真正不适用的维度。 |
| DSH-R07 | The real legacy CI loop failed because its development inventory included an absolute `node_modules` symlink; `.gitignore` is not consumed by that fallback. / 实际 legacy CI 因开发清单纳入绝对 `node_modules` 符号链接失败；该 fallback 不读取 `.gitignore`。 | Exclude development dependencies from the fallback only; retain rejection of other unsafe links and the Git-based release inventory. / 只在开发 fallback 排除依赖，保留其他危险链接拒绝和基于 Git 的发布清单。 |
| DSH-R08 | Independent real execution passed, then a new optional SUT configuration changed behavior; a fresh claim still returned `satisfied`. Existing-file hashes missed additions. / 独立真实执行通过后，新增可选 SUT 配置改变行为；新快照声明仍返回 `satisfied`，仅校验原文件哈希漏掉新增输入。 | Bind execution applicability to the complete relevant input path set and bytes; reject added or removed inputs. / 绑定完整相关输入路径集合与字节，拒绝新增或删除输入后复用旧执行。 |
| DSH-R09 | Independent reviewer executed only C1, but a report naming C1/C2 allowed complete task delivery. / 独立审查仅执行 C1，报告自报 C1/C2 即使完整任务交付通过。 | Compare delivery coverage with actual case observations, not report labels. / 以实际用例观察核对交付覆盖，不能仅信报告字段。 |
| DSH-R10 | Missing Artifact relation and diagnosis supporting-evidence IDs were accepted. / 工件关系与诊断支持证据指向不存在 ID 仍通过。 | Validate all typed references and preserve unsupported diagnoses as hypotheses. / 检查所有类型化引用，无支持的诊断不能确认。 |
| DSH-R11 | A JUnit root declared zero tests and one failure while containing one passing case; the parser accepted a pass. / JUnit 根声明零用例和一失败，但子项为一个通过用例，解析器接受通过。 | Reject conflicting aggregate and per-case statistics. / 拒绝聚合统计与逐用例统计冲突。 |
| DSH-R12 | A retained historical failed run blocked a fresh successful run and unrelated design claims after a valid fix. / 合法修复后保留的历史失败执行阻塞新成功执行及无关设计声明。 | Keep historical failure evidence, but evaluate execution applicability within the claim's explicit evidence scope. / 保留历史失败，按声明显式证据范围评估当前执行适用性。 |
| DSH-R13 | Swapping P1/O2 and P2/O1 in testability was accepted when all IDs existed. / 可测性中交换 P1/O2、P2/O1，所有 ID 存在就被接受。 | Check Oracle-to-proposition ownership, not only ID existence. / 检查 Oracle 所属命题关系，不能只确认 ID 存在。 |
| DSH-R14 | A child completed a managed write after the parent's awaited preparation; the real model received only `CONTEXT_INVALIDATED`, without canonical constraints, and the turn completed. / 子 Agent 在父 Agent 准备完成后写入，真实模型收到只有失效错误、缺少 canonical 约束的上下文，turn 仍完成。 | Rehydrate at the actual awaited request seam and reject invalidated admission before inference; verify the race against the real Agent loop. / 在真实请求的可等待接缝重新加载，并在推理前拒绝已失效请求；用真实 Agent loop 验证并发反例。 |
| DSH-R15 | The real filesystem provider returned a different binding, or the same binding with a different business artifact, from the subprocess world; the Agent still sent a satisfactory context to its model. / 真实 filesystem provider 与 subprocess 所见绑定不同，或绑定相同但业务工件不同，Agent 仍向模型发送满意上下文。 | Verify execution-world agreement for the governed binding and applicable facts before admitting context or managed operations. / 在接受上下文和受管操作前，核对受治理绑定与适用事实的执行环境一致性。 |
| DSH-R16 | The new filesystem witness included the provider's transient atomic session-pin directory; simultaneous child creation failed when that directory disappeared. / 新文件清单纳入 provider 写 session pin 的瞬时原子目录，并发创建子 Agent 时目录消失导致失败。 | Isolate runtime bookkeeping from governed facts and exclude that bounded, non-authoritative namespace consistently; retain exact artifact/source checks. / 将运行记账隔离到无事实权威的专用目录并一致排除，保留工件和来源精确核对。 |

These findings were sent to the owning implementers before integration. Their
test identifiers and final disposition are recorded in the acceptance evidence.

以上发现已在集成前发给对应实现者；测试编号与最终处理结果由验收证据记录。
