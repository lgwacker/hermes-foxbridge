# Foxbridge Patches — why they exist and how to maintain them

**Read this before rebuilding the binary or touching the image.** Both
patches are **mandatory** — the plugin is broken without either one. They
apply to upstream foxbridge commit `7dee166567d837ecfd0cce3664a6e03fc441e97b`
in this exact order:

```bash
git apply patches/foxbridge-fetch-noop.patch          # FIRST
git apply patches/foxbridge-mainframe-context.patch   # SECOND
```

The committed binary (`docker/foxbridge`) and the published image
(`ghcr.io/lgwacker/foxbridge-camoufox:latest`) already contain both.
You only need the patches if you **rebuild** the binary yourself.

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

## Updating the upstream foxbridge version

If you bump `FOXBRIDGE_REF` (in `scripts/build-image.sh`):

1. `git apply` both patches in order; fix any rejects manually (the
   `index` lines in each patch show the base hashes).
2. Rebuild: `scripts/build-image.sh` (needs Docker only — Go build runs
   in a throwaway `golang:1.26` container, nothing installed on host).
3. Re-test the triage: `example.com`, `reddit.com/r/technology/`,
   `olx.com.br/brasil?q=...` via `BU_CDP_URL` + browser-use CLI —
   first-try navigation on all three, no `id-N` errors.
4. Commit the new binary (`docker/foxbridge`) — the CI image build uses
   the **committed** binary, it does NOT rebuild from upstream
   (deliberate: `go install @latest` would silently drop both patches).
5. Push → `docker-image` workflow rebuilds and republishes
   `ghcr.io/lgwacker/foxbridge-camoufox:latest`.

## TL;DR checklist if the browser acts up

| Symptom | Likely cause | Fix |
|---|---|---|
| Navigation hangs on `about:blank` forever | Fetch.enable not no-op (patch 1 missing in binary) | Rebuild with `foxbridge-fetch-noop.patch` |
| `Failed to find execution context id-N` / ad iframes in results | Patch 2 missing | Rebuild with `foxbridge-mainframe-context.patch` |
| OLX/Reddit work, other sites flaky | Stale browser-use daemon after sidecar restart | `pkill -f "browser_harness[.]daemon"` + restart sidecar |
| Old tabs (Google Sign-In, ad pages) resurrect after restart | Camoufox sessionstore restore | Delete `recovery*.lz4` / `sessionstore*` in the profile dir before restart |
