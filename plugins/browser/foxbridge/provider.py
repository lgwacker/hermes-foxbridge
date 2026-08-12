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
from typing import Any, Dict, Optional

from agent.browser_provider import BrowserProvider

logger = logging.getLogger(__name__)

DEFAULT_CDP_URL = os.environ.get("FOXBRIDGE_CDP_URL", "http://127.0.0.1:9222")
DEFAULT_CONTAINER = os.environ.get("FOXBRIDGE_CONTAINER", "foxbridge")
DEFAULT_IMAGE = os.environ.get(
    "FOXBRIDGE_IMAGE",
    "ghcr.io/lgwacker/foxbridge-camoufox:latest",
)
DEFAULT_IDLE_TIMEOUT_S = int(os.environ.get("FOXBRIDGE_IDLE_TIMEOUT_S", "900"))
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
        container: Optional[str] = None,
        image: Optional[str] = None,
        idle_timeout_s: Optional[int] = None,
    ) -> None:
        self._cdp_url = cdp_url or DEFAULT_CDP_URL
        self._container = container or DEFAULT_CONTAINER
        self._image = image or DEFAULT_IMAGE
        self._idle_timeout_s = idle_timeout_s or DEFAULT_IDLE_TIMEOUT_S
        self._last_used = 0.0
        self._lock = threading.Lock()
        self._watcher: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # BrowserProvider contract
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Cheap check — no network, no heavy subprocess. Runs at tool
        registration time and on every ``hermes tools`` paint."""
        return shutil.which("docker") is not None

    def create_session(self, task_id: str) -> Dict[str, object]:
        self._ensure_running()
        self._touch()
        return {
            "session_name": f"foxbridge-{task_id}",
            "bb_session_id": f"foxbridge-{task_id}",
            "cdp_url": self._cdp_url,
            "expires_at": "",
            "features": {},
        }

    def close_session(self, session_id: str) -> bool:
        # The sidecar stays up; the idle watcher stops it. Nothing to
        # release per-session (the CDP endpoint is shared).
        return True

    def emergency_cleanup(self, session_id: str) -> None:
        # Best-effort teardown at process exit. The container is
        # disposable; the idle watcher / restart policy covers it.
        pass

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
            ],
        }

    # ------------------------------------------------------------------
    # Sidecar lifecycle: lazy start + idle stop (mirrors camofox server)
    # ------------------------------------------------------------------

    def _touch(self) -> None:
        with self._lock:
            self._last_used = time.time()
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
        """One idle-check iteration — split out for testability."""
        with self._lock:
            idle_s = time.time() - self._last_used
        if idle_s < self._idle_timeout_s:
            return
        if self._container_state() != "running":
            return
        code, out = _run(["docker", "stop", self._container], timeout=60)
        if code == 0:
            logger.info("foxbridge sidecar stopped after %.0fs idle", idle_s)
        else:
            logger.warning("foxbridge idle stop failed: %s", out[:200])

    def _container_state(self) -> str:
        """Return 'running', another live status, or 'absent'."""
        code, out = _run(
            ["docker", "inspect", "-f", "{{.State.Status}}", self._container],
            timeout=15,
        )
        if code != 0:
            return "absent"
        return out.strip() or "absent"

    def _ensure_running(self) -> None:
        state = self._container_state()
        if state == "running":
            # The foxbridge browser persists across CDP sessions: tabs
            # from previous sessions stay open. The browser-use CLI
            # harness (v0.1.8) attaches to the FIRST existing page target
            # and keeps evaluating there, so leftover tabs (about:blank,
            # example.com, ...) make every new navigation land in the
            # wrong tab. Restart the sidecar so each session starts with
            # a clean single-tab browser (~2-3s).
            code, out = _run(["docker", "restart", self._container], timeout=120)
            if code != 0:
                raise RuntimeError(
                    f"foxbridge sidecar could not be restarted: {out[:300]}"
                )
        elif state == "absent":
            code, out = _run(
                [
                    "docker", "run", "-d",
                    "--name", self._container,
                    "--restart", "unless-stopped",
                    # foxbridge binds 127.0.0.1 only (no --host flag yet),
                    # so the sidecar shares the host network namespace.
                    "--network", "host",
                    self._image,
                ],
                timeout=180,
            )
            if code != 0:
                raise RuntimeError(
                    f"foxbridge sidecar could not be created "
                    f"(image: {self._image}): {out[:300]}"
                )
        else:
            code, out = _run(["docker", "start", self._container], timeout=60)
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
        self._wait_healthy()

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
