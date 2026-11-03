# IPS3608 Control: MCP and agent skill

The MCP server exposes the running IPS3608 Control app to a local AI client over
stdio. The app remains the only owner of the serial port. Agent requests enter
the GUI event loop and use the same validation, interlocks and readback checks
as manual controls. The app must stay open; launching the MCP process does not
open the app or switch output on.

IPS3608, DPS-150 and DPS-150P are automatically detected on connection.
The existing server name `ips3608`, resources and `$ips3608-control` skill name
remain compatible. `get_capabilities` lists all profiles plus the connected
model's rated and effective live limits. `get_status` includes `model_profile`,
`limits`, `controls_available` and any limit-readback error. Stale live limits
are null. Unknown model identities are rejected even if the USB VID/PID matches.

`get_status` and `get_capabilities` also report `simulated`. When true, the
window is running `--demo`: readings and confirmed commands describe an
in-memory simulator, not a physical supply. GUI and MCP must use the same
`IPS3608_CONTROL_HOME` override when isolating a demo from a hardware session.

## Codex setup

Use the server name `ips3608` and skill `$ips3608-control`. Copy the
`skills/ips3608-control` folder into your client's personal skills directory
(for Codex, `~/.codex/skills/ips3608-control`).

For the Windows portable release, register the executable's absolute path:

```powershell
codex mcp add ips3608 -- 'C:\Tools\IPS3608-Control\IPS3608ControlMCP.exe'
codex mcp get ips3608
```

Replace that path with your extracted folder. For source installs, use the
absolute path to the environment's `ips3608-mcp.exe`, or its Python executable
followed by the absolute path to `tools/run_mcp.py`. Install `.[agent]` first.

Open **IPS3608Control.exe** (or **Launch Bench Control.cmd** for source use).
The window shows **Agent bridge ready**.
Connect in the UI or let the agent call `list_ports` and `connect`. The desktop
app does not automatically reconnect or enable output. If a running Codex
session does not list the new tools/skill, reload Codex to refresh discovery.

For another MCP client, use its stdio server configuration:

```json
{
  "mcpServers": {
    "ips3608": {
      "command": "C:\\Tools\\IPS3608-Control\\IPS3608ControlMCP.exe",
      "args": []
    }
  }
}
```

The absolute entrypoint works independently of the client's working directory.
The packaged server includes its Python runtime and dependencies. Source
installs use the official Python MCP SDK. Keep GUI and MCP from the same
release and restart the MCP client after replacing an older server binary.

## Tools and resources

| Purpose | Tools |
| --- | --- |
| Inspect | `get_capabilities`, `get_status`, `list_ports` |
| Connection | `connect`, `disconnect` |
| Observe | `monitor`, `get_history` |
| Live control | `set_setpoints`, `set_output` |
| Device settings | `set_protections`, `save_device_preset`, `set_brightness`, `set_metering` |
| Measurements | `reset_statistics`, `reset_energy`, `start_recording`, `stop_recording` |
| Programs | `preview_sweep`, `validate_program`, `start_program`, `control_program` |

JSON resources: `ips3608://status` and `ips3608://capabilities`.
Tool discovery supplies parameter schemas and descriptions. Read-only tools
are annotated accordingly; mutation tools carry conservative destructive hints.

`preview_sweep` and `validate_program` automatically use the connected model
and fresh live limits when available. For planning while disconnected, pass
`model="IPS3608"`, `model="DPS-150"` or `model="DPS-150P"`; that checks rated
limits. Results include the model and `validation_scope` (`live` or `rated`).
Actual execution always revalidates the connected model and current input.
Stored device presets use model ratings; applying those values uses live limits.
The widest family bounds in the MCP step schema are not permission to exceed
the connected supply's lower ratings or available input voltage.

`get_status` includes freshness, connection, identity, actual device settings,
latest sample, statistics, energy estimates, pending command, recording and
program state. `output_enabled` becomes null when stale. `device` and
`last_reported_output_enabled` retain the last readback and must not be treated
as current after communication loss.

Hardware mutations wait for a matching confirmed event. A successful command
returns `confirmed: true` with its readback. Program start instead returns
`accepted: true`; use status to follow execution. The two setpoint writes are
sequential and preserve output. Output enable is a separate explicit tool call.
Programs require output already on. Pause holds it, stop requests off, and
completion defaults to off. Disconnect, app close and MCP process exit preserve
physical output. Closing only the MCP client leaves the desktop program and
recording running. Existing app connection, statistics and energy behavior is
documented in the main README.

Requests expire rather than executing later from a blocked queue. A timeout
after dispatch does not imply rollback: read status before retrying. Fresh
telemetry/settings and no active change/program are required for normal writes.
Explicit output off can supersede pending work while connected, even if stale.
Multiple agent clients share these interlocks and cannot issue concurrent writes.

`monitor` observes for 0.5–30 seconds with an interval of 0.2–5 seconds, returning
unique latest samples and final state. It exits early on stale data. History
returns up to 1,000 retained samples over a window up to 3,600 seconds.
App reset tools do not reset device counters. Agent recordings accept only a
plain `.csv` filename inside the app data folder's `logs/` and never overwrite an existing file.

## Local access

The desktop app listens only on `127.0.0.1` with a random port and per-launch
bearer credential. `%LOCALAPPDATA%\IPS3608Control\.runtime\agent-bridge.json` lets local stdio
adapters discover it. Do not publish or copy that credential file. Requests
require the expected Host header and no browser Origin; proxy settings are
bypassed. The IPC endpoint is internal, not a general HTTP MCP endpoint.
This protects against accidental/browser access, not a hostile process running
as the same Windows user. There is no cloud relay, external API key, arbitrary
shell, arbitrary file access, raw register tool, fan tool, or firmware flasher.

Example prompts:

- “Use $ips3608-control to report voltage, current, power and output state.”
- “Monitor for 10 seconds and report min/max/average without changing output.”
- “Set the current limit to 0.25 A and leave voltage/output unchanged.”
- “Prepare a 5–12 V sweep at 0.2 A; show me the steps before execution.”

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use a mocked serial worker or simulated device. They exercise actual
MCP stdio discovery, parameter/error reporting, loopback authentication,
request expiry, readback confirmation, stale interlocks and reset/file scopes.
Network-restricted sandboxes need permission for loopback tests. Physical live
validation is read-only; do not run a load sweep to test installation.

With the app open, `python tools/check_mcp.py` checks discovery, resources and
two seconds of telemetry through a separate stdio process. The optional
`--connect COM12` connects the app if disconnected, without changing output.
