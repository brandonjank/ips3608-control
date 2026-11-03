"""Model identity, rated limits and conservative live output ceilings."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Model:
    name: str
    volts: float
    amps: float
    watts: float
    input_limited: bool = False
    microamps: bool = False

    def as_dict(self):
        return {"name": self.name, "voltage_V": self.volts, "current_A": self.amps,
                "power_W": self.watts, "input_limited": self.input_limited,
                "microamp_readback": self.microamps,
                "voltage_resolution_V": .01, "current_setpoint_resolution_A": .001}


MODELS = {
    "IPS3608": Model("IPS3608", 36., 8., 285.),
    "DPS-150": Model("DPS-150", 30., 5., 150., input_limited=True),
    "DPS-150P": Model("DPS-150P", 30., 5., 150., input_limited=True, microamps=True),
}


def model_for(name):
    """Only known identities; do not infer a model from a shared USB VID/PID."""
    key = name.strip().upper() if isinstance(name, str) else ""
    if key not in MODELS:
        raise ValueError(f"Unsupported supply model: {name or 'unknown'}. Expected IPS3608, DPS-150 or DPS-150P.")
    return MODELS[key]


def state_model(state=None):
    # Standalone protocol/file helpers retain their original IPS3608 default.
    # All live paths require a detected identity separately before dispatch.
    return model_for((state or {}).get("model", "IPS3608"))


def ceiling(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Device {name} limit is unavailable or invalid; wait for a valid snapshot.")
    return float(value)


def output_limits(state=None, *, live=True):
    model = state_model(state)
    limits = {"voltage_V": model.volts, "current_A": model.amps, "power_W": model.watts}
    if live and state:
        for key, field in (("voltage_V", "max_volts"), ("current_A", "max_amps")):
            if field in state or model.input_limited:
                limits[key] = min(limits[key], ceiling(state.get(field), key))
        if model.input_limited:
            # The DPS units are buck supplies. Keep 0.2 V of input headroom,
            # and honor a still-lower ceiling reported by the device itself.
            limits["voltage_V"] = min(limits["voltage_V"], max(0., ceiling(state.get("input_volts"), "input voltage") - .2))
    return limits


def current_reading(value, model):
    """Adaptive units keep DPS-150P low-current readings visible."""
    if model and model.microamps:
        if abs(value) < .001:
            return f"{value * 1e6:.1f}", "µA"
        if abs(value) < 1:
            return f"{value * 1e3:.3f}", "mA"
        return f"{value:.6f}", "A"
    return f"{value:.3f}", "A"
