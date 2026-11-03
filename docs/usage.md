# IPS3608 Control user guide

A local Windows desktop controller and CSV logger for the FNIRSI IPS3608,
DPS-150 and DPS-150P. It detects the model automatically from its USB replies.
Connect the supply's USB data port. No cloud service or FNIRSI PC
software is required. Firmware is not modified.

## Start

For a portable release, extract the full folder and open **IPS3608Control.exe**.
For source use, install Python 3.10+ with Tcl/Tk, run **Setup.cmd**, then open
**Launch Bench Control.cmd**. See the main README for installation commands.

1. Power the supply normally and connect its USB data port.
2. Click **Find supply**, select its port, and **Connect**. Only USB devices
   matching the family's VID/PID (2E3C:5740) are listed. The model is checked
   after connecting; unknown identities are rejected. Port numbers vary by computer.
3. The fields fill with the actual device setpoints. Connecting preserves output.
4. Select a preset or type values, then click **Apply settings**. Settings can be
   changed with output on or off; applying them preserves the output state.
   The app waits for matching device readback before reporting success.
5. Click **Turn output on** separately. **Output off** or **F8** requests output off.
   F8 works while this app has keyboard focus; it is not a global hotkey.

When output is on, applying settings changes the live output immediately. The
app sends the two setpoints separately: a lower current limit is sent first;
otherwise voltage is sent first. This avoids a temporary V×I setting larger
than both the old and new settings. It is not a ramp or an atomic transition.
Rated limits are IPS3608: 0–36 V / 0–8 A / 285 W; DPS-150 and DPS-150P:
0–30 V / 0–5 A / 150 W. Live settings also honor lower device-reported
voltage/current limits. DPS supplies reserve 0.2 V below measured input voltage
and require valid input/ceiling readbacks. These checks apply to output enable,
manual edits, program steps and MCP, and are repeated by the serial worker
before transmission. Invalid values are rejected, not silently clamped.
Setpoints are rounded to 0.01 V / 0.001 A on every model. Stored presets and
offline programs use model ratings; execution checks the current input limits.

DPS-150P measured current displays adapt between µA, mA and A, including its
statistics. MCP numeric current remains in amps, and CSV recordings preserve
up to nine decimal places in amps/power for that model. This changes readback
presentation, not the 0.001 A current-limit setting resolution.

The main window's six presets are local to this computer; **Edit** changes them
in the app data folder's `settings.json`. Selecting one only fills the fields. **Device settings →
Device presets** separately reads and writes the six slots stored in the supply.
Calibration is never written.

## Device settings

Open **Device settings** for:

- **Protections:** OVP, OCP, OPP, OTP and input undervoltage (UVP), with actual
  device readback and device-reported upper bounds. DPS trip limits use that
  device's own reported maxima, rather than the IPS3608 ranges. **Apply protections** sends
  only changed thresholds. Changes affect an active output and can trip protection.
  OTP is a thermal shutdown limit, not a fan curve. UVP is for the internal
  input rail, not the adjustable output.
- **Device presets:** read the six stored voltage/current pairs, edit a pair,
  then **Save to device slot**. This writes device memory but does not enable
  output. **Stage in main controls** only fills the main input fields.
- **Display & metering:** 14 brightness levels, device metering on/off, the
  device's Ah/Wh counters, and a separate **Reset app Ah/Wh totals** action.
  Settings require matching snapshot readback to report success.
- **Other controls:** availability of front-panel-only functions and a
  **Release front-panel controls** action that disconnects while preserving output.

Temperature, CV/CC mode and protection status appear above the live readings.
These and input rail voltage/device counters are included in new CSV recordings.

Fan speed/curve, beeper volume, language, on-device display theme, key lock,
address changes, factory reset and device counter reset have no verified USB
command in the official PC software inspected here. Use the front panel for
those supported by the device's own menu. The fan remains automatic. There is
no fake or guessed fan-control command in the app.

## Programs and sweeps

**Programs & sweeps** accepts one `volts, amps, hold seconds` step per line.
Blank lines and lines starting with `#` are ignored. **Validate** checks every
step and shows its range and total hold time. A sweep generator builds ascending
or descending voltage/current sequences, including the endpoint. The other
setpoint is held fixed. Programs can be saved to and loaded from JSON.

1. Review the generated steps, cycle count and completion behavior.
2. Turn output on explicitly using the main window.
3. Click **Run program**. It never enables output by itself.

Each hold starts after the supply confirms that step's setpoints. **Pause**
holds the current output and freezes the remaining hold time, **Resume**
continues, and **Stop + output off** cancels the program and requests output off.
Completion can turn output off (default) or hold the final settings.

Manual setting changes are locked while running. Stale telemetry/status,
protection trips and loss of output abort the program and request output off
while communication is available. A lost USB link cannot guarantee physical
shutdown. The supply's local output button remains the fallback. Disconnect or
app close stops the program and preserves output, as with manual operation.
Closing only the program editor leaves execution running; progress remains
visible in the main window, and the output-off button/F8 still stops it.

If a DPS supply's available voltage/current limit falls below an active step,
the program stops and requests output off. Programs cannot bypass model ratings
or a lower live input limit. See [model support and validation](model-compatibility.md)
for the current hardware-test coverage.

Up to 1,000 steps per sequence, 1,000 cycles, 10,000 total executed steps, and
0.1–86,400 seconds per hold are allowed. This is host-timed bench automation,
not a precision waveform generator. Communication and UI scheduling add delay.

## Readings and recording

Voltage, current and power come from checksum-validated live packets. Voltage
and current have independent, labelled plots with selectable 60-second,
5-minute and 30-minute histories (up to 20,000 samples retained).

**Start CSV recording** chooses a new file. Samples are written and flushed as
they arrive. Recording ends on disconnect or app close. Existing CSV files are
never overwritten. **Open logs** opens the last recording's folder or `logs/`.

CSV columns: local timestamp with UTC offset, monotonic elapsed seconds,
measured V/A/W, device setpoints, last reported output state, model, hardware
and firmware version, temperature, input voltage, mode/protection codes and
device Ah/Wh counters. Mode 1 = CV and 0 = CC. Protection 0 = normal, 1 = OVP,
2 = OCP, 3 = OPP, 4 = OTP, 5 = input undervoltage. An empty field means unknown,
not zero. Output state is
polled independently, roughly every 0.75 seconds; it is the last reported state
at sample time. Samples typically arrive around twice per second; the app does
not invent samples or claim oscilloscope-rate capture.

Session Ah/Wh totals integrate received current and power with the trapezoid
rule. Gaps longer than 2.5 seconds are excluded. Totals reset on each connection
and cover that connection, not just the CSV recording interval. They are
software estimates, not the supply's internal energy counters.

Each measured V/A/W value also shows its minimum, maximum and average over
samples received since connection or the last **Reset statistics** click.
Average is the arithmetic sample mean, including samples taken while output is
off. Reset clears all nine statistics together and starts with the next sample;
before that sample, statistics show a dash. It does not clear graph history,
stop CSV recording, change the supply, or reset Ah/Wh session totals.

## Connection behavior

- Connection/disconnection sends remote-mode commands and queries only.
- Disconnect and app close preserve output state. A warning is shown when
  closing while the app knows output is on.
- No automatic reconnect or automatic output enable.
- Stale readings or stale status disable setting changes and output enable.
- Output-off remains available while connected. It requires a reply to be
  reported as confirmed. A USB disconnect cannot guarantee physical output off;
  the display/button on the supply is the source of truth after communication loss.
- On a lost connection, last readings are dimmed and output shows unknown.
- The supply may lock its front-panel controls while in PC remote mode.

## AI agent control (MCP)

The app includes a local agent bridge and a 21-tool MCP stdio server for reading
telemetry, changing live settings, recording, and controlling programs. The
desktop app keeps sole ownership of USB. Use the `ips3608` MCP server and
`$ips3608-control` skill. See [MCP setup and tool behavior](mcp.md).

## Firmware/update-mode investigation

The Up-at-power-on update interface was captured read-only twice. It exposes
a synthetic disk whose data sectors are zero; it does not expose the installed
firmware for backup. The downloaded V1.9 application was analyzed offline and
its `0x08006800` link address confirmed. See the
[firmware investigation](firmware-investigation.md).

## Validation

Run protocol/recording tests without hardware:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run a read-only hardware check with the desktop app disconnected:

```powershell
.\.venv\Scripts\python.exe tools\check_connection.py COM12
```

That check captures approximately ten samples and never writes setpoints,
output enable, presets, calibration or firmware.

Protocol fields were initially verified on hardware V1.1 / firmware V1.3.
Additional offsets and commands were checked against FNIRSI's official PC
software by static analysis; the vendor executable was not run. See
  `protocol-notes.md` for the corrected map. Test readbacks again after any
firmware update.

References: [Balu46/FNIRSI_control](https://github.com/Balu46/FNIRSI_control),
[alexgavs/newfnrs](https://github.com/alexgavs/newfnrs), and
[hamsternz/fnirsi_psu](https://github.com/hamsternz/fnirsi_psu).
