# foxbridge-camoufox — CDP endpoint backed by a Camoufox anti-detect browser.
#
# Builds on the camofox-browser image, which already ships the complete
# Camoufox bundle at /root/.cache/camoufox/, and adds the foxbridge
# CDP -> Juggler proxy as the container entrypoint.
#
# Build:  docker build -t ghcr.io/lgwacker/foxbridge-camoufox:latest docker/
# Run:    docker run -d --name foxbridge --restart unless-stopped \
#             --network host ghcr.io/lgwacker/foxbridge-camoufox:latest
#         (foxbridge binds 127.0.0.1 only -> --network host is required
#          until upstream adds a --host flag)
#
# The foxbridge binary must exist in the build context (docker/foxbridge).
# Build it with scripts/build-image.sh or the CI workflow.

FROM camofox-browser:135.0.1-x86_64

COPY foxbridge /usr/local/bin/foxbridge
RUN chmod +x /usr/local/bin/foxbridge

ENTRYPOINT ["foxbridge", "--binary", "/root/.cache/camoufox/camoufox-bin", "--headless", "--port", "9222"]
