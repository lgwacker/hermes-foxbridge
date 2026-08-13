#!/usr/bin/env bash
# Build the foxbridge-camoufox image locally (no CI needed).
#
# 1. Builds the foxbridge binary from the maintained fork
#    (https://github.com/lgwacker/foxbridge) at the pinned FOXBRIDGE_REF —
#    the fork's main carries all THREE mandatory fixes as commits:
#    - Fetch.enable no-op            (Juggler never delivers interception
#                                     events; without it navigation deadlocks)
#    - main-frame context            (context-less Runtime.evaluate drifts to
#                                     ad iframes on ad-heavy pages without it)
#    - --host flag                   (bridge networking + -p instead of
#                                     --network host)
#    No patch step: the fork IS the patched source. Bump FOXBRIDGE_REF to a
#    new fork commit to change the binary.
# 2. Builds the image from docker/Dockerfile.
set -euo pipefail
cd "$(dirname "$0")/.."

FOXBRIDGE_REPO="${FOXBRIDGE_REPO:-https://github.com/lgwacker/foxbridge.git}"
FOXBRIDGE_REF="${FOXBRIDGE_REF:-c1f51a847d11dc8b5f530a7cc922ef39ce7069e4}"
mkdir -p dist
echo ">> cloning foxbridge fork @ ${FOXBRIDGE_REF} (throwaway)..."
rm -rf dist/foxbridge-src
git clone -q "$FOXBRIDGE_REPO" dist/foxbridge-src
git -C dist/foxbridge-src checkout -q "$FOXBRIDGE_REF"
# (no patch step — fork main already carries the three fixes as commits)

echo ">> building foxbridge binary (throwaway golang container)..."
docker run --rm -v "$PWD/dist/foxbridge-src:/src" -v "$PWD/dist:/out" golang:1.26 \
  sh -c "cd /src && go build -buildvcs=false -o /out/foxbridge ./cmd/foxbridge"

echo ">> building image..."
cp dist/foxbridge docker/foxbridge
docker build -t ghcr.io/lgwacker/foxbridge-camoufox:latest docker/

# docker/foxbridge is gitignored — it exists only as the image build context.
echo ">> done: ghcr.io/lgwacker/foxbridge-camoufox:latest"
