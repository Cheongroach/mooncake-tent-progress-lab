# Mooncake 上游提交手稿

本文包含可以复制到 GitHub 的 Issue 初稿，以及正式 PR 提交前需要补入现有 [PR 草稿](pr-draft.md) 的内容。

这不是已经获准提交的 Issue 或 PR。`#NNNN`、最新 SHA 和最终测试结果必须在提交当天替换。

## Issue draft

### Title

```text
[Bug][TENT] Ascend admission stalls after 100 groups without consumer polling
```

### Body

```markdown
## Bug report

### Summary

TENT `AscendDirectTransport` admits at most 100 in-flight vendor groups.
When a caller submits more than 100 independent groups before polling, the
101st submission waits for an admission credit. Credits are currently returned
only after `getTransferStatus()` observes a terminal vendor state.

If the same caller intends to submit the whole window before polling, it cannot
reach `getTransferStatus()` while the 101st submit is blocked. The vendor may
already have completed earlier groups, but software does not progress them.
`quiesce()` has the same dependency on external polling.

### Reproduction

1. Install TENT Ascend Direct with a completion-capable fake HIXL vendor.
2. Submit 100 independent groups without calling `getTransferStatus()`.
3. Make the vendor report those groups as completed.
4. Submit group 101 on the same caller thread.

Observed on unmodified production:

- group 101 waits until the admission deadline and fails;
- `quiesce()` can wait until its deadline without consumer polling;
- completed groups retain in-flight credits until individual task results are
  consumed.

Expected:

- admission and quiesce should cooperatively progress pending groups;
- a terminal group should release its credit independently of result
  consumption;
- task results should remain available until consumed.

### Current-main evidence

Audited main: `MAIN_SHA`.

The four target files are byte-identical to the original audited base, and the
candidate patch applies with `git apply --check`.

Differential regression:

- unmodified production + extended tests: 33 run, 7 fail;
- candidate Debug/Release: 34/34 pass;
- ASan+UBSan: 34/34 pass;
- TSan: 34/34 pass;
- concurrent case: 8 threads, 2,048 groups, final in-flight count 0.

The CANN/HIXL 9.1 library and upstream mock test target also build and link.
Device operations remain mocked; this is not real NPU DMA validation.

### Scope

Affected:

- remote HIXL groups;
- same-engine local-copy groups;
- admission windows above 100 independent groups;
- quiesce without consumer polling.

Requests coalesced into no more than 100 groups may not trigger the issue.

### Related work

- #3919 / #3933 introduced the current async AutoConnect-only TENT Ascend path.
- #2116 discusses explicit progress ownership.
- #2277 wires terminal events for RDMA/TCP/SHM/bufio and explicitly leaves
  query-completion transports, including `ascend_direct`, out of scope.
- #3116 and #2505 fix different Ascend result-lifecycle/polling defects.
- #4247 concerns quiescence in the classic synchronous Ascend path, not TENT.

This report is narrower than #2116/#2277: it adds cooperative progress inside
the Ascend transport during saturated admission and quiesce, without adding a
background progress thread.
```

## PR title

```text
[Bugfix][TENT] Progress Ascend pending groups during admission and quiesce
```

## PR description additions

Add the following immediately after `## Problem` in [pr-draft.md](pr-draft.md):

```markdown
Fixes #NNNN.

Related work:

- #3919 / #3933 established the async AutoConnect-only TENT Ascend backend.
- #2116 discusses separating observation from explicit progress.
- #2277 integrates terminal events for other transports and explicitly leaves
  `ascend_direct` out of scope because completion is query-driven.
- #3116 and #2505 are adjacent Ascend lifecycle fixes, not duplicates.

This PR is a transport-local cooperative-progress fix. It does not add the
runtime background worker proposed in #2277.
```

Update the evidence section with the submission-day results:

```markdown
Current-main SHA: `MAIN_SHA`.

- `git apply --check`: pass.
- `tent_ascend_direct_transport_test`: 34/34.
- Debug / Release: pass.
- ASan+UBSan / TSan: pass.
- PR-scoped pre-commit: pass.
- Real NPU validation: RESULT OR "not run".
```

## PR template selections

```text
Suggested Merge Priority: P2
Module: Transfer Engine
Type of Change: Bug fix
```

The production change is below 500 LOC, so the current contribution guide does not require an RFC.

## Commands to record in the PR

```bash
git fetch upstream main
./scripts/code_format.sh --changed-lines --check -b upstream/main

pre-commit run --files \
  $(git diff --name-only --diff-filter=ACMR upstream/main...HEAD)

cmake -S . -B build-ascend \
  -DUSE_TENT=ON \
  -DUSE_ASCEND_DIRECT=ON \
  -DBUILD_UNIT_TESTS=ON

cmake --build build-ascend \
  --target tent_ascend_direct_transport_test -j

ctest --test-dir build-ascend \
  -R tent_ascend_direct_transport_test \
  --output-on-failure
```

## Before publication

- Replace `#NNNN` with the new Issue number.
- Replace `MAIN_SHA` with the tested upstream commit.
- Repeat overlap search for `ascend`, `hixl`, `progress`, `quiesce`, `inflight`, `credit`, `handle reuse`, and saturation.
- Confirm all target files still accept the patch.
- Remove internal job IDs, hostnames, image IDs and artifact paths.
- Review every changed line manually.
- State real hardware coverage exactly; do not imply NPU validation from SDK build or RDMA results.
- Complete the AI assistance disclosure.

