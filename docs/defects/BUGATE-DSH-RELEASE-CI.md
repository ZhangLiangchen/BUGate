# DSH release CI findings / DSH 发布 CI 发现

## CI-01: missing-dependency negative control / 缺依赖反例

The first Linux run (40821826420, protocol-dsh) failed the AC-15 negative control.
`python -S` hides site-packages, including an installed BUGate wheel; the test
therefore received Python's module-not-found output instead of the intended
BUGate `DEPENDENCY_MISSING` envelope. The previous source-loaded environment had
kept BUGate reachable through PYTHONPATH. This is a test-infrastructure defect,
not evidence of a SUT failure or a reason to relax the assertion.

The test now copies only the actual imported BUGate package into an isolated
probe directory, explicitly supplies that directory as PYTHONPATH, and leaves
third-party dependencies unavailable under `-S`. The exact exit/envelope/code
assertions remain. Targeted source-loaded and installed-wheel tests both pass;
full remote CI remains the release authority.

首次 Linux run 40821826420 的 AC-15 反例失败：`python -S` 同时隐藏安装在
site-packages 中的 BUGate，自测实际触达 Python 模块加载器，没有触达预期的
BUGate 缺依赖边界。原本从源码 PYTHONPATH 加载的环境没有暴露此问题。
这是测试基础设施问题，不是 SUT 缺陷。

修复把实际导入的 BUGate 包复制到隔离 probe 目录，通过显式 PYTHONPATH 保留
它的可导入性，同时用 `-S` 隐藏第三方依赖。原退出码/envelope/reason code
断言不变。源码和已安装 wheel 两种定向测试均通过，最终仍以远端 CI 为发布门。
