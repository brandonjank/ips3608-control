# Official PC protocol verification

Source: FNIRSI's **DPS-150 / IPS3608 Software** download, linked from
https://www.fnirsi.com/pages/software-downloads and hosted at MediaFire file
`2lhihafua649rby`. Inspected 2026-11-02. The executable was extracted and
decompiled for static analysis only, never executed.

`FNIRSI Power supply.exe` SHA-256:
`DFA0C7B316345B4A6987D28473AAE0BD2D93E200DEEF79DBAE8502B68CDF4570`

Evidence locations in the decompiled application:

- `Command.cs`: connect/disconnect, query-all, output enable, device metering
  at D8, brightness read at D6, and baud-rate configuration at B0/00.
- `UserSelectBox2Data3.Send`: B1/C1 and B1/C2 float32 writes for live V/A.
- `UserSelectBox2Data2.Send`: six device preset pairs C5/C6 … CF/D0.
- `UserPage12` and `UserSelectBox2Data4.Send`: D1 OVP, D2 OCP, D3 OPP,
  D4 OTP, D5 input undervoltage, all B1 float32 writes.
- `UserSelectBox2Data1`: B1/D6 byte write for brightness. `UserPage3` limits
  brightness to levels 1–14 (not the 0–20 range in community documentation).
- `SerialData.setAllData`, `setData`, `getData31String`, `getData32String`:
  snapshot offsets, individual telemetry and status meanings.

## Full A1/FF response payload (139 bytes)

| Byte offset | Type | Meaning |
| --- | --- | --- |
| 0 | float32 | Internal input voltage |
| 4, 8 | float32 × 2 | Actual V/A setpoints |
| 12, 16, 20 | float32 × 3 | Output V/A/W snapshot |
| 24 | float32 | Temperature, °C |
| 28–75 | float32 × 12 | Six stored V/A preset pairs |
| 76, 80, 84, 88, 92 | float32 × 5 | OVP, OCP, OPP, OTP, UVP thresholds |
| 96 | byte | Brightness |
| 97 | byte | Unused by official PC software; left undecoded |
| 98 | byte | Device metering enabled |
| 99, 103 | float32 × 2 | Device Ah/Wh counters |
| 107 | byte | Output enabled |
| 108 | byte | Protection: 0 normal, 1 OVP, 2 OCP, 3 OPP, 4 OTP, 5 UVP |
| 109 | byte | Mode: 1 CV, 0 CC |
| 110 | byte | Unused here; left undecoded |
| 111, 115 | float32 × 2 | Maximum voltage/current setpoints |
| 119, 123, 127, 131, 135 | float32 × 5 | Maximum OVP/OCP/OPP/OTP/UVP limits |

Other reads: C0 input voltage, C3 live V/A/W, C4 temperature, D9 Ah, DA Wh,
DB output, DC protection, DD mode, DE model, DF hardware version string on
this unit, E0 firmware string, E1 address. DF is ambiguous in community maps;
we label its observed `V1.1` response as hardware version.

The earlier community map placed hardware maxima at 76/80 (those are protection
thresholds), misaligned counters, and reversed CV/CC. Those are corrected here.

No fan, beeper, language, theme, key-lock, address-write, factory-reset or
device-counter-reset command was identified in the official PC application.
Absence from this program does not prove absence from the device firmware.
Unverified functions are not sent as speculative writes.
