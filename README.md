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
- `docker/bootstrap.mjs` — dynamic bootstrap: calls the same `camoufox-js launchOptions()` the camofox server uses, writes `CAMOU_CONFIG_1..N` / `FONTCONFIG_PATH` / `DISPLAY` to the sidecar env, `firefoxUserPrefs` to the profile, and bakes the uBO addon into the fingerprint config. Fresh random seeds per boot = no fixed identity.
- `patches/` — the **three mandatory foxbridge fixes** (see [`patches/README.md`](patches/README.md)): `Fetch.enable` no-op (Juggler deadlock fix), main-frame `Runtime.evaluate` context (ad-iframe drift fix) and a `--host` bind flag (bridge networking: the sidecar uses `-p` mappings instead of `--network host`). They are maintained as **commits on the fork [`lgwacker/foxbridge`](https://github.com/lgwacker/foxbridge)** (main, pinned by `FOXBRIDGE_REF` in `scripts/build-image.sh`) — the binary is built from that fork, nothing is committed to this repo.
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

> ⚠️ Start a **new session** after installing (the provider selection is
> read once per process and cached).

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `FOXBRIDGE_CDP_URL` | `http://127.0.0.1:9222` | CDP endpoint the provider hands to Hermes |
| `FOXBRIDGE_CDP_PORT` | `9222` | CDP port the sidecar binds (move it away from the Hermes cron Chrome) |
| `FOXBRIDGE_CONTAINER` | `foxbridge` | Docker container name |
| `FOXBRIDGE_IMAGE` | `ghcr.io/lgwacker/foxbridge-camoufox:latest` | Sidecar image |
| `FOXBRIDGE_IDLE_TIMEOUT_S` | `900` | Idle seconds before the sidecar is stopped |
| `FOXBRIDGE_VNC` | `0` | `1` enables the sidecar VNC/noVNC for interactive logins |
| `FOXBRIDGE_VNC_PORT` | `5901` | x11vnc port (host loopback) |
| `FOXBRIDGE_VNC_NOVNC_PORT` | `6081` | noVNC web port (host loopback) |
| `FOXBRIDGE_VNC_BIND` | `127.0.0.1` | noVNC bind address |
| `FOXBRIDGE_VNC_PASSWORD` | — | optional x11vnc password |
| `FOXBRIDGE_VNC_VIEW_ONLY` | `0` | `1` = view-only VNC |

## Interactive logins via VNC (optional)

Some sites need a manual login (2FA, CAPTCHAs, unusual SSO). The sidecar
image ships the camofox VNC stack (x11vnc + noVNC) — the provider turns it
on with `FOXBRIDGE_VNC=1`:

1. Set `FOXBRIDGE_VNC=1` (e.g. in `~/.hermes/.env`) and start a browser
   session. The sidecar now also listens on `127.0.0.1:5901` (x11vnc) and
   `127.0.0.1:6081` (noVNC).
2. Open <http://127.0.0.1:6081/vnc.html> and log in manually — that is the
   same Camoufox instance the CDP side drives.
3. The login persists in the profile volume
   (`~/.hermes/foxbridge-profiles/`), so later automated sessions start
   logged in.

Notes:

- VNC binds host loopback only, and dies with the sidecar: idle-stop after
  `FOXBRIDGE_IDLE_TIMEOUT_S` or the restart every `create_session` runs.
  Raise `FOXBRIDGE_IDLE_TIMEOUT_S` while logging in interactively, and set
  `FOXBRIDGE_VNC_PASSWORD` if you bind beyond loopback.
- Ports 5901/6081 avoid the camofox-browser server's 5900/6080 (the sidecar
  shares the host network namespace).

## Known pitfalls

- **`wait_for_load()` race on `about:blank`** — the harness considers a page
  loaded as soon as `document.readyState` is `complete`, which is already
  true on `about:blank`. Right after `new_tab(url)`, `wait_for_load()` can
  therefore return before the navigation commits, and `page_info()` still
  shows `about:blank`. Workaround: poll `page_info()` a few times with short
  sleeps, or use `ensure_real_tab()` + `goto_url(url)` instead of
  `new_tab()` (verified landing cleanly through this stack).
- `CAMOFOX_URL` must not be set (see Install).
- **Sessionstore resurrection** — Camoufox restores old tabs (Google
  Sign-In, ad pages) from the profile's sessionstore on sidecar restart;
  the harness can then attach to an ad iframe instead of the main page.
  Delete `recovery*.lz4` / `sessionstore*` in `~/.hermes/foxbridge-profiles/`
  before restarting the sidecar manually.
- **Port 9222 conflict with the Hermes cron Chrome** — the gateway's
  cron-mode Chrome (`chrome-debug-cron`) binds `127.0.0.1:9222` while its
  daemon is up, so the sidecar's foxbridge cannot bind and sessions fail
  (`address already in use` in `docker logs foxbridge`). Set
  `FOXBRIDGE_CDP_PORT` (e.g. `9223`) — the provider passes it into the
  container and derives the CDP endpoint from it.
- **Stale browser-use daemon** — after a manual sidecar restart, kill the
  harness daemon (`pkill -f "browser_harness[.]daemon"`); the provider does
  this automatically in `create_session`.

## Development

```bash
python -m pytest tests/ -q          # unit tests (no Docker, no Hermes needed)
./scripts/build-image.sh            # build the sidecar image locally
```

`build-image.sh` clones the **fork** `lgwacker/foxbridge` at the pinned
`FOXBRIDGE_REF` (whose `main` carries all three fixes as commits) and builds
the binary in a throwaway golang container — no patch step, no binary in git.
To bump the upstream base, rebase the fork on upstream and update
`FOXBRIDGE_REF` (see [`patches/README.md`](patches/README.md)).

CI: unit tests on every push; image build+push to GHCR on `main` (the binary
is built from the fork inside CI — same `build-image.sh` path as locally).

## Roadmap

- [x] `--host` flag — implemented in the fork (`lgwacker/foxbridge`, commit `d50f813`): the sidecar now runs bridge networking with loopback-only `-p` (no more `--network host`)
- [ ] Upstream foxbridge: release binaries (drops the golang build step)
- [ ] Per-session browser contexts (`Target.createBrowserContext`) for cookie isolation
- [ ] Image tags per Camoufox version

## License

MIT
