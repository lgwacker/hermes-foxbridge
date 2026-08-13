# Foxbridge Fixes — why they exist and how to maintain them

**The three fixes are mandatory — the plugin is broken without any one of
them.** They live as **commits on the maintained fork
[`lgwacker/foxbridge`](https://github.com/lgwacker/foxbridge)** (main),
based on upstream foxbridge commit `7dee166567d837ecfd0cce3664a6e03fc441e97b`:

| Fix | Fork commit |
|---|---|
| Fetch.enable no-op | `a41f9e6` (chain: `33f703f`, `57ebbbc`, `a41f9e6`) |
| Main-frame context | `a9020bd` |
| `--host` flag | `d50f813` |

The `.patch` files in this directory are the **upstream-facing diffs** — kept
for the upstream issues/PRs and as documentation. Nothing applies them at
build time anymore: `scripts/build-image.sh` and the CI clone the fork at
`FOXBRIDGE_REF` (= fork main tip `c1f51a8`) and `go build` directly.

---

## Patch 1: `foxbridge-fetch-noop.patch` — Fetch.enable must be a no-op

**Files:** `pkg/bridge/fetch.go`, `pkg/bridge/events.go`

### The bug (upstream: VulpineOS/foxbridge issue #5, PR #6)

The Hermes browser-use harness installs a dialog bridge via
`Fetch.enable`. foxbridge translated that to Juggler's
`Network.setRequestInterception` — and **every navigation deadlocked**:
pages stayed on `about:blank` forever, requests paused silently.

### Root cause

Camoufox 135's Juggler implementation is broken in two ways:

1. **`Browser.requestIntercepted` does not exist.** foxbridge subscribed to
   a phantom event. The real protocol (verified in `omni.ja` of Camoufox
   135) is `Network.setRequestInterception` /
   `Network.resumeInterceptedRequest` / the `Network.requestWillBeSent`
   event with an `isIntercepted` field.
2. **The interception pipeline never delivers events.** The
   `NetworkObserver` (content process) never sends
   `PageNetwork.Events.Request` to the `PageHandler` (browser process).
   So even with the correct protocol names, activating interception pauses
   requests with nobody to resume them → 100% deadlock.

### The fix

`Fetch.enable` is a **no-op**: it records the requested URL patterns (so a
future Juggler fix can honor them) but never calls
`Network.setRequestInterception`. Navigation flows; dialog interception is
degraded but pages load. The phantom `Browser.requestIntercepted` handler
is removed; a real `Network.requestWillBeSent` + `isIntercepted` handler
replaces it (auto-resume for non-matching patterns).

### How we know (evidence)

- `Fetch.disable` (deactivation) unblocks navigation → interception was the
  cause.
- Puppeteer's own `page.setRequestInterception(true)` test **also hangs**
  against real Camoufox (the upstream "74/74" suite passes because it
  intercepts nothing: no patterns → `enabled:false`).
- A hook with `throw` in the patched `omni.ja` proved the bundle is loaded
  (`FB-HOOK-RAN`), yet `PageNetwork.Events.Request` never fires.
- Full write-up: upstream issue
  [VulpineOS/foxbridge#5](https://github.com/VulpineOS/foxbridge/issues/5).

---

## Patch 2: `foxbridge-mainframe-context.patch` — evaluate in the main frame

**Files:** `pkg/bridge/bridge.go`, `pkg/bridge/events.go`

### The bug

foxbridge resolved context-less `Runtime.evaluate` to the **last-created**
execution context. On ad-heavy pages (OLX, Reddit) iframes load *after*
the main frame, so "last" = an ad iframe. Symptoms:

- `page_info()` / `js()` return ad URLs (`adkernel`, `smilewanted`,
  `accounts.google.com/gsi/...`) instead of the page you navigated to;
- `-32000 Failed to find execution context with id = id-N` errors — the
  cached context belongs to a frame that was destroyed during navigation.

### The fix

Track the **main-frame** execution context per Juggler session
(`AuxData.FrameID` prefix `mainframe`) and prefer it for context-less
evaluates, falling back to "latest" only when no main-frame context is
known yet. This matches Chrome semantics (context-less evaluate = top
frame).

### How we know (evidence)

- With the patch: `example.com` AND `reddit.com/r/technology/` AND
  `olx.com.br` search (50 ads via `section.olx-adcard`) navigate and
  evaluate correctly on first try.
- Without it, the same harness loops forever on `id-16`-style errors and
  reports Google/ad iframes as the current page.

---

## Patch 3: `foxbridge-host-flag.patch` — `--host` flag (bridge networking)

**Files:** `cmd/foxbridge/main.go`

### The problem

foxbridge hardcodes `127.0.0.1` as the CDP bind host (`pkg/cdp/server.go`
defaults the `Server.host` field; the CLI had no flag to override it).
The `cdp.Server` already exposes `SetHost()`, but `cmd/foxbridge` never
wired it. With a plain `-p` publish, docker-proxy connects to the
container's bridge IP — not its loopback — so the CDP endpoint is
unreachable. The old workaround was `--network host`, which shares the
host network namespace with the container (bigger attack surface: a
compromised container can bind/connect anywhere on the host).

### The fix

Add a `--host` flag (default `127.0.0.1`, preserving upstream behaviour)
and always call `server.SetHost(*host)`. The sidecar entrypoint passes
`--host 0.0.0.0` (bind inside the container), and the provider publishes
loopback-only `-p` mappings — the same isolation model as the
camofox-browser server container.

### How we know (evidence)

- `foxbridge --host 0.0.0.0` inside a bridge-networked container is
  reachable through `-p 127.0.0.1:9222:9222` (`/json/version` returns the
  `webSocketDebuggerUrl`; `docker logs` shows
  `CDP server listening on 0.0.0.0:9222`).
- Validated e2e with the full stack (CDP + VNC): RFB handshake through
  noVNC on `127.0.0.1:6081`, page target alive, navigation via the
  browser-use harness.
- Upstream has no `--host` flag yet — this patch is the local
  implementation of the roadmap item; rebase on upstream when it lands.

---

## Updating the upstream foxbridge version (fork rebase)

The fork's main tracks upstream. To bump the upstream base:

1. In `~/repos/foxbridge` (or a fresh clone): `git fetch upstream`,
   `git rebase upstream/main` — fix any conflicts manually (the fix
   commits are small; the `.patch` files here are the reference diffs).
2. Rebuild + re-test the triage: `scripts/build-image.sh`, then
   `example.com`, `reddit.com/r/technology/`, `olx.com.br/brasil?q=...`
   via `BU_CDP_URL` + browser-use CLI — first-try navigation on all
   three, no `id-N` errors.
3. Push the fork, then update `FOXBRIDGE_REF` in
   `scripts/build-image.sh` to the new fork main tip and commit it here.
4. Push → the `docker-image` workflow rebuilds and republishes
   `ghcr.io/lgwacker/foxbridge-camoufox:latest` from the new ref.

## TL;DR checklist if the browser acts up

| Symptom | Likely cause | Fix |
|---|---|---|
| Navigation hangs on `about:blank` forever | Fetch.enable not no-op (fix 1 missing in binary) | Rebuild from the fork (`build-image.sh`) |
| `Failed to find execution context id-N` / ad iframes in results | Fix 2 missing | Rebuild from the fork (`build-image.sh`) |
| CDP endpoint unreachable through `-p` (loopback-only bind) | Fix 3 missing in binary | Rebuild from the fork (`build-image.sh`) |
| OLX/Reddit work, other sites flaky | Stale browser-use daemon after sidecar restart | `pkill -f "browser_harness[.]daemon"` + restart sidecar |
| Old tabs (Google Sign-In, ad pages) resurrect after restart | Camoufox sessionstore restore | Delete `recovery*.lz4` / `sessionstore*` in the profile dir before restart |
