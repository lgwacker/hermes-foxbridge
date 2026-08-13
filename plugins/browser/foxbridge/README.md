# Foxbridge browser provider (Hermes plugin)

Registers the `foxbridge` browser backend with Hermes: a Docker sidecar
running **foxbridge (CDP→Juggler bridge) + Camoufox (anti-detect Firefox)**,
exposed as `browser.cloud_provider: foxbridge`.

Serves both browser surfaces:
- `browser_exec` (browser-use CLI harness) — Hermes injects this provider's
  CDP endpoint as `BU_CDP_URL`;
- built-in tools (`browser_navigate`, `browser_click`, ...) — the legacy
  stack attaches to the same CDP endpoint.

Lifecycle: the sidecar starts on first use and is stopped after
`FOXBRIDGE_IDLE_TIMEOUT_S` (default 900 s) of inactivity — same lazy
start / idle-stop pattern as the camofox integration.

> ℹ️ **Idle timeout floor (0.2.0):** the core passes its own
> `browser.inactivity_timeout` (default 120 s) into every provider as
> `idle_timeout_s`. The plugin treats its documented value as a **floor** —
> the effective idle timeout is `max(core_arg, FOXBRIDGE_IDLE_TIMEOUT_S)`,
> so a short core value can never idle-stop the sidecar out from under an
> open session (the "dead-sidecar session trap", fixed 2026-08-13). The
> effective value is logged at provider construction.

**Self-healing (0.2.0):** if the sidecar exits with a non-zero code (a real
crash — not the clean exit-0 idle-stop) while a session is open, the idle
watcher restarts it automatically within ~30 s; the core's supervisor
reconnects as soon as the CDP endpoint answers. If a `docker start`/`restart`
comes back but fails the health check (e.g. the entrypoint's Xvfb lock after
`docker stop`), the provider drops and recreates the container once before
giving up. Health checks also verify the endpoint identity — a foreign
service on the port (cron-mode Chrome on 9222) is never declared healthy.

> 🔒 **Ownership guard (0.2.1):** several long-lived Hermes processes
> (desktop backend, gateway, leftover CLI sessions) can each run a provider
> with its own idle watcher on the SAME container name. The watcher
> captures the sidecar's `StartedAt` at every session start and only
> idle-stops while it still matches — a container started/restarted by
> another instance (or a manual `docker start`) is never stopped by a
> stale watcher. Symptom this fixes: the sidecar dying with a clean
> exit-0 `shutting down...` 7-15 s after every boot while a
> `browser_exec` is in flight ("no close frame received").

## Install

```bash
hermes plugins install lgwacker/hermes-foxbridge/plugins/browser/foxbridge --enable
hermes config set browser.cloud_provider foxbridge
```

That's it — **everything else is automatic**: the provider pulls the
published image `ghcr.io/lgwacker/foxbridge-camoufox:latest` (which
contains the patched foxbridge binary + Camoufox + dynamic bootstrap) and
manages the sidecar lifecycle for you.

**Requirements:**

| Requirement | Why | Check |
|---|---|---|
| Docker (running) | The provider runs the browser as a sidecar container | `docker ps` |
| browser-use CLI | Needed for `browser_exec` (harness); install via `hermes tools` → Browser Automation | `browser-use --version` |
| No `CAMOFOX_URL` in `~/.hermes/.env` | The legacy camofox backend takes precedence over any `browser.cloud_provider` (Hermes design) | `grep CAMOFOX_URL ~/.hermes/.env` |

> ⚠️ After installing, **start a new session** (`/reset` or new chat) —
> `browser.cloud_provider` is read once per process and cached.

## What the image contains (why it just works)

The image is built by the repo's `docker-image` CI workflow from
`docker/Dockerfile` + `docker/bootstrap.mjs`. The foxbridge binary inside
carries **three mandatory patches** (applied to upstream foxbridge
`7dee166`, see [`patches/README.md`](../../../patches/README.md) for the
full write-up):

1. **Fetch.enable no-op** — Juggler/Camoufox never delivers interception
   events (`NetworkObserver` → `PageHandler` broken in Camoufox 135), so
   activating interception deadlocks every navigation. The no-op keeps
   pages loading. (Upstream: [issue #5](https://github.com/VulpineOS/foxbridge/issues/5), [PR #6](https://github.com/VulpineOS/foxbridge/pull/6).)
2. **Main-frame context tracking** — without it, context-less
   `Runtime.evaluate` drifts to ad iframes on ad-heavy pages (OLX, Reddit)
   → `-32000 Failed to find execution context id-N` and wrong page state.
3. **`--host` bind flag** — without it the CDP server only binds
   container loopback, so the sidecar had to run with `--network host`;
   with it the sidecar uses bridge networking + loopback-only `-p`
   mappings (same isolation model as the camofox server container).

The dynamic bootstrap (`docker/bootstrap.mjs`) calls the same
`camoufox-js launchOptions()` the camofox server uses
(`{headless:false, humanize:true, enable_cache:true, virtual_display}`),
writes `CAMOU_CONFIG_1..N` + `FONTCONFIG_PATH` + `DISPLAY` into the
sidecar env, writes `firefoxUserPrefs` to the profile `user.js`, and bakes
the uBO addon path into the CAMOU_CONFIG JSON. Fresh random
audio/canvas/fonts seeds per boot = no fixed identity.

**Validated (2026-08-12):** `example.com`, `reddit.com/r/technology/` and
`olx.com.br` search (50 ads via `section.olx-adcard`) all navigate on the
first try through the browser-use harness. OLX loads without warm-up.

## Env vars

`FOXBRIDGE_CDP_URL` (default `http://127.0.0.1:9222`) ·
`FOXBRIDGE_CDP_PORT` (default `9222`) ·
`FOXBRIDGE_CONTAINER` (default `foxbridge`) ·
`FOXBRIDGE_IMAGE` (default `ghcr.io/lgwacker/foxbridge-camoufox:latest`) ·
`FOXBRIDGE_IDLE_TIMEOUT_S` (default `900`) ·
`FOXBRIDGE_BOOT_STABILIZE_S` (default `10`) ·
`FOXBRIDGE_VNC` (default `0`) ·
`FOXBRIDGE_VNC_PORT` (default `5901`) ·
`FOXBRIDGE_VNC_NOVNC_PORT` (default `6081`) ·
`FOXBRIDGE_VNC_BIND` (default `127.0.0.1`) ·
`FOXBRIDGE_VNC_PASSWORD` (optional) ·
`FOXBRIDGE_VNC_VIEW_ONLY` (default `0`)

## Interactive logins (VNC)

The sidecar image ships the camofox VNC stack (x11vnc + noVNC) attached to
the same Xvfb display the browser renders on. Enable it with
`FOXBRIDGE_VNC=1` (host env, e.g. `~/.hermes/.env`), then open
<http://127.0.0.1:6081/vnc.html> and log in manually — cookies persist in
the profile volume, so later automated sessions start logged in. VNC is
host-loopback only; ports 5901/6081 avoid the camofox-browser server's
5900/6080. The VNC stack dies with the sidecar (idle-stop or the
per-session restart), and every new `create_session` restarts the sidecar
— finish logins before sessions end, or raise `FOXBRIDGE_IDLE_TIMEOUT_S`.

`FOXBRIDGE_CDP_PORT` moves the sidecar's CDP bind (the Hermes cron-mode
Chrome holds 127.0.0.1:9222 while its daemon is up); the provider passes it
into the container and derives the CDP endpoint from it.

## Session hygiene (validated 2026-08-12)

Every `create_session` does three things so the browser-use harness always
gets a clean, ready browser:

1. **Restart the sidecar** if it is already running. foxbridge keeps tabs
   from previous sessions open, and the browser-use CLI 0.1.8 harness
   attaches to the FIRST existing page target — leftover tabs make new
   navigations land in the wrong tab.
2. **Kill the browser-use CLI daemon** (`browser_harness.daemon`). The
   daemon persists across CLI calls and holds the CDP WebSocket; after a
   sidecar restart its sessions point at dead tabs and navigation hangs
   (`Page.navigate` accepted, never committed). A fresh daemon reconnects
   cleanly.
3. **Settle ~10 s after the CDP endpoint answers** (`BOOT_STABILIZE_S`).
   The foxbridge CDP proxy is up before the Camoufox main frame accepts
   navigations; navigating too early corrupts the frame so every
   subsequent `Page.navigate` aborts with `NS_BINDING_ABORTED` until the
   next restart.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Failed to find execution context id-N` or results show ad iframes (adkernel, google gsi) | Image is stale (pre-mainframe-patch) → `docker pull ghcr.io/lgwacker/foxbridge-camoufox:latest` and restart |
| Navigation hangs on `about:blank` forever | Image is stale (pre-noop-patch); same fix as above |
| Old tabs (Google Sign-In, ad pages) resurrect after sidecar restart | Camoufox sessionstore restore: delete `recovery*.lz4` / `sessionstore*` in the profile dir (`~/.hermes/foxbridge-profiles/`) before restart |
| `docker logs foxbridge` shows `bind: address already in use` | The Hermes cron-mode Chrome holds 127.0.0.1:9222 → set `FOXBRIDGE_CDP_PORT` (e.g. `9223`) |
| `browser_exec` fails with `connect failed ('0.0.0.0', 9222)` after the sidecar stopped | Pre-0.2.0 dead-sidecar session trap (idle-stop + cached supervisor session) → upgrade the plugin, or rescue with `docker start foxbridge` |
| Flaky navigation after manual sidecar restarts | `pkill -f "browser_harness[.]daemon"` before the next `create_session` (the provider does this automatically) |
| `hermes plugins install` pulls a stale image | The image is rebuilt by CI on every push to `docker/**`; force with `docker pull` + `docker restart foxbridge` |

## Known limitations

- **Dialog interception is degraded** (Fetch.enable no-op): pages load but
  `javascriptDialogOpening`-style interception does not pause requests —
  the price of the Juggler deadlock fix.
- The harness `wait_for_load()` has an `about:blank` race: poll
  `page_info()` (2–3 s × 5) until `url` is not `about:blank`, or use
  `ensure_real_tab() + goto_url()`.
- `browser_exec` via a Hermes subprocess has an **open question**: the
  identical script via bash pipe commits navigations while the
  subprocess-invoked one occasionally fails to — treat as open, not
  broken; the provider's session hygiene mitigates it.
