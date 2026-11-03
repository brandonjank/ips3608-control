"""Single-owner serial worker. No automatic output on/off on connect or close."""

from datetime import datetime
import itertools
import queue
import threading
import time

import serial
from serial.tools import list_ports

from .protocol import (CONNECT, DISCONNECT, FrameParser, decode, output_command,
                       query, setpoint_commands, validate_setpoints, PROTECTIONS,
                       brightness_command, metering_command, preset_commands,
                       protection_commands, validate_protections, validate_action)
from .models import model_for, state_model

STALE_SECONDS = 2.5


def ports() -> list[str]:
    return [p.device for p in sorted(list_ports.comports(), key=lambda p: p.device)
            if p.vid == 0x2E3C and p.pid == 0x5740]


class Device:
    def __init__(self, events: queue.Queue, serial_factory=serial.Serial):
        self.events = events
        self.serial_factory = serial_factory
        self.actions = queue.PriorityQueue()
        self.sequence = itertools.count()
        self.stop = threading.Event()
        self.thread = None
        self.state = {}

    def start(self, port: str):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Already connected")
        self.stop.clear()
        self.state = {}
        self.thread = threading.Thread(target=self._run, args=(port,), daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()

    def request(self, action: str, *args):
        if action not in ("set", "on", "off", "refresh", "protections", "preset", "brightness", "metering"):
            raise ValueError("Unknown action")
        args = validate_action(action, args, self.state or None)
        self.actions.put((0 if action == "off" else 1, next(self.sequence), action, args))

    def emit(self, event: str, **values):
        self.events.put((event, values))

    def _run(self, port):
        connection = None
        state = {}
        last_telemetry = 0.0
        last_status = 0.0
        last_settings = 0.0
        ready = False
        pending = None
        parser = FrameParser()
        try:
            # No reset pulse: DTR is set before opening the USB CDC port.
            connection = self.serial_factory(port=None, baudrate=115200, timeout=0.1,
                                             write_timeout=0.5)
            connection.port = port
            connection.dtr = True
            connection.rts = False
            connection.open()
            connection.write(CONNECT)
            for register in (0xDE, 0xDF, 0xE0, 0xE1, 0xDB, 0xFF, 0xE2, 0xE3):
                if self.stop.is_set():
                    break
                connection.write(query(register))
                self.stop.wait(0.04)
            started = time.monotonic()
            while not self.stop.is_set():
                data = connection.read(min(max(connection.in_waiting, 1), 4096))
                now = time.monotonic()
                for frame in parser.feed(data):
                    updates = decode(frame)
                    if "model" in updates:
                        model = model_for(updates["model"])
                        if state.get("model") and model.name != state["model"]:
                            raise RuntimeError("Device identity changed during the connection.")
                        updates["model"] = model.name
                    if "output" in updates:
                        updates["status_monotonic"] = now
                    if "set_volts" in updates:
                        updates["settings_monotonic"] = now
                    state.update(updates)
                    self.state = dict(state)
                    if "volts" in updates:
                        last_telemetry = now
                        self.emit("sample", **state, monotonic=now,
                                  timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"))
                    if updates:
                        self.emit("state", **state)
                    if (not ready and state.get("model") and
                            "set_volts" in state and "output" in state and last_telemetry):
                        ready = True
                        self.emit("connected", port=port, **state)
                    if pending:
                        kind, target, deadline = pending
                        confirmed = frame.category == 0xA1 and ((
                            kind == "set" and frame.register == 0xFF and
                            abs(state.get("set_volts", -999) - target[0]) < 0.011 and
                            abs(state.get("set_amps", -999) - target[1]) < 0.0011
                        ) or (
                            kind in ("on", "off") and frame.register == 0xDB and
                            state.get("output") == (kind == "on")
                        ))
                        if frame.category == 0xA1 and frame.register == 0xFF:
                            if kind == "protections":
                                confirmed = all(abs(state.get(k, -999) - v) < 0.0011 for k, v in target[0].items())
                            elif kind == "preset" and "device_presets" in state:
                                actual = state["device_presets"][target[0] - 1]
                                confirmed = abs(actual[0] - target[1]) < 0.011 and abs(actual[1] - target[2]) < 0.0011
                            elif kind == "brightness":
                                confirmed = state.get("brightness") == target[0]
                            elif kind == "metering":
                                confirmed = state.get("metering") == target[0]
                        if confirmed:
                            self.emit("confirmed", action=kind, **state)
                            pending = None
                if not ready and now - started > 4:
                    raise RuntimeError("No complete supported FNIRSI supply response. Check the cable and close other serial apps.")
                if ready and now - last_telemetry > 4:
                    raise RuntimeError("Live readings stopped. Output state is unknown; check the supply.")
                if pending and now > pending[2]:
                    self.emit("warning", message="The supply did not confirm the change. Check its display before continuing.")
                    pending = None
                try:
                    priority, sequence, action, args = self.actions.get_nowait()
                except queue.Empty:
                    action = None
                if action and action != "off" and ready:
                    try:
                        args = validate_action(action, args, state)
                    except ValueError as error:
                        self.emit("warning", message=str(error))
                        action = None
                if action and not self.stop.is_set():
                    fresh = (ready and now - last_telemetry <= STALE_SECONDS and
                             now - state.get("status_monotonic", 0) <= STALE_SECONDS and
                             now - state.get("settings_monotonic", 0) <= 4.5)
                    if not fresh and action != "off":
                        self.emit("warning", message="Wait for fresh readings before changing settings.")
                    elif pending and action != "off":
                        self.emit("warning", message="Wait for the previous change to be confirmed.")
                    elif action == "set":
                        packets = setpoint_commands(*args, state=state)
                        # The device accepts one setpoint per packet. Reduce
                        # current before raising voltage; otherwise set voltage
                        # first. Both intermediate and final V×I then stay within
                        # the larger of the old and new power envelopes.
                        if args[1] < state["set_amps"]:
                            packets = packets[::-1]
                        for packet in packets:
                            connection.write(packet)
                        connection.write(query(0xFF))
                        pending = (action, args, now + 2)
                        self.emit("sent", action=action)
                    elif action in ("on", "off"):
                        if action == "on":
                            validate_setpoints(state["set_volts"], state["set_amps"], state)
                        else:
                            # Emergency off also cancels queued changes. They must
                            # never execute later after an automation was stopped.
                            while True:
                                try:
                                    self.actions.get_nowait()
                                except queue.Empty:
                                    break
                        connection.write(output_command(action == "on"))
                        connection.write(query(0xDB))
                        pending = (action, (), now + 2)
                        self.emit("sent", action=action)
                    elif action in ("protections", "preset", "brightness", "metering"):
                        if action == "protections":
                            packets = protection_commands(args[0], state)
                        elif action == "preset":
                            packets = preset_commands(*args, state=state)
                        elif action == "brightness":
                            packets = (brightness_command(*args),)
                        else:
                            packets = (metering_command(*args),)
                        for packet in packets:
                            connection.write(packet)
                        connection.write(query(0xFF))
                        pending = (action, args, now + 2)
                        self.emit("sent", action=action)
                    elif action == "refresh":
                        connection.write(query(0xFF))
                        connection.write(query(0xDB))
                # These are reads. In particular, FF is the vendor's READ_ALL
                # packet with a zero payload, not a write/apply command.
                if now - last_status >= 0.75:
                    connection.write(query(0xDB))
                    last_status = now
                if now - last_settings >= 2:
                    connection.write(query(0xFF))
                    if state.get("model") and state_model(state).input_limited:
                        for reg in (0xC0, 0xE2, 0xE3):
                            connection.write(query(reg))
                    last_settings = now
        except (serial.SerialException, OSError, RuntimeError, ValueError) as error:
            self.emit("error", message=str(error))
        finally:
            if connection is not None and connection.is_open:
                try:
                    connection.write(DISCONNECT)
                    time.sleep(0.08)
                except (serial.SerialException, OSError):
                    pass
                connection.close()
            # Commands from a former connection must never run after reconnect.
            while True:
                try:
                    self.actions.get_nowait()
                except queue.Empty:
                    break
            self.emit("disconnected")
