# Mooncake 上游缺陷与提交条件核查

核查日期：2026-10-27。

核查对象：

- 上游仓库：`kvcache-ai/Mooncake`
- 上游快照：`b730bbae5f8b89ce79f609df9c66b59bea1b3f8b`
- 补丁基线：`5d0636f5d39f9de729a58ea036f98c395dd0b291`
- 补丁：[0001-ascend-cooperative-progress.patch](../patches/0001-ascend-cooperative-progress.patch)

上游状态会变化。提交前必须重新执行本文中的 overlap search、目标文件哈希核对和 `git apply --check`。

## 结论

本补丁修复了 TENT `AscendDirectTransport` 中可复现的饱和进展与资源生命周期缺陷。

截至核查快照：

1. 最新 `main` 的 4 个目标文件与补丁基线逐字节一致。
2. 补丁可以直接应用到最新目标文件。
3. 原版在扩展回归中有 7 项失败，修复版 Debug、Release、ASan/UBSan、TSan 均为 34/34 通过。
4. 没有检索到完全对应“第 101 个 Ascend Group 因尚未轮询而停滞”的公开 Issue 或 PR。
5. 上游已经记录更广泛的 progress/lifecycle 设计问题；其中
   [PR #2277](https://github.com/kvcache-ai/Mooncake/pull/2277)
   明确将 `ascend_direct` 排除在事件驱动 terminal progress 之外。

因此，这是一项有证据支持的新 Bugfix，但当前没有正式上游 Bug 编号。

## 问题定义

`AscendDirectTransport` 最多允许 100 个在途 Vendor Group。一个 Group 可以包含多个 Request；限制作用于 Group 数量，不是字节数或 Request 总数。

复现条件：

1. 使用 TENT Ascend Direct。
2. 连续提交超过 100 个独立 Group。
3. 调用者计划提交结束后再统一查询状态。
4. 前面的 Vendor 请求已经完成，但 TENT 尚未 poll。
5. 第 101 次提交在 admission 内等待 credit。

旧代码等待 `inflight_cv_`，但不查询 pending Group。调用者又因为第 101 次提交尚未返回而无法进入 poll，于是形成循环依赖：

```text
第 101 次提交等待 credit
          ↑
已完成 Group 等待调用者 poll
          ↑
调用者等待第 101 次 submit 返回
```

`quiesce()` 存在同类问题：它只等待 `inflight_` 归零，自身不推进完成。

直接影响：

- 远端 HIXL Group；
- 同引擎 `LocalCopyEngine` Group；
- 无消费者轮询的 quiesce；
- 大窗口、先提交后轮询的调用模式。

若多个 Request 被合并成不超过 100 个 Group，则不一定触发。

## 当前主干证据

上游快照中：

- [`quiesce()` 只等待计数](https://github.com/kvcache-ai/Mooncake/blob/b730bbae5f8b89ce79f609df9c66b59bea1b3f8b/mooncake-transfer-engine/tent/src/transport/ascend/ascend_direct_transport.cpp#L264-L276)
- [远端 Group 饱和等待](https://github.com/kvcache-ai/Mooncake/blob/b730bbae5f8b89ce79f609df9c66b59bea1b3f8b/mooncake-transfer-engine/tent/src/transport/ascend/ascend_direct_transport.cpp#L401-L413)
- [本地 Group 饱和等待](https://github.com/kvcache-ai/Mooncake/blob/b730bbae5f8b89ce79f609df9c66b59bea1b3f8b/mooncake-transfer-engine/tent/src/transport/ascend/ascend_direct_transport.cpp#L499-L511)

目标文件 SHA-256：

| 文件 | 当前 `main` |
|---|---|
| `ascend_direct_transport.h` | `7189dc0a1d00386c1952f302fe8cd2a7f370e03c886b2e916b68acb490942b2f` |
| `ascend_direct_transport.cpp` | `20c39bf5ed700b3abfddafafefba2b79d51e6f360c9dbf3562676909788da44a` |
| `local_copy_engine.cpp` | `a5b654c81e8280cbe2ef83b0389464008a3ec900e9255e55809e9540ed733620` |
| `ascend_direct_transport_test.cpp` | `dcf9e1d447117b5d2b0a497e2237166ffa26e967214e54edcc860fe4f02b71aa` |

这些值与未修复基线相同。

## 修复逻辑

补丁包含六个相互关联的部分：

| 问题 | 修改 |
|---|---|
| 饱和 admission 不推进完成 | 等待 credit 时调用 `progressPending()` |
| quiesce 依赖外部 poll | quiesce 主动推进 pending Group |
| credit 与 task 结果消费耦合 | Group 终态时归还 credit，结果保留到每个 task 消费 |
| Vendor handle 可复用 | 使用单调递增 `group_id` 保存应用结果 |
| pending Batch 被提前释放 | pending 状态返回 non-OK，不回收状态 |
| DMA flag 释放顺序 | 先 abort/destroy ACL stream，再释放 host flag |

这个实现属于 cooperative progress：

- 没有增加永久后台线程；
- 饱和 admission、状态查询和 quiesce 负责推进；
- `progress_mutex_` 串行化 Vendor 提交、轮询和 route teardown；
- 等待 credit 时不持有 progress mutex。

## 回归证据

| 构建 | 结果 | 能支持的结论 |
|---|---:|---|
| 原版 + 扩展测试 | 33 项中 7 项失败 | 缺陷可稳定复现 |
| 修复版 Debug | 34/34 | 主机状态机回归通过 |
| 修复版 Release | 34/34 | 最终生产源码回归通过 |
| ASan + UBSan | 34/34 | 已执行的主机内存路径未报错 |
| TSan | 34/34 | 已执行的主机并发路径未报数据竞争 |
| CANN/HIXL 9.1 构建 | 34/34 mock 测试 | 真实 SDK 编译和链接成立 |

修复版还覆盖 8 线程、2,048 个 Group，结束时在途额度为 0。

证据：

- [CPU 回归汇总](../results/cpu/summary.json)
- [SDK 构建摘要](../results/sdk-cann91/summary.json)
- [主机控制路径样本](../results/cpu/roundtrip-release.json)

真实 NPU DMA、跨机 HIXL 和驱动异常下的 stream 终止仍未验证。

## 上游记录与重叠判断

| 记录 | 关系 | 是否同一缺陷 |
|---|---|---|
| [RFC #3919](https://github.com/kvcache-ai/Mooncake/issues/3919) | 设计当前 TENT Ascend async/AutoConnect 后端，包含 quiesce | 否；未描述饱和循环依赖 |
| [PR #3933](https://github.com/kvcache-ai/Mooncake/pull/3933) | 引入当前代码结构和 24 项 mock 测试 | 否；是缺陷所在实现的来源 |
| [Issue #2116](https://github.com/kvcache-ai/Mooncake/issues/2116) | 讨论 status query、显式 progress API 和生命周期所有权 | 否；范围更广 |
| [PR #2277](https://github.com/kvcache-ai/Mooncake/pull/2277) | 给 RDMA/TCP/SHM/bufio 接入 terminal event progress | 否；评论明确排除 `ascend_direct` |
| [PR #3116](https://github.com/kvcache-ai/Mooncake/pull/3116) | 修复 Ascend `req_map_` 单任务泄漏 | 否；相邻结果生命周期问题 |
| [PR #2505](https://github.com/kvcache-ai/Mooncake/pull/2505) | 修复状态晚一个 poll 周期 | 否；相邻 polling 问题 |
| [Issue #4247](https://github.com/kvcache-ai/Mooncake/issues/4247) | 经典 TE 同步 Ascend 路径缺少 quiescence contract | 否；不同实现路径 |
| [Known Issue List #1276](https://github.com/kvcache-ai/Mooncake/issues/1276) | 公共已知问题列表 | 未记录本缺陷 |

最重要的差异是：[PR #2277](https://github.com/kvcache-ai/Mooncake/pull/2277)
追求事件驱动的 runtime progress，本补丁只在 Ascend Transport 内增加窄范围
cooperative polling，不引入后台 worker。

## Bug 编号

正式上游编号：**未分配**。

建议先提交：

> `[Bug][TENT] Ascend admission stalls after 100 groups without consumer polling`

Issue 获得编号后，PR 使用：

```text
Fixes #NNNN
```

不要自造看似官方的 Bug 编号。

## 是否达到正式提交条件

这是可提交的正式成果，但当前状态是 PR candidate，而不是 ready-to-merge。

已经满足：

- 生产修改低于 500 LOC，不要求 RFC；
- 补丁可以应用到最新目标文件；
- 有原版失败、修复版通过的差分回归；
- 有 sanitizer 和真实 SDK 编译证据；
- 有 PR 范围 pre-commit 历史结果。

提交前仍需：

1. 在最新完整 `main` 上重新编译，而不只检查 4 个目标文件。
2. 运行 `tent_ascend_direct_transport_test` 全部 34 项。
3. 重新运行 PR-scoped pre-commit。
4. 人类提交者逐行审查并能解释锁、状态和资源生命周期。
5. 最好补充真实 NPU 的窗口 128、多 remote、quiesce 和清理测试。
6. 创建 Issue，并在 PR 中解释与
   [#2116](https://github.com/kvcache-ai/Mooncake/issues/2116) /
   [#2277](https://github.com/kvcache-ai/Mooncake/pull/2277) 的差异。

普通 `tent-ci` 不启用 Ascend Direct；Ascend E2E 当前又设置 `BUILD_UNIT_TESTS=OFF`。PR 必须附本地 CANN/HIXL 测试日志，并请求维护者考虑 `run-e2e-ci` 或增加目标测试覆盖。

## 审核与合并规则

Mooncake `main` ruleset 要求：

- 所有变更经 Pull Request；
- 至少 1 个批准；
- 必须有 CODEOWNER 批准；
- 只允许 squash merge；
- 禁止删除和 non-fast-forward 更新。

TENT CODEOWNERS：

```text
@alogfans @doujiang24 @chestnut-Q @staryxchen @00fish0 @dtcccc
```

项目工作流还会运行 format、spell、TENT build/test 和 `CI Gate`。`run-ci` 标签用于重新运行同一 SHA 的 CI；`run-e2e-ci` 触发 Ascend 和集成测试。

## 文档要求

仓库没有独立 `CHANGELOG` 文件，不应新增非标准 changelog。

建议更新：

- `docs/source/api-reference/cpp/tent.md`：明确 pending Batch 的 `freeBatch()` 返回 non-OK，且不会回收仍在途状态。
- `docs/source/design/tent/testing.md`：记录 query-completion Transport 的 cooperative progress 测试边界。
- 需要时在 TENT progress/failover 文档说明本修复没有增加后台线程。

文档变更应按 `docs/AGENTS.md` 运行：

```bash
cd docs
make clean
make html
```
