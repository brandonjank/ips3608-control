"""Telemetry recording and gap-aware session integration."""

import csv
import math
from pathlib import Path


class Statistics:
    """Sample min/max/mean, independent of logging and energy integration."""

    FIELDS = ("volts", "amps", "watts")

    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.minimum = {}
        self.maximum = {}
        self.average = {}

    def add(self, sample: dict):
        values = {field: sample[field] for field in self.FIELDS}
        if not all(math.isfinite(value) for value in values.values()):
            return
        self.count += 1
        for field, value in values.items():
            if self.count == 1:
                self.minimum[field] = self.maximum[field] = self.average[field] = value
            else:
                self.minimum[field] = min(self.minimum[field], value)
                self.maximum[field] = max(self.maximum[field], value)
                self.average[field] += (value - self.average[field]) / self.count


class Session:
    def __init__(self):
        self.samples = 0
        self.amp_hours = 0.0
        self.watt_hours = 0.0
        self.duration = 0.0
        self.previous = None

    def add(self, sample: dict):
        if self.previous:
            delta = sample["monotonic"] - self.previous["monotonic"]
            if 0 < delta <= 2.5:
                self.duration += delta
                self.amp_hours += (max(0, sample["amps"]) + max(0, self.previous["amps"])) * delta / 7200
                self.watt_hours += (max(0, sample["watts"]) + max(0, self.previous["watts"])) * delta / 7200
        self.samples += 1
        self.previous = sample


class Recorder:
    FIELDS = ("timestamp", "elapsed_s", "voltage_V", "current_A", "power_W",
              "set_voltage_V", "set_current_A", "output_enabled", "model", "hardware", "firmware",
              "temperature_C", "input_voltage_V", "mode_code", "protection_code",
              "device_capacity_Ah", "device_energy_Wh")

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.file = self.path.open("x", encoding="utf-8", newline="")
        self.writer = csv.writer(self.file)
        self.writer.writerow(self.FIELDS)
        self.file.flush()
        self.first = None
        self.rows = 0

    def add(self, sample: dict):
        if self.first is None:
            self.first = sample["monotonic"]
        precision = 9 if sample.get("model") == "DPS-150P" else 6
        self.writer.writerow((
            sample["timestamp"], f'{sample["monotonic"] - self.first:.3f}',
            f'{sample["volts"]:.6f}', f'{sample["amps"]:.{precision}f}', f'{sample["watts"]:.{precision}f}',
            sample.get("set_volts", ""), sample.get("set_amps", ""),
            "" if "output" not in sample else int(sample["output"]),
            sample.get("model", ""), sample.get("hardware", ""), sample.get("firmware", ""),
            sample.get("temperature", ""), sample.get("input_volts", ""),
            sample.get("mode", ""), sample.get("protection", ""),
            sample.get("device_ah", ""), sample.get("device_wh", "")))
        self.file.flush()
        self.rows += 1

    def close(self):
        self.file.close()
