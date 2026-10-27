# 对话与决策记录

本文不是原始聊天转录。它只保留能够解释项目方向、证据边界和后续选择的公开对话摘要。

## 对话主线

### 1. 这个项目是什么

问题：Mooncake TENT 是什么，为什么值得单独做一个项目？

结论：TENT 是应用与 RDMA、TCP、共享内存、Ascend Direct 等传输后端之间的数据搬运层。项目不修改模型计算，只研究已经生成的 Tensor/KV Cache 怎样异步移动、完成和回收资源。

形成的文档：

- [一步步认识 Mooncake TENT](../一步步认识Mooncake-TENT项目-friendly.md)
- [入门版项目说明](../REPORT-friendly.md)

### 2. 为什么第一次解释仍然难懂

问题：概念、源码和实验数字同时出现，读者无法建立顺序。

决策：参照逐步学习材料，把内容改成“每一步只增加一个概念”，代码路径放到最后。已有 embedding、Attention、Prefill/Decode 基础不再重复。

结果：主学习文档分为 10 个阶段、66 个步骤，从“一块已经算好的 Tensor 为什么还要搬”开始。

### 3. 这是不是一个已知上游 Bug

问题：补丁是否命中真实缺陷，是否已经有 Issue 编号或重复 PR？

结论：

- 缺陷可以在原版上稳定复现；
- 上游最新目标文件仍保留原逻辑；
- 没有检索到完全对应的公开 Issue/PR；
- [Mooncake#2116](https://github.com/kvcache-ai/Mooncake/issues/2116) 和
  [Mooncake#2277](https://github.com/kvcache-ai/Mooncake/pull/2277)
  记录了更广泛的 progress/lifecycle 设计；
- [Mooncake#2277](https://github.com/kvcache-ai/Mooncake/pull/2277)
  明确把 `ascend_direct` 留作 query-completion follow-up；
- 不应把相关记录冒充成本修复的正式 Bug 编号。

详细证据见[上游缺陷核查](upstream-bug-audit.md)。

### 4. 是否应该直接提交上游

问题：已有差分回归和 SDK 构建，是否等于 ready-to-merge？

结论：这是正式 Bugfix candidate，但还不是 ready-to-merge。

原因：

- 需要在提交当天的完整 `main` 上重新编译；
- 当前普通 TENT CI 不启用 Ascend Direct，Ascend E2E 又不构建该单元测试；
- 人类提交者尚需逐行审查；
- 真实 NPU DMA/HIXL 仍未完成；
- 应先创建独立 Issue 获得正式编号。

### 5. 为什么建立独立公开仓库

问题：如何公开学习材料和补丁，同时避免上传内部平台信息？

决策：

- 只发布教学文档、补丁、通用测试代码和脱敏汇总；
- 不发布内部任务 ID、HDFS 路径、镜像 ID、集群配置、原始平台日志；
- 固定 Mooncake commit 和补丁 SHA-256；
- 通过公开仓库保存可复核证据。

公开仓库：<https://github.com/Cheongroach/mooncake-tent-progress-lab>

## 决策表

| 决策 | 原因 | 后果 |
|---|---|---|
| 选择 TENT Ascend Direct，不扩建旧 HCCL 后端 | 当前 Mooncake 主线已经迁移到 TENT | 贡献与上游当前架构一致 |
| 不增加后台 progress thread | 保持补丁范围小，不改变公开 API | 采用 admission/quiesce cooperative progress |
| Group 终态与 task 结果消费分离 | credit 和应用结果属于不同生命周期 | 完成即归还 credit，结果保留到消费 |
| 使用 `group_id`，不把 Vendor handle 当永久 ID | Vendor handle 可以复用 | 防止新旧结果覆盖 |
| mock 与设备验证分层描述 | mock 可控但不执行真实 DMA | 不把 34/34 写成 NPU 验证 |
| RDMA 结果不计入 Ascend 性能收益 | RDMA 运行未执行 Ascend 补丁 | 只用来验证公共测试工具 |
| 不虚构 Bug 编号 | 上游没有对应独立 Issue | 先提交 Issue，再用 `Fixes #NNNN` |
| 暂不直接提交上游 PR | 最新完整构建、人审和设备证据仍需补齐 | 按实施清单逐项过 gate |

## 已确认事实

- 原版扩展回归：33 项中 7 项失败。
- 修复版 Debug、Release、ASan/UBSan、TSan：34/34。
- 8 线程、2,048 个 Group 后在途额度为 0。
- CANN/HIXL 9.1 完整 SDK 编译和 mock 测试通过。
- 两机 RDMA：147,456 次传输、51.1875 GiB 全部校验。
- 主机控制路径中位数：1,251 ns 增至 1,313 ns。
- 上游目标文件在核查快照中仍与未修复基线一致。

## 尚未回答

- 真实 NPU 驱动收到 stream abort 后，DMA 是否已经停止？
- 跨机 HIXL 的完成、断连和 handle 回收语义是否符合 mock？
- 多设备高并发下，`progress_mutex_` 是否限制吞吐？
- 维护者希望把 LocalCopy flag 顺序修复保留在同一 PR，还是拆成独立 PR？
- 维护者是否要求为 TENT Ascend Direct 增加常驻 CI lane？

这些问题不能由现有 CPU 回归、SDK 构建或 RDMA 实验替代回答。
