# Security and physical-output reports

This app controls electrical output. Reports of unintended energizing,
validation bypasses, incorrect confirmation or unauthorized local control
are especially useful.

Use GitHub's private vulnerability reporting for this repository when it is
enabled. Otherwise contact the maintainer through
[the GitHub profile](https://github.com/brandonjank) before posting details of
an exploitable issue. Do not include runtime bearer tokens or private captures
in public issues.

The MCP transport is local stdio. Desktop IPC binds only to loopback and uses
a per-launch token, expected Host header and browser-Origin rejection. This
does not protect against a hostile process running as the same OS user.

Only the latest release is maintained. Supply firmware and hardware protection
remain separate from the app; a disconnected USB link cannot guarantee output off.
