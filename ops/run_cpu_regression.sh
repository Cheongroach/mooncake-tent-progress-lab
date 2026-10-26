#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD=${BUILD:-/tmp/mooncake-regression-build}
extra=()
if [[ -n "${PREFIX:-}" ]]; then extra+=("-DCMAKE_PREFIX_PATH=$PREFIX"); fi
if [[ -n "${MOONCAKE_SOURCE:-}" ]]; then extra+=("-DMOONCAKE_SOURCE=$MOONCAKE_SOURCE"); fi
if [[ -n "${SANITIZER:-}" ]]; then extra+=("-DSANITIZER=$SANITIZER"); fi
cmake -S "$ROOT/tests" -B "$BUILD" -DCMAKE_BUILD_TYPE=RelWithDebInfo "${extra[@]}"
cmake --build "$BUILD" -j "${JOBS:-6}"
# If libraries came from PREFIX, also include its lib directory in LD_LIBRARY_PATH.
"$BUILD/ascend_regression" --gtest_output="xml:$BUILD/results.xml" "$@"
