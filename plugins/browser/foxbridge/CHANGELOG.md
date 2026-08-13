# Changelog

All notable changes to the foxbridge browser provider (Hermes plugin).

## [0.2.1] — 2026-08-13

### Fixed

- **Idle-watcher ownership guard (multi-instance race).** Long-lived Hermes
  processes (desktop backend, gateway, leftover CLI sessions) each run their
  own provider with its own idle watcher on the **same container name**. A
  stale watcher whose `_last_used` was ancient idle-stopped the sidecar 7–15 s
  after *any* process booted it — surfacing as `no close frame received` /
  `client closed` mid-`browser_exec` (this was wrongly blamed on the X
  `/quotes` page on 2026-08-13; the page's navigation actually landed after
  the container was already stopping). The watcher now captures the
  container's `StartedAt` at every session start and only idle-stops while it
  still matches; a sidecar (re)started by another instance or a manual
  `docker start` is never stopped.
  Note: processes that loaded older plugin code keep the old watcher behavior
  until restarted (gateway needs a restart; the desktop app needs a relaunch).

## [0.2.0] — 2026-08-13

### Fixed

- **Idle-timeout floor.** The core passes its own
  `browser.inactivity_timeout` (default 120 s) as `idle_timeout_s`; the
  plugin's documented `FOXBRIDGE_IDLE_TIMEOUT_S` (900 s) is now a floor —
  the effective timeout is `max(core_arg, FOXBRIDGE_IDLE_TIMEOUT_S)`, so a
  short core value can never idle-stop the sidecar out from under an open
  session (the "dead-sidecar session trap").
- **Sidecar auto-heal.** A sidecar that exits with a non-zero code (a real
  crash — not the clean exit-0 idle-stop) while a session is open is
  restarted within ~30 s; the core's supervisor reconnects without `/reset`.
- **Health identity check.** `_wait_healthy` requires `foxbridge` in the
  `/json/version` body — a foreign service on the port (e.g. cron-mode Chrome
  on 9222) is never declared healthy.
- **Recreate fallback.** A `docker start`/`restart` that boots unhealthy
  (Xvfb lock after `docker stop`, corrupted main frame) triggers one clean
  `rm -f` + `run` before giving up.

## [0.1.0] — 2026-08-12

### Added

- Initial provider: foxbridge (CDP→Juggler bridge) + Camoufox anti-detect
  browser as a Docker sidecar, registered as `browser.cloud_provider:
  foxbridge`. Serves both `browser_exec` (browser-use CLI harness via
  `BU_CDP_URL`) and the built-in browser tools.
- Session hygiene: per-`create_session` sidecar restart (clean single-tab
  browser), stale browser-use CLI daemon kill, boot settle before driving
  the browser.
- `foxbridge-camoufox` image: dynamic Camoufox bootstrap (fresh
  audio/canvas/font fingerprint seeds per boot), uBO addon baked into the
  fingerprint config, foxbridge binary built from the maintained fork
  (`lgwacker/foxbridge` — Fetch no-op, main-frame context, `--host` fixes).
- VNC/noVNC for interactive logins (`FOXBRIDGE_VNC`), bridge networking
  (loopback-only `-p` mappings), `FOXBRIDGE_CDP_PORT` to move off a
  contested 9222.
