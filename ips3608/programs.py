"""Validated timed setpoint programs; no serial I/O or implicit output enable."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import math

from .protocol import validate_setpoints


@dataclass(frozen=True)
class Step:
    volts: float
    amps: float
    seconds: float

    def validate(self, state=None, *, live=False):
        volts, amps = validate_setpoints(self.volts, self.amps, state, live=live)
        if not math.isfinite(self.seconds) or not 0.1 <= self.seconds <= 86400:
            raise ValueError("Hold time must be between 0.1 and 86,400 seconds.")
        return Step(volts, amps, round(self.seconds, 3))


def parse_steps(text: str, state=None, *, live=False) -> tuple[Step, ...]:
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            values = line.split(",")
            if len(values) != 3:
                raise ValueError("Use volts, amps, hold seconds.")
            result.append(Step(*(float(value.strip()) for value in values)).validate(state, live=live))
        except ValueError as error:
            raise ValueError(f"Line {number}: {error}") from error
    if not result or len(result) > 1000:
        raise ValueError("Provide 1–1,000 program steps.")
    return tuple(result)


def sweep_steps(axis: str, start: float, end: float, increment: float,
                fixed: float, seconds: float, state=None, *, live=False) -> tuple[Step, ...]:
    if axis not in ("voltage", "current"):
        raise ValueError("Choose a voltage or current sweep.")
    if not all(math.isfinite(v) for v in (start, end, increment, fixed, seconds)) or increment <= 0:
        raise ValueError("Use finite values and a positive step size.")
    first, last, step = map(lambda v: Decimal(str(v)), (start, end, increment))
    direction = 1 if last >= first else -1
    result = []
    value = first
    while (value - last) * direction <= 0:
        v, a = (float(value), fixed) if axis == "voltage" else (fixed, float(value))
        item = Step(v, a, seconds).validate(state, live=live)
        if result and (item.volts, item.amps) == (result[-1].volts, result[-1].amps):
            raise ValueError("Step size is smaller than device resolution.")
        result.append(item)
        if len(result) > 1000:
            raise ValueError("A sweep may contain at most 1,000 steps.")
        value += step * direction
    # Include the requested endpoint even when the increment doesn't divide it.
    v, a = (end, fixed) if axis == "voltage" else (fixed, end)
    endpoint = Step(v, a, seconds).validate(state, live=live)
    if result[-1] != endpoint:
        result.append(endpoint)
    if len(result) > 1000:
        raise ValueError("A sweep may contain at most 1,000 steps.")
    return tuple(result)


def program_to_dict(steps, cycles: int, finish_off: bool, state=None, *, live=False):
    runner = Runner(steps, cycles, finish_off, state, live=live)
    return {"version": 1, "cycles": runner.cycles, "finish_off": runner.finish_off,
            "steps": [asdict(step) for step in runner.steps]}


def program_from_dict(value, state=None):
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("finish_off"), bool):
        raise ValueError("Unsupported program file.")
    try:
        runner = Runner(tuple(Step(**step) for step in value["steps"]), value["cycles"], value["finish_off"], state)
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid program file.") from error
    return runner.steps, runner.cycles, runner.finish_off


class Runner:
    def __init__(self, steps, cycles=1, finish_off=True, state=None, *, live=False):
        self.steps = tuple(step.validate(state, live=live) for step in steps)
        if not 1 <= len(self.steps) <= 1000:
            raise ValueError("Provide 1–1,000 steps.")
        if isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= 1000:
            raise ValueError("Cycles must be an integer from 1 to 1,000.")
        if len(self.steps) * cycles > 10000:
            raise ValueError("A program may execute at most 10,000 steps.")
        self.cycles, self.finish_off = cycles, finish_off
        self.index = self.cycle = 0
        self.phase = "ready"
        self.paused = False
        self.remaining = 0.0
        self.last_tick = None
        self.finish_handled = False

    @property
    def active(self):
        return self.phase not in ("complete", "stopped")

    def tick(self, now):
        if not self.active or self.paused:
            return None
        if self.phase == "holding":
            self.remaining = max(0, self.remaining - max(0, now - self.last_tick))
            self.last_tick = now
            if self.remaining > 0:
                return None
            self.index += 1
            if self.index == len(self.steps):
                self.index = 0
                self.cycle += 1
                if self.cycle == self.cycles:
                    self.phase = "complete"
                    return None
            self.phase = "ready"
        if self.phase == "ready":
            self.phase = "awaiting readback"
            step = self.steps[self.index]
            return step.volts, step.amps
        return None

    def confirmed(self, now):
        if self.phase == "awaiting readback":
            self.phase = "holding"
            self.remaining = self.steps[self.index].seconds
            self.last_tick = now

    def pause(self, now):
        if self.phase == "holding" and not self.paused:
            self.remaining = max(0, self.remaining - max(0, now - self.last_tick))
        self.paused = True

    def resume(self, now):
        self.last_tick = now
        self.paused = False

    def stop(self):
        self.phase = "stopped"
        self.paused = False

    def status(self):
        if not self.active:
            return self.phase.capitalize()
        phase = "Paused · output held" if self.paused else self.phase
        suffix = f" · {self.remaining:.1f} s left" if self.phase == "holding" else ""
        return f"Cycle {self.cycle + 1}/{self.cycles} · step {self.index + 1}/{len(self.steps)} · {phase}{suffix}"
