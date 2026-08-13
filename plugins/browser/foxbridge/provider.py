"""Foxbridge browser provider — Camoufox anti-detect via the foxbridge CDP proxy.

Registers as a ``browser.cloud_provider`` backend named ``foxbridge``.

Serves BOTH browser surfaces:

* the browser-use CLI harness (``browser_exec``) — Hermes resolves this
  provider's CDP endpoint into ``BU_CDP_URL``; and
* the built-in browser tools (``browser_navigate``, ``browser_click``, ...)
  — the legacy stack attaches to the same CDP endpoint.

Backend: a Docker sidecar running foxbridge (CDP -> Juggler proxy) with a
Camoufox browser from the ``foxbridge-camoufox`` image. Lifecycle mirrors
the camofox integration: the sidecar starts on first use and is stopped
after ``FOXBRIDGE_IDLE_TIMEOUT_S`` (default 900 s) of inactivity.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from agent.browser_provider import BrowserProvider

logger = logging.getLogger(__name__)

DEFAULT_CDP_PORT = os.environ.get("FOXBRIDGE_CDP_PORT", "9222")
DEFAULT_CDP_URL = os.environ.get(
    "FOXBRIDGE_CDP_URL", f"http://127.0.0.1:{DEFAULT_CDP_PORT}"
)
DEFAULT_CONTAINER = os.environ.get("FOXBRIDGE_CONTAINER", "foxbridge")
DEFAULT_IMAGE = os.environ.get(
    "FOXBRIDGE_IMAGE",
    "ghcr.io/lgwacker/foxbridge-camoufox:latest",
)
DEFAULT_PROFILE_DIR = os.environ.get(
    "FOXBRIDGE_PROFILE_DIR",
    os.path.join(os.path.expanduser("~"), ".hermes", "foxbridge-profiles"),
)
DEFAULT_IDLE_TIMEOUT_S = int(os.environ.get("FOXBRIDGE_IDLE_TIMEOUT_S", "900"))
# VNC (interactive logins): the image ships the VNC stack from the
# camofox-browser base; the sidecar entrypoint starts it only when
# FOXBRIDGE_VNC is enabled. Defaults avoid the ports the camofox-browser
# server uses (5900/6080); host exposure is via -p (loopback by default).
DEFAULT_VNC_PORT = os.environ.get("FOXBRIDGE_VNC_PORT", "5901")
DEFAULT_VNC_NOVNC_PORT = os.environ.get("FOXBRIDGE_VNC_NOVNC_PORT", "6081")
DEFAULT_VNC_BIND = os.environ.get("FOXBRIDGE_VNC_BIND", "127.0.0.1")
HEALTH_TIMEOUT_S = 90
IDLE_POLL_S = 30
# The foxbridge CDP endpoint answers as soon as the proxy is up, but the
# Camoufox browser process needs a few more seconds before its main frame
# accepts navigations. Navigating too early makes every subsequent
# Page.navigate abort with NS_BINDING_ABORTED until the sidecar restarts
# (validated 2026-08-12: navigating 2s after boot corrupted the frame;
# restart + 10s settle navigated fine).
BOOT_STABILIZE_S = float(os.environ.get("FOXBRIDGE_BOOT_STABILIZE_S", "10"))


def _run(cmd, timeout: int = 30):
    """Run a command; return (exit_code, combined_output). Never raises."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "docker executable not found on PATH"
    except OSError as exc:
        return -1, f"command failed: {exc}"


class FoxbridgeBrowserProvider(BrowserProvider):
    """Camoufox anti-detect browsing through a foxbridge Docker sidecar."""

    name = "foxbridge"

    @property
    def display_name(self) -> str:
        return "Foxbridge (Camoufox via CDP)"

    def __init__(
        self,
        cdp_url: Optional[str] = None,
        cdp_port: Optional[str] = None,
        container: Optional[str] = None,
        image: Optional[str] = None,
        profile_dir: Optional[str] = None,
        idle_timeout_s: Optional[int] = None,
    ) -> None:
        # Port/env read at construction (not import) so tests and config
        # changes can move the CDP endpoint without a process restart.
        self._cdp_port = cdp_port or os.environ.get(
            "FOXBRIDGE_CDP_PORT"
        ) or DEFAULT_CDP_PORT
        self._cdp_url = cdp_url or os.environ.get("FOXBRIDGE_CDP_URL") or (
            f"http://127.0.0.1:{self._cdp_port}"
        )
        self._container = container or DEFAULT_CONTAINER
        self._image = image or DEFAULT_IMAGE
        self._profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        # The core Hermes passes its own idle timeout (browser.inactivity_timeout,
        # default 120 s) as `idle_timeout_s` — that arg silently beat the
        # plugin's documented FOXBRIDGE_IDLE_TIMEOUT_S (default 900 s), so the
        # watcher idle-stopped the sidecar mid-session while the supervisor
        # still held it open (dead-sidecar session trap, 2026-08-13). The
        # plugin default/env is the FLOOR: the core may extend, never shorten.
        self._idle_timeout_s = max(
            idle_timeout_s or 0, DEFAULT_IDLE_TIMEOUT_S
        )
        self._last_used = 0.0
        # Ownership baseline for the idle watcher: the container's
        # StartedAt captured at our last _touch(). Several long-lived
        # Hermes processes (desktop backend, gateway, leftover CLI
        # sessions) each run a provider with its OWN idle watcher on the
        # SAME container name — the watcher must never stop a sidecar
        # another instance (or a manual `docker start`) brought up.
        self._owned_started_at: Optional[str] = None
        self._foreign_sidecar = False
        self._session_open = False
        self._lock = threading.Lock()
        self._watcher: Optional[threading.Thread] = None
        logger.info(
            "foxbridge provider ready: cdp=%s container=%s image=%s "
            "idle_timeout_s=%s (core_arg=%s, plugin_default=%s)",
            self._cdp_url, self._container, self._image,
            self._idle_timeout_s, idle_timeout_s, DEFAULT_IDLE_TIMEOUT_S,
        )

    # ------------------------------------------------------------------
    # BrowserProvider contract
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Cheap check — no network, no heavy subprocess. Runs at tool
        registration time and on every ``hermes tools`` paint."""
        return shutil.which("docker") is not None

    def create_session(self, task_id: str) -> Dict[str, object]:
        self._ensure_running()
        with self._lock:
            self._session_open = True
        self._touch()
        features: Dict[str, str] = {}
        if self._vnc_enabled():
            features["vnc_url"] = self._vnc_url()
        return {
            "session_name": f"foxbridge-{task_id}",
            "bb_session_id": f"foxbridge-{task_id}",
            "cdp_url": self._cdp_url,
            "expires_at": "",
            "features": features,
        }

    def close_session(self, session_id: str) -> bool:
        # The sidecar stays up; the idle watcher stops it. Mark the session
        # closed so the auto-heal never resurrects the sidecar for a session
        # the core has already torn down.
        with self._lock:
            self._session_open = False
        return True

    def emergency_cleanup(self, session_id: str) -> None:
        # Best-effort teardown at process exit. The container is
        # disposable; the idle watcher / restart policy covers it.
        with self._lock:
            self._session_open = False

    def get_setup_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "name": self.display_name,
            "badge": "local",
            "tag": "Camoufox anti-detect via foxbridge CDP proxy "
            "(Docker sidecar, idle-stop)",
            "env_vars": [
                {
                    "key": "FOXBRIDGE_CONTAINER",
                    "prompt": "Docker container name for the foxbridge sidecar",
                    "default": DEFAULT_CONTAINER,
                },
                {
                    "key": "FOXBRIDGE_IMAGE",
                    "prompt": "Container image (foxbridge + Camoufox)",
                    "default": DEFAULT_IMAGE,
                },
                {
                    "key": "FOXBRIDGE_VNC",
                    "prompt": "Enable the sidecar VNC/noVNC for interactive logins (1 or 0)",
                    "default": "0",
                },
            ],
        }

    # ------------------------------------------------------------------
    # Sidecar lifecycle: lazy start + idle stop (mirrors camofox server)
    # ------------------------------------------------------------------

    def _touch(self) -> None:
        with self._lock:
            self._last_used = time.time()
            # create_session runs _ensure_running() first, which (re)starts
            # the sidecar — so the StartedAt captured here is the container
            # THIS instance owns from now on.
            self._foreign_sidecar = False
            self._owned_started_at = self._container_started_at()
        self._ensure_watcher()

    def _ensure_watcher(self) -> None:
        if self._watcher is not None:
            return
        with self._lock:
            if self._watcher is not None:
                return
            self._watcher = threading.Thread(
                target=self._idle_loop, daemon=True, name="foxbridge-idle-watcher"
            )
            self._watcher.start()

    def _idle_loop(self) -> None:
        while True:
            time.sleep(IDLE_POLL_S)
            self._idle_tick()

    def _idle_tick(self) -> None:
        """One idle-check iteration — split out for testability.

        Two jobs:
        1. Auto-heal: if the sidecar exited with a non-zero code (a real
           crash, NOT the clean exit-0 idle-stop) while a session is open,
           restart it. The core's browser_supervisor caches the session and
           never re-invokes ``create_session`` after the sidecar dies, so
           without this every later ``browser_exec`` fails with
           ``connect failed`` until /reset (dead-sidecar session trap,
           2026-08-13).
        2. Idle-stop: stop the sidecar after ``_idle_timeout_s`` of no
           ``create_session`` activity.
        """
        with self._lock:
            idle_s = time.time() - self._last_used
            session_open = self._session_open
        state = self._container_state()
        if state == "exited" and session_open:
            exit_code = self._container_exit_code()
            if exit_code is not None and exit_code != "0":
                logger.info(
                    "foxbridge sidecar exited with code %s while session "
                    "open — auto-healing", exit_code,
                )
                try:
                    # _ensure_running does docker start/restart + daemon
                    # cleanup + health check; must never kill the watcher.
                    self._ensure_running()
                except Exception as exc:  # noqa: BLE001 — watcher must live
                    logger.warning("foxbridge auto-heal failed: %s", exc)
                return
        if idle_s < self._idle_timeout_s:
            return
        if state != "running":
            return
        # Ownership guard (v0.2.1): stale watchers from OTHER provider
        # instances idle-stopped the sidecar 7-15 s after every boot
        # (docker events: SIGTERM + foxbridge "shutting down..." while a
        # browser_exec was in flight → "no close frame received"). The X
        # /quotes page was wrongly blamed on 2026-08-13 — its navigate
        # landed AFTER the container was already stopping. Never stop a
        # container whose StartedAt changed since our last touch: another
        # instance or a manual docker start owns it now.
        if self._foreign_sidecar:
            return  # another instance owns the sidecar — leave it alone
        started_at = self._container_started_at()
        if started_at != self._owned_started_at:
            self._foreign_sidecar = True
            logger.info(
                "foxbridge sidecar %s started/restarted after this "
                "instance's last session (owned=%r current=%r) — "
                "skipping idle-stop",
                self._container, self._owned_started_at, started_at,
            )
            return
        code, out = _run(["docker", "stop", self._container], timeout=60)
        if code == 0:
            logger.info("foxbridge sidecar stopped after %.0fs idle", idle_s)
        else:
            logger.warning("foxbridge idle stop failed: %s", out[:200])

    def _container_started_at(self) -> Optional[str]:
        """StartedAt (RFC3339 string) of the container, or None when it is
        absent or not inspectable. Used as the idle-watcher ownership
        token: a changed value means the sidecar was (re)started by
        another process since our last touch."""
        code, out = _run(
            [
                "docker", "inspect", "-f", "{{.State.StartedAt}}",
                self._container,
            ],
            timeout=15,
        )
        if code != 0:
            return None
        return out.strip() or None

    def _container_exit_code(self) -> Optional[str]:
        """Exit code of an exited container, or None when not inspectable."""
        code, out = _run(
            [
                "docker", "inspect", "-f", "{{.State.ExitCode}}",
                self._container,
            ],
            timeout=15,
        )
        if code != 0:
            return None
        return out.strip()

    def _container_state(self) -> str:
        """Return 'running', another live status, or 'absent'."""
        code, out = _run(
            ["docker", "inspect", "-f", "{{.State.Status}}", self._container],
            timeout=15,
        )
        if code != 0:
            return "absent"
        return out.strip() or "absent"

    def _container_has_profile_mount(self) -> bool:
        """True when the existing container was created with the persistent
        profile volume (old images ran without -v /profile)."""
        code, out = _run(
            [
                "docker", "inspect", "-f",
                "{{range .Mounts}}{{.Destination}} {{end}}",
                self._container,
            ],
            timeout=15,
        )
        return code == 0 and "/profile" in out

    def _container_has_host_network(self) -> bool:
        """True when the existing container was created with --network
        host (the pre-bridge recipe) — such a container must be recreated
        so the -p port mappings and the new entrypoint apply."""
        code, out = _run(
            [
                "docker", "inspect", "-f",
                "{{.HostConfig.NetworkMode}}",
                self._container,
            ],
            timeout=15,
        )
        return code == 0 and out.strip() == "host"

    def _container_is_stale(self) -> bool:
        """Container created by an older recipe (host network, or no
        persistent profile mount) must be dropped and rebuilt."""
        return (
            self._container_has_host_network()
            or not self._container_has_profile_mount()
        )

    def _recreate_container(self) -> None:
        """Drop a stale container (wrong image/entrypoint/mounts) and let the
        create path rebuild it. Best-effort — never raises."""
        code, out = _run(
            ["docker", "rm", "-f", self._container], timeout=60
        )
        if code != 0:
            logger.warning(
                "foxbridge stale container removal failed (rc=%s): %s",
                code, out[:120],
            )

    def _vnc_enabled(self) -> bool:
        """VNC opt-in: FOXBRIDGE_VNC on the host set to anything but '0'."""
        return os.environ.get("FOXBRIDGE_VNC", "0") != "0"

    def _vnc_env_args(self) -> List[str]:
        """Docker -e args turning on the sidecar VNC stack (x11vnc +
        noVNC) for interactive logins. The image ships the VNC bits (base
        camofox-browser image); the entrypoint starts the vnc-watcher
        against the Xvfb display only when FOXBRIDGE_VNC != '0'.
        websockify binds 0.0.0.0 INSIDE the container so docker-proxy can
        reach it over the bridge; host exposure is the -p mapping."""
        if not self._vnc_enabled():
            return []
        args = ["-e", "FOXBRIDGE_VNC=1", "-e", "VNC_BIND=0.0.0.0"]
        mapping = [
            ("FOXBRIDGE_VNC_PORT", "VNC_PORT", DEFAULT_VNC_PORT),
            ("FOXBRIDGE_VNC_NOVNC_PORT", "NOVNC_PORT", DEFAULT_VNC_NOVNC_PORT),
            ("FOXBRIDGE_VNC_PASSWORD", "VNC_PASSWORD", ""),
            ("FOXBRIDGE_VNC_VIEW_ONLY", "VIEW_ONLY", ""),
        ]
        for host_key, container_key, default in mapping:
            val = os.environ.get(host_key, default)
            if val:
                args += ["-e", f"{container_key}={val}"]
        return args

    def _vnc_url(self) -> str:
        bind = os.environ.get("FOXBRIDGE_VNC_BIND", DEFAULT_VNC_BIND)
        port = os.environ.get("FOXBRIDGE_VNC_NOVNC_PORT", DEFAULT_VNC_NOVNC_PORT)
        return f"http://{bind}:{port}/vnc.html"

    def _cdp_env_args(self) -> List[str]:
        """Keep the entrypoint's foxbridge --port in sync with the
        provider's endpoint. The Hermes cron-mode Chrome occupies 9222
        while its daemon is up; FOXBRIDGE_CDP_PORT moves the sidecar to a
        free port. Always passed so the container matches the provider."""
        return ["-e", f"FOXBRIDGE_CDP_PORT={self._cdp_port}"]

    def _port_env_args(self) -> List[str]:
        """docker -p args. The sidecar runs on the bridge network (NOT
        --network host): the local --host patch makes foxbridge bind
        0.0.0.0 inside the container so docker-proxy can reach it. Host
        exposure is loopback-only unless FOXBRIDGE_VNC_BIND is changed."""
        args = ["-p", f"127.0.0.1:{self._cdp_port}:{self._cdp_port}"]
        if self._vnc_enabled():
            bind = os.environ.get("FOXBRIDGE_VNC_BIND", DEFAULT_VNC_BIND)
            novnc = os.environ.get(
                "FOXBRIDGE_VNC_NOVNC_PORT", DEFAULT_VNC_NOVNC_PORT
            )
            args += ["-p", f"{bind}:{novnc}:{novnc}"]
        return args

    def _ensure_running(self) -> None:
        state = self._container_state()
        fresh_created = False
        if state == "running":
            # The foxbridge browser persists across CDP sessions: tabs
            # from previous sessions stay open. The browser-use CLI
            # harness (v0.1.8) attaches to the FIRST existing page target
            # and keeps evaluating there, so leftover tabs (about:blank,
            # example.com, ...) make every new navigation land in the
            # wrong tab. Restart the sidecar so each session starts with
            # a clean single-tab browser (~2-3s).
            if self._container_is_stale():
                self._recreate_container()
                state = "absent"
            else:
                code, out = _run(
                    ["docker", "restart", self._container], timeout=120
                )
                if code != 0:
                    raise RuntimeError(
                        f"foxbridge sidecar could not be restarted: {out[:300]}"
                    )
        if state == "absent":
            self._ensure_profile_dir()
            self._create_container()
            fresh_created = True
        elif state == "exited":
            if self._container_is_stale():
                self._recreate_container()
                self._ensure_profile_dir()
                self._create_container()
                fresh_created = True
            else:
                code, out = _run(
                    ["docker", "start", self._container], timeout=60
                )
                if code != 0:
                    raise RuntimeError(
                        f"foxbridge sidecar could not be started: {out[:300]}"
                    )
        # The browser-use CLI keeps a persistent daemon
        # (browser_harness.daemon) that holds the CDP WebSocket across
        # calls. After a sidecar restart the daemon's sessions point at
        # dead tabs, making navigation hang (Page.navigate accepted but
        # never committed). Kill it so the next browser_exec starts a
        # fresh daemon with a clean connection.
        self._stop_stale_cli_daemon()
        try:
            self._wait_healthy()
        except RuntimeError as exc:
            # A pre-existing container that just came back (start/restart)
            # can boot into a broken state — e.g. the entrypoint's Xvfb
            # lock after `docker stop`, or a corrupted main frame from an
            # early navigation. A clean recreate usually fixes it; a fresh
            # create failing again is a real problem, so re-raise.
            if fresh_created:
                raise
            logger.warning(
                "foxbridge health check failed after start/restart (%s) — "
                "recreating sidecar", exc,
            )
            self._recreate_container()
            self._ensure_profile_dir()
            self._create_container()
            self._stop_stale_cli_daemon()
            self._wait_healthy()

    def _ensure_profile_dir(self) -> None:
        if not os.path.isdir(self._profile_dir):
            os.makedirs(self._profile_dir, exist_ok=True)

    def _create_container(self) -> None:
        """``docker run -d`` with the current recipe: bridge networking
        (the --host patch makes foxbridge bind 0.0.0.0 inside), persistent
        Camoufox profile volume (cookies/ad-sync state and uBO filter lists
        survive restarts), VNC/CDP env, loopback-only port mappings."""
        code, out = _run(
            [
                "docker", "run", "-d",
                "--name", self._container,
                "--restart", "unless-stopped",
                # bridge networking: the --host patch makes foxbridge
                # bind 0.0.0.0 inside; -p publishes loopback-only.
                # persistent Camoufox profile: cookies/ad-sync state and
                # uBO filter lists survive restarts (see README).
                "-v", f"{self._profile_dir}:/profile",
                *self._vnc_env_args(),
                *self._cdp_env_args(),
                *self._port_env_args(),
                self._image,
            ],
            timeout=180,
        )
        if code != 0:
            raise RuntimeError(
                f"foxbridge sidecar could not be created "
                f"(image: {self._image}): {out[:300]}"
            )
        logger.info("foxbridge sidecar container %s created", self._container)

    def _stop_stale_cli_daemon(self) -> None:
        """Kill the browser-use CLI daemon (browser_harness.daemon) so the
        next browser_exec starts with a fresh CDP connection. The daemon
        persists across CLI calls and holds the CDP WebSocket plus target
        sessions; after the sidecar restarts those sessions point at dead
        tabs and navigation hangs (Page.navigate accepted, never
        committed). rc 0 = killed, 1 = nothing matched — both fine.
        """
        code, out = _run(
            ["pkill", "-f", "python -m browser_harness.daemon"], timeout=10
        )
        if code not in (0, 1):
            logger.warning(
                "foxbridge stale daemon cleanup failed (rc=%s): %s",
                code, out[:120],
            )

    def _wait_healthy(self) -> None:
        deadline = time.time() + HEALTH_TIMEOUT_S
        last_err = "no response"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{self._cdp_url}/json/version", timeout=3
                ) as resp:
                    if resp.status == 200:
                        # Identity check: ANY service answering on the port
                        # passes a bare 200 — including the Hermes cron-mode
                        # Chrome on 9222 or a leftover dev server. Only a
                        # foxbridge endpoint is usable (the harness resolves
                        # its WS URL from this body and would otherwise
                        # attach to the wrong browser and fail with 404).
                        body = resp.read(4096).decode("utf-8", "replace")
                        if "foxbridge" not in body:
                            last_err = (
                                "endpoint answered but is not foxbridge "
                                "(another service on the port?)"
                            )
                            time.sleep(2)
                            continue
                        # CDP is up but the Camoufox process may still be
                        # settling — give the browser boot a moment before
                        # declaring the sidecar usable (see BOOT_STABILIZE_S).
                        time.sleep(BOOT_STABILIZE_S)
                        logger.info(
                            "foxbridge sidecar healthy at %s", self._cdp_url
                        )
                        return
            except Exception as exc:  # health probe must never raise
                last_err = str(exc)
            time.sleep(2)
        raise RuntimeError(
            f"foxbridge CDP endpoint {self._cdp_url} not reachable "
            f"after {HEALTH_TIMEOUT_S}s: {last_err}"
        )
