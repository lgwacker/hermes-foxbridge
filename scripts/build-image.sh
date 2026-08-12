#!/usr/bin/env bash
# Build the foxbridge-camoufox image locally (no CI needed).
#
# 1. Builds the foxbridge binary in a throwaway golang container (no Go
#    install on the host), 2. builds the image from docker/Dockerfile.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p dist
echo ">> building foxbridge binary (throwaway golang container)..."
docker run --rm -v "$PWD/dist:/out" golang:1.26 \
  sh -c "go install github.com/VulpineOS/foxbridge/cmd/foxbridge@latest && cp /go/bin/foxbridge /out/foxbridge"

echo ">> building image..."
cp dist/foxbridge docker/foxbridge
docker build -t ghcr.io/lgwacker/foxbridge-camoufox:latest docker/
rm -f docker/foxbridge

echo ">> done: ghcr.io/lgwacker/foxbridge-camoufox:latest"
