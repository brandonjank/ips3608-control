"""Copy installed distribution license notices into a Windows release folder."""

import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys


def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    lines = ["Third-party license notices", "", "Dependency versions used for this build:"]
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        lines.append(f"{name} {dist.version}")
        for file in dist.files or []:
            lower = str(file).lower()
            if any(word in Path(file).name.lower() for word in ("license", "copying", "notice")) and ".dist-info" in lower:
                source = Path(dist.locate_file(file))
                target = destination / name / str(file).split(".dist-info", 1)[1].lstrip("/\\")
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    prefix = Path(sys.base_prefix)
    for name, source in (("Python", prefix / "LICENSE.txt"),
                         ("Tcl", prefix / "tcl" / "tcl8.6" / "license.terms"),
                         ("Tk", prefix / "tcl" / "tk8.6" / "license.terms")):
        if name == "Tcl" and not source.exists():
            source = Path(__file__).resolve().parent.parent / "packaging/third-party/Tcl-LICENSE.txt"
        if source.exists():
            shutil.copyfile(source, destination / f"{name}-LICENSE.txt")
        else:
            raise FileNotFoundError(f"Required {name} license not found: {source}")
    (destination / "INDEX.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    collect(sys.argv[1])
