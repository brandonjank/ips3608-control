"""Offline observations for an IPS3608 application binary and update disk image.

This tool only reads project files. It never opens USB or flashes anything.
Capstone is optional and used for small disassembly excerpts.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


def u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def disk_report(path):
    data = path.read_bytes()
    bps, spc, reserved, fats, entries = u16(data, 11), data[13], u16(data, 14), data[16], u16(data, 17)
    total = u16(data, 19) or u32(data, 32)
    fat_sectors = u16(data, 22)
    root_sectors = (entries * 32 + bps - 1) // bps
    root_start = (reserved + fats * fat_sectors) * bps
    data_start = root_start + root_sectors * bps
    clusters = (total * bps - data_start) // (spc * bps)
    root = []
    for offset in range(root_start, root_start + entries * 32, 32):
        entry = data[offset:offset + 32]
        if entry[0] == 0:
            break
        root.append({"offset": offset, "short_name_raw": entry[:11].decode("ascii", errors="replace"),
                     "attributes": entry[11], "first_cluster": u16(entry, 26), "bytes": u32(entry, 28)})
    nonzero = [{"sector": i // bps, "nonzero_bytes": sum(x != 0 for x in data[i:i+bps])}
               for i in range(0, len(data), bps) if any(data[i:i+bps])]
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "nonzero_bytes": sum(x != 0 for x in data), "nonzero_sectors": nonzero,
            "bytes_per_sector": bps, "sectors_per_cluster": spc, "reserved_sectors": reserved,
            "fat_copies": fats, "sectors_per_fat": fat_sectors, "root_entries": entries,
            "root_offset": root_start, "data_offset": data_start, "data_clusters": clusters,
            "fat_type_from_cluster_count": "FAT12" if clusters < 4085 else "FAT16",
            "filesystem_label_string": data[54:62].decode("ascii"),
            "root": root, "entire_data_area_zero": not any(data[data_start:]),
            "contains_cortex_m_vector_candidate": bool(re.search(rb"[\x00-\xff]{2}\x00\x20[\x01-\xff][\x00-\xff]{2}\x08", data[data_start:]))}


def app_report(path, base):
    data = path.read_bytes()
    strings = [{"offset": m.start(), "address_assuming_base": hex(base + m.start()),
                "text": m.group().decode("ascii")} for m in re.finditer(rb"[ -~]{6,}", data)]
    selected = [s for s in strings if any(key in s["text"].lower() for key in (
        "ips3608", "firmware", "upgrade", "ready", "restore", "version", "language", "load", "key1"))]
    vectors = [u32(data, i) for i in range(0, 64, 4)]
    needles = {hex(value): [m.start() for m in re.finditer(re.escape(struct.pack("<I", value)), data)]
               for value in (0x08005000, 0x08000000, 0xE000ED08, 0x40022000, 0x40021000)}
    report = {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
              "first_64_bytes": data[:64].hex(" "), "last_64_bytes": data[-64:].hex(" "),
              "initial_stack_pointer": hex(vectors[0]), "reset_vector": hex(vectors[1]),
              "first_16_vectors": [hex(v) for v in vectors],
              "analysis_base_assumption": hex(base), "file_end_assuming_base": hex(base + len(data)),
              "literal_occurrences": needles, "selected_strings": selected,
              "ascii_strings": strings}
    try:
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        report["disassembly_excerpts"] = {}
        reset = (vectors[1] & ~1) - base
        for name, offset, size in (("reset", reset, 12), ("startup_entry", 0x170, 12),
                                  ("application_init_vector_relocation", 0x0800E208-base, 28),
                                  ("vector_table_setter", 0x0801A5F4-base, 24)):
            if 0 <= offset < len(data):
                report["disassembly_excerpts"][name] = [f"{i.address:08x}: {i.mnemonic} {i.op_str}"
                    for i in md.disasm(data[offset:offset+size], base+offset)]
    except ImportError:
        pass
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--disk", type=Path)
    parser.add_argument("--base", type=lambda s: int(s, 0), default=0x08006800)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = {}
    if args.app:
        report["application"] = app_report(args.app, args.base)
    if args.disk:
        report["update_disk"] = disk_report(args.disk)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    concise = json.loads(json.dumps(report))
    if "application" in concise:
        concise["application"].pop("ascii_strings", None)
    print(json.dumps(concise, indent=2))


if __name__ == "__main__":
    main()
