#!/usr/bin/env bash
# Build the foxbridge-camoufox image locally (no CI needed).
#
# 1. Builds the foxbridge binary in a throwaway golang container (no Go
#    install on the host), applying BOTH required patches in order:
#    - foxbridge-fetch-noop.patch            (must go FIRST)
#    - foxbridge-mainframe-context.patch
# 2. Builds the image from docker/Dockerfile.
#
# Both patches are MANDATORY — the plugin is broken without them:
# - fetch-noop:  Juggler/Camoufox never delivers interception events, so
#   Fetch.enable must be a no-op or navigation deadlocks silently.
# - mainframe:   context-less Runtime.evaluate drifts to ad iframes on
#   ad-heavy pages (OLX/Reddit) without it, failing with
#   "Failed to find execution context id-N".
set -euo pipefail
cd "$(dirname "$0")/.."

FOXBRIDGE_REF="${FOXBRIDGE_REF:-7dee166567d837ecfd0cce3664a6e03fc441e97b}"
mkdir -p dist
echo ">> cloning foxbridge @ ${FOXBRIDGE_REF} (throwaway)..."
rm -rf dist/foxbridge-src
git clone -q https://github.com/VulpineOS/foxbridge.git dist/foxbridge-src
git -C dist/foxbridge-src checkout -q "$FOXBRIDGE_REF"
echo ">> applying patch(es)..."
git -C dist/foxbridge-src apply "$PWD/patches/foxbridge-fetch-noop.patch"
git -C dist/foxbridge-src apply "$PWD/patches/foxbridge-mainframe-context.patch"

echo ">> building foxbridge binary (throwaway golang container)..."
docker run --rm -v "$PWD/dist/foxbridge-src:/src" -v "$PWD/dist:/out" golang:1.26 \
  sh -c "cd /src && go build -buildvcs=false -o /out/foxbridge ./cmd/foxbridge"

echo ">> building image..."
cp dist/foxbridge docker/foxbridge
cp assets/camou-config.json docker/camou-config.json
docker build -t ghcr.io/lgwacker/foxbridge-camoufox:latest docker/
rm -f docker/camou-config.json
rm -rf dist/foxbridge-src

# NOTE: docker/foxbridge is KEPT (and updated) on purpose — the CI image
# build uses the COMMITTED binary, so after a local rebuild you should
# commit the refreshed docker/foxbridge (see patches/README.md).
echo ">> done: ghcr.io/lgwacker/foxbridge-camoufox:latest"
echo ">> docker/foxbridge refreshed with both patches — commit it to update CI."
