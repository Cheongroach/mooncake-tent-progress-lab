#!/usr/bin/env bash
set -euo pipefail
# PREFIX optionally points at unpacked system development packages.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD=${BUILD:-/tmp/mooncake-tent-build}
BACKEND=${BACKEND:-cpu}
extra=()
if [[ "$BACKEND" == ascend_direct ]]; then
    : "${ASCEND_HOME_PATH:?source the CANN environment first}"
    extra+=(-DUSE_ASCEND_DIRECT=ON)
fi
if [[ -n "${PREFIX:-}" ]]; then
    extra+=("-DCMAKE_PREFIX_PATH=$PREFIX")
    # The upstream fallback searches json/json.h, while distro packages install
    # it below include/jsoncpp; pkg-config may still point to system /usr.
    if [[ -f "$PREFIX/include/jsoncpp/json/json.h" ]]; then
        extra+=("-DJSONCPP_INCLUDE_DIR=$PREFIX/include/jsoncpp")
    fi
fi
if [[ -n "${YLT_SOURCE:-}" ]]; then
    extra+=("-DFETCHCONTENT_SOURCE_DIR_YALANTINGLIBS=$YLT_SOURCE")
fi
cmake -S "$ROOT/upstream" -B "$BUILD" \
    -DCMAKE_BUILD_TYPE=Release -DSKBUILD=ON \
    -DUSE_CUDA=OFF -DUSE_TENT=ON -DWITH_STORE=OFF -DWITH_STORE_RUST=OFF \
    -DWITH_RUST_EXAMPLE=OFF -DBUILD_UNIT_TESTS=ON \
    -DUSE_ETCD=OFF -DUSE_HTTP=OFF -DUSE_REDIS=OFF "${extra[@]}" "$@"
cmake --build "$BUILD" --target tent_shared -j "${JOBS:-8}"
if [[ "$BACKEND" == ascend_direct ]]; then
    cmake --build "$BUILD" --target tent_ascend_direct_transport_test -j "${JOBS:-8}"
fi
