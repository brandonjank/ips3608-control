"""Agent interface checks with a mocked serial worker: never touch the supply."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from ips3608.agent_bridge import AppBridge, BridgeClient, Request
from ips3608.app import App
from ips3608.mcp_server import create_server

ROOT = Path(__file__).resolve().parent.parent


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.device = Mock()
        now = time.monotonic()
        self.app.connected = True
        self.app.port.set("COM_TEST")
        self.app.last_sample = now
        self.app.state = dict(model="IPS3608", output=True, set_volts=5., set_amps=1.,
                              status_monotonic=now, settings_monotonic=now)
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = AppBridge(Path(self.temp.name) / "bridge.json")

    def tearDown(self):
        self.bridge.shutdown()
        if self.app.recorder:
            self.app.stop_recording()
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temp.cleanup()

    def submit(self, operation, **params):
        request = Request(operation, params)
        self.bridge.requests.put(request)
        self.bridge.drain(self.app)
        return request

    def test_stale_state_marks_output_unknown_and_blocks_changes(self):
        self.app.last_sample -= 10
        result = self.submit("status").future.result()
        self.assertIsNone(result["output_enabled"])
        self.assertTrue(result["last_reported_output_enabled"])
        with self.assertRaisesRegex(RuntimeError, "Fresh"):
            self.submit("setpoints", voltage_v=12).future.result()
        self.app.device.request.assert_not_called()

    def test_live_partial_setpoints_use_readback_and_wait_for_confirmation(self):
        self.app.voltage.set("30")  # Unapplied manual editor value must not leak.
        request = self.submit("setpoints", current_a=.25)
        self.app.device.request.assert_called_once_with("set", 5., .25)
        self.assertFalse(request.future.done())
        self.app.state["set_amps"] = .25
        self.app.pending = None
        self.bridge.on_event(self.app, "confirmed", {"action": "brightness"})
        self.assertFalse(request.future.done())
        self.bridge.on_event(self.app, "confirmed", {"action": "set"})
        result = request.future.result()
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["status"]["device"]["set_amps"], .25)
        self.assertTrue(result["status"]["output_enabled"])

    def test_off_supersedes_change_even_when_telemetry_is_stale(self):
        first = self.submit("brightness", level=5)
        self.app.last_sample -= 10
        off = self.submit("output", enabled=False)
        with self.assertRaisesRegex(RuntimeError, "Superseded"):
            first.future.result()
        self.assertEqual(self.app.device.request.call_args.args, ("off",))
        self.assertFalse(off.future.done())
        self.bridge.on_event(self.app, "confirmed", {"action": "off"})
        self.assertTrue(off.future.result()["confirmed"])

    def test_expired_and_cancelled_requests_never_write_later(self):
        old = Request("brightness", {"level": 5}, expires=time.monotonic()-1)
        cancelled = Request("brightness", {"level": 5})
        cancelled.future.cancel()
        self.bridge.requests.put(old)
        self.bridge.requests.put(cancelled)
        self.bridge.drain(self.app)
        self.assertTrue(old.future.cancelled())
        self.app.device.request.assert_not_called()

    def test_invalid_requests_and_path_traversal_never_write(self):
        cases = [("setpoints", dict(voltage_v=36, current_a=8)),
                 ("brightness", dict(level=True)), ("preset", dict(slot=7, voltage_v=5, current_a=1)),
                 ("record_start", dict(filename="../outside.csv")),
                 ("record_start", dict(filename="C:\\outside.csv")),
                 ("record_start", dict(filename="NUL.csv")),
                 ("record_start", dict(filename="COM1.csv")),
                 ("register", dict(address=1)), ("status", dict(extra=True))]
        for op, params in cases:
            with self.subTest(op=op, params=params), self.assertRaises(RuntimeError):
                self.submit(op, **params).future.result()
        self.app.device.request.assert_not_called()

    def test_recording_is_exclusive_and_reset_scopes_are_independent(self):
        sample = dict(monotonic=time.monotonic(), volts=5, amps=1, watts=5)
        self.app.history.append(sample)
        self.app.session.add(sample)
        self.app.statistics.add(sample)
        with patch("ips3608.agent_bridge.ROOT", Path(self.temp.name)):
            self.submit("record_start", filename="agent.csv").future.result()
            self.submit("reset_statistics").future.result()
            self.assertEqual(self.app.statistics.count, 0)
            self.assertEqual(self.app.session.samples, 1)
            self.assertIsNotNone(self.app.recorder)
            saved = self.submit("record_stop").future.result()
            self.assertEqual(Path(saved["saved_path"]).name, "agent.csv")
            with self.assertRaisesRegex(RuntimeError, "exists"):
                self.submit("record_start", filename="agent.csv").future.result()
        self.app.device.request.assert_not_called()

    def test_program_does_not_implicitly_enable_output(self):
        self.app.state["output"] = False
        with self.assertRaisesRegex(RuntimeError, "Turn output on explicitly"):
            self.submit("program_start", steps=[dict(volts=5, amps=1, seconds=1)]).future.result()
        self.app.device.request.assert_not_called()

    def test_authenticated_loopback_and_single_instance(self):
        self.bridge.start()
        self.assertEqual(self.bridge.server.server_address[0], "127.0.0.1")
        self.assertEqual(BridgeClient(self.bridge.discovery).call("health")["service"], "ips3608-app")
        conn = http.client.HTTPConnection("127.0.0.1", self.bridge.server.server_port, timeout=2)
        conn.request("GET", "/health")
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        for headers in ({"Origin": "https://example.com"}, {"Host": "example.com"}):
            headers["Authorization"] = f"Bearer {self.bridge.token}"
            conn = http.client.HTTPConnection("127.0.0.1", self.bridge.server.server_port, timeout=2)
            conn.request("GET", "/health", headers=headers)
            self.assertEqual(conn.getresponse().status, 403)
            conn.close()
        with self.assertRaisesRegex(RuntimeError, "already running"):
            AppBridge(self.bridge.discovery).start()

    def test_http_request_dispatch_and_confirmation_use_app_thread(self):
        self.bridge.start()
        client = BridgeClient(self.bridge.discovery)
        with ThreadPoolExecutor(max_workers=1) as pool:
            response = pool.submit(client.call, "brightness", level=6)
            deadline = time.monotonic() + 2
            while self.bridge.requests.empty() and time.monotonic() < deadline:
                time.sleep(.01)
            self.bridge.drain(self.app)
            self.app.device.request.assert_called_once_with("brightness", 6)
            self.assertFalse(response.done())
            self.app.state["brightness"] = 6
            self.app.pending = None
            self.bridge.on_event(self.app, "confirmed", {"action": "brightness"})
            result = response.result(timeout=2)
            self.assertTrue(result["confirmed"])
            self.assertEqual(result["status"]["device"]["brightness"], 6)

    def test_confirmation_timeout_does_not_claim_success(self):
        request = self.submit("brightness", level=5)
        request.expires = time.monotonic() - 1
        self.bridge.drain(self.app)
        with self.assertRaisesRegex(RuntimeError, "Readback timed out"):
            request.future.result()
        # A late device event cannot alter the completed failure.
        self.bridge.on_event(self.app, "confirmed", {"action": "brightness"})
        self.assertIsNone(self.bridge.waiting)


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_discovery_and_offline_validation(self):
        import sys
        transport = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tools" / "run_mcp.py")],
                                         cwd=tempfile.gettempdir())
        async with Client(transport) as client:
            tools = await client.list_tools()
            tools = tools.tools if hasattr(tools, "tools") else tools
            by_name = {tool.name: tool for tool in tools}
            self.assertEqual(len(by_name), 21)
            self.assertTrue(by_name["get_status"].annotations.read_only_hint)
            self.assertFalse(by_name["set_output"].annotations.read_only_hint)
            result = await client.call_tool("preview_sweep", dict(axis="voltage", start=5, end=6,
                                                                  increment=.5, fixed=.2, seconds=1, model="IPS3608"))
            self.assertFalse(result.is_error)
            data = result.structured_content
            if data is None:
                data = json.loads(result.content[0].text)
            self.assertEqual(len(data["steps"]), 3)
            result = await client.call_tool("validate_program", dict(steps=[dict(volts=36, amps=8, seconds=1)], model="IPS3608"))
            self.assertTrue(result.is_error)
            self.assertIn("285 W", result.content[0].text)
            resource = await client.read_resource("ips3608://capabilities")
            self.assertIn("285", str(resource))

    async def test_mcp_routes_to_bridge_and_monitor_reports_stale(self):
        ipc = Mock()
        ipc.call.return_value = {"fresh": False, "output_enabled": None, "latest_sample": None}
        async with Client(create_server(ipc)) as client:
            result = await client.call_tool("monitor", {"duration_s": .5})
            self.assertFalse(result.is_error)
            ipc.call.assert_called_once_with("status")
            ipc.call.reset_mock()
            ipc.call.return_value = {"confirmed": True}
            result = await client.call_tool("set_setpoints", {"current_a": .25})
            self.assertFalse(result.is_error)
            ipc.call.assert_called_once_with("setpoints", voltage_v=None, current_a=.25)


if __name__ == "__main__":
    unittest.main()
