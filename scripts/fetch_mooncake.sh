#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UPSTREAM="$ROOT/upstream"
COMMIT=5d0636f5d39f9de729a58ea036f98c395dd0b291
ARCHIVE_SHA256=688758d50daae1c7db835ae49e9b9b3da08f18e5d2c8634f1de0c0418962d4e1
PATCH="$ROOT/patches/0001-ascend-cooperative-progress.patch"

if [[ -e "$UPSTREAM" ]]; then
    echo "Refusing to replace existing path: $UPSTREAM" >&2
    exit 1
fi

TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT
ARCHIVE="$TEMP/mooncake.tar.gz"

curl --fail --location --retry 3 \
    "https://github.com/kvcache-ai/Mooncake/archive/$COMMIT.tar.gz" \
    --output "$ARCHIVE"
echo "$ARCHIVE_SHA256  $ARCHIVE" | sha256sum --check -

mkdir "$UPSTREAM"
tar -xzf "$ARCHIVE" --strip-components=1 -C "$UPSTREAM"
(
    cd "$UPSTREAM"
    git apply --check "$PATCH"
    git apply "$PATCH"
)

echo "Prepared patched Mooncake source at $UPSTREAM"
