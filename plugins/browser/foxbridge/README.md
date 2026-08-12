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
`FOXBRIDGE_IDLE_TIMEOUT_S` (default `900`)
