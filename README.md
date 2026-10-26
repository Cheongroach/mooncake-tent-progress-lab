# Mooncake TENT Progress Lab

这是一个针对 Mooncake TENT Ascend Direct 后端的异步进展与资源生命周期修复项目。

核心问题是：调用者连续提交超过 100 个 Vendor Group、尚未开始查询状态时，第 101 次提交可能等待 in-flight credit；即使前面的底层传输已经完成，旧实现也不会在提交路径主动推进完成和归还额度。

## 建议阅读顺序

第一次接触 Mooncake TENT：

1. [一步步认识这个项目](一步步认识Mooncake-TENT项目-friendly.md)
2. [入门版项目说明](REPORT-friendly.md)
3. [入门版源码指南](docs/source-guide-friendly.md)

准备审查实现：

1. [技术报告](REPORT.md)
2. [技术源码指南](docs/source-guide.md)
3. [PR 草稿](docs/pr-draft.md)
4. [可应用补丁](patches/0001-ascend-cooperative-progress.patch)

## 已完成的验证

| 证据 | 结果 | 结论边界 |
|---|---:|---|
| 原版生产代码 + 扩展回归 | 33 项中 7 项失败 | 目标缺陷可复现 |
| 修复版 Debug/Release | 34/34 通过 | 主机状态机回归通过 |
| ASan/UBSan、TSan | 均为 34/34 通过 | 已执行的主机内存和并发路径通过 |
| CANN/HIXL 9.1 完整构建 | 34/34 mock 测试通过 | 真实 SDK 编译和链接成立，不代表设备执行 |
| 两机 16 进程 RDMA | 147,456 次、51.1875 GiB 全部校验 | 验证公共测试工具和 RDMA 后端，不验证 Ascend 性能 |
| 主机控制路径微基准 | 1,251 ns → 1,313 ns | 注入 Vendor 的中位成本约增加 5% |

真实 NPU DMA、跨机 HIXL、驱动异常下的 stream 终止和原版/修复版设备性能对照仍未完成。补丁尚未提交或合并 Mooncake 上游。

## 获取固定上游并应用补丁

```bash
bash scripts/fetch_mooncake.sh
```

脚本会下载并校验 `kvcache-ai/Mooncake` 的固定源码 archive：

```text
5d0636f5d39f9de729a58ea036f98c395dd0b291
```

然后执行：

```bash
git apply --check ../patches/0001-ascend-cooperative-progress.patch
git apply ../patches/0001-ascend-cooperative-progress.patch
```

## 运行 CPU 差分回归

环境需要 GCC 12、CMake、GoogleTest、glog 和 libnuma 开发包。

修复版：

```bash
bash ops/run_cpu_regression.sh
```

Sanitizer：

```bash
SANITIZER=address,undefined BUILD=/tmp/mooncake-check-asan \
  bash ops/run_cpu_regression.sh

SANITIZER=thread BUILD=/tmp/mooncake-check-tsan \
  bash ops/run_cpu_regression.sh
```

原版对照需要另一份同 commit 的干净源码：

```bash
MOONCAKE_SOURCE=/path/to/pristine-mooncake \
BUILD=/tmp/mooncake-before \
bash ops/run_cpu_regression.sh \
  --gtest_filter=-AscendDirectTransportTest.ConcurrentSaturatedSubmitters
```

预期原版有 7 项失败。该命令不是要求全部通过的 CI。

## 目录

| 路径 | 内容 |
|---|---|
| `patches/` | 针对固定 Mooncake commit 的补丁 |
| `tests/` | 编译真实生产源码的 CPU harness 和 SDK 边界替身 |
| `benchmarks/` | 通过完整 TENT C ABI 发起 READ/WRITE |
| `ops/` | 构建、运行、汇总和绘图入口 |
| `results/` | 脱敏后的机读结果与图 |
| `docs/` | 技术源码指南和 PR 草稿 |

## 许可证与上游

本仓库中的 Mooncake 派生补丁遵循 Apache License 2.0。上游项目：

- <https://github.com/kvcache-ai/Mooncake>
- 固定基线：`5d0636f5d39f9de729a58ea036f98c395dd0b291`

详见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
