# Foxbridge browser provider (Hermes plugin)

Registers the `foxbridge` browser backend with Hermes: a Docker sidecar
running foxbridge + Camoufox, exposed as `browser.cloud_provider: foxbridge`.

Serves both browser surfaces:
- `browser_exec` (browser-use CLI harness) — Hermes injects this provider's
  CDP endpoint as `BU_CDP_URL`;
- built-in tools (`browser_navigate`, `browser_click`, ...) — the legacy
  stack attaches to the same CDP endpoint.

Lifecycle: the sidecar starts on first use and is stopped after
`FOXBRIDGE_IDLE_TIMEOUT_S` (default 900 s) of inactivity — same lazy
start / idle-stop pattern as the camofox integration.

## Install

```bash
hermes plugins install lgwacker/hermes-foxbridge/plugins/browser/foxbridge --enable
hermes config set browser.cloud_provider foxbridge
```

Requirements: Docker. For `browser_exec`: the browser-use CLI
(`uv tool install browser-use` via `hermes tools` → Browser Automation).

> ⚠️ Remove `CAMOFOX_URL` from `~/.hermes/.env` — the camofox backend takes
> precedence over any `browser.cloud_provider` (Hermes design).

## Env vars

`FOXBRIDGE_CDP_URL` (default `http://127.0.0.1:9222`) ·
`FOXBRIDGE_CONTAINER` (default `foxbridge`) ·
`FOXBRIDGE_IMAGE` (default `ghcr.io/lgwacker/foxbridge-camoufox:latest`) ·
`FOXBRIDGE_IDLE_TIMEOUT_S` (default `900`) ·
`FOXBRIDGE_BOOT_STABILIZE_S` (default `10`)

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

## Known limitations

- **OLX Brazil does not load** (verified 2026-08-12). The OLX anti-bot
  stack catches the foxbridge-launched Camoufox: headless gets an
  `NS_ERROR_REDIRECT_LOOP` (Cloudflare challenge loop); headed (Xvfb) with
  a persistent profile gets stuck in ad-sync redirects (tynt, smartadserver,
  criteo, prebid...). The camofox server (production container, established
  profile/identity) navigates OLX fine — keep `CAMOFOX_URL` set for OLX
  workloads and use the foxbridge provider for sites without aggressive
  anti-bot.
- `example.com`-class sites work end-to-end (Hermes `browser_exec` →
  CLI 0.1.8 → CDP :9222 → foxbridge → Camoufox).
- The harness `wait_for_load()` has an `about:blank` race: poll
  `page_info()` (2–3 s × 5) until `url` is not `about:blank`, or use
  `ensure_real_tab() + goto_url()`.
