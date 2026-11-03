---
name: ips3608-control
description: Control and monitor locally connected FNIRSI IPS3608, DPS-150 and DPS-150P power supplies through the ips3608 MCP server and IPS3608 Control desktop app. Use for live voltage/current limits, output switching, telemetry, resettable V/A/W statistics, CSV recording, protections, device presets, brightness, metering, or timed programs and sweeps. Use when the user asks about one of these supplies or its present output; do not use for unrelated instruments or firmware flashing.
---

# IPS3608 Control

Use the configured `ips3608` MCP tools. The desktop app owns the serial port;
keep it open. Never open a second serial connection or run a connection-check
script while the app is connected. Do not use guessed registers or fan commands.

## Establish the actual state

1. Call `get_capabilities` and `get_status`. Check detected `model_profile`,
   live `limits`, `controls_available`, `simulated`, identity, `fresh`, actual
   `device.set_volts` / `device.set_amps`, `output_enabled`, protection, pending
   change, and active program. Values in `device` are last reported values;
   only treat them as current when `fresh` is true. When `simulated` is true,
   readings and confirmations describe the in-memory demo; never present them
   as physical measurements or claim the real supply was changed.
2. If disconnected, use `list_ports` then `connect` to the matching supply.
   Do not hard-code COM12; Windows can assign a different port. Connection
   preserves output. If the app is closed, explain that it must be opened with
   `IPS3608Control.exe` (or `Launch Bench Control.cmd` for source use); starting the MCP process alone does not start it.
3. After connecting, wait for fresh telemetry before interpreting output or
   making changes. On stale data, report current output as unknown and label
   any retained readings as old. Do not silently reconnect or infer zero output.

## Make a requested change

- Follow the user's authorized target/range and output intent. Monitoring or
  connection is not authorization to energize a load or run a sweep. If an
  energizing request lacks necessary voltage/current limits or load constraints,
  ask for those missing values. Do not repeatedly ask about already authorized
  settings. Current in amps is a **current limit**, not a forced load current.
- `set_setpoints(voltage_v=..., current_a=...)` changes live settings even with
  output on. Omitted values come from device readback, not unapplied UI fields.
  It preserves output. IPS3608 ratings are 36 V / 8 A / 285 W; DPS-150 and
  DPS-150P ratings are 30 V / 5 A / 150 W. Use the detected live limits, which
  may be lower. DPS output also reserves 0.2 V of input headroom. Missing or
  invalid input/ceiling readbacks block live changes. Setpoint resolution is
  0.01 V and 0.001 A for all three, including DPS-150P. Its finer **measured**
  current readback does not imply microamp current-limit settings. Two writes
  are sequential, not an atomic change or ramp.
- Enable output separately with `set_output(enabled=true)` when requested.
  Resolve any unapplied manual UI fields first if the app refuses output enable.
  `set_output(enabled=false)` also cancels an active program and queued setting
  changes; it is available with stale data while still connected.
- A hardware change succeeds only when the result says `confirmed: true`.
  A timeout or connection error means the outcome may be unknown. Read status
  before deciding what to do; do not blindly retry a non-idempotent operation.
  A disconnected USB link cannot guarantee that output is physically off.
- The app interlocks concurrent writes and active programs. Do not bypass a
  refusal. Manual UI controls and MCP share the same validation and device worker.

## Monitor and record

Use `monitor` for bounded observation (0.5–30 seconds); repeat for longer
requested monitoring. Use `get_history` for retained samples and `get_status`
for V/A/W min/max/arithmetic sample average, session Ah/Wh, temperature,
CV/CC mode, protection and device counters. Inspect the final freshness flag.
Avoid high-frequency status polling; telemetry normally arrives around 2 Hz.
DPS-150P displays measured current in µA/mA/A as appropriate. MCP/CSV current
values remain in **amps**; do not rescale them based on the display unit.

`reset_statistics` clears the app's V/A/W extrema and means together. It leaves
history, recording, energy totals and output intact. `reset_energy` clears only
the app's integrated Ah/Wh estimates. Neither resets the device counters.
Reset only the measurement window the user requests.

`start_recording` accepts an optional plain `.csv` filename in the app's `logs`
folder. Existing files cannot be overwritten. `stop_recording` flushes and
returns the saved path and row count. Do not stop a pre-existing recording just
because a monitoring task ends. Never change output as a monitoring cleanup.

## Programs and device settings

Use `preview_sweep` or `validate_program` to prepare steps without running them.
They use the connected model automatically. For offline planning, pass an
explicit `model` (`IPS3608`, `DPS-150`, or `DPS-150P`); that checks rated limits,
not the future input supply. Execution always rechecks actual live limits.
Steps use `volts`, `amps`, `seconds`; review range, current limit, hold time,
cycles and `finish_off` against the user's intent. `start_program` requires
output already on and returns acceptance, not completed execution. Follow
`get_status`/`monitor` until completion or the requested observation ends.
Holds begin after device readback. `control_program(action="pause")` holds the
present physical output; `resume` continues; `stop` requests output off.
Completion turns output off by default; `finish_off=false` holds the final
settings. Stale data, protection or loss of output aborts execution and requests
off while communication is available. Closing an editor does not stop execution.

`set_protections` changes supplied trip limits only: OVP volts, OCP amps, OPP
watts, OTP degrees C, UVP internal input-rail volts. OTP is not fan control;
UVP is not output undervoltage. These changes can trip an active output.
`save_device_preset` writes a persistent slot 1–6 without applying it.
Stored presets are checked against model ratings; applying one uses current
live limits. Protection trip maxima come from the actual model's snapshot.
`set_brightness` supports 1–14. `set_metering` toggles device energy metering.
Keep changes within the user's requested scope and use actual readbacks.

Fan speed/curve, volume, language, theme, key lock, address writes, factory
reset, calibration, firmware and device-counter reset are not exposed by this
MCP. Use `disconnect` for requested front-panel access; it stops a program and
preserves physical output. Disconnecting, closing the app or ending the MCP
session does **not** turn output off. Leave the connection and output as the
user requested when the task ends.

Example requests this skill handles:

- “Monitor the supply for five seconds without changing output.”
- “Set the current limit to 0.25 A, keeping the voltage and output state.”
- “Prepare a 5–12 V sweep at 0.2 A, but show me the steps before running it.”
