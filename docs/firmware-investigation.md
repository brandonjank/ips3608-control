# IPS3608 update-mode investigation — 2026-11-02

## Result and backup scope

Raw captures, vendor firmware and local analysis files referenced here are
excluded from the public repository and release archives. This document records
observations; it does not distribute FNIRSI firmware.

The exposed USB disk was captured successfully, twice, using read-only raw
volume access. **This is a backup of the update interface's synthetic disk,
not a backup of the installed V1.3 application, bootloader, or calibration.**

Successful capture:

`backups/IPS3608_update_20261102_145924/`

Both `update-disk.img` and `update-disk-second-read.img`:

- Length: **8,386,560 bytes** (all 4,095 advertised 2,048-byte sectors).
- SHA-256: `a8353a02aa6152c7419e9fbaedbe86f1abfcbb5026102c391b2e1dc6e94853dd`.
- Only **91 nonzero bytes**, in sectors 0, 6 and 8.
- The entire filesystem data area, starting at byte 32,768, reads as zero.
- Root entries: zero-length `Ready.TXT` and volume label `IPS3608`.

Consequently there is no recoverable installed application or bootloader in
this disk image, including its unallocated data area. The captured disk is
not a raw mapping of MCU flash. Do not write the image back to the device:
the update disk interprets host writes as programming input, not ordinary
storage restoration.

The first capture attempt, `backups/IPS3608_update_20261102_145746/`, records
physical-disk access failures. The subsequent successful attempt used
`\\.\J:` after verifying that its partition starts at offset zero and covers
the entire identified update disk. No administrator prompt was needed for
that volume-level path. Both handles requested `GENERIC_READ` only.

Windows enumerated `System Volume Information` metadata and two small files;
copies are included for completeness. These do not appear in the raw disk
snapshot and may reflect Windows filesystem caching. They are not firmware.
Opening `Ready.TXT` via normal file APIs failed despite directory enumeration;
its empty entry is preserved in the raw image. `manifest.json` records this
limitation rather than claiming a successful file read.

## USB interface

| Property | Observed value |
| --- | --- |
| Drive | `J:`, label `IPS3608` |
| Physical disk during capture | `\\.\PHYSICALDRIVE6` |
| Inquiry model | `AT32 Msc iap USB Device`, revision 2.00 |
| VID/PID in update mode | `2E3C:5720` |
| Driver/class | `USBSTOR`, class 08 / subclass 06 / protocol 50 |
| Sector size/count | 2,048 bytes / 4,095 sectors |
| FAT geometry | 4 sectors/cluster, 6 reserved sectors, 2 FATs, 512 root entries |

The boot sector's text says `FAT16`, but its cluster count is 1,019, which
falls in FAT12 territory. The apparent 8 MB capacity does not establish MCU
flash size. This is an unusual, minimal FAT facade used as an update inbox.

The normal application's `2E3C:5740` COM12 interface is absent in this mode.
The only current serial port reported by Windows was COM3, which is not this
device. The app/MCP serial protocol cannot control a USB mass-storage endpoint.

## Downloaded V1.9 application

The ZIP already downloaded into the user's Downloads folder was copied into
`firmware/vendor-v1.9/` and extracted locally. Nothing was copied to `J:`.

- File: `APP_IPS3608_V1.9_260429.bin`
- Length: **156,472 bytes** (`0x26338`).
- SHA-256: `7a6e87a7661c5a0afe537b2b7865826106638fb2fa49a7a76268389e8013669d`.
- Begins directly with a Cortex-M vector table and contains readable Thumb
  instructions, strings and data; it is not an encrypted wrapper or ELF file.
- Initial stack pointer: `0x20007FF0`.
- Reset vector: `0x08006985` (Thumb instruction address `0x08006984`).
- **Confirmed application link/vector address: `0x08006800`.**
- File extent at that base: `0x08006800` through `0x0802CB37` inclusive.

The address is supported by actual instructions, not inferred only from the
manufacturer's generic IAP example. At `0x0800E210` the app loads `r0` with
`0x08000000`, then `r1` with `0x6800`, and calls `0x0801A5F4`. That function
combines the arguments and stores them to SCB VTOR at `0xE000ED08`.
The reset handler at file offset `0x184` calls the initialization routine at
`0x08012180`, then the runtime entry at `0x08006970`. The main loop at
`0x0801A518` initializes the application and repeatedly calls two routines.
These names describe behavior; they are not recovered original symbol names.

The area below `0x08006800` is outside the downloaded application. It is
consistent with space for a bootloader and metadata, but the exact bootloader
extent, update flags, persistent settings, calibration layout, actual MCU part
number and any backup slot remain unverified. The initial SP does not prove
the chip's total SRAM capacity. A complete device backup would need those
additional regions, not just this application file.

Readable strings include the device name, language selection, brightness,
metering, dataset and factory-reset UI, plus `LOAD:MODE CC`, `LOAD:CURR %f`,
`LOAD OFF` and `LOAD ON`. The latter are evidence of strings in the normal
application; they are **not verified commands accepted over USB**, especially
not by the update-mode interface. The quoted update-screen messages do not
appear as ASCII strings in this application. Their absence does not by itself
exclude another encoding, but the separate link region supports a separate
bootloader implementation.

The package README and changelog call this V1.9 with release date 2026-07-29;
the binary filename ends `260429`. Preserve both facts rather than assuming
the filename is an authoritative build date. The documented V1.9 change fixes
small voltage-display fluctuations while output is off. V1.7's notes mention
low-current calibration points and charging logic changes. This downloaded
V1.9 is not a substitute for a backup of the unit's previously observed V1.3.

Machine-readable results, ASCII strings and disassembly excerpts:
`firmware/analysis-v1.9.json`.

## How the update path appears to work

1. Holding Up during power-on selects the resident update code.
2. It exposes a small synthetic FAT USB mass-storage interface and status file.
3. A firmware-file transfer is interpreted by the resident update code as a
   programming operation, rather than simply storing a normal file for reading.
4. Starting the application transfers execution to its flash image/vector table.

Items 1 and 4 are described in the downloaded vendor README. The synthetic
read behavior is directly measured. The programming implementation is inferred
from the matching Artery reference implementation and the vendor workflow;
FNIRSI's resident update binary itself has not been obtained or disassembled.
Whether FNIRSI programs immediately while copying, stages data elsewhere,
verifies a signature/checksum, or keeps a rollback image is not established.

The manufacturer reference code has matching inquiry strings, 4,095-sector
capacity, 2 KB sectors, BPB fields and `Ready.TXT`. Its
`flash_fat16_read()` synthesizes headers/status and returns zeros for other
addresses. That matches this device's captured read behavior. Its write path
can erase/program internal flash, so modifying metadata or trying filenames as
"commands" is not a safe discovery method. It is a reference, not proof that
the FNIRSI fork supports every reference feature or uses the same addresses.

Primary reference sources, saved under `firmware/reference-at32/` with their
copyright notices and repository license:

- [Artery MSC disk interface](https://github.com/ArteryTek/AT32F403A_407_Firmware_Library/blob/master/project/at_start_f403a/examples/usb_device/virtual_msc_iap/src/msc_diskio.c)
- [Artery synthetic FAT implementation](https://github.com/ArteryTek/AT32F403A_407_Firmware_Library/blob/master/project/at_start_f403a/examples/usb_device/virtual_msc_iap/src/flash_fat16.c)
- [Artery reference boot flow](https://github.com/ArteryTek/AT32F403A_407_Firmware_Library/blob/master/project/at_start_f403a/examples/usb_device/virtual_msc_iap/src/main.c)

## Buttons and commands in update mode

| Action | What is established |
| --- | --- |
| Hold Up while powering on | Documented entry to update mode; observed by user. |
| Short press RUN/STOP | Vendor README says this starts the application from update mode. The user's screen describes it as restoring previous firmware. It has not been pressed during this investigation. |
| Copy the matching firmware `.bin` | Vendor's update method; a programming operation, not a read-only probe. Not performed. |
| Read directory / raw sectors | Works; captured and verified without requested write access. |
| Other arrow buttons, V/A, encoder, Settings, long presses | No verified update-mode functions found. Not tested. |
| Normal COM12/MCP commands | Not applicable while the serial interface is absent. |
| Firmware dump or debug commands over the disk | No supported command found; the exposed reads return zeros, not installed code. |

"Restore previous firmware" may mean resume the existing installed application
without updating. It does **not** establish a separate old-version backup or
an A/B rollback scheme. A true installed-firmware backup likely requires
identifying the MCU and investigating its SWD/readout interface. If readout is
protected, stop rather than issuing unlock/erase operations. No hardware debug
connection, protection change, firmware flash, button press or restore was
performed here. The unit was left in update mode.
