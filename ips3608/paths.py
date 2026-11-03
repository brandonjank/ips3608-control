"""Stable, writable user data shared by source, packaged GUI and MCP clients."""

import os
from pathlib import Path
import sys


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parent
ASSETS = PACKAGE / "assets"


def data_directory():
    override = os.environ.get("IPS3608_CONTROL_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "IPS3608Control"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "IPS3608 Control"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ips3608-control"


DATA = data_directory()


def migrate_presets():
    """Copy legacy source-tree presets once. Leave the original and logs alone."""
    old, new = PROJECT / "settings.json", DATA / "settings.json"
    if old.is_file() and not new.exists() and old.resolve() != new.resolve():
        DATA.mkdir(parents=True, exist_ok=True)
        try:
            with new.open("xb") as target:
                target.write(old.read_bytes())
        except FileExistsError:
            pass
