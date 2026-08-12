"""Unit tests for the foxbridge provider — no Docker, no Hermes core needed.

``agent.browser_provider`` (the Hermes ABC) is stubbed so the suite runs
standalone in CI:

    python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
import types
import unittest
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
            with mock.patch("provider._run") as run:
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
    def test_absent_creates_with_network_host(self, run, healthy):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="absent"):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][0], "docker")
        self.assertIn("run", cmds[0])
        self.assertIn("--network", cmds[0])
        self.assertIn("host", cmds[0])
        self.assertIn(p._image, cmds[0])
        # stale daemon cleanup runs after the container is up
        self.assertEqual(cmds[-1][:2], ["pkill", "-f"])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_exited_starts(self, run, healthy):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="exited"):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:2], ["docker", "start"])
        self.assertEqual(cmds[-1][:2], ["pkill", "-f"])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(0, ""))
    def test_running_restarts_to_clear_leftover_tabs(self, run, healthy):
        """A running sidecar carries leftover tabs that break the
        browser-use harness (it attaches to the first existing page) —
        every session must start from a restarted, single-tab browser."""
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="running"):
            p._ensure_running()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:2], ["docker", "restart"])
        self.assertEqual(cmds[0][2], p._container)
        # the stale CLI daemon is killed before the health check
        self.assertEqual(cmds[-1][:2], ["pkill", "-f"])
        healthy.assert_called_once()

    @mock.patch.object(FoxbridgeBrowserProvider, "_wait_healthy")
    @mock.patch("provider._run", return_value=(1, "boom"))
    def test_running_restart_failure_raises(self, run, healthy):
        p = FoxbridgeBrowserProvider()
        with mock.patch.object(p, "_container_state", return_value="running"):
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
        urlopen.return_value.__enter__.return_value = resp
        FoxbridgeBrowserProvider()._wait_healthy()  # must not raise
        # boot stabilization sleep runs before declaring healthy
        sleep.assert_called_once_with(provider.BOOT_STABILIZE_S)

    @mock.patch("provider.time.sleep")
    @mock.patch(
        "provider.urllib.request.urlopen", side_effect=OSError("conn refused")
    )
    def test_wait_healthy_timeout_raises(self, urlopen, sleep):
        p = FoxbridgeBrowserProvider()
        with mock.patch("provider.HEALTH_TIMEOUT_S", 0.01):
            with self.assertRaisesRegex(RuntimeError, "not reachable"):
                p._wait_healthy()


if __name__ == "__main__":
    unittest.main()
