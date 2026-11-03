# Validation — 2026-11-02

## Automated checks

- **59 tests pass** with `python -m unittest discover -s tests -v` on Windows,
  Python 3.14. CI targets Windows Python 3.10, 3.12 and 3.14; hosted results
  are available in [GitHub Actions](https://github.com/brandonjank/ips3608-control/actions).
- Packet framing, partial/corrupt replies, finite values, model detection,
  rated/live ceilings, DPS input headroom and unknown-identity rejection.
- Readback-confirmed live edits, explicit output switching, queued-target
  validation, stale-data interlocks and output-preserving disconnects.
- Statistics/reset scopes, integration gaps, CSV precision, flushing and
  overwrite refusal; timed programs, sweep limits and readback-gated holds.
- Authenticated loopback IPC, Host/Origin checks, request expiry, duplicate
  instances and MCP validation. No arbitrary shell/register access is exposed.
- Per-user storage, preset migration, packaged icon assets, in-memory demo
  commands and scrolling to the bottom of a short native window.

## Windows release checks

- Native GUI reviewed on the real device and with simulated output enabled;
  icon, About dialog, typography, complete layout and footer checked visually.
- Portable GUI starts outside the project directory without a separate Python
  install. Demo mode connects to an in-memory serial implementation only.
- Portable MCP executable initialized through the official SDK: 21 tools,
  resources, offline model planning and useful app-not-running errors checked.
- Release checks cover versions, required assets, documentation links, license
  notices and exclusion of runtime credentials, captures and vendor firmware.

## Physical hardware

IPS3608 hardware **V1.1** was read with firmware **V1.3 and V1.9**. The V1.9
release check used COM12 through the app and MCP: fresh telemetry, identity,
settings, history and resources worked. Setpoints remained **24 V / 0.5 A**,
with **output off**. No changed physical setpoints, protections, presets,
metering or firmware were written during release preparation.

Earlier V1.3 checks confirmed reapplying existing setpoints, explicit output
off and the existing brightness level. Physical output enable, energized live
edits and program execution were exercised with simulated devices instead.

**DPS-150 and DPS-150P have protocol evidence and simulated-device coverage;
physical validation remains outstanding.** Their identity, limits, input
headroom, preset writes, live edits and sub-microamp readback formatting have
simulation tests. See [model compatibility](docs/model-compatibility.md).

The Windows executables are unsigned. Loss of USB communication cannot prove
that a physical output is off. This is a first public beta, not a hardware
certification or firmware replacement.
