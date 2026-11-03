"""Release-only checks: storage, resources, simulation and display overflow."""

import os
from pathlib import Path
import queue
import struct
import tempfile
import unittest
from unittest.mock import patch

from ips3608 import paths
from ips3608.branding import VERSION
from ips3608.demo import DemoSerial
from ips3608.device import Device
from ips3608.protocol import output_command
import test_protocol as protocol_tests
import test_app as app_tests


class ReleaseTests(unittest.TestCase):
    def test_user_data_override_is_independent_of_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"IPS3608_CONTROL_HOME": directory}):
                self.assertEqual(paths.data_directory(), Path(directory).resolve())

    def test_legacy_presets_copy_once_and_preserve_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source", Path(directory) / "data"
            source.mkdir()
            original = source / "settings.json"
            original.write_text('{"presets": []}')
            with patch.object(paths, "PROJECT", source), patch.object(paths, "DATA", target):
                paths.migrate_presets()
                self.assertEqual((target / "settings.json").read_text(), original.read_text())
                (target / "settings.json").write_text("already customized")
                paths.migrate_presets()
                self.assertEqual((target / "settings.json").read_text(), "already customized")
                self.assertTrue(original.exists())

    def test_icon_resources_are_packaged_and_versioned(self):
        self.assertEqual((paths.ASSETS / "icon-48.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        header = (paths.ASSETS / "app.ico").read_bytes()[:6]
        self.assertEqual(struct.unpack("<HHH", header), (0, 1, 7))
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")


class DemoTests(unittest.TestCase):
    wait_for = protocol_tests.DeviceTests.wait_for

    def test_demo_runs_normal_confirmation_path_without_real_serial(self):
        events = queue.Queue()
        simulation = DemoSerial("DPS-150P")
        device = Device(events, serial_factory=lambda **kwargs: simulation)
        device.start("DEMO")
        try:
            result = self.wait_for(events, "connected")
            self.assertEqual(result["model"], "DPS-150P")
            self.assertFalse(result["output"])
            device.request("set", 6, .2)
            self.assertEqual(self.wait_for(events, "confirmed")["set_volts"], 6)
            device.request("on")
            self.assertTrue(self.wait_for(events, "confirmed")["output"])
            device.request("off")
            self.assertFalse(self.wait_for(events, "confirmed")["output"])
            self.assertEqual(simulation.port, "DEMO")
        finally:
            device.close()
            device.thread.join(3)


class LayoutTests(unittest.TestCase):
    setUp, tearDown = app_tests.AppTests.setUp, app_tests.AppTests.tearDown

    def test_short_window_can_reach_bottom_controls(self):
        self.root.deiconify()
        self.root.geometry("1000x640")
        self.root.update()
        area = self.app.scroll_area
        self.assertTrue(area.bar.winfo_manager())
        area.canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertGreater(area.canvas.yview()[0], 0)
        bottom = area.content.winfo_y() + area.content.winfo_height()
        self.assertLessEqual(bottom, area.canvas.winfo_height() + 1)
