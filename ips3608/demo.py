"""An explicitly selected, entirely in-memory supply for trying the app."""

import math
import struct
import time
from .models import model_for
from .protocol import CONNECT, DISCONNECT, PROTECTIONS, command


class DemoSerial:
    def __init__(self, model="IPS3608", **kwargs):
        self.model = model_for(model)
        self.port = "DEMO"
        self.is_open = self.streaming = self.enabled = False
        self.buffer = bytearray()
        self.volts, self.amps = 5., 1.
        self.brightness, self.metering = 12, False
        self.presets = [(v, .5) for v in (1.8, 3.3, 5, 9, 12, 24)]
        self.protections = dict(ovp=self.model.volts, ocp=self.model.amps,
                                opp=self.model.watts, otp=60., uvp=5. if self.model.input_limited else 40.)
        self.started = self.last = time.monotonic()

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    @property
    def in_waiting(self):
        return len(self.buffer)

    def reply(self, register, payload):
        self.buffer.extend(b"\xf0" + command(0xA1, register, payload)[1:])

    def measured(self):
        t = time.monotonic() - self.started
        voltage = self.volts + .003 * math.sin(t) if self.enabled else 0.
        current = min(self.amps, .22 + .08 * math.sin(t / 3) + .04 * math.sin(t * 2)) if self.enabled else 0.
        return voltage, current, voltage * current

    def write(self, packet):
        if packet == CONNECT:
            self.streaming = True
            self.reply(0xC3, struct.pack("<fff", *self.measured()))
        elif packet == DISCONNECT:
            self.streaming = False
        elif packet[1] == 0xB1:
            reg, payload = packet[2], packet[4:-1]
            if reg in (0xC1, 0xC2):
                setattr(self, "volts" if reg == 0xC1 else "amps", struct.unpack("<f", payload)[0])
            elif reg == 0xDB:
                self.enabled = bool(payload[0])
            elif reg in (0xD6, 0xD8):
                setattr(self, "brightness" if reg == 0xD6 else "metering", payload[0])
            elif 0xC5 <= reg <= 0xD0:
                index, axis = divmod(reg - 0xC5, 2)
                pair = list(self.presets[index])
                pair[axis] = struct.unpack("<f", payload)[0]
                self.presets[index] = tuple(pair)
            elif 0xD1 <= reg <= 0xD5:
                self.protections[list(PROTECTIONS)[reg - 0xD1]] = struct.unpack("<f", payload)[0]
        elif packet[1] == 0xA1:
            reg = packet[2]
            maximum = min(self.model.volts, 23.8) if self.model.input_limited else self.model.volts
            payload = {0xDE: self.model.name.encode(), 0xDF: b"Demo", 0xE0: b"Simulated", 0xE1: b"\x01",
                       0xDB: bytes((int(self.enabled),)), 0xE2: struct.pack("<f", maximum),
                       0xE3: struct.pack("<f", self.model.amps),
                       0xC0: struct.pack("<f", 24. if self.model.input_limited else 48.)}.get(reg)
            if reg == 0xFF:
                payload = bytearray(139)
                struct.pack_into("<7f", payload, 0, 24. if self.model.input_limited else 48.,
                                 self.volts, self.amps, *self.measured(), 31.)
                for i, pair in enumerate(self.presets):
                    struct.pack_into("<ff", payload, 28 + i * 8, *pair)
                struct.pack_into("<5f", payload, 76, *self.protections.values())
                payload[96], payload[98] = self.brightness, int(self.metering)
                payload[107], payload[109] = int(self.enabled), 1
                struct.pack_into("<7f", payload, 111, maximum, self.model.amps,
                                 self.model.volts, self.model.amps, self.model.watts, 99., 32. if self.model.input_limited else 48.)
            if payload is not None:
                self.reply(reg, payload)
        return len(packet)

    def read(self, count):
        if self.streaming and time.monotonic() - self.last >= .5:
            self.last = time.monotonic()
            self.reply(0xC3, struct.pack("<fff", *self.measured()))
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        if not result:
            time.sleep(.01)
        return result
