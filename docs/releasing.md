# Building and publishing a release

Release target: `brandonjank/ips3608-control`. App name: **IPS3608 Control**.
The initial release is **v0.1.0**, a public beta under MIT.

## Build locally

Use Windows x64 and Python 3.14 with Tcl/Tk:

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python tools/build_release.py
python tools/check_release.py
```

The build creates a portable folder, Windows ZIP, Python wheel, source tarball
and `SHA256SUMS.txt` in `dist/`. The GUI executable is windowed; the MCP
executable has stdio for an agent client. Keep the complete `_internal` folder.
Third-party notices are collected into the portable folder.

Try the packaged GUI from a different working directory, including `--demo`,
and initialize `IPS3608ControlMCP.exe` using an MCP client. Use
`IPS3608_CONTROL_HOME` for isolated smoke-test data. Before a hardware test,
release any other app's serial connection. A read-only connection test should
leave voltage/current setpoints and output state unchanged.

`tools/prepare_icon.py` regenerates ICO/PNG sizes from the checked-in master.
Update the version in `pyproject.toml`, `ips3608/branding.py` and
`packaging/windows-version.txt` together, and add a changelog entry.

## Repository contents

Keep source, tests, docs, branding assets, build configuration and the skill.
Do not commit `.venv`, `build`, `dist`, `.build-tmp`, `.runtime`, settings,
recordings, device backups, vendor firmware/executables or attachments.
`tools/check_release.py` checks the source distribution and wheel for those
paths. Vendor firmware and captures remain local research inputs.

## GitHub

Suggested description:

> Desktop control, logging, sweeps and local MCP automation for FNIRSI IPS3608,
> DPS-150 and DPS-150P power supplies.

Topics: `fnirsi`, `ips3608`, `dps-150`, `power-supply`, `electronics`,
`instrument-control`, `python`, `tkinter`, `mcp`.

The CI workflow tests Python 3.10/3.12/3.14 on Windows. The manual release
workflow checks out an existing version tag, tests and builds it, then creates
a **draft prerelease** with checksums and artifacts. Review the draft before
publishing it. The workflow needs repository contents-write permission only
for that release job.

Before the first publication, review README screenshots, model-test status,
MIT copyright and artifact contents. The executables are unsigned; do not
claim code signing or universal hardware certification. Keep hardware-verified
and simulated support distinct in the release notes.
