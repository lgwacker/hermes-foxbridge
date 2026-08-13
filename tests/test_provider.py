"""Unit tests for the foxbridge provider — no Docker, no Hermes core needed.

``agent.browser_provider`` (the Hermes ABC) is stubbed so the suite runs
standalone in CI:

    python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
import types
import unittest
import os
from pathlib import Path
from unittest import mock

# --- stub the Hermes ABC so tests run without the hermes-agent checkout ---
_agent = types.ModuleType("agent")
_bp = types.ModuleType("agent.browser_provider")


class _StubBrowserProvider:
    name = "base"

    def display_name(self):
        return self.name

    def is_available(self):
        return True

    def create_session(self, task_id):
        raise NotImplementedError

    def close_session(self, session_id):
        return True

    def emergency_cleanup(self, session_id):
        pass

    def get_setup_schema(self):
        return {}


_bp.BrowserProvider = _StubBrowserProvider
_agent.browser_provider = _bp
sys.modules.setdefault("agent", _agent)
sys.modules["agent.browser_provider"] = _bp

# --- import the provider under test ---
PROVIDER_DIR = Path(__file__).resolve().parents[1] / "plugins" / "browser" / "foxbridge"
sys.path.insert(0, str(PROVIDER_DIR))

from provider import FoxbridgeBrowserProvider  # noqa: E402
import provider  # noqa: E402 — module ref for constants (BOOT_STABILIZE_S)


class ProviderContractTest(unittest.TestCase):
    def test_name_and_display(self):
        p = FoxbridgeBrowserProvider()
        self.assertEqual(p.name, "foxbridge")
        self.assertIn("Foxbridge", p.display_name)

    def test_is_available_gates_on_docker(self):
        with mock.patch("provider.shutil.which", return_value=None):
            self.assertFalse(FoxbridgeBrowserProvider().is_available())
        with mock.patch("provider.shutil.which", return_value="/usr/bin/docker"):
            self.assertTrue(FoxbridgeBrowserProvider().is_available())

    def test_create_session_returns_contract(self):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_ensure_running") as ensure, mock.patch.object(
            p, "_touch"
        ) as touch:
            info = p.create_session("t1")
        ensure.assert_called_once()
        touch.assert_called_once()
        self.assertEqual(info["cdp_url"], "http://127.0.0.1:9222")
        self.assertEqual(info["bb_session_id"], "foxbridge-t1")
        self.assertEqual(info["session_name"], "foxbridge-t1")

    def test_close_session_keeps_sidecar(self):
        self.assertTrue(FoxbridgeBrowserProvider().close_session("x"))


class IdleLifecycleTest(unittest.TestCase):
    def test_tick_stops_after_idle_timeout(self):
        p = FoxbridgeBrowserProvider(idle_timeout_s=60)
        p._last_used = 0.0  # ancient
        with mock.patch.object(p, "_container_state", return_value="running"), mock.patch(
            "provider._run", return_value=(0, "")
        ) as run:
            p._idle_tick()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:2], ["docker", "stop"])
        self.assertEqual(cmd[2], p._container)

    def test_tick_skips_when_recently_used(self):
        p = FoxbridgeBrowserProvider(idle_timeout_s=900)
        with mock.patch("provider.time.time", return_value=100.0):
            p._last_used = 99.0  # 1s ago -> under timeout
            with mock.patch.object(
                p, "_container_state", return_value="running"
            ), mock.patch("provider._run") as run:
                p._idle_tick()
        run.assert_not_called()

    def test_tick_skips_when_container_absent(self):
        p = FoxbridgeBrowserProvider(idle_timeout_s=60)
        p._last_used = 0.0
        with mock.patch.object(p, "_container_state", return_value="absent"), mock.patch(
            "provider._run"
        ) as run:
            p._idle_tick()
        run.assert_not_called()

    def test_touch_starts_watcher_once(self):
        p = FoxbridgeBrowserProvider()
        with mock.patch("provider.threading.Thread") as thread:
            p._touch()
            p._touch()
        thread.assert_called_once()


class EnsureRunningTest(unittest.TestCase):
    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_absent_creates_with_port_mappings_and_profile(self, run, healthy):
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-test-profile")
        with mock.patch.object(p, "_container_state", return_value="absent"):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][0], "docker")
        self.assertIn("run", cmds[0])
        self.assertNotIn("--network", cmds[0])
        self.assertIn("-p", cmds[0])
        self.assertIn("127.0.0.1:9222:9222", " ".join(cmds[0]))
        self.assertIn("-v", cmds[0])
        self.assertIn("/profile", " ".join(cmds[0]))
        self.assertIn(p._image, cmds[0])
        # stale daemon cleanup runs after the container is up
        self.assertEqual(cmds[-1][:2], ["pkill", "-f"])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_exited_with_host_network_recreates(self, run, healthy):
        """A container from the pre-bridge recipe (--network host) must be
        recreated so the -p mappings and the new entrypoint apply."""
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-test-profile")
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=True), \
             mock.patch.object(p, "_container_has_host_network", return_value=True):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:3], ["docker", "rm", "-f"])
        self.assertIn("run", cmds[1])
        self.assertNotIn("--network", cmds[1])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_exited_with_mount_starts(self, run, healthy):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=True), \
             mock.patch.object(p, "_container_has_host_network", return_value=False):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:2], ["docker", "start"])
        self.assertEqual(cmds[-1][:2], ["pkill", "-f"])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_exited_without_mount_recreates(self, run, healthy):
        """A container created by the old image (no -v /profile) must be
        recreated so the persistent profile volume is attached."""
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-test-profile")
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=False), \
             mock.patch.object(p, "_container_has_host_network", return_value=False):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:3], ["docker", "rm", "-f"])
        self.assertIn("run", cmds[1])
        self.assertIn("-v", cmds[1])
        self.assertIn("/tmp/fb-test-profile:/profile", " ".join(cmds[1]))
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_running_with_mount_restarts(self, run, healthy):
        """A running sidecar carries leftover tabs that break the
        browser-use harness (it attaches to the first existing page) —
        every session must start from a restarted, single-tab browser."""
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="running"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=True), \
             mock.patch.object(p, "_container_has_host_network", return_value=False):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:2], ["docker", "restart"])
        self.assertEqual(cmds[0][2], p._container)
        # the stale CLI daemon is killed before the health check
        self.assertEqual(cmds[-1][:2], ["pkill", "-f"])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_running_without_mount_recreates(self, run, healthy):
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-test-profile")
        with mock.patch.object(p, "_container_state", return_value="running"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=False), \
             mock.patch.object(p, "_container_has_host_network", return_value=False):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:3], ["docker", "rm", "-f"])
        self.assertIn("run", cmds[1])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(1, "boom"))
    def test_running_restart_failure_raises(self, run, healthy):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="running"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=True):
            with self.assertRaises(RuntimeError):
                p._ensure_running()
        healthy.assert_not_called()

    @mock.patch("provider._run", return_value=(1, "docker: image pull failed"))
    def test_create_failure_raises_clear_error(self, run):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="absent"):
            with self.assertRaisesRegex(RuntimeError, "could not be created"):
                p._ensure_running()

    @mock.patch("provider.time.sleep")
    @mock.patch("provider.urllib.request.urlopen")
    def test_wait_healthy_success(self, urlopen, sleep):
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = (
            b'{"Browser": "foxbridge/1.0", '
            b'"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/foxbridge"}'
        )
        urlopen.return_value.__enter__.return_value = resp
        FoxbridgeBrowserProvider()._wait_healthy()  # must not raise
        # boot stabilization sleep runs before declaring healthy
        sleep.assert_called_once_with(provider.BOOT_STABILIZE_S)

    @mock.patch("provider.time.sleep")
    @mock.patch("provider.urllib.request.urlopen")
    def test_wait_healthy_rejects_foreign_endpoint(self, urlopen, sleep):
        """A non-foxbridge service on the port (e.g. cron-mode Chrome) must
        never be declared healthy — the harness would attach to the wrong
        browser and fail with WS 404."""
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"Browser": "Chrome/148.0.7778.96", "webSocketDebuggerUrl": "ws://x"}'
        urlopen.return_value.__enter__.return_value = resp
        p = FoxbridgeBrowserProvider()
        with mock.patch("provider.HEALTH_TIMEOUT_S", 0.02):
            with self.assertRaisesRegex(RuntimeError, "not foxbridge"):
                p._wait_healthy()

    @mock.patch("provider.time.sleep")
    @mock.patch(
        "provider.urllib.request.urlopen", side_effect=OSError("conn refused")
    )
    def test_wait_healthy_timeout_raises(self, urlopen, sleep):
        p = FoxbridgeBrowserProvider()
        with mock.patch("provider.HEALTH_TIMEOUT_S", 0.01):
            with self.assertRaisesRegex(RuntimeError, "not reachable"):
                p._wait_healthy()


class VncEnvTest(unittest.TestCase):
    def test_vnc_off_by_default(self):
        with mock.patch.dict(
            os.environ, {"FOXBRIDGE_VNC": "0"}, clear=False
        ):
            p = FoxbridgeBrowserProvider()
            self.assertFalse(p._vnc_enabled())
            self.assertEqual(p._vnc_env_args(), [])

    def test_vnc_gate(self):
        with mock.patch.dict(
            os.environ, {"FOXBRIDGE_VNC": "1"}, clear=False
        ):
            p = FoxbridgeBrowserProvider()
            self.assertTrue(p._vnc_enabled())
            # gate + fixed container bind + non-empty defaults
            self.assertEqual(
                p._vnc_env_args(),
                [
                    "-e", "FOXBRIDGE_VNC=1",
                    "-e", "VNC_BIND=0.0.0.0",
                    "-e", "VNC_PORT=5901",
                    "-e", "NOVNC_PORT=6081",
                ],
            )

    def test_vnc_maps_host_env_to_container_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "FOXBRIDGE_VNC": "1",
                "FOXBRIDGE_VNC_PORT": "5905",
                "FOXBRIDGE_VNC_NOVNC_PORT": "6085",
                "FOXBRIDGE_VNC_BIND": "0.0.0.0",
                "FOXBRIDGE_VNC_PASSWORD": "secret",
                "FOXBRIDGE_VNC_VIEW_ONLY": "1",
            },
            clear=False,
        ):
            args = FoxbridgeBrowserProvider()._vnc_env_args()
        joined = " ".join(args)
        for expected in [
            "FOXBRIDGE_VNC=1",
            "VNC_BIND=0.0.0.0",
            "VNC_PORT=5905",
            "NOVNC_PORT=6085",
            "VNC_PASSWORD=secret",
            "VIEW_ONLY=1",
        ]:
            self.assertIn("-e " + expected, joined)
        # FOXBRIDGE_VNC_BIND now controls the HOST-side -p bind
        with mock.patch.dict(
            os.environ,
            {
                "FOXBRIDGE_VNC": "1",
                "FOXBRIDGE_VNC_BIND": "0.0.0.0",
                "FOXBRIDGE_VNC_NOVNC_PORT": "6085",
            },
            clear=False,
        ):
            p = FoxbridgeBrowserProvider()
            self.assertIn(
                "-p 0.0.0.0:6085:6085", " ".join(p._port_env_args())
            )

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_docker_run_gets_vnc_env_when_enabled(self, run, healthy):
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-vnc-test-profile")
        with mock.patch.dict(
            os.environ, {"FOXBRIDGE_VNC": "1"}, clear=False
        ), mock.patch.object(p, "_container_state", return_value="absent"):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        run_cmd = " ".join(cmds[0])
        self.assertIn("-e FOXBRIDGE_VNC=1", run_cmd)
        self.assertIn("-e NOVNC_PORT=6081", run_cmd)
        self.assertIn("-p 127.0.0.1:6081:6081", run_cmd)
        self.assertIn("-p 127.0.0.1:9222:9222", run_cmd)
        healthy.assert_called_once()

    def test_port_args_without_vnc_only_cdp(self):
        with mock.patch.dict(
            os.environ, {"FOXBRIDGE_VNC": "0"}, clear=False
        ):
            p = FoxbridgeBrowserProvider()
            self.assertEqual(
                p._port_env_args(), ["-p", "127.0.0.1:9222:9222"]
            )

    def test_create_session_exposes_vnc_url(self):
        with mock.patch.dict(
            os.environ, {"FOXBRIDGE_VNC": "1"}, clear=False
        ):
            p = FoxbridgeBrowserProvider()
            with mock.patch.object(p, "_ensure_running") as ensure, mock.patch.object(
                p, "_touch"
            ) as touch:
                info = p.create_session("t1")
        self.assertEqual(
            info["features"]["vnc_url"], "http://127.0.0.1:6081/vnc.html"
        )


class CdpPortTest(unittest.TestCase):
    def test_default_port_9222(self):
        p = FoxbridgeBrowserProvider()
        self.assertEqual(p._cdp_port, "9222")
        self.assertEqual(p._cdp_url, "http://127.0.0.1:9222")
        self.assertEqual(
            p._cdp_env_args(), ["-e", "FOXBRIDGE_CDP_PORT=9222"]
        )

    def test_port_env_derives_url_and_container_env(self):
        with mock.patch.dict(
            os.environ, {"FOXBRIDGE_CDP_PORT": "9223"}, clear=False
        ):
            p = FoxbridgeBrowserProvider()
            self.assertEqual(p._cdp_url, "http://127.0.0.1:9223")
            self.assertEqual(
                p._cdp_env_args(), ["-e", "FOXBRIDGE_CDP_PORT=9223"]
            )

    def test_explicit_cdp_url_wins_over_port(self):
        with mock.patch.dict(
            os.environ,
            {
                "FOXBRIDGE_CDP_PORT": "9223",
                "FOXBRIDGE_CDP_URL": "http://127.0.0.1:9999",
            },
            clear=False,
        ):
            p = FoxbridgeBrowserProvider()
            self.assertEqual(p._cdp_url, "http://127.0.0.1:9999")
            self.assertEqual(p._cdp_port, "9223")

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_docker_run_gets_cdp_port_env(self, run, healthy):
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-cdp-test-profile")
        with mock.patch.object(p, "_container_state", return_value="absent"):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        run_cmd = " ".join(cmds[0])
        self.assertIn("-e FOXBRIDGE_CDP_PORT=9222", run_cmd)
        self.assertIn("-p 127.0.0.1:9222:9222", run_cmd)
        healthy.assert_called_once()


class ResilienceTest(unittest.TestCase):
    """2026-08-13 hardening: idle-timeout floor (core override), auto-heal
    of crashed sidecars, health identity check, recreate fallback."""

    # --- Fix 1: the core's idle_timeout_s must not shorten the plugin floor

    def test_idle_timeout_floor_is_plugin_default(self):
        """A core-provided arg below the plugin default (e.g. 120 s from
        browser.inactivity_timeout) must NOT shorten the documented 900 s."""
        p = FoxbridgeBrowserProvider(idle_timeout_s=120)
        self.assertEqual(p._idle_timeout_s, 900)

    def test_idle_timeout_core_may_extend(self):
        p = FoxbridgeBrowserProvider(idle_timeout_s=1200)
        self.assertEqual(p._idle_timeout_s, 1200)

    def test_idle_timeout_respects_env_floor(self):
        with mock.patch.object(provider, "DEFAULT_IDLE_TIMEOUT_S", 300):
            p = FoxbridgeBrowserProvider(idle_timeout_s=120)
            self.assertEqual(p._idle_timeout_s, 300)

    def test_init_logs_effective_idle_timeout(self):
        """The effective timeout must be visible in logs — the silent 120 s
        override cost a full debugging session on 2026-08-13."""
        with self.assertLogs("provider", level="INFO") as cm:
            FoxbridgeBrowserProvider(idle_timeout_s=120)
        self.assertTrue(
            any("idle_timeout_s=900" in m for m in cm.output),
            f"expected effective timeout in log, got: {cm.output}",
        )

    # --- Fix 2: auto-heal a crashed sidecar while a session is open

    def test_tick_auto_heals_crashed_container_with_session_open(self):
        p = FoxbridgeBrowserProvider()
        p._last_used = 1000.0  # recent — not an idle situation
        p._session_open = True
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_exit_code", return_value="1"), \
             mock.patch.object(p, "_ensure_running") as ensure, \
             mock.patch("provider._run") as run:
            p._idle_tick()
        ensure.assert_called_once()
        run.assert_not_called()  # no docker stop for an already-dead container

    def test_tick_does_not_heal_clean_idle_stop(self):
        """Exit 0 = the watcher's own clean idle-stop — no resurrection."""
        p = FoxbridgeBrowserProvider()
        p._last_used = 1000.0
        p._session_open = True
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_exit_code", return_value="0"), \
             mock.patch.object(p, "_ensure_running") as ensure, \
             mock.patch("provider._run") as run:
            p._idle_tick()
        ensure.assert_not_called()
        run.assert_not_called()

    def test_tick_does_not_heal_when_session_closed(self):
        p = FoxbridgeBrowserProvider()
        p._last_used = 1000.0
        p._session_open = False
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_exit_code", return_value="1"), \
             mock.patch.object(p, "_ensure_running") as ensure, \
             mock.patch("provider._run") as run:
            p._idle_tick()
        ensure.assert_not_called()
        run.assert_not_called()

    def test_session_open_lifecycle(self):
        p = FoxbridgeBrowserProvider()
        self.assertFalse(p._session_open)
        with mock.patch.object(p, "_ensure_running"), mock.patch.object(
            p, "_touch"
        ):
            p.create_session("t1")
        self.assertTrue(p._session_open)
        p.close_session("t1")
        self.assertFalse(p._session_open)
        with mock.patch.object(p, "_ensure_running"), mock.patch.object(
            p, "_touch"
        ):
            p.create_session("t2")
        p.emergency_cleanup("t2")
        self.assertFalse(p._session_open)

    # --- Fix 5: recreate fallback when a resurrected container is unhealthy

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy",
                       side_effect=[RuntimeError("boom"), None])
    @mock.patch("provider._run", return_value=(0, ""))
    def test_health_failure_after_start_recreates(self, run, healthy):
        """docker start brings the container up but health fails (Xvfb lock
        / corrupted frame) → clean recreate must be attempted before giving
        up."""
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-heal-profile")
        with mock.patch.object(p, "_container_state", return_value="exited"), \
             mock.patch.object(p, "_container_has_profile_mount", return_value=True), \
             mock.patch.object(p, "_container_has_host_network", return_value=False):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:2], ["docker", "start"])
        # recreate path: rm -f followed by a fresh docker run
        recreate_idx = next(
            i for i, c in enumerate(cmds) if c[:3] == ["docker", "rm", "-f"]
        )
        self.assertEqual(cmds[recreate_idx + 1][:2], ["docker", "run"])
        self.assertEqual(healthy.call_count, 2)

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy",
                       side_effect=RuntimeError("boom"))
    @mock.patch("provider._run", return_value=(0, ""))
    def test_health_failure_after_fresh_create_reraises(self, run, healthy):
        """A fresh create failing health is a real problem — re-raise, do
        not loop."""
        p = FoxbridgeBrowserProvider(profile_dir="/tmp/fb-fail-profile")
        with mock.patch.object(p, "_container_state", return_value="absent"):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        # exactly one create attempt, no rm -f loop
        self.assertEqual([c[:3] for c in cmds].count(["docker", "rm", "-f"]), 0)
        self.assertEqual(healthy.call_count, 1)


if __name__ == "__main__":
    unittest.main()
