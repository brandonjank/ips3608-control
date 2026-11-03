# FNIRSI power-supply model compatibility

Implemented 2026-11-02. The app auto-detects all three supported model identities.
DPS behavior has been validated with simulated serial devices; physical
DPS-150 / DPS-150P units were not available for this implementation.

| Model | Rated output | Evidence and current status |
| --- | --- | --- |
| IPS3608 | 36 V / 8 A / 285 W | Implemented. Readback verified on hardware V1.1 with firmware V1.3 and V1.9. |
| DPS-150 | 30 V / 5 A / 150 W | Implemented and tested with a simulated device using the shared protocol. Physical hardware verification remains outstanding. |
| DPS-150P | 30 V / 5 A / 150 W | Implemented with adaptive microamp readback display and finer CSV precision. Simulated-device tests pass; physical hardware verification remains outstanding. |
| DPS-200 | Not established | A design-time label in the vendor app, replaced by the actual model response. Not sufficient evidence of a supported shipping model. |

## Evidence

- FNIRSI [Software Downloads](https://www.fnirsi.com/pages/software-downloads)
  explicitly labels the shared download **DPS-150 / IPS3608 Software**.
- Official [DPS-150 specifications](https://www.fnirsi.com/products/dps-150)
  give 0–30 V, 0–5 A, 150 W and PC software support.
- Official [DPS-150P specifications](https://www.fnirsi.com/de/products/dps-150p?_pos=2&_sid=358f0aead&_ss=r)
  give the same output ratings, microamp current readback, and PC monitoring,
  output settings, presets and recording.
- [burgerwdev/fnrisi-power-supply-webcon](https://github.com/burgerwdev/fnrisi-power-supply-webcon)
  identifies an actual DPS-150P test setup. Its
  [register definitions](https://github.com/burgerwdev/fnrisi-power-supply-webcon/blob/main/src/protocol/const.ts)
  and [snapshot decoder](https://github.com/burgerwdev/fnrisi-power-supply-webcon/blob/main/src/protocol/decode.ts)
  match our principal register map and corrected 139-byte snapshot offsets:
  setpoints 4/8, V/A/W 12/16/20, presets 28–75, protections 76–95,
  brightness/metering 96/98, counters 99/103, output/protection/mode 107–109,
  and maxima 111–138. This is implementation evidence, not a test of our code
  against either DPS model. Do not assume every claimed optional command is
  supported by every model.

The decompiled official PC application backs this shared structure: it reads
the actual model from DE, displays it in `SerialData.Data33`, and adjusts
control maxima from the snapshot / E2 / E3 replies. In `UserPage4.cs`, the
literal `DPS-200` is assigned while building the initial UI; it is not a
supported-model allowlist or an identity validation result.

## Implemented behavior

1. Explicit model profiles identify the model before accepting control
   commands. Preserve the IPS3608 profile and reject unknown identities.
2. Per-model rated V/A/W limits apply throughout manual settings, presets,
   programs and MCP. DPS protection maxima come from the detected device's
   snapshot; IPS3608 additionally retains its verified hard protection ceilings.
3. Device-reported limits are checked during execution. The DPS units are
   externally powered step-down supplies; their achievable voltage depends
   on the input source, unlike the IPS3608's internal supply. The effective
   voltage limit is the minimum of model rating, reported voltage ceiling and
   measured input minus 0.2 V of conservative headroom. Current is limited by
   both rating and reported ceiling. Missing/nonfinite/negative ceiling data
   blocks live changes. The snapshot is polled, along with E2/E3/input reads;
   commands are checked again by the serial worker just before transmission.
4. Display precision and capabilities are model-aware. DPS-150P readings and
   statistics use µA/mA/A; its current trace can show microamp signals and CSV
   retains nine decimal places in A/W. Actual current setpoints remain 1 mA.
   Titles, MCP capabilities and the installed skill reflect the connected model.
5. Tests cover detection, readback-confirmed live writes and preset saves,
   model/input ceilings, unavailable limits, unknown identity rejection, queued
   target revalidation, program checks/abort after an input drop, GUI/MCP
   interlocks and microamp presentation/CSV. Shared USB VID/PID alone is never
   used as a model identity. Real hardware coverage remains IPS3608 only.

Persistent presets and offline recipes use model ratings, so a saved recipe
may target a stronger future input source. Applying/starting it always checks
the current live limits. Existing local presets are not silently clamped;
out-of-rating values are rejected for the connected model. Programs abort and
request off if a required live ceiling drops while running or paused.

## IPS3608 firmware V1.9 check

After the user's firmware update, the app was reconnected through its local
MCP bridge on COM12. Identity reported IPS3608 / hardware V1.1 / firmware V1.9.
The separate stdio MCP process successfully read live telemetry, history,
device settings and the status resource. Four samples were collected over
two seconds. Actual output was off, with retained setpoints 24 V / 0.5 A.
No output switching, changed setpoint, protection, preset or metering write was
performed. This verifies the read path on V1.9; it is not a new validation of
every physical write command against that firmware.
