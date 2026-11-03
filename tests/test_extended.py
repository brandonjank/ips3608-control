import queue
import struct
import unittest

from ips3608.device import Device
from ips3608.protocol import (Frame, PROTECTIONS, brightness_command, decode,
                             metering_command, preset_commands, protection_commands,
                             validate_protections)
from ips3608.programs import (Runner, Step, parse_steps, program_from_dict,
                             program_to_dict, sweep_steps)
import test_protocol
from test_protocol import FakeSerial, response


# Snapshot captured from this user's V1.3 supply. Official software offsets
# were used to decode it, rather than the conflicting community maps.
CAPTURE = bytes.fromhex(
    "000040420000A0400000803F00000000000000000000000055401E42"
    "0000803FC0CCCC3D0000A0400000803F0000A0400000803F0000A0400000803F"
    "0000A0400000803F0000A0400000803F00001042333303419A99934300007042"
    "000020420E0E00000000000000000000000101000010423333034100001042"
    "33330341000090430000C64200004042")


class ExtendedProtocolTests(unittest.TestCase):
    def test_official_snapshot_offsets(self):
        self.assertEqual(len(CAPTURE), 139)
        state = decode(Frame(0xA1, 0xFF, CAPTURE))
        self.assertEqual(state["input_volts"], 48)
        self.assertAlmostEqual(state["temperature"], 39.56, places=1)
        self.assertEqual(state["ovp"], 36)
        self.assertAlmostEqual(state["ocp"], 8.2, places=5)
        self.assertAlmostEqual(state["opp"], 295.2, places=3)
        self.assertEqual(state["otp"], 60)
        self.assertEqual(state["uvp"], 40)
        self.assertEqual(state["max_otp"], 99)
        self.assertEqual(state["brightness"], 14)
        self.assertFalse(state["metering"])
        self.assertFalse(state["output"])
        self.assertEqual(state["mode"], 1)  # Vendor: 1=CV, 0=CC.
        self.assertEqual(state["protection"], 0)
        self.assertEqual(state["device_presets"][1], (5, 1))

    def test_controls_use_vendor_registers_and_payload_sizes(self):
        values = dict(ovp=30, ocp=2, opp=100, otp=60, uvp=40)
        packets = protection_commands(values)
        self.assertEqual([p[2] for p in packets], [0xD1, 0xD2, 0xD3, 0xD4, 0xD5])
        self.assertTrue(all(p[1] == 0xB1 and p[3] == 4 for p in packets))
        self.assertEqual([p[2] for p in preset_commands(6, 24, 0.5)], [0xCF, 0xD0])
        self.assertEqual(brightness_command(14).hex(), "f1b1d6010ee5")
        self.assertEqual(metering_command(True).hex(), "f1b1d80101da")
        with self.assertRaises(ValueError):
            brightness_command(20)
        with self.assertRaises(ValueError):
            validate_protections(values, {"max_ovp": 25})
        with self.assertRaises(ValueError):
            preset_commands(7, 5, 1)

    def test_stock_overmaximum_threshold_is_preserved_when_changing_another(self):
        state = decode(Frame(0xA1, 0xFF, CAPTURE))
        values = {key: state[key] for key in PROTECTIONS}
        values["otp"] = 55
        packets = protection_commands(values, state)
        self.assertEqual([p[2] for p in packets], [0xD4])
        values["opp"] = 294
        with self.assertRaises(ValueError):
            protection_commands(values, state)


class ExtendedSerial(FakeSerial):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = bytearray(CAPTURE)

    def write(self, packet):
        if packet[1] == 0xA1 and packet[2] == 0xFF:
            self.sent.append(packet)
            struct.pack_into("<ff", self.snapshot, 4, self.volts, self.amps)
            self.snapshot[107] = int(self.enabled)
            self.buffer.extend(response(0xFF, self.snapshot))
            return len(packet)
        if packet[1] == 0xB1:
            reg, payload = packet[2], packet[4:-1]
            if reg in (0xD6, 0xD8):
                self.snapshot[96 if reg == 0xD6 else 98] = payload[0]
            elif 0xD1 <= reg <= 0xD5:
                self.snapshot[76 + (reg - 0xD1) * 4:80 + (reg - 0xD1) * 4] = payload
            elif 0xC5 <= reg <= 0xD0:
                self.snapshot[28 + (reg - 0xC5) * 4:32 + (reg - 0xC5) * 4] = payload
        return super().write(packet)


class ExtendedDeviceTests(unittest.TestCase):
    wait_for = test_protocol.DeviceTests.wait_for

    def test_all_new_writes_confirm_via_snapshot_without_toggling_output(self):
        events, port = queue.Queue(), ExtendedSerial()
        device = Device(events, serial_factory=lambda **kwargs: port)
        device.start("TEST")
        try:
            self.wait_for(events, "connected")
            actions = (("brightness", (10,), "brightness", 10),
                       ("metering", (True,), "metering", True),
                       ("protections", (dict(ovp=30, ocp=2, opp=100, otp=60, uvp=40),), "otp", 60),
                       ("preset", (3, 12, 0.5), "device_presets", None))
            for action, args, key, target in actions:
                with self.subTest(action=action):
                    device.request(action, *args)
                    value = self.wait_for(events, "confirmed")
                    self.assertEqual(value["action"], action)
                    self.assertTrue(value["output"])
                    if action == "preset":
                        self.assertEqual(value[key][2], (12, 0.5))
                    else:
                        self.assertEqual(value[key], target)
            self.assertFalse(any(p[1] == 0xB1 and p[2] == 0xDB for p in port.sent))
        finally:
            device.close()
            device.thread.join(3)

    def test_off_discards_queued_setpoints(self):
        events, port = queue.Queue(), ExtendedSerial()
        device = Device(events, serial_factory=lambda **kwargs: port)
        device.request("set", 12, 1)
        device.request("off")
        device.start("TEST")
        try:
            self.wait_for(events, "confirmed")
            self.assertFalse(port.enabled)
            self.assertEqual(port.volts, 5)
            self.assertFalse(any(p[1] == 0xB1 and p[2] in (0xC1, 0xC2) for p in port.sent))
        finally:
            device.close()
            device.thread.join(3)


class ProgramTests(unittest.TestCase):
    def test_sweeps_include_endpoints_descend_and_validate_power(self):
        self.assertEqual([s.volts for s in sweep_steps("voltage", 5, 3.3, 0.6, 1, 2)], [5, 4.4, 3.8, 3.3])
        self.assertEqual([s.amps for s in sweep_steps("current", 0, 0.3, 0.1, 5, 1)], [0, 0.1, 0.2, 0.3])
        with self.assertRaises(ValueError):
            sweep_steps("voltage", 30, 36, 1, 8, 1)
        with self.assertRaises(ValueError):
            sweep_steps("voltage", 0, 1, 0.001, 1, 1)

    def test_holds_start_after_readback_and_pause_freezes_time(self):
        runner = Runner((Step(5, 1, 2), Step(3.3, 0.5, 1)), cycles=2)
        self.assertEqual(runner.tick(0), (5, 1))
        self.assertIsNone(runner.tick(10))  # No readback, so no hold/advance.
        runner.confirmed(10)
        self.assertIsNone(runner.tick(11))
        runner.pause(11.5)
        self.assertIsNone(runner.tick(100))
        runner.resume(100)
        self.assertIsNone(runner.tick(100.4))
        self.assertEqual(runner.tick(100.6), (3.3, 0.5))
        runner.confirmed(101)
        self.assertEqual(runner.tick(102), (5, 1))
        runner.stop()
        self.assertFalse(runner.active)
        self.assertIsNone(runner.tick(1000))

    def test_paused_inflight_command_holds_and_completes_once(self):
        runner = Runner((Step(5, 1, 1),), finish_off=False)
        runner.tick(0)
        runner.pause(0.1)
        runner.confirmed(0.2)
        runner.resume(10)
        self.assertIsNone(runner.tick(11))
        self.assertEqual(runner.phase, "complete")
        self.assertFalse(runner.finish_off)

    def test_files_and_text_validate_before_any_execution(self):
        steps = parse_steps("# volts, amps, seconds\n5, 1, 2\n3.3, .1, 1")
        document = program_to_dict(steps, 3, True)
        self.assertEqual(program_from_dict(document), (steps, 3, True))
        for text in ("", "5,1", "5,1,0", "nan,1,1", "36,8,1"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_steps(text)
        with self.assertRaises(ValueError):
            Runner(steps, cycles=1001)
