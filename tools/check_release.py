"""Verify release versions, required assets and exclusion of private research data."""

from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ips3608.branding import VERSION

PRIVATE = {".venv", ".runtime", ".build-tmp", ".codex-remote-attachments", ".taste-skill", "backups", "firmware", "logs"}


def check_names(names):
    for name in names:
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if set(parts) & PRIVATE or "settings.json" in parts or "agent-bridge.json" in parts:
            raise RuntimeError(f"Private/runtime file in release: {name}")
        if ".." in parts or name.startswith(("/", "\\")):
            raise RuntimeError(f"Unexpected archive path: {name}")


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["version"] == VERSION
    assert project["project"]["license"] == "MIT"
    windows = (ROOT / "packaging" / "windows-version.txt").read_text()
    assert f"'{VERSION}'" in windows
    required = ("LICENSE", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
                "docs/overview.jpg", "docs/releasing.md", "ips3608/assets/app.ico")
    for path in required:
        assert (ROOT / path).is_file(), f"Missing release file: {path}"
    for path in (ROOT / "dist").glob("*.tar.gz"):
        with tarfile.open(path) as archive:
            names = archive.getnames()
            check_names(names)
            for required in ("Launch Bench Control.cmd", "Setup.cmd", ".github/workflows/ci.yml"):
                assert any(n.endswith("/" + required) for n in names), f"Missing source file: {required}"
    for path in (ROOT / "dist").glob("*.whl"):
        with zipfile.ZipFile(path) as archive:
            check_names(archive.namelist())
            assert "ips3608/assets/app.ico" in archive.namelist()
            assert "ips3608/demo.py" in archive.namelist()
    for path in (ROOT / "dist").glob("*-windows-x64.zip"):
        with zipfile.ZipFile(path) as archive:
            check_names(archive.namelist())
            names = archive.namelist()
            assert any(n.endswith("/IPS3608Control.exe") for n in names)
            assert any(n.endswith("/IPS3608ControlMCP.exe") for n in names)
            assert any(n.endswith("/Python-LICENSE.txt") for n in names)
            assert any(n.endswith("/Tcl-LICENSE.txt") for n in names)
            assert any(n.endswith("/Tk-LICENSE.txt") for n in names)
    # Local Markdown links must resolve in the public source tree.
    public = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    for path in public:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            assert (path.parent / target).exists(), f"Broken documentation link in {path.name}: {target}"
    print(f"Release {VERSION}: versions, assets, archive contents and documentation links verified.")


if __name__ == "__main__":
    main()
