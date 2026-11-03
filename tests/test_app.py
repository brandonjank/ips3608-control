"""Native UI regression checks, without opening a serial port."""

import time
import tkinter as tk
import unittest
from unittest.mock import Mock

from ips3608.app import App
from ips3608.programs import Step


class AppTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.device = Mock()
        now = time.monotonic()
        self.app.connected = True
        self.app.last_sample = now
        self.app.state = {"model": "IPS3608", "output": True, "set_volts": 5, "set_amps": 1,
                          "status_monotonic": now, "settings_monotonic": now}
        self.app.voltage.set("3.30")
        self.app.current.set("0.500")

    def tearDown(self):
        for panel in (self.app.device_panel, self.app.program_panel):
            if panel and panel.window.winfo_exists():
                panel.close()
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def test_apply_enabled_and_sends_settings_while_output_on(self):
        self.app.update_controls()
        self.assertEqual(self.app.apply_btn["state"], "normal")
        self.assertEqual(self.app.on_btn["state"], "disabled")
        self.app.apply()
        self.app.device.request.assert_called_once_with("set", 3.3, 0.5)
        self.assertEqual(self.app.pending, "set")

    def test_statistics_reset_preserves_history_session_and_recorder(self):
        sample = {"monotonic": time.monotonic(), "volts": 5, "amps": 1, "watts": 5}
        self.app.history.append(sample)
        self.app.session.add(sample)
        self.app.statistics.add(sample)
        recorder = self.app.recorder = Mock()
        recorder.rows = 1
        recorder.path.name = "test.csv"
        self.app.reset_statistics()
        self.assertEqual(self.app.statistics.count, 0)
        self.assertEqual(self.app.stat_labels["volts"]["average"]["text"], "—")
        self.assertEqual(len(self.app.history), 1)
        self.assertEqual(self.app.session.samples, 1)
        self.assertIs(self.app.recorder, recorder)
        recorder.close.assert_not_called()
        self.app.device.request.assert_not_called()

    def test_program_interlocks_manual_controls_and_finishes_with_explicit_off(self):
        self.app.start_program((Step(3.3, 0.5, 0.1),), 1, True)
        self.app.advance_program()
        self.app.device.request.assert_called_once_with("set", 3.3, 0.5)
        self.app.update_controls()
        self.assertEqual(self.app.apply_btn["state"], "disabled")
        self.app.pending = None
        self.app.program.confirmed(time.monotonic() - 0.2)
        self.app.advance_program()
        self.assertEqual(self.app.program.phase, "complete")
        self.assertEqual(self.app.device.request.call_args.args, ("off",))

    def test_program_requires_explicit_output_on_and_aborts_on_stale_data(self):
        self.app.state["output"] = False
        with self.assertRaises(ValueError):
            self.app.start_program((Step(5, 1, 1),), 1, True)
        self.app.state["output"] = True
        self.app.start_program((Step(5, 1, 1),), 1, True)
        self.app.last_sample -= 10
        self.app.advance_program()
        self.assertFalse(self.app.program.active)
        self.app.device.request.assert_called_once_with("off")

    def test_extended_panels_build_and_validate_sweeps_without_device_writes(self):
        self.app.state.update(ovp=36, ocp=8.2, opp=295.2, otp=60, uvp=40,
                              brightness=14, metering=False, device_presets=[(5, 1)] * 6)
        self.app.open_device_settings()
        panel = self.app.device_panel
        self.assertEqual(panel.protection_vars["otp"].get(), "60")
        panel.protection_vars["otp"].set("55")
        panel.apply_protections()
        self.assertEqual(self.app.device.request.call_args.args[0], "protections")
        self.assertEqual(self.app.device.request.call_args.args[1]["otp"], 55)
        self.app.pending = None
        self.app.open_programs()
        program = self.app.program_panel
        for name, value in dict(start=0, end=5, increment=1, fixed=1, seconds=2).items():
            program.sweep[name].set(str(value))
        self.app.device.request.reset_mock()
        program.generate()
        document = program.validate()
        self.assertEqual(len(document["steps"]), 6)
        self.app.device.request.assert_not_called()
