# Third-party components and protocol credits

The MIT license covers this project's original code. Dependencies and upstream
materials retain their own license terms.

- **Python** and its standard library: PSF license and included notices.
- **Tcl/Tk**: upstream Tcl/Tk license terms.
- **pyserial**: BSD-3-Clause.
- **MCP Python SDK**: MIT; its dependencies retain their respective licenses.
- **PyInstaller**: GPL with the distribution exception for bundled applications.
  It is a build tool, not a license change to this project's source.
- **Pillow**: used to encode the project icon during development; not required
  to run the app from source.

Portable release folders include collected dependency notices and build-time
versions under `third-party-notices/`.

Protocol investigation used public community implementations and static
inspection of the official FNIRSI PC software. Reference links are in
`docs/protocol-notes.md` and `docs/model-compatibility.md`. Vendor executables,
firmware, device captures and third-party reference source are not distributed
in this repository or its release artifacts.

FNIRSI and model names identify compatible hardware and belong to their
respective owners. The app icon is original project artwork generated with an
image-generation tool; its source and prompt brief are in `assets/branding/`.
