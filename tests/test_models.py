"""Model and input-ceiling integration tests. All serial devices are simulated."""

import asyncio
import csv
import json
import queue
import struct
import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import Mock

from mcp import Client

from ips3608.agent_bridge import capabilities
from ips3608.device import Device
from ips3608.mcp_server import create_server
from ips3608.models import MODELS, current_reading, model_for, output_limits
from ips3608.programs import Step, Runner, parse_steps, program_from_dict, program_to_dict, sweep_steps
from ips3608.protocol import Frame, decode, validate_setpoints, preset_commands, validate_protections
from ips3608.recording import Recorder
from test_protocol import response
from test_extended import ExtendedSerial
import test_protocol as protocol_tests
import test_app as app_tests
import test_agent as agent_tests


def dps_state(model="DPS-150", input_volts=32, max_volts=30):
    return dict(model=model, input_volts=input_volts, max_volts=max_volts, max_amps=5.1,
                max_ovp=30, max_ocp=5.1, max_opp=150, max_otp=99, max_uvp=32,
                ovp=30, ocp=5.1, opp=150, otp=60, uvp=5,
                set_volts=5, set_amps=1, output=True)


class ModelSerial(ExtendedSerial):
    def __init__(self, model="DPS-150", input_volts=32):
        super().__init__()
        self.model = model
        struct.pack_into("<f", self.snapshot, 0, input_volts)
        struct.pack_into("<5f", self.snapshot, 76, 30, 5.1, 150, 60, 5)
        struct.pack_into("<7f", self.snapshot, 111, 30, 5.1, 30, 5.1, 150, 99, 32)

    def write(self, packet):
        if packet[1] == 0xA1:
            reg = packet[2]
            value = {0xDE: self.model.encode(), 0xE2: self.snapshot[111:115],
                     0xE3: self.snapshot[115:119], 0xC0: self.snapshot[:4]}.get(reg)
            if value is not None:
                self.sent.append(packet)
                self.buffer.extend(response(reg, value))
                return len(packet)
        return super().write(packet)


class ModelLimitTests(unittest.TestCase):
    def test_ratings_and_unknown_identity(self):
        self.assertEqual([(m.volts, m.amps, m.watts) for m in MODELS.values()],
                         [(36, 8, 285), (30, 5, 150), (30, 5, 150)])
        self.assertEqual(model_for(" dps-150p ").name, "DPS-150P")
        for model in ("DPS-200", "DPS-150P Plus", None):
            with self.assertRaises(ValueError):
                model_for(model)

    def test_each_profile_enforces_rating_and_reported_ceiling(self):
        for name in ("DPS-150", "DPS-150P"):
            state = dps_state(name)
            with self.subTest(model=name):
                self.assertEqual(validate_setpoints(30, 5, state), (30, 5))
                for v, a in ((30.01, 1), (1, 5.001), (36, 8)):
                    with self.assertRaises(ValueError):
                        validate_setpoints(v, a, state)
                state.update(input_volts=12, max_volts=30, max_amps=2)
                self.assertAlmostEqual(output_limits(state)["voltage_V"], 11.8)
                self.assertEqual(validate_setpoints(11.8, 2, state), (11.8, 2))
                for v, a in ((11.81, 1), (5, 2.001)):
                    with self.assertRaises(ValueError):
                        validate_setpoints(v, a, state)
                state["max_volts"] = 10
                self.assertEqual(output_limits(state)["voltage_V"], 10)
                # Persistent recipes may target a future, stronger input source.
                self.assertEqual(len(preset_commands(1, 30, 5, state)), 2)
                with self.assertRaises(ValueError):
                    preset_commands(1, 36, 1, state)

    def test_unavailable_or_corrupt_limits_block_live_writes(self):
        for key in ("input_volts", "max_volts", "max_amps"):
            for bad in (None, float("nan"), -1, True):
                state = dps_state()
                state[key] = bad
                with self.subTest(field=key, bad=bad), self.assertRaises(ValueError):
                    validate_setpoints(5, 1, state)
        for reg, field in ((0xE2, "max_volts"), (0xE3, "max_amps"), (0xC0, "input_volts")):
            self.assertEqual(decode(Frame(0xA1, reg, struct.pack("<f", 12))), {field: 12})
            self.assertEqual(decode(Frame(0xA1, reg, struct.pack("<f", float("nan")))), {field: None})

    def test_trip_limits_use_dps_snapshot_not_ips_ranges(self):
        state = dps_state()
        values = {key: state[key] for key in ("ovp", "ocp", "opp", "otp", "uvp")}
        values["opp"] = 151
        with self.assertRaises(ValueError):
            validate_protections(values, state)
        values["opp"] = 100
        self.assertEqual(validate_protections(values, state)["opp"], 100)
        state["max_otp"] = None
        values["otp"] = 55
        with self.assertRaises(ValueError):
            validate_protections(values, state)

    def test_program_paths_validate_selected_model_and_live_input(self):
        state = dps_state()
        for factory in (lambda: parse_steps("31,1,1", state),
                        lambda: sweep_steps("voltage", 29, 31, 1, 1, 1, state),
                        lambda: Runner([Step(31, 1, 1)], state=state),
                        lambda: program_from_dict(dict(version=1, cycles=1, finish_off=True,
                                                       steps=[dict(volts=31, amps=1, seconds=1)]), state)):
            with self.assertRaises(ValueError):
                factory()
        steps = sweep_steps("voltage", 5, 15, 5, 1, 1, state)
        program_to_dict(steps, 1, True, state)
        state["input_volts"] = 12
        with self.assertRaises(ValueError):
            Runner(steps, state=state, live=True)

    def test_microamps_display_without_changing_setpoint_resolution(self):
        model = model_for("DPS-150P")
        self.assertEqual(current_reading(.000005, model), ("5.0", "µA"))
        self.assertEqual(current_reading(.125, model), ("125.000", "mA"))
        self.assertEqual(current_reading(.000005, model_for("IPS3608")), ("0.000", "A"))
        self.assertEqual(validate_setpoints(5, .0014, dps_state("DPS-150P")), (5, .001))

    def test_csv_keeps_sub_microamp_data_in_amps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "microamps.csv"
            recorder = Recorder(path)
            recorder.add(dict(model="DPS-150P", monotonic=1, timestamp="test", volts=5,
                              amps=.0000001, watts=.0000005))
            recorder.close()
            with path.open() as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(float(row["current_A"]), .0000001)
            self.assertEqual(float(row["power_W"]), .0000005)


class ModelDeviceTests(unittest.TestCase):
    wait_for = protocol_tests.DeviceTests.wait_for

    def test_both_dps_models_detect_and_confirm_live_writes(self):
        for name in ("DPS-150", "DPS-150P"):
            events, port = queue.Queue(), ModelSerial(name)
            device = Device(events, serial_factory=lambda **kwargs: port)
            device.start("TEST")
            try:
                result = self.wait_for(events, "connected")
                self.assertEqual(result["model"], name)
                self.assertTrue(port.enabled)
                self.assertFalse(any(p[1] == 0xB1 for p in port.sent))
                with self.assertRaises(ValueError):
                    device.request("set", 36, 1)
                device.request("set", 12, .2)
                result = self.wait_for(events, "confirmed")
                self.assertEqual(result["action"], "set")
                self.assertTrue(result["output"])
                self.assertAlmostEqual(result["set_volts"], 12)
                device.request("preset", 1, 30, 5)
                self.assertEqual(self.wait_for(events, "confirmed")["action"], "preset")
                self.assertFalse(any(p[1] == 0xB1 and p[2] == 0xDB for p in port.sent))
            finally:
                device.close()
                device.thread.join(3)

    def test_worker_rechecks_queued_targets_after_limit_change(self):
        events, port = queue.Queue(), ModelSerial()
        device = Device(events, serial_factory=lambda **kwargs: port)
        device.start("TEST")
        try:
            self.wait_for(events, "connected")
            port.buffer.extend(response(0xE2, struct.pack("<f", 10)))
            while self.wait_for(events, "state").get("max_volts") != 10:
                pass
            before = len(port.sent)
            # A target that was approved when an earlier ceiling was higher.
            device.actions.put((1, next(device.sequence), "set", (20, 1)))
            self.assertIn("10 V", self.wait_for(events, "warning")["message"])
            self.assertFalse(any(p[1] == 0xB1 for p in port.sent[before:]))
            self.assertTrue(device.thread.is_alive())
        finally:
            device.close()
            device.thread.join(3)

    def test_unknown_model_never_becomes_control_ready(self):
        events, port = queue.Queue(), ModelSerial("DPS-200")
        device = Device(events, serial_factory=lambda **kwargs: port)
        device.start("TEST")
        device.thread.join(3)
        results = []
        while not events.empty():
            results.append(events.get_nowait())
        self.assertTrue(any(e == "error" and "Unsupported" in v["message"] for e, v in results))
        self.assertFalse(any(e == "connected" for e, _ in results))
        self.assertFalse(any(p[1] == 0xB1 for p in port.sent))


class ModelAppTests(unittest.TestCase):
    setUp, tearDown = app_tests.AppTests.setUp, app_tests.AppTests.tearDown

    def prepare(self):
        self.app.state.update(dps_state("DPS-150P", input_volts=12))
        self.app.state["amps"] = .000005
        self.app.voltage.set("5")
        self.app.current.set("1")

    def test_ui_identity_limits_and_microamp_statistics(self):
        self.prepare()
        self.app.statistics.add(dict(volts=5, amps=.000005, watts=.000025))
        self.app.update_controls()
        self.assertEqual(self.app.model_label["text"], "DPS-150P")
        self.assertIn("150 W", self.app.limits_label["text"])
        self.assertIn("11.80 V", self.app.limits_label["text"])
        self.assertEqual(self.app.readings["amps"]["text"], "5.0")
        self.assertEqual(self.app.reading_units["amps"]["text"], "µA")
        self.assertEqual(self.app.stat_labels["amps"]["average"]["text"], "5.0 µA")

    def test_manual_presets_and_programs_cannot_bypass_model_limits(self):
        self.prepare()
        self.app.voltage.set("24")
        self.app.apply()
        self.app.device.request.assert_not_called()
        self.assertFalse(self.app.send_device("preset", 1, 36, 1))
        self.assertFalse(self.app.send_device("protections", dict(ovp=31, ocp=5, opp=150, otp=60, uvp=5)))
        with self.assertRaises(ValueError):
            self.app.start_program([Step(12, 1, 1)], 1, True)
        self.app.device.request.assert_not_called()

    def test_output_enable_rejects_unavailable_stored_voltage(self):
        self.prepare()
        self.app.state.update(output=False, set_volts=24)
        self.app.voltage.set("24")
        self.app.output_on()
        self.app.device.request.assert_not_called()
        self.app.update_controls()
        self.assertEqual(self.app.on_btn["state"], "disabled")

    def test_input_drop_aborts_active_program_and_requests_off(self):
        self.prepare()
        self.app.state["input_volts"] = 32
        self.app.start_program([Step(20, 1, 10)], 1, True)
        self.app.advance_program()
        self.app.pending = None
        self.app.program.confirmed(time.monotonic())
        self.app.state["input_volts"] = 12
        self.app.advance_program()
        self.assertFalse(self.app.program.active)
        self.assertEqual(self.app.device.request.call_args.args, ("off",))


class ModelBridgeTests(unittest.TestCase):
    setUp, tearDown, submit = agent_tests.BridgeTests.setUp, agent_tests.BridgeTests.tearDown, agent_tests.BridgeTests.submit

    def test_bridge_reports_model_and_enforces_limits_with_mock_worker(self):
        self.app.state.update(dps_state("DPS-150P", input_volts=12))
        status = self.submit("status").future.result()
        self.assertEqual(status["model_profile"]["power_W"], 150)
        self.assertEqual(status["limits"]["current_A"], 5)
        self.assertAlmostEqual(status["limits"]["voltage_V"], 11.8)
        for action, params in (("setpoints", dict(voltage_v=12)),
                               ("preset", dict(slot=1, voltage_v=31, current_a=1)),
                               ("program_start", dict(steps=[dict(volts=20, amps=1, seconds=1)]))):
            with self.assertRaises(RuntimeError):
                self.submit(action, **params).future.result()
        self.app.device.request.assert_not_called()


class ModelMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_planning_limits_and_explicit_offline_model(self):
        ipc = Mock()
        ipc.call.return_value = {"connected": True, "fresh": True, "device": dps_state("DPS-150P", input_volts=12)}
        async with Client(create_server(ipc)) as client:
            params = dict(axis="voltage", start=5, end=15, increment=5, fixed=1, seconds=1)
            result = await client.call_tool("preview_sweep", params)
            self.assertTrue(result.is_error)
            self.assertIn("11.8 V", result.content[0].text)
            result = await client.call_tool("preview_sweep", dict(params, model="DPS-150P"))
            self.assertFalse(result.is_error)
            data = result.structured_content or json.loads(result.content[0].text)
            self.assertEqual(data["model"], "DPS-150P")
            self.assertEqual(data["validation_scope"], "rated")
            result = await client.call_tool("preview_sweep", dict(params, end=31, model="DPS-150"))
            self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
