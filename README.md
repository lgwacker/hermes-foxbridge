# Hermes Foxbridge

Hermes browser provider that drives a **Camoufox anti-detect browser** (Firefox fork with C++-level fingerprint spoofing) through the **foxbridge** CDP→Juggler proxy — giving `browser_exec` (browser-use CLI harness) and the built-in browser tools a stealth browser backend, no Chrome, no cloud.

```
Hermes (browser_exec or browser_navigate/click/...)
   │ CDP
   ▼
foxbridge (Docker sidecar, port 9222)
   │ Juggler pipe
   ▼
Camoufox (anti-detect Firefox fork)
```

## Why

| | Chrome + harness | Camoufox + foxbridge (this repo) |
|---|---|---|
| Anti-bot stealth | trivially detectable | C++ fingerprint spoofing (canvas, WebGL, audio, fonts, ...) |
| Harness quality | browser-use CLI (SOTA web-task benchmarks) | same harness, same quality |
| Cost | free (local) or cloud credits | free, local, unlimited sessions |
| Persistence | — | per-profile via Camoufox |

## Components

- `plugins/browser/foxbridge/` — Hermes plugin (`kind: backend`, `provides_browser_providers: [foxbridge]`). Select with `browser.cloud_provider: foxbridge`.
- `docker/Dockerfile` — `foxbridge-camoufox` image: foxbridge + Camoufox bundle (built on the camofox-browser image), published to `ghcr.io/lgwacker/foxbridge-camoufox` by CI.
- Provider lifecycle mirrors the camofox integration: the sidecar **starts on first use** and **stops after `FOXBRIDGE_IDLE_TIMEOUT_S`** (default 900 s) of inactivity.

## Install

```bash
# 1. Plugin (user-space; no system installs)
hermes plugins install lgwacker/hermes-foxbridge/plugins/browser/foxbridge --enable

# 2. Sidecar image (first use only — the provider creates/starts the
#    container itself on demand, image pulled automatically)
docker pull ghcr.io/lgwacker/foxbridge-camoufox:latest

# 3. Select the provider
hermes config set browser.cloud_provider foxbridge
```

Requirements: Docker, and the browser-use CLI for the `browser_exec` surface
(`hermes tools` → Browser Automation → Browser Use installs it; the built-in
browser tools work without it).

> ⚠️ `CAMOFOX_URL` must NOT be set in `~/.hermes/.env` — the camofox backend
> takes precedence over any `browser.cloud_provider` (Hermes design). Remove it
> to let this provider serve browser traffic.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `FOXBRIDGE_CDP_URL` | `http://127.0.0.1:9222` | CDP endpoint the provider hands to Hermes |
| `FOXBRIDGE_CONTAINER` | `foxbridge` | Docker container name |
| `FOXBRIDGE_IMAGE` | `ghcr.io/lgwacker/foxbridge-camoufox:latest` | Sidecar image |
| `FOXBRIDGE_IDLE_TIMEOUT_S` | `900` | Idle seconds before the sidecar is stopped |

## Development

```bash
python -m pytest tests/ -q          # unit tests (no Docker, no Hermes needed)
./scripts/build-image.sh            # build the sidecar image locally
```

CI: unit tests on every push; image build+push to GHCR on `main`.

## Roadmap

- [ ] Upstream foxbridge: `--host` flag (drops the `--network host` requirement) and release binaries (drops the golang build step)
- [ ] Per-session browser contexts (`Target.createBrowserContext`) for cookie isolation
- [ ] Image tags per Camoufox version

## License

MIT
