import csv
from pathlib import Path
import queue
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from ips3608.device import Device
from ips3608.protocol import (CONNECT, DISCONNECT, Frame, FrameParser, command,
                             decode, output_command, query, setpoint_commands,
                             validate_setpoints)
from ips3608.recording import Recorder, Session, Statistics


def response(register, data):
    packet = command(0xA1, register, data)
    return b"\xf0" + packet[1:]


class ProtocolTests(unittest.TestCase):
    def test_known_packets(self):
        self.assertEqual(CONNECT.hex(), "f1c100010102")
        self.assertEqual(DISCONNECT.hex(), "f1c100010001")
        self.assertEqual(query(0xFF).hex(), "f1a1ff010000")
        self.assertEqual(output_command(False).hex(), "f1b1db0100dc")
        self.assertEqual(output_command(True).hex(), "f1b1db0101dd")
        voltage, current = setpoint_commands(5, 1)
        self.assertEqual(voltage, command(0xB1, 0xC1, struct.pack("<f", 5)))
        self.assertEqual(current, command(0xB1, 0xC2, struct.pack("<f", 1)))

    def test_fragmentation_garbage_and_checksum_recovery(self):
        good = response(0xC3, struct.pack("<fff", 5, 0.1, 0.5))
        bad = good[:-1] + bytes((good[-1] ^ 1,))
        parser = FrameParser()
        self.assertEqual(parser.feed(b"garbage" + good[:3]), [])
        frames = parser.feed(good[3:] + bad + response(0xE0, b"V1.3"))
        self.assertEqual(len(frames), 2)
        self.assertEqual(decode(frames[0])["volts"], 5)
        self.assertEqual(decode(frames[1]), {"firmware": "V1.3"})
        self.assertEqual(parser.bad_checksums, 1)

    def test_non_finite_measurements_and_unknown_fields_are_ignored(self):
        self.assertEqual(decode(Frame(0xA1, 0xC3, struct.pack("<fff", float("nan"), 0, 0))), {})
        self.assertEqual(decode(Frame(0xA1, 0xC4, struct.pack("<f", 39))), {"temperature": 39})
        self.assertEqual(decode(Frame(0xA1, 0xE9, b"\x00")), {})
        self.assertEqual(decode(Frame(0xB1, 0xDB, b"\x01")), {})

    def test_identity_and_snapshot_from_real_unit(self):
        self.assertEqual(decode(Frame(0xA1, 0xDE, b"IPS3608"))["model"], "IPS3608")
        self.assertEqual(decode(Frame(0xA1, 0xDF, b"V1.1"))["hardware"], "V1.1")
        self.assertEqual(decode(Frame(0xA1, 0xFF, bytes.fromhex("000040420000A0400000803F"))),
                         {"set_volts": 5, "set_amps": 1})
        self.assertEqual(decode(Frame(0xA1, 0xDB, b"\x00")), {"output": False})

    def test_limits_do_not_silently_clamp(self):
        for volts, amps in ((37, 1), (5, 9), (-1, 1), (5, -1), (36, 8),
                            (float("inf"), 1), (5, float("nan"))):
            with self.subTest(volts=volts, amps=amps), self.assertRaises(ValueError):
                validate_setpoints(volts, amps)
        self.assertEqual(validate_setpoints(5.004, 0.1004), (5, 0.1))
        self.assertEqual(validate_setpoints(0, 0), (0, 0))


class RecordingTests(unittest.TestCase):
    def sample(self, instant, amps=1, watts=5):
        return {"monotonic": instant, "timestamp": "2026-11-02T12:00:00.000-08:00",
                "volts": 5, "amps": amps, "watts": watts,
                "output": False, "model": "IPS3608"}

    def test_energy_skips_missing_intervals(self):
        session = Session()
        for instant in (0, 1, 2, 20, 21):
            session.add(self.sample(instant))
        self.assertEqual(session.samples, 5)
        self.assertEqual(session.duration, 3)
        self.assertAlmostEqual(session.amp_hours, 3 / 3600)
        self.assertAlmostEqual(session.watt_hours, 15 / 3600)

    def test_statistics_track_each_metric_and_reset_to_empty(self):
        stats = Statistics()
        samples = [dict(volts=5, amps=0, watts=0),
                   dict(volts=3, amps=2, watts=6),
                   dict(volts=4, amps=1, watts=4)]
        for sample in samples:
            stats.add(sample)
        self.assertEqual(stats.count, 3)
        self.assertEqual(stats.minimum, dict(volts=3, amps=0, watts=0))
        self.assertEqual(stats.maximum, dict(volts=5, amps=2, watts=6))
        self.assertEqual(stats.average["volts"], 4)
        self.assertEqual(stats.average["amps"], 1)
        self.assertAlmostEqual(stats.average["watts"], 10 / 3)
        stats.add(dict(volts=float("nan"), amps=1, watts=5))
        self.assertEqual(stats.count, 3)
        stats.reset()
        self.assertEqual(stats.count, 0)
        self.assertEqual(stats.minimum, {})
        self.assertEqual(stats.maximum, {})
        self.assertEqual(stats.average, {})
        stats.add(dict(volts=12, amps=0.5, watts=6))
        self.assertEqual(stats.minimum, stats.maximum)
        self.assertEqual(stats.average, dict(volts=12, amps=0.5, watts=6))

    def test_csv_flushes_preserves_zero_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "measurement.csv"
            recorder = Recorder(path)
            recorder.add(self.sample(0))
            recorder.add(self.sample(1))
            # Read while still recording: each sample must already be on disk.
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            recorder.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["output_enabled"], "0")
            self.assertEqual(rows[1]["elapsed_s"], "1.000")
            self.assertEqual(rows[0]["hardware"], "")
            with self.assertRaises(FileExistsError):
                Recorder(path)


class FakeSerial:
    """Responds only to reads; tracks every outbound packet."""
    def __init__(self, **kwargs):
        self.sent = []
        self.buffer = bytearray()
        self.is_open = False
        self.enabled = True  # Connection must preserve an already-running supply.
        self.volts, self.amps = 5.0, 1.0

    def open(self):
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.buffer)

    def write(self, packet):
        self.sent.append(packet)
        if packet == CONNECT:
            self.buffer.extend(response(0xC3, struct.pack("<fff", 5, 0, 0)))
        elif packet[1] == 0xA1:
            reg = packet[2]
            data = {0xDE: b"IPS3608", 0xDF: b"V1.1", 0xE0: b"V1.3",
                    0xDB: bytes((int(self.enabled),)),
                    0xFF: struct.pack("<fff", 48, self.volts, self.amps)}.get(reg)
            if data is not None:
                self.buffer.extend(response(reg, data))
        elif packet[1] == 0xB1:
            if packet[2] == 0xC1:
                self.volts = struct.unpack("<f", packet[4:8])[0]
            elif packet[2] == 0xC2:
                self.amps = struct.unpack("<f", packet[4:8])[0]
            elif packet[2] == 0xDB:
                self.enabled = bool(packet[4])
        return len(packet)

    def read(self, count):
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        if not data:
            time.sleep(0.01)
        return data

    def close(self):
        self.is_open = False


class DeviceTests(unittest.TestCase):
    def wait_for(self, events, name):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            event, values = events.get(timeout=3)
            if event == "error":
                self.fail(values["message"])
            if event == name:
                return values
        self.fail(f"No {name} event")

    def test_connect_disconnect_preserves_output_and_setpoints(self):
        events, port = queue.Queue(), FakeSerial()
        device = Device(events, serial_factory=lambda **kw: port)
        device.start("TEST")
        try:
            identity = self.wait_for(events, "connected")
            self.assertTrue(identity["output"])
            self.assertEqual(identity["set_volts"], 5)
        finally:
            device.close()
            device.thread.join(3)
        self.assertFalse(device.thread.is_alive())
        self.assertIn(DISCONNECT, port.sent)
        self.assertFalse(any(packet[1] in (0xB0, 0xB1) for packet in port.sent))
        self.assertTrue(port.enabled)

    def test_live_setpoints_preserve_output_and_order_intermediate_limits(self):
        events, port = queue.Queue(), FakeSerial()
        device = Device(events, serial_factory=lambda **kw: port)
        device.start("TEST")
        try:
            self.wait_for(events, "connected")
            for volts, amps, order in ((12, 0.2, [0xC2, 0xC1]),
                                       (3.3, 2, [0xC1, 0xC2])):
                with self.subTest(volts=volts, amps=amps):
                    before = len(port.sent)
                    device.request("set", volts, amps)
                    result = self.wait_for(events, "confirmed")
                    self.assertEqual(result["action"], "set")
                    self.assertAlmostEqual(result["set_volts"], volts, places=5)
                    self.assertAlmostEqual(result["set_amps"], amps, places=5)
                    self.assertTrue(result["output"])
                    writes = [p for p in port.sent[before:] if p[1] == 0xB1]
                    self.assertEqual([p[2] for p in writes], order)
                    self.assertTrue(port.enabled)
            self.assertFalse(any(p[2] == 0xDB and p[1] == 0xB1 for p in port.sent))
        finally:
            device.close()
            device.thread.join(3)

    def test_setpoints_and_output_require_distinct_explicit_actions(self):
        events, port = queue.Queue(), FakeSerial()
        port.enabled = False
        device = Device(events, serial_factory=lambda **kw: port)
        device.start("TEST")
        try:
            self.wait_for(events, "connected")
            device.request("set", 3.3, 0.2)
            result = self.wait_for(events, "confirmed")
            self.assertEqual(result["action"], "set")
            self.assertAlmostEqual(result["set_volts"], 3.3, places=5)
            self.assertFalse(port.enabled)
            device.request("on")
            result = self.wait_for(events, "confirmed")
            self.assertTrue(result["output"])
            device.request("off")
            result = self.wait_for(events, "confirmed")
            self.assertFalse(result["output"])
        finally:
            device.close()
            device.thread.join(3)

    def test_stale_measurements_block_on_but_allow_off(self):
        events, port = queue.Queue(), FakeSerial()
        port.enabled = False
        device = Device(events, serial_factory=lambda **kw: port)
        with patch("ips3608.device.STALE_SECONDS", 0.05):
            device.start("TEST")
            try:
                self.wait_for(events, "connected")
                time.sleep(0.08)
                device.request("on")
                result = self.wait_for(events, "warning")
                self.assertIn("fresh readings", result["message"])
                self.assertNotIn(output_command(True), port.sent)
                device.request("off")
                result = self.wait_for(events, "confirmed")
                self.assertEqual(result["action"], "off")
            finally:
                device.close()
                device.thread.join(3)


if __name__ == "__main__":
    unittest.main()
