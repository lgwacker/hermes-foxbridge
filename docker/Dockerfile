# foxbridge-camoufox — CDP endpoint backed by a Camoufox anti-detect browser.
#
# Builds on the camofox-browser image, which already ships the complete
# Camoufox bundle (/root/.cache/camoufox/) AND the camoufox-js wrapper
# (/app/node_modules/camoufox-js). We reuse camoufox-js' launchOptions()
# — the SAME function the camofox server calls (server.js) — to generate a
# fresh BrowserForge fingerprint (CAMOU_CONFIG_1..N with random audio/canvas
# seeds), the firefoxUserPrefs, and the UBO addon path at every boot.
# Validated 2026-08-12: OLX Brazil loads on the FIRST navigation with this
# config (raw headless => NS_ERROR_REDIRECT_LOOP; static config => ad-sync
# hijack; dynamic bootstrap => clean).
#
# Build:  docker build -t ghcr.io/lgwacker/foxbridge-camoufox:latest docker/
# Run:    docker run -d --name foxbridge --restart unless-stopped \
#             -p 127.0.0.1:9222:9222 -p 127.0.0.1:6081:6081 \
#             -v ~/.hermes/foxbridge-profiles:/profile \
#             ghcr.io/lgwacker/foxbridge-camoufox:latest
#         (the provider publishes these ports itself; the local --host
#          patch makes foxbridge bind 0.0.0.0 inside the container so
#          docker-proxy can reach it over the bridge network)
#
# The foxbridge binary must exist in the build context (docker/foxbridge).
# Build it with scripts/build-image.sh or the CI workflow.

FROM ghcr.io/lgwacker/camofox-browser-base:135.0.1-x86_64

COPY foxbridge /usr/local/bin/foxbridge
RUN chmod +x /usr/local/bin/foxbridge

# Bootstrap: generates the launch config via camoufox-js launchOptions()
# (fingerprint, prefs, UBO path) and writes /etc/foxbridge-env +
# /profile/user.js. Same mechanism the camofox server uses.
COPY bootstrap.mjs /usr/local/bin/foxbridge-bootstrap.mjs

# Pre-fetch the uBlock Origin addon at build time (camoufox-js
# addDefaultAddons -> /root/.cache/camoufox/addons/UBO). The bootstrap
# skips the download if already present, so this keeps boots offline-fast.
WORKDIR /app
RUN node -e "const m=require('camoufox-js/dist/addons.js'); m.addDefaultAddons([], undefined).then(()=>{ console.log('addons:', require('fs').readdirSync('/root/.cache/camoufox/addons')); }).catch(e=>{ console.error(e); process.exit(1); })"

# Headed via Xvfb + persistent profile + dynamic fingerprint.
# The X lock cleanup matters: a docker stop that kills Xvfb leaves
# /tmp/.X0-lock behind and the next boot crashes the Camoufox
# ("failed to enable Browser domain: client closed").
ENTRYPOINT ["sh", "-c"]
CMD ["rm -f /tmp/.X0-lock /tmp/.X11-unix/X0; rm -f /profile/sessionstore.jsonlz4 /profile/sessionstore-backups/recovery.jsonlz4 /profile/sessionstore-backups/recovery.baklz4; mkdir -p /profile; node /usr/local/bin/foxbridge-bootstrap.mjs; . /etc/foxbridge-env; Xvfb :0 -screen 0 2560x1440x24 >/dev/null 2>&1 & sleep 1; export DISPLAY=:0; [ \"${FOXBRIDGE_VNC:-0}\" != \"0\" ] && { VNC_PORT=\"${VNC_PORT:-5901}\" NOVNC_PORT=\"${NOVNC_PORT:-6081}\" VNC_BIND=\"${VNC_BIND:-127.0.0.1}\" VNC_RESOLUTION=2560x1440x24 sh /app/plugins/vnc/vnc-watcher.sh >/tmp/camofox-vnc-watcher.log 2>&1 & }; exec foxbridge --host 0.0.0.0 --binary \"$(cat /etc/foxbridge-execpath)\" --profile /profile --port \"${FOXBRIDGE_CDP_PORT:-9222}\""]
