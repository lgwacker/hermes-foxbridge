#!/usr/bin/env bash
# Build the foxbridge-camoufox image locally (no CI needed).
#
# 1. Builds the foxbridge binary in a throwaway golang container (no Go
#    install on the host), applying the local patch (main-frame execution
#    context — without it, context-less Runtime.evaluate drifts to ad
#    iframes and page_info()/js() report the wrong frame on ad-heavy pages
#    like OLX). 2. Builds the image from docker/Dockerfile.
set -euo pipefail
cd "$(dirname "$0")/.."

FOXBRIDGE_REF="${FOXBRIDGE_REF:-7dee166567d837ecfd0cce3664a6e03fc441e97b}"
mkdir -p dist
echo ">> cloning foxbridge @ ${FOXBRIDGE_REF} (throwaway)..."
rm -rf dist/foxbridge-src
git clone -q https://github.com/VulpineOS/foxbridge.git dist/foxbridge-src
git -C dist/foxbridge-src checkout -q "$FOXBRIDGE_REF"
echo ">> applying patch(es)..."
git -C dist/foxbridge-src apply "$PWD/patches/foxbridge-mainframe-context.patch"

echo ">> building foxbridge binary (throwaway golang container)..."
docker run --rm -v "$PWD/dist/foxbridge-src:/src" -v "$PWD/dist:/out" golang:1.26 \
  sh -c "cd /src && go build -buildvcs=false -o /out/foxbridge ./cmd/foxbridge"

echo ">> building image..."
cp dist/foxbridge docker/foxbridge
cp assets/camou-config.json docker/camou-config.json
docker build -t ghcr.io/lgwacker/foxbridge-camoufox:latest docker/
rm -f docker/foxbridge docker/camou-config.json
rm -rf dist/foxbridge-src

echo ">> done: ghcr.io/lgwacker/foxbridge-camoufox:latest"
