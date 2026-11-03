"""MCP stdio adapter for the running desktop controller. No serial ownership."""

import json
from functools import wraps
from typing import Literal
import time

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, ResourceError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from .agent_bridge import BridgeClient, capabilities
from .programs import Step, Runner, program_to_dict, sweep_steps
from .models import model_for, output_limits
from .branding import APP_NAME, VERSION

ModelName = Literal["IPS3608", "DPS-150", "DPS-150P"]

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)


class ProgramStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volts: float = Field(ge=0, le=36, description="Voltage setpoint in volts.")
    amps: float = Field(ge=0, le=8, description="Current limit in amps.")
    seconds: float = Field(ge=0.1, le=86400, description="Hold time after confirmed readback.")


def create_server(client=None):
    client = client or BridgeClient()
    server = MCPServer(
        "ips3608", version=VERSION, title=APP_NAME,
        instructions="Controls IPS3608, DPS-150 and DPS-150P through the running local app. Read status and detected model/limits first. "
        "Freshness and readback are authoritative; never infer physical output from a timeout. "
        "Changing setpoints preserves output; enabling output is a separate explicit action. "
        "A program requires output already on. Stop requests output off; pause holds output. "
        "No fan, arbitrary registers, firmware, shell or general filesystem operations are exposed.")

    def tool(*, annotations):
        # Expected refusals/timeouts must reach the agent as useful messages;
        # the SDK deliberately masks unexpected exception text.
        def decorate(fn):
            @wraps(fn)
            def checked(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except (ValueError, RuntimeError, OSError) as error:
                    raise ToolError(str(error)) from error
            return server.tool(annotations=annotations)(checked)
        return decorate

    @tool(annotations=READ)
    def get_capabilities() -> dict:
        """List all model profiles and detected model/live limits when available. Works even if the app is closed."""
        try:
            return client.call("capabilities")
        except RuntimeError:
            return capabilities()

    @tool(annotations=READ)
    def get_status() -> dict:
        """Read identity, fresh/stale telemetry, actual setpoints/output, protections, presets, statistics and program/recording state."""
        return client.call("status")

    @tool(annotations=READ)
    def list_ports() -> dict:
        """Find matching FNIRSI USB ports. Identity is confirmed after connection; shared VID/PID is not model detection."""
        return client.call("ports")

    @tool(annotations=WRITE)
    def connect(port: str | None = None) -> dict:
        """Connect a listed port, auto-detect IPS3608/DPS-150/DPS-150P and await readback. Preserves output."""
        return client.call("connect", port=port)

    @tool(annotations=WRITE)
    def disconnect() -> dict:
        """Release the app's serial connection/front-panel lock. Stops any program and preserves physical output."""
        return client.call("disconnect")

    @tool(annotations=READ)
    def get_history(seconds: float = 60, limit: int = 100) -> dict:
        """Read up to 1,000 retained samples from the last 1–3,600 seconds; does not reset or record anything."""
        return client.call("history", seconds=seconds, limit=limit)

    @tool(annotations=READ)
    def monitor(duration_s: float = 5, interval_s: float = 0.5) -> dict:
        """Collect new telemetry for 0.5–30 seconds, reporting stale/lost data. Never changes output or reconnects."""
        if not 0.5 <= duration_s <= 30 or not 0.2 <= interval_s <= 5:
            raise ValueError("duration_s must be 0.5–30 and interval_s 0.2–5.")
        started = time.monotonic()
        samples, previous = [], None
        final = None
        while True:
            final = client.call("status")
            if not final["fresh"]:
                return {"fresh": False, "samples": samples, "status": final,
                        "message": "Monitoring stopped because telemetry/status is stale or disconnected."}
            sample = final.get("latest_sample")
            if sample and sample["monotonic"] != previous:
                samples.append(sample)
                previous = sample["monotonic"]
            remaining = duration_s - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(interval_s, remaining))
        return {"fresh": True, "samples": samples, "count": len(samples), "status": final}

    @tool(annotations=WRITE)
    def set_setpoints(voltage_v: float | None = None, current_a: float | None = None) -> dict:
        """Set V and/or current limit, including while output is on. Preserves output state. Returns confirmed device readback."""
        return client.call("setpoints", voltage_v=voltage_v, current_a=current_a)

    @tool(annotations=WRITE)
    def set_output(enabled: bool) -> dict:
        """Explicitly enable/disable physical output and await confirmation. Off cancels a program and queued setting changes."""
        return client.call("output", enabled=enabled)

    @tool(annotations=WRITE)
    def set_protections(ovp: float | None = None, ocp: float | None = None, opp: float | None = None,
                        otp: float | None = None, uvp: float | None = None) -> dict:
        """Change only supplied device trip limits. OVP V; OCP A; OPP W; OTP °C; UVP internal input V. May trip active output."""
        values = {key: value for key, value in dict(ovp=ovp, ocp=ocp, opp=opp, otp=otp, uvp=uvp).items() if value is not None}
        return client.call("protections", **values)

    @tool(annotations=WRITE)
    def save_device_preset(slot: int, voltage_v: float, current_a: float) -> dict:
        """Write one of six persistent device V/A slots and await readback. Does not apply the slot or enable output."""
        return client.call("preset", slot=slot, voltage_v=voltage_v, current_a=current_a)

    @tool(annotations=WRITE)
    def set_brightness(level: int) -> dict:
        """Set the device display brightness to level 1–14 and await matching readback."""
        return client.call("brightness", level=level)

    @tool(annotations=WRITE)
    def set_metering(enabled: bool) -> dict:
        """Enable/disable the device's energy metering, independently of app statistics and CSV recording."""
        return client.call("metering", enabled=enabled)

    @tool(annotations=WRITE)
    def reset_statistics() -> dict:
        """Clear app V/A/W min/max/sample-average only. Recording, graphs, energy totals and physical output continue."""
        return client.call("reset_statistics")

    @tool(annotations=WRITE)
    def reset_energy() -> dict:
        """Reset app-integrated Ah/Wh session estimates only. Does not reset device counters or statistics."""
        return client.call("reset_energy")

    @tool(annotations=WRITE)
    def start_recording(filename: str | None = None) -> dict:
        """Start CSV recording in the app logs folder. Plain .csv filename only; existing files are never overwritten."""
        return client.call("record_start", filename=filename)

    @tool(annotations=WRITE)
    def stop_recording() -> dict:
        """Stop and flush CSV recording. Returns saved path/row count; never changes output."""
        return client.call("record_stop")

    def planning_state(model):
        if model is not None:
            return {"model": model_for(model).name}, False
        status = client.call("status")
        if not status.get("connected") or not status.get("device", {}).get("model"):
            raise ValueError("Connect for automatic model detection, or specify model for an offline preview.")
        state = status["device"]
        model_for(state["model"])
        return state, bool(status.get("fresh"))

    @tool(annotations=READ)
    def preview_sweep(axis: Literal["voltage", "current"], start: float, end: float, increment: float,
                      fixed: float, seconds: float, cycles: int = 1, finish_off: bool = True,
                      model: ModelName | None = None) -> dict:
        """Preview without executing. Auto-use connected model/live limits, or specify model for an offline rated-limit preview."""
        state, live = planning_state(model)
        steps = sweep_steps(axis, start, end, increment, fixed, seconds, state, live=live)
        result = program_to_dict(steps, cycles, finish_off, state, live=live)
        result.update(model=state["model"], limits=output_limits(state, live=live),
                      validation_scope="live" if live else "rated")
        return result

    @tool(annotations=READ)
    def validate_program(steps: list[ProgramStep], cycles: int = 1, finish_off: bool = True,
                         model: ModelName | None = None) -> dict:
        """Validate a timed sequence and return its normalized definition. Does not execute or change the device."""
        state, live = planning_state(model)
        runner = Runner([Step(**step.model_dump()) for step in steps], cycles, finish_off, state, live=live)
        return {"program": program_to_dict(runner.steps, cycles, finish_off, state, live=live),
                "model": state["model"], "limits": output_limits(state, live=live),
                "validation_scope": "live" if live else "rated",
                "total_steps": len(runner.steps) * cycles,
                "hold_seconds": sum(step.seconds for step in runner.steps) * cycles}

    @tool(annotations=WRITE)
    def start_program(steps: list[ProgramStep], cycles: int = 1, finish_off: bool = True) -> dict:
        """Execute reviewed setpoint steps. Requires output already on; never enables it. Holds start after readback; monitor status."""
        return client.call("program_start", steps=[step.model_dump() for step in steps], cycles=cycles, finish_off=finish_off)

    @tool(annotations=WRITE)
    def control_program(action: Literal["pause", "resume", "stop"]) -> dict:
        """Pause holds output; resume continues; stop cancels the program and requests output OFF, awaiting confirmation."""
        return client.call("program_control", action=action)

    @server.resource("ips3608://status")
    def status_resource() -> str:
        """Current app/device state; stale output is explicitly unknown."""
        try:
            return json.dumps(client.call("status"), allow_nan=False)
        except (ValueError, RuntimeError, OSError) as error:
            raise ResourceError(str(error)) from error

    @server.resource("ips3608://capabilities")
    def capabilities_resource() -> str:
        """Supported operations and limits."""
        return json.dumps(get_capabilities())

    return server


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
