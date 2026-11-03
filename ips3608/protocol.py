"""IPS3608 framing. Reading and remote mode do not write output settings.

References: Balu46/FNIRSI_control and alexgavs/newfnrs on GitHub.
The identity, status, telemetry and snapshot fields below were checked against
an IPS3608 hardware V1.1 / firmware V1.3. Unknown fields remain undecoded.
"""

from dataclasses import dataclass
import math
import struct
from .models import state_model, output_limits, ceiling

MAX_VOLTS = 36.0
MAX_AMPS = 8.0  # Use the product rating, even if a unit advertises 8.2 A.
MAX_WATTS = 285.0

# Register, snapshot offset, conservative upper bound, unit. Verified against
# the official PC application's UserPage12 and SerialData.setAllData.
PROTECTIONS = {
    "ovp": (0xD1, 76, 36.1, "V"),
    "ocp": (0xD2, 80, 8.2, "A"),
    "opp": (0xD3, 84, 295.2, "W"),
    "otp": (0xD4, 88, 99.0, "°C"),
    "uvp": (0xD5, 92, 48.0, "V input"),
}
PROTECTION_NAMES = {0: "Normal", 1: "OVP", 2: "OCP", 3: "OPP", 4: "OTP", 5: "Input undervoltage", 6: "Reverse connection"}
MODE_NAMES = {0: "CC", 1: "CV"}


def protection_limit(key, state=None):
    model = state_model(state)
    if model.input_limited:
        # Trip thresholds are not output setpoints. Use this model's actual
        # reported trip maxima rather than importing IPS3608 threshold ranges.
        return ceiling((state or {}).get(f"max_{key}"), key.upper())
    limit = PROTECTIONS[key][2]
    if state and f"max_{key}" in state:
        limit = min(limit, ceiling(state[f"max_{key}"], key.upper()))
    return limit


def validate_protections(values: dict, state: dict | None = None) -> dict:
    if set(values) != set(PROTECTIONS):
        raise ValueError("Provide all five protection thresholds.")
    result = {}
    for key, value in values.items():
        value = float(value)
        # Some stock thresholds exceed the maxima reported by the same unit.
        # Preserve those untouched values; never rewrite or silently clamp them.
        unchanged = state and key in state and math.isfinite(value) and abs(value - state[key]) < 0.0011
        limit = protection_limit(key, state) if not unchanged else None
        if not unchanged and (not math.isfinite(value) or not 0 <= value <= limit):
            raise ValueError(f"{key.upper()} must be between 0 and {limit:g} {PROTECTIONS[key][3]}.")
        result[key] = round(value, 3 if key == "ocp" else 0 if key == "otp" else 2)
    return result


def protection_commands(values: dict, state: dict | None = None) -> tuple[bytes, ...]:
    values = validate_protections(values, state)
    return tuple(command(0xB1, PROTECTIONS[key][0], struct.pack("<f", value))
                 for key, value in values.items()
                 if not state or key not in state or abs(state[key] - value) >= 0.0011)


def preset_commands(slot: int, volts: float, amps: float, state=None) -> tuple[bytes, bytes]:
    if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 6:
        raise ValueError("Choose device preset 1–6.")
    volts, amps = validate_setpoints(volts, amps, state, live=False)
    register = 0xC3 + 2 * slot
    return (command(0xB1, register, struct.pack("<f", volts)),
            command(0xB1, register + 1, struct.pack("<f", amps)))


def brightness_command(level: int) -> bytes:
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 14:
        raise ValueError("Brightness must be a level from 1 to 14.")
    return command(0xB1, 0xD6, bytes((level,)))


def metering_command(enabled: bool) -> bytes:
    return command(0xB1, 0xD8, bytes((int(enabled),)))


def command(category: int, register: int, payload: bytes = b"\x00") -> bytes:
    if len(payload) > 255:
        raise ValueError("Payload is too long")
    return bytes((0xF1, category, register, len(payload))) + payload + bytes(
        ((register + len(payload) + sum(payload)) & 255,)
    )


CONNECT = command(0xC1, 0, b"\x01")
DISCONNECT = command(0xC1, 0, b"\x00")


def query(register: int) -> bytes:
    return command(0xA1, register)


def output_command(enabled: bool) -> bytes:
    return command(0xB1, 0xDB, bytes((int(enabled),)))


def validate_setpoints(volts: float, amps: float, state=None, *, live=True) -> tuple[float, float]:
    if not math.isfinite(volts) or not math.isfinite(amps):
        raise ValueError("Enter finite numbers for voltage and current.")
    limits = output_limits(state, live=live)
    max_v, max_a, max_w = limits["voltage_V"], limits["current_A"], limits["power_W"]
    if not 0 <= volts <= max_v + 1e-6:
        raise ValueError(f"Voltage must be between 0 and {max_v:g} V for this supply/input.")
    if not 0 <= amps <= max_a + 1e-6:
        raise ValueError(f"Current must be between 0 and {max_a:g} A for this supply.")
    volts, amps = round(volts, 2), round(amps, 3)
    if volts > max_v + 1e-6 or amps > max_a + 1e-6:
        raise ValueError("Rounded setpoints exceed the available device limit.")
    if volts * amps > max_w + 0.00001:
        raise ValueError(f"Voltage × current must not exceed {max_w:g} W.")
    return volts, amps


def setpoint_commands(volts: float, amps: float, state=None) -> tuple[bytes, bytes]:
    volts, amps = validate_setpoints(volts, amps, state)
    return (command(0xB1, 0xC1, struct.pack("<f", volts)),
            command(0xB1, 0xC2, struct.pack("<f", amps)))


def validate_action(action, args, state=None):
    """Shared admission check, repeated by the serial owner at dispatch time."""
    if action == "set":
        return validate_setpoints(*args, state=state)
    if action == "on":
        if not state or "set_volts" not in state or "set_amps" not in state:
            raise ValueError("Connected device setpoints are required before enabling output.")
        validate_setpoints(state["set_volts"], state["set_amps"], state)
    elif action == "preset":
        preset_commands(*args, state=state)
    elif action == "protections":
        return (validate_protections(args[0], state),)
    elif action == "brightness":
        brightness_command(*args)
    elif action == "metering" and (len(args) != 1 or not isinstance(args[0], bool)):
        raise ValueError("Metering requires on or off.")
    return args


@dataclass(frozen=True)
class Frame:
    category: int
    register: int
    payload: bytes


class FrameParser:
    """Handles fragmented packets, garbage and bad checksums."""

    def __init__(self):
        self.buffer = bytearray()
        self.bad_checksums = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 5:
            if self.buffer[0] != 0xF0 or self.buffer[1] not in (0xA1, 0xB0, 0xB1, 0xC0, 0xC1):
                del self.buffer[0]
                continue
            size = self.buffer[3] + 5
            if len(self.buffer) < size:
                break
            if sum(self.buffer[2:size - 1]) & 255 != self.buffer[size - 1]:
                self.bad_checksums += 1
                del self.buffer[0]
                continue
            frames.append(Frame(self.buffer[1], self.buffer[2], bytes(self.buffer[4:size - 1])))
            del self.buffer[:size]
        return frames


def decode(frame: Frame) -> dict:
    """Decode only known read responses; never infer an output state from V/A."""
    if frame.category != 0xA1:
        return {}
    reg, p = frame.register, frame.payload
    if reg in (0xDE, 0xDF, 0xE0):
        name = {0xDE: "model", 0xDF: "hardware", 0xE0: "firmware"}[reg]
        return {name: p.decode("ascii", errors="replace").rstrip("\x00").strip()}
    if reg == 0xDB and len(p) == 1 and p[0] in (0, 1):
        return {"output": bool(p[0])}
    if reg in (0xDC, 0xDD, 0xD6, 0xD8, 0xE1) and len(p) == 1:
        return {{0xDC: "protection", 0xDD: "mode", 0xD6: "brightness",
                 0xD8: "metering", 0xE1: "address"}[reg]:
                bool(p[0]) if reg == 0xD8 else p[0]}
    if reg in (0xC0, 0xC4, 0xD9, 0xDA, 0xE2, 0xE3) and len(p) == 4:
        value, = struct.unpack("<f", p)
        if math.isfinite(value) or reg in (0xC0, 0xE2, 0xE3):
            return {{0xC0: "input_volts", 0xC4: "temperature", 0xD9: "device_ah", 0xDA: "device_wh",
                     0xE2: "max_volts", 0xE3: "max_amps"}[reg]: value if math.isfinite(value) else None}
    if reg == 0xC3 and len(p) == 12:
        values = struct.unpack("<fff", p)
        if all(math.isfinite(x) for x in values):
            return dict(zip(("volts", "amps", "watts"), values))
    if reg == 0xFF and len(p) >= 12:
        _, volts, amps = struct.unpack_from("<fff", p)
        if all(math.isfinite(x) for x in (volts, amps)):
            result = {"set_volts": volts, "set_amps": amps}
            if len(p) >= 139:
                # Unknown/short firmware layouts retain only the verified prefix.
                for key, offset in (("input_volts", 0), ("temperature", 24),
                                    ("device_ah", 99), ("device_wh", 103),
                                    ("max_volts", 111), ("max_amps", 115)):
                    value, = struct.unpack_from("<f", p, offset)
                    if math.isfinite(value) or key in ("max_volts", "max_amps", "input_volts"):
                        result[key] = value if math.isfinite(value) else None
                for index, (key, (_, offset, _, _)) in enumerate(PROTECTIONS.items()):
                    for name, pos in ((key, offset), (f"max_{key}", 119 + index * 4)):
                        # Max fields follow OVP, OCP, OPP, OTP, UVP, same order.
                        value, = struct.unpack_from("<f", p, pos)
                        if math.isfinite(value) or name.startswith("max_"):
                            result[name] = value if math.isfinite(value) else None
                result.update(brightness=p[96], metering=bool(p[98]),
                              output=bool(p[107]), protection=p[108], mode=p[109])
                presets = [struct.unpack_from("<ff", p, 28 + index * 8) for index in range(6)]
                if all(math.isfinite(x) for pair in presets for x in pair):
                    result["device_presets"] = presets
            return result
    return {}
