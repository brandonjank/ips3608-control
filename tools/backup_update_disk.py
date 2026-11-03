"""Read-only Windows capture of the IPS3608's AT32 update disk.

Opens only the uniquely identified small AT32 MSC IAP disk with GENERIC_READ.
Never formats, locks, dismounts, writes to, or sends upgrade commands to it.
The disk image is NOT assumed to contain the installed MCU firmware.
"""

import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def inventory():
    script = r"""
      $ErrorActionPreference = 'Stop'
      $disks = @(Get-CimInstance Win32_DiskDrive | Where-Object {
        $_.Model -eq 'AT32 Msc iap USB Device' -and $_.InterfaceType -eq 'USB'
      } | Select-Object Index,Model,InterfaceType,Size,BytesPerSector,TotalSectors,DeviceID,PNPDeviceID)
      if ($disks.Count -ne 1) { throw 'Expected exactly one AT32 MSC IAP disk.' }
      $disk = $disks[0]
      $parts = @(Get-Partition -DiskNumber $disk.Index | Select-Object DiskNumber,PartitionNumber,DriveLetter,Offset,Size)
      $volumes = @(Get-CimInstance Win32_LogicalDisk | Where-Object { $_.VolumeName -eq 'IPS3608' } |
        Select-Object DeviceID,VolumeName,FileSystem,Size,FreeSpace,VolumeSerialNumber)
      $usb = @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\VID_2E3C&PID_5720\*' } |
        Select-Object Class,FriendlyName,InstanceId,Status)
      @{disk=$disk;partitions=$parts;volumes=$volumes;usb=$usb} | ConvertTo-Json -Depth 6 -Compress
    """
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    disk = info["disk"]
    if not (0 < int(disk["Size"]) <= 32 * 1024 * 1024 and
            disk["PNPDeviceID"].startswith("USBSTOR\\DISK&VEN_AT32&PROD_MSC_IAP&")):
        raise RuntimeError("Unexpected disk identity or capacity; refusing capture.")
    if len(info["usb"]) != 1 or len(info["volumes"]) != 1 or len(info["partitions"]) != 1:
        raise RuntimeError("Ambiguous update disk/USB/volume mapping.")
    letter = info["partitions"][0]["DriveLetter"]
    if not isinstance(letter, str) or len(letter) != 1 or not letter.isalpha():
        raise RuntimeError("Update drive letter not available.")
    volume = info["volumes"][0]
    if volume["DeviceID"] != letter + ":" or info["partitions"][0]["Offset"] != 0:
        raise RuntimeError("Volume is not mapped to the expected AT32 disk.")
    return info, Path(letter + ":\\")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def capture_disk(path, size, destination):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE, OPEN_EXISTING. No write access.
    handle = kernel.CreateFileW(path, 0x80000000, 3, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_string_buffer(65536)
        received = wintypes.DWORD()
        count = 0
        with destination.open("xb") as target:
            while count < size:
                wanted = min(len(buffer), size - count)
                if not kernel.ReadFile(handle, buffer, wanted, ctypes.byref(received), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if received.value == 0 or received.value > wanted:
                    raise RuntimeError(f"Unexpected read length at offset {count}.")
                target.write(buffer.raw[:received.value])
                count += received.value
            target.flush()
            os.fsync(target.fileno())
        return {"bytes": count, "sha256": sha256(destination)}
    finally:
        kernel.CloseHandle(handle)


def main():
    if os.name != "nt":
        raise RuntimeError("This capture tool is for Windows only.")
    info, source = inventory()
    folder = ROOT / "backups" / datetime.now().strftime("IPS3608_update_%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"captured_at": datetime.now().astimezone().isoformat(), "inventory": info,
                "method": "Win32 CreateFileW GENERIC_READ on raw volume at verified partition offset 0; source files opened rb only",
                "scope": "Exposed update mass-storage disk, not a verified MCU flash/firmware dump",
                "files": [], "errors": []}
    manifest_path = folder / "manifest.json"
    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    save()
    disk = info["disk"]
    for name in ("update-disk.img", "update-disk-second-read.img"):
        # Revalidate disk numbering before opening the raw path again.
        fresh, _ = inventory()
        if fresh["disk"]["PNPDeviceID"] != disk["PNPDeviceID"] or fresh["disk"]["Index"] != disk["Index"]:
            raise RuntimeError("Device changed during capture; stopping.")
        try:
            result = capture_disk("\\\\.\\" + source.drive, int(disk["Size"]), folder / name)
            manifest[name] = result
        except (OSError, RuntimeError) as error:
            manifest["errors"].append({"path": name, "error": str(error)})
        save()
    manifest["raw_reads_match"] = (manifest.get("update-disk.img", {}).get("sha256") is not None and
        manifest.get("update-disk.img", {}).get("sha256") == manifest.get("update-disk-second-read.img", {}).get("sha256"))
    for directory, dirs, files in os.walk(source, followlinks=False,
                                           onerror=lambda e: manifest["errors"].append({"error": str(e)})):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        relative = Path(directory).relative_to(source)
        for name in files:
            original = Path(directory) / name
            if original.is_symlink():
                continue
            target = folder / "files" / relative / name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # read_bytes handles zero-byte virtual files without Get-Content semantics.
                content = original.read_bytes()
                with target.open("xb") as stream:
                    stream.write(content)
                manifest["files"].append({"path": str(relative / name), "bytes": len(content),
                                          "sha256": hashlib.sha256(content).hexdigest()})
            except OSError as error:
                manifest["errors"].append({"path": str(relative / name), "error": str(error)})
    save()
    print(json.dumps({"backup_folder": str(folder), "images": {
        name: manifest.get(name) for name in ("update-disk.img", "update-disk-second-read.img")},
        "raw_reads_match": manifest["raw_reads_match"], "files": manifest["files"],
        "errors": manifest["errors"]}, indent=2))
    if not manifest.get("update-disk.img") or not manifest.get("update-disk-second-read.img"):
        raise SystemExit("Full raw image capture failed; see manifest.json for partial results.")


if __name__ == "__main__":
    main()
