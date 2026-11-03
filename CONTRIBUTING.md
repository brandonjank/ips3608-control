# Contributing

Issues, focused pull requests and hardware verification are welcome. Start
with a reproducible problem or a concrete bench workflow.

Use Python 3.10+ with Tcl/Tk; release builds use Python 3.14 on Windows x64.
Install `.[dev]` and run `python -m unittest discover -s tests -v`. The tests
must remain independent of attached hardware. Use a temporary
`IPS3608_CONTROL_HOME` when testing storage or packaged builds.

For a device/protocol issue, include model, hardware/firmware versions, OS,
expected behavior and the actual result. A short packet capture is helpful;
remove personal paths, USB serial numbers and unrelated data. Do not upload
`.runtime` credentials, recordings with sensitive measurements, or vendor
firmware binaries.

New models need explicit identity and rating profiles, sample frames, checks
for model-dependent fields and tests proving invalid writes are rejected.
Describe what was tested on hardware and what was simulated. Never treat a
shared USB VID/PID as proof of protocol compatibility.

Preserve these invariants:

- Connection and setpoint edits do not implicitly enable output.
- Hardware writes report success after matching readback.
- Explicit off can cancel queued changes.
- UI and MCP share one serial owner and the same checks.
- Missing or stale data is not reported as a fresh zero or a confirmed off.

Keep changes small enough to review and document any user-visible behavior.
Contributions are provided under this repository's MIT license.
