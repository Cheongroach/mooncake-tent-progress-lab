# 后续实施与上游提交清单

目标：把当前补丁从“有证据的候选修复”推进到“可以由 Mooncake 维护者审核和合并的 PR”。

每个阶段都有明确 gate。前一阶段未通过时，不进入下一阶段。

## 当前状态

| 项目 | 状态 |
|---|---|
| 缺陷复现 | 已完成 |
| 修复版 CPU 回归 | 已完成 |
| ASan/UBSan、TSan | 已完成 |
| CANN/HIXL 9.1 编译和链接 | 已完成 |
| 当前目标文件 patch check | 已完成 |
| 最新完整 `main` 编译 | 待执行 |
| 真实 NPU/HIXL | 待执行 |
| 人类逐行审查 | 待执行 |
| 上游 Issue | 待创建 |
| 上游 PR | 待创建 |

## 阶段 1：创建上游 Issue

使用[提交手稿](upstream-submission-manuscript.md)中的 Issue draft。

提交前：

1. 获取最新 `main` SHA。
2. 重新搜索 Issue 和 PR：

```text
ascend hixl progress pending inflight in-flight
quiesce credit handle lifecycle saturation 100 101st
```

3. 在 Issue 中列出
   [#3919](https://github.com/kvcache-ai/Mooncake/issues/3919)、
   [#3933](https://github.com/kvcache-ai/Mooncake/pull/3933)、
   [#2116](https://github.com/kvcache-ai/Mooncake/issues/2116)、
   [#2277](https://github.com/kvcache-ai/Mooncake/pull/2277)、
   [#3116](https://github.com/kvcache-ai/Mooncake/pull/3116)、
   [#2505](https://github.com/kvcache-ai/Mooncake/pull/2505) 和
   [#4247](https://github.com/kvcache-ai/Mooncake/issues/4247)，并说明为何不重复。
4. 只提交公开、脱敏的复现。

Gate：

- Issue 获得正式 `#NNNN`；
- 没有维护者指出已有重复实现；
- 若维护者要求拆分 scope，先调整补丁再继续。

## 阶段 2：同步最新主干

```bash
git fetch upstream main
git checkout -b bugfix/tent-ascend-cooperative-progress upstream/main
git apply /path/to/0001-ascend-cooperative-progress.patch
git diff --check
```

核对：

- 只修改 3 个生产文件和 1 个测试文件；
- 没有格式化无关代码；
- 没有内部平台信息；
- `git apply --check` 在最新主干成立。

Gate：

- 补丁无冲突；
- 生产代码仍低于 500 LOC；
- `git diff` 与 Issue 描述一致。

## 阶段 3：当前主干 CPU 与 SDK 验证

### 构建和单测

```bash
cmake -S . -B build-ascend \
  -DUSE_TENT=ON \
  -DUSE_ASCEND_DIRECT=ON \
  -DBUILD_UNIT_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Debug

cmake --build build-ascend \
  --target tent_ascend_direct_transport_test -j

ctest --test-dir build-ascend \
  -R tent_ascend_direct_transport_test \
  --output-on-failure
```

必须保留：

- 编译器、CANN 和 HIXL 版本；
- 完整测试数量；
- 退出码；
- 失败测试名称；
- 被测试 commit SHA。

### 差分检查

在同一最新 commit 的原版上运行扩展测试：

- 原版应稳定出现预期失败；
- 修复版应全部通过；
- 不使用测试总时间计算性能提升。

### Sanitizer

运行：

- ASan + UBSan；
- TSan；
- Release 正常路径微基准。

Gate：

- 修复版 34/34；
- sanitizer 无报错；
- 原版红测与修复版绿测对应；
- SDK 动态依赖无 `not found`。

## 阶段 4：真实 Ascend 验证

最低矩阵：

| 场景 | 要验证的性质 |
|---|---|
| 远端 HIXL，窗口 128 | 第 101 个 Group 能继续提交 |
| 同引擎 LocalCopy，窗口 128 | 本地路径同样保持 progress |
| 多 remote | 一个 route 失败不污染其他 route |
| quiesce，无消费者 poll | 排空能主动推进 |
| Vendor handle 复用 | 新旧 task 结果不串线 |
| pending free | 释放被拒绝，终态后可回收 |
| stream abort/finalize | host flag 不在 DMA 停止前释放 |

记录 correctness，不急于做性能结论：

- 每次传输的完成状态和字节数；
- payload 校验；
- rank 退出码；
- route/stream 错误；
- quiesce 时间；
- 最终 in-flight 数。

Gate：

- 原版与修复版使用相同输入序列；
- 修复版所有指定范围校验通过；
- 没有把 H20/RDMA 结果当成 NPU 结果；
- 不能完成的设备实验在 PR 中明确列为限制。

## 阶段 5：代码规范

```bash
./scripts/code_format.sh --changed-lines --check -b upstream/main

pre-commit run --files \
  $(git diff --name-only --diff-filter=ACMR upstream/main...HEAD)
```

适用检查：

- clang-format 20；
- trailing whitespace / EOF；
- codespell；
- merge-conflict marker；
- added-large-files；
- CMake 格式检查（如果修改 CMake）。

不要为本 PR 运行并提交全仓格式化结果。

Gate：

- PR 范围所有 hook 通过；
- hook 自动修改内容经过人工复核并重新暂存；
- 无 `--no-verify`。

## 阶段 6：文档

建议更新：

1. `docs/source/api-reference/cpp/tent.md`
   - pending Batch 调用 `freeBatch()` 返回 non-OK；
   - 调用者必须继续 poll 到终态。
2. `docs/source/design/tent/testing.md`
   - 增加 query-completion Transport 的 cooperative-progress 回归边界。
3. 必要时增加 Ascend TENT 的 progress 说明
   - 无后台线程；
   - admission/quiesce 主动推进；
   - terminal result retention 与 credit 分离。

验证：

```bash
cd docs
make clean
make html
```

Gate：

- Sphinx 构建通过；
- 链接和代码引用有效；
- 没有新增非标准 CHANGELOG。

## 阶段 7：PR

使用：

- [Issue/PR 提交手稿](upstream-submission-manuscript.md)
- [完整 PR 草稿](pr-draft.md)

PR 选择：

```text
Priority: P2
Module: Transfer Engine
Type: Bug fix
```

PR body 必须包含：

- `Fixes #NNNN`；
- 最小复现；
- 原版失败和修复版通过；
- sanitizer 和 SDK 结果；
- 真实 NPU 结果或明确的未执行说明；
- 与 [#2116](https://github.com/kvcache-ai/Mooncake/issues/2116) /
  [#2277](https://github.com/kvcache-ai/Mooncake/pull/2277) 的差异；
- +5% 主机控制路径成本；
- AI assistance disclosure。

Gate：

- 人类提交者逐行审查；
- 所有测试结果对应 PR head SHA；
- 没有内部任务 ID、路径、主机或镜像信息。

## 阶段 8：审核与合并

当前 `main` 规则：

- 至少 1 个批准；
- 必须有 TENT CODEOWNER 批准；
- squash merge；
- 禁止 non-fast-forward。

建议主动请求 TENT CODEOWNER 审核：

```text
@alogfans @doujiang24 @chestnut-Q @staryxchen @00fish0 @dtcccc
```

CI：

- `CI Gate` 应通过；
- `run-ci` 可重跑同一 SHA；
- 请求维护者添加 `run-e2e-ci`，触发 Ascend 与集成检查；
- 当前 CI 未直接运行 `tent_ascend_direct_transport_test` 的 Ascend 配置，因此本地日志仍需附在 PR。

合并前：

- 处理全部实质性 review comment；
- push 后确认测试对应最新 SHA；
- 若设计改变，重新运行受影响测试；
- 最终通过 squash merge。

## Definition of Done

只有全部满足，才能写“已提交并完成上游验证”：

- [ ] 独立 Issue 已创建并获得编号
- [ ] overlap search 已在提交当天重做
- [ ] 最新 `main` 完整构建通过
- [ ] 34/34 回归通过
- [ ] ASan/UBSan、TSan 通过
- [ ] PR-scoped pre-commit 通过
- [ ] 文档构建通过
- [ ] 人类逐行审查完成
- [ ] PR 已创建并链接 Issue
- [ ] CODEOWNER 批准
- [ ] CI Gate 通过
- [ ] squash merge 完成

真实 NPU 验证如果因资源限制未完成，不能勾选为完成，也不能在简历中写成已验证。
