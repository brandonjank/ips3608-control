"""Build the Windows portable bundle, source distributions and checksums."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ips3608.branding import VERSION
from collect_notices import collect


def run(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main():
    run("-m", "build")
    run("-m", "PyInstaller", "--noconfirm", "--distpath", "dist/windows",
        "--workpath", "build/windows", "packaging/windows.spec")
    bundle = ROOT / "dist" / "windows" / "IPS3608-Control"
    for name in ("LICENSE", "README.md", "CHANGELOG.md", "CONTRIBUTING.md",
                 "SECURITY.md", "THIRD_PARTY.md", "VALIDATION.md"):
        shutil.copyfile(ROOT / name, bundle / name)
    for name in ("docs", "skills"):
        shutil.copytree(ROOT / name, bundle / name, dirs_exist_ok=True)
    shutil.copytree(ROOT / "ips3608/assets", bundle / "ips3608/assets", dirs_exist_ok=True)
    collect(bundle / "third-party-notices")
    output = ROOT / "dist" / f"IPS3608-Control-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(bundle.parent))
    artifacts = sorted([output, *(ROOT / "dist").glob("*.whl"), *(ROOT / "dist").glob("*.tar.gz")])
    checksums = []
    for path in artifacts:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksums.append(f"{digest}  {path.name}")
    (ROOT / "dist" / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print("\nRelease artifacts:")
    for path in artifacts:
        print(path.name, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
