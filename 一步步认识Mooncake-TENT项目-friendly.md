# 一步步认识这个 Mooncake TENT 项目

这不是 Mooncake API 手册，也不是 RDMA 术语表。

它只做一件事：让已经了解 Tensor、GPU 推理和 KV Cache 的读者，逐步看懂这个项目为什么修改 Mooncake TENT、修改了什么，以及证据能支持哪些结论。

本文不重复 embedding、Attention、Prefill、Decode 等基础知识。我们从一个已经计算好的 Tensor 开始，只研究它怎样从一处内存移动到另一处内存。

请按顺序阅读。每一步只增加一个新概念。读完一步后，先回答该步末尾的问题；答不出来就停在这里，不要继续查后面的代码。

全文分为 10 个阶段。本文中的数字来自已经完成的历史实验，不是你刚刚运行得到的结果。

---

## 第一阶段：先看懂项目为什么存在

### 第 1 步：模型算出的数据不一定留在原地

假设 Prefill 节点已经算出一块 KV Cache。

Decode 可能运行在另一张卡、另一台机器，甚至另一种加速器上。它要继续生成，就必须先拿到这块 KV Cache。

本项目从这里开始：

```text
一块已经算好的 Tensor
        ↓
移动到需要使用它的位置
```

只记住：**这个项目研究数据搬运，不研究模型怎样计算 Tensor。**

停下来回答：本项目修改的是模型计算，还是计算结果的移动过程？

---

### 第 2 步：普通复制不能覆盖所有位置组合

如果源和目标都在同一进程的 CPU 内存中，`memcpy` 就能复制。

但真实推理系统可能出现：

```text
本机 CPU → 本机 NPU
本机 CPU → 远端 CPU
本机 GPU → 远端 GPU
本机 NPU → 远端 NPU
```

不同位置需要不同的设备 API、网络协议和权限。

只记住：**数据位置不同，搬运方法也不同。**

停下来回答：为什么一个普通 `memcpy` 不能统一解决跨机和跨设备传输？

---

### 第 3 步：Mooncake 是项目，TENT 是其中的数据搬运层

Mooncake 是面向大模型系统的数据传输与缓存项目。

TENT 是 Mooncake Transfer Engine 的当前核心实现。它位于应用和具体传输后端之间：

```text
vLLM / SGLang / 自定义应用
              ↓
         Mooncake TENT
              ↓
RDMA / TCP / SHM / Ascend Direct
```

TENT 不是一种新网络协议。它把多种传输后端放在统一接口下面。

只记住：**TENT 是统一调度多种数据搬运方式的软件层。**

停下来回答：TENT 本身是不是 RDMA 的另一种名字？

---

### 第 4 步：应用只描述“搬什么、搬到哪里”

应用不应该为每个请求重新实现 RDMA、TCP 或 HIXL。

它向 TENT 提交的核心信息是：

```text
操作：READ 还是 WRITE
源地址：数据当前在哪里
目标：哪个远端内存段
偏移：写到目标的什么位置
长度：移动多少字节
```

TENT 再把这个统一请求交给合适的后端。

只记住：**应用描述数据移动，Transport 负责执行。**

停下来回答：应用提交给 TENT 的重点是模型层名称，还是地址、方向和长度？

---

### 第 5 步：这个项目只修改 Ascend Direct 后端

TENT 支持多种 Transport。本项目真正修改的是：

```text
TENT
└── AscendDirectTransport
    ├── HixlEngine
    └── LocalCopyEngine
```

它没有修改 NVIDIA RDMA Transport，也没有重新实现 TCP。

现在只需把 `HixlEngine` 理解为“调用 Ascend 数据传输 SDK 的路径”，把 `LocalCopyEngine` 理解为“使用 ACL stream 做本地异步拷贝的路径”。具体 API 放到最后再看。

项目运行 TCP 和 RDMA 实验，是为了验证公共测试工具和完整 TENT 数据路径，不是为了把 RDMA 成绩算成 Ascend 补丁收益。

只记住：**生产补丁作用于 Ascend Direct，TCP/RDMA 是独立验证路径。**

停下来回答：两机 RDMA 测试通过，能否直接证明 Ascend 补丁提高了性能？

---

### 第一阶段检查

你现在应该能说出这条链：

```text
应用描述一次 Tensor 搬运
        ↓
TENT 接收统一请求
        ↓
具体 Transport 执行
```

并且知道本项目只修改 `AscendDirectTransport`。

如果你仍把 TENT 理解成模型或网络协议，请不要继续。

---

## 第二阶段：认识一次 TENT 传输

### 第 6 步：Engine 是一次 TENT 运行实例

应用先创建一个 Transfer Engine：

```cpp
tent_engine_t engine = tent_create_engine();
```

Engine 持有配置、可用 Transport、远端信息和运行状态。

可以把它理解成一个已经启动的数据搬运服务对象。

只记住：**Engine 是应用使用 TENT 的入口实例。**

停下来回答：Engine 是一条具体的网络请求，还是管理许多请求的运行实例？

---

### 第 7 步：内存必须先注册

设备和远端节点不能无条件访问任意用户指针。

应用先向 TENT 登记一段内存：

```cpp
tent_register_memory(engine, buffer, size);
```

登记过程会说明地址、长度、权限、内存位置和允许使用的 Transport。

只记住：**注册内存是在传输前建立设备访问条件。**

停下来回答：为什么不能把任意指针直接交给网卡或 NPU？

---

### 第 8 步：Segment 是远端可定位的内存

本地指针只在本进程地址空间中有意义。

远端节点需要一个可交换的标识来定位目标内存。TENT 把可访问内存组织成 Segment，并让另一端打开它：

```cpp
tent_open_segment(engine, &remote_segment, remote_name);
```

Request 使用 Segment ID 和偏移定位目标，而不是使用对方进程中的裸指针。

只记住：**Segment 让远端内存可以被命名和定位。**

停下来回答：远端请求为什么不能直接使用目标进程的普通虚拟地址？

---

### 第 9 步：Request 描述一个 READ 或 WRITE

一个 Request 可以简化成：

```cpp
tent_request_t request = {
    .opcode = OPCODE_WRITE,
    .source = local_buffer,
    .target_id = remote_segment,
    .target_offset = 0,
    .length = bytes,
};
```

这里没有模型层、Token 或 hidden size。到 TENT 这一层，数据主要表现为地址、目标和字节数。

只记住：**Request 是一条字节搬运说明。**

停下来回答：`length` 表示 Tensor 的维度，还是实际传输的字节数？

---

### 第 10 步：Batch 组织一组 Request

应用先分配 Batch，再提交一个或多个 Request：

```cpp
auto batch = tent_allocate_batch(engine, request_count);
tent_submit(engine, batch, requests, request_count);
```

应用随后可以查询某个 task 或整个 Batch 的状态。

Batch 是应用层组织单位，不等于底层网卡队列，也不一定等于一个 Vendor 请求。

只记住：**Batch 是应用提交和查询一组任务的容器。**

停下来回答：一个 Batch 是否必然对应一个底层 Vendor 请求？

---

### 第 11 步：Transport 是具体执行者

TENT 根据配置、内存位置和 Request hint 选择 Transport。

本项目涉及的三种路径是：

| Transport | 主要用途 |
|---|---|
| TCP | 通用跨机网络路径 |
| RDMA | 低 CPU 参与的高速跨机内存访问 |
| Ascend Direct | Ascend 环境中的 HIXL 或 ACL 搬运 |

同一个 TENT API 可以驱动不同后端，但它们内部实现不同。

只记住：**统一接口不表示底层机制相同。**

停下来回答：RDMA 和 HIXL 为什么可以共享上层接口，却不能被当作同一底层实现？

---

### 第二阶段检查

你现在应该能按顺序说出：

```text
创建 Engine
→ 注册 Memory
→ 打开远端 Segment
→ 分配 Batch
→ 提交 Request
→ 查询 Status
→ 释放 Batch 和 Memory
```

如果 Request、Batch 和 Segment 仍混在一起，请只回看第 6 到 11 步。

---

## 第三阶段：理解异步传输

### 第 12 步：提交成功只表示请求被接受

调用：

```cpp
tent_submit(...)
```

返回成功时，通常只说明 TENT 和底层后端接受了请求。

设备可能还在搬运数据。

只记住：**submit 成功不等于 transfer 完成。**

停下来回答：`tent_submit()` 成功后，源 Buffer 是否一定可以立即释放？

---

### 第 13 步：完成状态来自稍后的观察

异步路径大致是：

```text
CPU 提交请求
    ↓
设备异步执行
    ↓
底层产生完成状态
    ↓
软件读取完成状态
    ↓
应用看到 COMPLETED
```

底层完成和应用看到完成之间存在一个软件推进步骤。

只记住：**硬件完成以后，软件仍要消费完成信息。**

停下来回答：设备已经完成，但软件从未读取状态，应用是否一定知道它已完成？

---

### 第 14 步：进展性描述系统能否继续向前

异步系统不仅要保证结果正确，还要保证已经具备完成条件的请求最终能被推进。

如果设备已经完成，但软件状态永远停在 pending，系统没有保持 progress。

本项目中的“进展性”不是速度指标，而是：

> 系统不会因为内部循环依赖而永久或超时停滞。

只记住：**progress 关注能不能继续，不等于每秒能传多少数据。**

停下来回答：吞吐很高的系统是否仍可能存在进展性缺陷？

---

### 第 15 步：在途请求需要额度

底层资源有容量限制，不能无限提交。

Ascend Direct 用 in-flight credit 限制尚未进入终态的传输 Group 数量。当前上限是 100。

简化理解：

```text
提交一个 Group：占用一个 credit
Group 完成或失败：归还一个 credit
```

只记住：**credit 是对在途 Group 数量的限制。**

停下来回答：credit 限制的是传输字节总数，还是未完成 Group 数量？

---

### 第 16 步：Request 会先被合并成 Group

这里的 Vendor 指 TENT 下方的设备 SDK/运行库，在本项目中主要是 HIXL。Ascend Direct 可以把具有相同执行条件的 Request 合并：

```text
(local_engine, remote, opcode) 相同
        ↓
同一个 Vendor Group
```

因此：

```text
应用 Request 数量 ≠ Vendor Group 数量
```

100 的限制作用于 Group。

只记住：**Group 是 Ascend 后端向 Vendor 提交的单位。**

停下来回答：一个包含 128 个 Request 的 Batch，是否必然占用 128 个 credit？

---

### 第 17 步：轮询是推进完成的一种方式

应用查询状态时，TENT 可以向 Vendor 询问 Group 是否完成。

如果返回终态，TENT 更新 task 结果并归还 credit。

旧实现主要依赖这种消费者查询来推进完成。

只记住：**旧实现把 credit 回收依赖在后续状态查询上。**

停下来回答：调用者一直不查询，旧实现是否一定会及时归还所有 credit？

---

### 第三阶段检查

你应该能够区分：

```text
提交：请求被接受
设备完成：底层搬运结束
软件推进：读取完成并更新状态
结果消费：调用者取得结果
```

这四个时刻不是同一时刻。

如果这点不清楚，请不要继续，因为后面的缺陷完全由这些时刻的错位产生。

---

## 第四阶段：用 3 个额度看懂第 101 个 Group

### 第 18 步：先把真实上限 100 缩小成 3

为了容易观察，假设最多只有 3 个在途 Group：

```text
初始 credit = 3
```

提交 A、B、C 后：

```text
A：pending
B：pending
C：pending
credit = 0
```

只记住：**3 个 Group 已经占满全部额度。**

停下来回答：此时还能直接提交 D 吗？

---

### 第 19 步：设备完成不自动改变软件 credit

假设 Vendor 已经完成 A。

但 TENT 尚未查询 A，因此软件仍然记录：

```text
A：pending
credit = 0
```

硬件事实和软件状态暂时不一致。

只记住：**Vendor 完成不等于 TENT 已经处理完成。**

停下来回答：为什么 A 已经完成，软件的 credit 仍可能是 0？

---

### 第 20 步：第 4 次提交开始等待

调用者继续提交 D。

`acquireInflight()` 发现没有 credit，于是等待某个 Group 归还额度。

```text
submit D → 等待 credit
```

只记住：**提交路径在进入 Vendor 之前就被额度阻塞。**

停下来回答：D 此时已经交给 Vendor 了吗？

---

### 第 21 步：调用者原本准备提交完再查询

一种常见调用顺序是：

```text
先提交 A、B、C、D
再查询 A、B、C、D
```

但 D 的提交尚未返回，所以程序还没有进入查询阶段。

只记住：**查询代码存在，但当前控制流还到不了那里。**

停下来回答：为什么不能简单说“调用者稍后会 poll，所以没有问题”？

---

### 第 22 步：两条路径形成循环依赖

现在出现：

```text
D 等待 A 归还 credit
A 等待调用者 poll
调用者等待 D submit 返回
```

三者互相等待。最终 D 可能在提交阶段超时。

这就是本项目修复的核心缺陷。

只记住：**第 101 个 Group 卡住，是提交与完成推进之间的循环依赖。**

停下来回答：缺陷发生时，底层是否必须还在传输 A？

---

### 第 23 步：真实系统把 3 换成 100

真实触发序列只是规模更大：

```text
提交 Group 1..100
Vendor 已完成其中一部分
提交 Group 101
Group 101 等待 credit
调用者尚未开始统一查询
```

问题不要求网络很慢，也不要求设备失败。

只记住：**正常完成的请求也能触发这个停滞。**

停下来回答：这个问题是错误处理问题，还是正常异步路径也可能出现？

---

### 第 24 步：quiesce 也有同类依赖

`quiesce()` 表示系统准备停止，并等待在途工作排空。

如果它只等待外部调用者查询，那么调用者不再查询时，已经完成的 Group 也可能一直占用额度和状态。

只记住：**排空路径必须能够主动发现完成。**

停下来回答：系统停止接收新请求以后，为什么仍需要推进完成？

---

### 第四阶段检查

请闭卷复述：

1. 为什么前 100 个 Group 会占满 credit？
2. 为什么 Vendor 已完成也不一定归还 credit？
3. 为什么第 101 次提交让调用者无法进入 poll？
4. 为什么 quiesce 也受影响？

四个问题都能回答，再进入修复。

---

## 第五阶段：理解修复为什么不是“加一行 poll”

### 第 25 步：饱和时主动推进 pending Group

修复后的 `acquireInflight()` 在没有 credit 时，不只是等待。

它调用 `progressPending()` 检查 pending Group：

```text
credit = 0
    ↓
poll pending Group
    ↓
发现 A 已完成
    ↓
归还 A 的 credit
    ↓
提交 D
```

只记住：**等待额度的线程会协作推进已有工作。**

停下来回答：修复是否要求新增一个永久后台线程？

---

### 第 26 步：quiesce 复用同一推进逻辑

排空时也调用 `progressPending()`。

这样即使没有外部消费者继续查询，系统仍能发现底层终态并完成清理。

只记住：**提交饱和和停机排空使用同一种完成推进机制。**

停下来回答：为什么 quiesce 不能只检查计数，然后原地等待？

---

### 第 27 步：Group 完成时应该立即归还 credit

credit 表示底层在途容量。

只要 Group 已经完成或失败，它就不再占用在途位置，应该归还 credit。

这件事不应等待应用读取每个 task 结果。

只记住：**底层终态决定 credit 回收。**

停下来回答：调用者尚未读取结果，是否意味着底层 Group 仍然在途？

---

### 第 28 步：结果仍要保留到调用者读取

一个 Group 可以包含多个 task。

假设 Group G 包含 G1 和 G2：

```text
Vendor 报告整个 G 完成
调用者只查询了 G1
G2 还没有被查询
```

此时 credit 应归还，但 G2 的结果不能删除。

只记住：**归还容量和删除结果是两个生命周期。**

停下来回答：为什么不能在第一个 task 被查询时删除整个 Group？

---

### 第 29 步：remaining 记录尚未消费的 task

修复后的 Group 状态需要同时知道：

```text
Group 是否已经终态
还有多少 task 结果未消费
credit 是否已经归还
```

`remaining` 记录未消费 task，`counted` 防止 credit 被重复归还。

只记住：**一个状态对象同时管理底层终态和应用结果消费。**

停下来回答：`remaining=0` 描述的是 Vendor 完成，还是所有 task 结果已被消费？

---

### 第 30 步：Vendor handle 不是永久唯一 ID

HIXL 返回一个 handle，TENT 用它查询底层状态。

底层完成后可能复用同一个 handle：

```text
旧 Group A → handle 42
新 Group B → handle 42
```

如果旧 Group 还有未读取结果，单独用 `42` 作为结果表 key 会发生覆盖。

只记住：**底层查询句柄的生命周期可能短于应用结果。**

停下来回答：为什么 Vendor handle 适合查询硬件，却不适合永久标识应用结果？

---

### 第 31 步：group_id 隔离新旧结果

修复为每个应用 Group 分配单调递增的 `group_id`：

```text
handle 42 + group_id 1001 → 旧 Group A
handle 42 + group_id 1002 → 新 Group B
```

Vendor handle 继续用于底层查询，`group_id` 用于保存应用结果。

只记住：**一个 ID 服务底层，一个 ID服务应用生命周期。**

停下来回答：新旧 Group 使用相同 handle 时，靠什么避免结果串线？

---

### 第 32 步：pending Batch 不能提前释放

如果 Batch 对应的 Group 仍在飞行，设备和完成逻辑仍可能引用其状态。

修复明确拒绝释放 pending sub-batch；进入终态并消费结果后才能回收。

只记住：**释放 API 也必须服从异步生命周期。**

停下来回答：`freeBatch()` 被调用，是否意味着底层一定已经停止访问相关状态？

---

### 第 33 步：DMA completion flag 必须最后释放

`LocalCopyEngine` 使用主机内存中的 flag 观察 ACL stream 完成。

旧顺序可能是：

```text
free flag
abort stream
```

设备可能在两步之间继续写已经释放的 flag。

修复后的顺序是：

```text
abort stream
destroy stream
free flag
```

只记住：**先停止可能的 DMA 写入，再释放写入目标。**

停下来回答：为什么 CPU 代码不再使用 flag，也不能立刻 free？

---

### 第 34 步：锁既要保护状态，也不能阻止进展

`progress_mutex_` 串行化：

- Vendor 提交和 Group 发布；
- pending 轮询和终态发布；
- route 失败和连接清理。

但等待 credit 时不能一直持有这把锁。否则其他线程无法进入 `progressPending()`，又会形成：

```text
持锁者等待 credit
推进者等待锁
```

只记住：**等待资源时不能堵住归还资源的路径。**

停下来回答：为什么“给所有函数都加同一把锁”不一定解决并发问题？

---

### 第 35 步：故障范围不能无条件扩大

远端 HIXL 失败属于某个 `(engine, remote)` route。

本地拷贝失败属于某个 ACL stream。

一个 route 失败，不应自动把另一个 remote 的正常请求标成失败。

只记住：**错误处理要按实际共享资源隔离。**

停下来回答：远端 A 连接失败，为什么不能直接把远端 B 的 Group 一起失败？

---

### 第五阶段检查

修复同时分开了三类生命周期：

```text
1. Vendor Group 何时进入终态
2. credit 何时归还
3. task 结果和 DMA 资源何时释放
```

请再回答：

- 为什么完成时归还 credit，但不能立刻删除全部结果？
- 为什么 handle 和 group_id 必须分开？
- 为什么等待 credit 时不能持有 progress mutex？
- 为什么 stream 要先于 host flag 释放？

---

## 第六阶段：把概念映射到状态变化

### 第 36 步：Group 从 Pending 开始

Group 成功提交给 Vendor 后：

```text
Group = Pending
inflight += 1
```

它进入 pending 集合，等待后续推进。

只记住：**Pending 表示已经提交但尚未发布终态。**

停下来回答：Pending 是否表示 Request 还没有交给底层？

---

### 第 37 步：完成和失败都是终态

Vendor 查询可能返回：

```text
Completed
Failed
Timeout
```

对 credit 来说，这些都是“不再继续占用正常在途位置”的终态。

结果内容不同，但额度回收都必须恰好发生一次。

只记住：**成功和失败都需要完成资源结算。**

停下来回答：失败 Group 是否可以永远不归还 credit？

---

### 第 38 步：Retained 表示底层已结束、结果仍保留

可以把终态后的 Group 理解成 Retained：

```text
Pending
  ↓ Vendor 终态
Completed / Failed
  ↓ 归还 credit
Retained result
  ↓ 所有 task 消费
删除 Group 状态
```

Retained 不是仍在传输，而是等待应用读取历史结果。

只记住：**终态结果可以存在，但不应占用 in-flight credit。**

停下来回答：Retained Group 是否应该被 `progressPending()` 反复轮询？

---

### 第 39 步：pending 集合和结果表用途不同

pending 集合只保存需要继续查询底层的 Group。

结果表可以保存已经完成、但应用尚未消费的结果。

把两者混在一起，会让完成扫描越来越大，也容易把容量与结果生命周期重新耦合。

只记住：**需要继续推进的对象和只需保留的对象应分开。**

停下来回答：为什么完成历史不应该继续出现在 pending 扫描中？

---

### 第 40 步：并发测试只承诺独立 Batch

修复版测试覆盖 8 个线程并发提交，共 2,048 个 Group。

每个线程拥有独立 Batch。

这不能推广成“同一个 Batch 可以被任意线程同时修改”，也没有覆盖并发 shutdown。

只记住：**并发结论只覆盖实际测试的所有权模型。**

停下来回答：8 线程测试通过，能否证明同一 Batch 的所有操作都线程安全？

---

### 第六阶段检查

你应该能画出：

```text
Pending
  ├── Completed
  └── Failed
         ↓
    credit 归还
         ↓
    结果继续保留
         ↓
    task 全部消费
         ↓
       删除
```

如果你仍把“完成、归还额度、删除结果”看作一个动作，请回看第 27 到 40 步。

---

## 第七阶段：理解每层测试到底证明什么

### 第 41 步：先用原版失败证明问题真实存在

扩展测试运行在原版生产代码上：

```text
33 项测试
7 项失败
```

失败场景覆盖饱和提交、quiesce、handle 复用、超时额度回收、pending 释放和 DMA flag 顺序。

只记住：**修复前失败是差分证据的一半。**

停下来回答：如果只展示修复版通过，为什么仍不足以证明测试真的命中了缺陷？

---

### 第 42 步：修复版通过证明主机状态机符合断言

修复版结果：

```text
Debug：34/34
Release：34/34
```

这说明扩展测试覆盖到的状态变化符合预期。

它不自动证明未测试的路径，也不证明真实设备行为。

只记住：**单元回归证明被隔离的代码性质，不证明整个系统。**

停下来回答：34/34 通过能否直接证明跨机 HIXL 已经工作？

---

### 第 43 步：Sanitizer 检查已执行的主机路径

修复版还通过：

```text
ASan + UBSan：34/34
TSan：34/34
```

ASan/UBSan 检查已执行的主机内存和未定义行为路径；TSan 检查已执行的主机线程访问。

它们看不到真实 NPU 仍在进行的 DMA 写入。

只记住：**主机 sanitizer 不能替代设备生命周期验证。**

停下来回答：ASan 没报错，是否证明 NPU 绝不会写入已释放内存？

---

### 第 44 步：CPU shim 用于可控地制造状态

测试用 shim 替换部分 ACL、HIXL 和 RPC 边界。

这样可以精确控制：

- 什么时候完成；
- 什么时候失败；
- handle 是否复用；
- 调用顺序如何记录。

shim 的目的不是模拟真实性能。

只记住：**mock 提供确定性，不提供设备真实性。**

停下来回答：为什么 handle 复用测试适合 mock，而吞吐测试不适合 mock？

---

### 第 45 步：真实 SDK 构建验证接口兼容

项目在 CANN/HIXL 9.1、GCC 11.4、GLIBC 2.35 环境中构建了：

```text
libtent_shared.so
tent_ascend_direct_transport_test
```

两个产物的动态库依赖都能解析，测试为 34/34。

只记住：**真实 SDK 构建回答“能否编译和链接”。**

停下来回答：为什么真实 SDK 构建成功仍不等于真实 NPU 传输成功？

---

### 第 46 步：头文件检查是更窄的一层证据

另一个检查只使用真实 CANN/HIXL 头文件执行 `-fsyntax-only`。

它验证类型和函数声明是否兼容，但不生成最终可执行文件，也不链接或运行 SDK。

只记住：**头文件检查、完整链接和设备执行是三层不同证据。**

停下来回答：`-fsyntax-only` 通过能否发现运行时驱动错误？

---

### 第 47 步：两机 RDMA 验证测试工具，不验证 Ascend 补丁

两机 16 进程 RDMA 实验完成：

```text
147,456 次传输
51.1875 GiB payload
所有指定范围校验通过
16 个进程正常退出
```

数据使用主机内存，走 RDMA Transport。

只记住：**它证明完整 TENT C ABI 测试链能搬运并校验真实数据。**

停下来回答：这组 RDMA 数字是否来自 Ascend Direct Transport？

---

### 第 48 步：验收器也必须被反向测试

只检查“输出记录数量正确”容易漏掉错误矩阵。

项目故意构造四类篡改：

- 修改消息大小但保持记录数；
- 混入错误 Transport；
- 修改 warmup 标记；
- 修改物理机归属。

验收器拒绝这些篡改，完整原始结果通过。

只记住：**验证工具本身也需要失败样本。**

停下来回答：为什么“记录数等于预期”不能单独证明实验矩阵正确？

---

### 第七阶段检查

请把证据按强弱和用途放回正确位置：

| 证据 | 回答的问题 |
|---|---|
| 原版 7 项失败 | 缺陷能否复现 |
| 修复版 34/34 | 主机状态机是否满足断言 |
| ASan/UBSan/TSan | 已执行的主机内存与并发路径 |
| CANN/HIXL 完整构建 | 真实 SDK 是否能编译链接 |
| 两机 RDMA | 公共测试工具和 RDMA 数据路径 |
| 尚缺的 NPU 实验 | 真实 HIXL/DMA 行为 |

如果你会把某一行当成另一行的替代，请回看第 41 到 48 步。

---

## 第八阶段：理解性能数字而不夸大

### 第 49 步：先测修复自身的主机控制路径成本

微基准注入一个立即完成的 Vendor，重复：

```text
allocate → submit → poll → free
```

每个样本运行 10,000 轮，原版和修复版各 11 个样本。

只记住：**这个实验隔离主机状态管理成本。**

停下来回答：它是否包含真实网卡或 NPU 的传输时间？

---

### 第 50 步：修复不是零成本

中位数：

```text
原版：1,251 ns
修复版：1,313 ns
差值：+62 ns，约 +5%
```

这说明新增状态和同步有可测成本。

不能把 +5% 推广成完整网络请求总延迟增加 5%。

只记住：**+5% 只属于注入 Vendor 的主机控制路径。**

停下来回答：为什么这个比例不能直接应用到 1 MiB 跨机传输延迟？

---

### 第 51 步：窗口表示同时在途的独立 Batch 数

网络测试使用窗口 1、32、128。

窗口更大时，调用者可以先提交更多工作，再集中等待完成，从而隐藏部分提交和完成延迟。

窗口 128 还能跨过 Ascend 后端的 100 Group 上限，用于复现饱和行为。

只记住：**窗口控制独立在途工作数量。**

停下来回答：窗口 128 是否表示一个 Batch 里有 128 个 Request？

---

### 第 52 步：大窗口不保证一直更快

RDMA 结果中，小消息从窗口 1 增加到 32 后吞吐明显提高。

但多目标 1 MiB WRITE 从窗口 32 的 16.765 GB/s 降到窗口 128 的 11.765 GB/s。

每个设置只有 3 次重复，现有证据不足以确定下降机制。

只记住：**观察到下降，不等于已经知道原因。**

停下来回答：能否仅凭这三个重复断言“窗口 128 导致 NIC 拥塞”？

---

### 第 53 步：图中的 GB/s 是单发起者 API 吞吐

RDMA 图中的每个值表示一个发起者完成一轮 API 调用的中位吞吐。

计时包含 Python/ctypes、submit、poll 和 free，不包含注册与数据校验。

多个进程共享同一 NIC，因此不能把每个发起者的中位数乘以 8，当作同步集群总吞吐。

只记住：**先确认统计口径，再解释带宽数字。**

停下来回答：为什么 8 个发起者各自的中位数不能简单相加？

---

### 第八阶段检查

你应该能准确说出：

- +5% 是什么成本；
- 窗口 128 为什么与缺陷复现有关；
- RDMA GB/s 的分母和计时范围；
- 哪些吞吐变化仍然无法解释。

如果只能记住“17.965 GB/s”，但说不清口径，请回看第 49 到 53 步。

---

## 第九阶段：准确说出项目完成了什么

### 第 54 步：项目产出的是一个真实上游补丁

补丁修改：

```text
3 个生产文件
1 个测试文件
生产代码 +202 / -142 行
新增 10 个测试场景
```

它可以应用到固定 Mooncake commit，并已执行 `git apply --check`。

只记住：**项目不是只写实验脚本，而是修改真实 Transport 实现。**

停下来回答：项目的核心交付是新的通信框架，还是针对现有后端的修复？

---

### 第 55 步：项目没有增加后台进度线程

完成推进发生在：

- 饱和 admission；
- 状态查询；
- quiesce。

没有新增永久后台线程。

这个选择减少了公开行为变化，但 progress 仍由进入这些路径的线程协作完成。

只记住：**这是 cooperative progress，不是独立 progress engine。**

停下来回答：没有后台线程时，谁负责调用 `progressPending()`？

---

### 第 56 步：项目没有改变公开 API

应用仍使用原来的 Engine、Memory、Batch、Request 和 Status 接口。

变化发生在 Ascend Direct 内部状态与资源管理中。

只记住：**调用者接口不变，内部语义被修正。**

停下来回答：现有应用是否需要因为这次补丁改写 Request 结构？

---

### 第 57 步：项目尚未完成真实 NPU HIXL 验证

已经完成：

- 真实 SDK 头文件检查；
- 完整 SDK 编译和链接；
- 使用 mock 的 34 项测试。

尚未完成：

- 真实 NPU DMA；
- 跨机 HIXL；
- 驱动异常下的 stream 终止；
- 原版与修复版真实设备性能对照。

只记住：**SDK 能构建不等于设备链路已验证。**

停下来回答：简历能否写“完成 16 卡 NPU 通信验证”？

---

### 第 58 步：项目尚未合并上游

项目有本地 PR 草稿，但没有发布 issue 或 pull request，也没有合并。

因此可以说“实现并验证补丁”，不能说“贡献已被 Mooncake 接受”。

只记住：**本地完成、公开提交和上游合并是三个阶段。**

停下来回答：有 PR 草稿是否等于已经向上游提交？

---

### 第 59 步：简历应同时写结果和边界

准确表述可以是：

> 基于 Mooncake TENT 修复 Ascend 异步传输的饱和提交进展与资源回收问题，解决调用者延迟轮询导致的额度停滞及 Vendor handle 复用串结果；新增 10 项上游风格回归，实现原版 7 项失败到修复版 34/34 通过，并通过 ASan/UBSan、TSan 及 CANN/HIXL 9.1 完整构建验证。

面试时主动补充：真实 NPU DMA/HIXL 尚未完成。

只记住：**边界说明不会削弱项目，反而证明你理解证据。**

停下来回答：为什么不能把 RDMA 结果写成 Ascend 补丁吞吐提升？

---

### 第九阶段检查

现在请把项目压缩成四句话：

1. TENT 在系统中负责什么？
2. 第 101 个 Group 为什么卡住？
3. 修复如何分离 credit、结果和资源生命周期？
4. 哪一项关键设备证据仍缺失？

能不看文档回答，再进入源码。

---

## 第十阶段：最后才看项目文件

### 第 60 步：先读公开 API，不看实现细节

打开：

```text
upstream/mooncake-transfer-engine/tent/include/tent/transfer_engine.h
```

只寻找：

1. Engine 如何创建；
2. Memory 如何注册；
3. Batch 如何分配；
4. Request 如何提交；
5. Status 如何查询。

本步不要进入 RDMA 或 HIXL。

新认知点：**应用看到的 TENT 契约是什么。**

---

### 第 61 步：再看请求如何进入核心实现

打开：

```text
upstream/mooncake-transfer-engine/tent/src/transfer_engine_c.cpp
upstream/mooncake-transfer-engine/tent/src/runtime/transfer_engine_impl.cpp
```

只跟踪：

```text
C API
→ TransferEngineImpl
→ 选择 Transport
→ 分配 SubBatch
→ 提交
```

本步不要研究所有配置项。

新认知点：**统一 Request 在哪里被交给具体 Transport。**

---

### 第 62 步：再读 Ascend Direct 的五个函数

打开：

```text
upstream/mooncake-transfer-engine/tent/src/transport/ascend/
ascend_direct_transport.cpp
```

按顺序寻找：

1. `submitTransferTasks()`
2. `resolveRequest()`
3. `startGroup()`
4. `acquireInflight()`
5. `progressPending()`

每读一个函数，只记录：

```text
输入状态
修改了什么
失败时返回什么
锁在哪里持有和释放
```

新认知点：**第 101 个 Group 的控制流实际落在哪些函数。**

---

### 第 63 步：再读 Group 结果生命周期

继续搜索：

```text
GroupState
completeGroup()
completePolledTask()
finishTaskLocked()
remaining
counted
group_id
```

对照第六阶段的状态图，确认：

- credit 何时归还；
- task 结果何时删除；
- handle 和 group_id 分别在哪里使用。

新认知点：**代码怎样实现“终态已发生，但结果仍保留”。**

---

### 第 64 步：再读 LocalCopyEngine 的释放顺序

打开：

```text
upstream/mooncake-transfer-engine/tent/src/transport/ascend/
local_copy_engine.cpp
```

只看 `submit()` 和 `finalize()`：

```text
创建 stream 和 flag
→ 异步提交
→ 检查完成
→ abort / destroy stream
→ free flag
```

新认知点：**为什么资源释放顺序属于正确性，而不只是清理风格。**

---

### 第 65 步：最后读测试怎样隔离每个性质

打开：

```text
upstream/mooncake-transfer-engine/tent/tests/
ascend_direct_transport_test.cpp
```

先按测试名阅读：

```text
Saturated
Quiesce
ReusedVendorHandle
Concurrent
FreeRejectsPending
FailureDoesNotPoison
FinalizeAbortsBeforeFreeingFlags
```

每个测试只回答：

1. 它控制了什么输入？
2. 它直接观察什么断言？
3. 它不能证明什么？

新认知点：**测试数量不是重点，隔离的性质才是重点。**

---

### 第 66 步：再看完整 API 测试工具

打开：

```text
benchmarks/tent_bench.py
ops/run_ranks.py
ops/summarize_transfers.py
```

分别寻找：

- `tent_bench.py`：怎样注册内存、提交独立 Batch、查询完成和校验 payload；
- `run_ranks.py`：怎样启动多进程并限制运行时间；
- `summarize_transfers.py`：怎样检查矩阵、拓扑和退出码。

新认知点：**单元状态机与完整网络数据路径如何分层验证。**

---

## 最终项目地图

现在你应该能看懂：

```text
应用 Request / Batch
        ↓
TransferEngineImpl
        ↓
AscendDirectTransport
   ┌────┴───────────┐
   ↓                ↓
HixlEngine     LocalCopyEngine
   ↓                ↓
HIXL Vendor      ACL stream
   └────┬───────────┘
        ↓
  progressPending()
        ↓
Group 进入终态
   ├── 归还 credit
   └── 保留 task 结果
            ↓
      调用者逐个消费
            ↓
        删除状态
```

项目得出的主要结论是：

1. 原版在固定回归中有 7 项失败，目标缺陷可复现。
2. 修复版 Debug、Release、ASan/UBSan 和 TSan 均为 34/34 通过。
3. CANN/HIXL 9.1 完整编译和链接成功，但设备操作仍由 mock 替换。
4. 两机 RDMA 完成 147,456 次传输和 51.1875 GiB 数据校验，验证测试工具与 RDMA 后端，不验证 Ascend 补丁性能。
5. 修复在注入 Vendor 的主机控制路径上增加约 5% 中位开销。
6. 真实 NPU DMA、跨机 HIXL 和真实设备性能对照仍未完成。

---

## 你现在应该怎样继续

不要马上打开全部源码。

先闭卷回答下面 12 个问题：

1. TENT 和具体 Transport 分别负责什么？
2. Request、Batch、Segment 的区别是什么？
3. 为什么内存必须注册？
4. 为什么 submit 成功不等于完成？
5. credit 限制什么？
6. 第 101 个 Group 为什么会卡住？
7. `progressPending()` 在什么时候运行？
8. 为什么 credit 回收和结果删除要分开？
9. 为什么 Vendor handle 不能作为永久结果 ID？
10. 为什么等待 credit 时不能持有 progress mutex？
11. RDMA 数据校验能证明什么，不能证明什么？
12. 哪一项关键 Ascend 验证仍然缺失？

能不看文档回答至少 10 个，再按第 60 到 66 步阅读代码。

## 原始证据入口

理解主线后，再按需打开：

- [入门版项目说明](REPORT-friendly.md)：项目结论和验证边界。
- [技术报告](REPORT.md)：实验设置、失败运行和证据链。
- [入门版源码指南](docs/source-guide-friendly.md)：更多代码阅读提示。
- [技术版源码指南](docs/source-guide.md)：函数、锁和状态细节。
- `results/cpu/summary.json`：原版与修复版回归。
- `results/cpu/roundtrip-release.json`：主机控制路径样本。
- `results/rdma16/summary.json`：两机 RDMA 机读汇总。
- `results/tcp-loopback/summary.json`：同机 TCP 机读汇总。
- `results/sdk-cann91/summary.json`：脱敏后的真实 SDK 构建边界。

第一次阅读本文时，不需要逐个打开这些证据文件。
