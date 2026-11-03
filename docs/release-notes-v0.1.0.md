# IPS3608 Control v0.1.0

A local Windows workbench for FNIRSI bench power supplies: live setpoints,
measurement logging, repeatable sequences and AI-agent control through MCP.

## Included

- Model detection for IPS3608, DPS-150 and DPS-150P, with rated/live limit checks.
- Readback-confirmed controls, live plots and resettable V/A/W statistics.
- CSV logging, presets, protection settings, brightness and metering.
- Timed programs and voltage/current sweeps.
- A 21-tool local MCP server and the `ips3608-control` agent skill.
- A Windows app icon, compact instrument UI, short-window scrolling and
  an in-memory demo for trying the app without a supply.

## Downloads

Extract the whole Windows ZIP, then open `IPS3608Control.exe`. Python is
included. `IPS3608ControlMCP.exe` is the optional stdio server for AI clients.
Source and wheel artifacts are also included; checksums are in `SHA256SUMS.txt`.

## Verification and limitations

IPS3608 hardware V1.1 was checked with firmware V1.3 and V1.9. DPS-150 and
DPS-150P have protocol and simulated-device coverage; physical testing is
still needed. This is a first public beta, independent of FNIRSI.

The Windows binaries are unsigned. Connection/disconnection and app close
preserve physical output. A lost USB link cannot guarantee output off.
