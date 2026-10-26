# [Bugfix] Progress pending TENT Ascend transfers during admission and quiesce

Local draft only. No issue or pull request has been published.

Patch base: `kvcache-ai/Mooncake@5d0636f5d39f9de729a58ea036f98c395dd0b291`.

## Problem

The Ascend Direct transport allows at most 100 in-flight transfer groups. A
caller may submit more than 100 independent groups before polling their status.
In the current implementation, the 101st submission waits for an in-flight
credit, while completed vendor groups release credits only when the caller
polls. Submission can therefore time out even though earlier transfers are
already terminal. Quiesce has the same dependency on external polling.

Vendor handles introduce a second lifecycle problem. A handle may be reused
after its transfer completes, while another task in the old group still has an
unconsumed result. Using that handle as the persistent result key allows the new
group to overwrite the old result.

The local-copy cleanup path also releases host completion flags before stopping
the ACL stream that may still write them.

## Change

- Progress pending groups while admission is saturated and during quiesce.
- Return the in-flight credit when a group becomes terminal, but retain each
  task result until the caller consumes it.
- Assign monotonically increasing group IDs so vendor-handle reuse cannot
  overwrite an older unconsumed result.
- Serialize vendor submission/publication, completion progress, and route
  teardown. Do not hold the progress mutex while waiting for a credit.
- Reject freeing a pending sub-batch.
- Abort and destroy local-copy streams before releasing DMA completion flags.

The change adds neither a public API nor a background progress thread.
In-flight credits bound pending and reserved groups; they do not bound terminal
application results that callers have not consumed.

## Scope

- [x] Transfer Engine (`mooncake-transfer-engine`)
- [x] Bug fix
- [x] P2: normal correctness fix

Three production files contain 202 additions and 142 deletions. The test file
contains 283 additions and 2 deletions. The production change remains below the
documented 500-line threshold, although maintainers may still request prior
design discussion.

## Evidence

### Differential regression

An isolated CPU harness compiles the real `AscendDirectTransport`,
`HixlEngine`, `LocalCopyEngine`, `SegmentManager`, and supporting sources. ACL,
HIXL, and unused RPC boundaries are replaced to make completion, failure, and
handle reuse deterministic.

- Original production source: 33 tests run, 7 fail. The additional concurrent
  saturation stress test is omitted to avoid repeated admission waits after the
  failure is established.
- Patched source: 34/34 pass.
- Concurrent saturation: 8 threads submit 2,048 groups; all groups are queried
  exactly once and the final in-flight count is zero.
- AddressSanitizer + UndefinedBehaviorSanitizer: 34/34 pass.
- ThreadSanitizer: 34/34 pass.
- PR-scoped pre-commit hooks, including changed-line formatting: pass.

This evidence isolates host-side state-machine and concurrency behavior. It
does not execute device DMA.

### CANN/HIXL compatibility

- GCC 12.2 compile-only checks against authentic CANN/HIXL 9.1 headers pass for
  four production sources and the regression test. No CPU SDK shim headers are
  included.
- The full TENT Ascend Direct library and upstream mock test target build with
  CANN/HIXL 9.1, GCC 11.4, and glibc 2.35.
- The built test target reports 34/34 passing, and `ldd` reports no unresolved
  dependencies for either target.

The test target still replaces ACL operations and the HIXL vendor. The HAL
resolves to the toolkit devlib, so these results establish SDK compilation and
linking, not NPU-driver or DMA execution. The build also emits GoogleTest header
warnings; it is not evidence of a clean test-framework header configuration.

### Cost

A Release microbenchmark executes 10,000
`allocate → submit → poll → free` rounds per sample, with 11 randomly
interleaved samples per variant:

- Original median: 1,251 ns per round.
- Patched median: 1,313 ns per round.
- Difference: +62 ns, approximately +5%.

This is host control-path overhead with an injected immediately completing
vendor. It is not HIXL device latency or end-to-end network latency.

### Test-harness integration

The full CPU TENT library builds, and a separate-process TCP integrity matrix
passes. A two-host, 16-process RDMA matrix also passes: 147,456 transfers,
51.1875 GiB, all selected payload ranges verified, and all ranks exiting
cleanly.

These runs validate the public-API test harness and the TCP/RDMA transports.
They do not execute the Ascend patch and are not evidence of an Ascend
performance improvement.

## Reproduction

The companion project contains:

- `tests/CMakeLists.txt`
- `ops/run_cpu_regression.sh`
- `ops/build_tent.sh`

The regression extensions themselves are included in this patch. On an Ascend
build, run `tent_ascend_direct_transport_test`.

Real Ascend/HIXL hardware validation remains outstanding.

## Checklist

- [ ] Human submitter has reviewed every changed line
- [x] Changed C/C++ line ranges are formatted with the upstream script
- [x] PR-scoped pre-commit hooks pass
- [x] Regression tests are included
- [ ] Real Ascend/HIXL integration is complete
- [ ] Overlapping upstream issues and pull requests have been checked immediately before submission

## AI Assistance Disclosure

- [x] AI tools were used

ChatGPT assisted with source analysis, implementation, regression tests, and
this draft. The human submitter must review and be able to defend every changed
line. Internal job identifiers, host metadata, and artifact locations must not
be copied into a public pull request.
