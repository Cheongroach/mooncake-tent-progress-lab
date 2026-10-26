# Mooncake TENT 入门源码指南

本指南假设读者没有接触过 Mooncake、RDMA 或 HIXL。目标不是一次读完所有源码，而是建立一条能够解释本次补丁的最短路径。

如果只想了解项目结论，先读[入门版项目说明](../REPORT-friendly.md)。需要审查全部并发与资源语义时，再进入[技术版源码指南](source-guide.md)。

## 1. 先建立系统位置感

假设大模型推理分成两台机器：

```text
机器 A：Prefill
读取 Prompt，生成 KV Cache
          │
          │ 需要搬运 KV Cache
          ▼
机器 B：Decode
使用 KV Cache 逐 Token 生成
```

TENT 位于应用和底层网络/设备之间：

```text
vLLM / SGLang / 自定义应用
            ↓
       Mooncake TENT
            ↓
RDMA / TCP / SHM / Ascend Direct
            ↓
远端 CPU、GPU、NPU 或存储
```

TENT 不计算模型输出。它负责描述“从哪里搬多少数据到哪里”，选择传输后端，并维护异步状态。

## 2. 一次传输需要哪些对象

从[公开接口](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/include/tent/transfer_engine.h)开始，只认识以下五个对象：

### Engine

传输引擎实例，持有配置、transport、远端 segment 和运行状态。

```cpp
tent_engine_t engine = tent_create_engine();
```

### Memory

要被设备或远端访问的本地内存。先注册，设备才能安全访问：

```cpp
tent_register_memory(engine, buffer, size);
```

### Segment

远端对外发布的一段可访问内存。调用者通过 segment ID 和 offset 指定目标位置。

### Request

一条 READ 或 WRITE：

```cpp
tent_request_t request = {
    .opcode = OPCODE_WRITE,
    .source = local_buffer,
    .target_id = remote_segment,
    .target_offset = 0,
    .length = bytes,
};
```

### Batch

一组 Request 的容器。调用者提交后，可以查询其中每个 task 或整个 batch 的状态：

```cpp
auto batch = tent_allocate_batch(engine, request_count);
tent_submit(engine, batch, requests, request_count);
tent_task_status(engine, batch, task_id, &status);
tent_free_batch(engine, batch);
```

第一遍只要理解：

> Request 描述工作，Batch 组织工作，Transport 执行工作，Status 告诉应用工作走到哪里。

## 3. 为什么要注册内存

普通 CPU 代码拿到一个指针就可以读写，但网卡或 NPU 不能无条件访问任意虚拟地址。注册内存是在告诉系统：

- 这段地址从哪里开始、有多长；
- 哪个设备或 transport 可以访问；
- 允许读还是写；
- 需要建立哪些设备侧映射和 key。

RDMA 中常见的 MR、`lkey` 和 `rkey` 都来自这个过程。可以把它类比为仓库登记：

- 用户 buffer 是仓库里的货物；
- MR 是仓库的设备访问许可；
- `lkey/rkey` 是本地或远端访问凭证。

阅读顺序：

1. [transfer_engine_impl.cpp](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/src/runtime/transfer_engine_impl.cpp) 中搜索 `registerLocalMemory`；
2. [buffers.cpp](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/src/transport/rdma/buffers.cpp) 中看 RDMA 注册；
3. 暂时跳过异常分支，先找出“注册成功后保存了什么”。

关键判断是：设备仍可能访问时，不能先释放用户 buffer 或 MR。

## 4. 异步传输的核心：提交不是完成

下面两行不能被理解成同一件事：

```text
submit succeeded
transfer completed
```

提交成功表示 TENT 或底层设备接受了请求。真正完成可能稍后才发生：

```text
CPU 提交 WR / Vendor Request
        ↓
设备异步执行
        ↓
CQ 或 Vendor 状态出现完成
        ↓
软件消费完成事件
        ↓
更新 task 状态并回收资源
```

RDMA 中：

- WR 是工作描述；
- QP 是提交工作的位置；
- CQ 是读取完成事件的位置。

阅读 [endpoint.cpp](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/src/transport/rdma/endpoint.cpp) 和 [cq.cpp](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/src/transport/rdma/cq.cpp) 时，不必立即理解所有 verbs API。先回答两个问题：

1. 请求在哪里提交？
2. 完成从哪里被读取并映射回 task？

本次补丁没有修改 RDMA，但 RDMA 路径能帮助理解：异步系统必须有人持续消费完成事件，否则硬件做完了，软件额度仍可能不释放。

## 5. 进入本次修改的 Ascend Direct 后端

主文件是：

[ascend_direct_transport.cpp](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/src/transport/ascend/ascend_direct_transport.cpp)

第一次阅读只跟踪五个函数：

| 函数 | 先理解什么 |
|---|---|
| `submitTransferTasks()` | 一个 Batch 如何进入 Ascend transport |
| `resolveRequest()` | 请求最终走 HIXL 还是本地拷贝 |
| `startGroup()` | 多个 Request 如何变成 Vendor Group |
| `acquireInflight()` | 为什么提交前需要额度 |
| `getTransferStatus()` | 应用如何看到完成或失败 |

Ascend Direct 内部有两条路径：

- `HixlEngine`：使用 HIXL Vendor 接口执行传输；
- `LocalCopyEngine`：同一执行环境中的 ACL stream 拷贝。

不要把 HIXL 和 RDMA verbs 当成同一实现。它们都需要异步完成和资源回收，但底层 API 不同。

## 6. 用 3 个额度模拟第 101 个组

真实上限是 100。为了容易理解，假设上限只有 3：

```text
初始 credit = 3

提交 A：credit 3 → 2
提交 B：credit 2 → 1
提交 C：credit 1 → 0
Vendor 实际已经完成 A
提交 D：等待 credit
```

旧实现的问题是：只有调用者执行 `getTransferStatus(A)`，软件才发现 A 完成并归还 credit。但调用者准备在 D 提交结束后才统一查询。

```text
提交 D 等待 A 归还 credit
调用者等待 D 提交结束后才查询 A
```

两边互相等待。

修复后的 `acquireInflight()` 发现 credit 为 0 时，会主动调用 `progressPending()`：

```text
poll A → A 已完成 → credit 0 → 1 → 提交 D
```

阅读代码时，把 100 想成 3，逐步记录：

- `inflight_` 什么时候加一；
- Group 何时进入 pending 集合；
- `progressPending()` 如何找到终态；
- credit 由哪个函数、在什么条件下归还。

## 7. 为什么要同时修改结果生命周期

假设 Group A 包含 task A1 和 A2。底层完成整个 Group 后：

- 硬件已经不需要占用 Group credit；
- 调用者可能只查询了 A1；
- A2 的结果必须继续保留。

因此需要两个计数：

```text
inflight credit：底层还有多少 Group 未进入终态
remaining tasks：应用还有多少 task 结果未消费
```

这解释了为什么不能在第一次查询 A1 时简单删除整个 Group，也不能等到 A2 被查询后才归还硬件额度。

在源码中搜索：

- `GroupState`
- `completeGroup`
- `completePolledTask`
- `finishTaskLocked`
- `remaining`
- `counted`

画出一个 Group 从 `Pending` 到 `Completed/Failed`，再到结果全部消费的过程。

## 8. 为什么 Vendor handle 不能作为永久 ID

Vendor handle 是底层 SDK 返回的句柄。底层完成后可能复用同一个值：

```text
旧 Group A → handle 42 → 已完成，但 A2 尚未查询
新 Group B → handle 42 → 新请求复用
```

如果结果表以 `42` 为 key，B 可能覆盖 A2 的结果。

补丁增加单调递增的 `group_id`：

```text
handle 42 + group_id 1001  → 旧 Group A
handle 42 + group_id 1002  → 新 Group B
```

底层查询仍使用 handle，应用结果使用 `group_id`。理解这一点后，再看 map 和 pending 集合会容易很多。

## 9. 锁保护什么，为什么等待时要放锁

`progress_mutex_` 用于避免多个线程同时修改 Vendor 和 Group 状态：

- 一个线程正在提交并发布 Group；
- 另一个线程正在轮询完成；
- 第三个线程正在处理 route 失败。

这些操作需要串行化。但线程等待 credit 时不能一直持锁：

```text
线程 A：持锁等待 credit
线程 B：需要同一把锁才能 poll 并归还 credit
```

否则又会形成死锁式循环。阅读 `acquireInflight()` 时，特别检查等待和 `progressPending()` 发生在哪个锁范围内。

当前测试只覆盖不同线程操作各自 Batch，不代表同一个 Batch 可以被任意并发修改。

## 10. DMA flag 为什么最后释放

[local_copy_engine.cpp](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/src/transport/ascend/local_copy_engine.cpp)使用 host flag 观察 ACL stream 的异步完成。

如果顺序是：

```text
free flag
abort stream
```

设备可能在两步之间继续写 flag，造成设备侧 use-after-free。正确顺序是：

```text
abort stream
destroy stream
free flag
```

CPU 的 ASan 看不到所有设备 DMA，因此这个顺序虽然可以通过 mock 验证，仍需要真实 NPU 驱动实验确认。

## 11. 读测试时怎样避免过度推断

打开[回归测试](https://github.com/kvcache-ai/Mooncake/blob/5d0636f5d39f9de729a58ea036f98c395dd0b291/mooncake-transfer-engine/tent/tests/ascend_direct_transport_test.cpp)，优先读名称包含以下关键词的用例：

- `Saturated`
- `Quiesce`
- `ReusedVendorHandle`
- `Concurrent`
- `FreeRejectsPending`
- `FailureDoesNotPoison`
- `FinalizeAbortsBeforeFreeingFlags`

每读一个用例，写下三件事：

1. 输入和故障条件是什么？
2. 断言直接观察了什么？
3. 这个断言不能证明什么？

例如，mock 测试可以证明“代码先调用 abort 再调用 free”，但不能证明真实 NPU 驱动收到 abort 后已经停止 DMA。

证据层次如下：

| 证据 | 能证明什么 |
|---|---|
| 原版 7 项失败 | 目标问题可复现 |
| 修复版 34/34 | 主机状态机回归通过 |
| ASan/UBSan/TSan | 已执行的主机内存与并发路径未报错 |
| CANN/HIXL 9.1 完整构建 | 真实 SDK 编译和链接成立 |
| TCP/RDMA 数据校验 | 测试工具和对应后端能搬运真实数据 |
| 尚未完成的 NPU 实验 | 不能声称真实 DMA/HIXL 已验证 |

## 12. 推荐阅读顺序

按照以下顺序，每一步只回答一个问题：

1. `transfer_engine.h`：应用能提交什么？
2. `transfer_engine_c.cpp`：C API 如何进入 C++ 实现？
3. `transfer_engine_impl.cpp`：Batch 如何拆到 transport？
4. `ascend_direct_transport.cpp`：Group、credit 和完成如何组织？
5. `hixl_engine.cpp`：Vendor handle 如何产生和查询？
6. `local_copy_engine.cpp`：ACL stream 和 host flag 如何管理？
7. `ascend_direct_transport_test.cpp`：每个故障条件如何被隔离验证？
8. `tent_bench.py`：完整公开 API 如何用于真实数据传输？

读完后，应该能用自己的话回答：

- 为什么第 101 个 Group 会卡住？
- 为什么完成时归还 credit，但结果还要保留？
- 为什么 handle 复用会串结果？
- 为什么 stream 必须先于 host flag 释放？
- 哪些结论已经验证，哪些仍需要真实 NPU？
